from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from server import main as main_module
from server.main import app
from server.model_router import (
    ModelRouterService,
    RouterConnectionCreate,
    SQLiteRouterRepository,
    configure_model_router,
    get_model_router_service,
)
from server.multimodal.api import (
    configure_chat_attachment_store,
    configure_video_analysis_service,
)
from server.multimodal.chat_attachments import ChatAttachmentStore
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget
from server.multimodal.video_analysis import (
    VideoAnalysisService,
    VideoAnalysisUsage,
)
from server.multimodal.video_catalog import (
    VideoModelCatalogResponse,
    VideoModelProfile,
)


MP4_BYTES = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 24


class FakeVideoCatalog:
    def __init__(self, target: OpenRouterTarget) -> None:
        self.target = target

    def _enabled(self, _: str) -> bool:
        return True

    def resolve_target(self) -> OpenRouterTarget:
        return self.target

    async def get_catalog(self) -> VideoModelCatalogResponse:
        return VideoModelCatalogResponse(
            source="openrouter",
            status="online",
            stale=False,
            synced_at="2026-07-29T00:00:00+00:00",
            profiles=[
                VideoModelProfile(
                    model_id="google/video-understanding",
                    operation="analyze_video",
                    supported_input_sources=["file", "url"],
                    interaction_status="ready",
                )
            ],
        )


def configure_video_chat_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    adapter: Any,
) -> tuple[ModelRouterService, ChatAttachmentStore]:
    monkeypatch.setenv("MULTIMODAL_CHAT_VIDEO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_VIDEO_ANALYSIS_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)

    repository = SQLiteRouterRepository(tmp_path / "router")
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="OpenRouter video",
            kind="openrouter",
            base_url="https://video.example/api/v1",
            api_key="private-video-key",
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-07-29T00:00:00+00:00",
        error_code=None,
        error_hint=None,
    )
    router_service = ModelRouterService(repository)
    target = OpenRouterTarget(
        base_url="https://video.example/api/v1",
        api_key="private-video-key",
        connection_id=connection.id,
        cache_key="test-video",
    )
    analysis_service = VideoAnalysisService(
        router_service,
        FakeVideoCatalog(target),  # type: ignore[arg-type]
        adapter=adapter,
    )
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    configure_model_router(router_service)
    configure_video_analysis_service(analysis_service)
    configure_chat_attachment_store(store)
    return router_service, store


def create_video_attachment(store: ChatAttachmentStore) -> str:
    return store.create(
        kind="video",
        filename="private-clip.mp4",
        content_type="video/mp4",
        content=MP4_BYTES,
    ).attachment_id


def video_chat_payload(
    attachment_id: str,
    *,
    model_id: str = "google/video-understanding",
    gateway: str = "default",
    tool_mode: str = "none",
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "gateway": gateway,
        "tool_mode": tool_mode,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请说明视频里的关键事件。"},
                    {
                        "type": "input_video",
                        "attachment_id": attachment_id,
                    },
                ],
            }
        ],
    }


def assert_attachment_retryable(
    store: ChatAttachmentStore,
    attachment_id: str,
) -> None:
    attachment = store.claim(attachment_id, expected_kind="video")
    assert attachment.attachment_id == attachment_id
    store.release_for_retry(attachment_id)


@pytest.mark.asyncio
async def test_direct_video_chat_returns_sse_receipt_and_consumes_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeAdapter:
        async def analyze(self, target, **kwargs):
            calls.append({"target": target, **kwargs})
            return (
                "视频中先出现道路，随后一辆汽车驶入画面。",
                "google/video-understanding:exact",
                VideoAnalysisUsage(
                    input_tokens=18,
                    output_tokens=12,
                    total_tokens=30,
                    cost_usd=0.002,
                    cost_kind="actual",
                ),
            )

    original_service = get_model_router_service()
    service, store = configure_video_chat_services(
        tmp_path,
        monkeypatch,
        adapter=FakeAdapter(),
    )
    attachment_id = create_video_attachment(store)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/chat",
                json=video_chat_payload(attachment_id),
            )

        assert response.status_code == 200, response.text
        assert len(calls) == 1
        assert calls[0]["model_id"] == "google/video-understanding"
        assert calls[0]["prompt"] == "请说明视频里的关键事件。"
        assert calls[0]["video_source"].startswith(
            "data:video/mp4;base64,"
        )
        assert "视频中先出现道路" in response.text
        assert response.text.count("event: route_receipt") == 1
        assert response.text.count("data: [DONE]") == 1
        receipt_event = next(
            event
            for event in response.text.split("\n\n")
            if event.startswith("event: route_receipt")
        )
        receipt = json.loads(
            next(
                line.removeprefix("data:").strip()
                for line in receipt_event.splitlines()
                if line.startswith("data:")
            )
        )
        assert receipt["actual_model"] == (
            "google/video-understanding:exact"
        )
        assert receipt["tokens"]["total"] == 30
        assert receipt["media"] == {
            "input_kind": "video",
            "processing": "direct",
            "format": "mp4",
            "raw_retained": False,
        }
        with pytest.raises(MultimodalServiceError) as consumed:
            store.claim(attachment_id)
        assert consumed.value.code == "attachment_not_found"
        decision = service.diagnostics()["recent_decisions"][0]
        assert decision["operation"] == "analyze_video"
        assert decision["outcome"] == "success"
        serialized = json.dumps(service.diagnostics(), ensure_ascii=False)
        assert "private-video-key" not in serialized
        assert "请说明视频里的关键事件" not in serialized
    finally:
        configure_video_analysis_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_id", "gateway", "tool_mode", "expected_status"),
    [
        ("auto", "auto", "none", 422),
        ("google/video-understanding", "default", "mcp_tools", 400),
    ],
)
async def test_direct_video_rejects_auto_and_tool_mode_before_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    gateway: str,
    tool_mode: str,
    expected_status: int,
) -> None:
    calls = 0

    class FakeAdapter:
        async def analyze(self, target, **kwargs):
            nonlocal calls
            calls += 1
            return "unexpected", kwargs["model_id"], VideoAnalysisUsage()

    original_service = get_model_router_service()
    _, store = configure_video_chat_services(
        tmp_path,
        monkeypatch,
        adapter=FakeAdapter(),
    )
    attachment_id = create_video_attachment(store)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/chat",
                json=video_chat_payload(
                    attachment_id,
                    model_id=model_id,
                    gateway=gateway,
                    tool_mode=tool_mode,
                ),
            )

        assert response.status_code == expected_status
        assert calls == 0
        assert_attachment_retryable(store, attachment_id)
    finally:
        configure_video_analysis_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
async def test_direct_video_failure_keeps_attachment_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAdapter:
        async def analyze(self, target, **kwargs):
            raise MultimodalServiceError(
                "provider_rate_limited",
                "视频理解请求较多，请稍后重试。",
                status_code=429,
            )

    original_service = get_model_router_service()
    _, store = configure_video_chat_services(
        tmp_path,
        monkeypatch,
        adapter=FailingAdapter(),
    )
    attachment_id = create_video_attachment(store)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/chat",
                json=video_chat_payload(attachment_id),
            )

        assert response.status_code == 429
        assert response.json()["code"] == "provider_rate_limited"
        assert_attachment_retryable(store, attachment_id)
    finally:
        configure_video_analysis_service(None)
        configure_chat_attachment_store(None)
        configure_model_router(original_service)
