from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.api import (
    configure_audio_catalog_service,
    configure_chat_attachment_store,
    router,
)
from server.multimodal.audio_catalog import (
    OPENROUTER_AUDIO_CONTRACTS,
    AudioCatalogService,
)
from server.multimodal.chat_attachments import ChatAttachmentStore
from server.multimodal.stt import MultimodalServiceError


def openrouter_service(tmp_path: Path) -> ModelRouterService:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="audio-catalog-secret",
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=5,
        checked_at="2026-07-28T00:00:00+00:00",
        error_code=None,
        error_hint=None,
    )
    return ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )


def audio_catalog_payload() -> dict[str, object]:
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
                "id": "google/gemini-3.6-flash",
                "name": "Google: Gemini 3.6 Flash",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "google/gemini-3.5-flash",
                "name": "Google: Gemini 3.5 Flash",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "google/gemini-3.5-flash-lite",
                "name": "Google: Gemini 3.5 Flash Lite",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "google/gemini-2.5-flash",
                "name": "Google: Gemini 2.5 Flash",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                "name": "NVIDIA: Nemotron 3 Nano Omni",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "mistralai/voxtral-small-24b-2507",
                "name": "Mistral: Voxtral Small 24B",
                "architecture": {
                    "input_modalities": ["text", "audio"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "microsoft/mai-transcribe-1.5",
                "name": "Microsoft: MAI Transcribe 1.5",
                "input_modalities": ["audio"],
                "output_modalities": ["transcription"],
            },
            {
                "id": "microsoft/mai-transcribe-2",
                "name": "Microsoft: MAI Transcribe 2",
                "input_modalities": ["audio"],
                "output_modalities": ["transcription"],
            },
            {
                "id": "microsoft/mai-voice-2",
                "name": "Microsoft: MAI Voice 2",
                "input_modalities": ["text"],
                "output_modalities": ["speech"],
                "supported_voices": ["en-US-Harper:MAI-Voice-2"],
            },
            {
                "id": "google/lyria-test",
                "name": "Google: Lyria",
                "input_modalities": ["text"],
                "output_modalities": ["audio"],
            },
            {
                "id": "provider/unverified-audio",
                "name": "Provider: Unverified Audio",
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
            },
            {
                "id": "provider/unverified-stt",
                "name": "Provider: Unverified STT",
                "input_modalities": ["audio"],
                "output_modalities": ["transcription"],
            },
            {
                "id": "x-ai/grok-voice-tts-1.0",
                "name": "xAI: Grok Voice TTS 1.0",
                "input_modalities": ["text"],
                "output_modalities": ["speech"],
                "supported_voices": ["ara", "eve", "unverified"],
            },
            {
                "id": "google/gemini-3.1-flash-tts-preview",
                "name": "Google: Gemini 3.1 Flash TTS Preview",
                "input_modalities": ["text"],
                "output_modalities": ["speech"],
                "supported_voices": ["Aoede", "Kore", "Puck"],
            },
            {
                "id": "deepgram/aura-2",
                "name": "Deepgram: Aura 2",
                "input_modalities": ["text"],
                "output_modalities": ["speech"],
                "supported_voices": [
                    "aura-2-amalthea-en",
                    "aura-2-apollo-en",
                ],
            },
            {
                "id": "minimax/speech-2.8-hd",
                "name": "MiniMax: Speech 2.8 HD",
                "input_modalities": ["text"],
                "output_modalities": ["speech"],
            },
            {
                "id": "minimax/speech-2.8-turbo",
                "name": "MiniMax: Speech 2.8 Turbo",
                "input_modalities": ["text"],
                "output_modalities": ["speech"],
            },
            {
                "id": "google/lyria-3-clip-preview",
                "name": "Google: Lyria 3 Clip Preview",
                "input_modalities": ["text"],
                "output_modalities": ["audio"],
            },
            {
                "id": "provider/text-only",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            {
                "id": "provider/audio-conditioned-video",
                "input_modalities": ["text", "audio"],
                "output_modalities": ["video"],
            },
            {
                "id": "openrouter/auto",
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
            },
            {
                "id": "openrouter/auto-beta",
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
            },
            {
                "id": "meta/muse-spark-1.1",
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
            },
            {
                "id": "~google/gemini-flash-latest",
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
            },
            {
                "id": "~google/gemini-pro-latest",
                "input_modalities": ["text", "audio"],
                "output_modalities": ["text"],
            },
        ]
    }


