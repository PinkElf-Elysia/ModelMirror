from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import server.main as main_module
from server.meta_agent.capabilities import build_capability_snapshot
from server.meta_agent.graph_ir_v3 import (
    graph_authoring_checksum,
    resolve_graph_intent,
    workflow_authoring_checksum,
    workflow_semantic_checksum,
)
from server.meta_agent.graph_patch import (
    ConnectDataOperation,
    DisconnectControlOperation,
    GraphPatchApplyRequest,
    GraphPatchEditorDiffRequest,
    GraphPatchEnvelopeV1,
    MoveNodeOperation,
    RemoveNodeOperation,
    SetFinalOutputOperation,
    SetOutputVariableOperation,
    SetXpertMetadataOperation,
    UpdateNodeOperation,
    apply_graph_patch,
)
from server.meta_agent.headless_authoring import (
    HeadlessAuthoringConflictError,
    HeadlessAuthoringError,
    HeadlessAuthoringService,
)
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    compile_xpert_candidate,
)
from server.meta_agent.schemas import (
    GraphIntentFinalOutputV3,
    GraphIntentInputBindingV3,
    GraphIntentNodeV3,
    GraphIntentOutputBindingV3,
    GraphIntentV3,
    MetaPlannerGenerateRequest,
    MetaPlannerIRResourceBinding,
    MetaPlannerScope,
    MetaPlannerTask,
    MetaPlannerTaskPlan,
)
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.workflow_native.node_contracts import WorkflowValueSchema
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xpert_runtime.middleware_registry import runtime_middleware_registry
from server.xpert_runtime.workflow_node_registry import workflow_node_registry
from server.xperts.store import XpertStore
from server.xperts.validation import validate_xpert_definition


def _snapshot(
    *,
    extra_model: str | None = None,
    toolset_version: int | None = None,
    all_resources: bool = False,
):
    model_ids = ["model/planner", "model/agent"]
    if extra_model:
        model_ids.append(extra_model)
    return build_capability_snapshot(
        workflow_registry=workflow_node_registry,
        middleware_registry=runtime_middleware_registry,
        external_xperts=(
            [
                SimpleNamespace(
                    id="xpert-safe",
                    slug="xpert-safe",
                    name="Safe expert",
                    description="Published expert",
                    status="published",
                    published_version=2,
                    kind="xpert",
                    aliases=[],
                )
            ]
            if all_resources
            else []
        ),
        knowledge_bases=(
            [
                {
                    "id": "kb-safe",
                    "name": "Safe knowledge",
                    "active_version_id": "kb-version-2",
                }
            ]
            if all_resources
            else []
        ),
        toolsets=(
            [
                SimpleNamespace(
                    id="toolset-safe",
                    slug="toolset-safe",
                    name="Safe tools",
                    description="Read-only tools",
                    status="published",
                    published_version=(
                        toolset_version if toolset_version is not None else 2
                    ),
                    kind="mcp",
                    aliases=[],
                )
            ]
            if toolset_version is not None or all_resources
            else []
        ),
        plugins=(
            [
                SimpleNamespace(
                    id="plugin-safe",
                    slug="plugin-safe",
                    name="Safe plugin",
                    description="Published plugin",
                    status="published",
                    published_version=2,
                    kind="plugin",
                    aliases=[],
                )
            ]
            if all_resources
            else []
        ),
        prompt_profiles=[],
        model_ids=model_ids,
    )


def _scope(snapshot) -> MetaPlannerScope:
    return MetaPlannerScope(
        allowed_node_kinds=[item["kind"] for item in snapshot.nodes],
        external_xpert_ids=[item["id"] for item in snapshot.external_xperts],
        knowledge_base_ids=[item["id"] for item in snapshot.knowledge_bases],
        toolset_ids=[item["id"] for item in snapshot.toolsets],
        plugin_ids=[item["id"] for item in snapshot.plugins],
    )


def _request(snapshot) -> MetaPlannerGenerateRequest:
    return MetaPlannerGenerateRequest(
        goal="Create a focused answer from the supplied user request.",
        planner_model_id="model/planner",
        default_agent_model_id="model/agent",
        max_agents=3,
        scope=_scope(snapshot),
    )


def _plan() -> MetaPlannerTaskPlan:
    return MetaPlannerTaskPlan(
        summary="Produce one bounded answer.",
        tasks=[
            MetaPlannerTask(
                task_id="answer",
                title="Answer",
                objective="Answer the request accurately.",
                output_contract="A concise response.",
            )
        ],
    )


def _intent() -> GraphIntentV3:
    return GraphIntentV3(
        name="Headless candidate",
        nodes=[
            GraphIntentNodeV3(
                ref="answerer",
                kind="workflow_agent",
                title="Answerer",
                task_ids=["answer"],
                inputs=[
                    GraphIntentInputBindingV3(
                        port="task",
                        variable="user_input",
                        source_ref="input",
                        source_port="user_input",
                        value_schema=WorkflowValueSchema(type="string"),
                    )
                ],
                outputs=[
                    GraphIntentOutputBindingV3(
                        port="result",
                        variable="agent_output",
                        value_schema=WorkflowValueSchema(type="string"),
                    )
                ],
                config={
                    "role_prompt": "Answer accurately.",
                    "task_input": "{{user_input}}",
                    "model_id": "model/agent",
                },
            )
        ],
        final_output=GraphIntentFinalOutputV3(
            node_ref="answerer", variable="agent_output"
        ),
    )


def _preflight(candidate):
    result = validate_xpert_definition(candidate)
    return result, candidate.draft.workflow, []


