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

from .models import XpertDefinition, XpertDraft, XpertStatus, XpertSummary, XpertVersion

try:
    from server.workflow_native.schemas import NativeWorkflowDefinition
except ModuleNotFoundError:
    from workflow_native.schemas import NativeWorkflowDefinition

try:
    from server.prompts.models import ResolvedPromptProfile
except ModuleNotFoundError:
    from prompts.models import ResolvedPromptProfile


SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class XpertStoreError(Exception):
    """Base error raised by the Xpert store."""


class XpertNotFoundError(XpertStoreError):
    """Raised when an Xpert or published version does not exist."""


class XpertValidationError(XpertStoreError):
    """Raised when Xpert metadata is invalid."""


class XpertConflictError(XpertStoreError):
    """Raised when a draft changes between validation and publish."""


def default_xpert_workflow(xpert_id: str, name: str) -> NativeWorkflowDefinition:
    return NativeWorkflowDefinition.model_validate(
        {
            "id": f"xpert-{xpert_id}-draft",
            "title": name,
            "version": "xpert-draft-v1",
            "source": "classic",
            "nodes": [
                {
                    "id": "input-1",
                    "type": "input",
                    "position": {"x": 40, "y": 140},
                    "data": {
                        "kind": "input",
                        "title": "对话输入",
                        "description": "接收用户消息。",
                        "variableName": "user_input",
                    },
                },
                {
                    "id": "workflow-agent-1",
                    "type": "workflow_agent",
                    "position": {"x": 380, "y": 140},
                    "data": {
                        "kind": "workflow_agent",
                        "title": "主智能体",
                        "description": "执行已发布 Xpert 的主要推理步骤。",
                        "agentName": "primary-agent",
                        "modelId": "deepseek/deepseek-v4-flash-0731",
                        "rolePrompt": "你是一个可靠的任务智能体。请结合对话上下文，直接完成用户请求。",
                        "taskInput": "历史对话：\n{{conversation_history}}\n\n当前请求：\n{{user_input}}",
                        "toolMode": "none",
                        "toolNames": "",
                        "maxIterations": "5",
                        "promptSuffix": "",
                        "outputVariable": "agent_output",
                        "disableOutput": "false",
                        "enableFileUnderstanding": "true",
                        "memoryReadEnabled": "true",
                        "memoryReadScope": "both",
                        "memoryWriteEnabled": "false",
                        "memoryWriteTarget": "xpert",
                        "retryOnFailure": "false",
                        "fallbackModelId": "",
                        "exceptionHandling": "none",
                    },
                },
                {
                    "id": "output-1",
                    "type": "output",
                    "position": {"x": 760, "y": 140},
                    "data": {
                        "kind": "output",
                        "title": "最终回答",
                        "description": "返回智能体生成的结果。",
                        "outputVariable": "agent_output",
                    },
                },
            ],
            "edges": [
                {"id": "edge-input-agent", "source": "input-1", "target": "workflow-agent-1"},
                {"id": "edge-agent-output", "source": "workflow-agent-1", "target": "output-1"},
            ],
        }
    )


