from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from .toolset import RuntimeToolError

logger = logging.getLogger(__name__)

ToolAuditStatus = Literal["started", "succeeded", "failed", "denied"]


@dataclass(slots=True)
class ToolAuditRecord:
    """Structured runtime audit metadata for a single tool call."""

    record_id: str
    tool_name: str
    status: ToolAuditStatus
    started_at: float
    finished_at: float | None = None
    duration_ms: float | None = None
    output_length: int | None = None
    content_types: list[str] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolPermissionPolicy:
    """Small allow/deny policy for runtime tool calls."""

    def __init__(
        self,
        *,
        allowed_tools: set[str] | None = None,
        denied_tools: set[str] | None = None,
        allow_by_default: bool = True,
    ) -> None:
        self.allowed_tools = set(allowed_tools or set())
        self.denied_tools = set(denied_tools or set())
        self.allow_by_default = allow_by_default

    async def check(self, tool_name: str) -> None:
        if not self.is_allowed(tool_name):
            raise RuntimeToolError(
                tool_name,
                "Tool is denied by policy",
                code="tool_denied",
            )

    def is_allowed(self, tool_name: str) -> bool:
        if tool_name in self.denied_tools:
            return False
        if self.allowed_tools:
            return tool_name in self.allowed_tools
        return self.allow_by_default


class InMemoryToolAuditStore:
    """Bounded in-memory audit store for runtime tool calls."""

    def __init__(self, max_records: int = 10000) -> None:
        self._lock = asyncio.Lock()
        self._records: list[ToolAuditRecord] = []
        self._max_records = max(1, max_records)

    async def record_started(
        self,
        tool_name: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        record_id = uuid.uuid4().hex
        record = ToolAuditRecord(
            record_id=record_id,
            tool_name=tool_name,
            status="started",
            started_at=time.time(),
            metadata=dict(metadata or {}),
        )
        async with self._lock:
            self._records.append(record)
            self._trim_locked()
        return record_id

    async def record_finished(
        self,
        record_id: str,
        *,
        output_length: int,
        content_types: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            record = self._find_locked(record_id)
            if record is None:
                logger.warning("Tool audit record not found: %s", record_id)
                return
            now = time.time()
            record.status = "succeeded"
            record.finished_at = now
            record.duration_ms = max(0.0, (now - record.started_at) * 1000)
            record.output_length = output_length
            record.content_types = list(content_types or [])
            record.metadata.update(metadata or {})

    async def record_failed(
        self,
        record_id: str,
        *,
        error: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            record = self._find_locked(record_id)
            if record is None:
                logger.warning("Tool audit record not found: %s", record_id)
                return
            now = time.time()
            record.status = "failed"
            record.finished_at = now
            record.duration_ms = max(0.0, (now - record.started_at) * 1000)
            record.error = error[:200]
            record.metadata.update(metadata or {})

    async def record_denied(
        self,
        tool_name: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        now = time.time()
        record_id = uuid.uuid4().hex
        record = ToolAuditRecord(
            record_id=record_id,
            tool_name=tool_name,
            status="denied",
            started_at=now,
            finished_at=now,
            duration_ms=0.0,
            error="Tool is denied by policy",
            metadata=dict(metadata or {}),
        )
        async with self._lock:
            self._records.append(record)
            self._trim_locked()
        return record_id

    async def list_records(
        self,
        *,
        tool_name: str | None = None,
        status: ToolAuditStatus | None = None,
    ) -> list[ToolAuditRecord]:
        async with self._lock:
            records = list(self._records)
        if tool_name is not None:
            records = [record for record in records if record.tool_name == tool_name]
        if status is not None:
            records = [record for record in records if record.status == status]
        return records

    def _find_locked(self, record_id: str) -> ToolAuditRecord | None:
        for record in self._records:
            if record.record_id == record_id:
                return record
        return None

    def _trim_locked(self) -> None:
        overflow = len(self._records) - self._max_records
        if overflow > 0:
            del self._records[:overflow]
