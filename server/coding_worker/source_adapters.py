from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .contracts import WorkspaceSource
from .workspace import (
    MAX_EXTERNAL_SOURCE_FILE_BYTES,
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILE_BYTES,
    MAX_SOURCE_FILES,
    SourceFile,
    SourceAdmissionReceipt,
    SourceSnapshot,
    WorkspaceError,
    source_admission_receipt,
)


class ProjectSnapshotClient(Protocol):
    async def check(self, project_id: str, expected_head: str) -> dict[str, Any]: ...

    async def acquire(
        self, project_id: str, *, expected_head: str | None = None
    ) -> dict[str, Any]: ...

    async def release(self, project_id: str, lease_id: str) -> bool: ...

    async def import_uploaded(
        self,
        *,
        upload_id: str,
        archive_sha256: str,
        project_id: str,
        name: str,
        branch: str,
        head: str,
    ) -> dict[str, Any]: ...


class ProjectHostSnapshotClient(Protocol):
    def check_project(
        self, project_id: str, head: str, branch: str | None
    ) -> dict[str, Any]: ...

    async def request_snapshot(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> dict[str, Any]: ...

    def finish_transfer(self, transfer_id: str) -> None: ...


_DANGEROUS_CONFIG = re.compile(
    r"^(?:include(?:if)?\.|filter\.|credential\.|url\.|diff\..*\.textconv$|"
    r"core\.worktree$|core\.excludesfile$|extensions\.(?:worktreeconfig|partialclone|refstorage)$|"
    r"remote\..*\.(?:promisor|partialclonefilter)$)",
    re.IGNORECASE,
)


def _public_revision_matches(expected: str, observed: object) -> bool:
    """Accept the path-free public SHA prefix returned after an exact check."""

    return (
        isinstance(observed, str)
        and re.fullmatch(r"[a-f0-9]{7,40}", observed) is not None
        and expected.startswith(observed)
    )


class BuiltinGitWorkspaceSourceAdapter:
    """Read tracked blobs from one deployment-fixed ModelMirror revision."""

    def __init__(self, root: Path, *, source_id: str, revision: str) -> None:
        self._root = Path(root)
        self._source_id = source_id
        self._revision = revision.lower()
        if re.fullmatch(r"[a-f0-9]{40}", self._revision) is None:
            raise ValueError("builtin revision must be a full commit id")

    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot:
        self._require_registered(source, revision_code="source_not_found")
        return await asyncio.to_thread(self._read_revision, source)

    async def admit(self, source: WorkspaceSource) -> SourceAdmissionReceipt:
        self._require_registered(source)
        file_count, total_bytes = await asyncio.to_thread(self._preflight_admission)
        return source_admission_receipt(
            source,
            facts={
                "adapter": "builtin",
                "revision": self._revision,
                "file_count": file_count,
                "total_bytes": total_bytes,
                "limit_policy": "builtin-16m-v1",
            },
        )

    def _require_registered(
        self, source: WorkspaceSource, *, revision_code: str = "source_revision_changed"
    ) -> None:
        if source.kind != "builtin" or source.source_id != self._source_id:
            raise WorkspaceError(
                "Builtin source is not registered at this revision.",
                code="source_not_found",
            )
        if source.revision != self._revision:
            raise WorkspaceError(
                "Builtin source revision changed.",
                code=revision_code,
            )

    def _read_revision(self, source: WorkspaceSource) -> SourceSnapshot:
        self._preflight_revision()
        entries, _total_bytes = self._revision_entries()
        contents = self._read_blobs(entries)
        return SourceSnapshot(
            source=source,
            files=tuple(
                SourceFile(path=path, content=content, executable=executable)
                for (path, _object_id, _size, executable), content in zip(
                    entries, contents, strict=True
                )
            ),
        )

    def _preflight_admission(self) -> tuple[int, int]:
        self._preflight_revision()
        entries, total_bytes = self._revision_entries()
        return len(entries), total_bytes

    def _revision_entries(self) -> tuple[list[tuple[str, str, int, bool]], int]:
        tree = self._git("ls-tree", "-r", "-z", "-l", self._revision)
        if len(tree.stdout) > 16 * 1024 * 1024:
            raise WorkspaceError(
                "Builtin tree metadata is too large.", code="source_limit_exceeded"
            )
        entries: list[tuple[str, str, int, bool]] = []
        total_bytes = 0
        for raw in tree.stdout.split(b"\0"):
            if not raw:
                continue
            try:
                header, encoded_path = raw.split(b"\t", 1)
                mode, object_type, object_id, encoded_size = header.split()
                relative = encoded_path.decode("utf-8", errors="strict")
                size = int(encoded_size)
            except (ValueError, UnicodeError) as exc:
                raise WorkspaceError(
                    "Builtin tree is invalid.", code="source_snapshot_unsafe"
                ) from exc
            pure = PurePosixPath(relative)
            if size > MAX_SOURCE_FILE_BYTES:
                raise WorkspaceError(
                    "Builtin tree file exceeds Worker limits.",
                    code="source_limit_exceeded",
                )
            if (
                object_type != b"blob"
                or mode not in {b"100644", b"100755"}
                or pure.is_absolute()
                or ".." in pure.parts
                or not pure.parts
                or pure.parts[0] == ".git"
                or size < 0
            ):
                raise WorkspaceError(
                    "Builtin tree contains an unsupported entry.",
                    code="source_snapshot_unsafe",
                )
            total_bytes += size
            if len(entries) >= MAX_SOURCE_FILES or total_bytes > MAX_SOURCE_BYTES:
                raise WorkspaceError(
                    "Builtin tree exceeds Worker limits.",
                    code="source_limit_exceeded",
                )
            entries.append(
                (
                    relative,
                    object_id.decode("ascii", errors="strict"),
                    size,
                    mode == b"100755",
                )
            )
        return entries, total_bytes

    def _preflight_revision(self) -> None:
        try:
            root_metadata = self._root.lstat()
            git_metadata = (self._root / ".git").lstat()
        except OSError as exc:
            raise WorkspaceError(
                "Builtin source is unavailable.", code="source_not_found"
            ) from exc
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_ISLNK(root_metadata.st_mode)
            or stat.S_ISLNK(git_metadata.st_mode)
            or not (
                stat.S_ISDIR(git_metadata.st_mode)
                or stat.S_ISREG(git_metadata.st_mode)
            )
        ):
            raise WorkspaceError(
                "Builtin source repository is unsafe.",
                code="source_snapshot_unsafe",
            )
        config = self._git("config", "--local", "--no-includes", "--name-only", "--list")
        names = config.stdout.decode("utf-8", errors="strict").splitlines()
        if any(_DANGEROUS_CONFIG.search(name.strip()) for name in names):
            raise WorkspaceError(
                "Builtin source configuration is unsafe.",
                code="source_snapshot_unsafe",
            )
        resolved = self._git("rev-parse", "--verify", f"{self._revision}^{{commit}}")
        if resolved.stdout.decode("ascii", errors="strict").strip().lower() != self._revision:
            raise WorkspaceError(
                "Builtin revision changed.", code="source_revision_changed"
            )

    def _read_blobs(
        self, entries: list[tuple[str, str, int, bool]]
    ) -> list[bytes]:
        command = self._git_command("cat-file", "--batch")
        try:
            process = subprocess.Popen(
                command,
                cwd=self._root,
                env=self._git_environment(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise WorkspaceError(
                "Builtin object reader is unavailable.", code="source_not_found"
            ) from exc
        assert process.stdin is not None and process.stdout is not None
        result: list[bytes] = []
        try:
            for _path, object_id, expected_size, _executable in entries:
                process.stdin.write(object_id.encode("ascii") + b"\n")
                process.stdin.flush()
                header = process.stdout.readline()
                parts = header.rstrip(b"\n").split(b" ")
                if (
                    len(parts) != 3
                    or parts[0].decode("ascii", errors="strict") != object_id
                    or parts[1] != b"blob"
                    or int(parts[2]) != expected_size
                ):
                    raise WorkspaceError(
                        "Builtin object response is invalid.",
                        code="source_revision_changed",
                    )
                content = process.stdout.read(expected_size)
                if len(content) != expected_size or process.stdout.read(1) != b"\n":
                    raise WorkspaceError(
                        "Builtin object response is truncated.",
                        code="source_revision_changed",
                    )
                result.append(content)
            process.stdin.close()
            if process.wait(timeout=15) != 0:
                raise WorkspaceError(
                    "Builtin object reader failed.", code="source_revision_changed"
                )
            return result
        except Exception:
            process.kill()
            process.wait(timeout=5)
            raise

    def _git(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                self._git_command(*args),
                cwd=self._root,
                env=self._git_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspaceError(
                "Builtin repository could not be inspected.",
                code="source_snapshot_unsafe",
            ) from exc

    def _git_command(self, *args: str) -> list[str]:
        null = os.devnull
        return [
            "git", "-c", f"core.hooksPath={null}", "-c", f"core.attributesFile={null}",
            "-c", f"core.excludesFile={null}", "-c", "credential.helper=", "-c", "protocol.file.allow=never",
            "-c", "protocol.ext.allow=never", *args,
        ]

    @staticmethod
    def _git_environment() -> dict[str, str]:
        keep = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR"}}
        return {
            **keep,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_SSH_COMMAND": "false",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }


class ProjectSnapshotWorkspaceSourceAdapter:
    """Copy one exact Project Source lease into a Worker-owned snapshot.

    The caller supplies only opaque project and revision identifiers. The fixed
    snapshot volume and Project Source socket stay behind this trusted adapter.
    """

    _KINDS = {"manifest": "local_clone"}

    def __init__(self, client: ProjectSnapshotClient, snapshot_root: Path) -> None:
        self._client = client
        self._snapshot_root = Path(snapshot_root)

    async def admit(self, source: WorkspaceSource) -> SourceAdmissionReceipt:
        expected_kind = self._KINDS.get(source.kind)
        if expected_kind is None:
            raise WorkspaceError(
                "Workspace source kind is unsupported.", code="source_not_found"
            )
        project = await self._client.check(source.source_id, source.revision)
        if project.get("id") != source.source_id or project.get("kind") != expected_kind:
            raise WorkspaceError(
                "Project source admission is inconsistent.",
                code="source_not_found",
            )
        if project.get("state") != "available":
            raise WorkspaceError(
                "Project source is temporarily unavailable.",
                code="source_temporarily_unavailable",
            )
        if not _public_revision_matches(source.revision, project.get("head")):
            raise WorkspaceError(
                "Project source revision changed.", code="source_revision_changed"
            )
        return source_admission_receipt(
            source,
            facts={
                "adapter": "project_source",
                "kind": expected_kind,
                "state": "available",
                "head": str(project.get("head") or ""),
            },
        )

    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot:
        expected_kind = self._KINDS.get(source.kind)
        if expected_kind is None:
            raise WorkspaceError(
                "Workspace source kind is unsupported.", code="source_not_found"
            )
        lease = await self._client.acquire(
            source.source_id, expected_head=source.revision
        )
        return await self.consume_lease(source, lease, expected_kind=expected_kind)

    async def consume_lease(
        self,
        source: WorkspaceSource,
        lease: dict[str, Any],
        *,
        expected_kind: str,
    ) -> SourceSnapshot:
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
                if metadata.st_size > MAX_EXTERNAL_SOURCE_FILE_BYTES:
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


class HostSnapshotWorkspaceSourceAdapter:
    """Lazily transfer one exact Windows Helper snapshot into the Worker.

    Queued tasks do not occupy the single Project Source lease. The physical
    path remains in the Helper; this adapter sees only the opaque project id,
    revision, transfer metadata, and the fixed Project Source snapshot mount.
    """

    def __init__(
        self,
        host: ProjectHostSnapshotClient,
        project_source: ProjectSnapshotClient,
        snapshot_root: Path,
    ) -> None:
        self._host = host
        self._project_source = project_source
        self._reader = ProjectSnapshotWorkspaceSourceAdapter(
            project_source, snapshot_root
        )

    async def admit(self, source: WorkspaceSource) -> SourceAdmissionReceipt:
        if source.kind != "host_snapshot":
            raise WorkspaceError(
                "Workspace source kind is unsupported.", code="source_not_found"
            )
        project = self._host.check_project(
            source.source_id, source.revision, None
        )
        if project.get("id") != source.source_id or project.get("kind") != "host_git":
            raise WorkspaceError(
                "Host source admission is inconsistent.",
                code="source_not_found",
            )
        if project.get("state") != "available":
            raise WorkspaceError(
                "Host source is temporarily unavailable.",
                code="source_temporarily_unavailable",
            )
        if not _public_revision_matches(source.revision, project.get("head")):
            raise WorkspaceError(
                "Host source revision changed.", code="source_revision_changed"
            )
        return source_admission_receipt(
            source,
            facts={
                "adapter": "project_host",
                "kind": "host_git",
                "state": "available",
                "head": str(project.get("head") or ""),
            },
        )

    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot:
        if source.kind != "host_snapshot":
            raise WorkspaceError(
                "Workspace source kind is unsupported.", code="source_not_found"
            )
        transfer_id: str | None = None
        try:
            transfer = await self._host.request_snapshot(
                source.source_id,
                expected_head=source.revision,
            )
            project = transfer.get("project")
            transfer_id = transfer.get("upload_id")
            archive_sha256 = transfer.get("archive_sha256")
            if (
                not isinstance(project, dict)
                or project.get("project_id") != source.source_id
                or project.get("head") != source.revision
                or not isinstance(project.get("name"), str)
                or not isinstance(project.get("branch"), str)
                or not isinstance(transfer_id, str)
                or re.fullmatch(r"[a-f0-9]{32}", transfer_id) is None
                or not isinstance(archive_sha256, str)
                or re.fullmatch(r"[a-f0-9]{64}", archive_sha256) is None
            ):
                raise WorkspaceError(
                    "Host snapshot response is inconsistent.",
                    code="source_revision_changed",
                )
            lease = await self._project_source.import_uploaded(
                upload_id=transfer_id,
                archive_sha256=archive_sha256,
                project_id=source.source_id,
                name=project["name"],
                branch=project["branch"],
                head=source.revision,
            )
            return await self._reader.consume_lease(
                source, lease, expected_kind="host_git"
            )
        except WorkspaceError:
            raise
        except Exception as exc:
            raise WorkspaceError(
                "Host snapshot is unavailable.",
                code=str(getattr(exc, "code", "source_unavailable")),
            ) from exc
        finally:
            if transfer_id is not None:
                self._host.finish_transfer(transfer_id)
