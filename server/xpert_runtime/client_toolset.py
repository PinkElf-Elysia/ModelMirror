from __future__ import annotations

import hashlib
import json
from typing import Any

from .capabilities import CapabilityRegistry
from .client_tool_store import (
    MUTATING_CLIENT_TOOLS,
    ClientToolRequest,
    ClientToolStore,
)
from .interrupts import RuntimeInterrupt
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


CLIENT_TOOL_NAMES = {
    "host_page_snapshot",
    "host_page_read",
    "host_page_click",
    "host_page_fill",
    "host_page_select",
    "host_page_press",
    "host_page_hover",
    "host_page_scroll",
    "host_page_wait_for",
    "host_page_navigate",
    "host_page_screenshot",
}


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> RuntimeTool:
    schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return RuntimeTool(
        name=name,
        description=description,
        input_schema=schema,
        provider="client",
        server_id="modelmirror-client-host",
    )


CLIENT_TOOLS = [
    _tool("host_page_snapshot", "Read an ARIA snapshot from the bound Chrome tab.", {}),
    _tool("host_page_read", "Read bounded untrusted text from the bound Chrome tab.", {}),
    _tool(
        "host_page_click",
        "Click an element from the latest snapshot by opaque ref.",
        {"ref": {"type": "string"}},
        ["ref"],
    ),
    _tool(
        "host_page_fill",
        "Fill a non-sensitive form field by opaque ref.",
        {"ref": {"type": "string"}, "value": {"type": "string", "maxLength": 10000}},
        ["ref", "value"],
    ),
    _tool(
        "host_page_select",
        "Select an option in a non-sensitive field by opaque ref.",
        {"ref": {"type": "string"}, "value": {"type": "string", "maxLength": 1000}},
        ["ref", "value"],
    ),
    _tool(
        "host_page_press",
        "Press an allowed keyboard key on an opaque ref.",
        {"ref": {"type": "string"}, "key": {"type": "string", "maxLength": 40}},
        ["ref", "key"],
    ),
    _tool(
        "host_page_hover",
        "Hover an element from the latest snapshot.",
        {"ref": {"type": "string"}},
        ["ref"],
    ),
    _tool(
        "host_page_scroll",
        "Scroll the bound page by a bounded amount.",
        {"delta_y": {"type": "integer", "minimum": -5000, "maximum": 5000}},
    ),
    _tool(
        "host_page_wait_for",
        "Wait for bounded milliseconds before reading the page again.",
        {"milliseconds": {"type": "integer", "minimum": 100, "maximum": 10000}},
    ),
    _tool(
        "host_page_navigate",
        "Navigate the bound tab within its currently authorized origin.",
        {"url": {"type": "string", "maxLength": 2048}},
        ["url"],
    ),
    _tool("host_page_screenshot", "Capture a bounded screenshot artifact.", {}),
]

CLIENT_TOOL_BY_NAME = {tool.name: tool for tool in CLIENT_TOOLS}


