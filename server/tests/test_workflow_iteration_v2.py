from __future__ import annotations

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
from server.workflow_native.r23_iteration import (
    MAX_LOCAL_ITEMS,
    WorkflowIterationError,
    execute_template_map,
    resolve_workflow_map_inputs,
    validate_iteration_v2_config,
    workflow_batch_input_digest,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xpert_runtime.execution_store import WorkflowExecutionStore


def template_config() -> dict:
    return {
        "contractVersion": 2,
        "mode": "template_map",
        "inputVariable": "items",
        "itemVariable": "item",
        "indexVariable": "item_index",
        "itemTemplate": "{{item_index}}={{item}}/{{prefix}}",
        "outputVariable": "mapped",
    }


def workflow_config(project_id: str = "wf_" + "1" * 32) -> dict:
    return {
        "contractVersion": 2,
        "mode": "workflow_map",
        "inputVariable": "items",
        "itemVariable": "item",
        "indexVariable": "item_index",
        "outputVariable": "receipts",
        "targetProjectId": project_id,
        "targetVersion": 1,
        "inputBindings": {
            "message": {"source": "item"},
            "position": {"source": "index"},
            "prefix": {"source": "variable", "variable": "prefix"},
            "enabled": {"source": "literal", "value": True},
        },
        "timeoutSeconds": 60,
    }


def render(template: str, variables: dict) -> str:
    result = template
    for name, value in variables.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        result = result.replace("{{" + name + "}}", text)
    return result


def callable_workflow() -> dict:
    return {
        "id": "callable",
        "title": "callable",
        "variables": [
            {"id": "v-message", "name": "message", "kind": "input", "valueType": "json"},
            {"id": "v-position", "name": "position", "kind": "input", "valueType": "number"},
            {"id": "v-prefix", "name": "prefix", "kind": "input", "valueType": "text"},
            {"id": "v-enabled", "name": "enabled", "kind": "input", "valueType": "boolean"},
        ],
        "nodes": [
            {
                "id": "entry",
                "type": "workflow_call_entry",
                "data": {"kind": "workflow_call_entry", "eventVariable": "call_event"},
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "message"},
            },
        ],
        "edges": [{"id": "edge", "source": "entry", "target": "output"}],
    }


def batch_caller(project_id: str, *, contract_version: int = 2) -> dict:
    data = {
        "kind": "iteration",
        **workflow_config(project_id),
    }
    if contract_version == 1:
        data = {
            "kind": "iteration",
            "inputVariable": "items",
            "iterationVariable": "item",
            "itemTemplate": "{{item}}",
            "outputVariable": "receipts",
        }
    return {
        "id": "caller",
        "title": "caller",
        "variables": [
            {"id": "v-items", "name": "items", "kind": "input", "valueType": "json"},
            {
                "id": "v-prefix",
                "name": "prefix",
                "kind": "input",
                "valueType": "text",
                "defaultValue": "p",
            },
        ],
        "nodes": [
            {
                "id": "entry",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {"id": "batch", "type": "iteration", "data": data},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "receipts"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "entry", "target": "batch"},
            {"id": "e2", "source": "batch", "target": "output"},
        ],
    }


def parse_sse_events(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def test_template_map_requires_real_array_and_keeps_locals_scoped() -> None:
    variables = {"items": ["甲", {"id": 2}], "prefix": "订单"}
    result = execute_template_map(template_config(), variables, render=render)

    assert result == ['0=甲/订单', '1={"id": 2}/订单']
    assert "item" not in variables
    assert "item_index" not in variables
    assert "mapped" not in variables

    with pytest.raises(WorkflowIterationError, match="must be a JSON array"):
        execute_template_map(
            template_config(),
            {"items": "a,b", "prefix": "x"},
            render=render,
        )
    with pytest.raises(WorkflowIterationError, match=str(MAX_LOCAL_ITEMS)):
        execute_template_map(
            template_config(),
            {"items": [None] * (MAX_LOCAL_ITEMS + 1), "prefix": "x"},
            render=render,
        )


def test_workflow_map_bindings_are_typed_and_require_one_item_source() -> None:
    data = workflow_config()
    validate_iteration_v2_config(data)
    resolved = resolve_workflow_map_inputs(
        data,
        {"prefix": "订单"},
        item={"id": 9},
        index=0,
    )
    assert resolved == {
        "message": {"id": 9},
        "position": 0,
        "prefix": "订单",
        "enabled": True,
    }

    invalid = workflow_config()
    invalid["inputBindings"]["message"] = {"source": "index"}
    with pytest.raises(WorkflowIterationError, match="exactly one item"):
        validate_iteration_v2_config(invalid)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_code"),
    [
        ("itemVariable", "items", "ITERATION_LOCAL_VARIABLES_SHADOW_INPUT"),
        ("indexVariable", "items", "ITERATION_LOCAL_VARIABLES_SHADOW_INPUT"),
        ("outputVariable", "items", "ITERATION_OUTPUT_VARIABLE_CONFLICT"),
    ],
)
def test_iteration_v2_rejects_input_shadowing(
    field_name: str,
    value: str,
    expected_code: str,
) -> None:
    data = template_config()
    data[field_name] = value

    with pytest.raises(WorkflowIterationError) as exc_info:
        validate_iteration_v2_config(data)

    assert exc_info.value.code == expected_code


