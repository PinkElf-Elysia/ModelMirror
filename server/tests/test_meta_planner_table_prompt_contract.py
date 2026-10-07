from __future__ import annotations

import json
from copy import deepcopy

import pytest

from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent, resolve_graph_intent, workflow_semantic_checksum,
)
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentNodeV3, GraphIntentV3
from server.tests.test_meta_planner_controlled_writes import (
    _plan, compile_case, intent, request, snapshot,
)


TABLE_KINDS = (
    "data_table_query", "data_table_insert", "data_table_update", "data_table_delete",
)
LITERAL_FILTER = {
    "ref": "selected", "field": "sku", "operator": "eq", "value": "DEMO",
}
INPUT_FILTER = {
    "ref": "selected", "field": "sku", "operator": "eq", "value_source": "input",
}
CONFIG_CASES = [
    ("data_table_query", {}, set()),
    ("data_table_query", {"filter": LITERAL_FILTER}, set()),
    ("data_table_query", {"filter": INPUT_FILTER}, {"predicate_selected"}),
    ("data_table_query", {"filter": {"kind": "group", "logic": "or", "items": [
        LITERAL_FILTER, {**INPUT_FILTER, "ref": "other"},
    ]}}, {"predicate_other"}),
    ("data_table_query", {"filter": {"ref": "empty", "field": "score", "operator": "is_null", "value_source": "none"}}, set()),
    ("data_table_insert", {"value_source": "literal", "values": {"sku": "DEMO"}}, set()),
    ("data_table_insert", {"value_source": "input"}, {"values"}),
    *[("data_table_update", {"filter": predicate, **values}, {"records"} | predicate_ports | value_ports)
      for predicate, predicate_ports in ((LITERAL_FILTER, set()), (INPUT_FILTER, {"predicate_selected"}))
      for values, value_ports in (({"value_source": "literal", "values": {"score": 5}}, set()),
                                 ({"value_source": "input"}, {"values"}))],
    ("data_table_delete", {"filter": LITERAL_FILTER}, {"records"}),
    ("data_table_delete", {"filter": INPUT_FILTER}, {"predicate_selected", "records"}),
]


def _contracts(stage="generate"):
    snap = snapshot()
    req = request(snap)
    req.scope.data_table_write_grants[0].operations = ["insert", "update", "delete"]
    if stage == "generate":
        raw = MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None)
    elif stage == "full_repair":
        raw = MetaPlannerV2Service._repair_prompt(req, _plan(), snap, "{}", ["invalid"])
    else:
        raw = MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, intent(), ["invalid"])
    prompt = json.loads(raw)
    return prompt["graph_intent_contract"]["node_roles"]["executable_node_contracts"]


def _declared_inputs(contract, config):
    rule = contract["input_binding_contract"]
    assert rule["matching"] == "exactly_once"
    result = set(rule["always"])
    if config.get("value_source", "input") == "input":
        result.update(rule["when_value_source_input"])

    def visit(item):
        if not item:
            return
        if item.get("kind") == "group":
            for child in item["items"]:
                visit(child)
        elif item.get("value_source", "literal") == "input":
            result.add(rule["filter_input_port_template"].format(ref=item["ref"]))

    visit(config.get("filter"))
    return result


def _node(kind, config, ports):
    return GraphIntentNodeV3(
        ref="tested_node", kind=kind, title="契约验证", config=deepcopy(config),
        resource_ref={"resource_id": "table-orders"},
        inputs=[{
            "port": port, "variable": f"value_{index}", "source_ref": f"source_{index}",
            "source_port": "result", "value_schema": {"type": "any"},
        } for index, port in enumerate(sorted(ports))],
        outputs=[{"port": "result", "variable": "result_value", "value_schema": {"type": "object"}}],
    )


