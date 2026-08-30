from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server.rag.document_processor import (
    ProcessedDocument,
    StructuredDocumentProcessor,
)
from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.processor_generator import (
    GeneratedIndexItem,
    ProcessorGenerationService,
)
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore


class FakeGenerator:
    def default_model(self) -> str:
        return "test/processor-model"

    def capabilities(self) -> dict[str, Any]:
        return {
            "llm_configured": True,
            "model": self.default_model(),
            "targets": ["test"],
        }

    async def generate(
        self,
        document: ProcessedDocument,
        *,
        mode: str,
        model_id: str,
        max_items: int,
    ) -> list[GeneratedIndexItem]:
        if mode == "general":
            return []
        first = document.blocks[0]
        if mode == "qa":
            return [
                GeneratedIndexItem(
                    item_id="qa_0",
                    index_text="What is the Orion approval code?",
                    context_text=(
                        "Question: What is the Orion approval code?\n"
                        "Answer: ORION-42\n\nSource:\n" + first.text
                    ),
                    source_block_ids=[first.block_id],
                    item_type="qa",
                )
            ]
        return [
            GeneratedIndexItem(
                item_id="summary_0",
                index_text="Orion release approval summary",
                context_text="Summary: Orion uses ORION-42.\n\nSource:\n" + first.text,
                source_block_ids=[first.block_id],
                item_type="summary",
            )
        ]


class FailingProcessor:
    def __init__(self) -> None:
        self.delegate = StructuredDocumentProcessor()
        self.fail_bad = True
        self.calls: dict[str, int] = {}

    def process(self, path: Path, **kwargs: Any) -> ProcessedDocument:
        filename = str(kwargs["filename"])
        self.calls[filename] = self.calls.get(filename, 0) + 1
        if filename == "bad.txt" and self.fail_bad:
            raise RuntimeError("synthetic document processor failure")
        return self.delegate.process(path, **kwargs)


def make_service(
    tmp_path: Path,
    *,
    processor: Any | None = None,
    generator: Any | None = None,
) -> RagService:
    storage = tmp_path / "storage"
    return RagService(
        storage_dir=storage,
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(storage / "vectors.json"),
        document_processor=processor,
        processor_generator=generator or FakeGenerator(),
        llm_enabled=False,
    )


def test_structured_processor_preserves_markdown_structure(tmp_path: Path) -> None:
    source = tmp_path / "guide.md"
    source.write_text(
        "# Release Guide\n\nIntro text.\n\n- first\n- second\n\n"
        "| Key | Value |\n| --- | --- |\n| code | ORION-42 |\n\n"
        "```python\nprint('safe')\n```\n",
        encoding="utf-8",
    )
    result = StructuredDocumentProcessor().process(
        source,
        filename="guide.md",
        source_id="doc-guide",
    )

    assert result.title == "Release Guide"
    assert [block.kind for block in result.blocks] == [
        "heading",
        "paragraph",
        "list",
        "table",
        "code",
    ]
    assert result.blocks[-1].heading_path == ["Release Guide"]
    assert "ORION-42" in result.blocks[3].text
    assert "print('safe')" in result.blocks[4].text
    assert all(block.block_id.startswith("block_") for block in result.blocks)


def test_shared_csv_and_vtt_sections_keep_source_metadata(tmp_path: Path) -> None:
    csv_path = tmp_path / "records.csv"
    csv_path.write_text("name,value\nalpha,42\n", encoding="utf-8")
    csv_result = StructuredDocumentProcessor().process(
        csv_path,
        filename="records.csv",
        source_id="doc-csv",
    )

    assert csv_result.blocks[0].kind == "table"
    assert csv_result.blocks[0].metadata["row_range"] == "1-2"
    assert "alpha" in csv_result.blocks[0].text

    vtt_path = tmp_path / "captions.vtt"
    vtt_path.write_text(
        "WEBVTT\n\n00:00.000 --> 00:01.500\nHello\n",
        encoding="utf-8",
    )
    vtt_result = StructuredDocumentProcessor().process(
        vtt_path,
        filename="captions.vtt",
        source_id="doc-vtt",
    )

    assert vtt_result.blocks[0].kind == "subtitle"
    assert vtt_result.blocks[0].metadata["time_range"] == (
        "00:00.000 --> 00:01.500"
    )
    assert vtt_result.blocks[0].metadata["line_range"]


