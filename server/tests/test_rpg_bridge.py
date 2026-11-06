"""Offline RPG bridge acceptance. All provider traffic uses httpx.MockTransport."""
import json
import copy
import sqlite3
import subprocess
from pathlib import Path
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.model_router import rpg_bridge as bridge
from server.model_router.call_context_storage import ProviderContextStore
from server.tests.test_provider_chat_stable_service import _service, _qualify_scoped_model, SCOPED_MODEL_ID

TOKEN = "synthetic-rpg-service-token-for-tests-only"
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.mark.parametrize("setup", ["google/gemini-3.8-flash", "google/gemini-4-argon"], indirect=True)
def test_god_long_profile_is_bound_and_default_unchanged(setup):
    client, _, _, meta, captured, _ = setup
    base = client.get('/api/rpg/v1/models', headers=HEADERS).json()['models'][0]
    path = '/api/rpg/v1/models?profile=god-long-v1'
    long = client.get(path, headers=HEADERS).json()['models'][0]
    assert base['parameters']['max_tokens'] == 16384
    assert long['parameters'] == {**bridge.PARAMETERS, 'max_tokens': 32768}
    assert long['available'] and long['selectionId'] != base['selectionId']
    assert long['selectionRevision'] != base['selectionRevision']
    value = payload(client)
    value.update(selectionId=long['selectionId'], selectionRevision=long['selectionRevision'], parameters=long['parameters'])
    assert client.post('/api/rpg/v1/chat/completions', headers=HEADERS, json=value).status_code == 422
    result = client.post('/api/rpg/v1/chat/completions?profile=god-long-v1', headers=HEADERS, json=value)
    assert result.status_code == 200, result.text
    assert captured[0]['max_tokens'] == 32768 and captured[0]['messages'] == value['messages']
    assert result.json()['receipt']['parameters'] == long['parameters']
    meta['max_output_tokens'] = 16384
    assert client.get(path, headers=HEADERS).json()['models'][0]['available'] is False
    assert client.get('/api/rpg/v1/models', headers=HEADERS).json()['models'][0]['available'] is True
    assert client.get('/api/rpg/v1/models?profile=arbitrary', headers=HEADERS).status_code == 422
    assert len(captured) == 1


def stream(model=SCOPED_MODEL_ID, raw=" 原文\n\t保留 **格式** ", finish="stop", done=True):
    event = {"choices": [{"delta": {"content": raw}, "finish_reason": finish}]}
    if model is not None:
        event["model"] = model
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n" + ("data: [DONE]\n\n" if done else "")


@pytest.fixture
def setup(tmp_path, monkeypatch, request):
    model_id = getattr(request, "param", SCOPED_MODEL_ID)
    if model_id != SCOPED_MODEL_ID:
        from server.tests import test_provider_chat_stable_service as fixtures
        monkeypatch.setattr(fixtures, "SCOPED_MODEL_ID", model_id)
    stable, repo, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    _qualify_scoped_model(repo, primary)
    monkeypatch.setenv("RPG_S2S_ENABLED", "true")
    monkeypatch.setenv("RPG_S2S_TOKEN", TOKEN)
    original = repo.list_catalog_models
    metadata = {"name": "Synthetic model", "context_length": 128000, "max_output_tokens": 32768,
                "supported_parameters": ["temperature", "top_p", "max_tokens"]}
    def listing(*args, **kwargs):
        rows = original(*args, **kwargs)
        return [{**r, "metadata_json": json.dumps(metadata)} for r in rows if r["model_id"] == model_id]
    monkeypatch.setattr(repo, "list_catalog_models", listing)
    captured = []
    response = {"status": 200, "content": stream(model=model_id), "type": "text/event-stream"}
    def upstream(request):
        captured.append(json.loads(request.content))
        return httpx.Response(response["status"], content=response["content"], headers={"content-type": response["type"]})
    monkeypatch.setattr(stable.transport, "client_kwargs", lambda: {"transport": httpx.MockTransport(upstream), "trust_env": False, "follow_redirects": False})
    app = FastAPI()
    app.include_router(bridge.router)
    app.dependency_overrides[bridge.get_stable] = lambda: stable
    client = TestClient(app)
    return client, stable, repo, metadata, captured, response


def payload(client, pairs=0):
    selected = next(m for m in client.get("/api/rpg/v1/models", headers=HEADERS).json()["models"] if m["available"])
    messages = [{"role": "system", "content": "完整冻结系统\n\t"}]
    for i in range(pairs):
        messages.extend([{"role": "user", "content": f"前置\n玩家{i}\n后置"}, {"role": "assistant", "content": f"原文{i}\n\t"}])
    messages.append({"role": "user", "content": "前置\n本轮玩家\n后置"})
    return {"sessionId": "synthetic-session", "requestId": "synthetic-request", "selectionId": selected["selectionId"],
            "selectionRevision": selected["selectionRevision"], "messages": messages, "parameters": dict(bridge.PARAMETERS)}


