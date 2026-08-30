from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import server.rag.splitter as splitter_module
from server.rag.embedder import EmbeddingClient
from server.rag.content_identity import generated_parent_identity
from server.rag.document_processor import StructuredDocumentProcessor
from server.rag.lexical_store import LexicalChunk
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.processor_generator import ProcessorGenerationService
from server.rag.rag_service import (
    PipelineContentContractError,
    PipelineDraftValidationError,
    PipelineJobStateError,
    RagService,
)
from server.rag.source_metadata import heading_path_source_hash, normalize_heading_path
from server.rag.splitter import (
    EstimatedTokenParentChildTextSplitter,
    EstimatedTokenTextSplitter,
    bounded_heading_prefix,
    estimate_mixed_cjk_latin_v1_tokens as estimate_text_tokens,
)
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


def test_estimated_token_splitter_bounds_mixed_text_and_preserves_offsets() -> None:
    text = (
        "  第一节介绍确定性的分块合同。\n\n"
        + "English evidence remains in its original source window. " * 20
        + "\n\n第二节继续说明偏移必须可以重放。"
    )
    splitter = EstimatedTokenTextSplitter(chunk_size=100, chunk_overlap=20)

    first = splitter.split_segments(text)
    second = splitter.split_segments(text)

    assert first == second
    assert len(first) >= 2
    for chunk in first:
        assert estimate_text_tokens(chunk.text) <= 100
        assert text[chunk.start_char : chunk.end_char] == chunk.text
    for previous, current in zip(first, first[1:]):
        overlap = text[current.start_char : previous.end_char]
        assert 0 < estimate_text_tokens(overlap) <= 20


def test_mixed_cjk_latin_estimator_and_ascii_overlap_are_contract_stable() -> None:
    assert estimate_text_tokens("中文") == 2
    assert estimate_text_tokens("abcdefgh") == 2
    assert estimate_text_tokens("中abcd文") == 3
    assert estimate_text_tokens("𠀀" * 4) == 4
    assert estimate_text_tokens("あ" * 4) == 4
    assert estimate_text_tokens("가" * 4) == 4
    assert estimate_text_tokens("丽" * 4) == 4
    assert estimate_text_tokens("🙂" * 4) == 1

    script_text = "𠀀あ가𠀁い나𠀂う다𠀃え라"
    script_chunks = EstimatedTokenTextSplitter(
        chunk_size=4,
        chunk_overlap=0,
        separators=[""],
    ).split_segments(script_text)
    assert len(script_chunks) == 3
    assert [estimate_text_tokens(chunk.text) for chunk in script_chunks] == [4, 4, 4]
    assert all(
        script_text[chunk.start_char : chunk.end_char] == chunk.text
        for chunk in script_chunks
    )

    text = "a" * 1_200
    chunks = EstimatedTokenTextSplitter(
        chunk_size=100,
        chunk_overlap=20,
        separators=[""],
    ).split_segments(text)

    assert len(chunks) >= 3
    for previous, current in zip(chunks, chunks[1:]):
        overlap = text[current.start_char : previous.end_char]
        assert estimate_text_tokens(overlap) == 20
        assert len(overlap) > 20


def test_estimated_token_splitter_measurement_work_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = splitter_module.estimate_mixed_cjk_latin_v1_tokens
    measured_lengths: list[int] = []

    def measured(value: str) -> int:
        measured_lengths.append(len(value))
        return original(value)

    monkeypatch.setattr(
        splitter_module,
        "estimate_mixed_cjk_latin_v1_tokens",
        measured,
    )
    text = "abcdefgh" * 12_500
    chunks = splitter_module.EstimatedTokenTextSplitter(
        chunk_size=100,
        chunk_overlap=20,
        separators=[""],
    ).split_segments(text)

    assert chunks
    assert measured_lengths
    assert max(measured_lengths) <= 400
    assert sum(measured_lengths) <= len(text) * 80


def test_generated_parent_identity_binds_payload_order_and_block_hashes() -> None:
    blocks = {
        "a": {"block_id": "a", "kind": "paragraph", "text": "Evidence A"},
        "b": {"block_id": "b", "kind": "paragraph", "text": "Evidence B"},
    }
    item = {
        "item_id": "summary-1",
        "item_type": "summary",
        "index_text": "Combined evidence",
        "context_text": "Evidence A and Evidence B",
        "source_block_ids": ["a", "b"],
    }
    identity, source_ids = generated_parent_identity("doc", item, blocks)

    assert identity.startswith("generated_v1_")
    assert source_ids == ("a", "b")
    for mutation in (
        {**item, "index_text": "Changed index"},
        {**item, "context_text": "Changed context"},
        {**item, "source_block_ids": ["b", "a"]},
    ):
        changed, _ = generated_parent_identity("doc", mutation, blocks)
        assert changed != identity
    changed_blocks = {**blocks, "b": {**blocks["b"], "text": "Tampered"}}
    changed, _ = generated_parent_identity("doc", item, changed_blocks)
    assert changed != identity

    with pytest.raises(ValueError):
        generated_parent_identity(
            "doc",
            {**item, "source_block_ids": ["a", "a"]},
            blocks,
        )

    with pytest.raises(ValueError, match="incomplete"):
        generated_parent_identity(
            "doc",
            {**item, "index_text": "   "},
            blocks,
        )


def test_deep_heading_prefix_preserves_root_and_true_leaf_with_receipt() -> None:
    prefix, truncated = bounded_heading_prefix(
        [f"H{index}" for index in range(13)],
        budget=64,
    )

    assert truncated is True
    assert prefix == "H0 > H12"


@pytest.mark.asyncio
async def test_character_bounded_deep_heading_keeps_root_and_true_leaf() -> None:
    root = "ROOT_TRUE_" + ("r" * 240)
    leaf = "LEAF_TRUE_" + ("l" * 240)
    heading_path = [
        root,
        *[
            f"MIDDLE_{index:02d}_" + ("m" * 240)
            for index in range(11)
        ],
        leaf,
    ]

    normalized = normalize_heading_path(heading_path)
    assert normalized[0].startswith("ROOT_TRUE_")
    assert normalized[-1].startswith("LEAF_TRUE_")

    service = _ChunkingServiceStub(
        _token_chunker(chunk_size=140, chunk_overlap=20)
    )
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    chunks = await executor._chunk_sources(  # noqa: SLF001
        "job-deep-heading",
        [
            {
                "source_id": "doc-deep-heading",
                "processed_document": {
                    "blocks": [
                        {
                            "block_id": "block-deep-heading",
                            "kind": "paragraph",
                            "text": "Stable evidence body. " * 20,
                            "start_char": 0,
                            "end_char": len("Stable evidence body. " * 20),
                            "heading_path": heading_path,
                        }
                    ]
                },
            }
        ],
    )

    assert chunks
    for chunk in chunks:
        assert chunk["heading_path"][0].startswith("ROOT_TRUE_")
        assert chunk["heading_path"][-1].startswith("LEAF_TRUE_")
        index_heading = chunk["index_text"].splitlines()[0]
        context_heading = chunk["context_text"].splitlines()[0]
        assert "ROOT_TRUE_" in index_heading
        assert "LEAF_TRUE_" in index_heading
        assert context_heading == index_heading


