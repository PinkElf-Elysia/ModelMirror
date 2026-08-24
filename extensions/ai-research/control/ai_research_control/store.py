from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


class StoreError(RuntimeError):
    pass


class IdempotencyConflict(StoreError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = threading.Lock()
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._schema_lock, self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );
                INSERT INTO schema_meta(version)
                SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    fixture_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL CHECK (tenant_id = 'local'),
                    project_id TEXT NOT NULL CHECK (project_id = 'local'),
                    actor_id TEXT NOT NULL CHECK (actor_id = 'local'),
                    phase TEXT NOT NULL CHECK (phase IN ('queued','running','terminal')),
                    outcome TEXT CHECK (outcome IN ('success','task_error','cancelled','infrastructure_error')),
                    inspect_status TEXT CHECK (inspect_status IN ('started','success','error','cancelled')),
                    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
                    cancel_applied INTEGER NOT NULL DEFAULT 0 CHECK (cancel_applied IN (0,1)),
                    cancel_requested_at TEXT,
                    cancel_applied_at TEXT,
                    evidence_state TEXT NOT NULL DEFAULT 'pending' CHECK (evidence_state IN ('pending','synced','failed')),
                    evidence_synced_at TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    replay_verified INTEGER NOT NULL DEFAULT 0 CHECK (replay_verified IN (0,1)),
                    worker_json TEXT,
                    receipt_json TEXT,
                    mlflow_run_id TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    terminal_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run_sequence ON events(run_id, sequence);

                CREATE TABLE IF NOT EXISTS outbox (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','synced','failed'))
                );
                """
            )
            version = connection.execute("SELECT version FROM schema_meta").fetchone()[0]
            if version == 1:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(runs)").fetchall()
                }
                additions = {
                    "cancel_requested_at": "TEXT",
                    "cancel_applied_at": "TEXT",
                    "evidence_synced_at": "TEXT",
                }
                for name, kind in additions.items():
                    if name not in columns:
                        connection.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")
                connection.execute("UPDATE schema_meta SET version=2")
                version = 2
            if version != 2:
                raise StoreError(f"unsupported control schema version: {version}")
            connection.commit()

    def probe(self) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("SELECT version FROM schema_meta").fetchone()
            connection.rollback()

    def create_or_get(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"))
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM runs WHERE idempotency_key = ?", (request["idempotency_key"],)
            ).fetchone()
            if existing is not None:
                connection.rollback()
                if existing["request_json"] != canonical:
                    raise IdempotencyConflict("idempotency key was already used for a different request")
                return self._row(existing), False
            run_id = f"ar0_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO runs(
                    run_id,idempotency_key,request_json,fixture_id,case_id,
                    tenant_id,project_id,actor_id,phase,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    request["idempotency_key"],
                    canonical,
                    request["fixture_id"],
                    request["case_id"],
                    request["tenant_id"],
                    request["project_id"],
                    request["actor_id"],
                    "queued",
                    now,
                    now,
                ),
            )
            self._append_event_tx(connection, run_id, "run.queued", {"caseId": request["case_id"]})
            connection.commit()
        return self.get(run_id), True

    def get(self, run_id: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._row(row)

    def list(self, *, after_run_id: str | None, limit: int) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        with self.connection() as connection:
            if after_run_id:
                anchor = connection.execute(
                    "SELECT created_at, run_id FROM runs WHERE run_id = ?", (after_run_id,)
                ).fetchone()
                if anchor is None:
                    raise KeyError(after_run_id)
                rows = connection.execute(
                    """
                    SELECT * FROM runs
                    WHERE (created_at, run_id) < (?, ?)
                    ORDER BY created_at DESC, run_id DESC LIMIT ?
                    """,
                    (anchor["created_at"], anchor["run_id"], limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC, run_id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row(row) for row in rows]

    def queued(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE phase = 'queued' ORDER BY created_at, run_id"
            ).fetchall()
        return [self._row(row) for row in rows]

    def active(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE phase = 'running' ORDER BY created_at, run_id"
            ).fetchall()
        return [self._row(row) for row in rows]

    def mark_running(self, run_id: str, worker: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        return self._update(
            run_id,
            {
                "phase": "running",
                "inspect_status": "started",
                "started_at": now,
                "worker_json": self._json(worker),
                "updated_at": now,
            },
            "run.started",
            {"inspectStatus": "started"},
        )

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        current = self.get(run_id)
        if current["cancel_requested"]:
            return current
        now = utc_now()
        return self._update(
            run_id,
            {"cancel_requested": 1, "cancel_requested_at": now, "updated_at": now},
            "run.cancel_requested",
            {},
        )

    def update_worker(self, run_id: str, worker: dict[str, Any]) -> dict[str, Any]:
        current = self.get(run_id)
        now = utc_now()
        cancel_requested = current["cancel_requested"] or bool(worker.get("cancelRequested"))
        cancel_applied = current["cancel_applied"] or bool(worker.get("cancelApplied"))
        fields: dict[str, Any] = {
            "worker_json": self._json(worker),
            "cancel_requested": int(cancel_requested),
            "cancel_applied": int(cancel_applied),
            "updated_at": now,
        }
        if cancel_requested and current.get("cancel_requested_at") is None:
            fields["cancel_requested_at"] = now
        if cancel_applied and current.get("cancel_applied_at") is None:
            fields["cancel_applied_at"] = now
        if worker.get("inspectStatus") in {"started", "success", "error", "cancelled"}:
            fields["inspect_status"] = worker["inspectStatus"]
        if worker.get("phase") == "terminal":
            fields.update(
                {
                    "phase": "terminal",
                    "outcome": worker.get("outcome"),
                    "error_type": worker.get("errorType"),
                    "error_message": worker.get("errorMessage"),
                    "replay_verified": int(bool(worker.get("replayVerified"))),
                    "terminal_at": utc_now(),
                    "evidence_state": "pending",
                }
            )
            event_type = "run.terminal"
        else:
            event_type = "run.worker_update"
        return self._update(run_id, fields, event_type, worker)

    def set_receipt(self, run_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE runs SET receipt_json=?,evidence_state='pending',updated_at=? WHERE run_id=?",
                (self._json(receipt), now, run_id),
            )
            connection.execute(
                """
                INSERT INTO outbox(run_id,next_attempt_at,state)
                VALUES(?,?,'pending')
                ON CONFLICT(run_id) DO UPDATE SET state='pending',next_attempt_at=excluded.next_attempt_at
                """,
                (run_id, now),
            )
            self._append_event_tx(connection, run_id, "evidence.pending", {})
            connection.commit()
        return self.get(run_id)

    def pending_outbox(self) -> list[dict[str, Any]]:
        now = utc_now()
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT r.*,o.attempt_count,o.last_error FROM runs r
                JOIN outbox o ON o.run_id=r.run_id
                WHERE o.state IN ('pending','failed') AND o.next_attempt_at <= ?
                ORDER BY r.created_at LIMIT 10
                """,
                (now,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def mark_evidence_synced(
        self, run_id: str, mlflow_run_id: str, receipt: dict[str, Any]
    ) -> None:
        now = utc_now()
        synced_at = (receipt.get("timestamps") or {}).get("syncedAt") or now
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE runs SET evidence_state='synced',mlflow_run_id=?,receipt_json=?,
                    evidence_synced_at=?,updated_at=?
                WHERE run_id=?
                """,
                (mlflow_run_id, self._json(receipt), synced_at, now, run_id),
            )
            connection.execute("UPDATE outbox SET state='synced',last_error=NULL WHERE run_id=?", (run_id,))
            self._append_event_tx(connection, run_id, "evidence.synced", {"mlflowRunId": mlflow_run_id})
            connection.commit()

    def mark_evidence_failed(self, run_id: str, message: str) -> None:
        now = utc_now()
        retry_at = (datetime.now(UTC) + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE runs SET evidence_state='failed',updated_at=? WHERE run_id=?", (now, run_id)
            )
            connection.execute(
                """
                UPDATE outbox SET state='failed',attempt_count=attempt_count+1,
                    next_attempt_at=?,last_error=? WHERE run_id=?
                """,
                (retry_at, message[:1000], run_id),
            )
            self._append_event_tx(connection, run_id, "evidence.retry_scheduled", {})
            connection.commit()

    def events(self, run_id: str, after_sequence: int, limit: int = 100) -> list[dict[str, Any]]:
        self.get(run_id)
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence,event_type,payload_json,created_at FROM events
                WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?
                """,
                (run_id, max(0, after_sequence), max(1, min(limit, 100))),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _update(
        self,
        run_id: str,
        fields: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not fields:
            return self.get(run_id)
        columns = ",".join(f"{name}=?" for name in fields)
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"UPDATE runs SET {columns} WHERE run_id=?", (*fields.values(), run_id)
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise KeyError(run_id)
            self._append_event_tx(connection, run_id, event_type, payload)
            connection.commit()
        return self.get(run_id)

    def _append_event_tx(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (run_id, event_type, self._json(payload), utc_now()),
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in ("cancel_requested", "cancel_applied", "replay_verified"):
            value[key] = bool(value[key])
        for key in ("request_json", "worker_json", "receipt_json"):
            if key in value and value[key] is not None:
                value[key] = json.loads(value[key])
        return value
