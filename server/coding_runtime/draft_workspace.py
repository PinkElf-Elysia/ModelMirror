from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal


DraftFileStatus = Literal["added", "modified"]
DraftCheckStatus = Literal["passed", "failed"]


@dataclass(frozen=True, slots=True)
class DraftLimits:
    max_changed_files: int = 20
    max_file_bytes: int = 512 * 1024
    max_patch_bytes: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DraftCheck:
    check_id: str
    label: str
    status: DraftCheckStatus
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.check_id,
            "label": self.label,
            "status": self.status,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DraftFileChange:
    path: str
    status: DraftFileStatus
    additions: int
    deletions: int
    diff: str = field(repr=False)

    def to_dict(self, *, include_diff: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "path": self.path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
        }
        if include_diff:
            result["diff"] = self.diff
        return result


@dataclass(frozen=True, slots=True)
class DraftReport:
    revision: int
    files: tuple[DraftFileChange, ...]
    checks: tuple[DraftCheck, ...]
    validation_status: DraftCheckStatus
    patch_bytes: int

    @property
    def additions(self) -> int:
        return sum(item.additions for item in self.files)

    @property
    def deletions(self) -> int:
        return sum(item.deletions for item in self.files)

    @property
    def can_download(self) -> bool:
        return bool(self.files) and self.validation_status == "passed"

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "files": [item.to_dict() for item in self.files],
            "file_count": len(self.files),
            "additions": self.additions,
            "deletions": self.deletions,
            "patch_bytes": self.patch_bytes,
            "validation_status": self.validation_status,
            "can_download": self.can_download,
            "checks": [check.to_dict() for check in self.checks],
        }


class DraftWorkspaceError(RuntimeError):
    """Base class for isolated draft workspace failures."""


