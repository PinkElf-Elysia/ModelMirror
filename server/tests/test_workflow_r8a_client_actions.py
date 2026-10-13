"""Explicit local request actions must not erase uncertain external effects."""
from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import HTTPException

from server.tests.test_workflow_r8a_client_dispatch import Socket, setup_request
from server.xpert_runtime import (
    ClientToolConflictError, ClientToolConnectionManager, ClientToolCoordinator,
    ClientToolStore, WorkflowExecutionStore,
)
import server.xpert_runtime.client_tool_store as store_module
import server.xpert_runtime.client_tool_api as api


def fail(store, request):
    return store.fail_request(request.request_id, host_id=request.host_id,
                              operation_id=request.operation_id,
                              tool_call_id=request.tool_call_id, error="SYNTHETIC_FAILURE")


def api_coordinator(store, request, tmp_path, wakes):
    executions = WorkflowExecutionStore(tmp_path / "parent-executions")
    executions.create(task_id=request.task_id, run_id=request.run_id, run_type="workflow",
                      workflow={"nodes": [], "edges": []}, inputs={})
    executions.suspend(request.task_id, wait_kind="client_tool", wait_id=request.request_id,
                       continuation={"queue": [request.node_id], "executed": []})
    async def forbidden_resume(*args):
        pytest.fail("An API action must not execute a continuation")
    coordinator = ClientToolCoordinator(store, executions, ClientToolConnectionManager(store), forbidden_resume)
    coordinator.wake = lambda: wakes.append(True)
    return coordinator


@pytest.mark.parametrize("action", ["retry", "cancel"])
@pytest.mark.parametrize("fault", ["persist", "replace"])
def test_failed_action_restores_memory_and_disk(tmp_path, monkeypatch, action, fault):
    store, host, request = setup_request(tmp_path, "host_page_read")
    if action == "retry":
        store.mark_dispatched(request.request_id)
        fail(store, request)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()

    def broken(*args):
        raise OSError("synthetic action persistence failure")

    if fault == "persist":
        monkeypatch.setattr(store, "_persist_unlocked", broken)
    else:
        monkeypatch.setattr(store_module.os, "replace", broken)
    with pytest.raises(OSError):
        getattr(store, f"{action}_request")(request.request_id)
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk
    assert ClientToolStore(tmp_path).require_request(request.request_id) == before


@pytest.mark.parametrize("status", ["failed", "cancelled", "expired", "uncertain"])
def test_legacy_dispatched_mutation_cannot_reuse_operation_identity(tmp_path, status):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    # Seed legacy terminal states; old cancel used cancelled even after dispatch.
    store.require_request(request.request_id).status = status
    store._persist_unlocked()
    store = ClientToolStore(tmp_path)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()
    with pytest.raises(ClientToolConflictError):
        store.retry_request(request.request_id)
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.parametrize("started", [False, True])
def test_cancel_mutation_after_dispatch_keeps_uncertainty(tmp_path, started):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    if started:
        store.mark_running(request.request_id, host_id=host.host_id)
    before = deepcopy(store.require_request(request.request_id))
    result = store.cancel_request(request.request_id)
    assert result.status == "uncertain"
    assert result.dispatch_count == before.dispatch_count
    assert result.operation_id == before.operation_id
    assert result.revision == before.revision + 1
    assert ClientToolStore(tmp_path).require_request(request.request_id) == result
    with pytest.raises(ClientToolConflictError):
        store.retry_request(request.request_id)


@pytest.mark.parametrize("action", ["retry", "cancel"])
def test_action_result_cannot_mutate_authoritative_request(tmp_path, action):
    store, host, request = setup_request(tmp_path, "host_page_read")
    if action == "retry":
        store.cancel_request(request.request_id)
    result = getattr(store, f"{action}_request")(request.request_id)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()
    result.status = "completed"
    result.arguments["forged"] = True
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk


@pytest.mark.asyncio
async def test_failed_retry_cannot_be_dispatched_by_polling(tmp_path, monkeypatch):
    store, host, request = setup_request(tmp_path, "host_page_read")
    store.mark_dispatched(request.request_id)
    fail(store, request)
    persist = store._persist_unlocked

    def broken():
        raise OSError("synthetic retry persistence failure")

    monkeypatch.setattr(store, "_persist_unlocked", broken)
    with pytest.raises(OSError):
        store.retry_request(request.request_id)
    monkeypatch.setattr(store, "_persist_unlocked", persist)
    calls = []

    async def send(payload):
        calls.append(payload)

    manager = ClientToolConnectionManager(store)
    await manager.attach(host.host_id, "connection-1", Socket(send))
    assert not await manager.dispatch(store.require_request(request.request_id))
    assert calls == []


@pytest.mark.parametrize("tool", ["host_page_read", "host_page_click"])
def test_pending_cancel_and_retry_preserve_safe_explicit_path(tmp_path, tool):
    store, host, request = setup_request(tmp_path, tool)
    cancelled = store.cancel_request(request.request_id)
    assert cancelled.status == "cancelled"
    retried = store.retry_request(request.request_id)
    assert retried.status == "pending"
    assert retried.operation_id == request.operation_id
    assert retried.dispatch_count == 0
    assert ClientToolStore(tmp_path).require_request(request.request_id) == retried


@pytest.mark.parametrize("tool", ["host_page_read", "host_page_click"])
@pytest.mark.parametrize("dispatched", [False, True])
def test_retry_projection_matches_store_decision(tmp_path, tool, dispatched):
    store, host, request = setup_request(tmp_path, tool)
    if dispatched:
        store.mark_dispatched(request.request_id)
    store.expire_due(now=request.expires_at + 1)
    saved = store.require_request(request.request_id)
    projection = store.serialize_request(saved)
    allowed = tool == "host_page_read" or not dispatched
    assert projection["retryable"] is allowed
    assert bool(projection["retry_blocked_reason"]) is (not allowed)
    if allowed:
        assert store.retry_request(request.request_id).status == "pending"
    else:
        with pytest.raises(ClientToolConflictError, match=projection["retry_blocked_reason"]):
            store.retry_request(request.request_id)