@pytest.mark.parametrize("headers,enabled,token,status", [({}, "true", TOKEN, 401), (HEADERS, "false", TOKEN, 503),
    (HEADERS, "true", "", 503), ({**HEADERS, "Origin": "http://localhost"}, "true", TOKEN, 403),
    ({**HEADERS, "Sec-Fetch-Site": "same-origin"}, "true", TOKEN, 403)])
def test_auth_before_catalog_or_provider(setup, monkeypatch, headers, enabled, token, status):
    client, stable, _, _, captured, _ = setup
    monkeypatch.setenv("RPG_S2S_ENABLED", enabled)
    monkeypatch.setenv("RPG_S2S_TOKEN", token)
    monkeypatch.setattr(stable.control, "get_policy", lambda: pytest.fail("auth must run first"))
    assert client.get("/api/rpg/v1/models", headers=headers).status_code == status
    assert client.post("/api/rpg/v1/chat/completions", headers=headers, content=b"bad json").status_code == status
    assert captured == []


def test_catalog_no_dispatch_unknown_metadata_not_inferred(setup):
    client, _, _, meta, captured, _ = setup
    models = client.get("/api/rpg/v1/models", headers=HEADERS).json()["models"]
    assert models[0]["available"] is True
    assert "pricing" not in models[0] and "_binding" not in models[0]
    assert "api_key" not in json.dumps(models)
    meta.pop("supported_parameters")
    assert client.get("/api/rpg/v1/models", headers=HEADERS).json()["models"][0]["available"] is False
    assert captured == []


def test_actual_transport_preserves_84_messages_and_receipt_and_duplicate_never_replays(setup):
    client, stable, repo, _, captured, _ = setup
    value = payload(client, pairs=41)
    response = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["raw"] == " 原文\n\t保留 **格式** "
    assert result["receipt"]["actualModel"] == SCOPED_MODEL_ID
    assert result["receipt"]["gateway"] == "rpg_scoped"
    assert captured[0]["messages"] == value["messages"] and len(captured[0]["messages"]) == 84
    assert all(captured[0][k] == v for k, v in bridge.PARAMETERS.items())
    assert captured[0]["model"] == SCOPED_MODEL_ID
    assert "response_format" not in captured[0]
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 409
    value["messages"][-1]["content"] = "changed"
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 409
    # New application with the same persistent repository cannot replay the claim.
    app = FastAPI(); app.include_router(bridge.router)
    app.dependency_overrides[bridge.get_stable] = lambda: stable
    assert TestClient(app).post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 409
    assert len(captured) == 1
    runs = repo.list_chat_control_receipts("local")["runs"]
    assert any(r["gateway"] == "rpg_scoped" and r["status"] == "succeeded" for r in runs)
    attempts = repo.list_chat_control_receipts("local")["attempts"]
    assert len(attempts) == 1 and attempts[0]["dispatched"]
    context = ProviderContextStore(repo).read("local", attempts[0]["id"])
    assert (context.actor_kind, context.actor_reference, context.entry_id, context.purpose) == (
        "service", "rpg-bridge", "rpg_bridge", "user_run")
    assert context.parent_run_id == result["receipt"]["runId"]
    assert context.attempt_id == result["receipt"]["attemptId"]
    assert "actor_reference" not in response.text and "call_context" not in response.text


@pytest.mark.parametrize("field", ["tenant_id", "actor_reference", "entry_id", "connection_id", "call_context"])
def test_client_cannot_supply_bridge_origin(setup, field):
    client, _, repo, _, captured, _ = setup
    value = {**payload(client), field: "forged"}
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 422
    assert captured == []
    assert repo.list_chat_control_receipts("local")["attempts"] == []


@pytest.mark.parametrize("change", ["revision", "params", "url", "roles", "capacity", "qualification"])
def test_preflight_rejects_without_dispatch(setup, monkeypatch, change):
    client, stable, _, meta, captured, _ = setup
    value = payload(client)
    if change == "revision": value["selectionRevision"] = "stale"
    if change == "params": value["parameters"]["temperature"] = 0
    if change == "url": value["providerUrl"] = "https://untrusted.invalid"
    if change == "roles": value["messages"].append({"role": "system", "content": "extra"})
    if change == "capacity": value["messages"][1]["content"] = "x" * 120000
    if change == "qualification": monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    response = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert response.status_code in (409, 422), response.text
    assert captured == []


