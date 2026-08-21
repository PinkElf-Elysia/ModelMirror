from __future__ import annotations

import hashlib
import json
import sqlite3
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
    SQLiteRouterRepository,
    configure_model_router,
    get_model_router_service,
)
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.provider_chat import PROVIDER_CHAT_CONTRACT_VERSION


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _service(tmp_path: Path) -> tuple[ModelRouterService, str]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="newAPI",
            kind="newapi",
            base_url="https://newapi.example/v1",
            api_key="canary-secret",
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-20T00:00:00+00:00",
    )
    certification, _ = repository.claim_chat_certification(
        "local",
        certification_id="cert-chat",
        connection_id=connection.id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection.id
        ),
        contract_version=PROVIDER_CHAT_CONTRACT_VERSION,
        requested_model="provider/model",
        idempotency_key_hash=hashlib.sha256(b"cert-chat").hexdigest(),
    )
    repository.complete_chat_certification(
        "local",
        str(certification["id"]),
        status="passed",
        checks={"terminal_observed": True},
        warning_codes=[],
    )
    repository.save_chat_canary_policy(
        "local", connection_id=connection.id, enabled=True
    )
    return (
        ModelRouterService(
            repository,
            egress_policy=ProviderEgressPolicy(
                resolver=lambda _host, _port: ["8.8.8.8", "1.1.1.1"]
            ),
        ),
        connection.id,
    )


class _FakeResponse:
    def __init__(
        self,
        request: object,
        *,
        status_code: int,
        chunks: list[str],
        body: bytes = b"",
    ) -> None:
        self.request = request
        self.status_code = status_code
        self.headers = {"content-type": "text/event-stream"}
        self._chunks = chunks
        self._body = body
        self.closed = False

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return self._body

    async def aclose(self) -> None:
        self.closed = True


def _fake_client(
    sent: list[dict[str, Any]],
    *,
    status_code: int = 200,
    chunks: list[str] | None = None,
    body: bytes = b"",
):
    stream_chunks = chunks or [
        'data: {"model":"provider/model","choices":[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n',
        "data: [DONE]\n\n",
    ]

    class FakeChatClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, request, *, stream, follow_redirects=False):
            assert stream is True
            assert follow_redirects is False
            sent.append(request)
            return _FakeResponse(
                request,
                status_code=status_code,
                chunks=stream_chunks,
                body=body,
            )

        async def aclose(self):
            return None

    return FakeChatClient


def _request(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model_id": "provider/model",
        "gateway": "newapi_canary",
        "messages": [{"role": "user", "content": "private user text"}],
        "routing": {"session_id": "page-session-1"},
    }
    payload.update(updates)
    return payload


@pytest.mark.asyncio
async def test_canary_stream_uses_one_pinned_post_and_stores_no_text(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, connection_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", _fake_client(sent))
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert len(sent) == 1
    pinned_hosts = {
        host for host in ("8.8.8.8", "1.1.1.1") if host in sent[0]["url"]
    }
    assert len(pinned_hosts) == 1
    assert sent[0]["headers"]["Host"] == "newapi.example"
    assert sent[0]["extensions"]["sni_hostname"] == "newapi.example"
    assert response.text.count("event: route_receipt") == 1
    assert response.text.index("event: route_receipt") < response.text.index(
        "data: [DONE]"
    )
    receipt_event = next(
        event
        for event in response.text.split("\n\n")
        if event.startswith("event: route_receipt")
    )
    receipt = json.loads(receipt_event.split("data:", 1)[1].strip())
    assert receipt["engine"] == "newapi_canary"
    assert receipt["strategy"] == "explicit_session"
    assert receipt["tokens"]["total"] == 3
    assert "connection_id" not in receipt
    rows = service.repository.list_chat_canary_runs("local")
    assert len(rows) == 1
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["dispatched"] == 1
    assert rows[0]["total_tokens"] == 3
    assert rows[0]["connection_id"] == connection_id
    with sqlite3.connect(service.repository.database_path) as database:
        dump = "\n".join(database.iterdump())
    assert "private user text" not in dump
    assert "OK" not in dump
    assert "canary-secret" not in response.text


@pytest.mark.asyncio
async def test_canary_http_failure_never_posts_to_default_and_pauses(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _connection_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    secret_body = b"upstream-secret-body"
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "LLM_GATEWAY_URL", "https://default.example/v1")
    monkeypatch.setattr(main_module, "LLM_GATEWAY_KEY", "default-secret")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        _fake_client(sent, status_code=401, body=secret_body),
    )
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 401
    assert len(sent) == 1
    assert "default.example" not in sent[0]["url"]
    assert "upstream-secret-body" not in response.text
    row = service.repository.list_chat_canary_runs("local")[0]
    assert row["result_class"] == "hard_failure"
    assert row["error_code"] == "provider_chat_http_401"
    from server.model_router.chat_canary import ProviderChatCanaryService

    status = ProviderChatCanaryService(service).public_status("provider/model")
    assert status.available is False
    assert status.reason_code == "automatically_paused"


