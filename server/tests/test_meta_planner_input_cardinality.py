"""Generation/Adapter conformance, not real-model success-rate evidence."""
from copy import deepcopy

from jsonschema import Draft202012Validator
import pytest

from server.meta_agent.generation_contract import graph_generation_schema
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentNodeV3
from server.tests.test_meta_planner_controlled_writes import request, snapshot


KINDS = (
    "json_serialize", "json_deserialize", "variable_aggregator",
    "data_aggregate", "dataset_compare", "condition", "data_merge",
)


def local_node(kind):
    adapter = get_planner_node_adapter(kind)
    config = adapter.default_intent_config()
    parsed = adapter.config_model.model_validate(config)
    node = GraphIntentNodeV3(
        ref="transform", kind=kind, title="合成输入契约检查", config=config,
        inputs=[{
            "port": port.name, "variable": f"source_{index}",
            "source_ref": f"producer_{index}", "source_port": "result",
            "value_schema": port.value_schema,
        } for index, port in enumerate(adapter.intent_port_contracts("input"))],
        outputs=[{
            "port": port.name, "variable": f"output_{port.name}",
            "value_schema": adapter.authoritative_output_schema(port.name, parsed),
        } for port in adapter.intent_port_contracts("output")],
    )
    return adapter, node


@pytest.fixture(scope="module")
def model_schema():
    snap = snapshot()
    return graph_generation_schema(request(snap), snap)


def accepts(schema, node):
    validator = Draft202012Validator({
        "$defs": schema["$defs"], "$ref": "#/$defs/ModelNode_" + node["kind"],
    })
    return not list(validator.iter_errors(node))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mutation", ["omitted", "empty"])
def test_missing_required_inputs_are_rejected_by_model_schema_and_adapter(model_schema, kind, mutation):
    adapter, node = local_node(kind)
    adapter.validate_intent_node(node)
    raw = node.model_dump(mode="json")
    assert accepts(model_schema, raw)
    if mutation == "omitted":
        raw.pop("inputs")
    else:
        raw["inputs"] = []
    with pytest.raises(ValueError):
        adapter.validate_intent_node(GraphIntentNodeV3.model_validate(raw))
    assert not accepts(model_schema, raw)


@pytest.mark.parametrize("kind", [kind for kind in KINDS if kind != "variable_aggregator"])
def test_duplicate_single_input_is_rejected_on_both_sides(model_schema, kind):
    adapter, node = local_node(kind)
    raw = node.model_dump(mode="json")
    raw["inputs"].append(deepcopy(raw["inputs"][0]))
    with pytest.raises(ValueError):
        adapter.validate_intent_node(GraphIntentNodeV3.model_validate(raw))
    assert not accepts(model_schema, raw)


@pytest.mark.parametrize("kind", KINDS)
def test_resolved_input_requirements_match_valid_adapter_shape(kind):
    adapter, node = local_node(kind)
    parsed = adapter.validate_intent_node(node)
    counts = adapter.input_port_counts(parsed)
    assert set(counts) == {item.port for item in node.inputs}
    for port, (minimum, maximum) in counts.items():
        actual = sum(item.port == port for item in node.inputs)
        assert minimum <= actual and (maximum is None or actual <= maximum)
    if kind == "variable_aggregator":
        assert counts == {"values": (len(parsed.output_fields), len(parsed.output_fields))}


def test_literals_can_still_have_no_inputs_and_many_agent_inputs_stay_legal(model_schema):
    from server.tests.test_meta_planner_controlled_writes import intent

    graph = intent("insert")
    raw = graph.nodes[0].model_dump(mode="json")
    raw.pop("inputs")
    assert accepts(model_schema, raw)
    agent = graph.nodes[-1]
    duplicate = agent.inputs[0].model_copy(update={"variable": "another_value", "source_ref": "another"})
    agent.inputs.append(duplicate)
    assert accepts(model_schema, agent.model_dump(mode="json"))
    adapter = get_planner_node_adapter("workflow_agent")
    assert adapter.input_port_counts(adapter.config_model.model_validate(agent.config)) == {"task": (1, None)}


def test_aggregator_counts_follow_final_field_mapping_not_a_fixed_one_input_rule():
    adapter, node = local_node("variable_aggregator")
    node.config["output_fields"] = ["first", "second"]
    parsed = adapter.config_model.model_validate(node.config)
    state = adapter.input_binding_state(node, parsed)
    assert state["ports"][0] == {
        "port": "values", "minimum": 2, "maximum": 2, "actual": 1, "input_indices": [0],
    }
    assert state["valid"] is False
    node.inputs.append(node.inputs[0].model_copy(update={"variable": "second_source", "source_ref": "second"}))
    adapter.validate_intent_node(node)
    assert adapter.input_binding_state(node, parsed)["valid"] is True


def test_count_evidence_never_echoes_unknown_ports_or_business_values():
    import json
    from server.meta_agent.generation_diagnostics import GraphPatchProgress, GenerationDiagnostics
    from server.meta_agent.schemas import GraphIntentV3

    adapter, node = local_node("json_serialize")
    node.inputs[0].port = "sk-private-canary"
    node.inputs[0].variable = "private_variable_canary"
    node.description = "private description canary"
    state = adapter.input_binding_state(node, adapter.config_model.model_validate(node.config))
    assert state["unexpected_input_indices"] == [0]
    assert state["valid"] is False
    progress = GraphPatchProgress()
    progress.record_node_change("update_node", node, node)
    graph = GraphIntentV3(name="合成诊断", nodes=[node], final_output={"sources": [{"node_ref": node.ref}]})
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.bind_graph(graph)
    serialized = json.dumps([state, progress.node_changes, diagnostics.as_dict()])
    for private in ("sk-private-canary", "private_variable_canary", "private description canary"):
        assert private not in serialized
    assert progress.node_changes[0]["after"]["input_shape_valid"] is False
