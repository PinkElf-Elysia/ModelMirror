"""Offline repair worklists, not evidence of real-model success rates."""
from copy import deepcopy
import json
import socket
import sys

import httpx
from jsonschema import Draft202012Validator
import pytest

from server.data_tables.store import SQLiteAgentTableBackend
from server.meta_agent import meta_planner_v2
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent, workflow_semantic_checksum
from server.meta_agent.graph_patch import GraphPatchEnvelopeV1, apply_graph_patch
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.node_adapters import PlannerWriteInputContractError
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, intent, request, snapshot
from server.workflow_native.node_contracts import WorkflowValueSchema
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xperts.store import XpertStore
from server.xperts.validation import validate_xpert_definition


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("修复上下文测试禁止网络和业务记录访问")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        # Windows asyncio uses a private loopback self-pipe, not a Provider call.
        caller = sys._getframe(1)
        if (caller.f_code is getattr(socket, "_fallback_socketpair", lambda: None).__code__
                and address == caller.f_locals["lsock"].getsockname()[:2]):
            return original_connect(sock, address)
        return forbidden()

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    for name in ("query_records", "execute_controlled_write", "create_record_for_schema",
                 "create_record", "update_records", "delete_records", "update_record", "delete_record"):
        monkeypatch.setattr(SQLiteAgentTableBackend, name, forbidden)


def _case(operation="update"):
    snap, graph = snapshot(), intent(operation)
    req = request(snap, operation)
    req.scope.allowed_node_kinds = ["input", "output", "workflow_agent", "json_serialize",
                                    "data_table_query", f"data_table_{operation}"]
    writer = graph.nodes[1]
    extra = writer.inputs[0].model_copy(deep=True)
    extra.port, extra.variable = "values", "invented_values"
    writer.inputs.append(extra)
    graph.control_edges[-1].source_ref = "write"
    return req, snap, graph


def _diagnose(req, snap, graph):
    stage = GenerationDiagnostics("capability_compile")
    stage.enter("intent_parse")
    stage.bind_graph(graph)
    stage.enter("authorization")
    issues = validate_blueprint_authorization(req, _plan(), graph, snap, diagnostics=stage)
    return issues, stage.as_dict()


def _prompt(req, snap, graph):
    issues, diagnostics = _diagnose(req, snap, graph)
    payload = json.loads(MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, graph, issues))
    return payload, diagnostics


def _operations(graph):
    config = deepcopy(graph.nodes[1].config)
    config["filter"] = {"ref": "sku", "field": "sku", "operator": "eq", "value_source": "input"}
    return [
        {"op": "disconnect_data", "source_ref": "lookup", "source_port": "result",
         "target_ref": "write", "target_port": "values"},
        {"op": "update_node", "ref": "write", "config": config},
        {"op": "connect_data", "source_ref": "input", "source_port": "user_input",
         "target_ref": "write", "target_port": "predicate_sku"},
        {"op": "disconnect_control", "source_ref": "write", "target_ref": "answer"},
        {"op": "connect_control", "source_ref": "encode", "target_ref": "answer"},
    ]


def _apply(req, graph, operations):
    patch = GraphPatchEnvelopeV1(proposal_revision=1, expected_graph_checksum="a" * 64,
                                expected_candidate_checksum="b" * 64, operations=operations)
    return apply_graph_patch(graph, patch, plan_task_ids={task.task_id for task in _plan().tasks},
                             allowed_node_kinds=set(req.scope.allowed_node_kinds)).intent


