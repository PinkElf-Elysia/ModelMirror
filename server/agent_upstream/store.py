from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    from server.agent_workspace.tools import BuiltinToolRunner, ToolExecutionError
except ImportError:  # pragma: no cover - container package layout
    from agent_workspace.tools import BuiltinToolRunner, ToolExecutionError

from .models import (
    TERMINAL_STATUSES,
    EngineShadowEvent,
    EngineShadowRunCreate,
    EngineShadowRunDetail,
    EngineShadowRunRecord,
    EngineShadowWorkspaceEntry,
    ResolvedShadowModel,
)
from .tools import compute_shadow_candidate_sha256


class EngineShadowStoreError(RuntimeError):
    pass


class EngineShadowNotFound(EngineShadowStoreError):
    pass


class EngineShadowConflict(EngineShadowStoreError):
    pass


class EngineShadowStore:
    """SQLite metadata and isolated workspaces for upstream shadow runs."""

    def __init__(self, root: Path | None = None) -> None:
        configured = os.getenv("AGENT_WORKSPACE_ROOT", "/data/agent-workspace")
        self.root = (root or Path(configured)).resolve()
        self.runs_root = self.root / "upstream-shadow"
        self.database_path = self.root / "agent_workspace.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._reconcile_interrupted()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_upstream_shadow_runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    model_base_id TEXT NOT NULL,
                    resolved_model_id TEXT NOT NULL,
                    thinking_level TEXT NOT NULL,
                    token_budget INTEGER NOT NULL,
                    max_goal_rounds INTEGER NOT NULL,
                    max_task_turns INTEGER NOT NULL,
                    goal_round INTEGER NOT NULL DEFAULT 0,
                    model_turns INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    token_total INTEGER NOT NULL DEFAULT 0,
                    usage_source TEXT NOT NULL DEFAULT 'none',
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    tool_failures INTEGER NOT NULL DEFAULT 0,
                    candidate_sha256 TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    public_error TEXT NOT NULL DEFAULT '',
                    upstream_revision TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL
                );
                CREATE TABLE IF NOT EXISTS agent_upstream_shadow_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES agent_upstream_shadow_runs(run_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_upstream_shadow_runs_updated
                    ON agent_upstream_shadow_runs(updated_at DESC);
                """
            )

    def _reconcile_interrupted(self) -> None:
        now = time.time()
        with self._connect() as connection:
            active = connection.execute(
                "SELECT run_id FROM agent_upstream_shadow_runs WHERE status IN ('pending','running')"
            ).fetchall()
            for row in active:
                run_id = str(row["run_id"])
                connection.execute(
                    """
                    UPDATE agent_upstream_shadow_runs
                    SET status='interrupted', error_code='server_restarted',
                        public_error='The server restarted while the upstream shadow run was active.',
                        updated_at=?, finished_at=?
                    WHERE run_id=?
                    """,
                    (now, now, run_id),
                )
                self._append_event_tx(
                    connection,
                    run_id,
                    "interrupted",
                    {"reason": "server_restarted"},
                    created_at=now,
                )

    def create_run(
        self, payload: EngineShadowRunCreate, model: ResolvedShadowModel
    ) -> EngineShadowRunRecord:
        run_id = uuid.uuid4().hex
        session_id = f"upstream-{uuid.uuid4().hex}"
        run_root = self.run_root(run_id)
        workspace = run_root / "workspace"
        now = time.time()
        try:
            (workspace / ".modelmirror").mkdir(parents=True, exist_ok=False)
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO agent_upstream_shadow_runs (
                        run_id, session_id, status, objective, model_base_id,
                        resolved_model_id, thinking_level, token_budget,
                        max_goal_rounds, max_task_turns, upstream_revision,
                        protocol, created_at, updated_at
                    ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?,
                              '047505dccc0cc16ad92be11011347d635f33ceb0',
                              'modelmirror.upstream-workbench/1', ?, ?)
                    """,
                    (
                        run_id,
                        session_id,
                        payload.objective,
                        payload.model_base_id,
                        model.invocation_id,
                        payload.thinking_level,
                        payload.token_budget,
                        payload.max_goal_rounds,
                        payload.max_task_turns,
                        now,
                        now,
                    ),
                )
                self._append_event_tx(
                    connection,
                    run_id,
                    "run_created",
                    {
                        "model_base_id": payload.model_base_id,
                        "thinking_level": payload.thinking_level,
                        "token_budget": payload.token_budget,
                    },
                    created_at=now,
                )
        except Exception:
            import shutil

            shutil.rmtree(run_root, ignore_errors=True)
            raise
        return self.get_run(run_id)

    def list_runs(self, *, limit: int = 100) -> list[EngineShadowRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_upstream_shadow_runs ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [self._run(row) for row in rows]

    def get_run(self, run_id: str) -> EngineShadowRunRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_upstream_shadow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise EngineShadowNotFound("Upstream shadow run was not found")
        return self._run(row)

    def get_detail(self, run_id: str) -> EngineShadowRunDetail:
        run = self.get_run(run_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM agent_upstream_shadow_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return EngineShadowRunDetail(run=run, last_event_sequence=int(row["value"]))

    def mark_running(self, run_id: str) -> EngineShadowRunRecord:
        now = time.time()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE agent_upstream_shadow_runs
                SET status='running', started_at=COALESCE(started_at, ?), updated_at=?
                WHERE run_id=? AND status='pending'
                """,
                (now, now, run_id),
            ).rowcount
            if updated != 1:
                raise EngineShadowConflict("Upstream shadow run is no longer pending")
            self._append_event_tx(connection, run_id, "run_started", {}, created_at=now)
        return self.get_run(run_id)

    def update_progress(self, run_id: str, **fields: Any) -> EngineShadowRunRecord:
        allowed = {
            "goal_round",
            "model_turns",
            "retry_count",
            "token_total",
            "usage_source",
            "tool_calls",
            "tool_failures",
        }
        clean = {key: value for key, value in fields.items() if key in allowed}
        if not clean:
            return self.get_run(run_id)
        assignments = ", ".join(f"{key}=?" for key in clean)
        with self._connect() as connection:
            updated = connection.execute(
                f"UPDATE agent_upstream_shadow_runs SET {assignments}, updated_at=? WHERE run_id=? AND status='running'",
                (*clean.values(), time.time(), run_id),
            ).rowcount
        if updated != 1:
            raise EngineShadowConflict("Upstream shadow run is not active")
        return self.get_run(run_id)

    def finish(
        self,
        run_id: str,
        status: str,
        *,
        candidate_sha256: str = "",
        error_code: str = "",
        public_error: str = "",
        progress: dict[str, Any] | None = None,
    ) -> EngineShadowRunRecord:
        if status not in TERMINAL_STATUSES:
            raise EngineShadowStoreError(f"invalid terminal status: {status}")
        now = time.time()
        already_finished = False
        progress = progress or {}
        allowed = {
            key: value
            for key, value in progress.items()
            if key
            in {
                "goal_round",
                "model_turns",
                "retry_count",
                "token_total",
                "usage_source",
                "tool_calls",
                "tool_failures",
            }
        }
        assignments = ["status=?", "candidate_sha256=?", "error_code=?", "public_error=?", "updated_at=?", "finished_at=?"]
        values: list[Any] = [status, candidate_sha256, error_code, public_error, now, now]
        for key, value in allowed.items():
            assignments.append(f"{key}=?")
            values.append(value)
        values.append(run_id)
        with self._connect() as connection:
            updated = connection.execute(
                f"UPDATE agent_upstream_shadow_runs SET {', '.join(assignments)} WHERE run_id=? AND status IN ('pending','running')",
                values,
            ).rowcount
            if updated != 1:
                current = connection.execute(
                    "SELECT status FROM agent_upstream_shadow_runs WHERE run_id=?", (run_id,)
                ).fetchone()
                if current is None:
                    raise EngineShadowNotFound("Upstream shadow run was not found")
                if str(current["status"]) != status:
                    raise EngineShadowConflict("Upstream shadow run is already terminal")
                already_finished = True
            if not already_finished:
                self._append_event_tx(
                    connection,
                    run_id,
                    status,
                    {"error_code": error_code, "candidate_sha256": candidate_sha256},
                    created_at=now,
                )
        return self.get_run(run_id)

    def append_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> EngineShadowEvent:
        with self._connect() as connection:
            return self._append_event_tx(connection, run_id, event_type, payload)

    def _append_event_tx(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        created_at: float | None = None,
    ) -> EngineShadowEvent:
        exists = connection.execute(
            "SELECT 1 FROM agent_upstream_shadow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if exists is None:
            raise EngineShadowNotFound("Upstream shadow run was not found")
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM agent_upstream_shadow_events WHERE run_id=?",
            (run_id,),
        ).fetchone()
        sequence = int(row["value"])
        timestamp = created_at if created_at is not None else time.time()
        connection.execute(
            "INSERT INTO agent_upstream_shadow_events(run_id,sequence,type,payload_json,created_at) VALUES(?,?,?,?,?)",
            (
                run_id,
                sequence,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            ),
        )
        return EngineShadowEvent(
            sequence=sequence,
            run_id=run_id,
            type=event_type,
            payload=payload,
            created_at=timestamp,
        )

    def list_events(
        self, run_id: str, *, after: int = 0, limit: int = 500
    ) -> list[EngineShadowEvent]:
        self.get_run(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_upstream_shadow_events
                WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?
                """,
                (run_id, max(0, after), max(1, min(limit, 1000))),
            ).fetchall()
        return [
            EngineShadowEvent(
                sequence=int(row["sequence"]),
                run_id=str(row["run_id"]),
                type=str(row["type"]),
                payload=json.loads(str(row["payload_json"])),
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    def run_root(self, run_id: str) -> Path:
        if not run_id or any(character not in "0123456789abcdef" for character in run_id):
            raise EngineShadowNotFound("Invalid upstream shadow run id")
        return self.runs_root / run_id

    def workspace(self, run_id: str) -> Path:
        self.get_run(run_id)
        path = self.run_root(run_id) / "workspace"
        if not path.is_dir():
            raise EngineShadowStoreError("Upstream shadow workspace is missing")
        return path.resolve(strict=True)

    def list_workspace(
        self, run_id: str, relative_path: str = ""
    ) -> list[EngineShadowWorkspaceEntry]:
        workspace = self.workspace(run_id)
        if relative_path.strip():
            directory = BuiltinToolRunner.resolve_read(workspace, relative_path)
        else:
            directory = workspace
        if not directory.is_dir():
            raise ToolExecutionError("Shadow Workspace path is not a directory")
        return [
            EngineShadowWorkspaceEntry(
                name=item.name,
                path=item.relative_to(workspace).as_posix(),
                kind="directory" if item.is_dir() else "file",
                size=0 if item.is_dir() else item.stat().st_size,
                modified_at=item.stat().st_mtime,
            )
            for item in sorted(
                directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
            )
        ]

    def read_workspace_file(self, run_id: str, relative_path: str) -> tuple[str, int]:
        workspace = self.workspace(run_id)
        path = BuiltinToolRunner.resolve_read(workspace, relative_path)
        if not path.is_file():
            raise ToolExecutionError("Shadow Workspace path is not a file")
        if path.stat().st_size > 512_000:
            raise ToolExecutionError("File is too large for text preview")
        return path.read_text(encoding="utf-8", errors="replace"), path.stat().st_size

    def candidate_hash(self, run_id: str) -> str:
        return compute_shadow_candidate_sha256(self.workspace(run_id))

    @staticmethod
    def _run(row: sqlite3.Row) -> EngineShadowRunRecord:
        return EngineShadowRunRecord(**dict(row))
