from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


TRANSACTION_ROOT_NAME = "modelmirror-transactions"
OWNER_FILE_NAME = "owner.marker"
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
Stamp = tuple[str, str, str]
StampCallback = Callable[[str | None], Stamp]
MutationHook = Callable[[str, int, str], None]
CommitCallback = Callable[[], None]
OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


class HostFileTransactionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FileMutation:
    path: str
    before: bytes | None
    after: bytes | None
    mode: int = 0o644

    @property
    def key(self) -> str:
        return hashlib.sha256(self.path.encode("utf-8")).hexdigest()[:24]


class HostFileTransaction:
    """Crash-recoverable, no-overwrite transaction for helper-owned files."""

    def __init__(
        self,
        root: Path,
        operation_id: str,
        action: str,
        expected_stamp: Stamp,
        stamp_callback: StampCallback,
        *,
        created_directories: Sequence[str] = (),
        mutation_hook: MutationHook | None = None,
    ) -> None:
        candidate = Path(root)
        if (
            not candidate.is_absolute()
            or _is_link_or_reparse(candidate)
            or not candidate.is_dir()
            or OPERATION_ID_PATTERN.fullmatch(operation_id) is None
            or action not in {"apply", "revert"}
            or len(expected_stamp) != 3
            or any(not isinstance(value, str) or not value for value in expected_stamp)
        ):
            raise HostFileTransactionError("transaction_parameters_invalid")
        self.root = candidate
        self.git = self.root / ".git"
        if _is_link_or_reparse(self.git) or not self.git.is_dir():
            raise HostFileTransactionError("git_metadata_unsafe")
        self.operation_id = operation_id
        self.action = action
        self.expected_stamp = expected_stamp
        self.stamp_callback = stamp_callback
        self.created_directories = tuple(created_directories)
        self.mutation_hook = mutation_hook
        self.tx_root = self.git / TRANSACTION_ROOT_NAME
        self.active_dir = self.tx_root / operation_id
        self.cleanup_dir = self.tx_root / f"{operation_id}.cleanup"
        self.rollback_dir = self.tx_root / f"{operation_id}.rollback"
        self.tx_dir = self.active_dir
        self._marker = f"modelmirror-lock:{operation_id}\n".encode("ascii")

    def prepare(self, mutations: Sequence[FileMutation]) -> None:
        files = self._validate_mutations(mutations)
        self._select_transaction_dir()
        if self.tx_dir == self.cleanup_dir:
            raise HostFileTransactionError("transaction_already_committed")
        if self.tx_dir == self.rollback_dir:
            self._recover_rolled_back(files)
            self._select_transaction_dir()
        with self._git_locks():
            self._assert_stamp()
            with self._filesystem_guard(files):
                if self._classify(files) != "before":
                    raise HostFileTransactionError("target_changed")
                self._prepare_transaction(files)
            self._assert_stamp()

    def settle(
        self,
        mutations: Sequence[FileMutation],
        *,
        commit_callback: CommitCallback | None = None,
    ) -> Literal["before", "after"]:
        files = self._validate_mutations(mutations)
        self._select_transaction_dir()
        if self.tx_dir == self.cleanup_dir:
            with self._filesystem_guard(files):
                self._validate_sealed_transaction(files)
                if self._classify(files) != "after":
                    raise HostFileTransactionError("transaction_conflict")
            return "after"
        if self.tx_dir == self.rollback_dir:
            self._recover_rolled_back(files)
            return "before"
        if not self.active_dir.exists():
            state = self._classify(files)
            if state not in {"before", "after"}:
                raise HostFileTransactionError("transaction_conflict")
            if state == "after" and commit_callback is not None:
                raise HostFileTransactionError("transaction_evidence_missing")
            return state
        with self._git_locks():
            self._assert_stamp()
            committed = False
            with contextlib.ExitStack() as published_targets:
                with self._filesystem_guard(files):
                    manifest = self._resume_transaction(files)
                    marker = self.active_dir / "commit.marker"
                    if marker.exists():
                        self._assert_exact(marker, _commit_marker(manifest))
                        self._assert_committed_artifacts(files)
                        if self._classify(files) != "after":
                            raise HostFileTransactionError("transaction_conflict")
                        published_targets.enter_context(
                            _guard_published_targets(self.root, self.active_dir, files)
                        )
                        committed = True
                    else:
                        self._remove_partial(marker)
                        self._rollback_locked(files)
                        if self._classify(files) != "before":
                            raise HostFileTransactionError("transaction_rollback_failed")
                        self._seal_rollback(files)
                        self._cleanup_rollback_locked(files)
                if not committed:
                    self._assert_stamp()
                    return "before"
                # Target handles remain protected while directory guards are
                # released, so revert may remove empty parents without opening
                # a same-content replacement window before the durable callback.
                if commit_callback is not None:
                    commit_callback()
                self._assert_stamp()
                self._seal_transaction(files)
                return "after"

    def apply(
        self,
        mutations: Sequence[FileMutation],
        *,
        commit_callback: CommitCallback,
    ) -> None:
        files = self._validate_mutations(mutations)
        self._select_transaction_dir()
        if self.tx_dir == self.cleanup_dir:
            with self._filesystem_guard(files):
                self._validate_sealed_transaction(files)
                if self._classify(files) != "after":
                    raise HostFileTransactionError("transaction_conflict")
            return
        if self.tx_dir == self.rollback_dir:
            self._recover_rolled_back(files)
            self._select_transaction_dir()
        with self._git_locks():
            self._assert_stamp()
            committed = False
            with contextlib.ExitStack() as published_targets:
                with self._filesystem_guard(files):
                    manifest = self._prepare_transaction(files)
                    marker = self.active_dir / "commit.marker"
                    if marker.exists():
                        self._assert_exact(marker, _commit_marker(manifest))
                        self._assert_committed_artifacts(files)
                        if self._classify(files) != "after":
                            raise HostFileTransactionError("transaction_conflict")
                        committed = True
                    else:
                        self._remove_partial(marker)
                        self._rollback_locked(files)
                        if self._classify(files) != "before":
                            raise HostFileTransactionError("target_changed")
                    try:
                        for index, item in enumerate(files) if not committed else ():
                            self._assert_stamp()
                            if self.mutation_hook is not None:
                                self.mutation_hook(self.action, index, item.path)
                            self._assert_stamp()
                            target = self.root / PurePosixPath(item.path)
                            backup = self.active_dir / f"before-{item.key}"
                            stage = self.active_dir / f"after-{item.key}"
                            if item.before is not None:
                                _move_verified_no_replace(target, backup, item.before)
                            elif target.exists():
                                raise HostFileTransactionError("target_changed")
                            if item.after is not None:
                                _move_verified_no_replace(stage, target, item.after)
                            elif target.exists():
                                raise HostFileTransactionError("target_changed")
                            self._assert_stamp()
                            if _read_regular(target) != item.after:
                                raise HostFileTransactionError("target_changed")
                        if not committed:
                            if self._classify(files) != "after":
                                raise HostFileTransactionError("transaction_incomplete")
                            self._assert_stamp()
                            _write_durable_no_replace(
                                marker,
                                _commit_marker(manifest),
                                0o600,
                            )
                            self._assert_committed_artifacts(files)
                            committed = True
                        published_targets.enter_context(
                            _guard_published_targets(self.root, self.active_dir, files)
                        )
                    except BaseException:
                        if marker.exists():
                            # A durable marker is the commit point. Never roll it
                            # back merely because the caller lost the acknowledgement.
                            self._assert_exact(marker, _commit_marker(manifest))
                            raise
                        try:
                            self._rollback_locked(files)
                            self._seal_rollback(files)
                            self._cleanup_rollback_locked(files)
                        except BaseException as rollback_error:
                            raise HostFileTransactionError(
                                "transaction_rollback_failed"
                            ) from rollback_error
                        raise
                if not committed:
                    raise HostFileTransactionError("transaction_incomplete")
                commit_callback()
                self._assert_stamp()
                self._seal_transaction(files)

    def cleanup_committed(self, mutations: Sequence[FileMutation]) -> None:
        files = self._validate_mutations(mutations)
        self._select_transaction_dir()
        if not self.tx_dir.exists():
            return
        if self.tx_dir != self.cleanup_dir:
            raise HostFileTransactionError("transaction_not_settled")
        with self._filesystem_guard(files):
            if self._classify(files) != "after":
                raise HostFileTransactionError("transaction_conflict")
            self._cleanup_sealed_locked(files)

    cleanup = cleanup_committed

    def _validate_mutations(
        self,
        mutations: Sequence[FileMutation],
    ) -> tuple[FileMutation, ...]:
        files = tuple(mutations)
        if not files or len(files) > 20:
            raise HostFileTransactionError("transaction_parameters_invalid")
        paths: list[str] = []
        for item in files:
            if not isinstance(item.path, str):
                raise HostFileTransactionError("transaction_parameters_invalid")
            pure = PurePosixPath(item.path)
            if (
                not item.path
                or "\\" in item.path
                or pure.is_absolute()
                or pure.as_posix() != item.path
                or any(part in {"", ".", ".."} for part in pure.parts)
                or item.before is None and item.after is None
                or item.before == item.after
                or item.before is not None and not isinstance(item.before, bytes)
                or item.after is not None and not isinstance(item.after, bytes)
                or item.mode not in {0o644, 0o755}
            ):
                raise HostFileTransactionError("transaction_parameters_invalid")
            paths.append(item.path)
        if paths != sorted(paths) or len({path.casefold() for path in paths}) != len(paths):
            raise HostFileTransactionError("transaction_parameters_invalid")
        if len({item.key for item in files}) != len(files):
            raise HostFileTransactionError("transaction_parameters_invalid")
        return files

    @contextlib.contextmanager
    def _filesystem_guard(self, files: Sequence[FileMutation]) -> Iterator[None]:
        parents = [self.root, self.git]
        for item in files:
            current = self.root
            for part in PurePosixPath(item.path).parts[:-1]:
                current = current / part
                parents.append(current)
        existing = tuple(path for path in parents if path.exists())
        with _guard_directories(existing):
            yield

    def _prepare_transaction(self, files: Sequence[FileMutation]) -> bytes:
        manifest = self._manifest(files)
        if self.tx_root.exists():
            _assert_directory(self.tx_root)
        else:
            self.tx_root.mkdir()
            _assert_directory(self.tx_root)
            _fsync_directory(self.git)
        if self.active_dir.exists():
            self.tx_dir = self.active_dir
            self._validate_transaction(files)
            return manifest
        owner = _owner_marker(manifest)
        # A hard stop may happen between directory creation and the first owner
        # write.  A fresh random name makes that orphan inert: later attempts do
        # not inspect or delete it, and therefore cannot mistake an unknown local
        # directory for transaction evidence.
        try:
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{self.operation_id}.preparing-",
                    dir=self.tx_root,
                )
            )
        except OSError as exc:
            raise HostFileTransactionError("transaction_unavailable") from exc
        _assert_directory(staging)
        _fsync_directory(self.tx_root)
        _write_durable_no_replace(staging / OWNER_FILE_NAME, owner, 0o600)
        self.tx_dir = staging
        with _guard_directories((self.tx_root, staging)):
            self._assert_exact(staging / OWNER_FILE_NAME, owner)
            _write_durable_no_replace(
                staging / "manifest.json",
                manifest,
                0o600,
            )
            for item in files:
                if item.after is None:
                    continue
                stage = staging / f"after-{item.key}"
                identity = staging / f"after-id-{item.key}"
                backup = staging / f"before-{item.key}"
                target = self.root / PurePosixPath(item.path)
                if stage.exists():
                    self._assert_exact(stage, item.after)
                    self._remove_partial(stage)
                    continue
                if backup.exists() or _read_regular(target) != item.before:
                    # Preparation artifacts are never synthesized once a target
                    # mutation may have started. Recovery must classify and either
                    # roll back the exact known state or fail closed.
                    self._remove_partial(stage)
                    continue
                _write_durable_no_replace(stage, item.after, item.mode)
                _write_durable_no_replace(
                    identity,
                    _file_identity(stage),
                    0o600,
                )
            for item in files:
                if item.after is None:
                    continue
                stage = staging / f"after-{item.key}"
                identity = staging / f"after-id-{item.key}"
                if not identity.exists():
                    if not stage.exists():
                        raise HostFileTransactionError("transaction_conflict")
                    _write_durable_no_replace(
                        identity,
                        _file_identity(stage),
                        0o600,
                    )
        self._validate_transaction(files, directory=staging)
        try:
            move_directory_no_replace(
                staging,
                self.active_dir,
                expected_identity=file_identity(staging),
                owner_name=OWNER_FILE_NAME,
                owner_content=owner,
            )
        except HostFileTransactionError:
            if not self.active_dir.exists():
                raise
        self.tx_dir = self.active_dir
        self._validate_transaction(files)
        return manifest

    def _resume_transaction(self, files: Sequence[FileMutation]) -> bytes:
        """Finish only the non-mutating prepare phase after a hard crash."""
        self.tx_dir = self.active_dir
        return self._prepare_transaction(files)

    def _validate_transaction(
        self,
        files: Sequence[FileMutation],
        *,
        directory: Path | None = None,
    ) -> bytes:
        _assert_directory(self.tx_root)
        tx_dir = directory or self.tx_dir
        _assert_directory(tx_dir)
        manifest = self._manifest(files)
        self._assert_exact(tx_dir / OWNER_FILE_NAME, _owner_marker(manifest))
        self._assert_exact(tx_dir / "manifest.json", manifest)
        allowed = {
            OWNER_FILE_NAME,
            "manifest.json",
            "manifest.json.partial",
            "commit.marker",
            "commit.marker.partial",
        }
        for item in files:
            allowed.update(
                {
                    f"before-{item.key}",
                    f"after-{item.key}",
                    f"after-{item.key}.partial",
                    f"after-id-{item.key}",
                    f"after-id-{item.key}.partial",
                }
            )
        try:
            entries = tuple(tx_dir.iterdir())
        except OSError as exc:
            raise HostFileTransactionError("transaction_unavailable") from exc
        if any(entry.name not in allowed or _is_link_or_reparse(entry) for entry in entries):
            raise HostFileTransactionError("transaction_conflict")
        for item in files:
            stage = tx_dir / f"after-{item.key}"
            identity = tx_dir / f"after-id-{item.key}"
            backup = tx_dir / f"before-{item.key}"
            if stage.exists():
                self._assert_exact(stage, item.after)
            if item.after is not None:
                recorded_identity = _read_file_identity(identity)
                current_after = (
                    stage
                    if stage.exists()
                    else self.root / PurePosixPath(item.path)
                )
                if _read_regular(current_after) == item.after:
                    if _file_identity(current_after) != recorded_identity:
                        raise HostFileTransactionError("transaction_conflict")
                elif not backup.exists():
                    raise HostFileTransactionError("transaction_conflict")
            elif identity.exists():
                raise HostFileTransactionError("transaction_conflict")
            if backup.exists():
                self._assert_exact(backup, item.before)
        return manifest

    def _rollback_locked(self, files: Sequence[FileMutation]) -> None:
        marker = self.tx_dir / "commit.marker"
        if marker.exists():
            raise HostFileTransactionError("transaction_already_committed")
        for item in reversed(tuple(files)):
            target = self.root / PurePosixPath(item.path)
            stage = self.tx_dir / f"after-{item.key}"
            backup = self.tx_dir / f"before-{item.key}"
            current = _read_regular(target)
            missing_during_move = (
                current is None
                and item.before is not None
                and backup.exists()
            )
            if current not in {item.before, item.after} and not missing_during_move:
                raise HostFileTransactionError("transaction_conflict")
            if backup.exists() and current == item.before:
                # Once the original object is parked in backup, an object at
                # the target path was created externally, even when bytes match.
                raise HostFileTransactionError("transaction_conflict")
            if current == item.after and item.after != item.before:
                if stage.exists():
                    # A stage file plus an after-image target is not a state
                    # produced by our true no-replace rename. It may be a user
                    # file; deleting either side would destroy evidence.
                    raise HostFileTransactionError("transaction_conflict")
                elif item.after is not None:
                    identity_path = self.tx_dir / f"after-id-{item.key}"
                    expected_identity = (
                        _read_file_identity(identity_path).decode("ascii").strip()
                    )
                    _move_verified_no_replace(
                        target,
                        stage,
                        item.after,
                        expected_identity=expected_identity,
                    )
            current = _read_regular(target)
            if item.before is not None and current is None:
                if not backup.exists():
                    raise HostFileTransactionError("transaction_conflict")
                _move_verified_no_replace(backup, target, item.before)
            elif item.before is None and current is not None:
                raise HostFileTransactionError("transaction_conflict")
            if _read_regular(target) != item.before:
                raise HostFileTransactionError("transaction_conflict")
            if backup.exists():
                _remove_exact(backup, item.before)
            if item.after is not None and not stage.exists():
                _write_durable_no_replace(stage, item.after, item.mode)

    def _assert_committed_artifacts(
        self,
        files: Sequence[FileMutation],
    ) -> None:
        for item in files:
            backup = self.tx_dir / f"before-{item.key}"
            stage = self.tx_dir / f"after-{item.key}"
            identity = self.tx_dir / f"after-id-{item.key}"
            if item.before is not None:
                self._assert_exact(backup, item.before)
            elif backup.exists():
                raise HostFileTransactionError("transaction_conflict")
            if stage.exists():
                raise HostFileTransactionError("transaction_conflict")
            if item.after is not None:
                if _file_identity(self.root / PurePosixPath(item.path)) != _read_file_identity(identity):
                    raise HostFileTransactionError("transaction_conflict")
            elif identity.exists():
                raise HostFileTransactionError("transaction_conflict")

    def _cleanup_locked(
        self,
        files: Sequence[FileMutation],
        *,
        require_marker: bool,
    ) -> None:
        directory_identity = file_identity(self.tx_dir)
        manifest = self._manifest(files)
        marker = self.tx_dir / "commit.marker"
        if require_marker:
            self._assert_exact(marker, _commit_marker(manifest))
        elif marker.exists():
            raise HostFileTransactionError("transaction_already_committed")
        self._validate_transaction(files)
        for item in files:
            for prefix, expected in (("before", item.before), ("after", item.after)):
                artifact = self.tx_dir / f"{prefix}-{item.key}"
                if artifact.exists():
                    _remove_exact(artifact, expected)
                self._remove_partial(artifact)
            identity = self.tx_dir / f"after-id-{item.key}"
            if identity.exists():
                _remove_file_identity_artifact(identity)
            self._remove_partial(identity)
        self._remove_partial(marker)
        if marker.exists():
            _remove_exact(marker, _commit_marker(manifest))
        self._remove_partial(self.tx_dir / "manifest.json")
        _remove_exact(self.tx_dir / "manifest.json", manifest)
        _remove_exact(self.tx_dir / OWNER_FILE_NAME, _owner_marker(manifest))
        _remove_empty_directory_exact(self.tx_dir, directory_identity)

    def _seal_rollback(self, files: Sequence[FileMutation]) -> None:
        """Publish a durable before-state before deleting rollback evidence."""
        self.tx_dir = self.active_dir
        manifest = self._validate_transaction(files)
        marker = self.active_dir / "commit.marker"
        if marker.exists():
            raise HostFileTransactionError("transaction_already_committed")
        if self._classify(files) != "before":
            raise HostFileTransactionError("transaction_rollback_failed")
        for item in files:
            backup = self.active_dir / f"before-{item.key}"
            stage = self.active_dir / f"after-{item.key}"
            if backup.exists():
                raise HostFileTransactionError("transaction_rollback_failed")
            if item.after is not None:
                self._assert_exact(stage, item.after)
            elif stage.exists():
                raise HostFileTransactionError("transaction_conflict")
        if self.rollback_dir.exists():
            raise HostFileTransactionError("transaction_conflict")
        move_directory_no_replace(
            self.active_dir,
            self.rollback_dir,
            expected_identity=file_identity(self.active_dir),
            owner_name=OWNER_FILE_NAME,
            owner_content=_owner_marker(manifest),
        )
        _fsync_directory(self.tx_root)
        self.tx_dir = self.rollback_dir

    def _recover_rolled_back(self, files: Sequence[FileMutation]) -> None:
        with self._git_locks():
            self._assert_stamp()
            with self._filesystem_guard(files):
                self.tx_dir = self.rollback_dir
                self._validate_rollback_transaction(files)
                if self._classify(files) != "before":
                    raise HostFileTransactionError("transaction_conflict")
                self._cleanup_rollback_locked(files)
            self._assert_stamp()

    def _validate_rollback_transaction(
        self,
        files: Sequence[FileMutation],
    ) -> bytes:
        self.tx_dir = self.rollback_dir
        _assert_directory(self.rollback_dir)
        manifest = self._manifest(files)
        owner_path = self.rollback_dir / OWNER_FILE_NAME
        if owner_path.exists():
            self._assert_exact(owner_path, _owner_marker(manifest))
        elif tuple(self.rollback_dir.iterdir()):
            raise HostFileTransactionError("transaction_conflict")
        manifest_path = self.rollback_dir / "manifest.json"
        if manifest_path.exists():
            self._assert_exact(manifest_path, manifest)
        marker = self.rollback_dir / "commit.marker"
        if marker.exists() or marker.with_name("commit.marker.partial").exists():
            raise HostFileTransactionError("transaction_conflict")
        allowed = {
            OWNER_FILE_NAME,
            "manifest.json",
            "manifest.json.partial",
        }
        for item in files:
            allowed.update(
                {
                    f"after-{item.key}",
                    f"after-{item.key}.partial",
                    f"after-id-{item.key}",
                    f"after-id-{item.key}.partial",
                }
            )
        for entry in tuple(self.rollback_dir.iterdir()):
            if entry.name not in allowed or _is_link_or_reparse(entry):
                raise HostFileTransactionError("transaction_conflict")
        for item in files:
            stage = self.rollback_dir / f"after-{item.key}"
            identity = self.rollback_dir / f"after-id-{item.key}"
            if stage.exists():
                self._assert_exact(stage, item.after)
                if item.after is None:
                    raise HostFileTransactionError("transaction_conflict")
                if identity.exists() and _file_identity(stage) != _read_file_identity(identity):
                    raise HostFileTransactionError("transaction_conflict")
            if identity.exists():
                _read_file_identity(identity)
        return manifest

    def _cleanup_rollback_locked(self, files: Sequence[FileMutation]) -> None:
        self.tx_dir = self.rollback_dir
        directory_identity = file_identity(self.rollback_dir)
        manifest = self._validate_rollback_transaction(files)
        for item in files:
            stage = self.rollback_dir / f"after-{item.key}"
            if stage.exists():
                _remove_exact(stage, item.after)
            self._remove_partial(stage)
            identity = self.rollback_dir / f"after-id-{item.key}"
            if identity.exists():
                _remove_file_identity_artifact(identity)
            self._remove_partial(identity)
        manifest_path = self.rollback_dir / "manifest.json"
        if manifest_path.exists():
            _remove_exact(manifest_path, manifest)
        self._remove_partial(manifest_path)
        owner_path = self.rollback_dir / OWNER_FILE_NAME
        if owner_path.exists():
            _remove_exact(owner_path, _owner_marker(manifest))
        _remove_empty_directory_exact(self.rollback_dir, directory_identity)
        _fsync_directory(self.tx_root)

    def _cleanup_incomplete(self, files: Sequence[FileMutation]) -> None:
        directory_identity = file_identity(self.tx_dir)
        for item in files:
            for prefix, expected in (("after", item.after),):
                artifact = self.tx_dir / f"{prefix}-{item.key}"
                if artifact.exists():
                    _remove_exact(artifact, expected)
        manifest = self.tx_dir / "manifest.json"
        if manifest.exists():
            _remove_exact(manifest, self._manifest(files))
        _remove_empty_directory_exact(self.tx_dir, directory_identity)

    def _select_transaction_dir(self) -> None:
        active = self.active_dir.exists()
        sealed = self.cleanup_dir.exists()
        rolled_back = self.rollback_dir.exists()
        if sum((active, sealed, rolled_back)) > 1:
            raise HostFileTransactionError("transaction_conflict")
        self.tx_dir = (
            self.cleanup_dir
            if sealed
            else self.rollback_dir
            if rolled_back
            else self.active_dir
        )

    def _seal_transaction(self, files: Sequence[FileMutation]) -> None:
        """Atomically publish durable completion before releasing Git locks."""
        self.tx_dir = self.active_dir
        manifest = self._validate_transaction(files)
        marker = self.active_dir / "commit.marker"
        self._assert_exact(marker, _commit_marker(manifest))
        self._assert_committed_artifacts(files)
        if self._classify(files) != "after":
            raise HostFileTransactionError("transaction_conflict")
        if self.cleanup_dir.exists():
            raise HostFileTransactionError("transaction_conflict")
        move_directory_no_replace(
            self.active_dir,
            self.cleanup_dir,
            expected_identity=file_identity(self.active_dir),
            owner_name=OWNER_FILE_NAME,
            owner_content=_owner_marker(manifest),
        )
        _fsync_directory(self.tx_root)
        self.tx_dir = self.cleanup_dir

    def _validate_sealed_transaction(self, files: Sequence[FileMutation]) -> bytes:
        self.tx_dir = self.cleanup_dir
        manifest = self._manifest(files)
        owner_path = self.cleanup_dir / OWNER_FILE_NAME
        if owner_path.exists():
            self._assert_exact(owner_path, _owner_marker(manifest))
        elif tuple(self.cleanup_dir.iterdir()):
            raise HostFileTransactionError("transaction_conflict")
        manifest_path = self.cleanup_dir / "manifest.json"
        if manifest_path.exists():
            self._assert_exact(manifest_path, manifest)
        marker = self.cleanup_dir / "commit.marker"
        if marker.exists():
            self._assert_exact(marker, _commit_marker(manifest))
        # A sealed name is created only after the callback and final stamp.
        # During idempotent cleanup either metadata file may already be gone.
        if not manifest_path.exists() and not marker.exists():
            allowed = {
                f"before-{item.key}" for item in files if item.before is not None
            }
            allowed.update(
                f"after-id-{item.key}" for item in files if item.after is not None
            )
            allowed.update(
                f"{name}.partial"
                for name in ("manifest.json", "commit.marker")
            )
            if any(entry.name not in allowed for entry in self.cleanup_dir.iterdir()):
                raise HostFileTransactionError("transaction_conflict")
        return manifest

    def _cleanup_sealed_locked(self, files: Sequence[FileMutation]) -> None:
        self.tx_dir = self.cleanup_dir
        directory_identity = file_identity(self.cleanup_dir)
        manifest = self._validate_sealed_transaction(files)
        allowed: set[str] = {
            OWNER_FILE_NAME,
            "manifest.json",
            "manifest.json.partial",
            "commit.marker",
            "commit.marker.partial",
        }
        for item in files:
            allowed.update(
                {
                    f"before-{item.key}",
                    f"after-{item.key}",
                    f"after-{item.key}.partial",
                    f"after-id-{item.key}",
                    f"after-id-{item.key}.partial",
                }
            )
        for entry in tuple(self.cleanup_dir.iterdir()):
            if entry.name not in allowed or _is_link_or_reparse(entry):
                raise HostFileTransactionError("transaction_conflict")
        for item in files:
            backup = self.cleanup_dir / f"before-{item.key}"
            stage = self.cleanup_dir / f"after-{item.key}"
            if backup.exists():
                _remove_exact(backup, item.before)
            if stage.exists():
                _remove_exact(stage, item.after)
            self._remove_partial(stage)
            identity = self.cleanup_dir / f"after-id-{item.key}"
            if identity.exists():
                if item.after is not None and _file_identity(
                    self.root / PurePosixPath(item.path)
                ) != _read_file_identity(identity):
                    raise HostFileTransactionError("transaction_conflict")
                _remove_file_identity_artifact(identity)
            self._remove_partial(identity)
        marker = self.cleanup_dir / "commit.marker"
        if marker.exists():
            _remove_exact(marker, _commit_marker(manifest))
        self._remove_partial(marker)
        manifest_path = self.cleanup_dir / "manifest.json"
        if manifest_path.exists():
            _remove_exact(manifest_path, manifest)
        self._remove_partial(manifest_path)
        owner_path = self.cleanup_dir / OWNER_FILE_NAME
        if owner_path.exists():
            _remove_exact(owner_path, _owner_marker(manifest))
        _remove_empty_directory_exact(self.cleanup_dir, directory_identity)
        _fsync_directory(self.tx_root)

    @staticmethod
    def _remove_partial(final: Path) -> None:
        partial = final.with_name(f"{final.name}.partial")
        if partial.exists():
            _remove_protected_partial(partial)

    def _manifest(self, files: Sequence[FileMutation]) -> bytes:
        payload = {
            "version": 1,
            "operation_id": self.operation_id,
            "action": self.action,
            "branch": self.expected_stamp[0],
            "head": self.expected_stamp[1],
            "index_sha256": self.expected_stamp[2],
            "created_directories": list(self.created_directories),
            "files": [
                {
                    "path": item.path,
                    "before_sha256": _sha256_or_none(item.before),
                    "after_sha256": _sha256_or_none(item.after),
                    "mode": item.mode,
                    "key": item.key,
                }
                for item in files
            ],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _classify(self, files: Sequence[FileMutation]) -> str:
        states: set[str] = set()
        for item in files:
            current = _read_regular(self.root / PurePosixPath(item.path))
            if current == item.before:
                states.add("before")
            elif current == item.after:
                states.add("after")
            else:
                states.add("other")
        if states == {"before"}:
            return "before"
        if states == {"after"}:
            return "after"
        if states <= {"before", "after"}:
            return "mixed"
        return "other"

    def _assert_stamp(self) -> None:
        if self.stamp_callback(self.operation_id) != self.expected_stamp:
            raise HostFileTransactionError("git_state_changed")

    @contextlib.contextmanager
    def _git_locks(self) -> Iterator[None]:
        created: list[Path] = []
        paths = self._git_lock_paths()
        parents = [self.git]
        for path in paths:
            parents.extend(_ensure_safe_directory_chain(self.git, path.parent))
        try:
            with _guard_directories(tuple(dict.fromkeys(parents))):
                for path in paths:
                    if path.exists():
                        # The helper holds its outer per-project process lock
                        # before entering this module. Under that prerequisite,
                        # an exact marker from this operation is stale crash
                        # debris; a foreign marker is never removed.
                        self._assert_exact(path, self._marker)
                        _remove_exact(path, self._marker)
                    try:
                        _write_exclusive(path, self._marker, 0o600)
                        created.append(path)
                    except FileExistsError as exc:
                        raise HostFileTransactionError("git_locked") from exc
                yield
        finally:
            cleanup_error: BaseException | None = None
            for path in reversed(created):
                try:
                    _remove_exact(path, self._marker)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                raise cleanup_error

    def _git_lock_paths(self) -> tuple[Path, ...]:
        branch = PurePosixPath(self.expected_stamp[0])
        if any(part in {"", ".", ".."} for part in branch.parts):
            raise HostFileTransactionError("branch_invalid")
        return (
            self.git / "index.lock",
            self.git / "HEAD.lock",
            self.git / "refs" / "heads" / branch.with_name(f"{branch.name}.lock"),
        )

    @staticmethod
    def _assert_exact(path: Path, expected: bytes | None) -> None:
        if expected is None or _read_regular(path) != expected:
            raise HostFileTransactionError("transaction_conflict")


def read_regular(path: Path) -> bytes | None:
    """Read one regular file through the same no-follow handle used for checks."""
    result = _read_regular_with_identity(Path(path))
    return None if result is None else result[0]


def _read_regular_with_identity(path: Path) -> tuple[bytes, str] | None:
    candidate = Path(path)
    if os.name == "nt":
        handle = _windows_open_existing(
            candidate,
            access=0x80000000 | 0x00000080,  # GENERIC_READ | FILE_READ_ATTRIBUTES
            share=0x00000001 | 0x00000002 | 0x00000004,
            allow_missing=True,
        )
        if handle is None:
            return None
        try:
            identity = _windows_handle_identity(handle, require_directory=False)
            return _windows_read_all(handle), identity
        finally:
            _windows_close_handle(handle)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(candidate, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise HostFileTransactionError("path_unavailable") from exc
    try:
        identity = _descriptor_identity(descriptor, require_directory=False)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks), identity
    except OSError as exc:
        raise HostFileTransactionError("path_unavailable") from exc
    finally:
        os.close(descriptor)


def _read_regular(path: Path) -> bytes | None:
    return read_regular(path)


def file_identity(path: Path) -> str:
    """Return a stable, path-free identity for a real file or directory."""
    candidate = Path(path)
    if os.name == "nt":
        handle = _windows_open_existing(
            candidate,
            access=0x00000080,  # FILE_READ_ATTRIBUTES
            share=0x00000001 | 0x00000002 | 0x00000004,
            allow_missing=False,
            directory=True,
        )
        assert handle is not None
        try:
            return _windows_handle_identity(handle, require_directory=None)
        finally:
            _windows_close_handle(handle)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise HostFileTransactionError("path_unavailable") from exc
    try:
        return _descriptor_identity(descriptor, require_directory=None)
    finally:
        os.close(descriptor)


def _descriptor_identity(
    descriptor: int,
    *,
    require_directory: bool | None,
) -> str:
    if os.name == "nt":
        import msvcrt

        return _windows_handle_identity(
            msvcrt.get_osfhandle(descriptor),
            require_directory=require_directory,
        )
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise HostFileTransactionError("path_unavailable") from exc
    is_directory = stat.S_ISDIR(metadata.st_mode)
    if (
        not (stat.S_ISREG(metadata.st_mode) or is_directory)
        or require_directory is not None
        and is_directory != require_directory
        or metadata.st_dev <= 0
        or metadata.st_ino <= 0
    ):
        raise HostFileTransactionError("path_unsafe")
    return f"{metadata.st_dev:x}-{metadata.st_ino:x}"


def _validate_expected_identity(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]+-[a-f0-9]+", value) is None:
        raise HostFileTransactionError("transaction_parameters_invalid")
    return value


@contextlib.contextmanager
def _guard_exact_regular_object(
    path: Path,
    expected: bytes,
    *,
    expected_identity: str | None = None,
    allow_delete_share: bool = False,
) -> Iterator[str]:
    identity = _validate_expected_identity(expected_identity)
    if os.name == "nt":
        share = 0x00000001 | (0x00000004 if allow_delete_share else 0)
        handle = _windows_open_existing(
            path,
            access=0x80000000 | 0x00000080,
            share=share,
            allow_missing=False,
        )
        assert handle is not None
        try:
            current_identity = _windows_handle_identity(
                handle,
                require_directory=False,
            )
            if (
                identity is not None
                and current_identity != identity
                or _windows_read_all(handle) != expected
            ):
                raise HostFileTransactionError("transaction_conflict")
            yield current_identity
            if (
                _windows_handle_identity(handle, require_directory=False)
                != current_identity
                or _windows_read_all(handle) != expected
            ):
                raise HostFileTransactionError("transaction_conflict")
        finally:
            _windows_close_handle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HostFileTransactionError("transaction_conflict") from exc
    try:
        current_identity = _descriptor_identity(
            descriptor,
            require_directory=False,
        )
        if (
            identity is not None
            and current_identity != identity
            or _read_descriptor(descriptor) != expected
        ):
            raise HostFileTransactionError("transaction_conflict")
        yield current_identity
        if (
            _descriptor_identity(descriptor, require_directory=False)
            != current_identity
            or _read_descriptor(descriptor) != expected
        ):
            raise HostFileTransactionError("transaction_conflict")
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _guard_published_targets(
    root: Path,
    transaction_dir: Path,
    files: Sequence[FileMutation],
) -> Iterator[None]:
    windows_handles: list[tuple[int, bytes, str]] = []
    posix_descriptors: list[tuple[int, Path, bytes, str]] = []
    try:
        for item in files:
            if item.after is None:
                continue
            target = root / PurePosixPath(item.path)
            identity_path = transaction_dir / f"after-id-{item.key}"
            expected_identity = _read_file_identity(identity_path).decode("ascii").strip()
            if os.name == "nt":
                handle = _windows_open_existing(
                    target,
                    access=0x80000000 | 0x00000080,
                    # Deny all future writers and deleters until callback+seal.
                    share=0x00000001,
                    allow_missing=False,
                )
                assert handle is not None
                try:
                    if (
                        _windows_handle_identity(handle, require_directory=False)
                        != expected_identity
                        or _windows_read_all(handle) != item.after
                    ):
                        raise HostFileTransactionError("transaction_conflict")
                except BaseException:
                    _windows_close_handle(handle)
                    raise
                windows_handles.append((handle, item.after, expected_identity))
                continue
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(target, flags)
            except OSError as exc:
                raise HostFileTransactionError("transaction_conflict") from exc
            try:
                metadata = os.fstat(descriptor)
                identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"
                if not stat.S_ISREG(metadata.st_mode) or identity != expected_identity:
                    raise HostFileTransactionError("transaction_conflict")
                content = _read_descriptor(descriptor)
                if content != item.after:
                    raise HostFileTransactionError("transaction_conflict")
            except BaseException:
                os.close(descriptor)
                raise
            posix_descriptors.append((descriptor, target, item.after, expected_identity))
        yield
        for handle, expected, identity in windows_handles:
            if (
                _windows_handle_identity(handle, require_directory=False) != identity
                or _windows_read_all(handle) != expected
            ):
                raise HostFileTransactionError("transaction_conflict")
        for descriptor, target, expected, identity in posix_descriptors:
            metadata = os.fstat(descriptor)
            if (
                f"{metadata.st_dev:x}-{metadata.st_ino:x}" != identity
                or _read_descriptor(descriptor) != expected
                or file_identity(target) != identity
            ):
                raise HostFileTransactionError("transaction_conflict")
    finally:
        for descriptor, _target, _expected, _identity in reversed(posix_descriptors):
            os.close(descriptor)
        for handle, _expected, _identity in reversed(windows_handles):
            _windows_close_handle(handle)


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _write_exclusive(path: Path, content: bytes, mode: int) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0),
        mode,
    )
    created_identity = _descriptor_identity(descriptor, require_directory=False)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        # The write may have stopped at any byte.  Never unlink by path after
        # releasing the descriptor: an external same-name file could already
        # have replaced it.  Exact object identity makes best-effort cleanup
        # safe; otherwise the partial remains as fail-closed crash evidence.
        with contextlib.suppress(HostFileTransactionError):
            partial = read_regular(path)
            if partial is not None:
                remove_regular_exact(
                    path,
                    partial,
                    expected_identity=created_identity,
                )
        raise


