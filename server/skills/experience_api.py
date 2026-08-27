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


router = APIRouter(prefix="/api/skills/experience", tags=["skill-experience"])
_service: SkillExperienceService | None = None


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


def configure_skill_experience(service: SkillExperienceService | None) -> None:
    global _service
    _service = service


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


def _api_error(exc: SkillExperienceError) -> HTTPException:
    if exc.code == "skill_experience_disabled":
        status_code = 404
    elif isinstance(exc, SkillExperienceNotFoundError):
        status_code = 404
    elif isinstance(exc, SkillExperienceConflictError) or exc.code in {
        "skill_experience_evidence_stale",
        "skill_experience_candidate_conflict",
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
    return await asyncio.to_thread(_service.status)


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