@pytest.mark.parametrize("stage", ["generate", "full_repair", "patch_repair"])
def test_all_model_paths_share_exact_table_authoring_contract(stage):
    contracts = _contracts(stage)
    original = _contracts()
    for kind in TABLE_KINDS:
        contract = contracts[kind]
        assert contract == original[kind]
        assert "input_binding_contract" in contract
        # Registry family names are not usable concrete inputs in GraphIntent.
        assert not [port for port in contract["ports"] if port["direction"] == "input"]
        fields = get_planner_node_adapter(kind).config_model.model_fields
        assert contract["config_field_contract"] == {
            "allowed": sorted(fields),
            "required": sorted(name for name, field in fields.items() if field.is_required()),
        }
        rules = " ".join(contract["input_binding_contract"]["rules"])
        assert ("config.values" in rules) == (kind in {"data_table_insert", "data_table_update"})
        assert ("records 始终必填" in rules) == (kind in {"data_table_update", "data_table_delete"})
        assert ("predicate_{ref}" in rules) == (kind != "data_table_insert")
    assert "max_affected_rows" not in contracts["data_table_query"]["config_field_contract"]["allowed"]
    assert "filter" in contracts["data_table_update"]["config_field_contract"]["required"]


@pytest.mark.parametrize("kind,config,expected", CONFIG_CASES)
def test_model_declared_inputs_equal_adapter_for_every_table_mode(kind, config, expected):
    ports = _declared_inputs(_contracts()[kind], config)
    assert ports == expected
    adapter = get_planner_node_adapter(kind)
    node = _node(kind, config, ports)
    adapter.validate_intent_node(node)
    for port in ports:
        missing = node.model_copy(deep=True)
        missing.inputs = [item for item in missing.inputs if item.port != port]
        with pytest.raises(ValueError):
            adapter.validate_intent_node(missing)
        duplicate = node.model_copy(deep=True)
        duplicate.inputs.append(next(item for item in duplicate.inputs if item.port == port))
        with pytest.raises(ValueError):
            adapter.validate_intent_node(duplicate)
    for invented in ("predicate", "records_id", "insert_id", "values_extra"):
        with pytest.raises(ValueError):
            adapter.validate_intent_node(_node(kind, config, ports | {invented}))


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_output_description_matches_actual_resolved_schema(operation):
    snap = snapshot()
    graph = resolve_graph_intent(
        intent(operation), snap, default_agent_model_id="model/agent",
        data_table_write_grants=request(snap, operation).scope.data_table_write_grants,
    )
    node = next(node for node in graph.nodes if node.ref == "write")
    output = _contracts()[f"data_table_{operation}"]["output_binding_contract"]
    assert output["port"] == "result"
    assert output["field_projection_supported"] is False
    assert output["shape"] == ("record" if operation == "insert" else "affected_counts")
    schema = next(port.value_schema.model_dump(mode="json") for port in node.ports if port.direction == "output")
    assert schema["type"] == "object"
    if operation == "insert":
        assert {"record_id", "revision", "created_at", "updated_at", "sku", "score"} == set(schema["properties"])
        assert schema["nullable"] is False
    else:
        assert output["value_schema"] == schema
        assert set(schema["properties"]) == {"matched", "affected"}


@pytest.mark.parametrize("mutation", ["query_limit", "missing_filter", "bare_predicate", "invented_id"])
def test_r2_observed_violations_remain_rejected(mutation):
    graph = intent("update")
    if mutation == "query_limit":
        graph.nodes[0].config["max_affected_rows"] = 1
    elif mutation == "missing_filter":
        graph.nodes[1].config.pop("filter")
    elif mutation == "bare_predicate":
        graph.nodes[1].config["filter"] = deepcopy(INPUT_FILTER)
        graph.nodes[1].inputs.append(graph.nodes[1].inputs[0].model_copy(update={"port": "predicate"}))
    else:
        graph.nodes[1].inputs[0].variable = "insert_id"
    with pytest.raises(ValueError):
        snap = snapshot()
        compile_case(graph, snap, request(snap, "update"))


