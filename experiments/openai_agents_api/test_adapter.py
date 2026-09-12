"""Offline contract/failure tests. No provider credentials or network required."""
from __future__ import annotations

import asyncio
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from experiments.openai_agents_api import adapter as adapter_module
from experiments.openai_agents_api.adapter import AgentsAPIAdapter, AgentsAPIError, recorded_usage
from experiments.openai_agents_api.smoke import (
    FIRST_INPUT, MARKER, SECOND_INPUT, final_history, receipt_json, run_probe,
)

KEY = "test-credential-not-a-real-key"
SID = "sess_test"
USAGE = {
    "input_tokens": 12, "output_tokens": 3, "total_tokens": 15,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens_details": {"reasoning_tokens": 1},
}


@pytest.mark.asyncio
async def test_public_text_progress_replaces_deltas_and_never_exposes_other_phases():
    api = FakeAPI()
    private = turn_events("turn_1", "hidden commentary must never reach UI")[:-1]
    private[1]["item"]["phase"] = "analysis"
    private[1]["item"]["id"] = "private_msg"
    for item in private[2:]:
        item["item_id"] = "private_msg"
    for index, item in enumerate(private):
        item["event_id"] = "private_" + str(index)
    api.first[1:1] = private
    updates = []
    async with AgentsAPIAdapter(KEY, transport=httpx.MockTransport(api),
                                on_text=lambda tid, text: updates.append((tid, text))) as adapter:
        result = await adapter.create_session("gpt-6-astra", FIRST_INPUT)
    assert result.text == "READY"
    assert ("turn_1", "temporary") in updates
    assert updates[-1] == ("turn_1", "READY")
    assert all("hidden" not in text for _, text in updates)


@pytest.mark.asyncio
async def test_unknown_phase_waits_for_terminal_instead_of_streaming():
    api = FakeAPI()
    api.first[2]["item"].pop("phase")
    updates = []
    async with AgentsAPIAdapter(KEY, transport=httpx.MockTransport(api),
                                on_text=lambda tid, text: updates.append(text)) as adapter:
        result = await adapter.create_session("gpt-6-astra", FIRST_INPUT)
    assert result.text == "READY"
    assert not any(updates)


def event(kind: str, eid: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "event_id": eid, "session_id": SID, **fields}


def turn(tid: str, status: str = "completed") -> dict[str, Any]:
    return {
        "id": tid, "session_id": SID, "subagent_id": None,
        "status": status, "usage": copy.deepcopy(USAGE),
    }


def message(tid: str, text: str, phase: str | None = "final_answer") -> dict[str, Any]:
    return {
        "type": "message", "id": "msg_" + tid, "turn_id": tid,
        "role": "assistant", "status": "completed", "phase": phase,
        "content": [{"type": "output_text", "text": text}],
    }


def turn_events(tid: str, text: str, status: str = "completed") -> list[dict[str, Any]]:
    return [
        event("agent.session.turn.created", tid + "_created", turn_id=tid, turn=turn(tid, "queued")),
        event("agent.session.turn.item.added", tid + "_item", turn_id=tid, item=message(tid, "")),
        event("agent.session.turn.output_text.delta", tid + "_delta", turn_id=tid,
              item_id="msg_" + tid, output_index=0, content_index=0, delta="temporary"),
        event("agent.session.turn.output_text.done", tid + "_done", turn_id=tid,
              item_id="msg_" + tid, output_index=0, content_index=0, text=text),
        event("agent.session.turn." + status, tid + "_terminal", turn_id=tid, turn=turn(tid, status)),
    ]


def frames(events: list[dict[str, Any]], *, multiline: bool = False) -> bytes:
    output = ": heartbeat\r\n\r\nretry: 1\r\n\r\n"
    for item in events:
        data = json.dumps(item, ensure_ascii=False, indent=1 if multiline else None)
        output += "".join("data: " + line + "\r\n" for line in data.splitlines()) + "\r\n"
    return output.encode("utf-8")


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, data: bytes, *, stall: bool = False):
        self.data, self.stall, self.closed = data, stall, False

    async def __aiter__(self):
        for offset in range(0, len(self.data), 7):
            yield self.data[offset:offset + 7]
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


