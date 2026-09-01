from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError

import server.main as main_module
from server.main import (
    WorkflowRunRequest,
    app,
    initialize_declared_workflow_variables,
    render_workflow_template,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.control_data import aggregate_rows
from server.workflow_native.node_contracts import WorkflowValueSchema
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


def _classifier_v2_model_workflow(mode: str) -> dict:
    return {
        "id": f"classifier-v2-{mode}",
        "title": f"classifier-v2-{mode}",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
            {
                "id": "classifier",
                "type": "question_classifier",
                "data": {
                    "kind": "question_classifier",
                    "contractVersion": 2,
                    "inputVariable": "user_input",
                    "outputVariable": "category",
                    "classificationMode": mode,
                    "categoriesV2": [
                        {"id": "category_1", "label": "退款", "description": "退款请求", "keywords": ["退款"], "matchMode": "contains_any"},
                        {"id": "category_2", "label": "物流", "description": "物流查询", "keywords": ["物流"], "matchMode": "contains_any"},
                    ],
                    "caseSensitive": False,
                    "modelId": "test/model",
                    "defaultLabel": "其他",
                },
            },
            *[
                {"id": f"output-{handle}", "type": "output", "data": {"kind": "output", "outputVariable": "category"}}
                for handle in ("category_1", "category_2", "default")
            ],
        ],
        "edges": [
            {"id": "e0", "source": "input", "target": "classifier"},
            *[
                {"id": f"e-{handle}", "source": "classifier", "sourceHandle": handle, "target": f"output-{handle}"}
                for handle in ("category_1", "category_2", "default")
            ],
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


@pytest.mark.asyncio
async def test_json_deserialize_v2_validates_declared_schema_and_fails_closed(
    client: httpx.AsyncClient,
) -> None:
    workflow = _json_round_trip_workflow()
    workflow["nodes"] = [
        workflow["nodes"][0],
        workflow["nodes"][2],
        workflow["nodes"][3],
    ]
    workflow["nodes"][1]["data"].update(
        {
            "contractVersion": 2,
            "inputVariable": "user_input",
            "expectedSchema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        }
    )
    workflow["edges"] = [
        {"id": "e1", "source": "input", "target": "deserialize"},
        {"id": "e2", "source": "deserialize", "target": "output"},
    ]

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "[]"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert any(event.get("event") == "error" for event in events)
    assert not any(event.get("event") == "workflow_end" for event in events)


def test_json_deserialize_v2_static_validation_requires_expected_schema() -> None:
    workflow = _json_round_trip_workflow()
    workflow["nodes"][2]["data"]["contractVersion"] = 2

    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(workflow))

    assert result.valid is False
    assert "invalid_json_deserialize_expected_schema" in {
        issue.code for issue in result.issues
    }


def test_json_v2_schema_and_output_limits_are_fail_closed() -> None:
    schema = WorkflowValueSchema(
        type="object",
        properties={"count": WorkflowValueSchema(type="integer")},
        required=("count",),
    )
    schema.assert_value({"count": 2})
    with pytest.raises(ValueError, match="must have type integer"):
        schema.assert_value({"count": "2"})
    WorkflowValueSchema(
        type="string",
        nullable=True,
        any_of=(WorkflowValueSchema(type="string"),),
    ).assert_value(None)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkflowValueSchema.model_validate(
            {"type": "string", "forged_runtime_policy": "ignore"}
        )
    with pytest.raises(ValueError, match="JSON_SERIALIZE_OUTPUT_TOO_LARGE"):
        serialize_workflow_value({"value": "0123456789"}, max_bytes=8)
    with pytest.raises(ValueError, match="JSON_DESERIALIZE_INPUT_TOO_LARGE"):
        deserialize_workflow_value('"0123456789"', max_bytes=8)
    with pytest.raises(ValueError, match="AGGREGATE_OUTPUT_LIMIT_EXCEEDED"):
        aggregate_rows(
            [{"group": "a"}],
            group_by_fields=["group"],
            measures=[{"outputField": "count", "operation": "count"}],
            max_output_bytes=8,
        )


def test_workflow_run_request_rejects_non_json_numbers() -> None:
    with pytest.raises(ValidationError):
        WorkflowRunRequest.model_validate(
            {
                "workflow": _json_round_trip_workflow(),
                "inputs": {"user_input": float("nan")},
            }
        )


@pytest.mark.asyncio
async def test_parameter_extractor_v2_repairs_once_and_writes_typed_value(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter(["not-json", '{"order_id":"A-19","amount":42.5}'])
    calls = 0

    async def fake_collect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return next(replies)

    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    workflow = {
        "id": "extractor-v2-runtime",
        "title": "extractor-v2-runtime",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "extractor",
                "type": "parameter_extractor",
                "data": {
                    "kind": "parameter_extractor",
                    "contractVersion": 2,
                    "inputVariable": "user_input",
                    "modelId": "test/model",
                    "outputVariable": "parameters",
                    "schemaMode": "fields",
                    "outputShape": "object",
                    "fields": [
                        {
                            "id": "field_1",
                            "name": "order_id",
                            "description": "Order ID",
                            "valueType": "string",
                            "required": True,
                            "nullable": False,
                        },
                        {
                            "id": "field_2",
                            "name": "amount",
                            "description": "Amount",
                            "valueType": "number",
                            "required": True,
                            "nullable": False,
                        },
                    ],
                    "jsonSchema": {},
                    "repairAttempts": 1,
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "parameters"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "extractor"},
            {"id": "e2", "source": "extractor", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "Order A-19 costs 42.5"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["variables"]["parameters"] == {
        "order_id": "A-19",
        "amount": 42.5,
    }
    assert calls == 2


@pytest.mark.asyncio
async def test_question_classifier_v2_runs_only_the_selected_stable_branch(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_model(*args, **kwargs):
        raise AssertionError("rules_only classifier must not call a model")

    monkeypatch.setattr(main_module, "collect_chat_completion_text", unexpected_model)
    workflow = {
        "id": "classifier-v2-runtime",
        "title": "classifier-v2-runtime",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {
                "id": "classifier",
                "type": "question_classifier",
                "data": {
                    "kind": "question_classifier",
                    "contractVersion": 2,
                    "inputVariable": "user_input",
                    "outputVariable": "category",
                    "classificationMode": "rules_only",
                    "categoriesV2": [
                        {
                            "id": "category_1",
                            "label": "退款",
                            "description": "",
                            "keywords": ["退款"],
                            "matchMode": "contains_any",
                        },
                        {
                            "id": "category_2",
                            "label": "物流",
                            "description": "",
                            "keywords": ["物流"],
                            "matchMode": "contains_any",
                        },
                    ],
                    "caseSensitive": False,
                    "modelId": "",
                    "defaultLabel": "其他",
                },
            },
            *[
                {
                    "id": f"output-{index}",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "category"},
                }
                for index in range(1, 4)
            ],
        ],
        "edges": [
            {"id": "e0", "source": "input", "target": "classifier"},
            {
                "id": "e1",
                "source": "classifier",
                "sourceHandle": "category_1",
                "target": "output-1",
            },
            {
                "id": "e2",
                "source": "classifier",
                "sourceHandle": "category_2",
                "target": "output-2",
            },
            {
                "id": "e3",
                "source": "classifier",
                "sourceHandle": "default",
                "target": "output-3",
            },
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "请查询物流进度"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert "matched_keyword" not in response.text
    completed = next(event for event in events if event.get("event") == "workflow_end")
    assert completed["variables"]["category"] == "category_2"
    starts = {
        event.get("node_id")
        for event in events
        if event.get("event") == "node_start"
    }
    assert "output-2" in starts
    assert "output-1" not in starts
    assert "output-3" not in starts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_reply", "expected_category", "expected_output"),
    [
        ('{"categoryId":"category_2"}', "category_2", "output-category_2"),
        ('{"categoryId":"default"}', "default", "output-default"),
    ],
)
async def test_question_classifier_v2_model_decision_uses_only_stable_ids(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    model_reply: str,
    expected_category: str,
    expected_output: str,
) -> None:
    async def fake_collect(*args, **kwargs):
        return model_reply

    monkeypatch.setattr(main_module, "WORKFLOW_QUESTION_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _classifier_v2_model_workflow("model_only"),
            "inputs": {"user_input": "没有规则提示的合成问题"},
        },
    )
    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    end = next(event for event in events if event.get("event") == "workflow_end")
    assert end["variables"]["category"] == expected_category
    starts = {event.get("node_id") for event in events if event.get("event") == "node_start"}
    assert expected_output in starts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_input", "expected_model_calls", "expected_category"),
    [
        ("请查询物流", 0, "category_2"),
        ("无法由规则识别", 1, "category_1"),
    ],
)
async def test_question_classifier_v2_rules_then_model_calls_only_after_rule_miss(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    user_input: str,
    expected_model_calls: int,
    expected_category: str,
) -> None:
    calls = 0

    async def fake_collect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return '{"categoryId":"category_1"}'

    monkeypatch.setattr(main_module, "WORKFLOW_QUESTION_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _classifier_v2_model_workflow("rules_then_model"),
            "inputs": {"user_input": user_input},
        },
    )

    assert response.status_code == 200, response.text
    end = next(
        event
        for event in _parse_sse_events(response.text)
        if event.get("event") == "workflow_end"
    )
    assert end["variables"]["category"] == expected_category
    assert calls == expected_model_calls


@pytest.mark.asyncio
async def test_question_classifier_v2_rules_only_uses_default_without_model(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden_model(*args, **kwargs):
        raise AssertionError("rules_only default must not call the model")

    monkeypatch.setattr(main_module, "collect_chat_completion_text", forbidden_model)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _classifier_v2_model_workflow("rules_only"),
            "inputs": {"user_input": "无法由规则识别"},
        },
    )

    assert response.status_code == 200, response.text
    end = next(
        event
        for event in _parse_sse_events(response.text)
        if event.get("event") == "workflow_end"
    )
    assert end["variables"]["category"] == "default"


@pytest.mark.asyncio
async def test_question_classifier_v2_invalid_model_result_fails_instead_of_defaulting(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(*args, **kwargs):
        return '{"categoryId":"退款"}'

    monkeypatch.setattr(main_module, "WORKFLOW_QUESTION_CLASSIFIER_ENABLED", True)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("url", "key"))
    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = await client.post(
        "/api/workflow/run",
        json={
            "workflow": _classifier_v2_model_workflow("model_only"),
            "inputs": {"user_input": "合成问题"},
        },
    )
    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    assert any(event.get("event") == "error" for event in events)
    starts = {event.get("node_id") for event in events if event.get("event") == "node_start"}
    assert not starts.intersection({"output-category_1", "output-category_2", "output-default"})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["parameter_extractor", "question_classifier"])
