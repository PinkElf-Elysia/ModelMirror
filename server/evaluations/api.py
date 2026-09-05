from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from .executor import JudgeRunner, TargetRunner, XpertEvaluationExecutor
from .models import (
    ConversationImportRequest,
    DatasetCasesRequest,
    DatasetCreateRequest,
    DatasetPublishRequest,
    DatasetUpdateRequest,
    EvaluationPreflightRequest,
    EvaluationRunRequest,
)
from .service import XpertEvaluationService
from .store import (
    EvaluationConflictError,
    EvaluationNotFoundError,
    EvaluationStateError,
    XpertEvaluationStore,
)


router = APIRouter(prefix="/api/xpert-evaluations", tags=["xpert-evaluations"])
_store: XpertEvaluationStore | None = None
_service: XpertEvaluationService | None = None
_executor: XpertEvaluationExecutor | None = None


def configure_xpert_evaluations(
    *,
    storage_dir: str | Path | None,
    xpert_store: Any,
    proposal_store: Any,
    prompt_preflight: Any,
    toolset_store: Any,
    plugin_store: Any,
    rag_service: Any,
    context_store: Any,
    target_runner: TargetRunner,
    judge_runner: JudgeRunner | None,
    run_registry: Any,
    agent_table_evaluation_backend: Any | None = None,
) -> XpertEvaluationExecutor:
    global _store, _service, _executor
    _store = XpertEvaluationStore(storage_dir)
    _service = XpertEvaluationService(
        _store,
        xpert_store=xpert_store,
        proposal_store=proposal_store,
        prompt_preflight=prompt_preflight,
        toolset_store=toolset_store,
        plugin_store=plugin_store,
        rag_service=rag_service,
        context_store=context_store,
        agent_table_evaluation_backend=agent_table_evaluation_backend,
    )
    _executor = XpertEvaluationExecutor(
        _store,
        target_runner=target_runner,
        judge_runner=judge_runner,
        run_registry=run_registry,
    )
    return _executor


def get_xpert_evaluation_executor() -> XpertEvaluationExecutor:
    if _executor is None:
        raise RuntimeError("Xpert Evaluator is not configured.")
    return _executor


def get_xpert_evaluation_service() -> XpertEvaluationService:
    return _require_service()


def get_xpert_evaluation_store() -> XpertEvaluationStore:
    return _require_store()


def _require_store() -> XpertEvaluationStore:
    if _store is None:
        raise RuntimeError("Xpert Evaluator is not configured.")
    return _store


def _require_service() -> XpertEvaluationService:
    if _service is None:
        raise RuntimeError("Xpert Evaluator is not configured.")
    return _service


@router.get("/capabilities")
async def get_capabilities() -> dict[str, Any]:
    return {
        "version": "evoagentx-xpert-evaluator-v1",
        "metrics": [
            "exact_match",
            "contains",
            "json_schema",
            "citation_hit",
            "tool_call_match",
            "workflow_path_match",
            "workflow_resource_match",
            "rubric_judge",
        ],
        "dataset_limits": {"max_cases": 500, "max_cases_per_run": 100},
        "budget_limits": {
            "repetitions": [1, 3],
            "max_concurrency": [1, 4],
            "case_timeout_seconds": [10, 600],
            "max_model_calls": 64,
            "max_tool_calls": 100,
        },
        "model_policies": ["snapshot", "override"],
        "resource_fixture_limits": {
            "max_rows_per_query": 200,
            "max_fixtures_per_run": 1000,
            "max_bytes_per_run": 16 * 1024 * 1024,
        },
        "safe_mode": "read_only_fail_closed",
    }


@router.get("/datasets")
async def list_datasets(status: str | None = None) -> dict[str, Any]:
    items = await _to_thread(_require_store().list_datasets, status=status)
    return {"items": items, "total": len(items)}


@router.post("/datasets")
async def create_dataset(payload: DatasetCreateRequest) -> dict[str, Any]:
    return await _to_thread(
        _require_store().create_dataset,
        payload.name,
        payload.description,
    )


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str) -> dict[str, Any]:
    try:
        item = await _to_thread(_require_store().require_dataset, dataset_id)
        return _require_store().dataset_payload(item, include_cases=True)
    except EvaluationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: str,
    payload: DatasetUpdateRequest,
) -> dict[str, Any]:
    try:
        return await _to_thread(
            _require_store().update_dataset,
            dataset_id,
            revision=payload.revision,
            name=payload.name,
            description=payload.description,
            status=payload.status,
        )
    except EvaluationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EvaluationNotFoundError, EvaluationStateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/datasets/{dataset_id}/cases")
async def put_dataset_cases(
    dataset_id: str,
    payload: DatasetCasesRequest,
) -> dict[str, Any]:
    try:
        return await _to_thread(
            _require_store().put_cases,
            dataset_id,
            revision=payload.revision,
            cases=[item.model_dump(mode="json") for item in payload.cases],
            replace=payload.replace,
        )
    except EvaluationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EvaluationNotFoundError, EvaluationStateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/datasets/{dataset_id}/import")
