"""Frozen compiler counterexamples, not evidence of model generation quality."""
from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from server.meta_agent.generation_recipe import lower_generation_recipe, recipe_schema
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, workflow_semantic_checksum
from server.meta_agent.meta_planner_v2 import validate_blueprint_authorization
from server.tests.meta_planner_recipe_fixtures import from_intent, step
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, intent, request, snapshot
from server.tests.test_meta_planner_control_flow import _branch_intent, _merge_intent, _route_error_intent


def edge_set(graph):
    return {(edge.source_ref, edge.outcome_ref, edge.target_ref) for edge in graph.control_edges}


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_write_recipe_derives_authority_and_roundtrips(operation):
    snap = snapshot()
    req = request(snap, operation)
    original = intent(operation)
    raw = from_intent(original)
    schema = recipe_schema(req, snap)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(raw)
    before = deepcopy(raw)
    graph = lower_generation_recipe(raw, req, snap)
    assert raw == before
    assert edge_set(graph) == edge_set(original)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    outputs = {(node.ref, output.port): output for node in graph.nodes for output in node.outputs}
    for node in graph.nodes:
        for binding in node.inputs:
            if binding.source_ref != "input":
                output = outputs[binding.source_ref, binding.source_port]
                assert binding.variable == output.variable
                assert binding.value_schema == output.value_schema
    write_schema = outputs["write", "result"].value_schema
    assert "record_id" in write_schema.properties if operation == "insert" else set(write_schema.properties) == {"matched", "affected"}
    candidate = compile_case(graph, snap, req)
    rebuilt = compile_case(decompile_candidate_to_graph_intent(candidate), snap, req)
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(rebuilt)


@pytest.mark.parametrize("case", ["condition", "route", "parallel"])
def test_structure_preserves_explicit_non_linear_edges(case):
    if case == "condition":
        original = _branch_intent()
        flow = [step("router", {"matched": [step("approved")], "unmatched": [step("rejected")]})]
    elif case == "route":
        original = _route_error_intent()
        flow = [step("router", {"case_1": [step("approved")], "case_2": [step("rejected")], "default": [step("stop")]})]
    else:
        original = _merge_intent()
        flow = [{"type": "parallel", "paths": [[step("left_rows")], [step("right_rows")]]}, step("merge"), step("encode"), step("answer")]
    snap = snapshot()
    req = request(snap)
    graph = lower_generation_recipe(from_intent(original, flow), req, snap)
    assert edge_set(graph) == edge_set(original)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    compile_case(graph, snap, req)


@pytest.mark.parametrize("mutation", ["variable", "schema", "outputs", "control_edges", "handle", "version", "config_variable", "outcome", "ref"])
def test_recipe_rejects_mechanical_and_authority_injection(mutation):
    raw = from_intent(intent("update"))
    if mutation == "variable":
        raw["nodes"][1]["inputs"][0]["variable"] = "forged"
    elif mutation == "schema":
        raw["nodes"][1]["inputs"][0]["value_schema"] = {"type": "string"}
    elif mutation == "outputs":
        raw["nodes"][0]["outputs"] = []
    elif mutation == "control_edges":
        raw["control_edges"] = []
    elif mutation == "handle":
        raw["control_flow"][0]["targetHandle"] = "records"
    elif mutation == "version":
        raw["nodes"][0]["resource_ref"]["schema_version"] = 1
    elif mutation == "config_variable":
        raw["nodes"][0]["config"]["outputVariable"] = "forged"
    elif mutation == "outcome":
        raw["control_flow"][0]["branches"] = [{"outcome_ref": "route_1", "steps": []}]
    else:
        raw["nodes"][0]["ref"] = "input"
    snap = snapshot()
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, request(snap, "update"), snap)


@pytest.mark.parametrize("mutation", ["duplicate", "omitted", "missing_branch", "empty_terminal", "extra_branch", "after_fatal", "empty_parallel", "unbound_prompt", "property_prompt"])
def test_recipe_never_guesses_missing_semantics(mutation):
    raw = from_intent(_route_error_intent(), [step("router", {
        "case_1": [step("approved")], "case_2": [step("rejected")], "default": [step("stop")],
    })])
    if mutation == "duplicate":
        raw["control_flow"].append(step("approved"))
    elif mutation == "omitted":
        raw["nodes"].append({"ref": "extra", "kind": "json_serialize", "title": "遗漏", "config": {}})
    elif mutation == "missing_branch":
        raw["control_flow"][0]["branches"].pop()
    elif mutation == "empty_terminal":
        raw["control_flow"][0]["branches"][0]["steps"] = []
        raw["nodes"] = [node for node in raw["nodes"] if node["ref"] != "approved"]
    elif mutation == "extra_branch":
        raw["control_flow"][0]["branches"].append({"outcome_ref": "success", "steps": []})
    elif mutation == "after_fatal":
        raw["control_flow"][0]["branches"][-1]["steps"].append(step("approved"))
    elif mutation == "empty_parallel":
        raw["control_flow"].insert(0, {"type": "parallel", "paths": [[], []]})
    else:
        raw["nodes"][1]["config"]["task_input"] = "{{other.result}}" if mutation == "unbound_prompt" else "{{input.user_input.secret}}"
    snap = snapshot()
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, request(snap), snap)


def test_source_type_is_not_narrowed_to_consumer_requirement():
    snap = snapshot()
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    raw["nodes"][-1]["inputs"] = [{"port": "task", "source_ref": "lookup", "source_port": "result"}]
    raw["nodes"][-1]["config"]["task_input"] = "{{lookup.result}}"
    graph = lower_generation_recipe(raw, req, snap)
    assert graph.nodes[-1].inputs[0].value_schema.type == "array"
    with pytest.raises(ValueError):
        compile_case(graph, snap, req)


def test_determinism_and_scope_do_not_expand():
    snap = snapshot()
    req = request(snap)
    raw = from_intent(intent())
    original = lower_generation_recipe(raw, req, snap)
    raw["nodes"].reverse()
    req.scope.allowed_node_kinds.reverse()
    snap.nodes.reverse()
    reordered = lower_generation_recipe(raw, req, snap)
    assert workflow_semantic_checksum(compile_case(original, snap, req)) == workflow_semantic_checksum(compile_case(reordered, snap, req))
    req.scope.data_table_write_grants = []
    with pytest.raises(ValueError, match="授权"):
        lower_generation_recipe(raw, req, snap)


def test_all_recipe_nodes_require_explicit_adapter_config():
    snap = snapshot()
    req = request(snap)
    schema = recipe_schema(req, snap)
    for name, node in schema["$defs"].items():
        if name.startswith("ModelNode_"):
            assert "config" in node["required"], name
    raw = from_intent(intent())
    raw["nodes"][0].pop("config")
    assert not Draft202012Validator(schema).is_valid(raw)
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, req, snap)


@pytest.mark.parametrize("wrong_type", [False, True])
def test_dynamic_predicate_validates_after_its_actual_inputs_exist(wrong_type):
    snap = snapshot()
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    query = raw["nodes"][0]
    query["config"]["filter"] = {"ref": "key", "field": "score" if wrong_type else "sku", "operator": "eq", "value_source": "input"}
    query["inputs"] = [{"port": "predicate_key", "source_ref": "input", "source_port": "user_input"}]
    if wrong_type:
        with pytest.raises(ValueError):
            graph = lower_generation_recipe(raw, req, snap)
            compile_case(graph, snap, req)
    else:
        graph = lower_generation_recipe(raw, req, snap)
        assert graph.nodes[0].inputs[0].value_schema.type == "string"
        compile_case(graph, snap, req)
