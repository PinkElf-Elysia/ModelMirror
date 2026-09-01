from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient as RealAsyncClient
from starlette.requests import ClientDisconnect

from server import main as main_module
from server.main import app
from server.model_router import configure_model_router, get_model_router_service
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import (
    ProviderWorkloadCallService,
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)
from server.multimodal.api import configure_chat_attachment_store
from server.multimodal.chat_attachments import ChatAttachmentStore
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget


MP3_FRAME = b"\xff\xfb\x90\xc0" + b"\x55" * 413
MP3_BYTES = MP3_FRAME * 3


class RuntimeResponse:
    def __init__(
        self,
        chunks: list[str],
        *,
        trailing_error: Exception | None = None,
    ) -> None:
        self.status_code = 200
        self.headers = {"x-request-id": "managed-audio-runtime"}
        self._chunks = chunks
        self._trailing_error = trailing_error
        self.closed = False
        self.client_closed = False
        self.text_iterator_used = False
        self.bytes_iterator_used = False

    async def aiter_text(self):
        self.text_iterator_used = True
        for chunk in self._chunks:
            yield chunk
        if self._trailing_error is not None:
            raise self._trailing_error

    async def aiter_bytes(self):
        self.bytes_iterator_used = True
        for chunk in self._chunks:
            yield chunk.encode("utf-8")
        if self._trailing_error is not None:
            raise self._trailing_error

    async def aread(self) -> bytes:
        return b""

    async def aclose(self) -> None:
        self.closed = True


class RuntimeClient:
    def __init__(
        self,
        response: RuntimeResponse,
        sent: list[dict[str, Any]],
    ) -> None:
        self.response = response
        self.sent = sent

    def build_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        return {"method": method, "url": str(url), **kwargs}

    async def send(
        self,
        request: dict[str, Any],
        *,
        stream: bool,
        follow_redirects: bool = False,
    ) -> RuntimeResponse:
        assert stream is True
        assert follow_redirects is False
        self.sent.append(request)
        return self.response

    async def aclose(self) -> None:
        self.response.client_closed = True


def certification_stream(model_id: str, shape: str) -> bytes:
    delta: dict[str, object]
    if shape == "chat_audio_input":
        delta = {"content": "Okay"}
    else:
        delta = {
            "audio": {
                "data": base64.b64encode(MP3_BYTES).decode("ascii")
            }
        }
    events = [
        {
            "model": model_id,
            "choices": [{"delta": delta, "finish_reason": None}],
        },
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    return (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n"
    ).encode()


async def configured_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entry_id: str,
    shape: str,
) -> tuple[ModelRouterService, ChatAttachmentStore]:
    model_id = "provider/audio-r8d"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            content=certification_stream(model_id, shape),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8D managed audio",
            kind="openrouter",
            base_url="https://provider.example/v1",
            api_key="r8d-runtime-secret",
            scopes=["chat", "audio"],
        ),
    )
    service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract="openrouter_chat_audio_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"r8d-chat-{shape}",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_STREAMING_AUDIO_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=shape,  # type: ignore[arg-type]
                    model_id=model_id,
                    connection_id=connection.id,
                    adapter_contract="openrouter_chat_audio_v1",
                )
            ],
        ),
    )
    control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    configure_model_router(service)
    configure_chat_attachment_store(store)
    assert [item.method for item in requests].count("POST") == 1
    return service, store


def output_payload() -> dict[str, object]:
    return {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [{"role": "user", "content": "Say OK."}],
        "response_audio": {"enabled": True, "voice": "alloy", "format": "mp3"},
    }


def output_stream(
    *,
    model_id: str | None = "provider/audio-r8d",
    encoded_audio: str | None = None,
    second_model: str | None = None,
) -> list[str]:
    encoded = encoded_audio or base64.b64encode(MP3_BYTES).decode("ascii")
    first: dict[str, object] = {
        "choices": [
            {
                "delta": {
                    "audio": {"data": encoded, "transcript": "private-output"}
                },
                "finish_reason": None,
            }
        ]
    }
    if model_id is not None:
        first["model"] = model_id
    second: dict[str, object] = {
        "choices": [{"delta": {}, "finish_reason": "stop"}]
    }
    if second_model is not None:
        second["model"] = second_model
    return [
        f"data: {json.dumps(first)}\n\n",
        f"data: {json.dumps(second)}\n\n",
        "data: [DONE]\n\n",
    ]


