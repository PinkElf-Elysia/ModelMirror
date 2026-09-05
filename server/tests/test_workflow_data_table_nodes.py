from __future__ import annotations

import json

import httpx
import pytest

import server.main as main_module
from server.data_tables.api import (
    configure_agent_table_store,
    get_agent_table_store,
)
from server.data_tables.store import (
    AgentTableConflictError,
    AgentTableStore,
    AgentTableValidationError,
)
from server.workflow_native.schemas import NativeWorkflowDefinition
from server.workflow_native.validate import validate_workflow_graph
from server.xperts.api import (
    _prepare_published_resource_snapshot,
    set_xpert_store_for_tests,
)
from server.xperts.app_api import _deployment_preflight
from server.xperts.app_models import XpertAppPolicy
from server.xperts.store import XpertStore
from server.xpert_runtime import WorkflowNodeRegistry, register_builtin_workflow_nodes


def _fields() -> list[dict[str, object]]:
    return [
        {"name": "name", "data_type": "string", "required": True},
        {
            "name": "priority",
            "data_type": "integer",
            "has_default": True,
            "default_value": 0,
        },
        {
            "name": "active",
            "data_type": "boolean",
            "has_default": True,
            "default_value": True,
        },
    ]


def _published_table(tmp_path):
    store = AgentTableStore(tmp_path / "tables")
    table = store.create_table(name="Tasks", fields=_fields())
    schema = store.publish_table(table.table_id, revision=table.draft_revision)
    return store, store.get_table(table.table_id), schema


def _parse_sse_events(text: str) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def _filter(field: str, operator: str, value: object) -> dict:
    return {
        "field": field,
        "operator": operator,
        "value": {"source": "literal", "value": value},
    }


def test_store_query_batch_writes_and_idempotency(tmp_path) -> None:
    store, table, schema = _published_table(tmp_path)
    for index, priority in enumerate([1, 3, 2], start=1):
        store.create_record_for_schema(
            table.table_id,
            schema_version=schema.version,
            data={"name": f"Task {index}", "priority": priority},
            operation_id=f"seed-{index}",
        )

    records = store.query_records(
        table.table_id,
        schema_version=schema.version,
        fields=["name", "priority"],
        filter_tree={
            "logic": "and",
            "items": [
                {"field": "priority", "operator": "gte", "value": 2},
                {"field": "name", "operator": "contains", "value": "Task"},
            ],
        },
        sort=[{"field": "priority", "direction": "desc"}],
        limit=20,
    )
    assert [record["priority"] for record in records] == [3, 2]
    assert all("active" not in record for record in records)
    assert all("record_id" in record and "revision" in record for record in records)

    update_filter = {"field": "priority", "operator": "gte", "value": 2}
    updated = store.update_records(
        table.table_id,
        schema_version=schema.version,
        filter_tree=update_filter,
        data={"active": False},
        operation_id="workflow-update",
    )
    replay = store.update_records(
        table.table_id,
        schema_version=schema.version,
        filter_tree=update_filter,
        data={"active": False},
        operation_id="workflow-update",
    )
    assert updated == replay == {"matched": 2, "affected": 2}

    deleted = store.delete_records(
        table.table_id,
        schema_version=schema.version,
        filter_tree={"field": "active", "operator": "eq", "value": False},
        operation_id="workflow-delete",
    )
    assert deleted == {"matched": 2, "affected": 2}
    assert len(store.list_records(table.table_id)) == 1


def test_store_rejects_unbounded_writes_old_schema_and_reserved_fields(tmp_path) -> None:
    store, table, schema = _published_table(tmp_path)
    with pytest.raises(AgentTableValidationError, match="non-empty filter"):
        store.delete_records(
            table.table_id,
            schema_version=schema.version,
            filter_tree={},
            operation_id="unsafe-delete",
        )

    draft_fields = [field.model_dump() for field in table.fields]
    draft_fields.append({"name": "owner", "data_type": "string"})
    changed = store.update_table(
        table.table_id,
        revision=table.draft_revision,
        patch={"fields": draft_fields},
    )
    store.publish_table(table.table_id, revision=changed.draft_revision)
    with pytest.raises(AgentTableConflictError, match="active schema"):
        store.create_record_for_schema(
            table.table_id,
            schema_version=1,
            data={"name": "Old"},
            operation_id="old-schema-write",
        )

    with pytest.raises(AgentTableValidationError, match="reserved"):
        store.create_table(
            name="Invalid",
            fields=[{"name": "record_id", "data_type": "string"}],
        )


