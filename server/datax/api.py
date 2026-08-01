from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .models import DataXImportJob, IndicatorProposal
from .service import DataXService
from .store import DataXConflictError, DataXNotFoundError, DataXValidationError


router = APIRouter(prefix="/api/datax", tags=["datax"])
_service: DataXService | None = None


def configure_datax(service: DataXService) -> None:
    global _service
    _service = service


def get_datax_service() -> DataXService:
    if _service is None:
        raise RuntimeError("Data X service is not configured.")
    return _service


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, DataXNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DataXConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (DataXValidationError, ValueError, TypeError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="Data X operation failed.")


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)


class ProjectPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    status: str | None = None


class SemanticModelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    entities: list[dict[str, Any]] = Field(min_length=1, max_length=5)
    joins: list[dict[str, Any]] = Field(default_factory=list)
    fields: list[dict[str, Any]] = Field(default_factory=list)


class SemanticModelPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    entities: list[dict[str, Any]] | None = None
    joins: list[dict[str, Any]] | None = None
    fields: list[dict[str, Any]] | None = None


class PreviewRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class IndicatorCreateRequest(BaseModel):
    model_id: str
    code: str
    name: str
    description: str = ""
    indicator_type: str = "basic"
    aggregation: str | None = None
    measure_field: str | None = None
    formula: str | None = None
    default_dimensions: list[str] = Field(default_factory=list)
    time_field: str | None = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class IndicatorPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    patch: dict[str, Any]


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class DataXQueryRequest(BaseModel):
    project_id: str
    model_id: str
    indicators: list[str] = Field(min_length=1, max_length=20)
    dimensions: list[str] = Field(default_factory=list, max_length=10)
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    time_range: dict[str, Any] | None = None
    order_by: list[dict[str, str]] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=100, ge=1, le=500)
    view: str = "table"


class IndicatorSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    project_ids: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=20, ge=1, le=100)


class ProposalPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    payload: dict[str, Any] | None = None


class ProposalActionRequest(BaseModel):
    revision: int = Field(ge=1)
    operator: str = Field(default="modelmirror-operator", max_length=120)
    reason: str = Field(default="", max_length=1000)


@router.get("/capabilities")
async def datax_capabilities():
    return get_datax_service().capabilities()


@router.get("/projects")
async def list_projects():
    items = get_datax_service().list_projects()
    return {"version": "modelmirror-datax-projects-v1", "items": items, "total": len(items)}


