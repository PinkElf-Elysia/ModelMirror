from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .embedder import cosine_similarity
from .source_metadata import (
    decode_heading_path,
    encode_heading_path,
    normalize_heading_path,
)


class VectorStoreUnavailableError(RuntimeError):
    """The configured vector backend cannot currently serve a request."""


class VectorStoreContractError(RuntimeError):
    """Stored vector/index metadata violates the immutable retrieval contract."""


@dataclass(slots=True)
class VectorChunk:
    id: str
    kb_id: str
    doc_id: str
    document_name: str
    text: str
    embedding: list[float]
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
class SearchResult:
    chunk_id: str
    kb_id: str
    doc_id: str
    document_name: str
    text: str
    score: float
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
class StoredVectorChunk:
    chunk_id: str
    kb_id: str
    doc_id: str
    document_name: str
    text: str
    chunk_index: int
    parent_chunk_id: str | None = None
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


class VectorStore(Protocol):
    """Protocol implemented by vector-store backends."""

    def add_chunks(self, chunks: list[VectorChunk]) -> None:
        """Add embedded chunks."""

    def query(self, kb_id: str, embedding: list[float], top_k: int) -> list[SearchResult]:
        """Query similar chunks in one knowledge base."""

    def delete_document(self, doc_id: str) -> None:
        """Delete all chunks for a document."""

    def delete_knowledge_base(self, kb_id: str) -> None:
        """Delete all chunks for a knowledge base."""

    def list_document_chunks(self, doc_id: str) -> list[StoredVectorChunk]:
        """List stored chunks for one document without exposing embeddings."""

    def get_chunk(self, kb_id: str, chunk_id: str) -> StoredVectorChunk | None:
        """Read one chunk from an explicit knowledge-base namespace."""

    def readiness(self) -> dict[str, Any]:
        """Return a credential-free backend readiness receipt."""

    def count_namespace(self, kb_id: str) -> int:
        """Return the number of vectors in an explicit namespace."""


