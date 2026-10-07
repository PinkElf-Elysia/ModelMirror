"""Template diagnostics must guide repair without changing the failed candidate."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics, RecipeTemplateError
from server.meta_agent.generation_recipe import lower_generation_recipe, parse_generation_recipe
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.workflow_native.node_contracts import canonical_checksum
from server.tests.meta_planner_recipe_fixtures import step
from server.tests.test_meta_planner_controlled_writes import _plan
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
from server.tests.test_meta_planner_recipe_source_feedback import broken_common_consumer
from server.tests.test_meta_planner_recipe_text_inputs import text_recipe


def diagnose(raw, req, snap):
    before = deepcopy(raw)
    observer = GenerationDiagnostics("generation_recipe_v1")
    observer.enter("recipe_lowering")
    observer.parsed_recipe = parse_generation_recipe(raw)
    with pytest.raises(ValueError) as failure:
        lower_generation_recipe(raw, req, snap, diagnostics=observer)
    observer.exception(failure.value)
    assert raw == before
    with pytest.raises(type(failure.value)) as plain:
        lower_generation_recipe(raw, req, snap)
    assert str(plain.value) == str(failure.value)
    return observer


@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("derived", [False, True])
def test_known_control_source_has_safe_facts_and_independent_structure_check(domain, derived):
    raw, req, snap = guarded_recipe(domain)
    if derived:
        raw = text_recipe(raw)
    answer = next(n for n in raw["nodes"] if n["ref"] == "answer")
    answer["config"]["task_input"] = "PRIVATE_PROMPT_CANARY {{gate.result}}"
    orphan = deepcopy(answer)
    orphan.update(ref="unplaced", config={**answer["config"], "task_input": "{{input.user_input}}"})
    if not derived:
        orphan["inputs"] = [{"port": "task", "source_ref": "input", "source_port": "user_input"}]
    raw["nodes"].append(orphan)
    report = diagnose(raw, req, snap).as_dict()
    assert [x["code"] for x in report["issues"]] == ["RECIPE_TEMPLATE_SOURCE_UNKNOWN", "RECIPE_OMITTED_NODE"]
    detail = report["issues"][0]["template_detail"]
    assert detail["source_ref"] == "gate"
    assert detail["source_kind"] == "condition"
    assert detail["available_output_ports"] == []
    assert detail["reference_index"] == 0
    assert detail["reason"] == "source_has_no_data_outputs"
    assert report["recipe_preflight"]["control_structure_status"] == "failed"
    assert "PRIVATE_PROMPT_CANARY" not in json.dumps(report)
    assert "gate.result" not in json.dumps(report)


def test_derived_template_errors_are_collected_across_fields_and_bounded():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    answer = next(n for n in raw["nodes"] if n["ref"] == "answer")
    answer["config"] = {"role_prompt": "{{private_unknown.result}}",
                        "task_input": " ".join("{{private_" + str(i) + ".result}}" for i in range(80))}
    report = diagnose(raw, req, snap).as_dict()
    assert report["issue_count"] == 81
    assert len(report["issues"]) == 64 and report["omitted_issue_count"] == 17
    assert report["recipe_preflight"]["invalid_template_reference_count"] == 81
    assert report["recipe_preflight"]["control_structure_status"] == "passed"
    assert "private_unknown" not in json.dumps(report)
    assert "private_0" not in json.dumps(report)


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_bad_template_does_not_hide_proven_dependency_of_other_valid_sources(domain):
    raw, req, snap = broken_common_consumer(domain)
    next(n for n in raw["nodes"] if n["ref"] == "answer")["config"]["task_input"] += " {{gate.result}}"
    report = diagnose(raw, req, snap).as_dict()
    local = report["recipe_preflight"]
    assert local["path_proof_status"] == "blocked"
    known = local["known_source_dependencies"]
    assert known["status"] == "partial"
    issue = known["issues"][0]
    assert issue["code"] == "DATA_PATH_NOT_GUARANTEED"
    assert (issue["node_ref"], issue["source_ref"], issue["source_port"]) == ("answer", "write", "result")
    assert issue["counterexample"]["choices"]["gate"] == "unmatched"
    assert issue["mapping_status"] == "mapped"
    assert "text_" not in json.dumps(known)
    assert report["failed_phase"] == "recipe_lowering"


def test_invalid_new_reference_does_not_mask_after_terminal_structure():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    answer = next(n for n in raw["nodes"] if n["ref"] == "answer")
    clone = deepcopy(answer)
    clone.update(ref="after_stop", config={**answer["config"], "task_input": "{{missing.config.message}}"})
    raw["nodes"].append(clone)
    raw["control_flow"][1]["branches"][0]["steps"].append(step("after_stop"))
    report = diagnose(raw, req, snap).as_dict()
    assert [x["code"] for x in report["issues"]] == ["RECIPE_TEMPLATE_REFERENCE_INVALID", "RECIPE_AFTER_TERMINAL"]
    assert report["issues"][1]["node_ref"] == "after_stop"
    assert report["issues"][1]["location"][0] == "control_flow"
    assert "missing.config.message" not in json.dumps(report)


@pytest.mark.parametrize("attack", ["scope", "config", "resource"])
def test_untrusted_preparation_still_blocks_further_facts(attack):
    raw, req, snap = broken_common_consumer()
    next(n for n in raw["nodes"] if n["ref"] == "answer")["config"]["task_input"] += " {{gate.result}}"
    if attack == "scope":
        req.scope.data_table_ids = []
    elif attack == "config":
        raw["nodes"][0]["config"]["sourceHandle"] = "forged"
    else:
        raw["nodes"][0]["resource_ref"]["resource_id"] = "private_unknown_table"
    report = diagnose(raw, req, snap).as_dict()
    assert report["recipe_preflight"] == {"status": "blocked", "blocked_by": "node_preparation"}
    assert not any(x["code"] in RecipeTemplateError.MESSAGES for x in report["issues"])
    assert "private_unknown_table" not in json.dumps(report)


@pytest.mark.parametrize("edit", [False, True])
@pytest.mark.parametrize("has_dependency", [False, True])
def test_repair_focus_keeps_source_facts_and_only_proven_branch_obligations(edit, has_dependency):
    raw, req, snap = broken_common_consumer() if has_dependency else guarded_recipe()
    if not has_dependency:
        raw = text_recipe(raw)
    next(n for n in raw["nodes"] if n["ref"] == "answer")["config"]["task_input"] += " {{gate.result}}"
    prompt_fn = MetaPlannerV2Service._recipe_edit_prompt if edit else MetaPlannerV2Service._recipe_repair_prompt
    payload = json.loads(prompt_fn(req, _plan(), snap, None, recipe=parse_generation_recipe(raw)))
    focus = payload["repair_focus"]
    first = focus["issues"][0]
    assert first["template_detail"]["source_ref"] == "gate"
    assert first["template_detail"]["available_output_ports"] == []
    assert focus["primary_layer"] == "template_sources"
    assert focus["branch_repair_required"] is (True if has_dependency else None)
    assert focus["path_proof_status"] == "blocked"
    advice = focus["obligation"] + " ".join(payload["repair_checklist"])
    assert "reference_index" in advice and "config" in advice
    if has_dependency:
        dependent = next(x for x in focus["issues"] if x["code"] == "DATA_PATH_NOT_GUARANTEED")
        assert dependent["proof_scope"] == "known_sources_only"
        assert dependent["node_ref"] == "answer"
        assert (dependent["source_ref"], dependent["source_port"]) == ("write", "result")
        assert "known_source_dependencies" not in payload["semantic_feedback"]
        assert payload["semantic_feedback"]["known_source_dependency_status"] == "partial"
    else:
        assert "克隆" not in advice
        assert "保持" in advice
    assert payload["invalid_generation"] == parse_generation_recipe(raw).model_dump(mode="json")


def test_changed_control_tree_cannot_disguise_unchanged_template_blocker():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    next(n for n in raw["nodes"] if n["ref"] == "answer")["config"]["task_input"] += " {{gate.result}}"
    before = diagnose(raw, req, snap)
    changed = deepcopy(raw)
    # A different control tree with a new defect, but the original blocker remains.
    changed["control_flow"].append(step("answer"))
    after = diagnose(changed, req, snap)
    after.compare_recipe_repair(before)
    progress = after.recipe_repair_progress
    assert progress["control_flow_before_checksum"] != progress["control_flow_after_checksum"]
    assert progress["persisting_issue_count"] == 1
    assert progress["new_issue_count"] == 1
    assert progress["no_longer_observed_issue_count"] == 0
    assert progress["assessment"] == "blocking_issues_persist"
    assert progress["acceptance"] == "requires_full_validation"


def test_moving_invalid_placeholder_does_not_count_as_removing_it():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    answer = next(n for n in raw["nodes"] if n["ref"] == "answer")
    answer["config"]["task_input"] = "{{gate.result}} {{lookup.result}} {{write.result}}"
    before = diagnose(raw, req, snap)
    answer["config"]["task_input"] = "{{lookup.result}} {{write.result}} {{gate.result}}"
    after = diagnose(raw, req, snap)
    after.compare_recipe_repair(before)
    assert after.recipe_repair_progress["persisting_issue_count"] == 1
    assert after.recipe_repair_progress["no_longer_observed_issue_count"] == 0


def test_unknown_template_cannot_become_valid_by_guessing_later_compiler_helper():
    raw, req, snap = guarded_recipe()
    raw = text_recipe(raw)
    helper = "text_" + canonical_checksum(["answer", "lookup", "result"])[:24]
    answer = next(n for n in raw["nodes"] if n["ref"] == "answer")
    answer["config"]["task_input"] += " {{" + helper + ".json}}"
    observer = diagnose(raw, req, snap)
    assert observer.issues[0]["code"] == "RECIPE_TEMPLATE_SOURCE_UNKNOWN"
    assert observer.issues[0]["template_detail"]["reason"] == "unknown_source"
    assert helper not in json.dumps(observer.as_dict())
