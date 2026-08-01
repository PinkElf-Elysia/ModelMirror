from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from server.main import app
from server.xpert_runtime import (
    ApprovalCoordinator,
    CapabilityRegistry,
    MiddlewareContext,
    MiddlewarePipeline,
    RuntimeApprovalConflictError,
    RuntimeApprovalStore,
    RuntimeInterrupt,
    RuntimeMiddlewareSpec,
    RuntimeToolCall,
    RuntimeToolResult,
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
    build_human_in_the_loop_middleware,
    run_tool_with_runtime,
)
from server.xpert_runtime import approval_api


def _create_approval(store: RuntimeApprovalStore):
    return store.create_request(
        action_key="task:agent:0:search",
        request_type="tool_call",
        task_id="task-1",
        run_id="run-1",
        node_id="agent-1",
        node_title="Research agent",
        scope_type="workflow",
        scope_id="task-1",
        timeout_seconds=3600,
        allowed_decisions=["approve", "edit", "reject"],
        tool_name="search",
        arguments={"query": "hello", "api_key": "secret-value"},
    )


def test_approval_store_persists_revision_and_redacts_public_payload(tmp_path) -> None:
    store = RuntimeApprovalStore(tmp_path)
    approval = _create_approval(store)

    public = store.serialize(approval)
    assert public["arguments"]["query"] == "hello"
    assert public["arguments"]["api_key"] == "[REDACTED]"

    decided = store.decide(
        approval.approval_id,
        revision=approval.revision,
        decision="edit",
        operator="tester",
        edited_arguments={"query": "updated", "authorization": "Bearer private"},
    )
    assert decided.status == "decided"
    assert decided.revision == 2
    assert store.serialize(decided)["edited_arguments"]["authorization"] == "[REDACTED]"

    reloaded = RuntimeApprovalStore(tmp_path).require(approval.approval_id)
    assert reloaded.decision == "edit"
    assert reloaded.edited_arguments == {
        "query": "updated",
        "authorization": "Bearer private",
    }
    with pytest.raises(RuntimeApprovalConflictError):
        store.decide(
            approval.approval_id,
            revision=1,
            decision="approve",
            operator="stale",
        )


def test_execution_store_suspend_claim_and_restart_recovery(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        workflow={"nodes": [], "edges": []},
        inputs={"user_input": "hello"},
    )
    store.suspend(
        "task-1",
        approval_id="approval-1",
        continuation={"queue": ["agent-1"], "executed": ["input-1"]},
        safe_event={
            "event": "runtime_approval_pending",
            "approval_id": "approval-1",
            "task_id": "task-1",
        },
    )

    reloaded = WorkflowExecutionStore(tmp_path)
    waiting = reloaded.require("task-1")
    assert waiting.status == "waiting"
    assert waiting.continuation["executed"] == ["input-1"]
    assert waiting.events[0]["event"] == "runtime_approval_pending"

    reloaded.mark_ready("task-1", approval_id="approval-1")
    claimed = reloaded.claim("task-1", worker_id="worker-a", lease_seconds=30)
    assert claimed.status == "running"
    assert claimed.lease_token
    with pytest.raises(WorkflowExecutionConflictError):
        reloaded.claim("task-1", worker_id="worker-b", lease_seconds=30)


@pytest.mark.asyncio
async def test_hitl_interrupt_never_falls_back_to_provider(tmp_path) -> None:
    approvals = RuntimeApprovalStore(tmp_path)
    provider = MagicMock()
    provider.call_tool = AsyncMock(
        return_value=RuntimeToolResult(output="should not run")
    )
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    spec = RuntimeMiddlewareSpec(
        node_id="hitl-1",
        middleware_id="human_in_the_loop",
        config={"interrupt_on_tools": "search", "timeout_seconds": 3600},
    )
    pipeline = MiddlewarePipeline(
        [build_human_in_the_loop_middleware(spec, approvals)]
    )
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "agent-1"},
    )

    with pytest.raises(RuntimeInterrupt) as caught:
        await run_tool_with_runtime(
            RuntimeToolCall(
                tool_name="search",
                arguments={"query": "hello"},
                metadata={"iteration": 1},
            ),
            capabilities,
            pipeline,
            context,
        )

    provider.call_tool.assert_not_awaited()
    approval = approvals.require(caught.value.approval_id)
    assert approval.status == "pending"
    assert approval.tool_name == "search"


@pytest.mark.asyncio
async def test_hitl_edit_resolution_calls_provider_once(tmp_path) -> None:
    approvals = RuntimeApprovalStore(tmp_path)
    provider = MagicMock()
    provider.call_tool = AsyncMock(
        return_value=RuntimeToolResult(output="edited result")
    )
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    spec = RuntimeMiddlewareSpec(
        node_id="hitl-1",
        middleware_id="human_in_the_loop",
        config={"interrupt_on_tools": "*"},
    )
    pipeline = MiddlewarePipeline(
        [build_human_in_the_loop_middleware(spec, approvals)]
    )
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "agent-1"},
    )

    result = await run_tool_with_runtime(
        RuntimeToolCall(
            tool_name="search",
            arguments={"query": "old"},
            metadata={
                "iteration": 1,
                "resolved_approval": {
                    "approval_id": "approval-1",
                    "decision": "edit",
                    "edited_arguments": {"query": "new"},
                },
            },
        ),
        capabilities,
        pipeline,
        context,
    )

    assert result.output == "edited result"
    provider.call_tool.assert_awaited_once()
    assert provider.call_tool.await_args.args[0].arguments == {"query": "new"}