class LocalJsonVectorStore:
    """Small persistent vector store used only when explicitly configured."""

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def add_chunks(self, chunks: list[VectorChunk]) -> None:
        records = self._read_records()
        existing_ids = {record["id"] for record in records}
        for chunk in chunks:
            contract = _v3_namespace_contract(chunk.kb_id)
            if contract is not None and len(chunk.embedding) != contract["dimension"]:
                raise VectorStoreContractError(
                    "V3 vector write does not match the index dimension contract."
                )
            if chunk.id in existing_ids:
                continue
            records.append(_chunk_to_record(chunk))
        self._write_records(records)

    def readiness(self) -> dict[str, Any]:
        return {
            "configured_backend": "local",
            "effective_backend": "local_json",
            "ready": True,
            "reason_code": None,
            "distance_contract": "cosine_v1",
        }

    def count_namespace(self, kb_id: str) -> int:
        return sum(1 for record in self._read_records() if record.get("kb_id") == kb_id)

    def query(self, kb_id: str, embedding: list[float], top_k: int) -> list[SearchResult]:
        contract = _v3_namespace_contract(kb_id)
        if contract is not None and len(embedding) != contract["dimension"]:
            raise VectorStoreContractError(
                "V3 vector query does not match the index dimension contract."
            )
        scored: list[SearchResult] = []
        for record in self._read_records():
            if record.get("kb_id") != kb_id:
                continue
            stored_embedding = [float(value) for value in record["embedding"]]
            if contract is not None and len(stored_embedding) != contract["dimension"]:
                raise VectorStoreContractError(
                    "V3 stored vector does not match the index dimension contract."
                )
            score = cosine_similarity(embedding, stored_embedding)
            if contract is not None:
                score = min(1.0, max(0.0, score))
            scored.append(
                SearchResult(
                    chunk_id=record["id"],
                    kb_id=record["kb_id"],
                    doc_id=record["doc_id"],
                    document_name=record["document_name"],
                    text=record["text"],
                    score=score,
                    parent_chunk_id=record.get("parent_chunk_id"),
                    parent_text=record.get("parent_text"),
                    chunk_type=str(record.get("chunk_type", "standard")),
                    start_char=int(record.get("start_char", 0)),
                    end_char=int(record.get("end_char", 0)),
                    page_number=_optional_int(record.get("page_number")),
                    slide=_optional_int(record.get("slide")),
                    heading_path=normalize_heading_path(record.get("heading_path")),
                    sheet=str(record.get("sheet") or "") or None,
                    row_range=str(record.get("row_range") or "") or None,
                    visual_kind=str(record.get("visual_kind") or "") or None,
                    source_block_id=str(record.get("source_block_id") or "") or None,
                    source_block_hash=str(record.get("source_block_hash") or "") or None,
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]

    def delete_document(self, doc_id: str) -> None:
        self._write_records(
            [record for record in self._read_records() if record.get("doc_id") != doc_id]
        )

    def delete_knowledge_base(self, kb_id: str) -> None:
        self._write_records(
            [record for record in self._read_records() if record.get("kb_id") != kb_id]
        )

    def list_document_chunks(self, doc_id: str) -> list[StoredVectorChunk]:
        chunks = [
            StoredVectorChunk(
                chunk_id=str(record["id"]),
                kb_id=str(record["kb_id"]),
                doc_id=str(record["doc_id"]),
                document_name=str(record["document_name"]),
                text=str(record["text"]),
                chunk_index=int(record.get("chunk_index", 0)),
                parent_chunk_id=record.get("parent_chunk_id"),
                chunk_type=str(record.get("chunk_type", "standard")),
                start_char=int(record.get("start_char", 0)),
                end_char=int(record.get("end_char", 0)),
                page_number=_optional_int(record.get("page_number")),
                slide=_optional_int(record.get("slide")),
                heading_path=normalize_heading_path(record.get("heading_path")),
                sheet=str(record.get("sheet") or "") or None,
                row_range=str(record.get("row_range") or "") or None,
                visual_kind=str(record.get("visual_kind") or "") or None,
                source_block_id=str(record.get("source_block_id") or "") or None,
                source_block_hash=str(record.get("source_block_hash") or "") or None,
            )
            for record in self._read_records()
            if record.get("doc_id") == doc_id
        ]
        return sorted(chunks, key=lambda item: item.chunk_index)

    def get_chunk(self, kb_id: str, chunk_id: str) -> StoredVectorChunk | None:
        for record in self._read_records():
            if record.get("kb_id") != kb_id or record.get("id") != chunk_id:
                continue
            return StoredVectorChunk(
                chunk_id=str(record["id"]),
                kb_id=str(record["kb_id"]),
                doc_id=str(record["doc_id"]),
                document_name=str(record["document_name"]),
                text=str(record["text"]),
                chunk_index=int(record.get("chunk_index", 0)),
                parent_chunk_id=record.get("parent_chunk_id"),
                chunk_type=str(record.get("chunk_type", "standard")),
                start_char=int(record.get("start_char", 0)),
                end_char=int(record.get("end_char", 0)),
                page_number=_optional_int(record.get("page_number")),
                slide=_optional_int(record.get("slide")),
                heading_path=normalize_heading_path(record.get("heading_path")),
                sheet=str(record.get("sheet") or "") or None,
                row_range=str(record.get("row_range") or "") or None,
                visual_kind=str(record.get("visual_kind") or "") or None,
                source_block_id=str(record.get("source_block_id") or "") or None,
                source_block_hash=str(record.get("source_block_hash") or "") or None,
            )
        return None

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.storage_path.exists():
            return []
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _write_records(self, records: list[dict[str, Any]]) -> None:
        self.storage_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class ChromaVectorStore:
    """ChromaDB-backed vector store with local persistence."""

    _LEGACY_COLLECTION = "modelmirror_rag_chunks"
    _NAMESPACE_COLLECTION_PREFIX = "modelmirror_rag_v2_"
    _V3_NAMESPACE_COLLECTION_PREFIX = "modelmirror_rag_v3_"

    def __init__(self, persist_path: Path) -> None:
        try:
            import chromadb  # type: ignore[import-not-found]
        except ImportError as exc:
            raise VectorStoreUnavailableError("chromadb is not installed") from exc

        persist_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_path))
        # Keep the original collection readable for existing installations. New
        # writes use one deterministic collection per immutable version namespace
        # because Chroma fixes a collection's dimension on its first insert.
        self._collection = self._client.get_or_create_collection(self._LEGACY_COLLECTION)

    def readiness(self) -> dict[str, Any]:
        return {
            "configured_backend": "chroma",
            "effective_backend": "chroma",
            "ready": True,
            "reason_code": None,
            "distance_contract": "cosine_v1",
        }

    def count_namespace(self, kb_id: str) -> int:
        collection = self._namespace_collection(kb_id, create=False)
        if collection is not None:
            return int(collection.count())
        if _is_v3_namespace(kb_id):
            return 0
        response = self._collection.get(where={"kb_id": kb_id}, include=[])
        return len(response.get("ids") or [])

    def add_chunks(self, chunks: list[VectorChunk]) -> None:
        if not chunks:
            return
        grouped: dict[str, list[VectorChunk]] = {}
        for chunk in chunks:
            grouped.setdefault(chunk.kb_id, []).append(chunk)
        for namespace, namespace_chunks in grouped.items():
            contract = _v3_namespace_contract(namespace)
            if contract is not None and any(
                len(chunk.embedding) != contract["dimension"]
                for chunk in namespace_chunks
            ):
                raise VectorStoreContractError(
                    "V3 vector write does not match the index dimension contract."
                )
            collection = self._namespace_collection(namespace, create=True)
            if collection is None:  # pragma: no cover - create=True is total.
                raise VectorStoreUnavailableError(
                    "Unable to create the Chroma namespace collection."
                )
            self._upsert(collection, namespace_chunks)

    def _upsert(self, collection: Any, chunks: list[VectorChunk]) -> None:
        collection.upsert(
            ids=[chunk.id for chunk in chunks],
            embeddings=[chunk.embedding for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            metadatas=[
                {
                    "kb_id": chunk.kb_id,
                    "doc_id": chunk.doc_id,
                    "document_name": chunk.document_name,
                    "chunk_index": chunk.chunk_index,
                    "parent_chunk_id": chunk.parent_chunk_id or "",
                    "parent_text": chunk.parent_text or "",
                    "chunk_type": chunk.chunk_type,
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                    "page_number": chunk.page_number or 0,
                    "slide": chunk.slide or 0,
                    "heading_path_json": encode_heading_path(chunk.heading_path),
                    "sheet": chunk.sheet or "",
                    "row_range": chunk.row_range or "",
                    "visual_kind": chunk.visual_kind or "",
                    "source_block_id": chunk.source_block_id or "",
                    "source_block_hash": chunk.source_block_hash or "",
                    "updated_at": time.time(),
                }
                for chunk in chunks
            ],
        )

    def query(self, kb_id: str, embedding: list[float], top_k: int) -> list[SearchResult]:
        collection = self._namespace_collection(kb_id, create=False)
        if collection is not None and collection.count() > 0:
            return self._query_collection(
                collection,
                kb_id,
                embedding,
                top_k,
                v3=_is_v3_namespace(kb_id),
            )
        if _is_v3_namespace(kb_id):
            raise VectorStoreUnavailableError("V3 vector namespace is unavailable.")
        return self._query_collection(
            self._collection,
            kb_id,
            embedding,
            top_k,
            legacy=True,
        )

    def _query_collection(
        self,
        collection: Any,
        kb_id: str,
        embedding: list[float],
        top_k: int,
        *,
        legacy: bool = False,
        v3: bool = False,
    ) -> list[SearchResult]:
        if v3:
            contract = _v3_namespace_contract(kb_id)
            if contract is None or len(embedding) != contract["dimension"]:
                raise VectorStoreContractError(
                    "V3 vector query does not match the index dimension contract."
                )
        query: dict[str, Any] = {
            "query_embeddings": [embedding],
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if legacy:
            query["where"] = {"kb_id": kb_id}
        response = collection.query(
            **query,
        )
        ids = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        results: list[SearchResult] = []
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] or {}
            distance = float(distances[index]) if index < len(distances) else 1.0
            results.append(
                SearchResult(
                    chunk_id=chunk_id,
                    kb_id=str(metadata.get("kb_id", kb_id)),
                    doc_id=str(metadata.get("doc_id", "")),
                    document_name=str(metadata.get("document_name", "")),
                    text=str(documents[index] if index < len(documents) else ""),
                    score=(
                        min(1.0, max(0.0, 1.0 - distance))
                        if v3
                        else 1.0 / (1.0 + max(distance, 0.0))
                    ),
                    parent_chunk_id=str(metadata.get("parent_chunk_id") or "") or None,
                    parent_text=str(metadata.get("parent_text") or "") or None,
                    chunk_type=str(metadata.get("chunk_type") or "standard"),
                    start_char=int(metadata.get("start_char", 0)),
                    end_char=int(metadata.get("end_char", 0)),
                    page_number=_optional_int(metadata.get("page_number")),
                    slide=_optional_int(metadata.get("slide")),
                    heading_path=decode_heading_path(metadata.get("heading_path_json")),
                    sheet=str(metadata.get("sheet") or "") or None,
                    row_range=str(metadata.get("row_range") or "") or None,
                    visual_kind=str(metadata.get("visual_kind") or "") or None,
                    source_block_id=str(metadata.get("source_block_id") or "") or None,
                    source_block_hash=str(metadata.get("source_block_hash") or "") or None,
                )
            )
        return results

    def delete_document(self, doc_id: str) -> None:
        for collection in self._managed_collections():
            collection.delete(where={"doc_id": doc_id})

    def delete_knowledge_base(self, kb_id: str) -> None:
        collection_name = self._namespace_collection_name(kb_id)
        if self._namespace_collection(kb_id, create=False) is not None:
            self._client.delete_collection(collection_name)
        self._collection.delete(where={"kb_id": kb_id})

    def list_document_chunks(self, doc_id: str) -> list[StoredVectorChunk]:
        chunks: dict[tuple[str, str], StoredVectorChunk] = {}
        for collection in self._managed_collections():
            response = collection.get(
                where={"doc_id": doc_id},
                include=["documents", "metadatas"],
            )
            for chunk in self._stored_chunks(response, fallback_doc_id=doc_id):
                chunks[(chunk.kb_id, chunk.chunk_id)] = chunk
        return sorted(chunks.values(), key=lambda item: item.chunk_index)

    def _stored_chunks(
        self,
        response: dict[str, Any],
        *,
        fallback_doc_id: str,
    ) -> list[StoredVectorChunk]:
        ids = response.get("ids") or []
        documents = response.get("documents") or []
        metadatas = response.get("metadatas") or []

        chunks: list[StoredVectorChunk] = []
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            metadata = metadata or {}
            chunks.append(
                StoredVectorChunk(
                    chunk_id=str(chunk_id),
                    kb_id=str(metadata.get("kb_id", "")),
                    doc_id=str(metadata.get("doc_id", fallback_doc_id)),
                    document_name=str(metadata.get("document_name", "")),
                    text=str(documents[index] if index < len(documents) else ""),
                    chunk_index=int(metadata.get("chunk_index", index)),
                    parent_chunk_id=str(metadata.get("parent_chunk_id") or "") or None,
                    chunk_type=str(metadata.get("chunk_type") or "standard"),
                    start_char=int(metadata.get("start_char", 0)),
                    end_char=int(metadata.get("end_char", 0)),
                    page_number=_optional_int(metadata.get("page_number")),
                    slide=_optional_int(metadata.get("slide")),
                    heading_path=decode_heading_path(metadata.get("heading_path_json")),
                    sheet=str(metadata.get("sheet") or "") or None,
                    row_range=str(metadata.get("row_range") or "") or None,
                    visual_kind=str(metadata.get("visual_kind") or "") or None,
                    source_block_id=str(metadata.get("source_block_id") or "") or None,
                    source_block_hash=str(metadata.get("source_block_hash") or "") or None,
                )
            )
        return chunks

    def get_chunk(self, kb_id: str, chunk_id: str) -> StoredVectorChunk | None:
        collection = self._namespace_collection(kb_id, create=False)
        if collection is not None:
            chunk = self._get_chunk_from_collection(collection, kb_id, chunk_id)
            if chunk is not None:
                return chunk
        return self._get_chunk_from_collection(
            self._collection,
            kb_id,
            chunk_id,
            legacy=True,
        )

    def _get_chunk_from_collection(
        self,
        collection: Any,
        kb_id: str,
        chunk_id: str,
        *,
        legacy: bool = False,
    ) -> StoredVectorChunk | None:
        query: dict[str, Any] = {
            "ids": [chunk_id],
            "include": ["documents", "metadatas"],
        }
        if legacy:
            query["where"] = {"kb_id": kb_id}
        response = collection.get(**query)
        ids = response.get("ids") or []
        if not ids:
            return None
        documents = response.get("documents") or []
        metadatas = response.get("metadatas") or []
        metadata = (metadatas[0] if metadatas else {}) or {}
        return StoredVectorChunk(
            chunk_id=str(ids[0]),
            kb_id=str(metadata.get("kb_id", kb_id)),
            doc_id=str(metadata.get("doc_id", "")),
            document_name=str(metadata.get("document_name", "")),
            text=str(documents[0] if documents else ""),
            chunk_index=int(metadata.get("chunk_index", 0)),
            parent_chunk_id=str(metadata.get("parent_chunk_id") or "") or None,
            chunk_type=str(metadata.get("chunk_type") or "standard"),
            start_char=int(metadata.get("start_char", 0)),
            end_char=int(metadata.get("end_char", 0)),
            page_number=_optional_int(metadata.get("page_number")),
            slide=_optional_int(metadata.get("slide")),
            heading_path=decode_heading_path(metadata.get("heading_path_json")),
            sheet=str(metadata.get("sheet") or "") or None,
            row_range=str(metadata.get("row_range") or "") or None,
            visual_kind=str(metadata.get("visual_kind") or "") or None,
            source_block_id=str(metadata.get("source_block_id") or "") or None,
            source_block_hash=str(metadata.get("source_block_hash") or "") or None,
        )

    @classmethod
    def _namespace_collection_name(cls, namespace: str) -> str:
        digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:32]
        prefix = (
            cls._V3_NAMESPACE_COLLECTION_PREFIX
            if _is_v3_namespace(namespace)
            else cls._NAMESPACE_COLLECTION_PREFIX
        )
        return f"{prefix}{digest}"

    def _namespace_collection(self, namespace: str, *, create: bool) -> Any | None:
        if not hasattr(self, "_client"):
            # Preserve the small adapter/test seam that injects a collection
            # directly without constructing a persistent Chroma client.
            return self._collection if create else None
        name = self._namespace_collection_name(namespace)
        if not create:
            collection = self._get_collection(name)
            if collection is not None:
                self._validate_namespace_collection(namespace, collection)
            return collection
        v3_contract = _v3_namespace_contract(namespace)
        metadata: dict[str, Any] = {
            "modelmirror_namespace": namespace,
            "modelmirror_schema_version": 3 if v3_contract else 2,
        }
        if v3_contract:
            metadata.update(
                {
                    "hnsw:space": "cosine",
                    "modelmirror_dimension": v3_contract["dimension"],
                    "modelmirror_distance_contract": "cosine_v1",
                }
            )
        collection = self._client.get_or_create_collection(
            name,
            metadata=metadata,
        )
        self._validate_namespace_collection(namespace, collection)
        return collection

    @staticmethod
    def _validate_namespace_collection(namespace: str, collection: Any) -> None:
        v3_contract = _v3_namespace_contract(namespace)
        stored_metadata = collection.metadata or {}
        stored_namespace = str(stored_metadata.get("modelmirror_namespace") or "")
        if stored_namespace and stored_namespace != namespace:
            raise VectorStoreContractError(
                "Chroma namespace collection identity collision."
            )
        if v3_contract and (
            int(stored_metadata.get("modelmirror_schema_version") or 0) != 3
            or int(stored_metadata.get("modelmirror_dimension") or 0)
            != v3_contract["dimension"]
            or str(stored_metadata.get("modelmirror_distance_contract") or "")
            != "cosine_v1"
            or str(stored_metadata.get("hnsw:space") or "") != "cosine"
        ):
            raise VectorStoreContractError(
                "Chroma namespace collection contract mismatch."
            )

    def _get_collection(self, name: str) -> Any | None:
        try:
            return self._client.get_collection(name)
        except Exception:
            return None

    def _managed_collections(self) -> list[Any]:
        collections: list[Any] = []
        for item in self._client.list_collections():
            name = item if isinstance(item, str) else str(getattr(item, "name", ""))
            if (
                name != self._LEGACY_COLLECTION
                and not name.startswith(self._NAMESPACE_COLLECTION_PREFIX)
                and not name.startswith(self._V3_NAMESPACE_COLLECTION_PREFIX)
            ):
                continue
            collection = self._get_collection(name)
            if collection is not None:
                collections.append(collection)
        return collections


