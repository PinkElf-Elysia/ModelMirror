from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient as RealAsyncClient
from starlette.requests import ClientDisconnect

from server import main as main_module
from server.file_assets.output_contracts import FileOutputResponse
from server.main import app
from server.model_router import configure_model_router, get_model_router_service
from server.model_router import chat_audio_input_fixture as chat_input_fixture
from server.model_router import multimodal_gateway as multimodal_gateway_module
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.multimodal_gateway import (
    ManagedMultimodalChatStreamEvidence,
)
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest,
    ProviderWorkloadBindingUpdate,
    ProviderWorkloadCertificationRequest,
    ProviderWorkloadPolicyUpdate,
    RouterConnectionCreate,
)
from server.model_router.service import ModelRouterService
from server.model_router.workload_control import (
    ProviderWorkloadCallService,
    ProviderWorkloadCertificationService,
    ProviderWorkloadControlService,
)
from server.model_router.multimodal_control import (
    SYNTHETIC_AUDIO_WAV_BYTES,
    is_complete_wav,
)
from server.multimodal.api import configure_chat_attachment_store
from server.multimodal.chat_attachments import ChatAttachmentStore
from server.multimodal.stt import MultimodalServiceError, OpenRouterTarget


MP3_FRAME = b"\xff\xfb\x90\xc0" + b"\x55" * 413
MP3_BYTES = MP3_FRAME * 3
WAV_BYTES = SYNTHETIC_AUDIO_WAV_BYTES
PCM16_BYTES = WAV_BYTES[44:]


@pytest.fixture(autouse=True)
def _approved_chat_audio_input_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_input_fixture,
        "CHAT_AUDIO_INPUT_FIXTURE_HUMAN_AUDIT_STATUS",
        "approved",
    )


class RuntimeResponse:
    def __init__(
        self,
        chunks: list[str],
        *,
        trailing_error: Exception | None = None,
        stall_after_chunks: bool = False,
    ) -> None:
        self.status_code = 200
        self.headers = {"x-request-id": "managed-audio-runtime"}
        self._chunks = chunks
        self._trailing_error = trailing_error
        self._stall_after_chunks = stall_after_chunks
        self.closed = False
        self.client_closed = False
        self.text_iterator_used = False
        self.bytes_iterator_used = False

    async def aiter_text(self):
        self.text_iterator_used = True
        for chunk in self._chunks:
            yield chunk
        if self._trailing_error is not None:
            raise self._trailing_error

    async def aiter_bytes(self):
        self.bytes_iterator_used = True
        for chunk in self._chunks:
            yield chunk.encode("utf-8")
        if self._trailing_error is not None:
            raise self._trailing_error
        if self._stall_after_chunks:
            await asyncio.Event().wait()

    async def aread(self) -> bytes:
        return b""

    async def aclose(self) -> None:
        self.closed = True


class RuntimeClient:
    def __init__(
        self,
        response: RuntimeResponse,
        sent: list[dict[str, Any]],
    ) -> None:
        self.response = response
        self.sent = sent

    def build_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        return {"method": method, "url": str(url), **kwargs}

    async def send(
        self,
        request: dict[str, Any],
        *,
        stream: bool,
        follow_redirects: bool = False,
    ) -> RuntimeResponse:
        assert stream is True
        assert follow_redirects is False
        self.sent.append(request)
        return self.response

    async def aclose(self) -> None:
        self.response.client_closed = True


def certification_stream(model_id: str, shape: str) -> bytes:
    delta: dict[str, object]
    if shape == "chat_audio_input":
        delta = {"content": '{"count":3,"color":"blue"}'}
    else:
        delta = {
            "audio": {
                "data": base64.b64encode(PCM16_BYTES).decode("ascii")
            }
        }
    events = [
        {
            "model": model_id,
            "choices": [{"delta": delta, "finish_reason": None}],
        },
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    return (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n"
    ).encode()


async def configured_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    entry_id: str,
    shape: str,
) -> tuple[ModelRouterService, ChatAttachmentStore]:
    model_id = "provider/audio-r8d"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        return httpx.Response(
            200,
            content=certification_stream(model_id, shape),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    repository = SQLiteRouterRepository(tmp_path / "router", master_key=b"x" * 32)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="R8D managed audio",
            kind="openrouter",
            base_url="https://provider.example/v1",
            api_key="r8d-runtime-secret",
            scopes=["chat", "audio"],
        ),
    )
    service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: ["8.8.8.8"]
        ),
    )
    certification = await ProviderWorkloadCertificationService(
        service,
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False
        ),
    ).run(
        connection.id,
        ProviderWorkloadCertificationRequest(
            execution_shape=shape,  # type: ignore[arg-type]
            model_id=model_id,
            adapter_contract="openrouter_chat_audio_v1",
            acknowledge_billed_call=True,
        ),
        idempotency_key=f"r8d-chat-{shape}",
    )
    assert certification.status == "passed"
    monkeypatch.setenv("MODEL_CONTROL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_CHAT_AUDIO_ENABLED", "true")
    monkeypatch.setenv("MULTIMODAL_STREAMING_AUDIO_ENABLED", "true")
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
                    adapter_contract="openrouter_chat_audio_v1",
                )
            ],
        ),
    )
    control.activate(
        entry_id,  # type: ignore[arg-type]
        ProviderWorkloadActivationRequest(
            expected_revision=saved.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
        ),
    )
    store = ChatAttachmentStore(root=tmp_path / "attachments")
    configure_model_router(service)
    configure_chat_attachment_store(store)
    assert [item.method for item in requests].count("POST") == 1
    return service, store


def output_payload(*, audio_format: str = "wav") -> dict[str, object]:
    return {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [{"role": "user", "content": "Say OK."}],
        "response_audio": {
            "enabled": True,
            "voice": "alloy",
            "format": audio_format,
        },
    }


