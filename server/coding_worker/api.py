from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field

from .contracts import (
    Origin,
    StrictModel,
    TaskCreateRequest,
    TaskRecord,
    TaskState,
    TERMINAL_STATES,
    WorkerApproval,
)
from .service import CodingWorkerService
from .store import WorkerConflictError, WorkerNotFoundError, WorkerStoreError
from .workspace import WorkspaceError


class TaskMessageRequest(StrictModel):
    message: str = Field(min_length=1, max_length=1_048_576)


class ApprovalDecisionRequest(StrictModel):
    approval_id: str = Field(pattern=r"^approval_[a-f0-9]{32}$")
    decision: Literal["approve_once", "approve_task", "reject"]
    ttl_seconds: int = Field(default=900, ge=30, le=3600)


_service: CodingWorkerService | None = None
_enabled_override: bool | None = None
_CONSOLE_ORIGIN = Origin(module="worker-console", object_id="local-user")


@asynccontextmanager
async def _lifespan(_app: object) -> AsyncIterator[None]:
    yield
    if _service is not None:
        await _service.shutdown()


router = APIRouter(
    prefix="/api/coding-worker/v1",
    tags=["coding-worker"],
    lifespan=_lifespan,
)


def is_coding_worker_enabled() -> bool:
    if _enabled_override is not None:
        return _enabled_override
    return os.getenv("CODING_WORKER_V14_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def configure_coding_worker_for_tests(
    service: CodingWorkerService | None, *, enabled: bool | None = None
) -> None:
    global _service, _enabled_override
    _service = service
    _enabled_override = enabled


def get_coding_worker_service() -> CodingWorkerService:
    _require_enabled()
    if _service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "coding_worker_provider_unavailable",
                "message": "The V14 Worker provider is not configured yet.",
            },
        )
    return _service


def _require_enabled() -> None:
    if not is_coding_worker_enabled():
        raise HTTPException(status_code=404, detail="Coding Worker V14 is disabled")


def _raise_worker_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, WorkerNotFoundError):
        status = 404
    elif isinstance(exc, WorkerConflictError):
        status = 409
    elif isinstance(exc, WorkspaceError) and exc.code in {
        "workspace_not_found",
        "entry_not_found",
    }:
        status = 404
    elif isinstance(exc, (WorkerStoreError, WorkspaceError)):
        status = 400
    else:
        status = 500
    code = getattr(exc, "code", "coding_worker_failed")
    raise HTTPException(
        status_code=status,
        detail={"code": code, "message": str(exc)},
    ) from exc


@router.get("")
async def coding_worker_status() -> dict[str, Any]:
    enabled = is_coding_worker_enabled()
    return {
        "enabled": enabled,
        "available": enabled and _service is not None,
        "version": "v1",
        "max_active_tasks": _service.max_active_tasks if _service is not None else 2,
        "retention_seconds": (
            _service.store.retention_seconds if _service is not None else 604800
        ),
        "network_enabled": False,
    }


@router.post("/tasks", response_model=TaskRecord, status_code=202)
async def create_task(payload: TaskCreateRequest) -> TaskRecord:
    _validate_model_route(payload.model_route)
    try:
        return await get_coding_worker_service().create_task(_CONSOLE_ORIGIN, payload)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks", response_model=dict[str, list[TaskRecord]])
