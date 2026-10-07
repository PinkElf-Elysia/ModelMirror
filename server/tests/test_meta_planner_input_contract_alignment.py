from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
from jsonschema import Draft202012Validator
import pytest

from server.meta_agent.generation_contract import _ports, graph_generation_schema
from server.meta_agent.generation_diagnostics import GenerationDiagnostics
from server.meta_agent.graph_ir_v3 import (
    GraphInputTypeError, GraphInputTypeIssue, _schemas_compatible,
    decompile_candidate_to_graph_intent, graph_intent_to_v2,
    resolve_graph_intent, workflow_semantic_checksum,
)
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service, validate_blueprint_authorization
from server.tests.meta_planner_legacy_replay import LegacyGraphReplayService
from server.meta_agent.node_adapters import PLANNER_NODE_ADAPTERS, get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentControlEdgeV3, GraphIntentNodeV3, GraphIntentV3
from server.tests.test_meta_planner_control_contract_alignment import _chain, _request
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, snapshot
from server.tests.test_meta_planner_read_resources import _answer_node, _input, _serialize_node
from server.tests.test_meta_planner_write_generation_failures import _forbid_records
from server.tests.test_meta_planner_write_headless import fixture
from server.workflow_native.node_contracts import WorkflowValueSchema


STRING = WorkflowValueSchema(type="string")
OBJECT = WorkflowValueSchema(type="object")


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    _forbid_records(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("输入契约测试禁止 HTTP、Provider 和业务记录访问")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def _pack_graph(*, mixed=True):
    pack = GraphIntentNodeV3(
        ref="pack", kind="variable_aggregator", title="打包合成输入",
        inputs=[_input("values", "user_input", "input", "user_input", STRING)],
        outputs=[{"port": "result", "variable": "packed", "value_schema": OBJECT}],
        config={"output_fields": ["request"]},
    )
    answer = _answer_node(source_ref="pack", source_port="result", variable="packed", schema=OBJECT)
    answer.title = "汇总合成结果"
    if mixed:
        answer.inputs.insert(0, _input("task", "user_input", "input", "user_input", STRING))
        answer.config["task_input"] = "{{user_input}}\n{{packed}}"
    return GraphIntentV3(name="输入契约合成反例", nodes=[pack, answer],
        control_edges=[{"source_ref": "pack", "target_ref": "answer"}],
        final_output={"sources": [{"node_ref": "answer"}]})


def _combined_graph():
    graph = _chain("stop")
    graph.nodes.pop()
    for node in graph.nodes:
        if node.kind == "data_table_query":
            node.config["return_mode"] = "first"
            # R14 observed this union declaration, not a list-only declaration.
            node.outputs[0].value_schema = WorkflowValueSchema(any_of=(
                WorkflowValueSchema(type="array", items=OBJECT),
                OBJECT.model_copy(update={"nullable": True}),
            ))
        elif node.kind in {"data_table_update", "data_table_delete"}:
            node.inputs[0].value_schema = OBJECT
    pack, answer = _pack_graph().nodes
    pack.inputs = [_input("values", node.outputs[0].variable, node.ref, "json", STRING)
                   for node in graph.nodes if node.kind == "json_serialize"]
    pack.config = {"output_fields": ["insert", "update", "delete"]}
    graph.nodes.extend([pack, answer])
    graph.control_edges = [GraphIntentControlEdgeV3(source_ref=a.ref, target_ref=b.ref)
                           for a, b in zip(graph.nodes, graph.nodes[1:])]
    return graph


def _pure_request(snap):
    req = _request(snap)
    req.goal = "打包合成输入并汇总结果。"
    req.scope.data_table_ids = []
    req.scope.data_table_write_grants = []
    return req


def _diagnose(graph, snap, req):
    stage = GenerationDiagnostics("capability_compile")

    def forbidden(*args):
        pytest.fail("非法类型图不得进入发布预检")

    result = MetaPlannerV2Service._compile_and_validate(
        SimpleNamespace(preflight=forbidden), request=req, plan=_plan(),
        raw_blueprint=graph.model_dump_json(), snapshot=snap, target=None, diagnostics=stage,
    )
    return result[3], stage.as_dict()


def _repair_prompt(graph, snap, req, issues):
    return json.loads(MetaPlannerV2Service._patch_repair_prompt(req, _plan(), snap, graph, issues))


@pytest.mark.parametrize("declared", [
    {"type": "string"}, {"type": "object"}, {"type": "array"}, {"type": "number"},
    {"type": "integer"}, {"type": "boolean"}, {"type": "null"}, {"type": "any"},
    {"type": "string", "nullable": True},
    {"any_of": [{"type": "string"}]},
    {"any_of": [{"type": "string"}, {"type": "object"}]},
])
def test_agent_input_projection_matches_contract(declared):
    snap = snapshot()
    graph = _pack_graph()
    graph.nodes[-1].inputs[-1].value_schema = WorkflowValueSchema.model_validate(declared)
    target = get_planner_node_adapter("workflow_agent").intent_port_contracts("input")[0]
    schema = graph_generation_schema(_request(snap), snap)
    accepted = Draft202012Validator(schema).is_valid(graph.model_dump(mode="json"))
    assert accepted == _schemas_compatible(graph.nodes[-1].inputs[-1].value_schema, target.value_schema)


@pytest.mark.parametrize("mixed", [False, True])
def test_repair_identifies_only_the_object_edge_on_a_many_input_port(mixed):
    snap = snapshot()
    req, graph = _pure_request(snap), _pack_graph(mixed=mixed)
    before = deepcopy(graph.model_dump(mode="json"))
    issues, _ = _diagnose(graph, snap, req)
    repair = _repair_prompt(graph, snap, req, issues)["repair_contract"]
    details = repair["data_contract_issues"]
    assert len(details) == 1
    assert details[0]["node_ref"] == "answer"
    assert details[0]["input_index"] == int(mixed)
    assert details[0]["source_ref"] == "pack"
    assert details[0]["source_assignable"] is False
    guidance = "\n".join(repair["issue_playbook"])
    assert "pack.result->answer.task" in guidance and "json_serialize" in guidance
    assert "input.user_input->answer.task" not in guidance
    assert graph.model_dump(mode="json") == before


def test_all_independent_types_are_reported_before_the_single_repair():
    snap = snapshot()
    req, graph = _request(snap), _combined_graph()
    before = deepcopy(graph.model_dump(mode="json"))
    issues, stage = _diagnose(graph, snap, req)
    assert len(issues) == 3
    assert all(any(ref in message for message in issues)
               for ref in ("update_ticket", "delete_ticket", "answer"))
    assert "compile" not in stage["checks_executed"]
    assert stage["category_counts"]["type_ports"] == 3
    repair = _repair_prompt(graph, snap, req, issues)["repair_contract"]
    assert len(repair["data_contract_issues"]) == 3
    assert graph.model_dump(mode="json") == before


def test_declared_string_cannot_hide_object_source_or_its_bridge_diagnostic():
    snap = snapshot()
    graph = _pack_graph()
    graph.nodes[-1].inputs[-1].value_schema = STRING
    issues, _ = _diagnose(graph, snap, _pure_request(snap))
    assert issues
    repair = _repair_prompt(graph, snap, _pure_request(snap), issues)["repair_contract"]
    assert repair["data_contract_issues"][0]["source_assignable"] is False
    assert any("pack.result->answer.task" in item for item in repair["issue_playbook"])


def test_explicit_bridge_preserves_compile_roundtrip():
    snap = snapshot()
    graph = _pack_graph()
    encode = _serialize_node(source_ref="pack", variable="packed", schema=OBJECT)
    graph.nodes.insert(1, encode)
    graph.nodes[-1].inputs[-1] = _input("task", "encoded_resource", "encode", "json", STRING)
    graph.nodes[-1].config["task_input"] = "{{user_input}}\n{{encoded_resource}}"
    graph.control_edges = [GraphIntentControlEdgeV3(source_ref=a.ref, target_ref=b.ref)
                           for a, b in zip(graph.nodes, graph.nodes[1:])]
    candidate = compile_case(graph, snap, _request(snap))
    rebuilt = compile_case(decompile_candidate_to_graph_intent(candidate), snap, _request(snap))
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(rebuilt)


def _bridge_operations(graph):
    config = deepcopy(graph.nodes[-1].config)
    config["task_input"] = "{{user_input}}\n{{packed_json}}"
    # An explicit reconnect, not empty-Patch normalization, corrects each narrowed record declaration.
    records = []
    for node in graph.nodes:
        if node.kind not in {"data_table_update", "data_table_delete"}:
            continue
        binding = next(item for item in node.inputs if item.port == "records")
        edge = {"source_ref": binding.source_ref, "source_port": binding.source_port,
                "target_ref": node.ref, "target_port": binding.port}
        records.extend([{"op": "disconnect_data", **edge}, {"op": "connect_data", **edge}])
    return records + [
        {"op": "disconnect_data", "source_ref": "pack", "source_port": "result", "target_ref": "answer", "target_port": "task"},
        {"op": "disconnect_control", "source_ref": "pack", "target_ref": "answer"},
        {"op": "add_node", "ref": "encode_pack", "kind": "json_serialize", "title": "将回执转为文本",
         "task_ids": [], "config": {"format": "compact"}, "output_variables": {"json": "packed_json"}},
        {"op": "connect_control", "source_ref": "pack", "target_ref": "encode_pack"},
        {"op": "connect_control", "source_ref": "encode_pack", "target_ref": "answer"},
        {"op": "connect_data", "source_ref": "pack", "source_port": "result", "target_ref": "encode_pack", "target_port": "value"},
        {"op": "connect_data", "source_ref": "encode_pack", "source_port": "json", "target_ref": "answer", "target_port": "task"},
        {"op": "update_node", "ref": "answer", "config": config},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_bridge", [False, True])
async def test_single_repair_sees_all_types_and_cannot_silently_fix_object_edge(
    tmp_path, monkeypatch, explicit_bridge,
):
    headless, authoring, _, snap = fixture(tmp_path, monkeypatch)
    graph, req = _combined_graph(), _request(snap)
    before = deepcopy(graph.model_dump(mode="json"))
    responses = [_plan().model_dump_json(), graph.model_dump_json(),
                 json.dumps({"operations": _bridge_operations(graph) if explicit_bridge else []})]
    calls = []

    async def completion(*args):
        calls.append(args)
        assert len(calls) <= 3, "不得增加修复调用"
        if len(calls) == 3:
            prompt = json.loads(args[2])
            assert len(prompt["validation_issues"]) == 3
            details = prompt["repair_contract"]["data_contract_issues"]
            assert len(details) == 3
            assert {item["node_ref"] for item in details if not item["source_assignable"]} == {"answer"}
            assert all("properties" not in json.dumps(item) for item in details)
        return responses[len(calls) - 1]

    service = LegacyGraphReplayService(authoring_service=authoring,
        preflight=headless.planner_service.preflight, completion=completion)
    result = await service.generate(req, snap)
    assert len(calls) == 3
    assert result.validation["valid"] is explicit_bridge
    assert graph.model_dump(mode="json") == before
    proposal = authoring.proposal_store.require(result.proposal_id)
    assert proposal.status == "pending" and proposal.revision == 1
    assert proposal.applied_resource_id is None and authoring.xpert_store.list_xperts() == []
    report = proposal.payload["meta_planner_report"]
    first = next(stage for stage in report["generation_diagnostics"] if stage["stage"] == "capability_compile")
    assert first["issue_count"] == 3 and first["category_counts"]["type_ports"] == 3
    assert all(item["code"] == "DATA_TYPE_MISMATCH" and "location" in item for item in first["issues"])


def test_unauthorized_resource_never_produces_trusted_type_facts(monkeypatch):
    import server.meta_agent.meta_planner_v2 as module

    snap = snapshot()
    graph, req = _combined_graph(), _request(snap)
    req.scope.data_table_write_grants = []
    issues = validate_blueprint_authorization(req, _plan(), graph, snap)
    assert issues

    def forbidden(*args, **kwargs):
        pytest.fail("未授权图不能进入权威资源类型解析")

    monkeypatch.setattr(module, "resolve_graph_intent", forbidden)
    assert _repair_prompt(graph, snap, req, issues)["repair_contract"]["data_contract_issues"] == []


def test_bridge_guidance_does_not_grant_unauthorized_serializer():
    snap = snapshot()
    graph, req = _pack_graph(), _pure_request(snap)
    req.scope.allowed_node_kinds.remove("json_serialize")
    issues, _ = _diagnose(graph, snap, req)
    repair = _repair_prompt(graph, snap, req, issues)["repair_contract"]
    assert len(repair["data_contract_issues"]) == 1
    assert "json_serialize" not in repair["addable_node_kinds"]
    assert any("未获授权" in item for item in repair["issue_playbook"])


def test_wrong_declaration_on_string_source_does_not_suggest_extra_serialization():
    snap = snapshot()
    graph, req = _pack_graph(), _pure_request(snap)
    graph.nodes[-1].inputs[0].value_schema = OBJECT
    issues, _ = _diagnose(graph, snap, req)
    repair = _repair_prompt(graph, snap, req, issues)["repair_contract"]
    assert len(repair["data_contract_issues"]) == 2
    root = next(item for item in repair["data_contract_issues"] if item["source_ref"] == "input")
    assert root["source_assignable"] is True
    assert not any("input.user_input->answer.task" in item for item in repair["issue_playbook"])


def test_resolver_keeps_legacy_first_message_and_reports_every_binding():
    graph, snap = _combined_graph(), snapshot()
    before = deepcopy(graph.model_dump(mode="json"))
    with pytest.raises(GraphInputTypeError, match="Node update_ticket input query_after_insert_rows has an incompatible type") as error:
        resolve_graph_intent(graph, snap, default_agent_model_id="model/agent",
            data_table_write_grants=_request(snap).scope.data_table_write_grants)
    assert len(error.value.issues) == 3
    assert graph.model_dump(mode="json") == before


def test_v2_keeps_its_type_gate_while_v3_uses_the_full_resolver():
    snap, graph = snapshot(), _pack_graph()
    req = _pure_request(snap)
    graph.nodes[-1].inputs[-1].value_schema = STRING
    legacy_issues = validate_blueprint_authorization(req, _plan(), graph_intent_to_v2(graph), snap)
    assert any("Variable packed type object does not match answer input type string" in issue
               for issue in legacy_issues)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    issues, stage = _diagnose(graph, snap, req)
    assert len(issues) == 1 and stage["category_counts"]["type_ports"] == 1
    with pytest.raises(GraphInputTypeError):
        compile_case(graph, snap, req)


def test_every_static_input_projection_matches_its_authoritative_port():
    declarations = [WorkflowValueSchema(type=name) for name in (
        "any", "null", "string", "boolean", "integer", "number", "object", "array",
    )]
    declarations.extend([
        WorkflowValueSchema(type="string", nullable=True),
        WorkflowValueSchema(type="object", nullable=True),
        WorkflowValueSchema(type="array", items=OBJECT),
        WorkflowValueSchema(type="array", items=STRING),
        WorkflowValueSchema(any_of=(STRING,)),
        WorkflowValueSchema(any_of=(STRING, OBJECT)),
    ])
    checked = set()
    for adapter in PLANNER_NODE_ADAPTERS.values():
        if adapter.model_binding_contract():
            continue  # Dynamic table ports have separate resource-schema tests.
        definitions = deepcopy(GraphIntentV3.model_json_schema()["$defs"])
        projected = _ports(adapter, "input", definitions)
        validator = Draft202012Validator({"$defs": definitions, **projected})
        ports = adapter.intent_port_contracts("input")
        for port in ports:
            for declared in declarations:
                inputs = [_input(item.name, "user_input", "input", "user_input",
                                 declared if item.name == port.name else item.value_schema)
                          for item in ports if item.required or item.name == port.name]
                actual = validator.is_valid([item.model_dump(mode="json") for item in inputs])
                assert actual == _schemas_compatible(declared, port.value_schema), (
                    adapter.kind, port.name, declared.model_dump(mode="json"),
                )
            checked.add(adapter.kind)
    assert {"workflow_agent", "json_deserialize", "json_serialize", "variable_aggregator",
            "data_aggregate", "dataset_compare", "data_merge", "vision_understanding"} <= checked


@pytest.mark.parametrize("fault", ["unknown_port", "unknown_source", "alias", "cycle"])
def test_dependency_failures_do_not_emit_guessed_type_facts(fault):
    snap, graph = snapshot(), _pack_graph()
    req = _pure_request(snap)
    binding = graph.nodes[-1].inputs[-1]
    if fault == "unknown_port":
        binding.port = "not_a_task_port"
    elif fault == "unknown_source":
        binding.source_port = "not_a_result_port"
    elif fault == "alias":
        binding.variable = "forged_variable"
    else:
        graph.control_edges.append(GraphIntentControlEdgeV3(source_ref="answer", target_ref="pack"))
    issues, _ = _diagnose(graph, snap, req)
    assert issues
    repair = _repair_prompt(graph, snap, req, issues)["repair_contract"]
    assert repair["data_contract_issues"] == []


def test_type_diagnostics_exclude_object_fields_and_business_values():
    field_canary = "private_field_canary"
    value_canary = "PRIVATE_SYNTHETIC_VALUE_DO_NOT_PERSIST"
    source = WorkflowValueSchema(type="object", properties={field_canary: STRING}, required=(field_canary,))
    binding = _input("task", "packed", "pack", "result", source)
    issue = GraphInputTypeIssue(1, 0, "answer", binding, source, STRING)
    stage = GenerationDiagnostics("capability_compile")
    stage.messages([issue.summary], category="type_ports", code="DATA_TYPE_MISMATCH",
                   location=["nodes", issue.node_index, "inputs", issue.input_index])
    detail = issue.repair_detail()
    assert detail["source_schema"] == {"type": "object", "nullable": False}
    assert field_canary not in json.dumps([detail, stage.as_dict()])
    snap, graph = snapshot(), _pack_graph()
    graph.nodes[-1].config["role_prompt"] = value_canary
    _, diagnostics = _diagnose(graph, snap, _pure_request(snap))
    assert value_canary not in json.dumps(diagnostics)