class UnavailableVectorStore:
    """Fail-closed vector backend used when the configured backend is unavailable."""

    def __init__(self, configured_backend: str, reason_code: str) -> None:
        self.configured_backend = configured_backend
        self.reason_code = reason_code

    def readiness(self) -> dict[str, Any]:
        return {
            "configured_backend": self.configured_backend,
            "effective_backend": "unavailable",
            "ready": False,
            "reason_code": self.reason_code,
            "distance_contract": "cosine_v1",
        }

    def add_chunks(self, chunks: list[VectorChunk]) -> None:
        if chunks:
            self._raise_unavailable()

    def query(self, kb_id: str, embedding: list[float], top_k: int) -> list[SearchResult]:
        self._raise_unavailable()

    def delete_document(self, doc_id: str) -> None:
        self._raise_unavailable()

    def delete_knowledge_base(self, kb_id: str) -> None:
        self._raise_unavailable()

    def list_document_chunks(self, doc_id: str) -> list[StoredVectorChunk]:
        return []

    def get_chunk(self, kb_id: str, chunk_id: str) -> StoredVectorChunk | None:
        return None

    def count_namespace(self, kb_id: str) -> int:
        return 0

    def _raise_unavailable(self) -> None:
        raise VectorStoreUnavailableError(
            f"Vector backend unavailable: {self.reason_code}"
        )


