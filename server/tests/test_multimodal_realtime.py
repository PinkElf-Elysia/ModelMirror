from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from server.main import app
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate
from server.model_router.service import ModelRouterService
from server.multimodal.api import configure_realtime_voice_service
from server.multimodal.realtime import (
    DirectOpenAITarget,
    OpenAIRealtimeAdapter,
    RealtimeCallRequest,
    RealtimeVoiceService,
)
from server.multimodal.stt import MultimodalServiceError


SDP_OFFER = (
    "v=0\r\n"
    "o=- 1 1 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
)
SDP_ANSWER = (
    "v=0\r\n"
    "o=- 2 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
)


def realtime_router_service(
    tmp_path: Path,
    *,
    tenant_id: str = "local",
    base_url: str = "https://api.openai.com/v1",
) -> tuple[ModelRouterService, SQLiteRouterRepository, str]:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        tenant_id,
        RouterConnectionCreate(
            name="OpenAI Realtime",
            kind="openai",
            base_url=base_url,
            api_key="direct-openai-test-secret",
        ),
    )
    repository.save_test_result(
        tenant_id,
        connection.id,
        health="online",
        model_count=2,
        checked_at="2026-07-29T00:00:00+00:00",
    )
    return (
        ModelRouterService(repository, tenant_id=tenant_id),
        repository,
        connection.id,
    )


def realtime_handler(
    requests: list[httpx.Request],
    *,
    create_status: int = 201,
    create_body: bytes | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/hangup"):
            return httpx.Response(200)
        return httpx.Response(
            create_status,
            content=(
                create_body
                if create_body is not None
                else SDP_ANSWER.encode("utf-8")
            ),
            headers={
                "content-type": "application/sdp",
                "location": "/v1/realtime/calls/rtc_test123",
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_realtime_call_uses_multipart_and_tenant_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    router_service, repository, connection_id = realtime_router_service(
        tmp_path
    )
    requests: list[httpx.Request] = []
    adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=realtime_handler(requests)
        )
    )
    service = RealtimeVoiceService(router_service, adapter=adapter)

    created = await service.create(
        RealtimeCallRequest(
            sdp=SDP_OFFER,
            model_id="gpt-realtime-2.1-mini",
            voice="marin",
            vad_mode="semantic_vad",
            language="zh-CN",
        )
    )

    assert created.session_id.startswith("local_rt_")
    assert created.sdp_answer == SDP_ANSWER.strip()
    assert created.model_id == "gpt-realtime-2.1-mini"
    assert requests[0].url == "https://api.openai.com/v1/realtime/calls"
    assert requests[0].headers["authorization"] == (
        "Bearer direct-openai-test-secret"
    )
    assert requests[0].headers["openai-safety-identifier"].startswith("mm_")
    assert requests[0].headers["content-type"].startswith(
        "multipart/form-data"
    )
    request_body = requests[0].content
    assert SDP_OFFER.encode("utf-8") in request_body
    assert b'"type":"semantic_vad"' in request_body
    assert b'"interrupt_response":true' in request_body
    assert b'"model":"gpt-realtime-2.1-mini"' in request_body
    assert b"direct-openai-test-secret" not in request_body

    row = repository.get_realtime_call("local", created.session_id)
    assert row is not None
    assert row["status"] == "active"
    assert row["connection_id"] == connection_id
    assert row["upstream_call_id"] == "rtc_test123"
    serialized = json.dumps(row, ensure_ascii=False)
    assert SDP_OFFER not in serialized
    assert "direct-openai-test-secret" not in serialized

    diagnostics = repository.get_diagnostics("local", limit=5)
    assert diagnostics["recent_decisions"][0]["operation"] == "realtime_voice"
    assert diagnostics["recent_decisions"][0]["outcome"] == "active"

    ended = await service.end(created.session_id)
    assert ended.status == "ended"
    assert requests[-1].url.path.endswith(
        "/v1/realtime/calls/rtc_test123/hangup"
    )
    ended_row = repository.get_realtime_call("local", created.session_id)
    assert ended_row is not None
    assert ended_row["status"] == "ended"
    assert ended_row["duration_seconds"] is not None
    assert ended_row["cost_kind"] == "unavailable"
    await service.shutdown()


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            RealtimeCallRequest(sdp="not-an-sdp"),
            "invalid_realtime_sdp",
        ),
        (
            RealtimeCallRequest(
                sdp=SDP_OFFER,
                model_id="gpt-realtime-unverified",
            ),
            "unsupported_realtime_model",
        ),
        (
            RealtimeCallRequest(sdp=SDP_OFFER, voice="custom-voice"),
            "unsupported_realtime_voice",
        ),
        (
            RealtimeCallRequest(sdp=SDP_OFFER, vad_mode="server_vad"),
            "unsupported_realtime_vad",
        ),
        (
            RealtimeCallRequest(sdp=SDP_OFFER, language="bad language"),
            "invalid_realtime_language",
        ),
    ],
)
@pytest.mark.asyncio
async def test_realtime_rejects_unverified_contract_values_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: RealtimeCallRequest,
    expected_code: str,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    router_service = ModelRouterService(SQLiteRouterRepository(tmp_path))
    service = RealtimeVoiceService(router_service)

    with pytest.raises(MultimodalServiceError) as captured:
        await service.create(payload)

    assert captured.value.code == expected_code
    assert SDP_OFFER not in captured.value.message


