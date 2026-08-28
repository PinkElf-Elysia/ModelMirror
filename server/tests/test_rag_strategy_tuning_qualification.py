from __future__ import annotations

from server.rag.strategy_tuning_qualification import (
    assess_chunk_sensitivity,
    build_threshold_calibration_readiness,
    build_tuning_readiness,
    ranking_fingerprint,
    realized_index_fingerprint,
    validate_tuning_dataset_pair,
)


def _case(
    index: int,
    *,
    query_type: str,
    expected_no_result: bool = False,
    review_status: str = "not_required",
    reference_count: int = 1,
) -> dict:
    refs = []
    if not expected_no_result:
        refs = [
            {
                "document_id": "doc-a",
                "chunk_id": f"chunk-{index}-{ref_index}",
                "source_block_id": f"block-{index}-{ref_index}",
                "match_mode": "source_block",
            }
            for ref_index in range(reference_count)
        ]
    return {
        "case_id": f"case-{index}",
        "query": f"query {index}",
        "expected_refs": refs,
        "expected_no_result": expected_no_result,
        "review_status": review_status,
        "tags": (
            [query_type, "corpus_near", "hard_negative"]
            if expected_no_result
            else [query_type]
        ),
        "targeting": {
            "blueprint_id": f"blueprint-{index}",
            "query_type": query_type,
            "context_refs": (
                [
                    {
                        "document_id": "doc-a",
                        "source_block_id": f"context-block-{index}",
                        "source_block_hash": f"{index:064x}"[-64:],
                    }
                ]
                if expected_no_result
                else []
            ),
            "full_corpus_verification": (
                {
                    "contract_version": "rag-no-result-verification-v1",
                    "completed": True,
                    "method": "full_corpus_lexical_scan_v1",
                    "query_hash": f"{index + 1000:064x}"[-64:],
                    "corpus_snapshot_checksum": "c" * 64,
                    "scanned_document_count": 1,
                    "scanned_source_block_count": 50,
                    "top_matches": [],
                }
                if expected_no_result
                else None
            ),
        },
    }


def _qualified_cases() -> list[dict]:
    cases = []
    cases.extend(
        _case(index, query_type="factual_lookup") for index in range(8)
    )
    cases.extend(
        _case(index + 8, query_type="paraphrase") for index in range(6)
    )
    cases.extend(
        _case(index + 14, query_type="section_context") for index in range(8)
    )
    cases.extend(
        _case(
            index + 22,
            query_type="multi_evidence",
            reference_count=2,
        )
        for index in range(8)
    )
    cases.extend(
        _case(
            index + 30,
            query_type="no_result",
            expected_no_result=True,
            review_status="approved",
            reference_count=0,
        )
        for index in range(12)
    )
    return cases


def test_catalog_pack_is_report_only_even_with_enough_cases() -> None:
    readiness = build_tuning_readiness(
        {
            "origin": "benchmark_catalog",
            "catalog_ref": {"pack_id": "modelmirror-rag-foundation-bilingual-v1"},
            "cases": _qualified_cases(),
            "calibration": {"status": "calibrated"},
        }
    )

    assert readiness["benchmark_role"] == "regression_guard"
    assert readiness["status"] == "report_only"
    assert readiness["selection_eligible"] is False
    assert any(item["check_id"] == "selection_role" for item in readiness["checks"])


def test_targeted_benchmark_qualifies_each_search_dimension() -> None:
    readiness = build_tuning_readiness(
        {
            "origin": "generated",
            "benchmark_role": "strategy_tuning",
            "cases": _qualified_cases(),
            "calibration": {"status": "calibrated"},
        }
    )

    assert readiness["status"] == "ready"
    assert readiness["selection_eligible"] is True
    assert readiness["dimensions"]["retrieval"]["eligible"] is True
    assert readiness["dimensions"]["threshold"]["eligible"] is True
    assert readiness["dimensions"]["chunking"]["eligible"] is True
    assert readiness["counts"]["reviewed_hard_negative"] == 12
    assert readiness["coverage"]["density"] == {
        "multi_dense": 8,
        "single_dense": 8,
        "sparse": 14,
    }


def test_hard_negative_requires_explicit_corpus_near_or_confusable_mark() -> None:
    cases = _qualified_cases()
    for case in cases:
        if case["expected_no_result"]:
            case["tags"] = ["hard_negative"]
            case["targeting"]["query_type"] = "no_result"

    readiness = build_tuning_readiness(
        {
            "origin": "generated",
            "benchmark_role": "strategy_tuning",
            "cases": cases,
            "calibration": {"status": "calibrated"},
        }
    )

    assert readiness["counts"]["reviewed_no_result"] == 12
    assert readiness["counts"]["reviewed_hard_negative"] == 0
    assert readiness["dimensions"]["threshold"]["eligible"] is False


def test_hard_negative_requires_full_corpus_verification_and_stable_context() -> None:
    cases = _qualified_cases()
    negatives = [case for case in cases if case["expected_no_result"]]
    for case in negatives:
        case["targeting"].pop("full_corpus_verification")

    readiness = build_threshold_calibration_readiness(
        {
            "version_id": "evalsetver_calibration",
            "published_at": 1,
            "origin": "generated",
            "benchmark_role": "threshold_calibration",
            "benchmark_contract_version": "rag-gold-v3",
            "qualification_manifest": {
                "status": "qualified",
                "dataset_role": "threshold_calibration",
            },
            "cases": cases,
            "calibration": {"status": "calibrated"},
        }
    )

    assert readiness["eligible"] is False
    assert readiness["counts"]["reviewed_hard_negative"] == 0
    assert "hard_negative_verification" in readiness["reason_codes"]


