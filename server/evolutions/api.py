from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .executor import OptimizerRunner, XpertEvolutionExecutor
from .models import EvolutionPreflightRequest, EvolutionRunRequest
from .service import XpertEvolutionService
from .store import (
    EvolutionConflictError,
    EvolutionNotFoundError,
    EvolutionStateError,
    XpertEvolutionStore,
)


router = APIRouter(prefix="/api/xpert-evolutions", tags=["xpert-evolutions"])
_store: XpertEvolutionStore | None = None
_service: XpertEvolutionService | None = None
_executor: XpertEvolutionExecutor | None = None


def configure_xpert_evolutions(
    *,
    storage_dir: str | Path | None,
    evaluation_store: Any,
    evaluation_service: Any,
    evaluation_executor: Any,
    xpert_store: Any,
    prompt_store: Any,
    proposal_store: Any,
    optimizer_runner: OptimizerRunner,
    run_registry: Any,
) -> XpertEvolutionExecutor:
    global _store, _service, _executor
    _store = XpertEvolutionStore(storage_dir)
    _service = XpertEvolutionService(
        _store,
        evaluation_store=evaluation_store,
        evaluation_service=evaluation_service,
        xpert_store=xpert_store,
        prompt_store=prompt_store,
        proposal_store=proposal_store,
    )
    _executor = XpertEvolutionExecutor(
        _store,
        _service,
        evaluation_service=evaluation_service,
        evaluation_store=evaluation_store,
        evaluation_executor=evaluation_executor,
        optimizer_runner=optimizer_runner,
        run_registry=run_registry,
    )
    return _executor


def get_xpert_evolution_executor() -> XpertEvolutionExecutor:
    if _executor is None:
        raise RuntimeError("Xpert Evolution is not configured.")
    return _executor


def _require_store() -> XpertEvolutionStore:
    if _store is None:
        raise RuntimeError("Xpert Evolution is not configured.")
    return _store


def _require_service() -> XpertEvolutionService:
    if _service is None:
        raise RuntimeError("Xpert Evolution is not configured.")
    return _service


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    return _require_service().capabilities()


@router.post("/preflight")
async def preflight(payload: EvolutionPreflightRequest) -> dict[str, Any]:
    try:
        return await _to_thread(_require_service().preflight, payload)
    except (EvolutionConflictError, EvolutionStateError, ValueError) as exc:
        return {
            "valid": False,
            "target": None,
            "dataset": None,
            "warnings": [],
            "issues": [{"code": "evolution_preflight", "message": str(exc)[:500]}],
        }


@router.get("/runs")
async def list_runs(status: str | None = None, limit: int = 100) -> dict[str, Any]:
    items = await _to_thread(
        _require_store().list_runs,
        status=status,
        limit=max(1, min(limit, 200)),
    )
    return {"items": items, "total": len(items)}


@router.post("/runs")
async def create_run(payload: EvolutionRunRequest) -> dict[str, Any]:
    try:
        run = await _to_thread(_require_service().create_run, payload)
        get_xpert_evolution_executor().wake()
        return _sanitize(run)
    except EvolutionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EvolutionStateError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    try:
        return _sanitize(
            await _to_thread(_require_service().run_detail, run_id)
        )
    except EvolutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        return _sanitize(await _to_thread(_require_store().cancel, run_id))
    except EvolutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(function, *args, **kwargs)


def _sanitize(run: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(run)
    dataset = dict(payload.get("dataset") or {})
    dataset.pop("cases", None)
    payload["dataset"] = dataset
    target = dict(payload.get("target") or {})
    target.pop("baseline_xpert", None)
    target.pop("baseline_snapshot", None)
    target.pop("baseline_profile", None)
    payload["target"] = target
    for generation in payload.get("generations") or []:
        for candidate in generation.get("candidates") or []:
            candidate.pop("snapshot", None)
            candidate.pop("xpert", None)
    return payload
