"""Automation wait expiry: real Stores, no provider or tool dispatch."""
from __future__ import annotations

from copy import deepcopy

import pytest

import server.main as main
from server.xpert_runtime import AutomationStore, ClientToolStore, RuntimeApprovalStore, WorkflowExecutionStore
from server.xpert_runtime.automation_store import AutomationConflictError
from server.tests.test_workflow_r8a_automation_outcomes import runtime


@pytest.fixture(params=["approval", "client_tool"])
def waiting(runtime, tmp_path, monkeypatch, request):
    parents, children, registry, execution = runtime
    kind = request.param
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    clients = ClientToolStore(tmp_path / "clients", tmp_path / "artifacts")
    monkeypatch.setattr(main, "runtime_approval_store", approvals)
    monkeypatch.setattr(main, "client_tool_store", clients)
    common = dict(task_id="child", run_id="child-run", node_id="node", scope_type="workflow", scope_id="child", timeout_seconds=30)
    if kind == "approval":
        pending = approvals.create_request(**common, action_key="action", request_type="manual_input", node_title="测试", allowed_decisions=["approve", "reject"])
        wait_id = pending.approval_id
    else:
        _, code = clients.create_pairing(name="synthetic")
        host, _ = clients.consume_pairing(code, version="1", capabilities=[])
        pending = clients.create_request(**common, operation_id="op", tool_call_id="call", host_id=host.host_id, tool_name="host_page_read", arguments={}, schema_hash="synthetic")
        wait_id = pending.request_id
    claim = parents.claim_execution(execution.execution_id, worker_id="parent")
    metadata = {"automation_id": execution.automation_id, "automation_execution_id": execution.execution_id, "automation_occurrence_key": execution.occurrence_key}
    children.create(task_id="child", run_id="child-run", run_type="xpert", workflow={}, inputs={}, runtime_metadata=metadata)
    children.suspend("child", wait_kind=kind, wait_id=wait_id, continuation={"queue": ["output"]})
    parents.mark_waiting(execution.execution_id, status="waiting_approval" if kind == "approval" else "waiting_client", run_id="child-run", workflow_task_id="child", wait_id=wait_id, lease_token=claim.lease_token)
    return kind, pending, approvals, clients


def expire(waiting):
    kind, pending, approvals, clients = waiting
    store = approvals if kind == "approval" else clients
    return deepcopy(store.expire_due(now=pending.expires_at + 1)[0])


async def callback(kind, snapshot, pending):
    entry = main.expire_runtime_approval_execution if kind == "approval" else main.expire_runtime_client_tool_execution
    await entry(snapshot, pending)


async def projection_count(registry, run_id):
    return sum(item.event_type == "automation.outcome_projected" for item in await registry.list_checkpoints(run_id))


@pytest.mark.asyncio
async def test_expiry_projects_failed_parent_and_registry(runtime, waiting):
    parents, children, registry, execution = runtime
    run = await registry.create_run("automation", "父执行", status="waiting", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id, "attempt": 1})
    await callback(waiting[0], children.snapshot("child"), expire(waiting))
    saved = parents.get_execution(execution.execution_id)
    assert saved.status == "dead_letter"
    assert saved.error == "AUTOMATION_WAIT_EXPIRED"
    assert (await registry.get_run(run.run_id)).status == "failed"
    assert parents.claimable() == []


@pytest.mark.asyncio
async def test_stale_expiry_cannot_fail_new_wait(runtime, waiting):
    parents, children, _, execution = runtime
    original = children.snapshot("child")
    expired = expire(waiting)
    children.mark_ready("child", wait_kind=original.wait_kind, wait_id=original.wait_id)
    claim = children.claim("child", worker_id="new")
    children.suspend("child", wait_kind=original.wait_kind, wait_id="new-wait", continuation={"queue": ["output"]}, expected_lease_token=claim.lease_token)
    parents.mark_waiting(execution.execution_id, status="waiting_approval" if waiting[0] == "approval" else "waiting_client", run_id="child-run", workflow_task_id="child", wait_id="new-wait")
    before = parents.get_execution(execution.execution_id)
    await callback(waiting[0], original, expired)
    assert parents.get_execution(execution.execution_id) == before


@pytest.mark.asyncio
async def test_pending_request_cannot_be_forged_as_expired(runtime, waiting):
    parents, children, _, execution = runtime
    forged = deepcopy(waiting[1])
    forged.status = "expired"
    before = parents.get_execution(execution.execution_id)
    await callback(waiting[0], children.snapshot("child"), forged)
    assert parents.get_execution(execution.execution_id) == before


