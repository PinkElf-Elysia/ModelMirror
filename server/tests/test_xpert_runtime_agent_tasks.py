from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from server.xpert_runtime import AgentTaskStore, RuntimeEventStore


@pytest.mark.asyncio
async def test_create_and_get_task() -> None:
    store = AgentTaskStore()
    task = await store.create_task("Test Task", "do something")

    assert task.task_id
    assert task.title == "Test Task"
    assert task.input == "do something"
    assert task.status == "pending"

    got = await store.get_task(task.task_id)
    assert got is not None
    assert got.task_id == task.task_id

    assert await store.get_task("nonexistent") is None


@pytest.mark.asyncio
async def test_update_task() -> None:
    store = AgentTaskStore()
    task = await store.create_task("T", "in")

    updated = await store.update_task(task.task_id, status="running")
    assert updated.status == "running"
    assert updated.updated_at >= task.created_at

    finished = await store.update_task(
        task.task_id,
        status="completed",
        result="done",
    )
    assert finished.status == "completed"
    assert finished.result == "done"


@pytest.mark.asyncio
async def test_cancel_task() -> None:
    store = AgentTaskStore()
    task = await store.create_task("T", "in")

    cancelled = await store.cancel_task(task.task_id, reason="user cancelled")

    assert cancelled.status == "cancelled"
    assert cancelled.error == "user cancelled"


@pytest.mark.asyncio
async def test_list_tasks_with_status_filter() -> None:
    store = AgentTaskStore()
    t1 = await store.create_task("A", "a")
    await store.create_task("B", "b")
    await store.update_task(t1.task_id, status="completed")

    all_tasks = await store.list_tasks()
    assert len(all_tasks) == 2
    assert all_tasks[0].created_at >= all_tasks[1].created_at

    completed = await store.list_tasks(status="completed")
    assert len(completed) == 1
    assert completed[0].task_id == t1.task_id


@pytest.mark.asyncio
async def test_handoff_create_and_list() -> None:
    store = AgentTaskStore()
    task = await store.create_task("T", "in")

    handoff = await store.create_handoff(
        task.task_id,
        source_agent="agent_a",
        target_agent="agent_b",
        reason="need expertise",
    )

    assert handoff.handoff_id
    assert handoff.status == "pending"

    all_handoffs = await store.list_handoffs()
    assert len(all_handoffs) == 1

    task_handoffs = await store.list_handoffs(task_id=task.task_id)
    assert len(task_handoffs) == 1

    other = await store.list_handoffs(task_id="other")
    assert other == []


@pytest.mark.asyncio
async def test_handoff_status_transitions() -> None:
    store = AgentTaskStore()
    task = await store.create_task("T", "in")
    handoff = await store.create_handoff(
        task.task_id,
        source_agent="agent_a",
        target_agent="agent_b",
        reason="need expertise",
    )

    accepted = await store.update_handoff_status(
        handoff.handoff_id,
        "accepted",
    )
    assert accepted.status == "accepted"

    completed = await store.update_handoff_status(
        handoff.handoff_id,
        "completed",
        metadata={"result": "done"},
    )
    assert completed.status == "completed"
    assert completed.metadata["result"] == "done"


@pytest.mark.asyncio
async def test_handoff_reject_and_invalid_transition() -> None:
    store = AgentTaskStore()
    task = await store.create_task("T", "in")
    handoff = await store.create_handoff(
        task.task_id,
        source_agent="agent_a",
        target_agent="agent_b",
        reason="need expertise",
    )

    rejected = await store.update_handoff_status(
        handoff.handoff_id,
        "rejected",
        metadata={"reason": "busy"},
    )
    assert rejected.status == "rejected"
    assert rejected.metadata["reason"] == "busy"

    with pytest.raises(ValueError):
        await store.update_handoff_status(handoff.handoff_id, "completed")


@pytest.mark.asyncio
async def test_event_store_records_handoff_events() -> None:
    event_store = RuntimeEventStore()
    store = AgentTaskStore(event_store=event_store)
    task = await store.create_task("T", "in")
    handoff = await store.create_handoff(
        task.task_id,
        source_agent="agent_a",
        target_agent="agent_b",
        reason="need expertise",
    )
    await store.update_handoff_status(handoff.handoff_id, "accepted")
    await store.update_handoff_status(handoff.handoff_id, "completed")

    events = await event_store.list_events(task.task_id)
    event_types = [event.type for event in events]
    assert "agent.handoff.created" in event_types
    assert "agent.handoff.accepted" in event_types
    assert "agent.handoff.completed" in event_types


