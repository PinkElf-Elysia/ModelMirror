from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .approval_coordinator import ApprovalCoordinator
from .approval_store import (
    RuntimeApprovalConflictError,
    RuntimeApprovalNotFoundError,
    RuntimeApprovalStore,
    RuntimeApprovalValidationError,
)
from .execution_store import WorkflowExecutionStore


router = APIRouter(prefix="/api/runtime", tags=["runtime-approvals"])
_approval_store: RuntimeApprovalStore | None = None
_execution_store: WorkflowExecutionStore | None = None
_coordinator: ApprovalCoordinator | None = None
ApprovalDecisionValidator = Callable[
    [Any, "ApprovalDecisionRequest"], Awaitable[None]
]
_decision_validator: ApprovalDecisionValidator | None = None


class ApprovalDecisionRequest(BaseModel):
    revision: int = Field(ge=1)
    decision: str = Field(min_length=1, max_length=30)
    operator: str = Field(default="local-operator", min_length=1, max_length=200)
    edited_arguments: dict[str, Any] | None = None
    replacement_text: str | None = Field(default=None, max_length=100_000)
    message: str | None = Field(default=None, max_length=4_000)


class ApprovalReopenRequest(BaseModel):
    revision: int = Field(ge=1)
    timeout_seconds: int = Field(default=3600, ge=30, le=86_400)
    operator: str = Field(default="local-operator", min_length=1, max_length=200)


class ApprovalCancelRequest(BaseModel):
    revision: int = Field(ge=1)
    operator: str = Field(default="local-operator", min_length=1, max_length=200)
    message: str = Field(default="cancelled", max_length=4_000)


def configure_runtime_approvals(
    approval_store: RuntimeApprovalStore,
    execution_store: WorkflowExecutionStore,
    coordinator: ApprovalCoordinator | None = None,
) -> None:
    global _approval_store, _execution_store, _coordinator
    _approval_store = approval_store
    _execution_store = execution_store
    _coordinator = coordinator


def configure_approval_coordinator(coordinator: ApprovalCoordinator) -> None:
    global _coordinator
    _coordinator = coordinator


def configure_approval_decision_validator(
    validator: ApprovalDecisionValidator,
) -> None:
    global _decision_validator
    _decision_validator = validator


def get_approval_store() -> RuntimeApprovalStore:
    if _approval_store is None:
        raise RuntimeError("Runtime approval store is not configured.")
    return _approval_store


def get_execution_store() -> WorkflowExecutionStore:
    if _execution_store is None:
        raise RuntimeError("Workflow execution store is not configured.")
    return _execution_store


@router.get("/approvals")
async def list_runtime_approvals(
    status: str | None = Query(default=None, max_length=30),
    task_id: str | None = Query(default=None, max_length=200),
    run_id: str | None = Query(default=None, max_length=200),
    scope_type: str | None = Query(default=None, max_length=80),
    scope_id: str | None = Query(default=None, max_length=400),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    try:
        items = get_approval_store().list_requests(
            status=status,
            task_id=task_id,
            run_id=run_id,
            scope_type=scope_type,
            scope_id=scope_id,
            limit=limit,
        )
    except RuntimeApprovalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "items": [get_approval_store().serialize(item) for item in items],
        "count": len(items),
    }


@router.get("/approvals/{approval_id}")
async def get_runtime_approval(approval_id: str) -> dict[str, Any]:
    try:
        return get_approval_store().serialize(get_approval_store().require(approval_id))
    except RuntimeApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/approvals/{approval_id}/decide")
async def decide_runtime_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
) -> dict[str, Any]:
    try:
        current = get_approval_store().require(approval_id)
        if _decision_validator is not None:
            await _decision_validator(current, payload)
        item = get_approval_store().decide(
            approval_id,
            revision=payload.revision,
            decision=payload.decision,
            operator=payload.operator,
            edited_arguments=payload.edited_arguments,
            replacement_text=payload.replacement_text,
            message=payload.message,
        )
        get_execution_store().mark_ready(
            item.task_id, approval_id=item.approval_id
        )
    except RuntimeApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeApprovalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if _coordinator is not None:
        _coordinator.wake()
    return get_approval_store().serialize(item)


@router.post("/approvals/{approval_id}/reopen")
async def reopen_runtime_approval(
    approval_id: str,
    payload: ApprovalReopenRequest,
) -> dict[str, Any]:
    try:
        item = get_approval_store().reopen(
            approval_id,
            revision=payload.revision,
            timeout_seconds=payload.timeout_seconds,
            operator=payload.operator,
        )
    except RuntimeApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return get_approval_store().serialize(item)


@router.post("/approvals/{approval_id}/cancel")
async def cancel_runtime_approval(
    approval_id: str,
    payload: ApprovalCancelRequest,
) -> dict[str, Any]:
    try:
        item = get_approval_store().cancel(
            approval_id,
            revision=payload.revision,
            operator=payload.operator,
            message=payload.message,
        )
        get_execution_store().cancel(item.task_id, error=payload.message)
    except RuntimeApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return get_approval_store().serialize(item)


@router.get("/approval-coordinator/status")
async def get_runtime_approval_coordinator_status() -> dict[str, Any]:
    if _coordinator is None:
        return {"enabled": False, "running": False}
    return await _coordinator.status()
