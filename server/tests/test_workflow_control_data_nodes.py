from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

import server.api.workflow_deployments as deployment_api
import server.main as main_module
from server.main import app
from server.workflow_deployments import WorkflowDeploymentStore
from server.workflow_native.control_data import (
    MAX_COLLECTION_ITEMS,
    WorkflowControlDataError,
    aggregate_rows,
    execute_list_operation,
    select_multi_route,
    typed_deep_equal,
    validate_terminate_error_config,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xpert_runtime.execution_store import WorkflowExecutionStore


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


def parse_sse_events(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def multi_route_workflow(*, terminate_first: bool = False) -> dict:
    first_target = "stop" if terminate_first else "first"
    nodes = [
        {
            "id": "input",
            "type": "input",
            "data": {"kind": "input", "variableName": "user_input"},
        },
        {
            "id": "route",
            "type": "multi_route",
            "data": {
                "kind": "multi_route",
                "inputVariable": "user_input",
                "routes": [
                    {
                        "id": "route_1",
                        "label": "first",
                        "operator": "contains",
                        "valueType": "text",
                        "value": "match",
                    },
                    {
                        "id": "route_2",
                        "label": "second",
                        "operator": "equals",
                        "valueType": "text",
                        "value": "match exactly",
                    },
                ],
            },
        },
        {
            "id": "first",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "variableName": "selected",
                "template": "first",
            },
        },
        {
            "id": "second",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "variableName": "selected",
                "template": "second",
            },
        },
        {
            "id": "fallback",
            "type": "variable_assign",
            "data": {
                "kind": "variable_assign",
                "variableName": "selected",
                "template": "default",
            },
        },
        {
            "id": "stop",
            "type": "terminate_error",
            "data": {
                "kind": "terminate_error",
                "errorCode": "MATCH_BLOCKED",
                "message": "The matching branch is blocked.",
            },
        },
        {
            "id": "output",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "selected"},
        },
    ]
    edges = [
        {"id": "e-input", "source": "input", "target": "route"},
        {
            "id": "e-first",
            "source": "route",
            "sourceHandle": "route_1",
            "target": first_target,
        },
        {
            "id": "e-second",
            "source": "route",
            "sourceHandle": "route_2",
            "target": "second",
        },
        {
            "id": "e-default",
            "source": "route",
            "sourceHandle": "default",
            "target": "fallback",
        },
        {"id": "e-first-output", "source": "first", "target": "output"},
        {"id": "e-second-output", "source": "second", "target": "output"},
        {"id": "e-default-output", "source": "fallback", "target": "output"},
    ]
    if terminate_first:
        edges = [edge for edge in edges if edge["id"] != "e-first-output"]
    return {
        "id": "multi-route-runtime",
        "title": "Multi route runtime",
        "nodes": nodes,
        "edges": edges,
    }


def test_typed_comparison_and_multi_route_use_first_match() -> None:
    routes = multi_route_workflow()["nodes"][1]["data"]["routes"]

    assert select_multi_route("match exactly", routes) == "route_1"
    assert select_multi_route("no result", routes) == "default"
    assert typed_deep_equal(1, 1.0)
    assert not typed_deep_equal(1, True)
    assert typed_deep_equal({"a": [1, None]}, {"a": [1.0, None]})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_value", "expected_node", "expected_output"),
    [
        ("match exactly", "first", "first"),
        ("no result", "fallback", "default"),
    ],
)
async def test_multi_route_runtime_executes_only_selected_branch(
    client: httpx.AsyncClient,
    input_value: str,
    expected_node: str,
    expected_output: str,
) -> None:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": multi_route_workflow(),
            "inputs": {"user_input": input_value},
        },
    )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    started = {
        event["node_id"]
        for event in events
        if event.get("event") == "node_start"
    }
    assert expected_node in started
    assert {"first", "second", "fallback"}.intersection(started) == {expected_node}
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["final_output"] == expected_output


