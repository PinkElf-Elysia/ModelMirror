from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from server.coding_applier.engine import CodingApplierEngine
from server.coding_committer.engine import (
    DEFAULT_AUTHOR_EMAIL,
    DEFAULT_AUTHOR_NAME,
    CodingCommitterEngine,
)
from server.coding_runtime.apply_models import (
    APPLY_ID_PATTERN,
    ApplyFileReceipt,
    ApplyReceipt,
    CodingApplyError,
)
from server.coding_runtime.commit_models import (
    COMMIT_ID_PATTERN,
    CodingCommitError,
    CommitReceipt,
)
from server.coding_runtime.patch_policy import PatchPolicyError, validate_patch
from server.coding_runtime.projects import (
    WRITEBACK_BRANCH,
    ProjectCatalogError,
    ProjectManifestEntry,
    ProjectState,
    build_safe_git_command,
    build_safe_git_environment,
    inspect_project,
    load_project_manifest,
    project_snapshot_path_is_allowed,
    resolve_project_path,
)


MAX_WRITER_FRAME_BYTES = 2 * 1024 * 1024
SAFE_PROJECT_ID = re.compile(r"^local-[a-f0-9]{24}$")
SAFE_OBJECT_ID = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
SOCKET_PATH = Path(
    os.getenv(
        "CODING_PROJECT_WRITER_SOCKET_PATH",
        "/run/modelmirror-coding-writeback/writer.sock",
    )
)
PROJECTS_ROOT = Path(os.getenv("CODING_PROJECTS_ROOT", "/projects-root"))
TEMPORARY_ROOT = Path(os.getenv("CODING_PROJECT_WRITER_TEMP", "/temporary"))


