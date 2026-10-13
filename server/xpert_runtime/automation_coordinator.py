from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .automation_store import (
    AutomationConflictError,
    AutomationDefinition,
    AutomationExecution,
    AutomationStore,
)
from .run_registry import RunRegistry
from .execution_store import WorkflowExecutionConflictError
from .workflow_outcomes import WorkflowOutcomeError

if TYPE_CHECKING:
    from .execution_store import WorkflowExecution, WorkflowExecutionStore


logger = logging.getLogger("modelmirror.automation_coordinator")


@dataclass(slots=True)
class AutomationTargetResult:
    output: str
    run_id: str
    workflow_task_id: str
    waiting_approval: bool = False
    waiting_client: bool = False
    wait_id: str | None = None
    workflow_event: dict[str, Any] | None = None


class AutomationOutcomeError(RuntimeError):
    permanent = True


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
        cancel_children: Callable[[AutomationExecution], Awaitable[None]] | None = None,
        workflow_executions: WorkflowExecutionStore | None = None,
        is_wait_expired: Callable[[WorkflowExecution], bool] | None = None,
    ) -> None:
        self.store = store
        self.run_registry = run_registry
        self.execute_target = execute_target
        self.cancel_children = cancel_children
        self.workflow_executions = workflow_executions
        self.is_wait_expired = is_wait_expired
        self.enabled = enabled
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_seconds = max(10.0, float(lease_seconds))
        self.max_concurrency = max(1, min(int(max_concurrency), 20))
        self.worker_id = worker_id or f"automation-{uuid.uuid4().hex[:8]}"
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._active: set[str] = set()
        self._targets: dict[str, asyncio.Task[AutomationTargetResult]] = {}
        self._projection_lock = asyncio.Lock()

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
        for expired in self.store.runtime_expiry_candidates():
            try:
                await self._reconcile_runtime_expiry(expired.execution_id)
            except Exception as exc:
                logger.warning("Automation runtime expiry projection pending: %s", type(exc).__name__)
        for uncertain in self.store.reconcile_uncertain_dispatches():
            try:
                runs = await self.run_registry.list_runs(run_type="automation", source_id=uncertain.automation_id, limit=sys.maxsize)
                for run in runs:
                    if (run.metadata.get("automation_execution_id") == uncertain.execution_id
                            and run.metadata.get("attempt") == uncertain.attempt):
                        await self._record_failure(run.run_id, uncertain, uncertain.error or "AUTOMATION_DISPATCH_UNCERTAIN")
            except Exception as exc:
                logger.warning("Automation uncertain projection pending: %s", type(exc).__name__)
        for cancelled in self.store.cancelled_executions():
            try:
                await self._reconcile_cancelled(cancelled.execution_id)
            except Exception as exc:
                logger.warning("Automation cancellation projection pending: %s", type(exc).__name__)
        for projected in self.store.workflow_projection_executions():
            try:
                if projected.status in {"waiting_approval", "waiting_client"} and self.workflow_executions is not None:
                    snapshot = self.workflow_executions.get(projected.workflow_task_id or "")
                    if snapshot is not None:
                        await self.expire_workflow_wait(snapshot, wait_kind=projected.wait_kind, wait_id=projected.wait_id)
                async with self._projection_lock:
                    current = self.store.get_execution(projected.execution_id)
                    if current == projected and isinstance(current.metadata.get("workflow_projection"), dict):
                        await self._project_verified_execution(current)
            except Exception as exc:
                logger.warning("Automation outcome projection pending: %s", type(exc).__name__)
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
            lease_token: str | None = None
            try:
                claimed = self.store.claim_execution(
                    item.execution_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                lease_token = claimed.lease_token
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
                async def invoke_target() -> AutomationTargetResult:
                    dispatched = self.store.mark_target_dispatch(
                        claimed.execution_id, lease_token=lease_token, run_id=automation_run.run_id,
                    )
                    return await self.execute_target(definition, dispatched, automation_run.run_id)

                async def dispatch_target() -> AutomationTargetResult:
                    target_task = asyncio.create_task(invoke_target())
                    self._targets[claimed.execution_id] = target_task
                    try:
                        while True:
                            done, _ = await asyncio.wait(
                                {target_task}, timeout=self.lease_seconds / 3,
                            )
                            if done:
                                return target_task.result()
                            self.store.renew_execution(
                                claimed.execution_id, lease_token=lease_token,
                                lease_seconds=self.lease_seconds,
                            )
                    finally:
                        if not target_task.done():
                            target_task.cancel()
                        await asyncio.gather(target_task, return_exceptions=True)
                        if self._targets.get(claimed.execution_id) is target_task:
                            self._targets.pop(claimed.execution_id, None)

                remaining = self.store.remaining_runtime(claimed.execution_id)
                result = await asyncio.wait_for(
                    dispatch_target(),
                    timeout=definition.budget.max_runtime_seconds if remaining is None else remaining,
                )
                await self._commit_result(claimed.execution_id, lease_token, automation_run.run_id, result)
                return True
            except asyncio.CancelledError:
                current = self.store.get_execution(item.execution_id)
                if current is not None and (current.status == "cancelled" or current.runtime_expired_at is not None):
                    return False
                raise
            except AutomationConflictError:
                return False
            except asyncio.TimeoutError:
                error = "AUTOMATION_RUNTIME_BUDGET_EXCEEDED"
                try:
                    failed = self.store.expire_runtime_budget(item.execution_id)
                    if failed.runtime_expired_at is None:
                        failed = self.store.fail_execution(
                            item.execution_id, error=error, lease_token=lease_token,
                        )
                except AutomationConflictError:
                    return False
                await self._record_failure(automation_run_id, failed, failed.error or error)
                return False
            except Exception as exc:
                logger.warning("Automation execution failed execution_id=%s type=%s", item.execution_id, type(exc).__name__)
                permanent = bool(getattr(exc, "permanent", False))
                if not lease_token:
                    return False
                try:
                    failed = self.store.fail_execution(
                        item.execution_id, error="AUTOMATION_PRE_DISPATCH_FAILED", permanent=permanent,
                        lease_token=lease_token,
                    )
                except AutomationConflictError:
                    return False
                await self._record_failure(automation_run_id, failed, failed.error or "AUTOMATION_PRE_DISPATCH_FAILED")
                return False
            finally:
                self._active.discard(item.execution_id)
                await self._reconcile_runtime_expiry(item.execution_id)
                await self._reconcile_cancelled(item.execution_id)

        results = await asyncio.gather(*(process(item) for item in candidates))
        return sum(1 for value in results if value)

    async def _reconcile_runtime_expiry(self, execution_id: str) -> None:
        async with self._projection_lock:
            execution = self.store.expire_runtime_budget(execution_id)
            if execution.runtime_expired_at is None:
                return
            target = self._targets.get(execution_id)
            if target is not None and not target.done() and not target.cancelling():
                target.cancel()
            projection_error: Exception | None = None
            if self.cancel_children is not None:
                try:
                    await self.cancel_children(execution)
                except Exception as exc:
                    projection_error = exc
            runs = await self.run_registry.list_runs(
                run_type="automation", source_id=execution.automation_id, limit=sys.maxsize,
            )
            for run in runs:
                if (run.metadata.get("automation_execution_id") == execution_id
                        and run.metadata.get("attempt") == execution.attempt):
                    await self._record_current_failure(run.run_id, execution, execution.error or "AUTOMATION_RUNTIME_BUDGET_EXCEEDED")
            if projection_error is not None:
                raise projection_error

    async def cancel_execution(self, execution_id: str) -> AutomationExecution:
        async with self._projection_lock:
            item = self.store.cancel_execution(execution_id)
            if item.status == "cancelled":
                await self._project_cancelled(item)
            return item

    async def retry_execution(self, execution_id: str, *, reset_attempts: bool = False) -> AutomationExecution:
        async with self._projection_lock:
            target = self._targets.get(execution_id)
            if target is not None and not target.done():
                raise AutomationConflictError("旧执行尚未退出，不能开始人工重试。")
            current = self.store.get_execution(execution_id)
            if current is not None and current.status == "cancelled":
                await self._project_cancelled(current)
            item = self.store.retry_execution(execution_id, reset_attempts=reset_attempts)
            self.wake()
            return item

    async def _reconcile_cancelled(self, execution_id: str) -> None:
        async with self._projection_lock:
            item = self.store.get_execution(execution_id)
            if item is not None and item.status == "cancelled":
                await self._project_cancelled(item)

    async def _project_cancelled(self, execution: AutomationExecution) -> None:
        target = self._targets.get(execution.execution_id)
        if target is not None and not target.done() and not target.cancelling():
            target.cancel()
        projection_error: Exception | None = None
        if self.cancel_children is not None:
            try:
                await self.cancel_children(execution)
            except Exception as exc:
                projection_error = exc
        runs = await self.run_registry.list_runs(
            run_type="automation", source_id=execution.automation_id, limit=sys.maxsize,
        )
        for run in runs:
            if (
                run.metadata.get("automation_execution_id") == execution.execution_id
                and run.status not in {"completed", "failed", "cancelled"}
            ):
                try:
                    await self.run_registry.cancel_run(run.run_id, reason="AUTOMATION_CANCELLED")
                except Exception as exc:
                    projection_error = projection_error or exc
        if projection_error is not None:
            raise projection_error

    async def _commit_result(
        self, execution_id: str, lease_token: str, run_id: str, result: AutomationTargetResult,
    ) -> None:
        async with self._projection_lock:
            if result.workflow_event is not None:
                if self.workflow_executions is None:
                    raise AutomationOutcomeError("自动化缺少持久子执行核验器。")
                if (
                    not result.workflow_task_id or not result.run_id
                    or result.workflow_event.get("task_id") != result.workflow_task_id
                    or result.workflow_event.get("run_id") != result.run_id
                    or self.workflow_executions.get(result.workflow_task_id) is None
                ):
                    raise AutomationOutcomeError("自动化结果缺少可核验的子执行身份。")
                snapshot = self.workflow_executions.snapshot(result.workflow_task_id)
                parent = self.store.get_execution(execution_id)
                if (
                    parent is None
                    or snapshot.runtime_metadata.get("automation_execution_id") != execution_id
                    or snapshot.runtime_metadata.get("automation_id") != parent.automation_id
                    or snapshot.runtime_metadata.get("automation_occurrence_key") != parent.occurrence_key
                ):
                    raise AutomationOutcomeError("自动化初次结果指向其他执行。")
                try:
                    projected = self.store.project_workflow_outcome(
                        self.workflow_executions, snapshot, result.workflow_event, lease_token=lease_token,
                    )
                except WorkflowOutcomeError as exc:
                    raise AutomationOutcomeError("自动化结果未通过持久状态核验。") from exc
                await self._project_verified_execution(projected)
                return
            waiting = result.waiting_approval or result.waiting_client
            waiting_status = "waiting_approval" if result.waiting_approval else "waiting_client"
            metadata = {"workflow_task_id": result.workflow_task_id, "target_run_id": result.run_id}
            if waiting:
                self.store.mark_waiting(
                    execution_id, status=waiting_status, run_id=result.run_id,
                    workflow_task_id=result.workflow_task_id, wait_id=result.wait_id,
                    lease_token=lease_token,
                )
                metadata.update({"wait_kind": "approval" if result.waiting_approval else "client_tool", "wait_id": result.wait_id})
            else:
                self.store.complete_execution(
                    execution_id, result=result.output, run_id=result.run_id,
                    workflow_task_id=result.workflow_task_id, lease_token=lease_token,
                )
                metadata["result_length"] = len(result.output)
            await self.run_registry.update_run(run_id, status="waiting" if waiting else "completed", metadata=metadata)
            await self.run_registry.record_checkpoint(
                run_id, event_type=f"automation.{waiting_status}" if waiting else "automation.completed",
                title="自动化等待中" if waiting else "自动化已完成",
                summary=f"wait_id={result.wait_id or ''}" if waiting else f"result_length={len(result.output)}",
                severity="warning" if waiting else "info",
                metadata={"workflow_task_id": result.workflow_task_id, **({"wait_id": result.wait_id} if waiting else {})},
            )

    async def project_workflow_outcome(
        self, executions: WorkflowExecutionStore, snapshot: WorkflowExecution, event: dict[str, Any],
    ) -> None:
        async with self._projection_lock:
            projected = self.store.project_workflow_outcome(executions, snapshot, event)
            await self._project_verified_execution(projected)

    async def expire_workflow_wait(
        self, snapshot: WorkflowExecution, *, wait_kind: str | None, wait_id: str | None,
    ) -> bool:
        async with self._projection_lock:
            if (self.workflow_executions is None or self.is_wait_expired is None
                    or snapshot.status != "waiting" or snapshot.wait_kind != wait_kind
                    or snapshot.wait_id != wait_id or not self.is_wait_expired(snapshot)):
                return False
            try:
                projected = self.store.expire_workflow_wait(self.workflow_executions, snapshot)
            except (AutomationConflictError, WorkflowExecutionConflictError):
                return False
            await self._project_verified_execution(projected)
            return True

    async def _project_verified_execution(self, execution: AutomationExecution) -> None:
        if execution.status == "cancelled":
            await self._project_cancelled(execution)
        status = "waiting" if execution.status.startswith("waiting_") else "failed" if execution.status == "dead_letter" else execution.status
        receipt = execution.metadata["workflow_projection"]
        runs = await self.run_registry.list_runs(run_type="automation", source_id=execution.automation_id, limit=sys.maxsize)
        for run in runs:
            if (
                run.metadata.get("automation_execution_id") != execution.execution_id
                or run.metadata.get("attempt", execution.attempt) != execution.attempt
                or run.status in {"completed", "failed", "cancelled"} and run.status != status
            ):
                continue
            if run.metadata.get("workflow_projection") != receipt or run.status != status:
                await self.run_registry.update_run(run.run_id, status=status, error=execution.error, metadata={
                    "workflow_projection": receipt, "workflow_task_id": execution.workflow_task_id,
                    "target_run_id": execution.run_id, "wait_kind": execution.wait_kind, "wait_id": execution.wait_id,
                    "result_length": len(execution.result or ""),
                })
            # Registry state and its checkpoint are separate writes; reconcile both.
            checkpoints = await self.run_registry.list_checkpoints(run.run_id, limit=sys.maxsize)
            if not any(item.event_type == "automation.outcome_projected" and item.metadata == receipt for item in checkpoints):
                await self.run_registry.record_checkpoint(run.run_id, event_type="automation.outcome_projected",
                    title="自动化子执行结果已核验", summary=f"status={execution.status}", metadata=receipt)

    async def complete_waiting(
        self,
        execution_id: str,
        *,
        result: str,
        target_run_id: str,
        workflow_task_id: str,
    ) -> None:
        async with self._projection_lock:
            await self._complete_waiting(execution_id, result=result, target_run_id=target_run_id, workflow_task_id=workflow_task_id)

    async def _complete_waiting(
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
        async with self._projection_lock:
            current = self.store.get_execution(execution.execution_id)
            if current != execution:
                if current is not None and current.status == "cancelled":
                    await self._project_cancelled(current)
                return
            await self._record_current_failure(run_id, execution, error)

    async def _record_current_failure(
        self,
        run_id: str | None,
        execution: AutomationExecution,
        error: str,
    ) -> None:
        if run_id is None:
            return
        status = "failed" if execution.status == "dead_letter" else "pending"
        receipt = {"execution_id": execution.execution_id, "attempt": execution.attempt,
                   "status": execution.status, "error": str(error)[:1000]}
        run = await self.run_registry.get_run(run_id)
        if run.status in {"completed", "cancelled"}:
            return
        if run.status != "failed" or run.metadata.get("failure_projection") != receipt:
            await self.run_registry.update_run(
                run_id, status="failed", error=str(error)[:1000], metadata={
                    "execution_status": execution.status, "retry_scheduled": execution.status == "pending",
                    "attempt": execution.attempt, "failure_projection": receipt,
                },
            )
        event_type = "automation.dead_letter" if execution.status == "dead_letter" else "automation.retry_scheduled"
        checkpoints = await self.run_registry.list_checkpoints(run_id, limit=sys.maxsize)
        if not any(item.event_type == event_type and item.metadata == receipt for item in checkpoints):
            await self.run_registry.record_checkpoint(
                run_id, event_type=event_type,
                title="自动化进入死信，需核对原执行" if execution.status == "dead_letter" else "自动化派发前失败，已安排重试",
                summary=str(error)[:500], severity="error" if status == "failed" else "warning", metadata=receipt,
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
