"""Automation cancellation authority and its local execution projection."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace

import pytest
import httpx
from fastapi import FastAPI

from server.xpert_runtime import AutomationConflictError, AutomationCoordinator, AutomationStore, AutomationTargetResult, RunRegistry
from server.xpert_runtime import automation_api


def setup(tmp_path):
    store = AutomationStore(tmp_path)
    definition = store.create_definition(
        name="自动化取消验收", prompt="仅合成测试", target_xpert_id="reader",
        target_xpert_slug="reader", target_xpert_version=1,
        trigger={"type": "interval", "interval_seconds": 60},
    )
    return store, store.run_now(definition.automation_id)


def test_cancel_persistence_failure_does_not_publish_uncommitted_decision(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    store.claim_execution(execution.execution_id, worker_id="owner")
    before = asdict(store.get_execution(execution.execution_id))

    def unavailable():
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(store, "_persist_unlocked", unavailable)
    with pytest.raises(OSError):
        store.cancel_execution(execution.execution_id)
    assert asdict(store.get_execution(execution.execution_id)) == before
    assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == before


@pytest.mark.asyncio
async def test_cancel_api_stops_local_target_without_waiting_for_lease(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def target(*args):
        entered.set()
        try:
            await release.wait()
            return AutomationTargetResult("late", "run", "task")
        finally:
            stopped.set()

    worker = AutomationCoordinator(store, registry, target, lease_seconds=120)
    monkeypatch.setattr(automation_api, "_store", store)
    monkeypatch.setattr(automation_api, "_coordinator", worker)
    operation = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        result = await automation_api.cancel_automation_execution(execution.execution_id)
        assert result["status"] == "cancelled"
        await asyncio.wait_for(stopped.wait(), 0.5)
        assert (await registry.list_runs(run_type="automation"))[0].status == "cancelled"
    finally:
        release.set()
        await asyncio.wait_for(operation, 3)


@pytest.mark.asyncio
async def test_reloaded_cancellation_projects_existing_registry_without_dispatch(tmp_path):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    run = await registry.create_run(
        "automation", "原执行", status="running", source_id=execution.automation_id,
        metadata={"automation_execution_id": execution.execution_id},
    )
    store.cancel_execution(execution.execution_id)

    async def forbidden(*args):
        pytest.fail("cancelled occurrence dispatched")

    worker = AutomationCoordinator(AutomationStore(tmp_path), registry, forbidden)
    await worker.run_once()
    assert (await registry.get_run(run.run_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_waiting_api_cancel_projects_only_matching_occurrence(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()

    async def target(*args):
        return AutomationTargetResult("", "child-run", "child", waiting_client=True, wait_id="wait")

    worker = AutomationCoordinator(store, registry, target)
    await worker.run_once()
    owned = (await registry.list_runs())[0]
    other = await registry.create_run("automation", "其他发生", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": "other"})
    monkeypatch.setattr(automation_api, "_store", store)
    monkeypatch.setattr(automation_api, "_coordinator", worker)
    await automation_api.cancel_automation_execution(execution.execution_id)
    assert (await registry.get_run(owned.run_id)).status == "cancelled"
    assert (await registry.get_run(other.run_id)).status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "waiting"])
async def test_cancelled_target_that_ignores_signal_cannot_publish_late_outcome(tmp_path, outcome):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    entered, received, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def target(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            received.set()
            await release.wait()
        if outcome == "failure":
            raise ValueError("synthetic late failure")
        return AutomationTargetResult("late", "run", "task", waiting_client=outcome == "waiting", wait_id="wait")

    worker = AutomationCoordinator(store, registry, target)
    operation = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await worker.cancel_execution(execution.execution_id)
        await asyncio.wait_for(received.wait(), 3)
        with pytest.raises(AutomationConflictError):
            await worker.retry_execution(execution.execution_id)
        # Repeated cancellation must not interrupt in-progress target cleanup.
        await worker.cancel_execution(execution.execution_id)
    finally:
        release.set()
    assert await asyncio.wait_for(operation, 3) == 0
    assert store.get_execution(execution.execution_id).status == "cancelled"
    run = (await registry.list_runs())[0]
    assert run.status == "cancelled"
    assert not any(item.event_type in {"automation.completed", "automation.waiting_client", "automation.retry_scheduled"} for item in await registry.list_checkpoints(run.run_id))
    assert worker._targets == {} and worker._active == set()


@pytest.mark.asyncio
async def test_failed_cancel_commit_does_not_stop_target(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def target(*args):
        entered.set()
        try:
            await release.wait()
            return AutomationTargetResult("done", "run", "task")
        finally:
            stopped.set()

    worker = AutomationCoordinator(store, registry, target)
    operation = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(entered.wait(), 3)

    def failed():
        raise OSError("synthetic disk failure")

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(store, "_persist_unlocked", failed)
            with pytest.raises(OSError):
                await worker.cancel_execution(execution.execution_id)
        assert not stopped.is_set()
        assert store.get_execution(execution.execution_id).status == "running"
    finally:
        release.set()
    assert await asyncio.wait_for(operation, 3) == 1
    assert store.get_execution(execution.execution_id).status == "completed"


@pytest.mark.asyncio
async def test_failure_projection_after_cancel_does_not_advertise_retry(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    entered, release = asyncio.Event(), asyncio.Event()

    async def target(*args):
        pytest.fail("pre-dispatch failure must not invoke target")

    original_checkpoint = registry.record_checkpoint

    async def checkpoint(*args, **kwargs):
        if kwargs.get("event_type") == "automation.started":
            raise ValueError("synthetic pre-dispatch failure")
        return await original_checkpoint(*args, **kwargs)

    monkeypatch.setattr(registry, "record_checkpoint", checkpoint)

    worker = AutomationCoordinator(store, registry, target)
    original = worker._record_failure

    async def delayed(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(worker, "_record_failure", delayed)
    operation = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await worker.cancel_execution(execution.execution_id)
    finally:
        release.set()
    await asyncio.wait_for(operation, 3)
    run = (await registry.list_runs())[0]
    assert run.status == "cancelled"
    assert not any(item.event_type == "automation.retry_scheduled" for item in await registry.list_checkpoints(run.run_id))


@pytest.mark.asyncio
async def test_projection_failure_continues_other_runs_and_replays(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    runs = [await registry.create_run("automation", "同发生执行", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id}) for _ in range(2)]
    worker = AutomationCoordinator(store, registry, None)
    original = registry.cancel_run
    list_runs = registry.list_runs

    async def failed_first(**kwargs):
        listed = await list_runs(**kwargs)
        return sorted(listed, key=lambda run: run.run_id != runs[1].run_id)

    async def unavailable(run_id, **kwargs):
        if run_id == runs[1].run_id:
            raise OSError("synthetic registry failure")
        return await original(run_id, **kwargs)

    monkeypatch.setattr(registry, "cancel_run", unavailable)
    monkeypatch.setattr(registry, "list_runs", failed_first)
    with pytest.raises(OSError):
        await worker.cancel_execution(execution.execution_id)
    assert (await registry.get_run(runs[0].run_id)).status == "cancelled"
    monkeypatch.setattr(registry, "cancel_run", original)
    await worker.run_once()
    for run in runs:
        assert (await registry.get_run(run.run_id)).status == "cancelled"


def test_retry_persistence_failure_keeps_cancellation(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    before = asdict(store.cancel_execution(execution.execution_id))

    def unavailable():
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(store, "_persist_unlocked", unavailable)
    with pytest.raises(OSError):
        store.retry_execution(execution.execution_id)
    assert asdict(store.get_execution(execution.execution_id)) == before
    assert asdict(AutomationStore(tmp_path).get_execution(execution.execution_id)) == before


@pytest.mark.asyncio
async def test_retry_requires_previous_cancellation_projection(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    run = await registry.create_run("automation", "旧执行", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id})
    worker = AutomationCoordinator(store, registry, None)
    store.cancel_execution(execution.execution_id)
    original = registry.cancel_run

    async def unavailable(*args, **kwargs):
        raise OSError("synthetic registry failure")

    monkeypatch.setattr(registry, "cancel_run", unavailable)
    with pytest.raises(OSError):
        await worker.retry_execution(execution.execution_id)
    assert store.get_execution(execution.execution_id).status == "cancelled"
    monkeypatch.setattr(registry, "cancel_run", original)
    assert (await worker.retry_execution(execution.execution_id)).status == "pending"
    assert (await registry.get_run(run.run_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_reconciliation_not_limited_by_management_pages(tmp_path):
    store, execution = setup(tmp_path)
    store.cancel_execution(execution.execution_id)
    # Synthetic bulk records exercise query limits, not persistence performance.
    for index in range(1005):
        other = replace(execution, execution_id=f"other-{index}", created_at=execution.created_at + index + 1)
        store._executions[other.execution_id] = other
    assert execution.execution_id not in {item.execution_id for item in store.list_executions(limit=1000)}
    assert [item.execution_id for item in store.cancelled_executions()] == [execution.execution_id]
    registry = RunRegistry()
    owned = await registry.create_run("automation", "取消目标", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id})
    for _ in range(105):
        await registry.create_run("automation", "其他发生", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": "other"})
    # No new occurrences are due in this fixture; ignore synthetic pending items.
    for item in store._executions.values():
        if item.execution_id != execution.execution_id:
            item.status = "completed"
    worker = AutomationCoordinator(store, registry, None)
    await worker.run_once()
    assert (await registry.get_run(owned.run_id)).status == "cancelled"


def test_cancel_and_cancelled_query_are_idempotent_detached(tmp_path):
    store, execution = setup(tmp_path)
    first = store.cancel_execution(execution.execution_id)
    before = asdict(first)
    assert asdict(store.cancel_execution(execution.execution_id)) == before
    first.status = "completed"
    listed = store.cancelled_executions()[0]
    listed.metadata["forged"] = True
    assert asdict(store.get_execution(execution.execution_id)) == before


@pytest.mark.asyncio
async def test_cancel_retry_api_serializes_and_preserves_previous_run(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    count = 0

    async def target(*args):
        nonlocal count
        count += 1
        return AutomationTargetResult("done", "child", "task")

    worker = AutomationCoordinator(store, registry, target)
    old_run = await registry.create_run("automation", "旧执行", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id})
    monkeypatch.setattr(automation_api, "_store", store)
    monkeypatch.setattr(automation_api, "_coordinator", worker)
    await automation_api.cancel_automation_execution(execution.execution_id)
    result = await automation_api.retry_automation_execution(execution.execution_id)
    assert result["status"] == "pending"
    assert await worker.run_once() == 1
    assert count == 1
    assert store.get_execution(execution.execution_id).status == "completed"
    assert (await registry.get_run(old_run.run_id)).status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting", [False, True])
async def test_committed_outcome_projection_serializes_with_cancel(tmp_path, monkeypatch, waiting):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    entered, release = asyncio.Event(), asyncio.Event()
    original = registry.update_run

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    async def target(*args):
        return AutomationTargetResult("done", "child", "task", waiting_approval=waiting, wait_id="approval")

    monkeypatch.setattr(registry, "update_run", delayed)
    worker = AutomationCoordinator(store, registry, target)
    operation = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(entered.wait(), 3)
    cancellation = asyncio.create_task(worker.cancel_execution(execution.execution_id))
    await asyncio.sleep(0)
    assert not cancellation.done()
    release.set()
    await asyncio.wait_for(operation, 3)
    result = await asyncio.wait_for(cancellation, 3)
    assert result.status == ("cancelled" if waiting else "completed")
    assert (await registry.list_runs())[0].status == result.status


@pytest.mark.asyncio
async def test_waiting_completion_after_cancel_is_rejected(tmp_path):
    store, execution = setup(tmp_path)
    registry = RunRegistry()

    async def target(*args):
        return AutomationTargetResult("", "child", "task", waiting_client=True, wait_id="client-request")

    worker = AutomationCoordinator(store, registry, target)
    await worker.run_once()
    await worker.cancel_execution(execution.execution_id)
    with pytest.raises(AutomationConflictError):
        await worker.complete_waiting(execution.execution_id, result="late", target_run_id="child", workflow_task_id="task")
    run = (await registry.list_runs())[0]
    assert run.status == "cancelled"
    assert not any(item.event_type == "automation.resumed.completed" for item in await registry.list_checkpoints(run.run_id))


@pytest.mark.asyncio
async def test_cancel_during_registry_registration_prevents_dispatch(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    entered, release = asyncio.Event(), asyncio.Event()
    original = registry.create_run

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    async def forbidden(*args):
        pytest.fail("cancelled execution dispatched")

    monkeypatch.setattr(registry, "create_run", delayed)
    worker = AutomationCoordinator(store, registry, forbidden)
    operation = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await worker.cancel_execution(execution.execution_id)
    finally:
        release.set()
    assert await asyncio.wait_for(operation, 3) == 0
    assert (await registry.list_runs())[0].status == "cancelled"


@pytest.mark.asyncio
async def test_coordinator_shutdown_does_not_forge_user_cancellation(tmp_path):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def target(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    worker = AutomationCoordinator(store, registry, target)
    worker.start()
    try:
        await asyncio.wait_for(entered.wait(), 3)
    finally:
        await asyncio.wait_for(worker.stop(), 3)
    assert stopped.is_set()
    assert worker._targets == {} and worker._active == set()
    assert store.get_execution(execution.execution_id).status == "running"
    assert (await registry.list_runs())[0].status == "running"


@pytest.mark.asyncio
async def test_http_cancel_retry_and_unknown_id_contract(tmp_path, monkeypatch):
    store, execution = setup(tmp_path)
    registry = RunRegistry()
    worker = AutomationCoordinator(store, registry, None, enabled=False)
    monkeypatch.setattr(automation_api, "_store", store)
    monkeypatch.setattr(automation_api, "_coordinator", worker)
    app = FastAPI()
    app.include_router(automation_api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        base = f"/api/runtime/automation-executions/{execution.execution_id}"
        first = await client.post(base + "/cancel")
        assert first.status_code == 200 and first.json()["status"] == "cancelled"
        assert (await client.post(base + "/cancel")).json() == first.json()
        retried = await client.post(base + "/retry", json={"reset_attempts": True})
        assert retried.status_code == 200 and retried.json()["status"] == "pending"
        assert (await client.post(base + "/retry")).status_code == 409
        assert (await client.post("/api/runtime/automation-executions/unknown/cancel")).status_code == 404
    assert await worker.run_once() == 0
    assert await registry.list_runs() == []