def test_tuning_and_calibration_pair_rejects_roles_overlap_and_near_duplicates() -> None:
    tuning = {
        "version_id": "evalsetver_tuning",
        "eval_set_id": "evalset_tuning",
        "published_at": 1,
        "benchmark_role": "strategy_tuning",
        "benchmark_contract_version": "rag-gold-v3",
        "checksum": "a" * 64,
        "qualification_manifest": {
            "status": "qualified",
            "dataset_role": "strategy_tuning",
        },
        "corpus_snapshot": {"checksum": "c" * 64},
        "cases": _qualified_cases(),
        "calibration": {"status": "calibrated"},
    }
    calibration = {
        "version_id": "evalsetver_calibration",
        "eval_set_id": "evalset_calibration",
        "published_at": 1,
        "benchmark_role": "threshold_calibration",
        "benchmark_contract_version": "rag-gold-v3",
        "checksum": "b" * 64,
        "qualification_manifest": {
            "status": "qualified",
            "dataset_role": "threshold_calibration",
        },
        "corpus_snapshot": {"checksum": "c" * 64},
        "cases": [
            {**case, "case_id": f"cal-{case['case_id']}", "query": f"calibration {case['query']}"}
            for case in _qualified_cases()
        ],
        "calibration": {"status": "calibrated"},
    }

    qualified = validate_tuning_dataset_pair(tuning, calibration)
    assert qualified["qualified"] is True
    assert qualified["query_overlap_count"] == 0

    held_out = {**tuning, "benchmark_role": "held_out_qualification"}
    wrong_role = validate_tuning_dataset_pair(held_out, calibration)
    assert wrong_role["qualified"] is False
    assert "tuning_role" in wrong_role["reason_codes"]

    near_duplicate_tuning = {
        **tuning,
        "cases": [
            {
                **tuning["cases"][0],
                "query": "one two three four five six seven eight nine alpha",
            },
            *tuning["cases"][1:],
        ],
    }
    duplicate = {
        **calibration,
        "cases": [
            {
                **calibration["cases"][0],
                "query": "one two three four five six seven eight nine beta",
            },
            *calibration["cases"][1:],
        ],
    }
    leaked = validate_tuning_dataset_pair(near_duplicate_tuning, duplicate)
    assert leaked["qualified"] is False
    assert leaked["query_overlap_count"] == 0
    assert leaked["near_duplicate_query_count"] >= 1
    assert "query_leakage" in leaked["reason_codes"]


def test_small_targeted_set_is_insufficient_and_does_not_enable_threshold() -> None:
    readiness = build_tuning_readiness(
        {
            "origin": "generated",
            "cases": _qualified_cases()[:12],
            "calibration": {"status": "calibrated"},
        }
    )

    assert readiness["status"] == "insufficient_data"
    assert readiness["selection_eligible"] is False
    assert readiness["dimensions"]["threshold"]["eligible"] is False
    assert readiness["counts"]["positive"] == 12


def test_generated_tuning_evidence_must_match_fixed_knowledge_version() -> None:
    readiness = build_tuning_readiness(
        {
            "origin": "generated",
            "benchmark_role": "strategy_tuning",
            "provenance": {"pipeline_version_id": "version-old"},
            "cases": _qualified_cases(),
            "calibration": {"status": "calibrated"},
        },
        target_version_id="version-current",
    )

    assert readiness["selection_eligible"] is False
    assert any(
        item["check_id"] == "target_version_snapshot" and not item["passed"]
        for item in readiness["checks"]
    )


def test_realized_and_ranking_fingerprints_ignore_sensitive_content() -> None:
    first = realized_index_fingerprint(
        chunker={"strategy": "recursive_character", "chunk_size": 400},
        cost={"chunk_count": 12},
        document_results=[
            {"source_id": "source-a", "chunk_count": 12, "block_count": 4}
        ],
    )
    second = realized_index_fingerprint(
        chunker={"strategy": "recursive_character", "chunk_size": 900},
        cost={"chunk_count": 12},
        document_results=[
            {"source_id": "source-a", "chunk_count": 12, "block_count": 4}
        ],
    )
    changed = realized_index_fingerprint(
        chunker={"strategy": "parent_child"},
        cost={"chunk_count": 18},
        document_results=[
            {"source_id": "source-a", "chunk_count": 18, "block_count": 4}
        ],
    )

    assert first == second
    assert changed != first
    assert ranking_fingerprint(
        [{"case_id": "case-a", "sources": [{"chunk_id": "chunk-a", "text": "secret"}]}]
    ) == ranking_fingerprint(
        [{"case_id": "case-a", "sources": [{"chunk_id": "chunk-a", "text": "other"}]}]
    )


def test_chunk_sensitivity_requires_distinct_realized_or_ranking_outcomes() -> None:
    candidates = [
        {
            "retrieval_input_checksum": "probe",
            "realized_index_fingerprint": "same-index",
            "ranking_fingerprint": "same-ranking",
        },
        {
            "retrieval_input_checksum": "probe",
            "realized_index_fingerprint": "same-index",
            "ranking_fingerprint": "same-ranking",
        },
    ]
    insufficient = assess_chunk_sensitivity(
        candidates,
        probe_retrieval_checksum="probe",
        enabled=True,
    )
    assert insufficient["status"] == "insufficient"
    assert insufficient["unique_realized_outcomes"] == 1

    candidates[1]["ranking_fingerprint"] = "different-ranking"
    sufficient = assess_chunk_sensitivity(
        candidates,
        probe_retrieval_checksum="probe",
        enabled=True,
    )
    assert sufficient["status"] == "sufficient"
    assert sufficient["unique_realized_outcomes"] == 2
