from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

import pytest

from server.xpert_runtime import AgentTaskStore, RuntimeEventStore


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("requested", [None, "pending", "running", "waiting_approval", "completed", "failed", "cancelled"])
async def test_terminal_task_rejects_late_mutation(tmp_path, terminal, requested):
    events = RuntimeEventStore()
    store = AgentTaskStore(event_store=events, storage_dir=tmp_path)
    task = await store.create_task("终态测试", "synthetic")
    saved = await store.update_task(
        task.task_id, status=terminal, result="original", error="original-error",
        metadata={"nested": {"value": "original"}},
    )
    before = asdict(saved)
    disk = store.storage_path.read_bytes()
    event_count = len(await events.list_events(task.task_id))
    result = await store.update_task(
        task.task_id, status=requested, clear_result=True, clear_error=True,
        assigned_agent="late", metadata={"nested": {"value": "late"}},
    )
    assert asdict(result) == before
    assert asdict(await store.get_task(task.task_id)) == before
    assert store.storage_path.read_bytes() == disk
    assert len(await events.list_events(task.task_id)) == event_count
    assert asdict(await AgentTaskStore(storage_dir=tmp_path).get_task(task.task_id)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["create", "get", "list", "update", "occurrence", "router", "router_repeat"])
async def test_public_task_references_cannot_bypass_terminal_guard(tmp_path, reader):
    store = AgentTaskStore(storage_dir=tmp_path)
    if reader.startswith("router"):
        task, _ = await store.create_task_and_handoff(
            title="引用隔离", input_text="synthetic", source_agent="parent",
            target_agent="child", reason="synthetic", occurrence_key="router",
            task_metadata={"nested": {"value": "original"}},
        )
        exposed = task
        if reader == "router_repeat":
            exposed, _ = await store.create_task_and_handoff(
                title="ignored", input_text="ignored", source_agent="parent",
                target_agent="child", reason="ignored", occurrence_key="router",
            )
    else:
        task = await store.create_task(
            "引用隔离", "synthetic", occurrence_key="task",
            metadata={"nested": {"value": "original"}},
        )
        if reader == "get":
            exposed = await store.get_task(task.task_id)
        elif reader == "list":
            exposed = (await store.list_tasks())[0]
        elif reader == "update":
            exposed = await store.update_task(task.task_id, status="running")
        elif reader == "occurrence":
            exposed = await store.create_task("ignored", "ignored", occurrence_key="task")
        else:
            exposed = task
    saved = await store.update_task(task.task_id, status="completed", result="original")
    before = asdict(saved)
    disk = store.storage_path.read_bytes()
    exposed.status = "running"
    exposed.metadata["nested"]["value"] = "injected"
    assert asdict(await store.get_task(task.task_id)) == before
    assert store.storage_path.read_bytes() == disk


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_task_metadata_does_not_retain_caller_reference(tmp_path, operation):
    store = AgentTaskStore(storage_dir=tmp_path)
    metadata = {"nested": {"value": "original"}}
    task = await store.create_task("输入隔离", "synthetic", metadata=metadata if operation == "create" else None)
    if operation == "update":
        await store.update_task(task.task_id, metadata=metadata)
    await store.update_task(task.task_id, status="completed")
    metadata["nested"]["value"] = "injected"
    assert (await store.get_task(task.task_id)).metadata["nested"]["value"] == "original"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_cancel_terminal_task_is_noop_without_false_audit(tmp_path, terminal):
    events = RuntimeEventStore()
    store = AgentTaskStore(event_store=events, storage_dir=tmp_path)
    task = await store.create_task("取消竞争", "synthetic")
    await store.update_task(task.task_id, status=terminal, result="original")
    before = store.storage_path.read_bytes()
    event_count = len(await events.list_events(task.task_id))
    result = await store.cancel_task(task.task_id)
    assert result.status == terminal
    assert store.storage_path.read_bytes() == before
    assert len(await events.list_events(task.task_id)) == event_count


