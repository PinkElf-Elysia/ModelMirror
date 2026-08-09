from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskSpec,
    WorkspaceSource,
)
from server.coding_worker.process_manager import (
    BackgroundProcessManager,
    ProcessManagerError,
)
from server.coding_worker.store import CodingWorkerStore, WorkerNotFoundError
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


async def _manager(
    tmp_path: Path, *, max_output_bytes: int = 4096
) -> tuple[BackgroundProcessManager, CodingWorkerStore, str, str]:
    source = WorkspaceSource(kind="manifest", source_id="process", revision="h0")
    workspace = WorkspaceBroker(
        tmp_path / "workspace",
        {"manifest": InMemoryWorkspaceSourceAdapter({("process", "h0"): {"app.py": b""}})},
        id_key=b"p" * 32,
    )
    prepared = await workspace.prepare(source)
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    task = store.create_task(
        TaskSpec(
            client_task_id="process-task",
            origin=Origin(module="tests", object_id="process"),
            objective="run service",
            workspace_source=source,
            acceptance=AcceptanceContract(
                contract_id="contract",
                required_checks=(
                    AcceptanceCheck(check_id="check", label="check", kind="command"),
                ),
            ),
            model_route="coding/default",
        )
    )

    def environment(_workspace_id: str) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "PYTHONIOENCODING": "utf-8",
        }

    return (
        BackgroundProcessManager(
            store=store,
            workspace_broker=workspace,
            environment_factory=environment,
            max_output_bytes=max_output_bytes,
        ),
        store,
        task.task_id,
        prepared.workspace_id,
    )


@pytest.mark.asyncio
async def test_service_output_is_archived_without_exposing_pid(tmp_path: Path) -> None:
    manager, store, task_id, workspace_id = await _manager(tmp_path)
    started = await manager.start(
        task_id=task_id,
        workspace_id=workspace_id,
        argv=(sys.executable, "-c", "print('ready')"),
        ttl_seconds=30,
    )
    assert started.state == "running" and "pid" not in started.model_dump()
    while manager.status(task_id=task_id, service_id=started.service_id).state == "running":
        await asyncio.sleep(0.01)
    completed = manager.status(task_id=task_id, service_id=started.service_id)
    assert completed.state == "completed" and completed.output_artifact_id
    assert store.read_artifact(
        completed.output_artifact_id, task_id=task_id
    ).splitlines() == [b"ready"]


@pytest.mark.asyncio
async def test_interrupt_and_lookup_are_task_bound(tmp_path: Path) -> None:
    manager, store, task_id, workspace_id = await _manager(tmp_path)
    other = store.create_task(
        store.get_task(task_id).spec.model_copy(
            update={"client_task_id": "other", "origin": Origin(module="tests", object_id="other")}
        )
    )
    started = await manager.start(
        task_id=task_id,
        workspace_id=workspace_id,
        argv=(sys.executable, "-c", "import time;print('up', flush=True);time.sleep(30)"),
        ttl_seconds=60,
    )
    with pytest.raises(WorkerNotFoundError):
        manager.status(task_id=other.task_id, service_id=started.service_id)
    stopped = await manager.interrupt(task_id=task_id, service_id=started.service_id)
    assert stopped.state == "stopped" and stopped.reason == "user_interrupted"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_output_limit_stops_background_service_and_records_reason(tmp_path: Path) -> None:
    manager, _, task_id, workspace_id = await _manager(tmp_path, max_output_bytes=1024)
    started = await manager.start(
        task_id=task_id,
        workspace_id=workspace_id,
        argv=(sys.executable, "-c", "print('x' * 5000)"),
        ttl_seconds=30,
    )
    while manager.status(task_id=task_id, service_id=started.service_id).state == "running":
        await asyncio.sleep(0.01)
    stopped = manager.status(task_id=task_id, service_id=started.service_id)
    assert stopped.state == "stopped" and stopped.reason == "service_output_limit"


@pytest.mark.asyncio
async def test_ttl_and_capacity_are_bounded(tmp_path: Path) -> None:
    manager, _, task_id, workspace_id = await _manager(tmp_path)
    manager.max_processes_per_task = 1
    started = await manager.start(
        task_id=task_id,
        workspace_id=workspace_id,
        argv=(sys.executable, "-c", "import time;time.sleep(30)"),
        ttl_seconds=1,
    )
    with pytest.raises(ProcessManagerError) as capacity:
        await manager.start(
            task_id=task_id,
            workspace_id=workspace_id,
            argv=(sys.executable, "-c", "print('no')"),
            ttl_seconds=30,
        )
    assert capacity.value.code == "service_capacity_exhausted"
    while manager.status(task_id=task_id, service_id=started.service_id).state == "running":
        await asyncio.sleep(0.02)
    expired = manager.status(task_id=task_id, service_id=started.service_id)
    assert expired.reason == "service_ttl_expired"
