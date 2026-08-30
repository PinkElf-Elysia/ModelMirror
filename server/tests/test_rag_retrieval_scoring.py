from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from server.rag.chunking_receipt import (
    CHUNKING_RECEIPT_VERSION,
    candidate_namespace_fingerprint,
    chunker_profile_fingerprint,
)
from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import LexicalChunk, SqliteLexicalStore
from server.rag.rag_service import PipelineDraftValidationError, PipelineJobStateError, RagService
from server.rag.reranker import RerankItem, RerankOutcome
from server.rag.retrieval import RetrievalCandidate, RetrievalConfig
from server.rag.vector_store import LocalJsonVectorStore


def build_service(tmp_path: Path, *, reranker=None) -> RagService:
    storage = tmp_path / "storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
        reranker=reranker,
        llm_enabled=False,
    )


def candidate(
    chunk_id: str,
    *,
    vector_score: float | None = None,
    fulltext_score: float | None = None,
    rerank_score: float | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        doc_id=f"doc-{chunk_id}",
        document_name=f"{chunk_id}.txt",
        matched_text=chunk_id,
        context_text=chunk_id,
        vector_score=vector_score,
        fulltext_score=fulltext_score,
        rerank_score=rerank_score,
    )


def absolute_config(**updates: object) -> RetrievalConfig:
    return RetrievalConfig.from_mapping(
        {
            "mode": "hybrid",
            "min_vector_similarity": 0.8,
            "min_lexical_confidence": 0.7,
            "no_result_policy": "absolute_relevance_v1",
            **updates,
        }
    )


def test_new_v3_draft_is_explicitly_unconfigured_and_rejects_legacy_threshold_mix(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("absolute contract")

    draft = service.get_pipeline_draft(kb["id"])

    assert draft["retrieval_profile"]["no_result_policy"] == "absolute_relevance_v1"
    assert draft["retrieval_profile"]["threshold_contract_status"] == "unconfigured"
    assert "score_threshold" not in draft["retrieval_profile"]

    with pytest.raises(PipelineDraftValidationError, match="score_threshold"):
        service.update_pipeline_draft(
            kb["id"],
            {},
            retrieval_profile={
                "score_threshold": 0.5,
                "min_lexical_confidence": 0.5,
            },
        )
    with pytest.raises(PipelineDraftValidationError, match="score_threshold"):
        service.update_pipeline_draft(
            kb["id"],
            {},
            retrieval_profile={
                "score_threshold": 0.5,
                "min_vector_similarity": None,
            },
        )

    configured = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={
            "mode": "fulltext",
            "min_lexical_confidence": 0.5,
            "no_result_policy": "absolute_relevance_v1",
        },
    )
    assert configured["retrieval_profile"]["threshold_contract_status"] == "configured"
    cleared = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"min_lexical_confidence": None},
    )
    assert cleared["retrieval_profile"]["min_lexical_confidence"] is None
    assert cleared["retrieval_profile"]["threshold_contract_status"] == "unconfigured"


def test_old_threshold_only_is_explicit_legacy_diagnostic_for_v3(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("legacy diagnostic")

    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "fulltext", "score_threshold": 0.4},
    )

    assert draft["retrieval_profile"]["score_threshold"] == 0.4
    assert draft["retrieval_profile"]["threshold_contract_status"] == "legacy"
    assert draft["retrieval_profile"]["no_result_policy"] == (
        "legacy_fused_threshold_v2"
    )


def test_absolute_thresholds_reject_rank_one_and_are_tail_invariant() -> None:
    config = absolute_config(
        min_vector_similarity=1.0,
        min_lexical_confidence=1.0,
    )
    shared_vector = candidate("shared", vector_score=0.99)
    shared_lexical = candidate("shared", fulltext_score=0.99)

    base_vector, base_lexical, base_receipt = config.filter_absolute_channels(
        [shared_vector],
        [shared_lexical],
    )
    tail_vector, tail_lexical, tail_receipt = config.filter_absolute_channels(
        [shared_vector, candidate("tail", vector_score=0.01)],
        [shared_lexical],
    )

    assert base_vector == []
    assert base_lexical == []
    assert tail_vector == []
    assert tail_lexical == []
    base = next(item for item in base_receipt if item["chunk_id"] == "shared")
    expanded = next(item for item in tail_receipt if item["chunk_id"] == "shared")
    assert base == expanded
    assert base["accepted"] is False
    assert set(base["rejection_reason_codes"]) == {
        "below_min_vector_similarity",
        "below_min_lexical_confidence",
    }


