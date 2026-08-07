from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

try:
    from server.evaluations.store import EvaluationStateError, XpertEvaluationStore
except ModuleNotFoundError:
    from evaluations.store import EvaluationStateError, XpertEvaluationStore

from .catalog import BenchmarkCatalog, BenchmarkCatalogError, BenchmarkPackNotFoundError
from .models import InstantiateBenchmarkRequest


router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
_catalog: BenchmarkCatalog | None = None
_evaluation_store: XpertEvaluationStore | None = None


def configure_benchmarks(evaluation_store: XpertEvaluationStore) -> BenchmarkCatalog:
    global _catalog, _evaluation_store
    _catalog = BenchmarkCatalog()
    _evaluation_store = evaluation_store
    return _catalog


def get_benchmark_catalog() -> BenchmarkCatalog:
    if _catalog is None:
        raise RuntimeError("Benchmark catalog is not configured.")
    return _catalog


def _require_evaluation_store() -> XpertEvaluationStore:
    if _evaluation_store is None:
        raise RuntimeError("Benchmark catalog is not configured.")
    return _evaluation_store


@router.get("/capabilities")
async def get_capabilities() -> dict[str, Any]:
    return get_benchmark_catalog().capabilities()


@router.get("/catalog")
async def list_catalog(
    kind: str | None = Query(default=None, pattern="^(agent_response|knowledge_retrieval)$"),
) -> dict[str, Any]:
    items = get_benchmark_catalog().list_packs(kind=kind)
    return {"items": items, "total": len(items)}


@router.get("/catalog/{pack_id}")
async def get_catalog_pack(pack_id: str) -> dict[str, Any]:
    try:
        return get_benchmark_catalog().pack_payload(pack_id)
    except BenchmarkPackNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/catalog/{pack_id}/instantiate")
async def instantiate_catalog_pack(
    pack_id: str,
    payload: InstantiateBenchmarkRequest,
) -> dict[str, Any]:
    try:
        return get_benchmark_catalog().instantiate(
            pack_id,
            store=_require_evaluation_store(),
            name=payload.name,
            description=payload.description,
        )
    except BenchmarkPackNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (BenchmarkCatalogError, EvaluationStateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