@pytest.mark.asyncio
async def test_multi_route_runtime_uses_the_same_normalized_handle_as_validation(
    client: httpx.AsyncClient,
) -> None:
    payload = multi_route_workflow()
    first_edge = next(edge for edge in payload["edges"] if edge["id"] == "e-first")
    first_edge["sourceHandle"] = " route_1 "

    validation = validate_workflow_graph(NativeWorkflowDefinition.model_validate(payload))
    assert validation.valid is True

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": payload, "inputs": {"user_input": "match exactly"}},
    )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    started = {
        event["node_id"]
        for event in events
        if event.get("event") == "node_start"
    }
    assert "first" in started
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["final_output"] == "first"


@pytest.mark.asyncio
async def test_typed_condition_runtime_trace_names_the_selected_field(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "typed-condition-trace",
        "title": "Typed condition trace",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "payload"},
            },
            {
                "id": "condition",
                "type": "condition",
                "data": {
                    "kind": "condition",
                    "contractVersion": 2,
                    "inputVariable": "payload",
                    "field": "statusCode",
                    "operator": "equals",
                    "valueType": "number",
                    "value": 200,
                },
            },
            {
                "id": "yes",
                "type": "variable_assign",
                "data": {
                    "kind": "variable_assign",
                    "variableName": "result",
                    "template": "yes",
                },
            },
            {
                "id": "no",
                "type": "variable_assign",
                "data": {
                    "kind": "variable_assign",
                    "variableName": "result",
                    "template": "no",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "result"},
            },
        ],
        "edges": [
            {"id": "e-input", "source": "input", "target": "condition"},
            {
                "id": "e-yes",
                "source": "condition",
                "sourceHandle": "true",
                "target": "yes",
            },
            {
                "id": "e-no",
                "source": "condition",
                "sourceHandle": "false",
                "target": "no",
            },
            {"id": "e-yes-output", "source": "yes", "target": "output"},
            {"id": "e-no-output", "source": "no", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"payload": {"statusCode": 200}}},
    )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    condition = next(
        event
        for event in events
        if event.get("event") == "node_end" and event.get("node_id") == "condition"
    )
    assert condition["output"] == "payload.statusCode equals 200 -> 是"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_value", "selected_node", "expected_outcomes", "source_ref"),
    [
        ("approve", "approved", ["router:matched", "approved:success"], "approved"),
        ("decline", "rejected", ["router:unmatched", "rejected:success"], "rejected"),
    ],
)
async def test_planner_control_flow_records_semantic_success_and_output_v2_terminal(
    client: httpx.AsyncClient,
    input_value: str,
    selected_node: str,
    expected_outcomes: list[str],
    source_ref: str,
) -> None:
    workflow = {
        "id": f"planner-control-flow-{selected_node}",
        "title": "Planner control flow runtime evidence",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "payload"},
            },
            {
                "id": "router",
                "type": "condition",
                "data": {
                    "kind": "condition",
                    "contractVersion": 2,
                    "inputVariable": "payload",
                    "field": "",
                    "operator": "equals",
                    "valueType": "text",
                    "value": "approve",
                    "plannerRef": "router",
                    "plannerOutcomeMapV1": {"matched": "true", "unmatched": "false"},
                },
            },
            {
                "id": "approved",
                "type": "json_serialize",
                "data": {
                    "kind": "json_serialize",
                    "contractVersion": 2,
                    "inputVariable": "payload",
                    "outputVariable": "approved_json",
                    "format": "compact",
                    "plannerRef": "approved",
                    "plannerOutcomeMapV1": {"success": ""},
                },
            },
            {
                "id": "rejected",
                "type": "json_serialize",
                "data": {
                    "kind": "json_serialize",
                    "contractVersion": 2,
                    "inputVariable": "payload",
                    "outputVariable": "rejected_json",
                    "format": "compact",
                    "plannerRef": "rejected",
                    "plannerOutcomeMapV1": {"success": ""},
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {
                    "kind": "output",
                    "contractVersion": 2,
                    "selectionPolicy": "exactly_one_arrived",
                    "outputSources": [
                        {
                            "sourceRef": "approved",
                            "sourcePort": "json",
                            "variable": "approved_json",
                        },
                        {
                            "sourceRef": "rejected",
                            "sourcePort": "json",
                            "variable": "rejected_json",
                        },
                    ],
                },
            },
        ],
        "edges": [
            {"id": "e-input-router", "source": "input", "target": "router"},
            {
                "id": "e-router-approved",
                "source": "router",
                "sourceHandle": "true",
                "target": "approved",
            },
            {
                "id": "e-router-rejected",
                "source": "router",
                "sourceHandle": "false",
                "target": "rejected",
            },
            {"id": "e-approved-output", "source": "approved", "target": "output"},
            {"id": "e-rejected-output", "source": "rejected", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"payload": input_value}},
    )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    started = {
        str(event.get("node_id"))
        for event in events
        if event.get("event") == "node_start"
    }
    assert selected_node in started
    assert {"approved", "rejected"}.intersection(started) == {selected_node}
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["final_output"] == json.dumps(input_value)

    runtime_run_id = response.headers["X-ModelMirror-Runtime-Run-Id"]
    summary = await main_module.evaluation_control_flow_summary(runtime_run_id)
    assert summary == {
        "supported": True,
        "outcomes": expected_outcomes,
        "terminal": "success",
        "source_ref": source_ref,
        "error_code": "",
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_legacy_planner_refs_without_outcome_metadata_still_execute(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "legacy-planner-v3-json",
        "title": "Legacy Planner V3",
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "kind": "input", "variableName": "user_input",
            }},
            {"id": "serialize", "type": "json_serialize", "data": {
                "kind": "json_serialize", "contractVersion": 2,
                "inputVariable": "user_input", "outputVariable": "json_text",
                "format": "compact", "plannerRef": "serialize",
            }},
            {"id": "output", "type": "output", "data": {
                "kind": "output", "outputVariable": "json_text",
            }},
        ],
        "edges": [
            {"id": "input-serialize", "source": "input", "target": "serialize"},
            {"id": "serialize-output", "source": "serialize", "target": "output"},
        ],
    }
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "legacy"}},
    )
    events = parse_sse_events(response.text)
    assert not [event for event in events if event.get("event") == "workflow_error"]
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["final_output"] == '"legacy"'
    evidence = await main_module.evaluation_control_flow_summary(
        response.headers["X-ModelMirror-Runtime-Run-Id"]
    )
    assert evidence["supported"] is False
    assert evidence["warnings"]


