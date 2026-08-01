from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.xpert_runtime.events import RuntimeEventStore


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_runtime_events_missing_task_returns_404(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/workflow/runtime-events/nonexistent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_runtime_event_store_record_and_list() -> None:
    store = RuntimeEventStore()

    await store.record_event(
        "tool.call.started",
        task_id="t1",
        payload={"tool_name": "fetch"},
    )

    events = await store.list_events(task_id="t1")
    assert len(events) == 1
    assert events[0].type == "tool.call.started"
    assert events[0].task_id == "t1"
    assert events[0].payload == {"tool_name": "fetch"}

    all_events = await store.list_events()
    assert len(all_events) == 1

    other_events = await store.list_events(task_id="other")
    assert other_events == []


@pytest.mark.asyncio
async def test_workflow_run_creates_task_with_event_store(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "obs-test",
        "title": "obs test",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "user_input"},
            },
        ],
        "edges": [{"id": "e1", "source": "input", "target": "output"}],
    }

    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": workflow,
            "inputs": {"user_input": "hello"},
        },
    )
    assert response.status_code == 200, response.text

    task_id = _extract_task_id(response.text)
    assert task_id is not None, response.text[:500]

    event_response = await client.get(f"/api/workflow/runtime-events/{task_id}")
    assert event_response.status_code == 200, event_response.text
    payload = event_response.json()

    assert payload["task_id"] == task_id
    assert isinstance(payload["events"], list)
    assert isinstance(payload["event_count"], int)
    assert isinstance(payload["tool_audit_records"], list)
    assert isinstance(payload["tool_audit_count"], int)


def _extract_task_id(sse_text: str) -> str | None:
    for line in sse_text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "workflow_meta":
            task_id = payload.get("task_id")
            return task_id if isinstance(task_id, str) else None
    return None