def output_stream(
    *,
    model_id: str | None = "provider/audio-r8d",
    encoded_audio: str | None = None,
    second_model: str | None = None,
    audio_bytes: bytes = PCM16_BYTES,
) -> list[str]:
    encoded = encoded_audio or base64.b64encode(audio_bytes).decode("ascii")
    first: dict[str, object] = {
        "choices": [
            {
                "delta": {
                    "audio": {"data": encoded, "transcript": "private-output"}
                },
                "finish_reason": None,
            }
        ]
    }
    if model_id is not None:
        first["model"] = model_id
    second: dict[str, object] = {
        "choices": [{"delta": {}, "finish_reason": "stop"}]
    }
    if second_model is not None:
        second["model"] = second_model
    return [
        f"data: {json.dumps(first)}\n\n",
        f"data: {json.dumps(second)}\n\n",
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "done", "expected_code"),
    [
        ("error", True, "provider_workload_stream_error"),
        ("content_filter", True, "provider_workload_content_filtered"),
        ("length", True, "provider_workload_output_truncated"),
        ("", True, "provider_chat_missing_terminal"),
        ("stop", False, "provider_chat_missing_terminal"),
    ],
)
async def test_managed_chat_audio_output_rejects_unsafe_terminal_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finish_reason: str,
    done: bool,
    expected_code: str,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    terminal = json.loads(chunks[-2][6:])
    terminal["choices"][0]["finish_reason"] = finish_reason
    chunks[-2] = f"data: {json.dumps(terminal)}\n\n"
    if not done:
        chunks.pop()
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert expected_code in result.text
    assert len(sent) == 1
    assert "private-output" not in result.text
    assert base64.b64encode(WAV_BYTES).decode("ascii") not in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_managed_chat_audio_output_rejects_empty_finish_before_later_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    first = json.loads(chunks[0][6:])
    first["choices"][0]["finish_reason"] = ""
    chunks[0] = f"data: {json.dumps(first)}\n\n"
    chunks.insert(
        -1,
        'data: {"choices":[],"usage":{"prompt_tokens":2,'
        '"completion_tokens":3,"total_tokens":5}}\n\n',
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert "provider_chat_missing_terminal" in result.text
    assert len(sent) == 1
    assert "private-output" not in result.text
    assert base64.b64encode(WAV_BYTES).decode("ascii") not in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_field", ["absent", "null"])
async def test_managed_chat_audio_output_accepts_done_only_terminal_under_v4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finish_field: str,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    terminal = json.loads(chunks[-2][6:])
    if finish_field == "absent":
        terminal["choices"][0].pop("finish_reason")
    else:
        terminal["choices"][0]["finish_reason"] = None
    chunks[-2] = f"data: {json.dumps(terminal)}\n\n"
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "private-output" in result.text
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("event: message_end") == 1
    assert result.text.count("data: [DONE]") == 1
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "passed"


def test_managed_chat_audio_evidence_rejects_post_terminal_content_and_choices() -> None:
    cases = [
        [
            {
                "model": "provider/audio-r8d",
                "choices": [
                    {"index": 0, "delta": {"content": "first"}, "finish_reason": "stop"}
                ],
            },
            {
                "choices": [
                    {"index": 0, "delta": {"content": "late"}, "finish_reason": "stop"}
                ],
            },
        ],
        [
            {
                "model": "provider/audio-r8d",
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"},
                    {"index": 1, "delta": {"content": "cross-choice"}},
                ],
            },
        ],
    ]
    for events in cases:
        evidence = ManagedMultimodalChatStreamEvidence(
            execution_shape="chat_audio_input",
            expected_model="provider/audio-r8d",
            started_at=0.0,
        )
        for event in events:
            evidence.feed(f"data: {json.dumps(event)}\n\n")
        evidence.feed("data: [DONE]\n\n")
        status, _result_class, code, *_rest = evidence.finish(
            transport_completed=True
        )
        assert status == "failed"
        assert code == "provider_multimodal_invalid_sse"


@pytest.mark.parametrize(
    "raw_event",
    [
        (
            'data: {"model":"provider/audio-r8d",'
            '"error":{"message":"hidden"},"error":null,'
            '"choices":[{"index":0,"delta":{"content":"verified"},'
            '"finish_reason":"stop"}]}\n\n'
        ),
        (
            'data: {"model":"provider/audio-r8d",'
            '"choices":[{"index":0,"delta":{"content":"verified"},'
            '"finish_reason":"length","finish_reason":"stop"}]}\n\n'
        ),
    ],
)
def test_managed_chat_audio_evidence_rejects_duplicate_sse_keys(
    raw_event: str,
) -> None:
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_input",
        expected_model="provider/audio-r8d",
        started_at=0.0,
    )

    evidence.feed(raw_event)
    evidence.feed("data: [DONE]\n\n")
    status, _result_class, code, *_rest = evidence.finish(
        transport_completed=True
    )

    assert status == "failed"
    assert code == "provider_multimodal_invalid_sse"


@pytest.mark.parametrize(
    "case",
    [
        "empty_before_stop",
        "empty_after_stop",
        "missing_choices_after_stop",
        "error_null",
        "duplicate_usage_terminal",
        "normal_usage_empty",
        "normal_usage_string",
        "normal_usage_inconsistent",
        "native_finish_without_finish",
        "mixed_refusal",
        "non_string_content",
        "audio_not_object",
        "audio_data_non_string",
        "audio_transcript_non_string",
    ],
)
def test_managed_chat_audio_runtime_rejects_invalid_terminal_shapes(
    case: str,
) -> None:
    content_event: dict[str, object] = {
        "id": "generation-a",
        "model": "provider/audio-r8d",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "verified"},
                "finish_reason": "stop",
            }
        ],
    }
    usage_event = {
        "id": "generation-a",
        "model": "provider/audio-r8d",
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
            {"type": "text", "text": "verified"}
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
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_input",
        expected_model="provider/audio-r8d",
        started_at=0.0,
    )

    for event in events:
        evidence.feed(f"data: {json.dumps(event)}\n\n")
    evidence.feed("data: [DONE]\n\n")
    status, _result_class, _code, *_rest = evidence.finish(
        transport_completed=True
    )

    assert status == "failed"


