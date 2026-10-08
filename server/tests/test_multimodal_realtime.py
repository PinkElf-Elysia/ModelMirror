from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from server.main import app
from server.model_router.egress import ProviderEgressError, ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderRealtimeCertificationCompleteRequest,
    ProviderRealtimeCertificationSessionRequest,
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import ProviderWorkloadControlService
from server.multimodal.api import configure_realtime_voice_service
from server.multimodal.realtime import (
    DirectOpenAITarget,
    OpenAIRealtimeAdapter,
    RealtimeCallEndEvidence,
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


def complete_runtime_evidence() -> RealtimeCallEndEvidence:
    return RealtimeCallEndEvidence(
        data_channel_open=True,
        local_audio_observed=True,
        outbound_audio_sent=True,
        input_speech_started=True,
        input_speech_stopped=True,
        response_created=True,
        remote_track_observed=True,
        output_audio_started=True,
        inbound_audio_received=True,
        remote_audio_observed=True,
        response_done=True,
        playback_started=True,
    )


def realtime_router_service(
    tmp_path: Path,
    *,
    tenant_id: str = "local",
    base_url: str = "https://api.openai.com/v1",
) -> tuple[ModelRouterService, SQLiteRouterRepository, str]:
    repository = SQLiteRouterRepository.open(tmp_path)
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


def managed_realtime_stack(
    tmp_path: Path,
    transport: httpx.AsyncBaseTransport,
    *,
    session_seconds: int = 600,
) -> tuple[RealtimeVoiceService, SQLiteRouterRepository, str]:
    repository = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="Official OpenAI Realtime",
            kind="openai",
            base_url="https://api.openai.com/v1",
            api_key="managed-openai-test-secret",
            scopes=["realtime"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-09-20T00:00:00+00:00",
    )
    client_factory = lambda: httpx.AsyncClient(  # noqa: E731
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    )
    router_service = ModelRouterService(
        repository,
        client_factory=client_factory,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8", "1.1.1.1"]
        ),
    )
    return (
        RealtimeVoiceService(
            router_service,
            adapter=OpenAIRealtimeAdapter(
                client_factory=client_factory,
                egress_policy=router_service.egress_policy,
            ),
            session_seconds=session_seconds,
        ),
        repository,
        connection.id,
    )


def managed_realtime_transport(
    requests: list[httpx.Request],
    *,
    create_status: int = 201,
    create_error: Exception | None = None,
    create_body: bytes | None = None,
    create_headers: dict[str, str] | None = None,
) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "gpt-realtime-2.1-mini"}]},
            )
        if request.url.path.endswith("/hangup"):
            return httpx.Response(200)
        if create_error is not None:
            raise create_error
        return httpx.Response(
            create_status,
            content=(
                create_body
                if create_body is not None
                else SDP_ANSWER.encode("utf-8")
                if create_status == 201
                else b"private-upstream-error-body"
            ),
            headers=(
                {
                    "content-type": "application/sdp",
                    "location": "/v1/realtime/calls/rtc_managed123",
                }
                if create_headers is None
                else create_headers
            ),
        )

    return httpx.MockTransport(handler)


