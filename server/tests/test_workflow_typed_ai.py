from __future__ import annotations

import copy

import pytest

from server.workflow_native.typed_ai import (
    WorkflowTypedAIError,
    build_parameter_extractor_schema,
    contract_version,
    parse_and_validate_extractor_output,
    parse_question_classifier_model_output,
    select_question_classifier_rule,
    validate_parameter_extractor_v2_config,
    validate_question_classifier_v2_config,
)


@pytest.mark.parametrize("value", ["2", 2.0, True, 3])
def test_typed_ai_contract_version_rejects_coercion_and_unknown_versions(
    value: object,
) -> None:
    assert contract_version({"contractVersion": value}) not in {1, 2}


def extractor_config(**updates: object) -> dict[str, object]:
    config: dict[str, object] = {
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
                "description": "Order identifier",
                "valueType": "string",
                "required": True,
                "nullable": False,
            },
            {
                "id": "field_2",
                "name": "amount",
                "description": "Order amount",
                "valueType": "number",
                "required": False,
                "nullable": True,
            },
        ],
        "jsonSchema": {},
        "repairAttempts": 0,
    }
    config.update(updates)
    return config


def classifier_config(**updates: object) -> dict[str, object]:
    config: dict[str, object] = {
        "contractVersion": 2,
        "inputVariable": "user_input",
        "outputVariable": "category",
        "classificationMode": "rules_then_model",
        "caseSensitive": False,
        "modelId": "test/model",
        "defaultLabel": "其他",
        "categoriesV2": [
            {
                "id": "category_1",
                "label": "退款",
                "description": "Customer requests a refund",
                "keywords": ["退款", "退钱"],
                "matchMode": "contains_any",
            },
            {
                "id": "category_2",
                "label": "物流",
                "description": "Shipping and delivery questions",
                "keywords": ["物流", "快递"],
                "matchMode": "contains_any",
            },
        ],
    }
    config.update(updates)
    return config


def test_parameter_extractor_fields_build_strict_typed_schema() -> None:
    schema = validate_parameter_extractor_v2_config(extractor_config())

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["order_id"]
    assert schema["properties"]["amount"]["anyOf"][1] == {"type": "null"}
    assert parse_and_validate_extractor_output(
        '```json\n{"order_id":"A-1","amount":12.5}\n```',
        schema,
    ) == {"order_id": "A-1", "amount": 12.5}


