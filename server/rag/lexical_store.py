from __future__ import annotations

import math
import json
import re
import sqlite3
import threading
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from .source_metadata import (
    decode_heading_path,
)
from .lexical_contract import LEGACY_LEXICAL_CONTRACT, LEXICAL_CONTRACT, tokenize_for_search_v2
from . import lexical_v2_store


_LATIN_TOKEN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


@dataclass(slots=True)
class LexicalChunk:
    chunk_id: str
    namespace: str
    doc_id: str
    document_name: str
    text: str
    chunk_index: int
    parent_chunk_id: str | None = None
    parent_text: str | None = None
    chunk_type: str = "standard"
    start_char: int = 0
    end_char: int = 0
    page_number: int | None = None
    slide: int | None = None
    heading_path: tuple[str, ...] = ()
    sheet: str | None = None
    row_range: str | None = None
    visual_kind: str | None = None
    source_block_id: str | None = None
    source_block_hash: str | None = None
    source_block_ids: tuple[str, ...] = ()
    generated_item: bool = False


@dataclass(slots=True)
class LexicalSearchResult:
    chunk_id: str
    namespace: str
    doc_id: str
    document_name: str
    text: str
    score: float
    rank: int
    score_contract: str = "weighted_term_coverage_v1"
    parent_chunk_id: str | None = None
    parent_text: str | None = None
    chunk_type: str = "standard"
    start_char: int = 0
    end_char: int = 0
    page_number: int | None = None
    slide: int | None = None
    heading_path: tuple[str, ...] = ()
    sheet: str | None = None
    row_range: str | None = None
    visual_kind: str | None = None
    source_block_id: str | None = None
    source_block_hash: str | None = None
    source_block_ids: tuple[str, ...] = ()
    generated_item: bool = False


@dataclass(slots=True)
class LexicalQueryOutcome:
    results: list[LexicalSearchResult]
    receipt: dict


