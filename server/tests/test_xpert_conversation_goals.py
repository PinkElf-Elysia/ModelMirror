from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from server.xpert_runtime.agent_tasks import AgentTaskStore
from server.xpert_runtime.goal_coordinator import GoalCoordinator, GoalPlan, PinnedXpert
from server.xpert_runtime.goals import (
    GoalConflictError,
    GoalStep,
    GoalStore,
    GoalValidationError,
    validate_goal_plan,
)
from server.xpert_runtime.run_registry import RunRegistry


def sample_steps() -> list[GoalStep]:
    return [
        GoalStep(
            step_id="research",
            title="Research",
            instruction="Collect evidence.",
            target_xpert_id="researcher",
        ),
        GoalStep(
            step_id="review",
            title="Review",
            instruction="Review constraints.",
            target_xpert_id="reviewer",
        ),
        GoalStep(
            step_id="deliver",
            title="Deliver",
            instruction="Synthesize a final answer.",
            target_xpert_id="writer",
            depends_on=["research", "review"],
        ),
    ]


async def create_planned_goal(store: GoalStore):
    goal = await store.create_goal(
        title="Durable goal",
        objective="Produce a researched delivery.",
        planner_xpert_id="planner",
        planner_version=1,
        max_parallel=2,
    )
    return await store.replace_plan(
        goal.goal_id,
        steps=sample_steps(),
        final_step_id="deliver",
        summary="Parallel research then synthesis.",
    )


def test_validate_goal_plan_rejects_cycles_and_unreachable_final() -> None:
    with pytest.raises(GoalValidationError, match="acyclic"):
        validate_goal_plan(
            [
                GoalStep("a", "A", "A", "x", depends_on=["b"]),
                GoalStep("b", "B", "B", "x", depends_on=["a"]),
            ],
            "b",
        )

    with pytest.raises(GoalValidationError, match="final step must depend"):
        validate_goal_plan(
            [
                GoalStep("a", "A", "A", "x"),
                GoalStep("b", "B", "B", "x"),
            ],
            "b",
        )


@pytest.mark.asyncio
async def test_goal_store_persists_plan_and_rejects_stale_revision(
    tmp_path: Path,
) -> None:
    store = GoalStore(tmp_path)
    goal = await create_planned_goal(store)
    assert goal.plan_revision == 1

    with pytest.raises(GoalConflictError, match="revision changed"):
        await store.replace_plan(
            goal.goal_id,
            steps=sample_steps(),
            final_step_id="deliver",
            expected_revision=0,
        )

    await store.update_goal(goal.goal_id, status="paused")
    reloaded = GoalStore(tmp_path)
    restored = await reloaded.require_goal(goal.goal_id)
    assert restored.status == "paused"
    assert restored.plan_revision == 1
    assert restored.final_step_id == "deliver"
    assert [step.step_id for step in restored.steps] == [
        "research",
        "review",
        "deliver",
    ]


def make_coordinator(
    goal_store: GoalStore,
    task_store: AgentTaskStore,
    run_registry: RunRegistry,
) -> GoalCoordinator:
    async def planner(goal, parent_run_id: str) -> GoalPlan:
        assert parent_run_id
        return GoalPlan(
            summary="Parallel research then synthesis.",
            final_step_id="deliver",
            steps=sample_steps(),
        )

    async def resolver(reference: str) -> PinnedXpert:
        return PinnedXpert(
            xpert_id=f"id-{reference}",
            slug=reference,
            version=3,
            name=reference.title(),
        )

    return GoalCoordinator(
        goal_store,
        task_store,
        run_registry,
        planner,
        resolver,
        enabled=True,
        poll_interval=0.01,
    )


