from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import sqlite3
import struct
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.model_router.egress import ProviderEgressPolicy
from server.model_router import chat_audio_input_fixture as chat_input_fixture
from server.model_router import workload_control as workload_control_module
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationChecks,
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
    R8D_CHAT_AUDIO_OUTPUT_PARAMETER_CONTRACT_VERSION,
    SYNTHETIC_AUDIO_GENERATION_IMAGE_BASE64,
    SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL,
    SYNTHETIC_AUDIO_GENERATION_IMAGE_FIXTURE_ID,
    SYNTHETIC_AUDIO_GENERATION_IMAGE_HEIGHT,
    SYNTHETIC_AUDIO_GENERATION_IMAGE_MEDIA_TYPE,
    SYNTHETIC_AUDIO_GENERATION_IMAGE_SHA256,
    SYNTHETIC_AUDIO_GENERATION_IMAGE_WIDTH,
    SYNTHETIC_AUDIO_GENERATION_PROMPT,
    SYNTHETIC_AUDIO_GENERATION_PROMPT_SHA256,
    SYNTHETIC_CHAT_AUDIO_INPUT_PROMPT,
    WORKLOAD_RESPONSE_CHUNK_BYTES,
    ProviderWorkloadCertificationService,
    ProviderWorkloadCallService,
    ProviderWorkloadControlService,
    r8d_audio_certification_evidence_reason,
    r8d_audio_parameter_profile_reason,
)
from server.model_router.multimodal_control import (
    CHAT_AUDIO_PCM16_BITS_PER_SAMPLE,
    CHAT_AUDIO_PCM16_BYTE_ORDER,
    CHAT_AUDIO_PCM16_CHANNELS,
    CHAT_AUDIO_PCM16_PARAMETER_EVIDENCE,
    CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ,
    OPENROUTER_AUDIO_GENERATION_REQUEST_CONTRACT,
    R8DAudioSseContractError,
    R8DAudioSseEventBuffer,
    SYNTHETIC_AUDIO_WAV_BYTES,
    build_openrouter_audio_generation_payload,
    chat_audio_pcm16_to_wav,
    is_valid_chat_audio_pcm16,
    is_complete_wav,
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
WAV_BYTES = SYNTHETIC_AUDIO_WAV_BYTES
PCM16_BYTES = WAV_BYTES[44:]


def test_r8d_sse_diagnostic_schema_is_fixed_and_bounded() -> None:
    accepted = ProviderWorkloadCertificationChecks(
        sse_accepted_event_count=65_535,
        sse_accepted_audio_fragment_count=65_535,
        sse_text_content_observed=True,
        sse_text_content_char_count=65_535,
        sse_rejection_reason="unclassified",
    )
    assert accepted.sse_accepted_event_count == 65_535
    assert accepted.sse_accepted_audio_fragment_count == 65_535
    assert accepted.sse_text_content_observed is True
    assert accepted.sse_text_content_char_count == 65_535

    with pytest.raises(ValueError):
        ProviderWorkloadCertificationChecks(sse_accepted_event_count=65_536)
    with pytest.raises(ValueError):
        ProviderWorkloadCertificationChecks(sse_text_content_char_count=65_536)
    with pytest.raises(ValueError):
        ProviderWorkloadCertificationChecks(
            sse_rejection_reason="private_provider_extension",
        )


@pytest.fixture(autouse=True)
def _approved_chat_audio_input_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_input_fixture,
        "CHAT_AUDIO_INPUT_FIXTURE_HUMAN_AUDIT_STATUS",
        "approved",
    )


def _append_wav_chunk(content: bytes, chunk_id: bytes, payload: bytes) -> bytes:
    chunk = chunk_id + len(payload).to_bytes(4, "little") + payload
    if len(payload) % 2:
        chunk += b"\x00"
    result = content + chunk
    return result[:4] + (len(result) - 8).to_bytes(4, "little") + result[8:]


def test_chat_audio_input_v5_fixture_is_frozen_bounded_and_distinct_from_stt() -> None:
    wav = chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_WAV_BYTES
    assert hashlib.sha256(wav).hexdigest() == (
        chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_WAV_SHA256
    )
    assert wav != SYNTHETIC_AUDIO_WAV_BYTES
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    channels, sample_rate, byte_rate, block_align, bits = struct.unpack_from(
        "<HIIHH", wav, 22
    )
    assert (channels, sample_rate, bits) == (1, 8_000, 16)
    assert byte_rate == sample_rate * block_align
    data_offset = wav.index(b"data", 36)
    data_size = int.from_bytes(wav[data_offset + 4 : data_offset + 8], "little")
    frames = data_size // block_align
    assert 2_000 <= frames * 1_000 // sample_rate <= 4_000
    samples = struct.unpack_from(f"<{data_size // 2}h", wav, data_offset + 8)
    assert max(abs(sample) for sample in samples) > 1_000
    assert chat_input_fixture.parse_chat_audio_input_fixture_response(
        '{"count":3,"color":"blue"}'
    ) is True


@pytest.mark.parametrize(
    "separator",
    [b"\n\n", b"\r\n\r\n", b"\r\r", b"\n\r\n", b"\r\n\n"],
)
def test_r8d_sse_event_buffer_is_chunk_independent(separator: bytes) -> None:
    raw = b'data: {"value":\ndata: 1}' + separator + b"data: [DONE]" + separator
    expected = ['data: {"value":\ndata: 1}', "data: [DONE]"]

    whole = R8DAudioSseEventBuffer(max_event_bytes=1024)
    whole_events = [*whole.feed(raw), *whole.finish()]
    split = R8DAudioSseEventBuffer(max_event_bytes=1024)
    split_events: list[str] = []
    for value in raw:
        split_events.extend(split.feed(bytes([value])))
    split_events.extend(split.finish())

    assert whole_events == split_events == expected


def test_audio_generation_certification_and_runtime_share_request_contract() -> None:
    model_id = "google/lyria-3-clip-preview"
    prompt = SYNTHETIC_AUDIO_GENERATION_PROMPT
    certification = ProviderWorkloadCertificationService._r8d_request_payload(
        _r8d_certification_request("audio_generation_stream", model_id)
    )
    runtime = OpenRouterAudioJobAdapter._payload(
        model_id=model_id,
        prompt=prompt,
        image_data_url=SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL,
    )
    assert certification == runtime == build_openrouter_audio_generation_payload(
        model_id=model_id,
        prompt=prompt,
        image_data_url=SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL,
    )
    image_bytes = base64.b64decode(
        SYNTHETIC_AUDIO_GENERATION_IMAGE_BASE64,
        validate=True,
    )
    assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert image_bytes[12:16] == b"IHDR"
    assert int.from_bytes(image_bytes[16:20], "big") == (
        SYNTHETIC_AUDIO_GENERATION_IMAGE_WIDTH
    )
    assert int.from_bytes(image_bytes[20:24], "big") == (
        SYNTHETIC_AUDIO_GENERATION_IMAGE_HEIGHT
    )
    assert hashlib.sha256(image_bytes).hexdigest() == (
        SYNTHETIC_AUDIO_GENERATION_IMAGE_SHA256
    )
    assert len(image_bytes) < 1_024
    assert SYNTHETIC_AUDIO_GENERATION_IMAGE_FIXTURE_ID == (
        "r8d-audio-generation-image-v2"
    )
    assert SYNTHETIC_AUDIO_GENERATION_IMAGE_SHA256 == (
        "422dec23a06d4dea51be6ce13039db1261372e2c56e6403fbf80631f571e4856"
    )
    assert (
        SYNTHETIC_AUDIO_GENERATION_IMAGE_WIDTH,
        SYNTHETIC_AUDIO_GENERATION_IMAGE_HEIGHT,
    ) == (96, 64)
    assert len(image_bytes) == 420
    assert SYNTHETIC_AUDIO_GENERATION_PROMPT_SHA256 == (
        "a2ad68b865b1f59afaa27788517d7ae56abcc76fe87d8e10096684ed92f60be5"
    )
    chunk_types: list[bytes] = []
    cursor = 8
    while cursor < len(image_bytes):
        chunk_length = int.from_bytes(image_bytes[cursor : cursor + 4], "big")
        chunk_type = image_bytes[cursor + 4 : cursor + 8]
        chunk_data = image_bytes[cursor + 8 : cursor + 8 + chunk_length]
        stored_crc = int.from_bytes(
            image_bytes[cursor + 8 + chunk_length : cursor + 12 + chunk_length],
            "big",
        )
        assert zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == stored_crc
        chunk_types.append(chunk_type)
        cursor += 12 + chunk_length
    assert cursor == len(image_bytes)
    assert chunk_types[0] == b"IHDR"
    assert chunk_types[-1] == b"IEND"

    no_image_runtime = OpenRouterAudioJobAdapter._payload(
        model_id=model_id,
        prompt=prompt,
        image_data_url=None,
    )
    assert no_image_runtime["messages"][0]["content"] == prompt


