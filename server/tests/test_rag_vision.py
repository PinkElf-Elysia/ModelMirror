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
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
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
async def test_visual_pipeline_builds_dual_index_and_returns_page_citation(vision_api) -> None:
    client, service, executor = vision_api
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

    version = service.get_pipeline_version(completed["candidate_version_id"])
    assert version["vision_profile"]["vision_model_id"] == "openai/gpt-4.1-mini"
    assert version["vector_index_ready"] is True
    assert version["lexical_index_ready"] is True
    preview = await service.query_pipeline_version(
        version["version_id"],
        "quarterly revenue chart",
        top_k=5,
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
