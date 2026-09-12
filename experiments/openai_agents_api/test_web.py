"""Offline lifecycle tests plus an actual loopback bridge test; no provider access."""
import asyncio
import json
import threading
from uuid import uuid4

import httpx
import pytest

from experiments.openai_agents_api.adapter import AgentsAPIAdapter
from experiments.openai_agents_api.smoke import FIRST_INPUT, SECOND_INPUT
from experiments.openai_agents_api.test_adapter import FakeAPI, KEY, SID, frames
from experiments.openai_agents_api.web import Experiment, LoopThread, PRESETS, TrialError, TrialServer


def body(text=FIRST_INPUT, number=1, oid=None):
    return {"text": text, "next_turn": number, "operation_id": oid or uuid4().hex}


def trial(api, **kwargs):
    return Experiment(mode="live", key=KEY, adapter=AgentsAPIAdapter(KEY, transport=httpx.MockTransport(api)), **kwargs)


@pytest.mark.asyncio
async def test_two_turns_incremental_history_receipt_and_auto_cleanup(tmp_path):
    api = FakeAPI()
    app = trial(api, receipt_path=tmp_path / "receipt.json")
    try:
        await app.submit(body())
        await app.task
        state = await app.snapshot()
        assert state["phase"] == "awaiting_input"
        assert state["turns"][0]["output"] == "READY"
        assert state["history_status"] == "passed"
        assert state["turns"][0]["expected_text_check"] == "not_applicable"
        await app.submit(body(SECOND_INPUT, 2))
        pending = await app.snapshot()
        assert pending["invocation_status"] == "in_progress"
        assert pending["history_status"] == "not_run"
        await app.task
        state = await app.snapshot()
        assert state["phase"] == "finished" and not state["can_send"]
        assert state["status"] == "completed"
        assert state["history_status"] == "passed"
        assert state["cleanup"]["status"] == "passed"
        assert state["cleanup"]["physical_cleanup"] == "unverified"
        report = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
        assert report["session_id"] == SID and len(report["history"]) == 2
        assert report["turns"][0]["usage"]["input_tokens"] == 12
        assert KEY not in json.dumps(report) and "csrf" not in report
        with pytest.raises(TrialError):
            await app.submit(body(SECOND_INPUT, 2))
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_duplicate_concurrent_and_stale_requests_never_resubmit():
    api = FakeAPI()
    app = trial(api)
    request = body()
    try:
        await app.submit(request)
        assert (await app.snapshot())["turns"][0]["operation_id"] == request["operation_id"]
        await app.submit(request)
        with pytest.raises(TrialError):
            await app.submit(body())
        await app.task
        await app.submit(request)
        with pytest.raises(TrialError):
            await app.submit(body("changed", oid=request["operation_id"]))
        with pytest.raises(TrialError):
            await app.submit(body())
        assert len([call for call in api.calls if call[:2] == ("POST", "/v1/agents/sessions")]) == 1
    finally:
        await app.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429])
async def test_provider_error_is_safe_and_never_replayed(status):
    api = FakeAPI()
    api.overrides[("POST", "/v1/agents/sessions")] = httpx.Response(status, json={"error": KEY})
    app = trial(api)
    try:
        await app.submit(body())
        await app.task
        state = await app.snapshot()
        assert state["status"] == "failed" and not state["can_send"]
        assert state["cleanup"]["remote_state"] == "unknown"
        with pytest.raises(TrialError):
            await app.submit(body())
        assert len(api.calls) == 1 and KEY not in json.dumps(state)
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_early_disconnect_recovers_known_session_without_success_or_replay():
    api = FakeAPI()
    api.first_bytes = frames(api.first[:-1])
    app = trial(api)
    try:
        await app.submit(body())
        await app.task
        state = await app.snapshot()
        assert state["status"] == "failed"
        assert state["cleanup"]["deletion_confirmed"]
        assert app.adapter.inputs_submitted == 1
        assert state["error"]["category"] == "stream_closed_before_terminal"
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_immediate_cancel_before_dispatch_does_not_leave_job_or_session():
    api = FakeAPI()
    app = trial(api)
    await app.submit(body())
    await app.end(cancel=True)
    await app.task
    assert app.phase == "finished" and not api.calls
    assert (await app.snapshot())["cleanup"]["remote_state"] == "not_created"
    await app.close()


@pytest.mark.asyncio
async def test_active_cancel_and_timeout_cleanup_are_bounded():
    for cancel in (False, True):
        api = FakeAPI()
        api.first_bytes = frames(api.first[:-1])
        api.first_stall = True
        app = trial(api, call_seconds=.04, cleanup_seconds=.3)
        await app.submit(body())
        await asyncio.sleep(.01)
        assert app.adapter.session_id == SID
        if cancel:
            await app.end(cancel=True)
            await app.end(cancel=True)
        await asyncio.sleep(.05)
        await app.task
        state = await app.snapshot()
        assert state["phase"] == "finished" and state["status"] == "failed"
        assert state["turns"][0]["status"] == "cancelled"
        assert api.deleted and api.cancelled
        assert sum(call[2] == {"events": [{"type": "agent.session.input.cancel"}]} for call in api.calls) == 1
        await app.close()


