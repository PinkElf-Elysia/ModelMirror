from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.broker_rpc import BrokerRPCServer
from server.coding_worker.contracts import PolicyProfile, TaskBudget
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderCapabilities,
    ProviderEvent,
    ProviderEventKind,
    ProviderOpenRequest,
)
from server.coding_worker.provider_rpc import (
    ProviderRPCError,
    ProviderRPCRequest,
    ProviderRPCServer,
    ProviderSidecarClientPool,
)
from server.coding_worker.harness_v3 import (
    PROVIDER_HARNESS_CODE_FILES,
    harness_code_bundle_sha256,
)
from server.coding_worker.executor import (
    ExecutorRPCError,
    ExecutorRPCServer,
    ExecutorSidecarClientPool,
    SidecarExecutor,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.tool_broker import ToolBroker
from server.coding_worker.workspace import WorkspaceBroker


def _request(task_id: str, workspace_id: str) -> ProviderOpenRequest:
    return ProviderOpenRequest(
        task_id=task_id,
        workspace_id=workspace_id,
        objective="Fix and test the project",
        model_route="coding/default",
        policy_profile=PolicyProfile.DEVELOP,
        budget=TaskBudget(),
    )


class _AbortTrackingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.cancel_count = 0

    async def message(self, session, text):
        yield ProviderEvent(
            kind=ProviderEventKind.MESSAGE,
            data={"text": "provider turn started"},
        )
        await self.release.wait()
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)

    async def cancel(self, session):
        self.cancel_count += 1
        self.release.set()
        return True


class _NoShellProvider(FakeCodingAgentProvider):
    async def capabilities(self) -> ProviderCapabilities:
        capabilities = await super().capabilities()
        return capabilities.model_copy(
            update={
                "tool_names": tuple(
                    item for item in capabilities.tool_names if item != "run_shell"
                )
            }
        )


@pytest.mark.asyncio
async def test_provider_attestation_uses_sidecar_environment_and_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ProviderRPCServer(FakeCodingAgentProvider(), token="a" * 48)
    request = ProviderRPCRequest(
        token="a" * 48,
        action="harness_attestation",
    )
    with pytest.raises(ProviderRPCError) as disabled:
        await server._dispatch(request)
    assert disabled.value.code == "harness_attestation_disabled"

    monkeypatch.setenv("CODING_WORKER_HARNESS_V3_ENABLED", "true")
    with pytest.raises(ProviderRPCError) as unavailable:
        await server._dispatch(request)
    assert unavailable.value.code == "harness_attestation_unavailable"

    server = ProviderRPCServer(
        FakeCodingAgentProvider(),
        token="a" * 48,
        harness_identity=(
            "coding/default",
            "openrouter/example-model",
            "opencode-1.18.9",
        ),
    )
    result = await server._dispatch(request)
    assert result == {
        "route_id": "coding/default",
        "model_identity_sha256": hashlib.sha256(
            b"openrouter/example-model"
        ).hexdigest(),
        "engine": "opencode-1.18.9",
        "sidecar_generation": result["sidecar_generation"],
        "code_bundle_sha256": harness_code_bundle_sha256(
            Path(__file__).parents[1] / "coding_worker",
            PROVIDER_HARNESS_CODE_FILES,
        ),
    }
    assert len(result["sidecar_generation"]) == 32


