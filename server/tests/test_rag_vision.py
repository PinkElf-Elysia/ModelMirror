from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from PIL import Image

from server.main import app
from server.rag.api import (
    set_pipeline_executor_for_tests,
    set_rag_service_for_tests,
)
from server.rag.embedder import EmbeddingClient
from server.rag.document_processor import DocumentBlock, ProcessedDocument
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import (
    PipelineJobStateError,
    RagService,
)
from server.rag.vector_store import LocalJsonVectorStore
from server.rag.vision_processor import (
    MAX_IMAGE_PIXELS,
    VisionProcessingError,
    VisionUnderstandingService,
    _parse_response,
)


def _png_bytes(*, width: int = 80, height: int = 40) -> bytes:
    image = Image.new("RGB", (width, height), color=(245, 245, 245))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _scanned_pdf_bytes(*, pages: int = 2) -> bytes:
    images = [
        Image.new("RGB", (180, 120), color=(245 - index * 20, 245, 245))
        for index in range(pages)
    ]
    output = io.BytesIO()
    images[0].save(
        output,
        format="PDF",
        save_all=True,
        append_images=images[1:],
        resolution=144,
    )
    return output.getvalue()


def _vlm_response(
    *,
    ocr: str = "Quarterly revenue 2026",
    summary: str = "A rising revenue bar chart.",
) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "ocr_text": ocr,
                            "visual_summary": summary,
                            "tables": ["Revenue: Q1 10, Q2 18"],
                            "charts": ["Revenue increases from Q1 to Q2"],
                            "language": "en",
                            "warnings": [],
                        }
                    )
                }
            }
        ]
    }


def test_image_validation_rejects_corruption_extension_spoof_and_pixel_bomb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = VisionUnderstandingService()
    content = _png_bytes()
    details = service.validate_image_bytes(content, "chart.png")
    assert details["format"] == "png"
    assert details["pixel_count"] == 3200

    with pytest.raises(VisionProcessingError, match="does not match"):
        service.validate_image_bytes(content, "chart.jpg")
    with pytest.raises(VisionProcessingError, match="invalid or corrupted"):
        service.validate_image_bytes(b"not-an-image", "chart.png")

    class OversizedImage:
        format = "PNG"
        size = (MAX_IMAGE_PIXELS + 1, 1)

        @property
        def width(self) -> int:
            return self.size[0]

        @property
        def height(self) -> int:
            return self.size[1]

        def verify(self) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: OversizedImage())
    with pytest.raises(VisionProcessingError, match="pixel safety limit"):
        service.validate_image_bytes(content, "oversized.png")


def test_visual_model_response_contract_is_strict() -> None:
    parsed = _parse_response(_vlm_response())
    assert parsed["ocr_text"] == "Quarterly revenue 2026"
    with pytest.raises(VisionProcessingError, match="invalid JSON"):
        _parse_response({"choices": [{"message": {"content": "not-json"}}]})
    with pytest.raises(VisionProcessingError, match="tables must be a list"):
        _parse_response(
            {
                "choices": [
                    {"message": {"content": '{"ocr_text":"x","tables":"bad"}'}}
                ]
            }
        )


