from __future__ import annotations

from itertools import permutations
from pathlib import Path

import pytest

from server.rag import retrieval as retrieval_module
from server.rag.embedder import EmbeddingClient
from server.rag.lexical_store import LexicalChunk, SqliteLexicalStore
from server.rag.rag_service import RagService
from server.rag.retrieval import RetrievalCandidate, RetrievalConfig
from server.rag.vector_store import LocalJsonVectorStore


def candidate(
    chunk_id: str,
    *,
    doc_id: str,
    text: str,
    score: float,
    source_block_id: str | None = None,
    parent_chunk_id: str | None = None,
    generated_item: bool = False,
    start_char: int = 0,
    end_char: int | None = None,
    page_number: int | None = 1,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        doc_id=doc_id,
        document_name=f"{doc_id}.txt",
        matched_text=text,
        context_text=text,
        source_block_id=source_block_id,
        parent_chunk_id=parent_chunk_id,
        generated_item=generated_item,
        start_char=start_char,
        end_char=len(text) if end_char is None else end_char,
        page_number=page_number,
        fused_score=score,
    )


def select(
    items: list[RetrievalCandidate],
    *,
    top_k: int = 10,
    max_chunks_per_document: int = 2,
):
    return retrieval_module.select_v3_candidates(
        items,
        top_k=top_k,
        max_chunks_per_document=max_chunks_per_document,
    )


def build_service(tmp_path: Path) -> RagService:
    storage = tmp_path / "storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        lexical_store=SqliteLexicalStore(storage / "lexical.sqlite3"),
        llm_enabled=False,
    )


def test_v3_defaults_to_two_chunks_per_document_and_validates_the_limit() -> None:
    config = RetrievalConfig.from_mapping(
        {"no_result_policy": "absolute_relevance_v1"}
    )

    assert config.max_chunks_per_document == 2
    assert config.payload()["max_chunks_per_document"] == 2
    with pytest.raises(ValueError, match="max_chunks_per_document"):
        RetrievalConfig.from_mapping(
            {
                "no_result_policy": "absolute_relevance_v1",
                "max_chunks_per_document": 0,
            }
        )


def test_v2_payload_and_selection_contract_do_not_gain_the_v3_limit(
    tmp_path: Path,
) -> None:
    config = RetrievalConfig.from_mapping(
        {"mode": "vector", "score_threshold": 0.25}
    )

    assert config.max_chunks_per_document is None
    assert "max_chunks_per_document" not in config.payload()

    explicit_v3_limit = RetrievalConfig.from_mapping(
        {
            "mode": "vector",
            "score_threshold": 0.25,
            "max_chunks_per_document": 7,
        }
    )
    assert explicit_v3_limit.max_chunks_per_document is None
    assert "max_chunks_per_document" not in explicit_v3_limit.payload()

    service = build_service(tmp_path)
    version = {
        "index_schema_version": 2,
        "retrieval_profile": {
            "mode": "vector",
            "score_threshold": 0.25,
        },
    }
    legacy_override = service._retrieval_config_for_version(
        version,
        {
            "no_result_policy": "absolute_relevance_v1",
            "max_chunks_per_document": 7,
        },
        top_k=None,
    )
    assert legacy_override.max_chunks_per_document is None
    assert "max_chunks_per_document" not in legacy_override.payload()


def test_normalized_text_and_source_block_duplicates_cannot_fill_top_k() -> None:
    outcome = select(
        [
            candidate(
                "primary",
                doc_id="doc-a",
                text="Alpha   BETA",
                score=0.99,
                source_block_id="block-a",
            ),
            candidate(
                "text-duplicate",
                doc_id="doc-b",
                text="  alpha beta  ",
                score=0.98,
                source_block_id="block-b",
            ),
            candidate(
                "block-duplicate",
                doc_id="doc-a",
                text="different child text",
                score=0.97,
                source_block_id="block-a",
                page_number=2,
            ),
            candidate(
                "independent",
                doc_id="doc-b",
                text="independent evidence",
                score=0.96,
                source_block_id="block-c",
            ),
        ]
    )

    assert [item.chunk_id for item in outcome.items] == ["primary", "independent"]
    assert outcome.candidate_counts == {
        "threshold": 4,
        "text_dedup": 3,
        "source_block_dedup": 2,
        "overlap_merge": 2,
        "document_limit": 2,
        "final": 2,
    }


