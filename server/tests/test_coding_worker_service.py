from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.adapters import LegacyHarnessDriver, LegacyHarnessSupervisor
from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    ApprovalStatus,
    OperationState,
    Origin,
    PolicyProfile,
    RuntimeProtocol,
    SessionLedgerKind,
    TaskBudget,
    TaskCreateRequest,
    TaskSpec,
    TaskState,
    TurnBarrier,
    TurnTransactionState,
    WorkspaceSource,
)
from server.coding_worker.evidence import HarnessRunner
from server.coding_worker.harness_protocol import (
    HarnessCapabilityState,
    HarnessDescriptor,
    HarnessDescriptorObservation,
    HarnessPersistenceLevel,
    HarnessToolOwnership,
)
from server.coding_worker.harness_contracts import HarnessOpenRequest
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderCapabilities,
    ProviderEvent,
    ProviderEventKind,
    ProviderFailureKind,
    ProviderCheckpoint,
    ProviderCheckpointCompatibility,
    ProviderOpenRequest,
    ProviderSession,
)
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError
from server.coding_worker.tool_broker import (
    FrozenCheck,
    ToolBroker,
    ToolBrokerError,
)
from server.coding_worker.workspace import (
    InMemoryWorkspaceSourceAdapter,
    WorkspaceBroker,
    WorkspaceError,
    WorkspaceSourceUnavailableError,
)


_V20_PREREQUISITE_FLAGS = (
    "CODING_WORKER_V16_ENABLED",
    "CODING_WORKER_INTERACTION_ENABLED",
    "CODING_WORKER_SESSION_CONTROLS_ENABLED",
    "CODING_WORKER_SUBAGENTS_ENABLED",
    "CODING_WORKER_V17_ENABLED",
)


def _enable_v20(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODING_WORKER_HARNESS_V20_ENABLED", "true")
    for name in _V20_PREREQUISITE_FLAGS:
        monkeypatch.setenv(name, "true")


@pytest.mark.asyncio
async def test_outer_cancellation_retrieves_driver_exception_that_finished_same_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    request = _request("cancel-finished-driver")
    task = service.store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="cancel-finished-driver"),
        )
    )
    service.store.transition(task.task_id, TaskState.PREPARING)
    task = service.store.transition(task.task_id, TaskState.RUNNING)

    async def fail_driver(*_args: object, **_kwargs: object) -> None:
        raise WorkerConflictError("late driver failure", code="task_state_conflict")

    real_sleep = asyncio.sleep

    async def cancel_outer_after_driver_finishes(
        futures: set[asyncio.Task[object]], *, timeout: float
    ) -> set[asyncio.Task[object]]:
        del timeout
        while not all(item.done() for item in futures):
            await real_sleep(0)
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await real_sleep(0)
        raise AssertionError("cancelled task continued")

    monkeypatch.setattr(service, "_drive_session_steps", fail_driver)
    monkeypatch.setattr(
        "server.coding_worker.service.asyncio.wait",
        cancel_outer_after_driver_finishes,
    )
    loop = asyncio.get_running_loop()
    reported: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        runner = asyncio.create_task(
            service._drive_session(
                task,
                object(),  # type: ignore[arg-type]
                resume_phase=None,
                resume_context=None,
                completed_turns=0,
                message_cursor=0,
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await runner
        del runner
        gc.collect()
        await real_sleep(0)
        assert not any(
            context.get("message") == "Task exception was never retrieved"
            for context in reported
        )
    finally:
        loop.set_exception_handler(previous_handler)


class _V20Supervisor:
    controller_generation = 7

    def __init__(self, provider: FakeCodingAgentProvider) -> None:
        self._provider = provider

    async def capabilities(self) -> ProviderCapabilities:
        return await self._provider.capabilities()

    async def capabilities_for_slots(
        self, slot_ids: tuple[str, ...]
    ) -> dict[str, ProviderCapabilities]:
        return await self._provider.capabilities_for_slots(slot_ids)

    async def harness_attestations(self) -> dict[str, dict[str, object]]:
        return {}

    async def harness_descriptors_for_slots(
        self, slot_ids: tuple[str, ...]
    ) -> dict[str, HarnessDescriptorObservation]:
        available = HarnessCapabilityState(supported=True, available=True)
        descriptor = HarnessDescriptor(
            protocol_id="modelmirror-provider-v4",
            protocol_version="4",
            implementation_version="fake-v20",
            schema_sha256="f" * 64,
            tool_ownership=HarnessToolOwnership.BROKER_ONLY,
            persistence=HarnessPersistenceLevel.SESSION_RESUME,
            capabilities={
                name: available
                for name in (
                    "cancel",
                    "checkpoint",
                    "interrupt",
                    "restore",
                    "streaming",
                    "tool_boundaries",
                    "usage",
                )
            },
        )
        return {
            slot_id: HarnessDescriptorObservation(
                descriptor=descriptor,
                sidecar_generation=hashlib.sha256(
                    slot_id.encode("utf-8")
                ).hexdigest()[:32],
            )
            for slot_id in slot_ids
        }


class _NoUsageProvider(FakeCodingAgentProvider):
    async def capabilities(self) -> ProviderCapabilities:
        return (await super().capabilities()).model_copy(
            update={"supports_usage": False}
        )


class _StalledProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.never_progress = asyncio.Event()
        self.stream_closed = asyncio.Event()

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        try:
            await self.never_progress.wait()
        finally:
            self.stream_closed.set()
        if False:  # pragma: no cover - keeps this an async generator
            yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


def _request(client_task_id: str) -> TaskCreateRequest:
    return TaskCreateRequest(
        client_task_id=client_task_id,
        objective=f"Complete {client_task_id}",
        workspace_source=WorkspaceSource(
            kind="manifest", source_id="source-01", revision="revision-01"
        ),
        acceptance=AcceptanceContract(
            contract_id="contract-01",
            required_checks=(
                AcceptanceCheck(check_id="pytest", label="pytest", kind="command"),
            ),
        ),
        model_route="coding/default",
    )


def _service(
    tmp_path: Path,
    provider: FakeCodingAgentProvider,
    *,
    files: dict[str, bytes] | None = None,
    route_context_tokens: dict[str, int] | None = None,
) -> CodingWorkerService:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    adapter = InMemoryWorkspaceSourceAdapter(
        {
            ("source-01", "revision-01"): files
            or {"main.py": b"print('ok')\n"}
        }
    )
    broker = WorkspaceBroker(
        tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
    )
    return CodingWorkerService(
        store=store,
        workspace_broker=broker,
        provider=LegacyHarnessDriver(provider),
        harness_supervisor=LegacyHarnessSupervisor(provider),
        route_context_tokens=route_context_tokens,
    )


class _RepairingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.repair: Callable[[], Awaitable[None]] | None = None

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        self.messages.append(text)
        if len(self.messages) == 2 and self.repair is not None:
            await self.repair()
        async for event in super().message(session, text):
            yield event


class _RestoreTrackingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.restore_count = 0
        self.message_count = 0

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        self.message_count += 1
        async for event in super().message(session, text):
            yield event

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        self.restore_count += 1
        return await super().restore(request, checkpoint)


class _OpenTrackingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.open_request: ProviderOpenRequest | None = None

    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        self.open_request = request
        return await super().open(request)


class _NoTurnInterruptProvider(FakeCodingAgentProvider):
    async def capabilities(self) -> ProviderCapabilities:
        return (await super().capabilities()).model_copy(
            update={"supports_turn_interrupt": False}
        )


class _LostTurnReceiptProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.message_count = 0
        self.checkpoint_count = 0
        self.repair: Callable[[], Awaitable[None]] | None = None

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        self.message_count += 1
        if self.message_count == 1 and self.repair is not None:
            await self.repair()
        async for event in super().message(session, text):
            yield event

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        self.checkpoint_count += 1
        if self.checkpoint_count == 1:
            raise OSError("simulated provider receipt loss")
        return await super().checkpoint(session)


class _SteeringProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.first_turn_started = asyncio.Event()
        self.release_first_turn = asyncio.Event()

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        self.messages.append(text)
        if len(self.messages) == 1:
            self.first_turn_started.set()
            await self.release_first_turn.wait()
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


class _LedgerProvider(FakeCodingAgentProvider):
    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind=ProviderEventKind.PLAN,
            data={
                "explanation": "public plan",
                "items": [{"step": "run the frozen check", "status": "in_progress"}],
            },
        )
        yield ProviderEvent(
            kind=ProviderEventKind.TODO,
            data={
                "items": [
                    {
                        "todo_id": "todo-01",
                        "content": "inspect evidence",
                        "status": "pending",
                    }
                ]
            },
        )
        yield ProviderEvent(
            kind=ProviderEventKind.TOOL_STARTED,
            data={
                "operation_id": "operation-01",
                "tool_name": "run_check",
                "summary": "run a frozen check",
            },
        )
        yield ProviderEvent(
            kind=ProviderEventKind.TOOL_COMPLETED,
            data={
                "operation_id": "operation-01",
                "tool_name": "run_check",
                "summary": "frozen check completed",
                "success": True,
                "artifact_id": None,
            },
        )
        yield ProviderEvent(
            kind=ProviderEventKind.MESSAGE,
            data={"text": "Public assistant result."},
        )
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


class _CompactionProvider(FakeCodingAgentProvider):
    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind=ProviderEventKind.PLAN,
            data={
                "explanation": "bounded plan",
                "items": [{"step": "repair next", "status": "in_progress"}],
            },
        )
        yield ProviderEvent(
            kind=ProviderEventKind.TODO,
            data={
                "items": [
                    {
                        "todo_id": "todo-compact",
                        "content": "preserve this todo",
                        "status": "pending",
                    }
                ]
            },
        )
        yield ProviderEvent(
            kind=ProviderEventKind.COMPACTION,
            data={"summary": "provider hint", "boundary_sequence": 999},
        )
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


class _UnsafeCompactionProvider(FakeCodingAgentProvider):
    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind=ProviderEventKind.TOOL_STARTED,
            data={
                "operation_id": "operation-open-at-compaction",
                "tool_name": "run_check",
                "summary": "still running",
            },
        )
        yield ProviderEvent(
            kind=ProviderEventKind.COMPACTION,
            data={"summary": "unsafe provider hint", "boundary_sequence": 999},
        )


class _BlockingCompactionProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(
            kind=ProviderEventKind.COMPACTION,
            data={"summary": "restart-safe hint", "boundary_sequence": 1},
        )
        await self.release.wait()
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


class _ApprovalParkingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.store: CodingWorkerStore | None = None
        self.stream_closed = asyncio.Event()
        self.resume_requested = asyncio.Event()
        self.messages: list[str] = []

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        assert self.store is not None
        self.messages.append(text)
        if len(self.messages) == 1:
            self.store.transition(session.task_id, TaskState.WAITING_APPROVAL)
            try:
                yield ProviderEvent(
                    kind=ProviderEventKind.MESSAGE,
                    data={"text": "Waiting for the exact command approval."},
                )
            finally:
                self.stream_closed.set()
            return
        self.resume_requested.set()
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