@pytest.mark.asyncio
async def test_image_analysis_builds_visual_blocks_deduplicates_ocr_and_reuses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls: list[dict] = []

    def request_override(url: str, key: str, payload: dict) -> dict:
        calls.append(payload)
        return _vlm_response()

    service = VisionUnderstandingService(request_override=request_override)
    path = tmp_path / "chart.png"
    path.write_bytes(_png_bytes())
    cache: dict[int, dict] = {}
    config = {
        "vision_model_id": "openai/gpt-4.1-mini",
        "pdf_page_strategy": "auto",
        "render_dpi": 144,
        "max_pages": 100,
        "max_image_edge": 2048,
        "failure_policy": "continue_on_error",
    }

    first = await service.analyze_source(
        path,
        filename="chart.png",
        source_id="doc_chart",
        config=config,
        cache_get=cache.get,
        cache_set=cache.__setitem__,
    )
    second = await service.analyze_source(
        path,
        filename="chart.png",
        source_id="doc_chart",
        config=config,
        cache_get=cache.get,
        cache_set=cache.__setitem__,
    )

    assert len(calls) == 1
    assert second.page_results[0].cached is True
    assert {block.kind for block in first.blocks} == {
        "image_ocr",
        "image_description",
        "visual_table",
        "visual_chart",
    }
    assert all(block.page_number == 1 for block in first.blocks)
    assert all(block.metadata["vision_model_id"] == config["vision_model_id"] for block in first.blocks)

    blocks, warning = service._blocks_from_analysis(
        json.loads(_vlm_response()["choices"][0]["message"]["content"]),
        source_id="pdf_doc",
        page_number=2,
        local_text="Quarterly revenue 2026",
        model_id=config["vision_model_id"],
    )
    assert "image_ocr" not in {block.kind for block in blocks}
    assert warning and "Duplicate OCR" in warning


@pytest.mark.asyncio
async def test_scanned_pdf_auto_selects_and_renders_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    service = VisionUnderstandingService(request_override=lambda *_: _vlm_response())
    path = tmp_path / "scan.pdf"
    path.write_bytes(_scanned_pdf_bytes())
    config = {
        "vision_model_id": "openai/gpt-4.1-mini",
        "pdf_page_strategy": "auto",
        "render_dpi": 144,
        "max_pages": 100,
        "max_image_edge": 1024,
        "failure_policy": "continue_on_error",
    }

    result = await service.analyze_source(
        path,
        filename="scan.pdf",
        source_id="doc_scan",
        config=config,
    )

    assert result.page_count == 2
    assert result.selected_page_count == 2
    assert result.processed_page_count == 2
    assert {item.reason for item in result.page_results} == {"sparse_text"}
    assert {block.page_number for block in result.blocks} == {1, 2}


@pytest.mark.asyncio
async def test_completed_vision_artifact_tamper_cannot_be_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed job must not trust mutable visual evidence by path alone."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = 0

    def request_override(*_args) -> dict:
        nonlocal calls
        calls += 1
        return _vlm_response()

    vision = VisionUnderstandingService(request_override=request_override)
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=vision,
    )
    kb_id = service.create_knowledge_base("vision-artifact-tamper")["id"]
    document = await service.upload_document(kb_id, "chart.png", _png_bytes())
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    claimed = service.claim_next_pipeline_job()
    assert claimed is not None and claimed["job_id"] == created["job_id"]
    first = await service.process_pipeline_job_vision(created["job_id"])
    assert first and first[0]["reused"] is False
    assert calls == 1

    stored = service.get_pipeline_job(created["job_id"])
    result = stored["document_results"][0]
    assert result.get("vision_artifact_hash")
    artifact_path = service._pipeline_vision_path(  # noqa: SLF001
        result["vision_artifact_key"]
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["page_count"] = 999
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(PipelineJobStateError, match="vision artifact"):
        await service.process_pipeline_job_vision(created["job_id"])
    with pytest.raises(PipelineJobStateError, match="vision artifact"):
        await service.process_pipeline_job_sources(created["job_id"])
    assert service.processor_gate_error(created["job_id"]) is None
    assert calls == 1


@pytest.mark.asyncio
async def test_vision_source_change_during_provider_call_fails_before_artifact_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider output cannot bind after its admitted source changes in flight."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider_calls = 0

    def request_override(*_args) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return _vlm_response()

    vision = VisionUnderstandingService(request_override=request_override)
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=vision,
    )
    kb_id = service.create_knowledge_base("vision-source-change-in-flight")["id"]
    document = await service.upload_document(kb_id, "chart.png", _png_bytes())
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    claimed = service.claim_next_pipeline_job()
    assert claimed is not None and claimed["job_id"] == created["job_id"]
    stored = service.get_pipeline_job(created["job_id"])
    source = stored["sources"][0]
    source_path = service.storage_dir / source["snapshot_key"]
    artifact_path = service._pipeline_vision_path(  # noqa: SLF001
        stored["document_results"][0]["vision_artifact_key"]
    )
    original_source_bytes = source_path.read_bytes()
    original_analyze = service.vision_processor.analyze_source
    mutate_once = True

    async def mutate_source_before_provider_returns(*args, **kwargs):
        nonlocal mutate_once
        result = await original_analyze(*args, **kwargs)
        if mutate_once:
            mutate_once = False
            source_path.write_bytes(_png_bytes(width=81, height=41))
        return result

    monkeypatch.setattr(
        service.vision_processor,
        "analyze_source",
        mutate_source_before_provider_returns,
    )

    with pytest.raises(PipelineJobStateError, match="source|hash|content"):
        await service.process_pipeline_job_vision(created["job_id"])

    assert provider_calls == 1
    assert not artifact_path.exists()
    page_dir = artifact_path.parent / f"{artifact_path.stem}_pages"
    assert list(page_dir.glob("page_*.json")) == []
    failed_binding = service.get_pipeline_job(created["job_id"])[
        "document_results"
    ][0]
    assert failed_binding.get("vision_artifact_hash") in {None, ""}

    source_path.write_bytes(original_source_bytes)
    retried = await service.process_pipeline_job_vision(created["job_id"])

    assert retried and retried[0]["reused"] is False
    assert provider_calls == 2
    assert artifact_path.is_file()
    assert len(list(page_dir.glob("page_*.json"))) == 1


