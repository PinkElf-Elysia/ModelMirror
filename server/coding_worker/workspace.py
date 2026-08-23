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
import time
import uuid
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import Field

from .contracts import RepositoryInstruction, StrictModel, WorkspaceSource


SAFE_WORKSPACE_ID = re.compile(r"^workspace_[a-f0-9]{32}$")
MAX_SOURCE_FILES = 20_000
# SourceFile is the internal hard ceiling. Untrusted Project Source and Host
# snapshots keep their narrower adapter-specific limit below.
MAX_SOURCE_FILE_BYTES = 16 * 1024 * 1024
MAX_EXTERNAL_SOURCE_FILE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 192 * 1024 * 1024
MAX_REPOSITORY_INSTRUCTIONS = 16
MAX_REPOSITORY_INSTRUCTION_DEPTH = 8
MAX_REPOSITORY_INSTRUCTION_BYTES = 16 * 1024
MAX_REPOSITORY_INSTRUCTIONS_BYTES = 64 * 1024


class WorkspaceError(RuntimeError):
    status = 400

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = (
            404 if code in {"workspace_not_found", "entry_not_found"} else 400
        )


SOURCE_ADMISSION_REASONS = frozenset(
    {
        "not_registered",
        "revision_changed",
        "temporarily_unavailable",
        "unsafe",
        "limit_exceeded",
    }
)


class WorkspaceSourceUnavailableError(WorkspaceError):
    """Public, path-free source admission failure."""

    def __init__(self, reason: str) -> None:
        if reason not in SOURCE_ADMISSION_REASONS:
            raise ValueError("workspace source admission reason is invalid")
        super().__init__(
            "Workspace source is unavailable.",
            code="workspace_source_unavailable",
        )
        self.status = 409
        self.reason = reason


class SourceAdmissionReceipt(StrictModel):
    protocol: Literal["modelmirror-workspace-source-admission/v1"] = (
        "modelmirror-workspace-source-admission/v1"
    )
    source: WorkspaceSource
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observed_at: float = Field(ge=0)
    facts: dict[str, str | int | bool | None] = Field(default_factory=dict, max_length=32)