@pytest.mark.asyncio
async def test_expired_request_reconciles_after_restart_without_callback(runtime, waiting, monkeypatch):
    parents, children, registry, execution = runtime
    expire(waiting)
    run = await registry.create_run("automation", "父执行", status="waiting", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id, "attempt": 1})
    monkeypatch.setattr(main, "automation_store", AutomationStore(parents.storage_dir))
    monkeypatch.setattr(main, "workflow_execution_store", WorkflowExecutionStore(children.storage_dir))
    monkeypatch.setattr(main, "runtime_approval_store", RuntimeApprovalStore(waiting[2].storage_dir))
    monkeypatch.setattr(main, "client_tool_store", ClientToolStore(waiting[3].storage_dir, waiting[3].artifact_root))
    worker = main.get_automation_coordinator()

    async def forbidden(*args):
        pytest.fail("expiry reconciliation must not dispatch")

    worker.execute_target = forbidden
    assert await worker.run_once() == 0
    assert main.automation_store.get_execution(execution.execution_id).status == "dead_letter"
    assert (await registry.get_run(run.run_id)).status == "failed"


def test_fail_persistence_does_not_mutate_memory_or_disk(runtime, monkeypatch):
    parents, _, _, execution = runtime
    claim = parents.claim_execution(execution.execution_id, worker_id="parent")
    before = parents.get_execution(execution.execution_id)
    definition = parents.require_definition(execution.automation_id)

    def failed():
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(parents, "_persist_unlocked", failed)
    with pytest.raises(OSError):
        parents.fail_execution(execution.execution_id, error="synthetic", lease_token=claim.lease_token)
    assert parents.get_execution(execution.execution_id) == before
    assert parents.require_definition(execution.automation_id) == definition
    assert AutomationStore(parents.storage_dir).get_execution(execution.execution_id) == before


def test_generic_failure_cannot_bypass_wait_identity(runtime, waiting):
    parents, _, _, execution = runtime
    with pytest.raises(AutomationConflictError):
        parents.fail_execution(execution.execution_id, error="unbound late failure", permanent=True)


@pytest.mark.asyncio
async def test_duplicate_expiry_is_idempotent_and_blocks_resume(runtime, waiting):
    parents, children, registry, execution = runtime
    run = await registry.create_run("automation", "父执行", status="waiting", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id, "attempt": 1})
    snapshot, expired = children.snapshot("child"), expire(waiting)
    await callback(waiting[0], snapshot, expired)
    saved = parents.get_execution(execution.execution_id)
    await callback(waiting[0], snapshot, expired)
    assert parents.get_execution(execution.execution_id) == saved
    assert await projection_count(registry, run.run_id) == 1
    assert set(saved.metadata["workflow_projection"]) == {"task_id", "run_id", "revision", "status", "wait_kind", "wait_id"}
    with pytest.raises(main.WorkflowExecutionConflictError):
        await main._assert_automation_parent_not_cancelled(snapshot.runtime_metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["cancelled", "completed", "new_run"])
async def test_late_expiry_cannot_override_parent_or_child(runtime, waiting, state):
    parents, children, _, execution = runtime
    snapshot, expired = children.snapshot("child"), expire(waiting)
    if state == "cancelled":
        parents.cancel_execution(execution.execution_id)
    elif state == "completed":
        parents.complete_execution(execution.execution_id, result="done", run_id="child-run", workflow_task_id="child")
    else:
        children.mark_ready("child", wait_kind=snapshot.wait_kind, wait_id=snapshot.wait_id)
        claim = children.claim("child", worker_id="new")
        children.update_run_id("child", run_id="new-run", expected_lease_token=claim.lease_token)
    before_parent, before_child = parents.get_execution(execution.execution_id), children.snapshot("child")
    await callback(waiting[0], snapshot, expired)
    assert parents.get_execution(execution.execution_id) == before_parent
    assert children.snapshot("child") == before_child


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["automation_id", "automation_occurrence_key"])
async def test_expiry_rejects_wrong_parent_identity(runtime, waiting, field):
    parents, children, _, execution = runtime
    expired = expire(waiting)
    snapshot = children.snapshot("child")
    snapshot.runtime_metadata[field] = "forged"
    before = parents.get_execution(execution.execution_id)
    await callback(waiting[0], snapshot, expired)
    assert parents.get_execution(execution.execution_id) == before