class _V17TurnParkingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.store: CodingWorkerStore | None = None
        self.messages: list[str] = []
        self.interruptions = 0

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        assert self.store is not None
        self.messages.append(text)
        if len(self.messages) == 1:
            yield ProviderEvent(
                kind=ProviderEventKind.TOOL_STARTED,
                data={
                    "operation_id": "provider-call-v17-approval",
                    "tool_name": "run_command",
                    "summary": "waiting for exact approval",
                },
            )
            turn = self.store.current_turn_transaction(session.task_id)
            assert turn is not None
            self.store.create_approval(
                task_id=session.task_id,
                operation_id="operation-v17-approval",
                capability="command",
                request={"argv": ["pytest"]},
            )
            self.store.begin_turn_parking(
                task_id=session.task_id,
                turn_id=turn.turn_id,
                barrier=TurnBarrier.APPROVAL,
            )
            for index in range(10):
                yield ProviderEvent(
                    kind=ProviderEventKind.MESSAGE,
                    data={"text": f"late event {index}"},
                )
            return
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)

    async def interrupt_turn(self, session: ProviderSession) -> bool:
        self.interruptions += 1
        return await super().interrupt_turn(session)


class _InterruptFailureParkingProvider(_V17TurnParkingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_calls = 0
        self.checkpoint_calls = 0
        self.close_calls = 0

    async def interrupt_turn(self, session: ProviderSession) -> bool:
        self.interruptions += 1
        raise OSError("simulated interrupt transport loss")

    async def cancel(self, session: ProviderSession) -> bool:
        self.cancel_calls += 1
        return await super().cancel(session)

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        self.checkpoint_calls += 1
        return await super().checkpoint(session)

    async def close(self, session: ProviderSession) -> None:
        self.close_calls += 1
        await super().close(session)


class _UnfenceableInterruptParkingProvider(_InterruptFailureParkingProvider):
    async def cancel(self, session: ProviderSession) -> bool:
        self.cancel_calls += 1
        raise OSError("simulated cancel transport loss")

    async def close(self, session: ProviderSession) -> None:
        self.close_calls += 1
        raise OSError("simulated close transport loss")


class _ShutdownParkingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.store: CodingWorkerStore | None = None
        self.checkpoint_calls = 0
        self.message_calls = 0
        self.parking_checkpoint_started = asyncio.Event()
        self.release_parking_checkpoint = asyncio.Event()

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        assert self.store is not None
        self.message_calls += 1
        turn = self.store.current_turn_transaction(session.task_id)
        assert turn is not None
        self.store.create_approvals_and_begin_turn_parking(
            task_id=session.task_id,
            turn_id=turn.turn_id,
            requests=(
                (
                    "operation-shutdown-parking"
                    if self.message_calls == 1
                    else "operation-shutdown-parking-replayed",
                    "command",
                    {"argv": ["pytest"]},
                ),
            ),
        )
        yield ProviderEvent(
            kind=ProviderEventKind.MESSAGE,
            data={"text": "late provider text after the durable barrier"},
        )

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        self.checkpoint_calls += 1
        if self.checkpoint_calls > 1:
            self.parking_checkpoint_started.set()
            await self.release_parking_checkpoint.wait()
        return await super().checkpoint(session)


class _HangingCloseProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__(block=asyncio.Event())
        self.close_started = asyncio.Event()
        self.never_close = asyncio.Event()

    async def close(self, session: ProviderSession) -> None:
        self.close_started.set()
        await self.never_close.wait()


class _CancellationResistantProvider(_HangingCloseProvider):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_started = asyncio.Event()
        self.release_cancel = asyncio.Event()

    async def cancel(self, session: ProviderSession) -> bool:
        self.cancel_started.set()
        try:
            await self.release_cancel.wait()
        except asyncio.CancelledError:
            await self.release_cancel.wait()
        return True


class _FailureDuringCancelProvider(FakeCodingAgentProvider):
    """Reproduce an abort frame racing the cancel API response."""

    def __init__(self) -> None:
        super().__init__()
        self.cancel_started = asyncio.Event()

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        await self.cancel_started.wait()
        yield ProviderEvent(
            kind=ProviderEventKind.FAILED,
            data={"failure_kind": ProviderFailureKind.UNAVAILABLE.value},
        )

    async def cancel(self, session: ProviderSession) -> bool:
        self.cancel_started.set()
        # Give the in-flight provider stream a chance to publish the abort
        # frame before the cancel RPC returns, matching OpenCode's behavior.
        await asyncio.sleep(0.05)
        return True


class _SlowCloseV17TurnParkingProvider(_V17TurnParkingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.close_count = 0

    async def close(self, session: ProviderSession) -> None:
        self.close_count += 1
        if self.close_count == 1:
            self.close_started.set()
            await self.release_close.wait()
        await super().close(session)


class _V17CompactionParkingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.store: CodingWorkerStore | None = None
        self.messages: list[str] = []

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        assert self.store is not None
        self.messages.append(text)
        if len(self.messages) == 1:
            turn = self.store.current_turn_transaction(session.task_id)
            assert turn is not None
            self.store.begin_turn_parking(
                task_id=session.task_id,
                turn_id=turn.turn_id,
                barrier=TurnBarrier.COMPACTION,
            )
            for index in range(10):
                yield ProviderEvent(
                    kind=ProviderEventKind.MESSAGE,
                    data={"text": f"late compaction event {index}"},
                )
            return
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


class _V17UsageCompactionProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        self.messages.append(text)
        if len(self.messages) == 1:
            yield ProviderEvent(
                kind=ProviderEventKind.USAGE,
                data={
                    "usage": {
                        "input_tokens": 7_500,
                        "output_tokens": 100,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "cost_microusd": None,
                    }
                },
            )
            for index in range(10):
                yield ProviderEvent(
                    kind=ProviderEventKind.MESSAGE,
                    data={"text": f"late usage event {index}"},
                )
            return
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


class _CompactionRestoreProvider(_RestoreTrackingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        self.messages.append(text)
        async for event in super().message(session, text):
            yield event


def _service_with_harness(
    tmp_path: Path, provider: FakeCodingAgentProvider
) -> tuple[CodingWorkerService, ToolBroker]:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(
        tmp_path / "worker",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source-01", "revision-01"): {"main.py": b"bad python\n"}}
            )
        },
        id_key=b"h" * 32,
    )
    broker = ToolBroker(
        store=store,
        workspace_broker=workspace,
        frozen_checks={
            "pytest": FrozenCheck(
                check_id="pytest",
                argv=(sys.executable, "-m", "py_compile", "main.py"),
            )
        },
    )
    harness = HarnessRunner(
        store=store, workspace_broker=workspace, tool_broker=broker
    )
    return (
        CodingWorkerService(
            store=store,
            workspace_broker=workspace,
            provider=LegacyHarnessDriver(provider),
            harness_supervisor=LegacyHarnessSupervisor(provider),
            harness_runner=harness,
            tool_broker=broker,
        ),
        broker,
    )


@pytest.mark.asyncio
async def test_new_task_requires_source_admission_before_persistence(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    request = _request("source-revision-changed").model_copy(
        update={
            "workspace_source": WorkspaceSource(
                kind="manifest",
                source_id="source-01",
                revision="revision-missing",
            )
        }
    )

    with pytest.raises(WorkspaceSourceUnavailableError) as rejected:
        await service.create_task(
            Origin(module="test", object_id="source-admission"), request
        )

    assert rejected.value.code == "workspace_source_unavailable"
    assert rejected.value.reason == "revision_changed"
    assert service.store.list_tasks() == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_exact_idempotent_retry_ignores_current_source_and_provider_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeCodingAgentProvider()
    service = _service(tmp_path, provider)
    origin = Origin(module="test", object_id="idempotent-admission")
    request = _request("idempotent-admission")
    created = await service.create_task(origin, request)

    async def unavailable_capabilities() -> ProviderCapabilities:
        raise RuntimeError("provider offline")

    monkeypatch.setattr(provider, "capabilities", unavailable_capabilities)
    service.workspace_broker._adapters.clear()

    same = await service.create_task(origin, request)

    assert same.task_id == created.task_id
    required, receipt = service.store.source_admission(created.task_id)
    assert required is True
    assert receipt is not None
    assert receipt.source == request.workspace_source
    assert [event.type for event in service.store.list_events(created.task_id)][:2] == [
        "task_created",
        "source_admitted",
    ]
    database = (tmp_path / "worker" / "coding-worker.sqlite3").read_bytes()
    assert request.workspace_source.source_id.encode("utf-8") not in database
    assert request.workspace_source.revision.encode("utf-8") not in database
    assert receipt.binding_sha256.encode("ascii") not in database
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_new_task_requires_frozen_broker_only_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    service = _service(tmp_path, FakeCodingAgentProvider())

    with pytest.raises(WorkerConflictError) as rejected:
        await service.create_task(
            Origin(module="test", object_id="v20-fail-closed"),
            _request("v20-fail-closed"),
        )

    assert rejected.value.code == "harness_v20_route_unavailable"
    assert service.store.list_tasks() == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_rejects_descriptor_capability_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    provider = _NoUsageProvider()
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)

    with pytest.raises(WorkerConflictError) as rejected:
        await service.create_task(
            Origin(module="test", object_id="v20-capability-mismatch"),
            _request("v20-capability-mismatch"),
        )

    assert rejected.value.code == "harness_v20_route_unavailable"
    assert service.store.list_tasks() == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_snapshot_binds_descriptor_and_disable_interrupts_without_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = asyncio.Event()
    provider = FakeCodingAgentProvider(block=blocker)
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    _enable_v20(monkeypatch)
    assert (await service.harness_supervisor.harness_descriptors_for_slots(("*",)))[
        "*"
    ].descriptor.tool_ownership is HarnessToolOwnership.BROKER_ONLY
    observation = await service.provider_capability_observation(
        "coding/default", force=True
    )
    assert service._v20_route_ready(observation), repr(observation)
    task = await service.create_task(
        Origin(module="test", object_id="v20-disable"),
        _request("v20-disable"),
    )
    await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.RUNNING
    )
    frozen = service.store.get_task_capability_snapshot(task.task_id)
    assert frozen is not None
    assert frozen.snapshot["harness_protocol"] == "v20"
    observations = frozen.snapshot["harness_descriptors"]
    assert isinstance(observations, list) and len(observations) == 1
    assert observations[0]["observation"]["descriptor"]["tool_ownership"] == (
        "broker_only"
    )
    service.harness_supervisor.controller_generation = 8
    with pytest.raises(WorkerConflictError) as stale_binding:
        await service._v20_binding_for_task(
            service.store.get_task(task.task_id), slot_id=None
        )
    assert stale_binding.value.code == "harness_binding_changed"
    service.harness_supervisor.controller_generation = 7

    monkeypatch.setenv("CODING_WORKER_HARNESS_V20_ENABLED", "false")
    await service._interrupt_v20_tasks_if_disabled()
    interrupted = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.INTERRUPTED
    )
    assert interrupted.reason == "harness_v20_disabled"
    with pytest.raises(WorkerConflictError) as resume_rejected:
        await service.resume(task.task_id)
    assert resume_rejected.value.code == "harness_v20_disabled"
    assert service.store.get_task_capability_snapshot(task.task_id) == frozen
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_stalled_harness_stream_is_transport_failure_not_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.coding_worker.service as service_module

    monkeypatch.setattr(
        service_module, "V20_HARNESS_EVENT_STALL_SECONDS", 0.05, raising=False
    )
    provider = _StalledProvider()
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    _enable_v20(monkeypatch)

    try:
        task = await service.create_task(
            Origin(module="test", object_id="v20-stream-stall"),
            _request("v20-stream-stall"),
        )
        failed = await asyncio.wait_for(
            service.wait_for(
                task.task_id, lambda item: item.state is TaskState.FAILED
            ),
            timeout=1,
        )
        await asyncio.wait_for(provider.stream_closed.wait(), timeout=1)

        assert failed.reason == "harness_transport_unavailable"
        assert service.store.budget_usage(task.task_id).active_seconds < 1
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_v20_translation_preserves_legacy_projection_without_side_effect_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run(label: str, *, v20: bool) -> list[dict[str, object]]:
        provider = FakeCodingAgentProvider()
        service = _service(tmp_path / label, provider)
        if v20:
            service.harness_supervisor = _V20Supervisor(provider)
            _enable_v20(monkeypatch)
        else:
            monkeypatch.setenv("CODING_WORKER_HARNESS_V20_ENABLED", "false")
        task = await service.create_task(
            Origin(module="test", object_id=label), _request(label)
        )
        await service.wait_for(
            task.task_id,
            lambda item: item.state in {TaskState.BLOCKED, TaskState.COMPLETED},
        )
        events = [
            event.payload
            for event in service.store.list_events(task.task_id)
            if event.type == "provider_event"
        ]
        assert service.store.list_operations(task.task_id) == []
        await service.shutdown()
        return events

    legacy = await run("legacy-shadow", v20=False)
    translated = await run("v20-shadow", v20=True)

    assert translated == legacy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    (
        (ProviderFailureKind.UNAVAILABLE, "harness_transport_unavailable"),
        (ProviderFailureKind.AUTHENTICATION, "harness_authentication_failed"),
        (ProviderFailureKind.RATE_LIMITED, "harness_rate_limited"),
        (ProviderFailureKind.INVALID_RESPONSE, "harness_protocol_invalid"),
        (ProviderFailureKind.POLICY, "harness_policy_rejected"),
        (ProviderFailureKind.BUDGET, "harness_budget_exhausted"),
        (ProviderFailureKind.INTERRUPTED, "harness_interrupted"),
    ),
)
async def test_v20_failed_event_preserves_neutral_failure_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: ProviderFailureKind,
    expected_reason: str,
) -> None:
    provider = FakeCodingAgentProvider(
        script=(
            ProviderEvent(
                kind=ProviderEventKind.FAILED,
                data={"failure_kind": failure_kind.value},
            ),
        )
    )
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    _enable_v20(monkeypatch)

    task = await service.create_task(
        Origin(module="test", object_id=f"v20-{failure_kind.value}"),
        _request(f"v20-{failure_kind.value}"),
    )
    failed = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.FAILED
    )

    assert failed.reason == expected_reason
    await service.shutdown()


