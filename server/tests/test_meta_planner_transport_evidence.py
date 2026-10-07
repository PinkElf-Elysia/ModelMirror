"""Offline transport-phase evidence; never dispatches to a real provider."""
import asyncio
from contextlib import nullcontext
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from server import main
from server.meta_agent.generation_evidence import GenerationEvidence
from server.meta_agent import transport_evidence as transport_module


PROMPT = json.dumps({"required_schema": {"type": "object"}, "goal": "PRIVATE_GOAL"})
BODY = json.dumps({
    "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
}).encode()


class BodyStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), *, block=False, error=None):
        self.chunks = chunks
        self.block = block
        self.error = error
        self.closed = 0
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.block:
            self.waiting.set()
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed += 1


async def collect(handler, **kwargs):
    options = {"transport": httpx.MockTransport(handler), "trust_env": False}
    options.update(kwargs.pop("client_kwargs", {}))
    return await main.collect_chat_completion_text(
        "offline-model", [main.ChatMessage(role="user", content=PROMPT)],
        gateway_url="https://offline.invalid", gateway_key="PRIVATE_KEY",
        client_kwargs_override=options, **kwargs,
    )


def receipt(evidence):
    return evidence.as_dict()["calls"][0]["transport"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_headers", "after_headers", "partial_body"])
async def test_deadline_distinguishes_header_and_body_phases(monkeypatch, phase):
    evidence = GenerationEvidence()
    stream = BodyStream([b"PRIVATE_PARTIAL_BODY"] if phase == "partial_body" else [], block=True)
    sent = []

    async def handler(request):
        sent.append(request)
        if phase == "before_headers":
            await asyncio.Event().wait()
        return httpx.Response(200, stream=stream)

    original_send = httpx.AsyncClient.send

    async def guarded_send(client, request, **kwargs):
        async with asyncio.timeout(0.03):
            return await original_send(client, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_send)
    with evidence.capture("capability_compile", PROMPT):
        with pytest.raises(TimeoutError):
            await collect(handler)
    observed = receipt(evidence)
    assert observed["outcome"] == "timeout"
    assert observed["exception_type"] == "TimeoutError"
    assert observed["response_count"] == (0 if phase == "before_headers" else 1)
    assert observed["status_code"] == (None if phase == "before_headers" else 200)
    assert observed["phase"] == ("before_response_headers" if phase == "before_headers" else "response_body")
    body = observed["body"]
    assert body["complete"] is False
    assert body["raw_bytes"] == (len(b"PRIVATE_PARTIAL_BODY") if phase == "partial_body" else 0)
    assert (body["first_byte_ms"] is not None) is (phase == "partial_body")
    assert (body["idle_ms_at_end"] is not None) is (phase == "partial_body")
    assert len(sent) == 1
    assert stream.closed == (0 if phase == "before_headers" else 1)
    assert "PRIVATE_" not in json.dumps(evidence.as_dict())


@pytest.mark.asyncio
async def test_complete_body_and_usage_are_unchanged():
    evidence = GenerationEvidence()
    stream = BodyStream([BODY[:20], BODY[20:]])
    sent, usage = [], []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, stream=stream)

    with evidence.capture("task_plan", PROMPT):
        result = await collect(handler, usage_observer=usage.append)
    assert result == '{"ok":true}'
    assert usage == [{"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}]
    observed = receipt(evidence)
    assert observed["outcome"] == "completed"
    assert observed["phase"] == "response_complete"
    assert observed["status_code"] == 200
    assert observed["body"]["complete"] is True
    assert observed["body"]["raw_bytes"] == len(BODY)
    assert observed["body"]["chunk_count"] == 2
    assert observed["headers_ms"] <= observed["body"]["first_byte_ms"] <= observed["body"]["last_byte_ms"]
    assert stream.closed == 1 and len(sent) == 1


@pytest.mark.asyncio
async def test_cancellation_is_not_retried_and_retains_partial_body():
    evidence = GenerationEvidence()
    stream = BodyStream([b"PRIVATE_PARTIAL_BODY"], block=True)
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, stream=stream)

    with evidence.capture("capability_compile", PROMPT):
        task = asyncio.create_task(collect(handler))
        await asyncio.wait_for(stream.waiting.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    observed = receipt(evidence)
    assert observed["outcome"] == "cancelled"
    assert observed["exception_type"] == "CancelledError"
    assert observed["status_code"] == 200
    assert observed["body"]["raw_bytes"] == len(b"PRIVATE_PARTIAL_BODY")
    assert observed["body"]["complete"] is False
    assert len(sent) == stream.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.RemoteProtocolError])
