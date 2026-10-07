"""Normative regressions for the CW10 full-chain audit, with no Provider calls."""
from itertools import permutations

import pytest

from server.meta_agent.control_flow import ControlFlowAnalysisError, analyze_control_flow
from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent, resolve_graph_intent,
    resolve_node_resource_snapshot, workflow_semantic_checksum,
)
from server.meta_agent.meta_planner_v2 import compile_xpert_candidate, validate_blueprint_authorization
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentControlEdgeV3, GraphIntentNodeV3
from server.tests.test_meta_planner_control_flow import (
    _agent, _bind_parsed_router_input, _branch_intent, _condition, _input,
    _query_null_branch,
)
from server.tests.test_meta_planner_read_resources import _plan, _request, _snapshot
from server.workflow_native.control_data import evaluate_typed_condition
from server.workflow_native.node_contracts import WorkflowValueSchema


def guarded_graph(*, root_nullable=False, field_nullable=False, guard=None, required=True):
    schema = WorkflowValueSchema(
        type="object", nullable=root_nullable,
        properties={"score": WorkflowValueSchema(type="number", nullable=field_nullable)},
        required=("score",) if required else (),
    )
    graph = _branch_intent()
    graph.nodes[0].config = {"field": "score", "operator": "lt", "value_type": "number", "value": 60}
    _bind_parsed_router_input(graph, schema)
    if guard:
        graph.nodes.extend([
            GraphIntentNodeV3(ref="guard", kind="condition", title="空值保护",
                inputs=[_input("value", "parsed_value", "parse", "value", schema)],
                config={"field": "score" if guard == "field" else "", "operator": "is_null"}),
            GraphIntentNodeV3(ref="stop", kind="terminate_error", title="输入缺失",
                config={"error_code": "MISSING_VALUE", "message": "输入缺失。"}),
        ])
        graph.control_edges[0].target_ref = "guard"
        graph.control_edges.extend([
            GraphIntentControlEdgeV3(source_ref="guard", outcome_ref="matched", target_ref="stop"),
            GraphIntentControlEdgeV3(source_ref="guard", outcome_ref="unmatched", target_ref="router"),
        ])
    return graph


def roundtrip(graph, snapshot=None):
    snapshot = snapshot or _snapshot()
    request = _request(snapshot)
    assert validate_blueprint_authorization(request, _plan(), graph, snapshot) == []
    candidate = compile_xpert_candidate(request=request, plan=_plan(), blueprint=graph, snapshot=snapshot, target=None)
    restored = decompile_candidate_to_graph_intent(candidate)
    rebuilt = compile_xpert_candidate(request=request, plan=_plan(), blueprint=restored, snapshot=snapshot, target=None)
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(rebuilt)
    return resolve_graph_intent(graph, snapshot).graph_checksum


@pytest.mark.parametrize("options", [
    {"root_nullable": True}, {"field_nullable": True}, {"required": False},
    {"root_nullable": True, "field_nullable": True, "guard": "root"},
    {"root_nullable": True, "field_nullable": True, "guard": "field"},
])
def test_unsafe_domain_is_rejected_not_sampled_away(options):
    with pytest.raises(ControlFlowAnalysisError):
        analyze_control_flow(guarded_graph(**options))


@pytest.mark.parametrize("options", [
    {"root_nullable": True, "guard": "root"},
    {"field_nullable": True, "guard": "field"},
])
def test_guarded_domains_use_path_facts_and_roundtrip(options):
    graph = guarded_graph(**options)
    assert analyze_control_flow(graph)["scenario_count"] == 3
    roundtrip(graph)


def test_nullable_scalar_guard():
    graph = guarded_graph(field_nullable=True, guard="field")
    schema = WorkflowValueSchema(type="number", nullable=True)
    graph.nodes[0].outputs[0].value_schema = schema
    graph.nodes[0].config["expected_schema"] = schema.model_dump(mode="json")
    for node in graph.nodes:
        if node.kind == "condition":
            node.inputs[0].value_schema = schema
            node.config["field"] = ""
    assert analyze_control_flow(graph)["scenario_count"] == 3
    roundtrip(graph)


