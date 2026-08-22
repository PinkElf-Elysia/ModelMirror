from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any


READINESS_VERSION = "rag-strategy-tuning-readiness-v1"
CALIBRATION_CONTRACT_V1 = "rag-calibration-v1"
BENCHMARK_ROLES = {
    "unclassified",
    "regression_guard",
    "strategy_tuning",
    "promotion_evidence",
    "calibration",
    "regression",
    "promotion_sealed",
}
MIN_TOTAL_CASES = 12
MIN_POSITIVE_CASES = 30
MIN_HARD_NEGATIVES = 12
MIN_DENSITY_CASES = {
    "sparse": 6,
    "single_dense": 4,
    "multi_dense": 4,
}


def normalize_benchmark_role(
    value: Any,
    *,
    origin: str = "manual",
    catalog_ref: dict[str, Any] | None = None,
) -> str:
    role = str(value or "").strip().lower()
    if role:
        if role not in BENCHMARK_ROLES:
            raise ValueError("Unknown knowledge evaluation benchmark role.")
        return role
    if origin == "benchmark_catalog":
        return "regression_guard"
    if origin == "generated":
        return "strategy_tuning"
    return "unclassified"


def benchmark_role_for(value: dict[str, Any]) -> str:
    return normalize_benchmark_role(
        value.get("benchmark_role"),
        origin=str(value.get("origin") or "manual"),
        catalog_ref=dict(value.get("catalog_ref") or {}),
    )