@pytest.mark.asyncio
async def test_hitl_reject_returns_synthetic_result_without_provider_call(tmp_path) -> None:
    approvals = RuntimeApprovalStore(tmp_path)
    provider = MagicMock()
    provider.call_tool = AsyncMock(
        return_value=RuntimeToolResult(output="must not run")
    )
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    spec = RuntimeMiddlewareSpec(
        node_id="hitl-1",
        middleware_id="human_in_the_loop",
        config={"interrupt_on_tools": "*"},
    )
    pipeline = MiddlewarePipeline(
        [build_human_in_the_loop_middleware(spec, approvals)]
    )
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "agent-1"},
    )

    result = await run_tool_with_runtime(
        RuntimeToolCall(
            tool_name="search",
            arguments={"query": "old"},
            metadata={
                "iteration": 1,
                "resolved_approval": {
                    "approval_id": "approval-1",
                    "decision": "reject",
                    "message": "Use the cached source instead.",
                },
            },
        ),
        capabilities,
        pipeline,
        context,
    )

    provider.call_tool.assert_not_awaited()
    assert "Use the cached source instead." in result.output


@pytest.mark.asyncio
async def test_approval_coordinator_resumes_once_and_never_auto_approves_timeout(
    tmp_path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    approval = _create_approval(approvals)
    executions.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        workflow={"nodes": [], "edges": []},
        inputs={},
    )
    executions.suspend(
        "task-1",
        approval_id=approval.approval_id,
        continuation={"queue": ["agent-1"]},
    )
    resumed: list[str] = []
    expired: list[str] = []

    async def resume(execution, resolved) -> None:
        resumed.append(resolved.approval_id)
        executions.complete(execution.task_id, result="done")

    async def expire(execution, pending) -> None:
        expired.append(pending.approval_id)

    coordinator = ApprovalCoordinator(
        approvals,
        executions,
        resume,
        expire_execution=expire,
        enabled=True,
        worker_id="test-worker",
    )
    approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision="approve",
        operator="tester",
    )

    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert resumed == [approval.approval_id]
    assert executions.require("task-1").status == "completed"

    timeout = approvals.create_request(
        action_key="timeout-action",
        request_type="tool_call",
        task_id="task-timeout",
        run_id="run-timeout",
        node_id="agent-timeout",
        node_title="Timeout",
        scope_type="workflow",
        scope_id="task-timeout",
        timeout_seconds=30,
        allowed_decisions=["approve", "reject"],
        tool_name="search",
    )
    executions.create(
        task_id="task-timeout",
        run_id="run-timeout",
        run_type="workflow",
        workflow={"nodes": [], "edges": []},
        inputs={},
    )
    executions.suspend(
        "task-timeout",
        approval_id=timeout.approval_id,
        continuation={"queue": ["agent-timeout"]},
    )
    timeout.expires_at = time.time() - 1

    assert await coordinator.run_once() == 0
    assert approvals.require(timeout.approval_id).status == "expired"
    assert approvals.require(timeout.approval_id).decision is None
    assert expired == [timeout.approval_id]
    assert executions.require("task-timeout").status == "waiting"


@pytest.mark.asyncio
async def test_runtime_approval_api_filters_redacts_and_rejects_stale_revision(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    approval = _create_approval(approvals)
    executions.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        workflow={"nodes": [], "edges": []},
        inputs={},
    )
    executions.suspend(
        "task-1",
        approval_id=approval.approval_id,
        continuation={"queue": ["agent-1"]},
    )
    monkeypatch.setattr(approval_api, "_approval_store", approvals)
    monkeypatch.setattr(approval_api, "_execution_store", executions)
    monkeypatch.setattr(approval_api, "_coordinator", None)
    monkeypatch.setattr(approval_api, "_decision_validator", None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        listing = await client.get(
            "/api/runtime/approvals",
            params={"status": "pending", "task_id": "task-1"},
        )
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["arguments"]["api_key"] == "[REDACTED]"

        decided = await client.post(
            f"/api/runtime/approvals/{approval.approval_id}/decide",
            json={
                "revision": approval.revision,
                "decision": "approve",
                "operator": "tester",
            },
        )
        assert decided.status_code == 200
        assert executions.require("task-1").status == "ready"

        stale = await client.post(
            f"/api/runtime/approvals/{approval.approval_id}/decide",
            json={
                "revision": approval.revision,
                "decision": "approve",
                "operator": "stale",
            },
        )
        assert stale.status_code == 409