def test_contract_projection_is_metadata_only_and_authorization_scoped(monkeypatch):
    from server.data_tables.store import SQLiteAgentTableBackend

    def forbidden(*args, **kwargs):
        pytest.fail("模型契约投影不得读取或写入记录")

    for name in ("query_records", "execute_controlled_write", "create_record"):
        monkeypatch.setattr(SQLiteAgentTableBackend, name, forbidden)
    contracts = _contracts()
    serialized = json.dumps(contracts)
    for secret in ("secret-default", "must-not-leak", "table-orders"):
        assert secret not in serialized
    snap = snapshot()
    req = request(snap)
    req.scope.allowed_node_kinds = ["input", "output", "workflow_agent"]
    raw = MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None)
    visible = json.loads(raw)["graph_intent_contract"]["node_roles"]["executable_node_contracts"]
    assert set(visible) == {"workflow_agent"}


def test_ordered_three_write_chain_roundtrips_without_property_extraction(monkeypatch):
    from server.tests.test_meta_planner_write_generation_failures import _forbid_records

    _forbid_records(monkeypatch)
    snap = snapshot()
    req = request(snap)
    req.scope.data_table_write_grants[0].operations = ["insert", "update", "delete"]
    nodes = []
    for operation, write_ref, query_ref, source_ref in (
        ("insert", "insert_row", None, None),
        ("update", "update_row", "before_update", "before_update"),
        ("delete", "delete_row", "after_update", "after_update"),
    ):
        sample = intent(operation)
        if query_ref:
            query = sample.nodes[0].model_copy(deep=True)
            query.ref = query_ref
            query.config["filter"] = deepcopy(LITERAL_FILTER)
            query.outputs[0].variable = f"{query_ref}_rows"
            nodes.append(query)
        write = next(node for node in sample.nodes if node.ref == "write").model_copy(deep=True)
        write.ref = write_ref
        write.outputs[0].variable = f"{write_ref}_result"
        if source_ref:
            write.inputs[0].source_ref = source_ref
            write.inputs[0].variable = f"{source_ref}_rows"
        nodes.append(write)
    agent = intent().nodes[-1].model_copy(deep=True)
    agent.title = "汇总实际写入结果"
    agent.inputs = []
    agent.config["role_prompt"] = "仅根据三个写入节点的真实结果进行中文汇总，不虚构业务写入。"
    for write_ref in ("insert_row", "update_row", "delete_row"):
        serializer = intent().nodes[-2].model_copy(deep=True)
        serializer.ref = f"encode_{write_ref}"
        serializer.title = "序列化写入结果"
        serializer.inputs[0].source_ref = write_ref
        serializer.inputs[0].variable = f"{write_ref}_result"
        serializer.outputs[0].variable = f"{write_ref}_json"
        nodes.append(serializer)
        binding = intent().nodes[-1].inputs[0].model_copy(deep=True)
        binding.source_ref = serializer.ref
        binding.variable = serializer.outputs[0].variable
        agent.inputs.append(binding)
    agent.config["task_input"] = "\n".join("{{" + item.variable + "}}" for item in agent.inputs)
    nodes.append(agent)
    graph = GraphIntentV3(
        name="合成记录顺序写入核验", nodes=nodes,
        control_edges=[{"source_ref": left.ref, "target_ref": right.ref} for left, right in zip(nodes, nodes[1:])],
        final_output={"sources": [{"node_ref": "answer"}]},
    )
    candidate = compile_case(graph, snap, req)
    restored = decompile_candidate_to_graph_intent(candidate)
    rebuilt = compile_case(restored, snap, req)
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(rebuilt)
    original_workflow = deepcopy(candidate["draft"]["workflow"])
    restored_workflow = deepcopy(rebuilt["draft"]["workflow"])
    # The existing decompiler sorts edges by semantic refs; compare every edge field.
    for workflow in (original_workflow, restored_workflow):
        workflow["edges"] = sorted(workflow["edges"], key=lambda item: item["id"])
    assert original_workflow == restored_workflow
    contracts = _contracts()
    for node in graph.nodes:
        if node.kind in TABLE_KINDS:
            assert {binding.port for binding in node.inputs} == _declared_inputs(contracts[node.kind], node.config)
    assert {node.kind for node in graph.nodes} == {*TABLE_KINDS, "json_serialize", "workflow_agent"}
