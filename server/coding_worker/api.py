from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import PurePosixPath
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
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
    TaskCapabilities,
    TaskRecord,
    TERMINAL_STATES,
    WorkerCapabilities,
    WorkerCapabilityReason,
    WorkerCapabilityStatus,
    WorkerFeatureName,
    WorkerApproval,
    WorkerArtifact,
    WorkerChangeset,
    WorkerEvidence,
    WorkerDiagnostic,
    WorkerPlan,
    WorkerTodo,
    WorkerQuestion,
    WorkerQuestionAnswer,
    WorkerTurnHistory,
    WorkerTaskExport,
    OperationOutputChunk,
    SubtaskRecord,
    SubtaskRequest,
)
from .ports import (
    CodingSubstrateError,
    CodingSubstrateHandle,
    EvaluationAdapter,
    HarnessCapabilities,
    InteractionProjection,
)
from .runtime import CodingWorkerRuntime, build_runtime_from_environment


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


class HarnessFaultRequest(StrictModel):
    task_id: str = Field(pattern=r"^task_[a-f0-9]{32}$")
    component: Literal["executor"]
    point: Literal["after_side_effect_before_receipt"]


class TaskChildrenResponse(StrictModel):
    tasks: tuple[TaskRecord, ...]
    subtasks: tuple[SubtaskRecord, ...] = ()


def _require_harness_controller(request: Request) -> None:
    if not _feature_enabled("CODING_WORKER_HARNESS_V3_ENABLED"):
        raise HTTPException(status_code=404, detail="Not found")
    expected = os.getenv("CODING_WORKER_HARNESS_CONTROLLER_TOKEN", "")
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if len(expected) < 32 or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


_substrate: CodingSubstrateHandle | None = None
_enabled_override: bool | None = None
_runtime: CodingWorkerRuntime | None = None
_startup_error: str | None = None
_CONSOLE_ORIGIN = Origin(module="worker-console", object_id="local-user")


@asynccontextmanager
async def _lifespan(_app: object) -> AsyncIterator[None]:
    global _substrate, _runtime, _startup_error
    if is_coding_worker_enabled() and _substrate is None:
        try:
            _runtime = build_runtime_from_environment()
            _substrate = await _runtime.start()
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
            _substrate = None


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


def _feature_flag_enabled(feature: WorkerFeatureName) -> bool:
    task_runtime = is_coding_worker_enabled()
    v15 = task_runtime and _feature_enabled("CODING_WORKER_V15_ENABLED")
    v16 = task_runtime and _feature_enabled("CODING_WORKER_V16_ENABLED")
    flags = {
        WorkerFeatureName.TASK_RUNTIME: task_runtime,
        WorkerFeatureName.PROFESSIONAL_FILE_TOOLS: v15,
        WorkerFeatureName.SHELL: (
            v15 and _feature_enabled("CODING_WORKER_SHELL_ENABLED")
        ),
        WorkerFeatureName.OPERATION_OUTPUT: v15,
        WorkerFeatureName.CHANGESETS: v15,
        WorkerFeatureName.CODE_INTELLIGENCE: (
            v15 and _feature_enabled("CODING_WORKER_CODE_INTELLIGENCE_ENABLED")
        ),
        WorkerFeatureName.STRUCTURED_PLAN: (
            v16 and _feature_enabled("CODING_WORKER_INTERACTION_ENABLED")
        ),
        WorkerFeatureName.USER_QUESTIONS: (
            v16 and _feature_enabled("CODING_WORKER_INTERACTION_ENABLED")
        ),
        WorkerFeatureName.CONTEXT_COMPACTION: v16,
        WorkerFeatureName.TURN_HISTORY: (
            v16 and _feature_enabled("CODING_WORKER_SESSION_CONTROLS_ENABLED")
        ),
        WorkerFeatureName.SUBTASKS: (
            v16 and _feature_enabled("CODING_WORKER_SUBAGENTS_ENABLED")
        ),
    }
    return flags[feature]


