from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app, mcp_connect_windows, mcp_manager, tool_registry


MOCK_SERVER = Path(__file__).resolve().parent / "mock_mcp_server.py"


@pytest_asyncio.fixture(autouse=True)
async def cleanup_runtime_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_LEGACY_UNRESTRICTED_CONNECT_ENABLED", "true")
    old_ttl = mcp_manager.idle_timeout_seconds
    mcp_manager.idle_timeout_seconds = 15 * 60
    mcp_connect_windows.clear()
    await tool_registry.clear()
    yield
    mcp_manager.idle_timeout_seconds = old_ttl
    mcp_connect_windows.clear()
    await tool_registry.clear()
    await mcp_manager.close_all()


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


async def connect_mock(client: httpx.AsyncClient, label: str) -> str:
    response = await client.post(
        "/api/mcp/connect",
        json={"server_command": [sys.executable, str(MOCK_SERVER), label]},
    )
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


@pytest.mark.asyncio
async def test_sessions_and_registry_aggregate_multiple_servers(
    client: httpx.AsyncClient,
) -> None:
    first_session_id = await connect_mock(client, "alpha")
    second_session_id = await connect_mock(client, "beta")
    assert first_session_id != second_session_id

    sessions_response = await client.get("/api/mcp/sessions")
    assert sessions_response.status_code == 200, sessions_response.text
    sessions = sessions_response.json()["sessions"]
    assert len(sessions) == 2
    assert {session["session_id"] for session in sessions} == {
        first_session_id,
        second_session_id,
    }
    assert all(session["created_at"] > 0 for session in sessions)
    assert all(session["tools_count"] >= 3 for session in sessions)

    registry_response = await client.get("/api/registry/tools")
    assert registry_response.status_code == 200, registry_response.text
    tools = registry_response.json()["tools"]
    tool_names = [tool["name"] for tool in tools]

    assert tool_names.count("fetch") == 1
    assert tool_names.count("echo") == 1
    assert "marker_alpha" in tool_names
    assert "marker_beta" in tool_names
    assert {tool["session_id"] for tool in tools}.issubset(
        {first_session_id, second_session_id}
    )


@pytest.mark.asyncio
async def test_ttl_cleanup_removes_sessions_and_registry_tools(
    client: httpx.AsyncClient,
) -> None:
    mcp_manager.idle_timeout_seconds = 0.05
    await connect_mock(client, "short")

    before_cleanup = await client.get("/api/mcp/sessions")
    assert before_cleanup.status_code == 200
    assert len(before_cleanup.json()["sessions"]) == 1

    await asyncio.sleep(0.12)

    sessions_response = await client.get("/api/mcp/sessions")
    assert sessions_response.status_code == 200
    assert sessions_response.json()["sessions"] == []

    registry_response = await client.get("/api/registry/tools")
    assert registry_response.status_code == 200
    assert registry_response.json()["tools"] == []
