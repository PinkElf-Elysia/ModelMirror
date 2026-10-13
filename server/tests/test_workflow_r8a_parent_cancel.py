"""Cancellation boundaries with real temporary Stores and no provider calls.

The API body is loaded unchanged without main's process-wide service singletons;
this is not the independent HTTP/preview acceptance gate.
"""
from __future__ import annotations

import ast
import asyncio
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException

from server.xpert_runtime import AgentTaskStore, RuntimeEventStore, WorkflowExecutionStore
from server.xpert_runtime.handoff_executor import HandoffExecutor, HandoffExecutionResult
from server.xpert_runtime.run_registry import RunRegistry


async def pair(store, status="pending"):
    task = await store.create_task("取消边界", "synthetic")
    handoff = await store.create_handoff(
        task.task_id, "parent", "xpert:child", "synthetic",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
    )
    if status != "pending":
        await store.update_handoff_status(handoff.handoff_id, "accepted")
        if status != "accepted":
            await store.update_handoff_status(handoff.handoff_id, status)
    return task, handoff


@pytest.fixture(scope="module")
def api_code():
    path = Path(__file__).resolve().parents[1] / "main.py"
    names = {"cancel_agent_task", "update_runtime_runs_for_source", "_cancel_agent_task_workflow_children"}
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    definitions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
    assert {node.name for node in definitions} == names
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *definitions], type_ignores=[])
    return compile(ast.fix_missing_locations(module), str(path), "exec")


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_api_projects_actual_cancel_outcome(tmp_path, api_code, terminal):
    store, registry = AgentTaskStore(storage_dir=tmp_path), RunRegistry()
    task = await store.create_task("终态取消", "synthetic")
    await store.update_task(task.task_id, status=terminal, error="original", result="original")
    run = await registry.create_run("agent_task", "synthetic", source_id=task.task_id, status=terminal)
    before = asdict(await registry.get_run(run.run_id))
    namespace = {"app": FastAPI(), "HTTPException": HTTPException, "agent_task_store": store, "run_registry": registry, "workflow_execution_store": WorkflowExecutionStore(tmp_path / "executions"), "workflow_task_store": {}, "asyncio": asyncio}
    exec(api_code, namespace)
    result = await namespace["cancel_agent_task"](task.task_id, {"reason": "late"})
    assert result["status"] == terminal
    assert asdict(await registry.get_run(run.run_id)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "accepted", "retry_wait", "waiting_approval", "waiting_client", "needs_attention"])
async def test_cancel_closes_handoffs_atomically_and_survives_reload(tmp_path, monkeypatch, status):
    events = RuntimeEventStore()
    store = AgentTaskStore(storage_dir=tmp_path, event_store=events)
    task, handoff = await pair(store, "waiting_approval" if status == "needs_attention" else status)
    if status == "needs_attention":
        await store.update_handoff_status(handoff.handoff_id, status)
    persisted = []
    original = store._persist_unlocked

    def persist():
        original()
        persisted.append(store.storage_path.read_bytes())

    monkeypatch.setattr(store, "_persist_unlocked", persist)
    await store.cancel_task(task.task_id, reason="stop")
    assert len(persisted) == 1
    reloaded = AgentTaskStore(storage_dir=tmp_path)
    assert (await reloaded.get_task(task.task_id)).status == "cancelled"
    stopped = await reloaded.get_handoff(handoff.handoff_id)
    assert stopped.status == "dead_letter"
    assert stopped.metadata["last_error"] == "HANDOFF_CANCELLED"
    assert stopped.metadata["ready_for_execution"] is False
    assert not stopped.metadata.get("lease_token")
    assert await reloaded.list_executable_handoffs(now=10**12) == []
    with pytest.raises(ValueError):
        await reloaded.claim_handoff(handoff.handoff_id, worker_id="late", now=10**12)
    before = persisted[-1]
    await store.cancel_task(task.task_id, reason="repeat")
    assert store.storage_path.read_bytes() == before
    assert len(persisted) == 1


@pytest.mark.asyncio
async def test_cancel_persist_failure_rolls_back_whole_pair(tmp_path, monkeypatch):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, handoff = await pair(store)
    before = store.storage_path.read_bytes()
    old_pair = [asdict(await store.get_task(task.task_id)), asdict(await store.get_handoff(handoff.handoff_id))]

    def fail():
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(store, "_persist_unlocked", fail)
    with pytest.raises(OSError):
        await store.cancel_task(task.task_id)
    assert [asdict(await store.get_task(task.task_id)), asdict(await store.get_handoff(handoff.handoff_id))] == old_pair
    assert store.storage_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_legacy_queue_cannot_dispatch_terminal_parent(tmp_path, terminal):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, handoff = await pair(store)
    # Represents an old persisted pair with a terminal Task and pending Handoff.
    await store.update_task(task.task_id, status=terminal)
    assert await store.list_executable_handoffs() == []
    with pytest.raises(ValueError):
        await store.claim_handoff(handoff.handoff_id, worker_id="late")


@pytest.mark.asyncio
async def test_cancelled_task_rejects_new_handoff(tmp_path):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, _ = await pair(store)
    await store.cancel_task(task.task_id)
    with pytest.raises(ValueError):
        await store.create_handoff(task.task_id, "parent", "xpert:late", "synthetic")


