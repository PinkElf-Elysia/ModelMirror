from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

import pytest

from server.rag.content_identity import generated_parent_window_identity
from server.rag.vector_store import (
    ChromaVectorStore,
    LocalJsonVectorStore,
    VectorChunk,
    VectorStoreContractError,
)


def _lineage_chunk() -> VectorChunk:
    return VectorChunk(
        id="chunk-1",
        kb_id="kb-1",
        doc_id="doc-1",
        document_name="generated.md",
        text="retrieval child",
        embedding=[1.0, 0.0],
        chunk_index=0,
        parent_chunk_id="parent-1",
        parent_text="bounded parent context",
        source_block_id=None,
        source_block_hash="a" * 64,
        source_block_ids=("block-a", "block-b"),
        generated_item=True,
    )


def _assert_lineage(chunk: Any) -> None:
    assert chunk.parent_text == "bounded parent context"
    assert chunk.source_block_ids == ("block-a", "block-b")
    assert chunk.source_block_id is None
    assert chunk.generated_item is True


def test_local_json_round_trips_generated_lineage_across_all_read_paths(
    tmp_path: Path,
) -> None:
    store = LocalJsonVectorStore(tmp_path / "vectors.json")
    store.add_chunks([_lineage_chunk()])

    _assert_lineage(store.query("kb-1", [1.0, 0.0], 1)[0])
    _assert_lineage(store.list_document_chunks("doc-1")[0])
    stored = store.get_chunk("kb-1", "chunk-1")
    assert stored is not None
    _assert_lineage(stored)

    record = store._read_records()[0]
    assert record["source_block_ids"] == ["block-a", "block-b"]
    assert record["generated_item"] is True


def test_local_json_keeps_generated_parent_windows_distinct(tmp_path: Path) -> None:
    item_parent_id = "generated_v1_" + ("d" * 64)
    first = _lineage_chunk()
    first.id = "window-0-child"
    first.parent_text = "first bounded parent context"
    first.parent_chunk_id = generated_parent_window_identity(
        item_parent_id,
        "parent_0",
        first.parent_text,
    )
    second = _lineage_chunk()
    second.id = "window-1-child"
    second.chunk_index = 1
    second.parent_text = "second bounded parent context"
    second.parent_chunk_id = generated_parent_window_identity(
        item_parent_id,
        "parent_1",
        second.parent_text,
    )
    store = LocalJsonVectorStore(tmp_path / "window-vectors.json")

    store.add_chunks([first, second])

    listed = store.list_document_chunks("doc-1")
    assert {
        (chunk.parent_chunk_id, chunk.parent_text) for chunk in listed
    } == {
        (first.parent_chunk_id, first.parent_text),
        (second.parent_chunk_id, second.parent_text),
    }
    queried = store.query("kb-1", [1.0, 0.0], 2)
    assert {chunk.parent_chunk_id for chunk in queried} == {
        first.parent_chunk_id,
        second.parent_chunk_id,
    }
    assert store.get_chunk("kb-1", first.id).parent_text == first.parent_text  # type: ignore[union-attr]
    assert store.get_chunk("kb-1", second.id).parent_text == second.parent_text  # type: ignore[union-attr]


def test_local_json_legacy_records_use_safe_lineage_defaults(tmp_path: Path) -> None:
    path = tmp_path / "legacy-vectors.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "legacy-chunk",
                    "kb_id": "kb-1",
                    "doc_id": "doc-1",
                    "document_name": "legacy.md",
                    "text": "legacy child",
                    "embedding": [1.0, 0.0],
                    "chunk_index": 0,
                    "parent_text": "legacy parent",
                    "source_block_id": "legacy-block",
                }
            ]
        ),
        encoding="utf-8",
    )
    store = LocalJsonVectorStore(path)

    for chunk in (
        store.query("kb-1", [1.0, 0.0], 1)[0],
        store.list_document_chunks("doc-1")[0],
        store.get_chunk("kb-1", "legacy-chunk"),
    ):
        assert chunk is not None
        assert chunk.parent_text == "legacy parent"
        assert chunk.source_block_ids == ()
        assert chunk.generated_item is False


class _FakeChromaCollection:
    def __init__(self) -> None:
        self.ids: list[str] = []
        self.documents: list[str] = []
        self.metadatas: list[dict[str, Any]] = []
        self.get_calls = 0

    def upsert(self, **kwargs: Any) -> None:
        self.ids = list(kwargs["ids"])
        self.documents = list(kwargs["documents"])
        self.metadatas = [dict(item) for item in kwargs["metadatas"]]

    def query(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "ids": [self.ids],
            "documents": [self.documents],
            "metadatas": [self.metadatas],
            "distances": [[0.0 for _item in self.ids]],
        }

    def get(self, **_kwargs: Any) -> dict[str, Any]:
        self.get_calls += 1
        return {
            "ids": self.ids,
            "documents": self.documents,
            "metadatas": self.metadatas,
        }


