"""B2 + Provider origin integration. Real stores, synthetic HTTP transport only."""
from __future__ import annotations

import asyncio
import contextvars
import json
from types import SimpleNamespace

import httpx
import pytest

import server.main as main
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.workflow_gateway import ManagedWorkflowGateway
from server.tests.test_workflow_managed_gateway import _qualified_router, _activate, MODEL_ID
from server.tests.test_workflow_r8a_budget_journal import LIMITS, initialize
from server.tests.test_workflow_r8a_budget_tree import budget
from server.tests.test_workflow_r8a_handoff_budget import setup
from server.tests.test_workflow_r8b_entry_evidence import publish, invoke_data, wire, reopen
from server.tests.test_workflow_subworkflows import model_callable_workflow
from server.workflow_deployments import WorkflowDeploymentStore
from server.xpert_runtime.execution_budget import use_execution_budget


@pytest.fixture(params=["workflow_classic", "workflow_deployment"])
def runtime(request, tmp_path, monkeypatch):
    source = request.param
    executions, _, _, _ = setup(tmp_path, monkeypatch)
    service, connection = _qualified_router(tmp_path / "router")
    monkeypatch.setattr(main, "get_model_router_service", lambda: service)
    for flag in ("MODEL_CONTROL_WORKFLOW_LLM_ENABLED", "MODEL_CONTROL_WORKFLOW_DEPLOYMENT_ENABLED"):
        monkeypatch.setenv(flag, "true")
    for entry in ("workflow_interactive_llm", "workflow_deployment_llm"):
        _activate(service, connection, entry)
    calls = []
    transport_state = {"uncertain": False}

    def response(request):
        calls.append(request.method)
        if transport_state["uncertain"]:
            raise httpx.ReadTimeout("synthetic unknown outcome", request=request)
        frame = {"model": MODEL_ID, "choices": [{"delta": {"content": "合成结果"},
                  "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}}
        return httpx.Response(200, content=("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode())

    class Gateway(ManagedWorkflowGateway):
        @classmethod
        def for_router(cls, router):
            return super().for_router(router, client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(response), trust_env=False))

    monkeypatch.setattr(main, "ManagedWorkflowGateway", Gateway)
    deployments = WorkflowDeploymentStore(tmp_path / "deployments")
    wire(monkeypatch, deployments, executions)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    executions.create(task_id="root", run_id="run-root", run_type="workflow", source_kind=source,
                      workflow={"id": "parent", "nodes": [], "edges": []}, inputs={})
    initialize(executions, limits={**LIMITS, "max_model_calls": 1})
    origins = ProviderContextStore(service.repository)
    origins.register_execution(execution_id="run-root", task_id="root", source_kind=source)
    graph = model_callable_workflow()
    graph["nodes"][1]["data"]["modelId"] = MODEL_ID
    release = publish(deployments, graph)
    node = main.WorkflowNodePayload.model_validate({
        "id": "invoke", "type": "invoke_workflow", "data": invoke_data(release)})
    state = SimpleNamespace(source=source, store=executions, deployments=deployments,
                            service=service, origins=origins, calls=calls, node=node, release=release,
                            transport_state=transport_state, router_path=tmp_path / "router")
    yield state
    state.service.repository.close()


async def invoke(runtime, node=None, **metadata):
    return await main.run_private_subworkflow_call(
        node=node or runtime.node, variables={}, task_id="root",
        runtime_metadata={"workflow_deployment_execution_id": "root",
                          "workflow_root_execution_id": "root", **metadata},
        cancellation_requested=lambda: False)


@pytest.mark.asyncio
async def test_child_keeps_parent_origin_and_root_budget(runtime):
    result = await asyncio.create_task(invoke(runtime), context=contextvars.Context())
    child = runtime.store.require(result["taskId"])
    origin = runtime.origins.execution("local", child.run_id)
    assert origin["parent_execution_id"] == "run-root"
    assert origin["source_kind"] == child.source_kind == runtime.source
    parent_origin = runtime.origins.execution("local", "run-root")
    assert tuple(origin[k] for k in ("actor_kind", "actor_reference", "purpose")) == tuple(
        parent_origin[k] for k in ("actor_kind", "actor_reference", "purpose"))
    assert child.execution_budget_parent["parent_task_id"] == "root"
    assert runtime.store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert runtime.calls == ["POST"]
    receipt = runtime.service.repository.list_workload_receipts("local")["calls"][0]
    context = runtime.origins.read("local", receipt["id"])
    assert context.actor_kind == parent_origin["actor_kind"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "wrong_task", "wrong_source"])
async def test_bad_origin_blocks_before_child_dispatch(runtime, fault):
    with runtime.service.repository._connect() as db:
        if fault == "missing":
            db.execute("DELETE FROM provider_execution_origins WHERE execution_id='run-root'")
        elif fault == "wrong_task":
            db.execute("UPDATE provider_execution_origins SET task_id='foreign' WHERE execution_id='run-root'")
        else:
            db.execute("UPDATE provider_execution_origins SET source_kind='xpert_chat',actor_kind='local_anonymous',"
                       "actor_reference='local-anonymous',purpose='user_run' WHERE execution_id='run-root'")
    with pytest.raises((ValueError, RuntimeError), match="可信|provider_call_context"):
        await invoke(runtime)
    assert runtime.calls == []
    assert len(runtime.store.list_items()) == 1


@pytest.mark.asyncio
async def test_request_metadata_cannot_replace_trusted_parent(runtime):
    result = await invoke(runtime, provider_origin_execution_id="forged", provider_workload_source_kind="xpert_app")
    child = runtime.store.require(result["taskId"])
    assert runtime.origins.execution("local", child.run_id)["parent_execution_id"] == "run-root"


@pytest.mark.asyncio
async def test_completed_reuse_and_next_item_after_parent_resume(runtime, monkeypatch):
    first = await invoke(runtime)
    runtime.deployments, runtime.store = reopen(monkeypatch, runtime.deployments, runtime.store)
    assert await invoke(runtime) == first
    claimed = runtime.store.claim("root", worker_id="resumed")
    runtime.origins.register_execution(execution_id="run-root-resumed", task_id="root",
                                       source_kind=runtime.source, resume_from="run-root")
    runtime.store.update_run_id("root", run_id="run-root-resumed", expected_lease_token=claimed.lease_token)
    other = runtime.node.model_copy(deep=True)
    other.id = "next-item"
    with use_execution_budget(budget(runtime.store, "root", claimed.lease_token)):
        with pytest.raises((ValueError, RuntimeError), match="预算"):
            await invoke(runtime, other)
    assert runtime.calls == ["POST"]
    new_child = next(item for item in runtime.store.list_items() if item.task_id not in {"root", first["taskId"]})
    assert runtime.origins.execution("local", new_child.run_id)["parent_execution_id"] == "run-root-resumed"
    assert runtime.store.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_uncertain_provider_request_is_not_replayed_after_all_stores_reload(runtime, monkeypatch):
    runtime.transport_state["uncertain"] = True
    with pytest.raises((ValueError, RuntimeError)):
        await invoke(runtime)
    receipts = runtime.service.repository.list_workload_receipts("local")
    assert len(receipts["calls"]) == 1
    call = receipts["calls"][0]
    assert call["dispatched"] and call["status"] == "uncertain"
    before_context = runtime.origins.read("local", call["id"])
    egress = runtime.service.egress_policy
    runtime.service.repository.close()
    repository = SQLiteRouterRepository.open(runtime.router_path, master_key=b"x" * 32)
    runtime.service = ModelRouterService(repository, egress_policy=egress)
    runtime.origins = ProviderContextStore(repository)
    monkeypatch.setattr(main, "get_model_router_service", lambda: runtime.service)
    runtime.deployments, runtime.store = reopen(monkeypatch, runtime.deployments, runtime.store)
    claimed = runtime.store.claim("root", worker_id="restarted")
    with use_execution_budget(budget(runtime.store, "root", claimed.lease_token)):
        with pytest.raises((ValueError, RuntimeError)):
            await invoke(runtime)
    assert runtime.calls == ["POST"]
    assert runtime.origins.read("local", call["id"]) == before_context
    assert len(repository.list_workload_receipts("local")["calls"]) == 1
    assert runtime.store.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["prepare", "settle"])
async def test_parent_origin_removed_during_execution_cannot_dispatch_or_report_success(runtime, monkeypatch, stage):
    original = main._run_workflow_response if stage == "prepare" else main.consume_workflow_stream
    observed = []

    def corrupt_origin():
        observed.append(stage)
        with runtime.service.repository._connect() as db:
            db.execute("DELETE FROM provider_execution_origins WHERE execution_id='run-root'")

    async def intercept(*args, **kwargs):
        if stage == "prepare":
            corrupt_origin()
        result = await original(*args, **kwargs)
        if stage == "settle":
            corrupt_origin()
        return result

    monkeypatch.setattr(main, "_run_workflow_response" if stage == "prepare" else "consume_workflow_stream", intercept)
    with pytest.raises((ValueError, RuntimeError), match="可信|provider_call_context"):
        await invoke(runtime)
    assert observed == [stage]
    assert len(runtime.calls) == (0 if stage == "prepare" else 1)
