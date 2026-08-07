from __future__ import annotations

import contextlib
import ctypes
import hashlib
import functools
import os
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.draft_workspace import DraftLimits, DraftPolicyError, DraftWorkspace
from server.coding_runtime.patch_policy import PatchPolicyError, validate_patch
from server.coding_runtime.projects import build_safe_git_command, build_safe_git_environment

from .host_file_transaction import (
    FileMutation,
    HostFileTransaction,
    HostFileTransactionError,
    file_identity,
    move_directory_no_replace,
    read_regular,
    remove_regular_exact,
)
from .operation_log import HostOperationJournal, HostOperationLogError


GIT_TIMEOUT_SECONDS = 30
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
MutationHook = Callable[[str, int, str], None]


class HostApplyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _serialize_project_operation(method):
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        # Direct host writeback is a Windows-only product capability.  POSIX is
        # used solely by isolated domain tests and must fail before creating a
        # project lock or any other host-side artifact in production mode.
        if self.enforce_windows and os.name != "nt":
            raise HostApplyError("windows_required")
        with _project_process_lock(self.root):
            return method(self, *args, **kwargs)

    return wrapped


@contextlib.contextmanager
def _project_process_lock(root: Path):
    git = root / ".git"
    if (
        _is_link_or_reparse(root)
        or not root.is_dir()
        or _is_link_or_reparse(git)
        or not git.is_dir()
    ):
        raise HostApplyError("git_metadata_unsafe")
    with _guard_directories((root, git)):
        directory = git / "modelmirror-transactions"
        if directory.exists():
            if _is_link_or_reparse(directory) or not directory.is_dir():
                raise HostApplyError("transaction_conflict")
        else:
            try:
                directory.mkdir()
            except FileExistsError:
                pass
            if _is_link_or_reparse(directory) or not directory.is_dir():
                raise HostApplyError("transaction_conflict")
        with _guard_directories((directory,)):
            lock_path = directory / ".project-operation.lock"
            try:
                stream = _open_project_lock_stream(lock_path)
            except OSError as exc:
                raise HostApplyError("transaction_conflict") from exc
            deadline = time.monotonic() + 5.0
            with stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt

                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(
                                stream.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                        break
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise HostApplyError("project_operation_busy") from exc
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _open_project_lock_stream(path: Path):
    if os.name != "nt":
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            os.close(descriptor)
            raise OSError("unsafe project lock")
        return os.fdopen(descriptor, "r+b")

    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    create_new = 1
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    error_file_exists = 80
    error_already_exists = 183
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
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value

    handle = kernel32.CreateFileW(
        str(path),
        generic_read | generic_write,
        file_share_read | file_share_write,
        None,
        create_new,
        file_flag_open_reparse_point,
        None,
    )
    if int(handle) == invalid:
        error = ctypes.get_last_error()
        if error not in {error_file_exists, error_already_exists}:
            raise OSError(error, "project lock create failed")
        handle = kernel32.CreateFileW(
            str(path),
            generic_read | generic_write,
            file_share_read | file_share_write,
            None,
            open_existing,
            file_flag_open_reparse_point,
            None,
        )
    if int(handle) == invalid:
        raise OSError(ctypes.get_last_error(), "project lock open failed")
    try:
        information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(information),
        ):
            raise OSError(ctypes.get_last_error(), "project lock inspect failed")
        if (
            information.attributes
            & (file_attribute_directory | file_attribute_reparse_point)
            or information.links != 1
        ):
            raise OSError("unsafe project lock")
        descriptor = msvcrt.open_osfhandle(
            int(handle),
            os.O_RDWR | getattr(os, "O_BINARY", 0),
        )
        handle = None
        return os.fdopen(descriptor, "r+b")
    finally:
        if handle is not None:
            kernel32.CloseHandle(handle)


@dataclass(frozen=True, slots=True)
class _PlannedFile:
    path: str
    before: bytes | None
    after: bytes | None
    mode: int

    @property
    def before_sha256(self) -> str | None:
        return _sha256(self.before) if self.before is not None else None

    @property
    def after_sha256(self) -> str | None:
        return _sha256(self.after) if self.after is not None else None


