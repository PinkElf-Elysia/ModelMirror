from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.api import (
    configure_video_analysis_service,
    router,
)
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget
from server.multimodal.video_analysis import (
    MAX_VIDEO_BYTES,
    OpenRouterVideoAnalysisAdapter,
    VideoAnalysisService,
    VideoAnalysisUsage,
)
from server.multimodal.video_catalog import (
    VideoModelCatalogResponse,
    VideoModelProfile,
)


MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24
MOV_BYTES = b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 24
WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
MPEG_BYTES = b"\x00\x00\x01\xba" + b"\x00" * 32


def router_service(tmp_path: Path) -> ModelRouterService:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter",
            kind="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="private-video-key",
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
    return ModelRouterService(repository)


class FakeCatalog:
    def __init__(
        self,
        target: OpenRouterTarget,
        *,
        enabled: bool = True,
        status: str = "online",
    ) -> None:
        self.target = target
        self.enabled = enabled
        self.status = status

    def _enabled(self, _: str) -> bool:
        return self.enabled

    def resolve_target(self) -> OpenRouterTarget:
        return self.target

    async def get_catalog(self) -> VideoModelCatalogResponse:
        return VideoModelCatalogResponse(
            source="openrouter",
            status=self.status,  # type: ignore[arg-type]
            stale=self.status == "stale",
            synced_at="2026-07-28T00:00:00+00:00",
            profiles=[
                VideoModelProfile(
                    model_id="google/video-understanding",
                    operation="analyze_video",
                    supported_input_sources=["file", "url"],
                )
            ],
        )


