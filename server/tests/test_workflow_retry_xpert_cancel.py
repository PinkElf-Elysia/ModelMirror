from __future__ import annotations

import copy

import httpx
import pytest

import server.main as main_module
from server.xpert_runtime import RunRegistry
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def _retry_workflow() -> dict:
    return {
        "id": "private-xpert-retry",
        "title": "Private Xpert retry",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "request",
                "type": "http_request",
                "data": {
                    "kind": "http_request",
                    "contractVersion": 2,
                    "method": "GET",
                    "url": "https://example.com/status",
                    "bodyMode": "none",
                    "outputVariable": "http_response",
                    "retryMode": "transient",
                    "maxAttempts": 2,
                },
            },
        ],
        "edges": [{"id": "edge-1", "source": "input", "target": "request"}],
    }


def _suspend_xpert_retry(
    store: WorkflowExecutionStore,
    *,
    task_id: str,
    run_id: str,
    resume_at: float = 100.0,
) -> None:
    store.create(
        task_id=task_id,
        run_id=run_id,
        run_type="xpert",
        workflow=_retry_workflow(),
        inputs={"user_input": "synthetic"},
        source_kind="xpert_chat",
    )
    store.suspend(
        task_id,
        wait_kind="node_retry",
        wait_id=f"node_retry:{task_id}",
        resume_at=resume_at,
        continuation={
            "retry_state": {
                "version": 1,
                "node_id": "request",
                "node_kind": "http_request",
                "next_attempt": 2,
                "max_attempts": 2,
                "error_code": "HTTP_TIMEOUT",
                "classification": "transient",
                "resume_at": resume_at,
                "target_fingerprint": None,
            }
        },
    )


