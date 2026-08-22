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
    WorkerEvent,
    WorkerQuestionAnswer,
    WorkerTaskExport,
    WorkerTurnHistory,
)
from .provider import (
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderEvent,
    ProviderOpenRequest,
    ProviderSession,
)


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


@runtime_checkable
class HarnessDriver(Protocol):
    """Private model-loop boundary.

    Provider v4 request, event and checkpoint models remain the compatibility
    carrier in V19.  New ACP or Codex adapters must normalize into these models
    instead of extending the public Worker API with supplier frames.
    """

    async def capabilities(self) -> ProviderCapabilities: ...

    async def capabilities_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, ProviderCapabilities | None]: ...

    @property
    def controller_generation(self) -> int: ...

    async def open(self, request: ProviderOpenRequest) -> ProviderSession: ...

    def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]: ...

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

    async def append_message(self, task_id: str, text: str) -> TaskRecord: ...

    async def answer_question(
        self, task_id: str, question_id: str, answer: WorkerQuestionAnswer
    ) -> TaskRecord: ...

    async def pause(self, task_id: str) -> TaskRecord: ...

    async def resume(self, task_id: str) -> TaskRecord: ...

    async def cancel(self, task_id: str) -> TaskRecord: ...

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

    def list_tasks(self, *, origin: Origin | None = None) -> Sequence[TaskRecord]: ...

    def list_events(
        self, task_id: str, *, after: int = 0, limit: int = 500
    ) -> Sequence[WorkerEvent]: ...


@runtime_checkable
class EvaluationAdapter(Protocol):
    """Optional evaluation-only operations, absent from production profiles."""

    @property
    def enabled(self) -> bool: ...

    async def attestation(self) -> Mapping[str, Any]: ...

    def arm_fault(self, task_id: str, component: str, point: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CodingSubstrateHandle:
    control_plane: TaskControlPlane
    projection: InteractionProjection
    harness_driver: HarnessDriver
    execution_backend: ExecutionBackend
    evaluation: EvaluationAdapter | None = None


SubtaskHandler = Callable[[str, SubtaskRequest], Awaitable[SubtaskRecord]]