@pytest.mark.parametrize("reason", ["rpg_fallback_limit_exhausted", "provider_address_blocked"])
def test_preflight_failure_has_bound_undispatched_receipt(setup, monkeypatch, reason):
    from types import SimpleNamespace
    client, stable, _, _, captured, _ = setup
    value = payload(client)
    async def rejected(*args, **kwargs):
        return SimpleNamespace(dispatch=None, error_code=reason)
    monkeypatch.setattr(stable, "begin_rpg_certified", rejected)
    response = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert response.status_code == 409
    receipt = response.json()["receipt"]
    assert receipt["status"] == "failed" and receipt["dispatched"] is False
    assert receipt["requestHash"] == bridge.digest(value)
    assert receipt["sessionId"] == value["sessionId"] and receipt["requestId"] == value["requestId"]
    assert receipt["selectionRevision"] == value["selectionRevision"]
    assert receipt["error"] == (reason if reason == "rpg_fallback_limit_exhausted" else "rpg_route_unavailable")
    assert captured == []


@pytest.mark.parametrize("setup", [SCOPED_MODEL_ID, "google/gemini-3.8-flash", "google/gemini-4-argon"], indirect=True)
def test_selected_compatible_backup_is_the_dispatched_route(setup, monkeypatch):
    client, stable, repo, _, captured, _ = setup
    route = next(r for r in stable.control.get_policy().routes if r.capability == "chat_text")
    primary, backup = route.connection_ids
    _qualify_scoped_model(repo, backup)
    original = repo.list_catalog_models
    def listing(*args, **kwargs):
        rows = original(*args, **kwargs)
        return [{**r, "metadata_json": "{}"} for r in rows] if kwargs.get("connection_id") == primary else rows
    monkeypatch.setattr(repo, "list_catalog_models", listing)
    selected = next(m for m in bridge.catalog(stable) if m["available"])
    assert selected["_binding"]["connection"] == backup
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=payload(client))
    assert result.status_code == 200, result.text
    attempts = repo.list_chat_control_receipts("local")["attempts"]
    assert len(attempts) == 1 and attempts[0]["connection_id"] == backup and attempts[0]["dispatched"]
    assert len(captured) == 1 and captured[0]["model"] == selected["model"]
    assert captured[0]["provider"] == {"allow_fallbacks": False, "require_parameters": True}


def test_route_drift_during_begin_blocks_fallback(setup, monkeypatch):
    client, stable, repo, _, captured, _ = setup
    value = payload(client)
    original = stable.begin_rpg_certified
    async def changed(model, **kwargs):
        result = await original(model, **kwargs)
        return replace(result, dispatch=replace(result.dispatch, connection_fingerprint="changed"))
    monkeypatch.setattr(stable, "begin_rpg_certified", changed)
    response = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert response.status_code == 409
    receipt = response.json()["receipt"]
    assert receipt["status"] == "failed" and receipt["dispatched"] is False
    assert receipt["error"] == "rpg_selection_changed"
    assert receipt["requestHash"] == bridge.digest(value)
    assert receipt["selectionId"] == value["selectionId"] and receipt["selectionRevision"] == value["selectionRevision"]
    assert response.json()["raw"] == response.json()["sse"] == ""
    assert receipt["runId"] and receipt["attemptId"]
    assert captured == []
    assert not any(a["dispatched"] for a in repo.list_chat_control_receipts("local")["attempts"])
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 409
    assert len(repo.list_chat_control_receipts("local")["attempts"]) == 1


@pytest.mark.parametrize("kind", ["http", "incomplete", "mismatch", "credential", "receipt"])
def test_failed_or_unknown_one_dispatch_never_retry(setup, monkeypatch, kind):
    client, stable, _, _, captured, response = setup
    value = payload(client)
    if kind == "http": response["status"] = 503
    if kind == "incomplete": response["content"] = stream(done=False)
    if kind == "mismatch": response["content"] = stream(model="some-other-model")
    if kind == "credential": response["content"] = stream(raw="newAPI-secret")
    if kind == "receipt": monkeypatch.setattr(stable, "complete", lambda *a, **k: False)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert result.status_code == 502, result.text
    assert result.json()["receipt"]["status"] == "failed_or_unknown"
    assert result.json()["receipt"]["retries"] == 0
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 409
    assert len(captured) == 1
    if kind == "credential":
        assert result.json()["raw"] == result.json()["sse"] == ""