@pytest.mark.asyncio
async def test_manual_output_v2_cannot_forge_planner_source_evidence(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "manual-output-v2-forged-source",
        "title": "Manual Output V2",
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "kind": "input", "variableName": "user_input",
            }},
            {"id": "serialize", "type": "json_serialize", "data": {
                "kind": "json_serialize", "contractVersion": 2,
                "inputVariable": "user_input", "outputVariable": "json_text",
                "format": "compact",
            }},
            {"id": "output", "type": "output", "data": {
                "kind": "output", "contractVersion": 2,
                "selectionPolicy": "exactly_one_arrived",
                "outputSources": [{
                    "sourceRef": "forged_source",
                    "sourcePort": "json",
                    "variable": "json_text",
                }],
            }},
        ],
        "edges": [
            {"id": "input-serialize", "source": "input", "target": "serialize"},
            {"id": "serialize-output", "source": "serialize", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "manual"}},
    )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    assert next(event for event in events if event.get("event") == "workflow_end")[
        "final_output"
    ] == '"manual"'
    evidence = await main_module.evaluation_control_flow_summary(
        response.headers["X-ModelMirror-Runtime-Run-Id"]
    )
    assert evidence["supported"] is False
    assert evidence["source_ref"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("planner_metadata", "expected_error_code", "supported"),
    [
        ({"plannerRef": "manual_stop"}, "EXPECTED_STOP", False),
        (
            {
                "plannerRef": "manual_stop",
                "plannerOutcomeMapV1": {"success": ""},
            },
            "PLANNER_OUTCOME_MAP_INVALID",
            False,
        ),
        (
            {"plannerRef": "planned_stop", "plannerOutcomeMapV1": {}},
            "EXPECTED_STOP",
            True,
        ),
    ],
)
async def test_terminate_error_path_evidence_requires_exact_planner_metadata(
    client: httpx.AsyncClient,
    planner_metadata: dict,
    expected_error_code: str,
    supported: bool,
) -> None:
    workflow = {
        "id": "terminal-planner-metadata",
        "title": "Terminal Planner Metadata",
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "kind": "input", "variableName": "user_input",
            }},
            {"id": "stop", "type": "terminate_error", "data": {
                "kind": "terminate_error", "errorCode": "EXPECTED_STOP",
                "message": "Expected safe stop.", **planner_metadata,
            }},
        ],
        "edges": [{"id": "input-stop", "source": "input", "target": "stop"}],
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "stop"}},
    )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == expected_error_code
    evidence = await main_module.evaluation_control_flow_summary(
        response.headers["X-ModelMirror-Runtime-Run-Id"]
    )
    assert evidence["supported"] is supported
    assert evidence["source_ref"] == ("planned_stop" if supported else "")


