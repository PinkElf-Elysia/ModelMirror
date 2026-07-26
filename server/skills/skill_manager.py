from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

from .draft_store import SkillDraftValidationError, WorkspaceSkillDraftStore


class SkillManagerError(Exception):
    """Base error raised by the Skill manager."""


class SkillInstallError(SkillManagerError):
    """Raised when a Skill cannot be installed."""


class SkillNotFoundError(SkillManagerError):
    """Raised when a requested Skill is not installed."""


class SkillValidationError(SkillManagerError):
    """Raised when a Skill source or identifier is invalid."""


@dataclass(frozen=True)
class InstalledSkill:
    """Metadata stored for an installed Skill."""

    skill_id: str
    name: str
    description: str
    repo_url: str
    sub_path: str
    installed_at: float


class SkillManager:
    """Install, list, read, and uninstall local Skill packages.

    The manager stores Skill directories under ``server/skills/installed`` by
    default. It is intentionally filesystem-backed for the MVP so it can be
    moved to a database later without changing the REST surface.
    """

    def __init__(
        self,
        installed_dir: Path | None = None,
        tmp_dir: Path | None = None,
        *,
        allow_local_repos: bool = False,
        git_timeout_seconds: int = 30,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.installed_dir = Path(
            installed_dir
            or os.getenv("SKILL_INSTALLED_DIR")
            or package_dir / "installed"
        )
        self.tmp_dir = Path(
            tmp_dir or os.getenv("SKILL_TMP_DIR") or package_dir / "tmp"
        )
        self.allow_local_repos = allow_local_repos
        self.git_timeout_seconds = git_timeout_seconds
        self.metadata_path = self.installed_dir / "installed.json"
        self._lock = threading.RLock()

    def install_skill(self, repo_url: str, sub_path: str = "") -> InstalledSkill:
        """Install a Skill from a GitHub repository subdirectory.

        Args:
            repo_url: GitHub repository URL, or a local repository path only
                when ``allow_local_repos`` is enabled for tests.
            sub_path: Repository subdirectory containing ``SKILL.md``.

        Returns:
            Metadata for the installed Skill.
        """

        normalized_repo_url = self._validate_repo_url(repo_url)
        normalized_sub_path = self._validate_sub_path(sub_path)
        skill_id = self._build_skill_id(normalized_repo_url, normalized_sub_path)

        with self._lock:
            self._ensure_dirs()
            target_dir = self._safe_skill_dir(skill_id)
            tmp_root = Path(
                tempfile.mkdtemp(prefix=f"{skill_id}-", dir=str(self.tmp_dir))
            )
            checkout_dir = tmp_root / "repo"
            try:
                self._git_sparse_clone(
                    normalized_repo_url,
                    normalized_sub_path,
                    checkout_dir,
                )
                source_dir = checkout_dir / normalized_sub_path if normalized_sub_path else checkout_dir
                skill_md = source_dir / "SKILL.md"
                if not skill_md.exists():
                    raise SkillInstallError(
                        f"SKILL.md not found in '{normalized_sub_path or '.'}'"
                    )

                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(source_dir, target_dir)

                metadata = self._parse_skill_metadata(
                    skill_id,
                    normalized_repo_url,
                    normalized_sub_path,
                    target_dir / "SKILL.md",
                )
                installed = self._read_metadata()
                installed[skill_id] = asdict(metadata)
                self._write_metadata(installed)
                return metadata
            finally:
                shutil.rmtree(tmp_root, ignore_errors=True)

    def install_workspace_draft(
        self,
        *,
        draft_id: str,
        slug: str,
        skill_markdown: str,
        files: dict[str, str],
    ) -> InstalledSkill:
        """Explicitly install a reviewed Workspace Skill draft.

        This path never overwrites an installed Skill. A changed draft must be
        installed as a new package after another explicit user action.
        """

        frontmatter = WorkspaceSkillDraftStore._parse_frontmatter(skill_markdown)
        try:
            normalized = WorkspaceSkillDraftStore.validate_package(
                name=frontmatter.get("name", ""),
                slug=slug,
                description=frontmatter.get("description", ""),
                skill_markdown=skill_markdown,
                files=files,
            )
        except SkillDraftValidationError as exc:
            raise SkillValidationError(str(exc)) from exc
        clean_slug = normalized["slug"]
        skill_markdown = normalized["skill_markdown"]
        files = normalized["files"]
        suffix = re.sub(r"[^a-z0-9]+", "", draft_id.lower())[-12:]
        skill_id = self._validate_skill_id(f"workspace-{clean_slug}-{suffix}")
        with self._lock:
            self._ensure_dirs()
            installed = self._read_metadata()
            if skill_id in installed:
                raise SkillInstallError(
                    "This Workspace Skill draft is already installed."
                )
            target_dir = self._safe_skill_dir(skill_id)
            if target_dir.exists():
                raise SkillInstallError("Workspace Skill target already exists.")
            temp_dir = Path(
                tempfile.mkdtemp(prefix=f"{skill_id}-", dir=str(self.tmp_dir))
            )
            try:
                (temp_dir / "SKILL.md").write_text(skill_markdown, encoding="utf-8")
                for relative_path, content in files.items():
                    target = temp_dir.joinpath(*relative_path.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                shutil.copytree(temp_dir, target_dir)
                metadata = self._parse_skill_metadata(
                    skill_id,
                    f"workspace://draft/{draft_id}",
                    "",
                    target_dir / "SKILL.md",
                )
                installed[skill_id] = asdict(metadata)
                self._write_metadata(installed)
                return metadata
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def install_plugin_skill(
        self,
        *,
        plugin_id: str,
        plugin_slug: str,
        plugin_version: int,
        skill_slug: str,
        skill_markdown: str,
        files: dict[str, bytes],
    ) -> InstalledSkill:
        """Materialize one reviewed Plugin Skill as a versioned package."""

        if plugin_version < 1:
            raise SkillValidationError("Plugin version must be positive.")
        clean_plugin_slug = re.sub(
            r"[^a-z0-9]+", "-", plugin_slug.strip().lower()
        ).strip("-")
        clean_skill_slug = re.sub(
            r"[^a-z0-9]+", "-", skill_slug.strip().lower()
        ).strip("-")
        if not clean_plugin_slug or not clean_skill_slug:
            raise SkillValidationError("Plugin and Skill slugs are required.")
        if len(files) > 40:
            raise SkillValidationError("A Plugin Skill may contain at most 40 files.")
        total_bytes = len(skill_markdown.encode("utf-8"))
        normalized_files: dict[str, bytes] = {}
        for relative_path, content in files.items():
            normalized_path = WorkspaceSkillDraftStore._validate_path(
                relative_path
            )
            if len(content) > 1024 * 1024:
                raise SkillValidationError(
                    f"Plugin Skill file exceeds 1 MB: {normalized_path}"
                )
            total_bytes += len(content)
            normalized_files[normalized_path] = bytes(content)
        if total_bytes > 5 * 1024 * 1024:
            raise SkillValidationError("A Plugin Skill may not exceed 5 MB.")

        frontmatter = WorkspaceSkillDraftStore._parse_frontmatter(skill_markdown)
        try:
            WorkspaceSkillDraftStore.validate_package(
                name=frontmatter.get("name", ""),
                slug=clean_skill_slug,
                description=frontmatter.get("description", ""),
                skill_markdown=skill_markdown,
                files={},
            )
        except SkillDraftValidationError as exc:
            raise SkillValidationError(str(exc)) from exc

        skill_id = self._validate_skill_id(
            f"plugin-{clean_plugin_slug}-v{plugin_version}-{clean_skill_slug}"[:161]
        )
        with self._lock:
            self._ensure_dirs()
            installed = self._read_metadata()
            if skill_id in installed:
                return InstalledSkill(**installed[skill_id])
            target_dir = self._safe_skill_dir(skill_id)
            if target_dir.exists():
                raise SkillInstallError("Plugin Skill target already exists.")
            temp_dir = Path(
                tempfile.mkdtemp(prefix=f"{skill_id}-", dir=str(self.tmp_dir))
            )
            try:
                (temp_dir / "SKILL.md").write_text(skill_markdown, encoding="utf-8")
                for relative_path, content in normalized_files.items():
                    target = temp_dir.joinpath(*relative_path.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                shutil.copytree(temp_dir, target_dir)
                metadata = self._parse_skill_metadata(
                    skill_id,
                    f"plugin://{plugin_id}/v{plugin_version}",
                    clean_skill_slug,
                    target_dir / "SKILL.md",
                )
                installed[skill_id] = asdict(metadata)
                self._write_metadata(installed)
                return metadata
            except Exception:
                shutil.rmtree(target_dir, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def uninstall_skill(self, skill_id: str) -> None:
        """Remove an installed Skill by id."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            installed = self._read_metadata()
            if normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")

            target_dir = self._safe_skill_dir(normalized_skill_id)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            installed.pop(normalized_skill_id, None)
            self._write_metadata(installed)

    def list_installed_skills(self) -> list[InstalledSkill]:
        """Return installed Skill metadata sorted by installation time."""

        with self._lock:
            return [
                InstalledSkill(**item)
                for item in sorted(
                    self._read_metadata().values(),
                    key=lambda value: float(value.get("installed_at", 0)),
                    reverse=True,
                )
            ]

    def get_skill_content(self, skill_id: str) -> str:
        """Read the raw ``SKILL.md`` content for an installed Skill."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            installed = self._read_metadata()
            if normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")

            skill_md = self._safe_skill_dir(normalized_skill_id) / "SKILL.md"
            if not skill_md.exists():
                raise SkillNotFoundError(
                    f"Skill '{normalized_skill_id}' is missing SKILL.md"
                )
            return skill_md.read_text(encoding="utf-8", errors="replace")

    def get_skill_directory(self, skill_id: str) -> Path:
        """Return the validated installed directory for Runtime staging."""

        normalized_skill_id = self._validate_skill_id(skill_id)
        with self._lock:
            installed = self._read_metadata()
            if normalized_skill_id not in installed:
                raise SkillNotFoundError(f"Skill '{normalized_skill_id}' is not installed")
            target = self._safe_skill_dir(normalized_skill_id)
            if not target.exists() or not target.is_dir():
                raise SkillNotFoundError(
                    f"Skill '{normalized_skill_id}' directory is unavailable"
                )
            return target

    def _ensure_dirs(self) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self._write_metadata({})

    def _read_metadata(self) -> dict[str, dict[str, object]]:
        self._ensure_dirs_for_read()
        try:
            raw = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if isinstance(raw, dict) and isinstance(raw.get("skills"), dict):
            return raw["skills"]  # type: ignore[return-value]
        if isinstance(raw, dict):
            return raw  # backward-compatible flat shape
        return {}

    def _write_metadata(self, skills: dict[str, dict[str, object]]) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        payload = {"skills": skills}
        self.metadata_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_dirs_for_read(self) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self.metadata_path.write_text('{"skills": {}}', encoding="utf-8")

    def _safe_skill_dir(self, skill_id: str) -> Path:
        target_dir = (self.installed_dir / skill_id).resolve()
        installed_root = self.installed_dir.resolve()
        if target_dir != installed_root and installed_root in target_dir.parents:
            return target_dir
        raise SkillValidationError(f"Unsafe Skill id '{skill_id}'")

    def _git_sparse_clone(
        self,
        repo_url: str,
        sub_path: str,
        checkout_dir: Path,
    ) -> None:
        clone_command = [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--sparse",
            repo_url,
            str(checkout_dir),
        ]
        self._run_command(clone_command)

        if sub_path:
            self._run_command(
                ["git", "-C", str(checkout_dir), "sparse-checkout", "set", sub_path]
            )

    def _run_command(self, command: list[str]) -> None:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.git_timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise SkillInstallError("git is not available on this server") from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillInstallError("Skill install timed out") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise SkillInstallError(stderr or "git command failed")

    def _parse_skill_metadata(
        self,
        skill_id: str,
        repo_url: str,
        sub_path: str,
        skill_md: Path,
    ) -> InstalledSkill:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
        frontmatter = self._parse_frontmatter(content)
        heading = self._first_heading(content)
        description = (
            str(frontmatter.get("description", "")).strip()
            or self._first_paragraph(content)
            or f"Installed Skill from {repo_url}"
        )
        name = (
            str(frontmatter.get("name", "")).strip()
            or heading
            or self._title_from_path(sub_path)
        )

        return InstalledSkill(
            skill_id=skill_id,
            name=name[:120],
            description=description[:500],
            repo_url=repo_url,
            sub_path=sub_path,
            installed_at=time.time(),
        )

    @staticmethod
    def _parse_frontmatter(content: str) -> dict[str, str]:
        if not content.startswith("---"):
            return {}

        lines = content.splitlines()
        values: dict[str, str] = {}
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            if key in {"name", "description"}:
                values[key] = value.strip().strip('"').strip("'")
        return values

    @staticmethod
    def _first_heading(content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    @staticmethod
    def _first_paragraph(content: str) -> str:
        in_frontmatter = content.startswith("---")
        for line in content.splitlines():
            stripped = line.strip()
            if in_frontmatter:
                if stripped == "---":
                    in_frontmatter = False
                continue
            if not stripped or stripped.startswith("#") or stripped.startswith("---"):
                continue
            return stripped
        return ""

    @staticmethod
    def _title_from_path(sub_path: str) -> str:
        last_part = (sub_path.rstrip("/") or "skill").split("/")[-1]
        return last_part.replace("-", " ").replace("_", " ").title()

    def _validate_repo_url(self, repo_url: str) -> str:
        raw = repo_url.strip()
        if not raw:
            raise SkillValidationError("repo_url is required")

        if self.allow_local_repos:
            path = Path(raw)
            if path.exists():
                return str(path.resolve())
            parsed = urlparse(raw)
            if parsed.scheme == "file":
                local_path = Path(parsed.path)
                if local_path.exists():
                    return raw

        parsed = urlparse(raw)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            raise SkillValidationError("Only https://github.com repositories are allowed")

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise SkillValidationError("GitHub repo URL must include owner and repo")

        owner, repo = parts[:2]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
            raise SkillValidationError("Invalid GitHub owner")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\.git)?", repo):
            raise SkillValidationError("Invalid GitHub repo")

        return f"https://github.com/{owner}/{repo.removesuffix('.git')}"

    @staticmethod
    def _validate_sub_path(sub_path: str) -> str:
        normalized = sub_path.strip().strip("/\\")
        if not normalized:
            return ""
        if "\\" in normalized:
            normalized = normalized.replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if any(part in {"", ".", ".."} for part in parts):
            raise SkillValidationError("Invalid Skill sub_path")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
            raise SkillValidationError("Invalid Skill sub_path")
        return "/".join(parts)

    @staticmethod
    def _validate_skill_id(skill_id: str) -> str:
        normalized = skill_id.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,160}", normalized):
            raise SkillValidationError("Invalid Skill id")
        return normalized

    @staticmethod
    def _build_skill_id(repo_url: str, sub_path: str) -> str:
        parsed = urlparse(repo_url)
        if parsed.scheme in {"http", "https"}:
            raw = f"{parsed.path.strip('/')}/{sub_path}".strip("/")
        else:
            raw = f"{Path(repo_url).name}/{sub_path}".strip("/")

        slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
        return slug[:140] or "skill"

