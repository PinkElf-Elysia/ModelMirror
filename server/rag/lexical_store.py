from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path


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
    visual_kind: str | None = None
    source_block_id: str | None = None


@dataclass(slots=True)
class LexicalSearchResult:
    chunk_id: str
    namespace: str
    doc_id: str
    document_name: str
    text: str
    score: float
    rank: int
    parent_chunk_id: str | None = None
    parent_text: str | None = None
    chunk_type: str = "standard"
    start_char: int = 0
    end_char: int = 0
    page_number: int | None = None
    visual_kind: str | None = None
    source_block_id: str | None = None


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
                        , page_number, visual_kind, source_block_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        chunk.visual_kind,
                        chunk.source_block_id,
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
                SELECT c.*, bm25(rag_chunks_fts) AS lexical_rank
                FROM rag_chunks_fts
                JOIN rag_chunks c ON c.chunk_id = rag_chunks_fts.chunk_id
                WHERE rag_chunks_fts MATCH ? AND rag_chunks_fts.namespace = ?
                ORDER BY lexical_rank ASC, c.chunk_id ASC
                LIMIT ?
                """,
                (expression, namespace, top_k),
            ).fetchall()
        return [
            LexicalSearchResult(
                chunk_id=str(row["chunk_id"]),
                namespace=str(row["namespace"]),
                doc_id=str(row["doc_id"]),
                document_name=str(row["document_name"]),
                text=str(row["text"]),
                score=round(1.0 / (1.0 + index), 6),
                rank=index + 1,
                parent_chunk_id=str(row["parent_chunk_id"]) if row["parent_chunk_id"] else None,
                parent_text=str(row["parent_text"]) if row["parent_text"] else None,
                chunk_type=str(row["chunk_type"] or "standard"),
                start_char=int(row["start_char"] or 0),
                end_char=int(row["end_char"] or 0),
                page_number=int(row["page_number"]) if row["page_number"] else None,
                visual_kind=str(row["visual_kind"]) if row["visual_kind"] else None,
                source_block_id=str(row["source_block_id"]) if row["source_block_id"] else None,
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
                    , visual_kind TEXT
                    , source_block_id TEXT
                )
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(rag_chunks)")
            }
            for name, sql_type in (
                ("page_number", "INTEGER"),
                ("visual_kind", "TEXT"),
                ("source_block_id", "TEXT"),
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
