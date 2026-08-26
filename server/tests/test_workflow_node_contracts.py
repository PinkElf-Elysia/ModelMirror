from __future__ import annotations

from typing import get_args

import pytest
from jsonschema import Draft202012Validator

from server.meta_agent.node_adapters import (
    META_PLANNER_COMPILABLE_NODE_KINDS,
    PlannerNodeCompileContext,
    decompile_planner_node,
    get_planner_node_adapter,
    planner_capability_metadata,
)
from server.meta_agent.schemas import (
    MetaPlannerCapabilitySnapshot,
    MetaPlannerIRInputBinding,
    MetaPlannerIRNode,
    MetaPlannerIROutputBinding,
    MetaPlannerWorkflowAgentConfig,
)
from server.workflow_native.node_contracts import (
    NODE_CONTRACT_VERSION,
    canonical_checksum,
    node_policy_service,
    workflow_node_contract_registry,
)
from server.workflow_native.schemas import NativeNodeKind, WorkflowPosition
from server.xpert_runtime.workflow_node_registry import workflow_node_registry


EXPECTED_PLANNER_KINDS = {
    "input",
    "output",
    "workflow_agent",
    "external_xpert",
    "knowledge_base",
    "toolset_resource",
    "plugin_resource",
}

BASELINE_213_COMPATIBILITY_KINDS = {
    "agent",
    "agent_handoff",
    "agent_task",
    "code",
    "condition",
    "document_extractor",
    "handoff_router",
    "http_request",
    "human_intervention",
    "iteration",
    "knowledge_citation",
    "list_operation",
    "llm",
    "mcp_tool",
    "parameter_extractor",
    "question_classifier",
    "template_transform",
    "time_tool",
    "variable_assign",
    "variable_aggregator",
}
PROMOTED_COMPLETE_KINDS = {
    "agent_handoff",
    "agent_task",
    "code",
    "condition",
    "document_extractor",
    "http_request",
    "human_intervention",
    "iteration",
    "llm",
    "list_operation",
    "mcp_tool",
    "parameter_extractor",
    "question_classifier",
    "handoff_router",
    "time_tool",
    "variable_assign",
    "variable_aggregator",
}


def test_r22_variable_pack_contract_is_complete_and_not_plannable() -> None:
    contract = workflow_node_contract_registry.require("variable_aggregator")

    assert contract.contract_status == "complete"
    assert contract.execution.side_effect == "none"
    assert contract.execution.deterministic is True
    assert contract.execution.idempotent is True
    assert contract.execution.can_wait is False
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False
    assert contract.ports[0].name == "values"
    assert contract.ports[0].cardinality == "many"
    assert contract.ports[1].value_schema.type == "object"
    for context in (
        "workflow",
        "xpert",
        "goal",
        "handoff",
        "app",
        "evaluation",
        "evolution",
    ):
        assert node_policy_service.decision("variable_aggregator", context).allowed


def test_contract_registry_covers_every_native_kind_once() -> None:
    expected = set(get_args(NativeNodeKind))

    assert len(expected) == 52
    assert workflow_node_contract_registry.kinds() == expected
    assert len(workflow_node_contract_registry.list()) == len(expected)
    assert workflow_node_contract_registry.get("not-a-node") is None
    with pytest.raises(KeyError, match="unknown workflow node kind"):
        workflow_node_contract_registry.require("not-a-node")


def test_contract_projection_and_checksums_are_deterministic() -> None:
    first = workflow_node_contract_registry.to_safe_payload()
    second = workflow_node_contract_registry.to_safe_payload()

    assert first == second
    assert first["contract_version"] == NODE_CONTRACT_VERSION
    assert len(first["contract_checksum"]) == 64
    assert canonical_checksum(first["items"]) == canonical_checksum(second["items"])
    for item in first["items"]:
        assert item["config_schema"]["type"] == "object"
        assert item["kind"]
        assert len(item["checksum"]) == 64
        assert len(item["compiler_checksum"]) == 64
        assert item["execution"]
        assert item["availability"]
        assert item["planner"]


