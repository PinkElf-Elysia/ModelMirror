from copy import deepcopy
import json

import httpx
import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.graph_ir_v3 import resolve_node_resource_snapshot
from server.meta_agent.meta_planner_v2 import validate_blueprint_authorization
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentInputBindingV3
from server.tests.test_meta_planner_control_contract_alignment import _chain, _request
from server.tests.test_meta_planner_controlled_writes import _plan, snapshot
from server.tests.test_meta_planner_write_generation_failures import _forbid_records


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    _forbid_records(monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail("资源诊断测试禁止 Provider 与 HTTP 请求")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def broken_graph():
    graph = _chain("stop")
    for node in graph.nodes:
        if node.kind not in {"data_table_query", "data_table_update", "data_table_delete"}:
            continue
        node.config["filter"] = {"ref": "selected", "field": "sku", "operator": "eq", "value_source": "input"}
        node.inputs.append(GraphIntentInputBindingV3(
            port="predicate_selected", variable="insert_ticket_result", source_ref="insert_ticket",
            source_port="result", value_schema={"type": "object"},
        ))
    return graph


def diagnose(graph):
    snap = snapshot()
    stage = GenerationDiagnostics("capability_compile")
    stage.enter("authorization")
    stage.bind_graph(graph)
    before = deepcopy(graph.model_dump(mode="json"))
    issues = validate_blueprint_authorization(_request(snap), _plan(), graph, snap, diagnostics=stage)
    assert issues == validate_blueprint_authorization(_request(snap), _plan(), graph, snap)
    assert graph.model_dump(mode="json") == before
    return issues, stage.as_dict()


def test_four_resource_type_failures_retain_distinct_first_causes_without_values():
    issues, report = diagnose(broken_graph())
    resource_issues = [issue for issue in report["issues"] if issue["category"] == "resource_contract"]
    assert len(resource_issues) == 4
    for issue in resource_issues:
        assert issue["code"] == "TABLE_PREDICATE_INPUT_TYPE_MISMATCH"
        assert issue["resource_detail"]["predicate_index"] == 0
        assert issue["resource_detail"]["expected_type"] == "string"
        assert issue["resource_detail"]["actual_type"] == "object"
        assert len(issue["resource_detail"]["expected_schema_checksum"]) == 64
    assert len({issue["node_index"] for issue in resource_issues}) == 4
    assert "DEMO" not in json.dumps(report) and "sku" not in json.dumps(report)
    assert len(issues) >= 4


@pytest.mark.parametrize("defect,code", [
    ("field", "TABLE_PREDICATE_FIELD_UNKNOWN"),
    ("literal", "TABLE_PREDICATE_LITERAL_TYPE_MISMATCH"),
    ("operator", "TABLE_PREDICATE_OPERATOR_INVALID"),
])
def test_resource_failures_are_classified_and_sensitive_literals_never_escape(defect, code):
    graph = _chain("stop")
    node = next(node for node in graph.nodes if node.kind == "data_table_query")
    predicate = {"ref": "selected", "field": "sku", "operator": "eq", "value_source": "literal", "value": "DEMO"}
    if defect == "field":
        predicate["field"] = "PRIVATE_SECRET_FIELD"
    elif defect == "literal":
        predicate["value"] = {"PRIVATE_SECRET_KEY": "PRIVATE_SECRET_VALUE"}
    else:
        predicate.update(field="revision", operator="contains", value=1)
    node.config["filter"] = predicate
    _, report = diagnose(graph)
    assert any(issue["code"] == code for issue in report["issues"])
    assert "PRIVATE_" not in json.dumps(report)


@pytest.mark.parametrize("operator,expected_type", [("eq", "string"), ("in", "array")])
def test_resource_input_projection_uses_the_same_schema_as_the_validator(operator, expected_type):
    graph = broken_graph()
    node = next(node for node in graph.nodes if node.kind == "data_table_update")
    node.config["filter"]["operator"] = operator
    adapter = get_planner_node_adapter(node.kind)
    parsed = adapter.validate_intent_node(node)
    resource = resolve_node_resource_snapshot(node, snapshot()).model_dump(mode="json")
    required = adapter.resolved_predicate_input_schemas(parsed, resource)
    assert set(required) == {"predicate_selected"}, "records 与动态谓词输入不得混用"
    assert required["predicate_selected"].type == expected_type
    if operator == "in":
        assert required["predicate_selected"].items.type == "string"
    with pytest.raises(ValueError):
        adapter.validate_resolved_resource(node, parsed, resource)
    node.inputs[-1].value_schema = required["predicate_selected"]
    adapter.validate_resolved_resource(node, parsed, resource)


def test_denied_resources_are_never_resolved_or_disclosed_by_diagnostics(monkeypatch):
    from server.meta_agent import meta_planner_v2

    graph, snap = broken_graph(), snapshot()
    req = _request(snap)
    req.scope.data_table_ids = []
    req.scope.data_table_write_grants = []
    def forbidden(*args, **kwargs):
        pytest.fail("未授权资源不得解析固定字段 Schema")
    monkeypatch.setattr(meta_planner_v2, "resolve_node_resource_snapshot", forbidden)
    stage = GenerationDiagnostics("capability_compile")
    stage.enter("authorization")
    stage.bind_graph(graph)
    issues = validate_blueprint_authorization(req, _plan(), graph, snap, diagnostics=stage)
    assert any("not authorized" in issue or "未获" in issue for issue in issues)
    assert "resource_detail" not in json.dumps(stage.as_dict())
    assert "expected_schema_checksum" not in json.dumps(stage.as_dict())