@pytest.mark.asyncio
async def test_short_deep_heading_records_source_truncation_in_executor_and_preview(
    tmp_path: Path,
) -> None:
    heading_path = [f"H{index}" for index in range(13)]
    body = "Stable evidence body. " * 8
    chunker = _token_chunker(chunk_size=100, chunk_overlap=20)
    processed = StructuredDocumentProcessor().process(
        tmp_path / "short-deep-heading.png",
        filename="short-deep-heading.png",
        source_id="doc-short-deep-heading",
        extra_blocks=[
            {
                "block_id": "block-short-deep-heading",
                "kind": "paragraph",
                "text": body,
                "start_char": 0,
                "end_char": len(body),
                "heading_path": heading_path,
            }
        ],
    ).payload(include_text=True, max_block_text=None)
    processed["document_id"] = "doc-short-deep-heading"
    processed["generated_items"] = []
    assert processed["blocks"][0]["heading_path_source_truncated"] is True
    assert len(processed["blocks"][0]["heading_path_source_hash"]) == 64
    service = _ChunkingServiceStub(chunker)
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]

    chunks = await executor._chunk_sources(  # noqa: SLF001
        "job-short-deep-heading",
        [
            {
                "source_id": processed["document_id"],
                "processed_document": processed,
            }
        ],
    )
    preview_service = _service(tmp_path / "preview-short-deep-heading")
    preview, preview_receipt = preview_service._preview_pipeline_chunks(  # noqa: SLF001
        processed,
        chunker,
        kind="recursive_chunker",
    )

    assert chunks and preview
    assert service.receipt["heading_prefix_truncated_count"] == 1
    assert preview_receipt["heading_prefix_truncated_count"] == 1
    assert [chunk["index_text"] for chunk in chunks] == [
        item["text_preview"] for item in preview
    ]
    assert [chunk["context_text"] for chunk in chunks] == [
        item["context_preview"] for item in preview
    ]
    assert all(chunk["heading_path"][0] == "H0" for chunk in chunks)
    assert all(chunk["heading_path"][-1] == "H12" for chunk in chunks)
    assert all(chunk["index_text"].splitlines()[0] == "H0 > H12" for chunk in chunks)


@pytest.mark.asyncio
async def test_single_long_heading_is_not_duplicated_after_processor_truncation(
    tmp_path: Path,
) -> None:
    heading = "LONG_ROOT_" + ("h" * 280)
    body = "Evidence body remains available after the bounded heading. " * 8
    source_path = tmp_path / "single-long-heading.md"
    source_path.write_text(f"# {heading}\n\n{body}", encoding="utf-8")
    processed = StructuredDocumentProcessor().process(
        source_path,
        filename=source_path.name,
        source_id="doc-single-long-heading",
    ).payload(include_text=True, max_block_text=None)
    processed["document_id"] = "doc-single-long-heading"
    processed["generated_items"] = []
    paragraph = next(
        block for block in processed["blocks"] if block["kind"] == "paragraph"
    )
    assert paragraph["heading_path_source_truncated"] is True

    chunker = _token_chunker(chunk_size=100, chunk_overlap=20)
    service = _ChunkingServiceStub(chunker)
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    chunks = await executor._chunk_sources(  # noqa: SLF001
        "job-single-long-heading",
        [
            {
                "source_id": processed["document_id"],
                "processed_document": processed,
            }
        ],
    )
    preview_service = _service(tmp_path / "preview-single-long-heading")
    preview, preview_receipt = preview_service._preview_pipeline_chunks(  # noqa: SLF001
        processed,
        chunker,
        kind="recursive_chunker",
    )

    assert chunks and preview
    assert service.receipt["heading_prefix_truncated_count"] == 1
    assert preview_receipt["heading_prefix_truncated_count"] == 1
    assert [chunk["index_text"] for chunk in chunks] == [
        item["text_preview"] for item in preview
    ]
    assert all(" > " not in chunk["index_text"].splitlines()[0] for chunk in chunks)
    assert any("Evidence body" in chunk["index_text"] for chunk in chunks)


@pytest.mark.parametrize("serialized", [False, True])
def test_extra_document_block_round_trip_preserves_heading_lineage(
    tmp_path: Path,
    serialized: bool,
) -> None:
    processor = StructuredDocumentProcessor()
    heading_path = [f"H{index}" for index in range(13)]
    original = processor._block(  # noqa: SLF001 - exercise the canonical block contract.
        "source-lineage",
        "paragraph",
        "Grounded lineage evidence.",
        0,
        26,
        heading_path=heading_path,
    )
    extra = original.payload(max_text=None) if serialized else original

    processed = processor.process(
        tmp_path / f"lineage-round-trip-{serialized}.png",
        filename=f"lineage-round-trip-{serialized}.png",
        source_id="source-lineage",
        extra_blocks=[extra],
    )

    assert len(processed.blocks) == 1
    round_tripped = processed.blocks[0]
    assert round_tripped.heading_path == original.heading_path
    assert round_tripped.heading_path_source_hash == original.heading_path_source_hash
    assert round_tripped.heading_path_source_truncated is True


@pytest.mark.parametrize(
    ("inherited_hash", "inherited_truncated"),
    [
        ("not-a-sha256", True),
        ("f" * 64, "true"),
        ("f" * 64, False),
    ],
)
def test_extra_block_recomputes_malformed_heading_lineage(
    tmp_path: Path,
    inherited_hash: str,
    inherited_truncated: object,
) -> None:
    heading_path = ["Root", "Leaf"]
    processed = StructuredDocumentProcessor().process(
        tmp_path / "malformed-lineage.png",
        filename="malformed-lineage.png",
        source_id="source-malformed-lineage",
        extra_blocks=[
            {
                "kind": "image_description",
                "text": "Grounded visual evidence.",
                "heading_path": heading_path,
                "heading_path_source_hash": inherited_hash,
                "heading_path_source_truncated": inherited_truncated,
            }
        ],
    )

    block = processed.blocks[0]
    assert block.heading_path == heading_path
    assert block.heading_path_source_hash == heading_path_source_hash(heading_path)
    assert block.heading_path_source_truncated is False