def test_audio_generation_single_event_budget_covers_bounded_stream() -> None:
    assert (
        workload_control_module.AUDIO_GENERATION_MAX_SSE_EVENT_BYTES
        == workload_control_module.AUDIO_GENERATION_MAX_SSE_STREAM_BYTES
    )
    assert (
        workload_control_module.AUDIO_GENERATION_MAX_SSE_EVENT_BYTES
        > workload_control_module.AUDIO_GENERATION_MAX_ENCODED_CHARS
    )


def test_audio_generation_framer_accepts_event_above_legacy_cap() -> None:
    legacy_event_cap = 2 * 1024 * 1024
    raw = b"data: " + b"x" * (legacy_event_cap + 1) + b"\n\n"
    event_buffer = R8DAudioSseEventBuffer(
        max_event_bytes=(
            workload_control_module.AUDIO_GENERATION_MAX_SSE_EVENT_BYTES
        )
    )
    events: list[str] = []
    for offset in range(0, len(raw), 64 * 1024):
        events.extend(event_buffer.feed(raw[offset : offset + 64 * 1024]))
    events.extend(event_buffer.finish())

    assert len(events) == 1
    assert len(events[0].encode("utf-8")) > legacy_event_cap


def test_audio_generation_framer_still_rejects_event_over_bound() -> None:
    event_buffer = R8DAudioSseEventBuffer(max_event_bytes=8)
    with pytest.raises(R8DAudioSseContractError) as exc_info:
        event_buffer.feed(b"123456789\n\n")

    assert exc_info.value.code == "sse_event_too_large"


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


class _CancellationResistantCloseStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.close_started = asyncio.Event()
        self.release = asyncio.Event()
        self.close_count = 0
        self.cancel_count = 0

    async def __aiter__(self):
        yield self.content

    async def aclose(self) -> None:
        self.close_count += 1
        self.close_started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancel_count += 1


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
    input_text: str = '{"count":3,"color":"blue"}',
    finish_reason: object = "stop",
    done: bool = True,
    trailing_stop: bool = False,
) -> bytes:
    first_delta: dict[str, object]
    if shape == "chat_audio_input":
        first_delta = {"content": input_text}
    else:
        audio_bytes = PCM16_BYTES if shape == "chat_audio_output" else MP3_BYTES
        encoded = base64.b64encode(audio_bytes).decode("ascii")
        first_delta = {"audio": {"data": encoded[:503]}}
    events: list[dict[str, object]] = [
        {
            "id": "generation-r8d",
            "model": model_id,
            "choices": [{"delta": first_delta, "finish_reason": None}],
        }
    ]
    if shape != "chat_audio_input":
        audio_bytes = PCM16_BYTES if shape == "chat_audio_output" else MP3_BYTES
        encoded = base64.b64encode(audio_bytes).decode("ascii")
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
    if shape != "audio_generation_stream" and finish_reason == "stop":
        events.append(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            }
        )
    if trailing_stop:
        events.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def _audio_generation_normalized_terminal_sse(
    model_id: str,
    *,
    native_finish_reason: str = "provider-completed",
) -> bytes:
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")
    events = [
        {
            "id": "generation-r8d-normalized-terminal",
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
            "id": "generation-r8d-normalized-terminal",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                    "native_finish_reason": native_finish_reason,
                }
            ],
        },
        {
            "id": "generation-r8d-normalized-terminal",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "", "role": "assistant"},
                    "finish_reason": "stop",
                    "native_finish_reason": native_finish_reason,
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
    return (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n"
    ).encode()


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
async def test_chat_audio_input_fixture_requires_human_audit_before_provider_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "provider/audio-r8d"
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        chat_input_fixture,
        "CHAT_AUDIO_INPUT_FIXTURE_HUMAN_AUDIT_STATUS",
        "pending",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        raise AssertionError("fixture audit gate must precede the Provider POST")

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="fixture-audit-pending",
    )

    assert result.status == "failed"
    assert result.error_code == (
        "provider_multimodal_chat_audio_input_fixture_not_human_verified"
    )
    assert result.provider_dispatch_state == "not_dispatched"
    assert result.checks.input_fixture_human_verified is not True
    assert [request.method for request in requests].count("POST") == 0


@pytest.mark.asyncio
async def test_audio_generation_v5_fixture_guard_blocks_before_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        raise AssertionError("invalid fixture request must not reach Provider POST")

    monkeypatch.setattr(
        ProviderWorkloadCertificationService,
        "_r8d_request_payload",
        staticmethod(
            lambda payload: build_openrouter_audio_generation_payload(
                model_id=payload.model_id,
                prompt=SYNTHETIC_AUDIO_GENERATION_PROMPT,
            )
        ),
    )
    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-v5-invalid-fixture",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_multimodal_audio_image_fixture_invalid"
    assert result.provider_dispatch_state == "not_dispatched"
    assert result.checks.image_prompt_request_verified is False
    assert [request.method for request in requests].count("POST") == 0


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
        if shape == "chat_audio_input":
            assert body["max_tokens"] == 64
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
            assert "three" not in SYNTHETIC_CHAT_AUDIO_INPUT_PROMPT.casefold()
            assert "blue" not in SYNTHETIC_CHAT_AUDIO_INPUT_PROMPT.casefold()
            audio = message["content"][1]["input_audio"]
            assert set(audio) == {"data", "format"}
            assert audio["format"] == "wav"
            assert not audio["data"].startswith("data:")
            wav_bytes = base64.b64decode(audio["data"], validate=True)
            assert wav_bytes == chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_WAV_BYTES
            assert wav_bytes != SYNTHETIC_AUDIO_WAV_BYTES
            assert "modalities" not in body
        elif shape == "chat_audio_output":
            assert body["max_tokens"] == 2048
            assert body["modalities"] == ["text", "audio"]
            assert body["audio"] == {"voice": "alloy", "format": "pcm16"}
        else:
            assert body == build_openrouter_audio_generation_payload(
                model_id=model_id,
                prompt=SYNTHETIC_AUDIO_GENERATION_PROMPT,
                image_data_url=SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL,
            )
            content = body["messages"][0]["content"]
            assert content == [
                {
                    "type": "text",
                    "text": SYNTHETIC_AUDIO_GENERATION_PROMPT,
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL,
                    },
                },
            ]
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
    expected_contract_version = {
        "chat_audio_input": R8D_CHAT_AUDIO_INPUT_PARAMETER_CONTRACT_VERSION,
        "chat_audio_output": R8D_CHAT_AUDIO_OUTPUT_PARAMETER_CONTRACT_VERSION,
    }.get(shape, R8D_AUDIO_PARAMETER_CONTRACT_VERSION)
    assert profile["audio_parameter_contract_version"] == expected_contract_version
    assert profile["stream"] is True
    assert r8d_audio_parameter_profile_reason(shape, profile) is None
    if shape == "chat_audio_input":
        assert profile["certified_input_formats"] == ["wav"]
        assert profile["fixture_id"] == chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_ID
        assert profile["fixture_wav_sha256"] == (
            chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_WAV_SHA256
        )
        assert profile["fixture_manifest_sha256"] == (
            chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_MANIFEST_SHA256
        )
        assert profile["input_fixture_human_verified"] is True
        assert result.checks.input_fixture_human_verified is True
        assert result.checks.audio_semantics_matches_fixture is True
        assert result.checks.transcript_matches_fixture is None
    elif shape == "chat_audio_output":
        assert profile["certified_voice"] == "alloy"
        assert profile["certified_response_format"] == "wav"
        assert profile["certified_upstream_format"] == "pcm16"
        assert profile["pcm_sample_rate_hz"] == CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ
        assert profile["pcm_channels"] == CHAT_AUDIO_PCM16_CHANNELS
        assert profile["pcm_bits_per_sample"] == CHAT_AUDIO_PCM16_BITS_PER_SAMPLE
        assert profile["pcm_byte_order"] == CHAT_AUDIO_PCM16_BYTE_ORDER
        assert profile["parameter_evidence_source"] == (
            CHAT_AUDIO_PCM16_PARAMETER_EVIDENCE
        )
        assert result.checks.audio_transport_format_verified is True
        assert result.checks.audio_delivery_format_verified is True
    else:
        assert profile["certified_output_format"] == "mp3"
        assert profile["supports_image_prompt"] is True
        assert profile["request_contract"] == (
            OPENROUTER_AUDIO_GENERATION_REQUEST_CONTRACT
        )
        assert profile["certification_prompt_sha256"] == (
            SYNTHETIC_AUDIO_GENERATION_PROMPT_SHA256
        )
        assert profile["image_prompt_fixture_id"] == (
            SYNTHETIC_AUDIO_GENERATION_IMAGE_FIXTURE_ID
        )
        assert profile["image_prompt_fixture_sha256"] == (
            SYNTHETIC_AUDIO_GENERATION_IMAGE_SHA256
        )
        assert profile["image_prompt_media_type"] == (
            SYNTHETIC_AUDIO_GENERATION_IMAGE_MEDIA_TYPE
        )
        assert profile["image_prompt_width"] == (
            SYNTHETIC_AUDIO_GENERATION_IMAGE_WIDTH
        )
        assert profile["image_prompt_height"] == (
            SYNTHETIC_AUDIO_GENERATION_IMAGE_HEIGHT
        )
        assert result.checks.image_prompt_request_verified is True

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
    assert SYNTHETIC_AUDIO_GENERATION_PROMPT not in serialized
    assert base64.b64encode(MP3_BYTES).decode("ascii") not in serialized
    assert base64.b64encode(WAV_BYTES).decode("ascii") not in serialized
    assert chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_WAV_BASE64 not in serialized
    assert SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL not in serialized
    assert SYNTHETIC_AUDIO_GENERATION_IMAGE_BASE64 not in serialized

    # Simulate stale persisted evidence without issuing another Provider POST.
    expected_stale_reason = "provider_multimodal_audio_evidence_incomplete"
    with sqlite3.connect(service.repository.database_path) as database:
        if shape == "audio_generation_stream":
            stale_profile = dict(profile)
            stale_profile["audio_parameter_contract_version"] = (
                "modelmirror-provider-audio-generation-parameters-v3"
            )
            database.execute(
                "UPDATE provider_workload_certifications SET profile_json = ? "
                "WHERE tenant_id = ? AND id = ?",
                (json.dumps(stale_profile), "local", result.certification_id),
            )
            expected_stale_reason = (
                "provider_multimodal_audio_parameter_contract_stale"
            )
        else:
            checks = json.loads(str(row["checks_json"]))
            checks.pop("safe_terminal_verified")
            for name in tuple(checks):
                if name.startswith("finish_") or name in {
                    "sse_done_observed", "transcript_matches_fixture",
                    "audio_semantics_matches_fixture",
                }:
                    checks.pop(name)
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
    assert old_summary.status == "stale"
    assert replayed_old.status == "stale"
    assert old_summary.blocked_reason == expected_stale_reason
    assert current_policy.effective_status == "degraded_required"
    assert current_policy.bindings[0].valid is False
    assert current_policy.bindings[0].reason_code == old_summary.blocked_reason
    with pytest.raises(RouterServiceError) as preflight:
        ProviderWorkloadCallService(restarted_service).start_run(
            entry_id  # type: ignore[arg-type]
        )
    assert preflight.value.code == "provider_workload_policy_not_active"
    assert [item.method for item in requests].count("POST") == 1
    assert old_summary.checks.safe_terminal_verified is (
        shape == "audio_generation_stream"
    )
    preserved = service.repository.get_workload_certification(
        "local", str(result.certification_id)
    )
    assert preserved is not None and preserved["status"] == "passed"
    if shape == "audio_generation_stream":
        assert old_summary.checks.sse_done_observed is True
        assert old_summary.checks.finish_stop_observed is True
        assert json.loads(str(preserved["profile_json"])) == stale_profile
        assert json.loads(str(preserved["checks_json"])) == json.loads(
            str(row["checks_json"])
        )
    else:
        assert old_summary.checks.sse_done_observed is None
        assert old_summary.checks.finish_stop_observed is None
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
    assert result.error_code == (
        "provider_multimodal_chat_audio_input_semantics_mismatch"
    )
    assert result.checks.media_format_verified is False
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["chat_audio_input", "chat_audio_output"])
async def test_gpt_audio_mini_streaming_certification_is_blocked_before_post(
    tmp_path: Path,
    shape: str,
) -> None:
    model_id = "openai/gpt-audio-mini"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        pytest.fail("streaming-incompatible model must not receive a paid POST")

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

    with pytest.raises(RouterServiceError) as blocked:
        await certifications.run(
            connection.id,
            _r8d_certification_request(shape, model_id),
            idempotency_key=f"gpt-audio-mini-{shape}",
        )

    assert blocked.value.code == (
        "provider_multimodal_upstream_streaming_unsupported"
    )
    assert requests
    assert all(request.method == "GET" for request in requests)
    assert [request.method for request in requests].count("POST") == 0
    assert service.repository.list_workload_certifications("local") == []
    assert service.repository.list_multimodal_certification_sessions("local") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["chat_audio_input", "chat_audio_output"])
