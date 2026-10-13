"""Exact parent permission for Client retry and dispatch; no real client IO."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from threading import Thread

import pytest
from fastapi import HTTPException

from server.tests.test_workflow_r8a_client_dispatch import Socket, setup_request
from server.xpert_runtime import ClientToolConnectionManager, ClientToolCoordinator, ClientToolStore, WorkflowExecutionStore
from server.xpert_runtime import client_tool_api as api


def parent_store(tmp_path, request, state="waiting"):
    executions = WorkflowExecutionStore(tmp_path / "executions")
    if state == "missing":
        return executions
    executions.create(task_id=request.task_id, run_id="other-run" if state == "wrong_run" else request.run_id,
                      run_type="workflow", workflow={"nodes": [], "edges": []}, inputs={},
                      runtime_metadata={"terminal_source_invalidated": state == "invalidated"})
    if state in {"completed", "failed", "cancelled", "rejected", "running"}:
        if state == "completed":
            executions.complete(request.task_id, result="synthetic")
        elif state != "running":
            getattr(executions, {"failed": "fail", "cancelled": "cancel", "rejected": "reject"}[state])(request.task_id, error="synthetic")
        return executions
    executions.suspend(request.task_id, wait_kind="approval" if state == "wrong_kind" else "client_tool",
                       wait_id="other-request" if state == "wrong_wait" else request.request_id,
                       continuation={"queue": [request.node_id], "executed": []})
    if state == "ready":
        executions.mark_ready(request.task_id, wait_kind="client_tool", wait_id=request.request_id)
    return executions


async def forbidden_resume(*args):
    pytest.fail("This test must not resume the parent")


def configure(monkeypatch, clients, executions):
    manager = ClientToolConnectionManager(clients)
    coordinator = ClientToolCoordinator(clients, executions, manager, forbidden_resume)
    monkeypatch.setattr(api, "_store", clients)
    monkeypatch.setattr(api, "_coordinator", coordinator)
    return manager, coordinator


INVALID_PARENTS = ["missing", "completed", "failed", "cancelled", "rejected", "running", "ready", "wrong_run", "wrong_wait", "wrong_kind", "invalidated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", INVALID_PARENTS)
@pytest.mark.parametrize("action", ["retry", "dispatch"])
async def test_invalid_parent_cannot_retry_or_dispatch(tmp_path, monkeypatch, state, action):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request, state)
    manager, coordinator = configure(monkeypatch, clients, executions)
    sent = []
    async def send(payload):
        sent.append(payload)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    if action == "retry":
        clients.cancel_request(request.request_id)
    disk = clients.snapshot_path.read_bytes()
    if action == "retry":
        with pytest.raises(HTTPException) as error:
            await api.retry_client_tool_request(request.request_id)
        assert error.value.status_code == 409
        assert not coordinator._wake.is_set()
        payload = await api.get_client_tool_request(request.request_id)
        assert payload["retryable"] is False
        assert payload["retry_blocked_reason"]
    else:
        assert await coordinator.run_once() == 0
    assert sent == []
    assert clients.snapshot_path.read_bytes() == disk


@pytest.mark.asyncio
async def test_exact_wait_can_retry_then_dispatch_once(tmp_path, monkeypatch):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    manager, coordinator = configure(monkeypatch, clients, executions)
    clients.cancel_request(request.request_id)
    assert (await api.get_client_tool_request(request.request_id))["retryable"] is True
    revision = clients.require_request(request.request_id).revision
    result = await api.retry_client_tool_request(request.request_id)
    assert result["revision"] == revision + 1
    assert result["status"] == "pending"
    sent = []
    async def send(payload):
        sent.append(payload)
        # The parent lock must never be held across socket IO.
        def check_lock():
            acquired = executions._lock.acquire(blocking=False)
            if acquired:
                executions._lock.release()
            return acquired
        assert await asyncio.to_thread(check_lock)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["retry", "dispatch"])
async def test_cancel_after_parent_read_rejects_stale_permission(tmp_path, monkeypatch, action):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    manager, coordinator = configure(monkeypatch, clients, executions)
    if action == "retry":
        clients.cancel_request(request.request_id)
    snapshot = executions.snapshot
    def cancelled_snapshot(task_id):
        before = snapshot(task_id)
        executions.cancel(task_id)
        return before
    monkeypatch.setattr(executions, "snapshot", cancelled_snapshot)
    sent = []
    async def send(payload):
        sent.append(payload)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    disk = clients.snapshot_path.read_bytes()
    if action == "retry":
        with pytest.raises(HTTPException) as error:
            await api.retry_client_tool_request(request.request_id)
        assert error.value.status_code == 409
    else:
        assert await coordinator.run_once() == 0
    assert sent == []
    assert clients.snapshot_path.read_bytes() == disk


@pytest.mark.asyncio
async def test_retry_cannot_be_woken_by_cached_failed_receipt(tmp_path, monkeypatch):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    _, coordinator = configure(monkeypatch, clients, executions)
    clients.mark_dispatched(request.request_id)
    clients.fail_request(request.request_id, host_id=host.host_id,
                         operation_id=request.operation_id, tool_call_id=request.tool_call_id, error="synthetic")
    original = clients.get_request
    intercepted = False
    def retry_after_read(request_id):
        nonlocal intercepted
        value = deepcopy(original(request_id))
        if not intercepted:
            intercepted = True
            clients.retry_request(request_id)
        return value
    monkeypatch.setattr(clients, "get_request", retry_after_read)
    assert await coordinator.run_once() == 0
    assert executions.require(request.task_id).status == "waiting"
    assert clients.require_request(request.request_id).status == "pending"


@pytest.mark.asyncio
async def test_retry_without_coordinator_is_not_authorized(tmp_path, monkeypatch):
    clients, _, request = setup_request(tmp_path, "host_page_read")
    clients.cancel_request(request.request_id)
    monkeypatch.setattr(api, "_store", clients)
    monkeypatch.setattr(api, "_coordinator", None)
    disk = clients.snapshot_path.read_bytes()
    with pytest.raises(HTTPException) as error:
        await api.retry_client_tool_request(request.request_id)
    assert error.value.status_code == 409
    assert (await api.get_client_tool_request(request.request_id))["retryable"] is False
    assert clients.snapshot_path.read_bytes() == disk


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["retry", "dispatch"])
async def test_parent_lock_fences_cancellation_during_local_commit(tmp_path, monkeypatch, action):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    manager, coordinator = configure(monkeypatch, clients, executions)
    if action == "retry":
        clients.cancel_request(request.request_id)
    acquired = []
    persisted = clients._persist_unlocked
    def probe_then_persist():
        def contender():
            owns = executions._lock.acquire(blocking=False)
            acquired.append(owns)
            if owns:
                executions._lock.release()
        thread = Thread(target=contender)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
        persisted()
    monkeypatch.setattr(clients, "_persist_unlocked", probe_then_persist)
    if action == "retry":
        await api.retry_client_tool_request(request.request_id)
    else:
        async def send(payload):
            pass
        await manager.attach(host.host_id, "connection-1", Socket(send))
        assert await coordinator.run_once() == 1
    assert acquired == [False]


@pytest.mark.asyncio
async def test_cancel_between_retry_and_dispatch_prevents_send_after_restart(tmp_path, monkeypatch):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    _, _ = configure(monkeypatch, clients, executions)
    clients.cancel_request(request.request_id)
    await api.retry_client_tool_request(request.request_id)
    executions.cancel(request.task_id)
    clients = ClientToolStore(tmp_path / "clients")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    clients.connect_host(host.host_id, connection_id="replacement", version="synthetic", capabilities=[], schema_hashes={})
    manager, coordinator = configure(monkeypatch, clients, executions)
    sent = []
    async def send(payload):
        sent.append(payload)
    await manager.attach(host.host_id, "replacement", Socket(send))
    assert await coordinator.run_once() == 0
    assert sent == []
    assert clients.require_request(request.request_id).dispatch_count == 0


@pytest.mark.asyncio
async def test_failed_local_dispatch_does_not_change_parent_or_send(tmp_path, monkeypatch):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    manager, coordinator = configure(monkeypatch, clients, executions)
    before = executions.require(request.task_id)
    sent = []
    async def send(payload):
        sent.append(payload)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    def broken():
        raise OSError("synthetic persistence error")
    monkeypatch.setattr(clients, "_persist_unlocked", broken)
    with pytest.raises(OSError):
        await coordinator.run_once()
    assert executions.require(request.task_id) == before
    assert clients.require_request(request.request_id).status == "pending"
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["waiting", "ready"])
async def test_another_parent_cannot_consume_client_receipt(tmp_path, monkeypatch, state):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    impostor = deepcopy(request)
    impostor.task_id = "other-parent"
    executions = parent_store(tmp_path, impostor, state)
    _, coordinator = configure(monkeypatch, clients, executions)
    clients.mark_dispatched(request.request_id)
    clients.complete_request(request.request_id, host_id=host.host_id,
                             operation_id=request.operation_id, tool_call_id=request.tool_call_id, result="synthetic")
    before = executions.require(impostor.task_id)
    assert await coordinator.run_once() == 0
    assert executions.require(impostor.task_id) == before


@pytest.mark.asyncio
async def test_parent_storage_failure_disables_retry_projection(tmp_path, monkeypatch):
    from server.xpert_runtime.execution_store import WorkflowExecutionStorageError
    clients, _, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    _, coordinator = configure(monkeypatch, clients, executions)
    clients.cancel_request(request.request_id)
    def broken(*args):
        raise WorkflowExecutionStorageError("synthetic-private-storage-location")
    monkeypatch.setattr(executions, "snapshot", broken)
    payload = await api.get_client_tool_request(request.request_id)
    assert payload["retryable"] is False
    assert "synthetic-private" not in payload["retry_blocked_reason"]
    with pytest.raises(HTTPException) as caught:
        await api.retry_client_tool_request(request.request_id)
    assert caught.value.status_code == 409
    assert not coordinator._wake.is_set()


@pytest.mark.asyncio
async def test_double_retry_increments_revision_once(tmp_path, monkeypatch):
    clients, _, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    _, coordinator = configure(monkeypatch, clients, executions)
    clients.cancel_request(request.request_id)
    before = clients.require_request(request.request_id).revision
    results = await asyncio.gather(api.retry_client_tool_request(request.request_id),
                                   api.retry_client_tool_request(request.request_id), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in results) == 1
    failures = [item for item in results if isinstance(item, HTTPException)]
    assert len(failures) == 1 and failures[0].status_code == 409
    assert clients.require_request(request.request_id).revision == before + 1
    assert coordinator._wake.is_set()


@pytest.mark.asyncio
async def test_disabled_coordinator_denies_retry_and_list_projection(tmp_path, monkeypatch):
    clients, _, request = setup_request(tmp_path / "clients", "host_page_read")
    executions = parent_store(tmp_path, request)
    _, coordinator = configure(monkeypatch, clients, executions)
    coordinator.enabled = False
    clients.cancel_request(request.request_id)
    rows = await api.list_client_tool_requests(limit=10)
    assert rows["requests"][0]["retryable"] is False
    with pytest.raises(HTTPException) as error:
        await api.retry_client_tool_request(request.request_id)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_valid_wait_remains_dispatchable_after_reload(tmp_path, monkeypatch):
    clients, host, request = setup_request(tmp_path / "clients", "host_page_read")
    parent_store(tmp_path, request)
    clients = ClientToolStore(tmp_path / "clients")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    clients.connect_host(host.host_id, connection_id="replacement", version="synthetic", capabilities=[], schema_hashes={})
    manager, coordinator = configure(monkeypatch, clients, executions)
    sent = []
    async def send(payload):
        sent.append(payload)
    await manager.attach(host.host_id, "replacement", Socket(send))
    assert await coordinator.run_once() == 1
    assert len(sent) == 1
    assert executions.require(request.task_id).status == "waiting"
