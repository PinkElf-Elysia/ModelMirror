from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.mcp.manager import MCPClientManager, MCPTransportUnavailableError
from server.main import (
    app,
    mcp_catalog_service,
    mcp_connect_windows,
    mcp_manager,
    tool_registry,
)


MOCK_SERVER = Path(__file__).resolve().parent / "mock_mcp_server.py"


@pytest_asyncio.fixture(autouse=True)
async def cleanup_sessions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_LEGACY_UNRESTRICTED_CONNECT_ENABLED", "true")
    mcp_connect_windows.clear()
    yield
    mcp_connect_windows.clear()
    await mcp_manager.close_all()


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_connect_list_call_and_disconnect(client: httpx.AsyncClient) -> None:
    connect_response = await client.post(
        "/api/mcp/connect",
        json={"server_command": [sys.executable, str(MOCK_SERVER)]},
    )
    assert connect_response.status_code == 200, connect_response.text
    session_id = connect_response.json()["session_id"]
    assert connect_response.json()["tools_count"] >= 1

    tools_response = await client.get(f"/api/mcp/{session_id}/tools")
    assert tools_response.status_code == 200, tools_response.text
    tool_names = {tool["name"] for tool in tools_response.json()["tools"]}
    assert "fetch" in tool_names

    call_response = await client.post(
        f"/api/mcp/{session_id}/call",
        json={
            "tool_name": "fetch",
            "arguments": {"url": "https://example.com"},
        },
    )
    assert call_response.status_code == 200, call_response.text
    text = "\n".join(
        item.get("text", "") for item in call_response.json()["content"]
    )
    assert "Example Domain" in text

    delete_response = await client.delete(f"/api/mcp/{session_id}")
    assert delete_response.status_code == 200

    missing_response = await client.get(f"/api/mcp/{session_id}/tools")
    assert missing_response.status_code == 404


@pytest.mark.asyncio
async def test_startup_failure_returns_400(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/mcp/connect",
        json={"server_command": ["definitely-not-a-real-mcp-command"]},
    )
    assert response.status_code == 400
    assert "MCP Server 启动失败" in response.text


@pytest.mark.asyncio
async def test_stdio_early_exit_is_normalized_as_transport_unavailable(
    tmp_path: Path,
) -> None:
    manager = MCPClientManager(sandbox_root=tmp_path, operation_timeout=5)

    with pytest.raises(MCPTransportUnavailableError) as failed:
        await manager.connect_profile(
            transport="stdio",
            server_command=[sys.executable, "-c", "raise SystemExit(69)"],
            reconnect_attempts=0,
        )

    assert failed.value.code == "mcp_transport_start_failed"
    assert "ExceptionGroup" not in str(failed.value)


@pytest.mark.asyncio
async def test_legacy_unrestricted_connect_is_disabled_by_default(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_LEGACY_UNRESTRICTED_CONNECT_ENABLED", "false")
    response = await client.post(
        "/api/mcp/connect",
        json={"server_command": [sys.executable, str(MOCK_SERVER)]},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "legacy_mcp_connect_disabled"


@pytest.mark.asyncio
async def test_legacy_unrestricted_install_is_disabled_by_default(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_LEGACY_UNRESTRICTED_CONNECT_ENABLED", "false")
    response = await client.post(
        "/api/mcp/install",
        json={
            "project_id": "legacy-example",
            "install_command": "npx --yes legacy-example",
            "server_command": ["npx", "--yes", "legacy-example"],
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "legacy_mcp_install_disabled"


@pytest.mark.asyncio
async def test_short_lived_stdio_environment_can_be_scrubbed() -> None:
    marker = "must-not-remain-in-managed-session"
    session_id = await mcp_manager.connect_profile(
        transport="stdio",
        server_command=[sys.executable, str(MOCK_SERVER)],
        environment={"MODELMIRROR_TEST_MARKER": marker},
        reconnect_attempts=0,
    )
    await mcp_manager.scrub_session_environment(session_id)

    assert mcp_manager._sessions[session_id].environment == {}
    assert marker not in json.dumps(await mcp_manager.get_sessions_summary())


@pytest.mark.asyncio
async def test_catalog_session_rejects_generic_call_and_disconnect(
    client: httpx.AsyncClient,
) -> None:
    session_id = await mcp_manager.connect([sys.executable, str(MOCK_SERVER)])
    scope_key = mcp_catalog_service._scope_key("context7")
    mcp_catalog_service._sessions[scope_key] = session_id
    try:
        call = await client.post(
            f"/api/mcp/{session_id}/call",
            json={"tool_name": "fetch", "arguments": {}},
        )
        assert call.status_code == 403
        disconnected = await client.delete(f"/api/mcp/{session_id}")
        assert disconnected.status_code == 403
        assert session_id in mcp_manager._sessions
    finally:
        mcp_catalog_service._sessions.pop(scope_key, None)
        await mcp_manager.disconnect(session_id)
        await tool_registry.unregister_session(session_id)


@pytest.mark.asyncio
async def test_rejects_shell_metacharacters(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/mcp/connect",
        json={"server_command": ["npx", "-y", "bad;command"]},
    )
    assert response.status_code == 400
    assert "shell" in response.text


@pytest.mark.asyncio
async def test_connection_rate_limit(client: httpx.AsyncClient) -> None:
    for _ in range(5):
        response = await client.post(
            "/api/mcp/connect",
            json={"server_command": ["npx", "-y", "bad;command"]},
        )
        assert response.status_code == 400

    limited = await client.post(
        "/api/mcp/connect",
        json={"server_command": ["npx", "-y", "bad;command"]},
    )
    assert limited.status_code == 429
