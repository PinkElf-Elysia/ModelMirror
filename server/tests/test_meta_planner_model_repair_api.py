from functools import partial

import httpx
import pytest

from server.tests.test_meta_planner_model_repair import consent, setup
from server.tests.test_meta_planner_failed_artifacts import offline_only

_SEND = httpx.Client.send


async def mock_legacy_wire(tmp_path, monkeypatch, *, response_model="test/model", mutate_during_prepare=False):
    import server.main as main
    from types import SimpleNamespace
    service, _, _, _, _ = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: service.recovery.headless)
    monkeypatch.setattr(main, "get_model_router_service", lambda: None)
    monkeypatch.setattr(main.ManagedMetaAgentGateway, "for_router", lambda _: SimpleNamespace(routing_mode=lambda: "legacy"))
    config = ["https://first.invalid/api", "synthetic-key-a"]
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: tuple(config))
    calls = []

    async def prepare(messages, **_):
        if mutate_during_prepare:
            config[:] = ["https://other.invalid/api", "synthetic-key-b"]
        return SimpleNamespace(messages=messages)

    class LocalClient:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(200, json={"model": response_model,
                "choices": [{"finish_reason": "stop", "message": {"content": '{"operations":[]}'}}],
                "usage": {"total_tokens": 51}})

    monkeypatch.setattr(main, "optimize_context", prepare)
    monkeypatch.setattr(main.httpx, "AsyncClient", LocalClient)
    return main.get_explicit_model_repair_service(), config, calls


@pytest.mark.asyncio
async def test_legacy_account_rotation_invalidates_preflight(tmp_path, monkeypatch):
    wired, config, calls = await mock_legacy_wire(tmp_path, monkeypatch)
    before = wired.route_projection("test/model")
    config[1] = "synthetic-key-b"
    after = wired.route_projection("test/model")
    assert before != after, "Same endpoint with a different billing identity must invalidate consent"
    assert not calls and "synthetic-key" not in str(before) + str(after)


@pytest.mark.asyncio
async def test_legacy_route_change_during_body_preparation_is_not_dispatched(tmp_path, monkeypatch):
    from server.meta_agent.model_repair import ModelRepairCallError
    wired, _, calls = await mock_legacy_wire(tmp_path, monkeypatch, mutate_during_prepare=True)
    with pytest.raises(ModelRepairCallError) as error:
        await wired.completion("test/model", "system", "user", 1024, "call", wired.route_projection("test/model"))
    assert error.value.receipt["provider_dispatched"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_legacy_response_model_mismatch_consumes_call_but_returns_no_suggestion(tmp_path, monkeypatch):
    from server.meta_agent.model_repair import ModelRepairCallError
    wired, _, calls = await mock_legacy_wire(tmp_path, monkeypatch, response_model="different/model")
    with pytest.raises(ModelRepairCallError) as error:
        await wired.completion("test/model", "system", "user", 1024, "call", wired.route_projection("test/model"))
    assert len(calls) == 1 and error.value.receipt["provider_dispatched"]
    assert error.value.receipt["response_received"] and error.value.receipt["total_tokens"] == 51
    assert error.value.reason_code == "repair_response_model_mismatch"


@pytest.mark.asyncio
async def test_management_routes_require_confirmation_and_only_return_suggestion(tmp_path, monkeypatch):
    import server.main as main
    from fastapi.testclient import TestClient

    service, proposal, authoring, request, calls = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_explicit_model_repair_service", lambda: service)
    client = TestClient(main.app)
    monkeypatch.setattr(client, "send", partial(_SEND, client))
    base = f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}/repair"
    preflight = client.post(base + "/preflight", json=request.model_dump())
    assert preflight.status_code == 200, preflight.text
    approved = consent(request, preflight.json()).model_dump()
    assert client.get(base + "/receipts").json() == {"attempts": []}
    assert calls == []
    for fields in ({"acknowledge_external_send": False}, {"max_calls": 2}, {"test_mode": True}, {"scope": {}}):
        assert client.post(base + "/execute", json={**approved, **fields}).status_code == 422
    assert calls == []
    response = client.post(base + "/execute", json=approved)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suggested" and len(calls) == 1
    assert client.post(base + "/execute", json=approved).json() == response.json()
    assert len(calls) == 1
    history = client.get(base + "/receipts").json()
    assert len(history["attempts"]) == 1 and "patch" not in history["attempts"][0]
    loaded = client.get(base + "/receipts/" + approved["request_id"])
    assert loaded.json()["patch"] == response.json()["patch"]
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_legacy_adapter_wires_completion_validation_and_usage(tmp_path, monkeypatch):
    import server.main as main
    from server.meta_agent.completion_contract import PlannerCompletionError

    service, _, _, _, _ = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: service.recovery.headless)
    monkeypatch.setattr(main, "get_model_router_service", lambda: None)
    from types import SimpleNamespace
    monkeypatch.setattr(main.ManagedMetaAgentGateway, "for_router", lambda _: SimpleNamespace(routing_mode=lambda: "legacy"))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://test.invalid/v1/chat/completions", "never-expose"))
    received = []

    async def collector(model, messages, **kwargs):
        received.append(kwargs)
        assert kwargs["allow_json_reasoning_fallback"] is False
        kwargs["request_guard"](main.build_chat_payload_from_messages(model, messages, stream=False, temperature=0,
            max_tokens=kwargs["max_tokens"], extra={"reasoning": kwargs["reasoning"], "response_format": kwargs["response_format"]}))
        kwargs["response_observer"]({"model": model, "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "usage": {"total_tokens": 91}})
        return '{"operations":[]}'

    monkeypatch.setattr(main, "collect_chat_completion_text", collector)
    wired = main.get_explicit_model_repair_service()
    route = wired.route_projection("test/model")
    result = await wired.completion("test/model", "system", "user", 1200, "request-test", route)
    assert result["receipt"]["total_tokens"] == 91 and len(received) == 1
    assert "never-expose" not in str(result) + str(route)
    with pytest.raises(PlannerCompletionError):
        received[0]["response_observer"]({"choices": [{"finish_reason": "length"}]})


@pytest.mark.asyncio
async def test_legacy_payload_drift_is_rejected_before_http(tmp_path, monkeypatch):
    import server.main as main
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from server.meta_agent.model_repair import ModelRepairCallError

    service, _, _, _, _ = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: service.recovery.headless)
    monkeypatch.setattr(main, "get_model_router_service", lambda: None)
    monkeypatch.setattr(main.ManagedMetaAgentGateway, "for_router", lambda _: SimpleNamespace(routing_mode=lambda: "legacy"))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://test.invalid/api", "private-test"))
    monkeypatch.setattr(main, "optimize_context", AsyncMock(return_value=SimpleNamespace(messages=[{"role": "user", "content": "changed"}])))
    wired = main.get_explicit_model_repair_service()
    with pytest.raises(ModelRepairCallError) as error:
        await wired.completion("test/model", "system", "user", 1024, "call", wired.route_projection("test/model"))
    assert error.value.receipt["provider_dispatched"] is False
    assert not error.value.receipt["response_received"]