def test_audio_contract_registry_keeps_verified_and_planned_models_separate() -> None:
    verified_direct_models = {
        "google/gemini-2.5-flash",
        "google/gemini-2.5-pro",
        "google/gemini-2.5-pro-preview-05-06",
        "google/gemini-3.1-flash-lite",
        "google/gemini-3.1-pro-preview",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "thinkingmachines/inkling",
        "xiaomi/mimo-v2.5",
    }
    assert all(
        OPENROUTER_AUDIO_CONTRACTS[model_id].behavior_verified
        for model_id in verified_direct_models
    )
    assert all(
        OPENROUTER_AUDIO_CONTRACTS[model_id].input_formats == ("wav",)
        for model_id in verified_direct_models
    )
    for model_id in (
        "google/lyria-3-clip-preview",
        "google/lyria-3-pro-preview",
    ):
        assert OPENROUTER_AUDIO_CONTRACTS[model_id].behavior_verified
        assert (
            OPENROUTER_AUDIO_CONTRACTS[model_id].supports_image_prompt
            is True
        )
        assert OPENROUTER_AUDIO_CONTRACTS[model_id].output_formats == (
            "mp3",
        )
    for model_id in (
        "minimax/speech-2.8-hd",
        "minimax/speech-2.8-turbo",
    ):
        contract = OPENROUTER_AUDIO_CONTRACTS[model_id]
        assert contract.behavior_verified
        assert contract.chat_modes == ("synthesize_speech",)
        assert contract.output_formats == ("mp3",)
        assert "Chinese (Mandarin)_News_Anchor" in contract.voices


@pytest.mark.asyncio
async def test_audio_catalog_disabled_without_chat_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MULTIMODAL_CHAT_AUDIO_ENABLED", raising=False)
    monkeypatch.delenv("MULTIMODAL_STREAMING_AUDIO_ENABLED", raising=False)
    monkeypatch.delenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", raising=False)
    monkeypatch.delenv("MULTIMODAL_REALTIME_VOICE_ENABLED", raising=False)
    monkeypatch.delenv("MULTIMODAL_VOICE_CLONING_ENABLED", raising=False)
    service = AudioCatalogService(openrouter_service(tmp_path))

    result = await service.get_catalog()

    assert result.status == "disabled"
    assert result.profiles
    by_id = {
        (profile.provider, profile.model_id): profile
        for profile in result.profiles
    }
    lyria = by_id[("openrouter", "google/lyria-3-clip-preview")]
    assert lyria.interaction_status == "disabled"
    assert lyria.operation_readiness[0].interaction_status == "ready"
    assert lyria.operation_readiness[0].availability_status == "disabled"
    realtime = by_id[("openai", "gpt-realtime-2.1-mini")]
    assert realtime.operation_readiness[0].verification_status == "verified"


