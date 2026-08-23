from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from server import main as main_module
from server.file_assets.document_parser import ParsedDocument, ParsedSection
from server.file_assets.service import ResolvedChatFile
from server.main import app
from server.model_router import (
    ModelRouterService,
    RouterConnectionCreate,
    SQLiteRouterRepository,
    configure_model_router,
    get_model_router_service,
)
from server.model_router.chat_control import ProviderChatControlService
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.schemas import (
    ProviderChatControlPolicyUpdate,
    ProviderChatControlRouteUpdate,
    RouterConnectionUpdate,
)


MODEL_ID = "provider/model"


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


def _qualified_connection(
    repository: SQLiteRouterRepository,
    *,
    name: str,
    kind: str,
    capabilities: tuple[str, ...] = ("chat_text",),
) -> str:
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name=name,
            kind=kind,
            base_url=f"https://{name.casefold()}.example/v1",
            api_key=f"{name}-secret",
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-21T00:00:00+00:00",
    )
    fingerprint = repository.connection_config_fingerprint("local", connection.id)
    refresh_id = f"refresh-{connection.id}"
    repository.claim_catalog_refresh(
        "local",
        refresh_id=refresh_id,
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        refresh_id,
        connection_id=connection.id,
        models=[
            {
                "model_id": MODEL_ID,
                "normalized_model_id": MODEL_ID,
                "capability_state": "declared",
            }
        ],
        offerings=[],
        model_count=1,
        truncated=False,
        catalog_fingerprint=f"catalog-{connection.id}",
        observed_at="2026-08-21T00:00:00+00:00",
    )
    for capability in capabilities:
        certification, created = repository.claim_chat_certification(
            "local",
            certification_id=f"cert-{capability}-{connection.id}",
            connection_id=connection.id,
            connection_fingerprint=fingerprint,
            contract_version="modelmirror-provider-chat-v1",
            capability=capability,
            requested_model=MODEL_ID,
            idempotency_key_hash=hashlib.sha256(
                f"{capability}:{connection.id}".encode()
            ).hexdigest(),
        )
        assert created is True
        repository.complete_chat_certification(
            "local",
            str(certification["id"]),
            status="passed",
            checks={"capability_verified": True},
            warning_codes=[],
            actual_model=MODEL_ID,
        )
    return connection.id


def _service(
    tmp_path: Path,
    *,
    block_primary: bool = False,
    capabilities: tuple[str, ...] = ("chat_text",),
) -> tuple[ModelRouterService, SQLiteRouterRepository, str, str]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)

    def resolve(host: str, _port: int):
        if block_primary and host.startswith("newapi"):
            return ["10.0.0.8"]
        return ["8.8.8.8"]

    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(resolver=resolve),
    )
    newapi_id = _qualified_connection(
        repository,
        name="newAPI",
        kind="newapi",
        capabilities=capabilities,
    )
    backup_id = _qualified_connection(
        repository,
        name="OpenRouter",
        kind="openrouter",
        capabilities=capabilities,
    )
    ProviderChatControlService(service).update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="newapi_preferred",
            stable_model_ids=[MODEL_ID],
            routes=[
                ProviderChatControlRouteUpdate(
                    capability=capability,
                    connection_ids=[newapi_id, backup_id],
                )
                for capability in capabilities
            ],
        )
    )
    return service, repository, newapi_id, backup_id


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        chunks: list[str],
        body: bytes = b"",
        stream_error: bool = False,
    ):
        self.status_code = status_code
        self.headers = {
            "content-type": "text/event-stream",
            "x-request-id": "request-1",
        }
        self._chunks = chunks
        self._body = body
        self._stream_error = stream_error
        self.closed = False

    async def aiter_text(self):
        for index, chunk in enumerate(self._chunks):
            yield chunk
            if self._stream_error and index == 0:
                raise httpx.ReadError("stream interrupted")

    async def aread(self) -> bytes:
        return self._body

    async def aclose(self) -> None:
        self.closed = True


