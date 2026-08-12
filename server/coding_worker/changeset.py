from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_validator

from .contracts import (
    ChangeKind,
    ChangesetEntry,
    ChangesetState,
    SAFE_ID,
    StrictModel,
    WorkerChangeset,
)
from .unified_patch import UnifiedPatchError, apply_unified_patch
from .workspace import SourceFile, WorkspaceBroker, WorkspaceSnapshot


MAX_CHANGESET_ENTRIES = 128
MAX_CHANGESET_BYTES = 32 * 1024 * 1024
_DIGEST = r"^[a-f0-9]{64}$"
class ChangesetError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class _WriteChange(StrictModel):
    kind: Literal["write"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str | None = Field(default=None, pattern=_DIGEST)
    expected_absent: bool = False
    content: str = Field(max_length=MAX_CHANGESET_BYTES)
    content_sha256: str = Field(pattern=_DIGEST)

    @model_validator(mode="after")
    def exact_preimage(self) -> "_WriteChange":
        if self.expected_absent == (self.expected_sha256 is not None):
            raise ValueError(
                "write requires either expected_sha256 or expected_absent"
            )
        return self


class _DeleteChange(StrictModel):
    kind: Literal["delete"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=_DIGEST)


class _MoveChange(StrictModel):
    kind: Literal["move"]
    path: str = Field(min_length=1, max_length=1024)
    destination: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=_DIGEST)
    destination_expected_absent: Literal[True] = True


class _PatchChange(StrictModel):
    kind: Literal["patch"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=_DIGEST)
    patch: str = Field(min_length=1, max_length=MAX_CHANGESET_BYTES)
    patch_sha256: str = Field(pattern=_DIGEST)


_Change = Annotated[
    _WriteChange | _DeleteChange | _MoveChange | _PatchChange,
    Field(discriminator="kind"),
]
_CHANGE_ADAPTER = TypeAdapter(tuple[_Change, ...])


class _DesiredFile(StrictModel):
    path: str
    exists: bool
    sha256: str | None = Field(default=None, pattern=_DIGEST)
    size: int = Field(default=0, ge=0)
    mode: int = Field(default=0, ge=0)
    stage: str | None = None


class _OriginalFile(StrictModel):
    path: str
    exists: bool
    sha256: str | None = Field(default=None, pattern=_DIGEST)
    size: int = Field(default=0, ge=0)
    mode: int = Field(default=0, ge=0)
    backup: str | None = None


class _Manifest(StrictModel):
    version: Literal[1] = 1
    operation_id: str
    task_id: str
    workspace_id: str
    changeset_id: str
    base_tree_hash: str = Field(pattern=_DIGEST)
    result_tree_hash: str = Field(pattern=_DIGEST)
    state: Literal["prepared", "applying", "applied"]
    entries: tuple[ChangesetEntry, ...]
    originals: tuple[_OriginalFile, ...]
    desired: tuple[_DesiredFile, ...]
    created_at: float
    updated_at: float


class _Owner(StrictModel):
    version: Literal[1] = 1
    operation_id: str
    task_id: str
    workspace_id: str
    base_tree_hash: str = Field(pattern=_DIGEST)


