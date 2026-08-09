from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

try:
    from server.evaluations.store import EvaluationStateError, XpertEvaluationStore
except ModuleNotFoundError:
    from evaluations.store import EvaluationStateError, XpertEvaluationStore

from .catalog import BenchmarkCatalog, BenchmarkCatalogError, BenchmarkPackNotFoundError
from .executor import BenchmarkJobExecutor, GeneratorRunner
from .models import (
    BenchmarkCalibrationRequest,
    BenchmarkGenerationPreflightRequest,
    BenchmarkGenerationRequest,
    InstantiateBenchmarkRequest,
)
from .service import BenchmarkGenerationError, BenchmarkGenerationService
from .store import BenchmarkJobNotFoundError, BenchmarkJobStore


router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
_catalog: BenchmarkCatalog | None = None
_evaluation_store: XpertEvaluationStore | None = None
_job_store: BenchmarkJobStore | None = None
_service: BenchmarkGenerationService | None = None
_executor: BenchmarkJobExecutor | None = None


def configure_benchmarks(
    evaluation_store: XpertEvaluationStore,
    *,
    storage_dir: str | None = None,
    evaluation_service: Any | None = None,
    evaluation_executor: Any | None = None,
    xpert_store: Any | None = None,
    proposal_store: Any | None = None,
    prompt_store: Any | None = None,
    context_store: Any | None = None,
    rag_service: Any | None = None,
    toolset_store: Any | None = None,
    generator_runner: GeneratorRunner | None = None,
) -> BenchmarkCatalog:
    global _catalog, _evaluation_store, _job_store, _service, _executor
    _catalog = BenchmarkCatalog()
    _evaluation_store = evaluation_store
    _job_store = None
    _service = None
    _executor = None
    if all(
        value is not None
        for value in (
            evaluation_service,
            evaluation_executor,
            xpert_store,
            proposal_store,
            prompt_store,
            context_store,
            generator_runner,
        )
    ):
        _job_store = BenchmarkJobStore(storage_dir)
        _service = BenchmarkGenerationService(
            evaluation_store=evaluation_store,
            evaluation_service=evaluation_service,
            xpert_store=xpert_store,
            proposal_store=proposal_store,
            prompt_store=prompt_store,
            context_store=context_store,
            rag_service=rag_service,
            toolset_store=toolset_store,
        )
        _executor = BenchmarkJobExecutor(
            _job_store,
            service=_service,
            generator_runner=generator_runner,
            evaluation_store=evaluation_store,
            evaluation_service=evaluation_service,
            evaluation_executor=evaluation_executor,
        )
    return _catalog


def get_benchmark_catalog() -> BenchmarkCatalog:
    if _catalog is None:
        raise RuntimeError("Benchmark catalog is not configured.")
    return _catalog


def _require_evaluation_store() -> XpertEvaluationStore:
    if _evaluation_store is None:
        raise RuntimeError("Benchmark catalog is not configured.")
    return _evaluation_store


def get_benchmark_job_store() -> BenchmarkJobStore:
    if _job_store is None:
        raise RuntimeError("Benchmark generator is not configured.")
    return _job_store


def get_benchmark_generation_service() -> BenchmarkGenerationService:
    if _service is None:
        raise RuntimeError("Benchmark generator is not configured.")
    return _service


def get_benchmark_job_executor() -> BenchmarkJobExecutor:
    if _executor is None:
        raise RuntimeError("Benchmark generator is not configured.")
    return _executor


@router.get("/capabilities")
async def get_capabilities() -> dict[str, Any]:
    payload = get_benchmark_catalog().capabilities()
    if _service is not None:
        payload["generator"] = _service.capabilities()
    return payload


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


