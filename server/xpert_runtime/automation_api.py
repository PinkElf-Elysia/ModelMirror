from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .automation_coordinator import AutomationCoordinator
from .automation_store import (
    AutomationConflictError,
    AutomationNotFoundError,
    AutomationStore,
    AutomationValidationError,
)


router = APIRouter(prefix="/api/runtime", tags=["runtime-automations"])
_store: AutomationStore | None = None
_coordinator: AutomationCoordinator | None = None
_target_resolver: Any = None


class AutomationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=20_000)
    target_xpert_id: str = Field(min_length=1, max_length=200)
    trigger: dict[str, Any]
    status: str = Field(default="draft", max_length=30)
    overlap_policy: str = Field(default="skip", max_length=30)
    misfire_policy: str = Field(default="latest", max_length=30)
    max_attempts: int = Field(default=3, ge=1, le=10)
    budget: dict[str, Any] = Field(default_factory=dict)


class AutomationPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    trigger: dict[str, Any] | None = None
    status: str | None = Field(default=None, max_length=30)
    overlap_policy: str | None = Field(default=None, max_length=30)
    misfire_policy: str | None = Field(default=None, max_length=30)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    budget: dict[str, Any] | None = None


def configure_runtime_automations(
    store: AutomationStore,
    coordinator: AutomationCoordinator,
    target_resolver: Any,
) -> None:
    global _store, _coordinator, _target_resolver
    _store = store
    _coordinator = coordinator
    _target_resolver = target_resolver


def get_automation_store() -> AutomationStore:
    if _store is None:
        raise RuntimeError("Automation store is not configured.")
    return _store


def get_automation_coordinator() -> AutomationCoordinator:
    if _coordinator is None:
        raise RuntimeError("Automation coordinator is not configured.")
    return _coordinator


def _api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AutomationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AutomationConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (AutomationValidationError, ValueError, TypeError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


async def _resolve_target(reference: str) -> Any:
    if _target_resolver is None:
        raise AutomationValidationError("Published Xpert resolver is unavailable.")
    return await _target_resolver(reference)


@router.post("/automations")
async def create_automation(payload: AutomationCreateRequest):
    try:
        target = await _resolve_target(payload.target_xpert_id)
        item = get_automation_store().create_definition(
            name=payload.name,
            prompt=payload.prompt,
            target_xpert_id=target.xpert_id,
            target_xpert_slug=target.slug,
            target_xpert_version=target.version,
            trigger=payload.trigger,
            status=payload.status,  # type: ignore[arg-type]
            overlap_policy=payload.overlap_policy,
            misfire_policy=payload.misfire_policy,
            max_attempts=payload.max_attempts,
            budget=payload.budget,
            metadata={"created_via": "api"},
        )
        get_automation_coordinator().wake()
        return AutomationStore.serialize_definition(item)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.get("/automations")
async def list_automations(
    status: str | None = None,
    search: str = "",
    limit: int = Query(default=100, ge=1, le=500),
):
    items = get_automation_store().list_definitions(
        status=status, search=search, limit=limit
    )
    return {
        "version": "xpert-automations-v1",
        "items": [AutomationStore.serialize_definition(item, include_prompt=False) for item in items],
        "total": len(items),
    }


@router.get("/automations/{automation_id}")
async def get_automation(automation_id: str):
    try:
        item = get_automation_store().require_definition(automation_id)
        executions = get_automation_store().list_executions(
            automation_id=automation_id, limit=50
        )
        return {
            **AutomationStore.serialize_definition(item),
            "executions": [AutomationStore.serialize_execution(value) for value in executions],
        }
    except Exception as exc:
        raise _api_error(exc) from exc


@router.patch("/automations/{automation_id}")
async def patch_automation(automation_id: str, payload: AutomationPatchRequest):
    try:
        patch = payload.model_dump(exclude={"revision"}, exclude_none=True)
        item = get_automation_store().update_definition(
            automation_id, revision=payload.revision, patch=patch
        )
        get_automation_coordinator().wake()
        return AutomationStore.serialize_definition(item)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/automations/{automation_id}/pause")
async def pause_automation(automation_id: str):
    try:
        return AutomationStore.serialize_definition(
            get_automation_store().set_status(automation_id, "paused")
        )
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/automations/{automation_id}/resume")
async def resume_automation(automation_id: str):
    try:
        item = get_automation_store().set_status(automation_id, "scheduled")
        get_automation_coordinator().wake()
        return AutomationStore.serialize_definition(item)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/automations/{automation_id}/run-now")
async def run_automation_now(automation_id: str):
    try:
        item = get_automation_store().run_now(automation_id)
        get_automation_coordinator().wake()
        return AutomationStore.serialize_execution(item)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/automations/{automation_id}/archive")
async def archive_automation(automation_id: str):
    try:
        return AutomationStore.serialize_definition(
            get_automation_store().set_status(automation_id, "archived")
        )
    except Exception as exc:
        raise _api_error(exc) from exc


@router.get("/automation-executions")
async def list_automation_executions(
    automation_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    items = get_automation_store().list_executions(
        automation_id=automation_id, status=status, limit=limit
    )
    return {
        "version": "xpert-automation-executions-v1",
        "items": [AutomationStore.serialize_execution(item) for item in items],
        "total": len(items),
    }


@router.post("/automation-executions/{execution_id}/retry")
async def retry_automation_execution(
    execution_id: str,
    payload: dict[str, Any] | None = None,
):
    try:
        item = get_automation_store().retry_execution(
            execution_id, reset_attempts=bool((payload or {}).get("reset_attempts"))
        )
        get_automation_coordinator().wake()
        return AutomationStore.serialize_execution(item)
    except Exception as exc:
        raise _api_error(exc) from exc


@router.post("/automation-executions/{execution_id}/cancel")
async def cancel_automation_execution(execution_id: str):
    try:
        return AutomationStore.serialize_execution(
            get_automation_store().cancel_execution(execution_id)
        )
    except Exception as exc:
        raise _api_error(exc) from exc


@router.get("/automation-coordinator/status")
async def automation_coordinator_status():
    return await get_automation_coordinator().status()
