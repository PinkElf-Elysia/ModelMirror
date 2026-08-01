from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .agent_tasks import AgentTaskStore
from .goals import (
    ConversationGoal,
    GoalConflictError,
    GoalNotFoundError,
    GoalStep,
    GoalStore,
    GoalValidationError,
    validate_goal_plan,
)
from .run_registry import RunRegistry, RuntimeRun


logger = logging.getLogger("modelmirror.goal_coordinator")


@dataclass(slots=True)
class GoalPlan:
    summary: str
    final_step_id: str
    steps: list[GoalStep]


@dataclass(slots=True)
class PinnedXpert:
    xpert_id: str
    slug: str
    version: int
    name: str


PlanGoal = Callable[[ConversationGoal, str], Awaitable[GoalPlan]]
ResolveXpert = Callable[[str], Awaitable[PinnedXpert]]
RenderSharedFileContext = Callable[[ConversationGoal], Awaitable[str]]


class GoalCoordinator:
    """Plans and dispatches durable conversation goals through Xpert handoffs."""

    def __init__(
        self,
        goal_store: GoalStore,
        task_store: AgentTaskStore,
        run_registry: RunRegistry,
        plan_goal: PlanGoal,
        resolve_xpert: ResolveXpert,
        render_shared_file_context: RenderSharedFileContext | None = None,
        *,
        enabled: bool = True,
        poll_interval: float = 1.0,
    ) -> None:
        self.goal_store = goal_store
        self.task_store = task_store
        self.run_registry = run_registry
        self.plan_goal_callback = plan_goal
        self.resolve_xpert_callback = resolve_xpert
        self.render_shared_file_context_callback = render_shared_file_context
        self.enabled = enabled
        self.poll_interval = max(0.1, poll_interval)
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._planning: set[str] = set()
        self._processing: set[str] = set()

    def start(self) -> None:
        if not self.enabled or (self._loop_task and not self._loop_task.done()):
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(self._run_loop(), name="goal-coordinator")

    async def stop(self) -> None:
        self._stopping.set()
        task = self._loop_task
        self._loop_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def run_once(self) -> int:
        if not self.enabled:
            return 0
        processed = 0
        goals = await self.goal_store.list_goals(limit=500)
        for goal in goals:
            if goal.status == "planning" and goal.goal_id not in self._planning:
                await self.plan_goal(goal.goal_id)
                processed += 1
            elif goal.status in {"running", "paused", "needs_attention"}:
                if goal.goal_id in self._processing:
                    continue
                await self.process_goal(goal.goal_id)
                processed += 1
        return processed

    async def plan_goal(self, goal_id: str) -> ConversationGoal:
        if goal_id in self._planning:
            raise GoalConflictError("Goal planning is already running.")
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status not in {"planning", "needs_attention"}:
            raise GoalConflictError("Only planning or needs_attention goals can be planned.")
        self._planning.add(goal_id)
        try:
            run = await self._ensure_goal_run(goal)
            await self._checkpoint(
                run,
                "goal.planning.started",
                "Goal planning started",
                f"planner_version={goal.planner_version}",
                {"goal_id": goal.goal_id, "planner_xpert_id": goal.planner_xpert_id},
            )
            await self.goal_store.update_goal(goal_id, status="planning", clear_error=True)
            try:
                plan = await self.plan_goal_callback(goal, run.run_id)
                normalized = validate_goal_plan(plan.steps, plan.final_step_id)
                for step in normalized:
                    await self.resolve_xpert_callback(step.target_xpert_id)
                updated = await self.goal_store.replace_plan(
                    goal_id,
                    steps=normalized,
                    final_step_id=plan.final_step_id,
                    summary=plan.summary,
                    status="awaiting_review",
                )
                await self._checkpoint(
                    run,
                    "goal.planning.completed",
                    "Goal plan ready for review",
                    f"steps={len(normalized)}",
                    {
                        "goal_id": goal_id,
                        "step_count": len(normalized),
                        "plan_revision": updated.plan_revision,
                    },
                )
                await self.run_registry.update_run(
                    run.run_id,
                    status="pending",
                    metadata={
                        "goal_status": "awaiting_review",
                        "plan_revision": updated.plan_revision,
                    },
                    clear_error=True,
                )
                return updated
            except Exception as exc:
                error = str(exc or "Goal planning failed.")[:2000]
                updated = await self.goal_store.update_goal(
                    goal_id,
                    status="needs_attention",
                    error=error,
                )
                await self._checkpoint(
                    run,
                    "goal.planning.failed",
                    "Goal planning needs attention",
                    error[:500],
                    {"goal_id": goal_id},
                    severity="error",
                )
                await self.run_registry.update_run(
                    run.run_id,
                    status="failed",
                    error=error,
                    metadata={"goal_status": "needs_attention"},
                )
                return updated
        finally:
            self._planning.discard(goal_id)

    async def start_goal(self, goal_id: str) -> ConversationGoal:
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status != "awaiting_review":
            raise GoalConflictError("Only an awaiting_review goal can be started.")
        normalized = validate_goal_plan(goal.steps, goal.final_step_id)
        pinned: dict[str, PinnedXpert] = {}
        for step in normalized:
            resolved = await self.resolve_xpert_callback(step.target_xpert_id)
            pinned[step.step_id] = resolved

        def apply_start(current: ConversationGoal) -> None:
            if current.status != "awaiting_review":
                raise GoalConflictError("Goal state changed before start.")
            for step in current.steps:
                resolved = pinned[step.step_id]
                step.target_xpert_id = resolved.xpert_id
                step.target_version = resolved.version
                step.status = "pending"
                step.task_id = None
                step.handoff_id = None
                step.xpert_run_id = None
                step.result = None
                step.error = None
                step.attempts = 0
                step.touch()
            current.status = "running"
            current.result = None
            current.error = None

        updated = await self.goal_store.mutate_steps(goal_id, apply_start)
        run = await self._ensure_goal_run(updated)
        await self.run_registry.update_run(run.run_id, status="running", clear_error=True)
        await self._checkpoint(
            run,
            "goal.started",
            "Goal execution started",
            f"steps={len(updated.steps)} max_parallel={updated.max_parallel}",
            {"goal_id": goal_id, "plan_revision": updated.plan_revision},
        )
        return updated

    async def pause_goal(self, goal_id: str) -> ConversationGoal:
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status != "running":
            raise GoalConflictError("Only a running goal can be paused.")
        updated = await self.goal_store.update_goal(goal_id, status="paused")
        run = await self._ensure_goal_run(updated)
        await self.run_registry.update_run(
            run.run_id,
            status="pending",
            metadata={"goal_status": "paused"},
        )
        await self._checkpoint(
            run,
            "goal.paused",
            "Goal paused",
            "No new steps will be dispatched.",
            {"goal_id": goal_id},
        )
        return updated

    async def resume_goal(self, goal_id: str) -> ConversationGoal:
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status not in {"paused", "needs_attention"}:
            raise GoalConflictError("Only paused or needs_attention goals can be resumed.")
        if any(step.status == "failed" for step in goal.steps):
            raise GoalConflictError("Resolve failed steps before resuming the goal.")
        updated = await self.goal_store.update_goal(
            goal_id,
            status="running",
            clear_error=True,
        )
        run = await self._ensure_goal_run(updated)
        await self.run_registry.update_run(run.run_id, status="running", clear_error=True)
        await self._checkpoint(
            run,
            "goal.resumed",
            "Goal resumed",
            "Dependency scheduling resumed.",
            {"goal_id": goal_id},
        )
        return updated

    async def cancel_goal(self, goal_id: str) -> ConversationGoal:
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status in {"completed", "cancelled"}:
            return goal

        def apply_cancel(current: ConversationGoal) -> None:
            current.status = "cancelled"
            current.error = "Cancelled by user."
            for step in current.steps:
                if step.status not in {"completed", "skipped"}:
                    step.status = "cancelled"
                    step.touch()

        updated = await self.goal_store.mutate_steps(goal_id, apply_cancel)
        run = await self._ensure_goal_run(updated)
        await self.run_registry.update_run(
            run.run_id,
            status="cancelled",
            error="Cancelled by user.",
        )
        await self._checkpoint(
            run,
            "goal.cancelled",
            "Goal cancelled",
            "Running Xpert requests are not force-terminated.",
            {"goal_id": goal_id},
            severity="warning",
        )
        return updated

    async def retry_step(self, goal_id: str, step_id: str) -> ConversationGoal:
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status not in {"needs_attention", "paused"}:
            raise GoalConflictError("Step retry requires a paused or needs_attention goal.")
        step = self._step(goal, step_id)
        if step.status not in {"failed", "blocked", "cancelled"}:
            raise GoalConflictError("Only a failed or blocked step can be retried.")
        resolved = await self.resolve_xpert_callback(step.target_xpert_id)

        def apply_retry(current: ConversationGoal) -> None:
            selected = self._step(current, step_id)
            selected.target_xpert_id = resolved.xpert_id
            selected.target_version = resolved.version
            selected.status = "pending"
            selected.task_id = None
            selected.handoff_id = None
            selected.xpert_run_id = None
            selected.result = None
            selected.error = None
            selected.attempts = 0
            selected.touch()
            by_id = {item.step_id: item for item in current.steps}
            for item in current.steps:
                blockers = [by_id[dependency] for dependency in item.depends_on]
                if item.status == "blocked" and not any(
                    dependency.status in {"failed", "blocked", "cancelled"}
                    for dependency in blockers
                ):
                    item.status = "pending"
                    item.error = None
                    item.touch()
            current.status = "running"
            current.error = None

        updated = await self.goal_store.mutate_steps(goal_id, apply_retry)
        run = await self._ensure_goal_run(updated)
        await self.run_registry.update_run(
            run.run_id,
            status="running",
            metadata={"goal_status": "running"},
            clear_error=True,
        )
        await self._checkpoint(
            run,
            "goal.step.retried",
            "Goal step queued for retry",
            step_id,
            {"goal_id": goal_id, "step_id": step_id, "target_xpert_id": resolved.xpert_id},
        )
        return updated

    async def reassign_step(
        self,
        goal_id: str,
        step_id: str,
        *,
        target_xpert_id: str,
        instruction: str | None = None,
    ) -> ConversationGoal:
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status not in {"awaiting_review", "paused", "needs_attention"}:
            raise GoalConflictError("Goal steps cannot be edited in the current state.")
        step = self._step(goal, step_id)
        if step.status in {"running", "completed"}:
            raise GoalConflictError("Running or completed steps cannot be reassigned.")
        resolved = await self.resolve_xpert_callback(target_xpert_id)

        def apply_reassign(current: ConversationGoal) -> None:
            selected = self._step(current, step_id)
            selected.target_xpert_id = resolved.xpert_id
            selected.target_version = (
                None if current.status == "awaiting_review" else resolved.version
            )
            if instruction is not None:
                selected.instruction = instruction.strip()
            selected.status = "pending"
            selected.task_id = None
            selected.handoff_id = None
            selected.result = None
            selected.error = None
            selected.touch()
            if current.status == "needs_attention":
                current.status = "paused"
                current.error = None
            current.plan_revision += 1

        updated = await self.goal_store.mutate_steps(goal_id, apply_reassign)
        run = await self._ensure_goal_run(updated)
        await self._checkpoint(
            run,
            "goal.step.reassigned",
            "Goal step reassigned",
            step_id,
            {"goal_id": goal_id, "step_id": step_id, "target_xpert_id": resolved.xpert_id},
        )
        return updated

    async def skip_step(self, goal_id: str, step_id: str) -> ConversationGoal:
        goal = await self.goal_store.require_goal(goal_id)
        if goal.status not in {"needs_attention", "paused"}:
            raise GoalConflictError("Step skip requires a paused or needs_attention goal.")
        step = self._step(goal, step_id)
        if step.step_id == goal.final_step_id:
            raise GoalConflictError("The final goal step cannot be skipped.")
        if step.status not in {"failed", "blocked", "pending"}:
            raise GoalConflictError("Only pending, blocked, or failed steps can be skipped.")

        def apply_skip(current: ConversationGoal) -> None:
            selected = self._step(current, step_id)
            selected.status = "skipped"
            selected.error = None
            selected.touch()
            by_id = {item.step_id: item for item in current.steps}
            for item in current.steps:
                blockers = [by_id[dependency] for dependency in item.depends_on]
                if item.status == "blocked" and not any(
                    dependency.status in {"failed", "blocked", "cancelled"}
                    for dependency in blockers
                ):
                    item.status = "pending"
                    item.error = None
                    item.touch()
            if current.status == "needs_attention":
                current.status = "running"
                current.error = None

        updated = await self.goal_store.mutate_steps(goal_id, apply_skip)
        run = await self._ensure_goal_run(updated)
        if updated.status == "running":
            await self.run_registry.update_run(
                run.run_id,
                status="running",
                metadata={"goal_status": "running"},
                clear_error=True,
            )
        await self._checkpoint(
            run,
            "goal.step.skipped",
            "Goal step skipped",
            step_id,
            {"goal_id": goal_id, "step_id": step_id},
            severity="warning",
        )
        return updated

    async def process_goal(self, goal_id: str) -> ConversationGoal:
        if goal_id in self._processing:
            return await self.goal_store.require_goal(goal_id)
        self._processing.add(goal_id)
        try:
            goal = await self.goal_store.require_goal(goal_id)
            if goal.status not in {"running", "paused", "needs_attention"}:
                return goal
            run = await self._ensure_goal_run(goal)
            await self._reconcile_steps(goal, run)
            goal = await self.goal_store.require_goal(goal_id)
            final = self._step(goal, goal.final_step_id or "")
            if final.status == "completed":
                completed = await self.goal_store.update_goal(
                    goal_id,
                    status="completed",
                    result=final.result or "",
                    clear_error=True,
                )
                await self.run_registry.update_run(
                    run.run_id,
                    status="completed",
                    metadata={"result_length": len(completed.result or "")},
                )
                await self._checkpoint(
                    run,
                    "goal.completed",
                    "Goal completed",
                    f"result_length={len(completed.result or '')}",
                    {"goal_id": goal_id, "final_step_id": final.step_id},
                )
                return completed
            if goal.status != "running":
                return goal
            await self._dispatch_ready_steps(goal, run)
            return await self.goal_store.require_goal(goal_id)
        finally:
            self._processing.discard(goal_id)

    async def status(self) -> dict[str, Any]:
        goals = await self.goal_store.list_goals(limit=10_000)
        return {
            "enabled": self.enabled,
            "running": bool(self._loop_task and not self._loop_task.done()),
            "poll_interval": self.poll_interval,
            "planning": len(self._planning),
            "processing": len(self._processing),
            "goal_count": len(goals),
            "running_goal_count": sum(1 for goal in goals if goal.status == "running"),
            "needs_attention_count": sum(
                1 for goal in goals if goal.status == "needs_attention"
            ),
        }

    async def _reconcile_steps(self, goal: ConversationGoal, run: RuntimeRun) -> None:
        failed_step: GoalStep | None = None
        for step in goal.steps:
            if step.status not in {
                "running",
                "waiting_approval",
                "waiting_client",
            } or not step.handoff_id:
                continue
            handoff = await self.task_store.get_handoff(step.handoff_id)
            if handoff is None:
                continue
            task = await self.task_store.get_task(step.task_id or "")
            if handoff.status == "completed":
                result = str(
                    (task.result if task is not None else None)
                    or handoff.metadata.get("result")
                    or ""
                )[:100_000]
                await self.goal_store.update_step(
                    goal.goal_id,
                    step.step_id,
                    status="completed",
                    result=result,
                    error=None,
                    attempts=int(handoff.metadata.get("attempts") or step.attempts),
                    xpert_run_id=handoff.metadata.get("xpert_run_id"),
                )
                await self._checkpoint(
                    run,
                    "goal.step.completed",
                    "Goal step completed",
                    f"{step.step_id} result_length={len(result)}",
                    {"goal_id": goal.goal_id, "step_id": step.step_id},
                )
            elif handoff.status == "waiting_approval":
                if step.status != "waiting_approval":
                    await self.goal_store.update_step(
                        goal.goal_id,
                        step.step_id,
                        status="waiting_approval",
                        attempts=int(
                            handoff.metadata.get("attempts") or step.attempts
                        ),
                    )
                    await self._checkpoint(
                        run,
                        "goal.step.waiting_approval",
                        "Goal step is waiting for approval",
                        str(handoff.metadata.get("approval_id") or ""),
                        {
                            "goal_id": goal.goal_id,
                            "step_id": step.step_id,
                            "approval_id": handoff.metadata.get("approval_id"),
                        },
                        severity="warning",
                    )
            elif handoff.status == "waiting_client":
                if step.status != "waiting_client":
                    await self.goal_store.update_step(
                        goal.goal_id,
                        step.step_id,
                        status="waiting_client",
                        attempts=int(
                            handoff.metadata.get("attempts") or step.attempts
                        ),
                    )
                    await self._checkpoint(
                        run,
                        "goal.step.waiting_client",
                        "Goal step is waiting for a client host",
                        str(handoff.metadata.get("client_request_id") or ""),
                        {
                            "goal_id": goal.goal_id,
                            "step_id": step.step_id,
                            "client_request_id": handoff.metadata.get(
                                "client_request_id"
                            ),
                        },
                        severity="warning",
                    )
            elif handoff.status == "needs_attention":
                waiting_for_client = bool(
                    handoff.metadata.get("client_request_id")
                )
                await self.goal_store.update_step(
                    goal.goal_id,
                    step.step_id,
                    status=(
                        "waiting_client"
                        if waiting_for_client
                        else "waiting_approval"
                    ),
                    error=str(
                        handoff.metadata.get("last_error")
                        or (
                            "Client tool request requires attention."
                            if waiting_for_client
                            else "Runtime approval requires attention."
                        )
                    )[:2000],
                )

                def apply_approval_attention(current: ConversationGoal) -> None:
                    current.status = "needs_attention"
                    current.error = (
                        f"Step {step.step_id} has an expired "
                        f"{'client tool request' if waiting_for_client else 'runtime approval'}."
                    )

                await self.goal_store.mutate_steps(
                    goal.goal_id,
                    apply_approval_attention,
                )
                await self._checkpoint(
                    run,
                    "goal.needs_attention",
                    "Goal approval needs attention",
                    step.step_id,
                    {
                        "goal_id": goal.goal_id,
                        "step_id": step.step_id,
                        "approval_id": handoff.metadata.get("approval_id"),
                        "client_request_id": handoff.metadata.get(
                            "client_request_id"
                        ),
                    },
                    severity="warning",
                )
            elif handoff.status in {"dead_letter", "rejected"}:
                error = str(
                    handoff.metadata.get("last_error")
                    or handoff.metadata.get("reason")
                    or (task.error if task is not None else None)
                    or "Goal step failed."
                )[:2000]
                await self.goal_store.update_step(
                    goal.goal_id,
                    step.step_id,
                    status="failed",
                    error=error,
                    attempts=int(handoff.metadata.get("attempts") or step.attempts),
                )
                failed_step = step
                await self._checkpoint(
                    run,
                    "goal.step.failed",
                    "Goal step needs attention",
                    f"{step.step_id}: {error[:300]}",
                    {"goal_id": goal.goal_id, "step_id": step.step_id},
                    severity="error",
                )
        if failed_step is not None:
            def apply_attention(current: ConversationGoal) -> None:
                current.status = "needs_attention"
                current.error = f"Step {failed_step.step_id} requires attention."
                failed_ids = {step.step_id for step in current.steps if step.status == "failed"}
                changed = True
                while changed:
                    changed = False
                    for item in current.steps:
                        if item.status == "pending" and any(
                            dependency in failed_ids for dependency in item.depends_on
                        ):
                            item.status = "blocked"
                            item.error = "A dependency requires attention."
                            failed_ids.add(item.step_id)
                            item.touch()
                            changed = True

            await self.goal_store.mutate_steps(goal.goal_id, apply_attention)
            await self.run_registry.update_run(
                run.run_id,
                status="failed",
                error=f"Step {failed_step.step_id} requires attention.",
            )
            await self._checkpoint(
                run,
                "goal.needs_attention",
                "Goal needs attention",
                failed_step.step_id,
                {"goal_id": goal.goal_id, "step_id": failed_step.step_id},
                severity="error",
            )

    async def _dispatch_ready_steps(self, goal: ConversationGoal, run: RuntimeRun) -> None:
        active = sum(
            1
            for step in goal.steps
            if step.status in {"running", "waiting_approval", "waiting_client"}
        )
        slots = max(0, goal.max_parallel - active)
        if slots == 0:
            return
        successful = {step.step_id for step in goal.steps if step.status in {"completed", "skipped"}}
        ready = [
            step
            for step in goal.steps
            if step.status == "pending" and all(dep in successful for dep in step.depends_on)
        ]
        for step in ready[:slots]:
            await self._dispatch_step(goal, step, run)

    async def _dispatch_step(
        self,
        goal: ConversationGoal,
        step: GoalStep,
        run: RuntimeRun,
    ) -> None:
        if step.target_version is None:
            resolved = await self.resolve_xpert_callback(step.target_xpert_id)
            await self.goal_store.update_step(
                goal.goal_id,
                step.step_id,
                target_xpert_id=resolved.xpert_id,
                target_version=resolved.version,
            )
            step = self._step(await self.goal_store.require_goal(goal.goal_id), step.step_id)
        task_input = self._render_step_input(goal, step)
        if self.render_shared_file_context_callback and goal.file_asset_ids:
            shared_context = await self.render_shared_file_context_callback(goal)
            if shared_context:
                marker = "\n\nExplicitly shared goal files:\n"
                available = max(0, 20_000 - len(task_input) - len(marker))
                task_input = f"{task_input}{marker}{shared_context[:available]}"
        task = await self.task_store.create_task(
            title=f"{goal.title}: {step.title}"[:200],
            input_text=task_input,
            source_agent=f"goal:{goal.goal_id}",
            assigned_agent=f"xpert:{step.target_xpert_id}",
            metadata={
                "goal_id": goal.goal_id,
                "goal_step_id": step.step_id,
                "parent_run_id": run.run_id,
                "target_xpert_id": step.target_xpert_id,
                "target_xpert_version": step.target_version,
                "source_xpert_id": goal.source_xpert_id,
                "source_conversation_id": goal.source_conversation_id,
                "file_asset_ids": list(goal.file_asset_ids),
            },
        )
        task_run = await self.run_registry.create_run(
            "agent_task",
            task.title,
            status="pending",
            source_id=task.task_id,
            parent_run_id=run.run_id,
            metadata={
                "goal_id": goal.goal_id,
                "goal_step_id": step.step_id,
                "agent_task_id": task.task_id,
            },
        )
        handoff = await self.task_store.create_handoff(
            task.task_id,
            source_agent=f"goal:{goal.goal_id}",
            target_agent=f"xpert:{step.target_xpert_id}",
            reason=f"Execute goal step {step.step_id}: {step.title}",
            metadata={
                "execution_mode": "xpert_auto",
                "ready_for_execution": False,
                "goal_id": goal.goal_id,
                "goal_step_id": step.step_id,
                "target_xpert_id": step.target_xpert_id,
                "target_xpert_version": step.target_version,
                "handoff_depth": 0,
                "source_xpert_id": goal.source_xpert_id,
                "source_conversation_id": goal.source_conversation_id,
                "file_asset_ids": list(goal.file_asset_ids),
            },
        )
        handoff_run = await self.run_registry.create_run(
            "agent_handoff",
            f"Goal step {step.step_id}",
            status="pending",
            source_id=handoff.handoff_id,
            parent_run_id=task_run.run_id,
            metadata={
                "goal_id": goal.goal_id,
                "goal_step_id": step.step_id,
                "agent_task_id": task.task_id,
                "handoff_id": handoff.handoff_id,
                "target_xpert_id": step.target_xpert_id,
                "target_xpert_version": step.target_version,
            },
        )
        await self.run_registry.record_checkpoint(
            handoff_run.run_id,
            event_type="agent_handoff.created",
            title="Goal step handoff created",
            summary=step.step_id,
            metadata={"goal_id": goal.goal_id, "goal_step_id": step.step_id},
        )
        await self.task_store.update_handoff_metadata(
            handoff.handoff_id,
            {"ready_for_execution": True},
        )
        await self.goal_store.update_step(
            goal.goal_id,
            step.step_id,
            status="running",
            task_id=task.task_id,
            handoff_id=handoff.handoff_id,
            error=None,
        )
        await self._checkpoint(
            run,
            "goal.step.dispatched",
            "Goal step dispatched",
            step.step_id,
            {
                "goal_id": goal.goal_id,
                "step_id": step.step_id,
                "agent_task_id": task.task_id,
                "handoff_id": handoff.handoff_id,
                "target_xpert_id": step.target_xpert_id,
                "target_xpert_version": step.target_version,
            },
        )

    def _render_step_input(self, goal: ConversationGoal, step: GoalStep) -> str:
        sections = [
            f"Goal: {goal.objective}",
            f"Step: {step.title}\n{step.instruction}",
        ]
        by_id = {item.step_id: item for item in goal.steps}
        if step.depends_on:
            sections.append("Dependency results:")
            for dependency_id in step.depends_on:
                dependency = by_id[dependency_id]
                sections.append(
                    f"[{dependency.step_id} - {dependency.title}]\n"
                    f"{dependency.result or '[skipped without result]'}"
                )
        rendered = "\n\n".join(sections)
        if len(rendered) <= 20_000:
            return rendered
        marker = "\n\n[Dependency context truncated to fit the 20,000 character limit.]"
        return rendered[: 20_000 - len(marker)] + marker

    async def _ensure_goal_run(self, goal: ConversationGoal) -> RuntimeRun:
        if goal.run_id:
            existing = await self.run_registry.get_run(goal.run_id)
            if existing is not None:
                return existing
        previous_run_id = goal.run_id
        run = await self.run_registry.create_run(
            "goal",  # type: ignore[arg-type]
            goal.title,
            status=(
                "running"
                if goal.status in {"planning", "running"}
                else "failed"
                if goal.status == "needs_attention"
                else "cancelled"
                if goal.status == "cancelled"
                else "completed"
                if goal.status == "completed"
                else "pending"
            ),
            source_id=goal.goal_id,
            metadata={
                "goal_id": goal.goal_id,
                "planner_xpert_id": goal.planner_xpert_id,
                "planner_version": goal.planner_version,
                "plan_revision": goal.plan_revision,
                "recovery_of_run_id": previous_run_id,
            },
        )
        await self.goal_store.update_goal(goal.goal_id, run_id=run.run_id)
        if previous_run_id:
            await self._checkpoint(
                run,
                "goal.recovered",
                "Goal coordinator recovered persisted goal",
                goal.status,
                {"goal_id": goal.goal_id, "previous_run_id": previous_run_id},
            )
        else:
            await self._checkpoint(
                run,
                "goal.created",
                "Goal registered",
                goal.status,
                {"goal_id": goal.goal_id},
            )
        return run

    async def _checkpoint(
        self,
        run: RuntimeRun,
        event_type: str,
        title: str,
        summary: str,
        metadata: dict[str, Any],
        *,
        severity: str = "info",
    ) -> None:
        await self.run_registry.record_checkpoint(
            run.run_id,
            event_type=event_type,
            title=title,
            summary=summary[:500],
            severity=severity,
            metadata=metadata,
        )

    @staticmethod
    def _step(goal: ConversationGoal, step_id: str) -> GoalStep:
        step = next((item for item in goal.steps if item.step_id == step_id), None)
        if step is None:
            raise GoalNotFoundError(f"Goal step not found: {step_id}")
        return step

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Goal coordinator loop failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
