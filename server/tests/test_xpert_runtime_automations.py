from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import server.xpert_runtime.automation_api as automation_api

from server.xpert_runtime import (
    AutomationConflictError,
    AutomationCoordinator,
    AutomationStore,
    AutomationTargetResult,
    AutomationToolsetProvider,
    AutomationValidationError,
    CronSchedule,
    RuntimeToolCall,
    RuntimeToolError,
    RunRegistry,
)


def _definition(
    store: AutomationStore,
    *,
    status: str = "draft",
    max_attempts: int = 3,
    budget: dict | None = None,
):
    return store.create_definition(
        name="Daily research",
        prompt="Collect the latest approved inputs and produce a report.",
        target_xpert_id="xpert-research",
        target_xpert_slug="researcher",
        target_xpert_version=3,
        trigger={"type": "interval", "interval_seconds": 60, "timezone": "UTC"},
        status=status,
        overlap_policy="skip",
        misfire_policy="latest",
        max_attempts=max_attempts,
        budget=budget or {},
    )


def test_trigger_validation_and_cron_timezone() -> None:
    trigger = AutomationStore.validate_trigger(
        {"type": "cron", "cron": "15 9 * * 1-5", "timezone": "Asia/Shanghai"}
    )
    current = datetime(2026, 7, 17, 0, 0).timestamp()
    next_at = AutomationStore.next_occurrence(trigger, current)

    assert trigger.cron == "15 9 * * 1-5"
    assert next_at is not None
    assert next_at > current
    assert CronSchedule.parse("*/10 * * * *").minute.values == frozenset(range(0, 60, 10))

    with pytest.raises(AutomationValidationError):
        AutomationStore.validate_trigger(
            {"type": "interval", "interval_seconds": 10, "timezone": "UTC"}
        )
    with pytest.raises(AutomationValidationError):
        CronSchedule.parse("0 0 * *")


def test_store_persists_pinned_definition_and_execution(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path)
    definition = _definition(store)
    execution = store.run_now(definition.automation_id)
    claimed = store.claim_execution(
        execution.execution_id,
        worker_id="worker-a",
        lease_seconds=30,
    )
    store.mark_waiting(
        claimed.execution_id,
        status="waiting_approval",
        run_id="target-run",
        workflow_task_id="workflow-task",
        wait_id="approval-1",
    )

    reloaded = AutomationStore(tmp_path)
    saved = reloaded.require_definition(definition.automation_id)
    resumed = reloaded.get_execution(execution.execution_id)

    assert saved.target_xpert_version == 3
    assert saved.prompt == definition.prompt
    assert resumed is not None
    assert resumed.status == "waiting_approval"
    assert resumed.wait_id == "approval-1"
    assert "workflow-task" in reloaded.snapshot_path.read_text(encoding="utf-8")


def test_claim_lease_retry_and_dead_letter(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path)
    definition = _definition(store, max_attempts=2)
    execution = store.run_now(definition.automation_id)

    first = store.claim_execution(
        execution.execution_id,
        worker_id="worker-a",
        lease_seconds=30,
        now=execution.available_at,
    )
    with pytest.raises(AutomationConflictError):
        store.claim_execution(
            execution.execution_id,
            worker_id="worker-b",
            lease_seconds=30,
            now=execution.available_at + 1,
        )

    pending = store.fail_execution(first.execution_id, error="temporary")
    assert pending.status == "pending"
    second = store.claim_execution(
        pending.execution_id,
        worker_id="worker-b",
        lease_seconds=30,
        now=pending.available_at,
    )
    dead = store.fail_execution(second.execution_id, error="still failing")
    assert dead.status == "dead_letter"
    assert dead.attempt == 2

    retried = store.retry_execution(dead.execution_id, reset_attempts=True)
    assert retried.status == "pending"
    assert retried.attempt == 0


def test_materialize_due_is_idempotent_and_skips_overlap(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path)
    definition = _definition(store, status="scheduled")
    store.run_now(definition.automation_id)
    due = time.time()
    definition.next_run_at = due

    first = store.materialize_due(now=due)
    second = store.materialize_due(now=due)

    assert len(first) == 1
    assert first[0].status == "skipped"
    assert first[0].error == "Previous occurrence is still active."
    assert second == []


@pytest.mark.asyncio
async def test_coordinator_executes_and_records_automation_run(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path)
    definition = _definition(store)
    execution = store.run_now(definition.automation_id)
    registry = RunRegistry()

    async def execute_target(definition, claimed, parent_run_id):
        assert definition.target_xpert_version == 3
        assert claimed.execution_id == execution.execution_id
        assert parent_run_id
        return AutomationTargetResult(
            output="scheduled result",
            run_id="target-xpert-run",
            workflow_task_id="workflow-task",
        )

    coordinator = AutomationCoordinator(
        store,
        registry,
        execute_target,
        poll_interval=0.1,
        lease_seconds=30,
        max_concurrency=2,
        worker_id="test-worker",
    )

    assert await coordinator.run_once() == 1
    completed = store.get_execution(execution.execution_id)
    runs = await registry.list_runs(run_type="automation")
    checkpoints = await registry.list_checkpoints(runs[0].run_id)

    assert completed is not None
    assert completed.status == "completed"
    assert completed.result == "scheduled result"
    assert runs[0].status == "completed"
    assert {item.event_type for item in checkpoints} >= {
        "automation.started",
        "automation.completed",
    }


