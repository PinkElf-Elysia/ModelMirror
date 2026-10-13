"""Goal terminal decisions must survive late callbacks and mutable snapshots."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from server.xpert_runtime.agent_tasks import AgentTaskStore
from server.xpert_runtime.goal_coordinator import GoalCoordinator, GoalPlan, PinnedXpert
from server.xpert_runtime.goals import GoalConflictError, GoalStep, GoalStore
from server.xpert_runtime.run_registry import RunRegistry


def steps():
    return [
        GoalStep("first", "准备", "准备合成输入", "reader"),
        GoalStep("last", "交付", "交付合成结果", "writer", depends_on=["first"]),
    ]


async def create(store, *, planned=True):
    goal = await store.create_goal(
        title="终态验收", objective="验证取消和迟到结果", planner_xpert_id="planner", planner_version=1,
    )
    if planned:
        goal = await store.replace_plan(goal.goal_id, steps=steps(), final_step_id="last")
    return goal


def coordinator(store, planner):
    async def resolve(reference):
        return PinnedXpert(reference, reference, 1, "固定合成目标")

    return GoalCoordinator(store, AgentTaskStore(), RunRegistry(), planner, resolve)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_cancel_during_planning_preserves_terminal_after_callback(tmp_path, failed):
    store = GoalStore(tmp_path)
    goal = await create(store, planned=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def planner(*_):
        entered.set()
        await release.wait()
        if failed:
            raise ValueError("synthetic late planning failure")
        return GoalPlan("合成计划", "last", steps())

    worker = coordinator(store, planner)
    planning = asyncio.create_task(worker.plan_goal(goal.goal_id))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        cancelled = await worker.cancel_goal(goal.goal_id)
        before = asdict(cancelled)
    finally:
        release.set()
    result = await asyncio.wait_for(planning, 3)
    assert result.status == "cancelled"
    assert asdict(await store.require_goal(goal.goal_id)) == before
    assert asdict(await GoalStore(tmp_path).require_goal(goal.goal_id)) == before
    assert (await worker.run_registry.get_run(result.run_id)).status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
@pytest.mark.parametrize("entry", ["goal", "step", "plan", "mutation"])
async def test_store_rejects_terminal_business_mutations(tmp_path, terminal, entry):
    store = GoalStore(tmp_path)
    goal = await create(store)
    await store.update_goal(goal.goal_id, status=terminal, result="original")
    before = asdict(await store.require_goal(goal.goal_id))
    with pytest.raises(GoalConflictError):
        if entry == "goal":
            await store.update_goal(goal.goal_id, status="running", result="late")
        elif entry == "step":
            await store.update_step(goal.goal_id, "first", status="running", task_id="late")
        elif entry == "plan":
            await store.replace_plan(goal.goal_id, steps=steps(), final_step_id="last")
        else:
            await store.mutate_steps(goal.goal_id, lambda current: setattr(current, "status", "running"))
    assert asdict(await store.require_goal(goal.goal_id)) == before
    assert asdict(await GoalStore(tmp_path).require_goal(goal.goal_id)) == before


@pytest.mark.asyncio
async def test_public_snapshots_cannot_mutate_store(tmp_path):
    store = GoalStore(tmp_path)
    goal = await create(store)
    before = asdict(goal)
    snapshots = [goal, await store.get_goal(goal.goal_id), await store.require_goal(goal.goal_id), (await store.list_goals())[0]]
    for snapshot in snapshots:
        snapshot.status = "cancelled"
        snapshot.steps[0].depends_on.append("injected")
    assert asdict(await store.require_goal(goal.goal_id)) == before


@pytest.mark.asyncio
async def test_rejected_mutation_does_not_leak_partial_changes(tmp_path):
    store = GoalStore(tmp_path)
    goal = await create(store)
    before = asdict(goal)

    def invalid(current):
        current.steps[0].task_id = "partial"
        current.status = "cancelled"
        raise GoalConflictError("synthetic rejection")

    with pytest.raises(GoalConflictError):
        await store.mutate_steps(goal.goal_id, invalid)
    assert asdict(await store.require_goal(goal.goal_id)) == before
    assert asdict(await GoalStore(tmp_path).require_goal(goal.goal_id)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["goal", "step", "plan", "mutation", "cancel"])
async def test_failed_persistence_does_not_publish_uncommitted_state(tmp_path, monkeypatch, entry):
    store = GoalStore(tmp_path)
    goal = await create(store)
    before = asdict(goal)

    def disk_failure():
        raise OSError("synthetic write failure before commit")

    monkeypatch.setattr(store, "_persist_unlocked", disk_failure)
    with pytest.raises(OSError):
        if entry == "goal":
            await store.update_goal(goal.goal_id, status="running")
        elif entry == "step":
            await store.update_step(goal.goal_id, "first", status="running")
        elif entry == "plan":
            await store.replace_plan(goal.goal_id, steps=steps(), final_step_id="last", summary="replacement")
        elif entry == "mutation":
            await store.mutate_steps(goal.goal_id, lambda current: setattr(current, "status", "paused"))
        else:
            await store.cancel_goal(goal.goal_id)
    assert asdict(await store.require_goal(goal.goal_id)) == before
    assert asdict(await GoalStore(tmp_path).require_goal(goal.goal_id)) == before


@pytest.mark.asyncio
async def test_failed_create_does_not_publish_goal(tmp_path, monkeypatch):
    store = GoalStore(tmp_path)

    def disk_failure():
        raise OSError("synthetic write failure before commit")

    monkeypatch.setattr(store, "_persist_unlocked", disk_failure)
    with pytest.raises(OSError):
        await create(store, planned=False)
    assert await store.list_goals() == []
    assert await GoalStore(tmp_path).list_goals() == []


@pytest.mark.asyncio
async def test_mutation_capture_and_patch_values_are_not_store_aliases(tmp_path):
    store = GoalStore(tmp_path)
    goal = await create(store)
    captured = []

    def mutation(current):
        captured.append(current)
        current.steps[0].title = "已修改"

    updated = await store.mutate_steps(goal.goal_id, mutation)
    captured[0].steps.clear()
    updated.status = "cancelled"
    assert (await store.require_goal(goal.goal_id)).status == "awaiting_review"
    assert (await store.require_goal(goal.goal_id)).steps[0].title == "已修改"
    depends = ["first"]
    patched = await store.update_step(goal.goal_id, "last", depends_on=depends)
    depends.append("injected")
    patched.depends_on.clear()
    assert (await store.require_goal(goal.goal_id)).steps[-1].depends_on == ["first"]


@pytest.mark.asyncio
async def test_mutation_cannot_replace_goal_identity(tmp_path):
    store = GoalStore(tmp_path)
    goal = await create(store)
    with pytest.raises(GoalConflictError):
        await store.mutate_steps(goal.goal_id, lambda current: setattr(current, "goal_id", "other"))
    assert await store.get_goal("other") is None
    assert (await store.require_goal(goal.goal_id)).status == "awaiting_review"


@pytest.mark.asyncio
async def test_cancel_is_one_commit_preserves_finished_steps_and_replays(tmp_path, monkeypatch):
    store = GoalStore(tmp_path)
    goal = await create(store)
    await store.update_step(goal.goal_id, "first", status="completed", result="original", task_id="task", handoff_id="handoff")
    await store.update_step(goal.goal_id, "last", status="running", task_id="last-task", handoff_id="last-handoff")
    first = asdict((await store.require_goal(goal.goal_id)).steps[0])
    original = store._persist_unlocked
    commits = []

    def persist():
        commits.append(asdict(store._goals[goal.goal_id]))
        original()

    monkeypatch.setattr(store, "_persist_unlocked", persist)
    cancelled = await store.cancel_goal(goal.goal_id)
    assert len(commits) == 1
    assert commits[0]["status"] == "cancelled"
    assert commits[0]["steps"][1]["status"] == "cancelled"
    assert asdict(cancelled.steps[0]) == first
    assert (cancelled.steps[1].task_id, cancelled.steps[1].handoff_id) == ("last-task", "last-handoff")
    assert asdict(await store.cancel_goal(goal.goal_id)) == asdict(cancelled)
    assert len(commits) == 1
    reloaded = GoalStore(tmp_path)
    assert asdict(await reloaded.cancel_goal(goal.goal_id)) == asdict(cancelled)


@pytest.mark.asyncio
async def test_completion_wins_cancel_race_under_store_lock(tmp_path, monkeypatch):
    store = GoalStore(tmp_path)
    goal = await create(store)
    worker = coordinator(store, None)
    original = store.cancel_goal

    async def complete_then_cancel(goal_id):
        await store.update_goal(goal_id, status="completed", result="winner")
        return await original(goal_id)

    monkeypatch.setattr(store, "cancel_goal", complete_then_cancel)
    result = await worker.cancel_goal(goal.goal_id)
    assert result.status == "completed" and result.result == "winner"
    assert await worker.run_registry.list_runs() == []


@pytest.mark.asyncio
async def test_cancel_during_step_reconciliation_ignores_late_success(tmp_path, monkeypatch):
    store = GoalStore(tmp_path)
    goal = await create(store)
    worker = coordinator(store, None)
    await worker.start_goal(goal.goal_id)
    await worker.process_goal(goal.goal_id)
    running = await store.require_goal(goal.goal_id)
    first = running.steps[0]
    await worker.task_store.update_task(first.task_id, status="completed", result="late")
    await worker.task_store.update_handoff_status(first.handoff_id, "accepted")
    await worker.task_store.update_handoff_status(first.handoff_id, "completed")
    entered, release = asyncio.Event(), asyncio.Event()
    original = worker.task_store.get_handoff

    async def delayed(handoff_id):
        result = await original(handoff_id)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(worker.task_store, "get_handoff", delayed)
    processing = asyncio.create_task(worker.process_goal(goal.goal_id))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        cancelled = await worker.cancel_goal(goal.goal_id)
        before = asdict(cancelled)
    finally:
        release.set()
    assert (await asyncio.wait_for(processing, 3)).status == "cancelled"
    assert asdict(await store.require_goal(goal.goal_id)) == before
    assert (await store.require_goal(goal.goal_id)).steps[-1].task_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"result": "late"}, {"error": "late"}, {"clear_result": True}, {"clear_error": True}])
async def test_terminal_payload_cannot_change_without_status(tmp_path, patch):
    store = GoalStore(tmp_path)
    goal = await create(store)
    before = asdict(await store.cancel_goal(goal.goal_id))
    with pytest.raises(GoalConflictError):
        await store.update_goal(goal.goal_id, **patch)
    assert asdict(await store.require_goal(goal.goal_id)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
async def test_terminal_registry_rebinding_does_not_reopen_goal(tmp_path, terminal):
    store = GoalStore(tmp_path)
    goal = await create(store)
    before = await store.update_goal(goal.goal_id, status=terminal, result="original", run_id="old-run")
    store = GoalStore(tmp_path)
    worker = coordinator(store, None)
    run = await worker._ensure_goal_run(await store.require_goal(goal.goal_id))
    current = await store.require_goal(goal.goal_id)
    assert current.status == run.status == terminal
    assert current.result == before.result
    assert [asdict(step) for step in current.steps] == [asdict(step) for step in before.steps]
    assert current.run_id == run.run_id != "old-run"


@pytest.mark.asyncio
async def test_idempotent_terminal_update_does_not_touch_timestamps(tmp_path):
    store = GoalStore(tmp_path)
    goal = await create(store)
    terminal = await store.cancel_goal(goal.goal_id)
    same = await store.update_goal(goal.goal_id, status="cancelled")
    assert asdict(same) == asdict(terminal)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "start", "pause", "resume", "retry", "reassign", "skip"])
async def test_management_returns_current_snapshot_after_first_run_binding(tmp_path, action):
    store = GoalStore(tmp_path)
    goal = await create(store)
    worker = coordinator(store, None)
    if action == "pause":
        await store.update_goal(goal.goal_id, status="running")
    elif action in {"resume", "skip"}:
        await store.update_goal(goal.goal_id, status="paused")
    elif action == "retry":
        await store.update_goal(goal.goal_id, status="needs_attention")
        await store.update_step(goal.goal_id, "first", status="failed")
    if action in {"cancel", "start", "pause", "resume"}:
        result = await getattr(worker, f"{action}_goal")(goal.goal_id)
    elif action == "retry":
        result = await worker.retry_step(goal.goal_id, "first")
    elif action == "reassign":
        result = await worker.reassign_step(goal.goal_id, "first", target_xpert_id="replacement")
    else:
        result = await worker.skip_step(goal.goal_id, "first")
    current = await store.require_goal(goal.goal_id)
    assert current.run_id is not None
    assert asdict(result) == asdict(current)