class SqliteLexicalStore:
    """SQLite FTS5 side index for deterministic local full-text retrieval."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @property
    def backend(self) -> str:
        return "sqlite_fts5"

    def add_chunks(self, chunks: list[LexicalChunk]) -> None:
        if not chunks:
            return
        with self._lock, self._connect() as connection:
            # FTS DDL and rows must roll back together, including first writes.
            connection.execute("BEGIN IMMEDIATE")
            lexical_v2_store.add_chunks(connection, chunks)

    def query(self, namespace: str, query: str, top_k: int) -> list[LexicalSearchResult]:
        return self.query_with_receipt(namespace, query, top_k).results

    def query_with_receipt(
        self, namespace: str, query: str, top_k: int, *,
        expected_contract: str | None = None,
        candidate_top_k: int | None = None,
    ) -> LexicalQueryOutcome:
        with self._lock, self._connect() as connection:
            current = lexical_v2_store.has_namespace(connection, namespace)
            actual = LEXICAL_CONTRACT if current else LEGACY_LEXICAL_CONTRACT
            if expected_contract is not None and expected_contract != actual:
                raise ValueError("rag_lexical_contract_mismatch")
            if current:
                rows, receipt = lexical_v2_store.query(
                    connection, namespace, query, top_k, candidate_top_k=candidate_top_k,
                )
                return LexicalQueryOutcome([
                    self._row_to_result(row, rank=index + 1, score=row["absolute_confidence"], score_contract="weighted_term_coverage_v1")
                    for index, row in enumerate(rows)
                ], receipt)
            results = self._query_legacy(connection, namespace, query, top_k)
            return LexicalQueryOutcome(results, {
                "contract_version": LEGACY_LEXICAL_CONTRACT,
                "query_policy": "legacy_or_v1", "status": "legacy_read_only",
                "final_count": len(results),
            })

    def _query_legacy(self, connection: sqlite3.Connection, namespace: str, query: str, top_k: int) -> list[LexicalSearchResult]:
        expression = build_fts_query(query)
        if not expression:
            return []
        rows = connection.execute(
                """
                SELECT c.*, rag_chunks_fts.tokens AS search_tokens,
                       bm25(rag_chunks_fts) AS lexical_rank
                FROM rag_chunks_fts
                JOIN rag_chunks c ON c.chunk_id = rag_chunks_fts.chunk_id
                WHERE rag_chunks_fts MATCH ? AND rag_chunks_fts.namespace = ?
                ORDER BY lexical_rank ASC, c.chunk_id ASC
                LIMIT ?
                """,
                (expression, namespace, top_k),
            ).fetchall()
        confidence_weights = _lexical_confidence_weights(connection, namespace, query)
        return [
            self._row_to_result(
                row,
                score=_lexical_confidence(
                    str(row["search_tokens"] or ""),
                    confidence_weights,
                    fallback_rank=index + 1,
                ),
                rank=index + 1,
                score_contract=(
                    "weighted_term_coverage_v1"
                    if confidence_weights
                    else "legacy_rank_fallback"
                ),
            )
            for index, row in enumerate(rows)
        ]

    def delete_document(self, doc_id: str) -> None:
        with self._lock, self._connect() as connection:
            lexical_v2_store.delete_document(connection, doc_id)
            ids = [row[0] for row in connection.execute(
                "SELECT chunk_id FROM rag_chunks WHERE doc_id = ?", (doc_id,)
            )]
            connection.executemany(
                "DELETE FROM rag_chunks_fts WHERE chunk_id = ?", ((item,) for item in ids)
            )
            connection.execute("DELETE FROM rag_chunks WHERE doc_id = ?", (doc_id,))

    def delete_namespace(self, namespace: str) -> None:
        with self._lock, self._connect() as connection:
            if lexical_v2_store.has_namespace(connection, namespace):
                lexical_v2_store.delete_namespace(connection, namespace)
                return
            ids = [row[0] for row in connection.execute(
                "SELECT chunk_id FROM rag_chunks WHERE namespace = ?", (namespace,)
            )]
            connection.executemany(
                "DELETE FROM rag_chunks_fts WHERE chunk_id = ?", ((item,) for item in ids)
            )
            connection.execute("DELETE FROM rag_chunks WHERE namespace = ?", (namespace,))

    def count_namespace(self, namespace: str) -> int:
        with self._lock, self._connect() as connection:
            if lexical_v2_store.has_namespace(connection, namespace):
                lexical_v2_store.validate_namespace(connection, namespace)
                return int(connection.execute("SELECT COUNT(*) FROM rag_chunks_v2 WHERE namespace = ?", (namespace,)).fetchone()[0])
            row = connection.execute(
                "SELECT COUNT(*) FROM rag_chunks WHERE namespace = ?", (namespace,)
            ).fetchone()
        return int(row[0] if row else 0)

    def list_document_chunks(self, doc_id: str, *, namespace: str | None = None) -> list[LexicalChunk]:
        """List stored lexical chunks without exposing FTS implementation details."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rag_chunks WHERE doc_id = ? ORDER BY chunk_index, chunk_id",
                (doc_id,),
            ).fetchall()
            rows.extend(connection.execute(
                "SELECT * FROM rag_chunks_v2 WHERE doc_id = ? ORDER BY namespace, chunk_index, chunk_id", (doc_id,),
            ).fetchall())
        return [self._row_to_chunk(row) for row in rows if namespace is None or row["namespace"] == namespace]

    def index_receipt(self, namespace: str) -> dict:
        with self._lock, self._connect() as connection:
            return lexical_v2_store.index_receipt(connection, namespace)

    def get_chunk(self, namespace: str, chunk_id: str) -> LexicalChunk | None:
        with self._lock, self._connect() as connection:
            if lexical_v2_store.has_namespace(connection, namespace):
                lexical_v2_store.validate_namespace(connection, namespace)
                row = connection.execute("SELECT * FROM rag_chunks_v2 WHERE namespace = ? AND chunk_id = ?", (namespace, chunk_id)).fetchone()
                return self._row_to_chunk(row) if row is not None else None
            row = connection.execute(
                "SELECT * FROM rag_chunks WHERE namespace = ? AND chunk_id = ?",
                (namespace, chunk_id),
            ).fetchone()
        return self._row_to_chunk(row) if row is not None else None

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> LexicalChunk:
        # Legacy schemas are not ALTERed to manufacture newer metadata.
        row = dict(row)
        for field in ("page_number", "slide", "heading_path_json", "sheet", "row_range", "visual_kind", "source_block_id", "source_block_hash"):
            row.setdefault(field, None)
        return LexicalChunk(
            chunk_id=str(row["chunk_id"]),
            namespace=str(row["namespace"]),
            doc_id=str(row["doc_id"]),
            document_name=str(row["document_name"]),
            text=str(row["text"]),
            chunk_index=int(row["chunk_index"]),
            parent_chunk_id=str(row["parent_chunk_id"]) if row["parent_chunk_id"] else None,
            parent_text=str(row["parent_text"]) if row["parent_text"] else None,
            chunk_type=str(row["chunk_type"] or "standard"),
            start_char=int(row["start_char"] or 0),
            end_char=int(row["end_char"] or 0),
            page_number=int(row["page_number"]) if row["page_number"] else None,
            slide=int(row["slide"]) if row["slide"] else None,
            heading_path=decode_heading_path(row["heading_path_json"]),
            sheet=str(row["sheet"]) if row["sheet"] else None,
            row_range=str(row["row_range"]) if row["row_range"] else None,
            visual_kind=str(row["visual_kind"]) if row["visual_kind"] else None,
            source_block_id=str(row["source_block_id"]) if row["source_block_id"] else None,
            source_block_hash=str(row["source_block_hash"]) if row["source_block_hash"] else None,
            source_block_ids=tuple(json.loads(row.get("source_block_ids_json") or "[]")),
            generated_item=bool(row.get("generated_item", False)),
        )

    @classmethod
    def _row_to_result(cls, row, *, rank: int, score: float, score_contract: str) -> LexicalSearchResult:
        values = asdict(cls._row_to_chunk(row))
        values.pop("chunk_index")
        return LexicalSearchResult(**values, rank=rank, score=score, score_contract=score_contract)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    document_name TEXT NOT NULL,
                    text TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    parent_chunk_id TEXT,
                    parent_text TEXT,
                    chunk_type TEXT NOT NULL,
                    start_char INTEGER NOT NULL,
                    end_char INTEGER NOT NULL
                    , page_number INTEGER
                    , slide INTEGER
                    , heading_path_json TEXT
                    , sheet TEXT
                    , row_range TEXT
                    , visual_kind TEXT
                    , source_block_id TEXT
                    , source_block_hash TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_chunks_namespace ON rag_chunks(namespace)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks(doc_id)"
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts
                USING fts5(chunk_id UNINDEXED, namespace UNINDEXED, tokens, tokenize='unicode61')
                """
            )
            lexical_v2_store.initialize(connection)


def tokenize_for_search(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens = _LATIN_TOKEN.findall(normalized)
    for run in _CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(dict.fromkeys(token for token in tokens if token.strip()))


def build_fts_query(text: str) -> str:
    tokens = tokenize_for_search(text).split()
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:64])


def _lexical_confidence_weights(
    connection: sqlite3.Connection,
    namespace: str,
    query: str,
) -> dict[str, float]:
    tokens = [
        token
        for token in tokenize_for_search(query).split()[:64]
        if _is_significant_confidence_token(token)
    ]
    if not tokens:
        return {}
    total_chunks = int(
        connection.execute(
            "SELECT COUNT(*) FROM rag_chunks WHERE namespace = ?", (namespace,)
        ).fetchone()[0]
    )
    weights: dict[str, float] = {}
    for token in dict.fromkeys(tokens):
        expression = f'"{token.replace(chr(34), chr(34) * 2)}"'
        document_frequency = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM rag_chunks_fts
                WHERE rag_chunks_fts MATCH ? AND namespace = ?
                """,
                (expression, namespace),
            ).fetchone()[0]
        )
        weights[token] = math.log(
            (total_chunks + 1) / (document_frequency + 1)
        ) + 1.0
    return weights


def _lexical_confidence(
    indexed_tokens: str,
    weights: dict[str, float],
    *,
    fallback_rank: int,
) -> float:
    if not weights:
        return round(1.0 / max(1, fallback_rank), 6)
    indexed = set(indexed_tokens.split())
    total_weight = sum(weights.values())
    matched_weight = sum(
        weight for token, weight in weights.items() if token in indexed
    )
    return round(matched_weight / total_weight if total_weight else 0.0, 6)


def _is_significant_confidence_token(token: str) -> bool:
    is_cjk = bool(token) and all("\u3400" <= char <= "\u9fff" for char in token)
    if is_cjk:
        return len(token) == 2
    return len(token) >= 2
