from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol

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
    CodingSubstrateError,
    CodingSubstrateStatus,
    HarnessCapabilityObservation,
    PreviewServiceStatus,
    TaskCapabilitySnapshot,
    WorkspaceTreeProjection,
)
from .harness_contracts import (
    HarnessCapabilities,
    HarnessCheckpoint,
    HarnessEvent,
    HarnessOpenRequest,
    HarnessSession,
)
from .harness_driver import HarnessDriverProtocolError, ProviderV4HarnessTranslator
from .harness_protocol import HarnessBinding, HarnessDescriptorObservation
from .provider import (
    CodingAgentProvider,
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderEvent,
    ProviderEventKind,
    ProviderOpenRequest,
    ProviderSession,
)
from .service import CodingWorkerService
from .store import WorkerConflictError, WorkerNotFoundError


class LegacyProviderBinding(CodingAgentProvider, Protocol):
    pass


class LegacySupervisorBinding(Protocol):
    controller_generation: int

    async def capabilities(self) -> ProviderCapabilities: ...

    async def capabilities_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, ProviderCapabilities | None]: ...

    async def harness_attestations(self) -> dict[str, dict[str, Any]]: ...

    async def harness_descriptors_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, HarnessDescriptorObservation | None]: ...


class LegacyHarnessSupervisor:
    """V20 process, health and generation adapter for Provider v4 sidecars."""

    def __init__(self, provider: LegacySupervisorBinding) -> None:
        self._provider = provider

    @property
    def controller_generation(self) -> int:
        value = self._provider.controller_generation
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    async def capabilities(self) -> HarnessCapabilities:
        value = await self._provider.capabilities()
        return HarnessCapabilities.model_validate(value.model_dump(mode="json"))

    async def capabilities_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, HarnessCapabilities | None]:
        values = await self._provider.capabilities_for_slots(slot_ids)
        return {
            slot_id: (
                HarnessCapabilities.model_validate(value.model_dump(mode="json"))
                if value is not None
                else None
            )
            for slot_id, value in values.items()
        }

    async def harness_attestations(self) -> dict[str, dict[str, Any]]:
        return await self._provider.harness_attestations()

    async def harness_descriptors_for_slots(
        self, slot_ids: Sequence[str]
    ) -> Mapping[str, HarnessDescriptorObservation | None]:
        return await self._provider.harness_descriptors_for_slots(slot_ids)