@pytest.mark.asyncio
async def test_waiting_for_manual_second_turn_also_expires():
    api = FakeAPI()
    app = trial(api, call_seconds=.03, cleanup_seconds=.3)
    await app.submit(body())
    await app.task
    assert app.phase == "awaiting_input"
    await asyncio.sleep(.05)
    await app.task
    assert app.phase == "finished" and api.deleted
    assert app.report["error"]["category"] == "deadline_exceeded"
    await app.close()


@pytest.mark.asyncio
async def test_missing_key_preview_and_invalid_fields_never_call_provider():
    for app in (Experiment(), Experiment(mode="live")):
        assert not (await app.snapshot())["can_send"]
        with pytest.raises(TrialError):
            await app.submit(body())
        await app.close()
    api = FakeAPI()
    app = trial(api)
    for bad in (body(""), body("x" * 4097), body(KEY), body(number=True),
                {**body(), "model": "other"}, body(oid="bad")):
        with pytest.raises(TrialError):
            await app.submit(bad)
    assert not api.calls
    await app.close()


@pytest.mark.asyncio
async def test_receipt_failure_prevents_dispatch(tmp_path):
    obstruction = tmp_path / "file"
    obstruction.write_text("occupied")
    api = FakeAPI()
    app = trial(api, receipt_path=obstruction / "receipt.json")
    with pytest.raises(TrialError, match="receipt_write_failed"):
        await app.submit(body())
    assert not api.calls
    await app.close()


@pytest.mark.asyncio
async def test_demo_has_no_http_client_no_credential_and_no_invented_usage():
    app = Experiment(mode="demo", key=KEY, demo_delay=0)
    assert app.key == "" and not hasattr(app.adapter, "client")
    for number, text in enumerate(PRESETS, 1):
        await app.submit(body(text, number))
        await app.task
    state = await app.snapshot()
    assert state["evidence"] == "offline_demo"
    assert state["turns"][0]["usage"] is None
    assert state["turns"][1]["expected_text_check"] == "passed"
    assert state["cleanup"]["status"] == "passed"
    await app.close()


def test_http_origin_csrf_whitelist_and_receipt(tmp_path):
    bridge = LoopThread()
    app = Experiment(mode="demo", demo_delay=0, receipt_path=tmp_path / "receipt.json")
    server = TrialServer(0, bridge, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False) as client:
            assert client.get("/").status_code == 200
            assert client.get("/api/state").status_code == 403
            assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403
            assert client.get("/api/state", headers={"X-Experiment-Client": "1", "Origin": "https://other.example"}).status_code == 403
            headers = {"X-Experiment-Client": "1", "Origin": server.origin}
            response = client.get("/api/state", headers=headers)
            state = response.json()
            assert state["mode"] == "demo" and "no-store" in response.headers["cache-control"]
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
            assert client.post("/api/input", headers=headers, json=body()).status_code == 403
            headers["X-Experiment-CSRF"] = state["csrf"]
            assert client.post("/api/input", headers=headers, json=body(PRESETS[0])).status_code == 202
            assert client.post("/api/input", headers=headers, json=body()).status_code == 409
            assert client.get("/../adapter.py").status_code == 404
            assert client.post("/api/cleanup", headers=headers, json={"session_id": "other"}).status_code == 400
            assert client.get("/api/receipt", headers=headers).json()["evidence"] == "offline_demo"
            assert client.get("/api/receipt").status_code == 403
            assert client.get("/api/receipt", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
            attachment = client.get("/api/receipt", headers={"Sec-Fetch-Site": "same-origin"})
            assert attachment.status_code == 200 and "attachment" in attachment.headers["content-disposition"]
            assert client.options("/api/input", headers=headers).status_code == 403
            assert client.post("/api/input", headers=headers, content="x" * 25000).status_code == 415
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
        bridge.call(app.close())
        bridge.close()


@pytest.mark.asyncio
async def test_cleanup_failure_cannot_report_overall_completion():
    api = FakeAPI()
    api.overrides[("DELETE", f"/v1/agents/sessions/{SID}")] = httpx.Response(500, json={"secret": KEY})
    app = trial(api)
    for number, text in enumerate((FIRST_INPUT, SECOND_INPUT), 1):
        await app.submit(body(text, number))
        await app.task
    state = await app.snapshot()
    assert state["phase"] == "finished" and state["status"] == "failed"
    assert state["cleanup"]["status"] == "failed" and not state["can_send"]
    assert state["error"]["category"] == "cleanup_unconfirmed"
    assert len([call for call in api.calls if call[0] == "DELETE"]) == 1
    assert KEY not in json.dumps(await app.receipt())
    await app.close()


@pytest.mark.asyncio
async def test_known_turn_id_survives_disconnect_before_first_text():
    api = FakeAPI()
    api.first_bytes = frames(api.first[:2])
    app = trial(api)
    await app.submit(body())
    await app.task
    state = await app.snapshot()
    assert state["turns"][0]["turn_id"] == "turn_1"
    assert state["status"] == "failed" and api.deleted
    await app.close()
