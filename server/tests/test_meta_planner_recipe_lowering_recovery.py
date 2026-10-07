"""Synthetic counterexamples, not a replay of the unretained E-G1 response."""
from copy import deepcopy
from functools import partial
import json

import httpx
import pytest

from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.generation_recipe import GenerationRecipeV1, lower_generation_recipe
from server.tests.meta_planner_recipe_fixtures import from_intent, step
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot
from server.tests.test_meta_planner_recipe_integration import generate

_HTTPX_SEND = httpx.Client.send


def test_nested_branch_repetition_has_both_locations_without_rewriting_edges():
    from server.tests.test_meta_planner_control_flow import _branch_intent
    raw = from_intent(_branch_intent(), [step("router", {
        "matched": [step("approved")], "unmatched": [step("approved")],
    })])
    before = deepcopy(raw)
    observer = GenerationDiagnostics("capability_compile")
    observer.enter("recipe_lowering")
    snap = snapshot()
    with pytest.raises(ValueError) as error:
        lower_generation_recipe(raw, request(snap), snap)
    observer.exception(error.value)
    issue = observer.as_dict()["issues"][0]
    assert issue["code"] == "RECIPE_REPEATED_NODE"
    assert issue["location"] == ["control_flow", 0, "branches", 1, "steps", 0, "node_ref"]
    assert issue["recipe_detail"]["first_location"] == ["control_flow", 0, "branches", 0, "steps", 0, "node_ref"]
    assert raw == before


def test_recipe_diagnostics_are_redacted_deduplicated_and_bounded():
    from server.meta_agent.generation_diagnostics import MAX_DIAGNOSTIC_ISSUES, RecipeControlFlowError
    observer = GenerationDiagnostics("capability_compile")
    observer.enter("recipe_lowering")
    error = RecipeControlFlowError("RECIPE_REPEATED_NODE", "sk-PRIVATE_CANARY", ["control_flow", 1, "node_ref"],
                                 first_location=["control_flow", 0, "node_ref"])
    observer.exception(error)
    observer.exception(error)
    assert len(observer.issues) == 1 and "node_ref" not in observer.issues[0]
    for index in range(MAX_DIAGNOSTIC_ISSUES):
        observer.exception(RecipeControlFlowError("RECIPE_UNKNOWN_NODE", f"unknown_{index}", ["control_flow", index, "node_ref"]))
    before = deepcopy(observer.issues)
    observer.exception(RecipeControlFlowError("RECIPE_REPEATED_NODE", "overflow", ["control_flow", 99, "node_ref"],
                                             first_location=["control_flow", 98, "node_ref"]))
    assert observer.issues == before and len(before) == MAX_DIAGNOSTIC_ISSUES
    assert "PRIVATE_CANARY" not in json.dumps(observer.as_dict())


@pytest.mark.parametrize("missing", [False, True])
def test_control_reference_diagnostics_distinguish_missing_and_repeated(missing):
    snap = snapshot()
    raw = from_intent(intent("update"))
    raw["control_flow"].append(step("unknown" if missing else "answer"))
    before = deepcopy(raw)
    observer = GenerationDiagnostics("capability_compile")
    observer.enter("recipe_lowering")
    with pytest.raises(ValueError) as error:
        lower_generation_recipe(raw, request(snap, "update"), snap)
    observer.exception(error.value)
    issue = observer.as_dict()["issues"][0]
    assert issue["code"] == ("RECIPE_UNKNOWN_NODE" if missing else "RECIPE_REPEATED_NODE")
    assert issue["location"] == ["control_flow", 4, "node_ref"]
    assert raw == before


@pytest.mark.asyncio
async def test_lowering_failure_is_retained_and_repair_receives_structured_cause(tmp_path, monkeypatch):
    raw = from_intent(intent("update"))
    raw["control_flow"].append(step("answer"))
    headless, authoring, result, calls, _ = await generate(
        tmp_path, monkeypatch, initial=raw, repaired={"operations": [{"op": "replace_control_flow", "control_flow": raw["control_flow"]}]},
    )
    assert len(calls) == 3 and not result.validation["valid"]
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    compilation = next(item for item in report["generation_diagnostics"] if item["stage"] == "capability_compile")
    assert compilation["failed_phase"] == "recipe_lowering"
    repair = json.loads(calls[2][2])
    problem = repair["lowering_diagnostics"]["issues"][0]
    assert problem["code"] == "RECIPE_REPEATED_NODE"
    assert problem["node_ref"] == "answer"
    assert problem["recipe_detail"]["first_location"] == ["control_flow", 3, "node_ref"]
    assert "RECIPE_REPAIR_UNCHANGED" in json.dumps(report["generation_diagnostics"])
    assert report["failure_artifact"]["recoverable"] is True
    state = headless.proposal_state(result.proposal_id)
    assert state["recovery"]["source_format"] == "recipe_v1"
    assert state["recovery"]["recipe"] == GenerationRecipeV1.model_validate(raw).model_dump(mode="json")
    assert state["can_author"] and not state["can_approve"]
    assert authoring.xpert_store.list_xperts() == []


async def setup_recipe_recovery(tmp_path, monkeypatch, mutate=None):
    raw = from_intent(intent("update"))
    if mutate:
        mutate(raw)
    raw["control_flow"].append(step("answer"))
    return await generate(tmp_path, monkeypatch, initial=raw,
        repaired={"operations": [{"op": "replace_control_flow", "control_flow": raw["control_flow"]}]})


def flow_patch(state, flow):
    from server.meta_agent.failed_recovery import RecoveryPreviewRequest
    return RecoveryPreviewRequest.model_validate({
        "mode": "recovery", "artifact_checksum": state["recovery"]["artifact_checksum"],
        "patch": {
            "protocol_version": "recipe_control_flow_v1",
            "proposal_revision": state["proposal_revision"],
            "expected_graph_checksum": state["graph_checksum"],
            "expected_candidate_checksum": state["candidate_checksum"],
            "operations": [{"op": "replace_recipe_control_flow", "control_flow": flow}],
        },
    })


@pytest.mark.asyncio
async def test_recipe_http_routes_preview_apply_and_reject_replay(tmp_path, monkeypatch):
    import server.main as main
    from fastapi.testclient import TestClient
    headless, authoring, result, calls, raw = await setup_recipe_recovery(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: headless)
    client = TestClient(main.app)
    # Only the in-process ASGI client is exempt; external sockets stay blocked.
    monkeypatch.setattr(client, "send", partial(_HTTPX_SEND, client))
    url = f"/api/meta-agent/authoring/proposals/{result.proposal_id}"
    response = client.get(url)
    assert response.status_code == 200
    state = response.json()
    assert state["recovery"]["intent"] is None
    payload = flow_patch(state, raw["control_flow"][:-1]).model_dump(mode="json")
    preview = client.post(url + "/patch/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["can_apply"], preview.text
    assert authoring.proposal_store.require(result.proposal_id).revision == 1
    request = {**payload, "preview_checksum": preview.json()["preview_checksum"]}
    assert client.post(url + "/patch/apply", json={**request, "preview_checksum": "0" * 64}).status_code == 409
    applied = client.post(url + "/patch/apply", json=request)
    assert applied.status_code == 200 and applied.json()["proposal_revision"] == 2, applied.text
    assert client.post(url + "/patch/apply", json=request).status_code == 409
    assert len(calls) == 3 and authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_recipe_manual_repair_roundtrip_restarts_without_executing_or_approving(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    from server.xpert_runtime.authoring_store import AuthoringProposalStore, AuthoringProposalValidationError

    headless, authoring, result, calls, raw = await setup_recipe_recovery(tmp_path, monkeypatch)
    stored = authoring.proposal_store.require(result.proposal_id)
    snapshot_data = json.loads(authoring.proposal_store.snapshot_path.read_text(encoding="utf-8"))
    assert all("meta_planner_recipe_recovery" not in item for item in snapshot_data["items"])
    private = deepcopy(stored.payload["_meta_planner_generation_artifact"])
    assert all(item["intent"] is None for item in private["attempts"])
    assert [item["diagnostic_subject"] for item in private["attempts"]] == ["recipe_result", "repair_input"]
    assert private["attempts"][1]["recipe"] == private["attempts"][0]["recipe"]
    assert private["attempts"][1]["result_intent_checksum"] is None
    assert "role_prompt" not in json.dumps(private["attempts"][0]["diagnostics"])
    for payload in (True, False):
        public = AuthoringProposalStore.serialize(stored, include_payload=payload)
        assert "_meta_planner_generation_artifact" not in json.dumps(public)
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(result.proposal_id, revision=1)
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    recovery = FailedDraftRecovery(headless)
    state = headless.proposal_state(result.proposal_id)
    request = flow_patch(state, raw["control_flow"][:-1])
    preview = recovery.preview(result.proposal_id, request)
    assert preview["can_apply"], preview["diagnostics"]
    assert preview["diff"]["source"] == "retained_recipe"
    assert recovery.preview(result.proposal_id, request)["preview_checksum"] == preview["preview_checksum"]
    assert authoring.proposal_store.require(result.proposal_id) == stored
    applied = recovery.apply(result.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert applied["proposal_revision"] == 2
    current = authoring.proposal_store.require(result.proposal_id)
    assert current.status == "pending"
    assert current.payload["meta_planner_report"]["candidate_origin"] == "human_repaired"
    receipt = current.payload["meta_planner_report"]["authoring_patch_receipts"][-1]
    assert receipt["protocol_version"] == "recipe_control_flow_v1"
    assert receipt["before_checksum_kind"] == "recipe_v1"
    assert current.payload["_meta_planner_generation_artifact"] == private
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    restored = authoring.proposal_store.require(result.proposal_id)
    assert restored.meta_planner_recipe_recovery == {"artifact_checksum": private["checksum"], "revision": 2}
    assert "meta_planner_recipe_recovery" not in AuthoringProposalStore.serialize(restored, include_payload=True)
    assert authoring._validate_payload(restored)["resource_kind"] == "xpert"
    assert len(calls) == 3 and authoring.xpert_store.list_xperts() == []
    assert headless.proposal_state(result.proposal_id).get("mode") != "recovery"


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["unchanged", "omit_write", "unknown", "duplicate", "reverse"])
async def test_invalid_flow_cannot_apply_or_skip_semantic_checks(tmp_path, monkeypatch, attack):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    from server.meta_agent.headless_authoring import HeadlessAuthoringError
    headless, authoring, result, calls, raw = await setup_recipe_recovery(tmp_path, monkeypatch)
    flow = deepcopy(raw["control_flow"][:-1])
    if attack == "unchanged":
        flow = raw["control_flow"]
    elif attack == "omit_write":
        flow = [item for item in flow if item["node_ref"] != "write"]
    elif attack == "unknown":
        flow[0]["node_ref"] = "not_declared"
    elif attack == "duplicate":
        flow[0]["node_ref"] = "write"
    else:
        flow.reverse()
    recovery = FailedDraftRecovery(headless)
    state = headless.proposal_state(result.proposal_id)
    request = flow_patch(state, flow)
    before = authoring.proposal_store.require(result.proposal_id)
    preview = recovery.preview(result.proposal_id, request)
    assert not preview["can_apply"] and preview["candidate"] is None
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(result.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(result.proposal_id) == before
    assert len(calls) == 3 and authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_repaired_flow_does_not_hide_invalid_data_bindings(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery
    headless, authoring, result, _, raw = await setup_recipe_recovery(
        tmp_path, monkeypatch, lambda value: value["nodes"][1].update(inputs=[]),
    )
    state = headless.proposal_state(result.proposal_id)
    preview = FailedDraftRecovery(headless).preview(result.proposal_id, flow_patch(state, raw["control_flow"][:-1]))
    assert not preview["can_apply"]
    assert preview["recovery_diagnostics"]["failed_phase"] == "authorization"
    assert authoring.proposal_store.require(result.proposal_id).revision == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["candidate", "artifact", "scope", "resource", "field_revoked", "node_revoked", "target", "preview"])
async def test_recipe_apply_rejects_stale_preview(tmp_path, monkeypatch, attack):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    from server.meta_agent.headless_authoring import HeadlessAuthoringError
    headless, authoring, result, _, raw = await setup_recipe_recovery(tmp_path, monkeypatch)
    recovery = FailedDraftRecovery(headless)
    request = flow_patch(headless.proposal_state(result.proposal_id), raw["control_flow"][:-1])
    preview = recovery.preview(result.proposal_id, request)
    assert preview["can_apply"]
    stored = authoring.proposal_store._items[result.proposal_id]
    if attack == "candidate":
        stored.payload["name"] = "其他窗口"
    elif attack == "artifact":
        stored.payload["_meta_planner_generation_artifact"]["attempts"][0]["recipe"]["name"] = "篡改"
    elif attack == "scope":
        stored.payload["meta_planner_report"]["authorized_scope"]["data_table_write_grants"] = []
    elif attack == "resource":
        changed = snapshot()
        changed.data_tables[0]["schema_versions"][0]["checksum"] = "b" * 64
        headless.capability_snapshot_builder = lambda: changed
    elif attack in {"field_revoked", "node_revoked"}:
        changed = snapshot()
        if attack == "field_revoked":
            changed.data_tables[0]["fields"] = []
        else:
            changed.nodes = [item for item in changed.nodes if item["kind"] != "data_table_update"]
        headless.capability_snapshot_builder = lambda: changed
    elif attack == "target":
        stored.revision += 1
    else:
        preview["preview_checksum"] = "0" * 64
    before = authoring.proposal_store.require(result.proposal_id)
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(result.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(result.proposal_id) == before


@pytest.mark.asyncio
async def test_recipe_apply_binds_actual_target_xpert_draft_revision(tmp_path, monkeypatch):
    from server.meta_agent.failed_recovery import FailedDraftRecovery, RecoveryApplyRequest
    from server.meta_agent.headless_authoring import HeadlessAuthoringError, _candidate_from_proposal
    headless, authoring, result, _, raw = await setup_recipe_recovery(tmp_path, monkeypatch)
    target = authoring.xpert_store.create_xpert(name="隔离目标草稿")
    stored = authoring.proposal_store._items[result.proposal_id]
    candidate = _candidate_from_proposal(stored)
    stored.kind, stored.target_id, stored.base_revision = "xpert_update", target.id, target.draft_revision
    stored.payload = {"patch": candidate, "xpert_id": target.id,
        "meta_planner_report": stored.payload["meta_planner_report"],
        "_meta_planner_generation_artifact": stored.payload["_meta_planner_generation_artifact"]}
    recovery = FailedDraftRecovery(headless)
    request = flow_patch(headless.proposal_state(result.proposal_id), raw["control_flow"][:-1])
    preview = recovery.preview(result.proposal_id, request)
    assert preview["can_apply"], preview["diagnostics"]
    authoring.xpert_store.update_xpert(target.id, {"draft": target.draft.model_dump(mode="json")})
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(result.proposal_id, RecoveryApplyRequest(**request.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(result.proposal_id).revision == 1
    assert authoring.xpert_store.get_xpert(target.id).draft_revision == target.draft_revision + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["human_repaired", "model_generated"])
async def test_mutated_report_cannot_clear_private_recipe_recovery_gate(tmp_path, monkeypatch, origin):
    from server.xpert_runtime.authoring_store import AuthoringProposalValidationError
    headless, authoring, result, _, _ = await setup_recipe_recovery(tmp_path, monkeypatch)
    stored = authoring.proposal_store._items[result.proposal_id]
    stored.payload["meta_planner_report"].update(human_modified=True, candidate_origin=origin)
    stored.payload["meta_planner_recipe_recovery"] = {"revision": 1, "artifact_checksum": "a" * 64}
    with pytest.raises(AuthoringProposalValidationError) as error:
        authoring._validate_payload(stored)
    assert error.value.code == "recipe_recovery_required"
    with pytest.raises(AuthoringProposalValidationError):
        authoring.update_pending(result.proposal_id, revision=1, title="不能绕过")
    assert headless.proposal_state(result.proposal_id)["mode"] == "recovery"
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_mixed_private_attempts_prefer_latest_intent_not_later_recipe(tmp_path, monkeypatch):
    from server.meta_agent.failed_artifacts import FailedGenerationCapture
    headless, authoring, result, _, raw = await setup_recipe_recovery(tmp_path, monkeypatch)
    capture = FailedGenerationCapture()
    capture.record(intent("update"), GenerationDiagnostics("capability_compile"))
    observer = GenerationDiagnostics("generation_recipe_v1")
    observer.parsed_recipe = GenerationRecipeV1.model_validate(raw)
    capture.record(None, observer)
    stored = authoring.proposal_store._items[result.proposal_id]
    stored.payload["_meta_planner_generation_artifact"] = capture.build(stored.payload["meta_planner_report"])
    state = headless.proposal_state(result.proposal_id)
    assert state["recovery"]["source_format"] == "graph_intent_v3"
    assert state["recovery"]["intent"] == intent("update").model_dump(mode="json")


@pytest.mark.asyncio
async def test_recipe_semantic_preflight_does_not_enable_full_payload_edits_or_dispatch(tmp_path, monkeypatch):
    from server.meta_agent.model_repair import ExplicitModelRepairService, ModelRepairPreflightRequest
    from server.tests.test_meta_planner_model_repair import ROUTE
    from server.xpert_runtime.authoring_store import AuthoringProposalStore, AuthoringProposalValidationError
    headless, authoring, result, calls, _ = await setup_recipe_recovery(tmp_path, monkeypatch)
    before = authoring.proposal_store.require(result.proposal_id)
    payload = AuthoringProposalStore.serialize(before, include_payload=True)["payload"]
    payload["meta_planner_report"]["human_modified"] = True
    payload["meta_planner_report"]["candidate_origin"] = "human_repaired"
    with pytest.raises(AuthoringProposalValidationError, match="占位"):
        authoring.update_pending(result.proposal_id, revision=1, payload=payload)
    with pytest.raises(AuthoringProposalValidationError, match="绑定预览"):
        authoring.apply_headless_authoring_payload(result.proposal_id, revision=1, payload=payload)
    with pytest.raises(AuthoringProposalValidationError, match="完整预览"):
        authoring.proposal_store.update_pending_from_headless_authoring(
            result.proposal_id, revision=1, payload=payload, validation={"valid": True},
        )
    service = ExplicitModelRepairService(headless, route_projection=lambda *_: deepcopy(ROUTE),
                                        completion=lambda *_: pytest.fail("preflight must not dispatch"))
    state = headless.proposal_state(result.proposal_id)
    preflight = service.preflight(result.proposal_id, ModelRepairPreflightRequest(
        proposal_revision=1, artifact_checksum=state["recovery"]["artifact_checksum"],
        expected_graph_checksum=state["graph_checksum"], expected_candidate_checksum=state["candidate_checksum"], model_id="test/repair"))
    assert preflight["repair_protocol"] == "recipe_edits_v1" and preflight["max_calls"] == 1
    assert len(calls) == 3
    assert authoring.proposal_store.require(result.proposal_id) == before


@pytest.mark.parametrize("attack", ["config", "resource", "plan", "handle", "extra_operation"])
def test_control_flow_repair_cannot_inject_other_authority(attack):
    from server.meta_agent.failed_recovery import RecoveryPreviewRequest
    payload = {"mode": "recovery", "artifact_checksum": "a" * 64, "patch": {
        "protocol_version": "recipe_control_flow_v1", "proposal_revision": 1,
        "expected_graph_checksum": "b" * 64, "expected_candidate_checksum": "c" * 64,
        "operations": [{"op": "replace_recipe_control_flow", "control_flow": [step("answer")]}],
    }}
    if attack == "extra_operation":
        payload["patch"]["operations"].append({"op": "set_node_resource", "resource_id": "foreign"})
    elif attack == "handle":
        payload["patch"]["operations"][0]["control_flow"][0]["sourceHandle"] = "error"
    else:
        payload["patch"]["operations"][0][attack] = {}
    with pytest.raises(ValueError):
        RecoveryPreviewRequest.model_validate(payload)


@pytest.mark.parametrize("attack", ["secret", "reasoning", "unknown_config", "size", "nonfinite"])
def test_unsafe_recipe_is_not_retained(attack):
    from server.meta_agent.failed_artifacts import FailedGenerationCapture
    from server.meta_agent.generation_recipe import GenerationRecipeV1
    raw = from_intent(intent("update"))
    if attack == "secret":
        raw["nodes"][-1]["config"]["role_prompt"] = "sk-" + "A" * 30
    elif attack == "reasoning":
        raw["nodes"][1]["config"]["values"] = {"reasoning_content": "PRIVATE_CANARY"}
    elif attack == "unknown_config":
        raw["nodes"][1]["config"]["raw_completion"] = "PRIVATE_CANARY"
    elif attack == "size":
        raw["nodes"][1]["config"]["values"] = {"sku": "A" * (1024 * 1024)}
    else:
        raw["nodes"][1]["config"]["values"] = {"score": float("nan")}
    observer = GenerationDiagnostics("capability_compile")
    observer.parsed_recipe = GenerationRecipeV1.model_validate(raw)
    capture = FailedGenerationCapture()
    capture.record(None, observer)
    artifact = capture.build({})
    assert artifact["attempts"][0]["recipe"] is None
    assert "PRIVATE_CANARY" not in json.dumps(artifact)
