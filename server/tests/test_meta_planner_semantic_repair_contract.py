"""Compound counterexamples exercise proof frontiers, not model success rates."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.control_flow import model_control_contract
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.generation_recipe import lower_generation_recipe, recipe_node_contracts
from server.meta_agent.meta_planner_v2 import validate_blueprint_authorization
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.tests.test_meta_planner_recipe_matrix import guarded_recipe, _plan
from server.workflow_native.control_data import evaluate_typed_condition


def compound_recipe(domain="quality"):
    raw, req, snap = guarded_recipe(domain)
    gate = raw["control_flow"][1]["branches"][1]["steps"][0]
    gate["branches"][0]["steps"].pop()  # Move the shared answer after both branches.
    gate["branches"][1]["steps"] = []
    raw["control_flow"].append({"type": "node", "node_ref": "answer"})
    raw["nodes"] = [node for node in raw["nodes"] if node["ref"] != "unchanged"]
    raw["final_output"]["sources"] = [{"node_ref": "answer", "port": "result"}]
    for branch in raw["control_flow"][1]["branches"]:
        branch["outcome_ref"] = "unmatched" if branch["outcome_ref"] == "matched" else "matched"
    next(node for node in raw["nodes"] if node["ref"] == "gate")["config"].pop("field")
    return raw, req, snap


def diagnose(raw, req, snap):
    graph = lower_generation_recipe(raw, req, snap)
    diagnostics = GenerationDiagnostics("generation_recipe_v1")
    diagnostics.bind_graph(graph)
    paths, dependencies = [], []
    errors = validate_blueprint_authorization(req, _plan(), graph, snap, diagnostics=diagnostics,
        control_path_issues=paths, control_dependency_issues=dependencies)
    return errors, diagnostics.as_dict(), paths, dependencies


def test_recipe_control_projection_shares_authoritative_semantics():
    _, req, snap = guarded_recipe()
    contracts = recipe_node_contracts(req, snap)
    adapter = get_planner_node_adapter("condition")
    assert contracts["condition"]["control_contract"] == model_control_contract("condition", adapter.config_model.model_json_schema())
    meanings = contracts["condition"]["control_contract"]["predicate_semantics"]
    assert "整个输入" in meanings["input_selection"]
    assert "值为空" in meanings["outcomes"]["matched"]
    assert "值非空" in meanings["outcomes"]["unmatched"]
    assert evaluate_typed_condition(None, field="", operator="is_null", value_type="null", expected=None) is True
    assert evaluate_typed_condition({"score": 42}, field="", operator="is_null", value_type="null", expected=None) is False
    assert evaluate_typed_condition({"score": 42}, field="score", operator="lt", value_type="number", expected=60) is True


@pytest.mark.parametrize("repair_field,repair_guard", [(False, False), (True, False), (False, True), (True, True)])
def test_compound_faults_cannot_hide_unproved_downstream_data(repair_field, repair_guard):
    raw, req, snap = compound_recipe()
    if repair_field:
        next(node for node in raw["nodes"] if node["ref"] == "gate")["config"]["field"] = "score"
    if repair_guard:
        for branch in raw["control_flow"][1]["branches"]:
            branch["outcome_ref"] = "unmatched" if branch["outcome_ref"] == "matched" else "matched"
    before = deepcopy(raw)
    errors, evidence, paths, dependencies = diagnose(raw, req, snap)
    assert errors and raw == before
    checks = {item["id"]: item for item in evidence["control_proof_checks"]}
    assert checks["structure"]["status"] == "passed"
    if not (repair_field and repair_guard):
        assert checks["predicate_domains"]["status"] == "failed"
        assert checks["data_availability"] == {"id": "data_availability", "status": "blocked", "blocked_by": "predicate_domains"}
        assert dependencies == []  # Empty is explicitly NOT a proof of validity.
        assert any(item["code"] == "ROUTER_INPUT_DOMAIN_INVALID" and item.get("node_ref") == "gate" for item in evidence["issues"])
    else:
        assert checks["predicate_domains"]["status"] == "passed"
        assert checks["data_availability"]["status"] == "failed"
        assert any(item["code"] == "DATA_PATH_NOT_GUARANTEED" and item["node_ref"] == "answer" for item in dependencies)
    safe = json.dumps(evidence, ensure_ascii=False)
    assert "DEMO" not in safe and "\"score\"" not in safe


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_complete_semantic_repair_proves_all_checks_across_domains(domain):
    raw, req, snap = guarded_recipe(domain)
    errors, evidence, _, _ = diagnose(raw, req, snap)
    assert not errors
    assert all(item["status"] == "passed" for item in evidence["control_proof_checks"])
