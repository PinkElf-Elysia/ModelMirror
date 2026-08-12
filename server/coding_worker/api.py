from __future__ import annotations

import asyncio
import json
import os
from pathlib import PurePosixPath
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
import httpx
from pydantic import Field, ValidationError

from .contracts import (
    Origin,
    CodeDiagnosticsSnapshot,
    CodeIntelligenceSnapshot,
    OperationState,
    StrictModel,
    TaskCreateRequest,
    TaskRecord,
    TERMINAL_STATES,
    WorkerCapabilities,
    WorkerApproval,
    WorkerArtifact,
    WorkerChangeset,
    WorkerEvidence,
    WorkerDiagnostic,
    WorkerPlan,
    WorkerQuestion,
    WorkerQuestionAnswer,
    WorkerTurnHistory,
    WorkerTaskExport,
    OperationOutputChunk,
    SubtaskRecord,
    SubtaskRequest,
)
from .service import CodingWorkerService
from .runtime import CodingWorkerRuntime, build_runtime_from_environment
from .store import WorkerConflictError, WorkerNotFoundError, WorkerStoreError
from .workspace import WorkspaceError


class TaskMessageRequest(StrictModel):
    message: str = Field(min_length=1, max_length=1_048_576)


class ApprovalDecisionRequest(StrictModel):
    approval_id: str = Field(pattern=r"^approval_[a-f0-9]{32}$")
    decision: Literal["approve_once", "approve_task", "reject"]
    ttl_seconds: int = Field(default=900, ge=30, le=3600)


class TaskForkRequest(StrictModel):
    client_fork_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class SubtaskMergeRequest(StrictModel):
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class TaskChildrenResponse(StrictModel):
    tasks: tuple[TaskRecord, ...]
    subtasks: tuple[SubtaskRecord, ...] = ()


_service: CodingWorkerService | None = None
_enabled_override: bool | None = None
_runtime: CodingWorkerRuntime | None = None
_startup_error: str | None = None
_CONSOLE_ORIGIN = Origin(module="worker-console", object_id="local-user")


@asynccontextmanager
async def _lifespan(_app: object) -> AsyncIterator[None]:
    global _service, _runtime, _startup_error
    if is_coding_worker_enabled() and _service is None:
        try:
            _runtime = build_runtime_from_environment()
            _service = await _runtime.start()
            _startup_error = None
        except Exception as exc:
            _startup_error = getattr(
                exc, "code", "coding_worker_provider_unavailable"
            )
    try:
        yield
    finally:
        if _runtime is not None:
            await _runtime.close()
            _runtime = None
            _service = None
        elif _service is not None:
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


def _feature_enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def coding_worker_capabilities() -> WorkerCapabilities:
    task_runtime = is_coding_worker_enabled() and _service is not None
    v15 = task_runtime and _feature_enabled("CODING_WORKER_V15_ENABLED")
    v16 = task_runtime and _feature_enabled("CODING_WORKER_V16_ENABLED")
    interaction = v16 and _feature_enabled("CODING_WORKER_INTERACTION_ENABLED")
    return WorkerCapabilities(
        task_runtime=task_runtime,
        professional_file_tools=v15,
        shell=v15 and _feature_enabled("CODING_WORKER_SHELL_ENABLED"),
        operation_output=v15,
        changesets=v15,
        code_intelligence=(
            v15 and _feature_enabled("CODING_WORKER_CODE_INTELLIGENCE_ENABLED")
        ),
        structured_plan=interaction,
        user_questions=interaction,
        context_compaction=v16,
        turn_history=(
            v16 and _feature_enabled("CODING_WORKER_SESSION_CONTROLS_ENABLED")
        ),
        subtasks=(v16 and _feature_enabled("CODING_WORKER_SUBAGENTS_ENABLED")),
    )


def _require_interaction_enabled() -> None:
    if not (
        _feature_enabled("CODING_WORKER_V16_ENABLED")
        and _feature_enabled("CODING_WORKER_INTERACTION_ENABLED")
    ):
        raise HTTPException(
            status_code=404, detail="Coding Worker V16 interaction is disabled"
        )


