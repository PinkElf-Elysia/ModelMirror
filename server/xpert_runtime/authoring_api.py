from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .authoring_service import AuthoringService
from .authoring_store import (
    AuthoringProposalConflictError,
    AuthoringProposalNotFoundError,
    AuthoringProposalStore,
    AuthoringProposalValidationError,
)


router = APIRouter(prefix="/api/runtime", tags=["runtime-authoring"])
_service: AuthoringService | None = None


class ProposalPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    payload: dict[str, Any] | None = None
    base_revision: int | None = Field(default=None, ge=1)


class ProposalActionRequest(BaseModel):
    revision: int = Field(ge=1)
    operator: str = Field(default="modelmirror-operator", max_length=120)
    reason: str = Field(default="", max_length=1000)


def configure_runtime_authoring(service: AuthoringService) -> None:
    global _service
    _service = service


def get_authoring_service() -> AuthoringService:
    if _service is None:
        raise RuntimeError("Authoring service is not configured.")
    return _service


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthoringProposalNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AuthoringProposalConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (AuthoringProposalValidationError, ValueError, TypeError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/authoring-proposals")
async def list_authoring_proposals(
    status: str | None = None,
    kind: str | None = None,
    target_id: str | None = None,
    source_xpert_id: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    store = get_authoring_service().proposal_store
    items = store.list(
        status=status,
        kind=kind,
        target_id=target_id,
        source_xpert_id=source_xpert_id,
        source_type=source_type,
        source_id=source_id,
        limit=limit,
    )
    return {
        "version": "xpert-authoring-proposals-v1",
        "items": [AuthoringProposalStore.serialize(item) for item in items],
        "total": len(items),
    }


@router.get("/authoring-proposals/{proposal_id}")
async def get_authoring_proposal(proposal_id: str):
    try:
        item = get_authoring_service().proposal_store.require(proposal_id)
        return AuthoringProposalStore.serialize(item, include_payload=True)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.patch("/authoring-proposals/{proposal_id}")
async def patch_authoring_proposal(
    proposal_id: str, payload: ProposalPatchRequest
):
    try:
        item = get_authoring_service().proposal_store.update_pending(
            proposal_id,
            revision=payload.revision,
            title=payload.title,
            payload=payload.payload,
            base_revision=payload.base_revision,
        )
        return AuthoringProposalStore.serialize(item, include_payload=True)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/authoring-proposals/{proposal_id}/validate")
async def validate_authoring_proposal(
    proposal_id: str, payload: ProposalActionRequest
):
    try:
        item = get_authoring_service().validate(
            proposal_id, revision=payload.revision
        )
        return AuthoringProposalStore.serialize(item, include_payload=True)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/authoring-proposals/{proposal_id}/approve")
async def approve_authoring_proposal(
    proposal_id: str, payload: ProposalActionRequest
):
    try:
        item = get_authoring_service().approve(
            proposal_id, revision=payload.revision, operator=payload.operator
        )
        return AuthoringProposalStore.serialize(item, include_payload=True)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/authoring-proposals/{proposal_id}/reject")
async def reject_authoring_proposal(
    proposal_id: str, payload: ProposalActionRequest
):
    try:
        item = get_authoring_service().reject(
            proposal_id,
            revision=payload.revision,
            operator=payload.operator,
            reason=payload.reason,
        )
        return AuthoringProposalStore.serialize(item, include_payload=True)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/authoring-proposals/{proposal_id}/cancel")
async def cancel_authoring_proposal(
    proposal_id: str, payload: ProposalActionRequest
):
    try:
        item = get_authoring_service().cancel(
            proposal_id, revision=payload.revision, operator=payload.operator
        )
        return AuthoringProposalStore.serialize(item, include_payload=True)
    except Exception as exc:
        raise _api_error(exc) from exc
