from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from server.main import app


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_environment_summary_returns_stable_redacted_shape(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/runtime/environment-summary")

    assert response.status_code == 200, response.text
    payload = response.json()

    expected_boolean_fields = {
        "llm_gateway_configured",
        "openrouter_configured",
        "model_gateway_ready",
        "git_available",
        "node_available",
        "npm_available",
        "npx_available",
        "python_available",
        "redacted",
    }
    assert expected_boolean_fields.issubset(payload.keys())
    assert all(isinstance(payload[field], bool) for field in expected_boolean_fields)
    assert isinstance(payload["updated_at"], (int, float))
    assert payload["redacted"] is True


@pytest.mark.asyncio
async def test_environment_summary_does_not_expose_secret_material(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/runtime/environment-summary")

    assert response.status_code == 200, response.text
    serialized = json.dumps(response.json(), ensure_ascii=False)

    assert "sk-" not in serialized
    assert ("OPENROUTER" + "_API_KEY") not in serialized
    assert ("LLM_GATEWAY" + "_KEY") not in serialized
