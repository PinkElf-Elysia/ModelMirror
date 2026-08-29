from __future__ import annotations

import json
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Path as ApiPath, Query, Request, status
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
    ModuleInfoView,
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
from .project_models import (
    LiteratureOutcome,
    LiteraturePhase,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectUpdateRequest,
    ProjectView,
    LiteratureSessionView,
    LiteratureRunCreateRequest,
    LiteratureUnlockRequest,
)
from .ldr_client import (
    LdrAuthenticationError,
    LdrConflict,
    LdrProtocolError,
    LdrSessionExpired,
    LdrUnavailable,
)
from .literature_artifacts import LiteratureArtifactError
from .project_store import ProjectConflict, ProjectIntegrityError
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
    version="0.3.0-v0.1",
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


def to_project_view(project: dict[str, Any]) -> ProjectView:
    literature = project["literature"]
    return ProjectView.model_validate(
        {
            "schemaVersion": project["schemaVersion"],
            "projectId": project["projectId"],
            "title": project["title"],
            "researchQuestion": project["researchQuestion"],
            "domain": project["domain"],
            "currentStage": project["currentStage"],
            "stages": project["stages"],
            "literaturePhase": literature["phase"],
            "literatureOutcome": literature["outcome"],
            "activeRunId": literature["activeRunId"],
            "completedRunId": literature["completedRunId"],
            "collectionId": literature["collectionId"],
            "profileId": literature["profileId"],
            "modelId": literature["modelId"],
            "attempts": literature["attempts"],
            "createdAt": project["createdAt"],
            "updatedAt": project["updatedAt"],
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


@app.get("/api/v1/module", response_model=ModuleInfoView, response_model_by_alias=True)
async def module_metadata() -> ModuleInfoView:
    boundary = json.loads(settings.module_boundary.read_text(encoding="utf-8"))
    source_lock = json.loads(settings.source_lock.read_text(encoding="utf-8"))
    return ModuleInfoView.model_validate({
        "moduleId": boundary["moduleId"],
        "moduleVersion": boundary["moduleVersion"],
        "apiVersion": boundary["apiVersion"],
        "workerProtocolVersion": boundary["workerProtocolVersion"],
        "fixtures": boundary["allowedFixtures"],
        "runtimes": source_lock["runtimes"],
        "capabilities": {
            "fixtureExecution": True,
            "cancellation": True,
            "evidenceVerification": True,
            "inspectView": True,
            "mlflow": True,
            "literatureResearch": True,
            "openAlex": True,
            "zoteroLibrary": True,
            "literatureArtifactExport": True,
            "modelEvaluation": False,
            "multiTenant": False,
        },
        "capabilityClaims": {
            "fixtureExecution": {
                "enabled": True,
                "claimLevel": "harness_only",
                "packStatus": "fixture_only",
            },
            "literatureResearch": {
                "enabled": True,
                "scientificClaim": "none",
                "acceptanceState": "pending_live_acceptance",
                "workflowSource": "local_deep_research",
            },
        },
        "links": {
            "mlflow": settings.mlflow_public_url,
            "inspectView": settings.inspect_view_public_url,
            "localDeepResearch": settings.ldr_public_url,
        },
        "limitations": [
            "one administrator-fixed text model through the restricted S2S bridge",
            "literature workflow only; scientificClaim=none",
            "no scientific EvalPack or score",
            "local single-tenant compatibility mode only",
        ],
    })


@app.get("/api/v1/system", response_model=SystemView, response_model_by_alias=True)
async def system_status(request: Request) -> SystemView:
    return SystemView.model_validate(await service(request).system_status())


def literature_session_response(
    value: dict[str, str | None], *, status_code: int = 200
) -> JSONResponse:
    view = LiteratureSessionView.model_validate(value)
    return JSONResponse(
        status_code=status_code,
        content=view.model_dump(),
        headers={"Cache-Control": "no-store"},
    )


@app.get(
    "/api/v1/literature/session",
    response_model=LiteratureSessionView,
)
async def get_literature_session(request: Request) -> Response:
    try:
        value = await service(request).literature_session()
    except LdrUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return literature_session_response(value)


@app.post(
    "/api/v1/literature/session/unlock",
    response_model=LiteratureSessionView,
)
async def unlock_literature_session(
    payload: LiteratureUnlockRequest, request: Request
) -> Response:
    try:
        value = await service(request).unlock_literature(
            username=payload.username,
            password=payload.password,
        )
    except (LdrAuthenticationError, LdrSessionExpired) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except LdrConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError, NotReady) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return literature_session_response(value)


@app.delete(
    "/api/v1/literature/session",
    response_model=LiteratureSessionView,
)
async def clear_literature_session(request: Request) -> Response:
    value = await service(request).clear_literature_session()
    return literature_session_response(value)


@app.post(
    "/api/v1/projects",
    response_model=ProjectView,
    response_model_by_alias=True,
)
async def create_project(payload: ProjectCreateRequest, request: Request) -> Response:
    try:
        project, created = await asyncio.to_thread(
            service(request).projects.create,
            title=payload.title,
            research_question=payload.research_question,
            idempotency_key=payload.idempotency_key,
        )
    except ProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    view = to_project_view(project)
    return JSONResponse(
        status_code=201 if created else 200,
        content=view.model_dump(by_alias=True),
    )


@app.get(
    "/api/v1/projects",
    response_model=ProjectListResponse,
    response_model_by_alias=True,
)
async def list_projects(
    request: Request,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    q: Annotated[str | None, Query(max_length=100)] = None,
    literature_phase: Annotated[
        LiteraturePhase | None, Query(alias="literaturePhase")
    ] = None,
    literature_outcome: Annotated[
        LiteratureOutcome | None, Query(alias="literatureOutcome")
    ] = None,
) -> ProjectListResponse:
    try:
        projects = await asyncio.to_thread(
            service(request).projects.list,
            after_project_id=cursor,
            limit=limit + 1,
            query=q,
            phase=literature_phase,
            outcome=literature_outcome,
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="invalid cursor") from exc
    except ProjectIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    has_more = len(projects) > limit
    visible = projects[:limit]
    return ProjectListResponse(
        items=[to_project_view(project) for project in visible],
        nextCursor=visible[-1]["projectId"] if has_more and visible else None,
    )


@app.get(
    "/api/v1/projects/{project_id}",
    response_model=ProjectView,
    response_model_by_alias=True,
)
async def get_project(project_id: str, request: Request) -> ProjectView:
    try:
        project = await asyncio.to_thread(service(request).projects.get, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except ProjectIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return to_project_view(project)


@app.patch(
    "/api/v1/projects/{project_id}",
    response_model=ProjectView,
    response_model_by_alias=True,
)
async def update_project(
    project_id: str, payload: ProjectUpdateRequest, request: Request
) -> ProjectView:
    try:
        project = await asyncio.to_thread(
            service(request).projects.update,
            project_id,
            title=payload.title,
            research_question=payload.research_question,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except (ProjectConflict, ProjectIntegrityError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return to_project_view(project)


@app.post(
    "/api/v1/projects/{project_id}/literature/runs",
    response_model=ProjectView,
    response_model_by_alias=True,
)
async def start_project_literature(
    project_id: str, payload: LiteratureRunCreateRequest, request: Request
) -> Response:
    try:
        project, created = await service(request).start_literature(
            project_id,
            idempotency_key=payload.idempotency_key,
            collection_id=payload.collection_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except (LdrSessionExpired, LdrAuthenticationError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except (ProjectConflict, LdrConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError, NotReady) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    view = to_project_view(project)
    return JSONResponse(
        status_code=201 if created else 200,
        content=view.model_dump(by_alias=True),
    )


@app.get(
    "/api/v1/projects/{project_id}/literature",
    response_model=ProjectView,
    response_model_by_alias=True,
)
async def get_project_literature(project_id: str, request: Request) -> ProjectView:
    return await get_project(project_id, request)


@app.post(
    "/api/v1/projects/{project_id}/literature/cancel",
    response_model=ProjectView,
    response_model_by_alias=True,
)
async def cancel_project_literature(project_id: str, request: Request) -> ProjectView:
    try:
        project = await service(request).cancel_literature(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except (LdrSessionExpired, LdrAuthenticationError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except LdrConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return to_project_view(project)


@app.post(
    "/api/v1/projects/{project_id}/literature/sync",
    response_model=ProjectView,
    response_model_by_alias=True,
)
async def sync_project_literature(project_id: str, request: Request) -> ProjectView:
    try:
        project = await service(request).sync_literature(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except (LdrSessionExpired, LdrAuthenticationError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except ProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return to_project_view(project)


@app.get("/api/v1/projects/{project_id}/sources")
async def get_project_sources(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return await service(request).literature_sources(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project or result not found") from exc
    except ProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectIntegrityError, LiteratureArtifactError) as exc:
        raise HTTPException(status_code=409, detail="result integrity check failed") from exc


@app.get("/api/v1/projects/{project_id}/review")
async def get_project_review(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return await service(request).literature_review(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project or result not found") from exc
    except ProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectIntegrityError, LiteratureArtifactError) as exc:
        raise HTTPException(status_code=409, detail="result integrity check failed") from exc


LITERATURE_ARTIFACT_MEDIA_TYPES = {
    "literature-review.md": "text/markdown; charset=utf-8",
    "upstream-quarto.zip": "application/zip",
    "literature-review.qmd": "text/markdown; charset=utf-8",
    "references.bib": "application/x-bibtex; charset=utf-8",
    "references.ris": "application/x-research-info-systems; charset=utf-8",
    "sources.json": "application/json",
    "literature-receipt.json": "application/json",
    "artifact-manifest.json": "application/json",
}


@app.get("/api/v1/projects/{project_id}/artifacts/{artifact_name}")
async def download_literature_artifact(
    project_id: str, artifact_name: str, request: Request
) -> Response:
    try:
        content, digest = await service(request).literature_artifact(
            project_id, artifact_name
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except ProjectConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ProjectIntegrityError, LiteratureArtifactError) as exc:
        raise HTTPException(status_code=409, detail="artifact integrity check failed") from exc
    return Response(
        content=content,
        media_type=LITERATURE_ARTIFACT_MEDIA_TYPES.get(
            artifact_name, "application/octet-stream"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{artifact_name}"',
            "Cache-Control": "no-store",
            "X-Content-SHA256": digest,
        },
    )


@app.get("/api/v1/literature/library/collections")
async def get_literature_collections(request: Request) -> dict[str, Any]:
    try:
        return await service(request).literature_collections()
    except (LdrSessionExpired, LdrAuthenticationError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/literature/library/collections/{collection_id}/index")
async def index_literature_collection(
    collection_id: Annotated[
        str,
        ApiPath(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ],
    request: Request,
) -> dict[str, Any]:
    try:
        return await service(request).index_literature_collection(collection_id)
    except (LdrSessionExpired, LdrAuthenticationError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except (ProjectConflict, LdrConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/literature/zotero/status")
async def get_literature_zotero_status(request: Request) -> dict[str, Any]:
    try:
        return await service(request).literature_zotero_status()
    except (LdrSessionExpired, LdrAuthenticationError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/literature/zotero/sync")
async def sync_literature_zotero(request: Request) -> dict[str, Any]:
    try:
        return await service(request).sync_literature_zotero()
    except (LdrSessionExpired, LdrAuthenticationError) as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except LdrConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (LdrUnavailable, LdrProtocolError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
