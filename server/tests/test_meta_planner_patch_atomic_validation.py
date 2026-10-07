from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from server.meta_agent.generation_diagnostics import GraphPatchProgress
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.graph_ir_v3 import resolve_graph_intent, workflow_semantic_checksum
from server.meta_agent.graph_patch import (
    GraphPatchApplyRequest,
    GraphPatchEditorDiffRequest,
    GraphPatchEnvelopeV1,
    apply_graph_patch,
    diff_graph_intents,
)
from server.meta_agent.headless_authoring import (
    HeadlessAuthoringError,
    HeadlessAuthoringService,
)
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service,
    _validation_report,
    compile_xpert_candidate,
    validate_blueprint_authorization,
)
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.tests.test_meta_planner_pure_nodes import (
    STRING,
    _aggregate_intent,
    _authorized_request,
    _input,
)
from server.tests.test_meta_planner_v2 import _plan, _snapshot
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xperts.store import XpertStore
from server.xperts.validation import validate_xpert_definition


def _encode(intent):
    return next(node for node in intent.nodes if node.ref == "encode_summary")


def _patch(operations):
    return GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64,
        operations=operations,
    )


def _apply(intent, operations, progress=None):
    return apply_graph_patch(
        intent,
        _patch(operations),
        plan_task_ids={task.task_id for task in _plan().tasks},
        allowed_node_kinds=set(_authorized_request().scope.allowed_node_kinds),
        progress=progress,
    )


def _edge(op, source_ref="aggregate_rows", source_port="result"):
    return {
        "op": op,
        "source_ref": source_ref,
        "source_port": source_port,
        "target_ref": "encode_summary",
        "target_port": "value",
    }


def _update(mode="config"):
    changes = (
        {"config": {"format": "pretty"}}
        if mode == "config"
        else {"title": "汇总序列化"}
    )
    return {"op": "update_node", "ref": "encode_summary", **changes}


def _preflight(candidate):
    return validate_xpert_definition(candidate), candidate.draft.workflow, []


def _prove_compilable(intent):
    request, plan, snapshot = _authorized_request(), _plan(), _snapshot()
    assert validate_blueprint_authorization(request, plan, intent, snapshot) == []
    graph = resolve_graph_intent(intent, snapshot)
    candidate = compile_xpert_candidate(
        request=request, plan=plan, blueprint=intent, snapshot=snapshot, target=None,
    )
    validation = _validation_report(candidate, target=None, preflight=_preflight)
    assert validation["valid"], validation
    return graph.graph_checksum, workflow_semantic_checksum(candidate)


def _services(tmp_path: Path, completion):
    store = AuthoringProposalStore(tmp_path / "runtime")
    xperts = XpertStore(tmp_path / "xperts")
    authoring = AuthoringService(
        store,
        xperts,
        WorkspaceSkillDraftStore(tmp_path / "skills"),
        xpert_preflight=_preflight,
    )
    planner = LegacyGraphReplayService(
        authoring_service=authoring, preflight=_preflight, completion=completion,
    )
    headless = HeadlessAuthoringService(
        authoring_service=authoring,
        planner_service=planner,
        capability_snapshot_builder=_snapshot,
    )
    return store, xperts, planner, headless


@pytest.mark.parametrize("input_count", [0, 2])
@pytest.mark.parametrize("update_mode", ["config", "title"])
def test_update_can_precede_data_repair_in_same_patch(input_count, update_mode):
    base = _aggregate_intent()
    if input_count == 0:
        _encode(base).inputs = []
        repair = _edge("connect_data")
    else:
        _encode(base).inputs.append(_input(
            port="value", variable="user_input", source_ref="input",
            source_port="user_input", schema=STRING,
        ))
        repair = _edge("disconnect_data", "input", "user_input")
    original = base.model_dump(mode="json")
    progress = GraphPatchProgress()

    result = _apply(base, [_update(update_mode), repair], progress)
    reference = _apply(base, [repair, _update(update_mode)])

    assert progress.phase == "completed" and progress.operation_index is None
    assert result.operation_types == ("update_node", repair["op"])
    assert _prove_compilable(result.intent) == _prove_compilable(reference.intent)
    assert base.model_dump(mode="json") == original


@pytest.mark.parametrize("update_mode", ["config", "title"])
def test_editor_diff_can_rewire_and_update_valid_graph_atomically(update_mode):
    base = _aggregate_intent()
    target = base.model_copy(deep=True)
    _encode(target).inputs = [_input(
        port="value", variable="rows_json", source_ref="researcher",
        source_port="result", schema=STRING,
    )]
    if update_mode == "config":
        _encode(target).config = {"format": "pretty"}
    else:
        _encode(target).title = "汇总序列化"
    _prove_compilable(base)
    expected = _prove_compilable(target)
    generated = diff_graph_intents(
        base, target, proposal_revision=1,
        expected_graph_checksum="a" * 64, expected_candidate_checksum="b" * 64,
    )
    assert [op.op for op in generated.operations] == [
        "disconnect_data", "update_node", "connect_data",
    ]

    result = _apply(base, generated.operations)

    assert _prove_compilable(result.intent) == expected


