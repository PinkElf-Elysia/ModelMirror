"""Characterize real resume callbacks without starting server.main singletons.

Only the runner and payload construction boundaries are substituted. Callback
ASTs and the SSE consumer execute unchanged with real temporary Stores. This
does not replace A2's full RunRegistry/Automation/runner integration gate.
"""
from __future__ import annotations

import ast
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.responses import JSONResponse, StreamingResponse

from server.xpert_runtime import AgentTaskStore, RuntimeEventStore, WorkflowExecutionStore
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
from server.xpert_runtime.workflow_outcomes import WorkflowOutcomeAccumulator, resolve_workflow_outcome


WAIT_EVENTS = {
    "runtime_approval_pending": "approval",
    "client_tool_waiting": "client_tool",
    "timer_waiting": "timer",
    "node_retry_scheduled": "node_retry",
    "agent_handoff_waiting": "agent_handoff",
}


@pytest.fixture(scope="module")
def callback_code():
    path = Path(__file__).resolve().parents[1] / "main.py"
    names = {
        "WorkflowStreamFailure", "consume_workflow_stream",
        "resume_runtime_approval_execution", "resume_runtime_client_tool_execution",
        "resume_workflow_handoff_execution",
    }
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    definitions = [
        node for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert {node.name for node in definitions} == names
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *definitions],
        type_ignores=[],
    )
    return compile(ast.fix_missing_locations(module), str(path), "exec")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["approval", "client_tool"])
@pytest.mark.parametrize("event_name", [*WAIT_EVENTS, "workflow_cancelled", "workflow_end"])
async def test_resume_does_not_project_non_success_as_parent_success(tmp_path, callback_code, entry, event_name):
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(storage_dir=tmp_path / "executions")
    task = await tasks.create_task("synthetic", "synthetic")
    handoff = await tasks.create_handoff(
        task.task_id, source_agent="parent", target_agent="child", reason="synthetic",
    )
    await tasks.update_handoff_status(handoff.handoff_id, "accepted")
    parent_wait = "waiting_approval" if entry == "approval" else "waiting_client"
    await tasks.update_handoff_status(handoff.handoff_id, parent_wait)
    await tasks.update_task(task.task_id, status=parent_wait)
    executions.create(
        task_id="execution", run_id="run", run_type="workflow",
        workflow={"id": "workflow", "nodes": [], "edges": []}, inputs={},
        runtime_metadata={"handoff_id": handoff.handoff_id, "agent_task_id": task.task_id},
    )
    execution = executions.claim("execution", worker_id="owner")
    event = {"event": event_name, "task_id": "execution", "run_id": "run"}
    if event_name in WAIT_EVENTS:
        event.update(approval_id="next", request_id="next", wait_id="next")
    elif event_name == "workflow_end":
        event["final_output"] = "synthetic-result"

    async def run_boundary(*_args, **_kwargs):
        if event_name in WAIT_EVENTS:
            executions.suspend(
                "execution", wait_kind=WAIT_EVENTS[event_name], wait_id="next",
                resume_at=time.time() + 60, continuation={"variables": {"count": 2}},
                expected_lease_token=execution.lease_token,
            )
        elif event_name == "workflow_cancelled":
            executions.cancel("execution")
        else:
            executions.complete("execution", result="synthetic-result", expected_lease_token=execution.lease_token)

        async def frames():
            # Split a real SSE frame to also exercise the production consumer.
            frame = ("data: " + json.dumps(event) + "\n\n").encode()
            yield frame[:9]
            yield frame[9:]

        return StreamingResponse(frames())

    run_registry = SimpleNamespace(list_runs=AsyncMock(return_value=[]))
    namespace = {
        "JSONResponse": JSONResponse, "StreamingResponse": StreamingResponse,
        "json": json, "time": time,
        "WorkflowPayload": SimpleNamespace(model_validate=lambda _value: SimpleNamespace(id="workflow")),
        "WorkflowRunRequest": lambda **kwargs: kwargs,
        "_run_workflow_response": run_boundary, "agent_task_store": tasks,
        "run_registry": run_registry, "workflow_execution_store": executions,
        "WorkflowOutcomeAccumulator": WorkflowOutcomeAccumulator,
        "resolve_workflow_outcome": resolve_workflow_outcome,
    }
    exec(callback_code, namespace)
    if entry == "approval":
        await namespace["resume_runtime_approval_execution"](execution, SimpleNamespace(approval_id="original"))
    else:
        await namespace["resume_runtime_client_tool_execution"](
            execution, SimpleNamespace(request_id="original", tool_name="synthetic", status="completed"),
        )

    child = executions.require("execution")
    parent = await tasks.get_handoff(handoff.handoff_id)
    parent_task = await tasks.get_task(task.task_id)
    if event_name == "workflow_end":
        assert child.status == parent.status == parent_task.status == "completed"
        assert parent_task.result == "synthetic-result"
    else:
        assert child.status == ("cancelled" if event_name == "workflow_cancelled" else "waiting")
        assert parent.status != "completed", f"{entry} incorrectly completed parent after {event_name}"
        assert parent_task.status != "completed"
        if event_name in WAIT_EVENTS:
            assert child.wait_id == "next"
            assert child.continuation["variables"] == {"count": 2}
        if event_name in {"runtime_approval_pending", "client_tool_waiting"}:
            expected = "waiting_approval" if event_name == "runtime_approval_pending" else "waiting_client"
            assert parent.status == parent_task.status == expected
            assert parent.metadata["workflow_projection"]["wait_id"] == "next"
        elif event_name == "workflow_cancelled":
            assert parent_task.status == "cancelled"
            assert parent.status == "dead_letter"
            assert parent.metadata["last_error"] == "WORKFLOW_CANCELLED"
        run_registry.list_runs.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("event_name", [*WAIT_EVENTS, "workflow_cancelled", "workflow_end"])
