from __future__ import annotations

import time
from pathlib import Path

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.multimodal.audio_catalog import (
    AUDIO_CATALOG_STALE_SECONDS,
    AudioCatalogService,
    AudioChatProfile,
    _CachedAudioCatalog,
)
from server.multimodal.image_catalog import (
    ImageCatalogService,
    ImageModelProfile,
    _CachedImageCatalog,
)
from server.multimodal.video_catalog import (
    VideoCatalogService,
    VideoModelProfile,
    _CachedVideoCatalog,
)


def _router(tmp_path: Path) -> ModelRouterService:
    return ModelRouterService(SQLiteRouterRepository(tmp_path))


def test_peek_catalog_never_populates_an_empty_cache(tmp_path: Path) -> None:
    router = _router(tmp_path)
    assert AudioCatalogService(router).peek_catalog() is None
    assert ImageCatalogService(router).peek_catalog() is None
    assert VideoCatalogService(router).peek_catalog() is None


def test_peek_catalog_returns_copied_snapshots_without_provider_io(
    tmp_path: Path,
) -> None:
    router = _router(tmp_path)
    now = time.monotonic()
    audio = AudioCatalogService(router)
    image = ImageCatalogService(router)
    video = VideoCatalogService(router)
    audio._cache = _CachedAudioCatalog(
        [
            AudioChatProfile(
                model_id="audio/model",
                display_name="Audio",
                provider="openrouter",
                invocable=True,
                interaction_status="ready",
                operations=["transcribe"],
            )
        ],
        "openrouter",
        "2026-08-21T00:00:00+00:00",
        now,
    )
    image._cache = _CachedImageCatalog(
        [
            ImageModelProfile(
                model_id="image/model",
                display_name="Image",
                operation="generate_image",
            )
        ],
        "2026-08-21T00:00:00+00:00",
        now,
    )
    video._cache = _CachedVideoCatalog(
        [VideoModelProfile(model_id="video/model", operation="generate_video")],
        "2026-08-21T00:00:00+00:00",
        now,
    )

    assert audio.peek_catalog().profiles[0].model_id == "audio/model"
    assert image.peek_catalog().profiles[0].model_id == "image/model"
    assert video.peek_catalog().profiles[0].model_id == "video/model"

    copied = image.peek_catalog()
    copied.profiles.clear()
    assert image.peek_catalog().profiles[0].model_id == "image/model"


def test_peek_catalog_rejects_snapshots_past_stale_window(tmp_path: Path) -> None:
    service = AudioCatalogService(_router(tmp_path))
    service._cache = _CachedAudioCatalog(
        [],
        "openrouter",
        "2026-08-21T00:00:00+00:00",
        time.monotonic() - AUDIO_CATALOG_STALE_SECONDS - 1,
    )
    assert service.peek_catalog() is None
