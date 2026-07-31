from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from server.coding_runtime.apply_models import (
    APPLY_ID_PATTERN,
    ApplyFileReceipt,
    ApplyReceipt,
    CodingApplyError,
)
from server.coding_runtime.draft_workspace import DraftLimits
from server.coding_runtime.patch_policy import (
    PatchPolicyError,
    snapshot_fingerprint,
    validate_patch,
)


APPLY_TIMEOUT_SECONDS = 30
MutationHook = Callable[[str, int, str], None]


@dataclass(frozen=True, slots=True)
class _Operation:
    revision: int
    snapshot_fingerprint: str
    patch_sha256: str
    paths: tuple[str, ...]
    receipt: ApplyReceipt
    reverted: bool = False


class CodingApplierEngine:
    """Applies one bounded Patch to an exact, dedicated source worktree."""

    def __init__(
        self,
        source_root: Path,
        target_root: Path,
        staging_root: Path,
        *,
        limits: DraftLimits | None = None,
        mutation_hook: MutationHook | None = None,
    ) -> None:
        for root in (source_root, target_root, staging_root):
            if root.is_symlink():
                raise CodingApplyError(
                    "Applier root must not be a symbolic link.",
                    code="unsafe_workspace_root",
                )
        self.source_root = source_root.resolve()
        self.target_root = target_root.resolve()
        self.staging_root = staging_root.resolve()
        self.limits = limits or DraftLimits()
        self._mutation_hook = mutation_hook
        self._lock = threading.Lock()
        self._operations: dict[str, _Operation] = {}
        self._validate_roots()
        try:
            self.source_fingerprint = snapshot_fingerprint(self.source_root)
        except PatchPolicyError as exc:
            raise CodingApplyError(str(exc), code=exc.code) from exc

    def health(self) -> dict[str, object]:
        result: dict[str, object] = {
            "configured": True,
            "available": False,
            "target": "dedicated_worktree",
            "snapshot_fingerprint": self.source_fingerprint,
        }
        try:
            self._assert_target_matches_baseline()
        except CodingApplyError as exc:
            result["reason"] = exc.code
            return result
        result["available"] = True
        return result

    def apply(
        self,
        *,
        operation_id: str,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
    ) -> ApplyReceipt:
        if not APPLY_ID_PATTERN.fullmatch(operation_id):
            raise CodingApplyError("Apply operation id is invalid.", code="invalid_request")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise CodingApplyError("Apply revision is invalid.", code="invalid_request")
        try:
            safe_paths = validate_patch(
                patch,
                expected_paths=paths,
                limits=self.limits,
            )
        except PatchPolicyError as exc:
            raise CodingApplyError(str(exc), code=exc.code) from exc
        patch_sha256 = _sha256(patch.encode("utf-8"))

        with self._lock:
            previous = self._operations.get(operation_id)
            if previous is not None:
                if (
                    previous.revision != revision
                    or previous.snapshot_fingerprint != expected_fingerprint
                    or previous.patch_sha256 != patch_sha256
                    or previous.paths != safe_paths
                ):
                    raise CodingApplyError(
                        "Apply operation was reused with different input.",
                        code="operation_conflict",
                    )
                if previous.reverted:
                    raise CodingApplyError(
                        "Applied changes were already reverted.",
                        code="already_reverted",
                    )
                self._assert_target_matches_receipt(previous.receipt)
                return previous.receipt

            if expected_fingerprint != self.source_fingerprint:
                raise CodingApplyError(
                    "Apply snapshot does not match the source.",
                    code="snapshot_mismatch",
                )
            self._assert_target_matches_baseline()
            try:
                self._prepare_staging(patch)
                receipt = self._build_receipt(
                    operation_id=operation_id,
                    revision=revision,
                    paths=safe_paths,
                )
                self._assert_target_matches_baseline()
                self._write_applied_files(receipt)
            finally:
                self._clear_staging()

            self._operations[operation_id] = _Operation(
                revision=revision,
                snapshot_fingerprint=expected_fingerprint,
                patch_sha256=patch_sha256,
                paths=safe_paths,
                receipt=receipt,
            )
            return receipt

    def revert(self, receipt: ApplyReceipt) -> ApplyReceipt:
        with self._lock:
            operation = self._operations.get(receipt.apply_id)
            if operation is not None:
                if operation.receipt != receipt:
                    raise CodingApplyError(
                        "Apply receipt does not match the operation.",
                        code="operation_conflict",
                    )
                if operation.reverted:
                    try:
                        self._assert_target_matches_baseline()
                    except CodingApplyError as exc:
                        raise CodingApplyError(
                            "The target changed after revert.",
                            code="revert_conflict",
                        ) from exc
                    return operation.receipt
            if receipt.snapshot_fingerprint != self.source_fingerprint:
                raise CodingApplyError(
                    "Revert snapshot does not match the source.",
                    code="snapshot_mismatch",
                )
            try:
                self._assert_target_matches_receipt(receipt)
            except CodingApplyError as exc:
                with contextlib.suppress(CodingApplyError):
                    self._assert_target_matches_baseline()
                    if operation is not None and operation.reverted:
                        return operation.receipt
                raise CodingApplyError(
                    "The target changed after application.",
                    code="revert_conflict",
                ) from exc

            self._restore_baseline(receipt)
            self._assert_target_matches_baseline()
            if operation is not None:
                self._operations[receipt.apply_id] = _Operation(
                    revision=operation.revision,
                    snapshot_fingerprint=operation.snapshot_fingerprint,
                    patch_sha256=operation.patch_sha256,
                    paths=operation.paths,
                    receipt=operation.receipt,
                    reverted=True,
                )
            return receipt

    def _validate_roots(self) -> None:
        roots = (self.source_root, self.target_root, self.staging_root)
        if len(set(roots)) != len(roots):
            raise CodingApplyError(
                "Applier roots must be separate.",
                code="unsafe_workspace_root",
            )
        for root in (self.source_root, self.target_root):
            if root.parent == root or root.is_symlink() or not root.is_dir():
                raise CodingApplyError(
                    "Applier root is unavailable.",
                    code="target_unavailable",
                )
        for first in roots:
            for second in roots:
                if first == second:
                    continue
                if _is_relative_to(first, second):
                    raise CodingApplyError(
                        "Applier roots must not contain each other.",
                        code="unsafe_workspace_root",
                    )
        git_entry = self.target_root / ".git"
        if (
            not git_entry.exists()
            or git_entry.is_symlink()
            or not git_entry.is_file()
        ):
            raise CodingApplyError(
                "Dedicated target metadata is unavailable.",
                code="target_unavailable",
            )

    def _assert_target_matches_baseline(self) -> None:
        git_entry = self.target_root / ".git"
        if (
            not git_entry.exists()
            or git_entry.is_symlink()
            or not git_entry.is_file()
        ):
            raise CodingApplyError(
                "Dedicated target metadata is unavailable.",
                code="target_not_ready",
            )
        source_entries = _snapshot_entries(self.source_root)
        target_entries = _snapshot_entries(
            self.target_root,
            ignored_root_names={".git"},
        )
        if source_entries != target_entries:
            raise CodingApplyError(
                "Dedicated target has unexpected files.",
                code="target_not_ready",
            )
        try:
            target_fingerprint = snapshot_fingerprint(
                self.target_root,
                ignored_root_names={".git"},
            )
        except PatchPolicyError as exc:
            raise CodingApplyError(str(exc), code="target_not_ready") from exc
        if target_fingerprint != self.source_fingerprint:
            raise CodingApplyError(
                "Dedicated target does not match the source snapshot.",
                code="target_not_ready",
            )

    def _prepare_staging(self, patch: str) -> None:
        self._clear_staging()
        try:
            shutil.copytree(self.source_root, self.staging_root)
        except OSError as exc:
            raise CodingApplyError(
                "Apply staging could not be prepared.",
                code="staging_unavailable",
            ) from exc
        for argv in (
            ("git", "apply", "--check", "--whitespace=nowarn", "-"),
            ("git", "apply", "--whitespace=nowarn", "-"),
        ):
            try:
                completed = subprocess.run(
                    argv,
                    cwd=self.staging_root,
                    env={
                        "PATH": os.environ.get(
                            "PATH",
                            "/usr/local/bin:/usr/bin:/bin",
                        ),
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                    },
                    input=patch.encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=APPLY_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CodingApplyError(
                    "Patch staging failed.",
                    code="patch_apply_failed",
                ) from exc
            if completed.returncode != 0:
                raise CodingApplyError(
                    "Patch could not be applied to the source snapshot.",
                    code="patch_apply_failed",
                )

    def _build_receipt(
        self,
        *,
        operation_id: str,
        revision: int,
        paths: tuple[str, ...],
    ) -> ApplyReceipt:
        files: list[ApplyFileReceipt] = []
        for relative in paths:
            source = self.source_root / relative
            staged = self.staging_root / relative
            if (
                staged.is_symlink()
                or not staged.is_file()
                or staged.stat().st_size > self.limits.max_file_bytes
            ):
                raise CodingApplyError(
                    "Applied file is outside the allowed scope.",
                    code="invalid_patch",
                )
            try:
                after = staged.read_bytes()
                after.decode("utf-8", errors="strict")
            except (OSError, UnicodeDecodeError) as exc:
                raise CodingApplyError(
                    "Applied file is not valid UTF-8 text.",
                    code="invalid_patch",
                ) from exc
            existed_before = source.is_file() and not source.is_symlink()
            before_hash = _file_sha256(source) if existed_before else None
            files.append(
                ApplyFileReceipt(
                    path=relative,
                    existed_before=existed_before,
                    before_sha256=before_hash,
                    after_sha256=_sha256(after),
                )
            )
        return ApplyReceipt(
            apply_id=operation_id,
            revision=revision,
            snapshot_fingerprint=self.source_fingerprint,
            files=tuple(files),
        )

    def _write_applied_files(self, receipt: ApplyReceipt) -> None:
        prepared: list[tuple[ApplyFileReceipt, Path]] = []
        replaced: list[ApplyFileReceipt] = []
        created_dirs: list[Path] = []
        try:
            for item in receipt.files:
                target = self.target_root / item.path
                created_dirs.extend(_create_missing_parents(target.parent, self.target_root))
                prepared.append(
                    (
                        item,
                        _prepare_temp_file(
                            target.parent,
                            (self.staging_root / item.path).read_bytes(),
                            mode=(
                                target.stat().st_mode & 0o777
                                if item.existed_before
                                else 0o644
                            ),
                        ),
                    )
                )
            for index, (item, temporary) in enumerate(prepared):
                self._verify_target_preimage(item)
                self._notify_mutation("apply", index, item.path)
                os.replace(temporary, self.target_root / item.path)
                replaced.append(item)
            self._assert_target_matches_receipt(receipt)
        except BaseException as exc:
            rollback_error = self._rollback_apply(replaced)
            for _, temporary in prepared:
                temporary.unlink(missing_ok=True)
            _remove_empty_directories(created_dirs)
            if rollback_error is not None:
                raise CodingApplyError(
                    "Apply rollback could not restore the target.",
                    code="rollback_failed",
                ) from rollback_error
            if isinstance(exc, CodingApplyError):
                raise
            raise CodingApplyError("Apply write failed.", code="apply_failed") from exc
        finally:
            for _, temporary in prepared:
                temporary.unlink(missing_ok=True)

    def _rollback_apply(
        self,
        replaced: Sequence[ApplyFileReceipt],
    ) -> BaseException | None:
        try:
            for item in reversed(replaced):
                target = self.target_root / item.path
                if item.existed_before:
                    _atomic_write(target, (self.source_root / item.path).read_bytes())
                else:
                    target.unlink(missing_ok=True)
            self._assert_touched_baseline(replaced)
        except BaseException as exc:
            return exc
        return None

    def _restore_baseline(self, receipt: ApplyReceipt) -> None:
        applied_content = {
            item.path: (self.target_root / item.path).read_bytes()
            for item in receipt.files
        }
        restored: list[ApplyFileReceipt] = []
        try:
            for index, item in enumerate(receipt.files):
                self._notify_mutation("revert", index, item.path)
                target = self.target_root / item.path
                if item.existed_before:
                    _atomic_write(target, (self.source_root / item.path).read_bytes())
                else:
                    target.unlink()
                restored.append(item)
        except BaseException as exc:
            try:
                for item in reversed(restored):
                    _atomic_write(
                        self.target_root / item.path,
                        applied_content[item.path],
                    )
                self._assert_target_matches_receipt(receipt)
            except BaseException as rollback_exc:
                raise CodingApplyError(
                    "Revert rollback could not restore applied files.",
                    code="rollback_failed",
                ) from rollback_exc
            if isinstance(exc, CodingApplyError):
                raise
            raise CodingApplyError("Revert write failed.", code="revert_failed") from exc
        for item in receipt.files:
            if not item.existed_before:
                _remove_empty_parents(
                    (self.target_root / item.path).parent,
                    self.target_root,
                )

    def _verify_target_preimage(self, item: ApplyFileReceipt) -> None:
        target = self.target_root / item.path
        if target.is_symlink():
            raise CodingApplyError(
                "Target file became a symbolic link.",
                code="target_changed",
            )
        if item.existed_before:
            if not target.is_file() or _file_sha256(target) != item.before_sha256:
                raise CodingApplyError(
                    "Target file changed before application.",
                    code="target_changed",
                )
        elif target.exists():
            raise CodingApplyError(
                "New target file already exists.",
                code="target_changed",
            )

    def _assert_touched_baseline(
        self,
        files: Sequence[ApplyFileReceipt],
    ) -> None:
        for item in files:
            target = self.target_root / item.path
            if item.existed_before:
                if not target.is_file() or _file_sha256(target) != item.before_sha256:
                    raise CodingApplyError(
                        "Target rollback is incomplete.",
                        code="rollback_failed",
                    )
            elif target.exists():
                raise CodingApplyError(
                    "Target rollback left a new file.",
                    code="rollback_failed",
                )

    def _assert_target_matches_receipt(self, receipt: ApplyReceipt) -> None:
        expected_entries = set(_snapshot_entries(self.source_root))
        expected_files = {
            relative: _file_sha256(self.source_root / relative)
            for kind, relative in expected_entries
            if kind == "file"
        }
        for item in receipt.files:
            expected_files[item.path] = item.after_sha256
            if not item.existed_before:
                expected_entries.add(("file", item.path))
                parent = Path(item.path).parent
                while parent != Path("."):
                    expected_entries.add(("directory", parent.as_posix()))
                    parent = parent.parent
        target_entries = _snapshot_entries(
            self.target_root,
            ignored_root_names={".git"},
        )
        if target_entries != frozenset(expected_entries):
            raise CodingApplyError(
                "Applied target contains unexpected files.",
                code="target_changed",
            )
        for relative, expected_hash in expected_files.items():
            target = self.target_root / relative
            if not target.is_file() or _file_sha256(target) != expected_hash:
                raise CodingApplyError(
                    "Applied target changed after application.",
                    code="target_changed",
                )

    def _notify_mutation(self, phase: str, index: int, path: str) -> None:
        if self._mutation_hook is not None:
            self._mutation_hook(phase, index, path)

    def _clear_staging(self) -> None:
        if not self.staging_root.exists():
            return
        if self.staging_root.is_symlink() or not self.staging_root.is_dir():
            raise CodingApplyError(
                "Apply staging root is unsafe.",
                code="unsafe_workspace_root",
            )
        shutil.rmtree(self.staging_root)


def _snapshot_entries(
    root: Path,
    *,
    ignored_root_names: set[str] | None = None,
) -> frozenset[tuple[str, str]]:
    ignored = ignored_root_names or set()
    entries: set[tuple[str, str]] = set()
    try:
        for path in sorted(root.rglob("*")):
            relative_path = path.relative_to(root)
            if relative_path.parts and relative_path.parts[0] in ignored:
                continue
            if path.is_symlink():
                raise CodingApplyError(
                    "Snapshot contains a symbolic link.",
                    code="target_not_ready",
                )
            relative = relative_path.as_posix()
            if path.is_dir():
                entries.add(("directory", relative))
            elif path.is_file():
                entries.add(("file", relative))
            else:
                raise CodingApplyError(
                    "Snapshot contains an unsupported file.",
                    code="target_not_ready",
                )
    except OSError as exc:
        raise CodingApplyError(
            "Snapshot could not be inspected.",
            code="target_not_ready",
        ) from exc
    return frozenset(entries)


def _prepare_temp_file(parent: Path, content: bytes, *, mode: int = 0o644) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".modelmirror-apply-", dir=parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _atomic_write(target: Path, content: bytes) -> None:
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    temporary = _prepare_temp_file(target.parent, content, mode=mode)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _create_missing_parents(parent: Path, root: Path) -> list[Path]:
    missing: list[Path] = []
    current = parent
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    if current == current.parent or not _is_relative_to(parent, root):
        raise CodingApplyError("Target parent is unsafe.", code="invalid_path")
    if current.exists() and (current.is_symlink() or not current.is_dir()):
        raise CodingApplyError("Target parent is unsafe.", code="target_changed")
    created: list[Path] = []
    for path in reversed(missing):
        path.mkdir()
        created.append(path)
    return created


def _remove_empty_directories(paths: Sequence[Path]) -> None:
    for path in reversed(paths):
        with contextlib.suppress(OSError):
            path.rmdir()


def _remove_empty_parents(path: Path, root: Path) -> None:
    current = path
    while current != root and _is_relative_to(current, root):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _file_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
