from __future__ import annotations

import math
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .source_metadata import (
    decode_heading_path,
    encode_heading_path,
)


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
            for chunk in chunks:
                connection.execute("DELETE FROM rag_chunks_fts WHERE chunk_id = ?", (chunk.chunk_id,))
                connection.execute("DELETE FROM rag_chunks WHERE chunk_id = ?", (chunk.chunk_id,))
                connection.execute(
                    """
                    INSERT INTO rag_chunks (
                        chunk_id, namespace, doc_id, document_name, text, chunk_index,
                        parent_chunk_id, parent_text, chunk_type, start_char, end_char
                        , page_number, slide, heading_path_json, sheet, row_range,
                        visual_kind, source_block_id, source_block_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.namespace,
                        chunk.doc_id,
                        chunk.document_name,
                        chunk.text,
                        chunk.chunk_index,
                        chunk.parent_chunk_id,
                        chunk.parent_text,
                        chunk.chunk_type,
                        chunk.start_char,
                        chunk.end_char,
                        chunk.page_number,
                        chunk.slide,
                        encode_heading_path(chunk.heading_path),
                        chunk.sheet,
                        chunk.row_range,
                        chunk.visual_kind,
                        chunk.source_block_id,
                        chunk.source_block_hash,
                    ),
                )
                connection.execute(
                    "INSERT INTO rag_chunks_fts (chunk_id, namespace, tokens) VALUES (?, ?, ?)",
                    (chunk.chunk_id, chunk.namespace, tokenize_for_search(chunk.text)),
                )

    def query(self, namespace: str, query: str, top_k: int) -> list[LexicalSearchResult]:
        expression = build_fts_query(query)
        if not expression:
            return []
        with self._lock, self._connect() as connection:
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
            confidence_weights = _lexical_confidence_weights(
                connection, namespace, query
            )
        return [
            LexicalSearchResult(
                chunk_id=str(row["chunk_id"]),
                namespace=str(row["namespace"]),
                doc_id=str(row["doc_id"]),
                document_name=str(row["document_name"]),
                text=str(row["text"]),
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
            )
            for index, row in enumerate(rows)
        ]

    def delete_document(self, doc_id: str) -> None:
        with self._lock, self._connect() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT chunk_id FROM rag_chunks WHERE doc_id = ?", (doc_id,)
            )]
            connection.executemany(
                "DELETE FROM rag_chunks_fts WHERE chunk_id = ?", ((item,) for item in ids)
            )
            connection.execute("DELETE FROM rag_chunks WHERE doc_id = ?", (doc_id,))

    def delete_namespace(self, namespace: str) -> None:
        with self._lock, self._connect() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT chunk_id FROM rag_chunks WHERE namespace = ?", (namespace,)
            )]
            connection.executemany(
                "DELETE FROM rag_chunks_fts WHERE chunk_id = ?", ((item,) for item in ids)
            )
            connection.execute("DELETE FROM rag_chunks WHERE namespace = ?", (namespace,))

    def count_namespace(self, namespace: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM rag_chunks WHERE namespace = ?", (namespace,)
            ).fetchone()
        return int(row[0] if row else 0)

    def list_document_chunks(self, doc_id: str) -> list[LexicalChunk]:
        """List stored lexical chunks without exposing FTS implementation details."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rag_chunks WHERE doc_id = ? ORDER BY chunk_index, chunk_id",
                (doc_id,),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_chunk(self, namespace: str, chunk_id: str) -> LexicalChunk | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM rag_chunks WHERE namespace = ? AND chunk_id = ?",
                (namespace, chunk_id),
            ).fetchone()
        return self._row_to_chunk(row) if row is not None else None

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> LexicalChunk:
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
        )

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
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(rag_chunks)")
            }
            for name, sql_type in (
                ("page_number", "INTEGER"),
                ("slide", "INTEGER"),
                ("heading_path_json", "TEXT"),
                ("sheet", "TEXT"),
                ("row_range", "TEXT"),
                ("visual_kind", "TEXT"),
                ("source_block_id", "TEXT"),
                ("source_block_hash", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE rag_chunks ADD COLUMN {name} {sql_type}")
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
