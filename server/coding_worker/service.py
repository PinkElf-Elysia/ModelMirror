from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    ApprovalStatus,
    EvidenceStatus,
    OperationState,
    Origin,
    PolicyProfile,
    QuestionStatus,
    RuntimeProtocol,
    SAFE_ID,
    TaskCreateRequest,
    TaskBudget,
    TaskRecord,
    TaskSpec,
    TaskState,
    TurnBarrier,
    TurnTransactionState,
    SubtaskKind,
    SubtaskMergeState,
    SubtaskRecord,
    SubtaskRequest,
    TERMINAL_STATES,
    WorkerEvidence,
    WorkerQuestion,
    WorkerQuestionAnswer,
    WorkerQuestionOption,
    WorkerTurnHistory,
    WorkerTaskExport,
    SessionLedgerKind,
)
from .evidence import HarnessRunner
from .provider import (
    CodingAgentProvider,
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderEvent,
    ProviderEventKind,
    ProviderOpenRequest,
    ProviderSession,
    provider_tools_for_policy,
)
from .store import CodingWorkerStore, WorkerConflictError
from .changeset import ChangesetError
from .tool_broker import ToolBroker, ToolBrokerError
from .workspace import WorkspaceBroker, WorkspaceError, WorkspaceSnapshot


PROVIDER_CAPABILITY_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class ProviderCapabilityObservation:
    capabilities: ProviderCapabilities | None
    binding_sha256: str
    observed_at: float
    expires_at: float
    reason: str | None


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
        route_slots: Mapping[str, Sequence[str]] | None = None,
        route_context_tokens: Mapping[str, int] | None = None,
    ) -> None:
        if not 1 <= max_active_tasks <= 16:
            raise ValueError("active task capacity is outside the allowed range")
        self.store = store
        self.workspace_broker = workspace_broker
        self.provider = provider
        self.harness_runner = harness_runner
        self.max_active_tasks = max_active_tasks
        self.tool_broker = tool_broker
        self._route_slots = (
            {
                route_id: tuple(dict.fromkeys(slot_ids))
                for route_id, slot_ids in route_slots.items()
            }
            if route_slots is not None
            else None
        )
        if self._route_slots is not None:
            known_slots = set(self.workspace_broker.slot_ids)
            if any(
                not route_id
                or not slot_ids
                or not set(slot_ids).issubset(known_slots)
                for route_id, slot_ids in self._route_slots.items()
            ):
                raise ValueError("provider route slot configuration is invalid")
        self._route_context_tokens = dict(route_context_tokens or {})
        if any(
            not route_id
            or isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or not 8_192 <= tokens <= 2_000_000
            for route_id, tokens in self._route_context_tokens.items()
        ):
            raise ValueError("provider route context configuration is invalid")
        self._active: dict[str, asyncio.Task[None]] = {}
        self._task_slots: dict[str, str] = {}
        self._sessions: dict[str, ProviderSession] = {}
        self._wake = asyncio.Event()
        self._scheduler: asyncio.Task[None] | None = None
        self._capability_refresher: asyncio.Task[None] | None = None
        self._capability_lock = asyncio.Lock()
        self._route_capabilities: dict[str, ProviderCapabilityObservation] = {}
        self._started = False
        self._closing = False

    @property
    def active_task_ids(self) -> frozenset[str]:
        return frozenset(self._active)

    @staticmethod
    def _subtasks_enabled() -> bool:
        enabled = {"1", "true", "yes", "on"}
        return (
            os.getenv("CODING_WORKER_V16_ENABLED", "false").strip().lower()
            in enabled
            and os.getenv("CODING_WORKER_SUBAGENTS_ENABLED", "false")
            .strip()
            .lower()
            in enabled
        )

    @staticmethod
    def _runtime_protocol() -> RuntimeProtocol:
        enabled = {"1", "true", "yes", "on"}
        required = (
            "CODING_WORKER_V16_ENABLED",
            "CODING_WORKER_INTERACTION_ENABLED",
            "CODING_WORKER_SESSION_CONTROLS_ENABLED",
            "CODING_WORKER_SUBAGENTS_ENABLED",
            "CODING_WORKER_V17_ENABLED",
        )
        return (
            RuntimeProtocol.V17
            if all(
                os.getenv(name, "false").strip().lower() in enabled
                for name in required
            )
            else RuntimeProtocol.V16
        )

    @staticmethod
    def _subtask_role_contract(kind: SubtaskKind) -> str:
        common = (
            "You are a depth-one, platform-owned coding subtask. Do not create "
            "another subtask. You receive no parent approval, network lease, "
            "operation id, budget, artifact, provider session, or hidden context. "
            "Return a concise public summary with findings and changed paths."
        )
        if kind is SubtaskKind.EXPLORE:
            return common + " Explore only: the workspace is strictly read-only."
        if kind is SubtaskKind.REVIEW:
            return common + " Review only: the workspace is strictly read-only."
        return (
            common
            + " Implement only the delegated objective in this isolated fork. "
            "The parent will merge by preimage and tree CAS and rerun acceptance."
        )

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._closing = False
        self._scheduler = asyncio.create_task(
            self._scheduler_loop(), name="coding-worker-scheduler"
        )
        self._capability_refresher = asyncio.create_task(
            self._capability_refresh_loop(),
            name="coding-worker-capabilities",
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
        if self._capability_refresher is not None:
            self._capability_refresher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._capability_refresher
        self._active.clear()
        self._task_slots.clear()
        self._sessions.clear()
        self._scheduler = None
        self._capability_refresher = None
        self._started = False

    async def create_task(self, origin: Origin, request: TaskCreateRequest) -> TaskRecord:
        if self._route_slots is not None and request.model_route not in self._route_slots:
            raise WorkerConflictError(
                "Model route is unavailable.", code="model_route_unavailable"
            )
        frozen_checks = getattr(self.tool_broker, "frozen_checks", None)
        if isinstance(frozen_checks, Mapping):
            unknown = [
                check.check_id
                for check in request.acceptance.required_checks
                if check.kind == "command"
                and check.check_id not in frozen_checks
            ]
            if unknown:
                raise WorkerConflictError(
                    "Acceptance check is not registered.",
                    code="worker_acceptance_not_registered",
                )
        await self.start()
        spec = TaskSpec(**request.model_dump(), origin=origin)
        observation = await self.provider_capability_observation(
            request.model_route, force=True
        )
        runtime_protocol = self._runtime_protocol()
        if runtime_protocol is RuntimeProtocol.V17 and not self._v17_route_ready(
            observation.capabilities
        ):
            raise WorkerConflictError(
                "Model route does not support the V17 interaction contract.",
                code="v17_route_unavailable",
            )
        task = self.store.create_task(
            spec,
            runtime_protocol=runtime_protocol,
            capability_binding_sha256=observation.binding_sha256,
            capability_snapshot={
                "available": observation.capabilities is not None,
                "capabilities": (
                    observation.capabilities.model_dump(mode="json")
                    if observation.capabilities is not None
                    else None
                ),
            },
            capability_observed_at=observation.observed_at,
            capability_expires_at=observation.expires_at,
        )
        self._wake.set()
        return task

    @staticmethod
    def _v17_route_ready(capabilities: ProviderCapabilities | None) -> bool:
        if capabilities is None:
            return False
        required_tools = {
            "update_plan",
            "update_todo",
            "request_user_input",
            "compact_context",
        }
        return (
            capabilities.supports_checkpoint
            and capabilities.supports_restore
            and capabilities.supports_turn_interrupt
            and required_tools.issubset(capabilities.tool_names)
        )

    async def provider_capability_observation(
        self, model_route: str, *, force: bool = False
    ) -> "ProviderCapabilityObservation":
        await self.refresh_provider_capabilities(force=force)
        observation = self._route_capabilities.get(model_route)
        if observation is not None:
            return observation
        default = self._route_capabilities.get("*")
        if default is not None:
            return ProviderCapabilityObservation(
                capabilities=default.capabilities,
                binding_sha256=self._capability_binding(model_route, ("*",)),
                observed_at=default.observed_at,
                expires_at=default.expires_at,
                reason=default.reason,
            )
        now = time.time()
        return ProviderCapabilityObservation(
            capabilities=None,
            binding_sha256=self._capability_binding(model_route, ()),
            observed_at=now,
            expires_at=now + PROVIDER_CAPABILITY_TTL_SECONDS,
            reason="route_unavailable",
        )

    def cached_provider_capabilities(self) -> tuple[ProviderCapabilities, ...]:
        """Return only live, explicitly reported route capabilities.

        This method deliberately does not perform I/O. HTTP handlers refresh the
        cache before using it, while synchronous callers fail closed when no
        recent provider observation exists.
        """

        now = time.time()
        return tuple(
            observation.capabilities
            for observation in self._route_capabilities.values()
            if observation.expires_at > now
            and observation.capabilities is not None
        )

    async def refresh_provider_capabilities(self, *, force: bool = False) -> None:
        now = time.time()
        if (
            not force
            and self._route_capabilities
            and all(item.expires_at > now for item in self._route_capabilities.values())
        ):
            return
        async with self._capability_lock:
            now = time.time()
            if (
                not force
                and self._route_capabilities
                and all(
                    item.expires_at > now
                    for item in self._route_capabilities.values()
                )
            ):
                return
            observations: dict[str, ProviderCapabilityObservation] = {}
            slot_reader = getattr(self.provider, "slot_capabilities", None)
            if self._route_slots is not None and callable(slot_reader):
                slot_values = await slot_reader()
                for route_id, slot_ids in self._route_slots.items():
                    values = [slot_values.get(slot_id) for slot_id in slot_ids]
                    capabilities = (
                        _intersect_provider_capabilities(
                            tuple(value for value in values if value is not None)
                        )
                        if values and all(value is not None for value in values)
                        else None
                    )
                    observations[route_id] = ProviderCapabilityObservation(
                        capabilities=capabilities,
                        binding_sha256=self._capability_binding(route_id, slot_ids),
                        observed_at=now,
                        expires_at=now + PROVIDER_CAPABILITY_TTL_SECONDS,
                        reason=(
                            None
                            if capabilities is not None
                            else "provider_unavailable"
                        ),
                    )
            else:
                try:
                    capabilities = await self.provider.capabilities()
                    reason = None
                except Exception:
                    capabilities = None
                    reason = "provider_unavailable"
                observations["*"] = ProviderCapabilityObservation(
                    capabilities=capabilities,
                    binding_sha256=self._capability_binding("*", ("*",)),
                    observed_at=now,
                    expires_at=now + PROVIDER_CAPABILITY_TTL_SECONDS,
                    reason=reason,
                )
            self._route_capabilities = observations

    async def _capability_refresh_loop(self) -> None:
        while not self._closing:
            with contextlib.suppress(Exception):
                await self.refresh_provider_capabilities(force=True)
            await asyncio.sleep(PROVIDER_CAPABILITY_TTL_SECONDS)

    def _capability_binding(
        self, route_id: str, slot_ids: Sequence[str]
    ) -> str:
        generation = getattr(self.provider, "controller_generation", 0)
        encoded = json.dumps(
            {
                "route": route_id,
                "slots": list(slot_ids),
                "generation": generation,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

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
        turn = self.store.current_turn_transaction(task_id)
        if task.runtime_protocol is RuntimeProtocol.V17 and turn is not None:
            if turn.state is TurnTransactionState.PARKING:
                raise WorkerConflictError(
                    "Turn checkpoint is not durable yet.", code="turn_not_parked"
                )
            if turn.state is TurnTransactionState.PARKED:
                if turn.checkpoint_id is None:
                    raise WorkerConflictError(
                        "Parked turn has no checkpoint.", code="turn_checkpoint_invalid"
                    )
                if turn.barrier in {
                    TurnBarrier.APPROVAL,
                    TurnBarrier.INPUT,
                    TurnBarrier.SUBTASKS,
                }:
                    raise WorkerConflictError(
                        "Turn still requires user settlement.",
                        code="turn_barrier_unresolved",
                    )
                resumed = self.store.settle_parked_turn(
                    task_id=task_id,
                    barrier=TurnBarrier.OPERATION_UNKNOWN,
                    expected_state=TaskState.INTERRUPTED,
                )
                self._wake.set()
                return resumed
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
            TaskState.WAITING_INPUT,
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
        if active is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await active
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
        if active is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await active
        self._wake.set()
        return cancelled

    async def append_message(self, task_id: str, text: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.state not in {TaskState.RUNNING, TaskState.WAITING_APPROVAL, TaskState.PAUSED}:
            raise WorkerConflictError("Task is not accepting messages.", code="task_state_conflict")
        self.store.append_message(task_id, role="user", content=text)
        self.store.append_event(task_id, "steering_queued", {})
        return self.store.get_task(task_id)

    async def answer_question(
        self, task_id: str, question_id: str, answer: WorkerQuestionAnswer
    ) -> WorkerQuestion:
        resolved = self.store.resolve_question(task_id, question_id, answer)
        self._wake.set()
        return resolved

    async def create_subtask(
        self, parent_task_id: str, request: SubtaskRequest
    ) -> SubtaskRecord:
        if not self._subtasks_enabled():
            raise WorkerConflictError(
                "Controlled subtasks are disabled.", code="subtasks_disabled"
            )
        existing = self.store.get_subtask(
            parent_task_id, request.client_subtask_id
        )
        if existing is not None:
            if existing.kind is not request.kind or existing.objective != request.objective:
                raise WorkerConflictError(
                    "Subtask id is bound to another intent.",
                    code="subtask_intent_conflict",
                )
            return existing
        relations = self.store.list_subtasks(parent_task_id)
        if request.kind is SubtaskKind.IMPLEMENT and any(
            relation.kind is SubtaskKind.IMPLEMENT
            and relation.merge_state is SubtaskMergeState.READY
            for relation in relations
        ):
            raise WorkerConflictError(
                "A ready implementation result must be merged or resolved before "
                "another implementation subtask is created.",
                code="subtask_merge_required",
            )
        for relation in relations:
            if (
                relation.kind is request.kind
                and relation.objective == request.objective
                and relation.merge_state
                in {
                    SubtaskMergeState.PENDING,
                    SubtaskMergeState.READY,
                }
            ):
                raise WorkerConflictError(
                    "An equivalent subtask is already active.",
                    code="subtask_duplicate_intent",
                )
        parent = self.store.get_task(parent_task_id)
        if parent.workspace_id is None or parent.state not in {
            TaskState.RUNNING,
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.BLOCKED,
        }:
            raise WorkerConflictError(
                "Task cannot create a subtask in its current state.",
                code="task_state_conflict",
            )
        base_tree_hash = self.workspace_broker.current_tree_hash(parent.workspace_id)
        target_slot: str | None = None
        if self.workspace_broker.dedicated_slots:
            parent_slot = self.workspace_broker.workspace_slot(parent.workspace_id)
            candidates = tuple(
                slot
                for slot in self.workspace_broker.slot_ids
                if slot != parent_slot
            ) + (parent_slot,)
            target_slot = candidates[
                len(relations) % len(candidates)
            ]
        workspace = self.workspace_broker.fork(
            parent.workspace_id,
            expected_tree_hash=base_tree_hash,
            slot_id=target_slot,
        )
        digest = hashlib.sha256(
            f"{parent_task_id}\0{request.client_subtask_id}".encode("utf-8")
        ).hexdigest()
        role_contract = self._subtask_role_contract(request.kind)
        child_spec = TaskSpec(
            client_task_id=f"subtask-{digest[:32]}",
            objective=(
                role_contract
                + "\n\nParent objective (context only):\n"
                + parent.spec.objective[:16_384]
                + "\n\nDelegated objective:\n"
                + request.objective
            )[:1_048_576],
            workspace_source=parent.spec.workspace_source,
            acceptance=AcceptanceContract(
                contract_id=f"subtask-{digest[:32]}",
                required_checks=(
                    AcceptanceCheck(
                        check_id="subtask-result",
                        label="Return one structured subtask result",
                        kind="custom",
                    ),
                ),
            ),
            policy_profile=(
                PolicyProfile.DEVELOP
                if request.kind is SubtaskKind.IMPLEMENT
                else PolicyProfile.INSPECT
            ),
            model_route=parent.spec.model_route,
            budget=TaskBudget(
                max_seconds=900,
                max_turns=8,
                max_tool_calls=64,
                max_output_bytes=8 * 1024 * 1024,
            ),
            context_refs=(),
            origin=Origin(
                module="coding-worker-subtask",
                object_id=parent_task_id,
            ),
        )
        try:
            parent_turn_id: str | None = None
            if parent.runtime_protocol is RuntimeProtocol.V17:
                turn = self.store.current_turn_transaction(parent_task_id)
                if turn is None:
                    raise WorkerConflictError(
                        "V17 subtask has no current turn.",
                        code="operation_turn_required",
                    )
                parent_turn_id = turn.turn_id
            subtask = self.store.create_subtask_task(
                parent_task_id=parent_task_id,
                client_subtask_id=request.client_subtask_id,
                kind=request.kind,
                objective=request.objective,
                spec=child_spec,
                workspace_id=workspace.workspace_id,
                base_tree_hash=base_tree_hash,
                parent_turn_id=parent_turn_id,
            )
        except Exception:
            self.workspace_broker.delete(workspace.workspace_id)
            raise
        if self.store.get_task(subtask.child_task_id).workspace_id != workspace.workspace_id:
            self.workspace_broker.delete(workspace.workspace_id)
        current_parent = self.store.get_task(parent_task_id)
        if current_parent.runtime_protocol is RuntimeProtocol.V17:
            self._wake.set()
            return subtask
        if current_parent.state in {
            TaskState.RUNNING,
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.BLOCKED,
        }:
            self.store.transition(
                parent_task_id,
                TaskState.WAITING_SUBTASKS,
                reason="subtasks_running",
                expected_state=current_parent.state,
            )
        self._wake.set()
        return subtask

    async def merge_subtask(
        self,
        parent_task_id: str,
        child_task_id: str,
        operation_id: str,
    ) -> SubtaskRecord:
        if not self._subtasks_enabled():
            raise WorkerConflictError(
                "Controlled subtasks are disabled.", code="subtasks_disabled"
            )
        parent = self.store.get_task(parent_task_id)
        if parent.workspace_id is None or parent.state not in {
            TaskState.RUNNING,
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.BLOCKED,
        }:
            raise WorkerConflictError(
                "Task cannot merge a subtask in its current state.",
                code="task_state_conflict",
            )
        relation = self.store.begin_subtask_merge(
            parent_task_id, child_task_id, operation_id
        )
        changesets = self._require_changesets()
        if relation.merge_state is SubtaskMergeState.MERGED:
            with contextlib.suppress(ChangesetError):
                if changesets.has_transaction(
                    workspace_id=parent.workspace_id, operation_id=operation_id
                ):
                    changesets.finalize(
                        task_id=parent_task_id,
                        workspace_id=parent.workspace_id,
                        operation_id=operation_id,
                    )
            return relation
        if relation.merge_state is SubtaskMergeState.CONFLICTED:
            return relation
        child = self.store.get_task(child_task_id)
        if child.workspace_id is None or relation.result_tree_hash is None:
            raise WorkerConflictError(
                "Subtask result is unavailable.", code="subtask_not_mergeable"
            )
        outcome = None
        if changesets.has_transaction(
            workspace_id=parent.workspace_id, operation_id=operation_id
        ):
            try:
                outcome = changesets.reconcile(
                    task_id=parent_task_id,
                    workspace_id=parent.workspace_id,
                    operation_id=operation_id,
                )
            except ChangesetError as exc:
                if exc.code != "changeset_rolled_back":
                    raise WorkerConflictError(str(exc), code=exc.code) from exc
        if outcome is None:
            try:
                changes = self.workspace_broker.fork_merge_changes(
                    child.workspace_id,
                    expected_base_tree_hash=relation.base_tree_hash,
                    expected_result_tree_hash=relation.result_tree_hash,
                    expected_changed_paths=relation.changed_paths,
                )
                current_tree_hash = self.workspace_broker.current_tree_hash(
                    parent.workspace_id
                )
                if not changes:
                    return self.store.settle_subtask_merge(
                        parent_task_id,
                        child_task_id,
                        operation_id,
                        merge_state=SubtaskMergeState.MERGED,
                        merged_tree_hash=current_tree_hash,
                    )
                outcome = changesets.apply(
                    task_id=parent_task_id,
                    workspace_id=parent.workspace_id,
                    operation_id=operation_id,
                    arguments={
                        "base_tree_hash": current_tree_hash,
                        "changes": changes,
                    },
                )
            except (ChangesetError, WorkspaceError) as exc:
                if getattr(exc, "code", "") in {
                    "operation_result_unknown",
                    "changeset_rollback_failed",
                }:
                    raise WorkerConflictError(str(exc), code=exc.code) from exc
                current_tree_hash = self.workspace_broker.current_tree_hash(
                    parent.workspace_id
                )
                return self.store.settle_subtask_merge(
                    parent_task_id,
                    child_task_id,
                    operation_id,
                    merge_state=SubtaskMergeState.CONFLICTED,
                    merged_tree_hash=current_tree_hash,
                )
        if outcome.result_tree_hash is None:
            raise WorkerConflictError(
                "Subtask merge result is unknown.", code="operation_result_unknown"
            )
        settled = self.store.settle_subtask_merge(
            parent_task_id,
            child_task_id,
            operation_id,
            merge_state=SubtaskMergeState.MERGED,
            merged_tree_hash=outcome.result_tree_hash,
        )
        with contextlib.suppress(ChangesetError):
            changesets.finalize(
                task_id=parent_task_id,
                workspace_id=parent.workspace_id,
                operation_id=operation_id,
            )
        return settled

    async def navigate_turn(self, task_id: str, action: str) -> WorkerTurnHistory:
        task = await self._require_session_control_safe(task_id)
        assert task.workspace_id is not None
        checkpoint, cursor, source_hash, target_hash, target_oid = (
            self.store.begin_turn_navigation(task_id, action)
        )
        current_hash = self.workspace_broker.current_tree_hash(task.workspace_id)
        operation_id = self._turn_navigation_operation_id(
            task_id, checkpoint.checkpoint_id, action
        )
        changesets = self._require_changesets()
        if changesets.has_transaction(
            workspace_id=task.workspace_id, operation_id=operation_id
        ):
            try:
                outcome = changesets.reconcile(
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    operation_id=operation_id,
                )
            except ChangesetError as exc:
                if exc.code != "changeset_rolled_back":
                    raise WorkerConflictError(str(exc), code=exc.code) from exc
            else:
                if outcome.result_tree_hash != target_hash:
                    raise WorkerConflictError(
                        "Turn navigation receipt changed.",
                        code="turn_navigation_conflict",
                    )
                changesets.finalize(
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    operation_id=operation_id,
                )
                current_hash = target_hash
        if current_hash == source_hash:
            try:
                outcome = changesets.restore_snapshot(
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    operation_id=operation_id,
                    expected_tree_hash=source_hash,
                    snapshot=WorkspaceSnapshot(
                        tree_hash=target_hash, tree_oid=target_oid
                    ),
                )
                if outcome.result_tree_hash != target_hash:
                    raise WorkerConflictError(
                        "Turn navigation produced another tree.",
                        code="turn_navigation_conflict",
                    )
                changesets.finalize(
                    task_id=task_id,
                    workspace_id=task.workspace_id,
                    operation_id=operation_id,
                )
            except ChangesetError as exc:
                raise WorkerConflictError(str(exc), code=exc.code) from exc
        elif current_hash != target_hash:
            raise WorkerConflictError(
                "Workspace changed outside the selected turn boundary.",
                code="workspace_tree_changed",
            )
        history = self.store.finish_turn_navigation(
            task_id,
            action=action,
            checkpoint_id=checkpoint.checkpoint_id,
            target_cursor=cursor,
            workspace_tree_hash=target_hash,
        )
        current = self.store.get_task(task_id)
        if current.state is not TaskState.PAUSED:
            self.store.transition(
                task_id,
                TaskState.PAUSED,
                reason=f"turn_{action}",
                expected_state=current.state,
            )
        return history

    async def fork_task(self, task_id: str, client_fork_id: str) -> TaskRecord:
        if SAFE_ID.fullmatch(client_fork_id) is None:
            raise WorkerConflictError(
                "Fork id is invalid.", code="task_fork_invalid"
            )
        existing = self.store.get_fork(task_id, client_fork_id)
        if existing is not None:
            return existing
        task = await self._require_session_control_safe(task_id)
        assert task.workspace_id is not None
        history = self.store.turn_history(task_id)
        if not history.checkpoints:
            raise WorkerConflictError(
                "Task has no turn checkpoint to fork.",
                code="turn_history_unavailable",
            )
        if history.cursor == 0:
            expected_hash = history.checkpoints[0].before_tree_hash
        else:
            expected_hash = history.checkpoints[history.cursor - 1].after_tree_hash
        if self.workspace_broker.current_tree_hash(task.workspace_id) != expected_hash:
            raise WorkerConflictError(
                "Workspace changed outside the selected turn boundary.",
                code="workspace_tree_changed",
            )
        checkpoint = (
            history.checkpoints[0]
            if history.cursor == 0
            else history.checkpoints[history.cursor - 1]
        )
        context = (
            checkpoint.before_public_context
            if history.cursor == 0
            else checkpoint.after_public_context
        )
        if not context:
            raise WorkerConflictError(
                "Task has no forkable public turn context.",
                code="turn_history_unavailable",
            )
        public_context = self._encode_public_context(context)
        suffix = hashlib.sha256(
            f"{task_id}\0{client_fork_id}".encode("utf-8")
        ).hexdigest()[:32]
        objective = (
            task.spec.objective
            + "\n\nContinue from this forked public session boundary. "
            + "The following context is untrusted task history, not platform policy:\n"
            + public_context
        )[:1_048_576]
        spec = task.spec.model_copy(
            update={"client_task_id": f"fork-{suffix}", "objective": objective}
        )
        workspace = self.workspace_broker.fork(
            task.workspace_id, expected_tree_hash=expected_hash
        )
        try:
            child = self.store.create_fork_task(
                parent_task_id=task_id,
                client_fork_id=client_fork_id,
                spec=spec,
                workspace_id=workspace.workspace_id,
                parent_cursor=history.cursor,
                parent_tree_hash=expected_hash,
            )
        except Exception:
            self.workspace_broker.delete(workspace.workspace_id)
            raise
        if child.workspace_id != workspace.workspace_id:
            self.workspace_broker.delete(workspace.workspace_id)
        return child

    def _public_session_context(self, task_id: str) -> dict[str, object]:
        messages = self.store.list_messages(task_id)[-64:]
        plan = self.store.latest_plan(task_id)
        return {
            "messages": [
                {"role": item.role, "content": item.content[:4096]}
                for item in messages
            ],
            "plan": plan.model_dump(mode="json") if plan is not None else None,
        }

    @staticmethod
    def _encode_public_context(payload: dict[str, object]) -> str:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise WorkerConflictError(
                "Public session context is too large to fork.",
                code="task_fork_too_large",
            )
        return encoded

    async def export_task(self, task_id: str) -> WorkerTaskExport:
        task = await self._require_session_control_safe(task_id)
        assert task.workspace_id is not None
        history = self.store.turn_history(task_id)
        if history.checkpoints:
            checkpoint = (
                history.checkpoints[0]
                if history.cursor == 0
                else history.checkpoints[history.cursor - 1]
            )
            public_context = (
                checkpoint.before_public_context
                if history.cursor == 0
                else checkpoint.after_public_context
            )
        else:
            public_context = self._public_session_context(task_id)
        ledger: list[object] = []
        cursor = 0
        while True:
            page = self.store.list_session_ledger(task_id, after=cursor, limit=1000)
            ledger.extend(page)
            if len(ledger) > 4096:
                raise WorkerConflictError(
                    "Public session is too large to export.", code="task_export_too_large"
                )
            if len(page) < 1000:
                break
            cursor = page[-1].sequence
        diff = self.workspace_broker.diff(task.workspace_id)
        artifacts = tuple(
            {
                "artifact_id": item.artifact_id,
                "media_type": item.media_type,
                "sha256": item.sha256,
                "size": item.size,
                "metadata": self._sanitize_export_value(item.metadata),
                "created_at": item.created_at,
            }
            for item in self.store.list_artifacts(task_id)
        )
        exported = WorkerTaskExport(
            task=task,
            public_context=public_context,
            session_ledger=tuple(ledger),
            questions=tuple(self.store.list_questions(task_id)),
            turn_history=history,
            evidence=tuple(
                self.store.list_evidence(
                    task_id,
                    current_tree_hash=self.workspace_broker.current_tree_hash(
                        task.workspace_id
                    ),
                )
            ),
            artifact_index=artifacts,
            workspace_tree_hash=self.workspace_broker.current_tree_hash(
                task.workspace_id
            ),
            workspace_diff_sha256=hashlib.sha256(diff).hexdigest(),
            workspace_diff_base64=base64.b64encode(diff).decode("ascii"),
            created_at=time.time(),
        )
        if len(exported.model_dump_json().encode("utf-8")) > 16 * 1024 * 1024:
            raise WorkerConflictError(
                "Task export is too large.", code="task_export_too_large"
            )
        return exported

    @classmethod
    def _sanitize_export_value(cls, value: object) -> object:
        forbidden = {
            "endpoint",
            "environment",
            "physical_path",
            "provider",
            "provider_session_id",
            "remote_url",
            "secret",
            "token",
            "credential",
        }
        if isinstance(value, dict):
            return {
                str(key): cls._sanitize_export_value(item)
                for key, item in value.items()
                if str(key).lower() not in forbidden
                and not str(key).lower().endswith(("_token", "_secret", "_credential"))
            }
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_export_value(item) for item in value]
        if isinstance(value, str) and (
            re.match(r"^[A-Za-z]:[\\/]", value)
            or value.startswith(("/", "unix:", "http://", "https://"))
        ):
            return "[redacted]"
        return value

    async def _require_session_control_safe(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.workspace_id is None or task.state not in {
            TaskState.PAUSED,
            TaskState.INTERRUPTED,
            TaskState.COMPLETED,
            TaskState.BLOCKED,
            TaskState.FAILED,
            TaskState.BUDGET_LIMITED,
        }:
            raise WorkerConflictError(
                "Task is not at a safe session boundary.",
                code="session_control_unavailable",
            )
        active = self._active.get(task_id)
        if active is not None and not active.done():
            raise WorkerConflictError(
                "Task still owns an active runner.", code="session_control_busy"
            )
        operations = self.store.list_operations(task_id)
        if any(
            item.state in {
                OperationState.PREPARED,
                OperationState.RUNNING,
                OperationState.UNKNOWN,
            }
            for item in operations
        ):
            raise WorkerConflictError(
                "Task has an unsettled tool operation.", code="session_control_busy"
            )
        if any(
            item.status is ApprovalStatus.PENDING
            for item in self.store.list_approvals(task_id)
        ) or self.store.has_active_lease(task_id):
            raise WorkerConflictError(
                "Task has a pending approval.", code="session_control_busy"
            )
        if self.tool_broker is not None and self.tool_broker.process_manager is not None:
            if any(
                item.state == "running"
                for item in self.tool_broker.process_manager.list(task_id)
            ):
                raise WorkerConflictError(
                    "Task has a running service.", code="session_control_busy"
                )
        if self.tool_broker is not None and self.tool_broker.executor is not None:
            stopped_services = {
                str(operation.result["service_id"])
                for operation in operations
                if operation.tool_name == "stop_service"
                and operation.state is OperationState.COMPLETED
                and isinstance(operation.result, dict)
                and isinstance(operation.result.get("service_id"), str)
            }
            for operation in operations:
                if (
                    operation.tool_name != "start_service"
                    or operation.state is not OperationState.COMPLETED
                    or not isinstance(operation.result, dict)
                ):
                    continue
                service_id = operation.result.get("service_id")
                if not isinstance(service_id, str) or not service_id:
                    raise WorkerConflictError(
                        "Task service receipt is incomplete.",
                        code="session_control_busy",
                    )
                if service_id in stopped_services:
                    continue
                try:
                    status = await self.tool_broker.executor.service_status(
                        task_id=task_id,
                        workspace_id=task.workspace_id,
                        service_id=service_id,
                    )
                except Exception as exc:
                    if getattr(exc, "code", None) == "service_not_found":
                        continue
                    raise WorkerConflictError(
                        "Task service state is unknown.",
                        code="session_control_busy",
                    ) from exc
                if status.get("state") == "running":
                    raise WorkerConflictError(
                        "Task has a running service.", code="session_control_busy"
                    )
        return task

    def _require_changesets(self):
        if self.tool_broker is None:
            raise WorkerConflictError(
                "Session controls require the Tool Broker.",
                code="session_control_unavailable",
            )
        return self.tool_broker.changesets

    @staticmethod
    def _turn_navigation_operation_id(
        task_id: str, checkpoint_id: str, action: str
    ) -> str:
        suffix = hashlib.sha256(
            f"{task_id}\0{checkpoint_id}\0{action}".encode("utf-8")
        ).hexdigest()[:32]
        return f"operation_{suffix}"

    def settle_approval_state(self, task_id: str) -> TaskRecord:
        """Leave a decided approval runnable only while its original runner exists."""
        task = self.store.get_task(task_id)
        if task.state is not TaskState.WAITING_APPROVAL:
            return task
        if task.runtime_protocol is RuntimeProtocol.V17:
            turn = self.store.current_turn_transaction(task_id)
            if (
                turn is None
                or turn.state is not TurnTransactionState.PARKED
                or turn.barrier is not TurnBarrier.APPROVAL
                or turn.checkpoint_id is None
            ):
                raise WorkerConflictError(
                    "Approval turn is not durably parked.", code="turn_not_parked"
                )
            self.store.resume_turn_transaction(
                task_id=task_id,
                turn_id=turn.turn_id,
                checkpoint_id=turn.checkpoint_id,
            )
            queued = self.store.transition(
                task_id,
                TaskState.QUEUED,
                expected_state=TaskState.WAITING_APPROVAL,
            )
            self._wake.set()
            return queued
        runner = self._active.get(task_id)
        if runner is not None and not runner.done():
            return self.store.transition(
                task_id,
                TaskState.RUNNING,
                expected_state=TaskState.WAITING_APPROVAL,
            )
        return self.store.transition(
            task_id,
            TaskState.INTERRUPTED,
            reason="approval_resume_required",
            expected_state=TaskState.WAITING_APPROVAL,
        )

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
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.25)
            except TimeoutError:
                # The queue is durable. Polling prevents a persisted runnable
                # task from depending on one in-memory wakeup surviving a
                # runner completion/settlement race.
                pass
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
        queued = [
            record
            for record in self.store.list_queued_tasks(limit=128)
            if record.task_id not in self._active
        ]
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
            route_slots = self._allowed_slots(record.spec.model_route)
            if route_slots is None:
                with contextlib.suppress(WorkerConflictError):
                    self.store.transition(
                        record.task_id,
                        TaskState.BLOCKED,
                        reason="model_route_unavailable",
                        expected_state=TaskState.QUEUED,
                    )
                continue
            required_slot: str | None = None
            if record.workspace_id is not None:
                try:
                    required_slot = self.workspace_broker.workspace_slot(
                        record.workspace_id
                    )
                except WorkspaceError:
                    # Let the runner persist the precise workspace failure.
                    required_slot = next(
                        (slot for slot in route_slots if slot in available), None
                    )
                if required_slot is None:
                    continue
                if required_slot not in route_slots:
                    with contextlib.suppress(WorkerConflictError):
                        self.store.transition(
                            record.task_id,
                            TaskState.BLOCKED,
                            reason="provider_binding_changed",
                            expected_state=TaskState.QUEUED,
                        )
                    continue
            if required_slot is None:
                selected = next(
                    (slot for slot in route_slots if slot in available), None
                )
                if selected is not None:
                    return record, selected
                continue
            if required_slot in available:
                return record, required_slot
        return None

    def _allowed_slots(self, model_route: str) -> tuple[str, ...] | None:
        if self._route_slots is None:
            return self.workspace_broker.slot_ids
        return self._route_slots.get(model_route)

    def _task_finished(self, task_id: str, _task: asyncio.Task[None]) -> None:
        owned_runner = self._active.get(task_id) is _task
        if owned_runner:
            self._active.pop(task_id, None)
            self._task_slots.pop(task_id, None)
            self._sessions.pop(task_id, None)
        if not self._closing:
            if owned_runner:
                self._resume_parent_after_subtasks(task_id)
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
                workspace_tree_hash=self.workspace_broker.current_tree_hash(
                    workspace.workspace_id
                ),
                repository_instructions=self.workspace_broker.repository_instructions(
                    workspace.workspace_id
                ),
                tool_allowlist=provider_tools_for_policy(task.spec.policy_profile),
            )
            resume_phase: str | None = None
            resume_context: dict[str, object] | None = None
            resume_question_id: str | None = None
            resume_turn_before: WorkspaceSnapshot | None = None
            resume_turn_public_before: dict[str, object] | None = None
            completed_turns = 0
            message_cursor = 0
            checkpoint = self.store.latest_checkpoint(task_id)
            uncheckpointed_turns = self._uncheckpointed_completed_turns(task_id)
            if uncheckpointed_turns:
                current_tree_hash = self.workspace_broker.current_tree_hash(
                    workspace.workspace_id
                )
                resume_phase = "testing"
                completed_turns = uncheckpointed_turns
                resume_context = self._context_summary(
                    task_id, tree_hash=current_tree_hash, public_output=""
                )
                session = await self.provider.open(request)
            elif checkpoint is not None:
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
                    message_cursor = int(checkpoint.payload.get("message_cursor", 0))
                    if message_cursor < 0:
                        raise ValueError("message cursor is invalid")
                    raw_context = checkpoint.payload.get("context_summary")
                    if raw_context is not None:
                        if not isinstance(raw_context, dict):
                            raise TypeError("context summary is invalid")
                        resume_context = raw_context
                    if resume_phase == "waiting_input":
                        resume_question_id = str(checkpoint.payload["question_id"])
                    raw_turn_before = checkpoint.payload.get("turn_before")
                    if raw_turn_before is not None:
                        resume_turn_before = WorkspaceSnapshot.model_validate(
                            raw_turn_before
                        )
                    raw_turn_public_before = checkpoint.payload.get(
                        "turn_public_before"
                    )
                    if raw_turn_public_before is not None:
                        if not isinstance(raw_turn_public_before, dict):
                            raise TypeError("turn public context is invalid")
                        resume_turn_public_before = raw_turn_public_before
                except (KeyError, TypeError, ValueError) as exc:
                    raise WorkerConflictError(
                        "Checkpoint payload is invalid.", code="checkpoint_invalid"
                    ) from exc
                if (
                    resume_phase
                    not in {
                        "turn_open",
                        "testing",
                        "waiting_approval",
                        "waiting_input",
                        "waiting_subtasks",
                        "compacted",
                        "operation_unknown",
                    }
                    or completed_turns < 1
                    or (resume_phase == "waiting_input" and not resume_question_id)
                ):
                    raise WorkerConflictError(
                        "Checkpoint phase is invalid.", code="checkpoint_invalid"
                    )
                current_turn = self.store.current_turn_transaction(task_id)
                if (
                    current_turn is not None
                    and current_turn.state is TurnTransactionState.RESUMING
                    and current_turn.barrier is TurnBarrier.OPERATION_UNKNOWN
                ):
                    resume_phase = "operation_unknown"
                session = await self.provider.restore(request, provider_checkpoint)
            else:
                session = await self.provider.open(request)
            self._sessions[task_id] = session
            messages = self.store.list_messages(task_id)
            if not messages:
                objective_message = self.store.append_message(
                    task_id, role="user", content=task.spec.objective
                )
                messages = [objective_message]
            if message_cursor == 0:
                objective_message = next(
                    (
                        item
                        for item in messages
                        if item.role == "user" and item.content == task.spec.objective
                    ),
                    None,
                )
                if objective_message is not None:
                    message_cursor = objective_message.sequence
            self.store.transition(
                task_id,
                TaskState.RUNNING,
                workspace_id=workspace.workspace_id,
                provider_session_id=session.session_id,
                expected_state=TaskState.PREPARING,
            )
            await self._drive_session(
                task,
                session,
                resume_phase=resume_phase,
                resume_context=resume_context,
                resume_question_id=resume_question_id,
                resume_turn_before=resume_turn_before,
                resume_turn_public_before=resume_turn_public_before,
                completed_turns=completed_turns,
                message_cursor=message_cursor,
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
        except Exception as exc:
            current = self.store.get_task(task_id)
            if current.state not in TERMINAL_STATES:
                code = getattr(exc, "code", "worker_failed")
                reason = (
                    code
                    if isinstance(code, str) and SAFE_ID.fullmatch(code) is not None
                    else "worker_failed"
                )
                with contextlib.suppress(WorkerConflictError):
                    self.store.transition(task_id, TaskState.FAILED, reason=reason)
        finally:
            with contextlib.suppress(Exception):
                self._settle_terminal_subtask(task_id)
            if session is not None:
                with contextlib.suppress(Exception):
                    await self.provider.close(session)

    def _uncheckpointed_completed_turns(self, task_id: str) -> int:
        cursor = 0
        completed_turns = 0
        last_completed_sequence = 0
        last_checkpoint_sequence = 0
        while True:
            events = self.store.list_events(task_id, after=cursor, limit=1000)
            if not events:
                break
            for event in events:
                if (
                    event.type == "provider_event"
                    and event.payload.get("kind")
                    == ProviderEventKind.TURN_COMPLETED.value
                ):
                    completed_turns += 1
                    last_completed_sequence = event.sequence
                elif event.type == "checkpoint_created":
                    last_checkpoint_sequence = event.sequence
            cursor = events[-1].sequence
            if len(events) < 1000:
                break
        return (
            completed_turns
            if last_completed_sequence > last_checkpoint_sequence
            else 0
        )

    async def _drive_session(
        self,
        task: TaskRecord,
        session: ProviderSession,
        *,
        resume_phase: str | None,
        resume_context: dict[str, object] | None,
        resume_question_id: str | None = None,
        resume_turn_before: WorkspaceSnapshot | None = None,
        resume_turn_public_before: dict[str, object] | None = None,
        completed_turns: int,
        message_cursor: int,
    ) -> None:
        driver = asyncio.create_task(
            self._drive_session_steps(
                task,
                session,
                resume_phase=resume_phase,
                resume_context=resume_context,
                resume_question_id=resume_question_id,
                resume_turn_before=resume_turn_before,
                resume_turn_public_before=resume_turn_public_before,
                completed_turns=completed_turns,
                message_cursor=message_cursor,
            ),
            name=f"coding-worker-drive-{task.task_id}",
        )
        try:
            while not driver.done():
                remaining = (
                    task.spec.budget.max_seconds
                    - self.store.budget_usage(task.task_id).active_seconds
                )
                if remaining <= 0:
                    driver.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await driver
                    raise TimeoutError
                await asyncio.wait({driver}, timeout=min(remaining, 0.25))
            await driver
        finally:
            if not driver.done():
                driver.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await driver

    async def _drive_session_steps(
        self,
        task: TaskRecord,
        session: ProviderSession,
        *,
        resume_phase: str | None,
        resume_context: dict[str, object] | None,
        resume_question_id: str | None,
        resume_turn_before: WorkspaceSnapshot | None,
        resume_turn_public_before: dict[str, object] | None,
        completed_turns: int,
        message_cursor: int,
    ) -> None:
        task_id = task.task_id
        message = task.spec.objective
        turns = completed_turns
        if resume_phase == "turn_open":
            message = self._restored_context_message(
                resume_context,
                "The previous turn stopped before a deterministic completion. "
                "Continue from the durable turn boundary and do not infer that "
                "any unreceipted side effect failed.",
            )
        elif resume_phase == "testing":
            steering, message_cursor = self._next_steering(
                task_id, after_sequence=message_cursor
            )
            if steering is not None:
                message = steering
            else:
                feedback, message_cursor = await self._evaluate_acceptance(
                    task, turns, message_cursor=message_cursor
                )
                if feedback is None:
                    return
                message = self._restored_context_message(resume_context, feedback)
        elif resume_phase == "waiting_input":
            question = next(
                (
                    item
                    for item in self.store.list_questions(task_id)
                    if item.question_id == resume_question_id
                ),
                None,
            )
            answer, message_cursor = self._next_steering(
                task_id, after_sequence=message_cursor
            )
            if (
                question is None
                or question.status is not QuestionStatus.RESOLVED
                or answer is None
                or f"\n\nAnswer to question {resume_question_id}: " not in answer
            ):
                self.store.transition(
                    task_id,
                    TaskState.BLOCKED,
                    reason="question_answer_missing",
                    expected_state=TaskState.RUNNING,
                )
                return
            message = answer
        elif resume_phase == "waiting_approval":
            message = self._approval_resume_message(task_id)
        elif resume_phase == "operation_unknown":
            message = self._restored_context_message(
                resume_context,
                "Reconcile only the exact unknown operation from the parked turn. "
                "Do not create a replacement operation id or repeat its side effect.",
            )
        elif resume_phase == "compacted":
            message = self._restored_context_message(
                resume_context,
                "Continue from the controlled compaction boundary. Reinspect any "
                "workspace state needed before the next side effect.",
            )
        elif resume_phase == "waiting_subtasks":
            message = self._restored_context_message(
                resume_context,
                self._subtask_results_message(task_id)
                + "\nMerge only approved implement changes by exact child task id "
                "and CAS, then rerun the immutable parent acceptance checks.",
            )
        while True:
            durable_usage = self.store.budget_usage(task_id)
            turns = max(turns, durable_usage.turns_started)
            current_turn = (
                self.store.current_turn_transaction(task_id)
                if task.runtime_protocol is RuntimeProtocol.V17
                else None
            )
            resuming_turn = (
                current_turn is not None
                and current_turn.state is TurnTransactionState.RESUMING
            )
            if not resuming_turn:
                if turns >= task.spec.budget.max_turns:
                    self.store.transition(
                        task_id,
                        TaskState.BUDGET_LIMITED,
                        reason="turn_budget_exhausted",
                        expected_state=TaskState.RUNNING,
                    )
                    return
                turns += 1
            workspace_id = self.store.get_task(task_id).workspace_id
            turn_before = (
                resume_turn_before
                if resuming_turn and resume_turn_before is not None
                else self.workspace_broker.capture_snapshot(workspace_id)
                if workspace_id is not None
                else None
            )
            turn_public_before = (
                resume_turn_public_before
                if resuming_turn and resume_turn_public_before is not None
                else self._public_session_context(task_id)
            )
            turn_id = (
                current_turn.turn_id
                if resuming_turn and current_turn is not None
                else f"turn_{uuid.uuid4().hex}"
            )
            if (
                resume_phase == "waiting_approval"
                and resuming_turn
                and current_turn is not None
            ):
                try:
                    message = await self._resume_approved_operation(
                        task_id, turn_id=current_turn.turn_id
                    )
                    resume_phase = None
                except ToolBrokerError as exc:
                    if exc.code == "operation_result_unknown":
                        try:
                            await self._park_v17_turn(
                                task,
                                session,
                                turn_id=current_turn.turn_id,
                                turns=turns,
                                message_cursor=message_cursor,
                                turn_before=turn_before,
                                turn_public_before=turn_public_before,
                            )
                        except Exception:
                            with contextlib.suppress(WorkerConflictError):
                                self.store.finish_turn_transaction(
                                    task_id=task_id,
                                    turn_id=current_turn.turn_id,
                                    state=TurnTransactionState.INTERRUPTED,
                                )
                            self.store.transition(
                                task_id,
                                TaskState.BLOCKED,
                                reason="turn_checkpoint_failed",
                                expected_state=TaskState.RUNNING,
                            )
                        return
                    self.store.finish_turn_transaction(
                        task_id=task_id,
                        turn_id=current_turn.turn_id,
                        state=TurnTransactionState.INTERRUPTED,
                    )
                    self.store.transition(
                        task_id,
                        TaskState.BLOCKED,
                        reason=exc.code,
                        expected_state=TaskState.RUNNING,
                    )
                    return
            if not resuming_turn:
                if task.runtime_protocol is RuntimeProtocol.V17:
                    self.store.open_turn_transaction(
                        task_id=task_id,
                        turn_id=turn_id,
                        workspace_tree_hash=(
                            turn_before.tree_hash
                            if turn_before is not None
                            else self.workspace_broker.current_tree_hash(
                                workspace_id or ""
                            )
                        ),
                    )
                self.store.append_session_ledger(
                    task_id,
                    kind=SessionLedgerKind.TURN_STARTED,
                    turn_id=turn_id,
                    payload={},
                )
                if task.runtime_protocol is RuntimeProtocol.V17:
                    try:
                        provider_checkpoint = await self.provider.checkpoint(session)
                        entry_tree_hash = self.workspace_broker.current_tree_hash(
                            workspace_id or ""
                        )
                        provider_checkpoint = self._bind_provider_checkpoint_tree(
                            provider_checkpoint,
                            task_id=task_id,
                            tree_hash=entry_tree_hash,
                        )
                        checkpoint = self.store.create_checkpoint(
                            task_id=task_id,
                            workspace_tree_hash=entry_tree_hash,
                            payload={
                                "phase": "turn_open",
                                "turn_id": turn_id,
                                "completed_turns": turns,
                                "message_cursor": message_cursor,
                                "provider": provider_checkpoint.model_dump(mode="json"),
                                "context_summary": self._context_summary(
                                    task_id,
                                    tree_hash=entry_tree_hash,
                                    public_output="",
                                ),
                                "turn_before": (
                                    turn_before.model_dump(mode="json")
                                    if turn_before is not None
                                    else None
                                ),
                                "turn_public_before": turn_public_before,
                            },
                        )
                        self.store.bind_turn_recovery_checkpoint(
                            task_id=task_id,
                            turn_id=turn_id,
                            checkpoint_id=checkpoint.checkpoint_id,
                        )
                    except Exception:
                        self.store.finish_session_turn(
                            task_id, turn_id=turn_id, result_state="interrupted"
                        )
                        self.store.finish_turn_transaction(
                            task_id=task_id,
                            turn_id=turn_id,
                            state=TurnTransactionState.INTERRUPTED,
                        )
                        self.store.transition(
                            task_id,
                            TaskState.BLOCKED,
                            reason="turn_entry_checkpoint_failed",
                            expected_state=TaskState.RUNNING,
                        )
                        return
            outcome = "interrupted"
            question_data: dict[str, object] | None = None
            compaction_failed = False
            try:
                stream = self.provider.message(session, message).__aiter__()
                while True:
                    try:
                        event, parked_state = await self._next_provider_event_when_runnable(
                            task_id, stream
                        )
                    except StopAsyncIteration:
                        break
                    if event is None:
                        transaction = (
                            self.store.current_turn_transaction(task_id)
                            if task.runtime_protocol is RuntimeProtocol.V17
                            else None
                        )
                        outcome = (
                            "turn_parking"
                            if transaction is not None
                            and transaction.state is TurnTransactionState.PARKING
                            else "waiting_subtasks"
                            if parked_state is TaskState.WAITING_SUBTASKS
                            else "waiting_approval"
                            if parked_state is TaskState.WAITING_APPROVAL
                            else "state_changed"
                        )
                        break
                    self.store.append_event(
                        task_id,
                        "provider_event",
                        {"kind": event.kind.value, "data": event.data},
                    )
                    self._record_provider_session_event(task_id, turn_id, event)
                    if self._should_auto_compact(task, event):
                        transaction = self.store.current_turn_transaction(task_id)
                        if transaction is not None and transaction.state in {
                            TurnTransactionState.OPEN,
                            TurnTransactionState.RESUMING,
                        }:
                            self.store.begin_turn_parking(
                                task_id=task_id,
                                turn_id=transaction.turn_id,
                                barrier=TurnBarrier.COMPACTION,
                            )
                            outcome = "turn_parking"
                            break
                    if self.store.get_task(task_id).state is TaskState.WAITING_SUBTASKS:
                        outcome = "waiting_subtasks"
                        break
                    if (
                        event.kind is ProviderEventKind.QUESTION
                        and task.runtime_protocol is not RuntimeProtocol.V17
                    ):
                        outcome = "waiting_input"
                        question_data = event.data
                        break
                    if (
                        event.kind is ProviderEventKind.COMPACTION
                        and task.runtime_protocol is not RuntimeProtocol.V17
                    ):
                        try:
                            await self._record_controlled_compaction(
                                task,
                                session,
                                turn_id=turn_id,
                                turns=turns,
                                message_cursor=message_cursor,
                                provider_note=str(event.data["summary"]),
                            )
                        except Exception:
                            outcome = "interrupted"
                            compaction_failed = True
                            break
                    if event.kind is ProviderEventKind.TURN_COMPLETED:
                        outcome = "completed"
                        break
                    if event.kind is ProviderEventKind.CANCELLED:
                        outcome = "cancelled"
                        break
                    if event.kind is ProviderEventKind.FAILED:
                        outcome = "failed"
                        break
            except BaseException:
                self.store.finish_session_turn(
                    task_id, turn_id=turn_id, result_state="interrupted"
                )
                raise
            if outcome == "turn_parking":
                try:
                    await self._park_v17_turn(
                        task,
                        session,
                        turn_id=turn_id,
                        turns=turns,
                        message_cursor=message_cursor,
                        turn_before=turn_before,
                        turn_public_before=turn_public_before,
                    )
                except Exception:
                    with contextlib.suppress(WorkerConflictError):
                        self.store.finish_turn_transaction(
                            task_id=task_id,
                            turn_id=turn_id,
                            state=TurnTransactionState.INTERRUPTED,
                        )
                    current = self.store.get_task(task_id)
                    if current.state is TaskState.RUNNING:
                        self.store.transition(
                            task_id,
                            TaskState.BLOCKED,
                            reason="turn_checkpoint_failed",
                            expected_state=TaskState.RUNNING,
                        )
                return
            if outcome == "completed" and workspace_id is not None and turn_before is not None:
                turn_after = self.workspace_broker.capture_snapshot(workspace_id)
                turn_public_after = self._public_session_context(task_id)
                self.store.finish_session_turn(
                    task_id,
                    turn_id=turn_id,
                    result_state=outcome,
                    turn_checkpoint={
                        "before_tree_hash": turn_before.tree_hash,
                        "before_tree_oid": turn_before.tree_oid,
                        "after_tree_hash": turn_after.tree_hash,
                        "after_tree_oid": turn_after.tree_oid,
                        "before_public_context": turn_public_before,
                        "after_public_context": turn_public_after,
                    },
                )
                if task.runtime_protocol is RuntimeProtocol.V17:
                    self.store.finish_turn_transaction(
                        task_id=task_id,
                        turn_id=turn_id,
                        state=TurnTransactionState.COMPLETED,
                    )
            else:
                self.store.finish_session_turn(
                    task_id, turn_id=turn_id, result_state=outcome
                )
                if task.runtime_protocol is RuntimeProtocol.V17:
                    self.store.finish_turn_transaction(
                        task_id=task_id,
                        turn_id=turn_id,
                        state=TurnTransactionState.INTERRUPTED,
                    )
            if outcome == "waiting_input":
                if question_data is None:
                    raise WorkerConflictError(
                        "Provider question payload is missing.",
                        code="provider_event_invalid",
                    )
                try:
                    question_id = str(question_data["question_id"])
                    self.store.create_question(
                        task_id=task_id,
                        question_id=question_id,
                        turn_id=turn_id,
                        prompt=str(question_data["prompt"]),
                        options=tuple(
                            WorkerQuestionOption.model_validate(item)
                            for item in question_data["options"]
                        ),
                    )
                    provider_checkpoint = await self.provider.checkpoint(session)
                    tree_hash = self.workspace_broker.current_tree_hash(
                        self.store.get_task(task_id).workspace_id or ""
                    )
                    provider_checkpoint = self._bind_provider_checkpoint_tree(
                        provider_checkpoint, task_id=task_id, tree_hash=tree_hash
                    )
                    self.store.create_checkpoint(
                        task_id=task_id,
                        workspace_tree_hash=tree_hash,
                        payload={
                            "phase": "waiting_input",
                            "question_id": question_id,
                            "completed_turns": turns,
                            "message_cursor": message_cursor,
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
                        reason="question_checkpoint_failed",
                        expected_state=TaskState.RUNNING,
                    )
                    return
                self.store.transition(
                    task_id,
                    TaskState.WAITING_INPUT,
                    reason="user_input_required",
                    expected_state=TaskState.RUNNING,
                )
                return
            if compaction_failed:
                self.store.transition(
                    task_id,
                    TaskState.BLOCKED,
                    reason="context_compaction_failed",
                    expected_state=TaskState.RUNNING,
                )
                return
            if outcome == "cancelled":
                self.store.transition(
                    task_id, TaskState.CANCELLED, reason="provider_cancelled"
                )
                return
            if outcome == "failed":
                self.store.transition(task_id, TaskState.FAILED, reason="provider_failed")
                return
            if outcome == "state_changed":
                return
            if outcome == "waiting_approval":
                current = self.store.get_task(task_id)
                if current.state is not TaskState.RUNNING:
                    return
                message = self._approval_resume_message(task_id)
                continue
            if outcome == "waiting_subtasks":
                current = self.store.get_task(task_id)
                try:
                    provider_checkpoint = await self.provider.checkpoint(session)
                    tree_hash = self.workspace_broker.current_tree_hash(
                        current.workspace_id or ""
                    )
                    provider_checkpoint = self._bind_provider_checkpoint_tree(
                        provider_checkpoint, task_id=task_id, tree_hash=tree_hash
                    )
                    self.store.create_checkpoint(
                        task_id=task_id,
                        workspace_tree_hash=tree_hash,
                        payload={
                            "phase": "waiting_subtasks",
                            "completed_turns": turns,
                            "message_cursor": message_cursor,
                            "provider": provider_checkpoint.model_dump(mode="json"),
                            "context_summary": self._context_summary(
                                task_id,
                                tree_hash=tree_hash,
                                public_output=str(
                                    provider_checkpoint.payload.get(
                                        "public_output", ""
                                    )
                                ),
                            ),
                        },
                    )
                except Exception:
                    self.store.transition(
                        task_id,
                        TaskState.BLOCKED,
                        reason="subtask_checkpoint_failed",
                        expected_state=TaskState.WAITING_SUBTASKS,
                    )
                return
            if outcome != "completed":
                current = self.store.get_task(task_id)
                if current.state not in TERMINAL_STATES:
                    self.store.transition(
                        task_id, TaskState.INTERRUPTED, reason="provider_stream_ended"
                    )
                return
            current = self.store.get_task(task_id)
            if current.state is TaskState.WAITING_SUBTASKS:
                try:
                    provider_checkpoint = await self.provider.checkpoint(session)
                    tree_hash = self.workspace_broker.current_tree_hash(
                        current.workspace_id or ""
                    )
                    provider_checkpoint = self._bind_provider_checkpoint_tree(
                        provider_checkpoint, task_id=task_id, tree_hash=tree_hash
                    )
                    self.store.create_checkpoint(
                        task_id=task_id,
                        workspace_tree_hash=tree_hash,
                        payload={
                            "phase": "waiting_subtasks",
                            "completed_turns": turns,
                            "message_cursor": message_cursor,
                            "provider": provider_checkpoint.model_dump(mode="json"),
                            "context_summary": self._context_summary(
                                task_id,
                                tree_hash=tree_hash,
                                public_output=str(
                                    provider_checkpoint.payload.get(
                                        "public_output", ""
                                    )
                                ),
                            ),
                        },
                    )
                except Exception:
                    self.store.transition(
                        task_id,
                        TaskState.BLOCKED,
                        reason="subtask_checkpoint_failed",
                        expected_state=TaskState.WAITING_SUBTASKS,
                    )
                return
            try:
                provider_checkpoint = await self.provider.checkpoint(session)
                tree_hash = self.workspace_broker.current_tree_hash(
                    self.store.get_task(task_id).workspace_id or ""
                )
                provider_checkpoint = self._bind_provider_checkpoint_tree(
                    provider_checkpoint, task_id=task_id, tree_hash=tree_hash
                )
                self.store.create_checkpoint(
                    task_id=task_id,
                    workspace_tree_hash=tree_hash,
                    payload={
                        "phase": "testing",
                        "completed_turns": turns,
                        "message_cursor": message_cursor,
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
            steering, message_cursor = self._next_steering(
                task_id, after_sequence=message_cursor
            )
            if steering is not None:
                message = steering
                continue
            feedback, message_cursor = await self._evaluate_acceptance(
                task, turns, message_cursor=message_cursor
            )
            if feedback is None:
                return
            message = feedback

    async def _next_provider_event_when_runnable(
        self,
        task_id: str,
        stream: AsyncIterator[ProviderEvent],
    ) -> tuple[ProviderEvent | None, TaskState | None]:
        """Abort one provider turn while an exact approval is unresolved."""

        while True:
            current = self.store.get_task(task_id)
            transaction = (
                self.store.current_turn_transaction(task_id)
                if current.runtime_protocol is RuntimeProtocol.V17
                else None
            )
            if (
                transaction is not None
                and transaction.state is TurnTransactionState.PARKING
            ):
                await self._close_provider_stream(stream)
                return None, None
            if current.state is TaskState.WAITING_APPROVAL:
                await self._close_provider_stream(stream)
                while current.state is TaskState.WAITING_APPROVAL:
                    await asyncio.sleep(0.05)
                    current = self.store.get_task(task_id)
                return (
                    None,
                    TaskState.WAITING_APPROVAL
                    if current.state is TaskState.RUNNING
                    else current.state,
                )
            if current.state is not TaskState.RUNNING:
                return None, current.state
            break

        pending = asyncio.create_task(anext(stream))
        try:
            while True:
                current = self.store.get_task(task_id)
                transaction = (
                    self.store.current_turn_transaction(task_id)
                    if current.runtime_protocol is RuntimeProtocol.V17
                    else None
                )
                if (
                    transaction is not None
                    and transaction.state is TurnTransactionState.PARKING
                ):
                    await self._close_provider_stream(stream, pending=pending)
                    return None, None
                if current.state is TaskState.WAITING_APPROVAL:
                    await self._close_provider_stream(stream, pending=pending)
                    while current.state is TaskState.WAITING_APPROVAL:
                        await asyncio.sleep(0.05)
                        current = self.store.get_task(task_id)
                    return (
                        None,
                        TaskState.WAITING_APPROVAL
                        if current.state is TaskState.RUNNING
                        else current.state,
                    )
                if current.state is not TaskState.RUNNING:
                    pending.cancel()
                    with contextlib.suppress(
                        asyncio.CancelledError, StopAsyncIteration
                    ):
                        await pending
                    return None, current.state
                if pending.done():
                    event = pending.result()
                    transaction = (
                        self.store.current_turn_transaction(task_id)
                        if current.runtime_protocol is RuntimeProtocol.V17
                        else None
                    )
                    if (
                        transaction is not None
                        and transaction.state is TurnTransactionState.PARKING
                    ):
                        await self._close_provider_stream(stream)
                        return None, None
                    return event, None
                await asyncio.wait({pending}, timeout=0.05)
        except BaseException:
            if not pending.done():
                pending.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError, StopAsyncIteration
                ):
                    await pending
            raise

    @staticmethod
    async def _close_provider_stream(
        stream: AsyncIterator[ProviderEvent],
        *,
        pending: asyncio.Task[ProviderEvent] | None = None,
    ) -> None:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        close = getattr(stream, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()

    def _approval_resume_message(self, task_id: str) -> str:
        approvals = self.store.list_approvals(task_id)
        decided = next(
            (
                approval
                for approval in reversed(approvals)
                if approval.status is not ApprovalStatus.PENDING
            ),
            None,
        )
        if decided is None:
            return (
                "The approval wait ended. Reconcile only the exact pending tool "
                "operation; do not create replacement operation IDs."
            )
        return (
            f"Approval {decided.status.value} was recorded for exact operation "
            f"{decided.operation_id}. Retry or reconcile only that same tool call "
            "with the identical operation_id and arguments. Do not create a new "
            "operation for the same intent."
        )

    async def _resume_approved_operation(self, task_id: str, *, turn_id: str) -> str:
        if self.tool_broker is None:
            return self._approval_resume_message(task_id)
        operations = {
            operation.operation_id: operation
            for operation in self.store.list_operations(task_id)
            if operation.turn_id == turn_id
        }
        approvals = [
            approval
            for approval in self.store.list_approvals(task_id)
            if approval.operation_id in operations
        ]
        if len(approvals) != 1:
            raise ToolBrokerError(
                "Approval does not bind one exact operation.",
                code="approval_operation_conflict",
            )
        approval = approvals[0]
        operation = operations[approval.operation_id]
        if approval.status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.CANCELLED,
            ApprovalStatus.EXPIRED,
        }:
            if operation.state is OperationState.PREPARED:
                self.store.transition_operation(
                    operation.operation_id,
                    OperationState.FAILED,
                    result={"code": "approval_rejected"},
                    expected_state=OperationState.PREPARED,
                )
            return (
                f"Approval was not granted for exact operation {operation.operation_id}. "
                "The operation was not executed; do not retry it under another id."
            )
        if operation.state is OperationState.COMPLETED:
            return (
                f"Exact approved operation {operation.operation_id} is already complete. "
                "Do not execute it again."
            )
        if (
            operation.state is not OperationState.PREPARED
            or approval.status is not ApprovalStatus.APPROVED
            or approval.lease is None
        ):
            raise ToolBrokerError(
                "Approved operation is not ready to resume.",
                code="approval_operation_unavailable",
            )
        network_lease_id: str | None = None
        if operation.tool_name in {"install_dependencies", "query_documentation"}:
            network_operation_id = "network_" + hashlib.sha256(
                operation.operation_id.encode("utf-8")
            ).hexdigest()[:32]
            network = next(
                (
                    item
                    for item in self.store.list_approvals(task_id)
                    if item.operation_id == network_operation_id
                ),
                None,
            )
            if (
                network is None
                or network.status is not ApprovalStatus.APPROVED
                or network.lease is None
            ):
                raise ToolBrokerError(
                    "Approved network operation is unavailable.",
                    code="approval_operation_unavailable",
                )
            network_lease_id = network.lease.lease_id
        result = await self.tool_broker.execute(
            task_id=task_id,
            operation_id=operation.operation_id,
            tool_name=operation.tool_name,
            arguments=operation.request.get("arguments", {}),
            lease_id=approval.lease.lease_id,
            network_lease_id=network_lease_id,
        )
        self.store.append_event(
            task_id,
            "operation_reconciled",
            {
                "operation_id": operation.operation_id,
                "state": result.state.value,
                "source": "approved_resume",
            },
        )
        output = str(result.data.get("output", ""))[:4096]
        exit_code = result.data.get("exit_code")
        summary = (
            f"Exact approved operation {operation.operation_id} completed once"
            + (f" with exit code {exit_code}" if exit_code is not None else "")
            + ". Do not execute it again."
        )
        if output:
            summary += f"\n\nBounded operation output:\n{output}"
        return summary

    async def _park_v17_turn(
        self,
        task: TaskRecord,
        session: ProviderSession,
        *,
        turn_id: str,
        turns: int,
        message_cursor: int,
        turn_before: WorkspaceSnapshot | None,
        turn_public_before: dict[str, object],
    ) -> None:
        transaction = self.store.get_turn_transaction(task.task_id, turn_id)
        if (
            transaction.state is not TurnTransactionState.PARKING
            or transaction.barrier is None
        ):
            raise WorkerConflictError(
                "Turn is not ready to park.", code="turn_state_conflict"
            )
        workspace_id = self.store.get_task(task.task_id).workspace_id or ""
        tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
        await self.provider.interrupt_turn(session)
        provider_checkpoint = await self.provider.checkpoint(session)
        provider_checkpoint = self._bind_provider_checkpoint_tree(
            provider_checkpoint, task_id=task.task_id, tree_hash=tree_hash
        )
        context = self._context_summary(
            task.task_id,
            tree_hash=tree_hash,
            public_output=str(provider_checkpoint.payload.get("public_output", "")),
        )
        phase = {
            TurnBarrier.APPROVAL: "waiting_approval",
            TurnBarrier.INPUT: "waiting_input",
            TurnBarrier.SUBTASKS: "waiting_subtasks",
            TurnBarrier.COMPACTION: "compacted",
            TurnBarrier.OPERATION_UNKNOWN: "operation_unknown",
        }[transaction.barrier]
        payload: dict[str, object] = {
            "phase": phase,
            "turn_id": turn_id,
            "turn_generation": transaction.generation,
            "completed_turns": turns,
            "message_cursor": message_cursor,
            "provider": provider_checkpoint.model_dump(mode="json"),
            "context_summary": context,
            "turn_before": (
                turn_before.model_dump(mode="json")
                if turn_before is not None
                else None
            ),
            "turn_public_before": turn_public_before,
        }
        if transaction.barrier is TurnBarrier.INPUT:
            pending = next(
                (
                    item
                    for item in reversed(self.store.list_questions(task.task_id))
                    if item.turn_id == turn_id
                    and item.status is QuestionStatus.PENDING
                ),
                None,
            )
            if pending is None:
                raise WorkerConflictError(
                    "Parked input turn has no question.", code="question_not_found"
                )
            payload["question_id"] = pending.question_id
        if transaction.barrier is TurnBarrier.COMPACTION:
            boundary = self.store.session_tool_boundary_sequence(task.task_id, turn_id)
            summary = self._controlled_compaction_summary(
                task.task_id,
                tree_hash=tree_hash,
                provider_note="Platform-requested controlled compaction.",
            )
            encoded = json.dumps(
                summary,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(encoded.encode("utf-8")) > 65_536:
                raise WorkerConflictError(
                    "Controlled context is too large to compact.",
                    code="context_compaction_too_large",
                )
            payload["context_summary"] = summary
            self.store.append_session_ledger(
                task.task_id,
                kind=SessionLedgerKind.COMPACTION,
                turn_id=turn_id,
                payload={"summary": encoded, "boundary_sequence": boundary},
            )
            self.store.append_event(
                task.task_id,
                "context_compacted",
                {
                    "boundary_sequence": boundary,
                    "workspace_tree_hash": tree_hash,
                },
            )
        checkpoint = self.store.create_checkpoint(
            task_id=task.task_id,
            workspace_tree_hash=tree_hash,
            payload=payload,
        )
        self.store.park_turn_transaction(
            task_id=task.task_id,
            turn_id=turn_id,
            checkpoint_id=checkpoint.checkpoint_id,
        )
        if transaction.barrier is TurnBarrier.COMPACTION:
            self.store.settle_parked_turn(
                task_id=task.task_id,
                barrier=TurnBarrier.COMPACTION,
                expected_state=TaskState.RUNNING,
            )
            self._wake.set()
            return
        target = {
            TurnBarrier.APPROVAL: TaskState.WAITING_APPROVAL,
            TurnBarrier.INPUT: TaskState.WAITING_INPUT,
            TurnBarrier.SUBTASKS: TaskState.WAITING_SUBTASKS,
            TurnBarrier.OPERATION_UNKNOWN: TaskState.INTERRUPTED,
        }[transaction.barrier]
        self.store.transition(
            task.task_id,
            target,
            reason=(
                "operation_result_unknown"
                if transaction.barrier is TurnBarrier.OPERATION_UNKNOWN
                else f"turn_parked_{transaction.barrier.value}"
            ),
            expected_state=TaskState.RUNNING,
        )

    def _record_provider_session_event(
        self, task_id: str, turn_id: str, event: ProviderEvent
    ) -> None:
        kind = event.kind
        data = event.data
        authoritative_provider_kinds = {
            ProviderEventKind.PLAN,
            ProviderEventKind.TODO,
            ProviderEventKind.QUESTION,
            ProviderEventKind.COMPACTION,
        }
        if (
            self.store.get_task(task_id).runtime_protocol is RuntimeProtocol.V17
            and kind in authoritative_provider_kinds
        ):
            self.store.append_event(
                task_id,
                "provider_hint",
                {"kind": kind.value, "turn_id": turn_id},
            )
            return
        if kind is ProviderEventKind.MESSAGE:
            self.store.append_message(task_id, role="assistant", content=str(data["text"]))
        elif kind is ProviderEventKind.PLAN:
            self.store.append_session_ledger(
                task_id,
                kind=SessionLedgerKind.PLAN,
                turn_id=turn_id,
                payload=data,
            )
        elif kind is ProviderEventKind.TODO:
            self.store.append_session_ledger(
                task_id,
                kind=SessionLedgerKind.TODO,
                turn_id=turn_id,
                payload=data,
            )
        elif kind is ProviderEventKind.TOOL_STARTED:
            self.store.append_session_ledger(
                task_id,
                kind=SessionLedgerKind.TOOL_STARTED,
                turn_id=turn_id,
                operation_id=str(data["operation_id"]),
                payload={
                    "tool_name": data["tool_name"],
                    "summary": data["summary"],
                },
            )

        elif kind is ProviderEventKind.TOOL_COMPLETED:
            self.store.append_session_ledger(
                task_id,
                kind=SessionLedgerKind.TOOL_FINISHED,
                turn_id=turn_id,
                operation_id=str(data["operation_id"]),
                payload={
                    "tool_name": data["tool_name"],
                    "summary": data["summary"],
                    "result_state": "succeeded" if data["success"] else "failed",
                    "artifact_id": data["artifact_id"],
                },
            )
        elif kind is ProviderEventKind.QUESTION:
            self.store.append_session_ledger(
                task_id,
                kind=SessionLedgerKind.QUESTION,
                turn_id=turn_id,
                payload=data,
            )

    def _should_auto_compact(
        self, task: TaskRecord, event: ProviderEvent
    ) -> bool:
        if (
            task.runtime_protocol is not RuntimeProtocol.V17
            or event.kind is not ProviderEventKind.USAGE
        ):
            return False
        context_tokens = self._route_context_tokens.get(task.spec.model_route)
        if context_tokens is None:
            return False
        usage = event.data.get("usage")
        if not isinstance(usage, dict):
            return False
        input_tokens = usage.get("input_tokens")
        return (
            isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and input_tokens * 4 >= context_tokens * 3
        )

    async def _record_controlled_compaction(
        self,
        task: TaskRecord,
        session: ProviderSession,
        *,
        turn_id: str,
        turns: int,
        message_cursor: int,
        provider_note: str,
    ) -> None:
        task_id = task.task_id
        boundary = self.store.session_tool_boundary_sequence(task_id, turn_id)
        workspace_id = self.store.get_task(task_id).workspace_id or ""
        tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
        summary = self._controlled_compaction_summary(
            task_id,
            tree_hash=tree_hash,
            provider_note=provider_note,
        )
        encoded = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded.encode("utf-8")) > 65_536:
            raise WorkerConflictError(
                "Controlled context is too large to compact.",
                code="context_compaction_too_large",
            )
        provider_checkpoint = await self.provider.checkpoint(session)
        provider_checkpoint = self._bind_provider_checkpoint_tree(
            provider_checkpoint, task_id=task_id, tree_hash=tree_hash
        )
        self.store.create_checkpoint(
            task_id=task_id,
            workspace_tree_hash=tree_hash,
            payload={
                "phase": "compacted",
                "completed_turns": turns,
                "message_cursor": message_cursor,
                "provider": provider_checkpoint.model_dump(mode="json"),
                "context_summary": summary,
            },
        )
        self.store.append_session_ledger(
            task_id,
            kind=SessionLedgerKind.COMPACTION,
            turn_id=turn_id,
            payload={"summary": encoded, "boundary_sequence": boundary},
        )
        self.store.append_event(
            task_id,
            "context_compacted",
            {
                "boundary_sequence": boundary,
                "workspace_tree_hash": tree_hash,
            },
        )

    @staticmethod
    def _bind_provider_checkpoint_tree(
        checkpoint: ProviderCheckpoint, *, task_id: str, tree_hash: str
    ) -> ProviderCheckpoint:
        compatibility = checkpoint.compatibility
        if compatibility is None:
            return checkpoint
        if compatibility.task_id != task_id:
            raise WorkerConflictError(
                "Provider checkpoint task binding changed.",
                code="checkpoint_invalid",
            )
        return checkpoint.model_copy(
            update={
                "compatibility": compatibility.model_copy(
                    update={"workspace_tree_hash": tree_hash}
                )
            }
        )

    def _controlled_compaction_summary(
        self, task_id: str, *, tree_hash: str, provider_note: str
    ) -> dict[str, object]:
        task = self.store.get_task(task_id)
        base = self._context_summary(
            task_id,
            tree_hash=tree_hash,
            public_output=provider_note[:16_384],
        )
        plan = self.store.latest_plan(task_id)
        todo = self.store.latest_session_entry(task_id, SessionLedgerKind.TODO)
        questions = self.store.list_questions(task_id)
        resolved = [item for item in questions if item.status is QuestionStatus.RESOLVED]
        pending = [item for item in questions if item.status is QuestionStatus.PENDING]
        raw_diff = self.workspace_broker.diff(task.workspace_id or "")
        changed_paths: list[str] = []
        for line in raw_diff.decode("utf-8", errors="replace").splitlines():
            if not line.startswith("diff --git a/") or " b/" not in line:
                continue
            path = line.split(" b/", 1)[1]
            if path not in changed_paths:
                changed_paths.append(path)
        base.update(
            {
                "version": 2,
                "acceptance_contract_id": task.spec.acceptance.contract_id,
                "plan": plan.model_dump(mode="json") if plan is not None else None,
                "todo": todo.payload if todo is not None else {"items": []},
                "decisions": [
                    {
                        "question_id": item.question_id,
                        "answer": item.answer,
                        "selected_option_id": item.selected_option_id,
                    }
                    for item in resolved[-16:]
                ],
                "unresolved_questions": [
                    {"question_id": item.question_id, "prompt": item.prompt}
                    for item in pending[-16:]
                ],
                "changed_files": {
                    "paths": changed_paths[:256],
                    "count": len(changed_paths),
                    "diff_sha256": hashlib.sha256(raw_diff).hexdigest(),
                },
                "next_step": self._next_compaction_step(plan),
            }
        )
        return base

    @staticmethod
    def _next_compaction_step(plan: object) -> str:
        if plan is not None:
            for item in plan.items:
                if item.status in {"in_progress", "pending"}:
                    return item.step
        return "continue_task"

    def _next_steering(
        self, task_id: str, *, after_sequence: int
    ) -> tuple[str | None, int]:
        pending = next(
            (
                item
                for item in self.store.list_messages(task_id)
                if item.role == "user" and item.sequence > after_sequence
            ),
            None,
        )
        if pending is None:
            return None, after_sequence
        self.store.append_event(
            task_id,
            "steering_scheduled",
            {"message_id": pending.message_id, "sequence": pending.sequence},
        )
        return (
            "User steering received at a safe tool boundary. Follow it without "
            "weakening the immutable acceptance contract.\n\n" + pending.content,
            pending.sequence,
        )

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

    async def _evaluate_acceptance(
        self, task: TaskRecord, turns: int, *, message_cursor: int
    ) -> tuple[str | None, int]:
        task_id = task.task_id
        turns = max(turns, self.store.budget_usage(task_id).turns_started)
        if task.runtime_protocol is RuntimeProtocol.V17:
            unsettled = any(
                item.state
                in {
                    OperationState.PREPARED,
                    OperationState.RUNNING,
                    OperationState.UNKNOWN,
                }
                for item in self.store.list_operations(task_id)
            ) or any(
                item.status is ApprovalStatus.PENDING
                for item in self.store.list_approvals(task_id)
            ) or any(
                item.status is QuestionStatus.PENDING
                for item in self.store.list_questions(task_id)
            ) or self.store.current_turn_transaction(task_id) is not None
            if unsettled:
                self.store.transition(
                    task_id,
                    TaskState.BLOCKED,
                    reason="turn_settlement_incomplete",
                    expected_state=TaskState.RUNNING,
                )
                return None, message_cursor
        ready_implementations = tuple(
            relation
            for relation in self.store.list_subtasks(task_id)
            if relation.kind is SubtaskKind.IMPLEMENT
            and relation.merge_state is SubtaskMergeState.READY
        )
        if ready_implementations:
            child_ids = ", ".join(
                relation.child_task_id for relation in ready_implementations
            )
            message = (
                "A controlled implementation changeset is ready but has not been "
                "settled. Call merge_subtask with the exact child task id and a new "
                "operation id before running parent acceptance. Do not reproduce the "
                f"child edits directly in the parent Workspace. Ready children: {child_ids}."
            )
            self.store.append_message(task_id, role="system", content=message)
            self.store.append_event(
                task_id,
                "acceptance_retry",
                {"turn": turns + 1, "reason": "subtask_merge_required"},
            )
            return message, message_cursor
        self.store.transition(
            task_id, TaskState.TESTING, expected_state=TaskState.RUNNING
        )
        relation = self.store.subtask_for_child(task_id)
        if relation is not None:
            await self._finish_subtask_acceptance(task, relation)
            return None, message_cursor
        if self.harness_runner is None:
            self.store.transition(
                task_id,
                TaskState.BLOCKED,
                reason="acceptance_runner_pending",
                expected_state=TaskState.TESTING,
            )
            return None, message_cursor
        acceptance_turn_id: str | None = None
        if task.runtime_protocol is RuntimeProtocol.V17:
            workspace_id = self.store.get_task(task_id).workspace_id or ""
            acceptance_turn_id = f"turn_acceptance_{uuid.uuid4().hex}"
            self.store.open_turn_transaction(
                task_id=task_id,
                turn_id=acceptance_turn_id,
                workspace_tree_hash=self.workspace_broker.current_tree_hash(workspace_id),
            )
        try:
            evidence = await self.harness_runner.run_required_checks(task_id)
        except ToolBrokerError as exc:
            if acceptance_turn_id is not None:
                self.store.finish_turn_transaction(
                    task_id=task_id,
                    turn_id=acceptance_turn_id,
                    state=TurnTransactionState.INTERRUPTED,
                )
            self.store.transition(
                task_id,
                TaskState.BLOCKED,
                reason=exc.code,
                expected_state=TaskState.TESTING,
            )
            return None, message_cursor
        except Exception:
            if acceptance_turn_id is not None:
                self.store.finish_turn_transaction(
                    task_id=task_id,
                    turn_id=acceptance_turn_id,
                    state=TurnTransactionState.INTERRUPTED,
                )
            raise
        if acceptance_turn_id is not None:
            self.store.finish_turn_transaction(
                task_id=task_id,
                turn_id=acceptance_turn_id,
                state=TurnTransactionState.COMPLETED,
            )
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
        steering, message_cursor = self._next_steering(
            task_id, after_sequence=message_cursor
        )
        if self.harness_runner.acceptance_satisfied(task_id):
            if steering is None:
                self.store.transition(
                    task_id,
                    TaskState.COMPLETED,
                    expected_state=TaskState.TESTING,
                )
                return None, message_cursor
            if turns < task.spec.budget.max_turns:
                self.store.transition(
                    task_id,
                    TaskState.RUNNING,
                    reason="steering_pending",
                    expected_state=TaskState.TESTING,
                )
                return steering, message_cursor
        if turns >= task.spec.budget.max_turns:
            self.store.transition(
                task_id,
                TaskState.BUDGET_LIMITED,
                reason="turn_budget_exhausted",
                expected_state=TaskState.TESTING,
            )
            return None, message_cursor
        message = self._acceptance_feedback(task_id, evidence)
        self.store.append_message(task_id, role="system", content=message)
        self.store.append_event(task_id, "acceptance_retry", {"turn": turns + 1})
        self.store.transition(
            task_id,
            TaskState.RUNNING,
            reason="acceptance_failed",
            expected_state=TaskState.TESTING,
        )
        if steering is not None:
            message = steering + "\n\nFrozen acceptance feedback:\n" + message
        return message, message_cursor

    async def _finish_subtask_acceptance(
        self, task: TaskRecord, relation: SubtaskRecord
    ) -> None:
        workspace_id = task.workspace_id or ""
        tree_hash = self.workspace_broker.current_tree_hash(workspace_id)
        changed_paths = self.workspace_broker.changed_paths(workspace_id)
        if (
            relation.kind in {SubtaskKind.EXPLORE, SubtaskKind.REVIEW}
            and tree_hash != relation.base_tree_hash
        ):
            self.store.finish_subtask(
                task.task_id,
                result_tree_hash=tree_hash,
                changed_paths=changed_paths,
                summary="Read-only subtask modified its isolated fork and was rejected.",
                failed=True,
            )
            self.store.transition(
                task.task_id,
                TaskState.BLOCKED,
                reason="subtask_readonly_changed",
                expected_state=TaskState.TESTING,
            )
            self._resume_parent_after_subtasks(relation.parent_task_id)
            return
        if relation.kind is SubtaskKind.IMPLEMENT and not changed_paths:
            self.store.finish_subtask(
                task.task_id,
                result_tree_hash=tree_hash,
                changed_paths=(),
                summary="Implement subtask completed without a mergeable changeset.",
                failed=True,
            )
            self.store.transition(
                task.task_id,
                TaskState.BLOCKED,
                reason="subtask_no_changes",
                expected_state=TaskState.TESTING,
            )
            self._resume_parent_after_subtasks(relation.parent_task_id)
            return
        messages = self.store.list_messages(task.task_id)
        summary = next(
            (
                item.content[:65_536]
                for item in reversed(messages)
                if item.role == "assistant" and item.content.strip()
            ),
            "Subtask completed without a public summary.",
        )
        self.store.finish_subtask(
            task.task_id,
            result_tree_hash=tree_hash,
            changed_paths=changed_paths,
            summary=summary,
        )
        self.store.transition(
            task.task_id,
            TaskState.COMPLETED,
            expected_state=TaskState.TESTING,
        )
        self._resume_parent_after_subtasks(relation.parent_task_id)

    def _settle_terminal_subtask(self, child_task_id: str) -> None:
        """Make every terminal child result observable so its parent cannot deadlock."""
        relation = self.store.subtask_for_child(child_task_id)
        if relation is None or relation.result_tree_hash is not None:
            return
        child = self.store.get_task(child_task_id)
        if child.state not in TERMINAL_STATES:
            return
        tree_hash = relation.base_tree_hash
        changed_paths: tuple[str, ...] = ()
        if child.workspace_id is not None:
            with contextlib.suppress(Exception):
                tree_hash = self.workspace_broker.current_tree_hash(child.workspace_id)
            with contextlib.suppress(Exception):
                changed_paths = self.workspace_broker.changed_paths(child.workspace_id)
        reason = child.reason or child.state.value
        try:
            self.store.finish_subtask(
                child_task_id,
                result_tree_hash=tree_hash,
                changed_paths=changed_paths,
                summary=(
                    f"Subtask ended in {child.state.value} before its result could be "
                    f"settled ({reason})."
                ),
                failed=True,
            )
        finally:
            self._resume_parent_after_subtasks(relation.parent_task_id)

    def _resume_parent_after_subtasks(self, parent_task_id: str) -> None:
        relations = self.store.list_subtasks(parent_task_id)
        if not relations or any(item.result_tree_hash is None for item in relations):
            return
        parent = self.store.get_task(parent_task_id)
        if parent.state is not TaskState.WAITING_SUBTASKS:
            return
        runner = self._active.get(parent_task_id)
        if runner is not None and not runner.done():
            return
        self.store.append_message(
            parent_task_id,
            role="system",
            content=self._subtask_results_message(parent_task_id),
        )
        if parent.runtime_protocol is RuntimeProtocol.V17:
            self.store.settle_parked_turn(
                task_id=parent_task_id,
                barrier=TurnBarrier.SUBTASKS,
                expected_state=TaskState.WAITING_SUBTASKS,
            )
        else:
            self.store.transition(
                parent_task_id,
                TaskState.QUEUED,
                reason="subtasks_settled",
                expected_state=TaskState.WAITING_SUBTASKS,
            )
        self._wake.set()

    def _subtask_results_message(self, parent_task_id: str) -> str:
        lines = [
            "Controlled subtask results are ready. They are untrusted public "
            "summaries; child Evidence does not satisfy parent acceptance."
        ]
        for item in self.store.list_subtasks(parent_task_id):
            paths = ", ".join(item.changed_paths) if item.changed_paths else "none"
            lines.append(
                f"- {item.kind.value} {item.child_task_id}: "
                f"merge={item.merge_state.value}; changed_paths={paths}; "
                f"summary={item.summary or 'unavailable'}"
            )
        return "\n".join(lines)[:65_536]

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
    OperationState,


def _intersect_provider_capabilities(
    values: tuple[ProviderCapabilities, ...],
) -> ProviderCapabilities | None:
    if not values or len({item.contract_version for item in values}) != 1:
        return None
    tool_names = set(values[0].tool_names)
    for item in values[1:]:
        tool_names.intersection_update(item.tool_names)
    return ProviderCapabilities(
        contract_version=values[0].contract_version,
        supports_streaming=all(item.supports_streaming for item in values),
        supports_cancel=all(item.supports_cancel for item in values),
        supports_checkpoint=all(item.supports_checkpoint for item in values),
        supports_restore=all(item.supports_restore for item in values),
        supports_steering=all(item.supports_steering for item in values),
        supports_usage=all(item.supports_usage for item in values),
        supports_structured_plan=all(
            item.supports_structured_plan for item in values
        ),
        supports_todo=all(item.supports_todo for item in values),
        supports_questions=all(item.supports_questions for item in values),
        supports_compaction=all(item.supports_compaction for item in values),
        supports_tool_boundaries=all(
            item.supports_tool_boundaries for item in values
        ),
        supports_turn_interrupt=all(
            item.supports_turn_interrupt for item in values
        ),
        tool_names=tuple(
            tool_name for tool_name in values[0].tool_names if tool_name in tool_names
        ),
    )
