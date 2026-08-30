from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import statistics
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .evaluation import (
    KnowledgeEvaluationStore,
    aggregate_target_metrics,
    evaluate_promotion_gate,
    evaluate_retrieval_case,
    published_gold_checksum_valid,
)
from .evaluation_executor import KnowledgeEvaluationExecutor
from .pipeline_executor import KnowledgePipelineExecutor
from .rag_service import RagService
from .retrieval import RetrievalConfig
from .strategy_router import RULES_VERSION, RagStrategyService
from .strategy_tuning_qualification import (
    assess_chunk_sensitivity,
    build_threshold_calibration_readiness,
    build_tuning_readiness,
    ranking_fingerprint,
    realized_index_fingerprint,
    validate_tuning_dataset_pair,
)


TUNER_VERSION = "rag-strategy-tuner-v5"
VALIDATION_VERSION = "rag-strategy-validation-v1"
KNOWN_WINNER_FIXTURE_VERSION = "rag-strategy-known-winner-v1"
VALIDATION_RESAMPLE_COUNT = 3
VALIDATION_QUERY_REPETITIONS = 3
VALIDATION_BOOTSTRAP_SAMPLES = 1000
VALIDATION_CONFIDENCE_LEVEL = 0.90
VALIDATION_MAX_REGRESSION = 0.02
IMPROVEMENT_CONTRACT_VERSION = "rag-strategy-improvement-v2"
RUNNING_STATUSES = {
    "queued",
    "profiling",
    "searching",
    "building",
    "evaluating",
    "materializing",
    "validating",
}
TERMINAL_STATUSES = {"completed", "no_improvement", "failed", "cancelled"}
OBJECTIVES = {"balanced", "quality", "low_latency"}


class RagStrategyTuningError(RuntimeError):
    pass


class RagStrategyTuningNotFoundError(RagStrategyTuningError):
    pass


class RagStrategyTuningStateError(RagStrategyTuningError):
    pass


class RagStrategyTuningValidationError(ValueError):
    pass


