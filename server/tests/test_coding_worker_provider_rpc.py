from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.broker_rpc import BrokerRPCServer
from server.coding_worker.contracts import PolicyProfile, TaskBudget
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderEventKind,
    ProviderOpenRequest,
)
from server.coding_worker.provider_rpc import (
    ProviderRPCError,
    ProviderRPCServer,
    ProviderSidecarClientPool,
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
