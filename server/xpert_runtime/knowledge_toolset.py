from __future__ import annotations

import json
from typing import Any

from .capabilities import CapabilityRegistry
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


READ_TOOL_NAMES = {"knowledge_search", "knowledge_get", "knowledge_cite"}
WRITE_TOOL_NAME = "knowledge_propose_write"


class KnowledgeToolsetProvider:
    """Runtime knowledge tools constrained to workflow-configured knowledge bases."""

    def __init__(self, rag_service: Any) -> None:
        self.rag_service = rag_service

    @property
    def service(self) -> Any:
        return self.rag_service() if callable(self.rag_service) else self.rag_service

    async def list_tools(self) -> list[RuntimeTool]:
        kb_property = {
            "type": "string",
            "description": "An allowed knowledge base ID. Optional for search when all configured bases should be queried.",
        }
        return [
            RuntimeTool(
                name="knowledge_search",
                description="Search the active versions of the configured knowledge bases.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "kb_id": kb_property,
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                },
                provider="knowledge",
                read_only=True,
                memory_mode="run",
                parallel_safe=True,
            ),
            RuntimeTool(
                name="knowledge_get",
                description="Read one bounded chunk from an active knowledge version.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "kb_id": kb_property,
                        "chunk_id": {"type": "string"},
                    },
                    "required": ["kb_id", "chunk_id"],
                },
                provider="knowledge",
                read_only=True,
                memory_mode="run",
                parallel_safe=True,
            ),
            RuntimeTool(
                name="knowledge_cite",
                description="Build a stable citation anchor for one active knowledge chunk.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "kb_id": kb_property,
                        "chunk_id": {"type": "string"},
                    },
                    "required": ["kb_id", "chunk_id"],
                },
                provider="knowledge",
                read_only=True,
                memory_mode="run",
                parallel_safe=True,
            ),
            RuntimeTool(
                name=WRITE_TOOL_NAME,
                description="Propose a knowledge update for human approval; never mutates an active index.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "kb_id": kb_property,
                        "title": {"type": "string", "maxLength": 160},
                        "content": {"type": "string", "maxLength": 20000},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["kb_id", "title", "content"],
                },
                provider="knowledge",
                read_only=False,
                memory_mode="run",
                parallel_safe=False,
            ),
        ]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((tool for tool in await self.list_tools() if tool.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        allowed_kb_ids = self._allowed_knowledge_bases(call)
        read_enabled = bool(call.metadata.get("knowledge_read_enabled"))
        write_enabled = bool(call.metadata.get("knowledge_write_enabled"))
        if call.tool_name in READ_TOOL_NAMES and not read_enabled:
            raise RuntimeToolError(
                call.tool_name,
                "Knowledge read tools are disabled for this workflow agent.",
                code="knowledge_read_disabled",
            )
        if call.tool_name == WRITE_TOOL_NAME and not write_enabled:
            raise RuntimeToolError(
                call.tool_name,
                "Knowledge write proposals are disabled for this workflow agent.",
                code="knowledge_write_disabled",
            )
        try:
            if call.tool_name == "knowledge_search":
                return await self._search(call, allowed_kb_ids)
            if call.tool_name == "knowledge_get":
                kb_id = self._required_allowed_kb(call, allowed_kb_ids)
                chunk_id = str(call.arguments.get("chunk_id") or "").strip()
                if not chunk_id:
                    raise ValueError("chunk_id is required.")
                item = self.service.get_knowledge_chunk(
                    kb_id,
                    chunk_id,
                    version_id=self._fixed_version(call, kb_id),
                )
                return RuntimeToolResult(
                    output=json.dumps(item, ensure_ascii=False),
                    metadata={
                        "content_types": ["text"],
                        "kb_id": kb_id,
                        "chunk_id": chunk_id,
                        "output_length": len(str(item.get("text") or "")),
                    },
                )
            if call.tool_name == "knowledge_cite":
                kb_id = self._required_allowed_kb(call, allowed_kb_ids)
                chunk_id = str(call.arguments.get("chunk_id") or "").strip()
                if not chunk_id:
                    raise ValueError("chunk_id is required.")
                item = self.service.get_knowledge_chunk(
                    kb_id,
                    chunk_id,
                    version_id=self._fixed_version(call, kb_id),
                )
                anchor = {
                    "citation_id": f"citation_{chunk_id}",
                    "kb_id": kb_id,
                    "version_id": item.get("version_id"),
                    "chunk_id": chunk_id,
                    "document_id": item.get("document_id"),
                    "document_name": item.get("document_name"),
                    "snippet": _preview(str(item.get("text") or ""), 500),
                    "page_number": item.get("page_number"),
                    "visual_kind": item.get("visual_kind"),
                    "source_block_id": item.get("source_block_id"),
                }
                return RuntimeToolResult(
                    output=json.dumps(anchor, ensure_ascii=False),
                    metadata={
                        "content_types": ["text"],
                        "kb_id": kb_id,
                        "chunk_id": chunk_id,
                    },
                )
            if call.tool_name == WRITE_TOOL_NAME:
                kb_id = self._required_allowed_kb(call, allowed_kb_ids)
                tags_raw = call.arguments.get("tags")
                tags = [str(item) for item in tags_raw] if isinstance(tags_raw, list) else []
                proposal = self.service.create_knowledge_write_proposal(
                    kb_id,
                    title=str(call.arguments.get("title") or ""),
                    content=str(call.arguments.get("content") or ""),
                    tags=tags,
                    source_xpert_id=_optional(call.metadata.get("xpert_id")),
                    source_conversation_id=_optional(call.metadata.get("conversation_id")),
                    source_goal_id=_optional(call.metadata.get("goal_id")),
                    source_handoff_id=_optional(call.metadata.get("handoff_id")),
                    source_run_id=_optional(call.metadata.get("run_id")),
                )
                result = {
                    "proposal_id": proposal["proposal_id"],
                    "kb_id": kb_id,
                    "status": proposal["status"],
                    "revision": proposal["revision"],
                    "approval_required": True,
                }
                return RuntimeToolResult(
                    output=json.dumps(result, ensure_ascii=False),
                    metadata={
                        "content_types": ["text"],
                        "proposal_id": proposal["proposal_id"],
                        "kb_id": kb_id,
                    },
                )
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                str(exc),
                code="knowledge_tool_error",
            ) from exc
        raise RuntimeToolError(call.tool_name, "Knowledge tool not found.", code="tool_not_found")

    async def _search(
        self,
        call: RuntimeToolCall,
        allowed_kb_ids: list[str],
    ) -> RuntimeToolResult:
        query = str(call.arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required.")
        resource_configs = self._resource_configs(call)
        requested_top_k = call.arguments.get("top_k")
        top_k = max(
            1,
            min(
                int(
                    requested_top_k
                    or max(
                        (
                            int(item.get("top_k") or 5)
                            for item in resource_configs.values()
                        ),
                        default=5,
                    )
                ),
                10,
            ),
        )
        requested_kb = str(call.arguments.get("kb_id") or "").strip()
        targets = [self._assert_allowed(requested_kb, allowed_kb_ids)] if requested_kb else allowed_kb_ids
        warnings: list[str] = []
        combined: list[dict[str, Any]] = []
        for kb_id in targets:
            resource_config = resource_configs.get(kb_id, {})
            target_top_k = max(
                1,
                min(
                    int(requested_top_k or resource_config.get("top_k") or top_k),
                    10,
                ),
            )
            score_threshold = max(
                0.0,
                min(float(resource_config.get("score_threshold") or 0), 1.0),
            )
            try:
                result = await self.service.search_knowledge(
                    kb_id,
                    query,
                    top_k=target_top_k,
                    version_id=self._fixed_version(call, kb_id),
                )
            except Exception as exc:
                warnings.append(f"{kb_id}: {str(exc)[:180]}")
                continue
            warnings.extend(str(item)[:180] for item in result.get("warnings", []))
            for source in result.get("sources", []):
                effective_score = float(
                    source.get("rerank_score")
                    or source.get("score")
                    or source.get("fused_score")
                    or 0.0
                )
                if effective_score < score_threshold:
                    continue
                combined.append(
                    {
                        "kb_id": kb_id,
                        "version_id": result.get("version_id"),
                        "chunk_id": source.get("chunk_id"),
                        "document_id": source.get("source_document_id") or source.get("doc_id"),
                        "document_name": source.get("document_name"),
                        "text": str(source.get("text") or "")[:2000],
                        "matched_text": str(source.get("matched_text") or "")[:2000],
                        "score": source.get("score"),
                        "vector_score": source.get("vector_score"),
                        "fulltext_score": source.get("fulltext_score"),
                        "fused_score": source.get("fused_score"),
                        "rerank_score": source.get("rerank_score"),
                        "page_number": source.get("page_number"),
                        "visual_kind": source.get("visual_kind"),
                        "source_block_id": source.get("source_block_id"),
                        "resource_score_threshold": score_threshold,
                    }
                )
        if not combined and warnings:
            raise RuntimeToolError(
                call.tool_name,
                "All configured knowledge bases failed: " + "; ".join(warnings[:3]),
                code="knowledge_search_failed",
            )
        combined.sort(
            key=lambda item: (
                -float(item.get("rerank_score") or item.get("score") or 0.0),
                str(item.get("kb_id") or ""),
                str(item.get("chunk_id") or ""),
            )
        )
        selected = combined[:top_k]
        return RuntimeToolResult(
            output=json.dumps(
                {"results": selected, "result_count": len(selected), "warnings": warnings[:10]},
                ensure_ascii=False,
            ),
            metadata={
                "content_types": ["text"],
                "knowledge_base_count": len(targets),
                "result_count": len(selected),
                "warning_count": len(warnings),
            },
        )

    def _resource_configs(
        self,
        call: RuntimeToolCall,
    ) -> dict[str, dict[str, Any]]:
        raw = call.metadata.get("knowledge_resource_configs")
        if not isinstance(raw, list):
            return {}
        return {
            str(item.get("knowledge_base_id") or ""): dict(item)
            for item in raw
            if isinstance(item, dict)
            and str(item.get("knowledge_base_id") or "").strip()
        }

    def _allowed_knowledge_bases(self, call: RuntimeToolCall) -> list[str]:
        raw = call.metadata.get("knowledge_base_ids")
        if not isinstance(raw, list):
            raise RuntimeToolError(
                call.tool_name,
                "Knowledge tools require an explicit knowledge-base scope.",
                code="knowledge_scope_missing",
            )
        result = list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))
        if not result or len(result) > 5:
            raise RuntimeToolError(
                call.tool_name,
                "Knowledge tools require between 1 and 5 configured knowledge bases.",
                code="knowledge_scope_invalid",
            )
        return result

    def _fixed_version(self, call: RuntimeToolCall, kb_id: str) -> str | None:
        config = self._resource_configs(call).get(kb_id, {})
        value = str(config.get("evaluation_version_id") or "").strip()
        return value or None

    def _required_allowed_kb(self, call: RuntimeToolCall, allowed: list[str]) -> str:
        requested = str(call.arguments.get("kb_id") or "").strip()
        if not requested:
            raise ValueError("kb_id is required.")
        return self._assert_allowed(requested, allowed)

    def _assert_allowed(self, kb_id: str, allowed: list[str]) -> str:
        if kb_id not in allowed:
            raise RuntimeToolError(
                "knowledge_scope",
                "The requested knowledge base is outside this workflow agent's configured scope.",
                code="knowledge_scope_denied",
            )
        return kb_id


def register_knowledge_toolset_capability(
    capability_registry: CapabilityRegistry,
    provider: KnowledgeToolsetProvider,
) -> None:
    capability_registry.register(
        "knowledge_tools",
        provider,
        description="Active-version knowledge retrieval, citation, and approval-gated write tools.",
        metadata={"provider": "knowledge"},
    )


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _preview(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else f"{normalized[:limit].rstrip()}..."
