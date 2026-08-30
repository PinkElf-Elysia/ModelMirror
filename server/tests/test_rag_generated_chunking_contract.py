from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.rag.embedder import EmbeddingClient
from server.rag.content_identity import (
    generated_parent_identity,
    generated_parent_window_identity,
    generated_segment_source_mapping,
    generated_source_block_match_status,
)
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.processor_generator import ProcessorGenerationService
from server.rag.rag_service import RagService
from server.rag.retrieval import RetrievalCandidate, select_v3_candidates
from server.rag.splitter import estimate_mixed_cjk_latin_v1_tokens
from server.rag.vector_store import LocalJsonVectorStore


def _service(tmp_path: Path) -> RagService:
    storage = tmp_path / "rag-storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "rag-uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        llm_enabled=False,
    )


def _token_chunker() -> dict[str, object]:
    return {
        "strategy": "recursive_estimated_token",
        "chunk_size": 100,
        "chunk_overlap": 10,
        "separators": ["\n\n", "\n", " ", ""],
        "parent_chunk_size": 300,
        "parent_chunk_overlap": 30,
        "child_chunk_size": 100,
        "child_chunk_overlap": 10,
        "parent_separators": ["\n\n", "\n", " ", ""],
        "child_separators": ["\n\n", "\n", " ", ""],
        "size_unit": "estimated_tokens",
        "token_estimator": "mixed_cjk_latin_v1",
        "chunk_contract_version": "rag-chunker-estimated-token-v1",
    }


class _ChunkingServiceStub:
    def __init__(self, chunker: dict[str, object]) -> None:
        self.job = {
            "candidate_version_id": "version-generated-chunking-test",
            "candidate_namespace": "namespace-generated-chunking-test",
            "config_snapshot": {"stages": {"stage_chunker": chunker}},
        }
        self.receipt: dict[str, Any] = {}

    def get_pipeline_job(self, _job_id: str) -> dict[str, Any]:
        return self.job

    def update_pipeline_document_chunk_counts(
        self,
        _job_id: str,
        _counts: dict[str, int],
    ) -> None:
        return None

    def update_pipeline_chunking_receipt(
        self,
        _job_id: str,
        receipt: dict[str, Any],
    ) -> None:
        self.receipt = receipt


def _parent_child_chunker() -> dict[str, object]:
    return {
        **_token_chunker(),
        "strategy": "parent_child_estimated_token",
        "parent_chunk_size": 120,
        "parent_chunk_overlap": 12,
        "child_chunk_size": 40,
        "child_chunk_overlap": 6,
    }