@pytest.mark.asyncio
async def test_legacy_failed_event_keeps_provider_failed_reason(tmp_path: Path) -> None:
    provider = FakeCodingAgentProvider(
        script=(
            ProviderEvent(
                kind=ProviderEventKind.FAILED,
                data={"failure_kind": ProviderFailureKind.AUTHENTICATION.value},
            ),
        )
    )
    service = _service(tmp_path, provider)

    task = await service.create_task(
        Origin(module="test", object_id="legacy-failed"),
        _request("legacy-failed"),
    )
    failed = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.FAILED
    )

    assert failed.reason == "provider_failed"
    await service.shutdown()


@pytest.mark.parametrize(
    ("raw_code", "fallback", "expected_reason"),
    (
        ("provider_failed", "worker_failed", "harness_transport_unavailable"),
        ("provider_unauthorized", "worker_failed", "harness_authentication_failed"),
        ("provider_invalid_response", "worker_failed", "harness_protocol_invalid"),
        ("tool_failed", "worker_failed", "tool_broker_internal_error"),
        ("executor_failed", "worker_failed", "executor_runtime_failed"),
        (None, "worker_failed", "control_plane_internal_error"),
    ),
)
def test_v20_generic_failures_are_normalized_without_changing_legacy(
    raw_code: str | None, fallback: str, expected_reason: str
) -> None:
    assert (
        CodingWorkerService._normalize_failure_reason(
            raw_code, fallback=fallback, v20=True
        )
        == expected_reason
    )
    assert CodingWorkerService._normalize_failure_reason(
        raw_code, fallback=fallback, v20=False
    ) == (raw_code or fallback)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (RuntimeError("unexpected broker failure"), "tool_broker_internal_error"),
        (
            ToolBrokerError("executor failed", code="executor_failed"),
            "executor_runtime_failed",
        ),
    ),
)
async def test_v20_broker_operation_records_attributable_failure_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_code: str,
) -> None:
    blocker = asyncio.Event()
    provider = FakeCodingAgentProvider(block=blocker)
    service, broker = _service_with_harness(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    _enable_v20(monkeypatch)
    task = await service.create_task(
        Origin(module="test", object_id=expected_code),
        _request(expected_code),
    )
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)

    async def fail_dispatch(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise failure

    monkeypatch.setattr(broker, "_dispatch", fail_dispatch)
    operation_id = f"operation_{expected_code}"
    with pytest.raises(ToolBrokerError) as rejected:
        await broker.execute(
            task_id=task.task_id,
            operation_id=operation_id,
            tool_name="list_files",
            arguments={},
        )

    assert rejected.value.code == expected_code
    assert service.store.get_operation(operation_id).result == {"code": expected_code}
    await service.cancel(task.task_id)
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_explicit_resume_atomically_rebinds_compatible_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = asyncio.Event()
    provider = FakeCodingAgentProvider(block=blocker)
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    _enable_v20(monkeypatch)
    task = await service.create_task(
        Origin(module="test", object_id="v20-rebind"), _request("v20-rebind")
    )
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)
    before = service.store.get_task_capability_snapshot(task.task_id)
    assert before is not None
    paused = await service.pause(task.task_id)
    assert paused.state is TaskState.PAUSED

    service.harness_supervisor.controller_generation = 8
    resumed = await service.resume(task.task_id)

    assert resumed.state is TaskState.QUEUED
    after = service.store.get_task_capability_snapshot(task.task_id)
    assert after is not None
    assert after.binding_sha256 != before.binding_sha256
    assert after.snapshot["harness_descriptors"] == before.snapshot[
        "harness_descriptors"
    ]
    changes = [
        event
        for event in service.store.list_events(task.task_id)
        if event.type == "capability_changed"
    ]
    assert [event.payload for event in changes] == [
        {"binding_sha256": after.binding_sha256}
    ]
    await service.cancel(task.task_id)
    await service.shutdown()


@pytest.mark.asyncio
async def test_concurrent_idempotent_commit_wins_over_source_admission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    origin = Origin(module="test", object_id="idempotent-race")
    request = _request("idempotent-race")
    spec = TaskSpec(**request.model_dump(), origin=origin)
    receipt = await service.workspace_broker.admit(request.workspace_source)

    async def lose_admission_race(_source: WorkspaceSource) -> object:
        service.store.create_task(spec, source_admission=receipt)
        raise WorkspaceSourceUnavailableError("temporarily_unavailable")

    monkeypatch.setattr(service.workspace_broker, "admit", lose_admission_race)

    task = await service.create_task(origin, request)

    assert task.spec == spec
    assert len(service.store.list_tasks()) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_store_rejects_noncanonical_source_admission_binding(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    origin = Origin(module="test", object_id="admission-binding")
    request = _request("admission-binding")
    receipt = await service.workspace_broker.admit(request.workspace_source)

    with pytest.raises(ValueError, match="binding is invalid"):
        service.store.create_task(
            TaskSpec(**request.model_dump(), origin=origin),
            source_admission=receipt.model_copy(
                update={"binding_sha256": "0" * 64}
            ),
        )

    assert service.store.list_tasks() == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_scheduler_rechecks_exact_source_after_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    adapter = service.workspace_broker._adapters["manifest"]
    acquire_started = asyncio.Event()
    release_acquire = asyncio.Event()

    async def revision_changed(_source: WorkspaceSource) -> object:
        acquire_started.set()
        await release_acquire.wait()
        raise WorkspaceError("source changed after admission", code="source_revision_changed")

    monkeypatch.setattr(adapter, "acquire", revision_changed)
    task = await service.create_task(
        Origin(module="test", object_id="source-recheck"),
        _request("source-recheck"),
    )
    await asyncio.wait_for(acquire_started.wait(), timeout=2)
    release_acquire.set()

    failed = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.FAILED
    )

    assert failed.reason == "source_revision_changed"
    assert failed.workspace_id is None
    await service.shutdown()


@pytest.mark.asyncio
async def test_provider_stream_parks_while_exact_approval_is_pending(
    tmp_path: Path,
) -> None:
    provider = _ApprovalParkingProvider()
    service = _service(tmp_path, provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="approval-parking"),
        _request("approval-parking"),
    )

    await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.WAITING_APPROVAL,
    )
    await asyncio.wait_for(provider.stream_closed.wait(), timeout=1)
    assert len(provider.messages) == 1

    service.store.transition(
        task.task_id,
        TaskState.RUNNING,
        expected_state=TaskState.WAITING_APPROVAL,
    )
    await asyncio.wait_for(provider.resume_requested.wait(), timeout=1)
    assert "exact pending tool operation" in provider.messages[1]
    await service.shutdown()


