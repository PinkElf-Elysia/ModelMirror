from __future__ import annotations

import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from server.xpert_runtime import (
    AutomationConflictError,
    AutomationCoordinator,
    AutomationStore,
    AutomationTargetResult,
    RunRegistry,
)
import server.xpert_runtime.automation_store as store_module


@pytest.fixture(autouse=True)
def store_clock(monkeypatch):
    # Lease/identity tests advance only the Store clock, not asyncio deadlines.
    clock = SimpleNamespace(now=1_800_000_000.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock.now))
    return clock


def make_execution(tmp_path):
    store = AutomationStore(tmp_path)
    definition = store.create_definition(
        name="隔离恢复验收",
        prompt="合成测试，不调用外部服务。",
        target_xpert_id="synthetic-xpert",
        target_xpert_slug="synthetic",
        target_xpert_version=1,
        trigger={"type": "interval", "interval_seconds": 60},
    )
    return store, store.run_now(definition.automation_id)


@pytest.mark.parametrize("reload_store", [False, True])
@pytest.mark.parametrize("explicit_now", [False, True])
@pytest.mark.parametrize("offset", [-0.001, 0.0, 0.001])
def test_claim_due_boundary_is_exact_and_rejection_is_read_only(
    tmp_path, store_clock, reload_store, explicit_now, offset,
):
    store, execution = make_execution(tmp_path)
    if reload_store:
        store = AutomationStore(tmp_path)
    observed_at = execution.available_at + offset
    store_clock.now = observed_at
    kwargs = {"now": observed_at} if explicit_now else {}
    before = asdict(store.get_execution(execution.execution_id))
    disk_before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    assert disk_before
    if offset < 0:
        assert store.claimable(**kwargs) == []
        with pytest.raises(AutomationConflictError) as captured:
            store.claim_execution(execution.execution_id, worker_id="owner", **kwargs)
        assert asdict(store.get_execution(execution.execution_id)) == before
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == disk_before
        assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == before
        assert "AUTOMATION_NOT_DUE" in str(captured.value)
        assert f"available_at={execution.available_at!r}" in str(captured.value)
        assert f"observed_at={observed_at!r}" in str(captured.value)
    else:
        assert [item.execution_id for item in store.claimable(**kwargs)] == [execution.execution_id]
        claim = store.claim_execution(execution.execution_id, worker_id="owner", **kwargs)
        assert claim.status == "running"
        assert claim.attempt == 1
        assert claim.started_at == observed_at
        assert claim.lease_expires_at == observed_at + 60
        assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == asdict(claim)


@pytest.mark.parametrize("reload_store", [False, True])
@pytest.mark.parametrize("offset", [-0.001, 0.0, 0.001])
def test_prepared_lease_takeover_requires_exact_expiry(
    tmp_path, store_clock, reload_store, offset,
):
    store, execution = make_execution(tmp_path)
    first = store.claim_execution(execution.execution_id, worker_id="old")
    if reload_store:
        store = AutomationStore(tmp_path)
    store_clock.now = first.lease_expires_at + offset
    before = asdict(store.get_execution(execution.execution_id))
    disk_before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    if offset < 0:
        assert store.claimable() == []
        with pytest.raises(AutomationConflictError, match="already leased"):
            store.claim_execution(execution.execution_id, worker_id="new")
        assert asdict(store.get_execution(execution.execution_id)) == before
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == disk_before
    else:
        assert [item.execution_id for item in store.claimable()] == [execution.execution_id]
        second = store.claim_execution(execution.execution_id, worker_id="new")
        assert second.attempt == 2
        assert second.lease_token != first.lease_token
        with pytest.raises(AutomationConflictError):
            store.complete_execution(execution.execution_id, result="late", lease_token=first.lease_token)
        assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == asdict(second)


