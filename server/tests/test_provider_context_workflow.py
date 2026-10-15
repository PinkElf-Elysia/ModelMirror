"""Real repository/gateway provenance checks; all Provider I/O is mocked."""
from dataclasses import replace

import httpx
import pytest

from server.model_router.call_context import ProviderCallContextError
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workflow_gateway import ManagedWorkflowGateway, ManagedWorkflowRoutingError
from server.tests.test_workflow_managed_gateway import _qualified_router, _activate, MODEL_ID


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_XPERT_ENABLED", "true")
    service, connection = _qualified_router(tmp_path)
    _activate(service, connection, "xpert", shapes=("chat_text", "chat_tools", "chat_json_object"))
    yield service
    service.repository.close()


def gateway(service, posts):
    def handler(request):
        posts.append(request.method)
        return httpx.Response(200, json={"model": MODEL_ID, "choices": [{"message": {"content": '{"answer":"synthetic answer"}'}}]})
    return ManagedWorkflowGateway.for_router(service, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False))


def test_unbound_gateway_cannot_mint_context(configured):
    with pytest.raises(ManagedWorkflowRoutingError, match="origin_missing"):
        gateway(configured, []).start_node_run(source_kind="xpert_chat", execution_reference="task", node_id="llm")
    assert configured.repository.list_workload_receipts("local")["calls"] == []


@pytest.mark.parametrize("agent", [False, True])
def test_bound_origin_cannot_be_reused_for_another_task(configured, agent):
    adapter = gateway(configured, [])
    adapter.bind_execution(execution_id="exec", task_id="task", source_kind="xpert_chat")
    start = adapter.start_agent_run if agent else adapter.start_node_run
    with pytest.raises(ManagedWorkflowRoutingError, match="reference_mismatch"):
        start(source_kind="xpert_chat", execution_reference="another-task", node_id="llm")
    assert configured.repository.list_workload_receipts("local")["calls"] == []


def test_client_wait_namespace_keeps_origin_and_blocks_duplicate(configured):
    adapter = gateway(configured, [])
    adapter.bind_execution(execution_id="exec", task_id="task", source_kind="xpert_chat")
    kwargs = dict(source_kind="xpert_chat", execution_reference="task", node_id="agent", client_wait_id="wait-1")
    run = adapter.start_agent_run(**kwargs)
    with configured.repository._connect() as db:
        origin = db.execute("SELECT execution_id FROM provider_workload_run_origins WHERE tenant_id='local' AND run_id=?",
                            (run.run_id,)).fetchone()
        assert origin[0] == "exec"
    with pytest.raises(ManagedWorkflowRoutingError, match="replay_blocked"):
        adapter.start_agent_run(**kwargs)
    assert configured.repository.list_workload_receipts("local")["calls"] == []


def test_client_wait_namespace_cannot_bypass_legacy_run_evidence(configured):
    adapter = gateway(configured, [])
    adapter.bind_execution(execution_id="exec", task_id="task", source_kind="xpert_chat")
    kwargs = dict(source_kind="xpert_chat", execution_reference="task", node_id="agent")
    old = adapter.start_agent_run(**kwargs)
    with pytest.raises(ManagedWorkflowRoutingError, match="replay_blocked"):
        adapter.start_agent_run(**kwargs, client_wait_id="wait-1")
    with configured.repository._connect() as db:
        assert [row[0] for row in db.execute("SELECT id FROM provider_workload_runs WHERE tenant_id='local'")] == [old.run_id]
        assert db.execute("SELECT COUNT(*) FROM provider_workload_run_origins WHERE tenant_id='local'").fetchone()[0] == 1


def test_client_wait_namespace_still_requires_trusted_origin(configured):
    with pytest.raises(ManagedWorkflowRoutingError, match="origin_missing"):
        gateway(configured, []).start_agent_run(source_kind="xpert_chat", execution_reference="task",
                                               node_id="agent", client_wait_id="wait-1")
    with configured.repository._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM provider_workload_runs WHERE tenant_id='local'").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_child_inherits_saved_actor_and_call_is_durable(configured):
    store = ProviderContextStore(configured.repository)
    store.register_execution(execution_id="parent", task_id="parent-task", source_kind="xpert_chat")
    posts = []
    adapter = gateway(configured, posts)
    adapter.bind_execution(execution_id="child", task_id="child-task", source_kind="xpert_chat", parent_execution_id="parent")
    run = adapter.start_node_run(source_kind="xpert_chat", execution_reference="child-task", node_id="llm")
    answer = await run.complete_json_object(logical_call_key="synthetic-query", call_sequence=1,
                                         model_id=MODEL_ID, messages=[{"role": "user", "content": "PRIVATE-CONTENT-MARKER"}],
                                         temperature=0, max_tokens=16)
    assert answer == '{"answer":"synthetic answer"}'
    assert posts == ["POST"]
    rows = configured.repository.list_workload_receipts("local")
    context = store.read("local", rows["calls"][0]["id"])
    assert (context.actor_kind, context.actor_reference, context.purpose) == ("local_anonymous", "local-anonymous", "user_run")
    assert context.entry_id == "xpert"
    assert context.parent_run_id == run.run_id
    with configured.repository._connect() as db:
        origin = db.execute("SELECT execution_id FROM provider_workload_run_origins WHERE tenant_id='local' AND run_id=?", (run.run_id,)).fetchone()
        assert origin[0] == "child"
        assert "PRIVATE-CONTENT-MARKER" not in str(db.execute("SELECT * FROM provider_call_contexts").fetchall())
    assert "actor_reference" not in str(run.receipt_summary())