async def certify_and_activate_realtime(
    service: RealtimeVoiceService,
    connection_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = await service.begin_certification(
        connection_id,
        ProviderRealtimeCertificationSessionRequest(
            model_id="gpt-realtime-2.1-mini",
            adapter_contract="openai_realtime_sdp_v1",
            offer_sdp=SDP_OFFER,
            acknowledge_billed_call=True,
        ),
        idempotency_key="r8f-browser-assisted-certification",
    )
    assert session.status == "running"
    assert session.answer_sdp == SDP_ANSWER
    completed = await service.complete_certification(
        session.certification_id,
        ProviderRealtimeCertificationCompleteRequest(
            media_observed=True,
            hangup_observed=True,
        ),
    )
    assert completed.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_REALTIME_VOICE_ENABLED", "true")
    control = ProviderWorkloadControlService(service.router_service)
    saved = control.update_policy(
        "realtime_voice",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="realtime_voice_session",
                    model_id="gpt-realtime-2.1-mini",
                    connection_id=connection_id,
                    adapter_contract="openai_realtime_sdp_v1",
                )
            ],
        ),
    )
    activated = control.activate(
        "realtime_voice",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert activated.effective_status == "managed_required"


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
    assert created.sdp_answer == SDP_ANSWER
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
    assert b'name="sdp"\r\n\r\n' in request_body
    assert b'name="session"\r\n\r\n' in request_body
    assert b'filename="offer.sdp"' not in request_body
    assert b'filename="session.json"' not in request_body
    assert SDP_OFFER.encode("utf-8") in request_body
    assert b'"type":"semantic_vad"' in request_body
    assert b'"output_modalities":["audio"]' in request_body
    assert b'"create_response":true' in request_body
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
    router_service = ModelRouterService(SQLiteRouterRepository.open(tmp_path))
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
async def test_realtime_logs_only_allowlisted_upstream_error_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(
        "WARNING",
        logger="modelmirror.multimodal.realtime",
    )
    requests: list[httpx.Request] = []
    private_message = "private provider detail sk-must-not-appear"
    adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=realtime_handler(
                requests,
                create_status=400,
                create_body=json.dumps(
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_value",
                            "param": "session.audio.input.turn_detection.type",
                            "message": private_message,
                        }
                    }
                ).encode("utf-8"),
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

    assert captured.value.code == "realtime_request_rejected"
    assert "error_type=invalid_request_error" in caplog.text
    assert "error_code=invalid_value" in caplog.text
    assert (
        "error_param=session.audio.input.turn_detection.type" in caplog.text
    )
    assert private_message not in caplog.text
    assert "private-key" not in caplog.text
    assert SDP_OFFER not in caplog.text


