from copy import deepcopy
import json

import pytest

from server.meta_agent.control_flow import analyze_control_flow
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, workflow_semantic_checksum
from server.meta_agent.meta_planner_v2 import validate_blueprint_authorization
from server.meta_agent.schemas import GraphIntentNodeV3
from server.tests.test_meta_planner_control_dependency_repair import _branch_case, _common_producer_patch
from server.tests.test_meta_planner_control_flow import _input
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case
from server.tests.test_meta_planner_repair_context import _apply, _prompt, offline_only


def joint_case():
    req, snap, graph = _branch_case(error_output=True)
    req.scope.allowed_node_kinds = ["input", "output", "workflow_agent", "condition", "terminate_error",
                                    "json_serialize", "data_table_query", "data_table_update"]
    by_ref = {node.ref: node for node in graph.nodes}
    value = GraphIntentNodeV3(ref="write_values", kind="json_serialize", title="无效写值来源",
        config={"format": "compact"}, inputs=[_input("value", "user_input", "input", "user_input")],
        outputs=[{"port": "json", "variable": "values_json", "value_schema": {"type": "string"}}])
    by_ref["write"].config.update(value_source="input", values=None)
    by_ref["write"].inputs.append(_input("values", "values_json", value.ref, "value", schema={"type": "object"}))
    graph.nodes = [node for node in graph.nodes if node.ref not in {"found", "unchanged"}] + [value]
    graph.control_edges = [type(graph.control_edges[0])(source_ref=s, outcome_ref=o, target_ref=t) for s, o, t in (
        ("lookup", "success", "score_gate"), ("lookup", "error", "stop"),
        ("score_gate", "matched", "write"), ("score_gate", "unmatched", "answer"),
        ("write_values", "success", "write"), ("write", "success", "encode"),
        ("encode", "success", "answer"), ("serialize_query", "success", "answer"),
    )]
    graph.final_output.sources = [graph.final_output.sources[0]]
    return req, snap, graph


def test_joint_review_contains_router_terminal_and_source_obligations_together():
    req, snap, graph = joint_case()
    before = graph.model_dump(mode="json")
    payload, _ = _prompt(req, snap, graph)
    review = payload["repair_contract"]["dependency_review"]
    assert {"lookup", "score_gate", "stop", "write", "write_values", "serialize_query", "answer"} <= {
        item["node_ref"] for item in review["nodes"]}
    paths = review["control_path_issues"]
    router = next(item for item in paths if item["code"] == "ROUTER_INPUT_DOMAIN_INVALID")
    assert router["node_ref"] == "score_gate"
    assert router["error_code"] == "CONDITION_FIELD_REQUIRES_OBJECT"
    assert router["input_contract"]["source_nullable"] is True
    assert router["input_contract"]["source_type"] == "object"
    terminal = next(item for item in paths if item["code"] == "CONTROL_TERMINAL_COUNT"
                    and item["counterexample"]["choices"].get("lookup") == "error")
    assert terminal["counterexample"]["success_sources"] == ["answer"]
    assert terminal["counterexample"]["error_sources"] == ["stop"]
    assert {"serialize_query", "write_values"} <= set(terminal["counterexample"]["reached_roots"])
    assert review["control_dependency_issues"]
    assert {"DATA_UNKNOWN_SOURCE_PORT", "WRITE_VALUES_PRODUCER_INVALID"} <= {
        item["code"] for item in payload["repair_contract"]["source_contract_issues"]}
    assert "operations" not in review
    assert graph.model_dump(mode="json") == before
    with pytest.raises(ValueError):
        compile_case(graph, snap, req)


def test_joint_facts_are_presentation_order_independent_and_do_not_include_values():
    req, snap, graph = joint_case()
    first, _ = _prompt(req, snap, graph)
    graph.nodes.reverse()
    graph.control_edges.reverse()
    second, _ = _prompt(req, snap, graph)
    paths = first["repair_contract"]["dependency_review"]["control_path_issues"]
    assert paths == second["repair_contract"]["dependency_review"]["control_path_issues"]
    raw = json.dumps(paths)
    for forbidden in ("DEMO", "values_json", "role_prompt", "witness_value", "record_id", "schema_checksum"):
        assert forbidden not in raw


def test_local_port_fix_does_not_remove_joint_path_obligations():
    req, snap, graph = joint_case()
    writer = next(node for node in graph.nodes if node.ref == "write")
    writer.inputs[-1].source_port = "json"
    payload, _ = _prompt(req, snap, graph)
    assert payload["repair_contract"]["dependency_review"]["control_path_issues"]
    assert any(item["code"] == "WRITE_VALUES_PRODUCER_INVALID" for item in payload["repair_contract"]["source_contract_issues"])
    with pytest.raises(ValueError):
        compile_case(graph, snap, req)


