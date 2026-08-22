from __future__ import annotations

import copy

import pytest

from server.workflow_native.control_data import (
    WorkflowControlDataError,
    compare_datasets,
    evaluate_typed_condition,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph


@pytest.mark.parametrize(
    ("actual", "operator", "value_type", "expected", "matched"),
    [
        ({"score": 3}, "equals", "number", 3, True),
        ({"score": 3}, "not_equals", "text", "3", True),
        ({"score": 3}, "gt", "number", 2, True),
        ({"score": 3}, "gte", "number", 3, True),
        ({"score": 3}, "lt", "number", 4, True),
        ({"score": 3}, "lte", "number", 3, True),
        ({"score": [1, "1"]}, "contains", "text", "1", True),
        ({"score": "beta"}, "in", "json", ["alpha", "beta"], True),
        ({"score": None}, "is_null", "null", None, True),
    ],
)
def test_typed_condition_supports_all_operators(
    actual: object,
    operator: str,
    value_type: str,
    expected: object,
    matched: bool,
) -> None:
    assert evaluate_typed_condition(
        actual,
        field="score",
        operator=operator,
        value_type=value_type,
        expected=expected,
    ) is matched


def test_typed_condition_fails_on_missing_field_and_invalid_type() -> None:
    with pytest.raises(WorkflowControlDataError) as missing:
        evaluate_typed_condition(
            {}, field="score", operator="equals", value_type="number", expected=1
        )
    assert missing.value.code == "CONDITION_FIELD_MISSING"

    with pytest.raises(WorkflowControlDataError) as invalid:
        evaluate_typed_condition(
            {"score": "3"}, field="score", operator="gt", value_type="number", expected=2
        )
    assert invalid.value.code == "NUMERIC_COMPARISON_TYPE_MISMATCH"


def test_dataset_compare_is_strict_ordered_and_does_not_mutate_inputs() -> None:
    left = [
        {"tenant": "a", "id": 1, "value": "old"},
        {"tenant": "a", "id": 2, "value": None},
        {"tenant": "b", "id": 3, "value": "removed"},
    ]
    right = [
        {"tenant": "a", "id": 2, "value": None},
        {"tenant": "a", "id": 1, "value": "new", "extra": True},
        {"tenant": "c", "id": 4, "value": "added"},
    ]
    before_left = copy.deepcopy(left)
    before_right = copy.deepcopy(right)

    result = compare_datasets(
        left,
        right,
        key_fields=["tenant", "id"],
        include_unchanged=True,
    )

    assert result["summary"] == {
        "leftCount": 3,
        "rightCount": 3,
        "addedCount": 1,
        "removedCount": 1,
        "changedCount": 1,
        "unchangedCount": 1,
    }
    assert result["added"] == [right[2]]
    assert result["removed"] == [left[2]]
    assert result["unchanged"] == [right[0]]
    assert result["changed"][0]["changedFields"] == ["extra", "value"]
    assert left == before_left
    assert right == before_right


def test_dataset_compare_distinguishes_key_types_and_missing_from_null() -> None:
    result = compare_datasets(
        [{"id": 1, "value": None}, {"id": "1", "value": "same"}],
        [{"id": "1", "value": "same"}, {"id": 1}],
        key_fields=["id"],
    )

    assert result["summary"]["changedCount"] == 1
    assert result["summary"]["unchangedCount"] == 1
    assert result["changed"][0]["changedFields"] == ["value"]


@pytest.mark.parametrize(
    ("left", "right", "code"),
    [
        ([{"id": 1}, {"id": 1}], [], "DATASET_KEY_NOT_UNIQUE"),
        ([{"value": 1}], [], "DATASET_KEY_FIELD_MISSING"),
        ([{"id": {"nested": 1}}], [], "DATASET_KEY_VALUE_NOT_SCALAR"),
        ([1], [], "DATASET_OBJECT_ARRAY_REQUIRED"),
    ],
)
def test_dataset_compare_rejects_invalid_rows(left: object, right: object, code: str) -> None:
    with pytest.raises(WorkflowControlDataError) as raised:
        compare_datasets(left, right, key_fields=["id"])
    assert raised.value.code == code


def test_dataset_compare_rejects_row_and_output_limits_before_assignment() -> None:
    with pytest.raises(WorkflowControlDataError) as rows:
        compare_datasets(
            [{"id": index} for index in range(10_001)],
            [],
            key_fields=["id"],
        )
    assert rows.value.code == "DATASET_ROW_LIMIT_EXCEEDED"

    with pytest.raises(WorkflowControlDataError) as output:
        compare_datasets(
            [],
            [{"id": 1, "value": "large"}],
            key_fields=["id"],
            max_output_bytes=10,
        )
    assert output.value.code == "DATASET_OUTPUT_LIMIT_EXCEEDED"


def linear_definition(kind: str, data: dict[str, object]) -> NativeWorkflowDefinition:
    output_variable = str(data.get("outputVariable") or "result")
    return NativeWorkflowDefinition.model_validate(
        {
            "id": f"r17-{kind}",
            "title": kind,
            "variables": [
                {"id": "v-user", "name": "user_input", "kind": "input", "valueType": "text"},
                {"id": "v-left", "name": "before_rows", "kind": "input", "valueType": "json"},
                {"id": "v-right", "name": "after_rows", "kind": "input", "valueType": "json"},
            ],
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "entry_event"},
                },
                {"id": "subject", "type": kind, "data": {"kind": kind, **data}},
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": output_variable},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "subject"},
                {"id": "e2", "source": "subject", "target": "output"},
            ],
        }
    )


