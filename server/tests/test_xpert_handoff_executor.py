from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import server.main as main_module
from server.main import app
from server.xpert_runtime.agent_tasks import AgentTaskStore
from server.xpert_runtime.handoff_executor import (
    HandoffExecutionResult,
    HandoffExecutor,
    HandoffPermanentError,
)
from server.xpert_runtime.run_registry import RunRegistry
from server.xperts import XpertStore, set_xpert_store_for_tests


@pytest.mark.asyncio
async def test_agent_task_store_persists_claim_and_requeue(tmp_path: Path) -> None:
    storage_dir = tmp_path / "agent-task-store"
    store = AgentTaskStore(storage_dir=storage_dir)
    task = await store.create_task(
        "Persistent task",
        "do work",
        assigned_agent="xpert:specialist",
    )
    handoff = await store.create_handoff(
        task.task_id,
        source_agent="manager",
        target_agent="xpert:specialist",
        reason="delegate",
        metadata={
            "execution_mode": "xpert_auto",
            "ready_for_execution": True,
        },
    )

    claimed = await store.claim_handoff(
        handoff.handoff_id,
        worker_id="worker-a",
        lease_seconds=30,
        max_attempts=3,
    )
    assert claimed.status == "accepted"
    assert claimed.metadata["attempts"] == 1

    reloaded = AgentTaskStore(storage_dir=storage_dir)
    persisted = await reloaded.get_handoff(handoff.handoff_id)
    persisted_task = await reloaded.get_task(task.task_id)
    assert persisted is not None
    assert persisted_task is not None
    assert persisted.status == "accepted"
    assert persisted.metadata["lease_owner"] == "worker-a"

    await reloaded.update_handoff_status(
        handoff.handoff_id,
        "dead_letter",
        metadata={"last_error": "permanent failure"},
    )
    requeued = await reloaded.requeue_handoff(
        handoff.handoff_id,
        operator="operator",
        reset_attempts=True,
        repin_version=True,
    )
    assert requeued.status == "pending"
    assert requeued.metadata["attempts"] == 0
    assert "last_error" not in requeued.metadata


@pytest.mark.asyncio
async def test_handoff_claim_is_atomic_and_manual_targets_are_not_executable() -> None:
    store = AgentTaskStore()
    task = await store.create_task("Atomic task", "payload")
    automatic = await store.create_handoff(
        task.task_id,
        "manager",
        "xpert:specialist",
        "delegate",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
    )
    await store.create_handoff(
        task.task_id,
        "manager",
        "review-agent",
        "manual review",
        metadata={"execution_mode": "manual", "ready_for_execution": True},
    )

    await store.claim_handoff(automatic.handoff_id, worker_id="worker-a")
    with pytest.raises(ValueError, match="already leased"):
        await store.claim_handoff(automatic.handoff_id, worker_id="worker-b")
    assert await store.list_executable_handoffs() == []


@pytest.mark.asyncio
async def test_handoff_executor_retries_then_completes() -> None:
    store = AgentTaskStore()
    registry = RunRegistry()
    task = await store.create_task("Retry task", "payload")
    handoff = await store.create_handoff(
        task.task_id,
        "manager",
        "xpert:specialist",
        "delegate",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
    )
    task_run = await registry.create_run(
        "agent_task",
        task.title,
        source_id=task.task_id,
    )
    handoff_run = await registry.create_run(
        "agent_handoff",
        "manager -> specialist",
        source_id=handoff.handoff_id,
        parent_run_id=task_run.run_id,
    )
    calls = 0

    async def execute_target(*_args: Any) -> HandoffExecutionResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary gateway failure")
        return HandoffExecutionResult(
            output="specialist result",
            run_id="xpert-run-1",
            xpert_id="xpert-1",
            xpert_slug="specialist",
            xpert_version=2,
        )

    executor = HandoffExecutor(
        store,
        registry,
        execute_target,
        max_attempts=3,
        worker_id="test-worker",
    )
    first = await executor.execute_handoff(handoff.handoff_id)
    assert first.status == "retry_wait"
    assert first.metadata["attempts"] == 1
    await store.update_handoff_metadata(handoff.handoff_id, {"next_attempt_at": 0})

    completed = await executor.execute_handoff(handoff.handoff_id)
    assert completed.status == "completed"
    assert completed.metadata["target_xpert_version"] == 2
    updated_task = await store.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == "completed"
    assert updated_task.result == "specialist result"

    checkpoints = await registry.list_checkpoints(handoff_run.run_id)
    checkpoint_types = {item.event_type for item in checkpoints}
    assert "agent_handoff.retry_scheduled" in checkpoint_types
    assert "agent_handoff.completed" in checkpoint_types


