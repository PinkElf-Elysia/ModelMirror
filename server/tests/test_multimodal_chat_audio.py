from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient as RealAsyncClient

from server import main as main_module
from server.main import app
from server.model_router import (
    ModelRouterService,
    RouterConnectionCreate,
    SQLiteRouterRepository,
    configure_model_router,
    get_model_router_service,
)
from server.multimodal.api import (
    configure_audio_catalog_service,
    configure_chat_attachment_store,
)
from server.multimodal.audio_catalog import AudioCatalogService
from server.multimodal.chat_attachments import ChatAttachmentStore
from server.multimodal.stt import MultimodalServiceError


def audio_bytes(audio_format: str) -> bytes:
    if audio_format == "wav":
        return (
            b"RIFF"
            + b"\x00\x00\x00\x00"
            + b"WAVEfmt "
            + b"\x00" * 24
        )
    if audio_format == "mp3":
        return b"ID3" + b"\x04\x00\x00" + b"\x00" * 24
    if audio_format == "m4a":
        return b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 24
    if audio_format == "webm":
        return b"\x1a\x45\xdf\xa3" + b"\x00" * 24
    raise AssertionError(f"unsupported test format: {audio_format}")


def audio_mime(audio_format: str) -> str:
    return {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "webm": "audio/webm",
    }[audio_format]


def audio_catalog_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "id": "openai/gpt-audio",
                "name": "OpenAI: GPT Audio",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text", "audio"],
                },
            },
            {
                "id": "provider/unverified-audio",
                "name": "Provider: Unverified Audio",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            },
        ]
    }


def configure_audio_test_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModelRouterService, ChatAttachmentStore]:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_STREAMING_AUDIO_ENABLED", "false")
    repository = SQLiteRouterRepository(tmp_path / "router")
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter audio",
            kind="openrouter",
            base_url="https://audio.example/api/v1",
            api_key="direct-audio-secret",
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=2,
        checked_at="2026-07-28T00:00:00+00:00",
        error_code=None,
        error_hint=None,
    )
    service = ModelRouterService(repository)
    catalog_service = AudioCatalogService(
        service,
        client_factory=lambda: RealAsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json=audio_catalog_payload(),
                )
            )
        ),
    )
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    configure_model_router(service)
    configure_audio_catalog_service(catalog_service)
    configure_chat_attachment_store(store)
    return service, store


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        chunks: list[str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.headers = {"x-request-id": "upstream-audio-request"}
        self.content = body
        self._chunks = chunks or []

    async def aiter_text(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return self.content

    async def aclose(self) -> None:
        return None


def fake_chat_client(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse,
    sent_requests: list[dict[str, Any]],
) -> None:
    class FakeChatClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def build_request(
            self,
            method: str,
            url: str,
            headers: dict[str, str],
            json: dict[str, Any],
        ) -> dict[str, Any]:
            return {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
            }

        async def send(
            self,
            request: dict[str, Any],
            *,
            stream: bool,
        ) -> FakeResponse:
            assert stream is True
            sent_requests.append(request)
            return response

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeChatClient)
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)


def create_attachment(
    store: ChatAttachmentStore,
    audio_format: str,
) -> str:
    created = store.create(
        kind="audio",
        filename=f"private-recording.{audio_format}",
        content_type=audio_mime(audio_format),
        content=audio_bytes(audio_format),
    )
    return created.attachment_id


def chat_payload(
    attachment_id: str,
    *,
    model_id: str = "openai/gpt-audio",
    gateway: str = "default",
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "gateway": gateway,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请概括这段录音。"},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment_id,
                    },
                ],
            }
        ],
    }