@pytest.mark.parametrize("reload_store", [False, True])
@pytest.mark.asyncio
async def test_clock_rewind_after_scan_defers_without_dispatch_or_state_change(
    tmp_path, monkeypatch, store_clock, reload_store,
):
    store, execution = make_execution(tmp_path)
    if reload_store:
        store = AutomationStore(tmp_path)
    before = asdict(store.get_execution(execution.execution_id))
    disk_before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    registry = RunRegistry()
    calls = []

    async def target(*args):
        calls.append(execution.execution_id)
        return AutomationTargetResult(output="done", run_id="child", workflow_task_id="task")

    scan = store.claimable

    def scan_then_rewind(**kwargs):
        candidates = scan(**kwargs)
        store_clock.now = execution.available_at - 0.001
        return candidates

    monkeypatch.setattr(store, "claimable", scan_then_rewind)
    coordinator = AutomationCoordinator(store, registry, target)
    assert await coordinator.run_once() == 0
    assert calls == []
    assert await registry.list_runs(run_type="automation") == []
    assert coordinator._active == set()
    assert asdict(store.get_execution(execution.execution_id)) == before
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == disk_before
    monkeypatch.setattr(store, "claimable", scan)
    store_clock.now = execution.available_at
    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert calls == [execution.execution_id]
    assert store.get_execution(execution.execution_id).status == "completed"
    assert store.get_execution(execution.execution_id).attempt == 1


@pytest.mark.parametrize("transition", ["wait", "complete", "fail"])
def test_late_callback_cannot_revive_cancelled_execution(tmp_path, transition):
    store, execution = make_execution(tmp_path)
    store.claim_execution(execution.execution_id, worker_id="old")
    store.cancel_execution(execution.execution_id)
    before = asdict(store.get_execution(execution.execution_id))
    with pytest.raises(AutomationConflictError):
        if transition == "wait":
            store.mark_waiting(
                execution.execution_id, status="waiting_client",
                run_id="child-run", workflow_task_id="child-task", wait_id="wait",
            )
        elif transition == "complete":
            store.complete_execution(execution.execution_id, result="late")
        else:
            store.fail_execution(execution.execution_id, error="late")
    assert asdict(store.get_execution(execution.execution_id)) == before
    assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == before


@pytest.mark.parametrize("outcome", ["success", "wait", "error", "timeout"])
@pytest.mark.parametrize("interruption", ["cancel", "takeover", "expire"])
@pytest.mark.asyncio
async def test_late_worker_cannot_commit_or_record_success(
    tmp_path, monkeypatch, outcome, interruption,
):
    store, execution = make_execution(tmp_path)
    now = [execution.available_at + 1]
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: now[0]))
    entered, release = asyncio.Event(), asyncio.Event()
    registry = RunRegistry()
    calls = []

    async def target(_definition, claimed, _parent):
        calls.append(claimed.execution_id)
        entered.set()
        await release.wait()
        if outcome == "error":
            raise ValueError("合成失败")
        if outcome == "timeout":
            raise asyncio.TimeoutError()
        return AutomationTargetResult(
            output="合成结果", run_id="child-run", workflow_task_id="child-task",
            waiting_client=outcome == "wait", wait_id="wait" if outcome == "wait" else None,
        )

    coordinator = AutomationCoordinator(store, registry, target, lease_seconds=10)
    running = asyncio.create_task(coordinator.run_once())
    await asyncio.wait_for(entered.wait(), timeout=5)
    try:
        if interruption == "cancel":
            store.cancel_execution(execution.execution_id)
        else:
            now[0] += 20
            if interruption == "takeover":
                with pytest.raises(AutomationConflictError):
                    store.claim_execution(execution.execution_id, worker_id="new", lease_seconds=30)
        before = asdict(store.get_execution(execution.execution_id))
    finally:
        release.set()
    assert await running == 0
    assert calls == [execution.execution_id]
    assert asdict(store.get_execution(execution.execution_id)) == before
    assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == before
    runs = await registry.list_runs(run_type="automation")
    checkpoints = await registry.list_checkpoints(runs[0].run_id)
    assert not any(item.event_type in {
        "automation.completed", "automation.waiting_client", "automation.failed",
    } for item in checkpoints)


def test_claim_receipt_and_public_reads_are_detached(tmp_path):
    store, execution = make_execution(tmp_path)
    claimed = store.claim_execution(execution.execution_id, worker_id="old")
    token = claimed.lease_token
    store.claim_execution(
        execution.execution_id, worker_id="new", now=claimed.lease_expires_at + 1,
    )
    assert claimed.lease_token == token
    current = asdict(store.get_execution(execution.execution_id))
    exposed = [execution, claimed, store.get_execution(execution.execution_id),
               store.list_executions()[0], store.claimable(now=10**12)[0]]
    for item in exposed:
        item.status = "completed"
        item.metadata["forged"] = True
    assert asdict(store.get_execution(execution.execution_id)) == current


