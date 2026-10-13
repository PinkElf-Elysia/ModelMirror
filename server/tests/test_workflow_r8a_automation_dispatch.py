"""Target dispatch uncertainty; no external model or business writes."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from server.xpert_runtime import AutomationCoordinator, AutomationStore, AutomationTargetResult, RunRegistry
from server.xpert_runtime.automation_store import AutomationConflictError
import server.xpert_runtime.automation_store as store_module
from server.tests.test_workflow_r8a_automation_fencing import make_execution


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError, asyncio.TimeoutError])
async def test_dispatched_error_never_schedules_retry(tmp_path, failure):
    store, execution = make_execution(tmp_path)
    calls = []

    async def target(*args):
        calls.append("sent")
        raise failure("private provider response")

    registry = RunRegistry()
    worker = AutomationCoordinator(store, registry, target)
    assert await worker.run_once() == 0
    saved = store.get_execution(execution.execution_id)
    assert saved.status == "dead_letter"
    assert saved.error == "AUTOMATION_DISPATCH_UNCERTAIN"
    assert "private provider response" not in repr(saved)
    assert await worker.run_once() == 0
    assert calls == ["sent"]
    assert store.claimable(now=10**12) == []


@pytest.mark.asyncio
async def test_shutdown_then_expired_lease_does_not_redispatch(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    now = [execution.available_at + 1]
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: now[0]))
    entered = asyncio.Event()
    calls = []

    async def target(*args):
        calls.append("sent")
        entered.set()
        await asyncio.Event().wait()

    worker = AutomationCoordinator(store, RunRegistry(), target, lease_seconds=10)
    operation = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(entered.wait(), 3)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    now[0] += 20
    restored = AutomationStore(tmp_path)

    async def forbidden(*args):
        calls.append("replayed")
        return AutomationTargetResult("unexpected", "run", "task")

    assert await AutomationCoordinator(restored, RunRegistry(), forbidden).run_once() == 0
    assert calls == ["sent"]
    assert restored.get_execution(execution.execution_id).error == "AUTOMATION_DISPATCH_UNCERTAIN"


@pytest.mark.asyncio
async def test_live_dispatched_lease_cannot_be_taken_over(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    now = [execution.available_at + 1]
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: now[0]))
    entered, release = asyncio.Event(), asyncio.Event()

    async def target(*args):
        entered.set()
        await release.wait()
        return AutomationTargetResult("late", "run", "task")

    operation = asyncio.create_task(AutomationCoordinator(store, RunRegistry(), target, lease_seconds=10).run_once())
    await asyncio.wait_for(entered.wait(), 3)
    now[0] += 20
    try:
        with pytest.raises(AutomationConflictError):
            store.claim_execution(execution.execution_id, worker_id="second")
    finally:
        release.set()
        await operation


@pytest.mark.asyncio
async def test_ambiguous_failure_cannot_be_cleared_by_plain_retry(tmp_path):
    store, execution = make_execution(tmp_path)

    async def target(*args):
        raise ValueError("unknown outcome")

    await AutomationCoordinator(store, RunRegistry(), target).run_once()
    assert store.get_execution(execution.execution_id).status == "dead_letter"
    with pytest.raises(AutomationConflictError):
        store.retry_execution(execution.execution_id, reset_attempts=True)


@pytest.mark.asyncio
async def test_real_runtime_deadline_cancels_target_without_retry(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    # Accelerate only the timer; the persisted authorization remains valid.
    monkeypatch.setattr(store, "remaining_runtime", lambda *_: 0.02)
    stopped = asyncio.Event()

    async def target(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    assert await AutomationCoordinator(store, RunRegistry(), target).run_once() == 0
    assert stopped.is_set()
    assert store.get_execution(execution.execution_id).error == "AUTOMATION_DISPATCH_UNCERTAIN"


@pytest.mark.asyncio
async def test_failed_dispatch_marker_never_invokes_target(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    persist = store._persist_unlocked

    def broken():
        item = store.get_execution(execution.execution_id)
        if item.metadata.get("target_dispatch", {}).get("phase") == "started":
            raise OSError("synthetic disk failure")
        persist()

    monkeypatch.setattr(store, "_persist_unlocked", broken)

    async def forbidden(*args):
        pytest.fail("uncommitted dispatch marker invoked target")

    assert await AutomationCoordinator(store, RunRegistry(), forbidden).run_once() == 0
    saved = store.get_execution(execution.execution_id)
    assert saved.status == "pending"
    assert saved.metadata["target_dispatch"]["phase"] == "prepared"
    assert AutomationStore(tmp_path).get_execution(execution.execution_id) == saved


@pytest.mark.parametrize("transition", ["claim", "dispatch", "complete", "wait", "reconcile"])
def test_dispatch_boundary_store_writes_rollback(tmp_path, monkeypatch, transition):
    store, execution = make_execution(tmp_path)
    claimed = None
    if transition != "claim":
        claimed = store.claim_execution(execution.execution_id, worker_id="one")
        if transition != "dispatch":
            store.mark_target_dispatch(execution.execution_id, lease_token=claimed.lease_token, run_id="registry-run")
    before = store.get_execution(execution.execution_id)

    def broken():
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(store, "_persist_unlocked", broken)
    with pytest.raises(OSError):
        if transition == "claim":
            store.claim_execution(execution.execution_id, worker_id="one")
        elif transition == "dispatch":
            store.mark_target_dispatch(execution.execution_id, lease_token=claimed.lease_token, run_id="registry-run")
        elif transition == "complete":
            store.complete_execution(execution.execution_id, result="done", lease_token=claimed.lease_token)
        elif transition == "wait":
            store.mark_waiting(execution.execution_id, status="waiting_client", run_id="run", workflow_task_id="task", wait_id="wait", lease_token=claimed.lease_token)
        else:
            store.reconcile_uncertain_dispatches(now=claimed.lease_expires_at + 1)
    assert store.get_execution(execution.execution_id) == before
    assert AutomationStore(tmp_path).get_execution(execution.execution_id) == before


def test_prepared_expired_lease_can_be_reclaimed_but_not_dispatched_twice(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    first = store.claim_execution(execution.execution_id, worker_id="first")
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: first.lease_expires_at + 1))
    second = store.claim_execution(execution.execution_id, worker_id="second")
    with pytest.raises(AutomationConflictError):
        store.mark_target_dispatch(execution.execution_id, lease_token=first.lease_token, run_id="old")
    store.mark_target_dispatch(execution.execution_id, lease_token=second.lease_token, run_id="new")
    with pytest.raises(AutomationConflictError):
        store.mark_target_dispatch(execution.execution_id, lease_token=second.lease_token, run_id="new")


@pytest.mark.asyncio
async def test_unknown_failure_registry_reconciliation_is_idempotent(tmp_path):
    store, execution = make_execution(tmp_path)
    registry = RunRegistry()

    async def target(*args):
        raise ValueError("private response")

    worker = AutomationCoordinator(store, registry, target)
    await worker.run_once()
    run = (await registry.list_runs())[0]
    checkpoints = await registry.list_checkpoints(run.run_id)
    await worker.run_once()
    await worker.run_once()
    assert await registry.list_checkpoints(run.run_id) == checkpoints
    assert "private response" not in repr(checkpoints)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["update_run", "record_checkpoint"])
async def test_unknown_result_registry_failure_does_not_redispatch(tmp_path, monkeypatch, method):
    store, execution = make_execution(tmp_path)
    registry = RunRegistry()
    original = getattr(registry, method)

    async def broken(*args, **kwargs):
        if store.get_execution(execution.execution_id).status == "dead_letter":
            raise OSError("synthetic registry failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(registry, method, broken)
    calls = []

    async def target(*args):
        calls.append("sent")
        raise ValueError("unknown")

    worker = AutomationCoordinator(store, registry, target)
    with pytest.raises(OSError):
        await worker.run_once()
    monkeypatch.setattr(registry, method, original)
    await worker.run_once()
    run = (await registry.list_runs())[0]
    checkpoints = await registry.list_checkpoints(run.run_id)
    assert sum(item.event_type == "automation.dead_letter" for item in checkpoints) == 1
    await worker.run_once()
    assert await registry.list_checkpoints(run.run_id) == checkpoints
    assert calls == ["sent"]


@pytest.mark.parametrize("receipt", [None, {}, "started", {"version": 1, "attempt": 99, "phase": "prepared"}, {"version": True, "attempt": 1, "phase": "prepared"}])
def test_legacy_or_invalid_dispatch_receipt_cannot_prove_no_dispatch(tmp_path, receipt):
    store, execution = make_execution(tmp_path)
    claim = store.claim_execution(execution.execution_id, worker_id="old")
    payload = json.loads(store.snapshot_path.read_text("utf-8"))
    payload["executions"][0]["metadata"]["target_dispatch"] = receipt
    store.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    restored = AutomationStore(tmp_path)
    with pytest.raises(AutomationConflictError):
        restored.claim_execution(execution.execution_id, worker_id="new", now=claim.lease_expires_at + 1)
    settled = restored.reconcile_uncertain_dispatches(now=claim.lease_expires_at + 1)
    assert len(settled) == 1 and settled[0].error == "AUTOMATION_DISPATCH_UNCERTAIN"
    assert restored.claimable(now=10**12) == []


@pytest.mark.asyncio
async def test_crash_between_dispatch_marker_and_target_entry_stops_conservatively(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    claim = store.claim_execution(execution.execution_id, worker_id="first")
    store.mark_target_dispatch(execution.execution_id, lease_token=claim.lease_token, run_id="run")
    restored = AutomationStore(tmp_path)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: claim.lease_expires_at + 1))

    async def forbidden(*args):
        pytest.fail("unknown boundary cannot be guessed as undispatched")

    assert await AutomationCoordinator(restored, RunRegistry(), forbidden).run_once() == 0
    assert restored.get_execution(execution.execution_id).error == "AUTOMATION_DISPATCH_UNCERTAIN"


@pytest.mark.asyncio
async def test_pre_dispatch_failure_still_has_bounded_retry(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    registry = RunRegistry()
    checkpoint = registry.record_checkpoint

    async def broken(*args, **kwargs):
        if kwargs.get("event_type") == "automation.started":
            raise OSError("synthetic local failure")
        return await checkpoint(*args, **kwargs)

    monkeypatch.setattr(registry, "record_checkpoint", broken)
    calls = []

    async def target(*args):
        calls.append("called once")
        return AutomationTargetResult("done", "run", "task")

    worker = AutomationCoordinator(store, registry, target)
    assert await worker.run_once() == 0 and calls == []
    pending = store.get_execution(execution.execution_id)
    assert pending.status == "pending"
    monkeypatch.setattr(registry, "record_checkpoint", checkpoint)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: pending.available_at + 1))
    assert await worker.run_once() == 1
    assert calls == ["called once"]
    assert store.get_execution(execution.execution_id).metadata["target_dispatch"]["phase"] == "settled"