class _TuningCancelled(RuntimeError):
    pass


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _checksum(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RagStrategyTuningStore:
    """Atomic, restart-safe state for bounded strategy tuning runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create_run(self, request: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        run = {
            "run_id": f"ragtune_{uuid.uuid4().hex}",
            "version": TUNER_VERSION,
            "kb_id": str(request["kb_id"]),
            "status": "queued",
            "progress": 0,
            "stage": "queued",
            "request": _copy(request),
            "snapshot": _copy(snapshot),
            "case_split": {},
            "validation_plan": {},
            "validation_baseline": {},
            "statistical_summary": {},
            "candidates": [],
            "pareto_front": [],
            "finalists": [],
            "trial_indexes": [],
            "trial_version_ids": [],
            "pipeline_job_ids": [],
            "evaluation_run_id": None,
            "final_version_id": None,
            "winner": None,
            "no_improvement_reason": None,
            "optimization_baseline_metrics": {},
            "optimization_gate_summary": {},
            "chunk_sensitivity": {},
            "retrieval_deduplication": {},
            "cancel_requested": False,
            "warnings": list(snapshot.get("warnings") or []),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }
        with self._lock:
            data = self._read_unlocked()
            data["runs"][run["run_id"]] = run
            self._write_unlocked(data)
        return self.payload(run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._read()["runs"].get(run_id)
        if not isinstance(run, dict):
            raise RagStrategyTuningNotFoundError("RAG strategy tuning run not found.")
        return self.payload(run)

    def list_runs(
        self, *, kb_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        items = list(self._read()["runs"].values())
        if kb_id:
            items = [item for item in items if item.get("kb_id") == kb_id]
        if status:
            items = [item for item in items if item.get("status") == status]
        items.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return [self.payload(item, detail=False) for item in items[: max(1, min(limit, 100))]]

    def claim_next_run(self) -> dict[str, Any] | None:
        with self._lock:
            data = self._read_unlocked()
            queued = [item for item in data["runs"].values() if item.get("status") == "queued"]
            if not queued:
                return None
            queued.sort(key=lambda item: float(item.get("created_at") or 0))
            run = queued[0]
            run.update(
                {
                    "status": "profiling",
                    "stage": "profiling",
                    "progress": max(1, int(run.get("progress") or 0)),
                    "started_at": run.get("started_at") or time.time(),
                    "updated_at": time.time(),
                }
            )
            self._write_unlocked(data)
            return _copy(run)

    def recover_runs(self) -> int:
        recovered = 0
        with self._lock:
            data = self._read_unlocked()
            for run in data["runs"].values():
                if run.get("status") in RUNNING_STATUSES - {"queued"}:
                    run["status"] = "queued"
                    run["stage"] = "recovery"
                    run["updated_at"] = time.time()
                    recovered += 1
            if recovered:
                self._write_unlocked(data)
        return recovered

    def update(self, run_id: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = data["runs"].get(run_id)
            if not isinstance(run, dict):
                raise RagStrategyTuningNotFoundError("RAG strategy tuning run not found.")
            run.update(_copy(values))
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return self.payload(run)

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in RUNNING_STATUSES:
            raise RagStrategyTuningStateError("Only active tuning runs can be cancelled.")
        values: dict[str, Any] = {"cancel_requested": True}
        if run["status"] == "queued":
            values.update(status="cancelled", stage="cancelled", completed_at=time.time())
        return self.update(run_id, **values)

    def retry(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in {"failed", "cancelled"}:
            raise RagStrategyTuningStateError("Only failed or cancelled tuning runs can be retried.")
        return self.update(
            run_id,
            status="queued",
            stage="queued",
            progress=0,
            cancel_requested=False,
            error=None,
            completed_at=None,
            case_split={},
            validation_plan={},
            validation_baseline={},
            statistical_summary={},
            candidates=[],
            finalists=[],
            pareto_front=[],
            trial_indexes=[],
            trial_version_ids=[],
            pipeline_job_ids=[],
            evaluation_run_id=None,
            final_version_id=None,
            winner=None,
            no_improvement_reason=None,
            optimization_baseline_metrics={},
            optimization_gate_summary={},
            chunk_sensitivity={},
            retrieval_deduplication={},
        )

    def cancelled(self, run_id: str) -> bool:
        return bool(self.get_run(run_id).get("cancel_requested"))

    def payload(self, run: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
        payload = _copy(run)
        if not detail:
            payload.pop("snapshot", None)
            payload.pop("candidates", None)
            payload.pop("finalists", None)
            payload.pop("pareto_front", None)
        return payload

    def _read(self) -> dict[str, Any]:
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": TUNER_VERSION, "runs": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        return {
            "version": TUNER_VERSION,
            "runs": raw.get("runs") if isinstance(raw.get("runs"), dict) else {},
        }

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(f"{self.path.suffix}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def stratified_split(cases: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        tags = case.get("tags") or []
        key = f"{bool(case.get('expected_no_result'))}:{str(tags[0]) if tags else 'default'}"
        buckets.setdefault(key, []).append(case)
    optimization: list[str] = []
    holdout: list[str] = []
    for key in sorted(buckets):
        ordered = sorted(
            buckets[key],
            key=lambda case: hashlib.sha256(
                f"{seed}:{case.get('case_id')}".encode("utf-8")
            ).hexdigest(),
        )
        holdout_count = max(1, round(len(ordered) / 3))
        holdout.extend(str(case["case_id"]) for case in ordered[:holdout_count])
        optimization.extend(str(case["case_id"]) for case in ordered[holdout_count:])
    target_holdout = min(4, len(cases) - 1)
    if len(holdout) < target_holdout:
        move = optimization[: target_holdout - len(holdout)]
        holdout.extend(move)
        optimization = [case_id for case_id in optimization if case_id not in move]
    return {
        "seed": seed,
        "optimization_case_ids": sorted(optimization),
        "holdout_case_ids": sorted(holdout),
    }


def _case_stratum(case: dict[str, Any]) -> str:
    tags = case.get("tags") or []
    return f"{bool(case.get('expected_no_result'))}:{str(tags[0]) if tags else 'default'}"


def repeated_validation_plan(
    cases: list[dict[str, Any]],
    holdout_case_ids: list[str],
    seed: int,
    *,
    resample_count: int = VALIDATION_RESAMPLE_COUNT,
) -> dict[str, Any]:
    """Create deterministic stratified bootstrap samples inside the fixed Holdout.

    Optimization cases are never admitted into these samples. Sampling with
    replacement gives several paired views of a small immutable Holdout without
    leaking its results back into candidate generation.
    """

    allowed = {str(item) for item in holdout_case_ids}
    buckets: dict[str, list[str]] = {}
    for case in cases:
        case_id = str(case.get("case_id") or "")
        if case_id in allowed:
            buckets.setdefault(_case_stratum(case), []).append(case_id)
    for values in buckets.values():
        values.sort()
    resamples: list[dict[str, Any]] = []
    for index in range(max(1, resample_count)):
        sample_seed = int(seed) + (index + 1) * 1009
        rng = random.Random(sample_seed)
        sample_ids: list[str] = []
        for key in sorted(buckets):
            values = buckets[key]
            sample_ids.extend(rng.choice(values) for _ in range(len(values)))
        resamples.append(
            {
                "index": index + 1,
                "seed": sample_seed,
                "case_ids": sample_ids,
            }
        )
    payload = {
        "validation_version": VALIDATION_VERSION,
        "seed": int(seed),
        "holdout_case_ids": sorted(allowed),
        "strata": {key: len(values) for key, values in sorted(buckets.items())},
        "resamples": resamples,
        "query_repetitions": VALIDATION_QUERY_REPETITIONS,
        "bootstrap_samples": VALIDATION_BOOTSTRAP_SAMPLES,
        "confidence_level": VALIDATION_CONFIDENCE_LEVEL,
        "max_quality_regression": VALIDATION_MAX_REGRESSION,
    }
    payload["checksum"] = _checksum(payload)
    return payload


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarize_repeated_case_results(
    case_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in case_results:
        grouped.setdefault(str(result.get("case_id") or ""), []).append(result)
    summaries: list[dict[str, Any]] = []
    for case_id in sorted(grouped):
        values = grouped[case_id]
        completed = [item for item in values if item.get("status") == "completed"]
        metric_names = sorted(
            {
                str(name)
                for item in completed
                for name in (item.get("metrics") or {}).keys()
            }
        )
        latencies = [float(item.get("latency_ms") or 0) for item in values]
        no_result_votes = sum(1 for item in completed if item.get("no_result"))
        summary = {
            "case_id": case_id,
            "status": "completed" if len(completed) == len(values) else "failed",
            "metrics": {
                name: round(
                    sum(float((item.get("metrics") or {}).get(name) or 0) for item in completed)
                    / len(completed),
                    6,
                )
                for name in metric_names
            }
            if completed
            else {},
            "latency_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
            "expected_no_result": bool(values[0].get("expected_no_result")),
            "stratum": str(
                values[0].get("stratum")
                or ("no_result" if values[0].get("expected_no_result") else "answerable")
            ),
            "no_result": bool(completed) and no_result_votes * 2 >= len(completed),
            "warning_count": sum(int(item.get("warning_count") or 0) for item in values),
            "repeat_count": len(values),
            "completed_repeat_count": len(completed),
        }
        if len(completed) != len(values):
            summary["error"] = next(
                (str(item.get("error") or "")[:240] for item in values if item.get("status") == "failed"),
                "Repeated validation query failed.",
            )
        summaries.append(summary)
    return summaries


def _case_quality_score(summary: dict[str, Any]) -> float:
    metrics = summary.get("metrics") or {}
    if summary.get("expected_no_result"):
        return float(metrics.get("no_result_accuracy") or 0)
    return float(metrics.get("ndcg_at_10") or 0)


def paired_statistical_validation(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Return deterministic paired quality evidence for one fixed Holdout."""

    baseline_by_id = {
        str(item.get("case_id") or ""): item
        for item in baseline.get("case_summaries") or []
    }
    candidate_by_id = {
        str(item.get("case_id") or ""): item
        for item in candidate.get("case_summaries") or []
    }
    expected_ids = [str(item) for item in plan.get("holdout_case_ids") or []]
    missing = [
        case_id
        for case_id in expected_ids
        if case_id not in baseline_by_id or case_id not in candidate_by_id
    ]
    failed = [
        case_id
        for case_id in expected_ids
        if case_id in baseline_by_id
        and case_id in candidate_by_id
        and (
            baseline_by_id[case_id].get("status") != "completed"
            or candidate_by_id[case_id].get("status") != "completed"
        )
    ]
    if missing or failed or not expected_ids:
        return {
            "validation_version": VALIDATION_VERSION,
            "status": "insufficient",
            "passed": False,
            "missing_case_ids": missing,
            "failed_case_ids": failed,
            "reason": "paired_holdout_results_incomplete",
        }

    deltas = {
        case_id: _case_quality_score(candidate_by_id[case_id])
        - _case_quality_score(baseline_by_id[case_id])
        for case_id in expected_ids
    }
    point_delta = sum(deltas.values()) / len(deltas)
    resample_deltas: list[float] = []
    for resample in plan.get("resamples") or []:
        ids = [str(item) for item in resample.get("case_ids") or [] if str(item) in deltas]
        if ids:
            resample_deltas.append(sum(deltas[item] for item in ids) / len(ids))

    cases_by_stratum: dict[str, list[str]] = {}
    for case_id in expected_ids:
        summary = baseline_by_id[case_id]
        key = str(
            summary.get("stratum")
            or ("no_result" if summary.get("expected_no_result") else "answerable")
        )
        cases_by_stratum.setdefault(key, []).append(case_id)
    rng = random.Random(int(plan.get("seed") or 0) + 7919)
    bootstrap_deltas: list[float] = []
    for _ in range(int(plan.get("bootstrap_samples") or VALIDATION_BOOTSTRAP_SAMPLES)):
        sampled: list[str] = []
        for key in sorted(cases_by_stratum):
            values = cases_by_stratum[key]
            sampled.extend(rng.choice(values) for _ in range(len(values)))
        bootstrap_deltas.append(sum(deltas[item] for item in sampled) / len(sampled))
    confidence = float(plan.get("confidence_level") or VALIDATION_CONFIDENCE_LEVEL)
    tail = (1.0 - confidence) / 2.0
    lower = _percentile(bootstrap_deltas, tail)
    upper = _percentile(bootstrap_deltas, 1.0 - tail)
    max_regression = float(
        plan.get("max_quality_regression") or VALIDATION_MAX_REGRESSION
    )
    required_stable = max(1, (len(resample_deltas) * 2 + 2) // 3)
    stable_count = sum(1 for value in resample_deltas if value >= -max_regression)
    passed = lower >= -max_regression and stable_count >= required_stable
    return {
        "validation_version": VALIDATION_VERSION,
        "status": "completed",
        "passed": passed,
        "primary_metric": "case_weighted_ndcg_or_no_result_accuracy",
        "quality_delta": round(point_delta, 6),
        "confidence_level": confidence,
        "confidence_interval": {
            "lower": round(lower, 6),
            "upper": round(upper, 6),
        },
        "bootstrap_samples": len(bootstrap_deltas),
        "resample_deltas": [round(item, 6) for item in resample_deltas],
        "stable_resample_count": stable_count,
        "required_stable_resamples": required_stable,
        "max_quality_regression": max_regression,
        "quality_improvement_confident": bool(point_delta >= 0.01 and lower >= 0),
        "holdout_case_count": len(expected_ids),
        "query_repetitions": int(plan.get("query_repetitions") or 1),
        "baseline_p95_latency_ms": float(
            (baseline.get("metrics") or {}).get("p95_latency_ms") or 0
        ),
        "candidate_p95_latency_ms": float(
            (candidate.get("metrics") or {}).get("p95_latency_ms") or 0
        ),
    }


def retrieval_candidates(base: dict[str, Any], *, degraded: bool) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    modes = ["fulltext"] if degraded else ["fulltext", "vector", "hybrid"]
    for mode in modes:
        weights = [0.5] if mode != "hybrid" else [0.3, 0.5, 0.7]
        for top_k in (5, 10):
            for vector_weight in weights:
                values.append(
                    RetrievalConfig.from_mapping(
                        {
                            "mode": mode,
                            "top_k": top_k,
                            "vector_weight": vector_weight,
                            "fulltext_weight": 1 - vector_weight,
                            "score_threshold": 0,
                            "candidate_multiplier": 4,
                            "rerank_enabled": False,
                            "rerank_provider": "none",
                            "rerank_top_n": top_k,
                        }
                    ).payload()
                )
    values.insert(0, RetrievalConfig.from_mapping(base).payload())
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        checksum = retrieval_semantic_checksum(value)
        if checksum not in seen:
            seen.add(checksum)
            deduped.append(value)
    return deduped


def retrieval_semantic_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Return only fields that can change retrieval behavior for this mode."""

    config = RetrievalConfig.from_mapping(value).payload()
    payload: dict[str, Any] = {
        "mode": config["mode"],
        "top_k": int(config["top_k"]),
        "score_threshold": round(float(config["score_threshold"]), 6),
        "candidate_multiplier": int(config["candidate_multiplier"]),
        "rerank_enabled": bool(config["rerank_enabled"]),
    }
    if config["mode"] == "hybrid":
        payload["vector_weight"] = round(float(config["vector_weight"]), 6)
        payload["fulltext_weight"] = round(float(config["fulltext_weight"]), 6)
    if config["rerank_enabled"]:
        payload.update(
            {
                "rerank_provider": str(config["rerank_provider"]),
                "rerank_model": str(config["rerank_model"]),
                "rerank_top_n": int(config["rerank_top_n"]),
            }
        )
    return payload


def retrieval_semantic_checksum(value: dict[str, Any]) -> str:
    return _checksum(retrieval_semantic_payload(value))


def chunker_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    base = _copy(snapshot["base_chunker"])
    strategy = str(base.get("strategy") or "recursive_character")
    if strategy in {
        "recursive_estimated_token",
        "parent_child_estimated_token",
    }:
        # Round 4A establishes a stable budget unit; it does not authorize
        # character-derived ratio search in that new unit. Retrieval tuning
        # may continue, while the chunker remains byte-for-byte unchanged.
        return [base]
    values = [base]
    recommendation = snapshot.get("router_recommendation") or {}
    for profile in recommendation.get("profiles") or []:
        if isinstance(profile, dict) and isinstance(profile.get("chunker"), dict):
            values.append(_copy(profile["chunker"]))
    if strategy == "parent_child":
        parent = int(base.get("parent_chunk_size") or 1800)
        child = int(base.get("child_chunk_size") or 450)
        for ratio in (0.75, 1.25):
            candidate = _copy(base)
            candidate["parent_chunk_size"] = max(400, min(4000, round(parent * ratio)))
            candidate["child_chunk_size"] = max(100, min(2000, round(child * ratio)))
            candidate["parent_chunk_overlap"] = min(
                int(candidate["parent_chunk_size"] * 0.1), candidate["parent_chunk_size"] - 1
            )
            candidate["child_chunk_overlap"] = min(
                int(candidate["child_chunk_size"] * 0.1), candidate["child_chunk_size"] - 1
            )
            values.append(candidate)
    else:
        size = int(base.get("chunk_size") or 700)
        for ratio in (0.7, 1.3):
            candidate = _copy(base)
            candidate["chunk_size"] = max(100, min(4000, round(size * ratio)))
            candidate["chunk_overlap"] = min(
                int(candidate["chunk_size"] * 0.1), candidate["chunk_size"] - 1
            )
            values.append(candidate)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        checksum = _checksum(value)
        if checksum not in seen:
            seen.add(checksum)
            deduped.append(value)
    return deduped[:4]


def calibrate_threshold(result: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
    cases_by_id = {str(case["case_id"]): case for case in result["cases"]}
    case_results = [
        item for item in result.get("case_results") or [] if isinstance(item, dict)
    ]
    observed_case_ids = {
        str(item.get("case_id") or "") for item in case_results if item.get("case_id")
    }
    rankings = [
        item
        for case_result in case_results
        for item in case_result.get("ranking") or []
        if isinstance(item, dict)
    ]
    empty_ranking_case_count = sum(
        1
        for case_result in case_results
        if not any(isinstance(item, dict) for item in case_result.get("ranking") or [])
    )
    missing_case_result_count = len(set(cases_by_id) - observed_case_ids)
    missing_fused_scores = [
        item for item in rankings if item.get("fused_score") is None
    ]
    if missing_fused_scores or empty_ranking_case_count or missing_case_result_count:
        current = round(float(retrieval.get("score_threshold") or 0), 6)
        return {
            "retrieval": {**_copy(retrieval), "score_threshold": current},
            "metrics": _copy(result.get("metrics") or {}),
            "threshold_candidates": [current],
            "threshold_front": [],
            "threshold_selection_reason": "missing_fused_score_evidence",
            "threshold_calibration_eligible": False,
            "threshold_score_domain": "fused_score",
            "missing_fused_score_count": (
                len(missing_fused_scores) + empty_ranking_case_count
                + missing_case_result_count
            ),
        }
    thresholds = _threshold_candidates(result, retrieval, cases_by_id=cases_by_id)
    points: list[dict[str, Any]] = []
    for threshold in thresholds:
        rescored: list[dict[str, Any]] = []
        for case_result in result["case_results"]:
            case = cases_by_id[str(case_result["case_id"])]
            ranking = [
                item
                for item in case_result.get("ranking") or []
                if float(item.get("fused_score") or 0) >= threshold
            ]
            sources = [
                {
                    "chunk_id": item.get("chunk_id"),
                    "document_id": item.get("document_id"),
                    "source_document_id": item.get("document_id"),
                    "document_name": item.get("document_name"),
                    "source_block_id": item.get("source_block_id"),
                    "page_number": item.get("page_number"),
                    "score": item.get("fused_score"),
                }
                for item in ranking
            ]
            rescored.append(
                evaluate_retrieval_case(
                    sources,
                    list(case.get("expected_refs") or []),
                    ks=[1, 3, 5, 10],
                    latency_ms=float(case_result.get("latency_ms") or 0),
                    expected_no_result=bool(case.get("expected_no_result")),
                )
            )
        metrics = aggregate_target_metrics(rescored, ks=[1, 3, 5, 10])
        points.append({"threshold": threshold, "metrics": metrics})

    baseline_threshold = round(float(retrieval.get("score_threshold") or 0), 6)
    baseline = min(points, key=lambda item: abs(float(item["threshold"]) - baseline_threshold))
    baseline_metrics = dict(baseline["metrics"])
    pareto = _threshold_pareto_front(points)
    admissible = [
        item
        for item in pareto
        if float((item.get("metrics") or {}).get("recall_at_5") or 0)
        >= float(baseline_metrics.get("recall_at_5") or 0) - 0.02
        and float((item.get("metrics") or {}).get("ndcg_at_10") or 0)
        >= float(baseline_metrics.get("ndcg_at_10") or 0) - 0.02
        and float((item.get("metrics") or {}).get("false_positive_rate") or 0)
        <= float(baseline_metrics.get("false_positive_rate") or 0)
    ]
    improved = [
        item
        for item in admissible
        if float((item.get("metrics") or {}).get("false_positive_rate") or 0)
        <= float(baseline_metrics.get("false_positive_rate") or 0) - 0.01
    ]
    if improved:
        selected = min(
            improved,
            key=lambda item: (
                float((item.get("metrics") or {}).get("false_positive_rate") or 0),
                -float((item.get("metrics") or {}).get("recall_at_5") or 0),
                -float((item.get("metrics") or {}).get("ndcg_at_10") or 0),
                float(item["threshold"]),
            ),
        )
        selection_reason = "hard_negative_false_positive_improved"
    else:
        selected = baseline
        selection_reason = "baseline_preserved_no_safe_negative_improvement"
    threshold = float(selected["threshold"])
    metrics = dict(selected["metrics"])
    return {
        "retrieval": {**_copy(retrieval), "score_threshold": threshold},
        "metrics": metrics,
        "threshold_candidates": thresholds,
        "threshold_front": [
            {
                "threshold": float(item["threshold"]),
                "recall_at_5": float((item.get("metrics") or {}).get("recall_at_5") or 0),
                "ndcg_at_10": float((item.get("metrics") or {}).get("ndcg_at_10") or 0),
                "no_result_accuracy": float(
                    (item.get("metrics") or {}).get("no_result_accuracy") or 0
                ),
                "false_positive_rate": float(
                    (item.get("metrics") or {}).get("false_positive_rate") or 0
                ),
            }
            for item in pareto
        ],
        "threshold_selection_reason": selection_reason,
        "threshold_calibration_eligible": True,
        "threshold_score_domain": "fused_score",
        "missing_fused_score_count": 0,
    }


def _threshold_candidates(
    result: dict[str, Any],
    retrieval: dict[str, Any],
    *,
    cases_by_id: dict[str, dict[str, Any]],
) -> list[float]:
    negative_top_scores: list[float] = []
    positive_scores: list[float] = []
    all_scores: list[float] = []
    for case_result in result.get("case_results") or []:
        case = cases_by_id.get(str(case_result.get("case_id") or ""), {})
        ranking = [
            item
            for item in case_result.get("ranking") or []
            if item.get("fused_score") is not None
        ]
        scores = [round(float(item.get("fused_score") or 0), 6) for item in ranking]
        all_scores.extend(scores)
        if case.get("expected_no_result"):
            if scores:
                negative_top_scores.append(max(scores))
        else:
            positive_scores.extend(scores[:5])

    thresholds = {
        0.0,
        round(float(retrieval.get("score_threshold") or 0), 6),
    }
    for values, add_epsilon in (
        (sorted(negative_top_scores), True),
        (sorted(positive_scores), False),
        (sorted(all_scores), True),
    ):
        if not values:
            continue
        for fraction in (0.0, 0.5, 1.0):
            observed = values[min(len(values) - 1, round((len(values) - 1) * fraction))]
            thresholds.add(
                min(1.0, max(0.0, round(observed + (0.000001 if add_epsilon else 0), 6)))
            )
    ordered = sorted(thresholds)
    if len(ordered) <= 8:
        return ordered
    selected = [ordered[0], ordered[-1]]
    for fraction in (0.2, 0.4, 0.6, 0.8):
        selected.append(ordered[round((len(ordered) - 1) * fraction)])
    current = round(float(retrieval.get("score_threshold") or 0), 6)
    selected.append(current)
    return sorted(set(selected))[:8]


def _threshold_pareto_front(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    front: list[dict[str, Any]] = []
    for point in points:
        metrics = point.get("metrics") or {}
        dominated = False
        for other in points:
            if other is point:
                continue
            other_metrics = other.get("metrics") or {}
            at_least_as_good = (
                float(other_metrics.get("recall_at_5") or 0)
                >= float(metrics.get("recall_at_5") or 0)
                and float(other_metrics.get("ndcg_at_10") or 0)
                >= float(metrics.get("ndcg_at_10") or 0)
                and float(other_metrics.get("false_positive_rate") or 0)
                <= float(metrics.get("false_positive_rate") or 0)
            )
            strictly_better = (
                float(other_metrics.get("recall_at_5") or 0)
                > float(metrics.get("recall_at_5") or 0)
                or float(other_metrics.get("ndcg_at_10") or 0)
                > float(metrics.get("ndcg_at_10") or 0)
                or float(other_metrics.get("false_positive_rate") or 0)
                < float(metrics.get("false_positive_rate") or 0)
            )
            if at_least_as_good and strictly_better:
                dominated = True
                break
        if not dominated:
            front.append(point)
    return sorted(front, key=lambda item: float(item["threshold"]))


def mark_semantic_duplicate_candidates(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    representatives: dict[str, str] = {}
    duplicate_ids: list[str] = []
    for candidate in candidates:
        semantic_key = _checksum(
            {
                "realized_index": candidate.get("realized_index_fingerprint"),
                "ranking": candidate.get("ranking_fingerprint"),
                "retrieval": retrieval_semantic_payload(
                    dict(candidate.get("retrieval") or {})
                ),
            }
        )
        candidate["semantic_outcome_checksum"] = semantic_key
        representative_id = representatives.get(semantic_key)
        if representative_id:
            if candidate.get("automatic_winner_eligible", True):
                candidate["automatic_winner_eligible"] = False
                candidate["ineligible_reason"] = "semantic_duplicate"
            candidate["duplicate_of_candidate_id"] = representative_id
            duplicate_ids.append(str(candidate.get("candidate_id") or ""))
        else:
            representatives[semantic_key] = str(candidate.get("candidate_id") or "")
    return {
        "candidate_count": len(candidates),
        "unique_semantic_outcomes": len(representatives),
        "duplicate_count": len(duplicate_ids),
        "duplicate_candidate_ids": duplicate_ids,
    }


def rank_key(metrics: dict[str, Any], cost: dict[str, Any], objective: str) -> tuple[Any, ...]:
    quality = (
        -float(metrics.get("ndcg_at_10") or 0),
        -float(metrics.get("recall_at_5") or 0),
        -float(metrics.get("mrr_at_10") or 0),
        -float(metrics.get("citation_coverage") or 0),
        -float(metrics.get("no_result_accuracy") or 0),
    )
    latency = float(metrics.get("p95_latency_ms") or 0)
    size = int(cost.get("estimated_index_bytes") or 0)
    build = float(cost.get("build_duration_ms") or 0)
    checksum = str(cost.get("checksum") or "")
    if objective == "quality":
        return (*quality, latency, size, build, checksum)
    if objective == "low_latency":
        return (latency, size, build, *quality, checksum)
    return (quality[0], latency, size, build, *quality[1:], checksum)


def pareto_front(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    front: list[dict[str, Any]] = []
    for candidate in candidates:
        metrics = candidate.get("holdout_metrics") or {}
        cost = candidate.get("cost") or {}
        dominated = False
        for other in candidates:
            if other is candidate:
                continue
            other_metrics = other.get("holdout_metrics") or {}
            other_cost = other.get("cost") or {}
            no_worse = (
                float(other_metrics.get("ndcg_at_10") or 0)
                >= float(metrics.get("ndcg_at_10") or 0)
                and float(other_metrics.get("p95_latency_ms") or 0)
                <= float(metrics.get("p95_latency_ms") or 0)
                and int(other_cost.get("estimated_index_bytes") or 0)
                <= int(cost.get("estimated_index_bytes") or 0)
            )
            better = (
                float(other_metrics.get("ndcg_at_10") or 0)
                > float(metrics.get("ndcg_at_10") or 0)
                or float(other_metrics.get("p95_latency_ms") or 0)
                < float(metrics.get("p95_latency_ms") or 0)
                or int(other_cost.get("estimated_index_bytes") or 0)
                < int(cost.get("estimated_index_bytes") or 0)
            )
            if no_worse and better:
                dominated = True
                break
        if not dominated:
            front.append(candidate)
    return front


def improvement_summary(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    baseline_cost: dict[str, Any],
    candidate_cost: dict[str, Any],
    *,
    objective: str = "balanced",
) -> dict[str, Any]:
    quality_delta = float(candidate_metrics.get("ndcg_at_10") or 0) - float(
        baseline_metrics.get("ndcg_at_10") or 0
    )
    no_result_accuracy_delta = float(
        candidate_metrics.get("no_result_accuracy") or 0
    ) - float(baseline_metrics.get("no_result_accuracy") or 0)
    baseline_p95 = float(baseline_metrics.get("p95_latency_ms") or 0)
    candidate_p95 = float(candidate_metrics.get("p95_latency_ms") or 0)
    latency_ratio = (baseline_p95 - candidate_p95) / baseline_p95 if baseline_p95 else 0.0
    baseline_size = int(baseline_cost.get("estimated_index_bytes") or 0)
    candidate_size = int(candidate_cost.get("estimated_index_bytes") or 0)
    size_ratio = (baseline_size - candidate_size) / baseline_size if baseline_size else 0.0
    baseline_chunks = int(baseline_cost.get("chunk_count") or 0)
    candidate_chunks = int(candidate_cost.get("chunk_count") or 0)
    chunk_ratio = (
        (baseline_chunks - candidate_chunks) / baseline_chunks if baseline_chunks else 0.0
    )
    quality_effective = quality_delta >= 0.01 or no_result_accuracy_delta >= 0.01
    any_effective = bool(
        quality_effective
        or latency_ratio >= 0.1
        or size_ratio >= 0.1
        or chunk_ratio >= 0.1
    )
    return {
        "contract_version": IMPROVEMENT_CONTRACT_VERSION,
        "quality_delta": round(quality_delta, 6),
        "no_result_accuracy_delta": round(no_result_accuracy_delta, 6),
        "p95_reduction_ratio": round(latency_ratio, 6),
        "index_size_reduction_ratio": round(size_ratio, 6),
        "chunk_reduction_ratio": round(chunk_ratio, 6),
        # Quality runs must not manufacture a winner from timing jitter when
        # paired retrieval quality is unchanged. Other objectives retain the
        # historical multi-dimensional improvement contract.
        "effective": quality_effective if objective == "quality" else any_effective,
    }


def apply_optimization_gate(
    candidates: list[dict[str, Any]],
    *,
    baseline_metrics: dict[str, Any],
    baseline_cost: dict[str, Any],
    policy: dict[str, Any],
    objective: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Apply the knowledge gate before candidates consume Holdout budget."""

    evaluated: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    failed_checks: dict[str, int] = {}
    for candidate in candidates:
        item = _copy(candidate)
        metrics = dict(item.get("optimization_metrics") or {})
        gate_metrics = {
            **metrics,
            # Search trials query each optimization case once. A single P95 is
            # diagnostic only; latency gating is deferred to repeated Holdout.
            "p95_latency_ms": float(baseline_metrics.get("p95_latency_ms") or 0),
        }
        gate = evaluate_promotion_gate(
            gate_metrics,
            baseline=baseline_metrics,
            policy=policy,
        )
        gate["latency_evidence"] = "deferred_to_repeated_holdout"
        item["optimization_gate"] = gate
        item["optimization_improvement"] = improvement_summary(
            baseline_metrics,
            metrics,
            baseline_cost,
            dict(item.get("cost") or {}),
            objective=objective,
        )
        evaluated.append(item)
        if not gate.get("passed"):
            for check in gate.get("checks") or []:
                if not check.get("passed"):
                    check_id = str(check.get("id") or "unknown")
                    failed_checks[check_id] = failed_checks.get(check_id, 0) + 1
            continue
        if item.get("automatic_winner_eligible"):
            eligible.append(item)

    eligible.sort(
        key=lambda item: rank_key(
            item["optimization_metrics"], item["cost"], objective
        )
    )
    summary = {
        "evaluated_count": len(evaluated),
        "passed_count": sum(
            1 for item in evaluated if item.get("optimization_gate", {}).get("passed")
        ),
        "eligible_count": len(eligible),
        "failed_check_counts": dict(sorted(failed_checks.items())),
    }
    return evaluated, eligible, summary


class RagStrategyTuner:
    """Deterministic bounded search over fixed RAG and evaluation snapshots."""

    def __init__(
        self,
        service: RagService,
        store: RagStrategyTuningStore,
        evaluation_store: KnowledgeEvaluationStore,
        pipeline_executor: KnowledgePipelineExecutor,
        evaluation_executor: KnowledgeEvaluationExecutor,
        *,
        run_registry: Any | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        self.service = service
        self.store = store
        self.evaluation_store = evaluation_store
        self.pipeline_executor = pipeline_executor
        self.evaluation_executor = evaluation_executor
        self.run_registry = run_registry
        self.poll_interval = max(0.1, poll_interval)
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def capabilities(self) -> dict[str, Any]:
        rerank = self.service.reranker.capabilities()
        return {
            "version": TUNER_VERSION,
            "rules_version": RULES_VERSION,
            "objectives": sorted(OBJECTIVES),
            "limits": {
                "max_chunk_indexes": 4,
                "max_retrieval_trials": 24,
                "max_finalists": 3,
                "minimum_evaluation_cases": 12,
                "minimum_positive_cases": 30,
                "minimum_reviewed_hard_negatives": 12,
            },
            "validation": {
                "version": VALIDATION_VERSION,
                "resample_count": VALIDATION_RESAMPLE_COUNT,
                "query_repetitions": VALIDATION_QUERY_REPETITIONS,
                "bootstrap_samples": VALIDATION_BOOTSTRAP_SAMPLES,
                "confidence_level": VALIDATION_CONFIDENCE_LEVEL,
                "max_quality_regression": VALIDATION_MAX_REGRESSION,
                "latency_aggregation": "median_per_case_then_p95",
                "known_winner_fixture_version": KNOWN_WINNER_FIXTURE_VERSION,
                "known_winner_validation_status": "blocked_until_lexical_v2",
                "known_winner_scenarios": [
                    "threshold_recovery",
                    "already_optimal_control",
                ],
            },
            "tunable": [
                "retrieval_mode",
                "top_k",
                "hybrid_weight",
                "score_threshold",
                "rerank_finalists",
            ],
            "deferred": ["chunker"],
            "chunker_search_status": "frozen_until_calibrated_token_budget",
            "fixed": ["processor", "vision", "embedding"],
            "rerank_available": bool(
                rerank.get("api_configured") or rerank.get("llm_configured")
            ),
        }

    def preflight(self, request: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_request(request)
        kb_id = normalized["kb_id"]
        base_version_id = normalized["base_version_id"]
        base = self.service.get_pipeline_version(base_version_id)
        if base.get("kb_id") != kb_id or base.get("status") not in {"ready", "active"}:
            raise RagStrategyTuningValidationError(
                "The base version must be a ready or active version in the selected knowledge base."
            )
        if int(base.get("index_schema_version") or 1) < 2:
            raise RagStrategyTuningValidationError("Strategy tuning requires a V2 index.")
        base_job = self.service.get_pipeline_job(str(base.get("job_id") or ""))
        if not base_job.get("sources"):
            raise RagStrategyTuningValidationError("The base source snapshot is unavailable.")
        evaluation_set = self.evaluation_store.get_set(normalized["eval_set_id"])
        evaluation_version = self.evaluation_store.get_set_version(
            normalized["eval_set_id"], normalized["eval_set_version"]
        )
        cases = list(evaluation_version.get("cases") or [])
        if evaluation_set.get("kb_id") != kb_id or evaluation_version.get("kb_id") != kb_id:
            raise RagStrategyTuningValidationError(
                "The published evaluation set must belong to the selected knowledge base."
            )
        readiness = build_tuning_readiness(
            evaluation_version,
            target_version_id=base_version_id,
        )
        dataset_pair: dict[str, Any] | None = None
        calibration_readiness: dict[str, Any] | None = None
        calibration_version: dict[str, Any] | None = None
        tuning_checksum_valid: bool | None = None
        calibration_checksum_valid: bool | None = None
        if normalized["request_contract_version"] == "rag-strategy-tuning-request-v3":
            calibration_set = self.evaluation_store.get_set(
                normalized["calibration_eval_set_id"]
            )
            calibration_version = self.evaluation_store.get_set_version(
                normalized["calibration_eval_set_id"],
                normalized["calibration_eval_set_version"],
            )
            if (
                calibration_set.get("kb_id") != kb_id
                or calibration_version.get("kb_id") != kb_id
            ):
                raise RagStrategyTuningValidationError(
                    "The calibration evaluation set must belong to the selected knowledge base."
                )
            dataset_pair = validate_tuning_dataset_pair(
                evaluation_version, calibration_version
            )
            tuning_checksum_valid = published_gold_checksum_valid(
                evaluation_version
            )
            calibration_checksum_valid = published_gold_checksum_valid(
                calibration_version
            )
            calibration_readiness = build_threshold_calibration_readiness(
                calibration_version,
                target_version_id=base_version_id,
            )
        positive = [case for case in cases if not case.get("expected_no_result")]
        stable_blocks = bool(positive) and all(
            all(
                str(ref.get("match_mode") or "") == "source_block"
                and bool(ref.get("source_block_id"))
                for ref in case.get("expected_refs") or []
            )
            for case in positive
        )
        warnings: list[str] = list(readiness.get("warnings") or [])
        if readiness.get("blockers"):
            warnings.extend(str(item) for item in readiness["blockers"])
        if not stable_blocks:
            warnings.append(
                "Some positive cases lack stable source-block Gold; tuning is limited to retrieval parameters."
            )
        if dataset_pair and not dataset_pair.get("qualified"):
            warnings.append(
                "Tuning and calibration datasets failed role, corpus, identity, or query-leakage checks."
            )
        if calibration_readiness and not calibration_readiness.get("eligible"):
            warnings.append(
                "The threshold-calibration dataset is not eligible for calibration."
            )
        if tuning_checksum_valid is False or calibration_checksum_valid is False:
            warnings.append(
                "A published tuning dataset checksum is invalid; the run is blocked."
            )
        embedding = dict(base.get("embedding_profile") or {})
        degraded_embedding = bool(embedding.get("degraded")) or str(
            embedding.get("provider") or ""
        ) == "hash"
        if degraded_embedding:
            warnings.append(
                "The fixed version uses hash embedding; vector and hybrid trials cannot win automatically."
            )
        rerank = self.service.reranker.capabilities()
        rerank_available = bool(
            rerank.get("api_configured") or rerank.get("llm_configured")
        )
        if normalized["enable_rerank"] and not rerank_available:
            raise RagStrategyTuningValidationError(
                "Rerank was requested but no rerank provider is configured."
            )
        recommendation = None
        if normalized["recommendation_id"]:
            recommendation = RagStrategyService(self.service).get_recommendation(
                normalized["recommendation_id"]
            )
            if recommendation.get("kb_id") != kb_id:
                raise RagStrategyTuningValidationError(
                    "The Router recommendation belongs to another knowledge base."
                )
            if recommendation.get("state") not in {"ready", "applied"}:
                raise RagStrategyTuningValidationError(
                    "The Router recommendation is stale or unavailable."
                )
        source_snapshot = [
            {
                "source_id": str(item.get("source_id") or ""),
                "document_id": str(item.get("document_id") or ""),
                "content_hash": str(item.get("content_hash") or ""),
            }
            for item in base_job.get("sources") or []
            if isinstance(item, dict)
        ]
        snapshot = {
            "kb_id": kb_id,
            "base_version_id": base_version_id,
            "base_version": int(base.get("version") or 0),
            "base_job_id": str(base.get("job_id") or ""),
            "source_snapshot_hash": _checksum(source_snapshot),
            "eval_set_id": normalized["eval_set_id"],
            "eval_set_version": normalized["eval_set_version"],
            "eval_set_version_id": str(evaluation_version.get("version_id") or ""),
            "eval_case_count": len(cases),
            "eval_checksum": str(evaluation_version.get("checksum") or _checksum(cases)),
            "request_contract_version": normalized["request_contract_version"],
            "tuning_eval_set_id": normalized["eval_set_id"],
            "tuning_eval_set_version": normalized["eval_set_version"],
            "calibration_eval_set_id": normalized.get("calibration_eval_set_id"),
            "calibration_eval_set_version": normalized.get(
                "calibration_eval_set_version"
            ),
            "calibration_eval_set_version_id": str(
                (calibration_version or {}).get("version_id") or ""
            )
            or None,
            "calibration_eval_checksum": str(
                (calibration_version or {}).get("checksum") or ""
            )
            or None,
            "calibration_case_count": len(
                list((calibration_version or {}).get("cases") or [])
            ),
            "dataset_pair_qualification": dataset_pair,
            "published_checksum_qualification": {
                "tuning_valid": tuning_checksum_valid,
                "calibration_valid": calibration_checksum_valid,
            },
            "calibration_readiness": calibration_readiness,
            "benchmark_role": str(readiness.get("benchmark_role") or "unclassified"),
            "tuning_readiness": readiness,
            "selection_eligible": bool(readiness.get("selection_eligible"))
            and (not dataset_pair or bool(dataset_pair.get("qualified")))
            and (
                not calibration_readiness
                or bool(calibration_readiness.get("eligible"))
            )
            and tuning_checksum_valid is not False
            and calibration_checksum_valid is not False,
            "rules_version": RULES_VERSION,
            "router_recommendation": recommendation,
            "chunk_tuning_available": bool(
                (readiness.get("dimensions") or {}).get("chunking", {}).get("eligible")
            ),
            "threshold_tuning_available": (
                bool(calibration_readiness.get("eligible"))
                if calibration_readiness is not None
                else bool(
                    (readiness.get("dimensions") or {})
                    .get("threshold", {})
                    .get("eligible")
                )
            ),
            "retrieval_only": not bool(
                (readiness.get("dimensions") or {}).get("chunking", {}).get("eligible")
            ),
            "embedding_degraded": degraded_embedding,
            "rerank_available": rerank_available,
            "processor_profile": _copy(base.get("processor_profile") or {}),
            "vision_profile": _copy(base.get("vision_profile") or {}),
            "embedding_profile": embedding,
            "base_chunker": _copy(
                (base_job.get("config_snapshot") or {})
                .get("stages", {})
                .get("stage_chunker", {})
            ),
            "base_retrieval": _copy(base.get("retrieval_profile") or {}),
            "warnings": warnings,
        }
        snapshot["snapshot_hash"] = _checksum(
            {key: value for key, value in snapshot.items() if key != "router_recommendation"}
        )
        return snapshot

    def create_run(self, request: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_request(request)
        snapshot = self.preflight(normalized)
        if not snapshot.get("selection_eligible"):
            readiness = dict(snapshot.get("tuning_readiness") or {})
            reasons = [str(item) for item in readiness.get("blockers") or []]
            detail = " ".join(reasons[:3]) or "The evaluation evidence is not tuning-ready."
            raise RagStrategyTuningValidationError(detail)
        run = self.store.create_run(normalized, snapshot)
        self.notify()
        return run

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self.store.recover_runs()
        self._task = asyncio.create_task(self._worker(), name="rag-strategy-tuner")
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
        self._task = None

    def notify(self) -> None:
        self._wake.set()

    async def run_once(self) -> bool:
        run = self.store.claim_next_run()
        if run is None:
            return False
        await self._execute(run)
        return True

    async def _worker(self) -> None:
        while not self._stopping:
            processed = await self.run_once()
            if processed:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    def _normalize_request(self, request: dict[str, Any]) -> dict[str, Any]:
        objective = str(request.get("objective") or "balanced")
        if objective not in OBJECTIVES:
            raise RagStrategyTuningValidationError("Unknown tuning objective.")
        max_indexes = int(request.get("max_chunk_indexes") or 4)
        max_trials = int(request.get("max_retrieval_trials") or 24)
        max_finalists = int(request.get("max_finalists") or 3)
        if not 1 <= max_indexes <= 4:
            raise RagStrategyTuningValidationError("max_chunk_indexes must be 1-4.")
        if not 1 <= max_trials <= 24:
            raise RagStrategyTuningValidationError("max_retrieval_trials must be 1-24.")
        if not 1 <= max_finalists <= 3:
            raise RagStrategyTuningValidationError("max_finalists must be 1-3.")
        normalized = {
            "kb_id": str(request.get("kb_id") or ""),
            "base_version_id": str(request.get("base_version_id") or ""),
            "eval_set_id": str(
                request.get("tuning_eval_set_id") or request.get("eval_set_id") or ""
            ),
            "eval_set_version": int(
                request.get("tuning_eval_set_version")
                or request.get("eval_set_version")
                or 0
            ),
            "calibration_eval_set_id": str(
                request.get("calibration_eval_set_id") or ""
            )
            or None,
            "calibration_eval_set_version": int(
                request.get("calibration_eval_set_version") or 0
            )
            or None,
            "request_contract_version": (
                "rag-strategy-tuning-request-v3"
                if request.get("tuning_eval_set_id")
                or request.get("calibration_eval_set_id")
                else "rag-strategy-tuning-request-v2"
            ),
            "recommendation_id": str(request.get("recommendation_id") or "") or None,
            "objective": objective,
            "seed": int(request.get("seed") or 42),
            "max_chunk_indexes": max_indexes,
            "max_retrieval_trials": max_trials,
            "max_finalists": max_finalists,
            "enable_rerank": bool(request.get("enable_rerank", False)),
            "rerank_provider": str(request.get("rerank_provider") or "auto"),
            "rerank_model": str(request.get("rerank_model") or "")[:200],
        }
        if not normalized["kb_id"] or not normalized["base_version_id"]:
            raise RagStrategyTuningValidationError("Knowledge base and base version are required.")
        if not normalized["eval_set_id"] or normalized["eval_set_version"] < 1:
            raise RagStrategyTuningValidationError(
                "A published evaluation set version is required."
            )
        if normalized["request_contract_version"] == "rag-strategy-tuning-request-v3" and (
            not normalized["calibration_eval_set_id"]
            or not normalized["calibration_eval_set_version"]
        ):
            raise RagStrategyTuningValidationError(
                "V3 tuning requires a published threshold-calibration dataset version."
            )
        return normalized

    async def _execute(self, run: dict[str, Any]) -> None:
        run_id = str(run["run_id"])
        trial_versions: list[str] = list(run.get("trial_version_ids") or [])
        registry_id = await self._ensure_registry_run(run)
        try:
            await self._checkpoint(
                registry_id,
                "rag_strategy_tuning.started",
                "RAG strategy tuning started",
                {
                    "tuning_run_id": run_id,
                    "kb_id": str(run["kb_id"]),
                    "objective": str((run.get("request") or {}).get("objective") or "balanced"),
                },
            )
            self._raise_if_cancelled(run_id)
            snapshot = self.preflight(dict(run["request"]))
            if snapshot["snapshot_hash"] != run["snapshot"]["snapshot_hash"]:
                raise RagStrategyTuningStateError(
                    "The fixed source or evaluation snapshot is no longer available."
                )
            search = await self._search(run_id, run["request"], snapshot, trial_versions)
            await self._checkpoint(
                registry_id,
                "rag_strategy_tuning.search_completed",
                "RAG strategy search completed",
                {
                    "tuning_run_id": run_id,
                    "candidate_count": len(self.store.get_run(run_id).get("candidates") or []),
                    "finalist_count": len(self.store.get_run(run_id).get("finalists") or []),
                    "eligible_count": len(search.get("eligible") or []),
                },
            )
            if not search["eligible"]:
                self.store.update(
                    run_id,
                    status="no_improvement",
                    stage="completed",
                    progress=100,
                    completed_at=time.time(),
                    winner=None,
                )
                await self._finish_registry(
                    registry_id,
                    "completed",
                    metadata={"tuning_run_id": run_id, "outcome": "no_improvement"},
                )
                return
            materialized = await self._materialize(
                run_id, run["request"], snapshot, search["eligible"][0]
            )
            completed = self.store.get_run(run_id)
            if not materialized:
                await self._checkpoint(
                    registry_id,
                    "rag_strategy_tuning.no_improvement",
                    "The materialized candidate did not pass full evaluation",
                    {
                        "tuning_run_id": run_id,
                        "final_version_id": str(completed.get("final_version_id") or ""),
                        "evaluation_run_id": str(completed.get("evaluation_run_id") or ""),
                    },
                )
                await self._finish_registry(
                    registry_id,
                    "completed",
                    metadata={
                        "tuning_run_id": run_id,
                        "outcome": "no_improvement",
                        "final_version_id": str(completed.get("final_version_id") or ""),
                    },
                )
                return
            await self._checkpoint(
                registry_id,
                "rag_strategy_tuning.completed",
                "RAG strategy candidate materialized",
                {
                    "tuning_run_id": run_id,
                    "final_version_id": str(completed.get("final_version_id") or ""),
                    "evaluation_run_id": str(completed.get("evaluation_run_id") or ""),
                },
            )
            await self._finish_registry(
                registry_id,
                "completed",
                metadata={
                    "tuning_run_id": run_id,
                    "final_version_id": str(completed.get("final_version_id") or ""),
                },
            )
        except asyncio.CancelledError:
            raise
        except _TuningCancelled:
            await self._cancel_trial_jobs(run_id)
            self.store.update(
                run_id,
                status="cancelled",
                stage="cancelled",
                completed_at=time.time(),
            )
            await self._finish_registry(
                registry_id,
                "cancelled",
                metadata={"tuning_run_id": run_id},
            )
        except Exception as exc:
            safe_error = self.service._safe_pipeline_error(exc)
            self.store.update(
                run_id,
                status="failed",
                stage="failed",
                error=safe_error,
                completed_at=time.time(),
            )
            await self._checkpoint(
                registry_id,
                "rag_strategy_tuning.failed",
                "RAG strategy tuning failed",
                {"tuning_run_id": run_id, "error": safe_error[:300]},
                severity="error",
            )
            await self._finish_registry(
                registry_id,
                "failed",
                error=safe_error,
                metadata={"tuning_run_id": run_id},
            )
        finally:
            known = [
                *trial_versions,
                *self.store.get_run(run_id).get("trial_version_ids", []),
            ]
            for version_id in list(dict.fromkeys(known)):
                try:
                    self.service.cleanup_strategy_tuning_trial_version(version_id)
                except Exception:
                    pass
            for job_id in self.store.get_run(run_id).get("pipeline_job_ids", []):
                try:
                    self.service.cleanup_strategy_tuning_trial_job(str(job_id))
                except Exception:
                    pass

    async def _search(
        self,
        run_id: str,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        trial_versions: list[str],
    ) -> dict[str, Any]:
        evaluation_version = self.evaluation_store.get_set_version(
            snapshot["eval_set_id"], snapshot["eval_set_version"]
        )
        cases = list(evaluation_version.get("cases") or [])
        calibration_cases: list[dict[str, Any]] = []
        if snapshot.get("request_contract_version") == "rag-strategy-tuning-request-v3":
            calibration_version = self.evaluation_store.get_set_version(
                str(snapshot["calibration_eval_set_id"]),
                int(snapshot["calibration_eval_set_version"]),
            )
            calibration_cases = list(calibration_version.get("cases") or [])
        split = stratified_split(cases, int(request["seed"]))
        validation_plan = repeated_validation_plan(
            cases,
            list(split["holdout_case_ids"]),
            int(request["seed"]),
        )
        self.store.update(
            run_id,
            status="searching",
            stage="retrieval_search",
            progress=8,
            case_split=split,
            validation_plan=validation_plan,
        )
        optimization_ids = set(split["optimization_case_ids"])
        holdout_ids = set(split["holdout_case_ids"])
        optimization_cases = [case for case in cases if case["case_id"] in optimization_ids]
        holdout_cases = [case for case in cases if case["case_id"] in holdout_ids]
        retrieval_profiles = retrieval_candidates(
            snapshot["base_retrieval"], degraded=bool(snapshot["embedding_degraded"])
        )
        if snapshot.get("threshold_tuning_available"):
            retrieval_profiles = [
                {**profile, "score_threshold": 0.0}
                for profile in retrieval_profiles
            ]
        else:
            fixed_threshold = float(
                (snapshot.get("base_retrieval") or {}).get("score_threshold") or 0
            )
            retrieval_profiles = [
                {**profile, "score_threshold": fixed_threshold}
                for profile in retrieval_profiles
            ]
        chunkers = chunker_candidates(snapshot)
        if snapshot["retrieval_only"]:
            chunkers = chunkers[:1]
        max_indexes = int(request["max_chunk_indexes"])
        max_trials = int(request["max_retrieval_trials"])
        current_run = self.store.get_run(run_id)
        candidates: list[dict[str, Any]] = list(current_run.get("candidates") or [])
        candidate_cache = {
            str(item.get("search_profile_checksum") or ""): item
            for item in candidates
            if item.get("search_profile_checksum")
        }
        trial_cache = {
            str(item.get("chunker_checksum") or ""): item
            for item in current_run.get("trial_indexes") or []
            if item.get("chunker_checksum")
        }
        baseline_cost = self.service.pipeline_version_cost_summary(
            snapshot["base_version_id"]
        )
        baseline_candidate_checksum = _checksum(
            {
                "chunker": snapshot["base_chunker"],
                "retrieval": snapshot["base_retrieval"],
            }
        )
        probe_retrieval_checksum = retrieval_semantic_checksum(retrieval_profiles[0])
        base_chunker_checksum = _checksum(snapshot["base_chunker"])
        for chunk_index, chunker in enumerate(chunkers[:max_indexes]):
            self._raise_if_cancelled(run_id)
            chunker_checksum = _checksum(chunker)
            if chunk_index == 0:
                version_id = snapshot["base_version_id"]
                cost = baseline_cost
                index_job_id = str(snapshot["base_job_id"])
            else:
                cached_trial = trial_cache.get(chunker_checksum)
                version_id = ""
                cost: dict[str, Any] = {}
                if cached_trial:
                    try:
                        cached_version = self.service.get_pipeline_version(
                            str(cached_trial["version_id"])
                        )
                        if str((cached_version.get("origin") or {}).get("kind") or "") == "rag_strategy_tuner_trial":
                            version_id = str(cached_version["version_id"])
                            cost = dict(cached_trial.get("cost") or {})
                            index_job_id = str(cached_trial.get("job_id") or "")
                    except Exception:
                        version_id = ""
                if not version_id:
                    self.store.update(
                        run_id,
                        status="building",
                        stage="building_trial_index",
                        progress=min(55, 12 + chunk_index * 10),
                    )
                    job = self.service.create_strategy_tuning_pipeline_job(
                        snapshot["kb_id"],
                        base_version_id=snapshot["base_version_id"],
                        chunker_profile=chunker,
                        retrieval_profile=retrieval_profiles[0],
                        tuning_run_id=run_id,
                        trial=True,
                    )
                    self._append_run_value(run_id, "pipeline_job_ids", job["job_id"])
                    self.pipeline_executor.notify()
                    version_id = await self._wait_for_pipeline(job["job_id"], run_id)
                    cost = self.service.pipeline_version_cost_summary(version_id)
                    index_job_id = str(job["job_id"])
                    cached_trial = {
                        "chunker_checksum": chunker_checksum,
                        "version_id": version_id,
                        "job_id": str(job["job_id"]),
                        "cost": cost,
                    }
                    trial_cache[chunker_checksum] = cached_trial
                    self._upsert_run_item(
                        run_id,
                        "trial_indexes",
                        cached_trial,
                        identity_key="chunker_checksum",
                    )
                trial_versions.append(version_id)
                self._append_run_value(run_id, "trial_version_ids", version_id)
                if not cost:
                    cost = self.service.pipeline_version_cost_summary(version_id)
            index_job = self.service.get_pipeline_job(index_job_id)
            index_fingerprint = realized_index_fingerprint(
                chunker=chunker,
                cost=cost,
                document_results=list(index_job.get("document_results") or []),
            )
            for retrieval in retrieval_profiles:
                if len(candidates) >= max_trials:
                    break
                search_profile_checksum = _checksum(
                    {
                        "version_id": version_id,
                        "retrieval": retrieval_semantic_payload(retrieval),
                    }
                )
                if search_profile_checksum in candidate_cache:
                    continue
                result = await self._evaluate_profile(
                    version_id, retrieval, optimization_cases
                )
                retrieval_input_checksum = retrieval_semantic_checksum(retrieval)
                if snapshot.get("threshold_tuning_available"):
                    calibration_result = await self._evaluate_profile(
                        version_id,
                        retrieval,
                        calibration_cases or optimization_cases,
                    )
                    calibrated = calibrate_threshold(calibration_result, retrieval)
                    optimized_result = await self._evaluate_profile(
                        version_id,
                        calibrated["retrieval"],
                        optimization_cases,
                    )
                else:
                    calibration_result = None
                    calibrated = {
                        "retrieval": _copy(retrieval),
                        "threshold_candidates": [
                            float(retrieval.get("score_threshold") or 0)
                        ],
                        "metrics": _copy(result["metrics"]),
                    }
                    optimized_result = result
                candidate = {
                    "candidate_id": f"ragtc_{uuid.uuid4().hex}",
                    "chunker": _copy(chunker),
                    "retrieval": calibrated["retrieval"],
                    "threshold_candidates": calibrated["threshold_candidates"],
                    "threshold_front": calibrated.get("threshold_front") or [],
                    "threshold_selection_reason": calibrated.get(
                        "threshold_selection_reason"
                    ),
                    "version_id": version_id,
                    "trial_version": chunk_index > 0,
                    "optimization_metrics": optimized_result["metrics"],
                    "threshold_calibration_metrics": _copy(
                        (calibration_result or {}).get("metrics") or {}
                    ),
                    "threshold_calibration_eligible": calibrated.get(
                        "threshold_calibration_eligible",
                        not snapshot.get("threshold_tuning_available"),
                    ),
                    "cost": {**cost, "checksum": _checksum(cost)},
                    "checksum": _checksum(
                        {"chunker": chunker, "retrieval": calibrated["retrieval"]}
                    ),
                    "search_profile_checksum": search_profile_checksum,
                    "chunker_checksum": chunker_checksum,
                    "realized_index_fingerprint": index_fingerprint,
                    "retrieval_input_checksum": retrieval_input_checksum,
                    "ranking_fingerprint": ranking_fingerprint(
                        list(optimized_result.get("case_results") or [])
                    ),
                    "rerank_call_count": 0,
                    "automatic_winner_eligible": not (
                        snapshot["embedding_degraded"]
                        and calibrated["retrieval"]["mode"] in {"vector", "hybrid"}
                    ),
                }
                if not candidate["automatic_winner_eligible"]:
                    candidate["ineligible_reason"] = "hash_embedding"
                elif snapshot.get("threshold_tuning_available") and not candidate.get(
                    "threshold_calibration_eligible"
                ):
                    candidate["automatic_winner_eligible"] = False
                    candidate["ineligible_reason"] = "threshold_calibration_evidence"
                elif candidate["checksum"] == baseline_candidate_checksum:
                    candidate["automatic_winner_eligible"] = False
                    candidate["ineligible_reason"] = "baseline_equivalent"
                candidates.append(candidate)
                candidate_cache[search_profile_checksum] = candidate
                self.store.update(run_id, candidates=candidates)
            if len(candidates) >= max_trials:
                break
        chunk_sensitivity = assess_chunk_sensitivity(
            candidates,
            probe_retrieval_checksum=probe_retrieval_checksum,
            enabled=bool(snapshot.get("chunk_tuning_available")),
        )
        if chunk_sensitivity["status"] == "insufficient":
            for candidate in candidates:
                if str(candidate.get("chunker_checksum") or "") == base_chunker_checksum:
                    continue
                candidate["automatic_winner_eligible"] = False
                candidate["ineligible_reason"] = "chunk_insensitive"
        retrieval_deduplication = mark_semantic_duplicate_candidates(candidates)
        self.store.update(
            run_id,
            candidates=candidates,
            chunk_sensitivity=chunk_sensitivity,
            retrieval_deduplication=retrieval_deduplication,
        )
        gate_policy = self.evaluation_store.get_gate_policy(snapshot["kb_id"])
        optimization_baseline = await self._evaluate_profile(
            snapshot["base_version_id"],
            snapshot["base_retrieval"],
            optimization_cases,
        )
        candidates, ranked_training, optimization_gate_summary = apply_optimization_gate(
            candidates,
            baseline_metrics=optimization_baseline["metrics"],
            baseline_cost=baseline_cost,
            policy=gate_policy,
            objective=request["objective"],
        )
        self.store.update(
            run_id,
            candidates=candidates,
            optimization_baseline_metrics=optimization_baseline["metrics"],
            optimization_gate_summary=optimization_gate_summary,
            no_improvement_reason=None if ranked_training else "optimization_gate",
        )
        if not ranked_training:
            return {
                "eligible": [],
                "baseline_metrics": optimization_baseline["metrics"],
            }
        self.store.update(
            run_id,
            status="evaluating",
            stage="holdout_evaluation",
            progress=62,
        )
        baseline_validation_checksum = _checksum(
            {
                "validation_plan": validation_plan["checksum"],
                "version_id": snapshot["base_version_id"],
                "retrieval": retrieval_semantic_payload(snapshot["base_retrieval"]),
            }
        )
        stored_baseline = dict(
            self.store.get_run(run_id).get("validation_baseline") or {}
        )
        if (
            stored_baseline.get("validation_version") == VALIDATION_VERSION
            and stored_baseline.get("checksum") == baseline_validation_checksum
        ):
            baseline = stored_baseline
        else:
            evaluated_baseline = await self._evaluate_profile(
                snapshot["base_version_id"],
                snapshot["base_retrieval"],
                holdout_cases,
                repetitions=VALIDATION_QUERY_REPETITIONS,
            )
            baseline = {
                "validation_version": VALIDATION_VERSION,
                "checksum": baseline_validation_checksum,
                "metrics": evaluated_baseline["metrics"],
                "case_summaries": evaluated_baseline["case_summaries"],
            }
            self.store.update(run_id, validation_baseline=baseline)
        stored_finalists = list(self.store.get_run(run_id).get("finalists") or [])
        finalist_cache = {
            str(item.get("checksum") or ""): item
            for item in stored_finalists
            if item.get("checksum")
        }
        finalists: list[dict[str, Any]] = []
        for candidate in ranked_training[: int(request["max_finalists"])]:
            finalist = finalist_cache.get(str(candidate.get("checksum") or ""))
            if finalist is not None and (
                (finalist.get("statistical_validation") or {}).get(
                    "validation_version"
                )
                != VALIDATION_VERSION
                or (finalist.get("improvement") or {}).get("contract_version")
                != IMPROVEMENT_CONTRACT_VERSION
                or finalist.get("validation_plan_checksum")
                != validation_plan["checksum"]
            ):
                finalist = None
            if finalist is None:
                result = await self._evaluate_profile(
                    candidate["version_id"],
                    candidate["retrieval"],
                    holdout_cases,
                    repetitions=VALIDATION_QUERY_REPETITIONS,
                )
                finalist = _copy(candidate)
                finalist["holdout_metrics"] = result["metrics"]
                finalist["validation_plan_checksum"] = validation_plan["checksum"]
                finalist["statistical_validation"] = paired_statistical_validation(
                    baseline, result, validation_plan
                )
                finalist["promotion_gate"] = evaluate_promotion_gate(
                    result["metrics"], baseline=baseline["metrics"], policy=gate_policy
                )
                finalist["improvement"] = improvement_summary(
                    baseline["metrics"],
                    result["metrics"],
                    baseline_cost,
                    finalist["cost"],
                    objective=request["objective"],
                )
                finalist_cache[str(finalist["checksum"])] = finalist
                stored_finalists = [
                    item
                    for item in stored_finalists
                    if str(item.get("checksum") or "") != str(finalist["checksum"])
                ]
                stored_finalists.append(finalist)
                self.store.update(run_id, finalists=stored_finalists)
            finalists.append(finalist)
        if request.get("enable_rerank") and finalists:
            reranked: list[dict[str, Any]] = []
            for candidate in finalists[:2]:
                retrieval = {
                    **candidate["retrieval"],
                    "rerank_enabled": True,
                    "rerank_provider": str(request.get("rerank_provider") or "auto"),
                    "rerank_model": str(request.get("rerank_model") or ""),
                    "rerank_top_n": int(candidate["retrieval"]["top_k"]),
                }
                rerank_checksum = _checksum(
                    {"chunker": candidate["chunker"], "retrieval": retrieval}
                )
                reranked_candidate = finalist_cache.get(rerank_checksum)
                if reranked_candidate is not None and (
                    (reranked_candidate.get("statistical_validation") or {}).get(
                        "validation_version"
                    )
                    != VALIDATION_VERSION
                    or (reranked_candidate.get("improvement") or {}).get(
                        "contract_version"
                    )
                    != IMPROVEMENT_CONTRACT_VERSION
                    or reranked_candidate.get("validation_plan_checksum")
                    != validation_plan["checksum"]
                ):
                    reranked_candidate = None
                if reranked_candidate is None:
                    result = await self._evaluate_profile(
                        candidate["version_id"],
                        retrieval,
                        holdout_cases,
                        repetitions=VALIDATION_QUERY_REPETITIONS,
                    )
                    reranked_candidate = {
                        **_copy(candidate),
                        "candidate_id": f"ragtc_{uuid.uuid4().hex}",
                        "retrieval": retrieval,
                        "checksum": rerank_checksum,
                        "holdout_metrics": result["metrics"],
                        "rerank_call_count": len(holdout_cases)
                        * VALIDATION_QUERY_REPETITIONS,
                        "validation_plan_checksum": validation_plan["checksum"],
                    }
                    reranked_candidate[
                        "statistical_validation"
                    ] = paired_statistical_validation(
                        baseline, result, validation_plan
                    )
                    reranked_candidate["promotion_gate"] = evaluate_promotion_gate(
                        result["metrics"], baseline=baseline["metrics"], policy=gate_policy
                    )
                    reranked_candidate["improvement"] = improvement_summary(
                        baseline["metrics"],
                        result["metrics"],
                        baseline_cost,
                        candidate["cost"],
                        objective=request["objective"],
                    )
                    finalist_cache[rerank_checksum] = reranked_candidate
                    stored_finalists = [
                        item
                        for item in stored_finalists
                        if str(item.get("checksum") or "") != rerank_checksum
                    ]
                    stored_finalists.append(reranked_candidate)
                    self.store.update(run_id, finalists=stored_finalists)
                reranked.append(reranked_candidate)
            finalists.extend(reranked)
        front = pareto_front(finalists)
        eligible = [
            item
            for item in front
            if item.get("promotion_gate", {}).get("passed")
            and item.get("improvement", {}).get("effective")
            and item.get("statistical_validation", {}).get("passed")
        ]
        eligible.sort(
            key=lambda item: rank_key(
                item["holdout_metrics"], item["cost"], request["objective"]
            )
        )
        if eligible:
            no_improvement_reason = None
        elif any(
            item.get("promotion_gate", {}).get("passed")
            and item.get("improvement", {}).get("effective")
            and not item.get("statistical_validation", {}).get("passed")
            for item in front
        ):
            no_improvement_reason = "statistical_gate"
        else:
            no_improvement_reason = "holdout_gate"
        self.store.update(
            run_id,
            progress=78,
            finalists=finalists,
            pareto_front=[item["candidate_id"] for item in front],
            no_improvement_reason=no_improvement_reason,
            statistical_summary={
                "validation_version": VALIDATION_VERSION,
                "evaluated_finalist_count": len(finalists),
                "statistically_non_degrading_count": sum(
                    1
                    for item in finalists
                    if item.get("statistical_validation", {}).get("passed")
                ),
                "eligible_count": len(eligible),
                "query_repetitions": VALIDATION_QUERY_REPETITIONS,
                "resample_count": len(validation_plan.get("resamples") or []),
                "confidence_level": VALIDATION_CONFIDENCE_LEVEL,
            },
        )
        return {
            "eligible": eligible,
            "baseline_metrics": baseline["metrics"],
            "evaluation_version": evaluation_version,
        }

    async def _materialize(
        self,
        run_id: str,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        winner: dict[str, Any],
    ) -> bool:
        if not (winner.get("statistical_validation") or {}).get("passed"):
            raise RagStrategyTuningStateError(
                "The winner lacks a passing paired Holdout validation report."
            )
        self.store.update(
            run_id,
            status="materializing",
            stage="materializing_winner",
            progress=82,
            winner=winner,
        )
        current = self.store.get_run(run_id)
        final_version_id = str(current.get("final_version_id") or "")
        if final_version_id:
            version = self.service.get_pipeline_version(final_version_id)
            origin = version.get("origin") or {}
            if (
                str(origin.get("kind") or "") != "rag_strategy_tuner"
                or str(origin.get("source_run_id") or "") != run_id
            ):
                raise RagStrategyTuningStateError(
                    "The persisted materialized version does not belong to this tuner run."
                )
        else:
            materialization_job: dict[str, Any] | None = None
            for job_id in current.get("pipeline_job_ids") or []:
                try:
                    candidate_job = self.service.get_pipeline_job(str(job_id))
                except Exception:
                    continue
                origin = candidate_job.get("origin") or {}
                if (
                    str(origin.get("kind") or "") == "rag_strategy_tuner"
                    and str(origin.get("source_run_id") or "") == run_id
                ):
                    materialization_job = candidate_job
                    break
            if materialization_job is None:
                materialization_job = self.service.create_strategy_tuning_pipeline_job(
                    snapshot["kb_id"],
                    base_version_id=snapshot["base_version_id"],
                    chunker_profile=winner["chunker"],
                    retrieval_profile=winner["retrieval"],
                    tuning_run_id=run_id,
                    trial=False,
                )
                self._append_run_value(
                    run_id, "pipeline_job_ids", str(materialization_job["job_id"])
                )
                self.pipeline_executor.notify()
            final_version_id = await self._wait_for_pipeline(
                str(materialization_job["job_id"]), run_id
            )
        self.store.update(
            run_id,
            status="validating",
            stage="full_evaluation",
            progress=90,
            final_version_id=final_version_id,
        )
        evaluation_version = self.evaluation_store.get_set_version(
            snapshot["eval_set_id"], snapshot["eval_set_version"]
        )
        evaluation_set = self.evaluation_store.get_set(snapshot["eval_set_id"])
        current = self.store.get_run(run_id)
        evaluation_run_id = str(current.get("evaluation_run_id") or "")
        if evaluation_run_id:
            official_run = self.evaluation_store.get_run(evaluation_run_id)
            target_versions = {
                str(item.get("version_id") or "")
                for item in official_run.get("targets") or []
                if isinstance(item, dict)
            }
            if (
                str(official_run.get("eval_set_id") or "") != snapshot["eval_set_id"]
                or int(official_run.get("eval_set_version") or 0)
                != int(snapshot["eval_set_version"])
                or target_versions
                != {snapshot["base_version_id"], final_version_id}
            ):
                raise RagStrategyTuningStateError(
                    "The persisted evaluation run does not match this tuner snapshot."
                )
        else:
            official_run = self.evaluation_store.create_run(
                evaluation_set=evaluation_set,
                evaluation_set_version=evaluation_version,
                targets=[
                    {
                        "target_id": f"target_{snapshot['base_version_id']}",
                        "version_id": snapshot["base_version_id"],
                        "label": "Fixed baseline",
                        "retrieval": snapshot["base_retrieval"],
                        "respect_profile_top_k": True,
                    },
                    {
                        "target_id": f"target_{final_version_id}",
                        "version_id": final_version_id,
                        "label": "Strategy tuner candidate",
                        "retrieval": winner["retrieval"],
                        "respect_profile_top_k": True,
                    },
                ],
                baseline_version_id=snapshot["base_version_id"],
                ks=[1, 3, 5, 10],
                gate_policy=self.evaluation_store.get_gate_policy(snapshot["kb_id"]),
            )
            evaluation_run_id = str(official_run["run_id"])
            self.store.update(run_id, evaluation_run_id=evaluation_run_id)
            self.evaluation_executor.notify()
        completed_evaluation = await self._wait_for_evaluation(
            evaluation_run_id, run_id
        )
        final_target = next(
            (
                item
                for item in completed_evaluation.get("target_results", [])
                if item.get("version_id") == final_version_id
            ),
            None,
        )
        if not isinstance(final_target, dict) or not final_target.get(
            "promotion_gate", {}
        ).get("passed"):
            self.store.update(
                run_id,
                status="no_improvement",
                stage="completed",
                progress=100,
                completed_at=time.time(),
                no_improvement_reason="full_evaluation_gate",
                winner={
                    **winner,
                    "materialized_version_id": final_version_id,
                    "full_evaluation_metrics": _copy(
                        final_target.get("metrics") if isinstance(final_target, dict) else {}
                    ),
                    "full_evaluation_gate": _copy(
                        final_target.get("promotion_gate")
                        if isinstance(final_target, dict)
                        else {}
                    ),
                },
            )
            return False
        self.store.update(
            run_id,
            status="completed",
            stage="completed",
            progress=100,
            completed_at=time.time(),
            no_improvement_reason=None,
            winner={**winner, "materialized_version_id": final_version_id},
        )
        return True

    async def _evaluate_profile(
        self,
        version_id: str,
        retrieval: dict[str, Any],
        cases: list[dict[str, Any]],
        *,
        repetitions: int = 1,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        top_k = max(1, min(int(retrieval.get("top_k") or 5), 50))
        for case in cases:
            for repeat_index in range(max(1, min(int(repetitions), 5))):
                started = time.perf_counter()
                try:
                    response = await self.service.query_pipeline_version(
                        version_id,
                        str(case["query"]),
                        top_k=top_k,
                        retrieval={**retrieval, "top_k": top_k},
                        generate_answer=False,
                    )
                    result = evaluate_retrieval_case(
                        list(response.get("sources") or []),
                        list(case.get("expected_refs") or []),
                        ks=[1, 3, 5, 10],
                        latency_ms=(time.perf_counter() - started) * 1000,
                        warnings=list(response.get("warnings") or []),
                        expected_no_result=bool(case.get("expected_no_result")),
                    )
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "metrics": {},
                        "ranking": [],
                        "latency_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "expected_no_result": bool(case.get("expected_no_result")),
                        "error": self.service._safe_pipeline_error(exc),
                    }
                result["case_id"] = str(case["case_id"])
                result["repeat_index"] = repeat_index
                result["stratum"] = _case_stratum(case)
                results.append(result)
        case_summaries = summarize_repeated_case_results(results)
        return {
            "metrics": {
                **aggregate_target_metrics(case_summaries, ks=[1, 3, 5, 10]),
                "execution_count": len(results),
                "query_repetitions": max(1, min(int(repetitions), 5)),
                "latency_aggregation": "median_per_case_then_p95",
            },
            "case_results": results,
            "case_summaries": case_summaries,
            "cases": _copy(cases),
        }

    async def _wait_for_pipeline(self, job_id: str, run_id: str) -> str:
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            self._raise_if_cancelled(run_id)
            job = self.service.get_pipeline_job(job_id)
            status = str(job.get("status") or "")
            if status == "succeeded":
                return str(job["candidate_version_id"])
            if status in {"failed", "cancelled"}:
                raise RagStrategyTuningStateError(
                    str(job.get("error") or f"Pipeline trial {status}.")
                )
            if self.pipeline_executor._task is None:
                await self.pipeline_executor.run_once()
            else:
                await asyncio.sleep(self.poll_interval)
        raise RagStrategyTuningStateError("Pipeline trial timed out.")

    async def _wait_for_evaluation(self, evaluation_run_id: str, run_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            self._raise_if_cancelled(run_id)
            evaluation = self.evaluation_store.get_run(evaluation_run_id)
            status = str(evaluation.get("status") or "")
            if status == "succeeded":
                return evaluation
            if status in {"failed", "cancelled"}:
                raise RagStrategyTuningStateError(
                    str(evaluation.get("error") or f"Evaluation {status}.")
                )
            if self.evaluation_executor._task is None:
                await self.evaluation_executor.run_once()
            else:
                await asyncio.sleep(self.poll_interval)
        raise RagStrategyTuningStateError("Full evaluation timed out.")

    def _raise_if_cancelled(self, run_id: str) -> None:
        if self.store.cancelled(run_id):
            raise _TuningCancelled()

    def _append_run_value(self, run_id: str, key: str, value: str) -> None:
        current = self.store.get_run(run_id)
        values = [str(item) for item in current.get(key, [])]
        if value not in values:
            values.append(value)
            self.store.update(run_id, **{key: values})

    def _upsert_run_item(
        self,
        run_id: str,
        key: str,
        value: dict[str, Any],
        *,
        identity_key: str,
    ) -> None:
        current = self.store.get_run(run_id)
        items = [dict(item) for item in current.get(key, []) if isinstance(item, dict)]
        identity = str(value.get(identity_key) or "")
        items = [item for item in items if str(item.get(identity_key) or "") != identity]
        items.append(_copy(value))
        self.store.update(run_id, **{key: items})

    async def _ensure_registry_run(self, run: dict[str, Any]) -> str | None:
        if self.run_registry is None:
            return None
        existing_id = str(run.get("run_registry_id") or "")
        if existing_id and await self.run_registry.get_run(existing_id) is not None:
            await self.run_registry.update_run(existing_id, status="running")
            return existing_id
        registry_run = await self.run_registry.create_run(
            "rag_strategy_tuning",
            f"RAG strategy tuning: {run['kb_id']}",
            status="running",
            source_id=str(run["run_id"]),
            metadata={
                "tuning_run_id": str(run["run_id"]),
                "kb_id": str(run["kb_id"]),
                "base_version_id": str((run.get("request") or {}).get("base_version_id") or ""),
                "eval_set_id": str((run.get("request") or {}).get("eval_set_id") or ""),
            },
        )
        self.store.update(str(run["run_id"]), run_registry_id=registry_run.run_id)
        return registry_run.run_id

    async def _checkpoint(
        self,
        run_id: str | None,
        event_type: str,
        title: str,
        metadata: dict[str, Any],
        *,
        severity: str = "info",
    ) -> None:
        if self.run_registry is None or not run_id:
            return
        await self.run_registry.record_checkpoint(
            run_id,
            event_type=event_type,
            title=title,
            severity=severity,
            metadata=metadata,
        )

    async def _finish_registry(
        self,
        run_id: str | None,
        status: str,
        error: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.run_registry is None or not run_id:
            return
        await self.run_registry.update_run(
            run_id,
            status=status,
            error=error,
            metadata=metadata,
        )

    async def _cancel_trial_jobs(self, run_id: str) -> None:
        job_ids = self.store.get_run(run_id).get("pipeline_job_ids", [])
        pending: list[str] = []
        for job_id in job_ids:
            try:
                job = self.service.get_pipeline_job(str(job_id))
                if str((job.get("origin") or {}).get("kind") or "") != "rag_strategy_tuner_trial":
                    continue
                if job.get("status") in {"queued", "running"}:
                    self.service.request_pipeline_job_cancel(str(job_id))
                    pending.append(str(job_id))
            except Exception:
                continue
        if not pending:
            return
        self.pipeline_executor.notify()
        deadline = time.monotonic() + 30
        while pending and time.monotonic() < deadline:
            remaining: list[str] = []
            for job_id in pending:
                try:
                    status = str(self.service.get_pipeline_job(job_id).get("status") or "")
                    if status not in {"succeeded", "failed", "cancelled"}:
                        remaining.append(job_id)
                except Exception:
                    pass
            pending = remaining
            if pending:
                await asyncio.sleep(self.poll_interval)