def test_managed_chat_audio_input_framing_is_chunk_independent() -> None:
    model_id = "provider/audio-r8d"
    payload = json.dumps(
        {
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "verified"},
                    "finish_reason": "stop",
                }
            ],
        },
        separators=(",", ":"),
    )
    raw = (
        f"data: {payload}\r\n\r\n"
        "data: [DONE]\n\r\n"
    )
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_input",
        expected_model=model_id,
        started_at=0.0,
    )

    for value in raw:
        evidence.feed(value)
    status, _result_class, code, *_rest = evidence.finish(
        transport_completed=True
    )

    assert status == "succeeded"
    assert code is None
    delivery = b"".join(evidence.normalized_input_delivery_events())
    assert b"\r" not in delivery
    assert b'"content":"verified"' in delivery
    assert b"[DONE]" not in delivery


@pytest.mark.asyncio
async def test_managed_chat_audio_input_canonicalizes_lone_cr_multiline_sse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    _service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="synthetic.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload: dict[str, object] = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
    }
    first = (
        'data: {"model":"provider/audio-r8d",\r'
        'data: "choices":[{"index":0,"delta":{"content":"verified-text"},'
        '"finish_reason":"stop"}]}\r\r'
    )
    raw = first + "data: [DONE]\r\r"
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            payload,
            RuntimeResponse(list(raw)),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "verified-text" in result.text
    assert result.text.count("data: [DONE]") == 1
    assert "provider_multimodal_invalid_sse" not in result.text


def test_managed_chat_audio_evidence_requires_clean_eof_and_allows_one_usage_replay() -> None:
    events = [
        {
            "model": "provider/audio-r8d",
            "choices": [
                {"index": 0, "delta": {"content": "verified"}, "finish_reason": None}
            ],
        },
        {
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        },
        {
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        },
    ]
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_input",
        expected_model="provider/audio-r8d",
        started_at=0.0,
    )
    for event in events:
        evidence.feed(f"data: {json.dumps(event)}\n\n")
    evidence.feed("data: [DONE]\n\n")
    status, _result_class, code, *_rest = evidence.finish(
        transport_completed=False
    )
    assert status == "uncertain"
    assert code == "provider_workload_stream_interrupted"

    complete = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_input",
        expected_model="provider/audio-r8d",
        started_at=0.0,
    )
    for event in events:
        complete.feed(f"data: {json.dumps(event)}\n\n")
    complete.feed("data: [DONE]\n\n")
    status, _result_class, code, *_rest = complete.finish(
        transport_completed=True
    )
    assert status == "succeeded"
    assert code is None


@pytest.mark.parametrize(
    "terminal_delta,usage,terminal_id",
    [
        (
            {"audio": {"transcript": "late"}},
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "gen-1",
        ),
        (
            {"tool_calls": [{"id": "late"}]},
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "gen-1",
        ),
        ({}, None, "gen-1"),
        (
            {},
            {"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0},
            "gen-1",
        ),
        (
            {},
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "gen-2",
        ),
    ],
)
def test_managed_audio_evidence_rejects_unsafe_terminal_usage_replay(
    terminal_delta: dict[str, object],
    usage: dict[str, int] | None,
    terminal_id: str,
) -> None:
    model_id = "provider/audio-r8d"
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_output",
        expected_model=model_id,
        started_at=0.0,
        expected_audio_format="wav",
    )
    events: list[dict[str, object]] = [
        {
            "id": "gen-1",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "audio": {
                            "data": base64.b64encode(WAV_BYTES).decode("ascii")
                        }
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "gen-1",
            "model": model_id,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    replay: dict[str, object] = {
        "id": terminal_id,
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": terminal_delta,
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        replay["usage"] = usage
    events.append(replay)
    for event in events:
        evidence.feed(f"data: {json.dumps(event)}\n\n")
    evidence.feed("data: [DONE]\n\n")

    status, result_class, code, _checks, _warnings = evidence.finish(
        transport_completed=True
    )

    assert status == "failed"
    assert result_class == "hard_failure"
    assert code == "provider_multimodal_invalid_sse"


def test_managed_audio_evidence_accepts_usage_only_terminal_replay() -> None:
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_output",
        expected_model="provider/audio-r8d",
        started_at=0.0,
        expected_audio_format="pcm16",
    )
    for event in [
        {
            "id": "gen-1",
            "model": "provider/audio-r8d",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "audio": {
                            "data": base64.b64encode(PCM16_BYTES).decode("ascii")
                        }
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "gen-1",
            "model": "provider/audio-r8d",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        },
        {
            "id": "gen-1",
            "model": "provider/audio-r8d",
            "choices": [],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        },
    ]:
        evidence.feed(f"data: {json.dumps(event)}\n\n")
    evidence.feed("data: [DONE]\n\n")

    status, _result_class, code, _checks, warnings = evidence.finish(
        transport_completed=True
    )

    assert status == "succeeded"
    assert code is None
    assert warnings == []
    assert evidence.total_tokens == 5


@pytest.mark.parametrize(
    ("native_finish_reason", "content"),
    [
        ("length", None),
        (None, [{"type": "text", "text": "must-not-be-dropped"}]),
    ],
)
def test_managed_audio_evidence_rejects_conflicting_terminal_or_part_content(
    native_finish_reason: str | None,
    content: object,
) -> None:
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_output",
        expected_model="provider/audio-r8d",
        started_at=0.0,
        expected_audio_format="pcm16",
    )
    first_delta: dict[str, object] = {
        "audio": {"data": base64.b64encode(PCM16_BYTES).decode("ascii")}
    }
    if content is not None:
        first_delta["content"] = content
    first_choice: dict[str, object] = {
        "index": 0,
        "delta": first_delta,
        "finish_reason": "stop" if native_finish_reason else None,
    }
    if native_finish_reason is not None:
        first_choice["native_finish_reason"] = native_finish_reason
    events = [
        {
            "model": "provider/audio-r8d",
            "choices": [first_choice],
        }
    ]
    if native_finish_reason is None:
        events.append(
            {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ]
            }
        )
    for event in events:
        evidence.feed(f"data: {json.dumps(event)}\n\n")
    evidence.feed("data: [DONE]\n\n")

    status, result_class, code, _checks, _warnings = evidence.finish(
        transport_completed=True
    )

    assert status == "failed"
    assert result_class == "hard_failure"
    assert code == "provider_multimodal_invalid_sse"