def _require_session_controls_enabled() -> None:
    if not (
        _feature_enabled("CODING_WORKER_V16_ENABLED")
        and _feature_enabled("CODING_WORKER_SESSION_CONTROLS_ENABLED")
    ):
        raise HTTPException(
            status_code=404, detail="Coding Worker V16 session controls are disabled"
        )


def _require_subtasks_enabled() -> None:
    if not (
        _feature_enabled("CODING_WORKER_V16_ENABLED")
        and _feature_enabled("CODING_WORKER_SUBAGENTS_ENABLED")
    ):
        raise HTTPException(
            status_code=404, detail="Coding Worker V16 subtasks are disabled"
        )


def _require_children_enabled() -> None:
    if not (
        _feature_enabled("CODING_WORKER_V16_ENABLED")
        and (
            _feature_enabled("CODING_WORKER_SESSION_CONTROLS_ENABLED")
            or _feature_enabled("CODING_WORKER_SUBAGENTS_ENABLED")
        )
    ):
        raise HTTPException(
            status_code=404, detail="Coding Worker V16 task children are disabled"
        )


def configure_coding_worker_for_tests(
    service: CodingWorkerService | None, *, enabled: bool | None = None
) -> None:
    global _service, _enabled_override, _startup_error
    _service = service
    _enabled_override = enabled
    _startup_error = None


def get_coding_worker_service() -> CodingWorkerService:
    _require_enabled()
    if _service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "coding_worker_provider_unavailable",
                "message": "The V14 Worker provider is unavailable.",
                "reason": _startup_error,
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
    capabilities = coding_worker_capabilities()
    return {
        "enabled": enabled,
        "available": enabled and _service is not None,
        "version": "v1",
        "max_active_tasks": _service.max_active_tasks if _service is not None else 2,
        "retention_seconds": (
            _service.store.retention_seconds if _service is not None else 604800
        ),
        "network_enabled": _runtime.network_enabled if _runtime is not None else False,
        "acceptance_checks": (
            sorted(_service.tool_broker.frozen_checks)
            if _service is not None and _service.tool_broker is not None
            else []
        ),
        "model_routes": _configured_model_routes(),
        "reason": _startup_error,
        "capabilities": capabilities.model_dump(mode="json"),
    }


@router.get("/capabilities", response_model=WorkerCapabilities)
async def get_worker_capabilities() -> WorkerCapabilities:
    return coding_worker_capabilities()


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


@router.get("/tasks/{task_id}/plan", response_model=WorkerPlan | None)
async def task_plan(task_id: str) -> WorkerPlan | None:
    _require_interaction_enabled()
    try:
        return get_coding_worker_service().store.latest_plan(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/questions",
    response_model=dict[str, list[WorkerQuestion]],
)
async def task_questions(task_id: str) -> dict[str, list[WorkerQuestion]]:
    _require_interaction_enabled()
    try:
        return {
            "questions": get_coding_worker_service().store.list_questions(task_id)
        }
    except Exception as exc:
        _raise_worker_error(exc)


