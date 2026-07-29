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
from server.multimodal.video_catalog import VideoCatalogService


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
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        if request.url.path.endswith("/videos/models"):
            return Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/veo-test",
                            "supported_resolutions": ["720p", "1080p"],
                            "supported_aspect_ratios": ["16:9", "9:16"],
                            "supported_durations": [5, 8],
                            "supported_frame_images": ["first_frame"],
                            "generate_audio": True,
                            "seed": True,
                            "pricing_skus": {
                                "per-video-second": "0.5"
                            },
                        }
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
    assert len(result.profiles) == 2
    analysis = next(
        item for item in result.profiles if item.operation == "analyze_video"
    )
    generation = next(
        item for item in result.profiles if item.operation == "generate_video"
    )
    assert analysis.model_id == "google/gemini-video-test"
    assert analysis.supported_input_sources == ["file", "url"]
    assert analysis.interaction_status == "ready"
    assert generation.model_id == "google/veo-test"
    assert generation.supported_resolutions == ["720p", "1080p"]
    assert generation.supported_durations == [5, 8]
    assert generation.supports_first_frame is True
    assert generation.supports_generated_audio is True
    assert generation.supports_seed is True
    assert generation.pricing_skus["per-video-second"] == "0.5"
    assert generation.interaction_status == "planned"
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

    def handler(_: Request) -> Response:
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
    finally:
        configure_video_catalog_service(None)

    assert response.status_code == 200
    assert response.json()["profiles"][0]["model_id"] == (
        "provider/video-model"
    )
    assert "video-catalog-secret" not in response.text
    assert "openrouter.ai" not in response.text
