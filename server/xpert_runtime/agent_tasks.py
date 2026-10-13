from __future__ import annotations

import asyncio
import copy
import json
import math
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .events import RuntimeEventStore
from .execution_store import WorkflowExecution, WorkflowExecutionStore
from .workflow_outcomes import resolve_workflow_outcome

AgentTaskStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "waiting_client",
    "needs_attention",
    "completed",
    "failed",
    "cancelled",
]
AgentHandoffStatus = Literal[
    "pending",
    "accepted",
    "retry_wait",
    "waiting_approval",
    "waiting_client",
    "needs_attention",
    "rejected",
    "completed",
    "dead_letter",
]
HANDOFF_TRANSITIONS: dict[AgentHandoffStatus, set[AgentHandoffStatus]] = {
    "pending": {"accepted", "rejected", "dead_letter"},
    "accepted": {"completed", "retry_wait", "waiting_approval", "waiting_client", "dead_letter"},
    "waiting_approval": {"completed", "accepted", "needs_attention", "dead_letter"},
    "waiting_client": {"completed", "accepted", "needs_attention", "dead_letter"},
    "needs_attention": {"completed", "accepted", "dead_letter"},
    "retry_wait": {"accepted", "dead_letter", "pending"},
    "rejected": set(),
    "completed": set(),
    "dead_letter": {"pending"},
}
TERMINAL_HANDOFF_STATUSES = {"rejected", "completed", "dead_letter"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
AUTO_XPERT_TARGET_PREFIX = "xpert:"


class HandoffOwnershipError(ValueError):
    """A stale worker cannot mutate or settle a claimed handoff."""


@dataclass(slots=True)
class AgentTask:
    task_id: str
    title: str
    input: str
    status: AgentTaskStatus = "pending"
    result: str | None = None
    error: str | None = None
    source_agent: str | None = None
    assigned_agent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass(slots=True)
class AgentHandoff:
    handoff_id: str
    task_id: str
    source_agent: str
    target_agent: str
    reason: str
    status: AgentHandoffStatus = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


class AgentTaskStore:
    """Agent task and handoff store with optional atomic JSON persistence."""

    def __init__(
        self,
        event_store: RuntimeEventStore | None = None,
        storage_dir: str | Path | None = None,
    ) -> None:
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition(self._lock)
        self._tasks: dict[str, AgentTask] = {}
        self._handoffs: list[AgentHandoff] = []
        self._occurrence_index: dict[str, str] = {}
        self._event_store = event_store
        self._handoff_commit_uncertain = False
        self.storage_dir = Path(storage_dir) if storage_dir is not None else None
        self.storage_path = (
            self.storage_dir / "agent_tasks.json" if self.storage_dir is not None else None
        )
        if self.storage_path is not None:
            self._load_snapshot()

    async def create_task(
        self,
        title: str,
        input_text: str,
        *,
        source_agent: str | None = None,
        assigned_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
        occurrence_key: str | None = None,
    ) -> AgentTask:
        occurrence = str(occurrence_key or "").strip()
        task = AgentTask(
            task_id=(f"task_{uuid.uuid4().hex}" if occurrence else str(uuid.uuid4())),
            title=title,
            input=input_text,
            source_agent=source_agent,
            assigned_agent=assigned_agent,
            metadata=copy.deepcopy(metadata or {}),
        )
        async with self._changed:
            if occurrence:
                existing_id = self._occurrence_index.get(f"task:{occurrence}")
                existing = self._tasks.get(str(existing_id or ""))
                if existing is not None:
                    return copy.deepcopy(existing)
            self._tasks[task.task_id] = task
            if occurrence:
                self._occurrence_index[f"task:{occurrence}"] = task.task_id
            self._persist_unlocked()
            self._changed.notify_all()
            task = copy.deepcopy(task)
        await self._record(
            "agent.task.created",
            task_id=task.task_id,
            payload={
                "task_id": task.task_id,
                "title": task.title,
                "source_agent": source_agent,
                "assigned_agent": assigned_agent,
            },
        )
        return task

    async def get_task(self, task_id: str) -> AgentTask | None:
        async with self._lock:
            return copy.deepcopy(self._tasks.get(task_id))

    async def list_tasks(
        self,
        *,
        status: AgentTaskStatus | None = None,
        limit: int = 50,
    ) -> list[AgentTask]:
        async with self._lock:
            tasks = copy.deepcopy(list(self._tasks.values()))
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        tasks.sort(key=lambda task: task.created_at, reverse=True)
        return tasks[: max(1, limit)]

    async def list_goal_tasks(self, goal_id: str | None = None) -> list[AgentTask]:
        """Find durable Goal associations without the management list's page limit."""
        async with self._lock:
            return [
                copy.deepcopy(task)
                for task in self._tasks.values()
                if isinstance(task.metadata.get("goal_id"), str)
                and task.metadata["goal_id"]
                and task.source_agent == f"goal:{task.metadata['goal_id']}"
                and (goal_id is None or task.metadata["goal_id"] == goal_id)
            ]

    async def publish_unstarted_goal_handoff(
        self, *, goal_id: str, step_id: str, task_id: str,
        handoff_id: str, target_xpert_id: str, target_version: int,
    ) -> bool:
        """Finish a linked registration only when no dispatch has occurred."""
        async with self._changed:
            task = self._tasks.get(task_id)
            handoff = self._find_handoff_unlocked(handoff_id)
            expected = {
                "goal_id": goal_id, "goal_step_id": step_id,
                "target_xpert_id": target_xpert_id, "target_xpert_version": target_version,
            }
            if (
                task is None or handoff is None or handoff.task_id != task_id
                or task.status != "pending" or handoff.status != "pending"
                or task.source_agent != f"goal:{goal_id}"
                or handoff.source_agent != task.source_agent
                or task.assigned_agent != f"xpert:{target_xpert_id}"
                or handoff.target_agent != task.assigned_agent
                or any(item.metadata.get(key) != value for item in (task, handoff) for key, value in expected.items())
                or handoff.metadata.get("ready_for_execution") is not False
                or handoff.metadata.get("execution_mode") != "xpert_auto"
                or type(handoff.metadata.get("attempts", 0)) is not int
                or handoff.metadata.get("attempts", 0) != 0
                or any(type(item.metadata.get("target_xpert_version")) is not int for item in (task, handoff))
                or any(handoff.metadata.get(key) for key in (
                    "lease_owner", "lease_token", "lease_expires_at", "xpert_run_id", "workflow_task_id",
                ))
            ):
                return False
            index = self._handoffs.index(handoff)
            updated = copy.deepcopy(handoff)
            updated.metadata["ready_for_execution"] = True
            updated.touch()
            self._handoffs[index] = updated
            try:
                self._persist_unlocked()
            except BaseException:
                self._handoffs[index] = handoff
                raise
            self._changed.notify_all()
            return True

    async def update_task(
        self,
        task_id: str,
        *,
        status: AgentTaskStatus | None = None,
        result: str | None = None,
        error: str | None = None,
        assigned_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
        clear_result: bool = False,
        clear_error: bool = False,
    ) -> AgentTask:
        async with self._changed:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Agent task not found: {task_id}")
            if task.status in TERMINAL_TASK_STATUSES:
                # Only an explicit handoff requeue may reopen a failed task.
                return copy.deepcopy(task)
            task = copy.deepcopy(task)
            if status is not None:
                task.status = status
            if clear_result:
                task.result = None
            elif result is not None:
                task.result = result
            if clear_error:
                task.error = None
            elif error is not None:
                task.error = error
            if assigned_agent is not None:
                task.assigned_agent = assigned_agent
            if metadata is not None:
                task.metadata.update(copy.deepcopy(metadata))
            task.touch()
            self._commit_task_unlocked(task)
            updated = copy.deepcopy(task)
        await self._record(
            "agent.task.updated",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "status": updated.status,
                "has_result": updated.result is not None,
                "has_error": updated.error is not None,
            },
        )
        return updated

    async def cancel_task(self, task_id: str, reason: str = "cancelled") -> AgentTask:
        async with self._changed:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Agent task not found: {task_id}")
            if task.status in TERMINAL_TASK_STATUSES:
                return copy.deepcopy(task)
            original_task = task
            original_handoffs = self._handoffs
            task = copy.deepcopy(task)
            task.status = "cancelled"
            task.error = reason
            task.touch()
            handoffs = list(original_handoffs)
            for index, handoff in enumerate(handoffs):
                if handoff.task_id != task_id or handoff.status in TERMINAL_HANDOFF_STATUSES:
                    continue
                handoff = copy.deepcopy(handoff)
                handoff.status = "dead_letter"
                handoff.metadata.update({
                    "last_error": "HANDOFF_CANCELLED",
                    "ready_for_execution": False,
                    "lease_owner": "", "lease_token": "", "lease_expires_at": 0.0,
                    "next_attempt_at": 0.0,
                })
                handoff.touch()
                handoffs[index] = handoff
            # The task and every still-active handoff share one durable decision.
            self._tasks[task_id], self._handoffs = task, handoffs
            try:
                self._persist_unlocked()
            except BaseException:
                self._tasks[task_id], self._handoffs = original_task, original_handoffs
                raise
            self._changed.notify_all()
            task = copy.deepcopy(task)
        await self._record(
            "agent.task.updated",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "status": task.status,
                "has_result": task.result is not None,
                "has_error": task.error is not None,
            },
        )
        await self._record(
            "agent.task.cancelled",
            task_id=task_id,
            payload={"task_id": task_id, "reason": reason},
        )
        return task

    async def expire_handoff(self, handoff_id: str) -> bool:
        """Commit timeout and task cancellation together; retry only local propagation."""
        async with self._changed:
            if self._handoff_commit_uncertain:
                raise HandoffOwnershipError("交接持久化结果不确定，须重新加载后核对超时决定。")
            handoff = self._find_handoff_unlocked(handoff_id)
            task = self._tasks.get(handoff.task_id) if handoff is not None else None
            if handoff is None or task is None:
                raise HandoffOwnershipError("超时交接或父任务不存在。")
            timed_out = handoff.status == "dead_letter" and handoff.metadata.get("last_error") == "HANDOFF_TIMEOUT"
            if handoff.status in TERMINAL_HANDOFF_STATUSES and not timed_out:
                return False
            if task.status in TERMINAL_TASK_STATUSES:
                if timed_out and not (task.status == "cancelled" and task.error == "HANDOFF_TIMEOUT"):
                    raise HandoffOwnershipError("交接超时终态与父任务取消决定不一致。")
                return timed_out
            bound_handoff = task.metadata.get("handoff_id")
            if bound_handoff and bound_handoff != handoff_id:
                raise HandoffOwnershipError("超时交接与父任务绑定不一致。")
            original_task, original_handoffs = task, self._handoffs
            task = copy.deepcopy(task)
            task.status, task.error = "cancelled", "HANDOFF_TIMEOUT"
            task.touch()
            handoffs = list(original_handoffs)
            for index, item in enumerate(handoffs):
                if item.task_id != task.task_id:
                    continue
                if item.status in TERMINAL_HANDOFF_STATUSES and item.handoff_id != handoff_id:
                    continue
                item = copy.deepcopy(item)
                item.status = "dead_letter"
                item.metadata.update({
                    "last_error": "HANDOFF_TIMEOUT" if item.handoff_id == handoff_id else "HANDOFF_CANCELLED",
                    "ready_for_execution": False, "lease_owner": "", "lease_token": "",
                    "lease_expires_at": 0.0, "next_attempt_at": 0.0,
                })
                if item.handoff_id == handoff_id:
                    item.metadata.setdefault("dead_lettered_at", time.time())
                item.touch()
                handoffs[index] = item
            self._tasks[task.task_id], self._handoffs = task, handoffs
            try:
                self._persist_unlocked()
            except BaseException:
                self._tasks[task.task_id], self._handoffs = original_task, original_handoffs
                self._handoff_commit_uncertain = True
                raise
            self._changed.notify_all()
        await self._record("agent.handoff.dead_letter", task_id=task.task_id, payload={
            "handoff_id": handoff_id, "task_id": task.task_id,
            "status": "dead_letter", "error": "HANDOFF_TIMEOUT",
        })
        await self._record("agent.task.cancelled", task_id=task.task_id, payload={
            "task_id": task.task_id, "reason": "HANDOFF_TIMEOUT",
        })
        return True

    async def project_workflow_outcome(
        self,
        executions: WorkflowExecutionStore,
        snapshot: WorkflowExecution,
        event: dict[str, Any],
        *,
        completion_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Commit a verified child decision to its task/handoff pair once."""
        snapshot, event = copy.deepcopy(snapshot), copy.deepcopy(event)
        completion_metadata = {
            key: copy.deepcopy(value)
            for key, value in (completion_metadata or {}).items()
            if key in {
                "completed_by", "approval_id", "approval_status",
                "client_request_id", "client_request_status",
            }
        }
        async with self._changed:
            with executions.projection_local_operation(
                snapshot.task_id, expected_snapshot=snapshot,
            ):
                outcome = resolve_workflow_outcome(event, snapshot)
                if outcome.status == "waiting" and outcome.wait_kind not in {"approval", "client_tool"}:
                    return False
                handoff_id = str(snapshot.runtime_metadata.get("handoff_id") or "")
                handoff = self._find_handoff_unlocked(handoff_id)
                if handoff is None:
                    raise KeyError("父状态投影的交接不存在。")
                task = self._tasks.get(handoff.task_id)
                if task is None:
                    raise KeyError("父状态投影的任务不存在。")
                source_task = snapshot.runtime_metadata.get("agent_task_id")
                bound_handoff = task.metadata.get("handoff_id")
                if (
                    (source_task and source_task != task.task_id)
                    or (bound_handoff and bound_handoff != handoff_id)
                ):
                    raise ValueError("父状态投影的任务与交接绑定不一致。")
                bound_run = handoff.metadata.get("xpert_run_id")
                if bound_run and bound_run not in [snapshot.run_id, *snapshot.previous_run_ids]:
                    return False
                requeued_at = self._metadata_float(handoff.metadata, "requeued_at")
                if requeued_at and snapshot.created_at <= requeued_at:
                    return False
                prior = handoff.metadata.get("workflow_projection") or {}
                if prior and prior.get("task_id") != snapshot.task_id and not requeued_at:
                    return False
                receipt = {
                    "task_id": snapshot.task_id, "run_id": snapshot.run_id,
                    "revision": snapshot.revision, "status": outcome.status,
                    "wait_kind": outcome.wait_kind, "wait_id": outcome.wait_id,
                }
                if outcome.status == "waiting":
                    task_status = handoff_status = (
                        "waiting_approval" if outcome.wait_kind == "approval" else "waiting_client"
                    )
                elif outcome.status == "cancelled":
                    task_status, handoff_status = "cancelled", "dead_letter"
                else:
                    task_status = handoff_status = "completed"
                if (
                    prior == receipt and task.metadata.get("workflow_projection") == receipt
                    and task.status == task_status and handoff.status == handoff_status
                ):
                    return True
                if task.status in TERMINAL_TASK_STATUSES or handoff.status in TERMINAL_HANDOFF_STATUSES:
                    return False
                if handoff.status not in {"accepted", "waiting_approval", "waiting_client", "needs_attention"}:
                    return False
                original_task, original_handoff = task, handoff
                index = self._handoffs.index(handoff)
                task, handoff = copy.deepcopy(task), copy.deepcopy(handoff)
                task.status, handoff.status = task_status, handoff_status
                task.metadata.update({
                    "handoff_id": handoff_id, "workflow_projection": copy.deepcopy(receipt),
                    "xpert_run_id": snapshot.run_id,
                })
                handoff.metadata.update({
                    "workflow_projection": copy.deepcopy(receipt), "xpert_run_id": snapshot.run_id,
                })
                if outcome.status == "completed":
                    result = str(snapshot.result or "")
                    task.result = result[:100_000]
                    task.error = None
                    task.metadata.update(copy.deepcopy(completion_metadata))
                    handoff.metadata.update({
                        **completion_metadata, "result": task.result,
                        "result_length": len(result), "completed_at": time.time(),
                    })
                elif outcome.status == "cancelled":
                    task.error = "WORKFLOW_CANCELLED"
                    handoff.metadata.update({
                        "last_error": "WORKFLOW_CANCELLED", "dead_lettered_at": time.time(),
                        "ready_for_execution": False,
                    })
                else:
                    task.error = None
                task.touch()
                handoff.touch()
                self._tasks[task.task_id], self._handoffs[index] = task, handoff
                try:
                    self._persist_unlocked()
                except Exception:
                    self._tasks[task.task_id], self._handoffs[index] = original_task, original_handoff
                    raise
                self._changed.notify_all()
        await self._record(
            f"agent.handoff.{handoff_status}", task_id=task.task_id,
            payload={
                "handoff_id": handoff_id, "task_id": task.task_id, "status": handoff_status,
                "source_agent": handoff.source_agent, "target_agent": handoff.target_agent,
            },
        )
        await self._record(
            "agent.task.updated", task_id=task.task_id,
            payload={
                "task_id": task.task_id, "status": task_status,
                "has_result": task.result is not None, "has_error": task.error is not None,
            },
        )
        return True

    async def create_handoff(
        self,
        task_id: str,
        source_agent: str,
        target_agent: str,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
        occurrence_key: str | None = None,
    ) -> AgentHandoff:
        occurrence = str(occurrence_key or "").strip()
        handoff = AgentHandoff(
            handoff_id=(
                f"handoff_{uuid.uuid4().hex}" if occurrence else str(uuid.uuid4())
            ),
            task_id=task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            reason=reason,
            metadata=dict(metadata or {}),
        )
        async with self._changed:
            if task_id not in self._tasks:
                raise KeyError(f"Agent task not found: {task_id}")
            if occurrence:
                existing_id = self._occurrence_index.get(f"handoff:{occurrence}")
                existing = self._find_handoff_unlocked(str(existing_id or ""))
                if existing is not None:
                    return existing
            if self._tasks[task_id].status == "cancelled":
                raise ValueError("已取消任务不能创建新的交接。")
            self._handoffs.append(handoff)
            if occurrence:
                self._occurrence_index[f"handoff:{occurrence}"] = handoff.handoff_id
            self._persist_unlocked()
            self._changed.notify_all()
        await self._record(
            "agent.handoff.created",
            task_id=task_id,
            payload={
                "handoff_id": handoff.handoff_id,
                "task_id": task_id,
                "source_agent": source_agent,
                "target_agent": target_agent,
            },
        )
        return handoff

    async def create_task_and_handoff(
        self,
        *,
        title: str,
        input_text: str,
        source_agent: str,
        target_agent: str,
        reason: str,
        occurrence_key: str,
        task_metadata: dict[str, Any] | None = None,
        handoff_metadata: dict[str, Any] | None = None,
    ) -> tuple[AgentTask, AgentHandoff]:
        """Atomically materialize a routed task and its handoff once."""

        occurrence = str(occurrence_key or "").strip()
        if not occurrence:
            raise ValueError("A routed handoff occurrence key is required.")
        task_index_key = f"router-task:{occurrence}"
        handoff_index_key = f"router-handoff:{occurrence}"
        created_task = False
        created_handoff = False
        async with self._changed:
            task = self._tasks.get(self._occurrence_index.get(task_index_key, ""))
            handoff = self._find_handoff_unlocked(
                self._occurrence_index.get(handoff_index_key, "")
            )
            if task is not None and handoff is not None:
                return copy.deepcopy(task), handoff
            if task is not None or handoff is not None:
                raise RuntimeError("Routed handoff occurrence index is inconsistent.")
            task = AgentTask(
                task_id=f"task_{uuid.uuid4().hex}",
                title=title,
                input=input_text,
                source_agent=source_agent,
                assigned_agent=target_agent,
                metadata=copy.deepcopy(task_metadata or {}),
            )
            handoff = AgentHandoff(
                handoff_id=f"handoff_{uuid.uuid4().hex}",
                task_id=task.task_id,
                source_agent=source_agent,
                target_agent=target_agent,
                reason=reason,
                metadata=dict(handoff_metadata or {}),
            )
            self._tasks[task.task_id] = task
            self._handoffs.append(handoff)
            self._occurrence_index[task_index_key] = task.task_id
            self._occurrence_index[handoff_index_key] = handoff.handoff_id
            created_task = True
            created_handoff = True
            self._persist_unlocked()
            self._changed.notify_all()
            task = copy.deepcopy(task)
        if created_task:
            await self._record(
                "agent.task.created",
                task_id=task.task_id,
                payload={
                    "task_id": task.task_id,
                    "title": task.title,
                    "source_agent": source_agent,
                    "assigned_agent": target_agent,
                },
            )
        if created_handoff:
            await self._record(
                "agent.handoff.created",
                task_id=task.task_id,
                payload={
                    "handoff_id": handoff.handoff_id,
                    "task_id": task.task_id,
                    "source_agent": source_agent,
                    "target_agent": target_agent,
                },
            )
        return task, handoff

    async def list_handoffs(self, task_id: str | None = None) -> list[AgentHandoff]:
        async with self._lock:
            handoffs = list(self._handoffs)
        if task_id is not None:
            handoffs = [handoff for handoff in handoffs if handoff.task_id == task_id]
        handoffs.sort(key=lambda handoff: handoff.created_at, reverse=True)
        return handoffs

    async def list_executable_handoffs(
        self,
        *,
        now: float | None = None,
        limit: int = 20,
    ) -> list[AgentHandoff]:
        current_time = time.time() if now is None else now
        async with self._lock:
            handoffs = [
                copy.deepcopy(handoff) for handoff in self._handoffs
                if (task := self._tasks.get(handoff.task_id)) is not None
                and task.status not in TERMINAL_TASK_STATUSES
            ]
        eligible: list[AgentHandoff] = []
        for handoff in handoffs:
            metadata = handoff.metadata
            if not handoff.target_agent.startswith(AUTO_XPERT_TARGET_PREFIX):
                continue
            if metadata.get("execution_mode") != "xpert_auto":
                continue
            if metadata.get("ready_for_execution") is not True:
                continue
            next_attempt_at = self._metadata_float(metadata, "next_attempt_at")
            lease_expires_at = self._metadata_float(metadata, "lease_expires_at")
            if handoff.status == "pending":
                eligible.append(handoff)
            elif handoff.status == "retry_wait" and next_attempt_at <= current_time:
                eligible.append(handoff)
            elif handoff.status == "accepted" and lease_expires_at <= current_time:
                eligible.append(handoff)
        eligible.sort(key=lambda handoff: handoff.created_at)
        return eligible[: max(1, limit)]

    async def get_handoff(self, handoff_id: str) -> AgentHandoff | None:
        async with self._lock:
            return self._find_handoff_unlocked(handoff_id)

    async def update_handoff_metadata(
        self,
        handoff_id: str,
        metadata: dict[str, Any],
    ) -> AgentHandoff:
        async with self._changed:
            handoff = self._find_handoff_unlocked(handoff_id)
            if handoff is None:
                raise KeyError(f"Agent handoff not found: {handoff_id}")
            task = self._tasks.get(handoff.task_id)
            if task is not None and task.status == "cancelled":
                return copy.deepcopy(handoff)
            handoff.metadata.update(metadata)
            handoff.touch()
            self._persist_unlocked()
            self._changed.notify_all()
            return handoff

    async def claim_handoff(
        self,
        handoff_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        max_attempts: int = 3,
        now: float | None = None,
    ) -> AgentHandoff:
        current_time = time.time() if now is None else now
        async with self._changed:
            if self._handoff_commit_uncertain:
                raise HandoffOwnershipError("交接持久化结果不确定，须重新加载并核对，禁止继续领取。")
            handoff = self._find_handoff_unlocked(handoff_id)
            if handoff is None:
                raise KeyError(f"Agent handoff not found: {handoff_id}")
            task = self._tasks.get(handoff.task_id)
            if task is None or task.status in TERMINAL_TASK_STATUSES:
                raise ValueError("父任务已终止或不存在，不能领取交接。")
            if not handoff.target_agent.startswith(AUTO_XPERT_TARGET_PREFIX):
                raise ValueError("Handoff target is not an executable Xpert target.")
            if handoff.metadata.get("execution_mode") != "xpert_auto":
                raise ValueError("Handoff execution mode is not xpert_auto.")
            if handoff.metadata.get("ready_for_execution") is not True:
                raise ValueError("Handoff is not ready for execution.")
            next_attempt_at = self._metadata_float(
                handoff.metadata,
                "next_attempt_at",
            )
            lease_expires_at = self._metadata_float(
                handoff.metadata,
                "lease_expires_at",
            )
            if handoff.status == "retry_wait" and next_attempt_at > current_time:
                raise ValueError("Handoff retry is not ready yet.")
            if handoff.status == "accepted" and lease_expires_at > current_time:
                raise ValueError("Handoff is already leased.")
            if handoff.status not in {"pending", "retry_wait", "accepted"}:
                raise ValueError(f"Handoff cannot be claimed from {handoff.status}.")
            attempts = int(handoff.metadata.get("attempts") or 0)
            if attempts >= max_attempts:
                raise ValueError("Handoff attempt limit has been reached.")
            handoff.status = "accepted"
            handoff.metadata.update(
                {
                    "execution_mode": "xpert_auto",
                    "accepted_by": worker_id,
                    "accepted_at": current_time,
                    "lease_owner": worker_id,
                    "lease_token": uuid.uuid4().hex,
                    "lease_expires_at": current_time + max(1.0, lease_seconds),
                    "attempts": attempts + 1,
                    "max_attempts": max_attempts,
                    "next_attempt_at": 0.0,
                }
            )
            handoff.touch()
            self._persist_unlocked()
            self._changed.notify_all()
            # Ownership must remain fixed even when the stored lease is replaced.
            claimed = copy.deepcopy(handoff)
        await self._record(
            "agent.handoff.claimed",
            task_id=claimed.task_id,
            payload={
                "handoff_id": claimed.handoff_id,
                "task_id": claimed.task_id,
                "worker_id": worker_id,
                "attempt": claimed.metadata.get("attempts"),
            },
        )
        return claimed

    def _require_handoff_owner_unlocked(self, handoff: AgentHandoff, task: AgentTask, token: str) -> None:
        expires = handoff.metadata.get("lease_expires_at")
        if (
            self._handoff_commit_uncertain or task.status in TERMINAL_TASK_STATUSES
            or handoff.status != "accepted" or not isinstance(token, str) or not token
            or handoff.metadata.get("lease_token") != token
            or type(expires) not in {int, float} or not math.isfinite(expires) or expires <= time.time()
        ):
            raise HandoffOwnershipError("交接执行权已失效，拒绝旧执行者结算。")

    async def settle_claimed_handoff(
        self, claimed: AgentHandoff, status: AgentHandoffStatus, *,
        metadata: dict[str, Any] | None = None, task_metadata: dict[str, Any] | None = None,
        result: str | None = None, error: str | None = None,
    ) -> AgentHandoff:
        """Fence ownership and commit task/handoff together; no await between check and persist."""
        task_states = {"accepted": "running", "completed": "completed", "dead_letter": "failed",
                       "retry_wait": "running", "waiting_approval": "waiting_approval", "waiting_client": "waiting_client"}
        if status not in task_states:
            raise ValueError("不支持的交接结算状态。")
        async with self._changed:
            handoff = self._find_handoff_unlocked(claimed.handoff_id)
            task = self._tasks.get(claimed.task_id)
            if handoff is None or task is None or handoff.task_id != task.task_id:
                raise HandoffOwnershipError("交接与父任务身份不一致。")
            self._require_handoff_owner_unlocked(handoff, task, claimed.metadata.get("lease_token"))
            index = self._handoffs.index(handoff)
            updated, updated_task = copy.deepcopy(handoff), copy.deepcopy(task)
            updated.status, updated_task.status = status, task_states[status]
            updated.metadata.update(copy.deepcopy(metadata or {}))
            updated_task.metadata.update(copy.deepcopy(task_metadata or {}))
            updated_task.metadata["handoff_id"] = handoff.handoff_id
            updated_task.error = error
            if status == "accepted":
                updated_task.assigned_agent = handoff.target_agent
            else:
                updated.metadata.update({"lease_owner": "", "lease_token": "", "lease_expires_at": 0.0})
            if status == "completed":
                updated_task.result = result
            updated.touch()
            updated_task.touch()
            self._handoffs[index], self._tasks[task.task_id] = updated, updated_task
            try:
                self._persist_unlocked()
            except BaseException:
                self._handoffs[index], self._tasks[task.task_id] = handoff, task
                self._handoff_commit_uncertain = True
                raise
            self._changed.notify_all()
            saved = copy.deepcopy(updated)
        await self._record("agent.task.updated", task_id=task.task_id, payload={
            "task_id": task.task_id, "status": updated_task.status,
            "has_result": updated_task.result is not None, "has_error": updated_task.error is not None,
        })
        if status != "accepted":
            await self._record(f"agent.handoff.{status}", task_id=task.task_id, payload={
                "handoff_id": saved.handoff_id, "task_id": task.task_id, "status": status,
                "source_agent": saved.source_agent, "target_agent": saved.target_agent,
            })
        return saved

    @asynccontextmanager
    async def handoff_projection(self, handoff_id: str, *, expected_lease_token: str | None = None):
        """Lock order: AgentTaskStore then RunRegistry. Never dispatch or reenter this store here."""
        async with self._lock:
            if self._handoff_commit_uncertain:
                raise HandoffOwnershipError("交接持久化结果不确定，不能投影结算状态。")
            handoff = self._find_handoff_unlocked(handoff_id)
            task = self._tasks.get(handoff.task_id) if handoff is not None else None
            if handoff is None or task is None:
                raise HandoffOwnershipError("交接或父任务不存在，无法投影。")
            if expected_lease_token is not None:
                self._require_handoff_owner_unlocked(handoff, task, expected_lease_token)
            yield copy.deepcopy(handoff), copy.deepcopy(task)

    async def update_handoff_status(
        self,
        handoff_id: str,
        status: AgentHandoffStatus,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> AgentHandoff:
        async with self._changed:
            handoff = self._find_handoff_unlocked(handoff_id)
            if handoff is None:
                raise KeyError(f"Agent handoff not found: {handoff_id}")
            allowed = HANDOFF_TRANSITIONS.get(handoff.status, set())
            if status not in allowed:
                raise ValueError(
                    f"Invalid handoff transition: {handoff.status} -> {status}"
                )
            handoff.status = status
            if metadata is not None:
                handoff.metadata.update(metadata)
            handoff.touch()
            self._persist_unlocked()
            self._changed.notify_all()
            updated = handoff
        await self._record(
            f"agent.handoff.{status}",
            task_id=updated.task_id,
            payload={
                "handoff_id": updated.handoff_id,
                "task_id": updated.task_id,
                "source_agent": updated.source_agent,
                "target_agent": updated.target_agent,
                "status": updated.status,
            },
        )
        return updated

    async def requeue_handoff(
        self,
        handoff_id: str,
        *,
        operator: str,
        reset_attempts: bool = True,
        repin_version: bool = True,
    ) -> AgentHandoff:
        now = time.time()
        async with self._changed:
            handoff = self._find_handoff_unlocked(handoff_id)
            if handoff is None:
                raise KeyError(f"Agent handoff not found: {handoff_id}")
            if handoff.status not in {"retry_wait", "dead_letter"}:
                raise ValueError(
                    f"Handoff cannot be requeued from {handoff.status}."
                )
            task = self._tasks.get(handoff.task_id)
            if task is None:
                raise KeyError(f"Agent task not found: {handoff.task_id}")
            if task.status in {"completed", "cancelled"}:
                raise ValueError("已完成或已取消的任务不能重新排队。")
            bound_handoff = task.metadata.get("handoff_id")
            if bound_handoff and bound_handoff != handoff_id:
                raise ValueError("任务当前绑定的交接与重排目标不一致。")
            original_task, original_handoff = task, handoff
            handoff_index = self._handoffs.index(handoff)
            task, handoff = copy.deepcopy(task), copy.deepcopy(handoff)
            task.status = "pending"
            task.result = None
            task.error = None
            task.metadata.update({"handoff_id": handoff_id, "requeued_by": operator})
            task.touch()
            handoff.status = "pending"
            for key in (
                "lease_owner",
                "lease_token",
                "lease_expires_at",
                "next_attempt_at",
                "last_error",
                "dead_lettered_at",
            ):
                handoff.metadata.pop(key, None)
            if reset_attempts:
                handoff.metadata["attempts"] = 0
            if repin_version and int(handoff.metadata.get("contract_version") or 1) != 2:
                for key in (
                    "target_xpert_id",
                    "target_xpert_slug",
                    "target_xpert_version",
                    "xpert_run_id",
                ):
                    handoff.metadata.pop(key, None)
            handoff.metadata.update(
                {
                    "requeued_by": operator,
                    "requeued_at": now,
                    "ready_for_execution": True,
                }
            )
            handoff.touch()
            # Task reopening and handoff readiness must share one persisted snapshot.
            self._tasks[task.task_id] = task
            self._handoffs[handoff_index] = handoff
            try:
                self._persist_unlocked()
            except Exception:
                self._tasks[task.task_id] = original_task
                self._handoffs[handoff_index] = original_handoff
                raise
            self._changed.notify_all()
            updated = copy.deepcopy(handoff)
        await self._record(
            "agent.handoff.requeued",
            task_id=updated.task_id,
            payload={
                "handoff_id": updated.handoff_id,
                "task_id": updated.task_id,
                "operator": operator,
            },
        )
        await self._record(
            "agent.task.updated",
            task_id=updated.task_id,
            payload={
                "task_id": updated.task_id,
                "status": "pending",
                "has_result": False,
                "has_error": False,
            },
        )
        return updated

    async def wait_for_handoff_terminal(
        self,
        handoff_id: str,
        *,
        timeout: float,
    ) -> AgentHandoff:
        deadline = asyncio.get_running_loop().time() + max(0.1, timeout)
        async with self._changed:
            while True:
                handoff = self._find_handoff_unlocked(handoff_id)
                if handoff is None:
                    raise KeyError(f"Agent handoff not found: {handoff_id}")
                if handoff.status in TERMINAL_HANDOFF_STATUSES:
                    return handoff
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Handoff timed out: {handoff_id}")
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError(f"Handoff timed out: {handoff_id}") from exc

    def _find_handoff_unlocked(self, handoff_id: str) -> AgentHandoff | None:
        return next(
            (item for item in self._handoffs if item.handoff_id == handoff_id),
            None,
        )

    def _commit_task_unlocked(self, task: AgentTask) -> None:
        original = self._tasks[task.task_id]
        self._tasks[task.task_id] = task
        try:
            self._persist_unlocked()
        except Exception:
            self._tasks[task.task_id] = original
            raise
        self._changed.notify_all()

    def _load_snapshot(self) -> None:
        assert self.storage_path is not None
        if not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            raw_tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
            raw_handoffs = (
                payload.get("handoffs", []) if isinstance(payload, dict) else []
            )
            self._tasks = {
                str(item["task_id"]): AgentTask(**item)
                for item in raw_tasks
                if isinstance(item, dict) and item.get("task_id")
            }
            self._handoffs = [
                AgentHandoff(**item)
                for item in raw_handoffs
                if isinstance(item, dict) and item.get("handoff_id")
            ]
            raw_index = payload.get("occurrence_index", {}) if isinstance(payload, dict) else {}
            self._occurrence_index = {
                str(key): str(value)
                for key, value in dict(raw_index or {}).items()
                if str(key) and str(value)
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Agent task storage is unreadable.") from exc

    def _persist_unlocked(self) -> None:
        if self._handoff_commit_uncertain:
            raise HandoffOwnershipError("交接持久化结果不确定，须重新加载并核对，禁止继续写入。")
        if self.storage_path is None or self.storage_dir is None:
            return
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "tasks": [asdict(task) for task in self._tasks.values()],
            "handoffs": [asdict(handoff) for handoff in self._handoffs],
            "occurrence_index": dict(self._occurrence_index),
        }
        temp_path = self.storage_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temp_path.replace(self.storage_path)

    @staticmethod
    def _metadata_float(metadata: dict[str, Any], key: str) -> float:
        try:
            return float(metadata.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    async def _record(
        self,
        event_type: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._event_store is None:
            return
        try:
            await self._event_store.record_event(
                event_type,
                task_id=task_id,
                payload=payload or {},
                severity="info",
            )
        except Exception:
            return
