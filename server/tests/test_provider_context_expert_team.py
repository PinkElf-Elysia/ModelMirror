"""Expert Team host-owned lineage, bounded Worker calls and recovery."""
import asyncio
import json

import httpx
import pytest

from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.expert_team_gateway import ManagedExpertTeamGateway
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError
from server.orchestration_worker import AgencyWorkerError
from server.tests.test_expert_team_managed_gateway import _gateway, _request, MODEL_ID


def make_gateway(tmp_path, monkeypatch, entry="expert_team_dag", posts=None):
    def handler(request):
        if posts is not None:
            posts.append(request.method)
        content = '{"pass":true}' if json.loads(request.content).get("response_format") else "synthetic"
        return httpx.Response(200, json={"model": MODEL_ID, "choices": [{"message": {"content": content}}]})
    return _gateway(tmp_path, monkeypatch, entry_id=entry,
        shapes=["chat_text_unary"] if entry == "expert_team_planner" else ["chat_text_unary", "chat_json_object"], handler=handler)


def start_dag(gateway, task="task-1", segment="initial"):
    return gateway.start_run("expert_team_dag", parent_run_reference=f"dag:{task}:{segment}", origin_execution_id=task)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,count", [("expert_team_planner", 3), ("expert_team_dag", 10)])
async def test_worker_calls_persist_correct_origin_without_leaking_to_response(tmp_path, monkeypatch, entry, count):
    posts = []
    gateway = make_gateway(tmp_path, monkeypatch, entry, posts)
    repo = gateway.call_service.repository
    try:
        if entry == "expert_team_dag":
            gateway.bind_dag_execution("task-1")
            run = start_dag(gateway)
        else:
            run = gateway.start_run(entry, parent_run_reference="planner:host-run-1")
        for i in range(count):
            response = await run.complete(_request(request_id=f"worker:{i}", json_response=entry == "expert_team_dag" and i % 2 == 1))
            assert set(response.model_dump()) == {"content", "usage", "finish_reason"}
        receipt = run.finish("passed")
        assert len(posts) == receipt["call_count"] == count
        calls = repo.list_workload_receipts("local")["calls"]
        contexts = [ProviderContextStore(repo).read("local", c["id"]) for c in calls]
        assert len({c.logical_call_id for c in contexts}) == count
        assert all((c.entry_id, c.actor_kind, c.actor_reference, c.purpose) ==
            (entry, "local_anonymous", "local-anonymous", "user_run") for c in contexts)
        assert "actor_reference" not in json.dumps(receipt)
        assert "Follow the contract" not in repr(contexts)
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_dag_reopen_restores_origin_for_hitl_and_explicit_child(tmp_path, monkeypatch):
    gateway = make_gateway(tmp_path, monkeypatch)
    repo = gateway.call_service.repository
    gateway.bind_dag_execution("task-1")
    initial = start_dag(gateway)
    await initial.complete(_request(request_id="step-1", json_response=False))
    initial.finish("passed")
    router = gateway.call_service.router_service
    repo.close()
    with SQLiteRouterRepository.open(tmp_path / "router", master_key=b"x" * 32) as restored:
        # Preserve only test egress/transport; no in-memory origin is reused.
        resumed = ManagedExpertTeamGateway.for_router(ModelRouterService(restored, egress_policy=router.egress_policy),
            client_factory=gateway._workflow_gateway._client_factory)
        hitl = start_dag(resumed, segment="interaction:approval-1:2")
        await hitl.complete(_request(request_id="step-2", json_response=False))
        hitl.finish("passed")
        resumed.bind_dag_execution("task-revision", parent_task_id="task-1")
        child = start_dag(resumed, task="task-revision")
        await child.complete(_request(request_id="step-revised", json_response=False))
        child.finish("passed")
        assert ProviderContextStore(restored).execution("local", "task-revision")["parent_execution_id"] == "task-1"
        with pytest.raises(ManagedWorkflowRoutingError):
            start_dag(resumed, segment="interaction:approval-1:2")
        assert len(restored.list_workload_receipts("local")["calls"]) == 3


