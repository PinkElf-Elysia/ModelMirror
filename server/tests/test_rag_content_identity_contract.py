from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.processor_generator import GeneratedIndexItem, GeneratedSourceRange
from server.rag.rag_service import PipelineJobStateError, RagService
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


class _SingleBlockGenerator:
    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {
            "llm_configured": True,
            "model": "strict-fake-generator",
            "targets": ["strict_fake"],
        }

    @staticmethod
    def default_model() -> str:
        return "strict-fake-generator"

    @staticmethod
    async def generate(document, **_kwargs) -> list[GeneratedIndexItem]:
        blocks = [block for block in document.blocks if block.kind != "heading"]
        assert len(blocks) == 1
        return [
            GeneratedIndexItem(
                item_id="qa_0",
                item_type="qa",
                index_text="Which evidence is canonical?",
                context_text=blocks[0].text,
                source_block_ids=[blocks[0].block_id],
                context_source_ranges=[
                    GeneratedSourceRange(
                        source_block_id=blocks[0].block_id,
                        context_start=0,
                        context_end=len(blocks[0].text),
                        source_start=blocks[0].start_char,
                        source_end=blocks[0].end_char,
                    )
                ],
            )
        ]


async def _execute_vector_version(
    service: RagService,
    *,
    name: str,
    filename: str,
    content: str,
    generated: bool = False,
) -> tuple[dict[str, Any], str, str]:
    kb = service.create_knowledge_base(name)
    document = await service.upload_document(
        str(kb["id"]),
        filename,
        content.encode("utf-8"),
        pipeline_only=True,
    )
    assert service.vector_store.count_namespace(str(kb["id"])) == 0
    assert service.lexical_store.count_namespace(str(kb["id"])) == 0
    stage_updates: dict[str, Any] = {}
    if generated:
        service.processor_generator = (  # type: ignore[assignment]
            _SingleBlockGenerator()
        )
        stage_updates["stage_processor"] = {
            "config": {
                "mode": "qa",
                "model_id": "strict-fake-generator",
            }
        }
    draft = service.update_pipeline_draft(
        str(kb["id"]),
        stage_updates,
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        str(kb["id"]),
        draft_version=int(draft["version"]),
        source_document_ids=[str(document["id"])],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.get_pipeline_job(str(created["job_id"]))
    assert completed["status"] == "succeeded", completed
    return completed, str(created["candidate_version_id"]), str(document["id"])


def _artifact(service: RagService, job: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    artifact_key = str(job["document_results"][0]["artifact_key"])
    path = service._pipeline_processed_path(artifact_key)  # noqa: SLF001
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_heading_is_canonical_but_not_index_or_gold_evidence_across_chunkers(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    job, version_id, document_id = await _execute_vector_version(
        service,
        name="heading identity",
        filename="heading.md",
        content="# Stable heading\n\nCanonical body evidence remains independently indexed.",
    )
    _, artifact = _artifact(service, job)
    blocks = artifact["processed_document"]["blocks"]
    heading_ids = {
        str(block["block_id"])
        for block in blocks
        if str(block.get("kind") or "") == "heading"
    }
    body_ids = {
        str(block["block_id"])
        for block in blocks
        if str(block.get("kind") or "") != "heading"
    }
    assert heading_ids and body_ids

    current_snapshot = service.pipeline_corpus_snapshot(version_id)
    current_evidence = service.pipeline_corpus_evidence(version_id)
    snapshot_ids = {
        str(block["source_block_id"])
        for block in current_snapshot["documents"][0]["source_blocks"]
    }
    evidence_ids = {
        str(block["source_block_id"])
        for block in current_evidence["documents"][0]["source_blocks"]
    }
    indexed = service.vector_store.list_document_chunks(
        f"{version_id}_{document_id}"
    )

    assert snapshot_ids == heading_ids | body_ids
    assert evidence_ids == body_ids
    assert all(
        str(chunk.source_block_id or "") not in heading_ids for chunk in indexed
    )

    # Rebuild the same immutable source through a second valid token budget.
    # Corpus identity is source-block based and must not absorb chunk parameters.
    reconfigured = service.update_pipeline_draft(
        str(job["kb_id"]),
        {
            "stage_chunker": {
                "config": {
                    "chunk_size": 120,
                    "chunk_overlap": 16,
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    rebuilt = service.create_pipeline_job(
        str(job["kb_id"]),
        draft_version=int(reconfigured["version"]),
        source_document_ids=[document_id],
    )
    assert await KnowledgePipelineExecutor(service).run_once() is True
    rebuilt_version_id = str(rebuilt["candidate_version_id"])
    assert service.pipeline_corpus_snapshot(rebuilt_version_id) == current_snapshot
    rebuilt_evidence = service.pipeline_corpus_evidence(rebuilt_version_id)
    assert {
        str(block["source_block_id"])
        for block in rebuilt_evidence["documents"][0]["source_blocks"]
    } == body_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "item_id",
        "index_text",
        "context_text",
        "source_refs",
        "source_range",
        "deleted",
    ],
)
async def test_generated_item_artifact_tampering_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    service = _service(tmp_path)
    job, version_id, _ = await _execute_vector_version(
        service,
        name=f"generated identity {mutation}",
        filename="evidence.txt",
        content="Canonical generated-item evidence.",
        generated=True,
    )
    assert service.pipeline_corpus_snapshot(version_id)
    assert service.pipeline_corpus_evidence(version_id)
    path, artifact = _artifact(service, job)
    item = artifact["generated_items"][0]
    if mutation == "item_id":
        item["item_id"] = "qa_changed"
    elif mutation == "index_text":
        item["index_text"] = "Which evidence was changed?"
    elif mutation == "context_text":
        item["context_text"] = "Tampered generated context."
    elif mutation == "source_refs":
        item["source_block_ids"] = ["missing-source-block"]
    elif mutation == "source_range":
        item["context_source_ranges"][0]["source_end"] -= 1
    else:
        artifact["generated_items"] = []
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(
        PipelineJobStateError,
        match="processed artifact|generated-item provenance|stored vector chunks",
    ):
        service.pipeline_corpus_snapshot(version_id)
    with pytest.raises(
        PipelineJobStateError,
        match="processed artifact|generated-item provenance",
    ):
        service.pipeline_corpus_evidence(version_id)


@pytest.mark.asyncio
async def test_forged_generated_child_parent_identity_fails_closed(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    _, version_id, document_id = await _execute_vector_version(
        service,
        name="forged generated child",
        filename="evidence.txt",
        content="Canonical generated-item evidence.",
        generated=True,
    )
    assert service.pipeline_corpus_snapshot(version_id)
    assert service.pipeline_corpus_evidence(version_id)

    records = service.vector_store._read_records()  # noqa: SLF001 - tamper fixture.
    document_key = f"{version_id}_{document_id}"
    generated_records = [
        record
        for record in records
        if record.get("doc_id") == document_key
        and str(record.get("parent_chunk_id") or "").startswith("generated_v1_")
    ]
    assert generated_records
    generated_records[0]["parent_chunk_id"] = f"generated_v1_{'0' * 64}"
    service.vector_store._write_records(records)  # noqa: SLF001 - tamper fixture.

    with pytest.raises(
        PipelineJobStateError,
        match="generated-item provenance|stored vector chunks",
    ):
        service.pipeline_corpus_snapshot(version_id)
    with pytest.raises(
        PipelineJobStateError,
        match="generated-item provenance|stored vector chunks",
    ):
        service.pipeline_corpus_evidence(version_id)


@pytest.mark.asyncio
async def test_processed_artifact_metadata_tamper_fails_before_corpus_projection(
    tmp_path: Path,
) -> None:
    """Canonical metadata is covered by the admitted processed artifact hash."""

    service = _service(tmp_path)
    job, version_id, _ = await _execute_vector_version(
        service,
        name="processed metadata identity",
        filename="evidence.md",
        content="# Evidence\n\nCanonical evidence keeps its admitted metadata.",
    )
    assert service.pipeline_corpus_snapshot(version_id)
    assert service.pipeline_corpus_evidence(version_id)
    path, artifact = _artifact(service, job)
    body = next(
        block
        for block in artifact["processed_document"]["blocks"]
        if block.get("kind") != "heading"
    )
    original_text = body["text"]
    body["heading_path"] = ["Forged heading"]
    body["page_number"] = 999
    path.write_text(json.dumps(artifact), encoding="utf-8")
    assert body["text"] == original_text

    with pytest.raises(PipelineJobStateError, match="processed artifact"):
        service.pipeline_corpus_snapshot(version_id)
    with pytest.raises(PipelineJobStateError, match="processed artifact"):
        service.pipeline_corpus_evidence(version_id)
