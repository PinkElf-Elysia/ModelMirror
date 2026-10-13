"""Production Handoff/Runner wiring with offline model output, never a paid call."""
from __future__ import annotations

import asyncio
import contextvars
from copy import deepcopy
from types import SimpleNamespace

import pytest

import server.main as main
from server.tests.test_workflow_r8a_budget_journal import LIMITS, ZERO, create, initialize
from server.xpert_runtime import AgentTaskStore, RunRegistry, WorkflowExecutionStore
from server.xpert_runtime.execution_budget import execution_operation
from server.xpert_runtime.handoff_executor import HandoffExecutor, HandoffPermanentError


def child_graph():
    return {"id": "child", "title": "离线子执行", "nodes": [
        {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
        {"id": "agent", "type": "workflow_agent", "data": {
            "kind": "workflow_agent", "title": "汇总", "modelId": "offline/model",
            "rolePrompt": "你是汇总助手。", "taskInput": "汇总 {{user_input}}", "outputVariable": "answer"}},
        {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "answer"}},
    ], "edges": [{"id": "one", "source": "input", "target": "agent"},
                  {"id": "two", "source": "agent", "target": "output"}]}


def setup(tmp_path, monkeypatch):
    executions = WorkflowExecutionStore(tmp_path / "executions")
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    registry = RunRegistry()
    monkeypatch.setattr(main, "workflow_execution_store", executions)
    monkeypatch.setattr(main, "agent_task_store", tasks)
    monkeypatch.setattr(main, "run_registry", registry)
    monkeypatch.setattr(main, "workflow_task_store", {})
    monkeypatch.setattr(main, "HANDOFF_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(main, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", False)
    monkeypatch.setattr(main.ManagedWorkflowGateway, "agent_routing_mode", lambda *_: "legacy")
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://offline.invalid", "synthetic-key"))
    monkeypatch.setattr(main, "validate_workflow_handoff_target", lambda *_: None)
    calls = []

    async def stream(*args, **kwargs):
        async with execution_operation("model_call"):
            calls.append("dispatched")
            yield "离线结果"

    async def prepare(reference, payload, **kwargs):
        assert reference == "target" and payload.version == 1
        return SimpleNamespace(
            xpert=SimpleNamespace(id="target", slug="target"), version=SimpleNamespace(version=1),
            request=main.WorkflowRunRequest(workflow=child_graph(), inputs={"user_input": payload.message}),
            runtime_metadata={"xpert_agent_config": LIMITS, "xpert_slug": "target"},
        )

    monkeypatch.setattr(main, "stream_workflow_llm_text", stream)
    monkeypatch.setattr(main, "prepare_published_xpert_run", prepare)
    return executions, tasks, registry, calls


async def registered(tmp_path, monkeypatch, *, cap=1):
    store, tasks, registry, calls = setup(tmp_path, monkeypatch)
    create(store, "root")
    initialize(store, limits={**LIMITS, "max_model_calls": cap})
    task = await tasks.create_task("合成任务", "固定输入")
    handoff = await tasks.create_handoff(task.task_id, source_agent="workflow", target_agent="xpert:target",
        reason="合成移交", metadata={"contract_version": 2, "target_xpert_id": "target",
        "target_xpert_version": 1, "execution_mode": "xpert_auto", "ready_for_execution": False})
    await main._register_workflow_handoff_budget(handoff, "root")
    handoff = await tasks.update_handoff_metadata(handoff.handoff_id, {"ready_for_execution": True})
    store.suspend("root", continuation={}, wait_kind="agent_handoff", wait_id=handoff.handoff_id)
    return store, tasks, registry, calls, task, handoff


@pytest.mark.asyncio
async def test_background_executor_charges_waiting_root_and_never_replays_completion(tmp_path, monkeypatch):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    executor = HandoffExecutor(tasks, registry, main.execute_xpert_handoff_target, enabled=True)
    result = await asyncio.create_task(executor.execute_handoff(handoff.handoff_id), context=contextvars.Context())
    assert result.status == "completed", result.metadata
    grant = store.find_execution_budget_child(f"handoff:{handoff.handoff_id}:")
    child = store.require(grant["child_task_id"])
    assert child.status == "completed"
    assert store.require("root").status == "waiting"
    assert child.execution_budget["usage"]["model_calls"] == 1
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    again = await main.execute_xpert_handoff_target(await tasks.get_handoff(handoff.handoff_id), task, None)
    assert again.output == "离线结果"
    assert calls == ["dispatched"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["input", "version", "files", "source", "reason", "depth"])
