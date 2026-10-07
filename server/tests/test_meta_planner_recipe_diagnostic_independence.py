"""Independent safe checks must not be hidden by a Prompt reference failure."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics, diagnostic_checksum
from server.meta_agent.generation_recipe import lower_generation_recipe, parse_generation_recipe
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.test_meta_planner_controlled_writes import _plan, intent
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
from server.tests.meta_planner_recipe_fixtures import from_intent


def combined_failures(raw):
    nodes = {node["ref"]: node for node in raw["nodes"]}
    answer = nodes["answer"]
    answer["inputs"] = [{"port": "task", "source_ref": "lookup", "source_port": "result"}]
    answer["config"]["task_input"] = "{{lookup.result}}\n{{write.result}}"
    if "unchanged" in nodes:
        nodes["unchanged"]["inputs"] = deepcopy(answer["inputs"])
        nodes["unchanged"]["config"]["task_input"] = "{{lookup.result}}"
    orphan = deepcopy(answer)
    orphan["ref"] = "unplaced"
    orphan["config"]["task_input"] = "{{lookup.result}}"
    raw["nodes"].append(orphan)
    raw["final_output"]["sources"].append({"node_ref": "unplaced", "port": "result"})
    return raw


def diagnose(raw, req, snap):
    observer = GenerationDiagnostics("generation_recipe_v1")
    observer.enter("recipe_lowering")
    with pytest.raises(ValueError) as error:
        lower_generation_recipe(raw, req, snap, diagnostics=observer)
    observer.exception(error.value)
    return observer.as_dict(), error.value


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_missing_template_binding_does_not_mask_types_or_unplaced_node(domain):
    raw, req, snap = guarded_recipe(domain)
    combined_failures(raw)
    before = deepcopy(raw)
    report, error = diagnose(raw, req, snap)
    assert getattr(error, "code", None) == "RECIPE_TEMPLATE_INPUT_MISSING"
    codes = [issue["code"] for issue in report["issues"]]
    assert codes == ["RECIPE_TEMPLATE_INPUT_MISSING", "RECIPE_OMITTED_NODE"]
    template, control = report["issues"]
    assert template["node_ref"] == "answer"
    assert template["location"][-2:] == ["config", "task_input"]
    assert template["template_detail"] == {"source_ref": "write", "source_port": "result", "reference_index": 1}
    assert control["node_ref"] == "unplaced"
    local = report["recipe_preflight"]
    assert local["input_type_issue_count"] == 3
    assert local["template_bindings_status"] == "failed"
    assert local["invalid_template_reference_count"] == 1
    assert local["control_structure_status"] == "failed"
    assert local["path_proof_status"] == "blocked"
    with pytest.raises(type(error)) as plain:
        lower_generation_recipe(raw, req, snap)
    assert str(plain.value) == str(error)
    assert raw == before


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_unique_repair_receives_all_independent_facts_without_new_rules(domain):
    raw, req, snap = guarded_recipe(domain)
    combined_failures(raw)
    prompt = json.loads(MetaPlannerV2Service._recipe_repair_prompt(
        req, _plan(), snap, None, recipe=parse_generation_recipe(raw)))
    assert [issue["code"] for issue in prompt["lowering_diagnostics"]["issues"]] == [
        "RECIPE_TEMPLATE_INPUT_MISSING", "RECIPE_OMITTED_NODE"]
    feedback = prompt["semantic_feedback"]
    assert feedback["input_type_issue_count"] == 3
    assert feedback["invalid_template_reference_count"] == 1
    assert feedback["path_proof_status"] == "blocked"
    assert feedback["control_path_issues"] == []
    keys = list(prompt)
    assert keys.index("lowering_diagnostics") < keys.index("invalid_generation")
    assert keys.index("semantic_feedback") < keys.index("required_schema")


@pytest.mark.parametrize("attack", ["scope", "config", "source", "resource_field"])
def test_untrusted_facts_remain_blocked_before_detailed_checks(attack):
    raw, req, snap = guarded_recipe()
    combined_failures(raw)
    if attack == "scope":
        req.scope.data_table_ids = []
    elif attack == "config":
        raw["nodes"][0]["config"]["sourceHandle"] = "injected"
    elif attack == "source":
        raw["nodes"][1]["inputs"][0]["source_ref"] = "unknown"
    else:
        raw["nodes"][0]["config"]["filter"]["field"] = "private_missing_field"
    report, _ = diagnose(raw, req, snap)
    assert report["recipe_preflight"] == {"status": "blocked", "blocked_by": "node_preparation"}
    assert not any(issue["code"].startswith("RECIPE_TEMPLATE") for issue in report["issues"])
    assert "private_missing_field" not in json.dumps(report)


@pytest.mark.parametrize("expression,code", [
    ("lookup.result.stock", "RECIPE_TEMPLATE_REFERENCE_INVALID"),
    ("user_input", "RECIPE_TEMPLATE_REFERENCE_INVALID"),
    ("lookup.stock", "RECIPE_TEMPLATE_SOURCE_UNKNOWN"),
    ("private_missing_ref.result", "RECIPE_TEMPLATE_SOURCE_UNKNOWN"),
])
def test_template_errors_have_safe_location_not_prompt_or_unknown_reference(expression, code):
    raw, req, snap = guarded_recipe()
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["config"]["task_input"] = "PRIVATE_PROMPT_CANARY {{" + expression + "}}"
    report, error = diagnose(raw, req, snap)
    issue = report["issues"][0]
    assert getattr(error, "code", None) == issue["code"] == code
    assert issue["node_ref"] == "answer"
    assert issue["location"][-2:] == ["config", "task_input"]
    detail = issue["template_detail"]
    assert detail["reference_checksum"] == diagnostic_checksum(expression)
    assert detail["reference_index"] == 0
    if expression.startswith("lookup."):
        assert set(detail) == {"reference_checksum", "reference_index", "reason", "source_ref", "source_kind", "available_output_ports"}
        assert detail["source_ref"] == "lookup" and detail["source_kind"] == "data_table_query"
        assert detail["available_output_ports"] == ["result"]
    else:
        assert set(detail) == {"reference_checksum", "reference_index", "reason"}
    assert "PRIVATE_PROMPT_CANARY" not in json.dumps(report)
    assert expression not in json.dumps(report)
    assert expression not in str(error)
    assert report["recipe_preflight"]["control_structure_status"] == "passed"


def test_template_checks_collect_multiple_fields_and_are_bounded():
    raw, req, snap = guarded_recipe()
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["config"]["role_prompt"] = "{{lookup.result}}"
    answer["config"]["task_input"] = " ".join("{{missing_" + str(index) + ".result}}" for index in range(80))
    report, _ = diagnose(raw, req, snap)
    assert report["recipe_preflight"]["invalid_template_reference_count"] == 81
    assert len(report["issues"]) == 64 and report["issue_count"] == 81
    assert report["omitted_issue_count"] == 17
    assert report["issues"][0]["location"][-1] == "role_prompt"
    assert report["issues"][1]["location"][-1] == "task_input"
    assert "missing_" not in json.dumps(report)


def test_diagnostics_do_not_translate_missing_bindings_or_change_valid_lowering():
    raw, req, snap = guarded_recipe()
    before = deepcopy(raw)
    plain = lower_generation_recipe(raw, req, snap)
    observer = GenerationDiagnostics("generation_recipe_v1")
    observer.enter("recipe_lowering")
    observed = lower_generation_recipe(raw, req, snap, diagnostics=observer)
    assert plain.model_dump(mode="json") == observed.model_dump(mode="json")
    assert not observer.as_dict()["issues"]
    assert observer.recipe_preflight["template_bindings_status"] == "passed"
    assert observer.recipe_preflight["invalid_template_reference_count"] == 0
    assert observer.recipe_preflight["control_structure_status"] == "passed"
    assert raw == before


@pytest.mark.asyncio
async def test_generation_repair_stays_three_calls_and_failed_proposal_has_both_diagnostics(tmp_path, monkeypatch):
    from server.tests.test_meta_planner_recipe_integration import generate

    raw = combined_failures(from_intent(intent("update")))
    edits = {"operations": [{"op": "update_node", "node_ref": "answer", "title": "改标题仍然存在原始语义错误"}]}
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired=edits)
    assert len(calls) == 3 and result.validation["valid"] is False
    repair = json.loads(calls[2][2])
    assert repair["semantic_feedback"]["input_type_issue_count"] == 2
    assert {issue["code"] for issue in repair["lowering_diagnostics"]["issues"]} == {
        "RECIPE_TEMPLATE_INPUT_MISSING", "RECIPE_OMITTED_NODE"}
    proposal = authoring.proposal_store.require(result.proposal_id)
    attempts = proposal.payload["_meta_planner_generation_artifact"]["attempts"]
    assert len(attempts) == 2 and proposal.status == "pending" and proposal.revision == 1
    assert proposal.payload["meta_planner_report"]["graph_ir_status"] == "fallback_unapprovable"
    for attempt in attempts:
        assert attempt["recipe"]
        assert "RECIPE_TEMPLATE_INPUT_MISSING" in {item["code"] for item in attempt["diagnostics"]["issues"]}
        assert "RECIPE_OMITTED_NODE" in {item["code"] for item in attempt["diagnostics"]["issues"]}
    assert authoring.xpert_store.list_xperts() == []