def client_tool_schema_hash(tool: RuntimeTool) -> str:
    raw = json.dumps(tool.input_schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ClientToolsetProvider:
    """Dispatches runtime tools to a paired client host through durable waits."""

    def __init__(
        self,
        store: ClientToolStore,
        *,
        tools: list[RuntimeTool] | None = None,
        capability_name: str = "client_tools",
        config_key: str = "client_tools_config",
        host_type: str = "chrome",
    ) -> None:
        self.store = store
        self.tools = list(tools or CLIENT_TOOLS)
        self.tool_by_name = {tool.name: tool for tool in self.tools}
        self.tool_names = set(self.tool_by_name)
        self.capability_name = capability_name
        self.config_key = config_key
        self.host_type = host_type

    async def list_tools(self) -> list[RuntimeTool]:
        return list(self.tools)

    async def list_tools_for_host(
        self,
        host_id: str,
        configured_names: set[str],
        *,
        require_bound_tab: bool,
    ) -> list[RuntimeTool]:
        try:
            host = self.store.require_host(host_id)
        except Exception:
            return []
        if host.revoked:
            return []
        if host.host_type != self.host_type:
            return []
        binding = host.document_binding if self.host_type == "office" else host.bound_tab
        if require_bound_tab and not binding.get("bound"):
            return []
        supported = {
            str(item.get("name") or "")
            for item in host.capabilities
            if isinstance(item, dict)
        }
        names = configured_names or self.tool_names
        result: list[RuntimeTool] = []
        for name in sorted(names):
            tool = self.tool_by_name.get(name)
            if tool is None or name not in supported:
                continue
            expected = client_tool_schema_hash(tool)
            if host.schema_hashes.get(name) != expected:
                continue
            result.append(tool)
        return result

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return self.tool_by_name.get(tool_name)

    async def prepare_dispatch(self, call: RuntimeToolCall) -> None:
        request = self._request_for_call(call)
        if request.status in {
            "completed",
            "failed",
            "cancelled",
            "expired",
            "uncertain",
        }:
            return
        raise RuntimeInterrupt(
            task_id=request.task_id,
            run_id=request.run_id,
            wait_kind="client_tool",
            wait_id=request.request_id,
        )

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        request = self._request_for_call(call)
        if request.status == "completed":
            return RuntimeToolResult(
                output=request.result,
                metadata={
                    "content_types": ["text"],
                    "client_host_id": request.host_id,
                    "client_request_id": request.request_id,
                    "operation_id": request.operation_id,
                    "artifact_id": request.result_metadata.get("artifact_id"),
                    "result_length": len(request.result),
                    **dict(request.result_metadata),
                },
            )
        if request.status in {"failed", "cancelled", "expired", "uncertain"}:
            return RuntimeToolResult(
                output=json.dumps(
                    {
                        "status": request.status,
                        "error": request.error or "Client tool did not complete.",
                        "request_id": request.request_id,
                    },
                    ensure_ascii=False,
                ),
                metadata={
                    "content_types": ["text"],
                    "client_host_id": request.host_id,
                    "client_request_id": request.request_id,
                    "operation_id": request.operation_id,
                    "is_error": True,
                },
                is_error=True,
            )
        raise RuntimeInterrupt(
            task_id=request.task_id,
            run_id=request.run_id,
            wait_kind="client_tool",
            wait_id=request.request_id,
        )

    def _request_for_call(self, call: RuntimeToolCall) -> ClientToolRequest:
        if call.tool_name not in self.tool_names:
            raise RuntimeToolError(
                call.tool_name,
                "Unsupported client tool.",
                code="client_tool_not_found",
            )
        metadata = dict(call.metadata or {})
        config = (
            dict(metadata.get(self.config_key) or {})
            if isinstance(metadata.get(self.config_key), dict)
            else {}
        )
        host_id = str(config.get("clientHostId") or "").strip()
        task_id = str(metadata.get("task_id") or "").strip()
        run_id = str(metadata.get("run_id") or task_id).strip()
        node_id = str(metadata.get("node_id") or "agent").strip()
        if not host_id or not task_id or not run_id:
            raise RuntimeToolError(
                call.tool_name,
                "Client tool requires a configured host and durable runtime IDs.",
                code="client_tool_context_missing",
            )
        tool = self.tool_by_name[call.tool_name]
        schema_hash = client_tool_schema_hash(tool)
        try:
            host = self.store.require_host(host_id)
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                "Configured client host is unavailable.",
                code="client_host_unavailable",
            ) from exc
        if host.host_type != self.host_type:
            raise RuntimeToolError(
                call.tool_name,
                f"Configured host is not a {self.host_type} client host.",
                code="client_host_type_mismatch",
            )
        if host.schema_hashes.get(call.tool_name) != schema_hash:
            raise RuntimeToolError(
                call.tool_name,
                "Client host schema version does not match the server.",
                code="client_tool_schema_mismatch",
            )
        require_binding = bool(
            config.get(
                "requireBoundDocument" if self.host_type == "office" else "requireBoundTab",
                True,
            )
        )
        binding = host.document_binding if self.host_type == "office" else host.bound_tab
        if require_binding and not binding.get("bound"):
            raise RuntimeToolError(
                call.tool_name,
                "The client host has no user-authorized bound document or tab.",
                code="client_binding_missing",
            )
        scope_type, scope_id = self._scope(metadata, node_id)
        operation_id = self._operation_id(call, task_id, node_id)
        timeout = self._bounded_timeout(
            config.get(
                "timeoutSeconds" if self.host_type == "office" else "clientToolTimeoutSeconds",
                1800,
            )
        )
        return self.store.create_request(
            operation_id=operation_id,
            tool_call_id=str(metadata.get("tool_call_id") or operation_id),
            host_id=host_id,
            task_id=task_id,
            run_id=run_id,
            node_id=node_id,
            scope_type=scope_type,
            scope_id=scope_id,
            tool_name=call.tool_name,
            arguments=dict(call.arguments or {}),
            schema_hash=schema_hash,
            timeout_seconds=timeout,
        )

    @staticmethod
    def _scope(metadata: dict[str, Any], node_id: str) -> tuple[str, str]:
        if metadata.get("conversation_id"):
            return "conversation", f"{metadata.get('xpert_id') or 'xpert'}:{metadata.get('conversation_id')}"
        if metadata.get("goal_id"):
            return "goal", f"{metadata.get('goal_id')}:{metadata.get('goal_step_id') or node_id}"
        if metadata.get("handoff_id"):
            return "handoff", str(metadata.get("handoff_id"))
        return "workflow", f"{metadata.get('task_id') or metadata.get('run_id')}:{node_id}"

    @staticmethod
    def _operation_id(call: RuntimeToolCall, task_id: str, node_id: str) -> str:
        metadata = dict(call.metadata or {})
        raw = json.dumps(
            {
                "task_id": task_id,
                "node_id": node_id,
                "iteration": metadata.get("iteration"),
                "tool_name": call.tool_name,
                "arguments": call.arguments or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"clientop_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"

    @staticmethod
    def _bounded_timeout(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 1800
        return max(30, min(parsed, 86400))


def register_client_toolset_capability(
    registry: CapabilityRegistry,
    provider: ClientToolsetProvider,
) -> None:
    registry.register(
        "client_tools",
        provider,
        description="Paired Chrome host tools for private Xpert runtimes.",
    )