def test_compatibility_contracts_cannot_expand_beyond_pr_213_baseline() -> None:
    compatibility = {
        contract.kind
        for contract in workflow_node_contract_registry.list()
        if contract.contract_status == "compatibility"
    }

    assert compatibility == BASELINE_213_COMPATIBILITY_KINDS - PROMOTED_COMPLETE_KINDS


def test_llm_contract_is_complete_without_enabling_planner() -> None:
    contract = workflow_node_contract_registry.require("llm")

    assert contract.contract_status == "complete"
    assert set(contract.config_schema["required"]) == {
        "modelId",
        "prompt",
        "outputVariable",
    }
    assert contract.execution.external_io is True
    assert contract.execution.can_wait is False
    assert contract.planner.enabled is False


def test_failure_entry_contract_is_complete_private_and_not_plannable() -> None:
    contract = workflow_node_contract_registry.require("failure_event_entry")

    assert contract.contract_status == "complete"
    assert set(contract.config_schema["required"]) == {
        "sourceProjectIds",
        "eventVariable",
    }
    assert contract.config_schema["properties"]["sourceProjectIds"]["maxItems"] == 50
    assert contract.execution.security_category == "private_trigger"
    assert contract.planner.enabled is False
    assert node_policy_service.decision("failure_event_entry", "workflow").allowed
    assert not node_policy_service.decision("failure_event_entry", "xpert").allowed


def test_subworkflow_call_contract_does_not_overclaim_idempotency() -> None:
    contract = workflow_node_contract_registry.require("invoke_workflow")

    assert contract.contract_status == "complete"
    assert contract.execution.side_effect == "external_write"
    assert contract.execution.idempotent is False
    assert contract.execution.external_io is True
    assert contract.execution.can_wait is False
    assert contract.planner.enabled is False


def test_r16_control_data_contracts_are_complete_local_and_not_plannable() -> None:
    kinds = {"terminate_error", "multi_route", "list_operation", "data_aggregate"}

    for kind in kinds:
        contract = workflow_node_contract_registry.require(kind)
        assert contract.contract_status == "complete"
        assert contract.execution.side_effect == "none"
        assert contract.execution.deterministic is True
        assert contract.execution.external_io is False
        assert contract.execution.can_wait is False
        assert contract.planner.enabled is False
        assert node_policy_service.decision(kind, "workflow").allowed
        assert node_policy_service.decision(kind, "xpert").allowed

    assert workflow_node_contract_registry.require("terminate_error").edge.modes == ()
    assert set(
        workflow_node_contract_registry.require("multi_route").edge.allowed_source_handles
    ) == {
        "route_1",
        "route_2",
        "route_3",
        "route_4",
        "route_5",
        "route_6",
        "route_7",
        "route_8",
        "default",
    }
    assert sum(
        contract.contract_status == "compatibility"
        for contract in workflow_node_contract_registry.list()
        ) == 3


def test_r23_iteration_contract_is_complete_and_not_plannable() -> None:
    contract = workflow_node_contract_registry.require("iteration")

    assert contract.contract_status == "complete"
    assert contract.execution.side_effect == "external_write"
    assert contract.execution.idempotent is False
    assert contract.execution.can_wait is False
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False
    assert node_policy_service.decision("iteration", "workflow").allowed
    assert node_policy_service.decision("iteration", "xpert").allowed
    assert node_policy_service.decision("iteration", "app").allowed
    assert node_policy_service.decision("iteration", "evaluation").allowed
    assert not node_policy_service.decision("iteration", "evolution").allowed


