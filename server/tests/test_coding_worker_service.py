from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    TaskBudget,
    TaskCreateRequest,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.evidence import HarnessRunner
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderEvent,
    ProviderSession,
)
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore
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
        ),
        broker,
    )


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
