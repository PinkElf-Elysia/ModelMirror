from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


TodoScopeType = Literal["conversation", "goal", "handoff", "workflow", "app_run"]
TodoStatus = Literal["pending", "in_progress", "completed", "cancelled", "archived"]
TODO_STATUSES: set[str] = {
    "pending",
    "in_progress",
    "completed",
    "cancelled",
    "archived",
}
TODO_SCOPE_TYPES: set[str] = {
    "conversation",
    "goal",
    "handoff",
    "workflow",
    "app_run",
}


class RuntimeTodoError(Exception):
    """Base error for runtime Todo operations."""


class RuntimeTodoNotFoundError(RuntimeTodoError):
    """Raised when a Todo item does not exist in the requested scope."""


class RuntimeTodoConflictError(RuntimeTodoError):
    """Raised when an optimistic revision check fails."""


class RuntimeTodoValidationError(RuntimeTodoError):
    """Raised when a Todo payload is invalid."""


@dataclass(slots=True)
class RuntimeTodoItem:
    todo_id: str
    scope_type: TodoScopeType
    scope_id: str
    title: str
    details: str = ""
    status: TodoStatus = "pending"
    priority: int = 0
    order: int = 0
    revision: int = 1
    source_run_id: str | None = None
    created_by: str = "runtime"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class RuntimeTodoStore:
    """Atomic file-backed Todo store with ephemeral public-App scopes."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        fallback = os.getenv("XPERT_CONTEXT_STORAGE_DIR", "").strip()
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or fallback
            or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "todos.json"
        self._lock = threading.RLock()
        self._items: dict[str, RuntimeTodoItem] = {}
        self._ephemeral_items: dict[str, RuntimeTodoItem] = {}
        self._load()

    def list_items(
        self,
        *,
        scope_type: str,
        scope_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeTodoItem]:
        clean_scope_type = self._scope_type(scope_type)
        clean_scope_id = self._required_text(scope_id, "scope_id", 300)
        clean_status = self._status(status) if status else None
        with self._lock:
            source = self._source_for_scope(clean_scope_type)
            items = [
                item
                for item in source.values()
                if item.scope_type == clean_scope_type
                and item.scope_id == clean_scope_id
                and (clean_status is None or item.status == clean_status)
            ]
        items.sort(key=lambda item: (item.order, -item.priority, item.created_at, item.todo_id))
        return items[: max(1, min(int(limit), 500))]

    def create_item(
        self,
        *,
        scope_type: str,
        scope_id: str,
        title: str,
        details: str = "",
        status: str = "pending",
        priority: int = 0,
        order: int | None = None,
        source_run_id: str | None = None,
        created_by: str = "runtime",
    ) -> RuntimeTodoItem:
        clean_scope_type = self._scope_type(scope_type)
        clean_scope_id = self._required_text(scope_id, "scope_id", 300)
        clean_title = self._required_text(title, "title", 500)
        clean_details = str(details or "").strip()[:10_000]
        clean_status = self._status(status)
        clean_priority = max(-10, min(int(priority), 10))
        with self._lock:
            source = self._source_for_scope(clean_scope_type)
            if order is None:
                scope_items = [
                    item
                    for item in source.values()
                    if item.scope_type == clean_scope_type and item.scope_id == clean_scope_id
                ]
                clean_order = max((item.order for item in scope_items), default=-1) + 1
            else:
                clean_order = max(0, min(int(order), 10_000))
            item = RuntimeTodoItem(
                todo_id=str(uuid.uuid4()),
                scope_type=clean_scope_type,
                scope_id=clean_scope_id,
                title=clean_title,
                details=clean_details,
                status=clean_status,
                priority=clean_priority,
                order=clean_order,
                source_run_id=self._optional_text(source_run_id, 200),
                created_by=self._required_text(created_by or "runtime", "created_by", 120),
            )
            source[item.todo_id] = item
            self._persist_if_needed_unlocked(clean_scope_type)
            return item

    def update_item(
        self,
        todo_id: str,
        *,
        scope_type: str,
        scope_id: str,
        revision: int,
        patch: dict[str, Any],
    ) -> RuntimeTodoItem:
        clean_scope_type = self._scope_type(scope_type)
        clean_scope_id = self._required_text(scope_id, "scope_id", 300)
        with self._lock:
            source = self._source_for_scope(clean_scope_type)
            item = source.get(todo_id)
            if (
                item is None
                or item.scope_type != clean_scope_type
                or item.scope_id != clean_scope_id
            ):
                raise RuntimeTodoNotFoundError("Runtime Todo not found.")
            if item.revision != int(revision):
                raise RuntimeTodoConflictError(
                    f"Runtime Todo revision conflict: expected {item.revision}."
                )
            allowed = {"title", "details", "status", "priority", "order"}
            unknown = set(patch) - allowed
            if unknown:
                raise RuntimeTodoValidationError(
                    f"Unsupported Todo fields: {', '.join(sorted(unknown))}."
                )
            if "title" in patch:
                item.title = self._required_text(patch["title"], "title", 500)
            if "details" in patch:
                item.details = str(patch["details"] or "").strip()[:10_000]
            if "status" in patch:
                item.status = self._status(patch["status"])
            if "priority" in patch:
                item.priority = max(-10, min(int(patch["priority"]), 10))
            if "order" in patch:
                item.order = max(0, min(int(patch["order"]), 10_000))
            item.revision += 1
            item.updated_at = time.time()
            self._persist_if_needed_unlocked(clean_scope_type)
            return item

    def archive_item(
        self,
        todo_id: str,
        *,
        scope_type: str,
        scope_id: str,
        revision: int,
    ) -> RuntimeTodoItem:
        return self.update_item(
            todo_id,
            scope_type=scope_type,
            scope_id=scope_id,
            revision=revision,
            patch={"status": "archived"},
        )

    @staticmethod
    def serialize(item: RuntimeTodoItem) -> dict[str, Any]:
        return asdict(item)

    def _source_for_scope(self, scope_type: TodoScopeType) -> dict[str, RuntimeTodoItem]:
        return self._ephemeral_items if scope_type == "app_run" else self._items

    def _persist_if_needed_unlocked(self, scope_type: TodoScopeType) -> None:
        if scope_type != "app_run":
            self._persist_unlocked()

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "runtime-todos-v1",
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
            items = payload.get("items", []) if isinstance(payload, dict) else []
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item = RuntimeTodoItem(**raw)
                if item.scope_type == "app_run":
                    continue
                self._items[item.todo_id] = item
        except Exception:
            self._items = {}

    @staticmethod
    def _required_text(value: Any, field_name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise RuntimeTodoValidationError(f"{field_name} is required.")
        if len(text) > limit:
            raise RuntimeTodoValidationError(
                f"{field_name} exceeds the {limit} character limit."
            )
        return text

    @staticmethod
    def _optional_text(value: Any, limit: int) -> str | None:
        text = str(value or "").strip()
        return text[:limit] or None

    @staticmethod
    def _scope_type(value: Any) -> TodoScopeType:
        text = str(value or "").strip()
        if text not in TODO_SCOPE_TYPES:
            raise RuntimeTodoValidationError("Unsupported Todo scope_type.")
        return text  # type: ignore[return-value]

    @staticmethod
    def _status(value: Any) -> TodoStatus:
        text = str(value or "").strip()
        if text not in TODO_STATUSES:
            raise RuntimeTodoValidationError("Unsupported Todo status.")
        return text  # type: ignore[return-value]
