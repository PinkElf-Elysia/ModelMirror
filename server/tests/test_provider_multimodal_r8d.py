from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import (
    R8D_AUDIO_PARAMETER_CONTRACT_VERSION,
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
    r8d_audio_certification_evidence_reason,
    r8d_audio_parameter_profile_reason,
)
from server.multimodal.api import configure_audio_job_service, router
from server.multimodal.audio_catalog import AudioCatalogService
from server.multimodal.audio_jobs import (
    AudioJobService,
    OpenRouterAudioJobAdapter,
)
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget


MP3_FRAME = b"\xff\xfb\x90\xc0" + b"\x55" * 413
MP3_BYTES = MP3_FRAME * 3


@pytest.mark.asyncio
async def test_managed_pinned_provider_url_is_redacted_from_httpx_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_url = "https://10.9.8.7:8443/private/audio?token=marker"
    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, request=request)
        )
    ) as client:
        await client.get(secret_url)

    assert "10.9.8.7" not in caplog.text
    assert "8443" not in caplog.text
    assert "/private/audio" not in caplog.text
    assert "token=marker" not in caplog.text
    assert "provider-address-redacted" in caplog.text


def _service(
    tmp_path: Path,
    transport: httpx.AsyncBaseTransport,
    *,
    resolver_addresses: list[str] | None = None,
) -> tuple[ModelRouterService, object]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8D audio stream",
            kind="openrouter",
            base_url="https://provider.example/v1",
            api_key="r8d-secret",
            scopes=["chat", "audio"],
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
            resolver=lambda _host, _port: resolver_addresses or ["8.8.8.8"]
        ),
    )
    return service, connection


