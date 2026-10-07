from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest

from server.meta_agent import generation_diagnostics as diagnostics
from server.meta_agent.generation_diagnostics import GenerationDiagnostics, GraphPatchProgress
from server.meta_agent.graph_patch import GraphPatchEnvelopeV1, apply_graph_patch
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.node_adapters import PlannerWriteInputContractError
from server.tests.test_meta_planner_controlled_writes import _plan, intent, request, snapshot
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    _forbid_records(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("Patch 对账测试禁止外部请求和记录访问")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def _graph():
    graph = intent("update")
    node = graph.nodes[1]
    node.config["filter"] = {"kind": "group", "logic": "and", "items": [
        {"kind": "predicate", "ref": name, "field": name,
         "operator": "eq", "value_source": "input"}
        for name in ("record_id", "revision")
    ]}
    for name in ("record_id", "revision"):
        node.inputs.append(node.inputs[0].model_copy(update={"port": "predicate_" + name}))
    return graph


def _literal_config():
    return {"value_source": "literal", "values": {"score": 5},
            "filter": {"ref": "score_check", "field": "score", "operator": "lt", "value": 60},
            "max_affected_rows": 1}


def _edge(op, port):
    return {"op": op, "source_ref": "lookup", "source_port": "result",
            "target_ref": "write", "target_port": port}


def _operations(config):
    return [
        {"op": "update_node", "ref": "write", "config": config},
        _edge("disconnect_data", "predicate_record_id"),
        _edge("disconnect_data", "predicate_revision"),
    ]


def _patch(operations):
    return GraphPatchEnvelopeV1(proposal_revision=1, expected_graph_checksum="a" * 64,
                                expected_candidate_checksum="b" * 64, operations=operations)


def _apply(graph, patch, progress):
    return apply_graph_patch(graph, patch, plan_task_ids={"answer"},
                             allowed_node_kinds=set(request(snapshot(), "update").scope.allowed_node_kinds),
                             progress=progress)


def _receipt(graph, patch, progress, *, failed=False):
    report = GenerationDiagnostics("graph_patch_v1")
    report.patch_receipt(patch, patch, progress, graph, failed=failed)
    return report.as_dict()["patch"]["node_changes"]


def test_failed_final_shape_retains_applied_config_and_edge_evidence():
    graph = _graph()
    before = graph.model_dump(mode="json")
    patch = _patch(_operations(deepcopy(graph.nodes[1].config)))
    progress = GraphPatchProgress()
    with pytest.raises(PlannerWriteInputContractError):
        _apply(graph, patch, progress)
    changes = _receipt(graph, patch, progress, failed=True)
    assert [item["operation_index"] for item in changes] == [0, 1, 2]
    assert [item["op"] for item in changes] == ["update_node", "disconnect_data", "disconnect_data"]
    assert all(item["node_ref"] == "write" for item in changes)
    assert changes[0]["before"]["effective_config_checksum"] == changes[-1]["after"]["effective_config_checksum"]
    assert changes[0]["before"]["actual_input_count"] == 3
    assert changes[-1]["after"]["actual_input_count"] == 1
    assert len(changes[-1]["after"]["expected_ports"]) == 3
    assert changes[-1]["after"]["input_shape_valid"] is False
    assert graph.model_dump(mode="json") == before


def test_complete_replacement_changes_required_ports_without_changing_acceptance():
    graph = _graph()
    patch = _patch(_operations(_literal_config()))
    progress = GraphPatchProgress()
    observed = _apply(graph, patch, progress).intent
    plain = _apply(graph, patch, None).intent
    assert observed == plain
    changes = _receipt(graph, patch, progress)
    assert changes[0]["before"]["effective_config_checksum"] != changes[0]["after"]["effective_config_checksum"]
    assert changes[0]["after"]["expected_ports"] == [{"port": "records"}]
    assert changes[0]["after"]["input_shape_valid"] is False
    assert changes[-1]["after"]["input_shape_valid"] is True
    assert changes[-1]["after"]["actual_input_count"] == 1


def test_later_config_restore_is_distinguishable_from_earlier_correct_replacement():
    graph = _graph()
    original_config = deepcopy(graph.nodes[1].config)
    operations = _operations(_literal_config())
    operations.append({"op": "update_node", "ref": "write", "config": original_config})
    patch, progress = _patch(operations), GraphPatchProgress()
    with pytest.raises(PlannerWriteInputContractError):
        _apply(graph, patch, progress)
    changes = _receipt(graph, patch, progress, failed=True)
    assert changes[-1]["operation_index"] == 3
    assert changes[0]["before"]["effective_config_checksum"] == changes[-1]["after"]["effective_config_checksum"]
    assert changes[-1]["before"]["effective_config_checksum"] != changes[-1]["after"]["effective_config_checksum"]


@pytest.mark.parametrize("config", ["omitted", None])
def test_title_only_update_preserves_config_evidence(config):
    graph = _graph()
    operation = {"op": "update_node", "ref": "write", "title": "仅修改标题"}
    if config is None:
        operation["config"] = None
    patch, progress = _patch([operation]), GraphPatchProgress()
    _apply(graph, patch, progress)
    change = _receipt(graph, patch, progress)[0]
    assert change["before"] == change["after"]


def test_connect_evidence_is_recorded_after_the_edge_is_applied():
    graph = _graph()
    patch = _patch([_edge("disconnect_data", "predicate_revision"),
                    _edge("connect_data", "predicate_revision")])
    progress = GraphPatchProgress()
    assert _apply(graph, patch, progress).intent == _apply(graph, patch, None).intent
    changes = _receipt(graph, patch, progress)
    assert changes[0]["after"]["input_shape_valid"] is False
    assert changes[1]["after"]["input_shape_valid"] is True


def test_evidence_hides_values_prompts_field_names_and_private_refs():
    graph = _graph()
    node = graph.nodes[1].model_copy(deep=True)
    node.ref = "secret_node_ref"
    node.title = "PRIVATE_TITLE_CANARY"
    node.config = {"value_source": "literal", "values": {"private_column": "PRIVATE_VALUE_CANARY"},
                   "filter": {"ref": "private_predicate", "field": "private_column",
                              "operator": "eq", "value": "PRIVATE_FILTER_CANARY"}}
    progress = GraphPatchProgress(operation_index=0)
    progress.record_node_change("update_node", node, node)
    raw = json.dumps(progress.node_changes)
    assert progress.node_changes
    for value in (node.ref, node.title, "private_column", "private_predicate", "PRIVATE_VALUE_CANARY", "PRIVATE_FILTER_CANARY"):
        assert value not in raw
    state = progress.node_changes[0]["after"]
    assert state["filter_shape"][0]["operator"] == "eq"
    assert state["filter_shape"][0]["value_source"] == "literal"


def test_evidence_is_bounded_and_reports_omissions(monkeypatch):
    node = _graph().nodes[1]
    progress = GraphPatchProgress()
    monkeypatch.setattr(diagnostics, "MAX_PATCH_CHANGE_BYTES", 4_000)
    for index in range(100):
        progress.operation_index = index
        progress.record_node_change("update_node", node, node)
    assert len(json.dumps(progress.node_changes, ensure_ascii=True, separators=(",", ":"))) <= 4_000
    assert len(progress.node_changes) <= 64
    assert progress.omitted_node_change_count == 100 - len(progress.node_changes)


def test_observer_failure_cannot_change_success_or_rejection(monkeypatch):
    def broken_observer(node):
        raise RuntimeError("PRIVATE_OBSERVER_CANARY")

    monkeypatch.setattr(diagnostics, "_patch_node_state", broken_observer)
    graph, progress = _graph(), GraphPatchProgress()
    patch = _patch(_operations(_literal_config()))
    assert _apply(graph, patch, progress).intent == _apply(graph, patch, None).intent
    assert progress.unavailable_node_change_count == 3
    assert progress.node_changes == []
    invalid = _patch(_operations(deepcopy(graph.nodes[1].config)))
    with pytest.raises(PlannerWriteInputContractError):
        _apply(graph, invalid, GraphPatchProgress())


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_repair", [False, True])
async def test_generation_persists_safe_changes_without_an_extra_completion(tmp_path, monkeypatch, complete_repair):
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch)
    graph = _graph()
    config = _literal_config() if complete_repair else deepcopy(graph.nodes[1].config)
    responses = [_plan().model_dump_json(), graph.model_dump_json(), json.dumps({"operations": _operations(config)})]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        return responses[len(calls) - 1]

    planner = LegacyGraphReplayService(authoring_service=authoring,
                                   preflight=headless.planner_service.preflight, completion=completion)
    result = await planner.generate(request(snap, "update"), snap)
    assert len(calls) == 3 and result.validation["valid"] is complete_repair
    proposal = authoring.proposal_store.require(result.proposal_id)
    change = proposal.payload["meta_planner_report"]["generation_diagnostics"][-1]["patch"]["node_changes"][-1]
    assert change["after"]["input_shape_valid"] is complete_repair
    assert proposal.revision == 1 and proposal.status == "pending"
    assert authoring.xpert_store.list_xperts() == []