@pytest.mark.asyncio
async def test_expiry_persist_failure_is_reconciled_without_request_reexpiration(runtime, waiting, monkeypatch):
    parents, children, _, execution = runtime
    snapshot, expired = children.snapshot("child"), expire(waiting)
    before = parents.get_execution(execution.execution_id)
    definition = parents.require_definition(execution.automation_id)
    persist = parents._persist_unlocked

    def failed():
        raise OSError("synthetic parent failure")

    monkeypatch.setattr(parents, "_persist_unlocked", failed)
    with pytest.raises(OSError):
        await callback(waiting[0], snapshot, expired)
    assert parents.get_execution(execution.execution_id) == before
    assert parents.require_definition(execution.automation_id) == definition
    assert AutomationStore(parents.storage_dir).get_execution(execution.execution_id) == before
    monkeypatch.setattr(parents, "_persist_unlocked", persist)
    assert waiting[2 if waiting[0] == "approval" else 3].expire_due(now=expired.expires_at + 2) == []
    assert await main.get_automation_coordinator().run_once() == 0
    assert parents.get_execution(execution.execution_id).status == "dead_letter"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["update_run", "record_checkpoint"])
async def test_expiry_registry_failure_replays_safely(runtime, waiting, monkeypatch, method):
    parents, children, registry, execution = runtime
    snapshot, expired = children.snapshot("child"), expire(waiting)
    run = await registry.create_run("automation", "父执行", status="waiting", source_id=execution.automation_id, metadata={"automation_execution_id": execution.execution_id, "attempt": 1})
    original = getattr(registry, method)

    async def failed(*args, **kwargs):
        raise OSError("synthetic registry failure")

    monkeypatch.setattr(registry, method, failed)
    with pytest.raises(OSError):
        await callback(waiting[0], snapshot, expired)
    assert parents.get_execution(execution.execution_id).status == "dead_letter"
    monkeypatch.setattr(registry, method, original)
    assert await main.get_automation_coordinator().run_once() == 0
    assert (await registry.get_run(run.run_id)).status == "failed"
    assert await projection_count(registry, run.run_id) == 1
    assert await main.get_automation_coordinator().run_once() == 0
    assert await projection_count(registry, run.run_id) == 1


@pytest.mark.asyncio
async def test_failed_request_expiration_cannot_become_parent_evidence(runtime, waiting, monkeypatch):
    parents, _, _, execution = runtime
    kind, pending, approvals, clients = waiting
    source = approvals if kind == "approval" else clients
    before = parents.get_execution(execution.execution_id)
    persist = source._persist_unlocked

    def failed():
        raise OSError("synthetic request persistence failure")

    monkeypatch.setattr(source, "_persist_unlocked", failed)
    with pytest.raises(OSError):
        source.expire_due(now=pending.expires_at + 1)
    assert await main.get_automation_coordinator().run_once() == 0
    assert parents.get_execution(execution.execution_id) == before
    getter = approvals.get if kind == "approval" else clients.get_request
    wait_id = pending.approval_id if kind == "approval" else pending.request_id
    assert getter(wait_id).status == "pending"
    restored = RuntimeApprovalStore(approvals.storage_dir) if kind == "approval" else ClientToolStore(clients.storage_dir, clients.artifact_root)
    restored_get = restored.get if kind == "approval" else restored.get_request
    assert restored_get(wait_id).status == "pending"
    monkeypatch.setattr(source, "_persist_unlocked", persist)
    assert len(source.expire_due(now=pending.expires_at + 2)) == 1
    assert await main.get_automation_coordinator().run_once() == 0
    assert parents.get_execution(execution.execution_id).status == "dead_letter"


@pytest.mark.asyncio
async def test_expiration_receipt_is_detached_from_request_store(runtime, waiting):
    _, children, _, _ = runtime
    expired = expire(waiting)
    expired.status = "pending"
    assert main._automation_wait_is_expired(children.snapshot("child")) is True


@pytest.mark.asyncio
async def test_expiry_after_manual_retry_cannot_close_new_attempt(runtime, waiting):
    parents, children, _, execution = runtime
    snapshot, expired = children.snapshot("child"), expire(waiting)
    await callback(waiting[0], snapshot, expired)
    worker = main.get_automation_coordinator()
    await worker.retry_execution(execution.execution_id)
    claim = parents.claim_execution(execution.execution_id, worker_id="new-parent")
    await callback(waiting[0], snapshot, expired)
    assert parents.get_execution(execution.execution_id) == claim
