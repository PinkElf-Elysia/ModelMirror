"""The offered Recipe contract must also decide the bounded repair protocol."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

from server.meta_agent.failed_artifacts import load_failed_generation_artifact
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.generation_recipe import (
    parse_generation_recipe, recipe_schema, validate_recipe_generation_contract,
)
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, RECIPE_SYSTEM_PROMPT
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request, snapshot
from server.tests.test_meta_planner_recipe_integration import generate


def _broken_resource(raw, mode):
    node = next(item for item in raw["nodes"] if item["kind"] == "data_table_update")
    if mode == "missing":
        node.pop("resource_ref")
    else:
        node["resource_ref"] = None if mode == "null" else {"resource_id": "foreign-table-canary"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "null", "foreign"])
async def test_resource_contract_failure_uses_one_strict_recipe_repair(tmp_path, monkeypatch, mode):
    corrected = from_intent(intent("update"))
    _, authoring, result, calls, raw = await generate(
        tmp_path, monkeypatch, malformed=lambda raw: _broken_resource(raw, mode), repaired=corrected,
    )
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert report["repair_protocol"] == "generation_recipe_v1"
    assert len(calls) == 3 and result.validation["valid"]
    assert calls[2][1] == RECIPE_SYSTEM_PROMPT
    initial_prompt, repair_prompt = (json.loads(call[2]) for call in calls[1:])
    assert initial_prompt["required_schema"] == repair_prompt["required_schema"]
    assert not Draft202012Validator(initial_prompt["required_schema"]).is_valid(raw)
    assert Draft202012Validator(repair_prompt["required_schema"]).is_valid(corrected)
    first = next(row for row in report["generation_diagnostics"] if row["stage"] == "capability_compile")
    issues = [issue for issue in first["issues"] if issue["code"] == "RECIPE_EFFECTIVE_CONTRACT_INVALID"]
    assert issues and all(issue["category"] == "intent_parse" for issue in issues)
    assert any(issue["location"][:3] == ["nodes", 1, "resource_ref"] for issue in issues)
    assert "foreign-table-canary" not in json.dumps(first)
    stored = authoring.proposal_store.require(result.proposal_id)
    assert stored.status == "pending" and stored.revision == 1
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_invalid_strict_repair_is_retained_without_fourth_call_or_resource_fill(tmp_path, monkeypatch):
    broken = from_intent(intent("update"))
    _broken_resource(broken, "missing")
    before = deepcopy(broken)
    _, authoring, result, calls, _ = await generate(
        tmp_path, monkeypatch, initial=broken, repaired=broken,
    )
    assert len(calls) == 3 and result.validation["valid"] is False
    stored = authoring.proposal_store.require(result.proposal_id)
    assert stored.payload["meta_planner_report"]["repair_protocol"] == "generation_recipe_v1"
    artifact = load_failed_generation_artifact(stored)
    assert artifact["attempts"][0]["recipe"]["nodes"][1]["resource_ref"] is None
    assert broken == before
    assert authoring.xpert_store.list_xperts() == []


def test_legacy_recipe_is_readable_but_impossible_edits_are_blocked_before_dispatch():
    snap = snapshot()
    raw = from_intent(intent("update"))
    _broken_resource(raw, "missing")
    legacy = parse_generation_recipe(raw)
    assert legacy.nodes[1].resource_ref is None
    with pytest.raises(ValidationError) as caught:
        MetaPlannerV2Service._recipe_edit_prompt(request(snap, "update"), _plan(), snap, None, recipe=legacy)
    diagnostics = GenerationDiagnostics(stage="repair_preparation")
    diagnostics.enter("repair_preparation")
    diagnostics.exception(caught.value)
    assert "RECIPE_EFFECTIVE_CONTRACT_INVALID" in json.dumps(diagnostics.as_dict())


@pytest.mark.parametrize("attack", [
    "valid", "missing", "null", "foreign", "kind", "task", "config", "handle",
    "version", "boolean_limit", "string_limit", "extra_resource", "write_grant",
])
def test_validator_matches_offered_schema_and_does_not_disclose_rejected_values(attack):
    snap = snapshot()
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    if attack in {"missing", "null", "foreign"}:
        _broken_resource(raw, attack)
    elif attack == "kind":
        raw["nodes"][1]["kind"] = "PRIVATE_KIND_CANARY"
    elif attack == "task":
        raw["nodes"][1]["task_ids"] = ["PRIVATE_TASK_CANARY"]
    elif attack in {"config", "handle", "version"}:
        key = {"config": "PRIVATE_FIELD_CANARY", "handle": "sourceHandle", "version": "pinnedSchemaVersion"}[attack]
        raw["nodes"][1]["config"][key] = "PRIVATE_VALUE_CANARY"
    elif attack in {"boolean_limit", "string_limit"}:
        raw["nodes"][0]["config"]["limit"] = True if attack == "boolean_limit" else "20"
    elif attack == "extra_resource":
        raw["resources"] = [{"target_ref": "answer", "kind": "toolset_resource", "resource_id": "PRIVATE_RESOURCE_CANARY"}]
    elif attack == "write_grant":
        req.scope.data_table_write_grants = []
    before = deepcopy(raw)
    offered = recipe_schema(req, snap)
    expected = Draft202012Validator(offered).is_valid(raw)
    assert expected is (attack == "valid")
    if expected:
        validate_recipe_generation_contract(raw, req, snap)
    else:
        with pytest.raises(ValidationError) as caught:
            validate_recipe_generation_contract(raw, req, snap)
        observer = GenerationDiagnostics("generation_recipe_v1")
        observer.enter("intent_parse")
        observer.exception(caught.value)
        summary = observer.as_dict()
        assert summary["issues"] and len(summary["issues"]) <= 64
        assert all(issue["code"] == "RECIPE_EFFECTIVE_CONTRACT_INVALID" for issue in summary["issues"])
        assert "PRIVATE_" not in json.dumps(summary)
        assert "foreign-table-canary" not in json.dumps(summary)
        if attack in {"missing", "null", "foreign"}:
            assert all(issue["location"][:3] == ["nodes", 1, "resource_ref"] for issue in summary["issues"])
    assert raw == before


def test_multiple_authorized_tables_never_fill_or_change_a_model_resource_choice():
    snap = snapshot()
    second = deepcopy(snap.data_tables[0])
    second["id"] = "table-other"
    snap.data_tables.append(second)
    req = request(snap, "update")
    req.scope.data_table_ids.append("table-other")
    grant = req.scope.data_table_write_grants[0].model_copy(update={"table_id": "table-other"})
    req.scope.data_table_write_grants.append(grant)
    raw = from_intent(intent("update"))
    for resource_id in ("table-orders", "table-other"):
        raw["nodes"][1]["resource_ref"] = {"resource_id": resource_id}
        validate_recipe_generation_contract(raw, req, snap)
        assert raw["nodes"][1]["resource_ref"] == {"resource_id": resource_id}
    _broken_resource(raw, "missing")
    with pytest.raises(ValidationError):
        validate_recipe_generation_contract(raw, req, snap)
    assert "resource_ref" not in raw["nodes"][1]


@pytest.mark.parametrize("domain", ["quality", "incident"])
def test_schema_acceptance_does_not_replace_occurrence_or_path_validation(domain):
    from server.meta_agent.generation_recipe import lower_generation_recipe
    from server.tests.test_meta_planner_recipe_occurrence_feedback import repeated_consumers

    raw, req, snap = repeated_consumers(domain)
    validate_recipe_generation_contract(raw, req, snap)
    observer = GenerationDiagnostics("generation_recipe_v1")
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, req, snap, diagnostics=observer)
    assert any(issue["code"] == "RECIPE_REPEATED_NODE" for issue in observer.as_dict()["issues"])
    prompt = json.loads(MetaPlannerV2Service._recipe_edit_prompt(req, _plan(), snap, None, recipe=parse_generation_recipe(raw)))
    assert prompt["repair_focus"]["branch_repair_required"] is True
    assert any(issue["code"] == "DATA_PATH_NOT_GUARANTEED" for issue in prompt["repair_focus"]["issues"])


@pytest.mark.asyncio
async def test_explicit_repair_api_rejects_contract_failure_without_ticket_or_dispatch(tmp_path, monkeypatch):
    from functools import partial
    import httpx
    from fastapi.testclient import TestClient
    import server.main as main
    from server.meta_agent.model_repair import ExplicitModelRepairService, ModelRepairPreflightRequest
    from server.tests.test_meta_planner_model_repair import ROUTE

    send = httpx.Client.send
    broken = from_intent(intent("update"))
    _broken_resource(broken, "missing")
    headless, authoring, result, _, _ = await generate(tmp_path, monkeypatch, initial=broken, repaired=broken)
    state = headless.proposal_state(result.proposal_id)
    req = ModelRepairPreflightRequest(proposal_revision=1, artifact_checksum=state["recovery"]["artifact_checksum"],
        expected_graph_checksum=state["graph_checksum"], expected_candidate_checksum=state["candidate_checksum"], model_id="test/repair")

    async def forbidden(*_):
        pytest.fail("Contract-invalid Recipe must not dispatch a repair completion")

    service = ExplicitModelRepairService(headless, route_projection=lambda _: deepcopy(ROUTE), completion=forbidden)
    monkeypatch.setattr(main, "get_explicit_model_repair_service", lambda: service)
    client = TestClient(main.app)
    monkeypatch.setattr(client, "send", partial(send, client))
    response = client.post(f"/api/meta-agent/authoring/proposals/{result.proposal_id}/repair/preflight", json=req.model_dump())
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "repair_recipe_contract_invalid"
    assert "authorization_token" not in response.text
    assert service.history(result.proposal_id) == {"attempts": []}
    assert authoring.proposal_store.require(result.proposal_id).revision == 1
    assert authoring.xpert_store.list_xperts() == []