@pytest.mark.asyncio
async def test_handoff_executor_preserves_waiting_approval_without_reclaim() -> None:
    store = AgentTaskStore()
    registry = RunRegistry()
    task = await store.create_task("Approval task", "payload")
    handoff = await store.create_handoff(
        task.task_id,
        "manager",
        "xpert:specialist",
        "delegate",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
    )
    task_run = await registry.create_run(
        "agent_task",
        task.title,
        source_id=task.task_id,
    )
    handoff_run = await registry.create_run(
        "agent_handoff",
        "manager -> specialist",
        source_id=handoff.handoff_id,
        parent_run_id=task_run.run_id,
    )

    async def execute_target(*_args: Any) -> HandoffExecutionResult:
        return HandoffExecutionResult(
            output="",
            run_id="xpert-run-waiting",
            xpert_id="xpert-1",
            xpert_slug="specialist",
            xpert_version=1,
            waiting_approval=True,
            approval_id="approval-1",
            task_id="workflow-task-1",
        )

    executor = HandoffExecutor(store, registry, execute_target, worker_id="worker")
    waiting = await executor.execute_handoff(handoff.handoff_id)

    assert waiting.status == "waiting_approval"
    assert waiting.metadata["approval_id"] == "approval-1"
    assert waiting.metadata["lease_token"] == ""
    updated_task = await store.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == "waiting_approval"
    assert await store.list_executable_handoffs() == []
    assert (await registry.get_run(task_run.run_id)).status == "waiting"
    assert (await registry.get_run(handoff_run.run_id)).status == "waiting"
    checkpoints = await registry.list_checkpoints(handoff_run.run_id)
    assert any(
        item.event_type == "agent_handoff.waiting_approval"
        for item in checkpoints
    )


@pytest.mark.asyncio
async def test_handoff_executor_dead_letters_permanent_failure() -> None:
    store = AgentTaskStore()
    registry = RunRegistry()
    task = await store.create_task("Dead task", "payload")
    handoff = await store.create_handoff(
        task.task_id,
        "manager",
        "xpert:missing",
        "delegate",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
    )
    await registry.create_run(
        "agent_task",
        task.title,
        source_id=task.task_id,
    )
    handoff_run = await registry.create_run(
        "agent_handoff",
        "manager -> missing",
        source_id=handoff.handoff_id,
    )

    async def execute_target(*_args: Any) -> HandoffExecutionResult:
        raise HandoffPermanentError("Published Xpert not found")

    executor = HandoffExecutor(store, registry, execute_target)
    dead = await executor.execute_handoff(handoff.handoff_id)
    assert dead.status == "dead_letter"
    assert dead.metadata["last_error"] == "Published Xpert not found"
    failed_task = await store.get_task(task.task_id)
    assert failed_task is not None
    assert failed_task.status == "failed"

    checkpoints = await registry.list_checkpoints(handoff_run.run_id)
    assert any(item.event_type == "agent_handoff.dead_letter" for item in checkpoints)


@pytest.mark.asyncio
async def test_handoff_executor_runs_two_handoffs_concurrently() -> None:
    store = AgentTaskStore()
    registry = RunRegistry()
    handoff_ids: list[str] = []
    for index in range(2):
        task = await store.create_task(f"Concurrent {index}", f"payload {index}")
        handoff = await store.create_handoff(
            task.task_id,
            "goal",
            f"xpert:specialist-{index}",
            "parallel goal step",
            metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
        )
        handoff_ids.append(handoff.handoff_id)

    active = 0
    peak_active = 0

    async def execute_target(
        handoff,
        _task,
        _parent_run_id,
    ) -> HandoffExecutionResult:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.03)
        active -= 1
        return HandoffExecutionResult(
            output=handoff.handoff_id,
            run_id=f"run-{handoff.handoff_id}",
            xpert_id=handoff.target_agent,
            xpert_slug=handoff.target_agent,
            xpert_version=1,
        )

    executor = HandoffExecutor(
        store,
        registry,
        execute_target,
        max_concurrency=2,
        worker_id="parallel-worker",
    )
    processed = await executor.run_once()
    assert processed == 2
    assert peak_active == 2
    for handoff_id in handoff_ids:
        handoff = await store.get_handoff(handoff_id)
        assert handoff is not None
        assert handoff.status == "completed"
    status = await executor.status()
    assert status["max_concurrency"] == 2


