from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .creator_evaluation import (
    SkillEvaluationConflictError,
    SkillEvaluationError,
    SkillEvaluationNotFoundError,
    SkillEvaluationStateError,
    SkillEvaluationStorageError,
    SkillEvaluationValidationError,
)
from .creator_evaluation_service import SkillCreatorEvaluationService
from .creator_evaluation_suite_service import SkillCreatorEvaluationSuiteService
from .creator_evolution_service import SkillCreatorEvolutionService
from .creator_resource_build_service import SkillCreatorResourceBuildService
from .creator_resource_service import SkillCreatorResourcePlanningService
from .creator_trigger_service import SkillCreatorTriggerOptimizationService
from .creator_service import SkillCreatorService
from .creator_store import (
    CREATOR_ASSISTANT_AGENT_ID,
    SkillCreatorConflictError,
    SkillCreatorError,
    SkillCreatorNotFoundError,
    SkillCreatorSessionStore,
    SkillCreatorStorageError,
    SkillCreatorValidationError,
)
from .draft_store import (
    SkillDraftConflictError,
    SkillDraftError,
    SkillDraftNotFoundError,
    SkillDraftStorageError,
    SkillDraftValidationError,
)
from .hook_contract import (
    HOOK_MANIFEST_VERSION,
    HOOK_RESULT_VERSION,
    skill_plugin_hook_v2_enabled,
)

try:
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
except ModuleNotFoundError:
    from xpert_runtime.authoring_store import AuthoringProposalStore


router = APIRouter(prefix="/api/skills/creator", tags=["skill-creator"])
_service: SkillCreatorService | None = None
_evaluation_service: SkillCreatorEvaluationService | None = None
_evaluation_suite_service: SkillCreatorEvaluationSuiteService | None = None
_resource_planning_service: SkillCreatorResourcePlanningService | None = None
_resource_build_service: SkillCreatorResourceBuildService | None = None
_evolution_service: SkillCreatorEvolutionService | None = None
_trigger_optimization_service: SkillCreatorTriggerOptimizationService | None = None


class CreatorSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["blank", "run"] = "blank"
    intent: str = Field(default="", max_length=8_000)
    positive_examples: list[str] = Field(default_factory=list, max_length=10)
    near_miss_examples: list[str] = Field(default_factory=list, max_length=10)
    expected_output: str = Field(default="", max_length=8_000)
    success_criteria: list[str] = Field(default_factory=list, max_length=12)
    source_kind: Literal["blank", "xpert_chat", "workflow_classic"] = "blank"
    source_task_id: str | None = Field(default=None, max_length=200)
    source_run_id: str | None = Field(default=None, max_length=200)
    source_xpert_id: str | None = Field(default=None, max_length=200)
    source_conversation_id: str | None = Field(default=None, max_length=200)
    source_message_id: str | None = Field(default=None, max_length=200)


class CreatorSessionPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    intent: str | None = Field(default=None, max_length=8_000)
    positive_examples: list[str] | None = Field(default=None, max_length=10)
    near_miss_examples: list[str] | None = Field(default=None, max_length=10)
    expected_output: str | None = Field(default=None, max_length=8_000)
    success_criteria: list[str] | None = Field(default=None, max_length=12)
    quality_mode: Literal["objective", "subjective"] | None = None


class CreatorEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    preview_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    candidate_ids: list[str] = Field(default_factory=list, max_length=30)


class CreatorGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)


class CreatorResourcePlanGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    expected_plan_revision: int | None = Field(default=None, ge=1)
    expected_plan_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorResourcePlanWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=200)
    expected_session_revision: int = Field(ge=1)
    expected_plan_revision: int = Field(ge=1)
    expected_plan_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorResourcePlanAnswersRequest(CreatorResourcePlanWriteRequest):
    answers: dict[str, str] = Field(max_length=5)


class CreatorResourcePlanPatchRequest(CreatorResourcePlanWriteRequest):
    skill_name: str | None = Field(default=None, min_length=1, max_length=64)
    skill_description: str | None = Field(default=None, min_length=1, max_length=1_024)
    workflow_steps: list[dict[str, Any]] | None = Field(default=None, max_length=10)
    output_contract: list[str] | None = Field(default=None, max_length=20)
    failure_modes: list[str] | None = Field(default=None, max_length=20)
    resources: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    hooks: list[dict[str, Any]] | None = Field(default=None, max_length=12)


class CreatorTriggerCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["should_trigger", "should_not_trigger", "exact_name_smoke"]
    text: str = Field(min_length=1, max_length=500)


class CreatorTriggerSuiteGenerateRequest(CreatorResourcePlanWriteRequest):
    expected_suite_revision: int | None = Field(default=None, ge=1)
    expected_suite_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorTriggerSuitePatchRequest(CreatorTriggerSuiteGenerateRequest):
    cases: list[CreatorTriggerCaseInput] = Field(min_length=4, max_length=13)
    change_reason: str = Field(min_length=1, max_length=2_000)


class CreatorTriggerSuiteConfirmRequest(CreatorResourcePlanWriteRequest):
    suite_id: str = Field(min_length=1, max_length=200)
    expected_suite_revision: int = Field(ge=1)
    expected_suite_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorTriggerDescriptionRequest(CreatorTriggerSuiteConfirmRequest):
    pass


class CreatorTriggerDescriptionEvaluateRequest(CreatorTriggerDescriptionRequest):
    description: str = Field(min_length=1, max_length=600)


class CreatorTriggerDescriptionConfirmRequest(CreatorTriggerDescriptionRequest):
    expected_attempt_revision: int = Field(ge=1)
    expected_attempt_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    selected_description_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorResourceBuildStartRequest(CreatorResourcePlanWriteRequest):
    pass


class CreatorResourceBuildMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    expected_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorResourceReviewRequest(CreatorResourceBuildMutationRequest):
    decision: Literal["accept", "revise"]
    feedback: str = Field(default="", max_length=4_000)


class CreatorResourceEditRequest(CreatorResourceBuildMutationRequest):
    content: str = Field(min_length=1, max_length=24 * 1024)


class CreatorResourceFinalizeRequest(CreatorResourceBuildMutationRequest):
    decision: Literal["accept", "revise"]
    feedback: str = Field(default="", max_length=4_000)


class CreatorBlankDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    skill_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    description: str = Field(min_length=1, max_length=1_024)


class CreatorDraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    expected_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1_024)
    skill_markdown: str | None = Field(default=None, max_length=1_048_576)
    files: dict[str, str] | None = None


class CreatorEvaluationCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=20_000)
    expected_behavior: str = Field(min_length=1, max_length=4_000)
    fixtures: list[dict[str, str]] = Field(default_factory=list, max_length=10)
    assertions: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class CreatorEvaluationWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    expected_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorEvaluationCasesRequest(CreatorEvaluationWriteRequest):
    quality_mode: Literal["objective", "subjective"] = "objective"
    cases: list[CreatorEvaluationCaseInput] = Field(max_length=3)


class CreatorEvaluationStartRequest(CreatorEvaluationWriteRequest):
    repetitions: int = Field(default=1, ge=1, le=3)
    evaluation_suite_revision: int | None = Field(default=None, ge=1)
    evaluation_suite_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorEvaluationSuiteCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=200)
    role: Literal["normal", "ambiguous", "boundary", "regression"]
    name: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=20_000)
    expected_behavior: str = Field(min_length=1, max_length=4_000)
    fixtures: list[dict[str, str]] = Field(default_factory=list, max_length=10)
    assertions: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    requirement_ids: list[str] = Field(default_factory=list, max_length=64)
    required_resource_paths: list[str] = Field(default_factory=list, max_length=20)
    workflow_step_ids: list[str] = Field(default_factory=list, max_length=64)


class CreatorEvaluationSuiteGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_session_revision: int = Field(ge=1)
    expected_draft_state_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    expected_draft_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    expected_suite_revision: int | None = Field(default=None, ge=1)
    expected_suite_digest: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorEvaluationSuitePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str = Field(min_length=1, max_length=240)
    expected_session_revision: int = Field(ge=1)
    expected_draft_state_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    expected_draft_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    expected_suite_revision: int = Field(ge=1)
    expected_suite_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    cases: list[CreatorEvaluationSuiteCaseInput] = Field(min_length=3, max_length=12)
    change_reason: str = Field(default="", max_length=4_000)


class CreatorEvaluationSuiteConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_id: str = Field(min_length=1, max_length=240)
    expected_session_revision: int = Field(ge=1)
    expected_draft_state_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    expected_draft_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )
    expected_suite_revision: int = Field(ge=1)
    expected_suite_digest: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$"
    )


class CreatorEvaluationRunMutationRequest(CreatorEvaluationWriteRequest):
    expected_run_revision: int = Field(ge=1)


class CreatorEvaluationRetryRequest(CreatorEvaluationRunMutationRequest):
    case_ids: list[str] | None = Field(default=None, max_length=3)


class CreatorEvaluationFeedbackRequest(CreatorEvaluationRunMutationRequest):
    expected_review_revision: int = Field(ge=0)
    feedback: str = Field(default="", max_length=4_000)


class CreatorEvaluationReviewRequest(CreatorEvaluationRunMutationRequest):
    expected_review_revision: int = Field(ge=0)
    decision: Literal["accept", "revise"]
    reason: str = Field(default="", max_length=4_000)
    acknowledge_failed_assertions: bool = False
    acknowledged_regression_item_ids: list[str] = Field(
        default_factory=list, max_length=36
    )


class CreatorEvaluationWaiveRequest(CreatorEvaluationWriteRequest):
    reason: str = Field(min_length=1, max_length=4_000)
    confirmed: bool = False


class CreatorEvaluationIterateRequest(CreatorEvaluationWriteRequest):
    evaluation_run_id: str = Field(min_length=1, max_length=200)
    expected_review_revision: int = Field(ge=0)


class CreatorEvolutionGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_run_id: str = Field(min_length=1, max_length=200)
    expected_session_revision: int = Field(ge=1)
    expected_draft_state_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    expected_draft_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    expected_review_revision: int = Field(ge=1)
    expected_run_revision: int = Field(ge=1)
    expected_resource_plan_revision: int = Field(ge=1)
    expected_resource_plan_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    expected_evolution_revision: int | None = Field(default=None, ge=1)
    expected_evolution_digest: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")


class CreatorEvolutionWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=200)
    expected_session_revision: int = Field(ge=1)
    expected_draft_state_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    expected_draft_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    expected_plan_revision: int = Field(ge=1)
    expected_plan_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")


class CreatorEvolutionAnswersRequest(CreatorEvolutionWriteRequest):
    answers: dict[str, str] = Field(max_length=5)


class CreatorEvolutionPatchRequest(CreatorEvolutionWriteRequest):
    changes: dict[str, Any]


def configure_skill_creator(service: SkillCreatorService | None) -> None:
    global _service
    global _evaluation_service
    global _evaluation_suite_service
    global _resource_planning_service
    global _resource_build_service
    global _evolution_service
    global _trigger_optimization_service
    if service is not _service:
        if (
            _evaluation_service is not None
            and getattr(_evaluation_service, "creator_service", None) is not service
        ):
            _evaluation_service = None
        if (
            _evaluation_suite_service is not None
            and getattr(_evaluation_suite_service, "creator_service", None) is not service
        ):
            _evaluation_suite_service = None
        if (
            _resource_planning_service is not None
            and getattr(_resource_planning_service, "creator_service", None) is not service
        ):
            _resource_planning_service = None
        if (
            _resource_build_service is not None
            and getattr(_resource_build_service, "creator_service", None) is not service
        ):
            _resource_build_service = None
        if (
            _evolution_service is not None
            and getattr(_evolution_service, "creator_service", None) is not service
        ):
            _evolution_service = None
        if (
            _trigger_optimization_service is not None
            and getattr(_trigger_optimization_service, "creator_service", None) is not service
        ):
            _trigger_optimization_service = None
    _service = service


def configure_skill_creator_evaluation(
    service: SkillCreatorEvaluationService | None,
) -> None:
    global _evaluation_service
    _evaluation_service = service


def configure_skill_creator_evaluation_suite(
    service: SkillCreatorEvaluationSuiteService | None,
) -> None:
    global _evaluation_suite_service
    _evaluation_suite_service = service


def configure_skill_creator_evolution(service: SkillCreatorEvolutionService | None) -> None:
    global _evolution_service
    _evolution_service = service


def configure_skill_creator_resource_planning(
    service: SkillCreatorResourcePlanningService | None,
) -> None:
    global _resource_planning_service
    _resource_planning_service = service


def configure_skill_creator_resource_build(
    service: SkillCreatorResourceBuildService | None,
) -> None:
    global _resource_build_service
    _resource_build_service = service


def configure_skill_creator_trigger_optimization(
    service: SkillCreatorTriggerOptimizationService | None,
) -> None:
    global _trigger_optimization_service
    _trigger_optimization_service = service