@pytest.mark.asyncio
async def test_provider_pool_observes_slots_independently_and_fails_closed(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(
        tmp_path / "control", master_key=Fernet.generate_key()
    )
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"c" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    first = ProviderRPCServer(FakeCodingAgentProvider(), token="a" * 48)
    second = ProviderRPCServer(_NoShellProvider(), token="b" * 48)
    first_endpoint = await first.start_tcp_for_tests()
    second_endpoint = await second.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": first_endpoint, "slot-b": second_endpoint},
        tokens={"slot-a": "a" * 48, "slot-b": "b" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )

    observations = await pool.slot_capabilities()
    first_capabilities = observations["slot-a"]
    second_capabilities = observations["slot-b"]
    assert first_capabilities is not None
    assert second_capabilities is not None
    assert "run_shell" in first_capabilities.tool_names
    assert "run_shell" not in second_capabilities.tool_names
    with pytest.raises(ProviderRPCError) as mismatch:
        await pool.capabilities()
    assert mismatch.value.code == "provider_capability_mismatch"

    await first.close()
    await second.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_provider_sidecar_pool_streams_neutral_events_and_revokes_broker_token(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"w" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    server = ProviderRPCServer(FakeCodingAgentProvider(), token="s" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "s" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    # Broker tokens bind to persisted tasks, just as the production service does.
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_id = store.create_task(
        TaskSpec(**task_request("rpc-task").model_dump(), origin=Origin(module="test", object_id="rpc"))
    ).task_id
    session = await pool.open(_request(task_id, "workspace_fake"))
    events = [event async for event in pool.message(session, "continue")]
    assert [event.kind for event in events] == [
        ProviderEventKind.PLAN,
        ProviderEventKind.TURN_COMPLETED,
    ]
    checkpoint = await pool.checkpoint(session)
    assert checkpoint.payload["fake_session"] == session.session_id
    assert task_id in broker_rpc._tokens
    await pool.close(session)
    assert task_id not in broker_rpc._tokens
    await server.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_closing_provider_stream_aborts_the_unfinished_sidecar_turn(
    tmp_path: Path,
) -> None:
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, object]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"z" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider = _AbortTrackingProvider()
    server = ProviderRPCServer(provider, token="z" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "z" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_id = store.create_task(
        TaskSpec(
            **task_request("rpc-abort").model_dump(),
            origin=Origin(module="test", object_id="rpc-abort"),
        )
    ).task_id
    session = await pool.open(_request(task_id, "workspace_abort"))
    stream = pool.message(session, "continue")
    first = await anext(stream)
    assert first.kind is ProviderEventKind.MESSAGE

    await stream.aclose()
    for _ in range(100):
        if provider.cancel_count == 1:
            break
        await asyncio.sleep(0.01)
    assert provider.cancel_count == 1

    await pool.close(session)
    await server.close()
    await broker_rpc.close()
    loop.set_exception_handler(previous_handler)
    assert unhandled == []


@pytest.mark.asyncio
async def test_provider_sidecar_rejects_wrong_token_and_second_active_task(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"x" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    server = ProviderRPCServer(FakeCodingAgentProvider(), token="a" * 48)
    endpoint = await server.start_tcp_for_tests()
    bad = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "b" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    with pytest.raises(ProviderRPCError) as unauthorized:
        await bad.capabilities()
    assert unauthorized.value.code == "provider_unauthorized"

    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_ids = [
        store.create_task(
            TaskSpec(
                **task_request(f"rpc-busy-{index}").model_dump(),
                origin=Origin(module="test", object_id=f"rpc-{index}"),
            )
        ).task_id
        for index in range(2)
    ]
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "a" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
    )
    first = await pool.open(_request(task_ids[0], "workspace_one"))
    with pytest.raises(ProviderRPCError) as busy:
        await pool.open(_request(task_ids[1], "workspace_two"))
    assert busy.value.code == "provider_slot_busy"
    await pool.close(first)
    await server.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_new_server_controller_reclaims_stale_provider_and_executor_bindings(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"r" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider = FakeCodingAgentProvider()
    provider_server = ProviderRPCServer(provider, token="p" * 48)
    provider_endpoint = await provider_server.start_tcp_for_tests()
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_ids = [
        store.create_task(
            TaskSpec(
                **task_request(f"restart-{index}").model_dump(),
                origin=Origin(module="test", object_id=f"restart-{index}"),
            )
        ).task_id
        for index in range(4)
    ]
    old_generation = store.allocate_controller_generation()
    new_generation = store.allocate_controller_generation()
    assert new_generation == old_generation + 1
    old_provider_pool = ProviderSidecarClientPool(
        endpoints={"slot-a": provider_endpoint},
        tokens={"slot-a": "p" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
        controller_id="controller_old",
        controller_generation=old_generation,
    )
    new_provider_pool = ProviderSidecarClientPool(
        endpoints={"slot-a": provider_endpoint},
        tokens={"slot-a": "p" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
        controller_id="controller_new",
        controller_generation=new_generation,
    )
    old_session = await old_provider_pool.open(
        _request(task_ids[0], "workspace_one")
    )
    new_session = await new_provider_pool.open(
        _request(task_ids[1], "workspace_two")
    )
    assert old_session.session_id in provider._closed
    with pytest.raises(ProviderRPCError) as stale_provider:
        await old_provider_pool.checkpoint(old_session)
    assert stale_provider.value.code == "session_not_found"
    assert (await new_provider_pool.checkpoint(new_session)).payload
    with pytest.raises(ProviderRPCError) as stale_provider_reopen:
        await old_provider_pool.open(_request(task_ids[2], "workspace_three"))
    assert stale_provider_reopen.value.code == "provider_controller_stale"
    assert (await new_provider_pool.checkpoint(new_session)).payload

    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "main.py").write_text("print('rebound')\n", encoding="utf-8")
    executor_server = ExecutorRPCServer(
        SidecarExecutor(lambda _workspace_id: repository, runtime_root=tmp_path / "run"),
        token="e" * 48,
    )
    executor_endpoint = await executor_server.start_tcp_for_tests()
    old_executor_pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": executor_endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        controller_id="controller_old",
        controller_generation=old_generation,
        auto_rebind=True,
    )
    new_executor_pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": executor_endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        controller_id="controller_new",
        controller_generation=new_generation,
    )
    await old_executor_pool.bind_task("task_one", "workspace_one")
    running = asyncio.create_task(
        old_executor_pool.run_process(
            task_id="task_one",
            workspace_id="workspace_one",
            argv=("python", "-c", "import time; time.sleep(30)"),
            timeout_seconds=60,
            isolated=True,
        )
    )
    for _ in range(200):
        if executor_server.executor._processes.get("task_one"):
            break
        await asyncio.sleep(0.01)
    assert executor_server.executor._processes.get("task_one")
    await new_executor_pool.bind_task("task_two", "workspace_two")
    with pytest.raises(ExecutorRPCError) as stopped:
        await asyncio.wait_for(running, timeout=5)
    assert stopped.value.code == "executor_controller_stale"
    with pytest.raises(ExecutorRPCError) as stale_executor:
        await old_executor_pool.run_process(
            task_id="task_one",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert stale_executor.value.code == "executor_controller_stale"
    result = await new_executor_pool.run_process(
        task_id="task_two",
        workspace_id="workspace_two",
        argv=("python", "main.py"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0

    await new_provider_pool.close(new_session)
    await new_executor_pool.close_task("task_two", "workspace_two")
    with pytest.raises(ProviderRPCError) as stale_provider_after_close:
        await old_provider_pool.open(_request(task_ids[3], "workspace_four"))
    assert stale_provider_after_close.value.code == "provider_controller_stale"
    with pytest.raises(ExecutorRPCError) as stale_executor_after_close:
        await old_executor_pool.bind_task("task_three", "workspace_three")
    assert stale_executor_after_close.value.code == "executor_controller_stale"
    await provider_server.close()
    await executor_server.close()
    await broker_rpc.close()


@pytest.mark.asyncio
async def test_credential_free_executor_requires_exact_task_workspace_binding(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "slots" / "workspaces" / "workspace_one" / "repo"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("print('isolated')\n")
    executor = SidecarExecutor(
        lambda workspace_id: repository if workspace_id == "workspace_one" else tmp_path / "missing",
        runtime_root=tmp_path / "runtime",
    )
    server = ExecutorRPCServer(executor, token="e" * 48)
    endpoint = await server.start_tcp_for_tests()
    pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
    )
    with pytest.raises(ExecutorRPCError) as unbound:
        await pool.run_process(
            task_id="task_one",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert unbound.value.code == "executor_binding_invalid"
    await pool.bind_task("task_one", "workspace_one")
    result = await pool.run_process(
        task_id="task_one",
        workspace_id="workspace_one",
        argv=("python", "main.py"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0 and str(result["output"]).splitlines() == [
        "isolated"
    ]
    with pytest.raises(ExecutorRPCError) as foreign:
        await pool.run_process(
            task_id="task_two",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert foreign.value.code == "executor_binding_invalid"
    await pool.close_task("task_one", "workspace_one")
    await server.close()


@pytest.mark.asyncio
async def test_runtime_executor_pool_rebinds_exact_task_after_executor_restart(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "slots" / "workspaces" / "workspace_one" / "repo"
    repository.mkdir(parents=True)
    (repository / "main.py").write_text("print('isolated')\n")
    server = ExecutorRPCServer(
        SidecarExecutor(
            lambda workspace_id: (
                repository if workspace_id == "workspace_one" else tmp_path / "missing"
            ),
            runtime_root=tmp_path / "runtime",
        ),
        token="e" * 48,
    )
    endpoint = await server.start_tcp_for_tests()
    pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": endpoint},
        tokens={"slot-a": "e" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        auto_rebind=True,
    )

    result = await pool.run_process(
        task_id="task_one",
        workspace_id="workspace_one",
        argv=("python", "main.py"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0
    assert str(result["output"]).splitlines() == ["isolated"]

    with pytest.raises(ExecutorRPCError) as foreign:
        await pool.run_process(
            task_id="task_two",
            workspace_id="workspace_one",
            argv=("python", "main.py"),
            timeout_seconds=10,
            isolated=False,
        )
    assert foreign.value.code == "executor_slot_busy"
    await pool.close_task("task_one", "workspace_one")
    await server.close()


@pytest.mark.asyncio
async def test_provider_pool_binds_and_closes_separate_executor(tmp_path: Path) -> None:
    store = CodingWorkerStore(tmp_path / "control", master_key=Fernet.generate_key())
    workspace = WorkspaceBroker(tmp_path / "workspace", {}, id_key=b"z" * 32)
    broker_rpc = BrokerRPCServer(ToolBroker(store=store, workspace_broker=workspace))
    await broker_rpc.start_tcp_for_tests()
    provider_server = ProviderRPCServer(FakeCodingAgentProvider(), token="p" * 48)
    provider_endpoint = await provider_server.start_tcp_for_tests()
    repository = tmp_path / "repo"
    repository.mkdir()
    executor_server = ExecutorRPCServer(
        SidecarExecutor(lambda _workspace_id: repository, runtime_root=tmp_path / "run"),
        token="x" * 48,
    )
    executor_endpoint = await executor_server.start_tcp_for_tests()
    executor_pool = ExecutorSidecarClientPool(
        endpoints={"slot-a": executor_endpoint},
        tokens={"slot-a": "x" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
    )
    pool = ProviderSidecarClientPool(
        endpoints={"slot-a": provider_endpoint},
        tokens={"slot-a": "p" * 48},
        workspace_slot_resolver=lambda _workspace_id: "slot-a",
        broker_rpc=broker_rpc,
        executor_pool=executor_pool,
    )
    from server.tests.test_coding_worker_service import _request as task_request
    from server.coding_worker.contracts import Origin, TaskSpec

    task_id = store.create_task(
        TaskSpec(**task_request("split-slot").model_dump(), origin=Origin(module="test", object_id="split"))
    ).task_id
    session = await pool.open(_request(task_id, "workspace_split"))
    result = await pool.run_process(
        task_id=task_id,
        workspace_id="workspace_split",
        argv=("python", "-c", "print('executor-only')"),
        timeout_seconds=10,
        isolated=False,
    )
    assert result["exit_code"] == 0
    await pool.close(session)
    with pytest.raises(ExecutorRPCError) as closed:
        await executor_pool.run_process(
            task_id=task_id,
            workspace_id="workspace_split",
            argv=("python", "-c", "print('no')"),
            timeout_seconds=10,
            isolated=False,
        )
    assert closed.value.code == "executor_binding_invalid"
    await provider_server.close()
    await executor_server.close()
    await broker_rpc.close()
