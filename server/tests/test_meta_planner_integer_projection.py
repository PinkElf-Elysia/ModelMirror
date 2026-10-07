from __future__ import annotations

import socket
import sqlite3
from typing import get_args

import pytest
from pydantic import ValidationError

from server.meta_agent.graph_ir_v3 import (
    _value_schema,
    decompile_candidate_to_graph_intent,
    graph_intent_to_v2,
    resolve_graph_intent,
    workflow_semantic_checksum,
)
from server.meta_agent.meta_planner_v2 import (
    _typed_blueprint,
    validate_blueprint_authorization,
)
from server.meta_agent.node_adapters import (
    PlannerWriteInputContractError,
    get_planner_node_adapter,
)
from server.meta_agent.schemas import (
    GraphIntentControlEdgeV3,
    GraphIntentNodeV3,
    GraphIntentV3,
    MetaPlannerIRInputBinding,
    MetaPlannerIROutputBinding,
    MetaPlannerValueType,
)
from server.tests.test_meta_planner_controlled_writes import (
    _plan,
    compile_case,
    intent,
    request,
    snapshot,
)
from server.tests.test_meta_planner_read_resources import (
    _answer_node,
    _input,
    _serialize_node,
)
from server.workflow_native.node_contracts import (
    WorkflowValueSchema,
    WorkflowValueType,
)


@pytest.fixture(autouse=True)
def forbid_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Integer projection must not access a provider or database")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


def _graph(source: WorkflowValueSchema, target: WorkflowValueSchema | None = None):
    decode = GraphIntentNodeV3(
        ref="decode",
        kind="json_deserialize",
        title="解析类型化数据",
        inputs=[_input("json", "user_input", "input", "user_input", WorkflowValueSchema(type="string"))],
        outputs=[{"port": "value", "variable": "decoded", "value_schema": source}],
        config={"expected_schema": source.model_dump(mode="json")},
    )
    encode = _serialize_node(source_ref="decode", variable="decoded", schema=target or source)
    encode.title = "序列化数据"
    encode.inputs[0].source_port = "value"
    answer = _answer_node(
        source_ref="encode", source_port="json", variable="encoded_resource",
        schema=WorkflowValueSchema(type="string"),
    )
    answer.title = "汇总结果"
    return GraphIntentV3(
        name="整数兼容投影验证",
        nodes=[decode, encode, answer],
        control_edges=[
            {"source_ref": "decode", "target_ref": "encode"},
            {"source_ref": "encode", "target_ref": "answer"},
        ],
        final_output={"sources": [{"node_ref": "answer"}]},
    )


def _context():
    snap = snapshot()
    req = request(snap)
    req.goal = "解析输入数据，序列化后由智能体汇总。"
    req.scope.data_table_ids = []
    req.scope.data_table_write_grants = []
    return snap, req


def test_compatibility_value_types_follow_authoritative_contract():
    assert get_args(MetaPlannerValueType) == get_args(WorkflowValueType)


def test_integer_import_does_not_fall_back_to_any():
    assert _value_schema("integer") == WorkflowValueSchema(type="integer")


@pytest.mark.parametrize("value_type", get_args(WorkflowValueType))
def test_compatibility_bindings_and_import_preserve_each_value_type(value_type):
    for binding_type in (MetaPlannerIRInputBinding, MetaPlannerIROutputBinding):
        binding = binding_type(port="value", variable="value", value_type=value_type)
        assert binding_type.model_validate_json(binding.model_dump_json()).value_type == value_type
    assert _value_schema(value_type).type == value_type


@pytest.mark.parametrize("project", [graph_intent_to_v2, lambda graph: _typed_blueprint(_plan(), graph)])
def test_both_compatibility_projections_preserve_integer(project):
    graph = _graph(WorkflowValueSchema(type="integer"))
    projected = project(graph)
    assert projected.nodes[0].outputs[0].value_type == "integer"
    assert projected.nodes[1].inputs[0].value_type == "integer"


