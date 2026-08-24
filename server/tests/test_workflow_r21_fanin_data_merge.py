from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

import server.main as main_module
from server.main import app
from server.workflow_native.r21_nodes import (
    WorkflowR21Error,
    WorkflowSchedulerV2,
    execute_data_merge,
    workflow_scheduler_graph_checksum,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def clear_request_windows() -> None:
    main_module.request_windows.clear()
    yield
    main_module.request_windows.clear()


def _sse_events(response: httpx.Response) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _node(node_id: str, kind: str = "variable_assign") -> SimpleNamespace:
    return SimpleNamespace(id=node_id, type=kind, data={"kind": kind})


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    source_handle: str = "",
    target_handle: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=edge_id,
        source=source,
        target=target,
        sourceHandle=source_handle or None,
        targetHandle=target_handle or None,
    )


def test_scheduler_v2_waits_for_all_fanin_edges_before_scheduling_once() -> None:
    nodes = [_node("left"), _node("right"), _node("merge", "data_merge")]
    edges = [
        _edge("left-merge", "left", "merge", target_handle="left"),
        _edge("right-merge", "right", "merge", target_handle="right"),
    ]
    scheduler = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=edges,
        order_index={"left": 0, "right": 1, "merge": 2},
    )
    queued = {"left", "right"}
    executed = {"left"}

    assert scheduler.resolve_node(
        "left",
        arrived_edge_ids={"left-merge"},
        queued=queued,
        executed=executed,
    ) == []

    executed.add("right")
    scheduled = scheduler.resolve_node(
        "right",
        arrived_edge_ids={"right-merge"},
        queued=queued,
        executed=executed,
    )
    assert scheduled == ["merge"]
    queued.add("merge")
    assert scheduler.resolve_node(
        "right",
        arrived_edge_ids={"right-merge"},
        queued=queued,
        executed=executed,
    ) == []


def test_scheduler_v2_does_not_collapse_duplicate_target_handles() -> None:
    nodes = [_node("left"), _node("right"), _node("merge", "data_merge")]
    edges = [
        _edge("left-one", "left", "merge", target_handle="left"),
        _edge("left-two", "right", "merge", target_handle="left"),
    ]
    scheduler = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=edges,
        order_index={"left": 0, "right": 1, "merge": 2},
    )

    with pytest.raises(WorkflowR21Error, match="DATA_MERGE_INPUT_HANDLES_INVALID"):
        scheduler.incoming_outcomes_by_handle("merge")


def test_scheduler_v2_propagates_unselected_branch_without_running_side_effects() -> None:
    nodes = [
        _node("branch", "condition"),
        _node("selected"),
        _node("unselected", "http_request"),
        _node("join"),
    ]
    edges = [
        _edge("branch-selected", "branch", "selected", source_handle="true"),
        _edge("branch-unselected", "branch", "unselected", source_handle="false"),
        _edge("selected-join", "selected", "join"),
        _edge("unselected-join", "unselected", "join"),
    ]
    scheduler = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=edges,
        order_index={node.id: index for index, node in enumerate(nodes)},
    )
    queued = {"branch"}
    executed = {"branch"}

    assert scheduler.resolve_node(
        "branch",
        arrived_edge_ids={"branch-selected"},
        queued=queued,
        executed=executed,
    ) == ["selected"]
    assert scheduler.skipped_nodes == {"unselected"}
    assert scheduler.edge_outcomes["unselected-join"] == "skipped"

    queued.add("selected")
    executed.add("selected")
    assert scheduler.resolve_node(
        "selected",
        arrived_edge_ids={"selected-join"},
        queued=queued,
        executed=executed,
    ) == ["join"]


def test_scheduler_v2_snapshot_restores_and_rejects_tampering() -> None:
    nodes = [_node("start", "input"), _node("wait", "suspend_wait")]
    edges = [_edge("start-wait", "start", "wait")]
    scheduler = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=edges,
        order_index={"start": 0, "wait": 1},
    )
    scheduler.resolve_node(
        "start",
        arrived_edge_ids={"start-wait"},
        queued={"start"},
        executed={"start"},
    )
    snapshot = scheduler.snapshot()
    restored = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=edges,
        order_index={"start": 0, "wait": 1},
        restored=snapshot,
    )
    restored.validate_resume_state(
        queue=["wait"],
        queued={"start", "wait"},
        executed={"start"},
    )

    snapshot["graph_checksum"] = "0" * 64
    with pytest.raises(WorkflowR21Error, match="SCHEDULER_GRAPH_CHECKSUM_MISMATCH"):
        WorkflowSchedulerV2(
            nodes=nodes,
            control_edges=edges,
            order_index={"start": 0, "wait": 1},
            restored=snapshot,
        )


