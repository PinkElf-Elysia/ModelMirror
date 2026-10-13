"""Real Managed gateway and durable budgets; HTTP responses remain offline."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.model_router.workflow_gateway import ManagedWorkflowGateway, ManagedWorkflowRoutingError
from server.tests.test_workflow_managed_gateway import (
    MODEL_ID, qualified_interactive, qualified_interactive_agent,
)
from server.xpert_runtime.execution_budget import XpertExecutionBudget, use_execution_budget
from server.xpert_runtime.execution_store import WorkflowExecutionStore


SHAPES = ["stream", "unary", "json", "agent_text", "agent_tools"]


def budget_for(tmp_path, **limits):
    store = WorkflowExecutionStore(tmp_path / "executions")
    store.create(task_id="root", run_id="run-root", run_type="xpert",
                 workflow={"id": "synthetic", "nodes": [], "edges": []}, inputs={})
    budget = XpertExecutionBudget(max_concurrency=1, recursion_limit=1000, **limits)
    budget.bind_persistence(store, "root")
    return store, budget


def response(request):
    payload = json.loads(request.content)
    if payload["stream"]:
        frame = {"model": MODEL_ID, "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}
        return httpx.Response(200, content=("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode())
    return httpx.Response(200, json={"model": MODEL_ID, "choices": [{"message": {"content": '{"ok":true}'}}]})


def gateway_for(request, shape, handler):
    service, _ = request.getfixturevalue(
        "qualified_interactive_agent" if shape.startswith("agent") else "qualified_interactive"
    )
    gateway = ManagedWorkflowGateway.for_router(service, client_factory=lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False,
    ))
    start = gateway.start_agent_run if shape.startswith("agent") else gateway.start_node_run
    run = start(source_kind="workflow_classic", execution_reference="budget-run", node_id="node")
    return gateway, run, service


async def call(run, shape, sequence=1):
    kwargs = {"model_id": MODEL_ID, "messages": [{"role": "user", "content": "synthetic"}],
              "temperature": 0, "max_tokens": 64}
    if shape.startswith("agent"):
        if shape == "agent_tools":
            kwargs["tools"] = [{"type": "function", "function": {"name": "synthetic_read",
                               "parameters": {"type": "object", "properties": {}}}}]
        return await run.complete(**kwargs)
    kwargs.update(logical_call_key=f"synthetic:{sequence}", call_sequence=sequence)
    if shape == "stream":
        return [delta async for delta in run.stream_text(**kwargs)]
    if shape == "json":
        return await run.complete_json_object(**kwargs)
    return await run.complete_text_unary(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_managed_call_budget_stops_second_request(request, tmp_path, shape):
    store, budget = budget_for(tmp_path, max_model_calls=1)
    requests = []
    def handler(req):
        requests.append(req)
        return response(req)
    _, run, _ = gateway_for(request, shape, handler)
    with use_execution_budget(budget):
        await call(run, shape)
        with pytest.raises(ManagedWorkflowRoutingError, match="workflow_execution_budget_exhausted"):
            await call(run, shape, 2)
    assert len(requests) == 1
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert run.calls[-1].dispatched is False


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_managed_dispatch_observes_budget_already_on_disk(request, tmp_path, shape):
    store, budget = budget_for(tmp_path, max_model_calls=1)
    observed = []
    def handler(req):
        observed.append(json.loads(store.snapshot_path.read_text(encoding="utf-8")))
        return response(req)
    _, run, _ = gateway_for(request, shape, handler)
    with use_execution_budget(budget):
        await call(run, shape)
    assert len(observed) == 1
    reopened = WorkflowExecutionStore(store.storage_dir)
    assert reopened.require("root").execution_budget["usage"]["model_calls"] == 1
    assert '"model_calls": 1' in json.dumps(observed[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_budget_save_failure_prevents_managed_dispatch(request, tmp_path, monkeypatch, shape):
    store, budget = budget_for(tmp_path, max_model_calls=1)
    requests = []
    def handler(req):
        requests.append(req)
        return response(req)
    _, run, _ = gateway_for(request, shape, handler)
    def fail():
        raise OSError("synthetic write failure")
    monkeypatch.setattr(store, "_persist_unlocked", fail)
    with use_execution_budget(budget):
        with pytest.raises(ManagedWorkflowRoutingError):
            await call(run, shape)
    assert requests == []
    assert budget.model_calls == 0
    assert run.calls[-1].dispatched is False


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_cancelled_budget_queue_does_not_dispatch_or_charge(request, tmp_path, shape):
    store, budget = budget_for(tmp_path, max_model_calls=1)
    requests = []
    def handler(req):
        requests.append(req)
        return response(req)
    _, run, _ = gateway_for(request, shape, handler)
    await budget._semaphore.acquire()
    with use_execution_budget(budget):
        task = asyncio.create_task(call(run, shape))
        for _ in range(10):
            await asyncio.sleep(0)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            budget._semaphore.release()
    assert requests == []
    assert store.require("root").execution_budget["usage"]["model_calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_uncertain_call_keeps_budget_and_blocks_replayed_occurrence(request, tmp_path, shape):
    store, budget = budget_for(tmp_path, max_model_calls=2)
    requests = []
    def handler(req):
        requests.append(req)
        raise httpx.ReadTimeout("synthetic uncertain response", request=req)
    gateway, run, _ = gateway_for(request, shape, handler)
    with use_execution_budget(budget):
        with pytest.raises(ManagedWorkflowRoutingError):
            await call(run, shape)
        run.finish("uncertain")
        start = gateway.start_agent_run if shape.startswith("agent") else gateway.start_node_run
        with pytest.raises(ManagedWorkflowRoutingError):
            start(source_kind="workflow_classic", execution_reference="budget-run", node_id="node")
    assert len(requests) == 1
    assert budget.model_calls == 1
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert run.calls[-1].status == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", SHAPES)
async def test_gateway_holds_budget_slot_until_response_is_consumed(request, tmp_path, shape):
    store, budget = budget_for(tmp_path, max_model_calls=2)
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    async def handler(req):
        requests.append(req)
        if len(requests) == 1:
            entered.set()
            await release.wait()
        return response(req)
    gateway, first, _ = gateway_for(request, shape, handler)
    start = gateway.start_agent_run if shape.startswith("agent") else gateway.start_node_run
    second = start(source_kind="workflow_classic", execution_reference="budget-run", node_id="second")
    with use_execution_budget(budget):
        first_task = asyncio.create_task(call(first, shape))
        second_task = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            second_task = asyncio.create_task(call(second, shape))
            for _ in range(10):
                await asyncio.sleep(0)
            assert len(requests) == 1
            assert not second_task.done()
            assert budget.model_calls == 1
            release.set()
            await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=5)
        finally:
            release.set()
            for task in (first_task, second_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (first_task, second_task) if task is not None), return_exceptions=True)
    assert len(requests) == 2
    assert store.require("root").execution_budget["usage"]["model_calls"] == 2
