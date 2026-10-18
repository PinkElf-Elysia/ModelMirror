"""Offline B2 recovery evidence; synthetic completions only, no Provider calls."""
from __future__ import annotations

import asyncio
import contextvars
import json

import pytest

import server.main as main
from server.tests.test_workflow_r8b_entry_evidence import runtime, invoke, reopen
from server.tests.test_workflow_r8a_budget_tree import budget
from server.xpert_runtime.execution_budget import execution_operation, use_execution_budget
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
from server.workflow_deployments import WorkflowDeploymentConflictError, WorkflowDeploymentStore, WorkflowDeploymentValidationError


@pytest.mark.asyncio
async def test_fresh_context_binds_and_charges_persisted_parent(runtime, monkeypatch):
    deployments, store, calls, _, node = runtime
    result = await asyncio.create_task(invoke(node), context=contextvars.Context())
    _, store = reopen(monkeypatch, deployments, store)
    child = store.require(result["taskId"])
    assert child.execution_budget_parent["parent_task_id"] == "root"
    assert child.execution_budget["usage"]["model_calls"] == 1
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert calls == ["dispatched"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_lease", "takeover", "cancelled", "forged_parent"])
async def test_invalid_parent_cannot_materialize_or_dispatch(runtime, fault):
    deployments, store, calls, _, node = runtime
    claimed = store.claim("root", worker_id="original")
    active = budget(store, "root", claimed.lease_token)
    if fault == "takeover":
        store.claim("root", worker_id="replacement", now=claimed.lease_expires_at + 1)
    elif fault == "cancelled":
        store.cancel("root")
    before = deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()
    with use_execution_budget(None if fault == "missing_lease" else active):
        with pytest.raises((ValueError, RuntimeError, WorkflowExecutionConflictError)):
            if fault == "forged_parent":
                await main.run_private_subworkflow_call(node=node, variables={}, task_id="forged",
                    runtime_metadata={}, cancellation_requested=lambda: False)
            else:
                await invoke(node)
    assert (deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()) == before
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["input", "root", "depth", "timeout", "definition", "suppression"])
async def test_contract_drift_never_reuses_or_dispatches(runtime, monkeypatch, fault):
    deployments, store, calls, release, node = runtime
    await invoke(node)
    deployments, store = reopen(monkeypatch, deployments, store)
    changed = node.model_copy(deep=True)
    metadata = {"workflow_deployment_execution_id": "root", "workflow_root_execution_id": "root"}
    if fault == "input":
        changed.data["inputBindings"]["message"]["value"] = "不同输入"
    elif fault == "root":
        metadata["workflow_root_execution_id"] = "another"
    elif fault == "depth":
        metadata["workflow_call_depth"] = 1
    elif fault == "timeout":
        changed.data["timeoutSeconds"] = 6
    elif fault == "definition":
        deployments.require_version(release.project_id, release.version).definition_checksum = "f" * 64
    else:
        metadata["suppress_failure_dispatch"] = True
    before = deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowDeploymentConflictError):
        await main.run_private_subworkflow_call(node=changed, variables={}, task_id="root",
            runtime_metadata=metadata, cancellation_requested=lambda: False)
    assert (deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()) == before
    assert calls == ["dispatched"]


@pytest.mark.asyncio
async def test_no_dispatch_before_crash_can_continue_same_occurrence(runtime, monkeypatch):
    deployments, store, calls, _, node = runtime
    execute = main.execute_workflow_trigger

    async def before_dispatch(*args, **kwargs):
        raise RuntimeError("synthetic pre-dispatch crash")

    monkeypatch.setattr(main, "execute_workflow_trigger", before_dispatch)
    with pytest.raises(RuntimeError, match="pre-dispatch"):
        await invoke(node)
    children = list(store.require("root").execution_budget_children)
    assert len(children) == 1 and calls == []
    _, store = reopen(monkeypatch, deployments, store)
    monkeypatch.setattr(main, "execute_workflow_trigger", execute)
    claimed = store.claim("root", worker_id="restart")
    with use_execution_budget(budget(store, "root", claimed.lease_token)):
        result = await invoke(node)
    assert result["status"] == "completed" and calls == ["dispatched"]
    assert list(main.workflow_execution_store.require("root").execution_budget_children) == children