def test_scheduler_v2_rejects_missing_resolved_edge_on_resume() -> None:
    nodes = [_node("start", "input"), _node("wait", "suspend_wait")]
    edge = _edge("start-wait", "start", "wait")
    scheduler = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=[edge],
        order_index={"start": 0, "wait": 1},
    )
    snapshot = scheduler.snapshot()
    restored = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=[edge],
        order_index={"start": 0, "wait": 1},
        restored=snapshot,
    )

    with pytest.raises(WorkflowR21Error, match="SCHEDULER_CONTINUATION_STATE_INVALID"):
        restored.validate_resume_state(
            queue=["wait"],
            queued={"start", "wait"},
            executed={"start"},
        )


@pytest.mark.parametrize("version", [True, 2.0, "2", None])
def test_scheduler_v2_rejects_non_integer_version_types(version: object) -> None:
    nodes = [_node("start", "input")]
    scheduler = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=[],
        order_index={"start": 0},
    )
    snapshot = scheduler.snapshot()
    snapshot["version"] = version

    with pytest.raises(WorkflowR21Error, match="SCHEDULER_VERSION_INVALID"):
        WorkflowSchedulerV2(
            nodes=nodes,
            control_edges=[],
            order_index={"start": 0},
            restored=snapshot,
        )


def test_scheduler_rejects_duplicate_edges_and_unexplained_pending_state() -> None:
    nodes = [_node("one"), _node("two")]
    duplicate_edges = [
        _edge("same", "one", "two"),
        _edge("same", "one", "two"),
    ]
    with pytest.raises(WorkflowR21Error, match="SCHEDULER_DUPLICATE_EDGE_ID"):
        WorkflowSchedulerV2(
            nodes=nodes,
            control_edges=duplicate_edges,
            order_index={"one": 0, "two": 1},
        )

    scheduler = WorkflowSchedulerV2(
        nodes=nodes,
        control_edges=[_edge("pending", "one", "two")],
        order_index={"one": 0, "two": 1},
    )
    with pytest.raises(WorkflowR21Error, match="SCHEDULER_PENDING_EDGES_REMAIN"):
        scheduler.assert_drained()


def test_scheduler_checksum_includes_handles_but_not_node_configuration() -> None:
    nodes = [_node("one"), _node("two")]
    first = [_edge("edge", "one", "two", target_handle="left")]
    second = [_edge("edge", "one", "two", target_handle="right")]
    baseline = workflow_scheduler_graph_checksum(nodes, first)
    nodes[0].data["literalValue"] = "runtime-independent"

    assert workflow_scheduler_graph_checksum(nodes, first) == baseline
    assert workflow_scheduler_graph_checksum(nodes, second) != baseline


def _merge_data(mode: str = "append", **patch: object) -> dict[str, object]:
    return {
        "kind": "data_merge",
        "contractVersion": 1,
        "mergeMode": mode,
        "leftVariable": "left_rows",
        "rightVariable": "right_rows",
        "outputVariable": "merged_rows",
        "keyFields": [] if mode == "append" else ["tenant", "id"],
        **patch,
    }


def test_data_merge_append_is_detached_ordered_and_bounded() -> None:
    left = [{"id": 1, "nested": ["left"]}]
    right = [{"id": 2}]
    output_name, result = execute_data_merge(
        _merge_data(),
        {"left_rows": left, "right_rows": right},
        incoming_outcomes={"left": "arrived", "right": "arrived"},
    )

    assert output_name == "merged_rows"
    assert result == [*left, *right]
    assert result is not left
    assert result[0] is not left[0]
    assert result[0]["nested"] is not left[0]["nested"]


def test_data_merge_keyed_join_is_typed_inner_join_in_left_order() -> None:
    left = [
        {"tenant": "a", "id": 1, "left": "first"},
        {"tenant": "a", "id": True, "left": "boolean"},
        {"tenant": "a", "id": None, "left": "null"},
        {"tenant": "a", "id": 2, "left": "second"},
    ]
    right = [
        {"tenant": "a", "id": 2, "right": "second"},
        {"tenant": "a", "id": 1, "right": "first"},
        {"tenant": "a", "id": None, "right": "null"},
        {"tenant": "a", "id": "1", "right": "string"},
    ]

    _, result = execute_data_merge(
        _merge_data("keyed_join"),
        {"left_rows": left, "right_rows": right},
    )

    assert [item["key"]["id"] for item in result] == [1, None, 2]
    assert all("left" in item and "right" in item for item in result)


def test_data_merge_accepts_non_identifier_top_level_key_names() -> None:
    _, result = execute_data_merge(
        _merge_data("keyed_join", keyFields=["tenant-id"]),
        {
            "left_rows": [{"tenant-id": "a", "value": "left"}],
            "right_rows": [{"tenant-id": "a", "value": "right"}],
        },
    )

    assert result[0]["key"] == {"tenant-id": "a"}


