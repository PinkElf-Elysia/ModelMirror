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
from .creator_resource_build_service import SkillCreatorResourceBuildService
from .creator_resource_service import SkillCreatorResourcePlanningService
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

try:
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
except ModuleNotFoundError:
    from xpert_runtime.authoring_store import AuthoringProposalStore


router = APIRouter(prefix="/api/skills/creator", tags=["skill-creator"])
_service: SkillCreatorService | None = None
_evaluation_service: SkillCreatorEvaluationService | None = None
_resource_planning_service: SkillCreatorResourcePlanningService | None = None
_resource_build_service: SkillCreatorResourceBuildService | None = None


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


class CreatorEvaluationWaiveRequest(CreatorEvaluationWriteRequest):
    reason: str = Field(min_length=1, max_length=4_000)
    confirmed: bool = False


class CreatorEvaluationIterateRequest(CreatorEvaluationWriteRequest):
    evaluation_run_id: str = Field(min_length=1, max_length=200)
    expected_review_revision: int = Field(ge=0)


def configure_skill_creator(service: SkillCreatorService | None) -> None:
    global _service
    global _evaluation_service
    global _resource_planning_service
    global _resource_build_service
    if service is not _service:
        if (
            _evaluation_service is not None
            and getattr(_evaluation_service, "creator_service", None) is not service
        ):
            _evaluation_service = None
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
    _service = service


def configure_skill_creator_evaluation(
    service: SkillCreatorEvaluationService | None,
) -> None:
    global _evaluation_service
    _evaluation_service = service


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


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (SkillCreatorNotFoundError, SkillDraftNotFoundError, SkillEvaluationNotFoundError),
    ):
        status = 404
        code = "skill_creator_not_found"
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
        code = "skill_evaluation_conflict" if isinstance(
            exc, (SkillEvaluationConflictError, SkillEvaluationStateError)
        ) else "skill_creator_conflict"
    elif isinstance(
        exc,
        (SkillCreatorStorageError, SkillDraftStorageError, SkillEvaluationStorageError),
    ):
        status = 503
        code = (
            "skill_evaluation_storage_unavailable"
            if isinstance(exc, SkillEvaluationStorageError)
            else "skill_creator_storage_unavailable"
        )
    elif isinstance(exc, SkillEvaluationValidationError):
        status = (
            503
            if exc.code
            in {
                "model_gateway_unconfigured",
                "skill_evaluation_sidecar_unavailable",
                "skill_evaluation_preflight_failed",
            }
            else 400
        )
        code = exc.code
    elif isinstance(exc, SkillCreatorValidationError):
        code = exc.code
        status = 404 if code in {
            "skill_creator_disabled",
            "skill_creator_resource_authoring_disabled",
        } else 400
        if code == "model_gateway_unconfigured":
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
        }:
            status = 502
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
                _resource_planning_service.plan_store.current_for_session(
                    session.session_id
                ),
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
    }
    return response


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
        _resource_build_service.status()
        if _resource_build_service is not None
        else {
            "resource_build_enabled": False,
            "resource_build_version": None,
            "resource_builder_available": False,
            "script_sandbox_configured": False,
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
        )
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


@router.post("/sessions/{session_id}/iterate")
async def iterate_creator_skill(
    session_id: str, payload: CreatorEvaluationIterateRequest
):
    try:
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