@pytest.mark.asyncio
async def test_vision_result_config_hash_tamper_cannot_repeat_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted result mismatch must fail before a second vision request."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = 0

    def request_override(*_args) -> dict:
        nonlocal calls
        calls += 1
        return _vlm_response()

    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=VisionUnderstandingService(
            request_override=request_override
        ),
    )
    kb_id = service.create_knowledge_base("vision-result-config-tamper")["id"]
    document = await service.upload_document(kb_id, "chart.png", _png_bytes())
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert service.claim_next_pipeline_job() is not None
    first = await service.process_pipeline_job_vision(created["job_id"])
    assert first and first[0]["reused"] is False
    assert calls == 1

    with service._metadata_lock:  # noqa: SLF001 - persisted lineage tamper.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_jobs"][created["job_id"]]["document_results"][0][
            "vision_config_hash"
        ] = "0" * 64
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineJobStateError, match="result configuration"):
        await service.process_pipeline_job_vision(created["job_id"])
    assert calls == 1


@pytest.mark.asyncio
async def test_partial_vision_page_cache_tamper_cannot_be_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durable per-page evidence must be sealed before a resumed provider run."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = 0

    def request_override(*_args) -> dict:
        nonlocal calls
        calls += 1
        return _vlm_response()

    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=VisionUnderstandingService(
            request_override=request_override
        ),
    )
    kb_id = service.create_knowledge_base("vision-page-cache-tamper")["id"]
    document = await service.upload_document(kb_id, "chart.png", _png_bytes())
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert service.claim_next_pipeline_job() is not None
    await service.process_pipeline_job_vision(created["job_id"])
    assert calls == 1

    stored = service.get_pipeline_job(created["job_id"])
    result = stored["document_results"][0]
    artifact_path = service._pipeline_vision_path(  # noqa: SLF001
        result["vision_artifact_key"]
    )
    cache_path = artifact_path.parent / f"{artifact_path.stem}_pages" / "page_1.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["result"]["blocks"][0]["text"] = "tampered"
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    with service._metadata_lock:  # noqa: SLF001 - partial recovery fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_jobs"][created["job_id"]]["document_results"][0][
            "vision_status"
        ] = "processing"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineJobStateError, match="page cache"):
        await service.process_pipeline_job_vision(created["job_id"])
    assert calls == 1


