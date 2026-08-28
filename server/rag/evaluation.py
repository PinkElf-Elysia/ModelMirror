from __future__ import annotations

import json
import hmac
import math
import os
import random
import re
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

try:
    from server.model_router.provider_operations import (
        provider_operation_model_matches,
    )
    from server.model_router.workload_control import (
        PROVIDER_WORKLOAD_CONTRACT_VERSION,
    )
except ModuleNotFoundError:
    from model_router.provider_operations import provider_operation_model_matches
    from model_router.workload_control import PROVIDER_WORKLOAD_CONTRACT_VERSION

from .strategy_tuning_qualification import (
    MIN_HARD_NEGATIVES,
    MIN_POSITIVE_CASES,
    build_tuning_readiness,
    normalize_benchmark_role,
)


class EvaluationError(RuntimeError):
    """Base error for knowledge evaluation operations."""


class EvaluationSetNotFoundError(EvaluationError):
    """Raised when an evaluation set does not exist."""


class EvaluationRunNotFoundError(EvaluationError):
    """Raised when an evaluation run does not exist."""


class EvaluationRevisionError(EvaluationError):
    """Raised when optimistic revision validation fails."""


class EvaluationStateError(EvaluationError):
    """Raised when an evaluation operation is invalid for the current state."""


class EvaluationPromotionError(EvaluationError):
    """Raised when a candidate does not satisfy the configured promotion gate."""


DEFAULT_KS = [1, 3, 5, 10]
FORMAL_POSITIVE_QUERY_TYPES = (
    "factual_lookup",
    "paraphrase",
    "section_context",
    "cross_language",
    "multi_evidence",
    "confusable_content",
)
DEFAULT_GATE_POLICY: dict[str, Any] = {
    "mode": "advisory",
    "min_recall_at_5": 0.8,
    "max_mrr_regression": 0.03,
    "max_citation_hit_regression": 0.02,
    "max_citation_precision_at_5_regression": 0.02,
    "max_no_result_increase": 0.05,
    "min_no_result_accuracy": 0.8,
    "min_citation_coverage": 0.0,
    "max_p95_latency_ratio": 2.0,
    "max_p95_latency_ms": 1500.0,
    "max_paired_primary_regression": 0.03,
    "paired_confidence_level": 0.95,
    "require_comparable_corpus": True,
    "require_zero_errors": True,
}
RAG_ROUTE_RECEIPT_CONTRACT_VERSION = "modelmirror-provider-rag-route-receipts-v1"


@lru_cache(maxsize=1)
def evaluation_runtime_code_fingerprint() -> str:
    """Hash the local sources that define a retrieval evaluation execution."""

    import hashlib

    rag_root = Path(__file__).parent
    model_router_root = rag_root.parent / "model_router"
    sources = (
        ("rag/embedder.py", rag_root / "embedder.py"),
        ("rag/evaluation.py", Path(__file__)),
        ("rag/evaluation_executor.py", rag_root / "evaluation_executor.py"),
        ("rag/lexical_store.py", rag_root / "lexical_store.py"),
        ("rag/rag_service.py", rag_root / "rag_service.py"),
        ("rag/reranker.py", rag_root / "reranker.py"),
        ("rag/retrieval.py", rag_root / "retrieval.py"),
        ("rag/vector_store.py", rag_root / "vector_store.py"),
        (
            "model_router/provider_operations.py",
            model_router_root / "provider_operations.py",
        ),
        ("model_router/egress.py", model_router_root / "egress.py"),
        ("model_router/provider_chat.py", model_router_root / "provider_chat.py"),
        ("model_router/provider_catalog.py", model_router_root / "provider_catalog.py"),
        (
            "model_router/rag_embedding_gateway.py",
            model_router_root / "rag_embedding_gateway.py",
        ),
        ("model_router/repository.py", model_router_root / "repository.py"),
        (
            "model_router/rerank_gateway.py",
            model_router_root / "rerank_gateway.py",
        ),
        ("model_router/schemas.py", model_router_root / "schemas.py"),
        ("model_router/service.py", model_router_root / "service.py"),
        ("model_router/chat_control.py", model_router_root / "chat_control.py"),
        (
            "model_router/multimodal_control.py",
            model_router_root / "multimodal_control.py",
        ),
        (
            "model_router/workload_control.py",
            model_router_root / "workload_control.py",
        ),
    )
    digest = hashlib.sha256()
    for source_id, path in sources:
        digest.update(source_id.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            return ""
    return digest.hexdigest()


def seal_execution_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe manifest with a checksum over every declared field."""

    sealed = _copy(manifest)
    sealed.pop("checksum", None)
    sealed["checksum"] = _checksum(sealed)
    return sealed


def _execution_manifest_checksum_valid(manifest: dict[str, Any]) -> bool:
    if not isinstance(manifest, dict):
        return False
    unsigned = {key: value for key, value in manifest.items() if key != "checksum"}
    expected = str(manifest.get("checksum") or "")
    return bool(unsigned) and bool(expected) and hmac.compare_digest(
        expected, _checksum(unsigned)
    )


def evaluate_retrieval_case(
    sources: list[dict[str, Any]],
    expected_refs: list[dict[str, Any]],
    *,
    ks: list[int] | None = None,
    latency_ms: float = 0.0,
    warnings: list[str] | None = None,
    expected_no_result: bool = False,
    retrieval_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one ranked retrieval response against stable relevance references."""

    normalized_ks = sorted(set(ks or DEFAULT_KS))
    if expected_no_result:
        no_result = len(sources) == 0
        return {
            "status": "completed",
            "metrics": {
                "no_result_accuracy": 1.0 if no_result else 0.0,
                "false_positive_rate": 0.0 if no_result else 1.0,
            },
            "latency_ms": round(max(0.0, latency_ms), 3),
            "source_count": len(sources),
            "expected_count": 0,
            "matched_expected_count": 0,
            "expected_no_result": True,
            "no_result": no_result,
            "warning_count": len(warnings or []),
            "warnings": [str(item)[:240] for item in (warnings or [])[:10]],
            "ranking": [_ranking_item(source, rank) for rank, source in enumerate(sources, 1)],
            "retrieval_receipt": _safe_retrieval_receipt(retrieval_receipt),
        }
    relevant_ranks: list[int] = []
    matched_ref_indexes: set[int] = set()
    ranking: list[dict[str, Any]] = []

    for rank, source in enumerate(sources, start=1):
        matches = [
            index
            for index, reference in enumerate(expected_refs)
            if index not in matched_ref_indexes and _source_matches_reference(source, reference)
        ]
        if matches:
            best_index = max(matches, key=lambda index: int(expected_refs[index].get("relevance", 1)))
            matched_ref_indexes.add(best_index)
            relevance = int(expected_refs[best_index].get("relevance", 1))
            relevant_ranks.append(rank)
            matched_ref_id = str(expected_refs[best_index].get("reference_id") or best_index)
        else:
            relevance = 0
            matched_ref_id = None
        ranking.append(
            {
                "rank": rank,
                "chunk_id": str(source.get("chunk_id") or ""),
                "document_id": str(
                    source.get("source_document_id")
                    or source.get("doc_id")
                    or source.get("document_id")
                    or ""
                ),
                "document_name": str(source.get("document_name") or "")[:240],
                "source_block_id": source.get("source_block_id"),
                "page_number": source.get("page_number"),
                "start_char": _optional_int(source.get("start_char")),
                "end_char": _optional_int(source.get("end_char")),
                "visual_kind": source.get("visual_kind"),
                "score": _float_or_none(source.get("score")),
                "vector_score": _float_or_none(source.get("vector_score")),
                "fulltext_score": _float_or_none(source.get("fulltext_score")),
                "fused_score": _float_or_none(source.get("fused_score")),
                "rerank_score": _float_or_none(source.get("rerank_score")),
                "relevance": relevance,
                "matched_reference_id": matched_ref_id,
            }
        )

    total_relevant = len(expected_refs)
    metrics: dict[str, float] = {}
    for k in normalized_ks:
        hits = sum(1 for rank in relevant_ranks if rank <= k)
        metrics[f"hit_at_{k}"] = 1.0 if hits else 0.0
        metrics[f"recall_at_{k}"] = hits / total_relevant if total_relevant else 0.0

    first_rank = min(relevant_ranks, default=0)
    max_k = max(normalized_ks, default=10)
    metrics[f"mrr_at_{max_k}"] = 1.0 / first_rank if first_rank and first_rank <= max_k else 0.0
    metrics[f"ndcg_at_{max_k}"] = _ndcg_at_k(ranking, expected_refs, max_k)
    relevant_source_count = sum(1 for item in ranking if int(item["relevance"]) > 0)
    metrics["citation_hit_rate"] = (
        relevant_source_count / len(ranking) if ranking else 0.0
    )
    metrics["citation_precision_at_5"] = (
        sum(1 for item in ranking[:5] if int(item["relevance"]) > 0) / 5.0
    )
    metrics["citation_coverage"] = (
        len(matched_ref_indexes) / total_relevant if total_relevant else 0.0
    )

    return {
        "status": "completed",
        "metrics": {key: round(value, 6) for key, value in metrics.items()},
        "latency_ms": round(max(0.0, latency_ms), 3),
        "source_count": len(sources),
        "expected_count": total_relevant,
        "matched_expected_count": len(matched_ref_indexes),
        "expected_no_result": False,
        "no_result": len(sources) == 0,
        "warning_count": len(warnings or []),
        "warnings": [str(item)[:240] for item in (warnings or [])[:10]],
        "ranking": ranking,
        "retrieval_receipt": _safe_retrieval_receipt(retrieval_receipt),
    }


def aggregate_target_metrics(
    case_results: list[dict[str, Any]],
    *,
    ks: list[int] | None = None,
) -> dict[str, Any]:
    """Aggregate deterministic retrieval metrics for one immutable target."""

    normalized_ks = sorted(set(ks or DEFAULT_KS))
    completed = [item for item in case_results if item.get("status") == "completed"]
    failed = [item for item in case_results if item.get("status") != "completed"]
    positive = [item for item in case_results if not item.get("expected_no_result")]
    completed_positive = [
        item for item in completed if not item.get("expected_no_result")
    ]
    failed_positive = [
        item for item in failed if not item.get("expected_no_result")
    ]
    no_result_cases = [item for item in case_results if item.get("expected_no_result")]
    completed_no_result = [
        item for item in completed if item.get("expected_no_result")
    ]
    failed_no_result = [
        item for item in failed if item.get("expected_no_result")
    ]
    metric_names = [
        *(f"hit_at_{k}" for k in normalized_ks),
        *(f"recall_at_{k}" for k in normalized_ks),
        f"mrr_at_{max(normalized_ks, default=10)}",
        f"ndcg_at_{max(normalized_ks, default=10)}",
        "citation_hit_rate",
        "citation_precision_at_5",
        "citation_coverage",
    ]
    metrics = {
        name: round(
            sum(
                float(item.get("metrics", {}).get(name, 0.0))
                for item in completed_positive
            )
            / len(positive),
            6,
        )
        if positive
        else 0.0
        for name in metric_names
    }
    latencies = sorted(float(item.get("latency_ms", 0.0)) for item in case_results)
    metrics.update(
        {
            "case_count": len(case_results),
            "expected_case_count": len(case_results),
            "completed_case_count": len(completed),
            "failed_case_count": len(failed),
            "error_count": len(failed),
            "positive_case_count": len(positive),
            "completed_positive_case_count": len(completed_positive),
            "failed_positive_case_count": len(failed_positive),
            "positive_quality_denominator": len(positive),
            "no_result_case_count": len(no_result_cases),
            "completed_no_result_case_count": len(completed_no_result),
            "failed_no_result_case_count": len(failed_no_result),
            "no_result_quality_denominator": len(no_result_cases),
            "no_result_accuracy": round(
                sum(
                    float(item.get("metrics", {}).get("no_result_accuracy", 0.0))
                    for item in completed_no_result
                )
                / len(no_result_cases),
                6,
            ) if no_result_cases else 1.0,
            "false_positive_rate": round(
                (
                    sum(
                        float(item.get("metrics", {}).get("false_positive_rate", 0.0))
                        for item in completed_no_result
                    )
                    + len(failed_no_result)
                )
                / len(no_result_cases),
                6,
            ) if no_result_cases else 0.0,
            "no_result_rate": round(
                sum(1 for item in case_results if item.get("no_result"))
                / len(case_results),
                6,
            )
            if case_results
            else 1.0,
            "positive_no_result_rate": round(
                sum(1 for item in positive if item.get("no_result"))
                / len(positive),
                6,
            )
            if positive
            else 0.0,
            "warning_rate": round(
                sum(
                    1
                    for item in case_results
                    if int(item.get("warning_count", 0)) > 0
                )
                / len(case_results),
                6,
            )
            if case_results
            else 0.0,
            "average_latency_ms": round(sum(latencies) / len(latencies), 3)
            if latencies
            else 0.0,
            "p95_latency_ms": round(_percentile(latencies, 0.95), 3),
        }
    )
    return metrics


def paired_primary_confidence_report(
    baseline_results: list[dict[str, Any]],
    candidate_results: list[dict[str, Any]],
    *,
    cases: list[dict[str, Any]],
    seed: str,
    bootstrap_samples: int = 10_000,
    confidence_level: float = 0.95,
    max_regression: float = 0.03,
) -> dict[str, Any]:
    baseline_by_id = {
        str(item.get("case_id") or ""): item for item in baseline_results
    }
    candidate_by_id = {
        str(item.get("case_id") or ""): item for item in candidate_results
    }
    expected_ids = [str(case.get("case_id") or "") for case in cases]
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
            "contract_version": "rag-paired-confidence-v1",
            "status": "insufficient",
            "passed": False,
            "missing_case_ids": missing,
            "failed_case_ids": failed,
            "reason": "paired_results_incomplete",
        }

    cases_by_id = {str(case.get("case_id") or ""): case for case in cases}

    def primary_score(result: dict[str, Any], case: dict[str, Any]) -> float:
        metrics = result.get("metrics") or {}
        return float(
            metrics.get("no_result_accuracy", 0.0)
            if case.get("expected_no_result")
            else metrics.get("recall_at_5", 0.0)
        )

    deltas = {
        case_id: primary_score(candidate_by_id[case_id], cases_by_id[case_id])
        - primary_score(baseline_by_id[case_id], cases_by_id[case_id])
        for case_id in expected_ids
    }
    strata: dict[str, list[str]] = {}
    for case_id in expected_ids:
        case = cases_by_id[case_id]
        case_type = "negative" if case.get("expected_no_result") else "positive"
        locale = str((case.get("targeting") or {}).get("locale") or "unknown")
        strata.setdefault(f"{case_type}:{locale}", []).append(case_id)
    seed_value = int(_checksum({"seed": seed})[:16], 16)
    rng = random.Random(seed_value)
    samples: list[float] = []
    for _ in range(max(1, int(bootstrap_samples))):
        selected: list[str] = []
        for key in sorted(strata):
            values = strata[key]
            selected.extend(rng.choice(values) for _ in range(len(values)))
        samples.append(sum(deltas[case_id] for case_id in selected) / len(selected))
    samples.sort()
    tail = (1.0 - float(confidence_level)) / 2.0
    lower = _percentile(samples, tail)
    upper = _percentile(samples, 1.0 - tail)
    point_delta = sum(deltas.values()) / len(deltas)
    return {
        "contract_version": "rag-paired-confidence-v1",
        "status": "completed",
        "passed": lower >= -float(max_regression),
        "primary_metric": "recall_at_5_or_no_result_accuracy",
        "point_delta": round(point_delta, 6),
        "confidence_level": float(confidence_level),
        "confidence_interval": {
            "lower": round(lower, 6),
            "upper": round(upper, 6),
        },
        "bootstrap_samples": len(samples),
        "max_regression": float(max_regression),
        "case_count": len(expected_ids),
        "strata": {key: len(value) for key, value in sorted(strata.items())},
    }