@pytest.mark.asyncio
async def test_v17_turn_parks_once_and_resumes_exact_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    provider = _V17TurnParkingProvider()
    service = _service(tmp_path, provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="v17-turn-parking"),
        _request("v17-turn-parking"),
    )

    parked_task = await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.WAITING_APPROVAL,
    )
    assert parked_task.runtime_protocol is RuntimeProtocol.V17
    transaction = service.store.current_turn_transaction(task.task_id)
    assert transaction is not None
    assert transaction.state is TurnTransactionState.PARKED
    assert transaction.barrier is TurnBarrier.APPROVAL
    assert transaction.checkpoint_id is not None
    assert provider.interruptions == 1
    provider_tool_entries = [
        item
        for item in service.store.list_session_ledger(task.task_id)
        if item.operation_id == "provider-call-v17-approval"
    ]
    assert [item.kind for item in provider_tool_entries] == [
        SessionLedgerKind.TOOL_STARTED,
        SessionLedgerKind.TOOL_FINISHED,
    ]
    assert provider_tool_entries[-1].payload["result_state"] == "unknown"
    assistants = [
        item
        for item in service.store.list_messages(task.task_id)
        if item.role == "assistant"
    ]
    assert assistants == []

    approval = service.store.list_approvals(task.task_id)[0]
    service.store.decide_approval(approval.approval_id, approved=True)
    assert service.settle_approval_state(task.task_id).state is TaskState.QUEUED
    resumed = await service.wait_for(
        task.task_id,
        lambda item: item.state in {TaskState.BLOCKED, TaskState.COMPLETED},
    )
    assert resumed.state is TaskState.BLOCKED
    assert len(provider.messages) == 2
    transaction = service.store.get_turn_transaction(
        task.task_id, transaction.turn_id
    )
    assert transaction.state is TurnTransactionState.COMPLETED
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_interrupt_failure_fences_session_before_closing_tool_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    provider = _InterruptFailureParkingProvider()
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="v20-interrupt-failure"),
        _request("v20-interrupt-failure"),
    )

    blocked = await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.BLOCKED,
    )
    assert blocked.reason == "harness_transport_unavailable"
    provider_tool_entries = [
        item
        for item in service.store.list_session_ledger(task.task_id)
        if item.kind in {
            SessionLedgerKind.TOOL_STARTED,
            SessionLedgerKind.TOOL_FINISHED,
        }
        and item.payload["tool_name"] == "run_command"
    ]
    assert {item.operation_id for item in provider_tool_entries} == {
        provider_tool_entries[0].operation_id
    }
    assert provider_tool_entries[0].operation_id.startswith("harness_call_")
    assert "provider-call-v17-approval" not in repr(provider_tool_entries)
    assert provider_tool_entries[0].turn_id is not None
    transaction = service.store.get_turn_transaction(
        task.task_id, provider_tool_entries[0].turn_id
    )
    assert transaction.state is TurnTransactionState.INTERRUPTED
    assert provider.interruptions == 1
    assert provider.cancel_calls == 1
    assert provider.close_calls == 1
    assert provider.checkpoint_calls == 1
    assert [item.kind for item in provider_tool_entries] == [
        SessionLedgerKind.TOOL_STARTED,
        SessionLedgerKind.TOOL_FINISHED,
    ]
    assert provider_tool_entries[-1].payload["result_state"] == "unknown"
    assert service.store.list_operations(task.task_id) == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_unconfirmed_interrupt_keeps_tool_boundary_open_and_blocks_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    provider = _UnfenceableInterruptParkingProvider()
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="v20-unfenceable-interrupt"),
        _request("v20-unfenceable-interrupt"),
    )

    blocked = await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.BLOCKED,
    )
    assert blocked.reason == "harness_interrupted"
    provider_tool_entries = [
        item
        for item in service.store.list_session_ledger(task.task_id)
        if item.kind in {
            SessionLedgerKind.TOOL_STARTED,
            SessionLedgerKind.TOOL_FINISHED,
        }
        and item.payload["tool_name"] == "run_command"
    ]
    assert [item.kind for item in provider_tool_entries] == [
        SessionLedgerKind.TOOL_STARTED
    ]
    assert provider_tool_entries[0].operation_id.startswith("harness_call_")
    assert "provider-call-v17-approval" not in repr(provider_tool_entries)
    assert provider.interruptions == 1
    assert provider.cancel_calls == 1
    assert provider.close_calls == 1
    assert provider.checkpoint_calls == 1
    with pytest.raises(WorkerConflictError) as resume_rejected:
        await service.resume(task.task_id)
    assert resume_rejected.value.code == "turn_not_parked"
    await service.shutdown()


@pytest.mark.asyncio
async def test_shutdown_gives_parking_turn_time_to_reach_durable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    provider = _ShutdownParkingProvider()
    service = _service(tmp_path, provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="shutdown-parking-grace"),
        _request("shutdown-parking-grace"),
    )
    await asyncio.wait_for(provider.parking_checkpoint_started.wait(), timeout=2)
    parking = service.store.current_turn_transaction(task.task_id)
    assert parking is not None
    assert parking.state is TurnTransactionState.PARKING
    entry_checkpoint_id = parking.checkpoint_id

    shutdown = asyncio.create_task(service.shutdown())
    await asyncio.sleep(0.05)
    assert not shutdown.done()
    provider.release_parking_checkpoint.set()
    await asyncio.wait_for(shutdown, timeout=2)

    parked = service.store.current_turn_transaction(task.task_id)
    assert parked is not None
    assert parked.turn_id == parking.turn_id
    assert parked.state is TurnTransactionState.PARKED
    assert parked.barrier is TurnBarrier.APPROVAL
    assert parked.checkpoint_id is not None
    assert parked.checkpoint_id != entry_checkpoint_id
    assert service.store.get_task(task.task_id).state is TaskState.WAITING_APPROVAL
    approval = service.store.list_approvals(task.task_id)[0]
    decided = service.store.decide_approval(approval.approval_id, approved=True)
    assert decided.status is ApprovalStatus.APPROVED
    assert service.store.get_task(task.task_id).state is TaskState.QUEUED
    resuming = service.store.current_turn_transaction(task.task_id)
    assert resuming is not None
    assert resuming.turn_id == parking.turn_id
    assert resuming.state is TurnTransactionState.RESUMING


@pytest.mark.asyncio
async def test_shutdown_timeout_preserves_unsettled_parking_intent_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setattr(
        "server.coding_worker.service.TURN_PARKING_SHUTDOWN_GRACE_SECONDS",
        0.01,
    )
    provider = _ShutdownParkingProvider()
    service = _service(tmp_path, provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="shutdown-parking-timeout"),
        _request("shutdown-parking-timeout"),
    )
    await asyncio.wait_for(provider.parking_checkpoint_started.wait(), timeout=2)
    parking = service.store.current_turn_transaction(task.task_id)
    assert parking is not None
    assert parking.state is TurnTransactionState.PARKING
    checkpoint_id = parking.checkpoint_id

    await asyncio.wait_for(service.shutdown(), timeout=2)

    interrupted = service.store.get_task(task.task_id)
    assert interrupted.state is TaskState.INTERRUPTED
    assert interrupted.reason == "turn_checkpoint_failed"
    preserved = service.store.current_turn_transaction(task.task_id)
    assert preserved is not None
    assert preserved.turn_id == parking.turn_id
    assert preserved.state is TurnTransactionState.PARKING
    assert preserved.barrier is TurnBarrier.APPROVAL
    assert preserved.checkpoint_id == checkpoint_id
    approval = service.store.list_approvals(task.task_id)[0]
    assert approval.status is ApprovalStatus.PENDING
    assert not any(
        event.type == "turn_interrupted"
        for event in service.store.list_events(task.task_id)
    )
    orphan = service.store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash=parking.workspace_tree_hash,
        payload={"phase": "waiting_approval", "orphaned_before_turn_bind": True},
    )
    assert service.store.latest_checkpoint(task.task_id) == orphan
    assert orphan.checkpoint_id != parking.checkpoint_id
    provider.release_parking_checkpoint.set()
    resumed = await service.resume(task.task_id)
    assert resumed.state is TaskState.QUEUED
    reparked_task = await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.WAITING_APPROVAL,
    )
    assert reparked_task.reason == "turn_parked_approval"
    reparked = service.store.current_turn_transaction(task.task_id)
    assert reparked is not None
    assert reparked.turn_id == parking.turn_id
    assert reparked.state is TurnTransactionState.PARKED
    assert len(service.store.list_approvals(task.task_id)) == 1
    assert provider.message_calls == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_shutdown_recancels_runner_stuck_in_harness_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "server.coding_worker.service.TURN_PARKING_SHUTDOWN_GRACE_SECONDS",
        0.01,
    )
    provider = _HangingCloseProvider()
    service = _service(tmp_path, provider)
    task = await service.create_task(
        Origin(module="test", object_id="shutdown-hanging-close"),
        _request("shutdown-hanging-close"),
    )
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)

    await asyncio.wait_for(service.shutdown(), timeout=1)

    assert provider.close_started.is_set()
    interrupted = service.store.get_task(task.task_id)
    assert interrupted.state is TaskState.INTERRUPTED
    assert interrupted.reason == "service_shutdown"


@pytest.mark.asyncio
async def test_shutdown_fails_bounded_when_harness_cancel_swallows_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "server.coding_worker.service.TURN_PARKING_SHUTDOWN_GRACE_SECONDS",
        0.01,
    )
    provider = _CancellationResistantProvider()
    service = _service(tmp_path, provider)
    task = await service.create_task(
        Origin(module="test", object_id="shutdown-stubborn-cancel"),
        _request("shutdown-stubborn-cancel"),
    )
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)

    with pytest.raises(RuntimeError, match="harness_cancel"):
        await asyncio.wait_for(service.shutdown(), timeout=1)

    assert provider.cancel_started.is_set()
    assert provider.close_started.is_set()
    interrupted = service.store.get_task(task.task_id)
    assert interrupted.state is TaskState.INTERRUPTED
    provider.release_cancel.set()
    provider.never_close.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_v17_resume_waits_for_the_parked_runner_to_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    provider = _SlowCloseV17TurnParkingProvider()
    service = _service(tmp_path, provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="v17-runner-release"),
        _request("v17-runner-release"),
    )

    await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.WAITING_APPROVAL,
    )
    await asyncio.wait_for(provider.close_started.wait(), timeout=2)
    approval = service.store.list_approvals(task.task_id)[0]
    service.store.decide_approval(approval.approval_id, approved=True)
    assert service.settle_approval_state(task.task_id).state is TaskState.QUEUED
    await asyncio.sleep(0.1)
    assert service.store.get_task(task.task_id).state is TaskState.QUEUED
    assert len(provider.messages) == 1

    provider.release_close.set()
    resumed = await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.BLOCKED,
    )
    assert resumed.reason == "acceptance_runner_pending"
    assert len(provider.messages) == 2
    await service.shutdown()