def test_iteration_v2_rejects_local_names_that_shadow_visible_variables() -> None:
    workflow = {
        "id": "iteration-shadowing",
        "title": "iteration shadowing",
        "variables": [
            {"id": "v-items", "name": "items", "kind": "input", "valueType": "json"},
            {"id": "v-prefix", "name": "prefix", "kind": "constant", "valueType": "text", "defaultValue": "订单"},
        ],
        "nodes": [
            {
                "id": "entry",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "batch",
                "type": "iteration",
                "data": {
                    "kind": "iteration",
                    **template_config(),
                    "itemVariable": "prefix",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "mapped"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "entry", "target": "batch"},
            {"id": "e2", "source": "batch", "target": "output"},
        ],
    }

    validation = validate_workflow_graph(
        NativeWorkflowDefinition.model_validate(workflow)
    )

    assert "iteration_local_variable_shadows_visible_variable" in {
        issue.code for issue in validation.issues
    }


def test_template_map_stops_rendering_when_output_limit_is_reached() -> None:
    data = template_config()
    data["itemTemplate"] = "x" * 20_000
    items = list(range(300))
    render_count = 0

    def counted_render(template: str, _variables: dict) -> str:
        nonlocal render_count
        render_count += 1
        return template

    with pytest.raises(WorkflowIterationError) as exc_info:
        execute_template_map(
            data,
            {"items": items, "prefix": "订单"},
            render=counted_render,
        )

    assert exc_info.value.code == "ITERATION_OUTPUT_TOO_LARGE"
    assert render_count < len(items)


def test_batch_reservation_is_durable_idempotent_and_input_pinned(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    target_project = store.create_project(callable_workflow())
    release = store.publish(target_project.project_id)
    store.activate(
        target_project.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    digest = workflow_batch_input_digest(
        target_project_id=target_project.project_id,
        target_version=release.version,
        resolved_inputs=[{"message": "a"}, {"message": "b"}],
    )
    batch, created = store.reserve_subworkflow_batch(
        parent_execution_id="parent",
        root_execution_id="root",
        call_node_id="batch-node",
        target_project_id=target_project.project_id,
        target_version=release.version,
        item_count=2,
        input_digest=digest,
    )
    assert created is True
    first, first_created = store.materialize_subworkflow_execution(
        parent_execution_id="parent",
        root_execution_id="root",
        parent_depth=0,
        call_node_id="batch-node",
        target_project_id=target_project.project_id,
        target_version=release.version,
        test_mode=True,
        suppress_failure_dispatch=False,
        batch_occurrence_key=batch.occurrence_key,
        batch_index=0,
        input_digest=digest,
    )
    repeated, repeated_created = store.materialize_subworkflow_execution(
        parent_execution_id="parent",
        root_execution_id="root",
        parent_depth=0,
        call_node_id="batch-node",
        target_project_id=target_project.project_id,
        target_version=release.version,
        test_mode=True,
        suppress_failure_dispatch=False,
        batch_occurrence_key=batch.occurrence_key,
        batch_index=0,
        input_digest=digest,
    )
    assert first_created is True
    assert repeated_created is False
    assert repeated.execution_id == first.execution_id
    assert first.batch_index == 0

    reloaded = WorkflowDeploymentStore(tmp_path)
    resumed, resumed_created = reloaded.reserve_subworkflow_batch(
        parent_execution_id="parent",
        root_execution_id="root",
        call_node_id="batch-node",
        target_project_id=target_project.project_id,
        target_version=release.version,
        item_count=2,
        input_digest=digest,
    )
    assert resumed_created is False
    assert resumed.occurrence_key == batch.occurrence_key
    with pytest.raises(WorkflowDeploymentConflictError, match="changed"):
        reloaded.reserve_subworkflow_batch(
            parent_execution_id="parent",
            root_execution_id="root",
            call_node_id="batch-node",
            target_project_id=target_project.project_id,
            target_version=release.version,
            item_count=2,
            input_digest="f" * 64,
        )
    reloaded.reserve_subworkflow_batch(
        parent_execution_id="another-parent",
        root_execution_id="root",
        call_node_id="another-batch",
        target_project_id=target_project.project_id,
        target_version=release.version,
        item_count=30,
        input_digest="a" * 64,
    )
    with pytest.raises(WorkflowDeploymentConflictError, match="32"):
        reloaded.reserve_subworkflow_batch(
            parent_execution_id="third-parent",
            root_execution_id="root",
            call_node_id="third-batch",
            target_project_id=target_project.project_id,
            target_version=release.version,
            item_count=1,
            input_digest="b" * 64,
        )

    payload = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    relation = payload["subworkflow_relations"][0]
    relation["occurrence_key"] = "call:parent:batch-node:1"
    child = next(
        item
        for item in payload["executions"]
        if item["execution_id"] == relation["child_execution_id"]
    )
    child["occurrence_key"] = relation["occurrence_key"]
    store.snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(
        WorkflowDeploymentValidationError,
        match="snapshot is invalid",
    ):
        WorkflowDeploymentStore(tmp_path)


def test_publish_rejects_v1_and_accepts_fixed_v2_batch_target(tmp_path) -> None:
    store = WorkflowDeploymentStore(tmp_path)
    target_project = store.create_project(callable_workflow())
    target_release = store.publish(target_project.project_id)
    store.activate(
        target_project.project_id,
        target_release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )

    legacy = store.create_project(
        batch_caller(target_project.project_id, contract_version=1)
    )
    with pytest.raises(WorkflowDeploymentValidationError, match="Legacy iteration"):
        store.publish(legacy.project_id)

    caller = store.create_project(batch_caller(target_project.project_id))
    release = store.publish(caller.project_id)

    wrong_index_type = batch_caller(target_project.project_id)
    wrong_index_type["nodes"][1]["data"]["inputBindings"]["prefix"] = {
        "source": "index"
    }
    invalid_caller = store.create_project(wrong_index_type)
    with pytest.raises(WorkflowDeploymentValidationError, match="index.*wrong type"):
        store.publish(invalid_caller.project_id)

    with pytest.raises(WorkflowDeploymentConflictError, match="disabled"):
        store.activate(
            caller.project_id,
            release.version,
            webhooks_enabled=False,
        )
    store.activate(
        caller.project_id,
        release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )


@pytest.mark.asyncio
async def test_template_batch_runtime_outputs_typed_array_without_local_leak() -> None:
    workflow = {
        "id": "template-batch",
        "title": "template batch",
        "variables": [
            {"id": "v-items", "name": "items", "kind": "input", "valueType": "json"},
            {
                "id": "v-prefix",
                "name": "prefix",
                "kind": "constant",
                "valueType": "text",
                "defaultValue": "订单",
            },
        ],
        "nodes": [
            {
                "id": "entry",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "batch",
                "type": "iteration",
                "data": {"kind": "iteration", **template_config()},
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "mapped"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "entry", "target": "batch"},
            {"id": "e2", "source": "batch", "target": "output"},
        ],
    }
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={"workflow": workflow, "inputs": {"items": ["甲", "乙"]}},
        )

    assert response.status_code == 200, response.text
    end = next(
        event for event in parse_sse_events(response.text)
        if event.get("event") == "workflow_end"
    )
    assert end["variables"]["mapped"] == ["0=甲/订单", "1=乙/订单"]
    assert "item" not in end["variables"]
    assert "item_index" not in end["variables"]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/workflow/run",
            json={
                "workflow": workflow,
                "inputs": {"items": "SENTINEL_BATCH_RAW,a"},
            },
        )
    error = next(
        event
        for event in parse_sse_events(rejected.text)
        if event.get("event") == "error"
    )
    assert error["code"] == "ITERATION_INPUT_NOT_ARRAY"
    assert error["message"] == "Batch processing input must be a JSON array."
    assert "SENTINEL_BATCH_RAW" not in json.dumps(error)
    durable = main_module.workflow_execution_store.get(error["task_id"])
    assert durable is not None
    assert "SENTINEL_BATCH_RAW" not in str(durable.error or "")


@pytest.mark.asyncio
async def test_workflow_batch_runtime_is_sequential_typed_and_safely_summarized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    deployment_store = WorkflowDeploymentStore(tmp_path / "deployments")
    execution_store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_deployment_store", deployment_store)
    monkeypatch.setattr(main_module, "workflow_execution_store", execution_store)
    monkeypatch.setattr(deployment_api, "_store", deployment_store)
    monkeypatch.setattr(
        deployment_api,
        "_trigger_executor",
        main_module.run_deployed_workflow_trigger,
    )
    monkeypatch.setenv("WORKFLOW_SUBWORKFLOWS_ENABLED", "true")
    target = deployment_store.create_project(callable_workflow())
    target_release = deployment_store.publish(target.project_id)
    deployment_store.activate(
        target.project_id,
        target_release.version,
        webhooks_enabled=False,
        subworkflows_enabled=True,
    )
    workflow = batch_caller(target.project_id)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/workflow/run",
            json={
                "workflow": workflow,
                "inputs": {"items": ["甲", "乙"], "prefix": "订单"},
            },
        )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    end = next(event for event in events if event.get("event") == "workflow_end")
    receipts = end["variables"]["receipts"]
    assert [item["index"] for item in receipts] == [0, 1]
    assert [item["result"] for item in receipts] == ["甲", "乙"]
    assert all("item" not in item for item in receipts)
    children = deployment_store.list_executions(target.project_id)
    assert [item.batch_index for item in sorted(children, key=lambda item: item.batch_index)] == [0, 1]
    progress = [
        event["output"] for event in events
        if event.get("event") == "node_delta"
        and event.get("node_id") == "batch"
        and str(event.get("output", "")).startswith("completed ")
    ]
    assert progress == ["completed 1/2", "completed 2/2"]