class ChangesetEngine:
    """Durable all-old/all-new publication for bounded workspace file batches."""

    def __init__(self, workspace_broker: WorkspaceBroker) -> None:
        self.workspace_broker = workspace_broker
        self.fault_hook: Callable[[int], None] | None = None

    def apply(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        arguments: Mapping[str, Any],
    ) -> WorkerChangeset:
        if SAFE_ID.fullmatch(operation_id) is None:
            raise ChangesetError("Operation id is invalid.", code="tool_input_invalid")
        base_tree_hash = str(arguments.get("base_tree_hash", ""))
        if re.fullmatch(_DIGEST, base_tree_hash) is None:
            raise ChangesetError("Base tree hash is invalid.", code="tool_input_invalid")
        try:
            changes = _CHANGE_ADAPTER.validate_python(arguments.get("changes"))
        except Exception as exc:
            raise ChangesetError(
                "Changeset input is invalid.", code="tool_input_invalid"
            ) from exc
        if not changes or len(changes) > MAX_CHANGESET_ENTRIES:
            raise ChangesetError(
                "Changeset entry count is invalid.", code="tool_input_invalid"
            )

        repository = self.workspace_broker.repository_path(workspace_id)
        current_tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
        if current_tree_hash != base_tree_hash:
            raise ChangesetError(
                "Workspace changed before changeset preparation.",
                code="workspace_tree_changed",
            )
        transaction = self._transaction_root(repository, operation_id, create_parent=True)
        if transaction.exists():
            raise ChangesetError(
                "Changeset transaction already exists.", code="operation_not_replayable"
            )
        transaction.mkdir(parents=True)
        (transaction / "new").mkdir()
        (transaction / "old").mkdir()
        try:
            self._write_owner(
                transaction,
                _Owner(
                    operation_id=operation_id,
                    task_id=task_id,
                    workspace_id=workspace_id,
                    base_tree_hash=base_tree_hash,
                ),
            )
            manifest = self._prepare(
                workspace_id=workspace_id,
                task_id=task_id,
                operation_id=operation_id,
                repository=repository,
                transaction=transaction,
                base_tree_hash=base_tree_hash,
                changes=changes,
            )
            self._write_manifest(transaction, manifest)
            applying = manifest.model_copy(
                update={"state": "applying", "updated_at": time.time()}
            )
            self._write_manifest(transaction, applying)
            self._install(repository, transaction, applying)
            actual_tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
            if actual_tree_hash != applying.result_tree_hash:
                raise ChangesetError(
                    "Changeset publication did not match its target tree.",
                    code="workspace_tree_changed",
                )
            applied = applying.model_copy(
                update={"state": "applied", "updated_at": time.time()}
            )
            self._write_manifest(transaction, applied)
            return self._outcome(applied)
        except Exception:
            try:
                if (transaction / "manifest.json").is_file():
                    manifest = self._read_manifest(transaction)
                    self._rollback(repository, transaction, manifest)
                    if self.workspace_broker.current_tree_hash(workspace_id) != base_tree_hash:
                        raise ChangesetError(
                            "Changeset rollback could not restore the base tree.",
                            code="changeset_rollback_failed",
                        )
                    self._remove_transaction(transaction)
                else:
                    self._remove_transaction(transaction)
            except ChangesetError:
                raise
            except Exception as rollback_error:
                raise ChangesetError(
                    "Changeset rollback failed.", code="changeset_rollback_failed"
                ) from rollback_error
            raise

    def restore_snapshot(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        expected_tree_hash: str,
        snapshot: WorkspaceSnapshot,
    ) -> WorkerChangeset:
        """Atomically restore a bounded binary-safe turn snapshot by exact CAS."""
        if SAFE_ID.fullmatch(operation_id) is None:
            raise ChangesetError("Operation id is invalid.", code="tool_input_invalid")
        if self.workspace_broker.current_tree_hash(workspace_id) != expected_tree_hash:
            raise ChangesetError(
                "Workspace changed before turn navigation.",
                code="workspace_tree_changed",
            )
        files = self.workspace_broker.snapshot_files(workspace_id, snapshot)
        desired_content, entries = self._snapshot_changes(
            workspace_id=workspace_id,
            operation_id=operation_id,
            files=files,
        )
        now = time.time()
        if not desired_content:
            if expected_tree_hash != snapshot.tree_hash:
                raise ChangesetError(
                    "Turn snapshot does not match the current tree.",
                    code="workspace_tree_changed",
                )
            return WorkerChangeset(
                changeset_id=self._changeset_id(operation_id),
                task_id=task_id,
                operation_id=operation_id,
                base_tree_hash=expected_tree_hash,
                result_tree_hash=snapshot.tree_hash,
                state=ChangesetState.APPLIED,
                entries=(),
                created_at=now,
                updated_at=now,
            )
        transaction = self._transaction_root(
            self.workspace_broker.repository_path(workspace_id),
            operation_id,
            create_parent=True,
        )
        if transaction.exists():
            raise ChangesetError(
                "Turn navigation transaction already exists.",
                code="operation_not_replayable",
            )
        repository = self.workspace_broker.repository_path(workspace_id)
        transaction.mkdir(parents=True)
        (transaction / "new").mkdir()
        (transaction / "old").mkdir()
        try:
            self._write_owner(
                transaction,
                _Owner(
                    operation_id=operation_id,
                    task_id=task_id,
                    workspace_id=workspace_id,
                    base_tree_hash=expected_tree_hash,
                ),
            )
            manifest = self._prepare_desired(
                task_id=task_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
                transaction=transaction,
                base_tree_hash=expected_tree_hash,
                desired_content=desired_content,
                entries=entries,
            )
            if manifest.result_tree_hash != snapshot.tree_hash:
                raise ChangesetError(
                    "Turn snapshot result hash is invalid.", code="workspace_changed"
                )
            self._write_manifest(transaction, manifest)
            applying = manifest.model_copy(
                update={"state": "applying", "updated_at": time.time()}
            )
            self._write_manifest(transaction, applying)
            self._install(repository, transaction, applying)
            if self.workspace_broker.current_tree_hash(workspace_id) != snapshot.tree_hash:
                raise ChangesetError(
                    "Turn snapshot publication did not match its target.",
                    code="workspace_tree_changed",
                )
            applied = applying.model_copy(
                update={"state": "applied", "updated_at": time.time()}
            )
            self._write_manifest(transaction, applied)
            return self._outcome(applied)
        except Exception:
            try:
                if (transaction / "manifest.json").is_file():
                    manifest = self._read_manifest(transaction)
                    self._rollback(repository, transaction, manifest)
                    if self.workspace_broker.current_tree_hash(workspace_id) != expected_tree_hash:
                        raise ChangesetError(
                            "Turn snapshot rollback could not restore its source tree.",
                            code="changeset_rollback_failed",
                        )
                self._remove_transaction(transaction)
            except ChangesetError:
                raise
            except Exception as rollback_error:
                raise ChangesetError(
                    "Turn snapshot rollback failed.", code="changeset_rollback_failed"
                ) from rollback_error
            raise

    def reconcile(
        self, *, task_id: str, workspace_id: str, operation_id: str
    ) -> WorkerChangeset:
        repository = self.workspace_broker.repository_path(workspace_id)
        transaction = self._transaction_root(repository, operation_id)
        if not (transaction / "manifest.json").is_file():
            owner = self._read_owner(transaction)
            if (
                owner.operation_id != operation_id
                or owner.task_id != task_id
                or owner.workspace_id != workspace_id
                or self.workspace_broker.current_tree_hash(workspace_id)
                != owner.base_tree_hash
            ):
                raise ChangesetError(
                    "Changeset result is unavailable.",
                    code="operation_result_unknown",
                )
            self._remove_transaction(transaction)
            raise ChangesetError(
                "Interrupted changeset preparation was rolled back.",
                code="changeset_rolled_back",
            )
        manifest = self._read_manifest(transaction)
        if (
            manifest.operation_id != operation_id
            or manifest.task_id != task_id
            or manifest.workspace_id != workspace_id
        ):
            raise ChangesetError(
                "Changeset transaction binding changed.",
                code="operation_result_unknown",
            )
        current_tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
        if manifest.state == "applied":
            if current_tree_hash != manifest.result_tree_hash:
                raise ChangesetError(
                    "Applied changeset tree changed before reconciliation.",
                    code="operation_result_unknown",
                )
            return self._outcome(manifest)
        self._rollback(repository, transaction, manifest)
        if self.workspace_broker.current_tree_hash(workspace_id) != manifest.base_tree_hash:
            raise ChangesetError(
                "Changeset rollback encountered a concurrent workspace change.",
                code="operation_result_unknown",
            )
        self._remove_transaction(transaction)
        raise ChangesetError(
            "Interrupted changeset was rolled back.", code="changeset_rolled_back"
        )

    def finalize(
        self, *, task_id: str, workspace_id: str, operation_id: str
    ) -> None:
        repository = self.workspace_broker.repository_path(workspace_id)
        transaction = self._transaction_root(repository, operation_id)
        if not transaction.exists():
            return
        manifest = self._read_manifest(transaction)
        if (
            manifest.operation_id != operation_id
            or manifest.task_id != task_id
            or manifest.workspace_id != workspace_id
            or manifest.state != "applied"
            or self.workspace_broker.current_tree_hash(workspace_id)
            != manifest.result_tree_hash
        ):
            raise ChangesetError(
                "Changeset transaction is not safe to finalize.",
                code="operation_result_unknown",
            )
        self._remove_transaction(transaction)

    def is_applied(
        self, *, task_id: str, workspace_id: str, operation_id: str
    ) -> bool:
        repository = self.workspace_broker.repository_path(workspace_id)
        transaction = self._transaction_root(repository, operation_id)
        try:
            manifest = self._read_manifest(transaction)
        except (OSError, ChangesetError):
            return False
        return (
            manifest.state == "applied"
            and manifest.task_id == task_id
            and manifest.workspace_id == workspace_id
            and manifest.operation_id == operation_id
        )

    def has_transaction(self, *, workspace_id: str, operation_id: str) -> bool:
        repository = self.workspace_broker.repository_path(workspace_id)
        transaction = self._transaction_root(repository, operation_id)
        if transaction.is_symlink():
            raise ChangesetError(
                "Changeset transaction root is unsafe.", code="workspace_changed"
            )
        return transaction.is_dir()

    def _prepare(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        repository: Path,
        transaction: Path,
        base_tree_hash: str,
        changes: tuple[_Change, ...],
    ) -> _Manifest:
        desired_content: dict[str, tuple[bytes, int] | None] = {}
        entries: list[ChangesetEntry] = []
        touched: set[str] = set()
        for change in changes:
            path = self._normalize_path(change.path)
            if path in touched:
                raise ChangesetError(
                    "Changeset paths overlap.", code="changeset_path_conflict"
                )
            target = self._target(repository, path)
            if isinstance(change, _WriteChange):
                existing = self._read_regular(target, required=not change.expected_absent)
                if change.expected_absent:
                    if existing is not None:
                        raise ChangesetError(
                            "Added file already exists.", code="preimage_changed"
                        )
                    kind = ChangeKind.ADD
                else:
                    assert existing is not None and change.expected_sha256 is not None
                    self._require_digest(existing[0], change.expected_sha256)
                    kind = ChangeKind.MODIFY
                content = change.content.encode("utf-8")
                self._require_digest(content, change.content_sha256)
                mode = existing[1] if existing is not None else 0o644
                desired_content[path] = (content, mode)
                entries.append(
                    self._entry(
                        workspace_id,
                        operation_id,
                        path,
                        kind,
                        change.expected_sha256,
                        change.content_sha256,
                    )
                )
            elif isinstance(change, _DeleteChange):
                existing = self._read_regular(target, required=True)
                assert existing is not None
                self._require_digest(existing[0], change.expected_sha256)
                desired_content[path] = None
                entries.append(
                    self._entry(
                        workspace_id,
                        operation_id,
                        path,
                        ChangeKind.DELETE,
                        change.expected_sha256,
                        None,
                    )
                )
            elif isinstance(change, _MoveChange):
                destination = self._normalize_path(change.destination)
                if destination in touched or destination == path:
                    raise ChangesetError(
                        "Changeset paths overlap.", code="changeset_path_conflict"
                    )
                existing = self._read_regular(target, required=True)
                assert existing is not None
                self._require_digest(existing[0], change.expected_sha256)
                if self._read_regular(
                    self._target(repository, destination), required=False
                ) is not None:
                    raise ChangesetError(
                        "Move destination already exists.", code="preimage_changed"
                    )
                desired_content[path] = None
                desired_content[destination] = existing
                touched.add(destination)
                entries.append(
                    self._entry(
                        workspace_id,
                        operation_id,
                        path,
                        ChangeKind.MOVE,
                        change.expected_sha256,
                        change.expected_sha256,
                        destination=destination,
                    )
                )
            else:
                existing = self._read_regular(target, required=True)
                assert existing is not None
                self._require_digest(existing[0], change.expected_sha256)
                patch_bytes = change.patch.encode("utf-8")
                self._require_digest(patch_bytes, change.patch_sha256)
                try:
                    content = apply_unified_patch(path, existing[0], change.patch)
                except UnifiedPatchError as exc:
                    raise ChangesetError(str(exc), code=exc.code) from exc
                digest = hashlib.sha256(content).hexdigest()
                desired_content[path] = (content, existing[1])
                entries.append(
                    self._entry(
                        workspace_id,
                        operation_id,
                        path,
                        ChangeKind.MODIFY,
                        change.expected_sha256,
                        digest,
                    )
                )
            touched.add(path)
        total_bytes = sum(
            len(desired[0])
            for desired in desired_content.values()
            if desired is not None
        )
        if total_bytes > MAX_CHANGESET_BYTES:
            raise ChangesetError(
                "Changeset content is too large.", code="tool_input_invalid"
            )

        originals: list[_OriginalFile] = []
        desired_files: list[_DesiredFile] = []
        for index, path in enumerate(sorted(desired_content)):
            target = self._target(repository, path)
            existing = self._read_regular(target, required=False)
            if existing is None:
                originals.append(_OriginalFile(path=path, exists=False))
            else:
                backup_name = f"old/{index:04d}"
                backup = transaction / backup_name
                self._write_bound(backup, existing[0], existing[1])
                originals.append(
                    _OriginalFile(
                        path=path,
                        exists=True,
                        sha256=hashlib.sha256(existing[0]).hexdigest(),
                        size=len(existing[0]),
                        mode=existing[1],
                        backup=backup_name,
                    )
                )
            desired = desired_content[path]
            if desired is None:
                desired_files.append(_DesiredFile(path=path, exists=False))
            else:
                stage_name = f"new/{index:04d}"
                stage = transaction / stage_name
                self._write_bound(stage, desired[0], desired[1])
                desired_files.append(
                    _DesiredFile(
                        path=path,
                        exists=True,
                        sha256=hashlib.sha256(desired[0]).hexdigest(),
                        size=len(desired[0]),
                        mode=desired[1],
                        stage=stage_name,
                    )
                )
        result_tree_hash = self._result_tree_hash(
            workspace_id, {item.path: item for item in desired_files}
        )
        now = time.time()
        return _Manifest(
            operation_id=operation_id,
            task_id=task_id,
            workspace_id=workspace_id,
            changeset_id=self._changeset_id(operation_id),
            base_tree_hash=base_tree_hash,
            result_tree_hash=result_tree_hash,
            state="prepared",
            entries=tuple(entries),
            originals=tuple(originals),
            desired=tuple(desired_files),
            created_at=now,
            updated_at=now,
        )

    def _snapshot_changes(
        self,
        *,
        workspace_id: str,
        operation_id: str,
        files: tuple[SourceFile, ...],
    ) -> tuple[dict[str, tuple[bytes, int] | None], list[ChangesetEntry]]:
        repository = self.workspace_broker.repository_path(workspace_id)
        target = {item.path: item for item in files}
        current = {
            item.display_path: item
            for item in self.workspace_broker.tree(workspace_id)
            if item.kind == "file"
        }
        paths = sorted(set(target) | set(current))
        if len(paths) > 4096:
            raise ChangesetError(
                "Turn snapshot touches too many files.", code="changeset_too_large"
            )
        desired: dict[str, tuple[bytes, int] | None] = {}
        entries: list[ChangesetEntry] = []
        for path in paths:
            source = target.get(path)
            existing = current.get(path)
            if source is None:
                assert existing is not None and existing.sha256 is not None
                desired[path] = None
                entries.append(
                    self._entry(
                        workspace_id,
                        operation_id,
                        path,
                        ChangeKind.DELETE,
                        existing.sha256,
                        None,
                    )
                )
                continue
            digest = hashlib.sha256(source.content).hexdigest()
            if existing is not None and existing.sha256 == digest:
                continue
            desired[path] = (source.content, 0o755 if source.executable else 0o644)
            entries.append(
                self._entry(
                    workspace_id,
                    operation_id,
                    path,
                    ChangeKind.ADD if existing is None else ChangeKind.MODIFY,
                    existing.sha256 if existing is not None else None,
                    digest,
                    binary=b"\0" in source.content,
                )
            )
        if sum(len(item[0]) for item in desired.values() if item is not None) > MAX_CHANGESET_BYTES:
            raise ChangesetError(
                "Turn snapshot content is too large.", code="changeset_too_large"
            )
        # Resolve every target now so path safety is checked before staging.
        for path in desired:
            self._target(repository, path)
        return desired, entries

    def _prepare_desired(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        transaction: Path,
        base_tree_hash: str,
        desired_content: Mapping[str, tuple[bytes, int] | None],
        entries: list[ChangesetEntry],
    ) -> _Manifest:
        repository = self.workspace_broker.repository_path(workspace_id)
        originals: list[_OriginalFile] = []
        desired_files: list[_DesiredFile] = []
        for index, path in enumerate(sorted(desired_content)):
            target = self._target(repository, path)
            existing = self._read_regular(target, required=False)
            if existing is None:
                originals.append(_OriginalFile(path=path, exists=False))
            else:
                backup_name = f"old/{index:04d}"
                self._write_bound(transaction / backup_name, existing[0], existing[1])
                originals.append(
                    _OriginalFile(
                        path=path,
                        exists=True,
                        sha256=hashlib.sha256(existing[0]).hexdigest(),
                        size=len(existing[0]),
                        mode=existing[1],
                        backup=backup_name,
                    )
                )
            content = desired_content[path]
            if content is None:
                desired_files.append(_DesiredFile(path=path, exists=False))
            else:
                stage_name = f"new/{index:04d}"
                self._write_bound(transaction / stage_name, content[0], content[1])
                desired_files.append(
                    _DesiredFile(
                        path=path,
                        exists=True,
                        sha256=hashlib.sha256(content[0]).hexdigest(),
                        size=len(content[0]),
                        mode=content[1],
                        stage=stage_name,
                    )
                )
        result_tree_hash = self._result_tree_hash(
            workspace_id, {item.path: item for item in desired_files}
        )
        now = time.time()
        return _Manifest(
            operation_id=operation_id,
            task_id=task_id,
            workspace_id=workspace_id,
            changeset_id=self._changeset_id(operation_id),
            base_tree_hash=base_tree_hash,
            result_tree_hash=result_tree_hash,
            state="prepared",
            entries=tuple(entries),
            originals=tuple(originals),
            desired=tuple(desired_files),
            created_at=now,
            updated_at=now,
        )

    def _install(
        self, repository: Path, transaction: Path, manifest: _Manifest
    ) -> None:
        if self.workspace_broker._tree_hash(repository) != manifest.base_tree_hash:
            raise ChangesetError(
                "Workspace changed before changeset publication.",
                code="workspace_tree_changed",
            )
        for index, desired in enumerate(manifest.desired):
            target = self._target(
                repository,
                desired.path,
                workspace_id=manifest.workspace_id,
                create_parents=True,
            )
            original = next(item for item in manifest.originals if item.path == desired.path)
            self._require_current_state(target, original, desired)
            if desired.exists:
                assert desired.stage is not None
                stage = transaction / desired.stage
                self._require_file(stage, desired.sha256, desired.size)
                os.replace(stage, target)
                os.chmod(target, desired.mode)
                self.workspace_broker.apply_slot_owner(manifest.workspace_id, target)
            elif target.exists():
                target.unlink()
            if self.fault_hook is not None:
                self.fault_hook(index)

    def _rollback(
        self, repository: Path, transaction: Path, manifest: _Manifest
    ) -> None:
        desired_by_path = {item.path: item for item in manifest.desired}
        for original in reversed(manifest.originals):
            target = self._target(
                repository,
                original.path,
                workspace_id=manifest.workspace_id,
                create_parents=True,
            )
            desired = desired_by_path[original.path]
            self._require_current_state(target, original, desired, rollback=True)
            if original.exists:
                assert original.backup is not None
                backup = transaction / original.backup
                self._require_file(backup, original.sha256, original.size)
                restore_id = hashlib.sha256(original.path.encode()).hexdigest()[:16]
                temporary = transaction / f"restore-{restore_id}"
                shutil.copyfile(backup, temporary)
                self._fsync_file(temporary)
                os.replace(temporary, target)
                os.chmod(target, original.mode)
                self.workspace_broker.apply_slot_owner(manifest.workspace_id, target)
            elif target.exists():
                target.unlink()

    def _require_current_state(
        self,
        target: Path,
        original: _OriginalFile,
        desired: _DesiredFile,
        *,
        rollback: bool = False,
    ) -> None:
        current = self._read_regular(target, required=False)
        if current is None:
            if (not original.exists and not rollback) or not desired.exists:
                return
            if rollback and not original.exists:
                return
            raise ChangesetError(
                "Changeset target identity changed.", code="workspace_changed"
            )
        digest = hashlib.sha256(current[0]).hexdigest()
        allowed = {value for value in (original.sha256, desired.sha256) if value}
        if digest not in allowed:
            raise ChangesetError(
                "Changeset target changed concurrently.", code="workspace_changed"
            )

    def _result_tree_hash(
        self, workspace_id: str, desired: Mapping[str, _DesiredFile]
    ) -> str:
        entries = {
            entry.display_path: (entry.size, entry.sha256)
            for entry in self.workspace_broker.tree(workspace_id)
            if entry.kind == "file"
        }
        for path, item in desired.items():
            if item.exists:
                entries[path] = (item.size, item.sha256)
            else:
                entries.pop(path, None)
        digest = hashlib.sha256()
        for path in sorted(entries):
            size, content_sha256 = entries[path]
            assert content_sha256 is not None
            encoded_path = path.encode("utf-8")
            digest.update(len(encoded_path).to_bytes(4, "big"))
            digest.update(encoded_path)
            digest.update(size.to_bytes(8, "big"))
            digest.update(bytes.fromhex(content_sha256))
        return digest.hexdigest()

    def _target(
        self,
        repository: Path,
        value: str,
        *,
        workspace_id: str | None = None,
        create_parents: bool = False,
    ) -> Path:
        path = PurePosixPath(value)
        parent = repository
        for part in path.parts[:-1]:
            parent = parent / part
            if parent.exists():
                if not parent.is_dir() or self._is_link(parent):
                    raise ChangesetError(
                        "Workspace path changed.", code="workspace_changed"
                    )
            elif create_parents:
                parent.mkdir()
                if workspace_id is not None:
                    self.workspace_broker.apply_slot_owner(workspace_id, parent)
            else:
                break
        target = repository.joinpath(*path.parts)
        if repository not in target.parents:
            raise ChangesetError(
                "Workspace path escaped the task.", code="workspace_path_invalid"
            )
        return target

    @staticmethod
    def _normalize_path(value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in path.parts)
        ):
            raise ChangesetError(
                "Workspace path is invalid.", code="workspace_path_invalid"
            )
        return path.as_posix()

    @classmethod
    def _read_regular(
        cls, target: Path, *, required: bool
    ) -> tuple[bytes, int] | None:
        try:
            before = target.lstat()
        except FileNotFoundError:
            if required:
                raise ChangesetError(
                    "Changeset preimage is missing.", code="preimage_changed"
                )
            return None
        if (
            not stat.S_ISREG(before.st_mode)
            or cls._is_link(target)
            or before.st_nlink != 1
        ):
            raise ChangesetError(
                "Changeset target is not a private regular file.",
                code="workspace_changed",
            )
        content = target.read_bytes()
        after = target.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(content) != before.st_size
        ):
            raise ChangesetError(
                "Changeset target changed while reading.", code="workspace_changed"
            )
        return content, stat.S_IMODE(before.st_mode)

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()  # type: ignore[attr-defined]
        )

    @staticmethod
    def _require_digest(content: bytes, expected: str) -> None:
        if hashlib.sha256(content).hexdigest() != expected:
            raise ChangesetError(
                "Changeset preimage binding changed.", code="preimage_changed"
            )

    @staticmethod
    def _require_file(path: Path, sha256: str | None, size: int) -> None:
        content = path.read_bytes()
        if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
            raise ChangesetError(
                "Changeset transaction file changed.", code="workspace_changed"
            )

    @staticmethod
    def _write_bound(path: Path, content: bytes, mode: int) -> None:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, mode)

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _write_manifest(transaction: Path, manifest: _Manifest) -> None:
        temporary = transaction / "manifest.next"
        payload = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, transaction / "manifest.json")
        ChangesetEngine._fsync_directory(transaction)

    @staticmethod
    def _write_owner(transaction: Path, owner: _Owner) -> None:
        path = transaction / "owner.json"
        payload = owner.model_dump_json().encode("utf-8")
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        ChangesetEngine._fsync_directory(transaction)

    @staticmethod
    def _read_owner(transaction: Path) -> _Owner:
        try:
            return _Owner.model_validate_json((transaction / "owner.json").read_bytes())
        except Exception as exc:
            raise ChangesetError(
                "Changeset owner record is unavailable.",
                code="operation_result_unknown",
            ) from exc

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_manifest(transaction: Path) -> _Manifest:
        try:
            return _Manifest.model_validate_json(
                (transaction / "manifest.json").read_bytes()
            )
        except Exception as exc:
            raise ChangesetError(
                "Changeset journal is unavailable.", code="operation_result_unknown"
            ) from exc

    @classmethod
    def _transaction_root(
        cls, repository: Path, operation_id: str, *, create_parent: bool = False
    ) -> Path:
        suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:32]
        root = repository.parent / "changesets"
        if root.exists():
            if not root.is_dir() or cls._is_link(root):
                raise ChangesetError(
                    "Changeset transaction root is unsafe.", code="workspace_changed"
                )
        elif create_parent:
            root.mkdir()
        return root / f"txn_{suffix}"

    @staticmethod
    def _changeset_id(operation_id: str) -> str:
        suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:32]
        return f"changeset_{suffix}"

    @staticmethod
    def _entry(
        workspace_id: str,
        operation_id: str,
        path: str,
        kind: ChangeKind,
        preimage: str | None,
        postimage: str | None,
        *,
        destination: str | None = None,
        binary: bool = False,
    ) -> ChangesetEntry:
        suffix = hashlib.sha256(
            f"{workspace_id}\0{operation_id}\0{path}".encode("utf-8")
        ).hexdigest()[:32]
        return ChangesetEntry(
            entry_id=f"entry_{suffix}",
            kind=kind,
            display_path=path,
            destination_display_path=destination,
            preimage_sha256=preimage,
            postimage_sha256=postimage,
            binary=binary,
        )

    @staticmethod
    def _outcome(manifest: _Manifest) -> WorkerChangeset:
        return WorkerChangeset(
            changeset_id=manifest.changeset_id,
            task_id=manifest.task_id,
            operation_id=manifest.operation_id,
            base_tree_hash=manifest.base_tree_hash,
            result_tree_hash=manifest.result_tree_hash,
            state=ChangesetState.APPLIED,
            entries=manifest.entries,
            created_at=manifest.created_at,
            updated_at=manifest.updated_at,
        )

    def _remove_transaction(self, transaction: Path) -> None:
        if transaction.exists():
            self.workspace_broker._remove_tree(transaction)
