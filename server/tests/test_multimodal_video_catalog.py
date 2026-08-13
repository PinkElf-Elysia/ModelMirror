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
    configure_video_catalog_service,
    router,
)
from server.multimodal.video_catalog import (
    VERIFIED_VIDEO_GENERATION_MODELS,
    VideoCatalogService,
)


def test_verified_video_registry_contains_batch_e_acceptance() -> None:
    assert {
        "x-ai/grok-imagine-video",
        "x-ai/grok-imagine-video-1.5",
        "alibaba/happyhorse-1.0",
        "alibaba/happyhorse-1.1",
        "alibaba/wan-2.6",
        "alibaba/wan-2.7",
        "minimax/hailuo-2.3",
        "minimax/hailuo-3",
    } <= VERIFIED_VIDEO_GENERATION_MODELS


def test_verified_video_registry_contains_batch_f_acceptance() -> None:
    assert {
        "kwaivgi/kling-v3.0-pro",
        "kwaivgi/kling-v3.0-std",
        "kwaivgi/kling-video-o1",
        "bytedance/seedance-1-5-pro",
        "bytedance/seedance-2.0-fast",
    } <= VERIFIED_VIDEO_GENERATION_MODELS


def test_verified_video_registry_contains_batch_g_acceptance() -> None:
    assert {
        "google/veo-3.1-lite",
        "google/veo-3.1-fast",
        "google/veo-3.1",
        "black-forest-labs/flux-3-video",
        "openai/sora-2-pro",
    } <= VERIFIED_VIDEO_GENERATION_MODELS


def test_verified_video_registry_contains_seedance_25_refresh() -> None:
    assert "bytedance/seedance-2.5" in VERIFIED_VIDEO_GENERATION_MODELS


def openrouter_service(tmp_path: Path) -> ModelRouterService:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="video-catalog-secret",
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
    return ModelRouterService(repository)


@pytest.mark.asyncio
async def test_video_catalog_is_disabled_without_feature_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MULTIMODAL_VIDEO_ANALYSIS_ENABLED", raising=False)
    monkeypatch.delenv("MULTIMODAL_VIDEO_GENERATION_ENABLED", raising=False)
    service = VideoCatalogService(openrouter_service(tmp_path))

    result = await service.get_catalog()

    assert result.status == "disabled"
    assert result.profiles == []