@pytest.mark.parametrize("transition", ["wait", "complete", "fail"])
@pytest.mark.parametrize("credential", ["missing", "wrong", "expired"])
def test_running_commits_require_current_live_lease(tmp_path, monkeypatch, transition, credential):
    store, execution = make_execution(tmp_path)
    claimed = store.claim_execution(execution.execution_id, worker_id="owner")
    token = {"missing": None, "wrong": "forged", "expired": claimed.lease_token}[credential]
    if credential == "expired":
        monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: claimed.lease_expires_at))
    before = asdict(store.get_execution(execution.execution_id))
    with pytest.raises(AutomationConflictError):
        if transition == "wait":
            store.mark_waiting(
                execution.execution_id, status="waiting_approval", wait_id="wait",
                run_id="run", workflow_task_id="task", lease_token=token,
            )
        elif transition == "complete":
            store.complete_execution(execution.execution_id, result="late", lease_token=token)
        else:
            store.fail_execution(execution.execution_id, error="late", lease_token=token)
    assert asdict(store.get_execution(execution.execution_id)) == before


@pytest.mark.parametrize("transition", ["wait", "complete", "fail"])
def test_completed_cannot_be_overwritten(tmp_path, transition):
    store, execution = make_execution(tmp_path)
    claimed = store.claim_execution(execution.execution_id, worker_id="owner")
    store.complete_execution(execution.execution_id, result="original", lease_token=claimed.lease_token)
    before = asdict(store.get_execution(execution.execution_id))
    with pytest.raises(AutomationConflictError):
        if transition == "wait":
            store.mark_waiting(
                execution.execution_id, status="waiting_client", wait_id="wait",
                run_id="run", workflow_task_id="task", lease_token=claimed.lease_token,
            )
        elif transition == "complete":
            store.complete_execution(execution.execution_id, result="late", lease_token=claimed.lease_token)
        else:
            store.fail_execution(execution.execution_id, error="late", lease_token=claimed.lease_token)
    assert asdict(store.cancel_execution(execution.execution_id)) == before
    assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == before


@pytest.mark.asyncio
async def test_cancel_before_dispatch_does_not_call_target(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    registry = RunRegistry()
    original = registry.record_checkpoint

    async def checkpoint(*args, **kwargs):
        result = await original(*args, **kwargs)
        store.cancel_execution(execution.execution_id)
        return result

    monkeypatch.setattr(registry, "record_checkpoint", checkpoint)

    async def target(*args):
        pytest.fail("cancelled execution dispatched")

    assert await AutomationCoordinator(store, registry, target).run_once() == 0
    assert store.get_execution(execution.execution_id).status == "cancelled"


@pytest.mark.parametrize("identity", ["run", "task", "missing_run", "missing_task"])
@pytest.mark.asyncio
async def test_waiting_completion_rejects_different_child(tmp_path, identity):
    store, execution = make_execution(tmp_path)
    claimed = store.claim_execution(execution.execution_id, worker_id="owner")
    store.mark_waiting(
        execution.execution_id, status="waiting_client", wait_id="wait",
        run_id="run", workflow_task_id="task", lease_token=claimed.lease_token,
    )
    before = asdict(store.get_execution(execution.execution_id))

    async def target(*args):
        pytest.fail("resume must not dispatch")

    registry = RunRegistry()
    coordinator = AutomationCoordinator(store, registry, target)
    with pytest.raises(AutomationConflictError):
        await coordinator.complete_waiting(
            execution.execution_id, result="wrong child",
            target_run_id={"run": "forged", "missing_run": ""}.get(identity, "run"),
            workflow_task_id={"task": "forged", "missing_task": ""}.get(identity, "task"),
        )
    assert asdict(store.get_execution(execution.execution_id)) == before
    assert await registry.list_runs(run_type="automation") == []


def test_new_owner_can_commit_but_old_receipt_remains_invalid(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    first = store.claim_execution(execution.execution_id, worker_id="old")
    now = first.lease_expires_at + 1
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: now))
    store = AutomationStore(tmp_path)
    second = store.claim_execution(execution.execution_id, worker_id="new")
    assert first.lease_token != second.lease_token
    with pytest.raises(AutomationConflictError):
        store.complete_execution(execution.execution_id, result="old", lease_token=first.lease_token)
    committed = store.complete_execution(execution.execution_id, result="new", lease_token=second.lease_token)
    assert committed.status == "completed"
    assert committed.result == "new"
    committed.result = "forged"
    assert AutomationStore(tmp_path).get_execution(execution.execution_id).result == "new"


