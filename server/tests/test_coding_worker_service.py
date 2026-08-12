from __future__ import annotations

import asyncio
import hashlib
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    SessionLedgerKind,
    TaskBudget,
    TaskCreateRequest,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.evidence import HarnessRunner
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderEvent,
    ProviderEventKind,
    ProviderCheckpoint,
    ProviderOpenRequest,
    ProviderSession,
)
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError
from server.coding_worker.tool_broker import FrozenCheck, ToolBroker
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


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


def _service(tmp_path: Path, provider: FakeCodingAgentProvider) -> CodingWorkerService:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    adapter = InMemoryWorkspaceSourceAdapter(
        {("source-01", "revision-01"): {"main.py": b"print('ok')\n"}}
    )
    broker = WorkspaceBroker(
        tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
    )
    return CodingWorkerService(store=store, workspace_broker=broker, provider=provider)


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
            provider=provider,
            harness_runner=harness,
            tool_broker=broker,
        ),
        broker,
    )


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
    service = _service(tmp_path, provider)
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
        provider=provider,
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
    service = CodingWorkerService(
        store=store,
        workspace_broker=broker,
        provider=FakeCodingAgentProvider(block=blocker),
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
        provider=provider,
    )
    restarted.transition(task.task_id, TaskState.QUEUED)
    restarted.transition(task.task_id, TaskState.PREPARING)
    running = restarted.transition(task.task_id, TaskState.RUNNING)
    session = ProviderSession(
        session_id="active-time-budget-session",
        task_id=task.task_id,
        provider_capabilities=await provider.capabilities(),
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