def test_model_frontier_omits_audit_bulk_but_keeps_every_issue_and_blocked_phase():
    req, snap, graph = _case()
    payload, full = _prompt(req, snap, graph)
    frontier = payload["validation_frontier"]
    assert "data_bindings" not in frontier
    assert "control_graph_checksum" not in frontier
    assert "router_inputs" not in frontier
    for key in ("issue_count", "omitted_issue_count", "failed_phase", "phase_results", "category_counts"):
        assert frontier[key] == full[key]
    without_fingerprint = lambda item: {key: value for key, value in item.items() if key != "fingerprint"}
    assert frontier["issues"] == [without_fingerprint(item) for item in full["issues"]]
    assert {"DATA_UNKNOWN_VARIABLE", "DATA_UNREACHABLE"} <= {item["code"] for item in frontier["issues"]}
    assert any(item["category"] == "control_flow" for item in frontier["issues"])
    assert "data_bindings" in full and all("fingerprint" in item for item in full["issues"])


def test_worklist_links_current_ports_inputs_and_control_without_inventing_a_repair():
    req, snap, graph = _case()
    frozen = graph.model_dump(mode="json")
    payload, _ = _prompt(req, snap, graph)
    review = payload["repair_contract"]["dependency_review"]
    assert next(iter(payload["repair_contract"])) == "dependency_review"
    work = {item["node_ref"]: item for item in review["nodes"]}
    assert work["write"]["required_inputs_now"] == ["records"]
    assert work["write"]["input_contract_basis"] == "current_config"
    assert work["write"]["bound_inputs_now"][1] == {
        "input_index": 1, "port": "values", "source_ref": "lookup",
        "source_port": "result", "variable": "invented_values",
    }
    assert work["answer"]["bound_inputs_now"][0]["source_ref"] == "encode"
    assert work["answer"]["control_predecessors_now"] == [{"source_ref": "write", "outcome_ref": "success"}]
    assert "encode" in work  # A producer to inspect, not an automatically selected replacement.
    rules = " ".join(review["rules"])
    assert "最终 config" in rules and "独立错误" in rules and "业务条件" in rules
    assert payload["base_graph_intent"] == frozen == graph.model_dump(mode="json")
    covered = {index for item in work.values() for index in item["issue_indices"]}
    assert covered | set(review["global_issue_indices"]) == set(range(len(payload["validation_frontier"]["issues"])))
    assert payload["input_role_contract"]["response_root_fields"] == ["operations"]
    assert "operations" not in review


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_partial_repair_still_fails_and_complete_explicit_batch_roundtrips(operation):
    req, snap, graph = _case(operation)
    payload, _ = _prompt(req, snap, graph)
    frozen, operations = graph.model_dump(mode="json"), _operations(graph)
    Draft202012Validator(payload["required_schema"]).validate({"operations": operations[:2]})
    with pytest.raises(PlannerWriteInputContractError) as caught:
        _apply(req, graph, operations[:2])
    assert caught.value.input_diagnostic["missing_ports"] == ["predicate_sku"]
    local_fix = _apply(req, graph, operations[:1])
    _, remaining = _diagnose(req, snap, local_fix)
    assert any(item["code"] == "DATA_UNREACHABLE" for item in remaining["issues"])
    for ordered in (operations, [operations[0], operations[2], operations[1], *operations[3:]]):
        fixed = _apply(req, graph, ordered)
        assert validate_blueprint_authorization(req, _plan(), fixed, snap) == []
        compiled = compile_case(fixed, snap, req)
        rebuilt = compile_case(decompile_candidate_to_graph_intent(compiled), snap, req)
        assert workflow_semantic_checksum(compiled) == workflow_semantic_checksum(rebuilt)
    assert graph.model_dump(mode="json") == frozen