@pytest.mark.asyncio
async def test_claimed_restart_still_cannot_bypass_spent_root_budget(runtime, monkeypatch):
    deployments, store, calls, _, node = runtime
    await invoke(node)
    _, store = reopen(monkeypatch, deployments, store)
    claimed = store.claim("root", worker_id="restart")
    node = node.model_copy(deep=True)
    node.id = "next_item"
    with use_execution_budget(budget(store, "root", claimed.lease_token)):
        with pytest.raises((ValueError, RuntimeError), match="model-call budget"):
            await invoke(node)
    assert calls == ["dispatched"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["prepare", "settle"])
@pytest.mark.parametrize("fault", ["cancel", "takeover"])
async def test_parent_race_does_not_return_success_or_replay(runtime, monkeypatch, stage, fault):
    deployments, store, calls, _, node = runtime
    claimed = store.claim("root", worker_id="original")
    active = budget(store, "root", claimed.lease_token)
    observed = []

    def change_owner():
        observed.append(stage)
        if fault == "cancel":
            store.cancel("root")
        else:
            store.claim("root", worker_id="replacement", now=claimed.lease_expires_at + 1)

    if stage == "prepare":
        original = main._run_workflow_response

        async def prepare(*args, **kwargs):
            change_owner()
            return await original(*args, **kwargs)

        monkeypatch.setattr(main, "_run_workflow_response", prepare)
    else:
        original = main.consume_workflow_stream

        async def settle(*args, **kwargs):
            result = await original(*args, **kwargs)
            change_owner()
            return result

        monkeypatch.setattr(main, "consume_workflow_stream", settle)
    with use_execution_budget(active):
        with pytest.raises((ValueError, RuntimeError, WorkflowExecutionConflictError)):
            await invoke(node)
    assert observed == [stage]
    current = store.require("root")
    assert current.status == ("cancelled" if fault == "cancel" else "running")
    assert current.lease_token != claimed.lease_token
    assert len(calls) == (0 if stage == "prepare" else 1)
    with use_execution_budget(active):
        with pytest.raises((ValueError, RuntimeError, WorkflowExecutionConflictError)):
            await invoke(node)
    assert len(calls) == (0 if stage == "prepare" else 1)


@pytest.mark.asyncio
async def test_raw_trigger_cannot_restart_unsettled_durable_child(runtime, monkeypatch):
    deployments, store, calls, release, node = runtime
    await invoke(node)
    child = deployments.list_executions(release.project_id)[0]
    # Fault injection models a persisted child whose external result was lost.
    snapshot = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    raw_child = next(item for item in snapshot["items"] if item["task_id"] == child.task_id)
    raw_child.update(status="running", result=None, completed_at=None)
    store.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    reopen(monkeypatch, deployments, store)
    with pytest.raises(RuntimeError, match="SUBWORKFLOW_EXECUTION_UNSETTLED"):
        await main.run_deployed_workflow_trigger(child, release, {"inputs": {"message": "合成输入"}})
    assert calls == ["dispatched"]


@pytest.mark.asyncio
async def test_call_checksum_survives_reopen_and_corruption_is_rejected(runtime):
    deployments, _, _, _, node = runtime
    await invoke(node)
    snapshot = json.loads(deployments.snapshot_path.read_text(encoding="utf-8"))
    relation = snapshot["subworkflow_relations"][0]
    assert len(relation["call_contract_checksum"]) == 64
    assert "合成输入" not in json.dumps(relation, ensure_ascii=False)
    relation["call_contract_checksum"] = {"forged": True}
    deployments.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(WorkflowDeploymentValidationError):
        WorkflowDeploymentStore(deployments.storage_dir)


