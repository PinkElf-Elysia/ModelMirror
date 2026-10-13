"""Cancellation of a persisted Goal must reach its registered child tasks."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from server.xpert_runtime.agent_tasks import AgentTaskStore
from server.xpert_runtime.goal_coordinator import GoalCoordinator, PinnedXpert
from server.xpert_runtime.goals import GoalStep, GoalStore
from server.xpert_runtime.run_registry import RunRegistry
from server.xpert_runtime.execution_store import WorkflowExecutionStore


async def setup(tmp_path):
    goals = GoalStore(tmp_path / "goals")
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    registry = RunRegistry()

    async def resolve(reference):
        return PinnedXpert(reference, reference, 1, "固定合成目标")

    worker = GoalCoordinator(goals, tasks, registry, None, resolve)
    goal = await goals.create_goal(title="取消树验收", objective="仅合成测试", planner_xpert_id="planner", planner_version=1)
    goal = await goals.replace_plan(goal.goal_id, steps=[
        GoalStep("one", "执行", "合成任务", "reader", target_version=1),
        GoalStep("last", "交付", "合成交付", "reader", target_version=1, depends_on=["one"]),
    ], final_step_id="last")
    goal = await goals.update_goal(goal.goal_id, status="running")
    return worker, goal


async def register(worker, goal):
    task = await worker.task_store.create_task(
        "子任务", "synthetic", source_agent=f"goal:{goal.goal_id}",
        metadata={"goal_id": goal.goal_id, "goal_step_id": "one"},
    )
    handoff = await worker.task_store.create_handoff(
        task.task_id, f"goal:{goal.goal_id}", "xpert:reader", "synthetic",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
    )
    return task, handoff


@pytest.mark.asyncio
async def test_goal_cancel_reaches_child_before_step_backlink(tmp_path):
    worker, goal = await setup(tmp_path)
    task, handoff = await register(worker, goal)
    assert (await worker.goal_store.require_goal(goal.goal_id)).steps[0].task_id is None
    await worker.cancel_goal(goal.goal_id)
    assert (await worker.task_store.get_task(task.task_id)).status == "cancelled"
    assert (await worker.task_store.get_handoff(handoff.handoff_id)).status == "dead_letter"


@pytest.mark.asyncio
async def test_restart_reconciles_cancelled_goal_outside_ui_page(tmp_path):
    worker, goal = await setup(tmp_path)
    task, _ = await register(worker, goal)
    await worker.goal_store.cancel_goal(goal.goal_id)
    for index in range(205):
        newer = await worker.goal_store.create_goal(title=f"new-{index}", objective="synthetic", planner_xpert_id="planner", planner_version=1)
        await worker.goal_store.update_goal(newer.goal_id, status="awaiting_review")
    assert goal.goal_id not in {item.goal_id for item in await worker.goal_store.list_goals(limit=500)}
    worker = GoalCoordinator(GoalStore(tmp_path / "goals"), AgentTaskStore(storage_dir=tmp_path / "tasks"), RunRegistry(), None, None)
    await worker.run_once()
    assert (await worker.task_store.get_task(task.task_id)).status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before_create", "task_created", "handoff_created", "before_ready"])
async def test_cancel_at_dispatch_boundary_never_leaves_executable_child(tmp_path, monkeypatch, boundary):
    worker, goal = await setup(tmp_path)
    target = worker.task_store
    name = "create_handoff" if boundary == "handoff_created" else "update_handoff_metadata" if boundary == "before_ready" else "create_task"
    original = getattr(target, name)
    fired = False
    pending = []

    async def interrupt(*args, **kwargs):
        nonlocal fired
        if fired:
            return await original(*args, **kwargs)
        fired = True
        if boundary == "before_ready":
            # Reproduce a separate request, not a reentrant callback.
            pending.append(asyncio.create_task(worker.cancel_goal(goal.goal_id)))
            await asyncio.sleep(0)
            result = await original(*args, **kwargs)
            return result
        if boundary == "before_create":
            await worker.cancel_goal(goal.goal_id)
        result = await original(*args, **kwargs)
        if boundary != "before_create":
            await worker.cancel_goal(goal.goal_id)
        return result

    monkeypatch.setattr(target, name, interrupt)
    await worker.process_goal(goal.goal_id)
    # Drain the independent cancellation request from the publication race.
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), 3)
    await worker.cancel_goal(goal.goal_id)
    assert (await worker.goal_store.require_goal(goal.goal_id)).status == "cancelled"
    children = await worker.task_store.list_tasks(limit=100)
    assert children and all(item.status == "cancelled" for item in children)
    for task in children:
        assert all(item.status == "dead_letter" for item in await worker.task_store.list_handoffs(task.task_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_cancel_preserves_terminal_child_and_result(tmp_path, terminal):
    worker, goal = await setup(tmp_path)
    task, _ = await register(worker, goal)
    await worker.task_store.update_task(task.task_id, status=terminal, result="original")
    before = asdict(await worker.task_store.get_task(task.task_id))
    await worker.cancel_goal(goal.goal_id)
    assert asdict(await worker.task_store.get_task(task.task_id)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["source", "goal", "step", "missing"])
async def test_cancel_does_not_infer_unproven_goal_association(tmp_path, mismatch):
    worker, goal = await setup(tmp_path)
    metadata = {"goal_id": goal.goal_id, "goal_step_id": "one"}
    source = f"goal:{goal.goal_id}"
    if mismatch == "source":
        source = "other"
    elif mismatch == "goal":
        metadata["goal_id"] = "other"
    elif mismatch == "step":
        metadata["goal_step_id"] = "unknown"
    else:
        metadata = {}
    task = await worker.task_store.create_task("unrelated", "synthetic", source_agent=source, metadata=metadata)
    await worker.cancel_goal(goal.goal_id)
    assert (await worker.task_store.get_task(task.task_id)).status == "pending"


@pytest.mark.asyncio
async def test_goal_task_query_unpaged_and_detached(tmp_path):
    worker, goal = await setup(tmp_path)
    for _ in range(55):
        await register(worker, goal)
    tasks = await worker.task_store.list_goal_tasks(goal.goal_id)
    assert len(tasks) == 55
    tasks[0].metadata["goal_step_id"] = "mutated"
    assert (await worker.task_store.get_task(tasks[0].task_id)).metadata["goal_step_id"] == "one"
    await worker.cancel_goal(goal.goal_id)
    assert all(task.status == "cancelled" for task in await worker.task_store.list_goal_tasks(goal.goal_id))


@pytest.mark.asyncio
async def test_cancel_failure_does_not_block_siblings_and_restart_retries(tmp_path):
    worker, goal = await setup(tmp_path)
    first, _ = await register(worker, goal)
    second, _ = await register(worker, goal)
    calls = []

    async def downstream(task_id):
        calls.append(task_id)
        if task_id == first.task_id:
            raise OSError("synthetic child store unavailable")

    worker.cancel_task_callback = downstream
    with pytest.raises(OSError):
        await worker.cancel_goal(goal.goal_id)
    assert calls == [first.task_id, second.task_id]
    assert (await worker.goal_store.require_goal(goal.goal_id)).status == "cancelled"
    assert all(task.status == "cancelled" for task in await worker.task_store.list_goal_tasks(goal.goal_id))
    calls.clear()

    async def recovered(task_id):
        calls.append(task_id)

    worker = GoalCoordinator(GoalStore(tmp_path / "goals"), AgentTaskStore(storage_dir=tmp_path / "tasks"), RunRegistry(), None, None, cancel_task=recovered)
    await worker.run_once()
    assert set(calls) == {first.task_id, second.task_id}
    current = await worker.goal_store.require_goal(goal.goal_id)
    assert (await worker.run_registry.get_run(current.run_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_failure_in_one_goal_does_not_starve_other_goal(tmp_path):
    worker, first = await setup(tmp_path)
    first_task, _ = await register(worker, first)
    second = await worker.goal_store.create_goal(title="second", objective="synthetic", planner_xpert_id="planner", planner_version=1)
    second = await worker.goal_store.replace_plan(second.goal_id, steps=first.steps, final_step_id="last")
    second_task, _ = await register(worker, second)
    for goal in (first, second):
        await worker.goal_store.cancel_goal(goal.goal_id)
    calls = []

    async def partial(task_id):
        calls.append(task_id)
        if task_id == first_task.task_id:
            raise OSError("synthetic failure")

    worker.cancel_task_callback = partial
    await worker.run_once()
    assert set(calls) == {first_task.task_id, second_task.task_id}
    assert (await worker.task_store.get_task(second_task.task_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_waits_for_publication_then_revokes_ready_handoff(tmp_path, monkeypatch):
    worker, goal = await setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = worker.task_store.update_handoff_metadata

    async def blocked_ready(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(worker.task_store, "update_handoff_metadata", blocked_ready)
    dispatch = asyncio.create_task(worker.process_goal(goal.goal_id))
    await asyncio.wait_for(entered.wait(), 3)
    cancellation = asyncio.create_task(worker.cancel_goal(goal.goal_id))
    try:
        await asyncio.sleep(0)
        assert not cancellation.done()
        assert (await worker.goal_store.require_goal(goal.goal_id)).status == "running"
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(dispatch, cancellation), 3)
    task = (await worker.task_store.list_goal_tasks(goal.goal_id))[0]
    handoff = (await worker.task_store.list_handoffs(task.task_id))[0]
    assert task.status == "cancelled"
    assert handoff.metadata["ready_for_execution"] is False
    assert handoff.status == "dead_letter"


@pytest.mark.asyncio
async def test_cancellation_committed_before_publication_prevents_ready(tmp_path, monkeypatch):
    worker, goal = await setup(tmp_path)
    original = worker.run_registry.record_checkpoint
    ready_calls = []
    original_ready = worker.task_store.update_handoff_metadata

    async def interrupt(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kwargs.get("event_type") == "agent_handoff.created":
            await worker.cancel_goal(goal.goal_id)
        return result

    async def ready(*args, **kwargs):
        ready_calls.append(args)
        return await original_ready(*args, **kwargs)

    monkeypatch.setattr(worker.run_registry, "record_checkpoint", interrupt)
    monkeypatch.setattr(worker.task_store, "update_handoff_metadata", ready)
    await worker.process_goal(goal.goal_id)
    assert not ready_calls
    assert all(task.status == "cancelled" for task in await worker.task_store.list_goal_tasks(goal.goal_id))


@pytest.fixture
def main_runtime(tmp_path, monkeypatch):
    import server.main as main

    executions = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main, "workflow_execution_store", executions)
    monkeypatch.setattr(main, "workflow_task_store", {})
    monkeypatch.setattr(main, "goal_coordinator", None)
    monkeypatch.setattr(main, "GOAL_COORDINATOR_ENABLED", True)
    return main, executions


async def wired(worker, main, monkeypatch):
    monkeypatch.setattr(main, "goal_store", worker.goal_store)
    monkeypatch.setattr(main, "agent_task_store", worker.task_store)
    monkeypatch.setattr(main, "run_registry", worker.run_registry)
    return main.get_goal_coordinator()


async def durable_child(worker, main, executions, task, handoff, *, wait):
    run = await worker.run_registry.create_run("xpert", "持久子执行", status="running")
    executions.create(
        task_id="child", run_id=run.run_id, run_type="xpert", workflow={"id": "synthetic", "nodes": [], "edges": []}, inputs={},
        runtime_metadata={"agent_task_id": task.task_id, "handoff_id": handoff.handoff_id},
    )
    if wait:
        executions.suspend("child", wait_kind=wait, wait_id="wait", continuation={"queue": []}, resume_at=100 if wait == "timer" else None)
    main.workflow_task_store["child"] = {"cancel_requested": False, "provider_cancel_event": asyncio.Event(), "pause_event": asyncio.Event()}
    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [None, "approval", "client_tool", "timer", "agent_handoff"])
async def test_production_goal_cancel_reaches_durable_child_and_registry(tmp_path, monkeypatch, main_runtime, wait):
    main, executions = main_runtime
    worker, goal = await setup(tmp_path)
    worker = await wired(worker, main, monkeypatch)
    task, handoff = await register(worker, goal)
    run = await durable_child(worker, main, executions, task, handoff, wait=wait)
    await worker.cancel_goal(goal.goal_id)
    assert executions.require("child").status == "cancelled"
    assert (await worker.run_registry.get_run(run.run_id)).status == "cancelled"
    assert main.workflow_task_store["child"]["provider_cancel_event"].is_set()
    revision = executions.require("child").revision
    await worker.cancel_goal(goal.goal_id)
    await worker.run_once()
    assert executions.require("child").revision == revision


@pytest.mark.asyncio
async def test_restart_after_task_commit_before_child_commit(tmp_path, monkeypatch, main_runtime):
    main, executions = main_runtime
    worker, goal = await setup(tmp_path)
    worker = await wired(worker, main, monkeypatch)
    task, handoff = await register(worker, goal)
    await durable_child(worker, main, executions, task, handoff, wait="approval")
    original = executions.cancel

    def unavailable(*args, **kwargs):
        raise OSError("synthetic interrupted child commit")

    monkeypatch.setattr(executions, "cancel", unavailable)
    with pytest.raises(OSError):
        await worker.cancel_goal(goal.goal_id)
    assert executions.require("child").status != "cancelled"
    assert (await worker.task_store.get_task(task.task_id)).status == "cancelled"
    monkeypatch.setattr(executions, "cancel", original)
    monkeypatch.setattr(main, "goal_coordinator", None)
    worker.goal_store = GoalStore(tmp_path / "goals")
    worker.task_store = AgentTaskStore(storage_dir=tmp_path / "tasks")
    monkeypatch.setattr(main, "workflow_execution_store", WorkflowExecutionStore(tmp_path / "executions"))
    worker = await wired(worker, main, monkeypatch)
    await worker.run_once()
    assert main.workflow_execution_store.require("child").status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_during_create_audit_still_finds_persisted_task(tmp_path, monkeypatch):
    worker, goal = await setup(tmp_path)
    original = worker.task_store._record

    async def failed_audit(event, **kwargs):
        if event == "agent.task.created":
            await worker.cancel_goal(goal.goal_id)
            raise OSError("synthetic audit failure after task commit")
        return await original(event, **kwargs)

    monkeypatch.setattr(worker.task_store, "_record", failed_audit)
    with pytest.raises(OSError):
        await worker.process_goal(goal.goal_id)
    tasks = await AgentTaskStore(storage_dir=tmp_path / "tasks").list_goal_tasks(goal.goal_id)
    assert len(tasks) == 1 and tasks[0].status == "cancelled"


@pytest.mark.asyncio
async def test_preparation_error_is_not_silently_accepted(tmp_path, monkeypatch):
    worker, goal = await setup(tmp_path)

    async def failed(*args, **kwargs):
        raise ValueError("synthetic registration error")

    monkeypatch.setattr(worker.task_store, "create_handoff", failed)
    with pytest.raises(ValueError, match="registration error"):
        await worker.process_goal(goal.goal_id)
    assert (await worker.goal_store.require_goal(goal.goal_id)).status == "running"


@pytest.mark.asyncio
async def test_task_store_failure_rolls_back_then_periodic_reconciliation_retries(tmp_path, monkeypatch):
    worker, goal = await setup(tmp_path)
    task, handoff = await register(worker, goal)
    original = worker.task_store._persist_unlocked

    def failed():
        raise OSError("synthetic task commit failure")

    monkeypatch.setattr(worker.task_store, "_persist_unlocked", failed)
    with pytest.raises(OSError):
        await worker.cancel_goal(goal.goal_id)
    assert (await worker.task_store.get_task(task.task_id)).status == "pending"
    assert (await worker.task_store.get_handoff(handoff.handoff_id)).status != "dead_letter"
    monkeypatch.setattr(worker.task_store, "_persist_unlocked", original)
    await worker.run_once()
    assert (await worker.task_store.get_task(task.task_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_completion_wins_no_downstream_cancel(tmp_path):
    worker, goal = await setup(tmp_path)
    task, _ = await register(worker, goal)
    await worker.goal_store.update_goal(goal.goal_id, status="completed", result="original")
    result = await worker.cancel_goal(goal.goal_id)
    assert result.status == "completed"
    assert result.result == "original"
    assert (await worker.task_store.get_task(task.task_id)).status == "pending"


@pytest.mark.asyncio
async def test_disabled_coordinator_does_not_reconcile(tmp_path):
    worker, goal = await setup(tmp_path)
    task, _ = await register(worker, goal)
    await worker.goal_store.cancel_goal(goal.goal_id)
    worker.enabled = False
    assert await worker.run_once() == 0
    assert (await worker.task_store.get_task(task.task_id)).status == "pending"


@pytest.mark.asyncio
async def test_restart_between_step_link_and_ready_does_not_strand_or_duplicate(tmp_path, monkeypatch):
    worker, goal = await setup(tmp_path)

    async def interrupted(*args, **kwargs):
        raise OSError("synthetic interrupted readiness commit")

    monkeypatch.setattr(worker.task_store, "update_handoff_metadata", interrupted)
    with pytest.raises(OSError):
        await worker.process_goal(goal.goal_id)
    current = await worker.goal_store.require_goal(goal.goal_id)
    step = current.steps[0]
    assert step.task_id and step.handoff_id
    assert (await worker.task_store.get_handoff(step.handoff_id)).metadata["ready_for_execution"] is False
    worker = GoalCoordinator(GoalStore(tmp_path / "goals"), AgentTaskStore(storage_dir=tmp_path / "tasks"), RunRegistry(), None, None)
    await worker.run_once()
    assert len(await worker.task_store.list_goal_tasks(goal.goal_id)) == 1
    assert (await worker.task_store.get_handoff(step.handoff_id)).metadata["ready_for_execution"] is True
    await worker.run_once()
    assert len(await worker.task_store.list_goal_tasks(goal.goal_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"attempts": 1}, {"workflow_task_id": "uncertain"}, {"xpert_run_id": "uncertain"},
    {"attempts": False}, {"attempts": "0"}, {"target_xpert_version": True},
    {"lease_token": "old-worker"}, {"target_xpert_version": 2},
    {"goal_step_id": "last"}, {"execution_mode": "manual"},
])
async def test_unstarted_recovery_rejects_uncertainty_or_binding_drift(tmp_path, monkeypatch, patch):
    worker, goal = await setup(tmp_path)

    async def interrupted(*args, **kwargs):
        raise OSError("synthetic interrupted readiness commit")

    with monkeypatch.context() as scoped:
        scoped.setattr(worker.task_store, "update_handoff_metadata", interrupted)
        with pytest.raises(OSError):
            await worker.process_goal(goal.goal_id)
    step = (await worker.goal_store.require_goal(goal.goal_id)).steps[0]
    await worker.task_store.update_handoff_metadata(step.handoff_id, patch)
    worker = GoalCoordinator(GoalStore(tmp_path / "goals"), AgentTaskStore(storage_dir=tmp_path / "tasks"), RunRegistry(), None, None)
    await worker.run_once()
    assert (await worker.task_store.get_handoff(step.handoff_id)).metadata["ready_for_execution"] is False
    assert len(await worker.task_store.list_goal_tasks(goal.goal_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["paused", "cancelled"])
async def test_unstarted_recovery_respects_goal_decision(tmp_path, monkeypatch, status):
    worker, goal = await setup(tmp_path)

    async def interrupted(*args, **kwargs):
        raise OSError("synthetic interrupted readiness commit")

    with monkeypatch.context() as scoped:
        scoped.setattr(worker.task_store, "update_handoff_metadata", interrupted)
        with pytest.raises(OSError):
            await worker.process_goal(goal.goal_id)
    step = (await worker.goal_store.require_goal(goal.goal_id)).steps[0]
    if status == "cancelled":
        await worker.goal_store.cancel_goal(goal.goal_id)
    else:
        await worker.goal_store.update_goal(goal.goal_id, status=status)
    await worker.run_once()
    assert (await worker.task_store.get_handoff(step.handoff_id)).metadata["ready_for_execution"] is False


@pytest.mark.asyncio
async def test_unstarted_publication_persistence_failure_keeps_not_ready(tmp_path, monkeypatch):
    worker, goal = await setup(tmp_path)

    async def interrupted(*args, **kwargs):
        raise OSError("synthetic interrupted readiness commit")

    with monkeypatch.context() as scoped:
        scoped.setattr(worker.task_store, "update_handoff_metadata", interrupted)
        with pytest.raises(OSError):
            await worker.process_goal(goal.goal_id)
    step = (await worker.goal_store.require_goal(goal.goal_id)).steps[0]

    def disk_failure():
        raise OSError("synthetic task storage unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(worker.task_store, "_persist_unlocked", disk_failure)
        with pytest.raises(OSError):
            await worker.run_once()
    for tasks in (worker.task_store, AgentTaskStore(storage_dir=tmp_path / "tasks")):
        assert (await tasks.get_handoff(step.handoff_id)).metadata["ready_for_execution"] is False
    await worker.run_once()
    assert (await worker.task_store.get_handoff(step.handoff_id)).metadata["ready_for_execution"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [[], {}])
async def test_malformed_association_does_not_starve_valid_child(tmp_path, malformed):
    worker, goal = await setup(tmp_path)
    invalid = await worker.task_store.create_task(
        "无效关联", "synthetic", source_agent=f"goal:{goal.goal_id}",
        metadata={"goal_id": goal.goal_id, "goal_step_id": malformed},
    )
    valid, _ = await register(worker, goal)
    await worker.cancel_goal(goal.goal_id)
    assert (await worker.task_store.get_task(invalid.task_id)).status == "pending"
    assert (await worker.task_store.get_task(valid.task_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_slow_child_projection_cannot_delay_sibling_task_cancellation(tmp_path):
    worker, goal = await setup(tmp_path)
    first, _ = await register(worker, goal)
    second, _ = await register(worker, goal)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(task_id):
        if task_id == first.task_id:
            entered.set()
            await release.wait()

    worker.cancel_task_callback = delayed
    operation = asyncio.create_task(worker.cancel_goal(goal.goal_id))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert (await worker.task_store.get_task(second.task_id)).status == "cancelled"
    finally:
        release.set()
        await asyncio.wait_for(operation, 3)
