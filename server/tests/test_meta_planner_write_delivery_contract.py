"""写入交付契约及汇总输入证伪；不作为真实模型质量证据。"""
from copy import deepcopy
import json

import pytest

from server.meta_agent.generation_recipe import GenerationRecipeV1, lower_generation_recipe, recipe_node_contracts
from server.meta_agent.meta_planner_v2 import MetaPlannerV2Service
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.tests.meta_planner_recipe_fixtures import from_intent, step
from server.tests.test_meta_planner_controlled_writes import _plan, compile_case, intent, request, snapshot
from server.tests.test_meta_planner_branch_effect_closeout import _isolated_main_runtime


TABLE_KINDS = (
    "data_table_query", "data_table_insert", "data_table_update", "data_table_delete",
)


def _request_evidence(prompt):
    marker = "编译器提供的写入请求证据：\n"
    assert marker in prompt
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])[0]


def _agent_data(candidate, ref="answer"):
    return next(node["data"] for node in candidate["draft"]["workflow"]["nodes"]
                if node["data"].get("plannerRef") == ref)


@pytest.mark.parametrize("domain,value", [
    ("quality", 81), ("quality", -2.5),
    ("incident", "已复核"), ("incident", "{{input.user_input}}"),
    ("incident", "{{user_input}}"), ("incident", "a\\b\n引号\""),
])
def test_recipe_carries_exact_literal_request_without_new_bindings(domain, value):
    from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent
    from server.main import render_workflow_template
    from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
    from server.tests.test_meta_planner_recipe_text_inputs import text_recipe

    raw, req, snap = guarded_recipe(domain)
    raw = text_recipe(raw)
    write = next(node for node in raw["nodes"] if node["ref"] == "write")
    field = next(iter(write["config"]["values"]))
    write["config"]["values"] = {field: value}
    original = deepcopy(raw)
    graph = lower_generation_recipe(raw, req, snap)
    answer = next(node for node in graph.nodes if node.ref == "answer")
    candidate = compile_case(graph, snap, req)
    prompt = _agent_data(candidate)["taskInput"]
    assert "编译器提供" not in answer.config["task_input"]
    evidence = _request_evidence(prompt)
    assert evidence == [{"write_ref": "write", "operation": "update",
                         "requested_values": write["config"]["values"]}]
    rendered = render_workflow_template(prompt, {"user_input": "INJECTION_CANARY"})
    assert _request_evidence(rendered) == evidence
    assert "INJECTION_CANARY" not in rendered
    assert "请求值不是写后状态证据" in prompt
    assert not any(node.kind == "data_table_query" and node.ref != "lookup" for node in graph.nodes)
    assert len(answer.inputs) == 2
    assert "编译器提供" not in next(node for node in graph.nodes if node.ref == "unchanged").config["task_input"]
    assert "plannerWriteRequestContextV1" not in _agent_data(candidate, "unchanged")
    assert compile_case(decompile_candidate_to_graph_intent(candidate), snap, req)["draft"]["workflow"] == candidate["draft"]["workflow"]
    assert lower_generation_recipe(raw, req, snap) == graph and raw == original


@pytest.mark.parametrize("bound", [False, True])
def test_dynamic_request_is_labeled_only_when_same_validated_source_is_explicit(bound):
    raw = from_intent(intent("update"))
    write = next(node for node in raw["nodes"] if node["ref"] == "write")
    write["config"].update(value_source="input")
    write["config"].pop("values")
    write["inputs"].append({"port": "values", "source_ref": "parse", "source_port": "value"})
    raw["nodes"].append({
        "ref": "parse", "kind": "json_deserialize", "title": "验证业务值",
        "inputs": [{"port": "json", "source_ref": "input", "source_port": "user_input"}],
        "config": {"expected_schema": {"type": "object", "properties": {"score": {"type": "number"}}, "required": ["score"]}},
    })
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["inputs"] = None
    answer["config"]["task_input"] = "回执={{encode.json}}" + ("\n值={{parse.value}}" if bound else "")
    raw["control_flow"] = [step(ref) for ref in ("lookup", "parse", "write", "encode", "answer")]
    snap = snapshot()
    req = request(snap, "update")
    before = deepcopy(raw)
    graph = lower_generation_recipe(raw, req, snap)
    actual = next(node for node in graph.nodes if node.ref == "answer")
    assert len(actual.inputs) == 1 + int(bound)
    candidate = compile_case(graph, snap, req)
    prompt = _agent_data(candidate)["taskInput"]
    evidence = _request_evidence(prompt)[0]
    if bound:
        assert evidence["requested_values_input"] == {"source_ref": "parse", "source_port": "value"}
        assert "动态请求值（write）" in prompt
    else:
        assert evidence["requested_values_available"] is False
        assert "动态请求值（write）" not in prompt
    assert "requested_values" not in evidence
    assert "编译器提供" not in actual.config["task_input"]
    assert raw == before