async def request_with_runtime(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    response: RuntimeResponse,
) -> tuple[httpx.Response, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(response, sent),
    )
    async with RealAsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        result = await client.post("/api/chat", json=payload)
    return result, sent


@pytest.mark.asyncio
async def test_managed_chat_audio_metrics_share_dispatch_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}

    class ClockedRuntimeResponse(RuntimeResponse):
        async def aiter_bytes(self):
            self.bytes_iterator_used = True
            for index, chunk in enumerate(self._chunks):
                if index == 0:
                    clock["now"] = 100.3
                yield chunk.encode("utf-8")
            clock["now"] = 100.35

    class ClockedRuntimeClient(RuntimeClient):
        def build_request(
            self, method: str, url: str, **kwargs: Any
        ) -> dict[str, Any]:
            clock["now"] = 100.2
            return super().build_request(method, url, **kwargs)

    original_service = get_model_router_service()
    service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="synthetic.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload: dict[str, object] = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
    }
    upstream = ClockedRuntimeResponse(
        [
            'data: {"model":"provider/audio-r8d","choices":[{"index":0,'
            '"delta":{"content":"verified-text"},"finish_reason":null}]}\n\n',
            'data: {"choices":[{"index":0,"delta":{},'
            '"finish_reason":"stop"}]}\n\n',
            "data: [DONE]\n\n",
        ]
    )
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: ClockedRuntimeClient(upstream, sent),
    )
    monkeypatch.setattr(time, "perf_counter", lambda: clock["now"])

    try:
        async with RealAsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            result = await client.post("/api/chat", json=payload)
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "verified-text" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    call = calls[0]
    assert call["actual_model"] == "provider/audio-r8d"
    assert float(call["ttft_ms"]) == pytest.approx(100.0)
    assert float(call["e2e_ms"]) == pytest.approx(150.0)
    assert 0.0 <= float(call["ttft_ms"]) <= float(call["e2e_ms"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stream_error", "expected_message"),
    [
        (
            httpx.ReadTimeout("non-audio read timeout"),
            "模型服务连接中断，请稍后重试。",
        ),
        (
            TimeoutError("non-audio total timeout"),
            "后端转发流式响应时出错，请查看服务日志。",
        ),
    ],
)
async def test_non_audio_chat_preserves_legacy_stream_timeout_errors(
    monkeypatch: pytest.MonkeyPatch,
    stream_error: Exception,
    expected_message: str,
) -> None:
    monkeypatch.setattr(
        main_module,
        "LLM_GATEWAY_URL",
        "https://legacy.example/v1",
    )
    monkeypatch.setattr(main_module, "LLM_GATEWAY_KEY", "legacy-test-key")
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    monkeypatch.setenv("MODEL_CONTROL_CHAT_AUDIO_ENABLED", "false")

    def unavailable_runtime():
        raise RuntimeError("runtime unavailable in legacy timeout regression")

    monkeypatch.setattr(
        main_module,
        "create_default_runtime",
        unavailable_runtime,
    )
    payload: dict[str, object] = {
        "model_id": "provider/text-model",
        "gateway": "default",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    result, sent = await request_with_runtime(
        monkeypatch,
        payload,
        RuntimeResponse(
            ['data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'],
            trailing_error=stream_error,
        ),
    )

    assert result.status_code == 200
    assert len(sent) == 1
    assert "partial" in result.text
    assert expected_message in result.text


@pytest.mark.asyncio
async def test_managed_chat_audio_output_releases_only_verified_complete_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(output_stream()),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert sent[0]["json"]["audio"] == {"voice": "alloy", "format": "pcm16"}
    assert "private-output" in result.text
    assert base64.b64encode(PCM16_BYTES).decode("ascii") not in result.text
    delivered_audio = next(
        json.loads(line[6:])["choices"][0]["delta"]["audio"]["data"]
        for line in result.text.splitlines()
        if line.startswith("data: {") and '"audio"' in line
    )
    assert is_complete_wav(base64.b64decode(delivered_audio, validate=True))
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("event: message_end") == 1
    assert result.text.count("data: [DONE]") == 1
    assert result.text.index("event: route_receipt") < result.text.index(
        "event: message_end"
    )
    assert result.text.index("event: message_end") < result.text.index(
        "data: [DONE]"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_output_file_precedes_receipt_and_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OutputService:
        def __init__(self) -> None:
            self.contents: list[bytes] = []

        def register_bytes(self, content: bytes, **context: object) -> FileOutputResponse:
            self.contents.append(content)
            return FileOutputResponse(
                output_id="output_" + "a" * 32,
                asset_id="file_" + "b" * 32,
                purpose="chat",
                scope_id=str(context["scope_id"]),
                producer_kind=str(context["producer_kind"]),
                display_name=str(context["filename"]),
                format=str(context["format_id"]),
                media_type=str(context["media_type"]),
                byte_size=len(content),
                preview_kind="audio",
                status="completed",
                source_message_id=str(context["source_message_id"]),
                created_at="2026-09-07T00:00:00+00:00",
                updated_at="2026-09-07T00:00:00+00:00",
            )

    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    output_service = OutputService()
    monkeypatch.setattr(main_module, "get_file_output_service", lambda: output_service)
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    payload = output_payload()
    payload["file_scope_id"] = "chat-r8d-audio"
    payload["output_context_id"] = "assistant-r8d-audio"
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            payload,
            RuntimeResponse(output_stream()),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert len(output_service.contents) == 1
    assert is_complete_wav(output_service.contents[0])
    assert result.text.count("event: output_file") == 1
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("event: message_end") == 1
    assert result.text.count("data: [DONE]") == 1
    assert result.text.index('"audio"') < result.text.index("event: output_file")
    assert result.text.index("event: output_file") < result.text.index(
        "event: route_receipt"
    )
    assert result.text.index("event: route_receipt") < result.text.index(
        "event: message_end"
    )
    assert result.text.index("event: message_end") < result.text.index(
        "data: [DONE]"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code", "total_timeout"),
    [
        (
            RuntimeResponse(
                output_stream(),
                trailing_error=httpx.ReadTimeout("stalled read"),
            ),
            "provider_workload_read_timeout",
            1.0,
        ),
        (
            RuntimeResponse(output_stream(), stall_after_chunks=True),
            "provider_workload_total_timeout",
            0.01,
        ),
    ],
)
async def test_managed_chat_audio_output_times_out_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: RuntimeResponse,
    expected_code: str,
    total_timeout: float,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_TOTAL_TIMEOUT_SECONDS",
        total_timeout,
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            response,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert expected_code in result.text
    assert "private-output" not in result.text
    assert base64.b64encode(WAV_BYTES).decode("ascii") not in result.text
    assert result.text.count("event: message_end") == 0
    assert result.text.count("event: route_receipt") == 1
    assert response.closed is True
    assert response.client_closed is True
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "uncertain"
    assert calls[0]["error_code"] == expected_code


@pytest.mark.asyncio
async def test_managed_chat_audio_consumer_deadline_is_not_client_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    original_feed = ManagedMultimodalChatStreamEvidence.feed

    def slow_feed(
        evidence: ManagedMultimodalChatStreamEvidence,
        line: str,
    ) -> None:
        original_feed(evidence, line)
        time.sleep(0.02)

    monkeypatch.setattr(ManagedMultimodalChatStreamEvidence, "feed", slow_feed)
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_TOTAL_TIMEOUT_SECONDS",
        0.01,
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(output_stream()),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "provider_workload_total_timeout" in result.text
    assert "provider_chat_client_cancelled" not in result.text
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count('"error"') == 1
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "uncertain"
    assert calls[0]["error_code"] == "provider_workload_total_timeout"


@pytest.mark.asyncio
async def test_managed_chat_audio_rejects_buffered_eof_after_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowBufferedEofResponse(RuntimeResponse):
        async def aiter_bytes(self):
            self.bytes_iterator_used = True
            time.sleep(0.02)
            if False:
                yield b""

    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    upstream = SlowBufferedEofResponse([])
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_TOTAL_TIMEOUT_SECONDS",
        0.01,
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "provider_workload_total_timeout" in result.text
    assert "provider_chat_client_cancelled" not in result.text
    assert upstream.closed is True
    assert upstream.client_closed is True
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "uncertain"
    assert calls[0]["error_code"] == "provider_workload_total_timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_id", "shape"),
    [
        ("chat_audio_input", "chat_audio_input"),
        ("chat_audio_output", "chat_audio_output"),
    ],
)
async def test_managed_chat_audio_read_error_delivers_receipt_before_one_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    entry_id: str,
    shape: str,
) -> None:
    original_service = get_model_router_service()
    service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id=entry_id,
        shape=shape,
    )
    if shape == "chat_audio_input":
        attachment = store.create(
            kind="audio",
            filename="synthetic.wav",
            content_type="audio/wav",
            content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
        )
        payload: dict[str, object] = {
            "model_id": "provider/audio-r8d",
            "gateway": "default",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Summarize."},
                        {
                            "type": "input_audio",
                            "attachment_id": attachment.attachment_id,
                        },
                    ],
                }
            ],
        }
        chunks = [
            'data: {"model":"provider/audio-r8d","choices":[{"index":0,'
            '"delta":{"content":"private-input"},"finish_reason":null}]}\n\n'
        ]
        private_marker = "private-input"
    else:
        payload = output_payload()
        chunks = output_stream()[:1]
        private_marker = "private-output"

    caplog.set_level("DEBUG")
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            payload,
            RuntimeResponse(
                chunks,
                trailing_error=httpx.ReadError(
                    "private-managed-audio-read-error"
                ),
            ),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert private_marker not in result.text
    assert "private-managed-audio-read-error" not in caplog.text
    assert result.text.count('data: {"error":') == 1
    assert result.text.count("event: route_receipt") == 1
    assert result.text.index("event: route_receipt") < result.text.index(
        'data: {"error":'
    )
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "uncertain"
    assert calls[0]["error_code"] == "provider_chat_stream_interrupted"


