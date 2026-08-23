from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .creator_quality import build_session_requirements
from .creator_resource_plan import SkillResourcePlan, SkillResourcePlanStore
from .creator_service import SkillCreatorService
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)
from .draft_store import WorkspaceSkillDraft, WorkspaceSkillDraftStore
from .hook_contract import (
    HOOK_MANIFEST_PATH,
    SkillHookContractError,
    parse_hook_manifest,
    skill_plugin_hook_v2_enabled,
)


RESOURCE_AUTHORING_VERSION = "resource-authoring-v2"
_RESOURCE_ROOTS = ("scripts/", "references/", "assets/")


@dataclass(frozen=True, slots=True)
class ResourcePlanningRequest:
    session: dict[str, Any]
    target_draft: dict[str, Any] | None
    current_plan: dict[str, Any] | None
    allowed_source_ids: list[str]


class ResourcePlannerExecutor(Protocol):
    def available(self) -> bool: ...

    async def plan(self, request: ResourcePlanningRequest) -> dict[str, Any]: ...


class ResourcePlanConfirmationGate(Protocol):
    def __call__(
        self,
        session: SkillCreatorSession,
        plan: SkillResourcePlan,
        draft: WorkspaceSkillDraft | None,
    ) -> Any: ...


class SkillCreatorResourcePlanningService:
    VERSION = RESOURCE_AUTHORING_VERSION

    def __init__(
        self,
        creator_service: SkillCreatorService,
        plan_store: SkillResourcePlanStore,
        *,
        planner: ResourcePlannerExecutor | None = None,
        confirmation_gate: ResourcePlanConfirmationGate | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.creator_service = creator_service
        self.plan_store = plan_store
        self.planner = planner
        self.confirmation_gate = confirmation_gate
        self.enabled = (
            os.getenv("SKILL_CREATOR_RESOURCE_AUTHORING_ENABLED", "true")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
            if enabled is None
            else bool(enabled)
        )
        self._locks_guard = threading.RLock()
        self._locks: dict[str, asyncio.Lock] = {}

    def status(self) -> dict[str, Any]:
        try:
            planner_available = bool(self.planner and self.planner.available())
        except Exception:
            planner_available = False
        return {
            "resource_authoring_version": self.VERSION,
            "resource_authoring_enabled": self.enabled,
            "resource_planner_available": self.enabled and planner_available,
        }

    def require_enabled(self) -> None:
        self.creator_service.require_enabled()
        if not self.enabled:
            raise SkillCreatorValidationError(
                "Skill Creator resource authoring is disabled.",
                code="skill_creator_resource_authoring_disabled",
            )

    def current_projection(self, session_id: str) -> dict[str, Any] | None:
        self.creator_service.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        current = self.plan_store.current_for_session(session_id)
        return self._projection(current, session=session, draft=draft)

    async def generate(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_plan_revision: int | None,
        expected_plan_digest: str | None,
    ) -> SkillResourcePlan:
        self.require_enabled()
        session, _ = self.creator_service.get_session(session_id)
        self._require_session_revision(session, expected_session_revision)
        async with self._lock(session.session_id):
            return await self._generate_locked(
                session.session_id,
                expected_session_revision=expected_session_revision,
                expected_plan_revision=expected_plan_revision,
                expected_plan_digest=expected_plan_digest,
            )

    async def _generate_locked(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_plan_revision: int | None,
        expected_plan_digest: str | None,
    ) -> SkillResourcePlan:
        session, draft = self.creator_service.get_session(session_id)
        self._require_session_revision(session, expected_session_revision)
        if session.authoring_flow == "legacy":
            self.creator_service._cancel_pending_proposal(session)
            session = self.creator_service.session_store.activate_resource_authoring(
                session.session_id,
                expected_session_revision=session.session_revision,
            )
        self.creator_service._require_ready_for_generation(session)
        current = self.plan_store.current_for_session(session_id)
        self._require_expected_plan(
            current,
            expected_revision=expected_plan_revision,
            expected_digest=expected_plan_digest,
        )
        if current is not None and current.state == "needs_input":
            raise SkillCreatorConflictError(
                "Answer the current clarification questions before regenerating the plan."
            )
        if current is not None and current.state == "confirmed" and not self._projection(
            current, session=session, draft=draft
        )["stale"]:
            raise SkillCreatorConflictError(
                "The resource plan is already confirmed and cannot be regenerated."
            )
        planner = self.planner
        try:
            available = bool(planner and planner.available())
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The Skill Creator resource planner status is unavailable.",
                code="skill_creator_resource_planner_failed",
            ) from exc
        if not available or planner is None:
            raise SkillCreatorValidationError(
                "The Skill Creator model gateway is not configured.",
                code="model_gateway_unconfigured",
            )
        source_ids = self._source_ids(session)
        try:
            payload = await planner.plan(
                ResourcePlanningRequest(
                    session=asdict(session),
                    target_draft=self._draft_context(draft),
                    current_plan=(
                        self._planner_plan_context(current) if current else None
                    ),
                    allowed_source_ids=sorted(source_ids),
                )
            )
        except (SkillCreatorConflictError, SkillCreatorValidationError):
            raise
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The dedicated Skill Creator agent could not create a resource plan.",
                code="skill_creator_resource_planner_failed",
            ) from exc
        if payload.get("hooks") and not skill_plugin_hook_v2_enabled():
            if self._hook_inventory(draft):
                raise SkillCreatorValidationError(
                    "Skill Hook V2 authoring is disabled.",
                    code="skill_hook_v2_disabled",
                )
            payload = {**payload, "hooks": []}
        self._validate_actions(payload, draft=draft)
        return self.plan_store.save_generated(
            session_id=session.session_id,
            session_revision=session.session_revision,
            draft_id=(draft.draft_id if draft else None),
            draft_revision=(draft.revision if draft else None),
            draft_digest=(draft.content_digest if draft else None),
            payload=payload,
            allowed_source_ids=source_ids,
            expected_plan_revision=expected_plan_revision,
            expected_plan_digest=expected_plan_digest,
        )

    def save_answers(
        self,
        session_id: str,
        *,
        plan_id: str,
        expected_session_revision: int,
        expected_plan_revision: int,
        expected_plan_digest: str,
        answers: dict[str, str],
    ) -> SkillResourcePlan:
        self.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        self._require_session_revision(session, expected_session_revision)
        current = self.plan_store.require(plan_id)
        self._require_plan_scope(current, session=session, draft=draft)
        return self.plan_store.save_answers(
            plan_id,
            expected_revision=expected_plan_revision,
            expected_digest=expected_plan_digest,
            answers=answers,
        )

    def patch(
        self,
        session_id: str,
        *,
        plan_id: str,
        expected_session_revision: int,
        expected_plan_revision: int,
        expected_plan_digest: str,
        changes: dict[str, Any],
    ) -> SkillResourcePlan:
        self.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        self._require_session_revision(session, expected_session_revision)
        current = self.plan_store.require(plan_id)
        self._require_plan_scope(current, session=session, draft=draft)
        if (
            (current.hooks or changes.get("hooks"))
            and not skill_plugin_hook_v2_enabled()
        ):
            raise SkillCreatorValidationError(
                "Skill Hook V2 authoring is disabled.",
                code="skill_hook_v2_disabled",
            )
        self._validate_actions({
            "resources": changes.get(
                "resources", [asdict(item) for item in current.resources]
            ),
            "hooks": changes.get("hooks", [asdict(item) for item in current.hooks]),
        }, draft=draft)
        return self.plan_store.patch(
            plan_id,
            expected_revision=expected_plan_revision,
            expected_digest=expected_plan_digest,
            changes=changes,
            allowed_source_ids=self._source_ids(session),
        )

    def confirm(
        self,
        session_id: str,
        *,
        plan_id: str,
        expected_session_revision: int,
        expected_plan_revision: int,
        expected_plan_digest: str,
    ) -> SkillResourcePlan:
        self.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        self._require_session_revision(session, expected_session_revision)
        current = self.plan_store.require(plan_id)
        self._require_plan_scope(current, session=session, draft=draft)
        if current.hooks and not skill_plugin_hook_v2_enabled():
            raise SkillCreatorValidationError(
                "Skill Hook V2 authoring is disabled.",
                code="skill_hook_v2_disabled",
            )
        if self.confirmation_gate is not None:
            self.confirmation_gate(session, current, draft)
        return self.plan_store.confirm(
            plan_id,
            expected_revision=expected_plan_revision,
            expected_digest=expected_plan_digest,
            session_revision=session.session_revision,
            draft_revision=(draft.revision if draft else None),
            draft_digest=(draft.content_digest if draft else None),
        )

    def serialize_projection(
        self,
        plan: SkillResourcePlan | None,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft | None,
    ) -> dict[str, Any] | None:
        return self._projection(plan, session=session, draft=draft)

    @staticmethod
    def _projection(
        plan: SkillResourcePlan | None,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft | None,
    ) -> dict[str, Any] | None:
        if plan is None:
            return None
        data = SkillResourcePlanStore.serialize(plan)
        data["stale"] = bool(
            plan.session_revision != session.session_revision
            or plan.draft_id != (draft.draft_id if draft else None)
            or plan.draft_revision != (draft.revision if draft else None)
            or plan.draft_digest != (draft.content_digest if draft else None)
        )
        return data

    @staticmethod
    def _source_ids(session: SkillCreatorSession) -> set[str]:
        result = {
            item.requirement_id
            for item in build_session_requirements(
                intent=session.intent,
                positive_examples=session.positive_examples,
                near_miss_examples=session.near_miss_examples,
                expected_output=session.expected_output,
                success_criteria=session.success_criteria,
            )
        }
        for evidence in session.selected_evidence:
            candidate_id = str(evidence.get("candidate_id") or "").strip()
            if candidate_id:
                result.add(f"evidence:{candidate_id}"[:200])
        return result

    @staticmethod
    def _draft_context(draft: WorkspaceSkillDraft | None) -> dict[str, Any] | None:
        if draft is None:
            return None
        files = [
            {
                "path": path,
                "size_bytes": len(content.encode("utf-8")),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            for path, content in sorted(draft.files.items())
            if path.startswith(_RESOURCE_ROOTS)
        ]
        return {
            "draft_id": draft.draft_id,
            "revision": draft.revision,
            "content_revision": draft.content_revision,
            "content_digest": draft.content_digest,
            "name": draft.name,
            "slug": draft.slug,
            "description": draft.description,
            "skill_markdown": draft.skill_markdown[:20_000],
            "resource_inventory": files,
            "hook_inventory": SkillCreatorResourcePlanningService._hook_inventory(draft),
        }

    @staticmethod
    def _planner_plan_context(plan: SkillResourcePlan) -> dict[str, Any]:
        data = SkillResourcePlanStore.serialize(plan)
        path_by_id = {item.resource_id: item.path for item in plan.resources}
        for resource in data["resources"]:
            resource["depends_on"] = [
                path_by_id[dependency_id]
                for dependency_id in resource.get("depends_on", [])
                if dependency_id in path_by_id
            ]
        return data

    @staticmethod
    def _validate_actions(payload: dict[str, Any], *, draft: WorkspaceSkillDraft | None) -> None:
        resources = payload.get("resources") or []
        if not isinstance(resources, list):
            raise SkillCreatorValidationError("Resource planner returned an invalid resource list.")
        existing = {
            path
            for path in (draft.files if draft else {})
            if path.startswith(_RESOURCE_ROOTS)
        }
        planned_existing: set[str] = set()
        for raw in resources:
            if not isinstance(raw, dict):
                continue
            path = str(raw.get("path") or "").strip()
            action = str(raw.get("action") or "create").strip()
            if action in {"keep", "update", "delete"}:
                if path not in existing:
                    raise SkillCreatorValidationError(
                        "A keep, update, or delete action must target an existing resource.",
                        code="skill_creator_resource_action_invalid",
                    )
                planned_existing.add(path)
            elif action == "create" and path in existing:
                raise SkillCreatorValidationError(
                    "An existing resource must use keep, update, or delete.",
                    code="skill_creator_resource_action_invalid",
                )
        if planned_existing != existing:
            missing = sorted(existing - planned_existing)
            raise SkillCreatorValidationError(
                "The resource plan must explicitly keep, update, or delete every existing resource: "
                + ", ".join(missing[:10]),
                code="skill_creator_resource_action_incomplete",
            )
        existing_hook_inventory = SkillCreatorResourcePlanningService._hook_inventory(draft)
        existing_hooks = {item["hook_id"]: item for item in existing_hook_inventory}
        existing_hook_ids = set(existing_hooks)
        resource_action_by_path = {
            str(item.get("path") or "").strip(): str(item.get("action") or "create").strip()
            for item in resources
            if isinstance(item, dict)
        }
        resource_path_by_id = {
            str(item.get("resource_id") or "").strip(): str(item.get("path") or "").strip()
            for item in resources
            if isinstance(item, dict) and str(item.get("resource_id") or "").strip()
        }
        planned_existing_hooks: set[str] = set()
        hooks = payload.get("hooks") or []
        if not isinstance(hooks, list):
            raise SkillCreatorValidationError("Resource planner returned an invalid Hook list.")
        for raw in hooks:
            if not isinstance(raw, dict):
                continue
            hook_id = str(raw.get("hook_id") or raw.get("id") or "").strip()
            action = str(raw.get("action") or "create").strip()
            if action in {"keep", "update", "delete"}:
                if hook_id not in existing_hook_ids:
                    raise SkillCreatorValidationError(
                        "A keep, update, or delete Hook action must target an existing Hook.",
                        code="skill_creator_hook_action_invalid",
                    )
                planned_existing_hooks.add(hook_id)
                if action == "keep":
                    existing_hook = existing_hooks[hook_id]
                    script_path = str(raw.get("script_path") or "").strip() or resource_path_by_id.get(
                        str(raw.get("script_resource_id") or "").strip(), ""
                    )
                    current_contract = {
                        "event": str(raw.get("event") or "").strip(),
                        "mode": str(raw.get("mode") or "").strip(),
                        "tool_names": list(raw.get("tool_names") or []),
                        "script_path": script_path,
                        "purpose": str(raw.get("purpose") or "").strip(),
                        "acceptance_checks": list(raw.get("acceptance_checks") or []),
                    }
                    expected_contract = {
                        key: existing_hook[key] for key in current_contract
                    }
                    if current_contract != expected_contract:
                        raise SkillCreatorValidationError(
                            "A changed Hook contract must use the update action.",
                            code="skill_creator_hook_action_invalid",
                        )
            elif action == "create" and hook_id in existing_hook_ids:
                raise SkillCreatorValidationError(
                    "An existing Hook must use keep, update, or delete.",
                    code="skill_creator_hook_action_invalid",
                )
            elif action == "create":
                script_path = str(raw.get("script_path") or "").strip() or resource_path_by_id.get(
                    str(raw.get("script_resource_id") or "").strip(), ""
                )
                if resource_action_by_path.get(script_path) == "keep":
                    raise SkillCreatorValidationError(
                        "A new Hook requires its bound existing script to use the update action.",
                        code="skill_creator_hook_script_invalid",
                    )
        if planned_existing_hooks != existing_hook_ids:
            missing = sorted(existing_hook_ids - planned_existing_hooks)
            raise SkillCreatorValidationError(
                "The resource plan must explicitly keep, update, or delete every existing Hook: "
                + ", ".join(missing[:10]),
                code="skill_creator_hook_action_incomplete",
            )

    @staticmethod
    def _hook_inventory(draft: WorkspaceSkillDraft | None) -> list[dict[str, Any]]:
        if draft is None:
            return []
        content = draft.files.get(HOOK_MANIFEST_PATH)
        if content is None:
            return []
        try:
            manifest = parse_hook_manifest(
                content,
                available_paths=draft.files.keys(),
            )
        except SkillHookContractError as exc:
            raise SkillCreatorValidationError(
                "The current draft Hook manifest is invalid and cannot be evolved safely.",
                code=exc.code,
            ) from exc
        return [
            {
                "hook_id": item.hook_id,
                "event": item.event,
                "mode": item.mode,
                "tool_names": list(item.tool_names),
                "script_path": item.script_path,
                "purpose": item.purpose,
                "acceptance_checks": list(item.acceptance_checks),
            }
            for item in manifest.hooks
        ]

    @staticmethod
    def _require_session_revision(
        session: SkillCreatorSession, expected_session_revision: int
    ) -> None:
        if session.session_revision != int(expected_session_revision):
            raise SkillCreatorConflictError(
                "Creator session changed. Reload it before continuing."
            )

    @staticmethod
    def _require_expected_plan(
        current: SkillResourcePlan | None,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        if current is None:
            if expected_revision is not None or expected_digest is not None:
                raise SkillCreatorConflictError("Resource plan changed. Reload it first.")
            return
        if (
            expected_revision is None
            or expected_digest is None
            or current.revision != int(expected_revision)
            or current.digest != str(expected_digest).lower()
        ):
            raise SkillCreatorConflictError("Resource plan changed. Reload it first.")

    @staticmethod
    def _require_plan_scope(
        plan: SkillResourcePlan,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft | None,
    ) -> None:
        if (
            plan.session_id != session.session_id
            or plan.session_revision != session.session_revision
            or plan.draft_id != (draft.draft_id if draft else None)
            or plan.draft_revision != (draft.revision if draft else None)
            or plan.draft_digest != (draft.content_digest if draft else None)
        ):
            raise SkillCreatorConflictError(
                "Resource plan no longer matches the current Creator session and draft."
            )

    def _lock(self, session_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock


__all__ = [
    "RESOURCE_AUTHORING_VERSION",
    "ResourcePlannerExecutor",
    "ResourcePlanningRequest",
    "SkillCreatorResourcePlanningService",
]
