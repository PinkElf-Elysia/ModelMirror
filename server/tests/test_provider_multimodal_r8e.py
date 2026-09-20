from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from server import main as main_module
from server.main import app
from server.model_router import r8e_video_fixture as video_fixture
from server.model_router import (
    configure_model_router,
    get_model_router_service,
)
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.multimodal_control import (
    R8E_VIDEO_GENERATION_ASPECT_RATIO,
    R8E_VIDEO_GENERATION_DURATION_SECONDS,
    R8E_VIDEO_GENERATION_RESOLUTION,
    ProviderMultimodalTarget,
    ProviderMultimodalTransport,
)
from server.model_router.multimodal_gateway import (
    ManagedMultimodalChatStreamEvidence,
    ManagedMultimodalError,
    ManagedMultimodalGateway,
)
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import (
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget
from server.multimodal.api import configure_chat_attachment_store
from server.multimodal.chat_attachments import ChatAttachmentStore
from server.multimodal.video_analysis import (
    VideoAnalysisService,
    VideoAnalysisUsage,
)
from server.multimodal.video_catalog import (
    VideoModelCatalogResponse,
    VideoModelProfile,
)
from server.multimodal.video_jobs import (
    MAX_VIDEO_JOB_RESPONSE_BYTES,
    OpenRouterVideoJobAdapter,
    VideoJobService,
)


MODEL_ID = "provider/video-model"
CANONICAL_MODEL_ID = "provider/video-model-20260919"


def _service(
    tmp_path: Path,
    transport: httpx.AsyncBaseTransport,
) -> tuple[ModelRouterService, object]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8E video",
            kind="openrouter",
            base_url="https://provider.example/api/v1",
            api_key="r8e-secret",
            scopes=["chat", "video"],
        ),
    )
    service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8", "1.1.1.1"]
        ),
    )
    return service, connection


def _certification_service(
    service: ModelRouterService,
    transport: httpx.AsyncBaseTransport,
) -> ProviderWorkloadCertificationService:
    return ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )


def _payload(shape: str) -> ProviderWorkloadCertificationRequest:
    return ProviderWorkloadCertificationRequest(
        execution_shape=shape,  # type: ignore[arg-type]
        model_id=MODEL_ID,
        adapter_contract="openrouter_chat_video_v1",
        acknowledge_billed_call=True,
    )


def _video_generation_payload() -> ProviderWorkloadCertificationRequest:
    return ProviderWorkloadCertificationRequest(
        execution_shape="video_generation_async",
        model_id=MODEL_ID,
        adapter_contract="openrouter_video_jobs_v1",
        acknowledge_billed_call=True,
    )


class _VideoCatalogStub:
    @staticmethod
    def _enabled(_name: str) -> bool:
        return True


async def _activate_video_analysis(
    service: ModelRouterService,
    connection: object,
    certification_transport: httpx.AsyncBaseTransport,
    runtime_transport: httpx.AsyncBaseTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> ManagedMultimodalGateway:
    certification = await _certification_service(
        service, certification_transport
    ).run(
        connection.id,
        _payload("video_analysis_unary"),
        idempotency_key="r8e-video-analysis-certification",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_VIDEO_ANALYSIS_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "multimodal_video_analysis",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="video_analysis_unary",
                    model_id=MODEL_ID,
                    connection_id=connection.id,
                    adapter_contract="openrouter_chat_video_v1",
                )
            ],
        ),
    )
    activated = control.activate(
        "multimodal_video_analysis",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert activated.effective_status == "managed_required"
    return ManagedMultimodalGateway.for_router(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=runtime_transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )


async def _activate_chat_video(
    service: ModelRouterService,
    connection: object,
    certification_transport: httpx.AsyncBaseTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certification = await _certification_service(
        service, certification_transport
    ).run(
        connection.id,
        _payload("chat_video_stream"),
        idempotency_key="r8e-chat-video-certification",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_CHAT_VIDEO_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "chat_video",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="chat_video_stream",
                    model_id=MODEL_ID,
                    connection_id=connection.id,
                    adapter_contract="openrouter_chat_video_v1",
                )
            ],
        ),
    )
    activated = control.activate(
        "chat_video",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert activated.effective_status == "managed_required"


async def _activate_video_generation(
    service: ModelRouterService,
    connection: object,
    certification_transport: httpx.AsyncBaseTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certifications = _certification_service(service, certification_transport)
    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-activation",
    )
    passed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )
    assert passed.status == "passed"

    monkeypatch.setenv("MODEL_CONTROL_VIDEO_GENERATION_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "video_generation",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="video_generation_async",
                    model_id=MODEL_ID,
                    connection_id=connection.id,
                    adapter_contract="openrouter_video_jobs_v1",
                )
            ],
        ),
    )
    activated = control.activate(
        "video_generation",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert activated.effective_status == "managed_required"


class _VideoChatClient:
    def __init__(
        self,
        sent: list[dict[str, object]],
        *,
        status_code: int = 200,
        include_model: bool = True,
        transport_error: Exception | None = None,
        content_override: bytes | None = None,
    ) -> None:
        self.sent = sent
        self.status_code = status_code
        self.include_model = include_model
        self.transport_error = transport_error
        self.content_override = content_override

    def build_request(self, method, url, **kwargs):
        return {"method": method, "url": str(url), **kwargs}

    async def send(self, request, *, stream, follow_redirects=False):
        assert stream is True
        assert follow_redirects is False
        self.sent.append(request)
        if self.transport_error is not None:
            raise self.transport_error
        model_field = f'"model":"{MODEL_ID}",' if self.include_model else ""
        content = self.content_override or (
            (
                "data: {"
                + model_field
                + '"choices":[{"delta":{"content":"A blue square."},'
                '"finish_reason":null}]}\n\n'
                "data: {"
                + model_field
                + '"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ).encode("utf-8")
            if self.status_code < 400
            else b'{"error":{"message":"redacted-upstream-body"}}'
        )
        return httpx.Response(
            self.status_code,
            content=content,
            headers={"content-type": "text/event-stream"},
            request=httpx.Request("POST", str(request["url"])),
        )

    async def aclose(self) -> None:
        return None


def _catalog_response(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/videos/models"):
        return httpx.Response(200, json=_catalog_response_payload())
    return httpx.Response(200, json={"data": [{"id": MODEL_ID}]})


def _certification_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _catalog_response(request)
        body = json.loads(request.content)
        if body.get("stream") is True:
            return httpx.Response(
                200,
                content=(
                    f'data: {{"model":"{MODEL_ID}","choices":'
                    '[{"delta":{"content":"A blue square."},'
                    '"finish_reason":null}]}\n\n'
                    'data: {"choices":[{"delta":{},'
                    '"finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ).encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {"message": {"content": "A blue square."}}
                ],
            },
        )

    return httpx.MockTransport(handler)


class _VideoGenerationCatalogStub:
    def __init__(self, router: ModelRouterService) -> None:
        self.router = router

    @staticmethod
    def _enabled(_name: str) -> bool:
        return True

    async def get_catalog(
        self, *, force: bool = False
    ) -> VideoModelCatalogResponse:
        del force
        return VideoModelCatalogResponse(
            source="openrouter",
            status="online",
            stale=False,
            synced_at="2026-09-19T00:00:00+00:00",
            profiles=[
                VideoModelProfile(
                    model_id=MODEL_ID,
                    operation="generate_video",
                    supported_resolutions=[R8E_VIDEO_GENERATION_RESOLUTION],
                    supported_aspect_ratios=[
                        R8E_VIDEO_GENERATION_ASPECT_RATIO
                    ],
                    supported_durations=[
                        R8E_VIDEO_GENERATION_DURATION_SECONDS
                    ],
                    interaction_status="ready",
                    verification_entry_enabled=True,
                )
            ],
        )


class _ForbiddenManagedVideoCatalogStub:
    @staticmethod
    def _enabled(_name: str) -> bool:
        return True

    async def get_catalog(
        self, *, force: bool = False
    ) -> VideoModelCatalogResponse:
        del force
        raise AssertionError(
            "managed video generation must use its exact certified binding"
        )


class _OversizedJSONStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.read_count = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in (
            b"{" + (b"x" * (MAX_VIDEO_JOB_RESPONSE_BYTES - 1)),
            b"x",
            b"must-not-be-read",
        ):
            self.read_count += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _async_video_certification_transport(
    states: list[dict[str, object]],
    requests: list[httpx.Request],
    *,
    catalog_canonical_model_id: str | None = None,
    generation_actual_model: str = MODEL_ID,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith(
            "/videos/models"
        ):
            return httpx.Response(
                200,
                json=_catalog_response_payload(
                    canonical_model_id=catalog_canonical_model_id
                ),
            )
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "provider/unrelated-model"}]},
            )
        if request.method == "POST" and request.url.path.endswith("/videos"):
            body = json.loads(request.content)
            assert body == {
                "model": MODEL_ID,
                "prompt": (
                    "A single blue circle moving slowly on a plain white background."
                ),
                "duration": R8E_VIDEO_GENERATION_DURATION_SECONDS,
                "resolution": R8E_VIDEO_GENERATION_RESOLUTION,
                "aspect_ratio": R8E_VIDEO_GENERATION_ASPECT_RATIO,
            }
            return httpx.Response(
                202,
                json={
                    "id": "video-cert-job-1",
                    "status": "pending",
                    "generation_id": "video-cert-generation-1",
                },
            )
        if request.method == "GET" and request.url.path.endswith(
            "/videos/video-cert-job-1"
        ):
            state = states.pop(0) if len(states) > 1 else states[0]
            return httpx.Response(200, json=state)
        if request.method == "GET" and request.url.path.endswith("/generation"):
            assert request.url.params["id"] == "video-cert-generation-1"
            return httpx.Response(
                200,
                json={"data": {"model": generation_actual_model}},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _runtime_video_jobs(
    service: ModelRouterService,
    transport: httpx.AsyncBaseTransport,
) -> VideoJobService:
    factory = lambda: httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    )
    return VideoJobService(
        service,
        _VideoGenerationCatalogStub(service),  # type: ignore[arg-type]
        adapter=OpenRouterVideoJobAdapter(
            client_factory=factory,
            egress_policy=service.egress_policy,
        ),
        managed_gateway=ManagedMultimodalGateway.for_router(
            service,
            client_factory=factory,
        ),
    )


async def _activated_video_generation_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModelRouterService, object]:
    certification_transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": [
                    "/api/v1/videos/video-cert-job-1/content?index=0"
                ],
            }
        ],
        [],
    )
    service, connection = _service(tmp_path, certification_transport)
    await _activate_video_generation(
        service,
        connection,
        certification_transport,
        monkeypatch,
    )
    return service, connection