@router.post(
    "/tasks/{task_id}/questions/{question_id}",
    response_model=WorkerQuestion,
    status_code=202,
)
async def answer_task_question(
    task_id: str, question_id: str, payload: WorkerQuestionAnswer
) -> WorkerQuestion:
    _require_interaction_enabled()
    try:
        return await get_coding_worker_service().answer_question(
            task_id, question_id, payload
        )
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
        if approval.capability == "shell" and payload.decision == "approve_task":
            raise WorkerConflictError(
                "Shell approval is always bound to one exact operation.",
                code="shell_task_approval_forbidden",
            )
        decided = service.store.decide_approval(
            payload.approval_id,
            approved=payload.decision != "reject",
            task_scope=payload.decision == "approve_task",
            ttl_seconds=payload.ttl_seconds,
        )
        service.settle_approval_state(task_id)
        return decided
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/evidence", response_model=dict[str, list[WorkerEvidence]]
)
async def task_evidence(task_id: str) -> dict[str, list[WorkerEvidence]]:
    service = get_coding_worker_service()
    try:
        task = service.store.get_task(task_id)
        tree_hash = (
            service.workspace_broker.current_tree_hash(task.workspace_id)
            if task.workspace_id is not None
            else None
        )
        return {
            "evidence": service.store.list_evidence(
                task_id, current_tree_hash=tree_hash
            )
        }
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/artifacts", response_model=dict[str, list[WorkerArtifact]]
)
async def task_artifacts(task_id: str) -> dict[str, list[WorkerArtifact]]:
    try:
        return {"artifacts": get_coding_worker_service().store.list_artifacts(task_id)}
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/operations/{operation_id}/output",
    response_model=dict[str, list[OperationOutputChunk]],
)
async def task_operation_output(
    task_id: str,
    operation_id: str,
    after: int = Query(default=0, ge=0),
) -> dict[str, list[OperationOutputChunk]]:
    service = get_coding_worker_service()
    try:
        operation = service.store.get_operation(operation_id)
        if operation.task_id != task_id:
            raise WorkerNotFoundError(
                "Operation was not found.", code="operation_not_found"
            )
        chunks: list[OperationOutputChunk] = []
        cursor = after
        scanned = 0
        while len(chunks) < 256 and scanned < 10_000:
            events = service.store.list_events(task_id, after=cursor, limit=1000)
            if not events:
                break
            cursor = events[-1].sequence
            scanned += len(events)
            for event in events:
                if (
                    event.type != "operation_output"
                    or event.payload.get("operation_id") != operation_id
                ):
                    continue
                chunks.append(
                    OperationOutputChunk(
                        task_id=task_id,
                        operation_id=operation_id,
                        sequence=event.sequence,
                        stream=event.payload.get("stream"),
                        text=event.payload.get("text"),
                        created_at=event.created_at,
                        truncated=event.payload.get("truncated", False),
                    )
                )
                if len(chunks) >= 256:
                    break
            if len(events) < 1000:
                break
        return {"chunks": chunks}
    except Exception as exc:
        _raise_worker_error(exc)


def _code_intelligence_snapshot(
    service: CodingWorkerService,
    task_id: str,
    operation_id: str,
) -> CodeIntelligenceSnapshot:
    task = service.store.get_task(task_id)
    operation = service.store.get_operation(operation_id)
    operation_kind = {
        "code_symbols": "symbols",
        "code_definition": "definition",
        "code_references": "references",
        "code_hover": "hover",
        "code_diagnostics": "diagnostics",
    }.get(operation.tool_name)
    if operation.task_id != task_id or operation_kind is None:
        raise WorkerNotFoundError(
            "Code intelligence result was not found.",
            code="code_intelligence_not_found",
        )
    if operation.state is not OperationState.COMPLETED:
        raise WorkerConflictError(
            "Code intelligence result is not available.",
            code="code_intelligence_result_unavailable",
        )
    if task.workspace_id is None:
        raise WorkerConflictError(
            "Task workspace is unavailable.", code="workspace_unavailable"
        )
    result = operation.result
    value_key = {
        "symbols": "symbols",
        "definition": "locations",
        "references": "locations",
        "hover": "hover",
        "diagnostics": "diagnostics",
    }[operation_kind]
    expected_keys = {
        "task_id",
        "entry_id",
        "workspace_tree_hash",
        "operation",
        "language",
        value_key,
    }
    if (
        not isinstance(result, dict)
        or set(result) != expected_keys
        or result.get("task_id") != task_id
        or result.get("operation") != operation_kind
    ):
        raise WorkerStoreError(
            "Code intelligence result is corrupt.", code="worker_data_corrupt"
        )
    current_tree_hash = service.workspace_broker.current_tree_hash(task.workspace_id)
    try:
        return CodeIntelligenceSnapshot(
            task_id=task_id,
            operation_id=operation_id,
            entry_id=result["entry_id"],
            operation=operation_kind,
            language=result["language"],
            workspace_tree_hash=result["workspace_tree_hash"],
            current_tree_hash=current_tree_hash,
            stale=result["workspace_tree_hash"] != current_tree_hash,
            result={value_key: result[value_key]},
        )
    except (KeyError, ValidationError) as exc:
        raise WorkerStoreError(
            "Code intelligence result is corrupt.", code="worker_data_corrupt"
        ) from exc


