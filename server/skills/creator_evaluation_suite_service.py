from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol

from .creator_evaluation import SkillEvaluationNotFoundError, SkillEvaluationStore
from .creator_evaluation_suite import SkillEvaluationSuite, SkillEvaluationSuiteStore
from .creator_quality import build_session_requirement_ids
from .creator_resource_plan import SkillResourcePlan, SkillResourcePlanStore
from .creator_service import SkillCreatorService
from .creator_store import (
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorValidationError,
)
from .draft_store import WorkspaceSkillDraft


EVALUATION_SUITE_SERVICE_VERSION = "skill-creator-evaluation-suite-v2"
_RESOURCE_ROOTS = ("scripts/", "references/", "assets/")


@dataclass(frozen=True, slots=True)
class EvaluationSuiteGenerationRequest:
    session: dict[str, Any]
    draft: dict[str, Any]
    resource_plan: dict[str, Any] | None
    allowed_requirement_ids: tuple[str, ...]
    allowed_resource_paths: tuple[str, ...]
    allowed_workflow_step_ids: tuple[str, ...]
    coverage_repair: dict[str, Any] | None = None


class EvaluationSuiteGenerator(Protocol):
    def available(self) -> bool: ...

    async def generate(
        self, request: EvaluationSuiteGenerationRequest
    ) -> dict[str, Any]: ...