@pytest.mark.asyncio
async def test_uncertain_cannot_be_reopened_as_new_segment_or_child(tmp_path, monkeypatch):
    gateway = make_gateway(tmp_path, monkeypatch)
    repo = gateway.call_service.repository
    gateway.bind_dag_execution("task-1")
    run = start_dag(gateway)
    prepared = await gateway.call_service.prepare_call(run_id=run._node_run.run_id, entry_id="expert_team_dag",
        execution_shape="chat_text_unary", model_id=MODEL_ID, logical_call_key="worker:1", call_sequence=1)
    gateway.call_service.mark_dispatched(prepared)
    repo.close()
    with SQLiteRouterRepository.open(tmp_path / "router", master_key=b"x" * 32) as restored:
        resumed = ManagedExpertTeamGateway.for_router(ModelRouterService(restored))
        assert ProviderContextStore(restored).read("local", prepared.call_id) == prepared.call_context
        for segment in ("initial", "interaction:other:1"):
            with pytest.raises(ManagedWorkflowRoutingError, match="provider_call_context_execution_unresolved"):
                start_dag(resumed, segment=segment)
        with pytest.raises(ManagedWorkflowRoutingError, match="provider_call_context_execution_unresolved"):
            resumed.bind_dag_execution("new-task", parent_task_id="task-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "actor", "source", "parent_cycle", "parent_missing"])
async def test_dag_origin_tampering_blocks_before_worker_post(tmp_path, monkeypatch, damage):
    posts = []
    gateway = make_gateway(tmp_path, monkeypatch, posts=posts)
    repo = gateway.call_service.repository
    try:
        gateway.bind_dag_execution("task-1")
        run = start_dag(gateway)
        with repo._connect() as db:
            if damage == "missing":
                db.execute("DELETE FROM provider_workload_run_origins")
            elif damage == "actor":
                db.execute("UPDATE provider_execution_origins SET purpose='certification'")
            elif damage == "source":
                db.execute("UPDATE provider_execution_origins SET source_kind='expert_team_planner'")
            else:
                db.execute("UPDATE provider_execution_origins SET parent_execution_id=?", ("task-1" if damage == "parent_cycle" else "missing",))
        with pytest.raises(AgencyWorkerError):
            await run.complete(_request(request_id="worker:1", json_response=False))
        assert posts == []
    finally:
        repo.close()


def test_dag_missing_historical_origin_is_not_recreated(tmp_path, monkeypatch):
    gateway = make_gateway(tmp_path, monkeypatch)
    try:
        with pytest.raises(ManagedWorkflowRoutingError, match="provider_call_context_origin_missing"):
            start_dag(gateway)
        with pytest.raises(ManagedWorkflowRoutingError, match="provider_call_context_origin_missing"):
            gateway.bind_dag_execution("child", parent_task_id="old-task")
    finally:
        gateway.call_service.repository.close()


@pytest.mark.asyncio
async def test_concurrent_duplicate_worker_request_has_one_post(tmp_path, monkeypatch):
    posts = []
    gateway = make_gateway(tmp_path, monkeypatch, posts=posts)
    try:
        gateway.bind_dag_execution("task-1")
        run = start_dag(gateway)
        request = _request(request_id="worker:same", json_response=False)
        results = await asyncio.gather(run.complete(request), run.complete(request), return_exceptions=True)
        errors = [result for result in results if isinstance(result, Exception)]
        assert len(errors) == 1
        assert isinstance(errors[0], AgencyWorkerError)
        assert errors[0].code == "provider_workload_logical_call_replay_blocked"
        assert posts == ["POST"]
        assert run.receipt_summary()["call_count"] == 1
    finally:
        gateway.call_service.repository.close()
