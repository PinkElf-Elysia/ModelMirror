from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from .models import (
    AgentCreateRequest,
    AgentListResponse,
    AgentPayload,
    AgentResetRequest,
    AgentUpdateRequest,
)
from .runtime import AgentRuntimeError, AgentRuntimeService
from .runtime_models import (
    ApprovalDecisionRequest,
    GenerateAgentRequest,
    SessionCreateRequest,
    SessionDetail,
    SessionRecord,
    SessionUpdateRequest,
    TaskCreateRequest,
    TaskRecord,
    WorkspaceEntry,
)
from .runtime_store import (
    AgentRuntimeStore,
    RuntimeConflictError,
    RuntimeNotFoundError,
    RuntimeStoreError,
)
from .store import (
    AgentConflictError,
    AgentNotFoundError,
    AgentStateStore,
    AgentStateValidationError,
    AgentWorkspaceError,
)
from .tools import BuiltinToolRunner, ToolExecutionError


_store: AgentStateStore | None = None
_runtime_service: AgentRuntimeService | None = None
_enabled_override: bool | None = None


@asynccontextmanager
async def _agent_workspace_lifespan(_app):
    yield
    global _runtime_service
    if _runtime_service is not None:
        await _runtime_service.shutdown()


router = APIRouter(
    prefix="/api/agent-workspace",
    tags=["agent-workspace"],
    lifespan=_agent_workspace_lifespan,
)


def is_agent_workspace_enabled() -> bool:
    if _enabled_override is not None:
        return _enabled_override
    return os.getenv("AGENT_WORKSPACE_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_agent_state_store() -> AgentStateStore:
    global _store
    if _store is None:
        _store = AgentStateStore()
    return _store


def get_agent_runtime_service() -> AgentRuntimeService:
    global _runtime_service
    if _runtime_service is None:
        state_store = get_agent_state_store()
        _runtime_service = AgentRuntimeService(
            state_store=state_store,
            runtime_store=AgentRuntimeStore(state_store.root),
        )
    return _runtime_service


def set_agent_workspace_for_tests(
    store: AgentStateStore | None,
    *,
    enabled: bool | None = None,
    runtime_service: AgentRuntimeService | None = None,
) -> None:
    global _store, _runtime_service, _enabled_override
    _store = store
    _runtime_service = runtime_service
    _enabled_override = enabled


def _require_enabled() -> None:
    if not is_agent_workspace_enabled():
        raise HTTPException(status_code=404, detail="Agent Workspace is disabled")


def _raise_store_error(exc: AgentWorkspaceError) -> None:
    if isinstance(exc, AgentNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AgentConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, AgentStateValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


def _raise_runtime_error(exc: Exception) -> None:
    if isinstance(exc, RuntimeNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, RuntimeConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (AgentRuntimeError, RuntimeStoreError, ToolExecutionError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, AgentWorkspaceError):
        _raise_store_error(exc)
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/status")
async def get_agent_workspace_status() -> dict[str, object]:
    return {
        "enabled": is_agent_workspace_enabled(),
        "version": "agent-workspace-r2",
        "runtime_enabled": is_agent_workspace_enabled(),
    }


@router.get("/agents", response_model=AgentListResponse)
async def list_agents() -> AgentListResponse:
    _require_enabled()
    try:
        agents = await asyncio.to_thread(get_agent_state_store().list_agents)
        return AgentListResponse(agents=agents)
    except AgentWorkspaceError as exc:
        _raise_store_error(exc)


@router.post("/agents", response_model=AgentPayload, status_code=201)
async def create_agent(payload: AgentCreateRequest) -> AgentPayload:
    _require_enabled()
    try:
        return await asyncio.to_thread(get_agent_state_store().create_agent, payload)
    except AgentWorkspaceError as exc:
        _raise_store_error(exc)


@router.get("/agents/{agent_id}", response_model=AgentPayload)
async def get_agent(agent_id: str) -> AgentPayload:
    _require_enabled()
    try:
        return await asyncio.to_thread(get_agent_state_store().get_agent, agent_id)
    except AgentWorkspaceError as exc:
        _raise_store_error(exc)


@router.put("/agents/{agent_id}", response_model=AgentPayload)
async def update_agent(agent_id: str, payload: AgentUpdateRequest) -> AgentPayload:
    _require_enabled()
    try:
        return await asyncio.to_thread(
            get_agent_state_store().update_agent,
            agent_id,
            expected_revision=payload.expected_revision,
            config=payload.config,
            agents_md=payload.agents_md,
        )
    except AgentWorkspaceError as exc:
        _raise_store_error(exc)


@router.post("/agents/{agent_id}/reset", response_model=AgentPayload)
async def reset_agent(agent_id: str, payload: AgentResetRequest) -> AgentPayload:
    _require_enabled()
    try:
        return await asyncio.to_thread(
            get_agent_state_store().reset_agent_config,
            agent_id,
            expected_revision=payload.expected_revision,
        )
    except AgentWorkspaceError as exc:
        _raise_store_error(exc)


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str) -> dict[str, bool]:
    _require_enabled()
    try:
        await asyncio.to_thread(get_agent_state_store().delete_agent, agent_id)
        return {"ok": True}
    except AgentWorkspaceError as exc:
        _raise_store_error(exc)


@router.get("/sessions")
async def list_sessions() -> dict[str, list[SessionRecord]]:
    _require_enabled()
    try:
        items = await asyncio.to_thread(get_agent_runtime_service().store.list_sessions)
        return {"sessions": items}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/sessions", response_model=SessionRecord, status_code=201)
async def create_session(payload: SessionCreateRequest) -> SessionRecord:
    _require_enabled()
    try:
        return await get_agent_runtime_service().create_session(payload)
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str) -> SessionDetail:
    _require_enabled()
    try:
        return await asyncio.to_thread(
            get_agent_runtime_service().store.get_session_detail, session_id
        )
    except Exception as exc:
        _raise_runtime_error(exc)


@router.patch("/sessions/{session_id}", response_model=SessionRecord)
async def update_session(
    session_id: str, payload: SessionUpdateRequest
) -> SessionRecord:
    _require_enabled()
    try:
        return await get_agent_runtime_service().update_session(
            session_id, payload
        )
    except Exception as exc:
        _raise_runtime_error(exc)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, bool]:
    _require_enabled()
    try:
        await asyncio.to_thread(
            get_agent_runtime_service().store.delete_session, session_id
        )
        return {"ok": True}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/sessions/{session_id}/subagents")
