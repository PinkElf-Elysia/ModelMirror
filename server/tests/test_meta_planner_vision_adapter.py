from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from server.meta_agent.node_adapters import (
    META_PLANNER_COMPILABLE_NODE_KINDS,
    PlannerNodeCompileContext,
    get_planner_node_adapter,
    planner_capability_metadata,
)
from server.meta_agent.schemas import (
    MetaPlannerIRInputBinding,
    MetaPlannerIRNode,
    MetaPlannerIROutputBinding,
)
from server.multimodal.vision_v2 import VisionModelBindingSnapshot
from server.workflow_native.node_contracts import (
    VisionUnderstandingPlannerConfig,
    WorkflowValueSchema,
    canonical_checksum,
    node_policy_service,
    vision_understanding_result_schema,
    workflow_node_contract_registry,
)
from server.workflow_native.schemas import WorkflowPosition


STRING = WorkflowValueSchema(type="string")
VISION_CONFIG = {
    "pdf_page_strategy": "all",
    "max_pages": 10,
    "max_image_edge": 2048,
    "failure_policy": "continue_on_error",
}


def _snapshot_payload(**updates) -> dict:
    payload = {
        "entry_id": "xpert_vision",
        "model_id": "managed/vision-model",
        "execution_shape": "vision_json_unary",
        "connection_id": "managed-vision-connection",
        "certification_id": "vision-v2-certification",
        "connection_fingerprint": "1" * 64,
        "qualification_fingerprint": "2" * 64,
        "adapter_contract": "vision-analysis-v2",
        "protocol_version": "openai-chat-completions-v1",
    }
    payload.update(updates)
    payload.pop("checksum", None)
    payload["checksum"] = canonical_checksum(payload)
    return payload


VISION_MODEL_SNAPSHOT = VisionModelBindingSnapshot.model_validate(
    _snapshot_payload()
).model_dump(mode="json")


def _node(**updates) -> MetaPlannerIRNode:
    payload = {
        "ref": "understand_asset",
        "kind": "vision_understanding",
        "title": "Understand asset",
        "description": "Read the selected private attachment.",
        "task_ids": [],
        "inputs": [
            MetaPlannerIRInputBinding(
                port="asset_id",
                variable="selected_file_asset_id",
                value_type="string",
            )
        ],
        "outputs": [
            MetaPlannerIROutputBinding(
                port="result",
                variable="vision_result",
                value_type="object",
            )
        ],
        "config": dict(VISION_CONFIG),
    }
    payload.update(updates)
    return MetaPlannerIRNode.model_validate(payload)


def _context(
    snapshot: dict | None = VISION_MODEL_SNAPSHOT,
) -> PlannerNodeCompileContext:
    return PlannerNodeCompileContext(
        node_id="planner_vision_1",
        position=WorkflowPosition(x=320, y=80),
        default_agent_model_id="must-not-be-used",
        output_variable="vision_result",
        acceptance_criteria="",
        has_runtime_resources=False,
        requires_runtime_mode=False,
        vision_model_snapshot=deepcopy(snapshot),
    )


def _compile(node: MetaPlannerIRNode | None = None, *, snapshot=VISION_MODEL_SNAPSHOT):
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    source = node or _node()
    parsed = adapter.validate_config(source)
    return adapter.compile_node(source, parsed, _context(snapshot))


def _annotate_v3(native) -> None:
    native.data.update(
        {
            "plannerIRVersion": 3,
            "plannerInputsV3": [
                {
                    "port": "asset_id",
                    "variable": "selected_file_asset_id",
                    "source_ref": "input",
                    "source_port": "selected_file_asset_id",
                    "value_schema": STRING.model_dump(mode="json"),
                }
            ],
            "plannerOutputsV3": [
                {
                    "port": "result",
                    "variable": "vision_result",
                    "value_schema": vision_understanding_result_schema().model_dump(
                        mode="json"
                    ),
                }
            ],
        }
    )


def test_contract_exposes_nineteenth_adapter_without_resource_binding() -> None:
    contract = workflow_node_contract_registry.require("vision_understanding")
    metadata = planner_capability_metadata("vision_understanding")

    assert len(META_PLANNER_COMPILABLE_NODE_KINDS) == 19
    assert metadata is not None
    assert metadata["support"] == "full"
    assert contract.planner.enabled is True
    assert contract.planner.task_binding == "forbidden"
    assert contract.resources == ()
    assert contract.planner.config_constraints["final_output"] == "forbidden"
    assert not node_policy_service.decision(
        "vision_understanding", "evaluation"
    ).allowed
    assert not node_policy_service.decision("vision_understanding", "app").allowed
    assert not node_policy_service.decision(
        "vision_understanding", "evolution"
    ).allowed
    assert any(
        "\u4e00" <= char <= "\u9fff"
        for char in node_policy_service.decision(
            "vision_understanding", "app"
        ).message
    )
    assert any(
        "\u4e00" <= char <= "\u9fff"
        for char in node_policy_service.decision(
            "vision_understanding", "evaluation"
        ).message
    )


