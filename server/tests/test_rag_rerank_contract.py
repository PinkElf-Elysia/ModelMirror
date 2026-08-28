from __future__ import annotations

import asyncio

import httpx
import pytest

from server.rag.reranker import RerankDocument, RerankService


@pytest.mark.asyncio
async def test_dedicated_and_llm_rerank_receipts_replay_actual_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    documents = [RerankDocument("a", "alpha"), RerankDocument("b", "beta")]
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/v1/rerank")
    monkeypatch.setenv("RERANK_API_KEY", "test-key")
    monkeypatch.setenv("RERANK_MODEL", "dedicated-reranker")

    async def dedicated_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.97}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", dedicated_post)
    dedicated = await service.rerank("beta", documents, provider="api", top_n=1)
    assert dedicated.provider == "api"
    assert dedicated.model == "dedicated-reranker"
    assert dedicated.attempted_provider == "api"
    assert dedicated.attempted_model == "dedicated-reranker"
    assert dedicated.provider_target == "rerank_api"
    assert dedicated.attempted_targets == ("rerank_api",)

    monkeypatch.delenv("RERANK_API_KEY")
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "test-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "chat-rank-judge")

    async def llm_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"results":[{"index":0,"score":0.91}]}'}}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", llm_post)
    llm = await service.rerank("alpha", documents, provider="auto", top_n=1)
    assert llm.provider == "llm"
    assert llm.model == "chat-rank-judge"
    assert llm.attempted_provider == "llm"
    assert llm.attempted_model == "chat-rank-judge"
    assert llm.provider_target in {"llm_gateway", "openrouter"}
    assert llm.provider_target in llm.attempted_targets


@pytest.mark.asyncio
async def test_invalid_response_and_timeout_fallback_are_replayable_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RerankService()
    documents = [RerankDocument("a", "alpha")]
    monkeypatch.setenv("RERANK_API_URL", "https://rerank.test/private")
    monkeypatch.setenv("RERANK_API_KEY", "secret-key")
    monkeypatch.setenv("RERANK_MODEL", "dedicated-reranker")

    async def invalid_post(self, url, **kwargs):
        return httpx.Response(
            200,
            content=b"{not-json",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", invalid_post)
    invalid = await service.rerank("q", documents, provider="api", top_n=1)
    assert invalid.provider == "none"
    assert invalid.attempted_provider == "api"
    assert invalid.attempted_model == "dedicated-reranker"
    assert invalid.fallback_reason
    assert invalid.attempted_targets == ("rerank_api",)
    assert "secret" not in f"{invalid.warning} {invalid.fallback_reason}".lower()
    assert "https://" not in f"{invalid.warning} {invalid.fallback_reason}".lower()

    monkeypatch.setenv("RAG_RERANK_TIMEOUT_SECONDS", "0.02")
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.test/v1")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "test-key")
    monkeypatch.setenv("RAG_RERANK_LLM_MODEL", "chat-rank-judge")

    async def slow_post(self, url, **kwargs):
        await asyncio.sleep(0.1)
        raise AssertionError("shared timeout must stop the route")

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)
    timeout = await service.rerank("q", documents, provider="auto", top_n=1)
    assert timeout.provider == "none"
    assert timeout.fallback_reason == "timeout_budget_exhausted"
    assert timeout.attempted_provider == "api"
    assert timeout.attempted_targets == ("rerank_api",)
    assert timeout.timeout_budget_ms == 20