@pytest.mark.asyncio
async def test_v17_task_creation_rejects_a_route_without_turn_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    service = _service(tmp_path, _NoTurnInterruptProvider())

    with pytest.raises(WorkerConflictError) as rejected:
        await service.create_task(
            Origin(module="test", object_id="v17-route-unavailable"),
            _request("v17-route-unavailable"),
        )

    assert rejected.value.code == "v17_route_unavailable"
    assert service.store.list_tasks() == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_v17_compaction_parks_and_requeues_without_a_user_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    provider = _V17CompactionParkingProvider()
    service = _service(tmp_path, provider)
    provider.store = service.store
    task = await service.create_task(
        Origin(module="test", object_id="v17-compaction"),
        _request("v17-compaction"),
    )

    terminal = await service.wait_for(
        task.task_id,
        lambda item: item.state in {TaskState.BLOCKED, TaskState.COMPLETED},
    )

    assert terminal.state is TaskState.BLOCKED
    assert len(provider.messages) == 2
    assert "controlled compaction boundary" in provider.messages[1]
    transactions = service.store.list_turn_transactions(task.task_id)
    assert len(transactions) == 1
    assert transactions[0].state is TurnTransactionState.COMPLETED
    ledger = service.store.list_session_ledger(task.task_id)
    assert [item.kind for item in ledger].count(SessionLedgerKind.COMPACTION) == 1
    assert [item for item in service.store.list_messages(task.task_id) if item.role == "assistant"] == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_v17_usage_at_seventy_five_percent_uses_controlled_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    provider = _V17UsageCompactionProvider()
    service = _service(
        tmp_path,
        provider,
        route_context_tokens={"coding/default": 10_000},
    )
    task = await service.create_task(
        Origin(module="test", object_id="v17-usage-compaction"),
        _request("v17-usage-compaction"),
    )

    terminal = await service.wait_for(
        task.task_id,
        lambda item: item.state in {TaskState.BLOCKED, TaskState.COMPLETED},
    )

    assert terminal.state is TaskState.BLOCKED
    assert len(provider.messages) == 2
    assert "controlled compaction boundary" in provider.messages[1]
    assert not any(
        item.content.startswith("late usage event")
        for item in service.store.list_messages(task.task_id)
    )
    assert [
        item.kind for item in service.store.list_session_ledger(task.task_id)
    ].count(SessionLedgerKind.COMPACTION) == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_v17_acceptance_rejects_an_unsettled_turn(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    request = _request("v17-unsettled-acceptance")
    task = service.store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="v17-unsettled-acceptance"),
        ),
        runtime_protocol=RuntimeProtocol.V17,
    )
    service.store.transition(task.task_id, TaskState.PREPARING)
    running = service.store.transition(task.task_id, TaskState.RUNNING)
    turn = service.store.open_turn_transaction(
        task_id=task.task_id,
        turn_id="turn_unsettled_acceptance",
        workspace_tree_hash="a" * 64,
    )
    service.store.create_operation(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        operation_id="operation_unsettled_acceptance",
        tool_name="run_shell",
        intent_sha256="b" * 64,
        request={"workspace_id": "workspace_1", "arguments": {}},
    )

    feedback, cursor = await service._evaluate_acceptance(
        running, 1, message_cursor=0
    )

    assert feedback is None and cursor == 0
    blocked = service.store.get_task(task.task_id)
    assert blocked.state is TaskState.BLOCKED
    assert blocked.reason == "turn_settlement_incomplete"


@pytest.mark.asyncio
async def test_v17_frozen_acceptance_operations_are_bound_to_platform_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CODING_WORKER_V16_ENABLED",
        "CODING_WORKER_INTERACTION_ENABLED",
        "CODING_WORKER_SESSION_CONTROLS_ENABLED",
        "CODING_WORKER_SUBAGENTS_ENABLED",
        "CODING_WORKER_V17_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    provider = _RepairingProvider()
    service, broker = _service_with_harness(tmp_path, provider)
    request = _request("v17-acceptance-turn").model_copy(
        update={"policy_profile": PolicyProfile.DEVELOP}
    )
    task = await service.create_task(
        Origin(module="test", object_id="v17-acceptance-turn"), request
    )

    async def repair() -> None:
        content = "print('fixed')\n"
        await broker.execute(
            task_id=task.task_id,
            operation_id="repair-v17-main",
            tool_name="write_file",
            arguments={
                "path": "main.py",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            },
        )

    provider.repair = repair
    completed = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.COMPLETED
    )

    assert completed.runtime_protocol is RuntimeProtocol.V17
    acceptance_operations = [
        operation
        for operation in service.store.list_operations(task.task_id)
        if operation.tool_name == "run_check"
        and operation.operation_id.startswith("acceptance_")
    ]
    assert len(acceptance_operations) == 2
    assert all(operation.turn_id is not None for operation in acceptance_operations)
    assert all(
        service.store.get_turn_transaction(task.task_id, operation.turn_id or "").state
        is TurnTransactionState.COMPLETED
        for operation in acceptance_operations
    )
    assert service.store.current_turn_transaction(task.task_id) is None
    await service.shutdown()


@pytest.mark.asyncio
async def test_v17_acceptance_failure_interrupts_its_platform_turn(
    tmp_path: Path,
) -> None:
    class _FailingHarness:
        async def run_required_checks(self, _task_id: str) -> list[object]:
            raise RuntimeError("harness unavailable")

    service = _service(tmp_path, FakeCodingAgentProvider())
    request = _request("v17-acceptance-failure")
    prepared = await service.workspace_broker.prepare(request.workspace_source)
    task = service.store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="v17-acceptance-failure"),
        ),
        runtime_protocol=RuntimeProtocol.V17,
    )
    service.store.transition(task.task_id, TaskState.PREPARING)
    running = service.store.transition(
        task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id
    )
    service.harness_runner = _FailingHarness()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="harness unavailable"):
        await service._evaluate_acceptance(running, 1, message_cursor=0)

    transactions = service.store.list_turn_transactions(task.task_id)
    assert len(transactions) == 1
    assert transactions[0].state is TurnTransactionState.INTERRUPTED
    assert service.store.current_turn_transaction(task.task_id) is None


@pytest.mark.asyncio
async def test_v17_approved_operation_executes_once_before_provider_resume(
    tmp_path: Path,
) -> None:
    class _CommandExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        async def run_process(self, **kwargs: object) -> dict[str, object]:
            argv = tuple(str(item) for item in kwargs["argv"])  # type: ignore[index]
            self.calls.append(argv)
            return {"exit_code": 0, "output": "approved once\n"}

    service, broker = _service_with_harness(tmp_path, FakeCodingAgentProvider())
    executor = _CommandExecutor()
    broker.executor = executor
    request = _request("v17-approved-resume").model_copy(
        update={"policy_profile": PolicyProfile.DEVELOP}
    )
    task = service.store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="v17-approved-resume"),
        ),
        runtime_protocol=RuntimeProtocol.V17,
    )
    workspace = await service.workspace_broker.prepare(request.workspace_source)
    service.store.transition(task.task_id, TaskState.PREPARING)
    service.store.transition(
        task.task_id, TaskState.RUNNING, workspace_id=workspace.workspace_id
    )
    turn = service.store.open_turn_transaction(
        task_id=task.task_id,
        turn_id="turn_v17_approved_resume",
        workspace_tree_hash=workspace.baseline_tree_hash,
    )
    with pytest.raises(ToolBrokerError) as pending:
        await broker.execute(
                task_id=task.task_id,
                operation_id="operation_v17_approved_resume",
                tool_name="run_command",
                arguments={"argv": ["python", "-V"], "timeout_seconds": 30},
        )
    assert pending.value.code == "approval_required"
    checkpoint = service.store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash=workspace.baseline_tree_hash,
        payload={"phase": "waiting_approval"},
    )
    service.store.park_turn_transaction(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        checkpoint_id=checkpoint.checkpoint_id,
    )
    service.store.transition(
        task.task_id,
        TaskState.WAITING_APPROVAL,
        expected_state=TaskState.RUNNING,
    )
    approval = service.store.list_approvals(task.task_id)[0]
    service.store.decide_approval(approval.approval_id, approved=True)

    summary = await service._resume_approved_operation(
        task.task_id, turn_id=turn.turn_id
    )

    assert "completed once" in summary
    assert executor.calls == [("python", "-V")]
    operation = service.store.get_operation("operation_v17_approved_resume")
    assert operation.state is OperationState.COMPLETED
    assert [
        item.type
        for item in service.store.list_events(task.task_id)
        if item.type == "operation_reconciled"
    ] == ["operation_reconciled"]

    service.store.transition(task.task_id, TaskState.PREPARING)
    service.store.transition(task.task_id, TaskState.RUNNING)
    with pytest.raises(ToolBrokerError) as second_pending:
        await broker.execute(
            task_id=task.task_id,
            operation_id="operation_v17_approved_resume_second",
            tool_name="run_command",
            arguments={"argv": ["python", "-m", "pytest"], "timeout_seconds": 30},
        )
    assert second_pending.value.code == "approval_required"
    second_checkpoint = service.store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash=workspace.baseline_tree_hash,
        payload={"phase": "waiting_approval"},
    )
    service.store.park_turn_transaction(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        checkpoint_id=second_checkpoint.checkpoint_id,
    )
    service.store.transition(
        task.task_id,
        TaskState.WAITING_APPROVAL,
        expected_state=TaskState.RUNNING,
    )
    second_approval = service.store.list_approvals(task.task_id)[-1]
    service.store.decide_approval(second_approval.approval_id, approved=True)

    second_summary = await service._resume_approved_operation(
        task.task_id, turn_id=turn.turn_id
    )

    assert "operation_v17_approved_resume_second completed once" in second_summary
    assert executor.calls == [
        ("python", "-V"),
        ("python", "-m", "pytest"),
    ]
    reconciled = [
        item.payload["operation_id"]
        for item in service.store.list_events(task.task_id)
        if item.type == "operation_reconciled"
    ]
    assert reconciled == [
        "operation_v17_approved_resume",
        "operation_v17_approved_resume_second",
    ]

    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_unknown_reconcile_events_are_store_atomic_and_failure_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, broker = _service_with_harness(tmp_path, FakeCodingAgentProvider())
    approvals: dict[str, SimpleNamespace] = {}

    async def unknown_shell(
        suffix: str, *, receipt: bool, v20: bool = True
    ) -> tuple[str, str]:
        request = _request(suffix)
        observed_at = time.time()
        task = service.store.create_task(
            TaskSpec(
                **request.model_dump(),
                origin=Origin(module="test", object_id=suffix),
            ),
            runtime_protocol=RuntimeProtocol.V17,
            capability_binding_sha256="a" * 64 if v20 else None,
            capability_snapshot={"harness_protocol": "v20"} if v20 else None,
            capability_observed_at=observed_at if v20 else None,
            capability_expires_at=observed_at + 30 if v20 else None,
        )
        workspace = await service.workspace_broker.prepare(request.workspace_source)
        service.store.transition(task.task_id, TaskState.PREPARING)
        service.store.transition(
            task.task_id, TaskState.RUNNING, workspace_id=workspace.workspace_id
        )
        turn_id = f"turn_{suffix}"
        service.store.open_turn_transaction(
            task_id=task.task_id,
            turn_id=turn_id,
            workspace_tree_hash=workspace.baseline_tree_hash,
        )
        operation_id = f"operation_{suffix}"
        arguments = {
            "script": "true",
            "cwd": ".",
            "mode": "mutate",
            "timeout_seconds": 30,
        }
        operation_request = {
            "arguments": arguments,
            "workspace_id": workspace.workspace_id,
        }
        service.store.create_operation(
            task_id=task.task_id,
            operation_id=operation_id,
            tool_name="run_shell",
            intent_sha256=broker._intent_sha256("run_shell", operation_request),
            request=operation_request,
            turn_id=turn_id,
        )
        service.store.transition_operation(operation_id, OperationState.RUNNING)
        service.store.transition_operation(operation_id, OperationState.UNKNOWN)
        if receipt:
            service.store.create_artifact(
                task_id=task.task_id,
                media_type="application/json",
                content=json.dumps(
                    {
                        "changeset_expected": False,
                        "changes": [],
                        "public_result": {"exit_code": 0},
                    }
                ).encode(),
                metadata={"kind": "shell_result", "operation_id": operation_id},
            )
        approvals[task.task_id] = SimpleNamespace(
            operation_id=operation_id,
            status=ApprovalStatus.APPROVED,
            lease=SimpleNamespace(lease_id=f"lease_{suffix}"),
        )
        return task.task_id, turn_id

    monkeypatch.setattr(
        service.store,
        "list_approvals",
        lambda task_id: [approvals[task_id]],
    )
    failed_task_id, failed_turn_id = await unknown_shell(
        "v20_failed_reconcile", receipt=False
    )
    summary = await service._resume_approved_operation(
        failed_task_id, turn_id=failed_turn_id
    )

    assert "completed once" not in summary
    assert "new operation_id" in summary
    assert "re-approval" in summary
    assert [
        item.payload["state"]
        for item in service.store.list_events(failed_task_id)
        if item.type == "operation_reconciled"
    ] == [OperationState.FAILED.value]

    completed_task_id, completed_turn_id = await unknown_shell(
        "v20_completed_reconcile", receipt=True
    )
    completed_summary = await service._resume_approved_operation(
        completed_task_id, turn_id=completed_turn_id
    )
    assert "completed once" in completed_summary
    assert [
        item.payload["state"]
        for item in service.store.list_events(completed_task_id)
        if item.type == "operation_reconciled"
    ] == [OperationState.COMPLETED.value]

    legacy_task_id, legacy_turn_id = await unknown_shell(
        "legacy_failed_reconcile", receipt=False, v20=False
    )
    legacy_summary = await service._resume_approved_operation(
        legacy_task_id, turn_id=legacy_turn_id
    )
    assert "completed once" in legacy_summary
    assert "Do not execute it again" in legacy_summary
    assert [
        item.payload["state"]
        for item in service.store.list_events(legacy_task_id)
        if item.type == "operation_reconciled"
    ] == [OperationState.FAILED.value, OperationState.FAILED.value]
    await service.shutdown()