@router.get(
    "/tasks/{task_id}/code-intelligence/{operation_id}",
    response_model=CodeIntelligenceSnapshot,
)
async def task_code_intelligence(
    task_id: str, operation_id: str
) -> CodeIntelligenceSnapshot:
    try:
        return _code_intelligence_snapshot(
            get_coding_worker_service(), task_id, operation_id
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/diagnostics/{operation_id}",
    response_model=CodeDiagnosticsSnapshot,
)
async def task_diagnostics(
    task_id: str, operation_id: str
) -> CodeDiagnosticsSnapshot:
    try:
        snapshot = _code_intelligence_snapshot(
            get_coding_worker_service(), task_id, operation_id
        )
        if snapshot.operation != "diagnostics":
            raise WorkerNotFoundError(
                "Diagnostics were not found.", code="diagnostics_not_found"
            )
        diagnostics = tuple(
            WorkerDiagnostic.model_validate(item)
            for item in snapshot.result.get("diagnostics", [])
        )
        if any(
            item.task_id != task_id
            or item.entry_id != snapshot.entry_id
            or item.workspace_tree_hash != snapshot.workspace_tree_hash
            for item in diagnostics
        ):
            raise WorkerStoreError(
                "Diagnostics result is corrupt.", code="worker_data_corrupt"
            )
        return CodeDiagnosticsSnapshot(
            task_id=task_id,
            operation_id=operation_id,
            entry_id=snapshot.entry_id,
            language=snapshot.language,
            workspace_tree_hash=snapshot.workspace_tree_hash,
            current_tree_hash=snapshot.current_tree_hash,
            stale=snapshot.stale,
            diagnostics=diagnostics,
        )
    except ValidationError as exc:
        _raise_worker_error(
            WorkerStoreError(
                "Diagnostics result is corrupt.", code="worker_data_corrupt"
            )
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/changesets/{operation_id}",
    response_model=WorkerChangeset,
)
async def task_changeset(task_id: str, operation_id: str) -> WorkerChangeset:
    service = get_coding_worker_service()
    try:
        operation = service.store.get_operation(operation_id)
        if operation.task_id != task_id:
            raise WorkerNotFoundError(
                "Changeset was not found.", code="changeset_not_found"
            )
        value = (operation.result or {}).get("changeset")
        if not isinstance(value, dict):
            raise WorkerConflictError(
                "Changeset result is not available.",
                code="changeset_result_unavailable",
            )
        return WorkerChangeset.model_validate(value)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