def test_realtime_ignores_unsafe_or_oversized_upstream_error_metadata() -> None:
    unsafe = httpx.Response(
        400,
        json={
            "error": {
                "type": "invalid_request_error\nsecret",
                "code": "invalid value",
                "param": "session.model?secret=sk-private",
                "message": "private provider detail",
            }
        },
    )
    oversized = httpx.Response(
        400,
        content=b"x" * (16_384 + 1),
    )

    assert OpenAIRealtimeAdapter._safe_error_metadata(unsafe) == {}
    assert OpenAIRealtimeAdapter._safe_error_metadata(oversized) == {}


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
async def test_realtime_restart_does_not_replay_an_inflight_hangup(
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
    created = await first_service.create(RealtimeCallRequest(sdp=SDP_OFFER))
    claimed, did_claim = repository.claim_realtime_hangup(
        "local",
        created.session_id,
    )
    assert claimed is not None
    assert did_claim is True
    assert claimed["status"] == "ending"

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

    assert recovery_requests == []
    row = repository.get_realtime_call("local", created.session_id)
    assert row is not None
    assert row["status"] == "interrupted"
    assert row["error_code"] == "realtime_restart_hangup_result_uncertain"
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
async def test_managed_realtime_certification_and_runtime_are_exact_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    certification_posts = [
        request
        for request in requests
        if request.method == "POST" and not request.url.path.endswith("/hangup")
    ]
    assert len(certification_posts) == 1
    assert SDP_OFFER.encode("utf-8") in certification_posts[0].content
    requests.clear()

    with pytest.raises(MultimodalServiceError) as missing_key:
        await service.create(RealtimeCallRequest(sdp=SDP_OFFER))
    assert missing_key.value.code == "invalid_idempotency_key"
    assert requests == []

    created = await service.create(
        RealtimeCallRequest(sdp=SDP_OFFER),
        idempotency_key="managed-runtime-session-1",
    )

    assert created.execution_mode == "managed"
    assert created.provider_dispatch_state == "confirmed"
    assert created.provider_route_receipts[0]["call_count"] == 1
    runtime_posts = [
        request
        for request in requests
        if request.method == "POST" and not request.url.path.endswith("/hangup")
    ]
    assert len(runtime_posts) == 1
    assert runtime_posts[0].url.host in {"8.8.8.8", "1.1.1.1"}
    assert runtime_posts[0].headers["host"] == "api.openai.com"

    with pytest.raises(MultimodalServiceError) as replay:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key="managed-runtime-session-1",
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(
        [
            request
            for request in requests
            if request.method == "POST"
            and not request.url.path.endswith("/hangup")
        ]
    ) == 1

    row = repository.get_realtime_call("local", created.session_id)
    assert row is not None
    evidence = json.dumps(
        {
            "realtime": row,
            "runs": repository.list_workload_receipts("local", limit=10)[
                "runs"
            ],
            "certifications": repository.list_workload_certifications("local"),
            "sessions": repository.list_multimodal_certification_sessions(
                "local", limit=10
            ),
        },
        ensure_ascii=False,
    )
    assert SDP_OFFER not in evidence
    assert SDP_ANSWER.strip() not in evidence
    assert "managed-openai-test-secret" not in evidence

    ended = await service.end(
        created.session_id,
        evidence=complete_runtime_evidence(),
    )
    assert ended.status == "ended"
    assert ended.provider_route_receipts[0]["call_count"] == 1
    assert ended.provider_route_receipts[0]["status"] == "passed"
    await service.shutdown()


@pytest.mark.asyncio
async def test_managed_realtime_missing_provider_speech_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    certification_requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=managed_realtime_transport(runtime_requests),
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )

    created = await service.create(
        RealtimeCallRequest(sdp=SDP_OFFER),
        idempotency_key="managed-runtime-missing-provider-speech",
    )
    ended = await service.end(
        created.session_id,
        evidence=RealtimeCallEndEvidence(
            data_channel_open=True,
            local_audio_observed=True,
            outbound_audio_sent=True,
        ),
    )

    expected_code = "provider_realtime_input_speech_not_observed"
    assert ended.status == "ended"
    assert ended.fallback_reason_codes == [expected_code]
    assert ended.provider_route_receipts[0]["status"] == "uncertain"
    row = repository.get_realtime_call("local", created.session_id)
    assert row is not None
    assert row["error_code"] == expected_code
    call = repository.get_workload_call(
        "local",
        str(row["workload_call_id"]),
    )
    assert call is not None
    assert call["status"] == "uncertain"
    assert call["result_class"] == "runtime_evidence_incomplete"
    assert call["error_code"] == expected_code
    assert len(
        [
            request
            for request in runtime_requests
            if request.method == "POST"
            and not request.url.path.endswith("/hangup")
        ]
    ) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_managed_realtime_egress_preflight_failure_closes_run_without_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    request_count_before_runtime = len(requests)

    async def reject_egress(_target: object) -> object:
        raise ProviderEgressError(
            "private_or_reserved_address",
            "blocked test target",
        )

    monkeypatch.setattr(
        service.call_service.multimodal_transport,
        "authorize",
        reject_egress,
    )
    key = "managed-runtime-egress-preflight-blocked"

    with pytest.raises(MultimodalServiceError) as blocked:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key=key,
        )

    assert blocked.value.code == "private_or_reserved_address"
    assert blocked.value.status_code == 409
    assert len(requests) == request_count_before_runtime
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    assert repository.get_realtime_call(
        "local",
        f"local_rt_{key_hash[:32]}",
    ) is None
    run_id = service.call_service.stable_run_id(
        "realtime_voice",
        f"realtime:{key_hash}",
    )
    run = repository.get_workload_run("local", run_id)
    assert run["status"] == "failed"
    assert run["result_class"] == "realtime_failed"
    assert json.loads(str(run["reason_codes_json"])) == [
        "private_or_reserved_address"
    ]
    assert [
        call
        for call in repository.list_workload_receipts("local")["calls"]
        if call["run_id"] == run_id
    ] == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_managed_realtime_silent_remote_audio_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    certification_requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=managed_realtime_transport(runtime_requests),
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )

    created = await service.create(
        RealtimeCallRequest(sdp=SDP_OFFER),
        idempotency_key="managed-runtime-silent-remote-audio",
    )
    evidence = complete_runtime_evidence().model_copy(
        update={"remote_audio_observed": False},
    )
    ended = await service.end(created.session_id, evidence=evidence)

    expected_code = "provider_realtime_remote_audio_not_observed"
    assert ended.status == "ended"
    assert ended.fallback_reason_codes == [expected_code]
    assert ended.provider_route_receipts[0]["status"] == "uncertain"
    row = repository.get_realtime_call("local", created.session_id)
    assert row is not None
    assert row["error_code"] == expected_code
    call = repository.get_workload_call(
        "local",
        str(row["workload_call_id"]),
    )
    assert call is not None
    assert call["status"] == "uncertain"
    assert call["result_class"] == "runtime_evidence_incomplete"
    assert len(
        [
            request
            for request in runtime_requests
            if request.method == "POST"
            and not request.url.path.endswith("/hangup")
        ]
    ) == 1
    await service.shutdown()


