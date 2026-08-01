from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from .models import RuntimeEvent, RuntimeTask, TaskStatus


class RuntimeEventStore:
    """In-memory task and event store for the first Xpert-aligned runtime slice."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, RuntimeTask] = {}
        self._events: list[RuntimeEvent] = []
        self._dead_letters: list[RuntimeTask] = []

    async def create_task(
        self,
        task_type: str,
        payload: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        ttl_seconds: float | None = None,
        max_attempts: int = 3,
    ) -> RuntimeTask:
        now = time.time()
        task = RuntimeTask(
            id=task_id or str(uuid.uuid4()),
            type=task_type,
            payload=payload or {},
            max_attempts=max_attempts,
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
            deadline_at=now + ttl_seconds if ttl_seconds else None,
        )
        async with self._lock:
            self._tasks[task.id] = task
            self._events.append(
                RuntimeEvent(
                    id=str(uuid.uuid4()),
                    type="task.created",
                    task_id=task.id,
                    trace_id=trace_id,
                    payload={"task_type": task_type},
                )
            )
        return task

    async def get_task(self, task_id: str) -> RuntimeTask | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(self, status: TaskStatus | None = None) -> list[RuntimeTask]:
        async with self._lock:
            tasks = list(self._tasks.values())
        if status is None:
            return tasks
        return [task for task in tasks if task.status == status]

    async def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        attempts: int | None = None,
    ) -> RuntimeTask:
        async with self._lock:
            task = self._require_task(task_id)
            if status is not None:
                task.status = status
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
            if attempts is not None:
                task.attempts = attempts
            task.touch()
            self._events.append(
                RuntimeEvent(
                    id=str(uuid.uuid4()),
                    type="task.updated",
                    task_id=task.id,
                    trace_id=task.trace_id,
                    payload={"status": task.status, "error": task.error},
                )
            )
            return task

    async def cancel_task(self, task_id: str, reason: str = "cancelled") -> RuntimeTask:
        return await self.update_task(task_id, status="cancelled", error=reason)

    async def mark_dead(self, task_id: str, reason: str) -> RuntimeTask:
        async with self._lock:
            task = self._require_task(task_id)
            task.status = "dead"
            task.error = reason
            task.touch()
            if task not in self._dead_letters:
                self._dead_letters.append(task)
            self._events.append(
                RuntimeEvent(
                    id=str(uuid.uuid4()),
                    type="task.dead",
                    task_id=task.id,
                    trace_id=task.trace_id,
                    severity="error",
                    payload={"reason": reason},
                )
            )
            return task

    async def record_event(
        self,
        event_type: str,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            id=str(uuid.uuid4()),
            type=event_type,
            task_id=task_id,
            trace_id=trace_id,
            payload=payload or {},
            severity=severity,  # type: ignore[arg-type]
        )
        async with self._lock:
            self._events.append(event)
        return event

    async def list_events(self, task_id: str | None = None) -> list[RuntimeEvent]:
        async with self._lock:
            events = list(self._events)
        if task_id is None:
            return events
        return [event for event in events if event.task_id == task_id]

    async def list_dead_letters(self) -> list[RuntimeTask]:
        async with self._lock:
            return list(self._dead_letters)

    async def cleanup_expired(self, *, now: float | None = None) -> list[RuntimeTask]:
        current = now or time.time()
        expired: list[RuntimeTask] = []
        async with self._lock:
            for task in self._tasks.values():
                if task.deadline_at is None:
                    continue
                if task.status not in ("pending", "running"):
                    continue
                if task.deadline_at > current:
                    continue
                task.status = "dead"
                task.error = "task expired"
                task.touch()
                expired.append(task)
                if task not in self._dead_letters:
                    self._dead_letters.append(task)
                self._events.append(
                    RuntimeEvent(
                        id=str(uuid.uuid4()),
                        type="task.expired",
                        task_id=task.id,
                        trace_id=task.trace_id,
                        severity="warning",
                        payload={"deadline_at": task.deadline_at},
                    )
                )
        return expired

    def _require_task(self, task_id: str) -> RuntimeTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Runtime task not found: {task_id}")
        return task
