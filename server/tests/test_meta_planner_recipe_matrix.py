"""Cross-domain, non-linear frozen corpus; no provider-success claim."""
from copy import deepcopy

import pytest

from server.meta_agent.graph_ir_v3 import resolve_graph_intent
from server.meta_agent.capabilities import build_capability_snapshot
from server.meta_agent.meta_planner_v2 import validate_blueprint_authorization
from server.tests.test_meta_planner_controlled_writes import intent, request, snapshot, compile_case, _plan
from server.tests.meta_planner_recipe_fixtures import from_intent, step
from server.meta_agent.generation_recipe import lower_generation_recipe
from server.tests.test_meta_planner_control_flow import _agent
from server.tests.test_meta_planner_branch_effect_closeout import _isolated_main_runtime
from server.xpert_runtime.middleware_registry import runtime_middleware_registry
from server.xpert_runtime.workflow_node_registry import workflow_node_registry


def guarded_recipe(domain="quality", error_output=False):
    key, field = ("sku", "score") if domain == "quality" else ("incident_code", "status")
    schema_type = "number" if domain == "quality" else "string"
    snap = build_capability_snapshot(
        workflow_registry=workflow_node_registry, middleware_registry=runtime_middleware_registry,
        external_xperts=[], knowledge_bases=[], toolsets=[], plugins=[], prompt_profiles=[],
        model_ids=["model/planner", "model/agent"],
        data_tables=[{"table_id": "table-orders", "name": "离线合成表", "status": "published", "active_schema_version": 1,
            "schema_versions": [{"version": 1, "checksum": "a" * 64, "fields": [
                {"name": key, "data_type": "string", "required": True},
                {"name": field, "data_type": schema_type, "required": True},
            ]}]}],
    )
    req = request(snap, "update")
    raw = from_intent(intent("update"))
    req.scope.data_table_write_grants[0].writable_fields = [key, field]
    req.goal = "检查合成质检分数，低于阈值时更新，否则说明无需变更。" if domain == "quality" else "查一下合成事件，待处理的改成已处理，没找到就停止，其他不用动。"
    raw["name"] = "合成质检分支" if domain == "quality" else "合成事件分支"
    lookup, write, _, _ = raw["nodes"]
    lookup["config"] = {"return_mode": "first", "filter": {"ref": "key", "field": key, "operator": "eq", "value": "DEMO"}}
    write["config"]["values"] = {field: 65 if domain == "quality" else "已处理"}
    write["config"]["filter"]["field"] = key
    raw["nodes"].extend([
        {"ref": "exists", "kind": "condition", "title": "检查记录存在", "inputs": [{"port": "value", "source_ref": "lookup", "source_port": "result"}], "config": {"operator": "is_null"}},
        {"ref": "gate", "kind": "condition", "title": "检查业务条件", "inputs": [{"port": "value", "source_ref": "lookup", "source_port": "result"}],
         "config": {"field": field, "operator": "lt" if domain == "quality" else "equals", "value_type": "number" if domain == "quality" else "text", "value": 60 if domain == "quality" else "待处理"}},
        {"ref": "missing", "kind": "terminate_error", "title": "记录不存在", "config": {"error_code": "NOT_FOUND", "message": "未找到记录。"}},
        from_intent(type(intent())(name="跳过", nodes=[_agent("unchanged", variable="unchanged_result")], final_output={"sources": [{"node_ref": "unchanged"}]}))["nodes"][0],
    ])
    raw["final_output"]["sources"].append({"node_ref": "unchanged", "port": "result"})
    success = [step("exists", {"matched": [step("missing")], "unmatched": [step("gate", {
        "matched": [step("write"), step("encode"), step("answer")], "unmatched": [step("unchanged")],
    })]})]
    if error_output:
        lookup["config"]["failure_action"] = "error_output"
        raw["nodes"].append({"ref": "read_failed", "kind": "terminate_error", "title": "读取失败", "config": {"error_code": "READ_FAILED", "message": "合成表读取失败。"}})
        raw["control_flow"] = [step("lookup", {"success": success, "error": [step("read_failed")]})]
    else:
        raw["control_flow"] = [step("lookup"), *success]
    return raw, req, snap


@pytest.mark.parametrize("domain", ["quality", "incident"])
@pytest.mark.parametrize("error_output", [False, True])
def test_same_compiler_keeps_null_business_no_write_and_read_error_paths(domain, error_output):
    raw, req, snap = guarded_recipe(domain, error_output)
    graph = lower_generation_recipe(raw, req, snap)
    assert validate_blueprint_authorization(req, _plan(), graph, snap) == []
    ir = resolve_graph_intent(graph, snap, default_agent_model_id=req.default_agent_model_id,
                             data_table_write_grants=req.scope.data_table_write_grants)
    scenarios = ir.control_flow_report["scenarios"]
    assert len(scenarios) == 3 + int(error_output)
    assert {tuple(item["success_sources"]) for item in scenarios} == {(), ("answer",), ("unchanged",)}
    for item in scenarios:
        assert ("write" in item["reached"]) == (item["success_sources"] == ["answer"])
        if "gate" in item["reached"]:
            assert item["choices"]["exists"] == "unmatched"
    assert all(node.inputs[0].value_schema.nullable for node in graph.nodes if node.ref in {"exists", "gate"})
    compile_case(graph, snap, req)


@pytest.mark.parametrize("mutation", ["bypass_null", "branch_only_input", "parallel_write", "swapped_guard", "cross_table", "forged_records", "any_narrowing"])
def test_matrix_counterexamples_fail_without_automatic_repair(mutation):
    raw, req, snap = guarded_recipe()
    compile_case(lower_generation_recipe(raw, req, snap), snap, req)
    if mutation == "bypass_null":
        # Same nodes but fields inspected on the null branch.
        raw["control_flow"][1]["branches"].reverse()
        for branch in raw["control_flow"][1]["branches"]:
            branch["outcome_ref"] = "unmatched" if branch["outcome_ref"] == "matched" else "matched"
    elif mutation == "branch_only_input":
        node = next(node for node in raw["nodes"] if node["ref"] == "unchanged")
        node["inputs"] = [{"port": "task", "source_ref": "encode", "source_port": "json"}]
        node["config"]["task_input"] = "{{encode.json}}"
    elif mutation == "parallel_write":
        raw["control_flow"] = [{"type": "parallel", "paths": [[step("lookup")], raw["control_flow"][1:]]}]
    elif mutation == "swapped_guard":
        raw["nodes"][4]["config"] = {"field": "score", "operator": "lt", "value_type": "number", "value": 60}
    elif mutation == "cross_table":
        table = deepcopy(snap.data_tables[0])
        table.update(id="table-second", table_id="table-second")
        snap.data_tables.append(table)
        req.scope.data_table_ids.append("table-second")
        raw["nodes"][0]["resource_ref"]["resource_id"] = "table-second"
    elif mutation == "forged_records":
        raw["nodes"][1]["inputs"][0].update(source_ref="input", source_port="user_input")
    else:
        raw["nodes"][0] = {"ref": "lookup", "kind": "json_deserialize", "title": "任意值不能伪造记录", "config": {"expected_schema": {"type": "any"}}, "inputs": [{"port": "json", "source_ref": "input", "source_port": "user_input"}]}
        for node in raw["nodes"]:
            for binding in node.get("inputs", []):
                if binding["source_ref"] == "lookup":
                    binding["source_port"] = "value"
    before = deepcopy(raw)
    with pytest.raises(ValueError):
        graph = lower_generation_recipe(raw, req, snap)
        compile_case(graph, snap, req)
    assert raw == before


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_score", [None, 42, 75], ids=["missing", "update", "no-write"])
async def test_recipe_compiled_paths_have_real_isolated_backend_effects(tmp_path, monkeypatch, initial_score):
    from server.tests import test_meta_planner_branch_effect_closeout as execution

    original_compile = execution.compile_case

    def compile_recipe(graph, snap, req):
        flow = [step("lookup"), step("found", {"matched": [step("stop")], "unmatched": [step("score_gate", {
            "matched": [step("write"), step("encode"), step("answer")], "unmatched": [step("unchanged")],
        })]})]
        return original_compile(lower_generation_recipe(from_intent(graph, flow), req, snap), snap, req)

    monkeypatch.setattr(execution, "compile_case", compile_recipe)
    await execution.test_branch_compiler_runner_effects_and_business_isolation(tmp_path, monkeypatch, initial_score)
