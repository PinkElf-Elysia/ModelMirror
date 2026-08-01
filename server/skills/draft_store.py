from __future__ import annotations

import json
import os
import posixpath
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal


SkillDraftStatus = Literal["draft", "installed", "archived"]


class SkillDraftError(Exception):
    """Base error for workspace Skill drafts."""


class SkillDraftNotFoundError(SkillDraftError):
    pass


class SkillDraftConflictError(SkillDraftError):
    pass


class SkillDraftValidationError(SkillDraftError):
    pass


@dataclass(slots=True)
class WorkspaceSkillDraft:
    draft_id: str
    name: str
    slug: str
    description: str
    skill_markdown: str
    files: dict[str, str] = field(default_factory=dict)
    status: SkillDraftStatus = "draft"
    revision: int = 1
    source_proposal_id: str | None = None
    installed_skill_id: str | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class WorkspaceSkillDraftStore:
    """File-backed, reviewable Skill packages that are not installed by default."""

    MAX_FILES = 40
    MAX_FILE_BYTES = 1024 * 1024
    MAX_TOTAL_BYTES = 5 * 1024 * 1024
    ALLOWED_ROOTS = {"scripts", "references", "assets", "agents"}

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        runtime_dir = os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
        self.storage_dir = Path(
            storage_dir or runtime_dir or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "skill_drafts.json"
        self._lock = threading.RLock()
        self._items: dict[str, WorkspaceSkillDraft] = {}
        self._load()

    def create(
        self,
        *,
        name: str,
        slug: str,
        description: str,
        skill_markdown: str,
        files: dict[str, str] | None = None,
        source_proposal_id: str | None = None,
    ) -> WorkspaceSkillDraft:
        normalized = self.validate_package(
            name=name,
            slug=slug,
            description=description,
            skill_markdown=skill_markdown,
            files=files or {},
        )
        now = time.time()
        item = WorkspaceSkillDraft(
            draft_id=f"skilldraft_{uuid.uuid4().hex}",
            source_proposal_id=source_proposal_id,
            created_at=now,
            updated_at=now,
            **normalized,
        )
        with self._lock:
            self._items[item.draft_id] = item
            self._save_unlocked()
        return self._copy(item)

    def require(self, draft_id: str) -> WorkspaceSkillDraft:
        with self._lock:
            item = self._items.get(draft_id)
            if item is None:
                raise SkillDraftNotFoundError(f"Skill draft not found: {draft_id}")
            return self._copy(item)

    def list(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[WorkspaceSkillDraft]:
        with self._lock:
            items = list(self._items.values())
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return [self._copy(item) for item in items[: max(1, min(limit, 500))]]

    def update(
        self,
        draft_id: str,
        *,
        revision: int,
        name: str | None = None,
        slug: str | None = None,
        description: str | None = None,
        skill_markdown: str | None = None,
        files: dict[str, str] | None = None,
    ) -> WorkspaceSkillDraft:
        with self._lock:
            item = self._require_unlocked(draft_id)
            self._require_revision(item, revision)
            if item.status == "archived":
                raise SkillDraftConflictError("Archived Skill drafts cannot be edited.")
            normalized = self.validate_package(
                name=item.name if name is None else name,
                slug=item.slug if slug is None else slug,
                description=item.description if description is None else description,
                skill_markdown=(
                    item.skill_markdown if skill_markdown is None else skill_markdown
                ),
                files=item.files if files is None else files,
            )
            for key, value in normalized.items():
                setattr(item, key, value)
            item.status = "draft"
            item.installed_skill_id = None
            item.validation = {}
            item.revision += 1
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    def set_validation(
        self, draft_id: str, *, revision: int, validation: dict[str, Any]
    ) -> WorkspaceSkillDraft:
        with self._lock:
            item = self._require_unlocked(draft_id)
            self._require_revision(item, revision)
            item.validation = dict(validation)
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    def mark_installed(
        self, draft_id: str, *, revision: int, skill_id: str
    ) -> WorkspaceSkillDraft:
        with self._lock:
            item = self._require_unlocked(draft_id)
            self._require_revision(item, revision)
            item.status = "installed"
            item.installed_skill_id = str(skill_id).strip()
            item.revision += 1
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    def archive(self, draft_id: str, *, revision: int) -> WorkspaceSkillDraft:
        with self._lock:
            item = self._require_unlocked(draft_id)
            self._require_revision(item, revision)
            item.status = "archived"
            item.revision += 1
            item.updated_at = time.time()
            self._save_unlocked()
            return self._copy(item)

    def validate_draft(self, draft_id: str) -> dict[str, Any]:
        item = self.require(draft_id)
        normalized = self.validate_package(
            name=item.name,
            slug=item.slug,
            description=item.description,
            skill_markdown=item.skill_markdown,
            files=item.files,
        )
        return {
            "valid": True,
            "issues": [],
            "file_count": 1 + len(normalized["files"]),
            "total_bytes": self._total_bytes(
                normalized["skill_markdown"], normalized["files"]
            ),
        }

    @classmethod
    def validate_package(
        cls,
        *,
        name: str,
        slug: str,
        description: str,
        skill_markdown: str,
        files: dict[str, str],
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        clean_slug = re.sub(r"[^a-z0-9-]+", "-", str(slug or "").lower()).strip("-")
        clean_description = str(description or "").strip()
        markdown = str(skill_markdown or "")
        if not clean_name or len(clean_name) > 120:
            raise SkillDraftValidationError("Skill name is required and limited to 120 characters.")
        if not clean_slug or len(clean_slug) > 80:
            raise SkillDraftValidationError("Skill slug is required and limited to 80 characters.")
        if len(clean_description) > 1000:
            raise SkillDraftValidationError("Skill description is limited to 1,000 characters.")
        if not markdown.strip() or len(markdown.encode("utf-8")) > cls.MAX_FILE_BYTES:
            raise SkillDraftValidationError("SKILL.md is required and limited to 1MB.")
        frontmatter = cls._parse_frontmatter(markdown)
        if not frontmatter.get("name") or not frontmatter.get("description"):
            raise SkillDraftValidationError(
                "SKILL.md frontmatter must include name and description."
            )
        if not isinstance(files, dict) or len(files) > cls.MAX_FILES - 1:
            raise SkillDraftValidationError("A Skill package can contain at most 40 files.")
        clean_files: dict[str, str] = {}
        for raw_path, raw_content in files.items():
            path = cls._validate_path(raw_path)
            content = str(raw_content)
            if len(content.encode("utf-8")) > cls.MAX_FILE_BYTES:
                raise SkillDraftValidationError(f"Skill file exceeds 1MB: {path}")
            clean_files[path] = content
        if cls._total_bytes(markdown, clean_files) > cls.MAX_TOTAL_BYTES:
            raise SkillDraftValidationError("Skill package exceeds 5MB.")
        return {
            "name": clean_name,
            "slug": clean_slug,
            "description": clean_description or frontmatter["description"][:1000],
            "skill_markdown": markdown,
            "files": clean_files,
        }

    @classmethod
    def _validate_path(cls, raw_path: Any) -> str:
        value = str(raw_path or "").replace("\\", "/").strip()
        normalized = posixpath.normpath(value)
        path = PurePosixPath(normalized)
        if (
            not value
            or value.startswith("/")
            or normalized in {".", ".."}
            or ".." in path.parts
            or any(part.startswith(".") for part in path.parts)
            or path.parts[0] not in cls.ALLOWED_ROOTS
        ):
            raise SkillDraftValidationError(f"Unsafe Skill file path: {value}")
        if path.parts[0] == "agents" and normalized != "agents/openai.yaml":
            raise SkillDraftValidationError(
                "Only agents/openai.yaml is supported under agents/."
            )
        return normalized

    @staticmethod
    def _parse_frontmatter(content: str) -> dict[str, str]:
        if not content.startswith("---"):
            return {}
        values: dict[str, str] = {}
        for line in content.splitlines()[1:]:
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() in {"name", "description"}:
                values[key.strip().lower()] = value.strip().strip('"').strip("'")
        return values

    @staticmethod
    def serialize(
        item: WorkspaceSkillDraft, *, include_content: bool = False
    ) -> dict[str, Any]:
        data = asdict(item)
        if not include_content:
            data.pop("skill_markdown", None)
            data.pop("files", None)
            data["file_count"] = 1 + len(item.files)
            data["total_bytes"] = WorkspaceSkillDraftStore._total_bytes(
                item.skill_markdown, item.files
            )
            data["file_paths"] = ["SKILL.md", *sorted(item.files)]
        return data

    @staticmethod
    def _total_bytes(markdown: str, files: dict[str, str]) -> int:
        return len(markdown.encode("utf-8")) + sum(
            len(value.encode("utf-8")) for value in files.values()
        )

    def _require_unlocked(self, draft_id: str) -> WorkspaceSkillDraft:
        item = self._items.get(draft_id)
        if item is None:
            raise SkillDraftNotFoundError(f"Skill draft not found: {draft_id}")
        return item

    @staticmethod
    def _require_revision(item: WorkspaceSkillDraft, revision: int) -> None:
        if item.revision != revision:
            raise SkillDraftConflictError(
                "Skill draft changed. Reload it before applying this operation."
            )

    def _load(self) -> None:
        with self._lock:
            if not self.snapshot_path.exists():
                return
            try:
                raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
                items = raw.get("items", []) if isinstance(raw, dict) else []
                self._items = {
                    item["draft_id"]: WorkspaceSkillDraft(**item)
                    for item in items
                    if isinstance(item, dict) and item.get("draft_id")
                }
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self._items = {}

    def _save_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.snapshot_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(
                {"version": 1, "items": [asdict(item) for item in self._items.values()]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temp_path, self.snapshot_path)

    @staticmethod
    def _copy(item: WorkspaceSkillDraft) -> WorkspaceSkillDraft:
        return WorkspaceSkillDraft(
            **json.loads(json.dumps(asdict(item), ensure_ascii=False))
        )