def evaluate_promotion_gate(
    candidate: dict[str, Any],
    *,
    baseline: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
    evidence_qualification: dict[str, Any] | None = None,
    paired_confidence: dict[str, Any] | None = None,
    comparability: dict[str, Any] | None = None,
    execution_integrity: dict[str, Any] | None = None,
    run_mode: str = "diagnostic",
) -> dict[str, Any]:
    """Evaluate absolute and regression thresholds for one candidate target."""

    effective = _compatible_gate_policy(policy)
    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, actual: float, threshold: float, message: str) -> None:
        checks.append(
            {
                "id": check_id,
                "passed": bool(passed),
                "actual": round(float(actual), 6),
                "threshold": round(float(threshold), 6),
                "message": message,
            }
        )

    recall = float(candidate.get("recall_at_5", 0.0))
    min_recall = float(effective["min_recall_at_5"])
    add_check(
        "min_recall_at_5",
        recall >= min_recall,
        recall,
        min_recall,
        "Recall@5 must meet the configured minimum.",
    )
    no_result_accuracy = float(candidate.get("no_result_accuracy", 1.0))
    minimum_no_result_accuracy = float(effective["min_no_result_accuracy"])
    add_check(
        "min_no_result_accuracy",
        no_result_accuracy >= minimum_no_result_accuracy,
        no_result_accuracy,
        minimum_no_result_accuracy,
        "No-result accuracy must meet the configured minimum.",
    )
    citation_coverage = float(candidate.get("citation_coverage", 0.0))
    minimum_citation_coverage = float(effective["min_citation_coverage"])
    add_check(
        "min_citation_coverage",
        citation_coverage >= minimum_citation_coverage,
        citation_coverage,
        minimum_citation_coverage,
        "Citation coverage must meet the configured minimum.",
    )
    errors = float(candidate.get("error_count", 0))
    if bool(effective.get("require_zero_errors", True)):
        add_check(
            "zero_errors",
            errors == 0,
            errors,
            0.0,
            "Evaluation must complete without case errors.",
        )

    if baseline is not None:
        mrr_regression = float(baseline.get("mrr_at_10", 0.0)) - float(
            candidate.get("mrr_at_10", 0.0)
        )
        add_check(
            "max_mrr_regression",
            mrr_regression <= float(effective["max_mrr_regression"]),
            mrr_regression,
            float(effective["max_mrr_regression"]),
            "MRR@10 regression must stay within the configured tolerance.",
        )
        has_precision = (
            "citation_precision_at_5" in baseline
            and "citation_precision_at_5" in candidate
        )
        citation_metric = (
            "citation_precision_at_5" if has_precision else "citation_hit_rate"
        )
        citation_regression = float(baseline.get(citation_metric, 0.0)) - float(
            candidate.get(citation_metric, 0.0)
        )
        add_check(
            "max_citation_precision_at_5_regression",
            citation_regression
            <= float(effective["max_citation_precision_at_5_regression"]),
            citation_regression,
            float(effective["max_citation_precision_at_5_regression"]),
            "Citation Precision@5 regression must stay within tolerance.",
        )
        checks[-1]["metric_source"] = citation_metric
        no_result_increase = float(
            candidate.get("positive_no_result_rate", candidate.get("no_result_rate", 0.0))
        ) - float(
            baseline.get("positive_no_result_rate", baseline.get("no_result_rate", 0.0))
        )
        add_check(
            "max_no_result_increase",
            no_result_increase <= float(effective["max_no_result_increase"]),
            no_result_increase,
            float(effective["max_no_result_increase"]),
            "Positive-case no-result rate increase must stay within tolerance.",
        )

    baseline_p95 = float((baseline or {}).get("p95_latency_ms", 0.0))
    candidate_p95 = float(candidate.get("p95_latency_ms", 0.0))
    has_relative_baseline = baseline is not None and baseline_p95 > 0
    latency_ratio = (
        candidate_p95 / baseline_p95
        if has_relative_baseline
        else (0.0 if candidate_p95 <= 0 else 1_000_000_000.0)
    )
    relative_passed = has_relative_baseline and latency_ratio <= float(
        effective["max_p95_latency_ratio"]
    )
    absolute_passed = candidate_p95 <= float(effective["max_p95_latency_ms"])
    add_check(
        "max_p95_latency_ratio",
        relative_passed or absolute_passed,
        latency_ratio,
        float(effective["max_p95_latency_ratio"]),
        "P95 latency must meet either the relative or absolute configured limit.",
    )
    checks[-1].update(
        {
            "actual_ms": round(candidate_p95, 3),
            "absolute_threshold_ms": round(
                float(effective["max_p95_latency_ms"]), 3
            ),
            "relative_baseline_available": has_relative_baseline,
            "pass_mode": (
                "relative"
                if relative_passed
                else "absolute"
                if absolute_passed
                else "none"
            ),
        }
    )

    if evidence_qualification is not None:
        qualified = bool(evidence_qualification.get("qualified"))
        checks.append(
            {
                "id": "qualified_promotion_evidence",
                "passed": qualified,
                "actual": 1.0 if qualified else 0.0,
                "threshold": 1.0,
                "status": str(
                    evidence_qualification.get("status") or "diagnostic_only"
                ),
                "counts": _copy(evidence_qualification.get("counts") or {}),
                "message": (
                    "Promotion evidence must include 30 stable-Gold positives and "
                    "12 approved corpus-near hard negatives."
                ),
            }
        )

    if run_mode == "formal":
        comparable = bool((comparability or {}).get("comparable")) and bool(
            (comparability or {}).get("same_corpus")
        )
        checks.append(
            {
                "id": "comparable_corpus",
                "passed": comparable,
                "actual": comparable,
                "required": bool(effective["require_comparable_corpus"]),
                "message": "Formal targets must share the immutable Gold corpus.",
            }
        )
        paired = paired_confidence or {}
        lower = float(
            (paired.get("confidence_interval") or {}).get("lower", -1.0)
        )
        paired_passed = (
            paired.get("status") == "completed"
            and lower >= -float(effective["max_paired_primary_regression"])
        )
        checks.append(
            {
                "id": "paired_non_inferiority",
                "passed": paired_passed,
                "actual": round(lower, 6),
                "threshold": -float(effective["max_paired_primary_regression"]),
                "confidence_level": float(effective["paired_confidence_level"]),
                "bootstrap_samples": int(paired.get("bootstrap_samples") or 0),
                "message": "Paired primary-score CI must satisfy non-inferiority.",
            }
        )
        if execution_integrity is not None:
            integrity = execution_integrity
            checks.append(
                {
                    "id": "formal_execution_integrity",
                    "passed": bool(integrity.get("qualified")),
                    "actual": bool(integrity.get("qualified")),
                    "required": True,
                    "status": str(integrity.get("status") or "missing"),
                    "reason_codes": [
                        str(item)
                        for item in integrity.get("reason_codes", [])
                        if str(item)
                    ],
                    "message": (
                        "Formal execution must have complete, consistent Provider and "
                        "retrieval receipts."
                    ),
                }
            )

    return {
        "passed": all(item["passed"] for item in checks),
        "mode": str(effective["mode"]),
        "checks": checks,
    }


def qualify_promotion_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Classify a fixed evaluation snapshot as promotion evidence or smoke-only."""

    readiness = build_tuning_readiness(snapshot)
    counts = dict(readiness.get("counts") or {})
    benchmark_role = str(readiness.get("benchmark_role") or "unclassified")
    immutable_snapshot = bool(snapshot.get("version_id") and snapshot.get("published_at"))
    positive_count = int(counts.get("positive") or 0)
    stable_positive_count = int(counts.get("stable_source_block_positive") or 0)
    reviewed_hard_negative_count = int(counts.get("reviewed_hard_negative") or 0)
    checks = [
        {
            "id": "immutable_evaluation_version",
            "passed": immutable_snapshot,
            "actual": immutable_snapshot,
            "required": True,
        },
        {
            "id": "selection_eligible",
            "passed": bool(readiness.get("selection_eligible")),
            "actual": bool(readiness.get("selection_eligible")),
            "required": True,
        },
        {
            "id": "minimum_positive_cases",
            "passed": positive_count >= MIN_POSITIVE_CASES,
            "actual": positive_count,
            "required": MIN_POSITIVE_CASES,
        },
        {
            "id": "stable_source_block_gold",
            "passed": positive_count > 0 and stable_positive_count == positive_count,
            "actual": stable_positive_count,
            "required": positive_count,
        },
        {
            "id": "reviewed_hard_negatives",
            "passed": reviewed_hard_negative_count >= MIN_HARD_NEGATIVES,
            "actual": reviewed_hard_negative_count,
            "required": MIN_HARD_NEGATIVES,
        },
    ]
    qualified = all(bool(item["passed"]) for item in checks)
    return {
        "version": "rag-promotion-evidence-v1",
        "status": "qualified" if qualified else "diagnostic_only",
        "qualified": qualified,
        "benchmark_role": benchmark_role,
        "immutable_snapshot": immutable_snapshot,
        "selection_eligible": bool(readiness.get("selection_eligible")),
        "positive_case_count": positive_count,
        "stable_source_block_positive_count": stable_positive_count,
        "reviewed_hard_negative_count": reviewed_hard_negative_count,
        "counts": {
            "total": int(counts.get("total") or 0),
            "positive": positive_count,
            "stable_source_block_positive": stable_positive_count,
            "reviewed_hard_negative": reviewed_hard_negative_count,
        },
        "checks": checks,
    }


def qualify_locked_dataset_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate the role-neutral immutable rag-gold-v3 evidence contract."""

    cases = [item for item in snapshot.get("cases") or [] if isinstance(item, dict)]
    corpus = snapshot.get("corpus_snapshot")
    corpus = corpus if isinstance(corpus, dict) else {}
    corpus_payload = {key: value for key, value in corpus.items() if key != "checksum"}
    corpus_checksum_valid = bool(corpus_payload) and hmac.compare_digest(
        str(corpus.get("checksum") or ""), _checksum(corpus_payload)
    )
    block_hashes: dict[tuple[str, str], str] = {}
    corpus_documents: set[str] = set()
    corpus_contains_text = False
    for document in corpus.get("documents") or []:
        if not isinstance(document, dict):
            continue
        document_id = str(document.get("document_id") or "")
        if not document_id:
            continue
        corpus_documents.add(document_id)
        for block in document.get("source_blocks") or []:
            if not isinstance(block, dict):
                continue
            source_block_id = str(block.get("source_block_id") or "")
            if source_block_id:
                corpus_contains_text = corpus_contains_text or "text" in block
                block_hashes[(document_id, source_block_id)] = str(
                    block.get("block_hash") or ""
                )
    anchors_valid = True
    negatives_valid = True
    for case in cases:
        if case.get("expected_no_result"):
            targeting = case.get("targeting")
            targeting = targeting if isinstance(targeting, dict) else {}
            contexts = [
                item
                for item in targeting.get("context_refs") or []
                if isinstance(item, dict)
            ]
            verification = targeting.get("full_corpus_verification")
            context = contexts[0] if len(contexts) == 1 else {}
            context_key = (
                str(context.get("document_id") or ""),
                str(context.get("source_block_id") or ""),
            )
            top_matches = (
                verification.get("top_matches")
                if isinstance(verification, dict)
                else None
            )
            if not (
                str(case.get("review_status") or "") == "approved"
                and len(contexts) == 1
                and block_hashes.get(context_key)
                == str(context.get("source_block_hash") or "")
                and isinstance(verification, dict)
                and verification.get("contract_version")
                == "rag-no-result-verification-v1"
                and verification.get("completed") is True
                and hmac.compare_digest(
                    str(verification.get("query_hash") or ""),
                    _checksum(_benchmark_normalized_query(case.get("query"))),
                )
                and hmac.compare_digest(
                    str(verification.get("corpus_snapshot_checksum") or ""),
                    str(corpus.get("checksum") or ""),
                )
                and int(verification.get("scanned_document_count") or 0)
                == len(corpus_documents)
                and int(verification.get("scanned_source_block_count") or 0)
                == len(block_hashes)
                and isinstance(top_matches, list)
                and all(
                    isinstance(match, dict)
                    and block_hashes.get(
                        (
                            str(match.get("document_id") or ""),
                            str(match.get("source_block_id") or ""),
                        )
                    )
                    == str(match.get("source_block_hash") or "")
                    and "text" not in match
                    for match in top_matches
                )
            ):
                negatives_valid = False
            continue
        references = [
            item for item in case.get("expected_refs") or [] if isinstance(item, dict)
        ]
        if not references:
            anchors_valid = False
        for reference in references:
            key = (
                str(reference.get("document_id") or ""),
                str(reference.get("source_block_id") or ""),
            )
            anchor_start = _optional_int(reference.get("anchor_start"))
            anchor_end = _optional_int(reference.get("anchor_end"))
            block_hash = str(reference.get("source_block_hash") or "")
            expected_hash = _checksum(
                {
                    "contract_version": "rag-anchor-v1",
                    "document_id": key[0],
                    "source_block_id": key[1],
                    "block_hash": block_hash,
                    "anchor_start": anchor_start,
                    "anchor_end": anchor_end,
                }
            )
            if not (
                str(reference.get("match_mode") or "") == "source_block"
                and block_hashes.get(key) == block_hash
                and anchor_start is not None
                and anchor_start >= 0
                and anchor_end is not None
                and anchor_end > anchor_start
                and hmac.compare_digest(
                    str(reference.get("anchor_hash") or ""), expected_hash
                )
            ):
                anchors_valid = False
    role = str(snapshot.get("benchmark_role") or "")
    manifest = snapshot.get("qualification_manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    checks = [
        {
            "id": "gold_contract_v3",
            "passed": snapshot.get("benchmark_contract_version") == "rag-gold-v3",
        },
        {
            "id": "locked_dataset_role",
            "passed": role
            in {
                "strategy_tuning",
                "threshold_calibration",
                "held_out_qualification",
            },
        },
        {
            "id": "immutable_evaluation_version",
            "passed": bool(snapshot.get("version_id") and snapshot.get("published_at")),
        },
        {
            "id": "published_checksum",
            "passed": hmac.compare_digest(
                str(snapshot.get("checksum") or ""), _published_gold_checksum(snapshot)
            ),
        },
        {
            "id": "corpus_snapshot",
            "passed": corpus.get("contract_version") == "rag-corpus-snapshot-v1"
            and corpus_checksum_valid
            and bool(corpus_documents)
            and not corpus_contains_text,
        },
        {"id": "anchored_source_block_gold", "passed": anchors_valid},
        {"id": "verified_hard_negative_contexts", "passed": negatives_valid},
        {
            "id": "qualification_manifest",
            "passed": manifest.get("contract_version")
            == "rag-gold-qualification-v3"
            and manifest.get("dataset_role") == role
            and hmac.compare_digest(
                str(manifest.get("corpus_checksum") or ""),
                str(corpus.get("checksum") or ""),
            )
            and hmac.compare_digest(
                str(manifest.get("anchor_checksum") or ""),
                _qualification_anchor_checksum(cases),
            )
            and manifest.get("tuner_usage_lineage") == [],
        },
    ]
    return {
        "version": "rag-locked-dataset-evidence-v1",
        "qualified": all(bool(check["passed"]) for check in checks),
        "status": (
            "qualified"
            if all(bool(check["passed"]) for check in checks)
            else "diagnostic_only"
        ),
        "checks": checks,
    }