def test_r8e_fixture_and_video_job_endpoint_are_fixed() -> None:
    assert len(video_fixture.SYNTHETIC_VIDEO_MP4_BYTES) == 1801
    assert video_fixture.SYNTHETIC_VIDEO_MP4_BYTES[4:8] == b"ftyp"
    assert (
        hashlib.sha256(video_fixture.SYNTHETIC_VIDEO_MP4_BYTES).hexdigest()
        == video_fixture.SYNTHETIC_VIDEO_MP4_SHA256
    )
    target = ProviderMultimodalTarget.create(
        provider_kind="openrouter",
        connection_id="conn-video",
        base_url="https://openrouter.example/api/v1/chat/completions",
        api_key="do-not-print",
        adapter_contract="openrouter_video_jobs_v1",
        execution_shape="video_generation_async",
    )
    assert target.endpoint_url == "https://openrouter.example/api/v1/videos"
    assert "do-not-print" not in repr(target)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "expected_stream"),
    [
        ("video_analysis_unary", False),
        ("chat_video_stream", True),
    ],
)
async def test_r8e_sync_certification_uses_one_exact_video_post(
    tmp_path: Path,
    shape: str,
    expected_stream: bool,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return _catalog_response(request)
        body = json.loads(request.content)
        assert body["model"] == MODEL_ID
        assert body["stream"] is expected_stream
        content = body["messages"][0]["content"]
        assert content[0] == {
            "type": "text",
            "text": "Describe the blue shape in one short sentence.",
        }
        assert content[1]["type"] == "video_url"
        assert (
            content[1]["video_url"]["url"]
            == video_fixture.SYNTHETIC_VIDEO_MP4_DATA_URL
        )
        if expected_stream:
            return httpx.Response(
                200,
                content=(
                    f'data: {{"model":"{MODEL_ID}","choices":'
                    '[{"delta":{"content":"A blue square."},'
                    '"finish_reason":null}]}\n\n'
                    'data: {"choices":[{"delta":{},'
                    '"finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ).encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "message": {"content": "A blue square."},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certification = await _certification_service(service, transport).run(
        connection.id,
        _payload(shape),
        idempotency_key=f"r8e-{shape}",
    )

    assert certification.status == "passed"
    assert certification.can_run is True
    assert certification.actual_model == MODEL_ID
    assert certification.provider_dispatch_state == "confirmed"
    assert certification.retry_allowed is False
    assert certification.checks.media_format_verified is True
    assert certification.checks.actual_model_verified is True
    assert certification.checks.multimodal_adapter_verified is True
    if expected_stream:
        assert certification.checks.terminal_signal_verified is True
        assert certification.checks.safe_terminal_verified is True
    posts = [request for request in requests if request.method == "POST"]
    assert len(posts) == 1
    assert posts[0].url.host in {"8.8.8.8", "1.1.1.1"}
    stored = service.repository.list_workload_certifications("local")[0]
    stored_checks = json.loads(str(stored["checks_json"]))
    assert stored_checks["multimodal_adapter_verified"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_content", "expected_code"),
    [
        (
            (
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n'
            ).encode(),
            "provider_workload_output_truncated",
        ),
        (
            b'data: {"error":{"message":"redacted"}}\n\n',
            "provider_multimodal_upstream_stream_error",
        ),
        (
            (
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
                "event: route_receipt\n"
                'data: {"forged":true}\n\n'
            ).encode(),
            "provider_multimodal_reserved_sse_event",
        ),
        (
            (
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"complete"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"after"},"finish_reason":null}]}\n\n'
            ).encode(),
            "provider_multimodal_invalid_sse",
        ),
        (
            (
                f'data: {{"id":{{"private":"provider-secret-sentinel"}},'
                f'"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"private body"},'
                '"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
            "provider_multimodal_invalid_sse",
        ),
    ],
)
async def test_chat_video_certification_uses_runtime_sse_contract(
    tmp_path: Path,
    upstream_content: bytes,
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return _catalog_response(request)
        return httpx.Response(
            200,
            content=upstream_content,
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certification = await _certification_service(service, transport).run(
        connection.id,
        _payload("chat_video_stream"),
        idempotency_key=f"r8e-strict-stream-{expected_code}",
    )

    assert certification.status == "failed"
    assert certification.can_run is False
    assert certification.error_code == expected_code
    assert certification.checks.safe_terminal_verified is False
    assert len([item for item in requests if item.method == "POST"]) == 1


@pytest.mark.asyncio
async def test_r8e_binding_rejects_tampered_video_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _certification_transport()
    service, connection = _service(tmp_path, transport)
    certification = await _certification_service(service, transport).run(
        connection.id,
        _payload("chat_video_stream"),
        idempotency_key="r8e-tampered-video-evidence",
    )
    assert certification.status == "passed"
    with sqlite3.connect(service.repository.database_path) as database:
        stored = database.execute(
            "SELECT checks_json FROM provider_workload_certifications WHERE id = ?",
            (certification.certification_id,),
        ).fetchone()
        assert stored is not None
        checks = json.loads(str(stored[0]))
        checks["safe_terminal_verified"] = False
        database.execute(
            "UPDATE provider_workload_certifications SET checks_json = ? WHERE id = ?",
            (json.dumps(checks, sort_keys=True), certification.certification_id),
        )

    monkeypatch.setenv("MODEL_CONTROL_CHAT_VIDEO_ENABLED", "true")
    with pytest.raises(RouterServiceError) as blocked:
        ProviderWorkloadControlService(service).update_policy(
            "chat_video",
            ProviderWorkloadPolicyUpdate(
                expected_revision=0,
                bindings=[
                    ProviderWorkloadBindingUpdate(
                        execution_shape="chat_video_stream",
                        model_id=MODEL_ID,
                        connection_id=connection.id,
                        adapter_contract="openrouter_chat_video_v1",
                    )
                ],
            ),
        )

    assert blocked.value.code == "provider_multimodal_video_evidence_incomplete"


@pytest.mark.asyncio
async def test_r8e_certification_rejects_missing_actual_model(
    tmp_path: Path,
) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "GET":
            return _catalog_response(request)
        posts += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "A blue square."}}
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certification = await _certification_service(service, transport).run(
        connection.id,
        _payload("video_analysis_unary"),
        idempotency_key="r8e-missing-model",
    )

    assert posts == 1
    assert certification.status == "failed"
    assert certification.can_run is False
    assert certification.actual_model is None
    assert certification.error_code == "provider_multimodal_actual_model_unverified"


@pytest.mark.asyncio
async def test_video_analysis_certification_rejects_untrusted_model_without_leak(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _catalog_response(request)
        return httpx.Response(
            200,
            json={
                "model": "bad\nprovider-secret-sentinel",
                "choices": [
                    {"message": {"content": "private certification body"}}
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certification = await _certification_service(service, transport).run(
        connection.id,
        _payload("video_analysis_unary"),
        idempotency_key="r8e-unary-model-redaction",
    )

    assert certification.status == "failed"
    assert certification.actual_model is None
    assert certification.error_code == "provider_video_certification_invalid_model"
    serialized = json.dumps(
        [
            certification.model_dump(mode="json"),
            service.repository.list_workload_certifications("local"),
            service.repository.list_multimodal_certification_sessions("local"),
        ],
        ensure_ascii=False,
    )
    assert "provider-secret-sentinel" not in serialized
    assert "private certification body" not in serialized


@pytest.mark.asyncio
async def test_r8e_dispatched_connect_timeout_is_uncertain_without_ip_retry(
    tmp_path: Path,
) -> None:
    post_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _catalog_response(request)
        post_urls.append(str(request.url))
        raise httpx.ConnectTimeout("synthetic timeout", request=request)

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certification = await _certification_service(service, transport).run(
        connection.id,
        _payload("chat_video_stream"),
        idempotency_key="r8e-timeout",
    )

    assert len(post_urls) == 1
    assert any(ip in post_urls[0] for ip in ("8.8.8.8", "1.1.1.1"))
    assert certification.status == "uncertain"
    assert certification.can_run is False
    assert certification.error_code == "provider_workload_connect_timeout"
    assert certification.provider_dispatch_state == "uncertain"
    assert certification.retry_allowed is False


@pytest.mark.asyncio
async def test_async_video_certification_posts_once_then_only_polls(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    states = [
        {
            "id": "video-cert-job-1",
            "status": "in_progress",
            "generation_id": "video-cert-generation-1",
        },
        {
            "id": "video-cert-job-1",
            "status": "completed",
            "generation_id": "video-cert-generation-1",
            "unsigned_urls": [
                "/api/v1/videos/video-cert-job-1/content?index=0"
            ],
        },
    ]
    transport = _async_video_certification_transport(states, requests)
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)

    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-certification",
    )
    assert submitted.status == "uncertain"
    assert submitted.error_code == "provider_video_certification_pending"
    assert submitted.refresh_available is True
    assert submitted.checks.async_job_id_verified is True
    assert submitted.checks.media_format_verified is False

    replay = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-certification",
    )
    assert replay.certification_id == submitted.certification_id

    pending = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )
    assert pending.status == "uncertain"
    assert pending.error_code == "provider_video_certification_pending"
    assert pending.refresh_available is True
    assert pending.actual_model == MODEL_ID

    completed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )
    assert completed.status == "passed"
    assert completed.can_run is True
    assert completed.actual_model == MODEL_ID
    assert completed.refresh_available is False
    assert completed.checks.actual_model_verified is True
    assert completed.checks.async_job_id_verified is True
    assert completed.checks.output_metadata_verified is True
    assert completed.checks.async_terminal_verified is True
    assert completed.checks.terminal_signal_verified is True
    assert completed.checks.media_format_verified is True
    assert completed.checks.video_catalog_model_verified is True
    assert completed.checks.video_catalog_parameters_verified is True
    assert (
        service.repository.list_catalog_models(
            "local",
            connection_id=connection.id,
            model_id=MODEL_ID,
            status="active",
            limit=1,
        )
        == []
    )

    video_posts = [
        request
        for request in requests
        if request.method == "POST" and request.url.path.endswith("/videos")
    ]
    video_polls = [
        request
        for request in requests
        if request.method == "GET" and "/videos/video-cert-job-1" in request.url.path
    ]
    specialized_catalog_reads = [
        request
        for request in requests
        if request.method == "GET" and request.url.path.endswith("/videos/models")
    ]
    assert len(video_posts) == 1
    assert len(video_polls) == 2
    assert len(specialized_catalog_reads) == 1


@pytest.mark.asyncio
async def test_video_generation_accepts_exact_catalog_canonical_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": CANONICAL_MODEL_ID,
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": [
                    "/api/v1/videos/video-cert-job-1/content?index=0"
                ],
            }
        ],
        requests,
        catalog_canonical_model_id=CANONICAL_MODEL_ID,
        generation_actual_model=CANONICAL_MODEL_ID,
    )
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)

    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-canonical-slug",
    )
    completed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )

    assert completed.status == "passed"
    assert completed.actual_model == CANONICAL_MODEL_ID
    assert completed.checks.actual_model_verified is True
    stored = service.repository.get_workload_certification(
        "local",
        str(completed.certification_id),
    )
    assert stored is not None
    profile = json.loads(str(stored["profile_json"]))
    assert profile["video_catalog_model_id"] == MODEL_ID
    assert (
        profile["video_catalog_canonical_model_id"]
        == CANONICAL_MODEL_ID
    )
    assert (
        profile["video_catalog_contract_version"]
        == "modelmirror-openrouter-video-models-v2"
    )

    monkeypatch.setenv("MODEL_CONTROL_VIDEO_GENERATION_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "video_generation",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="video_generation_async",
                    model_id=MODEL_ID,
                    connection_id=connection.id,
                    adapter_contract="openrouter_video_jobs_v1",
                )
            ],
        ),
    )
    activated = control.activate(
        "video_generation",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert activated.effective_status == "managed_required"
    assert len([item for item in requests if item.method == "POST"]) == 1


@pytest.mark.asyncio
async def test_video_generation_contract_upgrade_never_replays_existing_post(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    transport = _async_video_certification_transport(
        [{"id": "catalog-job", "status": "queued", "model": MODEL_ID}],
        requests,
    )
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)
    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-contract-upgrade",
    )
    stored = service.repository.get_workload_certification(
        "local",
        str(submitted.certification_id),
    )
    assert stored is not None
    old_profile = json.loads(str(stored["profile_json"]))
    old_profile["video_catalog_contract_version"] = (
        "modelmirror-openrouter-video-models-v1"
    )
    old_profile.pop("video_catalog_model_id", None)
    old_profile.pop("video_catalog_canonical_model_id", None)
    old_profile_json = json.dumps(
        old_profile,
        sort_keys=True,
        separators=(",", ":"),
    )
    old_profile_fingerprint = hashlib.sha256(
        old_profile_json.encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(service.repository.database_path) as database:
        database.execute(
            """
            UPDATE provider_workload_certifications
            SET profile_json = ?, profile_fingerprint = ?
            WHERE id = ?
            """,
            (
                old_profile_json,
                old_profile_fingerprint,
                submitted.certification_id,
            ),
        )
        database.commit()

    request_count = len(requests)
    replay = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-contract-upgrade",
    )

    assert replay.certification_id == submitted.certification_id
    assert len(requests) == request_count
    assert len([item for item in requests if item.method == "POST"]) == 1


