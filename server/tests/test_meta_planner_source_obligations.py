from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.meta_agent.schemas import GraphIntentNodeV3
from server.tests.test_meta_planner_control_flow import _input
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, intent, request, snapshot
from server.tests.test_meta_planner_repair_context import offline_only


def bad_values(*, port="value"):
    snap, graph = snapshot(), intent("update")
    req = request(snap, "update")
    query, writer, encoder, answer = graph.nodes
    value = GraphIntentNodeV3(ref="write_values", kind="json_serialize", title="无效写值来源",
        config={"format": "compact"}, inputs=[_input("value", "user_input", "input", "user_input")],
        outputs=[{"port": "json", "variable": "values_json", "value_schema": {"type": "string"}}])
    writer.config.update(value_source="input", values=None)
    writer.inputs.append(_input("values", "values_json", value.ref, port, schema={"type": "object"}))
    graph.nodes.insert(1, value)
    next(edge for edge in graph.control_edges if edge.target_ref == "write").target_ref = value.ref
    graph.control_edges.append(type(graph.control_edges[0])(source_ref=value.ref, target_ref="write"))
    return req, snap, graph


def facts(req, snap, graph):
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.bind_graph(graph)
    issues = validate_blueprint_authorization(req, _plan(), graph, snap, diagnostics=diagnostics)
    payload = json.loads(MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, graph, issues))
    return issues, diagnostics.as_dict(), payload


@pytest.mark.parametrize("mutation,code", [
    ("port", "DATA_UNKNOWN_SOURCE_PORT"), ("ref", "DATA_UNKNOWN_SOURCE_REF"),
    ("variable", "DATA_SOURCE_VARIABLE_MISMATCH"), ("duplicate", "DATA_AMBIGUOUS_SOURCE_PORT"),
])
def test_source_integrity_is_independent_of_types_and_control_proof(mutation, code):
    req, snap, graph = bad_values(port="json")
    writer = next(node for node in graph.nodes if node.ref == "write")
    binding = writer.inputs[-1]
    if mutation == "port":
        binding.source_port = "value"
    elif mutation == "ref":
        binding.source_ref = "missing"
    elif mutation == "variable":
        binding.variable = "rows"  # A real variable, but not from this ref/port.
    else:
        producer = next(node for node in graph.nodes if node.ref == "write_values")
        producer.outputs.append(producer.outputs[0].model_copy(deep=True))
    before = graph.model_dump(mode="json")
    issues, diagnostics, payload = facts(req, snap, graph)
    assert issues
    assert any(item["code"] == code and item.get("node_ref") == "write" for item in diagnostics["issues"])
    detail = next(item for item in payload["repair_contract"]["source_contract_issues"] if item["code"] == code)
    assert detail["node_ref"] == "write" and detail["input_index"] == 1
    assert "schema" not in json.dumps(detail).lower()
    with pytest.raises(ValueError):
        compile_case(graph, snap, req)
    assert graph.model_dump(mode="json") == before


def test_port_fix_does_not_hide_invalid_write_value_producer():
    for port in ("value", "json"):
        req, snap, graph = bad_values(port=port)
        issues, _, payload = facts(req, snap, graph)
        assert any("JSON Deserialize V2" in item for item in issues)
        detail = next(item for item in payload["repair_contract"]["source_contract_issues"]
                      if item["code"] == "WRITE_VALUES_PRODUCER_INVALID")
        assert detail["source_ref"] == "write_values"
        assert detail["required_producer_kind"] == "json_deserialize"
        with pytest.raises(ValueError):
            compile_case(graph, snap, req)


def test_denied_resource_does_not_invent_type_facts_or_suppress_source_error():
    req, snap, graph = bad_values()
    req.scope.data_table_ids = []
    req.scope.data_table_write_grants = []
    issues, _, payload = facts(req, snap, graph)
    assert any("not authorized" in item or "未获" in item for item in issues)
    assert payload["repair_contract"]["data_contract_issues"] == []
    assert any(item["code"] == "DATA_UNKNOWN_SOURCE_PORT" for item in payload["repair_contract"]["source_contract_issues"])
    assert payload["repair_contract"]["resolved_resource_inputs"] == []


def test_legal_compiler_input_and_roundtrip_are_unchanged():
    snap, graph = snapshot(), intent("update")
    req = request(snap, "update")
    issues, _, payload = facts(req, snap, graph)
    assert issues == []
    assert payload["repair_contract"]["source_contract_issues"] == []
    assert compile_case(graph, snap, req)