async def test_gpt_audio_streaming_certification_remains_eligible(
    tmp_path: Path,
    shape: str,
) -> None:
    model_id = "openai/gpt-audio"
    transport, requests = _sse_mock_transport(
        model_id,
        _sse_body(model_id, shape),
    )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape=shape,
        model_id=model_id,
        idempotency_key=f"gpt-audio-{shape}",
    )

    assert result.status == "passed"
    assert result.actual_model == model_id
    assert result.provider_dispatch_state == "confirmed"
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
    assert result.error_code == replay.error_code == "provider_workload_http_400"
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
    assert result.error_code == "provider_workload_http_400"
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
    assert result.error_code == "provider_workload_http_400"
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
    assert result.error_code == "provider_workload_http_400"
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
    assert result.error_code == "provider_workload_http_400"
    assert result.warning_codes == []
    assert result.provider_dispatch_state == "confirmed"
    assert result.retry_allowed is False
    assert post_count == 1
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_r8d_certification_deadline_discards_late_valid_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    stream = _CancellationResistantCloseStream(
        _sse_body(model_id, "chat_audio_input")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
        )

    monkeypatch.setattr(
        workload_control_module,
        "R8D_AUDIO_CERTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.03,
    )
    service, connection = _service(tmp_path, httpx.MockTransport(handler))
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )

    try:
        result = await certifications.run(
            connection.id,
            _r8d_certification_request("chat_audio_input", model_id),
            idempotency_key="late-valid-stream",
        )
    finally:
        stream.release.set()
        for _ in range(100):
            if not workload_control_module._R8D_AUDIO_CERTIFICATION_BACKGROUND_TASKS:
                break
            await asyncio.sleep(0.01)

    assert result.status == "uncertain"
    assert result.error_code == "provider_workload_total_timeout"
    assert result.provider_dispatch_state == "uncertain"
    assert result.retry_allowed is False
    assert post_count == 1
    assert stream.cancel_count >= 1
    replay = await certifications.run(
        connection.id,
        _r8d_certification_request("chat_audio_input", model_id),
        idempotency_key="late-valid-stream",
    )
    assert replay.status == "uncertain"
    assert replay.provider_dispatch_state == "uncertain"
    assert post_count == 1


@pytest.mark.asyncio
async def test_r8d_certification_deadline_preserves_known_http_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    stream = _CancellationResistantCloseStream(
        b'{"error":{"code":400,"message":"bounded diagnostic"}}'
    )

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

    monkeypatch.setattr(
        workload_control_module,
        "R8D_AUDIO_CERTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.03,
    )
    service, connection = _service(tmp_path, httpx.MockTransport(handler))
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    try:
        result = await certifications.run(
            connection.id,
            _r8d_certification_request("chat_audio_input", model_id),
            idempotency_key="known-error-at-deadline",
        )
    finally:
        stream.release.set()
        for _ in range(100):
            if not workload_control_module._R8D_AUDIO_CERTIFICATION_BACKGROUND_TASKS:
                break
            await asyncio.sleep(0.01)

    assert result.status == "failed"
    assert result.error_code == "provider_workload_http_400"
    assert result.provider_dispatch_state == "confirmed"
    assert result.retry_allowed is False
    assert post_count == 1


@pytest.mark.asyncio
async def test_r8d_known_http_error_double_cancel_releases_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "provider/audio-r8d"
    stream = _CancellationResistantCloseStream(
        b'{"error":{"code":400,"message":"bounded diagnostic"}}'
    )
    post_count = 0

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
    monkeypatch.setattr(
        workload_control_module,
        "MAX_R8D_AUDIO_CERTIFICATION_BACKGROUND_TASKS",
        3,
    )
    task = asyncio.create_task(
        certifications.run(
            connection.id,
            _r8d_certification_request("chat_audio_input", model_id),
            idempotency_key="known-error-double-cancel",
        )
    )
    await asyncio.wait_for(stream.close_started.wait(), timeout=1)
    assert task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        result = await task
    finally:
        stream.release.set()
        for _ in range(100):
            if not workload_control_module._R8D_AUDIO_CERTIFICATION_BACKGROUND_TASKS:
                break
            await asyncio.sleep(0.01)

    assert result.status == "failed"
    assert result.error_code == "provider_workload_http_400"
    assert result.provider_dispatch_state == "confirmed"
    assert post_count == 1
    assert workload_control_module._R8D_AUDIO_CERTIFICATION_RESERVED_UNITS == 0