def _provider_supports_feature(
    capabilities: HarnessCapabilities, feature: WorkerFeatureName
) -> bool:
    tools = set(capabilities.tool_names)
    if feature is WorkerFeatureName.TASK_RUNTIME:
        return (
            capabilities.supports_streaming
            and capabilities.supports_checkpoint
            and capabilities.supports_restore
        )
    if feature is WorkerFeatureName.PROFESSIONAL_FILE_TOOLS:
        return {
            "read_file_range",
            "glob_files",
            "search_regex",
            "apply_changeset",
        }.issubset(tools)
    if feature is WorkerFeatureName.SHELL:
        return "run_shell" in tools
    if feature is WorkerFeatureName.OPERATION_OUTPUT:
        return "read_operation_output" in tools
    if feature is WorkerFeatureName.CHANGESETS:
        return "apply_changeset" in tools
    if feature is WorkerFeatureName.CODE_INTELLIGENCE:
        return "code_diagnostics" in tools
    if feature is WorkerFeatureName.STRUCTURED_PLAN:
        return "update_plan" in tools
    if feature is WorkerFeatureName.USER_QUESTIONS:
        return "request_user_input" in tools
    if feature is WorkerFeatureName.CONTEXT_COMPACTION:
        return "compact_context" in tools
    if feature is WorkerFeatureName.TURN_HISTORY:
        return True
    if feature is WorkerFeatureName.SUBTASKS:
        return {"create_subtask", "merge_subtask"}.issubset(tools)
    return False


def coding_worker_capabilities() -> WorkerCapabilities:
    harness_capabilities = (
        _substrate.control_plane.cached_harness_capabilities()
        if _substrate is not None
        else ()
    )

    def available(feature: WorkerFeatureName) -> bool:
        if not _feature_flag_enabled(feature):
            return False
        if feature is WorkerFeatureName.TURN_HISTORY:
            return bool(harness_capabilities)
        return any(
            _provider_supports_feature(capabilities, feature)
            for capabilities in harness_capabilities
        )

    return WorkerCapabilities(
        task_runtime=available(WorkerFeatureName.TASK_RUNTIME),
        professional_file_tools=available(
            WorkerFeatureName.PROFESSIONAL_FILE_TOOLS
        ),
        shell=available(WorkerFeatureName.SHELL),
        operation_output=available(WorkerFeatureName.OPERATION_OUTPUT),
        changesets=available(WorkerFeatureName.CHANGESETS),
        code_intelligence=available(WorkerFeatureName.CODE_INTELLIGENCE),
        structured_plan=available(WorkerFeatureName.STRUCTURED_PLAN),
        user_questions=available(WorkerFeatureName.USER_QUESTIONS),
        context_compaction=available(WorkerFeatureName.CONTEXT_COMPACTION),
        turn_history=available(WorkerFeatureName.TURN_HISTORY),
        subtasks=available(WorkerFeatureName.SUBTASKS),
    )


