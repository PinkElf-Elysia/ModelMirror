import json

import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot, _plan
from server.tests.test_meta_planner_generation_diagnostics import generate, record_edge
from server.tests.test_meta_planner_path_proof import guarded_graph
from server.tests.test_meta_planner_read_resources import _request, _snapshot
from server.tests.test_meta_planner_write_generation_failures import _failed_generation
from server.workflow_native.node_contracts import WorkflowValueSchema
from server.xpert_runtime.authoring_store import AuthoringProposalValidationError


def test_independent_types_are_reported_despite_unreachable_control():
    graph, snap = intent("update"), snapshot()
    graph.nodes[1].inputs[0].value_schema = WorkflowValueSchema(type="string")
    graph.control_edges = [edge for edge in graph.control_edges if edge.target_ref != "write"]
    types = []
    issues = validate_blueprint_authorization(request(snap, "update"), _plan(), graph, snap, input_type_issues=types)
    assert any("not reachable" in item for item in issues)
    assert len(types) == 1 and types[0].binding.port == "records"
    payload = json.loads(MetaPlannerV2Service._patch_repair_prompt(request(snap, "update"), _plan(), snap, graph, issues))
    assert payload["repair_contract"]["data_contract_issues"][0]["target_port"] == "records"


def test_unauthorized_source_cannot_supply_type_facts():
    graph, snap = intent("update"), snapshot()
    req = request(snap, "update")
    req.scope.data_table_ids = []
    graph.nodes[1].inputs[0].value_schema = WorkflowValueSchema(type="string")
    types = []
    issues = validate_blueprint_authorization(req, _plan(), graph, snap, input_type_issues=types)
    assert any("not authorized" in item for item in issues)
    assert not types


@pytest.mark.asyncio
async def test_failed_fallback_has_explicit_origin_and_validation_scope(tmp_path, monkeypatch):
    _, authoring, result, calls = await _failed_generation(tmp_path, monkeypatch)
    proposal = authoring.proposal_store.require(result.proposal_id)
    report = proposal.payload["meta_planner_report"]
    assert report["candidate_origin"] == "server_synthesized_fallback"
    assert report["graph_ir_status"] == "fallback_unapprovable"
    assert report["validation_scope"] == "pre_authoring_proposal"
    assert report["authoritative_validation_source"] == "proposal.validation"
    assert "missing_workflow_agent_model" not in json.dumps(report["validation"])
    assert report["validation_candidate_checksum"] == ""
    assert report["validation"]["diagnostic_subject"] == "model_generation"
    assert result.validation["diagnostic_subject"] == "generation_and_authoring"
    placeholder = report["placeholder_validation"]
    assert placeholder["diagnostic_subject"] == "server_synthesized_fallback"
    assert placeholder["candidate_checksum"] == report["compiled_workflow_checksum"]
    assert len(placeholder["candidate_checksum"]) == 64
    assert any(item["code"] == "missing_workflow_agent_model" for item in placeholder["issues"])
    assert "missing_workflow_agent_model" not in json.dumps(result.validation)
    assert [stage["id"] for stage in report["validation"]["stages"]] == ["planner_repair"]
    assert not result.validation["valid"] and not report["validation"]["valid"]
    assert proposal.status == "pending" and proposal.revision == 1
    assert len(calls) == 3 and not authoring.xpert_store.list_xperts()
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(proposal.proposal_id, revision=1)
    assert not authoring.xpert_store.list_xperts()
    phases = {item["id"]: item for item in report["generation_diagnostics"][1]["phase_results"]}
    assert phases["intent_parse"]["status"] == "passed"
    assert phases["authorization"]["status"] == "failed"
    for phase in ("resolve", "compile", "publish_preflight"):
        assert phases[phase] == {"id": phase, "status": "blocked", "blocked_by": "authorization"}