def test_shared_html_and_source_sections_keep_heading_and_line_metadata(
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "guide.html"
    html_path.write_text(
        "<h1>Guide</h1><p>Safe body</p><script>hidden()</script>",
        encoding="utf-8",
    )
    html_result = StructuredDocumentProcessor().process(
        html_path,
        filename="guide.html",
        source_id="doc-html",
    )

    assert html_result.blocks[0].kind == "heading"
    assert html_result.blocks[0].heading_path == ["Guide"]
    assert html_result.blocks[0].metadata["heading_path"] == ["Guide"]
    assert "hidden" not in html_result.text

    source_path = tmp_path / "app.py"
    source_text = "  def greet():\n      return 'hello'\n"
    source_path.write_bytes(source_text.encode("utf-8"))
    source_result = StructuredDocumentProcessor().process(
        source_path,
        filename="app.py",
        source_id="doc-source-layout",
    )

    assert source_result.text == source_text
    assert source_result.blocks[0].kind == "source"
    assert source_result.blocks[0].metadata["line_range"] == "1-2"


def test_pdf_processor_removes_repeated_page_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"placeholder")
    processor = StructuredDocumentProcessor()
    monkeypatch.setattr(
        processor,
        "_pdf_pages",
        lambda _: [
            "Company Manual\nFirst page body\nConfidential",
            "Company Manual\nSecond page body\nConfidential",
            "Company Manual\nThird page body\nConfidential",
        ],
    )

    result = processor.process(
        source,
        filename="manual.pdf",
        source_id="doc-pdf",
    )

    assert [block.page_number for block in result.blocks] == [1, 2, 3]
    assert "Company Manual" not in result.text
    assert "Confidential" not in result.text
    assert "Second page body" in result.text
    assert result.warnings


