from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.rag.api import set_rag_service_for_tests
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


async def _create_kb_with_document(client: httpx.AsyncClient) -> str:
    kb_response = await client.post(
        "/api/rag/knowledge_bases",
        json={"name": "workflow citation test"},
    )
    assert kb_response.status_code == 200, kb_response.text
    kb_id = kb_response.json()["id"]

    upload_response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={
            "file": (
                "workflow-citation.txt",
                (
                    b"ModelMirror Knowledge Pipeline creates FileAsset, "
                    b"Artifact, Chunk, and CitationAnchor records for workflow use."
                ),
                "text/plain",
            )
        },
    )
    assert upload_response.status_code == 200, upload_response.text
    return kb_id


@pytest.mark.asyncio
async def test_workflow_knowledge_citation_outputs_json_and_run_trace(
    client: httpx.AsyncClient,
) -> None:
    kb_id = await _create_kb_with_document(client)
    workflow = {
        "id": "knowledge-citation-workflow",
        "title": "knowledge citation workflow",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "knowledge_citation",
                "type": "knowledge_citation",
                "data": {
                    "kind": "knowledge_citation",
                    "title": "Knowledge citations",
                    "queryVariable": "user_input",
                    "knowledgeBaseId": kb_id,
                    "top_k": "2",
                    "outputVariable": "citation_anchors_json",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "citation_anchors_json"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "knowledge_citation"},
            {"id": "e2", "source": "knowledge_citation", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": "What does the Knowledge Pipeline create?"},
        },
    )
    assert response.status_code == 200, response.text

    events = _parse_sse_events(response.text)
    workflow_meta = next(event for event in events if event.get("event") == "workflow_meta")
    workflow_run_id = workflow_meta["run_id"]

    citation_delta = next(
        event
        for event in events
        if event.get("event") == "node_delta"
        and event.get("node_id") == "knowledge_citation"
    )
    assert "CitationAnchor" in citation_delta["output"]
    assert citation_delta["citation_count"] >= 1

    citation_end = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "knowledge_citation"
    )
    citation_payload = json.loads(citation_end["output"])
    assert citation_payload["citation_count"] >= 1
    citation = citation_payload["citations"][0]
    assert citation["chunk_id"]
    assert citation["document_name"] == "workflow-citation.txt"
    assert isinstance(citation["score"], float)
    assert "CitationAnchor" in citation["snippet"]
    assert "stored_path" not in citation

    child_runs_response = await client.get(
        f"/api/runtime/runs?run_type=knowledge_citation&parent_run_id={workflow_run_id}&limit=20"
    )
    assert child_runs_response.status_code == 200, child_runs_response.text
    citation_runs = child_runs_response.json()
    citation_run = next(
        item for item in citation_runs if item["metadata"]["node_id"] == "knowledge_citation"
    )
    assert citation_run["status"] == "completed"
    assert citation_run["metadata"]["kb_id"] == kb_id
    assert citation_run["metadata"]["citation_count"] >= 1

    checkpoints_response = await client.get(
        f"/api/runtime/runs/{citation_run['run_id']}/checkpoints"
    )
    assert checkpoints_response.status_code == 200, checkpoints_response.text
    checkpoint_types = {
        checkpoint["event_type"] for checkpoint in checkpoints_response.json()
    }
    assert "knowledge_citation.started" in checkpoint_types
    assert "knowledge_citation.completed" in checkpoint_types


def _parse_sse_events(sse_text: str) -> list[dict]:
    events: list[dict] = []
    for line in sse_text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events
