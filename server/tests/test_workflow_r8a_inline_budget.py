"""Inline production Runner budget linkage, using only offline model output."""
from __future__ import annotations

import asyncio

import pytest

import server.main as main
from server.tests.test_workflow_r8a_budget_journal import LIMITS, create, initialize
from server.tests.test_workflow_r8a_budget_tree import budget
from server.tests.test_workflow_r8a_handoff_budget import setup, child_graph
from server.tests.test_workflow_resource_nodes import resource_stores, _with_bound_resources
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.xpert_runtime import WorkflowExecutionStore
from server.xpert_runtime.capabilities import CapabilityRegistry
from server.xpert_runtime.execution_budget import (
    XpertExecutionBudgetExceeded, XpertExecutionBudgetStateError, execution_operation, use_execution_budget,
)
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
from server.xpert_runtime.interrupts import RuntimeMiddlewareFatalError
from server.xpert_runtime.external_xpert_toolset import ExternalXpertToolsetProvider
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import MiddlewareContext
from server.xpert_runtime.tool_runner import run_tool_with_runtime
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolResult


RESOURCE = {"xpert_id": "target", "pinned_version": 1, "tool_name": "specialist"}


def call():
    return RuntimeToolCall(tool_name="specialist", arguments={"task": "合成输入"}, metadata={
        "task_id": "root", "node_id": "agent", "iteration": 1,
        "xpert_id": "parent", "external_xpert_tools": [dict(RESOURCE)],
    })


def root(tmp_path, monkeypatch, *, cap=1, concurrency=2):
    store, _, registry, calls = setup(tmp_path, monkeypatch)
    create(store, "root")
    initialize(store, limits={**LIMITS, "max_model_calls": cap, "max_concurrency": concurrency})
    return store, registry, calls


async def invoke(request=None):
    request = request or call()
    return await main.execute_external_xpert_resource(RESOURCE, request.arguments["task"], request)


@pytest.mark.asyncio
async def test_inline_child_has_durable_link_before_dispatch_and_after_reopen(tmp_path, monkeypatch):
    store, _, calls = root(tmp_path, monkeypatch)
    links = []

    async def inspect(*args, **kwargs):
        current = next(item for item in store.list_items() if item.task_id != "root")
        links.append(current.execution_budget_parent)
        async with execution_operation("model_call"):
            calls.append("dispatched")
            yield "离线结果"

    monkeypatch.setattr(main, "stream_workflow_llm_text", inspect)
    with use_execution_budget(budget(store, "root")):
        result = await invoke()
    assert result.output == "离线结果"
    assert links[0] is not None, "inline child dispatched as an independent budget root"
    reopened = WorkflowExecutionStore(store.storage_dir)
    child = next(item for item in reopened.list_items() if item.task_id != "root")
    assert child.execution_budget_parent["parent_task_id"] == "root"
    assert reopened.require("root").execution_budget_children[child.execution_budget_parent["occurrence_id"]] == child.task_id
    assert child.execution_budget["usage"]["model_calls"] == 1
    assert reopened.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_same_inline_occurrence_reuses_committed_result_without_reexecution(tmp_path, monkeypatch):
    store, _, calls = root(tmp_path, monkeypatch, cap=1)
    with use_execution_budget(budget(store, "root")):
        first = await invoke()
    reopened = WorkflowExecutionStore(store.storage_dir)
    monkeypatch.setattr(main, "workflow_execution_store", reopened)
    claimed = reopened.claim("root", worker_id="restart")
    with use_execution_budget(budget(reopened, "root", claimed.lease_token)):
        second = await invoke()
    assert first.output == second.output
    assert calls == ["dispatched"]
    assert len(reopened.list_items()) == 2


