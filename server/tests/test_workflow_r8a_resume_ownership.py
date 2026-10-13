"""A1 entry ownership checks using production callbacks and isolated Stores.

Runner boundaries in callback tests are explicit substitutes, not Provider or
deployment integration evidence. The runner entry tests call the real runner.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.responses import StreamingResponse

import server.main as main_module
from server.xpert_runtime import RunRegistry, WorkflowExecutionStore
from server.xpert_runtime import execution_store as store_module
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError, WorkflowExecutionStorageError


def workflow():
    return {
        "id": "ownership-check", "title": "恢复所有权核对",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
            {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "user_input"}},
        ],
        "edges": [{"id": "edge", "source": "input", "target": "output"}],
    }


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock.now))
    store = WorkflowExecutionStore(tmp_path / "executions")
    store.create(
        task_id="task", run_id="run", run_type="workflow", workflow=workflow(), inputs={},
        runtime_metadata={"workflow_deployment_execution_id": "deployment"},
    )
    store.suspend(
        "task", wait_kind="approval", wait_id="resume-fixture",
        continuation={"queue": ["input"], "queued": ["input"], "executed": []},
    )
    store.mark_ready("task", wait_kind="approval", wait_id="resume-fixture")
    registry = RunRegistry()
    deployment = SimpleNamespace(fail_execution=Mock(), complete_execution=Mock(), mark_execution_waiting=Mock())
    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment)
    return store, registry, deployment, clock


def claim(store):
    item = store.claim("task", worker_id="old", lease_seconds=5)
    item.continuation = {"agent_state": {"handoff_id": "handoff"}}
    return item


async def invoke(entry, execution):
    if entry == "runner":
        payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
        return await main_module._run_workflow_response(payload, None, resume_execution=execution)
    if entry == "approval":
        return await main_module.resume_runtime_approval_execution(execution, SimpleNamespace(approval_id="approval"))
    if entry in {"client", "office"}:
        return await main_module.resume_runtime_client_tool_execution(
            execution, SimpleNamespace(
                request_id="request", host_id="host", status="completed", result="synthetic",
                tool_name="office_read" if entry == "office" else "host_page_read",
            ),
        )
    return await main_module.resume_workflow_handoff_execution(
        execution, SimpleNamespace(handoff_id="handoff", status="completed"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["runner", "approval", "client", "office", "handoff"])
@pytest.mark.parametrize("ownership", ["missing", "empty", "expired", "stale", "cancelled", "completed"])
async def test_invalid_resume_is_rejected_before_preflight_or_callback_work(runtime, monkeypatch, entry, ownership):
    store, registry, deployment, clock = runtime
    execution = claim(store)
    if ownership in {"missing", "empty"}:
        execution.lease_token = None if ownership == "missing" else ""
    elif ownership in {"expired", "stale"}:
        clock.now += 10
        if ownership == "stale":
            store.claim("task", worker_id="new")
    elif ownership == "cancelled":
        store.cancel("task")
    else:
        store.complete("task", result="synthetic", expected_lease_token=execution.lease_token)
    before = store.snapshot("task")
    before_bytes = store.snapshot_path.read_bytes()
    barrier = Mock(side_effect=AssertionError("Invalid owner reached preflight"))
    runner = AsyncMock(side_effect=AssertionError("Invalid owner reached runner"))
    if entry == "runner":
        monkeypatch.setattr(main_module, "has_controlled_writes", barrier)
    else:
        monkeypatch.setattr(main_module, "_run_workflow_response", runner)
    with pytest.raises(WorkflowExecutionConflictError):
        await invoke(entry, execution)
    barrier.assert_not_called()
    runner.assert_not_awaited()
    assert asdict(store.require("task")) == asdict(before)
    assert store.snapshot_path.read_bytes() == before_bytes
    assert await registry.list_runs() == []
    deployment.fail_execution.assert_not_called()


@pytest.mark.asyncio
async def test_office_receipt_uses_claimed_owner_and_reaches_runner(runtime, monkeypatch):
    store, _registry, _deployment, _clock = runtime
    execution = claim(store)

    async def wait_again(*_args, **kwargs):
        assert kwargs["resume_execution"].lease_token == execution.lease_token
        store.suspend("task", wait_kind="client_tool", wait_id="next", continuation={"queue": ["next"]}, expected_lease_token=execution.lease_token)

        async def frames():
            yield ("data: " + json.dumps({"event": "client_tool_waiting", "request_id": "next", "task_id": execution.task_id, "run_id": execution.run_id}) + "\n\n").encode()

        return StreamingResponse(frames())

    runner = AsyncMock(side_effect=wait_again)
    monkeypatch.setattr(main_module, "_run_workflow_response", runner)
    await invoke("office", execution)
    runner.assert_awaited_once()
    assert store.require("task").status == "waiting"
    assert [event["event"] for event in store.require("task").events].count("office_operation_finished") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["running", "completed", "cancelled", "waiting"])
async def test_late_handoff_exception_cannot_fail_or_project_replacement(runtime, monkeypatch, replacement):
    store, _registry, deployment, clock = runtime
    execution = claim(store)
    snapshots = []

    async def late_failure(*_args, **_kwargs):
        clock.now += 10
        owner = store.claim("task", worker_id="new")
        if replacement == "completed":
            store.complete("task", result="new-result", expected_lease_token=owner.lease_token)
        elif replacement == "cancelled":
            store.cancel("task")
        elif replacement == "waiting":
            store.suspend("task", wait_kind="approval", wait_id="next", continuation={}, expected_lease_token=owner.lease_token)
        snapshots.append(store.snapshot_path.read_bytes())
        raise RuntimeError("synthetic delayed failure")

    monkeypatch.setattr(main_module, "_run_workflow_response", AsyncMock(side_effect=late_failure))
    with pytest.raises(WorkflowExecutionConflictError):
        await invoke("handoff", execution)
    assert store.snapshot_path.read_bytes() == snapshots[0]
    deployment.fail_execution.assert_not_called()


@pytest.mark.asyncio
async def test_current_handoff_failure_still_records_and_projects_failure(runtime, monkeypatch):
    store, _registry, deployment, _clock = runtime
    execution = claim(store)
    monkeypatch.setattr(main_module, "_run_workflow_response", AsyncMock(side_effect=RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await invoke("handoff", execution)
    assert store.require("task").status == "failed"
    deployment.fail_execution.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_real_pure_runner_keeps_initial_and_owned_execution_compatible(runtime, resumed):
    store, registry, _deployment, _clock = runtime
    execution = store.claim("task", worker_id="owner", lease_seconds=60) if resumed else None
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
    event = await main_module.consume_workflow_stream(response)
    assert event["event"] == "workflow_end"
    assert event["final_output"] == "synthetic"
    item = store.require(event["task_id"])
    assert item.status == "completed"
    assert (await registry.get_run(item.run_id)).status == "completed"


@pytest.mark.asyncio
async def test_reclaim_during_run_lookup_cannot_reopen_registry(runtime, monkeypatch):
    store, registry, _deployment, clock = runtime
    run = await registry.create_run("workflow", "synthetic", status="waiting")
    store.update_run_id("task", run_id=run.run_id)
    execution = store.claim("task", worker_id="old", lease_seconds=5)
    snapshots = []
    original_lookup = registry.get_run

    async def delayed_lookup(run_id):
        result = await original_lookup(run_id)
        clock.now += 10
        store.claim("task", worker_id="new")
        snapshots.append(store.snapshot_path.read_bytes())
        return result

    monkeypatch.setattr(registry, "get_run", delayed_lookup)
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    with pytest.raises(WorkflowExecutionConflictError):
        await main_module._run_workflow_response(payload, None, resume_execution=execution)
    assert run.status == "waiting"
    assert store.snapshot_path.read_bytes() == snapshots[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_terminal_audit_is_durable_before_success_frame(runtime, resumed):
    store, _registry, _deployment, _clock = runtime
    execution = store.claim("task", worker_id="owner", lease_seconds=60) if resumed else None
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
    try:
        async for frame in response.body_iterator:
            text = frame.decode() if isinstance(frame, bytes) else frame
            event = json.loads(text.removeprefix("data:").strip())
            if event.get("event") == "workflow_end":
                reloaded = WorkflowExecutionStore(store.storage_dir).require(event["task_id"])
                assert reloaded.status == "completed"
                assert [item["event"] for item in reloaded.events].count("workflow_end") == 1
                break
        else:
            pytest.fail("No success frame")
    finally:
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_success_projection_follows_durable_settlement(runtime, monkeypatch):
    store, registry, _deployment, _clock = runtime
    execution = store.claim("task", worker_id="owner", lease_seconds=60)
    original_update = registry.update_run
    observed = []

    async def checked_update(run_id, **kwargs):
        if kwargs.get("status") == "completed":
            item = WorkflowExecutionStore(store.storage_dir).require("task")
            observed.append((item.status, any(event["event"] == "workflow_end" for event in item.events)))
        return await original_update(run_id, **kwargs)

    monkeypatch.setattr(registry, "update_run", checked_update)
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
    assert (await main_module.consume_workflow_stream(response))["event"] == "workflow_end"
    assert observed == [("completed", True)]


@pytest.mark.asyncio
async def test_completed_checkpoint_cannot_reclaim_a_settled_execution(runtime, monkeypatch):
    store, registry, _deployment, clock = runtime
    execution = store.claim("task", worker_id="owner", lease_seconds=5)
    original_checkpoint = registry.record_checkpoint
    attempts = []

    async def checked_checkpoint(*args, **kwargs):
        if kwargs.get("event_type") == "workflow.completed":
            clock.now += 10
            before = store.snapshot_path.read_bytes()
            with pytest.raises(WorkflowExecutionConflictError):
                store.claim("task", worker_id="late-worker")
            assert store.snapshot_path.read_bytes() == before
            attempts.append(store.require("task").status)
        return await original_checkpoint(*args, **kwargs)

    monkeypatch.setattr(registry, "record_checkpoint", checked_checkpoint)
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
    assert (await main_module.consume_workflow_stream(response))["event"] == "workflow_end"
    assert attempts == ["completed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_cancel_wins_immediately_before_real_terminal_commit(runtime, monkeypatch, resumed):
    store, registry, _deployment, _clock = runtime
    execution = store.claim("task", worker_id="owner") if resumed else None
    original_complete = store.complete
    protected = []

    def cancel_then_complete(task_id, **kwargs):
        store.cancel(task_id)
        protected.append(store.snapshot_path.read_bytes())
        return original_complete(task_id, **kwargs)

    monkeypatch.setattr(store, "complete", cancel_then_complete)
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
    assert (await main_module.consume_workflow_stream(response))["event"] == "workflow_cancelled"
    assert store.snapshot_path.read_bytes() == protected[0]
    assert all(run.status != "completed" for run in await registry.list_runs())


def failure_for(kind):
    if kind == "termination":
        return main_module.WorkflowTerminationError("SYNTHETIC_ERROR", "synthetic", node_id="input")
    if kind == "content":
        return main_module.ContentPolicyError("SYNTHETIC_ERROR", "synthetic", phase="input")
    if kind == "generic":
        return RuntimeError("synthetic")
    classes = {
        "knowledge": main_module.WorkflowKnowledgeFatalError,
        "vision": main_module.WorkflowVisionFatalError,
        "document": main_module.WorkflowDocumentFatalError,
    }
    return classes[kind]("input", "SYNTHETIC_ERROR", "synthetic")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["termination", "knowledge", "vision", "document", "content", "generic"])
@pytest.mark.parametrize("boundary", ["owned", "reclaimed", "cancelled", "disk_failure"])
async def test_failure_projection_requires_its_durable_decision(runtime, monkeypatch, kind, boundary):
    store, registry, _deployment, clock = runtime
    execution = store.claim("task", worker_id="owner", lease_seconds=5)
    encode = main_module.sse_payload
    original_fail = store.fail
    original_update = registry.update_run
    projections = []
    protected = []

    def inject_error(event):
        if event.get("event") == "workflow_meta":
            raise failure_for(kind)
        return encode(event)

    def fail_write(*_args):
        raise OSError("synthetic disk failure")

    def intercepted_fail(task_id, **kwargs):
        if boundary == "reclaimed":
            clock.now += 10
            store.claim(task_id, worker_id="replacement")
        elif boundary == "cancelled":
            store.cancel(task_id)
        elif boundary == "disk_failure":
            monkeypatch.setattr(store, "_write_fsynced", fail_write)
        protected.append(store.snapshot_path.read_bytes())
        return original_fail(task_id, **kwargs)

    async def checked_update(run_id, **kwargs):
        if kwargs.get("status") in {"completed", "failed"}:
            current = WorkflowExecutionStore(store.storage_dir).require("task")
            projections.append((current.status, sum(event["event"] == "error" for event in current.events)))
        return await original_update(run_id, **kwargs)

    monkeypatch.setattr(main_module, "sse_payload", inject_error)
    monkeypatch.setattr(store, "fail", intercepted_fail)
    monkeypatch.setattr(registry, "update_run", checked_update)
    payload = main_module.WorkflowRunRequest(workflow=workflow(), inputs={"user_input": "synthetic"})
    response = await main_module._run_workflow_response(payload, None, resume_execution=execution)
    expected = main_module.WorkflowStreamFailure if boundary == "owned" else (
        WorkflowExecutionStorageError if boundary == "disk_failure" else WorkflowExecutionConflictError
    )
    with pytest.raises(expected):
        await main_module.consume_workflow_stream(response)
    if boundary == "owned":
        assert projections == [("failed", 1)]
    else:
        assert projections == []
        assert store.snapshot_path.read_bytes() == protected[0]
