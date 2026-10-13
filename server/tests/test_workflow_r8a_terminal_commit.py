"""Terminal decisions and their audit event are one fenced Store commit."""
from __future__ import annotations

from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading
from types import SimpleNamespace

import pytest

from server.xpert_runtime import WorkflowExecutionStore
from server.xpert_runtime import execution_store as store_module
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError, WorkflowExecutionStorageError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock.now))
    store = WorkflowExecutionStore(tmp_path / "executions")
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    return store, clock


def finish(store, status, token, event):
    kwargs = {"expected_lease_token": token, "terminal_event": event}
    if status == "completed":
        return store.complete("task", result="synthetic", **kwargs)
    return store.fail("task", error="SYNTHETIC_ERROR", **kwargs)


def event_for(status):
    return {"event": "workflow_end" if status == "completed" else "error", "terminal": True, "task_id": "task", "run_id": "run"}


@pytest.mark.parametrize("state", ["running", "claimed", "waiting", "ready"])
def test_management_cancel_commits_its_audit_once(runtime, monkeypatch, state):
    store, _ = runtime
    if state == "claimed":
        store.claim("task", worker_id="worker")
    elif state in {"waiting", "ready"}:
        store.suspend("task", wait_kind="timer", wait_id="timer", continuation={"queue": ["next"]}, resume_at=1100.0)
        if state == "ready":
            store.mark_ready("task", wait_kind="timer", wait_id="timer")
    writes = []
    persist = store._persist_unlocked

    def record_write():
        writes.append(store.snapshot("task"))
        persist()

    monkeypatch.setattr(store, "_persist_unlocked", record_write)
    result = store.cancel("task", error="synthetic cancellation")
    assert len(writes) == 1
    assert writes[0].status == "cancelled"
    event = writes[0].events[-1]
    assert event["event"] == "workflow_cancelled"
    assert event["task_id"] == "task" and event["run_id"] == "run"
    assert store.cancel("task") == result
    assert len(writes) == 1
    assert WorkflowExecutionStore(store.storage_dir).require("task") == result


def test_cancel_after_completion_does_not_manufacture_an_event(runtime):
    store, _ = runtime
    completed = store.complete("task", result="done")
    assert store.cancel("task") == completed
    assert not any(event.get("event") == "workflow_cancelled" for event in completed.events)


def test_cancel_audit_disk_failure_does_not_commit(runtime, monkeypatch):
    store, _ = runtime
    before = store.snapshot_path.read_bytes()

    def fail_write(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(store, "_write_fsynced", fail_write)
    with pytest.raises(WorkflowExecutionStorageError):
        store.cancel("task")
    assert not store.available
    assert store.snapshot_path.read_bytes() == before
    restored = WorkflowExecutionStore(store.storage_dir).require("task")
    assert restored.status != "cancelled"
    assert not any(event.get("event") == "workflow_cancelled" for event in restored.events)


@pytest.mark.parametrize("status", ["running", "failed", "cancelled"])
def test_post_completion_scope_rejects_other_states(runtime, status):
    store, _ = runtime
    if status == "failed":
        store.fail("task", error="synthetic")
    elif status == "cancelled":
        store.cancel("task")
    with pytest.raises(WorkflowExecutionConflictError):
        with store.completed_local_operation("task", expected_snapshot=store.snapshot("task")):
            pytest.fail("must not dispatch")


def test_post_completion_scope_serializes_invalidation_and_rejects_reuse(runtime):
    store, _ = runtime
    snapshot = store.complete("task", result="synthetic")
    entered, release, invalidating = threading.Event(), threading.Event(), threading.Event()

    def followup():
        with store.completed_local_operation("task", expected_snapshot=snapshot):
            entered.set()
            assert release.wait(5)
            store.append_event("task", {"event": "skill_creator_handoff", "status": "ready"})

    def invalidate():
        invalidating.set()
        return store.fail("task", error="synthetic invalidation")

    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(followup)
        try:
            assert entered.wait(5)
            invalidation = pool.submit(invalidate)
            assert invalidating.wait(5)
            with pytest.raises(TimeoutError):
                invalidation.result(timeout=0.1)
        finally:
            release.set()
        work.result(timeout=5)
        invalidation.result(timeout=5)
    current = store.require("task")
    assert current.status == "completed"
    assert current.runtime_metadata["terminal_source_invalidated"] is True
    assert current.events[-1]["event"] == "skill_creator_handoff"
    for candidate in (snapshot, current):
        with pytest.raises(WorkflowExecutionConflictError):
            with store.completed_local_operation("task", expected_snapshot=candidate):
                pytest.fail("must not replay invalidated completion")


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_decision_and_event_share_one_write_and_survive_reload(runtime, monkeypatch, status):
    store, _clock = runtime
    owner = store.claim("task", worker_id="owner")
    writes = []
    original = store._persist_unlocked

    def persist():
        writes.append(asdict(store.require("task")))
        original()

    monkeypatch.setattr(store, "_persist_unlocked", persist)
    result = finish(store, status, owner.lease_token, event_for(status))
    assert len(writes) == 1
    assert writes[0]["status"] == status
    assert writes[0]["events"][-1]["event"] == event_for(status)["event"]
    assert result.lease_token is None
    assert result.revision == owner.revision + 1
    assert asdict(WorkflowExecutionStore(store.storage_dir).require("task")) == asdict(result)
    result.events.clear()
    assert store.require("task").events


@pytest.mark.parametrize("status", ["completed", "failed"])
@pytest.mark.parametrize("ownership", ["missing", "empty", "expired", "reclaimed", "cancelled", "completed"])
def test_unowned_terminal_commit_cannot_append_an_event(runtime, status, ownership):
    store, clock = runtime
    owner = store.claim("task", worker_id="old", lease_seconds=5)
    token = owner.lease_token
    if ownership in {"missing", "empty"}:
        token = None if ownership == "missing" else ""
    elif ownership in {"expired", "reclaimed"}:
        clock.now += 10
        if ownership == "reclaimed":
            store.claim("task", worker_id="new")
    elif ownership == "cancelled":
        store.cancel("task")
    else:
        store.complete("task", result="winner", expected_lease_token=token)
    before = store.snapshot("task")
    raw = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        finish(store, status, token, event_for(status))
    assert store.snapshot("task") == before
    assert store.snapshot_path.read_bytes() == raw


@pytest.mark.parametrize("status", ["completed", "failed"])
@pytest.mark.parametrize("field,value", [("task_id", "other"), ("run_id", "other"), ("event", "timer_waiting")])
def test_invalid_terminal_event_is_rejected_before_mutation(runtime, status, field, value):
    store, _clock = runtime
    owner = store.claim("task", worker_id="owner")
    event = {**event_for(status), field: value}
    before = store.snapshot("task")
    raw = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        finish(store, status, owner.lease_token, event)
    assert store.snapshot("task") == before
    assert store.snapshot_path.read_bytes() == raw


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_failed_persist_does_not_return_a_successful_commit(runtime, monkeypatch, status):
    store, _clock = runtime
    owner = store.claim("task", worker_id="owner")
    before = store.snapshot_path.read_bytes()

    def fail_write(*_args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(store, "_write_fsynced", fail_write)
    with pytest.raises(WorkflowExecutionStorageError):
        finish(store, status, owner.lease_token, event_for(status))
    assert store.available is False
    assert store.snapshot_path.read_bytes() == before
    assert WorkflowExecutionStore(store.storage_dir).require("task").status != status