def test_data_merge_enforces_input_result_and_serialized_output_limits() -> None:
    with pytest.raises(WorkflowR21Error, match="DATA_MERGE_INPUT_LIMIT_EXCEEDED"):
        execute_data_merge(
            _merge_data(),
            {"left_rows": [None] * 10_001, "right_rows": []},
        )
    with pytest.raises(WorkflowR21Error, match="DATA_MERGE_RESULT_LIMIT_EXCEEDED"):
        execute_data_merge(
            _merge_data(),
            {"left_rows": [None] * 5_001, "right_rows": [None] * 5_001},
        )
    with pytest.raises(WorkflowR21Error, match="DATA_MERGE_OUTPUT_TOO_LARGE"):
        execute_data_merge(
            _merge_data(),
            {"left_rows": ["bounded"], "right_rows": []},
            max_output_bytes=2,
        )


@pytest.mark.parametrize(
    ("variables", "patch", "expected_code"),
    [
        ({"left_rows": [], "right_rows": []}, {"leftVariable": "right_rows"}, "DATA_MERGE_INPUTS_MUST_DIFFER"),
        ({"left_rows": [], "right_rows": []}, {"outputVariable": "left_rows"}, "DATA_MERGE_OUTPUT_CONFLICT"),
        ({"left_rows": [{"id": 1}], "right_rows": [{"id": 1}]}, {}, "DATA_MERGE_KEY_FIELD_MISSING"),
        (
            {
                "left_rows": [{"tenant": "a", "id": 1}, {"tenant": "a", "id": 1.0}],
                "right_rows": [],
            },
            {},
            "DATA_MERGE_KEY_NOT_UNIQUE",
        ),
    ],
)
def test_data_merge_rejects_ambiguous_or_invalid_contracts(
    variables: dict[str, object],
    patch: dict[str, object],
    expected_code: str,
) -> None:
    with pytest.raises(WorkflowR21Error) as caught:
        execute_data_merge(_merge_data("keyed_join", **patch), variables)

    assert caught.value.code == expected_code


def test_data_merge_fails_when_only_one_control_input_arrives() -> None:
    with pytest.raises(WorkflowR21Error) as caught:
        execute_data_merge(
            _merge_data(),
            {"left_rows": [], "right_rows": []},
            incoming_outcomes={"left": "arrived", "right": "skipped"},
        )

    assert caught.value.code == "DATA_MERGE_INPUT_NOT_REACHED"
    assert caught.value.safe_message == (
        "数据合流需要左、右两条输入路径都到达；当前有一侧未到达。"
    )


def test_data_merge_duplicate_key_error_identifies_rows_without_leaking_values() -> None:
    sentinel = "DO_NOT_LEAK_COMPOSITE_KEY"

    with pytest.raises(WorkflowR21Error) as caught:
        execute_data_merge(
            _merge_data("keyed_join"),
            {
                "left_rows": [
                    {"tenant": sentinel, "id": 7},
                    {"tenant": sentinel, "id": 7},
                ],
                "right_rows": [],
            },
        )

    assert caught.value.code == "DATA_MERGE_KEY_NOT_UNIQUE"
    assert caught.value.safe_message == "左侧数组第 2 条记录与第 1 条记录的复合键重复。"
    assert sentinel not in caught.value.safe_message


def _literal_assign(node_id: str, output_variable: str, value: object) -> dict[str, object]:
    return {
        "id": node_id,
        "type": "variable_assign",
        "data": {
            "kind": "variable_assign",
            "contractVersion": 2,
            "outputVariable": output_variable,
            "valueSource": "literal",
            "literalValue": value,
        },
    }


def _runtime_merge_workflow() -> dict[str, object]:
    return {
        "id": "reliable-fanin-data-merge",
        "title": "Reliable fan-in data merge",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            _literal_assign("short", "left_rows", [{"id": 1, "side": "left"}]),
            _literal_assign("long-one", "intermediate", "continue"),
            _literal_assign("long-two", "right_rows", [{"id": 2, "side": "right"}]),
            {
                "id": "merge",
                "type": "data_merge",
                "data": _merge_data(),
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "merged_rows"},
            },
        ],
        "edges": [
            {"id": "e-input-short", "source": "input", "target": "short"},
            {"id": "e-input-long", "source": "input", "target": "long-one"},
            {"id": "e-long", "source": "long-one", "target": "long-two"},
            {
                "id": "e-left",
                "source": "short",
                "target": "merge",
                "targetHandle": "left",
            },
            {
                "id": "e-right",
                "source": "long-two",
                "target": "merge",
                "targetHandle": "right",
            },
            {"id": "e-output", "source": "merge", "target": "output"},
        ],
    }


