"""P1-A: typed parameter contracts and captured ASGI -> MockTransport requests.

No real credentials, qualification, provider calls, shared configuration or data.
"""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3

import httpx
import pytest

from server.model_router import rpg_bridge as bridge
from server.model_router import rpg_parameters as typed
from server.model_router import rpg_authoring_execution as tasks
from server.model_router.provider_catalog import _parameter_capabilities, normalize_provider_catalog
from server.tests.test_rpg_bridge import setup, HEADERS, _declared_parameters

MODELS = "/api/rpg/v1/authoring/models?parameterVersion=2"
CHECK = "/api/rpg/v1/authoring/parameters/validate"
TASK = "/api/rpg/v1/authoring/tasks"


def policy():
    def numeric(key, low, high, default=None, required=False):
        return {"type": typed.SPECS[key][0], "minimum": low, "maximum": high,
                "required": required, "wireName": typed.SPECS[key][3][0],
                "default": {"mode": "omit"} if default is None else {"mode": "value", "value": default}}
    fields = {
        "temperature": numeric("temperature", 0, 2), "topP": numeric("topP", 0, 1),
        "maxOutputTokens": numeric("maxOutputTokens", 1, 32768, 4096, True),
        "topK": numeric("topK", 0, 100), "frequencyPenalty": numeric("frequencyPenalty", -2, 2),
        "presencePenalty": numeric("presencePenalty", -2, 2), "seed": numeric("seed", -2147483648, 2147483647),
        "stop": {"type": "text-list", "minItems": 0, "maxItems": 4, "maxItemLength": 80,
                 "required": False, "default": {"mode": "omit"}, "wireName": "stop"},
        "thinking.enabled": {"type": "boolean", "required": False, "default": {"mode": "omit"}, "wireName": "reasoning.enabled"},
        "thinking.effort": {"type": "enum", "required": False, "default": {"mode": "omit"}, "wireName": "reasoning.effort",
                            "options": ["low", "medium", "high"]},
        "thinking.budgetTokens": numeric("thinking.budgetTokens", 0, 16384),
    }
    return {"kind": "editable", "fields": fields, "exclusive": [["temperature", "topP"]],
            "requires": [{"if": {"field": "thinking.budgetTokens", "op": "set"},
                          "then": {"field": "thinking.enabled", "op": "eq", "value": True}}],
            "lessThan": [["thinking.budgetTokens", "maxOutputTokens"]], "purposes": ["story", "summary", "memory"]}


def config(values=None):
    return {"format": typed.CONFIG_FORMAT, "values": {k: {"mode": "explicit", "value": v} for k, v in (values or {}).items()}}


def declare(meta, supplied=None):
    supplied = deepcopy(supplied or policy())
    meta["supported_parameters"] = sorted({f["wireName"].split(".")[0] for f in supplied["fields"].values()})
    raw = {**meta, "parameter_capabilities": {"format": typed.CAPABILITY_FORMAT, "policy": supplied}}
    meta["parameter_capabilities"] = _parameter_capabilities(raw)
    assert meta["parameter_capabilities"]["status"] == "declared"
    return raw


def choice(client, parameters=None, purpose="story"):
    response = client.get(MODELS, headers=HEADERS)
    assert response.status_code == 200, response.text
    catalog = response.json()
    entry = next(e for e in catalog["models"] if e["available"])
    return catalog, {"format": "modelmirror.rpg.parameter-check-request/2", "catalogRevision": catalog["revision"],
                     "selectionId": entry["selectionId"], "selectionRevision": entry["selectionRevision"],
                     "capabilityRevision": entry["capabilityRevision"], "purpose": purpose,
                     "parameters": parameters or config()}


def task(client, parameters=None, purpose="story", request="request-p1"):
    _, selected = choice(client, parameters, purpose)
    return {**selected, "format": tasks.FORMAT_V2, "sessionId": "session-p1", "requestId": request,
            "operationId": "operation-" + request, "sourceRequestHash": "c" * 64,
            "messages": [{"role": "system", "content": "冻结 system\n"}, {"role": "user", "content": "玩家原文\n后置词"}]}