def _fake_client(
    sent: list[dict[str, Any]],
    *,
    status_code: int = 200,
    send_error: Exception | None = None,
    stream_error: bool = False,
):
    chunks = [
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"OK"}},"finish_reason":null}}]}}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n',
        "data: [DONE]\n\n",
    ]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, request, *, stream, follow_redirects=False):
            assert stream is True
            sent.append(request)
            if send_error is not None:
                raise send_error
            return _FakeResponse(
                status_code=status_code,
                chunks=chunks if status_code < 400 else [],
                body=b"upstream private error",
                stream_error=stream_error,
            )

        async def aclose(self):
            return None

    return FakeClient


def _request() -> dict[str, object]:
    return {
        "model_id": MODEL_ID,
        "gateway": "default",
        "messages": [{"role": "user", "content": "private user text"}],
    }


class _ResolvedFileService:
    def __init__(self) -> None:
        self.resolved = ResolvedChatFile(
            asset_id="file_" + "a" * 32,
            scope_id="chat-session-1",
            display_name="notes.md",
            format_id="markdown",
            media_type="text/markdown",
            byte_size=20,
            handling="extract",
            parsed_document=ParsedDocument(
                format="markdown",
                title="notes.md",
                sections=(ParsedSection(text="untrusted file text", page=1),),
                extracted_chars=19,
            ),
        )
        self.finalized: list[bool] = []

    def resolve_chat_inputs(
        self, _selections, *, scope_id: str, native_pdf_verified: bool = False
    ):
        assert scope_id == "chat-session-1"
        assert native_pdf_verified is False
        return (self.resolved,)

    def finalize_chat_inputs(self, _files, *, success: bool) -> bool:
        self.finalized.append(success)
        return success


def _disable_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable_runtime():
        raise RuntimeError("runtime unavailable in routing test")

    monkeypatch.setattr(main_module, "create_default_runtime", unavailable_runtime)
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))


@pytest.mark.asyncio
async def test_preferred_text_chat_uses_one_pinned_newapi_post_and_receipt(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, newapi_id, _backup_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", _fake_client(sent))
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert len(sent) == 1
    assert sent[0]["url"] == "https://8.8.8.8/v1/chat/completions"
    assert sent[0]["headers"]["Host"] == "newapi.example"
    assert sent[0]["extensions"]["sni_hostname"] == "newapi.example"
    assert response.text.count("event: route_receipt") == 1
    assert response.text.index("event: route_receipt") < response.text.index(
        "data: [DONE]"
    )
    receipt_event = next(
        item
        for item in response.text.split("\n\n")
        if item.startswith("event: route_receipt")
    )
    receipt = json.loads(receipt_event.split("data:", 1)[1].strip())
    assert receipt["engine"] == "newapi"
    assert receipt["strategy"] == "newapi_preferred"
    assert receipt["tokens"]["total"] == 3
    assert "connection_id" not in receipt
    stored = repository.list_chat_control_receipts("local")
    assert stored["runs"][0]["status"] == "succeeded"
    assert stored["attempts"][0]["connection_id"] == newapi_id
    assert stored["attempts"][0]["dispatched"] == 1
    with sqlite3.connect(repository.database_path) as database:
        receipt_rows = repr(
            {
                "runs": database.execute(
                    "SELECT * FROM provider_chat_runs"
                ).fetchall(),
                "attempts": database.execute(
                    "SELECT * FROM provider_chat_attempts"
                ).fetchall(),
            }
        )
    assert "private user text" not in receipt_rows
    assert "OK" not in receipt_rows


@pytest.mark.asyncio
async def test_preferred_accepts_only_confirmed_text_extracted_file_path(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    file_service = _ResolvedFileService()
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(main_module, "get_file_asset_service", lambda: file_service)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", _fake_client(sent))
    payload = {
        "model_id": MODEL_ID,
        "gateway": "default",
        "file_scope_id": "chat-session-1",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "summarize"},
                    {
                        "type": "input_file",
                        "asset_id": "file_" + "a" * 32,
                        "handling": "extract",
                        "confirmation_revision": 1,
                    },
                ],
            }
        ],
    }
    try:
        response = await client.post("/api/chat", json=payload)
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert len(sent) == 1
    serialized_messages = json.dumps(sent[0]["json"]["messages"], ensure_ascii=False)
    assert "untrusted file text" in serialized_messages
    assert "不可信的用户数据" in serialized_messages
    assert "input_file" not in serialized_messages
    assert response.text.count("event: route_receipt") == 1
    assert response.text.count("event: message_end") == 1
    receipt_event = next(
        item
        for item in response.text.split("\n\n")
        if item.startswith("event: route_receipt")
    )
    receipt = json.loads(receipt_event.split("data:", 1)[1].strip())
    assert receipt["engine"] == "newapi"
    assert receipt["files"]["handling"] == "extract"
    assert file_service.finalized == [True]
    with sqlite3.connect(repository.database_path) as database:
        dump = "\n".join(database.iterdump())
    assert "untrusted file text" not in dump