@pytest.mark.asyncio
async def test_handoff_executor_status_and_requeue_api() -> None:
    main_module.handoff_executor = None
    task = await main_module.agent_task_store.create_task(
        "API requeue task",
        "payload",
    )
    handoff = await main_module.agent_task_store.create_handoff(
        task.task_id,
        "manager",
        "xpert:missing-api-target",
        "delegate",
        metadata={"execution_mode": "xpert_auto", "ready_for_execution": True},
    )
    await main_module.agent_task_store.update_handoff_status(
        handoff.handoff_id,
        "dead_letter",
        metadata={"last_error": "missing target", "attempts": 3},
    )

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            status_response = await client.get(
                "/api/runtime/handoff-executor/status"
            )
            requeue_response = await client.post(
                f"/api/runtime/agent-handoffs/{handoff.handoff_id}/requeue",
                json={
                    "operator": "test-operator",
                    "reset_attempts": True,
                    "repin_version": True,
                },
            )
        assert status_response.status_code == 200
        assert status_response.json()["enabled"] is True
        assert requeue_response.status_code == 200, requeue_response.text
        payload = requeue_response.json()
        assert payload["status"] == "pending"
        assert payload["metadata"]["attempts"] == 0
        assert payload["metadata"]["requeued_by"] == "test-operator"
    finally:
        main_module.handoff_executor = None


@pytest.mark.asyncio
async def test_published_manager_waits_for_specialist_xpert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = XpertStore(tmp_path / "xperts")
    set_xpert_store_for_tests(store)
    main_module.handoff_executor = None
    calls = 0

    async def fake_stream_workflow_llm_text(
        model_id: str,
        prompt: str,
        *,
        system_prompt: str | None = None,
    ):
        nonlocal calls
        calls += 1
        yield "delegate packet" if calls == 1 else "specialist result"

    monkeypatch.setattr(
        main_module,
        "get_llm_gateway_config",
        lambda: ("http://test-gateway.local/v1/chat/completions", "test-key"),
    )
    monkeypatch.setattr(
        main_module,
        "stream_workflow_llm_text",
        fake_stream_workflow_llm_text,
    )

    specialist = store.create_xpert(name="Specialist", slug="specialist")
    store.publish_xpert(
        specialist.id,
        expected_revision=specialist.draft_revision,
    )
    manager = store.create_xpert(name="Manager", slug="manager")
    draft = manager.draft.model_dump(mode="json")
    workflow = draft["workflow"]
    workflow["nodes"].append(
        {
            "id": "handoff-router-1",
            "type": "handoff_router",
            "position": {"x": 620, "y": 140},
            "data": {
                "kind": "handoff_router",
                "title": "Delegate to specialist",
                "description": "Run the published specialist Xpert.",
                "sourceVariable": "agent_output",
                "taskTitle": "Specialist task",
                "sourceAgent": "manager",
                "targetAgent": "xpert:specialist",
                "reasonTemplate": "Complete the delegated work.",
                "executionMode": "xpert_auto",
                "waitForCompletion": "true",
                "resultVariable": "agent_output",
                "waitTimeoutSeconds": "30",
                "outputVariable": "agent_handoff_id",
            },
        }
    )
    workflow["edges"] = [
        {"id": "edge-input-agent", "source": "input-1", "target": "workflow-agent-1"},
        {
            "id": "edge-agent-handoff",
            "source": "workflow-agent-1",
            "target": "handoff-router-1",
        },
        {
            "id": "edge-handoff-output",
            "source": "handoff-router-1",
            "target": "output-1",
        },
    ]
    updated_manager = store.update_xpert(manager.id, {"draft": draft})
    store.publish_xpert(
        manager.id,
        expected_revision=updated_manager.draft_revision,
    )

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                f"/api/xperts/{manager.id}/run",
                json={"message": "Coordinate this task"},
            )
        assert response.status_code == 200, response.text
        events = _parse_sse_events(response.text)
        meta = next(item for item in events if item.get("event") == "workflow_meta")
        completed = next(item for item in events if item.get("event") == "workflow_end")
        assert completed["final_output"] == "specialist result"
        assert calls == 2

        handoffs = await main_module.agent_task_store.list_handoffs()
        handoff = next(
            item
            for item in handoffs
            if item.target_agent == "xpert:specialist"
        )
        assert handoff.status == "completed"
        assert handoff.metadata["target_xpert_id"] == specialist.id
        assert handoff.metadata["target_xpert_version"] == 1

        handoff_runs = await main_module.run_registry.list_runs(
            run_type="agent_handoff",
            source_id=handoff.handoff_id,
        )
        assert len(handoff_runs) == 1
        target_runs = await main_module.run_registry.list_runs(
            run_type="xpert",
            source_id=specialist.id,
        )
        target_run = next(
            item for item in target_runs if item.parent_run_id == handoff_runs[0].run_id
        )
        assert handoff_runs[0].parent_run_id == meta["run_id"]
        assert target_run.status == "completed"
    finally:
        main_module.handoff_executor = None
        set_xpert_store_for_tests(None)


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events