@pytest.mark.asyncio
async def test_budget_link_exists_before_first_external_dispatch(runtime, monkeypatch):
    _, store, calls, _, node = runtime

    async def inspect(*args, **kwargs):
        child = next(item for item in store.list_items() if item.task_id != "root")
        link = child.execution_budget_parent
        assert link is not None and link["parent_task_id"] == "root"
        assert store.require("root").execution_budget_children[link["occurrence_id"]] == child.task_id
        async with execution_operation("model_call"):
            calls.append("dispatched")
            yield "合成结果"

    monkeypatch.setattr(main, "stream_workflow_llm_text", inspect)
    assert (await invoke(node))["status"] == "completed"
    assert calls == ["dispatched"]


@pytest.mark.asyncio
async def test_completed_child_survives_missing_deployment_checkpoint(runtime, monkeypatch):
    deployments, store, calls, _, node = runtime
    snapshots = []
    consume = main.consume_workflow_stream

    async def after_result(*args, **kwargs):
        result = await consume(*args, **kwargs)
        snapshots.append(deployments.snapshot_path.read_bytes())
        return result

    monkeypatch.setattr(main, "consume_workflow_stream", after_result)
    first = await invoke(node)
    deployments.snapshot_path.write_bytes(snapshots[0])
    deployments, _ = reopen(monkeypatch, deployments, store)
    # Simulate lease expiry without delaying the test or changing the durable result.
    deployments.get_execution(first["executionId"]).lease_expires_at = 0
    second = await asyncio.create_task(invoke(node), context=contextvars.Context())
    assert second == first and calls == ["dispatched"]


@pytest.mark.asyncio
async def test_batch_item_input_cannot_drift_under_same_batch_digest(runtime):
    deployments, store, calls, release, node = runtime
    batch, _ = deployments.reserve_subworkflow_batch(parent_execution_id="root", root_execution_id="root",
        call_node_id=node.id, target_project_id=release.project_id, target_version=release.version,
        item_count=1, input_digest="a" * 64)

    async def item(message):
        return await main.run_private_subworkflow_call(node=node, variables={}, task_id="root",
            runtime_metadata={"workflow_deployment_execution_id": "root", "workflow_root_execution_id": "root"},
            cancellation_requested=lambda: False, resolved_inputs_override={"message": message},
            batch_occurrence_key=batch.occurrence_key, batch_index=0, input_digest=batch.input_digest)

    await item("第一项")
    before = deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowDeploymentConflictError):
        await item("被替换的第一项")
    assert (deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()) == before
    assert calls == ["dispatched"]


@pytest.mark.asyncio
async def test_concurrent_duplicate_call_dispatches_once(runtime, monkeypatch):
    _, _, calls, _, node = runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        async with execution_operation("model_call"):
            calls.append("dispatched")
            entered.set()
            await release.wait()
            yield "合成结果"

    monkeypatch.setattr(main, "stream_workflow_llm_text", blocked)
    first = asyncio.create_task(invoke(node), context=contextvars.Context())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(invoke(node), context=contextvars.Context())
        await asyncio.sleep(0.1)
        assert calls == ["dispatched"]
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, *([second] if second else [])), 5)
    assert len(results) == 2 and results[0] == results[1]
    assert calls == ["dispatched"]


@pytest.mark.asyncio
async def test_distinct_children_share_root_limit_without_context(runtime, monkeypatch):
    _, store, calls, _, node = runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        async with execution_operation("model_call"):
            calls.append("dispatched")
            entered.set()
            await release.wait()
            yield "合成结果"

    monkeypatch.setattr(main, "stream_workflow_llm_text", blocked)
    first = asyncio.create_task(invoke(node), context=contextvars.Context())
    other = node.model_copy(deep=True)
    other.id = "another_item"
    try:
        await asyncio.wait_for(entered.wait(), 3)
        with pytest.raises((ValueError, RuntimeError), match="model-call budget"):
            await asyncio.wait_for(asyncio.create_task(invoke(other), context=contextvars.Context()), 3)
    finally:
        release.set()
        await asyncio.wait_for(first, 5)
    assert calls == ["dispatched"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