@pytest.mark.asyncio
async def test_vision_page_cache_cannot_be_replayed_for_another_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid cache checksum cannot authorize a different page identity."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = 0

    def request_override(*_args) -> dict:
        nonlocal calls
        calls += 1
        return _vlm_response()

    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=VisionUnderstandingService(
            request_override=request_override,
            max_concurrency=1,
        ),
    )
    kb_id = service.create_knowledge_base("vision-page-identity")["id"]
    document = await service.upload_document(
        kb_id,
        "scan.pdf",
        _scanned_pdf_bytes(pages=2),
    )
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )
    assert service.claim_next_pipeline_job() is not None
    await service.process_pipeline_job_vision(created["job_id"])
    assert calls == 2

    stored = service.get_pipeline_job(created["job_id"])
    result = stored["document_results"][0]
    artifact_path = service._pipeline_vision_path(  # noqa: SLF001
        result["vision_artifact_key"]
    )
    page_dir = artifact_path.parent / f"{artifact_path.stem}_pages"
    (page_dir / "page_2.json").write_bytes(
        (page_dir / "page_1.json").read_bytes()
    )
    with service._metadata_lock:  # noqa: SLF001 - resume fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        metadata["pipeline_jobs"][created["job_id"]]["document_results"][0][
            "vision_status"
        ] = "processing"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineJobStateError, match="page cache identity"):
        await service.process_pipeline_job_vision(created["job_id"])
    assert calls == 2


@pytest.mark.asyncio
async def test_vision_page_cache_cannot_be_replayed_for_another_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal source bytes do not make two source identities interchangeable."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = 0

    def request_override(*_args) -> dict:
        nonlocal calls
        calls += 1
        return _vlm_response()

    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=VisionUnderstandingService(
            request_override=request_override,
            max_concurrency=1,
        ),
    )
    kb_id = service.create_knowledge_base("vision-source-cache-identity")["id"]
    first = await service.upload_document(kb_id, "first.png", _png_bytes())
    second = await service.upload_document(kb_id, "second.png", _png_bytes())
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[first["id"], second["id"]],
    )
    assert service.claim_next_pipeline_job() is not None
    await service.process_pipeline_job_vision(created["job_id"])
    assert calls == 2

    stored = service.get_pipeline_job(created["job_id"])
    by_source = {
        item["source_id"]: item for item in stored["document_results"]
    }
    first_artifact = service._pipeline_vision_path(  # noqa: SLF001
        by_source[first["id"]]["vision_artifact_key"]
    )
    second_artifact = service._pipeline_vision_path(  # noqa: SLF001
        by_source[second["id"]]["vision_artifact_key"]
    )
    first_cache = first_artifact.parent / f"{first_artifact.stem}_pages" / "page_1.json"
    second_cache = second_artifact.parent / f"{second_artifact.stem}_pages" / "page_1.json"
    second_cache.write_bytes(first_cache.read_bytes())
    with service._metadata_lock:  # noqa: SLF001 - cross-source replay fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        second_result = next(
            item
            for item in metadata["pipeline_jobs"][created["job_id"]][
                "document_results"
            ]
            if item["source_id"] == second["id"]
        )
        second_result["vision_status"] = "processing"
        service._write_metadata_unlocked(metadata)  # noqa: SLF001

    with pytest.raises(PipelineJobStateError, match="page cache identity"):
        await service.process_pipeline_job_vision(created["job_id"])
    assert calls == 2