@pytest.mark.asyncio
async def test_video_generation_rejects_model_outside_exact_catalog_mapping(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": "provider/video-model-unmapped",
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": [
                    "/api/v1/videos/video-cert-job-1/content?index=0"
                ],
            }
        ],
        requests,
        catalog_canonical_model_id=CANONICAL_MODEL_ID,
    )
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)
    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-unmapped-model",
    )
    completed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )

    assert completed.status == "failed"
    assert completed.actual_model == "provider/video-model-unmapped"
    assert completed.error_code == "provider_workload_model_mismatch"
    assert completed.refresh_available is False
    assert len([item for item in requests if item.method == "POST"]) == 1


@pytest.mark.asyncio
async def test_video_generation_binding_rejects_tampered_catalog_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": [
                    "/api/v1/videos/video-cert-job-1/content?index=0"
                ],
            }
        ],
        requests,
    )
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)
    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-tampered-catalog-evidence",
    )
    passed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )
    assert passed.status == "passed"

    with sqlite3.connect(service.repository.database_path) as database:
        stored = database.execute(
            "SELECT checks_json FROM provider_workload_certifications WHERE id = ?",
            (passed.certification_id,),
        ).fetchone()
        assert stored is not None
        checks = json.loads(str(stored[0]))
        checks["video_catalog_model_verified"] = False
        database.execute(
            "UPDATE provider_workload_certifications SET checks_json = ? WHERE id = ?",
            (json.dumps(checks, sort_keys=True), passed.certification_id),
        )

    monkeypatch.setenv("MODEL_CONTROL_VIDEO_GENERATION_ENABLED", "true")
    with pytest.raises(RouterServiceError) as blocked:
        ProviderWorkloadControlService(service).update_policy(
            "video_generation",
            ProviderWorkloadPolicyUpdate(
                expected_revision=0,
                bindings=[
                    ProviderWorkloadBindingUpdate(
                        execution_shape="video_generation_async",
                        model_id=MODEL_ID,
                        connection_id=connection.id,
                        adapter_contract="openrouter_video_jobs_v1",
                    )
                ],
            ),
        )

    assert blocked.value.code == "provider_multimodal_video_evidence_incomplete"


@pytest.mark.asyncio
async def test_video_generation_profile_mapping_is_bound_to_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": [
                    "/api/v1/videos/video-cert-job-1/content?index=0"
                ],
            }
        ],
        requests,
        catalog_canonical_model_id=CANONICAL_MODEL_ID,
    )
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)
    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-profile-fingerprint",
    )
    passed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )
    assert passed.status == "passed"

    with sqlite3.connect(service.repository.database_path) as database:
        stored = database.execute(
            "SELECT profile_json FROM provider_workload_certifications WHERE id = ?",
            (passed.certification_id,),
        ).fetchone()
        assert stored is not None
        profile = json.loads(str(stored[0]))
        profile["video_catalog_canonical_model_id"] = (
            "provider/forged-canonical-model"
        )
        database.execute(
            "UPDATE provider_workload_certifications SET profile_json = ? WHERE id = ?",
            (
                json.dumps(profile, sort_keys=True, separators=(",", ":")),
                passed.certification_id,
            ),
        )
        database.commit()

    monkeypatch.setenv("MODEL_CONTROL_VIDEO_GENERATION_ENABLED", "true")
    with pytest.raises(RouterServiceError) as blocked:
        ProviderWorkloadControlService(service).update_policy(
            "video_generation",
            ProviderWorkloadPolicyUpdate(
                expected_revision=0,
                bindings=[
                    ProviderWorkloadBindingUpdate(
                        execution_shape="video_generation_async",
                        model_id=MODEL_ID,
                        connection_id=connection.id,
                        adapter_contract="openrouter_video_jobs_v1",
                    )
                ],
            ),
        )
    assert blocked.value.code == "provider_workload_certification_profile_invalid"

    gateway = ManagedMultimodalGateway.for_router(service)
    with pytest.raises(ManagedMultimodalError) as runtime_blocked:
        gateway.certified_video_parameters(
            "video_generation",
            certification_id=str(passed.certification_id),
            execution_shape="video_generation_async",
        )
    assert (
        runtime_blocked.value.code
        == "provider_workload_certification_profile_invalid"
    )
    assert len([item for item in requests if item.method == "POST"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specialized_payload", "expected_code"),
    [
        (
            {"data": []},
            "provider_video_generation_model_not_found",
        ),
        (
            {
                "data": [
                    {
                        "id": MODEL_ID,
                        "supported_durations": [8],
                        "supported_resolutions": [
                            R8E_VIDEO_GENERATION_RESOLUTION
                        ],
                        "supported_aspect_ratios": [
                            R8E_VIDEO_GENERATION_ASPECT_RATIO
                        ],
                    }
                ]
            },
            "provider_video_generation_parameters_not_supported",
        ),
        (
            {
                "data": [
                    {
                        "id": MODEL_ID,
                        "supported_durations": [
                            R8E_VIDEO_GENERATION_DURATION_SECONDS
                        ],
                        "supported_resolutions": [
                            R8E_VIDEO_GENERATION_RESOLUTION
                        ],
                        "supported_aspect_ratios": [
                            R8E_VIDEO_GENERATION_ASPECT_RATIO
                        ],
                    },
                    {
                        "id": MODEL_ID,
                        "supported_durations": [
                            R8E_VIDEO_GENERATION_DURATION_SECONDS
                        ],
                        "supported_resolutions": [
                            R8E_VIDEO_GENERATION_RESOLUTION
                        ],
                        "supported_aspect_ratios": [
                            R8E_VIDEO_GENERATION_ASPECT_RATIO
                        ],
                    },
                ]
            },
            "provider_video_generation_catalog_invalid",
        ),
        (
            {"data": ["not-an-object"]},
            "provider_video_generation_catalog_invalid",
        ),
        (
            {
                "data": [
                    {
                        "id": MODEL_ID,
                        "canonical_slug": "bad\ncanonical-model",
                        "supported_durations": [
                            R8E_VIDEO_GENERATION_DURATION_SECONDS
                        ],
                        "supported_resolutions": [
                            R8E_VIDEO_GENERATION_RESOLUTION
                        ],
                        "supported_aspect_ratios": [
                            R8E_VIDEO_GENERATION_ASPECT_RATIO
                        ],
                    }
                ]
            },
            "provider_video_generation_catalog_invalid",
        ),
    ],
)
async def test_video_generation_specialized_catalog_blocks_before_post(
    tmp_path: Path,
    specialized_payload: dict[str, object],
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith(
            "/videos/models"
        ):
            return httpx.Response(200, json=specialized_payload)
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "provider/unrelated-model"}]},
            )
        pytest.fail("specialized catalog rejection must happen before POST")

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    with pytest.raises(RouterServiceError) as blocked:
        await _certification_service(service, transport).run(
            connection.id,
            _video_generation_payload(),
            idempotency_key=f"r8e-specialized-block-{expected_code}",
        )

    assert blocked.value.code == expected_code
    assert not [item for item in requests if item.method == "POST"]
    assert service.repository.list_workload_certifications("local") == []


@pytest.mark.asyncio
async def test_video_generation_specialized_catalog_fingerprint_drift_blocks_post(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    service_ref: ModelRouterService | None = None
    connection_ref: object | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith(
            "/videos/models"
        ):
            assert service_ref is not None
            assert connection_ref is not None
            service_ref.repository.update_connection(
                "local",
                connection_ref.id,
                RouterConnectionUpdate(api_key="rotated-during-video-catalog"),
            )
            return _catalog_response(request)
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "provider/unrelated-model"}]},
            )
        pytest.fail("connection drift must stop certification before POST")

    transport = httpx.MockTransport(handler)
    service_ref, connection_ref = _service(tmp_path, transport)
    with pytest.raises(RouterServiceError) as blocked:
        await _certification_service(service_ref, transport).run(
            connection_ref.id,
            _video_generation_payload(),
            idempotency_key="r8e-specialized-catalog-drift",
        )

    assert blocked.value.code == "provider_workload_catalog_stale"
    assert not [item for item in requests if item.method == "POST"]


class _TrackedVideoCatalogStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield json.dumps(_catalog_response_payload()).encode("utf-8")

    async def aclose(self) -> None:
        self.closed = True


def _catalog_response_payload(
    *,
    canonical_model_id: str | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": MODEL_ID,
        "supported_durations": [R8E_VIDEO_GENERATION_DURATION_SECONDS],
        "supported_resolutions": [R8E_VIDEO_GENERATION_RESOLUTION],
        "supported_aspect_ratios": [R8E_VIDEO_GENERATION_ASPECT_RATIO],
    }
    if canonical_model_id is not None:
        item["canonical_slug"] = canonical_model_id
    return {"data": [item]}


