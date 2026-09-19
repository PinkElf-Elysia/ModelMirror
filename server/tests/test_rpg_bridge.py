"""Offline RPG bridge acceptance. All provider traffic uses httpx.MockTransport."""
import json
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.model_router import rpg_bridge as bridge
from server.tests.test_provider_chat_stable_service import _service, _qualify_scoped_model, SCOPED_MODEL_ID

TOKEN = "synthetic-rpg-service-token-for-tests-only"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def stream(model=SCOPED_MODEL_ID, raw=" 原文\n\t保留 **格式** ", finish="stop", done=True):
    event = {"choices": [{"delta": {"content": raw}, "finish_reason": finish}]}
    if model is not None:
        event["model"] = model
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n" + ("data: [DONE]\n\n" if done else "")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    stable, repo, primary, _ = _service(tmp_path, monkeypatch, newapi_ip="8.8.8.8")
    _qualify_scoped_model(repo, primary)
    monkeypatch.setenv("RPG_S2S_ENABLED", "true")
    monkeypatch.setenv("RPG_S2S_TOKEN", TOKEN)
    original = repo.list_catalog_models
    metadata = {"name": "Synthetic model", "context_length": 128000, "max_output_tokens": 32768,
                "supported_parameters": ["temperature", "top_p", "max_tokens"]}
    def listing(*args, **kwargs):
        rows = original(*args, **kwargs)
        return [{**r, "metadata_json": json.dumps(metadata)} for r in rows if r["model_id"] == SCOPED_MODEL_ID]
    monkeypatch.setattr(repo, "list_catalog_models", listing)
    captured = []
    response = {"status": 200, "content": stream(), "type": "text/event-stream"}
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


def test_route_drift_during_begin_blocks_fallback(setup, monkeypatch):
    client, stable, repo, _, captured, _ = setup
    value = payload(client)
    original = stable.begin_rpg_certified
    async def changed(model):
        result = await original(model)
        return replace(result, dispatch=replace(result.dispatch, connection_fingerprint="changed"))
    monkeypatch.setattr(stable, "begin_rpg_certified", changed)
    response = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert response.status_code == 409
    assert captured == []
    assert not any(a["dispatched"] for a in repo.list_chat_control_receipts("local")["attempts"])


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
    async def changed(model):
        result = await original(model)
        meta["supported_parameters"] = []
        return result
    monkeypatch.setattr(stable, "begin_rpg_certified", changed)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=value)
    assert result.status_code == 502
    assert result.json()["receipt"]["dispatched"] is False
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
    assert registered == [('/api/rpg/v1/models', {'GET'}), ('/api/rpg/v1/chat/completions', {'POST'})]
    monkeypatch.delenv('RPG_S2S_ENABLED', raising=False)
    monkeypatch.delenv('RPG_S2S_TOKEN', raising=False)
    client = TestClient(app)
    assert client.get('/api/rpg/v1/models').status_code == 503
    assert client.post('/api/rpg/v1/chat/completions', content='{}').status_code == 503


def test_luna_exception_is_exact_and_does_not_change_other_models():
    assert bridge.parameters_for("openai/gpt-5.6-luna") == {"max_tokens": 16384}
    for model in ("google/gemini-3.8-flash", "deepseek/deepseek-v4-flash-0731", "openai/gpt-5.6-luna-pro", "unknown"):
        assert bridge.parameters_for(model) == bridge.PARAMETERS


def test_selected_effective_parameters_reach_wire_and_receipt(setup, monkeypatch):
    client, stable, repo, meta, captured, response = setup
    monkeypatch.setattr(bridge, "parameters_for", lambda model: {"max_tokens": 16384})
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
    async def begin(model):
        p = await original(model)
        return replace(p, dispatch=replace(p.dispatch, target=replace(p.dispatch.target, provider_kind="openrouter")))
    monkeypatch.setattr(stable, "begin_rpg_certified", begin)
    result = client.post("/api/rpg/v1/chat/completions", headers=HEADERS, json=payload(client))
    assert result.status_code == 200
    assert captured[0]["provider"] == {"allow_fallbacks": False, "require_parameters": True, "only": ["test-provider/endpoint"], "order": ["test-provider/endpoint"]}
