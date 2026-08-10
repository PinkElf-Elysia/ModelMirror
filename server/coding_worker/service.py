from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from .contracts import (
    EvidenceStatus,
    Origin,
    TaskCreateRequest,
    TaskRecord,
    TaskSpec,
    TaskState,
    TERMINAL_STATES,
    WorkerEvidence,
)
from .evidence import HarnessRunner
from .provider import (
    CodingAgentProvider,
    ProviderCheckpoint,
    ProviderEventKind,
    ProviderOpenRequest,
    ProviderSession,
)
from .store import CodingWorkerStore, WorkerConflictError
from .tool_broker import ToolBroker, ToolBrokerError
from .workspace import WorkspaceBroker, WorkspaceError


class CodingWorkerService:
    """Persistent two-slot scheduler. Provider processes never survive a restart."""

    def __init__(
        self,
        *,
        store: CodingWorkerStore,
        workspace_broker: WorkspaceBroker,
        provider: CodingAgentProvider,
        harness_runner: HarnessRunner | None = None,
        max_active_tasks: int = 2,
        tool_broker: ToolBroker | None = None,
    ) -> None:
        if not 1 <= max_active_tasks <= 16:
            raise ValueError("active task capacity is outside the allowed range")
        self.store = store
        self.workspace_broker = workspace_broker
        self.provider = provider
        self.harness_runner = harness_runner
        self.max_active_tasks = max_active_tasks
        self.tool_broker = tool_broker
        self._active: dict[str, asyncio.Task[None]] = {}
        self._task_slots: dict[str, str] = {}
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
        self._task_slots.clear()
        self._sessions.clear()
        self._scheduler = None
        self._started = False

    async def create_task(self, origin: Origin, request: TaskCreateRequest) -> TaskRecord:
        if self.tool_broker is not None:
            unknown = [
                check.check_id
                for check in request.acceptance.required_checks
                if check.kind == "command"
                and check.check_id not in self.tool_broker.frozen_checks
            ]
            if unknown:
                raise WorkerConflictError(
                    "Acceptance check is not registered.",
                    code="worker_acceptance_not_registered",
                )
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
            capacity = min(
                self.max_active_tasks,
                len(self.workspace_broker.slot_ids)
                if self.workspace_broker.dedicated_slots
                else self.max_active_tasks,
            )
            while not self._closing and len(self._active) < capacity:
                selected = self._select_queued_task()
                if selected is None:
                    break
                record, slot_id = selected
                try:
                    self.store.transition(
                        record.task_id,
                        TaskState.PREPARING,
                        expected_state=TaskState.QUEUED,
                    )
                except WorkerConflictError:
                    continue
                if slot_id is not None:
                    self._task_slots[record.task_id] = slot_id
                runner = asyncio.create_task(
                    self._run_task(record.task_id, slot_id=slot_id),
                    name=f"coding-worker-{record.task_id}",
                )
                self._active[record.task_id] = runner
                runner.add_done_callback(
                    lambda completed, task_id=record.task_id: self._task_finished(
                        task_id, completed
                    )
                )

    def _select_queued_task(self) -> tuple[TaskRecord, str | None] | None:
        queued = self.store.list_queued_tasks(limit=128)
        if not queued:
            return None
        if not self.workspace_broker.dedicated_slots:
            return queued[0], None
        occupied = set(self._task_slots.values())
        available = [
            slot_id
            for slot_id in self.workspace_broker.slot_ids
            if slot_id not in occupied
        ]
        if not available:
            return None
        for record in queued:
            required_slot: str | None = None
            if record.workspace_id is not None:
                try:
                    required_slot = self.workspace_broker.workspace_slot(
                        record.workspace_id
                    )
                except WorkspaceError:
                    # Let the runner persist the precise workspace failure.
                    required_slot = available[0]
            if required_slot is None:
                return record, available[0]
            if required_slot in available:
                return record, required_slot
        return None

    def _task_finished(self, task_id: str, _task: asyncio.Task[None]) -> None:
        self._active.pop(task_id, None)
        self._task_slots.pop(task_id, None)
        self._sessions.pop(task_id, None)
        if not self._closing:
            self._wake.set()

    async def _run_task(self, task_id: str, *, slot_id: str | None = None) -> None:
        session: ProviderSession | None = None
        try:
            task = self.store.get_task(task_id)
            workspace = (
                self.workspace_broker.get(task.workspace_id)
                if task.workspace_id is not None
                else await self.workspace_broker.prepare(
                    task.spec.workspace_source, slot_id=slot_id
                )
            )
            if slot_id is not None and workspace.slot_id != slot_id:
                raise WorkspaceError(
                    "Workspace slot binding changed.", code="workspace_slot_changed"
                )
            request = ProviderOpenRequest(
                task_id=task_id,
                workspace_id=workspace.workspace_id,
                objective=task.spec.objective,
                model_route=task.spec.model_route,
                policy_profile=task.spec.policy_profile,
                budget=task.spec.budget,
            )
            resume_phase: str | None = None
            resume_context: dict[str, object] | None = None
            completed_turns = 0
            checkpoint = self.store.latest_checkpoint(task_id)
            if checkpoint is not None:
                current_tree_hash = self.workspace_broker.current_tree_hash(
                    workspace.workspace_id
                )
                if checkpoint.workspace_tree_hash != current_tree_hash:
                    self.store.transition(
                        task_id,
                        TaskState.BLOCKED,
                        reason="checkpoint_workspace_changed",
                        expected_state=TaskState.PREPARING,
                    )
                    return
                try:
                    provider_checkpoint = ProviderCheckpoint.model_validate(
                        checkpoint.payload["provider"]
                    )
                    resume_phase = str(checkpoint.payload["phase"])
                    completed_turns = int(checkpoint.payload["completed_turns"])
                    raw_context = checkpoint.payload.get("context_summary")
                    if raw_context is not None:
                        if not isinstance(raw_context, dict):
                            raise TypeError("context summary is invalid")
                        resume_context = raw_context
                except (KeyError, TypeError, ValueError) as exc:
                    raise WorkerConflictError(
                        "Checkpoint payload is invalid.", code="checkpoint_invalid"
                    ) from exc
                if resume_phase != "testing" or completed_turns < 1:
                    raise WorkerConflictError(
                        "Checkpoint phase is invalid.", code="checkpoint_invalid"
                    )
                session = await self.provider.restore(request, provider_checkpoint)
            else:
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
            await self._drive_session(
                task,
                session,
                resume_phase=resume_phase,
                resume_context=resume_context,
                completed_turns=completed_turns,
            )
        except TimeoutError:
            current = self.store.get_task(task_id)
            if current.state not in TERMINAL_STATES:
                with contextlib.suppress(WorkerConflictError):
                    self.store.transition(
                        task_id, TaskState.BUDGET_LIMITED, reason="time_budget_exhausted"
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

    async def _drive_session(
        self,
        task: TaskRecord,
        session: ProviderSession,
        *,
        resume_phase: str | None,
        resume_context: dict[str, object] | None,
        completed_turns: int,
    ) -> None:
        task_id = task.task_id
        message = task.spec.objective
        turns = completed_turns
        async with asyncio.timeout(task.spec.budget.max_seconds):
            if resume_phase == "testing":
                feedback = await self._evaluate_acceptance(task, turns)
                if feedback is None:
                    return
                message = self._restored_context_message(resume_context, feedback)
            while True:
                turns += 1
                turn_completed = False
                async for event in self.provider.message(session, message):
                    self.store.append_event(
                        task_id,
                        "provider_event",
                        {"kind": event.kind.value, "data": event.data},
                    )
                    if event.kind is ProviderEventKind.TURN_COMPLETED:
                        turn_completed = True
                        break
                    if event.kind is ProviderEventKind.CANCELLED:
                        self.store.transition(
                            task_id, TaskState.CANCELLED, reason="provider_cancelled"
                        )
                        return
                    if event.kind is ProviderEventKind.FAILED:
                        self.store.transition(task_id, TaskState.FAILED, reason="provider_failed")
                        return
                if not turn_completed:
                    current = self.store.get_task(task_id)
                    if current.state not in TERMINAL_STATES:
                        self.store.transition(
                            task_id, TaskState.INTERRUPTED, reason="provider_stream_ended"
                        )
                    return
                try:
                    provider_checkpoint = await self.provider.checkpoint(session)
                    tree_hash = self.workspace_broker.current_tree_hash(
                        self.store.get_task(task_id).workspace_id or ""
                    )
                    self.store.create_checkpoint(
                        task_id=task_id,
                        workspace_tree_hash=tree_hash,
                        payload={
                            "phase": "testing",
                            "completed_turns": turns,
                            "provider": provider_checkpoint.model_dump(mode="json"),
                            "context_summary": self._context_summary(
                                task_id,
                                tree_hash=tree_hash,
                                public_output=str(
                                    provider_checkpoint.payload.get("public_output", "")
                                ),
                            ),
                        },
                    )
                except Exception:
                    self.store.transition(
                        task_id,
                        TaskState.BLOCKED,
                        reason="checkpoint_failed",
                        expected_state=TaskState.RUNNING,
                    )
                    return
                feedback = await self._evaluate_acceptance(task, turns)
                if feedback is None:
                    return
                message = feedback

    def _context_summary(
        self, task_id: str, *, tree_hash: str, public_output: str
    ) -> dict[str, object]:
        task = self.store.get_task(task_id)
        latest: dict[str, WorkerEvidence] = {}
        for item in self.store.list_evidence(task_id, current_tree_hash=tree_hash):
            latest[item.check_id] = item
        failures = [
            {
                "check_id": item.check_id,
                "evidence_id": item.evidence_id,
                "artifact_id": item.artifact_id,
                "exit_code": item.exit_code,
            }
            for item in latest.values()
            if item.status is EvidenceStatus.FAILED
        ]
        return {
            "version": 1,
            "objective": task.spec.objective,
            "required_checks": [
                item.check_id for item in task.spec.acceptance.required_checks
            ],
            "required_artifacts": [
                item.artifact_id for item in task.spec.acceptance.required_artifacts
            ],
            "state": task.state.value,
            "workspace_tree_hash": tree_hash,
            "failure_evidence": failures,
            "public_output": public_output[-16_384:],
            "next_step": "run_required_acceptance",
        }

    @staticmethod
    def _restored_context_message(
        summary: dict[str, object] | None, feedback: str
    ) -> str:
        if summary is None:
            return feedback
        objective = summary.get("objective")
        checks = summary.get("required_checks")
        if not isinstance(objective, str) or not isinstance(checks, list) or not all(
            isinstance(item, str) for item in checks
        ):
            raise WorkerConflictError(
                "Checkpoint context is invalid.", code="checkpoint_invalid"
            )
        public_output = summary.get("public_output")
        prior = public_output if isinstance(public_output, str) else ""
        text = (
            "Restored public task context. Hidden reasoning and raw provider frames were "
            "not persisted.\n"
            f"Objective: {objective}\n"
            f"Required checks: {', '.join(checks)}\n"
        )
        if prior:
            text += f"Last public provider output:\n{prior}\n"
        return (text + feedback)[:32_768]

    async def _evaluate_acceptance(self, task: TaskRecord, turns: int) -> str | None:
        task_id = task.task_id
        self.store.transition(
            task_id, TaskState.TESTING, expected_state=TaskState.RUNNING
        )
        if self.harness_runner is None:
            self.store.transition(
                task_id,
                TaskState.BLOCKED,
                reason="acceptance_runner_pending",
                expected_state=TaskState.TESTING,
            )
            return None
        try:
            evidence = await self.harness_runner.run_required_checks(task_id)
        except ToolBrokerError as exc:
            self.store.transition(
                task_id,
                TaskState.BLOCKED,
                reason=exc.code,
                expected_state=TaskState.TESTING,
            )
            return None
        self.store.append_event(
            task_id,
            "acceptance_evaluated",
            {
                "turn": turns,
                "evidence": [
                    {
                        "check_id": item.check_id,
                        "status": item.status.value,
                        "artifact_id": item.artifact_id,
                    }
                    for item in evidence
                ],
            },
        )
        if self.harness_runner.acceptance_satisfied(task_id):
            self.store.transition(
                task_id,
                TaskState.COMPLETED,
                expected_state=TaskState.TESTING,
            )
            return None
        if turns >= task.spec.budget.max_turns:
            self.store.transition(
                task_id,
                TaskState.BUDGET_LIMITED,
                reason="turn_budget_exhausted",
                expected_state=TaskState.TESTING,
            )
            return None
        message = self._acceptance_feedback(task_id, evidence)
        self.store.append_message(task_id, role="system", content=message)
        self.store.append_event(task_id, "acceptance_retry", {"turn": turns + 1})
        self.store.transition(
            task_id,
            TaskState.RUNNING,
            reason="acceptance_failed",
            expected_state=TaskState.TESTING,
        )
        return message

    def _acceptance_feedback(
        self, task_id: str, evidence: tuple[WorkerEvidence, ...]
    ) -> str:
        task = self.store.get_task(task_id)
        lines = [
            "Required acceptance checks are not yet satisfied.",
            "Fix the workspace without weakening or rewriting the acceptance contract, "
            "then finish the turn for an exact retest.",
        ]
        for item in evidence:
            if item.status is EvidenceStatus.PASSED:
                continue
            output = self.store.read_artifact(item.artifact_id, task_id=task_id)
            excerpt = output.decode("utf-8", errors="replace")[:4000]
            lines.append(
                f"\nCheck {item.check_id} failed with exit code {item.exit_code}:\n{excerpt}"
            )
        current_hash = self.workspace_broker.current_tree_hash(task.workspace_id or "")
        artifacts = self.store.list_artifacts(task_id)
        supplied = {
            str(item.metadata.get("requirement_id"))
            for item in artifacts
            if item.metadata.get("workspace_tree_hash") == current_hash
        }
        missing = [
            item.artifact_id
            for item in task.spec.acceptance.required_artifacts
            if item.artifact_id not in supplied
        ]
        if missing:
            lines.append("\nMissing required artifacts: " + ", ".join(missing))
        return "\n".join(lines)[:16_384]