@pytest.mark.asyncio
async def test_specialized_video_catalog_closes_stream() -> None:
    stream = _TrackedVideoCatalogStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/videos/models")
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "provider.example"
        assert request.extensions["sni_hostname"] == "provider.example"
        return httpx.Response(200, stream=stream)

    target = ProviderMultimodalTarget.create(
        provider_kind="openrouter",
        connection_id="conn-video-catalog",
        base_url="https://provider.example/api/v1",
        api_key="do-not-print",
        adapter_contract="openrouter_video_jobs_v1",
        execution_shape="video_generation_async",
    )
    transport = ProviderMultimodalTransport(
        ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        evidence = await transport.verify_openrouter_video_generation_model(
            client,
            target,
            MODEL_ID,
            duration_seconds=R8E_VIDEO_GENERATION_DURATION_SECONDS,
            resolution=R8E_VIDEO_GENERATION_RESOLUTION,
            aspect_ratio=R8E_VIDEO_GENERATION_ASPECT_RATIO,
        )

    assert evidence.model_verified is True
    assert evidence.parameters_verified is True
    assert evidence.catalog_model_id == MODEL_ID
    assert evidence.canonical_model_id == MODEL_ID
    assert stream.closed is True


@pytest.mark.asyncio
async def test_specialized_video_catalog_timeout_includes_dns() -> None:
    requests: list[httpx.Request] = []

    def slow_resolver(_host: str, _port: int) -> list[str]:
        time.sleep(0.05)
        return ["8.8.8.8"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_catalog_response_payload())

    target = ProviderMultimodalTarget.create(
        provider_kind="openrouter",
        connection_id="conn-video-catalog",
        base_url="https://provider.example/api/v1",
        api_key="do-not-print",
        adapter_contract="openrouter_video_jobs_v1",
        execution_shape="video_generation_async",
    )
    transport = ProviderMultimodalTransport(
        ProviderEgressPolicy(resolver=slow_resolver)
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(TimeoutError):
            await transport.verify_openrouter_video_generation_model(
                client,
                target,
                MODEL_ID,
                duration_seconds=R8E_VIDEO_GENERATION_DURATION_SECONDS,
                resolution=R8E_VIDEO_GENERATION_RESOLUTION,
                aspect_ratio=R8E_VIDEO_GENERATION_ASPECT_RATIO,
                timeout_seconds=0.01,
            )

    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsigned_urls",
    [
        ["not-a-url"],
        [
            "/api/v1/videos/video-cert-job-1/content?index=0",
            "/api/v1/videos/video-cert-job-1/content?index=1",
        ],
    ],
)
async def test_async_video_certification_rejects_uncertified_output_metadata(
    tmp_path: Path,
    unsigned_urls: list[str],
) -> None:
    requests: list[httpx.Request] = []
    transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": unsigned_urls,
            }
        ],
        requests,
    )
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)
    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key=f"r8e-invalid-output-{len(unsigned_urls)}-{unsigned_urls[0]}",
    )
    completed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )

    assert completed.status == "failed"
    assert completed.can_run is False
    assert (
        completed.error_code
        == "provider_video_certification_output_metadata_invalid"
    )
    assert completed.checks.output_metadata_verified is False
    assert len([item for item in requests if item.method == "POST"]) == 1


@pytest.mark.asyncio
async def test_async_video_submit_timeout_never_replays_post(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return _catalog_response(request)
        raise httpx.ReadTimeout("synthetic uncertain submit", request=request)

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)
    first = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-timeout",
    )
    second = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key="r8e-video-generation-timeout",
    )

    assert first.status == "uncertain"
    assert first.provider_dispatch_state == "uncertain"
    assert first.retry_allowed is False
    assert first.refresh_available is False
    assert second.certification_id == first.certification_id
    assert len([request for request in requests if request.method == "POST"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_code"),
    [
        (
            "id",
            {"private": "provider-secret-sentinel"},
            "provider_video_certification_missing_upstream_id",
        ),
        (
            "model",
            "bad\nprovider-secret-sentinel",
            "provider_video_certification_invalid_model",
        ),
    ],
)
async def test_async_video_certification_submit_rejects_untrusted_identifiers_without_leak(
    tmp_path: Path,
    field: str,
    invalid_value: object,
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return _catalog_response(request)
        payload: dict[str, object] = {
            "id": "video-cert-redaction",
            "status": "pending",
        }
        payload[field] = invalid_value
        return httpx.Response(202, json=payload)

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certification = await _certification_service(service, transport).run(
        connection.id,
        _video_generation_payload(),
        idempotency_key=f"r8e-submit-redaction-{field}",
    )

    assert certification.error_code == expected_code
    assert len([item for item in requests if item.method == "POST"]) == 1
    serialized = json.dumps(
        [
            service.repository.list_workload_certifications("local"),
            service.repository.list_multimodal_certification_sessions("local"),
        ],
        ensure_ascii=False,
    )
    assert "provider-secret-sentinel" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value", "expected_code"),
    [
        (
            "model",
            "bad\nprovider-secret-sentinel",
            "provider_video_certification_invalid_model",
        ),
        (
            "generation_id",
            {"private": "provider-secret-sentinel"},
            "provider_video_certification_invalid_generation_id",
        ),
    ],
)
async def test_async_video_certification_refresh_rejects_untrusted_identifiers_without_leak(
    tmp_path: Path,
    field: str,
    invalid_value: object,
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []
    terminal: dict[str, object] = {
        "id": "video-cert-job-1",
        "status": "completed",
        "model": MODEL_ID,
        "generation_id": "video-cert-generation-1",
        "unsigned_urls": [
            "/api/v1/videos/video-cert-job-1/content?index=0"
        ],
    }
    terminal[field] = invalid_value
    transport = _async_video_certification_transport([terminal], requests)
    service, connection = _service(tmp_path, transport)
    certifications = _certification_service(service, transport)
    submitted = await certifications.run(
        connection.id,
        _video_generation_payload(),
        idempotency_key=f"r8e-refresh-redaction-{field}",
    )
    completed = await certifications.refresh_multimodal_certification(
        str(submitted.certification_id)
    )

    assert completed.status == "failed"
    assert completed.error_code == expected_code
    assert len([item for item in requests if item.method == "POST"]) == 1
    serialized = json.dumps(
        [
            service.repository.list_workload_certifications("local"),
            service.repository.list_multimodal_certification_sessions("local"),
        ],
        ensure_ascii=False,
    )
    assert "provider-secret-sentinel" not in serialized


@pytest.mark.asyncio
async def test_managed_video_analysis_dispatches_once_and_blocks_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_requests: list[httpx.Request] = []

    def runtime_handler(request: httpx.Request) -> httpx.Response:
        runtime_requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == MODEL_ID
        assert body["stream"] is False
        assert body["messages"][0]["content"][1]["video_url"]["url"] == (
            video_fixture.SYNTHETIC_VIDEO_MP4_DATA_URL
        )
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {"message": {"content": "The video shows a blue square."}}
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    certification_transport = _certification_transport()
    runtime_transport = httpx.MockTransport(runtime_handler)
    service, connection = _service(tmp_path, certification_transport)
    gateway = await _activate_video_analysis(
        service,
        connection,
        certification_transport,
        runtime_transport,
        monkeypatch,
    )
    video = VideoAnalysisService(
        service,
        _VideoCatalogStub(),  # type: ignore[arg-type]
        managed_gateway=gateway,
    )
    result = await video.analyze(
        model_id=MODEL_ID,
        prompt="Describe the video.",
        source_type="file",
        filename="fixture.mp4",
        content_type="video/mp4",
        content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
        idempotency_key="runtime-video-analysis",
    )

    assert result.execution_mode == "managed"
    assert result.actual_model == MODEL_ID
    assert result.provider_route_receipts[0]["status"] == "passed"
    assert result.provider_route_receipts[0]["call_count"] == 1
    assert len(runtime_requests) == 1
    assert runtime_requests[0].url.host in {"8.8.8.8", "1.1.1.1"}

    with pytest.raises(MultimodalServiceError) as replay:
        await video.analyze(
            model_id=MODEL_ID,
            prompt="Describe the video again.",
            source_type="file",
            filename="fixture.mp4",
            content_type="video/mp4",
            content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
            idempotency_key="runtime-video-analysis",
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(runtime_requests) == 1


@pytest.mark.asyncio
async def test_managed_video_analysis_requires_idempotency_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_requests: list[httpx.Request] = []
    certification_transport = _certification_transport()
    runtime_transport = httpx.MockTransport(
        lambda request: runtime_requests.append(request)
        or httpx.Response(500)
    )
    service, connection = _service(tmp_path, certification_transport)
    gateway = await _activate_video_analysis(
        service,
        connection,
        certification_transport,
        runtime_transport,
        monkeypatch,
    )
    video = VideoAnalysisService(
        service,
        _VideoCatalogStub(),  # type: ignore[arg-type]
        managed_gateway=gateway,
    )

    with pytest.raises(MultimodalServiceError) as blocked:
        await video.analyze(
            model_id=MODEL_ID,
            prompt="Describe the video.",
            source_type="file",
            filename="fixture.mp4",
            content_type="video/mp4",
            content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
        )
    assert blocked.value.code == "invalid_idempotency_key"
    assert blocked.value.route_receipt is not None
    assert blocked.value.route_receipt["call_count"] == 0
    assert runtime_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_type", "filename", "content_type", "content", "video_url"),
    [
        ("file", "fixture.webm", "video/webm", b"\x1a\x45\xdf\xa3fixture", None),
        ("url", None, None, None, "https://example.com/fixture.mp4"),
    ],
)
async def test_managed_video_analysis_rejects_uncertified_input_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_type: str,
    filename: str | None,
    content_type: str | None,
    content: bytes | None,
    video_url: str | None,
) -> None:
    runtime_requests: list[httpx.Request] = []
    certification_transport = _certification_transport()
    service, connection = _service(tmp_path, certification_transport)
    gateway = await _activate_video_analysis(
        service,
        connection,
        certification_transport,
        httpx.MockTransport(
            lambda request: runtime_requests.append(request)
            or httpx.Response(500)
        ),
        monkeypatch,
    )
    video = VideoAnalysisService(
        service,
        _VideoCatalogStub(),  # type: ignore[arg-type]
        managed_gateway=gateway,
    )

    with pytest.raises(MultimodalServiceError) as blocked:
        await video.analyze(
            model_id=MODEL_ID,
            prompt="Describe the video.",
            source_type=source_type,
            filename=filename,
            content_type=content_type,
            content=content,
            video_url=video_url,
            idempotency_key="runtime-uncertified-input",
        )
    assert blocked.value.code == "provider_multimodal_video_input_not_certified"
    assert blocked.value.route_receipt is not None
    assert blocked.value.route_receipt["call_count"] == 0
    assert runtime_requests == []


@pytest.mark.asyncio
async def test_legacy_video_analysis_is_not_captured_by_standalone_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certification_transport = _certification_transport()
    service, connection = _service(tmp_path, certification_transport)
    gateway = await _activate_video_analysis(
        service,
        connection,
        certification_transport,
        httpx.MockTransport(lambda _request: httpx.Response(500)),
        monkeypatch,
    )
    legacy_calls: list[str] = []

    class LegacyAdapter:
        async def analyze(self, _target, *, model_id, prompt, video_source):
            legacy_calls.append(model_id)
            assert prompt == "Describe the video."
            assert video_source.startswith("data:video/mp4;base64,")
            return "legacy result", model_id, VideoAnalysisUsage()

    video = VideoAnalysisService(
        service,
        _VideoCatalogStub(),  # type: ignore[arg-type]
        adapter=LegacyAdapter(),  # type: ignore[arg-type]
        managed_gateway=gateway,
    )
    monkeypatch.setattr(video, "_verify_model", lambda *_args: None)
    video.catalog_service.resolve_target = lambda: OpenRouterTarget(  # type: ignore[method-assign]
        base_url="https://provider.example/api/v1",
        api_key="legacy-secret",
        connection_id=connection.id,
        cache_key="legacy",
    )

    async def allow_model(*_args) -> None:
        return None

    monkeypatch.setattr(video, "_verify_model", allow_model)
    result = await video.analyze(
        model_id=MODEL_ID,
        prompt="Describe the video.",
        source_type="file",
        filename="fixture.mp4",
        content_type="video/mp4",
        content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
        force_legacy=True,
    )

    assert result.execution_mode == "legacy"
    assert result.text == "legacy result"
    assert legacy_calls == [MODEL_ID]


@pytest.mark.parametrize(
    ("finish_reason", "include_done", "expected_status", "expected_code"),
    [
        ("stop", False, "succeeded", None),
        (None, True, "succeeded", None),
        ("length", True, "failed", "provider_workload_output_truncated"),
        (
            "content_filter",
            True,
            "failed",
            "provider_workload_content_filtered",
        ),
        ("error", True, "failed", "provider_workload_stream_error"),
        (
            "unexpected",
            True,
            "failed",
            "provider_workload_invalid_finish_reason",
        ),
    ],
)
def test_chat_video_stream_accepts_only_safe_terminal_semantics(
    finish_reason: str | None,
    include_done: bool,
    expected_status: str,
    expected_code: str | None,
) -> None:
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_video_stream",
        expected_model=MODEL_ID,
        started_at=0.0,
    )
    event = json.dumps(
        {
            "model": MODEL_ID,
            "choices": [
                {
                    "delta": {"content": "A blue square."},
                    "finish_reason": finish_reason,
                }
            ],
        }
    )
    evidence.feed(f"data: {event}\n\n")
    if include_done:
        evidence.feed("data: [DONE]\n\n")
    status, _result_class, error_code, _checks, _warnings = evidence.finish(
        transport_completed=True
    )

    assert status == expected_status
    assert error_code == expected_code