@pytest.mark.asyncio
async def test_coordinator_preserves_wait_and_completes_continuation(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path)
    definition = _definition(store)
    execution = store.run_now(definition.automation_id)
    registry = RunRegistry()

    async def execute_target(_definition, _claimed, _parent_run_id):
        return AutomationTargetResult(
            output="",
            run_id="waiting-target-run",
            workflow_task_id="waiting-task",
            waiting_client=True,
            wait_id="client-request-1",
        )

    coordinator = AutomationCoordinator(store, registry, execute_target)
    assert await coordinator.run_once() == 1
    waiting = store.get_execution(execution.execution_id)
    assert waiting is not None
    assert waiting.status == "waiting_client"
    assert waiting.wait_id == "client-request-1"

    await coordinator.complete_waiting(
        execution.execution_id,
        result="resumed result",
        target_run_id="waiting-target-run",
        workflow_task_id="waiting-task",
    )
    completed = store.get_execution(execution.execution_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result == "resumed result"


@pytest.mark.asyncio
async def test_automation_api_exposes_stable_crud_and_execution_shape(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path)
    registry = RunRegistry()

    async def execute_target(_definition, _claimed, _parent_run_id):
        raise AssertionError("API test does not execute targets")

    async def resolve_target(reference: str):
        return SimpleNamespace(xpert_id=reference, slug="published-helper", version=4)

    coordinator = AutomationCoordinator(
        store,
        registry,
        execute_target,
        enabled=False,
    )
    previous = (
        automation_api._store,
        automation_api._coordinator,
        automation_api._target_resolver,
    )
    automation_api.configure_runtime_automations(store, coordinator, resolve_target)
    app = FastAPI()
    app.include_router(automation_api.router)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            created_response = await client.post(
                "/api/runtime/automations",
                json={
                    "name": "API schedule",
                    "prompt": "Run the pinned helper.",
                    "target_xpert_id": "xpert-api",
                    "trigger": {
                        "type": "interval",
                        "interval_seconds": 300,
                        "timezone": "UTC",
                    },
                    "status": "scheduled",
                },
            )
            assert created_response.status_code == 200, created_response.text
            created = created_response.json()
            assert created["target_xpert_version"] == 4

            listing = await client.get("/api/runtime/automations?status=scheduled")
            assert listing.status_code == 200
            assert listing.json()["version"] == "xpert-automations-v1"
            assert listing.json()["items"][0]["automation_id"] == created["automation_id"]
            assert "prompt" not in listing.json()["items"][0]

            manual = await client.post(
                f"/api/runtime/automations/{created['automation_id']}/run-now"
            )
            assert manual.status_code == 200
            execution_id = manual.json()["execution_id"]
            executions = await client.get(
                f"/api/runtime/automation-executions?automation_id={created['automation_id']}"
            )
            assert executions.status_code == 200
            assert executions.json()["items"][0]["execution_id"] == execution_id
    finally:
        automation_api._store, automation_api._coordinator, automation_api._target_resolver = previous


@pytest.mark.asyncio
async def test_automation_toolset_is_pinned_and_scope_safe(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path)
    coordinator = AutomationCoordinator(store, RunRegistry(), lambda *_args: None, enabled=False)  # type: ignore[arg-type]
    provider = AutomationToolsetProvider(store, coordinator)
    metadata = {
        "xpert_id": "xpert-owned",
        "xpert_slug": "owned-helper",
        "xpert_version": 7,
        "runtime_run_type": "xpert",
        "automation_config": {
            "allow_agent_create": True,
            "default_timezone": "Asia/Shanghai",
            "max_runs_per_day": 5,
        },
    }
    created_result = await provider.call_tool(
        RuntimeToolCall(
            "automation_create",
            {
                "name": "Owned schedule",
                "prompt": "Perform the task.",
                "trigger": {"type": "interval", "interval_seconds": 600},
            },
            metadata,
        )
    )
    assert '"target_xpert_version": 7' in created_result.output
    definition = store.list_definitions()[0]
    assert definition.trigger.timezone == "Asia/Shanghai"
    assert definition.budget.max_runs_per_day == 5

    with pytest.raises(RuntimeToolError) as denied:
        await provider.call_tool(
            RuntimeToolCall(
                "automation_get",
                {"automation_id": definition.automation_id},
                {**metadata, "xpert_id": "other-xpert"},
            )
        )
    assert denied.value.code == "automation_scope_denied"

    with pytest.raises(RuntimeToolError) as app_denied:
        await provider.call_tool(
            RuntimeToolCall(
                "automation_list",
                {},
                {**metadata, "runtime_run_type": "xpert_app"},
            )
        )
    assert app_denied.value.code == "automation_app_denied"
