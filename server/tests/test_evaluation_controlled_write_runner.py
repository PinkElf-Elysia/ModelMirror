from copy import deepcopy
import json

import pytest

import server.main as main_module
from server.data_tables.store import AgentTableStore
from server.evaluations.executor import XpertEvaluationExecutor
from server.evaluations.store import XpertEvaluationStore
from server.evaluations.write_fixtures import checksum, inspect_write_node
from server.tests.test_controlled_write_execution_integration import (
    _full_write_workflow,
    _isolated_main_runtime,
    _published_business_table,
)
from server.xpert_runtime.run_registry import RunRegistry


def evaluation(tmp_path, monkeypatch, *, baseline=False, readonly_baseline=False, initial_records=None):
    business, table_id, schema, sentinel = _published_business_table(tmp_path)
    store = XpertEvaluationStore(tmp_path / "evaluations", agent_table_backend=business)
    graph = _full_write_workflow(table_id, schema)
    contracts = [
        inspect_write_node(node, backend=business, nested=False)
        for node in graph["nodes"]
        if node["type"] in {"data_table_insert", "data_table_update", "data_table_delete"}
    ]
    candidate = {"target_id": "candidate", "label": "合成候选", "workflow": graph, "resources": {"write_contracts": contracts}}
    dataset = store.create_dataset("真实隔离效果闭环")
    effects = [
        {"node_ref": "insert_row", "table_id": table_id, "operation": "insert", "affected_count": 1, "expected_after": {"title": "isolated-row", "status": "open"}},
        {"node_ref": "update_row", "table_id": table_id, "operation": "update", "affected_count": 1, "expected_before": {"status": "open"}, "expected_after": {"status": "closed"}},
        {"node_ref": "delete_row", "table_id": table_id, "operation": "delete", "affected_count": 1, "expected_before": {"status": "closed"}},
    ]
    draft = store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[{
        "case_id": "write-sequence", "message": "仅执行合成数据验证",
        "table_initializations": [{"table_id": table_id, "schema_version": schema.version, "source": "synthetic", "records": deepcopy(initial_records or [])}],
        "effects": effects,
    }])
    store.publish_dataset(dataset["dataset_id"], revision=draft["revision"])
    version = store.get_dataset_version(dataset["dataset_id"], 1)
    original = {**deepcopy(candidate), "target_id": "baseline", "label": "合成基线"} if baseline else None
    if readonly_baseline:
        query = deepcopy(next(node for node in graph["nodes"] if node["id"] == "query_after_insert"))
        query["data"]["filter"] = None
        original = {
            "target_id": "baseline", "label": "只读合成基线", "resources": {"write_contracts": []},
            "workflow": {
                "id": "readonly-baseline", "title": "只读基线",
                "nodes": [deepcopy(graph["nodes"][0]), query, {"id": "output", "type": "output", "position": {"x": 0, "y": 0}, "data": {"kind": "output", "outputVariable": query["data"]["outputVariable"]}}],
                "edges": [{"id": "read", "source": "input", "target": query["id"]}, {"id": "finish", "source": query["id"], "target": "output"}],
            },
        }
    run = store.create_run(
        dataset_version=version, cases=version["cases"],
        baseline=original,
        candidates=[candidate], config={"budget": {"max_concurrency": 1}}, warnings=[], write_isolation=True,
    )
    monkeypatch.setattr(main_module, "agent_table_store", business)
    monkeypatch.setattr(main_module, "get_xpert_evaluation_store", lambda: store)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    executor = XpertEvaluationExecutor(store, target_runner=main_module.run_xpert_evaluation_target)
    return business, table_id, schema, sentinel, store, executor, run


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_records", [
    [],
    [{"ref": "non_target", "data": {"title": "isolated-sentinel", "status": "protected"}}],
], ids=["empty", "non-target-sentinel"])
async def test_evaluator_scores_real_backend_effects_and_never_exposes_records(tmp_path, monkeypatch, initial_records):
    business, table_id, schema, sentinel, store, executor, run = evaluation(
        tmp_path, monkeypatch, baseline=True, initial_records=initial_records,
    )
    await executor._execute_run(store.claim_next_run())
    completed = store.require_run(run["run_id"])
    assert completed["status"] == "completed"
    assert len(completed["items"]) == 2
    for item in completed["items"]:
        assert item["status"] == "completed", item
        assert item["effect_evidence"] == "verified", item
        assert item["score"] == 1
        assert [effect["operation"] for effect in item["write_effects"]] == ["insert", "update", "delete"]
        assert item["partial_completion"] == {"committed_nodes": 3, "affected_rows": 3, "rolled_back": False}
        for effect in item["write_effects"]:
            assert effect["untouched_before_checksum"] == effect["untouched_after_checksum"]
        directory = store.storage_dir / "write_instances" / checksum({
            "run_id": run["run_id"], "item_id": item["item_id"],
        })
        assert (directory / "agent_tables.sqlite3").is_file()
        isolated = AgentTableStore(directory)
        remaining = isolated.query_records(table_id, schema_version=schema.version)
        assert [
            {field.name: record[field.name] for field in schema.fields}
            for record in remaining
        ] == [seed["data"] for seed in initial_records]
        assert all(record["revision"] == 1 for record in remaining)
    public = json.dumps(store.run_payload(completed, include_detail=True), ensure_ascii=False)
    assert "before_records" not in public and "after_records" not in public
    assert "table_initializations" not in public and "expected_after" not in public
    assert "business-sentinel" not in public
    assert "isolated-sentinel" not in public
    assert business.query_records(table_id, schema_version=schema.version) == [sentinel]
    restored = XpertEvaluationStore(store.storage_dir)
    assert restored.recover_runs() == 0
    assert restored.claim_next_run() is None