def test_r17_http_condition_and_dataset_contracts_are_complete_and_not_plannable() -> None:
    for kind in {"http_request", "condition", "dataset_compare"}:
        contract = workflow_node_contract_registry.require(kind)
        assert contract.contract_status == "complete"
        assert contract.planner.enabled is False
        assert node_policy_service.decision(kind, "workflow").allowed
        assert node_policy_service.decision(kind, "xpert").allowed

    http_contract = workflow_node_contract_registry.require("http_request")
    assert http_contract.execution.external_io is True
    assert http_contract.execution.idempotent is False
    assert not node_policy_service.decision("http_request", "app").allowed
    assert not node_policy_service.decision("http_request", "evaluation").allowed
    assert not node_policy_service.decision("http_request", "evolution").allowed

    condition = workflow_node_contract_registry.require("condition")
    dataset = workflow_node_contract_registry.require("dataset_compare")
    assert condition.edge.allowed_source_handles == ("true", "false")
    assert dataset.execution.deterministic is True
    assert dataset.execution.external_io is False


def test_r21_data_merge_contract_is_complete_fanin_and_not_plannable() -> None:
    contract = workflow_node_contract_registry.require("data_merge")

    assert contract.contract_status == "complete"
    assert contract.edge.allowed_target_handles == ("left", "right")
    assert [port.name for port in contract.ports if port.direction == "input"] == [
        "left",
        "right",
    ]
    assert contract.execution.side_effect == "none"
    assert contract.execution.deterministic is True
    assert contract.execution.idempotent is True
    assert contract.execution.can_wait is False
    assert contract.planner.enabled is False
    assert node_policy_service.decision("data_merge", "workflow").allowed
    assert node_policy_service.decision("data_merge", "xpert").allowed
    assert not node_policy_service.decision("data_merge", "evolution").allowed


def test_r18_file_data_contracts_are_complete_scoped_and_not_plannable() -> None:
    for kind in {"document_extractor", "time_tool", "object_transform", "file_output"}:
        contract = workflow_node_contract_registry.require(kind)
        assert contract.contract_status == "complete"
        assert contract.planner.enabled is False
        assert node_policy_service.decision(kind, "workflow").allowed
        assert node_policy_service.decision(kind, "xpert").allowed

    for kind in {"document_extractor", "file_output"}:
        assert not node_policy_service.decision(kind, "goal").allowed
        assert not node_policy_service.decision(kind, "handoff").allowed
        assert not node_policy_service.decision(kind, "app").allowed
        assert not node_policy_service.decision(kind, "evaluation").allowed
        assert not node_policy_service.decision(kind, "evolution").allowed

    assert workflow_node_contract_registry.require("file_output").execution.side_effect == "write"
    assert workflow_node_contract_registry.require("file_output").execution.deterministic is False
    assert workflow_node_contract_registry.require("file_output").execution.idempotent is True
    assert workflow_node_contract_registry.require("object_transform").execution.side_effect == "none"
    assert sum(
        contract.contract_status == "compatibility"
        for contract in workflow_node_contract_registry.list()
    ) == 3


def test_r24_content_parser_contract_accepts_v3_http_and_file_modes() -> None:
    contract = workflow_node_contract_registry.require("document_extractor")
    validator = Draft202012Validator(contract.config_schema)

    http_config = {
        "contractVersion": 3,
        "sourceMode": "http_response",
        "inputVariable": "http_result",
        "format": "auto",
        "outputMode": "structured",
        "outputVariable": "parsed_content",
    }
    file_config = {
        "contractVersion": 3,
        "sourceMode": "file_asset",
        "assetIdVariable": "selected_file_asset_id",
        "format": "auto",
        "outputMode": "text",
        "outputVariable": "document_text",
    }

    assert list(validator.iter_errors(http_config)) == []
    assert list(validator.iter_errors(file_config)) == []
    assert list(validator.iter_errors({**http_config, "assetIdVariable": "asset"}))
    assert [port.name for port in contract.ports] == ["content", "parsed"]
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False