@pytest.mark.asyncio
async def test_restart_keeps_source_but_never_replays_uncertain(configured, tmp_path):
    adapter = gateway(configured, [])
    adapter.bind_execution(execution_id="before", task_id="task", source_kind="xpert_chat")
    run = adapter.start_node_run(source_kind="xpert_chat", execution_reference="task", node_id="llm")
    prepared = await adapter.call_service.prepare_call(run_id=run.run_id, entry_id="xpert",
        execution_shape="chat_json_object", model_id=MODEL_ID, logical_call_key="round", call_sequence=1)
    adapter.call_service.mark_dispatched(prepared)
    configured.repository.close()
    reopened = SQLiteRouterRepository.open(tmp_path, master_key=b"x" * 32)
    try:
        restored = gateway(ModelRouterService(reopened, egress_policy=configured.egress_policy), [])
        restored.validate_resume_source(execution_id="before", task_id="task", source_kind="xpert_chat")
        restored.bind_execution(execution_id="after", task_id="task", source_kind="xpert_chat", resume_from="before")
        assert ProviderContextStore(reopened).read("local", prepared.call_id) == prepared.call_context
        assert reopened.get_workload_run("local", run.run_id)["status"] == "uncertain"
        with pytest.raises(ManagedWorkflowRoutingError, match="replay_blocked"):
            restored.start_node_run(source_kind="xpert_chat", execution_reference="task", node_id="llm")
        with pytest.raises(RouterServiceError):
            restored.call_service.mark_dispatched(prepared)
        with pytest.raises(ManagedWorkflowRoutingError, match="reference_mismatch"):
            restored.validate_resume_source(execution_id="after", task_id="task", source_kind=None)
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_context_tampering_and_write_failure_never_dispatch(configured, monkeypatch):
    adapter = gateway(configured, [])
    adapter.bind_execution(execution_id="exec", task_id="task", source_kind="xpert_chat")
    run = adapter.start_node_run(source_kind="xpert_chat", execution_reference="task", node_id="llm")
    prepared = await adapter.call_service.prepare_call(run_id=run.run_id, entry_id="xpert",
        execution_shape="chat_json_object", model_id=MODEL_ID, logical_call_key="round", call_sequence=1)
    with pytest.raises(RouterServiceError) as error:
        adapter.call_service.mark_dispatched(replace(prepared, call_context=replace(prepared.call_context, purpose="evaluation")))
    assert error.value.code == "provider_call_context_conflict"
    assert not configured.repository.list_workload_receipts("local")["calls"][0]["dispatched"]
    import server.model_router.repository as module
    def fail_save(*args, **kwargs):
        raise ProviderCallContextError("provider_call_context_storage_invalid")
    monkeypatch.setattr(module, "save_context", fail_save)
    with pytest.raises(ProviderCallContextError, match="storage_invalid"):
        await adapter.call_service.prepare_call(run_id=run.run_id, entry_id="xpert",
            execution_shape="chat_json_object", model_id=MODEL_ID, logical_call_key="second-round", call_sequence=2)
    assert len(configured.repository.list_workload_receipts("local")["calls"]) == 1


@pytest.mark.asyncio
async def test_missing_durable_call_context_cannot_downgrade_to_unadapted(configured):
    adapter = gateway(configured, [])
    adapter.bind_execution(execution_id="exec", task_id="task", source_kind="xpert_chat")
    run = adapter.start_node_run(source_kind="xpert_chat", execution_reference="task", node_id="llm")
    prepared = await adapter.call_service.prepare_call(run_id=run.run_id, entry_id="xpert",
        execution_shape="chat_json_object", model_id=MODEL_ID, logical_call_key="round", call_sequence=1)
    # Deliberate corruption of this test's synthetic DB, never a recovery operation.
    with configured.repository._connect() as db:
        db.execute("DELETE FROM provider_call_contexts WHERE tenant_id='local' AND attempt_id=?", (prepared.call_id,))
    with pytest.raises(RouterServiceError) as error:
        adapter.call_service.mark_dispatched(replace(prepared, call_context=None))
    assert error.value.code == "provider_call_context_origin_missing"
    assert not configured.repository.list_workload_receipts("local")["calls"][0]["dispatched"]
