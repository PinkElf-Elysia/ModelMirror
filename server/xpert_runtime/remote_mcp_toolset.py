"""Contract-only Runtime adapter for Hub and authenticated Catalog MCP tools."""

from __future__ import annotations

from typing import Any

try:
    from server.mcp.hub import HubError
except ModuleNotFoundError:
    from mcp.hub import HubError

from .hub_toolset import HubMCPToolsetProvider
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


class RemoteMCPToolsetProvider:
    """Preserve Hub behavior while adding reviewed Catalog remote contracts."""

    def __init__(self, service: Any, hub_provider: HubMCPToolsetProvider) -> None:
        self.service = service
        self.hub_provider = hub_provider

    async def list_tools(self) -> list[RuntimeTool]:
        hub_tools = await self.hub_provider.list_tools()
        catalog_tools = [
            RuntimeTool(
                name=str(item["name"]),
                description=str(item.get("description") or ""),
                input_schema=dict(item.get("input_schema") or {}),
                provider="mcp_remote",
                server_id=f"remote:catalog_project:{item['project_id']}",
                metadata={
                    "remote_target_type": "catalog_project",
                    "remote_target_id": item["project_id"],
                    "remote_upstream_tool_name": item["upstream_tool_name"],
                    "remote_tool_schema_digest": item["tool_schema_digest"],
                    "remote_schema_digest": item["schema_digest"],
                    "remote_origin": item["origin"],
                    "remote_version": item["version"],
                    "remote_source_digest": item["source_digest"],
                    "remote_auth_context_digest": item["auth_context_digest"],
                    "remote_contract_id": item["contract_id"],
                    "remote_contract_fingerprint": item["contract_fingerprint"],
                    "retry_on_failure": False,
                },
                read_only=False,
                requires_approval=True,
                sensitive=True,
                parallel_safe=False,
                public_app_allowed=False,
            )
            for item in self.service.catalog_runtime_tools()
        ]
        seen = {item.name for item in hub_tools}
        return [*hub_tools, *(item for item in catalog_tools if item.name not in seen)]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next(
            (item for item in await self.list_tools() if item.name == tool_name),
            None,
        )

    async def find_tool_exact(
        self, *, server_id: str, tool_name: str
    ) -> RuntimeTool | None:
        return next(
            (
                item
                for item in await self.list_tools()
                if item.name == tool_name and item.server_id == server_id
            ),
            None,
        )

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        tool = await self.find_tool(call.tool_name)
        if tool is None:
            raise RuntimeToolError(
                call.tool_name,
                "Reviewed remote MCP tool not found",
                code="tool_not_found",
            )
        if tool.provider == "mcp_hub":
            return await self.hub_provider.call_tool(call)
        resolved = call.metadata.get("resolved_approval")
        if not isinstance(resolved, dict):
            raise RuntimeToolError(
                call.tool_name,
                "Catalog remote MCP tool requires a decided approval.",
                code="mcp_remote_runtime_approval_required",
            )
        try:
            result = await self.service.execute_catalog_runtime(
                project_id=str(tool.metadata["remote_target_id"]),
                runtime_tool_name=tool.name,
                upstream_tool_name=str(
                    tool.metadata["remote_upstream_tool_name"]
                ),
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
                "provider": "mcp_remote",
                "target_type": "catalog_project",
                "target_id": tool.metadata["remote_target_id"],
                "origin": tool.metadata["remote_origin"],
                "retry_on_failure": False,
                "untrusted_external_content": True,
            },
            is_error=bool(result.get("isError") or result.get("is_error")),
        )


__all__ = ["RemoteMCPToolsetProvider"]
