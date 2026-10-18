"""B0 evidence, not a new R8-B entrypoint or permission policy.

Tests named ``required`` are unimplemented R8-B acceptance constraints. Keep
their failures visible until the production batches satisfy them. Legacy
characterization tests deliberately do not call permissive behavior safe.
All model outputs are synthetic; no Provider is contacted.
"""
from __future__ import annotations

import asyncio
import contextvars
from copy import deepcopy

import pytest

import server.api.workflow_deployments as deployment_api
import server.main as main
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.tests.test_workflow_deployments import secure_http_workflow
from server.tests.test_workflow_r8a_budget_journal import LIMITS, create, initialize
from server.tests.test_workflow_r8a_budget_tree import budget
from server.tests.test_workflow_r8a_handoff_budget import setup
from server.tests.test_workflow_subworkflows import callable_workflow, model_callable_workflow
from server.workflow_deployments import WorkflowDeploymentConflictError, WorkflowDeploymentStore
from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.xpert_runtime.execution_budget import execution_operation, use_execution_budget
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xpert_runtime.run_registry import RunRegistry


def publish(store, graph, **flags):
    project = store.create_project(graph)
    release = store.publish(project.project_id)
    store.activate(project.project_id, release.version, webhooks_enabled=False,
                   subworkflows_enabled=True, **flags)
    return release


def invoke_data(release):
    return {
        "kind": "invoke_workflow", "targetProjectId": release.project_id,
        "targetVersion": release.version,
        "inputBindings": {"message": {"source": "literal", "value": "合成输入"}},
        "resultVariable": "workflow_result", "timeoutSeconds": 5,
    }


def wire(monkeypatch, deployments, executions):
    monkeypatch.setattr(deployment_api, "_store", deployments)
    monkeypatch.setattr(deployment_api, "_trigger_executor", main.run_deployed_workflow_trigger)
    monkeypatch.setattr(main, "workflow_deployment_store", deployments)
    monkeypatch.setattr(main, "workflow_execution_store", executions)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    repository = SQLiteRouterRepository.open(tmp_path / "router", master_key=b"b" * 32)
    router = ModelRouterService(repository)
    monkeypatch.setattr(main, "get_model_router_service", lambda: router)
    executions, _, _, calls = setup(tmp_path, monkeypatch)
    deployments = WorkflowDeploymentStore(tmp_path / "deployments")
    wire(monkeypatch, deployments, executions)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    create(executions, "root")
    initialize(executions, limits={**LIMITS, "max_model_calls": 1})
    release = publish(deployments, model_callable_workflow())
    node = main.WorkflowNodePayload.model_validate({
        "id": "invoke", "type": "invoke_workflow", "data": invoke_data(release),
    })
    yield deployments, executions, calls, release, node
    repository.close()


async def invoke(node):
    return await main.run_private_subworkflow_call(
        node=node, variables={}, task_id="root",
        runtime_metadata={"workflow_deployment_execution_id": "root",
                          "workflow_root_execution_id": "root"},
        cancellation_requested=lambda: False,
    )


def reopen(monkeypatch, deployments, executions):
    deployments = WorkflowDeploymentStore(deployments.storage_dir)
    executions = WorkflowExecutionStore(executions.storage_dir)
    wire(monkeypatch, deployments, executions)
    monkeypatch.setattr(main, "run_registry", RunRegistry())
    monkeypatch.setattr(main, "workflow_task_store", {})
    return deployments, executions


@pytest.mark.asyncio
async def test_existing_inline_context_charges_root_and_reuses_completed_child(runtime, monkeypatch):
    deployments, executions, calls, _, node = runtime
    with use_execution_budget(budget(executions, "root")):
        first = await invoke(node)
    assert first["status"] == "completed"
    assert calls == ["dispatched"]
    assert executions.require("root").execution_budget["usage"]["model_calls"] == 1
    _, executions = reopen(monkeypatch, deployments, executions)
    second = await asyncio.create_task(invoke(node), context=contextvars.Context())
    assert second == first
    assert calls == ["dispatched"]
    assert executions.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_required_subworkflow_has_durable_root_budget_link(runtime):
    _, executions, _, _, node = runtime
    with use_execution_budget(budget(executions, "root")):
        result = await invoke(node)
    child = executions.require(result["taskId"])
    assert child.execution_budget_parent is not None, "子调用只有内存预算继承，没有持久父预算绑定"
    assert child.execution_budget_parent["parent_task_id"] == "root"


