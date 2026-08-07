from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.agent_workspace.api import set_agent_workspace_for_tests
from server.agent_workspace.gateway import GatewayTurn
from server.agent_workspace.runtime import AgentRuntimeService
from server.agent_workspace.runtime_models import SessionCreateRequest
from server.agent_workspace.runtime_store import AgentRuntimeStore
from server.agent_workspace.store import AgentStateStore
from server.agent_workspace.tools import ProcessRegistry
from server.main import app


class FakeGateway:
    def configuration(self):
        return "https://fake", "fake", "fake"

    async def stream_turn(self, *, on_delta, **_kwargs):
        await on_delta("text_delta", {"delta": "API completed"})
        return GatewayTurn(
            content="API completed",
            tool_calls=(),
            finish_reason="stop",
            model_id="test/model",
        )


@pytest_asyncio.fixture
async def runtime_client(tmp_path: Path):
    root = tmp_path / "workspace"
    state = AgentStateStore(root=root)
    runtime = AgentRuntimeService(
        state_store=state,
        runtime_store=AgentRuntimeStore(root),
        gateway=FakeGateway(),
        process_registry=ProcessRegistry(allow_commands=False),
    )
    set_agent_workspace_for_tests(state, enabled=True, runtime_service=runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client, runtime
    await runtime.shutdown()
    set_agent_workspace_for_tests(None, enabled=None)


@pytest.mark.asyncio
async def test_session_task_refresh_and_sse_replay(runtime_client) -> None:
    client, runtime = runtime_client
    created = await client.post(
        "/api/agent-workspace/sessions",
        json={
            "agent_id": "default_agent",
            "model_id": "test/model",
            "thinking_level": "medium",
            "approval_mode": "always-ask",
            "skillset_id": "general-agent-default",
            "title": "API Session",
        },
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    task_response = await client.post(
        f"/api/agent-workspace/sessions/{session_id}/tasks",
        json={"prompt": "hello"},
    )
    assert task_response.status_code == 202, task_response.text
    task = await runtime.wait_task(task_response.json()["task_id"])
    assert task.status == "completed"

    detail = await client.get(f"/api/agent-workspace/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["messages"][-1]["content"] == "API completed"

    stream = await client.get(
        f"/api/agent-workspace/sessions/{session_id}/events?follow=false",
        headers={"Last-Event-ID": "1"},
    )
    assert stream.status_code == 200
    assert "id: " in stream.text
    assert "event: completed" in stream.text
    assert "session_created" not in stream.text


@pytest.mark.asyncio
async def test_workspace_api_rejects_escape_and_previews_text(runtime_client) -> None:
    client, runtime = runtime_client
    created = await client.post(
        "/api/agent-workspace/sessions",
        json={
            "agent_id": "default_agent",
            "model_id": "test/model",
            "title": "Files",
        },
    )
    session_id = created.json()["session_id"]
    workspace = runtime.store.session_workspace(session_id)
    (workspace / "result.txt").write_text("hello", encoding="utf-8")

    listed = await client.get(f"/api/agent-workspace/sessions/{session_id}/workspace")
    assert listed.status_code == 200
    assert any(item["path"] == "result.txt" for item in listed.json()["entries"])
    preview = await client.get(
        f"/api/agent-workspace/sessions/{session_id}/workspace/file",
        params={"path": "result.txt"},
    )
    assert preview.json()["content"] == "hello"
    escaped = await client.get(
        f"/api/agent-workspace/sessions/{session_id}/workspace/file",
        params={"path": "../secret"},
    )
    assert escaped.status_code == 400


@pytest.mark.asyncio
async def test_session_approval_mode_can_be_changed_through_the_api(
    runtime_client,
) -> None:
    client, runtime = runtime_client
    created = await client.post(
        "/api/agent-workspace/sessions",
        json={
            "agent_id": "default_agent",
            "model_id": "test/model",
            "title": "Approval mode",
            "approval_mode": "always-ask",
        },
    )
    session_id = created.json()["session_id"]

    changed = await client.patch(
        f"/api/agent-workspace/sessions/{session_id}",
        json={"approval_mode": "allow-all"},
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["approval_mode"] == "allow-all"
    assert runtime.store.get_session(session_id).approval_mode == "allow-all"


@pytest.mark.asyncio
async def test_retry_generation_endpoint_preserves_task_kind(runtime_client) -> None:
    client, runtime = runtime_client
    session = await runtime.create_session(
        SessionCreateRequest(
            agent_id="default_agent",
            model_id="test/model",
            title="Failed generation",
            approval_mode="allow-all",
        )
    )
    failed = runtime.store.create_task(
        session.session_id,
        prompt="controlled generation prompt",
        kind="generate_agent",
        model_id="test/model",
        thinking_level="medium",
        approval_mode="allow-all",
    )
    runtime.store.update_task(
        failed.task_id,
        status="failed",
        error="temporary gateway failure",
    )

    retried = await client.post(
        f"/api/agent-workspace/tasks/{failed.task_id}/retry-generation"
    )

    assert retried.status_code == 202, retried.text
    assert retried.json()["kind"] == "generate_agent"
    assert retried.json()["session_id"] == session.session_id