def test_unpersisted_preflight_rejection_remains_unknown(setup, monkeypatch):
    client, stable, _, _, captured, _ = setup
    original = stable.begin_rpg_certified
    async def changed(model, **kwargs):
        result = await original(model, **kwargs)
        return replace(result, dispatch=replace(result.dispatch, connection_fingerprint="changed"))
    monkeypatch.setattr(stable, "begin_rpg_certified", changed)
    monkeypatch.setattr(stable, "fail_undispatched", lambda *a, **k: False)
    value = payload(client)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert result.status_code == 502
    assert result.json()["receipt"]["status"] == "failed_or_unknown"
    assert result.json()["receipt"]["error"] == "rpg_receipt_pending"
    assert result.json()["receipt"]["requestHash"] == bridge.digest(value)
    assert captured == []


def test_missing_actual_model_stays_unknown(setup):
    client, _, _, _, _, response = setup
    response["content"] = stream(model=None)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=payload(client))
    assert result.status_code == 200, result.text
    assert result.json()["receipt"]["actualModel"] is None


def test_metadata_changes_after_preflight_before_dispatch(setup, monkeypatch):
    client, stable, repo, meta, captured, _ = setup
    value = payload(client)
    original = stable.begin_rpg_certified
    async def changed(model, **kwargs):
        result = await original(model, **kwargs)
        meta["supported_parameters"] = []
        return result
    monkeypatch.setattr(stable, "begin_rpg_certified", changed)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert result.status_code == 409
    assert result.json()["receipt"]["dispatched"] is False
    assert result.json()["receipt"]["status"] == "failed"
    assert result.json()["receipt"]["error"] == "rpg_selection_changed"
    assert result.json()["receipt"]["requestHash"] == bridge.digest(value)
    assert captured == []
    assert not any(a["dispatched"] for a in repo.list_chat_control_receipts("local")["attempts"])


def test_oversize_malformed_and_non_text_payload_rejected(setup):
    client, _, _, _, captured, _ = setup
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, content=b"x" * (bridge.MAX_REQUEST_BYTES + 1)).status_code == 413
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, content=b"{").status_code == 422
    value = payload(client); value["messages"][1]["content"] = [{"type":"image_url"}]
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 422
    assert captured == []


def test_partial_output_is_kept_as_evidence_and_unknown_is_not_success(setup):
    client, _, _, _, _, response = setup
    response["content"] = stream(raw="未完整返回的原文", done=False)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=payload(client))
    assert result.status_code == 502
    assert result.json()["raw"] == "未完整返回的原文"
    assert result.json()["receipt"]["error"] == "rpg_incomplete_output"

def test_parent_app_registers_rpg_routes_and_default_disabled(monkeypatch):
    from server.main import app
    registered = [(r.path, set(r.methods or ())) for r in app.routes if r.path.startswith('/api/rpg/v1')]
    assert registered == [('/api/rpg/v1/models', {'GET'}), ('/api/rpg/v1/progress/{session_id}/{request_id}', {'GET'}), ('/api/rpg/v1/chat/completions', {'POST'}),
                          ('/api/rpg/v1/authoring/models', {'GET'}), ('/api/rpg/v1/authoring/parameters/validate', {'POST'}),
                          ('/api/rpg/v1/authoring/readiness', {'GET'}), ('/api/rpg/v1/authoring/tasks', {'POST'}), ('/api/rpg/v1/authoring/tasks/{session_id}/{request_id}', {'GET'}),
                          ('/api/rpg/v1/authoring/route', {'GET'}), ('/api/rpg/v1/authoring/route', {'PUT'})]
    monkeypatch.delenv('RPG_S2S_ENABLED', raising=False)
    monkeypatch.delenv('RPG_S2S_TOKEN', raising=False)
    client = TestClient(app)
    assert client.get('/api/rpg/v1/models').status_code == 503
    assert client.get('/api/rpg/v1/progress/example/example').status_code == 503
    assert client.post('/api/rpg/v1/chat/completions', content='{}').status_code == 503
    assert client.get('/api/rpg/v1/authoring/route').status_code == 503
    assert client.put('/api/rpg/v1/authoring/route', content='{}').status_code == 503


def test_luna_exception_is_exact_and_does_not_change_other_models():
    assert bridge.parameters_for("openai/gpt-5.6-luna") == {"max_tokens": 16384}
    for model in ("google/gemini-3.8-flash", "deepseek/deepseek-v4-flash-0731", "openai/gpt-5.6-luna-pro", "unknown"):
        assert bridge.parameters_for(model) == bridge.PARAMETERS


def test_selected_effective_parameters_reach_wire_and_receipt(setup, monkeypatch):
    client, stable, repo, meta, captured, response = setup
    monkeypatch.setattr(bridge, "parameters_for", lambda model, profile="default": {"max_tokens": 16384})
    meta["supported_parameters"] = ["max_tokens"]
    value = payload(client)
    assert client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value).status_code == 422
    assert captured == []
    value["parameters"] = {"max_tokens": 16384}
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert result.status_code == 200
    assert result.json()["receipt"]["parameters"] == {"max_tokens": 16384}
    assert captured[0]["max_tokens"] == 16384
    assert "temperature" not in captured[0] and "top_p" not in captured[0]


