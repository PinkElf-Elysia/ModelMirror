from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


GoalStatus = Literal[
    "planning",
    "awaiting_review",
    "running",
    "paused",
    "needs_attention",
    "completed",
    "cancelled",
]
GoalStepStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "waiting_client",
    "completed",
    "failed",
    "blocked",
    "skipped",
    "cancelled",
]

GOAL_EDITABLE_STATUSES = {"awaiting_review", "paused", "needs_attention"}
GOAL_ACTIVE_STATUSES = {"planning", "running", "paused", "needs_attention"}
GOAL_TERMINAL_STATUSES = {"completed", "cancelled"}
STEP_TERMINAL_STATUSES = {"completed", "skipped", "cancelled"}


class GoalStoreError(Exception):
    """Base error raised by the conversation goal store."""


class GoalNotFoundError(GoalStoreError):
    """Raised when a goal cannot be found."""


class GoalConflictError(GoalStoreError):
    """Raised when a goal revision or state no longer matches."""


class GoalValidationError(GoalStoreError):
    """Raised when a goal plan is invalid."""


@dataclass(slots=True)
class GoalStep:
    step_id: str
    title: str
    instruction: str
    target_xpert_id: str
    depends_on: list[str] = field(default_factory=list)
    target_version: int | None = None
    status: GoalStepStatus = "pending"
    task_id: str | None = None
    handoff_id: str | None = None
    xpert_run_id: str | None = None
    result: str | None = None
    error: str | None = None
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass(slots=True)
class ConversationGoal:
    goal_id: str
    title: str
    objective: str
    planner_xpert_id: str
    planner_version: int
    source_xpert_id: str | None = None
    source_conversation_id: str | None = None
    file_asset_ids: list[str] = field(default_factory=list)
    messages: list[dict[str, str]] = field(default_factory=list)
    status: GoalStatus = "planning"
    plan_summary: str = ""
    plan_revision: int = 0
    final_step_id: str | None = None
    max_parallel: int = 2
    steps: list[GoalStep] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    run_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


def goal_to_payload(goal: ConversationGoal, *, include_content: bool = True) -> dict[str, Any]:
    payload = asdict(goal)
    if not include_content:
        payload.pop("messages", None)
        payload["objective_preview"] = goal.objective[:240]
        payload.pop("objective", None)
        payload.pop("result", None)
        payload["step_count"] = len(goal.steps)
        payload["completed_step_count"] = sum(
            1 for step in goal.steps if step.status in {"completed", "skipped"}
        )
        payload.pop("steps", None)
    return payload


