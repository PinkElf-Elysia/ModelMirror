"""Synthetic Schema failures through the real entry, not historical body replay."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from server.meta_agent.failed_artifacts import (
    FailedGenerationCapture, _retained_recipe, parse_recipe_resource_draft,
)
from server.meta_agent.failed_recovery import (
    FailedDraftRecovery, RecoveryApplyRequest, RecoveryPreviewRequest,
)
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.generation_recipe import GenerationRecipeV1
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_recipe_fixtures import from_intent
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture


def invalid_resource_recipe():
    raw = from_intent(intent("update"))
    raw["resources"] = [{"kind": "data_table", "resource_id": "table-orders", "target_ref": "answer"}]
    return raw


async def setup_schema_failure(tmp_path, monkeypatch):
    _forbid_records(monkeypatch)
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch, "update")
    raw = invalid_resource_recipe()
    responses = ["{}", _plan().model_dump_json(), json.dumps(raw, ensure_ascii=False)]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        return responses[len(calls) - 1]

    planner = MetaPlannerV2Service(authoring_service=authoring,
        preflight=headless.planner_service.preflight, completion=completion)
    result = await planner.generate(request(snap, "update"), snap)
    headless.planner_service = planner
    assert len(calls) == 3 and not result.validation["valid"] and result.repair_used
    return headless, authoring, result, calls, raw


def resource_patch(state, resources=None):
    return RecoveryPreviewRequest.model_validate({
        "mode": "recovery", "artifact_checksum": state["recovery"]["artifact_checksum"],
        "patch": {
            "protocol_version": "recipe_resource_bindings_v1",
            "proposal_revision": state["proposal_revision"],
            "expected_graph_checksum": state["graph_checksum"],
            "expected_candidate_checksum": state["candidate_checksum"],
            "operations": [{"op": "replace_recipe_resources", "resources": resources or []}],
        },
    })


def test_retention_draft_does_not_change_strict_recipe_schema():
    raw = invalid_resource_recipe()
    before = deepcopy(raw)
    with pytest.raises(ValidationError):
        GenerationRecipeV1.model_validate(raw)
    draft = parse_recipe_resource_draft(raw, allowed_resource_ids={"table-orders"})
    assert draft is not None
    kept, status = _retained_recipe(draft)
    assert status == "retained" and kept["resources"][0]["kind"] == "data_table"
    with pytest.raises(ValidationError):
        GenerationRecipeV1.model_validate(kept)
    assert raw == before
    raw["resources"] = []
    assert parse_recipe_resource_draft(raw, allowed_resource_ids={"table-orders"}) is None


@pytest.mark.parametrize("attack", ["top_level", "resource_field", "handle", "other_schema", "kind_text", "unknown_node", "config", "credential", "non_finite", "oversize", "opaque_id", "node_opaque_id"])
def test_draft_retention_is_not_arbitrary_json_storage(attack):
    raw = invalid_resource_recipe()
    if attack == "top_level":
        raw["raw_response"] = "private"
    elif attack == "resource_field":
        raw["resources"][0]["schema"] = {}
    elif attack == "handle":
        raw["resources"][0]["targetHandle"] = "toolset"
    elif attack == "other_schema":
        raw["nodes"][0]["inputs"] = "bad"
    elif attack == "kind_text":
        raw["resources"][0]["kind"] = "private body / path"
    elif attack == "unknown_node":
        raw["nodes"][0]["kind"] = "not_registered"
    elif attack == "config":
        raw["nodes"][0]["config"]["tableId"] = "injected"
    elif attack == "credential":
        raw["nodes"][-1]["config"]["role_prompt"] = "OPENROUTER_API_KEY=sk-or-v1-" + "a" * 64
    elif attack == "non_finite":
        raw["nodes"][1]["config"]["values"] = {"score": float("nan")}
    elif attack in {"opaque_id", "node_opaque_id"}:
        target = raw["resources"][0] if attack == "opaque_id" else raw["nodes"][0]["resource_ref"]
        target["resource_id"] = "opaque.unrecognized.synthetic_value"
    else:
        raw["nodes"][1]["config"]["values"] = {"sku": "A" * 300_000}
    observer = GenerationDiagnostics("capability_compile")
    observer.recipe_draft = parse_recipe_resource_draft(raw, allowed_resource_ids={"table-orders"})
    capture = FailedGenerationCapture()
    capture.record(None, observer)
    assert capture.attempts[0]["recipe"] is None
    assert capture.attempts[0]["intent"] is None


@pytest.mark.asyncio
async def test_consumed_plan_repair_retains_schema_draft_without_fourth_call(tmp_path, monkeypatch):
    headless, authoring, result, calls, raw = await setup_schema_failure(tmp_path, monkeypatch)
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    assert report["repair_protocol"] == "task_plan_v1"
    assert report["failure_artifact"]["recoverable"] is True
    state = headless.proposal_state(result.proposal_id)
    assert state["can_author"] and not state["can_approve"]
    assert state["recovery"]["source_format"] == "recipe_resource_draft_v1"
    assert state["recovery"]["intent"] is None
    assert state["recovery"]["recipe"] == parse_recipe_resource_draft(raw, allowed_resource_ids={"table-orders"}).model_dump(mode="json")
    diagnosis = state["recovery"]["current_diagnostics"]
    assert diagnosis["failed_phase"] == "intent_parse"
    assert diagnosis["issues"][0]["location"] == ["resources", 0, "kind"]
    assert authoring.xpert_store.list_xperts() == [] and len(calls) == 3


@pytest.mark.asyncio
async def test_schema_recovery_preview_apply_restart_and_approval_guard(tmp_path, monkeypatch):
    from server.xpert_runtime.authoring_store import AuthoringProposalStore, AuthoringProposalValidationError
    from server.meta_agent.headless_authoring import HeadlessAuthoringConflictError, _canonical_patch_receipts
    headless, authoring, result, calls, _ = await setup_schema_failure(tmp_path, monkeypatch)
    before = authoring.proposal_store.require(result.proposal_id)
    with pytest.raises(AuthoringProposalValidationError):
        authoring.approve(result.proposal_id, revision=1)
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    state = headless.proposal_state(result.proposal_id)
    recovery = FailedDraftRecovery(headless)
    patch = resource_patch(state)
    preview = recovery.preview(result.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    assert authoring.proposal_store.require(result.proposal_id) == before
    assert recovery.preview(result.proposal_id, patch)["preview_checksum"] == preview["preview_checksum"]
    apply = RecoveryApplyRequest(**patch.model_dump(), preview_checksum=preview["preview_checksum"])
    applied = recovery.apply(result.proposal_id, apply)
    assert applied["proposal_revision"] == 2
    current = authoring.proposal_store.require(result.proposal_id)
    assert current.status == "pending"
    assert current.payload["_meta_planner_generation_artifact"] == before.payload["_meta_planner_generation_artifact"]
    report = current.payload["meta_planner_report"]
    receipt = report["authoring_patch_receipts"][-1]
    assert receipt["before_checksum_kind"] == "recipe_resource_draft_v1"
    assert receipt["operation_types"] == ["replace_recipe_resources"]
    assert "resources" not in receipt
    assert _canonical_patch_receipts([receipt]) == [receipt]
    authoring.proposal_store = AuthoringProposalStore(authoring.proposal_store.storage_dir)
    restored = authoring.proposal_store.require(result.proposal_id)
    assert restored.meta_planner_recipe_recovery["revision"] == 2
    assert authoring._validate_payload(restored)["resource_kind"] == "xpert"
    assert headless.proposal_state(result.proposal_id).get("mode") != "recovery"
    with pytest.raises(HeadlessAuthoringConflictError):
        recovery.apply(result.proposal_id, apply)
    assert len(calls) == 3 and authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_unauthorized_valid_kind_cannot_apply(tmp_path, monkeypatch):
    from server.meta_agent.headless_authoring import HeadlessAuthoringError
    headless, authoring, result, calls, _ = await setup_schema_failure(tmp_path, monkeypatch)
    state = headless.proposal_state(result.proposal_id)
    recovery = FailedDraftRecovery(headless)
    patch = resource_patch(state, [{"kind": "toolset_resource", "target_ref": "answer", "resource_id": "foreign"}])
    preview = recovery.preview(result.proposal_id, patch)
    assert not preview["can_apply"] and preview["candidate"] is None
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(result.proposal_id, RecoveryApplyRequest(**patch.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(result.proposal_id).revision == 1 and len(calls) == 3


@pytest.mark.asyncio
async def test_schema_recovery_http_and_model_repair_boundaries(tmp_path, monkeypatch):
    from functools import partial
    import server.main as main
    from fastapi.testclient import TestClient
    from server.meta_agent.model_repair import ExplicitModelRepairService
    from server.meta_agent.headless_authoring import HeadlessAuthoringError
    from server.tests.test_meta_planner_recipe_lowering_recovery import _HTTPX_SEND
    headless, authoring, result, calls, _ = await setup_schema_failure(tmp_path, monkeypatch)
    service = ExplicitModelRepairService(headless, route_projection=lambda *_: pytest.fail("must not read provider route"))
    with pytest.raises(HeadlessAuthoringError) as error:
        service._prepare(result.proposal_id, None)
    assert error.value.code == "repair_recipe_requires_manual_resources"
    monkeypatch.setattr(main, "get_headless_authoring_service", lambda: headless)
    client = TestClient(main.app)
    monkeypatch.setattr(client, "send", partial(_HTTPX_SEND, client))
    url = f"/api/meta-agent/authoring/proposals/{result.proposal_id}"
    state = client.get(url).json()
    payload = resource_patch(state).model_dump(mode="json")
    preview = client.post(url + "/patch/preview", json=payload)
    assert preview.status_code == 200 and preview.json()["can_apply"], preview.text
    assert authoring.proposal_store.require(result.proposal_id).revision == 1
    applied = client.post(url + "/patch/apply", json={**payload, "preview_checksum": preview.json()["preview_checksum"]})
    assert applied.status_code == 200 and applied.json()["proposal_revision"] == 2, applied.text
    assert len(calls) == 3 and authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["nodes", "scope", "resource_version", "handle", "kind", "operation"])
async def test_resource_recovery_request_rejects_authority_injection(tmp_path, monkeypatch, attack):
    headless, _, result, _, _ = await setup_schema_failure(tmp_path, monkeypatch)
    body = resource_patch(headless.proposal_state(result.proposal_id)).model_dump(mode="json")
    operation = body["patch"]["operations"][0]
    if attack in {"nodes", "scope"}:
        operation[attack] = []
    elif attack == "operation":
        operation["op"] = "replace_recipe_control_flow"
    else:
        binding = {"kind": "toolset_resource", "resource_id": "foreign", "target_ref": "answer"}
        if attack == "kind":
            binding["kind"] = "data_table"
        else:
            binding["version" if attack == "resource_version" else "targetHandle"] = "forged"
        operation["resources"] = [binding]
    with pytest.raises(ValidationError):
        RecoveryPreviewRequest.model_validate(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["snapshot", "revision", "schema"])
async def test_resource_recovery_toctou_blocks_stale_apply(tmp_path, monkeypatch, drift):
    from server.meta_agent.headless_authoring import HeadlessAuthoringError
    headless, authoring, result, calls, _ = await setup_schema_failure(tmp_path, monkeypatch)
    recovery = FailedDraftRecovery(headless)
    state = headless.proposal_state(result.proposal_id)
    patch = resource_patch(state)
    preview = recovery.preview(result.proposal_id, patch)
    assert preview["can_apply"]
    if drift == "revision":
        authoring.proposal_store._items[result.proposal_id].revision += 1
    else:
        original = headless.capability_snapshot_builder()
        changed = original.model_copy(deep=True)
        if drift == "snapshot":
            changed.data_tables = []
        else:
            changed.data_tables[0]["schema_checksum"] = "b" * 64
        monkeypatch.setattr(headless, "capability_snapshot_builder", lambda: changed)
    with pytest.raises(HeadlessAuthoringError):
        recovery.apply(result.proposal_id, RecoveryApplyRequest(**patch.model_dump(), preview_checksum=preview["preview_checksum"]))
    assert len(calls) == 3 and authoring.xpert_store.list_xperts() == []
