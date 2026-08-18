from __future__ import annotations

from typing import get_args

import pytest

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
    "variable_aggregator",
    "variable_assign",
}
PROMOTED_COMPLETE_KINDS = {"llm"}


def test_contract_registry_covers_every_native_kind_once() -> None:
    expected = set(get_args(NativeNodeKind))

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
        "suspend_wait",
        "http_event_reply",
    }
    evaluation_denied = {
        "agent_handoff",
        "handoff_router",
        "human_intervention",
        "vision_understanding",
        "data_table_query",
        "data_table_insert",
        "data_table_update",
        "data_table_delete",
        "scheduled_start",
        "http_event_entry",
        "suspend_wait",
        "http_event_reply",
    }
    app_denied = {
        "external_xpert",
        "plugin_resource",
        "human_intervention",
        "vision_understanding",
        "data_table_query",
        "data_table_insert",
        "data_table_update",
        "data_table_delete",
        "scheduled_start",
        "http_event_entry",
        "suspend_wait",
        "http_event_reply",
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

    assert payload["version"] == "xpert-workflow-node-registry-v4"
    assert payload["contract_version"] == 3
    assert payload["contract_checksum"] == workflow_node_contract_registry.checksum
    assert all(item["contract"]["kind"] == item["kind"] for item in items)
    assert all(item["planner"]["contract_checksum"] for item in items)
    assert "credential" not in serialized
    assert "api_key" not in serialized
    assert "embedding" not in serialized