class SkillCreatorEvaluationSuiteService:
    """Bind immutable suites to the current Creator session and draft facts."""

    VERSION = EVALUATION_SUITE_SERVICE_VERSION

    def __init__(
        self,
        creator_service: SkillCreatorService,
        suite_store: SkillEvaluationSuiteStore,
        evaluation_store: SkillEvaluationStore,
        *,
        resource_plan_store: SkillResourcePlanStore | None = None,
        generator: EvaluationSuiteGenerator | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.creator_service = creator_service
        self.suite_store = suite_store
        self.evaluation_store = evaluation_store
        self.resource_plan_store = resource_plan_store
        self.generator = generator
        self.enabled = (
            os.getenv("SKILL_CREATOR_EVOLUTION_V2_ENABLED", "true")
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
            generator_available = bool(self.generator and self.generator.available())
        except Exception:
            generator_available = False
        store_status = self.suite_store.status()
        return {
            "evaluation_suite_version": self.VERSION,
            "evaluation_suite_enabled": self.enabled,
            "evaluation_suite_generator_available": (
                self.enabled and generator_available
            ),
            "evaluation_suite_store_available": bool(store_status["available"]),
        }

    def require_enabled(self) -> None:
        self.creator_service.require_enabled()
        if not self.enabled:
            raise SkillCreatorValidationError(
                "Skill Creator Evolution V2 is disabled.",
                code="skill_creator_evolution_v2_disabled",
            )

    def current_projection(self, session_id: str) -> dict[str, Any] | None:
        self.creator_service.require_enabled()
        session, draft = self.creator_service.get_session(session_id)
        current = self.suite_store.current_for_session(session_id)
        return self._projection(current, session=session, draft=draft)

    async def generate(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_suite_revision: int | None,
        expected_suite_digest: str | None,
    ) -> SkillEvaluationSuite:
        self.require_enabled()
        session, _draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_draft_state_revision=expected_draft_state_revision,
            expected_draft_revision=expected_draft_revision,
            expected_draft_digest=expected_draft_digest,
        )
        async with self._lock(session.session_id):
            return await self._generate_locked(
                session.session_id,
                expected_session_revision=expected_session_revision,
                expected_draft_state_revision=expected_draft_state_revision,
                expected_draft_revision=expected_draft_revision,
                expected_draft_digest=expected_draft_digest,
                expected_suite_revision=expected_suite_revision,
                expected_suite_digest=expected_suite_digest,
            )

    async def _generate_locked(
        self,
        session_id: str,
        **expected: Any,
    ) -> SkillEvaluationSuite:
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected["expected_session_revision"],
            expected_draft_state_revision=expected["expected_draft_state_revision"],
            expected_draft_revision=expected["expected_draft_revision"],
            expected_draft_digest=expected["expected_draft_digest"],
        )
        self.creator_service._require_ready_for_generation(session)
        current = self.suite_store.current_for_session(session_id)
        self._require_expected_suite(
            current,
            expected_revision=expected.get("expected_suite_revision"),
            expected_digest=expected.get("expected_suite_digest"),
        )
        if current is not None and current.state == "confirmed" and not self._is_stale(
            current, session=session, draft=draft
        ):
            raise SkillCreatorConflictError(
                "The current evaluation suite is already confirmed. Edit it with a reason instead."
            )
        coverage = self._coverage_context(session, draft)

        # A V1 three-case set is authoritative local data. Its first V2 suite
        # migration must not spend a model call or reinterpret user-authored cases.
        if current is None and session.cases_revision > 0:
            try:
                legacy = self.evaluation_store.require_cases(
                    session.session_id, revision=session.cases_revision
                )
            except SkillEvaluationNotFoundError:
                legacy = None
            if legacy is not None and (
                legacy.draft_id == draft.draft_id
                and legacy.draft_revision == draft.content_revision
                and legacy.content_digest == draft.content_digest
                and len(legacy.cases) == 3
            ):
                return self.suite_store.migrate_case_set(
                    session_id=session.session_id,
                    session_revision=session.session_revision,
                    session_definition_digest=self._session_definition_digest(session),
                    draft_id=draft.draft_id,
                    draft_state_revision=draft.revision,
                    draft_revision=draft.content_revision,
                    draft_digest=draft.content_digest,
                    quality_mode=session.quality_mode,
                    cases=legacy.cases,
                    allowed_requirement_ids=coverage["requirements"],
                )

        generator = self.generator
        try:
            available = bool(generator and generator.available())
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The Skill Creator evaluation suite generator status is unavailable.",
                code="skill_evaluation_suite_generator_failed",
            ) from exc
        if not available or generator is None:
            raise SkillCreatorValidationError(
                "The Skill Creator model gateway is not configured.",
                code="model_gateway_unconfigured",
            )
        request = EvaluationSuiteGenerationRequest(
            session=asdict(session),
            draft=self._draft_context(draft),
            resource_plan=coverage["resource_plan"],
            allowed_requirement_ids=coverage["requirements"],
            allowed_resource_paths=coverage["resources"],
            allowed_workflow_step_ids=coverage["workflow_steps"],
        )
        try:
            payload = await generator.generate(request)
        except (SkillCreatorConflictError, SkillCreatorValidationError):
            raise
        except Exception as exc:
            raise SkillCreatorValidationError(
                "The dedicated Skill Creator agent could not design an evaluation suite.",
                code="skill_evaluation_suite_generator_failed",
            ) from exc
        cases = self._generated_cases(payload)
        missing_requirement_ids = self._missing_requirement_ids(
            cases, coverage["requirements"]
        )
        if missing_requirement_ids:
            repair_request = EvaluationSuiteGenerationRequest(
                session=request.session,
                draft=request.draft,
                resource_plan=request.resource_plan,
                allowed_requirement_ids=request.allowed_requirement_ids,
                allowed_resource_paths=request.allowed_resource_paths,
                allowed_workflow_step_ids=request.allowed_workflow_step_ids,
                coverage_repair={
                    "missing_requirement_ids": list(missing_requirement_ids),
                    "previous_cases": cases,
                },
            )
            try:
                payload = await generator.generate(repair_request)
            except (SkillCreatorConflictError, SkillCreatorValidationError):
                raise
            except Exception as exc:
                raise SkillCreatorValidationError(
                    "The dedicated Skill Creator agent could not repair evaluation suite coverage.",
                    code="skill_evaluation_suite_generator_failed",
                ) from exc
            cases = self._generated_cases(payload)
            missing_requirement_ids = self._missing_requirement_ids(
                cases, coverage["requirements"]
            )
        if missing_requirement_ids:
            raise SkillCreatorValidationError(
                "The evaluation suite generator did not cover every frozen Creator requirement.",
                code="skill_evaluation_suite_generator_coverage_incomplete",
            )

        # Freeze only if the session and draft are still the facts shown to the model.
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected["expected_session_revision"],
            expected_draft_state_revision=expected["expected_draft_state_revision"],
            expected_draft_revision=expected["expected_draft_revision"],
            expected_draft_digest=expected["expected_draft_digest"],
        )
        return self.suite_store.save_generated(
            session_id=session.session_id,
            session_revision=session.session_revision,
            session_definition_digest=self._session_definition_digest(session),
            draft_id=draft.draft_id,
            draft_state_revision=draft.revision,
            draft_revision=draft.content_revision,
            draft_digest=draft.content_digest,
            quality_mode=session.quality_mode,
            cases=cases,
            expected_suite_revision=expected.get("expected_suite_revision"),
            expected_suite_digest=expected.get("expected_suite_digest"),
            allowed_requirement_ids=coverage["requirements"],
            allowed_resource_paths=coverage["resources"],
            allowed_workflow_step_ids=coverage["workflow_steps"],
        )

    @staticmethod
    def _generated_cases(payload: Any) -> list[Mapping[str, Any]]:
        cases = payload.get("cases") if isinstance(payload, Mapping) else None
        if not isinstance(cases, list) or not all(
            isinstance(case, Mapping) for case in cases
        ):
            raise SkillCreatorValidationError(
                "The evaluation suite generator returned an invalid case list.",
                code="skill_evaluation_suite_generator_invalid",
            )
        return cases

    @staticmethod
    def _missing_requirement_ids(
        cases: list[Mapping[str, Any]], allowed_requirement_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        covered = {
            requirement_id
            for case in cases
            for requirement_id in (
                case.get("requirement_ids")
                if isinstance(case.get("requirement_ids"), (list, tuple))
                else ()
            )
            if isinstance(requirement_id, str)
        }
        return tuple(
            requirement_id
            for requirement_id in allowed_requirement_ids
            if requirement_id not in covered
        )

    def patch(
        self,
        session_id: str,
        *,
        suite_id: str,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
        cases: list[Mapping[str, Any]],
        change_reason: str,
    ) -> SkillEvaluationSuite:
        self.require_enabled()
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_draft_state_revision=expected_draft_state_revision,
            expected_draft_revision=expected_draft_revision,
            expected_draft_digest=expected_draft_digest,
        )
        current = self.suite_store.require(suite_id)
        self._require_suite_scope(current, session=session, draft=draft)
        coverage = self._coverage_context(session, draft)
        self.suite_store.validate_patch(
            suite_id,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            cases=cases,
            change_reason=change_reason,
            allowed_requirement_ids=coverage["requirements"],
            allowed_resource_paths=coverage["resources"],
            allowed_workflow_step_ids=coverage["workflow_steps"],
        )
        if current.state == "confirmed" and draft.quality_status not in {
            "not_evaluated",
            "outdated",
        }:
            draft = self.creator_service.draft_store.mark_quality_outdated(
                draft.draft_id,
                expected_revision=draft.revision,
                expected_digest=draft.content_digest,
            )
        return self.suite_store.patch(
            suite_id,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            session_revision=session.session_revision,
            session_definition_digest=self._session_definition_digest(session),
            draft_state_revision=draft.revision,
            cases=cases,
            change_reason=change_reason,
            allowed_requirement_ids=coverage["requirements"],
            allowed_resource_paths=coverage["resources"],
            allowed_workflow_step_ids=coverage["workflow_steps"],
        )

    def confirm(
        self,
        session_id: str,
        *,
        suite_id: str,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
        expected_suite_revision: int,
        expected_suite_digest: str,
    ) -> SkillEvaluationSuite:
        self.require_enabled()
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_draft_state_revision=expected_draft_state_revision,
            expected_draft_revision=expected_draft_revision,
            expected_draft_digest=expected_draft_digest,
        )
        current = self.suite_store.require(suite_id)
        self._require_suite_scope(current, session=session, draft=draft)
        coverage = self._coverage_context(session, draft)
        return self.suite_store.confirm(
            suite_id,
            expected_suite_revision=expected_suite_revision,
            expected_suite_digest=expected_suite_digest,
            session_revision=session.session_revision,
            session_definition_digest=self._session_definition_digest(session),
            draft_state_revision=draft.revision,
            allowed_requirement_ids=coverage["requirements"],
            allowed_resource_paths=coverage["resources"],
            allowed_workflow_step_ids=coverage["workflow_steps"],
        )

    def require_confirmed_current(
        self, session: SkillCreatorSession, draft: WorkspaceSkillDraft
    ) -> SkillEvaluationSuite:
        self.require_enabled()
        current = self.suite_store.current_for_session(session.session_id)
        if current is None or current.state != "confirmed":
            raise SkillCreatorValidationError(
                "Confirm the current evaluation suite before starting a V2 evaluation.",
                code="skill_evaluation_suite_confirmation_required",
            )
        self._require_suite_scope(current, session=session, draft=draft)
        return current

    @staticmethod
    def _projection(
        suite: SkillEvaluationSuite | None,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft | None,
    ) -> dict[str, Any] | None:
        if suite is None:
            return None
        data = SkillEvaluationSuiteStore.serialize(suite)
        data["stale"] = bool(
            draft is None
            or SkillCreatorEvaluationSuiteService._is_stale(
                suite, session=session, draft=draft
            )
        )
        return data

    def _coverage_context(
        self, session: SkillCreatorSession, draft: WorkspaceSkillDraft
    ) -> dict[str, Any]:
        creator_requirements = build_session_requirement_ids(
            intent=session.intent,
            positive_examples=session.positive_examples,
            near_miss_examples=session.near_miss_examples,
            expected_output=session.expected_output,
            success_criteria=session.success_criteria,
        )
        # Positive examples guide the fixed three-role case designer, but they are
        # not independent test obligations.  Requiring one frozen coverage ID per
        # example can make a valid normal/ambiguous/boundary suite impossible to
        # confirm without inventing extra model-authored regression cases.
        requirements = tuple(
            requirement_id
            for requirement_id in creator_requirements
            if not requirement_id.startswith("positive_example:")
        )
        resources = tuple(
            sorted(path for path in draft.files if path.startswith(_RESOURCE_ROOTS))
        )
        plan: SkillResourcePlan | None = None
        if self.resource_plan_store is not None:
            plan = self.resource_plan_store.current_for_session(session.session_id)
        steps = tuple(item.step_id for item in plan.workflow_steps) if plan else ()
        return {
            "requirements": requirements,
            "resources": resources,
            "workflow_steps": steps,
            "resource_plan": (
                SkillResourcePlanStore.serialize(plan) if plan is not None else None
            ),
        }

    @staticmethod
    def _draft_context(draft: WorkspaceSkillDraft) -> dict[str, Any]:
        inventory = []
        for path, content in sorted(draft.files.items()):
            if not path.startswith(_RESOURCE_ROOTS):
                continue
            encoded = content.encode("utf-8")
            inventory.append(
                {
                    "path": path,
                    "size_bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "kind": path.split("/", 1)[0],
                }
            )
        return {
            "draft_id": draft.draft_id,
            "draft_revision": draft.content_revision,
            "draft_digest": draft.content_digest,
            "name": draft.name,
            "description": draft.description,
            "skill_markdown": draft.skill_markdown[:20_000],
            "resource_inventory": inventory,
        }

    def _require_context(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_draft_state_revision: int,
        expected_draft_revision: int,
        expected_draft_digest: str,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft]:
        session, draft = self.creator_service.get_session(session_id)
        if draft is None:
            raise SkillCreatorValidationError(
                "Create a Skill draft before designing its evaluation suite.",
                code="skill_creator_draft_required",
            )
        if (
            session.session_revision != int(expected_session_revision)
            or draft.revision != int(expected_draft_state_revision)
            or draft.content_revision != int(expected_draft_revision)
            or draft.content_digest != str(expected_draft_digest or "").lower()
        ):
            raise SkillCreatorConflictError(
                "Creator session or draft changed. Reload before continuing."
            )
        if session.active_evaluation_run_id:
            raise SkillCreatorConflictError(
                "Cancel the active evaluation before changing its evaluation suite."
            )
        return session, draft

    @staticmethod
    def _require_expected_suite(
        current: SkillEvaluationSuite | None,
        *,
        expected_revision: int | None,
        expected_digest: str | None,
    ) -> None:
        if current is None:
            if expected_revision is not None or expected_digest is not None:
                raise SkillCreatorConflictError(
                    "Evaluation suite changed. Reload before continuing."
                )
            return
        if (
            expected_revision is None
            or expected_digest is None
            or current.suite_revision != int(expected_revision)
            or current.suite_digest != str(expected_digest).lower()
        ):
            raise SkillCreatorConflictError(
                "Evaluation suite changed. Reload before continuing."
            )

    @staticmethod
    def _require_suite_scope(
        suite: SkillEvaluationSuite,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> None:
        if SkillCreatorEvaluationSuiteService._is_stale(
            suite, session=session, draft=draft
        ):
            raise SkillCreatorConflictError(
                "Evaluation suite no longer matches the current Creator session and draft."
            )

    @staticmethod
    def _is_stale(
        suite: SkillEvaluationSuite,
        *,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> bool:
        return bool(
            suite.session_id != session.session_id
            or suite.session_definition_digest
            != SkillCreatorEvaluationSuiteService._session_definition_digest(session)
            or suite.draft_id != draft.draft_id
            or suite.draft_revision != draft.content_revision
            or suite.draft_digest != draft.content_digest
            or suite.quality_mode != session.quality_mode
        )

    @staticmethod
    def _session_definition_digest(session: SkillCreatorSession) -> str:
        payload = {
            "intent": session.intent,
            "positive_examples": list(session.positive_examples),
            "near_miss_examples": list(session.near_miss_examples),
            "expected_output": session.expected_output,
            "success_criteria": list(session.success_criteria),
            "evidence_preview_fingerprint": session.evidence_preview_fingerprint,
            "selected_evidence": list(session.selected_evidence),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _lock(self, session_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock


__all__ = [
    "EVALUATION_SUITE_SERVICE_VERSION",
    "EvaluationSuiteGenerationRequest",
    "EvaluationSuiteGenerator",
    "SkillCreatorEvaluationSuiteService",
]
