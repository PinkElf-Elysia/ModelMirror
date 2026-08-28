from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

from .creator_evaluation import (
    SkillEvaluationConflictError,
    SkillEvaluationError,
    SkillEvaluationExecutor,
    SkillEvaluationNotFoundError,
    SkillEvaluationRun,
    SkillEvaluationStateError,
    SkillEvaluationStore,
    SkillEvaluationValidationError,
    aggregate_skill_evaluation_report,
)
from .creator_evaluation_suite import SkillEvaluationSuite, SkillEvaluationSuiteStore
from .creator_evaluation_suite_service import SkillCreatorEvaluationSuiteService
from .creator_evolution import SkillEvolutionPlanStore
from .creator_store import (
    CreatorQualityMode,
    SkillCreatorConflictError,
    SkillCreatorSession,
    SkillCreatorSessionStore,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from .draft_store import WorkspaceSkillDraft, WorkspaceSkillDraftStore


EvaluationPurpose = Literal["evaluate", "accept", "waive"]
_BINDING_FAILURE_REASON = (
    "Creator session or draft changed before evaluation binding completed."
)


@dataclass(frozen=True, slots=True)
class SkillEvaluationPreflightResult:
    model_id: str | None = None
    config: dict[str, Any] | None = None


class SkillEvaluationPreflight(Protocol):
    async def __call__(
        self, draft: WorkspaceSkillDraft, purpose: EvaluationPurpose
    ) -> SkillEvaluationPreflightResult | Mapping[str, Any] | None: ...


class SkillEvaluationIteration(Protocol):
    async def __call__(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
        run: SkillEvaluationRun,
        feedback: str,
    ) -> Any: ...


class SkillEvaluationReceiptVerifier(Protocol):
    def __call__(self, run: SkillEvaluationRun) -> None: ...


class SkillCreatorEvaluationService:
    """Cross-store quality gate for one private Skill Creator session.

    Evaluation cases and results deliberately remain in ``SkillEvaluationStore``.
    The Session Store only carries a recoverable projection; the Draft Store owns
    the install-blocking quality receipt.
    """

    VERSION = "skill-creator-evaluation-v1"

    def __init__(
        self,
        session_store: SkillCreatorSessionStore,
        draft_store: WorkspaceSkillDraftStore,
        evaluation_store: SkillEvaluationStore,
        *,
        executor: SkillEvaluationExecutor,
        preflight: SkillEvaluationPreflight,
        actor_id: str,
        iteration: SkillEvaluationIteration | None = None,
        suite_service: SkillCreatorEvaluationSuiteService | None = None,
        evolution_store: SkillEvolutionPlanStore | None = None,
        application_receipt_verifier: SkillEvaluationReceiptVerifier | None = None,
    ) -> None:
        clean_actor_id = str(actor_id or "").strip()
        if not clean_actor_id:
            raise ValueError("Skill evaluation local-console actor id is required.")
        self.session_store = session_store
        self.draft_store = draft_store
        self.evaluation_store = evaluation_store
        self.executor = executor
        self.preflight = preflight
        self.actor_id = clean_actor_id
        self.iteration = iteration
        self.suite_service = suite_service
        self.evolution_store = evolution_store
        self.application_receipt_verifier = application_receipt_verifier

    def get_projection(
        self, session_id: str
    ) -> tuple[
        SkillCreatorSession,
        WorkspaceSkillDraft,
        dict[str, Any] | None,
        SkillEvaluationRun | None,
    ]:
        session = self.session_store.require(session_id)
        draft = self._require_session_draft(session)
        session, draft, recovered_case_set = self._recover_orphan_case_set(
            session, draft
        )
        session, draft, run = self._reconcile(session, draft)
        case_set = recovered_case_set or self._current_case_set(session, draft)
        return (
            session,
            draft,
            (
                self.evaluation_store.serialize(case_set)
                if case_set is not None
                else None
            ),
            run,
        )

    def get_run_projection(
        self, run_id: str
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun]:
        """Return one run after reconciling its authoritative Session/Draft projection."""

        requested = self.evaluation_store.require_run(run_id)
        session = self.session_store.require(requested.session_id)
        draft = self._require_session_draft(session)
        session, draft, _ = self._reconcile(session, draft)
        return session, draft, self.evaluation_store.require_run(run_id)

    def governance_projection(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> dict[str, Any]:
        """Return bounded version history and the next-run budget projection."""

        suite = None
        if self.suite_service is not None and self.suite_service.enabled:
            candidate_suite = self.suite_service.suite_store.current_for_session(
                session.session_id
            )
            if (
                candidate_suite is not None
                and candidate_suite.state == "confirmed"
                and candidate_suite.draft_id == draft.draft_id
                and candidate_suite.draft_revision == draft.content_revision
                and self._same_digest(
                    candidate_suite.draft_digest, draft.content_digest
                )
            ):
                suite = candidate_suite
        previous_revision: int | None = None
        previous_digest: str | None = None
        evolution_history_available = True
        if suite is not None and self.evolution_store is not None:
            try:
                evolution = self.evolution_store.current_for_session(
                    session.session_id
                )
            except SkillCreatorStorageError:
                evolution = None
                evolution_history_available = False
            if (
                evolution is not None
                and evolution.session_id == session.session_id
                and evolution.draft_id == draft.draft_id
                and evolution.draft_revision < draft.content_revision
                and evolution.state in {"confirmed", "stale"}
            ):
                previous_revision = evolution.draft_revision
                previous_digest = evolution.draft_digest
        case_count = len(suite.cases) if suite is not None else 3
        target_count = 3 if previous_revision is not None else 2
        estimated_model_calls = case_count * target_count
        max_repetitions = max(
            1,
            min(
                3,
                SkillEvaluationStore.MAX_RUN_ITEMS
                // max(1, estimated_model_calls),
            ),
        )
        revisions = []
        for snapshot in self.draft_store.list_revision_snapshots(draft.draft_id):
            revisions.append(
                {
                    "revision": snapshot.revision,
                    "content_digest": snapshot.content_digest,
                    "source_proposal_id": snapshot.source_proposal_id,
                    "created_at": snapshot.created_at,
                    "is_current": snapshot.revision == draft.content_revision,
                    "is_installed": (
                        snapshot.revision == draft.installed_content_revision
                        and self._same_digest(
                            snapshot.content_digest,
                            draft.installed_content_digest,
                        )
                    ),
                    "is_previous": snapshot.revision == previous_revision,
                }
            )
        runs = []
        for run in self.evaluation_store.list_runs(
            session_id=session.session_id, limit=50
        ):
            runs.append(
                {
                    "run_id": run.run_id,
                    "draft_revision": run.draft_revision,
                    "frozen_digest": run.frozen_digest,
                    "status": run.status,
                    "review_state": run.review_state,
                    "suite_revision": run.evaluation_suite_revision,
                    "target_count": 3 if run.previous_overlay_id else 2,
                    "item_count": len(run.items),
                    "comparison_counts": dict(
                        (run.report or {}).get("comparison_counts") or {}
                    ),
                    "created_at": run.created_at,
                    "completed_at": run.completed_at,
                }
            )
        return {
            "version": "skill-creator-regression-v1",
            "enabled": bool(suite is not None),
            "max_items": SkillEvaluationStore.MAX_RUN_ITEMS,
            "case_count": case_count,
            "target_count": target_count,
            "estimated_model_calls": estimated_model_calls,
            "max_repetitions": max_repetitions,
            "previous_revision": previous_revision,
            "previous_digest": previous_digest,
            "evolution_history_available": evolution_history_available,
            "revisions": revisions,
            "runs": runs,
        }

    def save_cases(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        quality_mode: CreatorQualityMode,
        cases: list[Mapping[str, Any]],
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, dict[str, Any]]:
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        draft = self.draft_store.require_evaluation_ready(
            draft.draft_id,
            expected_revision=draft.revision,
            expected_digest=draft.content_digest,
        )
        if (
            self.suite_service is not None
            and self.suite_service.enabled
            and self.suite_service.suite_store.current_for_session(session.session_id)
            is not None
        ):
            raise SkillCreatorConflictError(
                "This Creator session uses an evaluation suite; edit the suite instead."
            )
        if session.active_evaluation_run_id:
            raise SkillCreatorConflictError(
                "Cancel the active evaluation before changing its cases."
            )
        if quality_mode not in {"objective", "subjective"}:
            raise SkillCreatorValidationError(
                "Invalid Creator quality mode.", code="skill_creator_quality_mode_invalid"
            )

        if session.quality_mode != quality_mode:
            session = self.session_store.set_quality_mode(
                session.session_id,
                expected_session_revision=session.session_revision,
                quality_mode=quality_mode,
            )
        draft = self._mark_outdated_if_needed(draft)
        case_set = self.evaluation_store.save_cases(
            session_id=session.session_id,
            draft_id=draft.draft_id,
            draft_revision=draft.content_revision,
            content_digest=draft.content_digest,
            expected_revision=session.cases_revision,
            cases=cases,
            quality_mode=quality_mode,
        )
        session = self.session_store.bind_cases(
            session.session_id,
            expected_session_revision=session.session_revision,
            cases_revision=case_set.cases_revision,
            baseline_content_revision=draft.installed_content_revision,
            baseline_content_digest=draft.installed_content_digest,
        )
        session = self._project_session(session, draft, None)
        return session, draft, self.evaluation_store.serialize(case_set)

    def set_quality_mode(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        quality_mode: CreatorQualityMode,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft | None]:
        session = self.session_store.require(session_id)
        if session.session_revision != int(expected_session_revision):
            raise SkillCreatorConflictError(
                "Creator session changed. Reload before continuing."
            )
        if session.quality_mode == quality_mode:
            draft = (
                self._require_session_draft(session) if session.draft_id else None
            )
            return session, draft
        # Session Store enforces the active-run freeze before any cross-store
        # quality receipt is invalidated.
        if session.active_evaluation_run_id or session.review_state in {
            "accepted",
            "waived",
        }:
            raise SkillCreatorConflictError(
                "Quality mode is frozen for the current evaluation cycle."
            )
        draft = self._require_session_draft(session) if session.draft_id else None
        if draft is not None:
            draft = self._mark_outdated_if_needed(draft)
        session = self.session_store.set_quality_mode(
            session.session_id,
            expected_session_revision=session.session_revision,
            quality_mode=quality_mode,
        )
        if draft is not None:
            session = self._project_session(session, draft, None)
        return session, draft

    async def start_evaluation(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        repetitions: int = 1,
        evaluation_suite_revision: int | None = None,
        evaluation_suite_digest: str | None = None,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun]:
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        draft = self.draft_store.require_evaluation_ready(
            draft.draft_id,
            expected_revision=draft.revision,
            expected_digest=draft.content_digest,
        )
        suite = self._current_v2_suite(session, draft)
        if (evaluation_suite_revision is None) != (
            evaluation_suite_digest is None
        ):
            raise SkillEvaluationConflictError(
                "Evaluation suite revision and digest must be supplied together."
            )
        if suite is not None:
            if (
                evaluation_suite_revision is not None
                and int(evaluation_suite_revision) != suite.suite_revision
            ) or (
                evaluation_suite_digest is not None
                and str(evaluation_suite_digest).lower() != suite.suite_digest
            ):
                raise SkillEvaluationConflictError(
                    "Evaluation suite changed. Reload before starting the evaluation."
                )
            cases = SkillEvaluationSuiteStore.to_evaluation_cases(suite)
            case_set = None
        else:
            if (
                evaluation_suite_revision is not None
                or evaluation_suite_digest is not None
            ):
                raise SkillEvaluationConflictError(
                    "The requested evaluation suite is unavailable."
                )
            case_set = self._require_current_cases(session, draft)
            if len(case_set.cases) != SkillEvaluationStore.REQUIRED_CASE_COUNT:
                raise SkillEvaluationValidationError(
                    "Evaluation requires exactly three frozen cases.",
                    code="skill_evaluation_three_cases_required",
                )
            cases = None
        preflight = await self._run_preflight(draft, "evaluate")
        model_id = str(preflight.model_id or "").strip()
        if not model_id:
            raise SkillEvaluationValidationError(
                "The evaluation model is not configured.",
                code="skill_evaluation_model_unconfigured",
            )

        # Re-read after the await. No overlay or run may be created for a stale
        # content revision.
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if suite is not None:
            refreshed_suite = self._require_v2_suite(session, draft)
            if (
                refreshed_suite.suite_id != suite.suite_id
                or refreshed_suite.suite_revision != suite.suite_revision
                or not self._same_digest(
                    refreshed_suite.suite_digest, suite.suite_digest
                )
            ):
                raise SkillEvaluationConflictError(
                    "Evaluation suite changed during preflight. Reload before starting."
                )
            suite = refreshed_suite
            cases = SkillEvaluationSuiteStore.to_evaluation_cases(suite)
        existing = self._recoverable_run(
            session,
            draft,
            cases_revision=(case_set.cases_revision if case_set else None),
            suite=suite,
        )
        if existing is not None:
            draft = self._begin_or_reuse_draft_run(draft, existing.run_id)
            if session.active_evaluation_run_id != existing.run_id:
                session = self.session_store.bind_evaluation(
                    session.session_id,
                    expected_session_revision=session.session_revision,
                    run_id=existing.run_id,
                )
            session = self._project_session(session, draft, existing)
            self.executor.wake()
            return session, draft, existing

        previous_snapshot = None
        if suite is not None and self.evolution_store is not None:
            evolution = self.evolution_store.current_for_session(session.session_id)
            if (
                evolution is not None
                and evolution.session_id == session.session_id
                and evolution.draft_id == draft.draft_id
                and evolution.draft_revision < draft.content_revision
                and evolution.state in {"confirmed", "stale"}
            ):
                previous_snapshot = self.draft_store.require_revision_snapshot(
                    draft.draft_id,
                    revision=evolution.draft_revision,
                    content_digest=evolution.draft_digest,
                )
        case_count = len(cases if cases is not None else case_set.cases)
        target_count = 3 if previous_snapshot is not None else 2
        estimated_model_calls = case_count * int(repetitions) * target_count
        if estimated_model_calls > SkillEvaluationStore.MAX_RUN_ITEMS:
            raise SkillEvaluationValidationError(
                (
                    f"Evaluation would require {estimated_model_calls} model calls; "
                    f"the maximum is {SkillEvaluationStore.MAX_RUN_ITEMS}."
                ),
                code="skill_evaluation_item_budget_exceeded",
            )

        candidate_snapshot = self.draft_store.require_revision_snapshot(
            draft.draft_id,
            revision=draft.content_revision,
            content_digest=draft.content_digest,
        )
        candidate = self.evaluation_store.create_overlay(
            draft_id=draft.draft_id,
            draft_revision=candidate_snapshot.revision,
            content_digest=candidate_snapshot.content_digest,
            package=candidate_snapshot.package,
        )
        baseline_id: str | None = None
        if session.baseline_content_revision is not None:
            baseline = self.draft_store.require_revision_snapshot(
                draft.draft_id,
                revision=session.baseline_content_revision,
                content_digest=session.baseline_content_digest,
            )
            baseline_overlay = self.evaluation_store.create_overlay(
                draft_id=draft.draft_id,
                draft_revision=baseline.revision,
                content_digest=baseline.content_digest,
                package=baseline.package,
            )
            baseline_id = baseline_overlay.overlay_id
        previous_id: str | None = None
        if previous_snapshot is not None:
            previous_overlay = self.evaluation_store.create_overlay(
                draft_id=draft.draft_id,
                draft_revision=previous_snapshot.revision,
                content_digest=previous_snapshot.content_digest,
                package=previous_snapshot.package,
            )
            previous_id = previous_overlay.overlay_id
        run_config = dict(preflight.config or {})
        if suite is not None:
            run_config.update(
                {
                    "regression_governance_version": "skill-creator-regression-v1",
                    "target_count": target_count,
                    "estimated_model_calls": estimated_model_calls,
                }
            )
        run = self.evaluation_store.create_run(
            session_id=session.session_id,
            draft_id=draft.draft_id,
            draft_revision=draft.content_revision,
            frozen_digest=draft.content_digest,
            candidate_overlay_id=candidate.overlay_id,
            baseline_overlay_id=baseline_id,
            previous_overlay_id=previous_id,
            cases=cases,
            case_set_revision=(case_set.cases_revision if case_set else None),
            evaluation_suite_id=(suite.suite_id if suite else None),
            evaluation_suite_revision=(suite.suite_revision if suite else None),
            evaluation_suite_digest=(suite.suite_digest if suite else None),
            evaluation_suite_version=(suite.version if suite else None),
            model_id=model_id,
            repetitions=repetitions,
            config=run_config,
        )
        try:
            draft = self._begin_or_reuse_draft_run(draft, run.run_id)
            session = self.session_store.bind_evaluation(
                session.session_id,
                expected_session_revision=session.session_revision,
                run_id=run.run_id,
            )
            session = self._project_session(session, draft, run)
        except Exception:
            self.evaluation_store.mark_stale(
                run.run_id,
                reason=_BINDING_FAILURE_REASON,
            )
            raise
        self.executor.wake()
        return session, draft, run

    def require_run(self, run_id: str) -> SkillEvaluationRun:
        return self.evaluation_store.require_run(run_id)

    def cancel(
        self,
        run_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        expected_run_revision: int,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun]:
        session, draft, run = self._require_run_context(
            run_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_run_revision=expected_run_revision,
        )
        run = self.evaluation_store.cancel_run(
            run_id, expected_revision=expected_run_revision
        )
        if run.status in {"cancelled", "failed", "stale"}:
            draft = self._mark_outdated_if_needed(draft)
        session = self._project_session(session, draft, run)
        return session, draft, run

    def retry(
        self,
        run_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        expected_run_revision: int,
        case_ids: list[str] | None = None,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun]:
        session, draft, _ = self._require_run_context(
            run_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_run_revision=expected_run_revision,
        )
        run = self.evaluation_store.retry_run(
            run_id,
            case_ids=case_ids,
            expected_revision=expected_run_revision,
        )
        draft = self._begin_or_reuse_draft_run(draft, run.run_id)
        if session.active_evaluation_run_id != run.run_id:
            session = self.session_store.bind_evaluation(
                session.session_id,
                expected_session_revision=session.session_revision,
                run_id=run.run_id,
            )
        session = self._project_session(session, draft, run)
        self.executor.wake()
        return session, draft, run

    def save_feedback(
        self,
        run_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        expected_run_revision: int,
        expected_review_revision: int,
        feedback: str,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun]:
        session, draft, _ = self._require_run_context(
            run_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_run_revision=expected_run_revision,
        )
        run = self.evaluation_store.save_feedback(
            run_id,
            expected_revision=expected_run_revision,
            expected_feedback_revision=expected_review_revision,
            feedback=feedback,
        )
        session = self._project_session(session, draft, run)
        return session, draft, run

    async def review(
        self,
        run_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        expected_run_revision: int,
        expected_review_revision: int,
        decision: Literal["accept", "revise"],
        reason: str,
        acknowledge_failed_assertions: bool = False,
        acknowledged_regression_item_ids: list[str] | tuple[str, ...] = (),
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun]:
        session, draft, run = self._require_run_context(
            run_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_run_revision=expected_run_revision,
        )
        failed_assertions = int(
            (run.report or {}).get("candidate_assertion_failed_count") or 0
        )
        if decision == "accept" and failed_assertions:
            if not acknowledge_failed_assertions:
                raise SkillEvaluationValidationError(
                    "Accepting failed assertions requires explicit acknowledgement.",
                    code="skill_evaluation_failed_assertions_unacknowledged",
                )
            if not str(reason or "").strip():
                raise SkillEvaluationValidationError(
                    "Accepting failed assertions requires a reason.",
                    code="skill_evaluation_accept_reason_required",
                )
        if decision == "accept":
            await self._run_preflight(draft, "accept")
            session, draft, run = self._require_run_context(
                run_id,
                expected_session_revision=expected_session_revision,
                expected_revision=expected_revision,
                expected_digest=expected_digest,
                expected_run_revision=expected_run_revision,
            )
            self._require_v2_application_receipts(run)
        run = self.evaluation_store.review_run(
            run_id,
            expected_revision=expected_run_revision,
            expected_feedback_revision=expected_review_revision,
            decision=decision,
            reason=reason,
            actor_kind="local_console",
            acknowledge_failed_assertions=acknowledge_failed_assertions,
            acknowledged_regression_item_ids=acknowledged_regression_item_ids,
        )
        if decision == "accept":
            self._require_failed_assertion_acknowledgement(run)
            review = run.reviews[-1]
            draft = self.draft_store.accept_evaluation(
                draft.draft_id,
                expected_revision=draft.revision,
                expected_digest=draft.content_digest,
                run_id=run.run_id,
                decision_id=review.review_id,
                actor_id=self.actor_id,
                reason=review.reason or None,
            )
        else:
            draft = self._mark_outdated_if_needed(draft)
        session = self._project_session(session, draft, run)
        return session, draft, run

    async def waive(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        reason: str,
        confirmed: bool,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft]:
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if session.active_evaluation_run_id:
            raise SkillCreatorConflictError(
                "Cancel the active evaluation before waiving it."
            )
        if session.quality_mode != "subjective":
            raise SkillCreatorValidationError(
                "Only subjective Skills may waive automated evaluation.",
                code="skill_creator_evaluation_waiver_not_allowed",
            )
        if not confirmed:
            raise SkillCreatorValidationError(
                "Evaluation waiver requires explicit confirmation.",
                code="skill_creator_evaluation_waiver_confirmation_required",
            )
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise SkillCreatorValidationError(
                "Evaluation waiver requires a reason.",
                code="skill_creator_evaluation_waiver_reason_required",
            )
        await self._run_preflight(draft, "waive")
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        draft = self.draft_store.waive_evaluation(
            draft.draft_id,
            expected_revision=draft.revision,
            expected_digest=draft.content_digest,
            decision_id=f"skill_eval_waiver_{uuid.uuid4().hex}",
            actor_id=self.actor_id,
            reason=clean_reason,
        )
        session = self._project_session(session, draft, None, review_state="waived")
        return session, draft

    async def iterate(
        self,
        session_id: str,
        *,
        evaluation_run_id: str,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        expected_review_revision: int,
    ) -> Any:
        if self.iteration is None:
            raise SkillCreatorValidationError(
                "Creator iteration is not configured.",
                code="skill_creator_iteration_unavailable",
            )
        session, draft = self._require_context(
            session_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        run = self.evaluation_store.require_run(evaluation_run_id)
        self._assert_run_matches(session, draft, run)
        if run.review_state != "revise" or not run.reviews:
            raise SkillCreatorConflictError(
                "Only a reviewed revise decision can start a Creator iteration."
            )
        if run.feedback_revision != int(expected_review_revision):
            raise SkillEvaluationConflictError(
                "Evaluation feedback changed. Reload before iterating."
            )
        feedback = (run.reviews[-1].reason or run.reviews[-1].feedback).strip()
        if not feedback:
            raise SkillEvaluationValidationError(
                "Revision feedback is required.",
                code="skill_evaluation_feedback_required",
            )
        return await self.iteration(session, draft, run, feedback)

    async def _run_preflight(
        self, draft: WorkspaceSkillDraft, purpose: EvaluationPurpose
    ) -> SkillEvaluationPreflightResult:
        try:
            value = self.preflight(draft, purpose)
            raw = await value if inspect.isawaitable(value) else value
        except (SkillCreatorValidationError, SkillEvaluationValidationError):
            raise
        except Exception as exc:
            raise SkillEvaluationValidationError(
                "Skill evaluation preflight failed.",
                code=str(getattr(exc, "code", "skill_evaluation_preflight_failed")),
            ) from exc
        if raw is None:
            return SkillEvaluationPreflightResult()
        if isinstance(raw, SkillEvaluationPreflightResult):
            return raw
        if not isinstance(raw, Mapping):
            raise SkillEvaluationValidationError(
                "Skill evaluation preflight returned an invalid result.",
                code="skill_evaluation_preflight_invalid",
            )
        config = raw.get("config") or {}
        if not isinstance(config, Mapping):
            raise SkillEvaluationValidationError(
                "Skill evaluation preflight config is invalid.",
                code="skill_evaluation_preflight_invalid",
            )
        return SkillEvaluationPreflightResult(
            model_id=(str(raw.get("model_id") or "").strip() or None),
            config=dict(config),
        )

    def _require_context(
        self,
        session_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft]:
        session = self.session_store.require(session_id)
        if session.session_revision != int(expected_session_revision):
            raise SkillCreatorConflictError(
                "Creator session changed. Reload before continuing."
            )
        draft = self._require_session_draft(session)
        if draft.revision != int(expected_revision) or not self._same_digest(
            draft.content_digest, expected_digest
        ):
            raise SkillCreatorConflictError(
                "Creator draft changed. Reload before continuing."
            )
        if (
            session.current_revision != draft.content_revision
            or not self._same_digest(session.current_digest, draft.content_digest)
        ):
            raise SkillCreatorConflictError(
                "Creator session draft projection is stale. Reload before continuing."
            )
        return session, draft

    def _require_run_context(
        self,
        run_id: str,
        *,
        expected_session_revision: int,
        expected_revision: int,
        expected_digest: str,
        expected_run_revision: int,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun]:
        run = self.evaluation_store.require_run(run_id)
        session, draft = self._require_context(
            run.session_id,
            expected_session_revision=expected_session_revision,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        if run.revision != int(expected_run_revision):
            raise SkillEvaluationConflictError(
                "Evaluation changed. Reload before continuing."
            )
        self._assert_run_matches(session, draft, run)
        return session, draft, run

    def _assert_run_matches(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
        run: SkillEvaluationRun,
    ) -> None:
        if (
            run.session_id != session.session_id
            or run.draft_id != draft.draft_id
            or run.draft_revision != draft.content_revision
            or not self._same_digest(run.frozen_digest, draft.content_digest)
        ):
            if run.status in {"queued", "running"}:
                self.evaluation_store.mark_stale(
                    run.run_id,
                    reason="The Creator draft changed after this evaluation was frozen.",
                )
            raise SkillCreatorConflictError(
                "Evaluation no longer matches the current Creator draft."
            )

    def _require_session_draft(
        self, session: SkillCreatorSession
    ) -> WorkspaceSkillDraft:
        if not session.draft_id:
            raise SkillCreatorValidationError(
                "Create a Skill draft before designing evaluation cases.",
                code="skill_creator_draft_required",
            )
        draft = self.draft_store.require(session.draft_id)
        if draft.creator_session_id != session.session_id or not draft.quality_required:
            raise SkillCreatorConflictError(
                "Creator session references an unrelated or unguarded Skill draft."
            )
        return draft

    def _require_current_cases(self, session: SkillCreatorSession, draft: WorkspaceSkillDraft):
        if session.cases_revision < 1:
            raise SkillEvaluationValidationError(
                "Save three evaluation cases before starting.",
                code="skill_evaluation_cases_required",
            )
        case_set = self.evaluation_store.require_cases(
            session.session_id, revision=session.cases_revision
        )
        if (
            case_set.draft_id != draft.draft_id
            or case_set.draft_revision != draft.content_revision
            or not self._same_digest(case_set.content_digest, draft.content_digest)
            or case_set.quality_mode != session.quality_mode
        ):
            raise SkillEvaluationConflictError(
                "Evaluation cases are stale for the current Skill revision."
            )
        return case_set

    def _current_case_set(self, session: SkillCreatorSession, draft: WorkspaceSkillDraft):
        if session.cases_revision < 1:
            return None
        try:
            return self._require_current_cases(session, draft)
        except (SkillEvaluationConflictError, SkillEvaluationNotFoundError):
            return None

    def _recover_orphan_case_set(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, Any | None]:
        """Bind exactly one interrupted CaseSet append to its Session projection.

        A recoverable append must be the immediately following immutable revision and
        must still describe the current draft digest and quality mode.  Any gap or
        mismatched record remains fail-closed instead of guessing which cases won.
        """

        if session.active_evaluation_run_id:
            return session, draft, None
        try:
            latest = self.evaluation_store.require_cases(session.session_id)
        except SkillEvaluationNotFoundError:
            return session, draft, None
        if latest.cases_revision != session.cases_revision + 1:
            return session, draft, None
        if (
            latest.draft_id != draft.draft_id
            or latest.draft_revision != draft.content_revision
            or not self._same_digest(latest.content_digest, draft.content_digest)
            or latest.quality_mode != session.quality_mode
        ):
            return session, draft, None
        draft = self._mark_outdated_if_needed(draft)
        session = self.session_store.bind_cases(
            session.session_id,
            expected_session_revision=session.session_revision,
            cases_revision=latest.cases_revision,
            baseline_content_revision=draft.installed_content_revision,
            baseline_content_digest=draft.installed_content_digest,
        )
        session = self._project_session(session, draft, None)
        return session, draft, latest

    def _recoverable_run(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
        cases_revision: int | None,
        suite: SkillEvaluationSuite | None = None,
    ) -> SkillEvaluationRun | None:
        for run in self.evaluation_store.list_runs(
            session_id=session.session_id, limit=20
        ):
            if (
                run.draft_id == draft.draft_id
                and run.draft_revision == draft.content_revision
                and self._same_digest(run.frozen_digest, draft.content_digest)
                and (
                    (
                        suite is not None
                        and run.evaluation_suite_id == suite.suite_id
                        and run.evaluation_suite_revision == suite.suite_revision
                        and self._same_digest(
                            run.evaluation_suite_digest, suite.suite_digest
                        )
                    )
                    or (
                        suite is None
                        and run.evaluation_suite_id is None
                        and run.case_set_revision == cases_revision
                    )
                )
                and run.status in {"queued", "running"}
            ):
                return run
        return None

    def _current_v2_suite(
        self, session: SkillCreatorSession, draft: WorkspaceSkillDraft
    ) -> SkillEvaluationSuite | None:
        if self.suite_service is None or not self.suite_service.enabled:
            return None
        # Enabling Evolution V2 must not silently migrate an existing Creator
        # session. Only a session with an explicitly generated or migrated
        # suite enters the V2 evaluation contract.
        if self.suite_service.suite_store.current_for_session(session.session_id) is None:
            return None
        return self._require_v2_suite(session, draft)

    def _require_v2_suite(
        self, session: SkillCreatorSession, draft: WorkspaceSkillDraft
    ) -> SkillEvaluationSuite:
        if self.suite_service is None:
            raise SkillEvaluationValidationError(
                "Skill Creator evaluation suite is unavailable.",
                code="skill_evaluation_suite_unavailable",
            )
        return self.suite_service.require_confirmed_current(session, draft)

    def _begin_or_reuse_draft_run(
        self, draft: WorkspaceSkillDraft, run_id: str
    ) -> WorkspaceSkillDraft:
        decision = draft.quality_decision
        if (
            draft.quality_status == "running"
            and decision is not None
            and decision.run_id == run_id
            and decision.content_revision == draft.content_revision
            and self._same_digest(decision.content_digest, draft.content_digest)
        ):
            return draft
        return self.draft_store.begin_evaluation(
            draft.draft_id,
            expected_revision=draft.revision,
            expected_digest=draft.content_digest,
            run_id=run_id,
        )

    def _reconcile(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
    ) -> tuple[SkillCreatorSession, WorkspaceSkillDraft, SkillEvaluationRun | None]:
        runs = self.evaluation_store.list_runs(
            session_id=session.session_id, limit=20
        )
        current_runs: list[SkillEvaluationRun] = []
        superseded_v2_acceptance = False
        for run in runs:
            binding_current = run.case_set_revision == session.cases_revision
            if run.evaluation_suite_id is not None and self.suite_service is not None:
                try:
                    suite = self.suite_service.suite_store.current_for_session(
                        session.session_id
                    )
                    binding_current = bool(
                        suite is not None
                        and suite.state == "confirmed"
                        and suite.suite_id == run.evaluation_suite_id
                        and suite.suite_revision == run.evaluation_suite_revision
                        and suite.suite_digest == run.evaluation_suite_digest
                        and suite.session_id == session.session_id
                        and suite.draft_id == draft.draft_id
                        and suite.draft_revision == draft.content_revision
                        and self._same_digest(suite.draft_digest, draft.content_digest)
                    )
                except SkillEvaluationError:
                    binding_current = False
            if (
                run.draft_id == draft.draft_id
                and run.draft_revision == draft.content_revision
                and self._same_digest(run.frozen_digest, draft.content_digest)
                and binding_current
            ):
                current_runs.append(run)
            elif (
                run.evaluation_suite_id is not None
                and run.review_state == "accepted"
                and run.draft_id == draft.draft_id
                and run.draft_revision == draft.content_revision
                and self._same_digest(run.frozen_digest, draft.content_digest)
            ):
                superseded_v2_acceptance = True
            elif run.status in {"queued", "running"}:
                self.evaluation_store.mark_stale(
                    run.run_id,
                    reason="The Creator draft changed after this evaluation was frozen.",
                )
        discarded_unbound_orphan = any(
            self._is_unbound_orphan(run) for run in current_runs
        )
        current_runs = [
            run for run in current_runs if not self._is_unbound_orphan(run)
        ]
        run = current_runs[0] if current_runs else None
        if run is not None and run.status in {"queued", "running"}:
            draft = self._begin_or_reuse_draft_run(draft, run.run_id)
        if run is not None and run.review_state == "accepted" and run.reviews:
            self._require_failed_assertion_acknowledgement(run)
            self._require_v2_application_receipts(run)
            decision = draft.quality_decision
            if not (
                draft.quality_status == "accepted"
                and decision is not None
                and decision.run_id == run.run_id
                and self._same_digest(decision.content_digest, draft.content_digest)
            ):
                review = run.reviews[-1]
                draft = self.draft_store.accept_evaluation(
                    draft.draft_id,
                    expected_revision=draft.revision,
                    expected_digest=draft.content_digest,
                    run_id=run.run_id,
                    decision_id=review.review_id,
                    actor_id=self.actor_id,
                    reason=review.reason or None,
                )
        elif run is not None and run.review_state == "revise":
            draft = self._mark_outdated_if_needed(draft)
        elif run is not None and run.status in {"failed", "cancelled", "stale"}:
            draft = self._mark_outdated_if_needed(draft)
        elif run is None and superseded_v2_acceptance:
            draft = self._mark_outdated_if_needed(draft)
        session = self._project_session(
            session,
            draft,
            run,
            clear_latest_run=discarded_unbound_orphan and run is None,
        )
        return session, draft, run

    @staticmethod
    def _is_unbound_orphan(run: SkillEvaluationRun) -> bool:
        return bool(
            run.status == "stale"
            and run.started_at is None
            and run.error == _BINDING_FAILURE_REASON
            and all(item.status == "pending" for item in run.items)
        )

    @staticmethod
    def _require_failed_assertion_acknowledgement(
        run: SkillEvaluationRun,
    ) -> None:
        failed_assertions = int(
            aggregate_skill_evaluation_report(run).get(
                "candidate_assertion_failed_count"
            )
            or 0
        )
        if (
            failed_assertions
            and (
                not run.reviews
                or not run.reviews[-1].acknowledge_failed_assertions
            )
        ):
            raise SkillEvaluationStateError(
                "Accepted failed assertions are missing explicit acknowledgement."
            )
        report = aggregate_skill_evaluation_report(run)
        required_regressions = (
            set(report.get("regression_item_ids") or [])
            if run.config.get("regression_governance_version")
            == "skill-creator-regression-v1"
            else set()
        )
        acknowledged = (
            set(run.reviews[-1].acknowledged_regression_item_ids)
            if run.reviews
            else set()
        )
        if required_regressions != acknowledged:
            raise SkillEvaluationStateError(
                "Accepted regressions are missing item-level acknowledgement."
            )

    def _require_v2_application_receipts(self, run: SkillEvaluationRun) -> None:
        if run.evaluation_suite_id is None:
            return
        verifier = self.application_receipt_verifier
        if verifier is None:
            raise SkillEvaluationValidationError(
                "Verified Skill application receipts are unavailable.",
                code="skill_application_receipt_unavailable",
            )
        try:
            verifier(run)
        except SkillEvaluationError:
            raise
        except Exception as exc:
            raise SkillEvaluationValidationError(
                "Verified Skill application receipts are unavailable.",
                code=getattr(exc, "code", "skill_application_receipt_unavailable"),
            ) from exc

    def _project_session(
        self,
        session: SkillCreatorSession,
        draft: WorkspaceSkillDraft,
        run: SkillEvaluationRun | None,
        *,
        review_state: str | None = None,
        clear_latest_run: bool = False,
    ) -> SkillCreatorSession:
        decision = draft.quality_decision
        active_run_id = (
            run.run_id if run is not None and run.status in {"queued", "running"} else None
        )
        latest_run_id = (
            run.run_id
            if run is not None
            else None
            if clear_latest_run
            else session.latest_evaluation_run_id
        )
        projected_review = review_state
        if projected_review is None:
            if draft.quality_status == "accepted":
                projected_review = "accepted"
            elif draft.quality_status == "eval_waived":
                projected_review = "waived"
            elif run is not None:
                projected_review = run.review_state
            else:
                projected_review = "none"
        review_revision = (
            (run.feedback_revision + len(run.reviews))
            if run is not None
            else session.review_revision
        )
        return self.session_store.bind_quality_projection(
            session.session_id,
            draft_state_revision=draft.revision,
            content_revision=draft.content_revision,
            content_digest=draft.content_digest,
            quality_status=draft.quality_status,
            install_state=draft.install_state,
            active_run_id=active_run_id,
            latest_run_id=latest_run_id,
            review_state=projected_review,  # type: ignore[arg-type]
            review_revision=review_revision,
            quality_run_id=(decision.run_id if decision is not None else None),
            quality_reason=(decision.reason if decision is not None else None),
        )

    def _mark_outdated_if_needed(
        self, draft: WorkspaceSkillDraft
    ) -> WorkspaceSkillDraft:
        if draft.quality_status in {"not_evaluated", "outdated"}:
            return draft
        return self.draft_store.mark_quality_outdated(
            draft.draft_id,
            expected_revision=draft.revision,
            expected_digest=draft.content_digest,
        )

    @staticmethod
    def _same_digest(left: str | None, right: str | None) -> bool:
        return bool(left and right and left.lower() == right.lower())
