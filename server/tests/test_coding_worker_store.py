from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    RuntimeProtocol,
    SessionLedgerKind,
    TaskSpec,
    TaskState,
    TurnBarrier,
    TurnTransactionState,
    WorkspaceSource,
    WorkerQuestionAnswer,
    WorkerQuestionOption,
    WorkerSessionLedgerEntry,
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


def test_v17_turn_transaction_parks_and_rejects_new_operations(tmp_path: Path) -> None:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    task = store.create_task(_spec(), runtime_protocol=RuntimeProtocol.V17)
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING)
    tree_hash = "a" * 64
    turn = store.open_turn_transaction(
        task_id=task.task_id,
        turn_id="turn_v17_1",
        workspace_tree_hash=tree_hash,
    )
    assert turn.generation == 1
    operation = store.create_operation(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        operation_id="operation_v17_1",
        tool_name="run_command",
        intent_sha256="b" * 64,
        request={"arguments": {"argv": ["pytest"]}, "workspace_id": "workspace_1"},
    )
    assert operation.turn_id == turn.turn_id

    parking = store.begin_turn_parking(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        barrier=TurnBarrier.APPROVAL,
    )
    assert parking.state is TurnTransactionState.PARKING
    with pytest.raises(WorkerConflictError) as rejected:
        store.create_operation(
            task_id=task.task_id,
            turn_id=turn.turn_id,
            operation_id="operation_v17_2",
            tool_name="run_command",
            intent_sha256="c" * 64,
            request={"arguments": {"argv": ["pytest", "-q"]}, "workspace_id": "workspace_1"},
        )
    assert rejected.value.code == "turn_parked"
    assert (
        store.create_operation(
            task_id=task.task_id,
            turn_id=turn.turn_id,
            operation_id=operation.operation_id,
            tool_name=operation.tool_name,
            intent_sha256=operation.intent_sha256,
            request=operation.request,
        )
        == operation
    )

    checkpoint = store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash=tree_hash,
        payload={"phase": "approval"},
    )
    parked = store.park_turn_transaction(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        checkpoint_id=checkpoint.checkpoint_id,
    )
    assert parked.state is TurnTransactionState.PARKED
    resumed = store.resume_turn_transaction(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        checkpoint_id=checkpoint.checkpoint_id,
    )
    assert resumed.state is TurnTransactionState.RESUMING
    completed = store.finish_turn_transaction(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        state=TurnTransactionState.COMPLETED,
    )
    assert completed.state is TurnTransactionState.COMPLETED
    assert [event.type for event in store.list_events(task.task_id)] == [
        "task_created",
        "task_state",
        "task_state",
        "turn_started",
        "turn_parking",
        "checkpoint_created",
        "turn_parked",
        "turn_resumed",
        "turn_completed",
    ]


def test_existing_tasks_default_to_v16_and_reject_turn_transactions(tmp_path: Path) -> None:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    task = store.create_task(_spec())
    assert task.runtime_protocol is RuntimeProtocol.V16
    with pytest.raises(WorkerConflictError) as rejected:
        store.open_turn_transaction(
            task_id=task.task_id,
            turn_id="turn_legacy",
            workspace_tree_hash="d" * 64,
        )
    assert rejected.value.code == "turn_protocol_mismatch"


def test_restart_preserves_durably_parked_v17_approval_turn(tmp_path: Path) -> None:
    root = tmp_path / "worker"
    key = Fernet.generate_key()
    store = CodingWorkerStore(root, master_key=key)
    task = store.create_task(_spec(), runtime_protocol=RuntimeProtocol.V17)
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING)
    turn = store.open_turn_transaction(
        task_id=task.task_id,
        turn_id="turn_v17_restart",
        workspace_tree_hash="a" * 64,
    )
    store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TURN_STARTED,
        turn_id=turn.turn_id,
        payload={},
    )
    approval = store.create_approval(
        task_id=task.task_id,
        operation_id="operation_v17_restart",
        capability="command",
        request={"argv": ["pytest"]},
    )
    store.begin_turn_parking(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        barrier=TurnBarrier.APPROVAL,
    )
    with pytest.raises(WorkerConflictError) as too_early:
        store.decide_approval(approval.approval_id, approved=True)
    assert too_early.value.code == "task_state_conflict"
    assert store.get_approval(approval.approval_id).status.value == "pending"
    checkpoint = store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash="b" * 64,
        payload={"phase": "waiting_approval"},
    )
    store.park_turn_transaction(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        checkpoint_id=checkpoint.checkpoint_id,
    )
    store.transition(task.task_id, TaskState.WAITING_APPROVAL)

    restarted = CodingWorkerStore(root, master_key=key)

    assert restarted.get_task(task.task_id).state is TaskState.WAITING_APPROVAL
    parked = restarted.current_turn_transaction(task.task_id)
    assert parked is not None
    assert parked.state is TurnTransactionState.PARKED
    assert parked.workspace_tree_hash == "b" * 64
    assert [item.kind for item in restarted.list_session_ledger(task.task_id)] == [
        SessionLedgerKind.TURN_STARTED
    ]
    decided = restarted.decide_approval(approval.approval_id, approved=True)
    assert decided.status.value == "approved"
    assert restarted.get_task(task.task_id).state is TaskState.QUEUED
    resuming = restarted.current_turn_transaction(task.task_id)
    assert resuming is not None
    assert resuming.state is TurnTransactionState.RESUMING


