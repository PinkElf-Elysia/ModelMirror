from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any


READINESS_VERSION = "rag-strategy-tuning-readiness-v2"
BENCHMARK_ROLES = {
    "unclassified",
    "regression_guard",
    "strategy_tuning",
    "threshold_calibration",
    "held_out_qualification",
    "promotion_evidence",
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
    reviewed_negatives = [
        item for item in negatives if str(item.get("review_status") or "pending") == "approved"
    ]
    hard_negatives = [item for item in reviewed_negatives if _is_hard_negative(item)]
    stable_positive = [item for item in positive if _has_stable_gold(item)]
    density = Counter(_density_bucket(item) for item in positive)
    query_types = Counter(_query_type(item) for item in cases)
    calibration = dict(evaluation_version.get("calibration") or {})
    calibration_status = str(calibration.get("status") or "pending")
    origin = str(evaluation_version.get("origin") or "manual")
    provenance = dict(evaluation_version.get("provenance") or {})
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
        passed=role == "strategy_tuning",
        severity="blocker",
        actual=role,
        required=["strategy_tuning"],
        message=(
            "Regression packs verify engine consistency and cannot select a tuning winner."
            if role == "regression_guard"
            else "The evaluation version must declare a tuning or promotion evidence role."
        ),
    )
    if str(evaluation_version.get("benchmark_contract_version") or "") == "rag-gold-v3":
        manifest = evaluation_version.get("qualification_manifest")
        add_check(
            "locked_dataset_qualification",
            passed=isinstance(manifest, dict)
            and manifest.get("status") == "qualified"
            and manifest.get("dataset_role") == role,
            severity="blocker",
            actual=(manifest or {}).get("status") if isinstance(manifest, dict) else None,
            required="qualified",
            message="V3 tuning evidence must pass its immutable role qualification.",
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
    if target_version_id and origin == "generated":
        add_check(
            "target_version_snapshot",
            passed=declared_target_version == target_version_id,
            severity="blocker",
            actual=declared_target_version or None,
            required=target_version_id,
            message=(
                "Generated tuning evidence must target the fixed knowledge version exactly."
            ),
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
        passed=calibration_status in {"calibrated", "warning"},
        severity="blocker" if origin == "generated" else "warning",
        actual=calibration_status,
        required=["calibrated", "warning"],
        message="A calibrated targeted set provides stronger evidence than an uncalibrated draft.",
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


def build_threshold_calibration_readiness(
    evaluation_version: dict[str, Any],
    *,
    target_version_id: str | None = None,
) -> dict[str, Any]:
    cases = [
        item for item in evaluation_version.get("cases") or [] if isinstance(item, dict)
    ]
    positives = [item for item in cases if not item.get("expected_no_result")]
    negatives = [item for item in cases if item.get("expected_no_result")]
    stable_positive = [item for item in positives if _has_stable_gold(item)]
    hard_negatives = [
        item
        for item in negatives
        if str(item.get("review_status") or "") == "approved"
        and _is_hard_negative(item)
    ]
    role = benchmark_role_for(evaluation_version)
    contract = str(evaluation_version.get("benchmark_contract_version") or "")
    immutable = bool(
        evaluation_version.get("version_id") and evaluation_version.get("published_at")
    )
    provenance = dict(evaluation_version.get("provenance") or {})
    target_reference = dict(provenance.get("target_reference") or {})
    declared_target = str(
        provenance.get("pipeline_version_id")
        or target_reference.get("pipeline_version_id")
        or ""
    )
    reason_codes: list[str] = []
    if role != "threshold_calibration":
        reason_codes.append("calibration_role")
    if contract != "rag-gold-v3":
        reason_codes.append("calibration_contract")
    manifest = evaluation_version.get("qualification_manifest")
    if not (
        isinstance(manifest, dict)
        and manifest.get("status") == "qualified"
        and manifest.get("dataset_role") == "threshold_calibration"
    ):
        reason_codes.append("calibration_qualification")
    if not immutable:
        reason_codes.append("calibration_immutable")
    if not positives or len(stable_positive) != len(positives):
        reason_codes.append("stable_positive_gold")
    if len(hard_negatives) < MIN_HARD_NEGATIVES:
        reason_codes.append("hard_negative_verification")
    if (
        target_version_id
        and str(evaluation_version.get("origin") or "manual") == "generated"
        and declared_target != target_version_id
    ):
        reason_codes.append("target_version_snapshot")
    payload = {
        "version": "rag-threshold-calibration-readiness-v1",
        "eligible": not reason_codes,
        "benchmark_role": role,
        "reason_codes": reason_codes,
        "counts": {
            "total": len(cases),
            "positive": len(positives),
            "stable_positive": len(stable_positive),
            "no_result": len(negatives),
            "reviewed_hard_negative": len(hard_negatives),
        },
    }
    payload["checksum"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return payload


def validate_tuning_dataset_pair(
    tuning_version: dict[str, Any], calibration_version: dict[str, Any]
) -> dict[str, Any]:
    tuning_role = benchmark_role_for(tuning_version)
    calibration_role = benchmark_role_for(calibration_version)
    tuning_cases = [
        item for item in tuning_version.get("cases") or [] if isinstance(item, dict)
    ]
    calibration_cases = [
        item for item in calibration_version.get("cases") or [] if isinstance(item, dict)
    ]
    tuning_raw_queries = [str(item.get("query") or "") for item in tuning_cases]
    calibration_raw_queries = [
        str(item.get("query") or "") for item in calibration_cases
    ]
    tuning_queries = [_normalized_query(query) for query in tuning_raw_queries]
    calibration_queries = [
        _normalized_query(query) for query in calibration_raw_queries
    ]
    exact_overlap = {
        query for query in tuning_queries if query and query in set(calibration_queries)
    }
    near_duplicate_count = 0
    for tuning_query in tuning_raw_queries:
        tuning_tokens = _query_tokens(tuning_query)
        for calibration_query in calibration_raw_queries:
            calibration_tokens = _query_tokens(calibration_query)
            if not tuning_tokens and not calibration_tokens:
                continue
            similarity = len(tuning_tokens & calibration_tokens) / max(
                1, len(tuning_tokens | calibration_tokens)
            )
            if similarity >= 0.8:
                near_duplicate_count += 1
    tuning_corpus = str(
        (tuning_version.get("corpus_snapshot") or {}).get("checksum") or ""
    )
    calibration_corpus = str(
        (calibration_version.get("corpus_snapshot") or {}).get("checksum") or ""
    )
    reason_codes: list[str] = []
    if tuning_role != "strategy_tuning":
        reason_codes.append("tuning_role")
    if calibration_role != "threshold_calibration":
        reason_codes.append("calibration_role")
    if str(tuning_version.get("benchmark_contract_version") or "") != "rag-gold-v3":
        reason_codes.append("tuning_contract")
    if str(calibration_version.get("benchmark_contract_version") or "") != "rag-gold-v3":
        reason_codes.append("calibration_contract")
    tuning_manifest = tuning_version.get("qualification_manifest")
    calibration_manifest = calibration_version.get("qualification_manifest")
    if not (
        isinstance(tuning_manifest, dict)
        and tuning_manifest.get("status") == "qualified"
        and tuning_manifest.get("dataset_role") == "strategy_tuning"
    ):
        reason_codes.append("tuning_qualification")
    if not (
        isinstance(calibration_manifest, dict)
        and calibration_manifest.get("status") == "qualified"
        and calibration_manifest.get("dataset_role") == "threshold_calibration"
    ):
        reason_codes.append("calibration_qualification")
    if not tuning_version.get("version_id") or not tuning_version.get("published_at"):
        reason_codes.append("tuning_immutable")
    if not calibration_version.get("version_id") or not calibration_version.get(
        "published_at"
    ):
        reason_codes.append("calibration_immutable")
    if (
        str(tuning_version.get("eval_set_id") or "")
        == str(calibration_version.get("eval_set_id") or "")
        or str(tuning_version.get("checksum") or "")
        == str(calibration_version.get("checksum") or "")
    ):
        reason_codes.append("dataset_identity_overlap")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", tuning_corpus)
        or tuning_corpus != calibration_corpus
    ):
        reason_codes.append("corpus_snapshot_mismatch")
    if exact_overlap or near_duplicate_count:
        reason_codes.append("query_leakage")
    return {
        "version": "rag-tuning-dataset-pair-v1",
        "qualified": not reason_codes,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "tuning_role": tuning_role,
        "calibration_role": calibration_role,
        "query_overlap_count": len(exact_overlap),
        "near_duplicate_query_count": near_duplicate_count,
        "corpus_snapshot_checksum": tuning_corpus or None,
        "tuning_checksum": str(tuning_version.get("checksum") or ""),
        "calibration_checksum": str(calibration_version.get("checksum") or ""),
    }


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
    contexts = [
        item
        for item in targeting.get("context_refs") or []
        if isinstance(item, dict)
    ]
    verification = targeting.get("full_corpus_verification")
    verified = (
        isinstance(verification, dict)
        and verification.get("completed") is True
        and verification.get("contract_version") == "rag-no-result-verification-v1"
        and re.fullmatch(
            r"[0-9a-f]{64}",
            str(verification.get("corpus_snapshot_checksum") or ""),
        )
        is not None
        and int(verification.get("scanned_document_count") or 0) > 0
        and int(verification.get("scanned_source_block_count") or 0) > 0
    )
    stable_context = len(contexts) == 1 and bool(
        contexts[0].get("document_id")
        and contexts[0].get("source_block_id")
        and re.fullmatch(
            r"[0-9a-f]{64}", str(contexts[0].get("source_block_hash") or "")
        )
    )
    return bool(
        verified
        and stable_context
        and (
            query_type in {"confusable_content", "corpus_near"}
            or tags.intersection({"corpus_near", "confusable"})
        )
    )


def _normalized_query(value: Any) -> str:
    return re.sub(r"[^\w\u3400-\u9fff]+", "", str(value or "").casefold())


def _query_tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", str(value).casefold()))