async def test_fixed_contract_drift_is_rejected_before_prepare(tmp_path, monkeypatch, mutation):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    if mutation == "input":
        task.input = "替换输入"
    elif mutation == "version":
        handoff.metadata["target_xpert_version"] = 2
    elif mutation == "files":
        handoff.metadata["file_asset_ids"] = ["forged-asset"]
    elif mutation == "source":
        handoff.metadata["source_xpert_id"] = "other"
    elif mutation == "reason":
        handoff.reason = "替换理由"
    else:
        handoff.metadata["handoff_depth"] = 1
    with pytest.raises(HandoffPermanentError, match="CONTRACT_CHANGED"):
        await main.execute_xpert_handoff_target(handoff, task, None)
    assert calls == []
    assert store.require("root").execution_budget["usage"] == ZERO
    assert len(store.list_items()) == 1


@pytest.mark.asyncio
async def test_consumed_unknown_child_is_not_dispatched_again_after_restart(tmp_path, monkeypatch):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    grant = store.find_execution_budget_child(f"handoff:{handoff.handoff_id}:")
    create(store, "unknown")
    initialize(store, "unknown")
    store.bind_execution_budget_parent("unknown", parent_task_id="root", occurrence_id=grant["occurrence_id"])
    reopened = WorkflowExecutionStore(store.storage_dir)
    monkeypatch.setattr(main, "workflow_execution_store", reopened)
    with pytest.raises(HandoffPermanentError, match="ALREADY_DISPATCHED"):
        await main.execute_xpert_handoff_target(handoff, task, None)
    assert calls == []
    assert len(reopened.list_items()) == 2


@pytest.mark.asyncio
async def test_missing_required_grant_cannot_fall_back_to_independent_budget(tmp_path, monkeypatch):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "workflow_execution_store", WorkflowExecutionStore(tmp_path / "empty"))
    with pytest.raises(HandoffPermanentError, match="DELEGATION_MISSING"):
        await main.execute_xpert_handoff_target(handoff, task, None)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["handoff_router", "agent_handoff"])
async def test_actual_parent_runner_registers_before_handoff_becomes_ready(tmp_path, monkeypatch, kind):
    store, tasks, registry, calls = setup(tmp_path, monkeypatch)
    graph = {"id": "parent", "title": "父执行", "nodes": [
        {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
        {"id": "delegate", "type": "handoff_router", "data": {
            "kind": "handoff_router", "contractVersion": 2, "sourceVariable": "user_input",
            "taskTitle": "合成子任务", "sourceAgent": "workflow", "reasonTemplate": "合成移交",
            "targetMode": "xpert", "targetXpertId": "target", "targetVersion": 1,
            "waitForCompletion": True, "timeoutSeconds": 120, "outputVariable": "receipt", "resultVariable": "answer"}},
        {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "answer"}},
    ], "edges": [{"id": "one", "source": "input", "target": "delegate"},
                  {"id": "two", "source": "delegate", "target": "output"}]}
    if kind == "agent_handoff":
        graph["nodes"].insert(1, {"id": "task", "type": "agent_task", "data": {
            "kind": "agent_task", "contractVersion": 2, "taskTitle": "合成子任务",
            "taskInput": "{{user_input}}", "assignedAgent": "xpert:target", "outputVariable": "task_receipt"}})
        graph["nodes"][2]["type"] = kind
        graph["nodes"][2]["data"] = {
            "kind": kind, "contractVersion": 2, "taskVariable": "task_receipt", "taskValueKind": "receipt",
            "sourceAgent": "workflow", "reason": "合成移交", "targetMode": "xpert",
            "targetXpertId": "target", "targetVersion": 1, "waitForCompletion": True,
            "timeoutSeconds": 120, "outputVariable": "receipt", "resultVariable": "answer"}
        graph["edges"][0]["target"] = "task"
        graph["edges"].append({"id": "task_delegate", "source": "task", "target": "delegate"})
    update = tasks.update_handoff_metadata
    observed = []

    async def checked(handoff_id, metadata):
        if metadata.get("ready_for_execution"):
            observed.append(store.find_execution_budget_child(f"handoff:{handoff_id}:"))
            assert observed[-1] is not None
        return await update(handoff_id, metadata)

    monkeypatch.setattr(tasks, "update_handoff_metadata", checked)
    response = await main._run_workflow_response(
        main.WorkflowRunRequest(workflow=graph, inputs={"user_input": "固定输入"}), None,
        runtime_run_type="xpert", runtime_metadata={"xpert_agent_config": LIMITS}, runtime_task_id="root",
    )
    final = await main.consume_workflow_stream(response)
    assert final["event"] == "agent_handoff_waiting"
    assert len(observed) == 1
    executor = HandoffExecutor(tasks, registry, main.execute_xpert_handoff_target, enabled=True)
    completed = await asyncio.create_task(executor.execute_handoff(store.require("root").wait_id), context=contextvars.Context())
    assert completed.status == "completed", completed.metadata
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert calls == ["dispatched"]
    from server.xpert_runtime.workflow_handoff_coordinator import WorkflowHandoffCoordinator
    reopened = WorkflowExecutionStore(store.storage_dir)
    monkeypatch.setattr(main, "workflow_execution_store", reopened)
    coordinator = WorkflowHandoffCoordinator(tasks, reopened, main.resume_workflow_handoff_execution)
    assert await coordinator.run_once() == 1
    assert reopened.require("root").status == "completed"
    assert reopened.require("root").execution_budget["usage"]["model_calls"] == 1
    assert calls == ["dispatched"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["cancel", "exhausted", "expired_owner", "changed_owner"])