def test_v17_question_resolution_atomically_resumes_parked_turn(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    task = store.create_task(_spec(), runtime_protocol=RuntimeProtocol.V17)
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING)
    turn = store.open_turn_transaction(
        task_id=task.task_id,
        turn_id="turn_v17_input",
        workspace_tree_hash="a" * 64,
    )
    store.create_question(
        task_id=task.task_id,
        question_id="question_v17_input",
        turn_id=turn.turn_id,
        prompt="Choose a repair.",
        options=(
            WorkerQuestionOption(option_id="safe", label="Safe repair"),
        ),
    )
    store.begin_turn_parking(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        barrier=TurnBarrier.INPUT,
    )
    checkpoint = store.create_checkpoint(
        task_id=task.task_id,
        workspace_tree_hash="a" * 64,
        payload={"phase": "waiting_input"},
    )
    store.park_turn_transaction(
        task_id=task.task_id,
        turn_id=turn.turn_id,
        checkpoint_id=checkpoint.checkpoint_id,
    )
    store.transition(task.task_id, TaskState.WAITING_INPUT)

    resolved = store.resolve_question(
        task.task_id,
        "question_v17_input",
        WorkerQuestionAnswer(option_id="safe"),
    )

    assert resolved.status.value == "resolved"
    assert store.get_task(task.task_id).state is TaskState.QUEUED
    transaction = store.current_turn_transaction(task.task_id)
    assert transaction is not None
    assert transaction.state is TurnTransactionState.RESUMING


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


def test_session_ledger_is_encrypted_and_restart_closes_unknown_boundaries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "worker"
    key = Fernet.generate_key()
    store = CodingWorkerStore(root, master_key=key)
    task = store.create_task(_spec(client_task_id="ledger-restart"))
    store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TURN_STARTED,
        turn_id="turn-01",
        payload={},
    )
    store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TOOL_STARTED,
        turn_id="turn-01",
        operation_id="operation-01",
        payload={"tool_name": "run_check", "summary": "private tool summary"},
    )

    assert b"private tool summary" not in store.database_path.read_bytes()
    restarted = CodingWorkerStore(root, master_key=key)
    ledger = restarted.list_session_ledger(task.task_id)
    assert [entry.kind for entry in ledger] == [
        SessionLedgerKind.TURN_STARTED,
        SessionLedgerKind.TOOL_STARTED,
        SessionLedgerKind.TOOL_FINISHED,
        SessionLedgerKind.TURN_FINISHED,
    ]
    assert ledger[-2].operation_id == "operation-01"
    assert ledger[-2].payload["result_state"] == "unknown"
    assert ledger[-1].payload["result_state"] == "interrupted"


def test_session_ledger_rejects_hidden_provider_fields_and_unpaired_completion(
    tmp_path: Path,
) -> None:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    task = store.create_task(_spec(client_task_id="ledger-reject"))
    store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TURN_STARTED,
        turn_id="turn-01",
        payload={},
    )
    with pytest.raises(ValueError, match="canonical"):
        WorkerSessionLedgerEntry(
            ledger_id="ledger-01",
            task_id=task.task_id,
            sequence=1,
            kind=SessionLedgerKind.COMPACTION,
            turn_id="turn-01",
            payload={
                "summary": "public summary",
                "boundary_sequence": 1,
                "raw_frame": {"hidden_reasoning": "do not store"},
            },
            created_at=1,
        )
    with pytest.raises(WorkerConflictError) as raised:
        store.append_session_ledger(
            task.task_id,
            kind=SessionLedgerKind.TOOL_FINISHED,
            turn_id="turn-01",
            operation_id="missing-operation",
            payload={
                "tool_name": "run_check",
                "summary": "unbound result",
                "result_state": "succeeded",
                "artifact_id": None,
            },
        )
    assert raised.value.code == "session_tool_boundary_conflict"


def test_session_ledger_tool_boundaries_are_exactly_idempotent(tmp_path: Path) -> None:
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    task = store.create_task(_spec(client_task_id="ledger-idempotent"))
    store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TURN_STARTED,
        turn_id="turn-01",
        payload={},
    )
    start = store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TOOL_STARTED,
        turn_id="turn-01",
        operation_id="operation-01",
        payload={"tool_name": "run_check", "summary": "run check"},
    )
    replayed_start = store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TOOL_STARTED,
        turn_id="turn-01",
        operation_id="operation-01",
        payload={"tool_name": "run_check", "summary": "run check"},
    )
    assert replayed_start == start
    finish = store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TOOL_FINISHED,
        turn_id="turn-01",
        operation_id="operation-01",
        payload={
            "tool_name": "run_check",
            "summary": "check complete",
            "result_state": "succeeded",
            "artifact_id": None,
        },
    )
    replayed_finish = store.append_session_ledger(
        task.task_id,
        kind=SessionLedgerKind.TOOL_FINISHED,
        turn_id="turn-01",
        operation_id="operation-01",
        payload={
            "tool_name": "run_check",
            "summary": "check complete",
            "result_state": "succeeded",
            "artifact_id": None,
        },
    )
    assert replayed_finish == finish
    with pytest.raises(WorkerConflictError):
        store.append_session_ledger(
            task.task_id,
            kind=SessionLedgerKind.TOOL_FINISHED,
            turn_id="turn-01",
            operation_id="operation-01",
            payload={
                "tool_name": "run_check",
                "summary": "different result",
                "result_state": "failed",
                "artifact_id": None,
            },
        )
    assert len(store.list_session_ledger(task.task_id)) == 3
