from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .agent_tasks import AgentHandoff, AgentTaskStore, TERMINAL_HANDOFF_STATUSES
from .execution_store import (
    WorkflowExecution,
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)


logger = logging.getLogger("modelmirror.workflow_handoff_coordinator")
ResumeHandoffExecution = Callable[[WorkflowExecution, AgentHandoff], Awaitable[None]]
ExpireHandoffExecution = Callable[[WorkflowExecution, AgentHandoff], Awaitable[None]]


class WorkflowHandoffCoordinator:
    """Resumes durable workflow continuations after a handoff reaches a terminal state."""

    def __init__(
        self,
        handoffs: AgentTaskStore,
        executions: WorkflowExecutionStore,
        resume_execution: ResumeHandoffExecution,
        *,
        expire_execution: ExpireHandoffExecution | None = None,
        enabled: bool = True,
        poll_interval: float = 0.5,
        lease_seconds: float = 60.0,
        worker_id: str | None = None,
    ) -> None:
        self.handoffs = handoffs
        self.executions = executions
        self.resume_execution = resume_execution
        self.expire_execution = expire_execution
        self.enabled = enabled
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_seconds = max(5.0, float(lease_seconds))
        self.worker_id = worker_id or f"workflow-handoff-{uuid.uuid4().hex[:8]}"
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._active: set[str] = set()

    def start(self) -> None:
        if not self.enabled or (self._loop_task and not self._loop_task.done()):
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="workflow-handoff-coordinator"
        )

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        task = self._loop_task
        self._loop_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def wake(self) -> None:
        self._wake.set()

    async def run_once(self) -> int:
        now = time.time()
        for execution in self.executions.list_items(limit=1000):
            if (
                execution.status != "waiting"
                or execution.wait_kind != "agent_handoff"
                or not execution.wait_id
            ):
                continue
            handoff = await self.handoffs.get_handoff(execution.wait_id)
            if handoff is None:
                self.executions.fail(
                    execution.task_id, error="HANDOFF_RECEIPT_INVALID"
                )
                continue
            if (
                execution.resume_at is not None
                and execution.resume_at <= now
                and handoff.status not in TERMINAL_HANDOFF_STATUSES
                and self.expire_execution is not None
            ):
                await self.expire_execution(execution, handoff)
                handoff = await self.handoffs.get_handoff(execution.wait_id)
                if handoff is None:
                    continue
            if handoff.status not in TERMINAL_HANDOFF_STATUSES:
                continue
            try:
                self.executions.mark_ready(
                    execution.task_id,
                    wait_kind="agent_handoff",
                    wait_id=handoff.handoff_id,
                )
            except WorkflowExecutionConflictError:
                continue

        ready: list[tuple[WorkflowExecution, AgentHandoff]] = []
        for execution in self.executions.list_items(status="ready", limit=1000):
            if (
                execution.task_id in self._active
                or execution.wait_kind != "agent_handoff"
                or not execution.wait_id
            ):
                continue
            handoff = await self.handoffs.get_handoff(execution.wait_id)
            if handoff is not None and handoff.status in TERMINAL_HANDOFF_STATUSES:
                ready.append((execution, handoff))

        async def resume(
            execution: WorkflowExecution, handoff: AgentHandoff
        ) -> bool:
            self._active.add(execution.task_id)
            try:
                claimed = self.executions.claim(
                    execution.task_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                await self.resume_execution(claimed, handoff)
                return True
            except WorkflowExecutionConflictError:
                return False
            except Exception as exc:
                logger.exception(
                    "Workflow handoff continuation failed task_id=%s",
                    execution.task_id,
                )
                self.executions.fail(
                    execution.task_id,
                    error="HANDOFF_RESUME_FAILED",
                )
                return False
            finally:
                self._active.discard(execution.task_id)

        if not ready:
            return 0
        results = await asyncio.gather(*(resume(*item) for item in ready[:20]))
        return sum(1 for result in results if result)

    async def status(self) -> dict[str, Any]:
        executions = self.executions.list_items(limit=1000)
        return {
            "enabled": self.enabled,
            "running": bool(self._loop_task and not self._loop_task.done()),
            "worker_id": self.worker_id,
            "waiting_executions": sum(
                1
                for item in executions
                if item.status == "waiting" and item.wait_kind == "agent_handoff"
            ),
            "active_executions": len(self._active),
        }

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Workflow handoff coordinator loop failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass
