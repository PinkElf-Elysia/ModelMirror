from copy import deepcopy
from functools import partial

import httpx
import pytest

from server.meta_agent.headless_authoring import HeadlessAuthoringService, HeadlessAuthoringError
from server.tests.test_meta_planner_failed_artifacts import generate, offline_only
from server.tests.test_meta_planner_controlled_writes import snapshot
from server.xpert_runtime.authoring_store import AuthoringProposalValidationError

_HTTPX_SEND = httpx.Client.send


async def setup_recovery(tmp_path, monkeypatch):
    result, proposal, authoring, calls, graph = await generate(tmp_path, monkeypatch)
    from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
    from server.tests.test_meta_planner_headless_authoring import _preflight
    service = HeadlessAuthoringService(
        authoring_service=authoring,
        planner_service=MetaPlannerV2Service(authoring_service=authoring, preflight=_preflight),
        capability_snapshot_builder=snapshot,
    )
    return service, proposal, authoring, graph


def recovery_request(state, operations):
    from server.meta_agent.failed_recovery import RecoveryPreviewRequest
    return RecoveryPreviewRequest.model_validate({
        "mode": "recovery", "artifact_checksum": state["recovery"]["artifact_checksum"],
        "patch": {
            "proposal_revision": state["proposal_revision"],
            "expected_graph_checksum": state["graph_checksum"],
            "expected_candidate_checksum": state["candidate_checksum"],
            "operations": operations,
        },
    })


REPAIR = [{"op": "connect_data", "source_ref": "lookup", "source_port": "result", "target_ref": "write", "target_port": "records"}]


@pytest.mark.asyncio
async def test_failure_opens_original_not_fallback_and_read_is_side_effect_free(tmp_path, monkeypatch):
    service, proposal, authoring, graph = await setup_recovery(tmp_path, monkeypatch)
    before = authoring.proposal_store.require(proposal.proposal_id)
    state = service.proposal_state(proposal.proposal_id)
    assert state["mode"] == "recovery"
    assert state["recovery"]["intent"] == graph.model_dump(mode="json")
    assert state["recovery"]["executable"] is False
    assert state["recovery"]["selected_attempt_id"] == 2
    assert state["recovery"]["attempts"][-1]["diagnostic_subject"] == "repair_input"
    assert state["can_author"] and not state["can_approve"]
    assert state["diagnostics"], "必须显示原图当前复核错误，而非仅显示占位图失败"
    assert authoring.proposal_store.require(proposal.proposal_id) == before
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(proposal.proposal_id, revision=1)


@pytest.mark.asyncio
async def test_repair_preview_and_apply_only_updates_proposal_once(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    recovery = FailedDraftRecovery(service)
    state = service.proposal_state(proposal.proposal_id)
    request = recovery_request(state, REPAIR)
    before = authoring.proposal_store.require(proposal.proposal_id)
    preview = recovery.preview(proposal.proposal_id, request)
    assert preview["can_apply"], preview["diagnostics"]
    assert recovery.preview(proposal.proposal_id, request)["preview_checksum"] == preview["preview_checksum"]
    assert authoring.proposal_store.require(proposal.proposal_id) == before
    applied = recovery.apply(proposal.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert applied["proposal_revision"] == 2
    current = authoring.proposal_store.require(proposal.proposal_id)
    assert current.validation["valid"]
    assert current.payload["meta_planner_report"]["candidate_origin"] == "human_repaired"
    assert current.payload["_meta_planner_generation_artifact"] == before.payload["_meta_planner_generation_artifact"]
    assert authoring.xpert_store.list_xperts() == []
    assert service.proposal_state(proposal.proposal_id).get("mode") != "recovery"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "candidate", "artifact", "grant", "resource", "contract"])
