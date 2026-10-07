from __future__ import annotations

from copy import deepcopy
from itertools import product
import json

import pytest

from server.meta_agent.control_flow import control_contract_issues, semantic_outcomes, native_outcome_map
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, workflow_semantic_checksum
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentNodeV3, GraphIntentControlEdgeV3, GraphIntentV3
from server.tests.test_meta_planner_controlled_writes import (
    _plan, compile_case, intent, request, snapshot,
)
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture


QUERY_REFS = ("query_after_insert", "query_after_update")


def _chain(failure_action="error_output"):
    nodes = []
    for operation, write_ref, query_ref in (
        ("insert", "insert_ticket", None),
        ("update", "update_ticket", QUERY_REFS[0]),
        ("delete", "delete_ticket", QUERY_REFS[1]),
    ):
        sample = intent(operation)
        if query_ref:
            query = sample.nodes[0].model_copy(deep=True)
            query.ref = query_ref
            query.config = {"failure_action": failure_action}
            query.outputs[0].variable = f"{query_ref}_rows"
            nodes.append(query)
        write = next(node for node in sample.nodes if node.ref == "write").model_copy(deep=True)
        write.ref = write_ref
        write.outputs[0].variable = f"{write_ref}_result"
        if query_ref:
            write.inputs[0].source_ref = query_ref
            write.inputs[0].variable = f"{query_ref}_rows"
        nodes.append(write)
    agent = intent().nodes[-1].model_copy(deep=True)
    agent.inputs = []
    agent.config["role_prompt"] = "仅根据实际写入结果进行中文汇总。"
    for write_ref in ("insert_ticket", "update_ticket", "delete_ticket"):
        serializer = intent().nodes[-2].model_copy(deep=True)
        serializer.ref = f"encode_{write_ref}"
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
    edges = [GraphIntentControlEdgeV3(source_ref=a.ref, target_ref=b.ref) for a, b in zip(nodes, nodes[1:])]
    if failure_action == "error_output":
        for ref in QUERY_REFS:
            nodes.append(GraphIntentNodeV3(
                ref=f"{ref}_failed", kind="terminate_error", title="查询失败后停止",
                config={"error_code": "QUERY_FAILED", "message": "查询失败，停止后续写入。"},
            ))
            edges.append(GraphIntentControlEdgeV3(
                source_ref=ref, outcome_ref="error", target_ref=f"{ref}_failed",
            ))
    return GraphIntentV3(name="合成记录顺序核验", nodes=nodes, control_edges=edges,
                         final_output={"sources": [{"node_ref": "answer"}]})


def _request(snap):
    req = request(snap)
    req.goal = "查询 table-orders 并依次插入、更新、删除合成记录，汇总实际结果。"
    req.scope.data_table_write_grants[0].operations = ["insert", "update", "delete"]
    return req


def _prompt(stage, graph=None):
    snap = snapshot()
    req = _request(snap)
    req.scope.allowed_node_kinds = [item["kind"] for item in snap.nodes]
    if stage == "generate":
        raw = MetaPlannerV2Service._blueprint_prompt(req, _plan(), snap, None)
    elif stage == "full_repair":
        raw = MetaPlannerV2Service._repair_prompt(req, _plan(), snap, "{}", ["invalid"])
    else:
        raw = MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, graph or _chain(), ["invalid"])
    return json.loads(raw)


@pytest.mark.parametrize("stage", ["generate", "full_repair", "patch_repair"])
def test_all_executable_outcomes_match_authority_in_every_model_stage(stage):
    contracts = _prompt(stage)["graph_intent_contract"]["node_roles"]["executable_node_contracts"]
    assert set(contracts) == {item["kind"] for item in snapshot().nodes if get_planner_node_adapter(item["kind"])}
    for kind, projected in contracts.items():
        assert "control_outcomes" not in projected, "不得用无配置区分的出口列表覆盖真实语义"
        contract = projected["control_contract"]
        configurations = [({}, None)]
        if kind in {"data_table_query", "knowledge_retrieval"}:
            assert contract["selector"] == "value"
            assert contract["config_field"] == "failure_action"
            assert contract["default_value"] == "stop"
            configurations = [({"failure_action": mode}, mode) for mode in ("stop", "error_output")]
        elif kind == "multi_route":
            assert contract["selector"] == "length"
            assert contract["config_field"] == "routes"
            configurations = [({"routes": [{}] * count}, count) for count in range(2, 9)]
        assert len(contract["variants"]) == len(configurations)
        for config, value in configurations:
            variant = next(item for item in contract["variants"] if item["config_value"] == value)
            node = GraphIntentNodeV3(ref="probe", title="契约核对", kind=kind, config=config)
            expected = list(semantic_outcomes(node))
            assert variant["outcomes"] == expected
            assert set(expected) == set(native_outcome_map(node))
            rule = "none" if not expected else "exactly_once" if len(expected) > 1 else "fanout"
            assert variant["connections"] == rule


