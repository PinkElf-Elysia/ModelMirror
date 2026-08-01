from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from server.main import render_workflow_template
from server.xpert_runtime import (
    MCPToolsetProvider,
    RuntimeToolCall,
    RuntimeToolResult,
)


async def _call_runtime_mcp_tool(
    provider: MCPToolsetProvider,
    *,
    tool_name: str,
    arguments_json: str,
    variables: dict[str, str],
) -> RuntimeToolResult:
    matched_tool = await provider.find_tool(tool_name)
    if not matched_tool:
        raise ValueError(f"MCP 工具未注册：{tool_name}")

    raw_arguments = render_workflow_template(arguments_json, variables)
    arguments = json.loads(raw_arguments)
    if not isinstance(arguments, dict):
        raise ValueError("MCP 工具参数必须是 JSON 对象。")

    return await provider.call_tool(
        RuntimeToolCall(tool_name=tool_name, arguments=arguments)
    )


def _provider_with_call_result(result: RuntimeToolResult) -> MCPToolsetProvider:
    tool_registry = SimpleNamespace(
        list_tools=AsyncMock(
            return_value=[
                {
                    "name": "fetch",
                    "description": "Fetch URL content",
                    "input_schema": {"type": "object"},
                    "server_id": "fetch-server",
                    "session_id": "session-1",
                    "registered_at": 1234567890.0,
                }
            ]
        )
    )
    mcp_manager = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(content=result.content))
    )
    return MCPToolsetProvider(tool_registry, mcp_manager)


@pytest.mark.asyncio
async def test_mcp_tool_calls_provider_with_correct_arguments() -> None:
    provider = _provider_with_call_result(
        RuntimeToolResult(
            output="hello",
            content=[{"type": "text", "text": "hello"}],
            metadata={"content_types": ["text"]},
        )
    )

    output = await _call_runtime_mcp_tool(
        provider,
        tool_name="fetch",
        arguments_json='{"url": "{{url}}"}',
        variables={"url": "https://example.com"},
    )

    assert output.output == "hello"
    provider.mcp_manager.call_tool.assert_awaited_once_with(
        "session-1",
        "fetch",
        {"url": "https://example.com"},
    )


@pytest.mark.asyncio
async def test_mcp_tool_text_output_writes_to_output_variable() -> None:
    provider = _provider_with_call_result(
        RuntimeToolResult(
            output="result text",
            content=[{"type": "text", "text": "result text"}],
            metadata={"content_types": ["text"]},
        )
    )
    variables: dict[str, str] = {}

    result = await _call_runtime_mcp_tool(
        provider,
        tool_name="fetch",
        arguments_json="{}",
        variables=variables,
    )
    variables["mcp_output"] = result.output.strip()

    assert variables["mcp_output"] == "result text"
    assert isinstance(variables["mcp_output"], str)


@pytest.mark.asyncio
async def test_mcp_tool_non_text_content_does_not_crash() -> None:
    provider = _provider_with_call_result(
        RuntimeToolResult(
            output="",
            content=[
                {"type": "image", "data": "..."},
                {"type": "resource", "uri": "file://doc.txt"},
            ],
            metadata={"content_types": ["image", "resource"]},
        )
    )

    result = await _call_runtime_mcp_tool(
        provider,
        tool_name="fetch",
        arguments_json="{}",
        variables={},
    )
    non_text_types = [
        content_type
        for content_type in result.metadata.get("content_types", [])
        if content_type != "text"
    ]

    assert result.output == ""
    assert set(non_text_types) == {"image", "resource"}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_mcp_tool_not_found_degrades_gracefully() -> None:
    provider = MCPToolsetProvider(
        SimpleNamespace(list_tools=AsyncMock(return_value=[])),
        SimpleNamespace(call_tool=AsyncMock()),
    )
    variables: dict[str, str] = {}

    with pytest.raises(ValueError):
        await _call_runtime_mcp_tool(
            provider,
            tool_name="missing",
            arguments_json="{}",
            variables=variables,
        )

    variables["mcp_output"] = ""
    assert variables["mcp_output"] == ""


def test_mcp_tool_arguments_template_and_json_parse() -> None:
    assert json.loads(render_workflow_template("{}", {})) == {}

    parsed = json.loads(
        render_workflow_template(
            '{"a": "{{x}}", "b": "{{y}}"}',
            {"x": "1", "y": "2"},
        )
    )
    assert parsed == {"a": "1", "b": "2"}

    with pytest.raises(json.JSONDecodeError):
        json.loads(render_workflow_template("{invalid}", {}))