async def request_with_runtime(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    response: RuntimeResponse,
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(response, sent),
    )
    async with RealAsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        result = await client.post("/api/chat", json=payload)
    return result, sent


@pytest.mark.asyncio
async def test_managed_chat_audio_output_releases_only_verified_complete_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(output_stream()),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "private-output" in result.text
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("event: message_end") == 1
    assert result.text.count("data: [DONE]") == 1
    assert result.text.index("event: route_receipt") < result.text.index(
        "event: message_end"
    )
    assert result.text.index("event: message_end") < result.text.index(
        "data: [DONE]"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_rejects_mixed_input_and_output_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="synthetic.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload: dict[str, object] = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
        "response_audio": {
            "enabled": True,
            "voice": "alloy",
            "format": "mp3",
        },
    }
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            payload,
            RuntimeResponse(output_stream()),
        )
        assert result.status_code == 422
        assert result.json()["code"] == (
            "provider_multimodal_mixed_shape_unsupported"
        )
        assert sent == []
        assert service.repository.list_workload_receipts("local")["calls"] == []
        claimed = store.claim(
            attachment.attachment_id,
            expected_kind="audio",
        )
        assert claimed.attachment_id == attachment.attachment_id
        store.release_for_retry(attachment.attachment_id)
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
async def test_active_chat_audio_policy_uses_legacy_when_feature_flag_is_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setenv("MODEL_CONTROL_CHAT_AUDIO_ENABLED", "false")
    target = OpenRouterTarget(
        base_url="https://legacy-audio.example/v1",
        api_key="legacy-audio-secret",
        connection_id=None,
        cache_key="environment:legacy-audio",
    )

    class LegacyAudioCatalog:
        async def get_catalog(self) -> SimpleNamespace:
            return SimpleNamespace(
                status="online",
                profiles=[
                    SimpleNamespace(
                        model_id="provider/audio-r8d",
                        interaction_status="ready",
                        chat_modes=["native_streaming_audio_output"],
                        output_formats=["mp3"],
                        voices=["alloy"],
                    )
                ],
            )

        @staticmethod
        def resolve_target() -> OpenRouterTarget:
            return target

        @staticmethod
        def chat_completions_url(_target: OpenRouterTarget) -> str:
            return "https://legacy-audio.example/v1/chat/completions"

    async def skip_static_catalog_validation(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        main_module,
        "get_audio_catalog_service",
        lambda: LegacyAudioCatalog(),
    )
    monkeypatch.setattr(
        main_module,
        "validate_multimodal_content",
        skip_static_catalog_validation,
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(output_stream()),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert sent[0]["url"] == (
        "https://legacy-audio.example/v1/chat/completions"
    )
    assert service.repository.list_workload_receipts("local")["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_line",
    [
        "event: message_end",
        " event:route_receipt",
        "event: output_file",
        "event:error",
        "\ufeffevent: message_end",
        " \ufeff event:route_receipt",
    ],
)
async def test_managed_chat_audio_rejects_upstream_control_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_line: str,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    chunks[0] = event_line + "\n" + chunks[0]
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "event: message_end" not in result.text
    assert "event: output_file" not in result.text
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("data: [DONE]") == 1
    assert "provider_multimodal_reserved_sse_event" in result.text
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "failed"
    assert receipts["calls"][0]["error_code"] == (
        "provider_multimodal_reserved_sse_event"
    )
    assert receipts["runs"][0]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_chunks",
    [
        ["eve", "nt: output_file\n"],
        [" \ufeff ev", "ent: message_end\n"],
    ],
)
async def test_managed_chat_audio_rejects_fragmented_upstream_control_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_chunks: list[str],
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    upstream = RuntimeResponse(
        [*event_chunks[:-1], event_chunks[-1] + chunks[0], *chunks[1:]]
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "event: output_file" not in result.text
    assert "provider_multimodal_reserved_sse_event" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_managed_chat_audio_rejects_upstream_error_payload_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    chunks.insert(
        1,
        'data: {"error":{"message":"private-upstream-audio-error"}}\n\n',
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "private-upstream-audio-error" not in result.text
    assert "provider_multimodal_upstream_stream_error" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == (
        "provider_multimodal_upstream_stream_error"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_rejects_bom_prefixed_upstream_error_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    chunks.insert(
        1,
        '\ufeffdata: {"error":{"message":"private-bom-audio-error"}}\n\n',
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "private-bom-audio-error" not in result.text
    assert "provider_multimodal_upstream_stream_error" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == (
        "provider_multimodal_upstream_stream_error"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_closes_http_error_without_reading_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnboundedErrorResponse(RuntimeResponse):
        def __init__(self) -> None:
            super().__init__([])
            self.status_code = 500
            self.read_attempted = False

        async def aread(self) -> bytes:
            self.read_attempted = True
            raise AssertionError("managed error body must not be buffered")

    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    upstream = UnboundedErrorResponse()
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 500
    assert len(sent) == 1
    assert upstream.read_attempted is False
    assert upstream.closed is True
    assert upstream.client_closed is True
    assert "provider_workload_http_5xx" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == "provider_workload_http_5xx"


@pytest.mark.asyncio
async def test_managed_chat_audio_output_withholds_bytes_when_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )

    def fail_delivery_audit(
        _self: ProviderWorkloadCallService,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise RuntimeError("private-audit-backend-detail")

    monkeypatch.setattr(
        ProviderWorkloadCallService,
        "mark_delivery_pending",
        fail_delivery_audit,
    )
    upstream = RuntimeResponse(output_stream())
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "private-audit-backend-detail" not in result.text
    assert "event: output_file" not in result.text
    assert "event: message_end" not in result.text
    assert "provider_workload_audit_unavailable" in result.text
    assert upstream.closed is True
    assert upstream.client_closed is True


@pytest.mark.asyncio
async def test_managed_chat_audio_output_caps_unterminated_upstream_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_STREAM_BYTES",
        128,
    )
    upstream = RuntimeResponse(["data: " + "x" * 256])
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "provider_multimodal_stream_too_large" in result.text
    assert "event: message_end" not in result.text
    assert upstream.closed is True
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["error_code"] == "provider_multimodal_stream_too_large"


@pytest.mark.asyncio
async def test_managed_chat_audio_output_caps_one_sse_event_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_STREAM_BYTES",
        1024,
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_EVENT_BYTES",
        128,
    )
    upstream = RuntimeResponse(["data: " + "x" * 256])
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "provider_multimodal_sse_event_too_large" in result.text
    assert upstream.bytes_iterator_used is True
    assert upstream.text_iterator_used is False
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["error_code"] == (
        "provider_multimodal_sse_event_too_large"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_event_limit_ignores_whitespace_field_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_STREAM_BYTES",
        1024,
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_EVENT_BYTES",
        128,
    )
    upstream = RuntimeResponse(
        ["data: " + "x" * 72 + "\n \ndata: " + "y" * 72 + "\n\n"]
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "provider_multimodal_sse_event_too_large" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["error_code"] == (
        "provider_multimodal_sse_event_too_large"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_output_closes_resources_when_replay_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    sent: list[dict[str, Any]] = []
    upstream = RuntimeResponse(output_stream())
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 55123),
            "server": ("testserver", 80),
        }
    )
    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(output_payload()),
            request,
        )
        iterator = response.body_iterator
        first = await anext(iterator)
        assert b"private-output" in first
        await iterator.aclose()
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert upstream.closed is True
    assert upstream.client_closed is True
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "cancelled"
    assert receipts["calls"][0]["error_code"] == (
        "provider_chat_client_cancelled"
    )
    assert receipts["runs"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_chat_audio_asgi_23_disconnect_closes_and_cancels_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="disconnect.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
    }
    sent: list[dict[str, Any]] = []
    upstream = RuntimeResponse(
        [
            'data: {"model":"provider/audio-r8d","choices":[{"delta":{"content":"Okay"},"finish_reason":null}]}\n\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            "data: [DONE]\n\n",
        ]
    )
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 55125),
        "server": ("testserver", 80),
    }
    disconnect = asyncio.Event()
    blocked_send = asyncio.Event()
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, str]:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)
        if message.get("type") == "http.response.body" and message.get("body"):
            disconnect.set()
            await blocked_send.wait()

    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(payload),
            Request(scope),
        )
        await response(scope, receive, send)
        claimed = store.claim(attachment.attachment_id, expected_kind="audio")
        assert claimed.attachment_id == attachment.attachment_id
        store.release_for_retry(attachment.attachment_id)
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert any(message.get("body") for message in messages)
    assert upstream.closed is True
    assert upstream.client_closed is True
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "cancelled"
    assert receipts["calls"][0]["error_code"] == (
        "provider_chat_client_cancelled"
    )
    assert receipts["runs"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_chat_audio_asgi_24_send_failure_cancels_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    sent: list[dict[str, Any]] = []
    upstream = RuntimeResponse(output_stream())
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 55126),
        "server": ("testserver", 80),
    }
    attempted_bodies: list[bytes] = []

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        body = message.get("body")
        if isinstance(body, bytes) and body:
            attempted_bodies.append(body)
            raise OSError("synthetic-client-disconnect")

    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(output_payload()),
            Request(scope),
        )
        with pytest.raises(ClientDisconnect):
            await response(scope, receive, send)
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert len(attempted_bodies) == 1
    attempted = attempted_bodies[0]
    assert b"event: route_receipt" in attempted
    assert b"event: message_end" in attempted
    assert b"data: [DONE]" in attempted
    assert upstream.closed is True
    assert upstream.client_closed is True
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "cancelled"
    assert receipts["calls"][0]["error_code"] == (
        "provider_chat_client_cancelled"
    )
    assert receipts["runs"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_chat_audio_cleanup_closes_client_when_inner_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCloseRuntimeResponse(RuntimeResponse):
        def __init__(self, chunks: list[str]) -> None:
            super().__init__(chunks)
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            self.closed = True
            raise RuntimeError("synthetic-close")

    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    sent: list[dict[str, Any]] = []
    upstream = FailingCloseRuntimeResponse(output_stream())
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 55124),
            "server": ("testserver", 80),
        }
    )
    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(output_payload()),
            request,
        )
        iterator = response.body_iterator
        first = await anext(iterator)
        assert b"private-output" in first
        with pytest.raises(RuntimeError, match="synthetic-close"):
            await iterator.aclose()
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert upstream.close_calls == 1
    assert upstream.closed is True
    assert upstream.client_closed is True