@pytest.mark.asyncio
async def test_inline_tool_does_not_hold_root_slot_while_child_requests_it(tmp_path, monkeypatch):
    store, _, calls = root(tmp_path, monkeypatch, concurrency=1)
    registry = CapabilityRegistry()
    registry.register("external_xpert_tools", ExternalXpertToolsetProvider(main.execute_external_xpert_resource))
    with use_execution_budget(budget(store, "root")):
        result = await asyncio.wait_for(run_tool_with_runtime(
            call(), registry, MiddlewarePipeline(), MiddlewareContext(),
            capability_name="external_xpert_tools",
        ), 3)
    assert result.output == "离线结果"
    assert calls == ["dispatched"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert store.require("root").execution_budget["usage"]["tool_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["task", "target", "version", "source", "depth", "managed_source"])
async def test_same_call_slot_contract_drift_is_rejected(tmp_path, monkeypatch, field):
    store, _, calls = root(tmp_path, monkeypatch, cap=3)
    with use_execution_budget(budget(store, "root")):
        await invoke()
        request, resource = call(), dict(RESOURCE)
        if field == "task":
            request.arguments["task"] = "不同输入"
        elif field == "target":
            resource["xpert_id"] = "another-target"
        elif field == "version":
            resource["pinned_version"] = 2
        elif field == "source":
            request.metadata["xpert_id"] = "another-source"
        elif field == "depth":
            request.metadata["external_xpert_depth"] = 1
        else:
            request.metadata["provider_workload_source_kind"] = "xpert_chat"
        before = store.snapshot_path.read_bytes()
        with pytest.raises(XpertExecutionBudgetStateError, match="CONTRACT_CHANGED"):
            await main.execute_external_xpert_resource(resource, request.arguments["task"], request)
    assert store.snapshot_path.read_bytes() == before
    assert calls == ["dispatched"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_context", "forged_parent", "missing_node", "invalid_iteration", "invalid_index", "cancelled", "takeover"])
async def test_invalid_parent_authority_never_prepares_or_dispatches(tmp_path, monkeypatch, fault):
    store, _, calls = root(tmp_path, monkeypatch)
    request = call()
    claimed = store.claim("root", worker_id="original")
    value = budget(store, "root", claimed.lease_token)
    if fault == "forged_parent":
        request.metadata["task_id"] = "forged"
    elif fault == "missing_node":
        request.metadata.pop("node_id")
    elif fault == "invalid_iteration":
        request.metadata["iteration"] = True
    elif fault == "invalid_index":
        request.metadata["parallel_batch_index"] = -1
    elif fault == "cancelled":
        store.cancel("root")
    elif fault == "takeover":
        store.claim("root", worker_id="new", now=claimed.lease_expires_at + 1)
    before = store.snapshot_path.read_bytes()
    with use_execution_budget(None if fault == "missing_context" else value):
        with pytest.raises((XpertExecutionBudgetStateError, WorkflowExecutionConflictError)):
            await invoke(request)
    assert store.snapshot_path.read_bytes() == before
    assert calls == []
    assert len(store.list_items()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["cancelled", "takeover"])
async def test_parent_changes_during_prepare_prevent_child_creation(tmp_path, monkeypatch, fault):
    store, _, calls = root(tmp_path, monkeypatch)
    claimed = store.claim("root", worker_id="original")
    prepare = main.prepare_published_xpert_run

    async def changed(*args, **kwargs):
        result = await prepare(*args, **kwargs)
        if fault == "cancelled":
            store.cancel("root")
        else:
            store.claim("root", worker_id="new", now=claimed.lease_expires_at + 1)
        return result

    monkeypatch.setattr(main, "prepare_published_xpert_run", changed)
    with use_execution_budget(budget(store, "root", claimed.lease_token)):
        with pytest.raises(WorkflowExecutionConflictError):
            await invoke()
    assert calls == []
    assert len(store.list_items()) == 1


@pytest.mark.asyncio
async def test_unsettled_child_blocks_replay_even_with_new_model_call_slot(tmp_path, monkeypatch):
    store, _, calls = root(tmp_path, monkeypatch, cap=3)

    async def uncertain(*args, **kwargs):
        async with execution_operation("model_call"):
            calls.append("uncertain")
            raise OSError("synthetic lost response")
            yield "unreachable"

    monkeypatch.setattr(main, "stream_workflow_llm_text", uncertain)
    provider = ExternalXpertToolsetProvider(main.execute_external_xpert_resource)
    with use_execution_budget(budget(store, "root")):
        with pytest.raises(RuntimeMiddlewareFatalError, match="NOT_COMPLETED"):
            await provider.call_tool(call())
    reopened = WorkflowExecutionStore(store.storage_dir)
    monkeypatch.setattr(main, "workflow_execution_store", reopened)
    request = call()
    request.metadata["iteration"] = 2
    claimed = reopened.claim("root", worker_id="restart")
    with use_execution_budget(budget(reopened, "root", claimed.lease_token)):
        with pytest.raises(RuntimeMiddlewareFatalError, match="ALREADY_DISPATCHED"):
            await provider.call_tool(request)
    assert calls == ["uncertain"]
    assert len(reopened.list_items()) == 2
    assert reopened.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_budget_remains_linked_after_interrupted_consumer_and_fresh_context(tmp_path, monkeypatch):
    store, _, _ = root(tmp_path, monkeypatch)

    async def interrupted(response):
        item = next(item for item in store.list_items() if item.task_id != "root")
        with use_execution_budget(budget(store, item.task_id)):
            async with execution_operation("model_call"):
                pass
        raise OSError("synthetic consumer interruption")

    monkeypatch.setattr(main, "consume_workflow_stream", interrupted)
    with use_execution_budget(budget(store, "root")):
        with pytest.raises(RuntimeMiddlewareFatalError):
            await invoke()
    reopened = WorkflowExecutionStore(store.storage_dir)
    item = next(item for item in reopened.list_items() if item.task_id != "root")
    claimed = reopened.claim(item.task_id, worker_id="restart")
    # No parent ContextVar survives. The persisted link alone enforces root cap.
    with use_execution_budget(budget(reopened, item.task_id, claimed.lease_token)):
        with pytest.raises(XpertExecutionBudgetExceeded):
            async with execution_operation("model_call"):
                pytest.fail("fresh context bypassed the exhausted root")
    assert reopened.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_next_server_call_index_is_a_distinct_occurrence(tmp_path, monkeypatch):
    store, _, calls = root(tmp_path, monkeypatch, cap=2)
    with use_execution_budget(budget(store, "root")):
        await invoke()
        request = call()
        request.metadata["parallel_batch_index"] = 1
        await invoke(request)
    assert calls == ["dispatched", "dispatched"]
    assert len(store.require("root").execution_budget_children) == 2
    assert store.require("root").execution_budget["usage"]["model_calls"] == 2


@pytest.mark.asyncio
async def test_concurrent_children_still_share_single_root_model_slot(tmp_path, monkeypatch):
    store, _, calls = root(tmp_path, monkeypatch, cap=2, concurrency=1)
    entered, release, second_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def controlled(*args, **kwargs):
        async with execution_operation("model_call"):
            calls.append("dispatched")
            if len(calls) == 1:
                entered.set()
                await release.wait()
            else:
                second_entered.set()
            yield "离线结果"

    monkeypatch.setattr(main, "stream_workflow_llm_text", controlled)
    # Independent branches each have their own direct parent but one common root.
    from server.tests.test_workflow_r8a_budget_tree import child
    child(store, "left")
    child(store, "right")
    provider = ExternalXpertToolsetProvider(main.execute_external_xpert_resource)
    registry = CapabilityRegistry()
    registry.register("external_xpert_tools", provider)

    async def branch(key):
        request = call()
        request.metadata["task_id"] = key
        with use_execution_budget(budget(store, key)):
            return await run_tool_with_runtime(request, registry, MiddlewarePipeline(), MiddlewareContext(),
                                               capability_name="external_xpert_tools")

    first = asyncio.create_task(branch("left"))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(branch("right"))
        await asyncio.sleep(0.05)
        assert not second_entered.is_set()
    finally:
        release.set()
        await asyncio.wait_for(first, 3)
        if second is not None:
            await asyncio.wait_for(second, 3)
    assert second_entered.is_set()
    assert store.require("root").execution_budget["usage"]["model_calls"] == 2
    assert store.require("root").execution_budget["usage"]["tool_calls"] == 2


@pytest.mark.asyncio
async def test_zero_tool_budget_rejects_inline_before_creating_child(tmp_path, monkeypatch):
    store, _, _, calls = setup(tmp_path, monkeypatch)
    create(store, "root")
    initialize(store, limits={**LIMITS, "max_tool_calls": 0})
    registry = CapabilityRegistry()
    registry.register("external_xpert_tools", ExternalXpertToolsetProvider(main.execute_external_xpert_resource))
    with use_execution_budget(budget(store, "root")):
        with pytest.raises(XpertExecutionBudgetExceeded):
            await run_tool_with_runtime(call(), registry, MiddlewarePipeline(), MiddlewareContext(),
                                        capability_name="external_xpert_tools")
    assert calls == []
    assert len(store.list_items()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_v2", [False, True])
async def test_real_parent_runner_preserves_external_compatibility_and_binds_calls(resource_stores, tmp_path, monkeypatch, strategy_v2):
    xperts, _ = resource_stores
    prepare = main.prepare_published_xpert_run
    store, _, _, calls = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "prepare_published_xpert_run", prepare)
    monkeypatch.setattr(main, "WORKFLOW_AGENT_STRATEGY_V2_ENABLED", strategy_v2)
    specialist = xperts.create_xpert(name="离线子流程")
    draft = specialist.draft.model_copy(deep=True)
    draft.workflow = NativeWorkflowDefinition.model_validate(child_graph())
    draft.output_variable = "answer"
    specialist = xperts.update_xpert(specialist.id, {"draft": draft.model_dump(mode="json")})
    xperts.publish_xpert(specialist.id, expected_revision=specialist.draft_revision)
    graph = _with_bound_resources(NativeWorkflowDefinition.model_validate(child_graph()), external_xpert_id=specialist.id)
    graph.id = "parent"
    graph.nodes[1].data.update({"agentStrategy": "function_calling", "maxIterations": 4})
    turns = iter([
        '{"tool":"specialist","arguments":{"task":"第一项"}}',
        '{"tool":"specialist","arguments":{"task":"第二项"}}',
        '{"answer":"父执行完成"}',
    ])

    async def complete(*args, **kwargs):
        async with execution_operation("model_call"):
            return next(turns)

    class Model:
        def __init__(self, **kwargs):
            pytest.fail("External Xpert must retain its existing compatibility path")

    monkeypatch.setattr(main, "collect_chat_completion_text", complete)
    monkeypatch.setattr(main, "OpenAICompatibleAgentModelClient", Model)
    response = await main._run_workflow_response(
        main.WorkflowRunRequest(workflow=graph.model_dump(mode="json"), inputs={"user_input": "合成任务"}), None,
        runtime_run_type="xpert", runtime_source_id="parent",
        runtime_metadata={"xpert_id": "parent", "xpert_agent_config": {**LIMITS, "max_concurrency": 1, "max_model_calls": 5}},
    )
    outcome = await asyncio.wait_for(main.consume_workflow_stream(response), 10)
    assert outcome["final_output"] == "父执行完成"
    parent = next(item for item in store.list_items() if item.workflow["id"] == "parent")
    assert len(parent.execution_budget_children) == 2
    assert len(calls) == 2
    assert parent.execution_budget["usage"]["model_calls"] == 5
    assert parent.execution_budget["usage"]["tool_calls"] == 2
    for key in parent.execution_budget_children.values():
        assert store.require(key).execution_budget_parent["parent_task_id"] == parent.task_id


@pytest.mark.asyncio
async def test_real_child_timer_is_not_success_or_permission_to_redispatch(tmp_path, monkeypatch):
    store, _, calls = root(tmp_path, monkeypatch)
    prepare = main.prepare_published_xpert_run

    async def waiting(*args, **kwargs):
        prepared = await prepare(*args, **kwargs)
        graph = child_graph()
        graph["nodes"][1] = {"id": "agent", "type": "suspend_wait", "data": {
            "kind": "suspend_wait", "waitMode": "duration", "durationSeconds": 60,
            "outputVariable": "answer",
        }}
        prepared.request = main.WorkflowRunRequest(workflow=graph, inputs={"user_input": "合成输入"})
        return prepared

    monkeypatch.setattr(main, "prepare_published_xpert_run", waiting)
    with use_execution_budget(budget(store, "root")):
        with pytest.raises(RuntimeMiddlewareFatalError, match="NOT_COMPLETED"):
            await invoke()
        child = next(item for item in store.list_items() if item.task_id != "root")
        assert child.status == "waiting" and child.wait_kind == "timer"
        with pytest.raises(XpertExecutionBudgetStateError, match="ALREADY_DISPATCHED"):
            await invoke()
    assert calls == []
    assert len(store.list_items()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["cancelled", "takeover"])
async def test_late_child_completion_cannot_return_to_invalid_parent(tmp_path, monkeypatch, fault):
    store, _, calls = root(tmp_path, monkeypatch)
    claimed = store.claim("root", worker_id="original")
    consume = main.consume_workflow_stream

    async def late(response):
        result = await consume(response)
        if fault == "cancelled":
            store.cancel("root")
        else:
            store.claim("root", worker_id="new", now=claimed.lease_expires_at + 1)
        return result

    monkeypatch.setattr(main, "consume_workflow_stream", late)
    with use_execution_budget(budget(store, "root", claimed.lease_token)):
        with pytest.raises(WorkflowExecutionConflictError):
            await invoke()
    assert calls == ["dispatched"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert store.require("root").status == ("cancelled" if fault == "cancelled" else "running")


@pytest.mark.asyncio
async def test_forged_delegation_metadata_cannot_remove_ordinary_tool_gate(tmp_path, monkeypatch):
    store, _, _ = root(tmp_path, monkeypatch, concurrency=1)
    entered, release, second_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    class Provider:
        async def call_tool(self, request):
            calls.append(request.tool_name)
            if len(calls) == 1:
                entered.set()
                await release.wait()
            else:
                second_entered.set()
            return RuntimeToolResult(output="合成结果")

    registry = CapabilityRegistry()
    registry.register("mcp_tools", Provider())

    async def run():
        request = RuntimeToolCall(tool_name="synthetic", arguments={}, metadata={
            "capability": "external_xpert_tools", "delegated": True,
        })
        with use_execution_budget(budget(store, "root")):
            return await run_tool_with_runtime(request, registry, MiddlewarePipeline(), MiddlewareContext())

    first = asyncio.create_task(run())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(run())
        await asyncio.sleep(0.05)
        assert not second_entered.is_set()
    finally:
        release.set()
        await asyncio.wait_for(first, 3)
        if second is not None:
            await asyncio.wait_for(second, 3)
    assert second_entered.is_set()
    assert store.require("root").execution_budget["usage"]["tool_calls"] == 2