def test_generated_parent_siblings_cannot_fill_top_k() -> None:
    outcome = select(
        [
            candidate(
                "generated-0",
                doc_id="doc-a",
                text="First bounded context segment.",
                score=0.99,
                parent_chunk_id=f"generated_v1_{'a' * 64}",
                generated_item=True,
            ),
            candidate(
                "generated-1",
                doc_id="doc-a",
                text="Second bounded context segment.",
                score=0.98,
                parent_chunk_id=f"generated_v1_{'a' * 64}",
                generated_item=True,
            ),
            candidate(
                "independent",
                doc_id="doc-b",
                text="Independent evidence.",
                score=0.97,
                parent_chunk_id=f"generated_v1_{'b' * 64}",
                generated_item=True,
            ),
        ],
        top_k=3,
        max_chunks_per_document=3,
    )

    assert [item.chunk_id for item in outcome.items] == [
        "generated-0",
        "independent",
    ]
    assert outcome.candidate_counts["source_block_dedup"] == 2


def test_different_generated_parents_remain_independent() -> None:
    outcome = select(
        [
            candidate(
                "generated-a",
                doc_id="doc-a",
                text="Context A.",
                score=0.99,
                parent_chunk_id=f"generated_v1_{'a' * 64}",
                generated_item=True,
            ),
            candidate(
                "generated-b",
                doc_id="doc-a",
                text="Context B.",
                score=0.98,
                parent_chunk_id=f"generated_v1_{'b' * 64}",
                generated_item=True,
            ),
        ],
        top_k=2,
        max_chunks_per_document=2,
    )

    assert [item.chunk_id for item in outcome.items] == [
        "generated-a",
        "generated-b",
    ]


def test_generated_parent_identity_is_scoped_to_document() -> None:
    outcome = select(
        [
            candidate(
                "doc-a-generated",
                doc_id="doc-a",
                text="Document A context.",
                score=0.99,
                parent_chunk_id=f"generated_v1_{'a' * 64}",
                generated_item=True,
            ),
            candidate(
                "doc-b-generated",
                doc_id="doc-b",
                text="Document B context.",
                score=0.98,
                parent_chunk_id=f"generated_v1_{'a' * 64}",
                generated_item=True,
            ),
        ],
        top_k=2,
        max_chunks_per_document=2,
    )

    assert [item.chunk_id for item in outcome.items] == [
        "doc-a-generated",
        "doc-b-generated",
    ]


def test_ordinary_parent_child_candidates_without_source_block_remain_independent() -> None:
    outcome = select(
        [
            candidate(
                "ordinary-child-a",
                doc_id="doc-a",
                text="Distinct child A.",
                score=0.99,
                parent_chunk_id="legacy-parent-window",
            ),
            candidate(
                "ordinary-child-b",
                doc_id="doc-a",
                text="Distinct child B.",
                score=0.98,
                parent_chunk_id="legacy-parent-window",
                generated_item=False,
            ),
        ],
        top_k=2,
        max_chunks_per_document=2,
    )

    assert [item.chunk_id for item in outcome.items] == [
        "ordinary-child-a",
        "ordinary-child-b",
    ]
    assert outcome.candidate_counts["source_block_dedup"] == 2


def test_document_limit_prevents_one_document_from_saturating_top_ten() -> None:
    items = [
        candidate(
            f"doc-a-{index}",
            doc_id="doc-a",
            text=f"evidence a {index}",
            score=1 - index / 100,
            source_block_id=f"block-a-{index}",
        )
        for index in range(12)
    ]
    items.extend(
        candidate(
            f"doc-{index}",
            doc_id=f"doc-{index}",
            text=f"evidence {index}",
            score=0.5 - index / 100,
            source_block_id=f"block-{index}",
        )
        for index in range(1, 10)
    )

    outcome = select(items, top_k=10, max_chunks_per_document=2)

    assert len(outcome.items) == 10
    assert sum(item.doc_id == "doc-a" for item in outcome.items) == 2
    assert outcome.candidate_counts["document_limit"] == 11
    assert outcome.candidate_counts["final"] == 10


def test_two_distinct_source_blocks_from_one_document_remain_available() -> None:
    outcome = select(
        [
            candidate(
                "evidence-one",
                doc_id="doc-a",
                text="first fact",
                score=0.9,
                source_block_id="block-one",
            ),
            candidate(
                "evidence-two",
                doc_id="doc-a",
                text="second fact",
                score=0.8,
                source_block_id="block-two",
            ),
            candidate(
                "evidence-three",
                doc_id="doc-a",
                text="third fact",
                score=0.7,
                source_block_id="block-three",
            ),
        ],
        max_chunks_per_document=2,
    )

    assert [item.chunk_id for item in outcome.items] == [
        "evidence-one",
        "evidence-two",
    ]
    assert outcome.candidate_counts["source_block_dedup"] == 3
    assert outcome.candidate_counts["document_limit"] == 2


def test_diversity_preserves_the_upstream_rank_between_independent_candidates() -> None:
    outcome = select(
        [
            candidate(
                "upstream-rank-one",
                doc_id="doc-a",
                text="rank one",
                score=0.2,
                source_block_id="block-a",
            ),
            candidate(
                "upstream-rank-two",
                doc_id="doc-b",
                text="rank two",
                score=0.9,
                source_block_id="block-b",
            ),
        ]
    )

    assert [item.chunk_id for item in outcome.items] == [
        "upstream-rank-one",
        "upstream-rank-two",
    ]