class ProjectWriterError(RuntimeError):
    def __init__(self, code: str, message: str = "Project write operation failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class _Operation:
    project_id: str
    expected_head: str
    patch: str
    paths: tuple[str, ...]
    expected_fingerprint: str
    internal_apply: ApplyReceipt
    public_apply: ApplyReceipt
    applier: CodingApplierEngine
    source_root: Path
    commit_root: Path
    committer: CodingCommitterEngine | None = None
    committer_apply: ApplyReceipt | None = None
    commit_receipt: CommitReceipt | None = None
    reverted: bool = False


class CodingProjectWriterEngine:
    """One-project-at-a-time writer for explicitly authorized local clones."""

    def __init__(
        self,
        projects_root: Path = PROJECTS_ROOT,
        temporary_root: Path = TEMPORARY_ROOT,
        *,
        author_name: str = DEFAULT_AUTHOR_NAME,
        author_email: str = DEFAULT_AUTHOR_EMAIL,
    ) -> None:
        self.projects_root = Path(projects_root)
        self.temporary_root = Path(temporary_root)
        if (
            not self.projects_root.is_absolute()
            or self.projects_root.is_symlink()
            or not self.projects_root.is_dir()
            or not self.temporary_root.is_absolute()
            or self.temporary_root.is_symlink()
        ):
            raise ProjectWriterError("writer_not_configured")
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        self._clear_temporary()
        self.author_name = author_name
        self.author_email = author_email
        self._operations: dict[str, _Operation] = {}
        self._lock = threading.Lock()

    def health(self) -> dict[str, object]:
        try:
            entries = load_project_manifest(self.projects_root)
            writable = sum(1 for entry in entries if entry.writeback_enabled)
        except ProjectCatalogError as exc:
            return {
                "configured": False,
                "available": False,
                "target": "selected_local_repository",
                "reason": exc.code,
            }
        return {
            "configured": True,
            "available": True,
            "target": "selected_local_repository",
            "writable_project_count": writable,
        }

    def apply(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
    ) -> ApplyReceipt:
        self._validate_common(project_id, expected_head, operation_id)
        with self._lock:
            previous = self._operations.get(operation_id)
            if previous is not None:
                if not self._same_apply_request(
                    previous,
                    project_id,
                    expected_head,
                    revision,
                    patch,
                    paths,
                    expected_fingerprint,
                ):
                    raise ProjectWriterError("operation_conflict")
                if previous.reverted:
                    raise ProjectWriterError("already_reverted")
                return previous.public_apply

            entry, target = self._require_clean_write_target(project_id, expected_head)
            del entry
            safe_paths = self._validate_patch(patch, paths)
            operation_root, source, staging, commit_root = self._prepare_operation_root(
                operation_id,
                target,
            )
            try:
                if self._visible_fingerprint(source) != expected_fingerprint:
                    raise ProjectWriterError("snapshot_mismatch")
                applier = CodingApplierEngine(source, target, staging)
                internal = applier.apply(
                    operation_id=operation_id,
                    revision=revision,
                    patch=patch,
                    paths=safe_paths,
                    expected_fingerprint=applier.source_fingerprint,
                )
                public = replace(
                    internal,
                    snapshot_fingerprint=expected_fingerprint,
                )
                operation = _Operation(
                    project_id=project_id,
                    expected_head=expected_head,
                    patch=patch,
                    paths=safe_paths,
                    expected_fingerprint=expected_fingerprint,
                    internal_apply=internal,
                    public_apply=public,
                    applier=applier,
                    source_root=source,
                    commit_root=commit_root,
                )
                self._operations[operation_id] = operation
                return public
            except BaseException:
                shutil.rmtree(operation_root, ignore_errors=True)
                raise

    def revert(
        self,
        *,
        project_id: str,
        expected_head: str,
        receipt: ApplyReceipt,
    ) -> ApplyReceipt:
        with self._lock:
            operation = self._require_operation(project_id, expected_head, receipt)
            if operation.commit_receipt is not None:
                raise ProjectWriterError("commit_active")
            operation.applier.revert(operation.internal_apply)
            operation.reverted = True
            return operation.public_apply

    def commit(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        apply_receipt: ApplyReceipt,
        message: str,
    ) -> CommitReceipt:
        if not COMMIT_ID_PATTERN.fullmatch(operation_id):
            raise ProjectWriterError("invalid_request")
        with self._lock:
            operation = self._require_operation(
                project_id,
                expected_head,
                apply_receipt,
            )
            if operation.reverted:
                raise ProjectWriterError("already_reverted")
            if operation.committer is None:
                operation.commit_root.mkdir(parents=True, exist_ok=True)
                operation.committer = CodingCommitterEngine(
                    operation.source_root,
                    self._target_for(project_id),
                    operation.commit_root,
                    author_name=self.author_name,
                    author_email=self.author_email,
                )
            receipt = operation.committer.commit(
                operation_id=operation_id,
                apply_receipt=operation.internal_apply,
                message=message,
            )
            operation.committer_apply = operation.internal_apply
            operation.commit_receipt = receipt
            return receipt

    def undo(
        self,
        *,
        project_id: str,
        expected_head: str,
        apply_receipt: ApplyReceipt,
        commit_receipt: CommitReceipt,
    ) -> CommitReceipt:
        with self._lock:
            operation = self._require_operation(
                project_id,
                expected_head,
                apply_receipt,
            )
            if (
                operation.committer is None
                or operation.commit_receipt != commit_receipt
            ):
                raise ProjectWriterError("operation_conflict")
            receipt = operation.committer.undo(
                commit_receipt,
                operation.committer_apply or operation.internal_apply,
            )
            operation.commit_receipt = None
            return receipt

    def reconcile_apply(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
    ) -> tuple[str, ApplyReceipt | None]:
        self._validate_common(project_id, expected_head, operation_id)
        with self._lock:
            previous = self._operations.get(operation_id)
            if previous is not None:
                if previous.reverted:
                    return "not_applied", None
                return "applied", previous.public_apply
            entry = self._find_entry(project_id)
            if not entry.writeback_enabled:
                raise ProjectWriterError("writeback_not_enabled")
            target = resolve_project_path(self.projects_root, entry.relative_path)
            self._require_repository_identity(target, expected_head)
            safe_paths = self._validate_patch(patch, paths)
            clean = self._git(target, "status", "--porcelain=v2", "--untracked-files=all")
            if not clean.stdout:
                return "not_applied", None
            operation_root, source, staging, commit_root = self._prepare_operation_root(
                operation_id,
                target,
            )
            try:
                self._reverse_patch(source, patch)
                if self._visible_fingerprint(source) != expected_fingerprint:
                    raise ProjectWriterError("snapshot_mismatch")
                applier = CodingApplierEngine(source, target, staging)
                state, internal = applier.reconcile(
                    operation_id=operation_id,
                    revision=revision,
                    patch=patch,
                    paths=safe_paths,
                    expected_fingerprint=applier.source_fingerprint,
                )
                if state != "applied" or internal is None:
                    shutil.rmtree(operation_root, ignore_errors=True)
                    return state, None
                public = replace(internal, snapshot_fingerprint=expected_fingerprint)
                self._operations[operation_id] = _Operation(
                    project_id=project_id,
                    expected_head=expected_head,
                    patch=patch,
                    paths=safe_paths,
                    expected_fingerprint=expected_fingerprint,
                    internal_apply=internal,
                    public_apply=public,
                    applier=applier,
                    source_root=source,
                    commit_root=commit_root,
                )
                return "applied", public
            except BaseException:
                shutil.rmtree(operation_root, ignore_errors=True)
                raise

    def reconcile_commit(
        self,
        *,
        project_id: str,
        expected_head: str,
        operation_id: str,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
        apply_receipt: ApplyReceipt,
        commit_operation_id: str,
        message: str,
    ) -> tuple[str, ApplyReceipt, CommitReceipt | None]:
        self._validate_common(project_id, expected_head, operation_id)
        if not COMMIT_ID_PATTERN.fullmatch(commit_operation_id):
            raise ProjectWriterError("invalid_request")
        with self._lock:
            entry = self._find_entry(project_id)
            if not entry.writeback_enabled:
                raise ProjectWriterError("writeback_not_enabled")
            target = resolve_project_path(self.projects_root, entry.relative_path)
            self._require_recovery_repository(target, expected_head)
            safe_paths = self._validate_patch(patch, paths)
            operation_root, source, staging, commit_root = self._prepare_operation_root(
                operation_id,
                target,
            )
            try:
                self._reverse_patch(source, patch)
                if self._visible_fingerprint(source) != expected_fingerprint:
                    raise ProjectWriterError("snapshot_mismatch")
                applier = CodingApplierEngine(source, target, staging)
                apply_state, reconstructed = applier.reconcile(
                    operation_id=operation_id,
                    revision=revision,
                    patch=patch,
                    paths=safe_paths,
                    expected_fingerprint=applier.source_fingerprint,
                )
                if apply_state != "applied" or reconstructed is None:
                    raise ProjectWriterError("recovery_conflict")
                internal_apply = replace(
                    apply_receipt,
                    snapshot_fingerprint=applier.source_fingerprint,
                )
                if (
                    apply_receipt.apply_id != operation_id
                    or apply_receipt.revision != revision
                    or apply_receipt.snapshot_fingerprint != expected_fingerprint
                    or apply_receipt.files != reconstructed.files
                ):
                    raise ProjectWriterError("operation_conflict")
                commit_root.mkdir(parents=True, exist_ok=True)
                committer = CodingCommitterEngine(
                    source,
                    target,
                    commit_root,
                    author_name=self.author_name,
                    author_email=self.author_email,
                )
                state, receipt = committer.reconcile(
                    operation_id=commit_operation_id,
                    apply_receipt=internal_apply,
                    message=message,
                )
                if state == "conflict":
                    raise ProjectWriterError("commit_recovery_conflict")
                self._operations[operation_id] = _Operation(
                    project_id=project_id,
                    expected_head=expected_head,
                    patch=patch,
                    paths=safe_paths,
                    expected_fingerprint=expected_fingerprint,
                    internal_apply=reconstructed,
                    public_apply=apply_receipt,
                    applier=applier,
                    source_root=source,
                    commit_root=commit_root,
                    committer=committer,
                    committer_apply=internal_apply,
                    commit_receipt=receipt if state == "committed" else None,
                )
                return state, apply_receipt, receipt
            except BaseException:
                shutil.rmtree(operation_root, ignore_errors=True)
                raise

    def _require_clean_write_target(
        self,
        project_id: str,
        expected_head: str,
    ) -> tuple[ProjectManifestEntry, Path]:
        entry = self._find_entry(project_id)
        summary = inspect_project(self.projects_root, entry)
        if summary.state is not ProjectState.AVAILABLE:
            raise ProjectWriterError(summary.reason or "project_unavailable")
        if not summary.features.apply:
            raise ProjectWriterError(summary.writeback_reason or "writeback_unavailable")
        if summary.head != expected_head:
            raise ProjectWriterError("project_changed")
        return entry, resolve_project_path(self.projects_root, entry.relative_path)

    def _require_repository_identity(self, target: Path, expected_head: str) -> None:
        git_dir = target / ".git"
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise ProjectWriterError("git_repository_required")
        branch = self._git_text(target, "symbolic-ref", "--quiet", "--short", "HEAD")
        head = self._git_text(target, "rev-parse", "--verify", "HEAD^{commit}")
        remotes = self._git(target, "remote")
        if branch != WRITEBACK_BRANCH:
            raise ProjectWriterError("writeback_branch_required")
        if head != expected_head or remotes.stdout.strip():
            raise ProjectWriterError(
                "project_changed" if head != expected_head else "git_remote_not_allowed"
            )

    def _require_recovery_repository(self, target: Path, expected_head: str) -> None:
        git_dir = target / ".git"
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise ProjectWriterError("git_repository_required")
        branch = self._git_text(target, "symbolic-ref", "--quiet", "--short", "HEAD")
        current_head = self._git_text(target, "rev-parse", "--verify", "HEAD^{commit}")
        remotes = self._git(target, "remote")
        if branch != WRITEBACK_BRANCH:
            raise ProjectWriterError("writeback_branch_required")
        if remotes.stdout.strip():
            raise ProjectWriterError("git_remote_not_allowed")
        ancestor = subprocess.run(
            build_safe_git_command(
                target,
                ("merge-base", "--is-ancestor", expected_head, current_head),
            ),
            cwd=target,
            env=build_safe_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        if ancestor.returncode != 0:
            raise ProjectWriterError("project_changed")

    def _find_entry(self, project_id: str) -> ProjectManifestEntry:
        if SAFE_PROJECT_ID.fullmatch(project_id) is None:
            raise ProjectWriterError("invalid_request")
        for entry in load_project_manifest(self.projects_root):
            if secrets.compare_digest(entry.project_id, project_id):
                return entry
        raise ProjectWriterError("project_not_found")

    def _target_for(self, project_id: str) -> Path:
        entry = self._find_entry(project_id)
        return resolve_project_path(self.projects_root, entry.relative_path)

    def _require_operation(
        self,
        project_id: str,
        expected_head: str,
        receipt: ApplyReceipt,
    ) -> _Operation:
        operation = self._operations.get(receipt.apply_id)
        if (
            operation is None
            or operation.project_id != project_id
            or operation.expected_head != expected_head
            or operation.public_apply != receipt
        ):
            raise ProjectWriterError("operation_conflict")
        return operation

    @staticmethod
    def _same_apply_request(
        operation: _Operation,
        project_id: str,
        expected_head: str,
        revision: int,
        patch: str,
        paths: Sequence[str],
        expected_fingerprint: str,
    ) -> bool:
        return (
            operation.project_id == project_id
            and operation.expected_head == expected_head
            and operation.public_apply.revision == revision
            and operation.patch == patch
            and operation.paths == tuple(sorted(paths))
            and operation.expected_fingerprint == expected_fingerprint
        )

    @staticmethod
    def _validate_common(project_id: str, expected_head: str, operation_id: str) -> None:
        if (
            SAFE_PROJECT_ID.fullmatch(project_id) is None
            or SAFE_OBJECT_ID.fullmatch(expected_head) is None
            or APPLY_ID_PATTERN.fullmatch(operation_id) is None
        ):
            raise ProjectWriterError("invalid_request")

    @staticmethod
    def _validate_patch(patch: str, paths: Sequence[str]) -> tuple[str, ...]:
        try:
            return validate_patch(patch, expected_paths=paths)
        except PatchPolicyError as exc:
            raise ProjectWriterError(exc.code) from exc

    def _prepare_operation_root(
        self,
        operation_id: str,
        target: Path,
    ) -> tuple[Path, Path, Path, Path]:
        operation_root = self.temporary_root / operation_id
        if operation_root.exists() or operation_root.is_symlink():
            raise ProjectWriterError("operation_conflict")
        source = operation_root / "source"
        staging = operation_root / "staging"
        commit_root = operation_root / "commit"
        operation_root.mkdir(mode=0o700)
        self._copy_worktree(target, source)
        return operation_root, source, staging, commit_root

    @staticmethod
    def _copy_worktree(target: Path, source: Path) -> None:
        source.mkdir(mode=0o700)
        for current, directories, files in os.walk(target, followlinks=False):
            current_path = Path(current)
            relative_root = current_path.relative_to(target)
            if relative_root == Path(".") and ".git" in directories:
                directories.remove(".git")
            for name in tuple(directories) + tuple(files):
                if (current_path / name).is_symlink():
                    raise ProjectWriterError("project_symlink_not_allowed")
            destination_root = source / relative_root
            destination_root.mkdir(parents=True, exist_ok=True)
            for name in files:
                shutil.copy2(current_path / name, destination_root / name)

    @staticmethod
    def _visible_fingerprint(source: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ProjectWriterError("project_symlink_not_allowed")
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            if not project_snapshot_path_is_allowed(relative):
                continue
            content = path.read_bytes()
            content_hash = hashlib.sha256(content)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(len(content)).encode("ascii"))
            digest.update(b"\0")
            digest.update(content_hash.digest())
        return digest.hexdigest()

    @staticmethod
    def _reverse_patch(source: Path, patch: str) -> None:
        for arguments in (
            ("apply", "--reverse", "--check", "--whitespace=nowarn", "-"),
            ("apply", "--reverse", "--whitespace=nowarn", "-"),
        ):
            completed = subprocess.run(
                ("git", *arguments),
                cwd=source,
                env=build_safe_git_environment(),
                input=patch.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            if completed.returncode != 0:
                raise ProjectWriterError("recovery_conflict")

    @staticmethod
    def _git(target: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(
            build_safe_git_command(target, arguments),
            cwd=target,
            env=build_safe_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise ProjectWriterError("git_inspection_failed")
        return completed

    @classmethod
    def _git_text(cls, target: Path, *arguments: str) -> str:
        return cls._git(target, *arguments).stdout.decode(
            "utf-8", errors="strict"
        ).strip()

    def _clear_temporary(self) -> None:
        if not self.temporary_root.is_dir():
            raise ProjectWriterError("writer_not_configured")
        for child in self.temporary_root.iterdir():
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)


class CodingProjectWriterServer:
    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        *,
        engine: CodingProjectWriterEngine | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.engine = engine
        self.startup_error: str | None = None
        if engine is None:
            try:
                self.engine = CodingProjectWriterEngine(
                    author_name=os.getenv(
                        "CODING_COMMIT_AUTHOR_NAME",
                        DEFAULT_AUTHOR_NAME,
                    ),
                    author_email=os.getenv(
                        "CODING_COMMIT_AUTHOR_EMAIL",
                        DEFAULT_AUTHOR_EMAIL,
                    ),
                )
            except (ProjectWriterError, ProjectCatalogError) as exc:
                self.startup_error = getattr(exc, "code", "writer_not_configured")

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "health":
            _require_keys(request, {"action"})
            if self.engine is None:
                return {
                    "service": "coding-project-writer",
                    "configured": False,
                    "available": False,
                    "target": "selected_local_repository",
                    "reason": self.startup_error or "writer_unavailable",
                }
            return {"service": "coding-project-writer", **self.engine.health()}
        if self.engine is None:
            raise ProjectWriterError(self.startup_error or "writer_unavailable")
        if action in {"apply", "reconcile_apply"}:
            keys = {
                "action",
                "project_id",
                "expected_head",
                "operation_id",
                "revision",
                "patch",
                "paths",
                "expected_fingerprint",
            }
            _require_keys(request, keys)
            kwargs = {key: request[key] for key in keys if key != "action"}
            if action == "apply":
                receipt = await asyncio.to_thread(self.engine.apply, **kwargs)
                return {"receipt": _apply_receipt_payload(receipt)}
            state, receipt = await asyncio.to_thread(
                self.engine.reconcile_apply,
                **kwargs,
            )
            return {
                "state": state,
                "receipt": _apply_receipt_payload(receipt) if receipt else None,
            }
        if action == "reconcile_commit":
            _require_keys(
                request,
                {
                    "action",
                    "project_id",
                    "expected_head",
                    "operation_id",
                    "revision",
                    "patch",
                    "paths",
                    "expected_fingerprint",
                    "apply_receipt",
                    "commit_operation_id",
                    "message",
                },
            )
            state, apply_receipt, commit_receipt = await asyncio.to_thread(
                self.engine.reconcile_commit,
                project_id=request["project_id"],
                expected_head=request["expected_head"],
                operation_id=request["operation_id"],
                revision=request["revision"],
                patch=request["patch"],
                paths=request["paths"],
                expected_fingerprint=request["expected_fingerprint"],
                apply_receipt=_apply_receipt(request["apply_receipt"]),
                commit_operation_id=request["commit_operation_id"],
                message=request["message"],
            )
            return {
                "state": state,
                "apply_receipt": _apply_receipt_payload(apply_receipt),
                "commit_receipt": (
                    _commit_receipt_payload(commit_receipt)
                    if commit_receipt is not None
                    else None
                ),
            }
        if action == "revert":
            _require_keys(
                request,
                {"action", "project_id", "expected_head", "apply_receipt"},
            )
            receipt = await asyncio.to_thread(
                self.engine.revert,
                project_id=request["project_id"],
                expected_head=request["expected_head"],
                receipt=_apply_receipt(request["apply_receipt"]),
            )
            return {"receipt": _apply_receipt_payload(receipt)}
        if action == "commit":
            _require_keys(
                request,
                {
                    "action",
                    "project_id",
                    "expected_head",
                    "operation_id",
                    "apply_receipt",
                    "message",
                },
            )
            receipt = await asyncio.to_thread(
                self.engine.commit,
                project_id=request["project_id"],
                expected_head=request["expected_head"],
                operation_id=request["operation_id"],
                apply_receipt=_apply_receipt(request["apply_receipt"]),
                message=request["message"],
            )
            return {"receipt": _commit_receipt_payload(receipt)}
        if action == "undo":
            _require_keys(
                request,
                {
                    "action",
                    "project_id",
                    "expected_head",
                    "apply_receipt",
                    "commit_receipt",
                },
            )
            receipt = await asyncio.to_thread(
                self.engine.undo,
                project_id=request["project_id"],
                expected_head=request["expected_head"],
                apply_receipt=_apply_receipt(request["apply_receipt"]),
                commit_receipt=_commit_receipt(request["commit_receipt"]),
            )
            return {"receipt": _commit_receipt_payload(receipt)}
        raise ProjectWriterError("unsupported_action")

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_WRITER_FRAME_BYTES:
                raise ProjectWriterError("invalid_request")
            request = json.loads(raw.decode("utf-8", errors="strict"))
            if not isinstance(request, dict):
                raise ProjectWriterError("invalid_request")
            response = {"ok": True, **await self.dispatch(request)}
        except (
            CodingApplyError,
            CodingCommitError,
            ProjectCatalogError,
            ProjectWriterError,
        ) as exc:
            response = {
                "ok": False,
                "code": getattr(exc, "code", "writer_error"),
                "error": "Coding project write request failed.",
            }
        except Exception:
            response = {
                "ok": False,
                "code": "writer_internal_error",
                "error": "Coding project write request failed.",
            }
        encoded = json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
        writer.write(encoded)
        await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    async def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self.socket_path),
            limit=MAX_WRITER_FRAME_BYTES + 1,
        )
        os.chmod(self.socket_path, 0o660)
        try:
            async with server:
                await server.serve_forever()
        finally:
            self.socket_path.unlink(missing_ok=True)


def _require_keys(request: dict[str, Any], keys: set[str]) -> None:
    if set(request) != keys:
        raise ProjectWriterError("invalid_request")


def _apply_receipt_payload(receipt: ApplyReceipt) -> dict[str, Any]:
    return {
        "apply_id": receipt.apply_id,
        "revision": receipt.revision,
        "snapshot_fingerprint": receipt.snapshot_fingerprint,
        "applied_at": receipt.applied_at,
        "files": [
            {
                "path": item.path,
                "existed_before": item.existed_before,
                "before_sha256": item.before_sha256,
                "after_sha256": item.after_sha256,
            }
            for item in receipt.files
        ],
    }


def _apply_receipt(value: Any) -> ApplyReceipt:
    if not isinstance(value, dict) or set(value) != {
        "apply_id",
        "revision",
        "snapshot_fingerprint",
        "applied_at",
        "files",
    }:
        raise ProjectWriterError("invalid_request")
    files = value["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 20:
        raise ProjectWriterError("invalid_request")
    try:
        return ApplyReceipt(
            apply_id=value["apply_id"],
            revision=value["revision"],
            snapshot_fingerprint=value["snapshot_fingerprint"],
            applied_at=value["applied_at"],
            files=tuple(ApplyFileReceipt(**item) for item in files),
        )
    except (TypeError, ValueError) as exc:
        raise ProjectWriterError("invalid_request") from exc


def _commit_receipt_payload(receipt: CommitReceipt) -> dict[str, Any]:
    return {
        "commit_id": receipt.commit_id,
        "revision": receipt.revision,
        "apply_id": receipt.apply_id,
        "commit_sha": receipt.commit_sha,
        "parent_sha": receipt.parent_sha,
        "tree_sha": receipt.tree_sha,
        "message": receipt.message,
        "files": list(receipt.files),
        "branch": receipt.branch,
        "committed_at": receipt.committed_at,
    }


def _commit_receipt(value: Any) -> CommitReceipt:
    if not isinstance(value, dict):
        raise ProjectWriterError("invalid_request")
    try:
        return CommitReceipt(**{**value, "files": tuple(value["files"])})
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectWriterError("invalid_request") from exc


def main() -> None:
    asyncio.run(CodingProjectWriterServer().serve_forever())


if __name__ == "__main__":
    main()
