from __future__ import annotations

import base64
import hashlib
import math
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts import (
    ApprovalStatus,
    BUDGET_ACTIVE_STATES,
    CapabilityName,
    EvidenceStatus,
    CapabilityLease,
    OperationState,
    TERMINAL_STATES,
    Origin,
    SubtaskKind,
    SubtaskMergeState,
    SubtaskRecord,
    TaskRecord,
    TaskSpec,
    TaskState,
    WorkerEvent,
    WorkerEvidence,
    WorkerArtifact,
    WorkerApproval,
    WorkerBudgetUsage,
    WorkerCheckpoint,
    WorkerMessage,
    WorkerPlan,
    WorkerPlanItem,
    WorkerQuestion,
    WorkerTurnCheckpoint,
    WorkerTurnHistory,
    WorkerQuestionAnswer,
    WorkerQuestionOption,
    QuestionStatus,
    WorkerOperation,
    WorkerSessionLedgerEntry,
    SessionLedgerKind,
    require_transition,
)
from .crypto import WorkerCryptoError, WorkerEncryptedCodec


DEFAULT_RETENTION_SECONDS = 7 * 24 * 60 * 60
MIN_RETENTION_SECONDS = 60
MAX_RETENTION_SECONDS = 30 * 24 * 60 * 60
ACTIVE_ON_RESTART = (
    TaskState.PREPARING,
    TaskState.RUNNING,
    TaskState.WAITING_APPROVAL,
    TaskState.TESTING,
)


class WorkerStoreError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class WorkerNotFoundError(WorkerStoreError):
    pass


class WorkerConflictError(WorkerStoreError):
    pass