@pytest.mark.parametrize("evidence", ["dispatch_count", "dispatched_at", "started_at", "failed", "uncertain"])
def test_partial_legacy_dispatch_evidence_still_blocks_mutation_retry(tmp_path, evidence):
    store, host, request = setup_request(tmp_path)
    saved = store.require_request(request.request_id)
    saved.status = "expired"
    if evidence in {"failed", "uncertain"}:
        saved.status = evidence
    else:
        setattr(saved, evidence, 1)
    store._persist_unlocked()
    reloaded = ClientToolStore(tmp_path)
    with pytest.raises(ClientToolConflictError):
        reloaded.retry_request(request.request_id)
    assert reloaded.serialize_request(reloaded.require_request(request.request_id))["retryable"] is False


@pytest.mark.parametrize("first", ["complete", "cancel"])
def test_cancel_completion_order_never_reports_undo(tmp_path, monkeypatch, first):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)

    def complete():
        return store.complete_request(request.request_id, host_id=host.host_id,
                                      operation_id=request.operation_id, tool_call_id=request.tool_call_id,
                                      result="synthetic committed result")

    if first == "complete":
        saved = complete()
    else:
        saved = store.cancel_request(request.request_id)
        assert saved.status == "uncertain"
    disk = store.snapshot_path.read_bytes()

    def forbidden():
        pytest.fail("Settled actions must not write again")

    monkeypatch.setattr(store, "_persist_unlocked", forbidden)
    duplicate = store.cancel_request(request.request_id)
    assert duplicate == saved
    duplicate.result = "forged"
    if first == "cancel":
        with pytest.raises(ClientToolConflictError):
            complete()
    assert store.require_request(request.request_id) == saved
    assert store.snapshot_path.read_bytes() == disk


def test_uncertain_cancel_save_failure_leaves_original_dispatch(tmp_path, monkeypatch):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    before = deepcopy(store.require_request(request.request_id))
    disk = store.snapshot_path.read_bytes()

    def broken(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(store_module.os, "replace", broken)
    with pytest.raises(OSError):
        store.cancel_request(request.request_id)
    assert store.require_request(request.request_id) == before
    assert store.snapshot_path.read_bytes() == disk
    assert ClientToolStore(tmp_path).require_request(request.request_id).status == "uncertain"


@pytest.mark.asyncio
async def test_api_blocked_retry_does_not_wake_or_dispatch(tmp_path, monkeypatch):
    store, host, request = setup_request(tmp_path)
    store.mark_dispatched(request.request_id)
    store.cancel_request(request.request_id)
    wakes = []

    monkeypatch.setattr(api, "_store", store)
    monkeypatch.setattr(api, "_coordinator", api_coordinator(store, request, tmp_path, wakes))
    disk = store.snapshot_path.read_bytes()
    with pytest.raises(HTTPException) as caught:
        await api.retry_client_tool_request(request.request_id)
    assert caught.value.status_code == 409
    assert wakes == []
    assert store.snapshot_path.read_bytes() == disk
    assert (await api.get_client_tool_request(request.request_id))["retryable"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["retry", "cancel"])
async def test_api_unsaved_action_never_wakes_coordinator(tmp_path, monkeypatch, action):
    store, host, request = setup_request(tmp_path, "host_page_read")
    if action == "retry":
        store.cancel_request(request.request_id)
    before = deepcopy(store.require_request(request.request_id))
    wakes = []

    attempted = []
    def broken():
        attempted.append(True)
        raise OSError("synthetic local action save failure")

    monkeypatch.setattr(api, "_store", store)
    monkeypatch.setattr(api, "_coordinator", api_coordinator(store, request, tmp_path, wakes))
    monkeypatch.setattr(store, "_persist_unlocked", broken)
    with pytest.raises(HTTPException):
        await getattr(api, f"{action}_client_tool_request")(request.request_id)
    assert wakes == []
    assert attempted == [True]
    assert store.require_request(request.request_id) == before


@pytest.mark.asyncio
async def test_unsaved_cancel_is_invisible_to_workflow_polling(tmp_path, monkeypatch):
    store, host, request = setup_request(tmp_path / "clients")
    store.mark_dispatched(request.request_id)
    executions = WorkflowExecutionStore(tmp_path / "executions")
    executions.create(task_id=request.task_id, run_id=request.run_id, run_type="workflow",
                      workflow={"nodes": [], "edges": []}, inputs={})
    executions.suspend(request.task_id, wait_kind="client_tool", wait_id=request.request_id,
                       continuation={"queue": ["node"], "executed": []},
                       safe_event={"event": "client_tool_waiting", "request_id": request.request_id})
    persist = store._persist_unlocked

    def broken():
        raise OSError("synthetic cancellation save failure")

    async def forbidden_resume(*args):
        pytest.fail("An uncommitted cancellation must not resume the workflow")

    monkeypatch.setattr(store, "_persist_unlocked", broken)
    with pytest.raises(OSError):
        store.cancel_request(request.request_id)
    monkeypatch.setattr(store, "_persist_unlocked", persist)
    coordinator = ClientToolCoordinator(store, executions, ClientToolConnectionManager(store), forbidden_resume)
    assert await coordinator.run_once() == 0
    assert executions.require(request.task_id).status == "waiting"
    assert store.require_request(request.request_id).status == "dispatched"