class FakeAPI:
    """Documented wire fixtures, including empty HTTP 202 input acknowledgments."""

    def __init__(self):
        self.calls: list[tuple[str, str, Any]] = []
        self.streams: list[ByteStream] = []
        self.first = [
            event("agent.session.created", "session_created", session={"id": SID}),
            *turn_events("turn_1", "READY"),
        ]
        self.second = turn_events("turn_2", MARKER)
        self.first_stall = False
        self.first_bytes: bytes | None = None
        self.deleted = False
        self.cancelled = False
        self.overrides: dict[tuple[str, str], Any] = {}
        self.items = [message("turn_1", "READY"), message("turn_2", MARKER)]

    def stream(self, data: bytes, *, stall: bool = False, status: int = 200) -> httpx.Response:
        stream = ByteStream(data, stall=stall)
        self.streams.append(stream)
        return httpx.Response(status, headers={
            "content-type": "text/event-stream; charset=utf-8", "x-request-id": "req_test",
        }, stream=stream)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openai.com"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert request.headers["openai-beta"] == "agents=v1"
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        override = self.overrides.get((request.method, path))
        if override is not None:
            if isinstance(override, Exception):
                raise override
            return override(request) if callable(override) else override
        if request.method == "POST" and path == "/v1/agents/sessions":
            assert body == {
                "agent": {
                    "model": "gpt-6-astra", "tools": [], "multi_agent": {"enabled": False},
                    "instructions": "Follow the user's text-only instructions precisely.",
                },
                "environment": {"type": "none"}, "input": FIRST_INPUT, "stream": True,
            }
            data = self.first_bytes if self.first_bytes is not None else frames(self.first, multiline=True)
            return self.stream(data, stall=self.first_stall, status=201)
        if path == f"/v1/agents/sessions/{SID}/events":
            if request.method == "GET":
                return self.stream(frames(self.second))
            assert request.method == "POST"
            if body == {"events": [{"type": "agent.session.input.cancel"}]}:
                self.cancelled = True
            else:
                assert len(self.streams) == 2 and not self.streams[-1].closed
                assert body == {"events": [{
                    "type": "agent.session.input.message",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": SECOND_INPUT}]}],
                }]}
            return httpx.Response(202)  # A successful submission has no JSON body.
        if path == f"/v1/agents/sessions/{SID}":
            if request.method == "DELETE":
                self.deleted = True
                return httpx.Response(200, json={"id": SID, "deleted": True, "object": "agent.session.deleted"})
            if self.deleted:
                return httpx.Response(404, json={"error": {"message": "not found"}})
            return httpx.Response(200, json={
                "id": SID, "status": "idle", "required_actions": [], "usage": USAGE,
                "environment": {"type": "none"},
                "agent": {"model": "gpt-6-astra", "tools": [], "multi_agent": {"enabled": False}},
            })
        if path.startswith(f"/v1/agents/sessions/{SID}/turns/"):
            return httpx.Response(200, json=turn(path.rsplit("/", 1)[1], "cancelled" if self.cancelled else "completed"))
        if path == f"/v1/agents/sessions/{SID}/items":
            assert request.url.params["order"] == "asc"
            return httpx.Response(200, json={"data": self.items, "has_more": False, "last_id": None})
        raise AssertionError("Unexpected request in offline fixture")


async def probe(fake: FakeAPI, **kwargs: Any) -> dict[str, Any]:
    async with AgentsAPIAdapter(KEY, transport=httpx.MockTransport(fake)) as client:
        return await run_probe(client, "gpt-6-astra", evidence="offline_mock", **kwargs)


@pytest.mark.asyncio
async def test_two_turn_lifecycle_and_cleanup():
    fake = FakeAPI()
    report = await probe(fake)
    assert report["status"] == "passed"
    assert report["evidence"] == "offline_mock"
    assert report["checks"] == {"first_turn": "passed", "second_turn": "passed", "history": "passed"}
    assert report["turn_ids"] == ["turn_1", "turn_2"]
    assert [result["text"] for result in report["turns"]] == ["READY", MARKER]
    assert report["session"]["usage"] == USAGE
    assert report["cleanup"]["deletion_confirmed"] is True
    assert report["cleanup"]["api_absent"] is True
    assert report["cleanup"]["physical_cleanup"] == "unverified"
    assert fake.cancelled is False
    assert all(stream.closed for stream in fake.streams)
    paths = [(method, path) for method, path, _ in fake.calls]
    assert paths.index(("GET", f"/v1/agents/sessions/{SID}/events")) < paths.index(
        ("POST", f"/v1/agents/sessions/{SID}/events")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,category", [
    (400, "http_error"), (401, "authentication_error"), (403, "permission_denied"),
    (404, "not_found"), (429, "rate_limited"), (503, "http_error"),
])
async def test_create_rejections_do_not_retry_or_leak(status, category):
    fake = FakeAPI()
    fake.overrides[("POST", "/v1/agents/sessions")] = httpx.Response(
        status, json={"error": {"message": KEY + " private provider detail"}}
    )
    report = await probe(fake)
    assert report["status"] == "failed"
    assert report["error"]["category"] == category
    assert len(fake.calls) == 1
    assert "private provider detail" not in json.dumps(report)
    assert KEY not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_turn_failure_is_not_success_and_still_deletes(status):
    fake = FakeAPI()
    fake.first = [fake.first[0], *turn_events("turn_1", "READY", status)]
    report = await probe(fake)
    assert report["error"]["category"] == "turn_" + status
    assert report["checks"]["second_turn"] == "not_run"
    assert report["cleanup"]["status"] == "passed"
    assert fake.cancelled is False
    assert report["inputs_submitted"] == 1