@pytest.mark.parametrize("mode", ["stop", "error_output"])
def test_three_writes_and_two_queries_roundtrip_in_both_failure_modes(monkeypatch, mode):
    _forbid_records(monkeypatch)
    graph, snap = _chain(mode), snapshot()
    req = _request(snap)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    candidate = compile_case(graph, snap, req)
    rebuilt = compile_case(decompile_candidate_to_graph_intent(candidate), snap, req)
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(rebuilt)


def _break_edges(graph, defect):
    for ref in QUERY_REFS:
        index = next(i for i, edge in enumerate(graph.control_edges)
                     if edge.source_ref == ref and edge.outcome_ref == "error")
        if defect == "missing":
            graph.control_edges.pop(index)
        elif defect == "duplicate":
            graph.control_edges[index].outcome_ref = "success"
        else:
            graph.control_edges[index].outcome_ref = "matched"
    return graph


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unexpected"])
def test_repair_facts_distinguish_missing_duplicate_and_unexpected_outcomes(defect):
    graph = _break_edges(_chain(), defect)
    contract = _prompt("patch_repair", graph)["repair_contract"]
    assert contract["existing_control_edges"] == [edge.model_dump(mode="json") for edge in graph.control_edges]
    diagnostics = contract["control_contract_issues"]
    assert len(diagnostics) == 2
    for issue, ref in zip(diagnostics, QUERY_REFS):
        assert issue["node_ref"] == ref
        assert issue["expected_outcomes"] == ["success", "error"]
        assert issue["missing_outcomes"] == ["error"]
        assert issue["duplicate_outcomes"] == (["success"] if defect == "duplicate" else [])
        assert len(issue["unexpected_edge_indices"]) == (1 if defect == "unexpected" else 0)
        assert issue["actual_counts"] == {"success": 2 if defect == "duplicate" else 1, "error": 0}
    raw = json.dumps(diagnostics)
    for value in ("DEMO", "role_prompt", "table-orders", "sku", "score", "values"):
        assert value not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["explicit", "empty", "wrong_outcome", "duplicate_edge"])
async def test_one_repair_compiles_real_sequence_or_preserves_failure(tmp_path, monkeypatch, repair):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch)
    req = _request(snap)
    graph = _break_edges(_chain(), "missing")
    before = graph.model_dump(mode="json")
    operations = []
    if repair != "empty":
        operations = [{
            "op": "connect_control", "source_ref": ref,
            "outcome_ref": "error" if repair == "explicit" else "success",
            "target_ref": f"{ref}_failed",
        } for ref in QUERY_REFS]
    if repair == "duplicate_edge":
        operations = [dict(operations[0], target_ref="update_ticket")]
    responses = [_plan().model_dump_json(), graph.model_dump_json(), json.dumps({"operations": operations})]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        return responses[len(calls) - 1]

    service = LegacyGraphReplayService(authoring_service=authoring,
                                  preflight=headless.planner_service.preflight, completion=completion)
    result = await service.generate(req, snap)
    assert len(calls) == 3
    assert graph.model_dump(mode="json") == before
    assert result.validation["valid"] is (repair == "explicit"), result.validation
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    repair_prompt = json.loads(calls[2][2])
    assert len(repair_prompt["repair_contract"]["control_contract_issues"]) == 2
    diagnostics = proposal.payload["meta_planner_report"]["generation_diagnostics"]
    initial = next(item for item in diagnostics if item["stage"] == "capability_compile")
    repaired = next(item for item in diagnostics if item["stage"] == "graph_patch_v1")
    assert len(initial["control_contract_issues"]) == 2
    if repair == "explicit":
        assert repaired["control_contract_issues"] == []
        assert repaired["recompile_executed"]
        # Patch normalization is not the semantic graph delta.
        assert repaired["patch"]["before_checksum"] == repaired["patch"]["after_checksum"]
        assert repaired["control_graph_checksum"] != initial["control_graph_checksum"]
        restored = decompile_candidate_to_graph_intent(proposal.payload)
        assert {(e.source_ref, e.outcome_ref, e.target_ref) for e in restored.control_edges} == {
            (e.source_ref, e.outcome_ref, e.target_ref) for e in _chain().control_edges
        }
    elif repair != "duplicate_edge":
        assert len(repaired["control_contract_issues"]) == 2
        if repair == "wrong_outcome":
            assert repaired["control_graph_checksum"] != initial["control_graph_checksum"]
            assert all(item["duplicate_outcomes"] == ["success"] for item in repaired["control_contract_issues"])
        else:
            assert repaired["control_graph_checksum"] == initial["control_graph_checksum"]


