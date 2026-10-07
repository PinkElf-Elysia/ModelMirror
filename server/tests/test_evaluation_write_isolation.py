from copy import deepcopy
import json

import pytest

from server.data_tables.store import AgentTableStore
from server.evaluations.store import EvaluationStateError, XpertEvaluationStore
from server.evaluations.api import _sanitize_run_detail
from server.evaluations.write_fixtures import (
    checksum, create_isolated_write_context as create_fixed_context, freeze_case_tables,
    inspect_write_node, validate_write_dataset,
)
from server.evaluations.resource_fixtures import _resolve_filter, isolated_query_evidence
from server.workflow_native.controlled_writes import ControlledWriteRuntime
from server.xpert_runtime.execution_store import WorkflowExecutionStore
from server.tests.test_controlled_write_runtime import setup_table, workflow, seed


def create_isolated_write_context(store, *, run_id, item_id):
    run = store.require_run(run_id)
    item = next(item for item in run["items"] if item["item_id"] == item_id)
    target = next(target for target in run["targets"] if target["target_id"] == item["target_id"])
    case = next(case for case in run["dataset"]["cases"] if case["case_id"] == item["case_id"])
    return create_fixed_context(store, run_id=run_id, item_id=item_id, target=target, case=case)


def published(tmp_path, *, operation="update", seed_count=1):
    business, table_id, schema = setup_table(tmp_path)
    sentinel = seed(business, table_id, schema, "business-sentinel")
    store = XpertEvaluationStore(tmp_path / "evaluations", agent_table_backend=business)
    graph = workflow(table_id, schema, operation)
    node = next(node for node in graph["nodes"] if node["id"] == "write")
    contract = inspect_write_node(node, backend=business, nested=False)
    target = {"target_id": "candidate", "label": "候选", "workflow": graph, "resources": {"write_contracts": [contract]}}
    dataset = store.create_dataset("合成写入验证")
    draft = store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[{
        "case_id": "case-one", "message": "仅处理合成测试记录",
        "table_initializations": [{"table_id": table_id, "schema_version": schema.version, "source": "synthetic", "records": [{"ref": f"seed{index}", "data": {"title": "first" if index == 0 else f"other-{index}"}} for index in range(seed_count)]}],
        "effects": [{"node_ref": "write", "table_id": table_id, "operation": operation, "affected_count": 1, "expected_after": {"status": "closed"} if operation == "update" else {}}],
    }])
    store.publish_dataset(dataset["dataset_id"], revision=draft["revision"])
    version = store.get_dataset_version(dataset["dataset_id"], 1)
    return business, table_id, schema, sentinel, store, target, version


def create_run(store, target, version, *, baseline=False):
    original = {**deepcopy(target), "target_id": "baseline", "label": "基线"} if baseline else None
    return store.create_run(dataset_version=version, cases=version["cases"], baseline=original, candidates=[target], config={"budget": {"repetitions": 1}}, warnings=[], write_isolation=True)


def controller(tmp_path, context, target):
    execution = WorkflowExecutionStore(tmp_path)
    task_id = context.item_id
    execution.create(task_id=task_id, run_id=context.run_id, run_type="xpert_evaluation", workflow=target["workflow"], inputs={})
    return ControlledWriteRuntime(task_id=task_id, workflow=target["workflow"], backend=context.backend, execution_store=execution, isolated=True, receipt_callback=context.receipt_callback, dispatch_guard=context.dispatch_guard)


def test_published_initialization_is_private_fixed_and_never_copies_business(tmp_path):
    business, table_id, _, sentinel, store, target, version = published(tmp_path)
    fixed = version["_write_fixtures"]
    assert "business-sentinel" not in json.dumps(fixed)
    assert len(fixed["case-one"]["tables"][0]["records"]) == 1
    run = create_run(store, target, version)
    assert "_write_fixtures" not in run
    assert "records" not in run["dataset"]["cases"][0]["table_initialization_summary"][0]
    assert "expected_after" not in run["dataset"]["cases"][0]["effects"][0]
    assert business.query_records(table_id, schema_version=1) == [sentinel]


def test_api_final_projection_rejects_raw_private_run_bodies(tmp_path):
    _, _, _, _, store, target, version = published(tmp_path)
    run = create_run(store, target, version)
    raw = store.require_run(run["run_id"])
    secret = "合成私有正文不得出现在报告"
    raw["items"][0].update(output=secret, private_effect={"before_records": [secret]}, _private={"body": secret}, write_effects=[{"node_ref": "write", "status": "applied", "receipt": {"data": secret}, "output": secret, "private_effect": {"after_records": [secret]}}])
    raw["report"] = {"_private": {"data": secret}}
    public = _sanitize_run_detail(raw)
    text = json.dumps(public, ensure_ascii=False)
    for forbidden in (secret, "_write_fixtures", "private_effect", "table_initializations", "expected_after", "before_records", "after_records"):
        assert forbidden not in text
    assert public["items"][0]["write_effects"] == [{"node_ref": "write", "status": "applied"}]
    assert raw["items"][0]["output"] == secret


