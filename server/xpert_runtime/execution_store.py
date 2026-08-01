from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


WorkflowExecutionStatus = Literal[
    "running",
    "waiting",
    "ready",
    "completed",
    "failed",
    "cancelled",
]


class WorkflowExecutionError(Exception):
    """Base error for durable workflow execution state."""


class WorkflowExecutionNotFoundError(WorkflowExecutionError):
    """Raised when an execution does not exist."""


class WorkflowExecutionConflictError(WorkflowExecutionError):
    """Raised when a lease or revision check fails."""


@dataclass(slots=True)
class WorkflowExecution:
    task_id: str
    run_id: str
    run_type: str
    status: WorkflowExecutionStatus
    workflow: dict[str, Any]
    inputs: dict[str, Any]
    runtime_metadata: dict[str, Any] = field(default_factory=dict)
    continuation: dict[str, Any] = field(default_factory=dict)
    wait_kind: str | None = None
    wait_id: str | None = None
    approval_id: str | None = None
    result: str | None = None
    error: str | None = None
    revision: int = 1
    sequence: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class WorkflowExecutionStore:
    """Atomic file-backed workflow continuation and safe event journal."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "workflow_executions.json"
        self._lock = threading.RLock()
        self._items: dict[str, WorkflowExecution] = {}
        self._load()

    def create(
        self,
        *,
        task_id: str,
        run_id: str,
        run_type: str,
        workflow: dict[str, Any],
        inputs: dict[str, Any],
        runtime_metadata: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            existing = self._items.get(task_id)
            if existing is not None:
                return existing
            item = WorkflowExecution(
                task_id=str(task_id),
                run_id=str(run_id),
                run_type=str(run_type),
                status="running",
                workflow=dict(workflow),
                inputs=dict(inputs),
                runtime_metadata=dict(runtime_metadata or {}),
            )
            self._items[item.task_id] = item
            self._persist_unlocked()
            return item

    def get(self, task_id: str) -> WorkflowExecution | None:
        with self._lock:
            return self._items.get(task_id)

    def require(self, task_id: str) -> WorkflowExecution:
        item = self.get(task_id)
        if item is None:
            raise WorkflowExecutionNotFoundError("Workflow execution not found.")
        return item

    def list_items(
        self,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[WorkflowExecution]:
        with self._lock:
            items = list(self._items.values())
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
        return items[: max(1, min(int(limit), 1000))]

    def suspend(
        self,
        task_id: str,
        *,
        approval_id: str | None = None,
        wait_kind: str = "approval",
        wait_id: str | None = None,
        continuation: dict[str, Any],
        safe_event: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            item.status = "waiting"
            resolved_wait_id = str(wait_id or approval_id or "").strip()
            if not resolved_wait_id:
                raise WorkflowExecutionConflictError("A wait identifier is required.")
            item.wait_kind = str(wait_kind or "approval")
            item.wait_id = resolved_wait_id
            item.approval_id = (
                resolved_wait_id if item.wait_kind == "approval" else None
            )
            item.continuation = dict(continuation)
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = 0.0
            item.updated_at = time.time()
            item.revision += 1
            if safe_event is not None:
                self._append_event_unlocked(item, safe_event)
            self._persist_unlocked()
            return item

    def mark_ready(
        self,
        task_id: str,
        *,
        approval_id: str | None = None,
        wait_kind: str = "approval",
        wait_id: str | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status not in {"waiting", "ready"}:
                raise WorkflowExecutionConflictError(
                    f"Workflow execution cannot resume from {item.status}."
                )
            resolved_wait_id = str(wait_id or approval_id or "").strip()
            if item.wait_kind != wait_kind or item.wait_id != resolved_wait_id:
                raise WorkflowExecutionConflictError("Wait target does not match execution.")
            item.status = "ready"
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> WorkflowExecution:
        current = time.time() if now is None else float(now)
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.status == "running" and item.lease_expires_at > current:
                raise WorkflowExecutionConflictError("Workflow execution is already leased.")
            if item.status not in {"ready", "running"}:
                raise WorkflowExecutionConflictError(
                    f"Workflow execution cannot be claimed from {item.status}."
                )
            item.status = "running"
            item.lease_owner = str(worker_id)
            item.lease_token = uuid.uuid4().hex
            item.lease_expires_at = current + max(1.0, float(lease_seconds))
            item.updated_at = current
            item.revision += 1
            self._persist_unlocked()
            return item

    def refresh_lease(
        self,
        task_id: str,
        *,
        lease_token: str,
        lease_seconds: float = 60.0,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            if item.lease_token != lease_token:
                raise WorkflowExecutionConflictError("Workflow execution lease changed.")
            item.lease_expires_at = time.time() + max(1.0, float(lease_seconds))
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def append_event(self, task_id: str, event: dict[str, Any]) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            self._append_event_unlocked(item, event)
            item.updated_at = time.time()
            self._persist_unlocked()
            return item

    def update_run_id(self, task_id: str, *, run_id: str) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            item.run_id = str(run_id)
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def complete(self, task_id: str, *, result: str) -> WorkflowExecution:
        return self._finish(task_id, status="completed", result=result)

    def fail(self, task_id: str, *, error: str) -> WorkflowExecution:
        return self._finish(task_id, status="failed", error=error)

    def cancel(self, task_id: str, *, error: str = "cancelled") -> WorkflowExecution:
        return self._finish(task_id, status="cancelled", error=error)

    def _finish(
        self,
        task_id: str,
        *,
        status: WorkflowExecutionStatus,
        result: str | None = None,
        error: str | None = None,
    ) -> WorkflowExecution:
        with self._lock:
            item = self._require_unlocked(task_id)
            item.status = status
            item.result = str(result or "")[:200_000] if result is not None else item.result
            item.error = str(error or "")[:4_000] if error is not None else None
            item.approval_id = None
            item.wait_kind = None
            item.wait_id = None
            item.continuation = {}
            item.lease_owner = None
            item.lease_token = None
            item.lease_expires_at = 0.0
            item.updated_at = time.time()
            item.completed_at = item.updated_at
            item.revision += 1
            self._persist_unlocked()
            return item

    @staticmethod
    def serialize_public(item: WorkflowExecution) -> dict[str, Any]:
        return {
            "task_id": item.task_id,
            "run_id": item.run_id,
            "run_type": item.run_type,
            "status": item.status,
            "approval_id": item.approval_id,
            "wait_kind": item.wait_kind,
            "wait_id": item.wait_id,
            "result": item.result,
            "error": item.error,
            "revision": item.revision,
            "sequence": item.sequence,
            "events": list(item.events),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "completed_at": item.completed_at,
        }

    def _append_event_unlocked(
        self,
        item: WorkflowExecution,
        event: dict[str, Any],
    ) -> None:
        item.sequence += 1
        clean = self._safe_event(event)
        clean["sequence"] = item.sequence
        item.events.append(clean)
        if len(item.events) > 500:
            item.events = item.events[-500:]

    @staticmethod
    def _safe_event(event: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "event",
            "task_id",
            "run_id",
            "node_id",
            "node_title",
            "node_type",
            "approval_id",
            "wait_kind",
            "wait_id",
            "approval_status",
            "request_type",
            "request_id",
            "request_status",
            "host_id",
            "tool_name",
            "message",
            "final_output",
            "variable",
        }
        clean = {key: value for key, value in event.items() if key in allowed}
        for key in ("message", "final_output"):
            if key in clean:
                clean[key] = str(clean[key] or "")[:200_000]
        return clean

    def _require_unlocked(self, task_id: str) -> WorkflowExecution:
        item = self._items.get(task_id)
        if item is None:
            raise WorkflowExecutionNotFoundError("Workflow execution not found.")
        return item

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "workflow-executions-v1",
            "items": [asdict(item) for item in self._items.values()],
        }
        temporary = self.snapshot_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.snapshot_path)

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            for raw in payload.get("items", []):
                if not isinstance(raw, dict):
                    continue
                if not raw.get("wait_kind") and raw.get("approval_id"):
                    raw["wait_kind"] = "approval"
                    raw["wait_id"] = raw.get("approval_id")
                item = WorkflowExecution(**raw)
                if item.status == "running":
                    item.status = "ready" if item.wait_id is None else "waiting"
                    item.lease_owner = None
                    item.lease_token = None
                    item.lease_expires_at = 0.0
                self._items[item.task_id] = item
        except Exception:
            self._items = {}