@pytest.mark.asyncio
async def test_r8d_certification_deadline_revokes_pre_dispatch_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "provider/audio-r8d"
    post_count = 0
    authorization_started = asyncio.Event()
    authorization_release = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        return httpx.Response(
            200,
            content=_sse_body(model_id, "chat_audio_input"),
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
    original_authorize = certifications.multimodal_transport.authorize

    async def blocked_authorize(target):
        authorization_started.set()
        while not authorization_release.is_set():
            try:
                await authorization_release.wait()
            except asyncio.CancelledError:
                continue
        return await original_authorize(target)

    monkeypatch.setattr(
        certifications.multimodal_transport,
        "authorize",
        blocked_authorize,
    )
    monkeypatch.setattr(
        workload_control_module,
        "R8D_AUDIO_CERTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.03,
    )

    try:
        result = await certifications.run(
            connection.id,
            _r8d_certification_request("chat_audio_input", model_id),
            idempotency_key="pre-dispatch-deadline",
        )
    finally:
        authorization_release.set()
        for _ in range(100):
            if not workload_control_module._R8D_AUDIO_CERTIFICATION_BACKGROUND_TASKS:
                break
            await asyncio.sleep(0.01)

    assert authorization_started.is_set()
    assert result.status == "failed"
    assert result.error_code == "provider_workload_total_timeout"
    assert result.provider_dispatch_state == "not_dispatched"
    assert post_count == 0

    assert post_count == 0


@pytest.mark.asyncio
async def test_r8d_certification_finalizer_cannot_commit_success_after_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "provider/audio-r8d"
    transport, requests = _sse_mock_transport(
        model_id,
        _sse_body(model_id, "chat_audio_input"),
    )
    service, connection = _service(tmp_path, transport)
    certifications = ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ),
    )
    original_finalize = (
        service.repository.complete_multimodal_workload_certification
    )

    def delayed_finalize(*args, **kwargs):
        import time

        time.sleep(0.06)
        return original_finalize(*args, **kwargs)

    offerings: list[object] = []
    monkeypatch.setattr(
        service.repository,
        "complete_multimodal_workload_certification",
        delayed_finalize,
    )
    monkeypatch.setattr(
        certifications,
        "_record_certified_offering",
        lambda *_args: offerings.append(object()),
    )
    monkeypatch.setattr(
        workload_control_module,
        "R8D_AUDIO_CERTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.03,
    )

    result = await certifications.run(
        connection.id,
        _r8d_certification_request("chat_audio_input", model_id),
        idempotency_key="late-finalizer",
    )

    assert result.status == "uncertain"
    assert result.error_code == "provider_workload_total_timeout"
    assert result.provider_dispatch_state == "uncertain"
    assert offerings == []
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_r8d_cleanup_capacity_is_reserved_before_provider_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workload_control_module,
        "MAX_R8D_AUDIO_CERTIFICATION_BACKGROUND_TASKS",
        3,
    )
    monkeypatch.setattr(
        workload_control_module,
        "R8D_AUDIO_CERTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.03,
    )
    first_model = "provider/audio-r8d-first"
    first_posts = 0
    first_stream = _CancellationResistantCloseStream(
        _sse_body(first_model, "chat_audio_input")
    )

    def first_handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_posts
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": first_model}]})
        first_posts += 1
        return httpx.Response(200, stream=first_stream)

    first_service, first_connection = _service(
        tmp_path / "first",
        httpx.MockTransport(first_handler),
    )
    first_certifications = ProviderWorkloadCertificationService(
        first_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(first_handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    try:
        first = await first_certifications.run(
            first_connection.id,
            _r8d_certification_request("chat_audio_input", first_model),
            idempotency_key="capacity-first",
        )
        assert first.status == "uncertain"
        assert first_posts == 1

        second_model = "provider/audio-r8d-second"
        second_posts = 0

        def second_handler(request: httpx.Request) -> httpx.Response:
            nonlocal second_posts
            if request.method == "GET":
                return httpx.Response(
                    200, json={"data": [{"id": second_model}]}
                )
            second_posts += 1
            return httpx.Response(
                200,
                content=_sse_body(second_model, "chat_audio_input"),
            )

        second_transport = httpx.MockTransport(second_handler)
        second_service, second_connection = _service(
            tmp_path / "second",
            second_transport,
        )
        second_certifications = ProviderWorkloadCertificationService(
            second_service,
            client_factory=lambda: httpx.AsyncClient(
                transport=second_transport,
                follow_redirects=False,
                trust_env=False,
            ),
        )
        second = await second_certifications.run(
            second_connection.id,
            _r8d_certification_request("chat_audio_input", second_model),
            idempotency_key="capacity-second",
        )

        assert second.status == "failed"
        assert second.error_code == (
            "provider_workload_cleanup_capacity_exhausted"
        )
        assert second.provider_dispatch_state == "not_dispatched"
        assert second_posts == 0
    finally:
        first_stream.release.set()
        for _ in range(100):
            if not workload_control_module._R8D_AUDIO_CERTIFICATION_BACKGROUND_TASKS:
                break
            await asyncio.sleep(0.01)



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
    assert result.error_code == "provider_workload_http_400"
    assert result.warning_codes == expected_warnings
    assert post_count == 1
    assert len(streams) == 1 and streams[0].close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "status_code", "expected_error"),
    [
        ("chat_audio_output", 400, "provider_workload_http_400"),
        ("chat_audio_output", 402, "provider_workload_http_402"),
        ("chat_audio_output", 413, "provider_workload_http_413"),
        ("chat_audio_output", 422, "provider_workload_http_422"),
        ("chat_audio_input", 401, "provider_workload_http_401"),
        ("chat_audio_input", 429, "provider_workload_http_429"),
        ("chat_audio_input", 503, "provider_workload_http_5xx"),
    ],
)
async def test_r8d_openrouter_error_subtype_and_status_are_bounded(
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
    assert result.warning_codes == (
        ["openrouter_error_type_invalid_request"]
        if status_code in {400, 402, 413, 422}
        else []
    )
    assert post_count == 1
    assert "private-upstream-message" not in _serialized_certification_state(
        service, result
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_text", "expected_status"),
    [
        ('{"count":3,"color":"blue"}', "passed"),
        (' { "color": "blue", "count": 3 } \n', "passed"),
        ('{"count":true,"color":"blue"}', "failed"),
        ('{"count":3.0,"color":"blue"}', "failed"),
        ('{"count":3,"color":"Blue"}', "failed"),
        ('{"count":2,"color":"blue"}', "failed"),
        ('{"count":3,"color":"blue","extra":1}', "failed"),
        ('{"count":3,"count":3,"color":"blue"}', "failed"),
        ('```json\n{"count":3,"color":"blue"}\n```', "failed"),
        ('Answer: {"count":3,"color":"blue"}', "failed"),
        ("unrelated-private-marker", "failed"),
    ],
)
async def test_r8d_audio_input_accepts_only_exact_fixture_semantics(
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
    assert result.checks.audio_semantics_matches_fixture is (
        expected_status == "passed"
    )
    assert result.checks.transcript_matches_fixture is None
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
    ("events", "expected_error"),
    [
        (
            [
                {
                    "id": "generation-a",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"audio": {"data": base64.b64encode(MP3_BYTES).decode()}},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "generation-b",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ],
            "provider_workload_invalid_sse",
        ),
        (
            [
                {
                    "id": "generation-a",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"audio": {"data": base64.b64encode(MP3_BYTES).decode()}},
                            "finish_reason": "stop",
                        }
                    ],
                },
                {
                    "id": "generation-a",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "private trailing text"},
                            "finish_reason": None,
                        }
                    ],
                },
            ],
            "provider_workload_invalid_sse",
        ),
        (
            [{"error": "private upstream failure"}],
            "provider_workload_stream_error",
        ),
        (
            [
                {
                    "id": "generation-a",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": None},
                        {"index": 1, "delta": {}, "finish_reason": None},
                    ],
                }
            ],
            "provider_workload_invalid_sse",
        ),
    ],
)
async def test_audio_generation_certification_rejects_ambiguous_sse(
    tmp_path: Path,
    events: list[dict[str, object]],
    expected_error: str,
) -> None:
    model_id = "google/lyria-3-clip-preview"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        body = "".join(
            f"data: {json.dumps({'model': model_id, **event})}\n\n"
            for event in events
        )
        return httpx.Response(200, content=(body + "data: [DONE]\n\n").encode())

    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-ambiguous-sse",
    )

    assert result.status == "failed"
    assert result.error_code == expected_error
    assert "private trailing text" not in _serialized_certification_state(
        service, result
    )
    assert "private upstream failure" not in _serialized_certification_state(
        service, result
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "native_finish_reason",
    ["provider-completed", "x" * 128],
    ids=["provider-raw-reason", "maximum-bounded-raw-reason"],
)
async def test_audio_generation_certification_accepts_normalized_terminal_and_usage_replay(
    tmp_path: Path,
    native_finish_reason: str,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    transport, requests = _sse_mock_transport(
        model_id,
        _audio_generation_normalized_terminal_sse(
            model_id,
            native_finish_reason=native_finish_reason,
        ),
    )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-normalized-terminal",
    )

    assert result.status == "passed"
    assert result.error_code is None
    assert result.checks.safe_terminal_verified is True
    assert result.checks.sse_done_observed is True
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (
        2,
        3,
        5,
    )
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_error", "expected_rejection"),
    [
        ("content", "provider_workload_invalid_sse", "invalid_terminal_replay_shape"),
        ("audio", "provider_workload_invalid_sse", "invalid_terminal_replay_shape"),
        ("tool_calls", "provider_workload_invalid_sse", "invalid_terminal_replay_shape"),
        ("duplicate", "provider_workload_invalid_sse", "data_after_terminal_replay"),
        ("generation_id", "provider_workload_invalid_sse", "generation_id_mismatch"),
        ("model", "provider_workload_model_mismatch", None),
        ("missing_usage", "provider_workload_invalid_sse", "invalid_usage_shape"),
        ("invalid_usage", "provider_workload_invalid_sse", "usage_total_mismatch"),
        ("missing_done", "provider_workload_missing_terminal", None),
    ],
)
async def test_audio_generation_terminal_usage_replay_rejects_unsafe_variants(
    tmp_path: Path,
    case: str,
    expected_error: str,
    expected_rejection: str | None,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")
    replay: dict[str, object] = {
        "id": "generation-r8d-replay",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {"content": "", "role": "assistant"},
                "finish_reason": "stop",
                "native_finish_reason": "provider-completed",
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 5,
        },
    }
    replay_choice = replay["choices"][0]  # type: ignore[index]
    replay_delta = replay_choice["delta"]  # type: ignore[index]
    if case == "content":
        replay_delta["content"] = "private replay text"  # type: ignore[index]
    elif case == "audio":
        replay_delta["audio"] = {"data": encoded}  # type: ignore[index]
    elif case == "tool_calls":
        replay_delta["tool_calls"] = [  # type: ignore[index]
            {"id": "private-tool-call", "type": "function"}
        ]
    elif case == "generation_id":
        replay["id"] = "different-generation"
    elif case == "model":
        replay["model"] = "different/model"
    elif case == "missing_usage":
        replay.pop("usage")
    elif case == "invalid_usage":
        replay["usage"] = {
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 6,
        }

    events: list[dict[str, object]] = [
        {
            "id": "generation-r8d-replay",
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
            "id": "generation-r8d-replay",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                    "native_finish_reason": "provider-completed",
                }
            ],
        },
        replay,
    ]
    if case == "duplicate":
        events.append(dict(replay))
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    if case != "missing_done":
        body += "data: [DONE]\n\n"
    transport, requests = _sse_mock_transport(model_id, body.encode())

    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key=f"audio-generation-replay-{case}",
    )

    assert result.status == "failed"
    assert result.error_code == expected_error
    assert result.checks.safe_terminal_verified is False
    assert result.checks.sse_rejection_reason == expected_rejection
    assert [request.method for request in requests].count("POST") == 1
    serialized = _serialized_certification_state(service, result)
    assert "private replay text" not in serialized
    assert "private-tool-call" not in serialized
    assert encoded not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["chat_audio_input", "chat_audio_output"])
