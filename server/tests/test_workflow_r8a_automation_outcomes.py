"""Verified Automation child outcomes; synthetic runner, real durable Stores."""
from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from starlette.responses import StreamingResponse

import server.main as main
from server.xpert_runtime import AutomationStore, RunRegistry, WorkflowExecutionStore
from server.xpert_runtime.automation_store import AutomationConflictError
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError


EVENTS = {"approval": "runtime_approval_pending", "client_tool": "client_tool_waiting", "timer": "timer_waiting", "node_retry": "node_retry_scheduled", "agent_handoff": "agent_handoff_waiting", "completed": "workflow_end", "cancelled": "workflow_cancelled"}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    parents = AutomationStore(tmp_path / "parents")
    children = WorkflowExecutionStore(tmp_path / "children")
    registry = RunRegistry()
    monkeypatch.setattr(main, "automation_store", parents)
    monkeypatch.setattr(main, "workflow_execution_store", children)
    monkeypatch.setattr(main, "run_registry", registry)
    monkeypatch.setattr(main, "automation_coordinator", None)
    monkeypatch.setattr(main, "workflow_automation_provider", None)
    monkeypatch.setattr(main, "AUTOMATION_COORDINATOR_ENABLED", True)
    definition = parents.create_definition(name="结果投影验收", prompt="synthetic", target_xpert_id="xpert", target_xpert_slug="xpert", target_xpert_version=1, trigger={"type": "interval", "interval_seconds": 60})
    execution = parents.run_now(definition.automation_id)
    return parents, children, registry, execution


def settle(children, task_id, kind, token=None):
    lease = {"expected_lease_token": token} if token else {}
    if kind == "completed":
        children.complete(task_id, result="durable result", **lease)
    elif kind == "cancelled":
        children.cancel(task_id)
    else:
        children.suspend(task_id, wait_kind=kind, wait_id="next", continuation={"queue": ["output"]}, resume_at=9999999999 if kind in {"timer", "node_retry"} else None, **lease)
    saved = children.require(task_id)
    return {"event": EVENTS[kind], "task_id": task_id, "run_id": saved.run_id, "approval_id": "next", "request_id": "next", "wait_id": "next", "final_output": "untrusted event text"}


