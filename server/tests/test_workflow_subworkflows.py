from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import server.api.workflow_deployments as deployment_api
import server.main as main_module
from server.workflow_deployments import (
    WorkflowDeploymentConflictError,
    WorkflowDeploymentStore,
    WorkflowDeploymentValidationError,
)
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.xpert_runtime.run_registry import RunRegistry


def callable_workflow(
    *,
    invoke: dict | None = None,
    title: str = "callable",
) -> dict:
    nodes = [
        {
            "id": "entry",
            "type": "workflow_call_entry",
            "data": {
                "kind": "workflow_call_entry",
                "eventVariable": "call_event",
            },
        }
    ]
    if invoke is not None:
        nodes.append(
            {
                "id": "invoke",
                "type": "invoke_workflow",
                "data": {"kind": "invoke_workflow", **invoke},
            }
        )
    nodes.append(
        {
            "id": "output",
            "type": "output",
            "data": {
                "kind": "output",
                "outputVariable": "workflow_result" if invoke else "message",
            },
        }
    )
    return {
        "id": "draft",
        "title": title,
        "variables": [
            {
                "id": "input-message",
                "name": "message",
                "kind": "input",
                "valueType": "text",
            },
            {
                "id": "input-count",
                "name": "count",
                "kind": "input",
                "valueType": "number",
                "defaultValue": 1,
            },
            {
                "id": "constant-label",
                "name": "internal_label",
                "kind": "constant",
                "valueType": "text",
                "defaultValue": "internal",
            },
        ],
        "nodes": nodes,
        "edges": [
            {
                "id": f"edge-{index}",
                "source": nodes[index]["id"],
                "target": nodes[index + 1]["id"],
            }
            for index in range(len(nodes) - 1)
        ],
    }


def failing_callable_workflow() -> dict:
    workflow = callable_workflow(title="failing callable")
    workflow["nodes"].insert(
        1,
        {
            "id": "parse",
            "type": "json_deserialize",
            "data": {
                "kind": "json_deserialize",
                "inputVariable": "message",
                "outputVariable": "parsed",
            },
        },
    )
    workflow["nodes"][-1]["data"]["outputVariable"] = "parsed"
    workflow["edges"] = [
        {"id": "e1", "source": "entry", "target": "parse"},
        {"id": "e2", "source": "parse", "target": "output"},
    ]
    return workflow


def model_callable_workflow() -> dict:
    workflow = callable_workflow(title="model callable")
    workflow["nodes"].insert(
        1,
        {
            "id": "model",
            "type": "llm",
            "data": {
                "kind": "llm",
                "modelId": "test/model",
                "prompt": "{{message}}",
                "outputVariable": "model_output",
            },
        },
    )
    workflow["nodes"][-1]["data"]["outputVariable"] = "model_output"
    workflow["edges"] = [
        {"id": "e1", "source": "entry", "target": "model"},
        {"id": "e2", "source": "model", "target": "output"},
    ]
    return workflow