async def task_artifact(task_id: str, artifact_id: str) -> Response:
    service = get_coding_worker_service()
    try:
        artifact = next(
            (
                item
                for item in service.store.list_artifacts(task_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise WorkerNotFoundError("Artifact was not found.", code="artifact_not_found")
        content = service.store.read_artifact(artifact_id, task_id=task_id)
        return Response(
            content,
            media_type=artifact.media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{artifact_id}.bin"',
            },
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/pause", response_model=TaskRecord)
async def pause_task(task_id: str) -> TaskRecord:
    try:
        return await get_coding_worker_service().pause(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/turns", response_model=WorkerTurnHistory)
async def task_turn_history(task_id: str) -> WorkerTurnHistory:
    _require_session_controls_enabled()
    try:
        return get_coding_worker_service().store.turn_history(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/undo", response_model=WorkerTurnHistory)
async def undo_task_turn(task_id: str) -> WorkerTurnHistory:
    _require_session_controls_enabled()
    try:
        return await get_coding_worker_service().navigate_turn(task_id, "undo")
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/redo", response_model=WorkerTurnHistory)
async def redo_task_turn(task_id: str) -> WorkerTurnHistory:
    _require_session_controls_enabled()
    try:
        return await get_coding_worker_service().navigate_turn(task_id, "redo")
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/fork", response_model=TaskRecord, status_code=202)
async def fork_task(task_id: str, payload: TaskForkRequest) -> TaskRecord:
    _require_session_controls_enabled()
    try:
        return await get_coding_worker_service().fork_task(
            task_id, payload.client_fork_id
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.post(
    "/tasks/{task_id}/subtasks", response_model=SubtaskRecord, status_code=202
)
async def create_task_subtask(
    task_id: str, payload: SubtaskRequest
) -> SubtaskRecord:
    _require_subtasks_enabled()
    try:
        return await get_coding_worker_service().create_subtask(task_id, payload)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post(
    "/tasks/{task_id}/subtasks/{child_task_id}/merge",
    response_model=SubtaskRecord,
)
async def merge_task_subtask(
    task_id: str, child_task_id: str, payload: SubtaskMergeRequest
) -> SubtaskRecord:
    _require_subtasks_enabled()
    try:
        return await get_coding_worker_service().merge_subtask(
            task_id, child_task_id, payload.operation_id
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/children", response_model=TaskChildrenResponse)
async def task_children(task_id: str) -> TaskChildrenResponse:
    _require_children_enabled()
    try:
        service = get_coding_worker_service()
        return TaskChildrenResponse(
            tasks=tuple(service.store.list_children(task_id)),
            subtasks=tuple(service.store.list_subtasks(task_id)),
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/export", response_model=WorkerTaskExport)
async def export_task(task_id: str) -> WorkerTaskExport:
    _require_session_controls_enabled()
    try:
        return await get_coding_worker_service().export_task(task_id)
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


@router.get("/tasks/{task_id}/services/{service_id}/preview/{preview_path:path}")
async def service_preview(
    task_id: str,
    service_id: str,
    preview_path: str,
    request: Request,
) -> Response:
    service, task = _task_workspace(task_id)
    try:
        if service.tool_broker is None or service.tool_broker.executor is None:
            raise WorkerConflictError(
                "Preview service is unavailable.", code="preview_unavailable"
            )
        path = PurePosixPath(preview_path or ".")
        if "\\" in preview_path or any(part == ".." for part in path.parts):
            raise WorkerConflictError(
                "Preview path is invalid.", code="preview_path_invalid"
            )
        result = await service.tool_broker.executor.service_status(
            task_id=task_id,
            workspace_id=task.workspace_id,
            service_id=service_id,
        )
        port = result.get("preview_port")
        if result.get("state") != "running" or isinstance(port, bool) or not isinstance(port, int):
            raise WorkerConflictError(
                "Preview service is not running.", code="preview_unavailable"
            )
        slot_id = service.workspace_broker.workspace_slot(task.workspace_id)
        host = os.getenv(
            f"CODING_WORKER_{slot_id.replace('-', '_').upper()}_PREVIEW_HOST",
            f"coding-worker-{slot_id}",
        )
        query = request.url.query
        upstream = await _fetch_preview(
            f"http://{host}:{port}/{preview_path}" + (f"?{query}" if query else "")
        )
        if 300 <= upstream.status_code < 400:
            raise WorkerConflictError(
                "Preview redirects are not allowed.", code="preview_redirect_denied"
            )
        return Response(
            upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/octet-stream"),
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "sandbox allow-scripts allow-forms; default-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'self'",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:
        _raise_worker_error(exc)


async def _fetch_preview(url: str) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=3.0),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        request = client.build_request("GET", url)
        response = await client.send(request, stream=True)
        try:
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes(64 * 1024):
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise WorkerConflictError(
                        "Preview response is too large.",
                        code="preview_response_too_large",
                    )
                chunks.append(chunk)
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
            )
        finally:
            await response.aclose()


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


def _configured_model_routes() -> list[str]:
    return sorted({
        value.strip()
        for value in os.getenv("CODING_WORKER_MODEL_ROUTES", "coding/default").split(",")
        if value.strip()
    })


def _validate_model_route(model_route: str) -> None:
    configured = set(_configured_model_routes())
    if model_route not in configured:
        raise HTTPException(
            status_code=400,
            detail={"code": "model_route_not_allowed", "message": "Model route is not allowed."},
        )


def _encode_sse(event_type: str, payload: dict[str, Any], sequence: int) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event_type}\ndata: {encoded}\n\n"
