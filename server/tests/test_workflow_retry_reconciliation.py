from __future__ import annotations

import asyncio

import pytest

import server.api.workflow_deployments as deployment_api
import server.main as main_module
from server.workflow_deployments import (
    WorkflowDeploymentStore,
    WorkflowTriggerExecution,
)
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError


def _deployment_wait(
    store: WorkflowDeploymentStore,
    *,
    task_id: str,
    execution_id: str = "wfx_reconcile",
) -> WorkflowTriggerExecution:
    item = WorkflowTriggerExecution(
        execution_id=execution_id,
        project_id="wf_reconcile",
        version=1,
        deployment_id="wfd_reconcile",
        trigger_kind="schedule",
        occurrence_key="schedule:reconcile",
        status="waiting",
        task_id=task_id,
        run_id="run-reconcile",
        wait_kind="node_retry",
        wait_id=f"node_retry:{task_id}",
        resume_at=100.0,
    )
    with store._lock:
        store._executions[item.execution_id] = item
        store._persist_unlocked()
    return item


def _runtime_execution(
    store: WorkflowExecutionStore,
    *,
    task_id: str,
    deployment_execution_id: str = "wfx_reconcile",
) -> None:
    store.create(
        task_id=task_id,
        run_id="run-reconcile",
        run_type="workflow",
        workflow={"id": "reconcile", "title": "Reconcile", "nodes": [], "edges": []},
        inputs={},
        source_kind="workflow_deployment",
        runtime_metadata={
            "workflow_deployment_execution_id": deployment_execution_id,
            "workflow_project_id": "wf_reconcile",
            "workflow_version": 1,
        },
    )


async def _run_reconciliation_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    deployment_store: WorkflowDeploymentStore,
    execution_store: WorkflowExecutionStore,
) -> None:
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_wait_due_source",
        lambda: execution_store.list_due_waits(now=1_000.0, limit=20),
    )
    monkeypatch.setattr(
        deployment_api,
        "_wait_reconciliation_source",
        main_module.list_terminal_workflow_deployment_waits,
    )
    monkeypatch.setattr(
        deployment_api,
        "_wait_resume_executor",
        main_module.resume_runtime_due_execution,
    )
    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false")
    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false")
    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "false")

    coordinator = deployment_api.WorkflowTriggerCoordinator()
    await coordinator.run_once()
    tasks = list(coordinator._wait_resume_tasks.values())
    assert len(tasks) == 1
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
async def test_terminal_runtime_reconciles_waiting_deployment_after_crash_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    terminal_status: str,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    _deployment_wait(deployment_store, task_id="task-reconcile")
    _runtime_execution(execution_store, task_id="task-reconcile")
    if terminal_status == "completed":
        execution_store.complete("task-reconcile", result="winner")
    elif terminal_status == "failed":
        execution_store.fail(
            "task-reconcile",
            error="HTTP_TIMEOUT: HTTP request timed out.",
        )
        execution_store.append_event(
            "task-reconcile",
            {
                "event": "error",
                "terminal": True,
                "code": "HTTP_TIMEOUT",
                "message": "HTTP request timed out.",
            },
        )
    else:
        execution_store.cancel("task-reconcile", error="cancelled_by_user")

    await _run_reconciliation_coordinator(
        monkeypatch,
        deployment_store,
        execution_store,
    )

    reconciled = deployment_store.get_execution("wfx_reconcile")
    assert reconciled is not None
    assert reconciled.status == terminal_status
    assert reconciled.task_id == "task-reconcile"
    assert reconciled.run_id == "run-reconcile"
    if terminal_status == "failed":
        assert reconciled.error_summary == "HTTP_TIMEOUT"
    assert main_module.list_terminal_workflow_deployment_waits() == []


def test_terminal_runtime_reconciles_pending_deployment_before_fresh_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    deployment = _deployment_wait(deployment_store, task_id="task-pending-terminal")
    with deployment_store._lock:
        deployment.status = "pending"
        deployment_store._persist_unlocked()
    _runtime_execution(execution_store, task_id="task-pending-terminal")
    execution_store.complete("task-pending-terminal", result="winner")
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)

    assert main_module.guard_claimable_workflow_deployment_execution(deployment) is True
    projected = deployment_store.get_execution(deployment.execution_id)
    assert projected is not None
    assert projected.status == "completed"
    assert projected.result_summary.startswith("completed output_bytes=6 sha256=")
    assert main_module.list_terminal_workflow_deployment_waits() == []