@pytest.mark.asyncio
async def test_cancel_preserves_terminal_siblings_and_unrelated_task(tmp_path):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, pending = await pair(store)
    terminal = []
    for status in ("completed", "dead_letter", "rejected"):
        handoff = await store.create_handoff(task.task_id, "parent", "xpert:child", "synthetic")
        if status == "completed":
            await store.update_handoff_status(handoff.handoff_id, "accepted")
        await store.update_handoff_status(handoff.handoff_id, status)
        terminal.append(asdict(handoff))
    other_task, other_handoff = await pair(store)
    other = [asdict(other_task), asdict(other_handoff)]
    await store.cancel_task(task.task_id)
    assert (await store.get_handoff(pending.handoff_id)).status == "dead_letter"
    assert [asdict(await store.get_handoff(item["handoff_id"])) for item in terminal] == terminal
    assert [asdict(await store.get_task(other_task.task_id)), asdict(await store.get_handoff(other_handoff.handoff_id))] == other


@pytest.mark.asyncio
async def test_cancel_committed_before_audit_stays_cancelled_on_reload(tmp_path, monkeypatch):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, handoff = await pair(store)

    async def crash(*args, **kwargs):
        raise RuntimeError("synthetic audit interruption")

    monkeypatch.setattr(store, "_record", crash)
    with pytest.raises(RuntimeError):
        await store.cancel_task(task.task_id, reason="first")
    reloaded = AgentTaskStore(storage_dir=tmp_path)
    before = store.storage_path.read_bytes()
    await reloaded.cancel_task(task.task_id, reason="replay")
    assert reloaded.storage_path.read_bytes() == before
    assert (await reloaded.get_task(task.task_id)).error == "first"
    assert (await reloaded.get_handoff(handoff.handoff_id)).status == "dead_letter"


@pytest.mark.asyncio
async def test_stale_queue_snapshot_cannot_reclaim_cancelled_task(tmp_path):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, handoff = await pair(store)
    queued = (await store.list_executable_handoffs())[0]
    await store.cancel_task(task.task_id)
    queued.metadata["ready_for_execution"] = True
    queued.status = "pending"
    with pytest.raises(ValueError):
        await store.claim_handoff(queued.handoff_id, worker_id="late")
    assert (await store.get_handoff(handoff.handoff_id)).status == "dead_letter"


@pytest.mark.asyncio
async def test_api_cancels_related_runs_even_below_global_page_limit(tmp_path, api_code):
    store, registry = AgentTaskStore(storage_dir=tmp_path), RunRegistry()
    task, handoff = await pair(store)
    task_run = await registry.create_run("agent_task", "synthetic", source_id=task.task_id, status="running")
    handoff_run = await registry.create_run("agent_handoff", "synthetic", source_id=handoff.handoff_id, status="running")
    task_run.created_at = 1.0
    for index in range(205):
        await registry.create_run("agent_task", "unrelated", source_id=f"other-{index}", status="running")
    assert task_run.run_id not in {run.run_id for run in await registry.list_runs(run_type="agent_task", limit=200)}
    namespace = {"app": FastAPI(), "HTTPException": HTTPException, "agent_task_store": store, "run_registry": registry, "workflow_execution_store": WorkflowExecutionStore(tmp_path / "executions"), "workflow_task_store": {}, "asyncio": asyncio}
    exec(api_code, namespace)
    result = await namespace["cancel_agent_task"](task.task_id, {"reason": "stop"})
    assert result["status"] == "cancelled"
    assert (await registry.get_run(task_run.run_id)).status == "cancelled"
    assert (await registry.get_run(handoff_run.run_id)).status == "cancelled"
    assert all(run.status == "running" for run in await registry.list_runs(source_id="other-0"))


@pytest.mark.asyncio
async def test_late_metadata_does_not_rearm_cancelled_handoff(tmp_path):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, handoff = await pair(store)
    await store.cancel_task(task.task_id)
    before = store.storage_path.read_bytes()
    returned = await store.update_handoff_metadata(handoff.handoff_id, {"ready_for_execution": True, "lease_token": "late"})
    returned.metadata["ready_for_execution"] = True
    assert store.storage_path.read_bytes() == before
    assert (await store.get_handoff(handoff.handoff_id)).metadata["ready_for_execution"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before_dispatch", "success", "failure", "approval", "client"])
async def test_executor_cancellation_wins_dispatch_or_late_result(tmp_path, monkeypatch, boundary):
    store, registry = AgentTaskStore(storage_dir=tmp_path), RunRegistry()
    task, handoff = await pair(store)
    reached, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def target(*args):
        calls.append(True)
        reached.set()
        await release.wait()
        if boundary == "failure":
            raise RuntimeError("late failure")
        return HandoffExecutionResult(
            output="late result", run_id="child-run", xpert_id="child",
            xpert_slug="child", xpert_version=1,
            waiting_approval=boundary == "approval", waiting_client=boundary == "client",
        )

    executor = HandoffExecutor(store, registry, target, enabled=True)
    original = executor._checkpoint

    async def checkpoint(*args, **kwargs):
        await original(*args, **kwargs)
        if boundary == "before_dispatch" and kwargs["event_type"] == "agent_handoff.attempt.started":
            reached.set()
            await release.wait()

    monkeypatch.setattr(executor, "_checkpoint", checkpoint)
    worker = asyncio.create_task(executor.execute_handoff(handoff.handoff_id))
    try:
        await asyncio.wait_for(reached.wait(), 3)
        await store.cancel_task(task.task_id, reason="stop")
        release.set()
        result = await asyncio.wait_for(worker, 3)
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert len(calls) == (0 if boundary == "before_dispatch" else 1)
    assert result.status == "dead_letter"
    assert (await store.get_task(task.task_id)).status == "cancelled"
    assert (await store.get_task(task.task_id)).result is None
    assert "late result" not in store.storage_path.read_text(encoding="utf-8")
    assert {run.status for run in await registry.list_runs()} == {"cancelled"}