def test_chat_video_stream_bounds_unterminated_event() -> None:
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_video_stream",
        expected_model=MODEL_ID,
        started_at=0.0,
        max_event_bytes=64,
    )
    assert evidence.feed("data: " + "x" * 128) == ()
    assert evidence.hard_error_code == "provider_multimodal_sse_event_too_large"


def test_chat_video_stream_rejects_untrusted_model_without_retaining_body() -> None:
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_video_stream",
        expected_model=MODEL_ID,
        started_at=0.0,
    )
    event = json.dumps(
        {
            "model": "bad\nprovider-secret-sentinel",
            "choices": [
                {"delta": {"content": "private body"}, "finish_reason": "stop"}
            ],
        }
    )

    evidence.feed(f"data: {event}\n\n")
    status, _result_class, error_code, _checks, _warnings = evidence.finish(
        transport_completed=True
    )

    assert status == "failed"
    assert error_code == "provider_multimodal_invalid_sse"
    assert evidence.actual_model is None
    assert "provider-secret-sentinel" not in repr(evidence)


def test_video_identifier_hardening_does_not_change_r8b_model_aliases() -> None:
    model_alias = "provider/model@v1+custom"
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_image_stream",
        expected_model=model_alias,
        started_at=0.0,
    )
    event = json.dumps(
        {
            "id": "generation@custom+1",
            "model": model_alias,
            "choices": [
                {"delta": {"content": "safe image text"}, "finish_reason": "stop"}
            ],
        }
    )

    evidence.feed(f"data: {event}\n\n")
    evidence.feed("data: [DONE]\n\n")
    status, _result_class, error_code, _checks, _warnings = evidence.finish(
        transport_completed=True
    )

    assert status == "succeeded"
    assert error_code is None
    assert evidence.actual_model == model_alias
    assert evidence.generation_id == "generation@custom+1"


@pytest.mark.asyncio
async def test_managed_video_analysis_timeout_is_uncertain_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_requests: list[httpx.Request] = []

    def runtime_handler(request: httpx.Request) -> httpx.Response:
        runtime_requests.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    certification_transport = _certification_transport()
    runtime_transport = httpx.MockTransport(runtime_handler)
    service, connection = _service(tmp_path, certification_transport)
    gateway = await _activate_video_analysis(
        service,
        connection,
        certification_transport,
        runtime_transport,
        monkeypatch,
    )
    video = VideoAnalysisService(
        service,
        _VideoCatalogStub(),  # type: ignore[arg-type]
        managed_gateway=gateway,
    )

    with pytest.raises(MultimodalServiceError) as uncertain:
        await video.analyze(
            model_id=MODEL_ID,
            prompt="Describe the video.",
            source_type="file",
            filename="fixture.mp4",
            content_type="video/mp4",
            content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
            idempotency_key="runtime-timeout",
        )
    assert uncertain.value.code == "provider_workload_timeout"
    assert uncertain.value.route_receipt is not None
    assert uncertain.value.route_receipt["status"] == "uncertain"
    assert uncertain.value.route_receipt["call_count"] == 1
    assert len(runtime_requests) == 1


@pytest.mark.asyncio
async def test_managed_video_analysis_complete_invalid_json_is_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_requests: list[httpx.Request] = []

    def runtime_handler(request: httpx.Request) -> httpx.Response:
        runtime_requests.append(request)
        return httpx.Response(200, content=b"not-json")

    certification_transport = _certification_transport()
    service, connection = _service(tmp_path, certification_transport)
    gateway = await _activate_video_analysis(
        service,
        connection,
        certification_transport,
        httpx.MockTransport(runtime_handler),
        monkeypatch,
    )
    video = VideoAnalysisService(
        service,
        _VideoCatalogStub(),  # type: ignore[arg-type]
        managed_gateway=gateway,
    )

    with pytest.raises(MultimodalServiceError) as failed:
        await video.analyze(
            model_id=MODEL_ID,
            prompt="Describe the video.",
            source_type="file",
            filename="fixture.mp4",
            content_type="video/mp4",
            content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
            idempotency_key="runtime-invalid-json",
        )
    assert failed.value.code == "provider_multimodal_video_invalid_json"
    assert failed.value.route_receipt is not None
    assert failed.value.route_receipt["status"] == "failed"
    assert failed.value.route_receipt["call_count"] == 1
    assert len(runtime_requests) == 1


@pytest.mark.asyncio
async def test_managed_video_analysis_rejects_untrusted_model_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_requests: list[httpx.Request] = []

    def runtime_handler(request: httpx.Request) -> httpx.Response:
        runtime_requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "bad\nprovider-secret-sentinel",
                "choices": [
                    {"message": {"content": "private runtime body"}}
                ],
            },
        )

    certification_transport = _certification_transport()
    service, connection = _service(tmp_path, certification_transport)
    gateway = await _activate_video_analysis(
        service,
        connection,
        certification_transport,
        httpx.MockTransport(runtime_handler),
        monkeypatch,
    )
    video = VideoAnalysisService(
        service,
        _VideoCatalogStub(),  # type: ignore[arg-type]
        managed_gateway=gateway,
    )

    with pytest.raises(MultimodalServiceError) as failed:
        await video.analyze(
            model_id=MODEL_ID,
            prompt="Describe the video.",
            source_type="file",
            filename="fixture.mp4",
            content_type="video/mp4",
            content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
            idempotency_key="runtime-invalid-model",
        )

    assert failed.value.code == "provider_multimodal_video_invalid_model"
    assert failed.value.route_receipt is not None
    assert failed.value.route_receipt["status"] == "failed"
    assert failed.value.route_receipt["call_count"] == 1
    assert len(runtime_requests) == 1
    serialized = json.dumps(
        [
            failed.value.route_receipt,
            service.repository.list_workload_receipts("local"),
        ],
        ensure_ascii=False,
    )
    assert "provider-secret-sentinel" not in serialized
    assert "private runtime body" not in serialized


@pytest.mark.asyncio
async def test_managed_video_generation_submits_once_and_polls_original_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certification_requests: list[httpx.Request] = []
    certification_transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": CANONICAL_MODEL_ID,
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": [
                    "/api/v1/videos/video-cert-job-1/content?index=0"
                ],
            }
        ],
        certification_requests,
        catalog_canonical_model_id=CANONICAL_MODEL_ID,
        generation_actual_model=CANONICAL_MODEL_ID,
    )
    service, connection = _service(tmp_path, certification_transport)
    await _activate_video_generation(
        service,
        connection,
        certification_transport,
        monkeypatch,
    )

    runtime_requests: list[httpx.Request] = []

    def runtime_handler(request: httpx.Request) -> httpx.Response:
        runtime_requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["model"] == MODEL_ID
            assert body["prompt"] == "Generate a five-second blue-circle clip."
            assert body["duration"] == R8E_VIDEO_GENERATION_DURATION_SECONDS
            assert body["resolution"] == R8E_VIDEO_GENERATION_RESOLUTION
            assert body["aspect_ratio"] == R8E_VIDEO_GENERATION_ASPECT_RATIO
            return httpx.Response(
                202,
                json={
                    "id": "runtime-video-job-1",
                    "status": "pending",
                    "model": CANONICAL_MODEL_ID,
                },
            )
        assert request.method == "GET"
        assert request.url.path.endswith("/videos/runtime-video-job-1")
        return httpx.Response(
            200,
            json={
                "id": "runtime-video-job-1",
                "status": "completed",
                "model": CANONICAL_MODEL_ID,
                "generation_id": "runtime-generation-1",
                "unsigned_urls": [
                    "/api/v1/videos/runtime-video-job-1/content?index=0"
                ],
                "usage": {"cost": 0.01},
            },
        )

    runtime_transport = httpx.MockTransport(runtime_handler)
    runtime_factory = lambda: httpx.AsyncClient(
        transport=runtime_transport,
        follow_redirects=False,
        trust_env=False,
    )
    gateway = ManagedMultimodalGateway.for_router(
        service,
        client_factory=runtime_factory,
    )
    jobs = VideoJobService(
        service,
        _ForbiddenManagedVideoCatalogStub(),  # type: ignore[arg-type]
        adapter=OpenRouterVideoJobAdapter(
            client_factory=runtime_factory,
            egress_policy=service.egress_policy,
        ),
        managed_gateway=gateway,
    )

    created = await jobs.create(
        model_id=MODEL_ID,
        prompt="Generate a five-second blue-circle clip.",
        idempotency_key="r8e-runtime-video-generation",
    )
    replay = await jobs.create(
        model_id=MODEL_ID,
        prompt="This different prompt must not produce another POST.",
        idempotency_key="r8e-runtime-video-generation",
    )
    assert created.job_id == replay.job_id
    assert created.execution_mode == "managed"
    assert created.provider_dispatch_state == "confirmed"
    assert created.retry_allowed is False
    assert created.provider_route_receipts[0]["status"] == "running"
    assert created.provider_route_receipts[0]["call_count"] == 1
    assert len([item for item in runtime_requests if item.method == "POST"]) == 1

    completed = await jobs.refresh(created.job_id)
    assert completed.status == "succeeded"
    assert completed.actual_model == CANONICAL_MODEL_ID
    assert completed.output_count == 1
    assert completed.retry_allowed is False
    assert completed.provider_route_receipts[0]["status"] == "passed"
    assert completed.provider_route_receipts[0]["calls"][0]["status"] == "passed"
    assert len([item for item in runtime_requests if item.method == "GET"]) == 1
    terminal_replay = await jobs.refresh(created.job_id)
    assert terminal_replay.status == "succeeded"
    assert len([item for item in runtime_requests if item.method == "GET"]) == 1
    with pytest.raises(MultimodalServiceError) as retained:
        jobs.delete(created.job_id)
    assert retained.value.code == "managed_video_job_retained"

    stored = service.repository.get_video_job("local", created.job_id)
    assert stored is not None
    serialized = json.dumps(stored, ensure_ascii=False)
    assert "five-second blue-circle" not in serialized
    assert "different prompt" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"duration": 8},
        {"resolution": "1080p"},
        {"aspect_ratio": "9:16"},
        {"generate_audio": True},
        {"seed": 7},
        {"has_first_frame": True},
        {"has_last_frame": True},
        {"reference_image_count": 1},
        {"provider_option_keys": ["source_video"]},
        {"provider_option_keys": ["upscale_factor:2"]},
        {"provider_option_keys": ["creativity:4"]},
        {"provider_option_keys": ["enhancePrompt"]},
    ],
)
async def test_managed_video_generation_blocks_parameters_outside_certification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
) -> None:
    service, _connection = await _activated_video_generation_service(
        tmp_path,
        monkeypatch,
    )
    runtime_requests: list[httpx.Request] = []
    jobs = _runtime_video_jobs(
        service,
        httpx.MockTransport(
            lambda request: runtime_requests.append(request) or httpx.Response(500)
        ),
    )
    arguments: dict[str, object] = {
        "model_id": MODEL_ID,
        "idempotency_key_hash": hashlib.sha256(
            json.dumps(overrides, sort_keys=True).encode()
        ).hexdigest(),
        "payload": {"model": MODEL_ID, "prompt": "Generate one clip."},
        "input_bytes": 18,
        "duration": R8E_VIDEO_GENERATION_DURATION_SECONDS,
        "resolution": R8E_VIDEO_GENERATION_RESOLUTION,
        "aspect_ratio": R8E_VIDEO_GENERATION_ASPECT_RATIO,
        "generate_audio": False,
        "seed": None,
        "has_first_frame": False,
        "has_last_frame": False,
        "reference_image_count": 0,
        "provider_option_keys": [],
    }
    arguments.update(overrides)

    with pytest.raises(MultimodalServiceError) as blocked:
        await jobs._create_managed(**arguments)  # type: ignore[arg-type]  # noqa: SLF001

    assert (
        blocked.value.code
        == "provider_multimodal_video_parameters_not_certified"
    )
    assert blocked.value.route_receipt is not None
    assert blocked.value.route_receipt["call_count"] == 0
    assert runtime_requests == []