@pytest.mark.parametrize("fault", [False, True])
async def test_original_transport_exception_survives_observer_fault(monkeypatch, error_type, fault):
    evidence = GenerationEvidence()
    original_error = error_type("PRIVATE_ERROR_WITH_SECRET")
    stream = BodyStream([b"PRIVATE_PARTIAL_BODY"], error=original_error)

    if fault:
        def broken_clock():
            raise PermissionError("PRIVATE_OBSERVER_PATH")
        monkeypatch.setattr(transport_module, "monotonic", broken_clock)
    with evidence.capture("capability_compile", PROMPT):
        with pytest.raises(error_type) as failure:
            await collect(lambda _request: httpx.Response(200, stream=stream))
    assert failure.value is original_error
    observed = receipt(evidence)
    assert observed["outcome"] == ("timeout" if error_type is httpx.ReadTimeout else "failed")
    assert observed["status"] == ("partial" if fault else "observed")
    assert "PRIVATE_" not in json.dumps(evidence.as_dict())
    assert stream.closed == 1


@pytest.mark.asyncio
async def test_enabled_disabled_and_faulted_observers_preserve_wire_request_and_hooks(monkeypatch):
    wires, replies = [], []
    timeout = httpx.Timeout(connect=15, read=None, write=30, pool=10)
    for mode in ("on", "off", "fault"):
        evidence = GenerationEvidence()
        events = []

        async def request_hook(_request):
            events.append("request")

        async def response_hook(response):
            events.append("response")
            assert response.status_code == 200

        hooks = {"request": [request_hook], "response": [response_hook]}
        options = {"timeout": timeout, "event_hooks": hooks}

        def handler(request):
            wires.append((request.content, dict(request.headers), request.extensions["timeout"]))
            assert ("trace" in request.extensions) is (mode != "off")
            return httpx.Response(200, stream=BodyStream([BODY]))

        with monkeypatch.context() as patch:
            if mode == "fault":
                patch.setattr(transport_module.TransportEvidence, "_chunk", Mock(side_effect=ValueError("PRIVATE_FAULT")))
            with evidence.capture("task_plan", PROMPT) if mode != "off" else nullcontext():
                replies.append(await collect(handler, client_kwargs=options))
        assert hooks == {"request": [request_hook], "response": [response_hook]}
        assert options["timeout"] is timeout and options["event_hooks"] is hooks
        assert events == ["request", "response"]
        if mode != "off":
            assert receipt(evidence)["outcome"] == "completed"
            assert receipt(evidence)["status"] == ("partial" if mode == "fault" else "observed")
    assert wires[0] == wires[1] == wires[2]
    assert replies == ['{"ok":true}'] * 3
    assert wires[0][2] == {"connect": 15, "read": None, "write": 30, "pool": 10}


@pytest.mark.asyncio
async def test_safe_header_hashes_and_trace_vocabulary_are_bounded():
    evidence = GenerationEvidence()
    stream = BodyStream([BODY])

    async def handler(request):
        trace = request.extensions["trace"]
        for index in range(1000):
            await trace("http11.send_request_body.complete", {"PRIVATE_INFO": "PRIVATE_SECRET"})
            await trace(f"PRIVATE_UNKNOWN_{index}", {"request": request})
        return httpx.Response(200, headers={
            "x-request-id": "PRIVATE_REQUEST_ID", "x-openrouter-request-id": "x" * 513,
            "cf-ray": "PRIVATE_RAY", "set-cookie": "PRIVATE_COOKIE", "authorization": "PRIVATE_SECRET",
        }, stream=stream)

    with evidence.capture("task_plan", PROMPT):
        await collect(handler)
    observed = receipt(evidence)
    assert observed["request_id_hashes"] == {
        "x-request-id": hashlib.sha256(b"PRIVATE_REQUEST_ID").hexdigest(),
        "cf-ray": hashlib.sha256(b"PRIVATE_RAY").hexdigest(),
    }
    assert set(observed["trace_events"]) == {"http11.send_request_body.complete"}
    assert observed["trace_events"]["http11.send_request_body.complete"]["count"] == 1000
    assert len(json.dumps(observed)) < 3000
    assert "PRIVATE_" not in json.dumps(evidence.as_dict())


@pytest.mark.asyncio
async def test_preloaded_response_does_not_invent_first_byte_or_wire_size():
    evidence = GenerationEvidence()
    with evidence.capture("task_plan", PROMPT):
        await collect(lambda _request: httpx.Response(200, content=BODY))
    body = receipt(evidence)["body"]
    assert body == {
        "observation": "preloaded", "raw_bytes": 0, "chunk_count": 0,
        "first_byte_ms": None, "last_byte_ms": None, "idle_ms_at_end": None, "complete": True,
    }


@pytest.mark.asyncio
async def test_custom_sender_is_not_instrumented():
    evidence = GenerationEvidence()
    sender = AsyncMock(return_value=httpx.Response(200, content=BODY))

    def forbidden(_request):
        raise AssertionError("unexpected dispatch")

    with evidence.capture("task_plan", PROMPT):
        assert await collect(forbidden, response_sender=sender) == '{"ok":true}'
    sender.assert_awaited_once()
    assert "transport" not in evidence.as_dict()["calls"][0]


