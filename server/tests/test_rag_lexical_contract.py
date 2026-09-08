"""Lexical-v2 behavior attacks. No network or Provider is needed by this suite."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from server.rag.lexical_store import LexicalChunk, SqliteLexicalStore
from server.tests.rag_legacy_lexical_fixture import (
    LegacyLexicalRow,
    create_legacy_lexical_fixture,
)


NAMESPACE = "kb::v3::lexical_contract_test::fulltext"


def chunk(chunk_id: str, text: str, namespace: str = NAMESPACE) -> LexicalChunk:
    return LexicalChunk(chunk_id, namespace, "doc", "fixture.md", text, 0)


@pytest.fixture
def store(tmp_path: Path) -> SqliteLexicalStore:
    return SqliteLexicalStore(tmp_path / "lexical.sqlite3")


def test_term_frequency_changes_bm25_not_absolute_confidence(store) -> None:
    # Equal lengths and identical vocabulary: only term frequency differs.
    store.add_chunks([
        chunk("a_once", "alpha beta beta beta beta beta"),
        chunk("z_repeated", "alpha alpha alpha alpha alpha beta"),
    ])
    results = store.query(NAMESPACE, "alpha", 5)
    assert [item.chunk_id for item in results] == ["z_repeated", "a_once"]
    assert [item.score for item in results] == [1.0, 1.0]


def test_forwarded_lexical_candidates_are_not_the_final_retrieval_top_k(store) -> None:
    from server.rag.evaluation import _lexical_query_receipt_is_current

    store.add_chunks([chunk(f"chunk-{index}", "alpha") for index in range(8)])
    outcome = store.query_with_receipt(NAMESPACE, "alpha", 12, candidate_top_k=3)
    assert outcome.receipt["candidate_limit"] == 24
    assert outcome.receipt["final_count"] == len(outcome.results) == 8
    # Thresholds, fusion and diversity still run before the final version Top-K.
    assert _lexical_query_receipt_is_current(outcome.receipt, 3, "alpha")


@pytest.mark.parametrize("query", ["ticket ZX-4812", "ticket zx4812", "ticket ZX_4812"])
def test_complete_identifier_is_required_not_its_components(store, query) -> None:
    store.add_chunks([
        chunk("partial", "ticket zx 4812 for maintenance"),
        chunk("complete", query + " for maintenance"),
        chunk("different", "ticket ZX-4813 for maintenance"),
    ])
    assert [item.chunk_id for item in store.query(NAMESPACE, query, 5)] == ["complete"]


@pytest.mark.parametrize("word", ["ragidentifier", "ragidentifiers", "ragidentifier" + "a" * 64])
def test_plain_word_matching_internal_prefix_is_not_a_mandatory_identifier(store, word) -> None:
    store.add_chunks([chunk("ordinary_match", "alpha beta")])
    outcome = store.query_with_receipt(NAMESPACE, f"{word} alpha beta", 5)
    assert [item.chunk_id for item in outcome.results] == ["ordinary_match"]
    assert outcome.receipt["mandatory_identifier_count"] == 0
    assert outcome.receipt["effective_term_count"] == 3
    assert outcome.receipt["required_term_count"] == 2


def test_phrase_requires_order_and_adjacency(store) -> None:
    store.add_chunks([
        chunk("correct", "red blue storage"),
        chunk("reversed", "blue red storage"),
        chunk("separated", "red green blue storage"),
    ])
    assert [item.chunk_id for item in store.query(NAMESPACE, '"red blue"', 5)] == ["correct"]


@pytest.mark.parametrize("identifier,near", [("C++", "C"), ("C#", "C"), ("std::vector", "std vector")])
@pytest.mark.parametrize("quoted", [False, True])
def test_code_identifier_does_not_degrade_into_plain_words(store, identifier, near, quoted):
    store.add_chunks([chunk("exact", identifier + " guide"), chunk("near", near + " guide")])
    query = ('"' + identifier + '"' if quoted else identifier) + " guide"
    assert [item.chunk_id for item in store.query(NAMESPACE, query, 5)] == ["exact"]


@pytest.mark.parametrize("count,required", [(1, 1), (2, 2), (3, 2), (4, 2), (5, 3), (6, 4), (10, 6)])
def test_minimum_should_match_boundaries(store, count, required) -> None:
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()[:count]
    store.add_chunks([
        chunk("enough", " ".join(words[:required])),
        chunk("insufficient", " ".join(words[:required - 1]) + " unrelated"),
    ])
    assert [item.chunk_id for item in store.query(NAMESPACE, " ".join(words), 5)] == ["enough"]


def test_cjk_query_rejects_one_common_bigram_and_single_character(store) -> None:
    store.add_chunks([
        chunk("weak", "数据的"),
        chunk("complete", "数据库备份恢复"),
    ])
    assert [item.chunk_id for item in store.query(NAMESPACE, "数据库备份恢复", 5)] == ["complete"]
    assert store.query(NAMESPACE, "的", 5) == []


def test_confidence_uses_unique_terms_and_is_independent_of_result_limit(store) -> None:
    store.add_chunks([
        chunk("a", "alpha beta"),
        chunk("b", "alpha gamma"),
        chunk("c", "beta gamma"),
        chunk("tail", "unrelated material"),
    ])
    small = store.query(NAMESPACE, "alpha beta gamma", 1)
    large = store.query(NAMESPACE, "alpha alpha beta gamma", 100)
    assert small
    assert small[0].score == next(item.score for item in large if item.chunk_id == small[0].chunk_id)
    assert all(item.score_contract == "weighted_term_coverage_v1" for item in large)


def test_legacy_schema_and_rows_are_not_rewritten_by_v2(tmp_path) -> None:
    path = tmp_path / "mixed.sqlite3"
    create_legacy_lexical_fixture(path, [LegacyLexicalRow("same_id", "legacy", "old_doc", "old.md", "red blue")])
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT sql FROM sqlite_master WHERE name='rag_chunks'").fetchone()
        old_tokens = connection.execute("SELECT * FROM rag_chunks_fts").fetchall()
    store = SqliteLexicalStore(path)
    store.add_chunks([chunk("same_id", "green orange")])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT sql FROM sqlite_master WHERE name='rag_chunks'").fetchone() == before
        assert connection.execute("SELECT * FROM rag_chunks_fts").fetchall() == old_tokens
    assert [item.chunk_id for item in store.query("legacy", '"blue red"', 5)] == ["same_id"]
    assert store.query(NAMESPACE, "red blue", 5) == []
    with pytest.raises(ValueError, match="rag_lexical_contract_legacy_read_only"):
        store.add_chunks([chunk("new", "anything", "legacy")])


def test_v2_token_stream_preserves_mixed_order_repetitions_and_nfkc() -> None:
    from server.rag.lexical_store import tokenize_for_search_v2

    assert tokenize_for_search_v2("Ａlpha 数据库 alpha") == "alpha 数据 据库 alpha"
    assert tokenize_for_search_v2("中文中文") == "中文 文中 中文"
    assert "数据库" not in tokenize_for_search_v2("数据库").split()


def test_receipt_is_request_local_bounded_deterministic_and_text_free(store) -> None:
    store.add_chunks([chunk(str(index), "alpha beta ZX-4812 secret") for index in range(60)])
    first = store.query_with_receipt(NAMESPACE, 'alpha beta "ZX-4812 secret"', 2)
    second = store.query_with_receipt(NAMESPACE, "alpha", 1)
    assert first.receipt["contract_version"] == "sqlite-fts5-lexical-v2"
    assert first.receipt["query_policy"] == "minimum_should_match_auto_v1"
    assert first.receipt["candidate_limit"] == 16
    assert first.receipt["initial_candidate_count"] == 16
    assert first.receipt["candidate_pool_saturated"] is True
    assert first.receipt["required_term_count"] == 2
    assert second.receipt["candidate_limit"] == 8
    assert first.receipt == store.query_with_receipt(NAMESPACE, 'alpha beta "ZX-4812 secret"', 2).receipt
    encoded = json.dumps(first.receipt).lower()
    assert not any(value in encoded for value in ["zx-4812", "secret", "alpha", "beta", "fixture.md"])


def test_oversized_or_unclosed_query_cannot_silently_drop_mandatory_terms(store) -> None:
    store.add_chunks([chunk("a", "alpha beta")])
    for query in ['alpha "beta', "alpha " + " ".join(f"term{index}" for index in range(100))]:
        outcome = store.query_with_receipt(NAMESPACE, query, 5)
        assert outcome.results == []
        assert outcome.receipt["rejection_reason"] in {"query_syntax_invalid", "query_budget_exceeded"}


def test_mismatched_version_contract_fails_closed(store) -> None:
    store.add_chunks([chunk("a", "alpha beta")])
    with pytest.raises(ValueError, match="rag_lexical_contract_mismatch"):
        store.query_with_receipt(NAMESPACE, "alpha", 5, expected_contract="sqlite-fts5-lexical-v1")


def test_namespace_bm25_statistics_are_isolated(store) -> None:
    store.add_chunks([
        chunk("a", "alpha alpha alpha alpha beta gamma"),
        chunk("b", "alpha beta beta beta beta gamma"),
        chunk("c", "gamma gamma gamma gamma gamma gamma"),
    ])
    before = [(item.chunk_id, item.score) for item in store.query(NAMESPACE, "alpha beta", 5)]
    store.add_chunks([chunk(f"other_{index}", "alpha alpha alpha", "other_namespace") for index in range(100)])
    assert [(item.chunk_id, item.score) for item in store.query(NAMESPACE, "alpha beta", 5)] == before