def test_baseline_candidate_use_independent_backends_and_write_read_real_state(tmp_path):
    business, table_id, _, sentinel, store, target, version = published(tmp_path)
    graph = target["workflow"]
    query_after = deepcopy(graph["nodes"][1])
    query_after["id"] = "after"
    query_after["data"].update(plannerRef="after", outputVariable="after_rows")
    graph["nodes"].insert(-1, query_after)
    graph["edges"][-1]["target"] = "after"
    graph["edges"].append({"id": "after-output", "source": "after", "target": "output"})
    run = create_run(store, target, version, baseline=True)
    contexts = [create_isolated_write_context(store, run_id=run["run_id"], item_id=item["item_id"]) for item in run["items"]]
    before = [context.backend.query_records(table_id, schema_version=1) for context in contexts]
    assert before[0][0]["record_id"] != before[1][0]["record_id"]
    assert before[0][0]["status"] == before[1][0]["status"] == "open"
    runner = controller(tmp_path / "executions", contexts[0], target)
    rows, _ = runner.query("query", filter_tree=None)
    runner.execute("write", {"rows": rows}, resolve_filter=_resolve_filter)
    after, _ = runner.query("after", filter_tree=None)
    assert after[0]["status"] == "closed" and after[0]["revision"] == 2
    evidence = isolated_query_evidence(query_after, variables={}, backend=contexts[0].backend, records=after)
    assert evidence["record_ids"] == [after[0]["record_id"]]
    assert "status" not in evidence and len(evidence["query_checksum"]) == 64
    assert contexts[1].backend.query_records(table_id, schema_version=1)[0]["status"] == "open"
    assert business.query_records(table_id, schema_version=1) == [sentinel]


def test_restart_uses_same_instance_and_ledger_not_fresh_seed(tmp_path):
    _, table_id, _, _, store, target, version = published(tmp_path, operation="insert", seed_count=0)
    run = create_run(store, target, version)
    item_id = run["items"][0]["item_id"]
    context = create_isolated_write_context(store, run_id=run["run_id"], item_id=item_id)
    original = controller(tmp_path / "executions", context, target).execute("write", {}, resolve_filter=_resolve_filter)
    restored = XpertEvaluationStore(store.storage_dir)
    recovered = create_isolated_write_context(restored, run_id=run["run_id"], item_id=item_id)
    replay = controller(tmp_path / "executions", recovered, target).execute("write", {}, resolve_filter=_resolve_filter)
    assert replay["replayed"] and replay["output"] == original["output"]
    assert len(recovered.backend.query_records(table_id, schema_version=1)) == 1
    assert len(restored.require_run(run["run_id"])["items"][0]["write_receipts"]) == 1


def test_ready_instance_missing_marker_fails_closed(tmp_path, monkeypatch):
    _, _, _, _, store, target, version = published(tmp_path)
    run = create_run(store, target, version)
    item_id = run["items"][0]["item_id"]
    create_isolated_write_context(store, run_id=run["run_id"], item_id=item_id)
    class MissingBackend(AgentTableStore):
        def __init__(self, storage_dir):
            super().__init__(tmp_path / "empty-replacement")
    monkeypatch.setattr("server.data_tables.store.AgentTableStore", MissingBackend)
    with pytest.raises(Exception, match="initialization|初始化"):
        create_isolated_write_context(store, run_id=run["run_id"], item_id=item_id)


def test_fixture_tamper_changed_case_and_disabled_isolation_rejected(tmp_path):
    _, _, _, _, store, target, version = published(tmp_path)
    corrupt = deepcopy(version)
    corrupt["_write_fixtures"]["case-one"]["tables"][0]["records"][0]["data"]["title"] = "changed"
    with pytest.raises(ValueError, match="校验和"):
        validate_write_dataset([target], corrupt["cases"], dataset=corrupt)
    changed_case = deepcopy(version["cases"])
    changed_case[0]["effects"][0]["affected_count"] = 0
    with pytest.raises(EvaluationStateError, match="已发布版本"):
        store.create_run(dataset_version=version, cases=changed_case, baseline=None, candidates=[target], config={}, warnings=[], write_isolation=True)
    with pytest.raises(EvaluationStateError, match="隔离执行环境"):
        store.create_run(dataset_version=version, cases=version["cases"], baseline=None, candidates=[target], config={}, warnings=[])


