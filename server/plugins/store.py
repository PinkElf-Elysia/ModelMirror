from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from server.prompts.models import ResolvedPromptProfile
    from server.prompts.store import ALIAS_PATTERN, PLACEHOLDER_PATTERN
except ModuleNotFoundError:
    from prompts.models import ResolvedPromptProfile
    from prompts.store import ALIAS_PATTERN, PLACEHOLDER_PATTERN

from .models import (
    PluginDefinition,
    PluginMiddlewarePreset,
    PluginSkillDefinition,
    PluginToolsetReference,
    PluginVersion,
)


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


class PluginError(Exception):
    pass


class PluginNotFoundError(PluginError):
    pass


class PluginConflictError(PluginError):
    pass


class PluginValidationError(PluginError):
    pass


class PluginStore:
    """Trusted local declarative Plugin package store."""

    MAX_PACKAGE_BYTES = 20 * 1024 * 1024
    MAX_FILES = 200
    MAX_RESOURCE_ITEMS = 10

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("PLUGIN_STORAGE_DIR", "").strip()
            or os.getenv("XPERT_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.storage_path = self.storage_dir / "plugins.json"
        self.packages_dir = self.storage_dir / "plugin_packages"
        self._lock = threading.RLock()
        self._ensure_storage_unlocked()

    def list_plugins(
        self,
        *,
        status: str | None = None,
        search: str = "",
        limit: int = 100,
    ) -> list[PluginDefinition]:
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
                    [item.name, item.slug, item.description, *item.tags]
                ).lower()
            ]
        items.sort(key=lambda item: (-item.updated_at, item.id))
        return [item.model_copy(deep=True) for item in items[: max(1, min(limit, 500))]]

    def import_package(
        self,
        *,
        filename: str,
        content: bytes,
    ) -> PluginDefinition:
        if not filename.lower().endswith(".zip"):
            raise PluginValidationError("Plugin packages must be ZIP files.")
        files, manifest, package_checksum = self._inspect_zip(content)
        normalized = self._validate_manifest(manifest)
        plugin_id = f"plugin_{uuid.uuid4().hex}"
        now = time.time()
        item = PluginDefinition(
            id=plugin_id,
            manifest=normalized,
            package_checksum=package_checksum,
            file_count=len(files),
            total_bytes=sum(len(value) for value in files.values()),
            created_at=now,
            updated_at=now,
            **self._manifest_metadata(normalized),
        )
        with self._lock:
            items = self._read_unlocked()
            if any(other.slug == item.slug for other in items):
                raise PluginValidationError(
                    f"Plugin slug already exists: {item.slug}"
                )
            self._write_package_files(self._draft_dir(plugin_id), files)
            items.append(item)
            self._write_unlocked(items)
        return item.model_copy(deep=True)

    def get_plugin(self, plugin_id: str) -> PluginDefinition:
        with self._lock:
            return self._find_unlocked(self._read_unlocked(), plugin_id).model_copy(
                deep=True
            )

    def update_plugin(
        self,
        plugin_id: str,
        *,
        revision: int,
        patch: dict[str, Any],
    ) -> PluginDefinition:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, plugin_id)
            self._require_revision(item, revision)
            if item.status == "archived":
                raise PluginConflictError("Archived Plugins cannot be edited.")
            manifest = dict(item.manifest)
            for field in {"name", "description", "tags", "license"}:
                if field in patch:
                    manifest[field] = patch[field]
            normalized = self._validate_manifest(manifest)
            metadata = self._manifest_metadata(normalized)
            item.name = metadata["name"]
            item.description = metadata["description"]
            item.tags = metadata["tags"]
            item.license = metadata["license"]
            item.manifest = normalized
            item.draft_revision += 1
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def validate_plugin(self, plugin_id: str) -> dict[str, Any]:
        item = self.get_plugin(plugin_id)
        issues: list[dict[str, str]] = []
        try:
            self._validate_manifest(item.manifest)
            self._validate_package_skill_files(item.id, item.manifest)
        except PluginValidationError as exc:
            issues.append({"code": "plugin_package_invalid", "message": str(exc)})
        return {
            "valid": not issues,
            "issues": issues,
            "plugin_id": item.id,
            "draft_revision": item.draft_revision,
            "file_count": item.file_count,
            "total_bytes": item.total_bytes,
        }

    def next_version(self, plugin_id: str) -> int:
        item = self.get_plugin(plugin_id)
        return max((value.version for value in item.versions), default=0) + 1

    def publish_plugin(
        self,
        plugin_id: str,
        *,
        revision: int,
        installed_skill_ids: list[str],
    ) -> PluginVersion:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, plugin_id)
            self._require_revision(item, revision)
            if item.status == "archived":
                raise PluginConflictError("Archived Plugins cannot be published.")
            manifest = self._validate_manifest(item.manifest)
            self._validate_package_skill_files(item.id, manifest)
            next_version = max(
                (value.version for value in item.versions), default=0
            ) + 1
            prompts = self._prompt_snapshots(
                item.id, next_version, manifest.get("prompts") or []
            )
            skills = [
                PluginSkillDefinition.model_validate(value)
                for value in manifest.get("skills") or []
            ]
            if len(installed_skill_ids) != len(skills):
                raise PluginValidationError(
                    "Plugin Skills were not materialized before publish."
                )
            snapshot = PluginVersion(
                version=next_version,
                draft_revision=item.draft_revision,
                prompts=prompts,
                skills=skills,
                installed_skill_ids=list(installed_skill_ids),
                toolsets=[
                    PluginToolsetReference.model_validate(value)
                    for value in manifest.get("toolsets") or []
                ],
                middleware_presets=[
                    PluginMiddlewarePreset.model_validate(value)
                    for value in manifest.get("middleware_presets") or []
                ],
                package_checksum=item.package_checksum,
                file_count=item.file_count,
                total_bytes=item.total_bytes,
                published_at=time.time(),
                **self._manifest_metadata(manifest),
            )
            target = self._version_dir(item.id, next_version)
            if target.exists():
                raise PluginConflictError("Plugin version directory already exists.")
            shutil.copytree(self._draft_dir(item.id), target)
            try:
                item.versions.append(snapshot)
                item.published_version = next_version
                item.status = "published"
                item.updated_at = snapshot.published_at
                self._write_unlocked(items)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return snapshot.model_copy(deep=True)

    def archive_plugin(
        self, plugin_id: str, *, revision: int
    ) -> PluginDefinition:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, plugin_id)
            self._require_revision(item, revision)
            item.status = "archived"
            item.draft_revision += 1
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def list_versions(self, plugin_id: str) -> list[PluginVersion]:
        item = self.get_plugin(plugin_id)
        return [value.model_copy(deep=True) for value in reversed(item.versions)]

    def get_version(
        self, plugin_id: str, version: int | None = None
    ) -> PluginVersion:
        item = self.get_plugin(plugin_id)
        target = version if version is not None else item.published_version
        if target is None:
            raise PluginNotFoundError("Plugin has not been published.")
        for snapshot in item.versions:
            if snapshot.version == target:
                return snapshot.model_copy(deep=True)
        raise PluginNotFoundError(f"Plugin version not found: {target}")

    def read_draft_skill(
        self, plugin_id: str, skill: PluginSkillDefinition
    ) -> tuple[str, dict[str, bytes]]:
        root = self._safe_package_path(self._draft_dir(plugin_id), skill.root)
        skill_md = root / "SKILL.md"
        if not skill_md.is_file() or skill_md.is_symlink():
            raise PluginValidationError(
                f"Plugin Skill is missing SKILL.md: {skill.slug}"
            )
        files: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative == "SKILL.md":
                continue
            files[relative] = path.read_bytes()
        return skill_md.read_text(encoding="utf-8"), files

    def _inspect_zip(
        self, content: bytes
    ) -> tuple[dict[str, bytes], dict[str, Any], str]:
        if not content or len(content) > self.MAX_PACKAGE_BYTES:
            raise PluginValidationError("Plugin ZIP exceeds the 20 MB limit.")
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise PluginValidationError("Plugin ZIP is invalid.") from exc
        files: dict[str, bytes] = {}
        total = 0
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if (
                path.is_absolute()
                or ".." in path.parts
                or ".git" in path.parts
                or any(part.startswith(".") for part in path.parts)
            ):
                raise PluginValidationError(
                    f"Unsafe Plugin package path: {info.filename}"
                )
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise PluginValidationError("Plugin ZIP symlinks are not allowed.")
            if info.flag_bits & 0x1:
                raise PluginValidationError("Encrypted Plugin ZIP entries are not allowed.")
            data = archive.read(info)
            total += len(data)
            if total > self.MAX_PACKAGE_BYTES or len(files) >= self.MAX_FILES:
                raise PluginValidationError("Plugin package exceeds safety limits.")
            files[path.as_posix()] = data
        manifest_bytes = files.get("modelmirror-plugin.json")
        if manifest_bytes is None:
            raise PluginValidationError(
                "Plugin package must contain modelmirror-plugin.json at its root."
            )
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginValidationError("Plugin manifest is not valid UTF-8 JSON.") from exc
        if not isinstance(manifest, dict):
            raise PluginValidationError("Plugin manifest must be an object.")
        return files, manifest, hashlib.sha256(content).hexdigest()

    def _validate_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if int(manifest.get("schema_version") or 0) != 1:
            raise PluginValidationError("Plugin schema_version must be 1.")
        name = str(manifest.get("name") or "").strip()
        slug = str(manifest.get("slug") or "").strip().lower()
        if not name or len(name) > 120:
            raise PluginValidationError(
                "Plugin name is required and limited to 120 characters."
            )
        if not SLUG_PATTERN.fullmatch(slug):
            raise PluginValidationError(f"Invalid Plugin slug: {slug or '<empty>'}")
        normalized = {
            "schema_version": 1,
            "name": name,
            "slug": slug,
            "description": str(manifest.get("description") or "").strip()[:2000],
            "tags": self._string_list(manifest.get("tags"), 30, 80),
            "license": str(manifest.get("license") or "").strip()[:160],
            "prompts": list(manifest.get("prompts") or []),
            "skills": list(manifest.get("skills") or []),
            "toolsets": list(manifest.get("toolsets") or []),
            "middleware_presets": list(manifest.get("middleware_presets") or []),
        }
        for field in {
            "prompts",
            "skills",
            "toolsets",
            "middleware_presets",
        }:
            values = normalized[field]
            if not isinstance(values, list) or len(values) > self.MAX_RESOURCE_ITEMS:
                raise PluginValidationError(
                    f"Plugin {field} must contain at most 10 items."
                )
        self._prompt_snapshots("validation", 1, normalized["prompts"])
        for value in normalized["skills"]:
            PluginSkillDefinition.model_validate(value)
        for value in normalized["toolsets"]:
            PluginToolsetReference.model_validate(value)
        for value in normalized["middleware_presets"]:
            PluginMiddlewarePreset.model_validate(value)
        return normalized

    def _prompt_snapshots(
        self, plugin_id: str, plugin_version: int, prompts: list[Any]
    ) -> list[ResolvedPromptProfile]:
        snapshots: list[ResolvedPromptProfile] = []
        aliases: set[str] = set()
        for index, raw in enumerate(prompts):
            if not isinstance(raw, dict):
                raise PluginValidationError("Plugin prompts must be objects.")
            prompt_aliases = [
                str(value or "").strip().lower().lstrip("/")
                for value in raw.get("aliases") or []
            ]
            if not prompt_aliases or len(prompt_aliases) > 5:
                raise PluginValidationError(
                    "Each Plugin Prompt requires 1-5 aliases."
                )
            for alias in prompt_aliases:
                if not ALIAS_PATTERN.fullmatch(alias):
                    raise PluginValidationError(
                        f"Invalid Plugin Prompt alias: {alias}"
                    )
                if alias in aliases:
                    raise PluginValidationError(
                        f"Duplicate Plugin Prompt alias: {alias}"
                    )
                aliases.add(alias)
            template = str(raw.get("template") or "").strip()
            if not template or len(template) > 20_000:
                raise PluginValidationError(
                    "Plugin Prompt template is required and limited to 20000 characters."
                )
            unknown = sorted(
                {
                    value.strip()
                    for value in PLACEHOLDER_PATTERN.findall(template)
                }
                - {"args"}
            )
            if unknown:
                raise PluginValidationError(
                    "Unsupported Plugin Prompt placeholders: " + ", ".join(unknown)
                )
            checksum = hashlib.sha256(
                json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            snapshots.append(
                ResolvedPromptProfile(
                    profile_id=f"{plugin_id}:prompt:{index}",
                    slug=str(raw.get("slug") or prompt_aliases[0])[:80],
                    version=plugin_version,
                    name=str(raw.get("name") or prompt_aliases[0])[:120],
                    description=str(raw.get("description") or "")[:2000],
                    aliases=prompt_aliases,
                    template=template,
                    argument_hint=str(raw.get("argument_hint") or "")[:500],
                    public_app_allowed=False,
                    checksum=checksum,
                    source="plugin",
                    source_plugin_id=plugin_id,
                    source_plugin_version=plugin_version,
                )
            )
        return snapshots

    def _validate_package_skill_files(
        self, plugin_id: str, manifest: dict[str, Any]
    ) -> None:
        draft_root = self._draft_dir(plugin_id)
        for raw in manifest.get("skills") or []:
            skill = PluginSkillDefinition.model_validate(raw)
            root = self._safe_package_path(draft_root, skill.root)
            if not (root / "SKILL.md").is_file():
                raise PluginValidationError(
                    f"Plugin Skill is missing SKILL.md: {skill.slug}"
                )

    @staticmethod
    def _manifest_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": str(manifest["name"]),
            "slug": str(manifest["slug"]),
            "description": str(manifest.get("description") or ""),
            "tags": list(manifest.get("tags") or []),
            "license": str(manifest.get("license") or ""),
        }

    @staticmethod
    def _string_list(value: Any, maximum: int, item_limit: int) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise PluginValidationError("Plugin tags must be an array.")
        result: list[str] = []
        for raw in value:
            item = str(raw or "").strip()[:item_limit]
            if item and item not in result:
                result.append(item)
        return result[:maximum]

    def _safe_package_path(self, root: Path, relative: str) -> Path:
        parts = PurePosixPath(str(relative).replace("\\", "/")).parts
        if (
            not parts
            or ".." in parts
            or ".git" in parts
            or any(part.startswith(".") for part in parts)
        ):
            raise PluginValidationError(f"Unsafe Plugin path: {relative}")
        target = root.joinpath(*parts).resolve()
        resolved_root = root.resolve()
        if resolved_root not in target.parents and target != resolved_root:
            raise PluginValidationError(f"Plugin path escapes its package: {relative}")
        return target

    def _write_package_files(self, root: Path, files: dict[str, bytes]) -> None:
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=False)
        for relative, content in files.items():
            target = self._safe_package_path(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def _draft_dir(self, plugin_id: str) -> Path:
        return self.packages_dir / plugin_id / "draft"

    def _version_dir(self, plugin_id: str, version: int) -> Path:
        return self.packages_dir / plugin_id / "versions" / f"v{version}"

    @staticmethod
    def _require_revision(item: PluginDefinition, revision: int) -> None:
        if item.draft_revision != int(revision):
            raise PluginConflictError("Plugin draft changed. Reload and retry.")

    @staticmethod
    def _find_unlocked(
        items: list[PluginDefinition], plugin_id: str
    ) -> PluginDefinition:
        for item in items:
            if item.id == plugin_id or item.slug == plugin_id:
                return item
        raise PluginNotFoundError(f"Plugin not found: {plugin_id}")

    def _ensure_storage_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write_unlocked([])

    def _read_unlocked(self) -> list[PluginDefinition]:
        self._ensure_storage_unlocked()
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = []
        if not isinstance(payload, list):
            payload = []
        return [PluginDefinition.model_validate(value) for value in payload]

    def _write_unlocked(self, items: list[PluginDefinition]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        temp = self.storage_path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in items],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temp, self.storage_path)
