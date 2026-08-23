from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from .contracts import (
    Origin,
    StrictModel,
    SubtaskRecord,
    SubtaskRequest,
    TaskCreateRequest,
    TaskRecord,
    WorkerBudgetUsage,
    WorkerApproval,
    WorkerArtifact,
    WorkerEvidence,
    WorkerEvent,
    WorkerOperation,
    WorkerPlan,
    WorkerQuestion,
    WorkerQuestionAnswer,
    WorkerTaskExport,
    WorkerTodo,
    WorkerTurnHistory,
)
from .provider import (
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderEvent,
    ProviderOpenRequest,
    ProviderSession,
)
from .harness_protocol import HarnessDescriptorObservation


HarnessCapabilities = ProviderCapabilities


class CodingSubstrateError(RuntimeError):
    """Provider-neutral boundary error with a stable HTTP category."""

    def __init__(self, message: str, *, code: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class WritebackCandidate(StrictModel):
    """Immutable internal handoff from the Worker to the v13 writeback plane."""

    task_id: str
    source_id: str
    revision: str
    workspace_tree_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch: bytes


class HarnessCapabilityObservation(StrictModel):
    """Fail-closed health snapshot for one private harness binding."""

    capabilities: ProviderCapabilities | None = None
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: float
    expires_at: float
    reason: str | None = None


class TaskCapabilitySnapshot(StrictModel):
    task_id: str
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot: dict[str, Any]
    observed_at: float
    expires_at: float


class CodingSubstrateStatus(StrictModel):
    max_active_tasks: int = Field(ge=1, le=16)
    retention_seconds: int = Field(ge=1)
    network_enabled: bool = False
    acceptance_checks: tuple[str, ...] = ()


class PreviewServiceStatus(StrictModel):
    state: str
    preview_port: int | None = None
    slot_id: str


class WorkspaceTreeProjection(StrictModel):
    workspace_id: str
    tree_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[dict[str, Any], ...]


@runtime_checkable
class HarnessSupervisor(Protocol):
    """Private process, health, generation and attestation boundary."""

    async def capabilities(self) -> ProviderCapabilities: ...

    async def capabilities_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, ProviderCapabilities | None]: ...

    @property
    def controller_generation(self) -> int: ...

    async def harness_attestations(self) -> Mapping[str, Mapping[str, Any]]: ...

    async def harness_descriptors_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, HarnessDescriptorObservation | None]: ...


@runtime_checkable
class HarnessDriver(Protocol):
    """Private session and turn boundary.

    PR A retains Provider v4 request/session/checkpoint carriers for historical
    tasks.  V20 references and envelopes fence new adapters before production
    migration without changing the public Worker API.
    """

    async def open(self, request: ProviderOpenRequest) -> ProviderSession: ...

    def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]: ...

    async def steer(self, session: ProviderSession, text: str) -> bool: ...

    async def cancel(self, session: ProviderSession) -> bool: ...

    async def interrupt_turn(self, session: ProviderSession) -> bool: ...

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint: ...

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession: ...

    async def close(self, session: ProviderSession) -> None: ...


