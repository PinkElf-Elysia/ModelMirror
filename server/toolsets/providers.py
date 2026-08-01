from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx

try:
    from server.xpert_runtime.toolset import (
        RuntimeToolCall,
        RuntimeToolError,
        RuntimeToolResult,
    )
except ModuleNotFoundError:
    from xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError, RuntimeToolResult

from .credentials import CredentialStore
from .models import ToolDefinition
from .store import ToolsetValidationError


class BuiltinToolProviderRegistry:
    """Declarative builtin provider catalog with bounded execution."""

    def __init__(
        self,
        credentials: CredentialStore,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.credentials = credentials
        self._client = http_client
        self._runtime_delegates: dict[str, Any] = {}

    def register_runtime_delegate(self, provider_id: str, provider: Any) -> None:
        self._runtime_delegates[str(provider_id or "").strip().lower()] = provider

    def list_providers(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "tavily",
                "title": "Tavily",
                "description": "Search, extract, and crawl public web content.",
                "credential_required": True,
                "credential_kind": "provider_key",
                "instance_creatable": True,
                "tools": [
                    tool.model_dump(mode="json")
                    for tool in self.provider_tools("tavily")
                ],
            },
            {
                "id": "todos",
                "title": "Todos",
                "description": "Persistent private planning tools backed by RuntimeTodoStore.",
                "credential_required": False,
                "credential_kind": None,
                "instance_creatable": True,
                "runtime_binding": "todo_tools",
                "tools": [
                    tool.model_dump(mode="json")
                    for tool in self.provider_tools("todos")
                ],
            },
        ]

    def get_provider(self, provider_id: str) -> dict[str, Any]:
        clean = str(provider_id or "").strip().lower()
        provider = next(
            (item for item in self.list_providers() if item["id"] == clean),
            None,
        )
        if provider is None:
            raise ToolsetValidationError(f"Builtin Provider not found: {provider_id}")
        return provider

    def provider_tools(self, provider_id: str) -> list[ToolDefinition]:
        if provider_id == "tavily":
            return [
                ToolDefinition(
                    original_name="tavily_search",
                    description="Search the public web for relevant sources.",
                    input_schema={
                        "type": "object",
                        "required": ["query"],
                        "properties": {
                            "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                            "max_results": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 20,
                            },
                            "search_depth": {
                                "type": "string",
                                "enum": ["basic", "advanced"],
                            },
                            "include_answer": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    enabled=True,
                    order=0,
                    read_only=True,
                    memory_mode="run",
                    parallel_safe=True,
                    public_app_allowed=False,
                ),
                ToolDefinition(
                    original_name="tavily_extract",
                    description="Extract bounded content from public web URLs.",
                    input_schema={
                        "type": "object",
                        "required": ["urls"],
                        "properties": {
                            "urls": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 10,
                                "items": {
                                    "type": "string",
                                    "format": "uri",
                                    "maxLength": 2048,
                                },
                            },
                            "extract_depth": {
                                "type": "string",
                                "enum": ["basic", "advanced"],
                            },
                        },
                        "additionalProperties": False,
                    },
                    enabled=True,
                    order=1,
                    read_only=True,
                    memory_mode="run",
                    parallel_safe=True,
                    public_app_allowed=False,
                ),
                ToolDefinition(
                    original_name="tavily_crawl",
                    description="Crawl a public site from one root URL with bounded depth.",
                    input_schema={
                        "type": "object",
                        "required": ["url"],
                        "properties": {
                            "url": {
                                "type": "string",
                                "format": "uri",
                                "maxLength": 2048,
                            },
                            "max_depth": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "instructions": {
                                "type": "string",
                                "maxLength": 2000,
                            },
                        },
                        "additionalProperties": False,
                    },
                    enabled=True,
                    order=2,
                    read_only=True,
                    memory_mode="run",
                    parallel_safe=True,
                    public_app_allowed=False,
                ),
            ]
        if provider_id == "todos":
            return [
                ToolDefinition(
                    original_name="todo_list",
                    description="List Todos in the current private runtime scope.",
                    input_schema={"type": "object", "properties": {}},
                    enabled=True,
                    order=0,
                    read_only=True,
                    memory_mode="run",
                    parallel_safe=True,
                ),
                ToolDefinition(
                    original_name="todo_create",
                    description="Create a Todo in the current private runtime scope.",
                    input_schema={
                        "type": "object",
                        "required": ["title"],
                        "properties": {
                            "title": {"type": "string", "maxLength": 300},
                            "details": {"type": "string", "maxLength": 4000},
                            "priority": {
                                "type": "string",
                                "enum": ["low", "normal", "high"],
                            },
                        },
                    },
                    enabled=True,
                    order=1,
                    read_only=False,
                    requires_approval=False,
                    memory_mode="run",
                    parallel_safe=False,
                ),
                ToolDefinition(
                    original_name="todo_update",
                    description="Update one Todo in the current private runtime scope.",
                    input_schema={
                        "type": "object",
                        "required": ["todo_id", "revision"],
                        "properties": {
                            "todo_id": {"type": "string"},
                            "revision": {"type": "integer", "minimum": 1},
                            "title": {"type": "string", "maxLength": 300},
                            "details": {"type": "string", "maxLength": 4000},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "archived",
                                ],
                            },
                        },
                    },
                    enabled=True,
                    order=2,
                    read_only=False,
                    requires_approval=False,
                    memory_mode="run",
                    parallel_safe=False,
                ),
            ]
        raise ToolsetValidationError(f"Builtin Provider not found: {provider_id}")

    async def call(
        self,
        provider_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        credential_id: str,
        timeout_seconds: int,
        response_limit_bytes: int,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeToolResult:
        clean_provider_id = str(provider_id or "").strip().lower()
        delegate = self._runtime_delegates.get(clean_provider_id)
        if delegate is not None:
            matched = await delegate.find_tool(tool_name)
            if matched is None:
                raise RuntimeToolError(
                    tool_name,
                    "The builtin Runtime Provider no longer exposes this tool.",
                    code="toolset_schema_drift",
                )
            return await delegate.call_tool(
                RuntimeToolCall(
                    tool_name=tool_name,
                    arguments=dict(arguments),
                    metadata=dict(metadata or {}),
                )
            )
        if clean_provider_id != "tavily":
            raise RuntimeToolError(
                tool_name,
                "This builtin Provider is bound directly to an existing Runtime store.",
                code="provider_runtime_binding_required",
            )
        if tool_name not in {"tavily_search", "tavily_extract", "tavily_crawl"}:
            raise RuntimeToolError(
                tool_name,
                "Builtin Provider tool is not available.",
                code="tool_not_found",
            )
        if not credential_id:
            raise RuntimeToolError(
                tool_name,
                "Tavily credential is not configured.",
                code="credential_unavailable",
            )
        api_key = self.credentials.resolve(credential_id)
        endpoint = tool_name.removeprefix("tavily_")
        payload = deepcopy(arguments)
        if endpoint == "search":
            payload.setdefault("max_results", 5)
            payload.setdefault("search_depth", "basic")
            payload.setdefault("include_answer", False)
        elif endpoint == "extract":
            payload.setdefault("extract_depth", "basic")
        else:
            payload.setdefault("max_depth", 2)
            payload.setdefault("limit", 10)
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)),
            follow_redirects=False,
        )
        owns_client = self._client is None
        try:
            response = await client.post(
                f"https://api.tavily.com/{endpoint}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            raw = response.content
            if len(raw) > response_limit_bytes:
                raise RuntimeToolError(
                    tool_name,
                    "Tavily response exceeded the configured size limit.",
                    code="provider_response_too_large",
                )
            data = response.json()
            output = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if len(output) > 65536:
                output = output[:65536] + "\n...[truncated]"
            return RuntimeToolResult(
                output=output,
                metadata={
                    "provider_id": provider_id,
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "output_length": len(output),
                },
            )
        except RuntimeToolError:
            raise
        except httpx.HTTPStatusError as exc:
            raise RuntimeToolError(
                tool_name,
                f"Tavily request failed with HTTP {exc.response.status_code}.",
                code="provider_call_failed",
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeToolError(
                tool_name,
                "Tavily request failed before a valid response was received.",
                code="provider_call_failed",
            ) from exc
        except ValueError as exc:
            raise RuntimeToolError(
                tool_name,
                "Tavily returned an invalid JSON response.",
                code="provider_invalid_response",
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