@pytest.mark.asyncio
async def test_preferred_http_failure_never_posts_backup_or_legacy(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, backup_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(
        main_module.httpx, "AsyncClient", _fake_client(sent, status_code=503)
    )
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 503
    assert len(sent) == 1
    assert "newapi.example" == sent[0]["headers"]["Host"]
    assert "upstream private error" not in response.text
    payload = response.json()
    assert payload["code"] == "provider_chat_http_503"
    assert payload["route_receipt"]["engine"] == "newapi"
    attempts = repository.list_chat_control_receipts("local")["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["dispatched"] == 1
    assert attempts[0]["connection_id"] != backup_id


@pytest.mark.asyncio
async def test_preferred_connect_failure_after_dispatch_never_replays(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        _fake_client(sent, send_error=httpx.ConnectError("connect failed")),
    )
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 502
    assert len(sent) == 1
    payload = response.json()
    assert payload["code"] == "provider_chat_transport_error"
    assert payload["route_receipt"]["engine"] == "newapi"
    receipts = repository.list_chat_control_receipts("local")
    assert len(receipts["attempts"]) == 1
    assert receipts["attempts"][0]["dispatched"] == 1
    assert receipts["attempts"][0]["result_class"] == "transient_failure"


@pytest.mark.asyncio
async def test_preferred_stream_interruption_records_failure_without_replay(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        _fake_client(sent, stream_error=True),
    )
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert "provider_chat_stream_interrupted" in response.text
    assert response.text.count("event: route_receipt") == 1
    assert response.text.count("data: [DONE]") == 1
    receipts = repository.list_chat_control_receipts("local")
    assert len(receipts["attempts"]) == 1
    assert receipts["attempts"][0]["dispatched"] == 1
    assert receipts["attempts"][0]["result_class"] == "transient_failure"


@pytest.mark.asyncio
async def test_preflight_failure_selects_explicit_backup_before_only_post(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, backup_id = _service(
        tmp_path, block_primary=True
    )
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", _fake_client(sent))
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0]["headers"]["Host"] == "openrouter.example"
    receipt_event = next(
        item
        for item in response.text.split("\n\n")
        if item.startswith("event: route_receipt")
    )
    receipt = json.loads(receipt_event.split("data:", 1)[1].strip())
    assert receipt["engine"] == "openrouter"
    assert receipt["fallback_attempts"] == 1
    assert "provider_chat_preflight_backup_selected" in receipt["reason_codes"]
    attempts = repository.list_chat_control_receipts("local")["attempts"]
    assert len(attempts) == 2
    selected = next(item for item in attempts if item["connection_id"] == backup_id)
    assert selected["dispatched"] == 1


@pytest.mark.asyncio
async def test_disabled_flag_preserves_legacy_gateway_bytes_and_no_receipt(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(tmp_path)
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://legacy.example/v1/chat/completions", "legacy-secret"),
    )
    monkeypatch.setattr(main_module.httpx, "AsyncClient", _fake_client(sent))
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://legacy.example/v1/chat/completions", "legacy-secret"),
    )
    original_get_bundle = repository.get_chat_control_policy_bundle
    repository.get_chat_control_policy_bundle = lambda _tenant: (_ for _ in ()).throw(
        AssertionError("disabled feature must not read the control policy")
    )
    try:
        response = await client.post("/api/chat", json=_request())
    finally:
        repository.get_chat_control_policy_bundle = original_get_bundle
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0]["url"] == "https://legacy.example/v1/chat/completions"
    assert "event: route_receipt" not in response.text
    assert repository.list_chat_control_receipts("local")["runs"] == []


