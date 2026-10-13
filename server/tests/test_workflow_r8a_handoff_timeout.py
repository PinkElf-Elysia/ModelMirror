"""Timeout decisions and child cancellation with real Stores, no providers."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import server.main as main_module
from server.tests.test_workflow_handoff_coordinator import _waiting_execution
from server.tests.test_workflow_r8a_child_cancel import child, parent, runtime
from server.xpert_runtime import AgentTaskStore, WorkflowExecutionStore
from server.xpert_runtime.agent_tasks import HandoffOwnershipError
from server.tests.test_workflow_r8a_handoff_settlement import setup
from server.xpert_runtime.workflow_handoff_coordinator import WorkflowHandoffCoordinator


@pytest.mark.asyncio
async def test_restart_after_timeout_receipt_finishes_cancellation_before_resume(runtime, monkeypatch):
    tasks, executions, registry, live = runtime
    task, handoff, metadata = await parent(runtime)
    execution, run = await child(runtime, metadata, "approval")
    _waiting_execution(executions, handoff.handoff_id, resume_at=1)
    original_record = tasks._record

    async def crash_after_commit(event, **kwargs):
        if event == "agent.handoff.dead_letter":
            raise RuntimeError("synthetic process interruption after timeout receipt")
        await original_record(event, **kwargs)

    monkeypatch.setattr(tasks, "_record", crash_after_commit)
    with pytest.raises(RuntimeError, match="synthetic process interruption"):
        await main_module.expire_workflow_handoff_execution(
            executions.snapshot("workflow-task"), handoff,
        )
    tasks = AgentTaskStore(storage_dir=tasks.storage_path.parent)
    executions = WorkflowExecutionStore(executions.storage_dir)
    monkeypatch.setattr(main_module, "agent_task_store", tasks)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    resumed = []

    async def resume(current, receipt):
        resumed.append((
            (await tasks.get_task(task.task_id)).status,
            executions.snapshot(execution.task_id).status,
            (await registry.get_run(run.run_id)).status,
        ))
        executions.fail(current.task_id, error="HANDOFF_TIMEOUT", expected_lease_token=current.lease_token)

    coordinator = WorkflowHandoffCoordinator(
        tasks, executions, resume, expire_execution=main_module.expire_workflow_handoff_execution,
    )
    assert await coordinator.run_once() == 1
    assert resumed == [("cancelled", "cancelled", "cancelled")]
    assert live[execution.task_id]["provider_cancel_event"].is_set()
    assert await coordinator.run_once() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("failure", ["child_store", "registry"])
async def test_failed_propagation_blocks_parent_until_restart_reconciles(runtime, monkeypatch, ready, failure):
    tasks, executions, registry, _ = runtime
    task, handoff, metadata = await parent(runtime)
    execution, run = await child(runtime, metadata, "approval")
    _waiting_execution(executions, handoff.handoff_id, resume_at=1)
    await tasks.expire_handoff(handoff.handoff_id)
    if ready:
        executions.mark_ready("workflow-task", wait_kind="agent_handoff", wait_id=handoff.handoff_id)
    resumed = []

    async def resume(current, receipt):
        assert executions.snapshot(execution.task_id).status == "cancelled"
        assert (await registry.get_run(run.run_id)).status == "cancelled"
        resumed.append(current.task_id)
        executions.fail(current.task_id, error="HANDOFF_TIMEOUT", expected_lease_token=current.lease_token)

    coordinator = WorkflowHandoffCoordinator(
        tasks, executions, resume, expire_execution=main_module.expire_workflow_handoff_execution,
    )
    with monkeypatch.context() as fault:
        if failure == "child_store":
            def unavailable(*args, **kwargs):
                raise RuntimeError("synthetic child storage failure")
            fault.setattr(executions, "cancel_agent_task_children", unavailable)
        else:
            async def unavailable(*args, **kwargs):
                raise RuntimeError("synthetic registry failure")
            fault.setattr(registry, "cancel_run", unavailable)
        assert await coordinator.run_once() == 0
        assert await coordinator.run_once() == 0
        assert resumed == []
        assert executions.snapshot("workflow-task").status == "ready"
    tasks = AgentTaskStore(storage_dir=tasks.storage_path.parent)
    executions = WorkflowExecutionStore(executions.storage_dir)
    monkeypatch.setattr(main_module, "agent_task_store", tasks)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    coordinator = WorkflowHandoffCoordinator(
        tasks, executions, resume, expire_execution=main_module.expire_workflow_handoff_execution,
    )
    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert resumed == ["workflow-task"]


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [False, True])
async def test_uncertain_timeout_commit_requires_reload_without_replay(tmp_path, monkeypatch, persisted):
    tasks, _, task, handoff = await setup(tmp_path)
    claimed = await tasks.claim_handoff(handoff.handoff_id, worker_id="old")
    original_persist = tasks._persist_unlocked

    def fail_commit():
        if persisted:
            original_persist()
        raise OSError("synthetic uncertain persistence")

    monkeypatch.setattr(tasks, "_persist_unlocked", fail_commit)
    with pytest.raises(OSError, match="synthetic"):
        await tasks.expire_handoff(handoff.handoff_id)
    with pytest.raises(HandoffOwnershipError):
        await tasks.expire_handoff(handoff.handoff_id)
    with pytest.raises(HandoffOwnershipError):
        await tasks.settle_claimed_handoff(claimed, "completed", result="late")
    tasks = AgentTaskStore(storage_dir=tasks.storage_path.parent)
    assert (await tasks.get_handoff(handoff.handoff_id)).status == ("dead_letter" if persisted else "accepted")
    assert (await tasks.get_task(task.task_id)).status == ("cancelled" if persisted else "pending")
    assert await tasks.expire_handoff(handoff.handoff_id) is True
    saved = tasks.storage_path.read_bytes()
    assert await tasks.expire_handoff(handoff.handoff_id) is True
    assert tasks.storage_path.read_bytes() == saved
    assert (await tasks.get_task(task.task_id)).error == "HANDOFF_TIMEOUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "dead_letter", "retry_wait", "waiting_approval", "waiting_client"])
async def test_timeout_fences_all_late_worker_settlements(tmp_path, status):
    tasks, _, task, handoff = await setup(tmp_path)
    claimed = await tasks.claim_handoff(handoff.handoff_id, worker_id="old")
    assert await tasks.expire_handoff(handoff.handoff_id) is True
    with pytest.raises(HandoffOwnershipError):
        await tasks.settle_claimed_handoff(claimed, status, result="late")
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert (await tasks.get_handoff(handoff.handoff_id)).metadata["last_error"] == "HANDOFF_TIMEOUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["completed", "dead_letter", "cancelled"])
async def test_prior_terminal_decision_is_preserved(tmp_path, winner):
    tasks, _, task, handoff = await setup(tmp_path)
    claimed = await tasks.claim_handoff(handoff.handoff_id, worker_id="winner")
    if winner == "cancelled":
        await tasks.cancel_task(task.task_id, "manual cancellation")
    else:
        await tasks.settle_claimed_handoff(claimed, winner, result="original", error="original" if winner == "dead_letter" else None)
    before = tasks.storage_path.read_bytes()
    assert await tasks.expire_handoff(handoff.handoff_id) is False
    assert tasks.storage_path.read_bytes() == before


@pytest.mark.asyncio
async def test_legacy_split_timeout_is_reconciled(runtime):
    tasks, executions, _, _ = runtime
    task, handoff, metadata = await parent(runtime)
    execution, _ = await child(runtime, metadata, "timer")
    await tasks.update_handoff_status(handoff.handoff_id, "dead_letter", metadata={"last_error": "HANDOFF_TIMEOUT"})
    await main_module.expire_workflow_handoff_execution(execution, handoff)
    assert (await tasks.get_task(task.task_id)).status == "cancelled"
    assert executions.snapshot(execution.task_id).status == "cancelled"


@pytest.mark.asyncio
async def test_broken_expiration_does_not_starve_other_ready_parent(runtime):
    tasks, executions, _, _ = runtime
    _, first, _ = await parent(runtime)
    _waiting_execution(executions, first.handoff_id, resume_at=1)
    other = await tasks.create_task("其他任务", "synthetic")
    second = await tasks.create_handoff(other.task_id, "parent", "xpert:other", "synthetic")
    await tasks.update_handoff_status(second.handoff_id, "accepted")
    await tasks.update_handoff_status(second.handoff_id, "completed")
    executions.create(task_id="other", run_id="other-run", run_type="workflow", workflow={}, inputs={})
    executions.suspend("other", wait_kind="agent_handoff", wait_id=second.handoff_id, continuation={})
    resumed = []

    async def broken(*args):
        raise RuntimeError("synthetic cancellation failure")

    async def resume(current, receipt):
        resumed.append(current.task_id)
        executions.complete(current.task_id, result="done", expected_lease_token=current.lease_token)

    coordinator = WorkflowHandoffCoordinator(tasks, executions, resume, expire_execution=broken)
    assert await coordinator.run_once() == 1
    assert resumed == ["other"]
    assert executions.snapshot("workflow-task").status == "waiting"


@pytest.mark.asyncio
async def test_timeout_without_reconciler_cannot_resume(runtime):
    tasks, executions, _, _ = runtime
    _, handoff, _ = await parent(runtime)
    _waiting_execution(executions, handoff.handoff_id, resume_at=1)
    await tasks.expire_handoff(handoff.handoff_id)

    async def forbidden(*args):
        pytest.fail("timeout receipt is not cancellation propagation evidence")

    assert await WorkflowHandoffCoordinator(tasks, executions, forbidden).run_once() == 0


@pytest.mark.asyncio
async def test_actual_runner_timeout_after_restart_fails_without_dispatch(tmp_path, monkeypatch):
    from server.tests.test_workflow_r8a_handoff_budget import LIMITS, setup as budget_setup
    from server.xpert_runtime import workflow_handoff_coordinator as coordinator_module

    executions, tasks, registry, calls = budget_setup(tmp_path, monkeypatch)
    graph = {"id": "timeout-parent", "title": "超时恢复", "nodes": [
        {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
        {"id": "delegate", "type": "handoff_router", "data": {
            "kind": "handoff_router", "contractVersion": 2, "sourceVariable": "user_input",
            "taskTitle": "合成子任务", "sourceAgent": "workflow", "reasonTemplate": "合成移交",
            "targetMode": "xpert", "targetXpertId": "target", "targetVersion": 1,
            "waitForCompletion": True, "timeoutSeconds": 120, "outputVariable": "receipt", "resultVariable": "answer"}},
        {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "answer"}},
    ], "edges": [{"id": "one", "source": "input", "target": "delegate"},
                  {"id": "two", "source": "delegate", "target": "output"}]}
    response = await main_module._run_workflow_response(
        main_module.WorkflowRunRequest(workflow=graph, inputs={"user_input": "合成输入"}), None,
        runtime_run_type="xpert", runtime_metadata={"xpert_agent_config": LIMITS}, runtime_task_id="root",
    )
    assert (await main_module.consume_workflow_stream(response))["event"] == "agent_handoff_waiting"
    pending = executions.snapshot("root")
    handoff = await tasks.get_handoff(pending.wait_id)
    # Commit the timeout, then restart before propagating child cancellation.
    await tasks.expire_handoff(handoff.handoff_id)
    tasks = AgentTaskStore(storage_dir=tasks.storage_path.parent)
    executions = WorkflowExecutionStore(executions.storage_dir)
    monkeypatch.setattr(main_module, "agent_task_store", tasks)
    monkeypatch.setattr(main_module, "workflow_execution_store", executions)
    monkeypatch.setattr(coordinator_module, "time", SimpleNamespace(time=lambda: pending.resume_at + 1))
    coordinator = WorkflowHandoffCoordinator(
        tasks, executions, main_module.resume_workflow_handoff_execution,
        expire_execution=main_module.expire_workflow_handoff_execution,
    )
    # A failed Runner commits its terminal state and clears the lease; the
    # coordinator count includes only callbacks that returned normally.
    assert await coordinator.run_once() == 0
    assert executions.snapshot("root").status == "failed"
    assert "HANDOFF_TIMEOUT" in executions.snapshot("root").error
    assert (await tasks.get_task(handoff.task_id)).status == "cancelled"
    assert await tasks.list_executable_handoffs() == []
    assert (await registry.get_run(pending.run_id)).status == "failed"
    assert calls == []
    assert executions.snapshot("root").execution_budget["usage"]["model_calls"] == 0
    assert await coordinator.run_once() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_deadline_only_expires_due_wait(runtime, monkeypatch, expired):
    from server.xpert_runtime import workflow_handoff_coordinator as coordinator_module

    tasks, executions, _, _ = runtime
    task, handoff, metadata = await parent(runtime)
    execution, _ = await child(runtime, metadata, "approval")
    _waiting_execution(executions, handoff.handoff_id, resume_at=99 if expired else 101)
    monkeypatch.setattr(coordinator_module, "time", SimpleNamespace(time=lambda: 100))

    async def resume(current, receipt):
        assert executions.snapshot(execution.task_id).status == "cancelled"
        executions.fail(current.task_id, error="HANDOFF_TIMEOUT", expected_lease_token=current.lease_token)

    coordinator = WorkflowHandoffCoordinator(
        tasks, executions, resume, expire_execution=main_module.expire_workflow_handoff_execution,
    )
    assert await coordinator.run_once() == int(expired)
    assert (await tasks.get_task(task.task_id)).status == ("cancelled" if expired else "pending")
    assert executions.snapshot("workflow-task").status == ("failed" if expired else "waiting")


@pytest.mark.asyncio
async def test_conflicting_timeout_terminal_pair_blocks_recovery(runtime):
    tasks, executions, _, _ = runtime
    task, handoff, _ = await parent(runtime)
    _waiting_execution(executions, handoff.handoff_id, resume_at=1)
    await tasks.update_handoff_status(handoff.handoff_id, "dead_letter", metadata={"last_error": "HANDOFF_TIMEOUT"})
    await tasks.update_task(task.task_id, status="completed", result="contradictory legacy state")

    async def forbidden(*args):
        pytest.fail("conflicting terminal records must not resume")

    coordinator = WorkflowHandoffCoordinator(
        tasks, executions, forbidden, expire_execution=main_module.expire_workflow_handoff_execution,
    )
    assert await coordinator.run_once() == 0
    assert (await tasks.get_task(task.task_id)).status == "completed"