def test_unused_binding_and_node_config_cannot_supply_delivery_evidence():
    from server.meta_agent.write_delivery import build_write_request_contexts
    raw = from_intent(intent("update"))
    answer = next(node for node in raw["nodes"] if node["ref"] == "answer")
    answer["config"].update(role_prompt="不引用其他证据。", task_input="说明范围。")
    snap = snapshot()
    graph = lower_generation_recipe(raw, request(snap, "update"), snap)
    assert build_write_request_contexts({node.ref: node for node in graph.nodes}) == {}
    assert "编译器提供" not in next(node for node in graph.nodes if node.ref == "answer").config["task_input"]
    answer["config"]["write_request_evidence"] = {"requested_values": {"score": 999}}
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, request(snap, "update"), snap)


def test_compilation_preserves_source_prompt_and_old_native_reader():
    from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent
    snap = snapshot()
    req = request(snap, "update")
    graph = intent("update")
    before = graph.model_dump(mode="json")
    candidate = compile_case(graph, snap, req)
    assert _request_evidence(_agent_data(candidate)["taskInput"])
    restored = decompile_candidate_to_graph_intent(candidate)
    assert next(node for node in restored.nodes if node.ref == "answer").config["task_input"] == next(
        node for node in graph.nodes if node.ref == "answer").config["task_input"]
    rebuilt = compile_case(restored, snap, req)["draft"]["workflow"]
    original = candidate["draft"]["workflow"]
    assert rebuilt["nodes"] == original["nodes"]
    assert sorted(rebuilt["edges"], key=lambda edge: edge["id"]) == sorted(original["edges"], key=lambda edge: edge["id"])
    assert graph.model_dump(mode="json") == before
    legacy = deepcopy(candidate)
    data = _agent_data(legacy)
    context = data.pop("plannerWriteRequestContextV1")
    data["taskInput"] = data["taskInput"][:-len(context)]
    frozen = deepcopy(legacy)
    legacy_graph = decompile_candidate_to_graph_intent(legacy)
    assert legacy_graph == restored
    assert legacy == frozen


def test_literal_evidence_overflow_fails_without_truncation_or_value_in_error():
    from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
    raw, req, snap = guarded_recipe("incident")
    write = next(node for node in raw["nodes"] if node["ref"] == "write")
    value = "REQUEST_DATA_CANARY" * 500
    write["config"]["values"] = {"status": value}
    before = deepcopy(raw)
    graph = lower_generation_recipe(raw, req, snap)
    with pytest.raises(ValueError, match="写入请求证据超过 Prompt 上限") as raised:
        compile_case(graph, snap, req)
    assert "REQUEST_DATA_CANARY" not in str(raised.value)
    assert raw == before