@pytest.mark.asyncio
async def test_managed_video_generation_binding_drift_closes_prepared_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _connection = await _activated_video_generation_service(
        tmp_path,
        monkeypatch,
    )
    runtime_requests: list[httpx.Request] = []
    jobs = _runtime_video_jobs(
        service,
        httpx.MockTransport(
            lambda request: runtime_requests.append(request) or httpx.Response(500)
        ),
    )
    original_prepare = jobs.managed_gateway.prepare_chat_dispatch

    async def drifted_prepare(*args, **kwargs):
        dispatch = await original_prepare(*args, **kwargs)
        dispatch.prepared = replace(
            dispatch.prepared,
            connection_fingerprint="f" * 64,
        )
        return dispatch

    monkeypatch.setattr(
        jobs.managed_gateway,
        "prepare_chat_dispatch",
        drifted_prepare,
    )

    with pytest.raises(MultimodalServiceError) as blocked:
        await jobs.create(
            model_id=MODEL_ID,
            prompt="Generate one drift-safe clip.",
            idempotency_key="r8e-binding-drift",
        )

    assert blocked.value.code == "provider_workload_binding_changed"
    assert runtime_requests == []
    receipts = service.repository.list_workload_receipts("local")
    assert len(receipts["calls"]) == 1
    assert receipts["calls"][0]["status"] == "failed"
    assert receipts["calls"][0]["dispatched"] == 0
    jobs_rows = service.repository.list_video_jobs("local", limit=10)
    assert len(jobs_rows) == 1
    assert jobs_rows[0]["status"] == "failed"
    assert jobs_rows[0]["provider_dispatch_state"] == "not_dispatched"


@pytest.mark.asyncio
async def test_managed_video_generation_uncertain_submit_is_never_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certification_requests: list[httpx.Request] = []
    certification_transport = _async_video_certification_transport(
        [
            {
                "id": "video-cert-job-1",
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "video-cert-generation-1",
                "unsigned_urls": ["/api/v1/videos/video-cert-job-1/content"],
            }
        ],
        certification_requests,
    )
    service, connection = _service(tmp_path, certification_transport)
    await _activate_video_generation(
        service,
        connection,
        certification_transport,
        monkeypatch,
    )
    runtime_requests: list[httpx.Request] = []

    def runtime_handler(request: httpx.Request) -> httpx.Response:
        runtime_requests.append(request)
        raise httpx.ReadTimeout("synthetic uncertain response", request=request)

    runtime_transport = httpx.MockTransport(runtime_handler)
    runtime_factory = lambda: httpx.AsyncClient(
        transport=runtime_transport,
        follow_redirects=False,
        trust_env=False,
    )
    jobs = VideoJobService(
        service,
        _VideoGenerationCatalogStub(service),  # type: ignore[arg-type]
        adapter=OpenRouterVideoJobAdapter(
            client_factory=runtime_factory,
            egress_policy=service.egress_policy,
        ),
        managed_gateway=ManagedMultimodalGateway.for_router(
            service,
            client_factory=runtime_factory,
        ),
    )

    with pytest.raises(MultimodalServiceError) as uncertain:
        await jobs.create(
            model_id=MODEL_ID,
            prompt="Generate one clip.",
            idempotency_key="r8e-runtime-video-timeout",
        )
    assert uncertain.value.code == "provider_result_uncertain"
    assert len(runtime_requests) == 1

    replay = await jobs.create(
        model_id=MODEL_ID,
        prompt="Do not send this as a second POST.",
        idempotency_key="r8e-runtime-video-timeout",
    )
    assert replay.status == "failed"
    assert replay.provider_dispatch_state == "uncertain"
    assert replay.retry_allowed is False
    assert replay.error is not None
    assert replay.error.code == "provider_result_uncertain"
    assert len(runtime_requests) == 1


def test_managed_video_generation_recovery_never_replays_submission(
    tmp_path: Path,
) -> None:
    transport = _certification_transport()
    service, connection = _service(tmp_path, transport)
    fingerprint = service.repository.connection_config_fingerprint(
        "local", connection.id
    )
    uncertain_id = "local_recovery_uncertain"
    pollable_id = "local_recovery_pollable"
    common = {
        "connection_id": connection.id,
        "requested_model": MODEL_ID,
        "provider": "openrouter",
        "duration": None,
        "resolution": None,
        "aspect_ratio": None,
        "generate_audio": False,
        "seed": None,
        "has_first_frame": False,
        "connection_fingerprint": fingerprint,
        "adapter_contract": "openrouter_video_jobs_v1",
        "protocol_version": "modelmirror-provider-multimodal-v1",
        "provider_dispatch_state": "dispatched",
        "post_dispatched": True,
    }
    service.repository.create_video_job_if_absent(
        "local",
        job_id=uncertain_id,
        idempotency_key_hash="a" * 64,
        workload_run_id=f"managed-reservation:{uncertain_id}",
        **common,
    )
    service.repository.create_video_job_if_absent(
        "local",
        job_id=pollable_id,
        idempotency_key_hash="b" * 64,
        workload_run_id=f"managed-reservation:{pollable_id}",
        **common,
    )
    service.repository.update_video_job(
        "local",
        pollable_id,
        upstream_job_id="existing-upstream-job",
    )
    jobs = VideoJobService(
        service,
        _VideoGenerationCatalogStub(service),  # type: ignore[arg-type]
    )

    jobs.recover_interrupted()

    uncertain = service.repository.get_video_job("local", uncertain_id)
    pollable = service.repository.get_video_job("local", pollable_id)
    assert uncertain is not None
    assert uncertain["status"] == "failed"
    assert uncertain["error_code"] == "provider_result_uncertain"
    assert uncertain["provider_dispatch_state"] == "uncertain"
    assert pollable is not None
    assert pollable["status"] == "queued"
    assert pollable["upstream_job_id"] == "existing-upstream-job"


@pytest.mark.asyncio
async def test_managed_video_generation_poll_blocks_connection_drift(
    tmp_path: Path,
) -> None:
    transport = _certification_transport()
    service, connection = _service(tmp_path, transport)
    fingerprint = service.repository.connection_config_fingerprint(
        "local", connection.id
    )
    job_id = "local_connection_drift"
    service.repository.create_video_job_if_absent(
        "local",
        job_id=job_id,
        idempotency_key_hash="c" * 64,
        connection_id=connection.id,
        requested_model=MODEL_ID,
        provider="openrouter",
        duration=None,
        resolution=None,
        aspect_ratio=None,
        generate_audio=False,
        seed=None,
        has_first_frame=False,
        workload_run_id=f"managed-reservation:{job_id}",
        connection_fingerprint=fingerprint,
        adapter_contract="openrouter_video_jobs_v1",
        protocol_version="modelmirror-provider-multimodal-v1",
        provider_dispatch_state="confirmed",
        post_dispatched=True,
    )
    service.repository.update_video_job(
        "local", job_id, upstream_job_id="existing-upstream-job"
    )
    service.repository.update_connection(
        "local",
        connection.id,
        RouterConnectionUpdate(api_key="rotated-preview-key"),
    )
    jobs = VideoJobService(
        service,
        _VideoGenerationCatalogStub(service),  # type: ignore[arg-type]
    )

    with pytest.raises(MultimodalServiceError) as blocked:
        await jobs.refresh(job_id)
    assert blocked.value.code == "video_job_connection_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "terminal", "expected_status", "expected_code"),
    [
        (
            "wrong-model",
            {
                "status": "completed",
                "model": "provider/other-video-model",
                "generation_id": "runtime-generation-terminal",
                "unsigned_urls": [
                    "/api/v1/videos/runtime-video-terminal/content?index=0"
                ],
            },
            "failed",
            "provider_workload_model_mismatch",
        ),
        (
            "missing-output",
            {
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "runtime-generation-terminal",
            },
            "failed",
            "provider_multimodal_video_output_metadata_invalid",
        ),
        (
            "empty-output",
            {
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "runtime-generation-terminal",
                "unsigned_urls": [],
            },
            "failed",
            "provider_multimodal_video_output_metadata_invalid",
        ),
        (
            "malformed-output",
            {
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "runtime-generation-terminal",
                "unsigned_urls": ["not-a-url"],
            },
            "failed",
            "provider_multimodal_video_output_metadata_invalid",
        ),
        (
            "multiple-outputs",
            {
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "runtime-generation-terminal",
                "unsigned_urls": [
                    "/api/v1/videos/runtime-video-terminal/content?index=0",
                    "/api/v1/videos/runtime-video-terminal/content?index=1",
                ],
            },
            "failed",
            "provider_multimodal_video_output_metadata_invalid",
        ),
        (
            "provider-failed",
            {"status": "failed", "model": MODEL_ID},
            "failed",
            "provider_generation_failed",
        ),
        (
            "provider-cancelled",
            {"status": "cancelled", "model": MODEL_ID},
            "cancelled",
            "provider_generation_cancelled",
        ),
        (
            "provider-expired",
            {"status": "expired", "model": MODEL_ID},
            "expired",
            "provider_generation_expired",
        ),
    ],
)
async def test_managed_video_generation_terminal_evidence_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    terminal: dict[str, object],
    expected_status: str,
    expected_code: str,
) -> None:
    service, _ = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "id": "runtime-video-terminal",
                    "status": "pending",
                    "model": MODEL_ID,
                },
            )
        payload = {"id": "runtime-video-terminal", **terminal}
        return httpx.Response(200, json=payload)

    jobs = _runtime_video_jobs(service, httpx.MockTransport(handler))
    created = await jobs.create(
        model_id=MODEL_ID,
        prompt="Generate one terminal-evidence clip.",
        idempotency_key=f"r8e-terminal-{case}",
    )
    completed = await jobs.refresh(created.job_id)

    assert completed.status == expected_status
    assert completed.output_count == 0
    assert completed.error is not None
    assert completed.error.code == expected_code
    assert completed.provider_route_receipts[0]["status"] == "failed"
    assert completed.provider_route_receipts[0]["call_count"] == 1
    assert len([item for item in requests if item.method == "POST"]) == 1
    assert len([item for item in requests if item.method == "GET"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("id", {"private": "provider-secret-sentinel"}),
        ("model", {"private": "provider-secret-sentinel"}),
        ("generation_id", "bad\nprovider-secret-sentinel"),
        ("generation_id", "x" * 257 + "provider-secret-sentinel"),
    ],
)
async def test_managed_video_generation_rejects_untrusted_identifiers_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    invalid_value: object,
) -> None:
    service, _ = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "id": "runtime-video-redaction",
                    "status": "pending",
                    "model": MODEL_ID,
                },
            )
        payload: dict[str, object] = {
            "id": "runtime-video-redaction",
            "status": "completed",
            "model": MODEL_ID,
            "generation_id": "runtime-generation-redaction",
            "unsigned_urls": [
                "/api/v1/videos/runtime-video-redaction/content?index=0"
            ],
        }
        payload[field] = invalid_value
        return httpx.Response(200, json=payload)

    jobs = _runtime_video_jobs(service, httpx.MockTransport(handler))
    created = await jobs.create(
        model_id=MODEL_ID,
        prompt="Generate one redaction-safe clip.",
        idempotency_key=f"r8e-redaction-{field}-{type(invalid_value).__name__}",
    )

    with pytest.raises(MultimodalServiceError) as invalid:
        await jobs.refresh(created.job_id)
    assert invalid.value.code == "invalid_upstream_response"

    stored = service.repository.get_video_job("local", created.job_id)
    projected = jobs.get(created.job_id)
    receipts = service.repository.list_workload_receipts("local")
    serialized = json.dumps(
        [stored, projected.model_dump(mode="json"), receipts],
        ensure_ascii=False,
    )
    assert "provider-secret-sentinel" not in serialized


@pytest.mark.asyncio
async def test_managed_video_generation_waits_for_actual_model_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )
    requests: list[httpx.Request] = []
    metadata_gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metadata_gets
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "id": "runtime-video-metadata",
                    "status": "pending",
                    "generation_id": "runtime-generation-metadata",
                },
            )
        if request.url.path.endswith("/generation"):
            metadata_gets += 1
            if metadata_gets == 1:
                return httpx.Response(503, json={"error": "not ready"})
            return httpx.Response(200, json={"data": {"model": MODEL_ID}})
        return httpx.Response(
            200,
            json={
                "id": "runtime-video-metadata",
                "status": "completed",
                "generation_id": "runtime-generation-metadata",
                "unsigned_urls": [
                    "/api/v1/videos/runtime-video-metadata/content?index=0"
                ],
            },
        )

    jobs = _runtime_video_jobs(service, httpx.MockTransport(handler))
    created = await jobs.create(
        model_id=MODEL_ID,
        prompt="Generate one metadata clip.",
        idempotency_key="r8e-metadata-pending",
    )
    pending = await jobs.refresh(created.job_id)
    assert pending.status == "running"
    assert pending.actual_model is None
    assert pending.output_count == 0
    assert pending.error is not None
    assert pending.error.code == "provider_multimodal_actual_model_pending"
    assert pending.provider_route_receipts[0]["status"] == "running"

    completed = await jobs.refresh(created.job_id)
    assert completed.status == "succeeded"
    assert completed.actual_model == MODEL_ID
    assert completed.output_count == 1
    assert completed.provider_route_receipts[0]["status"] == "passed"
    assert metadata_gets == 2
    assert len([item for item in requests if item.method == "POST"]) == 1
    assert len(
        [
            item
            for item in requests
            if item.method == "GET" and "/videos/" in item.url.path
        ]
    ) == 2


@pytest.mark.asyncio
async def test_managed_video_generation_discards_untrusted_metadata_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "id": "runtime-video-metadata-redaction",
                    "status": "pending",
                    "generation_id": "runtime-generation-metadata-redaction",
                },
            )
        if request.url.path.endswith("/generation"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "model": "bad\nprovider-secret-sentinel",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "runtime-video-metadata-redaction",
                "status": "completed",
                "generation_id": "runtime-generation-metadata-redaction",
                "unsigned_urls": [
                    "/api/v1/videos/runtime-video-metadata-redaction/content?index=0"
                ],
            },
        )

    jobs = _runtime_video_jobs(service, httpx.MockTransport(handler))
    created = await jobs.create(
        model_id=MODEL_ID,
        prompt="Generate one metadata-redaction clip.",
        idempotency_key="r8e-metadata-redaction",
    )
    pending = await jobs.refresh(created.job_id)

    assert pending.status == "running"
    assert pending.actual_model is None
    assert pending.error is not None
    assert pending.error.code == "provider_multimodal_actual_model_pending"
    assert len([item for item in requests if item.method == "POST"]) == 1
    serialized = json.dumps(
        [
            service.repository.get_video_job("local", created.job_id),
            pending.model_dump(mode="json"),
            service.repository.list_workload_receipts("local"),
        ],
        ensure_ascii=False,
    )
    assert "provider-secret-sentinel" not in serialized


@pytest.mark.asyncio
async def test_managed_video_generation_cancelled_submit_is_uncertain_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise asyncio.CancelledError

    jobs = _runtime_video_jobs(service, httpx.MockTransport(handler))
    with pytest.raises(asyncio.CancelledError):
        await jobs.create(
            model_id=MODEL_ID,
            prompt="Generate one cancellation clip.",
            idempotency_key="r8e-cancelled-submit",
        )

    replay = await jobs.create(
        model_id=MODEL_ID,
        prompt="Never dispatch this replay.",
        idempotency_key="r8e-cancelled-submit",
    )
    assert replay.status == "failed"
    assert replay.provider_dispatch_state == "uncertain"
    assert replay.retry_allowed is False
    assert replay.provider_route_receipts[0]["status"] == "uncertain"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_managed_video_generation_restart_resumes_get_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "id": "runtime-video-restart",
                    "status": "pending",
                    "model": MODEL_ID,
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "runtime-video-restart",
                "status": "completed",
                "model": MODEL_ID,
                "generation_id": "runtime-generation-restart",
                "unsigned_urls": [
                    "/api/v1/videos/runtime-video-restart/content?index=0"
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    jobs = _runtime_video_jobs(service, transport)
    created = await jobs.create(
        model_id=MODEL_ID,
        prompt="Generate one restart clip.",
        idempotency_key="r8e-restart-get-only",
    )
    stored = service.repository.get_video_job("local", created.job_id)
    assert stored is not None

    reopened = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted_call = reopened.get_workload_call(
        "local", str(stored["workload_call_id"])
    )
    assert restarted_call is not None
    assert restarted_call["status"] == "uncertain"
    restarted_service = ModelRouterService(
        reopened,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8", "1.1.1.1"]
        ),
    )
    completed = await _runtime_video_jobs(
        restarted_service, transport
    ).refresh(created.job_id)

    assert completed.status == "succeeded"
    assert completed.provider_route_receipts[0]["status"] == "passed"
    assert len([item for item in requests if item.method == "POST"]) == 1
    assert len([item for item in requests if item.method == "GET"]) == 1


@pytest.mark.asyncio
async def test_managed_video_generation_restart_recovers_dispatched_without_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, connection = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )
    gateway = ManagedMultimodalGateway.for_router(service)
    job_id = "local_restart_without_upstream_id"
    fingerprint = service.repository.connection_config_fingerprint(
        "local", connection.id
    )
    service.repository.create_video_job_if_absent(
        "local",
        job_id=job_id,
        idempotency_key_hash="d" * 64,
        connection_id=connection.id,
        requested_model=MODEL_ID,
        provider="openrouter",
        duration=None,
        resolution=None,
        aspect_ratio=None,
        generate_audio=False,
        seed=None,
        has_first_frame=False,
        workload_run_id=f"managed-reservation:{job_id}",
        connection_fingerprint=fingerprint,
        adapter_contract="openrouter_video_jobs_v1",
        protocol_version="modelmirror-provider-multimodal-v1",
    )
    dispatch = await gateway.prepare_chat_dispatch(
        "video_generation",
        execution_shape="video_generation_async",
        requested_model=MODEL_ID,
        parent_run_reference=f"video-job:{job_id}",
    )
    service.repository.update_video_job(
        "local",
        job_id,
        workload_run_id=dispatch.prepared.run_id,
        workload_call_id=dispatch.prepared.call_id,
        policy_fingerprint=dispatch.prepared.policy_fingerprint,
    )
    gateway.call_service.mark_dispatched(dispatch.prepared)
    service.repository.update_video_job(
        "local",
        job_id,
        provider_dispatch_state="dispatched",
        post_dispatched=True,
    )

    reopened = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted_service = ModelRouterService(
        reopened,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    restarted_jobs = VideoJobService(
        restarted_service,
        _VideoGenerationCatalogStub(restarted_service),  # type: ignore[arg-type]
    )
    restarted_jobs.recover_interrupted()

    recovered = restarted_jobs.get(job_id)
    assert recovered.status == "failed"
    assert recovered.provider_dispatch_state == "uncertain"
    assert recovered.retry_allowed is False
    assert recovered.provider_route_receipts[0]["status"] == "uncertain"
    assert recovered.provider_route_receipts[0]["call_count"] == 1


@pytest.mark.asyncio
async def test_video_job_poll_rejects_oversized_json_and_closes_response(
    tmp_path: Path,
) -> None:
    stream = _OversizedJSONStream()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    service, connection = _service(tmp_path, _certification_transport())
    adapter = OpenRouterVideoJobAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.egress_policy,
    )
    with pytest.raises(MultimodalServiceError) as oversized:
        await adapter.poll(
            OpenRouterTarget(
                base_url=connection.base_url,
                api_key="r8e-secret",
                connection_id=connection.id,
                cache_key=f"connection:{connection.id}",
            ),
            "runtime-video-oversized",
        )
    assert oversized.value.code == "invalid_upstream_response"
    assert stream.read_count == 2
    assert stream.closed is True


