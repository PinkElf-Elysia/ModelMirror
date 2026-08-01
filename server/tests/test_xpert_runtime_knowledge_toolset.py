from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.xpert_runtime import (
    CapabilityRegistry,
    InMemoryToolAuditStore,
    KnowledgeToolsetProvider,
    MiddlewareContext,
    MiddlewarePipeline,
    RuntimeToolCall,
    RuntimeToolError,
    ToolPermissionPolicy,
    event_recorder,
    register_knowledge_toolset_capability,
    run_tool_with_runtime,
)


def knowledge_metadata(**overrides):
    return {
        "knowledge_base_ids": ["kb_one", "kb_two"],
        "knowledge_read_enabled": True,
        "knowledge_write_enabled": True,
        "xpert_id": "xpert_writer",
        "conversation_id": "conversation_1",
        "goal_id": "goal_1",
        "handoff_id": "handoff_1",
        "run_id": "run_1",
        **overrides,
    }


@pytest.mark.asyncio
async def test_knowledge_search_fuses_allowed_bases_and_bounds_text() -> None:
    service = SimpleNamespace(
        search_knowledge=AsyncMock(
            side_effect=[
                {
                    "version_id": "v1",
                    "sources": [
                        {
                            "chunk_id": "chunk_b",
                            "source_document_id": "doc_b",
                            "document_name": "B",
                            "text": "B" * 3000,
                            "score": 0.7,
                        }
                    ],
                    "warnings": [],
                },
                {
                    "version_id": "v2",
                    "sources": [
                        {
                            "chunk_id": "chunk_a",
                            "source_document_id": "doc_a",
                            "document_name": "A",
                            "text": "A text",
                            "score": 0.9,
                        }
                    ],
                    "warnings": ["rerank fallback"],
                },
            ]
        )
    )
    provider = KnowledgeToolsetProvider(service)

    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="knowledge_search",
            arguments={"query": "architecture", "top_k": 2},
            metadata=knowledge_metadata(),
        )
    )

    payload = json.loads(result.output)
    assert [item["chunk_id"] for item in payload["results"]] == ["chunk_a", "chunk_b"]
    assert len(payload["results"][1]["text"]) == 2000
    assert payload["warnings"] == ["rerank fallback"]
    assert result.metadata["knowledge_base_count"] == 2


@pytest.mark.asyncio
async def test_knowledge_search_applies_bound_resource_limits() -> None:
    service = SimpleNamespace(
        search_knowledge=AsyncMock(
            side_effect=[
                {
                    "version_id": "v1",
                    "sources": [
                        {"chunk_id": "keep", "document_name": "One", "text": "A", "score": 0.9},
                        {"chunk_id": "drop", "document_name": "One", "text": "B", "score": 0.6},
                    ],
                    "warnings": [],
                },
                {
                    "version_id": "v2",
                    "sources": [
                        {"chunk_id": "second", "document_name": "Two", "text": "C", "score": 0.8}
                    ],
                    "warnings": [],
                },
            ]
        )
    )
    provider = KnowledgeToolsetProvider(service)

    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="knowledge_search",
            arguments={"query": "architecture"},
            metadata=knowledge_metadata(
                knowledge_resource_configs=[
                    {
                        "knowledge_base_id": "kb_one",
                        "top_k": 2,
                        "score_threshold": 0.75,
                    },
                    {
                        "knowledge_base_id": "kb_two",
                        "top_k": 1,
                        "score_threshold": 0,
                    },
                ]
            ),
        )
    )

    payload = json.loads(result.output)
    assert [item["chunk_id"] for item in payload["results"]] == ["keep", "second"]
    assert payload["results"][0]["resource_score_threshold"] == 0.75
    assert service.search_knowledge.await_args_list[0].kwargs["top_k"] == 2
    assert service.search_knowledge.await_args_list[1].kwargs["top_k"] == 1


@pytest.mark.asyncio
async def test_knowledge_tools_reject_cross_base_access() -> None:
    provider = KnowledgeToolsetProvider(SimpleNamespace())

    with pytest.raises(RuntimeToolError) as exc_info:
        await provider.call_tool(
            RuntimeToolCall(
                tool_name="knowledge_get",
                arguments={"kb_id": "kb_forbidden", "chunk_id": "chunk_1"},
                metadata=knowledge_metadata(),
            )
        )

    assert exc_info.value.code == "knowledge_scope_denied"