class XpertStore:
    """Filesystem-backed store for Xpert drafts and immutable releases."""

    def __init__(self, storage_dir: Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("XPERT_STORAGE_DIR")
            or package_dir / "storage"
        )
        self.storage_path = self.storage_dir / "xperts.json"
        self._lock = threading.RLock()

    def list_xperts(
        self,
        *,
        status: XpertStatus | None = None,
        search: str = "",
        limit: int = 50,
    ) -> list[XpertSummary]:
        with self._lock:
            items = self._read_unlocked()
        if status is not None:
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
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return [self.to_summary(item) for item in items[: max(1, min(limit, 200))]]

    def get_xpert(self, xpert_id: str) -> XpertDefinition:
        with self._lock:
            item = self._find_unlocked(self._read_unlocked(), xpert_id)
            return item.model_copy(deep=True)

    def resolve_xpert(self, reference: str) -> XpertDefinition:
        """Resolve an Xpert by stable id first, then by its unique slug."""

        clean_reference = reference.strip()
        with self._lock:
            items = self._read_unlocked()
            for item in items:
                if item.id == clean_reference or item.slug == clean_reference:
                    return item.model_copy(deep=True)
        raise XpertNotFoundError(f"Xpert not found: {clean_reference}")

    def create_xpert(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        starters: list[str] | None = None,
    ) -> XpertDefinition:
        clean_name = name.strip()
        if not clean_name:
            raise XpertValidationError("Xpert name is required.")
        xpert_id = str(uuid.uuid4())
        clean_slug = self._normalize_slug(slug or clean_name, xpert_id)
        now = time.time()
        with self._lock:
            items = self._read_unlocked()
            if any(item.slug == clean_slug for item in items):
                raise XpertValidationError(f"Xpert slug already exists: {clean_slug}")
            item = XpertDefinition(
                id=xpert_id,
                slug=clean_slug,
                name=clean_name[:120],
                description=description.strip()[:2000],
                tags=self._clean_list(tags),
                starters=self._clean_list(starters, max_items=8, max_length=500),
                draft=XpertDraft(workflow=default_xpert_workflow(xpert_id, clean_name)),
                created_at=now,
                updated_at=now,
            )
            items.append(item)
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def update_xpert(self, xpert_id: str, patch: dict[str, Any]) -> XpertDefinition:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, xpert_id)
            if "name" in patch and patch["name"] is not None:
                name = str(patch["name"]).strip()
                if not name:
                    raise XpertValidationError("Xpert name is required.")
                item.name = name[:120]
            if "description" in patch and patch["description"] is not None:
                item.description = str(patch["description"]).strip()[:2000]
            if "tags" in patch and patch["tags"] is not None:
                item.tags = self._clean_list(patch["tags"])
            if "starters" in patch and patch["starters"] is not None:
                item.starters = self._clean_list(
                    patch["starters"], max_items=8, max_length=500
                )
            if "status" in patch and patch["status"] is not None:
                next_status = patch["status"]
                if next_status == "published" and not item.versions:
                    raise XpertValidationError(
                        "Publish an Xpert through the publish endpoint."
                    )
                item.status = next_status
            if "draft" in patch and patch["draft"] is not None:
                item.draft = XpertDraft.model_validate(patch["draft"])
                item.draft_revision += 1
            item.updated_at = time.time()
            self._write_unlocked(items)
            return item.model_copy(deep=True)

    def publish_xpert(
        self,
        xpert_id: str,
        *,
        release_notes: str = "",
        expected_revision: int | None = None,
        workflow_override: NativeWorkflowDefinition | None = None,
        prompt_profiles_override: list[ResolvedPromptProfile] | None = None,
    ) -> XpertVersion:
        with self._lock:
            items = self._read_unlocked()
            item = self._find_unlocked(items, xpert_id)
            if (
                expected_revision is not None
                and item.draft_revision != expected_revision
            ):
                raise XpertConflictError(
                    "Xpert draft changed during publish preflight. Validate and retry."
                )
            next_version = max((version.version for version in item.versions), default=0) + 1
            workflow = (
                workflow_override.model_copy(deep=True)
                if workflow_override is not None
                else item.draft.workflow.model_copy(deep=True)
            )
            workflow.id = f"xpert-{item.id}-v{next_version}"
            workflow.title = item.name
            workflow.version = f"xpert-v{next_version}"
            published_features = item.draft.features.model_copy(deep=True)
            published_prompt_profiles = [
                profile.model_copy(deep=True)
                for profile in (prompt_profiles_override or [])
            ]
            if (
                item.starters
                and not published_features.opening.questions
                and not published_features.opening.message.strip()
            ):
                published_features.opening.enabled = True
                published_features.opening.questions = list(item.starters)
            canonical = json.dumps(
                {
                    "workflow": workflow.model_dump(mode="json"),
                    "input_variable": item.draft.input_variable,
                    "history_variable": item.draft.history_variable,
                    "output_variable": item.draft.output_variable,
                    "agent_config": item.draft.agent_config.model_dump(mode="json"),
                    "features": published_features.model_dump(mode="json"),
                    "prompt_profiles": [
                        profile.model_dump(mode="json")
                        for profile in published_prompt_profiles
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            version = XpertVersion(
                version=next_version,
                draft_revision=item.draft_revision,
                workflow=workflow,
                input_variable=item.draft.input_variable,
                history_variable=item.draft.history_variable,
                output_variable=item.draft.output_variable,
                agent_config=item.draft.agent_config.model_copy(deep=True),
                features=published_features,
                prompt_profiles=published_prompt_profiles,
                release_notes=release_notes.strip()[:2000],
                checksum=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                published_at=time.time(),
            )
            item.versions.append(version)
            item.published_version=next_version
            item.status = "published"
            item.updated_at = version.published_at
            self._write_unlocked(items)
            return version.model_copy(deep=True)

    def list_versions(self, xpert_id: str) -> list[XpertVersion]:
        item = self.get_xpert(xpert_id)
        return [version.model_copy(deep=True) for version in reversed(item.versions)]

    def get_version(self, xpert_id: str, version: int | None = None) -> XpertVersion:
        item = self.get_xpert(xpert_id)
        target = version if version is not None else item.published_version
        if target is None:
            raise XpertNotFoundError("Xpert has not been published.")
        for snapshot in item.versions:
            if snapshot.version == target:
                return snapshot.model_copy(deep=True)
        raise XpertNotFoundError(f"Xpert version not found: {target}")

    @staticmethod
    def to_summary(item: XpertDefinition) -> XpertSummary:
        return XpertSummary(
            id=item.id,
            slug=item.slug,
            name=item.name,
            description=item.description,
            tags=list(item.tags),
            starters=list(item.starters),
            status=item.status,
            draft_revision=item.draft_revision,
            published_version=item.published_version,
            version_count=len(item.versions),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _ensure_storage_unlocked(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self._write_unlocked([])

    def _read_unlocked(self) -> list[XpertDefinition]:
        self._ensure_storage_unlocked()
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            raw_items = payload.get("xperts", []) if isinstance(payload, dict) else []
            return [XpertDefinition.model_validate(item) for item in raw_items]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise XpertStoreError("Xpert storage is unreadable.") from exc

    def _write_unlocked(self, items: list[XpertDefinition]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "xperts": [item.model_dump(mode="json") for item in items],
        }
        temp_path = self.storage_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.storage_path)

    @staticmethod
    def _find_unlocked(items: list[XpertDefinition], xpert_id: str) -> XpertDefinition:
        for item in items:
            if item.id == xpert_id:
                return item
        raise XpertNotFoundError(f"Xpert not found: {xpert_id}")

    @staticmethod
    def _normalize_slug(value: str, xpert_id: str) -> str:
        slug = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-_")
        slug = slug[:64] or f"xpert-{xpert_id[:8]}"
        if not SLUG_PATTERN.fullmatch(slug):
            raise XpertValidationError("Xpert slug must use lowercase letters, numbers, '-' or '_'.")
        return slug

    @staticmethod
    def _clean_list(
        values: list[str] | None,
        *,
        max_items: int = 20,
        max_length: int = 80,
    ) -> list[str]:
        result: list[str] = []
        for value in values or []:
            clean = str(value).strip()[:max_length]
            if clean and clean not in result:
                result.append(clean)
            if len(result) >= max_items:
                break
        return result
