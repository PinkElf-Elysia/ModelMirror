"""Recipe-source repair evidence; no provider or live-table execution."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.generation_recipe import lower_generation_recipe, parse_generation_recipe
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.meta_planner_recipe_fixtures import step
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
from server.tests.test_meta_planner_recipe_text_inputs import text_recipe


def broken_common_consumer(domain="quality", error_output=False):
    raw, req, snap = guarded_recipe(domain, error_output)
    raw = text_recipe(raw)
    raw["nodes"] = [node for node in raw["nodes"] if node["ref"] != "unchanged"]
    raw["final_output"]["sources"] = [{"node_ref": "answer", "port": "result"}]
    exists = raw["control_flow"][0]["branches"][0]["steps"][0] if error_output else raw["control_flow"][1]
    found = next(branch for branch in exists["branches"] if branch["outcome_ref"] == "unmatched")
    gate = found["steps"][0]
    next(branch for branch in gate["branches"] if branch["outcome_ref"] == "matched")["steps"] = [step("write")]
    next(branch for branch in gate["branches"] if branch["outcome_ref"] == "unmatched")["steps"] = []
    found["steps"].append(step("answer"))
    return raw, req, snap


def prompt_for(raw, req, snap):
    return json.loads(MetaPlannerV2Service._recipe_repair_prompt(
        req, _plan(), snap, None, recipe=parse_generation_recipe(raw)))


@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("error_output", [False, True])
def test_common_consumer_feedback_names_original_template_and_counterexample(domain, error_output):
    raw, req, snap = broken_common_consumer(domain, error_output)
    before = deepcopy(raw)
    prompt = prompt_for(raw, req, snap)
    focus = prompt["repair_focus"]
    assert focus["issue_count"] == 1 and focus["omitted_issue_count"] == 0
    assert focus["path_proof_status"] == "failed"
    issue = focus["issues"][0]
    index = next(i for i, node in enumerate(raw["nodes"]) if node["ref"] == "answer")
    assert issue["code"] == "DATA_PATH_NOT_GUARANTEED"
    assert issue["mapping_status"] == "mapped"
    assert issue["node_ref"] == "answer"
    assert issue["locations"] == [["nodes", index, "config", "task_input"]]
    assert (issue["source_ref"], issue["source_port"]) == ("write", "result")
    assert issue["counterexample"]["choices"]["gate"] == "unmatched"
    assert issue["counterexample"]["source_reached"] is False
    assert issue["counterexample"]["target_reached"] is True
    assert issue["target_scenario_count"] == 2 and issue["violating_scenario_count"] == 1
    assert issue["control_location"][0] == "control_flow"
    assert "text_" not in json.dumps(focus)
    assert list(prompt).index("repair_focus") < list(prompt).index("invalid_generation")
    assert raw == before
    graph = lower_generation_recipe(raw, req, snap)
    assert validate_blueprint_authorization(req, _plan(), graph, snap)


def test_prompt_compacts_telemetry_not_authority_or_recipe():
    raw, req, snap = broken_common_consumer()
    observer = GenerationDiagnostics("generation_recipe_v1")
    service = MetaPlannerV2Service(authoring_service=None, preflight=lambda _: pytest.fail("invalid graph"))
    result = service._compile_and_validate(request=req, plan=_plan(), raw_blueprint=json.dumps(raw),
        snapshot=snap, target=None, diagnostics=observer, require_recipe=True)
    report = observer.as_dict()
    prompt = json.loads(service._recipe_repair_prompt(req, _plan(), snap, None,
        recipe=parse_generation_recipe(raw), issues=result[3], recipe_diagnostics=report))
    initial = json.loads(service._recipe_prompt(req, _plan(), snap, None))
    for key in ("required_schema", "node_contracts", "resource_contract", "authorized_scope", "task_constraints"):
        assert prompt[key] == initial[key]
    assert prompt["invalid_generation"] == parse_generation_recipe(raw).model_dump(mode="json")
    assert report["data_bindings"] and report["router_inputs"]
    assert "data_bindings" not in prompt["lowering_diagnostics"]
    assert "router_inputs" not in prompt["lowering_diagnostics"]
    assert "control_proof_checks" in prompt["lowering_diagnostics"]
    assert prompt["semantic_feedback"]["control_dependency_issue_count"] == 1
    assert "control_dependency_issues" not in prompt["semantic_feedback"]
    assert len(json.dumps(prompt["lowering_diagnostics"])) < len(json.dumps(report))
    assert observer.as_dict() == report


def test_source_map_is_derived_fresh_and_does_not_change_compilation():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    before = deepcopy(raw)
    origins = {("forged", 0): {"node_ref": "answer"}}
    graph = lower_generation_recipe(raw, req, snap, input_origins=origins)
    assert ("forged", 0) not in origins
    assert graph == lower_generation_recipe(raw, req, snap)
    assert compile_case(graph, snap, req)
    helper = next(node for node in graph.nodes if node.kind == "json_serialize" and node.inputs[0].source_ref == "write")
    assert origins[(helper.ref, 0)]["node_ref"] == "answer"
    assert origins[(helper.ref, 0)]["source_ref"] == "write"
    assert raw == before
    req.scope.data_table_ids = []
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, req, snap, input_origins=origins)
    assert origins == {}


@pytest.mark.parametrize("field", ["role_prompt", "task_input"])
def test_source_location_tracks_actual_template_fields_without_body(field):
    raw, req, snap = broken_common_consumer()
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["config"] = {"role_prompt": "PRIVATE_BODY_CANARY", "task_input": "{{lookup.result}}"}
    answer["config"][field] += " {{write.result}} {{write.result}}"
    focus = prompt_for(raw, req, snap)["repair_focus"]
    assert focus["issues"][0]["locations"][0][-1] == field
    assert len(focus["issues"][0]["locations"]) == 1
    assert "PRIVATE_BODY_CANARY" not in json.dumps(focus)


def test_valid_branch_local_recipe_has_no_dependency_repair():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    before = deepcopy(raw)
    prompt = prompt_for(raw, req, snap)
    assert prompt["repair_focus"]["issues"] == []
    assert prompt["repair_focus"]["path_proof_status"] == "passed"
    assert prompt["semantic_feedback"]["path_proof_status"] == "passed"
    assert compile_case(lower_generation_recipe(raw, req, snap), snap, req)
    assert raw == before


@pytest.mark.parametrize("blocked_by", ["parse", "authorization"])
def test_empty_feedback_does_not_claim_proof_when_prerequisite_is_blocked(blocked_by):
    raw, req, snap = broken_common_consumer()
    recipe = parse_generation_recipe(raw) if blocked_by == "authorization" else None
    if blocked_by == "authorization":
        req.scope.data_table_ids = []
    prompt = json.loads(MetaPlannerV2Service._recipe_repair_prompt(
        req, _plan(), snap, None, recipe=recipe, invalid_blueprint="{"))
    assert prompt["repair_focus"]["issues"] == []
    assert prompt["repair_focus"]["path_proof_status"] == "blocked"
    assert prompt["semantic_feedback"]["path_proof_status"] == "blocked"


@pytest.mark.parametrize("placement", ["top", "node", "config"])
def test_model_cannot_supply_compiler_origins(placement):
    raw, req, snap = broken_common_consumer()
    owner = raw if placement == "top" else raw["nodes"][0] if placement == "node" else raw["nodes"][0]["config"]
    owner["input_origins"] = {"text_fake": {"node_ref": "answer"}}
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, req, snap)


@pytest.mark.parametrize("derived", [False, True], ids=["explicit", "direct-text"])
def test_non_helper_failures_keep_their_real_input_or_template_location(derived):
    raw, req, snap = guarded_recipe()
    consumer = next(node for node in raw["nodes"] if node["ref"] == "unchanged")
    consumer["inputs"] = None if derived else [{"port": "task", "source_ref": "encode", "source_port": "json"}]
    consumer["config"]["task_input"] = "{{encode.json}}"
    issue = prompt_for(raw, req, snap)["repair_focus"]["issues"][0]
    index = raw["nodes"].index(consumer)
    assert issue["node_ref"] == "unchanged" and issue["source_ref"] == "encode"
    assert issue["locations"] == [["nodes", index, *(('config', 'task_input') if derived else ('inputs', 0))]]


def test_repeated_source_in_both_fields_has_two_locations_not_duplicate_inputs():
    raw, req, snap = broken_common_consumer()
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["config"]["role_prompt"] += " {{write.result}} {{write.result}}"
    issue = prompt_for(raw, req, snap)["repair_focus"]["issues"][0]
    assert [location[-1] for location in issue["locations"]] == ["role_prompt", "task_input"]
    assert issue["target_scenario_count"] == 2


def test_read_error_path_is_a_missing_value_not_a_missing_execution():
    raw, req, snap = broken_common_consumer(error_output=True)
    raw["nodes"] = [node for node in raw["nodes"] if node["ref"] in {"lookup", "answer"}]
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["config"]["task_input"] = "{{lookup.result}}"
    raw["control_flow"] = [step("lookup", {"success": [], "error": []}), step("answer")]
    issue = prompt_for(raw, req, snap)["repair_focus"]["issues"][0]
    assert issue["mapping_status"] == "mapped" and issue["node_ref"] == "answer"
    assert issue["reason"] == "source_result_unavailable"
    assert issue["counterexample"]["choices"] == {"lookup": "error"}
    assert issue["counterexample"]["source_reached"] is True
    assert issue["counterexample"]["source_value_available"] is False


def test_user_ref_with_helper_prefix_is_not_treated_as_a_generated_node():
    raw, req, snap = broken_common_consumer()
    raw = json.loads(json.dumps(raw).replace('"answer"', '"text_user_answer"'))
    issue = prompt_for(raw, req, snap)["repair_focus"]["issues"][0]
    assert issue["node_ref"] == "text_user_answer"
    assert issue["mapping_status"] == "mapped"
    assert issue["locations"][0][-1] == "task_input"


@pytest.mark.parametrize("corruption", ["missing", "wrong-source", "wrong-port"])
def test_missing_or_mismatched_provenance_is_not_guessed_from_helper_ref(corruption):
    from server.meta_agent.recipe_preflight import recipe_dependency_feedback
    raw, req, snap = broken_common_consumer()
    origins, dependencies = {}, []
    graph = lower_generation_recipe(raw, req, snap, input_origins=origins)
    validate_blueprint_authorization(req, _plan(), graph, snap, control_dependency_issues=dependencies)
    issue = dependencies[0]
    key = (issue["node_ref"], issue["input_index"])
    assert key[0].startswith("text_")
    if corruption == "missing":
        del origins[key]
    elif corruption == "wrong-source":
        origins[key]["source_ref"] = "lookup"
    else:
        origins[key]["source_port"] = "json"
    focus = recipe_dependency_feedback(parse_generation_recipe(raw), origins, dependencies)
    assert focus["issues"][0]["mapping_status"] == "unavailable"
    assert "locations" not in focus["issues"][0]
    assert "node_ref" not in focus["issues"][0]
    assert validate_blueprint_authorization(req, _plan(), graph, snap)


def test_provenance_is_not_accepted_via_diagnostic_payload():
    raw, req, snap = broken_common_consumer()
    recipe = parse_generation_recipe(raw)
    forged = {"repair_focus": {"issues": [], "issue_count": 0}, "input_origins": {"text_fake": "answer"}}
    normal = MetaPlannerV2Service._recipe_repair_prompt(req, _plan(), snap, None, recipe=recipe)
    attacked = MetaPlannerV2Service._recipe_repair_prompt(req, _plan(), snap, None,
        recipe=recipe, recipe_diagnostics=forged)
    assert normal == attacked


def test_feedback_is_bounded_and_omits_private_refs_witnesses_and_literals():
    from server.meta_agent.recipe_preflight import recipe_dependency_feedback
    raw, req, snap = broken_common_consumer()
    origins, dependencies = {}, []
    graph = lower_generation_recipe(raw, req, snap, input_origins=origins)
    validate_blueprint_authorization(req, _plan(), graph, snap, control_dependency_issues=dependencies)
    item = deepcopy(dependencies[0])
    item["source_ref"] = "secret_source"
    origin = origins[(item["node_ref"], item["input_index"])]
    origin.update(source_ref="secret_source", node_ref="secret_consumer")
    item["counterexample"]["choices"]["secret_guard"] = "matched"
    item["counterexample"]["witness"] = {"field": "PRIVATE_RECORD_CANARY"}
    item["business_value"] = "PRIVATE_LITERAL_CANARY"
    focus = recipe_dependency_feedback(parse_generation_recipe(raw), origins, [item] * 70)
    assert focus["issue_count"] == 70 and focus["omitted_issue_count"] == 6
    assert len(focus["issues"]) == 64
    safe = focus["issues"][0]
    assert "node_ref_checksum" in safe and "source_ref_checksum" in safe
    assert safe["counterexample"]["omitted_choice_count"] == 1
    text = json.dumps(focus)
    assert all(canary not in text for canary in ("secret_", "PRIVATE_", "business_value", "witness"))


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_valid", [False, True])
async def test_real_service_uses_one_repair_and_keeps_proposal_unapproved(tmp_path, monkeypatch, repair_valid):
    from server.tests import test_meta_planner_write_headless
    from server.tests.test_meta_planner_recipe_integration import generate
    from server.tests.test_meta_planner_recipe_edits import split_consumer_edits
    broken, _, snap = broken_common_consumer()
    monkeypatch.setattr(test_meta_planner_write_headless, "snapshot", lambda: snap)
    repaired = split_consumer_edits(broken) if repair_valid else {"operations": [{
        "op": "update_node", "node_ref": "answer", "title": next(n["title"] for n in broken["nodes"] if n["ref"] == "answer"),
    }]}
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=broken, repaired=repaired)
    assert len(calls) == 3
    prompt = json.loads(calls[2][2])
    assert prompt["repair_focus"]["issues"][0]["node_ref"] == "answer"
    assert result.validation["valid"] is repair_valid, json.dumps(result.validation, ensure_ascii=False)
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []
    if not repair_valid:
        assert "RECIPE_REPAIR_UNCHANGED" in json.dumps(proposal.payload["meta_planner_report"])