@pytest.mark.asyncio
async def test_runtime_waits_for_long_branch_before_data_merge(
    client: httpx.AsyncClient,
) -> None:
    workflow = _runtime_merge_workflow()
    validation = validate_workflow_graph(
        NativeWorkflowDefinition.model_validate(workflow)
    )
    assert validation.valid is True, validation.issues

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "start"}},
    )

    assert response.status_code == 200, response.text
    events = _sse_events(response)
    starts = [
        str(event["node_id"])
        for event in events
        if event.get("event") == "node_start"
    ]
    assert starts.index("merge") > starts.index("long-two")
    assert starts.count("merge") == 1
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert json.loads(str(completed["final_output"])) == [
        {"id": 1, "side": "left"},
        {"id": 2, "side": "right"},
    ]


@pytest.mark.asyncio
async def test_runtime_rejects_duplicate_edge_ids_before_streaming(
    client: httpx.AsyncClient,
) -> None:
    workflow = _runtime_merge_workflow()
    workflow["edges"][1]["id"] = "e-input-short"  # type: ignore[index]

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "start"}},
    )

    assert response.status_code == 400
    assert any(
        issue["code"] == "duplicate_edge_id"
        for issue in response.json()["issues"]
    )


@pytest.mark.asyncio
async def test_runtime_rejects_data_merge_variable_from_the_wrong_path(
    client: httpx.AsyncClient,
) -> None:
    workflow = _runtime_merge_workflow()
    merge = next(
        node for node in workflow["nodes"] if node["id"] == "merge"  # type: ignore[index]
    )
    merge["data"]["leftVariable"] = "right_rows"  # type: ignore[index]
    merge["data"]["rightVariable"] = "left_rows"  # type: ignore[index]

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "start"}},
    )

    assert response.status_code == 400
    assert {
        issue["code"] for issue in response.json()["issues"]
    } == {"data_merge_variable_not_on_input_path"}


@pytest.mark.asyncio
async def test_runtime_fails_data_merge_when_condition_skips_one_input(
    client: httpx.AsyncClient,
) -> None:
    workflow = {
        "id": "data-merge-skipped-input",
        "title": "Data merge skipped input",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "request"},
            },
            {
                "id": "condition",
                "type": "condition",
                "data": {
                    "kind": "condition",
                    "contractVersion": 2,
                    "inputVariable": "request",
                    "operator": "equals",
                    "valueType": "text",
                    "value": "left",
                },
            },
            _literal_assign("left", "left_rows", [{"id": 1}]),
            _literal_assign("right", "right_rows", [{"id": 1}]),
            {"id": "merge", "type": "data_merge", "data": _merge_data()},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "merged_rows"},
            },
        ],
        "edges": [
            {"id": "e-input", "source": "input", "target": "condition"},
            {
                "id": "e-left-branch",
                "source": "condition",
                "sourceHandle": "true",
                "target": "left",
            },
            {
                "id": "e-right-branch",
                "source": "condition",
                "sourceHandle": "false",
                "target": "right",
            },
            {
                "id": "e-left",
                "source": "left",
                "target": "merge",
                "targetHandle": "left",
            },
            {
                "id": "e-right",
                "source": "right",
                "target": "merge",
                "targetHandle": "right",
            },
            {"id": "e-output", "source": "merge", "target": "output"},
        ],
    }
    validation = validate_workflow_graph(
        NativeWorkflowDefinition.model_validate(workflow)
    )
    assert validation.valid is True, validation.issues

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"request": "left"}},
    )

    assert response.status_code == 200, response.text
    events = _sse_events(response)
    assert not any(
        event.get("event") == "node_start" and event.get("node_id") == "right"
        for event in events
    )
    skipped_events = [
        event for event in events if event.get("event") == "node_skipped"
    ]
    assert skipped_events == [
        {
            "event": "node_skipped",
            "node_id": "right",
            "node_title": "right",
            "node_type": "variable_assign",
            "status": "skipped",
            "message": "未命中当前分支，已跳过。",
        }
    ]
    assert not ({"output", "variables"} & set(skipped_events[0]))
    task_id = str(next(
        event["task_id"] for event in events if event.get("event") == "workflow_meta"
    ))
    persisted_skips = [
        event
        for event in main_module.workflow_execution_store.require(task_id).events
        if event.get("event") == "node_skipped"
    ]
    assert [
        {key: value for key, value in event.items() if key != "sequence"}
        for event in persisted_skips
    ] == skipped_events
    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "DATA_MERGE_INPUT_NOT_REACHED"
    assert error["message"] == "数据合流需要左、右两条输入路径都到达；当前有一侧未到达。"
    assert not any(event.get("event") == "workflow_end" for event in events)
