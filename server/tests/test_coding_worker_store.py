from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.crypto import WorkerCryptoError
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError


def _spec(*, objective: str = "Secret objective", client_task_id: str = "client-01") -> TaskSpec:
    return TaskSpec(
        client_task_id=client_task_id,
        origin=Origin(module="test-module", object_id="object-01"),
        objective=objective,
        workspace_source=WorkspaceSource(
            kind="manifest", source_id="source-01", revision="head-01"
        ),
        acceptance=AcceptanceContract(
            contract_id="contract-01",
            required_checks=(
                AcceptanceCheck(check_id="pytest", label="pytest", kind="command"),
            ),
        ),
        model_route="coding/default",
    )


def test_task_intent_events_and_messages_are_encrypted_and_survive_restart(tmp_path: Path) -> None:
    root = tmp_path / "worker"
    key = Fernet.generate_key()
    store = CodingWorkerStore(root, master_key=key)
    task = store.create_task(_spec())
    same = store.create_task(_spec())
    assert same.task_id == task.task_id

    store.append_message(task.task_id, role="user", content="private user message")
    first = store.append_event(task.task_id, "plan", {"summary": "private plan"})
    second = store.append_event(task.task_id, "tool_summary", {"tool": "read"})

    restarted = CodingWorkerStore(root, master_key=key)
    loaded = restarted.get_task(task.task_id)
    assert loaded.spec.objective == "Secret objective"
    assert restarted.list_messages(task.task_id)[0].content == "private user message"
    assert [event.type for event in restarted.list_events(task.task_id, after=first.sequence)] == [
        second.type
    ]

    raw = store.database_path.read_bytes()
    assert b"Secret objective" not in raw
    assert b"private user message" not in raw
    assert b"private plan" not in raw


def test_idempotency_key_rejects_changed_intent(tmp_path: Path) -> None:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    store.create_task(_spec())
    with pytest.raises(WorkerConflictError) as raised:
        store.create_task(_spec(objective="Different"))
    assert raised.value.code == "task_intent_conflict"


def test_restart_interrupts_inflight_without_replaying_it(tmp_path: Path) -> None:
    root = tmp_path / "worker"
    key = Fernet.generate_key()
    store = CodingWorkerStore(root, master_key=key)
    task = store.create_task(_spec())
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING, provider_session_id="provider-01")

    restarted = CodingWorkerStore(root, master_key=key)
    interrupted = restarted.get_task(task.task_id)
    assert interrupted.state is TaskState.INTERRUPTED
    assert interrupted.reason == "server_restart"
    assert interrupted.provider_session_id == "provider-01"
    assert [event.payload.get("reason") for event in restarted.list_events(task.task_id)][-1] == (
        "server_restart"
    )


def test_pin_retention_cleanup_and_immediate_delete(tmp_path: Path) -> None:
    now = [100.0]
    store = CodingWorkerStore(
        tmp_path / "worker",
        retention_seconds=60,
        clock=lambda: now[0],
        master_key=Fernet.generate_key(),
    )
    pinned = store.create_task(_spec(client_task_id="pinned"))
    disposable = store.create_task(_spec(client_task_id="disposable"))
    store.set_pinned(pinned.task_id, True)
    store.transition(disposable.task_id, TaskState.CANCELLED)
    now[0] = 161.0
    assert store.cleanup_expired() == [disposable.task_id]
    assert store.get_task(pinned.task_id).pinned is True
    store.transition(pinned.task_id, TaskState.CANCELLED)
    assert store.delete_task(pinned.task_id) is True


def test_checkpoint_is_encrypted_tree_bound_and_survives_restart(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    root = tmp_path / "worker"
    store = CodingWorkerStore(root, master_key=key)
    task = store.create_task(_spec(client_task_id="checkpoint"))
    checkpoint = store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash="a" * 64,
        payload={
            "phase": "testing",
            "provider": {"checkpoint_id": "private-provider-session"},
        },
    )
    assert b"private-provider-session" not in store.database_path.read_bytes()

    restarted = CodingWorkerStore(root, master_key=key)
    loaded = restarted.latest_checkpoint(task.task_id)
    assert loaded == checkpoint
    assert loaded is not None and loaded.workspace_tree_hash == "a" * 64
    assert loaded.payload["phase"] == "testing"


def test_existing_database_without_key_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "worker"
    store = CodingWorkerStore(root, master_key=Fernet.generate_key())
    store.create_task(_spec())
    with pytest.raises(WorkerCryptoError) as raised:
        CodingWorkerStore(root)
    assert raised.value.code == "worker_key_missing"


def test_ciphertext_tampering_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "worker"
    key = Fernet.generate_key()
    store = CodingWorkerStore(root, master_key=key)
    task = store.create_task(_spec())
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE worker_tasks SET spec_ciphertext = 'invalid' WHERE task_id = ?",
            (task.task_id,),
        )
    with pytest.raises(Exception, match="corrupt"):
        store.get_task(task.task_id)