def issue_codes(definition: NativeWorkflowDefinition) -> set[str]:
    return {issue.code for issue in validate_workflow_graph(definition).issues}


def test_static_validation_accepts_http_v2_and_checks_all_variable_bindings() -> None:
    definition = linear_definition(
        "http_request",
        {
            "contractVersion": 2,
            "method": "POST",
            "url": "https://api.example.test/items/{{user_input}}",
            "queryItems": [],
            "headerItems": [
                {
                    "id": "header_1",
                    "name": "X-Trace",
                    "binding": {"source": "variable", "variable": "user_input"},
                }
            ],
            "bodyMode": "text",
            "bodyBinding": {"source": "variable", "variable": "user_input"},
            "formFields": [],
            "authType": "none",
            "timeoutSeconds": 30,
            "redirectLimit": 0,
            "responseLimitBytes": 1_024,
            "responseMode": "auto",
            "statusPolicy": "success_only",
            "outputVariable": "http_response",
        },
    )
    assert validate_workflow_graph(definition).valid is True

    definition.nodes[1].data["bodyBinding"] = {
        "source": "variable",
        "variable": "missing_body",
    }
    assert "missing_http_request_variable_reference" in issue_codes(definition)


def test_static_validation_accepts_dataset_compare_and_rejects_duplicate_keys() -> None:
    definition = linear_definition(
        "dataset_compare",
        {
            "leftVariable": "before_rows",
            "rightVariable": "after_rows",
            "keyFields": ["tenant", "id"],
            "includeUnchanged": False,
            "outputVariable": "dataset_difference",
        },
    )
    assert validate_workflow_graph(definition).valid is True

    definition.nodes[1].data["keyFields"] = ["id", "id"]
    assert "duplicate_dataset_key_field" in issue_codes(definition)


def test_static_validation_rejects_invalid_condition_v2_type_contract() -> None:
    definition = linear_definition(
        "condition",
        {
            "contractVersion": 2,
            "inputVariable": "user_input",
            "field": "",
            "operator": "gt",
            "valueType": "text",
            "value": "2",
            "outputVariable": "result",
        },
    )
    codes = issue_codes(definition)
    assert "numeric_comparison_requires_number" in codes