def test_realtime_certification_schema_preserves_sdp_wire_bytes() -> None:
    payload = ProviderRealtimeCertificationSessionRequest(
        model_id="gpt-realtime-2.1-mini",
        adapter_contract="openai_realtime_sdp_v1",
        offer_sdp=SDP_OFFER,
        acknowledge_billed_call=True,
    )

    assert payload.offer_sdp == SDP_OFFER
    assert payload.offer_sdp.endswith("\r\n")


def test_realtime_certification_completion_rejects_error_on_success() -> None:
    with pytest.raises(ValidationError):
        ProviderRealtimeCertificationCompleteRequest(
            media_observed=True,
            hangup_observed=True,
            browser_error_code=(
                "provider_realtime_browser_remote_description_failed"
            ),
        )


def test_realtime_certification_completion_rejects_diagnostics_on_success() -> None:
    with pytest.raises(ValidationError):
        ProviderRealtimeCertificationCompleteRequest(
            media_observed=True,
            hangup_observed=True,
            browser_diagnostic_codes=[
                "provider_realtime_browser_exception_operation_error"
            ],
        )


@pytest.mark.asyncio
async def test_managed_realtime_timeout_is_uncertain_and_never_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    certification_requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    runtime_transport = managed_realtime_transport(
        runtime_requests,
        create_error=httpx.ReadTimeout("provider result unknown"),
    )
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=runtime_transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )

    with pytest.raises(MultimodalServiceError) as uncertain:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key="managed-runtime-timeout",
        )
    assert uncertain.value.code == "provider_realtime_create_result_uncertain"
    assert "provider result unknown" not in uncertain.value.message
    session_id = (
        "local_rt_"
        + hashlib.sha256(b"managed-runtime-timeout").hexdigest()[:32]
    )
    row = repository.get_realtime_call("local", session_id)
    assert row is not None
    assert row["status"] == "interrupted"
    assert row["provider_dispatch_state"] == "uncertain"
    call = repository.get_workload_call("local", str(row["workload_call_id"]))
    assert call is not None
    assert call["status"] == "uncertain"
    assert call["provider_dispatch_state"] == "uncertain"

    with pytest.raises(MultimodalServiceError) as replay:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key="managed-runtime-timeout",
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(runtime_requests) == 1
    assert runtime_requests[0].url.host in {"8.8.8.8", "1.1.1.1"}
    await service.shutdown()