def _sse_body(
    model_id: str,
    shape: str,
    *,
    input_text: str = "Okay",
) -> bytes:
    first_delta: dict[str, object]
    if shape == "chat_audio_input":
        first_delta = {"content": input_text}
    else:
        encoded = base64.b64encode(MP3_BYTES).decode("ascii")
        first_delta = {"audio": {"data": encoded[:503]}}
    events: list[dict[str, object]] = [
        {
            "id": "generation-r8d",
            "model": model_id,
            "choices": [{"delta": first_delta, "finish_reason": None}],
        }
    ]
    if shape != "chat_audio_input":
        encoded = base64.b64encode(MP3_BYTES).decode("ascii")
        events.append(
            {
                "choices": [
                    {
                        "delta": {"audio": {"data": encoded[503:]}},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            }
        )
    else:
        events.append(
            {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        )
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return (body + "data: [DONE]\n\n").encode()


async def _activate_audio_generation(
    service: ModelRouterService,
    connection: object,
    transport: httpx.AsyncBaseTransport,
    monkeypatch: pytest.MonkeyPatch,
    *,
    model_id: str,
) -> None:
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="audio_generation_stream",
            model_id=model_id,
            adapter_contract="openrouter_audio_generation_stream_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="r8d-audio-generation-certification",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_AUDIO_GENERATION_ENABLED", "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        "audio_generation",
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape="audio_generation_stream",
                    model_id=model_id,
                    connection_id=connection.id,
                    adapter_contract=(
                        "openrouter_audio_generation_stream_v1"
                    ),
                )
            ],
        ),
    )
    activated = control.activate(
        "audio_generation",
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert activated.effective_status == "managed_required"


def _independently_padded_audio_sse(model_id: str) -> bytes:
    events = []
    for index in range(3):
        events.append(
            {
                "id": "runtime-generation",
                "model": model_id,
                "choices": [
                    {
                        "delta": {
                            "audio": {
                                "data": base64.b64encode(MP3_FRAME).decode(
                                    "ascii"
                                )
                            }
                        },
                        "finish_reason": "stop" if index == 2 else None,
                    }
                ],
                "usage": (
                    {"cost": 0.01, "total_tokens": 7}
                    if index == 2
                    else None
                ),
            }
        )
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return (body + "data: [DONE]\n\n").encode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "entry_id", "adapter", "feature_flag"),
    [
        (
            "chat_audio_input",
            "chat_audio_input",
            "openrouter_chat_audio_v1",
            "MODEL_CONTROL_CHAT_AUDIO_ENABLED",
        ),
        (
            "chat_audio_output",
            "chat_audio_output",
            "openrouter_chat_audio_v1",
            "MODEL_CONTROL_CHAT_AUDIO_ENABLED",
        ),
        (
            "audio_generation_stream",
            "audio_generation",
            "openrouter_audio_generation_stream_v1",
            "MODEL_CONTROL_AUDIO_GENERATION_ENABLED",
        ),
    ],
)
async def test_r8d_certification_is_shape_specific_single_post_and_qualifies_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
    entry_id: str,
    adapter: str,
    feature_flag: str,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "provider/audio-r8d"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "provider.example"
        assert request.headers["authorization"] == "Bearer r8d-secret"
        assert request.headers["accept"] == "text/event-stream"
        body = json.loads(request.content)
        assert body["model"] == model_id
        assert body["stream"] is True
        if shape == "chat_audio_input":
            audio = body["messages"][0]["content"][1]["input_audio"]
            assert audio["format"] == "wav"
            assert base64.b64decode(audio["data"], validate=True).startswith(b"RIFF")
            assert "modalities" not in body
        elif shape == "chat_audio_output":
            assert body["modalities"] == ["text", "audio"]
            assert body["audio"] == {"voice": "alloy", "format": "mp3"}
        else:
            assert body["messages"][0]["content"] == (
                "Generate a short neutral musical tone."
            )
            assert "modalities" not in body
        return httpx.Response(
            200,
            content=_sse_body(model_id, shape),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    request = ProviderWorkloadCertificationRequest(
        execution_shape=shape,  # type: ignore[arg-type]
        model_id=model_id,
        adapter_contract=adapter,  # type: ignore[arg-type]
        acknowledge_billed_call=True,
    )
    result = await certifications.run(
        connection.id,
        request,
        idempotency_key=f"r8d-{shape}",
    )
    replay = await certifications.run(
        connection.id,
        request,
        idempotency_key=f"r8d-{shape}",
    )

    assert result.status == replay.status == "passed"
    assert result.actual_model == model_id
    assert result.provider_dispatch_state == "confirmed"
    assert result.retry_allowed is False
    assert result.checks.http_ok is True
    assert result.checks.response_complete is True
    assert result.checks.content_observed is True
    assert result.checks.media_format_verified is True
    assert result.checks.terminal_signal_verified is True
    assert result.checks.actual_model_verified is True
    assert [item.method for item in requests].count("POST") == 1

    row = service.repository.get_workload_certification(
        "local", str(result.certification_id)
    )
    assert row is not None
    profile = json.loads(str(row["profile_json"]))
    assert profile["audio_parameter_contract_version"] == (
        R8D_AUDIO_PARAMETER_CONTRACT_VERSION
    )
    assert profile["stream"] is True
    assert r8d_audio_parameter_profile_reason(shape, profile) is None
    if shape == "chat_audio_input":
        assert profile["certified_input_formats"] == ["wav"]
    elif shape == "chat_audio_output":
        assert profile["certified_voice"] == "alloy"
        assert profile["certified_response_format"] == "mp3"
    else:
        assert profile["certified_output_format"] == "mp3"
        assert profile["supports_image_prompt"] is False

    monkeypatch.setenv(feature_flag, "true")
    control = ProviderWorkloadControlService(service)
    saved = control.update_policy(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadPolicyUpdate(
            expected_revision=0,
            bindings=[
                ProviderWorkloadBindingUpdate(
                    execution_shape=shape,  # type: ignore[arg-type]
                    model_id=model_id,
                    connection_id=connection.id,
                    adapter_contract=adapter,  # type: ignore[arg-type]
                )
            ],
        ),
    )
    activated = control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    assert activated.data_plane_integrated is True
    assert activated.effective_status == "managed_required"

    serialized = json.dumps(
        {
            "certification": result.model_dump(mode="json"),
            "stored": service.repository.list_workload_certifications("local"),
            "sessions": service.repository.list_multimodal_certification_sessions(
                "local"
            ),
        },
        sort_keys=True,
    )
    assert "r8d-secret" not in serialized
    assert "Transcribe the single spoken word in the audio." not in serialized
    assert "Generate a short neutral musical tone." not in serialized
    assert base64.b64encode(MP3_BYTES).decode("ascii") not in serialized


@pytest.mark.asyncio
async def test_chat_audio_input_certification_rejects_generic_text_only_reply(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "provider/audio-r8d"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            content=_sse_body(model_id, "chat_audio_input", input_text="OK"),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_audio_input",
            model_id=model_id,
            adapter_contract="openrouter_chat_audio_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="r8d-chat-audio-input-generic-reply",
    )

    assert result.status == "failed"
    assert result.error_code == (
        "provider_multimodal_chat_audio_input_content_mismatch"
    )
    assert result.checks.media_format_verified is False
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_r8d_certification_rejects_header_only_actual_model_evidence(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "provider/audio-r8d"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        events = [
            {"choices": [{"delta": {"content": "Okay"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        content = (
            "".join(f"data: {json.dumps(event)}\n\n" for event in events)
            + "data: [DONE]\n\n"
        ).encode()
        return httpx.Response(
            200,
            content=content,
            headers={
                "content-type": "text/event-stream",
                "x-model-id": model_id,
                "x-generation-id": "gen-r8d-header-only",
            },
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_audio_input",
            model_id=model_id,
            adapter_contract="openrouter_chat_audio_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="r8d-chat-audio-header-model-only",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_multimodal_actual_model_unverified"
    assert result.checks.actual_model_verified is False
    assert result.refresh_available is False
    refreshed = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).refresh_multimodal_certification(result.certification_id)
    assert refreshed.status == "failed"
    assert refreshed.error_code == (
        "provider_multimodal_actual_model_unverified"
    )
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_r8d_certification_rejects_data_after_done(
    tmp_path: Path,
) -> None:
    model_id = "provider/audio-r8d"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        trailing = {
            "model": model_id,
            "choices": [
                {
                    "delta": {"content": "Okay"},
                    "finish_reason": "stop",
                }
            ],
        }
        return httpx.Response(
            200,
            content=(
                "data: [DONE]\n\n"
                f"data: {json.dumps(trailing)}\n\n"
            ).encode(),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape="chat_audio_input",
            model_id=model_id,
            adapter_contract="openrouter_chat_audio_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key="r8d-chat-audio-post-done-data",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_invalid_sse"


@pytest.mark.asyncio
async def test_r8d_dispatched_transport_failure_is_uncertain_and_never_reposted(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "provider/audio-r8d"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        raise httpx.ReadTimeout("dispatched response timeout", request=request)

    transport = httpx.MockTransport(handler)
    service, connection = _service(
        tmp_path,
        transport,
        resolver_addresses=["8.8.8.8", "1.1.1.1"],
    )
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    payload = ProviderWorkloadCertificationRequest(
        execution_shape="audio_generation_stream",
        model_id=model_id,
        adapter_contract="openrouter_audio_generation_stream_v1",
        acknowledge_billed_call=True,
    )
    first = await certifications.run(
        connection.id,
        payload,
        idempotency_key="r8d-dispatched-timeout",
    )
    replay = await certifications.run(
        connection.id,
        payload,
        idempotency_key="r8d-dispatched-timeout",
    )

    assert first.status == replay.status == "uncertain"
    assert first.error_code == "provider_workload_read_timeout"
    assert [item.method for item in requests].count("POST") == 1
    post = next(item for item in requests if item.method == "POST")
    assert post.url.host in {"8.8.8.8", "1.1.1.1"}
    session = service.repository.list_multimodal_certification_sessions("local")[0]
    assert session["status"] == "uncertain"
    assert session["provider_dispatch_state"] == "uncertain"
    assert bool(session["post_dispatched"]) is True
    request_count = len(requests)
    with pytest.raises(RouterServiceError) as refresh_error:
        await certifications.refresh_multimodal_certification(
            first.certification_id
        )
    assert refresh_error.value.code == (
        "provider_multimodal_certification_not_refreshable"
    )
    assert len(requests) == request_count


@pytest.mark.asyncio
async def test_r8d_audio_generation_rejects_partial_media_or_missing_terminal(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    model_id = "provider/audio-r8d"
    encoded = base64.b64encode(b"ID3partial").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        event = {
            "model": model_id,
            "choices": [
                {"delta": {"audio": {"data": encoded}}, "finish_reason": None}
            ],
        }
        return httpx.Response(
            200,
            content=f"data: {json.dumps(event)}\n\n".encode(),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    payload = ProviderWorkloadCertificationRequest(
        execution_shape="audio_generation_stream",
        model_id=model_id,
        adapter_contract="openrouter_audio_generation_stream_v1",
        acknowledge_billed_call=True,
    )
    result = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    ).run(
        connection.id,
        payload,
        idempotency_key="r8d-partial-audio",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_multimodal_audio_stream_invalid"
    assert result.can_run is False
    assert [item.method for item in requests].count("POST") == 1


def test_r8d_parameter_profile_rejects_cross_shape_or_stale_evidence() -> None:
    output_profile = {
        "audio_parameter_contract_version": R8D_AUDIO_PARAMETER_CONTRACT_VERSION,
        "stream": True,
        "certified_voice": "alloy",
        "certified_response_format": "mp3",
    }
    assert r8d_audio_parameter_profile_reason(
        "chat_audio_output", output_profile
    ) is None
    assert r8d_audio_parameter_profile_reason(
        "chat_audio_input", output_profile
    ) == "provider_multimodal_audio_parameter_profile_invalid"
    stale = dict(output_profile)
    stale["audio_parameter_contract_version"] = "old"
    assert r8d_audio_parameter_profile_reason(
        "chat_audio_output", stale
    ) == "provider_multimodal_audio_parameter_contract_stale"
    assert r8d_audio_certification_evidence_reason(
        "chat_audio_output",
        {
            "http_ok": True,
            "response_complete": True,
            "content_observed": True,
            "actual_model_verified": True,
            "media_format_verified": True,
            "terminal_signal_verified": True,
        },
    ) == "provider_multimodal_audio_evidence_incomplete"


@pytest.mark.asyncio
async def test_r8d_managed_audio_job_is_single_post_and_persists_redacted_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=(
                _sse_body(model_id, "audio_generation_stream")
                if post_count == 1
                else _independently_padded_audio_sse(model_id)
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )

    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-job-0001",
    )
    assert launch.task is not None
    assert launch.job.execution_mode == "managed"
    await jobs.run(launch.task)

    completed = jobs.get(launch.job.job_id)
    replay = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-job-0001",
    )
    assert completed.status == "succeeded"
    assert completed.actual_model == model_id
    assert completed.generation_id is None
    assert completed.provider_dispatch_state == "confirmed"
    assert completed.provider_route_receipts[0]["call_count"] == 1
    assert completed.provider_route_receipts[0]["status"] == "passed"
    assert replay.task is None
    assert post_count == 2  # one certification plus one runtime call

    serialized = json.dumps(
        {
            "job": completed.model_dump(mode="json"),
            "row": service.repository.get_audio_job(
                "local", completed.job_id
            ),
        },
        sort_keys=True,
    )
    assert "A neutral synthetic melody." not in serialized
    assert "r8d-secret" not in serialized
    with pytest.raises(MultimodalServiceError) as retained:
        jobs.delete(completed.job_id)
    assert retained.value.code == "managed_audio_job_retained"
    replay_after_delete = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-job-0001",
    )
    assert replay_after_delete.task is None
    assert post_count == 2


@pytest.mark.asyncio
async def test_active_audio_generation_policy_uses_legacy_when_feature_flag_is_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=(
                _sse_body(model_id, "audio_generation_stream")
                if post_count == 1
                else _independently_padded_audio_sse(model_id)
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MODEL_CONTROL_AUDIO_GENERATION_ENABLED", "false")
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    catalog = AudioCatalogService(service)
    target = OpenRouterTarget(
        base_url="https://legacy-audio.example/v1",
        api_key="legacy-audio-secret",
        connection_id=None,
        cache_key="environment:legacy-audio",
    )
    jobs = AudioJobService(
        service,
        catalog,
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )

    async def profile(
        _model_id: str,
        *,
        has_image: bool,
    ) -> SimpleNamespace:
        assert has_image is False
        return SimpleNamespace(price_per_generation_usd=None)

    monkeypatch.setattr(jobs, "_profile", profile)
    monkeypatch.setattr(catalog, "resolve_target", lambda: target)

    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="legacy-when-r8d-disabled",
    )
    assert launch.task is not None
    assert launch.task.managed_dispatch is None
    assert launch.job.execution_mode == "legacy"
    await jobs.run(launch.task)

    completed = jobs.get(launch.job.job_id)
    assert completed.status == "succeeded"
    assert completed.execution_mode == "legacy"
    assert completed.provider_route_receipts == []
    assert service.repository.list_workload_receipts("local")["calls"] == []
    assert post_count == 2  # one certification plus one legacy runtime call


@pytest.mark.asyncio
async def test_r8d_managed_audio_job_rejects_sticky_model_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            content = _sse_body(model_id, "audio_generation_stream")
        else:
            encoded = base64.b64encode(MP3_BYTES).decode("ascii")
            events = [
                {
                    "model": "provider/wrong-audio-model",
                    "choices": [
                        {
                            "delta": {"audio": {"data": encoded}},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "model": model_id,
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                },
            ]
            content = (
                "".join(
                    f"data: {json.dumps(event)}\n\n" for event in events
                )
                + "data: [DONE]\n\n"
            ).encode()
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-model-mismatch",
    )
    assert launch.task is not None
    await jobs.run(launch.task)

    failed = jobs.get(launch.job.job_id)
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "provider_workload_model_mismatch"
    assert failed.provider_route_receipts[0]["status"] == "failed"
    assert post_count == 2


@pytest.mark.asyncio
async def test_r8d_managed_audio_job_persistence_failure_is_confirmed_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=_sse_body(model_id, "audio_generation_stream"),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )
    monkeypatch.setattr(
        jobs,
        "_write_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("disk unavailable")
        ),
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-write-failure",
    )
    assert launch.task is not None
    await jobs.run(launch.task)

    failed = jobs.get(launch.job.job_id)
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "audio_output_persistence_failed"
    assert failed.provider_dispatch_state == "confirmed"
    assert failed.retry_allowed is False
    assert failed.provider_route_receipts[0]["status"] == "failed"
    assert post_count == 2


@pytest.mark.asyncio
async def test_r8d_audio_job_cancel_waits_for_writer_then_removes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=_sse_body(model_id, "audio_generation_stream"),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-cancel-write-race",
    )
    assert launch.task is not None
    writer_started = threading.Event()
    release_writer = threading.Event()
    original_write = jobs._write_output  # noqa: SLF001

    def blocked_write(job_id: str, content: bytes) -> None:
        writer_started.set()
        assert release_writer.wait(timeout=5)
        original_write(job_id, content)

    monkeypatch.setattr(jobs, "_write_output", blocked_write)
    tasks_before = set(asyncio.all_tasks())
    running = asyncio.create_task(jobs.run(launch.task))
    assert await asyncio.to_thread(writer_started.wait, 5)
    spawned_tasks = set(asyncio.all_tasks()) - tasks_before - {running}
    assert spawned_tasks == set()
    running.cancel()
    running.cancel()
    await asyncio.sleep(0)
    assert not running.done()
    release_writer.set()
    with pytest.raises(asyncio.CancelledError):
        await running

    output_path = jobs._output_path(launch.job.job_id)  # noqa: SLF001
    assert not output_path.exists()
    assert not list(jobs.output_dir.glob("*.tmp-*"))
    failed = jobs.get(launch.job.job_id)
    assert failed.status == "failed"
    assert failed.provider_dispatch_state == "uncertain"
    assert failed.retry_allowed is False
    assert failed.error is not None
    assert failed.error.code == "provider_result_uncertain"
    assert post_count == 2

    # A historical terminal-job orphan is also removed defensively.
    output_path.write_bytes(MP3_BYTES)
    jobs.cleanup_expired(include_terminal_orphans=True)
    assert not output_path.exists()
    jobs.recover_interrupted()
    assert not output_path.exists()
    assert post_count == 2


@pytest.mark.asyncio
async def test_r8d_managed_audio_job_recovers_crash_after_receipt_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=_sse_body(model_id, "audio_generation_stream"),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-finalize-crash",
    )
    assert launch.task is not None
    original_update = jobs._update  # noqa: SLF001

    def crash_before_final_job_status(job_id: str, **changes: object):
        if changes.get("status") == "succeeded":
            raise KeyboardInterrupt("simulated process stop")
        return original_update(job_id, **changes)

    monkeypatch.setattr(jobs, "_update", crash_before_final_job_status)
    with pytest.raises(KeyboardInterrupt):
        await jobs.run(launch.task)

    restarted_repository = SQLiteRouterRepository(
        tmp_path, master_key=b"x" * 32
    )
    restarted_service = ModelRouterService(
        restarted_repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    restarted_jobs = AudioJobService(
        restarted_service,
        AudioCatalogService(restarted_service),
        output_dir=tmp_path / "outputs",
    )
    restarted_jobs.recover_interrupted()

    recovered = restarted_jobs.get(launch.job.job_id)
    assert recovered.status == "succeeded"
    assert recovered.provider_dispatch_state == "confirmed"
    assert recovered.provider_route_receipts[0]["status"] == "passed"
    assert recovered.output_bytes == len(MP3_BYTES)
    assert post_count == 2


@pytest.mark.asyncio
async def test_r8d_managed_audio_job_keeps_output_when_final_update_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=_sse_body(model_id, "audio_generation_stream"),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-finalize-store-error",
    )
    assert launch.task is not None
    original_update = jobs._update  # noqa: SLF001

    def fail_final_job_status(job_id: str, **changes: object):
        if changes.get("status") == "succeeded":
            raise RuntimeError("simulated final job store failure")
        return original_update(job_id, **changes)

    monkeypatch.setattr(jobs, "_update", fail_final_job_status)
    await jobs.run(launch.task)

    output_path = jobs._output_path(launch.job.job_id)  # noqa: SLF001
    pending = service.repository.get_audio_job("local", launch.job.job_id)
    assert pending is not None
    assert pending["status"] == "running"
    assert output_path.read_bytes() == MP3_BYTES
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "passed"

    monkeypatch.setattr(jobs, "_update", original_update)
    recovered = jobs.get(launch.job.job_id)
    assert recovered.status == "succeeded"
    assert recovered.output_bytes == len(MP3_BYTES)
    assert output_path.read_bytes() == MP3_BYTES
    content = await jobs.content(launch.job.job_id)
    streamed = b"".join([chunk async for chunk in content.chunks])
    assert streamed == MP3_BYTES
    assert post_count == 2


@pytest.mark.asyncio
async def test_r8d_managed_audio_reservation_cannot_be_deleted_before_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            content=_sse_body(model_id, "audio_generation_stream"),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        output_dir=tmp_path / "outputs",
    )
    original_prepare = jobs.managed_gateway.prepare_chat_dispatch

    async def assert_reservation_before_link(*args, **kwargs):
        row = service.repository.list_audio_jobs("local", limit=1)[0]
        assert str(row["workload_run_id"]).startswith("managed-reservation:")
        assert service.repository.delete_audio_job(
            "local", str(row["id"])
        ) is False
        return await original_prepare(*args, **kwargs)

    monkeypatch.setattr(
        jobs.managed_gateway,
        "prepare_chat_dispatch",
        assert_reservation_before_link,
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-reservation-race",
    )

    assert launch.task is not None
    with pytest.raises(MultimodalServiceError) as retained:
        jobs.delete(launch.job.job_id)
    assert retained.value.code == "managed_audio_job_retained"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_body", "expected_status", "expected_code", "dispatch_state"),
    [
        (
            lambda model_id: (
                f'data:{{"model":"{model_id}",\n'
                f'data:"choices":[{{"delta":{{"audio":{{"data":"{base64.b64encode(MP3_BYTES).decode("ascii")}"}}}},"finish_reason":"stop"}}]}}\n\n'
                "data:[DONE]\n\n"
            ).encode(),
            "succeeded",
            None,
            "confirmed",
        ),
        (
            lambda model_id: (
                f'data: {{"model":"{model_id}","choices":[{{"delta":{{"audio":{{"data":"{base64.b64encode(MP3_BYTES).decode("ascii")}"}}}},"finish_reason":"stop"}}]}}\n\n'
            ).encode(),
            "failed",
            "provider_result_uncertain",
            "uncertain",
        ),
        (
            lambda model_id: (
                f'data: {{"model":"{model_id}","choices":[{{"delta":{{"audio":{{"data":"{base64.b64encode(MP3_BYTES).decode("ascii")}"}}}},"finish_reason":"stop"}}]}}\n\n'
                "data: [DONE]\n\n"
                'data: {"choices":[{"delta":{"content":"late"}}]}\n\n'
            ).encode(),
            "failed",
            "audio_output_incomplete",
            "confirmed",
        ),
    ],
)
async def test_r8d_managed_audio_runtime_sse_terminal_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_body,
    expected_status: str,
    expected_code: str | None,
    dispatch_state: str,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=(
                _sse_body(model_id, "audio_generation_stream")
                if post_count == 1
                else runtime_body(model_id)
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key=f"managed-audio-terminal-{expected_status}-{dispatch_state}",
    )
    assert launch.task is not None
    await jobs.run(launch.task)

    completed = jobs.get(launch.job.job_id)
    assert completed.status == expected_status
    assert completed.provider_dispatch_state == dispatch_state
    assert (completed.error.code if completed.error else None) == expected_code
    if expected_status == "failed":
        assert completed.retry_allowed is False
    assert post_count == 2


@pytest.mark.asyncio
async def test_r8d_managed_audio_job_uncertain_never_reposts_and_api_requires_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        if post_count == 1:
            return httpx.Response(
                200,
                content=_sse_body(model_id, "audio_generation_stream"),
                headers={"content-type": "text/event-stream"},
            )
        raise httpx.ReadTimeout("runtime result uncertain", request=request)

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        adapter=OpenRouterAudioJobAdapter(
            client_factory=lambda: httpx.AsyncClient(
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ),
            egress_policy=service.egress_policy,
        ),
        output_dir=tmp_path / "outputs",
    )
    configure_audio_job_service(jobs)
    app = FastAPI()
    app.include_router(router)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            form = {
                "model_id": model_id,
                "prompt": "A neutral synthetic melody.",
                "idempotency_key": "managed-audio-api-0001",
            }
            missing = await client.post(
                "/api/multimodal/audio/jobs", data=form
            )
            assert missing.status_code == 422
            mismatch = await client.post(
                "/api/multimodal/audio/jobs",
                data=form,
                headers={"Idempotency-Key": "managed-audio-api-other"},
            )
            assert mismatch.status_code == 409
            created = await client.post(
                "/api/multimodal/audio/jobs",
                data=form,
                headers={"Idempotency-Key": form["idempotency_key"]},
            )
            assert created.status_code == 200
            created_job = created.json()
            detail = await client.get(
                "/api/multimodal/audio/jobs/"
                f"{created_job['job_id']}"
            )
            assert detail.status_code == 200
            job = detail.json()
            assert job["execution_mode"] == "managed"
            assert job["status"] == "failed"
            assert job["provider_dispatch_state"] == "uncertain"
            assert job["retry_allowed"] is False
            assert job["provider_route_receipts"][0]["call_count"] == 1

            replay = await client.post(
                "/api/multimodal/audio/jobs",
                data=form,
                headers={"Idempotency-Key": form["idempotency_key"]},
            )
            assert replay.status_code == 200
            assert replay.json()["job_id"] == job["job_id"]
            assert post_count == 2
    finally:
        configure_audio_job_service(None)


@pytest.mark.asyncio
async def test_r8d_audio_job_restart_closes_dispatch_gap_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=_sse_body(model_id, "audio_generation_stream"),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    service, connection = _service(tmp_path, transport)
    await _activate_audio_generation(
        service,
        connection,
        transport,
        monkeypatch,
        model_id=model_id,
    )
    monkeypatch.setenv("MULTIMODAL_AUDIO_GENERATION_ENABLED", "true")
    jobs = AudioJobService(
        service,
        AudioCatalogService(service),
        output_dir=tmp_path / "outputs",
    )
    launch = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-restart-0001",
    )
    assert launch.task is not None
    dispatch = launch.task.managed_dispatch
    assert dispatch is not None
    dispatch.run.gateway.call_service.mark_dispatched(dispatch.prepared)

    # The recovery scan must not lose an older dispatched job behind its
    # bounded first page of newer live work.
    for index in range(101):
        service.repository.create_audio_job_if_absent(
            "local",
            job_id=f"audio_newer_{index:03d}",
            idempotency_key_hash=f"newer-key-{index:03d}",
            connection_id=connection.id,
            requested_model=model_id,
            provider="openrouter",
            has_image=False,
            cost_kind="unavailable",
        )

    restarted_repository = SQLiteRouterRepository(
        tmp_path, master_key=b"x" * 32
    )
    restarted_service = ModelRouterService(
        restarted_repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    restarted_jobs = AudioJobService(
        restarted_service,
        AudioCatalogService(restarted_service),
        output_dir=tmp_path / "outputs",
    )
    restarted_jobs.recover_interrupted()

    recovered = restarted_jobs.get(launch.job.job_id)
    replay = await restarted_jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key="managed-audio-restart-0001",
    )
    assert recovered.status == "failed"
    assert recovered.provider_dispatch_state == "uncertain"
    assert recovered.retry_allowed is False
    assert recovered.error is not None
    assert recovered.error.code == "provider_result_uncertain"
    assert recovered.provider_route_receipts[0]["status"] == "uncertain"
    assert replay.task is None
    assert post_count == 1  # certification only; runtime POST never replayed
