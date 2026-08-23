from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskCreateRequest,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.executor import ExecutorSidecarClientPool
from server.coding_worker.provider import FakeCodingAgentProvider
from server.coding_worker.provider_rpc import ProviderRPCServer
from server.coding_worker.runtime import (
    CodingWorkerRuntime,
    CodingWorkerRuntimeError,
    _route_slots_from_environment,
)
from server.coding_worker.tool_broker import FrozenCheck
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter


@pytest.mark.asyncio
async def test_runtime_connects_two_dedicated_provider_slots(tmp_path: Path) -> None:
    blockers = {slot: asyncio.Event() for slot in ("slot-a", "slot-b")}
    servers = {
        slot: ProviderRPCServer(
            FakeCodingAgentProvider(block=blockers[slot]), token=token
        )
        for slot, token in {"slot-a": "a" * 48, "slot-b": "b" * 48}.items()
    }
    endpoints = {
        slot: await server.start_tcp_for_tests() for slot, server in servers.items()
    }
    runtime = CodingWorkerRuntime(
        storage_root=tmp_path / "control",
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
        source_adapters={
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "h0"): {"main.py": b"print('ok')\n"}}
            )
        },
        frozen_checks={
            "syntax": FrozenCheck(
                check_id="syntax", argv=("python", "-m", "py_compile", "main.py")
            )
        },
        provider_endpoints=endpoints,
        provider_tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        broker_socket_path=None,
        sidecar_uid=-1,
        sidecar_gid=-1,
    )
    assert runtime.tool_broker.executor is runtime.execution_backend
    assert runtime.substrate.harness_supervisor is runtime.harness_supervisor
    assert runtime.substrate.harness_driver is runtime.harness_driver
    assert runtime.substrate.execution_backend is runtime.execution_backend
    assert runtime.harness_driver is not runtime.execution_backend
    assert runtime.harness_supervisor is not runtime.harness_driver
    await runtime.start()
    source = WorkspaceSource(kind="manifest", source_id="source", revision="h0")
    tasks = [
        await runtime.service.create_task(
            Origin(module="test", object_id=str(index)),
            TaskCreateRequest(
                client_task_id=f"runtime-{index}",
                objective="inspect",
                workspace_source=source,
                acceptance=AcceptanceContract(
                    contract_id="syntax",
                    required_checks=(
                        AcceptanceCheck(
                            check_id="syntax", label="syntax", kind="command"
                        ),
                    ),
                ),
                model_route="coding/default",
            ),
        )
        for index in range(2)
    ]
    for task in tasks:
        running = await runtime.service.wait_for(
            task.task_id, lambda item: item.state is TaskState.RUNNING
        )
        assert running.workspace_id is not None
    assert {
        runtime.workspace_broker.workspace_slot(
            runtime.store.get_task(task.task_id).workspace_id or ""
        )
        for task in tasks
    } == {"slot-a", "slot-b"}
    for task in tasks:
        await runtime.service.cancel(task.task_id)
    await runtime.close()
    for server in servers.values():
        await server.close()


def test_runtime_routes_v15_tools_to_dedicated_executor_pool(
    tmp_path: Path,
) -> None:
    runtime = CodingWorkerRuntime(
        storage_root=tmp_path / "control",
        slot_roots={"slot-a": tmp_path / "slot-a"},
        source_adapters={},
        frozen_checks={},
        provider_endpoints={"slot-a": "tcp:127.0.0.1:18001"},
        provider_tokens={"slot-a": "p" * 48},
        executor_endpoints={"slot-a": "tcp:127.0.0.1:18002"},
        executor_tokens={"slot-a": "x" * 48},
        broker_socket_path=None,
        sidecar_uid=-1,
        sidecar_gid=-1,
    )

    assert isinstance(runtime.executor_pool, ExecutorSidecarClientPool)
    assert runtime.tool_broker.executor is runtime.execution_backend


def test_route_slot_catalog_is_strict_and_provider_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODING_WORKER_ROUTE_SLOTS_JSON",
        '{"coding/default":["slot-a"],"coding/quality":["slot-b"]}',
    )
    assert _route_slots_from_environment(("slot-a", "slot-b")) == {
        "coding/default": ("slot-a",),
        "coding/quality": ("slot-b",),
    }

    monkeypatch.setenv(
        "CODING_WORKER_ROUTE_SLOTS_JSON",
        '{"coding/quality":["missing-slot"]}',
    )
    with pytest.raises(CodingWorkerRuntimeError) as caught:
        _route_slots_from_environment(("slot-a", "slot-b"))
    assert caught.value.code == "coding_worker_config_invalid"
