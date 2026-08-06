from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from .commands import (
    CommandContractError,
    ProjectVerificationConfig,
    parse_project_verification,
)


PROJECT_MANIFEST_NAME = ".modelmirror-coding-projects.json"
PROJECT_MANIFEST_VERSION = 3
SUPPORTED_PROJECT_MANIFEST_VERSIONS = frozenset({1, 2, 3})
WRITEBACK_BRANCH = "coding/local-draft"
MAX_PROJECTS = 50
MAX_PROJECT_NAME_CHARS = 80
MAX_PROJECT_PATH_CHARS = 512
MAX_PROJECT_MANIFEST_BYTES = 256 * 1024
GIT_INSPECTION_TIMEOUT_SECONDS = 10.0
MAX_PROJECT_SNAPSHOT_FILES = 20_000
MAX_PROJECT_SNAPSHOT_BYTES = 192 * 1024 * 1024
MAX_PROJECT_SNAPSHOT_FILE_BYTES = 32 * 1024 * 1024
MAX_PROJECT_AGENTS_BYTES = 64 * 1024

_SNAPSHOT_HIDDEN_SEGMENTS = frozenset(
    {
        ".git",
        ".codex",
        ".agents",
        ".idea",
        ".vscode",
        ".opencode",
        "node_modules",
        "dist",
        "build",
        ".vite",
        "__pycache__",
        ".pytest_cache",
        "storage",
        "uploads",
        "artifacts",
    }
)
_SNAPSHOT_HIDDEN_SUFFIXES = (
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".log",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
)
_SNAPSHOT_HIDDEN_NAMES = frozenset(
    {
        ".mcp.json",
        "opencode.json",
        "opencode.jsonc",
        "credentials.json",
    }
)


class ProjectKind(StrEnum):
    BUILTIN = "builtin"
    LOCAL_CLONE = "local_clone"


class ProjectState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ProjectCatalogError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProjectFeatures:
    chat: bool
    draft: bool
    diff: bool
    download: bool
    recovery: bool
    verification: bool
    apply: bool
    commit: bool
    publish: bool

    @classmethod
    def builtin(cls) -> ProjectFeatures:
        return cls(**{field: True for field in cls.__dataclass_fields__})

    @classmethod
    def local_draft(cls, *, writeback: bool = False) -> ProjectFeatures:
        return cls(
            chat=True,
            draft=True,
            diff=True,
            download=True,
            recovery=True,
            verification=False,
            apply=writeback,
            commit=writeback,
            publish=False,
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "chat": self.chat,
            "draft": self.draft,
            "diff": self.diff,
            "download": self.download,
            "recovery": self.recovery,
            "verification": self.verification,
            "apply": self.apply,
            "commit": self.commit,
            "publish": self.publish,
        }


@dataclass(frozen=True, slots=True)
class ProjectManifestEntry:
    project_id: str
    name: str
    relative_path: str
    verification: ProjectVerificationConfig = field(
        default_factory=ProjectVerificationConfig
    )
    writeback_enabled: bool = False


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    project_id: str
    name: str
    kind: ProjectKind
    state: ProjectState
    reason: str | None
    branch: str | None
    head: str | None
    features: ProjectFeatures
    relative_path: str | None = None
    writeback_reason: str | None = None

    @classmethod
    def builtin(cls) -> ProjectSummary:
        return cls(
            project_id="modelmirror",
            name="ModelMirror",
            kind=ProjectKind.BUILTIN,
            state=ProjectState.AVAILABLE,
            reason=None,
            branch=None,
            head=None,
            features=ProjectFeatures.builtin(),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.project_id,
            "name": self.name,
            "kind": self.kind.value,
            "state": self.state.value,
            "reason": self.reason,
            "branch": self.branch,
            "head": self.head[:12] if self.head else None,
            "features": self.features.to_dict(),
            "writeback_reason": self.writeback_reason,
        }


GitRunner = Callable[[Path, Sequence[str]], subprocess.CompletedProcess[bytes]]


def build_project_id(relative_path: str) -> str:
    normalized = normalize_project_path(relative_path)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"local-{digest[:24]}"