@pytest.mark.asyncio
async def test_knowledge_get_and_cite_use_active_chunk_lookup() -> None:
    chunk = {
        "version_id": "version_1",
        "chunk_id": "chunk_1",
        "document_id": "doc_1",
        "document_name": "Guide.md",
        "text": "Bounded source context",
        "page_number": 3,
        "visual_kind": None,
        "source_block_id": "block_1",
    }
    service = SimpleNamespace(get_knowledge_chunk=MagicMock(return_value=chunk))
    provider = KnowledgeToolsetProvider(service)

    read = await provider.call_tool(
        RuntimeToolCall(
            tool_name="knowledge_get",
            arguments={"kb_id": "kb_one", "chunk_id": "chunk_1"},
            metadata=knowledge_metadata(),
        )
    )
    citation = await provider.call_tool(
        RuntimeToolCall(
            tool_name="knowledge_cite",
            arguments={"kb_id": "kb_one", "chunk_id": "chunk_1"},
            metadata=knowledge_metadata(),
        )
    )

    assert json.loads(read.output)["text"] == "Bounded source context"
    assert json.loads(citation.output)["citation_id"] == "citation_chunk_1"
    assert service.get_knowledge_chunk.call_count == 2


@pytest.mark.asyncio
async def test_propose_write_returns_only_approval_metadata() -> None:
    service = SimpleNamespace(
        create_knowledge_write_proposal=MagicMock(
            return_value={
                "proposal_id": "proposal_1",
                "status": "pending",
                "revision": 1,
                "content": "private proposal body",
            }
        )
    )
    provider = KnowledgeToolsetProvider(service)

    result = await provider.call_tool(
        RuntimeToolCall(
            tool_name="knowledge_propose_write",
            arguments={
                "kb_id": "kb_one",
                "title": "Correction",
                "content": "private proposal body",
            },
            metadata=knowledge_metadata(),
        )
    )

    assert "private proposal body" not in result.output
    assert json.loads(result.output) == {
        "proposal_id": "proposal_1",
        "kb_id": "kb_one",
        "status": "pending",
        "revision": 1,
        "approval_required": True,
    }
    service.create_knowledge_write_proposal.assert_called_once_with(
        "kb_one",
        title="Correction",
        content="private proposal body",
        tags=[],
        source_xpert_id="xpert_writer",
        source_conversation_id="conversation_1",
        source_goal_id="goal_1",
        source_handoff_id="handoff_1",
        source_run_id="run_1",
    )


@pytest.mark.asyncio
async def test_knowledge_tool_uses_runtime_policy_audit_and_middleware() -> None:
    service = SimpleNamespace(
        get_knowledge_chunk=MagicMock(
            return_value={
                "chunk_id": "chunk_1",
                "text": "safe",
                "document_id": "doc_1",
                "document_name": "Doc",
            }
        )
    )
    provider = KnowledgeToolsetProvider(service)
    registry = CapabilityRegistry()
    register_knowledge_toolset_capability(registry, provider)
    audit = InMemoryToolAuditStore()

    result = await run_tool_with_runtime(
        RuntimeToolCall(
            tool_name="knowledge_get",
            arguments={"kb_id": "kb_one", "chunk_id": "chunk_1"},
            metadata=knowledge_metadata(),
        ),
        registry,
        MiddlewarePipeline([event_recorder]),
        MiddlewareContext(task_id="knowledge_task"),
        capability_name="knowledge_tools",
        policy=ToolPermissionPolicy(allowed_tools={"knowledge_get"}, allow_by_default=False),
        audit_store=audit,
    )

    assert json.loads(result.output)["text"] == "safe"
    records = await audit.list_records(tool_name="knowledge_get")
    assert records[-1].status == "succeeded"


def test_register_knowledge_toolset_capability() -> None:
    registry = CapabilityRegistry()
    provider = KnowledgeToolsetProvider(SimpleNamespace())
    register_knowledge_toolset_capability(registry, provider)
    capability = registry.require("knowledge_tools")
    assert capability.implementation is provider
    assert capability.metadata["provider"] == "knowledge"
