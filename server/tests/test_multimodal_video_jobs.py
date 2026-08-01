from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi import FastAPI
import httpx
from httpx import (
    ASGITransport,
    AsyncClient,
    MockTransport,
    Request,
    Response,
)

from server.model_router.repository import SCHEMA_VERSION, SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.api import configure_video_job_service, router
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget
from server.multimodal.video_catalog import (
    VideoModelCatalogResponse,
    VideoModelProfile,
    VideoProviderOption,
)
from server.multimodal.video_jobs import (
    OpenRouterVideoJobAdapter,
    VideoContent,
    VideoJobService,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image"
VIDEO = b"\x00\x00\x00\x18ftypmp42safe-video"


def router_service(
    storage: Path, *, tenant_id: str = "local"
) -> ModelRouterService:
    repository = SQLiteRouterRepository(storage)
    connection = repository.create_connection(
        tenant_id,
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="video-job-secret",
        ),
    )
    repository.save_test_result(
        tenant_id,
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-07-29T00:00:00+00:00",
        error_code=None,
        error_hint=None,
    )
    return ModelRouterService(repository, tenant_id=tenant_id)


class StubCatalog:
    def __init__(
        self,
        router: ModelRouterService,
        *,
        supports_first_frame: bool = True,
        supports_generated_audio: bool = True,
        supports_seed: bool = True,
        model_id: str = "google/veo-test",
        supports_last_frame: bool = True,
        supports_reference_images: bool = False,
        max_reference_images: int | None = None,
        provider_options: list[VideoProviderOption] | None = None,
        catalog_status: Literal[
            "online", "stale", "offline", "disabled"
        ] = "online",
    ) -> None:
        self.router = router
        self.force_requests: list[bool] = []
        self.catalog_status = catalog_status
        self.profile = VideoModelProfile(
            model_id=model_id,
            operation="generate_video",
            supported_resolutions=["720p", "1080p"],
            supported_aspect_ratios=["16:9", "9:16"],
            supported_durations=[5, 8],
            supported_frame_types=[
                *(
                    ["first_frame"]
                    if supports_first_frame
                    else []
                ),
                *(
                    ["last_frame"]
                    if supports_last_frame
                    else []
                ),
            ],
            supports_first_frame=supports_first_frame,
            supports_reference_images=supports_reference_images,
            max_reference_images=max_reference_images,
            supports_generated_audio=supports_generated_audio,
            supports_seed=supports_seed,
            provider_options=provider_options or [],
            interaction_status="planned",
        )

    @staticmethod
    def _enabled(_: str) -> bool:
        return True

    async def get_catalog(
        self,
        *,
        force: bool = False,
    ) -> VideoModelCatalogResponse:
        self.force_requests.append(force)
        return VideoModelCatalogResponse(
            source="openrouter",
            status=self.catalog_status,
            stale=self.catalog_status == "stale",
            synced_at="2026-07-29T00:00:00+00:00",
            profiles=[self.profile],
        )

    def resolve_target(self) -> OpenRouterTarget:
        connection = self.router.list_connections()[0]
        return OpenRouterTarget(
            base_url=connection.base_url,
            api_key=self.router.repository.resolve_api_key(
                self.router.tenant_id, connection.id
            ),
            connection_id=connection.id,
            cache_key=connection.id,
        )


class FakeAdapter:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.poll_calls: list[str] = []
        self.poll_responses: list[dict[str, Any] | Exception] = []
        self.content_calls: list[tuple[str, int]] = []

    async def submit(
        self,
        _: OpenRouterTarget,
        payload: dict[str, object],
    ) -> dict[str, Any]:
        self.submit_calls.append(payload)
        return {
            "id": "upstream_private_1",
            "status": "pending",
        }

    async def poll(
        self,
        _: OpenRouterTarget,
        upstream_job_id: str,
    ) -> dict[str, Any]:
        self.poll_calls.append(upstream_job_id)
        result = self.poll_responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def content(
        self,
        _: OpenRouterTarget,
        upstream_job_id: str,
        *,
        index: int,
    ) -> VideoContent:
        self.content_calls.append((upstream_job_id, index))

        async def chunks() -> AsyncIterator[bytes]:
            yield VIDEO[:8]
            yield VIDEO[8:]

        return VideoContent(
            chunks=chunks(),
            media_type="video/mp4",
            content_length=len(VIDEO),
        )


