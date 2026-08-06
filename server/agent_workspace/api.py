from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException

from .models import (
    AgentCreateRequest,
    AgentListResponse,
    AgentPayload,
    AgentResetRequest,
    AgentUpdateRequest,
)
from .store import (
    AgentConflictError,
    AgentNotFoundError,
    AgentStateStore,
    AgentStateValidationError,
    AgentWorkspaceError,
)


router = APIRouter(prefix="/api/agent-workspace", tags=["agent-workspace"])
_store: AgentStateStore | None = None
_enabled_override: bool | None = None


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


def set_agent_workspace_for_tests(
    store: AgentStateStore | None,
    *,
    enabled: bool | None = None,
) -> None:
    global _store, _enabled_override
    _store = store
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


@router.get("/status")
async def get_agent_workspace_status() -> dict[str, object]:
    return {
        "enabled": is_agent_workspace_enabled(),
        "version": "agent-workspace-r1",
        "runtime_enabled": False,
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
