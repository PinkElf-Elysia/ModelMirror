from __future__ import annotations

import asyncio
import base64
import json
import logging
import sqlite3
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
    ProviderWorkloadCertificationSummary,
    ProviderWorkloadPolicyUpdate,
    RouterConnection,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import (
    R8D_AUDIO_PARAMETER_CONTRACT_VERSION,
    R8D_CHAT_AUDIO_INPUT_PARAMETER_CONTRACT_VERSION,
    SYNTHETIC_CHAT_AUDIO_INPUT_PROMPT,
    WORKLOAD_RESPONSE_CHUNK_BYTES,
    ProviderWorkloadCertificationService,
    ProviderWorkloadCallService,
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


class _CloseTrackingStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.close_count = 0
        self.yield_count = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yield_count += 1
            yield chunk

    async def aclose(self) -> None:
        self.close_count += 1


class _ReadErrorStream(httpx.AsyncByteStream):
    def __init__(self, prefix: bytes) -> None:
        self.prefix = prefix
        self.close_count = 0

    async def __aiter__(self):
        yield self.prefix
        raise httpx.ReadError("private-read-error-marker")

    async def aclose(self) -> None:
        self.close_count += 1


class _SlowErrorStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.close_count = 0

    async def __aiter__(self):
        await asyncio.sleep(2)
        yield b'{"error":{"code":400}}'

    async def aclose(self) -> None:
        self.close_count += 1


class _BlockingErrorStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self):
        self.started.set()
        await self.release.wait()
        yield b'{"error":{"code":400}}'

    async def aclose(self) -> None:
        self.close_count += 1


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


def _r8d_certification_request(
    shape: str,
    model_id: str,
) -> ProviderWorkloadCertificationRequest:
    adapter = (
        "openrouter_audio_generation_stream_v1"
        if shape == "audio_generation_stream"
        else "openrouter_chat_audio_v1"
    )
    return ProviderWorkloadCertificationRequest(
        execution_shape=shape,  # type: ignore[arg-type]
        model_id=model_id,
        adapter_contract=adapter,  # type: ignore[arg-type]
        acknowledge_billed_call=True,
    )


async def _run_r8d_certification_case(
    tmp_path: Path,
    transport: httpx.AsyncBaseTransport,
    *,
    shape: str,
    model_id: str,
    idempotency_key: str,
) -> tuple[
    ModelRouterService,
    RouterConnection,
    ProviderWorkloadCertificationService,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadCertificationSummary,
]:
    service, connection = _service(tmp_path, transport)
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    payload = _r8d_certification_request(shape, model_id)
    result = await certifications.run(
        connection.id,
        payload,
        idempotency_key=idempotency_key,
    )
    return service, connection, certifications, payload, result


def _sse_mock_transport(
    model_id: str,
    content: bytes,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, content=content)

    return httpx.MockTransport(handler), requests


def _serialized_certification_state(
    service: ModelRouterService,
    result: ProviderWorkloadCertificationSummary,
) -> str:
    return json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "certifications": service.repository.list_workload_certifications("local"),
            "sessions": service.repository.list_multimodal_certification_sessions("local"),
        }
    )


