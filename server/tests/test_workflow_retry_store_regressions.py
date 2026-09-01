from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)


def _create_wait(store, task_id="task-retry", *, wait_kind="node_retry"):
    store.create(
        task_id=task_id,
        run_id=f"run-{task_id}",
        run_type="workflow",
        workflow={"nodes": [{"id": "node-1"}]},
        inputs={},
    )
    return store.suspend(
        task_id,
        wait_kind=wait_kind,
        wait_id=f"{wait_kind}:{task_id}",
        resume_at=100 if wait_kind in {"timer", "node_retry"} else None,
        continuation={"queue": ["node-1"]},
    )


def _claim(store, *, worker="worker-a", now=100):
    return store.claim_due_wait(
        "task-retry",
        wait_kind="node_retry",
        wait_id="node_retry:task-retry",
        worker_id=worker,
        lease_seconds=5,
        now=now,
    )


def test_cancelled_execution_cannot_be_resuspended_or_reclassified(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    store.cancel("task-retry")
    with pytest.raises(WorkflowExecutionConflictError):
        store.suspend(
            "task-retry",
            wait_kind="node_retry",
            wait_id="node_retry:next",
            resume_at=200,
            continuation={},
        )
    with pytest.raises(WorkflowExecutionConflictError):
        _claim(store)
    store.complete("task-retry", result="late success")
    store.fail("task-retry", error="late failure")
    store.reject("task-retry", error="late rejection")
    assert store.require("task-retry").status == "cancelled"
    assert store.require("task-retry").error == "cancelled"


@pytest.mark.parametrize("wait_kind", ["approval", "agent_handoff"])
@pytest.mark.parametrize("final_action", ["suspend", "complete", "fail"])
def test_legacy_wait_callers_remain_compatible_without_explicit_token(
    tmp_path, wait_kind, final_action
):
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store, wait_kind=wait_kind)
    store.mark_ready(
        "task-retry", wait_kind=wait_kind, wait_id=f"{wait_kind}:task-retry"
    )
    store.claim("task-retry", worker_id="legacy-worker")
    if final_action == "suspend":
        store.suspend(
            "task-retry", wait_kind=wait_kind, wait_id="next-wait", continuation={}
        )
        assert store.require("task-retry").status == "waiting"
    elif final_action == "complete":
        store.complete("task-retry", result="done")
        assert store.require("task-retry").status == "completed"
    else:
        store.fail("task-retry", error="safe failure")
        assert store.require("task-retry").status == "failed"


@pytest.mark.parametrize(
    "action", ["suspend", "complete", "fail", "event", "refresh", "assert"]
)
def test_reclaimed_lease_fences_stale_worker_writes(tmp_path, monkeypatch, action):
    monkeypatch.setattr("server.xpert_runtime.execution_store.time.time", lambda: 106)
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    first = _claim(store)
    second = _claim(store, worker="worker-b", now=106)
    before = asdict(store.require("task-retry"))
    with pytest.raises(WorkflowExecutionConflictError):
        if action == "suspend":
            store.suspend(
                "task-retry", wait_kind="node_retry", wait_id="next",
                resume_at=200, continuation={}, expected_lease_token=first.lease_token,
            )
        elif action == "complete":
            store.complete("task-retry", result="late", expected_lease_token=first.lease_token)
        elif action == "fail":
            store.fail("task-retry", error="late", expected_lease_token=first.lease_token)
        elif action == "event":
            store.append_event(
                "task-retry", {"event": "node_retry_started", "attempt": 2},
                expected_lease_token=first.lease_token,
            )
        elif action == "assert":
            store.assert_lease("task-retry", lease_token=first.lease_token)
        else:
            store.refresh_lease("task-retry", lease_token=first.lease_token)
    assert asdict(store.require("task-retry")) == before
    assert store.require("task-retry").lease_token == second.lease_token


@pytest.mark.parametrize("action", ["complete", "fail", "event"])
def test_terminal_winner_still_fences_stale_worker(tmp_path, monkeypatch, action):
    clock = [100]
    monkeypatch.setattr(
        "server.xpert_runtime.execution_store.time.time", lambda: clock[0]
    )
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    first = _claim(store)
    clock[0] = 106
    second = _claim(store, worker="worker-b", now=clock[0])
    winner = store.complete(
        "task-retry",
        result="winner",
        expected_lease_token=second.lease_token,
    )
    assert winner.status == "completed"

    with pytest.raises(WorkflowExecutionConflictError):
        if action == "complete":
            store.complete(
                "task-retry",
                result="stale",
                expected_lease_token=first.lease_token,
            )
        elif action == "fail":
            store.fail(
                "task-retry",
                error="stale",
                expected_lease_token=first.lease_token,
            )
        else:
            store.append_event(
                "task-retry",
                {"event": "workflow_end", "final_output": "stale"},
                expected_lease_token=first.lease_token,
            )

    stored = store.require("task-retry")
    assert stored.status == "completed"
    assert stored.result == "winner"
    assert stored.error is None
    assert stored.events == []


