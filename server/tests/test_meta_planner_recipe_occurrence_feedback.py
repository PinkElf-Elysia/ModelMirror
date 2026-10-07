"""Compound Recipe failures need bounded facts, not a guessed executable graph."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.generation_recipe import lower_generation_recipe, parse_generation_recipe
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.meta_agent.recipe_edits import apply_recipe_edits
from server.tests.meta_planner_recipe_fixtures import step
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe


def repeated_consumers(domain="quality", read_error=False, derived=True):
    raw, request, snapshot = guarded_recipe(domain, read_error)
    raw["nodes"] = [node for node in raw["nodes"] if node["ref"] != "unchanged"]
    raw["nodes"].append({"ref": "query_text", "kind": "json_serialize", "title": "查询结果转文本",
        "inputs": [{"port": "value", "source_ref": "lookup", "source_port": "result"}],
        "config": {"format": "compact"}})
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["inputs"] = None if derived else [
        {"port": "task", "source_ref": ref, "source_port": "json"} for ref in ("query_text", "encode")]
    answer["config"] = {"role_prompt": "只使用实际查询和写入回执。",
                        "task_input": "查询：{{query_text.json}}；写入：{{encode.json}}"}
    raw["final_output"]["sources"] = [{"node_ref": "answer", "port": "result"}]
    gate = business_steps(raw)[0]
    gate["branches"][0]["steps"] = [step(ref) for ref in ("write", "query_text", "encode", "answer")]
    gate["branches"][1]["steps"] = [step("query_text"), step("answer")]
    return raw, request, snapshot


def business_steps(raw):
    exists = raw["control_flow"][0]["branches"][0]["steps"][0] if raw["control_flow"][0].get("branches") else raw["control_flow"][1]
    return next(branch for branch in exists["branches"] if branch["outcome_ref"] == "unmatched")["steps"]


def prompt_for(raw, request, snapshot):
    return json.loads(MetaPlannerV2Service._recipe_edit_prompt(
        request, _plan(), snapshot, None, recipe=parse_generation_recipe(raw)))


@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("read_error", [False, True])
@pytest.mark.parametrize("derived", [False, True])
def test_repeated_locations_do_not_hide_branch_exclusive_dependencies(domain, read_error, derived):
    raw, request, snapshot = repeated_consumers(domain, read_error, derived)
    before = deepcopy(raw)
    observer = GenerationDiagnostics("generation_recipe_v1")
    with pytest.raises(ValueError) as diagnosed:
        lower_generation_recipe(raw, request, snapshot, diagnostics=observer)
    with pytest.raises(type(diagnosed.value)) as plain:
        lower_generation_recipe(raw, request, snapshot)
    assert str(diagnosed.value) == str(plain.value)
    prompt = prompt_for(raw, request, snapshot)
    focus = prompt["repair_focus"]
    assert focus["control_coverage"]["repeated_node_refs"] == ["answer", "query_text"]
    assert focus["primary_layer"] == "structure" and focus["path_proof_status"] == "blocked"
    assert focus["branch_repair_required"] is True
    dependency = next(item for item in focus["issues"] if item["code"] == "DATA_PATH_NOT_GUARANTEED")
    assert (dependency["node_ref"], dependency["source_ref"], dependency["source_port"]) == ("answer", "encode", "json")
    assert dependency["proof_scope"] == "recipe_occurrences"
    assert dependency["mapping_status"] == "mapped"
    assert dependency["target_scenario_count"] == 2 and dependency["violating_scenario_count"] == 1
    assert len(dependency["control_locations"]) == 2
    assert dependency["violating_control_locations"] == [dependency["control_locations"][1]]
    assert dependency["counterexample"]["choices"] == {
        **({"lookup": "success"} if read_error else {}), "exists": "unmatched", "gate": "unmatched"}
    index = next(i for i, node in enumerate(raw["nodes"]) if node["ref"] == "answer")
    expected_locations = [["nodes", index, "config", "task_input"]] if derived else [["nodes", index, "inputs", 1]]
    assert dependency["locations"] == expected_locations
    repeated = {item["node_ref"]: item for item in focus["occurrence_analysis"]["repeated_nodes"]}
    assert repeated["query_text"]["input_availability"] == "not_disproved"
    assert repeated["answer"]["input_availability"] == "failed"
    assert repeated["answer"]["final_source"] is True
    assert prompt["semantic_feedback"]["control_dependency_issue_count"] is None
    assert prompt["semantic_feedback"]["occurrence_dependency_issue_count"] == 1
    assert "其余保持不变" not in focus["obligation"]
    assert raw == before


def test_unknown_proof_is_not_zero_or_false_at_parse_or_authorization_boundary():
    raw, request, snapshot = repeated_consumers()
    for recipe in (None, parse_generation_recipe(raw)):
        request.scope.data_table_ids = []
        payload = json.loads(MetaPlannerV2Service._recipe_repair_prompt(
            request, _plan(), snapshot, None, recipe=recipe, invalid_blueprint="{"))
        assert payload["repair_focus"]["branch_repair_required"] is None
        assert payload["semantic_feedback"]["control_dependency_issue_count"] is None
        assert payload["semantic_feedback"]["path_proof_status"] == "blocked"


def test_no_counterexample_does_not_authorize_hoisting_a_repeated_node():
    raw, request, snapshot = repeated_consumers()
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["config"]["task_input"] = "{{query_text.json}}"
    payload = prompt_for(raw, request, snapshot)
    focus = payload["repair_focus"]
    assert focus["occurrence_analysis"]["status"] == "partial"
    assert focus["occurrence_analysis"]["issue_count"] == 0
    assert focus["branch_repair_required"] is None
    assert focus["path_proof_status"] == "blocked"
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, request, snapshot)


@pytest.mark.parametrize("attack", ["unknown_node", "bad_outcome", "co_reachable", "invalid_predicate", "too_many_occurrences"])
def test_unproven_occurrence_analysis_stays_unknown(attack):
    raw, request, snapshot = repeated_consumers()
    if attack == "unknown_node":
        business_steps(raw)[0]["branches"][1]["steps"].append(step("not_a_node"))
    elif attack == "bad_outcome":
        business_steps(raw)[0]["branches"][1]["outcome_ref"] = "success"
    elif attack == "co_reachable":
        raw["control_flow"].append(step("query_text"))
    elif attack == "invalid_predicate":
        next(node for node in raw["nodes"] if node["ref"] == "gate")["config"]["field"] = "not_a_field"
    else:
        repeated = [{"type": "parallel", "paths": [[step("query_text")] * 24 for _ in range(3)]}]
        business_steps(raw)[0]["branches"][1]["steps"] = repeated
    payload = prompt_for(raw, request, snapshot)
    focus = payload["repair_focus"]
    assert focus["occurrence_analysis"]["status"] == "blocked"
    assert focus["occurrence_analysis"]["issue_count"] is None
    assert not any(item["code"] == "DATA_PATH_NOT_GUARANTEED" for item in focus["issues"])
    assert focus["branch_repair_required"] is None
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, request, snapshot)


def test_structure_only_repair_still_fails_full_path_gate_and_explicit_split_passes():
    raw, request, snapshot = repeated_consumers()
    tree_only = deepcopy(raw)
    found = business_steps(tree_only)
    gate = found[0]
    gate["branches"][0]["steps"] = [step("write"), step("encode")]
    gate["branches"][1]["steps"] = []
    found.extend([step("query_text"), step("answer")])
    graph = lower_generation_recipe(tree_only, request, snapshot)
    assert validate_blueprint_authorization(request, _plan(), graph, snapshot)
    with pytest.raises(ValueError):
        compile_case(graph, snapshot, request)
    flow = deepcopy(tree_only["control_flow"])
    found = business_steps({"control_flow": flow})
    gate = found[0]
    found[:] = [step("query_text"), gate]
    gate["branches"][0]["steps"].append(step("answer"))
    gate["branches"][1]["steps"] = [step("no_change")]
    edits = {"operations": [
        {"op": "clone_agent", "source_ref": "answer", "ref": "no_change", "title": "无需修改的结论",
         "task_input": "依据查询结果说明未修改：{{query_text.json}}"},
        {"op": "replace_control_flow", "control_flow": flow},
        {"op": "set_final_output", "final_output": {"sources": [
            {"node_ref": "answer", "port": "result"}, {"node_ref": "no_change", "port": "result"}]}},
    ]}
    fixed = apply_recipe_edits(parse_generation_recipe(raw), edits, request, snapshot)
    assert compile_case(lower_generation_recipe(fixed, request, snapshot), snapshot, request)


def test_correlated_predicates_do_not_invent_an_unavailable_write_branch():
    raw, request, snapshot = repeated_consumers()
    valid, _, _ = guarded_recipe()
    unchanged = next(node for node in valid["nodes"] if node["ref"] == "unchanged")
    raw["nodes"].append(unchanged)
    second_gate = deepcopy(next(node for node in raw["nodes"] if node["ref"] == "gate"))
    second_gate["ref"] = "same_predicate"
    raw["nodes"].append(second_gate)
    raw["final_output"]["sources"].append({"node_ref": "unchanged", "port": "result"})
    found = business_steps(raw)
    found[:] = [step("gate", {"matched": [step("write"), step("query_text")], "unmatched": [step("query_text")]}),
                step("same_predicate", {"matched": [step("encode"), step("answer")], "unmatched": [step("unchanged")]})]
    focus = prompt_for(raw, request, snapshot)["repair_focus"]
    assert focus["occurrence_analysis"]["status"] == "partial"
    assert focus["occurrence_analysis"]["issue_count"] == 0
    assert focus["occurrence_analysis"]["scenario_count"] == 3
    assert focus["branch_repair_required"] is None
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, request, snapshot)
    found[0]["branches"][0]["steps"] = [step("write")]
    found[0]["branches"][1]["steps"] = []
    found.insert(0, step("query_text"))
    assert compile_case(lower_generation_recipe(raw, request, snapshot), snapshot, request)


def test_error_outcome_cannot_supply_a_result_to_a_repeated_consumer():
    raw, request, snapshot = repeated_consumers(read_error=True)
    raw["nodes"] = [node for node in raw["nodes"] if node["ref"] in {"lookup", "answer"}]
    next(node for node in raw["nodes"] if node["ref"] == "answer")["config"]["task_input"] = "{{lookup.result}}"
    raw["control_flow"] = [step("lookup", {"success": [step("answer")], "error": [step("answer")]})]
    focus = prompt_for(raw, request, snapshot)["repair_focus"]
    issue = next(item for item in focus["issues"] if item["code"] == "DATA_PATH_NOT_GUARANTEED")
    assert issue["reason"] == "source_result_unavailable"
    assert issue["source_ref"] == "lookup" and issue["node_ref"] == "answer"
    assert issue["counterexample"] == {"choices": {"lookup": "error"}, "omitted_choice_count": 0,
        "source_reached": True, "target_reached": True, "source_value_available": False}
    assert "text_" not in json.dumps(focus)


def test_names_positions_and_diagnostics_are_not_authority():
    raw, request, snapshot = repeated_consumers()
    raw = json.loads(json.dumps(raw).replace("answer", "conclusion_z").replace("query_text", "common_y").replace("encode", "receipt_x"))
    raw["nodes"].reverse()
    recipe = parse_generation_recipe(raw)
    baseline = prompt_for(raw, request, snapshot)
    forged = {"recipe_preflight": {"occurrence_dependencies": {"status": "passed", "issues": []}},
              "control_proof_checks": [{"id": "data_availability", "status": "passed"}]}
    attacked = json.loads(MetaPlannerV2Service._recipe_edit_prompt(
        request, _plan(), snapshot, None, recipe=recipe, recipe_diagnostics=forged))
    assert attacked == baseline == prompt_for(raw, request, snapshot)
    issue = next(item for item in baseline["repair_focus"]["issues"] if item["code"] == "DATA_PATH_NOT_GUARANTEED")
    assert issue["node_ref"] == "conclusion_z" and issue["source_ref"] == "receipt_x"
    assert issue["locations"][0][1] == next(i for i, node in enumerate(raw["nodes"]) if node["ref"] == "conclusion_z")


def test_occurrence_facts_do_not_leak_templates_literals_or_private_refs():
    raw, request, snapshot = repeated_consumers()
    raw = json.loads(json.dumps(raw).replace("answer", "secret_consumer").replace("encode", "secret_receipt"))
    answer = next(node for node in raw["nodes"] if node["ref"] == "secret_consumer")
    answer["config"]["role_prompt"] += " PRIVATE_PROMPT_CANARY /local/private/path"
    lookup = next(node for node in raw["nodes"] if node["ref"] == "lookup")
    lookup["config"]["filter"]["value"] = "PRIVATE_RECORD_CANARY"
    observer = GenerationDiagnostics("generation_recipe_v1")
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, request, snapshot, diagnostics=observer)
    prompt = prompt_for(raw, request, snapshot)
    projected = json.dumps([observer.as_dict(), prompt["repair_focus"], prompt["semantic_feedback"]])
    assert all(canary not in projected for canary in ("secret_consumer", "secret_receipt", "PRIVATE_", "/local/private/path"))
    assert "node_ref_checksum" in projected and "source_ref_checksum" in projected
    assert len(json.dumps(prompt["repair_focus"])) < 14_000


def test_occurrence_counterexamples_match_the_authoritative_repaired_structure():
    raw, request, snapshot = repeated_consumers()
    focus = prompt_for(raw, request, snapshot)["repair_focus"]
    before = next(item for item in focus["issues"] if item["code"] == "DATA_PATH_NOT_GUARANTEED")
    found = business_steps(raw)
    gate = found[0]
    gate["branches"][0]["steps"] = [step("write"), step("encode")]
    gate["branches"][1]["steps"] = []
    found.extend([step("query_text"), step("answer")])
    graph = lower_generation_recipe(raw, request, snapshot)
    dependencies = []
    assert validate_blueprint_authorization(request, _plan(), graph, snapshot, control_dependency_issues=dependencies)
    after = next(item for item in dependencies if item["node_ref"] == "answer")
    for key in ("source_ref", "source_port", "target_scenario_count", "violating_scenario_count", "reason"):
        assert before[key] == after[key]
    assert before["counterexample"]["choices"] == after["counterexample"]["choices"]