def test_r22_agent_collaboration_contracts_are_typed_and_not_plannable() -> None:
    task = workflow_node_contract_registry.require("agent_task")
    handoff = workflow_node_contract_registry.require("agent_handoff")
    router = workflow_node_contract_registry.require("handoff_router")

    for contract in (task, handoff, router):
        assert contract.contract_status == "complete"
        assert contract.planner.enabled is False
        assert contract.execution.error_semantics == "fail_closed"
        assert node_policy_service.decision(contract.kind, "workflow").allowed
        assert node_policy_service.decision(contract.kind, "xpert").allowed
        assert not node_policy_service.decision(contract.kind, "evaluation").allowed
        assert not node_policy_service.decision(contract.kind, "evolution").allowed

    assert task.execution.can_wait is False
    assert task.execution.idempotent is True
    assert task.ports[-1].value_schema.type == "object"
    assert handoff.execution.can_wait is True
    assert router.execution.can_wait is True
    assert handoff.ports[-1].value_schema.type == "object"
    assert router.ports[-1].value_schema.type == "object"


def test_r21_safe_text_contract_is_complete_scoped_and_not_plannable() -> None:
    contract = workflow_node_contract_registry.require("code")

    assert contract.contract_status == "complete"
    assert contract.config_schema["properties"]["contractVersion"] == {"const": 2}
    assert contract.config_schema["properties"]["operation"]["enum"] == [
        "upper",
        "lower",
        "replace",
        "concat",
    ]
    validator = Draft202012Validator(contract.config_schema)
    assert not list(validator.iter_errors(contract.planner.default_data))
    for invalid in (
        {**contract.planner.default_data, "operation": " upper "},
        {**contract.planner.default_data, "inputVariable": True},
        {**contract.planner.default_data, "outputVariable": " bad "},
    ):
        assert list(validator.iter_errors(invalid))
    for legacy_field in (
        "codeOperation",
        "codeInputVariable",
        "codeOutputVariable",
        "pythonCode",
    ):
        invalid = {**contract.planner.default_data, legacy_field: "legacy"}
        assert list(validator.iter_errors(invalid)), legacy_field
    assert contract.execution.side_effect == "none"
    assert contract.execution.deterministic is True
    assert contract.execution.idempotent is True
    assert contract.execution.can_wait is False
    assert contract.execution.error_semantics == "fail_closed"
    assert contract.planner.enabled is False
    for context in ("workflow", "xpert", "goal", "handoff", "app", "evaluation"):
        assert node_policy_service.decision("code", context).allowed, context
    assert not node_policy_service.decision("code", "evolution").allowed

    retired = workflow_node_contract_registry.require("template_transform")
    assert retired.deprecated is True
    assert retired.replacement_kind == "variable_assign"


def test_r20_human_mcp_and_variable_contracts_are_complete_and_not_plannable() -> None:
    for kind in {"human_intervention", "mcp_tool", "variable_assign"}:
        contract = workflow_node_contract_registry.require(kind)
        assert contract.contract_status == "complete"
        assert contract.planner.enabled is False
        assert node_policy_service.decision(kind, "workflow").allowed
        assert node_policy_service.decision(kind, "xpert").allowed

    human = workflow_node_contract_registry.require("human_intervention")
    mcp = workflow_node_contract_registry.require("mcp_tool")
    variable = workflow_node_contract_registry.require("variable_assign")
    assert human.execution.can_wait is True
    assert human.execution.external_io is True
    assert mcp.execution.can_wait is True
    assert mcp.execution.external_io is True
    assert mcp.execution.idempotent is False
    assert variable.execution.side_effect == "none"
    assert variable.execution.deterministic is True
    assert not node_policy_service.decision("human_intervention", "app").allowed
    assert not node_policy_service.decision("mcp_tool", "app").allowed
    assert not node_policy_service.decision("mcp_tool", "evaluation").allowed
    legacy_citation = workflow_node_contract_registry.require("knowledge_citation")
    assert legacy_citation.deprecated is True
    assert legacy_citation.replacement_kind == "knowledge_retrieval"