def test_find_by_run_id_resolves_current_and_previous_run_ids(tmp_path) -> None:
    store = WorkflowExecutionStore(tmp_path / "executions")
    _suspend_xpert_retry(
        store,
        task_id="task-xpert-retry",
        run_id="run-before-recovery",
    )

    store.update_run_id("task-xpert-retry", run_id="run-after-recovery")

    current = store.find_by_run_id("run-after-recovery")
    previous = store.find_by_run_id("run-before-recovery")
    assert current is not None
    assert previous is not None
    assert current.task_id == "task-xpert-retry"
    assert previous.task_id == "task-xpert-retry"
    assert previous.run_id == "run-after-recovery"
    assert previous.previous_run_ids == ["run-before-recovery"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_with_previous_run", [False, True])
async def test_runtime_cancel_stops_private_xpert_retry_without_due_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    cancel_with_previous_run: bool,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    original_run = await registry.create_run(
        "xpert",
        "Private Xpert retry",
        status="running",
        source_id="xpert-private-retry",
    )
    _suspend_xpert_retry(
        execution_store,
        task_id="task-xpert-retry",
        run_id=original_run.run_id,
    )

    cancel_run_id = original_run.run_id
    if cancel_with_previous_run:
        replacement_run = await registry.create_run(
            "xpert",
            "Private Xpert retry recovery",
            status="running",
            source_id="xpert-private-retry",
        )
        execution_store.update_run_id(
            "task-xpert-retry",
            run_id=replacement_run.run_id,
        )

    assert [
        item.task_id for item in execution_store.list_due_waits(now=100.0)
    ] == ["task-xpert-retry"]

    resume_calls = 0

    async def should_not_resume(*_args, **_kwargs):
        nonlocal resume_calls
        resume_calls += 1
        raise AssertionError("A cancelled Xpert retry must not resume.")

    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "workflow_task_store", {})
    monkeypatch.setattr(main_module, "_run_workflow_response", should_not_resume)

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        first = await client.post(
            f"/api/runtime/runs/{cancel_run_id}/cancel",
            json={"reason": "user stopped retry"},
        )
        second = await client.post(
            f"/api/runtime/runs/{cancel_run_id}/cancel",
            json={"reason": "duplicate stop"},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["status"] == "cancelled"
    assert second.json()["status"] == "cancelled"

    cancelled = execution_store.require("task-xpert-retry")
    assert cancelled.status == "cancelled"
    assert cancelled.wait_kind is None
    assert cancelled.wait_id is None
    assert cancelled.resume_at is None
    assert execution_store.list_due_waits(now=10_000.0) == []
    assert sum(
        event.get("event") == "workflow_cancelled"
        for event in cancelled.events
    ) == 1
    cancel_checkpoints = await registry.list_checkpoints(cancel_run_id)
    assert sum(
        checkpoint.event_type == "run.cancelled"
        for checkpoint in cancel_checkpoints
    ) == 1
    current_run_id = execution_store.require("task-xpert-retry").run_id
    current_run = await registry.get_run(current_run_id)
    assert current_run is not None
    assert current_run.status == "cancelled"
    current_checkpoints = await registry.list_checkpoints(current_run_id)
    assert sum(
        checkpoint.event_type == "run.cancelled"
        for checkpoint in current_checkpoints
    ) == 1

    resume_result = await main_module.resume_runtime_due_execution(
        "task-xpert-retry"
    )
    assert resume_result == {
        "status": "cancelled",
        "task_id": "task-xpert-retry",
    }
    assert resume_calls == 0


@pytest.mark.asyncio
async def test_cancel_previous_run_survives_lost_old_registry_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    replacement = await registry.create_run(
        "xpert",
        "Recovered private Xpert retry",
        status="running",
        source_id="xpert-private-retry",
    )
    _suspend_xpert_retry(
        execution_store,
        task_id="task-xpert-retry",
        run_id="run-lost-on-restart",
    )
    execution_store.update_run_id(
        "task-xpert-retry",
        run_id=replacement.run_id,
    )
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "workflow_task_store", {})

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/runtime/runs/run-lost-on-restart/cancel",
            json={"reason": "SENTINEL_PRIVATE_REASON"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == replacement.run_id
    assert response.json()["status"] == "cancelled"
    assert "SENTINEL" not in response.text
    assert execution_store.require("task-xpert-retry").status == "cancelled"
    assert (await registry.get_run(replacement.run_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_repeat_cancel_repairs_store_won_registry_crash_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    recovery_run = await registry.create_run(
        "xpert",
        "Recovered private Xpert retry",
        status="running",
    )
    _suspend_xpert_retry(
        execution_store,
        task_id="task-xpert-retry",
        run_id=recovery_run.run_id,
    )
    # Simulate a crash after the durable Store won cancellation but before the
    # in-memory RunRegistry was projected.
    execution_store.cancel("task-xpert-retry", error="cancelled_by_user")
    assert (await registry.get_run(recovery_run.run_id)).status == "running"
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/runtime/runs/{recovery_run.run_id}/cancel",
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert (await registry.get_run(recovery_run.run_id)).status == "cancelled"
    checkpoints = await registry.list_checkpoints(recovery_run.run_id)
    assert sum(item.event_type == "run.cancelled" for item in checkpoints) == 1


@pytest.mark.asyncio
async def test_cancel_refreshes_run_ids_after_concurrent_recovery_rebind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    original = await registry.create_run(
        "xpert",
        "Original private Xpert retry",
        status="running",
    )
    recovery = await registry.create_run(
        "xpert",
        "Recovered private Xpert retry",
        status="running",
    )
    _suspend_xpert_retry(
        execution_store,
        task_id="task-xpert-retry",
        run_id=original.run_id,
    )
    original_find = execution_store.find_by_run_id

    def find_then_finish_rebind(run_id: str):
        stale = copy.deepcopy(original_find(run_id))
        execution_store.update_run_id(
            "task-xpert-retry",
            run_id=recovery.run_id,
        )
        return stale

    monkeypatch.setattr(execution_store, "find_by_run_id", find_then_finish_rebind)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "workflow_task_store", {})

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(f"/api/runtime/runs/{original.run_id}/cancel")

    assert response.status_code == 200, response.text
    assert execution_store.require("task-xpert-retry").status == "cancelled"
    assert (await registry.get_run(original.run_id)).status == "cancelled"
    assert (await registry.get_run(recovery.run_id)).status == "cancelled"


@pytest.mark.asyncio
async def test_new_recovery_run_is_cancelled_when_rebind_loses_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "server.xpert_runtime.execution_store.time.time",
        lambda: 100.0,
    )
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    registry = RunRegistry()
    _suspend_xpert_retry(
        execution_store,
        task_id="task-xpert-retry",
        run_id="run-missing-after-restart",
    )
    claimed = execution_store.claim_due_wait(
        "task-xpert-retry",
        wait_kind="node_retry",
        wait_id="node_retry:task-xpert-retry",
        worker_id="recovery-worker",
        now=100.0,
    )
    original_update = execution_store.update_run_id

    def cancel_before_rebind(*args, **kwargs):
        execution_store.cancel("task-xpert-retry", error="cancelled_by_user")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(execution_store, "update_run_id", cancel_before_rebind)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", registry)
    monkeypatch.setattr(main_module, "workflow_task_store", {})
    monkeypatch.setenv("WORKFLOW_NODE_RETRIES_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "true")
    payload = main_module.WorkflowRunRequest.model_validate(
        {
            "workflow": _retry_workflow(),
            "inputs": {"user_input": "synthetic"},
        }
    )

    with pytest.raises(main_module.WorkflowExecutionConflictError):
        await main_module._run_workflow_response(
            payload,
            None,
            runtime_run_type="xpert",
            runtime_source_id="xpert-private-retry",
            resume_execution=claimed,
            runtime_execution_source_kind="xpert_chat",
        )

    recovery_runs = await registry.list_runs(limit=10)
    assert len(recovery_runs) == 1
    assert recovery_runs[0].status == "cancelled"
    assert recovery_runs[0].error == "recovery_lease_lost"


@pytest.mark.asyncio
async def test_cancelled_run_registry_status_is_monotonic() -> None:
    registry = RunRegistry()
    run = await registry.create_run(
        "xpert",
        "Cancellation winner",
        status="running",
    )
    await registry.cancel_run(run.run_id, reason="cancelled_by_user")
    stale = await registry.update_run(
        run.run_id,
        status="completed",
        error="SENTINEL_STALE_WORKER",
    )

    assert stale.status == "cancelled"
    assert stale.error == "cancelled_by_user"
    checkpoints = await registry.list_checkpoints(run.run_id)
    assert sum(item.event_type == "run.cancelled" for item in checkpoints) == 1