def test_contract_keeps_legacy_v1_and_requires_full_v2_branch() -> None:
    contract = workflow_node_contract_registry.require("vision_understanding")
    validator = Draft202012Validator(contract.config_schema)
    legacy = {
        "assetIdVariable": "selected_file_asset_id",
        "visionModelId": "legacy/model",
        "pdfPageStrategy": "auto",
        "maxPages": 100,
        "maxImageEdge": 2048,
        "failurePolicy": "continue_on_error",
        "outputVariable": "vision_result",
    }
    v1 = {**legacy, "contractVersion": 1}
    v2 = _compile().data

    assert list(validator.iter_errors(legacy)) == []
    assert list(validator.iter_errors(v1)) == []
    assert list(validator.iter_errors(v2)) == []
    assert list(validator.iter_errors({**v2, "maxPages": 21}))
    incomplete_v2 = deepcopy(v2)
    incomplete_v2.pop("visionModelBinding")
    assert list(validator.iter_errors(incomplete_v2))

    binding_schema = contract.config_schema["anyOf"][2]["properties"][
        "visionModelBinding"
    ]
    assert binding_schema == VisionModelBindingSnapshot.model_json_schema()
    forged_binding_v2 = deepcopy(v2)
    forged_binding_v2["visionModelBinding"]["handle"] = "forged"
    assert list(validator.iter_errors(forged_binding_v2))
    wrong_type_v2 = deepcopy(v2)
    wrong_type_v2["visionModelBinding"]["connection_id"] = 7
    assert list(validator.iter_errors(wrong_type_v2))


def test_planner_config_defaults_and_schema_are_strict() -> None:
    parsed = VisionUnderstandingPlannerConfig.model_validate({})
    schema = VisionUnderstandingPlannerConfig.model_json_schema()

    assert parsed.model_dump(mode="json") == VISION_CONFIG
    assert set(schema["properties"]) == set(VISION_CONFIG)
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "field",
    [
        "model_id",
        "visionModelId",
        "asset_id_variable",
        "output_variable",
        "contract_version",
        "id",
        "version",
        "handle",
        "checksum",
    ],
)
def test_planner_config_rejects_compiler_owned_fields(field: str) -> None:
    config = {**VISION_CONFIG, field: "forged"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VisionUnderstandingPlannerConfig.model_validate(config)


@pytest.mark.parametrize(
    "updates",
    [
        {"pdf_page_strategy": "random"},
        {"max_pages": 0},
        {"max_pages": 21},
        {"max_pages": "10"},
        {"max_image_edge": 511},
        {"max_image_edge": 4097},
        {"failure_policy": "ignore"},
    ],
)
def test_planner_config_rejects_invalid_semantic_values(updates: dict) -> None:
    with pytest.raises(ValidationError):
        VisionUnderstandingPlannerConfig.model_validate(
            {**VISION_CONFIG, **updates}
        )


@pytest.mark.parametrize(
    "attack",
    [
        "task_ids",
        "resource_ref",
        "input_variable",
        "input_handle",
        "extra_input",
        "output_handle",
        "extra_output",
    ],
)
def test_shape_rejects_task_resource_port_and_variable_forgery(attack: str) -> None:
    node = _node()
    if attack == "task_ids":
        node.task_ids = ["forged_task"]
    elif attack == "resource_ref":
        node.resource_ref = {"resource_id": "asset-1"}
    elif attack == "input_variable":
        node.inputs[0].variable = "other_asset_id"
    elif attack == "input_handle":
        node.inputs[0].port = "asset_id_handle"
    elif attack == "extra_input":
        node.inputs.append(
            MetaPlannerIRInputBinding(
                port="asset_id",
                variable="selected_file_asset_id",
                value_type="string",
            )
        )
    elif attack == "output_handle":
        node.outputs[0].port = "result_handle"
    else:
        node.outputs.append(
            MetaPlannerIROutputBinding(
                port="result",
                variable="forged_result",
                value_type="object",
            )
        )
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None

    with pytest.raises(ValueError):
        adapter.validate_config(node)


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        {key: value for key, value in VISION_MODEL_SNAPSHOT.items() if key != "model_id"},
        {**VISION_MODEL_SNAPSHOT, "checksum": "0" * 64},
        _snapshot_payload(entry_id="workflow_interactive_vision"),
        _snapshot_payload(execution_shape="vision_chat"),
        _snapshot_payload(connection_id=7),
        _snapshot_payload(handle="forged"),
    ],
)
def test_compile_requires_managed_snapshot_fields(snapshot) -> None:
    with pytest.raises(ValueError, match="视觉模型|Managed Binding"):
        _compile(snapshot=snapshot)