async def test_chat_audio_shapes_still_reject_native_finish_mismatch(
    tmp_path: Path,
    shape: str,
) -> None:
    model_id = "provider/audio-r8d"
    first_delta: dict[str, object]
    if shape == "chat_audio_input":
        first_delta = {"content": '{"count":3,"color":"blue"}'}
    else:
        first_delta = {
            "audio": {"data": base64.b64encode(PCM16_BYTES).decode("ascii")}
        }
    events = [
        {
            "id": "generation-r8d-native-scope",
            "model": model_id,
            "choices": [
                {"index": 0, "delta": first_delta, "finish_reason": None}
            ],
        },
        {
            "id": "generation-r8d-native-scope",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                    "native_finish_reason": "provider-completed",
                }
            ],
        },
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    transport, requests = _sse_mock_transport(
        model_id,
        (body + "data: [DONE]\n\n").encode(),
    )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape=shape,
        model_id=model_id,
        idempotency_key=f"native-finish-shape-scope-{shape}",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_invalid_sse"
    assert result.checks.sse_rejection_reason == "native_finish_mismatch"
    assert result.checks.safe_terminal_verified is False
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("native_finish_reason", [7, "x" * 129])
async def test_audio_generation_certification_rejects_invalid_native_finish_reason(
    tmp_path: Path,
    native_finish_reason: object,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")
    event = {
        "id": "generation-a",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {"audio": {"data": encoded}},
                "finish_reason": "stop",
                "native_finish_reason": native_finish_reason,
            }
        ],
    }
    transport, requests = _sse_mock_transport(
        model_id,
        (f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n").encode(),
    )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key=(
            "audio-generation-invalid-native-"
            f"{type(native_finish_reason).__name__}"
        ),
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_invalid_sse"
    assert result.checks.sse_rejection_reason == "native_finish_mismatch"
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_event",
    [
        (
            '{"id":"generation-a","model":"google/lyria-3-clip-preview",'
            '"error":{"message":"hidden"},"error":null,'
            '"choices":[{"index":0,"delta":{"audio":{"data":"%s"}},'
            '"finish_reason":"stop"}]}'
        ),
        (
            '{"id":"generation-a","model":"google/lyria-3-clip-preview",'
            '"choices":[{"index":0,"delta":{"audio":{"data":"%s"}},'
            '"finish_reason":"length","finish_reason":"stop"}]}'
        ),
    ],
)
async def test_audio_generation_certification_rejects_duplicate_sse_keys(
    tmp_path: Path,
    raw_event: str,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            content=(
                f"data: {raw_event % encoded}\n\ndata: [DONE]\n\n"
            ).encode(),
        )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-duplicate-sse-keys",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_invalid_sse"
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_audio_generation_certification_persists_only_bounded_sse_diagnostics(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")
    private_field = "private_provider_extension"
    private_value = "private-provider-value"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        events = [
            {
                "id": "generation-a",
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
                "id": "generation-a",
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {private_field: private_value},
                        "finish_reason": None,
                    }
                ],
            },
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(
            200,
            content=(body + "data: [DONE]\n\n").encode(),
        )

    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-bounded-sse-diagnostics",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_invalid_sse"
    assert result.checks.sse_accepted_event_count == 1
    assert result.checks.sse_accepted_audio_fragment_count == 1
    assert (
        result.checks.sse_rejection_reason
        == "unexpected_audio_generation_delta"
    )
    assert [request.method for request in requests].count("POST") == 1
    row = service.repository.get_workload_certification(
        "local",
        str(result.certification_id),
    )
    assert row is not None
    stored_checks = json.loads(str(row["checks_json"]))
    assert stored_checks["sse_accepted_event_count"] == 1
    assert stored_checks["sse_accepted_audio_fragment_count"] == 1
    assert (
        stored_checks["sse_rejection_reason"]
        == "unexpected_audio_generation_delta"
    )
    serialized = _serialized_certification_state(service, result)
    assert private_field not in serialized
    assert private_value not in serialized
    assert private_field not in caplog.text
    assert private_value not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("empty_before_stop", "provider_workload_invalid_sse"),
        ("empty_after_stop", "provider_workload_invalid_sse"),
        ("missing_choices_after_stop", "provider_workload_invalid_sse"),
        ("error_null", "provider_workload_stream_error"),
        ("duplicate_usage_terminal", "provider_workload_invalid_sse"),
        ("normal_usage_empty", "provider_workload_invalid_sse"),
        ("normal_usage_string", "provider_workload_invalid_sse"),
        ("normal_usage_inconsistent", "provider_workload_invalid_sse"),
        ("native_finish_without_finish", "provider_workload_invalid_sse"),
        ("mixed_refusal", "provider_workload_invalid_sse"),
        ("non_string_content", "provider_workload_invalid_sse"),
        ("audio_not_object", "provider_workload_invalid_sse"),
        ("audio_data_non_string", "provider_workload_invalid_sse"),
        ("audio_transcript_non_string", "provider_workload_invalid_sse"),
    ],
)
async def test_chat_audio_certification_rejects_runtime_invalid_terminal_shapes(
    tmp_path: Path,
    case: str,
    expected_error: str,
) -> None:
    model_id = "provider/audio-r8d"
    content_event = {
        "id": "generation-a",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {"content": '{"count":3,"color":"blue"}'},
                "finish_reason": "stop",
            }
        ],
    }
    usage_event = {
        "id": "generation-a",
        "model": model_id,
        "choices": [],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 5,
        },
    }
    events: list[dict[str, object]] = [content_event]
    if case == "empty_before_stop":
        events = [{"choices": []}, content_event]
    elif case == "empty_after_stop":
        events.append({"choices": []})
    elif case == "missing_choices_after_stop":
        events.append({"usage": {"total_tokens": 5}})
    elif case == "error_null":
        content_event["error"] = None
    elif case == "duplicate_usage_terminal":
        events.extend([usage_event, usage_event])
    elif case == "normal_usage_empty":
        content_event["usage"] = {}
    elif case == "normal_usage_string":
        content_event["usage"] = {
            "prompt_tokens": "2",
            "completion_tokens": 3,
            "total_tokens": 5,
        }
    elif case == "normal_usage_inconsistent":
        content_event["usage"] = {
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 6,
        }
    elif case == "native_finish_without_finish":
        choice = content_event["choices"][0]  # type: ignore[index]
        choice["finish_reason"] = None  # type: ignore[index]
        choice["native_finish_reason"] = "stop"  # type: ignore[index]
    elif case == "mixed_refusal":
        choice = content_event["choices"][0]  # type: ignore[index]
        choice["delta"]["refusal"] = "blocked"  # type: ignore[index]
    elif case == "non_string_content":
        choice = content_event["choices"][0]  # type: ignore[index]
        choice["delta"]["content"] = [  # type: ignore[index]
            {"type": "text", "text": '{"count":3,"color":"blue"}'}
        ]
    elif case == "audio_not_object":
        choice = content_event["choices"][0]  # type: ignore[index]
        choice["delta"]["audio"] = []  # type: ignore[index]
    elif case == "audio_data_non_string":
        choice = content_event["choices"][0]  # type: ignore[index]
        choice["delta"]["audio"] = {"data": 123}  # type: ignore[index]
    elif case == "audio_transcript_non_string":
        choice = content_event["choices"][0]  # type: ignore[index]
        choice["delta"]["audio"] = {"transcript": []}  # type: ignore[index]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, content=(body + "data: [DONE]\n\n").encode())

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key=f"chat-audio-terminal-parity-{case}",
    )

    assert result.status == "failed"
    assert result.error_code == expected_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("error_null", "provider_workload_stream_error"),
        ("refusal", "provider_workload_invalid_sse"),
        ("normal_usage_empty", "provider_workload_invalid_sse"),
        ("native_finish_without_finish", "provider_workload_invalid_sse"),
        ("huge_cost", "provider_workload_invalid_sse"),
        ("audio_not_object", "provider_workload_invalid_sse"),
        ("audio_data_non_string", "provider_workload_invalid_sse"),
        ("audio_transcript_non_string", "provider_workload_invalid_sse"),
    ],
)
async def test_audio_generation_certification_uses_shared_sse_contract(
    tmp_path: Path,
    case: str,
    expected_error: str,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    event: dict[str, object] = {
        "id": "generation-a",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "audio": {
                        "data": base64.b64encode(MP3_BYTES).decode("ascii")
                    }
                },
                "finish_reason": "stop",
            }
        ],
    }
    choice = event["choices"][0]  # type: ignore[index]
    delta = choice["delta"]  # type: ignore[index]
    if case == "error_null":
        event["error"] = None
    elif case == "refusal":
        delta["refusal"] = "blocked"  # type: ignore[index]
    elif case == "normal_usage_empty":
        event["usage"] = {}
    elif case == "native_finish_without_finish":
        choice["finish_reason"] = None  # type: ignore[index]
        choice["native_finish_reason"] = "stop"  # type: ignore[index]
    elif case == "huge_cost":
        event["usage"] = {"cost": 10**400}
    elif case == "audio_not_object":
        delta["audio"] = []  # type: ignore[index]
    elif case == "audio_data_non_string":
        delta["audio"] = {"data": 123}  # type: ignore[index]
    elif case == "audio_transcript_non_string":
        delta["audio"] = {"transcript": []}  # type: ignore[index]

    # Keep a valid prefix so malformed trailing audio cannot be silently dropped.
    if case in {
        "audio_not_object",
        "audio_data_non_string",
        "audio_transcript_non_string",
    }:
        valid = {
            "id": "generation-a",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "audio": {
                            "data": base64.b64encode(MP3_BYTES).decode("ascii")
                        }
                    },
                    "finish_reason": None,
                }
            ],
        }
    else:
        valid = None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        body = (
            (f"data: {json.dumps(valid)}\n\n" if valid is not None else "")
            + f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"
        )
        return httpx.Response(200, content=body.encode())

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key=f"audio-shared-contract-{case}",
    )

    assert result.status == "failed"
    assert result.error_code == expected_error