def lookup(client, request):
    return client.get(TASK + "/" + request["sessionId"] + "/" + request["requestId"], headers=HEADERS,
                      params={"sourceRequestHash": request["sourceRequestHash"]})


def test_default_explicit_and_input_immutability():
    p = policy(); c = config(); original = deepcopy((p, c))
    default = typed.resolve_parameters(p, c, "story")
    explicit = typed.resolve_parameters(p, config({"temperature": 1}), "story")
    assert default["wireParameters"] == {"max_tokens": 4096}
    assert default["sources"]["temperature"] == {"mode": "default", "resolution": "omitted"}
    assert default["sources"]["maxOutputTokens"]["resolution"] == "declared-default"
    assert explicit["wireParameters"] == {"max_tokens": 4096, "temperature": 1}
    assert (p, c) == original
    default["wireParameters"]["max_tokens"] = 1
    assert p["fields"]["maxOutputTokens"]["default"]["value"] == 4096


@pytest.mark.parametrize("values,suffix", [
    ({"temperature": True}, "range"), ({"temperature": float("nan")}, "range"),
    ({"temperature": float("inf")}, "range"), ({"temperature": 2.01}, "range"),
    ({"maxOutputTokens": 1.5}, "range"), ({"maxOutputTokens": 10**1000}, "range"),
    ({"topK": -1}, "range"), ({"topK": 1.2}, "range"), ({"seed": 2**53}, "range"),
    ({"frequencyPenalty": -2.1}, "range"), ({"presencePenalty": 2.1}, "range"),
    ({"thinking.enabled": 1}, "range"), ({"thinking.effort": "xhigh"}, "range"),
    ({"stop": "STOP"}, "range"), ({"stop": [""]}, "range"), ({"stop": ["a", "a"]}, "range"),
    ({"stop": ["a" * 81]}, "range"), ({"stop": ["a", "b", "c", "d", "e"]}, "range"),
    ({"stop": ["\ud800"]}, "range"), ({"stop": [None]}, "range"),
    ({"temperature": .7, "topP": .8}, "conflict"),
    ({"thinking.effort": "low", "thinking.budgetTokens": 100}, "conflict"),
    ({"thinking.enabled": False, "thinking.effort": "low"}, "conflict"),
    ({"thinking.budgetTokens": 100}, "dependency"),
    ({"thinking.enabled": True, "thinking.budgetTokens": 4096}, "dependency"),
    ({"provider": {"order": ["untrusted"]}}, "unsupported"),
    ({"max_tokens": 10}, "unsupported"), ({"thinking": {"enabled": True}}, "unsupported"),
])
def test_invalid_values_fail_closed(values, suffix):
    with pytest.raises(typed.ParameterError, match="^rpg_authoring_parameter_" + suffix + "$"):
        typed.resolve_parameters(policy(), config(values), "story")


@pytest.mark.parametrize("key,value", [("temperature", 0), ("temperature", 2), ("topP", 0), ("topP", 1),
    ("maxOutputTokens", 1), ("maxOutputTokens", 32768), ("topK", 0), ("topK", 100),
    ("seed", -2147483648), ("seed", 2147483647), ("frequencyPenalty", -2), ("presencePenalty", 2),
    ("stop", []), ("stop", ["结束", "\n\n", "🧭"]), ("thinking.enabled", False), ("thinking.effort", "high")])
def test_allowed_boundary_values(key, value):
    assert typed.resolve_parameters(policy(), config({key: value}), "memory")["effectiveParameters"][key] == value


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(secret="not retained"),
    lambda p: p["fields"]["temperature"].update(wireName="api_key"),
    lambda p: p["fields"]["topK"].update(maximum=float("inf")),
    lambda p: p["fields"]["seed"].update(maximum=10**1000),
    lambda p: p["fields"]["stop"].update(maxItems=17),
    lambda p: p["fields"]["stop"].update(maxItemLength=1025),
    lambda p: p["fields"]["thinking.effort"].update(options=["high", "high"]),
    lambda p: p["fields"]["thinking.enabled"].update(default={"mode": "value", "value": "true"}),
    lambda p: p["fields"]["maxOutputTokens"].update(required=False),
    lambda p: p["fields"].pop("maxOutputTokens"),
    lambda p: p.update(purposes=["story", "story"]),
    lambda p: p.update(purposes=["arbitrary"]),
    lambda p: p.update(exclusive=[["temperature", "unknown"]]),
    lambda p: p.update(exclusive=[["temperature", "temperature"]]),
    lambda p: p.update(requires=[{"if": {"field": "temperature", "op": "eval", "value": "code"}, "then": {}}]),
    lambda p: p.update(lessThan=[["stop", "maxOutputTokens"]]),
    lambda p: p.update(lessThan=[["maxOutputTokens", "maxOutputTokens"]]),
])
def test_invalid_capability_is_not_repaired(mutation):
    p = policy(); mutation(p)
    with pytest.raises(ValueError, match="parameter_capabilities_invalid"):
        typed.validate_policy(p)


