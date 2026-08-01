from __future__ import annotations

import json
from typing import Any

from .capabilities import CapabilityRegistry
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


class MemoryToolsetProvider:
    """Runtime tools backed by an injected Xpert context store."""

    def __init__(self, context_store: Any) -> None:
        self.context_store = context_store

    async def list_tools(self) -> list[RuntimeTool]:
        return [
            RuntimeTool(
                name="memory_search",
                description="Search active Xpert or conversation memories.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["conversation", "xpert", "both"],
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                },
                provider="memory",
                read_only=True,
                memory_mode="run",
                parallel_safe=True,
            ),
            RuntimeTool(
                name="memory_get",
                description="Read one active memory by its stable ID.",
                input_schema={
                    "type": "object",
                    "properties": {"memory_id": {"type": "string"}},
                    "required": ["memory_id"],
                },
                provider="memory",
                read_only=True,
                memory_mode="run",
                parallel_safe=True,
            ),
            RuntimeTool(
                name="memory_propose_write",
                description="Create a memory candidate that requires human approval.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["conversation", "xpert"],
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "type": {
                            "type": "string",
                            "enum": ["user", "feedback", "project", "reference"],
                        },
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "action": {"type": "string", "enum": ["create", "update"]},
                        "target_memory_id": {"type": "string"},
                        "base_revision": {"type": "integer", "minimum": 1},
                        "source_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["content", "scope"],
                },
                provider="memory",
                read_only=False,
                memory_mode="run",
                parallel_safe=False,
            ),
        ]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((tool for tool in await self.list_tools() if tool.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        xpert_id = str(call.metadata.get("xpert_id") or "").strip()
        conversation_id = str(call.metadata.get("conversation_id") or "").strip() or None
        if not xpert_id:
            raise RuntimeToolError(
                call.tool_name,
                "Memory tools require an Xpert runtime context.",
                code="memory_context_missing",
            )
        try:
            if call.tool_name == "memory_search":
                query = str(call.arguments.get("query") or "").strip()
                scope = str(call.arguments.get("scope") or "both").strip()
                limit = max(1, min(int(call.arguments.get("limit") or 5), 10))
                records = self.context_store.search_memories(
                    xpert_id,
                    query,
                    scope=scope,
                    conversation_id=conversation_id,
                    limit=limit,
                )
                output = json.dumps(
                    [
                        {
                            "memory_id": item.memory_id,
                            "scope": item.scope,
                            "type": item.memory_type,
                            "title": item.title,
                            "summary": item.summary,
                            "content": item.content[:2_000],
                            "tags": item.tags,
                            "canonical_ref": item.canonical_ref,
                            "revision": item.revision,
                            "truncated": len(item.content) > 2_000,
                        }
                        for item in records
                    ],
                    ensure_ascii=False,
                )
                return RuntimeToolResult(
                    output=output,
                    metadata={"content_types": ["text"], "memory_count": len(records)},
                )
            if call.tool_name == "memory_get":
                memory_id = str(call.arguments.get("memory_id") or "").strip()
                record = self.context_store.get_memory(
                    xpert_id,
                    memory_id,
                    record_detail_read=True,
                    conversation_id=conversation_id,
                )
                if record.status != "active":
                    raise ValueError("Memory is archived.")
                return RuntimeToolResult(
                    output=json.dumps(
                        {
                            "memory_id": record.memory_id,
                            "scope": record.scope,
                            "type": record.memory_type,
                            "title": record.title,
                            "summary": record.summary,
                            "content": record.content[:8_000],
                            "tags": record.tags,
                            "canonical_ref": record.canonical_ref,
                            "revision": record.revision,
                            "truncated": len(record.content) > 8_000,
                        },
                        ensure_ascii=False,
                    ),
                    metadata={"content_types": ["text"]},
                )
            if call.tool_name == "memory_propose_write":
                content = str(call.arguments.get("content") or "").strip()
                scope = str(call.arguments.get("scope") or "xpert").strip()
                tags_raw = call.arguments.get("tags")
                tags = [str(item) for item in tags_raw] if isinstance(tags_raw, list) else []
                source_refs_raw = call.arguments.get("source_refs")
                source_refs = (
                    [str(item) for item in source_refs_raw]
                    if isinstance(source_refs_raw, list)
                    else []
                )
                candidate = self.context_store.create_candidate(
                    xpert_id,
                    content=content,
                    scope=scope,
                    conversation_id=conversation_id,
                    tags=tags,
                    source_run_id=str(call.metadata.get("run_id") or "") or None,
                    action=str(call.arguments.get("action") or "create"),
                    memory_type=str(call.arguments.get("type") or "project"),
                    title=str(call.arguments.get("title") or ""),
                    summary=str(call.arguments.get("summary") or ""),
                    target_memory_id=(
                        str(call.arguments.get("target_memory_id"))
                        if call.arguments.get("target_memory_id")
                        else None
                    ),
                    base_revision=(
                        int(call.arguments.get("base_revision"))
                        if call.arguments.get("base_revision") is not None
                        else None
                    ),
                    source_refs=source_refs,
                    confidence=(
                        float(call.arguments.get("confidence"))
                        if call.arguments.get("confidence") is not None
                        else None
                    ),
                )
                return RuntimeToolResult(
                    output=json.dumps(
                        {
                            "candidate_id": candidate.candidate_id,
                            "status": candidate.status,
                            "type": candidate.memory_type,
                            "action": candidate.action,
                        },
                        ensure_ascii=False,
                    ),
                    metadata={
                        "content_types": ["text"],
                        "candidate_id": candidate.candidate_id,
                    },
                )
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                str(exc),
                code="memory_tool_error",
            ) from exc
        raise RuntimeToolError(call.tool_name, "Memory tool not found.", code="tool_not_found")


def register_memory_toolset_capability(
    capability_registry: CapabilityRegistry,
    provider: MemoryToolsetProvider,
) -> None:
    capability_registry.register(
        "memory_tools",
        provider,
        description="Persistent Xpert conversation and long-term memory tools.",
        metadata={"provider": "memory"},
    )
