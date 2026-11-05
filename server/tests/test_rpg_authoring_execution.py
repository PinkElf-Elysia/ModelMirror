"""B6-AI uses only MockTransport and synthetic credentials/qualification."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.model_router import rpg_bridge as bridge
from server.model_router import rpg_authoring_execution as tasks
from server.tests.test_rpg_bridge import setup, HEADERS, stream, _declared_parameters, _authoring_selection

PATH = "/api/rpg/v1/authoring/tasks"


def task(setup):
    client, _, _, meta, _, _ = setup
    _declared_parameters(meta)
    _, choice = _authoring_selection(client, {"temperature": .6, "topP": .9, "maxOutputTokens": 4096})
    return {**choice, "format": tasks.FORMAT, "sessionId": "session-test", "requestId": "request-test",
            "operationId": "operation-test", "purpose": "story", "sourceRequestHash": "a" * 64,
            "messages": [{"role": "system", "content": "系统\n原文"}, {"role": "user", "content": "玩家\n后置"}]}


def lookup(client, value):
    return client.get(f"{PATH}/{value['sessionId']}/{value['requestId']}", headers=HEADERS,
                      params={"sourceRequestHash": value["sourceRequestHash"]})


def enabled(monkeypatch):
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")


def test_closed_by_default_and_auth_first(setup, monkeypatch):
    client, _, repo, _, captured, _ = setup
    value = task(setup)
    monkeypatch.delenv("RPG_AUTHORING_EXECUTION_ENABLED", raising=False)
    result = client.post(PATH, headers=HEADERS, json=value)
    assert result.status_code == 200
    assert result.json()["receipt"]["dispatched"] is False
    assert result.json()["receipt"]["error"] == "rpg_authoring_execution_disabled"
    assert client.post(PATH, json=value).status_code == 401
    assert client.post(PATH, headers={**HEADERS, "Origin": "http://localhost"}, json=value).status_code == 403
    assert lookup(client, value).json() == result.json()
    assert len(list(repo.storage_dir.glob("rpg-authoring-tasks/*.result.json"))) == 1
    assert captured == []


def test_disabled_execution_is_a_durable_failure_and_cannot_replay_when_enabled(setup, monkeypatch):
    client, _, _, _, captured, _ = setup
    value = task(setup)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "false")
    result = client.post(PATH, headers=HEADERS, json=value)
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "failed"
    assert body["receipt"]["dispatched"] is False
    assert body["receipt"]["error"] == "rpg_authoring_execution_disabled"
    assert body["receipt"]["runId"] is None and body["receipt"]["attemptId"] is None
    assert body["raw"] == body["sse"] == ""
    assert lookup(client, value).json() == body
    enabled(monkeypatch)
    assert client.post(PATH, headers=HEADERS, json=value).json() == body
    assert captured == []


def test_readiness_is_authenticated_non_dispatching_and_uncached(setup, monkeypatch):
    client, _, repo, _, captured, _ = setup
    path = "/api/rpg/v1/authoring/readiness"
    monkeypatch.delenv("RPG_AUTHORING_EXECUTION_ENABLED", raising=False)
    assert client.get(path).status_code == 401
    assert client.get(path, headers={**HEADERS, "Origin": "http://localhost"}).status_code == 403
    response = client.get(path, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"format": "modelmirror.rpg.authoring-readiness/1", "executionEnabled": False, "dispatched": False}
    enabled(monkeypatch)
    assert client.get(path, headers=HEADERS).json()["executionEnabled"] is True
    assert not list(repo.storage_dir.glob("rpg-authoring-tasks/*")) and captured == []


def test_exact_payload_receipt_restart_and_duplicate(setup, monkeypatch):
    client, stable, _, _, captured, response = setup
    value = task(setup); enabled(monkeypatch)
    result = client.post(PATH, headers=HEADERS, json=value)
    assert result.status_code == 200, result.text
    body = result.json(); receipt = body["receipt"]
    assert body["status"] == "completed" and receipt["dispatched"]
    assert receipt["gateway"] == "rpg_authoring" and receipt["sourceRequestHash"] == value["sourceRequestHash"]
    assert body["raw"] == " 原文\n\t保留 **格式** " and body["sse"] == response["content"]
    assert receipt["parameters"] == {"temperature": .6, "top_p": .9, "max_tokens": 4096}
    assert captured[0]["messages"] == value["messages"] and captured[0]["temperature"] == .6
    assert receipt["sha256"] == bridge.digest({k:v for k,v in receipt.items() if k != "sha256"})
    assert lookup(client, value).json() == body
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "false")
    assert client.post(PATH, headers=HEADERS, json=value).json() == body
    app = FastAPI(); app.include_router(bridge.router)
    app.dependency_overrides[bridge.get_stable] = lambda: stable
    assert lookup(TestClient(app), value).json() == body
    value["messages"][-1]["content"] = "changed"
    assert client.post(PATH, headers=HEADERS, json=value).status_code == 409
    assert len(captured) == 1


@pytest.mark.parametrize("purpose", ["summary", "compression", "memory"])
def test_same_task_contract_for_independent_purposes(setup, monkeypatch, purpose):
    client, _, _, _, captured, _ = setup
    value = task(setup); value["purpose"] = purpose; enabled(monkeypatch)
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "completed" and result["receipt"]["purpose"] == purpose
    assert len(captured) == 1 and "response_format" not in captured[0]


@pytest.mark.parametrize("kind", ["selection", "parameter", "capacity", "qualification"])
def test_known_preflight_failure_is_durable_without_dispatch(setup, monkeypatch, kind):
    client, _, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    if kind == "selection": value["selectionRevision"] = "b" * 64
    if kind == "parameter": value["parameters"]["n"] = 4
    if kind == "capacity": value["messages"][1]["content"] = "x" * 128000
    if kind == "qualification": monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "failed" and result["receipt"]["dispatched"] is False
    assert lookup(client, value).json() == result and captured == []


@pytest.mark.parametrize("kind,expected", [
    ("timeout", "rpg_authoring_preflight_timeout"),
    ("network", "rpg_authoring_preflight_network"),
    ("dns", "rpg_authoring_dns_failed"),
    ("blocked", "rpg_authoring_address_blocked"),
    ("unexpected", "rpg_authoring_preflight_failed"),
])
def test_preflight_diagnostic_is_safe_durable_and_never_replayed(setup, monkeypatch, kind, expected):
    from server.model_router.egress import ProviderEgressError
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    marker = "synthetic-private-exception-detail"
    failures = {
        "timeout": httpx.ConnectTimeout(marker),
        "network": httpx.ConnectError(marker),
        "dns": ProviderEgressError("provider_dns_failed", marker),
        "blocked": ProviderEgressError("provider_address_blocked", marker),
        "unexpected": RuntimeError(marker),
    }
    calls = []
    async def rejected(*args):
        calls.append(args)
        raise failures[kind]
    monkeypatch.setattr(stable, "begin_rpg_authoring_certified", rejected)
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "failed" and result["receipt"]["error"] == expected
    assert result["receipt"]["dispatched"] is False
    assert result["receipt"]["attemptId"] is None and result["receipt"]["runId"] is None
    assert result["raw"] == result["sse"] == "" and marker not in json.dumps(result)
    assert lookup(client, value).json() == result
    assert client.post(PATH, headers=HEADERS, json=value).json() == result
    assert len(calls) == 1 and captured == []


def test_timeout_after_dispatch_remains_unknown_not_preflight_failure(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    calls = []
    async def uncertain(*args):
        calls.append(args)
        raise httpx.ConnectTimeout("synthetic-private-transport-detail")
    monkeypatch.setattr(stable.transport, "send_authorized_stream", uncertain)
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "unknown"
    assert result["receipt"]["dispatched"] is True
    assert result["receipt"]["error"] == "rpg_authoring_result_unknown"
    assert lookup(client, value).json() == result
    assert client.post(PATH, headers=HEADERS, json=value).json() == result
    assert len(calls) == 1 and captured == []


def test_preflight_deadline_stops_before_provider_claim_and_remains_durable(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    calls, cancelled = [], []
    async def stalled(*args):
        calls.append(args)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(tasks, "PREFLIGHT_TIMEOUT_SECONDS", .02)
    monkeypatch.setattr(stable, "begin_rpg_authoring_certified", stalled)
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "failed"
    assert result["receipt"]["error"] == "rpg_authoring_preflight_timeout"
    assert result["receipt"]["dispatched"] is False and result["receipt"]["attemptId"] is None
    assert lookup(client, value).json() == result
    assert client.post(PATH, headers=HEADERS, json=value).json() == result
    assert calls and len(calls) == len(cancelled) == 1 and captured == []


def test_lost_local_result_stays_unknown_and_never_replays(setup, monkeypatch):
    client, _, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    def fail(*_): raise OSError("synthetic disk failure")
    monkeypatch.setattr(tasks, "_persist", fail)
    assert client.post(PATH, headers=HEADERS, json=value).json()["status"] == "unknown"
    assert lookup(client, value).json()["status"] == "unknown"
    assert client.post(PATH, headers=HEADERS, json=value).json()["status"] == "unknown"
    assert len(captured) == 1


@pytest.mark.parametrize("finish,done,status", [("length", True, "completed"), ("stop", False, "unknown"), ("content_filter", True, "failed")])
def test_output_partial_and_terminal_are_distinct(setup, monkeypatch, finish, done, status):
    client, _, _, _, captured, response = setup
    value = task(setup); enabled(monkeypatch)
    response["content"] = stream(raw="partial 原文", finish=finish, done=done)
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == status and result["raw"] == "partial 原文"
    assert result["receipt"]["finishReason"] == finish
    assert lookup(client, value).json() == result and len(captured) == 1


def test_exact_connection_method_is_used_and_rechecked(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    calls = []
    original = stable.begin_rpg_authoring_certified
    async def begin(model, connection):
        calls.append((model, connection))
        preflight = await original(model, connection)
        assert preflight.dispatch.target.connection_id == connection
        return replace(preflight, dispatch=replace(preflight.dispatch, target=replace(preflight.dispatch.target, connection_id="other")))
    monkeypatch.setattr(stable, "begin_rpg_authoring_certified", begin)
    monkeypatch.setattr(stable, "begin_rpg_certified", lambda *_: pytest.fail("legacy selector must not be used"))
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "failed" and result["receipt"]["error"] == "rpg_authoring_selection_changed"
    assert len(calls) == 1 and captured == []


def test_pinned_route_never_falls_back(setup):
    _, stable, _, _, captured, _ = setup
    preflight = asyncio.run(stable.begin_rpg_authoring_certified("missing-model", "not-in-policy"))
    assert preflight.dispatch is None and captured == []


def test_invalid_and_duplicate_fields_never_claim(setup, monkeypatch):
    client, _, repo, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    body = json.dumps(value)
    assert client.post(PATH, headers=HEADERS, content=body[:-1]+',"purpose":"memory"}').status_code == 422
    value["providerUrl"] = "https://not-allowed.invalid"
    assert client.post(PATH, headers=HEADERS, json=value).status_code == 422
    assert not list(repo.storage_dir.glob("rpg-authoring-tasks/*")) and captured == []


def test_completion_receipt_pending_preserves_output(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    monkeypatch.setattr(stable, "complete", lambda *a, **k: False)
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "unknown" and result["raw"].strip()
    assert result["receipt"]["error"] == "rpg_authoring_receipt_pending"
    assert lookup(client, value).json() == result and len(captured) == 1


def test_page_disconnect_does_not_cancel_owned_task(setup, monkeypatch):
    # Exercise the ASGI handler task cancellation separately from Provider failure.
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    app = client.app
    route = next(r for r in app.routes if getattr(r,"path",None) == PATH and "POST" in getattr(r,"methods",set()))
    original = stable.begin_rpg_authoring_certified
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def begin(*args):
            entered.set(); await release.wait(); return await original(*args)
        monkeypatch.setattr(stable,"begin_rpg_authoring_certified",begin)
        class Request:
            async def stream(self): yield json.dumps(value).encode()
        waiting = asyncio.create_task(route.endpoint(Request(), stable=stable))
        await entered.wait(); waiting.cancel()
        with pytest.raises(asyncio.CancelledError): await waiting
        release.set()
        owned = list(tasks._tasks)
        assert len(owned) == 1
        await asyncio.gather(*owned)
    asyncio.run(run())
    assert lookup(client,value).json()["status"] == "completed" and len(captured) == 1



def test_node_adapter_cross_language_task_receipts(setup, monkeypatch, tmp_path):
    """Real JS transport talks local HTTP to the actual ASGI routes; upstream stays MockTransport."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import subprocess
    import threading
    from server.tests.test_rpg_bridge import TOKEN
    client, _, _, meta, captured, _ = setup
    _declared_parameters(meta); enabled(monkeypatch)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def forward(self):
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            reply = client.request(self.command, self.path, content=body,
                                   headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")})
            self.send_response(reply.status_code)
            self.send_header("Content-Type", reply.headers.get("content-type", "application/json"))
            self.send_header("Content-Length", str(len(reply.content)))
            self.end_headers()
            self.wfile.write(reply.content)

        do_GET = forward
        do_POST = forward

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    module = Path(__file__).resolve().parents[2] / "experiments" / "ai-rpg-engine"
    settings = {"origin": f"http://127.0.0.1:{server.server_port}", "serviceToken": TOKEN,
                "directory": str(tmp_path / "node")}
    script = r"""
import assert from 'node:assert/strict';
import {ControlledProvider} from './authoring/controlled-provider.mjs';
import {ControlPlaneModelControl} from './authoring/model-control.mjs';
import {validateControlledReceipt} from './authoring/controlled-receipt.mjs';
import {canonical,sha256} from './authoring/contracts.mjs';
const options=JSON.parse(process.argv[1]),{directory,...connection}=options;
const control=new ControlPlaneModelControl(connection);await control.refresh();
const catalog=control.catalog(),entry=catalog.models.find(m=>m.available);
const choice=control.select({catalogRevision:catalog.revision,selectionId:entry.selectionId,selectionRevision:entry.selectionRevision,parameters:{temperature:0.6,topP:0.9,maxOutputTokens:4096}});
await control.validate(choice);
let provider=await ControlledProvider.open(options);
const records=[];
try {
 for(const [index,kind] of ['story','summary','compression','memory'].entries()) {
  const request={id:'cross-request-'+index,sessionId:'cross-session',operationId:'cross-operation-'+index,kind,modelChoice:choice,
   messages:[{role:'system',content:'系统\n原文'},{role:'user',content:'玩家\n后置'}]};
  request.messagesHash=sha256(canonical(request.messages));
  const receipt=await provider.dispatch(request);validateControlledReceipt(receipt,request);
  assert.equal(receipt.kind,'control-plane');assert.equal(receipt.result.text,' 原文\n\t保留 **格式** ');
  assert.equal(receipt.result.finishReason,'stop');records.push({request,receipt});
 }
 await provider.close();provider=await ControlledProvider.open(options);
 for(const {request,receipt} of records) {
  assert.deepEqual(await provider.lookup(request),receipt);
  await assert.rejects(()=>provider.dispatch(request),{code:'CONTROL_REQUEST_ALREADY_CLAIMED'});
 }
 console.log(JSON.stringify({purposes:records.map(r=>r.request.kind),recovered:records.length}));
} finally {await provider.close();}
"""
    try:
        result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(settings)],
                                cwd=module, capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == {"purposes": ["story", "summary", "compression", "memory"], "recovered": 4}
        assert len(captured) == 4
        assert all(c["messages"] == [{"role": "system", "content": "系统\n原文"}, {"role": "user", "content": "玩家\n后置"}] for c in captured)
        assert all(c["temperature"] == .6 and c["top_p"] == .9 and c["max_tokens"] == 4096 for c in captured)
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)


