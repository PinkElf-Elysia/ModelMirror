"""Parent authorization at admission and dispatch; all HTTP is synthetic."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import server.main as main
import server.xpert_runtime.execution_budget as budgets
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError
from server.tests.test_workflow_managed_gateway import qualified_interactive, qualified_interactive_agent
from server.tests.test_workflow_r8a_managed_budget import SHAPES, gateway_for, call, response
from server.tests.test_workflow_r8a_automation_children import runtime, child, workflow
from server.tests.test_workflow_r8a_automation_deadline import start
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
from server.xpert_runtime.capabilities import CapabilityRegistry
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import MiddlewareContext
from server.xpert_runtime.tool_runner import run_tool_with_runtime
from server.xpert_runtime.toolset import RuntimeToolCall


def guard_context(guard):
    return budgets.use_execution_dispatch_guard(guard)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["workflow_node", "model_call", "tool_call"])
async def test_guard_runs_without_any_budget_and_resets_after_exit(kind):
    async def denied():
        raise WorkflowExecutionConflictError("synthetic rejection")
    with guard_context(denied):
        with pytest.raises(WorkflowExecutionConflictError):
            if kind == "workflow_node":
                await budgets.charge_execution_step(kind)
            else:
                async with budgets.execution_operation(kind):
                    pytest.fail("rejected operation entered")
    async with budgets.execution_operation(kind):
        pass


@pytest.mark.asyncio
async def test_nested_empty_context_cannot_remove_parent_guard():
    visited = []
    async def denied():
        visited.append("parent")
        raise WorkflowExecutionConflictError("synthetic rejection")
    with guard_context(denied), guard_context(None), budgets.use_execution_budget(None):
        with pytest.raises(WorkflowExecutionConflictError):
            await budgets.charge_execution_step("workflow_node")
    assert visited == ["parent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("when", ["before", "queued", "client_enter"])
async def test_managed_rechecks_parent_before_actual_dispatch(runtime, monkeypatch, request, shape, when):
    parents, children, _, _ = runtime
    claimed, metadata, clock = start(runtime, monkeypatch)
    await child(runtime, metadata)
    budget = budgets.XpertExecutionBudget(max_concurrency=1, recursion_limit=1000)
    budget.bind_persistence(children, "child")
    requests = []
    def handler(req):
        requests.append(req)
        return response(req)
    gateway, run, _ = gateway_for(request, shape, handler)
    if when == "client_enter":
        class SlowClient(httpx.AsyncClient):
            async def __aenter__(self):
                result = await super().__aenter__()
                clock[0] += 31
                return result
        gateway._client_factory = lambda: SlowClient(transport=httpx.MockTransport(handler), trust_env=False)
    entered_queue = asyncio.Event()
    class ObservedSemaphore(asyncio.Semaphore):
        async def acquire(self):
            if self.locked():
                entered_queue.set()
            return await super().acquire()
    budget._semaphore = ObservedSemaphore(1)
    if when == "queued":
        await budget._semaphore.acquire()
    if when == "before":
        clock[0] += 31
    async def check():
        await main._assert_automation_parent_not_cancelled(metadata)
    async def invoke():
        with guard_context(check), budgets.use_execution_budget(budget):
            return await call(run, shape)
    operation = asyncio.create_task(invoke())
    try:
        if when == "queued":
            await asyncio.wait_for(entered_queue.wait(), 3)
            clock[0] += 31
            budget._semaphore.release()
        with pytest.raises((ManagedWorkflowRoutingError, WorkflowExecutionConflictError)):
            await asyncio.wait_for(operation, 3)
    finally:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
    assert requests == []
    assert parents.get_execution(claimed.execution_id).runtime_expired_at is not None
    assert children.require("child").status == "cancelled"
    # Admission already persisted before the slow client entered; never refund it.
    expected = 1 if when == "client_enter" else 0
    assert budget.model_calls == expected
    assert children.require("child").execution_budget["usage"]["model_calls"] == expected
    assert run.calls[-1].dispatched is False


@pytest.mark.asyncio
async def test_tool_middleware_cannot_fall_back_past_guard():
    calls = []
    class Provider:
        async def call_tool(self, request):
            calls.append(request)
            pytest.fail("tool entered after denied guard")
    registry = CapabilityRegistry()
    registry.register("mcp_tools", Provider())
    async def denied():
        raise WorkflowExecutionConflictError("synthetic rejection")
    with guard_context(denied):
        with pytest.raises(WorkflowExecutionConflictError):
            await run_tool_with_runtime(RuntimeToolCall(tool_name="synthetic", arguments={}),
                                        registry, MiddlewarePipeline(), MiddlewareContext())
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["unary", "stream"])
async def test_legacy_transport_rechecks_after_client_enter(monkeypatch, shape):
    expired = False
    requests = []
    def handler(request):
        requests.append(request)
        return response(request)
    original = httpx.AsyncClient
    class SlowClient(original):
        async def __aenter__(self):
            nonlocal expired
            result = await super().__aenter__()
            expired = True
            return result
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: SlowClient(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://synthetic.invalid", "synthetic-key"))
    monkeypatch.setattr(main, "llm_client_kwargs", lambda: {"trust_env": False})
    async def check():
        if expired:
            raise WorkflowExecutionConflictError("synthetic expiry")
    with guard_context(check):
        with pytest.raises(WorkflowExecutionConflictError):
            if shape == "unary":
                await main.collect_chat_completion_text("synthetic", [main.ChatMessage(role="user", content="synthetic")],
                                                        gateway_url="https://synthetic.invalid", gateway_key="synthetic-key")
            else:
                _ = [part async for part in main._stream_workflow_llm_messages(
                    "synthetic", [main.ChatMessage(role="user", content="synthetic")],
                    temperature=0, max_tokens=64, token_usage={}, completion_receipt={},
                )]
    assert requests == []


@pytest.mark.asyncio
async def test_state_failure_does_not_charge_or_run():
    budget = budgets.XpertExecutionBudget(max_concurrency=1, recursion_limit=1000)
    async def unavailable():
        raise OSError("synthetic unavailable authority")
    with guard_context(unavailable), budgets.use_execution_budget(budget):
        with pytest.raises(OSError):
            async with budgets.execution_operation("tool_call"):
                pytest.fail("unverified authority entered")
    assert budget.steps_used == 0
    await asyncio.wait_for(budget._semaphore.acquire(), 1)
    budget._semaphore.release()


@pytest.mark.asyncio
async def test_legacy_fallback_cannot_send_after_parent_expires(monkeypatch):
    expired = False
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(503, json={"error": {"message": "synthetic unavailable"}})
    original = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("https://synthetic.invalid", "synthetic-key"))
    monkeypatch.setattr(main, "llm_client_kwargs", lambda: {"trust_env": False})
    monkeypatch.setattr(main, "should_fallback_model", lambda *args: True)
    monkeypatch.setattr(main, "fallback_model_for", lambda *args: "synthetic-fallback")
    async def check():
        if expired:
            raise WorkflowExecutionConflictError("synthetic expiry")
    with guard_context(check):
        with pytest.raises(WorkflowExecutionConflictError):
            async for _ in main._stream_workflow_llm_messages(
                "synthetic", [main.ChatMessage(role="user", content="synthetic")],
                temperature=0, max_tokens=64, token_usage={}, completion_receipt={},
            ):
                expired = True
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_guard_context_is_task_local_and_does_not_leak_after_cancel():
    entered = asyncio.Event()
    async def denied():
        raise WorkflowExecutionConflictError("synthetic rejection")
    async def owner():
        with guard_context(denied):
            entered.set()
            await asyncio.Event().wait()
    operation = asyncio.create_task(owner())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        async with budgets.execution_operation("model_call"):
            pass
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
    async with budgets.execution_operation("model_call"):
        pass


@pytest.mark.asyncio
async def test_real_runner_checks_parent_after_stream_suspension(runtime, monkeypatch):
    parents, children, _, _ = runtime
    claimed, metadata, clock = start(runtime, monkeypatch)
    result = await main._run_workflow_response(
        main.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"}),
        None, runtime_run_type="xpert", runtime_metadata=metadata,
    )
    frames = []
    async for frame in result.body_iterator:
        if isinstance(frame, bytes):
            frame = frame.decode("utf-8")
        frames.append(frame)
        if '"event": "workflow_meta"' in frame:
            clock[0] += 31
    events = [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]
    assert any(event["event"] == "workflow_meta" for event in events)
    assert not any(event["event"] in {"node_start", "workflow_end"} for event in events)
    assert children.list_items()[0].status == "cancelled"
    assert parents.get_execution(claimed.execution_id).runtime_expired_at is not None
