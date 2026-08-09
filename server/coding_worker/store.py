from __future__ import annotations

import math
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts import (
    TERMINAL_STATES,
    Origin,
    TaskRecord,
    TaskSpec,
    TaskState,
    WorkerEvent,
    WorkerMessage,
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
        self.mark_inflight_interrupted()

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
                """
            )

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
