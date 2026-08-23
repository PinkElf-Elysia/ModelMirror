from __future__ import annotations

import hashlib
import json
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
    RuntimeMiddlewareFatalError,
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


def test_execution_store_preserves_bounded_skill_runtime_status(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-1",
        run_id="run-1",
        run_type="workflow",
        workflow={"nodes": [], "edges": []},
        inputs={"user_input": "hello"},
    )

    stored = store.append_event(
        "task-1",
        {
            "event": "skill_runtime_status",
            "status": "resource_accessed",
            "skill_id": "pdf-reader",
            "skill_version_id": "skillversion-1",
            "requirement": "required",
            "required_skill_ids": ["pdf-reader"],
            "available_skill_ids": ["optional-helper"],
            "resource_count": 9_999,
            "resource_paths": [
                "/host/private.txt",
                "../escape.txt",
                "C:\\private\\secret.txt",
                *[f"references/item-{index}.md" for index in range(20)],
            ],
            "query": "must not persist",
            "content": "must not persist",
        },
    ).events[-1]

    assert stored["skill_id"] == "pdf-reader"
    assert stored["skill_version_id"] == "skillversion-1"
    assert stored["requirement"] == "required"
    assert stored["required_skill_ids"] == ["pdf-reader"]
    assert stored["available_skill_ids"] == ["optional-helper"]
    assert stored["resource_count"] == 2_000
    assert len(stored["resource_paths"]) == 12
    assert all(path.startswith("references/") for path in stored["resource_paths"])
    assert "query" not in stored
    assert "content" not in stored


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
async def test_mcp_workflow_hitl_persists_only_redacted_read_only_arguments(
    tmp_path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path)
    provider = MagicMock()
    provider.call_tool = AsyncMock(return_value=RuntimeToolResult(output="no call"))
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    pipeline = MiddlewarePipeline(
        [
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hitl-1",
                    middleware_id="human_in_the_loop",
                    config={"interrupt_on_tools": "*"},
                ),
                approvals,
            )
        ]
    )
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "mcp-1"},
    )
    sentinel = "R20_PRIVATE_ARGUMENT_SENTINEL"

    with pytest.raises(RuntimeInterrupt) as caught:
        await run_tool_with_runtime(
            RuntimeToolCall(
                tool_name="search",
                arguments={"query": sentinel},
                metadata={
                    "iteration": 1,
                    "approval_read_only_args": True,
                    "approval_redacted_arguments": {"query": "[已脱敏]"},
                    "approval_argument_digest": "a" * 64,
                },
            ),
            capabilities,
            pipeline,
            context,
        )

    approval = approvals.require(caught.value.approval_id)
    assert approval.allowed_decisions == ["approve", "reject"]
    assert approval.arguments == {"query": "[已脱敏]"}
    assert approval.metadata["tool_input_schema"] == {}
    assert approval.metadata["arguments_digest"] == "a" * 64
    assert sentinel not in json.dumps(approvals.serialize(approval), ensure_ascii=False)

    with pytest.raises(RuntimeMiddlewareFatalError, match="does not allow argument editing"):
        await run_tool_with_runtime(
            RuntimeToolCall(
                tool_name="search",
                arguments={"query": sentinel},
                metadata={
                    "approval_read_only_args": True,
                    "resolved_approval": {
                        "approval_id": approval.approval_id,
                        "decision": "edit",
                        "edited_arguments": {"query": "changed"},
                    },
                },
            ),
            capabilities,
            pipeline,
            context,
        )

    provider.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_hub_hitl_request_exposes_fixed_registry_warning_and_redacts_arguments(
    tmp_path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path)
    provider = MagicMock()
    provider.call_tool = AsyncMock(return_value=RuntimeToolResult(output="no call"))
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    pipeline = MiddlewarePipeline(
        [
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hitl-1",
                    middleware_id="human_in_the_loop",
                    config={"interrupt_on_tools": "*"},
                ),
                approvals,
            )
        ]
    )
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "agent-1"},
    )

    with pytest.raises(RuntimeInterrupt) as caught:
        await run_tool_with_runtime(
            RuntimeToolCall(
                tool_name="hub__example__search",
                arguments={
                    "query": "x" * 20_001,
                    "items": list(range(201)),
                    "token": "private",
                },
                metadata={
                    "iteration": 1,
                    "hub_approval": {
                        "server_name": "io.example/public",
                        "version": "1.2.3",
                        "origin": "https://mcp.example.com",
                        "schema_digest": "a" * 64,
                        "arguments_digest": "b" * 64,
                    },
                },
            ),
            capabilities,
            pipeline,
            context,
        )

    provider.call_tool.assert_not_awaited()
    public = approvals.serialize(approvals.require(caught.value.approval_id))
    assert "io.example/public" in public["description"]
    assert "https://mcp.example.com" in public["description"]
    assert "Schema: " + "a" * 64 in public["description"]
    assert "Registry 收录不代表安全认证" in public["description"]
    assert public["arguments"]["query"] == "x" * 20_001
    assert public["arguments"]["items"] == list(range(201))
    assert public["arguments"]["token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_hub_hitl_edit_recomputes_bound_arguments_digest(tmp_path) -> None:
    approvals = RuntimeApprovalStore(tmp_path)
    hub_events: list[tuple[str, dict[str, object]]] = []
    provider = MagicMock()
    provider.call_tool = AsyncMock(return_value=RuntimeToolResult(output="edited"))
    capabilities = CapabilityRegistry()
    capabilities.register("mcp_tools", provider)
    pipeline = MiddlewarePipeline(
        [
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hitl-1",
                    middleware_id="human_in_the_loop",
                    config={"interrupt_on_tools": "*"},
                ),
                approvals,
                hub_event_recorder=lambda event_type, metadata: hub_events.append(
                    (event_type, metadata)
                ),
            )
        ]
    )
    context = MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={"run_id": "run-1", "node_id": "agent-1"},
    )
    edited = {"query": "new"}

    result = await run_tool_with_runtime(
        RuntimeToolCall(
            tool_name="hub__example__search",
            arguments={"query": "old"},
            metadata={
                "iteration": 1,
                "hub_approval": {"arguments_digest": "old"},
                "resolved_approval": {
                    "approval_id": "approval-1",
                    "decision": "edit",
                    "edited_arguments": edited,
                    "metadata": {"hub_approval": {"arguments_digest": "old"}},
                },
            },
        ),
        capabilities,
        pipeline,
        context,
    )

    assert result.output == "edited"
    forwarded = provider.call_tool.await_args.args[0]
    assert forwarded.arguments == edited
    expected = hashlib.sha256(
        json.dumps(
            edited,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert forwarded.metadata["hub_approval"]["arguments_digest"] == expected
    assert (
        forwarded.metadata["resolved_approval"]["metadata"]["hub_approval"][
            "arguments_digest"
        ]
        == expected
    )
    assert hub_events == [
        (
            "runtime_approval_approved",
            {
                "contract_id": None,
                "candidate_id": None,
                "tool_name": "hub__example__search",
            },
        )
    ]


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