def _task_capability_status(
    *,
    feature: WorkerFeatureName,
    snapshot: HarnessCapabilities | None,
    snapshot_exists: bool,
    current: HarnessCapabilities | None,
    binding_matches: bool,
    current_reason: str | None,
    v17_task: bool = False,
) -> WorkerCapabilityStatus:
    enabled = _feature_flag_enabled(feature)
    platform_owned = feature is WorkerFeatureName.TURN_HISTORY
    basic_runtime = feature is WorkerFeatureName.TASK_RUNTIME
    supported = platform_owned or (
        (snapshot is not None and _provider_supports_feature(snapshot, feature))
        or (
            basic_runtime
            and not snapshot_exists
            and current is not None
            and _provider_supports_feature(current, feature)
        )
    )
    if (
        v17_task
        and basic_runtime
        and snapshot is not None
        and not snapshot.supports_turn_interrupt
    ):
        supported = False
    reason: WorkerCapabilityReason | None = None
    if not enabled:
        reason = WorkerCapabilityReason.FEATURE_DISABLED
    elif platform_owned:
        reason = (
            None
            if _substrate is not None
            else WorkerCapabilityReason.PROVIDER_UNAVAILABLE
        )
    elif not snapshot_exists and not basic_runtime:
        reason = WorkerCapabilityReason.LEGACY_TASK
    elif basic_runtime and not snapshot_exists:
        if current is None:
            reason = (
                WorkerCapabilityReason.ROUTE_UNAVAILABLE
                if current_reason == "route_unavailable"
                else WorkerCapabilityReason.PROVIDER_UNAVAILABLE
            )
        elif not supported:
            reason = WorkerCapabilityReason.PROVIDER_UNSUPPORTED
    elif snapshot is None:
        reason = (
            WorkerCapabilityReason.ROUTE_UNAVAILABLE
            if current_reason == "route_unavailable"
            else WorkerCapabilityReason.PROVIDER_UNAVAILABLE
        )
    elif not supported:
        reason = WorkerCapabilityReason.PROVIDER_UNSUPPORTED
    elif not binding_matches:
        reason = WorkerCapabilityReason.PROVIDER_BINDING_CHANGED
    elif current is None:
        reason = (
            WorkerCapabilityReason.ROUTE_UNAVAILABLE
            if current_reason == "route_unavailable"
            else WorkerCapabilityReason.PROVIDER_UNAVAILABLE
        )
    elif not _provider_supports_feature(current, feature) or (
        v17_task and basic_runtime and not current.supports_turn_interrupt
    ):
        reason = WorkerCapabilityReason.PROVIDER_UNSUPPORTED
    return WorkerCapabilityStatus(
        name=feature,
        enabled=enabled,
        supported=supported,
        available=reason is None,
        reason=reason,
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
    substrate: CodingSubstrateHandle | None,
    *,
    enabled: bool | None = None,
) -> None:
    global _substrate, _enabled_override, _startup_error
    _substrate = substrate
    _enabled_override = enabled
    _startup_error = None


def _get_substrate() -> CodingSubstrateHandle:
    _require_enabled()
    if _substrate is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "coding_worker_provider_unavailable",
                "message": "The V14 Worker provider is unavailable.",
                "reason": _startup_error,
            },
        )
    return _substrate


def _get_evaluation_adapter() -> EvaluationAdapter:
    evaluation = _get_substrate().evaluation
    if evaluation is None or not evaluation.enabled:
        raise HTTPException(status_code=404, detail="Not found")
    return evaluation


def _require_enabled() -> None:
    if not is_coding_worker_enabled():
        raise HTTPException(status_code=404, detail="Coding Worker V14 is disabled")


def _raise_worker_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    kind = type(exc).__name__
    if kind == "WorkspaceSourceUnavailableError":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workspace_source_unavailable",
                "message": "Workspace source is unavailable.",
                "reason": getattr(exc, "reason", "temporarily_unavailable"),
            },
        ) from exc
    code = getattr(exc, "code", "coding_worker_failed")
    if kind == "WorkerNotFoundError":
        status = 404
    elif kind == "WorkerConflictError":
        status = 409
    elif kind == "WorkspaceError" and code in {
        "workspace_not_found",
        "entry_not_found",
    }:
        status = 404
    elif kind in {"WorkerStoreError", "WorkspaceError"}:
        status = 400
    elif isinstance(exc, CodingSubstrateError):
        status = exc.status
    else:
        status = 500
    raise HTTPException(
        status_code=status,
        detail={"code": code, "message": str(exc)},
    ) from exc