def _headless_fixture(
    tmp_path: Path,
    *,
    legacy_v2: bool = False,
    with_toolset: bool = False,
    update_target: bool = False,
    all_resources: bool = False,
    exclude_plugin_from_scope: bool = False,
    exclude_toolset_from_scope: bool = False,
):
    snapshot = _snapshot(
        toolset_version=2 if with_toolset else None,
        all_resources=all_resources,
    )
    request = _request(snapshot)
    if exclude_plugin_from_scope:
        request.scope.plugin_ids = []
    if exclude_toolset_from_scope:
        request.scope.toolset_ids = []
    plan = _plan()
    intent = _intent()
    if with_toolset:
        intent.resources.append(
            MetaPlannerIRResourceBinding(
                target_ref="answerer",
                kind="toolset_resource",
                resource_id="toolset-safe",
            )
        )
    xpert_store = XpertStore(tmp_path / "xperts")
    target = (
        xpert_store.create_xpert(name="Existing Xpert")
        if update_target
        else None
    )
    graph = resolve_graph_intent(
        intent, snapshot, default_agent_model_id=request.default_agent_model_id
    )
    candidate = compile_xpert_candidate(
        request=request,
        plan=plan,
        blueprint=intent,
        snapshot=snapshot,
        target=target,
    )
    if legacy_v2:
        candidate["draft"]["workflow"]["version"] = "evoagentx-meta-planner-v2"
        for node in candidate["draft"]["workflow"]["nodes"]:
            data = node.get("data") or {}
            if data.get("kind") == "workflow_agent":
                data.pop("plannerIRVersion", None)
                data.pop("plannerInputsV3", None)
                data.pop("plannerOutputsV3", None)
            elif data.get("kind") == "output":
                first_source = data["outputSources"][0]
                node["data"] = {
                    "kind": "output",
                    "title": data.get("title") or "Final answer",
                    "outputVariable": first_source["variable"],
                    "template": "{{" + first_source["variable"] + "}}",
                }
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    authoring = AuthoringService(
        proposal_store,
        xpert_store,
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=_preflight,
    )
    report = {
        "planner_version": "evoagentx-meta-planner-graph-ir-v3",
        "typed_ir_version": 3,
        "ir_version": 3,
        "graph_ir": graph.model_dump(mode="json"),
        "graph_ir_checksum": graph.graph_checksum,
        "authoring_graph_checksum": graph_authoring_checksum(graph),
        "graph_ir_status": "current",
        "compiled_workflow_checksum": workflow_semantic_checksum(candidate),
        "authoring_candidate_checksum": workflow_authoring_checksum(candidate),
        "compatibility": {
            "source_version": 2 if legacy_v2 else 3,
            "upgraded": legacy_v2,
            "lossy": False,
            "warnings": [],
        },
        "goal": request.goal,
        "mode": "update" if update_target else "create",
        "plan": plan.model_dump(mode="json"),
        "capability_snapshot": {
            "version": snapshot.version,
            "hash": snapshot.snapshot_hash,
        },
        "authorized_scope": request.scope.model_dump(mode="json"),
        "generation_config": {
            "planner_model_id": request.planner_model_id,
            "default_agent_model_id": request.default_agent_model_id,
            "max_agents": request.max_agents,
        },
        "validation": {"valid": True, "issues": []},
        "human_modified": False,
    }
    payload = (
        {
            "xpert_id": target.id,
            "patch": candidate,
            "meta_planner_report": report,
        }
        if target is not None
        else {**candidate, "meta_planner_report": report}
    )
    proposal = proposal_store.create(
        kind="xpert_update" if target is not None else "xpert_create",
        title="Headless candidate",
        payload=payload,
        source_type="meta_planner",
        source_id="meta-planner:test",
        target_id=target.id if target is not None else None,
        base_revision=target.draft_revision if target is not None else None,
    )
    service = HeadlessAuthoringService(
        authoring_service=authoring,
        planner_service=MetaPlannerV2Service(
            authoring_service=authoring, preflight=_preflight
        ),
        capability_snapshot_builder=lambda: snapshot,
    )
    return service, authoring, proposal


def test_capability_snapshot_exposes_patch_protocol_and_pure_node_pack():
    snapshot = _snapshot()

    assert snapshot.version == "evoagentx-meta-planner-capabilities-v7"
    assert snapshot.control_flow_contract_version == 1
    assert snapshot.authoring_protocol_version == 1
    assert snapshot.authoring_limits["max_operations"] == 64
    assert snapshot.authoring_limits["max_receipts"] == 20
    assert snapshot.authoring_limits["max_request_bytes"] == 2 * 1024 * 1024
    assert snapshot.authoring_limits["max_json_depth"] == 32
    assert set(snapshot.authoring_adapter_checksums) == {
        item["kind"] for item in snapshot.nodes
    }
    assert {item["kind"] for item in snapshot.nodes} == {
        "data_aggregate",
        "data_merge",
        "dataset_compare",
        "condition",
        "input",
        "json_deserialize",
        "json_serialize",
        "output",
        "workflow_agent",
        "external_xpert",
        "knowledge_base",
        "toolset_resource",
        "variable_aggregator",
        "plugin_resource",
        "multi_route",
        "terminate_error",
    }


@pytest.mark.parametrize(
    "operation",
    [
        {
            "op": "bind_resource",
            "target_ref": "answerer",
            "kind": "toolset_resource",
            "resource_id": "toolset-a",
            "pinned_version": 99,
        },
        {
            "op": "connect_control",
            "source_ref": "answerer",
            "target_ref": "writer",
            "source_handle": "forged",
        },
        {
            "op": "connect_data",
            "source_ref": "input",
            "source_port": "user_input",
            "target_ref": "answerer",
            "target_port": "task",
            "value_schema": {"type": "string"},
        },
    ],
)
def test_patch_schema_rejects_runtime_owned_fields(operation):
    with pytest.raises(ValidationError):
        GraphPatchEnvelopeV1(
            proposal_revision=1,
            expected_graph_checksum="a" * 64,
            expected_candidate_checksum="b" * 64,
            operations=[operation],
        )


def test_patch_rejects_undeclared_nested_adapter_config_fields():
    intent = _intent()
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[
            {
                "op": "update_node",
                "ref": "answerer",
                "config": {
                    "role_prompt": "Answer accurately.",
                    "task_input": "{{user_input}}",
                    "api_key": "must-not-be-accepted",
                },
            }
        ],
    )

    with pytest.raises(ValueError, match="undeclared Adapter config fields: api_key"):
        apply_graph_patch(
            intent,
            patch,
            plan_task_ids={"answer"},
            allowed_node_kinds={"workflow_agent"},
        )


def test_patch_infers_data_schema_and_requires_explicit_detach():
    intent = _intent()
    intent.nodes.append(
        GraphIntentNodeV3(
            ref="writer",
            kind="workflow_agent",
            title="Writer",
            task_ids=["answer"],
            outputs=[],
            config={
                "role_prompt": "Write from evidence.",
                "task_input": "{{agent_output}}",
                "model_id": "model/agent",
            },
        )
    )
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[
            DisconnectControlOperation(
                source_ref="answerer", target_ref="writer"
            ),
            ConnectDataOperation(
                source_ref="answerer",
                source_port="result",
                target_ref="writer",
                target_port="task",
            ),
            SetOutputVariableOperation(
                node_ref="writer", port="result", variable="writer_output"
            ),
            SetFinalOutputOperation(node_ref="writer", port="result"),
        ],
    )
    with pytest.raises(ValueError, match="does not exist"):
        apply_graph_patch(intent, patch, plan_task_ids={"answer"})

    intent.control_edges = []
    applied = apply_graph_patch(
        intent,
        patch.model_copy(update={"operations": patch.operations[1:]}),
        plan_task_ids={"answer"},
    )
    writer = next(node for node in applied.intent.nodes if node.ref == "writer")
    assert writer.inputs[0].value_schema.type == "string"
    assert writer.inputs[0].variable == "agent_output"
    assert writer.outputs[0].value_schema.type == "string"
    assert applied.intent.final_output.sources[0].node_ref == "writer"
    assert writer.outputs[0].variable == "writer_output"

    remove = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[RemoveNodeOperation(ref="answerer")],
    )
    with pytest.raises(ValueError, match="explicitly detach"):
        apply_graph_patch(applied.intent, remove, plan_task_ids={"answer"})