def test_shared_source_complementary_conditions_have_two_not_four_paths():
    graph = _branch_intent()
    opposite = _condition("opposite")
    opposite.config["operator"] = "not_equals"
    graph.nodes.append(opposite)
    graph.control_edges.extend([
        GraphIntentControlEdgeV3(source_ref="opposite", outcome_ref="matched", target_ref="rejected"),
        GraphIntentControlEdgeV3(source_ref="opposite", outcome_ref="unmatched", target_ref="approved"),
    ])
    assert analyze_control_flow(graph)["scenario_count"] == 2
    roundtrip(graph)


def test_guard_on_other_source_does_not_prove_safety():
    graph = guarded_graph(root_nullable=True, guard="root")
    parse = graph.nodes[0].model_copy(deep=True)
    parse.ref = "other_parse"
    parse.outputs[0].variable = "other_value"
    graph.nodes.append(parse)
    guard = next(node for node in graph.nodes if node.ref == "guard")
    guard.inputs[0].source_ref = parse.ref
    guard.inputs[0].variable = "other_value"
    graph.control_edges.append(GraphIntentControlEdgeV3(source_ref=parse.ref, target_ref="guard"))
    with pytest.raises(ControlFlowAnalysisError):
        analyze_control_flow(graph)


def test_node_and_edge_display_order_does_not_change_semantics():
    graph = guarded_graph(root_nullable=True, guard="root")
    expected = roundtrip(graph)
    for indices in list(permutations(range(3))):
        changed = graph.model_copy(deep=True)
        changed.nodes = [graph.nodes[index] for index in indices] + graph.nodes[3:]
        changed.control_edges.reverse()
        assert roundtrip(changed) == expected


def test_authorized_table_type_not_model_repetition_controls_proof():
    graph, snapshot = _query_null_branch()
    query = graph.nodes[0]
    for item in snapshot.data_tables[0]["schema_versions"][0]["fields"]:
        if item["name"] == "score":
            item["required"] = True
    adapter = get_planner_node_adapter(query.kind)
    resource = resolve_node_resource_snapshot(query, snapshot)
    schema = adapter.authoritative_output_schema("result", adapter.validate_intent_node(query), resource.model_dump(mode="json"))
    # A resource declaration is descriptive, not a replacement for the Store Schema.
    broad = WorkflowValueSchema(type="object", nullable=True)
    query.outputs[0].value_schema = broad
    for node in graph.nodes:
        for binding in node.inputs:
            if binding.source_ref == query.ref:
                binding.value_schema = broad
    graph.nodes.append(GraphIntentNodeV3(ref="score_gate", kind="condition", title="分数门禁",
        inputs=[_input("value", "query_result", query.ref, schema=broad)],
        config={"field": "score", "operator": "lt", "value_type": "number", "value": 60}))
    graph.control_edges[2].target_ref = "score_gate"
    graph.control_edges.extend([
        GraphIntentControlEdgeV3(source_ref="score_gate", outcome_ref="matched", target_ref="encode"),
        GraphIntentControlEdgeV3(source_ref="score_gate", outcome_ref="unmatched", target_ref="encode"),
    ])
    assert schema.properties["score"].nullable is False
    roundtrip(graph, snapshot)


@pytest.mark.parametrize("operator", ["lt", "lte", "gt", "gte"])
@pytest.mark.parametrize("boundary", [-0.5, 0, 0.00001, 60, 1000000000])
def test_proven_numeric_paths_cover_runtime_boundary_values(operator, boundary):
    graph = guarded_graph(field_nullable=True, guard="field")
    router = next(node for node in graph.nodes if node.ref == "router")
    router.config.update(operator=operator, value=boundary)
    report = analyze_control_flow(graph)
    expected_paths = {tuple(sorted(item["choices"].items())) for item in report["scenarios"]}
    for value in (None, boundary - 1, boundary, boundary + 1):
        choices = {"guard": "matched" if value is None else "unmatched"}
        if value is not None:
            matched = evaluate_typed_condition({"score": value}, field="score", operator=operator,
                value_type="number", expected=boundary)
            choices["router"] = "matched" if matched else "unmatched"
        assert tuple(sorted(choices.items())) in expected_paths