@pytest.mark.asyncio
async def test_event_store_records_task_events() -> None:
    event_store = RuntimeEventStore()
    store = AgentTaskStore(event_store=event_store)

    task = await store.create_task("ET", "input")
    await store.cancel_task(task.task_id)

    events = await event_store.list_events()
    event_types = [event.type for event in events]
    assert "agent.task.created" in event_types
    assert "agent.task.cancelled" in event_types
    assert "agent.task.updated" in event_types


from server.main import app


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_api_create_task(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/runtime/agent-tasks",
        json={"title": "API Task", "input": "hello"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "API Task"
    assert data["status"] == "pending"
    assert "task_id" in data


@pytest.mark.asyncio
async def test_api_create_task_missing_title(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/runtime/agent-tasks",
        json={"input": "hello"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_get_task_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/runtime/agent-tasks/nonexistent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_cancel_task(client: httpx.AsyncClient) -> None:
    create_response = await client.post(
        "/api/runtime/agent-tasks",
        json={"title": "Cancel Test", "input": "x"},
    )
    task_id = create_response.json()["task_id"]

    cancel_response = await client.post(
        f"/api/runtime/agent-tasks/{task_id}/cancel",
        json={"reason": "test cancel"},
    )

    assert cancel_response.status_code == 200
    data = cancel_response.json()
    assert data["status"] == "cancelled"
    assert data["error"] == "test cancel"

    runs_response = await client.get("/api/runtime/runs?run_type=agent_task&limit=50")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    task_run = next(item for item in runs if item["source_id"] == task_id)
    assert task_run["status"] == "cancelled"
    assert task_run["error"] == "test cancel"


@pytest.mark.asyncio
async def test_api_create_and_list_handoffs(client: httpx.AsyncClient) -> None:
    create_response = await client.post(
        "/api/runtime/agent-tasks",
        json={"title": "Handoff API Task", "input": "x", "assigned_agent": "agent_a"},
    )
    task_id = create_response.json()["task_id"]

    handoff_response = await client.post(
        f"/api/runtime/agent-tasks/{task_id}/handoffs",
        json={"target_agent": "agent_b", "reason": "need expertise"},
    )

    assert handoff_response.status_code == 200, handoff_response.text
    handoff = handoff_response.json()
    assert handoff["task_id"] == task_id
    assert handoff["source_agent"] == "agent_a"
    assert handoff["target_agent"] == "agent_b"
    assert handoff["status"] == "pending"

    list_response = await client.get(f"/api/runtime/agent-tasks/{task_id}/handoffs")
    assert list_response.status_code == 200
    handoffs = list_response.json()
    assert any(item["handoff_id"] == handoff["handoff_id"] for item in handoffs)


@pytest.mark.asyncio
async def test_api_global_handoff_list_filters(client: httpx.AsyncClient) -> None:
    task_a = await client.post(
        "/api/runtime/agent-tasks",
        json={"title": "Handoff Filter A", "input": "x", "assigned_agent": "a"},
    )
    task_b = await client.post(
        "/api/runtime/agent-tasks",
        json={"title": "Handoff Filter B", "input": "x", "assigned_agent": "b"},
    )
    task_a_id = task_a.json()["task_id"]
    task_b_id = task_b.json()["task_id"]

    handoff_a = await client.post(
        f"/api/runtime/agent-tasks/{task_a_id}/handoffs",
        json={"target_agent": "review-agent", "reason": "review this"},
    )
    handoff_b = await client.post(
        f"/api/runtime/agent-tasks/{task_b_id}/handoffs",
        json={"target_agent": "qa-agent", "reason": "qa this"},
    )
    assert handoff_a.status_code == 200, handoff_a.text
    assert handoff_b.status_code == 200, handoff_b.text
    handoff_a_id = handoff_a.json()["handoff_id"]

    await client.post(
        f"/api/runtime/agent-handoffs/{handoff_a_id}/accept",
        json={"accepted_by": "queue-operator"},
    )

    task_filter = await client.get(
        f"/api/runtime/agent-handoffs?task_id={task_a_id}&limit=20",
    )
    assert task_filter.status_code == 200, task_filter.text
    assert [item["task_id"] for item in task_filter.json()] == [task_a_id]

    status_filter = await client.get(
        "/api/runtime/agent-handoffs?status=accepted&limit=20",
    )
    assert status_filter.status_code == 200, status_filter.text
    assert any(item["handoff_id"] == handoff_a_id for item in status_filter.json())
    assert all(item["status"] == "accepted" for item in status_filter.json())

    target_filter = await client.get(
        "/api/runtime/agent-handoffs?target_agent=qa-agent&limit=1",
    )
    assert target_filter.status_code == 200, target_filter.text
    target_handoffs = target_filter.json()
    assert len(target_handoffs) == 1
    assert target_handoffs[0]["target_agent"] == "qa-agent"

    source_filter = await client.get(
        "/api/runtime/agent-handoffs?source_agent=a&limit=20",
    )
    assert source_filter.status_code == 200, source_filter.text
    assert any(item["handoff_id"] == handoff_a_id for item in source_filter.json())
    assert all(item["source_agent"] == "a" for item in source_filter.json())

    created_after_filter = await client.get(
        "/api/runtime/agent-handoffs?created_after=9999999999&limit=20",
    )
    assert created_after_filter.status_code == 200, created_after_filter.text
    assert created_after_filter.json() == []


@pytest.mark.asyncio
async def test_api_handoff_status_transitions(client: httpx.AsyncClient) -> None:
    create_response = await client.post(
        "/api/runtime/agent-tasks",
        json={"title": "Handoff Transition", "input": "x"},
    )
    task_id = create_response.json()["task_id"]
    handoff_response = await client.post(
        f"/api/runtime/agent-tasks/{task_id}/handoffs",
        json={
            "source_agent": "agent_a",
            "target_agent": "agent_b",
            "reason": "need expertise",
        },
    )
    handoff_id = handoff_response.json()["handoff_id"]

    accept_response = await client.post(
        f"/api/runtime/agent-handoffs/{handoff_id}/accept",
        json={"accepted_by": "queue-operator"},
    )
    assert accept_response.status_code == 200
    accepted = accept_response.json()
    assert accepted["status"] == "accepted"
    assert accepted["metadata"]["accepted_by"] == "queue-operator"
    assert isinstance(accepted["metadata"]["accepted_at"], float)

    complete_response = await client.post(
        f"/api/runtime/agent-handoffs/{handoff_id}/complete",
        json={"completed_by": "queue-operator", "result": "done"},
    )
    assert complete_response.status_code == 200
    completed = complete_response.json()
    assert completed["status"] == "completed"
    assert completed["metadata"]["completed_by"] == "queue-operator"
    assert isinstance(completed["metadata"]["completed_at"], float)
    assert completed["metadata"]["result"] == "done"

    runs_response = await client.get(
        "/api/runtime/runs?run_type=agent_handoff&limit=50",
    )
    assert runs_response.status_code == 200
    runs = runs_response.json()
    handoff_run = next(item for item in runs if item["source_id"] == handoff_id)
    assert handoff_run["status"] == "completed"
    assert handoff_run["metadata"]["handoff_status"] == "completed"
    assert handoff_run["metadata"]["completed_by"] == "queue-operator"
    assert handoff_run["metadata"]["result"] == "done"

    list_response = await client.get(
        f"/api/runtime/agent-handoffs?task_id={task_id}&status=completed",
    )
    assert list_response.status_code == 200, list_response.text
    listed = list_response.json()
    assert any(item["handoff_id"] == handoff_id for item in listed)
    assert all(item["status"] == "completed" for item in listed)


@pytest.mark.asyncio
async def test_api_handoff_invalid_transition(client: httpx.AsyncClient) -> None:
    create_response = await client.post(
        "/api/runtime/agent-tasks",
        json={"title": "Handoff Invalid", "input": "x"},
    )
    task_id = create_response.json()["task_id"]
    handoff_response = await client.post(
        f"/api/runtime/agent-tasks/{task_id}/handoffs",
        json={
            "source_agent": "agent_a",
            "target_agent": "agent_b",
            "reason": "need expertise",
        },
    )
    handoff_id = handoff_response.json()["handoff_id"]

    complete_response = await client.post(
        f"/api/runtime/agent-handoffs/{handoff_id}/complete",
    )
    assert complete_response.status_code == 400

    reject_response = await client.post(
        f"/api/runtime/agent-handoffs/{handoff_id}/reject",
        json={"rejected_by": "queue-operator", "reason": "not mine"},
    )
    assert reject_response.status_code == 200
    rejected = reject_response.json()
    assert rejected["status"] == "rejected"
    assert rejected["metadata"]["rejected_by"] == "queue-operator"
    assert isinstance(rejected["metadata"]["rejected_at"], float)
    assert rejected["metadata"]["reason"] == "not mine"


@pytest.mark.asyncio
async def test_api_list_tasks(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/runtime/agent-tasks")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