class LegacyHarnessDriver:
    """V20 session/turn adapter for the persisted Provider v4 private contract."""

    def __init__(self, provider: LegacyProviderBinding) -> None:
        self._provider = provider
        self._sessions: dict[tuple[str, str], ProviderSession] = {}
        self._translators: dict[
            tuple[str, str], ProviderV4HarnessTranslator
        ] = {}
        self._active_turns: dict[tuple[str, str], str] = {}
        self._provider_streams: dict[
            tuple[str, str, str], AsyncIterator[ProviderEvent]
        ] = {}

    @staticmethod
    def _session_key(session: HarnessSession | ProviderSession) -> tuple[str, str]:
        return session.task_id, session.session_id

    @staticmethod
    def _public_v20_event(
        event: ProviderEvent,
        *,
        binding: HarnessBinding,
        turn_id: str,
    ) -> ProviderEvent:
        """Replace supplier-local tool ids before an event reaches persistence.

        Provider-v4 needs the original id inside its private stream to match a
        tool result to its start frame.  The public ledger only needs a stable
        correlation id, so derive one from the frozen task binding and turn.
        """

        if event.kind not in {
            ProviderEventKind.TOOL_STARTED,
            ProviderEventKind.TOOL_COMPLETED,
        }:
            return event
        data = dict(event.data)
        raw_operation_id = str(data["operation_id"])
        digest = hashlib.sha256(
            "\0".join(
                (binding.binding_sha256, turn_id, raw_operation_id)
            ).encode("utf-8")
        ).hexdigest()
        data["operation_id"] = f"harness_call_{digest[:32]}"
        return event.model_copy(update={"data": data})

    @staticmethod
    def _provider_request(request: HarnessOpenRequest) -> ProviderOpenRequest:
        return ProviderOpenRequest.model_validate(request.model_dump(mode="json"))

    @staticmethod
    def _provider_checkpoint(checkpoint: HarnessCheckpoint) -> ProviderCheckpoint:
        return ProviderCheckpoint.model_validate(checkpoint.model_dump(mode="json"))

    @staticmethod
    def _harness_session(session: ProviderSession) -> HarnessSession:
        return HarnessSession(
            session_id=session.session_id,
            task_id=session.task_id,
            capabilities=HarnessCapabilities.model_validate(
                session.provider_capabilities.model_dump(mode="json")
            ),
        )

    def _require_provider_session(self, session: HarnessSession) -> ProviderSession:
        provider_session = self._sessions.get(self._session_key(session))
        if provider_session is None or provider_session.task_id != session.task_id:
            raise CodingSubstrateError(
                "Harness session is unavailable.",
                code="harness_session_unavailable",
                status=409,
            )
        return provider_session

    @staticmethod
    def _v20_boundary_error(exc: Exception) -> CodingSubstrateError:
        code = getattr(exc, "code", None)
        if code in {
            "harness_transport_unavailable",
            "harness_protocol_invalid",
            "harness_driver_internal",
            "harness_authentication_failed",
            "harness_rate_limited",
            "harness_policy_rejected",
            "harness_budget_exhausted",
            "harness_interrupted",
        }:
            return CodingSubstrateError(
                "Harness request failed.", code=str(code), status=503
            )
        if isinstance(exc, (OSError, TimeoutError)) or code in {
            "provider_unavailable",
            "provider_offline",
            "provider_stream_ended",
            "provider_transport_unavailable",
        }:
            return CodingSubstrateError(
                "Harness transport is unavailable.",
                code="harness_transport_unavailable",
                status=503,
            )
        if code in {
            "provider_unauthorized",
            "provider_credential_unavailable",
        }:
            return CodingSubstrateError(
                "Harness authentication failed.",
                code="harness_authentication_failed",
                status=503,
            )
        if isinstance(exc, (HarnessDriverProtocolError, ValueError)) or code in {
            "provider_invalid_response",
            "provider_response_too_large",
            "provider_request_too_large",
            "provider_request_invalid",
            "provider_endpoint_invalid",
            "provider_capability_mismatch",
            "provider_controller_stale",
            "provider_session_busy",
            "provider_slot_busy",
            "provider_version_mismatch",
            "session_not_found",
            "checkpoint_invalid",
            "harness_session_unavailable",
        }:
            return CodingSubstrateError(
                "Harness protocol response is invalid.",
                code="harness_protocol_invalid",
                status=502,
            )
        return CodingSubstrateError(
            "Harness driver failed internally.",
            code="harness_driver_internal",
            status=502,
        )

    @classmethod
    def _raise_boundary(cls, exc: Exception, *, v20: bool) -> None:
        if v20:
            raise cls._v20_boundary_error(exc) from exc
        raise exc

    def _bind(
        self,
        session: ProviderSession,
        binding: HarnessBinding | None,
    ) -> None:
        key = self._session_key(session)
        self._sessions[key] = session
        if binding is not None:
            self._translators[key] = ProviderV4HarnessTranslator(
                binding, session
            )

    async def open(
        self,
        request: HarnessOpenRequest,
        *,
        binding: HarnessBinding | None = None,
    ) -> HarnessSession:
        try:
            session = await self._provider.open(self._provider_request(request))
            self._bind(session, binding)
            return self._harness_session(session)
        except Exception as exc:
            self._raise_boundary(exc, v20=binding is not None)
            raise AssertionError("unreachable")

    def message(
        self,
        session: HarnessSession,
        text: str,
        *,
        turn_id: str,
    ) -> AsyncIterator[HarnessEvent]:
        provider_session = self._require_provider_session(session)
        key = self._session_key(session)
        translator = self._translators.get(key)

        async def stream() -> AsyncIterator[HarnessEvent]:
            failed = False
            finished = False
            provider_stream = self._provider.message(provider_session, text).__aiter__()
            stream_key = (*key, turn_id)
            self._provider_streams[stream_key] = provider_stream
            try:
                if translator is not None:
                    translator.start_turn(turn_id)
                    self._active_turns[key] = turn_id
                async for event in provider_stream:
                    if translator is not None:
                        translator.accept(event, turn_id=turn_id)
                        event = self._public_v20_event(
                            event,
                            binding=translator.session.binding,
                            turn_id=turn_id,
                        )
                    if event.kind in {
                        ProviderEventKind.TURN_COMPLETED,
                        ProviderEventKind.CANCELLED,
                        ProviderEventKind.FAILED,
                    }:
                        finished = True
                    yield HarnessEvent.model_validate(event.model_dump(mode="json"))
                finished = True
            except Exception as exc:
                failed = True
                finished = True
                self._raise_boundary(exc, v20=translator is not None)
            finally:
                owned_stream = self._provider_streams.get(stream_key)
                if (
                    (finished or translator is None)
                    and owned_stream is provider_stream
                ):
                    try:
                        await self._close_provider_stream(key, turn_id=turn_id)
                    except Exception as exc:
                        if not failed:
                            self._raise_boundary(exc, v20=translator is not None)
                # A completed stream can be finalized after its successor has
                # already started.  Only the stream that still owns this exact
                # turn may clear or interrupt it; otherwise stale cleanup would
                # fence the newer turn as a protocol violation.
                if (
                    finished
                    and translator is not None
                    and self._active_turns.get(key) == turn_id
                ):
                    self._active_turns.pop(key, None)
                    try:
                        translator.interrupt_turn(turn_id=turn_id)
                    except Exception as exc:
                        if not failed:
                            self._raise_boundary(exc, v20=True)

        return stream()

    async def _close_provider_stream(
        self, key: tuple[str, str], *, turn_id: str | None = None
    ) -> None:
        stream_keys = (
            [(*key, turn_id)]
            if turn_id is not None
            else [item for item in tuple(self._provider_streams) if item[:2] == key]
        )
        failure: Exception | None = None
        for stream_key in stream_keys:
            current = self._provider_streams.pop(stream_key, None)
            if current is None:
                continue
            close = getattr(current, "aclose", None)
            if close is None:
                continue
            try:
                await close()
            except Exception as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure

    async def steer(self, session: HarnessSession, text: str) -> bool:
        # Provider v4 queues steering through the control plane at a durable
        # tool boundary; it has no independent in-flight steer primitive.
        return False

    async def cancel(self, session: HarnessSession) -> bool:
        provider_session = self._require_provider_session(session)
        key = self._session_key(session)
        v20 = key in self._translators
        try:
            # ``cancel`` fences the whole session but does not own the task
            # currently awaiting the nested provider iterator.  The control
            # plane cancels that reader next and ``close`` performs the final
            # deterministic stream cleanup.  Calling ``aclose`` here races
            # with the in-flight ``anext`` and can raise ``async generator is
            # already running`` before the task is fenced.
            return await self._provider.cancel(provider_session)
        except Exception as exc:
            self._raise_boundary(exc, v20=v20)
            raise AssertionError("unreachable")

    async def interrupt_turn(self, session: HarnessSession) -> bool:
        provider_session = self._require_provider_session(session)
        key = self._session_key(session)
        translator = self._translators.get(key)
        failure: Exception | None = None
        interrupted = False
        try:
            interrupted = await self._provider.interrupt_turn(provider_session)
        except Exception as exc:
            failure = exc
        try:
            await self._close_provider_stream(key)
        except Exception as exc:
            if failure is None:
                failure = exc
        active_turn = self._active_turns.pop(key, None)
        if translator is not None and active_turn is not None:
            try:
                translator.interrupt_turn(turn_id=active_turn)
            except Exception as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            self._raise_boundary(failure, v20=translator is not None)
            raise AssertionError("unreachable")
        return interrupted

    async def checkpoint(self, session: HarnessSession) -> HarnessCheckpoint:
        key = self._session_key(session)
        try:
            checkpoint = await self._provider.checkpoint(
                self._require_provider_session(session)
            )
            return HarnessCheckpoint.model_validate(
                checkpoint.model_dump(mode="json")
            )
        except Exception as exc:
            self._raise_boundary(exc, v20=key in self._translators)
            raise AssertionError("unreachable")

    async def restore(
        self,
        request: HarnessOpenRequest,
        checkpoint: HarnessCheckpoint,
        *,
        binding: HarnessBinding | None = None,
    ) -> HarnessSession:
        try:
            session = await self._provider.restore(
                self._provider_request(request), self._provider_checkpoint(checkpoint)
            )
            self._bind(session, binding)
            return self._harness_session(session)
        except Exception as exc:
            self._raise_boundary(exc, v20=binding is not None)
            raise AssertionError("unreachable")

    async def close(self, session: HarnessSession) -> None:
        provider_session = self._require_provider_session(session)
        key = self._session_key(session)
        translator = self._translators.get(key)
        failure: Exception | None = None
        try:
            await self._close_provider_stream(key)
        except Exception as exc:
            failure = exc
        try:
            await self._provider.close(provider_session)
        except Exception as exc:
            if failure is None:
                failure = exc
        finally:
            self._active_turns.pop(key, None)
            for stream_key in tuple(self._provider_streams):
                if stream_key[:2] == key:
                    self._provider_streams.pop(stream_key, None)
            self._translators.pop(key, None)
            if translator is not None:
                try:
                    translator.close()
                except Exception as exc:
                    if failure is None:
                        failure = exc
            self._sessions.pop(key, None)
        if failure is not None:
            self._raise_boundary(failure, v20=translator is not None)