@pytest.mark.asyncio
async def test_repeated_cancel_records_only_winning_transition(tmp_path):
    events = RuntimeEventStore()
    store = AgentTaskStore(event_store=events, storage_dir=tmp_path)
    task = await store.create_task("重复取消", "synthetic")
    await store.cancel_task(task.task_id, reason="first")
    before = store.storage_path.read_bytes()
    await store.cancel_task(task.task_id, reason="late")
    assert store.storage_path.read_bytes() == before
    cancellations = [event for event in await events.list_events(task.task_id) if event.type == "agent.task.cancelled"]
    assert len(cancellations) == 1
    assert (await store.get_task(task.task_id)).error == "first"


async def failed_pair(store):
    task = await store.create_task("显式重排", "synthetic")
    handoff = await store.create_handoff(task.task_id, "parent", "xpert:child", "synthetic")
    await store.update_handoff_status(handoff.handoff_id, "accepted")
    await store.update_handoff_status(handoff.handoff_id, "dead_letter")
    await store.update_task(task.task_id, status="failed", error="failed", metadata={"handoff_id": handoff.handoff_id})
    return task, handoff


@pytest.mark.asyncio
async def test_explicit_requeue_commits_task_and_handoff_together(tmp_path, monkeypatch):
    events = RuntimeEventStore()
    store = AgentTaskStore(event_store=events, storage_dir=tmp_path)
    task, handoff = await failed_pair(store)
    persisted = []
    original = store._persist_unlocked

    def capture():
        original()
        persisted.append(store.storage_path.read_bytes())

    monkeypatch.setattr(store, "_persist_unlocked", capture)
    await store.requeue_handoff(handoff.handoff_id, operator="tester")
    assert len(persisted) == 1
    reopened = AgentTaskStore(storage_dir=tmp_path)
    assert (await reopened.get_task(task.task_id)).status == "pending"
    assert (await reopened.get_task(task.task_id)).error is None
    assert (await reopened.get_handoff(handoff.handoff_id)).status == "pending"
    updates = [event for event in await events.list_events(task.task_id) if event.type == "agent.task.updated"]
    assert len(updates) == 2
    assert {event.payload["status"] for event in updates} == {"failed", "pending"}


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
async def test_requeue_cannot_reopen_success_or_cancellation(tmp_path, terminal):
    store = AgentTaskStore(storage_dir=tmp_path)
    task = await store.create_task("重排限制", "synthetic")
    handoff = await store.create_handoff(task.task_id, "parent", "xpert:child", "synthetic")
    await store.update_handoff_status(handoff.handoff_id, "accepted")
    await store.update_handoff_status(handoff.handoff_id, "dead_letter")
    await store.update_task(task.task_id, status=terminal)
    before = store.storage_path.read_bytes()
    with pytest.raises(ValueError):
        await store.requeue_handoff(handoff.handoff_id, operator="tester")
    assert store.storage_path.read_bytes() == before
    assert (await store.get_handoff(handoff.handoff_id)).status == "dead_letter"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "cancel", "requeue"])
@pytest.mark.parametrize("failure_point", ["before_write", "replace"])
async def test_failed_persistence_does_not_leave_memory_ahead_of_disk(tmp_path, monkeypatch, operation, failure_point):
    events = RuntimeEventStore()
    store = AgentTaskStore(event_store=events, storage_dir=tmp_path)
    if operation == "requeue":
        task, handoff = await failed_pair(store)
    else:
        task = await store.create_task("持久化故障", "synthetic")
    before = asdict(await store.get_task(task.task_id))
    disk = store.storage_path.read_bytes()
    event_count = len(await events.list_events(task.task_id))

    def fail(*args):
        raise OSError("synthetic persist failure")

    if failure_point == "replace":
        monkeypatch.setattr(Path, "replace", fail)
    else:
        monkeypatch.setattr(store, "_persist_unlocked", fail)
    with pytest.raises(OSError):
        if operation == "requeue":
            await store.requeue_handoff(handoff.handoff_id, operator="tester")
        elif operation == "cancel":
            await store.cancel_task(task.task_id)
        else:
            await store.update_task(task.task_id, status="completed", result="synthetic")
    assert asdict(await store.get_task(task.task_id)) == before
    assert store.storage_path.read_bytes() == disk
    assert len(await events.list_events(task.task_id)) == event_count
    if operation == "requeue":
        assert (await store.get_handoff(handoff.handoff_id)).status == "dead_letter"


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["get", "list", "noop", "cancel", "occurrence"])
async def test_terminal_reads_return_detached_snapshots(tmp_path, reader):
    store = AgentTaskStore(storage_dir=tmp_path)
    task = await store.create_task("终态引用", "synthetic", occurrence_key="one")
    original = await store.update_task(
        task.task_id, status="completed", metadata={"nested": {"value": "original"}},
    )
    if reader == "get":
        exposed = await store.get_task(task.task_id)
    elif reader == "list":
        exposed = (await store.list_tasks())[0]
    elif reader == "noop":
        exposed = await store.update_task(task.task_id, status="running")
    elif reader == "cancel":
        exposed = await store.cancel_task(task.task_id)
    else:
        exposed = await store.create_task("ignored", "ignored", occurrence_key="one")
    exposed.status = "pending"
    exposed.metadata["nested"]["value"] = "injected"
    assert asdict(await store.get_task(task.task_id)) == asdict(original)
    assert asdict(await AgentTaskStore(storage_dir=tmp_path).get_task(task.task_id)) == asdict(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["completed", "cancelled"])