async def list_subagents(session_id: str) -> dict[str, list[SessionRecord]]:
    _require_enabled()
    try:
        items = await asyncio.to_thread(
            get_agent_runtime_service().store.list_children, session_id
        )
        return {"subagents": items}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post(
    "/sessions/{session_id}/tasks", response_model=TaskRecord, status_code=202
)
async def create_task(session_id: str, payload: TaskCreateRequest) -> TaskRecord:
    _require_enabled()
    try:
        return await get_agent_runtime_service().create_task(session_id, payload)
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/tasks/{task_id}", response_model=TaskRecord)
async def get_task(task_id: str) -> TaskRecord:
    _require_enabled()
    try:
        return await asyncio.to_thread(
            get_agent_runtime_service().store.get_task, task_id
        )
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/tasks/{task_id}/stop", response_model=TaskRecord)
async def stop_task(task_id: str) -> TaskRecord:
    _require_enabled()
    try:
        return await get_agent_runtime_service().stop_task(task_id)
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/tasks/{task_id}/retry-generation", response_model=TaskRecord, status_code=202)
async def retry_agent_generation(task_id: str) -> TaskRecord:
    _require_enabled()
    try:
        return await get_agent_runtime_service().retry_agent_generation(task_id)
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/agents/generate", status_code=202)
async def generate_agent(payload: GenerateAgentRequest) -> dict[str, object]:
    _require_enabled()
    try:
        session, task = await get_agent_runtime_service().generate_agent(payload)
        return {"session": session, "task": task}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/approvals/{approval_id}")