def test_endpoint_tag_is_pinned_on_openrouter_wire(setup, monkeypatch):
    client, stable, repo, meta, captured, response = setup
    meta["rpg_provider_tag"] = "test-provider/endpoint"
    original = stable.begin_rpg_certified
    async def begin(model, **kwargs):
        p = await original(model, **kwargs)
        return replace(p, dispatch=replace(p.dispatch, target=replace(p.dispatch.target, provider_kind="openrouter")))
    monkeypatch.setattr(stable, "begin_rpg_certified", begin)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=payload(client))
    assert result.status_code == 200
    assert captured[0]["provider"] == {"allow_fallbacks": False, "require_parameters": True, "only": ["test-provider/endpoint"], "order": ["test-provider/endpoint"]}


# B6-D: real router/repository, synthetic qualification, no provider dispatch.
AUTHORING_MODELS = "/api/rpg/v1/authoring/models"
AUTHORING_VALIDATE = "/api/rpg/v1/authoring/parameters/validate"


def _declared_parameters(meta, *, token_only=False, exclusive=False):
    from server.model_router.provider_catalog import TEXT_PARAMETER_CAPABILITIES_FORMAT, _parameter_capabilities
    field = lambda low, high, default, required, wire: dict(minimum=low, maximum=high, defaultValue=default, required=required, wireName=wire)
    fields = {"maxOutputTokens": field(1, 32768, 4096, True, "max_completion_tokens" if token_only else "max_tokens")}
    if not token_only:
        fields.update(temperature=field(0, 2, 0.7, False, "temperature"), topP=field(0, 1, 0.8, False, "top_p"))
    meta["supported_parameters"] = [f["wireName"] for f in fields.values()]
    raw = {**meta, "parameter_capabilities": {"format": TEXT_PARAMETER_CAPABILITIES_FORMAT,
           "policy": {"kind": "editable", "fields": fields, "exclusive": [["temperature", "topP"]] if exclusive else []}}}
    meta["parameter_capabilities"] = _parameter_capabilities(raw)
    return raw


def _authoring_selection(client, parameters=None):
    response = client.get(AUTHORING_MODELS, headers=HEADERS)
    assert response.status_code == 200, response.text
    catalog = response.json()
    entry = next(e for e in catalog["models"] if e["available"])
    return catalog, {"catalogRevision": catalog["revision"], "selectionId": entry["selectionId"],
                     "selectionRevision": entry["selectionRevision"], "parameters": parameters or {"maxOutputTokens": 4096}}


def test_authoring_legacy_fixed_and_unknown_are_not_editable(setup):
    client, stable, repo, meta, captured, _ = setup
    old = client.get("/api/rpg/v1/models", headers=HEADERS).json()
    c, value = _authoring_selection(client, {"temperature": .7, "topP": .8, "maxOutputTokens": 16384})
    e = c["models"][0]
    assert e["parameterEvidence"] == "legacy-fixed" and e["parameterPolicy"]["kind"] == "fixed"
    assert client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value).json()["wireParameters"] == bridge.PARAMETERS
    value["parameters"]["temperature"] = .8
    assert client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value).status_code == 422
    assert client.get("/api/rpg/v1/models", headers=HEADERS).json() == old
    meta.pop("supported_parameters")
    unknown = client.get(AUTHORING_MODELS, headers=HEADERS).json()["models"][0]
    assert not unknown["available"] and unknown["parameterPolicy"] is None and unknown["parameterEvidence"] == "unknown"
    assert not captured


def test_authoring_catalog_and_parameter_check_are_read_only(setup, monkeypatch):
    client, stable, repo, meta, captured, _ = setup
    _declared_parameters(meta)
    def dump():
        with sqlite3.connect(repo.database_path) as database:
            return list(database.iterdump())
    before = dump()
    monkeypatch.setattr(stable.control, "get_policy", lambda: pytest.fail("catalog must not advance gate epochs"))
    monkeypatch.setattr(stable, "begin_rpg_certified", lambda *_: pytest.fail("must not begin a run"))
    c, value = _authoring_selection(client)
    response = client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["wireParameters"] == {"max_tokens": 4096} and response.json()["dispatched"] is False
    assert c["source"] == "control-plane-cache" and c["models"][0]["parameterEvidence"] == "provider-declared"
    assert dump() == before and not captured
    assert not (repo.storage_dir / "rpg-bridge-requests").exists()
    for forbidden in ("api_key", "base_url", "_binding", "connection_id", TOKEN, "secret"):
        assert forbidden not in json.dumps(c)


