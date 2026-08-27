"""Policy-preserving Runtime adapter for connected MCP Catalog tools."""

from __future__ import annotations

from typing import Any

from .toolset import (
    RuntimeTool,
    RuntimeToolCall,
    RuntimeToolError,
    RuntimeToolResult,
)


class CatalogMCPToolsetProvider:
    """Expose only connected, read-only Catalog tools without approval gates."""

    def __init__(self, service: Any) -> None:
        self.service = service

    async def list_tools(self) -> list[RuntimeTool]:
        payload = self.service.list_adapters()
        adapters = payload.get("adapters") if isinstance(payload, dict) else []
        tools: list[RuntimeTool] = []
        seen: set[str] = set()
        for adapter in adapters if isinstance(adapters, list) else []:
            if not isinstance(adapter, dict):
                continue
            if (
                adapter.get("connection_kind") == "remote-mcp"
                or adapter.get("remote_review_capable") is True
            ):
                # Authenticated remote MCPs are executable only through a
                # published reviewed contract and the approval-only provider.
                continue
            if not adapter.get("connected") or not adapter.get("executable"):
                continue
            project_id = str(adapter.get("project_id") or "").strip()
            policies = adapter.get("tool_policies")
            if not project_id or not isinstance(policies, dict):
                continue
            try:
                discovered = await self.service.list_tools(project_id)
            except Exception:
                # A stale catalog session must not disable other connected tools.
                continue
            raw_tools = (
                discovered.get("tools") if isinstance(discovered, dict) else []
            )
            for raw_tool in raw_tools if isinstance(raw_tools, list) else []:
                if not isinstance(raw_tool, dict):
                    continue
                name = str(raw_tool.get("name") or "").strip()
                policy = policies.get(name)
                if not name or name in seen or not _chat_safe_policy(policy):
                    continue
                seen.add(name)
                tools.append(
                    RuntimeTool(
                        name=name,
                        description=str(raw_tool.get("description") or ""),
                        input_schema=_dict(
                            raw_tool.get("inputSchema")
                            or raw_tool.get("input_schema")
                        ),
                        provider="mcp_catalog",
                        server_id=f"catalog:{project_id}",
                        metadata={
                            "catalog_project_id": project_id,
                            "catalog_tool_name": name,
                            "retry_on_failure": True,
                        },
                        read_only=True,
                        requires_approval=False,
                        sensitive=False,
                        terminal=False,
                        parallel_safe=False,
                        public_app_allowed=False,
                    )
                )
        return tools

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next(
            (tool for tool in await self.list_tools() if tool.name == tool_name),
            None,
        )

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        tool = await self.find_tool(call.tool_name)
        if tool is None:
            raise RuntimeToolError(
                call.tool_name,
                "Catalog tool not found or is not safe for Chat Runtime",
                code="tool_not_found",
            )
        project_id = str(tool.metadata.get("catalog_project_id") or "")
        try:
            payload = await self.service.call_tool(
                project_id,
                call.tool_name,
                dict(call.arguments),
            )
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                str(exc) or exc.__class__.__name__,
                code="catalog_tool_call_error",
            ) from exc
        content = [
            dict(item)
            for item in payload.get("content", [])
            if isinstance(item, dict)
        ] if isinstance(payload, dict) else []
        output = "\n\n".join(
            str(item.get("text") or "")
            for item in content
            if item.get("type") == "text" and item.get("text")
        )
        return RuntimeToolResult(
            output=output,
            content=content,
            metadata={
                "provider": "mcp_catalog",
                "catalog_project_id": project_id,
                "content_types": sorted(
                    {str(item.get("type") or "unknown") for item in content}
                ),
                "retry_on_failure": True,
            },
            is_error=bool(
                isinstance(payload, dict)
                and (payload.get("is_error") or payload.get("isError"))
            ),
        )


def _chat_safe_policy(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("read_only") is True
        and value.get("requires_approval") is not True
        and value.get("sensitive") is not True
        and value.get("terminal") is not True
        and value.get("effect", "read") == "read"
    )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


__all__ = ["CatalogMCPToolsetProvider"]
