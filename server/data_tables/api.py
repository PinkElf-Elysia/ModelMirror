from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .models import AgentTableField
from .store import (
    AgentTableConflictError,
    AgentTableNotFoundError,
    AgentTableStore,
    AgentTableValidationError,
)


router = APIRouter(prefix="/api/data-tables", tags=["agent-tables"])
_store: AgentTableStore | None = None


def configure_agent_table_store(store: AgentTableStore) -> None:
    global _store
    _store = store


def get_agent_table_store() -> AgentTableStore:
    if _store is None:
        raise RuntimeError("Agent Table store is not configured.")
    return _store


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentTableNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AgentTableConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (AgentTableValidationError, ValueError, TypeError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="Agent Table operation failed.")


class TableCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    fields: list[AgentTableField] = Field(default_factory=list, max_length=50)


class TablePatchRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    fields: list[AgentTableField] | None = Field(default=None, max_length=50)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class RecordCreateRequest(BaseModel):
    data: dict[str, Any]
    operation_id: str | None = Field(default=None, min_length=1, max_length=160)


class RecordPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    data: dict[str, Any]
    operation_id: str | None = Field(default=None, min_length=1, max_length=160)


@router.get("")
def list_tables(
    status: str | None = Query(default=None),
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    try:
        store = get_agent_table_store()
        items = store.list_tables(status=status, search=search, limit=limit)
        return {
            "items": [item.model_dump() for item in items],
            "count": len(items),
            "backend": store.backend_name,
        }
    except Exception as exc:
        raise _error(exc) from exc


@router.post("")
def create_table(request: TableCreateRequest) -> dict[str, Any]:
    try:
        item = get_agent_table_store().create_table(
            name=request.name,
            description=request.description,
            fields=[field.model_dump() for field in request.fields],
        )
        return item.model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{table_id}")
def get_table(table_id: str) -> dict[str, Any]:
    try:
        return get_agent_table_store().get_detail(table_id).model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/{table_id}")
def patch_table(table_id: str, request: TablePatchRequest) -> dict[str, Any]:
    try:
        patch = request.model_dump(exclude={"revision"}, exclude_none=True)
        if "fields" in patch:
            patch["fields"] = [
                field.model_dump() if isinstance(field, AgentTableField) else field
                for field in request.fields or []
            ]
        return get_agent_table_store().update_table(
            table_id, revision=request.revision, patch=patch
        ).model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{table_id}/validate")
def validate_table(table_id: str, request: RevisionRequest) -> dict[str, Any]:
    try:
        return get_agent_table_store().validate_table(
            table_id, revision=request.revision
        ).model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{table_id}/publish")
def publish_table(table_id: str, request: RevisionRequest) -> dict[str, Any]:
    try:
        return get_agent_table_store().publish_table(
            table_id, revision=request.revision
        ).model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{table_id}/archive")
def archive_table(table_id: str, request: RevisionRequest) -> dict[str, Any]:
    try:
        return get_agent_table_store().archive_table(
            table_id, revision=request.revision
        ).model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{table_id}/schema-versions")
def list_schema_versions(table_id: str) -> dict[str, Any]:
    try:
        items = get_agent_table_store().list_schema_versions(table_id)
        return {"items": [item.model_dump() for item in items], "count": len(items)}
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/{table_id}/records")
def list_records(
    table_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    try:
        items = get_agent_table_store().list_records(
            table_id, limit=limit, offset=offset
        )
        return {"items": [item.model_dump() for item in items], "count": len(items)}
    except Exception as exc:
        raise _error(exc) from exc


@router.post("/{table_id}/records")
def create_record(table_id: str, request: RecordCreateRequest) -> dict[str, Any]:
    try:
        return get_agent_table_store().create_record(
            table_id,
            data=request.data,
            operation_id=request.operation_id,
        ).model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.patch("/{table_id}/records/{record_id}")
def patch_record(
    table_id: str, record_id: str, request: RecordPatchRequest
) -> dict[str, Any]:
    try:
        return get_agent_table_store().update_record(
            table_id,
            record_id,
            revision=request.revision,
            data=request.data,
            operation_id=request.operation_id,
        ).model_dump()
    except Exception as exc:
        raise _error(exc) from exc


@router.delete("/{table_id}/records/{record_id}")
def delete_record(
    table_id: str,
    record_id: str,
    revision: int = Query(ge=1),
    operation_id: str | None = Query(default=None, min_length=1, max_length=160),
) -> dict[str, Any]:
    try:
        return get_agent_table_store().delete_record(
            table_id,
            record_id,
            revision=revision,
            operation_id=operation_id,
        )
    except Exception as exc:
        raise _error(exc) from exc