async def test_typed_ai_unknown_contract_version_fails_closed_at_runtime(
    client: httpx.AsyncClient,
    kind: str,
) -> None:
    data = {
        "kind": kind,
        "contractVersion": 3,
        "inputVariable": "user_input",
        "outputVariable": "result",
    }
    if kind == "parameter_extractor":
        data.update({"modelId": "test/model", "schema": "topic: Topic"})
    else:
        data.update(
            {
                "categories": '{"Support":["help"],"Sales":["buy"]}',
                "defaultCategory": "Other",
                "matchMode": "contains_any",
                "caseSensitive": "false",
                "useLlmFallback": "false",
                "modelId": "",
            }
        )
    workflow = {
        "id": f"{kind}-unknown-contract",
        "title": f"{kind}-unknown-contract",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "data": {"kind": "input", "variableName": "user_input"},
            },
            {"id": "typed-ai", "type": kind, "data": data},
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "result"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "typed-ai"},
            {"id": "e2", "source": "typed-ai", "target": "output"},
        ],
    }

    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "safe sample"}},
    )

    assert response.status_code == 200, response.text
    events = _parse_sse_events(response.text)
    errors = [event for event in events if event.get("event") == "error"]
    assert errors
    assert "contractVersion must be 1 or 2" in errors[-1]["message"]
    assert not any(event.get("event") == "workflow_end" for event in events)


