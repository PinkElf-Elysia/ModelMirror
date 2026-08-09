from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from .contracts import (
    Origin,
    TaskCreateRequest,
    TaskRecord,
    TaskSpec,
    TaskState,
    TERMINAL_STATES,
)
from .provider import (
    CodingAgentProvider,
    ProviderEventKind,
    ProviderOpenRequest,
    ProviderSession,
)
from .store import CodingWorkerStore, WorkerConflictError
from .workspace import WorkspaceBroker, WorkspaceError


class CodingWorkerService:
    """Persistent two-slot scheduler. Provider processes never survive a restart."""

    def __init__(
        self,
        *,
        store: CodingWorkerStore,
        workspace_broker: WorkspaceBroker,
        provider: CodingAgentProvider,
        max_active_tasks: int = 2,
    ) -> None:
        if not 1 <= max_active_tasks <= 16:
            raise ValueError("active task capacity is outside the allowed range")
        self.store = store
        self.workspace_broker = workspace_broker
        self.provider = provider
        self.max_active_tasks = max_active_tasks
        self._active: dict[str, asyncio.Task[None]] = {}
        self._sessions: dict[str, ProviderSession] = {}
        self._wake = asyncio.Event()
        self._scheduler: asyncio.Task[None] | None = None
        self._started = False
        self._closing = False

    @property
    def active_task_ids(self) -> frozenset[str]:
        return frozenset(self._active)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._closing = False
        self._scheduler = asyncio.create_task(
            self._scheduler_loop(), name="coding-worker-scheduler"
        )
        self._wake.set()

    async def shutdown(self) -> None:
        if not self._started:
            return
        self._closing = True
        for task_id, session in tuple(self._sessions.items()):
            with contextlib.suppress(Exception):
                await self.provider.cancel(session)
            current = self.store.get_task(task_id)
            if current.state not in TERMINAL_STATES:
                with contextlib.suppress(WorkerConflictError):
                    self.store.transition(task_id, TaskState.INTERRUPTED, reason="service_shutdown")
        for task in tuple(self._active.values()):
            task.cancel()
        if self._active:
            await asyncio.gather(*tuple(self._active.values()), return_exceptions=True)
        if self._scheduler is not None:
            self._scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._scheduler
        self._active.clear()
        self._sessions.clear()
        self._scheduler = None
        self._started = False

    async def create_task(self, origin: Origin, request: TaskCreateRequest) -> TaskRecord:
        await self.start()
        spec = TaskSpec(**request.model_dump(), origin=origin)
        task = self.store.create_task(spec)
        self._wake.set()
        return task

    async def resume(self, task_id: str) -> TaskRecord:
        await self.start()
        task = self.store.get_task(task_id)
        if task.state not in {
            TaskState.INTERRUPTED,
            TaskState.PAUSED,
            TaskState.BLOCKED,
            TaskState.FAILED,
            TaskState.BUDGET_LIMITED,
        }:
            raise WorkerConflictError("Task cannot be resumed.", code="task_state_conflict")
        resumed = self.store.transition(task_id, TaskState.QUEUED)
        self._wake.set()
        return resumed

    async def pause(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.state is TaskState.QUEUED:
            return self.store.transition(task_id, TaskState.PAUSED)
        if task.state not in {
            TaskState.PREPARING,
            TaskState.RUNNING,
            TaskState.WAITING_APPROVAL,
            TaskState.TESTING,
        }:
            raise WorkerConflictError("Task cannot be paused.", code="task_state_conflict")
        session = self._sessions.get(task_id)
        if session is not None:
            await self.provider.cancel(session)
        active = self._active.get(task_id)
        if active is not None:
            active.cancel()
        paused = self.store.transition(task_id, TaskState.PAUSED, reason="user_paused")
        self._wake.set()
        return paused

    async def cancel(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.state in TERMINAL_STATES:
            return task
        session = self._sessions.get(task_id)
        if session is not None:
            await self.provider.cancel(session)
        active = self._active.get(task_id)
        if active is not None:
            active.cancel()
        cancelled = self.store.transition(task_id, TaskState.CANCELLED, reason="user_cancelled")
        self._wake.set()
        return cancelled

    async def append_message(self, task_id: str, text: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.state not in {TaskState.RUNNING, TaskState.WAITING_APPROVAL, TaskState.PAUSED}:
            raise WorkerConflictError("Task is not accepting messages.", code="task_state_conflict")
        self.store.append_message(task_id, role="user", content=text)
        self.store.append_event(task_id, "steering_queued", {})
        return self.store.get_task(task_id)

    async def wait_for(
        self,
        task_id: str,
        predicate: Callable[[TaskRecord], bool],
        *,
        timeout: float = 10.0,
    ) -> TaskRecord:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            task = self.store.get_task(task_id)
            if predicate(task):
                return task
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Task {task_id} did not reach the expected state")
            await asyncio.sleep(0.01)

    async def _scheduler_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while not self._closing and len(self._active) < self.max_active_tasks:
                queued = self.store.list_queued_tasks(limit=1)
                if not queued:
                    break
                record = queued[0]
                try:
                    self.store.transition(
                        record.task_id,
                        TaskState.PREPARING,
                        expected_state=TaskState.QUEUED,
                    )
                except WorkerConflictError:
                    continue
                runner = asyncio.create_task(
                    self._run_task(record.task_id), name=f"coding-worker-{record.task_id}"
                )
                self._active[record.task_id] = runner
                runner.add_done_callback(
                    lambda completed, task_id=record.task_id: self._task_finished(
                        task_id, completed
                    )
                )

    def _task_finished(self, task_id: str, _task: asyncio.Task[None]) -> None:
        self._active.pop(task_id, None)
        self._sessions.pop(task_id, None)
        if not self._closing:
            self._wake.set()

    async def _run_task(self, task_id: str) -> None:
        session: ProviderSession | None = None
        try:
            task = self.store.get_task(task_id)
            workspace = (
                self.workspace_broker.get(task.workspace_id)
                if task.workspace_id is not None
                else await self.workspace_broker.prepare(task.spec.workspace_source)
            )
            request = ProviderOpenRequest(
                task_id=task_id,
                workspace_id=workspace.workspace_id,
                objective=task.spec.objective,
                model_route=task.spec.model_route,
                policy_profile=task.spec.policy_profile,
                budget=task.spec.budget,
            )
            session = await self.provider.open(request)
            self._sessions[task_id] = session
            self.store.transition(
                task_id,
                TaskState.RUNNING,
                workspace_id=workspace.workspace_id,
                provider_session_id=session.session_id,
                expected_state=TaskState.PREPARING,
            )
            if not self.store.list_messages(task_id):
                self.store.append_message(task_id, role="user", content=task.spec.objective)
            terminal = False
            async for event in self.provider.message(session, task.spec.objective):
                self.store.append_event(
                    task_id,
                    "provider_event",
                    {"kind": event.kind.value, "data": event.data},
                )
                if event.kind is ProviderEventKind.TURN_COMPLETED:
                    self.store.transition(task_id, TaskState.TESTING)
                    self.store.transition(
                        task_id,
                        TaskState.BLOCKED,
                        reason="acceptance_runner_pending",
                    )
                    terminal = True
                    break
                if event.kind is ProviderEventKind.CANCELLED:
                    self.store.transition(task_id, TaskState.CANCELLED, reason="provider_cancelled")
                    terminal = True
                    break
                if event.kind is ProviderEventKind.FAILED:
                    self.store.transition(task_id, TaskState.FAILED, reason="provider_failed")
                    terminal = True
                    break
            if not terminal:
                current = self.store.get_task(task_id)
                if current.state not in TERMINAL_STATES:
                    self.store.transition(
                        task_id, TaskState.INTERRUPTED, reason="provider_stream_ended"
                    )
        except asyncio.CancelledError:
            current = self.store.get_task(task_id)
            if current.state not in TERMINAL_STATES and current.state not in {
                TaskState.PAUSED,
                TaskState.INTERRUPTED,
            }:
                with contextlib.suppress(WorkerConflictError):
                    self.store.transition(task_id, TaskState.INTERRUPTED, reason="runner_cancelled")
            raise
        except WorkspaceError as exc:
            current = self.store.get_task(task_id)
            if current.state not in TERMINAL_STATES:
                self.store.transition(task_id, TaskState.FAILED, reason=exc.code)
        except Exception:
            current = self.store.get_task(task_id)
            if current.state not in TERMINAL_STATES:
                with contextlib.suppress(WorkerConflictError):
                    self.store.transition(task_id, TaskState.FAILED, reason="worker_failed")
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    await self.provider.close(session)