def test_headless_write_edit_rebuilds_context_without_copying_it_into_intent(tmp_path, monkeypatch):
    from server.data_tables.store import SQLiteAgentTableBackend
    from server.meta_agent.graph_ir_v3 import decompile_candidate_to_graph_intent
    from server.meta_agent.graph_patch import GraphPatchApplyRequest, GraphPatchEditorDiffRequest, GraphPatchEnvelopeV1
    from server.tests.test_meta_planner_write_headless import fixture

    def forbidden(*args, **kwargs):
        pytest.fail("请求证据编译及预览不得访问业务记录")

    for name in ("query_records", "execute_controlled_write", "create_record_for_schema"):
        monkeypatch.setattr(SQLiteAgentTableBackend, name, forbidden)
    service, authoring, proposal, _ = fixture(tmp_path, monkeypatch, "update")
    state = service.proposal_state(proposal.proposal_id)
    before = deepcopy(authoring.proposal_store.require(proposal.proposal_id))
    definition = deepcopy(state["candidate"]["draft"]["workflow"])
    write = next(node["data"] for node in definition["nodes"] if node["data"].get("plannerRef") == "write")
    config = deepcopy(write["plannerAdapterConfigV1"])
    config["values"] = {"score": 723}
    write["plannerWriteIntentV2"] = {"resource_id": "table-orders", "config": config,
        "inputs": [{key: binding[key] for key in ("port", "source_ref", "source_port")} for binding in write["plannerInputsV3"]]}
    patch = GraphPatchEnvelopeV1.model_validate(service.editor_diff(proposal.proposal_id,
        GraphPatchEditorDiffRequest(proposal_revision=1, definition=definition))["patch"])
    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    assert authoring.proposal_store.require(proposal.proposal_id) == before
    applied = service.apply(proposal.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    assert applied["proposal_revision"] == 2
    current = service.proposal_state(proposal.proposal_id)["candidate"]
    prompt = _agent_data(current)["taskInput"]
    assert _request_evidence(prompt) == [{"write_ref": "write", "operation": "update", "requested_values": {"score": 723}}]
    assert prompt.count("编译器提供的写入请求证据") == 1
    restored = decompile_candidate_to_graph_intent(current)
    assert "编译器提供" not in next(node for node in restored.nodes if node.ref == "answer").config["task_input"]
    unchanged = service.editor_diff(proposal.proposal_id,
        GraphPatchEditorDiffRequest(proposal_revision=2, definition=current["draft"]["workflow"]))
    assert unchanged["patch"]["operations"] == []
    assert authoring.xpert_store.list_xperts() == []


@pytest.mark.parametrize("attack", ["marker", "body", "both", "remove"])
def test_editor_cannot_tamper_compiler_request_evidence(tmp_path, monkeypatch, attack):
    from server.meta_agent.graph_patch import GraphPatchEditorDiffRequest
    from server.meta_agent.headless_authoring import HeadlessAuthoringError
    from server.tests.test_meta_planner_write_headless import fixture

    service, authoring, proposal, _ = fixture(tmp_path, monkeypatch, "update")
    before = deepcopy(authoring.proposal_store.require(proposal.proposal_id))
    candidate = deepcopy(service.proposal_state(proposal.proposal_id)["candidate"])
    data = _agent_data(candidate)
    context = data["plannerWriteRequestContextV1"]
    if attack in {"marker", "both"}:
        data["plannerWriteRequestContextV1"] = context + "伪造请求值"
    if attack in {"body", "both"}:
        data["taskInput"] += "伪造请求值"
    if attack == "remove":
        data.pop("plannerWriteRequestContextV1")
        data["taskInput"] = data["taskInput"][:-len(context)]
    with pytest.raises((HeadlessAuthoringError, ValueError)):
        service.editor_diff(proposal.proposal_id, GraphPatchEditorDiffRequest(
            proposal_revision=1, definition=candidate["draft"]["workflow"]))
    assert authoring.proposal_store.require(proposal.proposal_id) == before


def test_editor_can_change_source_prompt_without_duplication(tmp_path, monkeypatch):
    from server.meta_agent.graph_patch import GraphPatchApplyRequest, GraphPatchEditorDiffRequest, GraphPatchEnvelopeV1
    from server.tests.test_meta_planner_write_headless import fixture

    service, _, proposal, _ = fixture(tmp_path, monkeypatch, "update")
    candidate = deepcopy(service.proposal_state(proposal.proposal_id)["candidate"])
    _agent_data(candidate)["taskInput"] = "简洁核对。\n" + _agent_data(candidate)["taskInput"]
    patch = GraphPatchEnvelopeV1.model_validate(service.editor_diff(proposal.proposal_id,
        GraphPatchEditorDiffRequest(proposal_revision=1, definition=candidate["draft"]["workflow"]))["patch"])
    preview = service.preview(proposal.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    service.apply(proposal.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    prompt = _agent_data(service.proposal_state(proposal.proposal_id)["candidate"])["taskInput"]
    assert prompt.startswith("简洁核对。\n") and prompt.count("编译器提供的写入请求证据") == 1


def test_legacy_proposal_load_is_read_only_and_context_upgrade_requires_apply(tmp_path, monkeypatch):
    from server.meta_agent.graph_patch import GraphPatchApplyRequest, GraphPatchEnvelopeV1, MoveNodeOperation
    from server.tests.test_meta_planner_write_headless import fixture

    service, authoring, proposal, _ = fixture(tmp_path, monkeypatch, "update")
    legacy_payload = deepcopy(proposal.payload)
    data = _agent_data(legacy_payload)
    context = data.pop("plannerWriteRequestContextV1")
    data["taskInput"] = data["taskInput"][:-len(context)]
    legacy = authoring.proposal_store.create(kind="xpert_create", title="旧候选离线兼容",
        payload=legacy_payload, source_type="meta_planner", source_id="meta-planner:test")
    before = deepcopy(authoring.proposal_store.require(legacy.proposal_id))
    state = service.proposal_state(legacy.proposal_id)
    assert "plannerWriteRequestContextV1" not in _agent_data(state["candidate"])
    assert authoring.proposal_store.require(legacy.proposal_id) == before
    patch = GraphPatchEnvelopeV1(proposal_revision=1, expected_graph_checksum=state["graph_checksum"],
        expected_candidate_checksum=state["candidate_checksum"],
        operations=[MoveNodeOperation(ref="write", x=123, y=456)])
    preview = service.preview(legacy.proposal_id, patch)
    assert preview["can_apply"], preview["diagnostics"]
    assert preview["candidate_checksum"] != state["candidate_checksum"]
    assert authoring.proposal_store.require(legacy.proposal_id) == before
    result = service.apply(legacy.proposal_id, GraphPatchApplyRequest(patch=patch, preview_checksum=preview["preview_checksum"]))
    assert result["proposal_revision"] == 2
    assert _request_evidence(_agent_data(service.proposal_state(legacy.proposal_id)["candidate"])["taskInput"])
    assert authoring.xpert_store.list_xperts() == []


def _model_contracts(stage, operation="update"):
    snap = snapshot()
    req = request(snap, operation)
    req.scope.data_table_write_grants[0].operations = ["insert", "update", "delete"]
    service = MetaPlannerV2Service
    if stage == "recipe":
        return json.loads(service._recipe_prompt(req, _plan(), snap, None))["node_contracts"]
    if stage == "recipe_repair":
        return json.loads(service._recipe_repair_prompt(
            req, _plan(), snap, None, recipe=None, invalid_blueprint="{}",
        ))["node_contracts"]
    if stage == "recipe_edit":
        return json.loads(service._recipe_edit_prompt(
            req, _plan(), snap, None,
            recipe=GenerationRecipeV1.model_validate(from_intent(intent(operation))),
        ))["node_contracts"]
    if stage == "plan":
        prompt = service._plan_prompt(req, snap)
    elif stage == "plan_repair":
        prompt = service._plan_repair_prompt(req, snap, "{}", ["离线诊断"])
    elif stage == "graph":
        prompt = service._blueprint_prompt(req, _plan(), snap, None)
    elif stage == "graph_repair":
        prompt = service._repair_prompt(req, _plan(), snap, "{}", ["离线诊断"])
    else:
        prompt = service._patch_repair_prompt(req, _plan(), snap, intent(operation), ["离线诊断"])
    payload = json.loads(prompt)
    if stage.startswith("plan"):
        return {item["kind"]: item for item in payload["task_planning_contract"]["auxiliary_node_contracts"]}
    return payload["graph_intent_contract"]["node_roles"]["executable_node_contracts"]


@pytest.mark.parametrize("stage", [
    "recipe", "recipe_repair", "recipe_edit", "plan", "plan_repair",
    "graph", "graph_repair", "graph_patch",
])
@pytest.mark.parametrize("kind", TABLE_KINDS)
def test_model_paths_preserve_authoritative_result_evidence(stage, kind):
    contracts = _model_contracts(stage, "update" if kind == "data_table_query" else kind.removeprefix("data_table_"))
    expected = get_planner_node_adapter(kind).model_binding_contract()["output_binding_contract"]
    if stage.startswith("plan"):
        assert contracts[kind].get("output_evidence") == {
            key: expected[key] for key in ("shape", "delivery_rules")
        }
        assert "output_binding_contract" not in contracts[kind]
        assert "config_field_contract" not in contracts[kind]
    else:
        assert contracts[kind].get("output_binding_contract") == expected


def test_recipe_result_projection_is_fresh_metadata_not_a_record_read(monkeypatch):
    from server.data_tables.store import SQLiteAgentTableBackend

    def forbidden(*args, **kwargs):
        pytest.fail("模型提示投影不得访问业务记录")

    monkeypatch.setattr(SQLiteAgentTableBackend, "query_records", forbidden)
    snap = snapshot()
    req = request(snap, "update")
    req.scope.allowed_node_kinds = [kind for kind in req.scope.allowed_node_kinds
                                    if kind not in {"data_table_insert", "data_table_delete"}]
    before = deepcopy((req.model_dump(mode="json"), snap.model_dump(mode="json")))
    first = recipe_node_contracts(req, snap)
    assert "data_table_insert" not in first and "data_table_delete" not in first
    first["data_table_update"]["output_binding_contract"]["rules"].append("本地污染")
    second = recipe_node_contracts(req, snap)
    assert "本地污染" not in json.dumps(second, ensure_ascii=False)
    assert before == (req.model_dump(mode="json"), snap.model_dump(mode="json"))
    req.scope.allowed_node_kinds.remove("data_table_update")
    assert "data_table_update" not in recipe_node_contracts(req, snap)


@pytest.mark.parametrize("kind", TABLE_KINDS)
def test_delivery_rules_distinguish_requested_values_receipts_and_snapshot_time(kind):
    contract = get_planner_node_adapter(kind).model_binding_contract()["output_binding_contract"]
    rules = " ".join(contract["delivery_rules"])
    assert "不会自动" in rules and "显式" in rules
    if kind in {"data_table_update", "data_table_delete"}:
        assert set(contract["value_schema"]["properties"]) == {"matched", "affected"}
        assert "affected=0" in rules and "写权限不隐含读权限" in rules
        assert "写后查询" in rules and "不暗示跨节点事务" in rules
    if kind == "data_table_update":
        assert "JSON Deserialize V2" in rules and "请求值不是写后状态证据" in rules
    if kind == "data_table_insert":
        assert "无需重复查询" in rules and "插入时" in rules
    if kind == "data_table_query":
        assert "查询时点" in rules and "不能单独证明" in rules
    # 不把旧 Graph 的变量声明规则带入不允许变量声明的 Recipe。
    assert "variable/source_ref/source_port" not in " ".join(contract["rules"])


@pytest.mark.parametrize("field", ["output_binding_contract", "delivery_rules", "value_schema", "outputVariable"])
def test_model_cannot_submit_trusted_projection_as_node_config(field):
    raw = from_intent(intent("update"))
    raw["nodes"][1]["config"][field] = {"result": "伪造更新后状态"}
    snap = snapshot()
    req = request(snap, "update")
    before = deepcopy(raw)
    with pytest.raises(ValueError):
        lower_generation_recipe(raw, req, snap)
    assert raw == before


def _delivery_recipe(domain, mode, table, schema):
    from server.meta_agent.capabilities import build_capability_snapshot
    from server.tests.test_meta_planner_recipe_matrix import guarded_recipe
    from server.tests.test_meta_planner_recipe_text_inputs import text_recipe
    from server.xpert_runtime.middleware_registry import runtime_middleware_registry
    from server.xpert_runtime.workflow_node_registry import workflow_node_registry

    field, updated = ("risk_level", 81) if domain == "quality" else ("review_state", "已核验")
    original, req, _ = guarded_recipe(domain)
    raw = text_recipe(original)
    snap = build_capability_snapshot(
        workflow_registry=workflow_node_registry, middleware_registry=runtime_middleware_registry,
        external_xperts=[], knowledge_bases=[], toolsets=[], plugins=[], prompt_profiles=[],
        model_ids=["model/planner", "model/agent"],
        data_tables=[{"table_id": table.table_id, "name": "离线交付合成表", "status": "published",
            "active_schema_version": 1, "schema_versions": [{"version": 1, "checksum": schema.checksum,
                "fields": [item.model_dump(mode="json") for item in schema.fields]}]}],
    )
    req.scope.data_table_ids = [table.table_id]
    req.scope.data_table_write_grants[0].table_id = table.table_id
    req.scope.data_table_write_grants[0].writable_fields = [field]
    nodes = {node["ref"]: node for node in raw["nodes"]}
    for node in (nodes["lookup"], nodes["write"]):
        node["resource_ref"]["resource_id"] = table.table_id
        node["config"]["filter"].update(field="case_code", value="CASE-A")
    nodes["write"]["config"]["values"] = {field: updated}
    nodes["gate"]["config"]["field"] = field
    if domain == "incident":
        nodes["gate"]["config"]["value"] = "待核验"
    nodes["answer"]["config"].update(
        role_prompt="只按显式证据汇总，区分请求值、回执和查询时点状态。",
        task_input="回执={{write.result}}\n操作前={{lookup.result}}",
    )
    nodes["unchanged"]["config"].update(
        role_prompt="未执行写入，只说明查询时点的记录。", task_input="未修改={{lookup.result}}",
    )
    if mode == "request":
        nodes["answer"]["config"]["task_input"] += (
            "\n请求=" + json.dumps({field: updated}, ensure_ascii=False) + "\n请求值不是写后状态。"
        )
    elif mode == "post_query":
        query = deepcopy(nodes["lookup"])
        query.update(ref="verify", title="核对写后记录")
        raw["nodes"].append(query)
        nodes["answer"]["config"]["task_input"] += "\n写后={{verify.result}}"
        exists = raw["control_flow"][1]
        gate = next(item for item in exists["branches"] if item["outcome_ref"] == "unmatched")["steps"][0]
        matched = next(item for item in gate["branches"] if item["outcome_ref"] == "matched")
        matched["steps"].insert(1, step("verify"))
    before = deepcopy(raw)
    graph = lower_generation_recipe(raw, req, snap)
    assert raw == before
    assert {node.ref for node in graph.nodes if node.kind.startswith("data_table_")} == (
        {"lookup", "write", "verify"} if mode == "post_query" else {"lookup", "write"}
    )
    return compile_case(graph, snap, req)["draft"]["workflow"]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("mode", ["counts_only", "request", "post_query"])
@pytest.mark.parametrize("scenario", ["missing", "changed", "unchanged"])
async def test_isolated_effect_and_actual_summary_inputs_are_separate_evidence(
    tmp_path, monkeypatch, domain, mode, scenario,
):
    import server.main as main_module
    from server.data_tables.store import AgentTableStore
    from server.evaluations.executor import XpertEvaluationExecutor
    from server.evaluations.store import XpertEvaluationStore
    from server.evaluations.write_fixtures import checksum, inspect_write_node
    from server.xpert_runtime.run_registry import RunRegistry

    field, data_type, initial, updated, unchanged, protected = (
        ("risk_level", "number", 31, 81, 97, 103) if domain == "quality" else
        ("review_state", "string", "待核验", "已核验", "无需核验", "保留")
    )
    changed = scenario == "changed"
    exists = scenario != "missing"
    old = initial if changed else unchanged
    business = AgentTableStore(tmp_path / "business-sentinel-only")
    table = business.create_table(name="交付证据合成表", fields=[
        {"name": "case_code", "data_type": "string", "required": True},
        {"name": field, "data_type": data_type, "required": True},
    ])
    schema = business.publish_table(table.table_id, revision=table.draft_revision)
    sentinel = business.create_record_for_schema(table.table_id, schema_version=1,
        data={"case_code": "SENTINEL", field: protected}, operation_id="seed-sentinel")
    workflow = _delivery_recipe(domain, mode, table, schema)
    contracts = [inspect_write_node(node, backend=business, nested=False) for node in workflow["nodes"]
                 if node["type"] == "data_table_update"]
    store = XpertEvaluationStore(tmp_path / "evaluation", agent_table_backend=business)
    dataset = store.create_dataset("交付上下文离线证伪")
    seeds = [{"ref": "protected", "data": {"case_code": "CASE-B", field: protected}}]
    if exists:
        seeds.append({"ref": "target", "data": {"case_code": "CASE-A", field: old}})
    effect = {"node_ref": "write", "table_id": table.table_id, "operation": "update",
              "status": "applied" if changed else "not_executed", "affected_count": int(changed)}
    if changed:
        effect.update(expected_before={field: old}, expected_after={field: updated})
    path = {"required_outcomes": ["exists:unmatched" if exists else "exists:matched"],
            "terminal": "success" if exists else "error"}
    if exists:
        path["required_outcomes"].append("gate:matched" if changed else "gate:unmatched")
    else:
        path["error_code"] = "NOT_FOUND"
    draft = store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[{
        "case_id": "delivery", "message": "CASE-A", "path": path, "effects": [effect],
        "table_initializations": [{"table_id": table.table_id, "schema_version": 1, "source": "synthetic", "records": seeds}],
    }])
    store.publish_dataset(dataset["dataset_id"], revision=draft["revision"])
    version = store.get_dataset_version(dataset["dataset_id"], 1)
    run = store.create_run(dataset_version=version, cases=version["cases"], baseline=None,
        candidates=[{"target_id": "candidate", "label": "离线交付证据", "workflow": workflow,
                     "resources": {"write_contracts": contracts}}],
        config={"budget": {"max_concurrency": 1}}, warnings=[], write_isolation=True)
    agent_inputs = []

    async def capture_agent(model_id, prompt, *, system_prompt=None):
        agent_inputs.append(prompt + "\n" + (system_prompt or ""))
        yield "仅捕获输入，不模拟正确业务回答。"

    monkeypatch.setattr(main_module, "agent_table_store", business)
    monkeypatch.setattr(main_module, "get_xpert_evaluation_store", lambda: store)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://127.0.0.1:1", ""))
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", capture_agent)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    executor = XpertEvaluationExecutor(store, target_runner=main_module.run_xpert_evaluation_target)
    await executor._execute_run(store.claim_next_run())
    item = store.require_run(run["run_id"])["items"][0]
    isolated = AgentTableStore(store.storage_dir / "write_instances" / checksum({"run_id": run["run_id"], "item_id": item["item_id"]}))
    assert business.query_records(table.table_id, schema_version=1) == [sentinel]
    assert item["status"] == "completed" and item["effect_evidence"] == "verified", item
    assert item["score"] == 1, item
    actual = {row["case_code"]: row for row in isolated.query_records(table.table_id, schema_version=1)}
    assert {key: row[field] for key, row in actual.items()} == {
        "CASE-B": protected, **({"CASE-A": updated if changed else old} if exists else {}),
    }
    assert len(agent_inputs) == int(exists)
    if not exists:
        return
    prompt = agent_inputs[0]

    def value(marker):
        return json.JSONDecoder().raw_decode(prompt.split(marker + "=", 1)[1])[0]

    if not changed:
        assert value("未修改") == actual["CASE-A"]
        assert "回执=" not in prompt and "写后=" not in prompt
        return
    assert value("回执") == {"matched": 1, "affected": 1}
    assert value("操作前")[field] == old
    assert value("操作前")["revision"] == 1 and actual["CASE-A"]["revision"] == 2
    assert _request_evidence(prompt) == [{
        "write_ref": "write", "operation": "update", "requested_values": {field: updated},
    }]
    if mode == "post_query":
        assert value("写后") == actual["CASE-A"]
        assert "请求=" not in prompt
    else:
        assert "写后=" not in prompt
        if mode == "request":
            assert value("请求") == {field: updated}
            assert "请求值不是写后状态" in prompt
        else:
            # 原生回执仍只有计数；编译器的请求上下文不伪造写后记录。
            assert "请求=" not in prompt
            assert updated not in value("操作前").values()
            assert updated not in value("回执").values()