@pytest.mark.asyncio
async def test_audio_catalog_only_marks_verified_interactions_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_STREAMING_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return Response(200, json=audio_catalog_payload())

    service = AudioCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )

    result = await service.get_catalog()
    by_id = {profile.model_id: profile for profile in result.profiles}

    assert result.status == "online"
    assert result.catalog_version == (
        "modelmirror-audio-contracts-2026-09-03-mai2"
    )
    assert by_id["openai/gpt-audio"].provider == "openrouter"
    assert by_id["openai/gpt-audio"].operations == ["analyze_audio"]
    assert by_id["openai/gpt-audio"].chat_modes == [
        "direct_audio_input",
        "native_streaming_audio_output",
    ]
    assert by_id["openai/gpt-audio"].input_formats == [
        "aac",
        "flac",
        "m4a",
        "mp3",
        "ogg",
        "wav",
    ]
    for model_id in (
        "google/gemini-2.5-flash",
        "google/gemini-3.6-flash",
        "google/gemini-3.5-flash",
        "google/gemini-3.5-flash-lite",
    ):
        assert by_id[model_id].interaction_status == "ready"
        assert by_id[model_id].operations == ["analyze_audio"]
        assert by_id[model_id].chat_modes == ["direct_audio_input"]
        assert by_id[model_id].supports_streaming_output is False
    assert by_id[
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    ].chat_modes == ["direct_audio_input"]
    assert by_id[
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    ].input_formats == ["wav"]
    assert by_id[
        "mistralai/voxtral-small-24b-2507"
    ].input_formats == ["flac", "m4a", "mp3", "ogg", "wav"]
    assert by_id["microsoft/mai-transcribe-1.5"].chat_modes == [
        "transcribe"
    ]
    assert "webm" in by_id[
        "microsoft/mai-transcribe-1.5"
    ].input_formats
    mai2 = by_id["microsoft/mai-transcribe-2"]
    assert mai2.operations == ["transcribe"]
    assert mai2.chat_modes == []
    assert mai2.interaction_status == "planned"
    assert mai2.operation_readiness[0].verification_status == (
        "manual_required"
    )
    assert "短音频人工验收" in (mai2.status_reason or "")
    assert "webm" in mai2.input_formats
    assert by_id["microsoft/mai-voice-2"].chat_modes == [
        "synthesize_speech"
    ]
    assert by_id["microsoft/mai-voice-2"].voices == [
        "en-US-Harper:MAI-Voice-2"
    ]
    assert by_id["x-ai/grok-voice-tts-1.0"].interaction_status == "ready"
    assert by_id["x-ai/grok-voice-tts-1.0"].voices == ["ara", "eve"]
    assert by_id[
        "google/gemini-3.1-flash-tts-preview"
    ].interaction_status == "ready"
    assert by_id[
        "google/gemini-3.1-flash-tts-preview"
    ].output_formats == ["wav"]
    assert by_id["deepgram/aura-2"].interaction_status == "ready"
    assert by_id["deepgram/aura-2"].voices == [
        "aura-2-amalthea-en",
        "aura-2-apollo-en",
    ]
    for model_id in (
        "minimax/speech-2.8-hd",
        "minimax/speech-2.8-turbo",
    ):
        assert by_id[model_id].interaction_status == "ready"
        assert by_id[model_id].chat_modes == ["synthesize_speech"]
        assert by_id[model_id].output_formats == ["mp3"]
        assert by_id[model_id].voices == [
            "Chinese (Mandarin)_Mature_Woman",
            "Chinese (Mandarin)_News_Anchor",
            "Chinese (Mandarin)_Reliable_Executive",
            "Chinese (Mandarin)_Warm_Girl",
            "English_CalmWoman",
            "English_Graceful_Lady",
            "English_expressive_narrator",
            "English_magnetic_voiced_man",
        ]
    assert by_id[
        "google/lyria-3-clip-preview"
    ].interaction_status == "ready"
    assert by_id[
        "google/lyria-3-clip-preview"
    ].supports_image_prompt is True
    assert by_id[
        "google/lyria-3-clip-preview"
    ].output_formats == ["mp3"]
    assert by_id[
        "google/lyria-3-clip-preview"
    ].price_per_generation_usd == 0.04
    assert by_id[
        "google/lyria-3-clip-preview"
    ].fixed_duration_seconds == 30
    assert by_id["google/lyria-3-clip-preview"].chat_modes == []
    assert by_id["provider/unverified-stt"].interaction_status == "planned"
    assert by_id["provider/unverified-stt"].chat_modes == []
    assert "格式与语言行为验证" in (
        by_id["provider/unverified-stt"].status_reason or ""
    )
    assert by_id["google/lyria-test"].interaction_status == "planned"
    assert by_id["google/lyria-test"].operations == ["generate_audio"]
    assert by_id["google/lyria-test"].status_reason
    assert by_id["google/lyria-test"].chat_modes == []
    assert (
        by_id["provider/unverified-audio"].interaction_status == "planned"
    )
    assert "provider/text-only" not in by_id
    assert "provider/audio-conditioned-video" not in by_id
    for model_id in ("openrouter/auto", "openrouter/auto-beta"):
        readiness = by_id[model_id].operation_readiness[0]
        assert by_id[model_id].interaction_status == "ready"
        assert readiness.interaction_status == "ready"
        assert readiness.support_level == "combined"
        assert "先将音频转成文字" in (readiness.status_reason or "")
    muse = by_id["meta/muse-spark-1.1"]
    assert muse.interaction_status == "disabled"
    assert muse.operation_readiness[0].availability_status == (
        "upstream_unavailable"
    )
    for model_id in (
        "~google/gemini-flash-latest",
        "~google/gemini-pro-latest",
    ):
        alias = by_id[model_id]
        assert alias.interaction_status == "disabled"
        assert alias.operation_readiness[0].verification_status == (
            "not_applicable"
        )
        assert "固定版本" in (alias.status_reason or "")
    direct_placeholder = next(
        profile
        for profile in result.profiles
        if profile.provider == "openai"
        and profile.model_id == "gpt-realtime-2.1-mini"
    )
    assert direct_placeholder.invocable is False
    assert direct_placeholder.interaction_status == "ready"
    assert (
        direct_placeholder.operation_readiness[0].availability_status
        == "needs_configuration"
    )
    assert requests[0].headers["authorization"] == (
        "Bearer audio-catalog-secret"
    )
    assert requests[0].url.params["output_modalities"] == "all"


