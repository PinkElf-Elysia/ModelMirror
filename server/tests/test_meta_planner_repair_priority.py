"""Offline repair-focus counterexamples; no provider success claim."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_recipe import lower_generation_recipe, parse_generation_recipe
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case
from server.tests.test_meta_planner_recipe_edits import split_consumer_edits
from server.tests.test_meta_planner_recipe_source_feedback import broken_common_consumer
from server.meta_agent.recipe_edits import apply_recipe_edits


def prompt(raw, req, snap, edit=True):
    method = MetaPlannerV2Service._recipe_edit_prompt if edit else MetaPlannerV2Service._recipe_repair_prompt
    return json.loads(method(req, _plan(), snap, None, recipe=parse_generation_recipe(raw)))


@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("edit", [False, True])
def test_array_field_failure_is_primary_before_unproven_branch_dependencies(domain, edit):
    raw, req, snap = broken_common_consumer(domain)
    lookup = next(n for n in raw["nodes"] if n["ref"] == "lookup")
    lookup["config"]["return_mode"] = "list"
    next(n for n in raw["nodes"] if n["ref"] == "exists")["config"] = {
        "operator": "equals", "value_type": "json", "value": [],
    }
    before = deepcopy(raw)
    context = prompt(raw, req, snap, edit)
    focus = context["repair_focus"]
    assert focus["issue_count"] > 0
    primary = focus["issues"][0]
    assert primary["code"] == "ROUTER_INPUT_DOMAIN_INVALID"
    assert primary["node_ref"] == "gate"
    assert primary["input_contract"]["source_type"] == "array"
    assert primary["input_contract"]["source_nullable"] is False
    assert primary["input_contract"]["has_field"] is True
    assert primary["source_location"] == ["nodes", 0, "config"]
    assert {i["node_ref"] for i in primary["related_inputs"]} >= {"exists", "gate", "write", "answer"}
    assert len(primary["related_inputs"]) == len({(i["node_ref"], tuple(i["location"])) for i in primary["related_inputs"]})
    assert context["repair_checklist"]
    assert raw == before
    assert validate_blueprint_authorization(req, _plan(), lower_generation_recipe(raw, req, snap), snap)


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_dependency_emerges_after_type_and_guard_are_corrected(domain):
    raw, req, snap = broken_common_consumer(domain)
    focus = prompt(raw, req, snap)["repair_focus"]
    assert focus["issues"][0]["code"] == "DATA_PATH_NOT_GUARANTEED"
    assert focus["issues"][0]["node_ref"] == "answer"
    assert focus["issues"][0]["locations"][0][-1] == "task_input"
    assert focus["path_proof_status"] == "failed"


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_clone_coverage_preserves_original_agent_and_all_business_nodes(domain):
    raw, req, snap = broken_common_consumer(domain)
    base = parse_generation_recipe(raw)
    edits = split_consumer_edits(raw)
    fixed = apply_recipe_edits(base, edits, req, snap)
    context = prompt(fixed.model_dump(mode="json"), req, snap)
    coverage = context["repair_focus"]["control_coverage"]
    assert set(coverage["required_node_refs"]) == {n.ref for n in fixed.nodes}
    assert coverage["omitted_node_refs"] == []
    assert context["repair_focus"]["issues"] == []
    compile_case(lower_generation_recipe(fixed, req, snap), snap, req)
    broken = fixed.model_dump(mode="json")
    broken["nodes"].append({**deepcopy(broken["nodes"][-1]), "ref": "orphan_summary"})
    context = prompt(broken, req, snap)
    assert context["repair_focus"]["control_coverage"]["omitted_node_refs"] == ["orphan_summary"]
    assert context["repair_focus"]["issues"][0]["code"] == "RECIPE_OMITTED_NODE"
    with pytest.raises(ValueError):
        lower_generation_recipe(broken, req, snap)
    assert base.model_dump(mode="json") == parse_generation_recipe(raw).model_dump(mode="json")


def test_focus_does_not_copy_prompts_values_or_instruction_fields():
    raw, req, snap = broken_common_consumer()
    next(n for n in raw["nodes"] if n["ref"] == "lookup")["config"]["return_mode"] = "list"
    next(n for n in raw["nodes"] if n["ref"] == "answer")["config"]["role_prompt"] = "PRIVATE_PROMPT_CANARY"
    context = prompt(raw, req, snap)
    text = json.dumps(context["repair_focus"])
    assert "PRIVATE_PROMPT_CANARY" not in text
    assert "role_prompt" not in text
    assert "required_node_refs" in text
    raw["nodes"][0]["config"]["repair_focus"] = {"issues": []}
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, req, snap)


def test_port_type_mismatch_is_not_hidden_behind_predicate_or_path_diagnostics():
    raw, req, snap = broken_common_consumer()
    answer = next(n for n in raw["nodes"] if n["ref"] == "answer")
    answer["inputs"] = [{"port": "task", "source_ref": "lookup", "source_port": "result"}]
    answer["config"]["task_input"] = "{{lookup.result}}"
    answer["config"]["role_prompt"] = "只概括提供的证据。"
    issue = prompt(raw, req, snap)["repair_focus"]["issues"][0]
    assert issue["code"] == "INPUT_TYPE_INCOMPATIBLE"
    assert issue["node_ref"] == "answer" and issue["target_port"] == "task"
    assert issue["source_schema"]["type"] == "object"
    assert issue["target_schema"]["type"] == "string"