def test_transport_break_keeps_received_partial_raw_and_original_lookup(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    partial = stream(raw="partial before transport break", done=False)
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield partial.encode("utf-8")
            raise httpx.ReadError("synthetic interrupted stream")
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, stream=Broken(), headers={"content-type": "text/event-stream"})
    monkeypatch.setattr(stable.transport, "client_kwargs", lambda: {"transport": httpx.MockTransport(upstream), "trust_env": False})
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "unknown" and result["raw"] == "partial before transport break"
    assert result["sse"] == partial and result["receipt"]["dispatched"]
    assert lookup(client, value).json() == result and len(captured) == 1


def use_openrouter_target(stable, monkeypatch):
    original = stable.begin_rpg_authoring_certified
    async def openrouter(*args):
        result = await original(*args)
        return replace(result, dispatch=replace(result.dispatch, target=replace(result.dispatch.target, provider_kind="openrouter")))
    monkeypatch.setattr(stable, "begin_rpg_authoring_certified", openrouter)


def test_openrouter_authoring_requires_parameters_and_forbids_fallback(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    use_openrouter_target(stable, monkeypatch)
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["status"] == "completed"
    assert captured[0]["provider"] == {"allow_fallbacks": False, "require_parameters": True}
    assert captured[0]["transforms"] == []
    assert captured[0]["temperature"] == .6 and captured[0]["top_p"] == .9
    assert lookup(client, value).json() == result and len(captured) == 1


@pytest.mark.parametrize(("kind", "error_type", "expected"), [
    ("openrouter", "permission_denied", "rpg_authoring_upstream_permission_denied"),
    ("openrouter", "content_policy_violation", "rpg_authoring_upstream_content_policy"),
    ("openrouter", "refusal", "rpg_authoring_upstream_refusal"),
    ("openrouter", "documented_guardrail", "rpg_authoring_upstream_guardrail"),
    ("openrouter", "unknown-private-type", "rpg_authoring_upstream_http"),
    ("openrouter", "message_only", "rpg_authoring_upstream_http"),
    ("openrouter", "oversize", "rpg_authoring_upstream_http"),
    ("openrouter", "mismatched_status", "rpg_authoring_upstream_http"),
    ("newapi", "content_policy_violation", "rpg_authoring_upstream_http"),
])
def test_authoring_forbidden_receipt_is_safe_and_not_replayed(setup, monkeypatch, kind, error_type, expected):
    client, stable, _, _, captured, response = setup
    value = task(setup)
    enabled(monkeypatch)
    if kind == "openrouter":
        use_openrouter_target(stable, monkeypatch)
    marker = "synthetic-private-provider-detail"
    error = {"code": 403, "message": marker, "metadata": {"error_type": error_type, "raw": marker}}
    if error_type == "documented_guardrail":
        error.update(message="Request blocked: prompt injection patterns detected", metadata={"patterns": [marker]})
    elif error_type == "message_only":
        error.update(message="permission_denied content_policy_violation " + marker, metadata={})
    elif error_type == "oversize":
        error.update(message=marker * 1000, metadata={"error_type": "refusal"})
    elif error_type == "mismatched_status":
        error.update(code=401, metadata={"error_type": "permission_denied"})
    response.update(status=403, type="application/json", content=json.dumps({"error": error}))
    completions = []
    complete = stable.complete

    def record(*args, **kwargs):
        completions.append((kwargs["result_class"], kwargs["hard_failure"]))
        return complete(*args, **kwargs)

    monkeypatch.setattr(stable, "complete", record)
    result = client.post(PATH, json=value, headers=HEADERS).json()
    receipt = result["receipt"]
    assert (result["status"], receipt["httpStatus"], receipt["error"]) == ("failed", 403, expected)
    assert receipt["dispatched"] and receipt["retries"] == 0 and receipt["actualModel"] is None
    assert result["raw"] == result["sse"] == "" and marker not in json.dumps(result)
    assert lookup(client, value).json() == result
    assert client.post(PATH, json=value, headers=HEADERS).json() == result
    assert len(captured) == 1 and completions == [("hard_failure", True)]


def test_gate_closed_during_preflight_never_reaches_transport(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    value = task(setup); enabled(monkeypatch)
    original = stable.begin_rpg_authoring_certified
    async def close_gate(*args):
        result = await original(*args)
        monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "false")
        return result
    monkeypatch.setattr(stable, "begin_rpg_authoring_certified", close_gate)
    body = client.post(PATH, headers=HEADERS, json=value).json()
    assert body["status"] == "failed" and body["receipt"]["dispatched"] is False
    assert body["receipt"]["error"] == "rpg_authoring_execution_disabled"
    assert lookup(client, value).json() == body and captured == []


def test_disabled_failure_with_lost_persistence_remains_unknown(setup, monkeypatch):
    client, _, _, _, captured, _ = setup
    value = task(setup)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "false")
    def failed_save(*_):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(tasks, "_persist", failed_save)
    assert client.post(PATH, headers=HEADERS, json=value).json()["status"] == "unknown"
    enabled(monkeypatch)
    assert client.post(PATH, headers=HEADERS, json=value).json()["status"] == "unknown"
    assert captured == []