async def list_tasks() -> dict[str, list[TaskRecord]]:
    try:
        return {"tasks": get_coding_worker_service().store.list_tasks(origin=_CONSOLE_ORIGIN)}
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}", response_model=TaskRecord)
async def get_task(task_id: str) -> TaskRecord:
    try:
        return get_coding_worker_service().store.get_task(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    service = get_coding_worker_service()
    try:
        service.store.get_task(task_id)
    except Exception as exc:
        _raise_worker_error(exc)

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            events = service.store.list_events(task_id, after=cursor)
            for event in events:
                cursor = event.sequence
                yield _encode_sse(event.type, event.model_dump(mode="json"), event.sequence)
            task = service.store.get_task(task_id)
            if task.state in TERMINAL_STATES and not events:
                return
            if not events:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/tasks/{task_id}/messages", response_model=TaskRecord, status_code=202)
async def append_task_message(task_id: str, payload: TaskMessageRequest) -> TaskRecord:
    try:
        return await get_coding_worker_service().append_message(task_id, payload.message)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/approvals", response_model=dict[str, list[WorkerApproval]])
async def task_approvals(task_id: str) -> dict[str, list[WorkerApproval]]:
    try:
        return {"approvals": get_coding_worker_service().store.list_approvals(task_id)}
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/approvals", response_model=WorkerApproval)
async def decide_task_approval(
    task_id: str, payload: ApprovalDecisionRequest
) -> WorkerApproval:
    service = get_coding_worker_service()
    try:
        approval = service.store.get_approval(payload.approval_id)
        if approval.task_id != task_id:
            raise WorkerNotFoundError("Approval was not found.", code="approval_not_found")
        decided = service.store.decide_approval(
            payload.approval_id,
            approved=payload.decision != "reject",
            task_scope=payload.decision == "approve_task",
            ttl_seconds=payload.ttl_seconds,
        )
        task = service.store.get_task(task_id)
        if task.state is TaskState.WAITING_APPROVAL:
            service.store.transition(
                task_id,
                TaskState.RUNNING,
                expected_state=TaskState.WAITING_APPROVAL,
            )
        return decided
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/pause", response_model=TaskRecord)
async def pause_task(task_id: str) -> TaskRecord:
    try:
        return await get_coding_worker_service().pause(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/resume", response_model=TaskRecord, status_code=202)
async def resume_task(task_id: str) -> TaskRecord:
    try:
        return await get_coding_worker_service().resume(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/cancel", response_model=TaskRecord)
async def cancel_task(task_id: str) -> TaskRecord:
    try:
        return await get_coding_worker_service().cancel(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/pin", response_model=TaskRecord)
async def pin_task(task_id: str) -> TaskRecord:
    try:
        return get_coding_worker_service().store.set_pinned(task_id, True)
    except Exception as exc:
        _raise_worker_error(exc)


@router.delete("/tasks/{task_id}/pin", response_model=TaskRecord)
async def unpin_task(task_id: str) -> TaskRecord:
    try:
        return get_coding_worker_service().store.set_pinned(task_id, False)
    except Exception as exc:
        _raise_worker_error(exc)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str) -> Response:
    service = get_coding_worker_service()
    try:
        task = service.store.get_task(task_id)
        if service.store.delete_task(task_id) and task.workspace_id is not None:
            service.workspace_broker.delete(task.workspace_id)
        return Response(status_code=204)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/workspace/tree")
async def workspace_tree(task_id: str) -> dict[str, Any]:
    service, task = _task_workspace(task_id)
    try:
        return {
            "workspace_id": task.workspace_id,
            "tree_hash": service.workspace_broker.current_tree_hash(task.workspace_id),
            "entries": [
                entry.model_dump(mode="json")
                for entry in service.workspace_broker.tree(task.workspace_id)
            ],
        }
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/workspace/entries/{entry_id}")
async def workspace_entry(task_id: str, entry_id: str) -> Response:
    service, task = _task_workspace(task_id)
    try:
        content = service.workspace_broker.read_entry(task.workspace_id, entry_id)
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise WorkspaceError(
                "Binary entries are not available as text previews.",
                code="preview_unavailable",
            ) from exc
        return Response(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/workspace/diff")
async def workspace_diff(task_id: str) -> Response:
    service, task = _task_workspace(task_id)
    try:
        return Response(
            service.workspace_broker.diff(task.workspace_id),
            media_type="text/x-diff; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:
        _raise_worker_error(exc)


def _task_workspace(task_id: str) -> tuple[CodingWorkerService, TaskRecord]:
    service = get_coding_worker_service()
    try:
        task = service.store.get_task(task_id)
    except Exception as exc:
        _raise_worker_error(exc)
    if task.workspace_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "workspace_not_ready", "message": "Workspace is not ready."},
        )
    return service, task


def _validate_model_route(model_route: str) -> None:
    configured = {
        value.strip()
        for value in os.getenv("CODING_WORKER_MODEL_ROUTES", "coding/default").split(",")
        if value.strip()
    }
    if model_route not in configured:
        raise HTTPException(
            status_code=400,
            detail={"code": "model_route_not_allowed", "message": "Model route is not allowed."},
        )


def _encode_sse(event_type: str, payload: dict[str, Any], sequence: int) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event_type}\ndata: {encoded}\n\n"
