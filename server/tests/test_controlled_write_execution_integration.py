from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import JSONResponse, StreamingResponse


import server.main as main_module
from server.data_tables.store import AgentTableStore
from server.workflow_native.controlled_writes import (
    EvaluationWriteBackendContext,
    write_workflow_checksum,
)
from server.xpert_runtime.execution_store import WorkflowExecutionStore


class _NoEgressWorkflowGateway:
    @classmethod
    def for_router(cls, _service: Any) -> "_NoEgressWorkflowGateway":
        return cls()

    def routing_mode(self, _source_kind: str | None) -> str:
        return "legacy"

    def agent_routing_mode(self, _source_kind: str | None) -> str:
        return "legacy"


def _literal_filter(field: str, value: Any) -> dict[str, Any]:
    return {
        "field": field,
        "operator": "eq",
        "value": {"source": "literal", "value": value},
    }


def _node(node_id: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": kind,
        "position": {"x": 0, "y": 0},
        "data": {"kind": kind, **data},
    }


def _workflow(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "controlled-write-execution-integration",
        "title": "受控写入执行集成",
        "nodes": nodes,
        "edges": [
            {
                "id": f"edge-{index}",
                "source": source["id"],
                "target": target["id"],
            }
            for index, (source, target) in enumerate(zip(nodes, nodes[1:]), start=1)
        ],
    }


def _write_data(
    table_id: str,
    schema: Any,
    *,
    operation: str,
    planner_ref: str,
    output_variable: str,
    writable_fields: list[str],
) -> dict[str, Any]:
    return {
        "tableId": table_id,
        "versionPolicy": "pinned",
        "pinnedSchemaVersion": schema.version,
        "pinnedSchemaChecksum": schema.checksum,
        "contractVersion": 2,
        "plannerRef": planner_ref,
        "writeGrant": {
            "table_id": table_id,
            "operations": [operation],
            "writable_fields": writable_fields,
            "max_affected_rows": 1,
        },
        "maxAffectedRows": 1,
        "failureAction": "stop",
        "retryMode": "none",
        "outputVariable": output_variable,
    }


def _query_data(
    table_id: str,
    schema: Any,
    *,
    planner_ref: str,
    output_variable: str,
) -> dict[str, Any]:
    return {
        "tableId": table_id,
        "versionPolicy": "pinned",
        "pinnedSchemaVersion": schema.version,
        "pinnedSchemaChecksum": schema.checksum,
        "plannerRef": planner_ref,
        "outputVariable": output_variable,
        "returnMode": "list",
        "limit": 20,
        "filter": _literal_filter("title", "isolated-row"),
    }


def _full_write_workflow(table_id: str, schema: Any) -> dict[str, Any]:
    insert = _write_data(
        table_id,
        schema,
        operation="insert",
        planner_ref="insert_row",
        output_variable="inserted_record",
        writable_fields=["title", "status"],
    )
    insert.update(
        valueSource="literal",
        literalValues={"title": "isolated-row", "status": "open"},
    )
    update = _write_data(
        table_id,
        schema,
        operation="update",
        planner_ref="update_row",
        output_variable="update_result",
        writable_fields=["status"],
    )
    update.update(
        recordsVariable="rows_after_insert",
        filter=_literal_filter("title", "isolated-row"),
        valueSource="literal",
        literalValues={"status": "closed"},
    )
    delete = _write_data(
        table_id,
        schema,
        operation="delete",
        planner_ref="delete_row",
        output_variable="delete_result",
        writable_fields=[],
    )
    delete.update(
        recordsVariable="rows_after_update",
        filter=_literal_filter("title", "isolated-row"),
    )
    return _workflow(
        [
            _node("input", "input", {"variableName": "user_input"}),
            _node("insert", "data_table_insert", insert),
            _node(
                "query_after_insert",
                "data_table_query",
                _query_data(
                    table_id,
                    schema,
                    planner_ref="query_after_insert",
                    output_variable="rows_after_insert",
                ),
            ),
            _node("update", "data_table_update", update),
            _node(
                "query_after_update",
                "data_table_query",
                _query_data(
                    table_id,
                    schema,
                    planner_ref="query_after_update",
                    output_variable="rows_after_update",
                ),
            ),
            _node("delete", "data_table_delete", delete),
            _node("output", "output", {"outputVariable": "delete_result"}),
        ]
    )