def test_r19_typed_ai_contracts_are_complete_and_not_plannable() -> None:
    extractor = workflow_node_contract_registry.require("parameter_extractor")
    classifier = workflow_node_contract_registry.require("question_classifier")

    for contract in (extractor, classifier):
        assert contract.contract_status == "complete"
        assert contract.execution.external_io is True
        assert contract.execution.error_semantics == "fail_closed"
        assert contract.planner.enabled is False
        assert node_policy_service.decision(contract.kind, "workflow").allowed
        assert node_policy_service.decision(contract.kind, "xpert").allowed
    assert set(classifier.edge.allowed_source_handles) == {
        "category_1",
        "category_2",
        "category_3",
        "category_4",
        "category_5",
        "category_6",
        "category_7",
        "category_8",
        "default",
    }
    legacy_configs = {
        "parameter_extractor": {
            "inputVariable": "user_input",
            "schema": "topic: Topic",
            "modelId": "test/model",
            "outputVariable": "parameters_json",
        },
        "question_classifier": {
            "inputVariable": "user_input",
            "categories": '{"Support":["help"],"Sales":["buy"]}',
            "outputVariable": "category",
        },
    }
    for kind, config in legacy_configs.items():
        validator = Draft202012Validator(
            workflow_node_contract_registry.require(kind).config_schema
        )
        assert not list(validator.iter_errors(config))
        for invalid_version in ("2", 3):
            assert list(
                validator.iter_errors(
                    {**config, "contractVersion": invalid_version}
                )
            )


@pytest.mark.parametrize(
    ("kind", "legacy_shaped_v2_config"),
    [
        (
            "document_extractor",
            {
                "contractVersion": 2,
                "sourcePathVariable": "source_path",
                "outputVariable": "document_text",
            },
        ),
        (
            "time_tool",
            {
                "contractVersion": 2,
                "operation": "now_iso",
                "outputVariable": "time_result",
            },
        ),
    ],
)
def test_r18_v2_configs_cannot_match_legacy_contract_branches(
    kind: str,
    legacy_shaped_v2_config: dict,
) -> None:
    contract = workflow_node_contract_registry.require(kind)

    errors = list(
        Draft202012Validator(contract.config_schema).iter_errors(
            legacy_shaped_v2_config
        )
    )

    assert errors


def test_only_current_seven_nodes_have_valid_planner_contracts() -> None:
    available = {
        kind
        for kind in workflow_node_contract_registry.kinds()
        if planner_capability_metadata(kind) is not None
    }

    assert available == EXPECTED_PLANNER_KINDS
    assert available == set(META_PLANNER_COMPILABLE_NODE_KINDS)
    for kind in available:
        contract = workflow_node_contract_registry.require(kind)
        metadata = planner_capability_metadata(kind)
        assert metadata is not None
        assert contract.contract_status == "complete"
        assert metadata["contract_checksum"] == contract.checksum
        assert metadata["compiler_checksum"] == contract.compiler_checksum


def test_workflow_agent_adapter_round_trip_is_stable() -> None:
    adapter = get_planner_node_adapter("workflow_agent")
    assert adapter is not None
    contract = workflow_node_contract_registry.require("workflow_agent")
    assert adapter.config_schema_checksum == canonical_checksum(
        contract.planner.ir_config_schema
    )
    ir_node = MetaPlannerIRNode(
        ref="researcher",
        kind="workflow_agent",
        title="Researcher",
        description="Find grounded evidence.",
        task_ids=["research"],
        inputs=[
            MetaPlannerIRInputBinding(
                port="request", variable="user_input", value_type="string"
            )
        ],
        outputs=[
            MetaPlannerIROutputBinding(
                port="result", variable="research_output", value_type="string"
            )
        ],
        config=MetaPlannerWorkflowAgentConfig(
            role_prompt="Find grounded evidence.",
            task_input="{{user_input}}",
            model_id="model/agent",
        ).model_dump(mode="json"),
    )
    context = PlannerNodeCompileContext(
        node_id="planner-researcher",
        position=WorkflowPosition(x=320, y=180),
        default_agent_model_id="model/default",
        output_variable="research_output",
        acceptance_criteria="Cite the evidence.",
        has_runtime_resources=True,
        requires_runtime_mode=False,
    )

    compiled = adapter.compile_node(ir_node, adapter.validate_config(ir_node), context)
    recovered = decompile_planner_node(compiled)
    recompiled = adapter.compile_node(
        recovered, adapter.validate_config(recovered), context
    )

    assert recovered == ir_node
    assert recompiled.model_dump(mode="json") == compiled.model_dump(mode="json")


