from __future__ import annotations

import asyncio
import hashlib
import os
import threading
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Protocol

from .creator_evaluation import SkillEvaluationRun, SkillEvaluationStore
from .creator_evaluation_suite import SkillEvaluationSuite, SkillEvaluationSuiteStore
from .creator_evolution import SkillEvolutionPlan, SkillEvolutionPlanStore
from .creator_resource_plan import SkillResourcePlan, SkillResourcePlanStore
from .creator_resource_build import SkillResourceBuildStore
from .creator_resource_service import SkillCreatorResourcePlanningService
from .creator_service import SkillCreatorService
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorNotFoundError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)
from .draft_store import WorkspaceSkillDraft
from .hook_contract import HOOK_MANIFEST_PATH
from .package_validation import compute_package_digest


EVOLUTION_SERVICE_VERSION = "skill-creator-resource-evolution-v1"


@dataclass(frozen=True, slots=True)
class EvolutionGenerationRequest:
    session: dict[str, Any]
    draft: dict[str, Any]
    evaluation: dict[str, Any]
    review: dict[str, Any]
    suite: dict[str, Any]
    resource_plan: dict[str, Any]
    current_plan: dict[str, Any] | None
    allowed_case_ids: tuple[str, ...]
    allowed_item_ids: tuple[str, ...]
    allowed_requirement_ids: tuple[str, ...]
    allowed_resource_ids: tuple[str, ...]
    allowed_source_ids: tuple[str, ...]
    allowed_step_ids: tuple[str, ...]
    repair: dict[str, Any] | None = None


class EvolutionPlanner(Protocol):
    def available(self) -> bool: ...

    async def generate(self, request: EvolutionGenerationRequest) -> dict[str, Any]: ...


