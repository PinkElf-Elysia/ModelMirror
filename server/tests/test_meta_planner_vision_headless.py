from copy import deepcopy

import pytest

from server.meta_agent.graph_patch import GraphPatchApplyRequest, GraphPatchEditorDiffRequest, GraphPatchEnvelopeV1, UpdateNodeOperation
from server.meta_agent.headless_authoring import HeadlessAuthoringError
from server.tests.test_meta_planner_headless_authoring import _headless_fixture
from server.tests.test_meta_planner_vision_graph import binding_fixture, vision_snapshot


def _patch(state):
    return GraphPatchEnvelopeV1(
        proposal_revision=state["proposal_revision"], expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[UpdateNodeOperation(ref="retrieve", config={"max_pages": 5})],
    )


def test_preview_apply_keeps_trusted_slot_and_binding(tmp_path):
    service, authoring, proposal = _headless_fixture(tmp_path, with_vision=True)
    state = service.proposal_state(proposal.proposal_id)
    assert state["vision_attachment"]["model_id"] == "model/vision"
    assert state["vision_attachment"]["max_model_calls"] == 10
    patch = _patch(state)
    preview = service.preview(proposal.proposal_id, patch)
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1
    assert preview["vision_attachment"]["max_model_calls"] == 5
    assert preview["can_apply"]
    service.apply(proposal.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    updated = service.proposal_state(proposal.proposal_id)
    assert updated["proposal_revision"] == 2
    assert updated["ir_state"] == "current"
    assert authoring.xpert_store.list_xperts() == []
    assert next(node["data"] for node in updated["candidate"]["draft"]["workflow"]["nodes"] if node["type"] == "vision_understanding")["visionModelBinding"] == binding_fixture()


def test_binding_drift_between_preview_and_apply_is_blocked(tmp_path):
    service, authoring, proposal = _headless_fixture(tmp_path, with_vision=True)
    state = service.proposal_state(proposal.proposal_id)
    patch = _patch(state)
    preview = service.preview(proposal.proposal_id, patch)
    service.capability_snapshot_builder = lambda: vision_snapshot(binding_fixture(connection_id="other"))
    with pytest.raises(HeadlessAuthoringError):
        service.apply(proposal.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


@pytest.mark.parametrize("key,value", [("visionModelId", "other/model"), ("contractVersion", 1), ("assetIdVariable", "user_input"), ("visionModelBinding", {}), ("retryMode", "transient")])
def test_editor_cannot_inject_trusted_vision_fields(tmp_path, key, value):
    service, _, proposal = _headless_fixture(tmp_path, with_vision=True)
    state = service.proposal_state(proposal.proposal_id)
    workflow = deepcopy(state["candidate"]["draft"]["workflow"])
    next(node["data"] for node in workflow["nodes"] if node["type"] == "vision_understanding")[key] = value
    with pytest.raises(HeadlessAuthoringError):
        service.editor_diff(proposal.proposal_id, GraphPatchEditorDiffRequest(proposal_revision=1, definition=workflow))


def test_editor_vision_configuration_becomes_typed_patch(tmp_path):
    service, authoring, proposal = _headless_fixture(tmp_path, with_vision=True)
    state = service.proposal_state(proposal.proposal_id)
    workflow = deepcopy(state["candidate"]["draft"]["workflow"])
    next(node["data"] for node in workflow["nodes"] if node["type"] == "vision_understanding")["maxPages"] = 4
    converted = service.editor_diff(proposal.proposal_id, GraphPatchEditorDiffRequest(proposal_revision=1, definition=workflow))
    patch = GraphPatchEnvelopeV1.model_validate(converted["patch"])
    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"]
    assert preview["vision_attachment"]["max_model_calls"] == 4
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1