@pytest.mark.asyncio
async def test_cancelled_wait_cannot_be_completed_or_reported(tmp_path):
    store, execution = make_execution(tmp_path)
    registry = RunRegistry()

    async def target(*args):
        return AutomationTargetResult(
            output="", run_id="run", workflow_task_id="task",
            waiting_client=True, wait_id="request",
        )

    coordinator = AutomationCoordinator(store, registry, target)
    assert await coordinator.run_once() == 1
    store.cancel_execution(execution.execution_id)
    before = asdict(store.get_execution(execution.execution_id))
    with pytest.raises(AutomationConflictError):
        await coordinator.complete_waiting(
            execution.execution_id, result="late", target_run_id="run", workflow_task_id="task",
        )
    assert asdict(store.get_execution(execution.execution_id)) == before
    run = (await registry.list_runs(run_type="automation"))[0]
    assert run.status != "completed"
    assert not any(item.event_type == "automation.resumed.completed"
                   for item in await registry.list_checkpoints(run.run_id))


@pytest.mark.parametrize("state", ["expired", "cancelled", "reclaimed", "completed"])
def test_renewal_cannot_revive_lost_ownership(tmp_path, monkeypatch, state):
    store, execution = make_execution(tmp_path)
    claimed = store.claim_execution(execution.execution_id, worker_id="old")
    if state in {"expired", "reclaimed"}:
        now = claimed.lease_expires_at + 1
        monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: now))
        if state == "reclaimed":
            store.claim_execution(execution.execution_id, worker_id="new")
    elif state == "cancelled":
        store.cancel_execution(execution.execution_id)
    else:
        store.complete_execution(execution.execution_id, result="done", lease_token=claimed.lease_token)
    before = asdict(store.get_execution(execution.execution_id))
    with pytest.raises(AutomationConflictError):
        store.renew_execution(execution.execution_id, lease_token=claimed.lease_token, lease_seconds=30)
    assert asdict(store.get_execution(execution.execution_id)) == before


@pytest.mark.asyncio
async def test_long_target_renews_without_extra_dispatch(tmp_path, monkeypatch):
    store, execution = make_execution(tmp_path)
    now = [execution.available_at + 1]
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: now[0]))
    entered, release, renewed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_renew = store.renew_execution
    calls = []

    def renew(*args, **kwargs):
        result = original_renew(*args, **kwargs)
        renewed.set()
        return result

    monkeypatch.setattr(store, "renew_execution", renew)

    async def target(*args):
        calls.append("dispatch")
        entered.set()
        await release.wait()
        return AutomationTargetResult(output="done", run_id="run", workflow_task_id="task")

    coordinator = AutomationCoordinator(store, RunRegistry(), target, lease_seconds=10)
    running = asyncio.create_task(coordinator.run_once())
    await asyncio.wait_for(entered.wait(), timeout=5)
    first_expiry = store.get_execution(execution.execution_id).lease_expires_at
    try:
        now[0] += 6
        await asyncio.wait_for(renewed.wait(), timeout=5)
        assert store.get_execution(execution.execution_id).lease_expires_at > first_expiry
        now[0] += 6
        assert now[0] > first_expiry
    finally:
        release.set()
    assert await running == 1
    assert calls == ["dispatch"]
    assert store.get_execution(execution.execution_id).status == "completed"


@pytest.mark.parametrize("interruption", ["cancel", "takeover"])
@pytest.mark.asyncio
async def test_renewal_loss_stops_local_target_without_committing(tmp_path, monkeypatch, interruption):
    store, execution = make_execution(tmp_path)
    now = [execution.available_at + 1]
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: now[0]))
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def target(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    coordinator = AutomationCoordinator(store, RunRegistry(), target, lease_seconds=10)
    running = asyncio.create_task(coordinator.run_once())
    await asyncio.wait_for(entered.wait(), timeout=5)
    if interruption == "cancel":
        store.cancel_execution(execution.execution_id)
    else:
        now[0] += 20
        with pytest.raises(AutomationConflictError):
            store.claim_execution(execution.execution_id, worker_id="new")
    before = asdict(store.get_execution(execution.execution_id))
    assert await asyncio.wait_for(running, timeout=5) == 0
    assert stopped.is_set()
    assert asdict(store.get_execution(execution.execution_id)) == before
    assert coordinator._active == set()