def test_data_table_node_validation_contract_and_variable_reachability() -> None:
    workflow = NativeWorkflowDefinition.model_validate(
        {
            "id": "data-table-contract",
            "title": "Data table contract",
            "nodes": [
                {
                    "id": "input",
                    "type": "input",
                    "data": {"kind": "input", "variableName": "min_priority"},
                },
                {
                    "id": "query",
                    "type": "data_table_query",
                    "data": {
                        "kind": "data_table_query",
                        "tableId": "table-id",
                        "versionPolicy": "pinned",
                        "pinnedSchemaVersion": 1,
                        "filter": {
                            "field": "priority",
                            "operator": "gte",
                            "value": {
                                "source": "variable",
                                "variable": "min_priority",
                            },
                        },
                        "limit": 20,
                        "returnMode": "list",
                        "outputVariable": "records",
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "data": {"kind": "output", "outputVariable": "records"},
                },
            ],
            "edges": [
                {"id": "e1", "source": "input", "target": "query"},
                {"id": "e2", "source": "query", "target": "output"},
            ],
        }
    )
    result = validate_workflow_graph(workflow)
    assert result.valid is True, result.issues

    workflow.nodes[1].data["filter"]["value"]["variable"] = "missing"
    result = validate_workflow_graph(workflow)
    assert "missing_data_table_variable_reference" in {
        issue.code for issue in result.issues
    }

    workflow.nodes[1].data["filter"]["value"] = 3
    result = validate_workflow_graph(workflow)
    assert "invalid_data_table_value_binding" in {
        issue.code for issue in result.issues
    }

    workflow.nodes[1].type = "data_table_update"
    workflow.nodes[1].data = {
        "kind": "data_table_update",
        "tableId": "table-id",
        "versionPolicy": "latest",
        "valueBindings": {
            "priority": {"source": "literal", "value": 4}
        },
        "outputVariable": "updated",
    }
    result = validate_workflow_graph(workflow)
    assert "missing_data_table_filter" in {issue.code for issue in result.issues}


def test_only_data_table_query_is_planner_enabled() -> None:
    registry = WorkflowNodeRegistry()
    register_builtin_workflow_nodes(registry)
    items = {
        item.kind: item
        for section in registry.sections()
        for item in section.items
        if item.kind and item.kind.startswith("data_table_")
    }

    assert set(items) == {
        "data_table_query",
        "data_table_insert",
        "data_table_update",
        "data_table_delete",
    }
    assert all(item.enabled is True for item in items.values())
    assert items["data_table_query"].to_payload()["planner"]["enabled"] is True
    assert all(
        items[kind].to_payload()["planner"]["enabled"] is False
        for kind in {
            "data_table_insert",
            "data_table_update",
            "data_table_delete",
        }
    )
    assert all(
        item.to_payload()["contract"]["contract_status"] == "complete"
        for item in items.values()
    )


@pytest.mark.asyncio
async def test_classic_workflow_query_preserves_typed_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = get_agent_table_store()
    store, table, schema = _published_table(tmp_path)
    store.create_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"name": "Critical", "priority": 5},
        operation_id="seed-critical",
    )
    configure_agent_table_store(store)
    monkeypatch.setattr(main_module, "agent_table_store", store)
    main_module.request_windows.clear()
    workflow = {
        "id": "data-table-query-run",
        "title": "Data table query run",
        "nodes": [
            {
                "id": "query",
                "type": "data_table_query",
                "data": {
                    "kind": "data_table_query",
                    "tableId": table.table_id,
                    "versionPolicy": "pinned",
                    "pinnedSchemaVersion": schema.version,
                    "selectFields": ["name", "priority"],
                    "filter": _filter("priority", "gte", 3),
                    "returnMode": "first",
                    "outputVariable": "record",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "record"},
            },
        ],
        "edges": [{"id": "e1", "source": "query", "target": "output"}],
    }
    try:
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/workflow/run",
                json={"workflow": workflow, "inputs": {}},
            )
        assert response.status_code == 200, response.text
        completed = next(
            event
            for event in _parse_sse_events(response.text)
            if event.get("event") == "workflow_end"
        )
        assert completed["variables"]["record"]["name"] == "Critical"
        assert completed["variables"]["record"]["priority"] == 5
        assert json.loads(completed["final_output"])["record_id"]
    finally:
        configure_agent_table_store(previous)
        main_module.request_windows.clear()


