"""Durable continuation and real runner; vision admission is an offline substitute."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

import server.main as main
from server.xpert_runtime import RunRegistry, WorkflowExecutionStore
import server.xpert_runtime.execution_store as store_module
from server.xpert_runtime.execution_budget import (
    XpertExecutionBudgetExceeded,
    use_execution_budget,
    execution_operation,
)


USAGE = {"steps_used": 23, "model_calls": 3, "tool_calls": 2}
ABSENT = object()


def workflow(vision=False, waits=False):
    nodes = [{"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}}]
    if vision:
        nodes.append({"id": "vision", "type": "vision_understanding", "data": {
            "kind": "vision_understanding", "contractVersion": 2,
            "assetIdVariable": "selected_file_asset_id", "visionModelId": "offline/vision",
            "pdfPageStrategy": "all", "maxPages": 10, "maxImageEdge": 2048,
            "failurePolicy": "continue_on_error", "outputVariable": "vision_result",
        }})
    if waits:
        for name in ("one", "two"):
            nodes.append({"id": f"wait-{name}", "type": "suspend_wait", "data": {
                "kind": "suspend_wait", "waitMode": "duration", "durationSeconds": 1,
                "outputVariable": f"timer_{name}",
            }})
    nodes.append({"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "user_input"}})
    return {
        "id": "budget-restore", "title": "恢复预算验收", "nodes": nodes,
        "edges": [{"id": f"edge-{i}", "source": source["id"], "target": target["id"]}
                  for i, (source, target) in enumerate(zip(nodes, nodes[1:]))],
    }


def setup_resume(tmp_path, monkeypatch, usage=USAGE, *, vision=False, run_type="xpert", wait_kind="approval", waits=False):
    graph = workflow(vision, waits)
    store = WorkflowExecutionStore(tmp_path / "executions")
    metadata = {"xpert_agent_config": {
        "max_concurrency": 2, "recursion_limit": 1000,
        "max_model_calls": 3, "max_tool_calls": 2,
    }} if run_type in {"xpert", "xpert_app", "xpert_evaluation"} else {}
    inputs = {"user_input": "已保存结果", "selected_file_asset_id": "synthetic-asset"}
    store.create(task_id="task", run_id="original-run", run_type=run_type,
                 workflow=graph, inputs=inputs, runtime_metadata=metadata)
    next_node = "wait-one" if waits else "output"
    continuation = {"queue": [next_node], "queued": [next_node],
                    "executed": ["input", "vision"] if vision else ["input"],
                    "variables": inputs, "scheduler": {"version": 1}}
    if usage is not ABSENT:
        continuation["execution_budget"] = deepcopy(usage)
    store.suspend("task", wait_kind=wait_kind, wait_id="wait",
                  resume_at=0.0 if wait_kind in {"timer", "node_retry"} else None,
                  continuation=continuation)
    store.mark_ready("task", wait_kind=wait_kind, wait_id="wait")
    reopened = WorkflowExecutionStore(store.storage_dir)
    execution = reopened.claim("task", worker_id="restarted-worker", lease_seconds=60)
    registry = RunRegistry()
    monkeypatch.setattr(main, "workflow_execution_store", reopened)
    monkeypatch.setattr(main, "workflow_task_store", {})
    monkeypatch.setattr(main, "run_registry", registry)
    if vision:
        binding = {"model_id": "offline/vision"}
        monkeypatch.setattr(main, "validate_vision_v2_request", lambda *args, **kwargs: binding)
        monkeypatch.setattr(main, "workflow_vision_service", SimpleNamespace(
            managed_binding_snapshot=lambda *args: binding))
        monkeypatch.setattr(main, "resolve_workflow_vision_asset", lambda **kwargs: object())
        monkeypatch.setattr(main, "validate_vision_v2_asset", lambda *args: None)
        monkeypatch.setattr(main, "get_file_asset_service", lambda: object())

        async def available(*args):
            return True

        monkeypatch.setattr(main, "workflow_vision_model_available", available)
        async def forbidden_vision(**kwargs):
            pytest.fail("已完成视觉节点不得重新执行")
        monkeypatch.setattr(main, "execute_workflow_vision", forbidden_vision)
    return reopened, registry, execution, main.WorkflowRunRequest(workflow=graph, inputs=inputs)


async def response_for(execution, payload):
    return await main._run_workflow_response(
        payload, None, runtime_run_type=execution.run_type, resume_execution=execution,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("run_type,vision", [("xpert", False), ("xpert_app", False), ("xpert_evaluation", False), ("workflow", True)])
async def test_store_reload_preserves_budget_before_and_after_next_node(tmp_path, monkeypatch, run_type, vision):
    store, registry, execution, payload = setup_resume(tmp_path, monkeypatch, run_type=run_type, vision=vision)
    response = await response_for(execution, payload)
    budget = main.workflow_task_store["task"]["execution_budget"]
    assert budget.usage() == USAGE
    result = await main.consume_workflow_stream(response)
    assert result["event"] == "workflow_end"
    assert result["final_output"] == "已保存结果"
    assert budget.usage() == {**USAGE, "steps_used": 24}
    assert WorkflowExecutionStore(store.storage_dir).require("task").status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["model_call", "tool_call"])
async def test_restored_xpert_limit_rejects_before_operation_body(tmp_path, monkeypatch, kind):
    _, _, execution, payload = setup_resume(tmp_path, monkeypatch)
    await response_for(execution, payload)
    budget = main.workflow_task_store["task"]["execution_budget"]
    with use_execution_budget(budget), pytest.raises(XpertExecutionBudgetExceeded):
        async with execution_operation(kind):
            pytest.fail("耗尽预算不得进入请求体")
    assert budget.usage() == USAGE


@pytest.mark.asyncio
async def test_vision_fallback_exhaustion_survives_store_reload(tmp_path, monkeypatch):
    used = {**USAGE, "steps_used": 1000}
    store, _, execution, payload = setup_resume(tmp_path, monkeypatch, used, vision=True, run_type="workflow")
    response = await response_for(execution, payload)
    events = []
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        events.extend(json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith("data:"))
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert store.require("task").status == "failed"
    assert main.workflow_task_store["task"]["execution_budget"].usage() == used


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_kind", ["approval", "client_tool", "timer"])
@pytest.mark.parametrize("usage", [
    [], "PRIVATE_BUDGET_SENTINEL", {}, {"steps_used": 23},
    {**USAGE, "steps_used": -1}, {**USAGE, "model_calls": True},
    {**USAGE, "tool_calls": "2"}, {**USAGE, "tool_calls": 2.5},
    {**USAGE, "secret": "PRIVATE_BUDGET_SENTINEL"},
    {"steps_used": 3, "model_calls": 2, "tool_calls": 2},
])
async def test_corrupt_budget_rejected_before_run_side_effects(tmp_path, monkeypatch, wait_kind, usage):
    store, registry, execution, payload = setup_resume(tmp_path, monkeypatch, usage, wait_kind=wait_kind)
    before = store.snapshot_path.read_bytes()
    with pytest.raises(main.WorkflowTerminationError) as caught:
        await response_for(execution, payload)
    assert caught.value.code == "WORKFLOW_EXECUTION_BUDGET_INVALID"
    assert "PRIVATE_BUDGET_SENTINEL" not in str(caught.value)
    assert store.snapshot_path.read_bytes() == before
    assert await registry.list_runs() == []
    assert main.workflow_task_store == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [ABSENT, None])
@pytest.mark.parametrize("run_type,vision", [("xpert", False), ("workflow", True), ("workflow", False)])
async def test_legacy_missing_budget_remains_readable(tmp_path, monkeypatch, usage, run_type, vision):
    _, _, execution, payload = setup_resume(tmp_path, monkeypatch, usage, run_type=run_type, vision=vision)
    result = await main.consume_workflow_stream(await response_for(execution, payload))
    assert result["event"] == "workflow_end"
    assert result["final_output"] == "已保存结果"


@pytest.mark.asyncio
@pytest.mark.parametrize("run_type,vision", [("xpert", False), ("workflow", True)])
async def test_rewait_and_second_store_restart_never_reset_saved_usage(tmp_path, monkeypatch, run_type, vision):
    clock = {"now": time.time()}
    monkeypatch.setattr(main, "time", SimpleNamespace(
        time=lambda: clock["now"], monotonic=time.monotonic, perf_counter=time.perf_counter,
    ))
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock["now"]))
    store, _, execution, payload = setup_resume(tmp_path, monkeypatch, vision=vision, run_type=run_type, waits=True)
    first = await main.consume_workflow_stream(await response_for(execution, payload))
    assert first["event"] == "timer_waiting"
    waiting = store.require("task")
    assert waiting.continuation["execution_budget"] == {**USAGE, "steps_used": 24}
    for index in range(2):
        clock["now"] = waiting.resume_at + 0.1
        store = WorkflowExecutionStore(store.storage_dir)
        monkeypatch.setattr(main, "workflow_execution_store", store)
        monkeypatch.setattr(main, "workflow_task_store", {})
        result = await main.resume_runtime_due_execution("task")
        waiting = store.require("task")
        if index == 0:
            assert result["event"] == "timer_waiting"
            assert waiting.wait_id.endswith(":wait-two")
            assert waiting.continuation["execution_budget"] == {**USAGE, "steps_used": 26}
        else:
            assert result["event"] == "workflow_end"
            assert waiting.status == "completed"
            assert main.workflow_task_store["task"]["execution_budget"].usage() == {**USAGE, "steps_used": 28}


@pytest.mark.asyncio
async def test_due_resume_commits_safe_failure_for_corrupt_budget(tmp_path, monkeypatch):
    store, _, execution, _ = setup_resume(tmp_path, monkeypatch, "PRIVATE_BUDGET_SENTINEL", wait_kind="timer")
    store.release_ready("task", expected_lease_token=execution.lease_token)
    result = await main.resume_runtime_due_execution("task")
    assert result["event"] == "error"
    assert result["code"] == "WORKFLOW_EXECUTION_BUDGET_INVALID"
    saved = WorkflowExecutionStore(store.storage_dir).require("task")
    assert saved.status == "failed"
    assert "PRIVATE_BUDGET_SENTINEL" not in json.dumps({"events": saved.events, "error": saved.error})


@pytest.mark.asyncio
@pytest.mark.parametrize("usage,valid", [(USAGE, True), ({**USAGE, "model_calls": True}, False), ({"steps_used": 3, "model_calls": 2, "tool_calls": 2}, False)])
async def test_retry_budget_validation_preserves_existing_safe_error(tmp_path, monkeypatch, usage, valid):
    from server.tests.test_workflow_retry_security_regressions import _retry_workflow
    from server.workflow_native.secure_http import WorkflowHttpRequestError

    clock = {"now": time.time()}
    monkeypatch.setattr(main, "time", SimpleNamespace(
        time=lambda: clock["now"], monotonic=time.monotonic, perf_counter=time.perf_counter,
    ))
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock["now"]))
    calls = []
    async def transient_http(*args, **kwargs):
        calls.append(True)
        raise WorkflowHttpRequestError("HTTP_TIMEOUT", "合成超时")
    monkeypatch.setattr(main, "execute_workflow_http_request", transient_http)
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main, "workflow_execution_store", store)
    monkeypatch.setattr(main, "run_registry", RunRegistry())
    monkeypatch.setattr(main, "workflow_task_store", {})
    payload = main.WorkflowRunRequest(workflow=_retry_workflow(), inputs={"user_input": "合成输入"})
    response = await main._run_workflow_response(
        payload, None, runtime_task_id="retry-budget", runtime_run_type="xpert",
        runtime_metadata={"xpert_agent_config": {"max_concurrency": 2, "recursion_limit": 1000}},
    )
    first = await main.consume_workflow_stream(response)
    assert first["event"] == "node_retry_scheduled"
    assert len(calls) == 1
    saved = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    item = saved["items"][0]
    saved_usage = item["continuation"]["execution_budget"]
    item["continuation"]["execution_budget"] = saved_usage if valid else usage
    store.snapshot_path.write_text(json.dumps(saved), encoding="utf-8")
    store = WorkflowExecutionStore(store.storage_dir)
    monkeypatch.setattr(main, "workflow_execution_store", store)
    clock["now"] = store.require("retry-budget").resume_at + 0.1
    result = await main.resume_runtime_due_execution("retry-budget")
    if valid:
        assert result["event"] == "node_retry_scheduled"
        assert len(calls) == 2
        assert store.require("retry-budget").continuation["execution_budget"] == {**saved_usage, "steps_used": saved_usage["steps_used"] + 1}
    else:
        assert result["event"] == "error"
        assert result["code"] == "NODE_RETRY_STATE_INVALID"
        assert store.require("retry-budget").status == "failed"
        assert len(calls) == 1
