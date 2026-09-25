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
async def test_decisions_proxy_uses_dedicated_openrouter_contract(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/alpha/decisions"
        assert request.headers["Authorization"] == "Bearer decisions-secret"
        assert json.loads(request.content) == {
            "model": "typesafe/jev-1.13",
            "state": "A production alert has repeated twice.",
            "questions": {
                "priority": {
                    "type": "choice",
                    "instructions": "How should this alert be handled?",
                    "criteria": {
                        "page": "Immediate human attention is required.",
                        "ticket": "Schedule normal follow-up.",
                        "ignore": "No action is needed.",
                    },
                }
            },
        }
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13",
                "answers": {"priority": {"choice": "page", "probability": 0.92}},
                "usage": {"prompt_tokens": 17},
            },
        )

    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "decisions-secret")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "llm_client_kwargs",
        lambda: {"transport": httpx.MockTransport(handler)},
    )

    response = await client.post(
        "/api/decisions",
        json={
            "model": "typesafe/jev-1.13",
            "state": "A production alert has repeated twice.",
            "questions": {
                "priority": {
                    "type": "choice",
                    "instructions": "How should this alert be handled?",
                    "criteria": {
                        "page": "Immediate human attention is required.",
                        "ticket": "Schedule normal follow-up.",
                        "ignore": "No action is needed.",
                    },
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["answers"]["priority"]["choice"] == "page"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id",
    ["jaredpalmer/kev-4b", "~typesafe/jev-latest"],
)
async def test_decisions_accepts_supported_models(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == model_id
        return httpx.Response(200, json={"answers": {"risk": {"score": 0.4}}})

    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "decisions-secret")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "llm_client_kwargs",
        lambda: {"transport": httpx.MockTransport(handler)},
    )

    response = await client.post(
        "/api/decisions",
        json={
            "model": model_id,
            "state": "Candidate evidence",
            "questions": {
                "risk": {
                    "type": "score",
                    "instructions": "How risky is this action?",
                    "criteria": ["Low risk", "High risk"],
                }
            },
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_decisions_rejects_chat_models_and_unknown_question_types(
    client: httpx.AsyncClient,
) -> None:
    chat_model = await client.post(
        "/api/decisions",
        json={
            "model": "openai/gpt-6-astra",
            "state": "state",
            "questions": {
                "route": {
                    "type": "choice",
                    "instructions": "Where should this go?",
                    "criteria": {"one": "First route", "two": "Second route"},
                }
            },
        },
    )
    unknown_type = await client.post(
        "/api/decisions",
        json={
            "model": "typesafe/jev-1.13",
            "state": "state",
            "questions": {
                "route": {
                    "type": "chat",
                    "instructions": "Where should this go?",
                    "criteria": {"one": "First route", "two": "Second route"},
                }
            },
        },
    )
    assert chat_model.status_code == 422
    assert unknown_type.status_code == 422


@pytest.mark.asyncio
async def test_decisions_requires_openrouter_credentials(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    response = await client.post(
        "/api/decisions",
        json={
            "model": "typesafe/jev-1.13",
            "state": "state",
            "questions": {
                "route": {
                    "type": "noul",
                    "instructions": "Is this valid?",
                    "criteria": {"true": "It is valid", "false": "It is invalid"},
                }
            },
        },
    )
    assert response.status_code == 503
    assert "OPENROUTER_API_KEY" in response.json()["error"]
