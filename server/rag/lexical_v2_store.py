"""Append-only lexical-v2 schema, isolated from persisted lexical-v1 tables."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict
from typing import Any

from .lexical_contract import (
    FTS_TOKENIZER, LEXICAL_CONTRACT, QUERY_POLICY, TOKENIZER_CONTRACT,
    plan_query, quote_phrase, tokenize_for_search_v2,
    lexical_profile, query_fingerprint,
)
from .source_metadata import encode_heading_path


_COLUMNS = (
    "chunk_id", "namespace", "doc_id", "document_name", "text", "chunk_index",
    "parent_chunk_id", "parent_text", "chunk_type", "start_char", "end_char",
    "page_number", "slide", "heading_path_json", "sheet", "row_range",
    "visual_kind", "source_block_id", "source_block_hash",
    "source_block_ids_json", "generated_item",
)


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS rag_lexical_namespaces_v2 (
            namespace TEXT PRIMARY KEY,
            contract_version TEXT NOT NULL,
            query_policy TEXT NOT NULL,
            tokenizer_contract TEXT NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS rag_chunks_v2 (
            chunk_id TEXT NOT NULL, namespace TEXT NOT NULL,
            doc_id TEXT NOT NULL, document_name TEXT NOT NULL,
            text TEXT NOT NULL, chunk_index INTEGER NOT NULL,
            parent_chunk_id TEXT, parent_text TEXT, chunk_type TEXT NOT NULL,
            start_char INTEGER NOT NULL, end_char INTEGER NOT NULL,
            page_number INTEGER, slide INTEGER, heading_path_json TEXT,
            sheet TEXT, row_range TEXT, visual_kind TEXT,
            source_block_id TEXT, source_block_hash TEXT,
            source_block_ids_json TEXT NOT NULL, generated_item INTEGER NOT NULL,
            PRIMARY KEY (namespace, chunk_id)
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_v2_doc ON rag_chunks_v2(doc_id)")


def has_namespace(connection: sqlite3.Connection, namespace: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM rag_lexical_namespaces_v2 WHERE namespace = ?", (namespace,),
    ).fetchone() is not None


def _table(namespace: str) -> str:
    # Never interpolate caller-controlled identifiers or names read from the DB.
    return "rag_fts_v2_" + hashlib.sha256(namespace.encode("utf-8")).hexdigest()


def _fts_definition(namespace: str) -> str:
    return f"CREATE VIRTUAL TABLE {_table(namespace)} USING fts5(chunk_id UNINDEXED, tokens, tokenize='{FTS_TOKENIZER}')"


def validate_namespace(connection: sqlite3.Connection, namespace: str) -> str:
    row = connection.execute(
        "SELECT contract_version, query_policy, tokenizer_contract FROM rag_lexical_namespaces_v2 WHERE namespace = ?",
        (namespace,),
    ).fetchone()
    schema = connection.execute("SELECT sql FROM sqlite_master WHERE name = ?", (_table(namespace),)).fetchone()
    if (
        row is None or tuple(row) != (LEXICAL_CONTRACT, QUERY_POLICY, TOKENIZER_CONTRACT)
        or schema is None or schema[0] != _fts_definition(namespace)
    ):
        raise ValueError("rag_lexical_contract_mismatch")
    return _table(namespace)


def add_chunks(connection: sqlite3.Connection, chunks: list[Any]) -> None:
    for namespace in dict.fromkeys(chunk.namespace for chunk in chunks):
        # An existing v1 namespace is never upgraded or written through v2.
        if connection.execute("SELECT 1 FROM rag_chunks WHERE namespace = ? LIMIT 1", (namespace,)).fetchone():
            raise ValueError("rag_lexical_contract_legacy_read_only")
        if not has_namespace(connection, namespace):
            connection.execute(_fts_definition(namespace))
            connection.execute(
                "INSERT INTO rag_lexical_namespaces_v2 VALUES (?, ?, ?, ?)",
                (namespace, LEXICAL_CONTRACT, QUERY_POLICY, TOKENIZER_CONTRACT),
            )
        validate_namespace(connection, namespace)
    for chunk in chunks:
        table = _table(chunk.namespace)
        values = asdict(chunk)
        values["heading_path_json"] = encode_heading_path(values.pop("heading_path"))
        values["source_block_ids_json"] = json.dumps(values.pop("source_block_ids"), ensure_ascii=False, separators=(",", ":"))
        connection.execute(f"DELETE FROM {table} WHERE chunk_id = ?", (chunk.chunk_id,))
        connection.execute("DELETE FROM rag_chunks_v2 WHERE namespace = ? AND chunk_id = ?", (chunk.namespace, chunk.chunk_id))
        connection.execute(
            f"INSERT INTO rag_chunks_v2 ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' for _ in _COLUMNS)})",
            tuple(values[column] for column in _COLUMNS),
        )
        connection.execute(f"INSERT INTO {table} (chunk_id, tokens) VALUES (?, ?)", (chunk.chunk_id, tokenize_for_search_v2(chunk.text)))


def query(
    connection: sqlite3.Connection, namespace: str, text: str, top_k: int,
    *, candidate_top_k: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = validate_namespace(connection, namespace)
    plan = plan_query(text)
    requested_k = max(0, int(top_k if candidate_top_k is None else candidate_top_k))
    limit = min(max(requested_k * 8, requested_k), 500)
    receipt: dict[str, Any] = {
        "contract_version": LEXICAL_CONTRACT,
        "query_policy": QUERY_POLICY,
        "tokenizer_contract": TOKENIZER_CONTRACT,
        "query_fingerprint": query_fingerprint(text),
        "candidate_limit": limit,
        "initial_candidate_count": 0,
        "candidate_pool_saturated": False,
        "minimum_should_match_count": 0,
        "final_count": 0,
        "effective_term_count": len(plan.ordinary_terms),
        "required_term_count": plan.required_term_count,
        "mandatory_identifier_count": plan.identifier_count,
        "mandatory_phrase_count": plan.phrase_count,
        "rejection_reason": plan.rejection_reason,
        "candidates": [],
    }
    if not plan.expression or not limit or top_k <= 0:
        return [], receipt
    rows = connection.execute(
        f"""SELECT c.*, f.tokens AS search_tokens, bm25({table}) AS lexical_rank
            FROM {table} f JOIN rag_chunks_v2 c ON c.chunk_id = f.chunk_id AND c.namespace = ?
            WHERE {table} MATCH ? ORDER BY lexical_rank ASC, c.chunk_id ASC LIMIT ?""",
        (namespace, plan.expression, limit),
    ).fetchall()
    receipt["initial_candidate_count"] = len(rows)
    receipt["candidate_pool_saturated"] = len(rows) == limit
    total = connection.execute("SELECT COUNT(*) FROM rag_chunks_v2 WHERE namespace = ?", (namespace,)).fetchone()[0]
    weights = {
        token: math.log((total + 1) / (connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {table} MATCH ?", (quote_phrase([token]),),
        ).fetchone()[0] + 1)) + 1.0
        for token in plan.confidence_terms
    }
    denominator = sum(weights.values())
    results = []
    for row in rows:
        indexed = set(row["search_tokens"].split())
        matched = len(indexed.intersection(plan.ordinary_terms))
        accepted = matched >= plan.required_term_count
        receipt["candidates"].append({
            "chunk_id": row["chunk_id"], "matched_term_count": matched,
            "required_term_count": plan.required_term_count, "accepted": accepted,
            "rejection_reason": None if accepted else "minimum_should_match_not_met",
        })
        if accepted:
            result = dict(row)
            result["absolute_confidence"] = round(sum(weight for token, weight in weights.items() if token in indexed) / denominator, 6) if denominator else 0.0
            results.append(result)
    receipt["minimum_should_match_count"] = len(results)
    results = results[:max(0, int(top_k))]
    receipt["final_count"] = len(results)
    if not results:
        receipt["rejection_reason"] = "minimum_should_match_not_met" if rows else "mandatory_or_terms_not_matched"
    return results, receipt


def delete_namespace(connection: sqlite3.Connection, namespace: str) -> None:
    connection.execute(f"DROP TABLE IF EXISTS {_table(namespace)}")
    connection.execute("DELETE FROM rag_chunks_v2 WHERE namespace = ?", (namespace,))
    connection.execute("DELETE FROM rag_lexical_namespaces_v2 WHERE namespace = ?", (namespace,))


def index_receipt(connection: sqlite3.Connection, namespace: str) -> dict[str, Any]:
    table = validate_namespace(connection, namespace)
    rows = connection.execute(
        f"SELECT c.chunk_id, c.text, f.tokens FROM rag_chunks_v2 c LEFT JOIN {table} f ON f.chunk_id = c.chunk_id WHERE c.namespace = ? ORDER BY c.chunk_id",
        (namespace,),
    ).fetchall()
    count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    metadata_count = connection.execute("SELECT COUNT(*) FROM rag_chunks_v2 WHERE namespace = ?", (namespace,)).fetchone()[0]
    if count != len(rows) or metadata_count != len(rows) or any(row["tokens"] != tokenize_for_search_v2(row["text"]) for row in rows):
        raise ValueError("rag_lexical_index_content_mismatch")
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([row["chunk_id"], row["tokens"]], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return {
        "receipt_version": "rag-lexical-index-receipt-v2",
        **lexical_profile(),
        "chunk_count": len(rows),
        "indexed_tokens_hash": digest.hexdigest(),
    }


def delete_document(connection: sqlite3.Connection, doc_id: str) -> None:
    rows = connection.execute("SELECT namespace, chunk_id FROM rag_chunks_v2 WHERE doc_id = ?", (doc_id,)).fetchall()
    for namespace, chunk_id in rows:
        table = validate_namespace(connection, namespace)
        connection.execute(f"DELETE FROM {table} WHERE chunk_id = ?", (chunk_id,))
    connection.execute("DELETE FROM rag_chunks_v2 WHERE doc_id = ?", (doc_id,))
