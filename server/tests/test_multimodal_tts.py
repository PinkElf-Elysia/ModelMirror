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
from server.multimodal.audio_catalog import AudioCatalogService
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget
from server.multimodal.tts import (
    ALLOWED_SPEECH_PROFILES,
    FISH_AUDIO_PUBLIC_VOICES,
    GEMINI_PCM_TTS_MODEL_ID,
    MAX_SPEECH_INPUT_CHARS,
    MINIMAX_SYSTEM_SPEECH_VOICES,
    OpenRouterTtsAdapter,
    SpeechService,
    speech_output_format,
)


MP3_BYTES = b"ID3" + b"\x04\x00\x00" + b"\x00" * 64
PCM_BYTES = b"\x00\x01" * 256
MODEL_ID = "microsoft/mai-voice-2"
VOICE = "en-US-Harper:MAI-Voice-2"


def test_verified_speech_profiles_cover_multiple_providers() -> None:
    assert {
        "fish-audio/s1",
        "fish-audio/s2-pro",
        "fish-audio/s2.1-pro-free:free",
        "fish-audio/s2.1-pro",
        "minimax/speech-2.8-hd",
        "minimax/speech-2.8-turbo",
        "microsoft/mai-voice-2",
        "mistralai/voxtral-mini-tts-2603",
        "qwen/qwen-audio-3.0-tts-flash",
        "x-ai/grok-voice-tts-1.0",
        "deepgram/aura-2",
        "zyphra/zonos-v0.1-transformer",
        "zyphra/zonos-v0.1-hybrid",
        "canopylabs/orpheus-3b-0.1-ft",
        "sesame/csm-1b",
        "hexgrad/kokoro-82m",
        GEMINI_PCM_TTS_MODEL_ID,
    } <= ALLOWED_SPEECH_PROFILES.keys()
    assert all(ALLOWED_SPEECH_PROFILES.values())
    assert speech_output_format(MODEL_ID) == "mp3"
    assert speech_output_format(GEMINI_PCM_TTS_MODEL_ID) == "wav"


def test_fish_audio_profiles_use_documented_public_voice_ids() -> None:
    assert FISH_AUDIO_PUBLIC_VOICES == (
        "8ef4a238714b45718ce04243307c57a7",
        "802e3bc2b27e49c2995d23ef70e6ac89",
    )
    for model_id in (
        "fish-audio/s1",
        "fish-audio/s2-pro",
        "fish-audio/s2.1-pro-free:free",
        "fish-audio/s2.1-pro",
    ):
        assert ALLOWED_SPEECH_PROFILES[model_id] == FISH_AUDIO_PUBLIC_VOICES
        assert speech_output_format(model_id) == "mp3"


@pytest.mark.parametrize(
    ("model_id", "input_modalities", "output_modalities", "chat_mode"),
    [
        ("fish-audio/transcribe-1", ["audio"], ["transcription"], "transcribe"),
        ("fish-audio/s1", ["text"], ["speech"], "synthesize_speech"),
        ("fish-audio/s2-pro", ["text"], ["speech"], "synthesize_speech"),
        (
            "fish-audio/s2.1-pro-free:free",
            ["text"],
            ["speech"],
            "synthesize_speech",
        ),
        (
            "fish-audio/s2.1-pro",
            ["text"],
            ["speech"],
            "synthesize_speech",
        ),
    ],
)
def test_fish_audio_catalog_profiles_are_ready(
    model_id: str,
    input_modalities: list[str],
    output_modalities: list[str],
    chat_mode: str,
) -> None:
    service = object.__new__(AudioCatalogService)
    profile = service._profile_from_item(
        "openrouter",
        "connection-test",
        {
            "id": model_id,
            "name": model_id,
            "architecture": {
                "input_modalities": input_modalities,
                "output_modalities": output_modalities,
            },
        },
        chat_enabled=True,
        streaming_enabled=False,
        generation_enabled=False,
        realtime_enabled=False,
    )

    assert profile is not None
    assert profile.interaction_status == "ready"
    assert chat_mode in profile.chat_modes
    if chat_mode == "synthesize_speech":
        assert tuple(profile.voices) == tuple(sorted(FISH_AUDIO_PUBLIC_VOICES))