def build_tuning_readiness(
    evaluation_version: dict[str, Any],
    *,
    target_version_id: str | None = None,
) -> dict[str, Any]:
    cases = [item for item in evaluation_version.get("cases") or [] if isinstance(item, dict)]
    role = benchmark_role_for(evaluation_version)
    positive = [item for item in cases if not item.get("expected_no_result")]
    negatives = [item for item in cases if item.get("expected_no_result")]
    provenance = dict(evaluation_version.get("provenance") or {})
    benchmark_contract_version = str(
        evaluation_version.get("benchmark_contract_version")
        or provenance.get("benchmark_contract_version")
        or ""
    )
    strict_calibration_review = (
        benchmark_contract_version == CALIBRATION_CONTRACT_V1
    )
    reviewed_negatives = [
        item
        for item in negatives
        if str(item.get("review_status") or "pending") == "approved"
        and (not strict_calibration_review or _has_manual_approval(item))
    ]
    hard_negatives = [
        item
        for item in reviewed_negatives
        if _is_hard_negative(item)
        and (not strict_calibration_review or _has_stable_negative_context(item))
    ]
    stable_positive = [item for item in positive if _has_stable_gold(item)]
    density = Counter(_density_bucket(item) for item in positive)
    query_types = Counter(_query_type(item) for item in cases)
    calibration = dict(evaluation_version.get("calibration") or {})
    calibration_status = str(calibration.get("status") or "pending")
    origin = str(evaluation_version.get("origin") or "manual")
    gold_v2 = benchmark_contract_version == "rag-gold-v2"
    target_reference = dict(provenance.get("target_reference") or {})
    calibration_reference = dict(calibration.get("target_reference") or {})
    declared_target_version = str(
        provenance.get("pipeline_version_id")
        or target_reference.get("pipeline_version_id")
        or calibration_reference.get("pipeline_version_id")
        or ""
    )

    checks: list[dict[str, Any]] = []

    def add_check(
        check_id: str,
        *,
        passed: bool,
        severity: str,
        actual: Any,
        required: Any,
        message: str,
    ) -> None:
        checks.append(
            {
                "check_id": check_id,
                "passed": bool(passed),
                "severity": severity,
                "actual": actual,
                "required": required,
                "message": message,
            }
        )

    add_check(
        "selection_role",
        passed=role in {"strategy_tuning", "calibration"},
        severity="blocker",
        actual=role,
        required=["strategy_tuning", "calibration"],
        message=(
            "Regression and sealed promotion packs cannot select a tuning winner."
            if role in {
                "regression_guard",
                "regression",
                "promotion_evidence",
                "promotion_sealed",
            }
            else "The evaluation version must declare a tuning or promotion evidence role."
        ),
    )
    add_check(
        "minimum_total_cases",
        passed=len(cases) >= MIN_TOTAL_CASES,
        severity="blocker",
        actual=len(cases),
        required=MIN_TOTAL_CASES,
        message="A tuning run needs enough cases for a separate validation slice.",
    )
    add_check(
        "minimum_positive_cases",
        passed=len(positive) >= MIN_POSITIVE_CASES,
        severity="blocker",
        actual=len(positive),
        required=MIN_POSITIVE_CASES,
        message="Strategy selection requires at least 30 answerable cases.",
    )
    if target_version_id and role in {"strategy_tuning", "calibration"}:
        add_check(
            "target_version_snapshot",
            passed=declared_target_version == target_version_id,
            severity="blocker",
            actual=declared_target_version or None,
            required=target_version_id,
            message=(
                "Tuning evidence must target the fixed knowledge version exactly."
            ),
        )
    if strict_calibration_review and evaluation_version.get("version_id"):
        expected_checksum = hashlib.sha256(
            json.dumps(
                calibration_evidence_checksum_payload(evaluation_version),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        actual_checksum = str(evaluation_version.get("checksum") or "")
        add_check(
            "published_checksum",
            passed=bool(actual_checksum) and actual_checksum == expected_checksum,
            severity="blocker",
            actual=actual_checksum or None,
            required=expected_checksum,
            message="Published calibration evidence checksum must remain intact.",
        )
    add_check(
        "stable_source_block_gold",
        passed=bool(positive) and len(stable_positive) == len(positive),
        severity="dimension",
        actual=len(stable_positive),
        required=len(positive),
        message="Cross-chunk comparison requires source-block Gold for every answerable case.",
    )
    add_check(
        "reviewed_hard_negatives",
        passed=len(hard_negatives) >= MIN_HARD_NEGATIVES,
        severity="dimension",
        actual=len(hard_negatives),
        required=MIN_HARD_NEGATIVES,
        message=(
            "Threshold tuning needs at least 12 reviewed corpus-near hard negatives; "
            "otherwise score_threshold remains fixed."
        ),
    )
    for bucket, minimum in MIN_DENSITY_CASES.items():
        add_check(
            f"density_{bucket}",
            passed=int(density.get(bucket, 0)) >= minimum,
            severity="dimension",
            actual=int(density.get(bucket, 0)),
            required=minimum,
            message=f"Chunk tuning needs {minimum} {bucket.replace('_', ' ')} evidence cases.",
        )
    add_check(
        "calibration_status",
        passed=(
            calibration_status in {"calibrated", "warning"}
            or (
                (gold_v2 or strict_calibration_review)
                and calibration_status == "not_required"
            )
        ),
        severity="blocker" if origin == "generated" else "warning",
        actual=calibration_status,
        required=(
            ["not_required", "calibrated", "warning"]
            if gold_v2 or strict_calibration_review
            else ["calibrated", "warning"]
        ),
        message=(
            (
                "Target-bound calibration evidence is the input to Strategy Tuner."
                if strict_calibration_review
                else "rag-gold-v2 defers retrieval measurement to the single paired Formal run."
            )
            if gold_v2 or strict_calibration_review
            else "A calibrated targeted set provides stronger evidence than an uncalibrated draft."
        ),
    )

    blockers = [item["message"] for item in checks if item["severity"] == "blocker" and not item["passed"]]
    warnings = [item["message"] for item in checks if item["severity"] != "blocker" and not item["passed"]]
    retrieval_ready = not blockers
    stable_gold_ready = bool(positive) and len(stable_positive) == len(positive)
    density_ready = all(
        int(density.get(bucket, 0)) >= minimum
        for bucket, minimum in MIN_DENSITY_CASES.items()
    )
    threshold_ready = retrieval_ready and len(hard_negatives) >= MIN_HARD_NEGATIVES
    chunk_ready = retrieval_ready and stable_gold_ready and density_ready
    if retrieval_ready:
        status = "ready"
        evidence_strength = "qualified" if threshold_ready and chunk_ready else "limited"
    elif role == "regression_guard":
        status = "report_only"
        evidence_strength = "regression_only"
    else:
        status = "insufficient_data"
        evidence_strength = "insufficient"

    payload = {
        "version": READINESS_VERSION,
        "status": status,
        "benchmark_role": role,
        "selection_eligible": retrieval_ready,
        "evidence_strength": evidence_strength,
        "counts": {
            "total": len(cases),
            "positive": len(positive),
            "no_result": len(negatives),
            "reviewed_no_result": len(reviewed_negatives),
            "reviewed_hard_negative": len(hard_negatives),
            "stable_source_block_positive": len(stable_positive),
        },
        "target": {
            "declared_pipeline_version_id": declared_target_version or None,
            "fixed_pipeline_version_id": target_version_id,
        },
        "coverage": {
            "density": dict(sorted(density.items())),
            "query_types": dict(sorted(query_types.items())),
        },
        "dimensions": {
            "retrieval": {"eligible": retrieval_ready},
            "threshold": {"eligible": threshold_ready},
            "chunking": {
                "eligible": chunk_ready,
                "sensitivity_probe_required": chunk_ready,
            },
        },
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
    }
    payload["checksum"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return payload


def realized_index_fingerprint(
    *,
    chunker: dict[str, Any],
    cost: dict[str, Any],
    document_results: list[dict[str, Any]] | None = None,
) -> str:
    per_document = [
        {
            "source_id": str(item.get("source_id") or ""),
            "chunk_count": int(item.get("chunk_count") or 0),
            "block_count": int(item.get("block_count") or 0),
        }
        for item in document_results or []
        if isinstance(item, dict)
    ]
    per_document.sort(key=lambda item: item["source_id"])
    payload = {
        "strategy": str(chunker.get("strategy") or ""),
        "chunk_count": int(cost.get("chunk_count") or 0),
        "per_document": per_document,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def ranking_fingerprint(case_results: list[dict[str, Any]]) -> str:
    payload = []
    for item in case_results:
        sources = []
        for source in item.get("ranking") or item.get("sources") or []:
            if not isinstance(source, dict):
                continue
            sources.append(
                str(
                    source.get("chunk_id")
                    or source.get("source_block_id")
                    or source.get("document_id")
                    or ""
                )
            )
        payload.append({"case_id": str(item.get("case_id") or ""), "sources": sources})
    payload.sort(key=lambda item: item["case_id"])
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def assess_chunk_sensitivity(
    candidates: list[dict[str, Any]],
    *,
    probe_retrieval_checksum: str,
    enabled: bool,
) -> dict[str, Any]:
    probes = [
        item
        for item in candidates
        if str(item.get("retrieval_input_checksum") or "")
        == probe_retrieval_checksum
    ]
    realized_pairs = {
        (
            str(item.get("realized_index_fingerprint") or ""),
            str(item.get("ranking_fingerprint") or ""),
        )
        for item in probes
        if item.get("realized_index_fingerprint") and item.get("ranking_fingerprint")
    }
    if enabled and len(probes) >= 2:
        status = "sufficient" if len(realized_pairs) >= 2 else "insufficient"
    elif enabled:
        status = "not_measured"
    else:
        status = "not_applicable"
    return {
        "status": status,
        "probe_count": len(probes),
        "unique_realized_outcomes": len(realized_pairs),
        "retrieval_checksum": probe_retrieval_checksum,
    }


def _has_stable_gold(case: dict[str, Any]) -> bool:
    refs = [item for item in case.get("expected_refs") or [] if isinstance(item, dict)]
    return bool(refs) and all(
        str(item.get("match_mode") or "") == "source_block"
        and bool(item.get("source_block_id"))
        for item in refs
    )


def _query_type(case: dict[str, Any]) -> str:
    targeting = case.get("targeting") if isinstance(case.get("targeting"), dict) else {}
    query_type = str(targeting.get("query_type") or "").strip().lower()
    if query_type:
        return query_type
    for tag in case.get("tags") or []:
        value = str(tag).strip().lower()
        if value in {
            "factual_lookup",
            "fact",
            "paraphrase",
            "section_context",
            "parent_child",
            "cross_language",
            "multi_evidence",
            "confusable_content",
            "no_result",
        }:
            return value
    return "unclassified"


def _density_bucket(case: dict[str, Any]) -> str:
    query_type = _query_type(case)
    reference_count = len(case.get("expected_refs") or [])
    if reference_count >= 2 or query_type == "multi_evidence":
        return "multi_dense"
    if query_type in {"section_context", "parent_child", "confusable_content"}:
        return "single_dense"
    return "sparse"


def _is_hard_negative(case: dict[str, Any]) -> bool:
    if not case.get("expected_no_result"):
        return False
    targeting = case.get("targeting") if isinstance(case.get("targeting"), dict) else {}
    tags = {str(item).strip().lower() for item in case.get("tags") or []}
    query_type = str(targeting.get("query_type") or "").strip().lower()
    return bool(
        query_type in {"confusable_content", "corpus_near"}
        or tags.intersection({"corpus_near", "confusable"})
    )


def _has_manual_approval(case: dict[str, Any]) -> bool:
    review = case.get("review_evidence")
    if not isinstance(review, dict):
        return False
    try:
        reviewed_at = float(review.get("reviewed_at") or 0)
        dataset_revision = int(review.get("dataset_revision") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        str(review.get("source") or "") == "manual_ui"
        and str(review.get("decision") or "") == "approved"
        and reviewed_at > 0
        and dataset_revision > 0
    )


def _has_stable_negative_context(case: dict[str, Any]) -> bool:
    targeting = case.get("targeting")
    if not isinstance(targeting, dict):
        return False
    return any(
        isinstance(item, dict)
        and bool(str(item.get("document_id") or ""))
        and bool(str(item.get("source_block_id") or ""))
        for item in targeting.get("context_refs") or []
    )


def calibration_evidence_checksum_payload(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "benchmark_contract_version": str(
            snapshot.get("benchmark_contract_version")
            or (snapshot.get("provenance") or {}).get(
                "benchmark_contract_version"
            )
            or ""
        ),
        "benchmark_role": benchmark_role_for(snapshot),
        "cases": snapshot.get("cases") or [],
        "provenance": snapshot.get("provenance") or {},
        "coverage": snapshot.get("coverage") or {},
        "calibration": snapshot.get("calibration") or {},
        "corpus_snapshot": snapshot.get("corpus_snapshot") or {},
        "qualification_manifest": snapshot.get("qualification_manifest") or {},
    }
