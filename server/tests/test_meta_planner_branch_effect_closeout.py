"""Compile and execute all guarded-write paths against a real isolated Backend."""
import pytest

import server.main as main_module
from server.data_tables.store import AgentTableStore
from server.evaluations.executor import XpertEvaluationExecutor
from server.evaluations.store import XpertEvaluationStore
from server.evaluations.write_fixtures import checksum, inspect_write_node
from server.meta_agent.capabilities import build_capability_snapshot
from server.meta_agent.graph_ir_v3 import (
    decompile_candidate_to_graph_intent, resolve_node_resource_snapshot, workflow_semantic_checksum,
)
from server.meta_agent.node_adapters import get_planner_node_adapter
from server.meta_agent.schemas import GraphIntentControlEdgeV3, GraphIntentNodeV3
from server.tests.test_controlled_write_execution_integration import _isolated_main_runtime
from server.tests.test_meta_planner_control_flow import _agent, _input
from server.tests.test_meta_planner_controlled_writes import compile_case, intent, request
from server.xpert_runtime.middleware_registry import runtime_middleware_registry
from server.xpert_runtime.run_registry import RunRegistry
from server.xpert_runtime.workflow_node_registry import workflow_node_registry


def branch_fixture(table, schema):
    snap = build_capability_snapshot(
        workflow_registry=workflow_node_registry, middleware_registry=runtime_middleware_registry,
        external_xperts=[], knowledge_bases=[], toolsets=[], plugins=[], prompt_profiles=[],
        model_ids=["model/planner", "model/agent"],
        data_tables=[{"table_id": table.table_id, "name": "离线合成表", "status": "published",
            "active_schema_version": 1, "schema_versions": [{"version": 1, "checksum": schema.checksum,
                "fields": [field.model_dump(mode="json") for field in schema.fields]}]}],
    )
    req = request(snap, "update")
    req.scope.data_table_ids = [table.table_id]
    req.scope.data_table_write_grants[0].table_id = table.table_id
    graph = intent("update")
    for node in graph.nodes:
        if node.resource_ref:
            node.resource_ref.resource_id = table.table_id
    query, writer, _, _ = graph.nodes
    query.config.update(return_mode="first", filter={"ref": "batch", "field": "sku", "operator": "eq", "value": "DEMO"})
    adapter = get_planner_node_adapter(query.kind)
    resource = resolve_node_resource_snapshot(query, snap)
    output = adapter.authoritative_output_schema("result", adapter.validate_intent_node(query), resource.model_dump(mode="json"))
    query.outputs[0].value_schema = writer.inputs[0].value_schema = output
    graph.nodes.extend([
        GraphIntentNodeV3(ref="found", kind="condition", title="检查记录存在",
            inputs=[_input("value", "rows", "lookup", schema=output)],
            config={"field": "", "operator": "is_null"}),
        GraphIntentNodeV3(ref="score_gate", kind="condition", title="检查数值阈值",
            inputs=[_input("value", "rows", "lookup", schema=output)],
            config={"field": "score", "operator": "lt", "value_type": "number", "value": 60}),
        GraphIntentNodeV3(ref="stop", kind="terminate_error", title="记录不存在",
            config={"error_code": "NOT_FOUND", "message": "记录不存在。"}),
        _agent("unchanged", variable="unchanged_result"),
    ])
    graph.control_edges = [GraphIntentControlEdgeV3(source_ref=source, outcome_ref=outcome, target_ref=target)
        for source, outcome, target in [
            ("lookup", "success", "found"), ("found", "matched", "stop"),
            ("found", "unmatched", "score_gate"), ("score_gate", "matched", "write"),
            ("score_gate", "unmatched", "unchanged"), ("write", "success", "encode"),
            ("encode", "success", "answer"),
        ]]
    graph.final_output.sources.append(type(graph.final_output.sources[0])(node_ref="unchanged"))
    candidate = compile_case(graph, snap, req)
    rebuilt = compile_case(decompile_candidate_to_graph_intent(candidate), snap, req)
    assert workflow_semantic_checksum(candidate) == workflow_semantic_checksum(rebuilt)
    return candidate["draft"]["workflow"]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_score", [None, 42, 75], ids=["missing", "update", "no-write"])