def stream(event):
    async def frames():
        yield "data: " + json.dumps(event) + "\n\n"
    return StreamingResponse(frames(), headers={"X-ModelMirror-Runtime-Task-Id": event["task_id"], "X-ModelMirror-Runtime-Run-Id": event["run_id"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(EVENTS))
async def test_first_execution_uses_durable_outcome(runtime, monkeypatch, kind):
    parents, children, registry, execution = runtime

    async def prepare(*args, **kwargs):
        return SimpleNamespace(request=None, xpert=SimpleNamespace(id="xpert"), runtime_metadata={})

    async def run(*args, **kwargs):
        children.create(task_id="child", run_id="child-run", run_type="xpert", workflow={}, inputs={}, runtime_metadata=kwargs["runtime_metadata"])
        return stream(settle(children, "child", kind))

    monkeypatch.setattr(main, "prepare_published_xpert_run", prepare)
    monkeypatch.setattr(main, "_run_workflow_response", run)
    await main.get_automation_coordinator().run_once()
    saved = parents.get_execution(execution.execution_id)
    expected = {"approval": "waiting_approval", "client_tool": "waiting_client", "completed": "completed", "cancelled": "cancelled"}.get(kind, "dead_letter")
    assert saved.status == expected
    assert saved.workflow_task_id == "child"
    if kind == "completed":
        assert saved.result == "durable result"
    if kind in {"timer", "node_retry", "agent_handoff"}:
        assert saved.error == "AUTOMATION_WAIT_UNSUPPORTED"
        assert parents.claimable() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["approval", "client_tool"])
@pytest.mark.parametrize("kind", list(EVENTS))
async def test_resume_rebinds_only_verified_child_run(runtime, monkeypatch, entry, kind):
    parents, children, registry, execution = runtime
    claimed_parent = parents.claim_execution(execution.execution_id, worker_id="parent")
    metadata = {"automation_id": execution.automation_id, "automation_execution_id": execution.execution_id, "automation_occurrence_key": execution.occurrence_key}
    children.create(task_id="child", run_id="old-run", run_type="xpert", workflow={"id": "workflow", "nodes": [{"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "user_input"}}], "edges": []}, inputs={}, runtime_metadata=metadata)
    children.suspend("child", wait_kind=entry, wait_id="original", continuation={"queue": []})
    parents.mark_waiting(execution.execution_id, status="waiting_approval" if entry == "approval" else "waiting_client", run_id="old-run", workflow_task_id="child", wait_id="original", lease_token=claimed_parent.lease_token)
    parent_run = await registry.create_run("automation", "父执行", status="waiting", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id, "attempt": claimed_parent.attempt})
    children.mark_ready("child", wait_kind=entry, wait_id="original")
    claimed = children.claim("child", worker_id="resume")

    async def run(*args, **kwargs):
        children.update_run_id("child", run_id="new-run", expected_lease_token=claimed.lease_token)
        return stream(settle(children, "child", kind, claimed.lease_token))

    monkeypatch.setattr(main, "_run_workflow_response", run)
    if entry == "approval":
        await main.resume_runtime_approval_execution(claimed, SimpleNamespace(approval_id="original"))
    else:
        await main.resume_runtime_client_tool_execution(claimed, SimpleNamespace(request_id="original", tool_name="synthetic", status="completed"))
    saved = parents.get_execution(execution.execution_id)
    expected = {"approval": "waiting_approval", "client_tool": "waiting_client", "completed": "completed", "cancelled": "cancelled"}.get(kind, "dead_letter")
    assert saved.status == expected and saved.run_id == "new-run"
    assert (await registry.get_run(parent_run.run_id)).status == ("waiting" if expected.startswith("waiting") else "failed" if expected == "dead_letter" else expected)


def projection_fixture(runtime):
    parents, children, _, execution = runtime
    claimed = parents.claim_execution(execution.execution_id, worker_id="parent")
    metadata = {"automation_id": execution.automation_id, "automation_execution_id": execution.execution_id, "automation_occurrence_key": execution.occurrence_key}
    children.create(task_id="child", run_id="child-run", run_type="xpert", workflow={}, inputs={}, runtime_metadata=metadata)
    event = settle(children, "child", "client_tool")
    return claimed, children.require("child"), event


def test_projection_persistence_failure_rolls_back(runtime, monkeypatch):
    parents, children, _, execution = runtime
    claimed, snapshot, event = projection_fixture(runtime)
    before = asdict(parents.get_execution(execution.execution_id))

    def failed():
        raise OSError("synthetic persistence failure")

    monkeypatch.setattr(parents, "_persist_unlocked", failed)
    with pytest.raises(OSError):
        parents.project_workflow_outcome(children, snapshot, event, lease_token=claimed.lease_token)
    assert asdict(parents.get_execution(execution.execution_id)) == before
    assert asdict(AutomationStore(parents.storage_dir).get_execution(execution.execution_id)) == before


def test_projection_duplicate_is_idempotent_but_cancel_is_not_reopened(runtime):
    parents, children, _, execution = runtime
    claimed, snapshot, event = projection_fixture(runtime)
    projected = parents.project_workflow_outcome(children, snapshot, event, lease_token=claimed.lease_token)
    assert asdict(parents.project_workflow_outcome(children, snapshot, event)) == asdict(projected)
    parents.cancel_execution(execution.execution_id)
    with pytest.raises(AutomationConflictError):
        parents.project_workflow_outcome(children, snapshot, event)
    assert parents.get_execution(execution.execution_id).status == "cancelled"


def test_stale_child_snapshot_rejected(runtime):
    parents, children, _, execution = runtime
    claimed, snapshot, event = projection_fixture(runtime)
    children.cancel("child")
    with pytest.raises(WorkflowExecutionConflictError):
        parents.project_workflow_outcome(children, snapshot, event, lease_token=claimed.lease_token)
    assert parents.get_execution(execution.execution_id).status == "running"


@pytest.mark.asyncio
async def test_registry_projection_reconciles_from_reloaded_parent_without_dispatch(runtime, monkeypatch):
    parents, children, registry, execution = runtime
    claimed, snapshot, event = projection_fixture(runtime)
    parents.project_workflow_outcome(children, snapshot, event, lease_token=claimed.lease_token)
    parent_run = await registry.create_run("automation", "父执行", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id, "attempt": claimed.attempt})
    monkeypatch.setattr(main, "automation_store", AutomationStore(parents.storage_dir))
    worker = main.get_automation_coordinator()

    async def forbidden(*args):
        pytest.fail("reconciliation must not dispatch")

    worker.execute_target = forbidden
    assert await worker.run_once() == 0
    assert (await registry.get_run(parent_run.run_id)).status == "waiting"
    first = await registry.list_checkpoints(parent_run.run_id)
    assert await worker.run_once() == 0
    assert len(await registry.list_checkpoints(parent_run.run_id)) == len(first)


@pytest.mark.asyncio
@pytest.mark.parametrize("forgery", ["missing", "run_id", "occurrence"])
async def test_invalid_initial_outcome_is_not_retried(runtime, monkeypatch, forgery):
    parents, children, _, execution = runtime
    from server.xpert_runtime import AutomationTargetResult

    async def target(definition, claimed, run_id):
        metadata = {"automation_id": execution.automation_id, "automation_execution_id": execution.execution_id, "automation_occurrence_key": "wrong" if forgery == "occurrence" else execution.occurrence_key}
        children.create(task_id="child", run_id="child-run", run_type="xpert", workflow={}, inputs={}, runtime_metadata=metadata)
        event = settle(children, "child", "completed")
        if forgery == "run_id":
            event["run_id"] = "forged"
        return AutomationTargetResult("forged text", "child-run", "missing" if forgery == "missing" else "child", workflow_event=event)

    worker = main.get_automation_coordinator()
    worker.execute_target = target
    assert await worker.run_once() == 0
    assert parents.get_execution(execution.execution_id).status == "dead_letter"
    assert parents.claimable() == []


def test_unrelated_child_run_cannot_rebind_parent(runtime):
    parents, children, _, execution = runtime
    claimed, snapshot, event = projection_fixture(runtime)
    parents.mark_waiting(execution.execution_id, status="waiting_approval", run_id="unrelated-run", workflow_task_id="child", wait_id="original", lease_token=claimed.lease_token)
    with pytest.raises(AutomationConflictError):
        parents.project_workflow_outcome(children, snapshot, event)
    assert parents.get_execution(execution.execution_id).run_id == "unrelated-run"


@pytest.mark.asyncio
async def test_unsupported_wait_parent_blocks_child_resume(runtime):
    parents, children, _, execution = runtime
    claimed, snapshot, _ = projection_fixture(runtime)
    children.mark_ready("child", wait_kind="client_tool", wait_id="next")
    owner = children.claim("child", worker_id="child")
    event = settle(children, "child", "timer", owner.lease_token)
    snapshot = children.require("child")
    parents.project_workflow_outcome(children, snapshot, event, lease_token=claimed.lease_token)
    with pytest.raises(WorkflowExecutionConflictError):
        await main._assert_automation_parent_not_cancelled(snapshot.runtime_metadata)


@pytest.mark.asyncio
async def test_registry_failure_after_commit_replays_without_model(runtime, monkeypatch):
    parents, children, registry, execution = runtime
    claimed, snapshot, event = projection_fixture(runtime)
    parents.project_workflow_outcome(children, snapshot, event, lease_token=claimed.lease_token)
    run = await registry.create_run("automation", "父执行", status="running", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id, "attempt": claimed.attempt})
    worker = main.get_automation_coordinator()
    original = registry.update_run

    async def failed(*args, **kwargs):
        raise OSError("synthetic registry failure")

    monkeypatch.setattr(registry, "update_run", failed)
    with pytest.raises(OSError):
        await worker.project_workflow_outcome(children, snapshot, event)
    assert parents.get_execution(execution.execution_id).status == "waiting_client"
    monkeypatch.setattr(registry, "update_run", original)
    assert await worker.run_once() == 0
    assert (await registry.get_run(run.run_id)).status == "waiting"