@pytest.mark.asyncio
async def test_runtime_rejects_reversed_planner_outcome_map(client: httpx.AsyncClient) -> None:
    workflow = {
        "id": "forged-planner-outcomes", "title": "Forged outcomes",
        "nodes": [
            {"id": "input", "type": "input", "data": {
                "kind": "input", "variableName": "user_input",
            }},
            {"id": "router", "type": "condition", "data": {
                "kind": "condition", "contractVersion": 2,
                "inputVariable": "user_input", "operator": "equals",
                "valueType": "text", "value": "approve", "plannerRef": "router",
                "plannerOutcomeMapV1": {"matched": "false", "unmatched": "true"},
            }},
            {"id": "output", "type": "output", "data": {
                "kind": "output", "outputVariable": "user_input",
            }},
        ],
        "edges": [
            {"id": "input-router", "source": "input", "target": "router"},
            {"id": "matched", "source": "router", "target": "output", "sourceHandle": "true"},
            {"id": "unmatched", "source": "router", "target": "output", "sourceHandle": "false"},
        ],
    }
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "approve"}},
    )
    events = parse_sse_events(response.text)
    assert any(event.get("code") == "PLANNER_OUTCOME_MAP_INVALID" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_terminate_error_fails_with_code_and_stops_other_routes(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": multi_route_workflow(terminate_first=True),
            "inputs": {"user_input": "match"},
        },
    )

    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "MATCH_BLOCKED"
    assert error["message"] == "The matching branch is blocked."
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert not {
        "first",
        "second",
        "fallback",
        "output",
    }.intersection(
        event.get("node_id")
        for event in events
        if event.get("event") == "node_start"
    )


def test_multi_route_static_validation_requires_stable_exact_edges() -> None:
    payload = multi_route_workflow()
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if edge["id"] not in {"e-second", "e-default"}
    ]
    payload["edges"].extend(
        [
            {
                "id": "duplicate-first",
                "source": "route",
                "sourceHandle": "route_1",
                "target": "second",
            },
            {
                "id": "unknown-route",
                "source": "route",
                "sourceHandle": "route_8",
                "target": "fallback",
            },
        ]
    )

    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(payload))
    codes = {issue.code for issue in result.issues}

    assert result.valid is False
    assert {
        "multi_route_duplicate_edge",
        "multi_route_missing_edge",
        "multi_route_unknown_edge_handle",
    }.issubset(codes)


def test_terminate_error_rejects_edges_templates_and_credentials() -> None:
    payload = multi_route_workflow(terminate_first=True)
    payload["edges"].append(
        {"id": "stop-output", "source": "stop", "target": "output"}
    )
    payload["nodes"][5]["data"]["message"] = "failed: {{api_key}}"
    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(payload))

    assert result.valid is False
    assert {issue.code for issue in result.issues}.issuperset(
        {"terminate_error_not_terminal", "terminate_error_template_forbidden"}
    )
    with pytest.raises(WorkflowControlDataError, match="SENSITIVE"):
        validate_terminate_error_config(
            "WORKFLOW_STOPPED",
            "api_key=" + "sk-" + "abcdefghijklmnop",
        )


@pytest.mark.parametrize(
    "secret",
    [
        "AKIA" + "1234567890ABCDEF",
        "AIza" + "A" * 35,
        "xoxb-" + "123456789012-abcdefghijklmnopqrstuv",
        "sk" + "_live_1234567890abcdefghijkl",
        "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123",
    ],
)
def test_terminate_error_rejects_common_bare_credential_shapes(secret: str) -> None:
    with pytest.raises(WorkflowControlDataError, match="SENSITIVE"):
        validate_terminate_error_config(
            "WORKFLOW_STOPPED",
            f"Credential {secret} leaked",
        )