class LegacyExecutionBackend:
    """Separates process execution from the harness binding during strangling."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    async def _invoke(self, method_name: str, **kwargs: Any) -> dict[str, Any]:
        method = getattr(self._backend, method_name, None)
        if not callable(method):
            raise CodingSubstrateError(
                "Execution backend capability is unavailable.",
                code="execution_backend_unavailable",
                status=503,
            )
        return await method(**kwargs)

    async def run_process(self, **kwargs: Any) -> dict[str, Any]:
        return await self._invoke("run_process", **kwargs)

    async def run_shell(self, **kwargs: Any) -> dict[str, Any]:
        return await self._invoke("run_shell", **kwargs)

    async def start_service(self, **kwargs: Any) -> dict[str, Any]:
        return await self._invoke("start_service", **kwargs)

    async def service_status(self, **kwargs: Any) -> dict[str, Any]:
        return await self._invoke("service_status", **kwargs)

    async def service_input(self, **kwargs: Any) -> dict[str, Any]:
        return await self._invoke("service_input", **kwargs)

    async def stop_service(self, **kwargs: Any) -> dict[str, Any]:
        return await self._invoke("stop_service", **kwargs)

    async def code_intelligence(self, **kwargs: Any) -> dict[str, Any]:
        return await self._invoke("code_intelligence", **kwargs)

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

    def cached_harness_capabilities(self) -> Sequence[HarnessCapabilities]:
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
    supervisor = service.harness_supervisor
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
        harness_supervisor=supervisor,
        harness_driver=driver,
        execution_backend=backend,
        evaluation=evaluation,
    )