def test_parameter_extractor_object_list_and_strict_failure() -> None:
    config = extractor_config(outputShape="object_list")
    schema = build_parameter_extractor_schema(config)

    assert schema["type"] == "array"
    assert parse_and_validate_extractor_output(
        '[{"order_id":"A-1"},{"order_id":"A-2","amount":null}]',
        schema,
    )[1]["amount"] is None
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        parse_and_validate_extractor_output('[{"order_id":3}]', schema)
    assert exc_info.value.code == "parameter_extractor_schema_mismatch"
    assert "3" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        ({"repairAttempts": 2}, "invalid_parameter_extractor_repair_attempts"),
        ({"repairAttempts": "1"}, "invalid_parameter_extractor_repair_attempts"),
        ({"fields": []}, "invalid_parameter_extractor_fields"),
        (
            {
                "schemaMode": "json_schema",
                "jsonSchema": {"type": "object", "$ref": "https://example.invalid/schema"},
            },
            "parameter_extractor_schema_ref_forbidden",
        ),
        (
            {
                "schemaMode": "json_schema",
                "outputShape": "object_list",
                "jsonSchema": {"type": "object"},
            },
            "invalid_parameter_extractor_schema_root",
        ),
    ],
)
def test_parameter_extractor_rejects_invalid_contracts(
    updates: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        validate_parameter_extractor_v2_config(extractor_config(**updates))
    assert exc_info.value.code == code


@pytest.mark.parametrize(
    ("schema", "code"),
    [
        (
            {
                "type": "object",
                "properties": {
                    f"field_{index}": {"type": "string"}
                    for index in range(101)
                },
            },
            "parameter_extractor_schema_too_many_properties",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "nested": {
                        "type": "object",
                        "properties": {
                            "a": {
                                "type": "object",
                                "properties": {
                                    "b": {
                                            "type": "object",
                                            "properties": {
                                                "c": {
                                                    "type": "object",
                                                    "properties": {
                                                        "d": {"type": "string"},
                                                    },
                                                },
                                            },
                                    }
                                },
                            }
                        },
                    }
                },
            },
            "parameter_extractor_schema_too_deep",
        ),
        (
            {
                "type": "object",
                "description": "x" * (64 * 1024),
                "properties": {},
            },
            "parameter_extractor_schema_too_large",
        ),
    ],
)
def test_parameter_extractor_rejects_schema_complexity_limits(
    schema: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        validate_parameter_extractor_v2_config(
            extractor_config(schemaMode="json_schema", jsonSchema=schema)
        )
    assert exc_info.value.code == code


def test_parameter_extractor_rejects_typed_output_before_assignment_limit() -> None:
    schema = validate_parameter_extractor_v2_config(extractor_config())
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        parse_and_validate_extractor_output(
            '{"order_id":"R19-LONG-VALUE"}',
            schema,
            max_output_bytes=10,
        )
    assert exc_info.value.code == "parameter_extractor_output_too_large"


def test_parameter_extractor_requires_boolean_field_flags() -> None:
    config = extractor_config()
    config["fields"][0]["required"] = "true"

    with pytest.raises(WorkflowTypedAIError) as exc_info:
        validate_parameter_extractor_v2_config(config)
    assert exc_info.value.code == "invalid_parameter_extractor_field_flags"


def test_question_classifier_selects_first_stable_rule() -> None:
    categories = validate_question_classifier_v2_config(classifier_config())

    selection = select_question_classifier_rule(
        "我要查物流，也可能退款",
        categories,
        case_sensitive=False,
    )
    assert selection is not None
    assert selection.category_id == "category_1"
    assert selection.matched_keyword == "退款"


def test_question_classifier_accepts_only_declared_model_ids_or_default() -> None:
    categories = validate_question_classifier_v2_config(classifier_config())

    selected = parse_question_classifier_model_output(
        '{"categoryId":"category_2"}',
        categories,
        default_label="其他",
    )
    assert selected.category_id == "category_2"
    assert selected.method == "model"
    fallback = parse_question_classifier_model_output(
        '{"categoryId":"default"}',
        categories,
        default_label="其他",
    )
    assert fallback.category_id == "default"
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        parse_question_classifier_model_output(
            '{"categoryId":"退款"}',
            categories,
            default_label="其他",
        )
    assert exc_info.value.code == "question_classifier_unknown_category"
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        parse_question_classifier_model_output(
            '{"categoryId":"category_1","explanation":"not allowed"}',
            categories,
            default_label="其他",
        )
    assert exc_info.value.code == "question_classifier_invalid_model_output"


def test_question_classifier_ids_survive_reordering() -> None:
    config = classifier_config()
    original = validate_question_classifier_v2_config(config)
    reordered = copy.deepcopy(config)
    reordered["categoriesV2"] = list(reversed(reordered["categoriesV2"]))

    updated = validate_question_classifier_v2_config(reordered)

    assert {item.id: item.label for item in original} == {
        item.id: item.label for item in updated
    }


def test_question_classifier_rules_only_requires_keywords() -> None:
    config = classifier_config(classificationMode="rules_only", modelId="")
    config["categoriesV2"][0]["keywords"] = []

    with pytest.raises(WorkflowTypedAIError) as exc_info:
        validate_question_classifier_v2_config(config)
    assert exc_info.value.code == "missing_question_classifier_keywords"


def test_question_classifier_requires_strict_boolean_and_bounded_keywords() -> None:
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        validate_question_classifier_v2_config(
            classifier_config(caseSensitive="false")
        )
    assert exc_info.value.code == "invalid_question_classifier_case_sensitive"

    config = classifier_config()
    config["categoriesV2"][0]["keywords"] = ["x" * 201]
    with pytest.raises(WorkflowTypedAIError) as exc_info:
        validate_question_classifier_v2_config(config)
    assert exc_info.value.code == "invalid_question_classifier_keywords"


def test_question_classifier_contains_all_respects_case_sensitivity() -> None:
    config = classifier_config(classificationMode="rules_only", modelId="")
    config["categoriesV2"][0].update(
        {"keywords": ["Refund", "Order"], "matchMode": "contains_all"}
    )
    categories = validate_question_classifier_v2_config(config)

    selected = select_question_classifier_rule(
        "Refund Order",
        categories,
        case_sensitive=True,
    )
    assert selected is not None and selected.category_id == "category_1"
    assert select_question_classifier_rule(
        "refund order",
        categories,
        case_sensitive=True,
    ) is None