def job_service(
    storage: Path,
    *,
    adapter: FakeAdapter | None = None,
    tenant_id: str = "local",
) -> tuple[VideoJobService, FakeAdapter]:
    router_instance = router_service(storage, tenant_id=tenant_id)
    fake = adapter or FakeAdapter()
    return (
        VideoJobService(
            router_instance,
            StubCatalog(router_instance),
            adapter=fake,
        ),
        fake,
    )


@pytest.mark.asyncio
async def test_submit_claims_idempotency_before_single_upstream_call(
    tmp_path: Path,
) -> None:
    service, adapter = job_service(tmp_path)

    first = await service.create(
        model_id="google/veo-test",
        prompt="A paper boat crossing a quiet lake",
        duration=5,
        resolution="720p",
        aspect_ratio="16:9",
        generate_audio=True,
        seed=42,
        first_frame_filename="frame.png",
        first_frame_content_type="image/png",
        first_frame_content=PNG,
        idempotency_key="submission-0001",
    )
    duplicate = await service.create(
        model_id="google/veo-test",
        prompt="This changed prompt must not trigger another paid call",
        duration=5,
        resolution="720p",
        aspect_ratio="16:9",
        generate_audio=True,
        seed=42,
        first_frame_filename="frame.png",
        first_frame_content_type="image/png",
        first_frame_content=PNG,
        idempotency_key="submission-0001",
    )

    assert first.job_id == duplicate.job_id
    assert first.status == "queued"
    assert len(adapter.submit_calls) == 1
    payload = adapter.submit_calls[0]
    assert payload["model"] == "google/veo-test"
    assert payload["duration"] == 5
    frame = payload["frame_images"][0]  # type: ignore[index]
    assert frame["frame_type"] == "first_frame"
    assert frame["image_url"]["url"].startswith("data:image/png;base64,")

    with sqlite3.connect(
        service.router_service.repository.database_path
    ) as connection:
        dump = "\n".join(connection.iterdump())
        operation = connection.execute(
            "SELECT operation FROM router_decisions"
        ).fetchone()[0]
    assert "paper boat" not in dump
    assert "changed prompt" not in dump
    assert "data:image" not in dump
    assert "upstream_private_1" in dump
    assert operation == "generate_video"


@pytest.mark.asyncio
async def test_submit_maps_first_and_last_frames_without_persisting_media(
    tmp_path: Path,
) -> None:
    service, adapter = job_service(tmp_path)

    job = await service.create(
        model_id="google/veo-test",
        prompt="Keep the subject between these exact endpoints",
        first_frame_filename="first.png",
        first_frame_content_type="image/png",
        first_frame_content=PNG,
        last_frame_filename="last.png",
        last_frame_content_type="image/png",
        last_frame_content=PNG + b"-last",
        idempotency_key="first-last-0001",
    )

    frames = adapter.submit_calls[0]["frame_images"]
    assert isinstance(frames, list)
    assert [frame["frame_type"] for frame in frames] == [
        "first_frame",
        "last_frame",
    ]
    assert job.parameters.has_first_frame is True
    assert job.parameters.has_last_frame is True
    with sqlite3.connect(
        service.router_service.repository.database_path
    ) as connection:
        dump = "\n".join(connection.iterdump())
    assert "exact endpoints" not in dump
    assert "data:image" not in dump


