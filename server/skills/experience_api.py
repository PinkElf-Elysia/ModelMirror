from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .experience import (
    SkillExperienceCandidateV1,
    SkillExperienceConflictError,
    SkillExperienceError,
    SkillExperienceNotFoundError,
    SkillExperienceService,
    SkillExperienceSource,
    SkillExperienceStorageError,
)
from .experience_distillation import SkillExperienceDistillationService
from .experience_promotion import SkillExperiencePromotionService
from .creator_store import SkillCreatorSessionStore
from .draft_store import WorkspaceSkillDraftStore


router = APIRouter(prefix="/api/skills/experience", tags=["skill-experience"])
_service: SkillExperienceService | None = None
_distillation_service: SkillExperienceDistillationService | None = None
_promotion_service: SkillExperiencePromotionService | None = None


class ExperienceSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["workflow_classic", "xpert_chat"]
    source_task_id: str = Field(min_length=1, max_length=240)
    source_run_id: str = Field(min_length=1, max_length=240)
    source_xpert_id: str | None = Field(default=None, min_length=1, max_length=240)
    source_conversation_id: str | None = Field(
        default=None, min_length=1, max_length=240
    )
    source_message_id: str | None = Field(default=None, min_length=1, max_length=240)


class ExperienceMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expected_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )


class ExperienceEvidenceRequest(ExperienceMutationRequest):
    preview_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    evidence_ids: list[str] = Field(min_length=0, max_length=6)


class ExperienceDismissRequest(ExperienceMutationRequest):
    reason: str = Field(default="", max_length=1_000)


class ExperienceBriefRequest(ExperienceMutationRequest):
    suggestion: Literal["create", "update", "no_skill"]
    recommendation_reason: str = Field(default="", max_length=2_000)
    no_skill_reason: Literal[
        "one_off_task",
        "preference_or_environment_fact",
        "insufficient_evidence",
        "already_covered",
        "cannot_generalize",
    ] | None = None
    intent: str = Field(default="", max_length=2_000)
    positive_examples: list[str] = Field(default_factory=list, max_length=6)
    negative_examples: list[str] = Field(default_factory=list, max_length=6)
    expected_output: str = Field(default="", max_length=4_000)
    success_criteria: list[str] = Field(default_factory=list, max_length=12)
    reusable_steps: list[str] = Field(default_factory=list, max_length=12)
    failure_boundaries: list[str] = Field(default_factory=list, max_length=12)
    resource_clues: list[str] = Field(default_factory=list, max_length=12)
    overfitting_risk: str = Field(default="", max_length=2_000)


class ExperienceDecisionRequest(ExperienceMutationRequest):
    decision: Literal["create", "update", "dismiss"]
    target_skill_id: str | None = Field(default=None, min_length=1, max_length=240)
    override_reason: str | None = Field(default=None, max_length=2_000)
    new_boundary: str | None = Field(default=None, max_length=2_000)


def configure_skill_experience(service: SkillExperienceService | None) -> None:
    global _service
    _service = service


def configure_skill_experience_distillation(
    service: SkillExperienceDistillationService | None,
) -> None:
    global _distillation_service
    _distillation_service = service


def configure_skill_experience_promotion(
    service: SkillExperiencePromotionService | None,
) -> None:
    global _promotion_service
    _promotion_service = service


def get_skill_experience_service() -> SkillExperienceService:
    if _service is None:
        raise SkillExperienceStorageError(
            "Skill experience service is unavailable.",
            code="skill_experience_store_unavailable",
        )
    return _service


def _require_enabled() -> SkillExperienceService:
    service = get_skill_experience_service()
    service.require_enabled()
    return service


def _require_distillation() -> SkillExperienceDistillationService:
    _require_enabled()
    if _distillation_service is None:
        raise SkillExperienceStorageError(
            "Skill experience distillation is unavailable.",
            code="skill_experience_store_unavailable",
        )
    return _distillation_service


def _require_promotion() -> SkillExperiencePromotionService:
    _require_enabled()
    if _promotion_service is None:
        raise SkillExperienceStorageError(
            "Skill experience promotion is unavailable.",
            code="skill_experience_store_unavailable",
        )
    return _promotion_service


def _api_error(exc: SkillExperienceError) -> HTTPException:
    if exc.code == "skill_experience_disabled":
        status_code = 404
    elif isinstance(exc, SkillExperienceNotFoundError):
        status_code = 404
    elif isinstance(exc, SkillExperienceConflictError) or exc.code in {
        "skill_experience_evidence_stale",
        "skill_experience_candidate_conflict",
        "skill_experience_decision_required",
        "skill_experience_update_target_invalid",
        "skill_experience_promotion_stale",
    }:
        status_code = 409
    elif isinstance(exc, SkillExperienceStorageError) or exc.code == "skill_experience_store_unavailable":
        status_code = 503
    elif exc.code == "skill_experience_source_not_completed":
        status_code = 409
    else:
        status_code = 400
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc), "details": exc.details},
    )


def _serialize_candidate(candidate: SkillExperienceCandidateV1) -> dict[str, Any]:
    return candidate.serialize()


