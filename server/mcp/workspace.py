"""Server-owned workspaces for file-backed MCP catalog adapters.

The browser only receives opaque workspace, file and artifact identifiers.
Paths are resolved here and are never accepted by the catalog configuration or
tool-call APIs.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import threading
import time
import unicodedata
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any


MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_WORKSPACE_BYTES = 512 * 1024 * 1024
MAX_WORKSPACE_FILES = 5_000
MAX_ZIP_RATIO = 20
EPHEMERAL_TTL_SECONDS = 24 * 60 * 60
ARTIFACT_TTL_SECONDS = 7 * 24 * 60 * 60

FILE_PROJECTS = {
    "basic-memory-mcp",
    "duckdb-mcp",
    "excel-mcp-server",
    "git-mcp",
    "markitdown-mcp",
}

PROJECT_EXTENSIONS: dict[str, set[str] | None] = {
    "basic-memory-mcp": {".md", ".markdown", ".txt"},
    "duckdb-mcp": {".duckdb"},
    "excel-mcp-server": {".xlsx", ".xls", ".csv", ".tsv", ".json"},
    "git-mcp": None,
    "markitdown-mcp": {
        ".txt", ".md", ".markdown", ".pdf", ".docx", ".pptx",
        ".xlsx", ".xls", ".csv", ".tsv", ".json", ".html", ".htm", ".xml",
    },
}


class CatalogWorkspaceError(RuntimeError):
    """Base workspace failure."""


class CatalogWorkspaceNotFoundError(CatalogWorkspaceError):
    """Raised when a workspace, file or artifact is missing."""


class CatalogWorkspacePolicyError(CatalogWorkspaceError):
    """Raised when an upload or path violates workspace policy."""


@dataclass(slots=True)
class CatalogWorkspaceFile:
    file_id: str
    relative_path: str
    filename: str
    size_bytes: int
    sha256: str
    content_type: str


@dataclass(slots=True)
class CatalogWorkspaceArtifact:
    artifact_id: str
    relative_path: str
    filename: str
    size_bytes: int
    sha256: str
    content_type: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(
        default_factory=lambda: time.time() + ARTIFACT_TTL_SECONDS
    )


@dataclass(slots=True)
class CatalogWorkspace:
    workspace_id: str
    tenant_id: str
    project_id: str
    display_name: str
    persistent: bool
    status: str = "uploading"
    files: list[CatalogWorkspaceFile] = field(default_factory=list)
    artifacts: list[CatalogWorkspaceArtifact] = field(default_factory=list)
    manifest_sha256: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None


def opaque_file_id(workspace_id: str, relative_path: str) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}:{relative_path}".encode("utf-8")
    ).hexdigest()[:24]
    return f"mcpf_{digest}"


class MCPCatalogWorkspaceStore:
    """Persist catalog workspace metadata and stage immutable inputs."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        input_root: str | Path | None = None,
        output_root: str | Path | None = None,
        memory_root: str | Path | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("MCP_CATALOG_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.input_root = Path(
            input_root
            or os.getenv("MCP_FILE_INPUT_ROOT", "").strip()
            or self.storage_dir / "file-inputs"
        )
        self.output_root = Path(
            output_root
            or os.getenv("MCP_FILE_OUTPUT_ROOT", "").strip()
            or self.storage_dir / "file-outputs"
        )
        self.memory_root = Path(
            memory_root
            or os.getenv("MCP_FILE_MEMORY_ROOT", "").strip()
            or self.storage_dir / "memory"
        )
        self.snapshot_path = self.storage_dir / "catalog_workspaces.json"
        self.runtime_uid = self._optional_numeric_env("MCP_FILE_RUNTIME_UID")
        self.runtime_gid = self._optional_numeric_env("MCP_FILE_RUNTIME_GID")
        for root in (self.storage_dir, self.input_root, self.output_root, self.memory_root):
            root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._workspaces: dict[str, CatalogWorkspace] = {}
        self._load()

    def create(
        self,
        project_id: str,
        *,
        display_name: str = "",
        tenant_id: str = "local",
    ) -> CatalogWorkspace:
        if project_id not in FILE_PROJECTS:
            raise CatalogWorkspacePolicyError("该目录条目不接受文件工作区。")
        clean_name = str(display_name or "").strip()[:120] or "未命名工作区"
        persistent = project_id == "basic-memory-mcp"
        now = time.time()
        item = CatalogWorkspace(
            workspace_id=f"mcpws_{uuid.uuid4().hex}",
            tenant_id=str(tenant_id or "local")[:120],
            project_id=project_id,
            display_name=clean_name,
            persistent=persistent,
            expires_at=None if persistent else now + EPHEMERAL_TTL_SECONDS,
        )
        with self._lock:
            self._workspaces[item.workspace_id] = item
            (self.input_root / item.workspace_id).mkdir(parents=True, exist_ok=False)
            output_workspace = self.output_root / item.workspace_id
            output_workspace.mkdir(parents=True, exist_ok=False)
            self._grant_runtime_write(output_workspace)
            if persistent:
                memory_workspace = self.memory_root / item.workspace_id
                notes = memory_workspace / "notes"
                notes.mkdir(
                    parents=True, exist_ok=False
                )
                self._grant_runtime_write(memory_workspace)
                self._grant_runtime_write(notes)
            self._persist_unlocked()
        return item

    def list(self, project_id: str, *, tenant_id: str = "local") -> list[CatalogWorkspace]:
        self.cleanup_expired()
        with self._lock:
            items = [
                item for item in self._workspaces.values()
                if item.project_id == project_id and item.tenant_id == tenant_id
            ]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def get(
        self,
        project_id: str,
        workspace_id: str,
        *,
        tenant_id: str = "local",
    ) -> CatalogWorkspace:
        with self._lock:
            item = self._workspaces.get(str(workspace_id or ""))
        if item is None or item.project_id != project_id or item.tenant_id != tenant_id:
            raise CatalogWorkspaceNotFoundError("MCP 文件工作区不存在。")
        now = time.time()
        if item.expires_at and item.expires_at <= now:
            raise CatalogWorkspaceNotFoundError("MCP 文件工作区已经过期。")
        self._cleanup_expired_artifacts(item)
        if not item.persistent:
            with self._lock:
                item.updated_at = now
                item.expires_at = now + EPHEMERAL_TTL_SECONDS
                self._persist_unlocked()
        return item

    def add_upload(
        self,
        project_id: str,
        workspace_id: str,
        *,
        filename: str,
        relative_path: str,
        content: bytes,
        tenant_id: str = "local",
    ) -> list[CatalogWorkspaceFile]:
        item = self.get(project_id, workspace_id, tenant_id=tenant_id)
        if item.status != "uploading":
            raise CatalogWorkspacePolicyError("已封存工作区不能继续上传文件。")
        if len(content) > MAX_FILE_BYTES:
            raise CatalogWorkspacePolicyError("单个上传文件不能超过 64 MiB。")
        clean_name = Path(str(filename or "upload")).name
        if clean_name.lower().endswith(".zip"):
            return self._extract_zip(item, content)
        path = self._normalize_relative_path(relative_path or clean_name)
        return [self._store_file(item, path, content)]

    def seal(
        self,
        project_id: str,
        workspace_id: str,
        *,
        tenant_id: str = "local",
    ) -> CatalogWorkspace:
        item = self.get(project_id, workspace_id, tenant_id=tenant_id)
        with self._lock:
            if item.status == "sealed":
                return item
            if project_id != "basic-memory-mcp" and not item.files:
                raise CatalogWorkspacePolicyError("请至少上传一个文件后再封存工作区。")
            digest = hashlib.sha256()
            for file_item in sorted(item.files, key=lambda value: value.relative_path):
                digest.update(file_item.relative_path.encode("utf-8"))
                digest.update(file_item.sha256.encode("ascii"))
            item.manifest_sha256 = digest.hexdigest()
            item.status = "sealed"
            item.updated_at = time.time()
            if not item.persistent:
                item.expires_at = item.updated_at + EPHEMERAL_TTL_SECONDS
            self._persist_unlocked()
            return item

    def require_sealed(
        self,
        project_id: str,
        workspace_id: str,
        *,
        tenant_id: str = "local",
    ) -> CatalogWorkspace:
        item = self.get(project_id, workspace_id, tenant_id=tenant_id)
        if item.status != "sealed":
            raise CatalogWorkspacePolicyError("工作区尚未封存，不能绑定到适配器。")
        self._verify_sealed_inputs(item)
        return item

    def _verify_sealed_inputs(self, item: CatalogWorkspace) -> None:
        """Fail closed if sealed input bytes or the file set drifted on disk."""
        root = (self.input_root / item.workspace_id).resolve()
        expected = {value.relative_path: value for value in item.files}
        actual: set[str] = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise CatalogWorkspacePolicyError("封存工作区包含符号链接，已拒绝使用。")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            actual.add(relative)
            file_item = expected.get(relative)
            if file_item is None:
                raise CatalogWorkspacePolicyError("封存工作区的文件集合已经变化。")
            if path.stat().st_size != file_item.size_bytes:
                raise CatalogWorkspacePolicyError("封存工作区的文件内容已经变化。")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != file_item.sha256:
                raise CatalogWorkspacePolicyError("封存工作区的文件内容已经变化。")
        if actual != set(expected):
            raise CatalogWorkspacePolicyError("封存工作区的文件集合已经变化。")

    def file_path(
        self,
        project_id: str,
        workspace_id: str,
        file_id: str,
        *,
        tenant_id: str = "local",
    ) -> Path:
        item = self.require_sealed(project_id, workspace_id, tenant_id=tenant_id)
        file_item = next((value for value in item.files if value.file_id == file_id), None)
        if file_item is None:
            raise CatalogWorkspaceNotFoundError("工作区文件不存在。")
        return self._contained_path(self.input_root / workspace_id, file_item.relative_path)

    def discover_artifacts(
        self,
        project_id: str,
        workspace_id: str,
        *,
        tenant_id: str = "local",
    ) -> list[CatalogWorkspaceArtifact]:
        item = self.get(project_id, workspace_id, tenant_id=tenant_id)
        root = (self.output_root / workspace_id).resolve()
        discovered: list[CatalogWorkspaceArtifact] = []
        with self._lock:
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                existing = next(
                    (value for value in item.artifacts if value.relative_path == relative),
                    None,
                )
                if existing is not None and existing.sha256 == digest:
                    discovered.append(existing)
                    continue
                artifact = CatalogWorkspaceArtifact(
                    artifact_id=f"mcpa_{uuid.uuid4().hex}",
                    relative_path=relative,
                    filename=path.name[:200],
                    size_bytes=len(data),
                    sha256=digest,
                    content_type=mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                )
                if existing is not None:
                    item.artifacts.remove(existing)
                item.artifacts.append(artifact)
                discovered.append(artifact)
            item.updated_at = time.time()
            self._persist_unlocked()
        return discovered

    def artifact_path(
        self,
        project_id: str,
        workspace_id: str,
        artifact_id: str,
        *,
        tenant_id: str = "local",
    ) -> tuple[CatalogWorkspaceArtifact, Path]:
        item = self.get(project_id, workspace_id, tenant_id=tenant_id)
        artifact = next(
            (value for value in item.artifacts if value.artifact_id == artifact_id),
            None,
        )
        if artifact is None or artifact.expires_at <= time.time():
            raise CatalogWorkspaceNotFoundError("MCP 产物不存在或已经过期。")
        path = self._contained_path(self.output_root / workspace_id, artifact.relative_path)
        if not path.is_file() or path.is_symlink():
            raise CatalogWorkspaceNotFoundError("MCP 产物文件不可用。")
        return artifact, path

    def delete(
        self,
        project_id: str,
        workspace_id: str,
        *,
        tenant_id: str = "local",
    ) -> None:
        item = self.get(project_id, workspace_id, tenant_id=tenant_id)
        self._delete_item(item)

    def _delete_item(self, item: CatalogWorkspace) -> None:
        with self._lock:
            self._workspaces.pop(item.workspace_id, None)
            for root in (self.input_root, self.output_root, self.memory_root):
                target = (root / item.workspace_id).resolve()
                if target.parent == root.resolve():
                    shutil.rmtree(target, ignore_errors=True)
            self._persist_unlocked()

    def cleanup_expired(self, *, now: float | None = None) -> list[str]:
        current = time.time() if now is None else float(now)
        with self._lock:
            expired = [
                item for item in self._workspaces.values()
                if not item.persistent and item.expires_at and item.expires_at <= current
            ]
        for item in expired:
            try:
                self._delete_item(item)
            except CatalogWorkspaceError:
                pass
        return [item.workspace_id for item in expired]

    def _cleanup_expired_artifacts(
        self,
        item: CatalogWorkspace,
        *,
        now: float | None = None,
    ) -> None:
        current = time.time() if now is None else float(now)
        expired = [
            artifact for artifact in item.artifacts
            if artifact.expires_at <= current
        ]
        if not expired:
            return
        output_root = (self.output_root / item.workspace_id).resolve()
        with self._lock:
            for artifact in expired:
                try:
                    path = self._contained_path(output_root, artifact.relative_path)
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
                except (CatalogWorkspaceError, OSError):
                    pass
                if artifact in item.artifacts:
                    item.artifacts.remove(artifact)
            item.updated_at = current
            self._persist_unlocked()

    @staticmethod
    def _optional_numeric_env(name: str) -> int | None:
        value = os.getenv(name, "").strip()
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise CatalogWorkspacePolicyError(
                f"{name} 必须是非负整数。"
            ) from exc
        if parsed < 0:
            raise CatalogWorkspacePolicyError(f"{name} 必须是非负整数。")
        return parsed

    def _grant_runtime_write(self, path: Path) -> None:
        """Assign only a workspace-owned writable directory to the sidecar UID."""
        if self.runtime_uid is None and self.runtime_gid is None:
            return
        if os.name != "posix" or not hasattr(os, "chown"):
            raise CatalogWorkspacePolicyError("当前宿主不支持受控运行目录所有权。")
        os.chown(
            path,
            self.runtime_uid if self.runtime_uid is not None else -1,
            self.runtime_gid if self.runtime_gid is not None else -1,
        )

    @staticmethod
    def payload(item: CatalogWorkspace) -> dict[str, Any]:
        value = asdict(item)
        value["file_count"] = len(item.files)
        value["size_bytes"] = sum(file_item.size_bytes for file_item in item.files)
        return value

    def _extract_zip(
        self,
        item: CatalogWorkspace,
        content: bytes,
    ) -> list[CatalogWorkspaceFile]:
        try:
            archive = zipfile.ZipFile(BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise CatalogWorkspacePolicyError("ZIP 文件损坏或格式无效。") from exc
        files = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(item.files) + len(files) > MAX_WORKSPACE_FILES:
            raise CatalogWorkspacePolicyError("工作区最多允许 5000 个文件。")
        total_uncompressed = sum(entry.file_size for entry in files)
        total_compressed = max(1, sum(entry.compress_size for entry in files))
        if total_uncompressed > MAX_WORKSPACE_BYTES:
            raise CatalogWorkspacePolicyError("ZIP 解压后不能超过 512 MiB。")
        if total_uncompressed > total_compressed * MAX_ZIP_RATIO:
            raise CatalogWorkspacePolicyError("ZIP 压缩比超过安全上限。")
        staged: list[tuple[str, bytes]] = []
        for entry in files:
            mode = (entry.external_attr >> 16) & 0o170000
            if mode not in {0, 0o100000}:
                raise CatalogWorkspacePolicyError("ZIP 不能包含链接或特殊设备文件。")
            if entry.file_size > MAX_FILE_BYTES:
                raise CatalogWorkspacePolicyError("ZIP 中单个文件不能超过 64 MiB。")
            relative = self._normalize_relative_path(entry.filename)
            staged.append((relative, archive.read(entry)))
        created: list[CatalogWorkspaceFile] = []
        for relative, data in staged:
            created.append(self._store_file(item, relative, data))
        return created

    def _store_file(
        self,
        item: CatalogWorkspace,
        relative_path: str,
        content: bytes,
    ) -> CatalogWorkspaceFile:
        allowed = PROJECT_EXTENSIONS[item.project_id]
        suffix = Path(relative_path).suffix.lower()
        if allowed is not None and suffix not in allowed:
            raise CatalogWorkspacePolicyError(
                f"该适配器不接受 {suffix or '无扩展名'} 文件。"
            )
        existing_paths = {value.relative_path.casefold() for value in item.files}
        if relative_path.casefold() in existing_paths:
            raise CatalogWorkspacePolicyError("工作区中存在规范化后重名的文件。")
        total_bytes = sum(value.size_bytes for value in item.files) + len(content)
        if len(item.files) >= MAX_WORKSPACE_FILES or total_bytes > MAX_WORKSPACE_BYTES:
            raise CatalogWorkspacePolicyError("工作区超过 5000 个文件或 512 MiB 上限。")
        root = self.input_root / item.workspace_id
        target = self._contained_path(root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_chain(root, target.parent)
        temporary = target.with_name(f".{target.name}.upload-{uuid.uuid4().hex}")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        file_item = CatalogWorkspaceFile(
            file_id=opaque_file_id(item.workspace_id, relative_path),
            relative_path=relative_path,
            filename=target.name[:200],
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            content_type=mimetypes.guess_type(target.name)[0]
            or "application/octet-stream",
        )
        with self._lock:
            item.files.append(file_item)
            item.updated_at = time.time()
            self._persist_unlocked()
        return file_item

    @staticmethod
    def _normalize_relative_path(value: str) -> str:
        clean = unicodedata.normalize("NFC", str(value or "")).replace("\\", "/")
        if not clean or clean.startswith("/") or "\x00" in clean or ":" in clean.split("/")[0]:
            raise CatalogWorkspacePolicyError("上传文件路径无效。")
        path = PurePosixPath(clean)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise CatalogWorkspacePolicyError("上传文件路径不能越过工作区。")
        if len(clean) > 512 or any(len(part) > 120 for part in path.parts):
            raise CatalogWorkspacePolicyError("上传文件路径过长。")
        return path.as_posix()

    @staticmethod
    def _contained_path(root: Path, relative_path: str) -> Path:
        base = root.resolve()
        target = (base / relative_path).resolve(strict=False)
        if target == base or base not in target.parents:
            raise CatalogWorkspacePolicyError("文件路径越过工作区。")
        return target

    @staticmethod
    def _reject_symlink_chain(root: Path, target: Path) -> None:
        base = root.resolve()
        current = target
        while current != base:
            if current.is_symlink():
                raise CatalogWorkspacePolicyError("工作区路径不能包含符号链接。")
            if base not in current.resolve(strict=False).parents and current != base:
                raise CatalogWorkspacePolicyError("工作区路径越界。")
            current = current.parent

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "mcp-catalog-workspaces-v1",
            "workspaces": [asdict(item) for item in self._workspaces.values()],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.snapshot_path)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            for raw in payload.get("workspaces", []):
                raw["files"] = [CatalogWorkspaceFile(**value) for value in raw.get("files", [])]
                raw["artifacts"] = [
                    CatalogWorkspaceArtifact(**value) for value in raw.get("artifacts", [])
                ]
                item = CatalogWorkspace(**raw)
                self._workspaces[item.workspace_id] = item
        except Exception:
            self._workspaces = {}
