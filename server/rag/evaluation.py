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
    CALIBRATION_CONTRACT_V1,
    MIN_HARD_NEGATIVES,
    MIN_POSITIVE_CASES,
    build_tuning_readiness,
    calibration_evidence_checksum_payload,
    normalize_benchmark_role,
)
from .runtime_identity import is_valid_rag_runtime_identity


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
GOLD_V2_EVIDENCE_POLICY = "content-source-block-v1"
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

ABSTENTION_CONTRACT_VERSION = "rag-abstention-v1"
DEVELOPMENT_EVIDENCE_VERSION = "rag-development-evidence-v1"


def build_development_evidence_manifest(
    evaluation_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Create a text-free fingerprint of every query used to tune a candidate."""

    query_fingerprints = []
    for case in evaluation_snapshot.get("cases") or []:
        if not isinstance(case, dict):
            continue
        query = str(case.get("query") or "")
        normalized = _normalized_query(query)
        query_fingerprints.append(
            {
                "query_hash": _checksum(normalized),
                "token_hashes": sorted(_checksum(token) for token in _query_tokens(query)),
            }
        )
    payload = {
        "version": DEVELOPMENT_EVIDENCE_VERSION,
        "eval_set_version_id": str(evaluation_snapshot.get("version_id") or ""),
        "eval_set_checksum": str(evaluation_snapshot.get("checksum") or ""),
        "corpus_snapshot_hash": str(
            evaluation_snapshot.get("corpus_snapshot_hash") or ""
        ),
        "case_count": len(query_fingerprints),
        "query_fingerprints": sorted(
            query_fingerprints, key=lambda item: str(item["query_hash"])
        ),
    }
    return {**payload, "checksum": _checksum(payload)}


def assess_formal_evidence_independence(
    development_manifest: dict[str, Any] | None,
    formal_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Reject exact or near-duplicate Formal queries previously used for tuning."""

    if not development_manifest:
        return {
            "version": DEVELOPMENT_EVIDENCE_VERSION,
            "status": "no_declared_development_evidence",
            "independent": True,
            "overlap_case_count": 0,
            "similarity_threshold": 0.8,
        }
    manifest = _copy(development_manifest)
    checksum = str(manifest.pop("checksum", ""))
    fingerprints = manifest.get("query_fingerprints")
    structurally_valid = (
        isinstance(fingerprints, list)
        and int(manifest.get("case_count") or 0) == len(fingerprints)
        and len(str(manifest.get("eval_set_checksum") or "")) == 64
        and len(str(manifest.get("corpus_snapshot_hash") or "")) == 64
        and all(
            isinstance(item, dict)
            and len(str(item.get("query_hash") or "")) == 64
            and isinstance(item.get("token_hashes"), list)
            and all(len(str(token)) == 64 for token in item.get("token_hashes") or [])
            for item in fingerprints
        )
    )
    if (
        manifest.get("version") != DEVELOPMENT_EVIDENCE_VERSION
        or len(checksum) != 64
        or checksum != _checksum(manifest)
        or not structurally_valid
    ):
        return {
            "version": DEVELOPMENT_EVIDENCE_VERSION,
            "status": "invalid_development_evidence",
            "independent": False,
            "overlap_case_count": 0,
            "similarity_threshold": 0.8,
        }
    development_queries = [
        item
        for item in manifest.get("query_fingerprints") or []
        if isinstance(item, dict)
    ]
    overlapping_formal_queries = 0
    for case in formal_snapshot.get("cases") or []:
        if not isinstance(case, dict):
            continue
        query = str(case.get("query") or "")
        query_hash = _checksum(_normalized_query(query))
        token_hashes = {_checksum(token) for token in _query_tokens(query)}
        overlaps = False
        for prior in development_queries:
            prior_tokens = {
                str(item) for item in prior.get("token_hashes") or [] if str(item)
            }
            union = token_hashes | prior_tokens
            similarity = len(token_hashes & prior_tokens) / len(union) if union else 0.0
            if query_hash == str(prior.get("query_hash") or "") or similarity >= 0.8:
                overlaps = True
                break
        overlapping_formal_queries += int(overlaps)
    independent = overlapping_formal_queries == 0
    return {
        "version": DEVELOPMENT_EVIDENCE_VERSION,
        "status": "independent" if independent else "development_evidence_overlap",
        "independent": independent,
        "overlap_case_count": overlapping_formal_queries,
        "similarity_threshold": 0.8,
        "development_eval_set_version_id": str(
            manifest.get("eval_set_version_id") or ""
        ),
        "development_eval_set_checksum": str(
            manifest.get("eval_set_checksum") or ""
        ),
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
    safe_receipt = _safe_retrieval_receipt(retrieval_receipt)
    explicit_abstention = isinstance(safe_receipt.get("abstained"), bool)
    abstained = (
        bool(safe_receipt.get("abstained"))
        if explicit_abstention
        else len(sources) == 0
    )
    abstention_contract = "explicit" if explicit_abstention else "legacy_source_empty"
    if expected_no_result:
        return {
            "status": "completed",
            "metrics": {
                "no_result_accuracy": 1.0 if abstained else 0.0,
                "false_positive_rate": 0.0 if abstained else 1.0,
            },
            "latency_ms": round(max(0.0, latency_ms), 3),
            "source_count": len(sources),
            "expected_count": 0,
            "matched_expected_count": 0,
            "expected_no_result": True,
            "no_result": abstained,
            "abstention_contract": abstention_contract,
            "warning_count": len(warnings or []),
            "warnings": [str(item)[:240] for item in (warnings or [])[:10]],
            "ranking": [_ranking_item(source, rank) for rank, source in enumerate(sources, 1)],
            "retrieval_receipt": safe_receipt,
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
        "no_result": abstained,
        "abstention_contract": abstention_contract,
        "warning_count": len(warnings or []),
        "warnings": [str(item)[:240] for item in (warnings or [])[:10]],
        "ranking": ranking,
        "retrieval_receipt": safe_receipt,
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
    evidence_policy = str(provenance.get("evidence_policy_version") or "")
    add(
        "content_source_block_evidence",
        evidence_policy == GOLD_V2_EVIDENCE_POLICY,
        evidence_policy or "legacy_or_missing",
        GOLD_V2_EVIDENCE_POLICY,
    )
    add("case_count", len(cases) == 42, len(cases), 42)
    add("positive_count", len(positives) == 30, len(positives), 30)
    add("hard_negative_count", len(negatives) == 12, len(negatives), 12)

    valid_reviews = 0
    leakage_warning_reasons = 0
    fresh_leakage_receipts = 0
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
        query_hash = str(leakage.get("query_hash") or "")
        receipt_is_fresh = not leakage.get("stale") and (
            not query_hash
            or query_hash == _checksum(_normalized_query(str(case.get("query") or "")))
        )
        fresh_leakage_receipts += int(
            bool(case.get("expected_no_result")) or receipt_is_fresh
        )
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
    has_bound_leakage_receipts = any(
        not case.get("expected_no_result")
        and (
            dict((case.get("targeting") or {}).get("leakage") or {}).get("stale")
            or dict((case.get("targeting") or {}).get("leakage") or {}).get(
                "query_hash"
            )
        )
        for case in cases
    )
    if has_bound_leakage_receipts:
        add(
            "fresh_leakage_receipts",
            fresh_leakage_receipts == len(cases),
            fresh_leakage_receipts,
            len(cases),
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
        "version": "rag-gold-v2-qualification-v2",
        "qualified": all(check["passed"] for check in checks),
        "checks": checks,
        "counts": {
            "total": len(cases),
            "positive": len(positives),
            "hard_negative": len(negatives),
            "reviewed": valid_reviews,
        },
    }


_GOLD_V2_REVIEW_STAGE_CHECKS = frozenset(
    {
        "manual_reviews",
        "leakage_warning_reasons",
        # A blocking leak must remain rejectable from the review workbench.
        "no_blocking_leakage",
    }
)


def gold_v2_review_admission_blockers(snapshot: dict[str, Any]) -> list[str]:
    """Return structural failures that must be fixed before manual review starts."""

    if _gold_contract_version(snapshot) != GOLD_CONTRACT_V2:
        return []
    return [
        str(check["id"])
        for check in _gold_v2_quality_manifest(snapshot)["checks"]
        if not check["passed"]
        and str(check["id"]) not in _GOLD_V2_REVIEW_STAGE_CHECKS
    ]


def _gold_v2_checksum_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "benchmark_contract_version": _gold_contract_version(snapshot),
        "benchmark_role": normalize_benchmark_role(
            snapshot.get("benchmark_role"),
            origin=str(snapshot.get("origin") or "manual"),
            catalog_ref=dict(snapshot.get("catalog_ref") or {}),
        ),
        "cases": snapshot.get("cases") or [],
        "provenance": snapshot.get("provenance") or {},
        "coverage": snapshot.get("coverage") or {},
        "calibration": snapshot.get("calibration") or {},
        "corpus_snapshot": snapshot.get("corpus_snapshot") or {},
        "qualification_manifest": snapshot.get("qualification_manifest") or {},
        "freshness_manifest": snapshot.get("freshness_manifest") or {},
    }


def _gold_v2_source_checksum_valid(snapshot: dict[str, Any]) -> bool:
    """Validate sealed Gold, including the pre-freshness v2 checksum shape.

    The freshness manifest was added after the first rag-gold-v2 version had
    already been sealed. That immutable version remains valid calibration
    source evidence, but it must not gain a new checksum or accept any other
    legacy payload shape.
    """

    published = str(snapshot.get("checksum") or "")
    if not published:
        return False
    payload = _gold_v2_checksum_payload(snapshot)
    if published == _checksum(payload):
        return True
    if "freshness_manifest" in snapshot:
        return False
    payload.pop("freshness_manifest", None)
    return published == _checksum(payload)


def _gold_v2_freshness_manifest(
    data: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Require a fully fresh question set after same-corpus Gold is consumed."""

    corpus_hash = str(candidate.get("corpus_snapshot_hash") or "")
    consumed_versions: list[dict[str, Any]] = []
    for version in (data.get("versions") or {}).values():
        if (
            not isinstance(version, dict)
            or _gold_contract_version(version) != GOLD_CONTRACT_V2
            or str(version.get("corpus_snapshot_hash") or "") != corpus_hash
        ):
            continue
        _, usage = _find_evidence_usage(
            data,
            version,
            version_id=str(version.get("version_id") or ""),
        )
        if isinstance(usage, dict) and str(usage.get("status") or "") == "consumed":
            consumed_versions.append(version)

    current_queries = [
        str(case.get("query") or "")
        for case in candidate.get("cases") or []
        if isinstance(case, dict)
    ]
    stale_indices: set[int] = set()
    near_duplicate_pair_count = 0
    for current_index, current_query in enumerate(current_queries):
        current_normalized = _normalized_query(current_query)
        current_tokens = _query_tokens(current_query)
        for version in consumed_versions:
            for prior_case in version.get("cases") or []:
                if not isinstance(prior_case, dict):
                    continue
                prior_query = str(prior_case.get("query") or "")
                exact_duplicate = bool(current_normalized) and (
                    current_normalized == _normalized_query(prior_query)
                )
                prior_tokens = _query_tokens(prior_query)
                union = current_tokens | prior_tokens
                near_duplicate = bool(union) and (
                    len(current_tokens & prior_tokens) / len(union) >= 0.8
                )
                if exact_duplicate or near_duplicate:
                    stale_indices.add(current_index)
                    near_duplicate_pair_count += 1

    current_count = len(current_queries)
    stale_count = len(stale_indices)
    return {
        "version": "rag-gold-v2-freshness-v1",
        "qualified": stale_count == 0,
        "scope": "same_corpus_consumed_gold",
        "similarity_threshold": 0.8,
        "prior_consumed_gold_count": len(consumed_versions),
        "prior_consumed_checksums": sorted(
            str(version.get("checksum") or "") for version in consumed_versions
        ),
        "current_query_count": current_count,
        "materially_fresh_query_count": current_count - stale_count,
        "stale_query_count": stale_count,
        "near_duplicate_pair_count": near_duplicate_pair_count,
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
            "id": "promotion_sealed_role",
            "passed": benchmark_role == "promotion_sealed",
            "actual": benchmark_role,
            "required": "promotion_sealed",
        },
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


def _sealed_evidence_usage_key(snapshot: dict[str, Any]) -> str:
    checksum = str(snapshot.get("checksum") or "")
    if len(checksum) != 64:
        checksum = ""
    else:
        try:
            int(checksum, 16)
        except ValueError:
            checksum = ""
    if checksum:
        return f"checksum:{checksum}"
    return f"version:{str(snapshot.get('version_id') or '')}"


def _find_evidence_usage(
    data: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    version_id: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    usage_key = _sealed_evidence_usage_key(snapshot)
    resolved_version_id = str(
        version_id or snapshot.get("version_id") or ""
    )
    if usage_key == "version:" and resolved_version_id:
        usage_key = f"version:{resolved_version_id}"
    checksum = str(snapshot.get("checksum") or "")
    usages = data.get("evidence_usages") or {}
    for key in (
        usage_key,
        resolved_version_id,
        f"version:{resolved_version_id}",
    ):
        usage = usages.get(key)
        if isinstance(usage, dict):
            return key, usage
    if checksum:
        versions = data.get("versions") or {}
        for key, usage in usages.items():
            if not isinstance(usage, dict):
                continue
            if str(usage.get("evidence_checksum") or "") == checksum:
                return str(key), usage
            legacy_version = versions.get(
                str(usage.get("eval_set_version_id") or "")
            )
            if (
                isinstance(legacy_version, dict)
                and str(legacy_version.get("checksum") or "") == checksum
            ):
                return str(key), usage
    return usage_key, None


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

    def fork_calibration_set(
        self,
        source_eval_set_id: str,
        *,
        source_version: int,
        target_pipeline_version_id: str,
        target_corpus_snapshot: dict[str, Any],
        name: str | None = None,
    ) -> dict[str, Any]:
        """Fork immutable Gold into target-bound calibration evidence.

        Human decisions are intentionally not inherited. The new draft reuses
        only query/Gold content and requires fresh manual review before publish.
        """

        clean_target = str(target_pipeline_version_id or "").strip()
        if not clean_target:
            raise ValueError("Calibration fork requires a target pipeline version.")
        with self._lock:
            data = self._read_unlocked()
            source_set = self._set_or_raise(data, source_eval_set_id)
            source = next(
                (
                    item
                    for item in data["versions"].values()
                    if item.get("eval_set_id") == source_eval_set_id
                    and int(item.get("version") or 0) == int(source_version)
                ),
                None,
            )
            if not isinstance(source, dict):
                raise EvaluationSetNotFoundError(
                    "Knowledge evaluation set version not found."
                )
            if _gold_contract_version(source) != GOLD_CONTRACT_V2:
                raise EvaluationStateError(
                    "Calibration forks require an immutable rag-gold-v2 source."
                )
            if not _gold_v2_source_checksum_valid(source):
                raise EvaluationStateError(
                    "Calibration source checksum validation failed."
                )
            source_manifest = dict(source.get("qualification_manifest") or {})
            if not source_manifest.get("qualified"):
                raise EvaluationStateError(
                    "Calibration source Gold is not structurally qualified."
                )
            source_corpus_hash = str(source.get("corpus_snapshot_hash") or "")
            target_corpus_hash = str(
                target_corpus_snapshot.get("corpus_snapshot_hash") or ""
            )
            target_corpus = dict(
                target_corpus_snapshot.get("corpus_snapshot") or {}
            )
            if (
                not source_corpus_hash
                or source_corpus_hash != target_corpus_hash
                or _checksum(target_corpus) != target_corpus_hash
            ):
                raise EvaluationStateError(
                    "Calibration source and target must use the same corpus snapshot."
                )

            cases = []
            for case in source.get("cases") or []:
                if not isinstance(case, dict):
                    continue
                cases.append(
                    self._normalize_case(
                        {
                            **_copy(case),
                            "review_status": "pending",
                            "review_evidence": {},
                        }
                    )
                )
            if not cases:
                raise EvaluationStateError("Calibration source contains no cases.")
            now = time.time()
            target_reference = {
                "kind": "knowledge_pipeline_version",
                "kb_id": str(source_set.get("kb_id") or ""),
                "pipeline_version_id": clean_target,
            }
            item = {
                "eval_set_id": f"evalset_{uuid.uuid4().hex}",
                "kb_id": str(source_set.get("kb_id") or ""),
                "name": str(
                    name or f"{source.get('name') or 'Gold'} calibration"
                )[:160],
                "description": (
                    "Target-bound Strategy Tuner calibration fork; manual reviews "
                    "must be completed again."
                ),
                "revision": 1,
                "status": "active",
                "cases": cases,
                "origin": "generated",
                "catalog_ref": {},
                "provenance": {
                    "benchmark_contract_version": CALIBRATION_CONTRACT_V1,
                    "evidence_policy_version": str(
                        (source.get("provenance") or {}).get(
                            "evidence_policy_version"
                        )
                        or GOLD_V2_EVIDENCE_POLICY
                    ),
                    "pipeline_version_id": clean_target,
                    "target_reference": target_reference,
                    "corpus_snapshot": _copy(source.get("corpus_snapshot") or {}),
                    "corpus_snapshot_hash": source_corpus_hash,
                    "source_evidence": {
                        "eval_set_id": source_eval_set_id,
                        "version_id": str(source.get("version_id") or ""),
                        "version": int(source.get("version") or 0),
                        "checksum": str(source.get("checksum") or ""),
                        "benchmark_contract_version": GOLD_CONTRACT_V2,
                    },
                    "forked_at": now,
                },
                "coverage": _copy(source.get("coverage") or {}),
                "calibration": {
                    "status": "not_required",
                    "reason": (
                        "This published pack is the input to Strategy Tuner "
                        "threshold calibration."
                    ),
                    "dataset_revision": 1,
                    "target_reference": target_reference,
                },
                "benchmark_role": "calibration",
                "latest_version": None,
                "created_at": now,
                "updated_at": now,
            }
            data["sets"][item["eval_set_id"]] = item
            self._write_unlocked(data)
            return self._set_payload(item)

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
                is_calibration_v1 = (
                    _gold_contract_version(item) == CALIBRATION_CONTRACT_V1
                )
                if int(calibration.get("dataset_revision") or 0) != int(
                    item.get("revision") or 0
                ):
                    raise EvaluationStateError(
                        "Generated evaluation set validation revision is stale."
                    )
                allowed_calibration_statuses = (
                    {"not_required", "calibrated", "warning"}
                    if is_gold_v2 or is_calibration_v1
                    else {"calibrated", "warning"}
                )
                if calibration_status not in allowed_calibration_statuses:
                    raise EvaluationStateError(
                        (
                            (
                                "Target-bound calibration evidence is validated by "
                                "Strategy Tuner readiness."
                                if is_calibration_v1
                                else "rag-gold-v2 uses structural validation before its single Formal run."
                            )
                            if is_gold_v2 or is_calibration_v1
                            else "Generated evaluation set must complete calibration before publishing."
                        )
                    )
                if calibration_status == "warning" and not acknowledge_calibration_warnings:
                    raise EvaluationStateError(
                        "Calibration warnings must be explicitly acknowledged before publishing."
                    )
                if is_gold_v2:
                    if normalize_benchmark_role(
                        item.get("benchmark_role"),
                        origin=str(item.get("origin") or "generated"),
                    ) != "promotion_sealed":
                        raise EvaluationStateError(
                            "rag-gold-v2 promotion evidence must use benchmark role promotion_sealed."
                        )
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
                elif is_calibration_v1:
                    provenance = dict(item.get("provenance") or {})
                    target_version_id = str(
                        provenance.get("pipeline_version_id") or ""
                    )
                    qualification_manifest = build_tuning_readiness(
                        item,
                        target_version_id=target_version_id or None,
                    )
                    threshold_ready = bool(
                        (qualification_manifest.get("dimensions") or {})
                        .get("threshold", {})
                        .get("eligible")
                    )
                    if not target_version_id or not threshold_ready:
                        raise EvaluationStateError(
                            "Calibration evidence requires target binding, stable Gold, "
                            "and fresh manual review of 12 corpus-near negatives."
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
                freshness_manifest = _gold_v2_freshness_manifest(data, version)
                if not freshness_manifest["qualified"]:
                    raise EvaluationStateError(
                        "A consumed same-corpus Gold requires 42 materially fresh queries; "
                        f"{freshness_manifest['stale_query_count']} remain duplicated or near-duplicated."
                    )
                version["freshness_manifest"] = freshness_manifest
                version["checksum"] = _checksum(_gold_v2_checksum_payload(version))
                duplicate = next(
                    (
                        existing
                        for existing in data["versions"].values()
                        if isinstance(existing, dict)
                        and str(existing.get("benchmark_contract_version") or "")
                        == GOLD_CONTRACT_V2
                        and str(existing.get("checksum") or "")
                        == str(version["checksum"])
                    ),
                    None,
                )
                if isinstance(duplicate, dict):
                    raise EvaluationStateError(
                        "An identical sealed Gold checksum is already published."
                    )
            elif _gold_contract_version(item) == CALIBRATION_CONTRACT_V1:
                provenance = dict(item.get("provenance") or {})
                version.update(
                    {
                        "benchmark_contract_version": CALIBRATION_CONTRACT_V1,
                        "corpus_snapshot": _copy(
                            provenance.get("corpus_snapshot") or {}
                        ),
                        "corpus_snapshot_hash": str(
                            provenance.get("corpus_snapshot_hash") or ""
                        ),
                        "qualification_manifest": _copy(qualification_manifest),
                    }
                )
                version["checksum"] = _checksum(
                    calibration_evidence_checksum_payload(version)
                )
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
            query_changed = (
                "query" in values
                and values.get("query") != item["cases"][index].get("query")
            )
            merged = {**item["cases"][index], **values, "case_id": case_id}
            if semantic_change and _gold_contract_version(item) == GOLD_CONTRACT_V2:
                merged["review_status"] = "pending"
                merged["review_evidence"] = {}
                if (
                    query_changed
                    and not bool(merged.get("expected_no_result"))
                ):
                    targeting = dict(merged.get("targeting") or {})
                    leakage = dict(targeting.get("leakage") or {})
                    expected_query_hash = _checksum(
                        _normalized_query(str(merged.get("query") or ""))
                    )
                    if str(leakage.get("query_hash") or "") != expected_query_hash:
                        leakage["stale"] = True
                    targeting["leakage"] = leakage
                    merged["targeting"] = targeting
                if query_changed:
                    provenance = dict(item.get("provenance") or {})
                    receipts = [
                        _copy(receipt)
                        for receipt in provenance.get("query_revision_receipts") or []
                        if isinstance(receipt, dict)
                    ]
                    receipts.append(
                        {
                            "case_id": case_id,
                            "source": "server_case_update",
                            "previous_query_hash": _checksum(
                                _normalized_query(
                                    str(item["cases"][index].get("query") or "")
                                )
                            ),
                            "new_query_hash": _checksum(
                                _normalized_query(str(merged.get("query") or ""))
                            ),
                            "changed_at": time.time(),
                            "dataset_revision": int(item.get("revision") or 0) + 1,
                        }
                    )
                    provenance["query_revision_contract"] = (
                        "server-case-update-v1"
                    )
                    provenance["query_revision_receipts"] = receipts[-500:]
                    item["provenance"] = provenance
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
        snapshot_role = normalize_benchmark_role(
            (evaluation_set_version or evaluation_set).get("benchmark_role"),
            origin=str((evaluation_set_version or evaluation_set).get("origin") or "manual"),
            catalog_ref=dict(
                (evaluation_set_version or evaluation_set).get("catalog_ref") or {}
            ),
        )
        if normalized_mode != "formal" and snapshot_role == "promotion_sealed":
            raise EvaluationStateError(
                "Sealed promotion Gold cannot be used for diagnostic evaluation."
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
                "observation_depth",
                "order_algorithm",
                "schedule_checksum",
                "threshold_score_domain",
                "abstention_contract_version",
                "runtime",
                "retry_policy",
                "warmup_policy",
                "development_evidence_independence",
            }
            manifest_runtime = normalized_manifest.get("runtime")
            independence = normalized_manifest.get(
                "development_evidence_independence"
            )
            if not is_valid_rag_runtime_identity(manifest_runtime):
                raise EvaluationStateError(
                    "Formal evaluation requires a valid RAG runtime identity."
                )
            if (
                required_manifest - set(normalized_manifest)
                or normalized_manifest.get("version") != "rag-eval-v2"
                or normalized_manifest.get("evaluation_set_checksum")
                != evaluation_set_version.get("checksum")
                or normalized_manifest.get("execution_seed") != int(execution_seed)
                or int(normalized_manifest.get("observation_depth") or 0)
                != max(int(item) for item in ks)
                or normalized_manifest.get("order_algorithm")
                != "sha256-paired-interleave-v1"
                or normalized_manifest.get("threshold_score_domain") != "fused_score"
                or normalized_manifest.get("abstention_contract_version")
                != ABSTENTION_CONTRACT_VERSION
                or normalized_manifest.get("retry_policy") != "none"
                or normalized_manifest.get("warmup_policy") != "none"
                or not isinstance(independence, dict)
                or not bool(independence.get("independent"))
                or independence.get("version") != DEVELOPMENT_EVIDENCE_VERSION
                or independence.get("status")
                not in {"independent", "no_declared_development_evidence"}
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
                fingerprint_runtime = fingerprint.get("runtime")
                evidence_runtime = evidence.get("runtime")
                if (
                    fingerprint_runtime != manifest_runtime
                    or evidence_runtime != manifest_runtime
                ):
                    raise EvaluationStateError(
                        "Formal evaluation runtime identity does not match its targets."
                    )
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
                        "runtime",
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
            "evidence_usage": {},
        }
        with self._lock:
            data = self._read_unlocked()
            if normalized_mode == "formal":
                version_id = str(evaluation_set_version.get("version_id") or "")
                evidence_usage_key, prior_usage = _find_evidence_usage(
                    data,
                    evaluation_set_version,
                )
                if isinstance(prior_usage, dict):
                    raise EvaluationStateError(
                        "This sealed promotion Gold has already been consumed by a Formal run."
                    )
                usage = {
                    "status": "reserved",
                    "eval_set_version_id": version_id,
                    "evidence_checksum": str(
                        evaluation_set_version.get("checksum") or ""
                    ),
                    "evidence_usage_key": evidence_usage_key,
                    "run_id": run["run_id"],
                    "target_fingerprints": [
                        {
                            "version_id": str(item.get("version_id") or ""),
                            "version_fingerprint": str(
                                item.get("version_fingerprint") or ""
                            ),
                        }
                        for item in normalized_manifest.get("target_fingerprints") or []
                        if isinstance(item, dict)
                    ],
                    "reserved_at": now,
                }
                run["evidence_usage"] = _copy(usage)
                data["evidence_usages"][evidence_usage_key] = usage
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
                now = time.time()
                run["completed_at"] = now
                self._consume_evidence_usage(data, run, "cancelled", now)
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
        return self._finalize_run(
            run_id,
            {
                "progress": 100,
                "target_results": _copy(target_results),
                "error": None,
            },
            terminal_status="succeeded",
        )

    def fail_run(self, run_id: str, error: str) -> dict[str, Any]:
        return self._finalize_run(
            run_id,
            {
                "error": _safe_error(error),
            },
            terminal_status="failed",
        )

    def complete_cancel(self, run_id: str) -> dict[str, Any]:
        return self._finalize_run(
            run_id,
            {},
            terminal_status="cancelled",
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
        current_runtime: dict[str, Any] | None = None,
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
        independence = (run.get("execution_manifest") or {}).get(
            "development_evidence_independence"
        )
        if (
            str(run.get("run_mode") or "diagnostic") != "formal"
            or str(run.get("metric_contract_version") or "legacy") != "rag-eval-v2"
            or not bool((run.get("comparability") or {}).get("comparable"))
            or str(
                (run.get("execution_manifest") or {}).get(
                    "abstention_contract_version"
                )
                or ""
            )
            != ABSTENTION_CONTRACT_VERSION
            or not isinstance(independence, dict)
            or independence.get("version") != DEVELOPMENT_EVIDENCE_VERSION
            or not bool(independence.get("independent"))
            or independence.get("status")
            not in {"independent", "no_declared_development_evidence"}
        ):
            raise EvaluationPromotionError(
                "Candidate activation requires a comparable formal rag-eval-v2 run "
                "with independent development evidence."
            )
        manifest_runtime = (run.get("execution_manifest") or {}).get("runtime")
        if (
            not is_valid_rag_runtime_identity(manifest_runtime)
            or current_runtime != manifest_runtime
        ):
            raise EvaluationPromotionError(
                "Candidate activation requires the exact RAG runtime used by the Formal run."
            )
        evidence_version_id = str(run.get("eval_set_version_id") or "")
        run_usage = dict(run.get("evidence_usage") or {})
        ledger_data = self._read()
        ledger_key, ledger_usage = _find_evidence_usage(
            ledger_data,
            dict(run.get("eval_set_snapshot") or {}),
            version_id=evidence_version_id,
        )
        evidence_checksum = str(
            (run.get("eval_set_snapshot") or {}).get("checksum") or ""
        )
        if (
            not evidence_version_id
            or not isinstance(ledger_usage, dict)
            or str(run_usage.get("run_id") or "") != str(run.get("run_id") or "")
            or str(ledger_usage.get("run_id") or "") != str(run.get("run_id") or "")
            or str(run_usage.get("eval_set_version_id") or "")
            != evidence_version_id
            or str(run_usage.get("evidence_checksum") or "")
            != evidence_checksum
            or str(ledger_usage.get("evidence_checksum") or "")
            != evidence_checksum
            or str(run_usage.get("evidence_usage_key") or "") != ledger_key
            or str(ledger_usage.get("evidence_usage_key") or "") != ledger_key
            or str(run_usage.get("status") or "") != "consumed"
            or str(ledger_usage.get("status") or "") != "consumed"
            or str(run_usage.get("terminal_status") or "") != "succeeded"
            or str(ledger_usage.get("terminal_status") or "") != "succeeded"
        ):
            raise EvaluationPromotionError(
                "Candidate activation requires the one-shot sealed Gold usage receipt."
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
        payload.setdefault("evidence_usage", {})
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

    def _finalize_run(
        self,
        run_id: str,
        values: dict[str, Any],
        *,
        terminal_status: str,
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            run = self._run_or_raise(data, run_id)
            now = time.time()
            run.update(values)
            run["status"] = terminal_status
            run["completed_at"] = now
            run["updated_at"] = now
            self._consume_evidence_usage(data, run, terminal_status, now)
            self._write_unlocked(data)
            return self.run_payload(run)

    @staticmethod
    def _consume_evidence_usage(
        data: dict[str, Any],
        run: dict[str, Any],
        terminal_status: str,
        now: float,
    ) -> None:
        if str(run.get("run_mode") or "diagnostic") != "formal":
            return
        version_id = str(run.get("eval_set_version_id") or "")
        run_id = str(run.get("run_id") or "")
        usage_key, usage = _find_evidence_usage(
            data,
            dict(run.get("eval_set_snapshot") or {}),
            version_id=version_id,
        )
        if (
            not version_id
            or not isinstance(usage, dict)
            or str(usage.get("run_id") or "") != run_id
        ):
            return
        consumed = {
            **usage,
            "status": "consumed",
            "terminal_status": terminal_status,
            "consumed_at": float(usage.get("consumed_at") or now),
        }
        data["evidence_usages"][usage_key] = consumed
        run["evidence_usage"] = _copy(consumed)

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
            if _gold_contract_version(item) in {
                GOLD_CONTRACT_V2,
                CALIBRATION_CONTRACT_V1,
            }:
                item["calibration"] = {
                    **calibration,
                    "status": "not_required",
                    "reason": (
                        "Target-bound evidence is structurally validated before its "
                        "single downstream evaluation run."
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
        elif _gold_contract_version(payload) == CALIBRATION_CONTRACT_V1:
            provenance = dict(payload.get("provenance") or {})
            payload["qualification_manifest"] = build_tuning_readiness(
                payload,
                target_version_id=str(
                    provenance.get("pipeline_version_id") or ""
                )
                or None,
            )
        return payload

    def _version_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        payload = _copy(item)
        payload["benchmark_role"] = normalize_benchmark_role(
            payload.get("benchmark_role"),
            origin=str(payload.get("origin") or "manual"),
            catalog_ref=dict(payload.get("catalog_ref") or {}),
        )
        payload["evidence_qualification"] = qualify_promotion_evidence(payload)
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
            return {
                "version": "knowledge-evaluation-v2",
                "sets": {},
                "versions": {},
                "runs": {},
                "evidence_usages": {},
                "gate_policies": {},
            }
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        return {
            "version": "knowledge-evaluation-v2",
            "sets": value.get("sets") if isinstance(value.get("sets"), dict) else {},
            "versions": value.get("versions") if isinstance(value.get("versions"), dict) else {},
            "runs": value.get("runs") if isinstance(value.get("runs"), dict) else {},
            "evidence_usages": (
                value.get("evidence_usages")
                if isinstance(value.get("evidence_usages"), dict)
                else {}
            ),
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
            "query_hash": _optional_string((leakage or {}).get("query_hash"), 64),
            "stale": bool((leakage or {}).get("stale")),
        }
        if isinstance(leakage, dict) and leakage
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
        "candidate_limit",
        "observation_depth",
        "abstention_enabled",
        "abstention_applied",
        "abstained",
        "abstention_score_domain",
        "abstention_threshold",
        "abstention_score",
        "abstention_input_count",
        "abstention_reason",
        "evidence_verification_enabled",
        "evidence_verification_applied",
        "evidence_verdict",
        "evidence_support_score",
        "evidence_reason_code",
        "evidence_provider",
        "evidence_model",
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
        "rerank_max_output_tokens",
        "rerank_timeout_budget_ms",
        "rerank_elapsed_ms",
        "rerank_provider_http_elapsed_ms",
        "rerank_provider_prompt_tokens",
        "rerank_provider_completion_tokens",
        "rerank_provider_total_tokens",
        "rerank_provider_response_char_count",
        "retrieval_elapsed_ms",
        "embedding_elapsed_ms",
        "vector_search_elapsed_ms",
        "fulltext_search_elapsed_ms",
        "fusion_elapsed_ms",
        "rerank_attempted_provider",
        "rerank_attempted_model",
        "rerank_fallback_reason",
        "rerank_provider_target_used",
        "rerank_attempted_targets",
        "rerank_target_attempt_count",
        "rerank_external_call_count",
        "threshold_score_domain",
        "embedding_provider",
        "embedding_model",
        "embedding_dimension",
        "external_call_limit",
        "external_call_count",
        "embedding_external_call_count",
        "answer_external_call_count",
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


def gold_v2_leakage_receipt(
    query: str,
    evidence_texts: list[str],
    *,
    query_type: str,
) -> dict[str, Any]:
    """Bind leakage analysis to a query and server-resolved source text."""

    normalized_query = _normalized_query(query)
    max_copy = max(
        (
            _max_shared_substring_length(
                normalized_query,
                _normalized_query(text),
                cap=32,
            )
            for text in evidence_texts
        ),
        default=0,
    )
    warning_threshold = 12 if query_type in {"paraphrase", "cross_language"} else 24
    return {
        "max_normalized_copy": max_copy,
        "warning_threshold": warning_threshold,
        "warning": max_copy >= warning_threshold,
        "blocked": max_copy >= 32,
        "query_hash": _checksum(normalized_query),
        "stale": False,
    }


def _max_shared_substring_length(left: str, right: str, *, cap: int) -> int:
    if not left or not right:
        return 0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    low = 0
    high = min(cap, len(shorter))
    while low < high:
        probe = (low + high + 1) // 2
        windows = {
            shorter[index : index + probe]
            for index in range(len(shorter) - probe + 1)
        }
        if any(
            longer[index : index + probe] in windows
            for index in range(len(longer) - probe + 1)
        ):
            low = probe
        else:
            high = probe - 1
    return low


def _query_tokens(value: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", str(value).casefold()))


def _safe_error(value: Any) -> str:
    return str(value or "Evaluation failed.").strip()[:500]


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))