@pytest.mark.asyncio
async def test_idle_and_eof_only_recover_state_without_resending():
    fake = FakeAPI()
    fake.first = fake.first[:2] + [event("agent.session.idle", "idle")]
    report = await probe(fake)
    assert report["error"]["category"] == "stream_closed_before_terminal"
    assert report["status"] == "failed"
    assert report["recovered_outputs"][0]["text"] == "READY"
    assert fake.cancelled and fake.deleted
    assert report["inputs_submitted"] == 1
    assert len([call for call in fake.calls if call[:2] == ("POST", "/v1/agents/sessions")]) == 1


@pytest.mark.asyncio
async def test_deadline_closes_stream_and_cancels_with_separate_cleanup_budget():
    fake = FakeAPI()
    fake.first = fake.first[:2]
    fake.first_stall = True
    report = await probe(fake, call_seconds=0.03, cleanup_seconds=1)
    assert report["error"]["category"] == "deadline_exceeded"
    assert report["cleanup"]["observed_turn_status"] == "cancelled"
    assert report["cleanup"]["status"] == "passed"
    assert fake.cancelled and all(stream.closed for stream in fake.streams)


@pytest.mark.asyncio
async def test_ambiguous_followup_timeout_never_replays_input():
    fake = FakeAPI()
    fake.overrides[("POST", f"/v1/agents/sessions/{SID}/events")] = httpx.ReadTimeout("private " + KEY)
    report = await probe(fake)
    assert report["error"]["category"] == "transport_timeout"
    messages = [
        body for method, path, body in fake.calls
        if method == "POST" and path.endswith("/events")
        and body["events"][0]["type"] == "agent.session.input.message"
    ]
    assert len(messages) == 1
    assert fake.deleted
    assert KEY not in json.dumps(report)


@pytest.mark.asyncio
async def test_unknown_session_after_disconnect_does_not_enumerate_or_delete():
    fake = FakeAPI()
    fake.first = []
    report = await probe(fake)
    assert report["session_id"] is None
    assert report["cleanup"]["remote_state"] == "unknown"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_exact_output_mismatch_stops_before_second_turn():
    fake = FakeAPI()
    fake.first = [fake.first[0], *turn_events("turn_1", "READY\n")]
    report = await probe(fake)
    assert report["error"]["category"] == "first_output_mismatch"
    assert report["inputs_submitted"] == 1
    assert fake.deleted


@pytest.mark.asyncio
async def test_saved_history_must_match_both_completed_turns():
    fake = FakeAPI()
    fake.items[1]["content"][0]["text"] = "WRONG"
    report = await probe(fake)
    assert report["error"]["category"] == "saved_history_mismatch"
    assert report["checks"]["history"] == "failed"
    assert fake.deleted


@pytest.mark.asyncio
async def test_duplicate_events_and_missing_deltas():
    fake = FakeAPI()
    done = fake.first[-2]
    fake.first = [fake.first[0], fake.first[1], fake.first[2], done, done, fake.first[-1]]
    report = await probe(fake)
    assert report["status"] == "passed"
    assert report["turns"][0]["text"] == "READY"


@pytest.mark.asyncio
async def test_utf8_multiline_sse_and_whole_text_replacement():
    fake = FakeAPI()
    fake.first = [fake.first[0], *turn_events("turn_1", "中文")]
    async with AgentsAPIAdapter(KEY, transport=httpx.MockTransport(fake)) as client:
        result = await client.create_session("gpt-6-astra", FIRST_INPUT)
    assert result.text == "中文"
    assert fake.streams[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,category", [
    (b"data: {bad}\n\n", "invalid_event_json"),
    (b'data: {"type":"incomplete"}', "truncated_event"),
    (b"data: [DONE]\n\n", "stream_closed_before_terminal"),
])
async def test_malformed_stream_is_not_accepted(extra, category):
    fake = FakeAPI()
    fake.first_bytes = frames(fake.first[:1]) + extra
    report = await probe(fake)
    assert report["error"]["category"] == category
    assert fake.deleted