def test_list_filter_sort_and_deduplicate_are_typed_stable_and_detached() -> None:
    rows = [
        {"id": 1, "score": 2, "group": "b"},
        {"id": 2, "score": None, "group": "a"},
        {"id": 3, "score": 2, "group": "a"},
        {"id": 4, "score": 1, "group": "a"},
    ]
    filtered = execute_list_operation(
        rows,
        operator="filter",
        filter_mode="all",
        filter_rules=[
            {
                "field": "group",
                "operator": "equals",
                "valueType": "text",
                "value": "a",
            }
        ],
    )
    sorted_rows = execute_list_operation(
        filtered,
        operator="sort",
        sort_keys=[
            {"field": "score", "direction": "asc", "nulls": "last"}
        ],
    )

    assert [row["id"] for row in sorted_rows] == [4, 3, 2]
    assert rows[0]["id"] == 1
    assert execute_list_operation(
        [1, 1.0, True, True, {"a": 1}, {"a": 1.0}],
        operator="deduplicate",
        deduplicate_fields=[],
    ) == [1, True, {"a": 1}]
    assert execute_list_operation(
        [{"id": 1, "v": "first"}, {"id": 1, "v": "later"}],
        operator="deduplicate",
        deduplicate_fields=["id"],
    ) == [{"id": 1, "v": "first"}]


def test_list_new_operations_reject_legacy_text_mixed_sort_and_limits() -> None:
    with pytest.raises(WorkflowControlDataError, match="TYPED_ARRAY_REQUIRED"):
        execute_list_operation(
            "a,b",
            operator="filter",
            filter_mode="all",
            filter_rules=[
                {"operator": "is_null", "valueType": "null", "value": None}
            ],
        )
    with pytest.raises(WorkflowControlDataError, match="TYPE_MISMATCH"):
        execute_list_operation(
            [1, "2"],
            operator="sort",
            sort_keys=[{"field": "", "direction": "asc", "nulls": "last"}],
        )
    with pytest.raises(WorkflowControlDataError, match="LIMIT_EXCEEDED"):
        execute_list_operation(
            list(range(MAX_COLLECTION_ITEMS + 1)),
            operator="deduplicate",
            deduplicate_fields=[],
        )
    with pytest.raises(WorkflowControlDataError, match="LIMIT_EXCEEDED"):
        execute_list_operation(
            list(range(MAX_COLLECTION_ITEMS + 1)),
            operator="length",
        )
    assert execute_list_operation("a,b,c", operator="length") == "3"
    assert execute_list_operation(["a", "b"], operator="length") == 2


def test_list_take_skip_and_slice_are_bounded_non_mutating_array_operations() -> None:
    source = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]

    assert execute_list_operation(source, operator="take", count=2) == source[:2]
    assert execute_list_operation(source, operator="skip", count=2) == source[2:]
    assert execute_list_operation(
        source,
        operator="slice",
        start_index=1,
        end_index=3,
    ) == source[1:3]
    assert source == [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]

    with pytest.raises(WorkflowControlDataError, match="INVALID_LIST_COUNT"):
        execute_list_operation(source, operator="take", count=-1)
    with pytest.raises(WorkflowControlDataError, match="INVALID_LIST_SLICE_END"):
        execute_list_operation(source, operator="slice", start_index=3, end_index=2)
    with pytest.raises(WorkflowControlDataError, match="TYPED_ARRAY_REQUIRED"):
        execute_list_operation("a,b,c", operator="skip", count=1)


def test_data_aggregate_preserves_group_order_and_measure_contracts() -> None:
    rows = [
        {"team": "blue", "score": 2},
        {"team": "red", "score": None},
        {"team": "blue", "score": 4},
        {"team": "red"},
    ]
    result = aggregate_rows(
        rows,
        group_by_fields=["team"],
        measures=[
            {"outputField": "rows", "operation": "count"},
            {"outputField": "total", "operation": "sum", "sourceField": "score"},
            {"outputField": "average", "operation": "avg", "sourceField": "score"},
            {"outputField": "lowest", "operation": "min", "sourceField": "score"},
            {"outputField": "highest", "operation": "max", "sourceField": "score"},
        ],
    )

    assert result == [
        {
            "team": "blue",
            "rows": 2,
            "total": 6,
            "average": 3,
            "lowest": 2,
            "highest": 4,
        },
        {
            "team": "red",
            "rows": 2,
            "total": 0,
            "average": None,
            "lowest": None,
            "highest": None,
        },
    ]
    assert aggregate_rows(
        [],
        group_by_fields=[],
        measures=[{"outputField": "rows", "operation": "count"}],
    ) == [{"rows": 0}]