def test_all_graph_patch_operations_form_an_atomic_round_trip():
    base = _intent()
    add_patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[
            {
                "op": "set_xpert_metadata",
                "description": "Edited through typed authoring.",
                "tags": ["review"],
            },
            {
                "op": "add_node",
                "ref": "writer",
                "kind": "workflow_agent",
                "title": "Writer",
                "task_ids": ["answer"],
                "config": {
                    "role_prompt": "Write a final response.",
                    "task_input": "{{draft_output}}",
                    "model_id": "model/agent",
                },
                "output_variables": {"result": "draft_output"},
            },
            {
                "op": "update_node",
                "ref": "writer",
                "description": "Adapter-backed writer.",
                "config": {
                    "role_prompt": "Write and verify a final response.",
                    "task_input": "{{agent_output}}",
                    "model_id": "model/agent",
                },
            },
            {
                "op": "connect_control",
                "source_ref": "answerer",
                "target_ref": "writer",
            },
            {
                "op": "connect_data",
                "source_ref": "answerer",
                "source_port": "result",
                "target_ref": "writer",
                "target_port": "task",
            },
            {
                "op": "set_output_variable",
                "node_ref": "writer",
                "port": "result",
                "variable": "writer_output",
            },
            {
                "op": "bind_resource",
                "target_ref": "answerer",
                "kind": "knowledge_base",
                "resource_id": "kb-safe",
            },
            {
                "op": "bind_middleware",
                "target_ref": "answerer",
                "middleware_id": "guardrail",
                "priority": 80,
                "config": {},
            },
            {"op": "bind_prompt_profile", "profile_id": "prompt-safe"},
            {
                "op": "set_final_output",
                "node_ref": "writer",
                "port": "result",
            },
            {"op": "move_node", "ref": "writer", "x": 720, "y": 240},
        ],
    )
    added = apply_graph_patch(
        base,
        add_patch,
        plan_task_ids={"answer"},
        allowed_node_kinds={"workflow_agent"},
    )

    assert added.intent.description == "Edited through typed authoring."
    assert added.intent.final_output.sources[0].node_ref == "writer"
    writer = next(node for node in added.intent.nodes if node.ref == "writer")
    assert writer.outputs[0].variable == "writer_output"
    assert added.layout["writer"] == {"x": 720.0, "y": 240.0}
    assert len(added.intent.resources) == 1
    assert len(added.intent.middleware) == 1
    assert added.intent.prompt_profile_ids == ["prompt-safe"]

    remove_patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="c" * 64,
        expected_candidate_checksum="d" * 64,
        operations=[
            {
                "op": "disconnect_data",
                "source_ref": "answerer",
                "source_port": "result",
                "target_ref": "writer",
                "target_port": "task",
            },
            {
                "op": "disconnect_control",
                "source_ref": "answerer",
                "target_ref": "writer",
            },
            {
                "op": "unbind_resource",
                "target_ref": "answerer",
                "kind": "knowledge_base",
                "resource_id": "kb-safe",
            },
            {
                "op": "unbind_middleware",
                "target_ref": "answerer",
                "middleware_id": "guardrail",
            },
            {"op": "unbind_prompt_profile", "profile_id": "prompt-safe"},
            {
                "op": "set_final_output",
                "node_ref": "answerer",
                "port": "result",
            },
            {"op": "remove_node", "ref": "writer"},
        ],
    )
    removed = apply_graph_patch(
        added.intent,
        remove_patch,
        plan_task_ids={"answer"},
        layout=added.layout,
        allowed_node_kinds={"workflow_agent"},
    )

    assert [node.ref for node in removed.intent.nodes] == ["answerer"]
    assert removed.intent.resources == []
    assert removed.intent.middleware == []
    assert removed.intent.prompt_profile_ids == []
    assert removed.intent.final_output.sources[0].node_ref == "answerer"
    assert "writer" not in removed.layout


def test_preview_is_side_effect_free_and_apply_increments_once(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[
            UpdateNodeOperation(
                ref="answerer",
                config={
                    "role_prompt": "Answer accurately and cite uncertainty.",
                    "task_input": "{{user_input}}",
                    "model_id": "model/agent",
                    "source_agent_id": None,
                    "method_skill_ids": [],
                },
            ),
            MoveNodeOperation(ref="answerer", x=640, y=240),
        ],
    )

    preview = service.preview(proposal.proposal_id, patch)
    unchanged = authoring.proposal_store.require(proposal.proposal_id)
    assert unchanged.revision == 1
    assert preview["can_apply"] is True
    assert preview["graph_checksum"] != state["graph_checksum"]
    assert preview["candidate_checksum"] != state["candidate_checksum"]

    result = service.apply(
        proposal.proposal_id,
        GraphPatchApplyRequest(
            patch=patch, preview_checksum=preview["preview_checksum"]
        ),
    )
    assert result["proposal_revision"] == 2
    updated = authoring.proposal_store.require(proposal.proposal_id)
    assert updated.validation["valid"] is True
    report = updated.payload["meta_planner_report"]
    assert report["graph_ir_status"] == "current"
    assert report["human_modified"] is True
    assert len(report["authoring_patch_receipts"]) == 1
    receipt_json = json.dumps(report["authoring_patch_receipts"])
    assert "cite uncertainty" not in receipt_json
    assert authoring.xpert_store.list_xperts() == []


def test_proposal_state_rejects_unconsumed_native_nodes(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    payload = deepcopy(proposal.payload)
    payload["draft"]["workflow"]["nodes"].append(
        {
            "id": "smuggled-node",
            "type": "json_serialize",
            "position": {"x": 900, "y": 900},
            "data": {"kind": "json_serialize", "apiKey": "not-a-real-key"},
        }
    )
    authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        payload=payload,
    )

    with pytest.raises(HeadlessAuthoringError) as error:
        service.proposal_state(proposal.proposal_id)

    assert error.value.code == "headless_lossy_conversion"
    assert "not-a-real-key" not in str(error.value)


