from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from server.mcp.manager import (
    MCPClientManager,
    MCPSessionNotFoundError,
    ManagedMCPSession,
)


@pytest.mark.asyncio
async def test_call_tool_can_disable_restart_and_resend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = MCPClientManager(sandbox_root=tmp_path)
    managed = manager._new_managed_session(["python"], session_id="session-write")
    manager._sessions[managed.session_id] = managed
    sends: list[str] = []
    restarts: list[Exception] = []

    async def fail_after_send(
        current: ManagedMCPSession,
        operation: str,
        **_: Any,
    ) -> CallToolResult:
        assert current is managed
        sends.append(operation)
        raise RuntimeError("response lost after provider boundary")

    async def restart(
        current: ManagedMCPSession,
        error: Exception,
    ) -> ManagedMCPSession:
        restarts.append(error)
        return current

    monkeypatch.setattr(manager, "_send_command", fail_after_send)
    monkeypatch.setattr(manager, "_restart_once", restart)

    with pytest.raises(RuntimeError, match="response lost"):
        await manager.call_tool(
            managed.session_id,
            "create_record",
            {"value": "one-shot"},
            retry_on_failure=False,
        )

    assert sends == ["call_tool"]
    assert restarts == []


@pytest.mark.asyncio
async def test_call_tool_keeps_existing_read_retry_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = MCPClientManager(sandbox_root=tmp_path)
    managed = manager._new_managed_session(["python"], session_id="session-read")
    manager._sessions[managed.session_id] = managed
    sends = 0
    restarts = 0

    async def fail_then_succeed(
        current: ManagedMCPSession,
        operation: str,
        **_: Any,
    ) -> CallToolResult:
        nonlocal sends
        assert current is managed
        assert operation == "call_tool"
        sends += 1
        if sends == 1:
            raise RuntimeError("transient read failure")
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            isError=False,
        )

    async def restart(
        current: ManagedMCPSession,
        error: Exception,
    ) -> ManagedMCPSession:
        nonlocal restarts
        assert current is managed
        assert "transient read failure" in str(error)
        restarts += 1
        return current

    monkeypatch.setattr(manager, "_send_command", fail_then_succeed)
    monkeypatch.setattr(manager, "_restart_once", restart)

    result = await manager.call_tool(
        managed.session_id,
        "list_records",
        {},
    )

    assert result.isError is False
    assert sends == 2
    assert restarts == 1


@pytest.mark.asyncio
async def test_owned_catalog_session_is_hidden_from_generic_manager_calls(
    tmp_path: Path,
) -> None:
    manager = MCPClientManager(sandbox_root=tmp_path)
    owner = "catalog:local:local:airtable-mcp"
    managed = manager._new_managed_session(
        ["python"],
        session_id="session-owned",
        session_owner=owner,
    )
    manager._sessions[managed.session_id] = managed

    assert await manager.get_sessions_summary() == []
    owned = await manager.get_sessions_summary(session_owner=owner)
    assert [item["session_id"] for item in owned] == [managed.session_id]

    with pytest.raises(MCPSessionNotFoundError, match="session not found"):
        await manager.list_tools(managed.session_id)
    with pytest.raises(MCPSessionNotFoundError, match="session not found"):
        await manager.call_tool(managed.session_id, "create_record", {})
    with pytest.raises(MCPSessionNotFoundError, match="session not found"):
        await manager.scrub_session_environment(managed.session_id)
    with pytest.raises(MCPSessionNotFoundError, match="session not found"):
        await manager.disconnect(managed.session_id)

    assert managed.session_id in manager._sessions
    await manager.disconnect(managed.session_id, session_owner=owner)
    assert managed.session_id not in manager._sessions
