from __future__ import annotations

import json
from typing import Any

from .capabilities import CapabilityRegistry
from .todo_store import RuntimeTodoStore
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


class TodoToolsetProvider:
    """Scope-bound runtime Todo tools for workflow agents."""

    def __init__(self, store: RuntimeTodoStore) -> None:
        self.store = store

    async def list_tools(self) -> list[RuntimeTool]:
        return [
            RuntimeTool(
                name="todo_list",
                description="List Todo items for the current agent execution scope.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                        }
                    },
                    "additionalProperties": False,
                },
                provider="todo",
                read_only=True,
                memory_mode="run",
                parallel_safe=True,
            ),
            RuntimeTool(
                name="todo_create",
                description="Create one Todo item in the current agent execution scope.",
                input_schema={
                    "type": "object",
                    "required": ["title"],
                    "properties": {
                        "title": {"type": "string", "maxLength": 500},
                        "details": {"type": "string", "maxLength": 10000},
                        "priority": {"type": "integer", "minimum": -10, "maximum": 10},
                    },
                    "additionalProperties": False,
                },
                provider="todo",
                read_only=False,
                memory_mode="run",
                parallel_safe=False,
            ),
            RuntimeTool(
                name="todo_update",
                description="Update status or content of a Todo item in the current scope.",
                input_schema={
                    "type": "object",
                    "required": ["todo_id", "revision"],
                    "properties": {
                        "todo_id": {"type": "string"},
                        "revision": {"type": "integer", "minimum": 1},
                        "title": {"type": "string", "maxLength": 500},
                        "details": {"type": "string", "maxLength": 10000},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"],
                        },
                        "priority": {"type": "integer", "minimum": -10, "maximum": 10},
                        "order": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                provider="todo",
                read_only=False,
                memory_mode="run",
                parallel_safe=False,
            ),
        ]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((tool for tool in await self.list_tools() if tool.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        scope_type, scope_id = _scope(call.metadata)
        arguments = dict(call.arguments or {})
        try:
            if call.tool_name == "todo_list":
                status = str(arguments.get("status") or "").strip() or None
                items = self.store.list_items(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    status=status,
                    limit=100,
                )
                payload = [self.store.serialize(item) for item in items]
            elif call.tool_name == "todo_create":
                max_items = max(
                    1,
                    min(int(call.metadata.get("todo_max_items") or 50), 100),
                )
                active_items = self.store.list_items(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    limit=500,
                )
                if len(
                    [item for item in active_items if item.status != "archived"]
                ) >= max_items:
                    raise RuntimeToolError(
                        call.tool_name,
                        f"Todo scope reached its {max_items} item limit.",
                        code="todo_limit_reached",
                    )
                item = self.store.create_item(
                    scope_type=scope_type,
                    scope_id=scope_id,
                    title=arguments.get("title"),
                    details=arguments.get("details") or "",
                    priority=int(arguments.get("priority") or 0),
                    source_run_id=call.metadata.get("run_id"),
                    created_by="agent",
                )
                payload = self.store.serialize(item)
            elif call.tool_name == "todo_update":
                todo_id = str(arguments.pop("todo_id", "")).strip()
                revision = int(arguments.pop("revision", 0))
                patch = {
                    key: value
                    for key, value in arguments.items()
                    if key in {"title", "details", "status", "priority", "order"}
                }
                item = self.store.update_item(
                    todo_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    revision=revision,
                    patch=patch,
                )
                payload = self.store.serialize(item)
            else:
                raise RuntimeToolError(
                    call.tool_name,
                    "Todo tool not found.",
                    code="tool_not_found",
                )
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                str(exc)[:300],
                code="todo_error",
            ) from exc
        output = json.dumps(payload, ensure_ascii=False)
        return RuntimeToolResult(
            output=output,
            content=[{"type": "text", "text": output}],
            metadata={"content_types": ["text"], "scope_type": scope_type},
        )


def register_todo_toolset_capability(
    registry: CapabilityRegistry,
    provider: TodoToolsetProvider,
) -> None:
    registry.register(
        "todo_tools",
        provider,
        description="Persistent scope-bound Todo planning tools.",
        metadata={"provider": "todo"},
    )


def _scope(metadata: dict[str, Any]) -> tuple[str, str]:
    scope_type = str(metadata.get("todo_scope_type") or "").strip()
    scope_id = str(metadata.get("todo_scope_id") or "").strip()
    if not scope_type or not scope_id:
        raise RuntimeToolError(
            "todo",
            "Todo scope is unavailable for this run.",
            code="todo_scope_missing",
        )
    return scope_type, scope_id