@pytest.mark.asyncio
async def test_submit_maps_three_audited_reference_images(
    tmp_path: Path,
) -> None:
    router_instance = router_service(tmp_path)
    adapter = FakeAdapter()
    service = VideoJobService(
        router_instance,
        StubCatalog(
            router_instance,
            model_id="bytedance/seedance-2.0-fast",
            supports_reference_images=True,
            max_reference_images=3,
        ),
        adapter=adapter,
    )

    job = await service.create(
        model_id="bytedance/seedance-2.0-fast",
        prompt="Preserve these three character references",
        reference_image_filenames=["one.png", "two.png", "three.png"],
        reference_image_content_types=[
            "image/png",
            "image/png",
            "image/png",
        ],
        reference_image_contents=[
            PNG + b"-one",
            PNG + b"-two",
            PNG + b"-three",
        ],
        idempotency_key="three-references-0001",
    )

    references = adapter.submit_calls[0]["input_references"]
    assert isinstance(references, list)
    assert len(references) == 3
    assert all(
        reference["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
        for reference in references
    )
    assert job.parameters.reference_image_count == 3

    with pytest.raises(MultimodalServiceError) as caught:
        await service.create(
            model_id="bytedance/seedance-2.0-fast",
            prompt="This must not be submitted",
            reference_image_filenames=[f"{index}.png" for index in range(4)],
            reference_image_content_types=["image/png"] * 4,
            reference_image_contents=[PNG] * 4,
            idempotency_key="four-references-0001",
        )
    assert caught.value.code == "too_many_reference_images"
    assert len(adapter.submit_calls) == 1


@pytest.mark.asyncio
async def test_provider_options_require_fresh_audited_capability(
    tmp_path: Path,
) -> None:
    router_instance = router_service(tmp_path)
    adapter = FakeAdapter()
    catalog = StubCatalog(
        router_instance,
        model_id="google/veo-3.1-lite",
        provider_options=[
            VideoProviderOption(
                key="negativePrompt",
                label="排除内容",
                type="text",
            ),
            VideoProviderOption(
                key="enhancePrompt",
                label="自动增强提示词",
                type="boolean",
            ),
        ],
    )
    service = VideoJobService(
        router_instance,
        catalog,
        adapter=adapter,
    )

    job = await service.create(
        model_id="google/veo-3.1-lite",
        prompt="A clean studio product shot",
        provider_options={
            "negativePrompt": "watermark-secret-value",
            "enhancePrompt": True,
        },
        idempotency_key="provider-options-0001",
    )

    assert catalog.force_requests == [True]
    assert adapter.submit_calls[0]["provider"] == {
        "options": {
            "google-vertex": {
                "parameters": {
                    "negativePrompt": "watermark-secret-value",
                    "enhancePrompt": True,
                }
            }
        }
    }
    assert job.parameters.provider_option_keys == [
        "enhancePrompt",
        "negativePrompt",
    ]
    with sqlite3.connect(
        service.router_service.repository.database_path
    ) as connection:
        dump = "\n".join(connection.iterdump())
    assert "watermark-secret-value" not in dump
    assert "clean studio" not in dump.lower()
    assert "negativePrompt" in dump

    with pytest.raises(MultimodalServiceError) as caught:
        await service.create(
            model_id="google/veo-3.1-lite",
            prompt="No paid request",
            provider_options={"arbitrary": {"nested": True}},
            idempotency_key="invalid-provider-options-0001",
        )
    assert caught.value.code == "provider_option_unavailable"
    assert len(adapter.submit_calls) == 1


@pytest.mark.asyncio
async def test_provider_options_reject_stale_capability_before_submit(
    tmp_path: Path,
) -> None:
    router_instance = router_service(tmp_path)
    adapter = FakeAdapter()
    catalog = StubCatalog(
        router_instance,
        model_id="google/veo-3.1-lite",
        provider_options=[
            VideoProviderOption(
                key="enhancePrompt",
                label="自动增强提示词",
                type="boolean",
            )
        ],
        catalog_status="stale",
    )
    service = VideoJobService(
        router_instance,
        catalog,
        adapter=adapter,
    )

    with pytest.raises(MultimodalServiceError) as caught:
        await service.create(
            model_id="google/veo-3.1-lite",
            prompt="Do not create a paid task",
            provider_options={"enhancePrompt": True},
            idempotency_key="stale-provider-options-0001",
        )

    assert caught.value.code == "provider_options_not_verified"
    assert catalog.force_requests == [True]
    assert adapter.submit_calls == []


@pytest.mark.asyncio
async def test_refresh_survives_service_restart_and_maps_statuses(
    tmp_path: Path,
) -> None:
    first_service, first_adapter = job_service(tmp_path)
    created = await first_service.create(
        model_id="google/veo-test",
        prompt="A lantern floating above a city",
        idempotency_key="restart-job-0001",
    )
    assert len(first_adapter.submit_calls) == 1

    restarted_router = ModelRouterService(
        SQLiteRouterRepository(tmp_path)
    )
    restarted_adapter = FakeAdapter()
    restarted_adapter.poll_responses = [
        {
            "id": "upstream_private_1",
            "status": "in_progress",
            "model": "google/veo-actual",
            "generation_id": "generation-safe-id",
        },
        {
            "id": "upstream_private_1",
            "status": "completed",
            "model": "google/veo-actual",
            "generation_id": "generation-safe-id",
            "unsigned_urls": ["https://private.invalid/signed-output"],
            "usage": {"cost": 0.75},
        },
    ]
    restarted = VideoJobService(
        restarted_router,
        StubCatalog(restarted_router),
        adapter=restarted_adapter,
    )

    running = await restarted.refresh(created.job_id)
    complete = await restarted.refresh(created.job_id)

    assert running.status == "running"
    assert complete.status == "succeeded"
    assert complete.actual_model == "google/veo-actual"
    assert complete.generation_id == "generation-safe-id"
    assert complete.output_count == 1
    assert complete.usage.cost_usd == 0.75
    assert complete.usage.cost_kind == "actual"
    assert "private.invalid" not in complete.model_dump_json()


@pytest.mark.asyncio
async def test_temporary_poll_error_does_not_change_local_status(
    tmp_path: Path,
) -> None:
    service, adapter = job_service(tmp_path)
    created = await service.create(
        model_id="google/veo-test",
        prompt="A slow dolly shot through a library",
        idempotency_key="poll-error-0001",
    )
    adapter.poll_responses = [
        MultimodalServiceError(
            "upstream_timeout",
            "safe timeout",
            status_code=504,
        )
    ]

    with pytest.raises(MultimodalServiceError) as caught:
        await service.refresh(created.job_id)

    assert caught.value.code == "upstream_timeout"
    assert service.get(created.job_id).status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream", "expected_status", "expected_code"),
    [
        ("failed", "failed", "provider_generation_failed"),
        ("cancelled", "cancelled", "provider_generation_cancelled"),
        ("expired", "expired", "provider_generation_expired"),
    ],
)
async def test_terminal_statuses_have_safe_errors(
    tmp_path: Path,
    upstream: str,
    expected_status: str,
    expected_code: str,
) -> None:
    service, adapter = job_service(tmp_path)
    job = await service.create(
        model_id="google/veo-test",
        prompt="A studio product shot",
        idempotency_key=f"terminal-{upstream}-0001",
    )
    adapter.poll_responses = [
        {
            "id": "upstream_private_1",
            "status": upstream,
            "error": "secret provider stack trace",
        }
    ]

    result = await service.refresh(job.job_id)

    assert result.status == expected_status
    assert result.error is not None
    assert result.error.code == expected_code
    assert "secret provider stack trace" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_parameters_are_cross_checked_before_submit(
    tmp_path: Path,
) -> None:
    service, adapter = job_service(tmp_path)

    with pytest.raises(MultimodalServiceError) as caught:
        await service.create(
            model_id="google/veo-test",
            prompt="A test",
            duration=12,
            idempotency_key="bad-duration-0001",
        )
    assert caught.value.code == "unsupported_duration"
    assert adapter.submit_calls == []

    no_frame_router = router_service(tmp_path / "no-frame")
    no_frame = VideoJobService(
        no_frame_router,
        StubCatalog(no_frame_router, supports_first_frame=False),
        adapter=adapter,
    )
    with pytest.raises(MultimodalServiceError) as caught:
        await no_frame.create(
            model_id="google/veo-test",
            prompt="A test",
            first_frame_filename="frame.png",
            first_frame_content_type="image/png",
            first_frame_content=PNG,
            idempotency_key="bad-frame-0001",
        )
    assert caught.value.code == "first_frame_unsupported"
    assert adapter.submit_calls == []