def test_compile_pins_managed_model_and_success_only_native_data() -> None:
    native = _compile()
    contract = workflow_node_contract_registry.require("vision_understanding")

    assert native.type == "vision_understanding"
    assert native.data["contractVersion"] == 2
    assert native.data["visionModelId"] == VISION_MODEL_SNAPSHOT["model_id"]
    assert native.data["visionModelBinding"] == VISION_MODEL_SNAPSHOT
    assert native.data["plannerVisionModelBindingChecksum"] == canonical_checksum(
        VISION_MODEL_SNAPSHOT
    )
    assert native.data["assetIdVariable"] == "selected_file_asset_id"
    assert native.data["outputVariable"] == "vision_result"
    assert native.data["plannerAdapterConfigV1"] == VISION_CONFIG
    assert native.data["plannerCompilerChecksum"] == contract.compiler_checksum
    assert native.data["plannerInputs"] == [
        {
            "port": "asset_id",
            "variable": "selected_file_asset_id",
            "value_type": "string",
        }
    ]
    assert native.data["plannerOutcomeMapV1"] == {"success": ""}
    assert "plannerOutcomeMapV2" not in native.data
    assert "retryMode" not in native.data
    assert "errorVariable" not in native.data
    assert "must-not-be-used" not in str(native.data)


def test_v2_compile_decompile_round_trip_is_stable() -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    source = _node()
    native = _compile(source)
    restored = adapter.decompile_node(native)
    rebuilt = _compile(restored)

    assert restored == source
    assert rebuilt.model_dump(mode="json") == native.model_dump(mode="json")
    assert set(restored.config) == set(VISION_CONFIG)
    assert "model_id" not in restored.config
    assert restored.resource_ref is None


def test_v3_decompile_preserves_typed_ports_without_model_in_intent() -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    native = _compile()
    _annotate_v3(native)

    restored = adapter.decompile_node_v3(native)

    assert restored.inputs[0].source_ref == "input"
    assert restored.inputs[0].source_port == "selected_file_asset_id"
    assert restored.inputs[0].value_schema == STRING
    assert restored.outputs[0].value_schema == vision_understanding_result_schema()
    assert restored.config == VISION_CONFIG
    assert "visionModelBinding" not in restored.config
    assert "visionModelId" not in restored.config


@pytest.mark.parametrize("version", [None, 1, "2", 3])
def test_decompile_refuses_legacy_or_non_v2_execution_nodes(version) -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    native = _compile()
    if version is None:
        native.data.pop("contractVersion")
    else:
        native.data["contractVersion"] = version

    with pytest.raises(ValueError, match="contractVersion 2"):
        adapter.decompile_node(native)


@pytest.mark.parametrize(
    "attack",
    [
        "native_model",
        "binding_model",
        "binding_metadata",
        "binding_extra",
        "entry_id",
        "execution_shape",
        "field_type",
        "summary",
        "checksum",
    ],
)
def test_decompile_detects_model_binding_tampering(attack: str) -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    native = _compile()
    if attack == "native_model":
        native.data["visionModelId"] = "forged/model"
    elif attack == "binding_model":
        native.data["visionModelBinding"]["model_id"] = "forged/model"
    elif attack == "binding_metadata":
        native.data["visionModelBinding"]["connection_id"] = "forged-connection"
    elif attack == "binding_extra":
        native.data["visionModelBinding"]["handle"] = "forged"
    elif attack == "entry_id":
        native.data["visionModelBinding"] = _snapshot_payload(
            entry_id="workflow_interactive_vision"
        )
        native.data["plannerVisionModelBindingChecksum"] = canonical_checksum(
            native.data["visionModelBinding"]
        )
    elif attack == "execution_shape":
        native.data["visionModelBinding"]["execution_shape"] = "vision_chat"
    elif attack == "field_type":
        native.data["visionModelBinding"]["connection_id"] = 7
    elif attack == "summary":
        native.data["plannerVisionModelBindingChecksum"] = "0" * 64
    else:
        native.data["visionModelBinding"].pop("checksum")

    with pytest.raises(ValueError, match="Managed Binding|visionModelId|摘要"):
        adapter.decompile_node(native)


