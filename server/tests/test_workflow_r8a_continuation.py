from __future__ import annotations

from copy import deepcopy

import pytest

import server.main as main_module
from server.xpert_runtime import RunRegistry, WorkflowExecutionStore


def workflow():
    return {
        "id": "continuation-check", "title": "恢复队列验收",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
            {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "user_input"}},
        ],
        "edges": [{"id": "edge", "source": "input", "target": "output"}],
    }


def setup_resume(tmp_path, monkeypatch, continuation):
    store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    store.create(task_id="task", run_id="run", run_type="workflow", workflow=workflow(), inputs={"user_input": "synthetic"})
    store.suspend("task", wait_kind="approval", wait_id="approval", continuation=continuation)
    store.mark_ready("task", wait_kind="approval", wait_id="approval")
    execution = store.claim("task", worker_id="owner", lease_seconds=60)
    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    return store, registry, execution


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation", [
    {}, {"variables": {"user_input": "synthetic"}}, {"queue": []},
    {"queue": "input"}, {"queue": ["missing"]}, {"queue": [True]},
    {"queue": ["input", "input"]}, {"queue": ["input"], "executed": ["input"]},
    {"queue": ["input"], "executed": "output"},
    {"queue": ["input"], "queued": ["missing"]},
])
async def test_invalid_resume_cannot_start_or_commit_success(tmp_path, monkeypatch, continuation):
    store, registry, execution = setup_resume(tmp_path, monkeypatch, deepcopy(continuation))
    before = store.snapshot_path.read_bytes()
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    with pytest.raises(main_module.WorkflowTerminationError) as caught:
        response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
        await main_module.consume_workflow_stream(response)
    assert caught.value.code == "WORKFLOW_CONTINUATION_INVALID"
    assert store.snapshot_path.read_bytes() == before
    assert await registry.list_runs() == []
    assert store.require("task").status == "running"
    assert not store.require("task").events


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduler", [None, {"version": 1}])
async def test_legacy_queue_executes_instead_of_empty_success(tmp_path, monkeypatch, scheduler):
    continuation = {"queue": ["output"], "executed": ["input"], "variables": {"user_input": "preserved"}}
    if scheduler is not None:
        continuation["scheduler"] = scheduler
    store, registry, execution = setup_resume(tmp_path, monkeypatch, continuation)
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "fresh-input-must-not-win"})
    response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
    result = await main_module.consume_workflow_stream(response)
    assert result["event"] == "workflow_end"
    assert result["final_output"] == "preserved"
    saved = WorkflowExecutionStore(store.storage_dir).require("task")
    assert saved.status == "completed"
    assert sum(item["event"] == "workflow_end" for item in saved.events) == 1
    assert (await registry.get_run(saved.run_id)).status == "completed"