@router.get("")
async def coding_worker_status() -> dict[str, Any]:
    enabled = is_coding_worker_enabled()
    if _substrate is not None:
        with suppress(Exception):
            await _substrate.control_plane.refresh_harness_capabilities()
    capabilities = coding_worker_capabilities()
    status = (
        _substrate.control_plane.status()
        if _substrate is not None
        else None
    )
    return {
        "enabled": enabled,
        "available": capabilities.task_runtime,
        "version": "v1",
        "max_active_tasks": status.max_active_tasks if status is not None else 2,
        "retention_seconds": status.retention_seconds if status is not None else 604800,
        "network_enabled": status.network_enabled if status is not None else False,
        "acceptance_checks": list(status.acceptance_checks) if status is not None else [],
        "model_routes": _configured_model_routes(),
        "reason": _startup_error,
        "capabilities": capabilities.model_dump(mode="json"),
    }


@router.get("/capabilities", response_model=WorkerCapabilities)
async def get_worker_capabilities() -> WorkerCapabilities:
    if _substrate is not None:
        with suppress(Exception):
            await _substrate.control_plane.refresh_harness_capabilities()
    return coding_worker_capabilities()


@router.post("/tasks", response_model=TaskRecord, status_code=202)
async def create_task(payload: TaskCreateRequest) -> TaskRecord:
    try:
        substrate = _get_substrate()
        existing = substrate.projection.find_task_by_idempotency(
            _CONSOLE_ORIGIN, payload.client_task_id
        )
        if existing is None:
            try:
                _validate_model_route(payload.model_route)
            except HTTPException:
                if substrate.projection.find_task_by_idempotency(
                    _CONSOLE_ORIGIN, payload.client_task_id
                ) is None:
                    raise
        return await substrate.control_plane.create_task(_CONSOLE_ORIGIN, payload)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks", response_model=dict[str, list[TaskRecord]])