def _sse_body(
    model_id: str,
    shape: str,
    *,
    input_text: str = "Okay",
    finish_reason: object = "stop",
    done: bool = True,
    trailing_stop: bool = False,
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
                        "finish_reason": finish_reason,
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
            {"choices": [{"delta": {}, "finish_reason": finish_reason}]}
        )
    events.extend(
        [
            {"choices": [], "usage": {"total_tokens": 5}},
            {"usage": {"total_tokens": 5}},
        ]
    )
    if trailing_stop:
        events.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


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
        assert request.url.path.endswith("/chat/completions")
        assert request.method == "POST"
        assert request.headers["host"] == "provider.example"
        assert request.headers["authorization"] == "Bearer r8d-secret"
        assert request.headers["accept"] == "text/event-stream"
        assert request.headers["content-type"].startswith("application/json")
        body = json.loads(request.content)
        assert body["model"] == model_id
        assert body["stream"] is True
        assert body["max_tokens"] == (64 if shape == "chat_audio_input" else 32)
        if shape == "chat_audio_input":
            assert set(body) == {
                "model", "stream", "temperature", "max_tokens", "messages"
            }
            assert len(body["messages"]) == 1
            message = body["messages"][0]
            assert message["role"] == "user"
            assert [part["type"] for part in message["content"]] == [
                "text", "input_audio"
            ]
            assert message["content"][0] == {
                "type": "text",
                "text": SYNTHETIC_CHAT_AUDIO_INPUT_PROMPT,
            }
            assert "okay" not in SYNTHETIC_CHAT_AUDIO_INPUT_PROMPT.casefold()
            audio = message["content"][1]["input_audio"]
            assert set(audio) == {"data", "format"}
            assert audio["format"] == "wav"
            assert not audio["data"].startswith("data:")
            wav_bytes = base64.b64decode(audio["data"], validate=True)
            assert wav_bytes.startswith(b"RIFF")
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
    request = _r8d_certification_request(shape, model_id)
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
    expected_contract_version = (
        R8D_CHAT_AUDIO_INPUT_PARAMETER_CONTRACT_VERSION
        if shape == "chat_audio_input"
        else R8D_AUDIO_PARAMETER_CONTRACT_VERSION
    )
    assert profile["audio_parameter_contract_version"] == expected_contract_version
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

    # Simulate an old passed record without fabricating new terminal evidence.
    checks = json.loads(str(row["checks_json"]))
    checks.pop("safe_terminal_verified")
    for name in tuple(checks):
        if name.startswith("finish_") or name in {
            "sse_done_observed", "transcript_matches_fixture",
        }:
            checks.pop(name)
    with sqlite3.connect(service.repository.database_path) as database:
        database.execute(
            "UPDATE provider_workload_certifications SET checks_json = ? "
            "WHERE tenant_id = ? AND id = ?",
            (json.dumps(checks), "local", result.certification_id),
        )
    restarted_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted_service = ModelRouterService(
        restarted_repository,
        client_factory=lambda: httpx.AsyncClient(transport=transport),
        egress_policy=ProviderEgressPolicy(resolver=lambda _host, _port: ["8.8.8.8"]),
    )
    restarted_certifications = ProviderWorkloadCertificationService(
        restarted_service,
        client_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    restarted_control = ProviderWorkloadControlService(restarted_service)
    old_summary = next(
        item for item in restarted_certifications.list().certifications
        if item.certification_id == result.certification_id
    )
    replayed_old = await restarted_certifications.run(
        connection.id,
        request,
        idempotency_key=f"r8d-{shape}",
    )
    current_policy = restarted_control.get_policy(entry_id)  # type: ignore[arg-type]
    if shape in {"chat_audio_input", "chat_audio_output"}:
        assert old_summary.status == "stale"
        assert replayed_old.status == "stale"
        assert old_summary.blocked_reason == "provider_multimodal_audio_evidence_incomplete"
        assert current_policy.effective_status == "degraded_required"
        assert current_policy.bindings[0].valid is False
        assert current_policy.bindings[0].reason_code == old_summary.blocked_reason
        with pytest.raises(RouterServiceError) as preflight:
            ProviderWorkloadCallService(restarted_service).start_run(
                entry_id  # type: ignore[arg-type]
            )
        assert preflight.value.code == "provider_workload_policy_not_active"
    else:
        assert old_summary.status == "passed"
        assert replayed_old.status == "passed"
        assert current_policy.effective_status == "managed_required"
    assert old_summary.checks.safe_terminal_verified is False
    assert old_summary.checks.sse_done_observed is None
    assert old_summary.checks.finish_stop_observed is None
    preserved = service.repository.get_workload_certification(
        "local", str(result.certification_id)
    )
    assert preserved is not None and preserved["status"] == "passed"
    assert json.loads(str(preserved["checks_json"])) == checks
    assert [item.method for item in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_chat_audio_input_certification_rejects_generic_text_only_reply(
    tmp_path: Path,
) -> None:
    model_id = "provider/audio-r8d"
    transport, requests = _sse_mock_transport(
        model_id,
        _sse_body(
            model_id,
            "chat_audio_input",
            input_text="I can help with audio.",
        ),
    )

    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="r8d-chat-audio-input-generic-reply",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_multimodal_chat_audio_input_content_mismatch"
    assert result.checks.media_format_verified is False
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_r8d_openrouter_chat_audio_400_keeps_http_error_and_only_fixed_subtype(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "provider/audio-r8d"
    requests: list[httpx.Request] = []
    streams: list[_CloseTrackingStream] = []
    private_values = (
        "private-upstream-message",
        "private-provider-code",
        "gen-private-body-id",
    )
    body = json.dumps(
        {
            "id": private_values[2],
            "error": {
                "code": 400,
                "message": private_values[0],
                "metadata": {
                    "error_type": "invalid_request",
                    "provider_code": private_values[1],
                },
            },
        }
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        stream = _CloseTrackingStream(body[:11], body[11:37], body[37:])
        streams.append(stream)
        return httpx.Response(
            400,
            headers={"content-type": "application/json; charset=utf-8"},
            stream=stream,
        )

    caplog.set_level(logging.DEBUG)
    transport = httpx.MockTransport(handler)
    service, connection, certifications, payload, result = (
        await _run_r8d_certification_case(
            tmp_path,
            transport,
            shape="chat_audio_input",
            model_id=model_id,
            idempotency_key="openrouter-error-envelope",
        )
    )
    replay = await certifications.run(
        connection.id,
        payload,
        idempotency_key="openrouter-error-envelope",
    )

    assert result.status == replay.status == "failed"
    assert result.error_code == replay.error_code == "provider_workload_http_error"
    assert result.warning_codes == replay.warning_codes == [
        "openrouter_error_type_invalid_request"
    ]
    assert [request.method for request in requests].count("POST") == 1
    assert len(streams) == 1 and streams[0].close_count == 1
    session = service.repository.get_multimodal_certification_session(
        "local", certification_id=str(result.certification_id)
    )
    assert session is not None
    assert session["provider_dispatch_state"] == "confirmed"
    assert session["upstream_operation_id"] is None
    serialized = _serialized_certification_state(service, result)
    for private_value in (
        *private_values,
        "r8d-secret",
        "Transcribe the single spoken word",
        "UklGR",
    ):
        assert private_value not in serialized
        assert private_value not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "headers"),
    [
        pytest.param(
            b'{"error":{"code":400,"message":"x","metadata":{"error_type":"private-secret"}}}',
            {"content-type": "application/json"},
            id="unknown-type",
        ),
        pytest.param(
            b'{"error":{"code":true,"message":"x","metadata":{"error_type":"invalid_request"}}}',
            {"content-type": "application/json"},
            id="bool-code",
        ),
        pytest.param(
            b'{"error":{"code":401,"message":"x","metadata":{"error_type":"invalid_request"}}}',
            {"content-type": "application/json"},
            id="status-mismatch",
        ),
        pytest.param(
            b'{"error":{"code":400,"message":"x","metadata":{"error_type":"invalid_request","error_type":"authentication"}}}',
            {"content-type": "application/json"},
            id="duplicate-key",
        ),
        pytest.param(
            b'["invalid_request"]',
            {"content-type": "application/json"},
            id="wrong-top-level",
        ),
        pytest.param(
            b'{"error":\xff}',
            {"content-type": "application/json"},
            id="invalid-utf8",
        ),
        pytest.param(
            b'{"error":',
            {"content-type": "application/json"},
            id="invalid-json",
        ),
        pytest.param(
            b'{"error":{"code":400,"message":"x","metadata":{"error_type":"invalid_request"}}}',
            {"content-type": "text/plain"},
            id="non-json-content-type",
        ),
        pytest.param(
            b'{"error":{"code":400,"message":"x","metadata":{"error_type":"invalid_request"}}}',
            {"content-type": "application/json", "content-encoding": "gzip"},
            id="compressed-content",
        ),
        pytest.param(
            b'{"error":{"code":400,"message":"x","metadata":{"error_type":"invalid_request"}}}',
            {"content-type": "application/json", "content-length": "invalid"},
            id="invalid-content-length",
        ),
        pytest.param(
            b'{"error":{"code":400,"message":"x","metadata":{"error_type":"invalid_request"}}}',
            {"content-type": "application/json", "content-length": "16385"},
            id="declared-too-large",
        ),
        pytest.param(
            b'{"error":{"code":400,"message":"x","metadata":{"error_type":"invalid_request"}}}',
            {"content-type": "application/json", "content-length": "9" * 5000},
            id="integer-conversion-limit",
        ),
        pytest.param(
            b"{" + b"x" * (16 * 1024) + b"}",
            {"content-type": "application/json", "content-length": "1"},
            id="actual-too-large-with-false-length",
        ),
    ],
)
async def test_r8d_openrouter_error_envelope_rejects_untrusted_shapes(
    tmp_path: Path,
    body: bytes,
    headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    streams: list[_CloseTrackingStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        stream = _CloseTrackingStream(body)
        streams.append(stream)
        return httpx.Response(400, headers=headers, stream=stream)

    caplog.set_level(logging.DEBUG)
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="untrusted-error-envelope",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_http_error"
    assert result.warning_codes == []
    assert post_count == 1
    assert len(streams) == 1 and streams[0].close_count == 1
    serialized = _serialized_certification_state(service, result)
    assert "private-secret" not in serialized
    assert "private-secret" not in caplog.text


@pytest.mark.asyncio
async def test_r8d_openrouter_error_body_read_failure_remains_determinate_http_400(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    streams: list[_ReadErrorStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        stream = _ReadErrorStream(
            b'{"error":{"code":400,"message":"private-upstream-message"'
        )
        streams.append(stream)
        return httpx.Response(
            400,
            headers={"content-type": "application/json"},
            stream=stream,
        )

    caplog.set_level(logging.DEBUG)
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="error-body-read-failure",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_http_error"
    assert result.warning_codes == []
    assert result.provider_dispatch_state == "confirmed"
    assert result.retry_allowed is False
    assert post_count == 1
    assert len(streams) == 1 and streams[0].close_count == 1
    serialized = _serialized_certification_state(service, result)
    for private_value in (
        "private-upstream-message",
        "private-read-error-marker",
    ):
        assert private_value not in serialized
        assert private_value not in caplog.text


@pytest.mark.asyncio
async def test_r8d_openrouter_error_body_timeout_remains_determinate_http_400(
    tmp_path: Path,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    streams: list[_SlowErrorStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        stream = _SlowErrorStream()
        streams.append(stream)
        return httpx.Response(
            400,
            headers={"content-type": "application/json"},
            stream=stream,
        )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="error-body-timeout",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_http_error"
    assert result.warning_codes == []
    assert result.provider_dispatch_state == "confirmed"
    assert result.retry_allowed is False
    assert post_count == 1
    assert len(streams) == 1 and streams[0].close_count == 1


@pytest.mark.asyncio
async def test_r8d_openrouter_error_body_cancellation_preserves_known_http_400(
    tmp_path: Path,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    stream = _BlockingErrorStream()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            400,
            headers={"content-type": "application/json"},
            stream=stream,
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
    task = asyncio.create_task(
        certifications.run(
            connection.id,
            _r8d_certification_request("chat_audio_input", model_id),
            idempotency_key="error-body-cancelled",
        )
    )
    await asyncio.wait_for(stream.started.wait(), timeout=1)
    assert task.cancel()
    result = await task

    assert result.status == "failed"
    assert result.error_code == "provider_workload_http_error"
    assert result.warning_codes == []
    assert result.provider_dispatch_state == "confirmed"
    assert result.retry_allowed is False
    assert post_count == 1
    assert stream.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("total_bytes", "expected_warnings"),
    [
        (16 * 1024, ["openrouter_error_type_invalid_request"]),
        (16 * 1024 + 1, []),
    ],
)
async def test_r8d_openrouter_error_envelope_enforces_exact_decoded_size_boundary(
    tmp_path: Path,
    total_bytes: int,
    expected_warnings: list[str],
) -> None:
    model_id = "provider/audio-r8d"
    prefix = b'{"padding":"'
    suffix = (
        b'","error":{"code":400,"message":"x","metadata":'
        b'{"error_type":"invalid_request"}}}'
    )
    body = prefix + b"x" * (total_bytes - len(prefix) - len(suffix)) + suffix
    assert len(body) == total_bytes
    post_count = 0
    streams: list[_CloseTrackingStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        stream = _CloseTrackingStream(body)
        streams.append(stream)
        return httpx.Response(
            400,
            headers={
                "content-type": "application/json",
                "content-length": str(len(body)),
            },
            stream=stream,
        )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="error-body-size-boundary",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_http_error"
    assert result.warning_codes == expected_warnings
    assert post_count == 1
    assert len(streams) == 1 and streams[0].close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "status_code", "expected_error"),
    [
        ("chat_audio_output", 400, "provider_workload_http_error"),
        ("chat_audio_input", 401, "provider_workload_http_401"),
        ("chat_audio_input", 429, "provider_workload_http_429"),
        ("chat_audio_input", 503, "provider_workload_http_5xx"),
    ],
)
async def test_r8d_openrouter_error_subtype_does_not_cross_shape_or_status(
    tmp_path: Path,
    shape: str,
    status_code: int,
    expected_error: str,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    body = json.dumps(
        {
            "error": {
                "code": status_code,
                "message": "private-upstream-message",
                "metadata": {"error_type": "invalid_request"},
            }
        }
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            status_code,
            headers={"content-type": "application/json"},
            content=body,
        )

    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape=shape,
        model_id=model_id,
        idempotency_key="cross-shape-status",
    )

    assert result.status == "failed"
    assert result.error_code == expected_error
    assert result.warning_codes == []
    assert post_count == 1
    assert "private-upstream-message" not in _serialized_certification_state(
        service, result
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_text", "expected_status"),
    [
        ("Okay", "passed"), ("OK", "passed"), ("O.K.", "passed"),
        (" okay! ", "passed"), ("Okay okay", "failed"),
        ("The word is okay", "failed"), ("unrelated-private-marker", "failed"),
    ],
)
async def test_r8d_audio_transcript_accepts_only_equivalent_spellings(
    tmp_path: Path,
    input_text: str,
    expected_status: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        assert json.loads(request.content)["max_tokens"] == 64
        return httpx.Response(
            200, content=_sse_body(model_id, "chat_audio_input", input_text=input_text),
        )

    transport = httpx.MockTransport(handler)
    service, connection, certifications, payload, result = (
        await _run_r8d_certification_case(
            tmp_path,
            transport,
            shape="chat_audio_input",
            model_id=model_id,
            idempotency_key="spelling",
        )
    )
    replay = await certifications.run(connection.id, payload, idempotency_key="spelling")
    assert result.status == replay.status == expected_status
    assert post_count == 1
    assert result.checks.actual_model_verified is True
    assert result.checks.safe_terminal_verified is True
    assert result.checks.transcript_matches_fixture is (expected_status == "passed")
    serialized = json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "certifications": service.repository.list_workload_certifications("local"),
            "sessions": service.repository.list_multimodal_certification_sessions("local"),
        }
    )
    assert input_text not in serialized
    if "private" in input_text:
        assert input_text not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "finish_reason", "error_code", "diagnostic", "trailing_stop"),
    [
        ("chat_audio_input", "error", "provider_workload_stream_error",
         "finish_error_observed", False),
        ("chat_audio_input", "content_filter", "provider_workload_content_filtered",
         "finish_filter_observed", True),
        ("chat_audio_input", "length", "provider_workload_output_truncated",
         "finish_length_observed", True),
        ("chat_audio_input", "tool_calls", "provider_workload_invalid_finish_reason",
         "finish_other_observed", True),
        ("chat_audio_input", {"private-upstream-marker": True},
         "provider_workload_invalid_finish_reason", "finish_other_observed", True),
        ("chat_audio_input", False, "provider_workload_invalid_finish_reason",
         "finish_other_observed", True),
        ("chat_audio_output", "length", "provider_workload_output_truncated",
         "finish_length_observed", True),
        ("audio_generation_stream", "length", "provider_workload_output_truncated",
         "finish_length_observed", True),
    ],
)
async def test_r8d_certification_rejects_unsafe_finish_and_persists_safe_diagnostics(
    tmp_path: Path,
    shape: str,
    finish_reason: object,
    error_code: str,
    diagnostic: str,
    trailing_stop: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "provider/audio-r8d"
    caplog.set_level(logging.DEBUG)
    transport, requests = _sse_mock_transport(
        model_id,
        _sse_body(
            model_id,
            shape,
            finish_reason=finish_reason,
            trailing_stop=trailing_stop,
        ),
    )
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape=shape,
        model_id=model_id,
        idempotency_key="unsafe-finish",
    )

    assert result.status == "failed"
    assert result.error_code == error_code
    assert result.checks.terminal_signal_verified is False
    assert result.checks.safe_terminal_verified is False
    assert result.checks.sse_done_observed is False
    assert result.checks.finish_stop_observed is False
    assert getattr(result.checks, diagnostic) is True
    assert result.checks.actual_model_verified is True
    assert [request.method for request in requests].count("POST") == 1
    row = service.repository.get_workload_certification("local", str(result.certification_id))
    assert row is not None
    checks = json.loads(str(row["checks_json"]))
    assert checks[diagnostic] is True
    assert checks["sse_done_observed"] is False
    serialized = _serialized_certification_state(service, result)
    assert "private-upstream-marker" not in serialized
    assert "private-upstream-marker" not in caplog.text
    assert "r8d-secret" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "finish_reason", "done", "expected_status"),
    [
        ("chat_audio_input", "stop", False, "passed"),
        ("chat_audio_input", None, True, "passed"),
        ("chat_audio_input", None, False, "failed"),
        ("chat_audio_output", "stop", False, "passed"),
        ("chat_audio_output", None, False, "failed"),
        ("audio_generation_stream", "stop", False, "failed"),
        ("audio_generation_stream", None, True, "failed"),
    ],
)
async def test_r8d_certification_preserves_shape_specific_terminal_contract(
    tmp_path: Path,
    shape: str,
    finish_reason: str | None,
    done: bool,
    expected_status: str,
) -> None:
    model_id = "provider/audio-r8d"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        body = _sse_body(model_id, shape, finish_reason=finish_reason, done=done)
        return httpx.Response(
            200,
            stream=_CloseTrackingStream(body[:17], body[17:53], body[53:]),
        )

    transport = httpx.MockTransport(handler)
    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape=shape,
        model_id=model_id,
        idempotency_key="terminal-contract",
    )
    terminal_verified = expected_status == "passed"
    assert result.status == expected_status
    if not terminal_verified:
        assert result.error_code == "provider_workload_missing_terminal"
    assert result.checks.safe_terminal_verified is terminal_verified
    assert result.checks.sse_done_observed is done
    assert result.checks.finish_stop_observed is (finish_reason == "stop")


@pytest.mark.asyncio
async def test_r8d_certification_closes_stream_after_early_validation_failure(
    tmp_path: Path,
) -> None:
    model_id = "provider/audio-r8d"
    streams: list[_CloseTrackingStream] = []
    private_tail = b'data: {"private-unconsumed-tail":true}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        first_event = (
            "data: "
            + json.dumps(
                {
                    "model": "provider/unexpected-model",
                    "choices": [{"delta": {"content": "private-transcript"}}],
                }
            )
            + "\n\n"
        ).encode()
        first_chunk = first_event + b":" + b" " * (
            WORKLOAD_RESPONSE_CHUNK_BYTES - len(first_event) - 1
        )
        stream = _CloseTrackingStream(first_chunk, private_tail)
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    transport = httpx.MockTransport(handler)
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="early-stream-failure",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_model_mismatch"
    assert len(streams) == 1
    assert streams[0].yield_count == 1
    assert streams[0].close_count == 1
    serialized = json.dumps(
        {
            "result": result.model_dump(mode="json"),
            "certifications": service.repository.list_workload_certifications("local"),
            "sessions": service.repository.list_multimodal_certification_sessions("local"),
        }
    )
    assert "private-transcript" not in serialized
    assert "private-unconsumed-tail" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_error",
    [
        {"message": "private-provider-error"},
        "private-provider-error",
        False,
    ],
)
async def test_r8d_certification_rejects_any_non_null_top_level_error(
    tmp_path: Path,
    provider_error: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "provider/audio-r8d"
    caplog.set_level(logging.DEBUG)
    events = [
        {"model": model_id, "choices": [{"index": 0, "delta": {"content": "Okay"}}]},
        {"error": provider_error},
        {"model": model_id, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    body = "".join(f"data: {json.dumps(item)}\n\n" for item in events)
    transport, requests = _sse_mock_transport(
        model_id, (body + "data: [DONE]\n\n").encode()
    )
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="top-level-error",
    )
    assert result.status == "failed"
    assert result.error_code == "provider_workload_stream_error"
    assert [request.method for request in requests].count("POST") == 1
    serialized = _serialized_certification_state(service, result)
    assert "private-provider-error" not in serialized
    assert "private-provider-error" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [
            {
                "choices": [
                    {"index": 0, "delta": {"content": "O"}},
                    {"index": 1, "delta": {"content": "K"}, "finish_reason": "stop"},
                ]
            }
        ],
        [
            {"choices": [{"index": 0, "delta": {"content": "O"}}]},
            {
                "choices": [
                    {"index": 1, "delta": {"content": "K"}, "finish_reason": "stop"}
                ]
            },
        ],
        [
            {
                "choices": [
                    {"index": 1, "delta": {"content": "Okay"}, "finish_reason": "stop"}
                ]
            }
        ],
        [
            {
                "id": "generation-r8d",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "Okay"},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "id": "generation-r8d",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "private-tail"},
                        "finish_reason": "stop",
                        "native_finish_reason": "stop",
                        "logprobs": None,
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
        ],
        [{"choices": {"index": 0, "delta": {"content": "Okay"}}}],
        [{"choices": None}],
        [{"choices": [None]}],
        [{"choices": [{"index": 0, "delta": None}]}],
        [{"choices": [{"index": 0, "delta": "private-delta"}]}],
        [{"choices": [{"index": 0, "delta": ["private-delta"]}]}],
    ],
)
async def test_r8d_certification_rejects_non_primary_or_ambiguous_choices(
    tmp_path: Path,
    events: list[dict[str, object]],
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        body = "".join(
            f"data: {json.dumps({'model': model_id, **item})}\n\n"
            for item in events
        )
        return httpx.Response(200, content=(body + "data: [DONE]\n\n").encode())

    transport = httpx.MockTransport(handler)
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="ambiguous-choices",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_invalid_sse"
    assert post_count == 1
    assert "private-tail" not in _serialized_certification_state(service, result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "done", "expected_status", "expected_usage", "expected_error"),
    [
        pytest.param([], True, "passed", None, None, id="no-usage"),
        pytest.param(
            [
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 90,
                        "completion_tokens": 9,
                        "total_tokens": 99,
                    },
                },
                {
                    "usage": {
                        "prompt_tokens": 80,
                        "completion_tokens": 8,
                        "total_tokens": 88,
                    }
                },
            ],
            True,
            "passed",
            None,
            None,
            id="post-stop-metadata-is-ignored",
        ),
        pytest.param(
            [
                {
                    "id": "generation-r8d",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "", "role": "assistant"},
                            "finish_reason": "stop",
                            "native_finish_reason": "stop",
                            "logprobs": None,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }
            ],
            True,
            "passed",
            (2, 3, 5),
            None,
            id="openrouter-terminal-usage-replay",
        ),
        pytest.param(
            [
                {
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ]
                }
            ],
            True,
            "failed",
            None,
            "provider_workload_invalid_sse",
            id="replay-without-usage",
        ),
        pytest.param(
            [
                {
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": True,
                        "completion_tokens": 3,
                        "total_tokens": 4,
                    },
                }
            ],
            True,
            "failed",
            None,
            "provider_workload_invalid_sse",
            id="terminal-usage-replay-rejects-non-integer-counter",
        ),
        pytest.param(
            [
                {
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 6,
                    },
                }
            ],
            True,
            "failed",
            None,
            "provider_workload_invalid_sse",
            id="terminal-usage-replay-rejects-inconsistent-total",
        ),
        pytest.param(
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "audio": {
                                    "data": base64.b64encode(MP3_BYTES).decode(
                                        "ascii"
                                    )
                                }
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }
            ],
            True,
            "failed",
            None,
            "provider_workload_invalid_sse",
            id="replay-with-audio",
        ),
        pytest.param(
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "id": "call-r8d-terminal",
                                        "type": "function",
                                        "function": {
                                            "name": "noop",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }
            ],
            True,
            "failed",
            None,
            "provider_workload_invalid_sse",
            id="replay-with-tool-call",
        ),
        pytest.param(
            [
                {
                    "id": "generation-r8d",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "", "role": "assistant"},
                            "finish_reason": "stop",
                            "native_finish_reason": "stop",
                            "logprobs": None,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                },
                {
                    "id": "generation-r8d",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "", "role": "assistant"},
                            "finish_reason": "stop",
                            "native_finish_reason": "stop",
                            "logprobs": None,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                },
            ],
            True,
            "failed",
            None,
            "provider_workload_invalid_sse",
            id="second-terminal-usage-replay",
        ),
        pytest.param(
            [
                {
                    "id": "generation-r8d",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "", "role": "assistant"},
                            "finish_reason": "stop",
                            "native_finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }
            ],
            False,
            "failed",
            None,
            "provider_workload_missing_terminal",
            id="terminal-usage-replay-requires-done",
        ),
        pytest.param(
            [
                {
                    "id": "different-generation",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    },
                }
            ],
            True,
            "failed",
            None,
            "provider_workload_invalid_sse",
            id="terminal-usage-replay-generation-mismatch",
        ),
    ],
)
async def test_r8d_certification_allows_only_one_content_free_terminal_usage_replay(
    tmp_path: Path,
    suffix: list[dict[str, object]],
    done: bool,
    expected_status: str,
    expected_usage: tuple[int, int, int] | None,
    expected_error: str | None,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        events = [
            {
                "id": "generation-r8d",
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "Okay"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "generation-r8d",
                "model": model_id,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
            },
            *({"model": model_id, **item} for item in suffix),
        ]
        body = "".join(f"data: {json.dumps(item)}\n\n" for item in events)
        done_event = "data: [DONE]\n\n" if done else ""
        return httpx.Response(200, content=(body + done_event).encode())

    transport = httpx.MockTransport(handler)
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="terminal-usage-replay",
    )

    assert result.status == expected_status
    assert post_count == 1
    if expected_status == "passed":
        assert result.error_code is None
        assert result.checks.safe_terminal_verified is True
        usage = (
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
        )
        expected_usage_values = expected_usage or (None, None, None)
        assert usage == expected_usage_values
    else:
        assert result.error_code == expected_error
        assert result.checks.safe_terminal_verified is False
        assert (
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
        ) == (None, None, None)
    serialized = _serialized_certification_state(service, result)
    assert base64.b64encode(MP3_BYTES).decode("ascii") not in serialized
    assert "call-r8d-terminal" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "expected_status"),
    [
        ("chat_audio_output", "passed"),
        ("audio_generation_stream", "failed"),
    ],
)
async def test_r8d_terminal_usage_replay_is_scoped_to_chat_audio(
    tmp_path: Path,
    shape: str,
    expected_status: str,
) -> None:
    model_id = "provider/audio-r8d"
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")
    events = [
        {
            "id": "generation-r8d",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"audio": {"data": encoded}},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "generation-r8d",
            "model": model_id,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        },
        {
            "id": "generation-r8d",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "", "role": "assistant"},
                    "finish_reason": "stop",
                    "native_finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        },
    ]
    body = "".join(f"data: {json.dumps(item)}\n\n" for item in events)
    transport, requests = _sse_mock_transport(
        model_id,
        (body + "data: [DONE]\n\n").encode(),
    )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape=shape,
        model_id=model_id,
        idempotency_key="terminal-usage-replay-shape-scope",
    )

    assert result.status == expected_status
    assert [request.method for request in requests].count("POST") == 1
    if expected_status == "passed":
        assert result.error_code is None
        assert result.checks.safe_terminal_verified is True
        assert (
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
        ) == (2, 3, 5)
    else:
        assert result.error_code == "provider_workload_invalid_sse"
        assert result.checks.safe_terminal_verified is False
        assert (
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
        ) == (None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("finish_reason", "done", "error_code"), [
    ("length", True, "provider_workload_output_truncated"),
    (None, False, "provider_workload_missing_terminal"),
    ("stop", True, "provider_multimodal_chat_audio_input_content_mismatch"),
])
async def test_r8d_content_mismatch_does_not_mask_termination_failure(
    tmp_path: Path, finish_reason: str | None, done: bool, error_code: str,
) -> None:
    model_id = "provider/audio-r8d"
    transport, requests = _sse_mock_transport(
        model_id,
        _sse_body(
            model_id,
            "chat_audio_input",
            input_text="private-content-marker",
            finish_reason=finish_reason,
            done=done,
        ),
    )
    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="termination-priority",
    )
    assert result.status == "failed"
    assert result.error_code == error_code
    assert result.checks.sse_done_observed is (finish_reason == "stop" and done)
    assert result.checks.transcript_matches_fixture is (
        None if finish_reason == "length" else False
    )
    assert result.checks.media_format_verified is False
    assert [request.method for request in requests].count("POST") == 1
    serialized = _serialized_certification_state(service, result)
    assert "private-content-marker" not in serialized


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
    input_profile = {
        "audio_parameter_contract_version": (
            R8D_CHAT_AUDIO_INPUT_PARAMETER_CONTRACT_VERSION
        ),
        "stream": True,
        "certified_input_formats": ["wav"],
    }
    assert r8d_audio_parameter_profile_reason(
        "chat_audio_input", input_profile
    ) is None
    stale_input = dict(input_profile)
    stale_input["audio_parameter_contract_version"] = (
        R8D_AUDIO_PARAMETER_CONTRACT_VERSION
    )
    assert r8d_audio_parameter_profile_reason(
        "chat_audio_input", stale_input
    ) == "provider_multimodal_audio_parameter_contract_stale"

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
    ) == "provider_multimodal_audio_parameter_contract_stale"
    misbound_output_profile = dict(output_profile)
    misbound_output_profile["audio_parameter_contract_version"] = (
        R8D_CHAT_AUDIO_INPUT_PARAMETER_CONTRACT_VERSION
    )
    assert r8d_audio_parameter_profile_reason(
        "chat_audio_input", misbound_output_profile
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
            "multimodal_adapter_verified": True,
        },
    ) == "provider_multimodal_audio_evidence_incomplete"
    complete_checks = {
        "http_ok": True,
        "response_complete": True,
        "content_observed": True,
        "actual_model_verified": True,
        "media_format_verified": True,
        "terminal_signal_verified": True,
        "safe_terminal_verified": True,
        "multimodal_adapter_verified": True,
    }
    assert r8d_audio_certification_evidence_reason(
        "chat_audio_output", complete_checks
    ) is None
    complete_checks.pop("safe_terminal_verified")
    assert r8d_audio_certification_evidence_reason(
        "audio_generation_stream", complete_checks
    ) is None


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