def test_per_table_200_rows_is_not_global_200(tmp_path):
    business, table_id, schema, _, _, _, _ = published(tmp_path)
    second = business.create_table(name="第二张合成表", fields=[{"name": "title", "data_type": "string"}])
    second_schema = business.publish_table(second.table_id, revision=second.draft_revision)
    tables = [{"table_id": identifier, "schema_version": version, "records": [{"ref": f"r{index}", "data": {"title": f"row-{index}"}} for index in range(200)]} for identifier, version in [(table_id, schema.version), (second.table_id, second_schema.version)]]
    fixed = freeze_case_tables([{"case_id": "multi", "table_initializations": tables}], business)["multi"]
    isolated = AgentTableStore(tmp_path / "isolated")
    isolated.initialize_evaluation_tables(fixed["tables"], initialization_checksum=fixed["checksum"])
    assert len(isolated.query_records(table_id, schema_version=1, limit=200)) == 200
    assert len(isolated.query_records(second.table_id, schema_version=1, limit=200)) == 200


def test_cancelled_and_completed_items_cannot_dispatch(tmp_path):
    _, _, _, _, store, target, version = published(tmp_path)
    run = create_run(store, target, version)
    store.record_item_result(run["run_id"], run["items"][0]["item_id"], result={"status": "completed", "score": 1})
    with pytest.raises(ValueError, match="不可派发"):
        create_isolated_write_context(store, run_id=run["run_id"], item_id=run["items"][0]["item_id"])


def test_cross_target_or_case_context_cannot_reuse_another_instance(tmp_path):
    _, _, _, _, store, target, version = published(tmp_path)
    run = create_run(store, target, version, baseline=True)
    with pytest.raises(ValueError, match="禁止交叉复用"):
        create_fixed_context(store, run_id=run["run_id"], item_id=run["items"][0]["item_id"], target=target, case=version["cases"][0])
    changed_case = {**version["cases"][0], "message": "替换输入"}
    with pytest.raises(ValueError, match="禁止交叉复用"):
        create_fixed_context(store, run_id=run["run_id"], item_id=run["items"][1]["item_id"], target=target, case=changed_case)
    assert all("write_instance" not in item for item in store.require_run(run["run_id"])["items"])


def test_contract_checksum_and_node_identity_cannot_be_forged(tmp_path):
    _, _, _, _, store, target, version = published(tmp_path)
    target["resources"]["write_contracts"][0]["contract_checksum"] = "b" * 64
    with pytest.raises(ValueError, match="固定节点配置"):
        validate_write_dataset([target], version["cases"], dataset=version)


def test_write_dispatch_fence_save_failure_blocks_backend_and_can_resume(tmp_path, monkeypatch):
    _, table_id, _, _, store, target, version = published(tmp_path, operation="insert", seed_count=0)
    run = create_run(store, target, version)
    item_id = run["items"][0]["item_id"]
    context = create_isolated_write_context(store, run_id=run["run_id"], item_id=item_id)
    runner = controller(tmp_path / "executions", context, target)
    save = store._save_unlocked

    def refuse_save():
        raise OSError("合成持久化失败")

    monkeypatch.setattr(store, "_save_unlocked", refuse_save)
    with pytest.raises(OSError, match="持久化失败"):
        runner.execute("write", {}, resolve_filter=_resolve_filter)
    assert not store.require_run(run["run_id"])["items"][0].get("write_dispatches")
    assert context.backend.query_records(table_id, schema_version=1) == []
    monkeypatch.setattr(store, "_save_unlocked", save)
    result = runner.execute("write", {}, resolve_filter=_resolve_filter)
    assert result["receipt"]["affected_count"] == 1
    persisted = XpertEvaluationStore(store.storage_dir).require_run(run["run_id"])
    assert persisted["items"][0]["write_dispatches"]["write"]["request_checksum"] == result["receipt"]["request_checksum"]


def test_cancelled_runtime_may_read_committed_ledger_but_not_dispatch_again(tmp_path):
    _, table_id, _, _, store, target, version = published(tmp_path, operation="insert", seed_count=0)
    run = create_run(store, target, version)
    item_id = run["items"][0]["item_id"]
    context = create_isolated_write_context(store, run_id=run["run_id"], item_id=item_id)
    first = controller(tmp_path / "executions", context, target).execute("write", {}, resolve_filter=_resolve_filter)
    store.cancel_run(run["run_id"])
    replay = controller(tmp_path / "executions", context, target).execute("write", {}, resolve_filter=_resolve_filter)
    assert replay["replayed"] and replay["output"] == first["output"]
    assert len(context.backend.query_records(table_id, schema_version=1)) == 1
