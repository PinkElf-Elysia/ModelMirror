from copy import deepcopy

import pytest
from pydantic import ValidationError

import server.main as main_module
from server.data_tables.store import AgentTableStore
from server.evaluations.executor import XpertEvaluationExecutor
from server.evaluations.models import EvaluationCaseInput
from server.evaluations.store import XpertEvaluationStore
from server.evaluations.write_fixtures import checksum, inspect_write_node
from server.tests.test_controlled_write_execution_integration import (
    _full_write_workflow,
    _isolated_main_runtime,
    _node,
    _published_business_table,
)
from server.xpert_runtime.run_registry import RunRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("write_before_stop", [False, True], ids=["no-write", "prior-commit"])
@pytest.mark.parametrize("expectation", [
    "expected", "wrong-code", "missing-path", "wrong-outcome", "missing-semantic-ref", "wrong-effect", "false-conflict",
])
async def test_declared_error_path_preserves_isolated_effect_evidence(
    tmp_path, monkeypatch, write_before_stop, expectation,
):
    business, table_id, schema, sentinel = _published_business_table(tmp_path)
    store = XpertEvaluationStore(tmp_path / "evaluations", agent_table_backend=business)
    template = _full_write_workflow(table_id, schema)
    insert = deepcopy(template["nodes"][1])
    insert["data"]["plannerOutcomeMapV1"] = {"success": ""}
    router = _node("router", "condition", {
        "contractVersion": 2, "inputVariable": "user_input", "field": "",
        "operator": "equals", "valueType": "text", "value": "write",
        "plannerRef": "router",
        "plannerOutcomeMapV1": {"matched": "true", "unmatched": "false"},
    })
    stop = _node("stop", "terminate_error", {
        "errorCode": "EXPECTED_STOP", "message": "合成用例按约定安全终止。",
        "plannerRef": "stop", "plannerOutcomeMapV1": {},
    })
    if expectation == "missing-semantic-ref":
        stop["data"].pop("plannerRef")
        stop["data"].pop("plannerOutcomeMapV1")
    graph = {
        "id": "isolated-write-error-path", "title": "隔离写入错误路径证伪",
        "nodes": [template["nodes"][0], router, insert, stop],
        "edges": [
            {"id": "start", "source": "input", "target": "router"},
            {"id": "matched", "source": "router", "sourceHandle": "true", "target": "insert"},
            {"id": "unmatched", "source": "router", "sourceHandle": "false", "target": "stop"},
            {"id": "after-write", "source": "insert", "target": "stop"},
        ],
    }
    contracts = [inspect_write_node(insert, backend=business, nested=False)]
    dataset = store.create_dataset("安全终止分支证伪")
    effect = {
        "node_ref": "insert_row", "table_id": table_id, "operation": "insert",
        "status": "applied" if write_before_stop else "not_executed",
        "affected_count": int(write_before_stop),
    }
    if write_before_stop:
        effect["expected_after"] = {"title": "isolated-row", "status": "open"}
    if expectation == "wrong-effect":
        effect.update(status="not_executed" if write_before_stop else "applied", affected_count=int(not write_before_stop))
        effect.pop("expected_after", None)
    elif expectation == "false-conflict":
        effect.update(status="conflict", affected_count=0, error_code="EXPECTED_STOP")
        effect.pop("expected_after", None)
    case = {
        "case_id": "expected-stop", "message": "write" if write_before_stop else "missing",
        "path": {
            "required_outcomes": ["router:matched" if write_before_stop else "router:unmatched"],
            "forbidden_outcomes": ["router:unmatched" if write_before_stop else "router:matched"],
            "terminal": "error", "error_code": "EXPECTED_STOP",
        },
        "table_initializations": [{"table_id": table_id, "schema_version": schema.version, "source": "synthetic", "records": []}],
        "effects": [effect],
    }
    if expectation == "wrong-code":
        case["path"]["error_code"] = "UNEXPECTED_STOP"
    elif expectation == "missing-path":
        case.pop("path")
    elif expectation == "wrong-outcome":
        case["path"]["required_outcomes"], case["path"]["forbidden_outcomes"] = (
            case["path"]["forbidden_outcomes"], case["path"]["required_outcomes"],
        )
    draft = store.put_cases(dataset["dataset_id"], revision=dataset["revision"], cases=[case])
    store.publish_dataset(dataset["dataset_id"], revision=draft["revision"])
    version = store.get_dataset_version(dataset["dataset_id"], 1)
    run = store.create_run(
        dataset_version=version, cases=version["cases"], baseline=None,
        candidates=[{"target_id": "candidate", "label": "合成候选", "workflow": graph, "resources": {"write_contracts": contracts}}],
        config={"budget": {"max_concurrency": 1}}, warnings=[], write_isolation=True,
    )
    monkeypatch.setattr(main_module, "agent_table_store", business)
    monkeypatch.setattr(main_module, "get_xpert_evaluation_store", lambda: store)
    monkeypatch.setattr(main_module, "run_registry", RunRegistry())
    executor = XpertEvaluationExecutor(store, target_runner=main_module.run_xpert_evaluation_target)
    await executor._execute_run(store.claim_next_run())
    result = store.require_run(run["run_id"])
    item = result["items"][0]
    assert item["control_flow"]["supported"] == (expectation != "missing-semantic-ref"), item
    assert item["control_flow"]["error_code"] == "EXPECTED_STOP", item
    assert business.query_records(table_id, schema_version=schema.version) == [sentinel]
    directory = store.storage_dir / "write_instances" / checksum({"run_id": run["run_id"], "item_id": item["item_id"]})
    actual = AgentTableStore(directory).query_records(table_id, schema_version=schema.version)
    assert len(actual) == int(write_before_stop)
    if expectation in {"wrong-code", "missing-path", "missing-semantic-ref"}:
        assert item["status"] == "failed", item
        assert item["score"] == 0, item
        assert all(not metric["passed"] for metric in item["metrics"])
        return
    assert item["status"] == "completed", item
    assert {metric["kind"] for metric in item["metrics"]} == {"workflow_path_match", "workflow_effect_match"}
    metrics = {metric["kind"]: metric for metric in item["metrics"]}
    if expectation == "wrong-outcome":
        assert not metrics["workflow_path_match"]["passed"]
        assert metrics["workflow_effect_match"]["passed"]
        assert item["score"] < 1
    elif expectation in {"wrong-effect", "false-conflict"}:
        assert metrics["workflow_path_match"]["passed"]
        assert not metrics["workflow_effect_match"]["passed"]
        assert item["effect_evidence"] == "failed"
        assert item["score"] == 0
    else:
        assert item["effect_evidence"] == "verified", item
        assert item["score"] == 1, item
        assert all(metric["passed"] for metric in item["metrics"])


@pytest.mark.parametrize("error_code", [None, ""])
def test_error_case_requires_nonempty_safe_error_code(error_code):
    with pytest.raises(ValidationError, match="safe error_code"):
        EvaluationCaseInput.model_validate({
            "message": "安全终止用例",
            "path": {"terminal": "error", "error_code": error_code},
        })
