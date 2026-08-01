from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Literal


TaskStatus = Literal["pending", "running", "succeeded", "failed", "cancelled", "dead"]
EventSeverity = Literal["debug", "info", "warning", "error"]


@dataclass(slots=True)
class RuntimeEvent:
    """A normalized runtime event for tracing agent, model, and tool work."""

    id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    trace_id: str | None = None
    severity: EventSeverity = "info"
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class RuntimeTask:
    """In-memory task record aligned with Xpert handoff/run registry concepts."""

    id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = "pending"
    attempts: int = 0
    max_attempts: int = 3
    result: dict[str, Any] | None = None
    error: str | None = None
    trace_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    deadline_at: float | None = None

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass(slots=True)
class MiddlewareContext:
    """Shared runtime context passed through middleware hooks."""

    task_id: str | None = None
    trace_id: str | None = None
    capabilities: Any = None
    store: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelCallRequest:
    """Normalized model call request used by runtime middleware."""

    model_id: str
    messages: list[dict[str, Any]]
    params: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **updates: Any) -> "ModelCallRequest":
        return replace(self, **updates)


@dataclass(slots=True)
class ModelCallResponse:
    """Normalized model call response used by runtime middleware."""

    text: str
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **updates: Any) -> "ModelCallResponse":
        return replace(self, **updates)


@dataclass(slots=True)
class ToolCallRequest:
    """Normalized tool call request for MCP and future toolset providers."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **updates: Any) -> "ToolCallRequest":
        return replace(self, **updates)


@dataclass(slots=True)
class ToolCallResponse:
    """Normalized tool call response for runtime wrappers and audits."""

    output: str
    raw: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **updates: Any) -> "ToolCallResponse":
        return replace(self, **updates)
