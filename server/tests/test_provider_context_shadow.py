"""Shadow host provenance with real SQLite and a no-network transport."""
import json
from types import SimpleNamespace

import httpx
import pytest

from server.agent_upstream.managed_gateway import ManagedShadowGateway
from server.agent_upstream.models import EngineShadowRunCreate
from server.agent_upstream.port import EngineRequest
from server.agent_upstream.service import EngineShadowService
from server.agent_upstream.store import EngineShadowStore
from server.agent_workspace.gateway import GatewayRequestError
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import ProviderWorkloadCallService
from server.tests.test_agent_upstream_managed_gateway import (
    MODEL_ID, _qualified_router, _activate_shadow_policy,
)


def gateway(tmp_path, monkeypatch, posts):
    service, connection_id = _qualified_router(tmp_path)
    monkeypatch.setenv("MODEL_CONTROL_AGENT_SHADOW_ENABLED", "true")
    _activate_shadow_policy(service, connection_id)
    def handler(request):
        posts.append(request.method)
        event = {"model": MODEL_ID, "choices": [{"delta": {"content": "synthetic"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
    return ManagedShadowGateway(ProviderWorkloadCallService(service),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False))


async def turn(adapter, run_id, logical_key="worker:1"):
    return await adapter.stream_turn(workload_run_id=run_id, origin_execution_id="shadow-host-1", logical_call_key=logical_key,
        call_sequence=int(logical_key.split(":")[-1]), model_id=MODEL_ID, messages=[{"role": "user", "content": "PRIVATE-SHADOW"}],
        tools=[], max_tokens=32, thinking_level="medium", timeout_ms=1000, on_delta=lambda *_: None)


@pytest.mark.asyncio
async def test_shadow_persists_host_source_and_each_worker_call(tmp_path, monkeypatch):
    posts = []
    adapter = gateway(tmp_path, monkeypatch, posts)
    repo = adapter.call_service.repository
    try:
        run_id = adapter.start_run(parent_run_reference="shadow-host-1")
        await turn(adapter, run_id)
        await turn(adapter, run_id, "worker:2")
        calls = repo.list_workload_receipts("local")["calls"]
        contexts = [ProviderContextStore(repo).read("local", c["id"]) for c in calls]
        assert len(contexts) == len(posts) == 2
        assert all((c.entry_id, c.actor_kind, c.purpose) == ("agent_shadow", "local_anonymous", "user_run") for c in contexts)
        assert len({c.logical_call_id for c in contexts}) == 2
        assert ProviderContextStore(repo).require_workload_source(run_id, source_kind="agent_shadow")["execution_id"] == "shadow-host-1"
        with pytest.raises(GatewayRequestError):
            await turn(adapter, run_id)
        with pytest.raises(RouterServiceError):
            adapter.start_run(parent_run_reference="shadow-host-1")
        assert len(posts) == 2
        with repo._connect() as db:
            rows = [tuple(row) for row in db.execute("SELECT * FROM provider_call_contexts")]
            assert "PRIVATE-SHADOW" not in str(rows)
    finally:
        repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "actor", "source", "unbound_run"])
async def test_shadow_rejects_missing_or_forged_origin_before_post(tmp_path, monkeypatch, damage):
    posts = []
    adapter = gateway(tmp_path, monkeypatch, posts)
    repo = adapter.call_service.repository
    try:
        run_id = adapter.start_run(parent_run_reference="shadow-host-1")
        if damage == "unbound_run":
            run_id = adapter.call_service.start_run("agent_shadow")
        else:
            with repo._connect() as db:
                if damage == "missing":
                    db.execute("DELETE FROM provider_workload_run_origins")
                elif damage == "actor":
                    db.execute("UPDATE provider_execution_origins SET actor_reference='forged-admin'")
                else:
                    db.execute("UPDATE provider_execution_origins SET source_kind='expert_team_agency'")
        with pytest.raises(GatewayRequestError):
            await turn(adapter, run_id)
        assert posts == []
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_shadow_restart_retains_source_and_does_not_reopen_run(tmp_path, monkeypatch):
    adapter = gateway(tmp_path, monkeypatch, [])
    repo = adapter.call_service.repository
    run_id = adapter.start_run(parent_run_reference="shadow-host-1")
    prepared = await adapter.call_service.prepare_call(run_id=run_id, entry_id="agent_shadow",
        execution_shape="chat_tools", model_id=MODEL_ID, logical_call_key="worker:1", call_sequence=1)
    adapter.call_service.mark_dispatched(prepared)
    repo.close()
    with SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32) as restored:
        resumed = ManagedShadowGateway(ProviderWorkloadCallService(ModelRouterService(restored)))
        assert ProviderContextStore(restored).read("local", prepared.call_id) == prepared.call_context
        assert restored.get_workload_run("local", run_id)["status"] == "uncertain"
        with pytest.raises(RouterServiceError):
            resumed.start_run(parent_run_reference="shadow-host-1")