@router.post("/projects")
async def create_project(payload: ProjectCreateRequest):
    try:
        return get_datax_service().create_project(name=payload.name, description=payload.description)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    try:
        service = get_datax_service()
        project = service.get_project(project_id)
        return {
            **project.model_dump(mode="json"),
            "sources": [item.model_dump(mode="json") for item in service.list_sources(project_id)],
            "models": [item.model_dump(mode="json") for item in service.list_models(project_id)],
            "indicators": [item.model_dump(mode="json") for item in service.list_indicators(project_id)],
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, payload: ProjectPatchRequest):
    try:
        return get_datax_service().update_project(project_id, **payload.model_dump())
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/projects/{project_id}/sources")
async def upload_source(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    try:
        content = await file.read(50 * 1024 * 1024 + 1)
        service = get_datax_service()
        job = service.create_import_job(
            project_id, file_name=file.filename or "source", content=content
        )
        if job.status == "pending":
            background_tasks.add_task(service.run_import_job, job.job_id)
        return job
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/import-jobs/{job_id}")
async def get_import_job(job_id: str):
    try:
        return get_datax_service().get_import_job(job_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/projects/{project_id}/models")
async def list_models(project_id: str):
    try:
        items = get_datax_service().list_models(project_id)
        return {"items": items, "total": len(items)}
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/projects/{project_id}/models")
async def create_model(project_id: str, payload: SemanticModelRequest):
    try:
        return get_datax_service().create_model(project_id, **payload.model_dump())
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/models/{model_id}")
async def patch_model(model_id: str, payload: SemanticModelPatchRequest):
    try:
        values = payload.model_dump(exclude={"revision"}, exclude_none=True)
        return get_datax_service().update_model(model_id, revision=payload.revision, patch=values)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/models/{model_id}/preview")
async def preview_model(model_id: str, payload: PreviewRequest):
    try:
        return get_datax_service().preview_model(model_id, limit=payload.limit)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/projects/{project_id}/indicators")
async def list_indicators(project_id: str, status: str | None = None):
    try:
        items = get_datax_service().list_indicators(project_id, status=status)
        return {"items": items, "total": len(items)}
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/projects/{project_id}/indicators")
async def create_indicator(project_id: str, payload: IndicatorCreateRequest):
    try:
        return get_datax_service().create_indicator(project_id, payload.model_dump())
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/indicators/{indicator_id}")
async def get_indicator(indicator_id: str):
    try:
        return get_datax_service().get_indicator(indicator_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/indicators/{indicator_id}")
async def patch_indicator(indicator_id: str, payload: IndicatorPatchRequest):
    try:
        return get_datax_service().update_indicator(
            indicator_id, revision=payload.revision, patch=payload.patch
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/indicators/{indicator_id}/validate")
async def validate_indicator(indicator_id: str):
    try:
        return get_datax_service().validate_indicator(
            get_datax_service().get_indicator(indicator_id)
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/indicators/{indicator_id}/publish")
async def publish_indicator(indicator_id: str, payload: RevisionRequest):
    try:
        return get_datax_service().publish_indicator(indicator_id, revision=payload.revision)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/indicators/{indicator_id}/archive")
async def archive_indicator(indicator_id: str, payload: RevisionRequest):
    try:
        return get_datax_service().archive_indicator(indicator_id, revision=payload.revision)
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/query")
async def query_datax(payload: DataXQueryRequest):
    try:
        return get_datax_service().query(
            project_id=payload.project_id,
            model_id=payload.model_id,
            indicator_codes=payload.indicators,
            dimensions=payload.dimensions,
            filters=payload.filters,
            time_range=payload.time_range,
            order_by=payload.order_by,
            limit=payload.limit,
            view=payload.view,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/indicators/search")
async def search_indicators(payload: IndicatorSearchRequest):
    try:
        return get_datax_service().search_indicators(
            payload.query, project_ids=payload.project_ids or None, limit=payload.limit
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/indicator-proposals")
async def list_indicator_proposals(
    project_id: str | None = None,
    status: str | None = None,
    source_xpert_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    items = get_datax_service().list_proposals(
        project_id=project_id,
        status=status,
        source_xpert_id=source_xpert_id,
        limit=limit,
    )
    return {"items": items, "total": len(items)}


@router.get("/indicator-proposals/{proposal_id}")
async def get_indicator_proposal(proposal_id: str):
    try:
        return get_datax_service().get_proposal(proposal_id)
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/indicator-proposals/{proposal_id}")
async def patch_indicator_proposal(proposal_id: str, payload: ProposalPatchRequest):
    try:
        return get_datax_service().update_proposal(
            proposal_id, revision=payload.revision, title=payload.title, payload=payload.payload
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/indicator-proposals/{proposal_id}/approve")
async def approve_indicator_proposal(proposal_id: str, payload: ProposalActionRequest):
    try:
        return get_datax_service().resolve_proposal(
            proposal_id,
            revision=payload.revision,
            action="approve",
            operator=payload.operator,
            reason=payload.reason,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/indicator-proposals/{proposal_id}/reject")
async def reject_indicator_proposal(proposal_id: str, payload: ProposalActionRequest):
    try:
        return get_datax_service().resolve_proposal(
            proposal_id,
            revision=payload.revision,
            action="reject",
            operator=payload.operator,
            reason=payload.reason,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/indicator-proposals/{proposal_id}/cancel")
async def cancel_indicator_proposal(proposal_id: str, payload: ProposalActionRequest):
    try:
        return get_datax_service().resolve_proposal(
            proposal_id,
            revision=payload.revision,
            action="cancel",
            operator=payload.operator,
            reason=payload.reason,
        )
    except Exception as exc:
        raise _error(exc) from exc