@pytest.mark.asyncio
async def test_canary_timeout_is_attributed_to_managed_canary_only(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_service = get_model_router_service()
    service, _connection_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []

    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, request, *, stream, follow_redirects=False):
            sent.append(request)
            raise httpx.ReadTimeout("canary timeout")

        async def aclose(self):
            return None

    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", TimeoutClient)
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 504
    assert len(sent) == 1
    assert "Managed chat canary timed out" in caplog.text
    assert "OpenRouter request timed out" not in caplog.text
    row = service.repository.list_chat_canary_runs("local")[0]
    assert row["result_class"] == "transient_failure"
    assert row["error_code"] == "provider_chat_timeout"


@pytest.mark.asyncio
async def test_invalid_sse_is_forwarded_once_then_hard_pauses_without_replay(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _connection_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        _fake_client(sent, chunks=["data: not-json\n\n"]),
    )
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert "provider_chat_invalid_sse" in response.text
    assert response.text.count("data: [DONE]") == 1
    row = service.repository.list_chat_canary_runs("local")[0]
    assert row["result_class"] == "hard_failure"
    assert row["error_code"] == "provider_chat_invalid_sse"


@pytest.mark.asyncio
async def test_unsupported_canary_shape_fails_before_any_client_or_post(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _connection_id = _service(tmp_path)
    configure_model_router(service)
    created = 0

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            nonlocal created
            created += 1

    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", ForbiddenClient)
    try:
        response = await client.post(
            "/api/chat", json=_request(tool_mode="mcp_tools")
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 422
    assert response.json()["code"] == "provider_chat_canary_request_unsupported"
    assert created == 0
    assert service.repository.list_chat_canary_runs("local") == []


@pytest.mark.asyncio
async def test_preflight_failure_uses_default_before_canary_post_and_records_fallback(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, connection_id = _service(tmp_path)
    service.repository.save_chat_canary_policy(
        "local", connection_id=connection_id, enabled=False
    )
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "LLM_GATEWAY_URL", "https://default.example/v1")
    monkeypatch.setattr(main_module, "LLM_GATEWAY_KEY", "default-secret")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", _fake_client(sent))
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0]["url"] == "https://default.example/v1/chat/completions"
    assert response.text.count("event: route_receipt") == 1
    receipt_event = next(
        event
        for event in response.text.split("\n\n")
        if event.startswith("event: route_receipt")
    )
    receipt = json.loads(receipt_event.split("data:", 1)[1].strip())
    assert receipt["engine"] == "newapi_canary_fallback"
    assert receipt["reason_codes"] == ["policy_disabled"]
    row = service.repository.list_chat_canary_runs("local")[0]
    assert row["status"] == "preflight_fallback"
    assert row["dispatched"] == 0
    assert row["error_code"] == "policy_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "error_code"),
    [
        (["data: [DONE]\n\n"], "provider_chat_empty_stream"),
        (
            ['data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'],
            "provider_chat_missing_terminal",
        ),
    ],
)
async def test_incomplete_canary_stream_hard_pauses_without_replay(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[str],
    error_code: str,
) -> None:
    original_service = get_model_router_service()
    service, _connection_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        _fake_client(sent, chunks=chunks),
    )
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert error_code in response.text
    row = service.repository.list_chat_canary_runs("local")[0]
    assert row["result_class"] == "hard_failure"
    assert row["error_code"] == error_code


@pytest.mark.asyncio
async def test_three_transient_http_failures_pause_exact_model(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _connection_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        _fake_client(sent, status_code=503),
    )
    try:
        responses = [
            await client.post(
                "/api/chat",
                json=_request(routing={"session_id": f"page-session-{index}"}),
            )
            for index in range(3)
        ]
    finally:
        configure_model_router(original_service)

    assert [response.status_code for response in responses] == [503, 503, 503]
    assert len(sent) == 3
    from server.model_router.chat_canary import ProviderChatCanaryService

    status = ProviderChatCanaryService(service).public_status("provider/model")
    assert status.available is False
    assert status.reason_code == "automatically_paused"
