from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .automation_store import (
    AutomationConflictError,
    AutomationDefinition,
    AutomationExecution,
    AutomationStore,
)
from .run_registry import RunRegistry


logger = logging.getLogger("modelmirror.automation_coordinator")


@dataclass(slots=True)
class AutomationTargetResult:
    output: str
    run_id: str
    workflow_task_id: str
    waiting_approval: bool = False
    waiting_client: bool = False
    wait_id: str | None = None


ExecuteAutomationTarget = Callable[
    [AutomationDefinition, AutomationExecution, str],
    Awaitable[AutomationTargetResult],
]


class AutomationCoordinator:
    """Materializes due occurrences and runs pinned Xperts with durable leases."""

    def __init__(
        self,
        store: AutomationStore,
        run_registry: RunRegistry,
        execute_target: ExecuteAutomationTarget,
        *,
        enabled: bool = True,
        poll_interval: float = 1.0,
        lease_seconds: float = 120.0,
        max_concurrency: int = 2,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.run_registry = run_registry
        self.execute_target = execute_target
        self.enabled = enabled
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_seconds = max(10.0, float(lease_seconds))
        self.max_concurrency = max(1, min(int(max_concurrency), 20))
        self.worker_id = worker_id or f"automation-{uuid.uuid4().hex[:8]}"
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._active: set[str] = set()

    def start(self) -> None:
        if not self.enabled or (self._loop_task and not self._loop_task.done()):
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="xpert-automation-coordinator"
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
        if not self.enabled:
            return 0
        self.store.materialize_due()
        slots = max(0, self.max_concurrency - len(self._active))
        if slots == 0:
            return 0
        candidates = [
            item
            for item in self.store.claimable(limit=slots * 2)
            if item.execution_id not in self._active
        ][:slots]
        if not candidates:
            return 0

        async def process(item: AutomationExecution) -> bool:
            self._active.add(item.execution_id)
            automation_run_id: str | None = None
            try:
                claimed = self.store.claim_execution(
                    item.execution_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                definition = self.store.require_definition(claimed.automation_id)
                automation_run = await self.run_registry.create_run(
                    "automation",
                    definition.name,
                    status="running",
                    source_id=definition.automation_id,
                    metadata={
                        "automation_id": definition.automation_id,
                        "automation_execution_id": claimed.execution_id,
                        "occurrence_key": claimed.occurrence_key,
                        "scheduled_at": claimed.scheduled_at,
                        "target_xpert_id": definition.target_xpert_id,
                        "target_xpert_version": definition.target_xpert_version,
                        "attempt": claimed.attempt,
                    },
                )
                automation_run_id = automation_run.run_id
                await self.run_registry.record_checkpoint(
                    automation_run.run_id,
                    event_type="automation.started",
                    title="Automation started",
                    summary=f"attempt={claimed.attempt}",
                    metadata={
                        "automation_id": definition.automation_id,
                        "execution_id": claimed.execution_id,
                        "scheduled_at": claimed.scheduled_at,
                    },
                )
                result = await asyncio.wait_for(
                    self.execute_target(definition, claimed, automation_run.run_id),
                    timeout=definition.budget.max_runtime_seconds,
                )
                if result.waiting_approval or result.waiting_client:
                    waiting_status = (
                        "waiting_approval" if result.waiting_approval else "waiting_client"
                    )
                    self.store.mark_waiting(
                        claimed.execution_id,
                        status=waiting_status,
                        run_id=result.run_id,
                        workflow_task_id=result.workflow_task_id,
                        wait_id=result.wait_id,
                    )
                    await self.run_registry.update_run(
                        automation_run.run_id,
                        status="waiting",
                        metadata={
                            "wait_kind": "approval" if result.waiting_approval else "client_tool",
                            "wait_id": result.wait_id,
                            "workflow_task_id": result.workflow_task_id,
                            "target_run_id": result.run_id,
                        },
                    )
                    await self.run_registry.record_checkpoint(
                        automation_run.run_id,
                        event_type=f"automation.{waiting_status}",
                        title="Automation waiting",
                        summary=f"wait_id={result.wait_id or ''}",
                        severity="warning",
                        metadata={"wait_id": result.wait_id, "workflow_task_id": result.workflow_task_id},
                    )
                else:
                    self.store.complete_execution(
                        claimed.execution_id,
                        result=result.output,
                        run_id=result.run_id,
                        workflow_task_id=result.workflow_task_id,
                    )
                    await self.run_registry.update_run(
                        automation_run.run_id,
                        status="completed",
                        metadata={
                            "result_length": len(result.output),
                            "workflow_task_id": result.workflow_task_id,
                            "target_run_id": result.run_id,
                        },
                    )
                    await self.run_registry.record_checkpoint(
                        automation_run.run_id,
                        event_type="automation.completed",
                        title="Automation completed",
                        summary=f"result_length={len(result.output)}",
                        metadata={"workflow_task_id": result.workflow_task_id},
                    )
                return True
            except AutomationConflictError:
                return False
            except asyncio.TimeoutError:
                error = "Automation exceeded its runtime budget."
                failed = self.store.fail_execution(item.execution_id, error=error)
                await self._record_failure(automation_run_id, failed, error)
                return False
            except Exception as exc:
                logger.exception("Automation execution failed execution_id=%s", item.execution_id)
                permanent = bool(getattr(exc, "permanent", False))
                failed = self.store.fail_execution(
                    item.execution_id, error=str(exc), permanent=permanent
                )
                await self._record_failure(automation_run_id, failed, str(exc))
                return False
            finally:
                self._active.discard(item.execution_id)

        results = await asyncio.gather(*(process(item) for item in candidates))
        return sum(1 for value in results if value)

    async def complete_waiting(
        self,
        execution_id: str,
        *,
        result: str,
        target_run_id: str,
        workflow_task_id: str,
    ) -> None:
        execution = self.store.complete_execution(
            execution_id,
            result=result,
            run_id=target_run_id,
            workflow_task_id=workflow_task_id,
        )
        runs = await self.run_registry.list_runs(
            run_type="automation",
            source_id=execution.automation_id,
            limit=100,
        )
        run = next(
            (
                item
                for item in runs
                if item.metadata.get("automation_execution_id") == execution_id
            ),
            None,
        )
        if run is not None:
            await self.run_registry.update_run(
                run.run_id,
                status="completed",
                metadata={
                    "result_length": len(result),
                    "workflow_task_id": workflow_task_id,
                    "target_run_id": target_run_id,
                },
            )
            await self.run_registry.record_checkpoint(
                run.run_id,
                event_type="automation.resumed.completed",
                title="Automation continuation completed",
                summary=f"result_length={len(result)}",
            )

    async def status(self) -> dict[str, Any]:
        definitions = self.store.list_definitions(limit=1000)
        executions = self.store.list_executions(limit=5000)
        return {
            "enabled": self.enabled,
            "running": bool(self._loop_task and not self._loop_task.done()),
            "worker_id": self.worker_id,
            "scheduled_definitions": sum(1 for item in definitions if item.status == "scheduled"),
            "pending_executions": sum(1 for item in executions if item.status == "pending"),
            "waiting_executions": sum(
                1
                for item in executions
                if item.status in {"waiting_approval", "waiting_client"}
            ),
            "dead_letter_executions": sum(1 for item in executions if item.status == "dead_letter"),
            "active_executions": len(self._active),
            "max_concurrency": self.max_concurrency,
        }

    async def _record_failure(
        self,
        run_id: str | None,
        execution: AutomationExecution,
        error: str,
    ) -> None:
        if run_id is None:
            return
        status = "failed" if execution.status == "dead_letter" else "pending"
        await self.run_registry.update_run(
            run_id,
            status="failed",
            error=str(error)[:1000],
            metadata={
                "execution_status": execution.status,
                "retry_scheduled": execution.status == "pending",
                "attempt": execution.attempt,
            },
        )
        await self.run_registry.record_checkpoint(
            run_id,
            event_type=(
                "automation.dead_letter"
                if execution.status == "dead_letter"
                else "automation.retry_scheduled"
            ),
            title=(
                "Automation moved to dead letter"
                if execution.status == "dead_letter"
                else "Automation retry scheduled"
            ),
            summary=str(error)[:500],
            severity="error" if status == "failed" else "warning",
            metadata={"attempt": execution.attempt},
        )

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Automation coordinator loop failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass
