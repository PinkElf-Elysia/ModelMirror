"""Composite pre-Intent failures must be repairable without widening authority."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryPreviewRequest, RecoveryApplyRequest
from server.meta_agent.headless_authoring import HeadlessAuthoringError
from server.tests.meta_planner_recipe_fixtures import step
from server.tests.test_meta_planner_recipe_edits import split_consumer_edits
from server.tests.test_meta_planner_recipe_integration import generate
from server.tests.test_meta_planner_recipe_source_feedback import broken_common_consumer


async def composite(tmp_path, monkeypatch, domain="quality", read_error=False):
    from server.tests import test_meta_planner_recipe_integration as integration, test_meta_planner_write_headless as helpers
    from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
    from server.meta_agent.generation_recipe import lower_generation_recipe
    raw, request, snapshot = broken_common_consumer(domain, read_error)
    valid, _, _ = guarded_recipe(domain, read_error)
    monkeypatch.setattr(helpers, "snapshot", lambda: snapshot)
    monkeypatch.setattr(helpers, "intent", lambda _: lower_generation_recipe(valid, request, snapshot))
    for helper in (helpers, integration):
        monkeypatch.setattr(helper, "request", lambda *_: request.model_copy(deep=True))
    edits = split_consumer_edits(raw)
    raw["control_flow"].append(step("answer"))
    result = await generate(tmp_path, monkeypatch, initial=raw, repaired={"operations": []})
    return *result[:3], edits


def edit_request(state, edits):
    return RecoveryPreviewRequest.model_validate({"mode": "recovery", "artifact_checksum": state["recovery"]["artifact_checksum"],
        "patch": {"protocol_version": "recipe_edits_v1", "proposal_revision": state["proposal_revision"],
                  "expected_graph_checksum": state["graph_checksum"], "expected_candidate_checksum": state["candidate_checksum"], **edits}})


@pytest.mark.asyncio
@pytest.mark.parametrize("domain,read_error", [("quality", False), ("incident", True)])
async def test_composite_repair_preview_apply_restart_is_atomic(tmp_path, monkeypatch, domain, read_error):
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
    headless, authoring, generated, edits = await composite(tmp_path, monkeypatch, domain, read_error)
    state = headless.proposal_state(generated.proposal_id)
    assert state["recovery"]["intent"] is None
    contract = state["recovery"]["semantic_repair"]
    assert contract["protocol_version"] == "recipe_edits_v1"
    assert set(contract["edit_contract"]["operations"]) == {"update_node", "clone_agent", "replace_control_flow", "set_final_output"}
    before = authoring.proposal_store.require(generated.proposal_id)
    recovery = FailedDraftRecovery(headless)
    request = edit_request(state, edits)
    preview = recovery.preview(generated.proposal_id, request)
    assert preview["can_apply"], preview["diagnostics"]
    assert recovery.preview(generated.proposal_id, request)["preview_checksum"] == preview["preview_checksum"]
    assert authoring.proposal_store.require(generated.proposal_id) == before
    assert authoring.xpert_store.list_xperts() == []
    applied = recovery.apply(generated.proposal_id, RecoveryApplyRequest(**request.model_dump(exclude_unset=True), preview_checksum=preview["preview_checksum"]))
    assert applied["proposal_revision"] == 2
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    current = authoring.proposal_store.require(generated.proposal_id)
    assert current.status == "pending" and current.revision == 2
    assert current.payload["_meta_planner_generation_artifact"] == before.payload["_meta_planner_generation_artifact"]
    receipt = current.payload["meta_planner_report"]["authoring_patch_receipts"][-1]
    assert receipt["protocol_version"] == "recipe_edits_v1" and receipt["before_checksum_kind"] == "recipe_v1"
    assert "task_input" not in json.dumps(receipt)
    assert headless.proposal_state(generated.proposal_id).get("mode") != "recovery"
    assert authoring.xpert_store.list_xperts() == []
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(generated.proposal_id, RecoveryApplyRequest(**request.model_dump(exclude_unset=True), preview_checksum=preview["preview_checksum"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["title_only", "omit_write", "model", "resource", "schema_drift"])
async def test_semantic_repair_cannot_hide_failure_or_expand_authority(tmp_path, monkeypatch, attack):
    headless, authoring, generated, edits = await composite(tmp_path, monkeypatch)
    state = headless.proposal_state(generated.proposal_id)
    before = authoring.proposal_store.require(generated.proposal_id)
    recovery = FailedDraftRecovery(headless)
    if attack == "title_only":
        edits = {"operations": [{"op": "update_node", "node_ref": "answer", "title": "只改标题不能修复"}]}
    elif attack == "omit_write":
        edits["operations"][1]["control_flow"] = [step("lookup"), step("answer")]
    elif attack in {"model", "resource"}:
        node = next(n for n in state["recovery"]["recipe"]["nodes"] if n["ref"] == "answer")
        config = deepcopy(node["config"])
        config["model_id" if attack == "model" else "tableId"] = "forged"
        edits["operations"].append({"op": "update_node", "node_ref": "answer", "config": config})
    request = edit_request(state, edits)
    preview = recovery.preview(generated.proposal_id, request)
    if attack == "schema_drift":
        assert preview["can_apply"]
        changed = headless.capability_snapshot_builder().model_copy(deep=True)
        changed.data_tables[0]["schema_versions"][0]["checksum"] = "b" * 64
        headless.capability_snapshot_builder = lambda: changed
    else:
        assert not preview["can_apply"] and preview["candidate"] is None
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(generated.proposal_id, RecoveryApplyRequest(**request.model_dump(exclude_unset=True), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(generated.proposal_id) == before


@pytest.mark.asyncio
async def test_pre_intent_model_suggestion_uses_same_kernel_and_never_auto_applies(tmp_path, monkeypatch):
    from server.meta_agent.model_repair import ExplicitModelRepairService, ModelRepairPreflightRequest
    from server.tests.test_meta_planner_model_repair import ROUTE, consent
    headless, authoring, generated, edits = await composite(tmp_path, monkeypatch)
    state = headless.proposal_state(generated.proposal_id)
    before = authoring.proposal_store.require(generated.proposal_id)
    calls = []
    async def completion(*args):
        args[-1]()
        calls.append(args)
        return {"text": json.dumps(edits), "receipt": {"provider_dispatched": True, "response_received": True}}
    service = ExplicitModelRepairService(headless, route_projection=lambda _: deepcopy(ROUTE), completion=completion)
    request = ModelRepairPreflightRequest(proposal_revision=1, artifact_checksum=state["recovery"]["artifact_checksum"],
        expected_graph_checksum=state["graph_checksum"], expected_candidate_checksum=state["candidate_checksum"], model_id="test/repair")
    preflight = service.preflight(generated.proposal_id, request)
    assert preflight["repair_protocol"] == "recipe_edits_v1" and not calls
    approved = consent(request, preflight)
    result = await service.execute(generated.proposal_id, approved)
    assert result["status"] == "suggested", result
    assert result["patch"]["protocol_version"] == "recipe_edits_v1"
    assert not result["automatically_applied"] and result["preview_summary"]["can_apply"]
    current = authoring.proposal_store.require(generated.proposal_id)
    assert current.payload == before.payload and current.revision == before.revision == 1
    assert current.status == before.status == "pending"
    assert len(current.meta_planner_model_repairs) == 1
    assert authoring.xpert_store.list_xperts() == []
    assert await service.execute(generated.proposal_id, approved) == result and len(calls) == 1
    assert len(authoring.proposal_store.require(generated.proposal_id).meta_planner_model_repairs) == 1


@pytest.mark.asyncio
async def test_semantic_http_roundtrip_preserves_omitted_update_fields(tmp_path, monkeypatch):
    from functools import partial
    from fastapi.testclient import TestClient
    import server.main as main
    from server.tests.test_meta_planner_recipe_lowering_recovery import _HTTPX_SEND
    headless, authoring, generated, edits = await composite(tmp_path, monkeypatch)
    edits["operations"].append({"op": "update_node", "node_ref": "answer", "title": "写入分支结论"})
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: headless)
    client = TestClient(main.app)
    monkeypatch.setattr(client, "send", partial(_HTTPX_SEND, client))
    url = f"/api/meta-agent/authoring/proposals/{generated.proposal_id}"
    state = client.get(url).json()
    payload = edit_request(state, edits).model_dump(mode="json", exclude_unset=True)
    preview = client.post(url + "/patch/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["can_apply"], preview.text
    assert authoring.proposal_store.require(generated.proposal_id).revision == 1
    assert client.post(url + "/patch/apply", json={**payload, "preview_checksum": preview.json()["preview_checksum"]}).status_code == 200
    assert authoring.proposal_store.require(generated.proposal_id).revision == 2
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.parametrize("field,value", [("sourceHandle", "route_1"), ("resource_ref", {"resource_id": "forged"}),
    ("task_ids", ["task"]), ("kind", "data_table_delete"), ("checksum", "a" * 64), ("policy", {})])
def test_recovery_envelope_rejects_operation_authority_injection(field, value):
    from server.meta_agent.failed_recovery import RecipeEditsPatchV1
    with pytest.raises(ValueError):
        RecipeEditsPatchV1.model_validate({"protocol_version": "recipe_edits_v1", "proposal_revision": 1,
            "expected_graph_checksum": "a" * 64, "expected_candidate_checksum": "b" * 64,
            "operations": [{"op": "update_node", "node_ref": "answer", "title": "只改标题", field: value}]})