def test_required_unknown_default_and_purpose_restriction():
    p = policy(); p["fields"]["maxOutputTokens"]["default"] = {"mode": "omit"}
    with pytest.raises(typed.ParameterError, match="required"):
        typed.resolve_parameters(p, config(), "story")
    assert typed.resolve_parameters(p, config({"maxOutputTokens": 50}), "compression")["purpose"] == "summary"
    p["purposes"] = ["memory"]
    with pytest.raises(typed.ParameterError, match="purpose"):
        typed.resolve_parameters(p, config(), "story")


@pytest.mark.parametrize("bad", [{"format": "future", "values": {}}, {"format": typed.CONFIG_FORMAT, "values": [],},
    {"format": typed.CONFIG_FORMAT, "values": {"temperature": {"mode": "default", "value": 1}}},
    {"format": typed.CONFIG_FORMAT, "values": {"temperature": {"mode": "explicit"}}},
    {"format": typed.CONFIG_FORMAT, "values": {"temperature": {"mode": "omit"}}}])
def test_config_shape_does_not_silently_drop_fields(bad):
    with pytest.raises(typed.ParameterError, match="invalid"):
        typed.resolve_parameters(policy(), bad, "story")


def test_typed_catalog_roundtrip_and_private_mapping(setup):
    client, stable, repo, meta, captured, _ = setup
    raw = {"id": "synthetic", **declare(meta)}
    connection = repo.get_connection("local", stable.control.route_candidates(stable.control.peek_policy(), "chat_text")[0])
    records = normalize_provider_catalog([raw], connection=connection, observed_at="2026-11-05T00:00:00Z")[0]
    assert records[0]["metadata"]["parameter_capabilities"]["policy"] == policy()
    assert "reasoning" in records[0]["metadata"]["supported_parameters"]
    meta.update(records[0]["metadata"])
    c, selected = choice(client)
    text = json.dumps(c)
    assert c["format"] == "modelmirror.rpg.control-model-catalog/2"
    assert all(token not in text for token in ("wireName", "_parameterPolicy", "api_key", "base_url"))
    assert client.get("/api/rpg/v1/authoring/models?parameterVersion=3", headers=HEADERS).status_code == 422
    checked = client.post(CHECK, headers=HEADERS, json=selected).json()
    assert checked["dispatched"] is False and checked["resolution"]["wireParameters"] == {"max_tokens": 4096}
    assert not captured
    # v1 clients must not lose advanced constraints by accepting a three-field projection.
    assert not client.get("/api/rpg/v1/authoring/models", headers=HEADERS).json()["models"][0]["available"]


def test_old_policy_and_old_catalog_remain_compatible(setup):
    client, _, _, meta, captured, _ = setup
    _declared_parameters(meta)
    old = client.get("/api/rpg/v1/authoring/models", headers=HEADERS).json()
    _, selected = choice(client)
    checked = client.post(CHECK, headers=HEADERS, json=selected).json()["resolution"]
    assert checked["wireParameters"] == {"max_tokens": 4096}
    assert checked["sources"]["temperature"]["resolution"] == "omitted"
    assert client.get("/api/rpg/v1/authoring/models", headers=HEADERS).json() == old
    assert not captured