def test_editor_diff_rejects_non_adapter_agent_field_changes(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    agent = next(
        node
        for node in definition["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    agent["data"]["maxIterations"] = "99"

    with pytest.raises(HeadlessAuthoringError) as error:
        service.editor_diff(
            proposal.proposal_id,
            GraphPatchEditorDiffRequest(
                proposal_revision=proposal.revision,
                definition=definition,
            ),
        )

    assert error.value.code == "headless_editor_diff_unrepresentable"
    assert "maxIterations" in str(error.value)
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


def test_stale_full_payload_with_non_adapter_agent_edit_is_lossy(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    payload = deepcopy(proposal.payload)
    agent = next(
        node
        for node in payload["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    agent["data"]["maxIterations"] = "99"
    authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        payload=payload,
    )

    with pytest.raises(HeadlessAuthoringError) as error:
        service.proposal_state(proposal.proposal_id)

    assert error.value.code == "headless_lossy_conversion"
    assert "maxIterations" not in str(error.value)


def test_v2_full_payload_cannot_bypass_round_trip_with_runtime_field_edit(
    tmp_path: Path,
):
    service, authoring, proposal = _headless_fixture(tmp_path, legacy_v2=True)
    payload = deepcopy(proposal.payload)
    agent = next(
        node
        for node in payload["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    agent["data"]["maxIterations"] = "99"
    authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        payload=payload,
    )

    with pytest.raises(HeadlessAuthoringError) as error:
        service.proposal_state(proposal.proposal_id)

    assert error.value.code == "headless_lossy_conversion"
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 2


def test_mixed_v2_v3_agent_markers_fail_closed(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    payload = deepcopy(proposal.payload)
    agent = next(
        node
        for node in payload["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    mixed = deepcopy(agent)
    mixed["id"] = "mixed-agent"
    mixed["data"]["plannerRef"] = "mixed_agent"
    mixed["data"].pop("plannerIRVersion", None)
    mixed["data"].pop("plannerInputsV3", None)
    mixed["data"].pop("plannerOutputsV3", None)
    payload["draft"]["workflow"]["nodes"].append(mixed)
    authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        payload=payload,
    )

    with pytest.raises(HeadlessAuthoringError) as error:
        service.proposal_state(proposal.proposal_id)

    assert error.value.code == "headless_lossy_conversion"
    assert "mixes Graph IR V2 and V3" in json.dumps(error.value.diagnostics)


@pytest.mark.parametrize(
    ("kind", "resource_id", "id_field", "source_handle", "target_handle"),
    [
        (
            "external_xpert",
            "xpert-safe",
            "xpertId",
            "expert-binding",
            "expert",
        ),
        (
            "knowledge_base",
            "kb-safe",
            "knowledgeBaseId",
            "knowledge-binding",
            "knowledge",
        ),
        (
            "toolset_resource",
            "toolset-safe",
            "toolsetId",
            "toolset-binding",
            "toolset",
        ),
        (
            "plugin_resource",
            "plugin-safe",
            "pluginId",
            "plugin-binding",
            "plugin",
        ),
    ],
)
def test_editor_diff_can_add_authorized_resources_with_server_owned_versions(
    tmp_path: Path,
    kind: str,
    resource_id: str,
    id_field: str,
    source_handle: str,
    target_handle: str,
):
    service, _, proposal = _headless_fixture(tmp_path, all_resources=True)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    agent = next(
        node
        for node in definition["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    resource_node_id = f"editor-{kind}"
    data = {
        "kind": kind,
        "title": kind,
        "description": "Editor-selected resource",
        id_field: resource_id,
    }
    if kind == "external_xpert":
        data["toolName"] = "safe_expert"
    elif kind == "knowledge_base":
        data.update({"topK": "5", "scoreThreshold": "0"})
    if kind != "knowledge_base":
        data.update({"versionPolicy": "pinned", "pinnedVersion": 999})
    definition["nodes"].append(
        {
            "id": resource_node_id,
            "type": kind,
            "position": {"x": 240, "y": 420},
            "data": data,
        }
    )
    definition["edges"].append(
        {
            "id": f"edge-{resource_node_id}",
            "source": resource_node_id,
            "target": agent["id"],
            "sourceHandle": source_handle,
            "targetHandle": target_handle,
        }
    )

    diff = service.editor_diff(
        proposal.proposal_id,
        GraphPatchEditorDiffRequest(
            proposal_revision=proposal.revision,
            definition=definition,
        ),
    )
    patch = GraphPatchEnvelopeV1.model_validate(diff["patch"])
    assert [operation.op for operation in patch.operations] == ["bind_resource"]
    preview = service.preview(proposal.proposal_id, patch)

    assert preview["can_apply"] is True
    compiled = next(
        node
        for node in preview["candidate"]["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get(id_field) == resource_id
    )
    if kind != "knowledge_base":
        assert compiled["data"]["pinnedVersion"] == 2


def test_editor_diff_adds_pure_node_through_adapter_projection(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    agent = next(
        node
        for node in definition["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    agent["data"]["rolePrompt"] = "Answer from {{encoded_request}}."
    agent["data"]["taskInput"] = "{{encoded_request}}"
    serializer_id = "editor-json-serialize"
    definition["nodes"].append(
        {
            "id": serializer_id,
            "type": "json_serialize",
            "position": {"x": 260, "y": 180},
            "data": {
                "kind": "json_serialize",
                "title": "Encode request",
                "description": "Deterministic JSON encoding",
                "contractVersion": 2,
                "inputVariable": "user_input",
                "outputVariable": "encoded_request",
                "format": "compact",
            },
        }
    )
    definition["edges"] = [
        edge
        for edge in definition["edges"]
        if not (edge["source"] == "input" and edge["target"] == agent["id"])
    ]
    definition["edges"].extend(
        [
            {
                "id": "edge-input-serializer",
                "source": "input",
                "target": serializer_id,
            },
            {
                "id": "edge-serializer-agent",
                "source": serializer_id,
                "target": agent["id"],
            },
        ]
    )

    diff = service.editor_diff(
        proposal.proposal_id,
        GraphPatchEditorDiffRequest(
            proposal_revision=proposal.revision,
            definition=definition,
        ),
    )
    patch = GraphPatchEnvelopeV1.model_validate(diff["patch"])
    add = next(operation for operation in patch.operations if operation.op == "add_node")
    assert add.kind == "json_serialize"
    assert add.task_ids == []

    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"] is True
    result = service.apply(
        proposal.proposal_id,
        GraphPatchApplyRequest(
            patch=patch,
            preview_checksum=preview["preview_checksum"],
        ),
    )

    assert result["proposal_revision"] == 2
    persisted = authoring.proposal_store.require(proposal.proposal_id)
    pure_nodes = [
        node
        for node in persisted.payload["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get("kind") == "json_serialize"
    ]
    assert len(pure_nodes) == 1
    assert pure_nodes[0]["data"]["contractVersion"] == 2
    assert pure_nodes[0]["data"]["plannerTaskIds"] == []


def test_editor_diff_can_remove_existing_fixed_resource_binding(tmp_path: Path):
    service, _, proposal = _headless_fixture(tmp_path, with_toolset=True)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    resource = next(
        node
        for node in definition["nodes"]
        if (node.get("data") or {}).get("kind") == "toolset_resource"
    )
    definition["nodes"] = [
        node for node in definition["nodes"] if node["id"] != resource["id"]
    ]
    definition["edges"] = [
        edge
        for edge in definition["edges"]
        if edge["source"] != resource["id"] and edge["target"] != resource["id"]
    ]

    diff = service.editor_diff(
        proposal.proposal_id,
        GraphPatchEditorDiffRequest(
            proposal_revision=proposal.revision,
            definition=definition,
        ),
    )
    patch = GraphPatchEnvelopeV1.model_validate(diff["patch"])

    assert [operation.op for operation in patch.operations] == ["unbind_resource"]
    assert service.preview(proposal.proposal_id, patch)["can_apply"] is True


def test_editor_diff_preserves_pin_when_existing_resource_metadata_changes(
    tmp_path: Path,
):
    service, _, proposal = _headless_fixture(tmp_path, with_toolset=True)
    drifted = _snapshot(toolset_version=3)
    drifted.toolsets[0]["metadata"]["available_versions"] = [
        {"version": 2, "checksum": "toolset-v2"},
        {"version": 3, "checksum": "toolset-v3"},
    ]
    service.capability_snapshot_builder = lambda: drifted
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    resource = next(
        node
        for node in definition["nodes"]
        if (node.get("data") or {}).get("kind") == "toolset_resource"
    )
    resource["data"]["description"] = "Updated editor description"

    diff = service.editor_diff(
        proposal.proposal_id,
        GraphPatchEditorDiffRequest(
            proposal_revision=proposal.revision,
            definition=definition,
        ),
    )
    patch = GraphPatchEnvelopeV1.model_validate(diff["patch"])
    assert [operation.op for operation in patch.operations] == [
        "unbind_resource",
        "bind_resource",
    ]

    preview = service.preview(proposal.proposal_id, patch)
    resolved_resource = next(
        node
        for node in preview["graph_ir"]["nodes"]
        if node["kind"] == "toolset_resource"
    )
    compiled_resource = next(
        node
        for node in preview["candidate"]["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get("kind") == "toolset_resource"
    )
    assert preview["can_apply"] is True
    assert resolved_resource["config"]["pinned_version"] == 2
    assert compiled_resource["data"]["pinnedVersion"] == 2


def test_editor_diff_resolves_same_resource_for_new_second_agent_binding(
    tmp_path: Path,
):
    service, _, proposal = _headless_fixture(tmp_path, with_toolset=True)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    first_agent = next(
        node
        for node in definition["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    second_agent = {
        "id": "editor-agent-second",
        "type": "workflow_agent",
        "position": {"x": 720, "y": 280},
        "data": {
            "kind": "workflow_agent",
            "title": "Second agent",
            "description": "Continue from the first result.",
            "modelId": "model/agent",
            "rolePrompt": "Produce a checked final answer.",
            "taskInput": "{{agent_output}}",
            "methodSkillIds": [],
            "outputVariable": "second_output",
        },
    }
    definition["nodes"].append(second_agent)
    output_edge = next(edge for edge in definition["edges"] if edge["target"] == "output")
    output_edge["source"] = second_agent["id"]
    definition["edges"].append(
        {
            "id": "edge-first-second",
            "source": first_agent["id"],
            "target": second_agent["id"],
        }
    )
    definition["nodes"].append(
        {
            "id": "editor-toolset-second",
            "type": "toolset_resource",
            "position": {"x": 720, "y": 460},
            "data": {
                "kind": "toolset_resource",
                "title": "Safe tools",
                "toolsetId": "toolset-safe",
                "versionPolicy": "pinned",
                "pinnedVersion": 999,
            },
        }
    )
    definition["edges"].append(
        {
            "id": "edge-toolset-second",
            "source": "editor-toolset-second",
            "target": second_agent["id"],
            "sourceHandle": "toolset-binding",
            "targetHandle": "toolset",
        }
    )

    diff = service.editor_diff(
        proposal.proposal_id,
        GraphPatchEditorDiffRequest(
            proposal_revision=proposal.revision,
            definition=definition,
        ),
    )
    patch = GraphPatchEnvelopeV1.model_validate(diff["patch"])
    preview = service.preview(proposal.proposal_id, patch)

    assert preview["can_apply"] is True
    compiled = preview["candidate"]["draft"]["workflow"]
    second_binding = next(
        edge
        for edge in compiled["edges"]
        if edge.get("targetHandle") == "toolset"
        and edge["target"]
        == next(
            node["id"]
            for node in compiled["nodes"]
            if (node.get("data") or {}).get("title") == "Second agent"
        )
    )
    second_resource = next(
        node for node in compiled["nodes"] if node["id"] == second_binding["source"]
    )
    assert second_resource["data"]["pinnedVersion"] == 2


def test_patch_cannot_bind_globally_available_but_unauthorized_resource(
    tmp_path: Path,
):
    service, _, proposal = _headless_fixture(
        tmp_path,
        all_resources=True,
        exclude_plugin_from_scope=True,
    )
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[
            {
                "op": "bind_resource",
                "target_ref": "answerer",
                "kind": "plugin_resource",
                "resource_id": "plugin-safe",
            }
        ],
    )

    with pytest.raises(HeadlessAuthoringError) as error:
        service.preview(proposal.proposal_id, patch)

    assert error.value.code == "headless_patch_invalid"
    assert "outside the Proposal authorization scope" in str(error.value)


def test_existing_intent_outside_original_scope_fails_closed(tmp_path: Path):
    service, _, proposal = _headless_fixture(
        tmp_path,
        with_toolset=True,
        exclude_toolset_from_scope=True,
    )

    with pytest.raises(HeadlessAuthoringError) as error:
        service.proposal_state(proposal.proposal_id)

    assert error.value.code == "headless_contract_or_resource_drift"
    assert "outside the Proposal authorization scope" in str(error.value)


def test_apply_final_validation_failure_is_atomic(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    before_payload = deepcopy(proposal.payload)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=680, y=320)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    authoring.xpert_preflight = lambda candidate: (
        SimpleNamespace(
            valid=False,
            issues=[SimpleNamespace(message="Final gate blocked the candidate.")],
            node_count=0,
        ),
        candidate.draft.workflow,
        [],
    )

    with pytest.raises(HeadlessAuthoringError, match="Final gate blocked"):
        service.apply(
            proposal.proposal_id,
            GraphPatchApplyRequest(
                patch=patch,
                preview_checksum=preview["preview_checksum"],
            ),
        )

    unchanged = authoring.proposal_store.require(proposal.proposal_id)
    assert unchanged.revision == proposal.revision
    assert unchanged.payload == before_payload


def test_semantic_noop_patch_does_not_consume_revision(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    preview = service.preview(
        proposal.proposal_id,
        GraphPatchEnvelopeV1(
            proposal_revision=proposal.revision,
            expected_graph_checksum=state["graph_checksum"],
            expected_candidate_checksum=state["candidate_checksum"],
            operations=[SetXpertMetadataOperation(name="Headless candidate")],
        ),
    )

    assert preview["can_apply"] is False
    assert any(item.get("code") == "no_effect" for item in preview["diagnostics"])
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


def test_apply_fails_closed_after_concurrent_proposal_edit(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=500, y=300)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        title="Concurrent edit",
    )

    with pytest.raises(HeadlessAuthoringConflictError):
        service.apply(
            proposal.proposal_id,
            GraphPatchApplyRequest(
                patch=patch, preview_checksum=preview["preview_checksum"]
            ),
        )


def test_unrelated_snapshot_drift_keeps_authoring_graph_checksum(tmp_path: Path):
    service, _, proposal = _headless_fixture(tmp_path)
    before = service.proposal_state(proposal.proposal_id)
    drifted = _snapshot(extra_model="model/unrelated")
    service.capability_snapshot_builder = lambda: drifted

    after = service.proposal_state(proposal.proposal_id)

    assert after["graph_checksum"] == before["graph_checksum"]
    assert any("Snapshot changed" in warning for warning in after["warnings"])


def test_latest_resource_advance_keeps_an_existing_immutable_pin_valid(
    tmp_path: Path,
):
    service, _, proposal = _headless_fixture(tmp_path, with_toolset=True)
    drifted = _snapshot(toolset_version=3)
    drifted.toolsets[0]["metadata"]["available_versions"] = [
        {"version": 2, "checksum": "toolset-v2"},
        {"version": 3, "checksum": "toolset-v3"},
    ]
    service.capability_snapshot_builder = lambda: drifted

    state = service.proposal_state(proposal.proposal_id)

    resource = next(
        node
        for node in state["graph_ir"]["nodes"]
        if node["kind"] == "toolset_resource"
    )
    assert state["can_author"] is True
    assert resource["config"]["pinned_version"] == 2
    assert resource["config"]["resource_checksum"] == "toolset-v2"


def test_missing_immutable_resource_pin_blocks_headless_authoring(tmp_path: Path):
    service, _, proposal = _headless_fixture(tmp_path, with_toolset=True)
    drifted = _snapshot(toolset_version=3)
    drifted.toolsets[0]["metadata"]["available_versions"] = [
        {"version": 3, "checksum": "toolset-v3"},
    ]
    service.capability_snapshot_builder = lambda: drifted

    with pytest.raises(HeadlessAuthoringError) as error:
        service.proposal_state(proposal.proposal_id)

    assert error.value.code == "headless_contract_or_resource_drift"
    assert "pinned version 2 is unavailable" in str(error.value)


def test_lossless_v2_candidate_upgrades_on_first_typed_apply(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path, legacy_v2=True)
    state = service.proposal_state(proposal.proposal_id)

    assert state["ir_version"] == 2
    assert state["compatibility"] == {
        "source_version": 2,
        "upgraded": True,
        "lossy": False,
        "warnings": [],
    }
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=700, y=260)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    result = service.apply(
        proposal.proposal_id,
        GraphPatchApplyRequest(
            patch=patch,
            preview_checksum=preview["preview_checksum"],
        ),
    )

    assert result["proposal_revision"] == 2
    updated = authoring.proposal_store.require(proposal.proposal_id)
    agent = next(
        node
        for node in updated.payload["draft"]["workflow"]["nodes"]
        if (node.get("data") or {}).get("kind") == "workflow_agent"
    )
    assert agent["data"]["plannerIRVersion"] == 3
    assert updated.payload["meta_planner_report"]["graph_ir_status"] == "current"


def test_headless_preview_and_apply_persists_pure_node_atomically(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[
            {
                "op": "add_node",
                "ref": "encode_request",
                "kind": "json_serialize",
                "title": "Encode request",
                "task_ids": [],
                "config": {"format": "compact"},
                "output_variables": {"json": "encoded_request"},
            },
            {
                "op": "connect_control",
                "source_ref": "encode_request",
                "target_ref": "answerer",
            },
            {
                "op": "disconnect_data",
                "source_ref": "input",
                "source_port": "user_input",
                "target_ref": "answerer",
                "target_port": "task",
            },
            {
                "op": "connect_data",
                "source_ref": "input",
                "source_port": "user_input",
                "target_ref": "encode_request",
                "target_port": "value",
            },
            {
                "op": "connect_data",
                "source_ref": "encode_request",
                "source_port": "json",
                "target_ref": "answerer",
                "target_port": "task",
            },
            {
                "op": "update_node",
                "ref": "answerer",
                "config": {
                    "role_prompt": "Answer from {{encoded_request}}.",
                    "task_input": "{{encoded_request}}",
                    "model_id": "model/agent",
                    "source_agent_id": None,
                    "method_skill_ids": [],
                },
            },
        ],
    )

    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"] is True
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1

    result = service.apply(
        proposal.proposal_id,
        GraphPatchApplyRequest(
            patch=patch,
            preview_checksum=preview["preview_checksum"],
        ),
    )

    assert result["proposal_revision"] == 2
    updated = authoring.proposal_store.require(proposal.proposal_id)
    assert updated.revision == 2
    workflow_nodes = updated.payload["draft"]["workflow"]["nodes"]
    serializer = next(
        node
        for node in workflow_nodes
        if (node.get("data") or {}).get("kind") == "json_serialize"
    )
    assert serializer["data"]["contractVersion"] == 2
    assert serializer["data"]["plannerTaskIds"] == []
    report = updated.payload["meta_planner_report"]
    assert report["graph_ir_status"] == "current"
    assert len(report["authoring_patch_receipts"]) == 1


def test_apply_rejects_tampered_preview_checksum(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=520, y=300)],
    )
    service.preview(proposal.proposal_id, patch)

    with pytest.raises(HeadlessAuthoringConflictError, match="Preview checksum"):
        service.apply(
            proposal.proposal_id,
            GraphPatchApplyRequest(patch=patch, preview_checksum="f" * 64),
        )
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


def test_attack_late_invalid_operation_leaves_source_and_layout_untouched():
    intent = _intent()
    intent._pinned_resource_versions = {
        ("toolset_resource", "toolset-safe", "answerer"): (2, "toolset-v2")
    }
    layout = {"answerer": {"x": 320.0, "y": 180.0}}
    before_intent = intent.model_dump(mode="json")
    before_pins = dict(intent._pinned_resource_versions)
    before_layout = deepcopy(layout)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=[
            SetXpertMetadataOperation(
                description="This valid first operation must be rolled back."
            ),
            MoveNodeOperation(ref="answerer", x=900, y=420),
            DisconnectControlOperation(
                source_ref="answerer", target_ref="missing"
            ),
        ],
    )

    with pytest.raises(ValueError, match="does not exist"):
        apply_graph_patch(
            intent,
            patch,
            plan_task_ids={"answer"},
            layout=layout,
            allowed_node_kinds={"workflow_agent"},
        )

    assert intent.model_dump(mode="json") == before_intent
    assert intent._pinned_resource_versions == before_pins
    assert layout == before_layout


def test_attack_preview_checksum_cannot_cross_proposal_boundary(tmp_path: Path):
    service_a, _, proposal_a = _headless_fixture(tmp_path / "a")
    service_b, authoring_b, proposal_b = _headless_fixture(tmp_path / "b")
    state_a = service_a.proposal_state(proposal_a.proposal_id)
    state_b = service_b.proposal_state(proposal_b.proposal_id)
    assert state_a["graph_checksum"] == state_b["graph_checksum"]

    patch_a = GraphPatchEnvelopeV1(
        proposal_revision=proposal_a.revision,
        expected_graph_checksum=state_a["graph_checksum"],
        expected_candidate_checksum=state_a["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=610, y=310)],
    )
    patch_b = patch_a.model_copy(
        update={
            "proposal_revision": proposal_b.revision,
            "expected_graph_checksum": state_b["graph_checksum"],
            "expected_candidate_checksum": state_b["candidate_checksum"],
        }
    )
    preview_a = service_a.preview(proposal_a.proposal_id, patch_a)

    with pytest.raises(HeadlessAuthoringConflictError, match="Preview checksum"):
        service_b.apply(
            proposal_b.proposal_id,
            GraphPatchApplyRequest(
                patch=patch_b,
                preview_checksum=preview_a["preview_checksum"],
            ),
        )

    unchanged = authoring_b.proposal_store.require(proposal_b.proposal_id)
    assert unchanged.revision == proposal_b.revision
    assert (
        unchanged.payload["meta_planner_report"].get("authoring_patch_receipts")
        is None
    )


def test_attack_successful_apply_request_cannot_be_replayed(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=620, y=320)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    request = GraphPatchApplyRequest(
        patch=patch,
        preview_checksum=preview["preview_checksum"],
    )

    first = service.apply(proposal.proposal_id, request)
    with pytest.raises(HeadlessAuthoringConflictError, match="Proposal revision"):
        service.apply(proposal.proposal_id, request)

    persisted = authoring.proposal_store.require(proposal.proposal_id)
    assert first["proposal_revision"] == 2
    assert persisted.revision == 2
    assert len(
        persisted.payload["meta_planner_report"]["authoring_patch_receipts"]
    ) == 1


def test_attack_patch_receipts_are_bounded_and_do_not_retain_content(
    tmp_path: Path,
):
    service, authoring, proposal = _headless_fixture(tmp_path)
    sentinel = "receipt-secret-content-must-not-survive"

    for index in range(21):
        state = service.proposal_state(proposal.proposal_id)
        operation = (
            SetXpertMetadataOperation(description=sentinel)
            if index == 0
            else MoveNodeOperation(
                ref="answerer",
                x=500 + index,
                y=300 + index,
            )
        )
        patch = GraphPatchEnvelopeV1(
            proposal_revision=state["proposal_revision"],
            expected_graph_checksum=state["graph_checksum"],
            expected_candidate_checksum=state["candidate_checksum"],
            operations=[operation],
        )
        preview = service.preview(proposal.proposal_id, patch)
        assert preview["can_apply"] is True
        service.apply(
            proposal.proposal_id,
            GraphPatchApplyRequest(
                patch=patch,
                preview_checksum=preview["preview_checksum"],
            ),
        )

    persisted = authoring.proposal_store.require(proposal.proposal_id)
    receipts = persisted.payload["meta_planner_report"][
        "authoring_patch_receipts"
    ]
    receipt_json = json.dumps(receipts)

    assert persisted.revision == 22
    assert len(receipts) == 20
    assert all(item["operation_types"] == ["move_node"] for item in receipts)
    assert sentinel not in receipt_json


def test_attack_legacy_payload_cannot_expand_original_authorization_scope(
    tmp_path: Path,
):
    service, authoring, proposal = _headless_fixture(
        tmp_path,
        all_resources=True,
        exclude_plugin_from_scope=True,
    )
    malicious_payload = deepcopy(proposal.payload)
    malicious_payload["meta_planner_report"]["authorized_scope"][
        "plugin_ids"
    ] = ["plugin-safe"]
    tampered = authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        payload=malicious_payload,
    )

    state = service.proposal_state(proposal.proposal_id)
    assert state["authorized_scope"]["plugin_ids"] == []
    patch = GraphPatchEnvelopeV1(
        proposal_revision=tampered.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[
            {
                "op": "bind_resource",
                "target_ref": "answerer",
                "kind": "plugin_resource",
                "resource_id": "plugin-safe",
            }
        ],
    )

    with pytest.raises(HeadlessAuthoringError, match="outside the Proposal"):
        service.preview(proposal.proposal_id, patch)


def test_attack_headless_apply_storage_failure_rolls_back_memory_and_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=630, y=330)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    store = authoring.proposal_store
    before = store.require(proposal.proposal_id)
    before_disk = store.snapshot_path.read_bytes()

    def fail_save() -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr(store, "_save_unlocked", fail_save)
    with pytest.raises(
        HeadlessAuthoringError, match="final validation failed"
    ):
        service.apply(
            proposal.proposal_id,
            GraphPatchApplyRequest(
                patch=patch,
                preview_checksum=preview["preview_checksum"],
            ),
        )

    in_memory = store.require(proposal.proposal_id)
    reloaded = AuthoringProposalStore(store.storage_dir).require(
        proposal.proposal_id
    )
    assert in_memory.revision == before.revision
    assert in_memory.payload == before.payload
    assert reloaded.revision == before.revision
    assert reloaded.payload == before.payload
    assert store.snapshot_path.read_bytes() == before_disk


def test_attack_tainted_legacy_receipts_are_recanonicalized_on_typed_apply(
    tmp_path: Path,
):
    service, authoring, proposal = _headless_fixture(tmp_path)
    sentinel = "sk-adversarial-legacy-receipt-123456"
    malicious_payload = deepcopy(proposal.payload)
    malicious_payload["meta_planner_report"]["authoring_patch_receipts"] = [
        {
            "operation_types": ["move_node"],
            "before_graph_checksum": "a" * 64,
            "after_graph_checksum": "b" * 64,
            "before_candidate_checksum": "c" * 64,
            "after_candidate_checksum": "d" * 64,
            "diagnostic_counts": {"info": 1},
            "applied_at": 1.0,
            "prompt": sentinel,
        }
    ]
    updated = authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        payload=malicious_payload,
    )
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=updated.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=640, y=340)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    service.apply(
        proposal.proposal_id,
        GraphPatchApplyRequest(
            patch=patch,
            preview_checksum=preview["preview_checksum"],
        ),
    )

    persisted = authoring.proposal_store.require(proposal.proposal_id)
    receipts = persisted.payload["meta_planner_report"][
        "authoring_patch_receipts"
    ]
    safe_keys = {
        "protocol_version",
        "operation_types",
        "before_graph_checksum",
        "after_graph_checksum",
        "before_candidate_checksum",
        "after_candidate_checksum",
        "diagnostic_counts",
        "applied_at",
    }
    assert all(set(receipt) <= safe_keys for receipt in receipts)
    assert sentinel not in json.dumps(receipts)


def test_apply_rejects_target_xpert_drift_after_preview(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path, update_target=True)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=540, y=320)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    target = authoring.xpert_store.get_xpert(proposal.target_id or "")
    authoring.xpert_store.update_xpert(
        target.id,
        {"draft": target.draft.model_dump(mode="json")},
    )

    with pytest.raises(HeadlessAuthoringConflictError, match="Target Xpert"):
        service.apply(
            proposal.proposal_id,
            GraphPatchApplyRequest(
                patch=patch,
                preview_checksum=preview["preview_checksum"],
            ),
        )
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


def test_target_revision_guard_covers_final_validation_and_proposal_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service, authoring, proposal = _headless_fixture(tmp_path, update_target=True)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=545, y=325)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    original_validate = authoring._validate_payload
    update_started = threading.Event()
    update_finished = threading.Event()
    update_thread: threading.Thread | None = None

    def guarded_validate(candidate):
        nonlocal update_thread
        target = authoring.xpert_store.get_xpert(proposal.target_id or "")

        def update_target() -> None:
            update_started.set()
            authoring.xpert_store.update_xpert(
                target.id,
                {"draft": target.draft.model_dump(mode="json")},
            )
            update_finished.set()

        update_thread = threading.Thread(target=update_target)
        update_thread.start()
        assert update_started.wait(timeout=1)
        assert not update_finished.wait(timeout=0.05)
        return original_validate(candidate)

    monkeypatch.setattr(authoring, "_validate_payload", guarded_validate)

    result = service.apply(
        proposal.proposal_id,
        GraphPatchApplyRequest(
            patch=patch,
            preview_checksum=preview["preview_checksum"],
        ),
    )
    assert update_thread is not None
    update_thread.join(timeout=2)

    assert result["proposal_revision"] == 2
    assert update_finished.is_set()
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 2


def test_apply_rejects_resource_version_drift_after_preview(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path, with_toolset=True)
    state = service.proposal_state(proposal.proposal_id)
    patch = GraphPatchEnvelopeV1(
        proposal_revision=proposal.revision,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="answerer", x=560, y=340)],
    )
    preview = service.preview(proposal.proposal_id, patch)
    service.capability_snapshot_builder = lambda: _snapshot(toolset_version=3)

    with pytest.raises(HeadlessAuthoringError) as error:
        service.apply(
            proposal.proposal_id,
            GraphPatchApplyRequest(
                patch=patch,
                preview_checksum=preview["preview_checksum"],
            ),
        )
    assert error.value.code == "headless_contract_or_resource_drift"
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


def test_editor_diff_rejects_forged_binding_handle(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path, with_toolset=True)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    binding = next(
        edge for edge in definition["edges"] if edge.get("targetHandle") == "toolset"
    )
    binding["targetHandle"] = "forged"

    with pytest.raises(HeadlessAuthoringError) as error:
        service.editor_diff(
            proposal.proposal_id,
            GraphPatchEditorDiffRequest(
                proposal_revision=proposal.revision,
                definition=definition,
            ),
        )
    assert error.value.code == "headless_editor_diff_unrepresentable"
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


def test_editor_diff_rejects_unexpressible_compiler_managed_edit(tmp_path: Path):
    service, authoring, proposal = _headless_fixture(tmp_path)
    state = service.proposal_state(proposal.proposal_id)
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    input_node = next(node for node in definition["nodes"] if node["id"] == "input")
    input_node["data"]["variableName"] = "forged_input"

    with pytest.raises(HeadlessAuthoringError) as error:
        service.editor_diff(
            proposal.proposal_id,
            GraphPatchEditorDiffRequest(
                proposal_revision=proposal.revision,
                definition=definition,
            ),
        )
    assert error.value.code == "headless_editor_diff_unrepresentable"
    assert "unexpressible" in str(error.value)
    assert authoring.proposal_store.require(proposal.proposal_id).revision == 1


def test_headless_authoring_routes_preserve_conflict_error_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service, authoring, proposal = _headless_fixture(tmp_path)
    monkeypatch.setattr(
        main_module,
        "get_headless_authoring_service",
        lambda: service,
    )
    client = TestClient(main_module.app)
    state_response = client.get(
        f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}"
    )
    assert state_response.status_code == 200
    state = state_response.json()
    patch = {
        "protocol_version": 1,
        "proposal_revision": proposal.revision,
        "expected_graph_checksum": state["graph_checksum"],
        "expected_candidate_checksum": state["candidate_checksum"],
        "operations": [
            {"op": "move_node", "ref": "answerer", "x": 620, "y": 360}
        ],
    }
    preview_response = client.post(
        f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}/patch/preview",
        json=patch,
    )
    assert preview_response.status_code == 200
    authoring.update_pending(
        proposal.proposal_id,
        revision=proposal.revision,
        title="Concurrent editor",
    )
    apply_response = client.post(
        f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}/patch/apply",
        json={
            "patch": patch,
            "preview_checksum": preview_response.json()["preview_checksum"],
        },
    )

    assert apply_response.status_code == 409
    assert apply_response.json()["detail"]["code"] == "headless_authoring_conflict"


def test_headless_authoring_route_validation_does_not_echo_secret_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service, _, proposal = _headless_fixture(tmp_path)
    monkeypatch.setattr(
        main_module,
        "get_headless_authoring_service",
        lambda: service,
    )
    client = TestClient(main_module.app)
    secret = "sk-super-secret-value"

    response = client.post(
        f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}/patch/preview",
        json={
            "protocol_version": 1,
            "proposal_revision": proposal.revision,
            "expected_graph_checksum": "a" * 64,
            "expected_candidate_checksum": "b" * 64,
            "operations": [
                {
                    "op": "move_node",
                    "ref": "answerer",
                    "x": 100,
                    "y": 200,
                    "api_key": secret,
                }
            ],
        },
    )

    body = response.text
    assert response.status_code == 422
    assert secret not in body
    assert "input_value" not in body

    non_object = client.post(
        f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}/patch/preview",
        json=[secret],
    )
    assert non_object.status_code == 422
    assert secret not in non_object.text
    assert "input_value" not in non_object.text

    malformed = client.post(
        f"/api/meta-agent/authoring/proposals/{proposal.proposal_id}/patch/preview",
        content=f'{{"api_key":"{secret}"',
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 422
    assert secret not in malformed.text


def test_attack_headless_route_rejects_oversized_json_before_service(
    monkeypatch: pytest.MonkeyPatch,
):
    service_called = False

    def forbidden_service():
        nonlocal service_called
        service_called = True
        raise AssertionError("oversized input reached the authoring service")

    monkeypatch.setattr(
        main_module,
        "get_headless_authoring_service",
        forbidden_service,
    )
    client = TestClient(main_module.app, raise_server_exceptions=False)
    body = json.dumps(
        {"padding": "x" * (2 * 1024 * 1024)},
        separators=(",", ":"),
    )

    response = client.post(
        "/api/meta-agent/authoring/proposals/proposal-attack/patch/preview",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "headless_request_too_large"
    assert service_called is False


def test_attack_headless_route_rejects_excessive_json_depth_before_service(
    monkeypatch: pytest.MonkeyPatch,
):
    service_called = False

    def forbidden_service():
        nonlocal service_called
        service_called = True
        raise AssertionError("deep input reached the authoring service")

    monkeypatch.setattr(
        main_module,
        "get_headless_authoring_service",
        forbidden_service,
    )
    nested: dict[str, object] = {"leaf": "safe"}
    for _ in range(40):
        nested = {"nested": nested}
    payload = {
        "protocol_version": 1,
        "proposal_revision": 1,
        "expected_graph_checksum": "a" * 64,
        "expected_candidate_checksum": "b" * 64,
        "operations": [
            {
                "op": "bind_middleware",
                "target_ref": "answerer",
                "middleware_id": "structured_output",
                "config": {"schema_json": nested},
            }
        ],
    }
    client = TestClient(main_module.app, raise_server_exceptions=False)

    response = client.post(
        "/api/meta-agent/authoring/proposals/proposal-attack/patch/preview",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "headless_request_too_deep"
    assert service_called is False
