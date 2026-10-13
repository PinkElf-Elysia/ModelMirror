from __future__ import annotations

import asyncio
import copy
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .agent_tasks import AgentHandoff, AgentTask, AgentTaskStore, HandoffOwnershipError
from .run_registry import RunRegistry, RuntimeRun


logger = logging.getLogger("modelmirror.handoff_executor")


class HandoffExecutorError(Exception):
    """Base error raised by automatic Xpert handoff execution."""


class HandoffPermanentError(HandoffExecutorError):
    """An execution error that should move directly to the dead-letter queue."""


class HandoffBusyError(HandoffExecutorError):
    """Raised when another worker currently owns the handoff lease."""


@dataclass(slots=True)
class HandoffExecutionResult:
    output: str
    run_id: str
    xpert_id: str
    xpert_slug: str
    xpert_version: int
    waiting_approval: bool = False
    waiting_client: bool = False
    approval_id: str | None = None
    client_request_id: str | None = None
    task_id: str | None = None


ExecuteHandoffTarget = Callable[
    [AgentHandoff, AgentTask, str | None],
    Awaitable[HandoffExecutionResult],
]


class HandoffExecutor:
    """Claims explicit Xpert handoffs and executes their published target."""

    def __init__(
        self,
        store: AgentTaskStore,
        run_registry: RunRegistry,
        execute_target: ExecuteHandoffTarget,
        *,
        enabled: bool = True,
        poll_interval: float = 1.0,
        lease_seconds: float = 60.0,
        max_attempts: int = 3,
        max_concurrency: int = 2,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.run_registry = run_registry
        self.execute_target = execute_target
        self.enabled = enabled
        self.poll_interval = max(0.1, poll_interval)
        self.lease_seconds = max(1.0, lease_seconds)
        self.max_attempts = max(1, max_attempts)
        self.max_concurrency = max(1, min(max_concurrency, 20))
        self.worker_id = worker_id or f"handoff-executor-{uuid.uuid4().hex[:8]}"
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._active_handoffs: set[str] = set()

    def start(self) -> None:
        if not self.enabled or (self._loop_task and not self._loop_task.done()):
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(),
            name="xpert-handoff-executor",
        )

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
        slots = max(0, self.max_concurrency - len(self._active_handoffs))
        if slots == 0:
            return 0
        eligible = [
            handoff
            for handoff in await self.store.list_executable_handoffs(limit=20)
            if handoff.handoff_id not in self._active_handoffs
        ][:slots]

        async def process(handoff: AgentHandoff) -> bool:
            try:
                await self.execute_handoff(handoff.handoff_id)
                return True
            except HandoffBusyError:
                return False
            except Exception:
                logger.exception(
                    "Unexpected handoff executor failure handoff_id=%s",
                    handoff.handoff_id,
                )
                return False

        if not eligible:
            return 0
        results = await asyncio.gather(*(process(handoff) for handoff in eligible))
        return sum(1 for value in results if value)

    async def execute_handoff(self, handoff_id: str) -> AgentHandoff:
        if not self.enabled:
            raise HandoffExecutorError("Handoff executor is disabled.")
        if handoff_id in self._active_handoffs:
            raise HandoffBusyError("Handoff is already executing in this worker.")
        self._active_handoffs.add(handoff_id)
        try:
            existing = await self.store.get_handoff(handoff_id)
            claim_attempts = 1 if existing is not None and existing.metadata.get("no_auto_retry") else self.max_attempts
            try:
                claimed = await self.store.claim_handoff(
                    handoff_id, worker_id=self.worker_id, lease_seconds=self.lease_seconds,
                    max_attempts=claim_attempts,
                )
            except HandoffOwnershipError:
                raise
            except ValueError as exc:
                if "already leased" in str(exc):
                    raise HandoffBusyError(str(exc)) from exc
                # A failed claim grants no authority to dead-letter an existing owner.
                raise HandoffExecutorError(str(exc)) from exc

            await self.store.settle_claimed_handoff(claimed, "accepted", task_metadata={
                "execution_mode": "xpert_auto", "handoff_attempt": claimed.metadata.get("attempts", 1),
            })
            await self._project_current(handoff_id, expected_lease_token=claimed.metadata["lease_token"])
            task = await self.store.get_task(claimed.task_id)
            handoff_run = await self._first_run("agent_handoff", handoff_id)
            async with self.store.handoff_projection(
                handoff_id, expected_lease_token=claimed.metadata["lease_token"],
            ):
                pass
            try:
                result = await self.execute_target(copy.deepcopy(claimed), task, handoff_run.run_id if handoff_run else None)
            except Exception as exc:
                return await self._finish_failure(claimed, task, exc)

            if result.waiting_approval and result.waiting_client:
                return await self._finish_failure(claimed, task, HandoffPermanentError("HANDOFF_WAIT_INVALID"))
            metadata = {
                "target_xpert_id": result.xpert_id, "target_xpert_slug": result.xpert_slug,
                "target_xpert_version": result.xpert_version, "xpert_run_id": result.run_id,
            }
            if result.waiting_approval or result.waiting_client:
                status = "waiting_approval" if result.waiting_approval else "waiting_client"
                wait_key = "approval_id" if result.waiting_approval else "client_request_id"
                wait_id = result.approval_id if result.waiting_approval else result.client_request_id
                await self.store.settle_claimed_handoff(
                    claimed, status,
                    metadata={**metadata, wait_key: wait_id, "workflow_task_id": result.task_id},
                    task_metadata={wait_key: wait_id, "workflow_task_id": result.task_id},
                )
            else:
                safe_output = str(result.output or "")
                if len(safe_output) > 100_000:
                    return await self._finish_failure(claimed, task, HandoffPermanentError("HANDOFF_RESULT_TOO_LARGE"))
                await self.store.settle_claimed_handoff(
                    claimed, "completed", result=safe_output,
                    metadata={**metadata, "completed_by": self.worker_id, "completed_at": time.time(),
                              "result": safe_output, "result_length": len(safe_output), "last_error": ""},
                    task_metadata={"xpert_run_id": result.run_id, "target_xpert_id": result.xpert_id,
                                   "target_xpert_version": result.xpert_version},
                )
            return await self._project_current(handoff_id)
        except HandoffOwnershipError:
            # Reconcile authoritative state only; never apply the stale callback payload.
            return await self._project_current(handoff_id)
        finally:
            self._active_handoffs.discard(handoff_id)

    async def requeue_handoff(
        self,
        handoff_id: str,
        *,
        operator: str,
        reset_attempts: bool = True,
        repin_version: bool = True,
    ) -> AgentHandoff:
        handoff = await self.store.requeue_handoff(
            handoff_id,
            operator=operator,
            reset_attempts=reset_attempts,
            repin_version=repin_version,
        )
        return await self._project_current(handoff.handoff_id)

    async def status(self) -> dict[str, object]:
        handoffs = await self.store.list_handoffs()
        executable = await self.store.list_executable_handoffs(limit=10_000)
        return {
            "enabled": self.enabled,
            "running": bool(self._loop_task and not self._loop_task.done()),
            "worker_id": self.worker_id,
            "poll_interval": self.poll_interval,
            "lease_seconds": self.lease_seconds,
            "max_attempts": self.max_attempts,
            "max_concurrency": self.max_concurrency,
            "active_leases": len(self._active_handoffs),
            "executable_pending": len(executable),
            "dead_letter_count": sum(
                1 for handoff in handoffs if handoff.status == "dead_letter"
            ),
        }

    async def _project_current(
        self, handoff_id: str, *, expected_lease_token: str | None = None,
    ) -> AgentHandoff:
        def identity(handoff, task):
            return {"handoff_status": handoff.status, "task_status": task.status,
                    "handoff_updated_at": handoff.updated_at, "task_updated_at": task.updated_at}

        for _ in range(8):
            # Source authority covers only local state projection, never observer callbacks.
            async with self.store.handoff_projection(
                handoff_id, expected_lease_token=expected_lease_token,
            ) as (handoff, task):
                task_run, handoff_run = await self._ensure_runs(handoff, task)
                status = {
                    "pending": "pending", "running": "running", "waiting_approval": "waiting",
                    "waiting_client": "waiting", "needs_attention": "waiting",
                    "completed": "completed", "failed": "failed", "cancelled": "cancelled",
                }[task.status]
                marker = identity(handoff, task)
                metadata = {
                    key: handoff.metadata[key] for key in (
                        "xpert_run_id", "target_xpert_id", "target_xpert_version", "result_length",
                        "approval_id", "client_request_id", "workflow_task_id", "attempts", "next_attempt_at",
                        "requeued_by",
                    ) if key in handoff.metadata
                }
                metadata.update({"handoff_id": handoff_id, "handoff_status": handoff.status,
                                 "handoff_settlement_projection": marker})
                for run in (task_run, handoff_run):
                    if run is not None and (run.metadata.get("handoff_settlement_projection") != marker or run.status != status):
                        await self._update_run(run, status=status, error=task.error,
                                               clear_error=task.error is None, metadata=metadata)
                event = {
                    "accepted": "attempt.started", "completed": "completed", "dead_letter": "dead_letter",
                    "retry_wait": "retry_scheduled", "waiting_approval": "waiting_approval",
                    "waiting_client": "waiting_client",
                }.get(handoff.status)
                if task.status == "cancelled":
                    event = "cancelled"
                elif handoff.status == "accepted" and task.status != "running":
                    event = None
                elif handoff.status == "pending" and handoff.metadata.get("requeued_at"):
                    event = "requeued"

            if event and handoff_run is not None and handoff_run.metadata.get("handoff_settlement_checkpoint") != marker:
                event_type = f"agent_handoff.{event}"
                checkpoints = await self.run_registry.list_checkpoints(handoff_run.run_id, limit=1)
                if not any(c.event_type == event_type and c.metadata.get("handoff_settlement_projection") == marker
                           for c in checkpoints):
                    await self._checkpoint(
                        handoff_run, event_type=event_type, title="交接权威状态已同步",
                        summary=f"status={handoff.status}; task_status={task.status}",
                        severity="error" if status == "failed" else "info", metadata=metadata,
                    )
                await self.run_registry.update_run(
                    handoff_run.run_id, metadata={"handoff_settlement_checkpoint": marker},
                )
            # A slow checkpoint may race cancellation or takeover. Refresh, not old payload replay.
            async with self.store.handoff_projection(handoff_id) as (current, current_task):
                if identity(current, current_task) == marker:
                    return current
        raise HandoffOwnershipError("交接状态持续变化，需重新同步权威状态。")

    async def _finish_failure(
        self, claimed: AgentHandoff, task: AgentTask | None, exc: Exception,
    ) -> AgentHandoff:
        is_v2 = int(claimed.metadata.get("contract_version") or 1) == 2
        error = "HANDOFF_TARGET_UNAVAILABLE" if is_v2 else str(exc or "交接执行失败。")[:1000]
        attempts = int(claimed.metadata.get("attempts") or 1)
        max_attempts = 1 if claimed.metadata.get("no_auto_retry") else self.max_attempts
        if isinstance(exc, HandoffPermanentError) or attempts >= max_attempts:
            await self.store.settle_claimed_handoff(
                claimed, "dead_letter", error=error,
                metadata={"last_error": error, "dead_lettered_at": time.time()},
                task_metadata={"handoff_status": "dead_letter", "attempts": attempts},
            )
        else:
            next_attempt_at = time.time() + min(30.0, float(2**attempts))
            await self.store.settle_claimed_handoff(
                claimed, "retry_wait", error=error,
                metadata={"last_error": error, "next_attempt_at": next_attempt_at},
                task_metadata={"handoff_status": "retry_wait", "next_attempt_at": next_attempt_at, "attempts": attempts},
            )
        return await self._project_current(claimed.handoff_id)

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Handoff executor poll failed")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.poll_interval,
                )
            except asyncio.TimeoutError:
                continue

    async def _first_run(self, run_type: str, source_id: str) -> RuntimeRun | None:
        runs = await self.run_registry.list_runs(
            run_type=run_type,  # type: ignore[arg-type]
            source_id=source_id,
            limit=1,
        )
        return runs[0] if runs else None

    async def _ensure_runs(
        self,
        handoff: AgentHandoff,
        task: AgentTask | None,
    ) -> tuple[RuntimeRun | None, RuntimeRun]:
        task_run = (
            await self._first_run("agent_task", task.task_id)
            if task is not None
            else None
        )
        if task is not None and task_run is None:
            task_run = await self.run_registry.create_run(
                "agent_task",
                task.title,
                status="pending",
                source_id=task.task_id,
                metadata={
                    "agent_task_id": task.task_id,
                    "assigned_agent": task.assigned_agent,
                    "restored_from_store": True,
                },
            )
        handoff_run = await self._first_run("agent_handoff", handoff.handoff_id)
        if handoff_run is None:
            handoff_run = await self.run_registry.create_run(
                "agent_handoff",
                f"{handoff.source_agent} -> {handoff.target_agent}",
                status="pending",
                source_id=handoff.handoff_id,
                parent_run_id=task_run.run_id if task_run else None,
                metadata={
                    "agent_task_id": handoff.task_id,
                    "handoff_id": handoff.handoff_id,
                    "source_agent": handoff.source_agent,
                    "target_agent": handoff.target_agent,
                    "restored_from_store": True,
                },
            )
            await self.run_registry.record_checkpoint(
                handoff_run.run_id,
                event_type="agent_handoff.restored",
                title="Persisted handoff restored",
                summary=handoff.status,
                metadata={
                    "handoff_id": handoff.handoff_id,
                    "status": handoff.status,
                },
            )
        return task_run, handoff_run

    async def _update_run(
        self,
        run: RuntimeRun | None,
        *,
        status: str,
        error: str | None = None,
        metadata: dict[str, object] | None = None,
        clear_error: bool = False,
    ) -> None:
        if run is None:
            return
        await self.run_registry.update_run(
            run.run_id,
            status=status,  # type: ignore[arg-type]
            error=error,
            metadata=metadata,
            clear_error=clear_error,
        )

    async def _checkpoint(
        self,
        run: RuntimeRun | None,
        *,
        event_type: str,
        title: str,
        summary: str,
        severity: str = "info",
        metadata: dict[str, object] | None = None,
    ) -> None:
        if run is None:
            return
        await self.run_registry.record_checkpoint(
            run.run_id,
            event_type=event_type,
            title=title,
            summary=summary,
            severity=severity,
            metadata=metadata,
        )