def test_managed_video_generation_recovery_filters_past_500_pollable_jobs(
    tmp_path: Path,
) -> None:
    service, connection = _service(tmp_path, _certification_transport())
    fingerprint = service.repository.connection_config_fingerprint(
        "local", connection.id
    )
    template_id = "local_recovery_template"
    service.repository.create_video_job_if_absent(
        "local",
        job_id=template_id,
        idempotency_key_hash="e" * 64,
        connection_id=connection.id,
        requested_model=MODEL_ID,
        provider="openrouter",
        duration=None,
        resolution=None,
        aspect_ratio=None,
        generate_audio=False,
        seed=None,
        has_first_frame=False,
        workload_run_id=f"managed-reservation:{template_id}",
        connection_fingerprint=fingerprint,
        adapter_contract="openrouter_video_jobs_v1",
        protocol_version="modelmirror-provider-multimodal-v1",
    )
    template = service.repository.get_video_job("local", template_id)
    assert template is not None
    columns = list(template)
    placeholders = ", ".join("?" for _ in columns)
    rows: list[tuple[object, ...]] = []
    for index in range(501):
        item = dict(template)
        item["id"] = f"local_pollable_{index:03d}"
        item["idempotency_key_hash"] = hashlib.sha256(
            f"pollable-{index}".encode()
        ).hexdigest()
        item["workload_run_id"] = f"managed-reservation:{item['id']}"
        item["upstream_job_id"] = f"upstream-{index:03d}"
        item["created_at"] = "2026-01-01T00:00:00+00:00"
        item["updated_at"] = item["created_at"]
        rows.append(tuple(item[column] for column in columns))
    tail_id = "local_recovery_after_500"
    tail = dict(template)
    tail["id"] = tail_id
    tail["idempotency_key_hash"] = "f" * 64
    tail["workload_run_id"] = f"managed-reservation:{tail_id}"
    tail["upstream_job_id"] = None
    tail["provider_dispatch_state"] = "dispatched"
    tail["post_dispatched"] = 1
    tail["created_at"] = "2026-01-02T00:00:00+00:00"
    tail["updated_at"] = tail["created_at"]
    rows.append(tuple(tail[column] for column in columns))
    with sqlite3.connect(service.repository.database_path) as connection_db:
        connection_db.executemany(
            f"INSERT INTO video_jobs ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            rows,
        )

    jobs = VideoJobService(
        service,
        _VideoGenerationCatalogStub(service),  # type: ignore[arg-type]
    )
    jobs.recover_interrupted()
    recovered = service.repository.get_video_job("local", tail_id)
    assert recovered is not None
    assert recovered["status"] == "failed"
    assert recovered["provider_dispatch_state"] == "uncertain"
    oldest = service.repository.get_video_job("local", "local_pollable_000")
    assert oldest is not None
    assert oldest["status"] == "queued"


@pytest.mark.asyncio
async def test_managed_video_generation_terminal_transaction_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = await _activated_video_generation_service(
        tmp_path, monkeypatch
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "id": "runtime-video-rollback",
                    "status": "pending",
                    "model": MODEL_ID,
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "runtime-video-rollback",
                "status": "completed",
                "model": MODEL_ID,
                "unsigned_urls": [
                    "/api/v1/videos/runtime-video-rollback/content?index=0"
                ],
            },
        )

    jobs = _runtime_video_jobs(service, httpx.MockTransport(handler))
    created = await jobs.create(
        model_id=MODEL_ID,
        prompt="Generate one rollback clip.",
        idempotency_key="r8e-terminal-rollback",
    )
    stored = service.repository.get_video_job("local", created.job_id)
    assert stored is not None
    call_id = str(stored["workload_call_id"])
    run_id = str(stored["workload_run_id"])
    with sqlite3.connect(service.repository.database_path) as connection_db:
        connection_db.execute(
            f"""
            CREATE TRIGGER fail_video_call_terminal_update
            BEFORE UPDATE ON provider_workload_calls
            WHEN OLD.id = '{call_id}'
            BEGIN
                SELECT RAISE(ABORT, 'synthetic terminal failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        await jobs.refresh(created.job_id)
    after_job = service.repository.get_video_job("local", created.job_id)
    after_call = service.repository.get_workload_call("local", call_id)
    after_run = service.repository.get_workload_run("local", run_id)
    assert after_job is not None
    assert after_job["status"] == stored["status"]
    assert after_job["actual_model"] == stored["actual_model"]
    assert after_job["output_count"] == stored["output_count"]
    assert after_call is not None and after_call["status"] == "running"
    assert after_run["status"] == "running"

    with sqlite3.connect(service.repository.database_path) as connection_db:
        connection_db.execute("DROP TRIGGER fail_video_call_terminal_update")
    completed = await jobs.refresh(created.job_id)
    assert completed.status == "succeeded"
    assert completed.provider_route_receipts[0]["status"] == "passed"


def _video_chat_payload(attachment_id: str) -> dict[str, object]:
    return {
        "model_id": MODEL_ID,
        "gateway": "default",
        "tool_mode": "none",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe the blue shape."},
                    {
                        "type": "input_video",
                        "attachment_id": attachment_id,
                    },
                ],
            }
        ],
    }


def _route_receipt(response_text: str) -> dict[str, object]:
    event = next(
        item
        for item in response_text.split("\n\n")
        if item.startswith("event: route_receipt")
    )
    return json.loads(
        next(
            line.removeprefix("data:").strip()
            for line in event.splitlines()
            if line.startswith("data:")
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "include_model", "expected_receipt_status"),
    [
        (200, True, "passed"),
        (200, False, "failed"),
        (500, True, "failed"),
    ],
)
async def test_api_chat_video_managed_stream_is_exact_and_never_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    include_model: bool,
    expected_receipt_status: str,
) -> None:
    certification_transport = _certification_transport()
    service, connection = _service(tmp_path / "router", certification_transport)
    await _activate_chat_video(
        service,
        connection,
        certification_transport,
        monkeypatch,
    )
    monkeypatch.setenv("MULTIMODAL_CHAT_VIDEO_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: (
            "https://legacy-must-not-run.example/v1/chat/completions",
            "legacy-secret",
        ),
    )
    sent: list[dict[str, object]] = []
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    attachment_id = store.create(
        kind="video",
        filename="fixture.mp4",
        content_type="video/mp4",
        content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
    ).attachment_id
    original_service = get_model_router_service()
    configure_model_router(service)
    configure_chat_attachment_store(store)
    attachment_error_code = ""
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            monkeypatch.setattr(
                main_module.httpx,
                "AsyncClient",
                lambda **_kwargs: _VideoChatClient(
                    sent,
                    status_code=status_code,
                    include_model=include_model,
                ),
            )
            response = await client.post(
                "/api/chat", json=_video_chat_payload(attachment_id)
            )
        try:
            store.claim(attachment_id)
        except MultimodalServiceError as exc:
            attachment_error_code = exc.code
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    request = sent[0]
    assert any(
        address in str(request["url"])
        for address in ("8.8.8.8", "1.1.1.1")
    )
    assert "legacy-must-not-run" not in str(request)
    body = request["json"]
    assert body["stream"] is True
    assert body["model"] == MODEL_ID
    video_part = body["messages"][0]["content"][1]
    assert video_part["type"] == "video_url"
    assert video_part["video_url"]["url"] == (
        video_fixture.SYNTHETIC_VIDEO_MP4_DATA_URL
    )
    assert attachment_error_code == "attachment_not_found"

    if status_code >= 400:
        assert response.status_code == status_code
        response_body = response.json()
        assert response_body["code"] == "provider_workload_http_5xx"
        receipt = response_body["route_receipt"]
        assert "redacted-upstream-body" not in response.text
    else:
        assert response.status_code == 200
        receipt = _route_receipt(response.text)
        assert response.text.count("data: [DONE]") == 1
        if include_model:
            assert "A blue square." in response.text
        else:
            assert "provider_multimodal_actual_model_unverified" in response.text
    assert receipt["status"] == expected_receipt_status
    assert receipt["call_count"] == 1
    stored = service.repository.list_workload_receipts("local")
    assert len(stored["calls"]) == 1
    assert stored["calls"][0]["dispatched"] == 1
    serialized = json.dumps(stored, ensure_ascii=False)
    assert "Describe the blue shape" not in serialized
    assert "A blue square" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_content", "expected_code", "forbidden_text"),
    [
        (
            (
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"safe prefix"},"finish_reason":null}]}\n\n'
                "event: route_receipt\n"
                'data: {"secret":"forged-receipt-secret"}\n\n'
            ).encode(),
            "provider_multimodal_reserved_sse_event",
            "forged-receipt-secret",
        ),
        (
            b'data: {"error":{"message":"upstream-secret-body"}}\n\n',
            "provider_multimodal_upstream_stream_error",
            "upstream-secret-body",
        ),
        (
            b"data: not-json-secret\n\n",
            "provider_multimodal_invalid_sse",
            "not-json-secret",
        ),
        (
            (
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"safe prefix"},"finish_reason":null}]}\n\n'
                "data: [DONE]\n\n"
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"post-done-secret"},"finish_reason":null}]}\n\n'
            ).encode(),
            "provider_multimodal_invalid_sse",
            "post-done-secret",
        ),
        (
            (
                f'data: {{"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"truncated-secret"},'
                '"finish_reason":"length"}]}\n\n'
            ).encode(),
            "provider_workload_output_truncated",
            "truncated-secret",
        ),
        (
            (
                f'data: {{"id":{{"private":"provider-secret-sentinel"}},'
                f'"model":"{MODEL_ID}","choices":'
                '[{"delta":{"content":"private-body-sentinel"},'
                '"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ).encode(),
            "provider_multimodal_invalid_sse",
            "provider-secret-sentinel",
        ),
    ],
)
async def test_api_chat_video_filters_untrusted_sse_before_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    upstream_content: bytes,
    expected_code: str,
    forbidden_text: str,
) -> None:
    certification_transport = _certification_transport()
    service, connection = _service(tmp_path / "router", certification_transport)
    await _activate_chat_video(
        service,
        connection,
        certification_transport,
        monkeypatch,
    )
    monkeypatch.setenv("MULTIMODAL_CHAT_VIDEO_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    sent: list[dict[str, object]] = []
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    attachment_id = store.create(
        kind="video",
        filename="fixture.mp4",
        content_type="video/mp4",
        content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
    ).attachment_id
    original_service = get_model_router_service()
    configure_model_router(service)
    configure_chat_attachment_store(store)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            monkeypatch.setattr(
                main_module.httpx,
                "AsyncClient",
                lambda **_kwargs: _VideoChatClient(
                    sent,
                    content_override=upstream_content,
                ),
            )
            response = await client.post(
                "/api/chat", json=_video_chat_payload(attachment_id)
            )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert response.status_code == 200
    assert len(sent) == 1
    assert forbidden_text not in response.text
    assert response.text.count("event: route_receipt") == 1
    assert expected_code in response.text
    receipt = _route_receipt(response.text)
    assert receipt["status"] == "failed"
    assert receipt["call_count"] == 1


@pytest.mark.asyncio
async def test_api_chat_video_transport_failure_consumes_once_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certification_transport = _certification_transport()
    service, connection = _service(tmp_path / "router", certification_transport)
    await _activate_chat_video(
        service,
        connection,
        certification_transport,
        monkeypatch,
    )
    monkeypatch.setenv("MULTIMODAL_CHAT_VIDEO_ENABLED", "true")
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    sent: list[dict[str, object]] = []
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    attachment_id = store.create(
        kind="video",
        filename="fixture.mp4",
        content_type="video/mp4",
        content=video_fixture.SYNTHETIC_VIDEO_MP4_BYTES,
    ).attachment_id
    original_service = get_model_router_service()
    configure_model_router(service)
    configure_chat_attachment_store(store)
    attachment_error_code = ""
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            monkeypatch.setattr(
                main_module.httpx,
                "AsyncClient",
                lambda **_kwargs: _VideoChatClient(
                    sent,
                    transport_error=httpx.ReadTimeout("synthetic timeout"),
                ),
            )
            response = await client.post(
                "/api/chat", json=_video_chat_payload(attachment_id)
            )
        try:
            store.claim(attachment_id)
        except MultimodalServiceError as exc:
            attachment_error_code = exc.code
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert response.status_code == 504
    assert response.json()["code"] == "provider_workload_timeout"
    assert response.json()["route_receipt"]["status"] == "uncertain"
    assert len(sent) == 1
    assert attachment_error_code == "attachment_not_found"