@pytest.mark.asyncio
async def test_parameter_extractor_v1_keeps_legacy_string_fallback(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    workflow = {
        "id": "extractor-v1-runtime",
        "title": "extractor-v1-runtime",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
            {
                "id": "extractor",
                "type": "parameter_extractor",
                "data": {
                    "kind": "parameter_extractor",
                    "inputVariable": "user_input",
                    "modelId": "legacy/model",
                    "schema": "name: 姓名",
                    "outputVariable": "parameters_json",
                },
            },
            {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "parameters_json"}},
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "extractor"},
            {"id": "e2", "source": "extractor", "target": "output"},
        ],
    }
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "姓名是小明"}},
    )
    assert response.status_code == 200, response.text
    end = next(event for event in _parse_sse_events(response.text) if event.get("event") == "workflow_end")
    assert end["variables"]["parameters_json"] == "{}"


@pytest.mark.asyncio
async def test_question_classifier_v1_keeps_name_string_and_single_outlet(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WORKFLOW_QUESTION_CLASSIFIER_ENABLED", True)
    workflow = {
        "id": "classifier-v1-runtime",
        "title": "classifier-v1-runtime",
        "nodes": [
            {"id": "input", "type": "input", "data": {"kind": "input", "variableName": "user_input"}},
            {
                "id": "classifier",
                "type": "question_classifier",
                "data": {
                    "kind": "question_classifier",
                    "inputVariable": "user_input",
                    "outputVariable": "category",
                    "categories": '{"退款":["退款"],"物流":["物流"]}',
                    "defaultCategory": "其他",
                    "matchMode": "contains_any",
                    "caseSensitive": "false",
                    "useLlmFallback": "false",
                    "modelId": "",
                },
            },
            {"id": "output", "type": "output", "data": {"kind": "output", "outputVariable": "category"}},
        ],
        "edges": [
            {"id": "e1", "source": "input", "target": "classifier"},
            {"id": "e2", "source": "classifier", "target": "output"},
        ],
    }
    response = await client.post(
        "/api/workflow/run",
        json={"workflow": workflow, "inputs": {"user_input": "我要退款"}},
    )
    assert response.status_code == 200, response.text
    end = next(event for event in _parse_sse_events(response.text) if event.get("event") == "workflow_end")
    assert end["variables"]["category"] == "退款"


def test_declared_workflow_variables_merge_constants_defaults_and_run_inputs() -> None:
    workflow = _json_round_trip_workflow()
    workflow["variables"] = [
        {
            "id": "constant-mode",
            "name": "fixed_mode",
            "kind": "constant",
            "valueType": "text",
            "defaultValue": "safe",
        },
        {
            "id": "input-locale",
            "name": "locale",
            "kind": "input",
            "valueType": "text",
            "defaultValue": "zh-CN",
        },
        {
            "id": "input-options",
            "name": "options",
            "kind": "input",
            "valueType": "json",
        },
    ]
    request = WorkflowRunRequest.model_validate(
        {
            "workflow": workflow,
            "inputs": {
                "user_input": "hello",
                "locale": "en-US",
                "options": {"strict": True},
            },
        }
    )

    assert initialize_declared_workflow_variables(
        request.workflow,
        request.inputs,
    ) == {
        "fixed_mode": "safe",
        "locale": "en-US",
        "user_input": "hello",
        "options": {"strict": True},
    }


def test_workflow_run_request_rejects_constant_override_and_unsafe_declarations() -> None:
    workflow = _json_round_trip_workflow()
    workflow["variables"] = [
        {
            "id": "constant-mode",
            "name": "fixed_mode",
            "kind": "constant",
            "valueType": "text",
            "defaultValue": "safe",
        }
    ]
    with pytest.raises(ValidationError, match="workflow_constant_override_not_allowed"):
        WorkflowRunRequest.model_validate(
            {"workflow": workflow, "inputs": {"fixed_mode": "unsafe"}}
        )

    workflow["variables"] = [
        {
            "id": "unsafe-path",
            "name": "api_key",
            "kind": "constant",
            "valueType": "json",
            "defaultValue": {"path": "C:\\private\\secret.txt"},
        }
    ]
    with pytest.raises(ValidationError, match="workflow_variable_sensitive"):
        WorkflowRunRequest.model_validate({"workflow": workflow, "inputs": {}})

    workflow["variables"] = [
        {
            "id": "unsafe-value",
            "name": "service_value",
            "kind": "constant",
            "valueType": "text",
            "defaultValue": "sk-abcdefghijklmnop",
        }
    ]
    with pytest.raises(ValidationError, match="workflow_variable_sensitive_value"):
        WorkflowRunRequest.model_validate({"workflow": workflow, "inputs": {}})


def test_workflow_run_request_rejects_declared_input_type_mismatch() -> None:
    workflow = _json_round_trip_workflow()
    workflow["variables"] = [
        {
            "id": "input-limit",
            "name": "limit",
            "kind": "input",
            "valueType": "number",
        }
    ]

    with pytest.raises(ValidationError, match="workflow_input_type_mismatch:limit:number"):
        WorkflowRunRequest.model_validate(
            {"workflow": workflow, "inputs": {"limit": "ten"}}
        )

    request = WorkflowRunRequest.model_validate(
        {"workflow": workflow, "inputs": {"limit": 10}}
    )
    assert request.inputs["limit"] == 10
    assert "defaultValue" not in request.workflow.model_dump()["variables"][0]


def test_native_validation_counts_declared_inputs_as_initial_variables() -> None:
    workflow = _json_round_trip_workflow()
    workflow["variables"] = [
        {
            "id": "input-options",
            "name": "options",
            "kind": "input",
            "valueType": "json",
        }
    ]
    workflow["nodes"][1]["data"]["inputVariable"] = "options"

    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(workflow))

    assert result.valid is True
    assert not any("missing_json_serialize_input_variable_reference" == issue.code for issue in result.issues)


def test_native_validation_rejects_duplicate_declaration_ids_and_names() -> None:
    workflow = _json_round_trip_workflow()
    workflow["variables"] = [
        {
            "id": "same-id",
            "name": "first_value",
            "kind": "input",
            "valueType": "text",
        },
        {
            "id": "same-id",
            "name": "first_value",
            "kind": "input",
            "valueType": "text",
        },
    ]

    result = validate_workflow_graph(NativeWorkflowDefinition.model_validate(workflow))

    assert result.valid is False
    assert {issue.code for issue in result.issues}.issuperset(
        {
            "duplicate_workflow_variable_declaration_id",
            "duplicate_workflow_variable_declaration",
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