@runtime_checkable
class ExecutionBackend(Protocol):
    """Process-oriented execution port behind the ModelMirror Tool Broker."""

    async def run_process(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        timeout_seconds: int,
        isolated: bool,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...

    async def run_shell(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        script: str,
        cwd: str,
        mode: str,
        timeout_seconds: int,
        output_callback: Any = None,
    ) -> dict[str, Any]: ...

    async def start_service(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        ttl_seconds: int,
        preview_port: int | None = None,
    ) -> dict[str, Any]: ...

    async def service_status(
        self, *, task_id: str, workspace_id: str, service_id: str
    ) -> dict[str, Any]: ...

    async def service_input(
        self,
        *,
        task_id: str,
        workspace_id: str,
        service_id: str,
        data: str,
    ) -> dict[str, Any]: ...

    async def stop_service(
        self, *, task_id: str, workspace_id: str, service_id: str
    ) -> dict[str, Any]: ...

    async def code_intelligence(
        self,
        *,
        task_id: str,
        workspace_id: str,
        operation_id: str,
        operation: str,
        path: str,
        line: int,
        character: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class TaskControlPlane(Protocol):
    """Use-case commands owned by ModelMirror, never by a harness driver."""

    async def create_task(
        self, origin: Origin, request: TaskCreateRequest
    ) -> TaskRecord: ...

    def status(self) -> CodingSubstrateStatus: ...

    async def refresh_harness_capabilities(self) -> None: ...

    def cached_harness_capabilities(self) -> Sequence[ProviderCapabilities]: ...

    async def harness_capability_observation(
        self, route_id: str
    ) -> HarnessCapabilityObservation: ...

    async def append_message(self, task_id: str, text: str) -> TaskRecord: ...

    async def answer_question(
        self, task_id: str, question_id: str, answer: WorkerQuestionAnswer
    ) -> TaskRecord: ...

    async def pause(self, task_id: str) -> TaskRecord: ...

    async def resume(self, task_id: str) -> TaskRecord: ...

    async def cancel(self, task_id: str) -> TaskRecord: ...

    def decide_approval(
        self,
        task_id: str,
        approval_id: str,
        *,
        approved: bool,
        task_scope: bool,
        ttl_seconds: int,
    ) -> WorkerApproval: ...

    def set_pinned(self, task_id: str, pinned: bool) -> TaskRecord: ...

    def delete_task(self, task_id: str) -> None: ...

    async def preview_service_status(
        self, task_id: str, service_id: str
    ) -> PreviewServiceStatus: ...

    async def navigate_turn(
        self, task_id: str, action: str
    ) -> WorkerTurnHistory: ...

    async def fork_task(self, task_id: str, client_fork_id: str) -> TaskRecord: ...

    async def create_subtask(
        self, task_id: str, request: SubtaskRequest
    ) -> SubtaskRecord: ...

    async def merge_subtask(
        self, task_id: str, child_task_id: str, operation_id: str
    ) -> SubtaskRecord: ...

    async def export_task(self, task_id: str) -> WorkerTaskExport: ...

    async def prepare_writeback_candidate(
        self, task_id: str
    ) -> WritebackCandidate: ...


@runtime_checkable
class InteractionProjection(Protocol):
    """Read-side task projection consumed by APIs, SDKs and SSE."""

    def find_task_by_idempotency(
        self, origin: Origin, client_task_id: str
    ) -> TaskRecord | None: ...

    def get_task(self, task_id: str) -> TaskRecord: ...

    def get_task_capability_snapshot(
        self, task_id: str
    ) -> TaskCapabilitySnapshot | None: ...

    def list_tasks(self, *, origin: Origin | None = None) -> Sequence[TaskRecord]: ...

    def list_events(
        self, task_id: str, *, after: int = 0, limit: int = 500
    ) -> Sequence[WorkerEvent]: ...

    def latest_plan(self, task_id: str) -> WorkerPlan | None: ...

    def latest_todo(self, task_id: str) -> WorkerTodo | None: ...

    def list_questions(self, task_id: str) -> Sequence[WorkerQuestion]: ...

    def list_approvals(self, task_id: str) -> Sequence[WorkerApproval]: ...

    def get_approval(self, approval_id: str) -> WorkerApproval: ...

    def get_operation(self, operation_id: str) -> WorkerOperation: ...

    def list_evidence(
        self, task_id: str, *, current_tree_hash: str | None = None
    ) -> Sequence[WorkerEvidence]: ...

    def list_artifacts(self, task_id: str) -> Sequence[WorkerArtifact]: ...

    def read_artifact(self, task_id: str, artifact_id: str) -> bytes: ...

    def list_children(self, task_id: str) -> Sequence[TaskRecord]: ...

    def list_subtasks(self, task_id: str) -> Sequence[SubtaskRecord]: ...

    def turn_history(self, task_id: str) -> WorkerTurnHistory: ...

    def budget_usage(self, task_id: str) -> WorkerBudgetUsage: ...

    def current_tree_hash(self, task_id: str) -> str | None: ...

    def workspace_tree(self, task_id: str) -> WorkspaceTreeProjection: ...

    def read_workspace_entry(self, task_id: str, entry_id: str) -> bytes: ...

    def workspace_diff(
        self, task_id: str, *, detect_renames: bool = True
    ) -> bytes: ...


@runtime_checkable
class EvaluationAdapter(Protocol):
    """Optional evaluation-only operations, absent from production profiles."""

    @property
    def enabled(self) -> bool: ...

    async def attestation(self) -> Mapping[str, Any]: ...

    def arm_fault(self, task_id: str, component: str, point: str) -> None: ...

    def export_workspace(
        self, task_id: str, *, harness_v3: bool
    ) -> WorkerArtifact: ...


@dataclass(frozen=True, slots=True)
class CodingSubstrateHandle:
    control_plane: TaskControlPlane
    projection: InteractionProjection
    harness_supervisor: HarnessSupervisor
    harness_driver: HarnessDriver
    execution_backend: ExecutionBackend
    evaluation: EvaluationAdapter | None = None


SubtaskHandler = Callable[[str, SubtaskRequest], Awaitable[SubtaskRecord]]