def assert_attachment_pending(
    store: ChatAttachmentStore,
    attachment_id: str,
) -> None:
    claimed = store.claim(attachment_id, expected_kind="audio")
    assert claimed.attachment_id == attachment_id
    store.release_for_retry(attachment_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("audio_format", ["wav", "mp3", "m4a"])
async def test_direct_audio_chat_uses_verified_openrouter_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audio_format: str,
) -> None:
    original_service = get_model_router_service()
    service, store = configure_audio_test_services(tmp_path, monkeypatch)
    attachment_id = create_attachment(store, audio_format)
    sent_requests: list[dict[str, Any]] = []
    response = FakeResponse(
        chunks=[
            (
                'data: {"model":"openai/gpt-audio","choices":'
                '[{"delta":{"content":"音频内容摘要"},'
                '"finish_reason":null}]}\n\n'
            ),
            (
                'data: {"choices":[{"delta":{"content":""},'
                '"finish_reason":"stop"}],"usage":{"prompt_tokens":12,'
                '"completion_tokens":4,"total_tokens":16}}\n\n'
            ),
            "data: [DONE]\n\n",
        ]
    )
    fake_chat_client(monkeypatch, response, sent_requests)

    try:
        async with RealAsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            result = await client.post(
                "/api/chat",
                json=chat_payload(attachment_id),
            )

        assert result.status_code == 200, result.text
        assert len(sent_requests) == 1
        sent = sent_requests[0]
        assert sent["url"] == "https://audio.example/api/v1/chat/completions"
        assert sent["headers"]["Authorization"] == (
            "Bearer direct-audio-secret"
        )
        assert sent["json"]["model"] == "openai/gpt-audio"
        audio_part = sent["json"]["messages"][0]["content"][1]
        assert audio_part == {
            "type": "input_audio",
            "input_audio": {
                "data": base64.b64encode(audio_bytes(audio_format)).decode(
                    "ascii"
                ),
                "format": audio_format,
            },
        }
        assert attachment_id not in json.dumps(sent["json"])
        assert "音频内容摘要" in result.text
        assert result.text.count("event: route_receipt") == 1
        assert result.text.index("event: route_receipt") < result.text.index(
            "data: [DONE]"
        )
        receipt_event = next(
            event
            for event in result.text.split("\n\n")
            if event.startswith("event: route_receipt")
        )
        receipt = json.loads(
            next(
                line.removeprefix("data:").strip()
                for line in receipt_event.splitlines()
                if line.startswith("data:")
            )
        )
        assert receipt["engine"] == "openrouter"
        assert receipt["actual_model"] == "openai/gpt-audio"
        assert receipt["fallback_attempts"] == 0
        assert receipt["tokens"]["total"] == 16
        assert receipt["media"] == {
            "input_kind": "audio",
            "processing": "direct",
            "format": audio_format,
            "raw_retained": False,
        }
        assert receipt["request_id"] == "upstream-audio-request"
        with pytest.raises(MultimodalServiceError) as consumed:
            store.claim(attachment_id)
        assert consumed.value.code == "attachment_not_found"
        decision = service.diagnostics()["recent_decisions"][0]
        assert decision["operation"] == "analyze_audio"
        assert decision["outcome"] == "success"
        assert decision["input_bytes"] == len(audio_bytes(audio_format))
    finally:
        configure_audio_catalog_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
async def test_auto_rejects_raw_audio_without_calling_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    _, store = configure_audio_test_services(tmp_path, monkeypatch)
    attachment_id = create_attachment(store, "wav")
    sent_requests: list[dict[str, Any]] = []
    fake_chat_client(monkeypatch, FakeResponse(), sent_requests)

    try:
        async with RealAsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            result = await client.post(
                "/api/chat",
                json=chat_payload(
                    attachment_id,
                    model_id="auto",
                    gateway="auto",
                ),
            )
        assert result.status_code == 422
        assert sent_requests == []
        assert_attachment_pending(store, attachment_id)
    finally:
        configure_audio_catalog_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "audio_format", "expected_code"),
    [
        ("provider/unverified-audio", "wav", "operation_mismatch"),
        (
            "openai/gpt-audio",
            "webm",
            "direct_audio_format_unsupported",
        ),
    ],
)
async def test_direct_audio_rejects_unverified_model_or_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    audio_format: str,
    expected_code: str,
) -> None:
    original_service = get_model_router_service()
    _, store = configure_audio_test_services(tmp_path, monkeypatch)
    attachment_id = create_attachment(store, audio_format)
    sent_requests: list[dict[str, Any]] = []
    fake_chat_client(monkeypatch, FakeResponse(), sent_requests)

    try:
        async with RealAsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            result = await client.post(
                "/api/chat",
                json=chat_payload(attachment_id, model_id=model_id),
            )
        assert result.status_code == 422
        assert result.json()["code"] == expected_code
        assert sent_requests == []
        assert_attachment_pending(store, attachment_id)
    finally:
        configure_audio_catalog_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
async def test_direct_audio_upstream_error_never_uses_gateway_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, store = configure_audio_test_services(tmp_path, monkeypatch)
    attachment_id = create_attachment(store, "wav")
    sent_requests: list[dict[str, Any]] = []
    fake_chat_client(
        monkeypatch,
        FakeResponse(status_code=503, body=b"private provider error"),
        sent_requests,
    )

    try:
        async with RealAsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            result = await client.post(
                "/api/chat",
                json=chat_payload(attachment_id),
            )
        assert result.status_code == 503
        assert len(sent_requests) == 1
        assert "private provider error" not in result.text
        assert "音频模型服务暂时不可用" in result.text
        assert_attachment_pending(store, attachment_id)
        decision = service.diagnostics()["recent_decisions"][0]
        assert decision["outcome"] == "http_503"
    finally:
        configure_audio_catalog_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
async def test_direct_audio_interrupted_stream_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, store = configure_audio_test_services(tmp_path, monkeypatch)
    attachment_id = create_attachment(store, "wav")
    sent_requests: list[dict[str, Any]] = []
    fake_chat_client(
        monkeypatch,
        FakeResponse(
            chunks=[
                (
                    'data: {"model":"openai/gpt-audio","choices":'
                    '[{"delta":{"content":"未完成回答"},'
                    '"finish_reason":null}]}\n\n'
                )
            ]
        ),
        sent_requests,
    )

    try:
        async with RealAsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            result = await client.post(
                "/api/chat",
                json=chat_payload(attachment_id),
            )
        assert result.status_code == 200
        assert "未完成回答" in result.text
        assert '"error":' in result.text
        assert "event: route_receipt" not in result.text
        assert_attachment_pending(store, attachment_id)
        decision = service.diagnostics()["recent_decisions"][0]
        assert decision["outcome"] == "stream_interrupted"
    finally:
        configure_audio_catalog_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)
