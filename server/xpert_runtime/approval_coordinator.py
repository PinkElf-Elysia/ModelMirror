from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .approval_store import RuntimeApprovalRequest, RuntimeApprovalStore
from .execution_store import (
    WorkflowExecution,
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)


logger = logging.getLogger("modelmirror.approval_coordinator")
ResumeExecution = Callable[[WorkflowExecution, RuntimeApprovalRequest], Awaitable[None]]
ExpireExecution = Callable[[WorkflowExecution, RuntimeApprovalRequest], Awaitable[None]]


class ApprovalCoordinator:
    """Resumes decided workflow continuations and escalates expired approvals."""

    def __init__(
        self,
        approvals: RuntimeApprovalStore,
        executions: WorkflowExecutionStore,
        resume_execution: ResumeExecution,
        *,
        expire_execution: ExpireExecution | None = None,
        enabled: bool = True,
        poll_interval: float = 0.5,
        lease_seconds: float = 60.0,
        worker_id: str | None = None,
    ) -> None:
        self.approvals = approvals
        self.executions = executions
        self.resume_execution = resume_execution
        self.expire_execution = expire_execution
        self.enabled = enabled
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_seconds = max(5.0, float(lease_seconds))
        self.worker_id = worker_id or f"approval-{uuid.uuid4().hex[:8]}"
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._active: set[str] = set()

    def start(self) -> None:
        if not self.enabled or (self._loop_task and not self._loop_task.done()):
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="runtime-approval-coordinator"
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
        expired = self.approvals.expire_due()
        for approval in expired:
            execution = self.executions.get(approval.task_id)
            if execution is not None and self.expire_execution is not None:
                await self.expire_execution(execution, approval)

        ready: list[tuple[WorkflowExecution, RuntimeApprovalRequest]] = []
        for execution in self.executions.list_items(limit=1000):
            if (
                execution.status != "waiting"
                or execution.wait_kind != "approval"
                or not execution.wait_id
            ):
                continue
            approval = self.approvals.get(execution.wait_id)
            if approval is not None and approval.status == "decided":
                try:
                    self.executions.mark_ready(
                        execution.task_id, approval_id=approval.approval_id
                    )
                except WorkflowExecutionConflictError:
                    continue
        for execution in self.executions.list_items(status="ready", limit=1000):
            if (
                execution.task_id in self._active
                or execution.wait_kind != "approval"
                or not execution.wait_id
            ):
                continue
            approval = self.approvals.get(execution.wait_id)
            if approval is not None and approval.status == "decided":
                ready.append((execution, approval))

        async def resume(
            execution: WorkflowExecution,
            approval: RuntimeApprovalRequest,
        ) -> bool:
            self._active.add(execution.task_id)
            try:
                claimed = self.executions.claim(
                    execution.task_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                await self.resume_execution(claimed, approval)
                return True
            except WorkflowExecutionConflictError:
                return False
            except Exception as exc:
                logger.exception(
                    "Runtime approval resume failed task_id=%s",
                    execution.task_id,
                )
                self.executions.fail(execution.task_id, error=str(exc))
                return False
            finally:
                self._active.discard(execution.task_id)

        if ready:
            results = await asyncio.gather(*(resume(*item) for item in ready[:20]))
            return sum(1 for result in results if result)
        return 0

    async def status(self) -> dict[str, Any]:
        approvals = self.approvals.list_requests(limit=1000)
        executions = self.executions.list_items(limit=1000)
        return {
            "enabled": self.enabled,
            "running": bool(self._loop_task and not self._loop_task.done()),
            "worker_id": self.worker_id,
            "pending_approvals": sum(1 for item in approvals if item.status == "pending"),
            "expired_approvals": sum(1 for item in approvals if item.status == "expired"),
            "waiting_executions": sum(1 for item in executions if item.status == "waiting"),
            "active_executions": len(self._active),
        }

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Runtime approval coordinator loop failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass
