from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

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


def configure_skill_creator(service: SkillCreatorService | None) -> None:
    global _service
    _service = service


def get_skill_creator_service() -> SkillCreatorService:
    if _service is None:
        raise SkillCreatorValidationError(
            "Skill Creator V2 is disabled.", code="skill_creator_disabled"
        )
    return _service


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (SkillCreatorNotFoundError, SkillDraftNotFoundError)):
        status = 404
        code = "skill_creator_not_found"
    elif isinstance(exc, (SkillCreatorConflictError, SkillDraftConflictError)):
        status = 409
        code = "skill_creator_conflict"
    elif isinstance(exc, (SkillCreatorStorageError, SkillDraftStorageError)):
        status = 503
        code = "skill_creator_storage_unavailable"
    elif isinstance(exc, SkillCreatorValidationError):
        code = exc.code
        status = 404 if code == "skill_creator_disabled" else 400
        if code == "model_gateway_unconfigured":
            status = 503
        elif code == "skill_creator_source_unavailable":
            status = 501
        elif code in {
            "skill_creator_generation_failed",
            "skill_creator_generation_invalid",
            "skill_creator_tool_not_called",
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


def _session_response(service: SkillCreatorService, session, draft=None):
    return {
        "version": service.VERSION,
        "session": SkillCreatorSessionStore.serialize(session),
        "draft": service.serialize_draft(draft) if draft is not None else None,
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
        }
    return _service.status()


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
        session = await asyncio.to_thread(
            service.create_session, **payload.model_dump(mode="python")
        )
        return _session_response(service, session)
    except (SkillCreatorError, SkillDraftError) as exc:
        raise _api_error(exc) from exc


@router.get("/sessions/{session_id}")
async def get_creator_session(session_id: str):
    try:
        service = get_skill_creator_service()
        session, draft = await asyncio.to_thread(service.get_session, session_id)
        return _session_response(service, session, draft)
    except (SkillCreatorError, SkillDraftError) as exc:
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
        session = await asyncio.to_thread(
            service.update_definition,
            session_id,
            expected_session_revision=payload.expected_session_revision,
            changes=changes,
        )
        return _session_response(service, session)
    except (SkillCreatorError, SkillDraftError) as exc:
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