@pytest.mark.asyncio
async def test_invalid_first_frame_magic_is_rejected(
    tmp_path: Path,
) -> None:
    service, adapter = job_service(tmp_path)
    with pytest.raises(MultimodalServiceError) as caught:
        await service.create(
            model_id="google/veo-test",
            prompt="A test",
            first_frame_filename="frame.png",
            first_frame_content_type="image/png",
            first_frame_content=b"<html>not an image</html>",
            idempotency_key="bad-magic-0001",
        )
    assert caught.value.code == "invalid_first_frame"
    assert adapter.submit_calls == []


@pytest.mark.asyncio
async def test_api_accepts_last_frame_and_repeated_reference_images(
    tmp_path: Path,
) -> None:
    router_instance = router_service(tmp_path)
    adapter = FakeAdapter()
    service = VideoJobService(
        router_instance,
        StubCatalog(
            router_instance,
            model_id="bytedance/seedance-2.0-fast",
            supports_reference_images=True,
            max_reference_images=3,
        ),
        adapter=adapter,
    )
    configure_video_job_service(service)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            created = await client.post(
                "/api/multimodal/video/jobs",
                data={
                    "model_id": "bytedance/seedance-2.0-fast",
                    "prompt": "Animate between frames and references",
                    "idempotency_key": "api-enhanced-video-0001",
                },
                files=[
                    (
                        "last_frame",
                        ("last.png", PNG + b"-last", "image/png"),
                    ),
                    (
                        "reference_images",
                        ("one.png", PNG + b"-one", "image/png"),
                    ),
                    (
                        "reference_images",
                        ("two.png", PNG + b"-two", "image/png"),
                    ),
                ],
            )
            malformed = await client.post(
                "/api/multimodal/video/jobs",
                data={
                    "model_id": "bytedance/seedance-2.0-fast",
                    "prompt": "Do not submit",
                    "idempotency_key": "api-invalid-options-0001",
                    "provider_options": "[\"arbitrary\"]",
                },
            )
    finally:
        configure_video_job_service(None)

    assert created.status_code == 200
    assert created.json()["parameters"]["has_last_frame"] is True
    assert created.json()["parameters"]["reference_image_count"] == 2
    payload = adapter.submit_calls[0]
    assert payload["frame_images"][0]["frame_type"] == "last_frame"
    assert len(payload["input_references"]) == 2
    assert malformed.status_code == 422
    assert (
        malformed.json()["detail"]["code"]
        == "invalid_provider_options"
    )
    assert len(adapter.submit_calls) == 1


