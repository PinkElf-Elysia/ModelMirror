"""Deterministic stream outcome classification; no dispatch or Store mutation."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal

WAIT_EVENTS = {
    "runtime_approval_pending": ("approval", "approval_id"),
    "client_tool_waiting": ("client_tool", "request_id"),
    "timer_waiting": ("timer", "wait_id"),
    "node_retry_scheduled": ("node_retry", "wait_id"),
    "agent_handoff_waiting": ("agent_handoff", "wait_id"),
}
OUTCOME_EVENTS = frozenset(WAIT_EVENTS) | {"workflow_end", "workflow_cancelled"}


class WorkflowOutcomeError(RuntimeError):
    pass


class WorkflowOutcomeAccumulator:
    def __init__(self) -> None:
        self._event: dict[str, Any] | None = None

    def observe(self, event: Any) -> None:
        if not isinstance(event, dict):
            raise WorkflowOutcomeError("工作流事件必须为对象。")
        if event.get("event") not in OUTCOME_EVENTS:
            return
        if self._event is not None and self._event != event:
            raise WorkflowOutcomeError("工作流返回相互冲突的结束或等待结果。")
        self._event = copy.deepcopy(event)

    def finish(self) -> dict[str, Any]:
        if self._event is None:
            raise WorkflowOutcomeError("工作流结束但未提供明确结果。")
        return copy.deepcopy(self._event)


@dataclass(frozen=True)
class WorkflowOutcome:
    status: Literal["completed", "waiting", "cancelled"]
    task_id: str
    run_id: str
    wait_kind: str | None = None
    wait_id: str | None = None
    resume_at: float | None = None


def resolve_workflow_outcome(event: dict[str, Any], snapshot: Any) -> WorkflowOutcome:
    """Require a matching durable decision, never infer completion from absence."""
    name = event.get("event")
    if name not in OUTCOME_EVENTS:
        raise WorkflowOutcomeError("工作流结果类型不受支持。")
    if (
        event.get("task_id") != snapshot.task_id
        or event.get("run_id") != snapshot.run_id
        or snapshot.runtime_metadata.get("terminal_source_invalidated") is True
    ):
        raise WorkflowOutcomeError("工作流结果与持久执行身份不一致或来源已失效。")
    status = "waiting" if name in WAIT_EVENTS else (
        "completed" if name == "workflow_end" else "cancelled"
    )
    if snapshot.status != status:
        raise WorkflowOutcomeError("工作流结果与持久执行状态不一致。")
    if status == "waiting":
        kind, key = WAIT_EVENTS[name]
        wait_id = event.get(key)
        if (
            not isinstance(wait_id, str) or not wait_id
            or snapshot.wait_kind != kind or snapshot.wait_id != wait_id
            or not snapshot.continuation
        ):
            raise WorkflowOutcomeError("工作流等待结果与持久登记不一致。")
        return WorkflowOutcome(status, snapshot.task_id, snapshot.run_id,
                               kind, wait_id, snapshot.resume_at)
    return WorkflowOutcome(status, snapshot.task_id, snapshot.run_id)