@pytest.mark.asyncio
async def test_audio_catalog_marks_enabled_direct_openai_realtime_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    repository = SQLiteRouterRepository(tmp_path)
    openrouter = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="openrouter-secret",
        ),
    )
    direct_openai = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenAI Audio",
            kind="openai",
            base_url="https://api.openai.com/v1",
            api_key="openai-secret",
        ),
    )
    for connection in (openrouter, direct_openai):
        repository.save_test_result(
            "local",
            connection.id,
            health="online",
            model_count=2,
            checked_at="2026-07-29T00:00:00+00:00",
        )
    requests: list[Request] = []
    openai_available = [True]

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.headers.get("host") == "api.openai.com":
            if not openai_available[0]:
                return Response(503, text="private direct provider error")
            return Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-realtime-2.1-mini"},
                        {"id": "gpt-4o-mini-tts"},
                        {"id": "gpt-5.6"},
                    ]
                },
            )
        return Response(200, json=audio_catalog_payload())

    service = AudioCatalogService(
        ModelRouterService(
            repository,
            egress_policy=ProviderEgressPolicy(
                resolver=lambda _host, _port: ["8.8.8.8"]
            ),
        ),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    result = await service.get_catalog()
    by_id = {
        (profile.provider, profile.model_id): profile
        for profile in result.profiles
    }

    assert result.source == "mixed"
    realtime = by_id[("openai", "gpt-realtime-2.1-mini")]
    assert realtime.connection_id == direct_openai.id
    assert realtime.operations == ["realtime_voice"]
    assert realtime.interaction_status == "ready"
    assert realtime.status_reason is None
    assert realtime.supports_streaming_input is True
    assert realtime.supports_streaming_output is True
    voice = by_id[("openai", "gpt-4o-mini-tts")]
    assert voice.operations == ["synthesize_speech", "clone_voice"]
    voice_readiness = {
        item.operation: item for item in voice.operation_readiness
    }
    assert voice_readiness["synthesize_speech"].interaction_status == "ready"
    assert voice_readiness["synthesize_speech"].availability_status == "available"
    assert voice_readiness["clone_voice"].interaction_status == "planned"
    assert "验证删除" in (
        voice_readiness["clone_voice"].status_reason or ""
    )
    assert ("openai", "gpt-5.6") not in by_id
    assert {
        request.headers["authorization"] for request in requests
    } == {"Bearer openrouter-secret", "Bearer openai-secret"}

    assert service._cache is not None
    service._cache.stored_at -= 301
    openai_available[0] = False
    stale = await service.get_catalog()
    assert stale.status == "stale"
    assert stale.stale is True
    assert any(
        profile.provider == "openai" for profile in stale.profiles
    )
    assert "private direct provider error" not in stale.model_dump_json()


@pytest.mark.parametrize(
    "model_id,formats",
    [
        ("google/gemini-3.8-flash", ["aac", "flac", "m4a", "mp3", "ogg", "wav"]),
        ("meta/muse-spark-1.3", ["wav"]),
        ("meta/muse-spark-1.3-contributor", ["wav"]),
    ],
)
@pytest.mark.parametrize("chat_enabled", [True, False])
@pytest.mark.parametrize("invocable", [True, False])
def test_september_audio_contracts_preserve_entry_gates_and_evidence(
    model_id: str,
    formats: list[str],
    chat_enabled: bool,
    invocable: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MULTIMODAL_VERIFICATION_MODEL_IDS", raising=False)
    service = object.__new__(AudioCatalogService)
    profile = service._profile_from_item(
        "openrouter",
        "connection-test",
        {
            "id": model_id,
            "architecture": {
                "input_modalities": ["text", "image", "video", "file", "audio"],
                "output_modalities": ["text"],
            },
        },
        chat_enabled=chat_enabled,
        streaming_enabled=True,
        generation_enabled=True,
        realtime_enabled=True,
        invocable=invocable,
    )
    assert profile is not None
    assert profile.input_formats == formats
    assert profile.operations == ["analyze_audio"]
    assert profile.chat_modes == (["direct_audio_input"] if chat_enabled else [])
    assert profile.output_formats == []
    assert profile.supports_streaming_output is False
    assert profile.invocable is invocable
    readiness = profile.operation_readiness[0]
    assert readiness.interaction_status == "ready"
    assert readiness.verification_status == "contract_verified"
    assert readiness.availability_status == (
        "disabled" if not chat_enabled
        else "available" if invocable
        else "needs_configuration"
    )
    assert OPENROUTER_AUDIO_CONTRACTS[model_id].behavior_verified is False
    if model_id.startswith("meta/") and chat_enabled and invocable:
        assert "音频理解尚未完整支持" in (profile.status_reason or "")
        if model_id.endswith("-contributor"):
            assert "改进 Meta 产品" in (profile.status_reason or "")


def test_gemini38_batch_does_not_inherit_realtime_audio_contract() -> None:
    assert "google/gemini-3.8-flash:batch" not in OPENROUTER_AUDIO_CONTRACTS


def test_manually_accepted_audio_contracts_are_verified_without_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    service = object.__new__(AudioCatalogService)
    item = {
        "id": "meta/muse-spark-1.2",
        "name": "Muse Spark 1.2",
        "architecture": {
            "input_modalities": ["audio", "text"],
            "output_modalities": ["text"],
        },
    }

    monkeypatch.delenv("MULTIMODAL_VERIFICATION_MODEL_IDS", raising=False)
    profile = service._profile_from_item(
        "openrouter",
        "connection-test",
        item,
        chat_enabled=True,
        streaming_enabled=False,
        generation_enabled=False,
        realtime_enabled=False,
    )
    assert profile is not None
    assert profile.interaction_status == "ready"
    assert profile.operation_readiness[0].verification_status == "verified"
    assert "direct_audio_input" in profile.chat_modes


@pytest.mark.asyncio
async def test_audio_catalog_uses_cache_and_stale_if_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    call_count = 0

    def handler(_: Request) -> Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(200, json=audio_catalog_payload())
        return Response(503, text="private upstream error")

    service = AudioCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    fresh = await service.get_catalog()
    cached = await service.get_catalog()
    assert fresh.status == "online"
    assert cached.status == "online"
    assert call_count == 1

    assert service._cache is not None
    service._cache.stored_at -= 301
    stale = await service.get_catalog()
    assert stale.status == "stale"
    assert stale.stale is True
    assert call_count == 2


@pytest.mark.asyncio
async def test_audio_catalog_endpoint_does_not_expose_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_MICROPHONE_ENABLED", "true")
    service = AudioCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(
                lambda _: Response(200, json=audio_catalog_payload())
            )
        ),
    )
    configure_audio_catalog_service(service)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/multimodal/audio/models?refresh=true"
            )
    finally:
        configure_audio_catalog_service(None)

    assert response.status_code == 200
    assert response.json()["profiles"]
    assert response.json()["catalog_version"] == (
        "modelmirror-audio-contracts-2026-09-03-mai2"
    )
    assert response.json()["microphone_enabled"] is True
    assert "audio-catalog-secret" not in response.text
    assert "openrouter.ai" not in response.text