@pytest.mark.asyncio
async def test_preferred_tool_mode_uses_qualified_managed_route_and_receipt(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, newapi_id, _backup_id = _service(
        tmp_path, capabilities=("chat_text", "chat_tools")
    )
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, request, *, stream, follow_redirects=False):
            sent.append({**request, "stream": stream})
            return _FakeResponse(status_code=200, chunks=[])

        async def aclose(self):
            return None

    async def fake_tool_stream(_payload, **kwargs):
        assert kwargs["client_kwargs_override"]["trust_env"] is False
        sender = kwargs["response_sender"]
        assert sender is not None
        response = await sender(
            FakeClient(),
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "private tool text"}],
            },
        )
        assert response.status_code == 200
        kwargs["actual_model_observer"](MODEL_ID)
        kwargs["usage_observer"](
            {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        )
        yield "managed tool final"

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(main_module, "stream_chat_toolset_text", fake_tool_stream)
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": MODEL_ID,
                "gateway": "default",
                "messages": [{"role": "user", "content": "private user text"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert len(sent) == 1
    assert sent[0]["stream"] is False
    assert sent[0]["url"] == "https://8.8.8.8/v1/chat/completions"
    assert sent[0]["headers"]["Host"] == "newapi.example"
    receipt_event = next(
        item
        for item in response.text.split("\n\n")
        if item.startswith("event: route_receipt")
    )
    receipt = json.loads(receipt_event.split("data:", 1)[1].strip())
    assert receipt["engine"] == "newapi"
    assert "provider_chat_chat_tools_managed" in receipt["reason_codes"]
    stored = repository.list_chat_control_receipts("local")
    assert stored["runs"][0]["capability"] == "chat_tools"
    assert stored["attempts"][0]["connection_id"] == newapi_id
    assert stored["attempts"][0]["dispatched"] == 1
    with sqlite3.connect(repository.database_path) as database:
        receipt_rows = repr(
            {
                "runs": database.execute(
                    "SELECT * FROM provider_chat_runs"
                ).fetchall(),
                "attempts": database.execute(
                    "SELECT * FROM provider_chat_attempts"
                ).fetchall(),
            }
        )
    assert "private user text" not in receipt_rows
    assert "private tool text" not in receipt_rows


@pytest.mark.asyncio
async def test_preferred_tool_http_hard_failure_is_classified_and_sanitized(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(
        tmp_path, capabilities=("chat_text", "chat_tools")
    )
    configure_model_router(service)
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    private_error = "upstream-private-error-marker"

    class FakeClient:
        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, _request, *, stream, follow_redirects=False):
            return _FakeResponse(status_code=401, chunks=[])

    async def failing_tool_stream(_payload, **kwargs):
        await kwargs["response_sender"](
            FakeClient(), {"model": MODEL_ID, "messages": []}
        )
        raise main_module.ChatCompletionUpstreamError(401, private_error)
        yield "must not be reached"

    monkeypatch.setattr(main_module, "stream_chat_toolset_text", failing_tool_stream)
    caplog.set_level("WARNING")
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": MODEL_ID,
                "gateway": "default",
                "messages": [{"role": "user", "content": "private text"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert "provider_chat_http_401" in response.text
    assert private_error not in response.text
    assert private_error not in caplog.text
    run_id = response.headers["x-modelmirror-runtime-run-id"]
    checkpoints = await main_module.run_registry.list_checkpoints(run_id)
    assert private_error not in repr(checkpoints)
    receipts = repository.list_chat_control_receipts("local")
    assert receipts["runs"][0]["hard_failure"] == 1
    assert receipts["runs"][0]["result_class"] == "hard_failure"
    assert receipts["attempts"][0]["error_code"] == "provider_chat_http_401"


@pytest.mark.asyncio
async def test_preferred_tool_client_cancel_finalizes_receipt_without_done_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(
        tmp_path, capabilities=("chat_text", "chat_tools")
    )
    configure_model_router(service)
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))

    class FakeClient:
        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, _request, *, stream, follow_redirects=False):
            return _FakeResponse(status_code=200, chunks=[])

    async def cancelled_tool_stream(_payload, **kwargs):
        await kwargs["response_sender"](
            FakeClient(), {"model": MODEL_ID, "messages": []}
        )
        raise asyncio.CancelledError
        yield "must not be reached"

    monkeypatch.setattr(main_module, "stream_chat_toolset_text", cancelled_tool_stream)
    try:
        request = main_module.Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/chat",
                "raw_path": b"/api/chat",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
            }
        )
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(
                {
                    "model_id": MODEL_ID,
                    "gateway": "default",
                    "messages": [{"role": "user", "content": "private text"}],
                    "tool_mode": "mcp_tools",
                    "tool_names": "fetch",
                }
            ),
            request,
        )
        with pytest.raises(asyncio.CancelledError):
            await anext(response.body_iterator)
    finally:
        configure_model_router(original_service)

    receipts = repository.list_chat_control_receipts("local")
    assert receipts["runs"][0]["status"] == "cancelled"
    assert receipts["runs"][0]["client_cancelled"] == 1
    assert receipts["attempts"][0]["status"] == "cancelled"
    assert receipts["attempts"][0]["dispatched"] == 1


