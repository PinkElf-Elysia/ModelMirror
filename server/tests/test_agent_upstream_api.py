from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.agent_upstream.api import set_engine_shadow_for_tests
from server.agent_upstream.models import (
    EngineShadowRunCreate,
    ResolvedShadowModel,
)
from server.agent_upstream.store import EngineShadowConflict, EngineShadowStore
from server.main import app


class _ApiShadowService:
    def __init__(self, root: Path) -> None:
        self.store = EngineShadowStore(root)

    async def create_run(self, payload: EngineShadowRunCreate):
        model = ResolvedShadowModel(
            requested_base_id=payload.model_base_id,
            invocation_id="deepseek/deepseek-v4-flash-0731",
            context_window=1_048_576,
            max_output_tokens=32_000,
        )
        record = self.store.create_run(payload, model)
        workspace = self.store.workspace(record.run_id)
        (workspace / "index.html").write_text(
            "<!doctype html><title>Shadow candidate</title>", encoding="utf-8"
        )
        return self.store.mark_running(record.run_id)

    def list_runs(self, *, limit: int = 100):
        return self.store.list_runs(limit=limit)

    def get_detail(self, run_id: str):
        return self.store.get_detail(run_id)

    def list_events(self, run_id: str, *, after: int = 0, limit: int = 500):
        return self.store.list_events(run_id, after=after, limit=limit)

    def list_workspace(self, run_id: str, relative_path: str = ""):
        return self.store.list_workspace(run_id, relative_path)

    def read_workspace_file(self, run_id: str, relative_path: str):
        return self.store.read_workspace_file(run_id, relative_path)

    async def stop_run(self, run_id: str):
        try:
            return self.store.finish(
                run_id,
                "stopped",
                error_code="user_stopped",
                public_error="The upstream shadow run was stopped.",
            )
        except EngineShadowConflict:
            return self.store.get_run(run_id)

    async def shutdown(self) -> None:
        return None


@pytest_asyncio.fixture
async def shadow_client(tmp_path: Path):
    service = _ApiShadowService(tmp_path / "agent-workspace")
    set_engine_shadow_for_tests(service, enabled=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client, service
    set_engine_shadow_for_tests(None, enabled=None)


@pytest.mark.asyncio
async def test_shadow_flag_is_fail_closed_without_initializing_storage(
    tmp_path: Path,
) -> None:
    set_engine_shadow_for_tests(None, enabled=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        status = await client.get("/api/agent-workspace/status")
        runs = await client.get(
            "/api/agent-workspace/apps/engine-shadow-runs"
        )
    set_engine_shadow_for_tests(None, enabled=None)

    assert status.status_code == 200
    assert status.json()["engine_shadow_enabled"] is False
    assert runs.status_code == 404
    assert not (tmp_path / "agent-workspace").exists()


@pytest.mark.asyncio
async def test_shadow_api_exposes_only_control_plane_and_read_only_workspace(
    shadow_client,
) -> None:
    client, service = shadow_client
    created = await client.post(
        "/api/agent-workspace/apps/engine-shadow-runs",
        json={
            "objective": "Build an offline single-file interaction",
            "model_base_id": "deepseek-v4-flash-0731",
            "thinking_level": "medium",
            "token_budget": 750_000,
            "max_goal_rounds": 12,
            "max_task_turns": 100,
        },
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    listed = await client.get(
        "/api/agent-workspace/apps/engine-shadow-runs"
    )
    detail = await client.get(
        f"/api/agent-workspace/apps/engine-shadow-runs/{run_id}"
    )
    events = await client.get(
        f"/api/agent-workspace/apps/engine-shadow-runs/{run_id}/events"
    )
    workspace = await client.get(
        f"/api/agent-workspace/apps/engine-shadow-runs/{run_id}/workspace"
    )
    preview = await client.get(
        f"/api/agent-workspace/apps/engine-shadow-runs/{run_id}/workspace/file",
        params={"path": "index.html"},
    )

    assert listed.status_code == 200
    assert [item["run_id"] for item in listed.json()["runs"]] == [run_id]
    assert detail.status_code == 200
    assert detail.json()["run"]["status"] == "running"
    assert [event["type"] for event in events.json()["events"]] == [
        "run_created",
        "run_started",
    ]
    assert [entry["path"] for entry in workspace.json()["entries"]] == [
        ".modelmirror",
        "index.html",
    ]
    assert preview.json()["content"].startswith("<!doctype html>")
    assert "preview" not in detail.text.lower()
    assert "artifact" not in detail.text.lower()

    service.store.finish(
        run_id,
        "candidate_ready",
        candidate_sha256=service.store.candidate_hash(run_id),
    )
    stream = await client.get(
        f"/api/agent-workspace/apps/engine-shadow-runs/{run_id}/events/stream"
    )
    assert stream.status_code == 200
    assert "event: shadow_event" in stream.text
    assert "candidate_ready" in stream.text


@pytest.mark.asyncio
async def test_shadow_stop_is_idempotent_and_unknown_run_is_404(
    shadow_client,
) -> None:
    client, _service = shadow_client
    created = await client.post(
        "/api/agent-workspace/apps/engine-shadow-runs",
        json={"objective": "Long-running candidate"},
    )
    run_id = created.json()["run_id"]

    stopped = await client.post(
        f"/api/agent-workspace/apps/engine-shadow-runs/{run_id}/stop"
    )
    stopped_again = await client.post(
        f"/api/agent-workspace/apps/engine-shadow-runs/{run_id}/stop"
    )
    missing = await client.get(
        "/api/agent-workspace/apps/engine-shadow-runs/00000000000000000000000000000000"
    )

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert stopped_again.status_code == 200
    assert stopped_again.json()["status"] == "stopped"
    assert missing.status_code == 404
