from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


ApprovalStatus = Literal["pending", "decided", "expired", "cancelled"]
ApprovalDecision = Literal["approve", "edit", "reject", "replace", "revise"]
ApprovalRequestType = Literal[
    "tool_call", "final_output", "manual_input", "browser_domain"
]

APPROVAL_STATUSES = {"pending", "decided", "expired", "cancelled"}
APPROVAL_DECISIONS = {"approve", "edit", "reject", "replace", "revise"}


class RuntimeApprovalError(Exception):
    """Base error for durable runtime approvals."""


class RuntimeApprovalNotFoundError(RuntimeApprovalError):
    """Raised when an approval request does not exist."""


class RuntimeApprovalConflictError(RuntimeApprovalError):
    """Raised when an approval revision or state changed concurrently."""


class RuntimeApprovalValidationError(RuntimeApprovalError):
    """Raised when an approval payload is invalid."""


@dataclass(slots=True)
class RuntimeApprovalRequest:
    approval_id: str
    action_key: str
    request_type: ApprovalRequestType
    task_id: str
    run_id: str
    node_id: str
    node_title: str
    status: ApprovalStatus = "pending"
    revision: int = 1
    scope_type: str = "workflow"
    scope_id: str = ""
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    content_preview: str = ""
    allowed_decisions: list[str] = field(default_factory=list)
    decision: ApprovalDecision | None = None
    edited_arguments: dict[str, Any] | None = None
    replacement_text: str | None = None
    message: str | None = None
    operator: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    decided_at: float | None = None


