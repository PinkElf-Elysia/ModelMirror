from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import Field

from .contracts import StrictModel, WorkspaceSource


SAFE_WORKSPACE_ID = re.compile(r"^workspace_[a-f0-9]{32}$")
MAX_SOURCE_FILES = 20_000
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 192 * 1024 * 1024


class WorkspaceError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class SourceFile(StrictModel):
    path: str = Field(min_length=1, max_length=1024)
    content: bytes = Field(max_length=MAX_SOURCE_FILE_BYTES)
    executable: bool = False


class SourceSnapshot(StrictModel):
    source: WorkspaceSource
    files: tuple[SourceFile, ...] = Field(max_length=MAX_SOURCE_FILES)


class WorkspaceRecord(StrictModel):
    workspace_id: str
    slot_id: str = "default"
    source: WorkspaceSource
    baseline_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    baseline_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class WorkspaceEntry(StrictModel):
    entry_id: str
    name: str
    display_path: str
    kind: str
    size: int = Field(default=0, ge=0)
    sha256: str | None = None


class WorkspaceSourceAdapter(Protocol):
    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot: ...


class InMemoryWorkspaceSourceAdapter:
    def __init__(self, snapshots: Mapping[tuple[str, str], Mapping[str, bytes]]) -> None:
        self._snapshots = snapshots

    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot:
        files = self._snapshots.get((source.source_id, source.revision))
        if files is None:
            raise WorkspaceError("Workspace source was not found.", code="source_not_found")
        return SourceSnapshot(
            source=source,
            files=tuple(SourceFile(path=path, content=content) for path, content in files.items()),
        )


