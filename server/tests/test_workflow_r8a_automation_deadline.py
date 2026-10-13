"""Fixed occurrence deadlines with real Stores and no external calls."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

import server.main as main
import server.xpert_runtime.automation_store as store_module
from server.xpert_runtime import AutomationStore
from server.xpert_runtime.automation_store import AutomationConflictError, AutomationValidationError
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
from server.tests.test_workflow_r8a_automation_children import runtime, child


def start(runtime, monkeypatch):
    parents, _, _, _ = runtime
    definition = parents.create_definition(
        name="固定期限验收", prompt="仅合成数据", target_xpert_id="reader",
        target_xpert_slug="reader", target_xpert_version=1,
        trigger={"type": "interval", "interval_seconds": 60},
        budget={"max_runtime_seconds": 30},
    )
    pending = parents.run_now(definition.automation_id)
    clock = [pending.available_at + 1]
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock[0]))
    claimed = parents.claim_execution(pending.execution_id, worker_id="owner", lease_seconds=120)
    metadata = {"automation_id": claimed.automation_id,
                "automation_execution_id": claimed.execution_id,
                "automation_occurrence_key": claimed.occurrence_key}
    return claimed, metadata, clock


def test_deadline_is_fixed_across_config_change_renewal_and_restart(runtime, monkeypatch):
    parents, _, _, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    assert claimed.runtime_deadline_at == clock[0] + 30
    parents._definitions[claimed.automation_id].budget.max_runtime_seconds = 86400
    clock[0] += 10
    parents.renew_execution(claimed.execution_id, lease_token=claimed.lease_token, lease_seconds=120)
    reloaded = AutomationStore(parents.storage_dir)
    assert reloaded.remaining_runtime(claimed.execution_id) == 20
    assert reloaded.get_execution(claimed.execution_id).runtime_budget_seconds == 30


@pytest.mark.parametrize("kind", ["approval", "client_tool"])
@pytest.mark.asyncio
async def test_waiting_deadline_cancels_child_and_projects_failure_after_restart(runtime, monkeypatch, kind):
    parents, children, registry, live = runtime
    claimed, metadata, clock = start(runtime, monkeypatch)
    saved, run = await child(runtime, metadata, kind)
    parents.mark_waiting(claimed.execution_id, status="waiting_approval" if kind == "approval" else "waiting_client",
                         run_id=run.run_id, workflow_task_id=saved.task_id, wait_id="wait", lease_token=claimed.lease_token)
    parent_run = await registry.create_run("automation", "固定期限", status="waiting", source_id=claimed.automation_id,
                                          metadata={"automation_execution_id": claimed.execution_id, "attempt": claimed.attempt})
    clock[0] += 31
    restored = AutomationStore(parents.storage_dir)
    monkeypatch.setattr(main, "automation_store", restored)
    worker = main.get_automation_coordinator()
    async def forbidden(*args):
        pytest.fail("expired occurrence must not dispatch")
    worker.execute_target = forbidden
    assert await worker.run_once() == 0
    result = restored.get_execution(claimed.execution_id)
    assert result.status == "dead_letter"
    assert result.error == "AUTOMATION_RUNTIME_BUDGET_EXCEEDED"
    assert children.require(saved.task_id).status == "cancelled"
    assert live[saved.task_id]["provider_cancel_event"].is_set()
    assert (await registry.get_run(parent_run.run_id)).status == "failed"
    before = asdict(result)
    await worker.run_once()
    assert asdict(restored.get_execution(claimed.execution_id)) == before


@pytest.mark.parametrize("action", ["dispatch", "complete", "wait", "claim", "retry"])
def test_expired_occurrence_cannot_dispatch_complete_or_reset(runtime, monkeypatch, action):
    parents, _, _, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    if action == "retry":
        parents.cancel_execution(claimed.execution_id)
    clock[0] += 121 if action == "claim" else 31
    with pytest.raises(AutomationConflictError):
        if action == "dispatch":
            parents.mark_target_dispatch(claimed.execution_id, lease_token=claimed.lease_token, run_id="run")
        elif action == "complete":
            parents.complete_execution(claimed.execution_id, result="late", lease_token=claimed.lease_token)
        elif action == "wait":
            parents.mark_waiting(claimed.execution_id, status="waiting_client", run_id="run", workflow_task_id="child",
                                 wait_id="wait", lease_token=claimed.lease_token)
        elif action == "claim":
            parents.claim_execution(claimed.execution_id, worker_id="new")
        else:
            parents.retry_execution(claimed.execution_id, reset_attempts=True)


@pytest.mark.asyncio
async def test_expired_parent_blocks_resume_before_poll(runtime, monkeypatch):
    parents, children, _, _ = runtime
    claimed, metadata, clock = start(runtime, monkeypatch)
    await child(runtime, metadata, "approval")
    clock[0] += 31
    with pytest.raises(WorkflowExecutionConflictError):
        await main._assert_automation_parent_not_cancelled(metadata)
    assert children.require("child").status == "cancelled"
    assert parents.get_execution(claimed.execution_id).status == "dead_letter"


def test_dispatched_expiry_preserves_uncertainty_and_forbids_retry(runtime, monkeypatch):
    parents, _, _, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    parents.mark_target_dispatch(claimed.execution_id, lease_token=claimed.lease_token, run_id="run")
    clock[0] += 31
    result = parents.expire_runtime_budget(claimed.execution_id)
    assert result.error == "AUTOMATION_DISPATCH_UNCERTAIN"
    assert result.runtime_expired_at == clock[0]
    with pytest.raises(AutomationConflictError):
        parents.retry_execution(claimed.execution_id, reset_attempts=True)


def test_expiry_persistence_failure_does_not_fake_a_commit(runtime, monkeypatch):
    parents, _, _, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    clock[0] += 31
    before = asdict(parents.get_execution(claimed.execution_id))
    def fail():
        raise OSError("synthetic save failure")
    monkeypatch.setattr(parents, "_persist_unlocked", fail)
    with pytest.raises(OSError):
        parents.expire_runtime_budget(claimed.execution_id)
    assert asdict(parents.get_execution(claimed.execution_id)) == before
    assert asdict(AutomationStore(parents.storage_dir).get_execution(claimed.execution_id)) == before


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_expiry_never_reopens_terminal(runtime, monkeypatch, terminal):
    parents, _, _, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    if terminal == "completed":
        parents.complete_execution(claimed.execution_id, result="done", lease_token=claimed.lease_token)
    else:
        parents.cancel_execution(claimed.execution_id)
    before = asdict(parents.get_execution(claimed.execution_id))
    clock[0] += 31
    assert asdict(parents.expire_runtime_budget(claimed.execution_id)) == before


@pytest.mark.asyncio
async def test_registry_delay_consumes_budget_before_first_dispatch(runtime, monkeypatch):
    parents, _, registry, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    parents.fail_execution(claimed.execution_id, error="pre-dispatch", lease_token=claimed.lease_token)
    clock[0] += 3
    original = registry.record_checkpoint
    async def slow(*args, **kwargs):
        result = await original(*args, **kwargs)
        clock[0] += 40
        return result
    monkeypatch.setattr(registry, "record_checkpoint", slow)
    async def forbidden(*args):
        pytest.fail("deadline expired during registry IO")
    worker = main.get_automation_coordinator()
    worker.execute_target = forbidden
    assert await worker.run_once() == 0
    assert parents.get_execution(claimed.execution_id).error == "AUTOMATION_RUNTIME_BUDGET_EXCEEDED"


@pytest.mark.parametrize("field,value", [
    ("runtime_budget_seconds", True), ("runtime_budget_seconds", 0),
    ("runtime_deadline_at", None), ("runtime_deadline_at", float("inf")),
    ("runtime_deadline_at", 1), ("runtime_expired_at", 1),
])
def test_invalid_deadline_is_not_loaded_as_empty_store(runtime, monkeypatch, field, value):
    parents, _, _, _ = runtime
    start(runtime, monkeypatch)
    payload = json.loads(parents.snapshot_path.read_text(encoding="utf-8"))
    payload["executions"][0][field] = value
    parents.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AutomationValidationError):
        AutomationStore(parents.storage_dir)
    assert json.loads(parents.snapshot_path.read_text(encoding="utf-8")) == payload


def test_legacy_started_execution_does_not_invent_a_frozen_deadline(runtime, monkeypatch):
    parents, _, _, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    payload = json.loads(parents.snapshot_path.read_text(encoding="utf-8"))
    for field in ("runtime_budget_seconds", "runtime_deadline_at", "runtime_expired_at"):
        payload["executions"][0].pop(field)
    parents.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    restored = AutomationStore(parents.storage_dir)
    clock[0] += 121
    resumed = restored.claim_execution(claimed.execution_id, worker_id="legacy")
    assert resumed.started_at == claimed.started_at
    assert restored.remaining_runtime(claimed.execution_id) is None
    assert restored.runtime_expiry_candidates() == []


@pytest.mark.asyncio
async def test_live_target_is_cancelled_without_cancelling_coordinator(runtime, monkeypatch):
    parents, _, _, _ = runtime
    claimed, _, clock = start(runtime, monkeypatch)
    parents.fail_execution(claimed.execution_id, error="pre-dispatch", lease_token=claimed.lease_token)
    clock[0] += 3
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def target(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    worker = main.get_automation_coordinator()
    worker.execute_target = target
    operation = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        clock[0] += 30
        await worker.run_once()
        assert await asyncio.wait_for(operation, 3) == 0
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
    assert stopped.is_set()
    assert parents.get_execution(claimed.execution_id).error == "AUTOMATION_DISPATCH_UNCERTAIN"


@pytest.mark.asyncio
async def test_child_projection_failure_is_reconciled_without_replay(runtime, monkeypatch):
    parents, children, _, _ = runtime
    claimed, metadata, clock = start(runtime, monkeypatch)
    await child(runtime, metadata, "approval")
    await child(runtime, {**metadata, "automation_occurrence_key": "unrelated"}, task_id="unrelated")
    original = children.cancel
    def fail(*args, **kwargs):
        raise OSError("synthetic child failure")
    monkeypatch.setattr(children, "cancel", fail)
    clock[0] += 31
    worker = main.get_automation_coordinator()
    assert await worker.run_once() == 0
    assert parents.get_execution(claimed.execution_id).status == "dead_letter"
    assert children.require("child").status == "waiting"
    monkeypatch.setattr(children, "cancel", original)
    assert await worker.run_once() == 0
    assert children.require("child").status == "cancelled"
    assert children.require("unrelated").status == "running"


@pytest.mark.asyncio
async def test_uncertain_lease_loss_still_propagates_later_deadline(runtime, monkeypatch):
    parents, children, _, _ = runtime
    claimed, metadata, clock = start(runtime, monkeypatch)
    parents.mark_target_dispatch(claimed.execution_id, lease_token=claimed.lease_token, run_id="parent-run")
    await child(runtime, metadata, "approval")
    # A ten-second lease loss precedes the thirty-second operation deadline.
    parents._executions[claimed.execution_id].lease_expires_at = clock[0] + 10
    clock[0] += 11
    uncertain = parents.reconcile_uncertain_dispatches()[0]
    assert uncertain.error == "AUTOMATION_DISPATCH_UNCERTAIN"
    clock[0] += 20
    await main.get_automation_coordinator().run_once()
    current = parents.get_execution(claimed.execution_id)
    assert current.error == uncertain.error and current.status == uncertain.status
    assert current.completed_at == uncertain.completed_at
    assert current.runtime_expired_at == clock[0]
    assert children.require("child").status == "cancelled"