def get_skill_creator_service() -> SkillCreatorService:
    if _service is None:
        raise SkillCreatorValidationError(
            "Skill Creator V2 is disabled.", code="skill_creator_disabled"
        )
    _service.require_enabled()
    return _service


def get_skill_creator_evaluation_service() -> SkillCreatorEvaluationService:
    get_skill_creator_service()
    if _evaluation_service is None:
        raise SkillCreatorValidationError(
            "Skill Creator evaluation is unavailable.",
            code="skill_creator_evaluation_unavailable",
        )
    return _evaluation_service


def get_skill_creator_evaluation_suite_service() -> SkillCreatorEvaluationSuiteService:
    get_skill_creator_service()
    if _evaluation_suite_service is None:
        raise SkillCreatorValidationError(
            "Skill Creator evaluation suite is unavailable.",
            code="skill_creator_evolution_v2_disabled",
        )
    _evaluation_suite_service.require_enabled()
    return _evaluation_suite_service


def get_skill_creator_resource_planning_service() -> SkillCreatorResourcePlanningService:
    get_skill_creator_service()
    if _resource_planning_service is None:
        raise SkillCreatorValidationError(
            "Skill Creator resource authoring is unavailable.",
            code="skill_creator_resource_authoring_disabled",
        )
    _resource_planning_service.require_enabled()
    return _resource_planning_service


def get_skill_creator_resource_build_service() -> SkillCreatorResourceBuildService:
    get_skill_creator_resource_planning_service()
    if _resource_build_service is None:
        raise SkillCreatorValidationError(
            "Skill Creator resource build is unavailable.",
            code="skill_creator_resource_authoring_disabled",
        )
    _resource_build_service.require_enabled()
    return _resource_build_service


def get_skill_creator_evolution_service() -> SkillCreatorEvolutionService:
    get_skill_creator_service()
    if _evolution_service is None:
        raise SkillCreatorValidationError(
            "Skill Creator evolution planning is unavailable.",
            code="skill_creator_evolution_v2_disabled",
        )
    _evolution_service.require_enabled()
    return _evolution_service


def get_skill_creator_trigger_optimization_service() -> SkillCreatorTriggerOptimizationService:
    get_skill_creator_resource_planning_service()
    if _trigger_optimization_service is None:
        raise SkillCreatorValidationError(
            "Skill trigger optimization is unavailable.",
            code="skill_trigger_gate_required",
        )
    _trigger_optimization_service.require_enabled()
    return _trigger_optimization_service


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (SkillCreatorNotFoundError, SkillDraftNotFoundError, SkillEvaluationNotFoundError),
    ):
        status = 404
        code = str(getattr(exc, "code", "skill_creator_not_found"))
    elif isinstance(
        exc,
        (
            SkillCreatorConflictError,
            SkillDraftConflictError,
            SkillEvaluationConflictError,
            SkillEvaluationStateError,
        ),
    ):
        status = 409
        code = str(
            getattr(
                exc,
                "code",
                "skill_evaluation_conflict"
                if isinstance(exc, (SkillEvaluationConflictError, SkillEvaluationStateError))
                else "skill_creator_conflict",
            )
        )
    elif isinstance(
        exc,
        (SkillCreatorStorageError, SkillDraftStorageError, SkillEvaluationStorageError),
    ):
        status = 503
        code = str(
            getattr(
                exc,
                "code",
                "skill_evaluation_storage_unavailable"
                if isinstance(exc, SkillEvaluationStorageError)
                else "skill_creator_storage_unavailable",
            )
        )
    elif isinstance(exc, SkillEvaluationValidationError):
        status = (
            503
            if exc.code
            in {
                "model_gateway_unconfigured",
                "skill_evaluation_sidecar_unavailable",
                "skill_evaluation_preflight_failed",
                "skill_application_receipt_unavailable",
                "skill_application_receipt_store_corrupt",
            }
            else 400
        )
        code = exc.code
        if code in {
            "skill_application_receipt_missing",
            "skill_application_receipt_incomplete",
            "skill_application_receipt_mismatch",
        }:
            status = 409
    elif isinstance(exc, SkillCreatorValidationError):
        code = exc.code
        status = 404 if code in {
            "skill_creator_disabled",
            "skill_creator_resource_authoring_disabled",
            "skill_creator_evolution_v2_disabled",
        } else 400
        if code == "model_gateway_unconfigured":
            status = 503
        elif code in {
            "skill_trigger_optimizer_unconfigured",
            "skill_trigger_index_unavailable",
        }:
            status = 503
        elif code in {
            "skill_creator_sandbox_unavailable",
            "skill_creator_sandbox_profile_invalid",
        }:
            status = 503
        elif code == "skill_creator_source_unavailable":
            status = 501
        elif code in {
            "skill_creator_generation_failed",
            "skill_creator_generation_invalid",
            "skill_creator_tool_not_called",
            "skill_creator_resource_planner_failed",
            "skill_creator_resource_planner_invalid",
            "skill_creator_resource_builder_failed",
            "skill_creator_resource_builder_invalid",
            "skill_evaluation_suite_generator_failed",
            "skill_evaluation_suite_generator_invalid",
            "skill_creator_evolution_planner_failed",
            "skill_creator_evolution_planner_invalid",
            "skill_trigger_optimizer_invalid",
        }:
            status = 502
        elif code == "skill_creator_evolution_plan_required":
            status = 409
        elif code == "skill_creator_proposal_binding_invalid":
            status = 409
    elif isinstance(exc, SkillDraftValidationError):
        status = 400
        code = "skill_package_invalid"
    else:
        status = 500
        code = "skill_creator_error"
    issues = list(getattr(exc, "issues", []) or [])[:20]
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": str(exc)[:500], "issues": issues},
    )


