"""Structural reconstruction of safe diagnostics, not a raw model replay."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.control_flow import ControlFlowAnalysisError, analyze_control_flow
from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent, resolve_node_resource_snapshot, workflow_semantic_checksum,
)
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, _graph_patch_repair_contract, validate_blueprint_authorization
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentControlEdgeV3, GraphIntentNodeV3, GraphIntentV3
from server.tests.test_meta_planner_control_flow import _agent, _input, _route_error_intent
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, intent, request, snapshot
from server.tests.test_meta_planner_repair_context import _apply, _prompt, offline_only


def _branch_case(*, error_output=False):
    snap, graph = snapshot(), intent("update")
    for version in snap.data_tables[0]["schema_versions"]:
        next(field for field in version["fields"] if field["name"] == "score")["required"] = True
    req = request(snap, "update")
    query, writer, encoder, answer = graph.nodes
    query.config.update(return_mode="first", limit=1)
    if error_output:
        query.config["failure_action"] = "error_output"
    adapter = get_planner_node_adapter(query.kind)
    resource = resolve_node_resource_snapshot(query, snap)
    output = adapter.authoritative_output_schema(
        "result", adapter.validate_intent_node(query), resource.model_dump(mode="json"),
    )
    query.outputs[0].value_schema = output
    writer.inputs[0].value_schema = output
    encode_query = encoder.model_copy(deep=True)
    encode_query.ref, encode_query.title = "serialize_query", "序列化查询证据"
    encode_query.inputs = [_input("value", "rows", "lookup", schema=output)]
    encode_query.outputs[0].variable = "query_json"
    answer.inputs.append(_input("task", "query_json", "serialize_query", "json"))
    answer.config["task_input"] += "\n查询证据：{{query_json}}"
    unchanged = _agent("unchanged", variable="unchanged_result", source_ref="serialize_query",
                       source_port="json", input_variable="query_json")
    unchanged.title = "无需修改的结论"
    unchanged.config["role_prompt"] = "仅依据查询证据给出中文结论。"
    graph.nodes.extend([
        GraphIntentNodeV3(ref="found", kind="condition", title="检查记录存在",
            inputs=[_input("value", "rows", "lookup", schema=output)],
            config={"field": "", "operator": "is_null"}),
        GraphIntentNodeV3(ref="score_gate", kind="condition", title="检查数值阈值",
            inputs=[_input("value", "rows", "lookup", schema=output)],
            config={"field": "score", "operator": "lt", "value_type": "number", "value": 60}),
        GraphIntentNodeV3(ref="stop", kind="terminate_error", title="记录不存在",
            config={"error_code": "NOT_FOUND", "message": "记录不存在。"}),
        encode_query, unchanged,
    ])
    graph.control_edges = [GraphIntentControlEdgeV3(source_ref=source, outcome_ref=outcome, target_ref=target)
        for source, outcome, target in [
            ("lookup", "success", "found"), ("found", "matched", "stop"),
            ("found", "unmatched", "score_gate"), ("score_gate", "matched", "write"),
            ("score_gate", "unmatched", "serialize_query"),
            ("serialize_query", "success", "unchanged"),
            ("write", "success", "encode"), ("encode", "success", "answer"),
        ]]
    if error_output:
        graph.control_edges.append(GraphIntentControlEdgeV3(source_ref="lookup", outcome_ref="error", target_ref="stop"))
    graph.final_output.sources.append(type(graph.final_output.sources[0])(node_ref="unchanged"))
    return req, snap, GraphIntentV3.model_validate(graph.model_dump(mode="json"))


def _common_producer_patch():
    return [
        {"op": "disconnect_control", "source_ref": "found", "outcome_ref": "unmatched", "target_ref": "score_gate"},
        {"op": "disconnect_control", "source_ref": "score_gate", "outcome_ref": "unmatched", "target_ref": "serialize_query"},
        {"op": "disconnect_control", "source_ref": "serialize_query", "target_ref": "unchanged"},
        {"op": "connect_control", "source_ref": "found", "outcome_ref": "unmatched", "target_ref": "serialize_query"},
        {"op": "connect_control", "source_ref": "serialize_query", "target_ref": "score_gate"},
        {"op": "connect_control", "source_ref": "score_gate", "outcome_ref": "unmatched", "target_ref": "unchanged"},
    ]


def _facts(req, snap, graph):
    facts = []
    issues = validate_blueprint_authorization(req, _plan(), graph, snap, control_dependency_issues=facts)
    return issues, facts


def test_reconstruction_reproduces_both_existing_errors_without_record_access():
    req, snap, graph = _branch_case()
    issues = validate_blueprint_authorization(req, _plan(), graph, snap)
    assert any("serialize_query.json is not available in every scenario" in issue for issue in issues)
    assert "Variable query_json is not reachable at node answer." in issues
    fixed = _apply(req, graph, _common_producer_patch())
    assert validate_blueprint_authorization(req, _plan(), fixed, snap) == []
    compiled = compile_case(fixed, snap, req)
    restored = compile_case(decompile_candidate_to_graph_intent(compiled), snap, req)
    assert workflow_semantic_checksum(compiled) == workflow_semantic_checksum(restored)


def test_repair_prompt_exposes_the_authoritative_counterexample():
    req, snap, graph = _branch_case()
    frozen = graph.model_dump(mode="json")
    payload, _ = _prompt(req, snap, graph)
    review = payload["repair_contract"]["dependency_review"]
    facts = review["control_dependency_issues"]
    path = next(item for item in facts if item["code"] == "DATA_PATH_NOT_GUARANTEED")
    assert {key: path[key] for key in ("node_ref", "input_index", "port", "source_ref", "source_port")} == {
        "node_ref": "answer", "input_index": 1, "port": "task", "source_ref": "serialize_query", "source_port": "json",
    }
    assert path["counterexample"] == {
        "choices": {"found": "unmatched", "score_gate": "matched"},
        "source_reached": False, "target_reached": True, "source_value_available": False,
    }
    assert path["reason"] == "source_not_reached"
    assert path["violating_scenario_count"] == path["target_scenario_count"] == 1
    assert any(item["code"] == "DATA_NOT_CONTROL_ANCESTOR" for item in facts)
    assert "control_dependency_issues" not in payload["repair_contract"]
    assert {"answer", "serialize_query"} <= {item["node_ref"] for item in review["nodes"]}
    assert "operations" not in review
    assert payload["base_graph_intent"] == frozen == graph.model_dump(mode="json")


def test_parallel_source_has_no_fabricated_missing_path_counterexample():
    snap, graph = snapshot(), intent("update")
    req = request(snap, "update")
    graph.nodes.append(GraphIntentNodeV3(
        ref="decode", kind="json_deserialize", title="还原写入证据",
        config={"expected_schema": {"type": "object"}},
        inputs=[_input("json", "encoded_resource", "encode", "json")],
        outputs=[{"port": "value", "variable": "decoded", "value_schema": {"type": "object"}}],
    ))
    graph.control_edges.extend([
        GraphIntentControlEdgeV3(source_ref="write", target_ref="decode"),
        GraphIntentControlEdgeV3(source_ref="decode", target_ref="answer"),
    ])
    # Both nodes arrive, but the producer is not a control ancestor of the consumer.
    assert analyze_control_flow(graph)["scenario_count"] == 1
    issues, facts = _facts(req, snap, graph)
    assert issues == ["Variable encoded_resource is not reachable at node decode."]
    assert len(facts) == 1 and facts[0]["code"] == "DATA_NOT_CONTROL_ANCESTOR"
    assert "counterexample" not in facts[0]


def test_local_port_repair_or_config_noop_cannot_remove_path_errors():
    req, snap, graph = _branch_case()
    operations = [{"op": "update_node", "ref": "serialize_query", "config": {"format": "compact"}}]
    fixed = _apply(req, graph, operations)
    assert _facts(req, snap, fixed) == _facts(req, snap, graph)
    before = graph.model_dump(mode="json")
    with pytest.raises(ValueError):
        compile_case(fixed, snap, req)
    assert graph.model_dump(mode="json") == before


def test_path_proof_survives_independent_write_port_errors():
    req, snap, graph = _branch_case()
    writer = next(node for node in graph.nodes if node.ref == "write")
    extra = writer.inputs[0].model_copy(deep=True)
    extra.port, extra.variable = "values", "invented_values"
    writer.inputs.append(extra)
    frozen = graph.model_dump(mode="json")
    payload, _ = _prompt(req, snap, graph)
    review = payload["repair_contract"]["dependency_review"]
    assert any(item["code"] == "DATA_UNKNOWN_VARIABLE" for item in payload["validation_frontier"]["issues"])
    assert payload["repair_contract"]["input_contract_issues"]
    assert {item["code"] for item in review["control_dependency_issues"]} >= {
        "DATA_PATH_NOT_GUARANTEED", "DATA_NOT_CONTROL_ANCESTOR",
    }
    port_fix = [{"op": "disconnect_data", "source_ref": "lookup", "source_port": "result",
                 "target_ref": "write", "target_port": "values"}]
    partial = _apply(req, graph, port_fix)
    issues, remaining = _facts(req, snap, partial)
    assert len(issues) == 2
    assert {item["code"] for item in remaining} == {"DATA_PATH_NOT_GUARANTEED", "DATA_NOT_CONTROL_ANCESTOR"}
    complete = _apply(req, graph, [*port_fix, *_common_producer_patch()])
    assert _facts(req, snap, complete) == ([], [])
    compile_case(complete, snap, req)
    assert graph.model_dump(mode="json") == frozen


def test_counterexample_is_stable_under_presentation_order_and_contains_no_payload():
    req, snap, graph = _branch_case()
    _, original = _facts(req, snap, graph)
    reordered = graph.model_copy(deep=True)
    reordered.nodes.reverse()
    reordered.control_edges.reverse()
    _, changed = _facts(req, snap, reordered)
    assert changed == original
    for fact in original:
        assert not {"value", "record", "schema", "config", "prompt", "resource_id", "model_id"} & fact.keys()
    assert "secret-default" not in json.dumps(original)
    assert "must-not-leak" not in json.dumps(original)


def test_untrusted_router_schema_does_not_gain_a_fabricated_path_proof():
    _, _, graph = _branch_case()
    with pytest.raises(ControlFlowAnalysisError) as caught:
        analyze_control_flow(graph, output_schemas={})
    assert caught.value.dependency_issues == []
    assert any("权威输入类型尚未解析" in issue for issue in caught.value.issues)


@pytest.mark.parametrize("rename", [False, True])
def test_multi_route_proof_uses_semantic_outcomes_not_specific_ref_names(rename):
    snap, graph = snapshot(), _route_error_intent()
    req = request(snap)
    encoder = intent("insert").nodes[1]
    encoder.ref, encoder.title = "context_encoder", "序列化输入证据"
    encoder.inputs = [_input("value", "user_input", "input", "user_input")]
    encoder.outputs[0].variable = "context_json"
    graph.nodes.append(encoder)
    for node in graph.nodes:
        if node.kind == "workflow_agent":
            node.inputs = [_input("task", "context_json", "context_encoder", "json")]
            node.config["task_input"] = "{{context_json}}"
    next(edge for edge in graph.control_edges if edge.outcome_ref == "case_2").target_ref = encoder.ref
    graph.control_edges.append(GraphIntentControlEdgeV3(source_ref=encoder.ref, target_ref="rejected"))
    if rename:
        refs = {node.ref: f"renamed_{node.ref}" for node in graph.nodes}
        for node in graph.nodes:
            node.ref = refs[node.ref]
            for binding in node.inputs:
                binding.source_ref = refs.get(binding.source_ref, binding.source_ref)
        for edge in graph.control_edges:
            edge.source_ref, edge.target_ref = refs[edge.source_ref], refs[edge.target_ref]
        for source in graph.final_output.sources:
            source.node_ref = refs[source.node_ref]
    _, facts = _facts(req, snap, graph)
    prefix = "renamed_" if rename else ""
    path = next(item for item in facts if item["code"] == "DATA_PATH_NOT_GUARANTEED")
    assert path["node_ref"] == prefix + "approved"
    assert path["source_ref"] == prefix + "context_encoder"
    assert path["counterexample"]["choices"] == {prefix + "router": "case_1"}
    assert path["reason"] == "source_not_reached"


def test_read_error_does_not_misreport_an_executed_source_as_absent():
    snap, graph = snapshot(), intent("update")
    req = request(snap, "update")
    query, _, encoder, answer = graph.nodes
    query.config["failure_action"] = "error_output"
    encoder.inputs = [_input("value", "rows", "lookup", schema=query.outputs[0].value_schema)]
    graph.nodes = [query, encoder, answer]
    graph.control_edges = [
        GraphIntentControlEdgeV3(source_ref="lookup", outcome_ref=outcome, target_ref="encode")
        for outcome in ("success", "error")
    ] + [GraphIntentControlEdgeV3(source_ref="encode", target_ref="answer")]
    issues, facts = _facts(req, snap, graph)
    assert len(issues) == len(facts) == 1
    fact = facts[0]
    assert fact["reason"] == "source_result_unavailable"
    assert fact["target_scenario_count"] == 2 and fact["violating_scenario_count"] == 1
    assert fact["counterexample"] == {
        "choices": {"lookup": "error"}, "source_reached": True,
        "target_reached": True, "source_value_available": False,
    }


@pytest.mark.parametrize("error_output", [False, True])
def test_safe_common_producer_preserves_null_error_and_mutually_exclusive_paths(error_output):
    req, snap, graph = _branch_case(error_output=error_output)
    fixed = _apply(req, graph, _common_producer_patch())
    assert _facts(req, snap, fixed) == ([], [])
    report = analyze_control_flow(fixed)
    assert report["scenario_count"] == (4 if error_output else 3)
    for scenario in report["scenarios"]:
        if scenario["error_sources"]:
            assert not {"write", "serialize_query"} & set(scenario["reached"])
        else:
            assert "serialize_query" in scenario["reached"]
            assert ("write" in scenario["reached"]) == (scenario["choices"]["score_gate"] == "matched")
            assert len(scenario["success_sources"]) == 1
    before = {node.ref: node for node in graph.nodes}
    for node in fixed.nodes:
        assert node.model_dump(mode="json") == before[node.ref].model_dump(mode="json")
    compiled = compile_case(fixed, snap, req)
    assert workflow_semantic_checksum(compiled) == workflow_semantic_checksum(
        compile_case(decompile_candidate_to_graph_intent(compiled), snap, req),
    )


def test_branch_local_producer_is_also_valid_not_forced_into_one_linear_solution():
    req, snap, graph = _branch_case()
    answer_config = deepcopy(next(node for node in graph.nodes if node.ref == "answer").config)
    answer_config["task_input"] = answer_config["task_input"].replace("{{query_json}}", "{{low_query_json}}")
    operations = [
        {"op": "add_node", "ref": "serialize_low", "kind": "json_serialize", "title": "低分查询证据",
         "config": {"format": "compact"}, "output_variables": {"json": "low_query_json"}},
        {"op": "connect_data", "source_ref": "lookup", "source_port": "result", "target_ref": "serialize_low", "target_port": "value"},
        {"op": "disconnect_data", "source_ref": "serialize_query", "source_port": "json", "target_ref": "answer", "target_port": "task"},
        {"op": "connect_data", "source_ref": "serialize_low", "source_port": "json", "target_ref": "answer", "target_port": "task"},
        {"op": "update_node", "ref": "answer", "config": answer_config},
        {"op": "disconnect_control", "source_ref": "encode", "target_ref": "answer"},
        {"op": "connect_control", "source_ref": "encode", "target_ref": "serialize_low"},
        {"op": "connect_control", "source_ref": "serialize_low", "target_ref": "answer"},
    ]
    fixed = _apply(req, graph, operations)
    assert _facts(req, snap, fixed) == ([], [])
    compiled = compile_case(fixed, snap, req)
    assert workflow_semantic_checksum(compiled) == workflow_semantic_checksum(
        compile_case(decompile_candidate_to_graph_intent(compiled), snap, req),
    )
    assert next(node for node in fixed.nodes if node.ref == "write").config == graph.nodes[1].config


def test_guessing_a_control_edge_does_not_get_an_automatic_repair_or_pass():
    req, snap, graph = _branch_case()
    frozen = graph.model_dump(mode="json")
    patched = _apply(req, graph, [{"op": "connect_control", "source_ref": "serialize_query", "target_ref": "answer"}])
    issues, facts = _facts(req, snap, patched)
    assert any("reaches 2 terminals" in issue for issue in issues), issues
    assert any(item["code"] == "DATA_PATH_NOT_GUARANTEED" for item in facts)
    with pytest.raises(ValueError):
        compile_case(patched, snap, req)
    assert graph.model_dump(mode="json") == frozen


def test_dependency_diagnostics_are_bounded_and_omissions_are_explicit():
    req, snap, graph = _branch_case()
    issues, facts = _facts(req, snap, graph)
    assert len(facts) == 2
    supplied = tuple(facts * 35)
    contract = _graph_patch_repair_contract(req, graph, issues, snapshot=snap, control_dependency_issues=supplied)
    assert len(contract["control_dependency_issues"]) == 64
    assert contract["omitted_control_dependency_issue_count"] == 6
    assert contract["control_dependency_issues"] == _graph_patch_repair_contract(
        req, graph, issues, snapshot=snap, control_dependency_issues=tuple(reversed(supplied)),
    )["control_dependency_issues"]


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["control_patch", "config_noop", "empty"])
async def test_one_repair_uses_proof_without_changing_call_budget_or_approval(tmp_path, repair):
    from server.skills.draft_store import WorkspaceSkillDraftStore
    from server.xpert_runtime.authoring_service import AuthoringService
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
    from server.xperts.store import XpertStore
    from server.xperts.validation import validate_xpert_definition

    req, snap, graph = _branch_case()
    operations = {"control_patch": _common_producer_patch(), "empty": [], "config_noop": [
        {"op": "update_node", "ref": "serialize_query", "config": {"format": "compact"}},
    ]}[repair]
    responses = [_plan().model_dump_json(), graph.model_dump_json(), json.dumps({"operations": operations})]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        if len(calls) == 3:
            payload = json.loads(args[2])
            facts = payload["repair_contract"]["dependency_review"]["control_dependency_issues"]
            assert any(item["code"] == "DATA_PATH_NOT_GUARANTEED" for item in facts)
            assert any(item["code"] == "DATA_NOT_CONTROL_ANCESTOR" for item in facts)
        return responses[len(calls) - 1]

    def preflight(candidate):
        return validate_xpert_definition(candidate), candidate.draft.workflow, []

    authoring = AuthoringService(AuthoringProposalStore(tmp_path / "runtime"), XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"), xpert_preflight=preflight)
    service = LegacyGraphReplayService(authoring_service=authoring, preflight=preflight, completion=completion)
    result = await service.generate(req, snap)
    assert len(calls) == 3
    assert result.validation["valid"] is (repair == "control_patch")
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    if repair != "control_patch":
        report = proposal.payload["meta_planner_report"]
        assert any(item["code"] == "DATA_UNREACHABLE" for item in report["generation_diagnostics"][-1]["issues"])