@pytest.mark.parametrize(
    "attack",
    ["asset_native", "output_native", "input_metadata", "output_metadata"],
)
def test_decompile_detects_native_variable_and_port_metadata_tampering(
    attack: str,
) -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    native = _compile()
    if attack == "asset_native":
        native.data["assetIdVariable"] = "forged_asset"
    elif attack == "output_native":
        native.data["outputVariable"] = "forged_result"
    elif attack == "input_metadata":
        native.data["plannerInputs"][0]["port"] = "asset_id_handle"
    else:
        native.data["plannerOutputs"][0]["variable"] = "forged_result"

    with pytest.raises(ValueError, match="视觉节点|asset_id|result"):
        adapter.decompile_node(native)


def test_v3_decompile_rejects_typed_output_metadata_forgery() -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    native = _compile()
    _annotate_v3(native)
    native.data["plannerOutputsV3"][0]["value_schema"] = {
        "type": "object",
        "properties": {},
    }

    with pytest.raises(ValueError, match="输出类型元数据"):
        adapter.decompile_node_v3(native)


def test_editor_projection_exposes_only_four_semantic_fields() -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    native = _compile()

    projected = adapter.authoring_config_from_native(native.data)

    assert projected.model_dump(mode="json") == VISION_CONFIG
    assert set(projected.model_dump(mode="json")) == set(VISION_CONFIG)
    _annotate_v3(native)
    native.data["visionModelId"] = "forged/model"
    with pytest.raises(ValueError, match="视觉模型|Managed Binding"):
        adapter.editor_config(native)


def test_authoritative_result_schema_matches_complete_workflow_payload() -> None:
    adapter = get_planner_node_adapter("vision_understanding")
    assert adapter is not None
    parsed = VisionUnderstandingPlannerConfig.model_validate({})
    schema = vision_understanding_result_schema()
    authoritative = adapter.authoritative_output_schema("result", parsed)
    contract = workflow_node_contract_registry.require("vision_understanding")
    legacy_result_port = next(
        port
        for port in contract.ports
        if port.direction == "output" and port.name == "result"
    )
    expected_fields = {
        "asset",
        "model_id",
        "page_count",
        "selected_page_count",
        "processed_page_count",
        "failed_page_count",
        "block_count",
        "blocks",
        "ocr",
        "visual_descriptions",
        "tables",
        "charts",
        "warnings",
        "truncated",
        "provider_route_receipts",
        "execution_mode",
        "fallback_reason_codes",
        "contract_version",
        "execution_summary",
    }
    block = {
        "block_id": "block-1",
        "kind": "image_ocr",
        "text": "hello",
        "page_number": None,
        "source_block_id": "source-1",
        "truncated": False,
    }
    payload = {
        "asset": {
            "asset_id": "asset-1",
            "filename": "scan.pdf",
            "format": "pdf",
            "byte_size": 1024,
            "sha256": "a" * 64,
        },
        "model_id": "managed/vision-model",
        "page_count": 1,
        "selected_page_count": 1,
        "processed_page_count": 1,
        "failed_page_count": 0,
        "block_count": 1,
        "blocks": [block],
        "ocr": [block],
        "visual_descriptions": [],
        "tables": [],
        "charts": [],
        "warnings": [],
        "truncated": False,
        "provider_route_receipts": [{}],
        "execution_mode": "managed",
        "fallback_reason_codes": [],
        "contract_version": 2,
        "execution_summary": {
            "model_calls": 1,
            "known_total_tokens": 42,
            "unverified_token_calls": 0,
            "token_usage_verified": True,
            "token_source": "provider",
            "uncertain_calls": 0,
        },
    }

    assert authoritative == schema
    assert legacy_result_port.value_schema == WorkflowValueSchema(type="object")
    assert adapter.intent_port_contracts("output")[0].value_schema == WorkflowValueSchema(
        type="object"
    )
    legacy_result_port.value_schema.assert_value(
        {"asset": {"asset_id": "legacy-asset"}}
    )
    assert set(schema.properties) == expected_fields
    assert set(schema.required) == expected_fields
    assert set(schema.properties["asset"].required) == {
        "asset_id",
        "filename",
        "format",
        "byte_size",
        "sha256",
    }
    assert schema.properties["blocks"].items.properties["page_number"].nullable
    schema.assert_value(payload)

    missing_sha = deepcopy(payload)
    missing_sha["asset"].pop("sha256")
    with pytest.raises(ValueError, match="sha256"):
        schema.assert_value(missing_sha)
    legacy_result_port.value_schema.assert_value(missing_sha)
    wrong_summary = deepcopy(payload)
    wrong_summary["execution_summary"]["model_calls"] = True
    with pytest.raises(ValueError, match="model_calls"):
        schema.assert_value(wrong_summary)