def source_admission_receipt(
    source: WorkspaceSource,
    *,
    facts: Mapping[str, str | int | bool | None],
    observed_at: float | None = None,
) -> SourceAdmissionReceipt:
    normalized = dict(sorted(facts.items()))
    encoded = json.dumps(
        {
            "protocol": "modelmirror-workspace-source-admission/v1",
            "source": source.model_dump(mode="json"),
            "facts": normalized,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return SourceAdmissionReceipt(
        source=source,
        binding_sha256=hashlib.sha256(encoded).hexdigest(),
        observed_at=time.time() if observed_at is None else observed_at,
        facts=normalized,
    )


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


class WorkspaceSnapshot(StrictModel):
    tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    tree_oid: str = Field(pattern=r"^[a-f0-9]{40}$")


class WorkspaceEntry(StrictModel):
    entry_id: str
    name: str
    display_path: str
    kind: str
    size: int = Field(default=0, ge=0)
    sha256: str | None = None


class WorkspaceSourceAdapter(Protocol):
    async def admit(self, source: WorkspaceSource) -> SourceAdmissionReceipt: ...

    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot: ...


class InMemoryWorkspaceSourceAdapter:
    def __init__(self, snapshots: Mapping[tuple[str, str], Mapping[str, bytes]]) -> None:
        self._snapshots = snapshots

    async def admit(self, source: WorkspaceSource) -> SourceAdmissionReceipt:
        files = self._snapshots.get((source.source_id, source.revision))
        if files is None:
            code = (
                "source_revision_changed"
                if any(source_id == source.source_id for source_id, _revision in self._snapshots)
                else "source_not_found"
            )
            raise WorkspaceError("Workspace source was not found.", code=code)
        total_bytes = sum(len(content) for content in files.values())
        if (
            len(files) > MAX_SOURCE_FILES
            or total_bytes > MAX_SOURCE_BYTES
            or any(len(content) > MAX_SOURCE_FILE_BYTES for content in files.values())
        ):
            raise WorkspaceError(
                "Workspace source exceeds Worker limits.",
                code="source_limit_exceeded",
            )
        return source_admission_receipt(
            source,
            facts={
                "adapter": "memory",
                "file_count": len(files),
                "total_bytes": total_bytes,
                "limit_policy": "trusted-16m-v1",
            },
        )

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

    async def admit(self, source: WorkspaceSource) -> SourceAdmissionReceipt:
        adapter = self._adapters.get(source.kind)
        if adapter is None:
            raise WorkspaceSourceUnavailableError("not_registered")
        try:
            admit = getattr(adapter, "admit", None)
            if not callable(admit):
                raise WorkspaceSourceUnavailableError("not_registered")
            receipt = await admit(source)
        except WorkspaceSourceUnavailableError:
            raise
        except Exception as exc:
            raise WorkspaceSourceUnavailableError(
                self._safe_admission_reason(getattr(exc, "code", None))
            ) from exc
        if receipt.source != source:
            raise WorkspaceSourceUnavailableError("revision_changed")
        return receipt

    async def prepare(
        self, source: WorkspaceSource, *, slot_id: str | None = None
    ) -> WorkspaceRecord:
        adapter = self._adapters.get(source.kind)
        if adapter is None:
            raise WorkspaceError("Workspace source kind is unavailable.", code="source_unavailable")
        snapshot = await adapter.acquire(source)
        if snapshot.source != source:
            raise WorkspaceError("Workspace source binding changed.", code="source_changed")
        return self._materialize(source, snapshot.files, slot_id=slot_id)

    @staticmethod
    def _safe_admission_reason(code: object) -> str:
        normalized = str(code or "").lower()
        if "limit" in normalized or "too_large" in normalized:
            return "limit_exceeded"
        if any(
            marker in normalized
            for marker in ("unsafe", "symlink", "reparse", "hardlink", "config")
        ):
            return "unsafe"
        if any(
            marker in normalized
            for marker in ("revision", "changed", "mismatch", "head_not_current")
        ):
            return "revision_changed"
        if any(
            marker in normalized
            for marker in ("not_found", "not_registered", "unknown_project")
        ):
            return "not_registered"
        return "temporarily_unavailable"

    def fork(
        self,
        workspace_id: str,
        *,
        expected_tree_hash: str,
        slot_id: str | None = None,
    ) -> WorkspaceRecord:
        """Create an isolated synthetic H0 from an exact turn-bound Workspace tree."""
        record = self.get(workspace_id)
        snapshot = self.capture_snapshot(workspace_id)
        if snapshot.tree_hash != expected_tree_hash:
            raise WorkspaceError(
                "Workspace changed before fork capture.", code="workspace_changed"
            )
        files = self.snapshot_files(workspace_id, snapshot)
        return self._materialize(
            record.source, files, slot_id=slot_id or record.slot_id
        )

    def _materialize(
        self,
        source: WorkspaceSource,
        files: Sequence[SourceFile],
        *,
        slot_id: str | None,
    ) -> WorkspaceRecord:
        normalized = self._validate_snapshot(files)
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

    def apply_slot_owner(self, workspace_id: str, path: Path) -> None:
        if self._slot_owner is None or os.name == "nt":
            return
        workspace_root = self._workspace_root(workspace_id)
        candidate = Path(path)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(workspace_root) or candidate.is_symlink():
            raise WorkspaceError(
                "Workspace ownership target is invalid.", code="workspace_changed"
            )
        uid, gid = self._slot_owner
        os.chown(candidate, uid, gid)
        candidate.chmod(0o770 if candidate.is_dir() else 0o660)

    def repository_path(self, workspace_id: str) -> Path:
        root = self._workspace_root(workspace_id)
        repository = root / "repo"
        if not repository.is_dir() or repository.is_symlink():
            raise WorkspaceError("Workspace repository is invalid.", code="workspace_corrupt")
        return repository

    def repository_instructions(
        self, workspace_id: str
    ) -> tuple[RepositoryInstruction, ...]:
        """Read bounded AGENTS.md rules from immutable synthetic H0 objects."""
        record = self.get(workspace_id)
        repository = self.repository_path(workspace_id)
        env = self._git_env(repository)
        raw_tree = self._git(
            repository,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            record.baseline_commit,
            env=env,
            text=False,
        )
        if not isinstance(raw_tree, bytes):
            raise WorkspaceError(
                "Repository instructions are unavailable.",
                code="repository_instructions_unsafe",
            )
        candidates: list[tuple[PurePosixPath, str]] = []
        for encoded_entry in raw_tree.split(b"\0"):
            if not encoded_entry:
                continue
            try:
                metadata, encoded_path = encoded_entry.split(b"\t", 1)
                mode, object_type, _object_id = metadata.decode("ascii").split(" ")
                rendered = encoded_path.decode("utf-8", errors="strict")
                path = PurePosixPath(rendered)
            except (UnicodeDecodeError, ValueError) as exc:
                raise WorkspaceError(
                    "Repository instruction metadata is invalid.",
                    code="repository_instructions_unsafe",
                ) from exc
            if path.name != "AGENTS.md":
                continue
            if (
                object_type != "blob"
                or mode not in {"100644", "100755"}
                or len(path.parts) - 1 > MAX_REPOSITORY_INSTRUCTION_DEPTH
            ):
                raise WorkspaceError(
                    "Repository instruction layout is unsafe.",
                    code="repository_instructions_unsafe",
                )
            candidates.append((path, rendered))
        if len(candidates) > MAX_REPOSITORY_INSTRUCTIONS:
            raise WorkspaceError(
                "Repository instruction count is too large.",
                code="repository_instructions_unsafe",
            )
        instructions: list[RepositoryInstruction] = []
        total_bytes = 0
        for path, rendered in sorted(
            candidates, key=lambda item: (len(item[0].parts), item[1])
        ):
            raw_content = self._git(
                repository,
                "cat-file",
                "blob",
                f"{record.baseline_commit}:{rendered}",
                env=env,
                text=False,
            )
            if not isinstance(raw_content, bytes):
                raise WorkspaceError(
                    "Repository instruction content is unavailable.",
                    code="repository_instructions_unsafe",
                )
            total_bytes += len(raw_content)
            if (
                len(raw_content) > MAX_REPOSITORY_INSTRUCTION_BYTES
                or total_bytes > MAX_REPOSITORY_INSTRUCTIONS_BYTES
                or b"\0" in raw_content
            ):
                raise WorkspaceError(
                    "Repository instruction content is unsafe.",
                    code="repository_instructions_unsafe",
                )
            try:
                content = raw_content.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise WorkspaceError(
                    "Repository instruction content is not text.",
                    code="repository_instructions_unsafe",
                ) from exc
            parent = path.parent.as_posix()
            instructions.append(
                RepositoryInstruction(
                    display_path=rendered,
                    scope=parent if parent else ".",
                    sha256=hashlib.sha256(raw_content).hexdigest(),
                    content=content,
                )
            )
        return tuple(instructions)

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
        entry, target = self.resolve_entry(workspace_id, entry_id, require_file=True)
        if entry.size is None or entry.size > max_bytes:
            raise WorkspaceError(
                "Workspace entry is not previewable.", code="preview_unavailable"
            )
        return target.read_bytes()

    def get_entry(self, workspace_id: str, entry_id: str) -> WorkspaceEntry:
        for entry in self.tree(workspace_id):
            if entry.entry_id == entry_id:
                return entry
        raise WorkspaceError("Workspace entry was not found.", code="entry_not_found")

    def resolve_entry(
        self,
        workspace_id: str,
        entry_id: str,
        *,
        require_file: bool = False,
    ) -> tuple[WorkspaceEntry, Path]:
        entry = self.get_entry(workspace_id, entry_id)
        if require_file and entry.kind != "file":
            raise WorkspaceError(
                "Workspace entry is not a file.", code="entry_not_found"
            )
        repository = self.repository_path(workspace_id)
        target = repository.joinpath(*PurePosixPath(entry.display_path).parts)
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise WorkspaceError(
                "Workspace entry changed.", code="workspace_changed"
            ) from exc
        if target.is_symlink() or not resolved.is_relative_to(repository):
            raise WorkspaceError(
                "Workspace entry changed.", code="workspace_changed"
            )
        if require_file and not target.is_file():
            raise WorkspaceError(
                "Workspace entry changed.", code="workspace_changed"
            )
        return entry, target

    def current_tree_hash(self, workspace_id: str) -> str:
        return self._tree_hash(self.repository_path(workspace_id))

    def capture_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        """Capture exact working bytes without filters, moving HEAD, or changing index."""
        repository = self.repository_path(workspace_id)
        before = self._tree_hash(repository)
        tree_oid = self._snapshot_tree_oid(repository)
        after = self._tree_hash(repository)
        if after != before:
            raise WorkspaceError(
                "Workspace changed during snapshot capture.", code="workspace_changed"
            )
        return WorkspaceSnapshot(tree_hash=after, tree_oid=tree_oid)

    def snapshot_files(
        self, workspace_id: str, snapshot: WorkspaceSnapshot
    ) -> tuple[SourceFile, ...]:
        """Materialize a bound Git tree as bounded regular-file content."""
        repository = self.repository_path(workspace_id)
        env = self._git_env(repository)
        object_type = self._git(
            repository, "cat-file", "-t", snapshot.tree_oid, env=env
        ).strip()
        if object_type != "tree":
            raise WorkspaceError(
                "Workspace snapshot object is invalid.", code="workspace_changed"
            )
        raw = self._git(
            repository,
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            snapshot.tree_oid,
            env=env,
            text=False,
        )
        if not isinstance(raw, bytes):
            raise WorkspaceError(
                "Workspace snapshot is unavailable.", code="workspace_changed"
            )
        files: list[SourceFile] = []
        for encoded_entry in raw.split(b"\0"):
            if not encoded_entry:
                continue
            try:
                metadata, encoded_path = encoded_entry.split(b"\t", 1)
                mode, object_type, object_id = metadata.decode("ascii").split(" ")
                path = encoded_path.decode("utf-8", errors="strict")
            except (UnicodeDecodeError, ValueError) as exc:
                raise WorkspaceError(
                    "Workspace snapshot metadata is invalid.",
                    code="workspace_changed",
                ) from exc
            if object_type != "blob" or mode not in {"100644", "100755"}:
                raise WorkspaceError(
                    "Workspace snapshot contains an unsupported entry.",
                    code="workspace_changed",
                )
            content = self._git(
                repository, "cat-file", "blob", object_id, env=env, text=False
            )
            if not isinstance(content, bytes):
                raise WorkspaceError(
                    "Workspace snapshot content is unavailable.",
                    code="workspace_changed",
                )
            files.append(
                SourceFile(path=path, content=content, executable=mode == "100755")
            )
        normalized = self._validate_snapshot(files)
        materialized = tuple(item for _, item in normalized)
        digest = hashlib.sha256()
        for path, item in normalized:
            rendered = path.as_posix().encode("utf-8")
            digest.update(len(rendered).to_bytes(4, "big"))
            digest.update(rendered)
            digest.update(len(item.content).to_bytes(8, "big"))
            digest.update(hashlib.sha256(item.content).digest())
        if digest.hexdigest() != snapshot.tree_hash:
            raise WorkspaceError(
                "Workspace snapshot hash changed.", code="workspace_changed"
            )
        return materialized

    def diff(
        self,
        workspace_id: str,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        detect_renames: bool = True,
    ) -> bytes:
        record = self.get(workspace_id)
        repository = self.repository_path(workspace_id)
        temporary_index = repository.parent / f"index-{uuid.uuid4().hex}"
        try:
            shutil.copy2(repository / ".git" / "index", temporary_index)
            env = self._git_env(repository)
            env["GIT_INDEX_FILE"] = str(temporary_index)
            self._git(repository, "add", "-A", env=env)
            diff_args = ["diff", "--cached", "--binary", "--no-ext-diff"]
            if not detect_renames:
                diff_args.append("--no-renames")
            diff_args.extend((record.baseline_commit, "--"))
            result = self._git(
                repository,
                *diff_args,
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

    def changed_paths(self, workspace_id: str) -> tuple[str, ...]:
        """Return bounded normalized paths changed from this fork's synthetic H0."""
        record = self.get(workspace_id)
        repository = self.repository_path(workspace_id)
        temporary_index = repository.parent / f"index-{uuid.uuid4().hex}"
        try:
            shutil.copy2(repository / ".git" / "index", temporary_index)
            env = self._git_env(repository)
            env["GIT_INDEX_FILE"] = str(temporary_index)
            self._git(repository, "add", "-A", env=env)
            raw = self._git(
                repository,
                "diff",
                "--cached",
                "--name-only",
                "-z",
                record.baseline_commit,
                "--",
                env=env,
                text=False,
            )
            if not isinstance(raw, bytes) or len(raw) > 1024 * 1024:
                raise WorkspaceError(
                    "Workspace changed-path output is unavailable.",
                    code="workspace_changed",
                )
            paths = tuple(
                item.decode("utf-8", errors="strict")
                for item in raw.split(b"\0")
                if item
            )
            if len(paths) > 4096 or paths != tuple(sorted(set(paths))):
                raise WorkspaceError(
                    "Workspace changed-path output is invalid.",
                    code="workspace_changed",
                )
            for path in paths:
                self._normalize_path(path)
            return paths
        except UnicodeDecodeError as exc:
            raise WorkspaceError(
                "Workspace changed-path output is invalid.",
                code="workspace_changed",
            ) from exc
        finally:
            try:
                temporary_index.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _normalize_path(value: str) -> PurePosixPath:
        if "\\" in value or "\x00" in value:
            raise WorkspaceError(
                "Workspace changed path is invalid.", code="workspace_changed"
            )
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", "..", ".git"} for part in path.parts)
            or path.as_posix() != value
        ):
            raise WorkspaceError(
                "Workspace changed path is invalid.", code="workspace_changed"
            )
        return path

    def fork_merge_changes(
        self,
        workspace_id: str,
        *,
        expected_base_tree_hash: str,
        expected_result_tree_hash: str,
        expected_changed_paths: tuple[str, ...],
    ) -> tuple[dict[str, object], ...]:
        """Build bounded text changes bound to a fork's immutable H0 and result."""
        record = self.get(workspace_id)
        if record.baseline_tree_hash != expected_base_tree_hash:
            raise WorkspaceError(
                "Subtask fork baseline changed.", code="subtask_result_changed"
            )
        current = self.capture_snapshot(workspace_id)
        if current.tree_hash != expected_result_tree_hash:
            raise WorkspaceError(
                "Subtask fork result changed.", code="subtask_result_changed"
            )
        repository = self.repository_path(workspace_id)
        tree_oid = self._git(
            repository,
            "rev-parse",
            f"{record.baseline_commit}^{{tree}}",
            env=self._git_env(repository),
        ).strip()
        if re.fullmatch(r"[a-f0-9]{40}", tree_oid) is None:
            raise WorkspaceError(
                "Subtask fork baseline is unavailable.", code="subtask_result_changed"
            )
        baseline_files = {
            item.path: item
            for item in self.snapshot_files(
                workspace_id,
                WorkspaceSnapshot(
                    tree_hash=record.baseline_tree_hash, tree_oid=tree_oid
                ),
            )
        }
        result_files = {
            item.path: item for item in self.snapshot_files(workspace_id, current)
        }
        changes: list[dict[str, object]] = []
        changed_paths: list[str] = []
        for path in sorted(set(baseline_files) | set(result_files)):
            before = baseline_files.get(path)
            after = result_files.get(path)
            if before == after:
                continue
            changed_paths.append(path)
            if before is not None and after is not None:
                if before.executable != after.executable:
                    raise WorkspaceError(
                        "Subtask changed a file mode that cannot be merged safely.",
                        code="subtask_mode_change_unsupported",
                    )
                content = self._merge_text(after.content)
                changes.append(
                    {
                        "kind": "write",
                        "path": path,
                        "expected_sha256": hashlib.sha256(before.content).hexdigest(),
                        "content": content,
                        "content_sha256": hashlib.sha256(after.content).hexdigest(),
                    }
                )
            elif before is not None:
                changes.append(
                    {
                        "kind": "delete",
                        "path": path,
                        "expected_sha256": hashlib.sha256(before.content).hexdigest(),
                    }
                )
            else:
                assert after is not None
                if after.executable:
                    raise WorkspaceError(
                        "Subtask added an executable file that cannot be merged safely.",
                        code="subtask_mode_change_unsupported",
                    )
                content = self._merge_text(after.content)
                changes.append(
                    {
                        "kind": "write",
                        "path": path,
                        "expected_absent": True,
                        "content": content,
                        "content_sha256": hashlib.sha256(after.content).hexdigest(),
                    }
                )
        if tuple(changed_paths) != expected_changed_paths:
            raise WorkspaceError(
                "Subtask changed-path receipt changed.", code="subtask_result_changed"
            )
        return tuple(changes)

    @staticmethod
    def _merge_text(content: bytes) -> str:
        if b"\0" in content:
            raise WorkspaceError(
                "Binary subtask changes require a separate artifact.",
                code="subtask_binary_change_unsupported",
            )
        try:
            return content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(
                "Non-UTF-8 subtask changes require a separate artifact.",
                code="subtask_binary_change_unsupported",
            ) from exc

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

    def _snapshot_tree_oid(self, repository: Path) -> str:
        root: dict[str, object] = {}
        for current, directories, files in os.walk(
            repository, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            if current_path == repository:
                directories[:] = [name for name in directories if name != ".git"]
            directories.sort()
            for name in directories:
                if (current_path / name).is_symlink():
                    raise WorkspaceError(
                        "Workspace contains a link.", code="workspace_changed"
                    )
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink() or not path.is_file():
                    raise WorkspaceError(
                        "Workspace contains an unsupported entry.",
                        code="workspace_changed",
                    )
                relative = path.relative_to(repository)
                node = root
                for part in relative.parts[:-1]:
                    child = node.setdefault(part, {})
                    if not isinstance(child, dict):
                        raise WorkspaceError(
                            "Workspace paths conflict.", code="workspace_changed"
                        )
                    node = child
                content = path.read_bytes()
                mode = 0o100755 if path.stat().st_mode & stat.S_IXUSR else 0o100644
                node[relative.name] = (
                    mode,
                    self._write_git_object(repository, "blob", content),
                )

        def write_tree(node: dict[str, object]) -> str:
            rendered: list[tuple[bytes, bytes]] = []
            for name, value in node.items():
                encoded = name.encode("utf-8")
                if isinstance(value, dict):
                    oid = write_tree(value)
                    rendered.append(
                        (encoded + b"/", b"40000 " + encoded + b"\0" + bytes.fromhex(oid))
                    )
                else:
                    mode, oid = value
                    rendered.append(
                        (
                            encoded,
                            f"{mode:o} ".encode("ascii")
                            + encoded
                            + b"\0"
                            + bytes.fromhex(oid),
                        )
                    )
            content = b"".join(item for _, item in sorted(rendered, key=lambda item: item[0]))
            return self._write_git_object(repository, "tree", content)

        return write_tree(root)

    @staticmethod
    def _write_git_object(repository: Path, object_type: str, content: bytes) -> str:
        raw = f"{object_type} {len(content)}\0".encode("ascii") + content
        oid = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        directory = repository / ".git" / "objects" / oid[:2]
        directory.mkdir(exist_ok=True)
        target = directory / oid[2:]
        if target.exists():
            try:
                if zlib.decompress(target.read_bytes()) != raw:
                    raise WorkspaceError(
                        "Workspace object store is corrupt.", code="workspace_corrupt"
                    )
            except (OSError, zlib.error) as exc:
                raise WorkspaceError(
                    "Workspace object store is corrupt.", code="workspace_corrupt"
                ) from exc
            return oid
        temporary = directory / f"snapshot-{uuid.uuid4().hex}"
        try:
            WorkspaceBroker._write_exclusive(temporary, zlib.compress(raw))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return oid

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
            ["git", "-c", f"safe.directory={repository}", *args],
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