@pytest.mark.parametrize(
    ("model_id", "voice"),
    [
        ("microsoft/mai-voice-2", "fr-FR-Soleil:MAI-Voice-2"),
        ("fish-audio/s2.1-pro", FISH_AUDIO_PUBLIC_VOICES[0]),
        (
            "minimax/speech-2.8-hd",
            "Chinese (Mandarin)_News_Anchor",
        ),
        (
            "minimax/speech-2.8-turbo",
            "English_expressive_narrator",
        ),
        ("mistralai/voxtral-mini-tts-2603", "en_paul_neutral"),
        ("qwen/qwen-audio-3.0-tts-flash", "longanhuan_v3.6"),
        ("x-ai/grok-voice-tts-1.0", "ara"),
        ("deepgram/aura-2", "aura-2-amalthea-en"),
        ("zyphra/zonos-v0.1-transformer", "american_female"),
        ("zyphra/zonos-v0.1-hybrid", "american_female"),
        ("canopylabs/orpheus-3b-0.1-ft", "dan"),
        ("sesame/csm-1b", "conversational_a"),
        ("hexgrad/kokoro-82m", "af_alloy"),
    ],
)
def test_speech_service_accepts_only_registered_model_voice_pairs(
    model_id: str,
    voice: str,
) -> None:
    assert SpeechService._model_id(model_id) == model_id
    assert SpeechService._voice(model_id, voice) == voice


def test_minimax_profiles_only_allow_curated_system_voices() -> None:
    assert len(MINIMAX_SYSTEM_SPEECH_VOICES) == 8
    assert set(
        ALLOWED_SPEECH_PROFILES["minimax/speech-2.8-hd"]
    ) == set(MINIMAX_SYSTEM_SPEECH_VOICES)
    assert (
        ALLOWED_SPEECH_PROFILES["minimax/speech-2.8-turbo"]
        == MINIMAX_SYSTEM_SPEECH_VOICES
    )
    with pytest.raises(MultimodalServiceError) as captured:
        SpeechService._voice(
            "minimax/speech-2.8-hd",
            "user-created-or-cloned-voice",
        )
    assert captured.value.code == "unsupported_voice"


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
            return MP3_BYTES, "generation-test", "mp3"

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

    assert first == (MP3_BYTES, "generation-123", "mp3")
    assert second[0] == MP3_BYTES
    assert sum(request.url.path.endswith("/models") for request in requests) == 1
    assert sum(
        request.url.path.endswith("/audio/speech") for request in requests
    ) == 2


@pytest.mark.asyncio
async def test_gemini_tts_wraps_verified_pcm_as_wav() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": GEMINI_PCM_TTS_MODEL_ID}]},
            )
        payload = json.loads((await request.aread()).decode("utf-8"))
        assert payload["model"] == GEMINI_PCM_TTS_MODEL_ID
        assert payload["voice"] == "Kore"
        assert payload["response_format"] == "pcm"
        return httpx.Response(
            200,
            content=PCM_BYTES,
            headers={
                "content-type": "audio/pcm;rate=24000;channels=1",
                "x-generation-id": "generation-gemini",
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
        cache_key="gemini-pcm",
    )

    content, generation_id, response_format = await adapter.synthesize(
        target,
        model_id=GEMINI_PCM_TTS_MODEL_ID,
        text="Hello",
        voice="Kore",
        speed=1.0,
    )

    assert response_format == "wav"
    assert generation_id == "generation-gemini"
    assert content[:4] == b"RIFF"
    assert content[8:12] == b"WAVE"
    assert content[44:] == PCM_BYTES
    assert len(content) == len(PCM_BYTES) + 44


@pytest.mark.parametrize(
    ("content", "content_type", "expected_code"),
    [
        (b"", "audio/pcm;rate=24000;channels=1", "empty_speech"),
        (b"\x00", "audio/pcm;rate=24000;channels=1", "invalid_speech_audio"),
        (PCM_BYTES, "audio/pcm;rate=16000;channels=1", "invalid_audio_mime"),
        (PCM_BYTES, "audio/mpeg", "invalid_audio_mime"),
    ],
)
def test_gemini_tts_rejects_invalid_pcm(
    content: bytes,
    content_type: str,
    expected_code: str,
) -> None:
    response = httpx.Response(
        200,
        content=content,
        headers={"content-type": content_type},
    )

    with pytest.raises(MultimodalServiceError) as captured:
        OpenRouterTtsAdapter._pcm_response_to_wav(response)

    assert captured.value.code == expected_code


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
            return MP3_BYTES, "generation-safe", "mp3"

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
async def test_speech_endpoint_returns_gemini_wav_with_safe_headers(
    tmp_path: Path,
) -> None:
    wav_bytes = OpenRouterTtsAdapter._pcm_response_to_wav(
        httpx.Response(
            200,
            content=PCM_BYTES,
            headers={"content-type": "audio/pcm;rate=24000;channels=1"},
        )
    )

    class FakeAdapter:
        async def synthesize(self, _target, **_kwargs):
            return wav_bytes, "generation-wav", "wav"

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
            response = await client.post(
                "/api/multimodal/speech",
                json={
                    "model_id": GEMINI_PCM_TTS_MODEL_ID,
                    "input": "Hello",
                    "voice": "Kore",
                    "response_format": "wav",
                    "speed": 1.0,
                },
            )
    finally:
        configure_speech_service(None)

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert "modelmirror-speech.wav" in response.headers[
        "content-disposition"
    ]
    assert response.content == wav_bytes


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
