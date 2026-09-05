from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import server.main as main_module
from server.data_tables.store import AgentTableStore
from server.evaluations.metrics import evaluate_case_metrics
from server.evaluations.api import _sanitize_run_detail
from server.evaluations.resource_fixtures import prepare_agent_table_fixtures
from server.evaluations.service import XpertEvaluationService
from server.evaluations.store import EvaluationStateError, XpertEvaluationStore
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.xperts import XpertStore


class _EmptyStore:
    def get_version(self, *_args: Any, **_kwargs: Any) -> Any:
        raise KeyError("resource not found")


class _RagStub:
    def get_active_pipeline_version(self, kb_id: str) -> dict[str, Any] | None:
        if kb_id == "kb-ready":
            return {"version_id": "pipeline-fixed"}
        return None


def _published_table(tmp_path: Path):
    store = AgentTableStore(tmp_path / "tables")
    table = store.create_table(
        name="Tasks",
        fields=[
            {"name": "name", "data_type": "string", "required": True},
            {"name": "priority", "data_type": "integer", "required": True},
        ],
    )
    schema = store.publish_table(table.table_id, revision=table.draft_revision)
    first = store.create_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"name": "Review", "priority": 5},
        operation_id="seed-review",
    )
    return store, table, schema, first


def _table_target(table_id: str, schema_version: int) -> dict[str, Any]:
    return {
        "target_id": "candidate",
        "label": "Candidate",
        "input_variable": "user_input",
        "history_variable": "conversation_history",
        "workflow": {
            "id": "fixture-workflow",
            "title": "Fixture workflow",
            "nodes": [
                {
                    "id": "query-native",
                    "type": "data_table_query",
                    "data": {
                        "kind": "data_table_query",
                        "plannerRef": "task_lookup",
                        "tableId": table_id,
                        "versionPolicy": "pinned",
                        "pinnedSchemaVersion": schema_version,
                        "selectFields": ["name", "priority"],
                        "filter": {
                            "field": "name",
                            "operator": "contains",
                            "value": {
                                "source": "variable",
                                "variable": "user_input",
                            },
                        },
                        "sort": [{"field": "priority", "direction": "desc"}],
                        "limit": 20,
                        "returnMode": "list",
                        "outputVariable": "records",
                    },
                }
            ],
            "edges": [],
        },
    }


def _case() -> dict[str, Any]:
    return {"case_id": "case-review", "message": "Review", "expected": {}}


def test_table_fixtures_survive_reload_and_do_not_follow_live_rows(
    tmp_path: Path,
) -> None:
    table_store, table, schema, original = _published_table(tmp_path)
    target = _table_target(table.table_id, schema.version)
    case = _case()
    fixtures = prepare_agent_table_fixtures(
        targets=[target],
        cases=[case],
        backend=table_store,
    )
    assert fixtures[0]["record_ids"] == [original["record_id"]]

    evaluation_store = XpertEvaluationStore(tmp_path / "evaluations")
    run = evaluation_store.create_run(
        dataset_version={
            "dataset_id": "dataset",
            "version": 1,
            "cases": [case],
        },
        cases=[case],
        baseline=None,
        candidates=[target],
        config={"budget": {"repetitions": 1}},
        warnings=[],
        resource_fixtures=fixtures,
    )
    assert "_resource_fixtures" not in run
    table_store.create_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"name": "Review later", "priority": 9},
        operation_id="seed-later",
    )

    restored = XpertEvaluationStore(tmp_path / "evaluations")
    frozen = restored.resource_fixtures_for_item(
        run["run_id"],
        target_id="candidate",
        case_id="case-review",
    )
    assert len(frozen) == 1
    assert frozen[0]["record_ids"] == [original["record_id"]]
    assert len(frozen[0]["records"]) == 1
    assert "_resource_fixtures" not in restored.run_payload(
        restored.require_run(run["run_id"]),
        include_detail=True,
    )


def test_persisted_table_fixture_tampering_fails_closed(tmp_path: Path) -> None:
    table_store, table, schema, _record = _published_table(tmp_path)
    target = _table_target(table.table_id, schema.version)
    case = _case()
    fixtures = prepare_agent_table_fixtures(
        targets=[target], cases=[case], backend=table_store
    )
    evaluation_store = XpertEvaluationStore(tmp_path / "evaluations")
    run = evaluation_store.create_run(
        dataset_version={"dataset_id": "dataset", "version": 1, "cases": [case]},
        cases=[case],
        baseline=None,
        candidates=[target],
        config={"budget": {"repetitions": 1}},
        warnings=[],
        resource_fixtures=fixtures,
    )

    raw = json.loads(evaluation_store.path.read_text(encoding="utf-8"))
    raw["runs"][run["run_id"]]["_resource_fixtures"][0]["records"][0][
        "priority"
    ] = 999
    evaluation_store.path.write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )

    restored = XpertEvaluationStore(tmp_path / "evaluations")
    with pytest.raises(EvaluationStateError, match="integrity"):
        restored.resource_fixtures_for_item(
            run["run_id"], target_id="candidate", case_id="case-review"
        )


