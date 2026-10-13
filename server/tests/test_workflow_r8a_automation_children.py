"""Automation parent cancellation through real entry points and synthetic Stores."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace

import pytest

import server.main as main
from server.xpert_runtime import AutomationStore, RunRegistry, WorkflowExecutionStore
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError


def workflow():
    return {
        "id": "automation-child", "title": "自动化子执行验收",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
            {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "user_input"}},
        ],
        "edges": [{"id": "edge", "source": "input", "target": "output"}],
    }


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    parents = AutomationStore(tmp_path / "parents")
    children = WorkflowExecutionStore(tmp_path / "children")
    registry = RunRegistry()
    live = {}
    monkeypatch.setattr(main, "automation_store", parents)
    monkeypatch.setattr(main, "workflow_execution_store", children)
    monkeypatch.setattr(main, "run_registry", registry)
    monkeypatch.setattr(main, "workflow_task_store", live)
    monkeypatch.setattr(main, "automation_coordinator", None)
    monkeypatch.setattr(main, "workflow_automation_provider", None)
    monkeypatch.setattr(main, "AUTOMATION_COORDINATOR_ENABLED", True)
    return parents, children, registry, live


def parent(runtime):
    parents, _, _, _ = runtime
    definition = parents.create_definition(name="自动化父执行", prompt="仅合成测试", target_xpert_id="reader", target_xpert_slug="reader", target_xpert_version=1, trigger={"type": "interval", "interval_seconds": 60})
    item = parents.run_now(definition.automation_id)
    claimed = parents.claim_execution(item.execution_id, worker_id="owner")
    return claimed, {
        "automation_id": claimed.automation_id,
        "automation_execution_id": claimed.execution_id,
        "automation_occurrence_key": claimed.occurrence_key,
    }


async def child(runtime, metadata, state="running", task_id="child"):
    _, children, registry, live = runtime
    run = await registry.create_run("xpert", "子执行", status="running")
    children.create(task_id=task_id, run_id=run.run_id, run_type="xpert", workflow=workflow(), inputs={}, runtime_metadata=metadata)
    if state in {"completed", "failed", "rejected"}:
        if state == "completed":
            children.complete(task_id, result="original")
        elif state == "failed":
            children.fail(task_id, error="original")
        else:
            children.reject(task_id)
    elif state != "running":
        kind = "approval" if state == "ready" else state
        children.suspend(task_id, wait_kind=kind, wait_id="wait", continuation={"queue": ["output"]}, resume_at=100 if kind in {"timer", "node_retry"} else None)
        if state == "ready":
            children.mark_ready(task_id, wait_kind=kind, wait_id="wait")
    live[task_id] = {"cancel_requested": False, "provider_cancel_event": asyncio.Event(), "pause_event": asyncio.Event()}
    return children.require(task_id), run


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["running", "ready", "approval", "client_tool", "timer", "node_retry", "agent_handoff"])
async def test_cancel_reaches_child_without_parent_result(runtime, state):
    parents, children, registry, live = runtime
    execution, metadata = parent(runtime)
    saved, run = await child(runtime, metadata, state)
    assert parents.get_execution(execution.execution_id).workflow_task_id is None
    await main.get_automation_coordinator().cancel_execution(execution.execution_id)
    assert children.require(saved.task_id).status == "cancelled"
    assert (await registry.get_run(run.run_id)).status == "cancelled"
    assert live[saved.task_id]["cancel_requested"]
    assert live[saved.task_id]["provider_cancel_event"].is_set()
    assert live[saved.task_id]["pause_event"].is_set()


@pytest.mark.asyncio
async def test_cancelled_parent_blocks_new_runner(runtime):
    parents, children, registry, _ = runtime
    execution, metadata = parent(runtime)
    parents.cancel_execution(execution.execution_id)
    with pytest.raises(WorkflowExecutionConflictError):
        await main._run_workflow_response(main.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"}), None, runtime_run_type="xpert", runtime_metadata=metadata)
    assert children.list_items() == []
    assert await registry.list_runs() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["automation_id", "automation_execution_id", "automation_occurrence_key"])
async def test_mismatched_parent_identity_never_cancels_other_child(runtime, field):
    _, children, _, live = runtime
    execution, metadata = parent(runtime)
    await child(runtime, metadata, task_id="owned")
    wrong = {**metadata, field: "another"}
    await child(runtime, wrong, task_id="unrelated")
    await main.get_automation_coordinator().cancel_execution(execution.execution_id)
    assert children.require("owned").status == "cancelled"
    assert children.require("unrelated").status == "running"
    assert not live["unrelated"]["cancel_requested"]
    with pytest.raises(WorkflowExecutionConflictError):
        await main._assert_automation_parent_not_cancelled(wrong)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "rejected"])
async def test_terminal_children_preserved(runtime, terminal):
    _, children, registry, live = runtime
    execution, metadata = parent(runtime)
    saved, run = await child(runtime, metadata, terminal)
    before_run = asdict(await registry.get_run(run.run_id))
    await main.get_automation_coordinator().cancel_execution(execution.execution_id)
    assert asdict(children.require(saved.task_id)) == asdict(saved)
    assert asdict(await registry.get_run(run.run_id)) == before_run
    assert not live[saved.task_id]["cancel_requested"]


@pytest.mark.asyncio
async def test_cancel_invalidates_lease_and_projects_all_run_aliases(runtime):
    _, children, registry, _ = runtime
    execution, metadata = parent(runtime)
    saved, old_run = await child(runtime, metadata, "ready")
    claimed = children.claim(saved.task_id, worker_id="old")
    new_run = await registry.create_run("xpert", "恢复执行", status="running")
    children.update_run_id(saved.task_id, run_id=new_run.run_id, expected_lease_token=claimed.lease_token)
    await main.get_automation_coordinator().cancel_execution(execution.execution_id)
    with pytest.raises(WorkflowExecutionConflictError):
        children.complete(saved.task_id, result="late", expected_lease_token=claimed.lease_token)
    assert {(await registry.get_run(run.run_id)).status for run in (old_run, new_run)} == {"cancelled"}


@pytest.mark.asyncio
async def test_parent_cancel_between_registry_and_durable_registration(runtime, monkeypatch):
    parents, children, registry, live = runtime
    execution, metadata = parent(runtime)
    original = registry.record_checkpoint

    async def cancel_during_registration(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kwargs.get("event_type") == "xpert.started":
            await main.get_automation_coordinator().cancel_execution(execution.execution_id)
        return result

    monkeypatch.setattr(registry, "record_checkpoint", cancel_during_registration)
    with pytest.raises(WorkflowExecutionConflictError):
        await main._run_workflow_response(main.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"}), None, runtime_run_type="xpert", runtime_metadata=metadata)
    assert parents.get_execution(execution.execution_id).status == "cancelled"
    assert len(children.list_items()) == 1
    assert children.list_items()[0].status == "cancelled"
    assert all(state["cancel_requested"] for state in live.values())
    assert {run.status for run in await registry.list_runs()} == {"cancelled"}


@pytest.mark.asyncio
async def test_reloaded_parent_cancel_blocks_resuming_child(runtime, monkeypatch):
    parents, children, _, _ = runtime
    execution, metadata = parent(runtime)
    saved, _ = await child(runtime, metadata, "ready")
    parents.cancel_execution(execution.execution_id)
    reloaded_parents = AutomationStore(parents.storage_dir)
    reloaded_children = WorkflowExecutionStore(children.storage_dir)
    monkeypatch.setattr(main, "automation_store", reloaded_parents)
    monkeypatch.setattr(main, "workflow_execution_store", reloaded_children)
    claimed = reloaded_children.claim(saved.task_id, worker_id="recovery")
    with pytest.raises(WorkflowExecutionConflictError):
        await main._run_workflow_response(main.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"}), None, runtime_run_type="xpert", resume_execution=claimed)
    assert reloaded_children.require(saved.task_id).status == "cancelled"


@pytest.mark.asyncio
async def test_child_commit_failure_keeps_parent_cancel_and_continues_siblings(runtime, monkeypatch):
    parents, children, registry, live = runtime
    execution, metadata = parent(runtime)
    await child(runtime, metadata, task_id="first")
    _, second_run = await child(runtime, metadata, task_id="second")
    original = children.cancel

    def unavailable(task_id, **kwargs):
        if task_id == "first":
            raise OSError("synthetic child store failure")
        return original(task_id, **kwargs)

    worker = main.get_automation_coordinator()
    monkeypatch.setattr(children, "cancel", unavailable)
    with pytest.raises(OSError):
        await worker.cancel_execution(execution.execution_id)
    assert parents.get_execution(execution.execution_id).status == "cancelled"
    assert children.require("first").status == "running"
    assert children.require("second").status == "cancelled"
    assert live["second"]["provider_cancel_event"].is_set()
    assert (await registry.get_run(second_run.run_id)).status == "cancelled"
    with pytest.raises(OSError):
        await worker.retry_execution(execution.execution_id)
    assert parents.get_execution(execution.execution_id).status == "cancelled"
    monkeypatch.setattr(children, "cancel", original)
    await worker.run_once()
    assert children.require("first").status == "cancelled"
    revision = children.require("first").revision
    await worker.run_once()
    assert children.require("first").revision == revision


@pytest.mark.asyncio
async def test_registry_failure_does_not_delay_child_signals_and_is_reconciled(runtime, monkeypatch):
    _, children, registry, live = runtime
    execution, metadata = parent(runtime)
    _, first_run = await child(runtime, metadata, task_id="first")
    _, second_run = await child(runtime, metadata, task_id="second")
    original = registry.cancel_run

    async def unavailable(run_id, **kwargs):
        if run_id == first_run.run_id:
            assert children.require("second").status == "cancelled"
            assert live["second"]["pause_event"].is_set()
            raise OSError("synthetic registry failure")
        return await original(run_id, **kwargs)

    monkeypatch.setattr(registry, "cancel_run", unavailable)
    worker = main.get_automation_coordinator()
    with pytest.raises(OSError):
        await worker.cancel_execution(execution.execution_id)
    assert (await registry.get_run(second_run.run_id)).status == "cancelled"
    monkeypatch.setattr(registry, "cancel_run", original)
    await worker.run_once()
    assert (await registry.get_run(first_run.run_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_internal_child_query_ignores_management_page_limit(runtime):
    _, children, _, _ = runtime
    execution, metadata = parent(runtime)
    saved, _ = await child(runtime, metadata)
    with children._lock:
        for index in range(1005):
            children._items[f"other-{index}"] = replace(saved, task_id=f"other-{index}", updated_at=saved.updated_at + index + 1, runtime_metadata={})
    assert saved.task_id not in {item.task_id for item in children.list_items(limit=1000)}
    items = children.list_automation_children(automation_id=execution.automation_id, execution_id=execution.execution_id, occurrence_key=execution.occurrence_key)
    assert [item.task_id for item in items] == [saved.task_id]
    items[0].runtime_metadata.clear()
    assert children.require(saved.task_id).runtime_metadata == metadata
    await main.get_automation_coordinator().cancel_execution(execution.execution_id)
    assert children.require(saved.task_id).status == "cancelled"
    assert children.require("other-0").status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["automation_id", "automation_execution_id", "automation_occurrence_key"])
async def test_incomplete_identity_is_rejected_before_registration(runtime, field):
    _, children, registry, _ = runtime
    _, metadata = parent(runtime)
    metadata.pop(field)
    with pytest.raises(WorkflowExecutionConflictError):
        await main._run_workflow_response(main.WorkflowRunRequest(workflow=workflow(), inputs={}), None, runtime_run_type="xpert", runtime_metadata=metadata)
    assert children.list_items() == [] and await registry.list_runs() == []


@pytest.mark.asyncio
async def test_cancel_reconciliation_after_both_stores_reload(runtime, monkeypatch):
    parents, children, registry, _ = runtime
    execution, metadata = parent(runtime)
    saved, run = await child(runtime, metadata, "timer")
    parents.cancel_execution(execution.execution_id)
    reloaded_children = WorkflowExecutionStore(children.storage_dir)
    monkeypatch.setattr(main, "automation_store", AutomationStore(parents.storage_dir))
    monkeypatch.setattr(main, "workflow_execution_store", reloaded_children)
    assert await main.get_automation_coordinator().run_once() == 0
    assert reloaded_children.require(saved.task_id).status == "cancelled"
    assert (await registry.get_run(run.run_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_non_cancelled_parent_cannot_use_management_cancel_callback(runtime):
    _, children, registry, live = runtime
    execution, metadata = parent(runtime)
    saved, run = await child(runtime, metadata)
    await main._cancel_automation_workflow_children(replace(execution, status="cancelled"))
    assert children.require(saved.task_id).status == "running"
    assert (await registry.get_run(run.run_id)).status == "running"
    assert not live[saved.task_id]["cancel_requested"]
