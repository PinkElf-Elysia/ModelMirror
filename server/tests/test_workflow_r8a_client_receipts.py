"""Client receipt persistence and continuation visibility, without external IO."""
from __future__ import annotations

from copy import deepcopy

import pytest

from server.tests.test_workflow_r8a_client_dispatch import setup_request
from server.tests.test_xpert_runtime_client_tools import client_call, pair_host
from server.xpert_runtime import (
    ClientToolConflictError, ClientToolConnectionManager, ClientToolCoordinator,
    ClientToolsetProvider, ClientToolStore, RuntimeInterrupt, WorkflowExecutionStore,
)
import server.xpert_runtime.client_tool_store as store_module


def receipt(store, request, kind, *, metadata=None):
    if kind == "running":
        return store.mark_running(request.request_id, host_id=request.host_id)
    identity = dict(host_id=request.host_id, operation_id=request.operation_id,
                    tool_call_id=request.tool_call_id)
    if kind == "completed":
        return store.complete_request(request.request_id, result="synthetic result",
                                      metadata=metadata, **identity)
    return store.fail_request(request.request_id, error="SYNTHETIC_CLIENT_FAILURE", **identity)


@pytest.mark.parametrize("kind", ["running", "completed", "failed"])
@pytest.mark.parametrize("fault", ["persist", "replace"])
def test_uncommitted_receipt_does_not_change_store(tmp_path, monkeypatch, kind, fault):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()

    def broken(*args):
        raise OSError("synthetic receipt persistence failure")

    if fault == "persist":
        monkeypatch.setattr(store, "_persist_unlocked", broken)
    else:
        monkeypatch.setattr(store_module.os, "replace", broken)
    with pytest.raises(OSError):
        receipt(store, request, kind)
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk
    assert ClientToolStore(tmp_path).require_request(request.request_id).status == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["completed", "failed"])
async def test_uncommitted_terminal_never_resumes_and_committed_receipt_resumes_once(tmp_path, monkeypatch, kind):
    clients = ClientToolStore(tmp_path / "clients")
    host, _ = pair_host(clients)
    provider = ClientToolsetProvider(clients)
    call = client_call(host.host_id, "host_page_click")
    with pytest.raises(RuntimeInterrupt) as caught:
        await provider.prepare_dispatch(call)
    request = clients.require_request(caught.value.wait_id)
    clients.mark_dispatched(request.request_id)
    executions = WorkflowExecutionStore(tmp_path / "executions")
    executions.create(task_id=request.task_id, run_id=request.run_id, run_type="workflow",
                      workflow={"nodes": [], "edges": []}, inputs={})
    executions.suspend(request.task_id, wait_kind="client_tool", wait_id=request.request_id,
                       continuation={"queue": ["node"], "executed": []},
                       safe_event={"event": "client_tool_waiting", "request_id": request.request_id})
    resumed = []

    async def resume(execution, response):
        result = await provider.call_tool(call)
        resumed.append((response.status, result.is_error))
        if result.is_error:
            executions.fail(execution.task_id, error="SYNTHETIC_CLIENT_FAILURE",
                            expected_lease_token=execution.lease_token)
        else:
            executions.complete(execution.task_id, result=result.output,
                                expected_lease_token=execution.lease_token)

    coordinator = ClientToolCoordinator(clients, executions, ClientToolConnectionManager(clients), resume)
    persist = clients._persist_unlocked

    def broken():
        raise OSError("synthetic receipt write failure")

    monkeypatch.setattr(clients, "_persist_unlocked", broken)
    with pytest.raises(OSError):
        receipt(clients, request, kind)
    monkeypatch.setattr(clients, "_persist_unlocked", persist)
    assert await coordinator.run_once() == 0
    assert resumed == []
    assert executions.require(request.task_id).status == "waiting"
    with pytest.raises(RuntimeInterrupt):
        await provider.prepare_dispatch(call)
    receipt(clients, request, kind)
    assert ClientToolStore(tmp_path / "clients").require_request(request.request_id).status == kind
    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert resumed == [(kind, kind == "failed")]


