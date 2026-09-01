from __future__ import annotations

import pytest

from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)


def _waiting_retry(store: WorkflowExecutionStore, *, resume_at: float = 100.0) -> None:
    store.create(
        task_id="task-retry",
        run_id="run-retry",
        run_type="workflow",
        workflow={"id": "wf-retry", "nodes": [], "edges": []},
        inputs={},
        source_kind="workflow_deployment",
    )
    store.suspend(
        "task-retry",
        wait_kind="node_retry",
        wait_id="node_retry:abc",
        resume_at=resume_at,
        continuation={
            "queue": ["http-1"],
            "executed": ["input-1"],
            "retry_state": {
                "version": 1,
                "node_id": "http-1",
                "node_kind": "http_request",
                "next_attempt": 2,
                "max_attempts": 3,
                "error_code": "HTTP_TIMEOUT",
                "classification": "transient",
                "resume_at": resume_at,
                "target_fingerprint": None,
            },
        },
    )


def test_due_wait_claim_is_atomic_and_timer_wrapper_stays_compatible(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path)
    _waiting_retry(store)

    assert store.list_due_waits(now=99) == []
    assert [item.task_id for item in store.list_due_waits(now=100)] == [
        "task-retry"
    ]
    assert store.list_due_timers(now=100) == []

    claimed = store.claim_due_wait(
        "task-retry",
        wait_kind="node_retry",
        wait_id="node_retry:abc",
        worker_id="worker-a",
        lease_seconds=30,
        now=100,
    )
    assert claimed.status == "running"
    assert claimed.lease_owner == "worker-a"
    assert store.list_due_waits(now=101) == []
    with pytest.raises(WorkflowExecutionConflictError):
        store.claim_due_wait(
            "task-retry",
            wait_kind="node_retry",
            wait_id="node_retry:abc",
            worker_id="worker-b",
            now=101,
        )


def test_expired_due_wait_lease_can_be_reclaimed_once(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path)
    _waiting_retry(store)
    first = store.claim_due_wait(
        "task-retry",
        wait_kind="node_retry",
        wait_id="node_retry:abc",
        worker_id="worker-a",
        lease_seconds=5,
        now=100,
    )
    first_lease_token = first.lease_token

    assert store.list_due_waits(now=104) == []
    assert [item.task_id for item in store.list_due_waits(now=105)] == [
        "task-retry"
    ]
    second = store.claim_due_wait(
        "task-retry",
        wait_kind="node_retry",
        wait_id="node_retry:abc",
        worker_id="worker-b",
        lease_seconds=5,
        now=105,
    )
    assert second.lease_owner == "worker-b"
    assert second.lease_token != first_lease_token


def test_cancelled_due_wait_cannot_be_listed_or_claimed(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path)
    _waiting_retry(store)
    store.cancel("task-retry")

    assert store.list_due_waits(now=200) == []
    with pytest.raises(WorkflowExecutionConflictError):
        store.claim_due_wait(
            "task-retry",
            wait_kind="node_retry",
            wait_id="node_retry:abc",
            worker_id="worker-a",
            now=200,
        )


def test_retry_events_are_bounded_and_idempotent_by_attempt(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path)
    _waiting_retry(store)
    event = {
        "event": "node_retry_scheduled",
        "node_id": "http-1",
        "node_type": "http_request",
        "attempt": 2,
        "max_attempts": 3,
        "resume_at": 100,
        "error_code": "HTTP_TIMEOUT",
        "classification": "transient",
        "url": "https://secret.invalid/path?token=sentinel",
    }
    store.append_event("task-retry", event)
    store.append_event("task-retry", event)

    saved = [
        item
        for item in store.require("task-retry").events
        if item.get("event") == "node_retry_scheduled"
    ]
    assert len(saved) == 1
    assert saved[0]["attempt"] == 2
    assert saved[0]["max_attempts"] == 3
    assert saved[0]["classification"] == "transient"
    assert "url" not in saved[0]
    assert "sentinel" not in str(saved[0])