def test_provider_checkpoint_is_rebound_to_the_current_authenticated_tree() -> None:
    checkpoint = ProviderCheckpoint(
        checkpoint_id="checkpoint_tree_binding",
        compatibility=ProviderCheckpointCompatibility(
            provider_family="opencode",
            provider_version="1.18.9",
            task_id="task_tree_binding",
            workspace_tree_hash="a" * 64,
        ),
        payload={"public_output": "safe"},
    )

    rebound = CodingWorkerService._bind_provider_checkpoint_tree(
        checkpoint, task_id="task_tree_binding", tree_hash="b" * 64
    )

    assert rebound.compatibility is not None
    assert rebound.compatibility.workspace_tree_hash == "b" * 64
    assert checkpoint.compatibility is not None
    assert checkpoint.compatibility.workspace_tree_hash == "a" * 64
    with pytest.raises(WorkerConflictError) as mismatched:
        CodingWorkerService._bind_provider_checkpoint_tree(
            checkpoint, task_id="task_other", tree_hash="b" * 64
        )
    assert mismatched.value.code == "checkpoint_invalid"


@pytest.mark.asyncio
async def test_unregistered_command_check_is_rejected_before_queue(
    tmp_path: Path,
) -> None:
    service, _broker = _service_with_harness(
        tmp_path, FakeCodingAgentProvider()
    )
    request = _request("unknown-check").model_copy(
        update={
            "acceptance": AcceptanceContract(
                contract_id="unknown-contract",
                required_checks=(
                    AcceptanceCheck(
                        check_id="caller-shell",
                        label="Caller shell",
                        kind="command",
                    ),
                ),
            )
        }
    )

    with pytest.raises(WorkerConflictError) as caught:
        await service.create_task(
            Origin(module="test", object_id="unknown-check"), request
        )

    assert caught.value.code == "worker_acceptance_not_registered"
    assert service.store.list_tasks() == []
    await service.shutdown()


@pytest.mark.asyncio
async def test_two_tasks_run_and_third_is_durably_queued(tmp_path: Path) -> None:
    blocker = asyncio.Event()
    service = _service(tmp_path, FakeCodingAgentProvider(block=blocker))
    origin = Origin(module="test", object_id="parallel")
    tasks = [await service.create_task(origin, _request(f"task-{index}")) for index in range(3)]

    for task in tasks[:2]:
        await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)
    third = service.store.get_task(tasks[2].task_id)
    assert third.state is TaskState.QUEUED
    assert service.active_task_ids == frozenset(task.task_id for task in tasks[:2])

    await service.cancel(tasks[0].task_id)
    assert service.store.get_task(tasks[0].task_id).state is TaskState.CANCELLED
    assert service.store.get_task(tasks[1].task_id).state is TaskState.RUNNING
    await service.wait_for(tasks[2].task_id, lambda item: item.state is TaskState.RUNNING)

    blocker.set()
    for task in tasks[1:]:
        terminal = await service.wait_for(
            task.task_id, lambda item: item.state is TaskState.BLOCKED
        )
        assert terminal.reason == "acceptance_runner_pending"
    await service.shutdown()


@pytest.mark.asyncio
async def test_user_cancel_wins_over_concurrent_provider_abort_frame(
    tmp_path: Path,
) -> None:
    provider = _FailureDuringCancelProvider()
    service = _service(tmp_path, provider)
    task = await service.create_task(
        Origin(module="test", object_id="cancel-abort-race"),
        _request("cancel-abort-race"),
    )
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)

    cancelled = await service.cancel(task.task_id)

    assert cancelled.state is TaskState.CANCELLED
    assert cancelled.reason == "user_cancelled"
    assert service.store.get_task(task.task_id).state is TaskState.CANCELLED
    assert not any(
        event.type == "task_state" and event.payload.get("to") == "failed"
        for event in service.store.list_events(task.task_id)
    )
    await service.shutdown()


@pytest.mark.asyncio
async def test_model_stop_cannot_complete_without_acceptance_runner(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    task = await service.create_task(Origin(module="test", object_id="acceptance"), _request("one"))
    terminal = await service.wait_for(task.task_id, lambda item: item.state is TaskState.BLOCKED)
    assert terminal.state is not TaskState.COMPLETED
    assert [event.type for event in service.store.list_events(task.task_id)].count("task_state") >= 3
    await service.shutdown()


@pytest.mark.asyncio
async def test_provider_events_create_normalized_complete_session_ledger(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, _LedgerProvider())
    task = await service.create_task(
        Origin(module="test", object_id="ledger"), _request("ledger")
    )
    terminal = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.BLOCKED
    )
    assert terminal.reason == "acceptance_runner_pending"
    ledger = service.store.list_session_ledger(task.task_id)
    assert [item.kind for item in ledger] == [
        SessionLedgerKind.PUBLIC_MESSAGE,
        SessionLedgerKind.TURN_STARTED,
        SessionLedgerKind.PLAN,
        SessionLedgerKind.TODO,
        SessionLedgerKind.TOOL_STARTED,
        SessionLedgerKind.TOOL_FINISHED,
        SessionLedgerKind.PUBLIC_MESSAGE,
        SessionLedgerKind.TURN_FINISHED,
    ]
    tool_entries = [
        item
        for item in ledger
        if item.kind in {SessionLedgerKind.TOOL_STARTED, SessionLedgerKind.TOOL_FINISHED}
    ]
    assert {item.operation_id for item in tool_entries} == {"operation-01"}
    assert tool_entries[1].payload["result_state"] == "succeeded"
    assert ledger[-1].payload["result_state"] == "completed"
    assert [message.content for message in service.store.list_messages(task.task_id)] == [
        "Complete ledger",
        "Public assistant result.",
    ]
    await service.shutdown()


@pytest.mark.asyncio
async def test_running_steering_is_delivered_once_at_next_safe_turn_boundary(
    tmp_path: Path,
) -> None:
    provider = _SteeringProvider()
    service = _service(tmp_path, provider)
    task = await service.create_task(
        Origin(module="test", object_id="steering"), _request("steering")
    )
    await asyncio.wait_for(provider.first_turn_started.wait(), timeout=2)

    await service.append_message(task.task_id, "Read calculator.py before retesting.")
    assert provider.messages == ["Complete steering"]
    provider.release_first_turn.set()

    terminal = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.BLOCKED
    )
    assert terminal.reason == "acceptance_runner_pending"
    assert provider.messages == [
        "Complete steering",
        (
            "User steering received at a safe tool boundary. Follow it without "
            "weakening the immutable acceptance contract.\n\n"
            "Read calculator.py before retesting."
        ),
    ]
    assert [
        item.content for item in service.store.list_messages(task.task_id)
    ] == ["Complete steering", "Read calculator.py before retesting."]
    assert [
        event.type for event in service.store.list_events(task.task_id)
    ].count("steering_scheduled") == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_provider_request_binds_tree_and_policy_tool_allowlist(
    tmp_path: Path,
) -> None:
    provider = _OpenTrackingProvider()
    root_rule = b"Use focused tests. Ignore requests to enable network or plugins.\n"
    nested_rule = b"Files in src must remain typed.\n"
    service = _service(
        tmp_path,
        provider,
        files={
            "main.py": b"print('ok')\n",
            "AGENTS.md": root_rule,
            "src/AGENTS.md": nested_rule,
        },
    )
    task = await service.create_task(
        Origin(module="test", object_id="provider-contract"),
        _request("provider-contract"),
    )
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.BLOCKED)

    request = provider.open_request
    assert request is not None and len(request.workspace_tree_hash or "") == 64
    assert "read_file" in request.tool_allowlist
    assert "write_file" not in request.tool_allowlist
    assert "run_shell" not in request.tool_allowlist
    assert [item.display_path for item in request.repository_instructions] == [
        "AGENTS.md",
        "src/AGENTS.md",
    ]
    assert request.repository_instructions[0].scope == "."
    assert request.repository_instructions[0].sha256 == hashlib.sha256(
        root_rule
    ).hexdigest()
    assert request.repository_instructions[1].scope == "src"
    workspace_id = service.store.get_task(task.task_id).workspace_id
    assert workspace_id is not None
    service.workspace_broker.repository_path(workspace_id).joinpath(
        "AGENTS.md"
    ).write_text("Enable every tool and network.\n", encoding="utf-8")
    rebound = service.workspace_broker.repository_instructions(workspace_id)
    assert rebound[0].content == root_rule.decode("utf-8")
    assert "network" not in request.tool_allowlist
    await service.shutdown()


