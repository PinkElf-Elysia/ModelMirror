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
from server.multimodal.api import configure_transcription_service
from server.multimodal.stt import (
    MultimodalServiceError,
    OpenRouterSttAdapter,
    OpenRouterTarget,
    TranscriptionService,
    TranscriptionUsage,
    VERIFIED_TRANSCRIPTION_PROFILES,
)


WAV_BYTES = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 32
MP3_BYTES = b"ID3" + b"\x04\x00\x00" + b"\x00" * 16
M4A_BYTES = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 16
WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 16


def test_verified_transcription_profiles_cover_common_formats_and_providers() -> None:
    expected_models = {
        "microsoft/mai-transcribe-1.5",
        "mistralai/voxtral-mini-transcribe",
        "openai/whisper-1",
        "qwen/qwen3-asr-flash-2026-02-10",
        "x-ai/grok-stt-1.0",
    }
    assert expected_models <= VERIFIED_TRANSCRIPTION_PROFILES.keys()
    for profile in VERIFIED_TRANSCRIPTION_PROFILES.values():
        assert {"mp3", "wav", "m4a", "webm"} <= set(
            profile.input_formats
        )
        assert profile.smoke_languages == ("zh", "en")


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "expected_format"),
    [
        ("sample.mp3", "audio/mpeg", MP3_BYTES, "mp3"),
        ("sample.wav", "audio/wav", WAV_BYTES, "wav"),
        ("sample.m4a", "audio/mp4", M4A_BYTES, "m4a"),
        ("sample.webm", "audio/webm", WEBM_BYTES, "webm"),
    ],
)
def test_transcription_accepts_common_verified_audio_formats(
    filename: str,
    content_type: str,
    content: bytes,
    expected_format: str,
) -> None:
    clean_name, audio_format = TranscriptionService._validate_audio(
        filename,
        content_type,
        content,
    )
    assert clean_name == filename
    assert audio_format == expected_format


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
async def test_transcription_service_uses_openrouter_and_records_safe_audit(
    tmp_path: Path,
) -> None:
    router_service = openrouter_service(tmp_path)
    captured: dict[str, Any] = {}

    class FakeAdapter:
        async def transcribe(self, target, **kwargs):
            captured["target"] = target
            captured["kwargs"] = kwargs
            return (
                "测试转写结果",
                "openai/whisper-1",
                TranscriptionUsage(
                    audio_seconds=2.5,
                    input_tokens=12,
                    output_tokens=4,
                    total_tokens=16,
                    cost_usd=0.001,
                    cost_kind="actual",
                ),
            )

    service = TranscriptionService(
        router_service,
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    result = await service.transcribe(
        model_id="openai/whisper-1",
        filename="../../private-recording.wav",
        content_type="audio/wav",
        content=WAV_BYTES,
        language="zh",
    )

    assert result.text == "测试转写结果"
    assert result.provider == "openrouter"
    assert result.request_id.startswith("decision_")
    assert captured["target"].api_key == "test-secret-key"
    assert captured["kwargs"]["filename"] == "private-recording.wav"
    diagnostics = router_service.diagnostics()
    decision = diagnostics["recent_decisions"][0]
    assert decision["operation"] == "transcribe"
    assert decision["input_bytes"] == len(WAV_BYTES)
    assert decision["media_seconds"] == 2.5
    assert decision["outcome"] == "success"
    assert decision["budget"]["settled_cost_usd"] == 0.001
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "test-secret-key" not in serialized
    assert "private-recording.wav" not in serialized
    assert "测试转写结果" not in serialized


@pytest.mark.asyncio
async def test_openrouter_adapter_forwards_multipart_and_caches_catalog() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/models"):
            assert request.url.params["output_modalities"] == "transcription"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "openai/whisper-1",
                            "architecture": {
                                "input_modalities": ["audio"],
                                "output_modalities": ["transcription"],
                            },
                        }
                    ]
                },
            )
        body = await request.aread()
        assert request.url.path.endswith("/audio/transcriptions")
        assert request.headers["authorization"] == "Bearer test-key"
        assert b'name="model"' in body
        assert b"openai/whisper-1" in body
        if len(
            [
                item
                for item in requests
                if item.url.path.endswith("/audio/transcriptions")
            ]
        ) == 1:
            assert b'name="language"' in body
        else:
            assert b'name="language"' not in body
        assert b'name="file"; filename="sample.wav"' in body
        assert WAV_BYTES in body
        return httpx.Response(
            200,
            json={
                "text": "hello",
                "model": "openai/whisper-1",
                "usage": {
                    "seconds": 1.5,
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                    "cost": 0.0005,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    adapter = OpenRouterSttAdapter(
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    target = OpenRouterTarget(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        connection_id=None,
        cache_key="test",
    )

    first = await adapter.transcribe(
        target,
        model_id="openai/whisper-1",
        filename="sample.wav",
        audio_format="wav",
        content=WAV_BYTES,
        language="en",
    )
    second = await adapter.transcribe(
        target,
        model_id="openai/whisper-1",
        filename="sample.wav",
        audio_format="wav",
        content=WAV_BYTES,
        language=None,
    )

    assert first[0] == "hello"
    assert first[2].cost_kind == "actual"
    assert second[2].total_tokens == 5
    assert sum(request.url.path.endswith("/models") for request in requests) == 1
    assert sum(
        request.url.path.endswith("/audio/transcriptions")
        for request in requests
    ) == 2


@pytest.mark.parametrize(
    ("upstream_status", "status_code", "code"),
    [
        (401, 502, "provider_credentials_invalid"),
        (402, 402, "provider_quota_exceeded"),
        (429, 429, "provider_rate_limited"),
        (503, 502, "provider_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_openrouter_errors_are_translated_without_upstream_body(
    upstream_status: int,
    status_code: int,
    code: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            upstream_status,
            json={
                "error": {
                    "message": "secret upstream account detail must not leak"
                }
            },
        )

    adapter = OpenRouterSttAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )
    target = OpenRouterTarget(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        connection_id=None,
        cache_key=f"error-{upstream_status}",
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.transcribe(
            target,
            model_id="openai/whisper-1",
            filename="sample.wav",
            audio_format="wav",
            content=WAV_BYTES,
            language=None,
        )

    assert captured.value.status_code == status_code
    assert captured.value.code == code
    assert "secret upstream" not in captured.value.message


@pytest.mark.asyncio
async def test_transcription_endpoint_validates_format_language_and_model(
    tmp_path: Path,
) -> None:
    class FakeAdapter:
        async def transcribe(self, _target, **_kwargs):
            return (
                "endpoint transcript",
                "openai/whisper-1",
                TranscriptionUsage(
                    audio_seconds=1.0,
                    cost_usd=None,
                    cost_kind="unavailable",
                ),
            )

    service = TranscriptionService(
        openrouter_service(tmp_path),
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    configure_transcription_service(service)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            invalid_format = await client.post(
                "/api/multimodal/transcriptions",
                data={
                    "model_id": "openai/whisper-1",
                    "language": "auto",
                },
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
            invalid_language = await client.post(
                "/api/multimodal/transcriptions",
                data={
                    "model_id": "openai/whisper-1",
                    "language": "english",
                },
                files={"file": ("sample.wav", WAV_BYTES, "audio/wav")},
            )
            invalid_model = await client.post(
                "/api/multimodal/transcriptions",
                data={"model_id": "auto", "language": "auto"},
                files={"file": ("sample.wav", WAV_BYTES, "audio/wav")},
            )
            renamed_file = await client.post(
                "/api/multimodal/transcriptions",
                data={
                    "model_id": "openai/whisper-1",
                    "language": "auto",
                },
                files={"file": ("sample.mp3", WAV_BYTES, "audio/mpeg")},
            )
            success = await client.post(
                "/api/multimodal/transcriptions",
                data={
                    "model_id": "openai/whisper-1",
                    "language": "auto",
                },
                files={"file": ("sample.wav", WAV_BYTES, "audio/wav")},
            )
    finally:
        configure_transcription_service(None)

    assert invalid_format.status_code == 415
    assert invalid_format.json()["detail"]["code"] == "unsupported_audio_format"
    assert invalid_language.status_code == 422
    assert invalid_language.json()["detail"]["code"] == "invalid_language"
    assert invalid_model.status_code == 422
    assert invalid_model.json()["detail"]["code"] == "invalid_model_id"
    assert renamed_file.status_code == 422
    assert renamed_file.json()["detail"]["code"] == "invalid_audio_file"
    assert success.status_code == 200
    assert success.json()["text"] == "endpoint transcript"
    assert success.json()["provider"] == "openrouter"
    assert success.json()["usage"]["cost_usd"] is None
    assert success.json()["usage"]["cost_kind"] == "unavailable"


@pytest.mark.asyncio
async def test_transcription_rejects_oversized_audio_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server.multimodal.stt as stt_module

    monkeypatch.setattr(stt_module, "MAX_AUDIO_BYTES", 16)
    service = TranscriptionService(openrouter_service(tmp_path))

    with pytest.raises(MultimodalServiceError) as captured:
        await service.transcribe(
            model_id="openai/whisper-1",
            filename="sample.wav",
            content_type="audio/wav",
            content=WAV_BYTES,
            language="auto",
        )

    assert captured.value.status_code == 413
    assert captured.value.code == "audio_too_large"
