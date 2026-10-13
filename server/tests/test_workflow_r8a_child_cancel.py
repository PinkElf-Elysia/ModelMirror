"""Real runner entry/management callbacks with temporary Stores, no providers."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace

import pytest

import server.main as main_module
from server.xpert_runtime import AgentTaskStore, RunRegistry, WorkflowExecutionStore
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError


def workflow():
    return {
        "id": "child-cancel", "title": "子执行取消验收",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
            {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "user_input"}},
        ],
        "edges": [{"id": "edge", "source": "input", "target": "output"}],
    }


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    live = {}
    monkeypatch.setattr(main_module, "agent_task_store", tasks)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "workflow_task_store", live)
    return tasks, executions, registry, live


async def parent(runtime):
    tasks, _, _, _ = runtime
    task = await tasks.create_task("父任务", "synthetic")
    handoff = await tasks.create_handoff(task.task_id, "parent", "xpert:child", "synthetic")
    await tasks.update_handoff_status(handoff.handoff_id, "accepted")
    return task, handoff, {"agent_task_id": task.task_id, "handoff_id": handoff.handoff_id}


async def child(runtime, metadata, state="running", child_id="child"):
    _, executions, registry, live = runtime
    run = await registry.create_run("xpert", "子执行", status="running")
    executions.create(task_id=child_id, run_id=run.run_id, run_type="xpert", workflow=workflow(), inputs={}, runtime_metadata=metadata)
    if state not in {"running", "completed", "failed", "rejected"}:
        kind = "approval" if state == "ready" else state
        executions.suspend(
            child_id, wait_kind=kind, wait_id="wait", continuation={"queue": ["output"]},
            resume_at=100.0 if kind in {"timer", "node_retry"} else None,
        )
        if state == "ready":
            executions.mark_ready(child_id, wait_kind=kind, wait_id="wait")
    elif state == "completed":
        executions.complete(child_id, result="original")
        await registry.update_run(run.run_id, status="completed")
    elif state == "failed":
        executions.fail(child_id, error="original")
        await registry.update_run(run.run_id, status="failed")
    elif state == "rejected":
        executions.reject(child_id)
    live[child_id] = {"cancel_requested": False, "provider_cancel_event": asyncio.Event(), "pause_event": asyncio.Event()}
    return executions.require(child_id), run


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["running", "ready", "approval", "client_tool", "timer", "node_retry", "agent_handoff"])
async def test_task_cancel_reaches_child_without_handoff_wait_receipt(runtime, state):
    tasks, executions, registry, live = runtime
    task, handoff, metadata = await parent(runtime)
    execution, run = await child(runtime, metadata, state)
    assert "workflow_task_id" not in (await tasks.get_handoff(handoff.handoff_id)).metadata
    result = await main_module.cancel_agent_task(task.task_id, {"reason": "stop"})
    assert result["status"] == "cancelled"
    assert executions.require(execution.task_id).status == "cancelled"
    assert (await registry.get_run(run.run_id)).status == "cancelled"
    assert live[execution.task_id]["cancel_requested"] is True
    assert live[execution.task_id]["provider_cancel_event"].is_set()
    assert live[execution.task_id]["pause_event"].is_set()


@pytest.mark.asyncio
async def test_cancel_invalidates_child_lease_and_all_run_aliases(runtime):
    _, executions, registry, _ = runtime
    task, _, metadata = await parent(runtime)
    execution, old_run = await child(runtime, metadata, "ready")
    claimed = executions.claim(execution.task_id, worker_id="old")
    new_run = await registry.create_run("xpert", "恢复子执行", status="running")
    executions.update_run_id(execution.task_id, run_id=new_run.run_id, expected_lease_token=claimed.lease_token)
    await main_module.cancel_agent_task(task.task_id)
    with pytest.raises(WorkflowExecutionConflictError):
        executions.complete(execution.task_id, result="late", expected_lease_token=claimed.lease_token)
    assert {(await registry.get_run(r.run_id)).status for r in (old_run, new_run)} == {"cancelled"}


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "rejected"])
async def test_cancel_preserves_terminal_child(runtime, terminal):
    _, executions, registry, live = runtime
    task, _, metadata = await parent(runtime)
    execution, run = await child(runtime, metadata, terminal)
    before = asdict(execution)
    run_before = asdict(await registry.get_run(run.run_id))
    await main_module.cancel_agent_task(task.task_id)
    assert asdict(executions.require(execution.task_id)) == before
    assert asdict(await registry.get_run(run.run_id)) == run_before
    assert live[execution.task_id]["cancel_requested"] is False


@pytest.mark.asyncio
async def test_cross_parent_or_wrong_handoff_not_cancelled(runtime):
    _, executions, _, _ = runtime
    task, handoff, metadata = await parent(runtime)
    other, other_handoff, other_metadata = await parent(runtime)
    await child(runtime, metadata, child_id="owned")
    await child(runtime, other_metadata, child_id="other")
    await child(runtime, {"agent_task_id": task.task_id, "handoff_id": other_handoff.handoff_id}, child_id="mismatch")
    await child(runtime, {"agent_task_id": other.task_id, "handoff_id": handoff.handoff_id}, child_id="reverse")
    await main_module.cancel_agent_task(task.task_id)
    assert executions.require("owned").status == "cancelled"
    assert all(executions.require(key).status == "running" for key in ("other", "mismatch", "reverse"))


@pytest.mark.asyncio
async def test_cancel_replay_after_child_store_failure(runtime, monkeypatch):
    tasks, executions, _, _ = runtime
    task, _, metadata = await parent(runtime)
    await child(runtime, metadata)
    original = executions.cancel

    def fail(*args, **kwargs):
        raise OSError("synthetic child store unavailable")

    monkeypatch.setattr(executions, "cancel", fail)
    with pytest.raises(OSError):
        await main_module.cancel_agent_task(task.task_id)
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    monkeypatch.setattr(executions, "cancel", original)
    reloaded = AgentTaskStore(storage_dir=tasks.storage_path.parent)
    monkeypatch.setattr(main_module, "agent_task_store", reloaded)
    await main_module.cancel_agent_task(task.task_id)
    assert executions.require("child").status == "cancelled"
    revision = executions.require("child").revision
    await main_module.cancel_agent_task(task.task_id)
    assert executions.require("child").revision == revision


@pytest.mark.asyncio
async def test_cancelled_parent_blocks_fresh_runner_before_registration(runtime):
    tasks, executions, registry, _ = runtime
    task, _, metadata = await parent(runtime)
    await tasks.cancel_task(task.task_id)
    with pytest.raises(WorkflowExecutionConflictError):
        await main_module._run_workflow_response(
            main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"}),
            None, runtime_run_type="xpert", runtime_metadata=metadata,
        )
    assert executions.list_items() == []
    assert await registry.list_runs() == []


@pytest.mark.asyncio
async def test_parent_cancel_during_start_before_child_registration(runtime, monkeypatch):
    tasks, executions, registry, live = runtime
    task, _, metadata = await parent(runtime)
    original = registry.record_checkpoint

    async def cancel_before_registration(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kwargs.get("event_type") == "xpert.started":
            await main_module.cancel_agent_task(task.task_id)
        return result

    monkeypatch.setattr(registry, "record_checkpoint", cancel_before_registration)
    with pytest.raises(WorkflowExecutionConflictError):
        await main_module._run_workflow_response(
            main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"}),
            None, runtime_run_type="xpert", runtime_metadata=metadata,
        )
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert len(executions.list_items()) == 1
    assert executions.list_items()[0].status == "cancelled"
    assert all(state["cancel_requested"] for state in live.values())
    assert {run.status for run in await registry.list_runs()} == {"cancelled"}


@pytest.mark.asyncio
async def test_parent_cancel_survives_restart_before_child_resume(runtime, monkeypatch):
    tasks, executions, _, _ = runtime
    task, _, metadata = await parent(runtime)
    execution, _ = await child(runtime, metadata, "ready")
    await tasks.cancel_task(task.task_id)
    reloaded_tasks = AgentTaskStore(storage_dir=tasks.storage_path.parent)
    reloaded_executions = WorkflowExecutionStore(executions.storage_dir)
    monkeypatch.setattr(main_module, "agent_task_store", reloaded_tasks)
    monkeypatch.setattr(main_module, "workflow_execution_store", reloaded_executions)
    claimed = reloaded_executions.claim(execution.task_id, worker_id="recovery")
    with pytest.raises(WorkflowExecutionConflictError):
        await main_module._run_workflow_response(
            main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"}),
            None, runtime_run_type="xpert", resume_execution=claimed,
        )
    assert reloaded_executions.require(execution.task_id).status == "cancelled"


@pytest.mark.asyncio
async def test_child_query_does_not_use_global_list_limit(runtime):
    _, executions, _, _ = runtime
    task, handoff, metadata = await parent(runtime)
    execution, _ = await child(runtime, metadata)
    with executions._lock:
        for index in range(1005):
            executions._items[f"other-{index}"] = replace(execution, task_id=f"other-{index}", runtime_metadata={"agent_task_id": "other", "handoff_id": "other"}, updated_at=execution.updated_at + 1)
    assert execution.task_id not in {item.task_id for item in executions.list_items(limit=1000)}
    children = executions.list_agent_task_children(task.task_id, handoff_ids={handoff.handoff_id})
    assert [item.task_id for item in children] == [execution.task_id]
    children[0].runtime_metadata["agent_task_id"] = "injected"
    assert executions.require(execution.task_id).runtime_metadata == metadata


@pytest.mark.asyncio
async def test_registry_wait_does_not_delay_other_child_cancellation(runtime, monkeypatch):
    _, executions, registry, live = runtime
    task, _, metadata = await parent(runtime)
    await child(runtime, metadata, child_id="first")
    await child(runtime, metadata, child_id="second")
    reached, release = asyncio.Event(), asyncio.Event()
    original = registry.cancel_run

    async def blocked(*args, **kwargs):
        reached.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(registry, "cancel_run", blocked)
    cancellation = asyncio.create_task(main_module.cancel_agent_task(task.task_id))
    try:
        await asyncio.wait_for(reached.wait(), 3)
        assert executions.require("second").status == "cancelled"
        assert live["second"]["provider_cancel_event"].is_set()
    finally:
        release.set()
        await asyncio.wait_for(cancellation, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["classic", "timeout"])
async def test_existing_parent_cancel_entries_propagate(runtime, monkeypatch, entry):
    tasks, executions, _, _ = runtime
    task, handoff, metadata = await parent(runtime)
    child_execution, _ = await child(runtime, metadata, "approval")
    if entry == "timeout":
        await main_module.expire_workflow_handoff_execution(child_execution, handoff)
    else:
        executions.create(task_id="classic", run_id="classic-run", run_type="workflow", workflow=workflow(), inputs={}, source_kind="workflow_classic")
        executions.suspend("classic", wait_kind="agent_handoff", wait_id=handoff.handoff_id, continuation={})
        monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda *_: None)
        monkeypatch.setattr(main_module, "client_ip", lambda *_: "test")
        await main_module.cancel_workflow_task("classic", None)
        assert executions.require("classic").status == "cancelled"
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert executions.require(child_execution.task_id).status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_timeout_does_not_overwrite_terminal_child_run(runtime, terminal):
    tasks, executions, registry, _ = runtime
    _, handoff, metadata = await parent(runtime)
    execution, run = await child(runtime, metadata, terminal)
    handoff = await tasks.update_handoff_metadata(handoff.handoff_id, {"xpert_run_id": run.run_id})
    before = asdict(await registry.get_run(run.run_id))
    await main_module.expire_workflow_handoff_execution(execution, handoff)
    assert executions.require(execution.task_id).status == terminal
    assert asdict(await registry.get_run(run.run_id)) == before
