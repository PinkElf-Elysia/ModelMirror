from __future__ import annotations

import contextlib
import hashlib
import os
import re
import stat
import subprocess
import tempfile
import threading
import zlib
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path, PurePosixPath

from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import (
    CommitReceipt,
    normalize_commit_message,
    validate_commit_branch,
)
from server.coding_runtime.draft_workspace import DraftPolicyError, DraftWorkspace
from server.coding_runtime.projects import build_safe_git_command, build_safe_git_environment

from .host_apply_engine import (
    HostApplyError,
    _guard_directories,
    _is_link_or_reparse,
    _project_process_lock,
)
from .host_file_transaction import (
    HostFileTransactionError,
    _guard_exact_regular_object,
    _move_verified_no_replace,
    _read_regular_with_identity,
    _remove_empty_directory_exact,
    _windows_close_handle,
    _windows_handle_identity,
    _windows_open_existing,
    _windows_read_all,
    _write_durable_no_replace,
    file_identity,
    read_regular,
    remove_regular_exact,
)
from .operation_log import HostOperationJournal, HostOperationLogError, HostOperationRecord


GIT_TIMEOUT_SECONDS = 30
DEFAULT_AUTHOR_NAME = "ModelMirror Coding Assistant"
DEFAULT_AUTHOR_EMAIL = "coding@modelmirror.local"
MutationHook = Callable[[str], None]
_OBJECT_ID = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_IDENTITY = re.compile(
    r"^(?:[a-f0-9]+-[a-f0-9]+|g2-[a-f0-9]+-[a-f0-9]+-[a-f0-9]+)$"
)
_DANGEROUS_CONFIG = (
    r"^(include|include[Ii]f|filter|credential|diff|url)(\.|$)"
    r"|^remote\..*\.(promisor|partial[Cc]lone[Ff]ilter)$"
    r"|^(core\.(worktree|excludes[Ff]ile)|"
    r"extensions\.(worktree[Cc]onfig|partial[Cc]lone|ref[Ss]torage))$"
)
_PERSISTED_CONFLICT_CODES = frozenset(
    {
        "apply_receipt_invalid",
        "branch_changed",
        "commit_conflict",
        "commit_receipt_invalid",
        "head_changed",
        "index_changed",
        "repository_config_unsafe",
        "repository_unsafe",
        "target_changed",
        "undo_conflict",
    }
)
MAX_GIT_NAMESPACE_ENTRIES = 500_000
MAX_GIT_CONFIG_BYTES = 2 * 1024 * 1024
MAX_PRIVATE_OBJECTS = 10_000
MAX_PRIVATE_OBJECT_BYTES = 8 * 1024 * 1024
MAX_PRIVATE_OBJECT_CONTENT_BYTES = 64 * 1024 * 1024
PRIVATE_OBJECT_OWNER_FILE = "modelmirror-owner"