@pytest.mark.asyncio
async def test_required_action_does_not_execute_tools():
    fake = FakeAPI()
    fake.first = fake.first[:2] + [event("agent.session.requires_action", "action", turn_id="turn_1")]
    report = await probe(fake)
    assert report["error"]["category"] == "unexpected_required_action"
    assert report["inputs_submitted"] == 1
    assert fake.deleted


@pytest.mark.asyncio
async def test_terminal_identity_mismatch_does_not_pass():
    fake = FakeAPI()
    fake.first[-1]["turn"]["session_id"] = "sess_another"
    report = await probe(fake)
    assert report["error"]["category"] == "invalid_terminal_event"
    assert all("sess_another" not in path for _, path, _ in fake.calls)


@pytest.mark.asyncio
async def test_third_input_and_repeated_create_rejected_locally():
    fake = FakeAPI()
    async with AgentsAPIAdapter(KEY, transport=httpx.MockTransport(fake)) as client:
        await client.create_session("gpt-6-astra", FIRST_INPUT)
        await client.continue_session(SECOND_INPUT)
        before = len(fake.calls)
        with pytest.raises(AgentsAPIError, match="input_limit_exceeded"):
            await client.continue_session("another")
        with pytest.raises(AgentsAPIError, match="create_already_attempted"):
            await client.create_session("gpt-6-astra", FIRST_INPUT)
        assert len(fake.calls) == before


@pytest.mark.asyncio
async def test_pagination_keeps_final_outputs_and_excludes_reasoning():
    fake = FakeAPI()
    def pages(request):
        if "after" not in request.url.params:
            return httpx.Response(200, json={
                "data": [{"type": "reasoning", "id": "reason_1", "summary": [{"text": "HIDDEN"}]}],
                "has_more": True, "last_id": "reason_1",
            })
        assert request.url.params["after"] == "reason_1"
        return httpx.Response(200, json={"data": fake.items, "has_more": False})
    fake.overrides[("GET", f"/v1/agents/sessions/{SID}/items")] = pages
    report = await probe(fake)
    assert report["status"] == "passed"
    assert "HIDDEN" not in json.dumps(report)


@pytest.mark.asyncio
async def test_deletion_failure_keeps_successful_invocation_separate():
    fake = FakeAPI()
    fake.overrides[("DELETE", f"/v1/agents/sessions/{SID}")] = httpx.Response(503)
    report = await probe(fake)
    assert report["invocation_status"] == "passed"
    assert report["status"] == "failed"
    assert report["cleanup"]["status"] == "failed"
    assert report["cleanup"]["api_absent"] is False
    assert len([call for call in fake.calls if call[0] == "DELETE"]) == 1


@pytest.mark.asyncio
async def test_bad_recovery_content_does_not_prevent_deletion():
    fake = FakeAPI()
    fake.first = fake.first[:2]
    fake.items[0]["content"] = "not a list"
    report = await probe(fake)
    assert report["recovery_error"]["category"] == "invalid_saved_message"
    assert fake.deleted


def test_usage_missing_is_unknown_and_receipt_redacts_exact_secret():
    assert recorded_usage(None) is None
    assert recorded_usage({"input_tokens": True, "debug": KEY}) is None
    assert recorded_usage(USAGE) == USAGE
    assert KEY not in receipt_json({"request_id": KEY, "nested": [KEY]}, KEY)


def test_history_excludes_commentary_and_requires_completed_messages():
    values = [
        message("turn_1", "ignore", "commentary"), message("turn_1", "READY"),
        {"type": "reasoning", "summary": [{"text": KEY}]},
    ]
    assert final_history(values, "turn_1") == "READY"
    values[1]["status"] = "incomplete"
    assert final_history(values, "turn_1") == ""


def test_cli_help_import_and_missing_opt_in_never_open_sockets():
    root = Path(__file__).resolve().parents[2]
    for argv, expected in [(["--help"], 0), ([], 2), (["--live"], 2), (["--model", "gpt-6-astra"], 2)]:
        code = (
            "import socket; "
            "socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); "
            "socket.socket.connect_ex=socket.socket.connect; "
            "from experiments.openai_agents_api.smoke import main; "
            f"raise SystemExit(main({argv!r}))"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=10)
        assert result.returncode == expected, result.stderr


@pytest.mark.asyncio
async def test_output_cap_and_hostile_identifier_do_not_expand_requests(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(adapter_module, "MAX_OUTPUT_CHARS", 4)
    report = await probe(fake)
    assert report["error"]["category"] == "output_limit_exceeded"
    assert fake.deleted
    async with AgentsAPIAdapter(KEY, transport=httpx.MockTransport(fake)) as client:
        before = len(fake.calls)
        with pytest.raises(AgentsAPIError, match="invalid_identifier"):
            await client.delete_session("../other")
        assert len(fake.calls) == before