def test_prompt_projection_is_smaller_deterministic_and_not_a_second_schema(monkeypatch):
    req, snap, graph = _case()
    payload, _ = _prompt(req, snap, graph)
    with monkeypatch.context() as patcher:
        patcher.setattr(meta_planner_v2, "project_patch_repair_context", lambda value: value)
        original, _ = _prompt(req, snap, graph)
    assert payload == _prompt(req, snap, graph)[0]
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert size(payload) < size(original)
    for key in ("base_graph_intent", "authorized_scope", "required_schema", "validation_issues",
                "patch_command_contract", "task_plan", "capability_snapshot", "graph_intent_contract"):
        assert payload[key] == original[key]
    before = original["graph_intent_contract"]["node_roles"]["executable_node_contracts"]
    after = payload["graph_intent_contract"]["node_roles"]["executable_node_contracts"]
    for kind in before:
        assert len(before[kind]["ports"]) == len(after[kind]["ports"])
        for left, right in zip(before[kind]["ports"], after[kind]["ports"]):
            assert WorkflowValueSchema.model_validate(left["value_schema"]) == WorkflowValueSchema.model_validate(right["value_schema"])
            assert {k: v for k, v in left.items() if k != "value_schema"} == {k: v for k, v in right.items() if k != "value_schema"}


def test_projection_preserves_nested_type_semantics_and_user_config(monkeypatch):
    from server.meta_agent.repair_context import project_patch_repair_context

    req, snap, graph = _case()
    with monkeypatch.context() as patcher:
        patcher.setattr(meta_planner_v2, "project_patch_repair_context", lambda value: value)
        original, _ = _prompt(req, snap, graph)
    nested = WorkflowValueSchema(type="object", required=["title"], properties={
        "title": WorkflowValueSchema(type="string"),
        "items": WorkflowValueSchema(type="array", items=WorkflowValueSchema(any_of=[
            WorkflowValueSchema(type="number"), WorkflowValueSchema(type="object", nullable=True,
                properties={"nullable": WorkflowValueSchema(type="boolean")}),
        ])),
    }).model_dump(mode="json")
    port = original["graph_intent_contract"]["node_roles"]["executable_node_contracts"]["json_serialize"]["ports"][0]
    port["value_schema"] = deepcopy(nested)
    original["base_graph_intent"]["nodes"][1]["config"]["values"] = {"title": "业务值", "nullable": False, "properties": {}}
    frozen = deepcopy(original)
    projected = project_patch_repair_context(original)
    compact = projected["graph_intent_contract"]["node_roles"]["executable_node_contracts"]["json_serialize"]["ports"][0]["value_schema"]
    assert WorkflowValueSchema.model_validate(compact) == WorkflowValueSchema.model_validate(nested)
    assert original == frozen
    assert projected["graph_intent_contract"] == original["graph_intent_contract"]
    assert projected["base_graph_intent"] == original["base_graph_intent"]
    assert projected["required_schema"] == original["required_schema"]
    assert projected["authorized_scope"] == original["authorized_scope"]


def test_unknown_or_unlocalized_issues_are_not_silently_assigned_or_dropped(monkeypatch):
    from server.meta_agent.repair_context import project_patch_repair_context

    req, snap, graph = _case()
    with monkeypatch.context() as patcher:
        patcher.setattr(meta_planner_v2, "project_patch_repair_context", lambda value: value)
        original, _ = _prompt(req, snap, graph)
    additions = [{"code": "FUTURE_CHECK", "node_ref": "missing_node"}, {"code": "GLOBAL_CHECK"}]
    old_count = len(original["validation_frontier"]["issues"])
    original["validation_frontier"]["issues"].extend(additions)
    original["validation_frontier"]["issue_count"] += len(additions)
    original["validation_frontier"]["omitted_issue_count"] = 7
    projected = project_patch_repair_context(original)
    review = projected["repair_contract"]["dependency_review"]
    assert set(range(old_count, old_count + len(additions))) <= set(review["global_issue_indices"])
    assert projected["validation_frontier"]["issues"][-2:] == additions
    assert projected["validation_frontier"]["omitted_issue_count"] == 7
    assert "missing_node" not in {node["node_ref"] for node in review["nodes"]}


