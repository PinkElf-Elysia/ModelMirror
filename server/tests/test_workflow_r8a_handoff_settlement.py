"""Deterministic ownership/settlement races, without provider requests."""
import asyncio

import pytest

from server.xpert_runtime.agent_tasks import AgentTaskStore, HandoffOwnershipError
from server.xpert_runtime.handoff_executor import HandoffExecutor, HandoffExecutionResult, HandoffPermanentError
from server.xpert_runtime.run_registry import RunRegistry


async def setup(tmp_path):
    store = AgentTaskStore(storage_dir=tmp_path / "tasks")
    registry = RunRegistry()
    task = await store.create_task("合成任务", "合成输入")
    handoff = await store.create_handoff(task.task_id, "parent", "xpert:target", "合成委托",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True})
    return store, registry, task, handoff


def result(kind):
    if kind == "failure":
        raise HandoffPermanentError("合成失败")
    if kind == "retry":
        raise RuntimeError("合成暂时错误")
    return HandoffExecutionResult("合成结果", "child-run", "target", "target", 1,
        waiting_approval=kind == "approval", waiting_client=kind == "client",
        approval_id="approval", client_request_id="request", task_id="child")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["success", "failure", "retry", "approval", "client"])
@pytest.mark.parametrize("fault", ["replacement", "expired", "cancel"])
async def test_late_callback_cannot_settle_new_owner_or_cancel(tmp_path, kind, fault):
    store, registry, task, handoff = await setup(tmp_path)

    async def target(*_args):
        if fault == "cancel":
            await store.cancel_task(task.task_id, "合成取消")
        else:
            await store.update_handoff_metadata(handoff.handoff_id, {
                "lease_token": "replacement"} if fault == "replacement" else {"lease_expires_at": 1.0})
        return result(kind)

    executor = HandoffExecutor(store, registry, target)
    await executor.execute_handoff(handoff.handoff_id)
    current = await store.get_handoff(handoff.handoff_id)
    parent = await store.get_task(task.task_id)
    assert current.status == ("dead_letter" if fault == "cancel" else "accepted")
    assert parent.status == ("cancelled" if fault == "cancel" else "running")
    assert parent.result is None
    if fault == "replacement":
        assert current.metadata["lease_token"] == "replacement"
    for run in await registry.list_runs(limit=20):
        assert run.status == ("cancelled" if fault == "cancel" else "running")
        events = await registry.list_checkpoints(run.run_id)
        assert not any(e.event_type in {"agent_handoff.completed", "agent_handoff.dead_letter",
            "agent_handoff.retry_scheduled", "agent_handoff.waiting_approval", "agent_handoff.waiting_client"}
            for e in events)
    reopened = AgentTaskStore(storage_dir=store.storage_dir)
    assert (await reopened.get_task(task.task_id)).status == parent.status
    assert (await reopened.get_handoff(handoff.handoff_id)).status == current.status


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["success", "failure", "retry", "approval", "client"])
@pytest.mark.parametrize("order", ["cancel_first", "settle_first"])
async def test_cancel_at_atomic_commit_boundary(tmp_path, monkeypatch, kind, order):
    store, registry, task, handoff = await setup(tmp_path)
    settle = store.settle_claimed_handoff

    async def racing(claimed, status, **kwargs):
        if status == "accepted":
            return await settle(claimed, status, **kwargs)
        if order == "cancel_first":
            await store.cancel_task(task.task_id, "合成取消")
        saved = await settle(claimed, status, **kwargs)
        if order == "settle_first":
            await store.cancel_task(task.task_id, "合成取消")
        return saved

    async def target(*_args):
        return result(kind)

    monkeypatch.setattr(store, "settle_claimed_handoff", racing)
    executor = HandoffExecutor(store, registry, target)
    await executor.execute_handoff(handoff.handoff_id)
    final = ("completed" if kind == "success" else "failed") if order == "settle_first" and kind in {"success", "failure"} else "cancelled"
    assert (await store.get_task(task.task_id)).status == final
    assert (await store.get_handoff(handoff.handoff_id)).status == ("completed" if final == "completed" else "dead_letter")
    assert all(run.status == final for run in await registry.list_runs(limit=20))
    reopened = AgentTaskStore(storage_dir=store.storage_dir)
    assert (await reopened.get_task(task.task_id)).status == final


