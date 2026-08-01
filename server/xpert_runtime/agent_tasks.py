from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .events import RuntimeEventStore

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
AUTO_XPERT_TARGET_PREFIX = "xpert:"


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
        self._event_store = event_store
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
    ) -> AgentTask:
        task = AgentTask(
            task_id=str(uuid.uuid4()),
            title=title,
            input=input_text,
            source_agent=source_agent,
            assigned_agent=assigned_agent,
            metadata=dict(metadata or {}),
        )
        async with self._changed:
            self._tasks[task.task_id] = task
            self._persist_unlocked()
            self._changed.notify_all()
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
            return self._tasks.get(task_id)

    async def list_tasks(
        self,
        *,
        status: AgentTaskStatus | None = None,
        limit: int = 50,
    ) -> list[AgentTask]:
        async with self._lock:
            tasks = list(self._tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        tasks.sort(key=lambda task: task.created_at, reverse=True)
        return tasks[: max(1, limit)]

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
                task.metadata.update(metadata)
            task.touch()
            self._persist_unlocked()
            self._changed.notify_all()
            updated = task
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
        task = await self.update_task(task_id, status="cancelled", error=reason)
        await self._record(
            "agent.task.cancelled",
            task_id=task_id,
            payload={"task_id": task_id, "reason": reason},
        )
        return task

    async def create_handoff(
        self,
        task_id: str,
        source_agent: str,
        target_agent: str,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> AgentHandoff:
        handoff = AgentHandoff(
            handoff_id=str(uuid.uuid4()),
            task_id=task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            reason=reason,
            metadata=dict(metadata or {}),
        )
        async with self._changed:
            if task_id not in self._tasks:
                raise KeyError(f"Agent task not found: {task_id}")
            self._handoffs.append(handoff)
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
            handoffs = list(self._handoffs)
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
            handoff = self._find_handoff_unlocked(handoff_id)
            if handoff is None:
                raise KeyError(f"Agent handoff not found: {handoff_id}")
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
            claimed = handoff
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
            if repin_version:
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
            self._persist_unlocked()
            self._changed.notify_all()
            updated = handoff
        await self._record(
            "agent.handoff.requeued",
            task_id=updated.task_id,
            payload={
                "handoff_id": updated.handoff_id,
                "task_id": updated.task_id,
                "operator": operator,
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
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Agent task storage is unreadable.") from exc

    def _persist_unlocked(self) -> None:
        if self.storage_path is None or self.storage_dir is None:
            return
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "tasks": [asdict(task) for task in self._tasks.values()],
            "handoffs": [asdict(handoff) for handoff in self._handoffs],
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