@pytest.mark.asyncio
async def test_api_lists_streams_and_removes_only_local_record(
    tmp_path: Path,
) -> None:
    service, adapter = job_service(tmp_path)
    adapter.poll_responses = [
        {
            "id": "upstream_private_1",
            "status": "completed",
            "unsigned_urls": ["https://private.invalid/video.mp4"],
        }
    ]
    configure_video_job_service(service)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            created = await client.post(
                "/api/multimodal/video/jobs",
                data={
                    "model_id": "google/veo-test",
                    "prompt": "A cinematic sunrise",
                    "idempotency_key": "api-create-0001",
                    "duration": "5",
                    "resolution": "720p",
                },
            )
            job_id = created.json()["job_id"]
            refreshed = await client.post(
                f"/api/multimodal/video/jobs/{job_id}/refresh"
            )
            listed = await client.get("/api/multimodal/video/jobs")
            content = await client.get(
                f"/api/multimodal/video/jobs/{job_id}/content?index=0"
            )
            removed = await client.delete(
                f"/api/multimodal/video/jobs/{job_id}"
            )
            missing = await client.get(
                f"/api/multimodal/video/jobs/{job_id}"
            )
    finally:
        configure_video_job_service(None)

    assert created.status_code == 200
    assert refreshed.json()["status"] == "succeeded"
    assert listed.json()["jobs"][0]["job_id"] == job_id
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("video/mp4")
    assert content.content == VIDEO
    assert "private.invalid" not in content.text
    assert adapter.content_calls == [("upstream_private_1", 0)]
    assert removed.json() == {
        "removed": True,
        "upstream_cancelled": False,
    }
    assert missing.status_code == 404