async def test_handoff_resume_projects_declared_durable_outcome(tmp_path, callback_code, event_name):
    executions = WorkflowExecutionStore(tmp_path / "executions")
    executions.create(
        task_id="execution", run_id="run", run_type="workflow", workflow={}, inputs={},
        runtime_metadata={"workflow_deployment_execution_id": "deployment"},
    )
    execution = executions.claim("execution", worker_id="owner")
    execution.continuation = {"agent_state": {"handoff_id": "child"}}
    event = {"event": event_name, "task_id": "execution", "run_id": "run"}

    async def run_boundary(*_args, **_kwargs):
        if event_name in WAIT_EVENTS:
            event.update(approval_id="next", request_id="next", wait_id="next")
            executions.suspend(
                "execution", wait_kind=WAIT_EVENTS[event_name], wait_id="next",
                continuation={"queue": ["next"]}, resume_at=time.time() + 60,
                expected_lease_token=execution.lease_token,
            )
        elif event_name == "workflow_cancelled":
            executions.cancel("execution")
        else:
            event["final_output"] = "result"
            executions.complete("execution", result="result", expected_lease_token=execution.lease_token)
        async def frames():
            yield "data: " + json.dumps(event) + "\n\n"
        return StreamingResponse(frames())

    deployment = SimpleNamespace(
        mark_execution_waiting=Mock(), complete_execution=Mock(),
        cancel_execution=Mock(), fail_execution=Mock(),
    )
    namespace = {
        "JSONResponse": JSONResponse, "StreamingResponse": StreamingResponse, "json": json,
        "WorkflowPayload": SimpleNamespace(model_validate=lambda _: SimpleNamespace(id="workflow")),
        "WorkflowRunRequest": lambda **kwargs: kwargs,
        "_run_workflow_response": run_boundary, "workflow_execution_store": executions,
        "workflow_deployment_store": deployment,
        "WorkflowOutcomeAccumulator": WorkflowOutcomeAccumulator,
        "resolve_workflow_outcome": resolve_workflow_outcome,
    }
    exec(callback_code, namespace)
    await namespace["resume_workflow_handoff_execution"](
        execution, SimpleNamespace(handoff_id="child", status="completed"),
    )
    if event_name in WAIT_EVENTS:
        deployment.mark_execution_waiting.assert_called_once()
        assert deployment.mark_execution_waiting.call_args.kwargs["wait_kind"] == WAIT_EVENTS[event_name]
        deployment.complete_execution.assert_not_called()
        deployment.cancel_execution.assert_not_called()
    elif event_name == "workflow_cancelled":
        deployment.cancel_execution.assert_called_once()
        deployment.complete_execution.assert_not_called()
    else:
        deployment.complete_execution.assert_called_once()
    deployment.fail_execution.assert_not_called()