def _session_response(
    service: SkillCreatorService,
    session,
    draft=None,
    *,
    case_set: dict[str, Any] | None = None,
    evaluation_run=None,
):
    current_resource_plan = (
        _resource_planning_service.plan_store.current_for_session(session.session_id)
        if _resource_planning_service is not None
        and _resource_planning_service.enabled
        else None
    )
    if (
        case_set is None
        and _evaluation_service is not None
        and int(getattr(session, "cases_revision", 0) or 0) > 0
    ):
        try:
            stored = _evaluation_service.evaluation_store.require_cases(
                session.session_id, revision=session.cases_revision
            )
            case_set = _evaluation_service.evaluation_store.serialize(stored)
        except SkillEvaluationError:
            case_set = None
    response = {
        "version": service.VERSION,
        "session": SkillCreatorSessionStore.serialize(session),
        "draft": service.serialize_draft(draft) if draft is not None else None,
        "cases": list((case_set or {}).get("cases") or []),
        "cases_revision": int((case_set or {}).get("cases_revision") or 0),
        "evaluation_run": (
            _evaluation_service.evaluation_store.serialize(evaluation_run)
            if _evaluation_service is not None and evaluation_run is not None
            else None
        ),
        "resource_plan": (
            _resource_planning_service.serialize_projection(
                current_resource_plan,
                session=session,
                draft=draft,
            )
            if _resource_planning_service is not None
            and _resource_planning_service.enabled
            else None
        ),
        "resource_build": (
            _resource_build_service.current_projection(session.session_id)
            if _resource_build_service is not None
            and _resource_build_service.enabled
            else None
        ),
        "evaluation_suite": (
            _evaluation_suite_service.current_projection(session.session_id)
            if _evaluation_suite_service is not None
            and _evaluation_suite_service.enabled
            and draft is not None
            else None
        ),
        "evolution_plan": _evolution_projection(session.session_id),
        "regression_governance": (
            _evaluation_service.governance_projection(session, draft)
            if _evaluation_service is not None and draft is not None
            else None
        ),
    }
    if _trigger_optimization_service is not None:
        try:
            response.update(
                _trigger_optimization_service.projection(
                    session,
                    current_resource_plan,
                )
            )
        except SkillCreatorStorageError:
            response.update(
                {
                    "trigger_required": False,
                    "trigger_suite": None,
                    "trigger_attempt": None,
                    "trigger_receipt": None,
                    "trigger_stale_reason": "skill_trigger_index_unavailable",
                }
            )
    else:
        response.update(
            {
                "trigger_required": False,
                "trigger_suite": None,
                "trigger_attempt": None,
                "trigger_receipt": None,
                "trigger_stale_reason": None,
            }
        )
    return response


def _evolution_projection(session_id: str) -> dict[str, Any] | None:
    if _evolution_service is None or not _evolution_service.enabled:
        return None
    try:
        return _evolution_service.current_projection(session_id)
    except SkillCreatorStorageError:
        return {
            "available": False,
            "code": "skill_creator_storage_unavailable",
        }


@router.get("/status")
async def get_creator_status():
    if _service is None:
        return {
            "version": SkillCreatorService.VERSION,
            "enabled": False,
            "assistant_agent_id": CREATOR_ASSISTANT_AGENT_ID,
            "model_available": False,
            "supported_sources": ["blank"],
            "quality_gate": "not_evaluated",
            "evaluation_available": False,
            "evaluation_version": None,
            "resource_authoring_enabled": False,
            "resource_authoring_version": None,
            "resource_planner_available": False,
            "resource_build_enabled": False,
            "resource_build_version": None,
            "resource_builder_available": False,
            "script_sandbox_configured": False,
            "evaluation_suite_enabled": False,
            "evaluation_suite_version": None,
            "evaluation_suite_generator_available": False,
            "evaluation_suite_store_available": False,
            "evolution_enabled": False,
            "evolution_version": None,
            "evolution_planner_available": False,
            "evolution_store_available": False,
            "hook_authoring_enabled": False,
            "hook_manifest_version": HOOK_MANIFEST_VERSION,
            "hook_result_version": HOOK_RESULT_VERSION,
            "hook_runtimes": ["python", "javascript"],
            "trigger_optimization_enabled": False,
            "trigger_optimization_version": None,
            "trigger_optimizer_available": False,
            "trigger_store_available": False,
        }
    status = _service.status()
    status["evaluation_available"] = _evaluation_service is not None
    status["evaluation_version"] = (
        _evaluation_service.VERSION if _evaluation_service is not None else None
    )
    status["quality_gate"] = (
        "evaluation_required" if _evaluation_service is not None else "not_evaluated"
    )
    status.update(
        _resource_planning_service.status()
        if _resource_planning_service is not None
        else {
            "resource_authoring_enabled": False,
            "resource_authoring_version": None,
            "resource_planner_available": False,
        }
    )
    status.update(
        _evolution_service.status()
        if _evolution_service is not None
        else {
            "evolution_enabled": False,
            "evolution_version": None,
            "evolution_planner_available": False,
            "evolution_store_available": False,
        }
    )
    status.update(
        _resource_build_service.status()
        if _resource_build_service is not None
        else {
            "resource_build_enabled": False,
            "resource_build_version": None,
            "resource_builder_available": False,
            "script_sandbox_configured": False,
        }
    )
    status.update(
        _evaluation_suite_service.status()
        if _evaluation_suite_service is not None
        else {
            "evaluation_suite_enabled": False,
            "evaluation_suite_version": None,
            "evaluation_suite_generator_available": False,
            "evaluation_suite_store_available": False,
        }
    )
    status.update(
        {
            "hook_authoring_enabled": bool(
                status.get("enabled")
                and status.get("resource_authoring_enabled")
                and skill_plugin_hook_v2_enabled()
            ),
            "hook_manifest_version": HOOK_MANIFEST_VERSION,
            "hook_result_version": HOOK_RESULT_VERSION,
            "hook_runtimes": ["python", "javascript"],
        }
    )
    trigger_status = (
        _trigger_optimization_service.status()
        if _trigger_optimization_service is not None
        else None
    )
    status.update(
        {
            "trigger_optimization_enabled": bool(
                trigger_status and trigger_status.get("enabled")
            ),
            "trigger_optimization_version": (
                trigger_status.get("version") if trigger_status else None
            ),
            "trigger_optimizer_available": bool(
                trigger_status and trigger_status.get("model_available")
            ),
            "trigger_store_available": bool(
                trigger_status
                and trigger_status.get("trigger_store", {}).get("available")
                and trigger_status.get("optimization_store", {}).get("available")
            ),
        }
    )
    return status