def manual_caller(target_project_id: str, target_version: int) -> dict:
    return {
        "id": "draft",
        "title": "caller",
        "variables": [
            {
                "id": "input-prompt",
                "name": "prompt",
                "kind": "input",
                "valueType": "text",
            }
        ],
        "nodes": [
            {
                "id": "entry",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "invoke",
                "type": "invoke_workflow",
                "data": {
                    "kind": "invoke_workflow",
                    "targetProjectId": target_project_id,
                    "targetVersion": target_version,
                    "inputBindings": {
                        "message": {"source": "variable", "variable": "prompt"}
                    },
                    "resultVariable": "workflow_result",
                    "timeoutSeconds": 60,
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "workflow_result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "entry", "target": "invoke"},
            {"id": "e2", "source": "invoke", "target": "output"},
        ],
    }


def test_callable_publish_activation_interface_and_binding_guards(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    target_project = store.create_project(callable_workflow())
    target_release = store.publish(target_project.project_id)
    assert target_release.trigger_kind == "call"
    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            target_project.project_id,
            target_release.version,
            webhooks_enabled=False,
        )
    target_deployment, _ = store.activate(
        target_project.project_id,
        target_release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    assert target_deployment.trigger_kind == "call"

    caller = store.create_project(
        manual_caller(target_project.project_id, target_release.version)
    )
    caller_release = store.publish(caller.project_id)
    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            caller.project_id,
            caller_release.version,
            webhooks_enabled=False,
        )
    store.activate(
        caller.project_id,
        caller_release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )

    invalid = manual_caller(target_project.project_id, target_release.version)
    invalid["nodes"][1]["data"]["inputBindings"] = {
        "internal_label": {"source": "literal", "value": "override"}
    }
    invalid_project = store.create_project(invalid)
    with pytest.raises(WorkflowDeploymentValidationError, match="unknown input"):
        store.publish(invalid_project.project_id)

    wrong_type = manual_caller(target_project.project_id, target_release.version)
    wrong_type["nodes"][1]["data"]["inputBindings"] = {
        "message": {"source": "literal", "value": 42}
    }
    wrong_type_project = store.create_project(wrong_type)
    with pytest.raises(WorkflowDeploymentValidationError, match="wrong type"):
        store.publish(wrong_type_project.project_id)

    waiting = callable_workflow(title="waiting callable")
    waiting["nodes"].insert(
        1,
        {
            "id": "wait",
            "type": "suspend_wait",
            "data": {
                "kind": "suspend_wait",
                "waitMode": "duration",
                "durationSeconds": 1,
                "outputVariable": "resume_event",
            },
        },
    )
    waiting["nodes"][-1]["data"]["outputVariable"] = "resume_event"
    waiting["edges"] = [
        {"id": "e1", "source": "entry", "target": "wait"},
        {"id": "e2", "source": "wait", "target": "output"},
    ]
    waiting_project = store.create_project(waiting)
    with pytest.raises(WorkflowDeploymentValidationError, match="waiting"):
        store.publish(waiting_project.project_id)


@pytest.mark.parametrize(
    ("kind", "data", "output_variable"),
    [
        (
            "human_intervention",
            {
                "kind": "human_intervention",
                "contractVersion": 2,
                "interactionMode": "approval",
                "prompt": "Approve the callable workflow",
                "outputVariable": "decision",
                "timeoutSeconds": 3600,
            },
            "decision",
        ),
        (
            "mcp_tool",
            {
                "kind": "mcp_tool",
                "contractVersion": 2,
                "serverId": "server_alpha",
                "toolName": "search",
                "inputSchemaChecksum": "0" * 64,
                "argumentMode": "fields",
                "argumentBindings": [],
                "outputVariable": "mcp_result",
            },
            "mcp_result",
        ),
    ],
)
def test_callable_publish_rejects_r20_waiting_nodes(
    tmp_path,
    kind: str,
    data: dict,
    output_variable: str,
) -> None:
    workflow = callable_workflow(title=f"waiting {kind}")
    workflow["nodes"].insert(
        1,
        {"id": "waiting", "type": kind, "data": data},
    )
    workflow["nodes"][-1]["data"]["outputVariable"] = output_variable
    workflow["edges"] = [
        {"id": "e1", "source": "entry", "target": "waiting"},
        {"id": "e2", "source": "waiting", "target": "output"},
    ]
    store = WorkflowDeploymentStore(tmp_path / kind)
    project = store.create_project(workflow)

    with pytest.raises(WorkflowDeploymentValidationError, match="waiting"):
        store.publish(project.project_id)


def test_subworkflow_relation_is_stable_bounded_and_additive(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    target = store.create_project(callable_workflow())
    release = store.publish(target.project_id)
    store.activate(
        target.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    first, created = store.materialize_subworkflow_execution(
        parent_execution_id="wfx_parent",
        root_execution_id="wfx_root",
        parent_depth=0,
        call_node_id="invoke",
        target_project_id=target.project_id,
        target_version=release.version,
        test_mode=False,
        suppress_failure_dispatch=False,
    )
    duplicate, duplicate_created = store.materialize_subworkflow_execution(
        parent_execution_id="wfx_parent",
        root_execution_id="wfx_root",
        parent_depth=0,
        call_node_id="invoke",
        target_project_id=target.project_id,
        target_version=release.version,
        test_mode=False,
        suppress_failure_dispatch=False,
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate.execution_id == first.execution_id
    assert first.task_id and first.task_id.startswith("wft_")
    summary = store.serialize_execution(first)
    assert summary["parent_execution_id"] == "wfx_parent"
    assert summary["root_execution_id"] == "wfx_root"
    assert summary["call_node_id"] == "invoke"

    with pytest.raises(WorkflowDeploymentConflictError, match="depth"):
        store.materialize_subworkflow_execution(
            parent_execution_id="wfx_deep",
            root_execution_id="wfx_root_deep",
            parent_depth=8,
            call_node_id="invoke",
            target_project_id=target.project_id,
            target_version=release.version,
            test_mode=False,
            suppress_failure_dispatch=False,
        )

    for index in range(32):
        store.materialize_subworkflow_execution(
            parent_execution_id=f"wfx_parent_{index}",
            root_execution_id="wfx_bounded_root",
            parent_depth=0,
            call_node_id="invoke",
            target_project_id=target.project_id,
            target_version=release.version,
            test_mode=False,
            suppress_failure_dispatch=False,
        )
    with pytest.raises(WorkflowDeploymentConflictError, match="32"):
        store.materialize_subworkflow_execution(
            parent_execution_id="wfx_parent_overflow",
            root_execution_id="wfx_bounded_root",
            parent_depth=0,
            call_node_id="invoke",
            target_project_id=target.project_id,
            target_version=release.version,
            test_mode=False,
            suppress_failure_dispatch=False,
        )

    reloaded = WorkflowDeploymentStore(tmp_path)
    restored = reloaded.get_execution(first.execution_id)
    assert restored is not None
    assert restored.parent_execution_id == "wfx_parent"
    relation = reloaded.subworkflow_relation_for_child(first.execution_id)
    assert relation is not None
    assert relation.task_id == first.task_id
    snapshot = json.loads(reloaded.snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["version"] == "workflow-deployments-v4"
    assert len(snapshot["subworkflow_relations"]) == 33


def test_subworkflow_direct_and_indirect_cycles_are_rejected(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    project_a = store.create_project(callable_workflow(title="A"))
    release_a = store.publish(project_a.project_id)
    store.activate(
        project_a.project_id,
        release_a.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    direct = callable_workflow(
        title="A direct self",
        invoke={
            "targetProjectId": project_a.project_id,
            "targetVersion": release_a.version,
            "inputBindings": {
                "message": {"source": "variable", "variable": "message"}
            },
            "resultVariable": "workflow_result",
            "timeoutSeconds": 60,
        },
    )
    store.save_draft(
        project_a.project_id,
        expected_revision=1,
        workflow=direct,
    )
    with pytest.raises(WorkflowDeploymentConflictError, match="itself"):
        store.publish(project_a.project_id)

    project_b = store.create_project(
        callable_workflow(
            title="B calls A",
            invoke={
                "targetProjectId": project_a.project_id,
                "targetVersion": release_a.version,
                "inputBindings": {
                    "message": {"source": "variable", "variable": "message"}
                },
                "resultVariable": "workflow_result",
                "timeoutSeconds": 60,
            },
        )
    )
    release_b = store.publish(project_b.project_id)
    store.activate(
        project_b.project_id,
        release_b.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    indirect = callable_workflow(
        title="A calls B",
        invoke={
            "targetProjectId": project_b.project_id,
            "targetVersion": release_b.version,
            "inputBindings": {
                "message": {"source": "variable", "variable": "message"}
            },
            "resultVariable": "workflow_result",
            "timeoutSeconds": 60,
        },
    )
    store.save_draft(
        project_a.project_id,
        expected_revision=2,
        workflow=indirect,
    )
    with pytest.raises(WorkflowDeploymentConflictError, match="cycle"):
        store.publish(project_a.project_id)


@pytest.mark.asyncio
async def test_subworkflow_api_flag_and_no_public_invoke_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    monkeypatch.setattr(deployment_api, "_store", store)
    target = store.create_project(callable_workflow())
    release = store.publish(target.project_id)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "false")
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        disabled = await client.post(
            f"/api/workflows/{target.project_id}/versions/{release.version}/activate"
        )
        assert disabled.status_code == 409
        monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
        activated = await client.post(
            f"/api/workflows/{target.project_id}/versions/{release.version}/activate"
        )
        assert activated.status_code == 200
        unavailable = await client.post(
            f"/api/workflows/{target.project_id}/invoke",
            json={"inputs": {"message": "not allowed"}},
        )
        assert unavailable.status_code == 404


@pytest.mark.asyncio
async def test_private_subworkflow_runtime_reuses_result_and_interface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")

    target = deployment_store.create_project(callable_workflow())
    release = deployment_store.publish(target.project_id)
    deployment_store.activate(
        target.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    node = main_module.WorkflowNodePayload.model_validate(
        {
            "id": "invoke",
            "type": "invoke_workflow",
            "data": {
                "kind": "invoke_workflow",
                "targetProjectId": target.project_id,
                "targetVersion": release.version,
                "inputBindings": {
                    "message": {"source": "literal", "value": "hello child"}
                },
                "resultVariable": "workflow_result",
                "timeoutSeconds": 5,
            },
        }
    )
    first = await main_module.run_private_subworkflow_call(
        node=node,
        variables={},
        task_id="manual-parent",
        runtime_metadata={},
        cancellation_requested=lambda: False,
    )
    second = await main_module.run_private_subworkflow_call(
        node=node,
        variables={},
        task_id="manual-parent",
        runtime_metadata={},
        cancellation_requested=lambda: False,
    )
    assert first == second
    assert first["result"] == "hello child"
    child_summary = deployment_store.serialize_execution(
        deployment_store.get_execution(first["executionId"])
    )
    assert child_summary["test_mode"] is True
    assert child_summary["parent_execution_id"] == "test:manual-parent"

    running_node = node.model_copy(deep=True)
    running_node.id = "invoke-running"
    running_child, _ = deployment_store.materialize_subworkflow_execution(
        parent_execution_id="test:manual-parent",
        root_execution_id="test:manual-parent",
        parent_depth=0,
        call_node_id=running_node.id,
        target_project_id=target.project_id,
        target_version=release.version,
        test_mode=True,
        suppress_failure_dispatch=False,
    )
    deployment_store.claim_execution(
        running_child.execution_id,
        worker_id="other-worker",
        lease_seconds=5,
    )
    execution_store.create(
        task_id=str(running_child.task_id),
        run_id="run-other-worker",
        run_type="workflow",
        workflow=release.workflow,
        inputs={"message": "hello child"},
        source_kind="workflow_deployment",
    )

    async def finish_other_worker() -> None:
        await asyncio.sleep(0.1)
        execution_store.complete(str(running_child.task_id), result="other result")
        deployment_store.complete_execution(
            running_child.execution_id,
            task_id=running_child.task_id,
            run_id="run-other-worker",
            result="other result",
        )

    finisher = asyncio.create_task(finish_other_worker())
    reused_running = await main_module.run_private_subworkflow_call(
        node=running_node,
        variables={},
        task_id="manual-parent",
        runtime_metadata={},
        cancellation_requested=lambda: False,
    )
    await finisher
    assert reused_running["result"] == "other result"

    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/workflows/{target.project_id}/versions/{release.version}/interface"
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is True
    assert payload["trigger_kind"] == "call"
    assert [item["name"] for item in payload["inputs"]] == ["message", "count"]
    assert payload["inputs"][0]["required"] is True
    assert payload["inputs"][1]["default_value"] == 1

    missing_node = node.model_copy(deep=True)
    missing_node.id = "invoke-missing"
    missing_node.data["inputBindings"] = {}
    with pytest.raises(ValueError, match="missing required input"):
        await main_module.run_private_subworkflow_call(
            node=missing_node,
            variables={},
            task_id="manual-parent",
            runtime_metadata={},
            cancellation_requested=lambda: False,
        )


@pytest.mark.asyncio
async def test_private_subworkflow_timeout_cancels_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    clock_values = iter((100.0, 102.0))
    monkeypatch.setattr(
        main_module,
        "workflow_call_monotonic",
        lambda: next(clock_values),
    )
    target = deployment_store.create_project(callable_workflow())
    release = deployment_store.publish(target.project_id)
    deployment_store.activate(
        target.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )

    async def never_finishes(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(main_module, "execute_workflow_trigger", never_finishes)
    node = main_module.WorkflowNodePayload.model_validate(
        {
            "id": "invoke-timeout",
            "type": "invoke_workflow",
            "data": {
                "kind": "invoke_workflow",
                "targetProjectId": target.project_id,
                "targetVersion": release.version,
                "inputBindings": {
                    "message": {"source": "literal", "value": "hello"}
                },
                "resultVariable": "workflow_result",
                "timeoutSeconds": 1,
            },
        }
    )
    with pytest.raises(ValueError, match="timed out"):
        await main_module.run_private_subworkflow_call(
            node=node,
            variables={},
            task_id="timeout-parent",
            runtime_metadata={},
            cancellation_requested=lambda: False,
        )
    child = deployment_store.list_executions(target.project_id)[0]
    assert child.status == "cancelled"
    assert "timed out" in str(child.error_summary).lower()


@pytest.mark.asyncio
async def test_private_subworkflow_reconciles_durable_terminal_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    target = deployment_store.create_project(callable_workflow())
    release = deployment_store.publish(target.project_id)
    deployment_store.activate(
        target.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    child, _ = deployment_store.materialize_subworkflow_execution(
        parent_execution_id="test:recovery-parent",
        root_execution_id="test:recovery-parent",
        parent_depth=0,
        call_node_id="invoke-recovery",
        target_project_id=target.project_id,
        target_version=release.version,
        test_mode=True,
        suppress_failure_dispatch=False,
        now=100,
    )
    deployment_store.claim_execution(
        child.execution_id,
        worker_id="crashed-worker",
        lease_seconds=5,
        now=100,
    )
    execution_store.create(
        task_id=str(child.task_id),
        run_id="run-before-crash",
        run_type="workflow",
        workflow=release.workflow,
        inputs={"message": "already completed"},
        source_kind="workflow_deployment",
    )
    execution_store.complete(str(child.task_id), result="durable result")

    async def must_not_replay(*_args, **_kwargs):
        raise AssertionError("completed durable child was replayed")

    monkeypatch.setattr(main_module, "execute_workflow_trigger", must_not_replay)
    node = main_module.WorkflowNodePayload.model_validate(
        {
            "id": "invoke-recovery",
            "type": "invoke_workflow",
            "data": {
                "kind": "invoke_workflow",
                "targetProjectId": target.project_id,
                "targetVersion": release.version,
                "inputBindings": {
                    "message": {"source": "literal", "value": "already completed"}
                },
                "resultVariable": "workflow_result",
                "timeoutSeconds": 5,
            },
        }
    )
    result = await main_module.run_private_subworkflow_call(
        node=node,
        variables={},
        task_id="recovery-parent",
        runtime_metadata={},
        cancellation_requested=lambda: False,
    )
    assert result["result"] == "durable result"
    restored = deployment_store.get_execution(child.execution_id)
    assert restored is not None
    assert restored.status == "completed"
    assert restored.run_id == "run-before-crash"


@pytest.mark.asyncio
async def test_private_subworkflow_rejects_inactive_target_and_cancels_with_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    target = deployment_store.create_project(callable_workflow())
    release = deployment_store.publish(target.project_id)
    deployment_store.activate(
        target.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    node = main_module.WorkflowNodePayload.model_validate(
        {
            "id": "invoke-cancel",
            "type": "invoke_workflow",
            "data": {
                "kind": "invoke_workflow",
                "targetProjectId": target.project_id,
                "targetVersion": release.version,
                "inputBindings": {
                    "message": {"source": "literal", "value": "cancel me"}
                },
                "resultVariable": "workflow_result",
                "timeoutSeconds": 5,
            },
        }
    )

    async def never_finishes(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(main_module, "execute_workflow_trigger", never_finishes)
    result = await main_module.run_private_subworkflow_call(
        node=node,
        variables={},
        task_id="cancel-parent",
        runtime_metadata={},
        cancellation_requested=lambda: True,
    )
    assert result["status"] == "cancelled"
    child = deployment_store.list_executions(target.project_id)[0]
    assert child.status == "cancelled"

    deployment_store.deactivate(target.project_id, release.version)
    inactive_node = node.model_copy(deep=True)
    inactive_node.id = "invoke-inactive"
    with pytest.raises(WorkflowDeploymentConflictError, match="not currently active"):
        await main_module.run_private_subworkflow_call(
            node=inactive_node,
            variables={},
            task_id="inactive-parent",
            runtime_metadata={},
            cancellation_requested=lambda: False,
        )


@pytest.mark.asyncio
async def test_private_subworkflow_failure_propagates_to_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    target = deployment_store.create_project(failing_callable_workflow())
    release = deployment_store.publish(target.project_id)
    deployment_store.activate(
        target.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    node = main_module.WorkflowNodePayload.model_validate(
        {
            "id": "invoke-failure",
            "type": "invoke_workflow",
            "data": {
                "kind": "invoke_workflow",
                "targetProjectId": target.project_id,
                "targetVersion": release.version,
                "inputBindings": {
                    "message": {"source": "literal", "value": "not-json"}
                },
                "resultVariable": "workflow_result",
                "timeoutSeconds": 5,
            },
        }
    )
    with pytest.raises(ValueError):
        await main_module.run_private_subworkflow_call(
            node=node,
            variables={},
            task_id="failure-parent",
            runtime_metadata={},
            cancellation_requested=lambda: False,
        )
    child = deployment_store.list_executions(target.project_id)[0]
    assert child.status == "failed"
    assert child.test_mode is True


@pytest.mark.asyncio
async def test_model_preflight_failure_persists_child_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    run_registry = RunRegistry()
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(main_module, "run_registry", run_registry)
    monkeypatch.setattr(main_module, "workflow_task_store", {})
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")

    target = deployment_store.create_project(model_callable_workflow())
    release = deployment_store.publish(target.project_id)
    deployment_store.activate(
        target.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    node = main_module.WorkflowNodePayload.model_validate(
        {
            "id": "invoke-model-preflight",
            "type": "invoke_workflow",
            "data": {
                "kind": "invoke_workflow",
                "targetProjectId": target.project_id,
                "targetVersion": release.version,
                "inputBindings": {
                    "message": {"source": "literal", "value": "hello child"}
                },
                "resultVariable": "workflow_result",
                "timeoutSeconds": 5,
            },
        }
    )

    with pytest.raises(ValueError, match="LLM"):
        await main_module.run_private_subworkflow_call(
            node=node,
            variables={},
            task_id="model-preflight-parent",
            runtime_metadata={},
            cancellation_requested=lambda: False,
        )

    children = deployment_store.list_executions(target.project_id)
    assert len(children) == 1
    child = children[0]
    assert child.status == "failed"
    assert child.task_id
    assert child.run_id
    durable = execution_store.require(child.task_id)
    assert durable.status == "failed"
    assert durable.run_id == child.run_id
    assert durable.error == main_module.LLM_GATEWAY_NOT_CONFIGURED_MESSAGE
    assert main_module.workflow_task_store == {}
    relation = deployment_store.subworkflow_relation_for_child(child.execution_id)
    assert relation is not None
    assert relation.task_id == child.task_id
