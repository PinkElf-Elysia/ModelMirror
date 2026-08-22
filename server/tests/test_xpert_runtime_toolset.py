from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.xpert_runtime import (
    CatalogMCPToolsetProvider,
    CapabilityRegistry,
    MCPToolsetProvider,
    RuntimeTool,
    RuntimeToolCall,
    RuntimeToolError,
    RuntimeToolResult,
    register_mcp_toolset_capability,
)


def registered_fetch_tool() -> dict:
    return {
        "name": "fetch",
        "description": "Fetch URL content",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
        },
        "server_id": "fetch-server",
        "session_id": "session-1",
        "registered_at": 1234567890.0,
    }


@pytest.mark.asyncio
async def test_mcp_toolset_lists_registered_tools() -> None:
    tool_registry = SimpleNamespace(
        list_tools=AsyncMock(return_value=[registered_fetch_tool()])
    )
    mcp_manager = SimpleNamespace()
    provider = MCPToolsetProvider(tool_registry, mcp_manager)

    tools = await provider.list_tools()

    assert len(tools) == 1
    assert isinstance(tools[0], RuntimeTool)
    assert tools[0].name == "fetch"
    assert tools[0].description == "Fetch URL content"
    assert tools[0].provider == "mcp"
    assert tools[0].session_id == "session-1"
    assert tools[0].server_id == "fetch-server"
    assert tools[0].input_schema["properties"]["url"]["type"] == "string"
    assert tools[0].metadata["registered_at"] == 1234567890.0


@pytest.mark.asyncio
async def test_mcp_toolset_calls_tool_by_session() -> None:
    tool_registry = SimpleNamespace(
        list_tools=AsyncMock(return_value=[registered_fetch_tool()])
    )
    mcp_manager = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                content=[{"type": "text", "text": "Hello from MCP"}]
            )
        )
    )
    provider = MCPToolsetProvider(tool_registry, mcp_manager)

    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="fetch",
            arguments={"url": "https://example.com"},
        )
    )

    assert isinstance(result, RuntimeToolResult)
    assert result.output == "Hello from MCP"
    assert result.is_error is False
    assert len(result.content) == 1
    assert result.content[0]["type"] == "text"
    assert result.metadata["session_id"] == "session-1"
    assert result.metadata["content_types"] == ["text"]
    mcp_manager.call_tool.assert_awaited_once_with(
        "session-1",
        "fetch",
        {"url": "https://example.com"},
    )


@pytest.mark.asyncio
async def test_mcp_toolset_missing_tool_raises_error() -> None:
    tool_registry = SimpleNamespace(list_tools=AsyncMock(return_value=[]))
    mcp_manager = SimpleNamespace()
    provider = MCPToolsetProvider(tool_registry, mcp_manager)

    with pytest.raises(RuntimeToolError) as exc_info:
        await provider.call_tool(
            RuntimeToolCall(tool_name="nonexistent", arguments={})
        )

    assert exc_info.value.code == "tool_not_found"
    assert exc_info.value.tool_name == "nonexistent"


@pytest.mark.asyncio
async def test_mcp_toolset_preserves_non_text_content_metadata() -> None:
    tool_registry = SimpleNamespace(
        list_tools=AsyncMock(return_value=[registered_fetch_tool()])
    )
    mcp_manager = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                content=[
                    {"type": "text", "text": "Image generated"},
                    {"type": "image", "data": "base64...", "mimeType": "image/png"},
                    {"type": "resource", "resource": {"text": "doc.txt"}},
                ]
            )
        )
    )
    provider = MCPToolsetProvider(tool_registry, mcp_manager)

    result = await provider.call_tool(
        RuntimeToolCall(tool_name="fetch", arguments={"url": "https://example.com"})
    )

    assert result.output == "Image generated"
    assert len(result.content) == 3
    assert set(result.metadata["content_types"]) == {"text", "image", "resource"}
    assert result.is_error is False


def test_register_mcp_toolset_capability() -> None:
    registry = CapabilityRegistry()
    provider = MCPToolsetProvider(
        SimpleNamespace(list_tools=AsyncMock(return_value=[])),
        SimpleNamespace(),
    )

    register_mcp_toolset_capability(registry, provider)

    capability = registry.require("mcp_tools")
    assert capability.implementation is provider
    assert capability.metadata["provider"] == "mcp"


@pytest.mark.asyncio
async def test_catalog_toolset_exposes_only_connected_chat_safe_tools() -> None:
    service = SimpleNamespace(
        list_adapters=lambda: {
            "adapters": [
                {
                    "project_id": "time-mcp",
                    "connected": True,
                    "executable": True,
                    "tool_policies": {
                        "get_current_time": {
                            "read_only": True,
                            "requires_approval": False,
                            "sensitive": False,
                            "terminal": False,
                            "effect": "read",
                        },
                        "write_time": {
                            "read_only": False,
                            "requires_approval": True,
                            "sensitive": False,
                            "terminal": False,
                            "effect": "state-write",
                        },
                    },
                },
                {
                    "project_id": "offline-mcp",
                    "connected": False,
                    "executable": True,
                    "tool_policies": {},
                },
            ]
        },
        list_tools=AsyncMock(
            return_value={
                "tools": [
                    {
                        "name": "get_current_time",
                        "description": "Current time",
                        "inputSchema": {"type": "object"},
                    },
                    {"name": "write_time", "inputSchema": {"type": "object"}},
                ]
            }
        ),
    )

    tools = await CatalogMCPToolsetProvider(service).list_tools()

    assert [tool.name for tool in tools] == ["get_current_time"]
    assert tools[0].provider == "mcp_catalog"
    assert tools[0].server_id == "catalog:time-mcp"
    assert tools[0].read_only is True
    assert tools[0].requires_approval is False
    service.list_tools.assert_awaited_once_with("time-mcp")


@pytest.mark.asyncio
async def test_catalog_toolset_calls_through_catalog_policy_service() -> None:
    policy = {
        "read_only": True,
        "requires_approval": False,
        "sensitive": False,
        "terminal": False,
        "effect": "read",
    }
    service = SimpleNamespace(
        list_adapters=lambda: {
            "adapters": [
                {
                    "project_id": "time-mcp",
                    "connected": True,
                    "executable": True,
                    "tool_policies": {"get_current_time": policy},
                }
            ]
        },
        list_tools=AsyncMock(
            return_value={
                "tools": [
                    {
                        "name": "get_current_time",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        ),
        call_tool=AsyncMock(
            return_value={
                "content": [{"type": "text", "text": "12:00 UTC"}],
                "is_error": False,
            }
        ),
    )
    provider = CatalogMCPToolsetProvider(service)

    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="get_current_time",
            arguments={"timezone": "UTC"},
        )
    )

    assert result.output == "12:00 UTC"
    assert result.metadata["catalog_project_id"] == "time-mcp"
    assert result.metadata["content_types"] == ["text"]
    service.call_tool.assert_awaited_once_with(
        "time-mcp", "get_current_time", {"timezone": "UTC"}
    )