@pytest.mark.asyncio
async def test_r8d_certification_accepts_mixed_sse_line_endings(
    tmp_path: Path,
) -> None:
    model_id = "provider/audio-r8d"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        body = _sse_body(model_id, "chat_audio_input").replace(b"\n\n", b"\n\r\n")
        return httpx.Response(200, content=body)

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_input",
        model_id=model_id,
        idempotency_key="chat-audio-mixed-line-endings",
    )

    assert result.status == "passed"


@pytest.mark.asyncio
async def test_audio_generation_certification_accepts_one_usage_only_terminal(
    tmp_path: Path,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        events = [
            {
                "id": "generation-a",
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"audio": {"data": encoded}},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "id": "generation-a",
                "model": model_id,
                "choices": [],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, content=(body + "data: [DONE]\n\n").encode())

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-usage-only-terminal",
    )
    assert result.status == "passed"
    assert result.checks.safe_terminal_verified is True
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (
        2,
        3,
        5,
    )


@pytest.mark.asyncio
async def test_audio_generation_certification_uses_runtime_budget_and_discards_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    encoded = base64.b64encode(MP3_BYTES).decode("ascii")
    monkeypatch.setattr(
        workload_control_module,
        "R8D_AUDIO_CERTIFICATION_TOTAL_TIMEOUT_SECONDS",
        -1.0,
    )
    monkeypatch.setattr(
        workload_control_module,
        "AUDIO_GENERATION_TOTAL_TIMEOUT_SECONDS",
        1.0,
    )
    monkeypatch.setattr(
        workload_control_module,
        "MAX_WORKLOAD_SSE_EVENT_BYTES",
        64,
    )
    monkeypatch.setattr(
        workload_control_module,
        "MAX_WORKLOAD_STREAM_BYTES",
        128,
    )
    monkeypatch.setattr(
        workload_control_module,
        "AUDIO_GENERATION_MAX_SSE_EVENT_BYTES",
        4 * 1024,
    )
    monkeypatch.setattr(
        workload_control_module,
        "AUDIO_GENERATION_MAX_SSE_STREAM_BYTES",
        8 * 1024,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        assert request.extensions["timeout"] == {
            "connect": workload_control_module.AUDIO_GENERATION_CONNECT_TIMEOUT_SECONDS,
            "read": workload_control_module.AUDIO_GENERATION_READ_TIMEOUT_SECONDS,
            "write": workload_control_module.AUDIO_GENERATION_WRITE_TIMEOUT_SECONDS,
            "pool": workload_control_module.AUDIO_GENERATION_POOL_TIMEOUT_SECONDS,
        }
        event = {
            "id": "generation-a",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "Instrumental intro, then a short refrain.",
                        "audio": {"data": encoded},
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode()
        assert len(body) > 128
        return httpx.Response(200, content=body)

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-runtime-budget",
    )

    assert result.status == "passed"
    assert result.checks.safe_terminal_verified is True
    assert result.checks.sse_text_content_observed is True
    assert result.checks.sse_text_content_char_count == len(
        "Instrumental intro, then a short refrain."
    )


@pytest.mark.asyncio
async def test_audio_generation_text_only_terminal_is_distinct_and_redacted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model_id = "google/lyria-3-clip-preview"
    private_text = "Private upstream text without any audio fragment."
    events = [
        {
            "id": "generation-text-only",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": private_text},
                    "finish_reason": None,
                }
            ],
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 3,
                "total_tokens": 11,
            },
        },
    ]
    body = (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n"
    ).encode()
    transport, requests = _sse_mock_transport(model_id, body)
    caplog.set_level(logging.DEBUG)

    service, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="audio_generation_stream",
        model_id=model_id,
        idempotency_key="audio-generation-text-only-diagnostic",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_multimodal_audio_text_only"
    assert result.checks.sse_text_content_observed is True
    assert result.checks.sse_text_content_char_count == len(private_text)
    assert result.checks.sse_accepted_audio_fragment_count == 0
    assert result.checks.safe_terminal_verified is True
    assert [request.method for request in requests].count("POST") == 1
    row = service.repository.get_workload_certification(
        "local",
        str(result.certification_id),
    )
    assert row is not None
    stored_checks = json.loads(str(row["checks_json"]))
    assert stored_checks["sse_text_content_observed"] is True
    assert stored_checks["sse_text_content_char_count"] == len(private_text)
    serialized = _serialized_certification_state(service, result)
    assert private_text not in serialized
    assert private_text not in caplog.text