async def test_branch_compiler_runner_effects_and_business_isolation(tmp_path, monkeypatch, initial_score):
    business = AgentTableStore(tmp_path / "business-sentinel-only")
    table = business.create_table(name="离线分支效果表", fields=[
        {"name": "sku", "data_type": "string", "required": True},
        {"name": "score", "data_type": "number", "required": True},
    ])
    schema = business.publish_table(table.table_id, revision=table.draft_revision)
    sentinel = business.create_record_for_schema(table.table_id, schema_version=1,
        data={"sku": "BUSINESS_SENTINEL", "score": 999}, operation_id="seed-sentinel")
    workflow = branch_fixture(table, schema)
    contracts = [inspect_write_node(node, backend=business, nested=False) for node in workflow["nodes"]
                 if node["type"] == "data_table_update"]
    store = XpertEvaluationStore(tmp_path / "evaluation", agent_table_backend=business)
    dataset = store.create_dataset("离线编译分支效果证伪")
    seeds = [{"ref": "protected", "data": {"sku": "OTHER", "score": 100}}]
    if initial_score is not None:
        seeds.append({"ref": "target", "data": {"sku": "DEMO", "score": initial_score}})
    changed = initial_score is not None and initial_score < 60
    effect = {"node_ref": "write", "table_id": table.table_id, "operation": "update",
              "status": "applied" if changed else "not_executed", "affected_count": int(changed)}
    if changed:
        effect.update(expected_before={"score": initial_score}, expected_after={"score": 5})
    path = {"required_outcomes": ["found:matched" if initial_score is None else "found:unmatched"],
            "terminal": "error" if initial_score is None else "success"}
    if initial_score is None:
        path["error_code"] = "NOT_FOUND"
    else:
        path["required_outcomes"].append("score_gate:matched" if changed else "score_gate:unmatched")
    draft = store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[{
        "case_id": "branch", "message": "DEMO", "path": path, "effects": [effect],
        "table_initializations": [{"table_id": table.table_id, "schema_version": 1, "source": "synthetic", "records": seeds}],
    }])
    store.publish_dataset(dataset["dataset_id"], revision=draft["revision"])
    version = store.get_dataset_version(dataset["dataset_id"], 1)
    run = store.create_run(dataset_version=version, cases=version["cases"], baseline=None,
        candidates=[{"target_id": "candidate", "label": "真实编译分支", "workflow": workflow,
                     "resources": {"write_contracts": contracts}}],
        config={"budget": {"max_concurrency": 1}}, warnings=[], write_isolation=True)
    agent_calls = []

    async def fake_agent(*args, **kwargs):
        agent_calls.append(1)
        yield "离线模型占位结果，不作为写入证据。"

    monkeypatch.setattr(main_module, "agent_table_store", business)
    monkeypatch.setattr(main_module, "get_xpert_evaluation_store", lambda: store)
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("http://127.0.0.1:1", ""))
    monkeypatch.setattr(main_module, "stream_workflow_llm_text", fake_agent)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    executor = XpertEvaluationExecutor(store, target_runner=main_module.run_xpert_evaluation_target)
    await executor._execute_run(store.claim_next_run())
    item = store.require_run(run["run_id"])["items"][0]
    isolated = AgentTableStore(store.storage_dir / "write_instances" / checksum({"run_id": run["run_id"], "item_id": item["item_id"]}))
    assert business.query_records(table.table_id, schema_version=1) == [sentinel]
    assert item["status"] == "completed", item
    assert item["score"] == 1 and item["effect_evidence"] == "verified", item
    assert len(agent_calls) == int(initial_score is not None)
    actual = {row["sku"]: row["score"] for row in isolated.query_records(table.table_id, schema_version=1)}
    assert actual == {"OTHER": 100, **({"DEMO": 5 if changed else initial_score} if initial_score is not None else {})}
