from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models import (
    CaseId,
    EventListResponse,
    EventView,
    EvidenceState,
    EvidenceView,
    Outcome,
    Phase,
    ReadyView,
    RunCreateRequest,
    RunListResponse,
    RunSummaryResponse,
    RunView,
    SystemView,
)
from .evidence import EvidenceError
from .service import NotReady, ResearchService
from .store import IdempotencyConflict


settings = Settings.from_env()
ui_directory = Path(__file__).resolve().parents[1] / "ui-dist"


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
    title="ModelMirror AI Research",
    version="0.2.0-ar1",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "ai-research-control", "testserver"],
)
app.mount(
    "/assets",
    StaticFiles(directory=ui_directory / "assets", check_dir=True),
    name="research-console-assets",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/assets/") and response.status_code < 400:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
            "cancelRequestedAt": run.get("cancel_requested_at"),
            "cancelAppliedAt": run.get("cancel_applied_at"),
            "terminalAt": run["terminal_at"],
            "evidenceSyncedAt": run.get("evidence_synced_at"),
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
        "capabilities": {
            "fixtureExecution": True,
            "cancellation": True,
            "evidenceVerification": True,
            "inspectView": True,
            "mlflow": True,
            "modelEvaluation": False,
            "multiTenant": False,
        },
        "links": {
            "mlflow": settings.mlflow_public_url,
            "inspectView": settings.inspect_view_public_url,
        },
        "limitations": [
            "no model or provider connection",
            "no scientific EvalPack or score",
            "local single-tenant compatibility mode only",
        ],
    }


@app.get("/api/v1/system", response_model=SystemView, response_model_by_alias=True)
async def system_status(request: Request) -> SystemView:
    return SystemView.model_validate(await service(request).system_status())


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
    q: Annotated[str | None, Query(max_length=100)] = None,
    case_id: Annotated[CaseId | None, Query(alias="caseId")] = None,
    phase: Phase | None = None,
    outcome: Outcome | None = None,
    evidence_state: Annotated[EvidenceState | None, Query(alias="evidenceState")] = None,
) -> RunListResponse:
    try:
        runs = await asyncio.to_thread(
            service(request).store.list,
            after_run_id=cursor,
            limit=limit + 1,
            query=q,
            case_id=case_id,
            phase=phase,
            outcome=outcome,
            evidence_state=evidence_state,
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
    has_more = len(runs) > limit
    visible = runs[:limit]
    return RunListResponse(
        items=[to_view(run) for run in visible],
        nextCursor=visible[-1]["run_id"] if has_more and visible else None,
    )


@app.get(
    "/api/v1/runs/summary",
    response_model=RunSummaryResponse,
    response_model_by_alias=True,
)
async def run_summary(request: Request) -> RunSummaryResponse:
    value = await asyncio.to_thread(service(request).store.summary)
    return RunSummaryResponse.model_validate(
        {
            "total": value["total"],
            "phases": value["phases"],
            "outcomes": value["outcomes"],
            "evidenceStates": value["evidence_states"],
            "updatedAt": value["updated_at"],
        }
    )


@app.get("/api/v1/runs/{run_id}", response_model=RunView, response_model_by_alias=True)
async def get_run(run_id: str, request: Request) -> RunView:
    try:
        run = await asyncio.to_thread(service(request).store.get, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return to_view(run)


@app.get(
    "/api/v1/runs/{run_id}/evidence",
    response_model=EvidenceView,
    response_model_by_alias=True,
)
async def get_evidence(run_id: str, request: Request) -> EvidenceView:
    try:
        value = await service(request).evidence(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return EvidenceView.model_validate(value)


@app.get("/api/v1/runs/{run_id}/artifacts/{artifact_name}")
async def download_artifact(run_id: str, artifact_name: str, request: Request) -> Response:
    try:
        content, digest = await service(request).artifact(run_id, artifact_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except EvidenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{artifact_name}"',
            "Cache-Control": "no-store",
            "X-Artifact-SHA256": digest,
        },
    )


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


@app.api_route(
    "/api/{unmatched_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def unknown_api(unmatched_path: str) -> None:
    raise HTTPException(status_code=404, detail="API route not found")


app.frontend(
    "/",
    directory=ui_directory,
    fallback="index.html",
)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()
