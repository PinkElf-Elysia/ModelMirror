from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.api import (
    configure_audio_catalog_service,
    configure_chat_attachment_store,
    router,
)
from server.multimodal.audio_catalog import AudioCatalogService
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
    return ModelRouterService(repository)


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
                "id": "microsoft/mai-transcribe-1.5",
                "name": "Microsoft: MAI Transcribe 1.5",
                "input_modalities": ["audio"],
                "output_modalities": ["transcription"],
            },
            {
                "id": "microsoft/mai-voice-2",
                "name": "Microsoft: MAI Voice 2",
                "input_modalities": ["text"],
                "output_modalities": ["speech"],
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
                "id": "provider/text-only",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        ]
    }


@pytest.mark.asyncio
async def test_audio_catalog_disabled_without_chat_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MULTIMODAL_CHAT_AUDIO_ENABLED", raising=False)
    monkeypatch.delenv("MULTIMODAL_STREAMING_AUDIO_ENABLED", raising=False)
    service = AudioCatalogService(openrouter_service(tmp_path))

    result = await service.get_catalog()

    assert result.status == "disabled"
    assert result.profiles == []


@pytest.mark.asyncio
async def test_audio_catalog_only_marks_verified_interactions_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_STREAMING_AUDIO_ENABLED", "true")
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
    assert by_id["microsoft/mai-transcribe-1.5"].chat_modes == [
        "transcribe"
    ]
    assert "webm" in by_id[
        "microsoft/mai-transcribe-1.5"
    ].input_formats
    assert by_id["microsoft/mai-voice-2"].chat_modes == [
        "synthesize_speech"
    ]
    assert by_id["microsoft/mai-voice-2"].voices == [
        "en-US-Harper:MAI-Voice-2"
    ]
    assert by_id["google/lyria-test"].interaction_status == "planned"
    assert by_id["google/lyria-test"].chat_modes == []
    assert (
        by_id["provider/unverified-audio"].interaction_status == "planned"
    )
    assert "provider/text-only" not in by_id
    assert requests[0].headers["authorization"] == (
        "Bearer audio-catalog-secret"
    )
    assert requests[0].url.params["output_modalities"] == "all"


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
            response = await client.get("/api/multimodal/audio/models")
    finally:
        configure_audio_catalog_service(None)

    assert response.status_code == 200
    assert response.json()["profiles"]
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
