from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from server.coding_runtime.projects import (
    MAX_PROJECT_AGENTS_BYTES,
    MAX_PROJECT_SNAPSHOT_BYTES,
    MAX_PROJECT_SNAPSHOT_FILE_BYTES,
    MAX_PROJECT_SNAPSHOT_FILES,
    ProjectCatalogError,
    ProjectManifestEntry,
    ProjectState,
    build_safe_git_command,
    build_safe_git_environment,
    inspect_project,
    load_project_manifest,
    project_snapshot_path_is_allowed,
    resolve_project_path,
    validate_git_tree,
)


MAX_SOURCE_FRAME_BYTES = 64 * 1024
SNAPSHOT_BUILD_TIMEOUT_SECONDS = 60.0
SOURCE_SOCKET_PATH = Path(
    os.getenv(
        "CODING_PROJECT_SOURCE_SOCKET_PATH",
        "/run/modelmirror-coding-projects/source.sock",
    )
)
PROJECTS_ROOT = Path(os.getenv("CODING_PROJECTS_ROOT", "/projects-root"))
SNAPSHOT_SLOT = Path(os.getenv("CODING_PROJECT_SNAPSHOT_ROOT", "/snapshot-slot"))

class ProjectSourceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SnapshotLimits:
    max_files: int = MAX_PROJECT_SNAPSHOT_FILES
    max_bytes: int = MAX_PROJECT_SNAPSHOT_BYTES
    max_file_bytes: int = MAX_PROJECT_SNAPSHOT_FILE_BYTES
    max_agents_bytes: int = MAX_PROJECT_AGENTS_BYTES


@dataclass(frozen=True, slots=True)
class SnapshotLease:
    lease_id: str
    project_id: str
    name: str
    branch: str
    head: str
    fingerprint: str
    file_count: int
    total_bytes: int
    hidden_files: int
    created_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "project_id": self.project_id,
            "name": self.name,
            "branch": self.branch,
            "head": self.head,
            "fingerprint": self.fingerprint,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "hidden_files": self.hidden_files,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    mode: str
    object_id: str
    path: str


