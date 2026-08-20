from __future__ import annotations

import json
import hashlib
import math
import os
import random
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

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
GOLD_CONTRACT_V2 = "rag-gold-v2"
GOLD_V2_POSITIVE_TYPES = (
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
    for ranking_k in sorted({10, max_k}):
        metrics[f"mrr_at_{ranking_k}"] = (
            1.0 / first_rank if first_rank and first_rank <= ranking_k else 0.0
        )
        metrics[f"ndcg_at_{ranking_k}"] = _ndcg_at_k(
            ranking, expected_refs, ranking_k
        )
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
    positive = [item for item in case_results if not item.get("expected_no_result")]
    no_result_cases = [item for item in case_results if item.get("expected_no_result")]
    errors = [item for item in case_results if item.get("status") == "failed"]
    ranking_metric_ks = sorted({10, max(normalized_ks, default=10)})
    metric_names = [
        *(f"hit_at_{k}" for k in normalized_ks),
        *(f"recall_at_{k}" for k in normalized_ks),
        *(f"mrr_at_{k}" for k in ranking_metric_ks),
        *(f"ndcg_at_{k}" for k in ranking_metric_ks),
        "citation_hit_rate",
        "citation_precision_at_5",
        "citation_coverage",
    ]
    metrics = {
        name: round(
            sum(float(item.get("metrics", {}).get(name, 0.0)) for item in positive)
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
            "failed_case_count": len(errors),
            "error_count": len(errors),
            "positive_case_count": len(positive),
            "no_result_case_count": len(no_result_cases),
            "positive_quality_denominator": len(positive),
            "no_result_quality_denominator": len(no_result_cases),
            "quality_denominator_count": len(case_results),
            "no_result_accuracy": round(
                sum(float(item.get("metrics", {}).get("no_result_accuracy", 0.0)) for item in no_result_cases)
                / len(no_result_cases),
                6,
            ) if no_result_cases else 1.0,
            "false_positive_rate": round(
                sum(float(item.get("metrics", {}).get("false_positive_rate", 0.0)) for item in no_result_cases)
                / len(no_result_cases),
                6,
            ) if no_result_cases else 0.0,
            "no_result_rate": round(
                sum(1 for item in case_results if item.get("no_result")) / len(case_results), 6
            )
            if case_results
            else 1.0,
            "positive_no_result_rate": round(
                sum(1 for item in positive if item.get("no_result")) / len(positive), 6
            )
            if positive
            else 0.0,
            "warning_rate": round(
                sum(1 for item in case_results if int(item.get("warning_count", 0)) > 0)
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


def build_paired_execution_schedule(
    cases: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    *,
    seed: int,
) -> list[dict[str, str]]:
    """Build a deterministic case-interleaved schedule from SHA-256 ranks."""

    def rank(*parts: str) -> str:
        payload = "\0".join((str(seed), *parts)).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    ordered_cases = sorted(
        (str(case.get("case_id") or "") for case in cases),
        key=lambda case_id: (rank("case", case_id), case_id),
    )
    target_ids = [str(target.get("target_id") or "") for target in targets]
    schedule: list[dict[str, str]] = []
    for case_id in ordered_cases:
        ordered_targets = sorted(
            target_ids,
            key=lambda target_id: (
                rank("target", case_id, target_id),
                target_id,
            ),
        )
        schedule.extend(
            {"case_id": case_id, "target_id": target_id}
            for target_id in ordered_targets
        )
    return schedule


def paired_primary_confidence_report(
    cases: list[dict[str, Any]],
    baseline_results: dict[str, dict[str, Any]],
    candidate_results: dict[str, dict[str, Any]],
    *,
    seed: int,
    iterations: int = 10_000,
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    """Compute a deterministic stratified paired bootstrap for primary quality."""

    bounded_iterations = max(1, min(int(iterations), 100_000))
    bounded_confidence = max(0.5, min(float(confidence_level), 0.999))
    strata: dict[str, list[float]] = {}
    for case in cases:
        case_id = str(case.get("case_id") or "")
        expected_no_result = bool(case.get("expected_no_result"))
        metric_name = "no_result_accuracy" if expected_no_result else "recall_at_5"
        locale = str((case.get("targeting") or {}).get("locale") or "unknown")
        stratum = f"{'negative' if expected_no_result else 'positive'}:{locale}"

        def score(result: dict[str, Any] | None) -> float:
            if not isinstance(result, dict) or result.get("status") != "completed":
                return 0.0
            return float((result.get("metrics") or {}).get(metric_name, 0.0))

        difference = score(candidate_results.get(case_id)) - score(
            baseline_results.get(case_id)
        )
        strata.setdefault(stratum, []).append(difference)

    differences = [value for values in strata.values() for value in values]
    point_estimate = sum(differences) / len(differences) if differences else 0.0
    random_seed = int(
        hashlib.sha256(f"paired-bootstrap-v1:{seed}".encode("utf-8")).hexdigest()[:16],
        16,
    )
    rng = random.Random(random_seed)
    samples: list[float] = []
    ordered_strata = [strata[key] for key in sorted(strata)]
    for _ in range(bounded_iterations):
        sampled = [
            values[rng.randrange(len(values))]
            for values in ordered_strata
            for _ in range(len(values))
        ]
        samples.append(sum(sampled) / len(sampled) if sampled else 0.0)
    samples.sort()
    tail = (1.0 - bounded_confidence) / 2.0
    lower_index = max(0, min(len(samples) - 1, math.floor(tail * len(samples))))
    upper_index = max(
        0,
        min(len(samples) - 1, math.ceil((1.0 - tail) * len(samples)) - 1),
    )
    return {
        "version": "rag-paired-bootstrap-v1",
        "seed": int(seed),
        "iterations": bounded_iterations,
        "confidence_level": bounded_confidence,
        "point_estimate": round(point_estimate, 6),
        "ci_lower": round(samples[lower_index], 6) if samples else 0.0,
        "ci_upper": round(samples[upper_index], 6) if samples else 0.0,
        "case_count": len(differences),
        "strata": {key: len(values) for key, values in sorted(strata.items())},
    }


def evaluate_promotion_gate(
    candidate: dict[str, Any],
    *,
    baseline: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
    evidence_qualification: dict[str, Any] | None = None,
    paired_confidence: dict[str, Any] | None = None,
    comparability: dict[str, Any] | None = None,
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

    if paired_confidence is not None:
        ci_lower = float(paired_confidence.get("ci_lower", -1.0))
        tolerance = float(effective["max_paired_primary_regression"])
        checks.append(
            {
                "id": "paired_primary_non_inferiority",
                "passed": ci_lower >= -tolerance,
                "actual": round(ci_lower, 6),
                "threshold": round(-tolerance, 6),
                "confidence_level": float(
                    paired_confidence.get("confidence_level")
                    or effective["paired_confidence_level"]
                ),
                "point_estimate": float(
                    paired_confidence.get("point_estimate") or 0.0
                ),
                "message": "Paired primary-score confidence lower bound must remain within tolerance.",
            }
        )

    if comparability is not None:
        comparable = bool(comparability.get("comparable"))
        checks.append(
            {
                "id": "comparable_corpus",
                "passed": comparable,
                "actual": 1.0 if comparable else 0.0,
                "threshold": 1.0,
                "corpus_snapshot_hash": comparability.get(
                    "corpus_snapshot_hash"
                ),
                "reasons": _copy(comparability.get("reasons") or []),
                "message": "Formal promotion evidence must compare the same corpus snapshot.",
            }
        )

    return {
        "passed": all(item["passed"] for item in checks),
        "mode": str(effective["mode"]),
        "checks": checks,
    }


def _gold_contract_version(snapshot: dict[str, Any]) -> str:
    provenance = dict(snapshot.get("provenance") or {})
    return str(
        snapshot.get("benchmark_contract_version")
        or provenance.get("benchmark_contract_version")
        or ""
    )


def _gold_v2_quality_manifest(snapshot: dict[str, Any]) -> dict[str, Any]:
    cases = [item for item in snapshot.get("cases") or [] if isinstance(item, dict)]
    positives = [item for item in cases if not item.get("expected_no_result")]
    negatives = [item for item in cases if item.get("expected_no_result")]
    provenance = dict(snapshot.get("provenance") or {})
    corpus_snapshot = dict(
        snapshot.get("corpus_snapshot") or provenance.get("corpus_snapshot") or {}
    )
    expected_corpus_hash = str(
        snapshot.get("corpus_snapshot_hash")
        or provenance.get("corpus_snapshot_hash")
        or ""
    )
    revision = _optional_int(
        snapshot.get("source_revision") or snapshot.get("revision")
    ) or 0
    checks: list[dict[str, Any]] = []

    def add(check_id: str, passed: bool, actual: Any, required: Any) -> None:
        checks.append(
            {
                "id": check_id,
                "passed": bool(passed),
                "actual": actual,
                "required": required,
            }
        )

    add("contract_version", _gold_contract_version(snapshot) == GOLD_CONTRACT_V2, _gold_contract_version(snapshot), GOLD_CONTRACT_V2)
    add("case_count", len(cases) == 42, len(cases), 42)
    add("positive_count", len(positives) == 30, len(positives), 30)
    add("hard_negative_count", len(negatives) == 12, len(negatives), 12)

    valid_reviews = 0
    leakage_warning_reasons = 0
    for case in cases:
        review = dict(case.get("review_evidence") or {})
        leakage = dict((case.get("targeting") or {}).get("leakage") or {})
        reason = str(review.get("reason") or "").strip()
        reviewed_at = _float_or_none(review.get("reviewed_at")) or 0.0
        review_revision = _optional_int(review.get("dataset_revision")) or 0
        valid = (
            str(case.get("review_status") or "") == "approved"
            and str(review.get("source") or "") == "manual_ui"
            and str(review.get("decision") or "") == "approved"
            and reviewed_at > 0
            and 0 < review_revision <= revision
        )
        if leakage.get("warning"):
            valid = valid and bool(reason)
            if reason:
                leakage_warning_reasons += 1
        valid_reviews += int(valid)
    add("manual_reviews", valid_reviews == len(cases), valid_reviews, len(cases))
    leakage_warnings = sum(
        1
        for case in cases
        if dict((case.get("targeting") or {}).get("leakage") or {}).get("warning")
    )
    add(
        "leakage_warning_reasons",
        leakage_warning_reasons == leakage_warnings,
        leakage_warning_reasons,
        leakage_warnings,
    )
    blocked_leakage = sum(
        1
        for case in positives
        if dict((case.get("targeting") or {}).get("leakage") or {}).get("blocked")
        or (
            _optional_int(
                dict((case.get("targeting") or {}).get("leakage") or {}).get(
                    "max_normalized_copy"
                )
            )
            or 0
        )
        >= 32
    )
    add("no_blocking_leakage", blocked_leakage == 0, blocked_leakage, 0)

    query_types = Counter(
        str((case.get("targeting") or {}).get("query_type") or "")
        for case in positives
    )
    add(
        "positive_type_balance",
        all(query_types.get(value, 0) == 5 for value in GOLD_V2_POSITIVE_TYPES)
        and sum(query_types.values()) == 30,
        dict(sorted(query_types.items())),
        {value: 5 for value in GOLD_V2_POSITIVE_TYPES},
    )
    positive_locales = Counter(
        str((case.get("targeting") or {}).get("locale") or "")
        for case in positives
    )
    negative_locales = Counter(
        str((case.get("targeting") or {}).get("locale") or "")
        for case in negatives
    )
    add("positive_locale_balance", positive_locales == {"zh-CN": 15, "en-US": 15}, dict(positive_locales), {"zh-CN": 15, "en-US": 15})
    add("negative_locale_balance", negative_locales == {"zh-CN": 6, "en-US": 6}, dict(negative_locales), {"zh-CN": 6, "en-US": 6})

    document_ids = {
        str(item.get("document_id") or "")
        for item in corpus_snapshot.get("documents") or []
        if isinstance(item, dict) and item.get("document_id")
    }
    source_blocks = {
        (str(item.get("document_id") or ""), str(item.get("source_block_id") or ""))
        for item in corpus_snapshot.get("source_blocks") or []
        if isinstance(item, dict) and item.get("document_id") and item.get("source_block_id")
    }
    content_hashes = [
        str(item.get("content_hash") or "")
        for item in [
            *(corpus_snapshot.get("documents") or []),
            *(corpus_snapshot.get("source_blocks") or []),
        ]
        if isinstance(item, dict)
    ]
    add(
        "corpus_content_hashes",
        bool(content_hashes)
        and all(
            len(value) == 64
            and all(character in "0123456789abcdef" for character in value.casefold())
            for value in content_hashes
        ),
        len([value for value in content_hashes if len(value) == 64]),
        len(content_hashes),
    )
    positive_block_counts: Counter[tuple[str, str]] = Counter()
    positive_document_counts: Counter[str] = Counter()
    stable_positive = 0
    for case in positives:
        refs = [item for item in case.get("expected_refs") or [] if isinstance(item, dict)]
        stable = bool(refs)
        seen_documents: set[str] = set()
        for ref in refs:
            key = (str(ref.get("document_id") or ""), str(ref.get("source_block_id") or ""))
            stable = stable and str(ref.get("match_mode") or "") == "source_block" and key in source_blocks
            positive_block_counts[key] += 1
            seen_documents.add(key[0])
        for document_id in seen_documents:
            positive_document_counts[document_id] += 1
        stable_positive += int(stable)
    add("stable_source_block_gold", stable_positive == len(positives), stable_positive, len(positives))
    add("document_coverage", set(positive_document_counts) == document_ids, sorted(positive_document_counts), sorted(document_ids))
    max_document_share = max(positive_document_counts.values(), default=0) / max(1, len(positives))
    add("max_document_share", max_document_share <= 0.4, round(max_document_share, 6), 0.4)
    max_block_reuse = max(positive_block_counts.values(), default=0)
    add("source_block_reuse", max_block_reuse <= 2, max_block_reuse, 2)

    negative_context_blocks: list[tuple[str, str]] = []
    qualified_negatives = 0
    for case in negatives:
        tags = {str(item) for item in case.get("tags") or []}
        contexts = [
            item
            for item in (case.get("targeting") or {}).get("context_refs") or []
            if isinstance(item, dict)
        ]
        valid_contexts = [
            (str(item.get("document_id") or ""), str(item.get("source_block_id") or ""))
            for item in contexts
            if (str(item.get("document_id") or ""), str(item.get("source_block_id") or "")) in source_blocks
        ]
        if {"corpus_near", "hard_negative"}.issubset(tags) and valid_contexts:
            qualified_negatives += 1
            negative_context_blocks.append(valid_contexts[0])
    add("qualified_hard_negatives", qualified_negatives == 12, qualified_negatives, 12)
    add("unique_negative_contexts", len(set(negative_context_blocks)) == 12, len(set(negative_context_blocks)), 12)

    normalized_queries = [_normalized_query(str(case.get("query") or "")) for case in cases]
    add("unique_queries", len(set(normalized_queries)) == len(cases), len(set(normalized_queries)), len(cases))
    token_sets = [_query_tokens(str(case.get("query") or "")) for case in cases]
    near_duplicates = 0
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1 :]:
            union = left | right
            if union and len(left & right) / len(union) >= 0.8:
                near_duplicates += 1
    add("no_near_duplicate_queries", near_duplicates == 0, near_duplicates, 0)

    provenance_required = (
        "generator",
        "generator_model_id",
        "seed",
        "generation_prompt_hash",
        "evidence_hash",
        "blueprint_hash",
        "generation_attempts",
        "source_pipeline_version_id",
    )
    missing_provenance = [key for key in provenance_required if provenance.get(key) in (None, "", [])]
    add("complete_provenance", not missing_provenance, missing_provenance, [])
    actual_corpus_hash = _checksum(corpus_snapshot) if corpus_snapshot else ""
    add("corpus_snapshot_hash", bool(corpus_snapshot) and actual_corpus_hash == expected_corpus_hash, actual_corpus_hash or None, expected_corpus_hash or "valid hash")

    return {
        "version": "rag-gold-v2-qualification-v1",
        "qualified": all(check["passed"] for check in checks),
        "checks": checks,
        "counts": {
            "total": len(cases),
            "positive": len(positives),
            "hard_negative": len(negatives),
            "reviewed": valid_reviews,
        },
    }


def _gold_v2_checksum_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark_contract_version": _gold_contract_version(snapshot),
        "cases": snapshot.get("cases") or [],
        "provenance": snapshot.get("provenance") or {},
        "coverage": snapshot.get("coverage") or {},
        "calibration": snapshot.get("calibration") or {},
        "corpus_snapshot": snapshot.get("corpus_snapshot") or {},
        "qualification_manifest": snapshot.get("qualification_manifest") or {},
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
    contract_version = _gold_contract_version(snapshot)
    quality_manifest = _gold_v2_quality_manifest(snapshot)
    stored_manifest = dict(snapshot.get("qualification_manifest") or {})
    published_checksum = str(snapshot.get("checksum") or "")
    checksum_valid = bool(published_checksum) and published_checksum == _checksum(
        _gold_v2_checksum_payload(snapshot)
    )
    checks = [
        {
            "id": "gold_contract_v2",
            "passed": contract_version == GOLD_CONTRACT_V2,
            "actual": contract_version or "legacy",
            "required": GOLD_CONTRACT_V2,
        },
        {
            "id": "published_checksum",
            "passed": checksum_valid,
            "actual": checksum_valid,
            "required": True,
        },
        {
            "id": "qualification_manifest",
            "passed": bool(quality_manifest.get("qualified"))
            and stored_manifest == quality_manifest,
            "actual": bool(quality_manifest.get("qualified"))
            and stored_manifest == quality_manifest,
            "required": True,
        },
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
        "version": "rag-promotion-evidence-v2",
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


class KnowledgeEvaluationStore:
    """File-backed evaluation datasets, runs, and promotion policies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

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
                is_gold_v2 = _gold_contract_version(item) == GOLD_CONTRACT_V2
                if int(calibration.get("dataset_revision") or 0) != int(
                    item.get("revision") or 0
                ):
                    raise EvaluationStateError(
                        "Generated evaluation set validation revision is stale."
                    )
                allowed_calibration_statuses = (
                    {"not_required", "calibrated", "warning"}
                    if is_gold_v2
                    else {"calibrated", "warning"}
                )
                if calibration_status not in allowed_calibration_statuses:
                    raise EvaluationStateError(
                        (
                            "rag-gold-v2 uses structural validation before its single Formal run."
                            if is_gold_v2
                            else "Generated evaluation set must complete calibration before publishing."
                        )
                    )
                if calibration_status == "warning" and not acknowledge_calibration_warnings:
                    raise EvaluationStateError(
                        "Calibration warnings must be explicitly acknowledged before publishing."
                    )
                if is_gold_v2:
                    unreviewed = [
                        case
                        for case in item.get("cases", [])
                        if str(case.get("review_status") or "pending") != "approved"
                    ]
                    if unreviewed:
                        raise EvaluationStateError(
                            "All 42 cases require explicit review before rag-gold-v2 can be published."
                        )
                    qualification_manifest = _gold_v2_quality_manifest(item)
                    if not qualification_manifest["qualified"]:
                        failed = [
                            check["id"]
                            for check in qualification_manifest["checks"]
                            if not check["passed"]
                        ]
                        raise EvaluationStateError(
                            "rag-gold-v2 qualification failed: " + ", ".join(failed)
                        )
                else:
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
            else:
                qualification_manifest = {}
            cases = [self._normalize_case(case, preserve_id=True) for case in item["cases"]]
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
                "benchmark_role": normalize_benchmark_role(
                    item.get("benchmark_role"),
                    origin=str(item.get("origin") or "manual"),
                    catalog_ref=dict(item.get("catalog_ref") or {}),
                ),
                "release_notes": str(release_notes or "")[:1000],
                "published_at": now,
            }
            if _gold_contract_version(item) == GOLD_CONTRACT_V2:
                provenance = dict(item.get("provenance") or {})
                version.update(
                    {
                        "benchmark_contract_version": GOLD_CONTRACT_V2,
                        "corpus_snapshot": _copy(provenance.get("corpus_snapshot") or {}),
                        "corpus_snapshot_hash": str(
                            provenance.get("corpus_snapshot_hash") or ""
                        ),
                        "qualification_manifest": _copy(qualification_manifest),
                    }
                )
                version["checksum"] = _checksum(_gold_v2_checksum_payload(version))
            else:
                version["checksum"] = _checksum(
                    {"cases": cases, "coverage": item.get("coverage") or {}}
                )
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
            semantic_fields = {"query", "expected_refs", "expected_no_result", "tags", "targeting"}
            semantic_change = any(
                key in values and values.get(key) != item["cases"][index].get(key)
                for key in semantic_fields
            )
            merged = {**item["cases"][index], **values, "case_id": case_id}
            if semantic_change and _gold_contract_version(item) == GOLD_CONTRACT_V2:
                merged["review_status"] = "pending"
                merged["review_evidence"] = {}
            item["cases"][index] = self._normalize_case(merged, preserve_id=True)
            self._touch_set(item)
            self._write_unlocked(data)
            return _copy(item)

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
        execution_seed: int = 0,
        run_mode: str = "diagnostic",
        metric_contract_version: str = "legacy",
        execution_manifest: dict[str, Any] | None = None,
        comparability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        normalized_mode = str(run_mode or "diagnostic")
        if normalized_mode not in {"diagnostic", "formal"}:
            raise EvaluationStateError("Evaluation run_mode must be diagnostic or formal.")
        normalized_metric_contract = str(metric_contract_version or "legacy")
        normalized_manifest = _copy(execution_manifest or {})
        normalized_comparability = _copy(
            comparability
            or {
                "comparable": False,
                "reasons": ["Diagnostic or legacy run has no formal comparability proof."],
            }
        )
        if normalized_mode == "formal":
            if case_ids is not None:
                raise EvaluationStateError("Formal evaluation does not allow case subsets.")
            if evaluation_set_version is None:
                raise EvaluationStateError("Formal evaluation requires a published Gold version.")
            qualification = qualify_promotion_evidence(evaluation_set_version)
            if not qualification.get("qualified"):
                raise EvaluationStateError(
                    "Formal evaluation requires a qualified published rag-gold-v2 version."
                )
            if len(targets) != 2 or not baseline_version_id:
                raise EvaluationStateError(
                    "Formal evaluation requires exactly one baseline and one candidate."
                )
            version_ids = {str(target.get("version_id") or "") for target in targets}
            if baseline_version_id not in version_ids or len(version_ids) != 2:
                raise EvaluationStateError(
                    "Formal evaluation baseline must identify one of two distinct targets."
                )
            if normalized_metric_contract != "rag-eval-v2":
                raise EvaluationStateError("Formal evaluation requires metric contract rag-eval-v2.")
            required_manifest = {
                "version",
                "evaluation_set_checksum",
                "corpus_snapshot_hash",
                "target_fingerprints",
                "execution_seed",
                "order_algorithm",
                "schedule_checksum",
                "threshold_score_domain",
                "retry_policy",
                "warmup_policy",
            }
            if (
                required_manifest - set(normalized_manifest)
                or normalized_manifest.get("version") != "rag-eval-v2"
                or normalized_manifest.get("evaluation_set_checksum")
                != evaluation_set_version.get("checksum")
                or normalized_manifest.get("execution_seed") != int(execution_seed)
                or normalized_manifest.get("order_algorithm")
                != "sha256-paired-interleave-v1"
                or normalized_manifest.get("threshold_score_domain") != "fused_score"
                or normalized_manifest.get("retry_policy") != "none"
                or normalized_manifest.get("warmup_policy") != "none"
            ):
                raise EvaluationStateError(
                    "Formal evaluation execution manifest is incomplete or inconsistent."
                )
            fingerprints = normalized_manifest.get("target_fingerprints")
            target_by_version = {
                str(target.get("version_id") or ""): target for target in targets
            }
            if (
                not isinstance(fingerprints, list)
                or len(fingerprints) != 2
                or {
                    str(item.get("version_id") or "")
                    for item in fingerprints
                    if isinstance(item, dict)
                    and item.get("version_fingerprint")
                }
                != version_ids
            ):
                raise EvaluationStateError(
                    "Formal evaluation requires complete fingerprints for both targets."
                )
            corpus_hash = str(evaluation_set_version.get("corpus_snapshot_hash") or "")
            for fingerprint in fingerprints:
                version_id = str(fingerprint.get("version_id") or "")
                target = target_by_version.get(version_id) or {}
                evidence = dict(target.get("version_evidence") or {})
                embedding = dict(fingerprint.get("embedding") or {})
                effective_embedding = dict(embedding.get("effective") or {})
                complete = (
                    len(str(fingerprint.get("version_fingerprint") or "")) == 64
                    and len(str(fingerprint.get("configuration_fingerprint") or ""))
                    == 64
                    and isinstance(fingerprint.get("processor"), dict)
                    and isinstance(fingerprint.get("retrieval"), dict)
                    and bool(fingerprint.get("retrieval"))
                    and str(effective_embedding.get("provider") or "")
                    and str(effective_embedding.get("model") or "")
                    and int(effective_embedding.get("dimension") or 0) > 0
                )
                consistent = all(
                    fingerprint.get(key) == evidence.get(key)
                    for key in (
                        "version_fingerprint",
                        "configuration_fingerprint",
                        "processor",
                        "retrieval",
                        "embedding",
                    )
                )
                if not complete or not consistent:
                    raise EvaluationStateError(
                        "Formal evaluation requires complete, target-backed fingerprints."
                    )
                if (
                    str(fingerprint.get("corpus_snapshot_hash") or "") != corpus_hash
                    or str(target.get("corpus_snapshot_hash") or "") != corpus_hash
                ):
                    raise EvaluationStateError(
                        "Formal evaluation target fingerprint corpus does not match Gold."
                    )
            expected_schedule_checksum = _checksum(
                build_paired_execution_schedule(
                    list(evaluation_set_version.get("cases") or []),
                    targets,
                    seed=int(execution_seed),
                )
            )
            if (
                str(normalized_manifest.get("schedule_checksum") or "")
                != expected_schedule_checksum
            ):
                raise EvaluationStateError(
                    "Formal evaluation execution manifest schedule is inconsistent."
                )
            if not normalized_comparability.get("comparable"):
                raise EvaluationStateError(
                    "Formal evaluation requires a comparable corpus snapshot."
                )
            if (
                str(normalized_comparability.get("corpus_snapshot_hash") or "")
                != corpus_hash
                or str(normalized_manifest.get("corpus_snapshot_hash") or "")
                != corpus_hash
            ):
                raise EvaluationStateError(
                    "Formal evaluation comparable corpus hash does not match Gold."
                )
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
            "evidence_qualification": qualify_promotion_evidence(snapshot),
            "run_mode": normalized_mode,
            "metric_contract_version": normalized_metric_contract,
            "execution_manifest": normalized_manifest,
            "comparability": normalized_comparability,
            "case_ids": [
                str(case.get("case_id") or "") for case in snapshot.get("cases", [])
            ],
            "targets": _copy(targets),
            "execution_seed": int(execution_seed),
            "execution_schedule": build_paired_execution_schedule(
                list(snapshot.get("cases") or []),
                targets,
                seed=int(execution_seed),
            ),
            "baseline_version_id": baseline_version_id,
            "ks": sorted(set(ks)),
            "gate_policy": _copy(gate_policy),
            "status": "queued",
            "progress": 0,
            "cancel_requested": False,
            "case_results": {},
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
        return [self.run_payload(item, include_cases=False) for item in runs[: max(1, limit)]]

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._read()["runs"].get(run_id)
        if not isinstance(run, dict):
            raise EvaluationRunNotFoundError("Knowledge evaluation run not found.")
        return self.run_payload(run)

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
                    run["status"] = "queued"
                    run["updated_at"] = time.time()
                    recovered += 1
            if recovered:
                self._write_unlocked(data)
        return recovered

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
    ) -> dict[str, Any]:
        return self._update_run(
            run_id,
            {
                "status": "succeeded",
                "progress": 100,
                "target_results": _copy(target_results),
                "completed_at": time.time(),
                "error": None,
            },
        )

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
            or str(run.get("metric_contract_version") or "legacy") != "rag-eval-v2"
            or not bool((run.get("comparability") or {}).get("comparable"))
        ):
            raise EvaluationPromotionError(
                "Candidate activation requires a comparable formal rag-eval-v2 run."
            )
        if run.get("eval_set_version") is None:
            current_set = self.get_set(str(run["eval_set_id"]))
            if int(current_set["revision"]) != int(run["eval_set_revision"]):
                raise EvaluationPromotionError("Evaluation set changed after this run; run it again before promotion.")
        if version_id == str(run.get("baseline_version_id") or ""):
            raise EvaluationPromotionError(
                "Promotion must select the formal candidate, not the baseline."
            )
        target = next(
            (item for item in run["target_results"] if item.get("version_id") == version_id),
            None,
        )
        if not isinstance(target, dict):
            raise EvaluationPromotionError("Evaluation run does not contain the requested version.")
        if int((target.get("metrics") or {}).get("error_count") or 0) != 0:
            raise EvaluationPromotionError(
                "Candidate activation requires zero formal evaluation errors."
            )
        metrics = dict(target.get("metrics") or {})
        expected_case_count = len(run.get("case_ids") or [])
        if (
            expected_case_count != 42
            or int(metrics.get("expected_case_count") or 0) != expected_case_count
            or int(metrics.get("completed_case_count") or 0) != expected_case_count
            or int(metrics.get("failed_case_count") or 0) != 0
        ):
            raise EvaluationPromotionError(
                "Candidate activation requires all 42 formal cases to complete exactly once."
            )
        if (
            int(metrics.get("positive_quality_denominator") or 0) != 30
            or int(metrics.get("no_result_quality_denominator") or 0) != 12
        ):
            raise EvaluationPromotionError(
                "Candidate activation requires fixed 30/12 formal quality denominators."
            )
        gate = target.get("promotion_gate") or {}
        if not bool(gate.get("passed")):
            raise EvaluationPromotionError("Candidate version did not pass the promotion gate.")
        return target

    def run_payload(self, run: dict[str, Any], *, include_cases: bool = True) -> dict[str, Any]:
        payload = _copy(run)
        payload.setdefault("run_mode", "diagnostic")
        payload.setdefault("metric_contract_version", "legacy")
        payload.setdefault("execution_manifest", {})
        payload.setdefault(
            "comparability",
            {
                "comparable": False,
                "reasons": ["Legacy run has no formal comparability proof."],
            },
        )
        if not include_cases:
            payload.pop("eval_set_snapshot", None)
            payload.pop("case_results", None)
        return payload

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
            normalized_refs.append(
                {
                    "reference_id": str(reference.get("reference_id") or f"ref_{uuid.uuid4().hex}"),
                    "document_id": str(reference["document_id"]).strip()[:200],
                    "chunk_id": _optional_string(reference.get("chunk_id"), 240),
                    "source_block_id": _optional_string(reference.get("source_block_id"), 240),
                    "page_number": _optional_int(reference.get("page_number")),
                    "relevance": relevance,
                    "match_mode": _normalize_match_mode(reference),
                    "catalog_anchor_key": _optional_string(reference.get("catalog_anchor_key"), 200),
                }
            )
        return {
            "case_id": str(raw.get("case_id")) if preserve_id and raw.get("case_id") else f"evalcase_{uuid.uuid4().hex}",
            "query": query,
            "expected_refs": normalized_refs,
            "expected_no_result": expected_no_result,
            "review_status": _normalize_review_status(
                raw.get("review_status"), expected_no_result=expected_no_result
            ),
            "review_evidence": _safe_review_evidence(raw.get("review_evidence")),
            "tags": [str(item)[:80] for item in raw.get("tags", []) if str(item).strip()][:20],
            "notes": str(raw.get("notes") or "")[:1000],
            "targeting": _safe_targeting(raw.get("targeting")),
        }

    def _touch_set(self, item: dict[str, Any]) -> None:
        item["revision"] = int(item.get("revision", 0)) + 1
        item["updated_at"] = time.time()
        if str(item.get("origin") or "manual") == "generated":
            calibration = dict(item.get("calibration") or {})
            if _gold_contract_version(item) == GOLD_CONTRACT_V2:
                item["calibration"] = {
                    **calibration,
                    "status": "not_required",
                    "reason": (
                        "rag-gold-v2 is structurally validated before one paired Formal run."
                    ),
                    "dataset_revision": int(item["revision"]),
                }
            else:
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
                case.setdefault("review_evidence", {})
                case.setdefault("targeting", {})
        if _gold_contract_version(payload) == GOLD_CONTRACT_V2:
            payload["qualification_manifest"] = _gold_v2_quality_manifest(payload)
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
        return bool(expected_block) and str(source.get("source_block_id") or "") == expected_block
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


def _safe_review_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    decision = str(value.get("decision") or "").strip()
    if decision not in {"approved", "rejected"}:
        return {}
    source = str(value.get("source") or "").strip()
    if source != "manual_ui":
        return {}
    reviewed_at = _float_or_none(value.get("reviewed_at"))
    dataset_revision = _optional_int(value.get("dataset_revision"))
    if reviewed_at is None or reviewed_at <= 0 or dataset_revision is None or dataset_revision <= 0:
        return {}
    return {
        "source": "manual_ui",
        "decision": decision,
        "reviewed_at": reviewed_at,
        "dataset_revision": dataset_revision,
        "reason": str(value.get("reason") or "")[:1000],
    }


def _safe_targeting(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    evidence_ids = value.get("evidence_ids")
    context_refs = value.get("context_refs")
    leakage = value.get("leakage")
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
                "page_number": _optional_int(item.get("page_number")),
            }
            for item in context_refs
            if isinstance(item, dict)
            and item.get("document_id")
            and item.get("chunk_id")
            and item.get("source_block_id")
        ][:3] if isinstance(context_refs, list) else [],
        "leakage": {
            "max_normalized_copy": max(
                0,
                min(
                    32,
                    _optional_int((leakage or {}).get("max_normalized_copy")) or 0,
                ),
            ),
            "warning_threshold": _optional_int(
                (leakage or {}).get("warning_threshold")
            ),
            "warning": bool((leakage or {}).get("warning")),
            "blocked": bool((leakage or {}).get("blocked")),
        }
        if isinstance(leakage, dict)
        else {},
    }


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
    confidence_level = float(policy["paired_confidence_level"])
    if confidence_level < 0.5 or confidence_level >= 1:
        raise ValueError("paired_confidence_level must be between 0.5 and 1.")
    policy["paired_confidence_level"] = confidence_level
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
        "embedding_provider",
        "embedding_model",
        "embedding_dimension",
        "vector_candidate_count",
        "fulltext_candidate_count",
    }
    receipt: dict[str, Any] = {}
    for key in allowed:
        item = value.get(key)
        if isinstance(item, (str, int, float, bool, type(None))):
            receipt[key] = item
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


def _normalized_query(value: str) -> str:
    return "".join(
        character.casefold()
        for character in str(value)
        if character.isalnum() or "\u3400" <= character <= "\u9fff"
    )


def _query_tokens(value: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", str(value).casefold()))


def _safe_error(value: Any) -> str:
    return str(value or "Evaluation failed.").strip()[:500]


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
