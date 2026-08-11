from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    OperationState,
    Origin,
    TaskSpec,
    WorkspaceSource,
)
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError


def _store(
    tmp_path: Path,
    *,
    clock=lambda: 100.0,
    key: bytes | None = None,
) -> tuple[CodingWorkerStore, str]:
    store = CodingWorkerStore(
        tmp_path / "worker", master_key=key or Fernet.generate_key(), clock=clock
    )
    task = store.create_task(
        TaskSpec(
            client_task_id="client-01",
            origin=Origin(module="test", object_id="one"),
            objective="work",
            workspace_source=WorkspaceSource(
                kind="manifest", source_id="source-01", revision="revision-01"
            ),
            acceptance=AcceptanceContract(
                contract_id="contract-01",
                required_checks=(
                    AcceptanceCheck(check_id="pytest", label="pytest", kind="command"),
                ),
            ),
            model_route="coding/default",
        )
    )
    return store, task.task_id


def test_approval_is_durable_single_decision_and_once_lease_is_consumed(tmp_path: Path) -> None:
    store, task_id = _store(tmp_path)
    approval = store.create_approval(
        task_id=task_id,
        operation_id="operation-01",
        capability="command",
        request={"argv": ["python", "-m", "pytest"]},
    )
    same = store.create_approval(
        task_id=task_id,
        operation_id="operation-01",
        capability="command",
        request={"argv": ["python", "-m", "pytest"]},
    )
    assert same.approval_id == approval.approval_id
    decided = store.decide_approval(approval.approval_id, approved=True)
    assert decided.lease is not None and decided.lease.operation_limit == 1
    store.consume_lease(
        decided.lease.lease_id, task_id=task_id, capability="command"
    )
    with pytest.raises(WorkerConflictError) as spent:
        store.consume_lease(
            decided.lease.lease_id, task_id=task_id, capability="command"
        )
    assert spent.value.code == "lease_unavailable"
    with pytest.raises(WorkerConflictError) as replay:
        store.decide_approval(approval.approval_id, approved=False)
    assert replay.value.code == "approval_already_decided"


def test_task_lease_is_bound_to_task_capability_ttl_and_encrypted(tmp_path: Path) -> None:
    now = [100.0]
    store, task_id = _store(tmp_path, clock=lambda: now[0])
    approval = store.create_approval(
        task_id=task_id,
        operation_id="operation-network",
        capability="network",
        request={"domains": ["registry.npmjs.org"], "purpose": "install"},
    )
    decided = store.decide_approval(
        approval.approval_id, approved=True, task_scope=True, ttl_seconds=60
    )
    assert decided.lease is not None and decided.lease.operation_limit == 1024
    with pytest.raises(WorkerConflictError):
        store.consume_lease(
            decided.lease.lease_id, task_id="task_other", capability="network"
        )
    now[0] = 161.0
    with pytest.raises(WorkerConflictError):
        store.consume_lease(
            decided.lease.lease_id, task_id=task_id, capability="network"
        )
    raw = store.database_path.read_bytes()
    assert b"registry.npmjs.org" not in raw and b"install" not in raw


def test_running_operation_becomes_unknown_after_restart_and_cannot_blind_replay(
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key()
    root = tmp_path / "worker"
    store, task_id = _store(tmp_path, key=key)
    operation = store.create_operation(
        task_id=task_id,
        operation_id="operation-write",
        tool_name="write_file",
        intent_sha256="a" * 64,
        request={"path": "result.txt", "sha256": "b" * 64},
    )
    store.transition_operation(
        operation.operation_id,
        OperationState.RUNNING,
        expected_state=OperationState.PREPARED,
    )
    restarted = CodingWorkerStore(root, master_key=key)
    unknown = restarted.get_operation(operation.operation_id)
    assert unknown.state is OperationState.UNKNOWN
    with pytest.raises(WorkerConflictError):
        store.transition_operation(
            operation.operation_id,
            OperationState.RUNNING,
            expected_state=OperationState.PREPARED,
        )


def test_operation_id_binds_exact_intent_and_completed_result_is_idempotent(
    tmp_path: Path,
) -> None:
    store, task_id = _store(tmp_path)
    operation = store.create_operation(
        task_id=task_id,
        operation_id="operation-01",
        tool_name="run_check",
        intent_sha256="c" * 64,
        request={"check_id": "pytest"},
    )
    with pytest.raises(WorkerConflictError) as conflict:
        store.create_operation(
            task_id=task_id,
            operation_id="operation-01",
            tool_name="run_check",
            intent_sha256="d" * 64,
            request={"check_id": "pytest"},
        )
    assert conflict.value.code == "operation_intent_conflict"
    store.transition_operation(operation.operation_id, OperationState.RUNNING)
    completed = store.transition_operation(
        operation.operation_id,
        OperationState.COMPLETED,
        result={"exit_code": 0, "artifact_id": "artifact-01"},
    )
    assert completed.result == {"exit_code": 0, "artifact_id": "artifact-01"}
