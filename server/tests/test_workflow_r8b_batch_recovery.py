"""B3 batch-identity probes; synthetic calls only, no Planner opening."""
from __future__ import annotations

import pytest

import server.main as main
from server.tests.test_workflow_r8a_budget_tree import budget
from server.tests.test_workflow_r8b_entry_evidence import reopen, runtime
from server.workflow_deployments import WorkflowDeploymentConflictError
from server.workflow_native.r23_iteration import workflow_batch_input_digest
from server.xpert_runtime.execution_budget import use_execution_budget


def reserve(deployments, release, node):
    inputs = [{"message": "合成第一项"}, {"message": "合成第二项"}]
    digest = workflow_batch_input_digest(
        target_project_id=release.project_id,
        target_version=release.version,
        resolved_inputs=inputs,
    )
    batch, _ = deployments.reserve_subworkflow_batch(
        parent_execution_id="root", root_execution_id="root", call_node_id=node.id,
        target_project_id=release.project_id, target_version=release.version,
        item_count=len(inputs), input_digest=digest,
    )
    return batch, inputs


async def invoke_item(node, batch, inputs, index):
    return await main.run_private_subworkflow_call(
        node=node, variables={}, task_id="root",
        runtime_metadata={"workflow_deployment_execution_id": "root",
                          "workflow_root_execution_id": "root"},
        cancellation_requested=lambda: False,
        resolved_inputs_override=inputs[index], batch_occurrence_key=batch.occurrence_key,
        batch_index=index, input_digest=batch.input_digest,
    )


@pytest.mark.asyncio
async def test_batch_restart_reuses_completed_item_and_preserves_root_limit(runtime, monkeypatch):
    deployments, store, calls, release, node = runtime
    batch, inputs = reserve(deployments, release, node)
    first = await invoke_item(node, batch, inputs, 0)
    deployments, store = reopen(monkeypatch, deployments, store)
    claimed = store.claim("root", worker_id="batch-restart")
    restored_batch, restored_inputs = reserve(deployments, release, node)
    assert restored_batch.occurrence_key == batch.occurrence_key
    with use_execution_budget(budget(store, "root", claimed.lease_token)):
        reused = await invoke_item(node, restored_batch, restored_inputs, 0)
        assert reused == first
        with pytest.raises((ValueError, RuntimeError), match="model-call budget"):
            await invoke_item(node, restored_batch, restored_inputs, 1)
    assert calls == ["dispatched"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    children = deployments.list_executions(release.project_id)
    assert len({child.task_id for child in children}) == len(children)
    assert sum(child.status == "completed" for child in children) == 1


@pytest.mark.asyncio
async def test_batch_item_input_drift_cannot_hide_behind_original_batch_digest(runtime, monkeypatch):
    deployments, store, calls, release, node = runtime
    batch, inputs = reserve(deployments, release, node)
    await invoke_item(node, batch, inputs, 0)
    deployments, store = reopen(monkeypatch, deployments, store)
    inputs[0] = {"message": "被替换的第一项"}
    before = deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowDeploymentConflictError):
        await invoke_item(node, batch, inputs, 0)
    assert (deployments.snapshot_path.read_bytes(), store.snapshot_path.read_bytes()) == before
    assert calls == ["dispatched"]