def qualify_formal_evidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate immutable held-out rag-gold-v3 evidence without trusting labels."""

    cases = [item for item in snapshot.get("cases", []) if isinstance(item, dict)]
    positives = [item for item in cases if not bool(item.get("expected_no_result"))]
    negatives = [item for item in cases if bool(item.get("expected_no_result"))]
    corpus = snapshot.get("corpus_snapshot")
    corpus = corpus if isinstance(corpus, dict) else {}
    corpus_without_checksum = {
        key: value for key, value in corpus.items() if key != "checksum"
    }
    corpus_checksum_valid = bool(corpus_without_checksum) and hmac.compare_digest(
        str(corpus.get("checksum") or ""), _checksum(corpus_without_checksum)
    )
    provenance = snapshot.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    attempts = provenance.get("generation_attempts")
    attempts = attempts if isinstance(attempts, list) else []
    provenance_hashes = (
        str(provenance.get("target_checksum") or ""),
        str(provenance.get("source_summary_hash") or ""),
        str(provenance.get("evidence_hash") or ""),
        str(provenance.get("blueprint_hash") or ""),
        str(provenance.get("prompt_contract_hash") or ""),
    )
    generation_provenance_valid = (
        provenance.get("generator") == "modelmirror-targeted-rag-benchmark-v3"
        and bool(str(provenance.get("generation_job_id") or ""))
        and bool(str(provenance.get("generator_model_id") or ""))
        and isinstance(provenance.get("seed"), int)
        and not isinstance(provenance.get("seed"), bool)
        and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in provenance_hashes)
        and provenance.get("repair_used") is False
        and len(attempts) == 1
        and isinstance(attempts[0], dict)
        and attempts[0].get("attempt") == "initial"
        and not attempts[0].get("error_code")
    )
    block_refs: set[tuple[str, str]] = set()
    block_hashes: dict[tuple[str, str], str] = {}
    corpus_documents: set[str] = set()
    corpus_contains_text = False
    for document in corpus.get("documents", []):
        if not isinstance(document, dict):
            continue
        document_id = str(document.get("document_id") or "")
        if not document_id:
            continue
        corpus_documents.add(document_id)
        for block in document.get("source_blocks", []):
            if isinstance(block, dict) and str(block.get("source_block_id") or ""):
                corpus_contains_text = corpus_contains_text or "text" in block
                key = (document_id, str(block["source_block_id"]))
                block_refs.add(key)
                block_hashes[key] = str(block.get("block_hash") or "")

    def trusted_review(case: dict[str, Any]) -> bool:
        evidence = case.get("review_evidence")
        if not isinstance(evidence, dict):
            return False
        reviewer = evidence.get("reviewer")
        return (
            str(case.get("review_status") or "") == "approved"
            and str(evidence.get("decision") or "") == "approved"
            and str(evidence.get("source") or "") == "authenticated_ui"
            and isinstance(reviewer, dict)
            and bool(str(reviewer.get("tenant_id") or ""))
            and bool(str(reviewer.get("role") or ""))
            and isinstance(evidence.get("reviewed_at"), (int, float))
            and int(evidence.get("dataset_revision") or 0) >= 1
            and (
                not (case.get("targeting") or {}).get("leakage_warning")
                or bool(str(evidence.get("reason") or "").strip())
            )
            and hmac.compare_digest(
                str(evidence.get("case_checksum") or ""),
                _case_review_checksum(case),
            )
        )

    stable_positive_refs: list[tuple[str, str]] = []
    positive_refs_valid = True
    for case in positives:
        references = case.get("expected_refs") or []
        if not references:
            positive_refs_valid = False
            continue
        for reference in references:
            if not isinstance(reference, dict):
                positive_refs_valid = False
                continue
            key = (
                str(reference.get("document_id") or ""),
                str(reference.get("source_block_id") or ""),
            )
            stable_positive_refs.append(key)
            anchor_start = _optional_int(reference.get("anchor_start"))
            anchor_end = _optional_int(reference.get("anchor_end"))
            block_hash = str(reference.get("source_block_hash") or "")
            expected_anchor_hash = _checksum(
                {
                    "contract_version": "rag-anchor-v1",
                    "document_id": key[0],
                    "source_block_id": key[1],
                    "block_hash": block_hash,
                    "anchor_start": anchor_start,
                    "anchor_end": anchor_end,
                }
            )
            if (
                str(reference.get("match_mode") or "") != "source_block"
                or not all(key)
                or key not in block_refs
                or block_hashes.get(key) != block_hash
                or anchor_start is None
                or anchor_start < 0
                or anchor_end is None
                or anchor_end <= anchor_start
                or not hmac.compare_digest(
                    str(reference.get("anchor_hash") or ""), expected_anchor_hash
                )
            ):
                positive_refs_valid = False

    negative_contexts: list[tuple[str, str]] = []
    hard_negatives_valid = True
    for case in negatives:
        tags = {str(tag) for tag in case.get("tags", [])}
        contexts = (case.get("targeting") or {}).get("context_refs") or []
        if "hard_negative" not in tags or "corpus_near" not in tags or len(contexts) != 1:
            hard_negatives_valid = False
            continue
        context = contexts[0] if isinstance(contexts[0], dict) else {}
        key = (
            str(context.get("document_id") or ""),
            str(context.get("source_block_id") or ""),
        )
        negative_contexts.append(key)
        verification = (case.get("targeting") or {}).get(
            "full_corpus_verification"
        )
        context_block_hash = str(context.get("source_block_hash") or "")
        top_matches = (
            verification.get("top_matches")
            if isinstance(verification, dict)
            else None
        )
        verification_valid = (
            isinstance(verification, dict)
            and verification.get("contract_version")
            == "rag-no-result-verification-v1"
            and verification.get("completed") is True
            and verification.get("method") == "full_corpus_lexical_scan_v1"
            and hmac.compare_digest(
                str(verification.get("query_hash") or ""),
                _checksum(_benchmark_normalized_query(case.get("query"))),
            )
            and hmac.compare_digest(
                str(verification.get("corpus_snapshot_checksum") or ""),
                str(corpus.get("checksum") or ""),
            )
            and int(verification.get("scanned_document_count") or 0)
            == len(corpus_documents)
            and int(verification.get("scanned_source_block_count") or 0)
            == len(block_refs)
            and isinstance(top_matches, list)
            and all(
                isinstance(match, dict)
                and (
                    str(match.get("document_id") or ""),
                    str(match.get("source_block_id") or ""),
                )
                in block_refs
                and block_hashes.get(
                    (
                        str(match.get("document_id") or ""),
                        str(match.get("source_block_id") or ""),
                    )
                )
                == str(match.get("source_block_hash") or "")
                and "text" not in match
                for match in top_matches
            )
        )
        if (
            not all(key)
            or key not in block_refs
            or block_hashes.get(key) != context_block_hash
            or not verification_valid
        ):
            hard_negatives_valid = False

    def locale_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        result = {"zh": 0, "en": 0}
        for case in items:
            raw_locale = str(
                (case.get("targeting") or {}).get("locale") or ""
            ).casefold().replace("_", "-")
            locale = (
                "zh"
                if raw_locale == "zh" or raw_locale.startswith("zh-")
                else "en"
                if raw_locale == "en" or raw_locale.startswith("en-")
                else raw_locale
            )
            if locale in result:
                result[locale] += 1
        return result

    positive_locales = locale_counts(positives)
    negative_locales = locale_counts(negatives)
    positive_query_types = {
        query_type: sum(
            1
            for case in positives
            if str((case.get("targeting") or {}).get("query_type") or "")
            == query_type
        )
        for query_type in FORMAL_POSITIVE_QUERY_TYPES
    }
    normalized_queries = [
        " ".join(str(case.get("query") or "").casefold().split()) for case in cases
    ]
    query_tokens = [_formal_query_tokens(query) for query in normalized_queries]
    has_near_duplicate = any(
        bool(query_tokens[left] or query_tokens[right])
        and len(query_tokens[left] & query_tokens[right])
        / max(1, len(query_tokens[left] | query_tokens[right]))
        >= 0.8
        for left in range(len(query_tokens))
        for right in range(left + 1, len(query_tokens))
    )
    positive_documents = {
        document_id for document_id, _ in stable_positive_refs if document_id
    }
    per_document: dict[str, int] = {}
    for document_id, _ in stable_positive_refs:
        per_document[document_id] = per_document.get(document_id, 0) + 1
    max_document_share = (
        max(per_document.values(), default=0) / len(positives) if positives else 1.0
    )
    source_block_counts: dict[tuple[str, str], int] = {}
    for key in stable_positive_refs:
        source_block_counts[key] = source_block_counts.get(key, 0) + 1
    qualification_manifest = snapshot.get("qualification_manifest")
    qualification_manifest = (
        qualification_manifest if isinstance(qualification_manifest, dict) else {}
    )
    manifest_valid = (
        qualification_manifest.get("contract_version")
        == "rag-gold-qualification-v3"
        and qualification_manifest.get("dataset_role")
        == "held_out_qualification"
        and hmac.compare_digest(
            str(qualification_manifest.get("corpus_checksum") or ""),
            str(corpus.get("checksum") or ""),
        )
        and hmac.compare_digest(
            str(qualification_manifest.get("anchor_checksum") or ""),
            _qualification_anchor_checksum(cases),
        )
        and qualification_manifest.get("tuner_usage_lineage") == []
    )

    checks = [
        {
            "id": "gold_contract_v3",
            "passed": snapshot.get("benchmark_contract_version") == "rag-gold-v3",
        },
        {
            "id": "held_out_qualification_role",
            "passed": snapshot.get("benchmark_role") == "held_out_qualification",
        },
        {"id": "qualification_manifest", "passed": manifest_valid},
        {
            "id": "generation_provenance",
            "passed": generation_provenance_valid,
        },
        {
            "id": "immutable_evaluation_version",
            "passed": bool(snapshot.get("version_id") and snapshot.get("published_at")),
        },
        {
            "id": "published_checksum",
            "passed": hmac.compare_digest(
                str(snapshot.get("checksum") or ""), _published_gold_checksum(snapshot)
            ),
        },
        {
            "id": "corpus_snapshot",
            "passed": corpus.get("contract_version") == "rag-corpus-snapshot-v1"
            and corpus_checksum_valid
            and bool(corpus_documents)
            and not corpus_contains_text,
        },
        {"id": "exact_case_counts", "passed": len(positives) == 30 and len(negatives) == 12},
        {
            "id": "trusted_case_reviews",
            "passed": len(cases) == 42 and all(trusted_review(case) for case in cases),
        },
        {
            "id": "anchored_source_block_gold",
            "passed": positive_refs_valid and len(stable_positive_refs) >= len(positives),
        },
        {
            "id": "verified_hard_negative_contexts",
            "passed": hard_negatives_valid
            and len(negative_contexts) == 12
            and len(set(negative_contexts)) == 12,
        },
        {
            "id": "locale_balance",
            "passed": positive_locales == {"zh": 15, "en": 15}
            and negative_locales == {"zh": 6, "en": 6},
        },
        {
            "id": "positive_query_type_balance",
            "passed": positive_query_types
            == {query_type: 5 for query_type in FORMAL_POSITIVE_QUERY_TYPES},
        },
        {
            "id": "document_coverage",
            "passed": positive_documents == corpus_documents and max_document_share <= 0.4,
        },
        {
            "id": "source_block_reuse",
            "passed": max(source_block_counts.values(), default=0) <= 2,
        },
        {
            "id": "unique_queries",
            "passed": len(normalized_queries) == 42
            and len(set(normalized_queries)) == len(normalized_queries)
            and not has_near_duplicate,
        },
    ]
    qualified = all(bool(check["passed"]) for check in checks)
    return {
        "version": "rag-formal-evidence-v3",
        "status": "qualified" if qualified else "diagnostic_only",
        "qualified": qualified,
        "counts": {
            "total": len(cases),
            "positive": len(positives),
            "hard_negative": len(negatives),
            "trusted_reviews": sum(1 for case in cases if trusted_review(case)),
            "positive_query_types": positive_query_types,
        },
        "checks": checks,
    }


def validate_formal_run_admission(
    evaluation_version: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    baseline_version_id: str | None,
) -> dict[str, Any]:
    qualification = qualify_formal_evidence(evaluation_version)
    if not qualification["qualified"]:
        raise ValueError("Formal evaluation requires qualified held-out rag-gold-v3 evidence.")
    if len(targets) != 2 or len({str(item.get("version_id") or "") for item in targets}) != 2:
        raise ValueError("Formal evaluation requires exactly one baseline and one candidate.")
    version_ids = {str(item.get("version_id") or "") for item in targets}
    if not baseline_version_id or baseline_version_id not in version_ids:
        raise ValueError("Formal evaluation requires one explicit baseline target.")

    expected_corpus = str(
        (evaluation_version.get("corpus_snapshot") or {}).get("checksum") or ""
    )
    expected_kb_id = str(evaluation_version.get("kb_id") or "")
    corpus_hashes = {str(item.get("corpus_snapshot_hash") or "") for item in targets}
    if not expected_corpus or corpus_hashes != {expected_corpus}:
        raise ValueError(
            "Formal baseline and candidate must use the same immutable corpus as Gold."
        )
    evaluator_code_fingerprint = evaluation_runtime_code_fingerprint()
    if re.fullmatch(r"[0-9a-f]{64}", evaluator_code_fingerprint) is None:
        raise ValueError("Formal evaluation code identity is unavailable.")

    manifest_targets: list[dict[str, Any]] = []
    for target in targets:
        if target.get("retrieval"):
            raise ValueError(
                "Formal evaluation does not allow per-run retrieval overrides."
            )
        evidence = target.get("version_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        effective = (evidence.get("embedding") or {}).get("effective") or {}
        processor = evidence.get("processor")
        processor = processor if isinstance(processor, dict) else {}
        retrieval = evidence.get("retrieval")
        retrieval = retrieval if isinstance(retrieval, dict) else {}
        index_contract = evidence.get("index_contract")
        index_contract = index_contract if isinstance(index_contract, dict) else {}
        vector_contract = index_contract.get("vector")
        vector_contract = (
            vector_contract if isinstance(vector_contract, dict) else {}
        )
        backend = evidence.get("vector_backend_readiness")
        backend = backend if isinstance(backend, dict) else {}
        runtime_backend = evidence.get("runtime_vector_backend_readiness")
        runtime_backend = (
            runtime_backend if isinstance(runtime_backend, dict) else {}
        )
        mode = str(retrieval.get("mode") or "")
        required_hashes = (
            str(evidence.get("version_fingerprint") or ""),
            str(evidence.get("configuration_fingerprint") or ""),
            str(processor.get("fingerprint") or ""),
            str(evidence.get("source_manifest_fingerprint") or ""),
        )
        if (
            evidence.get("schema_version") != "rag-version-evidence-v1"
            or not expected_kb_id
            or str(evidence.get("kb_id") or "") != expected_kb_id
            or str(evidence.get("version_id") or "")
            != str(target.get("version_id") or "")
            or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in required_hashes)
            or not str(processor.get("mode") or "")
            or mode not in {"vector", "fulltext", "hybrid"}
            or int(retrieval.get("top_k") or 0) <= 0
            or index_contract.get("contract_version") != "rag-index-contract-v3"
            or int(index_contract.get("index_schema_version") or 0) != 3
            or str(index_contract.get("retrieval_mode") or "") != mode
        ):
            raise ValueError("Formal evaluation target identity is incomplete.")
        vector_required = mode in {"vector", "hybrid"}
        if bool(vector_contract.get("required")) != vector_required:
            raise ValueError("Formal evaluation target index identity is inconsistent.")
        if vector_required:
            embedding_fingerprint = str(
                effective.get("embedding_space_fingerprint") or ""
            )
            if (
                str(effective.get("provider") or "") in {"", "none", "hash"}
                or not str(effective.get("model") or "")
                or int(effective.get("dimension") or 0) <= 0
                or effective.get("ready") is not True
                or bool(effective.get("degraded"))
                or str(effective.get("access_mode") or "") != "managed"
                or re.fullmatch(r"[0-9a-f]{64}", embedding_fingerprint) is None
            ):
                raise ValueError(
                    "Formal vector evaluation requires a ready production embedding identity."
                )
            if (
                str(vector_contract.get("embedding_space_fingerprint") or "")
                != embedding_fingerprint
                or int(vector_contract.get("dimension") or 0)
                != int(effective.get("dimension") or 0)
                or str(vector_contract.get("distance_contract") or "")
                != "cosine_v1"
                or backend.get("ready") is not True
                or str(backend.get("effective_backend") or "")
                in {"", "unavailable"}
                or str(backend.get("distance_contract") or "") != "cosine_v1"
                or runtime_backend.get("ready") is not True
                or str(runtime_backend.get("effective_backend") or "")
                != str(backend.get("effective_backend") or "")
                or str(runtime_backend.get("distance_contract") or "")
                != "cosine_v1"
            ):
                raise ValueError(
                    "Formal evaluation target index identity is inconsistent."
                )
        elif (
            str(effective.get("provider") or "") != "none"
            or int(effective.get("dimension") or 0) != 0
            or str(effective.get("access_mode") or "") != "not_applicable"
            or str(effective.get("status") or "") != "not_applicable"
            or str(vector_contract.get("distance_contract") or "")
            != "not_applicable"
        ):
            raise ValueError(
                "Formal fulltext evaluation requires embedding to be not_applicable."
            )
        manifest_backend = (
            _copy(backend) if vector_required else {"status": "not_applicable"}
        )
        manifest_runtime_backend = (
            _copy(runtime_backend)
            if vector_required
            else {"status": "not_applicable"}
        )
        rerank = {
            "enabled": bool(retrieval.get("rerank_enabled")),
            "provider": str(retrieval.get("rerank_provider") or "none"),
            "model": str(retrieval.get("rerank_model") or ""),
            "top_n": int(retrieval.get("rerank_top_n") or 0),
        }
        if rerank["enabled"] and (
            rerank["provider"] not in {"api", "llm", "auto"}
            or not rerank["model"]
            or rerank["top_n"] <= 0
        ):
            raise ValueError("Formal evaluation Rerank identity is incomplete.")
        manifest_targets.append(
            {
                "kb_id": expected_kb_id,
                "version_id": str(target["version_id"]),
                "role": (
                    "baseline"
                    if str(target["version_id"]) == baseline_version_id
                    else "candidate"
                ),
                "version_fingerprint": required_hashes[0],
                "configuration_fingerprint": required_hashes[1],
                "source_manifest_fingerprint": required_hashes[3],
                "corpus_snapshot_hash": expected_corpus,
                "processor": _copy(processor),
                "embedding": _copy(effective),
                "retrieval": _copy(retrieval),
                "rerank": rerank,
                "index_contract": _copy(index_contract),
                "vector_backend_readiness": manifest_backend,
                "runtime_vector_backend_readiness": manifest_runtime_backend,
            }
        )
    manifest_targets.sort(key=lambda item: item["role"])
    seed = _checksum(
        {
            "evaluation_checksum": evaluation_version.get("checksum"),
            "corpus_snapshot_hash": expected_corpus,
            "targets": manifest_targets,
        }
    )
    execution_manifest = seal_execution_manifest(
        {
            "contract_version": "rag-eval-v2",
            "metric_contract_version": "rag-metrics-v2",
            "evaluation_version_id": evaluation_version.get("version_id"),
            "evaluation_checksum": evaluation_version.get("checksum"),
            "corpus_snapshot_hash": expected_corpus,
            "execution_seed": seed,
            "order_contract": "paired-interleaved-sha256-v1",
            "evaluator_code_fingerprint": evaluator_code_fingerprint,
            "targets": manifest_targets,
        }
    )
    return {
        "evidence_qualification": qualification,
        "comparability": {
            "contract_version": "rag-comparability-v1",
            "comparable": True,
            "same_corpus": True,
            "corpus_snapshot_hash": expected_corpus,
            "reason": None,
        },
        "execution_manifest": execution_manifest,
    }


def formal_execution_preflight_reasons(run: dict[str, Any]) -> list[str]:
    """Return structural reasons that must block a Formal run before retrieval."""

    reasons: list[str] = []
    if (
        str(run.get("run_mode") or "") != "formal"
        or str(run.get("metric_contract_version") or "") != "rag-metrics-v2"
    ):
        reasons.append("formal_run_contract_invalid")
    manifest = run.get("execution_manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    if (
        manifest.get("contract_version") != "rag-eval-v2"
        or manifest.get("metric_contract_version") != "rag-metrics-v2"
        or not _execution_manifest_checksum_valid(manifest)
    ):
        reasons.append("execution_manifest_invalid")

    snapshot = run.get("eval_set_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    if (
        snapshot.get("benchmark_contract_version") != "rag-gold-v3"
        or str(snapshot.get("version_id") or "")
        != str(manifest.get("evaluation_version_id") or "")
        or str(snapshot.get("checksum") or "")
        != str(manifest.get("evaluation_checksum") or "")
        or not str(snapshot.get("checksum") or "")
        or not hmac.compare_digest(
            str(snapshot.get("checksum") or ""),
            _published_gold_checksum(snapshot),
        )
    ):
        reasons.append("evaluation_snapshot_invalid")

    raw_manifest_targets = [
        item for item in manifest.get("targets", []) if isinstance(item, dict)
    ]
    manifest_ids = [str(item.get("version_id") or "") for item in raw_manifest_targets]
    raw_run_targets = [
        item for item in run.get("targets", []) if isinstance(item, dict)
    ]
    internal_target_ids = [
        str(item.get("target_id") or "") for item in raw_run_targets
    ]
    run_ids = [
        str(item.get("version_id") or item.get("target_id") or "")
        for item in raw_run_targets
    ]
    roles = [str(item.get("role") or "") for item in raw_manifest_targets]
    baseline_version_id = str(run.get("baseline_version_id") or "")
    declared_baseline = next(
        (
            str(item.get("version_id") or "")
            for item in raw_manifest_targets
            if item.get("role") == "baseline"
        ),
        "",
    )
    if (
        len(raw_manifest_targets) != 2
        or len(set(manifest_ids)) != 2
        or any(not item for item in manifest_ids)
        or sorted(roles) != ["baseline", "candidate"]
        or declared_baseline != baseline_version_id
        or len(raw_run_targets) != 2
        or len(set(internal_target_ids)) != 2
        or any(not item for item in internal_target_ids)
        or len(set(run_ids)) != 2
        or set(run_ids) != set(manifest_ids)
        or internal_target_ids != run_ids
    ):
        reasons.append("formal_target_ledger_invalid")

    corpus_hash = str(manifest.get("corpus_snapshot_hash") or "")
    comparability = run.get("comparability")
    comparability = comparability if isinstance(comparability, dict) else {}
    if (
        not corpus_hash
        or comparability.get("comparable") is not True
        or comparability.get("same_corpus") is not True
        or str(comparability.get("corpus_snapshot_hash") or "") != corpus_hash
        or {
            str(item.get("corpus_snapshot_hash") or "")
            for item in raw_manifest_targets
        }
        != {corpus_hash}
    ):
        reasons.append("formal_corpus_comparability_invalid")

    case_ids = [
        str(item.get("case_id") or "")
        for item in snapshot.get("cases", [])
        if isinstance(item, dict)
    ]
    declared_case_ids = [str(item) for item in run.get("case_ids", [])]
    if (
        not case_ids
        or any(not item for item in case_ids)
        or len(case_ids) != len(set(case_ids))
        or declared_case_ids != case_ids
    ):
        reasons.append("formal_case_ledger_invalid")
    return list(dict.fromkeys(reasons))


def qualify_formal_execution_integrity(
    run: dict[str, Any],
) -> dict[str, Any]:
    """Validate immutable target identities and sanitized per-case route receipts."""

    reasons = formal_execution_preflight_reasons(run)
    if str(run.get("status") or "") == "succeeded":
        expected_integrity_checksum = str(
            run.get("execution_integrity_checksum") or ""
        )
        actual_integrity_checksum = _run_execution_integrity_checksum(run)
        if (
            not expected_integrity_checksum
            or not hmac.compare_digest(
                expected_integrity_checksum, actual_integrity_checksum
            )
        ):
            reasons.append("execution_integrity_checksum_invalid")
    manifest = run.get("execution_manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    if (
        manifest.get("contract_version") != "rag-eval-v2"
        or manifest.get("metric_contract_version") != "rag-metrics-v2"
        or not _execution_manifest_checksum_valid(manifest)
    ):
        reasons.append("execution_manifest_invalid")
    raw_manifest_targets = [
        item for item in manifest.get("targets", []) if isinstance(item, dict)
    ]
    manifest_targets = {
        str(item.get("version_id") or ""): item
        for item in raw_manifest_targets
        if str(item.get("version_id") or "")
    }
    raw_target_results = [
        item for item in run.get("target_results", []) if isinstance(item, dict)
    ]
    target_results = {
        str(item.get("version_id") or ""): item
        for item in raw_target_results
        if str(item.get("version_id") or "")
    }
    raw_run_targets = [
        item for item in run.get("targets", []) if isinstance(item, dict)
    ]
    run_target_ids = {
        str(item.get("version_id") or item.get("target_id") or "")
        for item in raw_run_targets
        if str(item.get("version_id") or item.get("target_id") or "")
    }
    expected_case_ids = {
        str(case.get("case_id") or "")
        for case in (run.get("eval_set_snapshot") or {}).get("cases", [])
        if isinstance(case, dict) and str(case.get("case_id") or "")
    }
    if (
        not run_target_ids
        or not manifest_targets
        or len(raw_run_targets) != len(manifest_targets)
        or len(raw_manifest_targets) != len(manifest_targets)
        or len(raw_target_results) != len(manifest_targets)
        or run_target_ids != set(manifest_targets)
        or set(target_results) != set(manifest_targets)
    ):
        reasons.append("target_ledger_missing")
    if not expected_case_ids:
        reasons.append("evaluation_case_ledger_missing")
    for target in run.get("targets", []):
        if not isinstance(target, dict):
            continue
        version_id = str(target.get("version_id") or target.get("target_id") or "")
        target_id = str(target.get("target_id") or version_id)
        declared = manifest_targets.get(version_id)
        if not isinstance(declared, dict):
            reasons.append(f"target_manifest_missing:{version_id}")
            continue
        aggregate = target_results.get(version_id)
        aggregate_metrics = (
            aggregate.get("metrics") if isinstance(aggregate, dict) else None
        )
        if (
            not isinstance(aggregate_metrics, dict)
            or "error_count" not in aggregate_metrics
        ):
            reasons.append(f"target_result_incomplete:{version_id}")
        elif int(aggregate_metrics.get("error_count") or 0):
            reasons.append(f"execution_errors:{version_id}")
        case_map = (run.get("case_results") or {}).get(target_id)
        case_map = case_map if isinstance(case_map, dict) else {}
        if expected_case_ids and set(case_map) != expected_case_ids:
            reasons.append(f"execution_slots_incomplete:{version_id}")
        if not case_map:
            reasons.append(f"case_receipts_missing:{version_id}")
            continue
        retrieval = declared.get("retrieval")
        retrieval = retrieval if isinstance(retrieval, dict) else {}
        embedding = declared.get("embedding")
        embedding = embedding if isinstance(embedding, dict) else {}
        rerank = declared.get("rerank")
        rerank = rerank if isinstance(rerank, dict) else {}
        mode = str(retrieval.get("mode") or "")
        for case_id, case_result in case_map.items():
            if not isinstance(case_result, dict):
                reasons.append(f"case_receipt_invalid:{version_id}:{case_id}")
                continue
            if str(case_result.get("status") or "") not in {
                "completed",
                "succeeded",
            }:
                reasons.append(f"case_execution_failed:{version_id}:{case_id}")
            fallback_codes = case_result.get("fallback_reason_codes")
            if not isinstance(fallback_codes, list):
                reasons.append(f"fallback_receipt_missing:{version_id}:{case_id}")
                fallback_codes = []
            if fallback_codes:
                reasons.append(f"provider_fallback_used:{version_id}:{case_id}")
            raw_receipt = case_result.get("provider_route_receipts")
            receipt_present = raw_receipt is not None
            receipt = raw_receipt if isinstance(raw_receipt, dict) else {}
            receipt_call_count = receipt.get("call_count")
            receipt_call_count_valid = (
                isinstance(receipt_call_count, int)
                and not isinstance(receipt_call_count, bool)
                and receipt_call_count >= 0
            )
            receipt_reason_codes = receipt.get("reason_codes")
            receipt_reason_codes_valid = (
                isinstance(receipt_reason_codes, list)
                and all(isinstance(item, str) for item in receipt_reason_codes)
            )
            calls = receipt.get("calls")
            calls = calls if isinstance(calls, list) else []
            embedding_calls = [
                call
                for call in calls
                if isinstance(call, dict)
                and call.get("operation") == "embedding_vectors"
            ]
            rerank_calls = [
                call
                for call in calls
                if isinstance(call, dict)
                and call.get("operation") == "rerank_documents"
            ]
            unknown_calls = [
                call
                for call in calls
                if not isinstance(call, dict)
                or call.get("operation")
                not in {"embedding_vectors", "rerank_documents"}
            ]
            routing_mode = str(receipt.get("routing_mode") or "")
            expected_receipt_contract = (
                RAG_ROUTE_RECEIPT_CONTRACT_VERSION
                if routing_mode == "composed"
                else PROVIDER_WORKLOAD_CONTRACT_VERSION
            )
            if calls and (
                unknown_calls
                or str(receipt.get("contract_version") or "")
                != expected_receipt_contract
            ):
                reasons.append(f"provider_receipt_contract_invalid:{version_id}:{case_id}")
            retrieval_receipt = case_result.get("retrieval_receipt")
            retrieval_receipt = (
                retrieval_receipt if isinstance(retrieval_receipt, dict) else {}
            )
            if (
                str(retrieval_receipt.get("mode") or "") != mode
                or retrieval_receipt.get("top_k") != retrieval.get("top_k")
            ):
                reasons.append(f"retrieval_execution_identity_mismatch:{version_id}:{case_id}")
            # Absolute channel filtering may legitimately leave no work for
            # Rerank. This is not a failed Provider call or a fail-open path.
            rerank_skipped_empty = (
                bool(rerank.get("enabled"))
                and retrieval_receipt.get("rerank_applied") is False
                and retrieval_receipt.get("rerank_input_count") == 0
                and retrieval_receipt.get("rerank_output_count") == 0
                and case_result.get("source_count") == 0
                and case_result.get("ranking") == []
            )
            rerank_required = bool(rerank.get("enabled")) and not rerank_skipped_empty
            if not bool(rerank.get("enabled")) and (
                rerank_calls or retrieval_receipt.get("rerank_applied") is True
            ):
                reasons.append(f"unexpected_rerank_execution:{version_id}:{case_id}")
            if mode == "fulltext":
                if embedding_calls:
                    reasons.append("fulltext_embedding_call_detected")
                if (
                    not rerank_required
                    and (
                        receipt_present
                        or str(case_result.get("execution_mode") or "")
                        != "local_non_model"
                    )
                ):
                    reasons.append(f"provider_receipt_invalid:{version_id}:{case_id}")
                elif rerank_required and (
                    not receipt
                    or not str(receipt.get("contract_version") or "")
                    or str(receipt.get("routing_mode") or "")
                    not in {"managed_required", "composed"}
                    or receipt.get("status") != "passed"
                    or not receipt_call_count_valid
                    or receipt_call_count != len(calls)
                    or not calls
                    or not receipt_reason_codes_valid
                    or bool(receipt_reason_codes)
                    or any(
                        not isinstance(call, dict)
                        or call.get("status") != "passed"
                        or call.get("dispatched") is not True
                        for call in calls
                    )
                ):
                    reasons.append(f"provider_receipt_invalid:{version_id}:{case_id}")
                if (
                    str(retrieval_receipt.get("embedding_provider") or "")
                    != "none"
                    or int(retrieval_receipt.get("embedding_dimension") or 0) != 0
                ):
                    reasons.append(
                        f"embedding_execution_identity_mismatch:{version_id}:{case_id}"
                    )
            else:
                if (
                    str(case_result.get("execution_mode") or "") != "managed"
                    or not receipt
                    or not str(receipt.get("contract_version") or "")
                    or str(receipt.get("routing_mode") or "")
                    not in {"managed_required", "composed"}
                    or receipt.get("status") != "passed"
                    or not receipt_call_count_valid
                    or receipt_call_count != len(calls)
                    or not calls
                    or not receipt_reason_codes_valid
                    or bool(receipt_reason_codes)
                    or len(embedding_calls) != 1
                    or any(
                        not isinstance(call, dict)
                        or call.get("status") != "passed"
                        or call.get("dispatched") is not True
                        for call in calls
                    )
                ):
                    reasons.append(f"provider_receipt_invalid:{version_id}:{case_id}")
                requested_models = {
                    str(call.get("model_id") or "")
                    for call in embedding_calls
                    if isinstance(call, dict)
                }
                if (
                    requested_models != {str(embedding.get("model") or "")}
                    or any(
                        not str(call.get("provider_kind") or "")
                        or not provider_operation_model_matches(
                            provider_kind=str(call.get("provider_kind") or ""),
                            requested_model=str(embedding.get("model") or ""),
                            actual_model=str(call.get("actual_model") or ""),
                        )
                        for call in embedding_calls
                    )
                ):
                    reasons.append(f"embedding_model_mismatch:{version_id}:{case_id}")
                if (
                    str(retrieval_receipt.get("embedding_provider") or "")
                    != str(embedding.get("provider") or "")
                    or str(retrieval_receipt.get("embedding_model") or "")
                    != str(embedding.get("model") or "")
                    or int(retrieval_receipt.get("embedding_dimension") or 0)
                    != int(embedding.get("dimension") or 0)
                    or str(
                        retrieval_receipt.get("embedding_space_fingerprint") or ""
                    )
                    != str(embedding.get("embedding_space_fingerprint") or "")
                ):
                    reasons.append(
                        f"embedding_execution_identity_mismatch:{version_id}:{case_id}"
                    )
            ineligibility = retrieval_receipt.get(
                "promotion_ineligibility_reasons"
            )
            if not isinstance(ineligibility, list):
                reasons.append(
                    f"promotion_eligibility_receipt_missing:{version_id}:{case_id}"
                )
                ineligibility = []
            if ineligibility or retrieval_receipt.get("promotion_eligible") is not True:
                reasons.append(f"retrieval_ineligible:{version_id}:{case_id}")
            if rerank_required:
                configured_rerank_provider = str(rerank.get("provider") or "")
                actual_rerank_provider = str(
                    retrieval_receipt.get("rerank_provider_used") or ""
                )
                provider_matches = (
                    actual_rerank_provider in {"api", "llm"}
                    if configured_rerank_provider == "auto"
                    else actual_rerank_provider == configured_rerank_provider
                )
                if actual_rerank_provider == "managed":
                    expected_modes = (
                        {"dedicated", "llm_json"}
                        if configured_rerank_provider == "auto"
                        else {"dedicated"} if configured_rerank_provider == "api"
                        else {"llm_json"}
                    )
                    provider_matches = (
                        len(rerank_calls) == 1
                        and str(rerank_calls[0].get("access_mode") or "") in expected_modes
                        and retrieval_receipt.get("rerank_provider_target_used")
                        == f"managed_rerank_{rerank_calls[0].get('access_mode')}"
                        and rerank_calls[0].get("model_id") == rerank.get("model")
                    )
                if (
                    retrieval_receipt.get("rerank_applied") is not True
                    or not provider_matches
                    or str(retrieval_receipt.get("rerank_model_used") or "")
                    != str(rerank.get("model") or "")
                    or len(rerank_calls) != 1
                ):
                    reasons.append(f"rerank_fail_open:{version_id}:{case_id}")
                requested_models = {
                    str(call.get("model_id") or "")
                    for call in rerank_calls
                    if isinstance(call, dict)
                }
                if (
                    requested_models != {str(rerank.get("model") or "")}
                    or any(
                        not str(call.get("provider_kind") or "")
                        or not provider_operation_model_matches(
                            provider_kind=str(call.get("provider_kind") or ""),
                            requested_model=str(rerank.get("model") or ""),
                            actual_model=str(call.get("actual_model") or ""),
                        )
                        for call in rerank_calls
                    )
                ):
                    reasons.append(f"rerank_model_mismatch:{version_id}:{case_id}")

    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "contract_version": "rag-formal-execution-integrity-v1",
        "status": "qualified" if not unique_reasons else "ineligible",
        "qualified": not unique_reasons,
        "reason_codes": unique_reasons,
        "target_count": len(manifest_targets),
        "case_receipt_count": sum(
            len(items)
            for items in (run.get("case_results") or {}).values()
            if isinstance(items, dict)
        ),
    }


class KnowledgeEvaluationStore:
    """File-backed evaluation datasets, runs, and promotion policies."""

    def __init__(
        self,
        path: Path,
        *,
        reproducibility_resolver: Callable[[dict[str, Any]], dict[str, Any]]
        | None = None,
        code_fingerprint_resolver: Callable[[], str] | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._reproducibility_resolver = reproducibility_resolver
        self._code_fingerprint_resolver = (
            code_fingerprint_resolver or evaluation_runtime_code_fingerprint
        )

    def create_set(
        self,
        kb_id: str,
        name: str,
        description: str = "",
        *,
        origin: str = "manual",
        catalog_ref: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        coverage: dict[str, Any] | None = None,
        calibration: dict[str, Any] | None = None,
        benchmark_role: str | None = None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Evaluation set name is required.")
        now = time.time()
        item = {
            "eval_set_id": f"evalset_{uuid.uuid4().hex}",
            "kb_id": kb_id,
            "name": clean_name[:160],
            "description": description.strip()[:1000],
            "revision": 1,
            "status": "active",
            "cases": [],
            "origin": str(origin or "manual")[:80],
            "catalog_ref": _copy(catalog_ref or {}),
            "provenance": _copy(provenance or {}),
            "coverage": _copy(coverage or {}),
            "calibration": _copy(calibration or {}),
            "benchmark_role": normalize_benchmark_role(
                benchmark_role,
                origin=str(origin or "manual"),
                catalog_ref=catalog_ref,
            ),
            "latest_version": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            data = self._read_unlocked()
            data["sets"][item["eval_set_id"]] = item
            self._write_unlocked(data)
        return _copy(item)

    def create_generated_set(
        self,
        kb_id: str,
        name: str,
        description: str,
        *,
        cases: list[dict[str, Any]],
        provenance: dict[str, Any],
        coverage: dict[str, Any],
        calibration: dict[str, Any],
        benchmark_role: str = "strategy_tuning",
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Evaluation set name is required.")
        if not cases or len(cases) > 500:
            raise ValueError("Generated evaluation set needs 1-500 cases.")
        normalized_cases = [self._normalize_case(case) for case in cases]
        now = time.time()
        item = {
            "eval_set_id": f"evalset_{uuid.uuid4().hex}",
            "kb_id": kb_id,
            "name": clean_name[:160],
            "description": description.strip()[:1000],
            "revision": 1,
            "status": "active",
            "cases": normalized_cases,
            "origin": "generated",
            "catalog_ref": {},
            "provenance": _copy(provenance),
            "coverage": _copy(coverage),
            "calibration": _copy(calibration),
            "benchmark_role": normalize_benchmark_role(
                benchmark_role,
                origin="generated",
            ),
            "latest_version": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            data = self._read_unlocked()
            data["sets"][item["eval_set_id"]] = item
            self._write_unlocked(data)
        return _copy(item)

    def list_sets(self, kb_id: str) -> list[dict[str, Any]]:
        data = self._read()
        items = [item for item in data["sets"].values() if item.get("kb_id") == kb_id]
        items.sort(key=lambda item: float(item.get("updated_at", 0.0)), reverse=True)
        return [self._set_payload(item) for item in items]

    def get_set(self, eval_set_id: str) -> dict[str, Any]:
        item = self._read()["sets"].get(eval_set_id)
        if not isinstance(item, dict):
            raise EvaluationSetNotFoundError("Knowledge evaluation set not found.")
        return self._set_payload(item)

    def publish_set(
        self,
        eval_set_id: str,
        *,
        expected_revision: int,
        release_notes: str = "",
        acknowledge_calibration_warnings: bool = False,
        corpus_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            if item.get("status") != "active" or not item.get("cases"):
                raise EvaluationStateError(
                    "Only active evaluation sets with cases can be published."
                )
            if str(item.get("origin") or "manual") == "generated":
                calibration = dict(item.get("calibration") or {})
                calibration_status = str(calibration.get("status") or "pending")
                if int(calibration.get("dataset_revision") or 0) != int(
                    item.get("revision") or 0
                ):
                    raise EvaluationStateError(
                        "Generated evaluation set must be recalibrated after editing."
                    )
                if calibration_status not in {"calibrated", "warning"}:
                    raise EvaluationStateError(
                        "Generated evaluation set must complete calibration before publishing."
                    )
                if calibration_status == "warning" and not acknowledge_calibration_warnings:
                    raise EvaluationStateError(
                        "Calibration warnings must be explicitly acknowledged before publishing."
                    )
                pending_reviews = [
                    case
                    for case in item.get("cases", [])
                    if case.get("expected_no_result")
                    and str(case.get("review_status") or "pending") != "approved"
                ]
                if pending_reviews:
                    raise EvaluationStateError(
                        "Generated no-result cases require explicit review before publishing."
                    )
            cases = [self._normalize_case(case, preserve_id=True) for case in item["cases"]]
            benchmark_role = normalize_benchmark_role(
                item.get("benchmark_role"),
                origin=str(item.get("origin") or "manual"),
                catalog_ref=dict(item.get("catalog_ref") or {}),
            )
            v3_roles = {
                "strategy_tuning",
                "threshold_calibration",
                "held_out_qualification",
            }
            if benchmark_role in v3_roles:
                if any(
                    str(case.get("review_status") or "") == "rejected"
                    for case in cases
                ):
                    raise EvaluationStateError(
                        "Rejected cases cannot be published as locked V3 evidence."
                    )
                if benchmark_role == "held_out_qualification" and any(
                    str(case.get("review_status") or "") != "approved"
                    for case in cases
                ):
                    raise EvaluationStateError(
                        "Held-out qualification requires explicit review of every case before publishing."
                    )
            current = [
                version
                for version in data["versions"].values()
                if version.get("eval_set_id") == eval_set_id
            ]
            version_number = max(
                (int(version.get("version") or 0) for version in current),
                default=0,
            ) + 1
            now = time.time()
            version_id = f"evalsetver_{uuid.uuid4().hex}"
            qualification_manifest = {
                "contract_version": (
                    "rag-gold-qualification-v3"
                    if benchmark_role in v3_roles
                    else "rag-gold-qualification-v2"
                ),
                "dataset_role": benchmark_role,
                "corpus_checksum": str((corpus_snapshot or {}).get("checksum") or ""),
                "anchor_checksum": _qualification_anchor_checksum(cases),
                "tuner_usage_lineage": [],
                "case_count": len(cases),
                "positive_case_count": sum(
                    1 for case in cases if not case.get("expected_no_result")
                ),
                "hard_negative_count": sum(
                    1 for case in cases if case.get("expected_no_result")
                ),
                "trusted_review_count": sum(
                    1
                    for case in cases
                    if isinstance(case.get("review_evidence"), dict)
                    and case["review_evidence"].get("source")
                    == "authenticated_ui"
                ),
            }
            version = {
                "version_id": version_id,
                "eval_set_id": eval_set_id,
                "kb_id": item["kb_id"],
                "version": version_number,
                "name": item["name"],
                "description": item["description"],
                "source_revision": int(item["revision"]),
                "cases": _copy(cases),
                "origin": str(item.get("origin") or "manual"),
                "catalog_ref": _copy(item.get("catalog_ref") or {}),
                "provenance": _copy(item.get("provenance") or {}),
                "coverage": _copy(item.get("coverage") or {}),
                "calibration": _copy(item.get("calibration") or {}),
                "benchmark_role": benchmark_role,
                "release_notes": str(release_notes or "")[:1000],
                "benchmark_contract_version": (
                    "rag-gold-v3" if benchmark_role in v3_roles else "rag-gold-v2"
                ),
                "corpus_snapshot": _copy(corpus_snapshot or {}),
                "qualification_manifest": qualification_manifest,
                "published_at": now,
            }
            version["checksum"] = _published_gold_checksum(version)
            if benchmark_role in v3_roles:
                locked_qualification = qualify_locked_dataset_evidence(version)
                formal_qualification = (
                    qualify_formal_evidence(version)
                    if benchmark_role == "held_out_qualification"
                    else None
                )
                qualified = bool(locked_qualification["qualified"]) and (
                    formal_qualification is None
                    or bool(formal_qualification["qualified"])
                )
                failed_checks = [
                    check["id"]
                    for check in locked_qualification["checks"]
                    if not check["passed"]
                ]
                if formal_qualification is not None:
                    failed_checks.extend(
                        check["id"]
                        for check in formal_qualification["checks"]
                        if not check["passed"]
                    )
                if benchmark_role == "held_out_qualification" and not qualified:
                    raise EvaluationStateError(
                        "Held-out qualification failed publication checks: "
                        + ", ".join(list(dict.fromkeys(failed_checks))[:8])
                    )
                if not locked_qualification["qualified"]:
                    version["benchmark_contract_version"] = "rag-gold-v1"
                version["qualification_manifest"] = {
                    **qualification_manifest,
                    "status": "qualified" if qualified else "diagnostic_only",
                    "failed_checks": list(dict.fromkeys(failed_checks)),
                }
            else:
                version["qualification_manifest"] = {
                    **qualification_manifest,
                    "status": "legacy_diagnostic",
                    "failed_checks": ["legacy_benchmark_contract"],
                }
            version["checksum"] = _published_gold_checksum(version)
            data["versions"][version_id] = version
            item["latest_version"] = version_number
            item["updated_at"] = now
            self._write_unlocked(data)
            return _copy(version)

    def set_calibration(
        self,
        eval_set_id: str,
        *,
        expected_revision: int,
        calibration: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            item["calibration"] = _copy(calibration)
            item["updated_at"] = time.time()
            self._write_unlocked(data)
            return self._set_payload(item)

    def list_set_versions(self, eval_set_id: str) -> list[dict[str, Any]]:
        data = self._read()
        self._set_or_raise(data, eval_set_id)
        items = [
            self._version_payload(version)
            for version in data["versions"].values()
            if version.get("eval_set_id") == eval_set_id
        ]
        items.sort(key=lambda value: int(value.get("version") or 0), reverse=True)
        return items

    def get_set_version(self, eval_set_id: str, version: int) -> dict[str, Any]:
        data = self._read()
        self._set_or_raise(data, eval_set_id)
        item = next(
            (
                value
                for value in data["versions"].values()
                if value.get("eval_set_id") == eval_set_id
                and int(value.get("version") or 0) == int(version)
            ),
            None,
        )
        if not isinstance(item, dict):
            raise EvaluationSetNotFoundError("Knowledge evaluation set version not found.")
        return self._version_payload(item)

    def update_set(
        self,
        eval_set_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        benchmark_role: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            if name is not None:
                clean_name = name.strip()
                if not clean_name:
                    raise ValueError("Evaluation set name is required.")
                item["name"] = clean_name[:160]
            if description is not None:
                item["description"] = description.strip()[:1000]
            if status is not None:
                if status not in {"active", "archived"}:
                    raise ValueError("Evaluation set status must be active or archived.")
                item["status"] = status
            if benchmark_role is not None:
                item["benchmark_role"] = normalize_benchmark_role(
                    benchmark_role,
                    origin=str(item.get("origin") or "manual"),
                    catalog_ref=dict(item.get("catalog_ref") or {}),
                )
            self._touch_set(item)
            self._write_unlocked(data)
            return self._set_payload(item)

    def add_cases(
        self,
        eval_set_id: str,
        *,
        expected_revision: int,
        cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not cases or len(cases) > 500:
            raise ValueError("Import must contain between 1 and 500 evaluation cases.")
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            if len(item["cases"]) + len(cases) > 500:
                raise ValueError("An evaluation set supports at most 500 cases.")
            for raw in cases:
                item["cases"].append(self._normalize_case(raw))
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

    def update_case(
        self,
        eval_set_id: str,
        case_id: str,
        *,
        expected_revision: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            index = next(
                (index for index, case in enumerate(item["cases"]) if case["case_id"] == case_id),
                None,
            )
            if index is None:
                raise EvaluationSetNotFoundError("Knowledge evaluation case not found.")
            merged = {**item["cases"][index], **values, "case_id": case_id}
            merged.pop("review_evidence", None)
            merged["review_status"] = (
                "pending" if bool(merged.get("expected_no_result")) else "not_required"
            )
            item["cases"][index] = self._normalize_case(merged, preserve_id=True)
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

    def review_case(
        self,
        eval_set_id: str,
        case_id: str,
        *,
        expected_revision: int,
        decision: str,
        reason: str,
        reviewer: dict[str, str],
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Evaluation review decision is invalid.")
        clean_reviewer = {
            "tenant_id": str(reviewer.get("tenant_id") or "")[:160],
            "role": str(reviewer.get("role") or "")[:80],
        }
        if not all(clean_reviewer.values()):
            raise ValueError("Evaluation review requires an authenticated reviewer.")
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            case = next(
                (
                    value
                    for value in item.get("cases", [])
                    if value.get("case_id") == case_id
                ),
                None,
            )
            if not isinstance(case, dict):
                raise EvaluationSetNotFoundError(
                    "Knowledge evaluation case not found."
                )
            item["revision"] = int(item.get("revision", 0)) + 1
            item["updated_at"] = time.time()
            calibration = item.get("calibration")
            if isinstance(calibration, dict) and calibration:
                calibration["dataset_revision"] = int(item["revision"])
            case["review_status"] = decision
            if (
                decision == "approved"
                and (case.get("targeting") or {}).get("leakage_warning")
                and not str(reason or "").strip()
            ):
                raise ValueError(
                    "Leakage warnings require an explicit human review reason."
                )
            case["review_evidence"] = {
                "decision": decision,
                "reviewed_at": time.time(),
                "dataset_revision": int(item["revision"]),
                "source": "authenticated_ui",
                "reviewer": clean_reviewer,
                "reason": str(reason or "").strip()[:1000],
                "case_checksum": _case_review_checksum(case),
            }
            self._write_unlocked(data)
            return self._set_payload(item)

    def delete_case(
        self,
        eval_set_id: str,
        case_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            item = self._set_or_raise(data, eval_set_id)
            self._check_revision(item, expected_revision)
            remaining = [case for case in item["cases"] if case["case_id"] != case_id]
            if len(remaining) == len(item["cases"]):
                raise EvaluationSetNotFoundError("Knowledge evaluation case not found.")
            item["cases"] = remaining
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

    def create_run(
        self,
        *,
        evaluation_set: dict[str, Any],
        targets: list[dict[str, Any]],
        baseline_version_id: str | None,
        ks: list[int],
        gate_policy: dict[str, Any],
        evaluation_set_version: dict[str, Any] | None = None,
        case_ids: list[str] | None = None,
        run_mode: str = "diagnostic",
        execution_manifest: dict[str, Any] | None = None,
        comparability: dict[str, Any] | None = None,
        evidence_qualification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if run_mode not in {"diagnostic", "formal"}:
            raise ValueError("Evaluation run_mode is invalid.")
        if run_mode == "formal" and case_ids is not None:
            raise EvaluationStateError("Formal evaluation cannot run a case subset.")
        if run_mode == "formal" and not _execution_manifest_checksum_valid(
            execution_manifest or {}
        ):
            raise EvaluationStateError(
                "Formal evaluation requires a checksummed execution manifest."
            )
        now = time.time()
        snapshot = _copy(evaluation_set_version or evaluation_set)
        if case_ids is not None:
            requested = list(dict.fromkeys(str(item) for item in case_ids if str(item)))
            by_id = {
                str(case.get("case_id") or ""): case
                for case in snapshot.get("cases", [])
                if isinstance(case, dict)
            }
            missing = [case_id for case_id in requested if case_id not in by_id]
            if missing:
                raise EvaluationStateError(
                    f"Evaluation case is unavailable in the fixed snapshot: {missing[0]}"
                )
            snapshot["cases"] = [_copy(by_id[case_id]) for case_id in requested]
            if not snapshot["cases"]:
                raise EvaluationStateError("Evaluation case subset cannot be empty.")
        effective_execution_manifest = _copy(execution_manifest or {})
        if run_mode == "diagnostic" and not effective_execution_manifest:
            experimental_variables = [
                {
                    "target_id": str(target.get("target_id") or ""),
                    "retrieval_override": _copy(target.get("retrieval") or {}),
                }
                for target in targets
                if isinstance(target, dict) and target.get("retrieval")
            ]
            manifest_targets: list[dict[str, Any]] = []
            for target in targets:
                evidence = target.get("version_evidence")
                evidence = evidence if isinstance(evidence, dict) else {}
                retrieval = evidence.get("retrieval")
                retrieval = retrieval if isinstance(retrieval, dict) else {}
                fulltext = str(retrieval.get("mode") or "") == "fulltext"
                manifest_targets.append(
                    {
                        "kb_id": str(
                            evidence.get("kb_id")
                            or evaluation_set.get("kb_id")
                            or ""
                        ),
                        "version_id": str(target.get("version_id") or ""),
                        "version_fingerprint": str(
                            evidence.get("version_fingerprint") or ""
                        ),
                        "configuration_fingerprint": str(
                            evidence.get("configuration_fingerprint") or ""
                        ),
                        "source_manifest_fingerprint": str(
                            evidence.get("source_manifest_fingerprint") or ""
                        ),
                        "corpus_snapshot_hash": str(
                            target.get("corpus_snapshot_hash") or ""
                        ),
                        "processor": _copy(evidence.get("processor") or {}),
                        "embedding": _copy(
                            (evidence.get("embedding") or {}).get("effective")
                            or {}
                        ),
                        "retrieval": _copy(retrieval),
                        "index_contract": _copy(
                            evidence.get("index_contract") or {}
                        ),
                        "vector_backend_readiness": (
                            {"status": "not_applicable"}
                            if fulltext
                            else _copy(
                                evidence.get("vector_backend_readiness") or {}
                            )
                        ),
                        "runtime_vector_backend_readiness": (
                            {"status": "not_applicable"}
                            if fulltext
                            else _copy(
                                evidence.get("runtime_vector_backend_readiness")
                                or {}
                            )
                        ),
                    }
                )
            effective_execution_manifest = seal_execution_manifest(
                {
                    "contract_version": "rag-eval-diagnostic-v1",
                    "metric_contract_version": "rag-metrics-v1",
                    "evaluator_code_fingerprint": self._code_fingerprint_resolver(),
                    "evaluation_version_id": (
                        evaluation_set_version or {}
                    ).get("version_id"),
                    "evaluation_checksum": (
                        evaluation_set_version or {}
                    ).get("checksum"),
                    "experimental_variables": experimental_variables,
                    "targets": manifest_targets,
                }
            )
        run = {
            "run_id": f"evalrun_{uuid.uuid4().hex}",
            "kb_id": evaluation_set["kb_id"],
            "eval_set_id": evaluation_set["eval_set_id"],
            "eval_set_revision": evaluation_set["revision"],
            "eval_set_version": (
                int(evaluation_set_version["version"])
                if evaluation_set_version is not None
                else None
            ),
            "eval_set_version_id": (
                str(evaluation_set_version["version_id"])
                if evaluation_set_version is not None
                else None
            ),
            "eval_set_snapshot": snapshot,
            "run_mode": run_mode,
            "metric_contract_version": (
                "rag-metrics-v2" if run_mode == "formal" else "rag-metrics-v1"
            ),
            "evidence_qualification": _copy(
                evidence_qualification or qualify_promotion_evidence(snapshot)
            ),
            "execution_manifest": effective_execution_manifest,
            "comparability": _copy(
                comparability
                or {
                    "contract_version": "rag-comparability-v1",
                    "comparable": False,
                    "same_corpus": False,
                    "reason": "diagnostic_run",
                }
            ),
            "case_ids": [
                str(case.get("case_id") or "") for case in snapshot.get("cases", [])
            ],
            "targets": _copy(targets),
            "baseline_version_id": baseline_version_id,
            "ks": sorted(set(ks)),
            "gate_policy": _copy(gate_policy),
            "status": "queued",
            "progress": 0,
            "cancel_requested": False,
            "case_results": {},
            "inflight_slots": {},
            "target_results": [],
            "run_registry_id": None,
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
        return self.run_payload(run)

    def list_runs(
        self,
        kb_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        data = self._read()
        runs = [item for item in data["runs"].values() if item.get("kb_id") == kb_id]
        if status:
            runs = [item for item in runs if item.get("status") == status]
        runs.sort(key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
        return [
            self.run_payload(item, include_cases=False, data=data)
            for item in runs[: max(1, limit)]
        ]

    def get_run(
        self,
        run_id: str,
        *,
        project_reproducibility: bool = True,
    ) -> dict[str, Any]:
        data = self._read()
        run = data["runs"].get(run_id)
        if not isinstance(run, dict):
            raise EvaluationRunNotFoundError("Knowledge evaluation run not found.")
        if not project_reproducibility:
            return _copy(run)
        return self.run_payload(run, data=data)

    def claim_next_run(self) -> dict[str, Any] | None:
        with self._lock:
            data = self._read_unlocked()
            queued = [item for item in data["runs"].values() if item.get("status") == "queued"]
            if not queued:
                return None
            queued.sort(key=lambda item: float(item.get("created_at", 0.0)))
            run = queued[0]
            run["status"] = "running"
            run["started_at"] = run.get("started_at") or time.time()
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return _copy(run)

    def recover_runs(self) -> int:
        recovered = 0
        with self._lock:
            data = self._read_unlocked()
            for run in data["runs"].values():
                if run.get("status") == "running":
                    for slot in list((run.get("inflight_slots") or {}).values()):
                        if not isinstance(slot, dict):
                            continue
                        target_id = str(slot.get("target_id") or "")
                        case_id = str(slot.get("case_id") or "")
                        if not target_id or not case_id:
                            continue
                        case = next(
                            (
                                item
                                for item in run.get("eval_set_snapshot", {}).get(
                                    "cases", []
                                )
                                if str(item.get("case_id") or "") == case_id
                            ),
                            {},
                        )
                        results = run.setdefault("case_results", {}).setdefault(
                            target_id, {}
                        )
                        results.setdefault(
                            case_id,
                            {
                                "status": "failed",
                                "metrics": {},
                                "latency_ms": round(
                                    max(
                                        0.0,
                                        time.time()
                                        - float(slot.get("claimed_at") or time.time()),
                                    )
                                    * 1000,
                                    3,
                                ),
                                "source_count": 0,
                                "expected_count": len(
                                    case.get("expected_refs") or []
                                ),
                                "matched_expected_count": 0,
                                "expected_no_result": bool(
                                    case.get("expected_no_result")
                                ),
                                "no_result": True,
                                "warning_count": 0,
                                "warnings": [],
                                "ranking": [],
                                "error": (
                                    "Interrupted evaluation call was not retried."
                                ),
                                "case_id": case_id,
                            },
                        )
                    run["inflight_slots"] = {}
                    run["status"] = "queued"
                    run["updated_at"] = time.time()
                    recovered += 1
            if recovered:
                self._write_unlocked(data)
        return recovered

    def claim_case_slot(
        self,
        run_id: str,
        target_id: str,
        case_id: str,
    ) -> bool:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            existing = (
                run.get("case_results", {}).get(target_id, {}).get(case_id)
            )
            if isinstance(existing, dict):
                return False
            inflight = run.setdefault("inflight_slots", {})
            if any(
                isinstance(slot, dict)
                and str(slot.get("target_id") or "") == target_id
                and str(slot.get("case_id") or "") == case_id
                for slot in inflight.values()
            ):
                return False
            slot_id = _checksum(
                {"run_id": run_id, "target_id": target_id, "case_id": case_id}
            )
            inflight[slot_id] = {
                "target_id": target_id,
                "case_id": case_id,
                "claimed_at": time.time(),
            }
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return True

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            if run["status"] not in {"queued", "running"}:
                raise EvaluationStateError("Only queued or running evaluation runs can be cancelled.")
            run["cancel_requested"] = True
            if run["status"] == "queued":
                run["status"] = "cancelled"
                run["completed_at"] = time.time()
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return self.run_payload(run)

    def cancel_requested(self, run_id: str) -> bool:
        return bool(self._run_or_raise(self._read(), run_id).get("cancel_requested"))

    def record_case_result(
        self,
        run_id: str,
        target_id: str,
        case_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            target_results = run.setdefault("case_results", {}).setdefault(target_id, {})
            target_results[case_id] = _copy(result)
            inflight = run.setdefault("inflight_slots", {})
            run["inflight_slots"] = {
                slot_id: slot
                for slot_id, slot in inflight.items()
                if not (
                    isinstance(slot, dict)
                    and str(slot.get("target_id") or "") == target_id
                    and str(slot.get("case_id") or "") == case_id
                )
            }
            total = max(1, len(run["targets"]) * len(run["eval_set_snapshot"]["cases"]))
            completed = sum(len(items) for items in run["case_results"].values())
            run["progress"] = min(99, int(completed * 100 / total))
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return _copy(run)

    def set_run_registry_id(self, run_id: str, registry_id: str) -> None:
        self._update_run(run_id, {"run_registry_id": registry_id})

    def complete_run(
        self,
        run_id: str,
        target_results: list[dict[str, Any]],
        *,
        paired_confidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            run.update(
                {
                    "status": "succeeded",
                    "progress": 100,
                    "target_results": _copy(target_results),
                    "paired_confidence": _copy(paired_confidence or {}),
                    "completed_at": time.time(),
                    "error": None,
                }
            )
            run["execution_integrity_checksum"] = _run_execution_integrity_checksum(
                run
            )
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return self.run_payload(run, data=data)

    def fail_run(self, run_id: str, error: str) -> dict[str, Any]:
        return self._update_run(
            run_id,
            {
                "status": "failed",
                "error": _safe_error(error),
                "completed_at": time.time(),
            },
        )

    def complete_cancel(self, run_id: str) -> dict[str, Any]:
        return self._update_run(
            run_id,
            {"status": "cancelled", "completed_at": time.time()},
        )

    def get_gate_policy(self, kb_id: str) -> dict[str, Any]:
        stored = self._read()["gate_policies"].get(kb_id)
        return {**_compatible_gate_policy(stored), "kb_id": kb_id}

    def set_gate_policy(self, kb_id: str, values: dict[str, Any]) -> dict[str, Any]:
        policy = _validate_gate_policy(_compatible_gate_policy(values))
        with self._lock:
            data = self._read_unlocked()
            data["gate_policies"][kb_id] = policy
            self._write_unlocked(data)
        return {**policy, "kb_id": kb_id}

    def assert_promotion_allowed(
        self,
        *,
        kb_id: str,
        version_id: str,
        evaluation_run_id: str | None,
        require_passed_run: bool,
    ) -> dict[str, Any] | None:
        policy = self.get_gate_policy(kb_id)
        required = require_passed_run or policy["mode"] == "required"
        if not evaluation_run_id:
            if required:
                raise EvaluationPromotionError(
                    "This knowledge base requires a passing evaluation run before activation."
                )
            return None
        run = self.get_run(evaluation_run_id)
        if run["status"] != "succeeded" or run["kb_id"] != kb_id:
            raise EvaluationPromotionError("Evaluation run is not a successful run for this knowledge base.")
        if (
            str(run.get("run_mode") or "diagnostic") != "formal"
            or str(run.get("metric_contract_version") or "") != "rag-metrics-v2"
            or not bool((run.get("comparability") or {}).get("comparable"))
        ):
            raise EvaluationPromotionError(
                "Candidate promotion requires a comparable Formal rag-eval-v2 run."
            )
        if str(run.get("reproducibility_status") or "") != "current":
            raise EvaluationPromotionError(
                "Candidate promotion requires a current reproducible evaluation run."
            )
        execution_integrity = qualify_formal_execution_integrity(run)
        if not bool(execution_integrity.get("qualified")):
            raise EvaluationPromotionError(
                "Candidate promotion requires complete and consistent Formal execution receipts."
            )
        if run.get("eval_set_version") is None:
            current_set = self.get_set(str(run["eval_set_id"]))
            if int(current_set["revision"]) != int(run["eval_set_revision"]):
                raise EvaluationPromotionError("Evaluation set changed after this run; run it again before promotion.")
        target = next(
            (item for item in run["target_results"] if item.get("version_id") == version_id),
            None,
        )
        if not isinstance(target, dict):
            raise EvaluationPromotionError("Evaluation run does not contain the requested version.")
        gate = target.get("promotion_gate") or {}
        if not bool(gate.get("passed")):
            raise EvaluationPromotionError("Candidate version did not pass the promotion gate.")
        return target

    def run_payload(
        self,
        run: dict[str, Any],
        *,
        include_cases: bool = True,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = _copy(run)
        payload.update(self._reproducibility_projection(run, data=data))
        if not include_cases:
            payload.pop("eval_set_snapshot", None)
            payload.pop("case_results", None)
        return payload

    def _reproducibility_projection(
        self,
        run: dict[str, Any],
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        orphaned = False
        if str(run.get("run_mode") or "diagnostic") == "formal":
            reasons.extend(formal_execution_preflight_reasons(run))
        manifest = run.get("execution_manifest")
        manifest = manifest if isinstance(manifest, dict) else {}
        if not _execution_manifest_checksum_valid(manifest):
            reasons.append("execution_manifest_checksum_invalid")
        current_code_fingerprint = self._code_fingerprint_resolver()
        expected_code_fingerprint = str(
            manifest.get("evaluator_code_fingerprint") or ""
        )
        if (
            not expected_code_fingerprint
            or expected_code_fingerprint != current_code_fingerprint
        ):
            reasons.append("evaluator_code_fingerprint_mismatch")

        current_data = data if isinstance(data, dict) else self._read()
        eval_set_id = str(run.get("eval_set_id") or "")
        if not isinstance(current_data.get("sets", {}).get(eval_set_id), dict):
            orphaned = True
            reasons.append("evaluation_set_missing")
        eval_version_id = str(run.get("eval_set_version_id") or "")
        if eval_version_id:
            version = current_data.get("versions", {}).get(eval_version_id)
            if not isinstance(version, dict):
                orphaned = True
                reasons.append("evaluation_version_missing")
            elif str(version.get("checksum") or "") != str(
                manifest.get("evaluation_checksum") or ""
            ):
                reasons.append("evaluation_checksum_mismatch")
            if (
                isinstance(version, dict)
                and version.get("benchmark_contract_version")
                in {"rag-gold-v2", "rag-gold-v3"}
            ):
                if not hmac.compare_digest(str(version.get("checksum") or ""), _published_gold_checksum(version)):
                    reasons.append("published_gold_checksum_invalid")
        elif str(run.get("run_mode") or "diagnostic") == "formal":
            reasons.append("formal_evaluation_version_missing")
        if str(run.get("status") or "") == "succeeded" and not hmac.compare_digest(
            str(run.get("execution_integrity_checksum") or ""),
            _run_execution_integrity_checksum(run),
        ):
            reasons.append("execution_integrity_checksum_invalid")

        actual: dict[str, Any] = {
            "evaluator_code_fingerprint": current_code_fingerprint,
            "targets": {},
        }
        if self._reproducibility_resolver is None:
            reasons.append("runtime_reference_resolver_unavailable")
        else:
            try:
                resolved = self._reproducibility_resolver(_copy(run))
            except Exception:
                resolved = {}
                orphaned = True
                reasons.append("runtime_reference_resolution_failed")
            if resolved.get("kb_exists") is not True:
                orphaned = True
                reasons.append("knowledge_base_missing")
            resolved_targets = resolved.get("targets")
            resolved_targets = (
                resolved_targets if isinstance(resolved_targets, dict) else {}
            )
            actual["targets"] = _copy(resolved_targets)
            declared_ids = {
                str(item.get("version_id") or "")
                for item in manifest.get("targets", []) if isinstance(item, dict)
            }
            for target in run.get("targets", []):
                if not isinstance(target, dict):
                    continue
                version_id = str(target.get("version_id") or target.get("target_id") or "")
                if version_id not in resolved_targets:
                    orphaned = True
                    reasons.append(f"pipeline_version_missing:{version_id}")
                if version_id not in declared_ids:
                    reasons.append(f"target_manifest_missing:{version_id}")
            for declared in manifest.get("targets", []):
                if not isinstance(declared, dict):
                    continue
                version_id = str(declared.get("version_id") or "")
                if any(
                    re.fullmatch(r"[0-9a-f]{64}", str(declared.get(field) or ""))
                    is None
                    for field in (
                        "version_fingerprint",
                        "configuration_fingerprint",
                        "source_manifest_fingerprint",
                    )
                ):
                    reasons.append(f"target_identity_incomplete:{version_id}")
                current = resolved_targets.get(version_id)
                if not isinstance(current, dict):
                    orphaned = True
                    reasons.append(f"pipeline_version_missing:{version_id}")
                    continue
                if current.get("corpus_snapshot_status") == "unreproducible":
                    reasons.append(f"corpus_snapshot_unreproducible:{version_id}")
                for field in (
                    "kb_id",
                    "version_fingerprint",
                    "configuration_fingerprint",
                    "source_manifest_fingerprint",
                    "corpus_snapshot_hash",
                ):
                    if str(current.get(field) or "") != str(
                        declared.get(field) or ""
                    ):
                        reasons.append(f"{field}_mismatch:{version_id}")
                for field in (
                    "embedding",
                    "retrieval",
                    "index_contract",
                    "vector_backend_readiness",
                    "runtime_vector_backend_readiness",
                ):
                    if field in declared and _checksum(current.get(field) or {}) != _checksum(
                        declared.get(field) or {}
                    ):
                        reasons.append(f"{field}_mismatch:{version_id}")

        unique_reasons = list(dict.fromkeys(reasons))
        status = "current"
        if orphaned:
            status = "orphaned"
        elif unique_reasons:
            status = "unreproducible"
        return {
            "reproducibility_status": status,
            "reproducibility_reasons": unique_reasons,
            "reproducibility_evidence": actual,
        }

    def _update_run(self, run_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            run.update(values)
            run["updated_at"] = time.time()
            self._write_unlocked(data)
            return self.run_payload(run)

    def _normalize_case(self, raw: dict[str, Any], *, preserve_id: bool = False) -> dict[str, Any]:
        query = str(raw.get("query") or "").strip()
        if not query or len(query) > 20_000:
            raise ValueError("Evaluation query must contain between 1 and 20,000 characters.")
        expected_no_result = bool(raw.get("expected_no_result", False))
        references = raw.get("expected_refs")
        if not isinstance(references, list) or len(references) > 50:
            raise ValueError("Evaluation expected_refs must be a list with at most 50 items.")
        if expected_no_result and references:
            raise ValueError("No-result cases cannot define expected references.")
        if not expected_no_result and not references:
            raise ValueError("Answerable evaluation cases need at least one expected reference.")
        normalized_refs: list[dict[str, Any]] = []
        for reference in references:
            if not isinstance(reference, dict) or not str(reference.get("document_id") or "").strip():
                raise ValueError("Each expected reference needs a document_id.")
            relevance = int(reference.get("relevance", 1))
            if relevance < 1 or relevance > 3:
                raise ValueError("Expected reference relevance must be between 1 and 3.")
            anchor_start = _optional_int(reference.get("anchor_start"))
            anchor_end = _optional_int(reference.get("anchor_end"))
            anchor_hash = _optional_string(reference.get("anchor_hash"), 64)
            source_block_hash = _optional_string(
                reference.get("source_block_hash"), 64
            )
            has_anchor = any(
                value is not None
                for value in (anchor_start, anchor_end, anchor_hash, source_block_hash)
            )
            if has_anchor and (
                str(reference.get("match_mode") or "") != "source_block"
                or anchor_start is None
                or anchor_start < 0
                or anchor_end is None
                or anchor_end <= anchor_start
                or not anchor_hash
                or not re.fullmatch(r"[0-9a-f]{64}", anchor_hash)
                or not source_block_hash
                or not re.fullmatch(r"[0-9a-f]{64}", source_block_hash)
            ):
                raise ValueError(
                    "Anchor references require a source block, valid span, block hash, and anchor hash."
                )
            normalized_refs.append(
                {
                    "reference_id": str(reference.get("reference_id") or f"ref_{uuid.uuid4().hex}"),
                    "document_id": str(reference["document_id"]).strip()[:200],
                    "chunk_id": _optional_string(reference.get("chunk_id"), 240),
                    "source_block_id": _optional_string(reference.get("source_block_id"), 240),
                    "source_block_hash": source_block_hash,
                    "anchor_start": anchor_start,
                    "anchor_end": anchor_end,
                    "anchor_hash": anchor_hash,
                    "page_number": _optional_int(reference.get("page_number")),
                    "relevance": relevance,
                    "match_mode": _normalize_match_mode(reference),
                    "catalog_anchor_key": _optional_string(reference.get("catalog_anchor_key"), 200),
                }
            )
        normalized = {
            "case_id": str(raw.get("case_id")) if preserve_id and raw.get("case_id") else f"evalcase_{uuid.uuid4().hex}",
            "query": query,
            "expected_refs": normalized_refs,
            "expected_no_result": expected_no_result,
            "review_status": _normalize_review_status(
                raw.get("review_status"), expected_no_result=expected_no_result
            ),
            "tags": [str(item)[:80] for item in raw.get("tags", []) if str(item).strip()][:20],
            "notes": str(raw.get("notes") or "")[:1000],
            "targeting": _safe_targeting(raw.get("targeting")),
        }
        review_evidence = raw.get("review_evidence")
        if isinstance(review_evidence, dict):
            normalized["review_evidence"] = _copy(review_evidence)
        return normalized

    def _touch_set(self, item: dict[str, Any]) -> None:
        item["revision"] = int(item.get("revision", 0)) + 1
        item["updated_at"] = time.time()
        if str(item.get("origin") or "manual") == "generated":
            calibration = dict(item.get("calibration") or {})
            item["calibration"] = {
                **calibration,
                "status": "stale",
                "reason": "Evaluation set changed after calibration.",
                "dataset_revision": int(item["revision"]),
            }

    def _set_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        payload = _copy(item)
        payload.setdefault("origin", "manual")
        payload.setdefault("catalog_ref", {})
        payload.setdefault("provenance", {})
        payload.setdefault("coverage", {})
        payload.setdefault("calibration", {})
        payload.setdefault("latest_version", None)
        payload["benchmark_role"] = normalize_benchmark_role(
            payload.get("benchmark_role"),
            origin=str(payload.get("origin") or "manual"),
            catalog_ref=dict(payload.get("catalog_ref") or {}),
        )
        for case in payload.get("cases", []):
            if isinstance(case, dict):
                case.setdefault("expected_no_result", False)
                case.setdefault("review_status", "not_required")
                case.setdefault("targeting", {})
        return payload

    def _version_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        payload = _copy(item)
        payload["benchmark_role"] = normalize_benchmark_role(
            payload.get("benchmark_role"),
            origin=str(payload.get("origin") or "manual"),
            catalog_ref=dict(payload.get("catalog_ref") or {}),
        )
        return payload

    def _check_revision(self, item: dict[str, Any], expected_revision: int) -> None:
        if int(item.get("revision", 0)) != expected_revision:
            raise EvaluationRevisionError("Evaluation set revision is stale; reload before saving.")

    def _set_or_raise(self, data: dict[str, Any], eval_set_id: str) -> dict[str, Any]:
        item = data["sets"].get(eval_set_id)
        if not isinstance(item, dict):
            raise EvaluationSetNotFoundError("Knowledge evaluation set not found.")
        return item

    def _run_or_raise(self, data: dict[str, Any], run_id: str) -> dict[str, Any]:
        item = data["runs"].get(run_id)
        if not isinstance(item, dict):
            raise EvaluationRunNotFoundError("Knowledge evaluation run not found.")
        return item

    def _read(self) -> dict[str, Any]:
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": "knowledge-evaluation-v2", "sets": {}, "versions": {}, "runs": {}, "gate_policies": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        return {
            "version": "knowledge-evaluation-v2",
            "sets": value.get("sets") if isinstance(value.get("sets"), dict) else {},
            "versions": value.get("versions") if isinstance(value.get("versions"), dict) else {},
            "runs": value.get("runs") if isinstance(value.get("runs"), dict) else {},
            "gate_policies": value.get("gate_policies") if isinstance(value.get("gate_policies"), dict) else {},
        }

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def _source_matches_reference(source: dict[str, Any], reference: dict[str, Any]) -> bool:
    source_document = str(
        source.get("source_document_id")
        or source.get("doc_id")
        or source.get("document_id")
        or ""
    )
    if source_document != str(reference.get("document_id") or ""):
        return False
    match_mode = str(reference.get("match_mode") or "").strip()
    if match_mode == "document":
        return True
    if match_mode == "source_block":
        expected_block = str(reference.get("source_block_id") or "")
        if not expected_block or str(source.get("source_block_id") or "") != expected_block:
            return False
        anchor_hash = str(reference.get("anchor_hash") or "")
        if not anchor_hash:
            return True
        source_start = _optional_int(source.get("start_char"))
        source_end = _optional_int(source.get("end_char"))
        anchor_start = _optional_int(reference.get("anchor_start"))
        anchor_end = _optional_int(reference.get("anchor_end"))
        return (
            source_start is not None
            and source_end is not None
            and anchor_start is not None
            and anchor_end is not None
            and source_start <= anchor_start
            and source_end >= anchor_end
        )
    if match_mode == "chunk":
        expected_chunk = str(reference.get("chunk_id") or "")
        return bool(expected_chunk) and str(source.get("chunk_id") or "") == expected_chunk
    expected_chunk = str(reference.get("chunk_id") or "")
    if expected_chunk:
        return str(source.get("chunk_id") or "") == expected_chunk
    expected_block = str(reference.get("source_block_id") or "")
    if expected_block:
        return str(source.get("source_block_id") or "") == expected_block
    expected_page = reference.get("page_number")
    if expected_page is not None:
        return _optional_int(source.get("page_number")) == int(expected_page)
    return True


def _normalize_review_status(value: Any, *, expected_no_result: bool) -> str:
    status = str(value or ("pending" if expected_no_result else "not_required")).strip()
    allowed = {"not_required", "pending", "approved", "rejected"}
    if status not in allowed:
        raise ValueError("Evaluation case review_status is invalid.")
    return status


def _case_review_checksum(case: dict[str, Any]) -> str:
    return _checksum(
        {
            "case_id": str(case.get("case_id") or ""),
            "query": str(case.get("query") or ""),
            "expected_refs": case.get("expected_refs") or [],
            "expected_no_result": bool(case.get("expected_no_result")),
            "tags": case.get("tags") or [],
            "targeting": case.get("targeting") or {},
        }
    )


def _safe_targeting(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    evidence_ids = value.get("evidence_ids")
    context_refs = value.get("context_refs")
    verification = value.get("full_corpus_verification")
    verification = verification if isinstance(verification, dict) else {}
    top_matches = verification.get("top_matches")
    return {
        "blueprint_id": _optional_string(value.get("blueprint_id"), 160),
        "query_type": _optional_string(value.get("query_type"), 80),
        "locale": _optional_string(value.get("locale"), 16),
        "difficulty": _optional_string(value.get("difficulty"), 40),
        "evidence_ids": [
            str(item)[:160]
            for item in evidence_ids
            if str(item).strip()
        ][:3] if isinstance(evidence_ids, list) else [],
        "context_refs": [
            {
                "document_id": _optional_string(item.get("document_id"), 200),
                "chunk_id": _optional_string(item.get("chunk_id"), 240),
                "source_block_id": _optional_string(
                    item.get("source_block_id"), 240
                ),
                "source_block_hash": _optional_string(
                    item.get("source_block_hash"), 64
                ),
                "page_number": _optional_int(item.get("page_number")),
            }
            for item in context_refs
            if isinstance(item, dict)
            and item.get("document_id")
            and item.get("source_block_id")
        ][:3] if isinstance(context_refs, list) else [],
        "leakage_warning": (
            {
                "threshold": max(
                    1,
                    min(
                        int((value.get("leakage_warning") or {}).get("threshold") or 0),
                        32,
                    ),
                ),
                "reason_required": True,
            }
            if isinstance(value.get("leakage_warning"), dict)
            else None
        ),
        "full_corpus_verification": (
            {
                "contract_version": _optional_string(
                    verification.get("contract_version"), 80
                ),
                "completed": verification.get("completed") is True,
                "method": _optional_string(verification.get("method"), 80),
                "query_hash": _optional_string(verification.get("query_hash"), 64),
                "corpus_snapshot_checksum": _optional_string(
                    verification.get("corpus_snapshot_checksum"), 64
                ),
                "scanned_document_count": max(
                    0, int(verification.get("scanned_document_count") or 0)
                ),
                "scanned_source_block_count": max(
                    0, int(verification.get("scanned_source_block_count") or 0)
                ),
                "top_matches": [
                    {
                        "document_id": _optional_string(item.get("document_id"), 200),
                        "source_block_id": _optional_string(
                            item.get("source_block_id"), 240
                        ),
                        "source_block_hash": _optional_string(
                            item.get("source_block_hash"), 64
                        ),
                        "lexical_query_coverage": max(
                            0.0,
                            min(
                                float(item.get("lexical_query_coverage") or 0.0),
                                1.0,
                            ),
                        ),
                    }
                    for item in top_matches
                    if isinstance(item, dict)
                    and item.get("document_id")
                    and item.get("source_block_id")
                ][:5]
                if isinstance(top_matches, list)
                else [],
            }
            if verification
            else None
        ),
    }


def _formal_query_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", value.casefold()))


def _benchmark_normalized_query(value: Any) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", str(value or "").casefold())


def _qualification_anchor_checksum(cases: list[dict[str, Any]]) -> str:
    anchors = [
        {
            "case_id": str(case.get("case_id") or ""),
            "reference_id": str(reference.get("reference_id") or ""),
            "document_id": str(reference.get("document_id") or ""),
            "source_block_id": str(reference.get("source_block_id") or ""),
            "source_block_hash": str(reference.get("source_block_hash") or ""),
            "anchor_start": _optional_int(reference.get("anchor_start")),
            "anchor_end": _optional_int(reference.get("anchor_end")),
            "anchor_hash": str(reference.get("anchor_hash") or ""),
        }
        for case in cases
        if not case.get("expected_no_result")
        for reference in case.get("expected_refs") or []
        if isinstance(reference, dict)
    ]
    anchors.sort(key=lambda item: (item["case_id"], item["reference_id"]))
    return _checksum(anchors)


def _ndcg_at_k(
    ranking: list[dict[str, Any]],
    expected_refs: list[dict[str, Any]],
    k: int,
) -> float:
    dcg = sum(
        (2 ** int(item.get("relevance", 0)) - 1) / math.log2(rank + 1)
        for rank, item in enumerate(ranking[:k], start=1)
        if int(item.get("relevance", 0)) > 0
    )
    ideal = sorted((int(item.get("relevance", 1)) for item in expected_refs), reverse=True)[:k]
    idcg = sum((2**relevance - 1) / math.log2(rank + 1) for rank, relevance in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(percentile * len(values)) - 1))
    return values[index]


def _validate_gate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if policy.get("mode") not in {"advisory", "required"}:
        raise ValueError("Evaluation gate mode must be advisory or required.")
    bounded = [
        "min_recall_at_5",
        "max_mrr_regression",
        "max_citation_hit_regression",
        "max_citation_precision_at_5_regression",
        "max_no_result_increase",
        "min_no_result_accuracy",
        "min_citation_coverage",
        "max_paired_primary_regression",
        "paired_confidence_level",
    ]
    for key in bounded:
        value = float(policy[key])
        if value < 0 or value > 1:
            raise ValueError(f"{key} must be between 0 and 1.")
        policy[key] = value
    latency = float(policy["max_p95_latency_ratio"])
    if latency < 1 or latency > 10:
        raise ValueError("max_p95_latency_ratio must be between 1 and 10.")
    policy["max_p95_latency_ratio"] = latency
    absolute_latency = float(policy["max_p95_latency_ms"])
    if absolute_latency < 1 or absolute_latency > 120_000:
        raise ValueError("max_p95_latency_ms must be between 1 and 120000.")
    policy["max_p95_latency_ms"] = absolute_latency
    policy["require_comparable_corpus"] = bool(
        policy.get("require_comparable_corpus", True)
    )
    policy["require_zero_errors"] = bool(policy.get("require_zero_errors", True))
    return policy


def _compatible_gate_policy(values: dict[str, Any] | None) -> dict[str, Any]:
    supplied = dict(values or {})
    effective = {**DEFAULT_GATE_POLICY, **supplied}
    if (
        "max_citation_precision_at_5_regression" not in supplied
        and "max_citation_hit_regression" in supplied
    ):
        effective["max_citation_precision_at_5_regression"] = supplied[
            "max_citation_hit_regression"
        ]
    return effective


def _optional_string(value: Any, limit: int) -> str | None:
    clean = str(value or "").strip()
    return clean[:limit] if clean else None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _safe_retrieval_receipt(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "mode",
        "vector_weight",
        "fulltext_weight",
        "top_k",
        "score_threshold",
        "candidate_multiplier",
        "rerank_enabled",
        "rerank_provider",
        "rerank_model",
        "rerank_top_n",
        "rerank_provider_used",
        "rerank_model_used",
        "rerank_applied",
        "rerank_input_count",
        "rerank_output_count",
        "rerank_tail_dropped",
        "rerank_requested_input_count",
        "rerank_input_char_count",
        "rerank_candidate_limit",
        "rerank_input_char_limit",
        "rerank_timeout_budget_ms",
        "rerank_elapsed_ms",
        "rerank_attempted_provider",
        "rerank_attempted_model",
        "rerank_fallback_reason",
        "rerank_provider_target_used",
        "rerank_attempted_targets",
        "rerank_target_attempt_count",
        "threshold_score_domain",
        "threshold_contract_status",
        "embedding_provider",
        "embedding_model",
        "embedding_dimension",
        "embedding_space_fingerprint",
        "vector_candidate_count",
        "fulltext_candidate_count",
        "promotion_eligible",
    }
    receipt: dict[str, Any] = {}
    for key in allowed:
        item = value.get(key)
        if isinstance(item, (str, int, float, bool, type(None))):
            receipt[key] = item
    reasons = value.get("promotion_ineligibility_reasons")
    if isinstance(reasons, list):
        receipt["promotion_ineligibility_reasons"] = [
            str(item)[:160] for item in reasons[:20] if str(item)
        ]
    attempted_targets = value.get("rerank_attempted_targets")
    if isinstance(attempted_targets, list):
        receipt["rerank_attempted_targets"] = [
            str(item)[:160] for item in attempted_targets[:10] if str(item)
        ]
    return receipt


def safe_provider_route_receipts(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Keep replayable Provider identity/usage fields without payloads or secrets."""

    if not isinstance(value, dict):
        return None
    receipt: dict[str, Any] = {}
    for key in (
        "contract_version",
        "entry_id",
        "routing_mode",
        "status",
    ):
        item = value.get(key)
        if isinstance(item, (str, int, float, bool, type(None))):
            receipt[key] = item
    call_count = value.get("call_count")
    if (
        isinstance(call_count, int)
        and not isinstance(call_count, bool)
        and call_count >= 0
    ):
        receipt["call_count"] = call_count
    reason_codes = value.get("reason_codes")
    if isinstance(reason_codes, list) and all(
        isinstance(item, str) for item in reason_codes
    ):
        receipt["reason_codes"] = [
            item[:160] for item in reason_codes[:20] if item
        ]
    safe_calls: list[dict[str, Any]] = []
    calls = value.get("calls")
    if isinstance(calls, list):
        for raw in calls[:20]:
            if not isinstance(raw, dict):
                continue
            call: dict[str, Any] = {}
            for key in (
                "call_sequence",
                "operation",
                "model_id",
                "provider_kind",
                "access_mode",
                "dispatched",
                "status",
                "actual_model",
                "error_code",
                "e2e_ms",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                item = raw.get(key)
                if isinstance(item, (str, int, float, bool, type(None))):
                    call[key] = item
            safe_calls.append(call)
    receipt["calls"] = safe_calls
    return receipt


def _ranking_item(source: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "chunk_id": str(source.get("chunk_id") or ""),
        "document_id": str(
            source.get("source_document_id")
            or source.get("doc_id")
            or source.get("document_id")
            or ""
        ),
        "document_name": str(source.get("document_name") or "")[:240],
        "source_block_id": source.get("source_block_id"),
        "start_char": _optional_int(source.get("start_char")),
        "end_char": _optional_int(source.get("end_char")),
        "page_number": source.get("page_number"),
        "visual_kind": source.get("visual_kind"),
        "score": _float_or_none(source.get("score")),
        "vector_score": _float_or_none(source.get("vector_score")),
        "fulltext_score": _float_or_none(source.get("fulltext_score")),
        "fused_score": _float_or_none(source.get("fused_score")),
        "rerank_score": _float_or_none(source.get("rerank_score")),
        "relevance": 0,
        "matched_reference_id": None,
    }


def _normalize_match_mode(reference: dict[str, Any]) -> str | None:
    value = str(reference.get("match_mode") or "").strip()
    if value:
        if value not in {"document", "source_block", "chunk"}:
            raise ValueError("Reference match_mode must be document, source_block, or chunk.")
        required = {
            "chunk": "chunk_id",
            "source_block": "source_block_id",
        }.get(value)
        if required and not str(reference.get(required) or "").strip():
            raise ValueError(f"Reference match_mode={value} requires {required}.")
        return value
    return None


def _checksum(value: Any) -> str:
    import hashlib

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_execution_integrity_checksum(run: dict[str, Any]) -> str:
    return _checksum(
        {
            "run_id": run.get("run_id"),
            "kb_id": run.get("kb_id"),
            "eval_set_id": run.get("eval_set_id"),
            "eval_set_revision": run.get("eval_set_revision"),
            "eval_set_version_id": run.get("eval_set_version_id"),
            "run_mode": run.get("run_mode"),
            "metric_contract_version": run.get("metric_contract_version"),
            "ks": run.get("ks") or [],
            "gate_policy": run.get("gate_policy") or {},
            "targets": run.get("targets") or [],
            "eval_set_snapshot": run.get("eval_set_snapshot") or {},
            "baseline_version_id": run.get("baseline_version_id"),
            "comparability": run.get("comparability") or {},
            "execution_manifest": run.get("execution_manifest") or {},
            "case_results": run.get("case_results") or {},
            "target_results": run.get("target_results") or [],
            "paired_confidence": run.get("paired_confidence") or {},
        }
    )


def _published_gold_checksum(snapshot: dict[str, Any]) -> str:
    return _checksum(
        {
            "benchmark_contract_version": snapshot.get(
                "benchmark_contract_version"
            ),
            "cases": snapshot.get("cases") or [],
            "coverage": snapshot.get("coverage") or {},
            "provenance": snapshot.get("provenance") or {},
            "calibration": snapshot.get("calibration") or {},
            "corpus_snapshot": snapshot.get("corpus_snapshot") or {},
            "qualification_manifest": snapshot.get("qualification_manifest") or {},
        }
    )


def published_gold_checksum_valid(snapshot: dict[str, Any]) -> bool:
    return hmac.compare_digest(
        str(snapshot.get("checksum") or ""), _published_gold_checksum(snapshot)
    )


def _safe_error(value: Any) -> str:
    return str(value or "Evaluation failed.").strip()[:500]


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
