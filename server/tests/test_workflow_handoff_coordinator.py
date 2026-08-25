from __future__ import annotations

import time

import pytest

from server.xpert_runtime import AgentTaskStore, WorkflowExecutionStore
from server.xpert_runtime.workflow_handoff_coordinator import WorkflowHandoffCoordinator


def _waiting_execution(
    store: WorkflowExecutionStore,
    handoff_id: str,
    *,
    resume_at: float | None = None,
) -> None:
    store.create(
        task_id="workflow-task",
        run_id="workflow-run",
        run_type="workflow",
        workflow={"id": "workflow", "nodes": [], "edges": []},
        inputs={},
        source_kind="workflow_deployment",
    )
    store.suspend(
        "workflow-task",
        wait_kind="agent_handoff",
        wait_id=handoff_id,
        continuation={"agent_state": {"handoff_id": handoff_id}},
        safe_event={"event": "agent_handoff_waiting", "wait_id": handoff_id},
        resume_at=time.time() + 60 if resume_at is None else resume_at,
    )


@pytest.mark.asyncio
async def test_terminal_handoff_is_leased_and_resumed_once_after_restart(tmp_path) -> None:
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(storage_dir=tmp_path / "executions")
    task = await tasks.create_task("T", "private input")
    handoff = await tasks.create_handoff(
        task.task_id,
        source_agent="workflow",
        target_agent="review-agent",
        reason="private reason",
    )
    _waiting_execution(executions, handoff.handoff_id)
    await tasks.update_handoff_status(handoff.handoff_id, "accepted")
    await tasks.update_handoff_status(handoff.handoff_id, "completed")
    resumed: list[str] = []

    async def resume(execution, terminal_handoff) -> None:
        resumed.append(terminal_handoff.handoff_id)
        executions.complete(execution.task_id, result="done")

    coordinator = WorkflowHandoffCoordinator(tasks, executions, resume)
    assert await coordinator.run_once() == 1
    assert await coordinator.run_once() == 0
    assert resumed == [handoff.handoff_id]
    assert executions.get("workflow-task").status == "completed"


@pytest.mark.asyncio
async def test_resume_failure_is_redacted_and_not_retried(tmp_path) -> None:
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(storage_dir=tmp_path / "executions")
    task = await tasks.create_task("T", "SENTINEL_TASK_INPUT")
    handoff = await tasks.create_handoff(
        task.task_id,
        source_agent="workflow",
        target_agent="review-agent",
        reason="SENTINEL_REASON",
    )
    _waiting_execution(executions, handoff.handoff_id)
    await tasks.update_handoff_status(handoff.handoff_id, "accepted")
    await tasks.update_handoff_status(handoff.handoff_id, "completed")

    async def resume(_execution, _handoff) -> None:
        raise RuntimeError("SENTINEL_PROVIDER_OUTPUT")

    coordinator = WorkflowHandoffCoordinator(tasks, executions, resume)
    assert await coordinator.run_once() == 0
    failed = executions.get("workflow-task")
    assert failed.status == "failed"
    assert failed.error == "HANDOFF_RESUME_FAILED"
    assert "SENTINEL" not in str(failed)


@pytest.mark.asyncio
async def test_expired_wait_invokes_bounded_expiration_then_resumes(tmp_path) -> None:
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    executions = WorkflowExecutionStore(storage_dir=tmp_path / "executions")
    task = await tasks.create_task("T", "private input")
    handoff = await tasks.create_handoff(
        task.task_id,
        source_agent="workflow",
        target_agent="review-agent",
        reason="private reason",
    )
    _waiting_execution(
        executions,
        handoff.handoff_id,
        resume_at=time.time() - 1,
    )
    expired: list[str] = []

    async def expire(_execution, current_handoff) -> None:
        expired.append(current_handoff.handoff_id)
        await tasks.update_handoff_status(current_handoff.handoff_id, "dead_letter")

    async def resume(execution, _handoff) -> None:
        executions.fail(execution.task_id, error="HANDOFF_TIMEOUT")

    coordinator = WorkflowHandoffCoordinator(
        tasks,
        executions,
        resume,
        expire_execution=expire,
    )
    assert await coordinator.run_once() == 1
    assert expired == [handoff.handoff_id]
    assert executions.get("workflow-task").error == "HANDOFF_TIMEOUT"
