from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest

from server.meta_agent import meta_planner_v2
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.graph_ir_v3 import graph_intent_to_v2
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.schemas import GraphIntentNodeResourceRefV3
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request, snapshot
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    _forbid_records(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("独立资源诊断测试禁止 HTTP、Provider 和业务记录访问")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def _missing_predicate(operation):
    graph = intent(operation)
    node = next(node for node in graph.nodes if node.ref == "write")
    node.config["filter"] = {
        "ref": "record_id_filter", "field": "record_id", "operator": "eq",
        "value_source": "input",
    }
    return graph, node


def _diagnose(graph, req, snap):
    stage = GenerationDiagnostics("capability_compile")
    stage.enter("authorization")
    if graph.ir_version == 3:
        stage.bind_graph(graph)
    before = graph.model_dump(mode="json")
    issues = validate_blueprint_authorization(req, _plan(), graph, snap, diagnostics=stage)
    assert graph.model_dump(mode="json") == before
    assert issues == validate_blueprint_authorization(req, _plan(), graph, snap)
    return issues, stage.as_dict()


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("ir_version", [2, 3])
def test_missing_port_and_resource_are_collected_together(operation, ir_version):
    graph, node = _missing_predicate(operation)
    node.resource_ref = None
    if ir_version == 2:
        graph = graph_intent_to_v2(graph)
    snap = snapshot()
    issues, stage = _diagnose(graph, request(snap, operation), snap)
    assert any("predicate_record_id_filter" in issue for issue in issues)
    assert issues.count("Node write requires a data_table resource.") == 1
    assert stage["category_counts"] == {"node_config": 1, "resource_contract": 1}
    assert stage["issue_count"] == 2


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("resource_error", ["denied_operation", "missing_table"])
def test_missing_port_does_not_hide_scope_or_catalog_failure(operation, resource_error):
    graph, node = _missing_predicate(operation)
    snap = snapshot()
    req = request(snap, operation)
    if resource_error == "denied_operation":
        req.scope.data_table_write_grants[0].operations = ["insert"]
        expected = f"节点 write 未获该表的 {operation} 显式授权。"
    else:
        node.resource_ref.resource_id = "table-missing"
        req.scope.data_table_write_grants[0].table_id = "table-missing"
        expected = "Node resource table-missing is no longer available."
    issues, stage = _diagnose(graph, req, snap)
    assert any("predicate_record_id_filter" in issue for issue in issues)
    assert expected in issues
    assert stage["category_counts"] == {"node_config": 1, "resource_contract": 1}


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
@pytest.mark.parametrize("resource_error", ["missing_reference", "denied_operation"])
def test_invalid_config_still_reports_independent_resource_error_without_resolution(
    monkeypatch, operation, resource_error,
):
    graph = intent(operation)
    node = next(node for node in graph.nodes if node.ref == "write")
    node.config["max_affected_rows"] = {"invalid": True}
    snap = snapshot()
    req = request(snap, operation)
    if resource_error == "missing_reference":
        node.resource_ref = None
        expected = "Node write requires a data_table resource."
    else:
        req.scope.data_table_write_grants = []
        expected = f"节点 write 未获该表的 {operation} 显式授权。"
    original = meta_planner_v2.resolve_node_resource_snapshot

    def resolve_checked(candidate, *args, **kwargs):
        assert candidate.ref != "write", "非法配置不能进入依赖配置的资源解析"
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(meta_planner_v2, "resolve_node_resource_snapshot", resolve_checked)
    issues, stage = _diagnose(graph, req, snap)
    assert any("config is invalid" in issue for issue in issues)
    assert expected in issues
    assert stage["category_counts"]["node_config"] >= 1
    assert stage["category_counts"]["resource_contract"] == 1


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_invalid_config_with_valid_scope_is_not_passed_to_full_grant_validation(operation):
    graph = intent(operation)
    node = next(node for node in graph.nodes if node.ref == "write")
    node.config["max_affected_rows"] = {"invalid": True}
    snap = snapshot()
    issues, stage = _diagnose(graph, request(snap, operation), snap)
    assert any("config is invalid" in issue for issue in issues)
    assert "resource_contract" not in stage["category_counts"]


@pytest.mark.parametrize("operation,violation", [
    ("update", "row_limit"), ("delete", "row_limit"),
    ("insert", "fields"), ("update", "fields"),
])
def test_valid_shape_still_requires_complete_write_grant(operation, violation):
    graph = intent(operation)
    node = next(node for node in graph.nodes if node.ref == "write")
    snap = snapshot()
    req = request(snap, operation)
    if violation == "row_limit":
        node.config["max_affected_rows"] = 2
        expected = "节点 write 的影响行数超过用户授权。"
    else:
        req.scope.data_table_write_grants[0].writable_fields = []
        expected = "节点 write 包含未授权写入字段。"
    issues, stage = _diagnose(graph, req, snap)
    assert expected in issues
    assert "node_config" not in stage["category_counts"]


@pytest.mark.parametrize("limit", [1, 2])
def test_insert_does_not_accept_an_impact_limit_in_config(limit):
    graph = intent("insert")
    node = next(node for node in graph.nodes if node.ref == "write")
    node.config["max_affected_rows"] = limit
    snap = snapshot()
    issues, stage = _diagnose(graph, request(snap), snap)
    assert any("max_affected_rows: Extra inputs are not permitted" in issue for issue in issues)
    assert stage["category_counts"]["node_config"] == 1


def test_invalid_read_config_does_not_hide_read_authorization():
    graph = intent("update")
    graph.nodes[0].config["limit"] = 201
    snap = snapshot()
    issues, stage = _diagnose(graph, request(snap, "update", read=False), snap)
    assert any("Node lookup config is invalid" in issue for issue in issues)
    assert "Node resource table-orders is not authorized for data_table." in issues
    assert any(item.get("node_ref") == "lookup" and item["category"] == "resource_contract" for item in stage["issues"])


def test_invalid_pure_config_does_not_hide_forbidden_resource_reference():
    graph = intent("insert")
    node = next(node for node in graph.nodes if node.ref == "encode")
    node.config["format"] = "unsupported-format"
    node.resource_ref = GraphIntentNodeResourceRefV3(resource_id="table-orders")
    snap = snapshot()
    issues, _ = _diagnose(graph, request(snap), snap)
    assert any("Node encode config is invalid" in issue for issue in issues)
    assert "Node encode cannot carry a node resource reference." in issues


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("repair_resource", [False, True])
async def test_unique_repair_receives_both_issues_and_cannot_skip_resource(
    tmp_path, monkeypatch, operation, repair_resource,
):
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch, operation)
    req = request(snap, operation)
    graph, node = _missing_predicate(operation)
    node.resource_ref = None
    original = deepcopy(graph.model_dump(mode="json"))
    operations = [{
        "op": "connect_data", "source_ref": "input", "source_port": "user_input",
        "target_ref": "write", "target_port": "predicate_record_id_filter",
    }]
    if repair_resource:
        operations.append({"op": "set_node_resource", "node_ref": "write", "resource_id": "table-orders"})
    responses = [_plan().model_dump_json(), graph.model_dump_json(), json.dumps({"operations": operations})]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        if len(calls) == 3:
            prompt = json.loads(args[2])
            assert prompt["base_graph_intent"] == original
            assert "Node write requires a data_table resource." in prompt["validation_issues"]
            assert any("predicate_record_id_filter" in issue for issue in prompt["validation_issues"])
        return responses[len(calls) - 1]

    service = LegacyGraphReplayService(
        authoring_service=authoring, preflight=headless.planner_service.preflight,
        completion=completion,
    )
    result = await service.generate(req, snap)
    assert len(calls) == 3 and calls[-1][3] == 0
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    report = proposal.payload["meta_planner_report"]
    first, repair = report["generation_diagnostics"][-2:]
    assert first["category_counts"] == {"node_config": 1, "resource_contract": 1}
    assert report["repair_protocol"] == "graph_patch_v1"
    assert repair["patch"]["phase"] == "completed"
    assert bool(result.validation["valid"]) is repair_resource
    assert repair["recompile_executed"] is repair_resource
    if not repair_resource:
        assert repair["failed_phase"] == "output_normalization"