@pytest.mark.asyncio
async def test_cancel_after_first_commit_blocks_later_node_dispatch(tmp_path, monkeypatch):
    business, table_id, schema, sentinel, store, executor, run = evaluation(tmp_path, monkeypatch)
    record_receipt = store.record_write_receipt

    def cancel_after_insert(run_id, item_id, receipt):
        record_receipt(run_id, item_id, receipt)
        if receipt["operation"] == "insert":
            store.cancel_run(run_id)

    monkeypatch.setattr(store, "record_write_receipt", cancel_after_insert)
    await executor._execute_run(store.claim_next_run())
    completed = store.require_run(run["run_id"])
    assert completed["status"] == "cancelled"
    item = completed["items"][0]
    assert [receipt["operation"] for receipt in item["write_receipts"]] == ["insert"]
    assert item["partial_completion"] == {"committed_nodes": 1, "affected_rows": 1, "rolled_back": False}
    assert item["score"] == 0
    assert business.query_records(table_id, schema_version=schema.version) == [sentinel]


@pytest.mark.asyncio
async def test_readonly_baseline_shares_initialization_not_candidate_state_or_live_table(tmp_path, monkeypatch):
    business, table_id, schema, sentinel, store, executor, run = evaluation(tmp_path, monkeypatch, readonly_baseline=True)
    await executor._execute_run(store.claim_next_run())
    completed = store.require_run(run["run_id"])
    assert completed["status"] == "completed"
    baseline = next(item for item in completed["items"] if item["target_id"] == "baseline")
    candidate = next(item for item in completed["items"] if item["target_id"] == "candidate")
    assert baseline["status"] == "completed", baseline
    assert baseline["write_effects"] == []
    assert baseline["resource_reads"][0]["result_count"] == 0
    assert baseline["resource_reads"][0]["record_ids"] == []
    assert baseline["effect_evidence"] != "verified"
    assert candidate["effect_evidence"] == "verified" and candidate["score"] == 1
    assert business.query_records(table_id, schema_version=schema.version) == [sentinel]
