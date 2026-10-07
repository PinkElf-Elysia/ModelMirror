"""Offline resource-type contrasts; no raw model responses or business records."""
from copy import deepcopy
import json

import pytest

from server.meta_agent.graph_ir_v3 import (
    GraphInputTypeError, decompile_candidate_to_graph_intent,
    resolve_graph_intent, workflow_semantic_checksum,
)
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.meta_planner_v2 import (
    MetaPlannerV2Service, _normalize_adapter_outputs_for_repair,
    validate_blueprint_authorization,
)
from server.tests.test_meta_planner_control_dependency_repair import _branch_case, _common_producer_patch
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, intent, request, snapshot
from server.tests.test_meta_planner_repair_context import _apply, offline_only
from server.workflow_native.node_contracts import WorkflowValueSchema


def _case(mode="first"):
    if mode == "first":
        req, snap, graph = _branch_case()
        graph = _apply(req, graph, _common_producer_patch())
    else:
        snap, graph = snapshot(), intent("update")
        req = request(snap, "update")
    req.scope.allowed_node_kinds = [
        "condition", "data_table_query", "input", "json_serialize", "output",
        "terminate_error", "workflow_agent", "data_table_update",
    ]
    return req, snap, graph


def _records(graph):
    return next(node for node in graph.nodes if node.kind == "data_table_update").inputs[0]


@pytest.mark.parametrize("mode,declared,accepted", [
    ("first", {"type": "any"}, True),
    ("first", {"type": "object", "nullable": True}, True),
    ("first", {"type": "object"}, False),
    ("first", {"type": "array", "items": {"type": "object"}}, False),
    ("first", {"type": "null"}, False),
    ("first", {"type": "string"}, False),
    ("first", {"type": "object", "nullable": True,
               "properties": {"revision": {"type": "string"}}}, False),
    ("list", {"type": "any"}, True),
    ("list", {"type": "object"}, False),
    ("list", {"type": "array", "items": {"type": "object"}}, True),
    ("list", {"type": "array", "items": {"type": "any"}}, True),
    ("list", {"type": "array", "items": {"type": "string"}}, False),
])
def test_direct_resource_types_are_identical_before_and_after_empty_patch(mode, declared, accepted):
    req, snap, graph = _case(mode)
    expected = compile_case(graph, snap, req)
    _records(graph).value_schema = WorkflowValueSchema.model_validate(declared)
    frozen = graph.model_dump(mode="json")
    patched, _ = _normalize_adapter_outputs_for_repair(_apply(req, graph, []), snap)
    assert _records(patched).value_schema == _records(graph).value_schema
    for candidate_graph in (graph, patched):
        type_issues = []
        assert validate_blueprint_authorization(
            req, _plan(), candidate_graph, snap, input_type_issues=type_issues,
        ) == []
        assert bool(type_issues) is not accepted
        if not accepted:
            with pytest.raises(GraphInputTypeError):
                compile_case(candidate_graph, snap, req)
            continue
        candidate = compile_case(candidate_graph, snap, req)
        ir = resolve_graph_intent(candidate_graph, snap, default_agent_model_id=req.default_agent_model_id,
                                  data_table_write_grants=req.scope.data_table_write_grants)
        source = next(node for node in ir.nodes if node.ref == "lookup")
        schema = next(port.value_schema for port in source.ports if port.direction == "output" and port.name == "result")
        edge = next(edge for edge in ir.edges if edge.mode == "data" and edge.target.port == "records")
        assert edge.value_schema == schema and edge.value_schema.type != "any"
        assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(expected)
        rebuilt = compile_case(decompile_candidate_to_graph_intent(candidate), snap, req)
        assert workflow_semantic_checksum(rebuilt) == workflow_semantic_checksum(candidate)
    assert graph.model_dump(mode="json") == frozen