@pytest.mark.asyncio
async def test_managed_realtime_invalid_201_answer_is_uncertain_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    certification_requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    runtime_transport = managed_realtime_transport(
        runtime_requests,
        create_body=b"provider-created-but-invalid-sdp",
    )
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=runtime_transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )
    key = "managed-runtime-invalid-201-answer"

    with pytest.raises(MultimodalServiceError) as uncertain:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key=key,
        )
    assert uncertain.value.code == "provider_realtime_create_result_uncertain"
    assert "provider-created-but-invalid-sdp" not in uncertain.value.message
    session_id = "local_rt_" + hashlib.sha256(key.encode()).hexdigest()[:32]
    row = repository.get_realtime_call("local", session_id)
    assert row is not None
    assert row["provider_dispatch_state"] == "uncertain"
    assert row["error_code"] == "provider_realtime_create_result_uncertain"

    with pytest.raises(MultimodalServiceError) as replay:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key=key,
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(runtime_requests) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_realtime_certification_invalid_201_answer_is_uncertain_and_not_replayed(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(
            requests,
            create_body=b"provider-created-but-invalid-certification-sdp",
        ),
    )
    key = "managed-certification-invalid-201-answer"
    payload = ProviderRealtimeCertificationSessionRequest(
        model_id="gpt-realtime-2.1-mini",
        adapter_contract="openai_realtime_sdp_v1",
        offer_sdp=SDP_OFFER,
        acknowledge_billed_call=True,
    )

    with pytest.raises(RouterServiceError) as uncertain:
        await service.begin_certification(
            connection_id,
            payload,
            idempotency_key=key,
        )
    assert uncertain.value.code == "provider_realtime_create_result_uncertain"
    assert "provider-created-but-invalid-certification-sdp" not in (
        uncertain.value.hint
    )
    certifications = repository.list_workload_certifications(
        "local",
        connection_id=connection_id,
    )
    assert len(certifications) == 1
    certification = certifications[0]
    assert certification["status"] == "uncertain"
    session = repository.get_multimodal_certification_session(
        "local",
        certification_id=str(certification["id"]),
    )
    assert session is not None
    assert session["provider_dispatch_state"] == "uncertain"
    assert session["error_code"] == "provider_realtime_create_result_uncertain"

    with pytest.raises(RouterServiceError) as replay:
        await service.begin_certification(
            connection_id,
            payload,
            idempotency_key=key,
        )
    assert replay.value.code == "provider_realtime_certification_replay_blocked"
    assert len([request for request in requests if request.method == "POST"]) == 1
    await service.shutdown()


@pytest.mark.parametrize(
    ("create_error", "create_body", "create_headers", "expected_diagnostic"),
    [
        (
            httpx.ReadError("private transport detail sk-must-not-appear"),
            None,
            None,
            "provider_realtime_transport_read_error",
        ),
        (
            httpx.RemoteProtocolError(
                "private protocol detail sk-must-not-appear"
            ),
            None,
            None,
            "provider_realtime_transport_protocol_error",
        ),
        (
            None,
            b"private wrong-content-type body sk-must-not-appear",
            {
                "content-type": "application/json",
                "location": "/v1/realtime/calls/rtc_managed123",
            },
            "provider_realtime_answer_sdp_invalid",
        ),
        (
            None,
            b"private malformed SDP sk-must-not-appear",
            None,
            "provider_realtime_answer_sdp_invalid",
        ),
        (
            None,
            SDP_ANSWER.encode("utf-8"),
            {"content-type": "application/sdp"},
            "provider_realtime_answer_location_invalid",
        ),
    ],
)
@pytest.mark.asyncio
async def test_realtime_certification_persists_only_safe_uncertain_diagnostic(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    create_error: Exception | None,
    create_body: bytes | None,
    create_headers: dict[str, str] | None,
    expected_diagnostic: str,
) -> None:
    caplog.set_level("WARNING", logger="modelmirror.multimodal.realtime")
    requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(
            requests,
            create_error=create_error,
            create_body=create_body,
            create_headers=create_headers,
        ),
    )
    key = f"managed-certification-{expected_diagnostic}"
    payload = ProviderRealtimeCertificationSessionRequest(
        model_id="gpt-realtime-2.1-mini",
        adapter_contract="openai_realtime_sdp_v1",
        offer_sdp=SDP_OFFER,
        acknowledge_billed_call=True,
    )

    with pytest.raises(RouterServiceError) as uncertain:
        await service.begin_certification(
            connection_id,
            payload,
            idempotency_key=key,
        )

    assert uncertain.value.code == "provider_realtime_create_result_uncertain"
    certifications = repository.list_workload_certifications(
        "local",
        connection_id=connection_id,
    )
    assert len(certifications) == 1
    certification = certifications[0]
    assert certification["status"] == "uncertain"
    assert json.loads(str(certification["warnings_json"])) == [
        expected_diagnostic
    ]
    session = repository.get_multimodal_certification_session(
        "local",
        certification_id=str(certification["id"]),
    )
    assert session is not None
    assert session["provider_dispatch_state"] == "uncertain"
    assert session["error_code"] == "provider_realtime_create_result_uncertain"

    with pytest.raises(RouterServiceError) as replay:
        await service.begin_certification(
            connection_id,
            payload,
            idempotency_key=key,
        )
    assert replay.value.code == "provider_realtime_certification_replay_blocked"
    assert len([request for request in requests if request.method == "POST"]) == 1

    safe_evidence = json.dumps(
        {"certification": certification, "session": session},
        ensure_ascii=False,
    )
    assert expected_diagnostic in caplog.text
    assert expected_diagnostic in safe_evidence
    assert "sk-must-not-appear" not in caplog.text
    assert "sk-must-not-appear" not in safe_evidence
    assert SDP_OFFER not in caplog.text
    assert SDP_OFFER not in safe_evidence
    await service.shutdown()