async def test_terminal_winner_is_fixed_before_event_recording(tmp_path, monkeypatch, winner):
    events = RuntimeEventStore()
    store = AgentTaskStore(event_store=events, storage_dir=tmp_path)
    task = await store.create_task("终态竞争", "synthetic")
    recording = asyncio.Event()
    release = asyncio.Event()
    original_record = store._record

    async def blocked_record(*args, **kwargs):
        recording.set()
        await release.wait()
        await original_record(*args, **kwargs)

    monkeypatch.setattr(store, "_record", blocked_record)
    first = asyncio.create_task(
        store.cancel_task(task.task_id, reason="first") if winner == "cancelled"
        else store.update_task(task.task_id, status="completed", result="first")
    )
    try:
        await asyncio.wait_for(recording.wait(), timeout=2)
        disk = store.storage_path.read_bytes()
        losers = await asyncio.wait_for(asyncio.gather(
            store.cancel_task(task.task_id, reason="late"),
            store.update_task(task.task_id, status="failed", error="late"),
            store.update_task(task.task_id, status="running", clear_result=True),
        ), timeout=2)
        assert all(item.status == winner for item in losers)
        assert store.storage_path.read_bytes() == disk
    finally:
        release.set()
        await first
    recorded = await events.list_events(task.task_id)
    assert len([item for item in recorded if item.type == "agent.task.updated"]) == 1
    assert len([item for item in recorded if item.type == "agent.task.cancelled"]) == (winner == "cancelled")


@pytest.mark.asyncio
async def test_requeue_rejects_a_different_bound_handoff(tmp_path):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, handoff = await failed_pair(store)
    other = await store.create_handoff(task.task_id, "parent", "xpert:other", "synthetic")
    await store.update_handoff_status(other.handoff_id, "accepted")
    await store.update_handoff_status(other.handoff_id, "dead_letter")
    before = store.storage_path.read_bytes()
    with pytest.raises(ValueError, match="不一致"):
        await store.requeue_handoff(other.handoff_id, operator="tester")
    assert store.storage_path.read_bytes() == before
    assert (await store.get_task(task.task_id)).metadata["handoff_id"] == handoff.handoff_id


@pytest.mark.asyncio
async def test_duplicate_requeue_has_one_committed_winner(tmp_path):
    store = AgentTaskStore(storage_dir=tmp_path)
    task, handoff = await failed_pair(store)
    results = await asyncio.gather(
        store.requeue_handoff(handoff.handoff_id, operator="one"),
        store.requeue_handoff(handoff.handoff_id, operator="two"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    reopened = AgentTaskStore(storage_dir=tmp_path)
    saved_task = await reopened.get_task(task.task_id)
    saved_handoff = await reopened.get_handoff(handoff.handoff_id)
    assert saved_task.status == saved_handoff.status == "pending"
    assert saved_task.metadata["requeued_by"] == saved_handoff.metadata["requeued_by"]