def test_policy_service_preserves_current_entrypoint_boundaries() -> None:
    independent_deployment_kinds = {
        "scheduled_start",
        "http_event_entry",
        "form_event_entry",
        "failure_event_entry",
        "workflow_call_entry",
        "invoke_workflow",
        "suspend_wait",
        "http_event_reply",
    }
    evaluation_denied = {
        "agent_task",
        "agent_handoff",
        "handoff_router",
        "human_intervention",
        "mcp_tool",
        "vision_understanding",
        "data_table_query",
        "data_table_insert",
        "data_table_update",
        "data_table_delete",
        "scheduled_start",
        "http_event_entry",
        "form_event_entry",
        "failure_event_entry",
        "workflow_call_entry",
        "invoke_workflow",
        "suspend_wait",
        "http_event_reply",
        "http_request",
        "document_extractor",
        "file_output",
    }
    app_denied = {
        "external_xpert",
        "plugin_resource",
        "human_intervention",
        "mcp_tool",
        "vision_understanding",
        "data_table_query",
        "data_table_insert",
        "data_table_update",
        "data_table_delete",
        "scheduled_start",
        "http_event_entry",
        "form_event_entry",
        "failure_event_entry",
        "workflow_call_entry",
        "invoke_workflow",
        "suspend_wait",
        "http_event_reply",
        "http_request",
        "document_extractor",
        "file_output",
    }

    assert {
        kind
        for kind in workflow_node_contract_registry.kinds()
        if not node_policy_service.decision(kind, "evaluation").allowed
    } == evaluation_denied
    assert {
        kind
        for kind in workflow_node_contract_registry.kinds()
        if not node_policy_service.decision(kind, "app").allowed
    } == app_denied
    assert all(
        not node_policy_service.decision(kind, "xpert").allowed
        for kind in independent_deployment_kinds
    )
    assert node_policy_service.evolution_control_kinds() == {"workflow_agent"}
    assert not node_policy_service.decision("not-a-node", "app").allowed


def test_runtime_middleware_contract_preserves_linear_and_binding_edges() -> None:
    edge = workflow_node_contract_registry.require("runtime_middleware").edge

    assert edge.modes == ("control", "binding")
    assert edge.topology_modes == ("control",)
    assert edge.supports("control")
    assert edge.supports("binding")
    assert not edge.supports("metadata")


def test_old_capability_snapshot_remains_readable() -> None:
    snapshot = MetaPlannerCapabilitySnapshot.model_validate(
        {
            "version": "evoagentx-meta-planner-capabilities-v2",
            "snapshot_hash": "legacy-hash",
            "generated_at": 1,
            "node_registry_version": "xpert-workflow-node-registry-v2",
            "nodes": [],
            "middleware": [],
            "external_xperts": [],
            "knowledge_bases": [],
            "toolsets": [],
            "plugins": [],
            "prompt_profiles": [],
            "models": [],
            "default_scope": {},
        }
    )

    assert snapshot.ir_version == 2
    assert snapshot.contract_version == 2
    assert snapshot.contract_checksum == ""


def test_registry_ui_projection_is_v4_and_contains_no_runtime_payloads() -> None:
    payload = workflow_node_registry.to_payload()
    serialized = str(payload).lower()
    items = [
        item
        for section in payload["sections"]
        for item in section["items"]
    ] + list(payload["knowledge_pipeline"]["items"])

    assert len({item["kind"] for item in items}) == 48
    assert payload["version"] == "xpert-workflow-node-registry-v4"
    assert payload["contract_version"] == 3
    assert payload["contract_checksum"] == workflow_node_contract_registry.checksum
    assert all(item["contract"]["kind"] == item["kind"] for item in items)
    assert all(item["planner"]["contract_checksum"] for item in items)
    assert "credentialid" in serialized
    assert "masked_value" not in serialized
    assert "encrypted_value" not in serialized
    assert "secret_value" not in serialized
    assert "embedding" not in serialized