@router.get("/sessions")
async def list_creator_sessions(limit: int = Query(default=100, ge=1, le=500)):
    try:
        service = get_skill_creator_service()
        items = await asyncio.to_thread(service.list_sessions, limit=limit)
        return {
            "version": service.VERSION,
            "items": [SkillCreatorSessionStore.serialize(item) for item in items],
            "total": len(items),
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions", status_code=201)
async def create_creator_session(payload: CreatorSessionCreateRequest):
    try:
        service = get_skill_creator_service()
        values = payload.model_dump(mode="python")
        values["authoring_flow"] = (
            "resource"
            if _resource_planning_service is not None
            and _resource_planning_service.enabled
            else "legacy"
        )
        session = await asyncio.to_thread(
            service.create_session, **values
        )
        return _session_response(service, session)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.get("/sessions/{session_id}")
async def get_creator_session(session_id: str):
    try:
        service = get_skill_creator_service()
        session, draft = await asyncio.to_thread(service.get_session, session_id)
        case_set = None
        evaluation_run = None
        if _evaluation_service is not None and draft is not None:
            session, draft, case_set, evaluation_run = await asyncio.to_thread(
                _evaluation_service.get_projection, session_id
            )
        return _session_response(
            service,
            session,
            draft,
            case_set=case_set,
            evaluation_run=evaluation_run,
        )
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.patch("/sessions/{session_id}")
async def patch_creator_session(
    session_id: str, payload: CreatorSessionPatchRequest
):
    try:
        service = get_skill_creator_service()
        changes = {
            name: getattr(payload, name)
            for name in (
                "intent",
                "positive_examples",
                "near_miss_examples",
                "expected_output",
                "success_criteria",
            )
            if name in payload.model_fields_set
        }
        session = None
        draft = None
        expected_session_revision = payload.expected_session_revision
        if changes:
            session = await asyncio.to_thread(
                service.update_definition,
                session_id,
                expected_session_revision=expected_session_revision,
                changes=changes,
            )
            expected_session_revision = session.session_revision
        if "quality_mode" in payload.model_fields_set:
            evaluation = get_skill_creator_evaluation_service()
            session, draft = await asyncio.to_thread(
                evaluation.set_quality_mode,
                session_id,
                expected_session_revision=expected_session_revision,
                quality_mode=payload.quality_mode,
            )
        elif session is None:
            session, draft = await asyncio.to_thread(service.get_session, session_id)
        return _session_response(service, session, draft)
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/source-preview")
async def preview_creator_source(session_id: str):
    try:
        service = get_skill_creator_service()
        session, _ = await asyncio.to_thread(service.get_session, session_id)
        preview = await asyncio.to_thread(service.preview_source, session_id)
        return {
            "version": service.VERSION,
            "preview_fingerprint": preview.fingerprint,
            "source_kind": session.source_kind,
            "source_task_id": session.source_task_id,
            "source_run_id": session.source_run_id,
            "candidates": [
                {
                    "candidate_id": str(item.get("candidate_id") or ""),
                    "kind": str(item.get("kind") or ""),
                    "title": str(item.get("title") or ""),
                    "summary": str(item.get("summary") or ""),
                    "content_hash": str(item.get("content_hash") or ""),
                    "default_selected": bool(item.get("default_selected", False)),
                }
                for item in preview.candidates
            ],
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.put("/sessions/{session_id}/evidence")
async def put_creator_evidence(session_id: str, payload: CreatorEvidenceRequest):
    try:
        service = get_skill_creator_service()
        session = await asyncio.to_thread(
            service.select_evidence,
            session_id,
            expected_session_revision=payload.expected_session_revision,
            preview_fingerprint=payload.preview_fingerprint.lower(),
            candidate_ids=payload.candidate_ids,
        )
        return _session_response(service, session)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/resource-plan/generate")
async def generate_creator_resource_plan(
    session_id: str, payload: CreatorResourcePlanGenerateRequest
):
    try:
        if (payload.expected_plan_revision is None) != (
            payload.expected_plan_digest is None
        ):
            raise SkillCreatorValidationError(
                "Expected resource plan revision and digest must be provided together.",
                code="skill_creator_resource_plan_invalid",
            )
        planning = get_skill_creator_resource_planning_service()
        plan = await planning.generate(
            session_id,
            expected_session_revision=payload.expected_session_revision,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=(
                payload.expected_plan_digest.lower()
                if payload.expected_plan_digest
                else None
            ),
        )
        service = get_skill_creator_service()
        session, draft = await asyncio.to_thread(service.get_session, session_id)
        response = _session_response(service, session, draft)
        response["resource_plan"] = planning.serialize_projection(
            plan, session=session, draft=draft
        )
        return response
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.put("/sessions/{session_id}/resource-plan/answers")
async def answer_creator_resource_plan(
    session_id: str, payload: CreatorResourcePlanAnswersRequest
):
    try:
        planning = get_skill_creator_resource_planning_service()
        plan = await asyncio.to_thread(
            planning.save_answers,
            session_id,
            plan_id=payload.plan_id,
            expected_session_revision=payload.expected_session_revision,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            answers=payload.answers,
        )
        service = get_skill_creator_service()
        session, draft = await asyncio.to_thread(service.get_session, session_id)
        response = _session_response(service, session, draft)
        response["resource_plan"] = planning.serialize_projection(
            plan, session=session, draft=draft
        )
        return response
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.patch("/sessions/{session_id}/resource-plan")
async def patch_creator_resource_plan(
    session_id: str, payload: CreatorResourcePlanPatchRequest
):
    try:
        planning = get_skill_creator_resource_planning_service()
        changes = {
            name: getattr(payload, name)
            for name in (
                "skill_name",
                "skill_description",
                "workflow_steps",
                "output_contract",
                "failure_modes",
                "resources",
                "hooks",
            )
            if name in payload.model_fields_set
        }
        if not changes:
            raise SkillCreatorValidationError(
                "Resource plan patch contains no changes.",
                code="skill_creator_resource_plan_invalid",
            )
        plan = await asyncio.to_thread(
            planning.patch,
            session_id,
            plan_id=payload.plan_id,
            expected_session_revision=payload.expected_session_revision,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            changes=changes,
        )
        service = get_skill_creator_service()
        session, draft = await asyncio.to_thread(service.get_session, session_id)
        response = _session_response(service, session, draft)
        response["resource_plan"] = planning.serialize_projection(
            plan, session=session, draft=draft
        )
        return response
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/resource-plan/confirm")
async def confirm_creator_resource_plan(
    session_id: str, payload: CreatorResourcePlanWriteRequest
):
    try:
        planning = get_skill_creator_resource_planning_service()
        plan = await asyncio.to_thread(
            planning.confirm,
            session_id,
            plan_id=payload.plan_id,
            expected_session_revision=payload.expected_session_revision,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
        )
        service = get_skill_creator_service()
        session, draft = await asyncio.to_thread(service.get_session, session_id)
        response = _session_response(service, session, draft)
        response["resource_plan"] = planning.serialize_projection(
            plan, session=session, draft=draft
        )
        return response
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/trigger-suite/generate")
async def generate_creator_trigger_suite(
    session_id: str, payload: CreatorTriggerSuiteGenerateRequest
):
    try:
        if (payload.expected_suite_revision is None) != (
            payload.expected_suite_digest is None
        ):
            raise SkillCreatorValidationError(
                "Expected trigger suite revision and digest must be provided together.",
                code="skill_trigger_suite_invalid",
            )
        trigger = get_skill_creator_trigger_optimization_service()
        await trigger.generate_suite(
            session_id,
            expected_session_revision=payload.expected_session_revision,
            plan_id=payload.plan_id,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=(
                payload.expected_suite_digest.lower()
                if payload.expected_suite_digest
                else None
            ),
        )
        creator = get_skill_creator_service()
        session, draft = await asyncio.to_thread(creator.get_session, session_id)
        return _session_response(creator, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.patch("/sessions/{session_id}/trigger-suite")
async def patch_creator_trigger_suite(
    session_id: str, payload: CreatorTriggerSuitePatchRequest
):
    try:
        if (payload.expected_suite_revision is None) != (
            payload.expected_suite_digest is None
        ):
            raise SkillCreatorValidationError(
                "Expected trigger suite revision and digest must be provided together.",
                code="skill_trigger_suite_invalid",
            )
        trigger = get_skill_creator_trigger_optimization_service()
        await asyncio.to_thread(
            trigger.save_suite,
            session_id,
            expected_session_revision=payload.expected_session_revision,
            plan_id=payload.plan_id,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            cases=[
                {"kind": item.kind, "text": item.text, "source": "user"}
                for item in payload.cases
            ],
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=(
                payload.expected_suite_digest.lower()
                if payload.expected_suite_digest
                else None
            ),
            change_reason=payload.change_reason,
        )
        creator = get_skill_creator_service()
        session, draft = await asyncio.to_thread(creator.get_session, session_id)
        return _session_response(creator, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/trigger-suite/confirm")
async def confirm_creator_trigger_suite(
    session_id: str, payload: CreatorTriggerSuiteConfirmRequest
):
    try:
        trigger = get_skill_creator_trigger_optimization_service()
        await asyncio.to_thread(
            trigger.confirm_suite,
            session_id,
            suite_id=payload.suite_id,
            expected_session_revision=payload.expected_session_revision,
            plan_id=payload.plan_id,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=payload.expected_suite_digest.lower(),
        )
        creator = get_skill_creator_service()
        session, draft = await asyncio.to_thread(creator.get_session, session_id)
        return _session_response(creator, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/trigger-descriptions/optimize")
async def optimize_creator_trigger_descriptions(
    session_id: str, payload: CreatorTriggerDescriptionRequest
):
    try:
        trigger = get_skill_creator_trigger_optimization_service()
        await trigger.optimize(
            session_id,
            expected_session_revision=payload.expected_session_revision,
            plan_id=payload.plan_id,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=payload.expected_suite_digest.lower(),
        )
        creator = get_skill_creator_service()
        session, draft = await asyncio.to_thread(creator.get_session, session_id)
        return _session_response(creator, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/trigger-descriptions/evaluate")
async def evaluate_creator_trigger_description(
    session_id: str, payload: CreatorTriggerDescriptionEvaluateRequest
):
    try:
        trigger = get_skill_creator_trigger_optimization_service()
        await asyncio.to_thread(
            trigger.evaluate_description,
            session_id,
            description=payload.description,
            expected_session_revision=payload.expected_session_revision,
            plan_id=payload.plan_id,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=payload.expected_suite_digest.lower(),
        )
        creator = get_skill_creator_service()
        session, draft = await asyncio.to_thread(creator.get_session, session_id)
        return _session_response(creator, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/trigger-descriptions/{attempt_id}/confirm")
async def confirm_creator_trigger_description(
    session_id: str,
    attempt_id: str,
    payload: CreatorTriggerDescriptionConfirmRequest,
):
    try:
        trigger = get_skill_creator_trigger_optimization_service()
        await asyncio.to_thread(
            trigger.confirm_description,
            session_id,
            attempt_id=attempt_id,
            selected_description_digest=payload.selected_description_digest.lower(),
            expected_attempt_revision=payload.expected_attempt_revision,
            expected_attempt_digest=payload.expected_attempt_digest.lower(),
            expected_session_revision=payload.expected_session_revision,
            plan_id=payload.plan_id,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=payload.expected_suite_digest.lower(),
        )
        creator = get_skill_creator_service()
        session, draft = await asyncio.to_thread(creator.get_session, session_id)
        return _session_response(creator, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/resource-build", status_code=201)
async def start_creator_resource_build(
    session_id: str, payload: CreatorResourceBuildStartRequest
):
    try:
        build_service = get_skill_creator_resource_build_service()
        build = await build_service.start(
            session_id,
            plan_id=payload.plan_id,
            expected_session_revision=payload.expected_session_revision,
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
        )
        return {
            "version": build_service.VERSION,
            "resource_build": build_service.build_store.serialize(build),
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.get("/resource-builds/{build_id}")
async def get_creator_resource_build(build_id: str):
    try:
        build_service = get_skill_creator_resource_build_service()
        build = await asyncio.to_thread(build_service.build_store.require, build_id)
        projection = build_service.current_projection(build.session_id)
        if projection is None or projection.get("build_id") != build_id:
            raise SkillCreatorConflictError("Resource build is no longer current for this session.")
        return {"version": build_service.VERSION, "resource_build": projection}
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/resource-builds/{build_id}/next")
async def advance_creator_resource_build(
    build_id: str, payload: CreatorResourceBuildMutationRequest
):
    try:
        build_service = get_skill_creator_resource_build_service()
        build = await build_service.next(
            build_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
        )
        return {
            "version": build_service.VERSION,
            "resource_build": build_service.build_store.serialize(build),
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/resource-builds/{build_id}/resources/{resource_id}/review")
async def review_creator_resource(
    build_id: str,
    resource_id: str,
    payload: CreatorResourceReviewRequest,
):
    try:
        build_service = get_skill_creator_resource_build_service()
        build = await asyncio.to_thread(
            build_service.review_resource,
            build_id,
            resource_id=resource_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            decision=payload.decision,
            feedback=payload.feedback,
        )
        return {
            "version": build_service.VERSION,
            "resource_build": build_service.build_store.serialize(build),
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.put("/resource-builds/{build_id}/resources/{resource_id}")
async def edit_creator_resource(
    build_id: str,
    resource_id: str,
    payload: CreatorResourceEditRequest,
):
    try:
        build_service = get_skill_creator_resource_build_service()
        build = await build_service.edit_resource(
            build_id,
            resource_id=resource_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            content=payload.content,
        )
        return {
            "version": build_service.VERSION,
            "resource_build": build_service.build_store.serialize(build),
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/resource-builds/{build_id}/finalize")
async def finalize_creator_resource_build(
    build_id: str, payload: CreatorResourceFinalizeRequest
):
    try:
        build_service = get_skill_creator_resource_build_service()
        build, proposal = await asyncio.to_thread(
            build_service.finalize,
            build_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            decision=payload.decision,
            feedback=payload.feedback,
        )
        return {
            "version": build_service.VERSION,
            "resource_build": build_service.build_store.serialize(build),
            "proposal": (
                AuthoringProposalStore.serialize(proposal, include_payload=True)
                if proposal is not None
                else None
            ),
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/generate")
async def generate_creator_proposal(
    session_id: str, payload: CreatorGenerateRequest
):
    try:
        service = get_skill_creator_service()
        proposal = await service.generate(
            session_id,
            expected_session_revision=payload.expected_session_revision,
        )
        return {
            "version": service.VERSION,
            "proposal": AuthoringProposalStore.serialize(
                proposal, include_payload=True
            ),
        }
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/draft", status_code=201)
async def create_creator_blank_draft(
    session_id: str, payload: CreatorBlankDraftRequest
):
    try:
        service = get_skill_creator_service()
        session, draft = await asyncio.to_thread(
            service.create_blank_draft,
            session_id,
            expected_session_revision=payload.expected_session_revision,
            skill_id=payload.skill_id,
            description=payload.description,
        )
        return _session_response(service, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.put("/sessions/{session_id}/draft")
async def update_creator_draft(
    session_id: str, payload: CreatorDraftUpdateRequest
):
    try:
        service = get_skill_creator_service()
        changes: dict[str, Any] = {
            name: getattr(payload, name)
            for name in (
                "name",
                "slug",
                "description",
                "skill_markdown",
                "files",
            )
            if name in payload.model_fields_set
        }
        session, draft = await asyncio.to_thread(
            service.update_draft,
            session_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            changes=changes,
        )
        return _session_response(service, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.put("/sessions/{session_id}/cases")
async def put_creator_evaluation_cases(
    session_id: str, payload: CreatorEvaluationCasesRequest
):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft, case_set = await asyncio.to_thread(
            evaluation.save_cases,
            session_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            quality_mode=payload.quality_mode,
            cases=[item.model_dump(mode="python") for item in payload.cases],
        )
        return _session_response(
            creator, session, draft, case_set=case_set, evaluation_run=None
        )
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/evaluation-suite/generate")
async def generate_creator_evaluation_suite(
    session_id: str, payload: CreatorEvaluationSuiteGenerateRequest
):
    try:
        creator = get_skill_creator_service()
        suites = get_skill_creator_evaluation_suite_service()
        await suites.generate(
            session_id,
            expected_session_revision=payload.expected_session_revision,
            expected_draft_state_revision=payload.expected_draft_state_revision,
            expected_draft_revision=payload.expected_draft_revision,
            expected_draft_digest=payload.expected_draft_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=(
                payload.expected_suite_digest.lower()
                if payload.expected_suite_digest is not None
                else None
            ),
        )
        session, draft = creator.get_session(session_id)
        response = _session_response(creator, session, draft)
        response["evaluation_suite"] = suites.current_projection(session_id)
        return response
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.patch("/sessions/{session_id}/evaluation-suite")
async def patch_creator_evaluation_suite(
    session_id: str, payload: CreatorEvaluationSuitePatchRequest
):
    try:
        creator = get_skill_creator_service()
        suites = get_skill_creator_evaluation_suite_service()
        await asyncio.to_thread(
            suites.patch,
            session_id,
            suite_id=payload.suite_id,
            expected_session_revision=payload.expected_session_revision,
            expected_draft_state_revision=payload.expected_draft_state_revision,
            expected_draft_revision=payload.expected_draft_revision,
            expected_draft_digest=payload.expected_draft_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=payload.expected_suite_digest.lower(),
            cases=[item.model_dump(mode="python") for item in payload.cases],
            change_reason=payload.change_reason,
        )
        session, draft = creator.get_session(session_id)
        response = _session_response(creator, session, draft)
        response["evaluation_suite"] = suites.current_projection(session_id)
        return response
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/evaluation-suite/confirm")
async def confirm_creator_evaluation_suite(
    session_id: str, payload: CreatorEvaluationSuiteConfirmRequest
):
    try:
        creator = get_skill_creator_service()
        suites = get_skill_creator_evaluation_suite_service()
        await asyncio.to_thread(
            suites.confirm,
            session_id,
            suite_id=payload.suite_id,
            expected_session_revision=payload.expected_session_revision,
            expected_draft_state_revision=payload.expected_draft_state_revision,
            expected_draft_revision=payload.expected_draft_revision,
            expected_draft_digest=payload.expected_draft_digest.lower(),
            expected_suite_revision=payload.expected_suite_revision,
            expected_suite_digest=payload.expected_suite_digest.lower(),
        )
        session, draft = creator.get_session(session_id)
        response = _session_response(creator, session, draft)
        response["evaluation_suite"] = suites.current_projection(session_id)
        return response
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/evaluations", status_code=202)
async def start_creator_evaluation(
    session_id: str, payload: CreatorEvaluationStartRequest
):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft, run = await evaluation.start_evaluation(
            session_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            repetitions=payload.repetitions,
            evaluation_suite_revision=payload.evaluation_suite_revision,
            evaluation_suite_digest=(
                payload.evaluation_suite_digest.lower()
                if payload.evaluation_suite_digest is not None
                else None
            ),
        )
        case_set = None
        if run.evaluation_suite_id is None:
            case_set = evaluation.evaluation_store.serialize(
                evaluation.evaluation_store.require_cases(
                    session.session_id, revision=session.cases_revision
                )
            )
        return _session_response(
            creator, session, draft, case_set=case_set, evaluation_run=run
        )
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.get("/evaluations/{run_id}")
async def get_creator_evaluation(run_id: str):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft, run = await asyncio.to_thread(
            evaluation.get_run_projection, run_id
        )
        return {
            "version": evaluation.VERSION,
            "run": evaluation.evaluation_store.serialize(run),
            "session": SkillCreatorSessionStore.serialize(session),
            "draft": creator.serialize_draft(draft),
        }
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/evaluations/{run_id}/cancel")
async def cancel_creator_evaluation(
    run_id: str, payload: CreatorEvaluationRunMutationRequest
):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft, run = await asyncio.to_thread(
            evaluation.cancel,
            run_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            expected_run_revision=payload.expected_run_revision,
        )
        return _session_response(creator, session, draft, evaluation_run=run)
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/evaluations/{run_id}/retry", status_code=202)
async def retry_creator_evaluation(
    run_id: str, payload: CreatorEvaluationRetryRequest
):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft, run = await asyncio.to_thread(
            evaluation.retry,
            run_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            expected_run_revision=payload.expected_run_revision,
            case_ids=payload.case_ids,
        )
        return _session_response(creator, session, draft, evaluation_run=run)
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.patch("/evaluations/{run_id}/review")
async def patch_creator_evaluation_review(
    run_id: str, payload: CreatorEvaluationFeedbackRequest
):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft, run = await asyncio.to_thread(
            evaluation.save_feedback,
            run_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            expected_run_revision=payload.expected_run_revision,
            expected_review_revision=payload.expected_review_revision,
            feedback=payload.feedback,
        )
        return _session_response(creator, session, draft, evaluation_run=run)
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/evaluations/{run_id}/review")
async def review_creator_evaluation(
    run_id: str, payload: CreatorEvaluationReviewRequest
):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft, run = await evaluation.review(
            run_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            expected_run_revision=payload.expected_run_revision,
            expected_review_revision=payload.expected_review_revision,
            decision=payload.decision,
            reason=payload.reason,
            acknowledge_failed_assertions=payload.acknowledge_failed_assertions,
            acknowledged_regression_item_ids=(
                payload.acknowledged_regression_item_ids
            ),
        )
        return _session_response(creator, session, draft, evaluation_run=run)
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/waive-evaluation")
async def waive_creator_evaluation(
    session_id: str, payload: CreatorEvaluationWaiveRequest
):
    try:
        creator = get_skill_creator_service()
        evaluation = get_skill_creator_evaluation_service()
        session, draft = await evaluation.waive(
            session_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            reason=payload.reason,
            confirmed=payload.confirmed,
        )
        return _session_response(creator, session, draft)
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/evolution-plan/generate")
async def generate_creator_evolution_plan(
    session_id: str, payload: CreatorEvolutionGenerateRequest
):
    try:
        if (payload.expected_evolution_revision is None) != (
            payload.expected_evolution_digest is None
        ):
            raise SkillCreatorValidationError(
                "Expected evolution revision and digest must be provided together.",
                code="skill_creator_evolution_plan_invalid",
            )
        evolution = get_skill_creator_evolution_service()
        plan = await evolution.generate(
            session_id,
            evaluation_run_id=payload.evaluation_run_id,
            expected_session_revision=payload.expected_session_revision,
            expected_draft_state_revision=payload.expected_draft_state_revision,
            expected_draft_revision=payload.expected_draft_revision,
            expected_draft_digest=payload.expected_draft_digest.lower(),
            expected_review_revision=payload.expected_review_revision,
            expected_run_revision=payload.expected_run_revision,
            expected_resource_plan_revision=payload.expected_resource_plan_revision,
            expected_resource_plan_digest=payload.expected_resource_plan_digest.lower(),
            expected_evolution_revision=payload.expected_evolution_revision,
            expected_evolution_digest=(
                payload.expected_evolution_digest.lower()
                if payload.expected_evolution_digest
                else None
            ),
        )
        return {
            "version": evolution.VERSION,
            "evolution_plan": evolution.evolution_store.serialize(plan),
        }
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.put("/sessions/{session_id}/evolution-plan/answers")
async def answer_creator_evolution_plan(
    session_id: str, payload: CreatorEvolutionAnswersRequest
):
    try:
        evolution = get_skill_creator_evolution_service()
        plan = await asyncio.to_thread(
            evolution.save_answers,
            session_id,
            plan_id=payload.plan_id,
            expected_session_revision=payload.expected_session_revision,
            expected_draft_state_revision=payload.expected_draft_state_revision,
            expected_draft_revision=payload.expected_draft_revision,
            expected_draft_digest=payload.expected_draft_digest.lower(),
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            answers=payload.answers,
        )
        return {
            "version": evolution.VERSION,
            "evolution_plan": evolution.evolution_store.serialize(plan),
        }
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.patch("/sessions/{session_id}/evolution-plan")
async def patch_creator_evolution_plan(
    session_id: str, payload: CreatorEvolutionPatchRequest
):
    try:
        evolution = get_skill_creator_evolution_service()
        plan = await asyncio.to_thread(
            evolution.patch,
            session_id,
            plan_id=payload.plan_id,
            expected_session_revision=payload.expected_session_revision,
            expected_draft_state_revision=payload.expected_draft_state_revision,
            expected_draft_revision=payload.expected_draft_revision,
            expected_draft_digest=payload.expected_draft_digest.lower(),
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
            changes=payload.changes,
        )
        return {
            "version": evolution.VERSION,
            "evolution_plan": evolution.evolution_store.serialize(plan),
        }
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/evolution-plan/confirm")
async def confirm_creator_evolution_plan(
    session_id: str, payload: CreatorEvolutionWriteRequest
):
    try:
        evolution = get_skill_creator_evolution_service()
        plan, resource_plan = await asyncio.to_thread(
            evolution.confirm,
            session_id,
            plan_id=payload.plan_id,
            expected_session_revision=payload.expected_session_revision,
            expected_draft_state_revision=payload.expected_draft_state_revision,
            expected_draft_revision=payload.expected_draft_revision,
            expected_draft_digest=payload.expected_draft_digest.lower(),
            expected_plan_revision=payload.expected_plan_revision,
            expected_plan_digest=payload.expected_plan_digest.lower(),
        )
        return {
            "version": evolution.VERSION,
            "evolution_plan": evolution.evolution_store.serialize(plan),
            "resource_plan": evolution.resource_plan_store.serialize(resource_plan),
        }
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc


@router.post("/sessions/{session_id}/iterate")
async def iterate_creator_skill(
    session_id: str, payload: CreatorEvaluationIterateRequest
):
    try:
        if _evolution_service is not None and _evolution_service.enabled:
            raise SkillCreatorValidationError(
                "Confirm a Skill evolution plan before rebuilding resources.",
                code="skill_creator_evolution_plan_required",
            )
        evaluation = get_skill_creator_evaluation_service()
        proposal = await evaluation.iterate(
            session_id,
            evaluation_run_id=payload.evaluation_run_id,
            expected_session_revision=payload.expected_session_revision,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            expected_review_revision=payload.expected_review_revision,
        )
        return {
            "version": evaluation.VERSION,
            "proposal": AuthoringProposalStore.serialize(
                proposal, include_payload=True
            ),
        }
    except (SkillCreatorError, SkillDraftError, SkillEvaluationError) as exc:
        raise _api_error(exc) from exc