def test_hybrid_candidate_enters_rrf_only_through_channels_that_pass() -> None:
    config = absolute_config()
    vector_items, lexical_items, receipt = config.filter_absolute_channels(
        [
            candidate("vector-pass", vector_score=0.9),
            candidate("lexical-pass", vector_score=0.2),
        ],
        [
            candidate("vector-pass", fulltext_score=0.1),
            candidate("lexical-pass", fulltext_score=0.9),
        ],
    )

    assert [item.chunk_id for item in vector_items] == ["vector-pass"]
    assert [item.chunk_id for item in lexical_items] == ["lexical-pass"]
    assert all(item["accepted"] is True for item in receipt)


def test_missing_required_raw_score_is_rejected_and_invalidates_receipt() -> None:
    config = RetrievalConfig.from_mapping(
        {
            "mode": "vector",
            "min_vector_similarity": 0.5,
            "no_result_policy": "absolute_relevance_v1",
        }
    )

    vector_items, _, receipt = config.filter_absolute_channels(
        [candidate("missing", vector_score=None)],
        [],
    )

    assert vector_items == []
    assert receipt == [
        {
            "chunk_id": "missing",
            "vector_score": None,
            "fulltext_score": None,
            "vector_passed": False,
            "fulltext_passed": None,
            "accepted": False,
            "raw_score_contract_valid": False,
            "rejection_reason_codes": ["missing_vector_similarity"],
        }
    ]


def test_non_finite_or_out_of_range_channel_scores_fail_closed() -> None:
    config = absolute_config(min_vector_similarity=0.0, min_lexical_confidence=0.0)

    vector_items, lexical_items, receipt = config.filter_absolute_channels(
        [candidate("bad-vector", vector_score=float("inf"))],
        [candidate("bad-lexical", fulltext_score=1.1)],
    )

    assert vector_items == []
    assert lexical_items == []
    assert all(item["raw_score_contract_valid"] is False for item in receipt)
    assert {reason for item in receipt for reason in item["rejection_reason_codes"]} == {
        "invalid_vector_similarity",
        "invalid_lexical_confidence",
    }
    json.dumps(receipt, allow_nan=False)


def test_absolute_channel_decisions_are_threshold_and_tail_invariant() -> None:
    rng = random.Random(20260827)
    vector_threshold = 0.63
    lexical_threshold = 0.41
    config = absolute_config(
        min_vector_similarity=vector_threshold,
        min_lexical_confidence=lexical_threshold,
    )
    vector_scores = [0.0, vector_threshold, 1.0] + [rng.random() for _ in range(40)]
    lexical_scores = [0.0, lexical_threshold, 1.0] + [rng.random() for _ in range(40)]
    vector_items = [
        candidate(f"vector-{index}", vector_score=score)
        for index, score in enumerate(vector_scores)
    ]
    lexical_items = [
        candidate(f"lexical-{index}", fulltext_score=score)
        for index, score in enumerate(lexical_scores)
    ]

    accepted_vector, accepted_lexical, base_receipt = config.filter_absolute_channels(
        vector_items,
        lexical_items,
    )
    _, _, expanded_receipt = config.filter_absolute_channels(
        vector_items + [candidate("irrelevant-vector-tail", vector_score=0.01)],
        lexical_items + [candidate("irrelevant-lexical-tail", fulltext_score=0.01)],
    )

    assert {item.chunk_id for item in accepted_vector} == {
        f"vector-{index}"
        for index, score in enumerate(vector_scores)
        if score >= vector_threshold
    }
    assert {item.chunk_id for item in accepted_lexical} == {
        f"lexical-{index}"
        for index, score in enumerate(lexical_scores)
        if score >= lexical_threshold
    }
    base_by_id = {item["chunk_id"]: item for item in base_receipt}
    expanded_by_id = {item["chunk_id"]: item for item in expanded_receipt}
    assert all(expanded_by_id[chunk_id] == item for chunk_id, item in base_by_id.items())