def test_estimated_token_parent_child_splitter_uses_independent_budgets() -> None:
    text = "证据段落。" * 240
    splitter = EstimatedTokenParentChildTextSplitter(
        parent_chunk_size=180,
        parent_chunk_overlap=24,
        child_chunk_size=72,
        child_chunk_overlap=12,
    )

    chunks = splitter.split_segments(text)

    assert chunks
    assert all(chunk.chunk_type == "child" for chunk in chunks)
    assert all(estimate_text_tokens(chunk.text) <= 72 for chunk in chunks)
    assert all(estimate_text_tokens(chunk.parent_text or "") <= 180 for chunk in chunks)
    assert all(text[chunk.start_char : chunk.end_char] == chunk.text for chunk in chunks)


def test_new_draft_declares_token_chunking_and_aggregate_content_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("token contract")

    draft = service.get_pipeline_draft(kb["id"])
    chunker = next(stage for stage in draft["stages"] if stage["kind"] == "chunker")

    assert chunker["config"]["strategy"] == "recursive_estimated_token"
    assert chunker["config"]["size_unit"] == "estimated_tokens"
    assert chunker["config"]["token_estimator"] == "mixed_cjk_latin_v1"
    assert chunker["config"]["chunk_contract_version"] == (
        "rag-chunker-estimated-token-v1"
    )
    assert draft["content_index_contract"] == {
        "contract_version": "rag-content-index-contract-v1",
        "chunker_contract_version": "rag-chunker-estimated-token-v1",
        "lexical_contract_version": "sqlite-fts5-lexical-v1",
        "parser_contract_version": "structured-local-parser-v1",
        "status": "legacy_read_only",
        "components": {
            "chunker": "current",
            "lexical": "legacy_read_only",
            "parser": "legacy_read_only",
        },
    }