class RuntimeApprovalStore:
    """Atomic file-backed approval requests with optimistic revisions."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        package_dir = Path(__file__).resolve().parent
        self.storage_dir = Path(
            storage_dir
            or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
            or package_dir / "storage"
        )
        self.snapshot_path = self.storage_dir / "runtime_approvals.json"
        self._lock = threading.RLock()
        self._items: dict[str, RuntimeApprovalRequest] = {}
        self._load()

    def create_request(
        self,
        *,
        action_key: str,
        request_type: str,
        task_id: str,
        run_id: str,
        node_id: str,
        node_title: str,
        scope_type: str,
        scope_id: str,
        timeout_seconds: int,
        allowed_decisions: list[str],
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        description: str = "",
        content_preview: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeApprovalRequest:
        clean_action_key = self._required_text(action_key, "action_key", 500)
        clean_request_type = str(request_type or "").strip()
        if clean_request_type not in {
            "tool_call",
            "final_output",
            "manual_input",
            "browser_domain",
        }:
            raise RuntimeApprovalValidationError("Unsupported approval request_type.")
        clean_decisions = self._decisions(allowed_decisions)
        clean_timeout = max(30, min(int(timeout_seconds), 86_400))
        now = time.time()
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._items.values()
                    if item.action_key == clean_action_key
                    and item.status in {"pending", "decided"}
                ),
                None,
            )
            if existing is not None:
                return existing
            item = RuntimeApprovalRequest(
                approval_id=str(uuid.uuid4()),
                action_key=clean_action_key,
                request_type=clean_request_type,  # type: ignore[arg-type]
                task_id=self._required_text(task_id, "task_id", 200),
                run_id=self._required_text(run_id, "run_id", 200),
                node_id=self._required_text(node_id, "node_id", 200),
                node_title=str(node_title or "Runtime approval").strip()[:300],
                scope_type=str(scope_type or "workflow").strip()[:80] or "workflow",
                scope_id=str(scope_id or task_id).strip()[:400],
                tool_name=self._optional_text(tool_name, 300),
                arguments=self.redact(arguments or {}),
                description=str(description or "").strip()[:4_000],
                content_preview=str(content_preview or "").strip()[:8_000],
                allowed_decisions=clean_decisions,
                metadata=self.redact(dict(metadata or {})),
                created_at=now,
                updated_at=now,
                expires_at=now + clean_timeout,
            )
            self._items[item.approval_id] = item
            self._persist_unlocked()
            return item

    def get(self, approval_id: str) -> RuntimeApprovalRequest | None:
        with self._lock:
            return self._items.get(approval_id)

    def require(self, approval_id: str) -> RuntimeApprovalRequest:
        item = self.get(approval_id)
        if item is None:
            raise RuntimeApprovalNotFoundError("Runtime approval not found.")
        return item

    def list_requests(
        self,
        *,
        status: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[RuntimeApprovalRequest]:
        clean_status = str(status or "").strip() or None
        if clean_status and clean_status not in APPROVAL_STATUSES:
            raise RuntimeApprovalValidationError("Unsupported approval status.")
        with self._lock:
            items = list(self._items.values())
        if clean_status:
            items = [item for item in items if item.status == clean_status]
        if task_id:
            items = [item for item in items if item.task_id == task_id]
        if run_id:
            items = [item for item in items if item.run_id == run_id]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        items.sort(key=lambda item: (item.created_at, item.approval_id), reverse=True)
        return items[: max(1, min(int(limit), 500))]

    def decide(
        self,
        approval_id: str,
        *,
        revision: int,
        decision: str,
        operator: str,
        edited_arguments: dict[str, Any] | None = None,
        replacement_text: str | None = None,
        message: str | None = None,
    ) -> RuntimeApprovalRequest:
        clean_decision = str(decision or "").strip()
        if clean_decision not in APPROVAL_DECISIONS:
            raise RuntimeApprovalValidationError("Unsupported approval decision.")
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                raise RuntimeApprovalNotFoundError("Runtime approval not found.")
            if item.revision != int(revision):
                raise RuntimeApprovalConflictError(
                    f"Runtime approval revision conflict: expected {item.revision}."
                )
            if item.status != "pending":
                raise RuntimeApprovalConflictError(
                    f"Runtime approval cannot be decided from {item.status}."
                )
            if clean_decision not in item.allowed_decisions:
                raise RuntimeApprovalValidationError(
                    f"Decision {clean_decision} is not allowed for this approval."
                )
            if clean_decision == "edit" and not isinstance(edited_arguments, dict):
                raise RuntimeApprovalValidationError(
                    "edited_arguments must be an object for edit decisions."
                )
            if clean_decision == "replace" and not str(replacement_text or "").strip():
                raise RuntimeApprovalValidationError(
                    "replacement_text is required for replace decisions."
                )
            now = time.time()
            item.status = "decided"
            item.decision = clean_decision  # type: ignore[assignment]
            item.edited_arguments = (
                dict(edited_arguments or {}) if clean_decision == "edit" else None
            )
            item.replacement_text = (
                str(replacement_text or "")[:100_000]
                if clean_decision == "replace"
                else None
            )
            item.message = str(message or "").strip()[:4_000] or None
            item.operator = self._required_text(operator or "local-operator", "operator", 200)
            item.decided_at = now
            item.updated_at = now
            item.revision += 1
            self._persist_unlocked()
            return item

    def reopen(
        self,
        approval_id: str,
        *,
        revision: int,
        timeout_seconds: int = 3600,
        operator: str = "local-operator",
    ) -> RuntimeApprovalRequest:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                raise RuntimeApprovalNotFoundError("Runtime approval not found.")
            if item.revision != int(revision):
                raise RuntimeApprovalConflictError(
                    f"Runtime approval revision conflict: expected {item.revision}."
                )
            if item.status != "expired":
                raise RuntimeApprovalConflictError(
                    "Only expired approvals can be reopened."
                )
            now = time.time()
            item.status = "pending"
            item.decision = None
            item.edited_arguments = None
            item.replacement_text = None
            item.message = None
            item.operator = self._required_text(operator, "operator", 200)
            item.decided_at = None
            item.expires_at = now + max(30, min(int(timeout_seconds), 86_400))
            item.updated_at = now
            item.revision += 1
            self._persist_unlocked()
            return item

    def cancel(
        self,
        approval_id: str,
        *,
        revision: int,
        operator: str = "local-operator",
        message: str = "cancelled",
    ) -> RuntimeApprovalRequest:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                raise RuntimeApprovalNotFoundError("Runtime approval not found.")
            if item.revision != int(revision):
                raise RuntimeApprovalConflictError(
                    f"Runtime approval revision conflict: expected {item.revision}."
                )
            if item.status not in {"pending", "expired"}:
                raise RuntimeApprovalConflictError(
                    f"Runtime approval cannot be cancelled from {item.status}."
                )
            item.status = "cancelled"
            item.operator = self._required_text(operator, "operator", 200)
            item.message = str(message or "cancelled")[:4_000]
            item.updated_at = time.time()
            item.revision += 1
            self._persist_unlocked()
            return item

    def expire_due(self, *, now: float | None = None) -> list[RuntimeApprovalRequest]:
        current = time.time() if now is None else float(now)
        expired: list[RuntimeApprovalRequest] = []
        with self._lock:
            for item in self._items.values():
                if item.status == "pending" and item.expires_at <= current:
                    item.status = "expired"
                    item.updated_at = current
                    item.revision += 1
                    expired.append(item)
            if expired:
                self._persist_unlocked()
        return expired

    @staticmethod
    def serialize(item: RuntimeApprovalRequest) -> dict[str, Any]:
        payload = asdict(item)
        payload["arguments"] = RuntimeApprovalStore.redact(item.arguments)
        if item.edited_arguments is not None:
            payload["edited_arguments"] = RuntimeApprovalStore.redact(
                item.edited_arguments
            )
        return payload

    @classmethod
    def redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): (
                    "[REDACTED]"
                    if cls._looks_sensitive(str(key))
                    else cls.redact(inner)
                )
                for key, inner in value.items()
            }
        if isinstance(value, list):
            return [cls.redact(item) for item in value[:200]]
        if isinstance(value, tuple):
            return [cls.redact(item) for item in value[:200]]
        if isinstance(value, str):
            return value[:20_000]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:2_000]

    @staticmethod
    def _looks_sensitive(name: str) -> bool:
        normalized = name.lower().replace("-", "_")
        return any(
            token in normalized
            for token in ("api_key", "apikey", "password", "passwd", "secret", "token", "authorization")
        )

    def _persist_unlocked(self) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "runtime-approvals-v1",
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
                item = RuntimeApprovalRequest(**raw)
                self._items[item.approval_id] = item
        except Exception:
            self._items = {}

    @staticmethod
    def _required_text(value: Any, name: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise RuntimeApprovalValidationError(f"{name} is required.")
        if len(text) > limit:
            raise RuntimeApprovalValidationError(f"{name} exceeds {limit} characters.")
        return text

    @staticmethod
    def _optional_text(value: Any, limit: int) -> str | None:
        text = str(value or "").strip()
        return text[:limit] or None

    @staticmethod
    def _decisions(values: list[str]) -> list[str]:
        decisions: list[str] = []
        for value in values:
            clean = str(value or "").strip()
            if clean not in APPROVAL_DECISIONS:
                raise RuntimeApprovalValidationError(
                    f"Unsupported approval decision: {clean}."
                )
            if clean not in decisions:
                decisions.append(clean)
        if not decisions:
            raise RuntimeApprovalValidationError("At least one decision is required.")
        return decisions
