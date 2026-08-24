from __future__ import annotations

from pathlib import Path

import pytest

from ai_research_control.store import IdempotencyConflict, RunStore


def request(key: str = "fixture:key-001", case_id: str = "success") -> dict[str, str]:
    return {
        "fixture_id": "inspect-smoke-v1",
        "case_id": case_id,
        "idempotency_key": key,
        "tenant_id": "local",
        "project_id": "local",
        "actor_id": "local",
    }


def test_idempotency_and_event_order(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "control.db")
    created, is_new = store.create_or_get(request())
    repeated, repeated_is_new = store.create_or_get(request())

    assert is_new is True
    assert repeated_is_new is False
    assert repeated["run_id"] == created["run_id"]
    assert [event["event_type"] for event in store.events(created["run_id"], 0)] == [
        "run.queued"
    ]

    with pytest.raises(IdempotencyConflict):
        store.create_or_get(request(case_id="task_error"))


def test_raw_cancel_state_is_not_overwritten(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "control.db")
    run, _ = store.create_or_get(request(case_id="long_running_cancel"))
    store.mark_running(
        run["run_id"],
        {
            "phase": "running",
            "inspectStatus": "started",
            "cancelRequested": False,
            "cancelApplied": False,
        },
    )
    store.request_cancel(run["run_id"])
    terminal = store.update_worker(
        run["run_id"],
        {
            "phase": "terminal",
            "outcome": "cancelled",
            "inspectStatus": "error",
            "cancelRequested": True,
            "cancelApplied": True,
            "errorType": "TerminateTaskError",
            "errorMessage": "Task cancelled by user (abort)",
            "replayVerified": False,
            "artifacts": {},
        },
    )

    assert terminal["outcome"] == "cancelled"
    assert terminal["inspect_status"] == "error"
    assert terminal["cancel_requested"] is True
    assert terminal["cancel_applied"] is True
    assert terminal["cancel_requested_at"] is not None
    assert terminal["cancel_applied_at"] is not None
    assert terminal["error_type"] == "TerminateTaskError"

    with store.connection() as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 2


def test_receipt_outbox_survives_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    store = RunStore(path)
    run, _ = store.create_or_get(request())
    store.mark_running(run["run_id"], {"phase": "running"})
    terminal = store.update_worker(
        run["run_id"],
        {
            "phase": "terminal",
            "outcome": "success",
            "inspectStatus": "success",
            "cancelRequested": False,
            "cancelApplied": False,
            "replayVerified": True,
            "artifacts": {},
        },
    )
    receipt = {"runId": run["run_id"], "claimLevel": "harness_only"}
    store.set_receipt(run["run_id"], receipt)

    reopened = RunStore(path)
    pending = reopened.pending_outbox()
    assert len(pending) == 1
    assert pending[0]["run_id"] == run["run_id"]
    assert pending[0]["receipt_json"] == receipt
    assert terminal["phase"] == "terminal"
