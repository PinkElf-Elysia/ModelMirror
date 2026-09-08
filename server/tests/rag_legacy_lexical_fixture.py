from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path


_LEGACY_LATIN_TOKEN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_LEGACY_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


LEGACY_RAG_CHUNKS_DDL = """
CREATE TABLE rag_chunks (
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
)
"""

LEGACY_RAG_CHUNKS_FTS_DDL = """
CREATE VIRTUAL TABLE rag_chunks_fts
USING fts5(chunk_id UNINDEXED, namespace UNINDEXED, tokens, tokenize='unicode61')
"""


@dataclass(frozen=True, slots=True)
class LegacyLexicalRow:
    chunk_id: str
    namespace: str
    doc_id: str
    document_name: str
    text: str
    chunk_index: int = 0
    parent_chunk_id: str | None = None
    parent_text: str | None = None
    chunk_type: str = "standard"
    start_char: int = 0
    end_char: int = 0


def frozen_v1_tokens(text: str) -> str:
    """Reproduce the persisted lexical-v1 token stream without production code."""

    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens = _LEGACY_LATIN_TOKEN.findall(normalized)
    for run in _LEGACY_CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(dict.fromkeys(token for token in tokens if token.strip()))


def create_legacy_lexical_fixture(
    path: Path,
    rows: list[LegacyLexicalRow],
) -> None:
    """Create and populate the frozen lexical-v1 tables without using store writes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        # Tests may append frozen v1 rows after the store created empty legacy
        # tables. This is a test-only fixture, never a production write API.
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='rag_chunks'").fetchone() is None:
            connection.execute(LEGACY_RAG_CHUNKS_DDL)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='rag_chunks_fts'").fetchone() is None:
            connection.execute(LEGACY_RAG_CHUNKS_FTS_DDL)
        for row in rows:
            connection.execute(
                """
                INSERT INTO rag_chunks (
                    chunk_id, namespace, doc_id, document_name, text, chunk_index,
                    parent_chunk_id, parent_text, chunk_type, start_char, end_char
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.chunk_id,
                    row.namespace,
                    row.doc_id,
                    row.document_name,
                    row.text,
                    row.chunk_index,
                    row.parent_chunk_id,
                    row.parent_text,
                    row.chunk_type,
                    row.start_char,
                    row.end_char,
                ),
            )
            connection.execute(
                """
                INSERT INTO rag_chunks_fts (chunk_id, namespace, tokens)
                VALUES (?, ?, ?)
                """,
                (row.chunk_id, row.namespace, frozen_v1_tokens(row.text)),
            )
