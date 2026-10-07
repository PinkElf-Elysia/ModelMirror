from copy import deepcopy
import json

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from server.meta_agent.graph_patch import GraphPatchEnvelopeV1, apply_graph_patch
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service, PlannerGraphPatchRepairPayloadV1,
    _normalize_adapter_outputs_for_repair, validate_blueprint_authorization,
)
from server.meta_agent.graph_ir_v3 import workflow_semantic_checksum, decompile_candidate_to_graph_intent
from server.tests.test_meta_planner_control_contract_alignment import _chain, _request
from server.tests.test_meta_planner_controlled_writes import _plan, snapshot, compile_case
from server.tests.test_meta_planner_resource_repair_diagnostics import broken_graph
from server.tests.test_meta_planner_write_generation_failures import _forbid_records


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    _forbid_records(monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail("Patch 管线测试只能使用 MockTransport，禁止真实 HTTP")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def prompt(graph=None, req=None):
    snap = snapshot()
    return json.loads(MetaPlannerV2Service._patch_repair_prompt(
        req or _request(snap), _plan(), snap, graph or broken_graph(), ["资源契约失败"],
    ))


def test_read_only_state_is_not_an_add_node_template_and_commands_derive_from_schema():
    payload = prompt()
    assert "existing_nodes" not in payload["repair_contract"]
    boundary = payload["input_role_contract"]
    assert {"base_graph_intent", "graph_intent_contract", "repair_contract"} <= set(boundary["read_only_fields"])
    assert boundary["response_root_fields"] == ["operations"]
    schema = PlannerGraphPatchRepairPayloadV1.model_json_schema()
    operations = schema["properties"]["operations"]["items"]["discriminator"]["mapping"]
    commands = payload["patch_command_contract"]["operation_fields"]
    assert set(commands) == set(operations)
    for name, ref in operations.items():
        definition = schema["$defs"][ref.rsplit("/", 1)[-1]]
        assert set(commands[name]["required"]) == set(definition.get("required", [])) | {"op"}
        assert set(commands[name]["required"] + commands[name]["optional"]) == set(definition["properties"])
    assert "title" in commands["add_node"]["required"]


def test_resolved_dynamic_requirements_are_read_only_and_do_not_relabel_records():
    payload = prompt()
    details = payload["repair_contract"]["resolved_resource_inputs"]
    assert len(details) == 5
    for item in details:
        if item["node_ref"] == "insert_ticket":
            continue
        assert item["predicate_inputs"]["predicate_selected"]["type"] == "string"
        assert "records" not in item["predicate_inputs"]
        assert ("records" in item["required_input_ports"]) == item["node_ref"].startswith(("update", "delete"))


def test_unauthorized_resources_do_not_receive_dynamic_schema_projection():
    req = _request(snapshot())
    req.scope.data_table_ids = []
    req.scope.data_table_write_grants = []
    assert prompt(req=req)["repair_contract"]["resolved_resource_inputs"] == []


@pytest.mark.parametrize("extra", ["input_sources", "output_ports", "required_output_ports", "control_outcomes", "final_source"])
def test_r13_summary_field_copy_stays_rejected(extra):
    raw = {"operations": [{"op": "add_node", "ref": "helper", "kind": "json_serialize", "title": "转换结果", "config": {"format": "compact"}, extra: []}]}
    with pytest.raises(ValidationError) as caught:
        PlannerGraphPatchRepairPayloadV1.model_validate(raw)
    assert any(error["type"] == "extra_forbidden" and error["loc"][-1] == extra for error in caught.value.errors())
    assert not Draft202012Validator(prompt()["required_schema"]).is_valid(raw)


def explicit_repair(graph):
    ops = []
    for node in graph.nodes:
        if node.kind not in {"data_table_query", "data_table_update", "data_table_delete"}:
            continue
        ops.append({"op": "disconnect_data", "source_ref": "insert_ticket", "source_port": "result", "target_ref": node.ref, "target_port": "predicate_selected"})
        config = deepcopy(node.config)
        # This is an explicit synthetic operator decision, never a production rewrite.
        config["filter"] = {"ref": "selected", "field": "sku", "operator": "eq", "value_source": "literal", "value": "DEMO"}
        ops.append({"op": "update_node", "ref": node.ref, "config": config})
    return {"operations": ops}


def test_explicit_patch_repairs_synthetic_dynamic_mismatch_without_guessing_or_record_access():
    graph = broken_graph()
    original = deepcopy(graph.model_dump(mode="json"))
    raw = explicit_repair(graph)
    Draft202012Validator(prompt(graph)["required_schema"]).validate(raw)
    patch = GraphPatchEnvelopeV1(proposal_revision=1, expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64, operations=PlannerGraphPatchRepairPayloadV1.model_validate(raw).operations)
    snap = snapshot()
    fixed = apply_graph_patch(graph, patch, plan_task_ids={task.task_id for task in _plan().tasks},
        allowed_node_kinds=set(_request(snap).scope.allowed_node_kinds)).intent
    fixed, _ = _normalize_adapter_outputs_for_repair(fixed, snap)
    assert graph.model_dump(mode="json") == original
    assert validate_blueprint_authorization(_request(snap), _plan(), fixed, snap) == []
    candidate = compile_case(fixed, snap, _request(snap))
    restored = compile_case(decompile_candidate_to_graph_intent(candidate), snap, _request(snap))
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(restored)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["legacy", "managed"])
@pytest.mark.parametrize("repair_valid", [False, True])
async def test_first_resource_error_survives_failed_patch_and_valid_patch_keeps_three_call_budget(
    tmp_path, monkeypatch, route, repair_valid,
):
    from server.tests.test_meta_planner_generation_boundary_audit import _transport, MODEL_ID
    from server.tests.test_meta_planner_write_headless import fixture

    headless, authoring, _, snap = fixture(tmp_path, monkeypatch)
    req = _request(snap)
    req.planner_model_id = MODEL_ID
    graph = broken_graph()
    malformed = {"operations": [
        {"op": "add_node", "ref": f"helper_{index}", "kind": "json_serialize", "task_ids": [],
         "input_sources": [], "output_ports": [], "required_output_ports": [],
         "control_outcomes": [], "final_source": False} for index in range(8)
    ] + [{"op": "PRIVATE_UNKNOWN_OPERATION"} for _ in range(12)]}
    patch = explicit_repair(graph) if repair_valid else malformed
    complete, sent, _, managed_run = await _transport(route, tmp_path, monkeypatch, [
        _plan().model_dump_json(), graph.model_dump_json(), json.dumps(patch),
    ])
    service = LegacyGraphReplayService(authoring_service=authoring,
        preflight=headless.planner_service.preflight, completion=complete)
    result = await service.generate(req, snap)
    assert len(sent) == 3
    assert result.validation["valid"] is repair_valid
    assert json.loads(sent[-1]["messages"][-1]["content"])["input_role_contract"]["response_root_fields"] == ["operations"]
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert proposal.applied_resource_id is None and authoring.xpert_store.list_xperts() == []
    report = proposal.payload["meta_planner_report"]
    stages = report["generation_diagnostics"]
    original = next(stage for stage in stages if stage["stage"] == "capability_compile")
    assert len([issue for issue in original["issues"] if issue["code"] == "TABLE_PREDICATE_INPUT_TYPE_MISMATCH"]) == 4
    if not repair_valid:
        assert stages[-1]["failed_phase"] == "patch_parse"
        assert stages[-1]["issue_count"] == 60
        assert stages[-1]["recompile_executed"] is False
    evidence = report["generation_evidence"]
    last = evidence["calls"][-1]["provider"]
    shape = last.get("structure") or evidence["structures"][last["structure_ref"]]
    assert shape["operation_count"] == (8 if repair_valid else 20)
    assert "PRIVATE_UNKNOWN_OPERATION" not in json.dumps(evidence)
    assert "PRIVATE_UNKNOWN_OPERATION" not in json.dumps(stages)
    if managed_run is not None:
        managed_run.finish("passed" if repair_valid else "failed")