@pytest.mark.asyncio
async def test_coordinator_plans_dispatches_dependencies_and_completes() -> None:
    goals = GoalStore()
    tasks = AgentTaskStore()
    runs = RunRegistry()
    coordinator = make_coordinator(goals, tasks, runs)
    goal = await goals.create_goal(
        title="Execution goal",
        objective="Research and write.",
        planner_xpert_id="planner",
        planner_version=1,
        max_parallel=2,
    )

    planned = await coordinator.plan_goal(goal.goal_id)
    assert planned.status == "awaiting_review"
    started = await coordinator.start_goal(goal.goal_id)
    assert all(step.target_version == 3 for step in started.steps)

    await coordinator.process_goal(goal.goal_id)
    running = await goals.require_goal(goal.goal_id)
    assert {step.step_id for step in running.steps if step.status == "running"} == {
        "research",
        "review",
    }
    assert next(step for step in running.steps if step.step_id == "deliver").status == "pending"

    for step in running.steps[:2]:
        assert step.handoff_id and step.task_id
        await tasks.update_task(step.task_id, status="completed", result=f"{step.step_id} result")
        await tasks.update_handoff_status(step.handoff_id, "accepted")
        await tasks.update_handoff_status(
            step.handoff_id,
            "completed",
            metadata={"result": f"{step.step_id} result", "xpert_run_id": f"run-{step.step_id}"},
        )

    await coordinator.process_goal(goal.goal_id)
    after_dependencies = await goals.require_goal(goal.goal_id)
    final = next(step for step in after_dependencies.steps if step.step_id == "deliver")
    assert final.status == "running"
    assert final.handoff_id and final.task_id
    final_task = await tasks.get_task(final.task_id)
    assert final_task is not None
    assert "research result" in final_task.input
    assert "review result" in final_task.input

    await tasks.update_task(final.task_id, status="completed", result="final delivery")
    await tasks.update_handoff_status(final.handoff_id, "accepted")
    await tasks.update_handoff_status(
        final.handoff_id,
        "completed",
        metadata={"result": "final delivery", "xpert_run_id": "run-final"},
    )
    await coordinator.process_goal(goal.goal_id)
    completed = await goals.require_goal(goal.goal_id)
    assert completed.status == "completed"
    assert completed.result == "final delivery"
    assert completed.run_id


@pytest.mark.asyncio
async def test_pause_reconciles_running_steps_without_dispatching_new_steps() -> None:
    goals = GoalStore()
    tasks = AgentTaskStore()
    runs = RunRegistry()
    coordinator = make_coordinator(goals, tasks, runs)
    goal = await goals.create_goal(
        title="Pause goal",
        objective="Pause safely.",
        planner_xpert_id="planner",
        planner_version=1,
    )
    await coordinator.plan_goal(goal.goal_id)
    await coordinator.start_goal(goal.goal_id)
    await coordinator.process_goal(goal.goal_id)
    await coordinator.pause_goal(goal.goal_id)
    paused = await goals.require_goal(goal.goal_id)

    for step in paused.steps[:2]:
        assert step.task_id and step.handoff_id
        await tasks.update_task(step.task_id, status="completed", result=step.step_id)
        await tasks.update_handoff_status(step.handoff_id, "accepted")
        await tasks.update_handoff_status(step.handoff_id, "completed", metadata={"result": step.step_id})

    await coordinator.process_goal(goal.goal_id)
    still_paused = await goals.require_goal(goal.goal_id)
    assert still_paused.status == "paused"
    assert next(step for step in still_paused.steps if step.step_id == "deliver").status == "pending"

    await coordinator.resume_goal(goal.goal_id)
    await coordinator.process_goal(goal.goal_id)
    resumed = await goals.require_goal(goal.goal_id)
    assert next(step for step in resumed.steps if step.step_id == "deliver").status == "running"


