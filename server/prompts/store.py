from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .models import PromptProfileDefinition, PromptProfileVersion


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PLACEHOLDER_PATTERN = re.compile(r"{{\s*([^{}]+?)\s*}}")


class PromptProfileError(Exception):
    pass


class PromptProfileNotFoundError(PromptProfileError):
    pass


class PromptProfileConflictError(PromptProfileError):
    pass


class PromptProfileValidationError(PromptProfileError):
    pass


class PromptProfileStore:
    """Atomic file-backed Prompt Profile drafts and immutable versions."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("PROMPT_PROFILE_STORAGE_DIR", "").strip()
            or os.getenv("XPERT_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.storage_path = self.storage_dir / "prompt_profiles.json"
        self._lock = threading.RLock()
        self._ensure_storage_unlocked()

    def list_profiles(
        self,
        *,
        status: str | None = None,
        search: str = "",
        limit: int = 100,
    ) -> list[PromptProfileDefinition]:
        with self._lock:
            items = self._read_unlocked()
        if status:
            items = [item for item in items if item.status == status]
        term = search.strip().lower()
        if term:
            items = [
                item
                for item in items
                if term
                in " ".join(
                    [
                        item.name,
                        item.slug,
                        item.description,
                        *item.aliases,
                        *item.tags,
                    ]
                ).lower()
            ]
        items.sort(key=lambda item: (-item.updated_at, item.id))
        return [item.model_copy(deep=True) for item in items[: max(1, min(limit, 500))]]

    def create_profile(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str = "",
        aliases: list[str] | None = None,
        template: str = "{{args}}",
        argument_hint: str = "",
        tags: list[str] | None = None,
        public_app_allowed: bool = False,
    ) -> PromptProfileDefinition:
        profile_id = f"prompt_{uuid.uuid4().hex}"
        now = time.time()
        item = PromptProfileDefinition(
            id=profile_id,
            slug=self._normalize_slug(slug or name, profile_id),
            name=self._required_text(name, "name", 120),
            description=str(description or "").strip()[:2000],
            aliases=self._clean_aliases(aliases or []),
            template=self._validate_template(template),
            argument_hint=str(argument_hint or "").strip()[:500],
            tags=self._clean_tags(tags),
            public_app_allowed=bool(public_app_allowed),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            items = self._read_unlocked()
            self._require_unique_slug(items, item.slug)
            items.append(item)
            self._write_unlocked(items)
        return item.model_copy(deep=True)

    def get_profile(self, profile_id: str) -> PromptProfileDefinition:
        with self._lock:
            return self._find_unlocked(self._read_unlocked(), profile_id).model_copy(
                deep=True
            )

    def update_profile(
        self,
        profile_id: str,
        *,
        revision: int,
        patch: dict[str, Any],
    ) -> PromptProfileDefinition:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, profile_id)
            self._require_revision(item, revision)
            if item.status == "archived":
                raise PromptProfileConflictError(
                    "Archived Prompt Profiles cannot be edited."
                )
            if "name" in patch:
                item.name = self._required_text(patch["name"], "name", 120)
            if "slug" in patch:
                next_slug = self._normalize_slug(str(patch["slug"]), item.id)
                self._require_unique_slug(items, next_slug, exclude_id=item.id)
                item.slug = next_slug
            if "description" in patch:
                item.description = str(patch["description"] or "").strip()[:2000]
            if "aliases" in patch:
                item.aliases = self._clean_aliases(patch["aliases"] or [])
            if "template" in patch:
                item.template = self._validate_template(patch["template"])
            if "argument_hint" in patch:
                item.argument_hint = str(patch["argument_hint"] or "").strip()[:500]
            if "tags" in patch:
                item.tags = self._clean_tags(patch["tags"])
            if "public_app_allowed" in patch:
                item.public_app_allowed = bool(patch["public_app_allowed"])
            item.draft_revision += 1
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def validate_profile(self, profile_id: str) -> dict[str, Any]:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, profile_id)
            issues = self._validation_issues_unlocked(item, items)
        return {
            "valid": not issues,
            "issues": issues,
            "profile_id": item.id,
            "draft_revision": item.draft_revision,
        }

    def publish_profile(
        self,
        profile_id: str,
        *,
        revision: int,
    ) -> PromptProfileVersion:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, profile_id)
            self._require_revision(item, revision)
            if item.status == "archived":
                raise PromptProfileConflictError(
                    "Archived Prompt Profiles cannot be published."
                )
            issues = self._validation_issues_unlocked(item, items)
            if issues:
                raise PromptProfileValidationError(
                    "; ".join(str(issue["message"]) for issue in issues)
                )
            next_version = max((value.version for value in item.versions), default=0) + 1
            canonical = json.dumps(
                {
                    "name": item.name,
                    "description": item.description,
                    "aliases": item.aliases,
                    "template": item.template,
                    "argument_hint": item.argument_hint,
                    "tags": item.tags,
                    "public_app_allowed": item.public_app_allowed,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            snapshot = PromptProfileVersion(
                version=next_version,
                draft_revision=item.draft_revision,
                name=item.name,
                description=item.description,
                aliases=list(item.aliases),
                template=item.template,
                argument_hint=item.argument_hint,
                tags=list(item.tags),
                public_app_allowed=item.public_app_allowed,
                checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                published_at=time.time(),
            )
            item.versions.append(snapshot)
            item.published_version = next_version
            item.status = "published"
            item.updated_at = snapshot.published_at
            self._write_unlocked(items)
            return snapshot.model_copy(deep=True)

    def archive_profile(
        self, profile_id: str, *, revision: int
    ) -> PromptProfileDefinition:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, profile_id)
            self._require_revision(item, revision)
            item.status = "archived"
            item.draft_revision += 1
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def list_versions(self, profile_id: str) -> list[PromptProfileVersion]:
        item = self.get_profile(profile_id)
        return [value.model_copy(deep=True) for value in reversed(item.versions)]

    def get_version(
        self, profile_id: str, version: int | None = None
    ) -> PromptProfileVersion:
        item = self.get_profile(profile_id)
        target = version if version is not None else item.published_version
        if target is None:
            raise PromptProfileNotFoundError("Prompt Profile has not been published.")
        for snapshot in item.versions:
            if snapshot.version == target:
                return snapshot.model_copy(deep=True)
        raise PromptProfileNotFoundError(f"Prompt Profile version not found: {target}")

    def render(self, profile_id: str, version: int, args: str) -> str:
        snapshot = self.get_version(profile_id, version)
        clean_args = str(args or "")
        if len(clean_args) > 8000:
            raise PromptProfileValidationError(
                "Prompt command arguments are limited to 8000 characters."
            )
        return re.sub(r"{{\s*args\s*}}", clean_args, snapshot.template)

    def resolve_alias(
        self,
        alias: str,
        resolved_profiles: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        clean_alias = alias.strip().lower()
        for profile in resolved_profiles:
            aliases = [str(value).lower() for value in profile.get("aliases") or []]
            if clean_alias in aliases:
                return dict(profile)
        return None

    def _validation_issues_unlocked(
        self,
        item: PromptProfileDefinition,
        items: list[PromptProfileDefinition],
    ) -> list[dict[str, str]]:
        issues: list[dict[str, str]] = []
        if not item.aliases:
            issues.append(
                {
                    "code": "prompt_alias_required",
                    "message": "At least one slash-command alias is required.",
                }
            )
        published_aliases: dict[str, str] = {}
        for other in items:
            if other.id == item.id or other.status != "published":
                continue
            for version in other.versions[-1:]:
                for alias in version.aliases:
                    published_aliases[alias] = other.id
        conflicts = sorted(alias for alias in item.aliases if alias in published_aliases)
        if conflicts:
            issues.append(
                {
                    "code": "prompt_alias_conflict",
                    "message": "Published command aliases already exist: "
                    + ", ".join(conflicts),
                }
            )
        return issues

    @staticmethod
    def _validate_template(value: Any) -> str:
        template = str(value or "").strip()
        if not template or len(template) > 20_000:
            raise PromptProfileValidationError(
                "Prompt template is required and limited to 20000 characters."
            )
        placeholders = {name.strip() for name in PLACEHOLDER_PATTERN.findall(template)}
        unknown = sorted(placeholders - {"args"})
        if unknown:
            raise PromptProfileValidationError(
                "Unsupported Prompt placeholders: " + ", ".join(unknown)
            )
        return template

    @staticmethod
    def _clean_aliases(values: Any) -> list[str]:
        if not isinstance(values, list):
            raise PromptProfileValidationError("aliases must be an array.")
        aliases: list[str] = []
        for raw in values:
            alias = str(raw or "").strip().lower().lstrip("/")
            if not ALIAS_PATTERN.fullmatch(alias):
                raise PromptProfileValidationError(
                    f"Invalid Prompt command alias: {alias or '<empty>'}."
                )
            if alias not in aliases:
                aliases.append(alias)
        if len(aliases) > 5:
            raise PromptProfileValidationError(
                "Prompt Profiles support at most 5 aliases."
            )
        return aliases

    @staticmethod
    def _clean_tags(values: Any) -> list[str]:
        if values is None:
            return []
        if not isinstance(values, list):
            raise PromptProfileValidationError("tags must be an array.")
        result: list[str] = []
        for raw in values:
            value = str(raw or "").strip()[:80]
            if value and value not in result:
                result.append(value)
        return result[:30]

    @staticmethod
    def _required_text(value: Any, label: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise PromptProfileValidationError(f"{label} is required.")
        return text[:limit]

    @staticmethod
    def _normalize_slug(value: str, fallback: str) -> str:
        slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
        if not slug:
            slug = fallback.replace("_", "-")[-32:]
        if not SLUG_PATTERN.fullmatch(slug):
            raise PromptProfileValidationError(f"Invalid Prompt Profile slug: {slug}")
        return slug

    @staticmethod
    def _require_revision(item: PromptProfileDefinition, revision: int) -> None:
        if item.draft_revision != int(revision):
            raise PromptProfileConflictError(
                "Prompt Profile draft changed. Reload and retry."
            )

    @staticmethod
    def _require_unique_slug(
        items: list[PromptProfileDefinition],
        slug: str,
        *,
        exclude_id: str | None = None,
    ) -> None:
        if any(item.slug == slug and item.id != exclude_id for item in items):
            raise PromptProfileValidationError(
                f"Prompt Profile slug already exists: {slug}"
            )

    @staticmethod
    def _find_unlocked(
        items: list[PromptProfileDefinition], profile_id: str
    ) -> PromptProfileDefinition:
        for item in items:
            if item.id == profile_id or item.slug == profile_id:
                return item
        raise PromptProfileNotFoundError(f"Prompt Profile not found: {profile_id}")

    def _ensure_storage_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write_unlocked([])

    def _read_unlocked(self) -> list[PromptProfileDefinition]:
        self._ensure_storage_unlocked()
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = []
        if not isinstance(payload, list):
            payload = []
        return [PromptProfileDefinition.model_validate(item) for item in payload]

    def _write_unlocked(self, items: list[PromptProfileDefinition]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.storage_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in items],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temp_path, self.storage_path)
