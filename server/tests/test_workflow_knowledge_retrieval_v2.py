from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.rag.api import set_rag_service_for_tests
from server.rag.embedder import EmbeddingClient
from server.rag.pipeline_executor import KnowledgePipelineExecutor
from server.rag.rag_service import RagService
from server.rag.vector_store import LocalJsonVectorStore
from server.xpert_runtime.workflow_knowledge import (
    WorkflowKnowledgeContractError,
    execute_workflow_knowledge_retrieval,
    resolve_workflow_knowledge_base,
)


class _FakeKnowledgeService:
    def __init__(self, knowledge_bases: list[dict[str, Any]]) -> None:
        self.knowledge_bases = knowledge_bases
        self.search_calls = 0

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        return list(self.knowledge_bases)

    async def search_knowledge(
        self,
        kb_id: str,
        question: str,
        *,
        top_k: int = 5,
    ) -> dict[str, Any]:
        self.search_calls += 1
        return {
            "kb_id": kb_id,
            "version_id": "version_2",
            "answer": "ignored generated answer",
            "sources": [
                {
                    "chunk_id": "chunk_1",
                    "source_document_id": "doc_1",
                    "document_name": "manual.md",
                    "text": "A" * 2_500,
                    "matched_text": "relevant context",
                    "score": 0.88,
                    "source_block_id": "block_1",
                }
            ],
            "retrieval": {"mode": "hybrid", "top_k": top_k},
            "warnings": ["hash embedding fallback"],
        }

    def citation_anchors_from_search_result(
        self,
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        source = result["sources"][0]
        return [
            {
                "citation_id": "citation_chunk_1",
                "chunk_id": source["chunk_id"],
                "document_id": source["source_document_id"],
                "document_name": source["document_name"],
                "score": source["score"],
                "snippet": source["matched_text"],
                "source_block_id": source["source_block_id"],
            }
        ]


def test_resolve_legacy_knowledge_base_only_allows_one_available_kb() -> None:
    service = _FakeKnowledgeService([{"id": "kb_one"}])

    kb_id, warnings = resolve_workflow_knowledge_base(
        service,
        "",
        allow_legacy_fallback=True,
    )

    assert kb_id == "kb_one"
    assert warnings == [
        "Legacy node omitted knowledgeBaseId; the only available knowledge base was used."
    ]

    service.knowledge_bases.append({"id": "kb_two"})
    with pytest.raises(WorkflowKnowledgeContractError) as exc_info:
        resolve_workflow_knowledge_base(
            service,
            "",
            allow_legacy_fallback=True,
        )
    assert exc_info.value.error_code == "workflow_knowledge_base_ambiguous"


@pytest.mark.asyncio
async def test_execute_v2_returns_limited_typed_result_without_rag_answer() -> None:
    service = _FakeKnowledgeService([{"id": "kb_one"}])

    result, metadata = await execute_workflow_knowledge_retrieval(
        service,
        configured_kb_id="kb_one",
        query="Where is the policy?",
        top_k=5,
        contract_version=2,
        return_mode="result",
    )

    assert service.search_calls == 1
    assert result["knowledge_base_id"] == "kb_one"
    assert result["version_id"] == "version_2"
    assert result["context"] == "A" * 2_000
    assert result["sources"][0]["text_length"] == 2_500
    assert result["sources"][0]["text_truncated"] is True
    assert result["citations"][0]["source_block_id"] == "block_1"
    assert "answer" not in result
    assert metadata == {
        "kb_id": "kb_one",
        "version_id": "version_2",
        "hit_count": 1,
        "citation_count": 1,
        "context_length": 2_000,
        "warning_count": 1,
        "contract_version": 2,
        "return_mode": "result",
    }


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    service = RagService(
        storage_dir=tmp_path / "storage",
        uploads_dir=tmp_path / "uploads",
        embedder=EmbeddingClient(api_key="", dimension=128),
        vector_store=LocalJsonVectorStore(tmp_path / "storage" / "vectors.json"),
        llm_enabled=False,
    )
    executor = KnowledgePipelineExecutor(service)
    set_rag_service_for_tests(service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client, service, executor
    set_rag_service_for_tests(None)


async def _create_kb(
    client: httpx.AsyncClient,
    service: RagService,
    executor: KnowledgePipelineExecutor,
    name: str,
    text: bytes,
) -> str:
    kb_response = await client.post(
        "/api/rag/knowledge_bases",
        json={"name": name},
    )
    assert kb_response.status_code == 200, kb_response.text
    kb_id = kb_response.json()["id"]
    upload_response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": (f"{name}.txt", text, "text/plain")},
    )
    assert upload_response.status_code == 200, upload_response.text
    document_id = str(upload_response.json()["id"])
    draft = service.update_pipeline_draft(
        kb_id,
        {},
        retrieval_profile={"mode": "vector", "top_k": 3},
    )
    job = service.create_pipeline_job(
        kb_id,
        draft_version=draft["version"],
        source_document_ids=[document_id],
    )
    assert await executor.run_once() is True
    version_id = str(job["candidate_version_id"])
    with service._metadata_lock:  # noqa: SLF001 - historical active fixture.
        metadata = service._read_metadata_unlocked()  # noqa: SLF001
        version = metadata["pipeline_versions"][version_id]
        version["status"] = "active"
        version["activated_at"] = 1.0
        metadata["pipeline_active_versions"][kb_id] = version_id
        service._write_metadata_unlocked(metadata)  # noqa: SLF001
    return kb_id


def _workflow(kb_id: str, *, contract_version: int | None = 2) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": "knowledge_retrieval",
        "queryVariable": "user_input",
        "knowledgeBaseId": kb_id,
        "top_k": "3",
        "outputVariable": "knowledge_result",
    }
    if contract_version is not None:
        data.update({"contractVersion": contract_version, "returnMode": "result"})
    return {
        "id": "knowledge-retrieval-v2-workflow",
        "title": "Knowledge retrieval V2",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {"id": "retrieval", "type": "knowledge_retrieval", "data": data},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "knowledge_result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "retrieval"},
            {"id": "e2", "source": "retrieval", "target": "output"},
        ],
    }


def _parse_sse_events(sse_text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
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


@pytest.mark.asyncio
async def test_workflow_retrieval_v2_preserves_typed_variable(
    client,
) -> None:
    http_client, service, executor = client
    kb_id = await _create_kb(
        http_client,
        service,
        executor,
        "retrieval-v2",
        b"The blue protocol requires a signed approval record before deployment.",
    )

    response = await http_client.post(
        "/api/workflow/run",
        json={
            "workflow": _workflow(kb_id),
            "inputs": {"user_input": "What does the blue protocol require?"},
        },
    )
    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    node_end = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "retrieval"
    )
    result = node_end["variables"]["knowledge_result"]
    assert isinstance(result, dict)
    assert result["knowledge_base_id"] == kb_id
    assert result["sources"]
    assert result["citations"]
    assert "signed approval record" in result["context"]


@pytest.mark.asyncio
async def test_legacy_retrieval_without_kb_fails_when_multiple_exist(
    client,
) -> None:
    http_client, service, executor = client
    await _create_kb(
        http_client, service, executor, "first", b"First knowledge base content."
    )
    await _create_kb(
        http_client, service, executor, "second", b"Second knowledge base content."
    )
    workflow = _workflow("", contract_version=None)

    response = await http_client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "content"}},
    )
    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "workflow_knowledge_base_ambiguous"
    assert "multiple" in error["message"].lower()