def test_preflight_is_read_only_and_stale_capability_is_blocked(setup):
    client, _, repo, meta, captured, _ = setup; declare(meta)
    def dump():
        with sqlite3.connect(repo.database_path) as db:
            return list(db.iterdump())
    before = dump(); _, selected = choice(client)
    assert client.post(CHECK, headers=HEADERS, json=selected).status_code == 200
    assert dump() == before
    stale = deepcopy(selected); stale["capabilityRevision"] = "f" * 64
    response = client.post(CHECK, headers=HEADERS, json=stale)
    assert response.status_code == 409 and response.json()["detail"] == "rpg_authoring_capability_changed"
    meta["parameter_capabilities"]["policy"]["fields"]["seed"]["maximum"] = 123
    assert client.post(CHECK, headers=HEADERS, json=selected).status_code == 409
    assert not captured and not (repo.storage_dir / "rpg-authoring-tasks").exists()


@pytest.mark.parametrize("purpose", ["story", "summary", "compression", "memory"])
def test_real_execution_builds_exact_typed_payload_and_recovers_once(setup, monkeypatch, purpose):
    client, _, repo, meta, captured, _ = setup; declare(meta)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    value = task(client, config({"temperature": .7, "topK": 40, "seed": 12, "frequencyPenalty": -.5,
        "presencePenalty": .5, "stop": ["结束", "\n🧭"], "thinking.enabled": True, "thinking.budgetTokens": 1000}), purpose)
    result = client.post(TASK, headers=HEADERS, json=value)
    assert result.status_code == 200, result.text
    body = result.json(); receipt = body["receipt"]
    assert body["status"] == "completed", body
    assert receipt["format"] == "modelmirror.rpg.authoring-task-receipt/2"
    assert receipt["parameters"] == {"temperature": .7, "max_tokens": 4096, "top_k": 40, "seed": 12,
        "frequency_penalty": -.5, "presence_penalty": .5, "stop": ["结束", "\n🧭"], "reasoning": {"enabled": True, "max_tokens": 1000}}
    assert captured[0]["messages"] == value["messages"] and "top_p" not in captured[0]
    assert receipt["providerRequestHash"] == bridge.digest(captured[0])
    assert receipt["parameterResolution"]["capabilityRevision"] == value["capabilityRevision"]
    assert receipt["parameterResolution"]["purpose"] == ("summary" if purpose == "compression" else purpose)
    assert receipt["sha256"] == bridge.digest({k: v for k, v in receipt.items() if k != "sha256"})
    assert lookup(client, value).json() == body
    assert client.post(TASK, headers=HEADERS, json=value).json() == body and len(captured) == 1
    assert len(list(repo.storage_dir.glob("rpg-authoring-tasks/*.claim.json"))) == 1
    (repo.storage_dir / "captured-typed-request.json").write_text(
        json.dumps({"request": value, "wirePayload": captured[0], "result": body}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_explicit_default_value_is_frozen_differently_without_extra_dispatch(setup, monkeypatch):
    client, _, _, meta, captured, _ = setup; declare(meta)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    a = task(client); b = task(client, config({"maxOutputTokens": 4096}), request="explicit")
    one = client.post(TASK, headers=HEADERS, json=a).json()
    two = client.post(TASK, headers=HEADERS, json=b).json()
    assert one["receipt"]["parameters"] == two["receipt"]["parameters"]
    assert one["requestHash"] != two["requestHash"]
    assert one["receipt"]["parameterResolution"]["sources"]["maxOutputTokens"]["mode"] == "default"
    assert two["receipt"]["parameterResolution"]["sources"]["maxOutputTokens"]["mode"] == "explicit"
    b["requestId"] = a["requestId"]
    assert client.post(TASK, headers=HEADERS, json=b).status_code == 409
    assert len(captured) == 2


@pytest.mark.parametrize("change,code", [("capacity", "context_capacity"), ("capability", "capability_changed"),
    ("dependency", "parameter_dependency"), ("purpose", "parameter_purpose"), ("field", "parameter_unsupported")])
def test_rejected_task_is_durable_and_not_a_provider_call(setup, monkeypatch, change, code):
    client, _, _, meta, captured, _ = setup; p = policy()
    if change == "purpose": p["purposes"] = ["memory"]
    declare(meta, p); monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    value = task(client)
    if change == "capacity": value["messages"][1]["content"] = "x" * 128000
    if change == "capability": value["capabilityRevision"] = "f" * 64
    if change == "dependency": value["parameters"] = config({"thinking.budgetTokens": 100})
    if change == "field": value["parameters"] = config({"providerUrl": "https://untrusted.invalid"})
    body = client.post(TASK, headers=HEADERS, json=value).json()
    assert body["status"] == "failed", body
    assert body["receipt"]["error"] == "rpg_authoring_" + code
    assert body["receipt"]["dispatched"] is False and not captured
    assert lookup(client, value).json() == body
    assert client.post(TASK, headers=HEADERS, json=value).json() == body


def test_unknown_result_never_redispatches(setup, monkeypatch):
    client, stable, _, meta, captured, _ = setup; declare(meta)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    attempts = []
    async def lost(*args):
        attempts.append(True)
        raise httpx.ReadTimeout("synthetic")
    monkeypatch.setattr(stable.transport, "send_authorized_stream", lost)
    value = task(client); body = client.post(TASK, headers=HEADERS, json=value).json()
    assert body["status"] == "unknown" and body["receipt"]["dispatched"] is True
    assert body["receipt"]["parameterResolution"]["wireParameters"] == {"max_tokens": 4096}
    assert body["receipt"]["providerRequestHash"]
    assert lookup(client, value).json() == body
    assert client.post(TASK, headers=HEADERS, json=value).json() == body and len(attempts) == 1
    assert not captured


def test_openrouter_mapping_pins_route_and_records_effective_payload(setup, monkeypatch):
    client, stable, _, meta, captured, _ = setup; declare(meta)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    original = stable.begin_rpg_authoring_certified
    async def begin(model, connection):
        preflight = await original(model, connection)
        return replace(preflight, dispatch=replace(preflight.dispatch, target=replace(preflight.dispatch.target, provider_kind="openrouter")))
    monkeypatch.setattr(stable, "begin_rpg_authoring_certified", begin)
    value = task(client, config({"thinking.effort": "high"}))
    body = client.post(TASK, headers=HEADERS, json=value).json()
    assert body["status"] == "completed", body
    assert captured[0]["reasoning"] == {"effort": "high"}
    assert captured[0]["provider"] == {"allow_fallbacks": False, "require_parameters": True}
    assert captured[0]["transforms"] == []
    assert body["receipt"]["providerRequestHash"] == bridge.digest(captured[0])


def test_declared_default_does_not_bypass_constraints_and_fixed_policy_cannot_change():
    p = policy()
    p["fields"]["temperature"]["default"] = {"mode": "value", "value": .7}
    with pytest.raises(typed.ParameterError, match="conflict"):
        typed.resolve_parameters(p, config({"topP": .8}), "story")
    fixed = {"kind": "fixed", "fields": {"maxOutputTokens": policy()["fields"]["maxOutputTokens"]},
             "exclusive": [], "requires": [], "lessThan": [], "purposes": ["story"]}
    assert typed.resolve_parameters(fixed, config(), "story")["wireParameters"] == {"max_tokens": 4096}
    with pytest.raises(typed.ParameterError, match="range"):
        typed.resolve_parameters(fixed, config({"maxOutputTokens": 8192}), "story")


def test_precise_reasoning_support_survives_catalog_normalization(setup):
    _, stable, repo, meta, _, _ = setup; raw = {"id": "precise", **declare(meta)}
    raw["supported_parameters"] = [f["wireName"] for f in policy()["fields"].values()]
    connection = repo.get_connection("local", stable.control.route_candidates(stable.control.peek_policy(), "chat_text")[0])
    records = normalize_provider_catalog([raw], connection=connection, observed_at="2026-11-05T00:00:00Z")[0]
    evidence = records[0]["metadata"]
    assert "reasoning.max_tokens" in evidence["supported_parameters"]
    assert bridge._authoring_policy(evidence, "precise", 2) == (policy(), "provider-declared")
    malformed = deepcopy(raw)
    malformed["supported_parameters"].remove("reasoning.max_tokens")
    assert _parameter_capabilities(malformed) == {"status": "invalid"}
    ambiguous = deepcopy(raw); ambiguous["parameter_capabilities"]["policy"]["fields"]["seed"]["maximum"] = 100
    assert normalize_provider_catalog([raw, ambiguous], connection=connection, observed_at="2026-11-05T00:00:00Z")[0][0]["metadata"]["parameter_capabilities"] == {"status": "invalid"}


@pytest.mark.parametrize("headers,status", [({}, 401), ({**HEADERS, "Origin": "http://localhost"}, 403)])
def test_v2_auth_precedes_catalog_or_dispatch(setup, monkeypatch, headers, status):
    client, stable, _, meta, captured, _ = setup; declare(meta)
    value = task(client)
    monkeypatch.setattr(stable.control, "peek_policy", lambda: pytest.fail("unauthorized evidence access"))
    assert client.get(MODELS, headers=headers).status_code == status
    assert client.post(CHECK, headers=headers, json={}).status_code == status
    assert client.post(TASK, headers=headers, json=value).status_code == status
    assert not captured


def test_max_completion_tokens_mapping_has_no_max_tokens_fallback(setup, monkeypatch):
    client, _, _, meta, captured, _ = setup; p = policy()
    p["fields"]["maxOutputTokens"]["wireName"] = "max_completion_tokens"
    p["fields"]["thinking.effort"]["wireName"] = "reasoning_effort"
    declare(meta, p); monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    value = task(client, config({"thinking.effort": "low"}))
    body = client.post(TASK, headers=HEADERS, json=value).json()
    assert body["status"] == "completed", body
    assert captured[0]["max_completion_tokens"] == 4096 and "max_tokens" not in captured[0]
    assert captured[0]["reasoning_effort"] == "low" and "reasoning" not in captured[0]


def test_model_metadata_drift_before_dispatch_stops_original_frozen_request(setup, monkeypatch):
    client, stable, _, meta, captured, _ = setup; declare(meta)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    original = stable.begin_rpg_authoring_certified
    async def begin(model, connection):
        preflight = await original(model, connection)
        meta["parameter_capabilities"]["policy"]["fields"]["maxOutputTokens"]["default"]["value"] = 8192
        return preflight
    monkeypatch.setattr(stable, "begin_rpg_authoring_certified", begin)
    value = task(client); body = client.post(TASK, headers=HEADERS, json=value).json()
    assert body["status"] == "failed" and body["receipt"]["dispatched"] is False
    assert body["receipt"]["error"] == "rpg_authoring_selection_changed"
    assert body["receipt"]["parameterResolution"]["effectiveParameters"]["maxOutputTokens"] == 4096
    assert not captured and lookup(client, value).json() == body


def test_v2_result_persistence_failure_stays_unknown_after_new_app(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    client, stable, _, meta, captured, _ = setup; declare(meta)
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "true")
    def disk_full(*_):
        raise OSError("synthetic disk full")
    monkeypatch.setattr(tasks, "_persist", disk_full)
    value = task(client)
    assert client.post(TASK, headers=HEADERS, json=value).json()["status"] == "unknown"
    app = FastAPI(); app.include_router(bridge.router)
    app.dependency_overrides[bridge.get_stable] = lambda: stable
    restarted = TestClient(app)
    assert lookup(restarted, value).json()["status"] == "unknown"
    assert restarted.post(TASK, headers=HEADERS, json=value).json()["status"] == "unknown"
    assert len(captured) == 1


def test_conditional_mutual_exclusion_uses_values_without_rewriting_them():
    p = policy()
    p["requires"].append({"if": {"field": "thinking.enabled", "op": "eq", "value": True},
                          "then": {"field": "temperature", "op": "unset"}})
    assert typed.resolve_parameters(p, config({"thinking.enabled": False, "temperature": .7}), "story")["effectiveParameters"]["temperature"] == .7
    with pytest.raises(typed.ParameterError, match="dependency"):
        typed.resolve_parameters(p, config({"thinking.enabled": True, "temperature": .7}), "story")
    assert typed.resolve_parameters(p, config({"thinking.enabled": True}), "story")["wireParameters"]["reasoning"] == {"enabled": True}
