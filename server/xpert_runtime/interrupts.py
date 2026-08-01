from __future__ import annotations

from typing import Any


class RuntimeInterrupt(Exception):
    """Non-fail-open signal used to suspend a durable runtime execution."""

    def __init__(
        self,
        approval_id: str | None = None,
        *,
        task_id: str,
        run_id: str,
        wait_kind: str = "approval",
        wait_id: str | None = None,
        continuation: dict[str, Any] | None = None,
    ) -> None:
        resolved_wait_id = str(wait_id or approval_id or "").strip()
        if not resolved_wait_id:
            raise ValueError("RuntimeInterrupt requires a wait identifier.")
        self.wait_kind = str(wait_kind or "approval")
        self.wait_id = resolved_wait_id
        super().__init__(f"Runtime wait required: {self.wait_kind}:{self.wait_id}")
        self.approval_id = (
            resolved_wait_id if self.wait_kind == "approval" else None
        )
        self.task_id = task_id
        self.run_id = run_id
        self.continuation = dict(continuation or {})


class RuntimeMiddlewareFatalError(Exception):
    """Middleware error that must never fall back to executing a provider."""