def create_vector_store(storage_dir: Path) -> VectorStore:
    """Create only the explicitly configured vector backend, failing closed."""

    backend = os.getenv("RAG_VECTOR_STORE", "chroma").strip().lower()
    if backend == "local":
        return LocalJsonVectorStore(storage_dir / "vector_index.json")

    if backend != "chroma":
        return UnavailableVectorStore(backend or "chroma", "vector_backend_unsupported")

    try:
        return ChromaVectorStore(Path(os.getenv("CHROMA_DB_PATH", str(storage_dir / "chroma_db"))))
    except Exception:
        return UnavailableVectorStore("chroma", "vector_backend_initialization_failed")


def _is_v3_namespace(namespace: str) -> bool:
    return _v3_namespace_contract(namespace) is not None


def _v3_namespace_contract(namespace: str) -> dict[str, Any] | None:
    parts = namespace.split("::")
    if len(parts) < 6 or parts[-5] != "v3" or parts[-1] != "cosine_v1":
        return None
    dimension_part = parts[-2]
    if not dimension_part.startswith("dim-"):
        return None
    try:
        dimension = int(dimension_part[4:])
    except ValueError:
        return None
    if dimension <= 0 or not re.fullmatch(r"[0-9a-f]{64}", parts[-3]):
        return None
    return {"dimension": dimension, "embedding_space_fingerprint": parts[-3]}


def _chunk_to_record(chunk: VectorChunk) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "kb_id": chunk.kb_id,
        "doc_id": chunk.doc_id,
        "document_name": chunk.document_name,
        "text": chunk.text,
        "embedding": chunk.embedding,
        "chunk_index": chunk.chunk_index,
        "parent_chunk_id": chunk.parent_chunk_id,
        "parent_text": chunk.parent_text,
        "chunk_type": chunk.chunk_type,
        "start_char": chunk.start_char,
        "end_char": chunk.end_char,
        "page_number": chunk.page_number,
        "slide": chunk.slide,
        "heading_path": list(normalize_heading_path(chunk.heading_path)),
        "sheet": chunk.sheet,
        "row_range": chunk.row_range,
        "visual_kind": chunk.visual_kind,
        "source_block_id": chunk.source_block_id,
        "source_block_hash": chunk.source_block_hash,
    }


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
