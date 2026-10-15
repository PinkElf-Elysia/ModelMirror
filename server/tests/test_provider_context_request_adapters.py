"""Actual Gateway/SQLite/MockTransport checks for interactive request origins."""
import json

import httpx
import pytest

from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService, RouterServiceError
from server.model_router.workload_control import ProviderWorkloadCallService
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError
from server.tests.test_route_team_managed_gateway import _gateway, _sse, MODEL_ID
from server.tests.test_fusion_managed_gateway import (
    _gateway as fusion_gateway, _events, _sse as fusion_sse, JUDGE,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,count", [("route_agent", 1), ("team_chat", 3)])
async def test_planned_calls_keep_request_origin_and_post_count(tmp_path, monkeypatch, entry, count):
    posts = []
    def handler(request):
        posts.append(request.method)
        return httpx.Response(200, content=_sse())
    gateway, repo = _gateway(tmp_path, monkeypatch, entry_id=entry, handler=handler)
    try:
        run = gateway.start_run(entry)
        plan = await run.prepare_plan(model_id=MODEL_ID, logical_call_keys=[f"round:{i}" for i in range(count)])
        for prepared in plan:
            assert prepared.call_context is not None
            assert prepared.call_context.entry_id == entry
            assert prepared.call_context.purpose == "user_run"
            assert prepared.call_context.actor_reference == "local-anonymous"
            assert ProviderContextStore(repo).read("local", prepared.call_id) == prepared.call_context
            assert [delta async for delta in run.stream_text(prepared,
                messages=[{"role": "user", "content": "PRIVATE-SYNTHETIC-MARKER"}], temperature=0, max_tokens=32)]
        receipt = run.finish("passed")
        assert posts == ["POST"] * count
        assert receipt["call_count"] == count
        assert len({p.call_context.logical_call_id for p in plan}) == count
        assert "actor_reference" not in json.dumps(receipt)
        with repo._connect() as db:
            payload = [row[0] for row in db.execute("SELECT context_json FROM provider_call_contexts")]
            assert "PRIVATE-SYNTHETIC-MARKER" not in str(payload)
            assert db.execute("SELECT source_kind FROM provider_execution_origins").fetchone()[0] == entry + "_request"
    finally:
        repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("native,count", [(False, 3), (True, 1)])
async def test_fusion_planned_contexts_cover_native_and_application(tmp_path, monkeypatch, native, count):
    posts = []
    def handler(request):
        model = json.loads(request.content)["model"]
        posts.append(model)
        return httpx.Response(200, content=fusion_sse(JUDGE if native else model, "synthetic answer"))
    gateway, repo = fusion_gateway(tmp_path, monkeypatch, handler=handler)
    try:
        events = await _events(gateway.start_run(), native=native)
        assert events[-1]["event"] == "fusion_end"
        calls = repo.list_workload_receipts("local")["calls"]
        assert len(calls) == len(posts) == count
        contexts = [ProviderContextStore(repo).read("local", row["id"]) for row in calls]
        assert all((c.entry_id, c.purpose, c.actor_kind) == ("fusion", "user_run", "local_anonymous") for c in contexts)
        assert len({c.parent_run_id for c in contexts}) == 1
        assert len({c.logical_call_id for c in contexts}) == count
    finally:
        repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["provider_call_contexts", "provider_execution_origins", "provider_workload_run_origins"])
async def test_deleted_origin_fails_closed_without_post(tmp_path, monkeypatch, table):
    posts = []
    def handler(request):
        posts.append(request.method)
        return httpx.Response(200, content=_sse())
    gateway, repo = _gateway(tmp_path, monkeypatch, entry_id="route_agent", handler=handler)
    try:
        run = gateway.start_run("route_agent")
        plan = await run.prepare_plan(model_id=MODEL_ID, logical_call_keys=["round:1"])
        with repo._connect() as db:
            # Intentional corruption fixture, not a maintenance operation.
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute(f"DELETE FROM {table}")
        with pytest.raises(ManagedWorkflowRoutingError):
            _ = [d async for d in run.stream_text(plan[0], messages=[], temperature=0, max_tokens=32)]
        assert posts == []
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_request_restart_preserves_context_and_blocks_replay(tmp_path, monkeypatch):
    gateway, repo = _gateway(tmp_path, monkeypatch, entry_id="team_chat",
        handler=lambda request: pytest.fail("must not POST"))
    run = gateway.start_run("team_chat")
    plan = await run.prepare_plan(model_id=MODEL_ID, logical_call_keys=["member:1"])
    gateway.call_service.mark_dispatched(plan[0])
    repo.close()
    with SQLiteRouterRepository.open(tmp_path / "router", master_key=b"x" * 32) as restored:
        assert ProviderContextStore(restored).read("local", plan[0].call_id) == plan[0].call_context
        restored_service = ProviderWorkloadCallService(ModelRouterService(restored))
        with pytest.raises(RouterServiceError) as caught:
            restored_service.mark_dispatched(plan[0])
        assert restored.get_workload_run("local", plan[0].run_id)["status"] == "uncertain"
        assert caught.value.code == "provider_workload_policy_changed"