def test_overlap_merge_keeps_highest_score_primary_and_document_order_text() -> None:
    outcome = select(
        [
            candidate(
                "primary",
                doc_id="doc-a",
                text="fghijklmno",
                score=0.9,
                source_block_id="block-a",
                start_char=5,
                end_char=15,
            ),
            candidate(
                "earlier-sibling",
                doc_id="doc-a",
                text="abcdefghij",
                score=0.8,
                source_block_id="block-a",
                start_char=0,
                end_char=10,
            ),
        ]
    )

    assert len(outcome.items) == 1
    merged = outcome.items[0]
    assert merged.chunk_id == "primary"
    assert merged.matched_text == "abcdefghijklmno"
    assert merged.context_text == "abcdefghijklmno"
    assert (merged.start_char, merged.end_char) == (0, 15)
    assert merged.merged_chunk_ids == ("earlier-sibling",)
    assert outcome.overlap_merged_chunk_count == 1


def test_overlap_chain_is_complete_and_independent_of_candidate_order() -> None:
    chain = [
        candidate(
            "left",
            doc_id="doc-a",
            text="abcdefghij",
            score=0.7,
            source_block_id="block-a",
            start_char=0,
            end_char=10,
        ),
        candidate(
            "middle",
            doc_id="doc-a",
            text="fghijklmno",
            score=0.8,
            source_block_id="block-a",
            start_char=5,
            end_char=15,
        ),
        candidate(
            "right-primary",
            doc_id="doc-a",
            text="klmnopqrst",
            score=0.9,
            source_block_id="block-a",
            start_char=10,
            end_char=20,
        ),
    ]

    receipts = []
    for ordering in permutations(chain):
        outcome = select(list(ordering))
        assert len(outcome.items) == 1
        merged = outcome.items[0]
        receipts.append(
            (
                merged.chunk_id,
                merged.matched_text,
                merged.start_char,
                merged.end_char,
                frozenset(merged.merged_chunk_ids),
                outcome.overlap_merged_chunk_count,
            )
        )

    assert set(receipts) == {
        (
            "right-primary",
            "abcdefghijklmnopqrst",
            0,
            20,
            frozenset({"left", "middle"}),
            2,
        )
    }


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            candidate(
                "page-one",
                doc_id="doc-a",
                text="abcdefghij",
                score=0.9,
                source_block_id="block-a",
                start_char=0,
                end_char=10,
                page_number=1,
            ),
            candidate(
                "page-two",
                doc_id="doc-a",
                text="fghijklmno",
                score=0.8,
                source_block_id="block-a",
                start_char=5,
                end_char=15,
                page_number=2,
            ),
        ),
        (
            candidate(
                "block-one",
                doc_id="doc-a",
                text="abcdefghij",
                score=0.9,
                source_block_id="block-one",
                start_char=0,
                end_char=10,
            ),
            candidate(
                "block-two",
                doc_id="doc-a",
                text="fghijklmno",
                score=0.8,
                source_block_id="block-two",
                start_char=5,
                end_char=15,
            ),
        ),
        (
            candidate(
                "unproven-one",
                doc_id="doc-a",
                text="abcdefghij",
                score=0.9,
                source_block_id="block-a",
                start_char=0,
                end_char=10,
            ),
            candidate(
                "unproven-two",
                doc_id="doc-a",
                text="XXXXXklmno",
                score=0.8,
                source_block_id="block-a",
                start_char=5,
                end_char=15,
            ),
        ),
        (
            candidate(
                "range-one",
                doc_id="doc-a",
                text="abcdefghij",
                score=0.9,
                source_block_id="block-a",
                start_char=0,
                end_char=100,
            ),
            candidate(
                "range-two",
                doc_id="doc-a",
                text="fghijklmno",
                score=0.8,
                source_block_id="block-a",
                start_char=95,
                end_char=105,
            ),
        ),
    ],
)
def test_cross_page_cross_block_or_unproven_overlap_is_not_merged(
    left: RetrievalCandidate,
    right: RetrievalCandidate,
) -> None:
    outcome = select([left, right])

    assert outcome.overlap_merged_chunk_count == 0
    assert all(item.merged_chunk_ids == () for item in outcome.items)