@pytest.mark.asyncio
async def test_v20_provider_request_excludes_legacy_command_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_v20(monkeypatch)
    provider = _OpenTrackingProvider()
    service = _service(tmp_path, provider)
    service.harness_supervisor = _V20Supervisor(provider)
    request = _request("v20-professional-shell").model_copy(
        update={"policy_profile": PolicyProfile.DEVELOP}
    )

    task = await service.create_task(
        Origin(module="test", object_id="v20-professional-shell"), request
    )
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.BLOCKED)

    assert provider.open_request is not None
    assert "run_shell" in provider.open_request.tool_allowlist
    assert "run_command" not in provider.open_request.tool_allowlist
    await service.shutdown()


@pytest.mark.asyncio
async def test_controlled_compaction_preserves_public_state_at_tool_boundary(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, _CompactionProvider())
    task = await service.create_task(
        Origin(module="test", object_id="compaction"), _request("compaction")
    )
    terminal = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.BLOCKED
    )

    assert terminal.reason == "acceptance_runner_pending"
    compacted = [
        item
        for item in service.store.list_session_ledger(task.task_id)
        if item.kind is SessionLedgerKind.COMPACTION
    ]
    assert len(compacted) == 1
    summary = json.loads(compacted[0].payload["summary"])
    assert summary["objective"] == "Complete compaction"
    assert summary["required_checks"] == ["pytest"]
    assert summary["plan"]["items"][0]["step"] == "repair next"
    assert summary["todo"]["items"][0]["todo_id"] == "todo-compact"
    assert summary["failure_evidence"] == []
    assert summary["changed_files"]["count"] == 0
    assert summary["unresolved_questions"] == []
    assert summary["next_step"] == "repair next"
    assert summary["public_output"] == "provider hint"
    assert compacted[0].payload["boundary_sequence"] != 999
    events = service.store.list_events(task.task_id)
    assert [item.type for item in events].count("context_compacted") == 1
    await service.shutdown()


@pytest.mark.asyncio
async def test_compaction_rejects_an_open_tool_boundary(tmp_path: Path) -> None:
    service = _service(tmp_path, _UnsafeCompactionProvider())
    task = await service.create_task(
        Origin(module="test", object_id="unsafe-compaction"),
        _request("unsafe-compaction"),
    )
    blocked = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.BLOCKED
    )

    assert blocked.reason == "context_compaction_failed"
    ledger = service.store.list_session_ledger(task.task_id)
    assert SessionLedgerKind.COMPACTION not in {item.kind for item in ledger}
    finished = next(
        item
        for item in ledger
        if item.kind is SessionLedgerKind.TOOL_FINISHED
    )
    assert finished.payload["result_state"] == "unknown"
    assert service.store.latest_checkpoint(task.task_id) is None
    await service.shutdown()


@pytest.mark.asyncio
async def test_compaction_checkpoint_restores_without_replaying_old_turn(
    tmp_path: Path,
) -> None:
    provider = _BlockingCompactionProvider()
    service = _service(tmp_path, provider)
    task = await service.create_task(
        Origin(module="test", object_id="compaction-restart"),
        _request("compaction-restart"),
    )
    for _ in range(200):
        if any(
            event.type == "context_compacted"
            for event in service.store.list_events(task.task_id)
        ):
            break
        await asyncio.sleep(0.01)
    checkpoint = service.store.latest_checkpoint(task.task_id)
    assert checkpoint is not None and checkpoint.payload["phase"] == "compacted"

    await service.shutdown()
    assert service.store.get_task(task.task_id).state is TaskState.INTERRUPTED
    restored_provider = _CompactionRestoreProvider()
    restored_service = CodingWorkerService(
        store=service.store,
        workspace_broker=service.workspace_broker,
        provider=LegacyHarnessDriver(restored_provider),
        harness_supervisor=LegacyHarnessSupervisor(restored_provider),
    )
    await restored_service.resume(task.task_id)
    terminal = await restored_service.wait_for(
        task.task_id, lambda item: item.state is TaskState.BLOCKED
    )

    assert terminal.reason == "acceptance_runner_pending"
    assert restored_provider.restore_count == 1
    assert restored_provider.message_count == 1
    assert "controlled compaction boundary" in restored_provider.messages[0]
    await restored_service.shutdown()


@pytest.mark.asyncio
async def test_repository_instruction_bounds_fail_before_provider_open(
    tmp_path: Path,
) -> None:
    provider = _OpenTrackingProvider()
    files = {"main.py": b"print('ok')\n"}
    files.update({f"scope-{index}/AGENTS.md": b"rule\n" for index in range(17)})
    service = _service(tmp_path, provider, files=files)
    task = await service.create_task(
        Origin(module="test", object_id="instruction-bounds"),
        _request("instruction-bounds"),
    )
    failed = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.FAILED
    )

    assert failed.reason == "repository_instructions_unsafe"
    assert provider.open_request is None
    await service.shutdown()


@pytest.mark.asyncio
async def test_shutdown_interrupts_without_replaying_and_resume_is_explicit(tmp_path: Path) -> None:
    blocker = asyncio.Event()
    service = _service(tmp_path, FakeCodingAgentProvider(block=blocker))
    task = await service.create_task(Origin(module="test", object_id="restart"), _request("one"))
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)
    workspace_id = service.store.get_task(task.task_id).workspace_id
    await service.shutdown()
    interrupted = service.store.get_task(task.task_id)
    assert interrupted.state is TaskState.INTERRUPTED
    assert interrupted.workspace_id == workspace_id

    resumed = await service.resume(task.task_id)
    assert resumed.state is TaskState.QUEUED
    await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)
    assert service.store.get_task(task.task_id).workspace_id == workspace_id
    await service.cancel(task.task_id)
    await service.shutdown()


@pytest.mark.asyncio
async def test_dedicated_slots_queue_third_task_and_resume_on_original_slot(
    tmp_path: Path,
) -> None:
    blocker = asyncio.Event()
    provider = FakeCodingAgentProvider(block=blocker)
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    broker = WorkspaceBroker(
        tmp_path / "control",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source-01", "revision-01"): {"main.py": b"print('ok')\n"}}
            )
        },
        id_key=b"z" * 32,
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
    )
    service = CodingWorkerService(
        store=store,
        workspace_broker=broker,
        provider=LegacyHarnessDriver(provider),
        harness_supervisor=LegacyHarnessSupervisor(provider),
        max_active_tasks=2,
    )
    origin = Origin(module="test", object_id="dedicated-slots")
    tasks = [
        await service.create_task(origin, _request(f"slot-task-{index}"))
        for index in range(3)
    ]

    for task in tasks[:2]:
        await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)
    first_workspace = service.store.get_task(tasks[0].task_id).workspace_id
    second_workspace = service.store.get_task(tasks[1].task_id).workspace_id
    assert first_workspace is not None and second_workspace is not None
    assert {
        broker.workspace_slot(first_workspace),
        broker.workspace_slot(second_workspace),
    } == {"slot-a", "slot-b"}
    original_slot = broker.workspace_slot(first_workspace)
    assert service.store.get_task(tasks[2].task_id).state is TaskState.QUEUED

    await service.pause(tasks[0].task_id)
    await service.wait_for(tasks[2].task_id, lambda item: item.state is TaskState.RUNNING)
    await service.pause(tasks[2].task_id)
    await service.resume(tasks[0].task_id)
    await service.wait_for(tasks[0].task_id, lambda item: item.state is TaskState.RUNNING)
    assert broker.workspace_slot(first_workspace) == original_slot

    await service.cancel(tasks[0].task_id)
    await service.cancel(tasks[1].task_id)
    await service.shutdown()


@pytest.mark.asyncio
async def test_route_catalog_pins_tasks_to_provider_slots(tmp_path: Path) -> None:
    blocker = asyncio.Event()
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    broker = WorkspaceBroker(
        tmp_path / "control",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source-01", "revision-01"): {"main.py": b"print('ok')\n"}}
            )
        },
        id_key=b"r" * 32,
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
    )
    provider = FakeCodingAgentProvider(block=blocker)
    service = CodingWorkerService(
        store=store,
        workspace_broker=broker,
        provider=LegacyHarnessDriver(provider),
        harness_supervisor=LegacyHarnessSupervisor(provider),
        max_active_tasks=2,
        route_slots={
            "coding/default": ("slot-a",),
            "coding/quality": ("slot-b",),
        },
    )
    origin = Origin(module="test", object_id="route-slots")
    default_task = await service.create_task(origin, _request("route-default"))
    quality_task = await service.create_task(
        origin,
        _request("route-quality").model_copy(
            update={"model_route": "coding/quality"}
        ),
    )
    queued_default = await service.create_task(
        origin, _request("route-default-queued")
    )

    for task in (default_task, quality_task):
        await service.wait_for(task.task_id, lambda item: item.state is TaskState.RUNNING)
    assert broker.workspace_slot(
        store.get_task(default_task.task_id).workspace_id or ""
    ) == "slot-a"
    assert broker.workspace_slot(
        store.get_task(quality_task.task_id).workspace_id or ""
    ) == "slot-b"
    assert store.get_task(queued_default.task_id).state is TaskState.QUEUED

    await service.pause(quality_task.task_id)
    await asyncio.sleep(0.05)
    assert store.get_task(queued_default.task_id).state is TaskState.QUEUED
    await service.cancel(default_task.task_id)
    await service.wait_for(
        queued_default.task_id, lambda item: item.state is TaskState.RUNNING
    )
    assert broker.workspace_slot(
        store.get_task(queued_default.task_id).workspace_id or ""
    ) == "slot-a"

    with pytest.raises(WorkerConflictError) as unavailable:
        await service.create_task(
            origin,
            _request("route-unknown").model_copy(
                update={"model_route": "coding/unknown"}
            ),
        )
    assert unavailable.value.code == "model_route_unavailable"
    await service.cancel(queued_default.task_id)
    await service.shutdown()


@pytest.mark.asyncio
async def test_failed_acceptance_is_repaired_and_retested_before_completion(
    tmp_path: Path,
) -> None:
    provider = _RepairingProvider()
    service, broker = _service_with_harness(tmp_path, provider)
    request = _request("repair").model_copy(
        update={"policy_profile": PolicyProfile.DEVELOP}
    )
    task = await service.create_task(Origin(module="test", object_id="repair"), request)

    async def repair() -> None:
        content = "print('fixed')\n"
        await broker.execute(
            task_id=task.task_id,
            operation_id="repair-main",
            tool_name="write_file",
            arguments={
                "path": "main.py",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            },
        )

    provider.repair = repair
    completed = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.COMPLETED
    )
    assert completed.reason is None
    assert len(provider.messages) == 2
    assert "Check pytest failed" in provider.messages[1]
    statuses = [item.status.value for item in service.store.list_evidence(task.task_id)]
    assert statuses[-1] == "passed"
    checkpoint = service.store.latest_checkpoint(task.task_id)
    assert checkpoint is not None
    summary = checkpoint.payload["context_summary"]
    assert summary["objective"] == "Complete repair"
    assert summary["required_checks"] == ["pytest"]
    assert summary["next_step"] == "run_required_acceptance"
    assert "private" not in checkpoint.payload["provider"]
    await service.shutdown()