@pytest.mark.parametrize("ref,port", [
    ("decode_rows", "json"),
    ("aggregate_rows", "rows"),
])
def test_non_serializer_adapter_shape_is_also_checked_after_all_edges(ref, port):
    base = _aggregate_intent()
    node = next(node for node in base.nodes if node.ref == ref)
    binding = node.inputs[0]
    edge = {
        "source_ref": binding.source_ref,
        "source_port": binding.source_port,
        "target_ref": ref,
        "target_port": port,
    }
    target = base.model_copy(deep=True)
    next(node for node in target.nodes if node.ref == ref).description = "保留类型契约"

    result = _apply(base, [
        {"op": "disconnect_data", **edge},
        {"op": "update_node", "ref": ref, "description": "保留类型契约"},
        {"op": "connect_data", **edge},
    ])

    assert _prove_compilable(result.intent) == _prove_compilable(target)


def test_new_node_can_be_updated_before_later_edge_and_explicit_removal():
    base = _aggregate_intent()
    add = {
        "op": "add_node", "ref": "temporary_encode", "kind": "json_serialize",
        "title": "临时序列化", "config": {"format": "compact"},
        "output_variables": {"json": "temporary_json"},
    }
    connect = {**_edge("connect_data"), "target_ref": "temporary_encode"}
    result = _apply(base, [
        add,
        {"op": "update_node", "ref": "temporary_encode", "description": "暂未接线"},
        connect,
        {**connect, "op": "disconnect_data"},
        {"op": "remove_node", "ref": "temporary_encode"},
    ])

    assert _prove_compilable(result.intent) == _prove_compilable(base)


@pytest.mark.parametrize("extra_input", [False, True])
def test_invalid_final_cardinality_still_fails_without_mutating_source(extra_input):
    base = _aggregate_intent()
    edge = (
        _edge("connect_data", "input", "user_input")
        if extra_input else _edge("disconnect_data")
    )
    progress = GraphPatchProgress()
    original = base.model_dump(mode="json")
    with pytest.raises(ValueError, match="requires exactly one value input"):
        _apply(base, [edge, _update()], progress)
    assert progress.phase == "validation" and progress.operation_index is None
    assert base.model_dump(mode="json") == original


@pytest.mark.parametrize("bad_config", [
    {"format": "invalid"},
    {"format": "compact", "inputVariable": "forged"},
    {"format": "compact", "contractVersion": 1},
    {"format": "compact", "sourceHandle": "forged"},
    {"format": "compact", "compiler_checksum": "a" * 64},
])
def test_invalid_config_is_rejected_before_later_operations(bad_config):
    base = _aggregate_intent()
    _encode(base).inputs = []
    progress = GraphPatchProgress()
    with pytest.raises(ValueError) as caught:
        _apply(base, [
            {**_update(), "config": bad_config}, _edge("connect_data"),
        ], progress)
    assert "requires exactly one value input" not in str(caught.value)
    assert progress.phase == "operations" and progress.operation_index == 0


@pytest.mark.parametrize("alteration", [
    {"source_ref": "missing"},
    {"source_port": "forged"},
    {"target_port": "forged"},
])
def test_data_repair_still_rejects_unknown_ref_or_port(alteration):
    base = _aggregate_intent()
    _encode(base).inputs = []
    progress = GraphPatchProgress()
    with pytest.raises(ValueError, match="Unknown source node|has no .* port"):
        _apply(base, [
            _update(), {**_edge("connect_data"), **alteration},
        ], progress)
    assert progress.phase == "operations" and progress.operation_index == 1


def test_pure_task_ownership_is_still_rejected_at_update_operation():
    base = _aggregate_intent()
    _encode(base).inputs = []
    progress = GraphPatchProgress()
    with pytest.raises(ValueError, match="cannot cover plan tasks"):
        _apply(base, [
            {**_update(), "task_ids": ["research"]}, _edge("connect_data"),
        ], progress)
    assert progress.phase == "operations" and progress.operation_index == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_first", [False, True])
async def test_single_model_patch_repair_accepts_both_orders_with_same_call_budget(
    tmp_path: Path, repair_first,
):
    invalid = _aggregate_intent()
    _encode(invalid).inputs = []
    operations = (
        [_edge("connect_data"), _update()]
        if repair_first else [_update(), _edge("connect_data")]
    )
    calls = []

    async def completion(*args):
        calls.append(None)
        if len(calls) == 1:
            return _plan().model_dump_json()
        if len(calls) == 2:
            return invalid.model_dump_json()
        assert len(calls) == 3
        return json.dumps({"operations": operations})

    store, xperts, planner, _ = _services(tmp_path, completion)
    response = await planner.generate(_authorized_request(), _snapshot())

    assert len(calls) == 3 and response.repair_used
    assert response.validation["valid"] is True
    proposal = store.require(response.proposal_id)
    diagnostic = proposal.payload["meta_planner_report"]["generation_diagnostics"][-1]
    assert diagnostic["recompile_executed"] is True
    assert diagnostic["failed_phase"] is None
    assert str(proposal.status) == "pending" and proposal.revision == 1
    assert xperts.list_xperts() == []