@pytest.mark.parametrize("headers,status", [({},401),({**HEADERS,"Origin":"http://localhost"},403),({**HEADERS,"Sec-Fetch-Site":"same-origin"},403)])
def test_authoring_auth_before_evidence(setup, monkeypatch, headers, status):
    client, stable, _, _, captured, _ = setup
    monkeypatch.setattr(stable.control, "peek_policy", lambda: pytest.fail("evidence accessed before auth"))
    assert client.get(AUTHORING_MODELS, headers=headers).status_code == status
    assert client.post(AUTHORING_VALIDATE, headers=headers, content=b"{}").status_code == status
    assert not captured


@pytest.mark.parametrize("params,diagnostic", [({"temperature":2.1,"maxOutputTokens":1},"range"),
    ({"topP":-1,"maxOutputTokens":1},"range"),({"maxOutputTokens":1.2},"range"),({"maxOutputTokens":True},"range"),
    ({"maxOutputTokens":32769},"range"),({"maxOutputTokens":10**1000},"range"),({"temperature":.5},"required"),({"maxOutputTokens":1,"top_p":.8},"unsupported"),
    ({"maxOutputTokens":1,"providerUrl":"https://untrusted.invalid"},"unsupported"),
    ({"maxOutputTokens":1,"temperature":.7,"topP":.8},"conflict")])
def test_authoring_parameter_rejections_no_dispatch(setup, params, diagnostic):
    client, _, _, meta, captured, _ = setup; _declared_parameters(meta, exclusive=True)
    _, value = _authoring_selection(client); value["parameters"] = params
    response = client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value)
    assert response.status_code == 422 and response.json()["detail"] == "rpg_authoring_parameter_" + diagnostic
    assert not captured


def test_authoring_token_only_bounds_and_mapping(setup):
    client, _, _, meta, captured, _ = setup; _declared_parameters(meta, token_only=True)
    for count in (1, 32768):
        _, value = _authoring_selection(client, {"maxOutputTokens": count})
        assert client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value).json()["wireParameters"] == {"max_completion_tokens": count}
    value["parameters"]["temperature"] = .7
    assert client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value).status_code == 422
    assert not captured


def test_authoring_same_model_routes_qualification_policy_and_metadata_drift(setup, monkeypatch):
    client, stable, repo, meta, captured, _ = setup; _declared_parameters(meta)
    policy = stable.control.peek_policy(); routes = stable.control.route_candidates(policy, "chat_text")
    _qualify_scoped_model(repo, routes[1])
    c, value = _authoring_selection(client)
    assert len(c["models"]) == 2 and all(e["available"] for e in c["models"])
    assert len({e["selectionId"] for e in c["models"]}) == 2
    original = stable.control.current_qualification
    def qualification(**kwargs):
        if kwargs["connection_id"] == routes[0]: return None, "test_revoked"
        return original(**kwargs)
    monkeypatch.setattr(stable.control, "current_qualification", qualification)
    current = client.get(AUTHORING_MODELS, headers=HEADERS).json()
    assert [e["available"] for e in current["models"]] == [False, True]
    assert client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value).status_code == 409
    c, value = _authoring_selection(client)
    meta["parameter_capabilities"]["policy"]["fields"]["maxOutputTokens"]["maximum"] = 20000
    assert client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value).status_code == 409
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    assert all(not e["available"] for e in client.get(AUTHORING_MODELS, headers=HEADERS).json()["models"])
    assert not captured


@pytest.mark.parametrize("bad", [b'{"catalogRevision":"x","catalogRevision":"y"}', b'{"parameters":{"maxOutputTokens":NaN}}', b'{"providerUrl":"x"}', b'{'*1200])
def test_authoring_malformed_requests_fail_closed(setup, bad):
    client, _, _, _, captured, _ = setup
    assert client.post(AUTHORING_VALIDATE, headers=HEADERS, content=bad).status_code == 422
    assert client.post(AUTHORING_VALIDATE, headers=HEADERS, content=b'x'*16385).status_code == 413
    assert not captured


