from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskCreateRequest,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.provider import FakeCodingAgentProvider
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore
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