@pytest.mark.asyncio
async def test_chat_audio_output_certification_uses_runtime_stream_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/gpt-audio"
    body = _sse_body(model_id, "chat_audio_output")
    monkeypatch.setattr(
        workload_control_module,
        "MAX_WORKLOAD_STREAM_BYTES",
        len(body) - 1,
    )
    monkeypatch.setattr(
        workload_control_module,
        "CHAT_AUDIO_MAX_SSE_STREAM_BYTES",
        len(body),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(200, content=body)

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        httpx.MockTransport(handler),
        shape="chat_audio_output",
        model_id=model_id,
        idempotency_key="chat-audio-output-runtime-stream-budget",
    )

    assert len(body) > workload_control_module.MAX_WORKLOAD_STREAM_BYTES
    assert result.status == "passed"
    assert result.checks.safe_terminal_verified is True


@pytest.mark.asyncio
async def test_chat_audio_output_certification_enforces_shared_stream_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = "openai/gpt-audio"
    body = _sse_body(model_id, "chat_audio_output")
    monkeypatch.setattr(
        workload_control_module,
        "MAX_WORKLOAD_STREAM_BYTES",
        len(body) + 1,
    )
    monkeypatch.setattr(
        workload_control_module,
        "CHAT_AUDIO_MAX_SSE_STREAM_BYTES",
        len(body) - 1,
    )
    transport, requests = _sse_mock_transport(model_id, body)

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_output",
        model_id=model_id,
        idempotency_key="chat-audio-output-shared-stream-cap",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_stream_too_large"
    assert len([request for request in requests if request.method == "POST"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_kind", ["encoded_audio", "delivery_text"])
async def test_chat_audio_output_certification_enforces_runtime_payload_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budget_kind: str,
) -> None:
    model_id = "openai/gpt-audio"
    encoded = base64.b64encode(PCM16_BYTES).decode("ascii")
    delta: dict[str, object] = {"audio": {"data": encoded}}
    if budget_kind == "encoded_audio":
        monkeypatch.setattr(
            workload_control_module,
            "CHAT_AUDIO_MAX_ENCODED_CHARS",
            len(encoded) - 1,
        )
    else:
        delta["content"] = "x" * 9
        monkeypatch.setattr(
            workload_control_module,
            "CHAT_AUDIO_MAX_DELIVERY_TEXT_CHARS",
            8,
        )
    events = [
        {
            "id": "generation-r8d",
            "model": model_id,
            "choices": [{"delta": delta, "finish_reason": None}],
        },
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    body = (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n"
    ).encode()
    transport, requests = _sse_mock_transport(model_id, body)

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_output",
        model_id=model_id,
        idempotency_key=f"chat-audio-output-{budget_kind}-cap",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_stream_too_large"
    assert len([request for request in requests if request.method == "POST"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "finish_reason", "done", "expected_status"),
    [
        ("chat_audio_input", "stop", False, "failed"),
        ("chat_audio_input", None, True, "failed"),
        ("chat_audio_input", None, False, "failed"),
        ("chat_audio_output", "stop", False, "failed"),
        ("chat_audio_output", None, True, "passed"),
        ("chat_audio_output", "", True, "failed"),
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
    assert (
        "finish_reason_missing_accepted" in result.warning_codes
    ) is (
        shape == "chat_audio_output"
        and finish_reason is None
        and done
    )


@pytest.mark.asyncio
async def test_chat_audio_output_certification_v4_accepts_done_only_without_usage(
    tmp_path: Path,
) -> None:
    model_id = "openai/gpt-audio"
    encoded = base64.b64encode(PCM16_BYTES).decode("ascii")
    body = (
        "data: "
        + json.dumps(
            {
                "id": "generation-r8d-done-only",
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"audio": {"data": encoded}},
                        "finish_reason": None,
                    }
                ],
            }
        )
        + "\n\ndata: [DONE]\n\n"
    ).encode()
    transport, requests = _sse_mock_transport(model_id, body)

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_output",
        model_id=model_id,
        idempotency_key="chat-audio-output-v4-done-only-no-usage",
    )

    assert result.status == "passed"
    assert result.error_code is None
    assert result.actual_model == model_id
    assert result.checks.safe_terminal_verified is True
    assert result.checks.sse_done_observed is True
    assert result.checks.finish_stop_observed is False
    assert result.checks.audio_transport_format_verified is True
    assert result.checks.audio_delivery_format_verified is True
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    assert result.total_tokens is None
    assert "finish_reason_missing_accepted" in result.warning_codes
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_chat_audio_output_certification_v4_rejects_empty_finish_before_stop(
    tmp_path: Path,
) -> None:
    model_id = "openai/gpt-audio"
    body = _sse_body(
        model_id,
        "chat_audio_output",
        finish_reason="",
        trailing_stop=True,
        done=False,
    ) + (
        'data: {"choices":[],"usage":{"prompt_tokens":2,'
        '"completion_tokens":3,"total_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    ).encode()
    transport, requests = _sse_mock_transport(
        model_id,
        body,
    )

    _, _, _, _, result = await _run_r8d_certification_case(
        tmp_path,
        transport,
        shape="chat_audio_output",
        model_id=model_id,
        idempotency_key="chat-audio-output-v4-empty-then-stop",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_workload_missing_terminal"
    assert result.checks.safe_terminal_verified is False
    assert result.checks.finish_stop_observed is True
    assert result.checks.sse_done_observed is True
    assert "finish_reason_missing_accepted" not in result.warning_codes
    assert [request.method for request in requests].count("POST") == 1


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
                        "delta": {"content": '{"count":3,"color":"blue"}'},
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
                "failed",
                None,
                "provider_workload_invalid_sse",
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
                        "delta": {"content": '{"count":3,"color":"blue"}'},
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
        ("audio_generation_stream", "passed"),
    ],
)
async def test_r8d_terminal_usage_replay_is_accepted_for_supported_audio_shapes(
    tmp_path: Path,
    shape: str,
    expected_status: str,
) -> None:
    model_id = "provider/audio-r8d"
    media_bytes = PCM16_BYTES if shape == "chat_audio_output" else MP3_BYTES
    encoded = base64.b64encode(media_bytes).decode("ascii")
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
    ("stop", True, "provider_multimodal_chat_audio_input_semantics_mismatch"),
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
    assert result.checks.audio_semantics_matches_fixture is (
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
            {"choices": [{"delta": {"content": '{"count":3,"color":"blue"}'}}]},
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
                    "delta": {"content": '{"count":3,"color":"blue"}'},
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
        "fixture_id": chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_ID,
        "fixture_wav_sha256": (
            chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_WAV_SHA256
        ),
        "fixture_manifest_sha256": (
            chat_input_fixture.CHAT_AUDIO_INPUT_FIXTURE_MANIFEST_SHA256
        ),
        "prompt_contract": chat_input_fixture.CHAT_AUDIO_INPUT_PROMPT_CONTRACT,
        "scoring_contract": chat_input_fixture.CHAT_AUDIO_INPUT_SCORING_CONTRACT,
        "input_fixture_human_verified": True,
        "input_fixture_human_audit_receipt": (
            chat_input_fixture.chat_audio_input_fixture_human_audit_receipt()
        ),
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
        "audio_parameter_contract_version": (
            R8D_CHAT_AUDIO_OUTPUT_PARAMETER_CONTRACT_VERSION
        ),
        "stream": True,
        "certified_voice": "alloy",
        "certified_response_format": "wav",
        "certified_upstream_format": "pcm16",
        "pcm_sample_rate_hz": CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ,
        "pcm_channels": CHAT_AUDIO_PCM16_CHANNELS,
        "pcm_bits_per_sample": CHAT_AUDIO_PCM16_BITS_PER_SAMPLE,
        "pcm_byte_order": CHAT_AUDIO_PCM16_BYTE_ORDER,
        "parameter_evidence_source": CHAT_AUDIO_PCM16_PARAMETER_EVIDENCE,
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
    stale["audio_parameter_contract_version"] = (
        "modelmirror-provider-chat-audio-output-parameters-v3"
    )
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
        "audio_transport_format_verified": True,
        "audio_delivery_format_verified": True,
    }
    assert r8d_audio_certification_evidence_reason(
        "chat_audio_output", complete_checks
    ) is None
    input_checks = {
        **complete_checks,
        "input_fixture_human_verified": True,
        "audio_semantics_matches_fixture": True,
    }
    assert r8d_audio_certification_evidence_reason(
        "chat_audio_input", input_checks
    ) is None
    input_checks["audio_semantics_matches_fixture"] = False
    assert r8d_audio_certification_evidence_reason(
        "chat_audio_input", input_checks
    ) == "provider_multimodal_audio_evidence_incomplete"
    generation_checks = {
        **complete_checks,
        "image_prompt_request_verified": True,
    }
    assert r8d_audio_certification_evidence_reason(
        "audio_generation_stream", generation_checks
    ) is None
    generation_checks["image_prompt_request_verified"] = False
    assert r8d_audio_certification_evidence_reason(
        "audio_generation_stream", generation_checks
    ) == "provider_multimodal_audio_evidence_incomplete"
    complete_checks.pop("safe_terminal_verified")
    assert r8d_audio_certification_evidence_reason(
        "audio_generation_stream", complete_checks
    ) == "provider_multimodal_audio_evidence_incomplete"

    generation_profile = {
        "audio_parameter_contract_version": R8D_AUDIO_PARAMETER_CONTRACT_VERSION,
        "stream": True,
        "certified_output_format": "mp3",
        "supports_image_prompt": True,
        "request_contract": OPENROUTER_AUDIO_GENERATION_REQUEST_CONTRACT,
        "certification_prompt_sha256": SYNTHETIC_AUDIO_GENERATION_PROMPT_SHA256,
        "image_prompt_fixture_id": SYNTHETIC_AUDIO_GENERATION_IMAGE_FIXTURE_ID,
        "image_prompt_fixture_sha256": SYNTHETIC_AUDIO_GENERATION_IMAGE_SHA256,
        "image_prompt_media_type": SYNTHETIC_AUDIO_GENERATION_IMAGE_MEDIA_TYPE,
        "image_prompt_width": SYNTHETIC_AUDIO_GENERATION_IMAGE_WIDTH,
        "image_prompt_height": SYNTHETIC_AUDIO_GENERATION_IMAGE_HEIGHT,
    }
    assert r8d_audio_parameter_profile_reason(
        "audio_generation_stream", generation_profile
    ) is None
    stale_generation_contract = dict(generation_profile)
    stale_generation_contract["audio_parameter_contract_version"] = (
        "modelmirror-provider-audio-generation-parameters-v4"
    )
    assert r8d_audio_parameter_profile_reason(
        "audio_generation_stream", stale_generation_contract
    ) == "provider_multimodal_audio_parameter_contract_stale"
    stale_generation = dict(generation_profile)
    stale_generation.pop("request_contract")
    assert r8d_audio_parameter_profile_reason(
        "audio_generation_stream", stale_generation
    ) == "provider_multimodal_audio_parameter_profile_invalid"
    for field, invalid_value in (
        ("supports_image_prompt", False),
        ("image_prompt_fixture_id", "other-fixture"),
        ("image_prompt_fixture_sha256", "0" * 64),
        ("certification_prompt_sha256", "0" * 64),
        ("image_prompt_media_type", "image/jpeg"),
        ("image_prompt_width", 3),
        ("image_prompt_height", 3),
    ):
        invalid_generation = dict(generation_profile)
        invalid_generation[field] = invalid_value
        assert r8d_audio_parameter_profile_reason(
            "audio_generation_stream", invalid_generation
        ) == "provider_multimodal_audio_parameter_profile_invalid"