def test_legacy_character_env_values_are_not_reinterpreted_as_token_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_CHUNK_SIZE", "1777")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "177")
    monkeypatch.delenv("RAG_TOKEN_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("RAG_TOKEN_CHUNK_OVERLAP", raising=False)
    service = _service(tmp_path)
    kb = service.create_knowledge_base("separate budget units")

    draft = service.get_pipeline_draft(str(kb["id"]))
    chunker = next(stage for stage in draft["stages"] if stage["kind"] == "chunker")

    assert service.splitter.chunk_size == 1777
    assert service.splitter.chunk_overlap == 177
    assert chunker["config"]["chunk_size"] == 500
    assert chunker["config"]["chunk_overlap"] == 50


def test_legacy_lexical_contract_cannot_create_new_fulltext_or_hybrid_job(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("lexical v1 read only")
    kb_id = str(kb["id"])
    document = service.uploads_dir / "source.txt"
    document.write_bytes(b"source-only evidence")
    with service._metadata_lock:  # noqa: SLF001 - source-only contract fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["documents"]["doc-source"] = {
            "id": "doc-source",
            "kb_id": kb_id,
            "filename": "source.txt",
            "stored_path": str(document),
            "size": document.stat().st_size,
            "chunk_count": 0,
            "content_type": "text/plain",
            "ingestion_status": "pipeline_required",
            "visual_candidate": False,
            "warnings": [],
            "content_hash": service._file_sha256(document),  # noqa: SLF001
            "created_at": 1.0,
        }
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    for mode in ("hybrid", "fulltext"):
        draft = service.update_pipeline_draft(
            kb_id,
            {},
            retrieval_profile={"mode": mode},
        )
        with pytest.raises(PipelineContentContractError) as blocked:
            service.create_pipeline_job(
                kb_id,
                draft_version=int(draft["version"]),
                source_document_ids=["doc-source"],
            )
        assert blocked.value.code == "rag_content_contract_legacy_read_only"
        with service._metadata_lock:  # noqa: SLF001
            metadata = service._read_metadata_unlocked()  # noqa: SLF001
            assert metadata["pipeline_jobs"] == {}


def test_stored_future_component_labels_cannot_forge_current_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    contract = service._content_index_contract(  # noqa: SLF001
        {
            "stage_chunker": _token_chunker(),
            "stage_processor": {
                "parser": "structured_local_parser",
                "parser_contract_version": "canonical-structured-parser-v2",
            },
        },
        {
            "contract_version": "rag-content-index-contract-v1",
            "lexical_contract_version": "sqlite-fts5-lexical-v2",
            "parser_contract_version": "canonical-structured-parser-v2",
            "status": "current",
        },
        index_contract={
            "lexical": {
                "required": True,
                "backend": "sqlite_fts5",
                "contract_version": "sqlite-fts5-lexical-v2",
            }
        },
    )

    assert contract["components"] == {
        "chunker": "current",
        "lexical": "legacy_read_only",
        "parser": "legacy_read_only",
    }
    assert contract["status"] == "legacy_read_only"


@pytest.mark.parametrize(
    "raw_chunker",
    [
        {},
        {"chunk_size": 800, "chunk_overlap": 80},
    ],
)
def test_historical_partial_chunker_draft_remains_character_read_only(
    tmp_path: Path,
    raw_chunker: dict[str, Any],
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("partial historical chunker")
    kb_id = str(kb["id"])
    with service._metadata_lock:  # noqa: SLF001 - historical persistence fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        draft = service._pipeline_draft_record(metadata, kb_id)  # noqa: SLF001
        draft["stages"]["stage_chunker"] = raw_chunker
        draft.pop("content_index_contract", None)
        metadata["pipeline_drafts"][kb_id] = draft
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    projected = service.get_pipeline_draft(kb_id)
    chunker = next(stage for stage in projected["stages"] if stage["kind"] == "chunker")

    assert chunker["config"]["strategy"] == "recursive_character"
    assert chunker["config"]["size_unit"] == "characters"
    assert chunker["config"]["token_estimator"] is None
    assert projected["content_index_contract"]["components"]["chunker"] == (
        "legacy_read_only"
    )


@pytest.mark.parametrize("missing_chunker", [False, True])
def test_malformed_historical_draft_cannot_silently_upgrade_to_token_default(
    tmp_path: Path,
    missing_chunker: bool,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("malformed legacy draft")
    kb_id = str(kb["id"])
    with service._metadata_lock:  # noqa: SLF001 - malformed persistence fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        draft = service._pipeline_draft_record(metadata, kb_id)  # noqa: SLF001
        if missing_chunker:
            draft["stages"].pop("stage_chunker", None)
        else:
            draft["stages"]["stage_chunker"] = {
                "strategy": "recursive_character",
                "chunk_size": 1,
                "chunk_overlap": 0,
            }
        draft.pop("content_index_contract", None)
        metadata["pipeline_drafts"][kb_id] = draft
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    projected = service.get_pipeline_draft(kb_id)
    chunker = next(stage for stage in projected["stages"] if stage["kind"] == "chunker")
    assert projected["content_index_contract"]["components"]["chunker"] == (
        "legacy_read_only"
    )
    assert chunker["config"]["contract_error_code"] == (
        "rag_chunker_config_invalid"
    )
    with pytest.raises(PipelineContentContractError):
        service.create_pipeline_job(kb_id, draft_version=projected["version"])


def test_legacy_character_draft_must_be_explicitly_upgraded_before_build(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("legacy draft")
    kb_id = str(kb["id"])

    with service._metadata_lock:  # noqa: SLF001 - contract fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        draft = service._pipeline_draft_record(metadata, kb_id)  # noqa: SLF001
        draft["stages"]["stage_chunker"]["strategy"] = "recursive_character"
        draft["stages"]["stage_chunker"].pop("chunk_contract_version", None)
        draft["stages"]["stage_chunker"].pop("size_unit", None)
        draft["stages"]["stage_chunker"].pop("token_estimator", None)
        metadata["pipeline_drafts"][kb_id] = draft
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineContentContractError) as blocked:
        service.create_pipeline_job(kb_id, draft_version=1)
    assert blocked.value.code == "rag_content_contract_legacy_read_only"

    with pytest.raises(PipelineDraftValidationError, match="explicitly upgraded"):
        service.update_pipeline_draft(
            kb_id,
            {"stage_chunker": {"config": {"chunk_size": 600}}},
        )

    with pytest.raises(PipelineDraftValidationError, match="size and overlap"):
        service.update_pipeline_draft(
            kb_id,
            {
                "stage_chunker": {
                    "config": {"strategy": "recursive_estimated_token"}
                }
            },
        )

    upgraded = service.update_pipeline_draft(
        kb_id,
        {
            "stage_chunker": {
                "config": {
                    "strategy": "recursive_estimated_token",
                    "chunk_size": 600,
                    "chunk_overlap": 60,
                }
            }
        },
    )
    chunker = next(stage for stage in upgraded["stages"] if stage["kind"] == "chunker")
    assert chunker["config"]["strategy"] == "recursive_estimated_token"


def test_switching_estimated_token_strategy_family_requires_target_budget(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("token family switch")
    kb_id = str(kb["id"])

    with pytest.raises(PipelineDraftValidationError, match="target strategy"):
        service.update_pipeline_draft(
            kb_id,
            {
                "stage_chunker": {
                    "config": {"strategy": "parent_child_estimated_token"}
                }
            },
        )

    parent = service.update_pipeline_draft(
        kb_id,
        {
            "stage_chunker": {
                "config": {
                    "strategy": "parent_child_estimated_token",
                    "parent_chunk_size": 1200,
                    "parent_chunk_overlap": 120,
                    "child_chunk_size": 300,
                    "child_chunk_overlap": 30,
                }
            }
        },
    )
    assert next(
        stage for stage in parent["stages"] if stage["kind"] == "chunker"
    )["config"]["parent_chunk_size"] == 1200

    with pytest.raises(PipelineDraftValidationError, match="target strategy"):
        service.update_pipeline_draft(
            kb_id,
            {
                "stage_chunker": {
                    "config": {"strategy": "recursive_estimated_token"}
                }
            },
        )


@pytest.mark.asyncio
async def test_legacy_content_version_is_query_compatible_but_only_prior_active_can_rollback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("legacy rollback")
    kb_id = str(kb["id"])
    profile = service._default_embedding_profile()  # noqa: SLF001
    base: dict[str, Any] = {
        "version_id": "kpv_legacy_content_active",
        "kb_id": kb_id,
        "version": 1,
        "status": "ready",
        "namespace": "legacy-content",
        "draft_id": f"draft_{kb_id}",
        "draft_version": 1,
        "index_schema_version": 2,
        "embedding_profile": profile,
        "embedding_space_fingerprint": profile["embedding_space_fingerprint"],
        "retrieval_profile": {"mode": "fulltext", "top_k": 2},
        "vector_index_ready": False,
        "lexical_index_ready": True,
        "source_summary": [],
        "document_count": 1,
        "chunk_count": 1,
        "job_id": "legacy-content-job",
        "created_at": 1.0,
        "activated_at": 1.0,
    }
    never_active = {
        **base,
        "version_id": "kpv_legacy_content_never_active",
        "version": 2,
        "activated_at": None,
    }
    with service._metadata_lock:  # noqa: SLF001 - compatibility fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_versions"][base["version_id"]] = dict(base)
        metadata["pipeline_versions"][never_active["version_id"]] = never_active
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    service.lexical_store.add_chunks(
        [
            LexicalChunk(
                chunk_id="legacy-query-chunk",
                namespace="legacy-content",
                doc_id=f"{base['version_id']}_doc-legacy",
                document_name="legacy.txt",
                text="Historical rollback evidence remains queryable.",
                chunk_index=0,
            )
        ]
    )
    query = await service.query_pipeline_version(
        base["version_id"],
        "rollback evidence",
        top_k=2,
        generate_answer=False,
    )

    assert service.get_pipeline_version(base["version_id"])["chunk_count"] == 1
    assert query["sources"][0]["chunk_id"] == "legacy-query-chunk"
    assert service.activate_pipeline_version(base["version_id"])["active"] is True
    with pytest.raises(PipelineJobStateError, match="Legacy V2 indexes"):
        service.activate_pipeline_version(never_active["version_id"])
    with pytest.raises(PipelineJobStateError, match="Legacy V2 indexes"):
        service.activate_pipeline_version(base["version_id"], promotion=True)


class _ChunkingServiceStub:
    def __init__(self, chunker: dict[str, Any]) -> None:
        self.job = {
            "candidate_version_id": "kpv_chunking_contract",
            "candidate_namespace": "kb_chunking_contract__v3",
            "config_snapshot": {"stages": {"stage_chunker": chunker}},
        }
        self.counts: dict[str, int] = {}
        self.receipt: dict[str, Any] = {}

    def get_pipeline_job(self, _job_id: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.job))

    def update_pipeline_document_chunk_counts(
        self,
        _job_id: str,
        counts: dict[str, int],
    ) -> None:
        self.counts = dict(counts)

    def update_pipeline_chunking_receipt(
        self,
        _job_id: str,
        receipt: dict[str, Any],
    ) -> None:
        self.receipt = json.loads(json.dumps(receipt))


def _token_chunker(**overrides: Any) -> dict[str, Any]:
    return {
        "strategy": "recursive_estimated_token",
        "chunk_size": 100,
        "chunk_overlap": 20,
        "separators": ["\n\n", "\n", "。", " ", ""],
        "parent_chunk_size": 180,
        "parent_chunk_overlap": 24,
        "child_chunk_size": 72,
        "child_chunk_overlap": 12,
        "parent_separators": ["\n\n", "\n", "。", " ", ""],
        "child_separators": ["\n\n", "\n", "。", " ", ""],
        "size_unit": "estimated_tokens",
        "token_estimator": "mixed_cjk_latin_v1",
        "chunk_contract_version": "rag-chunker-estimated-token-v1",
        **overrides,
    }


@pytest.mark.asyncio
async def test_markdown_parser_offsets_replay_the_original_chunk_body(
    tmp_path: Path,
) -> None:
    source_text = "# Root\n\n  indented evidence line\n"
    source_path = tmp_path / "offsets.md"
    source_path.write_bytes(source_text.encode("utf-8"))
    processed = StructuredDocumentProcessor().process(
        source_path,
        filename="offsets.md",
        source_id="doc-offsets",
    )
    paragraph = next(block for block in processed.blocks if block.kind == "paragraph")

    assert source_text[paragraph.start_char : paragraph.end_char] == paragraph.text

    service = _ChunkingServiceStub(_token_chunker())
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    chunks = await executor._chunk_sources(  # noqa: SLF001
        "job-offsets",
        [
            {
                "source_id": "doc-offsets",
                "processed_document": processed.payload(
                    include_text=True,
                    max_block_text=None,
                ),
            }
        ],
    )
    paragraph_chunk = next(
        chunk for chunk in chunks if chunk["source_block_id"] == paragraph.block_id
    )
    indexed_body = paragraph_chunk["index_text"].split("\n", 1)[-1]
    assert source_text[
        paragraph_chunk["start_char"] : paragraph_chunk["end_char"]
    ] == indexed_body


@pytest.mark.asyncio
async def test_pipeline_chunking_skips_heading_prefixes_both_texts_and_deduplicates() -> None:
    service = _ChunkingServiceStub(_token_chunker())
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    repeated = "Deterministic evidence body " * 10
    parsed = [
        {
            "source_id": "doc-1",
            "processed_document": {
                "blocks": [
                    {
                        "block_id": "heading-1",
                        "kind": "heading",
                        "text": "# Root",
                        "start_char": 0,
                        "end_char": 6,
                        "heading_path": ["Root"],
                    },
                    {
                        "block_id": "block-a",
                        "kind": "paragraph",
                        "text": repeated,
                        "start_char": 7,
                        "end_char": 7 + len(repeated),
                        "heading_path": ["Root", "A very long leaf heading " * 20],
                    },
                    {
                        "block_id": "block-a",
                        "kind": "paragraph",
                        "text": repeated,
                        "start_char": 7,
                        "end_char": 7 + len(repeated),
                        "heading_path": ["Root", "A very long leaf heading " * 20],
                    },
                    {
                        "block_id": "block-b",
                        "kind": "paragraph",
                        "text": repeated,
                        "start_char": 500,
                        "end_char": 500 + len(repeated),
                        "heading_path": ["Root"],
                    },
                ]
            },
        }
    ]

    chunks = await executor._chunk_sources("job-1", parsed)  # noqa: SLF001

    assert chunks
    assert all(chunk["chunk_type"] != "heading" for chunk in chunks)
    assert all(estimate_text_tokens(chunk["index_text"]) <= 100 for chunk in chunks)
    assert all(estimate_text_tokens(chunk["context_text"]) <= 100 for chunk in chunks)
    assert all(chunk["index_text"].splitlines()[0].startswith("Root") for chunk in chunks)
    assert all(chunk["context_text"].splitlines()[0].startswith("Root") for chunk in chunks)
    assert all(
        chunk["index_text"].splitlines()[0]
        == chunk["context_text"].splitlines()[0]
        for chunk in chunks
    )
    assert {chunk["source_block_id"] for chunk in chunks} == {"block-a", "block-b"}
    assert service.receipt["heading_block_count"] == 1
    assert service.receipt["deduplicated_chunk_count"] > 0
    assert service.receipt["heading_prefix_truncated_count"] > 0


@pytest.mark.asyncio
async def test_pipeline_chunking_deduplicates_document_scoped_content_without_block_id() -> None:
    service = _ChunkingServiceStub(_token_chunker())
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    parsed = [
        {
            "source_id": "doc-no-block-id",
            "processed_document": {
                "blocks": [
                    {
                        "kind": "paragraph",
                        "text": "A stable evidence",
                        "start_char": 100,
                        "end_char": 117,
                    },
                    {
                        "kind": "paragraph",
                        "text": "Ａ   stable evidence",
                        "start_char": 0,
                        "end_char": 19,
                    },
                ]
            },
        }
    ]

    chunks = await executor._chunk_sources("job-no-block-id", parsed)  # noqa: SLF001

    assert len(chunks) == 1
    assert chunks[0]["start_char"] == 0
    assert service.receipt["deduplicated_chunk_count"] == 1


@pytest.mark.asyncio
async def test_pipeline_chunking_never_deduplicates_across_documents() -> None:
    service = _ChunkingServiceStub(_token_chunker())
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    parsed = [
        {
            "source_id": source_id,
            "processed_document": {
                "blocks": [
                    {
                        "block_id": "shared-block-id",
                        "kind": "paragraph",
                        "text": "Ａ   stable evidence shared by two documents",
                        "start_char": 0,
                        "end_char": 43,
                    }
                ]
            },
        }
        for source_id in ("doc-left", "doc-right")
    ]

    first = await executor._chunk_sources("job-cross-document", parsed)  # noqa: SLF001
    first_hash = service.receipt["chunk_sequence_hash"]
    second = await executor._chunk_sources("job-cross-document", parsed)  # noqa: SLF001

    assert len(first) == len(second) == 2
    assert {chunk["source"]["source_id"] for chunk in first} == {
        "doc-left",
        "doc-right",
    }
    assert service.receipt["deduplicated_chunk_count"] == 0
    assert service.receipt["final_chunk_count"] == 2
    assert service.receipt["chunk_sequence_hash"] == first_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["code", "table"])
async def test_code_and_table_blocks_obey_estimated_token_budget(kind: str) -> None:
    service = _ChunkingServiceStub(_token_chunker(chunk_size=80, chunk_overlap=12))
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    body = (
        "| column | value |\n|---|---|\n| 证据 | stable |\n" * 30
        if kind == "table"
        else "```python\nvalue = '确定性 evidence'\n```\n" * 30
    )
    parsed = [
        {
            "source_id": f"doc-{kind}",
            "processed_document": {
                "blocks": [
                    {
                        "block_id": f"block-{kind}",
                        "kind": kind,
                        "text": body,
                        "start_char": 0,
                        "end_char": len(body),
                        "heading_path": ["Structured evidence"],
                    }
                ]
            },
        }
    ]

    chunks = await executor._chunk_sources(f"job-{kind}", parsed)  # noqa: SLF001

    assert chunks
    assert all(estimate_text_tokens(chunk["index_text"]) <= 80 for chunk in chunks)
    assert all(estimate_text_tokens(chunk["context_text"]) <= 80 for chunk in chunks)


@pytest.mark.asyncio
async def test_parent_child_dedup_keeps_distinct_children_with_shared_context() -> None:
    service = _ChunkingServiceStub(
        _token_chunker(
            strategy="parent_child_estimated_token",
            parent_chunk_size=120,
            parent_chunk_overlap=12,
            child_chunk_size=36,
            child_chunk_overlap=6,
        )
    )
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    body = "First distinct evidence sentence. " * 8 + "Second distinct evidence sentence. " * 8
    parsed = [
        {
            "source_id": "doc-parent-child",
            "processed_document": {
                "blocks": [
                    {
                        "block_id": "block-parent-child",
                        "kind": "paragraph",
                        "text": body,
                        "start_char": 0,
                        "end_char": len(body),
                        "heading_path": ["Evidence"],
                    }
                ]
            },
        }
    ]

    chunks = await executor._chunk_sources("job-parent-child", parsed)  # noqa: SLF001

    first_parent = chunks[0]["parent_chunk_id"]
    siblings = [chunk for chunk in chunks if chunk["parent_chunk_id"] == first_parent]
    assert len(siblings) >= 2
    assert len({chunk["index_text"] for chunk in siblings}) == len(siblings)
    assert len({chunk["context_text"] for chunk in siblings}) == 1
    assert len({chunk["index_text"] for chunk in chunks}) == len(chunks)


@pytest.mark.asyncio
async def test_heading_prefix_newline_cannot_exhaust_overlap_budget(
    tmp_path: Path,
) -> None:
    chunker = _token_chunker(chunk_size=100, chunk_overlap=92)
    service = _ChunkingServiceStub(chunker)
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    heading = "a" * 28
    body = "bounded body evidence " * 40
    parsed = [
        {
            "source_id": "doc-tight-overlap",
            "processed_document": {
                "blocks": [
                    {
                        "block_id": "tight-block",
                        "kind": "paragraph",
                        "text": body,
                        "start_char": 0,
                        "end_char": len(body),
                        "heading_path": [heading],
                    }
                ]
            },
        }
    ]

    chunks = await executor._chunk_sources("job-tight-overlap", parsed)  # noqa: SLF001

    assert chunks
    assert all(estimate_text_tokens(chunk["index_text"]) <= 100 for chunk in chunks)
    preview_service = _service(tmp_path / "preview")
    preview, receipt = preview_service._preview_pipeline_chunks(  # noqa: SLF001
        {"document_id": "doc-tight-overlap", **parsed[0]["processed_document"]},
        chunker,
        kind="recursive_chunker",
    )
    assert preview
    assert receipt["final_chunk_count"] == len(preview)
    assert all(item["estimated_index_tokens"] <= 100 for item in preview)


@pytest.mark.asyncio
async def test_generated_items_cannot_bypass_token_budget(tmp_path: Path) -> None:
    service = _ChunkingServiceStub(_token_chunker())
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    source_text = "Answer first. " + ("long source context " * 80)
    parsed = [
        {
            "source_id": "doc-generated",
            "processed_document": {
                "blocks": [
                    {
                        "block_id": "source-block",
                        "kind": "paragraph",
                        "text": source_text,
                        "start_char": 10,
                        "end_char": 10 + len(source_text),
                        "heading_path": ["Generated"],
                    }
                ]
            },
            "generated_items": [
                {
                    "item_id": "qa-valid",
                    "item_type": "qa",
                    "index_text": "What is the stable answer?",
                    "context_text": source_text,
                    "source_block_ids": ["source-block"],
                    "context_source_ranges": [
                        {
                            "source_block_id": "source-block",
                            "context_start": 0,
                            "context_end": len(source_text),
                            "source_start": 10,
                            "source_end": 10 + len(source_text),
                        }
                    ],
                },
                {
                    "item_id": "qa-rejected",
                    "item_type": "qa",
                    "index_text": "超" * 120,
                    "context_text": "must not be indexed",
                    "source_block_ids": ["source-block"],
                },
            ],
        }
    ]

    chunks = await executor._chunk_sources("job-generated", parsed)  # noqa: SLF001
    preview_service = _service(tmp_path / "preview-generated-budget")
    preview, preview_receipt = preview_service._preview_pipeline_chunks(  # noqa: SLF001
        {
            "document_id": "doc-generated",
            **parsed[0]["processed_document"],
            "generated_items": parsed[0]["generated_items"],
        },
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert len(chunks) >= 2
    assert len(preview) == len(chunks)
    parent_ids = {chunk["parent_chunk_id"] for chunk in chunks}
    assert len(parent_ids) > 1
    assert all(str(parent_id).startswith("generated_v1_") for parent_id in parent_ids)
    assert len(
        {
            (str(chunk["parent_chunk_id"]), str(chunk["context_text"]))
            for chunk in chunks
        }
    ) == len(parent_ids)
    assert all(chunk["source_block_id"] == "source-block" for chunk in chunks)
    assert all(estimate_text_tokens(chunk["index_text"]) <= 100 for chunk in chunks)
    assert all(estimate_text_tokens(chunk["context_text"]) <= 100 for chunk in chunks)
    assert all("What is the stable answer?" in chunk["index_text"] for chunk in chunks)
    assert [chunk["index_text"] for chunk in chunks] == [
        item["text_preview"] for item in preview
    ]
    assert service.receipt["generated_item_rejected_count"] == 1
    assert service.receipt["generated_item_chunk_count"] == len(chunks)
    assert preview_receipt["generated_item_rejected_count"] == 1
    assert preview_receipt["generated_item_chunk_count"] == len(preview)


@pytest.mark.asyncio
async def test_generated_item_records_short_deep_heading_source_truncation(
    tmp_path: Path,
) -> None:
    heading_path = [f"H{index}" for index in range(13)]
    source_text = "Grounded generated evidence. " * 4
    processed = StructuredDocumentProcessor().process(
        tmp_path / "generated-deep-heading.png",
        filename="generated-deep-heading.png",
        source_id="doc-generated-deep-heading",
        extra_blocks=[
            {
                "block_id": "source-block",
                "kind": "paragraph",
                "text": source_text,
                "start_char": 0,
                "end_char": len(source_text),
                "heading_path": heading_path,
            }
        ],
    ).payload(include_text=True, max_block_text=None)
    processed["document_id"] = "doc-generated-deep-heading"
    processed["generated_items"] = [
            {
                "item_id": "qa-deep-heading",
                "item_type": "qa",
                "index_text": "What evidence is grounded?",
                "context_text": source_text,
                "source_block_ids": ["source-block"],
            }
        ]
    assert processed["blocks"][0]["heading_path_source_truncated"] is True
    assert len(processed["blocks"][0]["heading_path_source_hash"]) == 64
    chunker = _token_chunker()
    service = _ChunkingServiceStub(chunker)
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]

    chunks = await executor._chunk_sources(  # noqa: SLF001
        "job-generated-deep-heading",
        [
            {
                "source_id": processed["document_id"],
                "processed_document": processed,
                "generated_items": processed["generated_items"],
            }
        ],
    )
    preview_service = _service(tmp_path / "preview-generated-deep-heading")
    preview, preview_receipt = preview_service._preview_pipeline_chunks(  # noqa: SLF001
        processed,
        chunker,
        kind="recursive_chunker",
    )

    assert chunks and preview
    assert service.receipt["heading_prefix_truncated_count"] == 1
    assert preview_receipt["heading_prefix_truncated_count"] == 1
    assert [chunk["index_text"] for chunk in chunks] == [
        item["text_preview"] for item in preview
    ]
    assert all(chunk["heading_path"][0] == "H0" for chunk in chunks)
    assert all(chunk["heading_path"][-1] == "H12" for chunk in chunks)
    assert all(chunk["index_text"].splitlines()[0] == "H0 > H12" for chunk in chunks)


@pytest.mark.asyncio
async def test_multi_block_generated_identity_preserves_lineage_without_false_heading(
    tmp_path: Path,
) -> None:
    service = _ChunkingServiceStub(_token_chunker())
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    multilingual_context = "証拠かな가나다" * 30
    split_at = len(multilingual_context) // 2
    block_b_text = multilingual_context[:split_at]
    block_a_text = multilingual_context[split_at:]
    block_a_start = 1000
    block_b_start = 2000
    shared_tail = [f"H{index}" for index in range(2, 13)]
    processor = StructuredDocumentProcessor()
    source_blocks = [
        processor._block(  # noqa: SLF001 - preserve lineage across extra-block transport.
            "doc-multi",
            "paragraph",
            block_a_text,
            block_a_start,
            block_a_start + len(block_a_text),
            heading_path=["Root", "A-only ancestor", *shared_tail],
        ),
        processor._block(  # noqa: SLF001 - preserve lineage across extra-block transport.
            "doc-multi",
            "paragraph",
            block_b_text,
            block_b_start,
            block_b_start + len(block_b_text),
            heading_path=["Root", "B-only ancestor", *shared_tail],
        ),
    ]
    source_blocks[0].block_id = "block-a"
    source_blocks[1].block_id = "block-b"
    processed = processor.process(
        tmp_path / "multi-source-heading.png",
        filename="multi-source-heading.png",
        source_id="doc-multi",
        extra_blocks=source_blocks,
    ).payload(include_text=True, max_block_text=None)
    processed["document_id"] = "doc-multi"
    processed_blocks = {
        block["block_id"]: block for block in processed["blocks"]
    }
    block_a_start = processed_blocks["block-a"]["start_char"]
    block_b_start = processed_blocks["block-b"]["start_char"]
    processed["generated_items"] = [
            {
                "item_id": "summary-1",
                "item_type": "summary",
                "index_text": "Combined evidence",
                "context_text": multilingual_context,
                "source_block_ids": ["block-b", "block-a"],
                "context_source_ranges": [
                    {
                        "source_block_id": "block-b",
                        "context_start": 0,
                        "context_end": len(block_b_text),
                        "source_start": block_b_start,
                        "source_end": block_b_start + len(block_b_text),
                    },
                    {
                        "source_block_id": "block-a",
                        "context_start": len(block_b_text),
                        "context_end": len(multilingual_context),
                        "source_start": block_a_start,
                        "source_end": block_a_start + len(block_a_text),
                    },
                ],
            }
        ]
    parsed = [
        {
            "source_id": "doc-multi",
            "processed_document": processed,
            "generated_items": processed["generated_items"],
        }
    ]

    chunks = await executor._chunk_sources("job-multi", parsed)  # noqa: SLF001
    preview_service = _service(tmp_path / "preview-multi")
    preview, preview_receipt = preview_service._preview_pipeline_chunks(  # noqa: SLF001
        processed,
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert chunks and preview
    assert len(chunks) == len(preview)
    assert [chunk["parent_chunk_id"] for chunk in chunks] == [
        item["parent_chunk_id"] for item in preview
    ]
    assert [estimate_text_tokens(chunk["context_text"]) for chunk in chunks] == [
        item["estimated_context_tokens"] for item in preview
    ]
    assert all(item["estimated_context_tokens"] <= 100 for item in preview)
    assert service.receipt["generated_item_chunk_count"] == len(chunks)
    assert preview_receipt["generated_item_chunk_count"] == len(preview)
    assert {
        block_id
        for chunk in chunks
        for block_id in chunk["source_block_ids"]
    } == {"block-a", "block-b"}
    assert [chunk["source_block_ids"] for chunk in chunks] == [
        item["source_block_ids"] for item in preview
    ]
    assert chunks[0]["heading_path"] == ()
    assert chunks[0]["index_text"].splitlines()[0] == "Combined evidence"
    assert preview[0]["text_preview"].splitlines()[0] == "Combined evidence"
    assert service.receipt["heading_prefix_truncated_count"] == 1
    assert preview_receipt["heading_prefix_truncated_count"] == 1
    assert [chunk["index_text"] for chunk in chunks] == [
        item["text_preview"] for item in preview
    ]
    assert len({chunk["index_text"] for chunk in chunks}) == len(chunks)


def test_processor_generated_refs_are_limited_to_the_actual_non_heading_batch() -> None:
    generator = ProcessorGenerationService()
    allowed = [
        {"block_id": "block-a", "kind": "paragraph", "text": "A" * 8_000},
        {"block_id": "block-b", "kind": "paragraph", "text": "B" * 8_000},
        {"block_id": "heading", "kind": "heading", "text": "Title"},
    ]

    with pytest.raises(ValueError, match="no valid qa items"):
        generator._parse_items(  # noqa: SLF001
            {"items": [{"question": "q", "answer": "a", "block_ids": ["other"]}]},
            allowed_blocks=allowed,
            mode="qa",
            max_items=1,
        )
    with pytest.raises(ValueError, match="no valid qa items"):
        generator._parse_items(  # noqa: SLF001
            {"items": [{"question": "q", "answer": "a", "block_ids": ["heading"]}]},
            allowed_blocks=allowed,
            mode="qa",
            max_items=1,
        )
    bounded = generator._parse_items(  # noqa: SLF001
        {
            "items": [
                {
                    "question": "q",
                    "answer": "a",
                    "block_ids": ["block-a", "block-b"],
                }
            ]
        },
        allowed_blocks=allowed,
        mode="qa",
        max_items=1,
    )

    assert len(bounded) == 1
    assert bounded[0].source_block_ids == ["block-a", "block-b"]
    assert "A" * 8_000 in bounded[0].context_text
    assert bounded[0].context_text.count("B") == 3_998


def test_document_summary_preserves_string_protocol_and_derives_batch_lineage() -> None:
    generator = ProcessorGenerationService()
    allowed = [
        {"block_id": "block-a", "kind": "paragraph", "text": "Evidence A"},
        {"block_id": "block-b", "kind": "paragraph", "text": "Evidence B"},
    ]

    generated = generator._parse_items(  # noqa: SLF001
        {"document_summary": "Grounded batch summary", "sections": []},
        allowed_blocks=allowed,
        mode="summary",
        max_items=2,
    )

    assert len(generated) == 1
    assert generated[0].source_block_ids == ["block-a", "block-b"]
    assert [
        item.source_block_id for item in generated[0].context_source_ranges
    ] == ["block-a", "block-b"]
    with pytest.raises(ValueError, match="no valid summary items"):
        generator._parse_items(  # noqa: SLF001
            {
                "document_summary": {
                    "summary": "Unknown evidence",
                    "block_ids": ["other"],
                },
                "sections": [],
            },
            allowed_blocks=allowed,
            mode="summary",
            max_items=2,
        )


@pytest.mark.asyncio
async def test_generated_item_sequence_hash_covers_index_and_context_text() -> None:
    service = _ChunkingServiceStub(_token_chunker())
    executor = KnowledgePipelineExecutor(service)  # type: ignore[arg-type]
    parsed = [
        {
            "source_id": "doc-generated-hash",
            "processed_document": {
                "blocks": [
                    {
                        "block_id": "source-block",
                        "kind": "paragraph",
                        "text": "source evidence",
                        "start_char": 0,
                        "end_char": 15,
                    }
                ]
            },
            "generated_items": [
                {
                    "item_id": "qa-stable",
                    "item_type": "qa",
                    "index_text": "First grounded question?",
                    "context_text": "The stable answer and its source context.",
                    "source_block_ids": ["source-block"],
                }
            ],
        }
    ]

    await executor._chunk_sources("job-generated-hash", parsed)  # noqa: SLF001
    first_hash = service.receipt["chunk_sequence_hash"]
    await executor._chunk_sources("job-generated-hash", parsed)  # noqa: SLF001
    assert service.receipt["chunk_sequence_hash"] == first_hash

    parsed[0]["generated_items"][0]["index_text"] = "Second grounded question?"
    await executor._chunk_sources("job-generated-hash", parsed)  # noqa: SLF001

    assert service.receipt["chunk_sequence_hash"] != first_hash


def test_token_chunker_with_legacy_aggregate_contract_cannot_first_activate(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    kb = service.create_knowledge_base("aggregate activation")
    kb_id = str(kb["id"])
    profile = service._default_embedding_profile()  # noqa: SLF001
    version = {
        "version_id": "kpv_token_chunker_legacy_aggregate",
        "kb_id": kb_id,
        "version": 1,
        "status": "ready",
        "namespace": "token-chunker-legacy-aggregate",
        "draft_id": f"draft_{kb_id}",
        "draft_version": 1,
        "index_schema_version": 3,
        "embedding_profile": profile,
        "embedding_space_fingerprint": profile["embedding_space_fingerprint"],
        "retrieval_profile": {"mode": "fulltext", "top_k": 2},
        "vector_index_ready": False,
        "lexical_index_ready": True,
        "config_snapshot": {
            "stages": {"stage_chunker": _token_chunker()},
            "content_index_contract": {
                "contract_version": "rag-content-index-contract-v1",
                "chunker_contract_version": "rag-chunker-estimated-token-v1",
                "lexical_contract_version": "sqlite-fts5-lexical-v1",
                "parser_contract_version": "structured-local-parser-v1",
                "status": "legacy_read_only",
            },
        },
        "source_summary": [],
        "document_count": 1,
        "chunk_count": 1,
        "job_id": "aggregate-job",
        "created_at": 1.0,
        "activated_at": None,
    }
    with service._metadata_lock:  # noqa: SLF001 - activation contract fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_versions"][version["version_id"]] = version
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineContentContractError):
        service.activate_pipeline_version(version["version_id"])


def test_chunk_preview_applies_generated_item_budget_and_reports_rejections(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    chunks, receipt = service._preview_pipeline_chunks(  # noqa: SLF001
        {
            "document_id": "doc-preview-generated",
            "blocks": [
                {
                    "block_id": "source-block",
                    "kind": "paragraph",
                    "text": "source evidence",
                    "start_char": 10,
                    "end_char": 25,
                    "heading_path": ["Root", "Generated evidence"],
                }
            ],
            "generated_items": [
                {
                    "item_id": "valid",
                    "item_type": "qa",
                    "index_text": "What is the stable answer?",
                    "context_text": "Answer first. " + ("long source context " * 80),
                    "source_block_ids": ["source-block"],
                },
                {
                    "item_id": "rejected",
                    "item_type": "qa",
                    "index_text": "超" * 120,
                    "context_text": "must not be indexed",
                    "source_block_ids": ["source-block"],
                },
            ],
        },
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert len(chunks) >= 2
    assert all(item["parent_chunk_id"].startswith("generated_v1_") for item in chunks)
    assert all(item["estimated_index_tokens"] <= 100 for item in chunks)
    assert all(item["estimated_context_tokens"] <= 100 for item in chunks)
    assert all(item["text_preview"].splitlines()[0] == "Root > Generated evidence" for item in chunks)
    assert all(len(item["text_preview"]) <= 600 for item in chunks)
    assert all(len(item["context_preview"]) <= 600 for item in chunks)
    assert receipt["generated_item_count"] == 2
    assert receipt["generated_item_rejected_count"] == 1
    assert receipt["generated_item_rejection_reasons"] == {
        "index_text_over_budget": 1
    }
    assert receipt["generated_item_chunk_count"] == len(chunks)
    assert receipt["final_chunk_count"] == len(chunks)


def test_chunk_preview_matches_heading_and_scoped_dedup_contract(tmp_path: Path) -> None:
    service = _service(tmp_path)
    repeated = "Deterministic preview evidence body " * 18
    paragraph = {
        "block_id": "block-a",
        "kind": "paragraph",
        "text": repeated,
        "start_char": 7,
        "end_char": 7 + len(repeated),
        "heading_path": ["Root", "A very long leaf heading " * 20],
    }
    chunks, receipt = service._preview_pipeline_chunks(  # noqa: SLF001
        {
            "document_id": "doc-preview",
            "blocks": [
                {
                    "block_id": "heading-1",
                    "kind": "heading",
                    "text": "# Root",
                    "start_char": 0,
                    "end_char": 6,
                    "heading_path": ["Root"],
                },
                paragraph,
                dict(paragraph),
            ],
            "generated_items": [],
        },
        _token_chunker(),
        kind="recursive_chunker",
    )

    assert chunks
    assert all(item["estimated_index_tokens"] <= 100 for item in chunks)
    assert all(item["estimated_context_tokens"] <= 100 for item in chunks)
    assert all(item["text_preview"].splitlines()[0].startswith("Root") for item in chunks)
    assert all(item["context_preview"].splitlines()[0].startswith("Root") for item in chunks)
    assert receipt["heading_block_count"] == 1
    assert receipt["heading_prefix_truncated_count"] > 0
    assert receipt["deduplicated_chunk_count"] > 0
    assert receipt["final_chunk_count"] == len(chunks)
