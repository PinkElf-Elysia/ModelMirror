from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.rag.api import (
    get_pipeline_executor,
    set_rag_service_for_tests,
)
from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_graph import (
    compile_pipeline_graph,
    default_pipeline_graph,
    validate_pipeline_graph,
)
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    set_rag_service_for_tests(service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client, service
    set_rag_service_for_tests(None)


async def create_kb(client: httpx.AsyncClient, name: str = "graph") -> str:
    response = await client.post("/api/rag/knowledge_bases", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def upload_document(client: httpx.AsyncClient, kb_id: str) -> dict:
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "architecture.md",
                b"# Architecture\n\nModelMirror compiles a knowledge graph into one durable pipeline job.\n\n## Index\n\nVector and lexical indexes are activated together.",
                "text/markdown",
            )
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_default_draft_generates_valid_compilable_graph(client) -> None:
    http_client, _ = client
    kb_id = await create_kb(http_client)
    draft = (await http_client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    graph_response = await http_client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")

    assert graph_response.status_code == 200, graph_response.text
    payload = graph_response.json()
    assert payload["graph_revision"] == 1
    assert payload["compiled_draft_version"] == draft["version"]
    assert payload["valid"] is True
    assert [item["kind"] for item in payload["graph"]["nodes"]] == [
        "data_source",
        "structured_processor",
        "recursive_chunker",
        "embedding",
        "dual_index",
        "retrieval",
    ]
    compiled = compile_pipeline_graph(payload["graph"])
    assert compiled.stage_updates["stage_chunker"]["strategy"] == "recursive_character"
    assert compiled.retrieval_profile["mode"] == draft["retrieval_profile"]["mode"]


def test_graph_validation_rejects_cycles_bad_ports_missing_stages_and_orphans() -> None:
    draft = {
        "stages": {
            "stage_data_source": {"source_mode": "uploaded_files"},
            "stage_processor": {"mode": "general"},
            "stage_chunker": {"strategy": "recursive_character"},
        },
        "embedding_profile": {"model": "hash"},
        "retrieval_profile": {"mode": "hybrid"},
    }
    graph = default_pipeline_graph("kb_test", draft)

    missing = {**graph, "nodes": graph["nodes"][:-1]}
    assert "missing_required_stage" in {item.code for item in validate_pipeline_graph(missing)}

    bad_port = {**graph, "edges": [dict(item) for item in graph["edges"]]}
    bad_port["edges"][0]["source_port"] = "wrong"
    assert "invalid_edge_port" in {item.code for item in validate_pipeline_graph(bad_port)}

    orphan = {**graph, "edges": graph["edges"][:-1]}
    assert "incomplete_chain" in {item.code for item in validate_pipeline_graph(orphan)}

    cycle = {**graph, "edges": [dict(item) for item in graph["edges"]]}
    cycle["edges"].append(
        {
            "id": "cycle",
            "source": "retrieval",
            "target": "source",
            "source_port": None,
            "target_port": None,
        }
    )
    assert "graph_cycle" in {item.code for item in validate_pipeline_graph(cycle)}


def test_graph_validation_rejects_dual_chunkers_and_unwired_image_stage() -> None:
    graph = default_pipeline_graph(
        "kb_test",
        {
            "stages": {
                "stage_data_source": {},
                "stage_processor": {},
                "stage_chunker": {"strategy": "recursive_character"},
            },
            "embedding_profile": {"model": "hash"},
            "retrieval_profile": {"mode": "hybrid"},
        },
    )
    extra = dict(graph["nodes"][2])
    extra["id"] = "parent_chunker"
    extra["kind"] = "parent_child_chunker"
    graph["nodes"].append(extra)
    image = {
        "id": "image",
        "kind": "image_understanding",
        "title": "Image",
        "position": {"x": 200, "y": 400},
        "config": {"enabled": False},
        "enabled": True,
    }
    graph["nodes"].append(image)
    codes = {item.code for item in validate_pipeline_graph(graph)}
    assert "duplicate_stage" in codes
    assert "invalid_stage_order" in codes


def test_graph_accepts_optional_image_understanding_stage() -> None:
    draft = {
        "stages": {
            "stage_data_source": {},
            "stage_processor": {},
            "stage_image_understanding": {
                "enabled": True,
                "vision_model_id": "openai/gpt-4.1-mini",
                "pdf_page_strategy": "auto",
                "render_dpi": 144,
                "max_pages": 100,
                "max_image_edge": 2048,
                "failure_policy": "continue_on_error",
            },
            "stage_chunker": {"strategy": "recursive_character"},
        },
        "embedding_profile": {"model": "hash"},
        "retrieval_profile": {"mode": "hybrid"},
    }
    graph = default_pipeline_graph("kb_visual", draft)
    assert [node["kind"] for node in graph["nodes"]][1] == "image_understanding"
    assert validate_pipeline_graph(graph) == []

    compiled = compile_pipeline_graph(graph)
    vision = compiled.stage_updates["stage_image_understanding"]
    assert vision["enabled"] is True
    assert vision["vision_model_id"] == "openai/gpt-4.1-mini"


@pytest.mark.asyncio
async def test_invalid_graph_save_does_not_mutate_draft_and_revision_conflicts(client) -> None:
    http_client, _ = client
    kb_id = await create_kb(http_client, "revision")
    current = (await http_client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")).json()
    draft_before = (await http_client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    invalid_graph = dict(current["graph"])
    invalid_graph["edges"] = invalid_graph["edges"][:-1]

    invalid = await http_client.put(
        f"/api/rag/pipeline/graph/{kb_id}",
        json={"expected_revision": 1, "graph": invalid_graph},
    )
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()["detail"]["issues"]
    draft_after = (await http_client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")).json()
    assert draft_after["version"] == draft_before["version"]

    saved = await http_client.put(
        f"/api/rag/pipeline/graph/{kb_id}",
        json={"expected_revision": 1, "graph": current["graph"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["graph_revision"] == 2
    stale = await http_client.put(
        f"/api/rag/pipeline/graph/{kb_id}",
        json={"expected_revision": 1, "graph": current["graph"]},
    )
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_draft_form_syncs_graph_config_and_preserves_positions(client) -> None:
    http_client, _ = client
    kb_id = await create_kb(http_client, "sync")
    current = (await http_client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")).json()
    current["graph"]["nodes"][2]["position"] = {"x": 777, "y": 333}
    saved = await http_client.put(
        f"/api/rag/pipeline/graph/{kb_id}",
        json={"expected_revision": current["graph_revision"], "graph": current["graph"]},
    )
    assert saved.status_code == 200, saved.text

    patched = await http_client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={
            "stages": {
                "stage_chunker": {
                    "config": {
                        "strategy": "parent_child",
                        "parent_chunk_size": 1200,
                        "child_chunk_size": 300,
                    }
                }
            }
        },
    )
    assert patched.status_code == 200, patched.text
    graph = (await http_client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")).json()
    chunker = next(item for item in graph["graph"]["nodes"] if item["id"] == "chunker")
    assert chunker["kind"] == "parent_child_chunker"
    assert chunker["position"] == {"x": 777.0, "y": 333.0}
    assert graph["compiled_draft_version"] == patched.json()["version"]


@pytest.mark.asyncio
async def test_node_preview_is_truncated_safe_and_does_not_create_job(client) -> None:
    http_client, _ = client
    kb_id = await create_kb(http_client, "preview")
    document = await upload_document(http_client, kb_id)
    graph = (await http_client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")).json()["graph"]

    response = await http_client.post(
        f"/api/rag/pipeline/graph/{kb_id}/preview-node",
        json={"graph": graph, "node_id": "chunker", "document_id": document["id"]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["preview_type"] == "chunks"
    assert len(payload["items"]) <= 20
    assert "stored_path" not in str(payload).lower()
    jobs = await http_client.get(f"/api/rag/pipeline/jobs?kb_id={kb_id}")
    assert jobs.json()["job_count"] == 0


@pytest.mark.asyncio
async def test_graph_execute_reuses_existing_pipeline_job_and_pins_revision(client) -> None:
    http_client, _ = client
    kb_id = await create_kb(http_client, "execute")
    await upload_document(http_client, kb_id)
    graph = (await http_client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")).json()

    response = await http_client.post(
        f"/api/rag/pipeline/graph/{kb_id}/execute",
        json={
            "graph_revision": graph["graph_revision"],
            "draft_version": graph["compiled_draft_version"],
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["graph_revision"] == graph["graph_revision"]
    assert job["status"] == "queued"

    processed = await get_pipeline_executor().run_once()
    assert processed is True
    completed = await http_client.get(f"/api/rag/pipeline/jobs/{job['job_id']}")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_graph_persists_across_service_reload(client, tmp_path: Path) -> None:
    http_client, service = client
    kb_id = await create_kb(http_client, "persist")
    current = (await http_client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")).json()
    current["graph"]["nodes"][0]["position"] = {"x": 123, "y": 456}
    saved = await http_client.put(
        f"/api/rag/pipeline/graph/{kb_id}",
        json={"expected_revision": 1, "graph": current["graph"]},
    )
    assert saved.status_code == 200, saved.text

    reloaded = RagService(
        storage_dir=service.storage_dir,
        uploads_dir=service.uploads_dir,
        embedder=EmbeddingClient(api_key="", dimension=64),
        vector_store=LocalJsonVectorStore(service.storage_dir / "vectors-reloaded.json"),
        llm_enabled=False,
    )
    graph = reloaded.get_pipeline_graph(kb_id)
    assert graph["graph_revision"] == 2
    source = next(item for item in graph["graph"]["nodes"] if item["id"] == "source")
    assert source["position"] == {"x": 123.0, "y": 456.0}