@pytest.mark.asyncio
async def test_patch_failure_marks_all_unexecuted_phases_blocked(tmp_path, monkeypatch):
    _, report, _, calls = await generate(tmp_path, monkeypatch, [record_edge(),
        {"op": "connect_control", "source_ref": "lookup", "target_ref": "write"}])
    phases = {item["id"]: item for item in report["generation_diagnostics"][-1]["phase_results"]}
    assert phases["patch_parse"]["status"] == phases["patch_normalization"]["status"] == "passed"
    assert phases["patch_apply"]["status"] == "failed"
    for phase in ("output_normalization", "intent_parse", "authorization", "resolve", "compile", "publish_preflight"):
        assert phases[phase]["status"] == "blocked" and phases[phase]["blocked_by"] == "patch_apply"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_success_is_model_generated_not_fallback(tmp_path, monkeypatch):
    result, report, _, _ = await generate(tmp_path, monkeypatch, [record_edge()])
    assert result.validation["valid"]
    assert report["candidate_origin"] == "model_generated"
    assert report["graph_ir_status"] == "current"
    assert "placeholder_validation" not in report
    assert len(report["validation_candidate_checksum"]) == 64
    assert all(item["status"] == "passed" for item in report["generation_diagnostics"][-1]["phase_results"])


@pytest.mark.asyncio
async def test_recipe_failure_is_isolated_and_survives_store_reload(tmp_path, monkeypatch):
    from server.tests.test_meta_planner_recipe_integration import generate as generate_recipe
    from server.xpert_runtime.authoring_store import AuthoringProposalStore

    def missing_records(recipe):
        next(node for node in recipe["nodes"] if node["ref"] == "write")["inputs"] = []

    headless, authoring, result, calls, _ = await generate_recipe(
        tmp_path, monkeypatch, malformed=missing_records, repaired={"operations": []},
    )
    stored = authoring.proposal_store.require(result.proposal_id)
    report = stored.payload["meta_planner_report"]
    assert "missing_workflow_agent_model" not in json.dumps(report["validation"])
    assert report["validation"]["issues"]
    assert "missing_workflow_agent_model" in json.dumps(report["placeholder_validation"])
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    assert authoring.proposal_store.require(result.proposal_id).payload == stored.payload
    before = authoring.proposal_store.require(result.proposal_id)
    state = headless.proposal_state(result.proposal_id)
    assert state["diagnostics"] and not state["can_approve"]
    assert "missing_workflow_agent_model" not in json.dumps(state["diagnostics"])
    assert authoring.proposal_store.require(result.proposal_id) == before
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(result.proposal_id, revision=1)
    assert len(calls) == 3 and not authoring.xpert_store.list_xperts()


@pytest.mark.asyncio
async def test_real_candidate_validation_is_not_filtered_by_placeholder_error_code(tmp_path, monkeypatch):
    import server.meta_agent.meta_planner_v2 as planner_module
    from server.tests.test_meta_planner_recipe_integration import generate as generate_recipe

    original = planner_module._validation_report

    def reject_candidate(*args, **kwargs):
        validation = original(*args, **kwargs)
        issue = {"code": "missing_workflow_agent_model", "message": "实际候选模型不可用", "severity": "error"}
        validation["stages"].append({"id": "model_preflight", "valid": False, "issues": [issue]})
        validation["issues"].append(issue)
        validation["valid"] = False
        return validation

    monkeypatch.setattr(planner_module, "_validation_report", reject_candidate)
    _, authoring, result, calls, _ = await generate_recipe(tmp_path, monkeypatch, repaired={"operations": [
        {"op": "update_node", "node_ref": "answer", "title": "仍需通过实际候选校验"},
    ]})
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert report["candidate_origin"] == "model_generated"
    assert "placeholder_validation" not in report
    assert "missing_workflow_agent_model" in json.dumps(report["validation"])
    assert "missing_workflow_agent_model" in json.dumps(result.validation)
    assert len(report["validation_candidate_checksum"]) == 64
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(result.proposal_id, revision=1)
    assert not result.validation["valid"] and len(calls) == 3


def test_router_contract_evidence_uses_authority_without_values_or_private_fields():
    graph = guarded_graph(root_nullable=True)
    router = next(node for node in graph.nodes if node.kind == "condition")
    router.config.update(field="PRIVATE_FIELD", value=123456789)
    diag = GenerationDiagnostics("capability_compile")
    diag.bind_graph(graph)
    diag.enter("authorization")
    snap = _snapshot()
    validate_blueprint_authorization(_request(snap), _plan(), graph, snap, diagnostics=diag)
    record = diag.as_dict()["router_inputs"][0]
    assert record["source_status"] == "resolved" and record["source_nullable"]
    assert record["field_declared"] is False and record["operator"] == "lt"
    assert len(record["config_checksum"]) == 64
    assert "PRIVATE_FIELD" not in json.dumps(diag.as_dict())
    assert "123456789" not in json.dumps(diag.as_dict())