@pytest.mark.asyncio
async def test_receipt_callback_failure_cannot_replace_success(monkeypatch):
    from server.meta_agent.generation_evidence import GenerationEvidenceCall
    original = GenerationEvidenceCall._observe

    def observe(call, key, operation):
        if key == "transport":
            raise RuntimeError("PRIVATE_RECEIPT_FAILURE")
        return original(call, key, operation)

    monkeypatch.setattr(GenerationEvidenceCall, "_observe", observe)
    evidence = GenerationEvidence()
    with evidence.capture("task_plan", PROMPT):
        assert await collect(lambda _request: httpx.Response(200, stream=BodyStream([BODY]))) == '{"ok":true}'
    assert "transport" not in evidence.as_dict()["calls"][0]


@pytest.mark.asyncio
async def test_concurrent_generation_contexts_do_not_share_receipts():
    async def run(index):
        evidence = GenerationEvidence()

        async def handler(_request):
            await asyncio.sleep(0)
            return httpx.Response(200, headers={"x-request-id": str(index)}, stream=BodyStream([BODY]))

        with evidence.capture("task_plan", PROMPT):
            await collect(handler)
        return receipt(evidence)

    first, second = await asyncio.gather(run(1), run(2))
    assert first["request_id_hashes"] == {"x-request-id": hashlib.sha256(b"1").hexdigest()}
    assert second["request_id_hashes"] == {"x-request-id": hashlib.sha256(b"2").hexdigest()}
    assert first["response_count"] == second["response_count"] == 1


@pytest.mark.asyncio
async def test_loopback_httpx_trace_and_decoded_response_are_preserved():
    import gzip

    evidence = GenerationEvidence()
    encoded = gzip.compress(BODY)
    handled = asyncio.Event()

    async def handler(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:"))
            await reader.readexactly(length)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: " + str(len(encoded)).encode() + b"\r\nConnection: close\r\n\r\n" + encoded)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            handled.set()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        with evidence.capture("task_plan", PROMPT):
            result = await main.collect_chat_completion_text(
                "offline-model", [main.ChatMessage(role="user", content=PROMPT)],
                gateway_url=f"http://127.0.0.1:{port}", gateway_key="unused",
                client_kwargs_override={"trust_env": False, "timeout": 2},
            )
        await asyncio.wait_for(handled.wait(), 2)
    finally:
        server.close()
        await server.wait_closed()
    assert result == '{"ok":true}'
    observed = receipt(evidence)
    assert observed["body"]["raw_bytes"] == len(encoded)
    assert observed["body"]["complete"] is True
    assert {"connection.connect_tcp.complete", "http11.send_request_body.complete", "http11.receive_response_headers.complete", "http11.receive_response_body.complete"} <= observed["trace_events"].keys()


def test_failed_api_persists_headers_and_partial_body_without_creating_proposal(monkeypatch):
    from server.tests.test_meta_planner_v2 import _request, _snapshot
    from server.tests.test_meta_planner_write_generation_failures import _forbid_records

    _forbid_records(monkeypatch)
    monkeypatch.setattr(main, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main, "get_model_router_service", lambda: None)
    monkeypatch.setattr(main.ManagedMetaAgentGateway, "for_router", staticmethod(
        lambda _router: SimpleNamespace(routing_mode=lambda: "legacy")
    ))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://offline.invalid", "unused"))
    monkeypatch.setattr(main, "build_meta_planner_capability_snapshot", _snapshot)
    registry = SimpleNamespace(
        create_run=AsyncMock(return_value=SimpleNamespace(run_id="run_transport_test")),
        record_checkpoint=AsyncMock(), update_run=AsyncMock(),
    )
    monkeypatch.setattr(main, "run_registry", registry)
    create = Mock(side_effect=AssertionError("Proposal creation is forbidden"))
    monkeypatch.setattr(main, "authoring_service", SimpleNamespace(proposal_store=SimpleNamespace(create=create)))
    stream = BodyStream([b"PRIVATE_PARTIAL_BODY"], error=httpx.ReadTimeout("PRIVATE_ERROR"))
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, stream=stream)

    original = main.collect_chat_completion_text

    async def completion(*args, **kwargs):
        return await original(*args, **kwargs, gateway_url="https://offline.invalid", gateway_key="unused",
            client_kwargs_override={"transport": httpx.MockTransport(handler), "trust_env": False})

    monkeypatch.setattr(main, "collect_chat_completion_text", completion)
    response = TestClient(main.app, raise_server_exceptions=False).post(
        "/api/meta-agent/generate-xpert-candidate", json=_request().model_dump(mode="json"),
    )
    assert response.status_code == 500
    evidence = response.json()["generation_evidence"]
    observed = evidence["calls"][0]["transport"]
    assert observed["status_code"] == 200
    assert observed["outcome"] == "timeout"
    assert observed["body"]["raw_bytes"] == len(b"PRIVATE_PARTIAL_BODY")
    assert observed["body"]["complete"] is False
    assert registry.update_run.call_args.kwargs["metadata"]["generation_evidence"] == evidence
    assert "PRIVATE_" not in json.dumps(evidence)
    assert len(sent) == 1
    create.assert_not_called()