@pytest.mark.parametrize("kind", ["running", "completed", "failed"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_receipt_return_is_not_a_mutable_store_alias(tmp_path, kind, duplicate):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    result = receipt(store, request, kind)
    if duplicate:
        result = receipt(store, request, kind)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()
    result.status = "pending"
    result.result_metadata["operation_receipt"] = {"forged": True}
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk


def test_complete_metadata_is_detached_from_caller(tmp_path):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    metadata = {"operation_receipt": {"status": "applied"}, "token": "synthetic-excluded"}
    receipt(store, request, "completed", metadata=metadata)
    metadata["operation_receipt"]["status"] = "forged"
    saved = store.require_request(request.request_id)
    assert saved.result_metadata == {"operation_receipt": {"status": "applied"}}
    assert ClientToolStore(tmp_path).require_request(request.request_id) == saved


def test_error_before_dispatch_cannot_settle_pending_request(tmp_path):
    store, host, request = setup_request(tmp_path)
    before = store.snapshot_path.read_bytes()
    with pytest.raises(ClientToolConflictError):
        receipt(store, request, "failed")
    assert store.require_request(request.request_id).status == "pending"
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.parametrize("kind", ["completed", "failed"])
@pytest.mark.parametrize("field", ["host_id", "operation_id", "tool_call_id"])
@pytest.mark.parametrize("settled", [False, True])
def test_receipt_identity_is_checked_before_terminal_noop(tmp_path, kind, field, settled):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    if settled:
        receipt(store, request, kind)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()
    identity = dict(host_id=host.host_id, operation_id=request.operation_id,
                    tool_call_id=request.tool_call_id)
    identity[field] = "another-synthetic-identity"
    with pytest.raises(ClientToolConflictError):
        if kind == "completed":
            store.complete_request(request.request_id, result="forged", **identity)
        else:
            store.fail_request(request.request_id, error="FORGED", **identity)
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk


def test_wrong_host_cannot_mark_running(tmp_path):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()
    with pytest.raises(ClientToolConflictError):
        store.mark_running(request.request_id, host_id="another-synthetic-host")
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.parametrize("kind", ["completed", "failed"])
def test_receipt_from_running_is_durable_and_duplicate_keeps_first_result(tmp_path, monkeypatch, kind):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    running = store.mark_running(request.request_id, host_id=host.host_id)
    saved = receipt(store, request, kind)
    assert saved.status == kind
    assert saved.revision == running.revision + 1
    assert ClientToolStore(tmp_path).require_request(request.request_id) == saved
    disk = store.snapshot_path.read_bytes()

    def forbidden():
        pytest.fail("A duplicate terminal receipt must not write again")

    monkeypatch.setattr(store, "_persist_unlocked", forbidden)
    identity = dict(host_id=host.host_id, operation_id=request.operation_id,
                    tool_call_id=request.tool_call_id)
    if kind == "completed":
        duplicate = store.complete_request(request.request_id, result="different late result",
                                           metadata={"operation_receipt": {"late": True}}, **identity)
        running_again = store.mark_running(request.request_id, host_id=host.host_id)
        assert running_again == saved
        running_again.status = "pending"
    else:
        duplicate = store.fail_request(request.request_id, error="DIFFERENT_LATE_ERROR", **identity)
    assert duplicate == saved
    assert duplicate is not store.require_request(request.request_id)
    assert store.require_request(request.request_id) == saved
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "expired", "uncertain"])
def test_late_receipt_cannot_revive_terminal_request(tmp_path, terminal):
    store, host, request = setup_request(tmp_path)
    if terminal != "cancelled":
        store.mark_dispatched(request.request_id)
    if terminal == "failed":
        receipt(store, request, "failed")
    elif terminal == "cancelled":
        store.cancel_request(request.request_id)
    elif terminal == "expired":
        store.expire_due(now=request.expires_at + 1)
    else:
        store.disconnect_host(host.host_id, connection_id="connection-1")
    before = deepcopy(store.require_request(request.request_id))
    assert before.status == terminal
    disk = store.snapshot_path.read_bytes()
    for kind in ["running", "completed"]:
        with pytest.raises(ClientToolConflictError):
            receipt(store, request, kind)
    duplicate = receipt(store, request, "failed")
    assert duplicate == before
    duplicate.status = "pending"
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["completed", "failed"])
async def test_committed_receipt_survives_restart_before_workflow_wake(tmp_path, kind):
    clients = ClientToolStore(tmp_path / "clients")
    host, _ = pair_host(clients)
    provider = ClientToolsetProvider(clients)
    call = client_call(host.host_id, "host_page_click")
    with pytest.raises(RuntimeInterrupt) as caught:
        await provider.prepare_dispatch(call)
    request = clients.require_request(caught.value.wait_id)
    clients.mark_dispatched(request.request_id)
    executions = WorkflowExecutionStore(tmp_path / "executions")
    executions.create(task_id=request.task_id, run_id=request.run_id, run_type="workflow",
                      workflow={"nodes": [], "edges": []}, inputs={})
    executions.suspend(request.task_id, wait_kind="client_tool", wait_id=request.request_id,
                       continuation={"queue": ["node"], "executed": []},
                       safe_event={"event": "client_tool_waiting", "request_id": request.request_id})
    saved = receipt(clients, request, kind)

    # Reload both stores before any coordinator has observed the terminal receipt.
    clients = ClientToolStore(tmp_path / "clients")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    provider = ClientToolsetProvider(clients)
    assert clients.require_request(request.request_id) == saved
    assert executions.require(request.task_id).status == "waiting"
    consumed = []

    async def resume(execution, response):
        result = await provider.call_tool(call)
        consumed.append((response.status, result.is_error))
        if result.is_error:
            executions.fail(execution.task_id, error="SYNTHETIC_CLIENT_FAILURE",
                            expected_lease_token=execution.lease_token)
        else:
            executions.complete(execution.task_id, result=result.output,
                                expected_lease_token=execution.lease_token)

    class NoDispatch(ClientToolConnectionManager):
        async def dispatch(self, request):
            pytest.fail("A committed receipt must never dispatch the tool again")

    coordinator = ClientToolCoordinator(clients, executions, NoDispatch(clients), resume)
    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert consumed == [(kind, kind == "failed")]
    assert clients.require_request(request.request_id) == saved
    assert WorkflowExecutionStore(tmp_path / "executions").require(request.task_id).status == kind