@pytest.mark.asyncio
async def test_realtime_requires_feature_and_official_openai_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router_service, _, _ = realtime_router_service(
        tmp_path,
        base_url="https://example.invalid/v1",
    )
    service = RealtimeVoiceService(router_service)
    payload = RealtimeCallRequest(sdp=SDP_OFFER)

    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "false")
    with pytest.raises(MultimodalServiceError) as disabled:
        await service.create(payload)
    assert disabled.value.code == "realtime_voice_disabled"

    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    with pytest.raises(MultimodalServiceError) as invalid_target:
        await service.create(payload)
    assert invalid_target.value.code == "invalid_realtime_connection"


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_code"),
    [
        (400, 422, "realtime_request_rejected"),
        (401, 401, "realtime_credentials_invalid"),
        (402, 402, "realtime_payment_required"),
        (403, 403, "realtime_access_denied"),
        (429, 429, "realtime_rate_limited"),
        (500, 502, "realtime_provider_error"),
    ],
)
@pytest.mark.asyncio
async def test_realtime_translates_upstream_errors_without_body(
    status: int,
    expected_status: int,
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []
    adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=realtime_handler(
                requests,
                create_status=status,
                create_body=b"private provider error and secret",
            )
        )
    )
    target = DirectOpenAITarget(
        base_url="https://api.openai.com/v1",
        api_key="private-key",
        connection_id="conn_test",
        safety_identifier="mm_test",
    )

    with pytest.raises(MultimodalServiceError) as captured:
        await adapter.create_call(
            target,
            sdp=SDP_OFFER,
            session={"type": "realtime", "model": "gpt-realtime-2.1-mini"},
        )

    assert captured.value.status_code == expected_status
    assert captured.value.code == expected_code
    assert "private provider error" not in captured.value.message
    assert "private-key" not in captured.value.message


@pytest.mark.asyncio
async def test_realtime_session_is_tenant_scoped_and_recovered_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    router_service, repository, _ = realtime_router_service(tmp_path)
    first_requests: list[httpx.Request] = []
    first_service = RealtimeVoiceService(
        router_service,
        adapter=OpenAIRealtimeAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=realtime_handler(first_requests)
            )
        ),
    )
    created = await first_service.create(
        RealtimeCallRequest(sdp=SDP_OFFER)
    )

    other_service = RealtimeVoiceService(
        ModelRouterService(repository, tenant_id="other")
    )
    with pytest.raises(MultimodalServiceError) as hidden:
        await other_service.end(created.session_id)
    assert hidden.value.code == "realtime_session_not_found"

    recovery_requests: list[httpx.Request] = []
    recovery = RealtimeVoiceService(
        router_service,
        adapter=OpenAIRealtimeAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=realtime_handler(recovery_requests)
            )
        ),
    )
    await recovery.recover_active()

    row = repository.get_realtime_call("local", created.session_id)
    assert row is not None
    assert row["status"] == "interrupted"
    assert row["error_code"] == "realtime_restart_interrupted"
    assert recovery_requests[-1].url.path.endswith("/hangup")
    await first_service.shutdown()
    await recovery.shutdown()


@pytest.mark.asyncio
async def test_realtime_session_hard_limit_hangs_up_and_marks_expired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    router_service, repository, _ = realtime_router_service(tmp_path)
    requests: list[httpx.Request] = []
    service = RealtimeVoiceService(
        router_service,
        adapter=OpenAIRealtimeAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=realtime_handler(requests)
            )
        ),
        session_seconds=1,
    )
    created = await service.create(
        RealtimeCallRequest(sdp=SDP_OFFER)
    )

    for _ in range(30):
        row = repository.get_realtime_call("local", created.session_id)
        if row is not None and row["status"] == "expired":
            break
        await asyncio.sleep(0.05)

    assert row is not None
    assert row["status"] == "expired"
    assert requests[-1].url.path.endswith("/hangup")
    await service.shutdown()


@pytest.mark.asyncio
async def test_realtime_api_returns_only_safe_session_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    router_service, _, _ = realtime_router_service(tmp_path)
    requests: list[httpx.Request] = []
    service = RealtimeVoiceService(
        router_service,
        adapter=OpenAIRealtimeAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=realtime_handler(requests)
            )
        ),
    )
    configure_realtime_voice_service(service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            created = await client.post(
                "/api/multimodal/realtime/calls",
                json={
                    "sdp": SDP_OFFER,
                    "model_id": "gpt-realtime-2.1",
                    "voice": "cedar",
                    "vad_mode": "semantic_vad",
                    "language": "zh-CN",
                },
            )
            assert created.status_code == 200
            payload: dict[str, Any] = created.json()
            assert set(payload) == {
                "session_id",
                "sdp_answer",
                "expires_at",
                "model_id",
                "voice",
            }
            assert "private" not in created.text
            ended = await client.delete(
                (
                    "/api/multimodal/realtime/calls/"
                    f"{payload['session_id']}"
                )
            )
            assert ended.status_code == 200
            assert ended.json()["status"] == "ended"
    finally:
        await service.shutdown()
        configure_realtime_voice_service(None)