class WorkspaceBroker:
    """Materializes opaque sources into isolated, remote-free synthetic Git workspaces."""

    def __init__(
        self,
        root: Path,
        adapters: Mapping[str, WorkspaceSourceAdapter],
        *,
        id_key: bytes,
        slot_roots: Mapping[str, Path] | None = None,
        slot_owner: tuple[int, int] | None = None,
    ) -> None:
        if len(id_key) < 32:
            raise ValueError("workspace id key is too short")
        self.root = Path(root).resolve()
        self.dedicated_slots = slot_roots is not None
        configured = slot_roots or {"default": self.root}
        if not configured or any(
            re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", key) is None
            for key in configured
        ):
            raise ValueError("workspace slot id is invalid")
        self._slot_roots = {
            key: Path(value).resolve() for key, value in configured.items()
        }
        self._slot_owner = slot_owner
        first_root = next(iter(self._slot_roots.values()))
        self.workspaces_root = first_root / "workspaces"
        self.staging_root = first_root / "workspace-staging"
        self._adapters = dict(adapters)
        self._id_key = bytes(id_key)
        for slot_root in self._slot_roots.values():
            (slot_root / "workspaces").mkdir(parents=True, exist_ok=True)
            (slot_root / "workspace-staging").mkdir(parents=True, exist_ok=True)

    @property
    def slot_ids(self) -> tuple[str, ...]:
        return tuple(self._slot_roots)

    async def prepare(
        self, source: WorkspaceSource, *, slot_id: str | None = None
    ) -> WorkspaceRecord:
        adapter = self._adapters.get(source.kind)
        if adapter is None:
            raise WorkspaceError("Workspace source kind is unavailable.", code="source_unavailable")
        snapshot = await adapter.acquire(source)
        if snapshot.source != source:
            raise WorkspaceError("Workspace source binding changed.", code="source_changed")
        normalized = self._validate_snapshot(snapshot.files)
        selected_slot = slot_id or (None if self.dedicated_slots else "default")
        slot_root = self._slot_roots.get(selected_slot or "")
        if slot_root is None:
            raise WorkspaceError(
                "Workspace slot is unavailable.", code="workspace_slot_unavailable"
            )
        workspace_id = f"workspace_{uuid.uuid4().hex}"
        stage = slot_root / "workspace-staging" / workspace_id
        destination = slot_root / "workspaces" / workspace_id
        if stage.exists() or destination.exists():
            raise WorkspaceError("Workspace id collision.", code="workspace_unavailable")
        repository = stage / "repo"
        try:
            repository.mkdir(parents=True)
            for path, file in normalized:
                target = repository.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._write_exclusive(target, file.content)
                if file.executable and os.name != "nt":
                    target.chmod(0o700)
            baseline_commit = self._initialize_git(repository)
            baseline_tree_hash = self._tree_hash(repository)
            total_bytes = sum(len(file.content) for _, file in normalized)
            metadata = WorkspaceRecord(
                workspace_id=workspace_id,
                slot_id=selected_slot or "",
                source=source,
                baseline_commit=baseline_commit,
                baseline_tree_hash=baseline_tree_hash,
                file_count=len(normalized),
                total_bytes=total_bytes,
            )
            self._write_exclusive(
                stage / "metadata.json",
                json.dumps(
                    metadata.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            os.replace(stage, destination)
            self._apply_slot_owner(destination)
            return self.get(workspace_id)
        except WorkspaceError:
            self._remove_tree(stage)
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            self._remove_tree(stage)
            raise WorkspaceError(
                "Workspace could not be prepared.", code="workspace_unavailable"
            ) from exc

    def get(self, workspace_id: str) -> WorkspaceRecord:
        slot_id, root = self._workspace_location(workspace_id)
        try:
            value = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
            record = WorkspaceRecord.model_validate(value)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise WorkspaceError("Workspace metadata is invalid.", code="workspace_corrupt") from exc
        if (
            record.workspace_id != workspace_id
            or record.slot_id != slot_id
            or not (root / "repo").is_dir()
        ):
            raise WorkspaceError("Workspace metadata is invalid.", code="workspace_corrupt")
        return record

    def workspace_slot(self, workspace_id: str) -> str:
        return self._workspace_location(workspace_id)[0]

    def repository_path(self, workspace_id: str) -> Path:
        root = self._workspace_root(workspace_id)
        repository = root / "repo"
        if not repository.is_dir() or repository.is_symlink():
            raise WorkspaceError("Workspace repository is invalid.", code="workspace_corrupt")
        return repository

    def tree(self, workspace_id: str) -> tuple[WorkspaceEntry, ...]:
        repository = self.repository_path(workspace_id)
        entries: list[WorkspaceEntry] = []
        for current, directories, files in os.walk(repository, topdown=True, followlinks=False):
            current_path = Path(current)
            if current_path == repository:
                directories[:] = [name for name in directories if name != ".git"]
            directories.sort()
            for name in list(directories):
                candidate = current_path / name
                if candidate.is_symlink():
                    raise WorkspaceError("Workspace contains a link.", code="workspace_changed")
                relative = candidate.relative_to(repository).as_posix()
                entries.append(
                    WorkspaceEntry(
                        entry_id=self._entry_id(workspace_id, relative),
                        name=name,
                        display_path=relative,
                        kind="directory",
                    )
                )
            for name in files:
                candidate = current_path / name
                if candidate.is_symlink():
                    raise WorkspaceError("Workspace contains a link.", code="workspace_changed")
                relative = candidate.relative_to(repository).as_posix()
                content = candidate.read_bytes()
                entries.append(
                    WorkspaceEntry(
                        entry_id=self._entry_id(workspace_id, relative),
                        name=name,
                        display_path=relative,
                        kind="file",
                        size=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                    )
                )
        return tuple(sorted(entries, key=lambda entry: (entry.display_path, entry.kind)))

    def read_entry(self, workspace_id: str, entry_id: str, *, max_bytes: int = 1024 * 1024) -> bytes:
        for entry in self.tree(workspace_id):
            if entry.entry_id == entry_id:
                if entry.kind != "file" or entry.size > max_bytes:
                    raise WorkspaceError("Workspace entry is not previewable.", code="preview_unavailable")
                target = self.repository_path(workspace_id).joinpath(
                    *PurePosixPath(entry.display_path).parts
                )
                return target.read_bytes()
        raise WorkspaceError("Workspace entry was not found.", code="entry_not_found")

    def current_tree_hash(self, workspace_id: str) -> str:
        return self._tree_hash(self.repository_path(workspace_id))

    def diff(self, workspace_id: str, *, max_bytes: int = 2 * 1024 * 1024) -> bytes:
        record = self.get(workspace_id)
        repository = self.repository_path(workspace_id)
        temporary_index = repository.parent / f"index-{uuid.uuid4().hex}"
        try:
            shutil.copy2(repository / ".git" / "index", temporary_index)
            env = self._git_env(repository)
            env["GIT_INDEX_FILE"] = str(temporary_index)
            self._git(repository, "add", "-A", env=env)
            result = self._git(
                repository,
                "diff",
                "--cached",
                "--binary",
                "--no-ext-diff",
                record.baseline_commit,
                "--",
                env=env,
                text=False,
            )
            if len(result) > max_bytes:
                raise WorkspaceError("Workspace diff is too large.", code="diff_too_large")
            return result
        finally:
            try:
                temporary_index.unlink(missing_ok=True)
            except OSError:
                pass

    def delete(self, workspace_id: str) -> None:
        self._remove_tree(self._workspace_root(workspace_id))

    def _workspace_root(self, workspace_id: str) -> Path:
        return self._workspace_location(workspace_id)[1]

    def _workspace_location(self, workspace_id: str) -> tuple[str, Path]:
        if SAFE_WORKSPACE_ID.fullmatch(workspace_id) is None:
            raise WorkspaceError("Workspace id is invalid.", code="workspace_not_found")
        matches = [
            (slot_id, slot_root / "workspaces" / workspace_id)
            for slot_id, slot_root in self._slot_roots.items()
            if (slot_root / "workspaces" / workspace_id).is_dir()
            and not (slot_root / "workspaces" / workspace_id).is_symlink()
        ]
        if len(matches) != 1:
            raise WorkspaceError("Workspace was not found.", code="workspace_not_found")
        return matches[0]

    @staticmethod
    def _validate_snapshot(files: Sequence[SourceFile]) -> list[tuple[PurePosixPath, SourceFile]]:
        if len(files) > MAX_SOURCE_FILES:
            raise WorkspaceError("Workspace source has too many files.", code="source_too_large")
        seen: set[str] = set()
        normalized: list[tuple[PurePosixPath, SourceFile]] = []
        total = 0
        for file in files:
            if "\\" in file.path or "\x00" in file.path:
                raise WorkspaceError("Workspace path is invalid.", code="source_path_invalid")
            path = PurePosixPath(file.path)
            if (
                path.is_absolute()
                or not path.parts
                or any(part in {"", ".", "..", ".git"} for part in path.parts)
            ):
                raise WorkspaceError("Workspace path is invalid.", code="source_path_invalid")
            rendered = path.as_posix()
            collision = rendered.casefold()
            if collision in seen:
                raise WorkspaceError("Workspace paths collide.", code="source_path_conflict")
            seen.add(collision)
            total += len(file.content)
            if total > MAX_SOURCE_BYTES:
                raise WorkspaceError("Workspace source is too large.", code="source_too_large")
            normalized.append((path, file))
        return sorted(normalized, key=lambda item: item[0].as_posix())

    @staticmethod
    def _write_exclusive(path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    def _initialize_git(self, repository: Path) -> str:
        env = self._git_env(repository)
        self._git(repository, "init", "--quiet", "--initial-branch=workspace", env=env)
        self._git(repository, "config", "user.name", "ModelMirror Coding Worker", env=env)
        self._git(repository, "config", "user.email", "coding-worker@modelmirror.local", env=env)
        self._git(repository, "add", "-A", env=env)
        self._git(
            repository,
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "ModelMirror synthetic baseline H0",
            env=env,
        )
        if self._git(repository, "remote", env=env).strip():
            raise WorkspaceError("Synthetic workspace has a remote.", code="workspace_unsafe")
        head = self._git(repository, "rev-parse", "HEAD", env=env).strip()
        if re.fullmatch(r"[a-f0-9]{40}", head) is None:
            raise WorkspaceError("Synthetic baseline is invalid.", code="workspace_unavailable")
        return head

    @staticmethod
    def _git_env(repository: Path) -> dict[str, str]:
        allowed = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ}
        home = repository.parent / "git-home"
        home.mkdir(exist_ok=True)
        return {
            **allowed,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "GIT_SSH_COMMAND": "false",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "LC_ALL": "C.UTF-8",
        }

    @staticmethod
    def _git(
        repository: Path,
        *args: str,
        env: dict[str, str],
        text: bool = True,
    ) -> str | bytes:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise WorkspaceError("Git workspace operation failed.", code="workspace_unavailable")
        return result.stdout

    @staticmethod
    def _tree_hash(repository: Path) -> str:
        digest = hashlib.sha256()
        for current, directories, files in os.walk(repository, topdown=True, followlinks=False):
            current_path = Path(current)
            if current_path == repository:
                directories[:] = [name for name in directories if name != ".git"]
            directories.sort()
            for name in directories:
                if (current_path / name).is_symlink():
                    raise WorkspaceError("Workspace contains a link.", code="workspace_changed")
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink():
                    raise WorkspaceError("Workspace contains a link.", code="workspace_changed")
                relative = path.relative_to(repository).as_posix().encode("utf-8")
                content = path.read_bytes()
                digest.update(len(relative).to_bytes(4, "big"))
                digest.update(relative)
                digest.update(len(content).to_bytes(8, "big"))
                digest.update(hashlib.sha256(content).digest())
        return digest.hexdigest()

    def _entry_id(self, workspace_id: str, relative_path: str) -> str:
        value = hmac.new(
            self._id_key,
            f"{workspace_id}\0{relative_path}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"entry_{value[:32]}"

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if path.exists():
            root = path.resolve()

            def remove_owned_readonly(
                function: object, name: str, error: tuple[type[BaseException], BaseException, object]
            ) -> None:
                failure = error[1]
                target = Path(name)
                try:
                    metadata = os.lstat(target)
                    if stat.S_ISLNK(metadata.st_mode) or not target.resolve().is_relative_to(root):
                        raise failure
                    os.chmod(target, metadata.st_mode | stat.S_IWRITE)
                    function(name)  # type: ignore[operator]
                except Exception:
                    raise failure

            shutil.rmtree(path, onerror=remove_owned_readonly)

    def _apply_slot_owner(self, root: Path) -> None:
        if self._slot_owner is None or os.name == "nt":
            return
        uid, gid = self._slot_owner
        for candidate in (root, *root.rglob("*")):
            if candidate.is_symlink():
                raise WorkspaceError(
                    "Workspace contains a link.", code="workspace_link_detected"
                )
            os.chown(candidate, uid, gid)
            candidate.chmod(0o770 if candidate.is_dir() else 0o660)