@pytest.mark.parametrize(
    "content",
    [
        MP3_BYTES,
        b"RIFF\x04\x00\x00\x00WAVE",
        WAV_BYTES[:-1],
        WAV_BYTES.replace(b"fmt ", b"bad ", 1),
        WAV_BYTES.replace(b"data", b"none", 1),
        WAV_BYTES[:20] + b"\x00" * 16 + WAV_BYTES[36:],
        WAV_BYTES[:32] + b"\x01\x00" + WAV_BYTES[34:],
        _append_wav_chunk(WAV_BYTES, b"data", b""),
        _append_wav_chunk(WAV_BYTES, b"fmt ", b"\x00"),
        _append_wav_chunk(WAV_BYTES, b"fmt ", WAV_BYTES[20:36]),
        WAV_BYTES[:12] + WAV_BYTES[36:] + WAV_BYTES[12:36],
    ],
)
def test_r8d_wav_validator_rejects_incomplete_or_wrong_media(
    content: bytes,
) -> None:
    assert is_complete_wav(content) is False


def test_r8d_wav_validator_accepts_complete_fixture() -> None:
    assert is_complete_wav(WAV_BYTES) is True


@pytest.mark.parametrize(
    "content",
    [b"", b"\x00", WAV_BYTES, MP3_BYTES, b"ID3\x00\x00", b"OggS\x00\x00"],
)
def test_r8d_pcm16_validator_rejects_empty_unaligned_or_container_media(
    content: bytes,
) -> None:
    assert is_valid_chat_audio_pcm16(content) is False


def test_r8d_pcm16_validator_does_not_reject_valid_negative_first_sample() -> None:
    content = b"\xff\xff" + b"\x00\x00" * 400

    assert is_valid_chat_audio_pcm16(content) is True


def test_r8d_pcm16_to_wav_uses_versioned_delivery_profile() -> None:
    wav = chat_audio_pcm16_to_wav(PCM16_BYTES)

    assert is_complete_wav(
        wav,
        expected_sample_rate_hz=CHAT_AUDIO_PCM16_SAMPLE_RATE_HZ,
        expected_channels=CHAT_AUDIO_PCM16_CHANNELS,
        expected_bits_per_sample=CHAT_AUDIO_PCM16_BITS_PER_SAMPLE,
    )
    assert wav[44:] == PCM16_BYTES
    assert is_complete_wav(
        wav,
        expected_sample_rate_hz=16_000,
    ) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_id",
    [
        "google/lyria-3-clip-preview",
        "google/lyria-3-pro-preview",
    ],
)
async def test_r8d_managed_audio_job_is_single_post_and_persists_redacted_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
) -> None:
    post_count = 0
    post_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        post_count += 1
        post_bodies.append(json.loads(request.content))
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
        idempotency_key=f"managed-audio-job-{model_id.rsplit('/', 1)[-1]}",
        image_filename="fixture.png",
        image_content_type="image/png",
        image_content=base64.b64decode(
            SYNTHETIC_AUDIO_GENERATION_IMAGE_BASE64,
            validate=True,
        ),
    )
    assert launch.task is not None
    assert launch.job.execution_mode == "managed"
    await jobs.run(launch.task)

    completed = jobs.get(launch.job.job_id)
    replay = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key=f"managed-audio-job-{model_id.rsplit('/', 1)[-1]}",
        image_filename="fixture.png",
        image_content_type="image/png",
        image_content=base64.b64decode(
            SYNTHETIC_AUDIO_GENERATION_IMAGE_BASE64,
            validate=True,
        ),
    )
    assert completed.status == "succeeded"
    assert completed.actual_model == model_id
    assert completed.generation_id is None
    assert completed.provider_dispatch_state == "confirmed"
    assert completed.provider_route_receipts[0]["call_count"] == 1
    assert completed.provider_route_receipts[0]["status"] == "passed"
    assert replay.task is None
    assert post_count == 2  # one certification plus one runtime call
    assert post_bodies[0] == build_openrouter_audio_generation_payload(
        model_id=model_id,
        prompt=SYNTHETIC_AUDIO_GENERATION_PROMPT,
        image_data_url=SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL,
    )
    assert post_bodies[1] == build_openrouter_audio_generation_payload(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        image_data_url=SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL,
    )

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
    assert SYNTHETIC_AUDIO_GENERATION_IMAGE_DATA_URL not in serialized
    assert SYNTHETIC_AUDIO_GENERATION_IMAGE_BASE64 not in serialized
    with pytest.raises(MultimodalServiceError) as retained:
        jobs.delete(completed.job_id)
    assert retained.value.code == "managed_audio_job_retained"
    replay_after_delete = await jobs.create(
        model_id=model_id,
        prompt="A neutral synthetic melody.",
        idempotency_key=f"managed-audio-job-{model_id.rsplit('/', 1)[-1]}",
        image_filename="fixture.png",
        image_content_type="image/png",
        image_content=base64.b64decode(
            SYNTHETIC_AUDIO_GENERATION_IMAGE_BASE64,
            validate=True,
        ),
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
async def test_r8d_managed_audio_job_preserves_receipt_metrics_when_first_result_update_fails(
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
        idempotency_key="managed-audio-first-result-store-error",
    )
    assert launch.task is not None
    original_update = jobs._update  # noqa: SLF001
    failed_once = False

    def fail_first_provider_result_update(job_id: str, **changes: object):
        nonlocal failed_once
        if (
            not failed_once
            and changes.get("status") == "running"
            and "output_bytes" in changes
        ):
            failed_once = True
            raise RuntimeError("simulated first provider result store failure")
        return original_update(job_id, **changes)

    monkeypatch.setattr(jobs, "_update", fail_first_provider_result_update)
    await jobs.run(launch.task)

    assert failed_once is True
    output_path = jobs._output_path(launch.job.job_id)  # noqa: SLF001
    assert output_path.read_bytes() == MP3_BYTES
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    call = calls[0]
    assert call["status"] == "passed"
    assert call["dispatched"] == 1
    assert call["actual_model"] == model_id
    assert call["ttft_ms"] is not None
    assert float(call["ttft_ms"]) >= 0.0
    assert call["prompt_tokens"] == 2
    assert call["completion_tokens"] == 3
    assert call["total_tokens"] == 5

    monkeypatch.setattr(jobs, "_update", original_update)
    recovered = jobs.get(launch.job.job_id)
    assert recovered.status == "succeeded"
    assert recovered.provider_dispatch_state == "confirmed"
    assert recovered.output_bytes == len(MP3_BYTES)
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
            _audio_generation_normalized_terminal_sse,
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
