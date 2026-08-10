from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .contracts import WorkspaceSource
from .workspace import (
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILE_BYTES,
    MAX_SOURCE_FILES,
    SourceFile,
    SourceSnapshot,
    WorkspaceError,
)


class ProjectSnapshotClient(Protocol):
    async def acquire(
        self, project_id: str, *, expected_head: str | None = None
    ) -> dict[str, Any]: ...

    async def release(self, project_id: str, lease_id: str) -> bool: ...


class ProjectSnapshotWorkspaceSourceAdapter:
    """Copy one exact Project Source lease into a Worker-owned snapshot.

    The caller supplies only opaque project and revision identifiers. The fixed
    snapshot volume and Project Source socket stay behind this trusted adapter.
    """

    _KINDS = {"manifest": "local_clone", "host_snapshot": "host_git"}

    def __init__(self, client: ProjectSnapshotClient, snapshot_root: Path) -> None:
        self._client = client
        self._snapshot_root = Path(snapshot_root)

    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot:
        expected_kind = self._KINDS.get(source.kind)
        if expected_kind is None:
            raise WorkspaceError(
                "Workspace source kind is unsupported.", code="source_not_found"
            )
        lease = await self._client.acquire(
            source.source_id, expected_head=source.revision
        )
        lease_id = lease.get("lease_id")
        if (
            lease.get("kind") != expected_kind
            or lease.get("project_id") != source.source_id
            or lease.get("head") != source.revision
            or not isinstance(lease_id, str)
        ):
            await self._release(source.source_id, lease_id)
            raise WorkspaceError(
                "Project source lease is inconsistent.",
                code="source_revision_changed",
            )

        snapshot: SourceSnapshot | None = None
        failure: BaseException | None = None
        try:
            snapshot = await asyncio.to_thread(self._read_snapshot, source, lease)
        except BaseException as exc:
            failure = exc
        released = await self._release(source.source_id, lease_id)
        if not released:
            raise WorkspaceError(
                "Project source lease could not be released.",
                code="source_release_failed",
            ) from failure
        if failure is not None:
            raise failure
        assert snapshot is not None
        return snapshot

    async def _release(self, project_id: str, lease_id: object) -> bool:
        if not isinstance(lease_id, str):
            return False
        try:
            return await self._client.release(project_id, lease_id)
        except Exception as exc:
            raise WorkspaceError(
                "Project source lease could not be released.",
                code="source_release_failed",
            ) from exc

    def _read_snapshot(
        self, source: WorkspaceSource, lease: dict[str, Any]
    ) -> SourceSnapshot:
        root = self._snapshot_root / "current" / "workspace"
        try:
            root_stat = root.lstat()
        except OSError as exc:
            raise WorkspaceError(
                "Project snapshot is unavailable.", code="source_not_found"
            ) from exc
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise WorkspaceError(
                "Project snapshot is unsafe.", code="source_snapshot_unsafe"
            )

        records: list[tuple[str, bytes, bool]] = []
        total_bytes = 0
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError as exc:
                raise WorkspaceError(
                    "Project snapshot is unavailable.", code="source_not_found"
                ) from exc
            for child in children:
                path = Path(child.path)
                try:
                    metadata = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise WorkspaceError(
                        "Project snapshot changed while reading.",
                        code="source_revision_changed",
                    ) from exc
                relative = path.relative_to(root).as_posix()
                pure = PurePosixPath(relative)
                if (
                    not relative
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or pure.parts[0] == ".git"
                ):
                    raise WorkspaceError(
                        "Project snapshot contains an unsafe path.",
                        code="source_snapshot_unsafe",
                    )
                if stat.S_ISLNK(metadata.st_mode):
                    raise WorkspaceError(
                        "Project snapshot contains a link.",
                        code="source_snapshot_unsafe",
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    stack.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    raise WorkspaceError(
                        "Project snapshot contains an unsupported entry.",
                        code="source_snapshot_unsafe",
                    )
                if metadata.st_size > MAX_SOURCE_FILE_BYTES:
                    raise WorkspaceError(
                        "Project snapshot file is too large.",
                        code="source_file_limit_exceeded",
                    )
                try:
                    content = path.read_bytes()
                    after = path.stat(follow_symlinks=False)
                except OSError as exc:
                    raise WorkspaceError(
                        "Project snapshot changed while reading.",
                        code="source_revision_changed",
                    ) from exc
                if (
                    len(content) != metadata.st_size
                    or (metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_mtime_ns)
                ):
                    raise WorkspaceError(
                        "Project snapshot changed while reading.",
                        code="source_revision_changed",
                    )
                total_bytes += len(content)
                if len(records) >= MAX_SOURCE_FILES or total_bytes > MAX_SOURCE_BYTES:
                    raise WorkspaceError(
                        "Project snapshot exceeds Worker limits.",
                        code="source_limit_exceeded",
                    )
                records.append(
                    (relative, content, bool(metadata.st_mode & stat.S_IXUSR))
                )

        records.sort(key=lambda item: item[0])
        fingerprint = hashlib.sha256()
        for relative, content, _executable in records:
            fingerprint.update(relative.encode("utf-8"))
            fingerprint.update(b"\0")
            fingerprint.update(str(len(content)).encode("ascii"))
            fingerprint.update(b"\0")
            fingerprint.update(hashlib.sha256(content).digest())
        if (
            lease.get("file_count") != len(records)
            or lease.get("total_bytes") != total_bytes
            or lease.get("fingerprint") != fingerprint.hexdigest()
        ):
            raise WorkspaceError(
                "Project snapshot does not match its lease.",
                code="source_revision_changed",
            )
        return SourceSnapshot(
            source=source,
            files=tuple(
                SourceFile(path=relative, content=content, executable=executable)
                for relative, content, executable in records
            ),
        )