def test_authoring_normalization_preserves_valid_and_rejects_ambiguous_evidence(setup):
    from server.model_router.provider_catalog import normalize_provider_catalog
    _, stable, repo, meta, _, _ = setup
    connection = repo.get_connection("local", stable.control.route_candidates(stable.control.peek_policy(), "chat_text")[0])
    raw = {"id": "synthetic", **_declared_parameters(meta, token_only=True)}
    def normalize(records):
        return normalize_provider_catalog(records, connection=connection, observed_at="2026-10-20T00:00:00Z")[0][0]["metadata"]
    result = normalize([raw])
    assert normalize([{"id":"synthetic"},raw])["parameter_capabilities"] == result["parameter_capabilities"]
    assert result["supported_parameters"] == ["max_completion_tokens"]
    assert result["parameter_capabilities"]["status"] == "declared"
    for change in ("version", "unknown", "nan", "wire", "output", "supported", "required", "exclusive"):
        bad = copy.deepcopy(raw); p = bad["parameter_capabilities"]["policy"]
        if change == "version": bad["parameter_capabilities"]["format"] = "future"
        if change == "unknown": p["secret"] = "do-not-retain"
        if change == "nan": p["fields"]["maxOutputTokens"]["maximum"] = float("nan")
        if change == "wire": p["fields"]["maxOutputTokens"]["wireName"] = "arbitrary"
        if change == "output": bad["max_output_tokens"] = 1
        if change == "supported": bad["supported_parameters"] = []
        if change == "required": p["fields"]["maxOutputTokens"]["required"] = False
        if change == "exclusive": p["exclusive"] = [["maxOutputTokens","maxOutputTokens"]]
        assert normalize([bad])["parameter_capabilities"] == {"status":"invalid"}
    conflict = copy.deepcopy(raw); conflict["parameter_capabilities"]["policy"]["fields"]["maxOutputTokens"]["maximum"] = 16000
    assert normalize([raw, conflict])["parameter_capabilities"] == {"status":"invalid"}


def test_authoring_invalid_declaration_never_falls_back_to_fixed(setup):
    client, _, _, meta, captured, _ = setup
    for declaration in ({"status":"invalid"}, {}, None):
        meta["parameter_capabilities"] = declaration
        e = client.get(AUTHORING_MODELS, headers=HEADERS).json()["models"][0]
        assert not e["available"] and e["parameterPolicy"] is None and e["parameterEvidence"] == "invalid"
    assert not captured


def test_authoring_actual_api_projection_drives_node_offline_session(setup, tmp_path):
    client, _, _, meta, captured, _ = setup; _declared_parameters(meta, token_only=True)
    c, value = _authoring_selection(client)
    checked = client.post(AUTHORING_VALIDATE, headers=HEADERS, json=value).json()
    script = r"""
      import http from 'node:http';
      import {AuthoringStore} from './authoring/store.mjs';
      import {blankDocument} from './authoring/contracts.mjs';
      import {openPublishedRuntime} from './authoring/runtime-binding.mjs';
      import {ControlPlaneModelControl} from './authoring/model-control.mjs';
      import {OfflineSessionHost,OfflineFixtureProvider} from './authoring/offline-session.mjs';
      const input=JSON.parse(await new Promise(r=>{let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>r(s));}));
      const requests=[];
      const server=http.createServer(async(req,res)=>{let body='';for await(const b of req)body+=b;requests.push({url:req.url,method:req.method,body:body?JSON.parse(body):null});res.setHeader('content-type','application/json');res.end(JSON.stringify(req.method==='GET'?input.catalog:input.checked));});
      await new Promise(r=>server.listen(0,'127.0.0.1',r));
      const control=new ControlPlaneModelControl({origin:'http://127.0.0.1:'+server.address().port,serviceToken:'synthetic-authoring-token-000000000000'});
      const store=await AuthoringStore.open(input.dir+'/works');let host,provider;
      try {
        await control.refresh();const selected=control.select(input.selection);await control.validate(selected);
        const document=blankDocument();document.metadata.name='cross-language';document.metadata.audience='male';document.sources.system='Frozen system';
        const w=await store.create({operationId:'work',expectedRevision:null,document});
        const v=await store.publish(w.workId,{operationId:'publish',expectedRevision:0});
        provider=await OfflineFixtureProvider.open(input.dir+'/provider',{replies:{one:{status:'completed',text:'raw reply',finishReason:'stop'}}});
        host=await OfflineSessionHost.open({directory:input.dir+'/sessions',resolveRuntime:r=>openPublishedRuntime(store,r),provider,modelControl:control});
        const state=(await host.create({operationId:'session',expectedRevision:null,reference:{workId:w.workId,versionId:v.result.versionId},openingText:'opening',presetId:null})).session;
        await host.configureModel({sessionId:state.id,operationId:'choose',expectedRevision:0,expectedModelRevision:0,purpose:'story',choice:input.selection});
        const result=await host.send({sessionId:state.id,operationId:'one',expectedRevision:1,expectedModelRevision:1,input:'opening'});
        console.log(JSON.stringify({state:result.operation.state,request:result.operation.request,requests,fixtureDispatches:provider.dispatchCount}));
      }finally{await host?.close();await provider?.close();await store.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
    """
    module = Path(__file__).resolve().parents[2] / "experiments/ai-rpg-engine"
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=module,
        input=json.dumps({"catalog":c,"selection":value,"checked":checked,"dir":str(tmp_path / "node")}),
        text=True, encoding="utf-8", capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["state"] == "completed" and evidence["fixtureDispatches"] == 1
    assert evidence["request"]["model"] == SCOPED_MODEL_ID
    assert evidence["request"]["parameters"] == {"max_completion_tokens":4096}
    assert evidence["request"]["modelConfiguration"]["choice"]["source"] == "control-plane-cache"
    assert [r["url"] for r in evidence["requests"]] == [AUTHORING_MODELS, AUTHORING_VALIDATE]
    assert evidence["requests"][1]["body"] == value and not captured
    (tmp_path / "cross-language-evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf-8")


