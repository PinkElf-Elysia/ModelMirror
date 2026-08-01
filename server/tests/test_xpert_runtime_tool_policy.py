from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from server.xpert_runtime import (
    CapabilityRegistry,
    InMemoryToolAuditStore,
    MiddlewareContext,
    MiddlewarePipeline,
    RuntimeToolCall,
    RuntimeToolError,
    RuntimeToolResult,
    ToolPermissionPolicy,
    run_tool_with_runtime,
)


def _registry_with_provider(provider: MagicMock) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register("mcp_tools", provider)
    return registry


@pytest.mark.asyncio
async def test_policy_allow_by_default() -> None:
    provider = MagicMock()
    provider.call_tool = AsyncMock(
        return_value=RuntimeToolResult(
            output="ok",
            content=[{"type": "text", "text": "ok"}],
            metadata={"content_types": ["text"]},
        )
    )
    audit_store = InMemoryToolAuditStore()

    result = await run_tool_with_runtime(
        RuntimeToolCall(tool_name="echo", arguments={"message": "hi"}),
        _registry_with_provider(provider),
        MiddlewarePipeline([]),
        MiddlewareContext(),
        policy=ToolPermissionPolicy(allow_by_default=True),
        audit_store=audit_store,
    )

    assert result.output == "ok"
    provider.call_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_deny_blocks_tool_call() -> None:
    provider = MagicMock()
    provider.call_tool = AsyncMock(
        return_value=RuntimeToolResult(output="should not run")
    )
    audit_store = InMemoryToolAuditStore()

    with pytest.raises(RuntimeToolError) as exc_info:
        await run_tool_with_runtime(
            RuntimeToolCall(tool_name="evil_tool", arguments={}),
            _registry_with_provider(provider),
            MiddlewarePipeline([]),
            MiddlewareContext(),
            policy=ToolPermissionPolicy(
                denied_tools={"evil_tool"},
                allow_by_default=True,
            ),
            audit_store=audit_store,
        )

    assert exc_info.value.code == "tool_denied"
    provider.call_tool.assert_not_awaited()
    records = await audit_store.list_records()
    assert len(records) == 1
    assert records[0].tool_name == "evil_tool"
    assert records[0].status == "denied"


@pytest.mark.asyncio
async def test_audit_records_success() -> None:
    provider = MagicMock()
    provider.call_tool = AsyncMock(
        return_value=RuntimeToolResult(
            output="hello world",
            content=[{"type": "text", "text": "hello world"}],
            metadata={"content_types": ["text"]},
        )
    )
    audit_store = InMemoryToolAuditStore()

    await run_tool_with_runtime(
        RuntimeToolCall(tool_name="echo", arguments={}),
        _registry_with_provider(provider),
        MiddlewarePipeline([]),
        MiddlewareContext(),
        policy=ToolPermissionPolicy(allow_by_default=True),
        audit_store=audit_store,
    )

    records = await audit_store.list_records()
    assert len(records) == 1
    record = records[0]
    assert record.tool_name == "echo"
    assert record.status == "succeeded"
    assert record.output_length == 11
    assert record.content_types == ["text"]
    assert record.duration_ms is not None and record.duration_ms >= 0
    assert record.finished_at is not None
    assert record.error is None


@pytest.mark.asyncio
async def test_audit_records_failure() -> None:
    provider = MagicMock()
    provider.call_tool = AsyncMock(
        side_effect=RuntimeToolError(
            "echo",
            "something went wrong",
            code="tool_error",
        )
    )
    audit_store = InMemoryToolAuditStore()

    with pytest.raises(RuntimeToolError):
        await run_tool_with_runtime(
            RuntimeToolCall(tool_name="echo", arguments={}),
            _registry_with_provider(provider),
            MiddlewarePipeline([]),
            MiddlewareContext(),
            policy=ToolPermissionPolicy(allow_by_default=True),
            audit_store=audit_store,
        )

    records = await audit_store.list_records()
    assert len(records) == 1
    record = records[0]
    assert record.status == "failed"
    assert record.error is not None
    assert "something went wrong" in record.error
    assert record.finished_at is not None
    assert record.duration_ms is not None