def test_run_create_cancel_and_api_projection_never_expose_private_fixtures(
    tmp_path: Path,
) -> None:
    table_store, table, schema, _record = _published_table(tmp_path)
    target = _table_target(table.table_id, schema.version)
    case = _case()
    fixtures = prepare_agent_table_fixtures(
        targets=[target], cases=[case], backend=table_store
    )
    store = XpertEvaluationStore(tmp_path / "evaluations")
    run = store.create_run(
        dataset_version={"dataset_id": "dataset", "version": 1, "cases": [case]},
        cases=[case],
        baseline=None,
        candidates=[target],
        config={"budget": {"repetitions": 1}},
        warnings=[],
        resource_fixtures=fixtures,
    )

    assert "_resource_fixtures" not in run
    assert "_resource_fixtures" not in store.cancel_run(run["run_id"])
    raw = store.require_run(run["run_id"])
    assert "_resource_fixtures" in raw
    assert "_resource_fixtures" not in _sanitize_run_detail(raw)


def test_fixture_capture_uses_the_same_prompt_profile_input_as_runtime(
    tmp_path: Path,
) -> None:
    table_store, table, schema, _record = _published_table(tmp_path)
    rendered = table_store.create_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"name": "Ticket Review", "priority": 8},
        operation_id="seed-rendered",
    )
    target = _table_target(table.table_id, schema.version)
    target["input_template"] = "Ticket {{args}}"

    fixtures = prepare_agent_table_fixtures(
        targets=[target], cases=[_case()], backend=table_store
    )

    assert fixtures[0]["filter"]["value"] == "Ticket Review"
    assert fixtures[0]["record_ids"] == [rendered["record_id"]]


def test_agent_derived_table_filter_is_rejected_before_fixture_capture(
    tmp_path: Path,
) -> None:
    table_store, table, schema, _record = _published_table(tmp_path)
    target = _table_target(table.table_id, schema.version)
    target["workflow"]["nodes"].insert(
        0,
        {
            "id": "agent-native",
            "type": "workflow_agent",
            "data": {
                "kind": "workflow_agent",
                "plannerRef": "agent_decision",
                "outputVariable": "agent_filter",
            },
        },
    )
    target["workflow"]["nodes"][1]["data"]["filter"]["value"][
        "variable"
    ] = "agent_filter"

    with pytest.raises(EvaluationStateError, match="prior Agent output"):
        prepare_agent_table_fixtures(
            targets=[target], cases=[_case()], backend=table_store
        )


def test_knowledge_retrieval_preflight_fixes_the_actual_index_version(
    tmp_path: Path,
) -> None:
    service = XpertEvaluationService(
        XpertEvaluationStore(tmp_path / "evaluations"),
        xpert_store=XpertStore(tmp_path / "xperts"),
        proposal_store=_EmptyStore(),
        prompt_preflight=lambda _xpert: None,
        toolset_store=_EmptyStore(),
        plugin_store=_EmptyStore(),
        rag_service=_RagStub(),
        context_store=_EmptyStore(),
    )
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "id": "knowledge-evaluation",
            "title": "Knowledge evaluation",
            "nodes": [
                {
                    "id": "lookup",
                    "type": "knowledge_retrieval",
                    "data": {
                        "kind": "knowledge_retrieval",
                        "plannerRef": "kb_lookup",
                        "knowledgeBaseId": "kb-ready",
                        "observedActiveVersionId": "pipeline-old",
                        "queryVariable": "user_input",
                        "outputVariable": "knowledge",
                    },
                }
            ],
            "edges": [],
        }
    )

    issues, warnings, resources = service._safe_preflight(
        workflow, recursion_path=("root",)
    )
    assert not issues
    assert any("changed" in warning for warning in warnings)
    assert workflow.nodes[0].data["evaluationPinnedVersionId"] == "pipeline-fixed"
    assert resources["knowledge_versions"] == [
        {
            "knowledge_base_id": "kb-ready",
            "version_id": "pipeline-fixed",
            "node_id": "lookup",
            "node_kind": "knowledge_retrieval",
        }
    ]