async def decide_approval(
    approval_id: str, payload: ApprovalDecisionRequest
) -> dict[str, object]:
    _require_enabled()
    try:
        item = await get_agent_runtime_service().decide_approval(
            approval_id,
            approved=payload.decision == "approve",
            message=payload.message,
        )
        return {"approval": item}
    except Exception as exc:
        _raise_runtime_error(exc)


def _encode_sse(event) -> str:
    payload = event.model_dump(mode="json")
    return (
        f"id: {event.sequence}\n"
        f"event: {event.type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


async def _session_event_stream(
    session_id: str,
    *,
    after: int,
    request: Request,
    follow: bool,
) -> AsyncIterator[str]:
    cursor = max(0, after)
    while True:
        if await request.is_disconnected():
            return
        try:
            events = await asyncio.to_thread(
                get_agent_runtime_service().store.list_events,
                session_id,
                after=cursor,
                limit=500,
            )
        except RuntimeNotFoundError:
            return
        for event in events:
            cursor = event.sequence
            yield _encode_sse(event)
        if not follow:
            return
        if not events:
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)


@router.get("/sessions/{session_id}/events")
async def stream_session_events(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    follow: bool = Query(default=True),
    last_event_id: int | None = Header(default=None),
) -> StreamingResponse:
    _require_enabled()
    try:
        await asyncio.to_thread(get_agent_runtime_service().store.get_session, session_id)
    except Exception as exc:
        _raise_runtime_error(exc)
    cursor = max(after, last_event_id or 0)
    return StreamingResponse(
        _session_event_stream(
            session_id, after=cursor, request=request, follow=follow
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


def _workspace_path(session_id: str, relative_path: str, *, directory: bool = False) -> Path:
    workspace = get_agent_runtime_service().store.session_workspace(session_id)
    if not relative_path.strip() and directory:
        return workspace.resolve(strict=True)
    path = BuiltinToolRunner.resolve_read(workspace, relative_path)
    if directory and not path.is_dir():
        raise ToolExecutionError("Workspace path is not a directory")
    return path


@router.get("/sessions/{session_id}/workspace")
async def list_workspace(
    session_id: str, path: str = Query(default="", max_length=2_000)
) -> dict[str, object]:
    _require_enabled()
    try:
        directory = await asyncio.to_thread(
            _workspace_path, session_id, path, directory=True
        )
        workspace = get_agent_runtime_service().store.session_workspace(session_id)
        entries = [
            WorkspaceEntry(
                name=item.name,
                path=item.relative_to(workspace).as_posix(),
                kind="directory" if item.is_dir() else "file",
                size=0 if item.is_dir() else item.stat().st_size,
                modified_at=item.stat().st_mtime,
            )
            for item in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        ]
        return {"path": path, "entries": entries}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/sessions/{session_id}/workspace/file")
async def read_workspace_file(
    session_id: str, path: str = Query(min_length=1, max_length=2_000)
) -> dict[str, object]:
    _require_enabled()
    try:
        file_path = await asyncio.to_thread(_workspace_path, session_id, path)
        if not file_path.is_file():
            raise ToolExecutionError("Workspace path is not a file")
        if file_path.stat().st_size > 512_000:
            raise ToolExecutionError("File is too large for text preview")
        content = await asyncio.to_thread(
            file_path.read_text, encoding="utf-8", errors="replace"
        )
        return {"path": path, "content": content, "size": file_path.stat().st_size}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/sessions/{session_id}/workspace/download")
async def download_workspace_file(
    session_id: str, path: str = Query(min_length=1, max_length=2_000)
) -> FileResponse:
    _require_enabled()
    try:
        file_path = await asyncio.to_thread(_workspace_path, session_id, path)
        if not file_path.is_file():
            raise ToolExecutionError("Workspace path is not a file")
        return FileResponse(file_path, filename=file_path.name)
    except Exception as exc:
        _raise_runtime_error(exc)
