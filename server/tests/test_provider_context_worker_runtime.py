"""Exercise the actual host factories and coordinator, never a paid Provider."""
import asyncio
import json

import pytest
from pydantic import ValidationError

import server.main as main
from server.agent_upstream.models import EngineShadowRunCreate
from server.expert_team_agency_runtime import AgencyExecutionCoordinator, prepare_agency_execution, ExpertTeamDagRunRequest
from server.model_router.call_context_storage import ProviderContextStore
from server.model_router.expert_team_gateway import ManagedExpertTeamGateway
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.workflow_gateway import ManagedWorkflowRoutingError
from server.tests.test_provider_context_expert_team import make_gateway
from server.tests.test_expert_team_managed_gateway import _request, MODEL_ID
from server.tests.test_expert_team_agency_execution import (
    experts, valid_plan_and_workflow, valid_hitl_plan_and_workflow,
    HitlCheckpointClient, ResumeClient, RevisionClient, _seed_revisable_run,
)
from server.xpert_runtime import WorkflowExecutionStore, RunRegistry, RuntimeApprovalStore
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError


async def no_legacy(*_args, **_kwargs):
    pytest.fail("legacy model path must remain unused")


class CallingWorker:
    """Same host callback contract; private context is not sent into Worker inputs."""
    def __init__(self, client):
        self.client = client
        self.worker_entry = client.worker_entry
        self.model_runner = no_legacy

    async def execute(self, **kwargs):
        assert not {"actor_kind", "actor_reference", "purpose", "connection_id", "provider_call_context"} & set(kwargs)
        # Worker process restarts may reuse a request ID in a new explicit segment.
        await self.model_runner(_request(request_id="worker:1", json_response=False))
        return await self.client.execute(**kwargs)


def coordinator(tmp_path, worker, approvals=None):
    return AgencyExecutionCoordinator(store=WorkflowExecutionStore(tmp_path),
        run_registry=RunRegistry(), model_runner=no_legacy, approval_store=approvals,
        client_factory=lambda: worker, managed_run_factory=main.expert_team_managed_run)