def _normalize_step_payload(item: GoalStep | dict[str, Any]) -> GoalStep:
    if isinstance(item, GoalStep):
        return GoalStep(**asdict(item))
    if not isinstance(item, dict):
        raise GoalValidationError("Each goal step must be an object.")
    step_id = str(item.get("step_id") or item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    instruction = str(item.get("instruction") or item.get("description") or "").strip()
    target_xpert_id = str(
        item.get("target_xpert_id") or item.get("targetXpertId") or ""
    ).strip()
    depends_raw = item.get("depends_on") or item.get("dependsOn") or []
    if not isinstance(depends_raw, list):
        raise GoalValidationError(f"Step {step_id or '<unknown>'} depends_on must be a list.")
    depends_on = [str(value).strip() for value in depends_raw if str(value).strip()]
    return GoalStep(
        step_id=step_id,
        title=title,
        instruction=instruction,
        target_xpert_id=target_xpert_id,
        depends_on=depends_on,
        target_version=(
            int(item["target_version"])
            if item.get("target_version") is not None
            else None
        ),
        status=str(item.get("status") or "pending"),  # type: ignore[arg-type]
        task_id=item.get("task_id"),
        handoff_id=item.get("handoff_id"),
        xpert_run_id=item.get("xpert_run_id"),
        result=item.get("result"),
        error=item.get("error"),
        attempts=int(item.get("attempts") or 0),
        created_at=float(item.get("created_at") or time.time()),
        updated_at=float(item.get("updated_at") or time.time()),
    )


def validate_goal_plan(
    steps: list[GoalStep | dict[str, Any]],
    final_step_id: str | None,
) -> list[GoalStep]:
    normalized = [_normalize_step_payload(item) for item in steps]
    if not 2 <= len(normalized) <= 20:
        raise GoalValidationError("A goal plan must contain between 2 and 20 steps.")
    ids = [step.step_id for step in normalized]
    if any(not step_id for step_id in ids):
        raise GoalValidationError("Every goal step requires a step_id.")
    if len(set(ids)) != len(ids):
        raise GoalValidationError("Goal step IDs must be unique.")
    known = set(ids)
    for step in normalized:
        if not step.title:
            raise GoalValidationError(f"Step {step.step_id} requires a title.")
        if not step.instruction:
            raise GoalValidationError(f"Step {step.step_id} requires an instruction.")
        if not step.target_xpert_id:
            raise GoalValidationError(f"Step {step.step_id} requires a target Xpert.")
        if step.step_id in step.depends_on:
            raise GoalValidationError(f"Step {step.step_id} cannot depend on itself.")
        missing = [dependency for dependency in step.depends_on if dependency not in known]
        if missing:
            raise GoalValidationError(
                f"Step {step.step_id} references unknown dependencies: {', '.join(missing)}."
            )

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {step.step_id: step for step in normalized}

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise GoalValidationError("Goal step dependencies must be acyclic.")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dependency in by_id[step_id].depends_on:
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in ids:
        visit(step_id)

    selected_final = str(final_step_id or "").strip()
    if not selected_final or selected_final not in known:
        raise GoalValidationError("A goal plan requires one valid final_step_id.")

    ancestors: set[str] = set()

    def collect_ancestors(step_id: str) -> None:
        for dependency in by_id[step_id].depends_on:
            if dependency not in ancestors:
                ancestors.add(dependency)
                collect_ancestors(dependency)

    collect_ancestors(selected_final)
    unreachable = known - ancestors - {selected_final}
    if unreachable:
        raise GoalValidationError(
            "The final step must depend on every other step; unreachable steps: "
            + ", ".join(sorted(unreachable))
            + "."
        )
    return normalized


class GoalStore:
    """Conversation goals with optional atomic JSON persistence."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        self._lock = asyncio.Lock()
        self._goals: dict[str, ConversationGoal] = {}
        self.storage_dir = Path(storage_dir) if storage_dir is not None else None
        self.storage_path = self.storage_dir / "goals.json" if self.storage_dir else None
        if self.storage_path is not None:
            self._load_snapshot()

    async def create_goal(
        self,
        *,
        title: str,
        objective: str,
        planner_xpert_id: str,
        planner_version: int,
        source_xpert_id: str | None = None,
        source_conversation_id: str | None = None,
        file_asset_ids: list[str] | None = None,
        messages: list[dict[str, str]] | None = None,
        max_parallel: int = 2,
    ) -> ConversationGoal:
        goal = ConversationGoal(
            goal_id=str(uuid.uuid4()),
            title=title.strip(),
            objective=objective.strip(),
            planner_xpert_id=planner_xpert_id,
            planner_version=planner_version,
            source_xpert_id=source_xpert_id,
            source_conversation_id=source_conversation_id,
            file_asset_ids=list(dict.fromkeys(file_asset_ids or []))[:5],
            messages=[dict(item) for item in (messages or [])][-20:],
            max_parallel=max(1, min(int(max_parallel), 2)),
        )
        async with self._lock:
            self._goals[goal.goal_id] = goal
            self._persist_unlocked()
        return goal

    async def get_goal(self, goal_id: str) -> ConversationGoal | None:
        async with self._lock:
            return self._goals.get(goal_id)

    async def require_goal(self, goal_id: str) -> ConversationGoal:
        goal = await self.get_goal(goal_id)
        if goal is None:
            raise GoalNotFoundError(f"Goal not found: {goal_id}")
        return goal

    async def list_goals(
        self,
        *,
        status: GoalStatus | None = None,
        search: str = "",
        limit: int = 50,
    ) -> list[ConversationGoal]:
        async with self._lock:
            goals = list(self._goals.values())
        if status is not None:
            goals = [goal for goal in goals if goal.status == status]
        query = search.strip().lower()
        if query:
            goals = [
                goal
                for goal in goals
                if query in f"{goal.title} {goal.objective}".lower()
            ]
        goals.sort(key=lambda goal: goal.updated_at, reverse=True)
        return goals[: max(1, min(limit, 200))]

    async def replace_plan(
        self,
        goal_id: str,
        *,
        steps: list[GoalStep | dict[str, Any]],
        final_step_id: str,
        summary: str = "",
        expected_revision: int | None = None,
        status: GoalStatus = "awaiting_review",
    ) -> ConversationGoal:
        normalized = validate_goal_plan(steps, final_step_id)
        async with self._lock:
            goal = self._require_unlocked(goal_id)
            if expected_revision is not None and goal.plan_revision != expected_revision:
                raise GoalConflictError(
                    f"Goal plan revision changed from {expected_revision} to {goal.plan_revision}."
                )
            goal.steps = normalized
            goal.final_step_id = final_step_id
            goal.plan_summary = summary[:4000]
            goal.plan_revision += 1
            goal.status = status
            goal.error = None
            goal.touch()
            self._persist_unlocked()
            return goal

    async def update_goal(
        self,
        goal_id: str,
        *,
        status: GoalStatus | None = None,
        result: str | None = None,
        error: str | None = None,
        run_id: str | None = None,
        clear_result: bool = False,
        clear_error: bool = False,
    ) -> ConversationGoal:
        async with self._lock:
            goal = self._require_unlocked(goal_id)
            if status is not None:
                goal.status = status
            if clear_result:
                goal.result = None
            elif result is not None:
                goal.result = result[:100_000]
            if clear_error:
                goal.error = None
            elif error is not None:
                goal.error = error[:2000]
            if run_id is not None:
                goal.run_id = run_id
            goal.touch()
            self._persist_unlocked()
            return goal

    async def update_step(
        self,
        goal_id: str,
        step_id: str,
        **patch: Any,
    ) -> GoalStep:
        allowed = {
            "title",
            "instruction",
            "target_xpert_id",
            "target_version",
            "depends_on",
            "status",
            "task_id",
            "handoff_id",
            "xpert_run_id",
            "result",
            "error",
            "attempts",
        }
        async with self._lock:
            goal = self._require_unlocked(goal_id)
            step = next((item for item in goal.steps if item.step_id == step_id), None)
            if step is None:
                raise GoalNotFoundError(f"Goal step not found: {step_id}")
            for key, value in patch.items():
                if key in allowed:
                    setattr(step, key, value)
            step.touch()
            goal.touch()
            self._persist_unlocked()
            return step

    async def mutate_steps(
        self,
        goal_id: str,
        mutation: Any,
    ) -> ConversationGoal:
        async with self._lock:
            goal = self._require_unlocked(goal_id)
            mutation(goal)
            goal.touch()
            self._persist_unlocked()
            return goal

    def _require_unlocked(self, goal_id: str) -> ConversationGoal:
        goal = self._goals.get(goal_id)
        if goal is None:
            raise GoalNotFoundError(f"Goal not found: {goal_id}")
        return goal

    def _load_snapshot(self) -> None:
        assert self.storage_path is not None
        if not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for item in payload.get("goals", []):
                steps = [_normalize_step_payload(step) for step in item.get("steps", [])]
                goal = ConversationGoal(
                    goal_id=str(item["goal_id"]),
                    title=str(item.get("title") or "Untitled goal"),
                    objective=str(item.get("objective") or ""),
                    planner_xpert_id=str(item.get("planner_xpert_id") or ""),
                    planner_version=int(item.get("planner_version") or 1),
                    source_xpert_id=item.get("source_xpert_id"),
                    source_conversation_id=item.get("source_conversation_id"),
                    file_asset_ids=[
                        str(value)
                        for value in item.get("file_asset_ids", [])
                        if str(value)
                    ][:5],
                    messages=[dict(value) for value in item.get("messages", []) if isinstance(value, dict)],
                    status=str(item.get("status") or "planning"),  # type: ignore[arg-type]
                    plan_summary=str(item.get("plan_summary") or ""),
                    plan_revision=int(item.get("plan_revision") or 0),
                    final_step_id=item.get("final_step_id"),
                    max_parallel=max(1, min(int(item.get("max_parallel") or 2), 4)),
                    steps=steps,
                    result=item.get("result"),
                    error=item.get("error"),
                    run_id=item.get("run_id"),
                    created_at=float(item.get("created_at") or time.time()),
                    updated_at=float(item.get("updated_at") or time.time()),
                )
                self._goals[goal.goal_id] = goal
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise GoalStoreError(f"Failed to load goal store: {exc}") from exc

    def _persist_unlocked(self) -> None:
        if self.storage_path is None:
            return
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "conversation-goals-v1",
            "goals": [asdict(goal) for goal in self._goals.values()],
        }
        temporary = self.storage_path.with_suffix(
            f".{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.storage_path)