@pytest.mark.asyncio
async def test_managed_chat_audio_output_rejects_mp3_before_provider_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(audio_format="mp3"),
            RuntimeResponse(output_stream(audio_bytes=MP3_BYTES)),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 422
    assert result.json()["code"] == (
        "provider_multimodal_audio_parameter_not_certified"
    )
    assert sent == []
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert bool(calls[0]["dispatched"]) is False


@pytest.mark.asyncio
async def test_managed_chat_audio_rejects_mixed_input_and_output_before_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="synthetic.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload: dict[str, object] = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
        "response_audio": {
            "enabled": True,
            "voice": "alloy",
            "format": "mp3",
        },
    }
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            payload,
            RuntimeResponse(output_stream()),
        )
        assert result.status_code == 422
        assert result.json()["code"] == (
            "provider_multimodal_mixed_shape_unsupported"
        )
        assert sent == []
        assert service.repository.list_workload_receipts("local")["calls"] == []
        claimed = store.claim(
            attachment.attachment_id,
            expected_kind="audio",
        )
        assert claimed.attachment_id == attachment.attachment_id
        store.release_for_retry(attachment.attachment_id)
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)


@pytest.mark.asyncio
async def test_active_chat_audio_policy_uses_legacy_when_feature_flag_is_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setenv("MODEL_CONTROL_CHAT_AUDIO_ENABLED", "false")
    target = OpenRouterTarget(
        base_url="https://legacy-audio.example/v1",
        api_key="legacy-audio-secret",
        connection_id=None,
        cache_key="environment:legacy-audio",
    )

    class LegacyAudioCatalog:
        async def get_catalog(self) -> SimpleNamespace:
            return SimpleNamespace(
                status="online",
                profiles=[
                    SimpleNamespace(
                        model_id="provider/audio-r8d",
                        interaction_status="ready",
                        chat_modes=["native_streaming_audio_output"],
                        output_formats=["mp3"],
                        voices=["alloy"],
                    )
                ],
            )

        @staticmethod
        def resolve_target() -> OpenRouterTarget:
            return target

        @staticmethod
        def chat_completions_url(_target: OpenRouterTarget) -> str:
            return "https://legacy-audio.example/v1/chat/completions"

    async def skip_static_catalog_validation(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        main_module,
        "get_audio_catalog_service",
        lambda: LegacyAudioCatalog(),
    )
    monkeypatch.setattr(
        main_module,
        "validate_multimodal_content",
        skip_static_catalog_validation,
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(audio_format="mp3"),
            RuntimeResponse(output_stream(audio_bytes=MP3_BYTES)),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert sent[0]["url"] == (
        "https://legacy-audio.example/v1/chat/completions"
    )
    assert service.repository.list_workload_receipts("local")["calls"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_line",
    [
        "event: message_end",
        " event:route_receipt",
        "event: output_file",
        "event:error",
        "\ufeffevent: message_end",
        " \ufeff event:route_receipt",
    ],
)
async def test_managed_chat_audio_rejects_upstream_control_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_line: str,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    chunks[0] = event_line + "\n" + chunks[0]
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "event: message_end" not in result.text
    assert "event: output_file" not in result.text
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("data: [DONE]") == 1
    assert "provider_multimodal_reserved_sse_event" in result.text
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "failed"
    assert receipts["calls"][0]["error_code"] == (
        "provider_multimodal_reserved_sse_event"
    )
    assert receipts["runs"][0]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_chunks",
    [
        ["eve", "nt: output_file\n"],
        [" \ufeff ev", "ent: message_end\n"],
    ],
)
async def test_managed_chat_audio_rejects_fragmented_upstream_control_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_chunks: list[str],
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    upstream = RuntimeResponse(
        [*event_chunks[:-1], event_chunks[-1] + chunks[0], *chunks[1:]]
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "event: output_file" not in result.text
    assert "provider_multimodal_reserved_sse_event" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_managed_chat_audio_rejects_upstream_error_payload_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    chunks.insert(
        1,
        'data: {"error":{"message":"private-upstream-audio-error"}}\n\n',
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "private-upstream-audio-error" not in result.text
    assert "provider_multimodal_upstream_stream_error" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == (
        "provider_multimodal_upstream_stream_error"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_rejects_bom_prefixed_upstream_error_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream()
    chunks.insert(
        1,
        '\ufeffdata: {"error":{"message":"private-bom-audio-error"}}\n\n',
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "private-bom-audio-error" not in result.text
    assert "provider_multimodal_upstream_stream_error" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == (
        "provider_multimodal_upstream_stream_error"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_closes_http_error_without_reading_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnboundedErrorResponse(RuntimeResponse):
        def __init__(self) -> None:
            super().__init__([])
            self.status_code = 500
            self.read_attempted = False

        async def aread(self) -> bytes:
            self.read_attempted = True
            raise AssertionError("managed error body must not be buffered")

    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    upstream = UnboundedErrorResponse()
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 500
    assert len(sent) == 1
    assert upstream.read_attempted is False
    assert upstream.closed is True
    assert upstream.client_closed is True
    assert "provider_workload_http_5xx" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == "provider_workload_http_5xx"


@pytest.mark.asyncio
async def test_managed_chat_audio_output_withholds_bytes_when_audit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )

    def fail_delivery_audit(
        _self: ProviderWorkloadCallService,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise RuntimeError("private-audit-backend-detail")

    monkeypatch.setattr(
        ProviderWorkloadCallService,
        "mark_delivery_pending",
        fail_delivery_audit,
    )
    upstream = RuntimeResponse(output_stream())
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "private-audit-backend-detail" not in result.text
    assert "event: output_file" not in result.text
    assert "event: message_end" not in result.text
    assert "provider_workload_audit_unavailable" in result.text
    assert upstream.closed is True
    assert upstream.client_closed is True


@pytest.mark.asyncio
async def test_managed_chat_audio_output_caps_unterminated_upstream_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_STREAM_BYTES",
        128,
    )
    upstream = RuntimeResponse(["data: " + "x" * 256])
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "provider_multimodal_stream_too_large" in result.text
    assert "event: message_end" not in result.text
    assert upstream.closed is True
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert len(calls) == 1
    assert calls[0]["error_code"] == "provider_multimodal_stream_too_large"


@pytest.mark.asyncio
async def test_managed_chat_audio_output_caps_one_sse_event_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_STREAM_BYTES",
        1024,
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_EVENT_BYTES",
        128,
    )
    upstream = RuntimeResponse(["data: " + "x" * 256])
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "provider_multimodal_sse_event_too_large" in result.text
    assert upstream.bytes_iterator_used is True
    assert upstream.text_iterator_used is False
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["error_code"] == (
        "provider_multimodal_sse_event_too_large"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
@pytest.mark.parametrize("extra_bytes", [0, 1])
async def test_managed_chat_audio_event_boundary_matches_shared_framer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    line_ending: str,
    extra_bytes: int,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    data_line = output_stream()[0].rstrip("\r\n")
    comment = ":" + "x" * (31 + extra_bytes)
    event_limit = len((":" + "x" * 31 + line_ending + data_line).encode())
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_EVENT_BYTES",
        event_limit,
    )
    chunks = [
        comment + line_ending + data_line + line_ending + line_ending,
        *output_stream()[1:],
    ]
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    if extra_bytes == 0:
        assert "provider_multimodal_sse_event_too_large" not in result.text
        assert result.text.count("event: message_end") == 1
    else:
        assert "provider_multimodal_sse_event_too_large" in result.text
        assert result.text.count("event: message_end") == 0


@pytest.mark.asyncio
async def test_managed_chat_audio_event_limit_ignores_whitespace_field_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_STREAM_BYTES",
        1024,
    )
    monkeypatch.setattr(
        main_module,
        "MANAGED_CHAT_AUDIO_MAX_EVENT_BYTES",
        128,
    )
    upstream = RuntimeResponse(
        ["data: " + "x" * 72 + "\n \ndata: " + "y" * 72 + "\n\n"]
    )
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            upstream,
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "provider_multimodal_sse_event_too_large" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["error_code"] == (
        "provider_multimodal_sse_event_too_large"
    )


@pytest.mark.asyncio
async def test_managed_chat_audio_output_closes_resources_when_replay_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    sent: list[dict[str, Any]] = []
    upstream = RuntimeResponse(output_stream())
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 55123),
            "server": ("testserver", 80),
        }
    )
    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(output_payload()),
            request,
        )
        iterator = response.body_iterator
        first = await anext(iterator)
        assert b"private-output" in first
        await iterator.aclose()
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert upstream.closed is True
    assert upstream.client_closed is True
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "cancelled"
    assert receipts["calls"][0]["error_code"] == (
        "provider_chat_client_cancelled"
    )
    assert receipts["runs"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_chat_audio_cancel_while_waiting_upstream_is_not_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    sent: list[dict[str, Any]] = []
    upstream = RuntimeResponse([], stall_after_chunks=True)
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 55124),
        "server": ("testserver", 80),
    }
    disconnect = asyncio.Event()
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, str]:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    async def disconnect_after_dispatch() -> None:
        for _ in range(100):
            if sent:
                disconnect.set()
                return
            await asyncio.sleep(0)
        raise AssertionError("managed audio POST was not dispatched")

    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(output_payload()),
            Request(scope),
        )
        await asyncio.gather(
            response(scope, receive, send),
            disconnect_after_dispatch(),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert upstream.closed is True
    assert upstream.client_closed is True
    body = b"".join(message.get("body", b"") for message in messages)
    assert b"event: message_end" not in body
    assert b"data: [DONE]" not in body
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "cancelled"
    assert receipts["calls"][0]["error_code"] == (
        "provider_chat_client_cancelled"
    )
    assert receipts["runs"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_chat_audio_asgi_23_disconnect_closes_and_cancels_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="disconnect.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
    }
    sent: list[dict[str, Any]] = []
    upstream = RuntimeResponse(
        [
            'data: {"model":"provider/audio-r8d","choices":[{"delta":{"content":"Okay"},"finish_reason":null}]}\n\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            "data: [DONE]\n\n",
        ]
    )
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 55125),
        "server": ("testserver", 80),
    }
    disconnect = asyncio.Event()
    blocked_send = asyncio.Event()
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, str]:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)
        if message.get("type") == "http.response.body" and message.get("body"):
            disconnect.set()
            await blocked_send.wait()

    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(payload),
            Request(scope),
        )
        await response(scope, receive, send)
        claimed = store.claim(attachment.attachment_id, expected_kind="audio")
        assert claimed.attachment_id == attachment.attachment_id
        store.release_for_retry(attachment.attachment_id)
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert any(message.get("body") for message in messages)
    assert upstream.closed is True
    assert upstream.client_closed is True
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "cancelled"
    assert receipts["calls"][0]["error_code"] == (
        "provider_chat_client_cancelled"
    )
    assert receipts["runs"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_chat_audio_asgi_24_send_failure_cancels_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    sent: list[dict[str, Any]] = []
    upstream = RuntimeResponse(output_stream())
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 55126),
        "server": ("testserver", 80),
    }
    attempted_bodies: list[bytes] = []

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        body = message.get("body")
        if isinstance(body, bytes) and body:
            attempted_bodies.append(body)
            raise OSError("synthetic-client-disconnect")

    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(output_payload()),
            Request(scope),
        )
        with pytest.raises(ClientDisconnect):
            await response(scope, receive, send)
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert len(attempted_bodies) == 1
    attempted = attempted_bodies[0]
    assert b"event: route_receipt" in attempted
    assert b"event: message_end" in attempted
    assert b"data: [DONE]" in attempted
    assert upstream.closed is True
    assert upstream.client_closed is True
    receipts = service.repository.list_workload_receipts("local")
    assert receipts["calls"][0]["status"] == "cancelled"
    assert receipts["calls"][0]["error_code"] == (
        "provider_chat_client_cancelled"
    )
    assert receipts["runs"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_managed_chat_audio_cleanup_closes_client_when_inner_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCloseRuntimeResponse(RuntimeResponse):
        def __init__(self, chunks: list[str]) -> None:
            super().__init__(chunks)
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            self.closed = True
            raise RuntimeError("synthetic-close")

    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    sent: list[dict[str, Any]] = []
    upstream = FailingCloseRuntimeResponse(output_stream())
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(
        main_module.httpx,
        "AsyncClient",
        lambda **_kwargs: RuntimeClient(upstream, sent),
    )
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat",
            "raw_path": b"/api/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 55124),
            "server": ("testserver", 80),
        }
    )
    try:
        response = await main_module.chat(
            main_module.ChatRequest.model_validate(output_payload()),
            request,
        )
        iterator = response.body_iterator
        first = await anext(iterator)
        assert b"private-output" in first
        with pytest.raises(RuntimeError, match="synthetic-close"):
            await iterator.aclose()
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert upstream.close_calls == 1
    assert upstream.closed is True
    assert upstream.client_closed is True