@pytest.mark.asyncio
async def test_video_catalog_normalizes_analysis_and_generation_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_VIDEO_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_VIDEO_GENERATION_ENABLED", "true")
    monkeypatch.setenv(
        "MULTIMODAL_VERIFICATION_MODEL_IDS",
        "google/veo-3.1-lite",
    )
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.url.path.endswith("/videos/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/veo-3.1-lite",
                            "supported_resolutions": ["720p", "1080p"],
                            "supported_aspect_ratios": ["16:9", "9:16"],
                            "supported_sizes": [
                                "1280x720",
                                "720x1280",
                                "1920x1080",
                                "1080x1920",
                            ],
                            "supported_durations": [5, 8],
                            "supported_frame_images": [
                                "first_frame",
                                "last_frame",
                            ],
                            "allowed_passthrough_parameters": [
                                "negativePrompt",
                                "enhancePrompt",
                                "personGeneration",
                            ],
                            "generate_audio": True,
                            "seed": True,
                            "pricing_skus": {
                                "per-video-second": "0.5"
                            },
                        },
                        {
                            "id": "bytedance/seedance-2.0-fast",
                            "supported_resolutions": ["720p"],
                            "supported_aspect_ratios": ["16:9"],
                            "supported_durations": [5],
                        },
                        {
                            "id": "bytedance/seedance-2.0-mini",
                            "supported_resolutions": ["480p", "720p"],
                            "supported_aspect_ratios": [
                                "1:1",
                                "3:4",
                                "9:16",
                                "4:3",
                                "16:9",
                                "21:9",
                                "9:21",
                            ],
                            "supported_sizes": [
                                "480x480",
                                "480x640",
                                "480x854",
                                "640x480",
                                "854x480",
                                "1120x480",
                                "720x720",
                                "720x960",
                                "720x1280",
                                "720x1680",
                                "960x720",
                                "1280x720",
                                "1680x720",
                            ],
                            "supported_durations": list(range(4, 16)),
                            "supported_frame_images": [
                                "first_frame",
                                "last_frame",
                            ],
                            "generate_audio": True,
                            "seed": True,
                            "pricing_skus": {
                                "video_tokens": "0.0000035",
                                "video_tokens_without_audio": "0.0000035",
                                "video_tokens_with_video_input": "0.0000021",
                            },
                            "allowed_passthrough_parameters": [
                                "watermark",
                                "req_key",
                                "return_last_frame",
                            ],
                        },
                        {
                            "id": "runway/aleph-2",
                            "supported_resolutions": None,
                            "supported_aspect_ratios": [
                                "16:9",
                                "4:3",
                                "3:2",
                                "1:1",
                                "2:3",
                                "3:4",
                                "9:16",
                                "21:9",
                            ],
                            "supported_durations": None,
                            "supported_frame_images": None,
                            "generate_audio": False,
                            "seed": True,
                            "allowed_passthrough_parameters": [
                                "contentModeration",
                                "keyframes",
                            ],
                        },
                        {
                            "id": "runway/gen-4.5",
                            "supported_resolutions": ["720p"],
                            "supported_aspect_ratios": ["16:9", "9:16"],
                            "supported_durations": list(range(2, 11)),
                            "supported_frame_images": ["first_frame"],
                            "generate_audio": False,
                            "seed": True,
                            "allowed_passthrough_parameters": [
                                "contentModeration"
                            ],
                        },
                    ]
                },
            )
        return Response(
            200,
            json={
                "data": [
                    {
                        "id": "google/gemini-video-test",
                        "architecture": {
                            "input_modalities": ["text", "video"],
                            "output_modalities": ["text"],
                        },
                    },
                    {
                        "id": "openai/text-only",
                        "architecture": {
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                        },
                    },
                ]
            },
        )

    service = VideoCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    result = await service.get_catalog()

    assert result.status == "online"
    assert result.stale is False
    assert len(result.profiles) == 6
    analysis = next(
        item for item in result.profiles if item.operation == "analyze_video"
    )
    generation = next(
        item
        for item in result.profiles
        if item.model_id == "google/veo-3.1-lite"
    )
    reference_model = next(
        item
        for item in result.profiles
        if item.model_id == "bytedance/seedance-2.0-fast"
    )
    seedance_mini = next(
        item
        for item in result.profiles
        if item.model_id == "bytedance/seedance-2.0-mini"
    )
    runway_aleph = next(
        item for item in result.profiles if item.model_id == "runway/aleph-2"
    )
    runway_gen = next(
        item for item in result.profiles if item.model_id == "runway/gen-4.5"
    )
    assert analysis.model_id == "google/gemini-video-test"
    assert analysis.supported_input_sources == ["file", "url"]
    assert analysis.interaction_status == "ready"
    assert analysis.operation_readiness[0].verification_status == "verified"
    assert analysis.operation_readiness[0].availability_status == "available"
    assert generation.model_id == "google/veo-3.1-lite"
    assert generation.supported_resolutions == ["720p", "1080p"]
    assert generation.supported_sizes == [
        "1280x720",
        "720x1280",
        "1920x1080",
        "1080x1920",
    ]
    assert generation.supported_durations == [5, 8]
    assert generation.supports_first_frame is True
    assert generation.supported_frame_types == [
        "first_frame",
        "last_frame",
    ]
    assert [option.key for option in generation.provider_options] == [
        "negativePrompt",
        "enhancePrompt",
    ]
    assert generation.supports_generated_audio is True
    assert generation.supports_seed is True
    assert generation.pricing_skus["per-video-second"] == "0.5"
    assert generation.interaction_status == "ready"
    assert generation.status_reason is None
    assert generation.verification_entry_enabled is False
    assert generation.verification_requires_cost_estimate is False
    assert generation.operation_readiness[0].verification_status == "verified"
    assert generation.operation_readiness[0].availability_status == "available"
    assert reference_model.supports_reference_images is True
    assert reference_model.max_reference_images == 3
    assert reference_model.verification_entry_enabled is False
    assert seedance_mini.interaction_status == "ready"
    assert seedance_mini.supported_resolutions == ["480p", "720p"]
    assert seedance_mini.supported_durations == list(range(4, 16))
    assert seedance_mini.supported_frame_types == [
        "first_frame",
        "last_frame",
    ]
    assert seedance_mini.supports_generated_audio is True
    assert seedance_mini.supports_seed is True
    assert seedance_mini.pricing_skus == {
        "video_tokens": "0.0000035",
        "video_tokens_without_audio": "0.0000035",
        "video_tokens_with_video_input": "0.0000021",
    }
    assert seedance_mini.operation_readiness[0].verification_status == (
        "verified"
    )
    assert runway_aleph.interaction_status == "ready"
    assert runway_aleph.verification_entry_enabled is False
    assert runway_aleph.operation_readiness[0].verification_status == (
        "verified"
    )
    assert runway_aleph.supported_resolutions == []
    assert runway_aleph.supported_durations == []
    assert runway_aleph.supported_aspect_ratios == [
        "16:9",
        "4:3",
        "3:2",
        "1:1",
        "2:3",
        "3:4",
        "9:16",
        "21:9",
    ]
    assert runway_aleph.supports_generated_audio is False
    assert runway_aleph.supports_seed is True
    assert runway_gen.interaction_status == "ready"
    assert runway_gen.supported_resolutions == ["720p"]
    assert runway_gen.supported_durations == list(range(2, 11))
    assert runway_gen.supported_frame_types == ["first_frame"]
    assert runway_gen.supports_first_frame is True
    assert runway_gen.supports_generated_audio is False
    assert runway_gen.supports_seed is True
    assert all(
        request.headers["authorization"]
        == "Bearer video-catalog-secret"
        for request in requests
    )


@pytest.mark.asyncio
async def test_video_catalog_uses_five_minute_cache_and_stale_if_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_VIDEO_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_VIDEO_GENERATION_ENABLED", "false")
    call_count = 0

    def handler(request: Request) -> Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": "provider/video-model",
                            "input_modalities": ["video"],
                            "output_modalities": ["text"],
                        }
                    ]
                },
            )
        return Response(503, text="private upstream error")

    service = VideoCatalogService(
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
    assert stale.profiles[0].model_id == "provider/video-model"
    assert call_count == 2


@pytest.mark.asyncio
async def test_video_catalog_api_does_not_expose_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_VIDEO_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_VIDEO_GENERATION_ENABLED", "false")

    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return Response(
            200,
            json={
                "data": [
                    {
                        "id": "provider/video-model",
                        "input_modalities": ["video"],
                        "output_modalities": ["text"],
                    }
                ]
            },
        )

    service = VideoCatalogService(
        openrouter_service(tmp_path),
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        ),
    )
    configure_video_catalog_service(service)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/multimodal/video/models")
            refreshed = await client.get(
                "/api/multimodal/video/models?refresh=true"
            )
    finally:
        configure_video_catalog_service(None)

    assert response.status_code == 200
    assert response.json()["profiles"][0]["model_id"] == (
        "provider/video-model"
    )
    assert "video-catalog-secret" not in response.text
    assert "openrouter.ai" not in response.text
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "online"
    assert len(requests) == 2