def _published_business_table(tmp_path: Path) -> tuple[AgentTableStore, str, Any, dict[str, Any]]:
    business = AgentTableStore(tmp_path / "business")
    table = business.create_table(
        name="合成集成测试表",
        fields=[
            {"name": "title", "data_type": "string", "required": True},
            {
                "name": "status",
                "data_type": "string",
                "has_default": True,
                "default_value": "open",
            },
        ],
    )
    schema = business.publish_table(table.table_id, revision=table.draft_revision)
    sentinel = business.create_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"title": "business-sentinel"},
        operation_id="seed-business-sentinel",
    )
    return business, table.table_id, schema, sentinel


def _isolated_backend(
    tmp_path: Path,
    business: AgentTableStore,
    table_id: str,
    schema: Any,
) -> tuple[AgentTableStore, str]:
    table = business.get_table(table_id)
    tables = [
        {
            "table_id": table_id,
            "name": table.name,
            "schema_version": schema.version,
            "schema_checksum": schema.checksum,
            "fields": [field.model_dump(mode="json") for field in schema.fields],
            "records": [],
        }
    ]
    initialization_checksum = hashlib.sha256(
        json.dumps(
            tables,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    isolated = AgentTableStore(tmp_path / "isolated")
    isolated.initialize_evaluation_tables(
        tables,
        initialization_checksum=initialization_checksum,
    )
    return isolated, initialization_checksum


def _evaluation_context(
    backend: AgentTableStore,
    workflow: dict[str, Any],
    initialization_checksum: str,
    receipts: list[dict[str, Any]],
) -> EvaluationWriteBackendContext:
    return EvaluationWriteBackendContext(
        backend=backend,
        run_id="evaluation-run",
        item_id="evaluation-item",
        fixture_checksum=initialization_checksum,
        target_id="evaluation-target",
        case_id="evaluation-case",
        workflow_checksum=write_workflow_checksum(workflow),
        receipt_callback=lambda receipt: receipts.append(dict(receipt)),
    )


def _metadata() -> dict[str, str]:
    return {
        "evaluation_run_id": "evaluation-run",
        "evaluation_item_id": "evaluation-item",
        "evaluation_target_id": "evaluation-target",
        "evaluation_case_id": "evaluation-case",
    }


async def _events(response: StreamingResponse) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    buffer = ""
    async for chunk in response.body_iterator:
        buffer += chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            for line in frame.splitlines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture(autouse=True)
def _isolated_main_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_module,
        "workflow_execution_store",
        WorkflowExecutionStore(tmp_path / "workflow-executions"),
    )
    monkeypatch.setattr(main_module, "workflow_task_store", {})
    monkeypatch.setattr(main_module, "get_llm_gateway_config", lambda: ("", ""))
    monkeypatch.setattr(main_module, "get_model_router_service", lambda: object())
    monkeypatch.setattr(
        main_module, "ManagedWorkflowGateway", _NoEgressWorkflowGateway
    )
    main_module.request_windows.clear()


async def _run_evaluation(
    workflow: dict[str, Any],
    context: EvaluationWriteBackendContext,
    *,
    task_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = await main_module._run_workflow_response(
        main_module.WorkflowRunRequest.model_validate(
            {"workflow": workflow, "inputs": {"user_input": "synthetic"}}
        ),
        None,
        runtime_run_type="xpert_evaluation",
        runtime_source_id="evaluation-target",
        runtime_metadata=_metadata(),
        runtime_task_id=task_id,
        evaluation_write_backend=context,
    )
    assert isinstance(response, StreamingResponse)
    events = await _events(response)
    return events, main_module.workflow_task_store[task_id]


@pytest.mark.asyncio
async def test_real_runner_executes_insert_query_update_query_delete_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    business, table_id, schema, sentinel = _published_business_table(tmp_path)
    isolated, initialization_checksum = _isolated_backend(
        tmp_path, business, table_id, schema
    )
    monkeypatch.setattr(main_module, "agent_table_store", business)
    workflow = _full_write_workflow(table_id, schema)
    receipts: list[dict[str, Any]] = []
    events, task_state = await _run_evaluation(
        workflow,
        _evaluation_context(isolated, workflow, initialization_checksum, receipts),
        task_id="full-controlled-write-sequence",
    )

    assert not [event for event in events if event.get("event") == "error"], events
    assert any(event.get("event") == "workflow_end" for event in events)
    assert [
        event["node_id"]
        for event in events
        if event.get("event") == "node_start"
        and event.get("node_id")
        in {"insert", "query_after_insert", "update", "query_after_update", "delete"}
    ] == ["insert", "query_after_insert", "update", "query_after_update", "delete"]

    query_after_insert_event = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "query_after_insert"
    )
    query_after_update_event = next(
        event
        for event in events
        if event.get("event") == "node_end"
        and event.get("node_id") == "query_after_update"
    )
    assert "rows_after_insert" not in query_after_insert_event["variables"]
    assert "rows_after_update" not in query_after_update_event["variables"]
    after_insert = main_module.workflow_execution_store.read_private_write_entry(
        "full-controlled-write-sequence", "source:query_after_insert"
    )["records"]
    after_update = main_module.workflow_execution_store.read_private_write_entry(
        "full-controlled-write-sequence", "source:query_after_update"
    )["records"]
    assert len(after_insert) == 1
    assert after_insert[0]["title"] == "isolated-row"
    assert after_insert[0]["status"] == "open"
    assert after_insert[0]["revision"] == 1
    assert len(after_update) == 1
    assert after_update[0]["record_id"] == after_insert[0]["record_id"]
    assert after_update[0]["status"] == "closed"
    assert after_update[0]["revision"] == 2
    assert isolated.query_records(table_id, schema_version=schema.version) == []
    assert [receipt["operation"] for receipt in receipts] == [
        "insert",
        "update",
        "delete",
    ]
    assert [effect["status"] for effect in task_state["controlled_write_runtime"].effects] == [
        "applied",
        "applied",
        "applied",
    ]
    assert business.query_records(table_id, schema_version=schema.version) == [sentinel]


@pytest.mark.asyncio
async def test_xpert_evaluation_rejects_v1_before_business_store_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    business, table_id, schema, sentinel = _published_business_table(tmp_path)
    monkeypatch.setattr(main_module, "agent_table_store", business)
    workflow = _workflow(
        [
            _node("input", "input", {"variableName": "user_input"}),
            _node(
                "legacy_insert",
                "data_table_insert",
                {
                    "tableId": table_id,
                    "versionPolicy": "pinned",
                    "pinnedSchemaVersion": schema.version,
                    "valueBindings": {
                        "title": {"source": "literal", "value": "must-not-write"}
                    },
                    "outputVariable": "legacy_result",
                },
            ),
            _node("output", "output", {"outputVariable": "legacy_result"}),
        ]
    )

    response = await main_module._run_workflow_response(
        main_module.WorkflowRunRequest.model_validate(
            {"workflow": workflow, "inputs": {"user_input": "synthetic"}}
        ),
        None,
        runtime_run_type="xpert_evaluation",
        runtime_metadata=_metadata(),
        runtime_task_id="legacy-evaluation-write",
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 422
    payload = json.loads(response.body)
    assert payload["code"] == "CONTROLLED_WRITE_PREFLIGHT_FAILED"
    assert "评测禁止旧版" in payload["error"]
    assert business.query_records(table_id, schema_version=schema.version) == [sentinel]


@pytest.mark.asyncio
async def test_later_runtime_error_preserves_prior_isolated_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    business, table_id, schema, sentinel = _published_business_table(tmp_path)
    isolated, initialization_checksum = _isolated_backend(
        tmp_path, business, table_id, schema
    )
    monkeypatch.setattr(main_module, "agent_table_store", business)
    insert = _write_data(
        table_id,
        schema,
        operation="insert",
        planner_ref="insert_before_error",
        output_variable="inserted_record",
        writable_fields=["title", "status"],
    )
    insert.update(
        valueSource="literal",
        literalValues={"title": "isolated-row", "status": "open"},
    )
    workflow = _workflow(
        [
            _node("input", "input", {"variableName": "user_input"}),
            _node("insert", "data_table_insert", insert),
            _node(
                "stop",
                "terminate_error",
                {
                    "errorCode": "EXPECTED_AFTER_WRITE",
                    "message": "提交后的预期合成失败。",
                },
            ),
        ]
    )
    receipts: list[dict[str, Any]] = []
    events, task_state = await _run_evaluation(
        workflow,
        _evaluation_context(isolated, workflow, initialization_checksum, receipts),
        task_id="controlled-write-then-error",
    )

    error = next(event for event in events if event.get("event") == "error")
    assert error["code"] == "EXPECTED_AFTER_WRITE"
    assert not any(event.get("event") == "workflow_end" for event in events)
    committed = isolated.query_records(table_id, schema_version=schema.version)
    assert len(committed) == 1
    assert committed[0]["title"] == "isolated-row"
    assert [receipt["operation"] for receipt in receipts] == ["insert"]
    assert task_state["controlled_write_runtime"].effects[0]["status"] == "applied"
    assert business.query_records(table_id, schema_version=schema.version) == [sentinel]
