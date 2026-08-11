from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server.data_tables.api import (
    configure_agent_table_store,
    get_agent_table_store,
    router,
)
from server.data_tables.store import (
    AgentTableConflictError,
    AgentTableStore,
    AgentTableValidationError,
)


def _fields() -> list[dict[str, object]]:
    return [
        {
            "name": "title",
            "label": "标题",
            "data_type": "string",
            "required": True,
        },
        {
            "name": "priority",
            "label": "优先级",
            "data_type": "integer",
            "has_default": True,
            "default_value": 0,
        },
        {
            "name": "done",
            "data_type": "boolean",
            "has_default": True,
            "default_value": False,
        },
        {"name": "metadata", "data_type": "json"},
    ]


def _published_store(tmp_path):
    store = AgentTableStore(tmp_path)
    table = store.create_table(name="Tasks", description="Local tasks", fields=_fields())
    version = store.publish_table(table.table_id, revision=table.draft_revision)
    return store, store.get_table(table.table_id), version


def test_sqlite_store_persists_schema_records_and_wal(tmp_path):
    store, table, version = _published_store(tmp_path)
    record = store.create_record(
        table.table_id,
        data={"title": "Review", "metadata": {"source": "manual"}},
    )

    assert version.version == 1
    assert record.data == {
        "title": "Review",
        "priority": 0,
        "done": False,
        "metadata": {"source": "manual"},
    }
    assert (tmp_path / "agent_tables.sqlite3").exists()

    reloaded = AgentTableStore(tmp_path)
    detail = reloaded.get_detail(table.table_id)
    assert detail.table.active_schema_version == 1
    assert detail.record_count == 1
    assert reloaded.list_records(table.table_id)[0].data == record.data


def test_schema_versions_are_immutable_and_evolution_is_compatible(tmp_path):
    store, table, first = _published_store(tmp_path)
    fields = [field.model_dump() for field in table.fields]
    fields[0]["label"] = "任务标题"
    fields.append(
        {"name": "owner", "data_type": "string", "required": False}
    )
    edited = store.update_table(
        table.table_id,
        revision=table.draft_revision,
        patch={"fields": fields},
    )
    second = store.publish_table(
        table.table_id, revision=edited.draft_revision
    )

    assert first.version == 1
    assert first.fields[0].label == "标题"
    assert second.version == 2
    assert second.fields[0].label == "任务标题"
    assert store.get_schema_version(table.table_id, 1).checksum == first.checksum

    incompatible = [field.model_dump() for field in edited.fields[1:]]
    with pytest.raises(AgentTableValidationError, match="cannot be removed"):
        store.update_table(
            table.table_id,
            revision=edited.draft_revision,
            patch={"fields": incompatible},
        )
    assert store.get_table(table.table_id).draft_revision == edited.draft_revision


def test_records_validate_types_revision_and_transaction_rollback(tmp_path):
    store, table, _ = _published_store(tmp_path)
    with pytest.raises(AgentTableValidationError, match="must be an integer"):
        store.create_record(
            table.table_id, data={"title": "Bad", "priority": "high"}
        )
    assert store.list_records(table.table_id) == []

    record = store.create_record(table.table_id, data={"title": "Good"})
    with pytest.raises(AgentTableConflictError, match="revision conflict"):
        store.update_record(
            table.table_id,
            record.record_id,
            revision=2,
            data={"done": True},
        )
    unchanged = store.list_records(table.table_id)[0]
    assert unchanged.revision == 1
    assert unchanged.data["done"] is False


def test_record_operations_are_idempotent_and_payload_bound(tmp_path):
    store, table, _ = _published_store(tmp_path)
    first = store.create_record(
        table.table_id,
        data={"title": "Once"},
        operation_id="task-1:create",
    )
    replay = store.create_record(
        table.table_id,
        data={"title": "Once"},
        operation_id="task-1:create",
    )
    assert replay.record_id == first.record_id
    assert len(store.list_records(table.table_id)) == 1

    with pytest.raises(AgentTableConflictError, match="different request"):
        store.create_record(
            table.table_id,
            data={"title": "Different"},
            operation_id="task-1:create",
        )

    updated = store.update_record(
        table.table_id,
        first.record_id,
        revision=1,
        data={"done": True},
        operation_id="task-1:update",
    )
    update_replay = store.update_record(
        table.table_id,
        first.record_id,
        revision=1,
        data={"done": True},
        operation_id="task-1:update",
    )
    assert updated.revision == update_replay.revision == 2

    deleted = store.delete_record(
        table.table_id,
        first.record_id,
        revision=2,
        operation_id="task-1:delete",
    )
    assert store.delete_record(
        table.table_id,
        first.record_id,
        revision=2,
        operation_id="task-1:delete",
    ) == deleted


def test_archived_table_is_read_only_and_record_body_is_bounded(tmp_path):
    store, table, _ = _published_store(tmp_path)
    with pytest.raises(AgentTableValidationError, match="exceeds"):
        store.create_record(
            table.table_id,
            data={"title": "x" * (256 * 1024)},
        )
    archived = store.archive_table(table.table_id, revision=table.draft_revision)
    assert archived.status == "archived"
    with pytest.raises(AgentTableConflictError, match="read-only"):
        store.create_record(table.table_id, data={"title": "No"})
    assert store.list_records(table.table_id) == []


def test_agent_table_api_exposes_management_contract_without_paths(tmp_path):
    previous = None
    try:
        previous = get_agent_table_store()
    except RuntimeError:
        pass
    store = AgentTableStore(tmp_path)
    configure_agent_table_store(store)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    try:
        created = client.post(
            "/api/data-tables",
            json={"name": "Contacts", "fields": _fields()},
        )
        assert created.status_code == 200
        table = created.json()
        published = client.post(
            f"/api/data-tables/{table['table_id']}/publish",
            json={"revision": table["draft_revision"]},
        )
        assert published.status_code == 200
        record = client.post(
            f"/api/data-tables/{table['table_id']}/records",
            json={"data": {"title": "Ada"}, "operation_id": "api-create-1"},
        )
        assert record.status_code == 200
        detail = client.get(f"/api/data-tables/{table['table_id']}").json()
        assert detail["record_count"] == 1
        assert "database_path" not in str(detail)
        assert client.patch(
            f"/api/data-tables/{table['table_id']}",
            json={"revision": 999, "description": "conflict"},
        ).status_code == 409
    finally:
        if previous is not None:
            configure_agent_table_store(previous)