async def test_background_dispatch_rejects_cancel_budget_and_old_owner(tmp_path, monkeypatch, fault):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    claimed = await tasks.claim_handoff(handoff.handoff_id, worker_id="one")
    if fault == "cancel":
        store.cancel("root")
    elif fault == "exhausted":
        store._items["root"].execution_budget["usage"] = {**ZERO, "steps_used": 1, "model_calls": 1}
    elif fault == "expired_owner":
        await tasks.update_handoff_metadata(handoff.handoff_id, {"lease_expires_at": 1.0})
    else:
        await tasks.update_handoff_metadata(handoff.handoff_id, {"lease_token": "replacement"})
        assert claimed.metadata["lease_token"] != "replacement"
    with pytest.raises((HandoffPermanentError, main.WorkflowStreamFailure, main.WorkflowExecutionConflictError)):
        await main.execute_xpert_handoff_target(claimed, task, None)
    assert calls == []


@pytest.mark.asyncio
async def test_old_owner_loses_permission_between_two_calls(tmp_path, monkeypatch):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch, cap=3)
    claimed = await tasks.claim_handoff(handoff.handoff_id, worker_id="one")

    async def stream(*args, **kwargs):
        async with execution_operation("model_call"):
            calls.append("first")
        await tasks.update_handoff_metadata(handoff.handoff_id, {"lease_token": "replacement"})
        async with execution_operation("model_call"):
            calls.append("forbidden")
        yield "不应返回"

    monkeypatch.setattr(main, "stream_workflow_llm_text", stream)
    with pytest.raises(main.WorkflowStreamFailure):
        await main.execute_xpert_handoff_target(claimed, task, None)
    assert calls == ["first"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_budget_contract_does_not_retain_task_or_prompt_text(tmp_path, monkeypatch):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    saved = store.require("root").execution_budget_children
    assert len(saved) == 1
    key = next(iter(saved))
    assert len(key.rsplit(":", 1)[1]) == 64
    assert task.input not in key and handoff.reason not in key


@pytest.mark.asyncio
async def test_mutable_workflow_task_pointer_is_not_budget_authority(tmp_path, monkeypatch):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    await tasks.update_handoff_metadata(handoff.handoff_id, {"workflow_task_id": "forged-parent"})
    executor = HandoffExecutor(tasks, registry, main.execute_xpert_handoff_target, enabled=True)
    result = await executor.execute_handoff(handoff.handoff_id)
    assert result.status == "completed"
    grant = store.find_execution_budget_child(f"handoff:{handoff.handoff_id}:")
    assert grant["parent_task_id"] == "root"
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert calls == ["dispatched"]


@pytest.mark.asyncio
async def test_ambiguous_grant_cannot_choose_an_arbitrary_root(tmp_path, monkeypatch):
    store, tasks, registry, calls, task, handoff = await registered(tmp_path, monkeypatch)
    grant = store.find_execution_budget_child(f"handoff:{handoff.handoff_id}:")
    create(store, "other")
    initialize(store, "other")
    store.authorize_execution_budget_child("other", grant["occurrence_id"])
    with pytest.raises(main.XpertExecutionBudgetStateError, match="不唯一"):
        await main.execute_xpert_handoff_target(handoff, task, None)
    assert calls == []