class SkillCreatorEvolutionService:
    """Turn one frozen revise review into a user-confirmed resource revision."""

    VERSION = EVOLUTION_SERVICE_VERSION

    def __init__(
        self,
        creator_service: SkillCreatorService,
        evolution_store: SkillEvolutionPlanStore,
        evaluation_store: SkillEvaluationStore,
        suite_store: SkillEvaluationSuiteStore,
        resource_planning_service: SkillCreatorResourcePlanningService,
        *,
        resource_build_store: SkillResourceBuildStore | None = None,
        planner: EvolutionPlanner | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.creator_service = creator_service
        self.evolution_store = evolution_store
        self.evaluation_store = evaluation_store
        self.suite_store = suite_store
        self.resource_planning_service = resource_planning_service
        self.resource_plan_store = resource_planning_service.plan_store
        self.resource_build_store = resource_build_store
        self.planner = planner
        self.enabled = (
            os.getenv("SKILL_CREATOR_EVOLUTION_V2_ENABLED", "true").strip().lower()
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
        store_status = self.evolution_store.status()
        return {
            "evolution_version": self.VERSION,
            "evolution_enabled": self.enabled,
            "evolution_planner_available": self.enabled and planner_available,
            "evolution_store_available": bool(store_status["available"]),
        }

    def require_enabled(self) -> None:
        self.creator_service.require_enabled()
        self.resource_planning_service.require_enabled()
        if not self.enabled:
            raise SkillCreatorValidationError(
                "Skill Creator Evolution V2 is disabled.",
                code="skill_creator_evolution_v2_disabled",
            )

    def current_projection(self, session_id: str) -> dict[str, Any] | None:
        self.creator_service.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        current = self.evolution_store.current_for_session(session_id)
        if current is None:
            return None
        stale = draft is None or self._is_stale(current, session=session, draft=draft)
        if not stale and draft is not None:
            try:
                self._facts_for_plan(current, session=session, draft=draft)
            except (SkillCreatorConflictError, SkillCreatorNotFoundError, SkillCreatorValidationError):
                stale = True
        if stale and current.state != "stale":
            try:
                current = self.evolution_store.mark_stale(
                    current.plan_id,
                    expected_revision=current.revision,
                    expected_digest=current.digest,
                )
            except SkillCreatorConflictError:
                latest = self.evolution_store.current_for_session(session_id)
                if latest is not None:
                    current = latest
                    stale = draft is None or self._is_stale(
                        current,
                        session=session,
                        draft=draft,
                    )
        result = self.evolution_store.serialize(current)
        result["stale"] = stale
        result["resource_plan"] = self._materialized_resource_projection(current)
        return result

    async def generate(
        self,
        session_id: str,
        *,
        evaluation_run_id: str,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_review_revision: int,
        expected_run_revision: int,
        expected_resource_plan_revision: int,
        expected_resource_plan_digest: str,
        expected_evolution_revision: int | None,
        expected_evolution_digest: str | None,
    ) -> SkillEvolutionPlan:
        self.require_enabled()
        # Validate existence before allocating a per-session lock. The Session
        # Store already bounds real sessions, while arbitrary missing IDs must
        # not grow the in-memory lock table.
        self.creator_service.get_session(session_id)
        async with self._lock(session_id):
            facts = self._require_facts(
                session_id,
                evaluation_run_id=evaluation_run_id,
                expected_session_revision=expected_session_revision,
                expected_draft_state_revision=expected_draft_state_revision,
                expected_draft_revision=expected_draft_revision,
                expected_draft_digest=expected_draft_digest,
                expected_review_revision=expected_review_revision,
                expected_run_revision=expected_run_revision,
                expected_resource_plan_revision=expected_resource_plan_revision,
                expected_resource_plan_digest=expected_resource_plan_digest,
            )
            planner = self.planner
            try:
                available = bool(planner and planner.available())
            except Exception as exc:
                raise SkillCreatorValidationError(
                    "Skill evolution planner status is unavailable.",
                    code="skill_creator_evolution_planner_failed",
                ) from exc
            if not available or planner is None:
                raise SkillCreatorValidationError(
                    "The Skill Creator model gateway is not configured.",
                    code="model_gateway_unconfigured",
                )
            request = self._generation_request(*facts)
            for attempt in range(2):
                try:
                    payload = await planner.generate(request)
                except SkillCreatorValidationError:
                    raise
                except Exception as exc:
                    raise SkillCreatorValidationError(
                        "The Skill evolution planner failed.",
                        code="skill_creator_evolution_planner_failed",
                    ) from exc
                facts = self._require_facts(
                    session_id,
                    evaluation_run_id=evaluation_run_id,
                    expected_session_revision=expected_session_revision,
                    expected_draft_state_revision=expected_draft_state_revision,
                    expected_draft_revision=expected_draft_revision,
                    expected_draft_digest=expected_draft_digest,
                    expected_review_revision=expected_review_revision,
                    expected_run_revision=expected_run_revision,
                    expected_resource_plan_revision=expected_resource_plan_revision,
                    expected_resource_plan_digest=expected_resource_plan_digest,
                )
                session, draft, run, suite, resource_plan = facts
                try:
                    payload = self._constrain_hook_script_actions(
                        payload,
                        resource_plan,
                    )
                    self._require_payload_evidence_links(payload, run)
                    return self.evolution_store.save_generated(
                        bindings=self._bindings(session, draft, run, suite, resource_plan),
                        payload=payload,
                        **self._allowed(request, resource_plan),
                        expected_plan_revision=expected_evolution_revision,
                        expected_plan_digest=expected_evolution_digest,
                    )
                except SkillCreatorValidationError as exc:
                    if attempt or exc.code != "skill_creator_evolution_plan_invalid":
                        raise
                    request = replace(request, repair={
                        "attempt": 1,
                        "error_code": "skill_creator_evolution_plan_invalid",
                        "instruction": (
                            "Return the complete contract again. Include one to twelve diagnoses "
                            "bound only to allowed case, requirement, and resource IDs. For each "
                            "diagnosis, copy evidence_item_ids exactly from "
                            "allowed.evidence_item_ids_by_case[case_id]; never invent or abbreviate "
                            "an ID. Copy each failure_types value exactly from "
                            "allowed.failure_types. Validate the full replacement against "
                            "output_contract_spec: include every required field, satisfy minItems, "
                            "and never return an empty required string."
                        ),
                    })
            raise AssertionError("bounded evolution planning loop exhausted")

    def save_answers(
        self,
        session_id: str,
        *,
        plan_id: str,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        answers: Mapping[str, Any],
    ) -> SkillEvolutionPlan:
        self.require_enabled()
        session, draft = self._require_context(
            session_id,
            expected_session_revision,
            expected_draft_state_revision,
            expected_draft_revision,
            expected_draft_digest,
        )
        current = self.evolution_store.require(plan_id)
        self._require_plan_scope(current, session=session, draft=draft)
        self._facts_for_plan(current, session=session, draft=draft)
        return self.evolution_store.save_answers(
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
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
        changes: Mapping[str, Any],
    ) -> SkillEvolutionPlan:
        self.require_enabled()
        session, draft = self._require_context(
            session_id,
            expected_session_revision,
            expected_draft_state_revision,
            expected_draft_revision,
            expected_draft_digest,
        )
        current = self.evolution_store.require(plan_id)
        self._require_plan_scope(current, session=session, draft=draft)
        facts = self._facts_for_plan(current, session=session, draft=draft)
        request = self._generation_request(*facts)
        resource_plan = facts[-1]
        return self.evolution_store.patch(
            plan_id,
            expected_revision=expected_plan_revision,
            expected_digest=expected_plan_digest,
            changes=changes,
            **self._allowed(request, resource_plan),
        )

    def confirm(
        self,
        session_id: str,
        *,
        plan_id: str,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_plan_revision: int,
        expected_plan_digest: str,
    ) -> tuple[SkillEvolutionPlan, SkillResourcePlan]:
        self.require_enabled()
        session, draft = self._require_context(
            session_id,
            expected_session_revision,
            expected_draft_state_revision,
            expected_draft_revision,
            expected_draft_digest,
        )
        current = self.evolution_store.require(plan_id)
        self._require_plan_scope(current, session=session, draft=draft)
        self._facts_for_plan(current, session=session, draft=draft)
        if current.revision == expected_plan_revision + 1 and current.state == "confirmed":
            previous = self.evolution_store.require(plan_id, expected_plan_revision)
            if previous.digest != str(expected_plan_digest).lower():
                raise SkillCreatorConflictError("Evolution plan changed. Reload before continuing.")
            confirmed = current
        else:
            confirmed = self.evolution_store.confirm(
                plan_id,
                expected_revision=expected_plan_revision,
                expected_digest=expected_plan_digest,
            )
        resource_plan = self._materialize_resource_plan(confirmed, session=session, draft=draft)
        return confirmed, resource_plan

    def _require_facts(
        self,
        session_id: str,
        *,
        evaluation_run_id: str,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_review_revision: int,
        expected_run_revision: int,
        expected_resource_plan_revision: int,
        expected_resource_plan_digest: str,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun, SkillEvaluationSuite, SkillResourcePlan]:
        session, draft = self._require_context(
            session_id,
            expected_session_revision,
            expected_draft_state_revision,
            expected_draft_revision,
            expected_draft_digest,
        )
        run = self.evaluation_store.require_run(evaluation_run_id)
        if (
            run.revision != int(expected_run_revision)
            or
            run.session_id != session.session_id
            or run.draft_id != draft.draft_id
            or run.draft_revision != draft.content_revision
            or run.frozen_digest != draft.content_digest
            or run.review_state != "revise"
            or not run.reviews
            or run.reviews[-1].review_revision != int(expected_review_revision)
        ):
            raise SkillCreatorConflictError(
                "Only the current draft's frozen revise review can create an evolution plan."
            )
        review = run.reviews[-1]
        if not (review.reason or review.feedback).strip():
            raise SkillCreatorValidationError(
                "Revision feedback is required before planning evolution.",
                code="skill_evaluation_feedback_required",
            )
        if not run.evaluation_suite_id or not run.evaluation_suite_revision or not run.evaluation_suite_digest:
            raise SkillCreatorValidationError(
                "Migrate and confirm a V2 evaluation suite before planning evolution.",
                code="skill_creator_evolution_suite_required",
            )
        suite = self.suite_store.require(run.evaluation_suite_id, revision=run.evaluation_suite_revision)
        if (
            suite.suite_digest != run.evaluation_suite_digest
            or suite.session_id != session.session_id
            or suite.draft_id != draft.draft_id
            or suite.draft_revision != draft.content_revision
            or suite.draft_digest != draft.content_digest
            or suite.state != "confirmed"
        ):
            raise SkillCreatorConflictError("The evaluation suite no longer matches the frozen review.")
        resource_plan = self.resource_plan_store.current_for_session(session.session_id)
        if (
            resource_plan is None
            or resource_plan.revision != int(expected_resource_plan_revision)
            or resource_plan.digest != str(expected_resource_plan_digest).lower()
            or resource_plan.state != "confirmed"
            or not self._resource_plan_produced_draft(
                resource_plan, session=session, draft=draft
            )
        ):
            raise SkillCreatorConflictError(
                "The confirmed resource plan no longer matches the evaluated draft."
            )
        return session, draft, run, suite, resource_plan

    def _resource_plan_produced_draft(
        self,
        plan: SkillResourcePlan,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> bool:
        if (
            plan.session_id == session.session_id
            and plan.draft_id == draft.draft_id
            and plan.draft_revision == draft.revision
            and plan.draft_digest == draft.content_digest
        ):
            return True
        if self.resource_build_store is None or not draft.source_proposal_id:
            return False
        build = self.resource_build_store.current_for_session(session.session_id)
        if (
            build is None
            or build.plan_id != plan.plan_id
            or build.plan_revision != plan.revision
            or build.plan_digest != plan.digest
            or build.proposal_id != draft.source_proposal_id
            or build.state not in {"accepted", "stale"}
            or build.phase != "proposal"
            or not build.skill_markdown
        ):
            return False
        files = {
            item.path: item.content
            for item in build.resources
            if item.action != "delete" and item.content is not None
        }
        files.update(
            {
                path: content
                for path, content in draft.files.items()
                if path.startswith("agents/")
            }
        )
        hook_manifest = str(getattr(build, "hook_manifest", "") or "")
        if hook_manifest:
            files[HOOK_MANIFEST_PATH] = hook_manifest
        try:
            built_digest = compute_package_digest(build.skill_markdown, files)
        except (TypeError, ValueError):
            return False
        return built_digest == draft.content_digest

    def _facts_for_plan(
        self,
        plan: SkillEvolutionPlan,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun, SkillEvaluationSuite, SkillResourcePlan]:
        run = self.evaluation_store.require_run(plan.evaluation_run_id)
        suite = self.suite_store.require(plan.suite_id, revision=plan.suite_revision)
        resource_plan = self.resource_plan_store.require(plan.resource_plan_id, plan.resource_plan_revision)
        review = run.reviews[-1] if run.reviews else None
        if (
            run.revision != plan.evaluation_run_revision
            or run.session_id != session.session_id
            or run.draft_id != draft.draft_id
            or run.draft_revision != draft.content_revision
            or run.frozen_digest != draft.content_digest
            or run.status != "completed"
            or run.review_state != "revise"
            or review is None
            or review.review_id != plan.review_id
            or review.review_revision != plan.review_revision
            or suite.suite_digest != plan.suite_digest
            or suite.session_id != session.session_id
            or suite.draft_id != draft.draft_id
            or suite.draft_revision != draft.content_revision
            or suite.draft_digest != draft.content_digest
            or suite.state != "confirmed"
            or resource_plan.digest != plan.resource_plan_digest
            or resource_plan.session_id != session.session_id
            or resource_plan.state != "confirmed"
            or not self._resource_plan_produced_draft(
                resource_plan,
                session=session,
                draft=draft,
            )
        ):
            raise SkillCreatorConflictError("Frozen evolution evidence changed unexpectedly.")
        self._require_plan_evidence_links(plan, run)
        return session, draft, run, suite, resource_plan

    def _generation_request(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
        run: SkillEvaluationRun,
        suite: SkillEvaluationSuite,
        resource_plan: SkillResourcePlan,
    ) -> EvolutionGenerationRequest:
        current = self.evolution_store.current_for_session(session.session_id)
        item_projection = []
        for item in run.items:
            if item.target != "candidate":
                continue
            item_projection.append({
                "item_id": item.item_id,
                "case_id": item.case_id,
                "status": item.status,
                "error_code": item.error_code,
                "assertion_results": [
                    {
                        "assertion_id": str(result.get("assertion_id") or result.get("id") or "")[:200],
                        "passed": result.get("passed") is True,
                        "code": str(result.get("code") or "")[:200],
                    }
                    for result in item.assertion_results[:30]
                    if isinstance(result, Mapping)
                ],
                "application_compliance": item.application_compliance,
            })
        review = run.reviews[-1]
        return EvolutionGenerationRequest(
            session={
                "session_id": session.session_id,
                "session_revision": session.session_revision,
                "intent": session.intent,
                "expected_output": session.expected_output,
                "success_criteria": list(session.success_criteria),
            },
            draft={
                "draft_id": draft.draft_id,
                "draft_state_revision": draft.revision,
                "draft_revision": draft.content_revision,
                "draft_digest": draft.content_digest,
                "name": draft.name,
                "description": draft.description,
                "skill_markdown_headings": self._markdown_headings(draft.skill_markdown),
                "resource_inventory": [
                    {"path": path, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}
                    for path, content in sorted(draft.files.items())
                    if path.startswith(("scripts/", "references/", "assets/"))
                ],
            },
            evaluation={
                "run_id": run.run_id,
                "run_revision": run.revision,
                "items": item_projection,
            },
            review={
                "review_id": review.review_id,
                "review_revision": review.review_revision,
                "reason": review.reason,
                "feedback": review.feedback,
            },
            suite={
                "suite_id": suite.suite_id,
                "suite_revision": suite.suite_revision,
                "suite_digest": suite.suite_digest,
                "cases": [
                    {
                        "case_id": case.case_id,
                        "role": case.role,
                        "name": case.name,
                        "expected_behavior": case.expected_behavior,
                        "requirement_ids": list(case.requirement_ids),
                        "required_resource_paths": list(case.required_resource_paths),
                        "workflow_step_ids": list(case.workflow_step_ids),
                    }
                    for case in suite.cases
                ],
            },
            resource_plan=SkillResourcePlanStore.serialize(resource_plan),
            current_plan=(self.evolution_store.serialize(current) if current else None),
            allowed_case_ids=tuple(case.case_id for case in suite.cases),
            allowed_item_ids=tuple(item["item_id"] for item in item_projection),
            allowed_requirement_ids=tuple(sorted({item for case in suite.cases for item in case.requirement_ids})),
            allowed_resource_ids=tuple(item.resource_id for item in resource_plan.resources),
            allowed_source_ids=tuple(sorted(self.resource_planning_service._source_ids(session))),
            allowed_step_ids=tuple(item.step_id for item in resource_plan.workflow_steps),
        )

    @staticmethod
    def _constrain_hook_script_actions(
        payload: dict[str, Any],
        resource_plan: SkillResourcePlan,
    ) -> dict[str, Any]:
        actions = payload.get("actions")
        if not isinstance(actions, list):
            return payload
        protected_ids = {
            hook.script_resource_id
            for hook in resource_plan.hooks
            if hook.action != "delete"
        }
        if not protected_ids:
            return payload
        resources = {
            item.resource_id: item
            for item in resource_plan.resources
            if item.resource_id in protected_ids
        }
        constrained: list[Any] = []
        for raw in actions:
            if not isinstance(raw, Mapping):
                constrained.append(raw)
                continue
            item = dict(raw)
            resource_id = str(item.get("resource_id") or "").strip()
            source = resources.get(resource_id)
            if source is not None and str(item.get("action") or "").lower() in {
                "update",
                "delete",
            }:
                item.update(
                    {
                        "action": "keep",
                        "purpose": source.purpose,
                        "source_ids": list(source.source_ids),
                        "used_by_steps": list(source.used_by_steps),
                        "depends_on": list(source.depends_on),
                        "acceptance_checks": list(source.acceptance_checks),
                        "expected_improvement": (
                            "Preserve the deterministically verified Hook script while "
                            "regenerating the final Skill guidance."
                        ),
                    }
                )
            constrained.append(item)
        return {**payload, "actions": constrained}

    @staticmethod
    def _allowed(request: EvolutionGenerationRequest, resource_plan: SkillResourcePlan) -> dict[str, Any]:
        return {
            "allowed_case_ids": set(request.allowed_case_ids),
            "allowed_item_ids": set(request.allowed_item_ids),
            "allowed_requirement_ids": set(request.allowed_requirement_ids),
            "allowed_resources": {
                item.resource_id: {"kind": item.kind, "path": item.path}
                for item in resource_plan.resources
            },
            "allowed_source_ids": set(request.allowed_source_ids),
            "allowed_step_ids": set(request.allowed_step_ids),
        }

    @staticmethod
    def _candidate_item_cases(run: SkillEvaluationRun) -> dict[str, str]:
        return {
            item.item_id: item.case_id
            for item in run.items
            if item.target == "candidate"
        }

    @classmethod
    def _require_payload_evidence_links(
        cls,
        payload: Any,
        run: SkillEvaluationRun,
    ) -> None:
        if not isinstance(payload, Mapping):
            return
        diagnoses = payload.get("diagnoses")
        if not isinstance(diagnoses, list):
            return
        item_cases = cls._candidate_item_cases(run)
        for diagnosis in diagnoses:
            if not isinstance(diagnosis, Mapping):
                continue
            case_id = str(diagnosis.get("case_id") or "").strip()
            item_ids = diagnosis.get("evidence_item_ids")
            if not isinstance(item_ids, list):
                continue
            if any(
                item_cases.get(str(item_id or "").strip()) not in {None, case_id}
                for item_id in item_ids
            ):
                raise SkillCreatorValidationError(
                    "Evolution evidence items must belong to the diagnosed case.",
                    code="skill_creator_evolution_evidence_invalid",
                )

    @classmethod
    def _require_plan_evidence_links(
        cls,
        plan: SkillEvolutionPlan,
        run: SkillEvaluationRun,
    ) -> None:
        item_cases = cls._candidate_item_cases(run)
        if any(
            item_cases.get(item_id) != diagnosis.case_id
            for diagnosis in plan.diagnoses
            for item_id in diagnosis.evidence_item_ids
        ):
            raise SkillCreatorValidationError(
                "Evolution evidence items no longer match the diagnosed case.",
                code="skill_creator_evolution_evidence_invalid",
            )

    @staticmethod
    def _bindings(
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
        run: SkillEvaluationRun,
        suite: SkillEvaluationSuite,
        resource_plan: SkillResourcePlan,
    ) -> dict[str, Any]:
        review = run.reviews[-1]
        return {
            "session_id": session.session_id,
            "session_revision": session.session_revision,
            "draft_id": draft.draft_id,
            "draft_state_revision": draft.revision,
            "draft_revision": draft.content_revision,
            "draft_digest": draft.content_digest,
            "evaluation_run_id": run.run_id,
            "evaluation_run_revision": run.revision,
            "review_id": review.review_id,
            "review_revision": review.review_revision,
            "suite_id": suite.suite_id,
            "suite_revision": suite.suite_revision,
            "suite_digest": suite.suite_digest,
            "resource_plan_id": resource_plan.plan_id,
            "resource_plan_revision": resource_plan.revision,
            "resource_plan_digest": resource_plan.digest,
        }

    def _materialize_resource_plan(
        self,
        evolution: SkillEvolutionPlan,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> SkillResourcePlan:
        source = self.resource_plan_store.require(
            evolution.resource_plan_id, evolution.resource_plan_revision
        )
        if source.digest != evolution.resource_plan_digest:
            raise SkillCreatorConflictError("The source resource plan no longer matches evolution evidence.")
        payload = self._resource_plan_payload(evolution, source)
        ready = self.resource_plan_store.save_evolution_revision(
            source_plan_id=source.plan_id,
            source_revision=source.revision,
            source_digest=source.digest,
            session_revision=session.session_revision,
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
            draft_digest=draft.content_digest,
            payload=payload,
            allowed_source_ids=self.resource_planning_service._source_ids(session),
        )
        return self.resource_plan_store.confirm(
            ready.plan_id,
            expected_revision=ready.revision,
            expected_digest=ready.digest,
            session_revision=session.session_revision,
            draft_revision=draft.revision,
            draft_digest=draft.content_digest,
        ) if ready.state == "ready" else ready

    def _materialized_resource_projection(self, evolution: SkillEvolutionPlan) -> dict[str, Any] | None:
        if evolution.state != "confirmed":
            return None
        try:
            source = self.resource_plan_store.require(
                evolution.resource_plan_id,
                evolution.resource_plan_revision,
            )
        except (SkillCreatorConflictError, SkillCreatorNotFoundError, SkillCreatorValidationError):
            return None
        for revision in (source.revision + 2, source.revision + 1):
            try:
                candidate = self.resource_plan_store.require(source.plan_id, revision)
            except SkillCreatorNotFoundError:
                continue
            if self._resource_plan_matches_evolution(candidate, evolution=evolution, source=source):
                return SkillResourcePlanStore.serialize(candidate)
        return None

    @staticmethod
    def _resource_plan_payload(
        evolution: SkillEvolutionPlan,
        source: SkillResourcePlan,
    ) -> dict[str, Any]:
        path_by_id = {item.resource_id: item.path for item in evolution.actions}
        source_by_id = {item.resource_id: item for item in source.resources}
        resources = []
        for action in evolution.actions:
            source_item = source_by_id.get(action.resource_id)
            resources.append({
                "resource_id": action.resource_id,
                "kind": action.kind,
                "action": action.action,
                "generation_cost": source_item.generation_cost if source_item is not None else "medium",
                "path": action.path,
                "purpose": action.purpose,
                "source_ids": list(action.source_ids),
                "used_by_steps": list(action.used_by_steps),
                "depends_on": [path_by_id[item] for item in action.depends_on],
                "acceptance_checks": list(action.acceptance_checks),
            })
        action_by_resource = {
            item.resource_id: item.action for item in evolution.actions
        }
        hooks = []
        for hook in source.hooks:
            resource_action = action_by_resource.get(hook.script_resource_id, "keep")
            hooks.append(
                {
                    "hook_id": hook.hook_id,
                    "event": hook.event,
                    "mode": hook.mode,
                    "tool_names": list(hook.tool_names),
                    "purpose": hook.purpose,
                    "script_resource_id": hook.script_resource_id,
                    "source_ids": list(hook.source_ids),
                    "used_by_steps": list(hook.used_by_steps),
                    "acceptance_checks": list(hook.acceptance_checks),
                    "action": (
                        "delete"
                        if resource_action == "delete"
                        else "update"
                        if resource_action in {"create", "update"}
                        else "keep"
                    ),
                }
            )
        return {
            "skill_name": source.skill_name,
            "skill_description": source.skill_description,
            "workflow_steps": list(evolution.workflow_steps),
            "output_contract": list(evolution.output_contract),
            "failure_modes": list(evolution.failure_modes),
            "resources": resources,
            "hooks": hooks,
        }

    @staticmethod
    def _resource_plan_matches_evolution(
        candidate: SkillResourcePlan,
        *,
        evolution: SkillEvolutionPlan,
        source: SkillResourcePlan,
    ) -> bool:
        if (
            candidate.plan_id != source.plan_id
            or candidate.revision not in {source.revision + 1, source.revision + 2}
            or candidate.state not in {"ready", "confirmed"}
            or candidate.session_id != evolution.session_id
            or candidate.session_revision != evolution.session_revision
            or candidate.draft_id != evolution.draft_id
            or candidate.draft_revision != evolution.draft_state_revision
            or candidate.draft_digest != evolution.draft_digest
            or candidate.skill_name != source.skill_name
            or candidate.skill_description != source.skill_description
            or tuple((item.step_id, item.instruction) for item in candidate.workflow_steps)
            != tuple((item["step_id"], item["instruction"]) for item in evolution.workflow_steps)
            or tuple(candidate.output_contract) != tuple(evolution.output_contract)
            or tuple(candidate.failure_modes) != tuple(evolution.failure_modes)
            or len(candidate.resources) != len(evolution.actions)
            or len(candidate.hooks) != len(source.hooks)
        ):
            return False
        source_by_id = {item.resource_id: item for item in source.resources}
        for actual, expected in zip(candidate.resources, evolution.actions):
            source_item = source_by_id.get(expected.resource_id)
            expected_cost = source_item.generation_cost if source_item is not None else "medium"
            if (
                actual.resource_id != expected.resource_id
                or actual.kind != expected.kind
                or actual.action != expected.action
                or actual.generation_cost != expected_cost
                or actual.path != expected.path
                or actual.purpose != expected.purpose
                or tuple(actual.source_ids) != tuple(expected.source_ids)
                or tuple(actual.used_by_steps) != tuple(expected.used_by_steps)
                or tuple(actual.depends_on) != tuple(expected.depends_on)
                or tuple(actual.acceptance_checks) != tuple(expected.acceptance_checks)
            ):
                return False
        source_hooks = {item.hook_id: item for item in source.hooks}
        action_by_resource = {
            item.resource_id: item.action for item in evolution.actions
        }
        for actual in candidate.hooks:
            expected = source_hooks.get(actual.hook_id)
            if expected is None:
                return False
            resource_action = action_by_resource.get(expected.script_resource_id, "keep")
            expected_action = (
                "delete"
                if resource_action == "delete"
                else "update"
                if resource_action in {"create", "update"}
                else "keep"
            )
            if (
                actual.event != expected.event
                or actual.mode != expected.mode
                or tuple(actual.tool_names) != tuple(expected.tool_names)
                or actual.purpose != expected.purpose
                or actual.script_resource_id != expected.script_resource_id
                or tuple(actual.source_ids) != tuple(expected.source_ids)
                or tuple(actual.used_by_steps) != tuple(expected.used_by_steps)
                or tuple(actual.acceptance_checks) != tuple(expected.acceptance_checks)
                or actual.action != expected_action
            ):
                return False
        return True

    def _require_context(
        self,
        session_id: str,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft]:
        session, draft = self.creator_service.get_session(session_id)
        if draft is None:
            raise SkillCreatorValidationError("Create a Skill draft before planning evolution.", code="skill_creator_draft_required")
        if (
            session.session_revision != int(expected_session_revision)
            or draft.revision != int(expected_draft_state_revision)
            or draft.content_revision != int(expected_draft_revision)
            or draft.content_digest != str(expected_draft_digest).lower()
        ):
            raise SkillCreatorConflictError("Creator session or draft changed. Reload before continuing.")
        return session, draft

    @staticmethod
    def _require_plan_scope(plan: SkillEvolutionPlan, *, session: SkillCreatorSession, draft: WorkspaceSkillDraft) -> None:
        if SkillCreatorEvolutionService._is_stale(plan, session=session, draft=draft):
            raise SkillCreatorConflictError("Evolution plan no longer matches the current session and draft.")

    @staticmethod
    def _is_stale(plan: SkillEvolutionPlan, *, session: SkillCreatorSession, draft: WorkspaceSkillDraft) -> bool:
        return bool(
            plan.session_id != session.session_id
            or plan.session_revision != session.session_revision
            or plan.draft_id != draft.draft_id
            or plan.draft_state_revision != draft.revision
            or plan.draft_revision != draft.content_revision
            or plan.draft_digest != draft.content_digest
        )

    @staticmethod
    def _markdown_headings(markdown: str) -> list[str]:
        return [line.lstrip("#").strip()[:300] for line in markdown.splitlines() if line.startswith("#")][:80]

    def _lock(self, session_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock


__all__ = [
    "EVOLUTION_SERVICE_VERSION",
    "EvolutionGenerationRequest",
    "EvolutionPlanner",
    "SkillCreatorEvolutionService",
]