@pytest.mark.asyncio
async def test_managed_chat_audio_output_accepts_independently_padded_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks: list[str] = []
    audio_chunks = [PCM16_BYTES[:15], PCM16_BYTES[15:47], PCM16_BYTES[47:]]
    for index, audio_chunk in enumerate(audio_chunks):
        event: dict[str, object] = {
            "choices": [
                {
                    "delta": {
                        "audio": {
                            "data": base64.b64encode(audio_chunk).decode(
                                "ascii"
                            )
                        }
                    },
                    "finish_reason": (
                        "stop" if index == len(audio_chunks) - 1 else None
                    ),
                }
            ]
        }
        if index == 0:
            event["model"] = "provider/audio-r8d"
        chunks.append(f"data: {json.dumps(event)}\n\n")
    chunks.append("data: [DONE]\n\n")
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("event: message_end") == 1
    assert result.text.count("data: [DONE]") == 1


@pytest.mark.parametrize("budget_kind", ["encoded_audio", "delivery_text"])
def test_managed_chat_audio_output_evidence_enforces_shared_payload_budgets(
    monkeypatch: pytest.MonkeyPatch,
    budget_kind: str,
) -> None:
    encoded = base64.b64encode(PCM16_BYTES).decode("ascii")
    delta: dict[str, object] = {"audio": {"data": encoded}}
    if budget_kind == "encoded_audio":
        monkeypatch.setattr(
            multimodal_gateway_module,
            "CHAT_AUDIO_MAX_ENCODED_CHARS",
            len(encoded) - 1,
        )
    else:
        delta["content"] = "x" * 9
        monkeypatch.setattr(
            multimodal_gateway_module,
            "CHAT_AUDIO_MAX_DELIVERY_TEXT_CHARS",
            8,
        )
    evidence = ManagedMultimodalChatStreamEvidence(
        execution_shape="chat_audio_output",
        expected_model="provider/audio-r8d",
        started_at=0.0,
        expected_audio_format="pcm16",
    )
    evidence.feed(
        "data: "
        + json.dumps(
            {
                "id": "generation-r8d",
                "model": "provider/audio-r8d",
                "choices": [{"delta": delta, "finish_reason": None}],
            }
        )
        + "\n\n"
    )
    evidence.feed(
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    )
    evidence.feed("data: [DONE]\n\n")

    status, result_class, code, *_rest = evidence.finish(
        transport_completed=True
    )

    assert status == "failed"
    assert result_class == "hard_failure"
    assert code == "provider_multimodal_invalid_sse"