@router.post("/generations/preflight")
async def preflight_generation(
    payload: BenchmarkGenerationPreflightRequest,
) -> dict[str, Any]:
    try:
        return await _to_thread(
            get_benchmark_generation_service().preflight,
            target_reference=payload.target.model_dump(mode="json"),
            requested_coverage=payload.coverage,
            conversation_selections=[
                item.model_dump(mode="json")
                for item in payload.conversation_selections
            ],
        )
    except Exception as exc:
        return {
            "valid": False,
            "target": None,
            "coverage": {"available": [], "recommended": []},
            "conversation_seed_count": 0,
            "warnings": [],
            "issues": [{"code": "benchmark_preflight", "message": str(exc)[:500]}],
        }


@router.get("/generations")
async def list_generations(
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    items = await _to_thread(
        get_benchmark_job_store().list_jobs,
        kind="generation",
        status=status,
        limit=limit,
    )
    return {"items": [_sanitize_job(item) for item in items], "total": len(items)}


@router.post("/generations")
async def create_generation(payload: BenchmarkGenerationRequest) -> dict[str, Any]:
    preflight = await preflight_generation(
        BenchmarkGenerationPreflightRequest(
            target=payload.target,
            coverage=payload.coverage,
            conversation_selections=payload.conversation_selections,
        )
    )
    if not preflight.get("valid"):
        raise HTTPException(status_code=400, detail=preflight)
    item = await _to_thread(
        get_benchmark_job_store().create_job,
        kind="generation",
        request=payload.model_dump(mode="json"),
    )
    get_benchmark_job_executor().wake()
    return _sanitize_job(item)


@router.get("/generations/{job_id}")
async def get_generation(job_id: str) -> dict[str, Any]:
    try:
        item = await _to_thread(get_benchmark_job_store().require_job, job_id)
    except BenchmarkJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if item.get("kind") != "generation":
        raise HTTPException(status_code=404, detail="Benchmark generation not found.")
    return _sanitize_job(item)


@router.post("/generations/{job_id}/cancel")
async def cancel_generation(job_id: str) -> dict[str, Any]:
    try:
        item = await _to_thread(get_benchmark_job_store().require_job, job_id)
        if item.get("kind") != "generation":
            raise BenchmarkJobNotFoundError("Benchmark generation not found.")
        result = await _to_thread(get_benchmark_job_store().cancel_job, job_id)
        get_benchmark_job_executor().wake()
        return _sanitize_job(result)
    except BenchmarkJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/calibrations")
async def create_calibration(payload: BenchmarkCalibrationRequest) -> dict[str, Any]:
    try:
        dataset = await _to_thread(
            _require_evaluation_store().require_dataset, payload.dataset_id
        )
        if int(dataset.get("revision") or 0) != payload.dataset_revision:
            raise EvaluationStateError("Dataset changed. Reload before calibration.")
        target = (
            payload.target.model_dump(mode="json")
            if payload.target is not None
            else dict((dataset.get("provenance") or {}).get("target_reference") or {})
        )
        if not target:
            raise EvaluationStateError("Calibration target is required.")
        preflight = await _to_thread(
            get_benchmark_generation_service().preflight,
            target_reference=target,
            requested_coverage=[],
            conversation_selections=[],
        )
        if not preflight.get("valid"):
            raise EvaluationStateError("Calibration target failed preflight.")
        item = await _to_thread(
            get_benchmark_job_store().create_job,
            kind="calibration",
            request={
                "dataset_id": payload.dataset_id,
                "dataset_revision": payload.dataset_revision,
                "target": target,
            },
        )
        get_benchmark_job_executor().wake()
        return _sanitize_job(item)
    except (EvaluationStateError, BenchmarkGenerationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/calibrations/{job_id}")
async def get_calibration(job_id: str) -> dict[str, Any]:
    try:
        item = await _to_thread(get_benchmark_job_store().require_job, job_id)
    except BenchmarkJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if item.get("kind") != "calibration":
        raise HTTPException(status_code=404, detail="Benchmark calibration not found.")
    return _sanitize_job(item)


async def _to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(function, *args, **kwargs)


def _sanitize_job(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    request = dict(payload.get("request") or {})
    request.pop("conversation_selections", None)
    payload["request"] = request
    return payload
