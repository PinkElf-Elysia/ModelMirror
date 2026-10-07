"""Local evidence must survive a rejected flow, without inventing a graph."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.generation_recipe import lower_generation_recipe
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe


def rejected_branch(kind):
    raw, req, snap = guarded_recipe(error_output=kind == "read_error")
    if kind == "read_error":
        ref, location, expected = "lookup", ["control_flow", 0, "branches"], ["success", "error"]
        raw["control_flow"][0]["branches"] = []
    else:
        node = next(item for item in raw["nodes"] if item["ref"] == "exists")
        if kind == "multi_route":
            node.update(kind="multi_route", config={"routes": [
                {"label": "为空", "operator": "is_null", "value_type": "null"},
                {"label": "匹配", "operator": "equals", "value_type": "json", "value": {}},
            ]})
        ref, location = "exists", ["control_flow", 1, "branches"]
        expected = ["case_1", "case_2", "default"] if kind == "multi_route" else ["matched", "unmatched"]
        raw["control_flow"][1]["branches"] = []
    return snap, req, raw, ref, location, expected


@pytest.mark.parametrize("kind", ["condition", "multi_route", "read_error"])
def test_branch_mismatch_has_actionable_expected_actual_and_location(kind):
    snap, req, raw, ref, location, expected = rejected_branch(kind)
    before = deepcopy(raw)
    observer = GenerationDiagnostics("generation_recipe_v1")
    observer.enter("recipe_lowering")
    with pytest.raises(ValueError) as error:
        lower_generation_recipe(raw, req, snap)
    observer.exception(error.value)
    issue = observer.as_dict()["issues"][0]
    assert issue["code"] == "RECIPE_BRANCH_OUTCOMES_MISMATCH"
    assert issue["node_ref"] == ref and issue["location"] == location
    assert issue["recipe_detail"]["expected_outcomes"] == expected
    assert issue["recipe_detail"]["actual_counts"] == {}
    assert issue["recipe_detail"]["missing_outcomes"] == expected
    assert raw == before


def inspect(raw, req, snap):
    observer = GenerationDiagnostics("generation_recipe_v1")
    observer.enter("recipe_lowering")
    try:
        lower_generation_recipe(raw, req, snap, diagnostics=observer)
    except ValueError as exc:
        observer.exception(exc)
    return observer.as_dict()


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_invalid_flow_does_not_hide_independent_type_or_predicate_errors(domain):
    raw, req, snap = guarded_recipe(domain)
    raw["control_flow"][1]["branches"] = []
    by_ref = {item["ref"]: item for item in raw["nodes"]}
    for ref, source in (("answer", "write"), ("unchanged", "lookup")):
        by_ref[ref]["inputs"] = [{"port": "task", "source_ref": source, "source_port": "result"}]
        by_ref[ref]["config"]["task_input"] = "{{" + source + ".result}}"
    by_ref["exists"].update(kind="multi_route", config={"routes": [
        {"label": "已找到", "operator": "is_null", "value_type": "null", "value": False},
        {"label": "未找到", "operator": "is_null", "value_type": "null", "value": True},
    ]})
    by_ref["gate"].update(kind="multi_route", config={"routes": [
        {"label": "低值", "operator": "lt", "value_type": "number", "value": 5},
        {"label": "高值", "operator": "gte", "value_type": "number", "value": 5},
    ]})
    before = deepcopy(raw)
    report = inspect(raw, req, snap)
    facts = report["recipe_preflight"]
    assert report["failed_phase"] == "recipe_lowering"
    assert facts["input_types_status"] == "failed"
    assert facts["input_type_issue_count"] == 2
    assert {item["node_ref"] for item in facts["input_type_issues"]} == {"answer", "unchanged"}
    assert all(item["target_schema"]["type"] == "string" for item in facts["input_type_issues"])
    routers = {item["node_ref"]: item for item in facts["router_facts"]}
    assert routers["exists"]["unreachable_outcomes"] == ["case_2"]
    assert routers["gate"]["code"] == "ROUTER_INPUT_DOMAIN_INVALID"
    assert facts["path_proof_status"] == "not_executed"
    assert next(item for item in report["phase_results"] if item["id"] == "resolve")["status"] == "blocked"
    assert raw == before


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_local_nullable_field_errors_do_not_replace_guard_proof(domain):
    raw, req, snap = guarded_recipe(domain)
    facts = inspect(raw, req, snap)["recipe_preflight"]
    assert facts["input_type_issue_count"] == 0
    gate = next(item for item in facts["router_facts"] if item["node_ref"] == "gate")
    assert gate["status"] == "blocked" and gate["blocked_by"] == "path_guard"
    assert gate["possible_outcomes"] == ["matched", "unmatched"]
    assert gate["code"] is None
    from server.tests.test_meta_planner_controlled_writes import compile_case
    compile_case(lower_generation_recipe(raw, req, snap), snap, req)


@pytest.mark.parametrize("attack", ["scope", "config", "source"])
def test_untrusted_node_preparation_does_not_claim_zero_local_issues(attack):
    raw, req, snap = guarded_recipe()
    if attack == "scope":
        raw["nodes"][0]["resource_ref"]["resource_id"] = "foreign"
    elif attack == "config":
        raw["nodes"][0]["config"]["sourceHandle"] = "success"
    else:
        raw["nodes"][1]["inputs"][0]["source_ref"] = "unknown"
    report = inspect(raw, req, snap)
    assert report["recipe_preflight"] == {"status": "blocked", "blocked_by": "node_preparation"}


def test_branch_diff_includes_duplicates_and_unexpected_without_accepting_them():
    snap, req, raw, _, _, _ = rejected_branch("condition")
    raw["control_flow"][1]["branches"] = [
        {"outcome_ref": key, "steps": []} for key in ("matched", "matched", "success")]
    issue = inspect(raw, req, snap)["issues"][0]["recipe_detail"]
    assert issue["actual_counts"] == {"matched": 2, "success": 1}
    assert issue["missing_outcomes"] == ["unmatched"]
    assert issue["duplicate_outcomes"] == ["matched"]
    assert issue["unexpected_outcomes"] == ["success"]


def test_local_facts_exclude_values_labels_prompts_and_private_refs():
    snap, req, raw, _, _, _ = rejected_branch("multi_route")
    raw["nodes"][-1]["config"]["role_prompt"] = "PRIVATE_PROMPT_CANARY"
    router = next(item for item in raw["nodes"] if item["ref"] == "exists")
    router["config"]["routes"][0]["label"] = "PRIVATE_LABEL_CANARY"
    router["ref"] = "secret_ref"
    raw["control_flow"][1]["node_ref"] = "secret_ref"
    text = json.dumps(inspect(raw, req, snap))
    assert "PRIVATE_" not in text and "secret_ref" not in text
    assert "node_ref_checksum" in text


def test_repair_frontloads_local_facts_even_when_no_graph_exists():
    from server.meta_agent.generation_recipe import parse_generation_recipe
    from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
    from server.tests.test_meta_planner_controlled_writes import _plan
    snap, req, raw, _, _, _ = rejected_branch("condition")
    answer = next(item for item in raw["nodes"] if item["ref"] == "answer")
    answer["inputs"] = [{"port": "task", "source_ref": "lookup", "source_port": "result"}]
    answer["config"]["task_input"] = "{{lookup.result}}"
    payload = json.loads(MetaPlannerV2Service._recipe_repair_prompt(req, _plan(), snap, None,
        recipe=parse_generation_recipe(raw)))
    feedback = payload["semantic_feedback"]
    assert feedback["input_type_issue_count"] == 1
    assert feedback["input_type_issues"][0]["node_ref"] == "answer"
    assert feedback["path_proof_status"] == "blocked"
    keys = list(payload)
    assert keys.index("lowering_diagnostics") < keys.index("invalid_generation") < keys.index("required_schema")
    assert keys.index("semantic_feedback") < keys.index("invalid_generation")


def test_repair_does_not_report_zero_when_local_preparation_is_blocked():
    from server.meta_agent.generation_recipe import parse_generation_recipe
    from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
    from server.tests.test_meta_planner_controlled_writes import _plan
    raw, req, snap = guarded_recipe()
    req.scope.data_table_ids = []
    payload = json.loads(MetaPlannerV2Service._recipe_repair_prompt(req, _plan(), snap, None,
        recipe=parse_generation_recipe(raw)))
    feedback = payload["semantic_feedback"]
    assert feedback["input_types_status"] == "blocked"
    assert feedback["input_type_issue_count"] is None
    assert feedback["path_proof_status"] == "blocked"


@pytest.mark.asyncio
async def test_changed_title_is_not_progress_on_the_same_failed_control_structure(tmp_path, monkeypatch):
    from server.tests.meta_planner_recipe_fixtures import from_intent, step
    from server.tests.test_meta_planner_controlled_writes import intent
    from server.tests.test_meta_planner_recipe_integration import generate
    raw = from_intent(intent("update"))
    raw["control_flow"].append(step("answer"))
    repaired = {"operations": [{"op": "update_node", "node_ref": raw["nodes"][-1]["ref"],
                               "title": "新的说明不能修复结构"}]}
    _, authoring, result, calls, _ = await generate(tmp_path, monkeypatch, initial=raw, repaired=repaired)
    assert len(calls) == 3 and not result.validation["valid"]
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    diagnostics = report["generation_diagnostics"][-1]
    assert diagnostics["recipe_repair_progress"]["blocking_structure_unchanged"] is True
    assert "RECIPE_CONTROL_FLOW_UNCHANGED" in json.dumps(diagnostics)
    assert authoring.xpert_store.list_xperts() == []


def test_progress_observation_does_not_reject_a_config_fix_with_unchanged_flow():
    from server.meta_agent.generation_recipe import parse_generation_recipe
    from server.tests.meta_planner_recipe_fixtures import step
    raw, req, snap = guarded_recipe()
    raw["control_flow"][1]["branches"] = [
        {"outcome_ref": "case_1", "steps": [step("missing")]},
        {"outcome_ref": "case_2", "steps": [step("gate", {
            "matched": [step("write"), step("encode"), step("answer")],
            "unmatched": [step("unchanged")],
        })]},
        {"outcome_ref": "default", "steps": []},
    ]
    before = GenerationDiagnostics("generation_recipe_v1")
    before.parsed_recipe = parse_generation_recipe(raw)
    before.enter("recipe_lowering")
    with pytest.raises(ValueError) as error:
        lower_generation_recipe(raw, req, snap, diagnostics=before)
    before.exception(error.value)
    fixed = deepcopy(raw)
    router = next(item for item in fixed["nodes"] if item["ref"] == "exists")
    router.update(kind="multi_route", config={"routes": [
        {"label": "为空", "operator": "is_null"},
        {"label": "其他", "operator": "equals", "value_type": "json", "value": {}},
    ]})
    after = GenerationDiagnostics("generation_recipe_v1")
    after.parsed_recipe = parse_generation_recipe(fixed)
    after.enter("recipe_lowering")
    with pytest.raises(ValueError) as error:
        lower_generation_recipe(fixed, req, snap, diagnostics=after)
    after.exception(error.value)
    # The old mismatch is gone even though a different terminal error remains.
    assert not after.compare_recipe_repair(before)
    assert after.recipe_repair_progress["acceptance"] == "requires_full_validation"


def legacy_inline_recipe_schema(req, snap):
    """Pre-U2 layout, derived from the same frozen Adapter definitions."""
    from server.meta_agent.generation_contract import _install_config, compact_generation_schema, generation_node_kinds
    from server.meta_agent.generation_recipe import GenerationRecipeV1
    from server.meta_agent.node_adapters import get_planner_node_adapter, planner_capability_metadata
    from server.meta_agent.resource_generation_contract import scope_resource_schema
    schema = GenerationRecipeV1.model_json_schema()
    definitions = schema["$defs"]
    kinds = generation_node_kinds(req, snap)
    alternatives = []
    for kind in sorted(kinds):
        node = deepcopy(definitions["RecipeNode"])
        node["properties"]["kind"] = {"const": kind}
        node["properties"]["config"] = _install_config(definitions, kind, kinds)
        if planner_capability_metadata(kind)["task_binding"] == "forbidden":
            node["properties"]["task_ids"]["maxItems"] = 0
        else:
            node["properties"]["task_ids"]["minItems"] = 1
            node["required"] = sorted(set(node["required"]) | {"task_ids"})
        if get_planner_node_adapter(kind).resource_kind:
            node["properties"]["resource_ref"] = {"$ref": "#/$defs/GraphIntentNodeResourceRefV3"}
            node["required"] = sorted(set(node["required"]) | {"resource_ref"})
        else:
            node["properties"]["resource_ref"] = {"type": "null"}
        definitions[f"ModelNode_{kind}"] = node
        alternatives.append({"$ref": f"#/$defs/ModelNode_{kind}"})
    schema["properties"]["nodes"]["items"] = {"oneOf": alternatives} if alternatives else False
    definitions.pop("RecipeNode")
    scope_resource_schema(schema, req, snap)
    return compact_generation_schema(schema)


@pytest.mark.parametrize("mutation", ["valid", "config", "binding", "task", "extra", "kind", "resource",
                                    "handle", "version", "port_schema", "title", "empty_config"])
def test_schema_factoring_retains_the_previous_acceptance_boundary(mutation):
    from jsonschema import Draft202012Validator
    from server.meta_agent.generation_recipe import recipe_schema
    raw, req, snap = guarded_recipe()
    if mutation == "config":
        raw["nodes"][0]["config"]["outputVariable"] = "injected"
    elif mutation == "binding":
        raw["nodes"][0]["inputs"] = [{"port": "value", "source_ref": "x", "source_port": "r", "variable": "v"}]
    elif mutation == "task":
        raw["nodes"][0]["task_ids"] = ["task_1"]
    elif mutation == "extra":
        raw["nodes"][0]["compiler_checksum"] = "a" * 64
    elif mutation == "kind":
        raw["nodes"][0]["kind"] = "http_request"
    elif mutation == "resource":
        raw["nodes"][0]["resource_ref"]["resource_id"] = "foreign"
    elif mutation == "handle":
        raw["control_flow"][0]["sourceHandle"] = "success"
    elif mutation == "version":
        raw["nodes"][0]["resource_ref"]["schema_version"] = 1
    elif mutation == "port_schema":
        raw["nodes"][1]["inputs"][0]["value_schema"] = {"type": "any"}
    elif mutation == "title":
        raw["nodes"][0]["title"] = ""
    elif mutation == "empty_config":
        raw["nodes"][0].pop("config")
    old, current = legacy_inline_recipe_schema(req, snap), recipe_schema(req, snap)
    Draft202012Validator.check_schema(current)
    assert Draft202012Validator(current).is_valid(raw) == Draft202012Validator(old).is_valid(raw) == (mutation == "valid")
    node_definitions = [value for name, value in current["$defs"].items() if name.startswith("ModelNode_")]
    assert all(value["allOf"][0] == {"$ref": "#/$defs/ModelRecipeNodeBase"} for value in node_definitions)
    assert "title" in current["$defs"]["ModelRecipeNodeBase"]["properties"]
    assert all("title" not in value["allOf"][-1]["properties"] for value in node_definitions)
    assert len(json.dumps(current, ensure_ascii=False)) < len(json.dumps(old, ensure_ascii=False))


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_compiler_success_cannot_certify_business_predicate_correctness(domain):
    from server.tests.test_meta_planner_controlled_writes import compile_case
    from server.workflow_native.control_data import evaluate_typed_condition
    raw, req, snap = guarded_recipe(domain)
    correct = next(item for item in raw["nodes"] if item["ref"] == "gate")["config"]
    wrong = deepcopy(raw)
    inverted = next(item for item in wrong["nodes"] if item["ref"] == "gate")["config"]
    inverted["operator"] = "gte" if domain == "quality" else "not_equals"
    for recipe in (raw, wrong):
        compile_case(lower_generation_recipe(recipe, req, snap), snap, req)
    cases = [(42, True), (60, False), (75, False)] if domain == "quality" else [("待处理", True), ("已处理", False)]
    for value, should_write in cases:
        record = {correct["field"]: value}
        def check(config):
            return evaluate_typed_condition(record, field=config["field"], operator=config["operator"],
                value_type=config["value_type"], expected=config["value"])
        assert check(correct) is should_write
        assert check(inverted) is not should_write
