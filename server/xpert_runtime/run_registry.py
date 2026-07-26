from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

RuntimeRunType = Literal[
    "workflow",
    "xpert",
    "xpert_app",
    "goal",
    "workflow_agent",
    "agent_task",
    "agent_handoff",
    "chat",
    "knowledge_citation",
    "knowledge_pipeline",
    "knowledge_evaluation",
    "automation",
    "meta_planner",
]
RuntimeRunStatus = Literal[
    "pending",
    "running",
    "waiting",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(slots=True)
class RuntimeRun:
    """In-memory run record for Xpert-aligned workflow and agent execution."""

    run_id: str
    run_type: RuntimeRunType
    status: RuntimeRunStatus
    title: str
    source_id: str | None = None
    parent_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancelled_at: float | None = None
    error: str | None = None

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass(slots=True)
class RuntimeRunCheckpoint:
    """Small trace item attached to a RuntimeRun."""

    checkpoint_id: str
    run_id: str
    event_type: str
    title: str
    summary: str = ""
    severity: str = "info"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class RunRegistry:
    """Small in-memory RunRegistry for workflow, agent, task, and handoff runs."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._runs: dict[str, RuntimeRun] = {}
        self._checkpoints: dict[str, list[RuntimeRunCheckpoint]] = {}

    async def create_run(
        self,
        run_type: RuntimeRunType,
        title: str,
        *,
        status: RuntimeRunStatus = "pending",
        source_id: str | None = None,
        parent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeRun:
        run = RuntimeRun(
            run_id=str(uuid.uuid4()),
            run_type=run_type,
            status=status,
            title=title,
            source_id=source_id,
            parent_run_id=parent_run_id,
            metadata=dict(metadata or {}),
        )
        async with self._lock:
            self._runs[run.run_id] = run
            self._checkpoints[run.run_id] = []
        return run

    async def get_run(self, run_id: str) -> RuntimeRun | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def list_runs(
        self,
        *,
        run_type: RuntimeRunType | None = None,
        status: RuntimeRunStatus | None = None,
        parent_run_id: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
    ) -> list[RuntimeRun]:
        async with self._lock:
            runs = list(self._runs.values())
        if run_type is not None:
            runs = [run for run in runs if run.run_type == run_type]
        if status is not None:
            runs = [run for run in runs if run.status == status]
        if parent_run_id is not None:
            runs = [run for run in runs if run.parent_run_id == parent_run_id]
        if source_id is not None:
            runs = [run for run in runs if run.source_id == source_id]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        return runs[: max(1, limit)]

    async def record_checkpoint(
        self,
        run_id: str,
        *,
        event_type: str,
        title: str,
        summary: str = "",
        severity: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeRunCheckpoint:
        checkpoint = RuntimeRunCheckpoint(
            checkpoint_id=str(uuid.uuid4()),
            run_id=run_id,
            event_type=event_type,
            title=title,
            summary=summary[:500],
            severity=severity,
            metadata=dict(metadata or {}),
        )
        async with self._lock:
            if run_id not in self._runs:
                raise KeyError(f"Runtime run not found: {run_id}")
            self._checkpoints.setdefault(run_id, []).append(checkpoint)
        return checkpoint

    async def list_checkpoints(
        self,
        run_id: str,
        *,
        limit: int = 50,
    ) -> list[RuntimeRunCheckpoint]:
        async with self._lock:
            if run_id not in self._runs:
                raise KeyError(f"Runtime run not found: {run_id}")
            checkpoints = list(self._checkpoints.get(run_id, []))
        checkpoints.sort(key=lambda checkpoint: checkpoint.created_at, reverse=True)
        return checkpoints[: max(1, limit)]

    async def update_run(
        self,
        run_id: str,
        *,
        status: RuntimeRunStatus | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        clear_error: bool = False,
    ) -> RuntimeRun:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(f"Runtime run not found: {run_id}")
            if status is not None:
                run.status = status
                if status == "cancelled" and run.cancelled_at is None:
                    run.cancelled_at = time.time()
            if clear_error:
                run.error = None
            elif error is not None:
                run.error = error
            if metadata is not None:
                run.metadata.update(metadata)
            run.touch()
            if status == "failed":
                self._checkpoints.setdefault(run_id, []).append(
                    RuntimeRunCheckpoint(
                        checkpoint_id=str(uuid.uuid4()),
                        run_id=run_id,
                        event_type="run.failed",
                        title="Run failed",
                        summary=str(error or run.error or "")[:500],
                        severity="error",
                        metadata={"status": status},
                    )
                )
            elif status == "cancelled":
                self._checkpoints.setdefault(run_id, []).append(
                    RuntimeRunCheckpoint(
                        checkpoint_id=str(uuid.uuid4()),
                        run_id=run_id,
                        event_type="run.cancelled",
                        title="Run cancelled",
                        summary=str(error or run.error or "")[:500],
                        severity="warning",
                        metadata={"status": status},
                    )
                )
            return run

    async def cancel_run(
        self,
        run_id: str,
        *,
        reason: str = "cancelled",
    ) -> RuntimeRun:
        return await self.update_run(
            run_id,
            status="cancelled",
            error=reason,
            metadata={"cancel_reason": reason},
        )