async def projection_pair(tmp_path, decision="client_tool_waiting", *, source_task=None):
    events = RuntimeEventStore()
    tasks = AgentTaskStore(event_store=events, storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    task = await tasks.create_task("synthetic", "synthetic")
    handoff = await tasks.create_handoff(task.task_id, "parent", "child", "synthetic")
    await tasks.update_handoff_status(handoff.handoff_id, "accepted")
    await tasks.update_handoff_status(handoff.handoff_id, "waiting_approval")
    await tasks.update_task(task.task_id, status="waiting_approval")
    executions.create(
        task_id="child", run_id="run", run_type="workflow", workflow={}, inputs={},
        runtime_metadata={"handoff_id": handoff.handoff_id, "agent_task_id": source_task or task.task_id},
    )
    claim = executions.claim("child", worker_id="owner")
    event = {"event": decision, "task_id": "child", "run_id": "run"}
    if decision in WAIT_EVENTS:
        event.update(approval_id="next", request_id="next", wait_id="next")
        executions.suspend(
            "child", wait_kind=WAIT_EVENTS[decision], wait_id="next",
            continuation={"queue": ["next"]}, resume_at=time.time() + 60,
            expected_lease_token=claim.lease_token,
        )
    elif decision == "workflow_cancelled":
        executions.cancel("child")
    else:
        event["final_output"] = "untrusted-event-result"
        executions.complete("child", result="durable-result", expected_lease_token=claim.lease_token)
    return SimpleNamespace(
        tasks=tasks, executions=executions, task=task, handoff=handoff,
        events=events, event=event, snapshot=executions.snapshot("child"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["runtime_approval_pending", "client_tool_waiting", "workflow_cancelled", "workflow_end"])
async def test_parent_pair_projection_is_one_commit_and_replayable(tmp_path, monkeypatch, decision):
    ctx = await projection_pair(tmp_path, decision)
    persist = Mock(wraps=ctx.tasks._persist_unlocked)
    monkeypatch.setattr(ctx.tasks, "_persist_unlocked", persist)
    assert await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    assert persist.call_count == 1
    disk = ctx.tasks.storage_path.read_bytes()
    count = len(await ctx.events.list_events(ctx.task.task_id))
    reloaded = AgentTaskStore(event_store=ctx.events, storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(tmp_path / "executions")
    assert await reloaded.project_workflow_outcome(executions, executions.snapshot("child"), ctx.event)
    assert reloaded.storage_path.read_bytes() == disk
    assert len(await ctx.events.list_events(ctx.task.task_id)) == count
    task = await reloaded.get_task(ctx.task.task_id)
    handoff = await reloaded.get_handoff(ctx.handoff.handoff_id)
    assert task.metadata["workflow_projection"] == handoff.metadata["workflow_projection"]
    if decision == "workflow_end":
        assert task.result == handoff.metadata["result"] == "durable-result"
    safe_events = json.dumps([asdict(event) for event in await ctx.events.list_events(ctx.task.task_id)])
    assert "durable-result" not in safe_events
    assert "untrusted-event-result" not in safe_events


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["ready", "reclaimed", "cancelled", "next_wait"])
async def test_projection_checks_source_after_waiting_for_parent_lock(tmp_path, replacement):
    ctx = await projection_pair(tmp_path)
    disk = ctx.tasks.storage_path.read_bytes()
    async with ctx.tasks._changed:
        pending = asyncio.create_task(ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event))
        await asyncio.sleep(0)
        if replacement == "cancelled":
            ctx.executions.cancel("child")
        else:
            ctx.executions.mark_ready("child", wait_kind="client_tool", wait_id="next")
            if replacement != "ready":
                claim = ctx.executions.claim("child", worker_id="replacement")
                if replacement == "next_wait":
                    ctx.executions.suspend(
                        "child", wait_kind="approval", wait_id="later",
                        continuation={"queue": ["next"]}, expected_lease_token=claim.lease_token,
                    )
    with pytest.raises(WorkflowExecutionConflictError):
        await pending
    assert ctx.tasks.storage_path.read_bytes() == disk
    assert (await ctx.tasks.get_task(ctx.task.task_id)).status == "waiting_approval"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["task_binding", "handoff_binding", "run_binding", "requeue"])
