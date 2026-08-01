from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


SandboxOperationStatus = Literal["running", "completed", "failed"]


class SandboxStoreError(RuntimeError):
    """Base error for sandbox runtime metadata."""


class SandboxNotFoundError(SandboxStoreError):
    """Raised when a workspace, operation, or artifact is missing."""


class SandboxValidationError(SandboxStoreError):
    """Raised for invalid sandbox metadata requests."""


@dataclass(slots=True)
class SandboxWorkspace:
    workspace_id: str
    scope_type: str
    scope_id: str
    node_id: str
    quota_bytes: int = 256 * 1024 * 1024
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None


@dataclass(slots=True)
class SandboxOperation:
    operation_id: str
    workspace_id: str
    tool_name: str
    status: SandboxOperationStatus
    command_name: str | None = None
    output_length: int = 0
    exit_code: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None


@dataclass(slots=True)
class RuntimeArtifact:
    artifact_id: str
    workspace_id: str
    filename: str
    relative_path: str
    content_type: str
    size_bytes: int
    sha256: str
    source_run_id: str | None = None
    source_node_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class SandboxWorkspaceStore:
    """Atomic metadata store for isolated workspaces, operations, and artifacts."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        workspace_root: str | Path | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.workspace_root = Path(
            workspace_root
            or os.getenv("SANDBOX_WORKSPACE_ROOT", "").strip()
            or self.storage_dir / "sandbox-workspaces"
        )
        self.snapshot_path = self.storage_dir / "sandbox_runtime.json"
        self._lock = threading.RLock()
        self._workspaces: dict[str, SandboxWorkspace] = {}
        self._operations: dict[str, SandboxOperation] = {}
        self._artifacts: dict[str, RuntimeArtifact] = {}
        self._load()

    def get_or_create_workspace(
        self,
        *,
        scope_type: str,
        scope_id: str,
        node_id: str,
        quota_bytes: int = 256 * 1024 * 1024,
        expires_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SandboxWorkspace:
        clean_scope_type = self._required(scope_type, "scope_type", 80)
        clean_scope_id = self._required(scope_id, "scope_id", 400)
        clean_node_id = self._required(node_id, "node_id", 200)
        clean_quota = max(1 * 1024 * 1024, min(int(quota_bytes), 1024 * 1024 * 1024))
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._workspaces.values()
                    if item.scope_type == clean_scope_type
                    and item.scope_id == clean_scope_id
                    and item.node_id == clean_node_id
                    and item.status == "active"
                ),
                None,
            )
            if existing is not None:
                existing.quota_bytes = clean_quota
                existing.updated_at = time.time()
                if metadata:
                    existing.metadata.update(self._safe_metadata(metadata))
                self._persist_unlocked()
                return existing
            item = SandboxWorkspace(
                workspace_id=f"ws_{uuid.uuid4().hex}",
                scope_type=clean_scope_type,
                scope_id=clean_scope_id,
                node_id=clean_node_id,
                quota_bytes=clean_quota,
                expires_at=expires_at,
                metadata=self._safe_metadata(metadata or {}),
            )
            self._workspaces[item.workspace_id] = item
            self._persist_unlocked()
            return item

    def get_workspace(self, workspace_id: str) -> SandboxWorkspace:
        with self._lock:
            item = self._workspaces.get(workspace_id)
            if item is None:
                raise SandboxNotFoundError("Sandbox workspace not found.")
            return item

    def list_workspaces(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[SandboxWorkspace]:
        with self._lock:
            items = list(self._workspaces.values())
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        items.sort(key=lambda item: (item.updated_at, item.workspace_id), reverse=True)
        return items[: max(1, min(int(limit), 500))]

    def start_operation(
        self,
        operation_id: str,
        *,
        workspace_id: str,
        tool_name: str,
        command_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SandboxOperation:
        with self._lock:
            existing = self._operations.get(operation_id)
            if existing is not None:
                return existing
            self.get_workspace(workspace_id)
            item = SandboxOperation(
                operation_id=self._required(operation_id, "operation_id", 200),
                workspace_id=workspace_id,
                tool_name=self._required(tool_name, "tool_name", 200),
                status="running",
                command_name=str(command_name or "")[:100] or None,
                metadata=self._safe_metadata(metadata or {}),
            )
            self._operations[item.operation_id] = item
            self._persist_unlocked()
            return item

    def complete_operation(
        self,
        operation_id: str,
        *,
        output_length: int = 0,
        exit_code: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SandboxOperation:
        with self._lock:
            item = self._require_operation(operation_id)
            item.status = "completed"
            item.output_length = max(0, int(output_length))
            item.exit_code = exit_code
            item.error = None
            if metadata:
                item.metadata.update(self._safe_metadata(metadata))
            item.updated_at = time.time()
            item.completed_at = item.updated_at
            self._persist_unlocked()
            return item

    def fail_operation(self, operation_id: str, *, error: str) -> SandboxOperation:
        with self._lock:
            item = self._require_operation(operation_id)
            item.status = "failed"
            item.error = str(error or "")[:1000]
            item.updated_at = time.time()
            item.completed_at = item.updated_at
            self._persist_unlocked()
            return item

    def list_operations(self, workspace_id: str, *, limit: int = 100) -> list[SandboxOperation]:
        self.get_workspace(workspace_id)
        with self._lock:
            items = [item for item in self._operations.values() if item.workspace_id == workspace_id]
        items.sort(key=lambda item: (item.created_at, item.operation_id), reverse=True)
        return items[: max(1, min(int(limit), 500))]

    def register_artifact(
        self,
        *,
        artifact_id: str,
        workspace_id: str,
        filename: str,
        relative_path: str,
        size_bytes: int,
        sha256: str,
        source_run_id: str | None = None,
        source_node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeArtifact:
        with self._lock:
            existing = self._artifacts.get(artifact_id)
            if existing is not None:
                return existing
            workspace = self.get_workspace(workspace_id)
            item = RuntimeArtifact(
                artifact_id=self._required(artifact_id, "artifact_id", 200),
                workspace_id=workspace_id,
                filename=Path(filename).name[:200],
                relative_path=self._safe_relative_path(relative_path),
                content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                size_bytes=max(0, int(size_bytes)),
                sha256=str(sha256 or "")[:128],
                source_run_id=str(source_run_id or "")[:200] or None,
                source_node_id=str(source_node_id or "")[:200] or None,
                metadata=self._safe_metadata(metadata or {}),
            )
            self._artifacts[item.artifact_id] = item
            if item.artifact_id not in workspace.artifact_ids:
                workspace.artifact_ids.append(item.artifact_id)
            workspace.updated_at = time.time()
            self._persist_unlocked()
            return item

    def get_artifact(self, artifact_id: str) -> RuntimeArtifact:
        with self._lock:
            item = self._artifacts.get(artifact_id)
            if item is None:
                raise SandboxNotFoundError("Sandbox artifact not found.")
            return item

    def artifact_path(self, artifact_id: str) -> Path:
        artifact = self.get_artifact(artifact_id)
        workspace = self.get_workspace(artifact.workspace_id)
        base = (self.workspace_root / workspace.workspace_id).resolve()
        target = (base / artifact.relative_path).resolve(strict=False)
        if base not in target.parents or target.is_symlink():
            raise SandboxValidationError("Unsafe sandbox artifact path.")
        return target

    def cleanup_expired(self, *, now: float | None = None) -> list[str]:
        current = time.time() if now is None else float(now)
        expired: list[str] = []
        with self._lock:
            for item in self._workspaces.values():
                if item.status == "active" and item.expires_at and item.expires_at <= current:
                    item.status = "expired"
                    item.updated_at = current
                    expired.append(item.workspace_id)
            if expired:
                self._persist_unlocked()
        return expired

    @staticmethod
    def workspace_payload(item: SandboxWorkspace, operations: list[SandboxOperation] | None = None) -> dict[str, Any]:
        payload = asdict(item)
        payload["operations"] = [asdict(operation) for operation in operations or []]
        return payload

    @staticmethod
    def artifact_payload(item: RuntimeArtifact) -> dict[str, Any]:
        return asdict(item)

    def _require_operation(self, operation_id: str) -> SandboxOperation:
        item = self._operations.get(operation_id)
        if item is None:
            raise SandboxNotFoundError("Sandbox operation not found.")
        return item

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "sandbox-runtime-v1",
            "workspaces": [asdict(item) for item in self._workspaces.values()],
            "operations": [asdict(item) for item in self._operations.values()],
            "artifacts": [asdict(item) for item in self._artifacts.values()],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.snapshot_path)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            for raw in payload.get("workspaces", []):
                item = SandboxWorkspace(**raw)
                self._workspaces[item.workspace_id] = item
            for raw in payload.get("operations", []):
                item = SandboxOperation(**raw)
                if item.status == "running":
                    item.status = "failed"
                    item.error = "Sandbox operation was interrupted by a service restart."
                    item.completed_at = time.time()
                self._operations[item.operation_id] = item
            for raw in payload.get("artifacts", []):
                item = RuntimeArtifact(**raw)
                self._artifacts[item.artifact_id] = item
        except Exception:
            self._workspaces = {}
            self._operations = {}
            self._artifacts = {}

    @staticmethod
    def _required(value: Any, name: str, limit: int) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise SandboxValidationError(f"{name} is required.")
        if len(clean) > limit:
            raise SandboxValidationError(f"{name} exceeds {limit} characters.")
        return clean

    @staticmethod
    def _safe_relative_path(value: str) -> str:
        clean = str(value or "").replace("\\", "/").strip("/")
        parts = Path(clean).parts
        if not clean or any(part in {"", ".", ".."} for part in parts):
            raise SandboxValidationError("Unsafe relative path.")
        return "/".join(parts)

    @classmethod
    def _safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, inner in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(token in normalized for token in ("password", "secret", "token", "api_key", "authorization", "path")):
                continue
            if inner is None or isinstance(inner, (bool, int, float)):
                safe[str(key)] = inner
            elif isinstance(inner, str):
                safe[str(key)] = inner[:1000]
            elif isinstance(inner, list):
                safe[str(key)] = [str(item)[:300] for item in inner[:100]]
        return safe