def test_legal_branches_keep_four_scenarios_and_roundtrip_without_repair_actions():
    req, snap, graph = _branch_case(error_output=True)
    graph = _apply(req, graph, _common_producer_patch())
    issues = validate_blueprint_authorization(req, _plan(), graph, snap)
    assert issues == []
    payload, _ = _prompt(req, snap, graph)
    assert payload["repair_contract"]["dependency_review"]["control_path_issues"] == []
    compiled = compile_case(graph, snap, req)
    assert workflow_semantic_checksum(compile_case(decompile_candidate_to_graph_intent(compiled), snap, req)) == workflow_semantic_checksum(compiled)
    scenarios = analyze_control_flow(graph)["scenarios"]
    assert len(scenarios) == 4
    for scenario in scenarios:
        assert len(scenario["success_sources"]) + len(scenario["error_sources"]) == 1
        if scenario["error_sources"]:
            assert not {"write", "answer", "unchanged"} & set(scenario["reached"])


def test_path_diagnostics_do_not_trust_denied_resource_schema():
    req, snap, graph = joint_case()
    req.scope.data_table_ids = []
    payload, _ = _prompt(req, snap, graph)
    assert payload["repair_contract"]["dependency_review"]["control_path_issues"] == []
    assert any("权威输入类型尚未解析" in issue for issue in payload["validation_issues"])


def complete_joint_patch(graph):
    req, _, target = _branch_case(error_output=True)
    target = _apply(req, target, _common_producer_patch())
    target_nodes = {node.ref: node for node in target.nodes}
    operations = [{"op": "disconnect_control", **edge.model_dump(mode="json")}
                  for edge in graph.control_edges]
    operations += [
        {"op": "disconnect_data", "source_ref": "write_values", "source_port": "value",
         "target_ref": "write", "target_port": "values"},
        {"op": "disconnect_data", "source_ref": "input", "source_port": "user_input",
         "target_ref": "write_values", "target_port": "value"},
        {"op": "remove_node", "ref": "write_values"},
        {"op": "update_node", "ref": "write", "config": deepcopy(target_nodes["write"].config)},
    ]
    for ref in ("found", "unchanged"):
        node = target_nodes[ref]
        operations.append({"op": "add_node", "ref": ref, "kind": node.kind, "title": node.title,
                           "config": deepcopy(node.config), "task_ids": list(node.task_ids),
                           "output_variables": {output.port: output.variable for output in node.outputs}})
        operations.extend({"op": "connect_data", "source_ref": binding.source_ref, "source_port": binding.source_port,
                           "target_ref": ref, "target_port": binding.port} for binding in node.inputs)
    operations.append({"op": "set_final_outputs", "sources": [source.model_dump(mode="json") for source in target.final_output.sources]})
    operations.extend({"op": "connect_control", **edge.model_dump(mode="json")} for edge in target.control_edges)
    return operations


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["complete", "port_only", "empty"])
async def test_one_joint_patch_repairs_all_obligations_or_stays_invalid_with_three_calls(tmp_path, repair):
    from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
    from server.skills.draft_store import WorkspaceSkillDraftStore
    from server.xpert_runtime.authoring_service import AuthoringService
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
    from server.xperts.store import XpertStore
    from server.xperts.validation import validate_xpert_definition

    req, snap, graph = joint_case()
    scope = req.scope.model_dump(mode="json")
    operations = {"complete": complete_joint_patch(graph), "empty": [], "port_only": [
        {"op": "disconnect_data", "source_ref": "write_values", "source_port": "value", "target_ref": "write", "target_port": "values"},
        {"op": "connect_data", "source_ref": "write_values", "source_port": "json", "target_ref": "write", "target_port": "values"},
    ]}[repair]
    responses = [_plan().model_dump_json(), graph.model_dump_json(), json.dumps({"operations": operations})]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        if len(calls) == 3:
            payload = json.loads(args[2])
            assert payload["repair_contract"]["dependency_review"]["control_path_issues"]
            assert {"DATA_UNKNOWN_SOURCE_PORT", "WRITE_VALUES_PRODUCER_INVALID"} <= {
                item["code"] for item in payload["repair_contract"]["source_contract_issues"]}
            assert payload["authorized_scope"] == scope
        return responses[len(calls) - 1]

    def preflight(candidate):
        return validate_xpert_definition(candidate), candidate.draft.workflow, []

    authoring = AuthoringService(AuthoringProposalStore(tmp_path / "runtime"), XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"), xpert_preflight=preflight)
    service = LegacyGraphReplayService(authoring_service=authoring, preflight=preflight, completion=completion)
    result = await service.generate(req, snap)
    assert len(calls) == 3
    assert result.validation["valid"] is (repair == "complete"), json.dumps(result.validation, ensure_ascii=False)
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    assert req.scope.model_dump(mode="json") == scope
    if repair == "complete":
        fixed = _apply(req, graph, operations)
        assert validate_blueprint_authorization(req, _plan(), fixed, snap) == []
        assert len(analyze_control_flow(fixed)["scenarios"]) == 4


def test_joint_path_facts_have_explicit_budget_and_omission_count():
    from server.meta_agent.meta_planner_v2 import _graph_patch_repair_contract
    req, snap, graph = joint_case()
    payload, _ = _prompt(req, snap, graph)
    sample = payload["repair_contract"]["dependency_review"]["control_path_issues"][0]
    result = _graph_patch_repair_contract(req, graph, [], snapshot=snap, control_path_issues=tuple([sample] * 70))
    assert len(result["control_path_issues"]) == 64
    assert result["omitted_control_path_issue_count"] == 6