async def import_dataset_cases(
    dataset_id: str,
    revision: int,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Evaluation import exceeds 5 MB.")
    try:
        cases = _parse_import(file.filename or "", content)
        return await _to_thread(
            _require_store().put_cases,
            dataset_id,
            revision=revision,
            cases=cases,
        )
    except EvaluationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, EvaluationNotFoundError, EvaluationStateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/datasets/{dataset_id}/import-conversations")
async def import_conversations(
    dataset_id: str,
    payload: ConversationImportRequest,
) -> dict[str, Any]:
    try:
        return await _to_thread(
            _require_service().import_conversations,
            dataset_id,
            revision=payload.revision,
            selections=[
                item.model_dump(mode="json") for item in payload.selections
            ],
        )
    except EvaluationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EvaluationNotFoundError, EvaluationStateError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/datasets/{dataset_id}/publish")
async def publish_dataset(
    dataset_id: str,
    payload: DatasetPublishRequest,
) -> dict[str, Any]:
    try:
        dataset = await _to_thread(_require_store().require_dataset, dataset_id)
        if str(dataset.get("origin") or "manual") == "generated":
            try:
                from server.benchmarks import get_benchmark_generation_service
            except ModuleNotFoundError:
                from benchmarks import get_benchmark_generation_service

            await _to_thread(
                get_benchmark_generation_service().assert_dataset_target_fresh,
                dataset,
            )
        return await _to_thread(
            _require_store().publish_dataset,
            dataset_id,
            revision=payload.revision,
            release_notes=payload.release_notes,
            acknowledge_calibration_warnings=(
                payload.acknowledge_calibration_warnings
            ),
        )
    except EvaluationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EvaluationNotFoundError, EvaluationStateError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/versions")
async def list_dataset_versions(dataset_id: str) -> dict[str, Any]:
    try:
        items = await _to_thread(
            _require_store().list_dataset_versions, dataset_id
        )
        return {"items": items, "total": len(items)}
    except EvaluationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/preflight")
async def preflight(payload: EvaluationPreflightRequest) -> dict[str, Any]:
    try:
        return await _to_thread(
            _require_service().preflight,
            baseline=(
                payload.baseline.model_dump(mode="json")
                if payload.baseline
                else None
            ),
            candidates=[
                item.model_dump(mode="json") for item in payload.candidates
            ],
            model_policy=payload.model_policy,
            override_model_id=payload.override_model_id,
        )
    except (EvaluationConflictError, EvaluationStateError, ValueError) as exc:
        return {
            "valid": False,
            "baseline": None,
            "candidates": [],
            "targets": [],
            "warnings": [],
            "issues": [{"code": "evaluation_preflight", "message": str(exc)[:500]}],
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
async def create_run(payload: EvaluationRunRequest) -> dict[str, Any]:
    try:
        run = await _to_thread(
            _require_service().create_run,
            payload.model_dump(mode="json"),
        )
        get_xpert_evaluation_executor().wake()
        return _sanitize_run_detail(run)
    except EvaluationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (EvaluationNotFoundError, EvaluationStateError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    try:
        item = await _to_thread(_require_service().run_detail, run_id)
        return _sanitize_run_detail(item)
    except EvaluationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    try:
        return _sanitize_run_detail(
            await _to_thread(_require_store().cancel_run, run_id)
        )
    except EvaluationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(function, *args, **kwargs)


def _parse_import(filename: str, content: bytes) -> list[dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    text = content.decode("utf-8-sig")
    if suffix == ".json":
        payload = json.loads(text)
        raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(raw_cases, list):
            raise ValueError("JSON import must be an array or contain a cases array.")
        return [dict(item) for item in raw_cases if isinstance(item, dict)]
    if suffix == ".csv":
        result: list[dict[str, Any]] = []
        for row in csv.DictReader(io.StringIO(text)):
            expected: dict[str, Any] = {}
            if row.get("exact_answer"):
                expected["exact_answer"] = row["exact_answer"]
            if row.get("contains"):
                expected["contains"] = [
                    item.strip() for item in row["contains"].split("|") if item.strip()
                ]
            if row.get("rubric"):
                expected["rubric"] = row["rubric"]
            if row.get("json_schema"):
                expected["json_schema"] = json.loads(row["json_schema"])
            result.append(
                {
                    "case_id": row.get("case_id") or None,
                    "name": row.get("name") or "",
                    "message": row.get("message") or "",
                    "tags": [
                        item.strip()
                        for item in str(row.get("tags") or "").split("|")
                        if item.strip()
                    ],
                    "expected": expected,
                }
            )
        return result
    raise ValueError("Evaluation import must be JSON or CSV.")


def _sanitize_run_detail(run: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(run, ensure_ascii=False))
    payload.pop("_resource_fixtures", None)
    dataset = dict(payload.get("dataset") or {})
    for case in dataset.get("cases") or []:
        case["message"] = str(case.get("message") or "")[:20_000]
        case["messages"] = list(case.get("messages") or [])[-20:]
    payload["dataset"] = dataset
    for target in payload.get("targets") or []:
        target.pop("workflow", None)
        target.pop("prompt_profiles", None)
        target.pop("features", None)
        target.pop("agent_config", None)
    for item in payload.get("items") or []:
        item["output"] = str(item.get("output") or "")[:20_000]
    return payload
