from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .todo_store import (
    RuntimeTodoConflictError,
    RuntimeTodoNotFoundError,
    RuntimeTodoStore,
    RuntimeTodoValidationError,
)


router = APIRouter(prefix="/api/runtime", tags=["runtime-todos"])
_todo_store: RuntimeTodoStore | None = None


class RuntimeTodoCreateRequest(BaseModel):
    scope_type: str = Field(min_length=1, max_length=30)
    scope_id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    details: str = Field(default="", max_length=10_000)
    status: str = Field(default="pending", max_length=30)
    priority: int = Field(default=0, ge=-10, le=10)
    order: int | None = Field(default=None, ge=0, le=10_000)
    source_run_id: str | None = Field(default=None, max_length=200)


class RuntimeTodoPatchRequest(BaseModel):
    scope_type: str = Field(min_length=1, max_length=30)
    scope_id: str = Field(min_length=1, max_length=300)
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    details: str | None = Field(default=None, max_length=10_000)
    status: str | None = Field(default=None, max_length=30)
    priority: int | None = Field(default=None, ge=-10, le=10)
    order: int | None = Field(default=None, ge=0, le=10_000)


def configure_runtime_todo_store(store: RuntimeTodoStore) -> RuntimeTodoStore:
    global _todo_store
    _todo_store = store
    return store


def get_runtime_todo_store() -> RuntimeTodoStore:
    if _todo_store is None:
        raise RuntimeError("Runtime Todo store is not configured.")
    return _todo_store


@router.get("/todos")
async def list_runtime_todos(
    scope_type: str = Query(min_length=1, max_length=30),
    scope_id: str = Query(min_length=1, max_length=300),
    status: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    try:
        items = get_runtime_todo_store().list_items(
            scope_type=scope_type,
            scope_id=scope_id,
            status=status,
            limit=limit,
        )
    except RuntimeTodoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "items": [get_runtime_todo_store().serialize(item) for item in items],
        "count": len(items),
    }


@router.post("/todos")
async def create_runtime_todo(payload: RuntimeTodoCreateRequest) -> dict[str, Any]:
    try:
        item = get_runtime_todo_store().create_item(
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            title=payload.title,
            details=payload.details,
            status=payload.status,
            priority=payload.priority,
            order=payload.order,
            source_run_id=payload.source_run_id,
            created_by="user",
        )
    except RuntimeTodoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_runtime_todo_store().serialize(item)


@router.patch("/todos/{todo_id}")
async def update_runtime_todo(
    todo_id: str,
    payload: RuntimeTodoPatchRequest,
) -> dict[str, Any]:
    patch = payload.model_dump(
        exclude={"scope_type", "scope_id", "revision"},
        exclude_none=True,
    )
    try:
        item = get_runtime_todo_store().update_item(
            todo_id,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            revision=payload.revision,
            patch=patch,
        )
    except RuntimeTodoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTodoConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeTodoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_runtime_todo_store().serialize(item)


@router.delete("/todos/{todo_id}")
async def archive_runtime_todo(
    todo_id: str,
    scope_type: str = Query(min_length=1, max_length=30),
    scope_id: str = Query(min_length=1, max_length=300),
    revision: int = Query(ge=1),
) -> dict[str, Any]:
    try:
        item = get_runtime_todo_store().archive_item(
            todo_id,
            scope_type=scope_type,
            scope_id=scope_id,
            revision=revision,
        )
    except RuntimeTodoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTodoConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeTodoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_runtime_todo_store().serialize(item)