def test_projection_does_not_resolve_unauthorized_resource_inputs():
    req, snap, graph = _case()
    req.scope.data_table_ids.clear()
    req.scope.data_table_write_grants.clear()
    payload, _ = _prompt(req, snap, graph)
    assert payload["capability_snapshot"]["resources"]["data_tables"] == []
    assert payload["repair_contract"]["resolved_resource_inputs"] == []
    assert all("required_inputs_now" not in node for node in payload["repair_contract"]["dependency_review"]["nodes"]
               if node["node_ref"] in {"lookup", "write"})
    assert any(item["category"] == "resource_contract" for item in payload["validation_frontier"]["issues"])


def _mixed_input_case():
    from server.meta_agent.schemas import GraphIntentV3

    snap, graph = snapshot(), intent("update")
    req = request(snap, "update")
    req.scope.allowed_node_kinds = ["input", "output", "workflow_agent", "json_serialize",
                                    "data_table_query", "data_table_update"]
    raw = graph.model_dump(mode="json")
    query_encoder = deepcopy(raw["nodes"][2])
    query_encoder.update(ref="encode_query", title="查询证据", inputs=[])
    query_encoder["outputs"][0]["variable"] = "query_text"
    extra = deepcopy(raw["nodes"][1]["inputs"][0])
    extra["port"] = "value"
    raw["nodes"][2]["inputs"].append(extra)
    raw["nodes"].insert(2, query_encoder)
    raw["nodes"][-1]["inputs"].append({
        "port": "task", "variable": "query_text", "source_ref": "encode_query",
        "source_port": "json", "value_schema": {"type": "string"},
    })
    raw["nodes"][-1]["config"]["task_input"] = "查询：{{query_text}}\n写入回执：{{encoded_resource}}"
    raw["control_edges"] = [{"source_ref": a["ref"], "target_ref": b["ref"]}
                            for a, b in zip(raw["nodes"], raw["nodes"][1:])]
    return req, snap, GraphIntentV3.model_validate(raw)


def _mixed_repairs():
    return [
        {"op": "disconnect_data", "source_ref": "lookup", "source_port": "result",
         "target_ref": "encode", "target_port": "value"},
        {"op": "connect_data", "source_ref": "lookup", "source_port": "result",
         "target_ref": "encode_query", "target_port": "value"},
    ]


def test_pure_node_worklist_exposes_expected_and_actual_counts_without_picking_sources():
    req, snap, graph = _mixed_input_case()
    before = graph.model_dump(mode="json")
    payload, _ = _prompt(req, snap, graph)
    work = {item["node_ref"]: item for item in payload["repair_contract"]["dependency_review"]["nodes"]}
    for ref, actual in (("encode_query", 0), ("encode", 2)):
        assert work[ref]["required_inputs_now"] == ["value"]
        assert work[ref]["input_shape_valid_now"] is False
        assert work[ref]["input_requirements_now"] == [{
            "port": "value", "minimum": 1, "maximum": 1, "actual": actual,
            "input_indices": list(range(actual)),
        }]
        assert work[ref]["issue_indices"]
    assert graph.model_dump(mode="json") == before
    assert "operations" not in payload["repair_contract"]["dependency_review"]


@pytest.mark.parametrize("operations", [[], _mixed_repairs()[:1], _mixed_repairs()[1:]])
def test_partial_input_repair_never_passes_atomic_validation(operations):
    req, _, graph = _mixed_input_case()
    frozen = graph.model_dump(mode="json")
    with pytest.raises(ValueError, match="exactly one value input"):
        _apply(req, graph, operations)
    assert graph.model_dump(mode="json") == frozen