@pytest.mark.asyncio
async def test_managed_chat_audio_output_accepts_independently_padded_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks: list[str] = []
    for index in range(3):
        event: dict[str, object] = {
            "choices": [
                {
                    "delta": {
                        "audio": {
                            "data": base64.b64encode(MP3_FRAME).decode(
                                "ascii"
                            )
                        }
                    },
                    "finish_reason": "stop" if index == 2 else None,
                }
            ]
        }
        if index == 0:
            event["model"] = "provider/audio-r8d"
        chunks.append(f"data: {json.dumps(event)}\n\n")
    chunks.append("data: [DONE]\n\n")
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("event: message_end") == 1
    assert result.text.count("data: [DONE]") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "expected_code"),
    [
        (output_stream(model_id=None), "provider_multimodal_actual_model_unverified"),
        (
            output_stream(second_model="provider/wrong-audio"),
            "provider_workload_model_mismatch",
        ),
        (
            output_stream(encoded_audio="bm90LW1wMw=="),
            "provider_multimodal_audio_stream_invalid",
        ),
    ],
)
async def test_managed_chat_audio_output_discards_unverified_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[str],
    expected_code: str,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    payload = output_payload()
    payload["file_scope_id"] = "chat-r8d-audio"
    payload["output_context_id"] = "assistant-r8d-audio"
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            payload,
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "event: output_file" not in result.text
    assert "event: message_end" not in result.text
    assert result.text.count('"error"') == 1
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("data: [DONE]") == 1
    assert expected_code in result.text
    stored = service.repository.list_workload_receipts("local")
    assert len(stored["calls"]) == 1
    assert stored["calls"][0]["error_code"] == expected_code


@pytest.mark.asyncio
async def test_known_model_mismatch_wins_over_trailing_transport_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream(second_model="provider/wrong-audio")[:-1]
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(
                chunks,
                trailing_error=httpx.ReadError("closed after mismatch"),
            ),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert result.text.count('"error"') == 1
    assert result.text.count("event: route_receipt") == 1
    assert "provider_workload_model_mismatch" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == "provider_workload_model_mismatch"


@pytest.mark.asyncio
async def test_managed_chat_audio_input_consumes_only_after_verified_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    _service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="synthetic.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload: dict[str, object] = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
    }
    chunks = [
        'data: {"model":"provider/audio-r8d","choices":[{"delta":{"content":"verified-text"},"finish_reason":null}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    try:
        result, sent = await request_with_runtime(
            monkeypatch, payload, RuntimeResponse(chunks)
        )
        assert result.status_code == 200
        assert len(sent) == 1
        assert "verified-text" in result.text
        with pytest.raises(MultimodalServiceError) as consumed:
            store.claim(attachment.attachment_id)
        assert consumed.value.code == "attachment_not_found"
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)