def _serialize_preview(preview: Any | None) -> dict[str, Any] | None:
    if preview is None:
        return None
    return {
        "version": preview.version,
        "source_kind": preview.source_kind,
        "source_task_id": preview.source_task_id,
        "source_run_id": preview.source_run_id,
        "source_title": preview.source_title,
        "preview_fingerprint": preview.preview_fingerprint,
        "candidates": [asdict(item) for item in preview.candidates],
    }


@router.get("/status")
async def skill_experience_status() -> dict[str, Any]:
    if _service is None:
        return {
            "version": "skill-experience-candidate-v1",
            "enabled": False,
            "available": False,
            "candidate_count": 0,
            "quarantine_count": 0,
            "error_code": "skill_experience_store_unavailable",
            "supported_sources": ["workflow_classic", "xpert_chat"],
            "evidence_version": "creator-evidence-v1",
            "model_calls_enabled": False,
        }
    status = await asyncio.to_thread(_service.status)
    if _distillation_service is not None:
        status.update(await asyncio.to_thread(_distillation_service.status))
    return status


@router.get("/candidates")
async def list_skill_experience_candidates(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    try:
        service = _require_enabled()
        candidates = await asyncio.to_thread(service.list_candidates, limit=limit)
        return {"candidates": [_serialize_candidate(item) for item in candidates]}
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.post("/candidates")
async def create_skill_experience_candidate(
    payload: ExperienceSourceRequest,
) -> dict[str, Any]:
    try:
        service = _require_enabled()
        candidate, preview = await asyncio.to_thread(
            service.create_or_get,
            SkillExperienceSource(**payload.model_dump()),
        )
        return {
            "candidate": _serialize_candidate(candidate),
            "evidence_preview": _serialize_preview(preview),
        }
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.get("/candidates/{candidate_id}")
async def get_skill_experience_candidate(candidate_id: str) -> dict[str, Any]:
    try:
        service = _require_enabled()
        candidate, preview = await asyncio.to_thread(service.get_candidate, candidate_id)
        return {
            "candidate": _serialize_candidate(candidate),
            "evidence_preview": _serialize_preview(preview),
        }
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.put("/candidates/{candidate_id}/evidence")
async def put_skill_experience_evidence(
    candidate_id: str,
    payload: ExperienceEvidenceRequest,
) -> dict[str, Any]:
    try:
        service = _require_enabled()
        candidate = await asyncio.to_thread(
            service.select_evidence,
            candidate_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            preview_fingerprint=payload.preview_fingerprint.lower(),
            evidence_ids=payload.evidence_ids,
        )
        return {"candidate": _serialize_candidate(candidate)}
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.post("/candidates/{candidate_id}/dismiss")
async def dismiss_skill_experience_candidate(
    candidate_id: str,
    payload: ExperienceDismissRequest,
) -> dict[str, Any]:
    try:
        service = _require_enabled()
        candidate = await asyncio.to_thread(
            service.dismiss,
            candidate_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            reason=payload.reason,
        )
        return {"candidate": _serialize_candidate(candidate)}
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.post("/candidates/{candidate_id}/analyze", status_code=202)
async def analyze_skill_experience_candidate(
    candidate_id: str,
    payload: ExperienceMutationRequest,
) -> dict[str, Any]:
    try:
        service = _require_distillation()
        candidate = await service.start_analysis(
            candidate_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
        )
        return {"candidate": _serialize_candidate(candidate)}
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.patch("/candidates/{candidate_id}/brief")
async def patch_skill_experience_brief(
    candidate_id: str,
    payload: ExperienceBriefRequest,
) -> dict[str, Any]:
    try:
        service = _require_distillation()
        values = payload.model_dump(
            exclude={"expected_revision", "expected_digest"}
        )
        candidate = await asyncio.to_thread(
            service.update_brief,
            candidate_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            payload=values,
        )
        return {"candidate": _serialize_candidate(candidate)}
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.post("/candidates/{candidate_id}/decision")
async def decide_skill_experience_candidate(
    candidate_id: str,
    payload: ExperienceDecisionRequest,
) -> dict[str, Any]:
    try:
        service = _require_distillation()
        candidate = await asyncio.to_thread(
            service.decide,
            candidate_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
            decision=payload.decision,
            target_skill_id=payload.target_skill_id,
            override_reason=payload.override_reason,
            new_boundary=payload.new_boundary,
        )
        return {"candidate": _serialize_candidate(candidate)}
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc


@router.post("/candidates/{candidate_id}/promote")
async def promote_skill_experience_candidate(
    candidate_id: str,
    payload: ExperienceMutationRequest,
) -> dict[str, Any]:
    try:
        service = _require_promotion()
        result = await asyncio.to_thread(
            service.promote,
            candidate_id,
            expected_revision=payload.expected_revision,
            expected_digest=payload.expected_digest.lower(),
        )
        return {
            "candidate": _serialize_candidate(result.candidate),
            "creator_session_id": result.session.session_id,
            "route": result.route,
            "session": SkillCreatorSessionStore.serialize(result.session),
            "draft": (
                WorkspaceSkillDraftStore.serialize(
                    result.draft, include_content=False
                )
                if result.draft is not None
                else None
            ),
        }
    except SkillExperienceError as exc:
        raise _api_error(exc) from exc