def test_config_noop_keeps_both_pure_input_errors_observable():
    from server.meta_agent.generation_diagnostics import GraphPatchProgress, GenerationDiagnostics
    from server.meta_agent.graph_patch import apply_graph_patch

    req, _, graph = _mixed_input_case()
    diagnostics = GenerationDiagnostics("capability_compile")
    diagnostics.bind_graph(graph)
    counts = [issue for issue in diagnostics.as_dict()["input_contract_issues"]
              if issue["code"] == "INPUT_PORT_COUNT_MISMATCH"]
    assert {(item["node_index"], item["actual_count"]) for item in counts} == {(2, 0), (3, 2)}
    patch = GraphPatchEnvelopeV1(proposal_revision=1, expected_graph_checksum="a" * 64,
        expected_candidate_checksum="b" * 64, operations=[
            {"op": "update_node", "ref": ref, "config": {"format": "compact"}}
            for ref in ("encode_query", "encode")
        ])
    progress = GraphPatchProgress()
    with pytest.raises(ValueError):
        apply_graph_patch(graph, patch, plan_task_ids={task.task_id for task in _plan().tasks},
                          allowed_node_kinds=set(req.scope.allowed_node_kinds), progress=progress)
    assert len(progress.node_changes) == 2
    for item in progress.node_changes:
        assert item["after"]["expected_ports"] == [{"port": "value"}]
        assert item["after"]["input_shape_valid"] is False
        assert item["before"]["inputs_checksum"] == item["after"]["inputs_checksum"]


def test_complete_patch_preserves_query_and_write_evidence_through_roundtrip():
    req, snap, graph = _mixed_input_case()
    frozen = graph.model_dump(mode="json")
    for operations in (_mixed_repairs(), list(reversed(_mixed_repairs()))):
        fixed = _apply(req, graph, operations)
        assert validate_blueprint_authorization(req, _plan(), fixed, snap) == []
        compiled = compile_case(fixed, snap, req)
        rebuilt = compile_case(decompile_candidate_to_graph_intent(compiled), snap, req)
        assert workflow_semantic_checksum(compiled) == workflow_semantic_checksum(rebuilt)
        answer = fixed.nodes[-1]
        assert {item.source_ref for item in answer.inputs} == {"encode_query", "encode"}
        assert all("{{" + item.variable + "}}" in answer.config["task_input"] for item in answer.inputs)
    assert graph.model_dump(mode="json") == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_kind", ["two_ops", "remove_only", "complete", "unchanged_config"])
async def test_same_single_repair_budget_and_full_persisted_evidence(tmp_path, repair_kind):
    req, snap, graph = _case()
    all_ops = _operations(graph)
    operations = {"two_ops": all_ops[:2], "remove_only": all_ops[:1], "complete": all_ops,
                  "unchanged_config": [all_ops[0], *all_ops[3:]]}[repair_kind]
    responses = [_plan().model_dump_json(), graph.model_dump_json(), json.dumps({"operations": operations})]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3
        if len(calls) == 3:
            prompt = json.loads(args[2])
            assert prompt["repair_contract"]["dependency_review"]["nodes"]
            assert "data_bindings" not in prompt["validation_frontier"]
        return responses[len(calls) - 1]

    def preflight(candidate):
        return validate_xpert_definition(candidate), candidate.draft.workflow, []

    authoring = AuthoringService(AuthoringProposalStore(tmp_path / "runtime"), XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"), xpert_preflight=preflight)
    service = LegacyGraphReplayService(authoring_service=authoring, preflight=preflight, completion=completion)
    result = await service.generate(req, snap)
    assert len(calls) == 3
    assert result.validation["valid"] is (repair_kind in {"complete", "unchanged_config"})
    proposal = authoring.proposal_store.require(result.proposal_id)
    report = proposal.payload["meta_planner_report"]
    first, repaired = report["generation_diagnostics"][-2:]
    assert "data_bindings" in first and all("fingerprint" in issue for issue in first["issues"])
    assert proposal.status == "pending" and proposal.revision == 1
    assert not authoring.xpert_store.list_xperts()
    if repair_kind == "two_ops":
        assert repaired["failed_phase"] == "patch_apply"
        assert all(phase["status"] == "blocked" for phase in repaired["phase_results"]
                   if phase["id"] in ("resolve", "compile", "publish_preflight"))
    elif repair_kind == "remove_only":
        assert repaired["failed_phase"] == "authorization"
        assert any(issue["code"] == "DATA_UNREACHABLE" for issue in repaired["issues"])