@pytest.mark.parametrize(
    "create_headers",
    [
        {
            "content-type": "text/plain; charset=utf-8",
            "location": "/v1/realtime/calls/rtc_warning123",
        },
        {"location": "/v1/realtime/calls/rtc_warning123"},
    ],
)
@pytest.mark.asyncio
async def test_managed_realtime_accepts_valid_sdp_with_advisory_content_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    create_headers: dict[str, str],
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    caplog.set_level("WARNING", logger="modelmirror.multimodal.realtime")
    certification_requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=managed_realtime_transport(
                runtime_requests,
                create_headers=create_headers,
            ),
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )

    created = await service.create(
        RealtimeCallRequest(sdp=SDP_OFFER),
        idempotency_key="managed-runtime-advisory-content-type",
    )

    assert created.execution_mode == "managed"
    assert created.provider_dispatch_state == "confirmed"
    assert created.fallback_reason_codes == [
        "provider_realtime_answer_content_type_unexpected"
    ]
    assert len(
        [
            request
            for request in runtime_requests
            if request.method == "POST"
            and not request.url.path.endswith("/hangup")
        ]
    ) == 1
    assert (
        "realtime_provider_warning "
        "diagnostic_code=provider_realtime_answer_content_type_unexpected"
    ) in caplog.text
    assert (
        "realtime_create_uncertain "
        "diagnostic_code=provider_realtime_answer_content_type_unexpected"
    ) not in caplog.text
    row = repository.get_realtime_call("local", created.session_id)
    assert row is not None
    evidence = json.dumps(row, ensure_ascii=False)
    assert SDP_OFFER not in evidence
    assert SDP_ANSWER.strip() not in evidence
    assert "managed-openai-test-secret" not in evidence
    await service.end(created.session_id)
    await service.shutdown()