def wav_bytes() -> bytes:
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WAVEfmt " + b"\x00" * 24


def mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24


def test_attachment_store_is_tenant_scoped_and_single_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    now = [1_000.0]
    store = ChatAttachmentStore(
        root=tmp_path / "attachments",
        clock=lambda: now[0],
    )

    created = store.create(
        kind="audio",
        filename="voice.wav",
        content_type="audio/wav",
        content=wav_bytes(),
    )
    assert created.kind == "audio"
    assert created.format == "wav"
    assert created.bytes == len(wav_bytes())
    assert (store.root / created.attachment_id).exists()

    with pytest.raises(MultimodalServiceError) as cross_tenant:
        store.claim(created.attachment_id, tenant_id="another")
    assert cross_tenant.value.code == "attachment_not_found"

    claimed = store.claim(created.attachment_id, expected_kind="audio")
    assert claimed.content == wav_bytes()
    with pytest.raises(MultimodalServiceError) as duplicate:
        store.claim(created.attachment_id, expected_kind="audio")
    assert duplicate.value.code == "attachment_already_in_use"

    store.release_for_retry(created.attachment_id)
    claimed_again = store.claim(
        created.attachment_id,
        expected_kind="audio",
    )
    assert claimed_again.content == wav_bytes()
    store.complete(created.attachment_id)
    assert not (store.root / created.attachment_id).exists()