def test_unconfigured_diagnostic_contract_still_rejects_invalid_raw_scores() -> None:
    config = RetrievalConfig.from_mapping(
        {
            "mode": "hybrid",
            "min_vector_similarity": None,
            "min_lexical_confidence": None,
            "no_result_policy": "absolute_relevance_v1",
        }
    )

    vector_items, lexical_items, receipt = config.filter_absolute_channels(
        [candidate("invalid", vector_score=float("nan"))],
        [candidate("valid", fulltext_score=0.25)],
    )

    assert vector_items == []
    assert [item.chunk_id for item in lexical_items] == ["valid"]
    decisions = {item["chunk_id"]: item for item in receipt}
    assert decisions["invalid"]["accepted"] is False
    assert decisions["invalid"]["raw_score_contract_valid"] is False
    assert decisions["invalid"]["rejection_reason_codes"] == [
        "invalid_vector_similarity"
    ]
    assert decisions["valid"]["accepted"] is True
    json.dumps(receipt, allow_nan=False)


@pytest.mark.asyncio
async def test_v3_fulltext_does_not_treat_rank_fallback_as_absolute_confidence(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("lexical rank fallback")
    namespace = f"{kb['id']}::v3::fulltext"
    service.lexical_store.add_chunks(
        [
            LexicalChunk(
                chunk_id="rank-one",
                namespace=namespace,
                doc_id="doc-rank-one",
                document_name="rank-one.txt",
                text="x",
                chunk_index=0,
            )
        ]
    )
    config = RetrievalConfig.from_mapping(
        {
            "mode": "fulltext",
            "min_lexical_confidence": 1.0,
            "no_result_policy": "absolute_relevance_v1",
        }
    )

    result = await service._query_namespace(
        kb["id"],
        namespace,
        "x",
        config=config,
        lexical_ready=True,
        generate_answer=False,
    )

    assert result["sources"] == []
    assert result["retrieval"]["promotion_eligible"] is False
    assert result["retrieval"]["rejection_diagnostics"] == [
        {
            "chunk_id": "rank-one",
            "vector_score": None,
            "fulltext_score": None,
            "vector_passed": None,
            "fulltext_passed": False,
            "accepted": False,
            "raw_score_contract_valid": False,
            "rejection_reason_codes": ["missing_lexical_confidence"],
        }
    ]


@pytest.mark.asyncio
async def test_successful_rerank_uses_rerank_threshold_not_fused_score(
    tmp_path: Path,
) -> None:
    class LowScoreReranker:
        async def rerank(self, _query, documents, **_kwargs):
            return RerankOutcome(
                items=[RerankItem(documents[0].chunk_id, 0.2)],
                provider="api",
                model="contract-reranker",
                requested_input_count=len(documents),
                input_count=len(documents),
                input_char_count=sum(len(item.text) for item in documents),
                output_count=1,
            )

    service = build_service(tmp_path, reranker=LowScoreReranker())
    kb = service.create_knowledge_base("rerank absolute score")
    namespace = f"{kb['id']}::v3::fulltext"
    service.lexical_store.add_chunks(
        [
            LexicalChunk(
                chunk_id="candidate",
                namespace=namespace,
                doc_id="doc-candidate",
                document_name="candidate.txt",
                text="ORBIT evidence is present here",
                chunk_index=0,
            )
        ]
    )
    config = RetrievalConfig.from_mapping(
        {
            "mode": "fulltext",
            "top_k": 5,
            "min_lexical_confidence": 0.1,
            "min_rerank_score": 0.8,
            "no_result_policy": "absolute_relevance_v1",
            "rerank_enabled": True,
            "rerank_provider": "api",
        }
    )

    result = await service._query_namespace(
        kb["id"],
        namespace,
        "ORBIT evidence",
        config=config,
        lexical_ready=True,
        generate_answer=False,
    )

    assert result["sources"] == []
    assert result["retrieval"]["threshold_score_domain"] == "rerank_score"
    assert result["retrieval"]["rejection_diagnostics"][0]["rerank_passed"] is False


@pytest.mark.asyncio
async def test_rerank_failure_falls_back_to_absolute_channels_but_is_not_promotable(
    tmp_path: Path,
) -> None:
    class FailedReranker:
        async def rerank(self, _query, documents, **_kwargs):
            return RerankOutcome(
                items=[],
                provider="none",
                warning="rerank failed",
                fallback_reason="api:http_status_503",
                requested_input_count=len(documents),
                input_count=len(documents),
                input_char_count=sum(len(item.text) for item in documents),
            )

    service = build_service(tmp_path, reranker=FailedReranker())
    kb = service.create_knowledge_base("rerank fallback")
    namespace = f"{kb['id']}::v3::fulltext"
    service.lexical_store.add_chunks(
        [
            LexicalChunk(
                chunk_id="candidate",
                namespace=namespace,
                doc_id="doc-candidate",
                document_name="candidate.txt",
                text="ORBIT evidence is present here",
                chunk_index=0,
            )
        ]
    )
    config = RetrievalConfig.from_mapping(
        {
            "mode": "fulltext",
            "top_k": 5,
            "min_lexical_confidence": 0.1,
            "min_rerank_score": 0.8,
            "no_result_policy": "absolute_relevance_v1",
            "rerank_enabled": True,
            "rerank_provider": "api",
        }
    )

    result = await service._query_namespace(
        kb["id"],
        namespace,
        "ORBIT evidence",
        config=config,
        lexical_ready=True,
        generate_answer=False,
    )

    assert len(result["sources"]) == 1
    assert result["retrieval"]["threshold_score_domain"] == "channel_absolute_scores"
    assert result["retrieval"]["promotion_eligible"] is False
    assert "rerank_fail_open" in result["retrieval"]["promotion_ineligibility_reasons"]


def test_v3_promotion_requires_configured_thresholds_and_independent_calibration(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("promotion threshold evidence")
    version_id = "kpv_absolute_contract"
    namespace = f"{kb['id']}::v3::{version_id}::fulltext"
    chunker_profile = {
        "strategy": "recursive_estimated_token",
        "chunk_size": 500,
        "chunk_overlap": 50,
        "size_unit": "estimated_tokens",
        "token_estimator": "mixed_cjk_latin_v1",
        "chunk_contract_version": "rag-chunker-estimated-token-v1",
    }
    chunking_receipt = {
        "receipt_version": CHUNKING_RECEIPT_VERSION,
        "contract_version": "rag-chunker-estimated-token-v1",
        "strategy": "recursive_estimated_token",
        "size_unit": "estimated_tokens",
        "token_estimator": "mixed_cjk_latin_v1",
        "chunker_profile_fingerprint": chunker_profile_fingerprint(
            chunker_profile
        ),
        "candidate_version_id": version_id,
        "candidate_namespace_fingerprint": candidate_namespace_fingerprint(
            namespace
        ),
        "raw_candidate_count": 1,
        "heading_block_count": 0,
        "heading_prefix_truncated_count": 0,
        "generated_item_count": 0,
        "generated_item_chunk_count": 0,
        "generated_item_rejected_count": 0,
        "generated_item_rejection_reasons": {},
        "deduplicated_chunk_count": 0,
        "final_chunk_count": 1,
        "chunk_sequence_hash": "e" * 64,
    }
    base_version = {
        "version_id": version_id,
        "kb_id": kb["id"],
        "version": 1,
        "status": "ready",
        "namespace": namespace,
        "draft_id": f"draft_{kb['id']}",
        "draft_version": 1,
        "index_schema_version": 3,
        "config_snapshot": {
            "stages": {
                "stage_chunker": {
                    **chunker_profile,
                },
                "stage_processor": {
                    "parser_contract_version": "canonical-structured-parser-v2",
                },
            },
            "index_contract": {
                "vector": {
                    "required": False,
                },
                "lexical": {
                    "contract_version": "sqlite-fts5-lexical-v2",
                }
            },
            "retrieval_profile": {"mode": "fulltext"},
        },
        "content_index_contract": {
            "contract_version": "rag-content-index-contract-v1",
            "chunker_contract_version": "rag-chunker-estimated-token-v1",
            "lexical_contract_version": "sqlite-fts5-lexical-v2",
            "parser_contract_version": "canonical-structured-parser-v2",
            "status": "current",
            "components": {
                "chunker": "current",
                "lexical": "current",
                "parser": "current",
            },
        },
        "retrieval_profile": {
            "mode": "fulltext",
            "min_lexical_confidence": None,
            "no_result_policy": "absolute_relevance_v1",
        },
        "job_id": "job",
        "chunk_count": 1,
        "chunking_receipt": chunking_receipt,
        "source_summary": [
            {
                "source_id": "source-calibration",
                "content_hash": "c" * 64,
            }
        ],
        "created_at": 1.0,
        "activated_at": None,
    }
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id] = base_version
        metadata["pipeline_jobs"]["job"] = {
            "job_id": "job",
            "status": "succeeded",
            "candidate_version_id": version_id,
            "candidate_namespace": namespace,
            "config_snapshot": json.loads(
                json.dumps(base_version["config_snapshot"])
            ),
            "chunking_receipt": json.loads(json.dumps(chunking_receipt)),
        }
        service._write_metadata_unlocked(metadata)

    def assert_threshold_promotion_ready() -> None:
        service._assert_v3_threshold_promotion_ready(  # noqa: SLF001
            service.get_pipeline_version(version_id)
        )

    with pytest.raises(PipelineJobStateError, match="thresholds"):
        assert_threshold_promotion_ready()

    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id]["retrieval_profile"][
            "min_lexical_confidence"
        ] = 0.5
        service._write_metadata_unlocked(metadata)

    with pytest.raises(PipelineJobStateError, match="calibration"):
        assert_threshold_promotion_ready()

    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id]["threshold_calibration_evidence"] = {
            "contract_version": "rag-threshold-calibration-v1",
            "status": "qualified",
            "independent": True,
            "dataset_checksum": "a" * 64,
            "retrieval_profile_checksum": "b" * 64,
            "score_domains": ["lexical_confidence"],
        }
        service._write_metadata_unlocked(metadata)

    with pytest.raises(PipelineJobStateError, match="calibration"):
        assert_threshold_promotion_ready()

    version = service.get_pipeline_version(version_id)
    expected_profile_checksum = service._mapping_sha256(
        RetrievalConfig.from_mapping(version["retrieval_profile"]).payload()
    )
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id]["threshold_calibration_evidence"][
            "retrieval_profile_checksum"
        ] = expected_profile_checksum
        service._write_metadata_unlocked(metadata)

    with pytest.raises(PipelineJobStateError, match="calibration"):
        assert_threshold_promotion_ready()

    expected_configuration_fingerprint = service.pipeline_version_evidence(version_id)[
        "configuration_fingerprint"
    ]
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id]["threshold_calibration_evidence"][
            "configuration_fingerprint"
        ] = expected_configuration_fingerprint
        service._write_metadata_unlocked(metadata)

    with pytest.raises(PipelineJobStateError, match="calibration"):
        assert_threshold_promotion_ready()

    expected_source_manifest_fingerprint = service.pipeline_version_evidence(version_id)[
        "source_manifest_fingerprint"
    ]
    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id]["threshold_calibration_evidence"][
            "source_manifest_fingerprint"
        ] = expected_source_manifest_fingerprint
        metadata["pipeline_versions"][version_id]["threshold_calibration_evidence"][
            "score_domains"
        ] = None
        service._write_metadata_unlocked(metadata)

    with pytest.raises(PipelineJobStateError, match="calibration"):
        assert_threshold_promotion_ready()

    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id]["threshold_calibration_evidence"][
            "score_domains"
        ] = ["lexical_confidence"]
        metadata["pipeline_versions"][version_id]["source_summary"][0][
            "content_hash"
        ] = "d" * 64
        service._write_metadata_unlocked(metadata)

    with pytest.raises(PipelineJobStateError, match="calibration"):
        assert_threshold_promotion_ready()

    with service._metadata_lock:
        metadata = service._read_metadata_unlocked()
        metadata["pipeline_versions"][version_id]["source_summary"][0][
            "content_hash"
        ] = "c" * 64
        service._write_metadata_unlocked(metadata)

    assert_threshold_promotion_ready()
    with pytest.raises(PipelineJobStateError, match="Legacy content-index contracts"):
        service.activate_pipeline_version(version_id, promotion=True)


def test_legacy_v2_score_threshold_remains_readable() -> None:
    config = RetrievalConfig.from_mapping(
        {"mode": "vector", "score_threshold": 0.75}
    )

    assert config.score_threshold == 0.75
    assert config.payload()["score_threshold"] == 0.75
    assert config.payload()["threshold_contract_status"] == "legacy"