@pytest.mark.asyncio
async def test_realtime_certification_preserves_advisory_content_type_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger="modelmirror.multimodal.realtime")
    requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(
            requests,
            create_headers={
                "content-type": "text/plain; charset=utf-8",
                "location": "/v1/realtime/calls/rtc_warning123",
            },
        ),
    )

    session = await service.begin_certification(
        connection_id,
        ProviderRealtimeCertificationSessionRequest(
            model_id="gpt-realtime-2.1-mini",
            adapter_contract="openai_realtime_sdp_v1",
            offer_sdp=SDP_OFFER,
            acknowledge_billed_call=True,
        ),
        idempotency_key="managed-certification-advisory-content-type",
    )

    assert session.status == "running"
    certification = repository.get_workload_certification(
        "local",
        session.certification_id,
    )
    assert certification is not None
    assert json.loads(str(certification["warnings_json"])) == [
        "provider_realtime_answer_content_type_unexpected"
    ]

    completed = await service.complete_certification(
        session.certification_id,
        ProviderRealtimeCertificationCompleteRequest(
            media_observed=True,
            hangup_observed=True,
        ),
    )

    assert completed.status == "passed"
    assert completed.warning_codes == [
        "provider_realtime_answer_content_type_unexpected"
    ]
    assert len(
        [
            request
            for request in requests
            if request.method == "POST"
            and not request.url.path.endswith("/hangup")
        ]
    ) == 1
    persisted = repository.get_workload_certification(
        "local",
        session.certification_id,
    )
    assert persisted is not None
    safe_evidence = json.dumps(persisted, ensure_ascii=False)
    assert "provider_realtime_answer_content_type_unexpected" in safe_evidence
    assert SDP_OFFER not in safe_evidence
    assert SDP_ANSWER.strip() not in safe_evidence
    assert "managed-openai-test-secret" not in safe_evidence
    assert (
        "realtime_provider_warning "
        "diagnostic_code=provider_realtime_answer_content_type_unexpected"
    ) in caplog.text
    assert (
        "realtime_create_uncertain "
        "diagnostic_code=provider_realtime_answer_content_type_unexpected"
    ) not in caplog.text
    await service.shutdown()


@pytest.mark.asyncio
async def test_realtime_certification_persists_browser_handshake_failure_once(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(requests),
    )
    session = await service.begin_certification(
        connection_id,
        ProviderRealtimeCertificationSessionRequest(
            model_id="gpt-realtime-2.1-mini",
            adapter_contract="openai_realtime_sdp_v1",
            offer_sdp=SDP_OFFER,
            acknowledge_billed_call=True,
        ),
        idempotency_key="managed-certification-browser-handshake-failure",
    )

    completed = await service.complete_certification(
        session.certification_id,
        ProviderRealtimeCertificationCompleteRequest(
            media_observed=False,
            hangup_observed=True,
            browser_error_code=(
                "provider_realtime_browser_remote_description_failed"
            ),
            browser_diagnostic_codes=[
                "provider_realtime_browser_exception_invalid_access_error",
                "provider_realtime_answer_terminal_crlf_missing",
            ],
        ),
    )

    expected_error = "provider_realtime_browser_remote_description_failed"
    assert completed.status == "failed"
    assert completed.error_code == expected_error
    certification = repository.get_workload_certification(
        "local",
        session.certification_id,
    )
    assert certification is not None
    assert certification["error_code"] == expected_error
    assert json.loads(str(certification["warnings_json"])) == [
        "provider_realtime_browser_exception_invalid_access_error",
        "provider_realtime_answer_terminal_crlf_missing",
    ]
    certification_session = (
        repository.get_multimodal_certification_session(
            "local",
            certification_id=session.certification_id,
        )
    )
    assert certification_session is not None
    assert certification_session["error_code"] == expected_error
    realtime = repository.get_realtime_call(
        "local",
        str(certification_session["id"]),
    )
    assert realtime is not None
    assert realtime["status"] == "interrupted"
    assert realtime["error_code"] == expected_error
    create_posts = [
        request
        for request in requests
        if request.method == "POST" and not request.url.path.endswith("/hangup")
    ]
    hangup_posts = [
        request
        for request in requests
        if request.method == "POST" and request.url.path.endswith("/hangup")
    ]
    assert len(create_posts) == 1
    assert len(hangup_posts) == 1
    safe_evidence = json.dumps(
        {
            "certification": certification,
            "session": certification_session,
            "realtime": realtime,
        },
        ensure_ascii=False,
    )
    assert SDP_OFFER not in safe_evidence
    assert SDP_ANSWER.strip() not in safe_evidence
    assert "managed-openai-test-secret" not in safe_evidence
    await service.shutdown()


