from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.main import app


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_batch_submission_uses_base_model_and_text_only_body(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/beta/batches"
        assert request.headers["Authorization"] == "Bearer batch-secret"
        payload = json.loads(request.content)
        captured.update(payload)
        assert list(payload) == ["endpoint", "model", "requests"]
        assert payload["model"] == "google/gemini-2.5-flash"
        assert payload["requests"] == [
            {
                "custom_id": "request-1",
                "body": {
                    "model": "google/gemini-2.5-flash",
                    "messages": [
                        {"role": "user", "content": "Summarize this text."}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 512,
                },
            }
        ]
        return httpx.Response(
            202,
            json={
                "id": "batch_123",
                "object": "batch",
                "endpoint": payload["endpoint"],
                "model": payload["model"],
                "completion_window": "24h",
                "status": "validating",
                "created_at": 1,
                "finalized_at": None,
                "request_counts": {"total": 1, "completed": 0, "failed": 0},
                "usage": None,
                "results": None,
                "error": None,
            },
        )

    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "batch-secret")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "openrouter_batch_client_kwargs",
        lambda: {"transport": httpx.MockTransport(handler)},
    )

    response = await client.post(
        "/api/openrouter/batches",
        json={
            "model_id": "google/gemini-2.5-flash",
            "endpoint": "/v1/chat/completions",
            "requests": [
                {"custom_id": "request-1", "input": "Summarize this text."}
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        },
    )

    assert response.status_code == 202
    assert response.json()["id"] == "batch_123"
    assert captured["model"] == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_embedding_batch_uses_base_model_and_text_input(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload == {
            "endpoint": "/v1/embeddings",
            "model": "openai/text-embedding-3-large",
            "requests": [
                {
                    "custom_id": "document-1",
                    "body": {
                        "model": "openai/text-embedding-3-large",
                        "input": "Document text",
                    },
                }
            ],
        }
        return httpx.Response(
            202,
            json={
                "id": "batch_embedding_123",
                "object": "batch",
                "endpoint": payload["endpoint"],
                "model": payload["model"],
                "completion_window": "24h",
                "status": "validating",
                "created_at": 1,
                "finalized_at": None,
                "request_counts": {"total": 1, "completed": 0, "failed": 0},
                "usage": None,
                "results": None,
                "error": None,
            },
        )

    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "batch-secret")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "openrouter_batch_client_kwargs",
        lambda: {"transport": httpx.MockTransport(handler)},
    )

    response = await client.post(
        "/api/openrouter/batches",
        json={
            "model_id": "openai/text-embedding-3-large",
            "endpoint": "/v1/embeddings",
            "requests": [
                {"custom_id": "document-1", "input": "Document text"}
            ],
        },
    )

    assert response.status_code == 202
    assert response.json()["id"] == "batch_embedding_123"


@pytest.mark.asyncio
async def test_batch_submission_rejects_catalog_variant_id_and_duplicate_ids(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "batch-secret")

    variant_response = await client.post(
        "/api/openrouter/batches",
        json={
            "model_id": "google/gemini-2.5-flash:batch",
            "endpoint": "/v1/chat/completions",
            "requests": [{"custom_id": "request-1", "input": "Hello"}],
        },
    )
    duplicate_response = await client.post(
        "/api/openrouter/batches",
        json={
            "model_id": "google/gemini-2.5-flash",
            "endpoint": "/v1/chat/completions",
            "requests": [
                {"custom_id": "same", "input": "Hello"},
                {"custom_id": "same", "input": "World"},
            ],
        },
    )

    assert variant_response.status_code == 422
    assert duplicate_response.status_code == 422


@pytest.mark.asyncio
async def test_batch_polling_proxies_completed_results(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "https://openrouter.ai/api/beta/batches/batch_123"
        return httpx.Response(
            200,
            json={
                "id": "batch_123",
                "object": "batch",
                "endpoint": "/v1/chat/completions",
                "model": "google/gemini-2.5-flash",
                "completion_window": "24h",
                "status": "completed",
                "created_at": 1,
                "finalized_at": 2,
                "request_counts": {"total": 1, "completed": 1, "failed": 0},
                "usage": {"cost": 0.00015},
                "results": [
                    {
                        "id": "batch_req_123",
                        "custom_id": "request-1",
                        "response": {
                            "status_code": 200,
                            "body": {
                                "choices": [
                                    {"message": {"content": "Finished"}}
                                ]
                            },
                        },
                        "error": None,
                    }
                ],
                "error": None,
            },
        )

    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "batch-secret")
    monkeypatch.setattr(
        main_module,
        "openrouter_batch_client_kwargs",
        lambda: {"transport": httpx.MockTransport(handler)},
    )

    response = await client.get("/api/openrouter/batches/batch_123")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["results"][0]["custom_id"] == "request-1"


@pytest.mark.asyncio
async def test_batch_requires_openrouter_specific_configuration(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "")

    response = await client.post(
        "/api/openrouter/batches",
        json={
            "model_id": "google/gemini-2.5-flash",
            "endpoint": "/v1/chat/completions",
            "requests": [{"custom_id": "request-1", "input": "Hello"}],
        },
    )

    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["error"]
