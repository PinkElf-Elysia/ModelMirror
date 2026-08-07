from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .runtime_models import (
    ApprovalMode,
    ApprovalRecord,
    MessageRecord,
    RuntimeEvent,
    SessionDetail,
    SessionRecord,
    TaskKind,
    TaskRecord,
    TaskStatus,
    ThinkingLevel,
)


class RuntimeStoreError(RuntimeError):
    pass


class RuntimeNotFoundError(RuntimeStoreError):
    pass


class RuntimeConflictError(RuntimeStoreError):
    pass


class AgentRuntimeStore:
    """SQLite-backed Session runtime with monotonic, replayable events."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.database_path = self.root / "agent_workspace.sqlite3"
        self.sessions_root = self.root / "sessions"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    thinking_level TEXT NOT NULL,
                    approval_mode TEXT NOT NULL,
                    skillset_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parent_session_id TEXT,
                    depth INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(parent_session_id) REFERENCES agent_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_sessions_updated
                    ON agent_sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_sessions_parent
                    ON agent_sessions(parent_session_id, created_at);

                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    thinking_level TEXT NOT NULL,
                    approval_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_session
                    ON agent_tasks(session_id, created_at);

                CREATE TABLE IF NOT EXISTS agent_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_call_id TEXT,
                    tool_calls_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_messages_session
                    ON agent_messages(session_id, sequence);

                CREATE TABLE IF NOT EXISTS agent_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_session
                    ON agent_events(session_id, sequence);

                CREATE TABLE IF NOT EXISTS agent_approvals (
                    approval_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision_message TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    decided_at REAL,
                    FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_agent_approvals_task
                    ON agent_approvals(task_id, created_at);
                """
            )

    def create_session(
        self,
        *,
        agent_id: str,
        title: str,
        model_id: str,
        thinking_level: ThinkingLevel,
        approval_mode: ApprovalMode,
        skillset_id: str,
        parent_session_id: str | None = None,
        workspace_id: str | None = None,
        depth: int = 0,
    ) -> SessionRecord:
        now = time.time()
        session_id = uuid.uuid4().hex
        effective_workspace_id = workspace_id or session_id
        with self._lock, self._connect() as connection:
            if parent_session_id:
                self._require_session_row(connection, parent_session_id)
            connection.execute(
                """
                INSERT INTO agent_sessions (
                    session_id, agent_id, workspace_id, title, model_id,
                    thinking_level, approval_mode, skillset_id, status,
                    parent_session_id, depth, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?, ?)
                """,
                (
                    session_id,
                    agent_id,
                    effective_workspace_id,
                    title,
                    model_id,
                    thinking_level,
                    approval_mode,
                    skillset_id,
                    parent_session_id,
                    depth,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                session_id=session_id,
                task_id=None,
                event_type="session_created",
                payload={"agent_id": agent_id, "parent_session_id": parent_session_id},
                created_at=now,
            )
        self.workspace_path(effective_workspace_id).mkdir(parents=True, exist_ok=True)
        return self.get_session(session_id)

    def list_sessions(self, *, include_children: bool = False) -> list[SessionRecord]:
        query = "SELECT * FROM agent_sessions"
        params: tuple[Any, ...] = ()
        if not include_children:
            query += " WHERE parent_session_id IS NULL"
        query += " ORDER BY updated_at DESC, session_id"
        with self._connect() as connection:
            return [self._session(row) for row in connection.execute(query, params)]

    def list_children(self, session_id: str) -> list[SessionRecord]:
        with self._connect() as connection:
            self._require_session_row(connection, session_id)
            rows = connection.execute(
                "SELECT * FROM agent_sessions WHERE parent_session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [self._session(row) for row in rows]

    def get_session(self, session_id: str) -> SessionRecord:
        with self._connect() as connection:
            return self._session(self._require_session_row(connection, session_id))

    def get_session_detail(self, session_id: str) -> SessionDetail:
        session = self.get_session(session_id)
        events = self.list_events(session_id, after=0, limit=1_000_000)
        return SessionDetail(
            session=session,
            messages=self.list_messages(session_id),
            tasks=self.list_tasks(session_id),
            approvals=self.list_approvals(session_id=session_id),
            last_event_sequence=events[-1].sequence if events else 0,
        )

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        approval_mode: ApprovalMode | None = None,
        read_only_tools: frozenset[str] = frozenset(),
    ) -> SessionRecord:
        if title is None and approval_mode is None:
            raise ValueError("title or approval_mode is required")
        now = time.time()
        with self._lock, self._connect() as connection:
            row = self._require_session_row(connection, session_id)
            next_title = str(row["title"]) if title is None else title
            next_mode = (
                str(row["approval_mode"])
                if approval_mode is None
                else approval_mode
            )
            connection.execute(
                """
                UPDATE agent_sessions
                SET title = ?, approval_mode = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (next_title, next_mode, now, session_id),
            )
            resolved: list[tuple[str, str, str]] = []
            if approval_mode is not None:
                connection.execute(
                    """
                    UPDATE agent_tasks SET approval_mode = ?, updated_at = ?
                    WHERE session_id = ?
                      AND status IN ('pending','running','waiting_approval')
                    """,
                    (approval_mode, now, session_id),
                )
                if approval_mode != "always-ask":
                    pending = connection.execute(
                        """
                        SELECT * FROM agent_approvals
                        WHERE session_id = ? AND status = 'pending'
                        ORDER BY created_at
                        """,
                        (session_id,),
                    ).fetchall()
                    for approval in pending:
                        approved = approval_mode == "allow-all" or (
                            approval_mode == "read-only"
                            and str(approval["tool_name"]) in read_only_tools
                        )
                        status = "approved" if approved else "rejected"
                        message = (
                            "Automatically resolved after Session approval mode "
                            f"changed to {approval_mode}."
                        )
                        connection.execute(
                            """
                            UPDATE agent_approvals
                            SET status = ?, decision_message = ?, decided_at = ?
                            WHERE approval_id = ? AND status = 'pending'
                            """,
                            (status, message, now, approval["approval_id"]),
                        )
                        resolved.append(
                            (
                                str(approval["approval_id"]),
                                str(approval["task_id"]),
                                status,
                            )
                        )
            self._insert_event(
                connection,
                session_id=session_id,
                task_id=None,
                event_type="session_updated",
                payload={"title": next_title, "approval_mode": next_mode},
                created_at=now,
            )
            if approval_mode is not None:
                self._insert_event(
                    connection,
                    session_id=session_id,
                    task_id=None,
                    event_type="approval_mode_changed",
                    payload={
                        "approval_mode": approval_mode,
                        "resolved_approval_count": len(resolved),
                    },
                    created_at=now,
                )
                for approval_id, task_id, status in resolved:
                    self._insert_event(
                        connection,
                        session_id=session_id,
                        task_id=task_id,
                        event_type="approval_decided",
                        payload={"approval_id": approval_id, "status": status},
                        created_at=now,
                    )
        return self.get_session(session_id)

    def rename_session(self, session_id: str, title: str) -> SessionRecord:
        return self.update_session(session_id, title=title)

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            row = self._require_session_row(connection, session_id)
            active = connection.execute(
                "SELECT 1 FROM agent_tasks WHERE session_id = ? AND status IN ('pending','running','waiting_approval') LIMIT 1",
                (session_id,),
            ).fetchone()
            if active:
                raise RuntimeConflictError("A running Session cannot be deleted")
            children = connection.execute(
                "SELECT 1 FROM agent_sessions WHERE parent_session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            if children:
                raise RuntimeConflictError("A Session with child Agents cannot be deleted")
            connection.execute(
                "DELETE FROM agent_sessions WHERE session_id = ?", (session_id,)
            )
        workspace_id = str(row["workspace_id"])
        if workspace_id == session_id:
            # Workspace deletion is intentionally deferred to Round 3 lifecycle controls.
            return

    def create_task(
        self,
        session_id: str,
        *,
        prompt: str,
        kind: TaskKind,
        model_id: str,
        thinking_level: ThinkingLevel,
        approval_mode: ApprovalMode,
    ) -> TaskRecord:
        now = time.time()
        task_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            self._require_session_row(connection, session_id)
            active = connection.execute(
                "SELECT 1 FROM agent_tasks WHERE session_id = ? AND status IN ('pending','running','waiting_approval') LIMIT 1",
                (session_id,),
            ).fetchone()
            if active:
                raise RuntimeConflictError("This Session already has an active task")
            connection.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, session_id, kind, prompt, model_id, thinking_level,
                    approval_mode, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    task_id,
                    session_id,
                    kind,
                    prompt,
                    model_id,
                    thinking_level,
                    approval_mode,
                    now,
                    now,
                ),
            )
            self._insert_message(
                connection,
                session_id=session_id,
                task_id=task_id,
                role="user",
                content=prompt,
                created_at=now,
            )
            connection.execute(
                "UPDATE agent_sessions SET status = 'running', updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            self._insert_event(
                connection,
                session_id=session_id,
                task_id=task_id,
                event_type="task_created",
                payload={"kind": kind, "model_id": model_id},
                created_at=now,
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise RuntimeNotFoundError(f"Task '{task_id}' was not found")
        return self._task(row)

    def list_tasks(self, session_id: str) -> list[TaskRecord]:
        with self._connect() as connection:
            self._require_session_row(connection, session_id)
            rows = connection.execute(
                "SELECT * FROM agent_tasks WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [self._task(row) for row in rows]

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        output: str | None = None,
        error: str | None = None,
        runtime_event_type: str | None = None,
        runtime_event_payload: dict[str, Any] | None = None,
    ) -> TaskRecord:
        now = time.time()
        terminal = status in {"completed", "failed", "stopped"}
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise RuntimeNotFoundError(f"Task '{task_id}' was not found")
            started_at = row["started_at"]
            if status == "running" and started_at is None:
                started_at = now
            finished_at = now if terminal else None
            connection.execute(
                """
                UPDATE agent_tasks SET status = ?, output = ?, error = ?,
                    updated_at = ?, started_at = ?, finished_at = ?
                WHERE task_id = ?
                """,
                (
                    status,
                    str(row["output"]) if output is None else output,
                    str(row["error"]) if error is None else error,
                    now,
                    started_at,
                    finished_at,
                    task_id,
                ),
            )
            session_status = (
                "idle" if status in {"completed", "stopped"} else
                "failed" if status == "failed" else
                "waiting_approval" if status == "waiting_approval" else
                "running"
            )
            connection.execute(
                "UPDATE agent_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (session_status, now, row["session_id"]),
            )
            self._insert_event(
                connection,
                session_id=str(row["session_id"]),
                task_id=task_id,
                event_type=f"task_{status}",
                payload={"status": status, "error": error or ""},
                created_at=now,
            )
            if runtime_event_type:
                self._insert_event(
                    connection,
                    session_id=str(row["session_id"]),
                    task_id=task_id,
                    event_type=runtime_event_type,
                    payload=runtime_event_payload or {},
                    created_at=now,
                )
        return self.get_task(task_id)

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        task_id: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> MessageRecord:
        now = time.time()
        with self._lock, self._connect() as connection:
            self._require_session_row(connection, session_id)
            message_id = self._insert_message(
                connection,
                session_id=session_id,
                task_id=task_id,
                role=role,
                content=content,
                tool_call_id=tool_call_id,
                tool_calls=tool_calls,
                created_at=now,
            )
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> MessageRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        if row is None:
            raise RuntimeNotFoundError(f"Message '{message_id}' was not found")
        return self._message(row)

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self._connect() as connection:
            self._require_session_row(connection, session_id)
            rows = connection.execute(
                "SELECT * FROM agent_messages WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return [self._message(row) for row in rows]

    def append_event(
        self,
        session_id: str,
        event_type: str,
        *,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        now = time.time()
        with self._lock, self._connect() as connection:
            self._require_session_row(connection, session_id)
            sequence = self._insert_event(
                connection,
                session_id=session_id,
                task_id=task_id,
                event_type=event_type,
                payload=payload or {},
                created_at=now,
            )
        return RuntimeEvent(
            sequence=sequence,
            session_id=session_id,
            task_id=task_id,
            type=event_type,
            payload=payload or {},
            created_at=now,
        )

    def list_events(
        self, session_id: str, *, after: int = 0, limit: int = 500
    ) -> list[RuntimeEvent]:
        with self._connect() as connection:
            self._require_session_row(connection, session_id)
            rows = connection.execute(
                """
                SELECT * FROM agent_events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (session_id, max(0, after), max(1, min(limit, 10_000))),
            ).fetchall()
        return [self._event(row) for row in rows]

    def create_approval(
        self,
        *,
        session_id: str,
        task_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ApprovalRecord:
        now = time.time()
        approval_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            self._require_session_row(connection, session_id)
            try:
                connection.execute(
                    """
                    INSERT INTO agent_approvals (
                        approval_id, session_id, task_id, tool_call_id,
                        tool_name, arguments_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        approval_id,
                        session_id,
                        task_id,
                        tool_call_id,
                        tool_name,
                        json.dumps(arguments, ensure_ascii=False),
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM agent_approvals WHERE tool_call_id = ?",
                    (tool_call_id,),
                ).fetchone()
                if existing is None:
                    raise
                return self._approval(existing)
            self._insert_event(
                connection,
                session_id=session_id,
                task_id=task_id,
                event_type="approval_waiting",
                payload={
                    "approval_id": approval_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                },
                created_at=now,
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        if row is None:
            raise RuntimeNotFoundError(f"Approval '{approval_id}' was not found")
        return self._approval(row)

    def list_approvals(
        self, *, session_id: str | None = None, task_id: str | None = None
    ) -> list[ApprovalRecord]:
        where: list[str] = []
        params: list[Any] = []
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if task_id:
            where.append("task_id = ?")
            params.append(task_id)
        query = "SELECT * FROM agent_approvals"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._approval(row) for row in rows]

    def decide_approval(
        self, approval_id: str, *, approved: bool, message: str = ""
    ) -> ApprovalRecord:
        now = time.time()
        status = "approved" if approved else "rejected"
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise RuntimeNotFoundError(f"Approval '{approval_id}' was not found")
            if row["status"] != "pending":
                raise RuntimeConflictError("Approval has already been decided")
            connection.execute(
                """
                UPDATE agent_approvals SET status = ?, decision_message = ?, decided_at = ?
                WHERE approval_id = ?
                """,
                (status, message, now, approval_id),
            )
            self._insert_event(
                connection,
                session_id=str(row["session_id"]),
                task_id=str(row["task_id"]),
                event_type="approval_decided",
                payload={"approval_id": approval_id, "status": status},
                created_at=now,
            )
        return self.get_approval(approval_id)

    def cancel_pending_approvals(self, task_id: str) -> None:
        now = time.time()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_approvals WHERE task_id = ? AND status = 'pending'",
                (task_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE agent_approvals SET status = 'cancelled', decided_at = ? WHERE approval_id = ?",
                    (now, row["approval_id"]),
                )
                self._insert_event(
                    connection,
                    session_id=str(row["session_id"]),
                    task_id=task_id,
                    event_type="approval_decided",
                    payload={"approval_id": row["approval_id"], "status": "cancelled"},
                    created_at=now,
                )

    def workspace_path(self, workspace_id: str) -> Path:
        candidate = (self.sessions_root / workspace_id / "workspace").resolve()
        root = self.sessions_root.resolve()
        if candidate.parent.parent != root:
            raise RuntimeStoreError("Unsafe Workspace id")
        return candidate

    def session_workspace(self, session_id: str) -> Path:
        return self.workspace_path(self.get_session(session_id).workspace_id)

    @staticmethod
    def _insert_message(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        task_id: str | None,
        role: str,
        content: str,
        created_at: float,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> str:
        message_id = uuid.uuid4().hex
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO agent_messages (
                message_id, session_id, task_id, sequence, role, content,
                tool_call_id, tool_calls_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                task_id,
                sequence,
                role,
                content,
                tool_call_id,
                json.dumps(tool_calls or [], ensure_ascii=False),
                created_at,
            ),
        )
        return message_id

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        task_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        created_at: float,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO agent_events (session_id, task_id, type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                task_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _require_session_row(
        connection: sqlite3.Connection, session_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise RuntimeNotFoundError(f"Session '{session_id}' was not found")
        return row

    @staticmethod
    def _session(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord.model_validate(dict(row))

    @staticmethod
    def _task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord.model_validate(dict(row))

    @staticmethod
    def _message(row: sqlite3.Row) -> MessageRecord:
        payload = dict(row)
        payload["tool_calls"] = json.loads(payload.pop("tool_calls_json"))
        return MessageRecord.model_validate(payload)

    @staticmethod
    def _event(row: sqlite3.Row) -> RuntimeEvent:
        payload = dict(row)
        payload["payload"] = json.loads(payload.pop("payload_json"))
        return RuntimeEvent.model_validate(payload)

    @staticmethod
    def _approval(row: sqlite3.Row) -> ApprovalRecord:
        payload = dict(row)
        payload["arguments"] = json.loads(payload.pop("arguments_json"))
        return ApprovalRecord.model_validate(payload)
