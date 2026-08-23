"""In-memory MCP tool registry.

The registry aggregates tools exposed by all active MCP sessions. It keeps the
raw per-session mapping and exposes one current record for each stable
``(server_id, tool_name)`` identity.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from mcp.types import Tool


@dataclass(slots=True)
class RegisteredTool:
    """Serializable metadata for one MCP tool."""

    name: str
    description: str | None
    input_schema: dict[str, Any]
    server_id: str
    session_id: str
    registered_at: float
    registration_sequence: int

    @property
    def schema_checksum(self) -> str:
        encoded = json.dumps(
            self.input_schema,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ToolRegistry:
    """Thread-safe in-memory registry for active MCP tools."""

    def __init__(self) -> None:
        self._tools_by_session: dict[str, list[RegisteredTool]] = {}
        self._snapshot: tuple[RegisteredTool, ...] = ()
        self._lock = asyncio.Lock()
        self._registration_sequence = 0

    async def register_session_tools(
        self,
        *,
        session_id: str,
        server_id: str,
        tools: list[Tool],
    ) -> None:
        """Replace the tools registered for one session."""

        async with self._lock:
            self._registration_sequence += 1
            registered_at = time.time()
            records = [
                RegisteredTool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.inputSchema,
                    server_id=server_id,
                    session_id=session_id,
                    registered_at=registered_at,
                    registration_sequence=self._registration_sequence,
                )
                for tool in tools
            ]
            self._tools_by_session[session_id] = records
            self._refresh_snapshot_unlocked()

    async def unregister_session(self, session_id: str) -> None:
        """Remove all tools belonging to a session."""

        async with self._lock:
            self._tools_by_session.pop(session_id, None)
            self._refresh_snapshot_unlocked()

    async def unregister_sessions(self, session_ids: list[str]) -> None:
        """Remove all tools belonging to a list of sessions."""

        async with self._lock:
            for session_id in session_ids:
                self._tools_by_session.pop(session_id, None)
            self._refresh_snapshot_unlocked()

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return one current tool per stable ``(server_id, name)`` identity."""

        async with self._lock:
            snapshot = self._snapshot
        return [self._payload(record) for record in snapshot]

    async def find_tool(self, *, server_id: str, name: str) -> dict[str, Any] | None:
        for item in await self.list_tools():
            if item["server_id"] == server_id and item["name"] == name:
                return item
        return None

    def snapshot_tools(self) -> list[dict[str, Any]]:
        """Return a non-awaiting metadata snapshot for synchronous publish guards."""

        return [self._payload(record) for record in self._snapshot]

    def _refresh_snapshot_unlocked(self) -> None:
        current: dict[tuple[str, str], RegisteredTool] = {}
        for records in self._tools_by_session.values():
            for record in records:
                key = (record.server_id, record.name)
                existing = current.get(key)
                if (
                    existing is None
                    or record.registration_sequence >= existing.registration_sequence
                ):
                    current[key] = record
        self._snapshot = tuple(
            record
            for _, record in sorted(
                current.items(),
                key=lambda item: (item[0][0].casefold(), item[0][1].casefold()),
            )
        )

    @staticmethod
    def _payload(record: RegisteredTool) -> dict[str, Any]:
        return {
            "name": record.name,
            "description": record.description,
            "input_schema": dict(record.input_schema),
            "schema_checksum": record.schema_checksum,
            "server_id": record.server_id,
            "session_id": record.session_id,
            "registered_at": record.registered_at,
        }

    async def clear(self) -> None:
        """Clear all registered tools."""

        async with self._lock:
            self._tools_by_session.clear()
            self._refresh_snapshot_unlocked()
