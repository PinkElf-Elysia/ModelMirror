"""Durable client dispatch boundary using synthetic sockets only."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from server.tests.test_xpert_runtime_client_tools import client_call, pair_host
from server.xpert_runtime import (
    ClientToolConnectionManager, ClientToolCoordinator, ClientToolStore,
    ClientToolsetProvider, RuntimeInterrupt, WorkflowExecutionStore,
)
import server.xpert_runtime.client_tool_store as store_module


def setup_request(tmp_path, tool="host_page_click"):
    store = ClientToolStore(tmp_path)
    host, _ = pair_host(store)
    request = store.create_request(
        operation_id="synthetic-operation", tool_call_id="synthetic-call",
        host_id=host.host_id, task_id="task", run_id="run", node_id="node",
        scope_type="workflow", scope_id="task:node", tool_name=tool,
        arguments={"ref": "synthetic-ref"}, schema_hash="synthetic-schema",
        timeout_seconds=60,
    )
    return store, host, request


class Socket:
    def __init__(self, send):
        self.send = send

    async def send_json(self, payload):
        await self.send(payload)

    async def close(self, **kwargs):
        pass


@pytest.mark.asyncio
async def test_dispatch_is_durable_before_socket_and_fast_reply(tmp_path):
    store, host, request = setup_request(tmp_path)
    manager = ClientToolConnectionManager(store)
    observed = []

    async def send(payload):
        disk = json.loads(store.snapshot_path.read_text(encoding="utf-8"))["requests"][0]
        observed.append(disk["status"])
        assert disk["dispatch_count"] == payload["dispatch_count"] == 1
        assert disk["dispatched_connection_id"] == "connection-1"
        store.mark_running(request.request_id, host_id=host.host_id,
                           connection_id="connection-1", dispatch_count=payload["dispatch_count"])
        store.complete_request(
            request.request_id, host_id=host.host_id,
            operation_id=request.operation_id, tool_call_id=request.tool_call_id,
            result="synthetic success",
            connection_id="connection-1", dispatch_count=payload["dispatch_count"],
        )

    await manager.attach(host.host_id, "connection-1", Socket(send))
    assert await manager.dispatch(request)
    assert observed == ["dispatched"]
    assert store.require_request(request.request_id).status == "completed"
    assert ClientToolStore(tmp_path).require_request(request.request_id).status == "completed"


@pytest.mark.asyncio
async def test_send_error_after_delivery_keeps_mutation_uncertain(tmp_path):
    store, host, request = setup_request(tmp_path)
    manager = ClientToolConnectionManager(store)
    calls = []

    async def send(payload):
        calls.append(payload["operation_id"])
        raise OSError("synthetic connection loss after delivery")

    await manager.attach(host.host_id, "connection-1", Socket(send))
    assert not await manager.dispatch(request)
    assert calls == [request.operation_id]
    assert store.require_request(request.request_id).status == "uncertain"
    assert ClientToolStore(tmp_path).require_request(request.request_id).status == "uncertain"
    assert store.list_requests(status="pending") == []


@pytest.mark.asyncio
async def test_stale_cancelled_request_is_not_sent(tmp_path):
    store, host, request = setup_request(tmp_path)
    cached = deepcopy(request)
    store.cancel_request(request.request_id)
    manager = ClientToolConnectionManager(store)
    calls = []

    async def send(payload):
        calls.append(payload)

    await manager.attach(host.host_id, "connection-1", Socket(send))
    assert not await manager.dispatch(cached)
    assert calls == []


@pytest.mark.asyncio
async def test_duplicate_dispatch_does_not_send_twice(tmp_path):
    store, host, request = setup_request(tmp_path)
    manager = ClientToolConnectionManager(store)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def send(payload):
        calls.append(payload)
        entered.set()
        await release.wait()

    await manager.attach(host.host_id, "connection-1", Socket(send))
    first = asyncio.create_task(manager.dispatch(deepcopy(request)))
    await asyncio.wait_for(entered.wait(), 3)
    second = asyncio.create_task(manager.dispatch(deepcopy(request)))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    assert results == [True, False]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_failed_marker_never_sends_and_rolls_back(tmp_path, monkeypatch):
    store, host, request = setup_request(tmp_path)
    before = deepcopy(request)
    disk = store.snapshot_path.read_bytes()
    calls = []

    def broken():
        raise OSError("synthetic disk failure")

    async def send(payload):
        calls.append(payload)

    monkeypatch.setattr(store, "_persist_unlocked", broken)
    manager = ClientToolConnectionManager(store)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    with pytest.raises(OSError):
        await manager.dispatch(request)
    assert calls == []
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.parametrize("tool", ["host_page_click", "host_page_read"])
def test_disconnect_after_dispatch_preserves_read_only_replay(tmp_path, tool):
    store, host, request = setup_request(tmp_path, tool)
    store.mark_dispatched(request.request_id)
    store.disconnect_host(host.host_id, connection_id="connection-1")
    expected = "uncertain" if request.mutating else "pending"
    assert store.require_request(request.request_id).status == expected
    assert ClientToolStore(tmp_path).require_request(request.request_id).status == expected


def test_disconnect_persistence_failure_rolls_back_host_and_requests(tmp_path, monkeypatch):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    before_host = deepcopy(store.require_host(host.host_id))
    before_request = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()

    def broken():
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(store, "_persist_unlocked", broken)
    with pytest.raises(OSError):
        store.disconnect_host(host.host_id, connection_id="connection-1")
    assert store.require_host(host.host_id) == before_host
    assert store.require_request(request.request_id) == before_request
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["expired", "revision", "revoked", "replaced", "wrong_host"])
async def test_dispatch_rechecks_authoritative_identity(tmp_path, monkeypatch, invalid):
    store, host, request = setup_request(tmp_path)
    cached = deepcopy(request)
    connection_id = "connection-1"
    if invalid == "expired":
        monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: request.expires_at + 1))
    elif invalid == "revision":
        store.cancel_request(request.request_id)
        store.retry_request(request.request_id)
    elif invalid == "revoked":
        store.revoke_host(host.host_id)
    elif invalid == "replaced":
        connection_id = "old-connection"
    else:
        other, _ = pair_host(store, name="Other synthetic host")
        cached.host_id = other.host_id
    calls = []

    async def send(payload):
        calls.append(payload)

    manager = ClientToolConnectionManager(store)
    await manager.attach(cached.host_id, connection_id, Socket(send))
    before = store.snapshot_path.read_bytes()
    assert not await manager.dispatch(cached)
    assert calls == []
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.asyncio
async def test_payload_comes_from_store_not_cached_request(tmp_path):
    store, host, request = setup_request(tmp_path)
    cached = deepcopy(request)
    cached.arguments = {"value": "forged cached payload"}
    cached.operation_id = "forged-operation"
    payloads = []

    async def send(payload):
        payloads.append(payload)

    manager = ClientToolConnectionManager(store)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    assert await manager.dispatch(cached)
    assert payloads[0]["arguments"] == request.arguments
    assert payloads[0]["operation_id"] == request.operation_id


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_save_fails", [False, True])
async def test_cancelled_socket_send_is_not_replayed(tmp_path, monkeypatch, disconnect_save_fails):
    store, host, request = setup_request(tmp_path)
    manager = ClientToolConnectionManager(store)
    entered = asyncio.Event()

    async def send(payload):
        entered.set()
        await asyncio.Event().wait()

    await manager.attach(host.host_id, "connection-1", Socket(send))
    operation = asyncio.create_task(manager.dispatch(request))
    await asyncio.wait_for(entered.wait(), 3)
    if disconnect_save_fails:
        def broken():
            raise OSError("synthetic reconciliation failure")
        monkeypatch.setattr(store, "_persist_unlocked", broken)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert not await manager.is_online(host.host_id)
    assert store.list_requests(status="pending") == []
    assert ClientToolStore(tmp_path).require_request(request.request_id).status == "uncertain"


@pytest.mark.parametrize("transition", ["dispatch", "disconnect"])
def test_atomic_replace_failure_does_not_advance_dispatch_state(tmp_path, monkeypatch, transition):
    store, host, request = setup_request(tmp_path)
    if transition == "disconnect":
        store.mark_dispatched(request.request_id)
    before = deepcopy(store.require_request(request.request_id))
    before_host = deepcopy(store.require_host(host.host_id))
    disk = store.snapshot_path.read_bytes()

    def broken(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(store_module.os, "replace", broken)
    with pytest.raises(OSError):
        if transition == "dispatch":
            store.mark_dispatched(request.request_id)
        else:
            store.disconnect_host(host.host_id, connection_id="connection-1")
    assert store.require_request(request.request_id) == before
    assert store.require_host(host.host_id) == before_host
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.asyncio
async def test_lost_ack_uncertainty_reaches_workflow_as_error_without_resend(tmp_path):
    store = ClientToolStore(tmp_path / "clients")
    host, _ = pair_host(store)
    provider = ClientToolsetProvider(store)
    call = client_call(host.host_id, "host_page_click")
    with pytest.raises(RuntimeInterrupt) as caught:
        await provider.prepare_dispatch(call)
    request = store.require_request(caught.value.wait_id)
    executions = WorkflowExecutionStore(tmp_path / "executions")
    executions.create(task_id=request.task_id, run_id=request.run_id, run_type="workflow",
                      workflow={"nodes": [], "edges": []}, inputs={})
    executions.suspend(request.task_id, wait_kind="client_tool", wait_id=request.request_id,
                       continuation={"queue": ["node"], "executed": []},
                       safe_event={"event": "client_tool_waiting", "request_id": request.request_id})
    calls, results = [], []

    async def send(payload):
        calls.append(payload["operation_id"])
        raise OSError("synthetic lost acknowledgement")

    async def resume(execution, resolved):
        await provider.prepare_dispatch(call)
        result = await provider.call_tool(call)
        results.append((resolved.status, result.is_error, json.loads(result.output)["status"]))
        executions.fail(execution.task_id, error="CLIENT_RESULT_UNCERTAIN",
                        expected_lease_token=execution.lease_token)

    manager = ClientToolConnectionManager(store)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    coordinator = ClientToolCoordinator(store, executions, manager, resume)
    await coordinator.run_once()
    await coordinator.run_once()
    assert len(calls) == 1
    assert results == [("uncertain", True, "uncertain")]
    assert executions.require(request.task_id).status == "failed"
    assert len(store.list_requests()) == 1


@pytest.mark.asyncio
async def test_send_failure_does_not_log_transport_payload(tmp_path, caplog):
    store, host, request = setup_request(tmp_path)

    async def send(payload):
        raise OSError("synthetic-private-transport-body")

    manager = ClientToolConnectionManager(store)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    assert not await manager.dispatch(request)
    assert "synthetic-private-transport-body" not in caplog.text
    assert "synthetic-private-transport-body" not in store.snapshot_path.read_text(encoding="utf-8")
