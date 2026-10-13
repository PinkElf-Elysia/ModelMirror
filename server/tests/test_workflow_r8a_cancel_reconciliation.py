"""Cancellation crash windows with real Stores and management callbacks."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace

import pytest

import server.main as main_module
from server.xpert_runtime import AgentTaskStore, RunRegistry, WorkflowExecutionStore
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
from server.xpert_runtime.execution_store import WorkflowExecutionStorageError
from server.xpert_runtime.workflow_handoff_coordinator import WorkflowHandoffCoordinator


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    monkeypatch.setattr(main_module, "agent_task_store", tasks)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "workflow_task_store", {})
    monkeypatch.setattr(main_module, "rate_limit_or_raise", lambda *_: None)
    monkeypatch.setattr(main_module, "client_ip", lambda *_: "test")
    return tasks, executions, registry


async def family(runtime, prefix="one"):
    tasks, executions, registry = runtime
    task = await tasks.create_task("取消对账", "synthetic")
    handoff = await tasks.create_handoff(task.task_id, "workflow", "xpert:child", "synthetic")
    await tasks.update_handoff_status(handoff.handoff_id, "accepted")
    root, child = f"{prefix}-root", f"{prefix}-child"
    for key in (root, child):
        run = await registry.create_run("workflow" if key == root else "xpert", "取消对账", status="running")
        executions.create(
            task_id=key, run_id=run.run_id,
            run_type="workflow" if key == root else "xpert",
            source_kind="workflow_classic" if key == root else "xpert_chat",
            workflow={"id": "synthetic", "nodes": [], "edges": []}, inputs={},
            runtime_metadata={} if key == root else {"agent_task_id": task.task_id, "handoff_id": handoff.handoff_id},
        )
    executions.suspend(root, wait_kind="agent_handoff", wait_id=handoff.handoff_id, continuation={"agent_state": {"handoff_id": handoff.handoff_id}})
    executions.suspend(child, wait_kind="approval", wait_id="approval", continuation={})
    # This legacy field now names the child, not the waiting parent.
    await tasks.update_handoff_metadata(handoff.handoff_id, {"workflow_task_id": child})
    return task, handoff, root, child


def reload_runtime(runtime, monkeypatch):
    tasks, executions, registry = runtime
    tasks = AgentTaskStore(storage_dir=tasks.storage_path.parent)
    executions = WorkflowExecutionStore(executions.storage_dir)
    monkeypatch.setattr(main_module, "agent_task_store", tasks)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    return tasks, executions, registry


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_after_task", [False, True])
async def test_repeated_cancel_recovers_committed_parent_after_restart(runtime, monkeypatch, crash_after_task):
    tasks, executions, _ = runtime
    task, _, root, child = await family(runtime)
    original = tasks.cancel_task

    async def interrupted(*args, **kwargs):
        if crash_after_task:
            await original(*args, **kwargs)
        raise OSError("synthetic process interruption")

    monkeypatch.setattr(tasks, "cancel_task", interrupted)
    with pytest.raises(OSError):
        await main_module.cancel_workflow_task(root, None)
    assert executions.require(root).status == "cancelled"
    assert executions.require(child).status == "waiting"
    tasks, executions, _ = reload_runtime(runtime, monkeypatch)
    await main_module.cancel_workflow_task(root, None)
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert executions.require(child).status == "cancelled"


def coordinator(runtime):
    tasks, executions, _ = runtime

    async def forbidden_resume(*_):
        pytest.fail("cancelled execution must not resume")

    return WorkflowHandoffCoordinator(
        tasks, executions, forbidden_resume,
        cancel_execution=main_module.reconcile_cancelled_workflow_handoff,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_stage", ["root", "task", "child"])
async def test_background_reconciliation_after_restart(runtime, monkeypatch, crash_stage):
    tasks, executions, registry = runtime
    task, handoff, root, child = await family(runtime)
    cancelled = executions.cancel(root)
    assert cancelled.wait_id is None and cancelled.continuation == {}
    assert executions.pending_handoff_cancellation_id(cancelled) == handoff.handoff_id
    assert "pending_handoff_cancellation_id" not in str(executions.serialize_public(cancelled))
    if crash_stage != "root":
        await tasks.cancel_task(task.task_id)
    if crash_stage == "child":
        await main_module._cancel_agent_task_workflow_children(task.task_id)
    runtime = reload_runtime(runtime, monkeypatch)
    tasks, executions, _ = runtime
    worker = coordinator(runtime)
    assert await worker.run_once() == 0
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert (await tasks.get_handoff(handoff.handoff_id)).status == "dead_letter"
    assert executions.require(child).status == "cancelled"
    assert executions.pending_handoff_cancellations() == []
    assert (await registry.get_run(cancelled.run_id)).status == "cancelled"
    before = [asdict(executions.require(key)) for key in (root, child)]
    await worker.run_once()
    await main_module.cancel_workflow_task(root, None)
    assert [asdict(executions.require(key)) for key in (root, child)] == before
    assert WorkflowExecutionStore(executions.storage_dir).pending_handoff_cancellations() == []


@pytest.mark.asyncio
async def test_ack_persist_failure_retains_request_for_restart(runtime, monkeypatch):
    _, executions, _ = runtime
    task, _, root, child = await family(runtime)
    executions.cancel(root)
    original_ack = executions.acknowledge_handoff_cancellation

    def fail_ack(*args, **kwargs):
        def disk_failure(*_):
            raise OSError("synthetic full disk")

        with monkeypatch.context() as scoped:
            scoped.setattr(executions, "_write_fsynced", disk_failure)
            return original_ack(*args, **kwargs)

    monkeypatch.setattr(executions, "acknowledge_handoff_cancellation", fail_ack)
    with pytest.raises(WorkflowExecutionStorageError):
        await main_module.reconcile_cancelled_workflow_handoff(executions.require(root))
    runtime = reload_runtime(runtime, monkeypatch)
    tasks, executions, _ = runtime
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert executions.require(child).status == "cancelled"
    assert len(executions.pending_handoff_cancellations()) == 1
    child_before = asdict(executions.require(child))
    await coordinator(runtime).run_once()
    assert executions.pending_handoff_cancellations() == []
    assert asdict(executions.require(child)) == child_before


@pytest.mark.asyncio
async def test_cancel_commit_failure_does_not_leave_terminal_without_request(runtime, monkeypatch):
    _, executions, _ = runtime
    _, handoff, root, _ = await family(runtime)

    def disk_failure(*_):
        raise OSError("synthetic full disk")

    monkeypatch.setattr(executions, "_write_fsynced", disk_failure)
    with pytest.raises(WorkflowExecutionStorageError):
        executions.cancel(root)
    reloaded = WorkflowExecutionStore(executions.storage_dir)
    assert reloaded.require(root).status == "waiting"
    assert reloaded.require(root).wait_id == handoff.handoff_id
    cancelled = reloaded.cancel(root)
    assert reloaded.pending_handoff_cancellation_id(cancelled) == handoff.handoff_id


@pytest.mark.asyncio
async def test_one_failed_relation_does_not_block_other_cancellations(runtime, monkeypatch):
    tasks, executions, _ = runtime
    first, first_handoff, first_root, _ = await family(runtime, "first")
    second, _, second_root, second_child = await family(runtime, "second")
    executions.cancel(first_root)
    executions.cancel(second_root)
    original = tasks.get_handoff

    async def missing(handoff_id):
        return None if handoff_id == first_handoff.handoff_id else await original(handoff_id)

    monkeypatch.setattr(tasks, "get_handoff", missing)
    await coordinator(runtime).run_once()
    assert (await tasks.get_task(first.task_id)).status == "pending"
    assert (await tasks.get_task(second.task_id)).status == "cancelled"
    assert executions.require(second_child).status == "cancelled"
    assert [item.task_id for item in executions.pending_handoff_cancellations()] == [first_root]
    monkeypatch.setattr(tasks, "get_handoff", original)
    await coordinator(runtime).run_once()
    assert executions.pending_handoff_cancellations() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_child_task_terminal_wins_without_overwriting_child(runtime, terminal):
    tasks, executions, registry = runtime
    task, handoff, root, child = await family(runtime)
    await tasks.update_task(task.task_id, status=terminal)
    child_before = asdict(executions.require(child))
    handoff_before = asdict(await tasks.get_handoff(handoff.handoff_id))
    run_before = asdict(await registry.get_run(executions.require(child).run_id))
    executions.cancel(root)
    await coordinator(runtime).run_once()
    assert (await tasks.get_task(task.task_id)).status == terminal
    assert asdict(executions.require(child)) == child_before
    assert asdict(await tasks.get_handoff(handoff.handoff_id)) == handoff_before
    assert asdict(await registry.get_run(executions.require(child).run_id)) == run_before
    assert executions.pending_handoff_cancellations() == []


@pytest.mark.asyncio
async def test_completed_parent_and_unrelated_family_untouched(runtime):
    tasks, executions, _ = runtime
    task, handoff, root, _ = await family(runtime)
    other, _, other_root, other_child = await family(runtime, "other")
    executions.mark_ready(root, wait_kind="agent_handoff", wait_id=handoff.handoff_id)
    claimed = executions.claim(root, worker_id="winner")
    executions.complete(root, result="done", expected_lease_token=claimed.lease_token)
    before = asdict(executions.require(root))
    await main_module.cancel_workflow_task(root, None)
    await coordinator(runtime).run_once()
    assert asdict(executions.require(root)) == before
    assert (await tasks.get_task(task.task_id)).status == "pending"
    executions.cancel(other_root)
    await coordinator(runtime).run_once()
    assert (await tasks.get_task(task.task_id)).status == "pending"
    assert (await tasks.get_task(other.task_id)).status == "cancelled"
    assert executions.require(other_child).status == "cancelled"


@pytest.mark.asyncio
async def test_pending_query_is_copy_and_not_global_pagination(runtime):
    _, executions, _ = runtime
    _, handoff, root, child = await family(runtime)
    cancelled = executions.cancel(root)
    with executions._lock:
        for index in range(1005):
            executions._items[f"noise-{index}"] = replace(
                executions.require(child), task_id=f"noise-{index}", updated_at=cancelled.updated_at + 1,
            )
    assert root not in {item.task_id for item in executions.list_items(limit=1000)}
    pending = executions.pending_handoff_cancellations()
    assert [item.task_id for item in pending] == [root]
    pending[0].runtime_metadata.clear()
    assert executions.pending_handoff_cancellation_id(executions.require(root)) == handoff.handoff_id
    with pytest.raises(WorkflowExecutionConflictError):
        executions.acknowledge_handoff_cancellation(root, handoff_id="different")
    assert len(executions.pending_handoff_cancellations()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source,kind", [("workflow_classic", "approval"), ("workflow_classic", "client_tool"), ("workflow_deployment", "agent_handoff")])
async def test_request_cannot_be_injected_or_inferred_from_other_wait(runtime, source, kind):
    tasks, executions, _ = runtime
    task, handoff, _, _ = await family(runtime)
    executions.create(
        task_id="injected", run_id="injected-run", run_type="workflow", source_kind=source,
        workflow={}, inputs={}, runtime_metadata={"pending_handoff_cancellation_id": handoff.handoff_id},
    )
    executions.suspend("injected", wait_kind=kind, wait_id=handoff.handoff_id, continuation={})
    executions.cancel("injected")
    assert executions.pending_handoff_cancellations() == []
    await coordinator(runtime).run_once()
    assert (await tasks.get_task(task.task_id)).status == "pending"


@pytest.mark.asyncio
async def test_stale_callback_rechecks_store_and_duplicate_callbacks_are_safe(runtime):
    tasks, executions, _ = runtime
    task, _, root, child = await family(runtime)
    old = executions.require(root)
    old.runtime_metadata["pending_handoff_cancellation_id"] = "unrelated"
    executions.cancel(root)
    await asyncio.gather(*[
        main_module.reconcile_cancelled_workflow_handoff(old) for _ in range(2)
    ])
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert executions.require(child).status == "cancelled"
    assert executions.pending_handoff_cancellations() == []


def test_production_coordinator_has_cancel_reconciliation(runtime, monkeypatch):
    monkeypatch.setattr(main_module, "workflow_handoff_coordinator", None)
    assert main_module.get_workflow_handoff_coordinator().cancel_execution is main_module.reconcile_cancelled_workflow_handoff


@pytest.mark.asyncio
async def test_registry_delay_cannot_precede_durable_child_cancel(runtime, monkeypatch):
    tasks, executions, registry = runtime
    task, _, root, child = await family(runtime)
    reached, release = asyncio.Event(), asyncio.Event()
    original = registry.cancel_run

    async def blocked(*args, **kwargs):
        reached.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(registry, "cancel_run", blocked)
    main_module.workflow_task_store[root] = {"provider_cancel_event": asyncio.Event()}
    cancellation = asyncio.create_task(main_module.cancel_workflow_task(root, None))
    try:
        await asyncio.wait_for(reached.wait(), 3)
        assert main_module.workflow_task_store[root]["provider_cancel_event"].is_set()
        assert (await tasks.get_task(task.task_id)).status == "cancelled"
        assert executions.require(child).status == "cancelled"
    finally:
        release.set()
        await asyncio.wait_for(cancellation, 3)


@pytest.mark.asyncio
async def test_legacy_cancel_without_durable_relation_is_not_guessed(runtime):
    tasks, executions, _ = runtime
    task, handoff, root, child = await family(runtime)
    executions.cancel(root)
    # Emulate an already-cancelled old snapshot that never recorded a request.
    with executions._lock:
        executions._items[root].runtime_metadata.pop("pending_handoff_cancellation_id", None)
        executions._persist_unlocked()
    await tasks.update_handoff_metadata(handoff.handoff_id, {"workflow_task_id": root})
    await main_module.cancel_workflow_task(root, None)
    await coordinator(runtime).run_once()
    assert (await tasks.get_task(task.task_id)).status == "pending"
    assert executions.require(child).status == "waiting"
