from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.agent_workspace.api import set_agent_workspace_for_tests
from server.agent_workspace.store import AgentStateStore
from server.main import app


@pytest_asyncio.fixture
async def client(tmp_path: Path):
    store = AgentStateStore(root=tmp_path / "workspace")
    set_agent_workspace_for_tests(store, enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        yield async_client
    set_agent_workspace_for_tests(None, enabled=None)


@pytest.mark.asyncio
async def test_disabled_flag_hides_agent_api(tmp_path: Path) -> None:
    store = AgentStateStore(root=tmp_path / "workspace")
    set_agent_workspace_for_tests(store, enabled=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        status = await client.get("/api/agent-workspace/status")
        agents = await client.get("/api/agent-workspace/agents")
    set_agent_workspace_for_tests(None, enabled=None)

    assert status.status_code == 200
    assert status.json() == {
        "enabled": False,
        "version": "agent-workspace-r2",
        "runtime_enabled": False,
        "engine_shadow_enabled": False,
    }
    assert agents.status_code == 404
    assert not (tmp_path / "workspace").exists()


@pytest.mark.asyncio
async def test_agent_config_round_trip_and_conflict(client: httpx.AsyncClient) -> None:
    listed = await client.get("/api/agent-workspace/agents")
    assert listed.status_code == 200, listed.text
    assert [item["agent_id"] for item in listed.json()["agents"]] == [
        "default_agent"
    ]

    fetched = await client.get("/api/agent-workspace/agents/default_agent")
    payload = fetched.json()
    payload["config"]["max_turns"] = 25
    updated = await client.put(
        "/api/agent-workspace/agents/default_agent",
        json={
            "expected_revision": payload["revision"],
            "config": payload["config"],
            "agents_md": "# User behavior",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config"]["max_turns"] == 25

    stale = await client.put(
        "/api/agent-workspace/agents/default_agent",
        json={
            "expected_revision": payload["revision"],
            "config": payload["config"],
            "agents_md": "stale",
        },
    )
    assert stale.status_code == 409

    reset = await client.post(
        "/api/agent-workspace/agents/default_agent/reset",
        json={"expected_revision": updated.json()["revision"]},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["config"]["max_turns"] == 100
    assert reset.json()["agents_md"] == "# User behavior"


@pytest.mark.asyncio
async def test_api_rejects_invalid_id_and_general_agent_delete(
    client: httpx.AsyncClient,
) -> None:
    invalid = await client.post(
        "/api/agent-workspace/agents",
        json={"agent_id": "../escape", "name": "Bad", "description": ""},
    )
    assert invalid.status_code == 422

    await client.get("/api/agent-workspace/agents")
    deleted = await client.delete(
        "/api/agent-workspace/agents/default_agent"
    )
    assert deleted.status_code == 409