def test_repository_enforces_video_job_tenant_isolation(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    row, created = repository.create_video_job_if_absent(
        "tenant-a",
        job_id="local_a",
        idempotency_key_hash="same-hash",
        connection_id=None,
        requested_model="provider/video",
        provider="openrouter",
        duration=None,
        resolution=None,
        aspect_ratio=None,
        generate_audio=False,
        seed=None,
        has_first_frame=False,
    )
    other, other_created = repository.create_video_job_if_absent(
        "tenant-b",
        job_id="local_b",
        idempotency_key_hash="same-hash",
        connection_id=None,
        requested_model="provider/video",
        provider="openrouter",
        duration=None,
        resolution=None,
        aspect_ratio=None,
        generate_audio=False,
        seed=None,
        has_first_frame=False,
    )

    assert created is True and other_created is True
    assert row["id"] == "local_a"
    assert other["id"] == "local_b"
    assert repository.get_video_job("tenant-a", "local_b") is None
    assert repository.delete_video_job("tenant-b", "local_a") is False
    assert repository.count_schema_tenant_columns()["video_jobs"] is True


def test_repository_migrates_existing_video_jobs_without_data_loss(
    tmp_path: Path,
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    database_path = tmp_path / "router.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE video_jobs (
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                idempotency_key_hash TEXT NOT NULL,
                decision_id TEXT,
                connection_id TEXT,
                requested_model TEXT NOT NULL,
                actual_model TEXT,
                provider TEXT NOT NULL DEFAULT 'openrouter',
                upstream_job_id TEXT,
                generation_id TEXT,
                status TEXT NOT NULL,
                duration INTEGER,
                resolution TEXT,
                aspect_ratio TEXT,
                generate_audio INTEGER NOT NULL DEFAULT 0,
                seed INTEGER,
                has_first_frame INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL,
                cost_kind TEXT NOT NULL DEFAULT 'unavailable',
                error_code TEXT,
                output_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, id),
                UNIQUE (tenant_id, idempotency_key_hash)
            );
            INSERT INTO video_jobs (
                id, tenant_id, idempotency_key_hash, requested_model,
                provider, status, has_first_frame, created_at, updated_at
            ) VALUES (
                'local_existing', 'local', 'existing-hash',
                'provider/video', 'openrouter', 'queued', 1,
                '2026-07-29T00:00:00+00:00',
                '2026-07-29T00:00:00+00:00'
            );
            PRAGMA user_version = 6;
            """
        )

    repository = SQLiteRouterRepository(tmp_path)
    row = repository.get_video_job("local", "local_existing")
    assert row is not None
    assert row["has_first_frame"] == 1
    assert row["has_last_frame"] == 0
    assert row["reference_image_count"] == 0
    assert row["provider_option_keys"] == "[]"
    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_http", "expected_code"),
    [
        (401, 502, "provider_credentials_invalid"),
        (402, 402, "provider_quota_exceeded"),
        (429, 429, "provider_rate_limited"),
        (503, 502, "provider_unavailable"),
    ],
)
async def test_adapter_translates_upstream_errors_without_raw_body(
    status: int,
    expected_http: int,
    expected_code: str,
) -> None:
    def handler(_: Request) -> Response:
        return Response(status, text="secret upstream stack and signed URL")

    adapter = OpenRouterVideoJobAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        )
    )
    target = OpenRouterTarget(
        base_url="https://openrouter.ai/api/v1",
        api_key="never-expose-this",
        connection_id=None,
        cache_key="test",
    )

    with pytest.raises(MultimodalServiceError) as caught:
        await adapter.submit(
            target,
            {"model": "provider/video", "prompt": "safe prompt"},
        )

    assert caught.value.status_code == expected_http
    assert caught.value.code == expected_code
    assert "secret upstream" not in caught.value.message
    assert "never-expose-this" not in caught.value.message


@pytest.mark.asyncio
async def test_adapter_content_uses_authenticated_derived_endpoint() -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return Response(
            200,
            content=VIDEO,
            headers={
                "content-type": "video/mp4",
                "content-length": str(len(VIDEO)),
            },
        )

    adapter = OpenRouterVideoJobAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        )
    )
    target = OpenRouterTarget(
        base_url="https://openrouter.ai/api/v1",
        api_key="content-proxy-secret",
        connection_id=None,
        cache_key="test",
    )

    content = await adapter.content(target, "upstream_123", index=0)
    received = b"".join([chunk async for chunk in content.chunks])

    assert received == VIDEO
    assert content.media_type == "video/mp4"
    assert content.content_length == len(VIDEO)
    assert requests[0].url.path == (
        "/api/v1/videos/upstream_123/content"
    )
    assert requests[0].url.params["index"] == "0"
    assert requests[0].headers["authorization"] == (
        "Bearer content-proxy-secret"
    )