@pytest.mark.asyncio
async def test_shadow_lost_host_mapping_cannot_fall_back_or_accept_other_run(tmp_path):
    from server.tests.test_agent_upstream_service import _FakeGateway
    legacy = _FakeGateway()
    service = EngineShadowService(store=EngineShadowStore(tmp_path), gateway=legacy)
    service._histories["host-1"] = [{"role": "system", "content": "synthetic"}]
    request = EngineRequest(run_id="host-1", request_id="worker:1", payload={"new_messages": []})
    # No restored history/mapping grants a fresh model attempt.
    model = SimpleNamespace(invocation_id=MODEL_ID)
    await service._execute_model(request, model, expected_run_id="host-1", managed_required=True)
    await service._execute_model(request, model, expected_run_id="host-2", managed_required=True)
    assert legacy.messages == []


@pytest.mark.asyncio
async def test_shadow_cross_wired_valid_run_is_blocked_before_post(tmp_path, monkeypatch):
    from server.tests.test_agent_upstream_service import _FakeGateway
    posts = []
    adapter = gateway(tmp_path / "router", monkeypatch, posts)
    legacy = _FakeGateway()
    service = EngineShadowService(store=EngineShadowStore(tmp_path / "workspace"),
        gateway=legacy, managed_gateway=adapter,
        catalog_provider=lambda: SimpleNamespace(models=[]))
    try:
        model = await service.resolve_model(MODEL_ID, allow_managed_exact=True)
        host = service.store.create_run(EngineShadowRunCreate(objective="synthetic", model_base_id=MODEL_ID), model)
        adapter.start_run(parent_run_reference=host.run_id)
        other = adapter.start_run(parent_run_reference="other-host")
        service._managed_runs[host.run_id] = other
        service._histories[host.run_id] = [{"role": "system", "content": "synthetic"}]
        result = await service._execute_model(
            EngineRequest(run_id=host.run_id, request_id="worker:1", payload={"new_messages": []}),
            model, expected_run_id=host.run_id, managed_required=True)
        assert posts == []
        assert legacy.messages == []
        assert result["outcome"]["status"] != "completed"
        assert adapter.call_service.repository.list_workload_receipts("local")["calls"] == []
    finally:
        adapter.call_service.repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, "", "other-host"])
async def test_shadow_requires_exact_host_origin_argument(tmp_path, monkeypatch, origin):
    posts = []
    adapter = gateway(tmp_path, monkeypatch, posts)
    try:
        run_id = adapter.start_run(parent_run_reference="shadow-host-1")
        with pytest.raises(GatewayRequestError):
            await adapter.stream_turn(workload_run_id=run_id, origin_execution_id=origin,
                logical_call_key="worker:1", call_sequence=1, model_id=MODEL_ID,
                messages=[], tools=[], max_tokens=32, thinking_level="medium",
                timeout_ms=1000, on_delta=lambda *_: None)
        assert posts == []
    finally:
        adapter.call_service.repository.close()


@pytest.mark.asyncio
async def test_shadow_host_to_sqlite_preserves_workspace_and_private_worker_boundary(tmp_path, monkeypatch):
    from server.tests.test_agent_upstream_service import _CandidatePort, _FakeGateway, _wait_terminal
    posts = []
    adapter = gateway(tmp_path / "router", monkeypatch, posts)
    legacy = _FakeGateway()
    service = EngineShadowService(store=EngineShadowStore(tmp_path / "workspace"),
        port=_CandidatePort(), gateway=legacy, managed_gateway=adapter,
        catalog_provider=lambda: SimpleNamespace(models=[]))
    try:
        created = await service.create_run(EngineShadowRunCreate(objective="synthetic build", model_base_id=MODEL_ID))
        terminal = await _wait_terminal(service, created.run_id)
        assert terminal.status == "candidate_ready"
        assert posts == ["POST"]
        assert legacy.messages == []
        origin = ProviderContextStore(adapter.call_service.repository).execution("local", created.run_id)
        assert origin["source_kind"] == "agent_shadow"
        assert origin["task_id"] == created.run_id
        assert (service.store.workspace(created.run_id) / "index.html").is_file()
        assert "actor_reference" not in terminal.model_dump_json()
        # Restart never resumes an old Shadow Worker or deletes its workspace.
        restored_store = EngineShadowStore(tmp_path / "workspace")
        assert restored_store.get_run(created.run_id).status == "candidate_ready"
        assert (restored_store.workspace(created.run_id) / "index.html").is_file()
    finally:
        await service.shutdown()
        adapter.call_service.repository.close()