@pytest.mark.asyncio
async def test_retry_suspend_crash_before_deployment_projection_never_replays_fresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_path = tmp_path / "deployments"
    execution_path = tmp_path / "executions"
    deployment_store = WorkflowDeploymentStore(deployment_path)
    execution_store = WorkflowExecutionStore(execution_path)
    deployment = _deployment_wait(deployment_store, task_id="task-cross-store")
    with deployment_store._lock:
        deployment.status = "running"
        deployment.lease_owner = "worker-before-crash"
        deployment.lease_token = "lease-before-crash"
        deployment.lease_expires_at = 10_000.0
        deployment_store._persist_unlocked()
    _runtime_execution(execution_store, task_id="task-cross-store")
    execution_store.suspend(
        "task-cross-store",
        wait_kind="node_retry",
        wait_id="node_retry:cross-store",
        resume_at=1.0,
        continuation={"retry_state": {"version": 1}},
    )

    # Simulate the process ending after the durable continuation commit but
    # before the deployment row is projected from running to waiting.
    deployment_store = WorkflowDeploymentStore(deployment_path)
    execution_store = WorkflowExecutionStore(execution_path)
    assert deployment_store.get_execution(deployment.execution_id).status == "pending"
    assert execution_store.require("task-cross-store").status == "waiting"

    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_execution_recovery_guard",
        main_module.guard_claimable_workflow_deployment_execution,
    )
    monkeypatch.setattr(
        deployment_api,
        "_wait_due_source",
        lambda: execution_store.list_due_waits(limit=20),
    )
    monkeypatch.setattr(deployment_api, "_wait_reconciliation_source", lambda: [])
    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false")
    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false")

    fresh_calls = 0
    resume_calls = 0
    release = asyncio.Event()

    async def forbidden_fresh_trigger(*_args, **_kwargs):
        nonlocal fresh_calls
        fresh_calls += 1
        return {"status": "completed"}

    async def claimed_resume(task_id: str) -> dict[str, str]:
        nonlocal resume_calls
        current = execution_store.require(task_id)
        try:
            execution_store.claim_due_wait(
                task_id,
                wait_kind=str(current.wait_kind),
                wait_id=str(current.wait_id),
                worker_id="recovery-worker",
            )
        except WorkflowExecutionConflictError:
            return {"status": execution_store.require(task_id).status}
        resume_calls += 1
        await release.wait()
        return {"status": "running"}

    monkeypatch.setattr(deployment_api, "_trigger_executor", forbidden_fresh_trigger)
    monkeypatch.setattr(deployment_api, "_wait_resume_executor", claimed_resume)

    first = deployment_api.WorkflowTriggerCoordinator()
    second = deployment_api.WorkflowTriggerCoordinator()
    await asyncio.gather(first.run_once(), second.run_once())
    await asyncio.sleep(0)
    assert fresh_calls == 0
    assert resume_calls == 1
    assert deployment_store.get_execution(deployment.execution_id).status == "waiting"
    release.set()
    pending = [
        task
        for coordinator in (first, second)
        for task in coordinator._wait_resume_tasks.values()
    ]
    if pending:
        await asyncio.gather(*pending)