@pytest.mark.asyncio
async def test_dead_letter_requires_attention_then_retry_resumes() -> None:
    goals = GoalStore()
    tasks = AgentTaskStore()
    runs = RunRegistry()
    coordinator = make_coordinator(goals, tasks, runs)
    goal = await goals.create_goal(
        title="Recovery goal",
        objective="Recover a failed step.",
        planner_xpert_id="planner",
        planner_version=1,
    )
    await coordinator.plan_goal(goal.goal_id)
    await coordinator.start_goal(goal.goal_id)
    await coordinator.process_goal(goal.goal_id)
    running = await goals.require_goal(goal.goal_id)
    failed = next(step for step in running.steps if step.step_id == "research")
    assert failed.handoff_id and failed.task_id
    await tasks.update_task(failed.task_id, status="failed", error="model unavailable")
    await tasks.update_handoff_status(failed.handoff_id, "accepted")
    await tasks.update_handoff_status(
        failed.handoff_id,
        "dead_letter",
        metadata={"last_error": "model unavailable", "attempts": 3},
    )

    await coordinator.process_goal(goal.goal_id)
    attention = await goals.require_goal(goal.goal_id)
    assert attention.status == "needs_attention"
    assert next(step for step in attention.steps if step.step_id == "research").status == "failed"
    assert next(step for step in attention.steps if step.step_id == "deliver").status == "blocked"

    await coordinator.retry_step(goal.goal_id, "research")
    retried = await goals.require_goal(goal.goal_id)
    assert retried.status == "running"
    assert next(step for step in retried.steps if step.step_id == "research").status == "pending"
    await coordinator.process_goal(goal.goal_id)
    dispatched = await goals.require_goal(goal.goal_id)
    assert next(step for step in dispatched.steps if step.step_id == "research").status == "running"


@pytest.mark.asyncio
async def test_goal_api_create_review_and_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server.main as main_module

    goals = GoalStore(tmp_path / "goals-api")
    tasks = AgentTaskStore()
    runs = RunRegistry()

    async def planner(goal, parent_run_id: str) -> GoalPlan:
        assert parent_run_id
        return GoalPlan(
            summary="Reviewed plan.",
            final_step_id="deliver",
            steps=sample_steps(),
        )

    async def resolver(reference: str) -> PinnedXpert:
        xpert_id = reference if reference.startswith("xpert-") else f"xpert-{reference}"
        return PinnedXpert(
            xpert_id=xpert_id,
            slug=xpert_id,
            version=2,
            name=xpert_id,
        )

    coordinator = GoalCoordinator(
        goals,
        tasks,
        runs,
        planner,
        resolver,
        enabled=True,
        poll_interval=0.01,
    )
    monkeypatch.setattr(main_module, "goal_store", goals)
    monkeypatch.setattr(main_module, "goal_coordinator", coordinator)
    monkeypatch.setattr(main_module, "resolve_published_xpert", resolver)

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created_response = await client.post(
            "/api/runtime/goals",
            json={
                "title": "API goal",
                "objective": "Plan and execute safely.",
                "planner_xpert_id": "planner",
                "messages": [{"role": "user", "content": "context"}],
                "max_parallel": 2,
            },
        )
        assert created_response.status_code == 200, created_response.text
        created = created_response.json()
        assert created["status"] == "planning"
        assert created["planner_xpert_id"] == "xpert-planner"

        await coordinator.plan_goal(created["goal_id"])
        detail_response = await client.get(
            f"/api/runtime/goals/{created['goal_id']}"
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["status"] == "awaiting_review"
        assert detail["plan_revision"] == 1

        missing_revision = await client.patch(
            f"/api/runtime/goals/{created['goal_id']}/plan",
            json={
                "summary": detail["plan_summary"],
                "final_step_id": detail["final_step_id"],
                "steps": detail["steps"],
            },
        )
        assert missing_revision.status_code == 400

        saved_response = await client.patch(
            f"/api/runtime/goals/{created['goal_id']}/plan",
            json={
                "plan_revision": detail["plan_revision"],
                "summary": "Human reviewed.",
                "final_step_id": detail["final_step_id"],
                "steps": detail["steps"],
            },
        )
        assert saved_response.status_code == 200, saved_response.text
        assert saved_response.json()["plan_revision"] == 2

        started = await client.post(
            f"/api/runtime/goals/{created['goal_id']}/start"
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "running"

        paused = await client.post(
            f"/api/runtime/goals/{created['goal_id']}/pause"
        )
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"

        resumed = await client.post(
            f"/api/runtime/goals/{created['goal_id']}/resume"
        )
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "running"

        cancelled = await client.post(
            f"/api/runtime/goals/{created['goal_id']}/cancel"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        list_response = await client.get(
            "/api/runtime/goals",
            params={"status": "cancelled", "search": "API"},
        )
        assert list_response.status_code == 200
        assert list_response.json()["total"] == 1