@pytest.mark.asyncio
async def test_generation_service_retries_invalid_json_and_builds_qa_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("The approval code is ORION-42.", encoding="utf-8")
    document = StructuredDocumentProcessor().process(
        source,
        filename="source.txt",
        source_id="doc-source",
    )
    generator = ProcessorGenerationService()
    monkeypatch.setattr(
        generator,
        "_targets",
        lambda: [("test", "https://example.invalid/chat", "secret")],
    )
    calls = 0

    async def fake_post(*_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("invalid JSON")
        return {
            "items": [
                {
                    "question": "What is the approval code?",
                    "answer": "ORION-42",
                    "block_ids": [document.blocks[0].block_id],
                }
            ]
        }

    monkeypatch.setattr(generator, "_post", fake_post)
    items = await generator.generate(
        document,
        mode="qa",
        model_id="test/model",
        max_items=20,
    )

    assert calls == 2
    assert items[0].index_text == "What is the approval code?"
    assert "Answer: ORION-42" in items[0].context_text
    assert "The approval code" in items[0].context_text


@pytest.mark.asyncio
async def test_summary_generation_preserves_provider_payload_and_derives_lineage_locally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "summary.md"
    source.write_text("# Stable heading\n\nGrounded body evidence.", encoding="utf-8")
    document = StructuredDocumentProcessor().process(
        source,
        filename="summary.md",
        source_id="doc-summary",
    )
    generator = ProcessorGenerationService()
    observed_blocks: list[dict[str, Any]] = []

    async def fake_generate_batch(**kwargs: Any) -> dict[str, Any]:
        observed_blocks.extend(kwargs["blocks"])
        return {"document_summary": "Stable summary", "sections": []}

    monkeypatch.setattr(generator, "_generate_batch", fake_generate_batch)
    items = await generator.generate(
        document,
        mode="summary",
        model_id="test/model",
        max_items=2,
    )

    assert any(block["kind"] == "heading" for block in observed_blocks)
    assert all("start_char" not in block for block in observed_blocks)
    assert all("end_char" not in block for block in observed_blocks)
    assert items[0].source_block_ids
    assert all(
        block_id
        not in {
            block.block_id for block in document.blocks if block.kind == "heading"
        }
        for block_id in items[0].source_block_ids
    )
    assert items[0].context_source_ranges


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["qa", "summary"])
async def test_generated_processor_modes_index_prompt_and_return_source_context(
    tmp_path: Path,
    mode: str,
) -> None:
    service = make_service(tmp_path, generator=FakeGenerator())
    kb = service.create_knowledge_base(f"{mode} index")
    document = await service.upload_document(
        kb["id"],
        "orion.txt",
        b"The Orion release requires approval code ORION-42.",
    )
    draft = service.update_pipeline_draft(
        kb["id"],
        {
            "stage_processor": {
                "mode": mode,
                "model_id": "test/processor-model",
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    executor = KnowledgePipelineExecutor(service)

    assert await executor.run_once() is True
    completed = service.pipeline_job_payload(service.get_pipeline_job(job["job_id"]))
    assert completed["status"] == "succeeded"
    version = service.list_pipeline_versions(kb["id"])[0]
    assert version[f"{mode}_count"] == 1
    query = (
        "What is the Orion approval code?"
        if mode == "qa"
        else "Orion release approval summary"
    )
    result = await service.query_pipeline_version(
        version["version_id"],
        query,
        top_k=3,
        retrieval={"mode": "vector"},
    )

    assert result["sources"]
    assert "ORION-42" in result["sources"][0]["text"]
    assert result["sources"][0]["chunk_type"] == mode
    assert result["sources"][0]["parent_lifted"] is True


@pytest.mark.asyncio
async def test_strict_retry_reuses_completed_documents_and_reruns_failures(
    tmp_path: Path,
) -> None:
    processor = FailingProcessor()
    service = make_service(tmp_path, processor=processor)
    kb = service.create_knowledge_base("recovery")
    good = await service.upload_document(kb["id"], "good.txt", b"Good source text.")
    bad = await service.upload_document(kb["id"], "bad.txt", b"Bad source text.")
    draft = service.update_pipeline_draft(
        kb["id"],
        {"stage_processor": {"failure_policy": "strict"}},
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[good["id"], bad["id"]],
    )
    executor = KnowledgePipelineExecutor(service)

    assert await executor.run_once() is True
    failed = service.pipeline_job_payload(service.get_pipeline_job(job["job_id"]))
    assert failed["status"] == "failed"
    assert {item["status"] for item in failed["document_results"]} == {
        "completed",
        "failed",
    }
    assert processor.calls == {"good.txt": 1, "bad.txt": 1}

    processor.fail_bad = False
    service.retry_pipeline_job(job["job_id"])
    assert await executor.run_once() is True
    completed = service.pipeline_job_payload(service.get_pipeline_job(job["job_id"]))

    assert completed["status"] == "succeeded"
    assert {item["status"] for item in completed["document_results"]} == {"completed"}
    assert processor.calls == {"good.txt": 1, "bad.txt": 2}
    assert len(service.list_pipeline_versions(kb["id"])) == 1


@pytest.mark.asyncio
async def test_continue_on_error_builds_candidate_from_successful_documents(
    tmp_path: Path,
) -> None:
    processor = FailingProcessor()
    service = make_service(tmp_path, processor=processor)
    kb = service.create_knowledge_base("partial success")
    good = await service.upload_document(kb["id"], "good.txt", b"Good source text.")
    bad = await service.upload_document(kb["id"], "bad.txt", b"Bad source text.")
    draft = service.update_pipeline_draft(
        kb["id"],
        {},
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb["id"],
        draft_version=draft["version"],
        source_document_ids=[good["id"], bad["id"]],
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True
    completed = service.pipeline_job_payload(service.get_pipeline_job(job["job_id"]))
    assert completed["status"] == "succeeded"
    assert completed["warnings"] == [
        "1 document(s) failed and were excluded from this candidate."
    ]
    version = service.list_pipeline_versions(kb["id"])[0]
    assert version["document_count"] == 1
    assert version["warnings"] == completed["warnings"]
    assert {item["status"] for item in version["document_results"]} == {
        "completed",
        "failed",
    }
