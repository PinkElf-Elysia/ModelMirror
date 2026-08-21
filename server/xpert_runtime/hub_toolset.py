"""Approval-only Runtime adapter for active MCP Hub candidates."""

from __future__ import annotations

from typing import Any

try:
    from server.mcp.hub import HubError, MCPHubService
except ModuleNotFoundError:
    from mcp.hub import HubError, MCPHubService

from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


class HubMCPToolsetProvider:
    def __init__(self, service: MCPHubService) -> None:
        self.service = service

    async def list_tools(self) -> list[RuntimeTool]:
        return [
            RuntimeTool(
                name=str(item["name"]),
                description=str(item.get("description") or ""),
                input_schema=dict(item.get("input_schema") or {}),
                provider="mcp_hub",
                server_id=str(item["candidate_id"]),
                metadata={
                    "hub_candidate_id": item["candidate_id"],
                    "hub_upstream_tool_name": item["upstream_tool_name"],
                    "hub_tool_schema_digest": item["tool_schema_digest"],
                    "hub_schema_digest": item["schema_digest"],
                    "hub_origin": item["origin"],
                    "hub_server_name": item["server_name"],
                    "hub_version": item["version"],
                    "retry_on_failure": False,
                },
                read_only=False,
                requires_approval=True,
                sensitive=True,
                parallel_safe=False,
                public_app_allowed=False,
            )
            for item in self.service.runtime_tools()
        ]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((item for item in await self.list_tools() if item.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        tool = await self.find_tool(call.tool_name)
        if tool is None:
            raise RuntimeToolError(call.tool_name, "MCP Hub tool not found", code="tool_not_found")
        resolved = call.metadata.get("resolved_approval")
        if not isinstance(resolved, dict):
            raise RuntimeToolError(
                call.tool_name,
                "MCP Hub tool requires a decided approval.",
                code="hub_approval_required",
            )
        try:
            result = await self.service.execute(
                candidate_id=str(tool.metadata["hub_candidate_id"]),
                runtime_tool_name=tool.name,
                upstream_tool_name=str(tool.metadata["hub_upstream_tool_name"]),
                arguments=dict(call.arguments),
                approval=resolved,
            )
        except HubError as exc:
            raise RuntimeToolError(call.tool_name, str(exc), code=exc.code) from exc
        content = result.get("content")
        content_items = list(content) if isinstance(content, list) else []
        output = "\n\n".join(
            str(item.get("text") or "")
            for item in content_items
            if isinstance(item, dict) and item.get("type") == "text"
        )
        return RuntimeToolResult(
            output=output,
            content=[dict(item) for item in content_items if isinstance(item, dict)],
            metadata={
                "provider": "mcp_hub",
                "candidate_id": tool.metadata["hub_candidate_id"],
                "origin": tool.metadata["hub_origin"],
                "retry_on_failure": False,
                "untrusted_external_content": True,
            },
            is_error=bool(result.get("isError") or result.get("is_error")),
        )