@pytest.mark.asyncio
async def test_managed_chat_audio_output_accepts_continuous_base64_splits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    encoded = base64.b64encode(PCM16_BYTES).decode("ascii")
    parts = [encoded[:503], encoded[503:1001], encoded[1001:]]
    chunks: list[str] = []
    for index, part in enumerate(parts):
        event: dict[str, object] = {
            "choices": [
                {
                    "delta": {"audio": {"data": part}},
                    "finish_reason": "stop" if index == len(parts) - 1 else None,
                }
            ]
        }
        if index == 0:
            event["model"] = "provider/audio-r8d"
        chunks.append(f"data: {json.dumps(event)}\n\n")
    chunks.append("data: [DONE]\n\n")
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("event: message_end") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "expected_code"),
    [
        (output_stream(model_id=None), "provider_multimodal_actual_model_unverified"),
        (
            output_stream(second_model="provider/wrong-audio"),
            "provider_workload_model_mismatch",
        ),
        (
            output_stream(encoded_audio="bm90LW1wMw=="),
            "provider_multimodal_audio_stream_invalid",
        ),
        (
            output_stream(
                encoded_audio=base64.b64encode(WAV_BYTES[:-1]).decode("ascii")
            ),
            "provider_multimodal_audio_stream_invalid",
        ),
    ],
)
async def test_managed_chat_audio_output_discards_unverified_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chunks: list[str],
    expected_code: str,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    monkeypatch.setenv("FILE_OUTPUT_ASSETS_ENABLED", "true")
    payload = output_payload()
    payload["file_scope_id"] = "chat-r8d-audio"
    payload["output_context_id"] = "assistant-r8d-audio"
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            payload,
            RuntimeResponse(chunks),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert result.status_code == 200
    assert len(sent) == 1
    assert "private-output" not in result.text
    assert "event: output_file" not in result.text
    assert "event: message_end" not in result.text
    assert result.text.count('"error"') == 1
    assert result.text.count("event: route_receipt") == 1
    assert result.text.count("data: [DONE]") == 1
    assert expected_code in result.text
    stored = service.repository.list_workload_receipts("local")
    assert len(stored["calls"]) == 1
    assert stored["calls"][0]["error_code"] == expected_code