def test_reconciliation_never_promotes_arbitrary_uppercase_raw_error(
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    _runtime_execution(execution_store, task_id="task-unsafe-error")
    execution_store.fail(
        "task-unsafe-error",
        error="SENTINEL_PRIVATE_CREDENTIAL",
    )
    execution_store.append_event(
        "task-unsafe-error",
        {
            "event": "error",
            "terminal": True,
            "code": "SENTINEL_PRIVATE_CREDENTIAL",
            "message": "SENTINEL_PRIVATE_CREDENTIAL",
        },
    )

    assert (
        main_module._safe_reconciled_execution_error(
            execution_store.require("task-unsafe-error")
        )
        == "WORKFLOW_EXECUTION_FAILED"
    )


@pytest.mark.asyncio
async def test_invalid_due_wait_is_failed_then_reconciled_in_same_poll(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    _deployment_wait(deployment_store, task_id="task-invalid-wait")
    _runtime_execution(execution_store, task_id="task-invalid-wait")
    execution_store.suspend(
        "task-invalid-wait",
        wait_kind="node_retry",
        wait_id="node_retry:invalid",
        resume_at=100.0,
        continuation={"retry_state": {"version": 1}},
    )
    with execution_store._lock:
        execution_store._items["task-invalid-wait"].resume_at = float("inf")
        execution_store._persist_unlocked()

    await _run_reconciliation_coordinator(
        monkeypatch,
        deployment_store,
        execution_store,
    )

    durable = execution_store.require("task-invalid-wait")
    assert durable.status == "failed"
    assert durable.error == "WORKFLOW_WAIT_STATE_INVALID"
    reconciled = deployment_store.get_execution("wfx_reconcile")
    assert reconciled is not None
    assert reconciled.status == "failed"
    assert reconciled.error_summary == "WORKFLOW_WAIT_STATE_INVALID"


def test_reconciliation_rejects_mismatched_deployment_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    _deployment_wait(deployment_store, task_id="task-mismatch")
    _runtime_execution(execution_store, task_id="task-mismatch")
    execution_store.complete("task-mismatch", result="must-not-project")
    with execution_store._lock:
        execution_store._items["task-mismatch"].runtime_metadata[
            "workflow_project_id"
        ] = "wf_other"
        execution_store._persist_unlocked()
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)

    candidates = main_module.list_terminal_workflow_deployment_waits()
    assert [item.task_id for item in candidates] == ["task-mismatch"]
    result = main_module.reconcile_runtime_deployment_execution(
        execution_store.require("task-mismatch")
    )
    assert result == {"status": "completed", "task_id": "task-mismatch"}
    assert deployment_store.get_execution("wfx_reconcile").status == "waiting"
    assert main_module.list_terminal_workflow_deployment_waits() == []


def test_terminal_reconciliation_is_not_starved_by_older_waiting_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    for index in range(101):
        task_id = f"task-history-{index:03d}"
        execution_id = f"wfx-history-{index:03d}"
        _deployment_wait(
            deployment_store,
            task_id=task_id,
            execution_id=execution_id,
        )
        _runtime_execution(
            execution_store,
            task_id=task_id,
            deployment_execution_id=execution_id,
        )
        execution_store.complete(task_id, result="historical")
        deployment_store.complete_execution(
            execution_id,
            task_id=task_id,
            run_id="run-reconcile",
            result="historical",
        )

    _deployment_wait(
        deployment_store,
        task_id="task-terminal-after-page",
        execution_id="wfx-terminal-after-page",
    )
    _runtime_execution(
        execution_store,
        task_id="task-terminal-after-page",
        deployment_execution_id="wfx-terminal-after-page",
    )
    execution_store.complete("task-terminal-after-page", result="winner")
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)

    observed_target = False
    for _ in range(7):
        candidates = main_module.list_terminal_workflow_deployment_waits()
        for candidate in candidates:
            main_module.reconcile_runtime_deployment_execution(candidate)
            observed_target = observed_target or (
                candidate.task_id == "task-terminal-after-page"
            )
        if observed_target:
            break

    assert observed_target is True
    assert (
        deployment_store.get_execution("wfx-terminal-after-page").status
        == "completed"
    )
    assert main_module.list_terminal_workflow_deployment_waits() == []


@pytest.mark.parametrize(
    "safe_code",
    [
        "HTTP_RESPONSE_TOO_LARGE",
        "HTTP_RESPONSE_NOT_UTF8",
        "HTTP_RESPONSE_JSON_INVALID",
        "HTTP_BINARY_RESPONSE_FORBIDDEN",
    ],
)
def test_reconciliation_reuses_structured_error_routing_safe_codes(
    tmp_path,
    safe_code: str,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / safe_code)
    _runtime_execution(execution_store, task_id=f"task-{safe_code.lower()}")
    execution_store.fail(
        f"task-{safe_code.lower()}",
        error=f"{safe_code}: safe fixed message",
    )

    assert (
        main_module._safe_reconciled_execution_error(
            execution_store.require(f"task-{safe_code.lower()}")
        )
        == safe_code
    )
