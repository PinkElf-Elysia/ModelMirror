from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .agent_tasks import AgentHandoff, AgentTask, AgentTaskStore
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
            try:
                claimed = await self.store.claim_handoff(
                    handoff_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                    max_attempts=self.max_attempts,
                )
            except ValueError as exc:
                if "already leased" in str(exc):
                    raise HandoffBusyError(str(exc)) from exc
                if "attempt limit" in str(exc):
                    existing = await self.store.get_handoff(handoff_id)
                    if existing is not None:
                        task = await self.store.get_task(existing.task_id)
                        return await self._finish_failure(
                            existing,
                            task,
                            HandoffPermanentError(str(exc)),
                        )
                raise HandoffExecutorError(str(exc)) from exc

            task = await self.store.get_task(claimed.task_id)
            if task is None:
                return await self._finish_failure(
                    claimed,
                    None,
                    HandoffPermanentError("Agent task no longer exists."),
                )

            attempt = int(claimed.metadata.get("attempts") or 1)
            await self.store.update_task(
                task.task_id,
                status="running",
                assigned_agent=claimed.target_agent,
                clear_error=True,
                metadata={
                    "execution_mode": "xpert_auto",
                    "handoff_id": claimed.handoff_id,
                    "handoff_attempt": attempt,
                },
            )
            task_run, handoff_run = await self._ensure_runs(claimed, task)
            await self._update_run(
                handoff_run,
                status="running",
                metadata={
                    "handoff_status": "accepted",
                    "attempt": attempt,
                    "lease_owner": self.worker_id,
                },
            )
            await self._update_run(
                task_run,
                status="running",
                metadata={"handoff_id": claimed.handoff_id, "attempt": attempt},
            )
            await self._checkpoint(
                handoff_run,
                event_type="agent_handoff.attempt.started",
                title="Xpert handoff attempt started",
                summary=f"attempt={attempt}",
                metadata={
                    "handoff_id": claimed.handoff_id,
                    "attempt": attempt,
                    "target_agent": claimed.target_agent,
                },
            )

            try:
                result = await self.execute_target(
                    claimed,
                    task,
                    handoff_run.run_id if handoff_run else None,
                )
            except Exception as exc:
                return await self._finish_failure(claimed, task, exc)

            if result.waiting_approval:
                waiting = await self.store.update_handoff_status(
                    claimed.handoff_id,
                    "waiting_approval",
                    metadata={
                        "approval_id": result.approval_id,
                        "workflow_task_id": result.task_id,
                        "xpert_run_id": result.run_id,
                        "target_xpert_id": result.xpert_id,
                        "target_xpert_slug": result.xpert_slug,
                        "target_xpert_version": result.xpert_version,
                        "lease_owner": "",
                        "lease_token": "",
                        "lease_expires_at": 0.0,
                    },
                )
                await self.store.update_task(
                    task.task_id,
                    status="waiting_approval",
                    metadata={
                        "handoff_id": waiting.handoff_id,
                        "approval_id": result.approval_id,
                        "workflow_task_id": result.task_id,
                    },
                )
                await self._update_run(
                    handoff_run,
                    status="waiting",
                    metadata={
                        "handoff_status": "waiting_approval",
                        "approval_id": result.approval_id,
                    },
                )
                await self._update_run(
                    task_run,
                    status="waiting",
                    metadata={
                        "handoff_id": waiting.handoff_id,
                        "approval_id": result.approval_id,
                    },
                )
                await self._checkpoint(
                    handoff_run,
                    event_type="agent_handoff.waiting_approval",
                    title="Xpert handoff is waiting for approval",
                    summary=f"approval_id={result.approval_id}",
                    severity="warning",
                    metadata={
                        "handoff_id": waiting.handoff_id,
                        "approval_id": result.approval_id,
                    },
                )
                return waiting
            if result.waiting_client:
                waiting = await self.store.update_handoff_status(
                    claimed.handoff_id,
                    "waiting_client",
                    metadata={
                        "client_request_id": result.client_request_id,
                        "workflow_task_id": result.task_id,
                        "xpert_run_id": result.run_id,
                        "target_xpert_id": result.xpert_id,
                        "target_xpert_slug": result.xpert_slug,
                        "target_xpert_version": result.xpert_version,
                        "lease_owner": "",
                        "lease_token": "",
                        "lease_expires_at": 0.0,
                    },
                )
                await self.store.update_task(
                    task.task_id,
                    status="waiting_client",
                    metadata={
                        "handoff_id": waiting.handoff_id,
                        "client_request_id": result.client_request_id,
                        "workflow_task_id": result.task_id,
                    },
                )
                await self._update_run(
                    handoff_run,
                    status="waiting",
                    metadata={
                        "handoff_status": "waiting_client",
                        "client_request_id": result.client_request_id,
                    },
                )
                await self._update_run(
                    task_run,
                    status="waiting",
                    metadata={
                        "handoff_id": waiting.handoff_id,
                        "client_request_id": result.client_request_id,
                    },
                )
                await self._checkpoint(
                    handoff_run,
                    event_type="agent_handoff.waiting_client",
                    title="Xpert handoff is waiting for a client host",
                    summary=f"request_id={result.client_request_id}",
                    severity="warning",
                    metadata={
                        "handoff_id": waiting.handoff_id,
                        "client_request_id": result.client_request_id,
                    },
                )
                return waiting

            completed_at = time.time()
            safe_output = str(result.output or "")[:100_000]
            completed = await self.store.update_handoff_status(
                claimed.handoff_id,
                "completed",
                metadata={
                    "completed_by": self.worker_id,
                    "completed_at": completed_at,
                    "result": safe_output,
                    "result_length": len(safe_output),
                    "target_xpert_id": result.xpert_id,
                    "target_xpert_slug": result.xpert_slug,
                    "target_xpert_version": result.xpert_version,
                    "xpert_run_id": result.run_id,
                    "lease_owner": "",
                    "lease_token": "",
                    "lease_expires_at": 0.0,
                    "last_error": "",
                },
            )
            await self.store.update_task(
                task.task_id,
                status="completed",
                result=safe_output,
                clear_error=True,
                metadata={
                    "handoff_id": completed.handoff_id,
                    "xpert_run_id": result.run_id,
                    "target_xpert_id": result.xpert_id,
                    "target_xpert_version": result.xpert_version,
                },
            )
            await self._update_run(
                handoff_run,
                status="completed",
                metadata={
                    "handoff_status": "completed",
                    "xpert_run_id": result.run_id,
                    "target_xpert_id": result.xpert_id,
                    "target_xpert_version": result.xpert_version,
                    "result_length": len(safe_output),
                },
            )
            await self._update_run(
                task_run,
                status="completed",
                metadata={
                    "handoff_id": completed.handoff_id,
                    "xpert_run_id": result.run_id,
                    "result_length": len(safe_output),
                },
            )
            await self._checkpoint(
                handoff_run,
                event_type="agent_handoff.completed",
                title="Target Xpert completed handoff",
                summary=f"result_length={len(safe_output)}",
                metadata={
                    "handoff_id": completed.handoff_id,
                    "attempt": attempt,
                    "xpert_run_id": result.run_id,
                    "target_xpert_id": result.xpert_id,
                    "target_xpert_version": result.xpert_version,
                },
            )
            return completed
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
        await self.store.update_task(
            handoff.task_id,
            status="pending",
            clear_result=True,
            clear_error=True,
            metadata={"handoff_id": handoff.handoff_id, "requeued_by": operator},
        )
        task = await self.store.get_task(handoff.task_id)
        task_run, handoff_run = await self._ensure_runs(handoff, task)
        await self._update_run(
            handoff_run,
            status="pending",
            clear_error=True,
            metadata={"handoff_status": "pending", "requeued_by": operator},
        )
        await self._update_run(
            task_run,
            status="pending",
            clear_error=True,
            metadata={"handoff_id": handoff.handoff_id, "requeued_by": operator},
        )
        await self._checkpoint(
            handoff_run,
            event_type="agent_handoff.requeued",
            title="Xpert handoff requeued",
            summary=operator,
            metadata={"handoff_id": handoff.handoff_id, "operator": operator},
        )
        return handoff

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

    async def _finish_failure(
        self,
        claimed: AgentHandoff,
        task: AgentTask | None,
        exc: Exception,
    ) -> AgentHandoff:
        error = str(exc or "Handoff execution failed.")[:1000]
        attempts = int(claimed.metadata.get("attempts") or 1)
        permanent = isinstance(exc, HandoffPermanentError)
        exhausted = attempts >= self.max_attempts
        task_run, handoff_run = await self._ensure_runs(claimed, task)
        if permanent or exhausted:
            dead_lettered_at = time.time()
            handoff = await self.store.update_handoff_status(
                claimed.handoff_id,
                "dead_letter",
                metadata={
                    "last_error": error,
                    "dead_lettered_at": dead_lettered_at,
                    "lease_owner": "",
                    "lease_token": "",
                    "lease_expires_at": 0.0,
                },
            )
            if task is not None:
                await self.store.update_task(
                    task.task_id,
                    status="failed",
                    error=error,
                    metadata={
                        "handoff_id": handoff.handoff_id,
                        "handoff_status": "dead_letter",
                        "attempts": attempts,
                    },
                )
            await self._update_run(
                handoff_run,
                status="failed",
                error=error,
                metadata={
                    "handoff_status": "dead_letter",
                    "attempts": attempts,
                },
            )
            await self._update_run(
                task_run,
                status="failed",
                error=error,
                metadata={
                    "handoff_id": handoff.handoff_id,
                    "handoff_status": "dead_letter",
                },
            )
            await self._checkpoint(
                handoff_run,
                event_type="agent_handoff.dead_letter",
                title="Xpert handoff moved to dead letter",
                summary=error,
                severity="error",
                metadata={
                    "handoff_id": handoff.handoff_id,
                    "attempts": attempts,
                    "permanent": permanent,
                },
            )
            return handoff

        delay = min(30.0, float(2**attempts))
        next_attempt_at = time.time() + delay
        handoff = await self.store.update_handoff_status(
            claimed.handoff_id,
            "retry_wait",
            metadata={
                "last_error": error,
                "next_attempt_at": next_attempt_at,
                "lease_owner": "",
                "lease_token": "",
                "lease_expires_at": 0.0,
            },
        )
        if task is not None:
            await self.store.update_task(
                task.task_id,
                status="running",
                error=error,
                metadata={
                    "handoff_id": handoff.handoff_id,
                    "handoff_status": "retry_wait",
                    "next_attempt_at": next_attempt_at,
                    "attempts": attempts,
                },
            )
        await self._update_run(
            handoff_run,
            status="running",
            metadata={
                "handoff_status": "retry_wait",
                "attempts": attempts,
                "next_attempt_at": next_attempt_at,
                "last_error": error,
            },
        )
        await self._checkpoint(
            handoff_run,
            event_type="agent_handoff.retry_scheduled",
            title="Xpert handoff retry scheduled",
            summary=f"retry_in={delay:.0f}s: {error}",
            severity="warning",
            metadata={
                "handoff_id": handoff.handoff_id,
                "attempts": attempts,
                "next_attempt_at": next_attempt_at,
            },
        )
        return handoff

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