def test_data_aggregate_rejects_deep_groups_bad_numbers_and_output_conflicts() -> None:
    with pytest.raises(WorkflowControlDataError, match="NOT_SCALAR"):
        aggregate_rows(
            [{"group": {"nested": True}}],
            group_by_fields=["group"],
            measures=[{"outputField": "rows", "operation": "count"}],
        )
    with pytest.raises(WorkflowControlDataError, match="NUMERIC_VALUE_REQUIRED"):
        aggregate_rows(
            [{"score": "secret text"}],
            group_by_fields=[],
            measures=[
                {"outputField": "total", "operation": "sum", "sourceField": "score"}
            ],
        )
    with pytest.raises(WorkflowControlDataError, match="FIELD_CONFLICT"):
        aggregate_rows(
            [],
            group_by_fields=["team"],
            measures=[{"outputField": "team", "operation": "count"}],
        )


@pytest.mark.asyncio
async def test_deployed_terminate_error_dispatches_one_sanitized_failure(
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
    monkeypatch.setenv("WORKFLOW_FAILURE_TRIGGERS_ENABLED", "true")

    source = deployment_store.create_project(
        {
            "id": "draft",
            "title": "terminating schedule",
            "nodes": [
                {
                    "id": "entry",
                    "type": "scheduled_start",
                    "data": {
                        "kind": "scheduled_start",
                        "scheduleType": "interval",
                        "intervalSeconds": 30,
                        "timezone": "UTC",
                        "eventVariable": "schedule_event",
                    },
                },
                {
                    "id": "stop",
                    "type": "terminate_error",
                    "data": {
                        "kind": "terminate_error",
                        "errorCode": "EXPECTED_STOP",
                        "message": "Expected safe failure.",
                    },
                },
            ],
            "edges": [{"id": "source-edge", "source": "entry", "target": "stop"}],
        }
    )
    source_release = deployment_store.publish(source.project_id)
    deployment_store.activate(
        source.project_id,
        source_release.version,
        webhooks_enabled=False,
        now=100,
    )
    handler = deployment_store.create_project(
        {
            "id": "draft",
            "title": "failure handler",
            "nodes": [
                {
                    "id": "failure-entry",
                    "type": "failure_event_entry",
                    "data": {
                        "kind": "failure_event_entry",
                        "sourceProjectIds": [source.project_id],
                        "eventVariable": "failure_event",
                    },
                },
                {
                    "id": "failure-output",
                    "type": "output",
                    "data": {
                        "kind": "output",
                        "outputVariable": "failure_event",
                    },
                },
            ],
            "edges": [
                {
                    "id": "handler-edge",
                    "source": "failure-entry",
                    "target": "failure-output",
                }
            ],
        }
    )
    handler_release = deployment_store.publish(handler.project_id)
    deployment_store.activate(
        handler.project_id,
        handler_release.version,
        webhooks_enabled=False,
        failure_triggers_enabled=True,
        now=101,
    )

    pending = deployment_store.materialize_due_schedules(now=130)[0]
    failed = await deployment_api._execute_trigger(
        pending,
        {"type": "schedule_event"},
    )

    assert failed.status == "failed"
    assert failed.error_summary == "EXPECTED_STOP: Expected safe failure."
    dispatched = deployment_store.list_executions(handler.project_id)
    assert len(dispatched) == 1
    assert dispatched[0].trigger_summary["source_execution_id"] == failed.execution_id
    assert dispatched[0].trigger_summary["failed_node_id"] == "stop"
    assert dispatched[0].trigger_summary["suppress_failure_dispatch"] is True

    deployment_store.fail_execution(
        failed.execution_id,
        error="duplicate callback must not dispatch again",
    )
    assert len(deployment_store.list_executions(handler.project_id)) == 1