@pytest.mark.asyncio
async def test_resource_metric_distinguishes_verified_missing_and_failed() -> None:
    case = {
        "message": "lookup",
        "expected": {},
        "resource_reads": [
            {
                "node_ref": "task_lookup",
                "kind": "data_table_query",
                "resource_id": "table-1",
                "schema_version": 2,
                "query_checksum": "a" * 64,
                "expected_count": 1,
                "record_ids": ["record-1"],
            }
        ],
        "weights": {"workflow_resource_match": 1},
    }
    verified = await evaluate_case_metrics(
        case=case,
        output="done",
        citations={},
        resource_reads=[
            {
                "node_ref": "task_lookup",
                "kind": "data_table_query",
                "resource_id": "table-1",
                "schema_version": 2,
                "query_checksum": "a" * 64,
                "result_count": 1,
                "record_ids": ["record-1"],
            }
        ],
    )
    missing = await evaluate_case_metrics(
        case={"message": "lookup", "expected": {}},
        output="done",
        citations={},
        resource_reads=[],
        resource_evidence_required=True,
    )
    failed = await evaluate_case_metrics(
        case=case,
        output="done",
        citations={},
        resource_reads=[],
    )

    assert verified["resource_evidence"] == "verified"
    assert verified["metrics"][0]["kind"] == "workflow_resource_match"
    assert verified["metrics"][0]["passed"] is True
    assert missing["resource_evidence"] == "missing"
    assert failed["resource_evidence"] == "failed"


def test_resource_fixture_size_limit_is_checked_before_run_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_store, table, schema, _record = _published_table(tmp_path)
    target = _table_target(table.table_id, schema.version)
    case = _case()
    fixtures = prepare_agent_table_fixtures(
        targets=[target], cases=[case], backend=table_store
    )
    store = XpertEvaluationStore(tmp_path / "evaluations")
    monkeypatch.setattr(XpertEvaluationStore, "MAX_RESOURCE_FIXTURE_BYTES", 1)

    with pytest.raises(EvaluationStateError, match="16 MiB"):
        store.create_run(
            dataset_version={"dataset_id": "dataset", "version": 1, "cases": [case]},
            cases=[case],
            baseline=None,
            candidates=[target],
            config={"budget": {"repetitions": 1}},
            warnings=[],
            resource_fixtures=fixtures,
        )
    assert store.list_runs() == []


@pytest.mark.asyncio
async def test_real_evaluation_runner_returns_fixed_table_resource_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_store, table, schema, _original = _published_table(tmp_path)
    rendered = table_store.create_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"name": "Ticket Review", "priority": 8},
        operation_id="seed-runner-rendered",
    )
    target = _table_target(table.table_id, schema.version)
    target.update(
        {
            "source": {"kind": "xpert_version", "version": 1},
            "xpert": {"id": "xpert-test", "slug": "test", "name": "Test"},
            "output_variable": "records",
            "agent_config": {},
            "checksum": "fixture-target",
            "input_template": "Ticket {{args}}",
        }
    )
    target["workflow"]["nodes"].append(
        {
            "id": "output-native",
            "type": "output",
            "data": {"kind": "output", "outputVariable": "records"},
        }
    )
    target["workflow"]["edges"] = [
        {"id": "query-output", "source": "query-native", "target": "output-native"}
    ]
    case = _case()
    fixtures = prepare_agent_table_fixtures(
        targets=[target], cases=[case], backend=table_store
    )
    evaluation_store = XpertEvaluationStore(tmp_path / "evaluations")
    run = evaluation_store.create_run(
        dataset_version={"dataset_id": "dataset", "version": 1, "cases": [case]},
        cases=[case],
        baseline=None,
        candidates=[target],
        config={"budget": {"repetitions": 1}},
        warnings=[],
        resource_fixtures=fixtures,
    )
    monkeypatch.setattr(main_module, "agent_table_store", table_store)
    monkeypatch.setattr(
        main_module, "get_xpert_evaluation_store", lambda: evaluation_store
    )
    previous_task_ids = set(main_module.workflow_task_store)
    try:
        result = await main_module.run_xpert_evaluation_target(
            target,
            case,
            {
                "evaluation_run_id": run["run_id"],
                "evaluation_target_id": target["target_id"],
                "evaluation_case_id": case["case_id"],
                "budget": {},
            },
            None,
        )
    finally:
        for task_id in set(main_module.workflow_task_store) - previous_task_ids:
            main_module.workflow_task_store.pop(task_id, None)

    assert result["resource_reads"] == [
        {
            "node_ref": "task_lookup",
            "kind": "data_table_query",
            "resource_id": table.table_id,
            "schema_version": schema.version,
            "query_checksum": fixtures[0]["query_checksum"],
            "result_count": 1,
            "record_ids": [rendered["record_id"]],
        }
    ]
    assert rendered["record_id"] in result["output"]
