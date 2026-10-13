"""Validate durable queue identity without repairing or dispatching a snapshot."""
from __future__ import annotations

from typing import Any


class WorkflowContinuationError(ValueError):
    pass


def validate_resume_queue(continuation: Any, *, executable_node_ids: set[str]) -> None:
    if not isinstance(continuation, dict):
        raise WorkflowContinuationError("恢复快照必须为对象，不能重新推断执行队列。")
    queue = continuation.get("queue")
    if not isinstance(queue, list) or not queue:
        raise WorkflowContinuationError("恢复快照缺少非空执行队列，拒绝空执行或重新派发。")
    collections = {"queue": queue}
    for key in ("queued", "executed"):
        if key in continuation:
            collections[key] = continuation[key]
    for key, values in collections.items():
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or value not in executable_node_ids for value in values)
            or len(values) != len(set(values))
        ):
            raise WorkflowContinuationError(f"恢复快照的 {key} 必须为无重复的可执行节点标识列表。")
    if set(queue).intersection(continuation.get("executed", [])):
        raise WorkflowContinuationError("恢复队列包含已完成节点，拒绝重复执行。")