@pytest.mark.asyncio
async def test_generated_single_source_wrapper_maps_to_canonical_anchor(
    tmp_path: Path,
) -> None:
    source_text = "Canonical approval evidence is ORION-42."
    source_block = {
        "block_id": "source-block",
        "kind": "paragraph",
        "text": source_text,
        "start_char": 240,
        "end_char": 240 + len(source_text),
        "heading_path": [],
    }
    item = ProcessorGenerationService()._parse_items(  # noqa: SLF001
        {
            "items": [
                {
                    "question": "What is the approval evidence?",
                    "answer": "ORION-42",
                    "block_ids": ["source-block"],
                }
            ]
        },
        allowed_blocks=[source_block],
        mode="qa",
        max_items=1,
    )[0].payload(max_text=None)

    source_range = item["context_source_ranges"][0]
    assert item["context_text"][
        source_range["context_start"] : source_range["context_end"]
    ] == source_text
    assert (source_range["source_start"], source_range["source_end"]) == (
        240,
        240 + len(source_text),
    )

    processed = {
        "document_id": "doc-generated-wrapper",
        "blocks": [source_block],
        "generated_items": [item],
    }
    stub = _ChunkingServiceStub(_token_chunker())
    production = await KnowledgePipelineExecutor(stub)._chunk_sources(  # type: ignore[arg-type]  # noqa: SLF001
        "job-generated-wrapper",
        [
            {
                "source_id": processed["document_id"],
                "processed_document": {"blocks": processed["blocks"]},
                "generated_items": processed["generated_items"],
            }
        ],
    )
    preview, _ = _service(tmp_path)._preview_pipeline_chunks(  # noqa: SLF001
        processed,
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert len(production) == len(preview) == 1
    for chunk in (production[0], preview[0]):
        assert chunk["source_block_id"] == "source-block"
        assert chunk["source_block_match_status"] == "eligible"
        assert (int(chunk["start_char"]), int(chunk["end_char"])) == (
            240,
            240 + len(source_text),
        )


def test_generated_multi_source_mapping_is_unique_or_fail_closed() -> None:
    blocks = [
        {
            "block_id": "block-a",
            "kind": "paragraph",
            "text": "Alpha canonical evidence.",
            "start_char": 100,
            "end_char": 125,
        },
        {
            "block_id": "block-b",
            "kind": "paragraph",
            "text": "Beta canonical evidence.",
            "start_char": 400,
            "end_char": 424,
        },
    ]
    item = ProcessorGenerationService()._parse_items(  # noqa: SLF001
        {"document_summary": "Combined evidence"},
        allowed_blocks=blocks,
        mode="summary",
        max_items=1,
    )[0].payload(max_text=None)
    ranges = item["context_source_ranges"]
    first, second = ranges

    within_first = generated_segment_source_mapping(
        item["context_text"][first["context_start"] + 3 : first["context_start"] + 10],
        blocks,
        segment_start=first["context_start"] + 3,
        segment_end=first["context_start"] + 10,
        context_source_ranges=ranges,
    )
    assert within_first.source_block_id == "block-a"
    assert within_first.source_block_ids == ("block-a",)
    assert within_first.status == "eligible"
    assert (within_first.start_char, within_first.end_char) == (103, 110)

    crossing = generated_segment_source_mapping(
        item["context_text"][first["context_end"] - 3 : second["context_start"] + 3],
        blocks,
        segment_start=first["context_end"] - 3,
        segment_end=second["context_start"] + 3,
        context_source_ranges=ranges,
    )
    assert crossing.source_block_id is None
    assert crossing.source_block_ids == ("block-a", "block-b")
    assert crossing.status == "ambiguous_multi_source"
    assert (crossing.start_char, crossing.end_char) == (0, 0)

    wrapper_only = generated_segment_source_mapping(
        item["context_text"][: first["context_start"]],
        blocks,
        segment_start=0,
        segment_end=first["context_start"],
        context_source_ranges=ranges,
    )
    assert wrapper_only.source_block_id is None
    assert wrapper_only.source_block_ids == ()
    assert wrapper_only.status == "unmapped"

    generated_parent_identity("doc-generated-multi", item, {
        block["block_id"]: block for block in blocks
    })
    item["context_source_ranges"][0]["source_end"] -= 1
    with pytest.raises(ValueError, match="source ranges"):
        generated_parent_identity("doc-generated-multi", item, {
            block["block_id"]: block for block in blocks
        })


@pytest.mark.asyncio
async def test_generated_wrapper_without_source_range_remains_unmapped() -> None:
    source_text = "Canonical source text that is absent from the generated wrapper."
    source_block = {
        "block_id": "source-block",
        "kind": "paragraph",
        "text": source_text,
        "start_char": 50,
        "end_char": 50 + len(source_text),
    }
    generated = {
        "item_id": "legacy-wrapper",
        "item_type": "qa",
        "index_text": "What is the answer?",
        "context_text": "Question: What is the answer?\nAnswer: A generated claim.",
        "source_block_ids": ["source-block"],
    }
    stub = _ChunkingServiceStub(_token_chunker())

    chunks = await KnowledgePipelineExecutor(stub)._chunk_sources(  # type: ignore[arg-type]  # noqa: SLF001
        "job-generated-unmapped",
        [
            {
                "source_id": "doc-generated-unmapped",
                "processed_document": {"blocks": [source_block]},
                "generated_items": [generated],
            }
        ],
    )

    assert len(chunks) == 1
    assert chunks[0]["source_block_id"] is None
    assert chunks[0]["source_block_ids"] == []
    assert chunks[0]["source_block_match_status"] == "unmapped"
    assert (chunks[0]["start_char"], chunks[0]["end_char"]) == (0, 0)


@pytest.mark.asyncio
async def test_generated_parent_child_keeps_independent_context_and_index_budgets(
    tmp_path: Path,
) -> None:
    chunker = _parent_child_chunker()
    source_text = " ".join(f"evidence_{index:03d}" for index in range(120))
    processed = {
        "document_id": "doc-generated-parent-child",
        "blocks": [
            {
                "block_id": "source-block",
                "kind": "paragraph",
                "text": source_text,
                "start_char": 200,
                "end_char": 200 + len(source_text),
                "heading_path": ["Root", "Generated evidence"],
            }
        ],
        "generated_items": [
            {
                "item_id": "summary-parent-child",
                "item_type": "summary",
                "index_text": "Grounded evidence",
                "context_text": source_text,
                "source_block_ids": ["source-block"],
            }
        ],
    }

    stub = _ChunkingServiceStub(chunker)
    production = await KnowledgePipelineExecutor(stub)._chunk_sources(  # type: ignore[arg-type]  # noqa: SLF001
        "job-generated-parent-child",
        [
            {
                "source_id": processed["document_id"],
                "processed_document": {"blocks": processed["blocks"]},
                "generated_items": processed["generated_items"],
            }
        ],
    )
    preview, _ = _service(tmp_path)._preview_pipeline_chunks(  # noqa: SLF001
        processed,
        chunker,
        kind="parent_child_chunker",
    )

    assert production and len(production) == len(preview)
    assert [item["index_text"] for item in production] == [
        item["text_preview"] for item in preview
    ]
    assert [item["context_text"] for item in production] == [
        item["context_preview"] for item in preview
    ]
    assert all(
        estimate_mixed_cjk_latin_v1_tokens(item["index_text"]) <= 40
        for item in production
    )
    assert all(
        estimate_mixed_cjk_latin_v1_tokens(item["context_text"]) <= 120
        for item in production
    )
    assert any(
        estimate_mixed_cjk_latin_v1_tokens(item["context_text"]) > 40
        for item in production
    )
    assert all(
        int(item["start_char"]) >= 200
        and int(item["end_char"]) > int(item["start_char"])
            and source_text[
                int(item["start_char"]) - 200 : int(item["end_char"]) - 200
            ]
            in str(item["context_text"])
        for item in production
    )


@pytest.mark.asyncio
async def test_generated_multi_source_parent_context_cannot_claim_one_child_block() -> None:
    alpha_text = "CTX_A " + ("alpha canonical evidence " * 40)
    beta_text = "CTX_B " + ("beta canonical evidence " * 40)
    blocks = [
        {
            "block_id": "block-a",
            "kind": "paragraph",
            "text": alpha_text,
            "start_char": 100,
            "end_char": 100 + len(alpha_text),
            "page_number": 1,
            "heading_path": ["Alpha"],
        },
        {
            "block_id": "block-b",
            "kind": "paragraph",
            "text": beta_text,
            "start_char": 500,
            "end_char": 500 + len(beta_text),
            "page_number": 2,
            "heading_path": ["Beta"],
        },
    ]
    generated = ProcessorGenerationService()._parse_items(  # noqa: SLF001
        {"document_summary": "Combined evidence"},
        allowed_blocks=blocks,
        mode="summary",
        max_items=1,
    )[0].payload(max_text=None)
    chunker = _parent_child_chunker()
    chunker.update(
        {
            "parent_chunk_size": 800,
            "parent_chunk_overlap": 80,
            "child_chunk_size": 100,
            "child_chunk_overlap": 20,
        }
    )
    stub = _ChunkingServiceStub(chunker)

    chunks = await KnowledgePipelineExecutor(stub)._chunk_sources(  # type: ignore[arg-type]  # noqa: SLF001
        "job-generated-multi-parent",
        [
            {
                "source_id": "doc-generated-multi-parent",
                "processed_document": {"blocks": blocks},
                "generated_items": [generated],
            }
        ],
    )

    assert len(chunks) > 1
    assert all("CTX_A" in chunk["context_text"] for chunk in chunks)
    assert all("CTX_B" in chunk["context_text"] for chunk in chunks)
    assert all(chunk["source_block_id"] is None for chunk in chunks)
    assert all(chunk["source_block_ids"] == ["block-a", "block-b"] for chunk in chunks)
    assert all(
        chunk["source_block_match_status"] == "ambiguous_multi_source"
        for chunk in chunks
    )
    assert all(chunk["page_number"] is None for chunk in chunks)
    assert all(chunk["heading_path"] == () for chunk in chunks)

    selected = select_v3_candidates(
        [
            RetrievalCandidate(
                chunk_id=f"generated-{index}",
                doc_id="doc-generated-multi-parent",
                document_name="generated.md",
                matched_text=chunk["index_text"],
                context_text=chunk["context_text"],
                parent_chunk_id=chunk["parent_chunk_id"],
                source_block_id=chunk["source_block_id"],
                source_block_ids=tuple(chunk["source_block_ids"]),
                generated_item=True,
                fused_score=1.0 - index / 100,
            )
            for index, chunk in enumerate(chunks)
        ],
        top_k=5,
        max_chunks_per_document=2,
    )
    assert len(selected.items) == 1
    assert selected.items[0].source_block_id is None
    assert selected.items[0].source_block_ids == ("block-a", "block-b")


@pytest.mark.asyncio
async def test_generated_parent_child_keeps_distinct_actual_parent_windows(
    tmp_path: Path,
) -> None:
    block_texts = [
        "A_WINDOW " + ("alpha evidence " * 70),
        "B_WINDOW " + ("beta evidence " * 70),
        "C_WINDOW " + ("gamma evidence " * 70),
    ]
    blocks: list[dict[str, Any]] = []
    context_parts: list[str] = []
    context_ranges: list[dict[str, Any]] = []
    context_cursor = 0
    source_cursor = 100
    for index, text in enumerate(block_texts):
        block_id = f"block-{index}"
        blocks.append(
            {
                "block_id": block_id,
                "kind": "paragraph",
                "text": text,
                "start_char": source_cursor,
                "end_char": source_cursor + len(text),
                "heading_path": [],
            }
        )
        context_parts.append(text)
        context_ranges.append(
            {
                "source_block_id": block_id,
                "context_start": context_cursor,
                "context_end": context_cursor + len(text),
                "source_start": source_cursor,
                "source_end": source_cursor + len(text),
            }
        )
        context_cursor += len(text)
        source_cursor += len(text) + 100
        if index < len(block_texts) - 1:
            context_parts.append("\n")
            context_cursor += 1

    chunker = _parent_child_chunker()
    chunker.update(
        {
            "parent_chunk_size": 100,
            "parent_chunk_overlap": 55,
            "child_chunk_size": 25,
            "child_chunk_overlap": 5,
        }
    )
    generated_item = {
        "item_id": "summary-parent-windows",
        "item_type": "summary",
        "index_text": "Combined source evidence",
        "context_text": "".join(context_parts),
        "source_block_ids": [str(block["block_id"]) for block in blocks],
        "context_source_ranges": context_ranges,
    }
    processed = {
        "document_id": "doc-generated-parent-windows",
        "blocks": blocks,
        "generated_items": [generated_item],
    }
    chunks = await KnowledgePipelineExecutor(
        _ChunkingServiceStub(chunker)
    )._chunk_sources(  # type: ignore[arg-type]  # noqa: SLF001
        "job-generated-parent-windows",
        [
            {
                "source_id": "doc-generated-parent-windows",
                "processed_document": {"blocks": blocks},
                "generated_items": [generated_item],
            }
        ],
    )

    ambiguous = [chunk for chunk in chunks if chunk["source_block_id"] is None]
    assert len(ambiguous) >= 2
    actual_parent_ids = {str(chunk["parent_chunk_id"]) for chunk in ambiguous}
    assert len(actual_parent_ids) >= 2
    assert all(parent_id.startswith("generated_v1_") for parent_id in actual_parent_ids)
    contexts_by_parent: dict[str, str] = {}
    for chunk in chunks:
        parent_id = str(chunk["parent_chunk_id"])
        context = str(chunk["context_text"])
        assert contexts_by_parent.setdefault(parent_id, context) == context

    preview, _receipt = _service(tmp_path)._preview_pipeline_chunks(  # noqa: SLF001
        processed,
        chunker,
        kind="parent_child_chunker",
    )
    assert [str(item["parent_chunk_id"]) for item in preview] == [
        str(item["parent_chunk_id"]) for item in chunks
    ]
    assert {
        str(item["parent_chunk_id"]): str(item["context_preview"])
        for item in preview
    } == {
        parent_id: context[:600]
        for parent_id, context in contexts_by_parent.items()
    }

    selected = select_v3_candidates(
        [
            RetrievalCandidate(
                chunk_id=f"ambiguous-{index}",
                doc_id="doc-generated-parent-windows",
                document_name="generated.txt",
                matched_text=str(chunk["index_text"]),
                context_text=str(chunk["context_text"]),
                parent_chunk_id=str(chunk["parent_chunk_id"]),
                source_block_id=None,
                source_block_ids=tuple(chunk["source_block_ids"]),
                generated_item=True,
                fused_score=1.0 - index / 100,
            )
            for index, chunk in enumerate(ambiguous)
        ],
        top_k=5,
        max_chunks_per_document=5,
    )
    assert len({item.parent_chunk_id for item in selected.items}) >= 2


def test_current_generated_projection_uses_persisted_chunk_source_blocks() -> None:
    parent_id = "generated_v1_" + ("a" * 64)
    chunk = SimpleNamespace(
        chunk_id="generated-a-only",
        parent_chunk_id=parent_id,
        parent_text="bounded block-a context",
        generated_item=True,
        source_block_id=None,
        source_block_ids=("block-a",),
    )

    chunks_by_block, generated_links = (
        RagService._group_indexed_chunks_by_canonical_block(  # noqa: SLF001
            [chunk],
            {parent_id: ("block-a", "block-b")},
            strict_generated_lineage=True,
        )
    )

    assert set(chunks_by_block) == {"block-a"}
    assert generated_links == {("block-a", "generated-a-only")}


def test_current_generated_projection_rejects_conflicting_singular_lineage() -> None:
    parent_id = "generated_v1_" + ("b" * 64)
    chunk = SimpleNamespace(
        chunk_id="generated-conflict",
        parent_chunk_id=parent_id,
        parent_text="bounded conflicting context",
        generated_item=True,
        source_block_id="block-a",
        source_block_ids=("block-a", "block-b"),
    )

    with pytest.raises(Exception, match="generated-item provenance is inconsistent"):
        RagService._group_indexed_chunks_by_canonical_block(  # noqa: SLF001
            [chunk],
            {parent_id: ("block-a", "block-b")},
            strict_generated_lineage=True,
        )


def test_current_generated_projection_rejects_window_text_tamper() -> None:
    parent_id = "generated_v1_" + ("c" * 64)
    window_id = generated_parent_window_identity(
        parent_id,
        "parent_0",
        "authoritative parent context",
    )
    chunk = SimpleNamespace(
        chunk_id="generated-window-tamper",
        parent_chunk_id=window_id,
        parent_text="tampered parent context",
        generated_item=True,
        source_block_id="block-a",
        source_block_ids=("block-a",),
    )

    with pytest.raises(Exception, match="generated-item provenance is inconsistent"):
        RagService._group_indexed_chunks_by_canonical_block(  # noqa: SLF001
            [chunk],
            {parent_id: ("block-a",)},
            strict_generated_lineage=True,
        )


@pytest.mark.parametrize(
    ("generated_item", "parent_chunk_id"),
    [
        (True, None),
        (False, "generated_v1_" + ("e" * 64)),
    ],
)
def test_current_generated_projection_rejects_flag_parent_mismatch(
    generated_item: bool,
    parent_chunk_id: str | None,
) -> None:
    chunk = SimpleNamespace(
        chunk_id="generated-flag-mismatch",
        parent_chunk_id=parent_chunk_id,
        parent_text="bounded context",
        generated_item=generated_item,
        source_block_id="block-a",
        source_block_ids=("block-a",),
    )

    with pytest.raises(Exception, match="generated-item provenance is inconsistent"):
        RagService._group_indexed_chunks_by_canonical_block(  # noqa: SLF001
            [chunk],
            {"generated_v1_" + ("e" * 64): ("block-a",)},
            strict_generated_lineage=True,
        )


def test_current_generated_projection_rejects_one_parent_with_two_contexts() -> None:
    parent_id = "generated_v1_" + ("f" * 64)
    chunks = [
        SimpleNamespace(
            chunk_id=f"generated-context-{index}",
            parent_chunk_id=parent_id,
            parent_text=context,
            generated_item=True,
            source_block_id="block-a",
            source_block_ids=("block-a",),
        )
        for index, context in enumerate(("first context", "second context"))
    ]

    with pytest.raises(Exception, match="generated-item provenance is inconsistent"):
        RagService._group_indexed_chunks_by_canonical_block(  # noqa: SLF001
            chunks,
            {parent_id: ("block-a",)},
            strict_generated_lineage=True,
        )


def test_legacy_generated_projection_keeps_item_global_compatibility() -> None:
    parent_id = "generated_v1_" + ("1" * 64)
    legacy = SimpleNamespace(
        chunk_id="legacy-generated",
        parent_chunk_id=parent_id,
        source_block_id=None,
        source_block_ids=(),
    )

    chunks_by_block, _links = RagService._group_indexed_chunks_by_canonical_block(  # noqa: SLF001
        [legacy],
        {parent_id: ("block-a", "block-b")},
        strict_generated_lineage=False,
    )

    assert set(chunks_by_block) == {"block-a", "block-b"}


def test_generated_match_status_rejects_negative_source_span() -> None:
    assert (
        generated_source_block_match_status(
            "block-a",
            ("block-a",),
            -1,
            100,
        )
        == "unmapped"
    )


def test_generated_children_index_their_own_segment_without_false_anchor_span(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source_text = ("alpha evidence " * 160) + "UNIQUE_TAIL_MARKER"
    marker_start = source_text.index("UNIQUE_TAIL_MARKER")
    chunks, _ = service._preview_pipeline_chunks(  # noqa: SLF001
        {
            "document_id": "doc-generated-segments",
            "blocks": [
                {
                    "block_id": "source-block",
                    "kind": "paragraph",
                    "text": source_text,
                    "start_char": 1000,
                    "end_char": 1000 + len(source_text),
                    "heading_path": ["Root", "Evidence"],
                }
            ],
            "generated_items": [
                {
                    "item_id": "summary-tail",
                    "item_type": "summary",
                    "index_text": "Summarize the evidence",
                    "context_text": source_text,
                    "source_block_ids": ["source-block"],
                    "context_source_ranges": [
                        {
                            "source_block_id": "source-block",
                            "context_start": 0,
                            "context_end": len(source_text),
                            "source_start": 1000,
                            "source_end": 1000 + len(source_text),
                        }
                    ],
                }
            ],
        },
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert len(chunks) > 1
    parent_ids = {str(item["parent_chunk_id"]) for item in chunks}
    assert len(parent_ids) > 1
    marker_chunks = [
        item for item in chunks if "UNIQUE_TAIL_MARKER" in item["text_preview"]
    ]
    assert len(marker_chunks) == 1
    assert "UNIQUE_TAIL_MARKER" not in chunks[0]["text_preview"]
    assert int(chunks[0]["end_char"]) <= 1000 + marker_start
    assert int(marker_chunks[0]["start_char"]) <= 1000 + marker_start
    assert int(marker_chunks[0]["end_char"]) >= (
        1000 + marker_start + len("UNIQUE_TAIL_MARKER")
    )

    candidates = [
        RetrievalCandidate(
            chunk_id=f"chunk-{index}",
            doc_id="doc-generated-segments",
            document_name="generated.txt",
            matched_text=str(item["text_preview"]),
            context_text=str(item["context_preview"]),
            parent_chunk_id=str(item["parent_chunk_id"]),
            source_block_id=item["source_block_id"],
            source_block_ids=tuple(item.get("source_block_ids") or []),
            generated_item=True,
            start_char=int(item["start_char"]),
            end_char=int(item["end_char"]),
            fused_score=(
                0.9 if "UNIQUE_TAIL_MARKER" in item["text_preview"] else 0.2
            ),
        )
        for index, item in enumerate(chunks)
    ]
    selected = select_v3_candidates(
        candidates,
        top_k=5,
        max_chunks_per_document=2,
    )

    assert len(selected.items) == 1
    assert "UNIQUE_TAIL_MARKER" in selected.items[0].matched_text


def test_generated_text_without_exact_source_mapping_has_unknown_span(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    chunks, _ = service._preview_pipeline_chunks(  # noqa: SLF001
        {
            "document_id": "doc-generated-unknown-span",
            "blocks": [
                {
                    "block_id": "source-block",
                    "kind": "paragraph",
                    "text": "Canonical source wording.",
                    "start_char": 40,
                    "end_char": 66,
                    "heading_path": [],
                }
            ],
            "generated_items": [
                {
                    "item_id": "paraphrase",
                    "item_type": "summary",
                    "index_text": "Paraphrased evidence",
                    "context_text": "A generated paraphrase that is not a source substring.",
                    "source_block_ids": ["source-block"],
                }
            ],
        },
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert chunks
    assert {(int(item["start_char"]), int(item["end_char"])) for item in chunks} == {
        (0, 0)
    }


def test_generated_repeated_exact_source_mapping_has_unknown_span(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    repeated = "Repeated exact evidence."
    source_text = f"{repeated} A separator. {repeated}"
    chunks, _ = service._preview_pipeline_chunks(  # noqa: SLF001
        {
            "document_id": "doc-generated-repeated-span",
            "blocks": [
                {
                    "block_id": "source-block",
                    "kind": "paragraph",
                    "text": source_text,
                    "start_char": 80,
                    "end_char": 80 + len(source_text),
                    "heading_path": [],
                }
            ],
            "generated_items": [
                {
                    "item_id": "repeated-excerpt",
                    "item_type": "summary",
                    "index_text": "Repeated evidence",
                    "context_text": repeated,
                    "source_block_ids": ["source-block"],
                }
            ],
        },
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert len(chunks) == 1
    assert (int(chunks[0]["start_char"]), int(chunks[0]["end_char"])) == (0, 0)
