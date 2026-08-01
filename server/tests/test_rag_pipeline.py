from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.rag.api import get_rag_service, set_rag_service_for_tests
from server.rag.document_parser import DocumentParseError
from server.rag.embedder import EmbeddingClient
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    set_rag_service_for_tests(service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client
    set_rag_service_for_tests(None)


async def create_kb(client: httpx.AsyncClient, name: str = "pipeline") -> str:
    response = await client.post("/api/rag/knowledge_bases", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def upload_pipeline_document(client: httpx.AsyncClient, kb_id: str) -> dict:
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "pipeline.txt",
                b"ModelMirror Knowledge Pipeline maps uploaded files into artifacts, chunks, and citations.",
                "text/plain",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_rag_pipeline_assets_artifacts_and_chunks(client: httpx.AsyncClient) -> None:
    kb_id = await create_kb(client, "pipeline metadata")
    document = await upload_pipeline_document(client, kb_id)

    assets_response = await client.get(f"/api/rag/pipeline/assets?kb_id={kb_id}")
    assert assets_response.status_code == 200, assets_response.text
    assets_data = assets_response.json()
    assert assets_data["asset_count"] == 1
    asset = assets_data["assets"][0]
    assert asset["document_id"] == document["id"]
    assert asset["knowledge_base_id"] == kb_id
    assert asset["filename"] == "pipeline.txt"
    assert asset["extension"] == ".txt"
    assert "stored_path" not in asset

    artifacts_response = await client.get(f"/api/rag/pipeline/artifacts?kb_id={kb_id}")
    assert artifacts_response.status_code == 200, artifacts_response.text
    artifacts_data = artifacts_response.json()
    assert artifacts_data["artifact_count"] == 1
    artifact = artifacts_data["artifacts"][0]
    assert artifact["artifact_id"] == f"artifact_{document['id']}"
    assert artifact["file_asset_id"] == asset["file_asset_id"]
    assert artifact["chunk_count"] == document["chunk_count"]

    chunks_response = await client.get(
        f"/api/rag/pipeline/artifacts/{artifact['artifact_id']}/chunks"
    )
    assert chunks_response.status_code == 200, chunks_response.text
    chunks_data = chunks_response.json()
    assert chunks_data["chunk_count"] == document["chunk_count"]
    assert chunks_data["chunks"][0]["artifact_id"] == artifact["artifact_id"]
    assert chunks_data["chunks"][0]["text_length"] > 0
    assert "Knowledge Pipeline" in chunks_data["chunks"][0]["text_preview"]


@pytest.mark.asyncio
async def test_rag_pipeline_draft_empty_knowledge_base(client: httpx.AsyncClient) -> None:
    kb_id = await create_kb(client, "empty pipeline draft")

    response = await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["kb_id"] == kb_id
    assert data["draft_id"] == f"draft_{kb_id}"
    assert data["version"] == 1
    assert data["editable"] is True
    assert isinstance(data["updated_at"], float)
    assert data["stage_count"] == 4

    stages = {stage["kind"]: stage for stage in data["stages"]}
    assert set(stages) == {
        "data_source",
        "processor",
        "chunker",
        "image_understanding",
    }
    assert stages["data_source"]["item_count"] == 0
    assert stages["processor"]["item_count"] == 0
    assert stages["chunker"]["item_count"] == 0
    assert data["index_schema_version"] == 2
    assert data["retrieval_profile"]["mode"] == "hybrid"
    assert data["embedding_profile"]["degraded"] is True
    assert stages["chunker"]["config"]["strategy"] == "recursive_character"
    assert stages["chunker"]["config"]["chunk_size"] == 500
    assert stages["chunker"]["config"]["chunk_overlap"] == 50
    assert stages["image_understanding"]["status"] == "disabled"
    assert stages["image_understanding"]["metadata"]["enabled"] is False
    assert stages["image_understanding"]["config"]["enabled"] is False


@pytest.mark.asyncio
async def test_rag_pipeline_draft_counts_and_safe_fields(client: httpx.AsyncClient) -> None:
    kb_id = await create_kb(client, "pipeline draft")
    document = await upload_pipeline_document(client, kb_id)

    response = await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    stages = {stage["kind"]: stage for stage in data["stages"]}

    assert stages["data_source"]["item_count"] == 1
    assert stages["data_source"]["metadata"]["asset_count"] == 1
    assert stages["data_source"]["metadata"]["document_count"] == 1
    assert stages["processor"]["item_count"] == 1
    assert stages["processor"]["metadata"]["artifact_count"] == 1
    assert stages["chunker"]["item_count"] == document["chunk_count"]
    assert stages["chunker"]["metadata"]["chunk_count"] == document["chunk_count"]

    serialized = str(data).lower()
    assert "stored_path" not in serialized
    assert "stored_path" not in serialized
    assert "api_key" not in serialized
    assert "embedding_vector" not in serialized
    assert "modelmirror knowledge pipeline maps uploaded files" not in serialized


@pytest.mark.asyncio
async def test_rag_pipeline_draft_patch_persists_chunker_config(
    client: httpx.AsyncClient,
) -> None:
    kb_id = await create_kb(client, "editable pipeline draft")

    response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={
            "embedding_profile": {"model": "text-embedding-3-small"},
            "retrieval_profile": {
                "mode": "hybrid",
                "vector_weight": 0.6,
                "fulltext_weight": 0.4,
                "top_k": 8,
                "score_threshold": 0.2,
                "candidate_multiplier": 3,
                "rerank_enabled": False,
                "rerank_provider": "auto",
                "rerank_top_n": 8,
            },
            "stages": {
                "stage_chunker": {
                    "config": {
                        "chunk_size": 800,
                        "chunk_overlap": 120,
                    }
                }
            }
        },
    )
    assert response.status_code == 200, response.text
    patched = response.json()
    assert patched["version"] == 2
    chunker = {stage["kind"]: stage for stage in patched["stages"]}["chunker"]
    assert chunker["config"]["chunk_size"] == 800
    assert chunker["config"]["chunk_overlap"] == 120
    assert patched["retrieval_profile"]["vector_weight"] == 0.6
    assert patched["retrieval_profile"]["fulltext_weight"] == 0.4
    assert patched["retrieval_profile"]["top_k"] == 8
    assert patched["embedding_profile"]["model"] == "text-embedding-3-small"

    response = await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")
    assert response.status_code == 200, response.text
    data = response.json()
    chunker = {stage["kind"]: stage for stage in data["stages"]}["chunker"]
    assert data["version"] == 2
    assert chunker["config"]["chunk_size"] == 800
    assert chunker["config"]["chunk_overlap"] == 120
    assert data["retrieval_profile"]["score_threshold"] == 0.2


@pytest.mark.asyncio
async def test_rag_retrieval_capabilities_are_safe(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/rag/retrieval-capabilities")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["index_schema_version"] == 2
    assert data["fulltext"]["backend"] == "sqlite_fts5"
    assert data["modes"] == ["vector", "fulltext", "hybrid"]
    serialized = str(data).lower()
    assert "api_key" not in serialized
    assert "sk-" not in serialized


@pytest.mark.asyncio
async def test_rag_processor_capabilities_and_preview_are_safe(
    client: httpx.AsyncClient,
) -> None:
    kb_id = await create_kb(client, "processor preview")
    document = await upload_pipeline_document(client, kb_id)

    capabilities = await client.get("/api/rag/processor-capabilities")
    assert capabilities.status_code == 200, capabilities.text
    capability_data = capabilities.json()
    assert capability_data["modes"] == ["general", "qa", "summary"]
    assert "table" in capability_data["block_types"]
    assert isinstance(capability_data["llm_configured"], bool)

    preview = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/processor-preview",
        json={
            "document_id": document["id"],
            "processor": {"mode": "general", "extract_title": True},
        },
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["document_id"] == document["id"]
    assert data["block_count"] >= 1
    assert data["generated_count"] == 0
    assert data["blocks"][0]["kind"] == "paragraph"
    serialized = str(data).lower()
    assert "stored_path" not in serialized
    assert "api_key" not in serialized
    assert "embedding" not in serialized


@pytest.mark.asyncio
async def test_rag_processor_preview_maps_parse_failure_to_validation_error(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kb_id = await create_kb(client, "processor preview failure")
    document = await upload_pipeline_document(client, kb_id)

    def fail_parse(*args, **kwargs):
        raise DocumentParseError("preview parser failed")

    monkeypatch.setattr(get_rag_service().document_processor, "process", fail_parse)
    response = await client.post(
        f"/api/rag/pipeline/draft/{kb_id}/processor-preview",
        json={"document_id": document["id"], "processor": {"mode": "general"}},
    )

    assert response.status_code == 400
    assert "preview parser failed" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "processor",
    [
        {"mode": "unknown"},
        {"failure_policy": "ignore"},
        {"max_generated_items": 0},
        {"max_generated_items": 51},
        {"preserve_tables": "true"},
    ],
)
async def test_rag_pipeline_draft_rejects_invalid_processor_config(
    client: httpx.AsyncClient,
    processor: dict,
) -> None:
    kb_id = await create_kb(client, "invalid processor")
    response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"stages": {"stage_processor": {"config": processor}}},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rag_pipeline_draft_rejects_invalid_retrieval_profile(
    client: httpx.AsyncClient,
) -> None:
    kb_id = await create_kb(client, "invalid retrieval draft")
    response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"retrieval_profile": {"mode": "graph", "top_k": 51}},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"chunk_size": 99, "chunk_overlap": 10},
        {"chunk_size": 4001, "chunk_overlap": 10},
        {"chunk_size": 200, "chunk_overlap": 200},
        {"chunk_size": 200, "chunk_overlap": -1},
    ],
)
async def test_rag_pipeline_draft_rejects_invalid_chunker_config(
    client: httpx.AsyncClient,
    config: dict,
) -> None:
    kb_id = await create_kb(client, "invalid pipeline draft")

    response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={"stages": {"stage_chunker": {"config": config}}},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rag_pipeline_draft_rejects_enabled_image_understanding(
    client: httpx.AsyncClient,
) -> None:
    kb_id = await create_kb(client, "image stage draft")

    response = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={
            "stages": {
                "stage_image_understanding": {
                    "config": {"enabled": True},
                }
            }
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_rag_pipeline_draft_preflight_empty_and_with_document(
    client: httpx.AsyncClient,
) -> None:
    kb_id = await create_kb(client, "pipeline preflight")

    response = await client.post(f"/api/rag/pipeline/draft/{kb_id}/preflight")
    assert response.status_code == 200, response.text
    empty = response.json()
    assert empty["ready"] is False
    assert empty["document_count"] == 0
    assert empty["artifact_count"] == 0
    assert empty["chunk_count"] == 0
    assert empty["warnings"]
    assert {item["kind"] for item in empty["stage_checks"]} == {
        "data_source",
        "processor",
        "chunker",
        "image_understanding",
    }

    await upload_pipeline_document(client, kb_id)
    response = await client.post(f"/api/rag/pipeline/draft/{kb_id}/preflight")
    assert response.status_code == 200, response.text
    populated = response.json()
    assert populated["ready"] is True
    assert populated["document_count"] == 1
    assert populated["artifact_count"] == 1
    assert populated["chunk_count"] >= 1
    assert populated["warnings"] == []

    serialized = str(populated).lower()
    assert "stored_path" not in serialized
    assert "embedding" not in serialized
    assert "modelmirror knowledge pipeline maps uploaded files" not in serialized


@pytest.mark.asyncio
async def test_rag_pipeline_citations(client: httpx.AsyncClient) -> None:
    kb_id = await create_kb(client, "pipeline citations")
    document = await upload_pipeline_document(client, kb_id)

    response = await client.post(
        "/api/rag/pipeline/citations",
        json={
            "kb_id": kb_id,
            "question": "What does the Knowledge Pipeline map?",
            "top_k": 2,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["kb_id"] == kb_id
    assert data["citation_count"] >= 1
    citation = data["citations"][0]
    assert citation["document_id"] == document["id"]
    assert citation["document_name"] == "pipeline.txt"
    assert citation["chunk_id"].startswith(document["id"])
    assert "artifacts" in citation["snippet"]
    assert isinstance(citation["score"], float)


@pytest.mark.asyncio
async def test_rag_pipeline_missing_resources_return_404(client: httpx.AsyncClient) -> None:
    assets_response = await client.get("/api/rag/pipeline/assets?kb_id=missing")
    assert assets_response.status_code == 404

    artifacts_response = await client.get("/api/rag/pipeline/artifacts?kb_id=missing")
    assert artifacts_response.status_code == 404

    chunks_response = await client.get("/api/rag/pipeline/artifacts/artifact_missing/chunks")
    assert chunks_response.status_code == 404

    draft_response = await client.get("/api/rag/pipeline/draft?kb_id=missing")
    assert draft_response.status_code == 404

    draft_patch_response = await client.patch(
        "/api/rag/pipeline/draft/missing",
        json={"stages": {"stage_chunker": {"config": {"chunk_size": 500}}}},
    )
    assert draft_patch_response.status_code == 404

    preflight_response = await client.post("/api/rag/pipeline/draft/missing/preflight")
    assert preflight_response.status_code == 404
