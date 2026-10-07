from types import SimpleNamespace

import httpx
import pytest

from server.meta_agent.headless_authoring import HeadlessAuthoringConflictError, HeadlessAuthoringError
from server.meta_agent.managed_gateway import ManagedMetaAgentGateway
from server.meta_agent.model_repair import ModelRepairCallError
from server.meta_agent.model_repair_transport import ModelRepairTransport
from server.tests.test_meta_agent_managed_gateway import MODEL_ID, _activate_policy, _qualified_router


@pytest.mark.asyncio
async def test_legacy_timeout_is_unknown_and_never_retried():
    calls = []

    async def failing(*args):
        calls.append(args)
        raise TimeoutError("secret transport detail")

    transport = ModelRepairTransport(SimpleNamespace(routing_mode=lambda: "legacy"),
        legacy_config=lambda: ("https://test.invalid/v1/chat/completions", "synthetic"), legacy_completion=failing)
    route = transport.route_projection("test/model")
    with pytest.raises(ModelRepairCallError) as error:
        await transport.complete("test/model", "system", "prompt", 1024, "one-call", route)
    assert len(calls) == 1
    assert error.value.receipt["provider_dispatched"] is None
    assert not error.value.receipt["response_received"]
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_changed_route_and_degraded_managed_never_use_legacy():
    calls = []

    async def legacy(*args):
        calls.append(args)

    gateway = SimpleNamespace(routing_mode=lambda: "legacy")
    transport = ModelRepairTransport(gateway, legacy_config=lambda: ("https://first.invalid/api", "synthetic"), legacy_completion=legacy)
    route = transport.route_projection("test/model")
    transport.legacy_config = lambda: ("https://other.invalid/api", "synthetic")
    with pytest.raises(HeadlessAuthoringError):
        await transport.complete("test/model", "s", "u", 1024, "call", route)
    gateway.routing_mode = lambda: "degraded_required"
    with pytest.raises(HeadlessAuthoringError):
        transport.route_projection("test/model")
    assert not calls


@pytest.mark.asyncio
async def test_real_managed_adapter_has_one_mock_dispatch_and_actual_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    router, connection = await _qualified_router(tmp_path)
    _activate_policy(router, connection)
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"model": MODEL_ID, "choices": [{"finish_reason": "stop", "message": {"content": '{"operations":[]}'}}],
                                       "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

    gateway = ManagedMetaAgentGateway.for_router(router, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    async def forbidden(*_):
        raise AssertionError("NO_LEGACY_FALLBACK")
    transport = ModelRepairTransport(gateway, legacy_config=lambda: ("", ""), legacy_completion=forbidden)
    route = transport.route_projection(MODEL_ID)
    result = await transport.complete(MODEL_ID, "system", "prompt", 1024, "explicit-one", route)
    assert len(calls) == 1 and result["receipt"]["total_tokens"] == 120
    assert result["receipt"]["response_received"] and result["receipt"]["provider_dispatched"]
    with pytest.raises(HeadlessAuthoringError):
        transport.route_projection("unbound/model")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_managed_basis_guard_runs_after_client_entry_before_network_send(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_META_AGENT_ENABLED", "true")
    router, connection = await _qualified_router(tmp_path)
    _activate_policy(router, connection)
    calls, state = [], {"changed": False}

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"model": MODEL_ID, "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})

    class ChangedClient(httpx.AsyncClient):
        async def __aenter__(self):
            client = await super().__aenter__()
            state["changed"] = True
            return client

    def guard():
        if state["changed"]:
            raise HeadlessAuthoringConflictError("合成修复依据已变化。")

    async def forbidden(*_):
        raise AssertionError("NO_LEGACY_FALLBACK")
    gateway = ManagedMetaAgentGateway.for_router(router, client_factory=lambda: ChangedClient(transport=httpx.MockTransport(handle)))
    transport = ModelRepairTransport(gateway, legacy_config=lambda: ("", ""), legacy_completion=forbidden)
    with pytest.raises(ModelRepairCallError) as error:
        await transport.complete(MODEL_ID, "system", "prompt", 1024, "guarded-one", transport.route_projection(MODEL_ID), guard)
    assert not calls
    assert error.value.receipt["provider_dispatched"] is False
    assert error.value.reason_code == "repair_basis_changed"