@pytest.mark.asyncio
async def test_preferred_tool_mode_blocks_second_step_after_policy_drift(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, newapi_id, backup_id = _service(
        tmp_path, capabilities=("chat_text", "chat_tools")
    )
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, request, *, stream, follow_redirects=False):
            sent.append(request)
            return _FakeResponse(status_code=200, chunks=[])

        async def aclose(self):
            return None

    async def drifting_tool_stream(_payload, **kwargs):
        sender = kwargs["response_sender"]
        assert sender is not None
        await sender(FakeClient(), {"model": MODEL_ID, "messages": []})
        repository.update_connection(
            "local",
            newapi_id,
            RouterConnectionUpdate(base_url="https://changed.example/v1"),
        )
        await sender(FakeClient(), {"model": MODEL_ID, "messages": []})
        yield "must not be reached"

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        main_module, "stream_chat_toolset_text", drifting_tool_stream
    )
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": MODEL_ID,
                "gateway": "default",
                "messages": [{"role": "user", "content": "private user text"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert len(sent) == 1
    assert "provider_chat_policy_or_qualification_changed" in response.text
    assert "event: route_receipt" in response.text
    receipts = repository.list_chat_control_receipts("local")
    assert len(receipts["attempts"]) == 1
    assert receipts["attempts"][0]["connection_id"] == newapi_id
    assert receipts["attempts"][0]["connection_id"] != backup_id
    assert receipts["attempts"][0]["dispatched"] == 1
    assert receipts["runs"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_preferred_tool_mode_rejects_actual_model_mismatch(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(
        tmp_path, capabilities=("chat_text", "chat_tools")
    )
    configure_model_router(service)
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))

    class FakeClient:
        def build_request(self, method, url, **kwargs):
            return {"method": method, "url": str(url), **kwargs}

        async def send(self, _request, *, stream, follow_redirects=False):
            return _FakeResponse(status_code=200, chunks=[])

    async def mismatched_tool_stream(_payload, **kwargs):
        await kwargs["response_sender"](
            FakeClient(), {"model": MODEL_ID, "messages": []}
        )
        kwargs["actual_model_observer"]("provider/substituted-model")
        yield "must not be reached"

    monkeypatch.setattr(
        main_module, "stream_chat_toolset_text", mismatched_tool_stream
    )
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": MODEL_ID,
                "gateway": "default",
                "messages": [{"role": "user", "content": "private text"}],
                "tool_mode": "mcp_tools",
                "tool_names": "fetch",
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert "provider_chat_actual_model_mismatch" in response.text
    assert "must not be reached" not in response.text
    receipts = repository.list_chat_control_receipts("local")
    assert receipts["runs"][0]["status"] == "failed"
    assert receipts["attempts"][0]["error_code"] == (
        "provider_chat_actual_model_mismatch"
    )


@pytest.mark.asyncio
async def test_preferred_file_output_uses_managed_target_without_vendor_hint(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(
        tmp_path, capabilities=("chat_text", "chat_file_output")
    )
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", _fake_client(sent))
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": MODEL_ID,
                "gateway": "default",
                "messages": [{"role": "user", "content": "private file request"}],
                "file_scope_id": "chat-session-1",
                "output_mode": "allowlisted",
                "output_context_id": "turn-1",
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 200, response.text
    assert len(sent) == 1
    assert sent[0]["url"] == "https://8.8.8.8/v1/chat/completions"
    assert sent[0]["headers"]["Host"] == "newapi.example"
    assert "provider" not in sent[0]["json"]
    receipt_event = next(
        item
        for item in response.text.split("\n\n")
        if item.startswith("event: route_receipt")
    )
    receipt = json.loads(receipt_event.split("data:", 1)[1].strip())
    assert receipt["engine"] == "newapi"
    assert "provider_chat_chat_file_output_managed" in receipt["reason_codes"]
    stored = repository.list_chat_control_receipts("local")
    assert stored["runs"][0]["capability"] == "chat_file_output"
    assert stored["attempts"][0]["dispatched"] == 1


@pytest.mark.asyncio
async def test_preferred_file_output_preserves_managed_http_hard_failure(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(
        tmp_path, capabilities=("chat_text", "chat_file_output")
    )
    configure_model_router(service)
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    _disable_runtime(monkeypatch)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        _fake_client(sent, status_code=401),
    )
    try:
        response = await client.post(
            "/api/chat",
            json={
                "model_id": MODEL_ID,
                "gateway": "default",
                "messages": [{"role": "user", "content": "private file request"}],
                "file_scope_id": "chat-session-1",
                "output_mode": "allowlisted",
                "output_context_id": "turn-1",
            },
        )
    finally:
        configure_model_router(original_service)

    assert response.status_code == 502, response.text
    payload = response.json()
    assert payload["code"] == "provider_chat_http_401"
    assert payload["route_receipt"]["reason_codes"][-1] == (
        "provider_chat_http_401"
    )
    receipts = repository.list_chat_control_receipts("local")
    assert receipts["runs"][0]["hard_failure"] == 1
    assert receipts["runs"][0]["result_class"] == "hard_failure"
    assert receipts["attempts"][0]["error_code"] == "provider_chat_http_401"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_update",
    [
        {"tool_mode": "mcp_tools", "tool_names": "fetch"},
        {
            "output_mode": "allowlisted",
            "file_scope_id": "chat-session-1",
            "output_context_id": "turn-1",
        },
    ],
)
async def test_specialized_capabilities_never_borrow_text_qualification_or_legacy(
    client: httpx.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request_update: dict[str, object],
) -> None:
    original_service = get_model_router_service()
    service, repository, _newapi_id, _backup_id = _service(tmp_path)
    configure_model_router(service)
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    monkeypatch.setenv("CHAT_FILE_OUTPUT_TOOL_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("https://legacy.example/v1/chat/completions", "legacy-secret"),
    )
    request = _request()
    request.update(request_update)
    try:
        response = await client.post("/api/chat", json=request)
    finally:
        configure_model_router(original_service)

    assert response.status_code == 503, response.text
    payload = response.json()
    assert payload["code"] == "provider_chat_no_qualified_route"
    assert payload["route_receipt"]["engine"] == "managed_chat_blocked"
    stored = repository.list_chat_control_receipts("local")
    assert stored["runs"][0]["capability"] in {
        "chat_tools",
        "chat_file_output",
    }
    assert all(item["dispatched"] == 0 for item in stored["attempts"])
