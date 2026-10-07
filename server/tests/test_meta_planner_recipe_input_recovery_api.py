"""Manual input repair stays bound to the retained proposal and original grants."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest, RecoveryPreviewRequest
from server.meta_agent.headless_authoring import HeadlessAuthoringError, _canonical_patch_receipts
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.test_meta_planner_controlled_writes import request, _plan
from server.tests.test_meta_planner_recipe_input_recovery import input_failure
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture
from server.xpert_runtime.authoring_store import AuthoringProposalStore, AuthoringProposalValidationError


async def setup_failure(tmp_path, monkeypatch, *, missing_protocol=False):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch, "update")
    raw = input_failure(missing_protocol=missing_protocol)
    responses = [_plan().model_dump_json(), json.dumps(raw), json.dumps(raw)]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        return responses[len(calls) - 1]

    planner = MetaPlannerV2Service(authoring_service=authoring, preflight=headless.planner_service.preflight, completion=completion)
    result = await planner.generate(request(snap, "update"), snap)
    headless.planner_service = planner
    assert len(calls) == 3 and not result.validation["valid"]
    return headless, authoring, result, calls, snap


def patch_for(state, *, confirm_protocol=False):
    operations = [{"op": "replace_recipe_inputs", "node_ref": "lookup", "inputs": []}]
    if confirm_protocol:
        operations.insert(0, {"op": "confirm_recipe_protocol"})
    return RecoveryPreviewRequest.model_validate({
        "mode": "recovery", "artifact_checksum": state["recovery"]["artifact_checksum"],
        "patch": {"protocol_version": "recipe_inputs_v1", "proposal_revision": state["proposal_revision"],
                  "expected_graph_checksum": state["graph_checksum"], "expected_candidate_checksum": state["candidate_checksum"],
                  "operations": operations},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_protocol", [False, True])
async def test_input_repair_preview_apply_restart_and_no_execution(tmp_path, monkeypatch, missing_protocol):
    headless, authoring, result, calls, _ = await setup_failure(tmp_path, monkeypatch, missing_protocol=missing_protocol)
    before = authoring.proposal_store.require(result.proposal_id)
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(result.proposal_id, revision=1)
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    state = headless.proposal_state(result.proposal_id)
    assert state["can_author"] and not state["can_approve"]
    assert state["recovery"]["source_format"] == "recipe_input_draft_v1"
    assert state["recovery"]["recipe"]["nodes"][0]["inputs"] is None
    assert state["recovery"]["node_contracts"]["data_table_query"]["input_mode"]["null_allowed"] is False
    assert any(issue.get("schema_detail") == {"expected": "array", "actual": "null", "input_mode": "explicit"}
        for attempt in state["recovery"]["attempts"] for issue in attempt["diagnostics"]["issues"])
    recovery = FailedDraftRecovery(headless)
    patch = patch_for(state, confirm_protocol=missing_protocol)
    preview = recovery.preview(result.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    assert authoring.proposal_store.require(result.proposal_id) == before
    recovery.apply(result.proposal_id, RecoveryApplyRequest(**patch.model_dump(), preview_checksum=preview["preview_checksum"]))
    current = authoring.proposal_store.require(result.proposal_id)
    assert current.revision == 2 and current.status == "pending"
    assert current.payload["_meta_planner_generation_artifact"] == before.payload["_meta_planner_generation_artifact"]
    receipt = current.payload["meta_planner_report"]["authoring_patch_receipts"][-1]
    assert receipt["before_checksum_kind"] == "recipe_input_draft_v1"
    assert _canonical_patch_receipts([receipt]) == [receipt]
    assert "inputs" not in receipt and len(calls) == 3
    assert authoring.xpert_store.list_xperts() == []
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    restored = authoring.proposal_store.require(result.proposal_id)
    assert restored.meta_planner_recipe_recovery["revision"] == 2
    assert authoring._validate_payload(restored)["resource_kind"] == "xpert"
    restored_state = headless.proposal_state(result.proposal_id)
    assert restored_state["can_author"] and restored_state.get("mode") != "recovery"
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(result.proposal_id, RecoveryApplyRequest(**patch.model_dump(), preview_checksum=preview["preview_checksum"]))


@pytest.mark.asyncio
async def test_protocol_confirmation_is_explicit_and_does_not_grant_model_repair(tmp_path, monkeypatch):
    from server.meta_agent.model_repair import ExplicitModelRepairService
    headless, authoring, result, calls, _ = await setup_failure(tmp_path, monkeypatch, missing_protocol=True)
    state = headless.proposal_state(result.proposal_id)
    recovery = FailedDraftRecovery(headless)
    assert not recovery.preview(result.proposal_id, patch_for(state))["can_apply"]
    service = ExplicitModelRepairService(headless, route_projection=lambda *_: pytest.fail("must not read provider route"))
    with pytest.raises(HeadlessAuthoringError) as error:
        service._prepare(result.proposal_id, None)
    assert error.value.code == "repair_recipe_requires_manual_inputs"
    assert authoring.proposal_store.require(result.proposal_id).revision == 1 and len(calls) == 3


@pytest.mark.asyncio
async def test_input_recovery_http_uses_the_same_preview_and_apply_guard(tmp_path, monkeypatch):
    from functools import partial
    from fastapi.testclient import TestClient
    import server.main as main
    from server.tests.test_meta_planner_recipe_lowering_recovery import _HTTPX_SEND
    headless, authoring, result, calls, _ = await setup_failure(tmp_path, monkeypatch, missing_protocol=True)
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: headless)
    client = TestClient(main.app)
    monkeypatch.setattr(client, "send", partial(_HTTPX_SEND, client))
    url = f"/api/meta-agent/authoring/proposals/{result.proposal_id}"
    state = client.get(url).json()
    payload = patch_for(state, confirm_protocol=True).model_dump(mode="json")
    preview = client.post(url + "/patch/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["can_apply"], preview.text
    assert authoring.proposal_store.require(result.proposal_id).revision == 1
    body = {**payload, "preview_checksum": preview.json()["preview_checksum"]}
    applied = client.post(url + "/patch/apply", json=body)
    assert applied.status_code == 200 and applied.json()["proposal_revision"] == 2, applied.text
    assert client.post(url + "/patch/apply", json=body).status_code == 409
    assert len(calls) == 3 and authoring.xpert_store.list_xperts() == []


@pytest.mark.parametrize("attack", ["config", "scope", "version", "handle", "variable", "schema", "operation", "limit"])
def test_input_patch_cannot_carry_other_authority(attack):
    state = {"recovery": {"artifact_checksum": "a" * 64}, "proposal_revision": 1,
             "graph_checksum": "b" * 64, "candidate_checksum": "c" * 64}
    body = patch_for(state, confirm_protocol=True).model_dump(mode="json")
    operation = body["patch"]["operations"][1]
    if attack in {"config", "scope"}:
        operation[attack] = {}
    elif attack == "version":
        body["patch"]["operations"][0]["generation_protocol_version"] = 1
    elif attack == "operation":
        operation["op"] = "replace_recipe"
    elif attack == "limit":
        body["patch"]["operations"] *= 13
    else:
        binding = {"port": "records", "source_ref": "lookup", "source_port": "result"}
        binding[{"handle": "targetHandle", "variable": "variable", "schema": "value_schema"}[attack]] = "injected"
        operation["inputs"] = [binding]
    with pytest.raises(ValidationError):
        RecoveryPreviewRequest.model_validate(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["duplicate", "unknown_node", "forged_records", "schema_drift"])
async def test_input_recovery_rejects_unsafe_or_stale_change(tmp_path, monkeypatch, attack):
    headless, authoring, result, calls, snap = await setup_failure(tmp_path, monkeypatch)
    state = headless.proposal_state(result.proposal_id)
    recovery = FailedDraftRecovery(headless)
    body = patch_for(state).model_dump(mode="json")
    if attack == "duplicate": body["patch"]["operations"] *= 2
    elif attack == "unknown_node": body["patch"]["operations"][0]["node_ref"] = "unknown"
    elif attack == "forged_records": body["patch"]["operations"].append({"op": "replace_recipe_inputs", "node_ref": "write", "inputs": [{"port": "records", "source_ref": "input", "source_port": "user_input"}]})
    patch = RecoveryPreviewRequest.model_validate(body)
    preview = recovery.preview(result.proposal_id, patch)
    if attack == "schema_drift":
        assert preview["can_apply"]
        changed = deepcopy(snap)
        changed.data_tables[0]["schema_checksum"] = "b" * 64
        changed.data_tables[0]["schema_versions"][0]["checksum"] = "b" * 64
        monkeypatch.setattr(headless, "capability_snapshot_builder", lambda: changed)
        with pytest.raises(HeadlessAuthoringError):
            recovery.apply(result.proposal_id, RecoveryApplyRequest(**patch.model_dump(), preview_checksum=preview["preview_checksum"]))
    else:
        assert not preview["can_apply"]
    assert authoring.proposal_store.require(result.proposal_id).revision == 1 and len(calls) == 3
