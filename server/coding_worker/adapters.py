from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from .contracts import (
    Origin,
    SubtaskRecord,
    SubtaskRequest,
    TaskCreateRequest,
    TaskRecord,
    TaskState,
    WorkerApproval,
    WorkerArtifact,
    WorkerBudgetUsage,
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
from .ports import WritebackCandidate
from .ports import (
    CodingSubstrateHandle,
    CodingSubstrateStatus,
    HarnessCapabilityObservation,
    PreviewServiceStatus,
    TaskCapabilitySnapshot,
    WorkspaceTreeProjection,
)
from .provider import (
    CodingAgentProvider,
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderEvent,
    ProviderOpenRequest,
    ProviderSession,
)
from .service import CodingWorkerService
from .store import WorkerConflictError, WorkerNotFoundError


class LegacyHarnessDriver:
    """V19 adapter for the persisted Provider v4 private contract."""

    def __init__(self, provider: CodingAgentProvider) -> None:
        self._provider = provider

    @property
    def controller_generation(self) -> int:
        value = getattr(self._provider, "controller_generation", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    async def capabilities(self) -> ProviderCapabilities:
        return await self._provider.capabilities()

    async def capabilities_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, ProviderCapabilities | None]:
        reader = getattr(self._provider, "slot_capabilities", None)
        if callable(reader):
            values = await reader()
            return {slot_id: values.get(slot_id) for slot_id in slot_ids}
        try:
            capabilities = await self._provider.capabilities()
        except Exception:
            capabilities = None
        return {slot_id: capabilities for slot_id in slot_ids}

    async def harness_attestations(self) -> dict[str, dict[str, Any]]:
        reader = getattr(self._provider, "harness_attestations", None)
        if not callable(reader):
            return {}
        return await reader()

    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        return await self._provider.open(request)

    def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        return self._provider.message(session, text)

    async def cancel(self, session: ProviderSession) -> bool:
        return await self._provider.cancel(session)

    async def interrupt_turn(self, session: ProviderSession) -> bool:
        return await self._provider.interrupt_turn(session)

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        return await self._provider.checkpoint(session)

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        return await self._provider.restore(request, checkpoint)

    async def close(self, session: ProviderSession) -> None:
        await self._provider.close(session)


class LegacyExecutionBackend:
    """Separates process execution from the harness binding during strangling."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    async def run_process(self, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.run_process(**kwargs)

    async def run_shell(self, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.run_shell(**kwargs)

    async def start_service(self, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.start_service(**kwargs)

    async def service_status(self, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.service_status(**kwargs)

    async def service_input(self, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.service_input(**kwargs)

    async def stop_service(self, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.stop_service(**kwargs)

    async def code_intelligence(self, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.code_intelligence(**kwargs)

    async def bind_task(self, task_id: str, workspace_id: str) -> None:
        binder = getattr(self._backend, "bind_task", None)
        if callable(binder):
            await binder(task_id, workspace_id)

    async def close_task(self, task_id: str, workspace_id: str) -> None:
        closer = getattr(self._backend, "close_task", None)
        if callable(closer):
            await closer(task_id, workspace_id)


class StoreInteractionProjection:
    """Read-only facade over the V14-V18 stores and workspace projection."""

    def __init__(self, service: CodingWorkerService) -> None:
        self._service = service

    def find_task_by_idempotency(
        self, origin: Origin, client_task_id: str
    ) -> TaskRecord | None:
        return self._service.store.find_task_by_idempotency(origin, client_task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        return self._service.store.get_task(task_id)

    def get_task_capability_snapshot(
        self, task_id: str
    ) -> TaskCapabilitySnapshot | None:
        snapshot = self._service.store.get_task_capability_snapshot(task_id)
        if snapshot is None:
            return None
        return TaskCapabilitySnapshot(
            task_id=snapshot.task_id,
            binding_sha256=snapshot.binding_sha256,
            snapshot=snapshot.snapshot,
            observed_at=snapshot.observed_at,
            expires_at=snapshot.expires_at,
        )

    def list_tasks(self, *, origin: Origin | None = None) -> Sequence[TaskRecord]:
        return self._service.store.list_tasks(origin=origin)

    def list_events(
        self, task_id: str, *, after: int = 0, limit: int = 500
    ) -> Sequence[WorkerEvent]:
        return self._service.store.list_events(task_id, after=after, limit=limit)

    def latest_plan(self, task_id: str) -> WorkerPlan | None:
        return self._service.store.latest_plan(task_id)

    def latest_todo(self, task_id: str) -> WorkerTodo | None:
        return self._service.store.latest_todo(task_id)

    def list_questions(self, task_id: str) -> Sequence[WorkerQuestion]:
        return self._service.store.list_questions(task_id)

    def list_approvals(self, task_id: str) -> Sequence[WorkerApproval]:
        return self._service.store.list_approvals(task_id)

    def get_approval(self, approval_id: str) -> WorkerApproval:
        return self._service.store.get_approval(approval_id)

    def get_operation(self, operation_id: str) -> WorkerOperation:
        return self._service.store.get_operation(operation_id)

    def list_evidence(
        self, task_id: str, *, current_tree_hash: str | None = None
    ) -> Sequence[WorkerEvidence]:
        return self._service.store.list_evidence(
            task_id, current_tree_hash=current_tree_hash
        )

    def list_artifacts(self, task_id: str) -> Sequence[WorkerArtifact]:
        return self._service.store.list_artifacts(task_id)

    def read_artifact(self, task_id: str, artifact_id: str) -> bytes:
        return self._service.store.read_artifact(artifact_id, task_id=task_id)

    def list_children(self, task_id: str) -> Sequence[TaskRecord]:
        return self._service.store.list_children(task_id)

    def list_subtasks(self, task_id: str) -> Sequence[SubtaskRecord]:
        return self._service.store.list_subtasks(task_id)

    def turn_history(self, task_id: str) -> WorkerTurnHistory:
        return self._service.store.turn_history(task_id)

    def budget_usage(self, task_id: str) -> WorkerBudgetUsage:
        return self._service.store.budget_usage(task_id)

    def current_tree_hash(self, task_id: str) -> str | None:
        task = self._service.store.get_task(task_id)
        if task.workspace_id is None:
            return None
        return self._service.workspace_broker.current_tree_hash(task.workspace_id)

    def workspace_tree(self, task_id: str) -> WorkspaceTreeProjection:
        task = self._workspace_task(task_id)
        entries = self._service.workspace_broker.tree(task.workspace_id)
        return WorkspaceTreeProjection(
            workspace_id=task.workspace_id,
            tree_hash=self._service.workspace_broker.current_tree_hash(
                task.workspace_id
            ),
            entries=tuple(item.model_dump(mode="json") for item in entries),
        )

    def read_workspace_entry(self, task_id: str, entry_id: str) -> bytes:
        task = self._workspace_task(task_id)
        return self._service.workspace_broker.read_entry(task.workspace_id, entry_id)

    def workspace_diff(
        self, task_id: str, *, detect_renames: bool = True
    ) -> bytes:
        task = self._workspace_task(task_id)
        return self._service.workspace_broker.diff(
            task.workspace_id, detect_renames=detect_renames
        )

    def _workspace_task(self, task_id: str) -> TaskRecord:
        task = self._service.store.get_task(task_id)
        if task.workspace_id is None:
            raise WorkerConflictError(
                "Workspace is not ready.", code="workspace_not_ready"
            )
        return task


class LegacyTaskControlPlane:
    """Use-case facade over the current persistent scheduler."""

    def __init__(
        self, service: CodingWorkerService, *, network_enabled: bool = False
    ) -> None:
        self._service = service
        self._network_enabled = network_enabled

    def status(self) -> CodingSubstrateStatus:
        return CodingSubstrateStatus(
            max_active_tasks=self._service.max_active_tasks,
            retention_seconds=self._service.store.retention_seconds,
            network_enabled=self._network_enabled,
            acceptance_checks=tuple(
                sorted(
                    self._service.tool_broker.frozen_checks
                    if self._service.tool_broker is not None
                    else ()
                )
            ),
        )

    async def refresh_harness_capabilities(self) -> None:
        await self._service.refresh_provider_capabilities()

    def cached_harness_capabilities(self) -> Sequence[ProviderCapabilities]:
        return self._service.cached_provider_capabilities()

    async def harness_capability_observation(
        self, route_id: str
    ) -> HarnessCapabilityObservation:
        item = await self._service.provider_capability_observation(route_id)
        return HarnessCapabilityObservation(
            capabilities=item.capabilities,
            binding_sha256=item.binding_sha256,
            observed_at=item.observed_at,
            expires_at=item.expires_at,
            reason=item.reason,
        )

    async def create_task(
        self, origin: Origin, request: TaskCreateRequest
    ) -> TaskRecord:
        return await self._service.create_task(origin, request)

    async def append_message(self, task_id: str, text: str) -> TaskRecord:
        return await self._service.append_message(task_id, text)

    async def answer_question(
        self, task_id: str, question_id: str, answer: WorkerQuestionAnswer
    ) -> TaskRecord:
        return await self._service.answer_question(task_id, question_id, answer)

    async def pause(self, task_id: str) -> TaskRecord:
        return await self._service.pause(task_id)

    async def resume(self, task_id: str) -> TaskRecord:
        return await self._service.resume(task_id)

    async def cancel(self, task_id: str) -> TaskRecord:
        return await self._service.cancel(task_id)

    def decide_approval(
        self,
        task_id: str,
        approval_id: str,
        *,
        approved: bool,
        task_scope: bool,
        ttl_seconds: int,
    ) -> WorkerApproval:
        approval = self._service.store.get_approval(approval_id)
        if approval.task_id != task_id:
            raise WorkerNotFoundError(
                "Approval was not found.", code="approval_not_found"
            )
        if approval.capability == "shell" and task_scope:
            raise WorkerConflictError(
                "Shell approval is always bound to one exact operation.",
                code="shell_task_approval_forbidden",
            )
        decided = self._service.store.decide_approval(
            approval_id,
            approved=approved,
            task_scope=task_scope,
            ttl_seconds=ttl_seconds,
        )
        self._service.settle_approval_state(task_id)
        return decided

    def set_pinned(self, task_id: str, pinned: bool) -> TaskRecord:
        return self._service.store.set_pinned(task_id, pinned)

    def delete_task(self, task_id: str) -> None:
        task = self._service.store.get_task(task_id)
        if self._service.store.delete_task(task_id) and task.workspace_id is not None:
            self._service.workspace_broker.delete(task.workspace_id)

    async def preview_service_status(
        self, task_id: str, service_id: str
    ) -> PreviewServiceStatus:
        task = self._service.store.get_task(task_id)
        if task.workspace_id is None:
            raise WorkerConflictError(
                "Workspace is not ready.", code="workspace_not_ready"
            )
        broker = self._service.tool_broker
        if broker is None or broker.executor is None:
            raise WorkerConflictError(
                "Preview service is unavailable.", code="preview_unavailable"
            )
        result = await broker.executor.service_status(
            task_id=task_id,
            workspace_id=task.workspace_id,
            service_id=service_id,
        )
        port = result.get("preview_port")
        return PreviewServiceStatus(
            state=str(result.get("state", "unknown")),
            preview_port=(
                port if isinstance(port, int) and not isinstance(port, bool) else None
            ),
            slot_id=self._service.workspace_broker.workspace_slot(
                task.workspace_id
            ),
        )

    async def navigate_turn(
        self, task_id: str, action: str
    ) -> WorkerTurnHistory:
        return await self._service.navigate_turn(task_id, action)

    async def fork_task(self, task_id: str, client_fork_id: str) -> TaskRecord:
        return await self._service.fork_task(task_id, client_fork_id)

    async def create_subtask(
        self, task_id: str, request: SubtaskRequest
    ) -> SubtaskRecord:
        return await self._service.create_subtask(task_id, request)

    async def merge_subtask(
        self, task_id: str, child_task_id: str, operation_id: str
    ) -> SubtaskRecord:
        return await self._service.merge_subtask(
            task_id, child_task_id, operation_id
        )

    async def export_task(self, task_id: str) -> WorkerTaskExport:
        return await self._service.export_task(task_id)

    async def prepare_writeback_candidate(
        self, task_id: str
    ) -> WritebackCandidate:
        task = self._service.store.get_task(task_id)
        if (
            task.state is not TaskState.COMPLETED
            or task.workspace_id is None
            or task.spec.workspace_source.kind != "host_snapshot"
        ):
            raise WorkerConflictError(
                "Worker task is not writeback ready.",
                code="worker_task_not_writeback_ready",
            )
        harness = self._service.harness_runner
        if harness is None or not harness.acceptance_satisfied(task_id):
            raise WorkerConflictError(
                "Worker acceptance evidence is invalid.",
                code="worker_acceptance_invalidated",
            )
        workspace = self._service.workspace_broker
        before = workspace.current_tree_hash(task.workspace_id)
        patch = workspace.diff(task.workspace_id, detect_renames=False)
        after = workspace.current_tree_hash(task.workspace_id)
        if before != after or not harness.acceptance_satisfied(task_id):
            raise WorkerConflictError(
                "Worker acceptance evidence is invalid.",
                code="worker_acceptance_invalidated",
            )
        return WritebackCandidate(
            task_id=task.task_id,
            source_id=task.spec.workspace_source.source_id,
            revision=task.spec.workspace_source.revision,
            workspace_tree_hash=after,
            patch_sha256=hashlib.sha256(patch).hexdigest(),
            patch=patch,
        )


def legacy_substrate_from_service(
    service: CodingWorkerService,
    *,
    execution_backend: Any | None = None,
    evaluation: Any | None = None,
    network_enabled: bool = False,
) -> CodingSubstrateHandle:
    """Test/legacy composition helper; callers receive ports, never the service."""

    driver = (
        service.provider
        if isinstance(service.provider, LegacyHarnessDriver)
        else LegacyHarnessDriver(service.provider)
    )
    backend = LegacyExecutionBackend(
        execution_backend
        or (service.tool_broker.executor if service.tool_broker is not None else None)
        or service.provider
    )
    return CodingSubstrateHandle(
        control_plane=LegacyTaskControlPlane(
            service, network_enabled=network_enabled
        ),
        projection=StoreInteractionProjection(service),
        harness_driver=driver,
        execution_backend=backend,
        evaluation=evaluation,
    )
