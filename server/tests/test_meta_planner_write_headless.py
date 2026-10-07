from copy import deepcopy

import pytest
from pydantic import ValidationError

from server.meta_agent.graph_patch import GraphPatchApplyRequest, GraphPatchEditorDiffRequest, GraphPatchEnvelopeV1
from server.meta_agent.graph_patch import MoveNodeOperation
from server.meta_agent.headless_authoring import HeadlessAuthoringError
from server.data_tables.store import SQLiteAgentTableBackend
from server.meta_agent.write_contract import WriteEditorIntent
from server.tests import test_meta_planner_headless_authoring as helpers
from server.tests.test_meta_planner_controlled_writes import snapshot, request, intent, _plan
from server.workflow_native.node_contracts import workflow_node_contract_registry
from server.workflow_native.retry_policy import validate_retry_configuration, WorkflowRetryPolicyError


def fixture(tmp_path, monkeypatch, operation="insert"):
    snap = snapshot()
    req = request(snap, operation)
    monkeypatch.setattr(helpers, "_snapshot", lambda **kwargs: snap)
    monkeypatch.setattr(helpers, "_request", lambda _: req)
    monkeypatch.setattr(helpers, "_plan", _plan)
    monkeypatch.setattr(helpers, "_intent", lambda: intent(operation))
    return (*helpers._headless_fixture(tmp_path), snap)


@pytest.mark.parametrize("injection", [{"version": 9}, {"tableId": "forged"}, {"writeGrant": {}}, {"schema": {}}, {"sourceHandle": "out"}, {"checksum": "a" * 64}])
def test_semantic_editor_request_rejects_native_authority(injection):
    with pytest.raises(ValidationError):
        WriteEditorIntent.model_validate({"resource_id": "table-orders", "config": {}, **injection})


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_v2_write_explicit_retry_disabled_is_not_retry_authorization(operation):
    kind = "data_table_" + operation
    contract = workflow_node_contract_registry.require(kind)
    validate_retry_configuration({"contractVersion": 2, "retryMode": "none"}, node_kind=kind, contract=contract)
    for config in ({"contractVersion": 2, "retryMode": "transient"}, {"contractVersion": 2, "retryMode": "none", "maxAttempts": 2}, {"contractVersion": 1, "retryMode": "none"}):
        with pytest.raises(WorkflowRetryPolicyError):
            validate_retry_configuration(config, node_kind=kind, contract=contract)


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_write_editor_preview_apply_never_executes_records(tmp_path, monkeypatch, operation):
    service, authoring, proposal, _ = fixture(tmp_path, monkeypatch, operation)
    before = authoring.proposal_store.require(proposal.proposal_id)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    node = next(node for node in definition["nodes"] if node["data"].get("plannerRef") == "write")
    config = deepcopy(node["data"]["plannerAdapterConfigV1"])
    if operation == "insert":
        config["values"]["sku"] = "EDITED"
    elif operation == "update":
        config["values"]["score"] = 7
    else:
        config["filter"]["value"] = "EDITED"
    node["data"]["plannerWriteIntentV2"] = {"resource_id": "table-orders", "config": config, "inputs": [{key: binding[key] for key in ("port", "source_ref", "source_port")} for binding in node["data"]["plannerInputsV3"]]}
    patch = GraphPatchEnvelopeV1.model_validate(service.editor_diff(proposal.proposal_id, GraphPatchEditorDiffRequest(proposal_revision=1, definition=definition))["patch"])
    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    assert authoring.proposal_store.require(proposal.proposal_id).revision == before.revision
    result = service.apply(proposal.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    assert result["proposal_revision"] == before.revision + 1
    state = service.proposal_state(proposal.proposal_id)
    changed = next(node for node in state["candidate"]["draft"]["workflow"]["nodes"] if node["data"].get("plannerRef") == "write")
    assert changed["data"]["plannerAdapterConfigV1"] == config
    assert "plannerWriteIntentV2" not in changed["data"]
    assert authoring.xpert_store.list_xperts() == []


def test_editor_cannot_change_native_record_variable(tmp_path, monkeypatch):
    service, _, proposal, _ = fixture(tmp_path, monkeypatch, "update")
    definition = deepcopy(service.proposal_state(proposal.proposal_id)["candidate"]["draft"]["workflow"])
    node = next(node for node in definition["nodes"] if node["data"].get("plannerRef") == "write")
    node["data"]["recordsVariable"] = "user_input"
    with pytest.raises(HeadlessAuthoringError, match="recordsVariable"):
        service.editor_diff(proposal.proposal_id, GraphPatchEditorDiffRequest(proposal_revision=1, definition=definition))


@pytest.mark.parametrize("drift", ["schema_advance", "schema_checksum", "archive", "missing"])
def test_write_resource_drift_after_preview_cannot_apply(tmp_path, monkeypatch, drift):
    service, authoring, proposal, snap = fixture(tmp_path, monkeypatch)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="write", x=540, y=320)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    drifted = deepcopy(snap)
    if drift == "schema_advance":
        drifted.data_tables[0]["active_schema_version"] = 2
    elif drift == "schema_checksum":
        drifted.data_tables[0]["schema_versions"][0]["checksum"] = "b" * 64
    elif drift == "archive":
        drifted.data_tables[0]["status"] = "archived"
    else:
        drifted.data_tables.clear()
    service.capability_snapshot_builder = lambda: drifted
    before = deepcopy(authoring.proposal_store.require(proposal.proposal_id).payload)
    with pytest.raises(HeadlessAuthoringError):
        service.apply(proposal.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    persisted = authoring.proposal_store.require(proposal.proposal_id)
    assert persisted.revision == 1 and persisted.payload == before
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.parametrize("operation", ["insert", "update", "delete"])
def test_write_proposal_approval_only_creates_draft(tmp_path, monkeypatch, operation):
    def forbidden(*args, **kwargs):
        pytest.fail("预览或审批不得查询、修改业务记录")

    for name in ("query_records", "execute_controlled_write", "create_record_for_schema", "create_record", "update_records", "delete_records", "update_record", "delete_record"):
        monkeypatch.setattr(SQLiteAgentTableBackend, name, forbidden)
    service, authoring, proposal, _ = fixture(tmp_path, monkeypatch, operation)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="write", x=540, y=320)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    assert authoring.xpert_store.list_xperts() == []
    result = authoring.approve(proposal.proposal_id, revision=proposal.revision)
    assert result.status == "approved"
    drafts = authoring.xpert_store.list_xperts()
    assert len(drafts) == 1
    assert drafts[0].published_version is None
    created = authoring.xpert_store.get_xpert(drafts[0].id)
    assert any(node.data.get("kind") == f"data_table_{operation}" for node in created.draft.workflow.nodes)