@pytest.mark.asyncio
async def test_incomplete_repair_fails_without_a_fourth_model_call(tmp_path: Path):
    invalid = _aggregate_intent()
    _encode(invalid).inputs = []
    completions = [
        _plan().model_dump_json(), invalid.model_dump_json(),
        json.dumps({"operations": [_update()]}),
    ]

    async def completion(*args):
        return completions.pop(0)

    store, xperts, planner, _ = _services(tmp_path, completion)
    response = await planner.generate(_authorized_request(), _snapshot())

    assert not completions and response.repair_used
    assert response.validation["valid"] is False
    assert "requires exactly one value input" in json.dumps(response.validation)
    proposal = store.require(response.proposal_id)
    diagnostic = proposal.payload["meta_planner_report"]["generation_diagnostics"][-1]
    assert diagnostic["failed_phase"] == "patch_apply"
    assert diagnostic["recompile_executed"] is False
    assert str(proposal.status) == "pending" and proposal.revision == 1
    assert xperts.list_xperts() == []


@pytest.mark.asyncio
async def test_headless_editor_patch_previews_and_applies_once(tmp_path: Path):
    completions = [_plan().model_dump_json(), _aggregate_intent().model_dump_json()]

    async def completion(*args):
        return completions.pop(0)

    store, xperts, planner, headless = _services(tmp_path, completion)
    response = await planner.generate(_authorized_request(), _snapshot())
    assert response.validation["valid"] and not completions
    original = asdict(store.require(response.proposal_id))
    definition = deepcopy(original["payload"]["draft"]["workflow"])
    encoder = next(
        node for node in definition["nodes"]
        if node["data"].get("plannerRef") == "encode_summary"
    )
    encoder["data"].update(inputVariable="rows_json", format="pretty")
    diff = headless.editor_diff(response.proposal_id, GraphPatchEditorDiffRequest(
        proposal_revision=1, definition=definition,
    ))
    patch = GraphPatchEnvelopeV1.model_validate(diff["patch"])
    assert [op.op for op in patch.operations] == [
        "disconnect_data", "update_node", "connect_data",
    ]

    preview = headless.preview(response.proposal_id, patch)

    assert preview["can_apply"] is True
    assert asdict(store.require(response.proposal_id)) == original
    headless.apply(response.proposal_id, GraphPatchApplyRequest(
        patch=patch, preview_checksum=preview["preview_checksum"],
    ))
    saved = store.require(response.proposal_id)
    assert saved.revision == 2 and str(saved.status) == "pending"
    assert saved.payload["meta_planner_report"]["graph_ir_status"] == "current"
    saved_encoder = next(
        node for node in saved.payload["draft"]["workflow"]["nodes"]
        if node["data"].get("plannerRef") == "encode_summary"
    )
    assert saved_encoder["data"]["inputVariable"] == "rows_json"
    assert saved_encoder["data"]["format"] == "pretty"
    assert xperts.list_xperts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_final", ["missing_input", "type_mismatch"])
async def test_headless_rejects_invalid_final_graph_without_persisting(
    tmp_path: Path, invalid_final,
):
    completions = [_plan().model_dump_json(), _aggregate_intent().model_dump_json()]

    async def completion(*args):
        return completions.pop(0)

    store, xperts, planner, headless = _services(tmp_path, completion)
    response = await planner.generate(_authorized_request(), _snapshot())
    original = asdict(store.require(response.proposal_id))
    state = headless.proposal_state(response.proposal_id)
    operations = [_edge("disconnect_data"), _update()]
    if invalid_final == "type_mismatch":
        operations.extend([
            {
                "op": "disconnect_data", "source_ref": "encode_summary",
                "source_port": "json", "target_ref": "writer", "target_port": "task",
            },
            {
                "op": "connect_data", "source_ref": "aggregate_rows",
                "source_port": "result", "target_ref": "writer", "target_port": "task",
            },
            {
                "op": "update_node", "ref": "writer", "config": {
                    "role_prompt": "根据汇总结果撰写报告。",
                    "task_input": "{{summary_rows}}", "model_id": "model/agent",
                },
            },
            _edge("connect_data"),
        ])
    patch = GraphPatchEnvelopeV1(
        proposal_revision=1,
        expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=operations,
    )
    if invalid_final == "missing_input":
        with pytest.raises(HeadlessAuthoringError, match="requires exactly one value input"):
            headless.preview(response.proposal_id, patch)
    else:
        with pytest.raises(HeadlessAuthoringError, match="input port task rejects its value type"):
            headless.preview(response.proposal_id, patch)
        with pytest.raises(HeadlessAuthoringError, match="input port task rejects its value type"):
            headless.apply(response.proposal_id, GraphPatchApplyRequest(
                patch=patch, preview_checksum="f" * 64,
            ))
    assert asdict(store.require(response.proposal_id)) == original
    assert xperts.list_xperts() == []