async def test_preview_binding_rejects_drift(tmp_path, monkeypatch, change):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    recovery = FailedDraftRecovery(service)
    state = service.proposal_state(proposal.proposal_id)
    request = recovery_request(state, REPAIR)
    preview = recovery.preview(proposal.proposal_id, request)
    assert preview["can_apply"]
    if change == "revision":
        authoring.update_pending(proposal.proposal_id, revision=1, title="另一窗口的修改")
    elif change in {"candidate", "artifact", "grant"}:
        current = authoring.proposal_store._items[proposal.proposal_id]
        if change == "candidate":
            current.payload["name"] = "篡改候选"
        elif change == "artifact":
            current.payload["_meta_planner_generation_artifact"]["attempts"][0]["intent"]["name"] = "篡改证据"
        else:
            current.payload["meta_planner_report"]["authorized_scope"]["data_table_write_grants"] = []
    else:
        changed = snapshot()
        if change == "resource":
            changed.data_tables[0]["schema_versions"][0]["checksum"] = "b" * 64
        else:
            changed.nodes[0]["title"] = "变更契约投影"
        service.capability_snapshot_builder = lambda: changed
    before = authoring.proposal_store.require(proposal.proposal_id)
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(proposal.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(proposal.proposal_id) == before
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    {"op": "update_node", "ref": "write", "config": {"tableId": "foreign", "values": {"score": 6}}},
    {"op": "update_node", "ref": "write", "config": {"schema_checksum": "a" * 64}},
    {"op": "set_node_resource", "node_ref": "write", "resource_id": "foreign"},
    {"op": "connect_control", "source_ref": "answer", "target_ref": "lookup"},
    {"op": "update_node", "ref": "write", "task_ids": ["answer"]},
    {"op": "set_final_output", "node_ref": "write", "port": "result"},
    {"op": "remove_node", "ref": "lookup"},
])
async def test_recovery_does_not_relax_patch_or_execution_authority(tmp_path, monkeypatch, operation):
    from server.meta_agent.failed_recovery import FailedDraftRecovery
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    state = service.proposal_state(proposal.proposal_id)
    before = authoring.proposal_store.require(proposal.proposal_id)
    preview = FailedDraftRecovery(service).preview(proposal.proposal_id, recovery_request(state, [*REPAIR, operation]))
    assert not preview["can_apply"]
    assert preview["diagnostics"]
    assert preview["candidate"] is None
    assert authoring.proposal_store.require(proposal.proposal_id) == before


@pytest.mark.asyncio
async def test_unretained_legacy_is_explicit_and_never_reconstructed(tmp_path, monkeypatch):
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    authoring.proposal_store._items[proposal.proposal_id].payload.pop("_meta_planner_generation_artifact")
    state = service.proposal_state(proposal.proposal_id)
    assert not state["can_author"] and not state["can_approve"]
    assert state["recovery"]["status"] == "unavailable"
    assert "intent" not in state["recovery"]
    assert "not_retained" in state["recovery"]["reason"]