@pytest.mark.asyncio
async def test_continue_on_error_allows_processor_after_top_level_vision_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider orchestration failure is not artifact corruption."""

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=VisionUnderstandingService(
            request_override=lambda *_: _vlm_response()
        ),
    )

    async def fail_vision(*_args, **_kwargs):
        raise RuntimeError("synthetic vision orchestration failure")

    def process_without_visuals(
        _path: Path,
        *,
        filename: str,
        source_id: str,
        **_kwargs,
    ) -> ProcessedDocument:
        text = "Locally parsed fallback evidence."
        return ProcessedDocument(
            source_id=source_id,
            filename=filename,
            title=filename,
            text=text,
            blocks=[
                DocumentBlock(
                    block_id=f"block_{source_id}",
                    kind="paragraph",
                    text=text,
                    start_char=0,
                    end_char=len(text),
                )
            ],
        )

    monkeypatch.setattr(service.vision_processor, "analyze_source", fail_vision)
    monkeypatch.setattr(service.document_processor, "process", process_without_visuals)
    kb_id = service.create_knowledge_base("vision continue on error")["id"]
    document = await service.upload_document(kb_id, "chart.png", _png_bytes())
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                    "failure_policy": "continue_on_error",
                }
            },
            "stage_processor": {
                "config": {"failure_policy": "continue_on_error"}
            },
        },
        retrieval_profile={"mode": "vector"},
    )
    created = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )

    assert await KnowledgePipelineExecutor(service).run_once() is True

    completed = service.get_pipeline_job(created["job_id"])
    result = completed["document_results"][0]
    assert completed["status"] == "succeeded"
    assert result["vision_status"] == "failed"
    assert result.get("vision_artifact_hash") in {None, ""}
    assert result["status"] == "completed"
    assert completed["warnings"]
    assert service.get_pipeline_version(created["candidate_version_id"])[
        "status"
    ] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_policy", "expected_status"),
    [("continue_on_error", "succeeded"), ("strict", "failed")],
)
async def test_scanned_pdf_job_honors_visual_failure_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_policy: str,
    expected_status: str,
) -> None:
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = 0

    def fail_first_page(url: str, key: str, payload: dict) -> dict:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise TimeoutError("simulated visual timeout")
        return _vlm_response(ocr="Second page revenue", summary="Second page chart")

    vision = VisionUnderstandingService(
        request_override=fail_first_page,
        max_concurrency=1,
    )
    service = RagService(
        storage_dir=tmp_path / failure_policy / "storage",
        uploads_dir=tmp_path / failure_policy / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(
            tmp_path / failure_policy / "storage" / "vectors.json"
        ),
        llm_enabled=False,
        vision_processor=vision,
    )
    executor = KnowledgePipelineExecutor(service, poll_interval=0.01)
    kb_id = service.create_knowledge_base(f"scan-{failure_policy}")["id"]
    document = await service.upload_document(kb_id, "scan.pdf", _scanned_pdf_bytes())
    assert document["ingestion_status"] == "pipeline_required"
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                    "pdf_page_strategy": "auto",
                    "render_dpi": 144,
                    "max_pages": 100,
                    "max_image_edge": 1024,
                    "failure_policy": failure_policy,
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document["id"]],
    )

    assert await executor.run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == expected_status
    result = completed["document_results"][0]
    assert result["vision_failed_page_count"] == 1
    assert result["vision_processed_page_count"] == 1
    if failure_policy == "continue_on_error":
        assert completed["warnings"]
        assert service.get_pipeline_version(completed["candidate_version_id"])["status"] == "ready"
    else:
        with pytest.raises(Exception):
            service.get_pipeline_version(completed["candidate_version_id"])


@pytest_asyncio.fixture
async def vision_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    vision = VisionUnderstandingService(
        request_override=lambda *_: _vlm_response(),
    )
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
        vision_processor=vision,
    )
    executor = KnowledgePipelineExecutor(service, poll_interval=0.01)
    set_rag_service_for_tests(service)
    set_pipeline_executor_for_tests(executor)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service, executor
    set_pipeline_executor_for_tests(None)
    set_rag_service_for_tests(None)


@pytest.mark.asyncio
async def test_image_upload_requires_pipeline_and_mime_matches_extension(vision_api) -> None:
    client, _, _ = vision_api
    kb = await client.post("/api/rag/knowledge_bases", json={"name": "visual"})
    kb_id = kb.json()["id"]

    mismatch = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": ("chart.png", _png_bytes(), "image/jpeg")},
    )
    assert mismatch.status_code == 400
    assert "MIME type" in mismatch.json()["detail"]

    uploaded = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": ("chart.png", _png_bytes(), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    assert payload["chunk_count"] == 0
    assert payload["ingestion_status"] == "pipeline_required"
    assert payload["visual_candidate"] is True

    capabilities = await client.get("/api/rag/vision-capabilities")
    assert capabilities.status_code == 200
    serialized = capabilities.text.lower()
    assert "test-key" not in serialized
    assert "api_key" not in serialized
    assert capabilities.json()["renderer"]["name"] == "pdfium"


@pytest.mark.asyncio
async def test_managed_rag_vision_does_not_require_legacy_gateway(
    vision_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, _executor = vision_api
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_KEY", raising=False)
    monkeypatch.setattr(
        service.vision_processor,
        "managed_model_available",
        lambda entry_id, model_id, execution_shape="vision_json_unary": (
            entry_id == "rag_vision"
            and model_id == "openai/gpt-4o-mini"
            and execution_shape == "vision_json_unary"
        ),
    )
    kb_id = (
        await client.post(
            "/api/rag/knowledge_bases",
            json={"name": "managed visual"},
        )
    ).json()["id"]
    document = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": ("managed.png", _png_bytes(), "image/png")},
    )
    assert document.status_code == 200
    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4o-mini",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )

    job = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document.json()["id"]],
    )

    assert job["status"] == "queued"


@pytest.mark.asyncio
async def test_visual_pipeline_builds_vector_index_and_returns_page_citation(
    vision_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service, executor = vision_api
    original_analyze = service.vision_processor.analyze_source

    async def managed_analyze(*args, **kwargs):
        result = await original_analyze(*args, **kwargs)
        result.execution_mode = "managed"
        result.provider_route_receipts = [
            {
                "entry_id": "rag_vision",
                "status": "passed",
                "call_count": 1,
                "calls": [],
            }
        ]
        return result

    monkeypatch.setattr(
        service.vision_processor,
        "analyze_source",
        managed_analyze,
    )
    kb_id = (await client.post("/api/rag/knowledge_bases", json={"name": "charts"})).json()["id"]
    document = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": ("revenue.png", _png_bytes(), "image/png")},
    )
    assert document.status_code == 200, document.text
    document_id = document.json()["id"]

    draft = service.update_pipeline_draft(
        kb_id,
        {
            "stage_image_understanding": {
                "config": {
                    "enabled": True,
                    "vision_model_id": "openai/gpt-4.1-mini",
                    "pdf_page_strategy": "auto",
                    "render_dpi": 144,
                    "max_pages": 100,
                    "max_image_edge": 2048,
                    "failure_policy": "continue_on_error",
                }
            }
        },
        retrieval_profile={"mode": "vector"},
    )
    job = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document_id],
    )
    assert await executor.run_once() is True
    completed = service.get_pipeline_job(job["job_id"])
    assert completed["status"] == "succeeded", completed.get("error")
    assert completed["stages"][1]["id"] == "vision"
    result = completed["document_results"][0]
    assert result["vision_processed_page_count"] == 1
    assert result["vision_failed_page_count"] == 0
    assert result["vision_block_count"] == 4
    assert result["vision_execution_mode"] == "managed"
    assert result["vision_provider_route_receipts"][0]["entry_id"] == "rag_vision"
    assert result["execution_mode"] == "managed"
    assert result["provider_route_receipts"]["entry_id"] == "rag_vision"

    version = service.get_pipeline_version(completed["candidate_version_id"])
    assert version["vision_profile"]["vision_model_id"] == "openai/gpt-4.1-mini"
    assert version["vector_index_ready"] is True
    assert version["lexical_index_ready"] is False
    preview = await service.query_pipeline_version(
        version["version_id"],
        "quarterly revenue chart",
        top_k=5,
        retrieval={"mode": "vector"},
    )
    assert preview["sources"]
    source = preview["sources"][0]
    assert source["page_number"] == 1
    assert source["visual_kind"] in {
        "image_ocr",
        "image_description",
        "visual_table",
        "visual_chart",
    }
    assert source["source_block_id"]
    serialized = json.dumps(version).lower()
    assert "stored_path" not in serialized
    assert "test-key" not in serialized