def test_stage_counts_and_order_are_deterministic_and_attribute_drops() -> None:
    items = [
        candidate(
            "z",
            doc_id="doc-a",
            text="same text",
            score=0.8,
            source_block_id="block-z",
        ),
        candidate(
            "a",
            doc_id="doc-b",
            text="same   text",
            score=0.8,
            source_block_id="block-a",
        ),
        candidate(
            "b",
            doc_id="doc-b",
            text="other text",
            score=0.7,
            source_block_id="block-b",
        ),
        candidate(
            "c",
            doc_id="doc-b",
            text="third text",
            score=0.6,
            source_block_id="block-c",
        ),
    ]

    first = select(items, top_k=2, max_chunks_per_document=1)
    second = select(list(items), top_k=2, max_chunks_per_document=1)

    assert [item.chunk_id for item in first.items] == ["a"]
    assert [item.chunk_id for item in second.items] == ["a"]
    assert first.candidate_counts == second.candidate_counts == {
        "threshold": 4,
        "text_dedup": 3,
        "source_block_dedup": 3,
        "overlap_merge": 3,
        "document_limit": 1,
        "final": 1,
    }


@pytest.mark.asyncio
async def test_v3_query_exposes_replayable_diversity_stage_counts(
    tmp_path: Path,
) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("diversity diagnostics")
    namespace = f"{kb['id']}::v3::fulltext"
    chunks = [
        LexicalChunk(
            chunk_id="a-one",
            namespace=namespace,
            doc_id="doc-a",
            document_name="a.txt",
            text="ORBIT alpha",
            chunk_index=0,
            source_block_id="block-a",
            start_char=0,
            end_char=11,
            page_number=1,
        ),
        LexicalChunk(
            chunk_id="a-two",
            namespace=namespace,
            doc_id="doc-a",
            document_name="a.txt",
            text="ORBIT beta",
            chunk_index=1,
            source_block_id="block-a",
            start_char=20,
            end_char=30,
            page_number=2,
        ),
        LexicalChunk(
            chunk_id="a-three",
            namespace=namespace,
            doc_id="doc-a",
            document_name="a.txt",
            text="ORBIT gamma",
            chunk_index=2,
            source_block_id="block-b",
            start_char=40,
            end_char=51,
            page_number=3,
        ),
        LexicalChunk(
            chunk_id="b-one",
            namespace=namespace,
            doc_id="doc-b",
            document_name="b.txt",
            text="ORBIT delta",
            chunk_index=0,
            source_block_id="block-c",
            start_char=0,
            end_char=11,
            page_number=1,
        ),
    ]
    service.lexical_store.add_chunks(chunks)
    config = RetrievalConfig.from_mapping(
        {
            "mode": "fulltext",
            "top_k": 5,
            "min_lexical_confidence": 0.0,
            "no_result_policy": "absolute_relevance_v1",
            "max_chunks_per_document": 1,
        }
    )

    result = await service._query_namespace(
        kb["id"],
        namespace,
        "ORBIT",
        config=config,
        lexical_ready=True,
        generate_answer=False,
    )

    assert len(result["sources"]) == 2
    assert result["retrieval"]["candidate_stage_counts"] == {
        "raw": 4,
        "threshold": 4,
        "text_dedup": 4,
        "source_block_dedup": 3,
        "overlap_merge": 3,
        "document_limit": 2,
        "final": 2,
    }


@pytest.mark.asyncio
async def test_v3_query_returns_safe_overlap_merge_receipt(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    kb = service.create_knowledge_base("overlap receipt")
    namespace = f"{kb['id']}::v3::fulltext"
    service.lexical_store.add_chunks(
        [
            LexicalChunk(
                chunk_id="earlier",
                namespace=namespace,
                doc_id="doc-a",
                document_name="a.txt",
                text="prefix ORBIT",
                chunk_index=0,
                source_block_id="block-a",
                start_char=0,
                end_char=12,
                page_number=1,
            ),
            LexicalChunk(
                chunk_id="later",
                namespace=namespace,
                doc_id="doc-a",
                document_name="a.txt",
                text="ORBIT suffix",
                chunk_index=1,
                source_block_id="block-a",
                start_char=7,
                end_char=19,
                page_number=1,
            ),
        ]
    )
    config = RetrievalConfig.from_mapping(
        {
            "mode": "fulltext",
            "top_k": 5,
            "min_lexical_confidence": 0.0,
            "no_result_policy": "absolute_relevance_v1",
        }
    )

    result = await service._query_namespace(
        kb["id"],
        namespace,
        "ORBIT",
        config=config,
        lexical_ready=True,
        generate_answer=False,
    )

    assert len(result["sources"]) == 1
    source = result["sources"][0]
    assert source["matched_text"] == "prefix ORBIT suffix"
    assert source["text"] == "prefix ORBIT suffix"
    assert (source["start_char"], source["end_char"]) == (0, 19)
    assert len(source["merged_chunk_ids"]) == 1
    assert {source["chunk_id"], *source["merged_chunk_ids"]} == {
        "earlier",
        "later",
    }
    assert result["retrieval"]["overlap_merged_chunk_count"] == 1