class ProjectSnapshotBroker:
    """Build one immutable, HEAD-only snapshot without exposing the projects root."""

    def __init__(
        self,
        projects_root: Path = PROJECTS_ROOT,
        snapshot_slot: Path = SNAPSHOT_SLOT,
        *,
        limits: SnapshotLimits | None = None,
    ) -> None:
        self._projects_root = Path(projects_root)
        self._snapshot_slot = Path(snapshot_slot)
        self._limits = limits or SnapshotLimits()
        self._lock = threading.Lock()
        self._active: SnapshotLease | None = None
        self._prepare_slot()

    def health(self) -> dict[str, object]:
        try:
            projects = load_project_manifest(self._projects_root)
        except (ProjectCatalogError, ProjectSourceError):
            return {
                "service": "coding-project-source",
                "configured": False,
                "available": False,
                "reason": "project_source_not_configured",
                "active": self._active is not None,
            }
        return {
            "service": "coding-project-source",
            "configured": True,
            "available": True,
            "reason": None,
            "project_count": len(projects),
            "active": self._active is not None,
        }

    def list_projects(self) -> tuple[dict[str, Any], ...]:
        entries = load_project_manifest(self._projects_root)
        return tuple(
            inspect_project(self._projects_root, entry).to_public_dict()
            for entry in entries
        )

    def check(self, project_id: str, expected_head: str) -> dict[str, Any]:
        if not _valid_opaque_id(project_id) or not _valid_object_id(expected_head):
            raise ProjectSourceError("invalid_request", "Project check is invalid")
        entry = self._find_entry(project_id)
        summary = inspect_project(self._projects_root, entry)
        if summary.state is not ProjectState.AVAILABLE or summary.head is None:
            raise ProjectSourceError(summary.reason or "project_unavailable", "Project is unavailable")
        if summary.head != expected_head.lower():
            raise ProjectSourceError("project_changed", "Project HEAD changed")
        return summary.to_public_dict()

    def acquire(self, project_id: str, expected_head: str | None = None) -> SnapshotLease:
        if not _valid_opaque_id(project_id) or (
            expected_head is not None and not _valid_object_id(expected_head)
        ):
            raise ProjectSourceError("invalid_request", "Snapshot request is invalid")
        with self._lock:
            entry = self._find_entry(project_id)
            summary = inspect_project(self._projects_root, entry)
            if summary.state is not ProjectState.AVAILABLE or not summary.head or not summary.branch:
                raise ProjectSourceError(summary.reason or "project_unavailable", "Project is unavailable")
            if expected_head is not None and summary.head != expected_head.lower():
                raise ProjectSourceError("project_changed", "Project HEAD changed")
            if self._active is not None:
                if self._active.project_id == project_id and self._active.head == summary.head:
                    return self._active
                raise ProjectSourceError("snapshot_busy", "Another project snapshot is active")

            self._clear_slot()
            staging = self._snapshot_slot / f".staging-{secrets.token_hex(12)}"
            workspace = staging / "workspace"
            try:
                workspace.mkdir(parents=True, mode=0o700)
                lease = self._build_snapshot(entry, summary.branch, summary.head, staging, workspace)
                rechecked = inspect_project(self._projects_root, entry)
                if (
                    rechecked.state is not ProjectState.AVAILABLE
                    or rechecked.head != summary.head
                    or rechecked.branch != summary.branch
                ):
                    raise ProjectSourceError("project_changed", "Project changed while snapshotting")
                _write_json(staging / "lease.json", lease.to_dict())
                staging.replace(self._snapshot_slot / "current")
                self._active = lease
                return lease
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                self._clear_slot()
                raise

    def release(self, project_id: str, lease_id: str) -> bool:
        if not _valid_opaque_id(project_id) or not _valid_opaque_id(lease_id):
            raise ProjectSourceError("invalid_request", "Snapshot release is invalid")
        with self._lock:
            if self._active is None:
                self._clear_slot()
                return False
            if self._active.project_id != project_id or self._active.lease_id != lease_id:
                raise ProjectSourceError("snapshot_lease_mismatch", "Snapshot lease does not match")
            self._active = None
            self._clear_slot()
            return True

    def close(self) -> None:
        with self._lock:
            self._active = None
            self._clear_slot()

    def _find_entry(self, project_id: str) -> ProjectManifestEntry:
        for entry in load_project_manifest(self._projects_root):
            if secrets.compare_digest(entry.project_id, project_id):
                return entry
        raise ProjectSourceError("project_not_found", "Project is not registered")

    def _build_snapshot(
        self,
        entry: ProjectManifestEntry,
        branch: str,
        head: str,
        staging: Path,
        workspace: Path,
    ) -> SnapshotLease:
        project_path = resolve_project_path(self._projects_root, entry.relative_path)
        tree_result = _run_git(project_path, ("ls-tree", "-r", "-z", "--full-tree", head))
        if tree_result.returncode != 0:
            raise ProjectSourceError("snapshot_git_failed", "Git tree could not be read")
        validate_git_tree(tree_result.stdout)
        entries = _parse_tree(tree_result.stdout)
        if len(entries) > self._limits.max_files:
            raise ProjectSourceError("snapshot_file_limit_exceeded", "Project has too many files")

        hidden_files = sum(1 for item in entries if not project_snapshot_path_is_allowed(item.path))
        visible = tuple(item for item in entries if project_snapshot_path_is_allowed(item.path))
        fingerprint = hashlib.sha256()
        total_bytes = 0
        process = subprocess.Popen(
            build_safe_git_command(project_path, ("cat-file", "--batch")),
            cwd=project_path,
            env=build_safe_git_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        timeout = threading.Timer(SNAPSHOT_BUILD_TIMEOUT_SECONDS, process.kill)
        timeout.daemon = True
        timeout.start()
        assert process.stdin is not None and process.stdout is not None
        try:
            for item in visible:
                process.stdin.write(item.object_id.encode("ascii") + b"\n")
                process.stdin.flush()
                header = process.stdout.readline()
                object_id, object_type, size = _parse_cat_file_header(header)
                if object_id != item.object_id or object_type != "blob":
                    raise ProjectSourceError("snapshot_git_failed", "Git returned a different object")
                if size > self._limits.max_file_bytes:
                    raise ProjectSourceError("snapshot_file_too_large", "Project file is too large")
                total_bytes += size
                if total_bytes > self._limits.max_bytes:
                    raise ProjectSourceError("snapshot_size_limit_exceeded", "Project snapshot is too large")
                content = _read_exact(process.stdout, size)
                if process.stdout.read(1) != b"\n":
                    raise ProjectSourceError("snapshot_git_failed", "Git object frame is invalid")
                if item.path == "AGENTS.md":
                    if size > self._limits.max_agents_bytes:
                        raise ProjectSourceError("agents_instructions_too_large", "AGENTS.md is too large")
                    try:
                        content.decode("utf-8", errors="strict")
                    except UnicodeError as exc:
                        raise ProjectSourceError("agents_instructions_invalid", "AGENTS.md must use UTF-8") from exc
                target = workspace.joinpath(*PurePosixPath(item.path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                content_hash = hashlib.sha256(content)
                fingerprint.update(item.path.encode("utf-8"))
                fingerprint.update(b"\0")
                fingerprint.update(str(size).encode("ascii"))
                fingerprint.update(b"\0")
                fingerprint.update(content_hash.digest())
            process.stdin.close()
            if process.wait(timeout=10) != 0:
                raise ProjectSourceError("snapshot_git_failed", "Git object reader failed")
        except Exception:
            with contextlib.suppress(Exception):
                process.kill()
            with contextlib.suppress(Exception):
                process.wait(timeout=2)
            raise
        finally:
            timeout.cancel()

        lease = SnapshotLease(
            lease_id=secrets.token_urlsafe(24),
            project_id=entry.project_id,
            name=entry.name,
            branch=branch,
            head=head,
            fingerprint=fingerprint.hexdigest(),
            file_count=len(visible),
            total_bytes=total_bytes,
            hidden_files=hidden_files,
            created_at=time.time(),
        )
        return lease

    def _prepare_slot(self) -> None:
        if self._snapshot_slot.is_symlink():
            raise ProjectSourceError("snapshot_slot_unsafe", "Snapshot slot is unsafe")
        self._snapshot_slot.mkdir(parents=True, exist_ok=True)
        self._clear_slot()

    def _clear_slot(self) -> None:
        if self._snapshot_slot.is_symlink() or not self._snapshot_slot.is_dir():
            raise ProjectSourceError("snapshot_slot_unsafe", "Snapshot slot is unsafe")
        for child in self._snapshot_slot.iterdir():
            if child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


class CodingProjectSourceServer:
    def __init__(
        self,
        socket_path: Path = SOURCE_SOCKET_PATH,
        *,
        broker: ProjectSnapshotBroker | None = None,
    ) -> None:
        self._socket_path = Path(socket_path)
        self._broker = broker or ProjectSnapshotBroker()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_SOURCE_FRAME_BYTES:
                raise ProjectSourceError("invalid_request", "Project source request is invalid")
            try:
                request = json.loads(raw.decode("utf-8", errors="strict"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProjectSourceError("invalid_request", "Project source request is invalid") from exc
            if not isinstance(request, dict):
                raise ProjectSourceError("invalid_request", "Project source request is invalid")
            response = await self._dispatch(request)
            await self._send(writer, {"ok": True, **response})
        except (ProjectCatalogError, ProjectSourceError) as exc:
            await self._send(writer, {"ok": False, "code": getattr(exc, "code", "project_source_failed")})
        except Exception:
            await self._send(writer, {"ok": False, "code": "project_source_internal_error"})
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "health":
            _require_keys(request, {"action"})
            return await asyncio.to_thread(self._broker.health)
        if action == "list":
            _require_keys(request, {"action"})
            projects = await asyncio.to_thread(self._broker.list_projects)
            return {"projects": list(projects)}
        if action == "check":
            _require_keys(request, {"action", "project_id", "expected_head"})
            project = await asyncio.to_thread(
                self._broker.check,
                request["project_id"],
                request["expected_head"],
            )
            return {"project": project}
        if action == "acquire":
            _require_keys(request, {"action", "project_id", "expected_head"})
            lease = await asyncio.to_thread(
                self._broker.acquire,
                request["project_id"],
                request["expected_head"],
            )
            return {"lease": lease.to_dict()}
        if action == "release":
            _require_keys(request, {"action", "project_id", "lease_id"})
            released = await asyncio.to_thread(
                self._broker.release,
                request["project_id"],
                request["lease_id"],
            )
            return {"released": released}
        raise ProjectSourceError("unsupported_action", "Project source action is unsupported")

    async def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self._socket_path),
            limit=MAX_SOURCE_FRAME_BYTES + 1,
        )
        os.chmod(self._socket_path, 0o660)
        try:
            async with server:
                await server.serve_forever()
        finally:
            self._socket_path.unlink(missing_ok=True)
            self._broker.close()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_SOURCE_FRAME_BYTES:
            encoded = b'{"ok":false,"code":"response_too_large"}\n'
        writer.write(encoded)
        await writer.drain()


def _run_git(project_path: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        build_safe_git_command(project_path, arguments),
        cwd=project_path,
        env=build_safe_git_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def _parse_tree(payload: bytes) -> tuple[_TreeEntry, ...]:
    entries: list[_TreeEntry] = []
    for record in payload.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", maxsplit=1)
        raw_mode, object_type, raw_object_id = header.split(b" ", maxsplit=2)
        if object_type != b"blob":
            raise ProjectSourceError("snapshot_git_failed", "Git tree entry is unsupported")
        entries.append(
            _TreeEntry(
                mode=raw_mode.decode("ascii"),
                object_id=raw_object_id.decode("ascii").lower(),
                path=raw_path.decode("utf-8", errors="strict"),
            )
        )
    return tuple(entries)


def _parse_cat_file_header(value: bytes) -> tuple[str, str, int]:
    try:
        raw_object_id, raw_type, raw_size = value.rstrip(b"\n").split(b" ", maxsplit=2)
        object_id = raw_object_id.decode("ascii").lower()
        object_type = raw_type.decode("ascii")
        size_text = raw_size.decode("ascii")
        if not size_text.isdecimal():
            raise ValueError
        size = int(size_text)
    except (UnicodeError, ValueError) as exc:
        raise ProjectSourceError("snapshot_git_failed", "Git object header is invalid") from exc
    return object_id, object_type, size


def _read_exact(stream: Any, size: int) -> bytes:
    remaining = size
    chunks: list[bytes] = []
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise ProjectSourceError("snapshot_git_failed", "Git object was truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    path.write_bytes(encoded)


def _require_keys(request: dict[str, Any], expected: set[str]) -> None:
    if set(request) != expected:
        raise ProjectSourceError("invalid_request", "Project source request fields are invalid")


def _valid_opaque_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 20 <= len(value) <= 64
        and value.isascii()
        and all(character.isalnum() or character in "-_" for character in value)
    )


def _valid_object_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and value.isascii()
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


async def main() -> None:
    await CodingProjectSourceServer().serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