async def list_tasks() -> dict[str, list[TaskRecord]]:
    try:
        return {"tasks": list(_get_substrate().projection.list_tasks(origin=_CONSOLE_ORIGIN))}
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}", response_model=TaskRecord)
async def get_task(task_id: str) -> TaskRecord:
    try:
        return _get_substrate().projection.get_task(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/capabilities", response_model=TaskCapabilities)
async def get_task_capabilities(task_id: str) -> TaskCapabilities:
    try:
        substrate = _get_substrate()
        task = substrate.projection.get_task(task_id)
        persisted = substrate.projection.get_task_capability_snapshot(task_id)
        observation = await substrate.control_plane.harness_capability_observation(
            task.spec.model_route
        )
        snapshot_capabilities: HarnessCapabilities | None = None
        if persisted is not None:
            raw_capabilities = persisted.snapshot.get("capabilities")
            if raw_capabilities is not None:
                snapshot_capabilities = HarnessCapabilities.model_validate(
                    raw_capabilities
                )
        binding_matches = (
            persisted is not None
            and persisted.binding_sha256 == observation.binding_sha256
        )
        return TaskCapabilities(
            task_id=task_id,
            observed_at=observation.observed_at,
            expires_at=observation.expires_at,
            capabilities=tuple(
                _task_capability_status(
                    feature=feature,
                    snapshot=snapshot_capabilities,
                    snapshot_exists=persisted is not None,
                    current=observation.capabilities,
                    binding_matches=binding_matches,
                    current_reason=observation.reason,
                    v17_task=task.runtime_protocol.value == "v17",
                )
                for feature in WorkerFeatureName
            ),
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/events")
async def task_events(
    task_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    projection = _get_substrate().projection
    try:
        projection.get_task(task_id)
    except Exception as exc:
        _raise_worker_error(exc)

    async def stream() -> AsyncIterator[str]:
        cursor = after
        while not await request.is_disconnected():
            events = projection.list_events(task_id, after=cursor)
            for event in events:
                cursor = event.sequence
                yield _encode_sse(event.type, event.model_dump(mode="json"), event.sequence)
            task = projection.get_task(task_id)
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
        return await _get_substrate().control_plane.append_message(task_id, payload.message)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/plan", response_model=WorkerPlan | None)
async def task_plan(task_id: str) -> WorkerPlan | None:
    _require_interaction_enabled()
    try:
        return _get_substrate().projection.latest_plan(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/todo", response_model=WorkerTodo | None)
async def task_todo(task_id: str) -> WorkerTodo | None:
    _require_interaction_enabled()
    try:
        return _get_substrate().projection.latest_todo(task_id)
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
            "questions": list(_get_substrate().projection.list_questions(task_id))
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
        return await _get_substrate().control_plane.answer_question(
            task_id, question_id, payload
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/approvals", response_model=dict[str, list[WorkerApproval]])
async def task_approvals(task_id: str) -> dict[str, list[WorkerApproval]]:
    try:
        return {"approvals": list(_get_substrate().projection.list_approvals(task_id))}
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/approvals", response_model=WorkerApproval)
async def decide_task_approval(
    task_id: str, payload: ApprovalDecisionRequest
) -> WorkerApproval:
    try:
        return _get_substrate().control_plane.decide_approval(
            task_id,
            payload.approval_id,
            approved=payload.decision != "reject",
            task_scope=payload.decision == "approve_task",
            ttl_seconds=payload.ttl_seconds,
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/evidence", response_model=dict[str, list[WorkerEvidence]]
)
async def task_evidence(task_id: str) -> dict[str, list[WorkerEvidence]]:
    try:
        projection = _get_substrate().projection
        tree_hash = projection.current_tree_hash(task_id)
        return {
            "evidence": list(projection.list_evidence(
                task_id, current_tree_hash=tree_hash
            ))
        }
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/artifacts", response_model=dict[str, list[WorkerArtifact]]
)
async def task_artifacts(task_id: str) -> dict[str, list[WorkerArtifact]]:
    try:
        return {"artifacts": list(_get_substrate().projection.list_artifacts(task_id))}
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
    projection = _get_substrate().projection
    try:
        operation = projection.get_operation(operation_id)
        if operation.task_id != task_id:
            raise CodingSubstrateError(
                "Operation was not found.",
                code="operation_not_found",
                status=404,
            )
        chunks: list[OperationOutputChunk] = []
        cursor = after
        scanned = 0
        while len(chunks) < 256 and scanned < 10_000:
            events = projection.list_events(task_id, after=cursor, limit=1000)
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
    projection: InteractionProjection,
    task_id: str,
    operation_id: str,
) -> CodeIntelligenceSnapshot:
    task = projection.get_task(task_id)
    operation = projection.get_operation(operation_id)
    operation_kind = {
        "code_symbols": "symbols",
        "code_definition": "definition",
        "code_references": "references",
        "code_hover": "hover",
        "code_diagnostics": "diagnostics",
    }.get(operation.tool_name)
    if operation.task_id != task_id or operation_kind is None:
        raise CodingSubstrateError(
            "Code intelligence result was not found.",
            code="code_intelligence_not_found",
            status=404,
        )
    if operation.state is not OperationState.COMPLETED:
        raise CodingSubstrateError(
            "Code intelligence result is not available.",
            code="code_intelligence_result_unavailable",
            status=409,
        )
    if task.workspace_id is None:
        raise CodingSubstrateError(
            "Task workspace is unavailable.",
            code="workspace_unavailable",
            status=409,
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
        raise CodingSubstrateError(
            "Code intelligence result is corrupt.",
            code="worker_data_corrupt",
            status=400,
        )
    current_tree_hash = projection.current_tree_hash(task_id)
    if current_tree_hash is None:
        raise CodingSubstrateError(
            "Task workspace is unavailable.",
            code="workspace_unavailable",
            status=409,
        )
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
        raise CodingSubstrateError(
            "Code intelligence result is corrupt.",
            code="worker_data_corrupt",
            status=400,
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
            _get_substrate().projection, task_id, operation_id
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
            _get_substrate().projection, task_id, operation_id
        )
        if snapshot.operation != "diagnostics":
            raise CodingSubstrateError(
                "Diagnostics were not found.",
                code="diagnostics_not_found",
                status=404,
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
            raise CodingSubstrateError(
                "Diagnostics result is corrupt.",
                code="worker_data_corrupt",
                status=400,
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
            CodingSubstrateError(
                "Diagnostics result is corrupt.",
                code="worker_data_corrupt",
                status=400,
            )
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get(
    "/tasks/{task_id}/changesets/{operation_id}",
    response_model=WorkerChangeset,
)
async def task_changeset(task_id: str, operation_id: str) -> WorkerChangeset:
    try:
        operation = _get_substrate().projection.get_operation(operation_id)
        if operation.task_id != task_id:
            raise CodingSubstrateError(
                "Changeset was not found.",
                code="changeset_not_found",
                status=404,
            )
        value = (operation.result or {}).get("changeset")
        if not isinstance(value, dict):
            raise CodingSubstrateError(
                "Changeset result is not available.",
                code="changeset_result_unavailable",
                status=409,
            )
        return WorkerChangeset.model_validate(value)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
async def task_artifact(task_id: str, artifact_id: str) -> Response:
    try:
        projection = _get_substrate().projection
        artifact = next(
            (
                item
                for item in projection.list_artifacts(task_id)
                if item.artifact_id == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise CodingSubstrateError(
                "Artifact was not found.", code="artifact_not_found", status=404
            )
        content = projection.read_artifact(task_id, artifact_id)
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
        return await _get_substrate().control_plane.pause(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/turns", response_model=WorkerTurnHistory)
async def task_turn_history(task_id: str) -> WorkerTurnHistory:
    _require_session_controls_enabled()
    try:
        return _get_substrate().projection.turn_history(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/undo", response_model=WorkerTurnHistory)
async def undo_task_turn(task_id: str) -> WorkerTurnHistory:
    _require_session_controls_enabled()
    try:
        return await _get_substrate().control_plane.navigate_turn(task_id, "undo")
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/redo", response_model=WorkerTurnHistory)
async def redo_task_turn(task_id: str) -> WorkerTurnHistory:
    _require_session_controls_enabled()
    try:
        return await _get_substrate().control_plane.navigate_turn(task_id, "redo")
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/fork", response_model=TaskRecord, status_code=202)
async def fork_task(task_id: str, payload: TaskForkRequest) -> TaskRecord:
    _require_session_controls_enabled()
    try:
        return await _get_substrate().control_plane.fork_task(
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
        return await _get_substrate().control_plane.create_subtask(task_id, payload)
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
        return await _get_substrate().control_plane.merge_subtask(
            task_id, child_task_id, payload.operation_id
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/children", response_model=TaskChildrenResponse)
async def task_children(task_id: str) -> TaskChildrenResponse:
    _require_children_enabled()
    try:
        projection = _get_substrate().projection
        return TaskChildrenResponse(
            tasks=tuple(projection.list_children(task_id)),
            subtasks=tuple(projection.list_subtasks(task_id)),
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/export", response_model=WorkerTaskExport)
async def export_task(task_id: str) -> WorkerTaskExport:
    _require_session_controls_enabled()
    try:
        return await _get_substrate().control_plane.export_task(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/resume", response_model=TaskRecord, status_code=202)
async def resume_task(task_id: str) -> TaskRecord:
    try:
        return await _get_substrate().control_plane.resume(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/cancel", response_model=TaskRecord)
async def cancel_task(task_id: str) -> TaskRecord:
    try:
        return await _get_substrate().control_plane.cancel(task_id)
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/tasks/{task_id}/pin", response_model=TaskRecord)
async def pin_task(task_id: str) -> TaskRecord:
    try:
        return _get_substrate().control_plane.set_pinned(task_id, True)
    except Exception as exc:
        _raise_worker_error(exc)


@router.delete("/tasks/{task_id}/pin", response_model=TaskRecord)
async def unpin_task(task_id: str) -> TaskRecord:
    try:
        return _get_substrate().control_plane.set_pinned(task_id, False)
    except Exception as exc:
        _raise_worker_error(exc)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str) -> Response:
    try:
        _get_substrate().control_plane.delete_task(task_id)
        return Response(status_code=204)
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/workspace/tree")
async def workspace_tree(task_id: str) -> dict[str, Any]:
    try:
        return _get_substrate().projection.workspace_tree(task_id).model_dump(
            mode="json"
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/workspace/entries/{entry_id}")
async def workspace_entry(task_id: str, entry_id: str) -> Response:
    try:
        content = _get_substrate().projection.read_workspace_entry(
            task_id, entry_id
        )
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise CodingSubstrateError(
                "Binary entries are not available as text previews.",
                code="preview_unavailable",
                status=400,
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
    try:
        return Response(
            _get_substrate().projection.workspace_diff(task_id),
            media_type="text/x-diff; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.post(
    "/tasks/{task_id}/workspace/parity-export",
    response_model=WorkerArtifact,
)
async def export_parity_workspace(task_id: str) -> WorkerArtifact:
    """Create an opaque, deterministic terminal Workspace artifact for Checker.

    The endpoint is absent unless the isolated parity profile is enabled. It
    never exposes a Workspace path and cannot export a still-running task.
    """

    parity_enabled = _feature_enabled("CODING_WORKER_PARITY_ENABLED")
    harness_v3_enabled = _feature_enabled("CODING_WORKER_HARNESS_V3_ENABLED")
    if not (parity_enabled or harness_v3_enabled):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return _get_evaluation_adapter().export_workspace(
            task_id, harness_v3=harness_v3_enabled
        )
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/harness/attestation")
async def harness_attestation(request: Request) -> dict[str, Any]:
    _require_harness_controller(request)
    try:
        return dict(await _get_evaluation_adapter().attestation())
    except Exception as exc:
        _raise_worker_error(exc)


@router.post("/harness/faults", status_code=202)
async def arm_harness_fault(
    payload: HarnessFaultRequest, request: Request
) -> dict[str, str]:
    _require_harness_controller(request)
    try:
        _get_evaluation_adapter().arm_fault(
            payload.task_id, payload.component, payload.point
        )
        return {"status": "armed"}
    except Exception as exc:
        _raise_worker_error(exc)


@router.get("/tasks/{task_id}/services/{service_id}/preview/{preview_path:path}")
async def service_preview(
    task_id: str,
    service_id: str,
    preview_path: str,
    request: Request,
) -> Response:
    try:
        path = PurePosixPath(preview_path or ".")
        if "\\" in preview_path or any(part == ".." for part in path.parts):
            raise CodingSubstrateError(
                "Preview path is invalid.",
                code="preview_path_invalid",
                status=409,
            )
        result = await _get_substrate().control_plane.preview_service_status(
            task_id, service_id
        )
        port = result.preview_port
        if result.state != "running" or port is None:
            raise CodingSubstrateError(
                "Preview service is not running.",
                code="preview_unavailable",
                status=409,
            )
        slot_id = result.slot_id
        host = os.getenv(
            f"CODING_WORKER_{slot_id.replace('-', '_').upper()}_PREVIEW_HOST",
            f"coding-worker-{slot_id}",
        )
        query = request.url.query
        upstream = await _fetch_preview(
            f"http://{host}:{port}/{preview_path}" + (f"?{query}" if query else "")
        )
        if 300 <= upstream.status_code < 400:
            raise CodingSubstrateError(
                "Preview redirects are not allowed.",
                code="preview_redirect_denied",
                status=409,
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
                    raise CodingSubstrateError(
                        "Preview response is too large.",
                        code="preview_response_too_large",
                        status=409,
                    )
                chunks.append(chunk)
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
            )
        finally:
            await response.aclose()


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