@pytest.mark.parametrize(("kind", "config", "expected"), [
    ("workflow_agent", {}, ["success"]),
    ("data_table_query", {}, ["success"]),
    ("data_table_query", {"failure_action": "error_output"}, ["success", "error"]),
    ("knowledge_retrieval", {"failure_action": "error_output"}, ["success", "error"]),
    ("condition", {}, ["matched", "unmatched"]),
    ("multi_route", {"routes": [{}, {}]}, ["case_1", "case_2", "default"]),
    ("terminate_error", {}, []),
])
def test_connection_decisions_match_pre_refactor_multiset_contract(kind, config, expected):
    outcomes = [*expected, "matched" if "matched" not in expected else "success"]
    for final, counts in product((False, True), product(range(3), repeat=len(outcomes))):
        actual = [outcome for outcome, count in zip(outcomes, counts) for _ in range(count)]
        graph = GraphIntentV3(
            name="出口基数对照", nodes=[
                GraphIntentNodeV3(ref="probe", title="被测节点", kind=kind, config=config),
                GraphIntentNodeV3(ref="sink", title="终点", kind="workflow_agent"),
            ],
            control_edges=[{"source_ref": "probe", "outcome_ref": outcome, "target_ref": "sink"} for outcome in actual],
            final_output={"sources": [{"node_ref": "probe" if final else "sink"}]},
        )
        if kind == "terminate_error":
            invalid = bool(actual)
        else:
            invalid = bool((not actual and not final) or (actual and final))
            if len(expected) > 1:
                invalid |= set(actual) != set(expected) or len(actual) != len(expected)
            elif actual:
                invalid |= set(actual) != set(expected)
        diagnosed = any(item["node_ref"] == "probe" for item in control_contract_issues(graph))
        assert diagnosed is invalid, (kind, final, actual)


def test_control_receipt_is_order_stable_and_redacts_private_refs_and_values():
    graph = _break_edges(_chain(), "missing")
    graph.nodes[0].config["values"] = {"sku": "PRIVATE_BUSINESS_CANARY"}
    private_ref = "secret-private-canary"
    graph.nodes[1].ref = private_ref
    for edge in graph.control_edges:
        if edge.source_ref == QUERY_REFS[0]:
            edge.source_ref = private_ref
    first = GenerationDiagnostics("capability_compile")
    first.bind_graph(graph)
    original = first.as_dict()
    raw = json.dumps(original)
    assert private_ref not in raw and "PRIVATE_BUSINESS_CANARY" not in raw
    assert "sku" not in raw and "table-orders" not in raw
    graph.nodes.reverse()
    graph.control_edges.reverse()
    second = GenerationDiagnostics("capability_compile")
    second.bind_graph(graph)
    assert original["control_graph_checksum"] == second.as_dict()["control_graph_checksum"]


def test_invalid_large_route_config_cannot_expand_persistent_diagnostics():
    graph = _chain("stop")
    graph.nodes[0].kind = "multi_route"
    graph.nodes[0].config = {"routes": [{}] * 1000}
    report = GenerationDiagnostics("capability_compile")
    report.bind_graph(graph)
    raw = json.dumps(report.as_dict())
    assert len(raw) < 8192
    assert "case_999" not in raw