class CodingWorkerStore:
    """Encrypted SQLite task/event store with durable idempotency and replay."""

    def __init__(
        self,
        storage_root: Path,
        *,
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        clock: Callable[[], float] = time.time,
        master_key: bytes | str | None = None,
    ) -> None:
        if (
            isinstance(retention_seconds, bool)
            or not isinstance(retention_seconds, int)
            or not MIN_RETENTION_SECONDS <= retention_seconds <= MAX_RETENTION_SECONDS
        ):
            raise ValueError("Worker retention is outside the allowed range")
        self.root = Path(storage_root)
        self.database_path = self.root / "coding-worker.sqlite3"
        self.workspaces_root = self.root / "workspaces"
        self.retention_seconds = retention_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._codec = WorkerEncryptedCodec(self.root, master_key=master_key)
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.mark_open_session_boundaries_interrupted()
        self.mark_inflight_interrupted()
        self.mark_inflight_operations_unknown()

    def create_task(self, spec: TaskSpec) -> TaskRecord:
        now = self._now()
        expires_at = now + self.retention_seconds
        task_id = f"task_{uuid.uuid4().hex}"
        encrypted_spec = self._codec.encrypt(spec.model_dump(mode="json"))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM worker_tasks
                WHERE origin_module = ? AND origin_object_id = ? AND client_task_id = ?
                """,
                (spec.origin.module, spec.origin.object_id, spec.client_task_id),
            ).fetchone()
            if existing is not None:
                current = self._task(existing, connection)
                if current.spec != spec:
                    raise WorkerConflictError(
                        "The idempotency key is already bound to another task.",
                        code="task_intent_conflict",
                    )
                return current
            connection.execute(
                """
                INSERT INTO worker_tasks (
                    task_id, origin_module, origin_object_id, client_task_id,
                    state, spec_ciphertext, created_at, updated_at, expires_at, pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    task_id,
                    spec.origin.module,
                    spec.origin.object_id,
                    spec.client_task_id,
                    TaskState.QUEUED.value,
                    encrypted_spec,
                    now,
                    now,
                    expires_at,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="task_created",
                payload={"state": TaskState.QUEUED.value},
                created_at=now,
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._connect() as connection:
            row = self._require_task_row(connection, task_id)
            return self._task(row, connection)

    def list_tasks(self, *, origin: Origin | None = None) -> list[TaskRecord]:
        query = "SELECT * FROM worker_tasks"
        params: tuple[Any, ...] = ()
        if origin is not None:
            query += " WHERE origin_module = ? AND origin_object_id = ?"
            params = (origin.module, origin.object_id)
        query += " ORDER BY created_at DESC, task_id"
        with self._connect() as connection:
            return [self._task(row, connection) for row in connection.execute(query, params)]

    def get_fork(self, parent_task_id: str, client_fork_id: str) -> TaskRecord | None:
        with self._connect() as connection:
            self._require_task_row(connection, parent_task_id)
            row = connection.execute(
                """
                SELECT child.* FROM worker_task_forks AS fork
                JOIN worker_tasks AS child ON child.task_id = fork.child_task_id
                WHERE fork.parent_task_id = ? AND fork.client_fork_id = ?
                """,
                (parent_task_id, client_fork_id),
            ).fetchone()
            return self._task(row, connection) if row is not None else None

    def list_children(self, parent_task_id: str) -> list[TaskRecord]:
        with self._connect() as connection:
            self._require_task_row(connection, parent_task_id)
            rows = connection.execute(
                """
                SELECT child.* FROM worker_task_forks AS fork
                JOIN worker_tasks AS child ON child.task_id = fork.child_task_id
                WHERE fork.parent_task_id = ? ORDER BY fork.created_at, child.task_id
                """,
                (parent_task_id,),
            ).fetchall()
            return [self._task(row, connection) for row in rows]

    def get_subtask(
        self, parent_task_id: str, client_subtask_id: str
    ) -> SubtaskRecord | None:
        with self._connect() as connection:
            self._require_task_row(connection, parent_task_id)
            row = connection.execute(
                """
                SELECT * FROM worker_subtasks
                WHERE parent_task_id = ? AND client_subtask_id = ?
                """,
                (parent_task_id, client_subtask_id),
            ).fetchone()
            return self._subtask(row) if row is not None else None

    def subtask_for_child(self, child_task_id: str) -> SubtaskRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_subtasks WHERE child_task_id = ?",
                (child_task_id,),
            ).fetchone()
            return self._subtask(row) if row is not None else None

    def list_subtasks(self, parent_task_id: str) -> list[SubtaskRecord]:
        with self._connect() as connection:
            self._require_task_row(connection, parent_task_id)
            rows = connection.execute(
                """
                SELECT * FROM worker_subtasks
                WHERE parent_task_id = ? ORDER BY created_at, child_task_id
                """,
                (parent_task_id,),
            ).fetchall()
            return [self._subtask(row) for row in rows]

    def create_subtask_task(
        self,
        *,
        parent_task_id: str,
        client_subtask_id: str,
        kind: SubtaskKind,
        objective: str,
        spec: TaskSpec,
        workspace_id: str,
        base_tree_hash: str,
    ) -> SubtaskRecord:
        now = self._now()
        expires_at = now + self.retention_seconds
        task_id = f"task_{uuid.uuid4().hex}"
        encrypted_spec = self._codec.encrypt(spec.model_dump(mode="json"))
        encrypted_objective = self._codec.encrypt(objective)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, parent_task_id)
            if connection.execute(
                "SELECT 1 FROM worker_subtasks WHERE child_task_id = ?",
                (parent_task_id,),
            ).fetchone() is not None:
                raise WorkerConflictError(
                    "Subtasks cannot create nested subtasks.",
                    code="subtask_depth_exceeded",
                )
            existing = connection.execute(
                """
                SELECT * FROM worker_subtasks
                WHERE parent_task_id = ? AND client_subtask_id = ?
                """,
                (parent_task_id, client_subtask_id),
            ).fetchone()
            if existing is not None:
                current = self._subtask(existing)
                if (
                    current.kind is not kind
                    or current.objective != objective
                    or current.base_tree_hash != base_tree_hash
                ):
                    raise WorkerConflictError(
                        "Subtask id is bound to another intent.",
                        code="subtask_intent_conflict",
                    )
                return current
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_subtasks WHERE parent_task_id = ?",
                    (parent_task_id,),
                ).fetchone()[0]
            )
            if count >= 4:
                raise WorkerConflictError(
                    "A task can create at most four subtasks.",
                    code="subtask_limit_exceeded",
                )
            connection.execute(
                """
                INSERT INTO worker_tasks (
                    task_id, origin_module, origin_object_id, client_task_id,
                    state, spec_ciphertext, workspace_id, created_at,
                    updated_at, expires_at, pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    task_id,
                    spec.origin.module,
                    spec.origin.object_id,
                    spec.client_task_id,
                    TaskState.QUEUED.value,
                    encrypted_spec,
                    workspace_id,
                    now,
                    now,
                    expires_at,
                ),
            )
            merge_state = (
                SubtaskMergeState.PENDING
                if kind is SubtaskKind.IMPLEMENT
                else SubtaskMergeState.NOT_APPLICABLE
            )
            connection.execute(
                """
                INSERT INTO worker_subtasks (
                    parent_task_id, client_subtask_id, child_task_id, kind,
                    objective_ciphertext, base_tree_hash, merge_state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_task_id,
                    client_subtask_id,
                    task_id,
                    kind.value,
                    encrypted_objective,
                    base_tree_hash,
                    merge_state.value,
                    now,
                    now,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="task_created",
                payload={"state": TaskState.QUEUED.value, "subtask": True},
                created_at=now,
            )
            self._append_event_locked(
                connection,
                task_id=parent_task_id,
                event_type="subtask_created",
                payload={
                    "child_task_id": task_id,
                    "kind": kind.value,
                    "base_tree_hash": base_tree_hash,
                },
                created_at=now,
            )
        return self.get_subtask(parent_task_id, client_subtask_id)  # type: ignore[return-value]

    def finish_subtask(
        self,
        child_task_id: str,
        *,
        result_tree_hash: str,
        changed_paths: tuple[str, ...],
        summary: str,
        failed: bool = False,
    ) -> SubtaskRecord:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM worker_subtasks WHERE child_task_id = ?",
                (child_task_id,),
            ).fetchone()
            if row is None:
                raise WorkerNotFoundError(
                    "Subtask was not found.", code="subtask_not_found"
                )
            current = self._subtask(row)
            target = (
                SubtaskMergeState.FAILED
                if failed
                else SubtaskMergeState.READY
                if current.kind is SubtaskKind.IMPLEMENT
                else SubtaskMergeState.NOT_APPLICABLE
            )
            if current.result_tree_hash is not None:
                if (
                    current.result_tree_hash != result_tree_hash
                    or current.changed_paths != changed_paths
                    or current.summary != summary
                    or current.merge_state is not target
                ):
                    raise WorkerConflictError(
                        "Subtask result changed.", code="subtask_result_conflict"
                    )
                return current
            connection.execute(
                """
                UPDATE worker_subtasks SET result_tree_hash = ?,
                    changed_paths_ciphertext = ?, summary_ciphertext = ?,
                    merge_state = ?, updated_at = ? WHERE child_task_id = ?
                """,
                (
                    result_tree_hash,
                    self._codec.encrypt(list(changed_paths)),
                    self._codec.encrypt(summary),
                    target.value,
                    now,
                    child_task_id,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=current.parent_task_id,
                event_type="subtask_completed" if not failed else "subtask_failed",
                payload={
                    "child_task_id": child_task_id,
                    "kind": current.kind.value,
                    "merge_state": target.value,
                    "result_tree_hash": result_tree_hash,
                    "changed_paths": list(changed_paths),
                },
                created_at=now,
            )
        return self.subtask_for_child(child_task_id)  # type: ignore[return-value]

    def begin_subtask_merge(
        self, parent_task_id: str, child_task_id: str, operation_id: str
    ) -> SubtaskRecord:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM worker_subtasks WHERE child_task_id = ?",
                (child_task_id,),
            ).fetchone()
            if row is None:
                raise WorkerNotFoundError(
                    "Subtask was not found.", code="subtask_not_found"
                )
            current = self._subtask(row)
            if current.parent_task_id != parent_task_id:
                raise WorkerConflictError(
                    "Subtask parent binding changed.", code="subtask_merge_conflict"
                )
            if current.kind is not SubtaskKind.IMPLEMENT:
                raise WorkerConflictError(
                    "Read-only subtasks cannot be merged.", code="subtask_not_mergeable"
                )
            if current.merge_state in {
                SubtaskMergeState.MERGED,
                SubtaskMergeState.CONFLICTED,
            }:
                if current.merge_operation_id != operation_id:
                    raise WorkerConflictError(
                        "Subtask merge operation changed.",
                        code="subtask_merge_conflict",
                    )
                return current
            if current.merge_state is not SubtaskMergeState.READY:
                raise WorkerConflictError(
                    "Subtask is not ready to merge.", code="subtask_not_mergeable"
                )
            if current.merge_operation_id is not None:
                if current.merge_operation_id != operation_id:
                    raise WorkerConflictError(
                        "Subtask merge operation changed.",
                        code="subtask_merge_conflict",
                    )
                return current
            connection.execute(
                """
                UPDATE worker_subtasks SET merge_operation_id = ?, updated_at = ?
                WHERE child_task_id = ? AND merge_operation_id IS NULL
                """,
                (operation_id, now, child_task_id),
            )
            self._append_event_locked(
                connection,
                task_id=parent_task_id,
                event_type="changeset_merge_started",
                payload={"child_task_id": child_task_id},
                created_at=now,
            )
        return self.subtask_for_child(child_task_id)  # type: ignore[return-value]

    def settle_subtask_merge(
        self,
        parent_task_id: str,
        child_task_id: str,
        operation_id: str,
        *,
        merge_state: SubtaskMergeState,
        merged_tree_hash: str,
    ) -> SubtaskRecord:
        if merge_state not in {
            SubtaskMergeState.MERGED,
            SubtaskMergeState.CONFLICTED,
        }:
            raise ValueError("subtask merge settlement is invalid")
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM worker_subtasks WHERE child_task_id = ?",
                (child_task_id,),
            ).fetchone()
            if row is None:
                raise WorkerNotFoundError(
                    "Subtask was not found.", code="subtask_not_found"
                )
            current = self._subtask(row)
            if (
                current.parent_task_id != parent_task_id
                or current.merge_operation_id != operation_id
            ):
                raise WorkerConflictError(
                    "Subtask merge binding changed.", code="subtask_merge_conflict"
                )
            if current.merge_state in {
                SubtaskMergeState.MERGED,
                SubtaskMergeState.CONFLICTED,
            }:
                if (
                    current.merge_state is not merge_state
                    or current.merged_tree_hash != merged_tree_hash
                ):
                    raise WorkerConflictError(
                        "Subtask merge result changed.", code="subtask_merge_conflict"
                    )
                return current
            if current.merge_state is not SubtaskMergeState.READY:
                raise WorkerConflictError(
                    "Subtask is not ready to merge.", code="subtask_not_mergeable"
                )
            connection.execute(
                """
                UPDATE worker_subtasks SET merge_state = ?, merged_tree_hash = ?,
                    updated_at = ? WHERE child_task_id = ?
                """,
                (merge_state.value, merged_tree_hash, now, child_task_id),
            )
            self._append_event_locked(
                connection,
                task_id=parent_task_id,
                event_type=(
                    "changeset_merged"
                    if merge_state is SubtaskMergeState.MERGED
                    else "changeset_conflicted"
                ),
                payload={
                    "child_task_id": child_task_id,
                    "merged_tree_hash": merged_tree_hash,
                },
                created_at=now,
            )
        return self.subtask_for_child(child_task_id)  # type: ignore[return-value]

    def create_fork_task(
        self,
        *,
        parent_task_id: str,
        client_fork_id: str,
        spec: TaskSpec,
        workspace_id: str,
        parent_cursor: int,
        parent_tree_hash: str,
    ) -> TaskRecord:
        now = self._now()
        expires_at = now + self.retention_seconds
        task_id = f"task_{uuid.uuid4().hex}"
        encrypted_spec = self._codec.encrypt(spec.model_dump(mode="json"))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, parent_task_id)
            existing = connection.execute(
                """
                SELECT child.* FROM worker_task_forks AS fork
                JOIN worker_tasks AS child ON child.task_id = fork.child_task_id
                WHERE fork.parent_task_id = ? AND fork.client_fork_id = ?
                """,
                (parent_task_id, client_fork_id),
            ).fetchone()
            if existing is not None:
                child = self._task(existing, connection)
                if child.spec != spec:
                    raise WorkerConflictError(
                        "Fork id is bound to another public context.",
                        code="task_fork_conflict",
                    )
                return child
            connection.execute(
                """
                INSERT INTO worker_tasks (
                    task_id, origin_module, origin_object_id, client_task_id,
                    state, spec_ciphertext, workspace_id, created_at,
                    updated_at, expires_at, pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    task_id,
                    spec.origin.module,
                    spec.origin.object_id,
                    spec.client_task_id,
                    TaskState.PAUSED.value,
                    encrypted_spec,
                    workspace_id,
                    now,
                    now,
                    expires_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO worker_task_forks (
                    parent_task_id, client_fork_id, child_task_id,
                    parent_cursor, parent_tree_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_task_id,
                    client_fork_id,
                    task_id,
                    parent_cursor,
                    parent_tree_hash,
                    now,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="task_created",
                payload={"state": TaskState.PAUSED.value, "forked": True},
                created_at=now,
            )
            self._append_event_locked(
                connection,
                task_id=parent_task_id,
                event_type="task_forked",
                payload={"child_task_id": task_id, "cursor": parent_cursor},
                created_at=now,
            )
        return self.get_task(task_id)

    def list_queued_tasks(self, *, limit: int = 100) -> list[TaskRecord]:
        if not 1 <= limit <= 1000:
            raise ValueError("invalid queued task limit")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM worker_tasks WHERE state = ?
                ORDER BY created_at, task_id LIMIT ?
                """,
                (TaskState.QUEUED.value, limit),
            ).fetchall()
            return [self._task(row, connection) for row in rows]

    def transition(
        self,
        task_id: str,
        target: TaskState,
        *,
        reason: str | None = None,
        workspace_id: str | None = None,
        provider_session_id: str | None = None,
        expected_state: TaskState | None = None,
    ) -> TaskRecord:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_task_row(connection, task_id)
            current = TaskState(row["state"])
            if expected_state is not None and current is not expected_state:
                raise WorkerConflictError(
                    "Task state changed before the requested transition.",
                    code="task_state_conflict",
                )
            try:
                require_transition(current, target)
            except ValueError as exc:
                raise WorkerConflictError(str(exc), code="task_state_conflict") from exc
            encrypted_reason = self._codec.encrypt(reason) if reason is not None else None
            encrypted_provider = (
                self._codec.encrypt(provider_session_id)
                if provider_session_id is not None
                else row["provider_session_ciphertext"]
            )
            connection.execute(
                """
                UPDATE worker_tasks SET state = ?, reason_ciphertext = ?,
                    workspace_id = COALESCE(?, workspace_id),
                    provider_session_ciphertext = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    target.value,
                    encrypted_reason,
                    workspace_id,
                    encrypted_provider,
                    now,
                    task_id,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="task_state",
                payload={"from": current.value, "to": target.value, "reason": reason},
                created_at=now,
            )
        return self.get_task(task_id)

    def append_event(
        self, task_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> WorkerEvent:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            sequence = self._append_event_locked(
                connection,
                task_id=task_id,
                event_type=event_type,
                payload=payload or {},
                created_at=now,
            )
        return WorkerEvent(
            sequence=sequence,
            task_id=task_id,
            type=event_type,
            payload=payload or {},
            created_at=now,
        )

    def list_events(self, task_id: str, *, after: int = 0, limit: int = 500) -> list[WorkerEvent]:
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid event replay window")
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                """
                SELECT * FROM worker_events
                WHERE task_id = ? AND sequence > ? ORDER BY sequence LIMIT ?
                """,
                (task_id, after, limit),
            ).fetchall()
        return [
            WorkerEvent(
                sequence=int(row["sequence"]),
                task_id=task_id,
                type=str(row["type"]),
                payload=self._decrypt_dict(row["payload_ciphertext"]),
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    def active_runtime_seconds(self, task_id: str) -> float:
        """Return durable active time, excluding queue and human wait intervals."""
        with self._lock:
            task = self.get_task(task_id)
            total = 0.0
            state = TaskState.QUEUED
            last_at = task.created_at
            cursor = 0
            while True:
                events = self.list_events(task_id, after=cursor, limit=1000)
                if not events:
                    break
                for event in events:
                    if event.type != "task_state":
                        continue
                    try:
                        source = TaskState(str(event.payload["from"]))
                        target = TaskState(str(event.payload["to"]))
                    except (KeyError, ValueError) as exc:
                        raise WorkerStoreError(
                            "Worker state history is corrupt.", code="worker_data_corrupt"
                        ) from exc
                    if source is not state or event.created_at < last_at:
                        raise WorkerStoreError(
                            "Worker state history is inconsistent.",
                            code="worker_data_corrupt",
                        )
                    if state in BUDGET_ACTIVE_STATES:
                        total += event.created_at - last_at
                    state = target
                    last_at = event.created_at
                cursor = events[-1].sequence
                if len(events) < 1000:
                    break
            if state is not task.state:
                raise WorkerStoreError(
                    "Worker state history does not match the task.",
                    code="worker_data_corrupt",
                )
            now = self._now()
            if now < last_at:
                raise WorkerStoreError(
                    "Worker state clock moved backwards.", code="worker_data_corrupt"
                )
            if state in BUDGET_ACTIVE_STATES:
                total += now - last_at
            return total

    def budget_usage(self, task_id: str) -> WorkerBudgetUsage:
        active_seconds = self.active_runtime_seconds(task_id)
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            tool_calls = int(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_operations WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            )
            ledger_turns = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM worker_session_ledger
                    WHERE task_id = ? AND kind = ?
                    """,
                    (task_id, SessionLedgerKind.TURN_STARTED.value),
                ).fetchone()[0]
            )
            if ledger_turns:
                turns_started = ledger_turns
            else:
                turns_started = 0
                rows = connection.execute(
                    """
                    SELECT payload_ciphertext FROM worker_events
                    WHERE task_id = ? AND type = 'provider_event'
                    ORDER BY sequence
                    """,
                    (task_id,),
                ).fetchall()
                for row in rows:
                    if self._decrypt_dict(row["payload_ciphertext"]).get("kind") == "turn_completed":
                        turns_started += 1
        return WorkerBudgetUsage(
            active_seconds=active_seconds,
            turns_started=turns_started,
            tool_calls=tool_calls,
        )

    def append_message(self, task_id: str, *, role: str, content: str) -> WorkerMessage:
        if role not in {"user", "assistant", "tool", "system"} or not content.strip():
            raise ValueError("invalid worker message")
        now = self._now()
        message_id = f"message_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            next_sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM worker_messages WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO worker_messages (
                    message_id, task_id, sequence, role, content_ciphertext, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, task_id, next_sequence, role, self._codec.encrypt(content), now),
            )
            self._append_session_ledger_locked(
                connection,
                task_id=task_id,
                kind=SessionLedgerKind.PUBLIC_MESSAGE,
                turn_id=None,
                operation_id=None,
                payload={"role": role, "text": content},
                created_at=now,
            )
        return WorkerMessage(
            message_id=message_id,
            task_id=task_id,
            sequence=next_sequence,
            role=role,  # type: ignore[arg-type]
            content=content,
            created_at=now,
        )

    def list_messages(self, task_id: str) -> list[WorkerMessage]:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM worker_messages WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return [
            WorkerMessage(
                message_id=str(row["message_id"]),
                task_id=task_id,
                sequence=int(row["sequence"]),
                role=str(row["role"]),  # type: ignore[arg-type]
                content=self._decrypt_string(row["content_ciphertext"]),
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    def append_session_ledger(
        self,
        task_id: str,
        *,
        kind: SessionLedgerKind,
        payload: dict[str, Any],
        turn_id: str | None = None,
        operation_id: str | None = None,
    ) -> WorkerSessionLedgerEntry:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            return self._append_session_ledger_locked(
                connection,
                task_id=task_id,
                kind=kind,
                payload=payload,
                turn_id=turn_id,
                operation_id=operation_id,
                created_at=now,
            )

    def finish_session_turn(
        self,
        task_id: str,
        *,
        turn_id: str,
        result_state: str,
        turn_checkpoint: dict[str, Any] | None = None,
    ) -> list[WorkerSessionLedgerEntry]:
        if result_state not in {
            "completed",
            "cancelled",
            "failed",
            "interrupted",
            "waiting_input",
        }:
            raise ValueError("invalid session turn result")
        if turn_checkpoint is not None and result_state != "completed":
            raise ValueError("turn checkpoint is only valid for a completed turn")
        now = self._now()
        appended: list[WorkerSessionLedgerEntry] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_open_turn(connection, task_id, turn_id)
            open_tools = connection.execute(
                """
                SELECT started.operation_id, started.payload_ciphertext
                FROM worker_session_ledger AS started
                LEFT JOIN worker_session_ledger AS finished
                  ON finished.task_id = started.task_id
                 AND finished.operation_id = started.operation_id
                 AND finished.kind = ?
                WHERE started.task_id = ? AND started.turn_id = ?
                  AND started.kind = ? AND finished.ledger_id IS NULL
                ORDER BY started.sequence
                """,
                (
                    SessionLedgerKind.TOOL_FINISHED.value,
                    task_id,
                    turn_id,
                    SessionLedgerKind.TOOL_STARTED.value,
                ),
            ).fetchall()
            if open_tools and result_state in {"completed", "waiting_input"}:
                raise WorkerConflictError(
                    "A completed turn cannot contain an unfinished tool call.",
                    code="session_tool_boundary_incomplete",
                )
            for row in open_tools:
                started = self._decrypt_dict(row["payload_ciphertext"])
                appended.append(
                    self._append_session_ledger_locked(
                        connection,
                        task_id=task_id,
                        kind=SessionLedgerKind.TOOL_FINISHED,
                        turn_id=turn_id,
                        operation_id=str(row["operation_id"]),
                        payload={
                            "tool_name": started["tool_name"],
                            "summary": "Completion receipt was not observed before interruption.",
                            "result_state": "unknown",
                            "artifact_id": None,
                        },
                        created_at=now,
                    )
                )
            finished = self._append_session_ledger_locked(
                connection,
                task_id=task_id,
                kind=SessionLedgerKind.TURN_FINISHED,
                turn_id=turn_id,
                payload={"result_state": result_state},
                operation_id=None,
                created_at=now,
            )
            appended.append(finished)
            if turn_checkpoint is not None:
                self._insert_turn_checkpoint_locked(
                    connection,
                    task_id=task_id,
                    turn_id=turn_id,
                    before_tree_hash=turn_checkpoint["before_tree_hash"],
                    before_tree_oid=turn_checkpoint["before_tree_oid"],
                    after_tree_hash=turn_checkpoint["after_tree_hash"],
                    after_tree_oid=turn_checkpoint["after_tree_oid"],
                    before_public_context=turn_checkpoint.get(
                        "before_public_context", {}
                    ),
                    after_public_context=turn_checkpoint.get(
                        "after_public_context", {}
                    ),
                    ledger_sequence=finished.sequence,
                    created_at=now,
                )
        return appended

    def list_session_ledger(
        self, task_id: str, *, after: int = 0, limit: int = 500
    ) -> list[WorkerSessionLedgerEntry]:
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("invalid session ledger replay window")
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                """
                SELECT * FROM worker_session_ledger
                WHERE task_id = ? AND sequence > ? ORDER BY sequence LIMIT ?
                """,
                (task_id, after, limit),
            ).fetchall()
        return [self._session_ledger_entry(row) for row in rows]

    def latest_session_entry(
        self, task_id: str, kind: SessionLedgerKind
    ) -> WorkerSessionLedgerEntry | None:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            row = connection.execute(
                """
                SELECT * FROM worker_session_ledger
                WHERE task_id = ? AND kind = ? ORDER BY sequence DESC LIMIT 1
                """,
                (task_id, kind.value),
            ).fetchone()
        return self._session_ledger_entry(row) if row is not None else None

    def session_tool_boundary_sequence(self, task_id: str, turn_id: str) -> int:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            self._require_open_turn(connection, task_id, turn_id)
            open_tool = connection.execute(
                """
                SELECT 1
                FROM worker_session_ledger AS started
                LEFT JOIN worker_session_ledger AS finished
                  ON finished.task_id = started.task_id
                 AND finished.operation_id = started.operation_id
                 AND finished.kind = ?
                WHERE started.task_id = ? AND started.turn_id = ?
                  AND started.kind = ? AND finished.ledger_id IS NULL
                LIMIT 1
                """,
                (
                    SessionLedgerKind.TOOL_FINISHED.value,
                    task_id,
                    turn_id,
                    SessionLedgerKind.TOOL_STARTED.value,
                ),
            ).fetchone()
            if open_tool is not None:
                raise WorkerConflictError(
                    "Context compaction requires a complete tool boundary.",
                    code="session_tool_boundary_incomplete",
                )
            return int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0)
                    FROM worker_session_ledger WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()[0]
            )

    def latest_plan(self, task_id: str) -> WorkerPlan | None:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            row = connection.execute(
                """
                SELECT * FROM worker_session_ledger
                WHERE task_id = ? AND kind = ? ORDER BY sequence DESC LIMIT 1
                """,
                (task_id, SessionLedgerKind.PLAN.value),
            ).fetchone()
        if row is None:
            return None
        entry = self._session_ledger_entry(row)
        if entry.turn_id is None:
            raise WorkerStoreError(
                "Worker plan data is corrupt.", code="worker_data_corrupt"
            )
        return WorkerPlan(
            task_id=task_id,
            sequence=entry.sequence,
            turn_id=entry.turn_id,
            explanation=entry.payload["explanation"],
            items=tuple(
                WorkerPlanItem.model_validate(item) for item in entry.payload["items"]
            ),
            updated_at=entry.created_at,
        )

    def create_question(
        self,
        *,
        task_id: str,
        question_id: str,
        turn_id: str,
        prompt: str,
        options: tuple[WorkerQuestionOption, ...],
    ) -> WorkerQuestion:
        now = self._now()
        pending = WorkerQuestion(
            task_id=task_id,
            question_id=question_id,
            turn_id=turn_id,
            status=QuestionStatus.PENDING,
            prompt=prompt,
            options=options,
            created_at=now,
        )
        request = {
            "prompt": pending.prompt,
            "options": [item.model_dump(mode="json") for item in pending.options],
        }
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task_row = self._require_task_row(connection, task_id)
            if TaskState(task_row["state"]) is not TaskState.RUNNING:
                raise WorkerConflictError(
                    "Task is not accepting a provider question.",
                    code="task_state_conflict",
                )
            existing = connection.execute(
                "SELECT * FROM worker_questions WHERE task_id = ? AND question_id = ?",
                (task_id, question_id),
            ).fetchone()
            if existing is not None:
                current = self._question(existing)
                if (
                    current.turn_id == turn_id
                    and current.prompt == pending.prompt
                    and current.options == pending.options
                ):
                    return current
                raise WorkerConflictError(
                    "Question identifier is already bound to another request.",
                    code="question_intent_conflict",
                )
            connection.execute(
                """
                INSERT INTO worker_questions (
                    task_id, question_id, turn_id, status, request_ciphertext,
                    answer_ciphertext, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    task_id,
                    question_id,
                    turn_id,
                    QuestionStatus.PENDING.value,
                    self._codec.encrypt(request),
                    now,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="question_requested",
                payload={
                    "question_id": question_id,
                    "prompt": pending.prompt,
                    "options": request["options"],
                },
                created_at=now,
            )
        return pending

    def list_questions(self, task_id: str) -> list[WorkerQuestion]:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                """
                SELECT * FROM worker_questions
                WHERE task_id = ? ORDER BY created_at, question_id
                """,
                (task_id,),
            ).fetchall()
        return [self._question(row) for row in rows]

    def resolve_question(
        self, task_id: str, question_id: str, answer: WorkerQuestionAnswer
    ) -> WorkerQuestion:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task_row = self._require_task_row(connection, task_id)
            row = connection.execute(
                "SELECT * FROM worker_questions WHERE task_id = ? AND question_id = ?",
                (task_id, question_id),
            ).fetchone()
            if row is None:
                raise WorkerNotFoundError(
                    "Question was not found.", code="question_not_found"
                )
            question = self._question(row)
            if question.status is not QuestionStatus.PENDING:
                raise WorkerConflictError(
                    "Question was already resolved.", code="question_already_resolved"
                )
            if TaskState(task_row["state"]) is not TaskState.WAITING_INPUT:
                raise WorkerConflictError(
                    "Task is not waiting for input.", code="task_state_conflict"
                )
            selected = next(
                (item for item in question.options if item.option_id == answer.option_id),
                None,
            )
            if answer.option_id is not None and selected is None:
                raise WorkerConflictError(
                    "Question option is not available.", code="question_option_invalid"
                )
            content = (
                answer.answer
                if answer.answer is not None
                else f"{selected.label} [option:{selected.option_id}]"
            )
            connection.execute(
                """
                UPDATE worker_questions
                SET status = ?, answer_ciphertext = ?, resolved_at = ?
                WHERE task_id = ? AND question_id = ?
                """,
                (
                    QuestionStatus.RESOLVED.value,
                    self._codec.encrypt(answer.model_dump(mode="json")),
                    now,
                    task_id,
                    question_id,
                ),
            )
            next_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM worker_messages WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchone()[0]
            )
            provider_message = f"Answer to question {question_id}: {content}"
            connection.execute(
                """
                INSERT INTO worker_messages (
                    message_id, task_id, sequence, role, content_ciphertext, created_at
                ) VALUES (?, ?, ?, 'user', ?, ?)
                """,
                (
                    f"message_{uuid.uuid4().hex}",
                    task_id,
                    next_sequence,
                    self._codec.encrypt(provider_message),
                    now,
                ),
            )
            self._append_session_ledger_locked(
                connection,
                task_id=task_id,
                kind=SessionLedgerKind.PUBLIC_MESSAGE,
                turn_id=None,
                operation_id=None,
                payload={"role": "user", "text": provider_message},
                created_at=now,
            )
            connection.execute(
                """
                UPDATE worker_tasks
                SET state = ?, reason_ciphertext = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (TaskState.QUEUED.value, now, task_id),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="question_resolved",
                payload={"question_id": question_id},
                created_at=now,
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="task_state",
                payload={
                    "from": TaskState.WAITING_INPUT.value,
                    "to": TaskState.QUEUED.value,
                    "reason": None,
                },
                created_at=now,
            )
            resolved = connection.execute(
                "SELECT * FROM worker_questions WHERE task_id = ? AND question_id = ?",
                (task_id, question_id),
            ).fetchone()
        return self._question(resolved)

    def set_pinned(self, task_id: str, pinned: bool) -> TaskRecord:
        now = self._now()
        with self._lock, self._connect() as connection:
            self._require_task_row(connection, task_id)
            connection.execute(
                "UPDATE worker_tasks SET pinned = ?, expires_at = ?, updated_at = ? WHERE task_id = ?",
                (1 if pinned else 0, now + self.retention_seconds, now, task_id),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="task_pinned" if pinned else "task_unpinned",
                payload={},
                created_at=now,
            )
        return self.get_task(task_id)

    def create_approval(
        self,
        *,
        task_id: str,
        operation_id: str,
        capability: CapabilityName,
        request: dict[str, Any],
    ) -> WorkerApproval:
        now = self._now()
        approval_id = f"approval_{uuid.uuid4().hex}"
        encrypted_request = self._codec.encrypt(request)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            existing = connection.execute(
                "SELECT * FROM worker_approvals WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                approval = self._approval(existing)
                if (
                    approval.task_id != task_id
                    or approval.capability != capability
                    or approval.request != request
                ):
                    raise WorkerConflictError(
                        "Approval operation id is bound to another intent.",
                        code="approval_intent_conflict",
                    )
                return approval
            connection.execute(
                """
                INSERT INTO worker_approvals (
                    approval_id, task_id, operation_id, capability, status,
                    request_ciphertext, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    operation_id,
                    capability,
                    ApprovalStatus.PENDING.value,
                    encrypted_request,
                    now,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="approval_requested",
                payload={"approval_id": approval_id, "capability": capability},
                created_at=now,
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> WorkerApproval:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        if row is None:
            raise WorkerNotFoundError("Approval was not found.", code="approval_not_found")
        return self._approval(row)

    def list_approvals(self, task_id: str) -> list[WorkerApproval]:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM worker_approvals WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        return [self._approval(row) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        task_scope: bool = False,
        ttl_seconds: int = 900,
    ) -> WorkerApproval:
        if isinstance(ttl_seconds, bool) or not 30 <= ttl_seconds <= 3600:
            raise ValueError("approval ttl is outside the allowed range")
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM worker_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise WorkerNotFoundError("Approval was not found.", code="approval_not_found")
            if row["status"] != ApprovalStatus.PENDING.value:
                raise WorkerConflictError(
                    "Approval has already been decided.", code="approval_already_decided"
                )
            lease: CapabilityLease | None = None
            status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            if approved:
                task_row = self._require_task_row(connection, str(row["task_id"]))
                lease = CapabilityLease(
                    lease_id=f"lease_{uuid.uuid4().hex}",
                    task_id=str(row["task_id"]),
                    capability=str(row["capability"]),  # type: ignore[arg-type]
                    scope=self._decrypt_dict(row["request_ciphertext"]),
                    issued_at=now,
                    expires_at=min(now + ttl_seconds, float(task_row["expires_at"])),
                    operation_limit=1024 if task_scope else 1,
                )
                connection.execute(
                    """
                    INSERT INTO worker_leases (
                        lease_id, task_id, capability, scope_ciphertext,
                        issued_at, expires_at, remaining_operations
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease.lease_id,
                        lease.task_id,
                        lease.capability,
                        self._codec.encrypt(lease.scope),
                        lease.issued_at,
                        lease.expires_at,
                        lease.operation_limit,
                    ),
                )
            connection.execute(
                """
                UPDATE worker_approvals SET status = ?, lease_ciphertext = ?, decided_at = ?
                WHERE approval_id = ?
                """,
                (
                    status.value,
                    self._codec.encrypt(lease.model_dump(mode="json")) if lease else None,
                    now,
                    approval_id,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=str(row["task_id"]),
                event_type="approval_decided",
                payload={"approval_id": approval_id, "status": status.value},
                created_at=now,
            )
        return self.get_approval(approval_id)

    def consume_lease(
        self, lease_id: str, *, task_id: str, capability: CapabilityName
    ) -> CapabilityLease:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM worker_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if (
                row is None
                or row["task_id"] != task_id
                or row["capability"] != capability
                or float(row["expires_at"]) <= now
                or int(row["remaining_operations"]) <= 0
            ):
                raise WorkerConflictError(
                    "Capability lease is unavailable.", code="lease_unavailable"
                )
            remaining = int(row["remaining_operations"]) - 1
            connection.execute(
                "UPDATE worker_leases SET remaining_operations = ? WHERE lease_id = ?",
                (remaining, lease_id),
            )
            return CapabilityLease(
                lease_id=lease_id,
                task_id=task_id,
                capability=capability,  # type: ignore[arg-type]
                scope=self._decrypt_dict(row["scope_ciphertext"]),
                issued_at=float(row["issued_at"]),
                expires_at=float(row["expires_at"]),
                operation_limit=remaining + 1,
            )

    def has_active_lease(self, task_id: str) -> bool:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            return (
                connection.execute(
                    """
                    SELECT 1 FROM worker_leases
                    WHERE task_id = ? AND expires_at > ? AND remaining_operations > 0
                    LIMIT 1
                    """,
                    (task_id, self._now()),
                ).fetchone()
                is not None
            )

    def create_operation(
        self,
        *,
        task_id: str,
        operation_id: str,
        tool_name: str,
        intent_sha256: str,
        request: dict[str, Any],
    ) -> WorkerOperation:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task(self._require_task_row(connection, task_id), connection)
            existing = connection.execute(
                "SELECT * FROM worker_operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if existing is not None:
                operation = self._operation(existing)
                if (
                    operation.task_id != task_id
                    or operation.tool_name != tool_name
                    or operation.intent_sha256 != intent_sha256
                    or operation.request != request
                ):
                    raise WorkerConflictError(
                        "Tool operation id is bound to another intent.",
                        code="operation_intent_conflict",
                    )
                return operation
            operation_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM worker_operations WHERE task_id = ?",
                    (task_id,),
                ).fetchone()[0]
            )
            if operation_count >= task.spec.budget.max_tool_calls:
                raise WorkerConflictError(
                    "The durable tool call budget is exhausted.",
                    code="tool_budget_exhausted",
                )
            connection.execute(
                """
                INSERT INTO worker_operations (
                    operation_id, task_id, tool_name, intent_sha256, state,
                    request_ciphertext, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    task_id,
                    tool_name,
                    intent_sha256,
                    OperationState.PREPARED.value,
                    self._codec.encrypt(request),
                    now,
                    now,
                ),
            )
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> WorkerOperation:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        if row is None:
            raise WorkerNotFoundError("Tool operation was not found.", code="operation_not_found")
        return self._operation(row)

    def list_operations(self, task_id: str) -> list[WorkerOperation]:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM worker_operations WHERE task_id = ? ORDER BY created_at, operation_id",
                (task_id,),
            ).fetchall()
        return [self._operation(row) for row in rows]

    def transition_operation(
        self,
        operation_id: str,
        target: OperationState,
        *,
        result: dict[str, Any] | None = None,
        expected_state: OperationState | None = None,
    ) -> WorkerOperation:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM worker_operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise WorkerNotFoundError("Tool operation was not found.", code="operation_not_found")
            current = OperationState(row["state"])
            if expected_state is not None and current is not expected_state:
                raise WorkerConflictError(
                    "Tool operation state changed.", code="operation_state_conflict"
                )
            allowed = {
                OperationState.PREPARED: {OperationState.RUNNING, OperationState.FAILED},
                OperationState.RUNNING: {
                    OperationState.COMPLETED,
                    OperationState.FAILED,
                    OperationState.UNKNOWN,
                },
                OperationState.UNKNOWN: {
                    OperationState.COMPLETED,
                    OperationState.FAILED,
                },
                OperationState.COMPLETED: set(),
                OperationState.FAILED: set(),
            }
            if current is not target and target not in allowed[current]:
                raise WorkerConflictError(
                    "Tool operation transition is invalid.", code="operation_state_conflict"
                )
            connection.execute(
                """
                UPDATE worker_operations SET state = ?, result_ciphertext = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    target.value,
                    self._codec.encrypt(result) if result is not None else row["result_ciphertext"],
                    now,
                    operation_id,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=str(row["task_id"]),
                event_type="tool_operation",
                payload={"operation_id": operation_id, "state": target.value},
                created_at=now,
            )
        return self.get_operation(operation_id)

    def mark_inflight_operations_unknown(self) -> int:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT operation_id, task_id FROM worker_operations WHERE state = ?",
                (OperationState.RUNNING.value,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE worker_operations SET state = ?, updated_at = ? WHERE operation_id = ?",
                    (OperationState.UNKNOWN.value, now, row["operation_id"]),
                )
                self._append_event_locked(
                    connection,
                    task_id=str(row["task_id"]),
                    event_type="tool_operation",
                    payload={
                        "operation_id": str(row["operation_id"]),
                        "state": OperationState.UNKNOWN.value,
                    },
                    created_at=now,
                )
        return len(rows)

    def create_artifact(
        self,
        *,
        task_id: str,
        media_type: str,
        content: bytes,
        metadata: dict[str, Any] | None = None,
    ) -> WorkerArtifact:
        if not media_type or len(media_type) > 120:
            raise ValueError("artifact media type is invalid")
        if len(content) > 16 * 1024 * 1024:
            raise ValueError("artifact is too large")
        now = self._now()
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        digest = hashlib.sha256(content).hexdigest()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            connection.execute(
                """
                INSERT INTO worker_artifacts (
                    artifact_id, task_id, media_type, sha256, size,
                    metadata_ciphertext, content_ciphertext, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    task_id,
                    media_type,
                    digest,
                    len(content),
                    self._codec.encrypt(metadata or {}),
                    self._codec.encrypt({"base64": base64.b64encode(content).decode("ascii")}),
                    now,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="artifact_created",
                payload={"artifact_id": artifact_id, "media_type": media_type},
                created_at=now,
            )
        return self.get_artifact(artifact_id)

    def get_artifact(self, artifact_id: str) -> WorkerArtifact:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            raise WorkerNotFoundError("Artifact was not found.", code="artifact_not_found")
        return self._artifact(row)

    def create_checkpoint(
        self,
        *,
        task_id: str,
        workspace_tree_hash: str,
        payload: dict[str, Any],
    ) -> WorkerCheckpoint:
        now = self._now()
        checkpoint = WorkerCheckpoint(
            checkpoint_id=f"checkpoint_{uuid.uuid4().hex}",
            task_id=task_id,
            workspace_tree_hash=workspace_tree_hash,
            payload=payload,
            created_at=now,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            connection.execute(
                """
                INSERT INTO worker_checkpoints (
                    checkpoint_id, task_id, workspace_tree_hash,
                    payload_ciphertext, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.checkpoint_id,
                    task_id,
                    workspace_tree_hash,
                    self._codec.encrypt(payload),
                    now,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="checkpoint_created",
                payload={
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "workspace_tree_hash": workspace_tree_hash,
                },
                created_at=now,
            )
        return checkpoint

    def latest_checkpoint(self, task_id: str) -> WorkerCheckpoint | None:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            row = connection.execute(
                """
                SELECT * FROM worker_checkpoints
                WHERE task_id = ? ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return self._checkpoint(row) if row is not None else None

    def create_turn_checkpoint(
        self,
        *,
        task_id: str,
        turn_id: str,
        before_tree_hash: str,
        before_tree_oid: str,
        after_tree_hash: str,
        after_tree_oid: str,
        ledger_sequence: int,
        before_public_context: dict[str, Any] | None = None,
        after_public_context: dict[str, Any] | None = None,
    ) -> WorkerTurnCheckpoint:
        now = self._now()
        checkpoint_id = f"turn_checkpoint_{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            existing = connection.execute(
                "SELECT * FROM worker_turn_checkpoints WHERE task_id = ? AND turn_id = ?",
                (task_id, turn_id),
            ).fetchone()
            if existing is not None:
                checkpoint = self._turn_checkpoint(existing, connection)
                if (
                    checkpoint.before_tree_hash != before_tree_hash
                    or checkpoint.before_tree_oid != before_tree_oid
                    or checkpoint.after_tree_hash != after_tree_hash
                    or checkpoint.after_tree_oid != after_tree_oid
                    or checkpoint.ledger_sequence != ledger_sequence
                ):
                    raise WorkerConflictError(
                        "Turn checkpoint binding changed.",
                        code="turn_checkpoint_conflict",
                    )
                return checkpoint
            created = self._insert_turn_checkpoint_locked(
                connection,
                task_id=task_id,
                turn_id=turn_id,
                before_tree_hash=before_tree_hash,
                before_tree_oid=before_tree_oid,
                after_tree_hash=after_tree_hash,
                after_tree_oid=after_tree_oid,
                ledger_sequence=ledger_sequence,
                created_at=now,
                before_public_context=before_public_context,
                after_public_context=after_public_context,
                checkpoint_id=checkpoint_id,
            )
        return created

    def turn_history(self, task_id: str) -> WorkerTurnHistory:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM worker_turn_checkpoints WHERE task_id = ? ORDER BY ordinal",
                (task_id,),
            ).fetchall()
            state = connection.execute(
                "SELECT * FROM worker_turn_state WHERE task_id = ?", (task_id,)
            ).fetchone()
            checkpoints = tuple(
                self._turn_checkpoint(row, connection) for row in rows
            )
        return WorkerTurnHistory(
            task_id=task_id,
            cursor=int(state["cursor"]) if state is not None else len(rows),
            checkpoints=checkpoints,
            pending_action=(
                str(state["pending_action"])
                if state is not None and state["pending_action"] is not None
                else None
            ),
        )

    def begin_turn_navigation(
        self, task_id: str, action: str
    ) -> tuple[WorkerTurnCheckpoint, int, str, str, str]:
        if action not in {"undo", "redo"}:
            raise ValueError("turn navigation action is invalid")
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_task_row(connection, task_id)
            state = connection.execute(
                "SELECT * FROM worker_turn_state WHERE task_id = ?", (task_id,)
            ).fetchone()
            if state is None:
                raise WorkerConflictError(
                    "Task has no turn checkpoints.", code="turn_history_unavailable"
                )
            cursor = int(state["cursor"])
            pending = state["pending_action"]
            if pending is not None:
                if str(pending) != action:
                    raise WorkerConflictError(
                        "Another turn navigation is pending.",
                        code="turn_navigation_pending",
                    )
                row = connection.execute(
                    "SELECT * FROM worker_turn_checkpoints WHERE checkpoint_id = ?",
                    (state["pending_checkpoint_id"],),
                ).fetchone()
                if row is None:
                    raise WorkerStoreError(
                        "Turn navigation data is corrupt.", code="worker_data_corrupt"
                    )
                checkpoint = self._turn_checkpoint(row, connection)
            else:
                ordinal = cursor if action == "undo" else cursor + 1
                if (action == "undo" and cursor == 0) or (
                    action == "redo"
                    and ordinal
                    > int(
                        connection.execute(
                            "SELECT COALESCE(MAX(ordinal), 0) FROM worker_turn_checkpoints WHERE task_id = ?",
                            (task_id,),
                        ).fetchone()[0]
                    )
                ):
                    raise WorkerConflictError(
                        "No turn is available for navigation.",
                        code="turn_navigation_unavailable",
                    )
                row = connection.execute(
                    "SELECT * FROM worker_turn_checkpoints WHERE task_id = ? AND ordinal = ?",
                    (task_id, ordinal),
                ).fetchone()
                if row is None:
                    raise WorkerStoreError(
                        "Turn history is corrupt.", code="worker_data_corrupt"
                    )
                checkpoint = self._turn_checkpoint(row, connection)
                connection.execute(
                    """
                    UPDATE worker_turn_state
                    SET pending_action = ?, pending_checkpoint_id = ?, updated_at = ?
                    WHERE task_id = ? AND cursor = ? AND pending_action IS NULL
                    """,
                    (action, checkpoint.checkpoint_id, now, task_id, cursor),
                )
            target_cursor = cursor - 1 if action == "undo" else cursor + 1
            source_hash = (
                checkpoint.after_tree_hash if action == "undo" else checkpoint.before_tree_hash
            )
            target_hash = (
                checkpoint.before_tree_hash if action == "undo" else checkpoint.after_tree_hash
            )
            target_oid = (
                checkpoint.before_tree_oid if action == "undo" else checkpoint.after_tree_oid
            )
        return checkpoint, target_cursor, source_hash, target_hash, target_oid

    def finish_turn_navigation(
        self,
        task_id: str,
        *,
        action: str,
        checkpoint_id: str,
        target_cursor: int,
        workspace_tree_hash: str,
    ) -> WorkerTurnHistory:
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT * FROM worker_turn_state WHERE task_id = ?", (task_id,)
            ).fetchone()
            if (
                state is None
                or state["pending_action"] != action
                or state["pending_checkpoint_id"] != checkpoint_id
            ):
                raise WorkerConflictError(
                    "Turn navigation binding changed.", code="turn_navigation_conflict"
                )
            connection.execute(
                """
                UPDATE worker_turn_state SET cursor = ?, pending_action = NULL,
                    pending_checkpoint_id = NULL, updated_at = ? WHERE task_id = ?
                """,
                (target_cursor, now, task_id),
            )
            connection.execute(
                "DELETE FROM worker_checkpoints WHERE task_id = ?", (task_id,)
            )
            connection.execute(
                "UPDATE worker_tasks SET provider_session_ciphertext = NULL WHERE task_id = ?",
                (task_id,),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type=f"turn_{action}",
                payload={
                    "checkpoint_id": checkpoint_id,
                    "cursor": target_cursor,
                    "workspace_tree_hash": workspace_tree_hash,
                },
                created_at=now,
            )
        return self.turn_history(task_id)

    def _insert_turn_checkpoint_locked(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        turn_id: str,
        before_tree_hash: str,
        before_tree_oid: str,
        after_tree_hash: str,
        after_tree_oid: str,
        ledger_sequence: int,
        created_at: float,
        before_public_context: dict[str, Any] | None = None,
        after_public_context: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
    ) -> WorkerTurnCheckpoint:
        existing = connection.execute(
            "SELECT * FROM worker_turn_checkpoints WHERE task_id = ? AND turn_id = ?",
            (task_id, turn_id),
        ).fetchone()
        if existing is not None:
            checkpoint = self._turn_checkpoint(existing, connection)
            if (
                checkpoint.before_tree_hash != before_tree_hash
                or checkpoint.before_tree_oid != before_tree_oid
                or checkpoint.after_tree_hash != after_tree_hash
                or checkpoint.after_tree_oid != after_tree_oid
                or checkpoint.ledger_sequence != ledger_sequence
            ):
                raise WorkerConflictError(
                    "Turn checkpoint binding changed.", code="turn_checkpoint_conflict"
                )
            return checkpoint
        state = connection.execute(
            "SELECT * FROM worker_turn_state WHERE task_id = ?", (task_id,)
        ).fetchone()
        if state is not None and state["pending_action"] is not None:
            raise WorkerConflictError(
                "Turn navigation is not settled.", code="turn_navigation_pending"
            )
        maximum = int(
            connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) FROM worker_turn_checkpoints WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
        )
        cursor = int(state["cursor"]) if state is not None else maximum
        if cursor < maximum:
            connection.execute(
                "DELETE FROM worker_turn_checkpoints WHERE task_id = ? AND ordinal > ?",
                (task_id, cursor),
            )
            maximum = cursor
        ordinal = maximum + 1
        identifier = checkpoint_id or f"turn_checkpoint_{uuid.uuid4().hex}"
        connection.execute(
            """
            INSERT INTO worker_turn_checkpoints (
                checkpoint_id, task_id, ordinal, turn_id,
                before_tree_hash, before_tree_oid, after_tree_hash,
                after_tree_oid, ledger_sequence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                task_id,
                ordinal,
                turn_id,
                before_tree_hash,
                before_tree_oid,
                after_tree_hash,
                after_tree_oid,
                ledger_sequence,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO worker_turn_contexts (
                checkpoint_id, before_context_ciphertext, after_context_ciphertext
            ) VALUES (?, ?, ?)
            """,
            (
                identifier,
                self._codec.encrypt(before_public_context or {}),
                self._codec.encrypt(after_public_context or {}),
            ),
        )
        connection.execute(
            """
            INSERT INTO worker_turn_state (task_id, cursor, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                cursor = excluded.cursor, pending_action = NULL,
                pending_checkpoint_id = NULL, updated_at = excluded.updated_at
            """,
            (task_id, ordinal, created_at),
        )
        self._append_event_locked(
            connection,
            task_id=task_id,
            event_type="turn_checkpoint_created",
            payload={
                "checkpoint_id": identifier,
                "ordinal": ordinal,
                "workspace_tree_hash": after_tree_hash,
            },
            created_at=created_at,
        )
        return WorkerTurnCheckpoint(
            checkpoint_id=identifier,
            task_id=task_id,
            ordinal=ordinal,
            turn_id=turn_id,
            before_tree_hash=before_tree_hash,
            before_tree_oid=before_tree_oid,
            after_tree_hash=after_tree_hash,
            after_tree_oid=after_tree_oid,
            ledger_sequence=ledger_sequence,
            before_public_context=before_public_context or {},
            after_public_context=after_public_context or {},
            created_at=created_at,
        )

    def list_artifacts(self, task_id: str) -> list[WorkerArtifact]:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM worker_artifacts WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        return [self._artifact(row) for row in rows]

    def read_artifact(self, artifact_id: str, *, task_id: str) -> bytes:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_artifacts WHERE artifact_id = ? AND task_id = ?",
                (artifact_id, task_id),
            ).fetchone()
        if row is None:
            raise WorkerNotFoundError("Artifact was not found.", code="artifact_not_found")
        try:
            value = self._decrypt_dict(row["content_ciphertext"])
            content = base64.b64decode(str(value["base64"]), validate=True)
        except (KeyError, ValueError, WorkerCryptoError) as exc:
            raise WorkerStoreError(
                "Worker artifact data is corrupt.", code="worker_data_corrupt"
            ) from exc
        if (
            len(content) != int(row["size"])
            or hashlib.sha256(content).hexdigest() != str(row["sha256"])
        ):
            raise WorkerStoreError(
                "Worker artifact data is corrupt.", code="worker_data_corrupt"
            )
        return content

    def record_evidence(
        self,
        *,
        task_id: str,
        check_id: str,
        operation_id: str,
        workspace_tree_hash: str,
        exit_code: int,
        artifact_id: str,
    ) -> WorkerEvidence:
        now = self._now()
        evidence_id = f"evidence_{uuid.uuid4().hex}"
        status = EvidenceStatus.PASSED if exit_code == 0 else EvidenceStatus.FAILED
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task(self._require_task_row(connection, task_id), connection)
            if check_id not in {item.check_id for item in task.spec.acceptance.required_checks}:
                raise WorkerConflictError(
                    "Evidence check is not in the acceptance contract.",
                    code="acceptance_check_unknown",
                )
            artifact = connection.execute(
                "SELECT task_id FROM worker_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if artifact is None or str(artifact["task_id"]) != task_id:
                raise WorkerConflictError(
                    "Evidence artifact is not bound to this task.",
                    code="artifact_binding_conflict",
                )
            connection.execute(
                """
                INSERT INTO worker_evidence (
                    evidence_id, task_id, check_id, operation_id,
                    workspace_tree_hash, status, exit_code, artifact_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    task_id,
                    check_id,
                    operation_id,
                    workspace_tree_hash,
                    status.value,
                    exit_code,
                    artifact_id,
                    now,
                ),
            )
            self._append_event_locked(
                connection,
                task_id=task_id,
                event_type="evidence_recorded",
                payload={
                    "evidence_id": evidence_id,
                    "check_id": check_id,
                    "status": status.value,
                    "workspace_tree_hash": workspace_tree_hash,
                },
                created_at=now,
            )
            self._append_session_ledger_locked(
                connection,
                task_id=task_id,
                kind=SessionLedgerKind.CHECK_EVIDENCE,
                turn_id=None,
                operation_id=None,
                payload={
                    "check_id": check_id,
                    "evidence_id": evidence_id,
                    "status": status.value,
                    "exit_code": exit_code,
                    "artifact_id": artifact_id,
                    "workspace_tree_hash": workspace_tree_hash,
                },
                created_at=now,
            )
        return self.get_evidence(evidence_id)

    def get_evidence(self, evidence_id: str) -> WorkerEvidence:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        if row is None:
            raise WorkerNotFoundError("Evidence was not found.", code="evidence_not_found")
        return self._evidence(row)

    def list_evidence(
        self, task_id: str, *, current_tree_hash: str | None = None
    ) -> list[WorkerEvidence]:
        with self._connect() as connection:
            self._require_task_row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM worker_evidence WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            ).fetchall()
        evidence = [self._evidence(row) for row in rows]
        if current_tree_hash is None:
            return evidence
        return [
            item.model_copy(update={"status": EvidenceStatus.INVALIDATED})
            if item.workspace_tree_hash != current_tree_hash
            else item
            for item in evidence
        ]

    def delete_task(self, task_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = self._require_task_row(connection, task_id)
            state = TaskState(row["state"])
            if state not in TERMINAL_STATES and state not in {TaskState.PAUSED, TaskState.INTERRUPTED}:
                raise WorkerConflictError(
                    "Active tasks must be cancelled before deletion.", code="task_active"
                )
            return connection.execute(
                "DELETE FROM worker_tasks WHERE task_id = ?", (task_id,)
            ).rowcount == 1

    def cleanup_expired(self) -> list[str]:
        now = self._now()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT task_id FROM worker_tasks WHERE pinned = 0 AND expires_at <= ?",
                (now,),
            ).fetchall()
            task_ids = [str(row["task_id"]) for row in rows]
            if task_ids:
                connection.executemany(
                    "DELETE FROM worker_tasks WHERE task_id = ?",
                    ((task_id,) for task_id in task_ids),
                )
        return task_ids

    def mark_inflight_interrupted(self) -> int:
        now = self._now()
        count = 0
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT task_id, state FROM worker_tasks WHERE state IN ({','.join('?' for _ in ACTIVE_ON_RESTART)})",
                tuple(state.value for state in ACTIVE_ON_RESTART),
            ).fetchall()
            for row in rows:
                task_id = str(row["task_id"])
                connection.execute(
                    "UPDATE worker_tasks SET state = ?, reason_ciphertext = ?, updated_at = ? WHERE task_id = ?",
                    (
                        TaskState.INTERRUPTED.value,
                        self._codec.encrypt("server_restart"),
                        now,
                        task_id,
                    ),
                )
                self._append_event_locked(
                    connection,
                    task_id=task_id,
                    event_type="task_state",
                    payload={
                        "from": str(row["state"]),
                        "to": TaskState.INTERRUPTED.value,
                        "reason": "server_restart",
                    },
                    created_at=now,
                )
                count += 1
        return count

    def mark_open_session_boundaries_interrupted(self) -> int:
        """Close only unreceipted ledger boundaries; never infer tool success."""
        now = self._now()
        count = 0
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT started.task_id, started.turn_id
                FROM worker_session_ledger AS started
                LEFT JOIN worker_session_ledger AS finished
                  ON finished.task_id = started.task_id
                 AND finished.turn_id = started.turn_id
                 AND finished.kind = ?
                WHERE started.kind = ? AND finished.ledger_id IS NULL
                ORDER BY started.task_id, started.sequence
                """,
                (
                    SessionLedgerKind.TURN_FINISHED.value,
                    SessionLedgerKind.TURN_STARTED.value,
                ),
            ).fetchall()
            for row in rows:
                task_id = str(row["task_id"])
                turn_id = str(row["turn_id"])
                open_tools = connection.execute(
                    """
                    SELECT started.operation_id, started.payload_ciphertext
                    FROM worker_session_ledger AS started
                    LEFT JOIN worker_session_ledger AS finished
                      ON finished.task_id = started.task_id
                     AND finished.operation_id = started.operation_id
                     AND finished.kind = ?
                    WHERE started.task_id = ? AND started.turn_id = ?
                      AND started.kind = ? AND finished.ledger_id IS NULL
                    ORDER BY started.sequence
                    """,
                    (
                        SessionLedgerKind.TOOL_FINISHED.value,
                        task_id,
                        turn_id,
                        SessionLedgerKind.TOOL_STARTED.value,
                    ),
                ).fetchall()
                for tool in open_tools:
                    started = self._decrypt_dict(tool["payload_ciphertext"])
                    self._append_session_ledger_locked(
                        connection,
                        task_id=task_id,
                        kind=SessionLedgerKind.TOOL_FINISHED,
                        turn_id=turn_id,
                        operation_id=str(tool["operation_id"]),
                        payload={
                            "tool_name": started["tool_name"],
                            "summary": "Completion receipt was not observed before restart.",
                            "result_state": "unknown",
                            "artifact_id": None,
                        },
                        created_at=now,
                    )
                self._append_session_ledger_locked(
                    connection,
                    task_id=task_id,
                    kind=SessionLedgerKind.TURN_FINISHED,
                    turn_id=turn_id,
                    operation_id=None,
                    payload={"result_state": "interrupted"},
                    created_at=now,
                )
                count += 1
        return count

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_tasks (
                    task_id TEXT PRIMARY KEY,
                    origin_module TEXT NOT NULL,
                    origin_object_id TEXT NOT NULL,
                    client_task_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    spec_ciphertext TEXT NOT NULL,
                    reason_ciphertext TEXT,
                    workspace_id TEXT,
                    provider_session_ciphertext TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                    UNIQUE(origin_module, origin_object_id, client_task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_worker_tasks_state
                    ON worker_tasks(state, created_at);
                CREATE TABLE IF NOT EXISTS worker_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload_ciphertext TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_events_task
                    ON worker_events(task_id, sequence);
                CREATE TABLE IF NOT EXISTS worker_messages (
                    message_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content_ciphertext TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id, sequence),
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS worker_session_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    turn_id TEXT,
                    operation_id TEXT,
                    payload_ciphertext TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id, sequence),
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_session_ledger_task
                    ON worker_session_ledger(task_id, sequence);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_session_ledger_tool_start
                    ON worker_session_ledger(task_id, operation_id)
                    WHERE kind = 'tool_started';
                CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_session_ledger_tool_finish
                    ON worker_session_ledger(task_id, operation_id)
                    WHERE kind = 'tool_finished';
                CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_session_ledger_turn_start
                    ON worker_session_ledger(task_id, turn_id)
                    WHERE kind = 'turn_started';
                CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_session_ledger_turn_finish
                    ON worker_session_ledger(task_id, turn_id)
                    WHERE kind = 'turn_finished';
                CREATE TABLE IF NOT EXISTS worker_questions (
                    task_id TEXT NOT NULL,
                    question_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_ciphertext TEXT NOT NULL,
                    answer_ciphertext TEXT,
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    PRIMARY KEY(task_id, question_id),
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_questions_task
                    ON worker_questions(task_id, created_at);
                CREATE TABLE IF NOT EXISTS worker_approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE,
                    capability TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_ciphertext TEXT NOT NULL,
                    lease_ciphertext TEXT,
                    created_at REAL NOT NULL,
                    decided_at REAL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_approvals_task
                    ON worker_approvals(task_id, created_at);
                CREATE TABLE IF NOT EXISTS worker_leases (
                    lease_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    scope_ciphertext TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    remaining_operations INTEGER NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS worker_operations (
                    operation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    intent_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    request_ciphertext TEXT NOT NULL,
                    result_ciphertext TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_operations_task
                    ON worker_operations(task_id, created_at);
                CREATE TABLE IF NOT EXISTS worker_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    metadata_ciphertext TEXT NOT NULL,
                    content_ciphertext TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_artifacts_task
                    ON worker_artifacts(task_id, created_at);
                CREATE TABLE IF NOT EXISTS worker_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    check_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    workspace_tree_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    artifact_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE,
                    FOREIGN KEY(artifact_id) REFERENCES worker_artifacts(artifact_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_evidence_task
                    ON worker_evidence(task_id, check_id, created_at);
                CREATE TABLE IF NOT EXISTS worker_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    workspace_tree_hash TEXT NOT NULL,
                    payload_ciphertext TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_checkpoints_task
                    ON worker_checkpoints(task_id, created_at);
                CREATE TABLE IF NOT EXISTS worker_turn_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    turn_id TEXT NOT NULL,
                    before_tree_hash TEXT NOT NULL,
                    before_tree_oid TEXT NOT NULL,
                    after_tree_hash TEXT NOT NULL,
                    after_tree_oid TEXT NOT NULL,
                    ledger_sequence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(task_id, ordinal),
                    UNIQUE(task_id, turn_id),
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_turn_checkpoints_task
                    ON worker_turn_checkpoints(task_id, ordinal);
                CREATE TABLE IF NOT EXISTS worker_turn_contexts (
                    checkpoint_id TEXT PRIMARY KEY,
                    before_context_ciphertext TEXT NOT NULL,
                    after_context_ciphertext TEXT NOT NULL,
                    FOREIGN KEY(checkpoint_id) REFERENCES worker_turn_checkpoints(checkpoint_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS worker_turn_state (
                    task_id TEXT PRIMARY KEY,
                    cursor INTEGER NOT NULL DEFAULT 0,
                    pending_action TEXT,
                    pending_checkpoint_id TEXT,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE,
                    FOREIGN KEY(pending_checkpoint_id) REFERENCES worker_turn_checkpoints(checkpoint_id)
                );
                CREATE TABLE IF NOT EXISTS worker_task_forks (
                    parent_task_id TEXT NOT NULL,
                    client_fork_id TEXT NOT NULL,
                    child_task_id TEXT NOT NULL UNIQUE,
                    parent_cursor INTEGER NOT NULL,
                    parent_tree_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(parent_task_id, client_fork_id),
                    FOREIGN KEY(parent_task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE,
                    FOREIGN KEY(child_task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_task_forks_parent
                    ON worker_task_forks(parent_task_id, created_at);
                CREATE TABLE IF NOT EXISTS worker_subtasks (
                    parent_task_id TEXT NOT NULL,
                    client_subtask_id TEXT NOT NULL,
                    child_task_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    objective_ciphertext TEXT NOT NULL,
                    base_tree_hash TEXT NOT NULL,
                    merge_state TEXT NOT NULL,
                    result_tree_hash TEXT,
                    merge_operation_id TEXT,
                    merged_tree_hash TEXT,
                    changed_paths_ciphertext TEXT,
                    summary_ciphertext TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(parent_task_id, client_subtask_id),
                    FOREIGN KEY(parent_task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE,
                    FOREIGN KEY(child_task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_worker_subtasks_parent
                    ON worker_subtasks(parent_task_id, created_at);
                """
            )
            subtask_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(worker_subtasks)"
                ).fetchall()
            }
            for column, declaration in (
                ("merge_operation_id", "TEXT"),
                ("merged_tree_hash", "TEXT"),
            ):
                if column not in subtask_columns:
                    connection.execute(
                        f"ALTER TABLE worker_subtasks ADD COLUMN {column} {declaration}"
                    )

    def _subtask(self, row: sqlite3.Row) -> SubtaskRecord:
        try:
            changed_paths = (
                tuple(self._codec.decrypt(str(row["changed_paths_ciphertext"])))
                if row["changed_paths_ciphertext"] is not None
                else ()
            )
            return SubtaskRecord(
                parent_task_id=str(row["parent_task_id"]),
                child_task_id=str(row["child_task_id"]),
                client_subtask_id=str(row["client_subtask_id"]),
                kind=SubtaskKind(str(row["kind"])),
                objective=self._decrypt_string(row["objective_ciphertext"]),
                base_tree_hash=str(row["base_tree_hash"]),
                merge_state=SubtaskMergeState(str(row["merge_state"])),
                result_tree_hash=(
                    str(row["result_tree_hash"])
                    if row["result_tree_hash"] is not None
                    else None
                ),
                merge_operation_id=(
                    str(row["merge_operation_id"])
                    if row["merge_operation_id"] is not None
                    else None
                ),
                merged_tree_hash=(
                    str(row["merged_tree_hash"])
                    if row["merged_tree_hash"] is not None
                    else None
                ),
                changed_paths=changed_paths,
                summary=(
                    self._decrypt_string(row["summary_ciphertext"])
                    if row["summary_ciphertext"] is not None
                    else None
                ),
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
            )
        except (WorkerCryptoError, ValueError, TypeError) as exc:
            raise WorkerStoreError(
                "Worker subtask data is corrupt.", code="worker_data_corrupt"
            ) from exc

    def _task(self, row: sqlite3.Row, connection: sqlite3.Connection) -> TaskRecord:
        try:
            spec = TaskSpec.model_validate(self._codec.decrypt(str(row["spec_ciphertext"])))
            reason = (
                self._decrypt_string(row["reason_ciphertext"])
                if row["reason_ciphertext"] is not None
                else None
            )
            provider_session_id = (
                self._decrypt_string(row["provider_session_ciphertext"])
                if row["provider_session_ciphertext"] is not None
                else None
            )
        except (WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker task data is corrupt.", code="worker_data_corrupt"
            ) from exc
        last_sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM worker_events WHERE task_id = ?",
                (row["task_id"],),
            ).fetchone()[0]
        )
        return TaskRecord(
            task_id=str(row["task_id"]),
            spec=spec,
            state=TaskState(row["state"]),
            workspace_id=row["workspace_id"],
            provider_session_id=provider_session_id,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=float(row["expires_at"]),
            pinned=bool(row["pinned"]),
            last_event_sequence=last_sequence,
            reason=reason,
        )

    def _append_event_locked(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: float,
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO worker_events (task_id, type, payload_ciphertext, created_at) VALUES (?, ?, ?, ?)",
            (task_id, event_type, self._codec.encrypt(payload), created_at),
        )
        return int(cursor.lastrowid)

    def _append_session_ledger_locked(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        kind: SessionLedgerKind,
        payload: dict[str, Any],
        turn_id: str | None,
        operation_id: str | None,
        created_at: float,
    ) -> WorkerSessionLedgerEntry:
        if kind in {
            SessionLedgerKind.TURN_STARTED,
            SessionLedgerKind.TURN_FINISHED,
            SessionLedgerKind.TOOL_STARTED,
            SessionLedgerKind.TOOL_FINISHED,
        }:
            if turn_id is None:
                raise ValueError("session ledger turn binding is required")
        if kind is not SessionLedgerKind.TURN_STARTED and turn_id is not None:
            self._require_open_turn(connection, task_id, turn_id)
        if kind is SessionLedgerKind.TOOL_STARTED:
            existing = connection.execute(
                """
                SELECT * FROM worker_session_ledger
                WHERE task_id = ? AND operation_id = ? AND kind = ?
                """,
                (task_id, operation_id, SessionLedgerKind.TOOL_STARTED.value),
            ).fetchone()
            if existing is not None:
                existing_entry = self._session_ledger_entry(existing)
                if existing_entry.turn_id == turn_id and existing_entry.payload == payload:
                    return existing_entry
                raise WorkerConflictError(
                    "The tool operation already has a ledger boundary.",
                    code="session_tool_boundary_conflict",
                )
        elif kind is SessionLedgerKind.TOOL_FINISHED:
            started = connection.execute(
                """
                SELECT turn_id, payload_ciphertext FROM worker_session_ledger
                WHERE task_id = ? AND operation_id = ? AND kind = ?
                """,
                (task_id, operation_id, SessionLedgerKind.TOOL_STARTED.value),
            ).fetchone()
            if started is None or str(started["turn_id"]) != turn_id:
                raise WorkerConflictError(
                    "The tool completion is not bound to its start boundary.",
                    code="session_tool_boundary_conflict",
                )
            started_payload = self._decrypt_dict(started["payload_ciphertext"])
            if payload.get("tool_name") != started_payload.get("tool_name"):
                raise WorkerConflictError(
                    "The tool completion name does not match its start boundary.",
                    code="session_tool_boundary_conflict",
                )
            duplicate = connection.execute(
                """
                SELECT * FROM worker_session_ledger
                WHERE task_id = ? AND operation_id = ? AND kind = ?
                """,
                (task_id, operation_id, SessionLedgerKind.TOOL_FINISHED.value),
            ).fetchone()
            if duplicate is not None:
                existing_entry = self._session_ledger_entry(duplicate)
                if existing_entry.turn_id == turn_id and existing_entry.payload == payload:
                    return existing_entry
                raise WorkerConflictError(
                    "The tool completion was already recorded.",
                    code="session_tool_boundary_conflict",
                )
        sequence = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM worker_session_ledger WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()[0]
        )
        entry = WorkerSessionLedgerEntry(
            ledger_id=f"ledger_{uuid.uuid4().hex}",
            task_id=task_id,
            sequence=sequence,
            kind=kind,
            turn_id=turn_id,
            operation_id=operation_id,
            payload=payload,
            created_at=created_at,
        )
        connection.execute(
            """
            INSERT INTO worker_session_ledger (
                ledger_id, task_id, sequence, kind, turn_id, operation_id,
                payload_ciphertext, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.ledger_id,
                task_id,
                sequence,
                kind.value,
                turn_id,
                operation_id,
                self._codec.encrypt(payload),
                created_at,
            ),
        )
        return entry

    @staticmethod
    def _require_open_turn(
        connection: sqlite3.Connection, task_id: str, turn_id: str
    ) -> None:
        started = connection.execute(
            """
            SELECT 1 FROM worker_session_ledger
            WHERE task_id = ? AND turn_id = ? AND kind = ?
            """,
            (task_id, turn_id, SessionLedgerKind.TURN_STARTED.value),
        ).fetchone()
        finished = connection.execute(
            """
            SELECT 1 FROM worker_session_ledger
            WHERE task_id = ? AND turn_id = ? AND kind = ?
            """,
            (task_id, turn_id, SessionLedgerKind.TURN_FINISHED.value),
        ).fetchone()
        if started is None or finished is not None:
            raise WorkerConflictError(
                "The session turn is not open.", code="session_turn_boundary_conflict"
            )

    def _approval(self, row: sqlite3.Row) -> WorkerApproval:
        try:
            lease_value = (
                self._codec.decrypt(str(row["lease_ciphertext"]))
                if row["lease_ciphertext"] is not None
                else None
            )
            lease = CapabilityLease.model_validate(lease_value) if lease_value else None
            return WorkerApproval(
                approval_id=str(row["approval_id"]),
                task_id=str(row["task_id"]),
                operation_id=str(row["operation_id"]),
                capability=str(row["capability"]),
                status=ApprovalStatus(row["status"]),
                request=self._decrypt_dict(row["request_ciphertext"]),
                lease=lease,
                created_at=float(row["created_at"]),
                decided_at=(
                    float(row["decided_at"]) if row["decided_at"] is not None else None
                ),
            )
        except (WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker approval data is corrupt.", code="worker_data_corrupt"
            ) from exc

    def _session_ledger_entry(self, row: sqlite3.Row) -> WorkerSessionLedgerEntry:
        try:
            return WorkerSessionLedgerEntry(
                ledger_id=str(row["ledger_id"]),
                task_id=str(row["task_id"]),
                sequence=int(row["sequence"]),
                kind=SessionLedgerKind(row["kind"]),
                turn_id=(str(row["turn_id"]) if row["turn_id"] is not None else None),
                operation_id=(
                    str(row["operation_id"])
                    if row["operation_id"] is not None
                    else None
                ),
                payload=self._decrypt_dict(row["payload_ciphertext"]),
                created_at=float(row["created_at"]),
            )
        except (WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker session ledger data is corrupt.", code="worker_data_corrupt"
            ) from exc

    def _question(self, row: sqlite3.Row) -> WorkerQuestion:
        try:
            request = self._decrypt_dict(row["request_ciphertext"])
            answer = (
                self._decrypt_dict(row["answer_ciphertext"])
                if row["answer_ciphertext"] is not None
                else {"answer": None, "option_id": None}
            )
            return WorkerQuestion(
                task_id=str(row["task_id"]),
                question_id=str(row["question_id"]),
                turn_id=str(row["turn_id"]),
                status=QuestionStatus(row["status"]),
                prompt=request["prompt"],
                options=tuple(
                    WorkerQuestionOption.model_validate(item)
                    for item in request["options"]
                ),
                answer=answer.get("answer"),
                selected_option_id=answer.get("option_id"),
                created_at=float(row["created_at"]),
                resolved_at=(
                    float(row["resolved_at"])
                    if row["resolved_at"] is not None
                    else None
                ),
            )
        except (KeyError, TypeError, WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker question data is corrupt.", code="worker_data_corrupt"
            ) from exc

    def _operation(self, row: sqlite3.Row) -> WorkerOperation:
        try:
            result = (
                self._decrypt_dict(row["result_ciphertext"])
                if row["result_ciphertext"] is not None
                else None
            )
            return WorkerOperation(
                operation_id=str(row["operation_id"]),
                task_id=str(row["task_id"]),
                tool_name=str(row["tool_name"]),
                intent_sha256=str(row["intent_sha256"]),
                state=OperationState(row["state"]),
                request=self._decrypt_dict(row["request_ciphertext"]),
                result=result,
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]),
            )
        except (WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker operation data is corrupt.", code="worker_data_corrupt"
            ) from exc

    def _artifact(self, row: sqlite3.Row) -> WorkerArtifact:
        try:
            return WorkerArtifact(
                artifact_id=str(row["artifact_id"]),
                task_id=str(row["task_id"]),
                media_type=str(row["media_type"]),
                sha256=str(row["sha256"]),
                size=int(row["size"]),
                metadata=self._decrypt_dict(row["metadata_ciphertext"]),
                created_at=float(row["created_at"]),
            )
        except (WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker artifact data is corrupt.", code="worker_data_corrupt"
            ) from exc

    def _checkpoint(self, row: sqlite3.Row) -> WorkerCheckpoint:
        try:
            return WorkerCheckpoint(
                checkpoint_id=str(row["checkpoint_id"]),
                task_id=str(row["task_id"]),
                workspace_tree_hash=str(row["workspace_tree_hash"]),
                payload=self._decrypt_dict(row["payload_ciphertext"]),
                created_at=float(row["created_at"]),
            )
        except (WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker checkpoint data is corrupt.", code="worker_data_corrupt"
            ) from exc

    def _turn_checkpoint_by_id(self, checkpoint_id: str) -> WorkerTurnCheckpoint:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM worker_turn_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            raise WorkerNotFoundError(
                "Turn checkpoint was not found.", code="turn_checkpoint_not_found"
            )
        return self._turn_checkpoint(row)

    def _turn_checkpoint(
        self,
        row: sqlite3.Row,
        connection: sqlite3.Connection | None = None,
    ) -> WorkerTurnCheckpoint:
        try:
            if connection is None:
                with self._connect() as opened:
                    context = opened.execute(
                        "SELECT * FROM worker_turn_contexts WHERE checkpoint_id = ?",
                        (row["checkpoint_id"],),
                    ).fetchone()
            else:
                context = connection.execute(
                    "SELECT * FROM worker_turn_contexts WHERE checkpoint_id = ?",
                    (row["checkpoint_id"],),
                ).fetchone()
            return WorkerTurnCheckpoint(
                checkpoint_id=str(row["checkpoint_id"]),
                task_id=str(row["task_id"]),
                ordinal=int(row["ordinal"]),
                turn_id=str(row["turn_id"]),
                before_tree_hash=str(row["before_tree_hash"]),
                before_tree_oid=str(row["before_tree_oid"]),
                after_tree_hash=str(row["after_tree_hash"]),
                after_tree_oid=str(row["after_tree_oid"]),
                ledger_sequence=int(row["ledger_sequence"]),
                before_public_context=(
                    self._decrypt_dict(context["before_context_ciphertext"])
                    if context is not None
                    else {}
                ),
                after_public_context=(
                    self._decrypt_dict(context["after_context_ciphertext"])
                    if context is not None
                    else {}
                ),
                created_at=float(row["created_at"]),
            )
        except (KeyError, TypeError, WorkerCryptoError, ValueError) as exc:
            raise WorkerStoreError(
                "Worker turn checkpoint data is corrupt.", code="worker_data_corrupt"
            ) from exc

    @staticmethod
    def _evidence(row: sqlite3.Row) -> WorkerEvidence:
        try:
            return WorkerEvidence(
                evidence_id=str(row["evidence_id"]),
                task_id=str(row["task_id"]),
                check_id=str(row["check_id"]),
                operation_id=str(row["operation_id"]),
                workspace_tree_hash=str(row["workspace_tree_hash"]),
                status=EvidenceStatus(row["status"]),
                exit_code=int(row["exit_code"]),
                artifact_id=str(row["artifact_id"]),
                created_at=float(row["created_at"]),
            )
        except ValueError as exc:
            raise WorkerStoreError(
                "Worker evidence data is corrupt.", code="worker_data_corrupt"
            ) from exc

    @staticmethod
    def _require_task_row(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM worker_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise WorkerNotFoundError("Worker task was not found.", code="task_not_found")
        return row

    def _decrypt_dict(self, ciphertext: str) -> dict[str, Any]:
        value = self._codec.decrypt(str(ciphertext))
        if not isinstance(value, dict):
            raise WorkerStoreError("Worker event is corrupt.", code="worker_data_corrupt")
        return value

    def _decrypt_string(self, ciphertext: str) -> str:
        value = self._codec.decrypt(str(ciphertext))
        if not isinstance(value, str):
            raise WorkerStoreError("Worker value is corrupt.", code="worker_data_corrupt")
        return value

    def _now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value) or value < 0:
            raise WorkerStoreError("Worker clock is invalid.", code="worker_storage_unavailable")
        return value
