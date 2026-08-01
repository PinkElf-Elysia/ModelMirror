from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .capabilities import CapabilityRegistry


@dataclass(slots=True)
class RuntimeTool:
    """Normalized tool metadata exposed by runtime toolset providers."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    provider: str = "mcp"
    session_id: str | None = None
    server_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    requires_approval: bool = False
    sensitive: bool = False
    terminal: bool = False
    memory_mode: str = "off"
    parallel_safe: bool = False
    public_app_allowed: bool = False


@dataclass(slots=True)
class RuntimeToolCall:
    """Normalized runtime tool call payload."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeToolResult:
    """Normalized runtime tool call result."""

    output: str
    content: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False


class RuntimeToolError(RuntimeError):
    """Structured runtime tool error for provider callers."""

    def __init__(
        self,
        tool_name: str,
        message: str,
        *,
        code: str = "tool_call_error",
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.message = message
        self.code = code


class ToolsetProvider(Protocol):
    """Protocol implemented by runtime tool sources."""

    async def list_tools(self) -> list[RuntimeTool]:
        ...

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        ...

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        ...


class MCPToolsetProvider:
    """Runtime toolset provider backed by ModelMirror MCP sessions."""

    def __init__(self, tool_registry: Any, mcp_manager: Any) -> None:
        self.tool_registry = tool_registry
        self.mcp_manager = mcp_manager

    async def list_tools(self) -> list[RuntimeTool]:
        records = await self.tool_registry.list_tools()
        tools: list[RuntimeTool] = []
        for record in records:
            if not isinstance(record, dict):
                record = _content_to_dict(record)
            tools.append(
                RuntimeTool(
                    name=str(record.get("name") or ""),
                    description=str(record.get("description") or ""),
                    input_schema=_ensure_dict(record.get("input_schema")),
                    provider="mcp",
                    session_id=_optional_str(record.get("session_id")),
                    server_id=_optional_str(record.get("server_id")),
                    metadata={"registered_at": record.get("registered_at")},
                    read_only=True,
                    parallel_safe=False,
                    public_app_allowed=False,
                )
            )
        return tools

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        for tool in await self.list_tools():
            if tool.name == tool_name:
                return tool
        return None

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        tool = await self.find_tool(call.tool_name)
        if tool is None:
            raise RuntimeToolError(
                call.tool_name,
                "Tool not found",
                code="tool_not_found",
            )
        if not tool.session_id:
            raise RuntimeToolError(
                call.tool_name,
                "Tool session is missing",
                code="session_not_found",
            )

        try:
            result = await self.mcp_manager.call_tool(
                tool.session_id,
                call.tool_name,
                call.arguments,
            )
        except Exception as exc:
            raise _wrap_tool_error(call.tool_name, exc) from exc

        content_items = list(getattr(result, "content", []) or [])
        content = [_content_to_dict(item) for item in content_items]
        output_parts = [
            text
            for text in (_extract_text(item) for item in content_items)
            if text is not None
        ]
        content_types = sorted(
            {
                str(item.get("type") or "unknown")
                for item in content
            }
        )

        return RuntimeToolResult(
            output="\n\n".join(output_parts),
            content=content,
            metadata={
                "content_types": content_types,
                "session_id": tool.session_id,
                "server_id": tool.server_id,
            },
            is_error=False,
        )


def register_mcp_toolset_capability(
    capability_registry: CapabilityRegistry,
    provider: MCPToolsetProvider,
    *,
    capability_name: str = "mcp_tools",
) -> None:
    """Register MCP tools as a runtime capability."""

    capability_registry.register(
        capability_name,
        provider,
        description="MCP tools exposed through the Xpert-aligned runtime toolset.",
        metadata={"provider": "mcp"},
    )


def _wrap_tool_error(tool_name: str, exc: Exception) -> RuntimeToolError:
    class_name = exc.__class__.__name__
    message = str(exc) or class_name
    if class_name == "MCPSessionNotFoundError":
        return RuntimeToolError(tool_name, message, code="session_not_found")
    if isinstance(exc, ValueError):
        return RuntimeToolError(tool_name, message, code="invalid_argument")
    return RuntimeToolError(tool_name, message, code="tool_call_error")


def _content_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        data = dict(item)
    elif hasattr(item, "model_dump"):
        try:
            data = dict(item.model_dump())
        except Exception:
            data = {}
    elif hasattr(item, "__dict__"):
        data = dict(vars(item))
    else:
        data = {"raw": str(item)}

    item_type = data.get("type") or getattr(item, "type", None) or "unknown"
    data["type"] = str(item_type)
    return data


def _extract_text(item: Any) -> str | None:
    data = _content_to_dict(item)
    if data.get("type") != "text":
        return None
    text = data.get("text")
    if isinstance(text, str):
        return text
    content = data.get("content")
    if isinstance(content, str):
        return content
    if hasattr(item, "text"):
        value = getattr(item, "text")
        if isinstance(value, str):
            return value
    return str(item)


def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