def _planner(tmp_path, responses, calls):
    from server.skills.draft_store import WorkspaceSkillDraftStore
    from server.xpert_runtime.authoring_service import AuthoringService
    from server.xpert_runtime.authoring_store import AuthoringProposalStore
    from server.xperts.store import XpertStore
    from server.xperts.validation import validate_xpert_definition

    async def completion(*args):
        calls.append(json.loads(args[2]))
        assert len(calls) <= len(responses) <= 3, "Unexpected model dispatch"
        return responses[len(calls) - 1]

    def preflight(candidate):
        return validate_xpert_definition(candidate), candidate.draft.workflow, []

    authoring = AuthoringService(AuthoringProposalStore(tmp_path / "runtime"), XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "skills"), xpert_preflight=preflight)
    return LegacyGraphReplayService(authoring_service=authoring, preflight=preflight, completion=completion), authoring


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_plan,broad_declaration", [(True, True), (True, False), (False, False), (False, True)])
async def test_resource_derivation_does_not_consume_the_single_model_repair(tmp_path, bad_plan, broad_declaration):
    req, snap, graph = _case()
    task = _plan().model_dump(mode="json")
    first = deepcopy(task)
    if bad_plan:
        first["tasks"][0]["input_contract"] = "synthetic non-list"
    if broad_declaration:
        _records(graph).value_schema = WorkflowValueSchema(type="any")
    responses = [json.dumps(first)] + ([json.dumps(task)] if bad_plan else []) + [graph.model_dump_json()]
    calls = []
    planner, authoring = _planner(tmp_path, responses, calls)
    result = await planner.generate(req, snap)
    assert len(calls) == (3 if bad_plan else 2)
    assert result.validation["valid"] is True
    assert result.repair_used is bad_plan
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.asyncio
async def test_empty_patch_cannot_erase_explicit_resource_type_conflicts(tmp_path):
    req, snap, graph = _case()
    _records(graph).value_schema = WorkflowValueSchema(type="object")
    calls = []
    planner, authoring = _planner(tmp_path, [_plan().model_dump_json(), graph.model_dump_json(),
        json.dumps({"operations": []})], calls)
    result = await planner.generate(req, snap)
    assert len(calls) == 3 and result.repair_used
    assert result.validation["valid"] is False
    report = authoring.proposal_store.require(result.proposal_id).payload["meta_planner_report"]
    failed = [stage for stage in report["generation_diagnostics"] if stage["stage"] in {"capability_compile", "graph_patch_v1"}]
    assert len(failed) == 2 and all(stage["category_counts"]["type_ports"] >= 1 for stage in failed)
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.parametrize("fault", [
    "unknown_source", "unknown_port", "variable_alias", "missing_control", "unauthorized_field",
    "no_read_grant", "no_write_grant", "missing_table", "archive", "row_limit", "cross_table",
])
def test_derived_type_never_repairs_identity_authority_or_control(fault):
    req, snap, graph = _case()
    writer = next(node for node in graph.nodes if node.kind == "data_table_update")
    binding = _records(graph)
    binding.value_schema = WorkflowValueSchema(type="any")
    if fault == "unknown_source":
        binding.source_ref = "unknown"
    elif fault == "unknown_port":
        binding.source_port = "record_id"
    elif fault == "variable_alias":
        binding.variable = "forged_records"
    elif fault == "missing_control":
        graph.control_edges = [edge for edge in graph.control_edges if edge.target_ref != writer.ref]
    elif fault == "unauthorized_field":
        writer.config["values"] = {"unknown_field": "synthetic"}
    elif fault == "no_read_grant":
        req.scope.data_table_ids = []
    elif fault == "no_write_grant":
        req.scope.data_table_write_grants = []
    elif fault == "missing_table":
        snap.data_tables.clear()
    elif fault == "archive":
        snap.data_tables[0]["status"] = "archived"
    elif fault == "row_limit":
        writer.config["max_affected_rows"] = 2
    else:
        other = deepcopy(snap.data_tables[0])
        other["id"] = "table-other"
        snap.data_tables.append(other)
        req.scope.data_table_ids.append("table-other")
        next(node for node in graph.nodes if node.ref == "lookup").resource_ref.resource_id = "table-other"
    for repair in (False, True):
        if validate_blueprint_authorization(req, _plan(), graph, snap):
            continue  # Scope is checked before the resolver/compiler on both service paths.
        with pytest.raises(ValueError):
            checked = _normalize_adapter_outputs_for_repair(_apply(req, graph, []), snap)[0] if repair else graph
            compile_case(checked, snap, req)


def test_real_resource_type_cannot_be_relabeled_for_text_consumer():
    req, snap, graph = _case("list")
    answer = next(node for node in graph.nodes if node.kind == "workflow_agent")
    binding = answer.inputs[0]
    binding.source_ref, binding.source_port, binding.variable = "lookup", "result", "rows"
    binding.value_schema = WorkflowValueSchema(type="any")
    answer.config["task_input"] = "{{rows}}"
    for repair in (False, True):
        checked = _normalize_adapter_outputs_for_repair(graph, snap)[0] if repair else graph
        with pytest.raises(GraphInputTypeError):
            compile_case(checked, snap, req)
