from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
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
    SnapshotManifest,
    snapshot_manifest,
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
    before_contents: tuple[tuple[str, bytes | None], ...] = ()
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
        self._target_hash_cache: dict[str, tuple[tuple[int, int, int, int], str]] = {}
        self._validate_roots()
        try:
            self._source_manifest = snapshot_manifest(self.source_root)
        except PatchPolicyError as exc:
            raise CodingApplyError(str(exc), code=exc.code) from exc
        self.source_fingerprint = self._source_manifest.fingerprint
        self._expected_manifest = self._source_manifest
        self._health_snapshot = self._inspect_health()

    def health(self) -> dict[str, object]:
        return dict(self._health_snapshot)

    def _inspect_health(self) -> dict[str, object]:
        result: dict[str, object] = {
            "configured": True,
            "available": False,
            "target": "dedicated_worktree",
            "snapshot_fingerprint": self.source_fingerprint,
        }
        try:
            self._assert_target_matches_expected()
        except CodingApplyError as exc:
            result["reason"] = exc.code
            return result
        result["available"] = True
        return result

    def _record_health(self, *, available: bool, reason: str | None = None) -> None:
        result: dict[str, object] = {
            "configured": True,
            "available": available,
            "target": "dedicated_worktree",
            "snapshot_fingerprint": self.source_fingerprint,
        }
        if reason is not None:
            result["reason"] = reason
        self._health_snapshot = result

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
            try:
                self._assert_target_matches_expected()
            except CodingApplyError as exc:
                self._record_health(available=False, reason=exc.code)
                raise
            try:
                self._prepare_staging(patch, safe_paths)
                before_contents = tuple(
                    (
                        path,
                        (self.target_root / path).read_bytes()
                        if (self.target_root / path).is_file()
                        else None,
                    )
                    for path in safe_paths
                )
                receipt = self._build_receipt(
                    operation_id=operation_id,
                    revision=revision,
                    paths=safe_paths,
                )
                self._write_applied_files(receipt, dict(before_contents))
            except CodingApplyError as exc:
                self._record_health(available=False, reason=exc.code)
                raise
            finally:
                self._clear_staging()

            self._operations[operation_id] = _Operation(
                revision=revision,
                snapshot_fingerprint=expected_fingerprint,
                patch_sha256=patch_sha256,
                paths=safe_paths,
                receipt=receipt,
                before_contents=before_contents,
            )
            self._expected_manifest = self._target_manifest()
            self._record_health(available=True)
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
                        self._assert_target_matches_expected()
                    except CodingApplyError as exc:
                        raise CodingApplyError(
                            "The target changed after revert.",
                            code="revert_conflict",
                        ) from exc
                    self._record_health(available=True)
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
                        self._record_health(available=True)
                        return operation.receipt
                self._record_health(available=False, reason="revert_conflict")
                raise CodingApplyError(
                    "The target changed after application.",
                    code="revert_conflict",
                ) from exc

            before_contents = dict(operation.before_contents) if operation else {}
            if len(before_contents) != len(receipt.files):
                raise CodingApplyError(
                    "The previous target content is unavailable.",
                    code="revert_conflict",
                )
            self._restore_previous(receipt, before_contents)
            self._expected_manifest = self._target_manifest()
            if operation is not None:
                self._operations[receipt.apply_id] = _Operation(
                    revision=operation.revision,
                    snapshot_fingerprint=operation.snapshot_fingerprint,
                    patch_sha256=operation.patch_sha256,
                    paths=operation.paths,
                    receipt=operation.receipt,
                    before_contents=operation.before_contents,
                    reverted=True,
                )
            self._record_health(available=True)
            return receipt

    def reconcile(
        self,
        *,
        operation_id: str,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
    ) -> tuple[str, ApplyReceipt | None]:
        """Inspect an interrupted application without mutating the target."""

        if not APPLY_ID_PATTERN.fullmatch(operation_id):
            raise CodingApplyError(
                "Apply operation id is invalid.",
                code="invalid_request",
            )
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise CodingApplyError(
                "Apply revision is invalid.",
                code="invalid_request",
            )
        try:
            safe_paths = validate_patch(
                patch,
                expected_paths=paths,
                limits=self.limits,
            )
        except PatchPolicyError as exc:
            raise CodingApplyError(str(exc), code=exc.code) from exc
        if expected_fingerprint != self.source_fingerprint:
            raise CodingApplyError(
                "Apply snapshot does not match the source.",
                code="snapshot_mismatch",
            )

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
                    try:
                        self._assert_target_matches_expected()
                    except CodingApplyError:
                        self._record_health(
                            available=False,
                            reason="recovery_conflict",
                        )
                        return "conflict", None
                    self._record_health(available=True)
                    return "not_applied", None
                try:
                    self._assert_target_matches_receipt(previous.receipt)
                except CodingApplyError:
                    self._record_health(
                        available=False,
                        reason="recovery_conflict",
                    )
                    return "conflict", None
                self._record_health(available=True)
                return "applied", previous.receipt
            try:
                self._assert_target_matches_expected()
            except CodingApplyError:
                pass
            else:
                self._record_health(available=True)
                return "not_applied", None

            try:
                self._prepare_reverse_staging(patch, safe_paths)
                receipt = self._build_reconciled_receipt(
                    operation_id=operation_id,
                    revision=revision,
                    paths=safe_paths,
                )
                before_contents = tuple(
                    (
                        item.path,
                        (self.staging_root / item.path).read_bytes()
                        if item.existed_before
                        else None,
                    )
                    for item in receipt.files
                )
                self._assert_target_matches_receipt(receipt)
            except CodingApplyError:
                self._record_health(available=False, reason="recovery_conflict")
                return "conflict", None
            finally:
                self._clear_staging()

            self._operations[operation_id] = _Operation(
                revision=revision,
                snapshot_fingerprint=expected_fingerprint,
                patch_sha256=patch_sha256,
                paths=safe_paths,
                receipt=receipt,
                before_contents=before_contents,
            )
            self._expected_manifest = self._target_manifest()
            self._record_health(available=True)
            return "applied", receipt

    def _prepare_reverse_staging(
        self,
        patch: str,
        paths: tuple[str, ...],
    ) -> None:
        """Reconstruct the exact pre-apply checkpoint from the current target."""

        self._clear_staging()
        self._copy_paths_to_staging(paths)
        for argv in (
            ("git", "apply", "--reverse", "--check", "--whitespace=nowarn", "-"),
            ("git", "apply", "--reverse", "--whitespace=nowarn", "-"),
        ):
            try:
                completed = subprocess.run(
                    argv,
                    cwd=self.staging_root,
                    env={
                        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
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
                    "Recovery Patch inspection failed.",
                    code="patch_apply_failed",
                ) from exc
            if completed.returncode != 0:
                raise CodingApplyError(
                    "Target does not contain the exact applied Patch.",
                    code="patch_apply_failed",
                )

    def _build_reconciled_receipt(
        self,
        *,
        operation_id: str,
        revision: int,
        paths: tuple[str, ...],
    ) -> ApplyReceipt:
        files: list[ApplyFileReceipt] = []
        for relative in paths:
            before = self.staging_root / relative
            after = self.target_root / relative
            existed_before = before.is_file() and not before.is_symlink()
            if after.is_symlink() or (after.exists() and not after.is_file()):
                raise CodingApplyError("Applied file is unavailable.", code="invalid_patch")
            if not existed_before and not after.is_file():
                raise CodingApplyError("Applied file is unavailable.", code="invalid_patch")
            files.append(
                ApplyFileReceipt(
                    path=relative,
                    existed_before=existed_before,
                    before_sha256=_file_sha256(before) if existed_before else None,
                    after_sha256=_file_sha256(after) if after.is_file() else None,
                )
            )
        return ApplyReceipt(
            apply_id=operation_id,
            revision=revision,
            snapshot_fingerprint=self.source_fingerprint,
            files=tuple(files),
        )

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
            or not (git_entry.is_file() or git_entry.is_dir())
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
            or not (git_entry.is_file() or git_entry.is_dir())
        ):
            raise CodingApplyError(
                "Dedicated target metadata is unavailable.",
                code="target_not_ready",
            )
        target_manifest = self._target_manifest()
        if self._source_manifest.entries != target_manifest.entries:
            raise CodingApplyError(
                "Dedicated target has unexpected files.",
                code="target_not_ready",
            )
        if target_manifest.fingerprint != self.source_fingerprint:
            raise CodingApplyError(
                "Dedicated target does not match the source snapshot.",
                code="target_not_ready",
            )

    def _assert_target_matches_expected(self) -> None:
        target_manifest = self._target_manifest()
        if (
            target_manifest.entries != self._expected_manifest.entries
            or target_manifest.file_hashes != self._expected_manifest.file_hashes
        ):
            raise CodingApplyError(
                "Dedicated target does not match the latest checkpoint.",
                code="target_not_ready",
            )

    def _target_manifest(self) -> SnapshotManifest:
        try:
            digest = hashlib.sha256()
            entries: set[tuple[str, str]] = set()
            file_hashes: list[tuple[str, str]] = []
            next_cache: dict[str, tuple[tuple[int, int, int, int], str]] = {}
            pending: list[tuple[Path, str]] = [(self.target_root, "")]
            while pending:
                directory, prefix = pending.pop()
                with os.scandir(directory) as iterator:
                    children = sorted(iterator, key=lambda item: item.name, reverse=True)
                for child in children:
                    if not prefix and child.name == ".git":
                        continue
                    relative = f"{prefix}/{child.name}" if prefix else child.name
                    metadata = child.stat(follow_symlinks=False)
                    if stat.S_ISLNK(metadata.st_mode):
                        raise CodingApplyError(
                            "Dedicated target contains a symbolic link.",
                            code="target_not_ready",
                        )
                    if stat.S_ISDIR(metadata.st_mode):
                        entries.add(("directory", relative))
                        pending.append((Path(child.path), relative))
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        raise CodingApplyError(
                            "Dedicated target contains an unsupported file.",
                            code="target_not_ready",
                        )
                    signature = (
                        metadata.st_size,
                        metadata.st_mtime_ns,
                        metadata.st_ctime_ns,
                        metadata.st_ino,
                    )
                    cached = self._target_hash_cache.get(relative)
                    if cached is not None and cached[0] == signature:
                        content_hash = cached[1]
                    else:
                        content_hash = _file_sha256(Path(child.path))
                    entries.add(("file", relative))
                    file_hashes.append((relative, content_hash))
                    next_cache[relative] = (signature, content_hash)
            file_hashes.sort()
            for relative, content_hash in file_hashes:
                size = next_cache[relative][0][0]
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                digest.update(str(size).encode("ascii"))
                digest.update(b"\0")
                digest.update(bytes.fromhex(content_hash))
            self._target_hash_cache = next_cache
            return SnapshotManifest(
                entries=frozenset(entries),
                file_hashes=tuple(file_hashes),
                fingerprint=digest.hexdigest(),
            )
        except CodingApplyError:
            raise
        except OSError as exc:
            raise CodingApplyError(
                "Dedicated target could not be inspected.",
                code="target_not_ready",
            ) from exc

    def _prepare_staging(self, patch: str, paths: tuple[str, ...]) -> None:
        self._clear_staging()
        self._copy_paths_to_staging(paths)
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

    def _copy_paths_to_staging(self, paths: tuple[str, ...]) -> None:
        try:
            self.staging_root.mkdir(parents=True, exist_ok=False)
            for relative in paths:
                source = self.target_root / relative
                if not source.exists():
                    continue
                if source.is_symlink() or not source.is_file():
                    raise CodingApplyError(
                        "Apply source path is unsafe.",
                        code="target_changed",
                    )
                destination = self.staging_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            _make_staging_writable(self.staging_root)
        except CodingApplyError:
            raise
        except OSError as exc:
            raise CodingApplyError(
                "Apply staging could not be prepared.",
                code="staging_unavailable",
            ) from exc

    def _build_receipt(
        self,
        *,
        operation_id: str,
        revision: int,
        paths: tuple[str, ...],
    ) -> ApplyReceipt:
        files: list[ApplyFileReceipt] = []
        for relative in paths:
            source = self.target_root / relative
            staged = self.staging_root / relative
            existed_before = source.is_file() and not source.is_symlink()
            if staged.is_symlink() or (staged.exists() and not staged.is_file()):
                raise CodingApplyError(
                    "Applied file is outside the allowed scope.",
                    code="invalid_patch",
                )
            after_hash: str | None = None
            if staged.is_file():
                if staged.stat().st_size > self.limits.max_file_bytes:
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
                after_hash = _sha256(after)
            elif not existed_before:
                raise CodingApplyError(
                    "Applied file is outside the allowed scope.",
                    code="invalid_patch",
                )
            before_hash = _file_sha256(source) if existed_before else None
            files.append(
                ApplyFileReceipt(
                    path=relative,
                    existed_before=existed_before,
                    before_sha256=before_hash,
                    after_sha256=after_hash,
                )
            )
        return ApplyReceipt(
            apply_id=operation_id,
            revision=revision,
            snapshot_fingerprint=self.source_fingerprint,
            files=tuple(files),
        )

    def _write_applied_files(
        self,
        receipt: ApplyReceipt,
        before_contents: dict[str, bytes | None],
    ) -> None:
        prepared: dict[str, Path] = {}
        replaced: list[ApplyFileReceipt] = []
        created_dirs: list[Path] = []
        try:
            for item in receipt.files:
                target = self.target_root / item.path
                if item.after_sha256 is None:
                    continue
                created_dirs.extend(_create_missing_parents(target.parent, self.target_root))
                prepared[item.path] = _prepare_temp_file(
                    target.parent,
                    (self.staging_root / item.path).read_bytes(),
                    mode=(
                        target.stat().st_mode & 0o777
                        if item.existed_before
                        else 0o644
                    ),
                )
            for index, item in enumerate(receipt.files):
                self._verify_target_preimage(item)
                self._notify_mutation("apply", index, item.path)
                target = self.target_root / item.path
                if item.after_sha256 is None:
                    target.unlink()
                else:
                    os.replace(prepared[item.path], target)
                replaced.append(item)
            self._assert_target_matches_receipt(receipt)
        except BaseException as exc:
            rollback_error = self._rollback_apply(replaced, before_contents)
            for temporary in prepared.values():
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
            for temporary in prepared.values():
                temporary.unlink(missing_ok=True)

    def _rollback_apply(
        self,
        replaced: Sequence[ApplyFileReceipt],
        before_contents: dict[str, bytes | None],
    ) -> BaseException | None:
        try:
            for item in reversed(replaced):
                target = self.target_root / item.path
                if item.existed_before:
                    before = before_contents.get(item.path)
                    if before is None:
                        raise CodingApplyError(
                            "Apply rollback preimage is unavailable.",
                            code="rollback_failed",
                        )
                    _atomic_write(target, before)
                else:
                    target.unlink(missing_ok=True)
            self._assert_touched_baseline(replaced)
        except BaseException as exc:
            return exc
        return None

    def _restore_previous(
        self,
        receipt: ApplyReceipt,
        before_contents: dict[str, bytes | None],
    ) -> None:
        applied_content: dict[str, bytes | None] = {
            item.path: (
                (self.target_root / item.path).read_bytes()
                if item.after_sha256 is not None
                else None
            )
            for item in receipt.files
        }
        restored: list[ApplyFileReceipt] = []
        try:
            for index, item in enumerate(receipt.files):
                self._notify_mutation("revert", index, item.path)
                target = self.target_root / item.path
                if item.existed_before:
                    before = before_contents.get(item.path)
                    if before is None:
                        raise CodingApplyError(
                            "Revert preimage is unavailable.",
                            code="revert_conflict",
                        )
                    _atomic_write(target, before)
                else:
                    target.unlink()
                restored.append(item)
        except BaseException as exc:
            try:
                for item in reversed(restored):
                    target = self.target_root / item.path
                    content = applied_content[item.path]
                    if content is None:
                        target.unlink(missing_ok=True)
                    else:
                        _atomic_write(target, content)
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
        target_manifest = self._target_manifest()
        current_hashes = dict(target_manifest.file_hashes)
        if (
            target_manifest.entries == self._expected_manifest.entries
            and target_manifest.file_hashes == self._expected_manifest.file_hashes
            and all(
                current_hashes.get(item.path) == item.after_sha256
                for item in receipt.files
            )
        ):
            return
        expected_entries = set(self._expected_manifest.entries)
        expected_files = dict(self._expected_manifest.file_hashes)
        for item in receipt.files:
            if item.after_sha256 is None:
                expected_files.pop(item.path, None)
                expected_entries.discard(("file", item.path))
            else:
                expected_files[item.path] = item.after_sha256
            if not item.existed_before and item.after_sha256 is not None:
                expected_entries.add(("file", item.path))
                parent = Path(item.path).parent
                while parent != Path("."):
                    expected_entries.add(("directory", parent.as_posix()))
                    parent = parent.parent
        if target_manifest.entries != frozenset(expected_entries):
            raise CodingApplyError(
                "Applied target contains unexpected files.",
                code="target_changed",
            )
        if dict(target_manifest.file_hashes) != expected_files:
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
        shutil.rmtree(self.staging_root, onerror=_retry_remove_readonly)


def _make_staging_writable(root: Path) -> None:
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(directory)
        if current.is_symlink():
            raise CodingApplyError(
                "Apply staging contains a symbolic link.",
                code="unsafe_workspace_root",
            )
        os.chmod(current, 0o700)
        for name in (*directory_names, *file_names):
            entry = current / name
            if entry.is_symlink():
                raise CodingApplyError(
                    "Apply staging contains a symbolic link.",
                    code="unsafe_workspace_root",
                )
            if entry.is_file():
                os.chmod(entry, 0o600)


def _retry_remove_readonly(
    function: Callable[[str], object],
    path: str,
    _error: object,
) -> None:
    os.chmod(path, 0o700)
    function(path)


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
