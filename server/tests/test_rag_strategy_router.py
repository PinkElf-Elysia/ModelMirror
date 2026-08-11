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
async def router_client(tmp_path: Path):
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
    ) as client:
        yield client, service
    set_rag_service_for_tests(None)


async def _create_kb(client: httpx.AsyncClient, name: str) -> str:
    response = await client.post("/api/rag/knowledge_bases", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _upload_text(
    client: httpx.AsyncClient,
    kb_id: str,
    text: str,
    *,
    filename: str = "strategy.md",
) -> dict:
    response = await client.post(
        f"/api/rag/knowledge_bases/{kb_id}/documents",
        files={"file": (filename, text.encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _long_policy_text() -> str:
    sentence = (
        "Policy MM-2026-ALPHA requires reviewers to retain the numbered control "
        "identifier and compare the conflicting exception before approval. "
    )
    return "# Review policy\n\n" + sentence * 32


@pytest.mark.asyncio
async def test_strategy_router_capabilities_and_empty_corpus(
    router_client,
) -> None:
    client, _ = router_client
    kb_id = await _create_kb(client, "empty strategy")

    capabilities = await client.get("/api/rag/strategy-router/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    payload = capabilities.json()
    assert payload["rules_version"] == "rag-strategy-rules-v1"
    assert payload["score_threshold_fixed"] == 0.0
    assert payload["embedding"]["degraded"] is True
    assert "semantic_chunking" in payload["deferred_strategies"]

    response = await client.post(
        "/api/rag/strategy-router/recommendations",
        json={"kb_id": kb_id, "objective": "balanced", "requirements": {}},
    )
    assert response.status_code == 200, response.text
    recommendation = response.json()
    assert recommendation["state"] == "insufficient_data"
    assert recommendation["profiles"] == []
    assert recommendation["insufficient_reasons"]

    apply_response = await client.post(
        f"/api/rag/strategy-router/recommendations/{recommendation['recommendation_id']}/apply",
        json={"expected_draft_version": 1},
    )
    assert apply_response.status_code == 400


@pytest.mark.asyncio
async def test_strategy_router_is_deterministic_and_hash_safe(
    router_client,
) -> None:
    client, _ = router_client
    kb_id = await _create_kb(client, "deterministic strategy")
    await _upload_text(client, kb_id, _long_policy_text())
    request = {
        "kb_id": kb_id,
        "objective": "balanced",
        "requirements": {
            "exact_terms": True,
            "citation_precision": True,
        },
    }

    first = await client.post("/api/rag/strategy-router/recommendations", json=request)
    second = await client.post("/api/rag/strategy-router/recommendations", json=request)
    assert first.status_code == second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["snapshot_hash"] == second_payload["snapshot_hash"]
    assert (
        first_payload["recommendation_checksum"]
        == second_payload["recommendation_checksum"]
    )
    assert first_payload["profiles"][0]["retrieval"]["mode"] == "fulltext"
    assert first_payload["profiles"][0]["retrieval"]["score_threshold"] == 0.0
    assert first_payload["profiles"][0]["retrieval"]["rerank_enabled"] is False

    semantic_only = await client.post(
        "/api/rag/strategy-router/recommendations",
        json={
            "kb_id": kb_id,
            "objective": "quality",
            "requirements": {"semantic_rewrite": True, "cross_language": True},
        },
    )
    assert semantic_only.status_code == 200, semantic_only.text
    assert semantic_only.json()["state"] == "insufficient_data"
    assert "real embedding provider" in " ".join(
        semantic_only.json()["insufficient_reasons"]
    )


@pytest.mark.asyncio
async def test_low_confidence_apply_updates_only_draft_and_saved_graph(
    router_client,
) -> None:
    client, service = router_client
    kb_id = await _create_kb(client, "long context strategy")
    await _upload_text(client, kb_id, _long_policy_text())

    graph_response = await client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")
    assert graph_response.status_code == 200, graph_response.text
    graph = graph_response.json()
    save_response = await client.put(
        f"/api/rag/pipeline/graph/{kb_id}",
        json={"expected_revision": graph["graph_revision"], "graph": graph["graph"]},
    )
    assert save_response.status_code == 200, save_response.text
    saved_graph_revision = save_response.json()["graph_revision"]
    active_before = service._read_metadata()["pipeline_active_versions"].get(kb_id)

    response = await client.post(
        "/api/rag/strategy-router/recommendations",
        json={
            "kb_id": kb_id,
            "objective": "quality",
            "requirements": {"long_context": True, "citation_precision": True},
        },
    )
    assert response.status_code == 200, response.text
    recommendation = response.json()
    primary = recommendation["profiles"][0]
    assert primary["chunker"]["strategy"] == "parent_child"
    assert primary["confidence"] == "low"

    unconfirmed = await client.post(
        f"/api/rag/strategy-router/recommendations/{recommendation['recommendation_id']}/apply",
        json={"expected_draft_version": recommendation["draft_version"]},
    )
    assert unconfirmed.status_code == 400

    applied = await client.post(
        f"/api/rag/strategy-router/recommendations/{recommendation['recommendation_id']}/apply",
        json={
            "expected_draft_version": recommendation["draft_version"],
            "profile_id": "primary",
            "confirm_low_confidence": True,
        },
    )
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["recommendation"]["state"] == "applied"
    assert payload["draft"]["version"] == recommendation["draft_version"] + 1
    chunker = next(
        stage for stage in payload["draft"]["stages"] if stage["id"] == "stage_chunker"
    )
    assert chunker["config"]["strategy"] == "parent_child"

    graph_after = await client.get(f"/api/rag/pipeline/graph?kb_id={kb_id}")
    assert graph_after.status_code == 200, graph_after.text
    graph_payload = graph_after.json()
    assert graph_payload["graph_revision"] == saved_graph_revision + 1
    assert graph_payload["compiled_draft_version"] == payload["draft"]["version"]
    assert service._read_metadata()["pipeline_active_versions"].get(kb_id) == active_before


@pytest.mark.asyncio
async def test_strategy_router_rejects_stale_draft_and_hides_sensitive_data(
    router_client,
) -> None:
    client, _ = router_client
    kb_id = await _create_kb(client, "stale strategy")
    secret_marker = "PRIVATE-CORPUS-TEXT-DO-NOT-RETURN"
    await _upload_text(client, kb_id, _long_policy_text() + secret_marker)

    response = await client.post(
        "/api/rag/strategy-router/recommendations",
        json={
            "kb_id": kb_id,
            "objective": "balanced",
            "requirements": {"exact_terms": True},
        },
    )
    assert response.status_code == 200, response.text
    recommendation = response.json()
    serialized = json.dumps(recommendation, ensure_ascii=False)
    assert secret_marker not in serialized
    assert "stored_path" not in serialized
    assert "OPENROUTER_API_KEY" not in serialized

    draft = await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")
    assert draft.status_code == 200, draft.text
    changed = await client.patch(
        f"/api/rag/pipeline/draft/{kb_id}",
        json={
            "retrieval_profile": {
                **draft.json()["retrieval_profile"],
                "top_k": 7,
            }
        },
    )
    assert changed.status_code == 200, changed.text

    detail = await client.get(
        f"/api/rag/strategy-router/recommendations/{recommendation['recommendation_id']}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["state"] == "stale"

    apply_response = await client.post(
        f"/api/rag/strategy-router/recommendations/{recommendation['recommendation_id']}/apply",
        json={"expected_draft_version": changed.json()["version"]},
    )
    assert apply_response.status_code == 409

    listing = await client.get(
        f"/api/rag/strategy-router/recommendations?kb_id={kb_id}"
    )
    assert listing.status_code == 200, listing.text
    assert listing.json()["recommendation_count"] == 1
    assert listing.json()["recommendations"][0]["state"] == "stale"


@pytest.mark.asyncio
async def test_strategy_router_preserves_short_corpus_chunker_config(
    router_client,
) -> None:
    client, _ = router_client
    kb_id = await _create_kb(client, "short structured strategy")
    await _upload_text(
        client,
        kb_id,
        ("# Terms\n\nTERM-2026 remains active.\n\n" * 30),
        filename="short.md",
    )
    draft = await client.get(f"/api/rag/pipeline/draft?kb_id={kb_id}")
    assert draft.status_code == 200, draft.text
    original = next(
        stage
        for stage in draft.json()["stages"]
        if stage["id"] == "stage_chunker"
    )["config"]

    response = await client.post(
        "/api/rag/strategy-router/recommendations",
        json={
            "kb_id": kb_id,
            "objective": "balanced",
            "requirements": {"exact_terms": True},
        },
    )

    assert response.status_code == 200, response.text
    chunker = response.json()["profiles"][0]["chunker"]
    assert chunker == original
    assert "config" not in chunker
