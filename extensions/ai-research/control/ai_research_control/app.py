from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .models import EventListResponse, EventView, ReadyView, RunCreateRequest, RunListResponse, RunView
from .service import NotReady, ResearchService
from .store import IdempotencyConflict


settings = Settings.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = ResearchService(settings)
    app.state.research = service
    await service.start()
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(
    title="ModelMirror AI Research AR0",
    version="0.1.0-ar0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "ai-research-control", "testserver"],
)


def service(request: Request) -> ResearchService:
    return request.app.state.research


def to_view(run: dict[str, Any]) -> RunView:
    return RunView.model_validate(
        {
            "runId": run["run_id"],
            "fixtureId": run["fixture_id"],
            "caseId": run["case_id"],
            "tenantId": run["tenant_id"],
            "projectId": run["project_id"],
            "actorId": run["actor_id"],
            "phase": run["phase"],
            "outcome": run["outcome"],
            "inspectStatus": run["inspect_status"],
            "cancelRequested": run["cancel_requested"],
            "cancelApplied": run["cancel_applied"],
            "evidenceState": run["evidence_state"],
            "errorType": run["error_type"],
            "errorMessage": run["error_message"],
            "replayVerified": run["replay_verified"],
            "mlflowRunId": run["mlflow_run_id"],
            "createdAt": run["created_at"],
            "startedAt": run["started_at"],
            "terminalAt": run["terminal_at"],
            "updatedAt": run["updated_at"],
        }
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/readyz", response_model=ReadyView, response_model_by_alias=True)
async def readyz(request: Request) -> ReadyView:
    checks = await service(request).readiness()
    ready = all(value == "ready" for value in checks.values())
    if not ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ReadyView(status="not_ready", checks=checks).model_dump(by_alias=True),
        )
    return ReadyView(status="ready", checks=checks)


@app.get("/api/v1/module")
async def module_metadata() -> dict[str, Any]:
    boundary = json.loads(settings.module_boundary.read_text(encoding="utf-8"))
    source_lock = json.loads(settings.source_lock.read_text(encoding="utf-8"))
    return {
        "moduleId": boundary["moduleId"],
        "moduleVersion": boundary["moduleVersion"],
        "apiVersion": boundary["apiVersion"],
        "workerProtocolVersion": boundary["workerProtocolVersion"],
        "claimLevel": "harness_only",
        "packStatus": "fixture_only",
        "fixtures": boundary["allowedFixtures"],
        "runtimes": source_lock["runtimes"],
        "limitations": [
            "no model or provider connection",
            "no scientific EvalPack or score",
            "local single-tenant compatibility mode only",
        ],
    }


@app.post("/api/v1/runs", response_model=RunView, response_model_by_alias=True)
async def create_run(payload: RunCreateRequest, request: Request):
    internal = payload.model_dump()
    try:
        run, created = await service(request).create_run(internal)
    except NotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    view = to_view(run)
    return JSONResponse(
        status_code=201 if created else 200,
        content=view.model_dump(by_alias=True),
    )


@app.get("/api/v1/runs", response_model=RunListResponse, response_model_by_alias=True)
async def list_runs(
    request: Request,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RunListResponse:
    try:
        runs = await asyncio.to_thread(
            service(request).store.list, after_run_id=cursor, limit=limit + 1
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    has_more = len(runs) > limit
    visible = runs[:limit]
    return RunListResponse(
        items=[to_view(run) for run in visible],
        nextCursor=visible[-1]["run_id"] if has_more and visible else None,
    )


@app.get("/api/v1/runs/{run_id}", response_model=RunView, response_model_by_alias=True)
async def get_run(run_id: str, request: Request) -> RunView:
    try:
        run = await asyncio.to_thread(service(request).store.get, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return to_view(run)


@app.get(
    "/api/v1/runs/{run_id}/events",
    response_model=EventListResponse,
    response_model_by_alias=True,
)
async def get_events(
    run_id: str,
    request: Request,
    after_seq: Annotated[int, Query(alias="afterSeq", ge=0)] = 0,
) -> EventListResponse:
    try:
        events = await asyncio.to_thread(
            service(request).store.events, run_id, after_seq
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return EventListResponse(
        items=[
            EventView(
                sequence=event["sequence"],
                eventType=event["event_type"],
                payload=event["payload"],
                createdAt=event["created_at"],
            )
            for event in events
        ],
        nextSequence=events[-1]["sequence"] if events else after_seq,
    )


@app.post(
    "/api/v1/runs/{run_id}/cancel",
    response_model=RunView,
    response_model_by_alias=True,
)
async def cancel_run(run_id: str, request: Request) -> RunView:
    try:
        run = await service(request).cancel(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return to_view(run)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()