def fake_catalog(**kwargs: Any) -> FakeCatalog:
    return FakeCatalog(
        OpenRouterTarget(
            base_url="https://openrouter.ai/api/v1",
            api_key="private-video-key",
            connection_id="connection_test",
            cache_key="test",
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_video_file_analysis_uses_data_url_and_safe_audit(
    tmp_path: Path,
) -> None:
    model_router = router_service(tmp_path)
    captured: dict[str, Any] = {}

    class FakeAdapter:
        async def analyze(self, target, **kwargs):
            captured["target"] = target
            captured["kwargs"] = kwargs
            return (
                "视频中有一辆汽车。",
                "google/video-understanding",
                VideoAnalysisUsage(
                    input_tokens=20,
                    output_tokens=8,
                    total_tokens=28,
                    cost_usd=0.01,
                    cost_kind="actual",
                ),
            )

    service = VideoAnalysisService(
        model_router,
        fake_catalog(),  # type: ignore[arg-type]
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    result = await service.analyze(
        model_id="google/video-understanding",
        prompt="请描述视频内容",
        source_type="file",
        filename="private-trip.mp4",
        content_type="video/mp4",
        content=MP4_BYTES,
    )

    assert result.text == "视频中有一辆汽车。"
    assert result.source_kind == "file"
    assert captured["kwargs"]["video_source"].startswith(
        "data:video/mp4;base64,"
    )
    diagnostics = model_router.diagnostics()
    decision = diagnostics["recent_decisions"][0]
    assert decision["operation"] == "analyze_video"
    assert decision["input_bytes"] == len(MP4_BYTES)
    assert decision["outcome"] == "success"
    assert decision["budget"]["settled_cost_usd"] == 0.01
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "private-video-key" not in serialized
    assert "private-trip.mp4" not in serialized
    assert "请描述视频内容" not in serialized
    assert "视频中有一辆汽车" not in serialized


@pytest.mark.asyncio
async def test_https_url_is_forwarded_without_local_fetch(
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    class FakeAdapter:
        async def analyze(self, target, **kwargs):
            captured["source"] = kwargs["video_source"]
            return "result", kwargs["model_id"], VideoAnalysisUsage()

    service = VideoAnalysisService(
        router_service(tmp_path),
        fake_catalog(),  # type: ignore[arg-type]
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    url = "https://www.youtube.com/watch?v=safe-test"
    result = await service.analyze(
        model_id="google/video-understanding",
        prompt="总结视频",
        source_type="url",
        video_url=url,
    )

    assert result.source_kind == "url"
    assert captured["source"] == url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "content", "expected_mime"),
    [
        ("a.mp4", "video/mp4", MP4_BYTES, "video/mp4"),
        ("a.mov", "video/quicktime", MOV_BYTES, "video/quicktime"),
        ("a.webm", "video/webm", WEBM_BYTES, "video/webm"),
        ("a.mpeg", "video/mpeg", MPEG_BYTES, "video/mpeg"),
    ],
)
async def test_supported_video_formats(
    tmp_path: Path,
    filename: str,
    content_type: str,
    content: bytes,
    expected_mime: str,
) -> None:
    sources: list[str] = []

    class FakeAdapter:
        async def analyze(self, target, **kwargs):
            sources.append(kwargs["video_source"])
            return "ok", kwargs["model_id"], VideoAnalysisUsage()

    service = VideoAnalysisService(
        router_service(tmp_path),
        fake_catalog(),  # type: ignore[arg-type]
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    await service.analyze(
        model_id="google/video-understanding",
        prompt="分析",
        source_type="file",
        filename=filename,
        content_type=content_type,
        content=content,
    )
    assert sources[0].startswith(f"data:{expected_mime};base64,")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "code", "status"),
    [
        (
            {
                "source_type": "file",
                "filename": "bad.mp4",
                "content_type": "video/mp4",
                "content": b"not-a-video",
            },
            "invalid_video_file",
            422,
        ),
        (
            {
                "source_type": "file",
                "filename": "bad.avi",
                "content_type": "video/x-msvideo",
                "content": b"RIFF" + b"\0" * 16,
            },
            "unsupported_video_format",
            422,
        ),
        (
            {
                "source_type": "url",
                "video_url": "http://example.com/video.mp4",
            },
            "invalid_video_url",
            422,
        ),
        (
            {
                "source_type": "url",
                "video_url": "https://user:pass@example.com/video.mp4",
            },
            "invalid_video_url",
            422,
        ),
    ],
)
async def test_invalid_inputs_are_rejected_before_upstream(
    tmp_path: Path,
    kwargs: dict[str, Any],
    code: str,
    status: int,
) -> None:
    service = VideoAnalysisService(
        router_service(tmp_path),
        fake_catalog(),  # type: ignore[arg-type]
    )
    with pytest.raises(MultimodalServiceError) as error:
        await service.analyze(
            model_id="google/video-understanding",
            prompt="分析",
            **kwargs,
        )
    assert error.value.code == code
    assert error.value.status_code == status


@pytest.mark.asyncio
async def test_oversized_video_is_rejected(tmp_path: Path) -> None:
    service = VideoAnalysisService(
        router_service(tmp_path),
        fake_catalog(),  # type: ignore[arg-type]
    )
    with pytest.raises(MultimodalServiceError) as error:
        await service.analyze(
            model_id="google/video-understanding",
            prompt="分析",
            source_type="file",
            filename="large.mp4",
            content_type="video/mp4",
            content=b"\0\0\0\x18ftyp" + b"\0" * MAX_VIDEO_BYTES,
        )
    assert error.value.code == "video_too_large"
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_wrong_model_and_offline_catalog_are_explicit(
    tmp_path: Path,
) -> None:
    model_router = router_service(tmp_path)
    service = VideoAnalysisService(
        model_router,
        fake_catalog(),  # type: ignore[arg-type]
    )
    with pytest.raises(MultimodalServiceError) as mismatch:
        await service.analyze(
            model_id="openai/text-only",
            prompt="分析",
            source_type="url",
            video_url="https://example.com/video.mp4",
        )
    assert mismatch.value.code == "operation_mismatch"

    offline = VideoAnalysisService(
        model_router,
        fake_catalog(status="offline"),  # type: ignore[arg-type]
    )
    with pytest.raises(MultimodalServiceError) as unavailable:
        await offline.analyze(
            model_id="google/video-understanding",
            prompt="分析",
            source_type="url",
            video_url="https://example.com/video.mp4",
        )
    assert unavailable.value.code == "video_catalog_unavailable"
    assert unavailable.value.status_code == 503


@pytest.mark.asyncio
async def test_openrouter_adapter_contract_and_content_parts() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: Request) -> Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["json"] = json.loads((await request.aread()).decode())
        return Response(
            200,
            json={
                "id": "generation_safe",
                "model": "google/video-understanding:exact",
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一段"},
                                {"type": "text", "text": "第二段"},
                            ]
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "cost": 0.02,
                },
            },
        )

    adapter = OpenRouterVideoAnalysisAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        )
    )
    text, actual_model, usage = await adapter.analyze(
        fake_catalog().target,
        model_id="google/video-understanding",
        prompt="描述",
        video_source="https://example.com/video.mp4",
    )

    assert captured["url"].endswith("/api/v1/chat/completions")
    assert captured["authorization"] == "Bearer private-video-key"
    assert captured["json"]["stream"] is False
    parts = captured["json"]["messages"][0]["content"]
    assert parts[1] == {
        "type": "video_url",
        "video_url": {"url": "https://example.com/video.mp4"},
    }
    assert text == "第一段\n第二段"
    assert actual_model == "google/video-understanding:exact"
    assert usage.total_tokens == 14
    assert usage.cost_kind == "actual"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_status", "code", "public_status"),
    [
        (401, "provider_credentials_invalid", 502),
        (402, "provider_quota_exceeded", 402),
        (413, "provider_file_too_large", 413),
        (429, "provider_rate_limited", 429),
        (500, "provider_unavailable", 502),
    ],
)
async def test_upstream_errors_are_translated_without_raw_body(
    upstream_status: int,
    code: str,
    public_status: int,
) -> None:
    def handler(_: Request) -> Response:
        return Response(upstream_status, text="secret upstream diagnostics")

    adapter = OpenRouterVideoAnalysisAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(handler)
        )
    )
    with pytest.raises(MultimodalServiceError) as error:
        await adapter.analyze(
            fake_catalog().target,
            model_id="google/video-understanding",
            prompt="描述",
            video_source="https://example.com/video.mp4",
        )
    assert error.value.code == code
    assert error.value.status_code == public_status
    assert "secret upstream diagnostics" not in error.value.message


@pytest.mark.asyncio
async def test_video_analysis_multipart_api(tmp_path: Path) -> None:
    class FakeAdapter:
        async def analyze(self, target, **kwargs):
            return "API result", kwargs["model_id"], VideoAnalysisUsage()

    service = VideoAnalysisService(
        router_service(tmp_path),
        fake_catalog(),  # type: ignore[arg-type]
        adapter=FakeAdapter(),  # type: ignore[arg-type]
    )
    configure_video_analysis_service(service)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/multimodal/video/analysis",
                data={
                    "model_id": "google/video-understanding",
                    "prompt": "描述",
                    "source_type": "file",
                },
                files={"file": ("clip.mp4", MP4_BYTES, "video/mp4")},
            )
    finally:
        configure_video_analysis_service(None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "API result"
    assert payload["source_kind"] == "file"
    assert payload["request_id"].startswith("decision_")
    assert "private-video-key" not in response.text