def normalize_project_path(value: str) -> str:
    if not isinstance(value, str):
        raise ProjectCatalogError("manifest_path_invalid", "Project path must be text")
    if value != unicodedata.normalize("NFC", value):
        raise ProjectCatalogError("manifest_path_invalid", "Project path must use NFC")
    if not value or len(value) > MAX_PROJECT_PATH_CHARS:
        raise ProjectCatalogError("manifest_path_invalid", "Project path length is invalid")
    if "\\" in value or any(_is_control(character) for character in value):
        raise ProjectCatalogError("manifest_path_invalid", "Project path is not canonical")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProjectCatalogError("manifest_path_invalid", "Project path escapes its root")
    if any(":" in part for part in path.parts):
        raise ProjectCatalogError("manifest_path_invalid", "Project path contains a drive marker")
    normalized = "/".join(path.parts)
    if normalized != value:
        raise ProjectCatalogError("manifest_path_invalid", "Project path is not canonical")
    return normalized


def load_project_manifest(root: Path) -> tuple[ProjectManifestEntry, ...]:
    root = Path(root)
    if not root.is_absolute():
        raise ProjectCatalogError("projects_root_not_absolute", "Projects root must be absolute")
    if root.is_symlink() or not root.is_dir():
        raise ProjectCatalogError("projects_root_unavailable", "Projects root is unavailable")
    manifest_path = root / PROJECT_MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ProjectCatalogError("project_manifest_unavailable", "Project manifest is unavailable")
    try:
        raw_manifest = manifest_path.read_bytes()
        if len(raw_manifest) > MAX_PROJECT_MANIFEST_BYTES:
            raise ProjectCatalogError("project_manifest_invalid", "Project manifest is too large")
        payload = json.loads(raw_manifest.decode("utf-8", errors="strict"))
    except ProjectCatalogError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectCatalogError("project_manifest_invalid", "Project manifest is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"version", "projects"}:
        raise ProjectCatalogError("project_manifest_invalid", "Project manifest shape is invalid")
    if (
        type(payload["version"]) is not int
        or payload["version"] not in SUPPORTED_PROJECT_MANIFEST_VERSIONS
    ):
        raise ProjectCatalogError("project_manifest_version_unsupported", "Project manifest version is unsupported")
    manifest_version = payload["version"]
    projects = payload["projects"]
    if not isinstance(projects, list) or len(projects) > MAX_PROJECTS:
        raise ProjectCatalogError("project_manifest_invalid", "Project manifest project count is invalid")

    entries: list[ProjectManifestEntry] = []
    seen_paths: set[str] = set()
    seen_folded_paths: set[str] = set()
    for item in projects:
        if manifest_version == 1:
            allowed_keys = {"name", "path"}
        elif manifest_version == 2:
            allowed_keys = {"name", "path", "verification"}
        else:
            allowed_keys = {"name", "path", "verification", "writeback"}
        if (
            not isinstance(item, dict)
            or not {"name", "path"}.issubset(item)
            or not set(item).issubset(allowed_keys)
        ):
            raise ProjectCatalogError("project_manifest_invalid", "Project entry is invalid")
        name = _normalize_project_name(item["name"])
        relative_path = normalize_project_path(item["path"])
        try:
            verification = parse_project_verification(item.get("verification"))
        except CommandContractError as exc:
            raise ProjectCatalogError(exc.code, str(exc)) from exc
        writeback = item.get("writeback")
        if writeback is None:
            writeback_enabled = False
        elif (
            not isinstance(writeback, dict)
            or set(writeback) != {"enabled"}
            or not isinstance(writeback["enabled"], bool)
        ):
            raise ProjectCatalogError(
                "project_writeback_invalid",
                "Project writeback configuration is invalid",
            )
        else:
            writeback_enabled = writeback["enabled"]
        folded_path = relative_path.casefold()
        if relative_path in seen_paths:
            raise ProjectCatalogError("manifest_path_duplicate", "Project path is duplicated")
        if folded_path in seen_folded_paths:
            raise ProjectCatalogError("manifest_path_case_conflict", "Project paths conflict by case")
        seen_paths.add(relative_path)
        seen_folded_paths.add(folded_path)
        entries.append(
            ProjectManifestEntry(
                project_id=build_project_id(relative_path),
                name=name,
                relative_path=relative_path,
                verification=verification,
                writeback_enabled=writeback_enabled,
            )
        )
    return tuple(entries)


def inspect_project(
    root: Path,
    entry: ProjectManifestEntry,
    *,
    git_runner: GitRunner | None = None,
) -> ProjectSummary:
    project_path, path_error = _resolve_project_path(Path(root), entry.relative_path)
    if path_error is not None:
        return _unavailable(entry, path_error)
    assert project_path is not None

    git_path = project_path / ".git"
    if git_path.is_symlink():
        return _unavailable(entry, "git_shared_directory_not_allowed")
    if git_path.is_file():
        return _unavailable(entry, "git_worktree_not_allowed")
    if not git_path.is_dir():
        return _unavailable(entry, "git_repository_required")
    if (git_path / "commondir").exists():
        return _unavailable(entry, "git_shared_directory_not_allowed")
    alternates = git_path / "objects" / "info" / "alternates"
    try:
        if alternates.is_symlink() or (alternates.is_file() and alternates.stat().st_size > 0):
            return _unavailable(entry, "git_alternates_not_allowed")
    except OSError:
        return _unavailable(entry, "git_inspection_failed")

    runner = git_runner or _run_git
    try:
        inside = _run_git_text(runner, project_path, ("rev-parse", "--is-inside-work-tree"))
        if inside.strip() != "true":
            return _unavailable(entry, "git_repository_required")
        branch_result = runner(project_path, ("symbolic-ref", "--quiet", "--short", "HEAD"))
        if branch_result.returncode != 0:
            return _unavailable(entry, "git_branch_required")
        branch = branch_result.stdout.decode("utf-8", errors="strict").strip()
        if not branch or len(branch) > 200 or any(_is_control(character) for character in branch):
            return _unavailable(entry, "git_branch_required")
        head = _run_git_text(runner, project_path, ("rev-parse", "--verify", "HEAD^{commit}")).strip().lower()
        if len(head) not in {40, 64} or any(character not in "0123456789abcdef" for character in head):
            return _unavailable(entry, "git_head_invalid")
        tree = runner(project_path, ("ls-tree", "-r", "-z", "--full-tree", "HEAD"))
        _require_git_success(tree)
        validate_git_tree(tree.stdout)
        status = runner(project_path, ("status", "--porcelain=v2", "--untracked-files=all"))
        _require_git_success(status)
        if status.stdout:
            return _unavailable(entry, "git_repository_dirty")
    except subprocess.TimeoutExpired:
        return _unavailable(entry, "git_inspection_timeout")
    except ProjectCatalogError as exc:
        return _unavailable(entry, exc.code)
    except (OSError, UnicodeError):
        return _unavailable(entry, "git_inspection_failed")

    writeback_available = False
    writeback_reason = "writeback_not_enabled"
    if entry.writeback_enabled:
        if branch != WRITEBACK_BRANCH:
            writeback_reason = "writeback_branch_required"
        else:
            try:
                remotes = runner(project_path, ("remote",))
                _require_git_success(remotes)
                remote_names = remotes.stdout.decode("utf-8", errors="strict").strip()
                if remote_names:
                    writeback_reason = "git_remote_not_allowed"
                else:
                    writeback_available = True
                    writeback_reason = None
            except (ProjectCatalogError, OSError, UnicodeError):
                writeback_reason = "writeback_inspection_failed"

    return ProjectSummary(
        project_id=entry.project_id,
        name=entry.name,
        kind=ProjectKind.LOCAL_CLONE,
        state=ProjectState.AVAILABLE,
        reason=None,
        branch=branch,
        head=head,
        features=ProjectFeatures.local_draft(writeback=writeback_available),
        relative_path=entry.relative_path,
        writeback_reason=writeback_reason,
    )


def resolve_project_path(root: Path, relative_path: str) -> Path:
    normalized = normalize_project_path(relative_path)
    project_path, error = _resolve_project_path(Path(root), normalized)
    if error is not None or project_path is None:
        raise ProjectCatalogError(error or "project_unavailable", "Project path is unavailable")
    return project_path


def project_snapshot_path_is_allowed(path: str) -> bool:
    if not isinstance(path, str) or not path or "\\" in path:
        return False
    if any(_is_control(character) for character in path):
        return False
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or any(part in {"", ".", ".."} for part in pure_path.parts):
        return False
    if pure_path.as_posix() != path:
        return False
    lowered = path.casefold()
    parts = PurePosixPath(lowered).parts
    name = parts[-1]
    if name == "agents.md" and path != "AGENTS.md":
        return False
    return not (
        any(part in _SNAPSHOT_HIDDEN_SEGMENTS for part in parts)
        or name == ".env"
        or name.startswith(".env.")
        or name in _SNAPSHOT_HIDDEN_NAMES
        or name.endswith(_SNAPSHOT_HIDDEN_SUFFIXES)
    )


def validate_git_tree(payload: bytes) -> None:
    seen_paths: set[str] = set()
    seen_folded_paths: set[str] = set()
    for record in payload.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", maxsplit=1)
            mode, object_type, object_id = header.split(b" ", maxsplit=2)
        except ValueError as exc:
            raise ProjectCatalogError("git_tree_invalid", "Git tree output is invalid") from exc
        if mode == b"120000":
            raise ProjectCatalogError("git_symlink_not_allowed", "Git symlink entries are not allowed")
        if mode == b"160000" or object_type == b"commit":
            raise ProjectCatalogError("git_submodule_not_allowed", "Git submodules are not allowed")
        if object_type != b"blob" or mode not in {b"100644", b"100755"}:
            raise ProjectCatalogError("git_tree_invalid", "Git tree entry is unsupported")
        if len(object_id) not in {40, 64} or any(
            character not in b"0123456789abcdef" for character in object_id.lower()
        ):
            raise ProjectCatalogError("git_tree_invalid", "Git object id is invalid")
        try:
            path = raw_path.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ProjectCatalogError("git_path_encoding_not_supported", "Git paths must use UTF-8") from exc
        normalized = PurePosixPath(path)
        if normalized.is_absolute() or any(part in {"", ".", ".."} for part in normalized.parts):
            raise ProjectCatalogError("git_tree_invalid", "Git path is unsafe")
        canonical = "/".join(normalized.parts)
        if canonical != path or canonical in seen_paths:
            raise ProjectCatalogError("git_tree_invalid", "Git path is not canonical")
        folded = canonical.casefold()
        if folded in seen_folded_paths:
            raise ProjectCatalogError("git_path_case_conflict", "Git paths conflict by case")
        seen_paths.add(canonical)
        seen_folded_paths.add(folded)


def _normalize_project_name(value: Any) -> str:
    if not isinstance(value, str) or value != unicodedata.normalize("NFC", value):
        raise ProjectCatalogError("manifest_name_invalid", "Project name is invalid")
    if not value or value != value.strip() or len(value) > MAX_PROJECT_NAME_CHARS:
        raise ProjectCatalogError("manifest_name_invalid", "Project name is invalid")
    if any(_is_control(character) for character in value):
        raise ProjectCatalogError("manifest_name_invalid", "Project name contains control characters")
    return value


def _is_control(value: str) -> bool:
    return unicodedata.category(value).startswith("C")


def _resolve_project_path(root: Path, relative_path: str) -> tuple[Path | None, str | None]:
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        return None, "projects_root_unavailable"
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        try:
            if current.is_symlink():
                return None, "project_symlink_not_allowed"
        except OSError:
            return None, "project_unavailable"
    try:
        resolved_root = root.resolve(strict=True)
        resolved_project = current.resolve(strict=True)
        if os.path.commonpath((str(resolved_root), str(resolved_project))) != str(resolved_root):
            return None, "project_path_outside_root"
    except (OSError, ValueError):
        return None, "project_unavailable"
    if not resolved_project.is_dir():
        return None, "project_unavailable"
    return resolved_project, None


def _run_git(project_path: Path, arguments: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        build_safe_git_command(project_path, arguments),
        cwd=project_path,
        env=build_safe_git_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=GIT_INSPECTION_TIMEOUT_SECONDS,
    )


def build_safe_git_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return environment


def build_safe_git_command(project_path: Path, arguments: Sequence[str]) -> tuple[str, ...]:
    return (
        "git",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "credential.helper=",
        "-c",
        f"core.attributesFile={os.devnull}",
        "-c",
        f"safe.directory={project_path}",
        *arguments,
    )


def _run_git_text(runner: GitRunner, project_path: Path, arguments: Sequence[str]) -> str:
    result = runner(project_path, arguments)
    _require_git_success(result)
    return result.stdout.decode("utf-8", errors="strict")


def _require_git_success(result: subprocess.CompletedProcess[bytes]) -> None:
    if result.returncode != 0:
        raise ProjectCatalogError("git_inspection_failed", "Git inspection failed")


def _unavailable(entry: ProjectManifestEntry, reason: str) -> ProjectSummary:
    return ProjectSummary(
        project_id=entry.project_id,
        name=entry.name,
        kind=ProjectKind.LOCAL_CLONE,
        state=ProjectState.UNAVAILABLE,
        reason=reason,
        branch=None,
        head=None,
        features=ProjectFeatures.local_draft(),
        relative_path=entry.relative_path,
        writeback_reason=reason,
    )