class HostGitApplyEngine:
    """Apply one bounded text Patch to the helper-owned Git project.

    The engine never enumerates remotes and never accepts a command or path from
    the browser. It only inspects Git metadata plus files named by a validated
    Patch, which avoids the latency and false positives of a whole-tree scan.
    """

    def __init__(
        self,
        project_root: Path,
        project_id: str,
        journal: HostOperationJournal,
        *,
        limits: DraftLimits | None = None,
        mutation_hook: MutationHook | None = None,
        enforce_windows: bool = True,
    ) -> None:
        root = Path(project_root)
        if (
            not root.is_absolute()
            or _is_link_or_reparse(root)
            or not root.is_dir()
        ):
            raise HostApplyError("project_path_invalid")
        self.root = root.resolve(strict=True)
        self.project_id = project_id
        self.journal = journal
        self.limits = limits or DraftLimits()
        self.mutation_hook = mutation_hook
        self.enforce_windows = enforce_windows
        self._lock = threading.Lock()
        self._validate_repository_layout()

    @_serialize_project_operation
    def apply(
        self,
        *,
        operation_id: str,
        revision: int,
        branch: str,
        expected_head: str,
        snapshot_fingerprint: str,
        patch: str,
        paths: Sequence[str],
    ) -> ApplyReceipt:
        safe_paths = self._validate_patch(patch, paths)
        patch_sha256 = _sha256(patch.encode("utf-8"))
        with self._lock:
            try:
                record = self.journal.create(
                    operation_id=operation_id,
                    action="apply",
                    project_id=self.project_id,
                    revision=revision,
                    branch=branch,
                    expected_head=expected_head,
                    patch_sha256=patch_sha256,
                    patch=patch,
                )
            except HostOperationLogError as exc:
                raise HostApplyError(exc.code) from exc
            if record.state == "conflict":
                raise HostApplyError("apply_conflict")
            existing_receipt = (
                _apply_receipt(record.apply_receipt)
                if record.apply_receipt is not None
                else None
            )
            if (
                existing_receipt is not None
                and existing_receipt.snapshot_fingerprint != snapshot_fingerprint
            ):
                raise HostApplyError("operation_conflict")
            if record.state == "applied" and existing_receipt is not None:
                self._assert_file_identities(
                    record.file_identities,
                    existing_receipt,
                    applied=True,
                )
            plan = self._build_plan(
                patch=patch,
                paths=safe_paths,
                branch=branch,
                expected_head=expected_head,
                apply_receipt=existing_receipt,
                owned_operation_id=operation_id,
            )
            receipt = existing_receipt or self._receipt(
                operation_id,
                revision,
                snapshot_fingerprint,
                plan,
            )
            if record.state == "applied":
                if self._settle_plan(
                    plan,
                    action="apply",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                    created_directories=record.created_directories,
                ) != "after":
                    raise HostApplyError("apply_conflict")
                self._finalize_created_directories(
                    record.created_directories,
                    operation_id,
                )
                self._assert_finalized_created_directories(
                    record.created_directories,
                    operation_id,
                )
                self._cleanup_committed_plan(
                    plan,
                    action="apply",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                    created_directories=record.created_directories,
                )
                return receipt
            if record.state == "prepared":
                if self._settle_plan(
                    plan,
                    action="apply",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                ) != "before":
                    self._mark_conflict(operation_id)
                    raise HostApplyError("apply_conflict")
                record = self.journal.transition(
                    operation_id,
                    "applying",
                    apply_receipt=_receipt_dict(receipt),
                )
            elif record.state not in {"applying", "applied"}:
                raise HostApplyError("apply_conflict")

            if record.state == "applying":
                def commit_recovered_before_directory_prepare() -> None:
                    self._finalize_created_directories(
                        record.created_directories,
                        operation_id,
                    )
                    self._finish_apply(operation_id, receipt)

                recovered_state = self._settle_plan(
                    plan,
                    action="apply",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                    created_directories=record.created_directories,
                    commit_callback=commit_recovered_before_directory_prepare,
                )
                if recovered_state == "after":
                    self._cleanup_committed_plan(
                        plan,
                        action="apply",
                        branch=branch,
                        expected_head=expected_head,
                        operation_id=operation_id,
                        created_directories=record.created_directories,
                    )
                    return receipt

            created_directories = self._prepare_created_directories(
                plan,
                operation_id,
                record.created_directories,
            )
            if created_directories != record.created_directories:
                try:
                    record = self.journal.transition(
                        operation_id,
                        record.state,
                        created_directories=created_directories,
                    )
                except HostOperationLogError as exc:
                    self._remove_created_directories(
                        created_directories,
                        operation_id,
                    )
                    raise HostApplyError(exc.code) from exc
            self._publish_created_directories(
                created_directories,
                operation_id,
            )

            def commit_apply() -> None:
                self._finalize_created_directories(
                    created_directories,
                    operation_id,
                )
                self._finish_apply(operation_id, receipt)

            current = self._settle_plan(
                plan,
                action="apply",
                branch=branch,
                expected_head=expected_head,
                operation_id=operation_id,
                created_directories=created_directories,
                commit_callback=(
                    commit_apply if record.state == "applying" else None
                ),
            )
            if current == "after":
                self._cleanup_committed_plan(
                    plan,
                    action="apply",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                    created_directories=created_directories,
                )
                return receipt
            try:
                self._write_plan(
                    plan,
                    action="apply",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                    commit_callback=commit_apply,
                    created_directories=created_directories,
                )
            except BaseException as exc:
                if (
                    isinstance(exc, HostApplyError)
                    and exc.code == "transaction_rollback_failed"
                ):
                    self._mark_conflict(operation_id)
                else:
                    try:
                        if self._classify_plan(plan) == "before":
                            self._remove_created_directories(
                                created_directories,
                                operation_id,
                            )
                            if created_directories:
                                self.journal.transition(
                                    operation_id,
                                    "applying",
                                    created_directories=(),
                                )
                    except BaseException as cleanup_error:
                        self._mark_conflict(operation_id)
                        raise HostApplyError(
                            "apply_rollback_failed"
                        ) from cleanup_error
                raise
            self._cleanup_committed_plan(
                plan,
                action="apply",
                branch=branch,
                expected_head=expected_head,
                operation_id=operation_id,
                created_directories=created_directories,
            )
            return receipt

    @_serialize_project_operation
    def revert(
        self,
        *,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        branch: str,
        expected_head: str,
    ) -> ApplyReceipt:
        with self._lock:
            applied = self.journal.get(apply_receipt.apply_id)
            existing_revert = self.journal.get(operation_id)
            if (
                applied is None
                or applied.action != "apply"
                or applied.state != "applied"
                or applied.apply_receipt != _receipt_dict(apply_receipt)
                or applied.branch != branch
                or applied.expected_head != expected_head
            ):
                raise HostApplyError("revert_conflict")
            if existing_revert is None or existing_revert.state == "prepared":
                try:
                    self._assert_file_identities(
                        applied.file_identities,
                        apply_receipt,
                        applied=True,
                    )
                except HostApplyError as exc:
                    raise HostApplyError("revert_conflict") from exc
            safe_paths = self._validate_patch(
                applied.patch,
                tuple(item.path for item in apply_receipt.files),
            )
            try:
                record = self.journal.create(
                    operation_id=operation_id,
                    action="revert",
                    project_id=self.project_id,
                    revision=apply_receipt.revision,
                    branch=branch,
                    expected_head=expected_head,
                    patch_sha256=applied.patch_sha256,
                    patch=applied.patch,
                    apply_receipt=_receipt_dict(apply_receipt),
                    created_directories=applied.created_directories,
                    file_identities=applied.file_identities,
                )
            except HostOperationLogError as exc:
                raise HostApplyError(exc.code) from exc
            if record.state == "conflict":
                raise HostApplyError("revert_conflict")

            plan = self._build_plan(
                patch=applied.patch,
                paths=safe_paths,
                branch=branch,
                expected_head=expected_head,
                apply_receipt=apply_receipt,
                owned_operation_id=operation_id,
            )
            if record.state in {"reverting", "reverted"} and self._classify_plan(plan) == "after":
                try:
                    self._assert_file_identities(
                        applied.file_identities,
                        apply_receipt,
                        applied=True,
                    )
                except HostApplyError as exc:
                    self._mark_conflict(operation_id)
                    raise HostApplyError("revert_conflict") from exc
            expected = self._receipt(
                apply_receipt.apply_id,
                apply_receipt.revision,
                apply_receipt.snapshot_fingerprint,
                plan,
            )
            if expected.files != apply_receipt.files:
                self._mark_conflict(operation_id)
                raise HostApplyError("revert_conflict")
            inverse = tuple(
                _PlannedFile(item.path, item.after, item.before, item.mode)
                for item in plan
            )
            if record.state == "prepared":
                if self._classify_plan(plan) != "after":
                    self._mark_conflict(operation_id)
                    raise HostApplyError("revert_conflict")
                record = self.journal.transition(operation_id, "reverting")
            elif record.state not in {"reverting", "reverted"}:
                raise HostApplyError("revert_conflict")

            def commit_revert() -> None:
                self._assert_receipt_state(apply_receipt, applied=False)
                self._remove_created_directories(
                    record.created_directories,
                    apply_receipt.apply_id,
                )
                self._transition(operation_id, "reverted")

            current = self._settle_plan(
                inverse,
                action="revert",
                branch=branch,
                expected_head=expected_head,
                operation_id=operation_id,
                created_directories=record.created_directories,
                commit_callback=(
                    commit_revert if record.state == "reverting" else None
                ),
            )
            if current == "after":
                self._cleanup_committed_plan(
                    inverse,
                    action="revert",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                    created_directories=record.created_directories,
                )
                return apply_receipt
            try:
                self._write_plan(
                    inverse,
                    action="revert",
                    branch=branch,
                    expected_head=expected_head,
                    operation_id=operation_id,
                    commit_callback=commit_revert,
                    created_directories=record.created_directories,
                )
            except HostApplyError as exc:
                if exc.code == "transaction_rollback_failed":
                    self._mark_conflict(operation_id)
                raise
            self._cleanup_committed_plan(
                inverse,
                action="revert",
                branch=branch,
                expected_head=expected_head,
                operation_id=operation_id,
                created_directories=record.created_directories,
            )
            return apply_receipt

    @_serialize_project_operation
    def reconcile_apply(
        self,
        *,
        operation_id: str,
        snapshot_fingerprint: str,
    ) -> tuple[Literal["not_applied", "applied", "conflict"], ApplyReceipt | None]:
        with self._lock:
            record = self.journal.get(operation_id)
            if record is None or record.action != "apply":
                raise HostApplyError("operation_not_found")
            if record.state == "conflict":
                return "conflict", None
            stored_receipt = (
                _apply_receipt(record.apply_receipt)
                if record.apply_receipt is not None
                else None
            )
            if (
                stored_receipt is not None
                and stored_receipt.snapshot_fingerprint != snapshot_fingerprint
            ):
                return "conflict", None
            if record.state in {"applying", "applied"} and stored_receipt is None:
                return "conflict", None
            if record.state == "applied" and stored_receipt is not None:
                try:
                    self._assert_file_identities(
                        record.file_identities,
                        stored_receipt,
                        applied=True,
                    )
                except HostApplyError:
                    self._mark_conflict(operation_id)
                    return "conflict", None
            try:
                safe_paths = self._validate_patch(
                    record.patch,
                    _patch_paths(record.patch),
                )
                plan = self._build_plan(
                    patch=record.patch,
                    paths=safe_paths,
                    branch=record.branch,
                    expected_head=record.expected_head,
                    apply_receipt=stored_receipt,
                    owned_operation_id=operation_id,
                )

                def commit_recovered_apply() -> None:
                    if stored_receipt is None:
                        raise HostApplyError("apply_conflict")
                    self._finalize_created_directories(
                        record.created_directories,
                        operation_id,
                    )
                    self._finish_apply(operation_id, stored_receipt)

                state = self._settle_plan(
                    plan,
                    action="apply",
                    branch=record.branch,
                    expected_head=record.expected_head,
                    operation_id=operation_id,
                    created_directories=record.created_directories,
                    commit_callback=(
                        commit_recovered_apply
                        if record.state == "applying"
                        else None
                    ),
                )
            except HostApplyError as exc:
                if exc.code in {
                    "git_locked",
                    "git_inspection_failed",
                    "path_unavailable",
                    "project_operation_busy",
                    "transaction_unavailable",
                }:
                    raise
                self._mark_conflict(operation_id)
                return "conflict", None
            if state == "before":
                if record.state == "applied":
                    self._mark_conflict(operation_id)
                    return "conflict", None
                try:
                    cleanup_receipts = record.created_directories
                    if not cleanup_receipts:
                        cleanup_receipts = self._recover_created_directory_stages(
                            plan,
                            operation_id,
                        )
                    self._remove_created_directories(
                        cleanup_receipts,
                        operation_id,
                    )
                    if record.created_directories:
                        self.journal.transition(
                            operation_id,
                            record.state,
                            created_directories=(),
                        )
                except (HostApplyError, HostOperationLogError):
                    self._mark_conflict(operation_id)
                    return "conflict", None
                return "not_applied", None
            if state == "after":
                if stored_receipt is None:
                    self._mark_conflict(operation_id)
                    return "conflict", None
                self._cleanup_committed_plan(
                    plan,
                    action="apply",
                    branch=record.branch,
                    expected_head=record.expected_head,
                    operation_id=operation_id,
                    created_directories=record.created_directories,
                )
                return "applied", stored_receipt
            self._mark_conflict(operation_id)
            return "conflict", None

    def _build_plan(
        self,
        *,
        patch: str,
        paths: tuple[str, ...],
        branch: str,
        expected_head: str,
        apply_receipt: ApplyReceipt | None = None,
        owned_operation_id: str | None = None,
    ) -> tuple[_PlannedFile, ...]:
        self._assert_repository_state(
            branch,
            expected_head,
            owned_operation_id=owned_operation_id,
        )
        for path in paths:
            self._assert_path_chain(path)
        self._assert_case_safe(paths)
        receipt_files = (
            {item.path: item for item in apply_receipt.files}
            if apply_receipt is not None
            else {}
        )
        if apply_receipt is not None and tuple(sorted(receipt_files)) != paths:
            raise HostApplyError("operation_conflict")
        canonical: dict[str, tuple[bytes | None, int]] = {}
        working_before: dict[str, bytes | None] = {}
        for path in paths:
            baseline, mode = self._head_blob(expected_head, path)
            current_path = self.root / PurePosixPath(path)
            current = _read_regular(current_path)
            prior = receipt_files.get(path)
            if prior is not None:
                if prior.existed_before:
                    if baseline is None or prior.before_sha256 is None:
                        raise HostApplyError("operation_conflict")
                    current = _bytes_for_hash(baseline, prior.before_sha256)
                elif baseline is not None or prior.before_sha256 is not None:
                    raise HostApplyError("operation_conflict")
                else:
                    current = None
            elif baseline is None:
                if current is not None:
                    raise HostApplyError("target_changed")
            else:
                current = self._match_checkout_bytes(baseline, current, path)
            if current is not None:
                self._validate_text(current, path)
            canonical[path] = (baseline, mode)
            working_before[path] = current

        with tempfile.TemporaryDirectory(prefix="modelmirror-host-apply-") as value:
            staging = Path(value)
            for path, (baseline, mode) in canonical.items():
                if baseline is None:
                    continue
                target = staging / PurePosixPath(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(baseline)
                with contextlib.suppress(OSError):
                    target.chmod(mode)
            self._git_apply(staging, patch)
            result: list[_PlannedFile] = []
            for path in paths:
                staged = staging / PurePosixPath(path)
                after = _read_regular(staged)
                before = working_before[path]
                if after is not None and before is not None and _uses_crlf(before):
                    after = _to_crlf(after)
                if after is not None:
                    self._validate_text(after, path)
                if before == after:
                    raise HostApplyError("invalid_patch")
                mode = canonical[path][1] if before is not None else 0o644
                result.append(_PlannedFile(path, before, after, mode))
        return tuple(result)

    def _assert_repository_state(
        self,
        branch: str,
        expected_head: str,
        *,
        owned_operation_id: str | None = None,
    ) -> None:
        self._validate_repository_layout()
        full_ref = self._git_text("symbolic-ref", "--quiet", "HEAD")
        if full_ref != f"refs/heads/{branch}":
            raise HostApplyError("branch_changed")
        if self._git_text("rev-parse", "--verify", "HEAD^{commit}").lower() != expected_head:
            raise HostApplyError("head_changed")
        index_lock = self.root / ".git" / "index.lock"
        if index_lock.exists():
            expected_lock = (
                f"modelmirror-lock:{owned_operation_id}\n".encode("ascii")
                if owned_operation_id is not None
                else None
            )
            if expected_lock is None or _read_regular(index_lock) != expected_lock:
                raise HostApplyError("git_index_locked")
        staged = self._git("diff", "--cached", "--quiet", "--no-ext-diff", "HEAD", "--")
        if staged.returncode == 1:
            raise HostApplyError("git_index_dirty")
        if staged.returncode != 0:
            raise HostApplyError("git_inspection_failed")
        forbidden = self._git(
            "config",
            "--local",
            "--no-includes",
            "--name-only",
            "--get-regexp",
            r"^(include\.|includeif\.|core\.worktree$|extensions\.worktreeconfig$|filter\.|credential\.|diff\..*\.textconv$)",
        )
        if forbidden.returncode == 0 and forbidden.stdout.strip():
            raise HostApplyError("git_config_unsafe")
        if forbidden.returncode not in {0, 1}:
            raise HostApplyError("git_inspection_failed")

    def _git_stamp(
        self,
        branch: str,
        expected_head: str,
        *,
        owned_operation_id: str | None,
    ) -> tuple[str, str, str]:
        self._assert_repository_state(
            branch,
            expected_head,
            owned_operation_id=owned_operation_id,
        )
        index = _read_regular(self.root / ".git" / "index")
        if index is None:
            raise HostApplyError("git_index_unavailable")
        return branch, expected_head, _sha256(index)

    def _validate_repository_layout(self) -> None:
        if self.enforce_windows:
            if os.name != "nt":
                raise HostApplyError("windows_required")
            if str(self.root).startswith("\\\\") or not self.root.drive:
                raise HostApplyError("network_path_not_allowed")
            import ctypes

            if ctypes.windll.kernel32.GetDriveTypeW(
                ctypes.c_wchar_p(f"{self.root.drive}\\")
            ) == 4:
                raise HostApplyError("network_path_not_allowed")
        if _is_link_or_reparse(self.root):
            raise HostApplyError("project_reparse_point_not_allowed")
        git = self.root / ".git"
        if not git.is_dir() or _is_link_or_reparse(git):
            raise HostApplyError("git_repository_required")
        if (git / "commondir").exists():
            raise HostApplyError("git_shared_directory_not_allowed")
        alternates = git / "objects" / "info" / "alternates"
        if _is_link_or_reparse(alternates) or (alternates.is_file() and alternates.stat().st_size):
            raise HostApplyError("git_alternates_not_allowed")
        for metadata in (git / "config", git / "HEAD", git / "index"):
            if metadata.exists() and _is_link_or_reparse(metadata):
                raise HostApplyError("git_metadata_unsafe")

    def _assert_path_chain(self, relative: str) -> None:
        current = self.root
        for part in PurePosixPath(relative).parts:
            current = current / part
            if not current.exists():
                continue
            if _is_link_or_reparse(current):
                raise HostApplyError("symlink_not_allowed")
            if current != self.root / PurePosixPath(relative) and not current.is_dir():
                raise HostApplyError("path_parent_invalid")

    def _assert_case_safe(self, paths: tuple[str, ...]) -> None:
        folded = [path.casefold() for path in paths]
        if len(folded) != len(set(folded)):
            raise HostApplyError("path_case_conflict")
        for relative in paths:
            current = self.root
            for part in PurePosixPath(relative).parts:
                if current.is_dir():
                    matches = [entry.name for entry in os.scandir(current) if entry.name.casefold() == part.casefold()]
                    if len(matches) > 1 or (matches and matches[0] != part):
                        raise HostApplyError("path_case_conflict")
                current = current / part

    def _head_blob(self, expected_head: str, path: str) -> tuple[bytes | None, int]:
        tree = self._git(
            "--literal-pathspecs",
            "ls-tree",
            "-z",
            expected_head,
            "--",
            path,
        )
        if tree.returncode != 0:
            raise HostApplyError("git_tree_unreadable")
        if not tree.stdout:
            return None, 0o644
        records = [item for item in tree.stdout.split(b"\0") if item]
        if len(records) != 1:
            raise HostApplyError("git_tree_invalid")
        try:
            header, raw_path = records[0].split(b"\t", 1)
            mode, object_type, object_id = header.split(b" ", 2)
            decoded_path = raw_path.decode("utf-8", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise HostApplyError("git_tree_invalid") from exc
        if decoded_path != path or object_type != b"blob" or mode not in {b"100644", b"100755"}:
            raise HostApplyError("git_tree_unsafe")
        blob = self._git("cat-file", "blob", object_id.decode("ascii"))
        if blob.returncode != 0:
            raise HostApplyError("git_object_unavailable")
        return blob.stdout, 0o755 if mode == b"100755" else 0o644

    def _git_apply(self, staging: Path, patch: str) -> None:
        for check in (True, False):
            arguments = ["apply"]
            if check:
                arguments.append("--check")
            arguments.extend(("--whitespace=nowarn", "-"))
            try:
                completed = subprocess.run(
                    build_safe_git_command(staging, tuple(arguments)),
                    cwd=staging,
                    env=build_safe_git_environment(),
                    input=patch.encode("utf-8"),
                    stdin=None,
                    stdout=subprocess.PIPE,
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
                raise HostApplyError("patch_apply_failed") from exc
            if completed.returncode != 0:
                raise HostApplyError("patch_apply_failed")

    def _prepare_created_directories(
        self,
        plan: tuple[_PlannedFile, ...],
        operation_id: str,
        recorded: tuple[str, ...],
    ) -> tuple[str, ...]:
        expected_marker = _directory_owner_marker(operation_id)
        recorded_by_path = {
            value.split(":", 1)[1]: value for value in recorded
        }
        candidates = _parent_paths(plan)
        if len(candidates) > 64:
            raise HostApplyError("too_many_created_directories")
        owned: list[str] = []
        try:
            for relative in candidates:
                receipt = self._prepare_created_directory(
                    relative,
                    operation_id,
                    expected_marker,
                    recorded_by_path.get(relative),
                )
                if receipt is not None:
                    owned.append(receipt)
            if set(recorded_by_path) - set(candidates):
                raise HostApplyError("operation_record_invalid")
        except BaseException:
            try:
                self._remove_created_directories(tuple(owned), operation_id)
            except BaseException as cleanup_error:
                raise HostApplyError("apply_rollback_failed") from cleanup_error
            raise
        return tuple(owned)

    def _publish_created_directories(
        self,
        receipts: tuple[str, ...],
        operation_id: str,
    ) -> None:
        expected_marker = _directory_owner_marker(operation_id)
        for receipt in receipts:
            relative = receipt.split(":", 1)[1]
            directory = self.root / PurePosixPath(relative)
            stage = self._created_directory_stage(operation_id, relative)
            if directory.exists():
                if (
                    stage.exists()
                    or _directory_receipt(directory, relative, operation_id) != receipt
                ):
                    raise HostApplyError("created_directory_changed")
                marker = directory / _directory_marker_name(operation_id)
                if _read_regular(marker) != expected_marker:
                    raise HostApplyError("operation_artifact_conflict")
                continue
            if _directory_receipt(stage, relative, operation_id) != receipt:
                raise HostApplyError("created_directory_changed")
            marker = stage / _directory_marker_name(operation_id)
            if _read_regular(marker) != expected_marker:
                raise HostApplyError("operation_artifact_conflict")
            parent = directory.parent
            if _is_link_or_reparse(parent) or not parent.is_dir():
                raise HostApplyError("path_parent_invalid")
            with _guard_directories((parent, stage.parent)):
                try:
                    move_directory_no_replace(
                        stage,
                        directory,
                        expected_identity=_directory_id_from_receipt(receipt),
                        owner_name=_directory_marker_name(operation_id),
                        owner_content=expected_marker,
                        owner_only=True,
                    )
                except HostFileTransactionError as exc:
                    raise HostApplyError(exc.code) from exc
            marker = directory / _directory_marker_name(operation_id)
            with _guard_directories((directory,)):
                if (
                    _directory_receipt(directory, relative, operation_id) != receipt
                    or tuple(directory.iterdir()) != (marker,)
                    or _read_regular(marker) != expected_marker
                ):
                    raise HostApplyError("created_directory_changed")

    def _prepare_created_directory(
        self,
        relative: str,
        operation_id: str,
        expected_marker: bytes,
        prior: str | None,
    ) -> str | None:
        directory = self.root / PurePosixPath(relative)
        marker = directory / _directory_marker_name(operation_id)
        stage = self._created_directory_stage(operation_id, relative)
        if prior is not None:
            present = tuple(
                candidate
                for candidate in (directory, stage)
                if candidate.exists()
            )
            if (
                len(present) != 1
                or _directory_receipt(present[0], relative, operation_id) != prior
            ):
                raise HostApplyError("created_directory_changed")
            owned_marker = present[0] / _directory_marker_name(operation_id)
            if _read_regular(owned_marker) != expected_marker:
                raise HostApplyError("operation_artifact_conflict")
            return prior
        if directory.exists():
            if stage.exists() or _is_link_or_reparse(stage):
                raise HostApplyError("operation_artifact_conflict")
            if _is_link_or_reparse(directory) or not directory.is_dir():
                raise HostApplyError("path_parent_invalid")
            marker_value = _read_regular(marker) if marker.exists() else None
            if marker_value is not None:
                raise HostApplyError("operation_artifact_conflict")
            return None
        stage_parent = stage.parent
        if _is_link_or_reparse(stage_parent) or not stage_parent.is_dir():
            raise HostApplyError("git_metadata_unsafe")
        with _guard_directories((stage_parent,)):
            _prepare_directory_stage(stage, expected_marker, operation_id)
            return _directory_receipt(stage, relative, operation_id)

    def _created_directory_stage(self, operation_id: str, relative: str) -> Path:
        return (
            self.root
            / ".git"
            / "modelmirror-transactions"
            / _directory_stage_name(operation_id, relative)
        )

    def _recover_created_directory_stages(
        self,
        plan: tuple[_PlannedFile, ...],
        operation_id: str,
    ) -> tuple[str, ...]:
        expected_marker = _directory_owner_marker(operation_id)
        receipts: list[str] = []
        for relative in _parent_paths(plan):
            stage = self._created_directory_stage(operation_id, relative)
            if not stage.exists():
                continue
            if _is_link_or_reparse(stage) or not stage.is_dir():
                raise HostApplyError("operation_artifact_conflict")
            marker = stage / _directory_marker_name(operation_id)
            with _guard_directories((stage,)):
                entries = tuple(stage.iterdir())
                if (
                    entries != (marker,)
                    or _read_regular(marker) != expected_marker
                ):
                    raise HostApplyError("operation_artifact_conflict")
                receipts.append(_directory_receipt(stage, relative, operation_id))
        return tuple(receipts)

    def _finalize_created_directories(
        self,
        receipts: tuple[str, ...],
        operation_id: str,
    ) -> None:
        marker_value = _directory_owner_marker(operation_id)
        for receipt in receipts:
            relative = receipt.split(":", 1)[1]
            directory = self.root / PurePosixPath(relative)
            stage = self._created_directory_stage(operation_id, relative)
            try:
                directory_identity = file_identity(directory)
            except HostFileTransactionError as exc:
                raise HostApplyError("created_directory_changed") from exc
            if (
                stage.exists()
                or _is_link_or_reparse(stage)
                or directory_identity != _directory_id_from_receipt(receipt)
            ):
                raise HostApplyError("created_directory_changed")
            marker = directory / _directory_marker_name(operation_id)
            with _guard_directories((directory,)):
                if marker.exists():
                    if (
                        _read_regular(marker) != marker_value
                        or file_identity(marker) != _marker_id_from_receipt(receipt)
                    ):
                        raise HostApplyError("created_directory_changed")
                    try:
                        remove_regular_exact(
                            marker,
                            marker_value,
                            expected_identity=_marker_id_from_receipt(receipt),
                        )
                    except HostFileTransactionError as exc:
                        raise HostApplyError(exc.code) from exc
                elif _is_link_or_reparse(marker):
                    raise HostApplyError("created_directory_changed")

    def _assert_finalized_created_directories(
        self,
        receipts: tuple[str, ...],
        operation_id: str,
    ) -> None:
        for receipt in receipts:
            relative = receipt.split(":", 1)[1]
            directory = self.root / PurePosixPath(relative)
            stage = self._created_directory_stage(operation_id, relative)
            marker = directory / _directory_marker_name(operation_id)
            if (
                stage.exists()
                or file_identity(directory) != _directory_id_from_receipt(receipt)
                or marker.exists()
            ):
                raise HostApplyError("created_directory_changed")

    def _remove_created_directories(
        self,
        receipts: tuple[str, ...],
        operation_id: str,
    ) -> None:
        marker_value = _directory_owner_marker(operation_id)
        for receipt in reversed(receipts):
            relative = receipt.split(":", 1)[1]
            directory = self.root / PurePosixPath(relative)
            stage = self._created_directory_stage(operation_id, relative)
            present = tuple(
                candidate
                for candidate in (directory, stage)
                if candidate.exists()
            )
            if not present:
                continue
            if (
                len(present) != 1
                or file_identity(present[0]) != _directory_id_from_receipt(receipt)
            ):
                raise HostApplyError("created_directory_changed")
            owned = present[0]
            marker = owned / _directory_marker_name(operation_id)
            with _guard_directories((owned,)):
                entries = tuple(owned.iterdir())
                if entries:
                    if entries != (marker,) or _read_regular(marker) != marker_value:
                        raise HostApplyError("created_directory_not_empty")
                    try:
                        remove_regular_exact(
                            marker,
                            marker_value,
                            expected_identity=_marker_id_from_receipt(receipt),
                        )
                    except HostFileTransactionError as exc:
                        raise HostApplyError(exc.code) from exc
            _remove_empty_directory_exact(owned, receipt)

    def _write_plan(
        self,
        plan: tuple[_PlannedFile, ...],
        *,
        action: str,
        branch: str,
        expected_head: str,
        operation_id: str,
        commit_callback: Callable[[], None],
        created_directories: tuple[str, ...] = (),
    ) -> None:
        try:
            self._transaction(
                plan,
                action=action,
                branch=branch,
                expected_head=expected_head,
                operation_id=operation_id,
                created_directories=created_directories,
            ).apply(
                _transaction_files(plan),
                commit_callback=commit_callback,
            )
        except HostFileTransactionError as exc:
            raise HostApplyError(exc.code) from exc

    def _settle_plan(
        self,
        plan: tuple[_PlannedFile, ...],
        *,
        action: str,
        branch: str,
        expected_head: str,
        operation_id: str,
        created_directories: tuple[str, ...] = (),
        commit_callback: Callable[[], None] | None = None,
    ) -> Literal["before", "after"]:
        try:
            return self._transaction(
                plan,
                action=action,
                branch=branch,
                expected_head=expected_head,
                operation_id=operation_id,
                created_directories=created_directories,
            ).settle(
                _transaction_files(plan),
                commit_callback=commit_callback,
            )
        except HostFileTransactionError as exc:
            raise HostApplyError(exc.code) from exc

    def _cleanup_committed_plan(
        self,
        plan: tuple[_PlannedFile, ...],
        *,
        action: str,
        branch: str,
        expected_head: str,
        operation_id: str,
        created_directories: tuple[str, ...] = (),
    ) -> None:
        try:
            self._transaction(
                plan,
                action=action,
                branch=branch,
                expected_head=expected_head,
                operation_id=operation_id,
                created_directories=created_directories,
            ).cleanup_committed(_transaction_files(plan))
        except HostFileTransactionError as exc:
            raise HostApplyError(exc.code) from exc

    def _transaction(
        self,
        plan: tuple[_PlannedFile, ...],
        *,
        action: str,
        branch: str,
        expected_head: str,
        operation_id: str,
        created_directories: tuple[str, ...],
    ) -> HostFileTransaction:
        expected_stamp = self._git_stamp(
            branch,
            expected_head,
            owned_operation_id=operation_id,
        )
        return HostFileTransaction(
            self.root,
            operation_id,
            action,
            expected_stamp,
            lambda owned: self._git_stamp(
                branch,
                expected_head,
                owned_operation_id=owned,
            ),
            created_directories=created_directories,
            mutation_hook=self.mutation_hook,
        )

    def _classify_plan(self, plan: tuple[_PlannedFile, ...]) -> str:
        states = {self._file_state(item.path, item.before, item.after) for item in plan}
        if states == {"before"}:
            return "before"
        if states == {"after"}:
            return "after"
        if states <= {"before", "after"}:
            return "mixed"
        return "other"

    def _file_state(self, path: str, before: bytes | None, after: bytes | None) -> str:
        self._assert_path_chain(path)
        current = _read_regular(self.root / PurePosixPath(path))
        if current == before:
            return "before"
        if current == after:
            return "after"
        return "other"

    def _receipt(
        self,
        operation_id: str,
        revision: int,
        snapshot_fingerprint: str,
        plan: tuple[_PlannedFile, ...],
    ) -> ApplyReceipt:
        return ApplyReceipt(
            apply_id=operation_id,
            revision=revision,
            snapshot_fingerprint=snapshot_fingerprint,
            files=tuple(
                ApplyFileReceipt(
                    path=item.path,
                    existed_before=item.before is not None,
                    before_sha256=item.before_sha256,
                    after_sha256=item.after_sha256,
                )
                for item in plan
            ),
        )

    def _finish_apply(self, operation_id: str, receipt: ApplyReceipt) -> ApplyReceipt:
        self._assert_receipt_state(receipt, applied=True)
        file_identities = self._file_identity_receipts(receipt, applied=True)
        self._transition(
            operation_id,
            "applied",
            apply_receipt=_receipt_dict(receipt),
            file_identities=file_identities,
        )
        return receipt

    def _file_identity_receipts(
        self,
        receipt: ApplyReceipt,
        *,
        applied: bool,
    ) -> tuple[str, ...]:
        values: list[str] = []
        for item in receipt.files:
            expected_hash = item.after_sha256 if applied else item.before_sha256
            target = self.root / PurePosixPath(item.path)
            current = _read_regular(target)
            if expected_hash is None:
                if current is not None:
                    raise HostApplyError("apply_conflict")
                values.append(f"missing:{item.path}")
                continue
            if current is None or _sha256(current) != expected_hash:
                raise HostApplyError("apply_conflict")
            try:
                identity = file_identity(target)
            except HostFileTransactionError as exc:
                raise HostApplyError("apply_conflict") from exc
            values.append(f"{identity}:{item.path}")
        return tuple(values)

    def _assert_file_identities(
        self,
        identities: tuple[str, ...],
        receipt: ApplyReceipt,
        *,
        applied: bool,
    ) -> None:
        if identities != self._file_identity_receipts(receipt, applied=applied):
            raise HostApplyError("target_changed")

    def _assert_receipt_state(self, receipt: ApplyReceipt, *, applied: bool) -> None:
        for item in receipt.files:
            expected_hash = item.after_sha256 if applied else item.before_sha256
            current = _read_regular(self.root / PurePosixPath(item.path))
            if (current is None) != (expected_hash is None) or (
                current is not None and _sha256(current) != expected_hash
            ):
                raise HostApplyError("apply_conflict")

    def _transition(self, operation_id: str, state: str, **kwargs: object) -> None:
        try:
            self.journal.transition(operation_id, state, **kwargs)
        except HostOperationLogError as exc:
            raise HostApplyError(exc.code) from exc

    def _mark_conflict(self, operation_id: str) -> None:
        with contextlib.suppress(HostOperationLogError):
            self.journal.transition(operation_id, "conflict")

    def _validate_patch(self, patch: str, paths: Sequence[str]) -> tuple[str, ...]:
        try:
            safe = validate_patch(patch, expected_paths=paths, limits=self.limits)
        except PatchPolicyError as exc:
            raise HostApplyError(exc.code) from exc
        if len({path.casefold() for path in safe}) != len(safe):
            raise HostApplyError("path_case_conflict")
        for path in safe:
            if any(
                not _valid_windows_component(part)
                for part in PurePosixPath(path).parts
            ):
                raise HostApplyError("forbidden_path")
        return safe

    def _validate_text(self, content: bytes, path: str) -> None:
        if len(content) > self.limits.max_file_bytes or b"\0" in content:
            raise HostApplyError("binary_file_not_allowed")
        try:
            text = content.decode("utf-8", errors="strict")
            DraftWorkspace._reject_secrets(text, path)
        except UnicodeError as exc:
            raise HostApplyError("non_utf8_not_allowed") from exc
        except DraftPolicyError as exc:
            raise HostApplyError(exc.code) from exc

    @staticmethod
    def _match_checkout_bytes(baseline: bytes, current: bytes | None, path: str) -> bytes:
        if current is None:
            raise HostApplyError("target_changed")
        if current == baseline:
            return current
        if _uses_crlf(current) and current.replace(b"\r\n", b"\n") == baseline:
            return current
        raise HostApplyError("target_changed")

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                build_safe_git_command(self.root, arguments),
                cwd=self.root,
                env=build_safe_git_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
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
            raise HostApplyError("git_inspection_failed") from exc

    def _git_text(self, *arguments: str) -> str:
        result = self._git(*arguments)
        if result.returncode != 0:
            raise HostApplyError("git_inspection_failed")
        try:
            return result.stdout.decode("utf-8", errors="strict").strip()
        except UnicodeError as exc:
            raise HostApplyError("git_encoding_not_supported") from exc


def _patch_paths(patch: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git a/") and " b/" in line:
            left, right = line[len("diff --git a/") :].split(" b/", 1)
            if left != right:
                raise HostApplyError("invalid_patch")
            paths.append(left)
    return tuple(sorted(paths))


def _transaction_files(plan: Sequence[_PlannedFile]) -> tuple[FileMutation, ...]:
    return tuple(
        FileMutation(item.path, item.before, item.after, item.mode) for item in plan
    )


def _read_regular(path: Path) -> bytes | None:
    try:
        return read_regular(path)
    except HostFileTransactionError as exc:
        code = "symlink_not_allowed" if exc.code == "path_unsafe" else "target_unavailable"
        raise HostApplyError(code) from exc


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise HostApplyError("target_unavailable") from exc
    return stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _uses_crlf(content: bytes) -> bool:
    return b"\r\n" in content and b"\r" not in content.replace(b"\r\n", b"")


def _to_crlf(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def _bytes_for_hash(canonical: bytes, expected_sha256: str) -> bytes:
    for candidate in (canonical, _to_crlf(canonical)):
        if _sha256(candidate) == expected_sha256:
            return candidate
    raise HostApplyError("operation_conflict")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parent_paths(plan: Sequence[_PlannedFile]) -> tuple[str, ...]:
    values: set[str] = set()
    for item in plan:
        parts = PurePosixPath(item.path).parts[:-1]
        for index in range(1, len(parts) + 1):
            values.add(PurePosixPath(*parts[:index]).as_posix())
    return tuple(sorted(values, key=lambda value: (len(PurePosixPath(value).parts), value)))


def _directory_marker_name(operation_id: str) -> str:
    return f".modelmirror-{operation_id}.dir-owner"


def _directory_stage_name(operation_id: str, relative: str) -> str:
    suffix = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
    return f".modelmirror-{operation_id}-{suffix}.dir-stage"


def _directory_owner_marker(operation_id: str) -> bytes:
    return f"modelmirror-created:{operation_id}\n".encode("ascii")


def _prepare_directory_stage(
    stage: Path,
    marker_value: bytes,
    operation_id: str,
) -> None:
    marker = stage / _directory_marker_name(operation_id)
    if stage.exists():
        if _is_link_or_reparse(stage) or not stage.is_dir():
            raise HostApplyError("operation_artifact_conflict")
        with _guard_directories((stage,)):
            entries = tuple(stage.iterdir())
            if any(entry.name != marker.name or _is_link_or_reparse(entry) for entry in entries):
                raise HostApplyError("operation_artifact_conflict")
            if marker.exists() and _read_regular(marker) != marker_value:
                raise HostApplyError("operation_artifact_conflict")
    else:
        try:
            stage.mkdir()
        except FileExistsError as exc:
            raise HostApplyError("operation_artifact_conflict") from exc
    if not marker.exists():
        _write_exclusive(marker, marker_value, 0o600)
    elif _read_regular(marker) != marker_value:
        raise HostApplyError("operation_artifact_conflict")


def _directory_receipt(
    directory: Path,
    relative: str,
    operation_id: str,
) -> str:
    try:
        directory_identity = file_identity(directory)
        marker_identity = file_identity(
            directory / _directory_marker_name(operation_id)
        )
    except HostFileTransactionError as exc:
        raise HostApplyError("created_directory_changed") from exc
    return f"{directory_identity}@{marker_identity}:{relative}"


def _directory_id_from_receipt(receipt: str) -> str:
    try:
        identity_bundle, _relative = receipt.split(":", 1)
        directory_identity, _marker_identity = identity_bundle.split("@", 1)
    except ValueError as exc:
        raise HostApplyError("operation_record_invalid") from exc
    return directory_identity


def _marker_id_from_receipt(receipt: str) -> str:
    try:
        identity_bundle, _relative = receipt.split(":", 1)
        _directory_identity, marker_identity = identity_bundle.split("@", 1)
    except ValueError as exc:
        raise HostApplyError("operation_record_invalid") from exc
    return marker_identity


def _remove_empty_directory_exact(directory: Path, receipt: str) -> None:
    try:
        _identity_bundle, relative = receipt.split(":", 1)
        identity = _directory_id_from_receipt(receipt)
        device_text, inode_text = identity.split("-", 1)
        expected_device = int(device_text, 16)
        expected_inode = int(inode_text, 16)
    except (ValueError, TypeError) as exc:
        raise HostApplyError("operation_record_invalid") from exc
    try:
        current_identity = file_identity(directory)
    except HostFileTransactionError as exc:
        raise HostApplyError("created_directory_changed") from exc
    if current_identity != _directory_id_from_receipt(receipt):
        raise HostApplyError("created_directory_changed")
    if os.name == "nt":
        _windows_remove_empty_directory_exact(
            directory,
            expected_device=expected_device,
            expected_inode=expected_inode,
        )
        return
    parent_descriptor = os.open(
        directory.parent,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    directory_descriptor = -1
    try:
        directory_descriptor = os.open(
            directory.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(directory_descriptor)
        if (metadata.st_dev, metadata.st_ino) != (expected_device, expected_inode):
            raise HostApplyError("created_directory_changed")
        if os.listdir(directory_descriptor):
            raise HostApplyError("created_directory_not_empty")
        try:
            os.rmdir(directory.name, dir_fd=parent_descriptor)
        except OSError as exc:
            raise HostApplyError("created_directory_not_empty") from exc
    finally:
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        os.close(parent_descriptor)


def _windows_remove_empty_directory_exact(
    directory: Path,
    *,
    expected_device: int,
    expected_inode: int,
) -> None:
    from ctypes import wintypes

    delete_access = 0x00010000
    file_read_attributes = 0x00000080
    file_share_read = 0x00000001
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    file_disposition_info = 4
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
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

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

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
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(directory),
        delete_access | file_read_attributes,
        file_share_read,
        None,
        open_existing,
        file_flag_open_reparse_point | file_flag_backup_semantics,
        None,
    )
    if int(handle) == ctypes.c_void_p(-1).value:
        raise HostApplyError("created_directory_changed")
    try:
        information = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(information),
        ):
            raise HostApplyError("created_directory_changed")
        inode = (information.file_index_high << 32) | information.file_index_low
        if (
            not information.attributes & file_attribute_directory
            or information.attributes & file_attribute_reparse_point
            or inode != expected_inode
        ):
            raise HostApplyError("created_directory_changed")
        if information.volume_serial != expected_device:
            raise HostApplyError("created_directory_changed")
        verified = _ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(verified),
        ):
            raise HostApplyError("created_directory_changed")
        verified_inode = (verified.file_index_high << 32) | verified.file_index_low
        if (
            not verified.attributes & file_attribute_directory
            or verified.attributes & file_attribute_reparse_point
            or verified.volume_serial != expected_device
            or verified_inode != expected_inode
        ):
            raise HostApplyError("created_directory_changed")
        disposition = _FileDispositionInfo(True)
        if not kernel32.SetFileInformationByHandle(
            handle,
            file_disposition_info,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise HostApplyError("created_directory_not_empty")
    finally:
        kernel32.CloseHandle(handle)
    if directory.exists():
        raise HostApplyError("created_directory_not_empty")


def _write_exclusive(path: Path, content: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        # The exact object may have been replaced after a failed write. Leave
        # ambiguous evidence in place so recovery fails closed instead of
        # deleting a user-created object by path.
        raise


@contextlib.contextmanager
def _guard_directories(paths: Sequence[Path]):
    unique = tuple(dict.fromkeys(Path(path) for path in paths))
    for path in unique:
        if _is_link_or_reparse(path) or not path.is_dir():
            raise HostApplyError("path_parent_invalid")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        file_read_attributes = 0x0080
        file_share_read = 0x00000001
        open_existing = 3
        file_flag_open_reparse_point = 0x00200000
        file_flag_backup_semantics = 0x02000000
        file_attribute_tag_info = 9

        class _FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("reparse_tag", wintypes.DWORD),
            ]

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
        kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handles: list[int] = []
        try:
            for path in unique:
                handle = kernel32.CreateFileW(
                    str(path),
                    file_read_attributes,
                    file_share_read,
                    None,
                    open_existing,
                    file_flag_open_reparse_point | file_flag_backup_semantics,
                    None,
                )
                value = int(handle) if handle is not None else 0
                if value == ctypes.c_void_p(-1).value:
                    raise HostApplyError("path_parent_unavailable")
                info = _FileAttributeTagInfo()
                if not kernel32.GetFileInformationByHandleEx(
                    handle,
                    file_attribute_tag_info,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                ) or info.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    kernel32.CloseHandle(handle)
                    raise HostApplyError("project_reparse_point_not_allowed")
                handles.append(handle)
            yield
        finally:
            for handle in reversed(handles):
                kernel32.CloseHandle(handle)
        return

    descriptors: list[int] = []
    identities: list[tuple[Path, int, int]] = []
    try:
        for path in unique:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            descriptors.append(descriptor)
            identities.append((path, metadata.st_dev, metadata.st_ino))
        yield
        for path, device, inode in identities:
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if metadata.st_dev != device or metadata.st_ino != inode:
                raise HostApplyError("path_parent_changed")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _receipt_dict(receipt: ApplyReceipt) -> dict[str, object]:
    return {
        "apply_id": receipt.apply_id,
        "revision": receipt.revision,
        "snapshot_fingerprint": receipt.snapshot_fingerprint,
        "files": [
            {
                "path": item.path,
                "existed_before": item.existed_before,
                "before_sha256": item.before_sha256,
                "after_sha256": item.after_sha256,
            }
            for item in receipt.files
        ],
        "applied_at": receipt.applied_at,
    }


def _valid_windows_component(value: str) -> bool:
    if (
        not value
        or value.endswith((" ", "."))
        or any(character in '<>:"|?*' for character in value)
    ):
        return False
    stem = value.split(".", 1)[0].casefold()
    return stem not in {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }


def _apply_receipt(value: dict[str, object] | None) -> ApplyReceipt:
    if not isinstance(value, dict):
        raise HostApplyError("operation_record_invalid")
    try:
        files = tuple(ApplyFileReceipt(**item) for item in value["files"])
        return ApplyReceipt(
            apply_id=value["apply_id"],
            revision=value["revision"],
            snapshot_fingerprint=value["snapshot_fingerprint"],
            files=files,
            applied_at=value["applied_at"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HostApplyError("operation_record_invalid") from exc