@pytest.mark.asyncio
async def test_known_model_mismatch_wins_over_trailing_transport_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    service, _store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_output",
        shape="chat_audio_output",
    )
    chunks = output_stream(second_model="provider/wrong-audio")[:-1]
    try:
        result, sent = await request_with_runtime(
            monkeypatch,
            output_payload(),
            RuntimeResponse(
                chunks,
                trailing_error=httpx.ReadError("closed after mismatch"),
            ),
        )
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)

    assert len(sent) == 1
    assert "private-output" not in result.text
    assert result.text.count('"error"') == 1
    assert result.text.count("event: route_receipt") == 1
    assert "provider_workload_model_mismatch" in result.text
    calls = service.repository.list_workload_receipts("local")["calls"]
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == "provider_workload_model_mismatch"


@pytest.mark.asyncio
async def test_managed_chat_audio_input_consumes_only_after_verified_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_service = get_model_router_service()
    _service, store = await configured_service(
        tmp_path,
        monkeypatch,
        entry_id="chat_audio_input",
        shape="chat_audio_input",
    )
    attachment = store.create(
        kind="audio",
        filename="synthetic.wav",
        content_type="audio/wav",
        content=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 24,
    )
    payload: dict[str, object] = {
        "model_id": "provider/audio-r8d",
        "gateway": "default",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize."},
                    {
                        "type": "input_audio",
                        "attachment_id": attachment.attachment_id,
                    },
                ],
            }
        ],
    }
    chunks = [
        'data: {"model":"provider/audio-r8d","choices":[{"delta":{"content":"verified-text"},"finish_reason":null}]}\n\n',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]
    try:
        result, sent = await request_with_runtime(
            monkeypatch, payload, RuntimeResponse(chunks)
        )
        assert result.status_code == 200
        assert len(sent) == 1
        assert "verified-text" in result.text
        with pytest.raises(MultimodalServiceError) as consumed:
            store.claim(attachment.attachment_id)
        assert consumed.value.code == "attachment_not_found"
    finally:
        configure_chat_attachment_store(None)
        configure_model_router(original_service)