def test_chroma_round_trips_generated_lineage_across_all_read_paths() -> None:
    collection = _FakeChromaCollection()
    store = object.__new__(ChromaVectorStore)
    store._collection = collection
    store._upsert(collection, [_lineage_chunk()])

    assert collection.metadatas[0]["source_block_ids_json"] == '["block-a","block-b"]'
    assert collection.metadatas[0]["generated_item"] is True
    _assert_lineage(
        store._query_collection(collection, "kb-1", [1.0, 0.0], 1)[0]
    )
    _assert_lineage(store._stored_chunks(collection.get(), fallback_doc_id="doc-1")[0])
    stored = store._get_chunk_from_collection(collection, "kb-1", "chunk-1")
    assert stored is not None
    _assert_lineage(stored)


def test_chroma_v3_receipt_reads_only_the_authoritative_namespace_collection() -> None:
    namespace = (
        "kb-lineage::v3::kpv-lineage::"
        + ("a" * 64)
        + "::dim-2::cosine_v1"
    )
    target = _FakeChromaCollection()
    authoritative = _lineage_chunk()
    authoritative.id = "kpv-lineage_doc-1_chunk_0"
    authoritative.kb_id = namespace
    authoritative.doc_id = "kpv-lineage_doc-1"
    authoritative.text = "authoritative"
    target.upsert(
        ids=[authoritative.id],
        documents=[authoritative.text],
        embeddings=[[1.0, 0.0]],
        metadatas=[
            {
                "kb_id": namespace,
                "doc_id": authoritative.doc_id,
                "document_name": authoritative.document_name,
                "chunk_index": 0,
            }
        ],
    )
    decoy = _FakeChromaCollection()
    decoy.upsert(
        ids=[authoritative.id],
        documents=["decoy"],
        embeddings=[[0.0, 1.0]],
        metadatas=[
            {
                "kb_id": namespace,
                "doc_id": authoritative.doc_id,
                "document_name": "decoy.md",
                "chunk_index": 0,
            }
        ],
    )
    store = object.__new__(ChromaVectorStore)
    store._namespace_collection = types.MethodType(  # type: ignore[method-assign]
        lambda _self, requested, *, create: target
        if requested == namespace and not create
        else None,
        store,
    )
    store._managed_collections = types.MethodType(  # type: ignore[method-assign]
        lambda _self: [decoy, target],
        store,
    )

    chunks = store.list_document_chunks(
        authoritative.doc_id,
        kb_id=namespace,
    )

    assert [chunk.text for chunk in chunks] == ["authoritative"]
    assert target.get_calls == 1
    assert decoy.get_calls == 0


def test_chroma_v3_namespace_identity_conflict_uses_contract_error() -> None:
    namespace = (
        "kb-lineage::v3::kpv-lineage::"
        + ("a" * 64)
        + "::dim-2::cosine_v1"
    )
    collection = _FakeChromaCollection()
    collection.ids = ["chunk-1"]
    collection.documents = ["conflicting namespace"]
    collection.metadatas = [
        {
            "kb_id": "different-namespace",
            "doc_id": "doc-1",
            "document_name": "conflict.md",
            "chunk_index": 0,
        }
    ]
    store = object.__new__(ChromaVectorStore)
    store._namespace_collection = types.MethodType(  # type: ignore[method-assign]
        lambda _self, requested, *, create: collection
        if requested == namespace and not create
        else None,
        store,
    )

    with pytest.raises(VectorStoreContractError, match="namespace identity"):
        store.list_document_chunks("doc-1", kb_id=namespace)


def test_chroma_duplicate_cross_collection_identity_uses_contract_error() -> None:
    collections = []
    for text in ("first", "duplicate"):
        collection = _FakeChromaCollection()
        collection.ids = ["chunk-1"]
        collection.documents = [text]
        collection.metadatas = [
            {
                "kb_id": "kb-1",
                "doc_id": "doc-1",
                "document_name": "duplicate.md",
                "chunk_index": 0,
            }
        ]
        collections.append(collection)
    store = object.__new__(ChromaVectorStore)
    store._managed_collections = types.MethodType(  # type: ignore[method-assign]
        lambda _self: collections,
        store,
    )

    with pytest.raises(VectorStoreContractError, match="duplicate chunk identity"):
        store.list_document_chunks("doc-1")


def test_chroma_legacy_metadata_uses_safe_lineage_defaults() -> None:
    collection = _FakeChromaCollection()
    collection.ids = ["legacy-chunk"]
    collection.documents = ["legacy child"]
    collection.metadatas = [
        {
            "kb_id": "kb-1",
            "doc_id": "doc-1",
            "document_name": "legacy.md",
            "parent_text": "legacy parent",
            "source_block_id": "legacy-block",
        }
    ]
    store = object.__new__(ChromaVectorStore)

    for chunk in (
        store._query_collection(collection, "kb-1", [1.0, 0.0], 1)[0],
        store._stored_chunks(collection.get(), fallback_doc_id="doc-1")[0],
        store._get_chunk_from_collection(collection, "kb-1", "legacy-chunk"),
    ):
        assert chunk is not None
        assert chunk.parent_text == "legacy parent"
        assert chunk.source_block_ids == ()
        assert chunk.generated_item is False