@pytest.mark.parametrize("schema", [
    WorkflowValueSchema(type="number"),
    WorkflowValueSchema(type="integer"),
    WorkflowValueSchema(type="integer", nullable=True),
    WorkflowValueSchema(type="array", items=WorkflowValueSchema(type="integer")),
    WorkflowValueSchema(type="object", properties={"revision": WorkflowValueSchema(type="integer")}, required=("revision",)),
])
def test_candidate_roundtrip_preserves_full_schema(schema):
    snap, req = _context()
    graph = _graph(schema)
    resolve_graph_intent(graph, snap, default_agent_model_id=req.default_agent_model_id)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    candidate = compile_case(graph, snap, req)
    native = next(node for node in candidate["draft"]["workflow"]["nodes"] if node["data"].get("plannerRef") == "decode")
    assert native["data"]["expectedSchema"] == schema.model_dump(mode="json")
    restored = decompile_candidate_to_graph_intent(candidate)
    restored_decode = next(node for node in restored.nodes if node.ref == "decode")
    assert restored_decode.outputs[0].value_schema == schema
    rebuilt = compile_case(restored, snap, req)
    assert workflow_semantic_checksum(candidate["draft"]["workflow"]) == workflow_semantic_checksum(rebuilt["draft"]["workflow"])


def test_integer_can_widen_to_number_without_weakening_source_schema():
    snap, req = _context()
    graph = _graph(WorkflowValueSchema(type="integer"), WorkflowValueSchema(type="number"))
    resolve_graph_intent(graph, snap, default_agent_model_id=req.default_agent_model_id)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    restored = decompile_candidate_to_graph_intent(compile_case(graph, snap, req))
    assert next(node for node in restored.nodes if node.ref == "decode").outputs[0].value_schema.type == "integer"
    assert next(node for node in restored.nodes if node.ref == "encode").inputs[0].value_schema.type == "number"


@pytest.mark.parametrize("source", [
    WorkflowValueSchema(type="number"),
    WorkflowValueSchema(type="any"),
    WorkflowValueSchema(type="integer", nullable=True),
    WorkflowValueSchema(type="boolean"),
])
def test_integer_input_cannot_narrow_an_unproven_source(source):
    snap, req = _context()
    graph = _graph(source, WorkflowValueSchema(type="integer"))
    with pytest.raises(ValueError, match="(?i)type"):
        compile_case(graph, snap, req)


@pytest.mark.parametrize("value", [1.5, True, "1", None])
def test_integer_runtime_validation_is_not_number_coercion(value):
    schema = WorkflowValueSchema(type="integer")
    schema.assert_value(1)
    with pytest.raises(ValueError):
        schema.assert_value(value)


def test_unknown_compatibility_type_still_fails_closed():
    for binding_type in (MetaPlannerIRInputBinding, MetaPlannerIROutputBinding):
        with pytest.raises(ValidationError):
            binding_type(port="value", variable="value", value_type="integer_unsafe")


def test_integer_revision_predicate_keeps_direct_query_records():
    snap = snapshot()
    req = request(snap, "delete")
    graph = intent("delete")
    decode = _graph(WorkflowValueSchema(type="integer")).nodes[0]
    graph.nodes.insert(0, decode)
    graph.control_edges.insert(0, GraphIntentControlEdgeV3(source_ref="decode", target_ref="lookup"))
    write = next(node for node in graph.nodes if node.kind == "data_table_delete")
    write.config["filter"] = {
        "kind": "group", "logic": "and", "items": [
            write.config["filter"],
            {"ref": "revision", "field": "revision", "operator": "eq", "value_source": "input"},
        ],
    }
    write.inputs.append(_input(
        "predicate_revision", "decoded", "decode", "value", WorkflowValueSchema(type="integer"),
    ))
    graph = GraphIntentV3.model_validate(graph.model_dump(mode="json"))
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    candidate = compile_case(graph, snap, req)
    restored = decompile_candidate_to_graph_intent(candidate)
    restored_write = next(node for node in restored.nodes if node.ref == "write")
    ports = {binding.port: binding for binding in restored_write.inputs}
    assert set(ports) == {"predicate_revision", "records"}
    assert ports["predicate_revision"].value_schema.type == "integer"
    assert ports["records"].source_ref == "lookup"
    assert ports["records"].source_port == "result"
    rebuilt = compile_case(restored, snap, req)
    assert workflow_semantic_checksum(candidate["draft"]["workflow"]) == workflow_semantic_checksum(rebuilt["draft"]["workflow"])


def test_missing_records_is_not_repaired_by_integer_compatibility():
    snap = snapshot()
    req = request(snap, "delete")
    graph = intent("delete")
    compile_case(graph, snap, req)
    write = next(node for node in graph.nodes if node.kind == "data_table_delete")
    write.inputs = []
    with pytest.raises(PlannerWriteInputContractError) as caught:
        get_planner_node_adapter(write.kind).validate_intent_node(write)
    assert caught.value.input_diagnostic["missing_ports"] == ["records"]
    with pytest.raises(ValueError):
        compile_case(graph, snap, req)