@pytest.mark.asyncio
async def test_managed_realtime_cancel_after_dispatch_is_uncertain_and_not_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    certification_requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    runtime_transport = managed_realtime_transport(
        runtime_requests,
        create_error=asyncio.CancelledError(),
    )
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=runtime_transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )
    key = "managed-runtime-cancelled-after-dispatch"

    with pytest.raises(asyncio.CancelledError):
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key=key,
        )

    session_id = "local_rt_" + hashlib.sha256(key.encode()).hexdigest()[:32]
    row = repository.get_realtime_call("local", session_id)
    assert row is not None
    assert row["provider_dispatch_state"] == "uncertain"
    assert row["error_code"] == "provider_realtime_create_result_uncertain"
    call = repository.get_workload_call("local", str(row["workload_call_id"]))
    assert call is not None
    assert call["status"] == "uncertain"
    assert call["provider_dispatch_state"] == "uncertain"

    with pytest.raises(MultimodalServiceError) as replay:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key=key,
        )
    assert replay.value.code == "provider_workload_logical_run_replay_blocked"
    assert len(runtime_requests) == 1
    await service.shutdown()


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, "realtime_credentials_invalid"),
        (429, "realtime_rate_limited"),
        (503, "realtime_provider_error"),
    ],
)
@pytest.mark.asyncio
async def test_managed_realtime_provider_failures_are_determinate_and_single_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_code: str,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    certification_requests: list[httpx.Request] = []
    service, repository, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    runtime_transport = managed_realtime_transport(
        runtime_requests,
        create_status=status_code,
    )
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=runtime_transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )
    key = f"managed-runtime-{status_code}"

    with pytest.raises(MultimodalServiceError) as failed:
        await service.create(
            RealtimeCallRequest(sdp=SDP_OFFER),
            idempotency_key=key,
        )
    assert failed.value.code == expected_code
    session_id = "local_rt_" + hashlib.sha256(key.encode()).hexdigest()[:32]
    row = repository.get_realtime_call("local", session_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["provider_dispatch_state"] == "confirmed"
    assert len(runtime_requests) == 1
    assert "private-upstream-error-body" not in failed.value.message
    await service.shutdown()


@pytest.mark.asyncio
async def test_managed_realtime_concurrent_hangup_sends_only_one_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_REALTIME_VOICE_ENABLED", "true")
    certification_requests: list[httpx.Request] = []
    service, _, connection_id = managed_realtime_stack(
        tmp_path,
        managed_realtime_transport(certification_requests),
    )
    await certify_and_activate_realtime(service, connection_id, monkeypatch)
    runtime_requests: list[httpx.Request] = []
    hangup_started = asyncio.Event()
    release_hangup = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        runtime_requests.append(request)
        if request.url.path.endswith("/hangup"):
            hangup_started.set()
            await release_hangup.wait()
            return httpx.Response(200)
        return httpx.Response(
            201,
            content=SDP_ANSWER.encode(),
            headers={
                "content-type": "application/sdp",
                "location": "/v1/realtime/calls/rtc_concurrent",
            },
        )

    runtime_transport = httpx.MockTransport(handler)
    service.adapter = OpenAIRealtimeAdapter(
        client_factory=lambda: httpx.AsyncClient(
            transport=runtime_transport,
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=service.router_service.egress_policy,
    )
    created = await service.create(
        RealtimeCallRequest(sdp=SDP_OFFER),
        idempotency_key="managed-runtime-concurrent-hangup",
    )
    first = asyncio.create_task(service.end(created.session_id))
    await asyncio.wait_for(hangup_started.wait(), timeout=1)
    with pytest.raises(MultimodalServiceError) as duplicate:
        await service.end(created.session_id)
    assert duplicate.value.code == "realtime_hangup_in_progress"
    release_hangup.set()
    assert (await first).status == "ended"
    assert len(
        [
            request
            for request in runtime_requests
            if request.url.path.endswith("/hangup")
        ]
    ) == 1
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
                "execution_mode",
                "provider_route_receipts",
                "provider_dispatch_state",
                "fallback_reason_codes",
            }
            assert payload["execution_mode"] == "legacy"
            assert payload["provider_route_receipts"] == []
            assert payload["provider_dispatch_state"] is None
            assert payload["fallback_reason_codes"] == []
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