async def test_parent_projection_rejects_wrong_or_requeued_target(tmp_path, boundary):
    ctx = await projection_pair(tmp_path, source_task="wrong-task" if boundary == "task_binding" else None)
    if boundary == "handoff_binding":
        await ctx.tasks.update_task(ctx.task.task_id, metadata={"handoff_id": "other"})
    elif boundary == "run_binding":
        await ctx.tasks.update_handoff_metadata(ctx.handoff.handoff_id, {"xpert_run_id": "other"})
    elif boundary == "requeue":
        await ctx.tasks.update_handoff_status(ctx.handoff.handoff_id, "dead_letter")
        await ctx.tasks.update_task(ctx.task.task_id, status="failed")
        await ctx.tasks.requeue_handoff(ctx.handoff.handoff_id, operator="operator")
        await ctx.tasks.update_handoff_status(ctx.handoff.handoff_id, "accepted")
        await ctx.tasks.update_task(ctx.task.task_id, status="running")
    disk = ctx.tasks.storage_path.read_bytes()
    if boundary in {"task_binding", "handoff_binding"}:
        with pytest.raises(ValueError, match="绑定不一致"):
            await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    else:
        assert not await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    assert ctx.tasks.storage_path.read_bytes() == disk


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", ["completed", "failed", "cancelled", "handoff_completed", "handoff_dead_letter"])
@pytest.mark.parametrize("decision", ["client_tool_waiting", "workflow_cancelled", "workflow_end"])
async def test_projection_does_not_overwrite_parent_terminal_decisions(tmp_path, parent, decision):
    ctx = await projection_pair(tmp_path, decision)
    if parent.startswith("handoff_"):
        await ctx.tasks.update_handoff_status(ctx.handoff.handoff_id, parent.removeprefix("handoff_"))
    else:
        await ctx.tasks.update_task(ctx.task.task_id, status=parent, result="original")
    before = ctx.tasks.storage_path.read_bytes()
    assert not await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    assert ctx.tasks.storage_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["client_tool_waiting", "workflow_cancelled", "workflow_end"])
async def test_parent_projection_replace_failure_leaves_pair_and_audit_unchanged(tmp_path, monkeypatch, decision):
    ctx = await projection_pair(tmp_path, decision)
    before = ctx.tasks.storage_path.read_bytes()
    task, handoff = asdict(await ctx.tasks.get_task(ctx.task.task_id)), asdict(await ctx.tasks.get_handoff(ctx.handoff.handoff_id))
    count = len(await ctx.events.list_events(ctx.task.task_id))
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", Mock(side_effect=OSError("synthetic replace failure")))
        with pytest.raises(OSError):
            await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    assert ctx.tasks.storage_path.read_bytes() == before
    assert asdict(await ctx.tasks.get_task(ctx.task.task_id)) == task
    assert asdict(await ctx.tasks.get_handoff(ctx.handoff.handoff_id)) == handoff
    assert len(await ctx.events.list_events(ctx.task.task_id)) == count
    assert await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)


@pytest.mark.asyncio
async def test_committed_pair_survives_interruption_before_audit(tmp_path, monkeypatch):
    ctx = await projection_pair(tmp_path, "workflow_end")
    monkeypatch.setattr(ctx.tasks, "_record", AsyncMock(side_effect=RuntimeError("synthetic crash")))
    with pytest.raises(RuntimeError):
        await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    reloaded = AgentTaskStore(storage_dir=tmp_path / "tasks")
    disk = reloaded.storage_path.read_bytes()
    assert (await reloaded.get_task(ctx.task.task_id)).status == "completed"
    assert (await reloaded.get_handoff(ctx.handoff.handoff_id)).status == "completed"
    assert await reloaded.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    assert reloaded.storage_path.read_bytes() == disk


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["timer_waiting", "node_retry_scheduled", "agent_handoff_waiting"])
async def test_unopened_parent_wait_projection_is_not_silently_remapped(tmp_path, decision):
    ctx = await projection_pair(tmp_path, decision)
    before = ctx.tasks.storage_path.read_bytes()
    assert not await ctx.tasks.project_workflow_outcome(ctx.executions, ctx.snapshot, ctx.event)
    assert ctx.tasks.storage_path.read_bytes() == before