def _write_durable_no_replace(path: Path, content: bytes, mode: int) -> None:
    """Publish a small transaction artifact through a recoverable partial."""
    with _guard_directories((path.parent,)):
        partial = path.with_name(f"{path.name}.partial")
        if path.exists():
            if _read_regular(path) != content:
                raise HostFileTransactionError("transaction_conflict")
            if partial.exists():
                _remove_protected_partial(partial)
            return
        if partial.exists():
            # The partial is inside a validated transaction directory and is never
            # used as evidence. A hard crash may leave it empty or truncated.
            _remove_protected_partial(partial)
        _write_exclusive(partial, content, mode)
        _fsync_directory(path.parent)
        try:
            _move_verified_no_replace(partial, path, content)
        except BaseException:
            if path.exists() and _read_regular(path) == content:
                if partial.exists():
                    _remove_protected_partial(partial)
                return
            raise
        _fsync_directory(path.parent)


def _remove_protected_partial(path: Path) -> None:
    if not path.name.endswith(".partial"):
        raise HostFileTransactionError("transaction_cleanup_failed")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise HostFileTransactionError("transaction_cleanup_failed") from exc
    if not stat.S_ISREG(metadata.st_mode) or _is_reparse(metadata):
        raise HostFileTransactionError("transaction_conflict")
    current = _read_regular_with_identity(path)
    if current is None:
        return
    content, identity = current
    _remove_exact(path, content, expected_identity=identity)
    _fsync_directory(path.parent)


