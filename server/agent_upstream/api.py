from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .models import (
    TERMINAL_STATUSES,
    EngineShadowEvent,
    EngineShadowRunCreate,
    EngineShadowRunDetail,
    EngineShadowRunRecord,
    EngineShadowWorkspaceEntry,
)
from .managed_gateway import ManagedShadowGateway
from .service import EngineShadowService, EngineShadowServiceError
from .store import (
    EngineShadowConflict,
    EngineShadowNotFound,
    EngineShadowStoreError,
)


_service: EngineShadowService | None = None
_enabled_override: bool | None = None


@asynccontextmanager
async def _engine_shadow_lifespan(_app):
    yield
    global _service
    if _service is not None:
        await _service.shutdown()


router = APIRouter(
    prefix="/api/agent-workspace/apps/engine-shadow-runs",
    tags=["agent-upstream-shadow"],
    lifespan=_engine_shadow_lifespan,
)


def is_engine_shadow_enabled() -> bool:
    if _enabled_override is not None:
        return _enabled_override
    return os.getenv("AGENT_APP_ENGINE_SHADOW_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_engine_shadow_service() -> EngineShadowService:
    global _service
    if _service is None:
        try:
            from server.model_router.api import get_model_router_service
            from server.model_router.workload_control import (
                ProviderWorkloadCallService,
            )
        except ImportError:  # pragma: no cover - container package layout
            from model_router.api import get_model_router_service
            from model_router.workload_control import ProviderWorkloadCallService

        _service = EngineShadowService(
            managed_gateway=ManagedShadowGateway(
                ProviderWorkloadCallService(get_model_router_service())
            )
        )
    return _service


def set_engine_shadow_for_tests(
    service: EngineShadowService | None,
    *,
    enabled: bool | None = None,
) -> None:
    global _service, _enabled_override
    _service = service
    _enabled_override = enabled


def _require_enabled() -> None:
    if not is_engine_shadow_enabled():
        raise HTTPException(status_code=404, detail="Upstream Engine Shadow is disabled")


def _raise_shadow_error(exc: Exception) -> None:
    if isinstance(exc, EngineShadowServiceError):
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.public_message},
        ) from exc
    if isinstance(exc, EngineShadowNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, EngineShadowConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, EngineShadowStoreError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="Upstream Engine Shadow failed") from exc


@router.post("", response_model=EngineShadowRunRecord, status_code=202)
async def create_engine_shadow_run(
    payload: EngineShadowRunCreate,
) -> EngineShadowRunRecord:
    _require_enabled()
    try:
        return await get_engine_shadow_service().create_run(payload)
    except Exception as exc:
        _raise_shadow_error(exc)


@router.get("")
async def list_engine_shadow_runs(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, list[EngineShadowRunRecord]]:
    _require_enabled()
    try:
        return {"runs": await asyncio.to_thread(get_engine_shadow_service().list_runs, limit=limit)}
    except Exception as exc:
        _raise_shadow_error(exc)


@router.get("/{run_id}", response_model=EngineShadowRunDetail)
async def get_engine_shadow_run(run_id: str) -> EngineShadowRunDetail:
    _require_enabled()
    try:
        return await asyncio.to_thread(get_engine_shadow_service().get_detail, run_id)
    except Exception as exc:
        _raise_shadow_error(exc)


@router.post("/{run_id}/stop", response_model=EngineShadowRunRecord)
async def stop_engine_shadow_run(run_id: str) -> EngineShadowRunRecord:
    _require_enabled()
    try:
        return await get_engine_shadow_service().stop_run(run_id)
    except Exception as exc:
        _raise_shadow_error(exc)


@router.get("/{run_id}/events")
async def list_engine_shadow_events(
    run_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1_000),
) -> dict[str, list[EngineShadowEvent]]:
    _require_enabled()
    try:
        return {
            "events": await asyncio.to_thread(
                get_engine_shadow_service().list_events,
                run_id,
                after=after,
                limit=limit,
            )
        }
    except Exception as exc:
        _raise_shadow_error(exc)


@router.get("/{run_id}/events/stream")
async def stream_engine_shadow_events(
    request: Request,
    run_id: str,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    _require_enabled()
    try:
        await asyncio.to_thread(get_engine_shadow_service().get_detail, run_id)
    except Exception as exc:
        _raise_shadow_error(exc)

    async def generate() -> AsyncIterator[str]:
        cursor = after
        while True:
            events = await asyncio.to_thread(
                get_engine_shadow_service().list_events,
                run_id,
                after=cursor,
                limit=500,
            )
            for event in events:
                cursor = event.sequence
                data = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"id: {event.sequence}\nevent: shadow_event\ndata: {data}\n\n"
            detail = await asyncio.to_thread(
                get_engine_shadow_service().get_detail, run_id
            )
            if detail.run.status in TERMINAL_STATUSES and cursor >= detail.last_event_sequence:
                return
            if await request.is_disconnected():
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/workspace")
async def list_engine_shadow_workspace(
    run_id: str,
    path: str = Query(default="", max_length=1_024),
) -> dict[str, object]:
    _require_enabled()
    try:
        entries: list[EngineShadowWorkspaceEntry] = await asyncio.to_thread(
            get_engine_shadow_service().list_workspace, run_id, path
        )
        return {"path": path, "entries": entries}
    except Exception as exc:
        _raise_shadow_error(exc)


@router.get("/{run_id}/workspace/file")
async def read_engine_shadow_workspace_file(
    run_id: str,
    path: str = Query(min_length=1, max_length=1_024),
) -> dict[str, object]:
    _require_enabled()
    try:
        content, size = await asyncio.to_thread(
            get_engine_shadow_service().read_workspace_file, run_id, path
        )
        return {"path": path, "content": content, "size": size}
    except Exception as exc:
        _raise_shadow_error(exc)