@pytest.mark.asyncio
@pytest.mark.parametrize("old_kind", ["success", "failure", "retry", "approval", "client"])
async def test_real_takeover_finishes_before_old_callback(tmp_path, old_kind):
    store, registry, task, handoff = await setup(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    async def old(*_args):
        started.set()
        await release.wait()
        return result(old_kind)

    async def new(*_args):
        return HandoffExecutionResult("新持有者结果", "new-run", "target", "target", 1)

    old_executor = HandoffExecutor(store, registry, old, worker_id="old")
    pending = asyncio.create_task(old_executor.execute_handoff(handoff.handoff_id))
    await asyncio.wait_for(started.wait(), 2)
    await store.update_handoff_metadata(handoff.handoff_id, {"lease_expires_at": 1.0})
    await HandoffExecutor(store, registry, new, worker_id="new").execute_handoff(handoff.handoff_id)
    before = store.storage_path.read_bytes()
    release.set()
    await asyncio.wait_for(pending, 2)
    assert store.storage_path.read_bytes() == before
    assert (await store.get_task(task.task_id)).result == "新持有者结果"
    assert all(run.status == "completed" for run in await registry.list_runs(limit=20))
    run = (await registry.list_runs(run_type="agent_handoff", source_id=handoff.handoff_id))[0]
    checkpoints = await registry.list_checkpoints(run.run_id)
    assert sum(c.event_type == "agent_handoff.completed" for c in checkpoints) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("after_replace", [False, True])
async def test_persist_fault_does_not_publish_or_retry_uncertain_settlement(tmp_path, monkeypatch, after_replace):
    store, registry, task, handoff = await setup(tmp_path)
    persist = store._persist_unlocked

    def broken():
        if after_replace:
            persist()
        raise OSError("合成持久化中断")

    async def target(*_args):
        monkeypatch.setattr(store, "_persist_unlocked", broken)
        return result("success")

    with pytest.raises(OSError):
        await HandoffExecutor(store, registry, target).execute_handoff(handoff.handoff_id)
    assert (await store.get_handoff(handoff.handoff_id)).status == "accepted"
    assert (await store.get_task(task.task_id)).status == "running"
    assert all(run.status == "running" for run in await registry.list_runs(limit=20))
    with pytest.raises(HandoffOwnershipError):
        await store.claim_handoff(handoff.handoff_id, worker_id="forbidden")
    monkeypatch.setattr(store, "_persist_unlocked", persist)
    disk = store.storage_path.read_bytes()
    with pytest.raises(HandoffOwnershipError):
        await store.cancel_task(task.task_id, "不应覆盖不确定提交")
    assert store.storage_path.read_bytes() == disk
    reopened = AgentTaskStore(storage_dir=store.storage_dir)
    assert (await reopened.get_handoff(handoff.handoff_id)).status == ("completed" if after_replace else "accepted")
    assert (await reopened.get_task(task.task_id)).status == ("completed" if after_replace else "running")


@pytest.mark.asyncio
async def test_commit_then_projection_failure_recovers_without_target_replay(tmp_path, monkeypatch):
    store, registry, task, handoff = await setup(tmp_path)
    calls = []

    async def target(*_args):
        calls.append("only")
        return result("success")

    executor = HandoffExecutor(store, registry, target)
    project = executor._project_current

    async def fail_terminal(handoff_id, **kwargs):
        if not kwargs:
            raise RuntimeError("合成投影中断")
        return await project(handoff_id, **kwargs)

    monkeypatch.setattr(executor, "_project_current", fail_terminal)
    with pytest.raises(RuntimeError, match="合成投影中断"):
        await executor.execute_handoff(handoff.handoff_id)
    reopened = AgentTaskStore(storage_dir=store.storage_dir)
    recovery = HandoffExecutor(reopened, registry, target)
    await recovery._project_current(handoff.handoff_id)
    run = (await registry.list_runs(run_type="agent_handoff", source_id=handoff.handoff_id))[0]
    await registry.record_checkpoint(run.run_id, event_type="unrelated", title="合成独立观测")
    await recovery._project_current(handoff.handoff_id)
    assert calls == ["only"]
    assert all(run.status == "completed" for run in await registry.list_runs(limit=20))
    run = (await registry.list_runs(run_type="agent_handoff", source_id=handoff.handoff_id))[0]
    assert sum(c.event_type == "agent_handoff.completed" for c in await registry.list_checkpoints(run.run_id)) == 1


@pytest.mark.asyncio
async def test_cancel_waits_for_local_projection_then_authority_wins(tmp_path, monkeypatch):
    store, registry, task, handoff = await setup(tmp_path)
    claimed = await store.claim_handoff(handoff.handoff_id, worker_id="one")
    await store.settle_claimed_handoff(claimed, "waiting_client")
    entered, release = asyncio.Event(), asyncio.Event()
    update = registry.update_run

    async def delayed(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        return await update(*args, **kwargs)

    monkeypatch.setattr(registry, "update_run", delayed)
    executor = HandoffExecutor(store, registry, None)
    projection = asyncio.create_task(executor._project_current(handoff.handoff_id))
    await asyncio.wait_for(entered.wait(), 2)
    cancel = asyncio.create_task(store.cancel_task(task.task_id, "合成取消"))
    await asyncio.sleep(0)
    assert not cancel.done()
    release.set()
    await asyncio.wait_for(projection, 2)
    await asyncio.wait_for(cancel, 2)
    await executor._project_current(handoff.handoff_id)
    assert all(run.status == "cancelled" for run in await registry.list_runs(limit=20))


@pytest.mark.asyncio
async def test_claim_attempt_limit_is_not_authority_to_settle(tmp_path):
    store, registry, task, handoff = await setup(tmp_path)
    await store.claim_handoff(handoff.handoff_id, worker_id="owner", max_attempts=1)
    await store.update_handoff_metadata(handoff.handoff_id, {"lease_expires_at": 1.0, "no_auto_retry": True})
    before = store.storage_path.read_bytes()
    calls = []

    async def target(*_args):
        calls.append("forbidden")
        return result("success")

    with pytest.raises(Exception, match="attempt limit"):
        await HandoffExecutor(store, registry, target).execute_handoff(handoff.handoff_id)
    assert calls == []
    assert store.storage_path.read_bytes() == before
    assert await registry.list_runs(limit=20) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [None, 0, 1, True, float("nan"), float("inf"), "99999999999"])
async def test_invalid_expiry_never_authorizes_settlement(tmp_path, expiry):
    store, _, task, handoff = await setup(tmp_path)
    claimed = await store.claim_handoff(handoff.handoff_id, worker_id="owner")
    await store.update_handoff_metadata(handoff.handoff_id, {"lease_expires_at": expiry})
    before = store.storage_path.read_bytes()
    with pytest.raises(HandoffOwnershipError):
        await store.settle_claimed_handoff(claimed, "completed", result="不应保存")
    assert store.storage_path.read_bytes() == before
    assert (await store.get_task(task.task_id)).result is None


@pytest.mark.asyncio
async def test_settlement_is_one_persist_for_both_entities(tmp_path, monkeypatch):
    store, _, task, handoff = await setup(tmp_path)
    claimed = await store.claim_handoff(handoff.handoff_id, worker_id="owner")
    persist = store._persist_unlocked
    observed = []

    def checked():
        observed.append((store._tasks[task.task_id].status, store._find_handoff_unlocked(handoff.handoff_id).status))
        persist()

    monkeypatch.setattr(store, "_persist_unlocked", checked)
    await store.settle_claimed_handoff(claimed, "completed", result="唯一结果")
    assert observed == [("completed", "completed")]
    with pytest.raises(HandoffOwnershipError):
        await store.settle_claimed_handoff(claimed, "dead_letter", error="迟到异常")
    assert observed == [("completed", "completed")]
    assert (await store.get_task(task.task_id)).result == "唯一结果"