class DraftPolicyError(DraftWorkspaceError):
    """A hard safety rule failed; only a stable public code is exposed."""

    def __init__(self, code: str, *, path: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.path = path


class DraftRevisionError(DraftWorkspaceError):
    """The requested revision does not match the current draft."""


class DraftValidationError(DraftWorkspaceError):
    """The draft is not currently eligible for download."""


class DraftTransactionError(DraftWorkspaceError):
    """The caller used the per-turn transaction in an invalid order."""


_FORBIDDEN_SEGMENTS = frozenset(
    {
        ".git",
        ".codex",
        ".agents",
        ".idea",
        ".vscode",
        "node_modules",
        "dist",
        "build",
        ".vite",
        "__pycache__",
        ".pytest_cache",
        "docker-data",
        "new-api-data",
        "omniroute-data",
        "volumes",
        "storage",
        "uploads",
        "artifacts",
    }
)
_FORBIDDEN_PREFIXES = (
    "server/skills/installed/",
    "server/skills/tmp/",
    "server/mcp/installed/",
    "server/workflow_sandboxes/",
    "server/office_host/certs/",
)
_FORBIDDEN_SUFFIXES = (
    ".key",
    ".pem",
    ".log",
    ".err",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
)
_CONFLICT_MARKER = re.compile(r"^(?:<<<<<<<|=======|>>>>>>>)")


@dataclass(frozen=True, slots=True)
class _Candidate:
    path: str
    status: DraftFileStatus
    old_text: str
    new_text: str
    diff: str
    additions: int
    deletions: int
    digest: str


class DraftWorkspace:
    """A non-Git, transactional draft built from an immutable source snapshot."""

    def __init__(
        self,
        source_root: Path,
        workspace_root: Path,
        checkpoint_root: Path,
        *,
        limits: DraftLimits | None = None,
        preserve_workspace_root: bool = False,
    ) -> None:
        self.source_root = source_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.checkpoint_root = checkpoint_root.resolve()
        self.limits = limits or DraftLimits()
        self.preserve_workspace_root = preserve_workspace_root
        self._validate_roots()
        self._revision = 0
        self._committed_fingerprint = ""
        self._turn_active = False
        self._initialized = False

    @property
    def revision(self) -> int:
        return self._revision

    def initialize(self) -> DraftReport:
        if not self.source_root.is_dir():
            raise DraftWorkspaceError("source_snapshot_unavailable")
        self._collect_files(self.source_root, enforce_paths=False)
        self._reset_workspace_from(self.source_root)
        self._clear_tree(self.checkpoint_root)
        self._revision = 0
        self._committed_fingerprint = self._fingerprint(())
        self._turn_active = False
        self._initialized = True
        return self.validate()

    def begin_turn(self) -> None:
        self._ensure_initialized()
        if self._turn_active:
            raise DraftTransactionError("turn_already_active")
        candidates = self._scan_candidates()
        if self._fingerprint(candidates) != self._committed_fingerprint:
            raise DraftTransactionError("uncommitted_workspace_change")
        self._clear_tree(self.checkpoint_root)
        self.checkpoint_root.mkdir(parents=True)
        for candidate in candidates:
            source = self.workspace_root / candidate.path
            target = self.checkpoint_root / candidate.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self._turn_active = True

    def commit_turn(self) -> DraftReport:
        self._ensure_initialized()
        if not self._turn_active:
            raise DraftTransactionError("turn_not_active")
        try:
            candidates = self._scan_candidates()
        except DraftPolicyError:
            self._restore_checkpoint()
            raise

        fingerprint = self._fingerprint(candidates)
        if fingerprint != self._committed_fingerprint:
            self._revision += 1
            self._committed_fingerprint = fingerprint
        self._clear_tree(self.checkpoint_root)
        self._turn_active = False
        return self._report(candidates)

    def rollback_turn(self) -> DraftReport:
        self._ensure_initialized()
        if not self._turn_active:
            raise DraftTransactionError("turn_not_active")
        self._restore_checkpoint()
        return self.validate()

    def validate(self) -> DraftReport:
        self._ensure_initialized()
        if self._turn_active:
            raise DraftTransactionError("turn_active")
        candidates = self._scan_candidates()
        if self._fingerprint(candidates) != self._committed_fingerprint:
            raise DraftTransactionError("uncommitted_workspace_change")
        return self._report(candidates)

    def changes(self) -> DraftReport:
        return self.validate()

    def diff_for(self, path: str, revision: int) -> str:
        self._check_revision(revision)
        safe_path = self.normalize_relative_path(path)
        report = self.validate()
        for item in report.files:
            if item.path == safe_path:
                return item.diff
        raise DraftWorkspaceError("change_not_found")

    def patch(self, revision: int) -> str:
        self._check_revision(revision)
        report = self.validate()
        if not report.files:
            raise DraftValidationError("draft_is_empty")
        if not report.can_download:
            raise DraftValidationError("validation_failed")
        return "".join(item.diff for item in report.files)

    def discard(self) -> DraftReport:
        self._ensure_initialized()
        if self._turn_active:
            raise DraftTransactionError("turn_active")
        self._reset_workspace_from(self.source_root)
        self._clear_tree(self.checkpoint_root)
        self._revision += 1
        self._committed_fingerprint = self._fingerprint(())
        return self.validate()

    def destroy(self) -> None:
        if self.preserve_workspace_root:
            self._clear_contents(self.workspace_root)
        else:
            self._clear_tree(self.workspace_root)
        self._clear_tree(self.checkpoint_root)
        self._turn_active = False
        self._initialized = False

    @staticmethod
    def normalize_relative_path(path: str) -> str:
        if not isinstance(path, str) or not path or "\\" in path:
            raise DraftPolicyError("invalid_path")
        if any(ord(character) < 32 for character in path):
            raise DraftPolicyError("invalid_path")
        pure_path = PurePosixPath(path)
        if pure_path.is_absolute() or any(
            part in {"", ".", ".."} for part in pure_path.parts
        ):
            raise DraftPolicyError("invalid_path")
        normalized = pure_path.as_posix()
        if normalized != path or DraftWorkspace._is_forbidden_path(normalized):
            raise DraftPolicyError("forbidden_path", path=normalized)
        return normalized

    def _validate_roots(self) -> None:
        roots = (self.source_root, self.workspace_root, self.checkpoint_root)
        if len(set(roots)) != len(roots):
            raise DraftWorkspaceError("workspace_roots_overlap")
        for root in roots:
            if root.parent == root:
                raise DraftWorkspaceError("unsafe_workspace_root")
        for left in roots:
            for right in roots:
                if left != right and left in right.parents:
                    raise DraftWorkspaceError("workspace_roots_overlap")

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise DraftWorkspaceError("workspace_not_initialized")

    def _scan_candidates(self) -> tuple[_Candidate, ...]:
        source_files = self._collect_files(self.source_root, enforce_paths=False)
        workspace_files = self._collect_files(self.workspace_root, enforce_paths=True)

        deleted_paths = sorted(source_files.keys() - workspace_files.keys())
        if deleted_paths:
            raise DraftPolicyError("deletion_not_allowed", path=deleted_paths[0])

        candidates: list[_Candidate] = []
        for path in sorted(workspace_files):
            source_path = source_files.get(path)
            workspace_path = workspace_files[path]
            new_bytes = workspace_path.read_bytes()
            if source_path is not None:
                old_bytes = source_path.read_bytes()
                if old_bytes == new_bytes:
                    continue
                status: DraftFileStatus = "modified"
            else:
                old_bytes = b""
                status = "added"

            if len(new_bytes) > self.limits.max_file_bytes:
                raise DraftPolicyError("file_too_large", path=path)
            old_text = self._decode_text(old_bytes, path)
            new_text = self._decode_text(new_bytes, path)
            self._reject_secrets(new_text, path)
            diff = self._unified_diff(path, old_text, new_text, status=status)
            additions, deletions = self._count_changes(diff)
            candidates.append(
                _Candidate(
                    path=path,
                    status=status,
                    old_text=old_text,
                    new_text=new_text,
                    diff=diff,
                    additions=additions,
                    deletions=deletions,
                    digest=hashlib.sha256(new_bytes).hexdigest(),
                )
            )

        if len(candidates) > self.limits.max_changed_files:
            raise DraftPolicyError("too_many_files")
        patch_bytes = len(
            "".join(candidate.diff for candidate in candidates).encode("utf-8")
        )
        if patch_bytes > self.limits.max_patch_bytes:
            raise DraftPolicyError("patch_too_large")
        return tuple(candidates)

    def _report(self, candidates: tuple[_Candidate, ...]) -> DraftReport:
        checks = self._run_checks(candidates)
        files = tuple(
            DraftFileChange(
                path=item.path,
                status=item.status,
                additions=item.additions,
                deletions=item.deletions,
                diff=item.diff,
            )
            for item in candidates
        )
        return DraftReport(
            revision=self._revision,
            files=files,
            checks=checks,
            validation_status=(
                "failed" if any(check.status == "failed" for check in checks) else "passed"
            ),
            patch_bytes=len(
                "".join(candidate.diff for candidate in candidates).encode("utf-8")
            ),
        )

    def _run_checks(self, candidates: tuple[_Candidate, ...]) -> tuple[DraftCheck, ...]:
        python_errors: list[str] = []
        json_errors: list[str] = []
        conflict_paths: list[str] = []
        whitespace_paths: list[str] = []

        for candidate in candidates:
            if candidate.path.endswith(".py"):
                try:
                    ast.parse(candidate.new_text, filename=candidate.path)
                except SyntaxError as exc:
                    python_errors.append(f"{candidate.path}:{exc.lineno or 1}")
            if candidate.path.endswith(".json"):
                try:
                    json.loads(candidate.new_text)
                except json.JSONDecodeError as exc:
                    json_errors.append(f"{candidate.path}:{exc.lineno}")

            introduced_lines = self._introduced_lines(
                candidate.old_text, candidate.new_text
            )
            if any(_CONFLICT_MARKER.match(line) for line in introduced_lines):
                conflict_paths.append(candidate.path)
            if any(line.endswith((" ", "\t")) for line in introduced_lines):
                whitespace_paths.append(candidate.path)

        checks = (
            self._check(
                "python_syntax",
                "Python 文件结构",
                python_errors,
                "Python 文件结构正常",
            ),
            self._check(
                "json_syntax",
                "JSON 文件结构",
                json_errors,
                "JSON 文件结构正常",
            ),
            self._check(
                "conflict_markers",
                "未完成的合并标记",
                conflict_paths,
                "未发现未完成的合并标记",
            ),
            self._check(
                "trailing_whitespace",
                "行尾多余空格",
                whitespace_paths,
                "未发现新增的行尾多余空格",
            ),
            DraftCheck(
                check_id="diff_integrity",
                label="修改内容完整性",
                status="passed",
                message="修改内容完整且在大小限制内",
            ),
            DraftCheck(
                check_id="safety_policy",
                label="安全范围",
                status="passed",
                message="文件类型、路径和数量均符合安全范围",
            ),
        )
        return checks

    @staticmethod
    def _check(
        check_id: str,
        label: str,
        errors: list[str],
        success_message: str,
    ) -> DraftCheck:
        if errors:
            locations = "、".join(sorted(set(errors))[:5])
            return DraftCheck(
                check_id=check_id,
                label=label,
                status="failed",
                message=f"请检查：{locations}",
            )
        return DraftCheck(
            check_id=check_id,
            label=label,
            status="passed",
            message=success_message,
        )

    @staticmethod
    def _introduced_lines(old_text: str, new_text: str) -> list[str]:
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
        introduced: list[str] = []
        for operation, _old_start, _old_end, new_start, new_end in matcher.get_opcodes():
            if operation in {"replace", "insert"}:
                introduced.extend(new_lines[new_start:new_end])
        return introduced

    @staticmethod
    def _decode_text(content: bytes, path: str) -> str:
        if b"\x00" in content:
            raise DraftPolicyError("binary_file_not_allowed", path=path)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DraftPolicyError("non_utf8_not_allowed", path=path) from exc

    @staticmethod
    def _reject_secrets(content: str, path: str) -> None:
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            raise DraftPolicyError("secret_detected", path=path)

    @staticmethod
    def _unified_diff(
        path: str,
        old_text: str,
        new_text: str,
        *,
        status: DraftFileStatus,
    ) -> str:
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        raw_lines = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="/dev/null" if status == "added" else f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
        rendered = [f"diff --git a/{path} b/{path}\n"]
        if status == "added":
            rendered.append("new file mode 100644\n")
        for line in raw_lines:
            if line.endswith(("\n", "\r")):
                rendered.append(line)
            else:
                rendered.extend((f"{line}\n", "\\ No newline at end of file\n"))
        if status == "added" and not old_lines and not new_lines:
            rendered.extend(("--- /dev/null\n", f"+++ b/{path}\n"))
        return "".join(rendered)

    @staticmethod
    def _count_changes(diff: str) -> tuple[int, int]:
        additions = 0
        deletions = 0
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
        return additions, deletions

    @staticmethod
    def _fingerprint(candidates: tuple[_Candidate, ...]) -> str:
        digest = hashlib.sha256()
        for candidate in candidates:
            digest.update(candidate.path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(candidate.status.encode("ascii"))
            digest.update(b"\0")
            digest.update(candidate.digest.encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _is_forbidden_path(path: str) -> bool:
        lowered = path.lower()
        parts = PurePosixPath(lowered).parts
        name = parts[-1]
        return (
            any(part in _FORBIDDEN_SEGMENTS for part in parts)
            or name == ".env"
            or name.startswith(".env.")
            or name.endswith(_FORBIDDEN_SUFFIXES)
            or any(lowered.startswith(prefix) for prefix in _FORBIDDEN_PREFIXES)
        )

    def _collect_files(
        self, root: Path, *, enforce_paths: bool
    ) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for current, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            for name in list(directory_names):
                directory = current_path / name
                if directory.is_symlink():
                    relative = directory.relative_to(root).as_posix()
                    raise DraftPolicyError("symlink_not_allowed", path=relative)
            for name in file_names:
                file_path = current_path / name
                relative = file_path.relative_to(root).as_posix()
                if file_path.is_symlink():
                    raise DraftPolicyError("symlink_not_allowed", path=relative)
                if not file_path.is_file():
                    raise DraftPolicyError("unsupported_file_type", path=relative)
                if enforce_paths:
                    relative = self.normalize_relative_path(relative)
                files[relative] = file_path
        return files

    def _check_revision(self, revision: int) -> None:
        if revision != self._revision:
            raise DraftRevisionError("stale_revision")

    def _restore_checkpoint(self) -> None:
        self._reset_workspace_from(self.source_root)
        shutil.copytree(
            self.checkpoint_root,
            self.workspace_root,
            dirs_exist_ok=True,
            symlinks=False,
        )
        self._clear_tree(self.checkpoint_root)
        self._turn_active = False

    def _reset_workspace_from(self, source: Path) -> None:
        if not self.preserve_workspace_root:
            self._replace_tree(source, self.workspace_root)
            return
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._clear_contents(self.workspace_root)
        shutil.copytree(
            source,
            self.workspace_root,
            dirs_exist_ok=True,
            symlinks=False,
        )

    @staticmethod
    def _clear_contents(path: Path) -> None:
        if path.is_symlink() or not path.is_dir():
            raise DraftWorkspaceError("unsafe_workspace_root")
        for child in path.iterdir():
            if child.is_symlink() or not child.is_dir():
                child.unlink()
            else:
                shutil.rmtree(child)

    @staticmethod
    def _clear_tree(path: Path) -> None:
        if path.exists():
            if path.is_symlink() or not path.is_dir():
                raise DraftWorkspaceError("unsafe_workspace_root")
            shutil.rmtree(path)

    def _replace_tree(self, source: Path, target: Path) -> None:
        self._clear_tree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, symlinks=False)