@pytest.mark.asyncio
async def test_required_restart_cannot_bypass_exhausted_root_budget(runtime, monkeypatch):
    deployments, executions, calls, _, node = runtime
    with use_execution_budget(budget(executions, "root")):
        await invoke(node)
    _, executions = reopen(monkeypatch, deployments, executions)
    next_node = node.model_copy(deep=True)
    next_node.id = "next_item"
    try:
        await asyncio.create_task(invoke(next_node), context=contextvars.Context())
    except (ValueError, RuntimeError):
        pass
    assert len(calls) == 1, "根预算已耗尽，空上下文中的下一子调用仍派发了模型"
    assert executions.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_required_single_call_input_drift_rejected_after_restart(runtime, monkeypatch):
    deployments, executions, calls, _, node = runtime
    await invoke(node)
    reopen(monkeypatch, deployments, executions)
    changed = node.model_copy(deep=True)
    changed.data["inputBindings"]["message"]["value"] = "不同输入"
    with pytest.raises(WorkflowDeploymentConflictError):
        await invoke(changed)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_required_dispatched_unknown_snapshot_is_not_replayed(runtime, monkeypatch):
    deployments, executions, calls, _, node = runtime
    snapshots = []
    monkeypatch.setattr(deployment_api, "WORKFLOW_TRIGGER_LEASE_SECONDS", 5)

    async def dispatched(*args, **kwargs):
        async with execution_operation("model_call"):
            calls.append("dispatched")
            # Capture actual durable state after admission, before a result.
            snapshots.append((deployments.snapshot_path.read_bytes(), executions.snapshot_path.read_bytes()))
            yield "合成完成结果"

    monkeypatch.setattr(main, "stream_workflow_llm_text", dispatched)
    with use_execution_budget(budget(executions, "root")):
        await invoke(node)
    assert len(snapshots) == 1 and len(calls) == 1
    deployments.snapshot_path.write_bytes(snapshots[0][0])
    executions.snapshot_path.write_bytes(snapshots[0][1])
    deployments, executions = reopen(monkeypatch, deployments, executions)
    await asyncio.sleep(5.1)
    try:
        await asyncio.create_task(invoke(node), context=contextvars.Context())
    except (ValueError, RuntimeError):
        pass
    assert len(calls) == 1, "派发后未持久化结果的原发生标识被重新执行"


@pytest.mark.parametrize("mutation", ["input_digest", "target_version", "item_count", "root_execution_id"])
def test_existing_batch_drift_rejected_after_restart(tmp_path, mutation):
    store = WorkflowDeploymentStore(tmp_path / "deployments")
    release = publish(store, callable_workflow())
    args = dict(parent_execution_id="parent", root_execution_id="root", call_node_id="map",
                target_project_id=release.project_id, target_version=release.version,
                item_count=2, input_digest="a" * 64)
    first, created = store.reserve_subworkflow_batch(**args)
    assert created
    store = WorkflowDeploymentStore(store.storage_dir)
    same, created = store.reserve_subworkflow_batch(**args)
    assert same == first and not created
    changed = {"input_digest": "b" * 64, "target_version": 2,
               "item_count": 3, "root_execution_id": "other"}
    args[mutation] = changed[mutation]
    before = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowDeploymentConflictError):
        store.reserve_subworkflow_batch(**args)
    assert store.snapshot_path.read_bytes() == before


def test_existing_batch_item_identity_is_stable_and_distinct(tmp_path):
    store = WorkflowDeploymentStore(tmp_path / "deployments")
    release = publish(store, callable_workflow())
    common = dict(parent_execution_id="parent", root_execution_id="root", call_node_id="map",
                  target_project_id=release.project_id, target_version=release.version)
    batch, _ = store.reserve_subworkflow_batch(**common, item_count=2, input_digest="a" * 64)
    args = {**common, "parent_depth": 0, "test_mode": True, "suppress_failure_dispatch": True,
            "batch_occurrence_key": batch.occurrence_key, "input_digest": batch.input_digest}
    first, _ = store.materialize_subworkflow_execution(**args, batch_index=0)
    first_id, first_task = first.execution_id, first.task_id
    store = WorkflowDeploymentStore(store.storage_dir)
    same, created = store.materialize_subworkflow_execution(**args, batch_index=0)
    second, second_created = store.materialize_subworkflow_execution(**args, batch_index=1)
    assert not created and second_created
    assert same.execution_id == first_id and same.task_id == first_task
    assert second.execution_id != first_id and second.task_id != first_task
    with pytest.raises(WorkflowDeploymentConflictError):
        store.materialize_subworkflow_execution(**args, batch_index=2)


def test_legacy_callable_acceptance_is_not_transitive_readonly_evidence(tmp_path):
    """Characterization only: R8-B must not reuse legacy activation as its permit."""
    store = WorkflowDeploymentStore(tmp_path / "deployments")
    graph = callable_workflow(title="合成出口资格反例")
    http = deepcopy(secure_http_workflow()["nodes"][1])
    http["data"].update(method="POST", url="https://api.example.test/items")
    graph["nodes"].insert(1, http)
    graph["nodes"][-1]["data"]["outputVariable"] = "http_response"
    graph["edges"] = [{"id": "one", "source": "entry", "target": "http"},
                      {"id": "two", "source": "http", "target": "output"}]
    leaf = publish(store, graph, http_requests_enabled=True)
    wrapper = publish(store, callable_workflow(invoke=invoke_data(leaf)))
    caller = publish(store, callable_workflow(invoke=invoke_data(wrapper)))
    assert caller.trigger_kind == "call"
    assert leaf.workflow["nodes"][1]["data"]["method"] == "POST"
    assert workflow_node_contract_registry.require("http_request").execution.side_effect == "external_read"
    assert workflow_node_contract_registry.require("invoke_workflow").planner.support == "unsupported"
