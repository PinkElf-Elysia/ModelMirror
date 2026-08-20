from __future__ import annotations

from server.rag.strategy_tuning_qualification import (
    assess_chunk_sensitivity,
    build_tuning_readiness,
    ranking_fingerprint,
    realized_index_fingerprint,
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
            "context_evidence_ids": [f"evidence-{index}"] if expected_no_result else [],
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