@pytest.mark.asyncio
async def test_classic_workflow_write_nodes_execute_typed_crud(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = get_agent_table_store()
    store, table, _schema = _published_table(tmp_path)
    configure_agent_table_store(store)
    monkeypatch.setattr(main_module, "agent_table_store", store)
    main_module.request_windows.clear()
    workflow = {
        "id": "data-table-write-run",
        "title": "Data table write run",
        "nodes": [
            {
                "id": "insert",
                "type": "data_table_insert",
                "data": {
                    "kind": "data_table_insert",
                    "tableId": table.table_id,
                    "versionPolicy": "latest",
                    "valueBindings": {
                        "name": {"source": "literal", "value": "Disposable"},
                        "priority": {"source": "literal", "value": 1},
                    },
                    "outputVariable": "inserted",
                },
            },
            {
                "id": "update",
                "type": "data_table_update",
                "data": {
                    "kind": "data_table_update",
                    "tableId": table.table_id,
                    "versionPolicy": "latest",
                    "filter": _filter("name", "eq", "Disposable"),
                    "valueBindings": {
                        "priority": {"source": "literal", "value": 7},
                        "active": {"source": "literal", "value": False},
                    },
                    "outputVariable": "updated",
                },
            },
            {
                "id": "delete",
                "type": "data_table_delete",
                "data": {
                    "kind": "data_table_delete",
                    "tableId": table.table_id,
                    "versionPolicy": "latest",
                    "filter": _filter("priority", "eq", 7),
                    "outputVariable": "deleted",
                },
            },
            {
                "id": "output",
                "type": "output",
                "data": {"kind": "output", "outputVariable": "deleted"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "insert", "target": "update"},
            {"id": "e2", "source": "update", "target": "delete"},
            {"id": "e3", "source": "delete", "target": "output"},
        ],
    }
    try:
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/workflow/run",
                json={"workflow": workflow, "inputs": {}},
            )
        assert response.status_code == 200, response.text
        completed = next(
            event
            for event in _parse_sse_events(response.text)
            if event.get("event") == "workflow_end"
        )
        assert completed["variables"]["inserted"]["name"] == "Disposable"
        assert completed["variables"]["inserted"]["record_id"]
        assert completed["variables"]["updated"] == {"matched": 1, "affected": 1}
        assert completed["variables"]["deleted"] == {"matched": 1, "affected": 1}
        assert store.list_records(table.table_id) == []
    finally:
        configure_agent_table_store(previous)
        main_module.request_windows.clear()


def test_xpert_publish_pins_schema_and_public_app_rejects_agent_tables(
    tmp_path,
) -> None:
    previous_table_store = get_agent_table_store()
    table_store, table, schema = _published_table(tmp_path)
    xpert_store = XpertStore(tmp_path / "xperts")
    set_xpert_store_for_tests(xpert_store)
    configure_agent_table_store(table_store)
    try:
        xpert = xpert_store.create_xpert(name="Table Reader")
        draft = xpert.draft.model_copy(deep=True)
        draft.workflow.nodes.insert(
            1,
            NativeWorkflowDefinition.model_validate(
                {
                    "nodes": [
                        {
                            "id": "table-query",
                            "type": "data_table_query",
                            "data": {
                                "kind": "data_table_query",
                                "tableId": table.table_id,
                                "versionPolicy": "latest",
                                "outputVariable": "table_result",
                            },
                        }
                    ]
                }
            ).nodes[0],
        )
        draft.workflow = NativeWorkflowDefinition.model_validate(
            {
                **draft.workflow.model_dump(mode="json"),
                "edges": [
                    {"id": "e1", "source": "input-1", "target": "table-query"},
                    {"id": "e2", "source": "table-query", "target": "output-1"},
                ],
            }
        )
        output = next(node for node in draft.workflow.nodes if node.id == "output-1")
        output.data["outputVariable"] = "table_result"
        xpert = xpert_store.update_xpert(
            xpert.id,
            {"draft": draft.model_dump(mode="json")},
        )
        query_node = next(
            node for node in xpert.draft.workflow.nodes if node.id == "table-query"
        )
        query_node.data["selectFields"] = ["missing_field"]
        _, invalid_issues = _prepare_published_resource_snapshot(xpert)
        assert "xpert_agent_table_invalid" in {
            issue.code for issue in invalid_issues
        }
        query_node.data["selectFields"] = ["name"]
        frozen, issues = _prepare_published_resource_snapshot(xpert)
        assert issues == []
        node = next(node for node in frozen.nodes if node.id == "table-query")
        assert node.data["versionPolicy"] == "pinned"
        assert node.data["pinnedSchemaVersion"] == schema.version

        version = xpert_store.publish_xpert(
            xpert.id,
            expected_revision=xpert.draft_revision,
            workflow_override=frozen,
        )
        preflight = _deployment_preflight(version, XpertAppPolicy())
        assert preflight["valid"] is False
        assert "app_agent_table_forbidden" in {
            issue["code"] for issue in preflight["issues"]
        }
    finally:
        configure_agent_table_store(previous_table_store)
        set_xpert_store_for_tests(None)


@pytest.mark.asyncio
async def test_data_table_resource_options_are_schema_safe(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, table, schema = _published_table(tmp_path)
    monkeypatch.setattr(main_module, "agent_table_store", store)
    transport = httpx.ASGITransport(app=main_module.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/workflow/resource-options",
            params={"kind": "data_table"},
        )
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["id"] == table.table_id
    assert item["active_schema_version"] == schema.version
    assert item["fields"][0]["name"] == "name"
    assert "database_path" not in response.text