def remove_regular_exact(
    path: Path,
    expected: bytes,
    expected_identity: str | None = None,
) -> None:
    """Delete the exact regular file object without following replacements."""
    _remove_exact(
        Path(path),
        expected,
        expected_identity=expected_identity,
    )


def _remove_exact(
    path: Path,
    expected: bytes | None,
    *,
    expected_identity: str | None = None,
) -> None:
    if expected is None:
        raise HostFileTransactionError("transaction_conflict")
    identity = _validate_expected_identity(expected_identity)
    if os.name == "nt":
        _windows_delete_regular_exact(
            path,
            expected,
            expected_identity=identity,
        )
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent = os.open(path.parent, flags)
    except OSError as exc:
        raise HostFileTransactionError("transaction_cleanup_failed") from exc
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
        except OSError as exc:
            raise HostFileTransactionError("transaction_conflict") from exc
        current_identity = _descriptor_identity(
            descriptor,
            require_directory=False,
        )
        if (
            identity is not None
            and current_identity != identity
            or _read_descriptor(descriptor) != expected
        ):
            raise HostFileTransactionError("transaction_conflict")
        try:
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise HostFileTransactionError("transaction_conflict") from exc
        named_identity = f"{named.st_dev:x}-{named.st_ino:x}"
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_dev <= 0
            or named.st_ino <= 0
            or named_identity != current_identity
        ):
            raise HostFileTransactionError("transaction_conflict")
        try:
            os.unlink(path.name, dir_fd=parent)
            os.fsync(parent)
        except OSError as exc:
            raise HostFileTransactionError("transaction_cleanup_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _remove_empty_directory_exact(path: Path, expected_identity: str) -> None:
    identity = _validate_expected_identity(expected_identity)
    assert identity is not None
    if os.name == "nt":
        _windows_delete_empty_directory_exact(path, identity)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent = os.open(path.parent, flags)
    except OSError as exc:
        raise HostFileTransactionError("transaction_cleanup_failed") from exc
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(path.name, flags, dir_fd=parent)
        except OSError as exc:
            raise HostFileTransactionError("transaction_conflict") from exc
        current_identity = _descriptor_identity(
            descriptor,
            require_directory=True,
        )
        if current_identity != identity:
            raise HostFileTransactionError("transaction_conflict")
        try:
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise HostFileTransactionError("transaction_conflict") from exc
        if (
            not stat.S_ISDIR(named.st_mode)
            or named.st_dev <= 0
            or named.st_ino <= 0
            or f"{named.st_dev:x}-{named.st_ino:x}" != identity
        ):
            raise HostFileTransactionError("transaction_conflict")
        try:
            os.rmdir(path.name, dir_fd=parent)
            os.fsync(parent)
        except OSError as exc:
            raise HostFileTransactionError("transaction_cleanup_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _windows_open_existing(
    path: Path,
    *,
    access: int,
    share: int,
    allow_missing: bool,
    directory: bool = False,
) -> int | None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    handle = kernel32.CreateFileW(
        str(path),
        access,
        share,
        None,
        3,  # OPEN_EXISTING
        flags,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle is None or int(handle) == invalid:
        error = ctypes.get_last_error()
        if allow_missing and error in {2, 3}:
            return None
        raise HostFileTransactionError(
            "path_unavailable" if allow_missing else "transaction_conflict"
        )
    return int(handle)


def _windows_close_handle(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


def _windows_handle_identity(
    handle: int,
    *,
    require_directory: bool | None,
) -> str:
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("access_time", wintypes.FILETIME),
            ("write_time", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    information = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        raise HostFileTransactionError("path_unavailable")
    is_directory = bool(information.attributes & 0x00000010)
    file_index = (information.file_index_high << 32) | information.file_index_low
    if (
        information.attributes & FILE_ATTRIBUTE_REPARSE_POINT
        or require_directory is not None
        and is_directory != require_directory
        or information.volume_serial == 0
        or file_index == 0
    ):
        raise HostFileTransactionError("path_unsafe")
    return f"{information.volume_serial:x}-{file_index:x}"


def _windows_read_all(handle: int) -> bytes:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileSizeEx.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_longlong),
    ]
    kernel32.GetFileSizeEx.restype = wintypes.BOOL
    kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    size = ctypes.c_longlong()
    if not kernel32.GetFileSizeEx(handle, ctypes.byref(size)) or size.value < 0:
        raise HostFileTransactionError("path_unavailable")
    if not kernel32.SetFilePointerEx(handle, 0, None, 0):
        raise HostFileTransactionError("path_unavailable")
    remaining = size.value
    chunks: list[bytes] = []
    while remaining:
        count = min(remaining, 64 * 1024)
        buffer = (ctypes.c_ubyte * count)()
        read = wintypes.DWORD()
        if not kernel32.ReadFile(
            handle,
            buffer,
            count,
            ctypes.byref(read),
            None,
        ):
            raise HostFileTransactionError("path_unavailable")
        if read.value == 0:
            raise HostFileTransactionError("path_unavailable")
        chunks.append(bytes(buffer[: read.value]))
        remaining -= read.value
    return b"".join(chunks)


def _windows_delete_regular_exact(
    path: Path,
    expected: bytes,
    *,
    expected_identity: str | None,
) -> None:
    from ctypes import wintypes

    generic_read = 0x80000000
    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    file_disposition_info = 4
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    handle = _windows_open_existing(
        path,
        access=generic_read | delete_access | file_read_attributes,
        share=file_share_read,
        allow_missing=False,
    )
    assert handle is not None
    try:
        identity = _windows_handle_identity(handle, require_directory=False)
        if expected_identity is not None and identity != expected_identity:
            raise HostFileTransactionError("transaction_conflict")
        if _windows_read_all(handle) != expected:
            raise HostFileTransactionError("transaction_conflict")
        disposition = _FileDispositionInfo(True)
        if not kernel32.SetFileInformationByHandle(
            handle,
            file_disposition_info,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise HostFileTransactionError("transaction_cleanup_failed")
    finally:
        _windows_close_handle(handle)
    if path.exists():
        raise HostFileTransactionError("transaction_cleanup_failed")


def _windows_delete_empty_directory_exact(
    path: Path,
    expected_identity: str,
) -> None:
    from ctypes import wintypes

    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    file_disposition_info = 4
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    handle = _windows_open_existing(
        path,
        access=delete_access | file_read_attributes,
        share=file_share_read,
        allow_missing=False,
        directory=True,
    )
    assert handle is not None
    try:
        if (
            _windows_handle_identity(handle, require_directory=True)
            != expected_identity
        ):
            raise HostFileTransactionError("transaction_conflict")
        disposition = _FileDispositionInfo(True)
        if not kernel32.SetFileInformationByHandle(
            handle,
            file_disposition_info,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise HostFileTransactionError("transaction_cleanup_failed")
    finally:
        _windows_close_handle(handle)
    if path.exists():
        raise HostFileTransactionError("transaction_cleanup_failed")


def _move_verified_no_replace(
    source: Path,
    destination: Path,
    expected: bytes,
    *,
    expected_identity: str | None = None,
) -> None:
    expected_identity = _validate_expected_identity(expected_identity)
    with _guard_directories((source.parent, destination.parent)):
        if os.name == "nt":
            _windows_move_verified_no_replace(
                source,
                destination,
                expected,
                expected_identity=expected_identity,
            )
            return
        if _read_regular(source) != expected:
            raise HostFileTransactionError("target_changed")
        _posix_rename_no_replace(
            source,
            destination,
            expected_identity=expected_identity,
        )
        if _read_regular(destination) != expected:
            if not source.exists():
                with contextlib.suppress(HostFileTransactionError):
                    _posix_rename_no_replace(destination, source)
            raise HostFileTransactionError("target_changed")
    _fsync_directory(source.parent)
    if destination.parent != source.parent:
        _fsync_directory(destination.parent)


def move_directory_no_replace(
    source: Path,
    destination: Path,
    *,
    expected_identity: str | None = None,
    owner_name: str | None = None,
    owner_content: bytes | None = None,
    owner_only: bool = False,
) -> None:
    """Atomically move one real directory without replacing any destination."""
    _move_directory_no_replace(
        Path(source),
        Path(destination),
        expected_identity=expected_identity,
        owner_name=owner_name,
        owner_content=owner_content,
        owner_only=owner_only,
    )


def _move_directory_no_replace(
    source: Path,
    destination: Path,
    *,
    expected_identity: str | None = None,
    owner_name: str | None = None,
    owner_content: bytes | None = None,
    owner_only: bool = False,
) -> None:
    _assert_directory(source)
    if destination.exists() or _is_link_or_reparse(destination):
        raise HostFileTransactionError("transaction_conflict")
    identity = _validate_expected_identity(expected_identity) or file_identity(source)
    if (owner_name is None) != (owner_content is None):
        raise HostFileTransactionError("transaction_parameters_invalid")
    if owner_only and owner_name is None:
        raise HostFileTransactionError("transaction_parameters_invalid")
    if owner_name is not None and (
        not isinstance(owner_content, bytes)
        or owner_name in {"", ".", ".."}
        or Path(owner_name).name != owner_name
        or "/" in owner_name
        or "\\" in owner_name
    ):
        raise HostFileTransactionError("transaction_parameters_invalid")
    if os.name == "nt":
        with _guard_directories((source.parent, destination.parent)):
            _windows_move_directory_no_replace(
                source,
                destination,
                expected_identity=identity,
                owner_name=owner_name,
                owner_content=owner_content,
                owner_only=owner_only,
            )
    else:
        with contextlib.ExitStack() as ownership:
            owner_identity: str | None = None
            if owner_name is not None:
                assert owner_content is not None
                owner_identity = ownership.enter_context(
                    _guard_exact_regular_object(
                        source / owner_name,
                        owner_content,
                        allow_delete_share=True,
                    )
                )
                if owner_only and {
                    entry.name for entry in source.iterdir()
                } != {owner_name}:
                    raise HostFileTransactionError("transaction_conflict")
            with _guard_directories((source.parent, destination.parent)):
                _posix_rename_no_replace(
                    source,
                    destination,
                    expected_identity=identity,
                )
            if owner_name is not None:
                assert owner_content is not None and owner_identity is not None
                moved_owner = destination / owner_name
                if (
                    file_identity(moved_owner) != owner_identity
                    or read_regular(moved_owner) != owner_content
                ):
                    raise HostFileTransactionError("transaction_conflict")
    if file_identity(destination) != identity:
        raise HostFileTransactionError("transaction_conflict")
    _fsync_directory(destination.parent)


def _posix_rename_no_replace(
    source: Path,
    destination: Path,
    *,
    expected_identity: str | None = None,
) -> None:
    import errno

    library = ctypes.CDLL(None, use_errno=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_parent = os.open(source.parent, flags)
    destination_parent = os.open(destination.parent, flags)
    try:
        before = os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise HostFileTransactionError("path_unsafe")
        before_identity = f"{before.st_dev:x}-{before.st_ino:x}"
        if (
            before.st_dev <= 0
            or before.st_ino <= 0
            or expected_identity is not None
            and before_identity != expected_identity
        ):
            raise HostFileTransactionError("target_changed")
        if hasattr(library, "renameat2"):
            library.renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            library.renameat2.restype = ctypes.c_int
            result = library.renameat2(
                source_parent,
                os.fsencode(source.name),
                destination_parent,
                os.fsencode(destination.name),
                1,  # RENAME_NOREPLACE
            )
        elif hasattr(library, "renameatx_np"):
            library.renameatx_np.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            library.renameatx_np.restype = ctypes.c_int
            result = library.renameatx_np(
                source_parent,
                os.fsencode(source.name),
                destination_parent,
                os.fsencode(destination.name),
                4,  # RENAME_EXCL
            )
        else:
            raise HostFileTransactionError("atomic_rename_unsupported")
        if result != 0:
            error = ctypes.get_errno()
            if error in {errno.EEXIST, errno.ENOTEMPTY, errno.ENOENT}:
                raise HostFileTransactionError("target_changed")
            if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
                raise HostFileTransactionError("atomic_rename_unsupported")
            raise HostFileTransactionError("target_unavailable")
        after = os.stat(
            destination.name,
            dir_fd=destination_parent,
            follow_symlinks=False,
        )
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise HostFileTransactionError("path_parent_changed")
        os.fsync(source_parent)
        if source.parent != destination.parent:
            os.fsync(destination_parent)
    finally:
        os.close(destination_parent)
        os.close(source_parent)


def _windows_move_verified_no_replace(
    source: Path,
    destination: Path,
    expected: bytes,
    *,
    expected_identity: str | None = None,
) -> None:
    _windows_rename_handle_no_replace(
        source,
        destination,
        expected=expected,
        directory=False,
        expected_identity=expected_identity,
    )
    if _read_regular(destination) != expected:
        raise HostFileTransactionError("target_changed")


def _windows_move_directory_no_replace(
    source: Path,
    destination: Path,
    *,
    expected_identity: str | None = None,
    owner_name: str | None = None,
    owner_content: bytes | None = None,
    owner_only: bool = False,
) -> None:
    _windows_rename_handle_no_replace(
        source,
        destination,
        expected=None,
        directory=True,
        expected_identity=expected_identity,
        owner_name=owner_name,
        owner_content=owner_content,
        owner_only=owner_only,
    )


def _windows_rename_handle_no_replace(
    source: Path,
    destination: Path,
    *,
    expected: bytes | None,
    directory: bool,
    expected_identity: str | None = None,
    owner_name: str | None = None,
    owner_content: bytes | None = None,
    owner_only: bool = False,
) -> None:
    from ctypes import wintypes

    generic_read = 0x80000000
    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    file_rename_info = 3
    file_attribute_directory = 0x00000010
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("access_time", wintypes.FILETIME),
            ("write_time", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    kernel32.GetFileSizeEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(source),
        (generic_read if not directory else 0)
        | delete_access
        | file_read_attributes,
        file_share_read,
        None,
        open_existing,
        file_flag_open_reparse_point
        | (file_flag_backup_semantics if directory else 0),
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if int(handle) == invalid:
        raise HostFileTransactionError("target_changed")
    try:
        information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(information),
        ):
            raise HostFileTransactionError("target_changed")
        if information.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise HostFileTransactionError("path_unsafe")
        if bool(information.attributes & file_attribute_directory) != directory:
            raise HostFileTransactionError("path_unsafe")
        identity = (
            information.volume_serial,
            (information.file_index_high << 32) | information.file_index_low,
        )
        identity_value = f"{identity[0]:x}-{identity[1]:x}"
        if (
            identity[0] == 0
            or identity[1] == 0
            or expected_identity is not None
            and identity_value != expected_identity
        ):
            raise HostFileTransactionError("target_changed")
        owner_identity: str | None = None
        if owner_name is not None:
            if not directory or owner_content is None:
                raise HostFileTransactionError("transaction_parameters_invalid")
            # Keep the source directory handle open while validating its owner,
            # then close the child handle before the parent rename.  Windows
            # rejects a directory rename while a child handle is held; the
            # before/after identity checks still bind the moved directory to
            # the exact owner object inspected under this source handle.
            with _guard_exact_regular_object(
                source / owner_name,
                owner_content,
                allow_delete_share=True,
            ) as guarded_owner_identity:
                owner_identity = guarded_owner_identity
            if owner_only and {
                entry.name for entry in source.iterdir()
            } != {owner_name}:
                raise HostFileTransactionError("transaction_conflict")
        if not directory:
            size = ctypes.c_longlong()
            if not kernel32.GetFileSizeEx(handle, ctypes.byref(size)) or size.value < 0:
                raise HostFileTransactionError("target_changed")
            buffer = (ctypes.c_ubyte * size.value)()
            read = wintypes.DWORD()
            if size.value and not kernel32.ReadFile(
                handle,
                buffer,
                size.value,
                ctypes.byref(read),
                None,
            ):
                raise HostFileTransactionError("target_changed")
            content = bytes(buffer[: read.value]) if size.value else b""
            if content != expected or read.value != size.value:
                raise HostFileTransactionError("target_changed")
        name = str(destination)

        class _FileRenameInfo(ctypes.Structure):
            _fields_ = [
                ("ReplaceIfExists", wintypes.BOOLEAN),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", ctypes.c_wchar * (len(name) + 1)),
            ]

        info = _FileRenameInfo()
        info.ReplaceIfExists = False
        info.RootDirectory = None
        info.FileNameLength = len(name.encode("utf-16-le"))
        info.FileName = name
        if not kernel32.SetFileInformationByHandle(
            handle,
            file_rename_info,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise HostFileTransactionError("target_changed")
        moved_information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(moved_information),
        ):
            raise HostFileTransactionError("target_changed")
        moved_identity = (
            moved_information.volume_serial,
            (moved_information.file_index_high << 32)
            | moved_information.file_index_low,
        )
        if moved_identity != identity or moved_information.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise HostFileTransactionError("path_parent_changed")
        if owner_name is not None:
            assert owner_content is not None and owner_identity is not None
            moved_owner = destination / owner_name
            if (
                file_identity(moved_owner) != owner_identity
                or read_regular(moved_owner) != owner_content
            ):
                raise HostFileTransactionError("transaction_conflict")
    finally:
        kernel32.CloseHandle(handle)
    try:
        destination_metadata = destination.lstat()
    except OSError as exc:
        raise HostFileTransactionError("target_changed") from exc
    if _is_reparse(destination_metadata):
        raise HostFileTransactionError("path_unsafe")
    # CPython exposes the Windows file index as st_ino. Recheck when available;
    # zero-valued legacy filesystems remain protected by the still-open handle.
    if destination_metadata.st_ino and destination_metadata.st_ino != identity[1]:
        raise HostFileTransactionError("path_parent_changed")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _commit_marker(manifest: bytes) -> bytes:
    return f"committed:{hashlib.sha256(manifest).hexdigest()}\n".encode("ascii")


def _owner_marker(manifest: bytes) -> bytes:
    return f"owned:{hashlib.sha256(manifest).hexdigest()}\n".encode("ascii")


def _file_identity(path: Path) -> bytes:
    current = _read_regular_with_identity(path)
    if current is None:
        raise HostFileTransactionError("path_unsafe")
    _content, identity = current
    return f"{identity}\n".encode("ascii")


def _read_file_identity(path: Path) -> bytes:
    value = _read_regular(path)
    if value is None or re.fullmatch(rb"[a-f0-9]+-[a-f0-9]+\n", value) is None:
        raise HostFileTransactionError("transaction_conflict")
    return value


def _remove_file_identity_artifact(path: Path) -> None:
    current = _read_regular_with_identity(path)
    if current is None:
        raise HostFileTransactionError("transaction_conflict")
    content, object_identity = current
    if re.fullmatch(rb"[a-f0-9]+-[a-f0-9]+\n", content) is None:
        raise HostFileTransactionError("transaction_conflict")
    _remove_exact(
        path,
        content,
        expected_identity=object_identity,
    )


def _sha256_or_none(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _assert_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise HostFileTransactionError("transaction_unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse(metadata):
        raise HostFileTransactionError("transaction_conflict")


def _ensure_safe_directory_chain(root: Path, parent: Path) -> tuple[Path, ...]:
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise HostFileTransactionError("git_metadata_unsafe") from exc
    current = root
    values = [root]
    for part in relative.parts:
        current = current / part
        if not current.exists():
            try:
                current.mkdir()
            except FileExistsError:
                pass
        _assert_directory(current)
        values.append(current)
    return tuple(values)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise HostFileTransactionError("path_unavailable") from exc
    return stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


@contextlib.contextmanager
def _guard_directories(paths: Sequence[Path]) -> Iterator[None]:
    unique = tuple(dict.fromkeys(Path(path) for path in paths))
    for path in unique:
        _assert_directory(path)
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        file_read_attributes = 0x0080
        file_share_read = 0x00000001
        open_existing = 3
        flags = 0x00200000 | 0x02000000
        file_attribute_tag_info = 9

        class _FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("reparse_tag", wintypes.DWORD),
            ]

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("creation_time", wintypes.FILETIME),
                ("access_time", wintypes.FILETIME),
                ("write_time", wintypes.FILETIME),
                ("volume_serial", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("file_index_high", wintypes.DWORD),
                ("file_index_low", wintypes.DWORD),
            ]

        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        handles: list[int] = []
        identities: list[tuple[Path, int, int]] = []
        try:
            for path in unique:
                handle = kernel32.CreateFileW(
                    str(path),
                    file_read_attributes,
                    file_share_read,
                    None,
                    open_existing,
                    flags,
                    None,
                )
                if int(handle) == ctypes.c_void_p(-1).value:
                    raise HostFileTransactionError("path_parent_unavailable")
                info = _FileAttributeTagInfo()
                if not kernel32.GetFileInformationByHandleEx(
                    handle,
                    file_attribute_tag_info,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                ) or info.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    kernel32.CloseHandle(handle)
                    raise HostFileTransactionError("path_parent_changed")
                identity = _ByHandleFileInformation()
                if not kernel32.GetFileInformationByHandle(
                    handle,
                    ctypes.byref(identity),
                ):
                    kernel32.CloseHandle(handle)
                    raise HostFileTransactionError("path_parent_changed")
                handles.append(handle)
                identities.append(
                    (
                        path,
                        identity.volume_serial,
                        (identity.file_index_high << 32) | identity.file_index_low,
                    )
                )
            yield
            for handle, (path, volume, file_index) in zip(handles, identities):
                identity = _ByHandleFileInformation()
                if not kernel32.GetFileInformationByHandle(
                    handle,
                    ctypes.byref(identity),
                ):
                    raise HostFileTransactionError("path_parent_changed")
                current = path.lstat()
                current_index = (identity.file_index_high << 32) | identity.file_index_low
                if (
                    identity.attributes & FILE_ATTRIBUTE_REPARSE_POINT
                    or identity.volume_serial != volume
                    or current_index != file_index
                    or (current.st_ino and current.st_ino != file_index)
                ):
                    raise HostFileTransactionError("path_parent_changed")
        finally:
            for handle in reversed(handles):
                kernel32.CloseHandle(handle)
        return
    descriptors: list[int] = []
    identities: list[tuple[Path, int, int]] = []
    try:
        for path in unique:
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(descriptor)
            descriptors.append(descriptor)
            identities.append((path, metadata.st_dev, metadata.st_ino))
        yield
        for path, device, inode in identities:
            try:
                metadata = path.lstat()
            except FileNotFoundError as exc:
                raise HostFileTransactionError("path_parent_changed") from exc
            if (metadata.st_dev, metadata.st_ino) != (device, inode):
                raise HostFileTransactionError("path_parent_changed")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