def test_authoring_catalog_capacity_and_missing_output_block_without_guessing(setup, monkeypatch):
    client, stable, repo, meta, captured, _ = setup; _declared_parameters(meta)
    meta["max_output_tokens"] = None
    entry = client.get(AUTHORING_MODELS, headers=HEADERS).json()["models"][0]
    assert not entry["available"] and entry["parameterEvidence"] == "invalid"
    monkeypatch.setattr(bridge, "AUTHORING_CATALOG_LIMIT", 0)
    assert client.get(AUTHORING_MODELS, headers=HEADERS).status_code == 503
    assert not captured


def test_authoring_policy_route_removal_invalidates_snapshot(setup, monkeypatch):
    client, stable, _, meta, captured, _ = setup; _declared_parameters(meta)
    _, value = _authoring_selection(client)
    policy = stable.control.peek_policy()
    monkeypatch.setattr(stable.control,"peek_policy",lambda:policy.model_copy(update={"routes":[],"policy_fingerprint":"changed"}))
    assert client.get(AUTHORING_MODELS,headers=HEADERS).json()["models"] == []
    assert client.post(AUTHORING_VALIDATE,headers=HEADERS,json=value).status_code == 409
    assert not captured


@pytest.mark.asyncio
async def test_authoring_provider_extension_survives_actual_catalog_cache(tmp_path):
    from server.tests.test_provider_catalog_service import _service as catalog_service
    meta = {"context_length":128000,"max_output_tokens":32768}
    raw = {"id":"provider/declared-example", **_declared_parameters(meta, token_only=True)}
    service, repository, connection, requests = catalog_service(tmp_path, lambda _:httpx.Response(200,json={"data":[raw]}))
    result = await service.refresh_connection(connection)
    assert result.status == "succeeded" and len(requests) == 1 and requests[0].method == "GET"
    row = repository.list_catalog_models("local",connection_id=connection)[0]
    stored = json.loads(row["metadata_json"])
    assert stored["parameter_capabilities"] == meta["parameter_capabilities"]
    assert stored["supported_parameters"] == ["max_completion_tokens"]
    assert repository.get_latest_chat_certification("local",connection,raw["id"],"chat_text") is None
    assert repository.list_chat_control_receipts("local")["runs"] == []


def test_authoring_disabled_gate_and_unsupported_public_name(setup, monkeypatch):
    client, _, _, meta, captured, _ = setup
    monkeypatch.setenv("RPG_S2S_ENABLED","false")
    assert client.get(AUTHORING_MODELS,headers=HEADERS).status_code == 503
    assert client.post(AUTHORING_VALIDATE,headers=HEADERS,json={}).status_code == 503
    monkeypatch.setenv("RPG_S2S_ENABLED","true")
    meta["name"] = "\U0001F680" * 101
    assert client.get(AUTHORING_MODELS,headers=HEADERS).status_code == 503
    assert not captured


def test_authoring_uncertified_models_do_not_enter_credential_qualification_loop(setup, monkeypatch):
    client, stable, repo, meta, captured, _ = setup; _declared_parameters(meta)
    listing = repo.list_catalog_models
    def list_with_uncertified(*args, **kwargs):
        rows = listing(*args, **kwargs)
        if rows and not kwargs.get("model_id"):
            rows.append({**rows[0],"model_id":"provider/uncertified"})
        return rows
    qualification = stable.control.current_qualification
    def checked(**kwargs):
        assert kwargs["model_id"] != "provider/uncertified"
        return qualification(**kwargs)
    monkeypatch.setattr(repo,"list_catalog_models",list_with_uncertified)
    monkeypatch.setattr(stable.control,"current_qualification",checked)
    entries = client.get(AUTHORING_MODELS,headers=HEADERS).json()["models"]
    assert len(entries) == 2 and entries[0]["available"] and not entries[1]["available"]
    assert not captured