def test_unfenced_late_failure_marks_source_invalid_without_overwriting_result(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-source",
        run_id="run-source",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "workflow-source", "nodes": []},
        inputs={},
    )
    completed = store.complete("task-source", result="trusted result")
    completed_revision = completed.revision

    invalidated = store.fail("task-source", error="private late failure")

    assert invalidated.status == "completed"
    assert invalidated.result == "trusted result"
    assert invalidated.error is None
    assert invalidated.revision == completed_revision + 1
    assert invalidated.runtime_metadata["terminal_source_invalidated"] is True
    reloaded = WorkflowExecutionStore(tmp_path).require("task-source")
    assert reloaded.status == "completed"
    assert reloaded.runtime_metadata["terminal_source_invalidated"] is True
    assert "terminal_source_invalidated" not in WorkflowExecutionStore.serialize_public(
        reloaded
    )


def test_due_claim_is_a_deep_copy_and_refresh_rejects_expired_or_empty_token(
    tmp_path, monkeypatch
):
    clock = [100]
    monkeypatch.setattr("server.xpert_runtime.execution_store.time.time", lambda: clock[0])
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    claimed = _claim(store)
    claimed.continuation["queue"].append("malicious-node")
    claimed.workflow["nodes"][0]["id"] = "mutated"
    claimed.lease_token = "mutated"
    assert store.require("task-retry").continuation == {"queue": ["node-1"]}
    assert store.require("task-retry").workflow["nodes"][0]["id"] == "node-1"
    token = store.require("task-retry").lease_token
    with pytest.raises(WorkflowExecutionConflictError):
        store.refresh_lease("task-retry", lease_token="")
    clock[0] = 105
    with pytest.raises(WorkflowExecutionConflictError):
        store.refresh_lease("task-retry", lease_token=token)


def test_run_id_rebind_requires_the_current_live_resume_lease(tmp_path, monkeypatch):
    clock = [100]
    monkeypatch.setattr("server.xpert_runtime.execution_store.time.time", lambda: clock[0])
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    claimed = _claim(store)
    store.cancel("task-retry")

    with pytest.raises(WorkflowExecutionConflictError):
        store.update_run_id(
            "task-retry",
            run_id="run-after-cancel",
            expected_lease_token=claimed.lease_token,
        )

    cancelled = store.require("task-retry")
    assert cancelled.status == "cancelled"
    assert cancelled.run_id == "run-task-retry"
    assert cancelled.previous_run_ids == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("resume_at", "bad-timestamp"), ("resume_at", float("nan")),
        ("resume_at", True), ("resume_at", None),
        ("lease_expires_at", "bad-lease"), ("lease_expires_at", float("nan")),
        ("lease_expires_at", None), ("lease_expires_at", True),
        ("wait_id", []), ("wait_id", ""), ("wait_id", 123),
    ],
)
def test_one_invalid_due_wait_is_quarantined_without_blocking_timer(tmp_path, field, value):
    store = WorkflowExecutionStore(tmp_path)
    bad = _create_wait(store)
    _create_wait(store, "task-timer", wait_kind="timer")
    setattr(bad, field, value)
    bad.status = "running"
    assert [item.task_id for item in store.list_due_waits(now=200)] == ["task-timer"]
    assert store.require("task-retry").status == "failed"
    assert store.require("task-retry").error == "WORKFLOW_WAIT_STATE_INVALID"
    assert store.require("task-timer").status == "waiting"


def test_one_bad_snapshot_record_does_not_discard_valid_neighbors(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    first = _create_wait(store, "task-first", wait_kind="timer")
    last = _create_wait(store, "task-last", wait_kind="timer")
    store.snapshot_path.write_text(
        json.dumps({"version": "workflow-executions-v1", "items": [
            asdict(first), {"task_id": "broken-record"}, asdict(last),
        ]}), encoding="utf-8",
    )
    restored = WorkflowExecutionStore(tmp_path)
    assert {item.task_id for item in restored.list_due_waits(now=200)} == {
        "task-first", "task-last"
    }
    assert restored.get("broken-record") is None


def test_loaded_invalid_wait_is_safe_failure_and_valid_timer_still_recovers(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    bad = _create_wait(store)
    valid = _create_wait(store, "task-timer", wait_kind="timer")
    bad_raw = asdict(bad)
    bad_raw["lease_expires_at"] = "malformed-lease"
    store.snapshot_path.write_text(
        json.dumps({"version": "workflow-executions-v1", "items": [bad_raw, asdict(valid)]}),
        encoding="utf-8",
    )
    restored = WorkflowExecutionStore(tmp_path)
    assert [item.task_id for item in restored.list_due_waits(now=200)] == ["task-timer"]
    assert restored.require("task-retry").error == "WORKFLOW_WAIT_STATE_INVALID"


def test_invalid_suspend_does_not_partially_change_running_execution(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    before = asdict(store.require("task"))
    with pytest.raises(WorkflowExecutionConflictError):
        store.suspend("task", wait_kind="node_retry", wait_id="", continuation={})
    assert asdict(store.require("task")) == before