class HostCommitError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HostGitCommitEngine:
    """Create and undo a local commit without exposing paths or remote access.

    The helper owns the physical path and this engine accepts only receipts that
    were durably produced by the host apply engine.  Git commands are a closed
    plumbing-only set; repository automation and remote commands are never run.
    """

    def __init__(
        self,
        project_root: Path,
        project_id: str,
        journal: HostOperationJournal,
        *,
        author_name: str = DEFAULT_AUTHOR_NAME,
        author_email: str = DEFAULT_AUTHOR_EMAIL,
        mutation_hook: MutationHook | None = None,
        enforce_windows: bool = True,
    ) -> None:
        if enforce_windows and os.name != "nt":
            raise HostCommitError("windows_required")
        root = Path(project_root)
        if not root.is_absolute() or not root.is_dir() or _is_link_or_reparse(root):
            raise HostCommitError("project_path_invalid")
        self.root = root.resolve(strict=True)
        self.git = self.root / ".git"
        self.project_id = project_id
        self.journal = journal
        self.author_name = _validate_identity(author_name)
        self.author_email = _validate_identity(author_email)
        if "@" not in self.author_email:
            raise HostCommitError("invalid_author")
        self.mutation_hook = mutation_hook
        self.enforce_windows = enforce_windows
        self._lock = threading.Lock()
        self._validate_repository_layout()

    def commit(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        branch: str,
        expected_head: str,
        message: str,
    ) -> CommitReceipt:
        safe_branch = _branch(branch)
        try:
            safe_message = normalize_commit_message(message)
        except ValueError as exc:
            raise HostCommitError("commit_message_invalid") from exc
        if expected_head != _object_id(expected_head):
            raise HostCommitError("commit_conflict")
        with self._operation_lock():
            applied = self._applied_record(apply_receipt, safe_branch, expected_head)
            try:
                record = self.journal.create(
                    operation_id=operation_id,
                    action="commit",
                    project_id=self.project_id,
                    revision=apply_receipt.revision,
                    branch=safe_branch,
                    expected_head=expected_head,
                    patch_sha256=applied.patch_sha256,
                    patch=applied.patch,
                    apply_receipt=_apply_receipt_dict(apply_receipt),
                    file_identities=applied.file_identities,
                    commit_message=safe_message,
                )
            except HostOperationLogError as exc:
                raise HostCommitError(exc.code) from exc
            if record.state == "conflict":
                self._settle_conflict(record)
                raise HostCommitError("commit_conflict")
            return self._resume_with_conflict(record)

    def undo(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        commit_receipt: CommitReceipt,
        branch: str,
    ) -> CommitReceipt:
        safe_branch = _branch(branch)
        if commit_receipt.branch != safe_branch:
            raise HostCommitError("undo_conflict")
        with self._operation_lock():
            applied = self._applied_record(
                apply_receipt,
                safe_branch,
                commit_receipt.parent_sha,
            )
            self._assert_commit_receipt(commit_receipt, apply_receipt, safe_branch)
            self._committed_record(commit_receipt, apply_receipt, safe_branch)
            try:
                record = self.journal.create(
                    operation_id=operation_id,
                    action="undo",
                    project_id=self.project_id,
                    revision=apply_receipt.revision,
                    branch=safe_branch,
                    expected_head=commit_receipt.commit_sha,
                    patch_sha256=applied.patch_sha256,
                    patch=applied.patch,
                    apply_receipt=_apply_receipt_dict(apply_receipt),
                    commit_receipt=_commit_receipt_dict(commit_receipt),
                    file_identities=applied.file_identities,
                )
            except HostOperationLogError as exc:
                raise HostCommitError(exc.code) from exc
            if record.state == "conflict":
                self._settle_conflict(record)
                raise HostCommitError("undo_conflict")
            return self._resume_with_conflict(record)

    def reconcile(self, operation_id: str) -> tuple[str, CommitReceipt | None]:
        with self._operation_lock():
            try:
                record = self.journal.get(operation_id)
            except HostOperationLogError as exc:
                raise HostCommitError(exc.code) from exc
            if record is None:
                return "not_committed", None
            if record.project_id != self.project_id or record.action not in {"commit", "undo"}:
                raise HostCommitError("operation_conflict")
            if record.state == "conflict":
                self._settle_conflict(record)
                return "conflict", _optional_commit_receipt(record.commit_receipt)
            try:
                receipt = self._resume_with_conflict(record)
                return ("committed" if record.action == "commit" else "undone"), receipt
            except HostCommitError as exc:
                if exc.code in _PERSISTED_CONFLICT_CODES:
                    return "conflict", _optional_commit_receipt(record.commit_receipt)
                raise

    def _resume_with_conflict(self, record: HostOperationRecord) -> CommitReceipt:
        record = self._bind_reflog_metadata(record)
        # A process can stop after one or both reflogs were parked. Restore the
        # exact journal-bound objects before any Git command or namespace scan;
        # an ambiguous replacement is never overwritten.
        terminal_without_parked_reflogs = (
            record.state in {"committed", "undone"}
            and not any(
                _path_entry_exists(backup)
                for _label, _source, backup in self._operation_reflog_paths(record)
            )
        )
        if not terminal_without_parked_reflogs:
            self._recover_reflogs(record)
        if self._conflict_marker_present(record.operation_id):
            self._mark_conflict(
                record.operation_id,
                "commit_conflict" if record.action == "commit" else "undo_conflict",
            )
            raise HostCommitError(
                "commit_conflict" if record.action == "commit" else "undo_conflict"
            )
        try:
            if record.action == "commit":
                return self._resume_commit(record)
            return self._resume_undo(record)
        except HostCommitError as exc:
            if exc.code in _PERSISTED_CONFLICT_CODES:
                self._mark_conflict(record.operation_id, exc.code)
            raise

    def _bind_reflog_metadata(
        self,
        record: HostOperationRecord,
    ) -> HostOperationRecord:
        if record.reflog_metadata:
            return record
        if record.state != "prepared" or record.action not in {"commit", "undo"}:
            raise HostCommitError("operation_log_unavailable")
        self._notify("metadata_before_reflog_bind")
        metadata = self._capture_reflog_metadata(record.branch)
        return self._transition(
            record.operation_id,
            record.state,
            reflog_metadata=metadata,
        )

    @contextlib.contextmanager
    def _operation_lock(self):
        if self.enforce_windows and os.name != "nt":
            raise HostCommitError("windows_required")
        try:
            with _project_process_lock(
                self.root,
                preflight=self._guard_repository_preflight,
            ), self._lock:
                yield
        except HostApplyError as exc:
            raise HostCommitError(exc.code) from exc

    @contextlib.contextmanager
    def _guard_repository_preflight(self):
        """Bind safe Git configuration before creating operation artifacts."""

        self._validate_repository_layout()
        config = self.git / "config"
        namespaces = [self.git / "objects", self.git / "refs"]
        if _path_entry_exists(self.git / "info"):
            namespaces.append(self.git / "info")
        try:
            with contextlib.ExitStack() as stack:
                stack.enter_context(_guard_directories(tuple(namespaces)))
                directories = tuple(
                    dict.fromkeys(
                        directory
                        for namespace in namespaces
                        for directory in _safe_git_namespace(namespace)
                    )
                )
                stack.enter_context(_guard_directories(directories))
                current = _read_regular_with_identity(config)
                if current is None:
                    raise HostCommitError("repository_unsafe")
                content, identity = current
                if len(content) > MAX_GIT_CONFIG_BYTES:
                    raise HostCommitError("repository_config_unsafe")
                stack.enter_context(
                    _guard_exact_regular_object(
                        config,
                        content,
                        expected_identity=identity,
                    )
                )
                self._assert_repository_redirections_absent()
                self._assert_no_dangerous_config_file(config)
                yield
                self._validate_repository_layout()
                self._assert_repository_redirections_absent()
                for namespace in namespaces:
                    _safe_git_namespace(namespace)
        except HostCommitError:
            raise
        except (HostApplyError, HostFileTransactionError, OSError) as exc:
            raise HostCommitError("repository_unsafe") from exc

    @contextlib.contextmanager
    def _git_write_guards(
        self,
        branch: str,
        *,
        parked_record: HostOperationRecord | None = None,
    ):
        self._assert_repository_layout_and_branch(branch)
        directories: list[Path] = []
        try:
            for namespace in (
                self.git / "objects",
                self.git / "refs",
                self.git / "logs",
                self.git / "info",
            ):
                if namespace.exists():
                    directories.extend(_safe_git_namespace(namespace))
            branch_parts = PurePosixPath(branch).parts[:-1]
            current = self.git / "refs" / "heads"
            for part in branch_parts:
                current = current / part
                if not current.is_dir() or _is_link_or_reparse(current):
                    raise HostCommitError("repository_unsafe")
                directories.append(current)
            logs = self.git / "logs"
            if logs.exists():
                for base in (logs, logs / "refs", logs / "refs" / "heads"):
                    if base.exists():
                        if not base.is_dir() or _is_link_or_reparse(base):
                            raise HostCommitError("repository_unsafe")
                        directories.append(base)
                current = logs / "refs" / "heads"
                for part in branch_parts:
                    current = current / part
                    if current.exists():
                        if not current.is_dir() or _is_link_or_reparse(current):
                            raise HostCommitError("repository_unsafe")
                        directories.append(current)
            with contextlib.ExitStack() as stack:
                stack.enter_context(_guard_directories(tuple(dict.fromkeys(directories))))
                for metadata in (self.git / "config", self.git / "HEAD"):
                    current_file = _read_regular_with_identity(metadata)
                    if current_file is None:
                        raise HostCommitError("repository_unsafe")
                    stack.enter_context(
                        _guard_exact_regular_object(
                            metadata,
                            current_file[0],
                            expected_identity=current_file[1],
                        )
                    )
                packed_refs = self.git / "packed-refs"
                if packed_refs.exists():
                    packed = _read_regular_with_identity(packed_refs)
                    if packed is None:
                        raise HostCommitError("repository_unsafe")
                    stack.enter_context(
                        _guard_exact_regular_object(
                            packed_refs,
                            packed[0],
                            expected_identity=packed[1],
                        )
                    )
                if parked_record is None:
                    for reflog in (
                        self.git / "logs" / "HEAD",
                        self.git / "logs" / "refs" / "heads" / PurePosixPath(branch),
                    ):
                        if not reflog.exists():
                            raise HostCommitError("repository_unsafe")
                        stack.enter_context(_guard_mutable_regular_leaf(reflog))
                else:
                    self._assert_reflogs_parked(parked_record)
                yield
        except HostFileTransactionError as exc:
            raise HostCommitError("repository_unsafe") from exc

    def _resume_commit(self, record: HostOperationRecord) -> CommitReceipt:
        if record.action != "commit" or record.commit_message is None:
            raise HostCommitError("operation_conflict")
        apply_receipt = _apply_receipt(record.apply_receipt)
        if record.state == "committed":
            receipt = _commit_receipt(record.commit_receipt)
            self._assert_commit_receipt(receipt, apply_receipt, record.branch)
            self._cleanup_index_backup(record)
            if self._git_text("rev-parse", "--verify", "HEAD") != receipt.commit_sha:
                raise HostCommitError("commit_not_current")
            return receipt
        applied = self._applied_record(apply_receipt, record.branch, record.expected_head)
        if applied.file_identities != record.file_identities:
            return self._conflict(record.operation_id, "target_changed")
        receipt: CommitReceipt | None = None
        should_transition = False
        with self._parked_reflogs(record), self._git_write_guards(
            record.branch,
            parked_record=record,
        ), self._guard_applied_files(
            apply_receipt,
            record.file_identities,
        ) as contents:
            receipt = _optional_commit_receipt(record.commit_receipt)
            prepared_index: Path | None = None
            try:
                if receipt is None:
                    self._assert_repository_state(
                        branch=record.branch,
                        allowed_heads=(record.expected_head,),
                        expected_paths=tuple(item.path for item in apply_receipt.files),
                        require_applied_status=True,
                    )
                    (
                        tree,
                        commit_sha,
                        prepared_index,
                        source_index,
                        source_index_identity,
                    ) = self._prepare_commit(
                        record=record,
                        apply_receipt=apply_receipt,
                        contents=contents,
                    )
                    receipt = CommitReceipt(
                        commit_id=record.operation_id,
                        revision=record.revision,
                        apply_id=apply_receipt.apply_id,
                        commit_sha=commit_sha,
                        parent_sha=record.expected_head,
                        tree_sha=tree,
                        message=record.commit_message,
                        files=tuple(item.path for item in apply_receipt.files),
                        branch=record.branch,
                        committed_at=record.created_at,
                    )
                    record = self._stage_operation_index(
                        record=record,
                        prepared=prepared_index,
                        state="committing",
                        commit_receipt=receipt,
                        old_tree=self._tree_for_commit(receipt.parent_sha),
                        new_tree=receipt.tree_sha,
                        source_index=source_index,
                        source_index_identity=source_index_identity,
                    )
                    prepared_index = None
                    self._notify("commit_after_receipt")
                else:
                    self._assert_commit_receipt(receipt, apply_receipt, record.branch)
                state = self._metadata_state(record, receipt)
                if record.state != "committing":
                    return self._conflict(record.operation_id, "commit_conflict")
                if state == "parent_parent":
                    self._assert_applied_status(apply_receipt)
                    self._advance_ref_with_index_lock(
                        record=record,
                        receipt=receipt,
                        new_head=receipt.commit_sha,
                        old_head=receipt.parent_sha,
                        old_tree=self._tree_for_commit(receipt.parent_sha),
                        expected_status=receipt.files,
                        message="ModelMirror controlled host commit",
                    )
                    self._notify("commit_after_ref")
                    state = "commit_parent"
                if state == "commit_parent":
                    transition_status = _commit_ref_transition_paths(apply_receipt)
                    self._assert_status_paths(
                        transition_status,
                        "commit_conflict",
                        index_path=self._operation_status_index(record),
                    )
                    self._publish_index_lock(
                        record=record,
                        branch=record.branch,
                        expected_head=receipt.commit_sha,
                        old_tree=self._tree_for_commit(receipt.parent_sha),
                        new_tree=receipt.tree_sha,
                        expected_status=transition_status,
                    )
                    self._notify("commit_after_index")
                    state = "commit_commit"
                if state != "commit_commit":
                    return self._conflict(record.operation_id, "commit_conflict")
                self._assert_post_commit(receipt)
                should_transition = True
            finally:
                if prepared_index is not None:
                    prepared_index.unlink(missing_ok=True)
        if receipt is None:
            raise HostCommitError("commit_receipt_invalid")
        if should_transition:
            self._transition(
                record.operation_id,
                "committed",
                commit_receipt=_commit_receipt_dict(receipt),
            )
            self._notify("commit_after_journal")
        self._cleanup_index_backup(record)
        return receipt

    def _resume_undo(self, record: HostOperationRecord) -> CommitReceipt:
        if record.action != "undo":
            raise HostCommitError("operation_conflict")
        apply_receipt = _apply_receipt(record.apply_receipt)
        receipt = _commit_receipt(record.commit_receipt)
        self._assert_commit_receipt(receipt, apply_receipt, record.branch)
        if record.state == "undone":
            self._cleanup_index_backup(record)
            if self._git_text("rev-parse", "--verify", "HEAD") != receipt.parent_sha:
                raise HostCommitError("undo_not_current")
            return receipt
        self._committed_record(receipt, apply_receipt, record.branch)
        applied = self._applied_record(apply_receipt, record.branch, receipt.parent_sha)
        if applied.file_identities != record.file_identities:
            return self._conflict(record.operation_id, "target_changed")
        should_transition = False
        with self._parked_reflogs(record), self._git_write_guards(
            record.branch,
            parked_record=record,
        ), self._guard_applied_files(
            apply_receipt,
            record.file_identities,
        ):
            self._assert_repository_layout_and_branch(record.branch)
            state = self._metadata_state(record, receipt)
            if record.state == "prepared":
                if state != "commit_commit" or self._status_paths():
                    return self._conflict(record.operation_id, "undo_conflict")
                parent_tree = self._tree_for_commit(receipt.parent_sha)
                (
                    parent_index,
                    source_index,
                    source_index_identity,
                ) = self._prepare_undo_index(
                    apply_receipt=apply_receipt,
                    parent_sha=receipt.parent_sha,
                    current_tree=receipt.tree_sha,
                    parent_tree=parent_tree,
                )
                record = self._stage_operation_index(
                    record=record,
                    prepared=parent_index,
                    state="undoing",
                    commit_receipt=receipt,
                    old_tree=receipt.tree_sha,
                    new_tree=parent_tree,
                    source_index=source_index,
                    source_index_identity=source_index_identity,
                )
                self._notify("undo_after_intent")
            if record.state != "undoing":
                return self._conflict(record.operation_id, "undo_conflict")
            if state == "commit_commit":
                if self._status_paths():
                    return self._conflict(record.operation_id, "undo_conflict")
                self._advance_ref_with_index_lock(
                    record=record,
                    receipt=receipt,
                    new_head=receipt.parent_sha,
                    old_head=receipt.commit_sha,
                    old_tree=receipt.tree_sha,
                    expected_status=(),
                    message="ModelMirror controlled host commit undo",
                )
                self._notify("undo_after_ref")
                state = "parent_commit"
            if state == "parent_commit":
                self._assert_applied_status(apply_receipt, record=record)
                self._publish_index_lock(
                    record=record,
                    branch=record.branch,
                    expected_head=receipt.parent_sha,
                    old_tree=receipt.tree_sha,
                    new_tree=self._tree_for_commit(receipt.parent_sha),
                    expected_status=receipt.files,
                )
                self._notify("undo_after_index")
                state = "parent_parent"
            if state != "parent_parent":
                return self._conflict(record.operation_id, "undo_conflict")
            self._assert_applied_status(apply_receipt)
            should_transition = True
        if should_transition:
            self._transition(
                record.operation_id,
                "undone",
                commit_receipt=_commit_receipt_dict(receipt),
            )
            self._notify("undo_after_journal")
        self._cleanup_index_backup(record)
        return receipt

    def _prepare_commit(
        self,
        *,
        record: HostOperationRecord,
        apply_receipt: ApplyReceipt,
        contents: dict[str, bytes],
    ) -> tuple[str, str, Path, bytes, str]:
        expected_tree = self._tree_for_commit(record.expected_head)
        index, source_index, source_index_identity = self._copy_current_index(
            expected_tree
        )
        try:
            with self._private_object_store(record) as object_directory:
                object_arguments = {
                    "object_directory": object_directory,
                    "alternate_object_directories": (self.git / "objects",),
                }
                for item in apply_receipt.files:
                    if item.after_sha256 is None:
                        self._git(
                            "update-index",
                            "--force-remove",
                            "--",
                            item.path,
                            index_path=index,
                            **object_arguments,
                        )
                        continue
                    working = contents[item.path]
                    mode, baseline = self._head_file(record.expected_head, item.path)
                    if item.existed_before:
                        if baseline is None or item.before_sha256 is None:
                            raise HostCommitError("target_changed")
                        blob = _canonical_blob(baseline, working, item.before_sha256)
                    else:
                        if baseline is not None:
                            raise HostCommitError("target_changed")
                        blob = working
                        mode = "100644"
                    object_id = self._git_text(
                        "hash-object",
                        "-w",
                        "--no-filters",
                        "--stdin",
                        input_bytes=blob,
                        **object_arguments,
                    )
                    self._git(
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        mode,
                        object_id,
                        item.path,
                        index_path=index,
                        **object_arguments,
                    )
                tree = self._git_text(
                    "write-tree",
                    index_path=index,
                    **object_arguments,
                )
                timestamp = int(record.created_at)
                commit_sha = self._git_text(
                    "commit-tree",
                    tree,
                    "-p",
                    record.expected_head,
                    input_bytes=(record.commit_message + "\n").encode("utf-8"),
                    extra_environment={
                        "GIT_AUTHOR_DATE": f"@{timestamp} +0000",
                        "GIT_COMMITTER_DATE": f"@{timestamp} +0000",
                    },
                    **object_arguments,
                )
                self._notify("commit_before_object_publish")
                self._publish_private_objects(object_directory)
                self._assert_generated_objects_available(
                    tree=tree,
                    commit_sha=commit_sha,
                    parent_sha=record.expected_head,
                )
            self._notify("commit_after_tree")
            self._assert_object_namespace_safe()
            return tree, commit_sha, index, source_index, source_index_identity
        except BaseException:
            index.unlink(missing_ok=True)
            raise

    def _stage_operation_index(
        self,
        *,
        record: HostOperationRecord,
        prepared: Path,
        state: str,
        commit_receipt: CommitReceipt,
        old_tree: str,
        new_tree: str,
        source_index: bytes,
        source_index_identity: str,
    ) -> HostOperationRecord:
        stage = self._index_stage(record.operation_id)
        index = self.git / "index"
        try:
            replacement = prepared.read_bytes()
            if self._tree_from_index_bytes(replacement) != new_tree:
                raise HostCommitError("index_changed")
            current = _read_regular_with_identity(index)
            if current != (source_index, source_index_identity):
                raise HostCommitError("index_changed")
            original, before_identity = current
            before_digest = hashlib.sha256(original).hexdigest()
            if self._tree_from_index_bytes(original) != old_tree:
                raise HostCommitError("index_changed")
            if (self.git / "index.lock").exists():
                raise HostCommitError("index_busy")
            if stage.exists():
                staged = _read_regular_with_identity(stage)
                if staged is None or staged[0] != replacement:
                    raise HostCommitError("index_changed")
            else:
                _write_durable_no_replace(stage, replacement, 0o600)
                staged = _read_regular_with_identity(stage)
            if staged is None:
                raise HostCommitError("index_changed")
            staged_content, staged_identity = staged
            digest = hashlib.sha256(staged_content).hexdigest()
            reflog_metadata = record.reflog_metadata
            if not reflog_metadata:
                raise HostCommitError("operation_log_unavailable")
            with _guard_exact_regular_object(
                index,
                original,
                expected_identity=before_identity,
            ), _guard_exact_regular_object(
                stage,
                staged_content,
                expected_identity=staged_identity,
            ):
                return self._transition(
                    record.operation_id,
                    state,
                    commit_receipt=_commit_receipt_dict(commit_receipt),
                    index_sha256=digest,
                    index_before_sha256=before_digest,
                    index_identity=staged_identity,
                    index_before_identity=before_identity,
                    reflog_metadata=reflog_metadata,
                )
        except HostFileTransactionError as exc:
            raise HostCommitError("index_changed") from exc
        finally:
            prepared.unlink(missing_ok=True)

    def _reflog_paths(self, branch: str) -> tuple[tuple[str, Path, Path], ...]:
        transaction_root = self.git / "modelmirror-transactions"
        return (
            (
                "HEAD",
                self.git / "logs" / "HEAD",
                transaction_root / "{operation_id}.head-reflog-before",
            ),
            (
                "branch",
                self.git / "logs" / "refs" / "heads" / PurePosixPath(branch),
                transaction_root / "{operation_id}.branch-reflog-before",
            ),
        )

    def _operation_reflog_paths(
        self,
        record: HostOperationRecord,
    ) -> tuple[tuple[str, Path, Path], ...]:
        return tuple(
            (label, source, Path(str(backup).format(operation_id=record.operation_id)))
            for label, source, backup in self._reflog_paths(record.branch)
        )

    def _capture_reflog_metadata(self, branch: str) -> tuple[str, ...]:
        metadata: list[str] = []
        with self._guard_reflog_directories(branch):
            for label, source, _backup in self._reflog_paths(branch):
                current = _read_regular_with_identity(source)
                if current is None:
                    raise HostCommitError("repository_unsafe")
                content, identity = current
                _assert_single_link_regular(source)
                metadata.append(
                    f"{label}:{hashlib.sha256(content).hexdigest()}@{identity}"
                )
        return tuple(metadata)

    def _reflog_directories(self, branch: str) -> tuple[Path, ...]:
        directories = [
            self.git / "logs",
            self.git / "logs" / "refs",
            self.git / "logs" / "refs" / "heads",
        ]
        current = self.git / "logs" / "refs" / "heads"
        for part in PurePosixPath(branch).parts[:-1]:
            current = current / part
            directories.append(current)
        directories.append(self.git / "modelmirror-transactions")
        return tuple(dict.fromkeys(directories))

    @contextlib.contextmanager
    def _guard_reflog_directories(self, branch: str):
        directories = self._reflog_directories(branch)
        try:
            with _guard_directories(directories):
                if any(
                    not directory.is_dir() or _is_link_or_reparse(directory)
                    for directory in directories
                ):
                    raise HostCommitError("repository_unsafe")
                yield
                if any(
                    not directory.is_dir() or _is_link_or_reparse(directory)
                    for directory in directories
                ):
                    raise HostCommitError("repository_unsafe")
        except (HostApplyError, HostFileTransactionError) as exc:
            raise HostCommitError("repository_unsafe") from exc

    def _expected_reflogs(
        self,
        record: HostOperationRecord,
    ) -> dict[str, tuple[str, str]]:
        if not record.reflog_metadata:
            return {}
        if len(record.reflog_metadata) != 2:
            raise HostCommitError("operation_log_unavailable")
        expected: dict[str, tuple[str, str]] = {}
        for item in record.reflog_metadata:
            try:
                label, value = item.split(":", 1)
                digest, identity = value.split("@", 1)
            except ValueError as exc:
                raise HostCommitError("operation_log_unavailable") from exc
            if label not in {"HEAD", "branch"} or label in expected:
                raise HostCommitError("operation_log_unavailable")
            expected[label] = (digest, identity)
        if set(expected) != {"HEAD", "branch"}:
            raise HostCommitError("operation_log_unavailable")
        return expected

    def _recover_reflogs(self, record: HostOperationRecord) -> None:
        with self._guard_reflog_directories(record.branch):
            self._recover_reflogs_guarded(record)

    def _recover_reflogs_guarded(self, record: HostOperationRecord) -> None:
        expected = self._expected_reflogs(record)
        paths = self._operation_reflog_paths(record)
        if not expected:
            if any(_path_entry_exists(backup) for _label, _source, backup in paths):
                raise HostCommitError("operation_log_unavailable")
            return
        restored: list[str] = []
        try:
            for label, source, backup in paths:
                digest, identity = expected[label]
                current = _read_regular_with_identity(source)
                parked = _read_regular_with_identity(backup)
                if current is not None and parked is None:
                    if (
                        current[1] != identity
                        or hashlib.sha256(current[0]).hexdigest() != digest
                    ):
                        raise HostCommitError("operation_log_unavailable")
                    _assert_single_link_regular(source)
                    continue
                if current is None and parked is not None:
                    if (
                        parked[1] != identity
                        or hashlib.sha256(parked[0]).hexdigest() != digest
                    ):
                        raise HostCommitError("operation_log_unavailable")
                    _assert_single_link_regular(backup)
                    _move_verified_no_replace(
                        backup,
                        source,
                        parked[0],
                        expected_identity=identity,
                    )
                    recovered = _read_regular_with_identity(source)
                    if recovered != parked:
                        raise HostCommitError("operation_log_unavailable")
                    _assert_single_link_regular(source)
                    restored.append(label)
                    continue
                # Both present is ambiguous; neither present has lost durable
                # evidence. Never replace or delete either pathname.
                raise HostCommitError("operation_log_unavailable")
        except HostFileTransactionError as exc:
            raise HostCommitError("operation_log_unavailable") from exc
        if restored:
            self._notify("metadata_after_reflog_restore")

    def _assert_reflogs_parked(self, record: HostOperationRecord) -> None:
        expected = self._expected_reflogs(record)
        if set(expected) != {"HEAD", "branch"}:
            raise HostCommitError("operation_log_unavailable")
        for label, source, backup in self._operation_reflog_paths(record):
            if _path_entry_exists(source):
                raise HostCommitError("repository_unsafe")
            parked = _read_regular_with_identity(backup)
            digest, identity = expected[label]
            if (
                parked is None
                or parked[1] != identity
                or hashlib.sha256(parked[0]).hexdigest() != digest
            ):
                raise HostCommitError("operation_log_unavailable")
            _assert_single_link_regular(backup)

    @contextlib.contextmanager
    def _parked_reflogs(self, record: HostOperationRecord):
        expected = self._expected_reflogs(record)
        if set(expected) != {"HEAD", "branch"}:
            raise HostCommitError("operation_log_unavailable")
        self._recover_reflogs(record)
        with self._guard_reflog_directories(record.branch):
            try:
                for label, source, backup in self._operation_reflog_paths(record):
                    digest, identity = expected[label]
                    current = _read_regular_with_identity(source)
                    if (
                        current is None
                        or current[1] != identity
                        or hashlib.sha256(current[0]).hexdigest() != digest
                        or _path_entry_exists(backup)
                    ):
                        raise HostCommitError("repository_unsafe")
                    _assert_single_link_regular(source)
                    _move_verified_no_replace(
                        source,
                        backup,
                        current[0],
                        expected_identity=identity,
                    )
                    parked = _read_regular_with_identity(backup)
                    if parked != current:
                        raise HostCommitError("repository_unsafe")
                    self._notify(f"metadata_after_{label.lower()}_reflog_park")
                self._assert_reflogs_parked(record)
                yield
            except HostFileTransactionError as exc:
                raise HostCommitError("repository_unsafe") from exc
            finally:
                # Python exceptions restore immediately; an actual process stop
                # is recovered from the journal-bound backups on restart.
                self._recover_reflogs_guarded(record)

    def _assert_direct_branch_ref(
        self,
        branch: str,
        expected_head: str,
    ) -> tuple[bytes, str]:
        path = self.git / "refs" / "heads" / PurePosixPath(branch)
        current = _read_regular_with_identity(path)
        if current is None or current[0] not in {
            expected_head.encode("ascii"),
            f"{expected_head}\n".encode("ascii"),
        }:
            raise HostCommitError("head_changed")
        _assert_single_link_regular(path)
        return current

    def _update_ref_without_reflog(
        self,
        *,
        record: HostOperationRecord,
        new_head: str,
        old_head: str,
        message: str,
    ) -> None:
        self._assert_repository_redirections_absent()
        self._assert_direct_branch_ref(record.branch, old_head)
        self._assert_reflogs_parked(record)
        try:
            self._git(
                "update-ref",
                "--no-deref",
                "--no-create-reflog",
                "-m",
                message,
                f"refs/heads/{record.branch}",
                new_head,
                old_head,
            )
        except HostCommitError as exc:
            raise HostCommitError("head_changed") from exc
        self._notify("metadata_after_ref_update")
        self._assert_reflogs_parked(record)
        self._assert_direct_branch_ref(record.branch, new_head)

    def _advance_ref_with_index_lock(
        self,
        *,
        record: HostOperationRecord,
        receipt: CommitReceipt,
        new_head: str,
        old_head: str,
        old_tree: str,
        expected_status: tuple[str, ...],
        message: str,
    ) -> None:
        replacement = self._ensure_index_lock(record, old_tree)
        index = self.git / "index"
        lock = self.git / "index.lock"
        head_path = self.git / "HEAD"
        current_index = _read_regular_with_identity(index)
        current_head = _read_regular_with_identity(head_path)
        if current_index is None or current_head is None:
            raise HostCommitError("repository_unsafe")
        index_content, index_identity = current_index
        head_content, head_identity = current_head
        if index_identity != record.index_before_identity:
            raise HostCommitError("index_changed")
        try:
            with _guard_exact_regular_object(
                index,
                index_content,
                expected_identity=index_identity,
            ), _guard_exact_regular_object(
                lock,
                replacement,
                expected_identity=record.index_identity,
            ), _guard_exact_regular_object(
                head_path,
                head_content,
                expected_identity=head_identity,
            ):
                self._notify("metadata_before_ref")
                self._assert_object_namespace_safe()
                self._assert_repository_redirections_absent()
                self._assert_direct_branch_ref(record.branch, old_head)
                if (
                    self._git_text("symbolic-ref", "--quiet", "HEAD")
                    != f"refs/heads/{record.branch}"
                    or self._git_text("rev-parse", "--verify", "HEAD") != old_head
                    or self._real_index_tree() != old_tree
                    or self._status_paths() != expected_status
                ):
                    raise HostCommitError("head_changed")
                with self._guard_current_object_namespace():
                    self._update_ref_without_reflog(
                        record=record,
                        new_head=new_head,
                        old_head=old_head,
                        message=message,
                    )
                if (
                    self._git_text("symbolic-ref", "--quiet", "HEAD")
                    != f"refs/heads/{record.branch}"
                    or self._git_text("rev-parse", "--verify", "HEAD") != new_head
                    or self._real_index_tree() != old_tree
                ):
                    raise HostCommitError("head_changed")
        except HostFileTransactionError as exc:
            raise HostCommitError("index_changed") from exc

    def _publish_index_lock(
        self,
        *,
        record: HostOperationRecord,
        branch: str,
        expected_head: str,
        old_tree: str,
        new_tree: str,
        expected_status: tuple[str, ...],
    ) -> None:
        replacement = self._ensure_index_lock(record, old_tree)
        index = self.git / "index"
        lock = self.git / "index.lock"
        backup = self._index_backup(record.operation_id)
        current = _read_regular_with_identity(index)
        parked = _read_regular_with_identity(backup)
        if current is not None and parked is not None:
            raise HostCommitError("index_changed")
        original_record = current if current is not None else parked
        if (
            original_record is None
            or original_record[1] != record.index_before_identity
            or hashlib.sha256(original_record[0]).hexdigest()
            != record.index_before_sha256
            or self._tree_from_index_bytes(original_record[0]) != old_tree
        ):
            raise HostCommitError("index_changed")
        original, original_identity = original_record
        branch_ref = self.git / "refs" / "heads" / PurePosixPath(branch)
        branch_ref_record = _read_regular_with_identity(branch_ref)
        if branch_ref_record is None:
            raise HostCommitError("repository_unsafe")
        if branch_ref_record[0] not in {
            expected_head.encode("ascii"),
            f"{expected_head}\n".encode("ascii"),
        }:
            raise HostCommitError("head_changed")
        try:
            with _guard_exact_regular_object(
                branch_ref,
                branch_ref_record[0],
                expected_identity=branch_ref_record[1],
            ):
                self._notify("metadata_before_index")
                if _read_regular_with_identity(branch_ref) != branch_ref_record:
                    raise HostCommitError("head_changed")
                if (
                    self._git_text("symbolic-ref", "--quiet", "HEAD")
                    != f"refs/heads/{branch}"
                    or self._git_text("rev-parse", "--verify", "HEAD")
                    != expected_head
                    or self._status_paths(
                        index_path=(backup if current is None else None)
                    )
                    != expected_status
                ):
                    raise HostCommitError("index_changed")
                if current is not None:
                    _move_verified_no_replace(
                        index,
                        backup,
                        original,
                        expected_identity=original_identity,
                    )
                    parked = _read_regular_with_identity(backup)
                    if parked != original_record:
                        raise HostCommitError("index_changed")
                _move_verified_no_replace(
                    lock,
                    index,
                    replacement,
                    expected_identity=record.index_identity,
                )
                if _read_regular_with_identity(branch_ref) != branch_ref_record:
                    raise HostCommitError("head_changed")
        except HostFileTransactionError as exc:
            self._restore_parked_index(record, old_tree)
            raise HostCommitError("index_changed") from exc
        installed = _read_regular_with_identity(index)
        if (
            installed is None
            or installed[1] != record.index_identity
            or hashlib.sha256(installed[0]).hexdigest() != record.index_sha256
            or self._tree_from_index_bytes(installed[0]) != new_tree
        ):
            raise HostCommitError("index_changed")

    def _ensure_index_lock(self, record: HostOperationRecord, old_tree: str) -> bytes:
        if (
            record.index_sha256 is None
            or record.index_before_sha256 is None
            or record.index_identity is None
            or record.index_before_identity is None
        ):
            raise HostCommitError("operation_conflict")
        index = self.git / "index"
        backup = self._index_backup(record.operation_id)
        current = _read_regular_with_identity(index)
        parked = _read_regular_with_identity(backup)
        if current is not None and parked is not None:
            raise HostCommitError("index_changed")
        original = current if current is not None else parked
        if (
            original is None
            or original[1] != record.index_before_identity
            or hashlib.sha256(original[0]).hexdigest() != record.index_before_sha256
            or self._tree_from_index_bytes(original[0]) != old_tree
        ):
            raise HostCommitError("index_changed")
        stage = self._index_stage(record.operation_id)
        lock = self.git / "index.lock"
        if stage.exists() and lock.exists():
            raise HostCommitError("index_changed")
        artifact = stage if stage.exists() else lock
        staged = _read_regular_with_identity(artifact)
        if (
            staged is None
            or staged[1] != record.index_identity
            or hashlib.sha256(staged[0]).hexdigest() != record.index_sha256
        ):
            raise HostCommitError("index_changed")
        if artifact == stage:
            try:
                _move_verified_no_replace(
                    stage,
                    lock,
                    staged[0],
                    expected_identity=record.index_identity,
                )
            except HostFileTransactionError as exc:
                raise HostCommitError("index_changed") from exc
            moved = _read_regular_with_identity(lock)
            if moved is None or moved != staged:
                raise HostCommitError("index_changed")
        return staged[0]

    def _restore_parked_index(self, record: HostOperationRecord, old_tree: str) -> None:
        index = self.git / "index"
        backup = self._index_backup(record.operation_id)
        if index.exists() or not backup.exists():
            return
        parked = _read_regular_with_identity(backup)
        if (
            parked is None
            or parked[1] != record.index_before_identity
            or hashlib.sha256(parked[0]).hexdigest() != record.index_before_sha256
            or self._tree_from_index_bytes(parked[0]) != old_tree
        ):
            return
        try:
            _move_verified_no_replace(
                backup,
                index,
                parked[0],
                expected_identity=record.index_before_identity,
            )
        except HostFileTransactionError:
            return

    def _cleanup_index_backup(self, record: HostOperationRecord) -> None:
        backup = self._index_backup(record.operation_id)
        parked = _read_regular_with_identity(backup)
        if parked is None:
            return
        if _read_regular_with_identity(self.git / "index") is None:
            raise HostCommitError("index_cleanup_pending")
        if parked[1] != record.index_before_identity:
            raise HostCommitError("index_cleanup_pending")
        if hashlib.sha256(parked[0]).hexdigest() != record.index_before_sha256:
            raise HostCommitError("index_cleanup_pending")
        try:
            remove_regular_exact(
                backup,
                parked[0],
                expected_identity=record.index_before_identity,
            )
        except HostFileTransactionError as exc:
            raise HostCommitError("index_cleanup_pending") from exc

    def _index_stage(self, operation_id: str) -> Path:
        return self.git / "modelmirror-transactions" / f"{operation_id}.commit-index"

    def _index_backup(self, operation_id: str) -> Path:
        return self.git / "modelmirror-transactions" / f"{operation_id}.index-before"

    def _index_conflict_artifact(self, operation_id: str) -> Path:
        return self.git / "modelmirror-transactions" / f"{operation_id}.index-conflict"

    def _conflict_marker(self, operation_id: str) -> Path:
        return self.git / "modelmirror-transactions" / f"{operation_id}.commit-conflict"

    def _metadata_state(
        self,
        record: HostOperationRecord,
        receipt: CommitReceipt,
    ) -> str:
        self._assert_repository_layout_and_branch(receipt.branch)
        head = self._git_text("rev-parse", "--verify", "HEAD")
        parent_tree = self._tree_for_commit(receipt.parent_sha)
        index = _read_regular_with_identity(self.git / "index")
        if index is not None:
            index_tree = self._tree_from_index_bytes(index[0])
            if record.state in {"committing", "undoing"} and record.index_identity is not None:
                old_tree = parent_tree if record.action == "commit" else receipt.tree_sha
                new_tree = receipt.tree_sha if record.action == "commit" else parent_tree
                expected_metadata = (
                    (record.index_before_identity, record.index_before_sha256)
                    if index_tree == old_tree
                    else (record.index_identity, record.index_sha256)
                    if index_tree == new_tree
                    else (None, None)
                )
                if (
                    expected_metadata[0] is None
                    or index[1] != expected_metadata[0]
                    or hashlib.sha256(index[0]).hexdigest() != expected_metadata[1]
                ):
                    return "conflict"
        else:
            # A hard stop can occur after the exact old index was parked but
            # before the owned lock was published.  Recover only when both
            # private artifacts still match the durable operation record.
            backup = _read_regular_with_identity(self._index_backup(record.operation_id))
            lock = _read_regular_with_identity(self.git / "index.lock")
            old_tree = parent_tree if record.action == "commit" else receipt.tree_sha
            if (
                backup is None
                or lock is None
                or backup[1] != record.index_before_identity
                or hashlib.sha256(backup[0]).hexdigest()
                != record.index_before_sha256
                or self._tree_from_index_bytes(backup[0]) != old_tree
                or lock[1] != record.index_identity
                or hashlib.sha256(lock[0]).hexdigest() != record.index_sha256
            ):
                return "conflict"
            index_tree = old_tree
        if head == receipt.parent_sha and index_tree == parent_tree:
            return "parent_parent"
        if head == receipt.parent_sha and index_tree == receipt.tree_sha:
            return "parent_commit"
        if head == receipt.commit_sha and index_tree == receipt.tree_sha:
            return "commit_commit"
        if head == receipt.commit_sha and index_tree == parent_tree:
            return "commit_parent"
        return "conflict"

    def _assert_repository_state(
        self,
        *,
        branch: str,
        allowed_heads: Sequence[str],
        expected_paths: tuple[str, ...],
        require_applied_status: bool,
    ) -> None:
        self._assert_repository_layout_and_branch(branch)
        if self._git_text("rev-parse", "--verify", "HEAD") not in allowed_heads:
            raise HostCommitError("head_changed")
        if (self.git / "index.lock").exists():
            raise HostCommitError("index_busy")
        if self._real_index_tree() != self._tree_for_commit(allowed_heads[0]):
            raise HostCommitError("index_changed")
        if require_applied_status:
            paths = self._status_paths()
            if paths != expected_paths:
                raise HostCommitError("target_changed")

    def _assert_repository_layout_and_branch(self, branch: str) -> None:
        self._validate_repository_layout()
        if self._git_text("symbolic-ref", "--quiet", "HEAD") != f"refs/heads/{branch}":
            raise HostCommitError("branch_changed")
        self._assert_no_dangerous_config()

    def _assert_post_commit(self, receipt: CommitReceipt) -> None:
        self._assert_repository_layout_and_branch(receipt.branch)
        if (
            self._git_text("rev-parse", "--verify", "HEAD") != receipt.commit_sha
            or self._real_index_tree() != receipt.tree_sha
            or self._status_paths()
        ):
            raise HostCommitError("commit_conflict")

    def _assert_applied_status(
        self,
        apply_receipt: ApplyReceipt,
        *,
        record: HostOperationRecord | None = None,
    ) -> None:
        self._assert_status_paths(
            tuple(item.path for item in apply_receipt.files),
            "undo_conflict",
            index_path=(self._operation_status_index(record) if record is not None else None),
        )

    def _assert_status_paths(
        self,
        expected: tuple[str, ...],
        code: str,
        *,
        index_path: Path | None = None,
    ) -> None:
        if self._status_paths(index_path=index_path) != tuple(sorted(expected)):
            raise HostCommitError(code)

    def _status_paths(self, *, index_path: Path | None = None) -> tuple[str, ...]:
        raw = self._git(
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            index_path=index_path,
        ).stdout
        paths: list[str] = []
        try:
            for entry in raw.split(b"\0"):
                if not entry:
                    continue
                if len(entry) < 4 or entry[2:3] != b" ":
                    raise ValueError
                paths.append(entry[3:].decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HostCommitError("repository_unsafe") from exc
        try:
            normalized = tuple(
                DraftWorkspace.normalize_relative_path(path) for path in paths
            )
        except DraftPolicyError as exc:
            raise HostCommitError("repository_unsafe") from exc
        return tuple(sorted(normalized))

    def _operation_status_index(self, record: HostOperationRecord) -> Path | None:
        if (self.git / "index").exists():
            return None
        backup = self._index_backup(record.operation_id)
        return backup if backup.exists() else None

    @contextlib.contextmanager
    def _guard_applied_files(
        self,
        receipt: ApplyReceipt,
        identities: tuple[str, ...],
    ):
        identity_map = _identity_map(identities)
        contents: dict[str, bytes] = {}
        try:
            with contextlib.ExitStack() as stack:
                for item in receipt.files:
                    target = self.root / PurePosixPath(item.path)
                    expected_identity = identity_map[item.path]
                    if item.after_sha256 is None:
                        if target.exists() or expected_identity != "missing":
                            raise HostCommitError("target_changed")
                        continue
                    content = read_regular(target)
                    if (
                        content is None
                        or hashlib.sha256(content).hexdigest() != item.after_sha256
                        or expected_identity == "missing"
                    ):
                        raise HostCommitError("target_changed")
                    stack.enter_context(
                        _guard_exact_regular_object(
                            target,
                            content,
                            expected_identity=expected_identity,
                        )
                    )
                    contents[item.path] = content
                yield contents
                for item in receipt.files:
                    if item.after_sha256 is None and (self.root / PurePosixPath(item.path)).exists():
                        raise HostCommitError("target_changed")
        except HostFileTransactionError as exc:
            raise HostCommitError("target_changed") from exc

    def _applied_record(
        self,
        receipt: ApplyReceipt,
        branch: str,
        expected_head: str,
    ) -> HostOperationRecord:
        try:
            record = self.journal.get(receipt.apply_id)
        except HostOperationLogError as exc:
            raise HostCommitError(exc.code) from exc
        if (
            record is None
            or record.action != "apply"
            or record.state != "applied"
            or record.project_id != self.project_id
            or record.revision != receipt.revision
            or record.branch != branch
            or record.expected_head != expected_head
            or record.apply_receipt != _apply_receipt_dict(receipt)
            or not record.file_identities
        ):
            raise HostCommitError("apply_receipt_invalid")
        return record

    def _committed_record(
        self,
        receipt: CommitReceipt,
        apply_receipt: ApplyReceipt,
        branch: str,
    ) -> HostOperationRecord:
        try:
            record = self.journal.get(receipt.commit_id)
        except HostOperationLogError as exc:
            raise HostCommitError(exc.code) from exc
        if (
            record is None
            or record.action != "commit"
            or record.state != "committed"
            or record.project_id != self.project_id
            or record.branch != branch
            or record.apply_receipt != _apply_receipt_dict(apply_receipt)
            or record.commit_receipt != _commit_receipt_dict(receipt)
            or not record.file_identities
        ):
            raise HostCommitError("commit_receipt_invalid")
        return record

    def _assert_commit_receipt(
        self,
        receipt: CommitReceipt,
        apply_receipt: ApplyReceipt,
        branch: str,
    ) -> None:
        if (
            receipt.branch != branch
            or receipt.revision != apply_receipt.revision
            or receipt.apply_id != apply_receipt.apply_id
            or receipt.files != tuple(item.path for item in apply_receipt.files)
        ):
            raise HostCommitError("commit_receipt_invalid")

    def _head_file(self, head: str, path: str) -> tuple[str, bytes | None]:
        mode, object_id = self._head_entry(head, path)
        if object_id is None:
            return mode, None
        return mode, self._git("cat-file", "blob", object_id).stdout

    def _head_entry(self, head: str, path: str) -> tuple[str, str | None]:
        raw = self._git("ls-tree", "-z", head, "--", path).stdout
        if not raw:
            return "100644", None
        entries = [entry for entry in raw.split(b"\0") if entry]
        if len(entries) != 1:
            raise HostCommitError("repository_unsafe")
        try:
            metadata, encoded_path = entries[0].split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            actual_path = encoded_path.decode("utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError) as exc:
            raise HostCommitError("repository_unsafe") from exc
        if actual_path != path or kind != "blob" or mode not in {"100644", "100755"}:
            raise HostCommitError("repository_unsafe")
        return mode, _object_id(object_id)

    def _tree_for_commit(self, commit: str) -> str:
        tree = self._git_text("rev-parse", "--verify", f"{commit}^{{tree}}")
        return _object_id(tree)

    def _tree_from_index_bytes(self, content: bytes) -> str:
        descriptor, name = tempfile.mkstemp(prefix="modelmirror-index-check-")
        index = Path(name)
        try:
            _write_all(descriptor, content)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            with tempfile.TemporaryDirectory(
                prefix="modelmirror-index-objects-"
            ) as object_directory_name:
                return self._git_text(
                    "write-tree",
                    index_path=index,
                    object_directory=Path(object_directory_name),
                    alternate_object_directories=(self.git / "objects",),
                )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            index.unlink(missing_ok=True)

    def _real_index_tree(self) -> str:
        current = read_regular(self.git / "index")
        if current is None:
            raise HostCommitError("index_changed")
        return self._tree_from_index_bytes(current)

    def _copy_current_index(self, expected_tree: str) -> tuple[Path, bytes, str]:
        current = _read_regular_with_identity(self.git / "index")
        if current is None or self._tree_from_index_bytes(current[0]) != expected_tree:
            raise HostCommitError("index_changed")
        descriptor, name = tempfile.mkstemp(prefix="modelmirror-index-")
        index = Path(name)
        try:
            _write_all(descriptor, current[0])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            return index, current[0], current[1]
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            index.unlink(missing_ok=True)
            raise

    def _prepare_undo_index(
        self,
        *,
        apply_receipt: ApplyReceipt,
        parent_sha: str,
        current_tree: str,
        parent_tree: str,
    ) -> tuple[Path, bytes, str]:
        index, source, source_identity = self._copy_current_index(current_tree)
        try:
            for item in apply_receipt.files:
                mode, object_id = self._head_entry(parent_sha, item.path)
                if object_id is None:
                    self._git(
                        "update-index",
                        "--force-remove",
                        "--",
                        item.path,
                        index_path=index,
                    )
                else:
                    self._git(
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        mode,
                        object_id,
                        item.path,
                        index_path=index,
                    )
            if self._git_text("write-tree", index_path=index) != parent_tree:
                raise HostCommitError("index_changed")
            return index, source, source_identity
        except BaseException:
            index.unlink(missing_ok=True)
            raise

    def _validate_repository_layout(self) -> None:
        if self.enforce_windows and os.name != "nt":
            raise HostCommitError("windows_required")
        if self.enforce_windows:
            if str(self.root).startswith("\\\\") or not self.root.drive:
                raise HostCommitError("network_path_not_allowed")
            import ctypes

            if ctypes.windll.kernel32.GetDriveTypeW(
                ctypes.c_wchar_p(f"{self.root.drive}\\")
            ) == 4:
                raise HostCommitError("network_path_not_allowed")
        if _is_link_or_reparse(self.root) or not self.root.is_dir():
            raise HostCommitError("project_path_invalid")
        if _is_link_or_reparse(self.git) or not self.git.is_dir():
            raise HostCommitError("repository_unsafe")
        required_directories = (
            self.git / "objects",
            self.git / "refs",
            self.git / "refs" / "heads",
        )
        for directory in required_directories:
            if not directory.is_dir() or _is_link_or_reparse(directory):
                raise HostCommitError("repository_unsafe")
        for directory in (
            self.git / "logs",
            self.git / "logs" / "refs",
            self.git / "logs" / "refs" / "heads",
        ):
            if _path_entry_exists(directory) and (
                not directory.is_dir() or _is_link_or_reparse(directory)
            ):
                raise HostCommitError("repository_unsafe")
        for directory in (
            self.git / "objects" / "info",
            self.git / "info",
        ):
            if _path_entry_exists(directory) and (
                not directory.is_dir() or _is_link_or_reparse(directory)
            ):
                raise HostCommitError("repository_unsafe")
        for metadata in (self.git / "config", self.git / "HEAD"):
            if not metadata.is_file() or _is_link_or_reparse(metadata):
                raise HostCommitError("repository_unsafe")
        index = self.git / "index"
        if _is_link_or_reparse(index) or index.exists() and not index.is_file():
            raise HostCommitError("repository_unsafe")
        packed_refs = self.git / "packed-refs"
        if _path_entry_exists(packed_refs) and (
            not packed_refs.is_file() or _is_link_or_reparse(packed_refs)
        ):
            raise HostCommitError("repository_unsafe")
        self._assert_repository_redirections_absent()

    def _assert_no_dangerous_config(self) -> None:
        result = self._git(
            "config",
            "--local",
            "--no-includes",
            "--name-only",
            "--get-regexp",
            _DANGEROUS_CONFIG,
            allowed=(0, 1),
            discard_output=True,
        )
        if result.returncode == 0:
            raise HostCommitError("repository_config_unsafe")

    def _assert_no_dangerous_config_file(self, config: Path) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="modelmirror-git-config-") as safe_cwd:
                result = subprocess.run(
                    build_safe_git_command(
                        self.root,
                        (
                            "config",
                            "--file",
                            str(config),
                            "--no-includes",
                            "--name-only",
                            "--get-regexp",
                            _DANGEROUS_CONFIG,
                        ),
                    ),
                    cwd=safe_cwd,
                    env=build_safe_git_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=GIT_TIMEOUT_SECONDS,
                    creationflags=(
                        getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        if os.name == "nt"
                        else 0
                    ),
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostCommitError("repository_unsafe") from exc
        if result.returncode == 0:
            raise HostCommitError("repository_config_unsafe")
        if result.returncode != 1:
            raise HostCommitError("repository_unsafe")

    def _assert_repository_redirections_absent(self) -> None:
        for path in (
            self.git / "commondir",
            self.git / "objects" / "info" / "alternates",
            self.git / "objects" / "info" / "http-alternates",
            self.git / "refs" / "replace",
            self.git / "info" / "grafts",
        ):
            if _path_entry_exists(path):
                raise HostCommitError("repository_unsafe")

    def _assert_object_namespace_safe(self) -> None:
        _safe_git_namespace(self.git / "objects")

    @contextlib.contextmanager
    def _private_object_store(self, record: HostOperationRecord):
        transaction_root = self.git / "modelmirror-transactions"
        root = transaction_root / f"{record.operation_id}.private-objects"
        owner = root / PRIVATE_OBJECT_OWNER_FILE
        owner_content = f"{record.operation_id}\n".encode("ascii")
        try:
            with _guard_directories((transaction_root,)):
                if not _path_entry_exists(root):
                    os.mkdir(root, 0o700)
                if not root.is_dir() or _is_link_or_reparse(root):
                    raise HostCommitError("repository_unsafe")
                _write_durable_no_replace(owner, owner_content, 0o600)
                if read_regular(owner) != owner_content:
                    raise HostCommitError("repository_unsafe")
                _safe_git_namespace(root)
            try:
                yield root
            finally:
                self._cleanup_private_object_store(
                    root,
                    owner_content=owner_content,
                )
        except (HostApplyError, HostFileTransactionError, OSError) as exc:
            raise HostCommitError("repository_unsafe") from exc

    def _cleanup_private_object_store(
        self,
        root: Path,
        *,
        owner_content: bytes,
    ) -> None:
        if not root.is_dir() or _is_link_or_reparse(root):
            raise HostCommitError("repository_unsafe")
        _safe_git_namespace(root)
        root_identity = file_identity(root)
        owner = root / PRIVATE_OBJECT_OWNER_FILE
        owner_record = _read_regular_with_identity(owner)
        if owner_record is None or owner_record[0] != owner_content:
            raise HostCommitError("repository_unsafe")
        fanouts = tuple(
            path for path in root.iterdir() if path.name != PRIVATE_OBJECT_OWNER_FILE
        )
        for fanout in fanouts:
            if (
                re.fullmatch(r"[a-f0-9]{2}", fanout.name) is None
                or not fanout.is_dir()
                or _is_link_or_reparse(fanout)
            ):
                raise HostCommitError("repository_unsafe")
            fanout_identity = file_identity(fanout)
            for entry in tuple(fanout.iterdir()):
                object_id = fanout.name + entry.name
                current = _read_regular_with_identity(entry)
                if current is None or _OBJECT_ID.fullmatch(object_id) is None:
                    raise HostCommitError("repository_unsafe")
                _validate_loose_object(object_id, current[0])
                if os.name == "nt":
                    _windows_remove_private_loose_object(
                        entry,
                        current[0],
                        expected_identity=current[1],
                    )
                else:
                    remove_regular_exact(
                        entry,
                        current[0],
                        expected_identity=current[1],
                    )
            _remove_empty_directory_exact(fanout, fanout_identity)
        remove_regular_exact(
            owner,
            owner_record[0],
            expected_identity=owner_record[1],
        )
        _remove_empty_directory_exact(root, root_identity)

    def _publish_private_objects(self, private_root: Path) -> None:
        if (
            not private_root.is_absolute()
            or not private_root.is_dir()
            or _is_link_or_reparse(private_root)
        ):
            raise HostCommitError("repository_unsafe")
        _safe_git_namespace(private_root)
        objects: list[tuple[str, bytes]] = []
        try:
            fanouts = tuple(private_root.iterdir())
        except OSError as exc:
            raise HostCommitError("repository_unsafe") from exc
        for fanout in fanouts:
            if fanout.name == PRIVATE_OBJECT_OWNER_FILE:
                owner = read_regular(fanout)
                if owner is None or re.fullmatch(rb"[A-Za-z0-9_-]{20,64}\n", owner) is None:
                    raise HostCommitError("repository_unsafe")
                continue
            if (
                re.fullmatch(r"[a-f0-9]{2}", fanout.name) is None
                or not fanout.is_dir()
                or _is_link_or_reparse(fanout)
            ):
                raise HostCommitError("repository_unsafe")
            try:
                entries = tuple(fanout.iterdir())
            except OSError as exc:
                raise HostCommitError("repository_unsafe") from exc
            for entry in entries:
                object_id = fanout.name + entry.name
                if (
                    _OBJECT_ID.fullmatch(object_id) is None
                    or not entry.is_file()
                    or _is_link_or_reparse(entry)
                ):
                    raise HostCommitError("repository_unsafe")
                content = read_regular(entry)
                if content is None or len(content) > MAX_PRIVATE_OBJECT_BYTES:
                    raise HostCommitError("repository_unsafe")
                _validate_loose_object(object_id, content)
                objects.append((object_id, content))
        if len(objects) > MAX_PRIVATE_OBJECTS:
            raise HostCommitError("repository_unsafe")
        real_root = self.git / "objects"
        try:
            with _guard_directories((real_root,)):
                self._assert_repository_redirections_absent()
                self._assert_object_namespace_safe()
                for object_id, content in sorted(objects):
                    fanout = real_root / object_id[:2]
                    if not _path_entry_exists(fanout):
                        try:
                            os.mkdir(fanout, 0o700)
                        except FileExistsError:
                            pass
                        except OSError as exc:
                            raise HostCommitError("repository_unsafe") from exc
                    if not fanout.is_dir() or _is_link_or_reparse(fanout):
                        raise HostCommitError("repository_unsafe")
                    self._notify("commit_before_object_file_publish")
                    with _guard_directories((fanout,)):
                        self._assert_repository_redirections_absent()
                        target = fanout / object_id[2:]
                        _write_durable_no_replace(target, content, 0o444)
                        published = _read_regular_with_identity(target)
                        if published is None or published[0] != content:
                            raise HostCommitError("repository_unsafe")
                        _assert_single_link_regular(target)
                self._assert_repository_redirections_absent()
                self._assert_object_namespace_safe()
        except (HostApplyError, HostFileTransactionError) as exc:
            raise HostCommitError("repository_unsafe") from exc

    def _assert_generated_objects_available(
        self,
        *,
        tree: str,
        commit_sha: str,
        parent_sha: str,
    ) -> None:
        self._assert_repository_redirections_absent()
        self._assert_object_namespace_safe()
        if (
            self._git_text("cat-file", "-t", tree) != "tree"
            or self._git_text("cat-file", "-t", commit_sha) != "commit"
            or self._git_text("rev-parse", "--verify", f"{commit_sha}^{{tree}}")
            != tree
            or self._git_text("rev-parse", "--verify", f"{commit_sha}^")
            != parent_sha
        ):
            raise HostCommitError("repository_unsafe")

    @contextlib.contextmanager
    def _guard_current_object_namespace(self):
        directories = _safe_git_namespace(self.git / "objects")
        try:
            with _guard_directories(directories):
                self._assert_repository_redirections_absent()
                yield
                self._assert_repository_redirections_absent()
                self._assert_object_namespace_safe()
        except HostApplyError as exc:
            raise HostCommitError("repository_unsafe") from exc

    def _assert_reflog_single_link(self, branch: str) -> None:
        path = self.git / "logs" / "refs" / "heads" / PurePosixPath(branch)
        try:
            metadata = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise HostCommitError("repository_unsafe") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise HostCommitError("repository_unsafe")

    def _git_text(self, *arguments: str, **kwargs: object) -> str:
        return self._git(*arguments, **kwargs).stdout.decode("utf-8", errors="strict").strip()

    def _git(
        self,
        *arguments: str,
        index_path: Path | None = None,
        input_bytes: bytes | None = None,
        extra_environment: dict[str, str] | None = None,
        object_directory: Path | None = None,
        alternate_object_directories: tuple[Path, ...] = (),
        allowed: Sequence[int] = (0,),
        discard_output: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        self._assert_repository_redirections_absent()
        real_objects = self.git / "objects"
        selected_objects = Path(object_directory or real_objects)
        if (
            not selected_objects.is_absolute()
            or not selected_objects.is_dir()
            or _is_link_or_reparse(selected_objects)
            or any(Path(path) != real_objects for path in alternate_object_directories)
        ):
            raise HostCommitError("repository_unsafe")
        environment = build_safe_git_environment()
        if extra_environment:
            environment.update(extra_environment)
        environment.update(
            {
                "GIT_AUTHOR_NAME": self.author_name,
                "GIT_AUTHOR_EMAIL": self.author_email,
                "GIT_COMMITTER_NAME": self.author_name,
                "GIT_COMMITTER_EMAIL": self.author_email,
                "GIT_DIR": str(self.git),
                "GIT_WORK_TREE": str(self.root),
                "GIT_COMMON_DIR": str(self.git),
                "GIT_OBJECT_DIRECTORY": str(selected_objects),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(
                    str(Path(path)) for path in alternate_object_directories
                ),
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
            }
        )
        if index_path is not None:
            environment["GIT_INDEX_FILE"] = str(index_path)
        try:
            safe_arguments = (
                "-c",
                "commit.gpgSign=false",
                "-c",
                "tag.gpgSign=false",
                "-c",
                "core.logAllRefUpdates=false",
                "--literal-pathspecs",
                *arguments,
            )
            result = subprocess.run(
                build_safe_git_command(self.root, safe_arguments),
                cwd=self.root,
                env=environment,
                stdin=None if input_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if discard_output else subprocess.PIPE,
                stderr=subprocess.PIPE,
                input=input_bytes,
                check=False,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostCommitError("git_unavailable") from exc
        if result.returncode not in allowed:
            raise HostCommitError("git_operation_failed")
        return result

    def _transition(
        self,
        operation_id: str,
        state: str,
        **kwargs: object,
    ) -> HostOperationRecord:
        try:
            return self.journal.transition(operation_id, state, **kwargs)
        except HostOperationLogError as exc:
            raise HostCommitError(exc.code) from exc

    def _mark_conflict(self, operation_id: str, code: str) -> None:
        marker = self._conflict_marker(operation_id)
        marker_content = f"{operation_id}\n".encode("ascii")
        marker_ready = False
        try:
            current_marker = _read_regular_with_identity(marker)
            if current_marker is None:
                _write_durable_no_replace(marker, marker_content, 0o600)
                current_marker = _read_regular_with_identity(marker)
            marker_ready = current_marker is not None and current_marker[0] == marker_content
        except (HostFileTransactionError, OSError, RuntimeError):
            # The encrypted journal is an independent fail-closed channel.  A
            # marker failure must not prevent a durable conflict transition.
            marker_ready = False
        try:
            record = self.journal.get(operation_id)
            if record is None:
                raise HostCommitError("operation_log_unavailable")
            if not marker_ready:
                # With no filesystem fence, persist the journal fence before
                # any ref/index cleanup.  Conflict reconciliation will retry
                # cleanup idempotently if this process stops afterwards.
                record = self.journal.transition(operation_id, "conflict")
            self._settle_conflict(record, code=code)
            if marker_ready:
                self.journal.transition(operation_id, "conflict")
        except HostCommitError:
            raise
        except (HostOperationLogError, OSError, RuntimeError) as exc:
            raise HostCommitError("operation_log_unavailable") from exc

    def _settle_conflict(
        self,
        record: HostOperationRecord,
        *,
        code: str | None = None,
    ) -> None:
        conflict_code = code or (
            "commit_conflict" if record.action == "commit" else "undo_conflict"
        )
        self._recover_reflogs(record)
        self._rollback_visible_ref(record, conflict_code)
        self._quarantine_owned_index_artifact(record)

    def _conflict_marker_present(self, operation_id: str) -> bool:
        marker = self._conflict_marker(operation_id)
        current = _read_regular_with_identity(marker)
        if current is None:
            return False
        if current[0] != f"{operation_id}\n".encode("ascii"):
            raise HostCommitError("operation_log_unavailable")
        return True

    def _rollback_visible_ref(self, record: HostOperationRecord, code: str) -> None:
        """Best-effort CAS rollback when our ref moved but index did not.

        External changes are never overwritten: rollback is attempted only
        while the current branch, ref and logical index still match the exact
        operation-owned transition.  Namespace failures simply leave the task
        in the durable read-only conflict state.
        """

        if (
            code not in _PERSISTED_CONFLICT_CODES
            or record.commit_receipt is None
            or record.index_before_identity is None
            or record.index_before_sha256 is None
        ):
            return
        receipt = _commit_receipt(record.commit_receipt)
        if record.action == "commit":
            visible_head = receipt.commit_sha
            restored_head = receipt.parent_sha
            old_tree: str | None = None
        elif record.action == "undo":
            visible_head = receipt.parent_sha
            restored_head = receipt.commit_sha
            old_tree = receipt.tree_sha
        else:
            return
        try:
            with self._parked_reflogs(record), self._git_write_guards(
                record.branch,
                parked_record=record,
            ):
                if old_tree is None:
                    old_tree = self._tree_for_commit(receipt.parent_sha)
                current_head = self._git_text("rev-parse", "--verify", "HEAD")
                if current_head == restored_head:
                    return
                if current_head != visible_head:
                    return
                current_index = _read_regular_with_identity(self.git / "index")
                using_backup = current_index is None
                if current_index is None:
                    current_index = _read_regular_with_identity(
                        self._index_backup(record.operation_id)
                    )
                if (
                    current_index is None
                    or using_backup
                    and current_index[1] != record.index_before_identity
                    or using_backup
                    and hashlib.sha256(current_index[0]).hexdigest()
                    != record.index_before_sha256
                    or self._tree_from_index_bytes(current_index[0]) != old_tree
                ):
                    return
                with self._guard_current_object_namespace():
                    self._update_ref_without_reflog(
                        record=record,
                        new_head=restored_head,
                        old_head=visible_head,
                        message="ModelMirror controlled host operation rollback",
                    )
        except HostCommitError:
            return

    def _quarantine_owned_index_artifact(self, record: HostOperationRecord) -> None:
        if (
            record.index_sha256 is None
            or record.index_before_sha256 is None
            or record.index_identity is None
        ):
            return
        backup = self._index_backup(record.operation_id)
        parked = _read_regular_with_identity(backup)
        index = self.git / "index"
        if not index.exists():
            if (
                parked is None
                or parked[1] != record.index_before_identity
                or hashlib.sha256(parked[0]).hexdigest()
                != record.index_before_sha256
            ):
                raise HostCommitError("operation_log_unavailable")
            try:
                _move_verified_no_replace(
                    backup,
                    index,
                    parked[0],
                    expected_identity=record.index_before_identity,
                )
            except HostFileTransactionError as exc:
                raise HostCommitError("operation_log_unavailable") from exc
            restored = _read_regular_with_identity(index)
            if (
                restored is None
                or restored[1] != record.index_before_identity
                or hashlib.sha256(restored[0]).hexdigest()
                != record.index_before_sha256
            ):
                raise HostCommitError("operation_log_unavailable")
        stage = self._index_stage(record.operation_id)
        lock = self.git / "index.lock"
        destination = self._index_conflict_artifact(record.operation_id)
        existing_destination = _read_regular_with_identity(destination)
        for source in (stage, lock):
            current = _read_regular_with_identity(source)
            if current is None:
                continue
            if (
                current[1] != record.index_identity
                or hashlib.sha256(current[0]).hexdigest() != record.index_sha256
            ):
                # Never remove or move an index lock that is not provably ours.
                continue
            if existing_destination is not None:
                if existing_destination != current:
                    raise HostCommitError("operation_log_unavailable")
                try:
                    remove_regular_exact(
                        source,
                        current[0],
                        expected_identity=record.index_identity,
                    )
                except HostFileTransactionError as exc:
                    raise HostCommitError("operation_log_unavailable") from exc
                continue
            try:
                _move_verified_no_replace(
                    source,
                    destination,
                    current[0],
                    expected_identity=record.index_identity,
                )
            except HostFileTransactionError as exc:
                raise HostCommitError("operation_log_unavailable") from exc
            existing_destination = current

    def _conflict(self, operation_id: str, code: str):
        self._mark_conflict(operation_id, code)
        raise HostCommitError(code)

    def _notify(self, phase: str) -> None:
        if self.mutation_hook is not None:
            self.mutation_hook(phase)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise HostCommitError("repository_unsafe") from exc
    return True


def _assert_single_link_regular(path: Path) -> None:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise HostCommitError("repository_unsafe") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_link_or_reparse(path)
        or metadata.st_nlink != 1
    ):
        raise HostCommitError("repository_unsafe")


def _windows_remove_private_loose_object(
    path: Path,
    expected: bytes,
    *,
    expected_identity: str,
) -> None:
    """Delete one operation-owned loose object through one no-follow handle.

    Git marks loose objects read-only on Windows.  Clearing that bit by path
    would let a concurrent pathname replacement redirect the attribute change
    outside the private object store.  Keep identity, bytes, link count,
    attribute update and delete disposition bound to the same handle instead.
    """

    if os.name != "nt":
        raise HostFileTransactionError("transaction_cleanup_failed")

    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    file_write_attributes = 0x00000100
    file_share_read = 0x00000001
    file_basic_info = 0
    file_disposition_info = 4
    file_attribute_readonly = 0x00000001
    file_attribute_normal = 0x00000080

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

    class _FileBasicInformation(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_longlong),
            ("access_time", ctypes.c_longlong),
            ("write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("attributes", wintypes.DWORD),
        ]

    class _FileDispositionInformation(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL

    handle = _windows_open_existing(
        path,
        access=(
            generic_read
            | delete_access
            | file_read_attributes
            | file_write_attributes
        ),
        # Do not share write or delete access: once this handle opens, the
        # verified pathname cannot be replaced before its handle-bound delete.
        share=file_share_read,
        allow_missing=False,
    )
    assert handle is not None

    def inspect() -> tuple[str, int]:
        identity = _windows_handle_identity(handle, require_directory=False)
        information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(information),
        ):
            raise HostFileTransactionError("transaction_conflict")
        if information.links != 1 or identity != expected_identity:
            raise HostFileTransactionError("transaction_conflict")
        if _windows_read_all(handle) != expected:
            raise HostFileTransactionError("transaction_conflict")
        return identity, int(information.attributes)

    try:
        identity, attributes = inspect()
        if attributes & file_attribute_readonly:
            updated_attributes = attributes & ~file_attribute_readonly
            if updated_attributes == 0:
                updated_attributes = file_attribute_normal
            basic = _FileBasicInformation(attributes=updated_attributes)
            if not kernel32.SetFileInformationByHandle(
                handle,
                file_basic_info,
                ctypes.byref(basic),
                ctypes.sizeof(basic),
            ):
                raise HostFileTransactionError("transaction_cleanup_failed")
        verified_identity, _verified_attributes = inspect()
        if verified_identity != identity:
            raise HostFileTransactionError("transaction_conflict")
        disposition = _FileDispositionInformation(True)
        if not kernel32.SetFileInformationByHandle(
            handle,
            file_disposition_info,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise HostFileTransactionError("transaction_cleanup_failed")
    finally:
        _windows_close_handle(handle)

    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise HostFileTransactionError("transaction_cleanup_failed") from exc
    raise HostFileTransactionError("transaction_cleanup_failed")


def _validate_loose_object(object_id: str, compressed: bytes) -> None:
    try:
        inflater = zlib.decompressobj()
        payload = inflater.decompress(
            compressed,
            MAX_PRIVATE_OBJECT_CONTENT_BYTES + 1,
        )
        if (
            len(payload) > MAX_PRIVATE_OBJECT_CONTENT_BYTES
            or inflater.unconsumed_tail
            or not inflater.eof
            or inflater.unused_data
        ):
            raise ValueError("invalid loose object")
        header, body = payload.split(b"\0", 1)
        kind, encoded_size = header.split(b" ", 1)
        if kind not in {b"blob", b"tree", b"commit"}:
            raise ValueError("invalid loose object")
        if int(encoded_size.decode("ascii")) != len(body):
            raise ValueError("invalid loose object")
        algorithm = hashlib.sha1 if len(object_id) == 40 else hashlib.sha256
        if algorithm(payload).hexdigest() != object_id:
            raise ValueError("invalid loose object")
    except (UnicodeError, ValueError, zlib.error) as exc:
        raise HostCommitError("repository_unsafe") from exc


def _safe_git_namespace(root: Path) -> tuple[Path, ...]:
    """Reject every link/reparse/special entry before Git traverses metadata."""

    if _is_link_or_reparse(root) or not root.is_dir():
        raise HostCommitError("repository_unsafe")
    directories: list[Path] = []
    pending = [root]
    entries_seen = 0
    while pending:
        directory = pending.pop()
        if _is_link_or_reparse(directory) or not directory.is_dir():
            raise HostCommitError("repository_unsafe")
        directories.append(directory)
        try:
            children = list(os.scandir(directory))
        except OSError as exc:
            raise HostCommitError("repository_unsafe") from exc
        entries_seen += len(children)
        if entries_seen > MAX_GIT_NAMESPACE_ENTRIES:
            raise HostCommitError("repository_unsafe")
        for child in children:
            path = Path(child.path)
            try:
                if child.is_symlink() or _is_link_or_reparse(path):
                    raise HostCommitError("repository_unsafe")
                if child.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif child.is_file(follow_symlinks=False):
                    # DirEntry.stat() reports st_nlink=0 on some supported
                    # Windows/Python combinations; a fresh path stat exposes
                    # the real NTFS link count.
                    if os.stat(path, follow_symlinks=False).st_nlink != 1:
                        raise HostCommitError("repository_unsafe")
                else:
                    raise HostCommitError("repository_unsafe")
            except OSError as exc:
                raise HostCommitError("repository_unsafe") from exc
    return tuple(directories)


@contextlib.contextmanager
def _guard_mutable_regular_leaf(path: Path):
    """Hold a mutable reflog leaf without permitting path replacement."""

    try:
        initial = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise HostFileTransactionError("transaction_conflict") from exc
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise HostFileTransactionError("transaction_conflict")
    if os.name == "nt":
        handle = _windows_open_existing(
            path,
            access=0x80000000 | 0x00000080,
            # Git may append, but neither Git nor another process may replace
            # the pathname while the controlled update is in flight.
            share=0x00000001 | 0x00000002,
            allow_missing=False,
        )
        assert handle is not None
        try:
            identity = _windows_handle_identity(handle, require_directory=False)
            yield
            if _windows_handle_identity(handle, require_directory=False) != identity:
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
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise HostFileTransactionError("transaction_conflict")
        identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"
        yield
        current = file_identity(path)
        if current != identity:
            raise HostFileTransactionError("transaction_conflict")
    finally:
        os.close(descriptor)


def _validate_identity(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise HostCommitError("invalid_author")
    return value


def _branch(value: str) -> str:
    try:
        return validate_commit_branch(value)
    except ValueError as exc:
        raise HostCommitError("branch_invalid") from exc


def _object_id(value: str) -> str:
    if not isinstance(value, str) or _OBJECT_ID.fullmatch(value) is None:
        raise HostCommitError("repository_unsafe")
    return value


def _canonical_blob(baseline: bytes, working: bytes, before_sha256: str) -> bytes:
    if hashlib.sha256(baseline).hexdigest() == before_sha256:
        return working
    crlf = baseline.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if hashlib.sha256(crlf).hexdigest() == before_sha256:
        return working.replace(b"\r\n", b"\n")
    raise HostCommitError("target_changed")


def _commit_ref_transition_paths(receipt: ApplyReceipt) -> tuple[str, ...]:
    """Exact porcelain path multiset while HEAD is new and index is old.

    A newly added file is simultaneously deleted from the old index relative
    to the new HEAD and untracked in the worktree, so porcelain emits it twice.
    Other touched file states emit one record.  Comparing the exact multiset
    preserves the no-unrelated-change gate without treating this intentional
    hand-off state as a conflict.
    """

    paths = [item.path for item in receipt.files]
    paths.extend(
        item.path
        for item in receipt.files
        if not item.existed_before and item.after_sha256 is not None
    )
    return tuple(sorted(paths))


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _identity_map(values: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for value in values:
            identity, path = value.split(":", 1)
            normalized = DraftWorkspace.normalize_relative_path(path)
            if (
                normalized != path
                or path in result
                or identity != "missing" and _IDENTITY.fullmatch(identity) is None
            ):
                raise ValueError
            result[path] = identity
    except (DraftPolicyError, TypeError, ValueError) as exc:
        raise HostCommitError("apply_receipt_invalid") from exc
    return result


def _apply_receipt_dict(receipt: ApplyReceipt) -> dict[str, object]:
    return {
        "apply_id": receipt.apply_id,
        "revision": receipt.revision,
        "snapshot_fingerprint": receipt.snapshot_fingerprint,
        "files": [asdict(item) for item in receipt.files],
        "applied_at": receipt.applied_at,
    }


def _commit_receipt_dict(receipt: CommitReceipt) -> dict[str, object]:
    value = asdict(receipt)
    value["files"] = list(receipt.files)
    return value


def _apply_receipt(value: dict[str, object] | None) -> ApplyReceipt:
    if not isinstance(value, dict):
        raise HostCommitError("apply_receipt_invalid")
    try:
        files = value["files"]
        if not isinstance(files, list):
            raise TypeError
        return ApplyReceipt(
            apply_id=value["apply_id"],
            revision=value["revision"],
            snapshot_fingerprint=value["snapshot_fingerprint"],
            files=tuple(ApplyFileReceipt(**item) for item in files),
            applied_at=value["applied_at"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HostCommitError("apply_receipt_invalid") from exc


def _commit_receipt(value: dict[str, object] | None) -> CommitReceipt:
    if not isinstance(value, dict):
        raise HostCommitError("commit_receipt_invalid")
    try:
        return CommitReceipt(
            commit_id=value["commit_id"],
            revision=value["revision"],
            apply_id=value["apply_id"],
            commit_sha=value["commit_sha"],
            parent_sha=value["parent_sha"],
            tree_sha=value["tree_sha"],
            message=value["message"],
            files=tuple(value["files"]),
            branch=value["branch"],
            committed_at=value["committed_at"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HostCommitError("commit_receipt_invalid") from exc


def _optional_commit_receipt(value: dict[str, object] | None) -> CommitReceipt | None:
    return None if value is None else _commit_receipt(value)