def test_attachment_store_expires_and_rejects_disabled_or_invalid_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1_000.0]
    store = ChatAttachmentStore(
        root=tmp_path / "attachments",
        clock=lambda: now[0],
    )
    monkeypatch.delenv("MULTIMODAL_CHAT_AUDIO_ENABLED", raising=False)
    with pytest.raises(MultimodalServiceError) as disabled:
        store.create(
            kind="audio",
            filename="voice.wav",
            content_type="audio/wav",
            content=wav_bytes(),
        )
    assert disabled.value.code == "chat_audio_disabled"

    monkeypatch.setenv("MULTIMODAL_CHAT_VIDEO_ENABLED", "true")
    with pytest.raises(MultimodalServiceError) as invalid:
        store.create(
            kind="video",
            filename="clip.mp4",
            content_type="video/mp4",
            content=b"not-a-video",
        )
    assert invalid.value.code == "invalid_video_file"

    created = store.create(
        kind="video",
        filename="clip.mp4",
        content_type="video/mp4",
        content=mp4_bytes(),
    )
    now[0] += 1_801
    assert store.cleanup_expired() == 1
    with pytest.raises(MultimodalServiceError) as expired:
        store.claim(created.attachment_id)
    assert expired.value.code == "attachment_not_found"


@pytest.mark.asyncio
async def test_attachment_api_returns_only_safe_metadata_and_deletes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    configure_chat_attachment_store(store)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/multimodal/chat/attachments",
                data={"kind": "audio"},
                files={
                    "file": (
                        "private-recording.wav",
                        wav_bytes(),
                        "audio/wav",
                    )
                },
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["kind"] == "audio"
            assert payload["format"] == "wav"
            assert "private-recording" not in response.text
            assert str(store.root) not in response.text
            assert "content" not in payload

            deleted = await client.delete(
                "/api/multimodal/chat/attachments/"
                + payload["attachment_id"]
            )
            assert deleted.status_code == 200
            assert deleted.json() == {
                "attachment_id": payload["attachment_id"],
                "deleted": True,
            }
    finally:
        configure_chat_attachment_store(None)
