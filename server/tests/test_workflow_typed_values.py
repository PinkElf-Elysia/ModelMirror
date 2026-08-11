from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError

import server.main as main_module
from server.main import WorkflowRunRequest, app, render_workflow_template
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.workflow_native.values import (
    deserialize_workflow_value,
    serialize_workflow_value,
    workflow_condition_matches,
)
from server.xpert_runtime import WorkflowExecutionStore


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def clear_request_windows():
    main_module.request_windows.clear()
    yield
    main_module.request_windows.clear()


def _parse_sse_events(text: str) -> list[dict]:
    events: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        events.append(json.loads(line[5:].strip()))
    return events


def _json_round_trip_workflow(*, include_annotation: bool = False) -> dict:
    nodes = [
        {
            "id": "input",
            "type": "input",
            "data": {"kind": "input", "variableName": "user_input"},
        },
        {
            "id": "serialize",
            "type": "json_serialize",
            "data": {
                "kind": "json_serialize",
                "inputVariable": "user_input",
                "outputVariable": "json_text",
                "format": "compact",
            },
        },
        {
            "id": "deserialize",
            "type": "json_deserialize",
            "data": {
                "kind": "json_deserialize",
                "inputVariable": "json_text",
                "outputVariable": "restored",
            },
        },
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "restored"},
        },
    ]
    if include_annotation:
        nodes.append(
            {
                "id": "note",
                "type": "annotation",
                "data": {
                    "kind": "annotation",
                    "content": "This note belongs to the canvas snapshot only.",
                },
            }
        )
    return {
        "id": "typed-json-round-trip",
        "title": "Typed JSON round trip",
        "nodes": nodes,
        "edges": [
            {"id": "e1", "source": "input", "target": "serialize"},
            {"id": "e2", "source": "serialize", "target": "deserialize"},
            {"id": "e3", "source": "deserialize", "target": "output"},
        ],
    }


@pytest.mark.asyncio
async def test_json_nodes_preserve_typed_values_and_annotation_has_no_events(
    client: httpx.AsyncClient,
) -> None:
    value = {
        "name": "typed",
        "items": [1, True, None, {"nested": "value"}],
    }
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _json_round_trip_workflow(include_annotation=True),
            "inputs": {"user_input": value},
        },
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["variables"]["user_input"] == value
    assert completed["variables"]["restored"] == value
    assert completed["variables"]["json_text"] == json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert completed["final_output"] == completed["variables"]["json_text"]
    assert not any(event.get("node_id") == "note" for event in events)


@pytest.mark.asyncio
async def test_invalid_json_deserialize_emits_error_without_coercing_input(
    client: httpx.AsyncClient,
) -> None:
    workflow = _json_round_trip_workflow()
    workflow["nodes"] = [
        workflow["nodes"][0],
        workflow["nodes"][2],
        workflow["nodes"][3],
    ]
    workflow["nodes"][1]["data"]["inputVariable"] = "user_input"
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "deserialize"},
        {"id": "e2", "source": "deserialize", "target": "output"},
    ]
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "{not-json"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    error = next(
        event
        for event in events
        if event.get("event") == "error" and event.get("node_id") == "deserialize"
    )
    assert "invalid" in error["message"].lower()
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["variables"]["restored"] is None
    assert completed["final_output"] == "null"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_value", "expected_value"),
    [([1, 2, 3], 3), ("a,b,c", "3")],
)
async def test_list_operation_preserves_typed_and_legacy_length_contracts(
    client: httpx.AsyncClient,
    input_value: object,
    expected_value: object,
) -> None:
    workflow = {
        "id": "typed-list-length",
        "title": "Typed list length",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "length",
                "type": "list_operation",
                "data": {
                    "kind": "list_operation",
                    "inputVariable": "user_input",
                    "operator": "length",
                    "outputVariable": "length",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "length"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "length"},
            {"id": "e2", "source": "length", "target": "output"},
        ],
    }
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": input_value}},
    )

    assert response.status_code == 200, response.text
    completed = next(
        event
        for event in _parse_sse_events(response.text)
        if event.get("event") == "workflow_end"
    )
    assert completed["variables"]["length"] == expected_value
    assert completed["final_output"] == "3"


def test_json_helpers_and_template_conversion_are_deterministic() -> None:
    value = {"zh": "中文", "array": [1, False, None]}
    compact = '{"zh":"中文","array":[1,false,null]}'

    assert serialize_workflow_value(value) == compact
    assert deserialize_workflow_value(compact) == value
    assert serialize_workflow_value(value, pretty=True).startswith("{\n  ")
    assert render_workflow_template("value={{payload}}", {"payload": value}) == (
        f"value={compact}"
    )
    assert workflow_condition_matches(["draft", "published"], "contains", '"draft"')
    assert workflow_condition_matches({"status": "ready"}, "contains", "status")


def test_workflow_run_request_rejects_non_json_numbers() -> None:
    with pytest.raises(ValidationError):
        WorkflowRunRequest.model_validate(
            {
                "workflow": _json_round_trip_workflow(),
                "inputs": {"user_input": float("nan")},
            }
        )


def test_annotation_is_preserved_but_excluded_from_validation_order() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        _json_round_trip_workflow(include_annotation=True)
    )

    result = validate_workflow_graph(workflow)

    assert result.valid is True
    assert result.node_count == 5
    assert result.order == ["input", "serialize", "deserialize", "output"]
    assert workflow.nodes[-1].data["content"].startswith("This note")


def test_annotation_edges_and_missing_json_variables_are_rejected() -> None:
    payload = _json_round_trip_workflow(include_annotation=True)
    payload["edges"].append(
        {"id": "note-edge", "source": "note", "target": "output"}
    )
    payload["nodes"][1]["data"]["inputVariable"] = "missing_value"

    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(payload))
    issue_codes = {issue.code for issue in result.issues}

    assert result.valid is False
    assert "annotation_edge_forbidden" in issue_codes
    assert "missing_json_serialize_input_variable_reference" in issue_codes


def test_execution_store_preserves_typed_inputs_and_continuation_on_reload(
    tmp_path,
) -> None:
    value = {"items": [1, True, None], "metadata": {"count": 3}}
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="typed-task",
        run_id="typed-run",
        run_type="workflow",
        workflow=_json_round_trip_workflow(include_annotation=True),
        inputs={"user_input": value},
    )
    store.suspend(
        "typed-task",
        approval_id="approval-1",
        continuation={
            "variables": {"user_input": value, "flag": False},
            "queue": ["deserialize"],
            "executed": ["input", "serialize"],
        },
    )

    recovered = WorkflowExecutionStore(tmp_path).require("typed-task")
    assert recovered.inputs["user_input"] == value
    assert recovered.continuation["variables"]["user_input"] == value
    assert recovered.continuation["variables"]["flag"] is False
    assert recovered.workflow["nodes"][-1]["data"]["kind"] == "annotation"