async def wait_task(runtime, task_id):
    task = runtime._tasks.get(task_id)
    if task is not None:
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_actual_host_hitl_restart_uses_saved_origin_and_new_segment(tmp_path, monkeypatch):
    posts = []
    gateway = make_gateway(tmp_path, monkeypatch, posts=posts)
    repo = gateway.call_service.repository
    monkeypatch.setattr(main, "expert_team_managed_gateway", lambda: gateway)
    plan, workflow = valid_hitl_plan_and_workflow()
    prepared = prepare_agency_execution(plan=plan, workflow=workflow, expert_records=experts())
    approvals = RuntimeApprovalStore(tmp_path / "execution")
    runtime = coordinator(tmp_path / "execution", CallingWorker(HitlCheckpointClient()), approvals)
    initial = await runtime.start(goal="synthetic", model_id=MODEL_ID, prepared=prepared,
        capability_snapshot_version="v1", capability_snapshot_hash="hash", upstream_revision="revision",
        managed_provider_required=True)
    task_id = initial["task_id"]
    await wait_task(runtime, task_id)
    assert runtime.get(task_id)["status"] == "waiting"
    old_origin = ProviderContextStore(repo).execution("local", task_id)
    router = gateway.call_service.router_service
    transport = gateway._workflow_gateway._client_factory
    repo.close()
    with SQLiteRouterRepository.open(tmp_path / "router", master_key=b"x" * 32) as restored:
        gateway = ManagedExpertTeamGateway.for_router(ModelRouterService(restored, egress_policy=router.egress_policy), client_factory=transport)
        # New coordinator, file store, provider service and registry: no memory context survives.
        runtime = coordinator(tmp_path / "execution", CallingWorker(HitlCheckpointClient()), approvals)
        pending = runtime.get(task_id)["pending_interaction"]
        approval = approvals.decide(pending["approval_id"], revision=pending["revision"],
            decision="replace", operator="tester", replacement_text="synthetic audience")
        runtime.store.mark_ready(task_id, approval_id=approval.approval_id)
        claimed = runtime.store.claim(task_id, worker_id="test", lease_seconds=60)
        await runtime.resume_interaction(execution=claimed, approval=approval, prepared=prepared)
        await wait_task(runtime, task_id)
        assert runtime.get(task_id)["status"] == "completed"
        assert ProviderContextStore(restored).execution("local", task_id) == old_origin
        assert posts == ["POST", "POST"]
        calls = restored.list_workload_receipts("local")["calls"]
        assert len(calls) == 2
        assert len({c["run_id"] for c in calls}) == 2
        # The current Runtime rejects the completed task's stale lease before
        # reaching Provider replay checks; neither path may dispatch again.
        with pytest.raises(WorkflowExecutionConflictError, match="lease is unavailable from completed"):
            await runtime.resume_interaction(execution=claimed, approval=approval, prepared=prepared)
        assert posts == ["POST", "POST"]
        assert "actor_reference" not in json.dumps(runtime.get(task_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("current_mode", ["managed_required", "degraded_required", "legacy"])
async def test_legacy_hitl_rechecks_new_managed_policy_before_worker_start(tmp_path, monkeypatch, current_mode):
    posts = []
    gateway = make_gateway(tmp_path, monkeypatch, posts=posts)
    monkeypatch.setattr(main, "expert_team_managed_gateway", lambda: gateway)
    plan, workflow = valid_hitl_plan_and_workflow()
    prepared = prepare_agency_execution(plan=plan, workflow=workflow, expert_records=experts())
    approvals = RuntimeApprovalStore(tmp_path / "execution")
    worker = HitlCheckpointClient()
    runtime = coordinator(tmp_path / "execution", worker, approvals)
    monkeypatch.setenv("MODEL_CONTROL_EXPERT_TEAM_DAG_ENABLED", "false")
    # A pre-C1/legacy task has no Provider origin. Its checkpoint is not authority
    # to bypass the policy which is active when the host recovers the task.
    initial = await runtime.start(goal="synthetic", model_id=MODEL_ID, prepared=prepared,
        capability_snapshot_version="v1", capability_snapshot_hash="hash", upstream_revision="revision",
        managed_provider_required=False)
    task_id = initial["task_id"]
    await wait_task(runtime, task_id)
    assert runtime.get(task_id)["status"] == "waiting"
    monkeypatch.setenv("MODEL_CONTROL_EXPERT_TEAM_DAG_ENABLED", "true")
    if current_mode != "managed_required":
        monkeypatch.setattr(gateway, "routing_mode", lambda _entry: current_mode)
    runtime = coordinator(tmp_path / "execution", worker, approvals)
    pending = runtime.get(task_id)["pending_interaction"]
    approval = approvals.decide(pending["approval_id"], revision=pending["revision"],
        decision="replace", operator="tester", replacement_text="synthetic audience")
    runtime.store.mark_ready(task_id, approval_id=approval.approval_id)
    claimed = runtime.store.claim(task_id, worker_id="test", lease_seconds=60)
    try:
        if current_mode == "legacy":
            await runtime.resume_interaction(execution=claimed, approval=approval, prepared=prepared)
            await wait_task(runtime, task_id)
            assert runtime.get(task_id)["status"] == "completed"
            assert len(worker.calls) == 2
        else:
            code = ("provider_call_context_origin_missing" if current_mode == "managed_required"
                else "provider_workload_policy_not_active")
            with pytest.raises(ManagedWorkflowRoutingError, match=code):
                await runtime.resume_interaction(execution=claimed, approval=approval, prepared=prepared)
            assert len(worker.calls) == 1
        assert runtime._tasks == {}
        assert posts == []
    finally:
        await wait_task(runtime, task_id)
        gateway.call_service.repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["retry", "revise"])
@pytest.mark.parametrize("known_origin", [True, False])
async def test_coordinator_child_inherits_server_source_or_blocks(tmp_path, monkeypatch, action, known_origin):
    posts = []
    gateway = make_gateway(tmp_path, monkeypatch, posts=posts)
    repo = gateway.call_service.repository
    monkeypatch.setattr(main, "expert_team_managed_gateway", lambda: gateway)
    plan, workflow = valid_plan_and_workflow()
    prepared = prepare_agency_execution(plan=plan, workflow=workflow, expert_records=experts())
    runtime = coordinator(tmp_path / "execution", CallingWorker(ResumeClient() if action == "retry" else RevisionClient()))
    source = _seed_revisable_run(runtime, prepared, failed=action == "retry")
    if known_origin:
        previous = main.expert_team_managed_run(source.task_id, "initial")
        await previous.complete(_request(request_id="worker:1", json_response=False))
        previous.finish("passed")
    try:
        async def child_action():
            if action == "retry":
                return await runtime.retry(source_task_id=source.task_id, prepared=prepared, managed_provider_required=True)
            return await runtime.revise(source_task_id=source.task_id, target_task_id="research",
                feedback="synthetic feedback to revise this step", prepared=prepared, managed_provider_required=True)
        if not known_origin:
            with pytest.raises(ManagedWorkflowRoutingError, match="provider_call_context_origin_missing"):
                await child_action()
            assert posts == []
            assert runtime._tasks == {}
        else:
            child = await child_action()
            await wait_task(runtime, child["task_id"])
            assert runtime.get(child["task_id"])["status"] == "completed"
            origin = ProviderContextStore(repo).execution("local", child["task_id"])
            assert origin["parent_execution_id"] == source.task_id
            assert origin["actor_reference"] == "local-anonymous"
            assert len(posts) == 2
    finally:
        repo.close()


@pytest.mark.parametrize("field", ["tenant_id", "actor_reference", "entry_id", "parent_task_id", "connection_id", "provider_call_context"])
def test_worker_entry_models_reject_client_provenance_fields(field):
    with pytest.raises(ValidationError) as caught:
        EngineShadowRunCreate.model_validate({"objective": "synthetic", field: "forged"})
    assert any(error["type"] == "extra_forbidden" and error["loc"] == (field,) for error in caught.value.errors())
    with pytest.raises(ValidationError) as caught:
        ExpertTeamDagRunRequest.model_validate({field: "forged"})
    assert any(error["type"] == "extra_forbidden" and error["loc"] == (field,) for error in caught.value.errors())
