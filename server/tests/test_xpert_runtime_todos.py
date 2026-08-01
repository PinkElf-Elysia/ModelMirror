from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.main import app
from server.xpert_runtime.todo_api import (
    configure_runtime_todo_store,
    get_runtime_todo_store,
)
from server.xpert_runtime.todo_store import (
    RuntimeTodoConflictError,
    RuntimeTodoStore,
)
from server.xpert_runtime.todo_toolset import TodoToolsetProvider
from server.xpert_runtime.toolset import RuntimeToolCall, RuntimeToolError


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    previous = get_runtime_todo_store()
    configure_runtime_todo_store(RuntimeTodoStore(tmp_path / "api"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client
    configure_runtime_todo_store(previous)


def test_todo_store_persists_scopes_and_rejects_stale_revisions(tmp_path: Path) -> None:
    store = RuntimeTodoStore(tmp_path)
    first = store.create_item(
        scope_type="conversation",
        scope_id="conversation-a",
        title="Draft plan",
        priority=2,
    )
    store.create_item(
        scope_type="conversation",
        scope_id="conversation-b",
        title="Other conversation",
    )
    updated = store.update_item(
        first.todo_id,
        scope_type="conversation",
        scope_id="conversation-a",
        revision=first.revision,
        patch={"status": "in_progress", "order": 3},
    )

    with pytest.raises(RuntimeTodoConflictError):
        store.update_item(
            first.todo_id,
            scope_type="conversation",
            scope_id="conversation-a",
            revision=1,
            patch={"status": "completed"},
        )

    reloaded = RuntimeTodoStore(tmp_path)
    restored = reloaded.list_items(
        scope_type="conversation",
        scope_id="conversation-a",
    )
    assert len(restored) == 1
    assert restored[0].status == "in_progress"
    assert restored[0].revision == updated.revision
    assert reloaded.list_items(
        scope_type="conversation",
        scope_id="conversation-b",
    )[0].title == "Other conversation"


def test_app_run_todos_are_ephemeral(tmp_path: Path) -> None:
    store = RuntimeTodoStore(tmp_path)
    store.create_item(
        scope_type="app_run",
        scope_id="public-run",
        title="Temporary work",
    )

    assert store.list_items(scope_type="app_run", scope_id="public-run")
    assert RuntimeTodoStore(tmp_path).list_items(
        scope_type="app_run",
        scope_id="public-run",
    ) == []


@pytest.mark.asyncio
async def test_todo_toolset_creates_lists_and_updates_current_scope(tmp_path: Path) -> None:
    provider = TodoToolsetProvider(RuntimeTodoStore(tmp_path))
    metadata = {
        "todo_scope_type": "goal",
        "todo_scope_id": "goal-1:step-1",
        "run_id": "run-1",
    }

    created = await provider.call_tool(
        RuntimeToolCall(
            tool_name="todo_create",
            arguments={"title": "Research sources", "priority": 3},
            metadata=metadata,
        )
    )
    item = json.loads(created.output)
    assert item["source_run_id"] == "run-1"

    updated = await provider.call_tool(
        RuntimeToolCall(
            tool_name="todo_update",
            arguments={
                "todo_id": item["todo_id"],
                "revision": item["revision"],
                "status": "completed",
            },
            metadata=metadata,
        )
    )
    assert json.loads(updated.output)["status"] == "completed"

    listed = await provider.call_tool(
        RuntimeToolCall(
            tool_name="todo_list",
            arguments={"status": "completed"},
            metadata=metadata,
        )
    )
    assert [entry["title"] for entry in json.loads(listed.output)] == [
        "Research sources"
    ]


@pytest.mark.asyncio
async def test_todo_toolset_enforces_scope_item_limit(tmp_path: Path) -> None:
    provider = TodoToolsetProvider(RuntimeTodoStore(tmp_path))
    metadata = {
        "todo_scope_type": "workflow",
        "todo_scope_id": "task-1:agent-1",
        "todo_max_items": 1,
    }
    await provider.call_tool(
        RuntimeToolCall(
            tool_name="todo_create",
            arguments={"title": "First"},
            metadata=metadata,
        )
    )

    with pytest.raises(RuntimeToolError) as captured:
        await provider.call_tool(
            RuntimeToolCall(
                tool_name="todo_create",
                arguments={"title": "Second"},
                metadata=metadata,
            )
        )

    assert captured.value.code == "todo_limit_reached"


@pytest.mark.asyncio
async def test_todo_api_crud_and_revision_conflict(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/runtime/todos",
        json={
            "scope_type": "conversation",
            "scope_id": "conversation-api",
            "title": "Review the output",
        },
    )
    assert created.status_code == 200, created.text
    item = created.json()

    stale = await client.patch(
        f"/api/runtime/todos/{item['todo_id']}",
        json={
            "scope_type": "conversation",
            "scope_id": "conversation-api",
            "revision": item["revision"] + 1,
            "status": "completed",
        },
    )
    assert stale.status_code == 409

    updated = await client.patch(
        f"/api/runtime/todos/{item['todo_id']}",
        json={
            "scope_type": "conversation",
            "scope_id": "conversation-api",
            "revision": item["revision"],
            "status": "completed",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "completed"

    listed = await client.get(
        "/api/runtime/todos",
        params={
            "scope_type": "conversation",
            "scope_id": "conversation-api",
            "status": "completed",
        },
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    archived = await client.delete(
        f"/api/runtime/todos/{item['todo_id']}",
        params={
            "scope_type": "conversation",
            "scope_id": "conversation-api",
            "revision": updated.json()["revision"],
        },
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
