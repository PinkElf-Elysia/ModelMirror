from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.main import app
from server.model_router import (
    ModelRouterService,
    RouterConnectionCreate,
    RouterPolicy,
    SQLiteRouterRepository,
    configure_model_router,
    get_model_router_service,
)


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


class CatalogClient:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _url: str, headers: dict[str, str]):
        assert headers["Authorization"] == "Bearer native-secret"
        return httpx.Response(200, json={"data": self.records})


def native_service(tmp_path: Path) -> ModelRouterService:
    records = [
        {
            "id": "provider/model-a",
            "context_length": 128000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        },
        {
            "id": "provider/model-b",
            "context_length": 128000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        },
    ]
    repository = SQLiteRouterRepository(tmp_path)
    repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Native test",
            kind="openai_compatible",
            base_url="https://native.example/v1",
            api_key="native-secret",
        ),
    )
    repository.save_policy(
        "local",
        RouterPolicy(tenant_id="local", engine="native"),
    )
    return ModelRouterService(
        repository,
        client_factory=lambda: CatalogClient(records),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_native_auto_retries_empty_stream_before_visible_output(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service = native_service(tmp_path)
    configure_model_router(service)
    sent_models: list[str] = []

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        content = b""

        def __init__(self, model_id: str) -> None:
            self.model_id = model_id

        async def aiter_text(self):
            if self.model_id.endswith("model-a"):
                yield "data: [DONE]\n\n"
                return
            yield (
                'data: {"model":"provider/model-b","choices":'
                '[{"delta":{"content":"native answer"}}]}\n\n'
            )
            yield (
                'data: {"choices":[],"usage":{"prompt_tokens":4,'
                '"completion_tokens":2,"total_tokens":6}}\n\n'
            )
            yield "data: [DONE]\n\n"

        async def aread(self):
            return self.content

        async def aclose(self):
            return None

    class FakeChatClient:
        def __init__(self, *args, **kwargs):
            pass

        def build_request(self, method, url, headers, json):
            return {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
            }

        async def send(self, request, stream):
            assert stream is True
            model_id = request["json"]["model"]
            sent_models.append(model_id)
            return FakeResponse(model_id)

        async def aclose(self):
            return None

    try:
        monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
        monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeChatClient)
        response = await client.post(
            "/api/chat",
            json={
                "model_id": "auto",
                "gateway": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "routing": {
                    "session_id": "stable-session",
                    "mode": "balanced",
                    "budget_usd": 0.01,
                    "budget_fallback": "strict",
                },
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert sent_models == ["provider/model-a", "provider/model-b"]
    assert "native answer" in response.text
    assert response.text.count("event: route_receipt") == 1
    receipt_event = next(
        event
        for event in response.text.split("\n\n")
        if event.startswith("event: route_receipt")
    )
    receipt = json.loads(
        next(
            line.removeprefix("data:").strip()
            for line in receipt_event.splitlines()
            if line.startswith("data:")
        )
    )
    assert receipt["engine"] == "native"
    assert receipt["actual_model"] == "provider/model-b"
    assert receipt["fallback_attempts"] == 1
    assert receipt["tokens"]["total"] == 6
    assert receipt["response_cost_usd"] == pytest.approx(0.000008)
    assert receipt["cost_kind"] == "actual"
    assert receipt["budget"]["status"] == "settled"
    assert receipt["version"] == "2"
    assert response.text.rstrip().endswith("data: [DONE]")
    diagnostics = service.diagnostics()
    assert diagnostics["recent_decisions"][0]["budget"]["status"] == "settled"
    assert diagnostics["recent_decisions"][0]["budget"][
        "settled_cost_usd"
    ] == pytest.approx(0.000008)


def test_native_canary_assignment_is_stable() -> None:
    from server.model_router.engine import NativeRouterEngine

    first = NativeRouterEngine.stable_canary_selected("session-a", 50)
    assert all(
        NativeRouterEngine.stable_canary_selected("session-a", 50) == first
        for _ in range(20)
    )
    assert NativeRouterEngine.stable_canary_selected("session-a", 0) is False
    assert NativeRouterEngine.stable_canary_selected("session-a", 100) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "expected_outcome", "expect_error", "expect_output_limit"),
    [
        (
            [
                (
                    'data: {"model":"provider/model-a","choices":'
                    '[{"delta":{"content":"partial answer"},'
                    '"finish_reason":null}]}\n\n'
                ),
            ],
            "stream_interrupted",
            True,
            False,
        ),
        (
            [
                (
                    'data: {"model":"provider/model-a","choices":'
                    '[{"delta":{"content":"limited answer"},'
                    '"finish_reason":null}]}\n\n'
                ),
                (
                    'data: {"choices":[{"delta":{"content":""},'
                    '"finish_reason":"length"}],"usage":{"prompt_tokens":4,'
                    '"completion_tokens":8,"total_tokens":12}}\n\n'
                ),
                "data: [DONE]\n\n",
            ],
            "output_limit",
            False,
            True,
        ),
    ],
)
async def test_native_stream_requires_a_terminal_marker_and_reports_output_limit(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[str],
    expected_outcome: str,
    expect_error: bool,
    expect_output_limit: bool,
) -> None:
    original_service = get_model_router_service()
    service = native_service(tmp_path)
    configure_model_router(service)

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        content = b""

        async def aiter_text(self):
            for chunk in chunks:
                yield chunk

        async def aread(self):
            return self.content

        async def aclose(self):
            return None

    class FakeChatClient:
        def __init__(self, *args, **kwargs):
            pass

        def build_request(self, method, url, headers, json):
            return {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
            }

        async def send(self, request, stream):
            assert stream is True
            return FakeResponse()

        async def aclose(self):
            return None

    try:
        monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
        monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeChatClient)
        response = await client.post(
            "/api/chat",
            json={
                "model_id": "auto",
                "gateway": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "routing": {
                    "session_id": "stream-completion-session",
                    "mode": "fast",
                },
                "compression": {"mode": "off"},
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert response.text.count("event: route_receipt") == 1
    assert response.text.rstrip().endswith("data: [DONE]")
    assert ('"error":' in response.text) is expect_error
    receipt_event = next(
        event
        for event in response.text.split("\n\n")
        if event.startswith("event: route_receipt")
    )
    receipt = json.loads(
        next(
            line.removeprefix("data:").strip()
            for line in receipt_event.splitlines()
            if line.startswith("data:")
        )
    )
    assert (
        "output_limit_reached" in receipt["reason_codes"]
    ) is expect_output_limit
    assert service.diagnostics()["recent_decisions"][0]["outcome"] == (
        expected_outcome
    )
