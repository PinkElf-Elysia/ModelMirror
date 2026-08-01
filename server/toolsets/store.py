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

from .models import (
    MCPConnectionProfile,
    ToolDefinition,
    ToolsetDefinition,
    ToolsetKind,
    ToolsetVersion,
)


TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,199}$")


class ToolsetStoreError(Exception):
    """Base Toolset persistence error."""


class ToolsetNotFoundError(ToolsetStoreError):
    """Raised when a Toolset or version does not exist."""


class ToolsetConflictError(ToolsetStoreError):
    """Raised when a stale draft revision is submitted."""


class ToolsetValidationError(ToolsetStoreError):
    """Raised when Toolset data is not publishable."""


class ToolsetStore:
    """Atomic file-backed Toolset draft and immutable version store."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("TOOLSET_STORAGE_DIR", "").strip()
            or os.getenv("XPERT_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.storage_path = self.storage_dir / "toolsets.json"
        self._lock = threading.RLock()
        self._ensure_storage_unlocked()

    def list_toolsets(
        self,
        *,
        status: str | None = None,
        search: str = "",
        limit: int = 100,
    ) -> list[ToolsetDefinition]:
        clean_search = search.strip().lower()
        with self._lock:
            items = self._read_unlocked()
        if status:
            items = [item for item in items if item.status == status]
        if clean_search:
            items = [
                item
                for item in items
                if clean_search
                in " ".join([item.name, item.description, *item.tags]).lower()
            ]
        items.sort(key=lambda item: (-item.updated_at, item.id))
        return [item.model_copy(deep=True) for item in items[: max(1, min(limit, 500))]]

    def create_toolset(
        self,
        *,
        name: str,
        kind: ToolsetKind = "mcp",
        description: str = "",
        tags: list[str] | None = None,
        privacy_policy: str = "",
        disclaimer: str = "",
        connection: dict[str, Any] | MCPConnectionProfile | None = None,
    ) -> ToolsetDefinition:
        now = time.time()
        item = ToolsetDefinition(
            id=f"toolset_{uuid.uuid4().hex}",
            kind=kind,
            name=self._required_text(name, "name", 160),
            description=str(description or "").strip()[:4000],
            tags=self._clean_tags(tags),
            privacy_policy=str(privacy_policy or "").strip()[:4000],
            disclaimer=str(disclaimer or "").strip()[:4000],
            connection=MCPConnectionProfile.model_validate(connection or {}),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            items = self._read_unlocked()
            items.append(item)
            self._write_unlocked(items)
        return item.model_copy(deep=True)

    def get_toolset(self, toolset_id: str) -> ToolsetDefinition:
        with self._lock:
            return self._find_unlocked(self._read_unlocked(), toolset_id).model_copy(
                deep=True
            )

    def update_toolset(
        self,
        toolset_id: str,
        *,
        revision: int,
        patch: dict[str, Any],
    ) -> ToolsetDefinition:
        allowed = {
            "name",
            "description",
            "tags",
            "privacy_policy",
            "disclaimer",
            "connection",
            "status",
            "import_warnings",
            "drift_report",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ToolsetValidationError(
                f"Unsupported Toolset fields: {', '.join(sorted(unknown))}."
            )
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, toolset_id)
            self._check_revision(item, revision)
            if "name" in patch:
                item.name = self._required_text(patch["name"], "name", 160)
            if "description" in patch:
                item.description = str(patch["description"] or "").strip()[:4000]
            if "tags" in patch:
                item.tags = self._clean_tags(patch["tags"])
            if "privacy_policy" in patch:
                item.privacy_policy = str(patch["privacy_policy"] or "").strip()[:4000]
            if "disclaimer" in patch:
                item.disclaimer = str(patch["disclaimer"] or "").strip()[:4000]
            if "connection" in patch:
                item.connection = MCPConnectionProfile.model_validate(
                    patch["connection"] or {}
                )
            if "import_warnings" in patch:
                item.import_warnings = [
                    str(value or "").strip()[:500]
                    for value in list(patch["import_warnings"] or [])[:100]
                    if str(value or "").strip()
                ]
            if "drift_report" in patch:
                if not isinstance(patch["drift_report"], dict):
                    raise ToolsetValidationError("drift_report must be an object.")
                item.drift_report = dict(patch["drift_report"])
            if "status" in patch:
                status = str(patch["status"] or "").strip()
                if status not in {"draft", "published", "archived"}:
                    raise ToolsetValidationError("Unsupported Toolset status.")
                if status == "published" and not item.versions:
                    raise ToolsetValidationError("Publish through the publish endpoint.")
                item.status = status  # type: ignore[assignment]
            item.revision += 1
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def replace_discovered_tools(
        self,
        toolset_id: str,
        *,
        tools: list[dict[str, Any] | ToolDefinition],
        runtime_status: str = "connected",
        enable_new_tools: bool = False,
        import_warnings: list[str] | None = None,
        drift_report: dict[str, Any] | None = None,
        connection: MCPConnectionProfile | None = None,
    ) -> ToolsetDefinition:
        now = time.time()
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, toolset_id)
            current = {tool.original_name: tool for tool in item.tools}
            discovered: list[ToolDefinition] = []
            for index, raw in enumerate(tools):
                candidate = (
                    raw
                    if isinstance(raw, ToolDefinition)
                    else ToolDefinition.model_validate(raw)
                )
                if not TOOL_NAME_PATTERN.fullmatch(candidate.original_name):
                    raise ToolsetValidationError(
                        f"Invalid MCP tool name: {candidate.original_name}"
                    )
                previous = current.get(candidate.original_name)
                candidate.alias = previous.alias if previous else candidate.alias
                candidate.description = (
                    previous.description
                    if previous and previous.description
                    else candidate.description
                )
                candidate.default_arguments = (
                    dict(previous.default_arguments) if previous else {}
                )
                candidate.enabled = (
                    previous.enabled if previous else bool(enable_new_tools)
                )
                candidate.order = previous.order if previous else index
                candidate.read_only = (
                    previous.read_only if previous else candidate.read_only
                )
                candidate.requires_approval = (
                    previous.requires_approval
                    if previous
                    else candidate.requires_approval
                )
                candidate.sensitive = (
                    previous.sensitive if previous else candidate.sensitive
                )
                candidate.terminal = (
                    previous.terminal if previous else candidate.terminal
                )
                candidate.memory_mode = (
                    previous.memory_mode if previous else candidate.memory_mode
                )
                candidate.parallel_safe = (
                    previous.parallel_safe
                    if previous
                    else candidate.parallel_safe
                )
                candidate.public_app_allowed = (
                    previous.public_app_allowed
                    if previous
                    else candidate.public_app_allowed
                )
                candidate.schema_hash = self.schema_hash(candidate.input_schema)
                candidate.discovered_at = now
                discovered.append(candidate)
            item.tools = discovered
            if connection is not None:
                item.connection = connection.model_copy(deep=True)
            item.runtime_status = runtime_status
            item.runtime_error = ""
            item.import_warnings = [
                str(value or "").strip()[:500]
                for value in list(import_warnings or [])[:100]
                if str(value or "").strip()
            ]
            item.drift_report = dict(drift_report or {})
            item.revision += 1
            item.updated_at = now
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def update_tool(
        self,
        toolset_id: str,
        tool_name: str,
        *,
        revision: int,
        patch: dict[str, Any],
    ) -> ToolsetDefinition:
        allowed = {
            "alias",
            "description",
            "default_arguments",
            "enabled",
            "order",
            "read_only",
            "requires_approval",
            "sensitive",
            "terminal",
            "memory_mode",
            "parallel_safe",
            "public_app_allowed",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ToolsetValidationError(
                f"Unsupported tool fields: {', '.join(sorted(unknown))}."
            )
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, toolset_id)
            self._check_revision(item, revision)
            tool = next(
                (candidate for candidate in item.tools if candidate.original_name == tool_name),
                None,
            )
            if tool is None:
                raise ToolsetNotFoundError(f"Tool not found: {tool_name}")
            if "alias" in patch:
                alias = str(patch["alias"] or "").strip()
                if alias and not TOOL_NAME_PATTERN.fullmatch(alias):
                    raise ToolsetValidationError("Tool alias is invalid.")
                tool.alias = alias
            if "description" in patch:
                tool.description = str(patch["description"] or "").strip()[:4000]
            if "default_arguments" in patch:
                if not isinstance(patch["default_arguments"], dict):
                    raise ToolsetValidationError("default_arguments must be an object.")
                tool.default_arguments = dict(patch["default_arguments"])
            if "enabled" in patch:
                tool.enabled = bool(patch["enabled"])
            if "order" in patch:
                tool.order = max(0, min(int(patch["order"]), 10_000))
            if "read_only" in patch:
                if item.kind != "mcp":
                    raise ToolsetValidationError(
                        "API and builtin Provider tool mutability is fixed by its schema."
                    )
                tool.read_only = bool(patch["read_only"])
                if not tool.read_only:
                    tool.parallel_safe = False
                    tool.public_app_allowed = False
            if "requires_approval" in patch:
                if not tool.read_only and not bool(patch["requires_approval"]):
                    raise ToolsetValidationError(
                        "Mutating API operations must require approval."
                    )
                tool.requires_approval = bool(patch["requires_approval"])
            if "sensitive" in patch:
                tool.sensitive = bool(patch["sensitive"])
                if tool.sensitive:
                    tool.requires_approval = True
            if "terminal" in patch:
                tool.terminal = bool(patch["terminal"])
            if "memory_mode" in patch:
                memory_mode = str(patch["memory_mode"] or "off").strip()
                if memory_mode not in {"off", "run", "conversation"}:
                    raise ToolsetValidationError(
                        "memory_mode must be off, run, or conversation."
                    )
                tool.memory_mode = memory_mode  # type: ignore[assignment]
            if "parallel_safe" in patch:
                requested = bool(patch["parallel_safe"])
                if requested and (
                    not tool.read_only or tool.sensitive or tool.terminal
                ):
                    raise ToolsetValidationError(
                        "Only non-sensitive, non-terminal read-only tools may be parallel safe."
                    )
                tool.parallel_safe = requested
            if "public_app_allowed" in patch:
                requested = bool(patch["public_app_allowed"])
                if requested and (
                    not tool.read_only
                    or tool.sensitive
                    or tool.memory_mode == "conversation"
                ):
                    raise ToolsetValidationError(
                        "Public App tools must be read-only, non-sensitive, and not use conversation memory."
                    )
                tool.public_app_allowed = requested
            if tool.sensitive and not tool.requires_approval:
                raise ToolsetValidationError(
                    "Sensitive tools must require approval."
                )
            self._ensure_unique_runtime_names(item.tools)
            item.revision += 1
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def set_runtime_state(
        self,
        toolset_id: str,
        *,
        status: str,
        session_id: str | None,
        error: str = "",
    ) -> ToolsetDefinition:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, toolset_id)
            item.runtime_status = str(status or "disconnected")[:80]
            item.runtime_session_id = session_id
            item.runtime_error = str(error or "").strip()[:500]
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def publish(
        self,
        toolset_id: str,
        *,
        revision: int,
        release_notes: str = "",
        connection_override: MCPConnectionProfile | None = None,
    ) -> ToolsetVersion:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, toolset_id)
            self._check_revision(item, revision)
            expected_runtime_status = (
                "connected" if item.kind == "mcp" else "ready"
            )
            if item.runtime_status != expected_runtime_status:
                raise ToolsetValidationError(
                    "MCP Toolset must be connected before publish."
                    if item.kind == "mcp"
                    else (
                        "Builtin Provider must be configured before publish."
                        if item.kind == "builtin"
                        else "API Toolset must be imported successfully before publish."
                    )
                )
            enabled = sorted(
                (tool.model_copy(deep=True) for tool in item.tools if tool.enabled),
                key=lambda tool: (tool.order, tool.original_name),
            )
            if not enabled:
                raise ToolsetValidationError("Enable at least one discovered tool.")
            self._ensure_unique_runtime_names(enabled)
            version_connection = (
                connection_override.model_copy(deep=True)
                if connection_override is not None
                else item.connection.model_copy(deep=True)
            )
            next_version = max((version.version for version in item.versions), default=0) + 1
            canonical = json.dumps(
                {
                    "connection": version_connection.model_dump(mode="json"),
                    "tools": [tool.model_dump(mode="json") for tool in enabled],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            version = ToolsetVersion(
                version=next_version,
                draft_revision=item.revision,
                kind=item.kind,
                connection=version_connection,
                tools=enabled,
                schema_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                release_notes=str(release_notes or "").strip()[:2000],
                published_at=time.time(),
            )
            item.versions.append(version)
            item.published_version = version.version
            item.status = "published"
            item.updated_at = version.published_at
            self._write_unlocked(items)
            return version.model_copy(deep=True)

    def get_version(
        self, toolset_id: str, version: int | None = None
    ) -> ToolsetVersion:
        item = self.get_toolset(toolset_id)
        target = version if version is not None else item.published_version
        if target is None:
            raise ToolsetNotFoundError("Toolset has not been published.")
        for snapshot in item.versions:
            if snapshot.version == target:
                return snapshot.model_copy(deep=True)
        raise ToolsetNotFoundError(f"Toolset version not found: {target}")

    def list_versions(self, toolset_id: str) -> list[ToolsetVersion]:
        item = self.get_toolset(toolset_id)
        return [version.model_copy(deep=True) for version in reversed(item.versions)]

    @staticmethod
    def schema_hash(schema: dict[str, Any]) -> str:
        canonical = json.dumps(
            schema or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def runtime_name(tool: ToolDefinition, prefix: str = "") -> str:
        name = tool.alias.strip() or tool.original_name
        return f"{prefix}_{name}" if prefix else name

    @classmethod
    def _ensure_unique_runtime_names(cls, tools: list[ToolDefinition]) -> None:
        seen: set[str] = set()
        for tool in tools:
            if not tool.enabled:
                continue
            name = cls.runtime_name(tool)
            if name in seen:
                raise ToolsetValidationError(f"Duplicate enabled tool name: {name}")
            seen.add(name)

    def _ensure_storage_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write_unlocked([])

    def _read_unlocked(self) -> list[ToolsetDefinition]:
        self._ensure_storage_unlocked()
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            raw = payload.get("toolsets", []) if isinstance(payload, dict) else []
            return [ToolsetDefinition.model_validate(item) for item in raw]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ToolsetStoreError("Toolset storage is unreadable.") from exc

    def _write_unlocked(self, items: list[ToolsetDefinition]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "modelmirror-toolsets-v1",
            "toolsets": [item.model_dump(mode="json") for item in items],
        }
        temporary = self.storage_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.storage_path)

    @staticmethod
    def _find_unlocked(
        items: list[ToolsetDefinition], toolset_id: str
    ) -> ToolsetDefinition:
        for item in items:
            if item.id == toolset_id:
                return item
        raise ToolsetNotFoundError(f"Toolset not found: {toolset_id}")

    @staticmethod
    def _check_revision(item: ToolsetDefinition, revision: int) -> None:
        if item.revision != int(revision):
            raise ToolsetConflictError(
                f"Toolset revision conflict: expected {item.revision}."
            )

    @staticmethod
    def _required_text(value: Any, field_name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ToolsetValidationError(f"{field_name} is required.")
        if len(text) > limit:
            raise ToolsetValidationError(f"{field_name} exceeds {limit} characters.")
        return text

    @staticmethod
    def _clean_tags(values: list[str] | None) -> list[str]:
        tags: list[str] = []
        for value in values or []:
            clean = str(value or "").strip()[:80]
            if clean and clean not in tags:
                tags.append(clean)
            if len(tags) >= 30:
                break
        return tags