@pytest.mark.asyncio
async def test_recovery_restarts_and_routes_use_same_guarded_kernel(tmp_path, monkeypatch):
    import server.main as main
    from fastapi.testclient import TestClient
    from server.xpert_runtime.authoring_store import AuthoringProposalStore

    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: service)
    client = TestClient(main.app)
    # Only this in-process TestClient bypasses the provider-send tripwire.
    # All other clients and real sockets remain blocked by the offline fixture.
    monkeypatch.setattr(client, "send", partial(_HTTPX_SEND, client))
    url = f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}"
    response = client.get(url)
    assert response.status_code == 200
    state = response.json()
    request = recovery_request(state, REPAIR).model_dump(mode="json")
    preview = client.post(url + "/patch/preview", json=request)
    assert preview.status_code == 200 and preview.json()["can_apply"]
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1
    forged = client.post(url + "/patch/apply", json={**request, "preview_checksum": "0" * 64})
    assert forged.status_code == 409
    applied = client.post(url + "/patch/apply", json={**request, "preview_checksum": preview.json()["preview_checksum"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["proposal_revision"] == 2
    assert client.post(url + "/patch/apply", json={**request, "preview_checksum": preview.json()["preview_checksum"]}).status_code == 409
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_invalid_partial_patch_cannot_apply_or_overwrite_evidence(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    recovery = FailedDraftRecovery(service)
    state = service.proposal_state(proposal.proposal_id)
    request = recovery_request(state, [{"op": "set_xpert_metadata", "name": "仍未修好的图"}])
    before = authoring.proposal_store.require(proposal.proposal_id)
    preview = recovery.preview(proposal.proposal_id, request)
    assert not preview["can_apply"] and preview["diagnostics"]
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(proposal.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(proposal.proposal_id) == before


@pytest.mark.asyncio
async def test_ordinary_authoring_cannot_consume_failed_fallback(tmp_path, monkeypatch):
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    before = authoring.proposal_store.require(proposal.proposal_id)
    for action in (service.preview, service.apply, service.editor_diff):
        with pytest.raises(HeadlessAuthoringError) as error:
            action(proposal.proposal_id, None)
        assert error.value.code == "recovery_required"
    assert authoring.proposal_store.require(proposal.proposal_id) == before


@pytest.mark.asyncio
async def test_preview_receipt_is_bound_to_proposal_identity(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    recovery = FailedDraftRecovery(service)
    clone = deepcopy(proposal)
    clone.proposal_id = "proposal_other"
    authoring.proposal_store._items[clone.proposal_id] = clone
    request = recovery_request(service.proposal_state(proposal.proposal_id), REPAIR)
    preview = recovery.preview(proposal.proposal_id, request)
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(clone.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(clone.proposal_id) == clone


@pytest.mark.asyncio
async def test_snapshot_drift_during_final_validation_prevents_commit(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    recovery = FailedDraftRecovery(service)
    request = recovery_request(service.proposal_state(proposal.proposal_id), REPAIR)
    preview = recovery.preview(proposal.proposal_id, request)
    before = authoring.proposal_store.require(proposal.proposal_id)
    validate = authoring._validate_payload

    def change_resource_after_validation(detached):
        result = validate(detached)
        changed = snapshot()
        changed.data_tables[0]["schema_versions"][0]["checksum"] = "c" * 64
        service.capability_snapshot_builder = lambda: changed
        return result

    monkeypatch.setattr(authoring, "_validate_payload", change_resource_after_validation)
    with pytest.raises(HeadlessAuthoringError) as error:
        recovery.apply(proposal.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert error.value.status_code == 409
    assert authoring.proposal_store.require(proposal.proposal_id) == before


def refresh_artifact_checksum(proposal):
    from server.meta_agent.headless_authoring import canonical_checksum
    artifact = proposal.payload["_meta_planner_generation_artifact"]
    artifact["checksum"] = canonical_checksum({key: value for key, value in artifact.items() if key != "checksum"})


@pytest.mark.asyncio
async def test_no_effect_patch_is_not_a_human_repair(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery
    from server.meta_agent.headless_authoring import canonical_checksum
    from server.tests.test_meta_planner_controlled_writes import intent
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    current = authoring.proposal_store._items[proposal.proposal_id]
    # Model a formerly failed graph that becomes valid after an environment fix.
    valid = intent("update").model_dump(mode="json")
    for attempt in current.payload["_meta_planner_generation_artifact"]["attempts"]:
        attempt["intent"] = valid
        attempt["intent_checksum"] = canonical_checksum(valid)
    refresh_artifact_checksum(current)
    state = service.proposal_state(proposal.proposal_id)
    request = recovery_request(state, [{"op": "set_xpert_metadata", "name": valid["name"]}])
    preview = FailedDraftRecovery(service).preview(proposal.proposal_id, request)
    assert preview["validation"]["valid"]
    assert not preview["can_apply"]
    assert any(item["code"] == "no_effect" for item in preview["diagnostics"])
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


@pytest.mark.asyncio
async def test_attempt_projection_does_not_disclose_extra_or_nested_fields(tmp_path, monkeypatch):
    import json
    from server.meta_agent.headless_authoring import canonical_checksum
    service, proposal, authoring, _ = await setup_recovery(tmp_path, monkeypatch)
    current = authoring.proposal_store._items[proposal.proposal_id]
    for attempt in current.payload["_meta_planner_generation_artifact"]["attempts"]:
        attempt["raw_response"] = "PRIVATE_ATTEMPT_CANARY"
        attempt["diagnostics"]["physical_path"] = "PRIVATE_NESTED_CANARY"
        attempt["diagnostics"]["issues"][0]["raw_response"] = "PRIVATE_ISSUE_CANARY"
        attempt["diagnostics_checksum"] = canonical_checksum(attempt["diagnostics"])
    refresh_artifact_checksum(current)
    response = service.proposal_state(proposal.proposal_id)
    assert response["can_author"]
    body = json.dumps(response)
    assert "PRIVATE_ATTEMPT_CANARY" not in body
    assert "PRIVATE_NESTED_CANARY" not in body
    assert "PRIVATE_ISSUE_CANARY" not in body
    assert response["recovery"]["attempts"][-1]["diagnostics"]["issues"]
