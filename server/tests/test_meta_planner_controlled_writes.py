from copy import deepcopy

import pytest

from server.meta_agent.capabilities import assert_scope_is_authorized
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, resolve_graph_intent
from server.meta_agent.meta_planner_v2 import compile_xpert_candidate, validate_blueprint_authorization
from server.meta_agent.schemas import GraphIntentControlEdgeV3, GraphIntentNodeV3, GraphIntentV3, MetaPlannerScope
from server.meta_agent.write_contract import WRITE_KINDS
from server.workflow_native.node_contracts import WorkflowValueSchema
from server.tests.test_meta_planner_read_resources import (
    _answer_node, _input, _plan, _request, _serialize_node, _snapshot,
)


OBJECT = WorkflowValueSchema(type="object")
ROWS = WorkflowValueSchema(type="array", items=OBJECT)


def snapshot():
    result = _snapshot()
    result.data_tables[0]["schema_checksum"] = "a" * 64
    result.data_tables[0]["schema_versions"][0]["checksum"] = "a" * 64
    return result


def request(snap, operation="insert", *, read=True):
    result = _request(snap)
    result.scope = MetaPlannerScope(
        allowed_node_kinds=[item["kind"] for item in snap.nodes if item["kind"] != "vision_understanding"],
        data_table_ids=["table-orders"] if read else [],
        data_table_write_grants=[{"table_id": "table-orders", "operations": [operation], "writable_fields": ["sku", "score"] if operation != "delete" else []}],
    )
    return result


def intent(operation="insert"):
    nodes = []
    if operation != "insert":
        nodes.append(GraphIntentNodeV3(ref="lookup", kind="data_table_query", title="查询记录", resource_ref={"resource_id": "table-orders"}, outputs=[{"port": "result", "variable": "rows", "value_schema": ROWS}], config={}))
    config = {}
    if operation != "delete":
        config.update(value_source="literal", values={"sku": "DEMO"} if operation == "insert" else {"score": 5})
    if operation != "insert":
        config.update(filter={"ref": "selected", "field": "sku", "operator": "eq", "value": "DEMO"}, max_affected_rows=1)
    nodes.append(GraphIntentNodeV3(
        ref="write", kind=f"data_table_{operation}", title="受控写入", config=config,
        resource_ref={"resource_id": "table-orders"},
        inputs=[_input("records", "rows", "lookup", "result", ROWS)] if operation != "insert" else [],
        outputs=[{"port": "result", "variable": "write_result", "value_schema": OBJECT}],
    ))
    nodes.extend([_serialize_node(source_ref="write", variable="write_result", schema=OBJECT), _answer_node(source_ref="encode", source_port="json", variable="encoded_resource", schema=WorkflowValueSchema(type="string"))])
    return GraphIntentV3(name="受控写入验证", nodes=nodes, control_edges=[{"source_ref": left.ref, "target_ref": right.ref} for left, right in zip(nodes, nodes[1:])], final_output={"sources": [{"node_ref": "answer"}]})


def compile_case(graph, snap, req):
    return compile_xpert_candidate(request=req, plan=_plan(), blueprint=graph, snapshot=snap, target=None)


def test_capability_exact_22_default_write_closed():
    snap = snapshot()
    assert len(snap.nodes) == 22
    assert WRITE_KINDS <= {item["kind"] for item in snap.nodes}
    assert not WRITE_KINDS & set(snap.default_scope.allowed_node_kinds)
    assert snap.default_scope.data_table_write_grants == []
    assert snap.version.endswith("-v10")
    for kind in ("http_request", "data_merge_loop", "sandbox", "human_intervention"):
        assert kind not in {item["kind"] for item in snap.nodes}


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_write_adapter_roundtrip_and_authority(operation):
    snap = snapshot()
    req = request(snap, operation)
    graph = intent(operation)
    assert_scope_is_authorized(req.scope, snap)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    candidate = compile_case(graph, snap, req)
    restored = decompile_candidate_to_graph_intent(candidate)
    rebuilt = compile_case(restored, snap, req)
    original_nodes = candidate["draft"]["workflow"]["nodes"]
    rebuilt_nodes = rebuilt["draft"]["workflow"]["nodes"]
    assert original_nodes == rebuilt_nodes
    native = next(node for node in original_nodes if node["data"].get("plannerRef") == "write")
    assert native["data"]["contractVersion"] == 2
    assert native["data"]["maxAffectedRows"] == 1
    assert native["data"]["pinnedSchemaChecksum"] == "a" * 64
    assert native["data"]["writeGrant"] == req.scope.data_table_write_grants[0].model_dump(mode="json")


def test_write_grant_does_not_authorize_queries():
    snap = snapshot()
    req = request(snap, "update", read=False)
    assert any("not authorized" in issue for issue in validate_blueprint_authorization(req, _plan(), intent("update"), snap))
    req = request(snap, "insert", read=False)
    assert validate_blueprint_authorization(req, _plan(), intent(), snap) == []


def test_missing_or_exceeded_grant_fails_closed():
    snap = snapshot()
    with pytest.raises(ValueError, match="显式授权"):
        resolve_graph_intent(intent(), snap, default_agent_model_id="model/agent")
    graph = intent("update")
    graph.nodes[1].config["max_affected_rows"] = 2
    with pytest.raises(ValueError, match="影响行数"):
        compile_case(graph, snap, request(snap, "update"))


def test_schema_advance_and_archive_prevent_first_write():
    snap = snapshot()
    req = request(snap)
    restored = decompile_candidate_to_graph_intent(compile_case(intent(), snap, req))
    snap.data_tables[0]["active_schema_version"] = 2
    with pytest.raises(ValueError, match="Schema 漂移"):
        compile_case(restored, snap, req)
    snap.data_tables[0]["active_schema_version"] = 1
    snap.data_tables[0]["status"] = "archived"
    with pytest.raises(ValueError, match="归档"):
        compile_case(restored, snap, req)


def test_forged_record_source_is_not_a_query_receipt():
    snap = snapshot()
    graph = intent("update")
    graph.nodes[0] = GraphIntentNodeV3(ref="lookup", kind="json_deserialize", title="伪造记录", inputs=[_input("json", "user_input", "input", "user_input", WorkflowValueSchema(type="string"))], outputs=[{"port": "value", "variable": "rows", "value_schema": ROWS}], config={"expected_schema": ROWS.model_dump(mode="json")})
    graph.nodes[1].inputs[0].source_port = "value"
    with pytest.raises(ValueError, match="直接来自真实查询"):
        compile_case(graph, snap, request(snap, "update"))


def test_unordered_same_table_read_write_is_rejected():
    snap = snapshot()
    graph = intent()
    query = intent("update").nodes[0]
    graph.nodes.insert(0, query)
    graph.control_edges.append(GraphIntentControlEdgeV3(source_ref="lookup", target_ref="answer"))
    graph = GraphIntentV3.model_validate(graph.model_dump(mode="json"))
    with pytest.raises(ValueError, match="控制先后"):
        compile_case(graph, snap, request(snap))


def test_native_variable_forgery_fails_decompile():
    snap = snapshot()
    candidate = compile_case(intent("update"), snap, request(snap, "update"))
    forged = deepcopy(candidate)
    node = next(node for node in forged["draft"]["workflow"]["nodes"] if node["data"].get("plannerRef") == "write")
    node["data"]["recordsVariable"] = "user_input"
    with pytest.raises(ValueError, match="变量"):
        decompile_candidate_to_graph_intent(forged)