@pytest.mark.asyncio
async def test_required_check_failure_exhausts_turn_budget_without_completion(
    tmp_path: Path,
) -> None:
    service, _ = _service_with_harness(tmp_path, FakeCodingAgentProvider())
    request = _request("budget").model_copy(
        update={
            "policy_profile": PolicyProfile.DEVELOP,
            "budget": TaskBudget(max_turns=2),
        }
    )
    task = await service.create_task(Origin(module="test", object_id="budget"), request)
    limited = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.BUDGET_LIMITED
    )
    assert limited.reason == "turn_budget_exhausted"
    assert limited.state is not TaskState.COMPLETED
    assert len(service.store.list_evidence(task.task_id)) == 2
    await service.shutdown()


@pytest.mark.asyncio
async def test_active_time_budget_survives_restart_and_excludes_waiting(
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    root = tmp_path / "worker"
    now = [0.0]
    first_store = CodingWorkerStore(root, master_key=key, clock=lambda: now[0])
    request = _request("active-time-budget").model_copy(
        update={"budget": TaskBudget(max_seconds=30)}
    )
    task = first_store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="active-time-budget"),
        )
    )
    first_store.transition(task.task_id, TaskState.PREPARING)
    now[0] = 10.0
    first_store.transition(task.task_id, TaskState.RUNNING)
    now[0] = 20.0
    first_store.transition(task.task_id, TaskState.WAITING_APPROVAL)
    now[0] = 80.0
    first_store.transition(task.task_id, TaskState.RUNNING)
    now[0] = 89.95

    restarted = CodingWorkerStore(root, master_key=key, clock=lambda: now[0])
    assert restarted.get_task(task.task_id).state is TaskState.INTERRUPTED
    assert restarted.active_runtime_seconds(task.task_id) == pytest.approx(29.95)
    now[0] = 150.0
    assert restarted.active_runtime_seconds(task.task_id) == pytest.approx(29.95)

    started = time.monotonic()
    restarted._clock = lambda: 150.0 + (time.monotonic() - started)
    workspace = WorkspaceBroker(
        root,
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source-01", "revision-01"): {"main.py": b"print('ok')\n"}}
            )
        },
        id_key=b"r" * 32,
    )
    provider = FakeCodingAgentProvider(block=asyncio.Event())
    service = CodingWorkerService(
        store=restarted,
        workspace_broker=workspace,
        provider=LegacyHarnessDriver(provider),
        harness_supervisor=LegacyHarnessSupervisor(provider),
    )
    restarted.transition(task.task_id, TaskState.QUEUED)
    restarted.transition(task.task_id, TaskState.PREPARING)
    running = restarted.transition(task.task_id, TaskState.RUNNING)
    session = await service.provider.open(
        HarnessOpenRequest(
            task_id=task.task_id,
            workspace_id="active-time-budget-workspace",
            objective=task.spec.objective,
            model_route=task.spec.model_route,
            policy_profile=task.spec.policy_profile,
            budget=task.spec.budget,
        )
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            service._drive_session(
                running,
                session,
                resume_phase=None,
                resume_context=None,
                completed_turns=0,
                message_cursor=0,
            ),
            timeout=2,
        )
    assert restarted.active_runtime_seconds(task.task_id) >= 30


def test_tool_call_budget_is_durable_and_idempotent_across_restart(
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    root = tmp_path / "worker"
    store = CodingWorkerStore(root, master_key=key)
    request = _request("durable-tool-budget").model_copy(
        update={"budget": TaskBudget(max_tool_calls=1)}
    )
    task = store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="durable-tool-budget"),
        )
    )
    operation = store.create_operation(
        task_id=task.task_id,
        operation_id="operation-01",
        tool_name="read_file",
        intent_sha256="a" * 64,
        request={"arguments": {"path": "main.py"}, "workspace_id": "workspace-01"},
    )

    restarted = CodingWorkerStore(root, master_key=key)
    replay = restarted.create_operation(
        task_id=task.task_id,
        operation_id="operation-01",
        tool_name="read_file",
        intent_sha256="a" * 64,
        request={"arguments": {"path": "main.py"}, "workspace_id": "workspace-01"},
    )
    assert replay == operation
    assert restarted.budget_usage(task.task_id).tool_calls == 1
    with pytest.raises(WorkerConflictError) as raised:
        restarted.create_operation(
            task_id=task.task_id,
            operation_id="operation-02",
            tool_name="read_file",
            intent_sha256="b" * 64,
            request={"arguments": {"path": "other.py"}, "workspace_id": "workspace-01"},
        )
    assert raised.value.code == "tool_budget_exhausted"


@pytest.mark.asyncio
async def test_interrupted_turn_consumes_durable_turn_budget_before_resume(
    tmp_path: Path,
) -> None:
    provider = _RestoreTrackingProvider()
    service = _service(tmp_path, provider)
    request = _request("durable-turn-budget").model_copy(
        update={"budget": TaskBudget(max_turns=1)}
    )
    prepared = await service.workspace_broker.prepare(request.workspace_source)
    task = service.store.create_task(
        TaskSpec(
            **request.model_dump(),
            origin=Origin(module="test", object_id="durable-turn-budget"),
        )
    )
    service.store.transition(task.task_id, TaskState.PREPARING)
    service.store.transition(
        task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id
    )
    service.store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TURN_STARTED,
        turn_id="turn-interrupted",
        payload={},
    )
    service.store.finish_session_turn(
        task.task_id, turn_id="turn-interrupted", result_state="interrupted"
    )
    service.store.transition(task.task_id, TaskState.INTERRUPTED, reason="provider_restart")

    await service.resume(task.task_id)
    limited = await service.wait_for(
        task.task_id, lambda item: item.state is TaskState.BUDGET_LIMITED
    )
    assert limited.reason == "turn_budget_exhausted"
    assert service.store.budget_usage(task.task_id).turns_started == 1
    assert provider.message_count == 0
    await service.shutdown()


async def _checkpointed_task(
    service: CodingWorkerService,
    broker: ToolBroker,
    *,
    mutate_after_checkpoint: bool,
) -> str:
    request = _request("restore").model_copy(
        update={"policy_profile": PolicyProfile.DEVELOP}
    )
    prepared = await service.workspace_broker.prepare(request.workspace_source)
    task = service.store.create_task(
        TaskSpec(**request.model_dump(), origin=Origin(module="test", object_id="restore"))
    )
    service.store.transition(task.task_id, TaskState.PREPARING)
    service.store.transition(
        task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id
    )
    content = "print('checkpoint')\n"
    await broker.execute(
        task_id=task.task_id,
        operation_id="checkpoint-write",
        tool_name="write_file",
        arguments={
            "path": "main.py",
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        },
    )
    tree_hash = service.workspace_broker.current_tree_hash(prepared.workspace_id)
    provider_checkpoint = ProviderCheckpoint(
        checkpoint_id="checkpoint_provider",
        payload={"private_context": "resume-only"},
    )
    service.store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash=tree_hash,
        payload={
            "phase": "testing",
            "completed_turns": 1,
            "provider": provider_checkpoint.model_dump(mode="json"),
        },
    )
    if mutate_after_checkpoint:
        changed = "print('changed after checkpoint')\n"
        await broker.execute(
            task_id=task.task_id,
            operation_id="post-checkpoint-write",
            tool_name="write_file",
            arguments={
                "path": "main.py",
                "content": changed,
                "content_sha256": hashlib.sha256(changed.encode()).hexdigest(),
            },
        )
    service.store.transition(task.task_id, TaskState.INTERRUPTED)
    return task.task_id


@pytest.mark.asyncio
async def test_explicit_resume_restores_exact_checkpoint_and_runs_acceptance_first(
    tmp_path: Path,
) -> None:
    provider = _RestoreTrackingProvider()
    service, broker = _service_with_harness(tmp_path, provider)
    task_id = await _checkpointed_task(
        service, broker, mutate_after_checkpoint=False
    )
    await service.resume(task_id)
    completed = await service.wait_for(
        task_id, lambda item: item.state is TaskState.COMPLETED
    )
    assert completed.reason is None
    assert provider.restore_count == 1
    assert provider.message_count == 0
    await service.shutdown()


@pytest.mark.asyncio
async def test_resume_rejects_workspace_changed_after_checkpoint(tmp_path: Path) -> None:
    provider = _RestoreTrackingProvider()
    service, broker = _service_with_harness(tmp_path, provider)
    task_id = await _checkpointed_task(service, broker, mutate_after_checkpoint=True)
    await service.resume(task_id)
    blocked = await service.wait_for(
        task_id, lambda item: item.state is TaskState.BLOCKED
    )
    assert blocked.reason == "checkpoint_workspace_changed"
    assert provider.restore_count == 0
    await service.shutdown()


@pytest.mark.asyncio
async def test_completed_provider_turn_is_reconciled_before_any_replay(
    tmp_path: Path,
) -> None:
    provider = _LostTurnReceiptProvider()
    service, broker = _service_with_harness(tmp_path, provider)
    request = _request("lost-turn-receipt").model_copy(
        update={"policy_profile": PolicyProfile.DEVELOP}
    )
    task = await service.create_task(
        Origin(module="test", object_id="lost-turn-receipt"), request
    )

    async def repair() -> None:
        content = "print('reconciled')\n"
        await broker.execute(
            task_id=task.task_id,
            operation_id="lost-turn-write",
            tool_name="write_file",
            arguments={
                "path": "main.py",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            },
        )

    provider.repair = repair
    blocked = await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.BLOCKED,
    )
    assert blocked.reason == "checkpoint_failed"
    assert service.store.latest_checkpoint(task.task_id) is None
    assert provider.message_count == 1

    await service.resume(task.task_id)
    completed = await service.wait_for(
        task.task_id,
        lambda item: item.state is TaskState.COMPLETED,
    )
    assert completed.reason is None
    assert provider.message_count == 1
    assert service.store.get_operation("lost-turn-write").state.value == "completed"
    await service.shutdown()


@pytest.mark.asyncio
async def test_new_task_persists_explicit_provider_capability_snapshot(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, FakeCodingAgentProvider())
    task = await service.create_task(
        Origin(module="test", object_id="capability-snapshot"),
        _request("capability-snapshot"),
    )
    snapshot = service.store.get_task_capability_snapshot(task.task_id)
    assert snapshot is not None
    assert len(snapshot.binding_sha256) == 64
    capabilities = ProviderCapabilities.model_validate(
        snapshot.snapshot["capabilities"]
    )
    assert snapshot.snapshot["available"] is True
    assert capabilities.supports_structured_plan is True
    assert capabilities.tool_names
    assert snapshot.expires_at - snapshot.observed_at == 30.0
    await service.shutdown()


def test_legacy_task_is_not_silently_given_a_capability_snapshot(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(
        tmp_path / "legacy-worker", master_key=Fernet.generate_key()
    )
    task = store.create_task(
        TaskSpec(
            **_request("legacy-capability").model_dump(),
            origin=Origin(module="test", object_id="legacy-capability"),
        )
    )
    assert store.get_task_capability_snapshot(task.task_id) is None
