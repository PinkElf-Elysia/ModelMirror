from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.main import app
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.api import configure_speech_service
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget
from server.multimodal.tts import (
    MAX_SPEECH_INPUT_CHARS,
    OpenRouterTtsAdapter,
    SpeechService,
)


MP3_BYTES = b"ID3" + b"\x04\x00\x00" + b"\x00" * 64
MODEL_ID = "microsoft/mai-voice-2"
VOICE = "en-US-Harper:MAI-Voice-2"


def openrouter_service(tmp_path: Path) -> ModelRouterService:
    repository = SQLiteRouterRepository(tmp_path)
    service = ModelRouterService(repository)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-secret-key",
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-07-28T00:00:00+00:00",
        error_code=None,
        error_hint=None,
    )
    return service


@pytest.mark.asyncio
async def test_speech_service_records_bytes_without_text_or_secret(
    tmp_path: Path,
) -> None:
    router_service = openrouter_service(tmp_path)
    captured: dict[str, Any] = {}

    class FakeAdapter:
        async def synthesize(self, target, **kwargs):
            captured["target"] = target
            captured["kwargs"] = kwargs
            return MP3_BYTES, "generation-test"

    service = SpeechService(
        router_service,
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    result = await service.synthesize(
        model_id=MODEL_ID,
        text="不要把这段文字写入审计",
        voice=VOICE,
        response_format="mp3",
        speed=1.0,
    )

    assert result.content == MP3_BYTES
    assert result.request_id.startswith("decision_")
    assert result.output_bytes == len(MP3_BYTES)
    assert captured["target"].api_key == "test-secret-key"
    assert captured["kwargs"]["voice"] == VOICE
    diagnostics = router_service.diagnostics()
    decision = diagnostics["recent_decisions"][0]
    assert decision["operation"] == "synthesize_speech"
    assert decision["input_bytes"] == len(
        "不要把这段文字写入审计".encode("utf-8")
    )
    assert decision["output_bytes"] == len(MP3_BYTES)
    assert decision["outcome"] == "success"
    assert decision["budget"]["status"] == "unavailable"
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "test-secret-key" not in serialized
    assert "不要把这段文字写入审计" not in serialized


@pytest.mark.asyncio
async def test_tts_adapter_posts_json_and_caches_speech_catalog() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/models"):
            assert request.url.params["output_modalities"] == "speech"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": MODEL_ID,
                            "architecture": {
                                "input_modalities": ["text"],
                                "output_modalities": ["speech"],
                            },
                        }
                    ]
                },
            )
        payload = json.loads((await request.aread()).decode("utf-8"))
        assert request.url.path.endswith("/audio/speech")
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload == {
            "model": MODEL_ID,
            "input": "Hello",
            "voice": VOICE,
            "response_format": "mp3",
            "speed": 1.25,
        }
        return httpx.Response(
            200,
            content=MP3_BYTES,
            headers={
                "content-type": "audio/mpeg",
                "x-generation-id": "generation-123",
            },
        )

    adapter = OpenRouterTtsAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    target = OpenRouterTarget(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        connection_id=None,
        cache_key="test",
    )

    first = await adapter.synthesize(
        target,
        model_id=MODEL_ID,
        text="Hello",
        voice=VOICE,
        speed=1.25,
    )
    second = await adapter.synthesize(
        target,
        model_id=MODEL_ID,
        text="Hello",
        voice=VOICE,
        speed=1.25,
    )

    assert first == (MP3_BYTES, "generation-123")
    assert second[0] == MP3_BYTES
    assert sum(request.url.path.endswith("/models") for request in requests) == 1
    assert sum(
        request.url.path.endswith("/audio/speech") for request in requests
    ) == 2


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_code"),
    [
        (401, 502, "provider_credentials_invalid"),
        (402, 402, "provider_quota_exceeded"),
        (429, 429, "provider_rate_limited"),
        (503, 502, "provider_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_tts_upstream_errors_do_not_leak_body(
    status: int,
    expected_status: int,
    expected_code: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"message": "secret upstream account detail"}},
        )

    adapter = OpenRouterTtsAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    target = OpenRouterTarget(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        connection_id=None,
        cache_key=f"error-{status}",
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.synthesize(
            target,
            model_id=MODEL_ID,
            text="Hello",
            voice=VOICE,
            speed=1.0,
        )

    assert captured.value.status_code == expected_status
    assert captured.value.code == expected_code
    assert "secret upstream" not in captured.value.message


@pytest.mark.parametrize(
    ("content", "content_type", "expected_code"),
    [
        (b"", "audio/mpeg", "empty_speech"),
        (b'{"error":"not audio"}', "application/json", "invalid_audio_mime"),
        (b"not-an-mp3", "audio/mpeg", "invalid_speech_audio"),
    ],
)
@pytest.mark.asyncio
async def test_tts_rejects_empty_or_corrupt_audio(
    content: bytes,
    content_type: str,
    expected_code: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": MODEL_ID}]},
            )
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": content_type},
        )

    adapter = OpenRouterTtsAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    target = OpenRouterTarget(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        connection_id=None,
        cache_key=f"invalid-{expected_code}",
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.synthesize(
            target,
            model_id=MODEL_ID,
            text="Hello",
            voice=VOICE,
            speed=1.0,
        )

    assert captured.value.code == expected_code


@pytest.mark.asyncio
async def test_speech_endpoint_validates_profile_and_returns_safe_headers(
    tmp_path: Path,
) -> None:
    class FakeAdapter:
        async def synthesize(self, _target, **_kwargs):
            return MP3_BYTES, "generation-safe"

    service = SpeechService(
        openrouter_service(tmp_path),
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    configure_speech_service(service)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            invalid_model = await client.post(
                "/api/multimodal/speech",
                json={
                    "model_id": "example/unverified-tts",
                    "input": "Hello",
                    "voice": VOICE,
                    "response_format": "mp3",
                    "speed": 1.0,
                },
            )
            invalid_voice = await client.post(
                "/api/multimodal/speech",
                json={
                    "model_id": MODEL_ID,
                    "input": "Hello",
                    "voice": "unverified-voice",
                    "response_format": "mp3",
                    "speed": 1.0,
                },
            )
            success = await client.post(
                "/api/multimodal/speech",
                json={
                    "model_id": MODEL_ID,
                    "input": "Hello",
                    "voice": VOICE,
                    "response_format": "mp3",
                    "speed": 1.0,
                },
            )
    finally:
        configure_speech_service(None)

    assert invalid_model.status_code == 422
    assert (
        invalid_model.json()["detail"]["code"]
        == "unsupported_speech_model"
    )
    assert invalid_voice.status_code == 422
    assert invalid_voice.json()["detail"]["code"] == "unsupported_voice"
    assert success.status_code == 200
    assert success.headers["content-type"] == "audio/mpeg"
    assert success.headers["x-modelmirror-actual-model"] == MODEL_ID
    assert success.headers["x-modelmirror-provider"] == "openrouter"
    assert success.headers["x-modelmirror-cost-kind"] == "unavailable"
    assert success.content == MP3_BYTES


@pytest.mark.asyncio
async def test_speech_service_rejects_text_length_speed_and_format(
    tmp_path: Path,
) -> None:
    service = SpeechService(openrouter_service(tmp_path))
    cases = [
        (
            {
                "model_id": MODEL_ID,
                "text": " ",
                "voice": VOICE,
                "response_format": "mp3",
                "speed": 1.0,
            },
            "empty_speech_input",
        ),
        (
            {
                "model_id": MODEL_ID,
                "text": "a" * (MAX_SPEECH_INPUT_CHARS + 1),
                "voice": VOICE,
                "response_format": "mp3",
                "speed": 1.0,
            },
            "speech_input_too_long",
        ),
        (
            {
                "model_id": MODEL_ID,
                "text": "Hello",
                "voice": VOICE,
                "response_format": "pcm",
                "speed": 1.0,
            },
            "unsupported_speech_format",
        ),
        (
            {
                "model_id": MODEL_ID,
                "text": "Hello",
                "voice": VOICE,
                "response_format": "mp3",
                "speed": 2.1,
            },
            "invalid_speech_speed",
        ),
    ]

    for payload, expected_code in cases:
        with pytest.raises(MultimodalServiceError) as captured:
            await service.synthesize(**payload)
        assert captured.value.code == expected_code
