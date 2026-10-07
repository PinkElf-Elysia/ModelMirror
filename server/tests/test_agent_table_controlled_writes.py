from __future__ import annotations

import hashlib
import json

import pytest

from server.data_tables.store import (
    AgentTableConflictError,
    AgentTableNotFoundError,
    AgentTableStore,
    AgentTableValidationError,
    SQLiteAgentTableBackend,
    controlled_write_request_checksum,
)


def _fields() -> list[dict[str, object]]:
    return [
        {"name": "title", "data_type": "string", "required": True},
        {
            "name": "priority",
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
        {"name": "note", "data_type": "string"},
    ]


def _published_store(tmp_path, *, fields=None):
    store = AgentTableStore(tmp_path)
    table = store.create_table(name="Tasks", fields=fields or _fields())
    schema = store.publish_table(table.table_id, revision=table.draft_revision)
    return store, store.get_table(table.table_id), schema


def _request(
    table_id: str,
    schema,
    *,
    operation: str,
    data: dict[str, object] | None,
    filter_tree: dict[str, object] | None = None,
    expected_records: list[dict[str, object]] | None = None,
    writable_fields: list[str] | None = None,
    max_affected_rows: int = 1,
) -> dict[str, object]:
    return {
        "operation": operation,
        "table_id": table_id,
        "schema_version": schema.version,
        "schema_checksum": schema.checksum,
        "data": data,
        "filter": filter_tree,
        "expected_records": expected_records or [],
        "writable_fields": writable_fields or [],
        "max_affected_rows": max_affected_rows,
    }


def _execute(
    store: AgentTableStore,
    request: dict[str, object],
    operation_id: str,
    **options,
):
    return store.execute_controlled_write(
        request["table_id"],
        operation=request["operation"],
        schema_version=request["schema_version"],
        schema_checksum=request["schema_checksum"],
        data=request["data"],
        filter_tree=request["filter"],
        expected_records=request["expected_records"],
        writable_fields=request["writable_fields"],
        max_affected_rows=request["max_affected_rows"],
        operation_id=operation_id,
        request_checksum=controlled_write_request_checksum(request),
        **options,
    )


def _insert(store, table_id, schema, title, *, done=False, operation_id=None):
    request = _request(
        table_id,
        schema,
        operation="insert",
        data={"title": title, "done": done},
        writable_fields=["title", "done"],
    )
    return _execute(store, request, operation_id or f"insert-{title}")


def _expected(record: dict[str, object]) -> dict[str, object]:
    return {"record_id": record["record_id"], "revision": record["revision"]}


def _evaluation_fields() -> list[dict[str, object]]:
    return [
        {
            "field_id": "field_title",
            "name": "title",
            "label": "Title",
            "description": "Evaluator fixture title",
            "data_type": "string",
            "required": True,
            "has_default": False,
            "default_value": None,
        },
        {
            "field_id": "field_priority",
            "name": "priority",
            "label": "Priority",
            "description": "",
            "data_type": "integer",
            "required": False,
            "has_default": True,
            "default_value": 0,
        },
    ]


def _evaluation_schema_checksum(fields: list[dict[str, object]]) -> str:
    encoded = json.dumps(
        fields,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _evaluation_table(
    records: list[dict[str, object]],
    *,
    table_id: str = "evaluation_tasks",
    name: str = "Evaluation Tasks",
    schema_version: int = 7,
) -> dict[str, object]:
    fields = _evaluation_fields()
    return {
        "table_id": table_id,
        "name": name,
        "schema_version": schema_version,
        "schema_checksum": _evaluation_schema_checksum(fields),
        "fields": fields,
        "records": records,
    }


def _initialization_checksum(label: str = "fixture-v1") -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def test_controlled_write_checksum_is_exact_canonical_json():
    request = {"z": "雪", "a": [1, True, None]}
    expected = hashlib.sha256(
        b'{"a":[1,true,null],"z":"\\u96ea"}'
    ).hexdigest()
    assert controlled_write_request_checksum(request) == expected
    assert controlled_write_request_checksum({"a": [1, True, None], "z": "雪"}) == expected
    assert controlled_write_request_checksum({**request, "extra": None}) != expected

    with pytest.raises(AgentTableValidationError, match="finite"):
        controlled_write_request_checksum({"value": float("nan")})
    with pytest.raises(AgentTableValidationError, match="non-JSON"):
        controlled_write_request_checksum({"value": (1, 2)})
    with pytest.raises(AgentTableValidationError, match="string object keys"):
        controlled_write_request_checksum({1: "value"})


def test_validate_record_for_schema_is_read_only_and_reuses_type_validation(tmp_path):
    backend = SQLiteAgentTableBackend(tmp_path)
    table = backend.create_table(name="Validation", fields=_fields())
    schema = backend.publish_table(table.table_id, revision=table.draft_revision)
    audit_before = backend.list_audit(table.table_id)

    assert backend.validate_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"title": "Seed"},
    ) == {
        "title": "Seed",
        "priority": 0,
        "done": False,
    }
    assert backend.validate_record_for_schema(
        table.table_id,
        schema_version=schema.version,
        data={"priority": 3},
        partial=True,
    ) == {"priority": 3}
    with pytest.raises(AgentTableValidationError, match="must be an integer"):
        backend.validate_record_for_schema(
            table.table_id,
            schema_version=schema.version,
            data={"priority": "high"},
            partial=True,
        )

    assert backend.list_records(table.table_id) == []
    assert backend.list_audit(table.table_id) == audit_before


def test_all_operations_confine_expected_set_and_capture_private_effects(tmp_path):
    store, table, schema = _published_store(tmp_path)
    first = _insert(store, table.table_id, schema, "first")["output"]
    second = _insert(store, table.table_id, schema, "second", done=True)["output"]
    outside = _insert(store, table.table_id, schema, "outside")["output"]

    update_request = _request(
        table.table_id,
        schema,
        operation="update",
        data={"priority": 9},
        filter_tree={"field": "done", "operator": "eq", "value": False},
        expected_records=[_expected(first), _expected(second)],
        writable_fields=["priority"],
        max_affected_rows=1,
    )
    updated = _execute(
        store, update_request, "controlled-update", capture_effects=True
    )
    assert updated["output"] == {"matched": 1, "affected": 1}
    assert updated["receipt"]["changed_fields"] == ["priority"]
    assert updated["receipt"]["affected_count"] == 1
    assert set(updated["receipt"]) == {
        "operation_id",
        "table_id",
        "operation",
        "schema_version",
        "schema_checksum",
        "request_checksum",
        "affected_count",
        "changed_fields",
    }
    effect = updated["private_effect"]
    assert [item["record_id"] for item in effect["before_records"]] == [
        first["record_id"]
    ]
    assert effect["after_records"][0]["priority"] == 9
    assert effect["untouched_before_checksum"] == effect["untouched_after_checksum"]
    current = {record.record_id: record for record in store.list_records(table.table_id)}
    assert current[outside["record_id"]].data["priority"] == 0
    assert current[second["record_id"]].data["priority"] == 0

    delete_request = _request(
        table.table_id,
        schema,
        operation="delete",
        data=None,
        filter_tree={"field": "done", "operator": "eq", "value": True},
        expected_records=[_expected(second)],
        writable_fields=[],
    )
    deleted = _execute(
        store, delete_request, "controlled-delete", capture_effects=True
    )
    assert deleted["output"] == {"matched": 1, "affected": 1}
    assert deleted["private_effect"]["after_records"] == []
    assert (
        deleted["private_effect"]["untouched_before_checksum"]
        == deleted["private_effect"]["untouched_after_checksum"]
    )
    assert second["record_id"] not in {
        record.record_id for record in store.list_records(table.table_id)
    }


def test_first_dispatch_requires_existing_active_exact_nonarchived_schema(tmp_path):
    store, table, schema = _published_store(tmp_path / "published")
    unknown = _request(
        "table_missing",
        schema,
        operation="insert",
        data={"title": "unknown"},
        writable_fields=["title"],
    )
    with pytest.raises(AgentTableNotFoundError, match="not found"):
        _execute(store, unknown, "unknown-table")

    wrong_version = dict(
        _request(
            table.table_id,
            schema,
            operation="insert",
            data={"title": "wrong-version"},
            writable_fields=["title"],
        ),
        schema_version=schema.version + 1,
    )
    with pytest.raises(AgentTableConflictError, match="active schema"):
        _execute(store, wrong_version, "wrong-version")

    wrong_checksum = dict(
        _request(
            table.table_id,
            schema,
            operation="insert",
            data={"title": "wrong-checksum"},
            writable_fields=["title"],
        ),
        schema_checksum="0" * 64,
    )
    with pytest.raises(AgentTableConflictError, match="checksum"):
        _execute(store, wrong_checksum, "wrong-schema-checksum")

    draft_store = AgentTableStore(tmp_path / "draft")
    draft = draft_store.create_table(name="Draft", fields=_fields())
    draft_request = {
        **wrong_checksum,
        "table_id": draft.table_id,
        "schema_version": 1,
    }
    with pytest.raises(AgentTableConflictError, match="Publish"):
        _execute(draft_store, draft_request, "draft-table")

    store.archive_table(table.table_id, revision=table.draft_revision)
    archived = _request(
        table.table_id,
        schema,
        operation="insert",
        data={"title": "archived"},
        writable_fields=["title"],
    )
    with pytest.raises(AgentTableConflictError, match="read-only"):
        _execute(store, archived, "archived-table")


@pytest.mark.parametrize("max_rows", [True, 0, 101])
def test_strict_affected_cap_values_are_rejected(tmp_path, max_rows):
    store, table, schema = _published_store(tmp_path)
    request = _request(
        table.table_id,
        schema,
        operation="insert",
        data={"title": "cap"},
        writable_fields=["title"],
        max_affected_rows=max_rows,
    )
    with pytest.raises(AgentTableValidationError, match="max_affected_rows"):
        _execute(store, request, f"cap-{max_rows}")


def test_write_fields_types_conditions_body_and_expected_shape_are_strict(tmp_path):
    store, table, schema = _published_store(tmp_path)
    record = _insert(store, table.table_id, schema, "valid")["output"]

    invalid_requests = [
        (
            _request(
                table.table_id,
                schema,
                operation="insert",
                data={"title": "system", "record_id": "injected"},
                writable_fields=["title"],
            ),
            "not writable",
        ),
        (
            _request(
                table.table_id,
                schema,
                operation="insert",
                data={"title": "type", "priority": "high"},
                writable_fields=["title", "priority"],
            ),
            "must be an integer",
        ),
        (
            _request(
                table.table_id,
                schema,
                operation="insert",
                data={"title": "duplicate"},
                writable_fields=["title", "title"],
            ),
            "Duplicate writable field",
        ),
        (
            _request(
                table.table_id,
                schema,
                operation="update",
                data={"done": True},
                filter_tree=None,
                expected_records=[_expected(record)],
                writable_fields=["done"],
            ),
            "受控写入条件不能为空",
        ),
        (
            _request(
                table.table_id,
                schema,
                operation="delete",
                data={},
                filter_tree={},
                expected_records=[_expected(record)],
            ),
            "受控写入条件不能为空",
        ),
        (
            _request(
                table.table_id,
                schema,
                operation="insert",
                data={"title": "x" * (256 * 1024)},
                writable_fields=["title"],
            ),
            "exceeds",
        ),
    ]
    for index, (request, message) in enumerate(invalid_requests):
        with pytest.raises(AgentTableValidationError, match=message):
            _execute(store, request, f"invalid-{index}")

    for revision in (True, 0):
        request = _request(
            table.table_id,
            schema,
            operation="update",
            data={"done": True},
            filter_tree={"field": "title", "operator": "eq", "value": "valid"},
            expected_records=[
                {"record_id": record["record_id"], "revision": revision}
            ],
            writable_fields=["done"],
        )
        with pytest.raises(AgentTableValidationError, match="positive integer"):
            _execute(store, request, f"revision-{revision}")

    duplicate_expected = _request(
        table.table_id,
        schema,
        operation="update",
        data={"done": True},
        filter_tree={"field": "title", "operator": "eq", "value": "valid"},
        expected_records=[_expected(record), _expected(record)],
        writable_fields=["done"],
    )
    with pytest.raises(AgentTableValidationError, match="Duplicate expected"):
        _execute(store, duplicate_expected, "duplicate-expected")

    too_many = _request(
        table.table_id,
        schema,
        operation="delete",
        data={},
        filter_tree={"field": "done", "operator": "eq", "value": False},
        expected_records=[
            {"record_id": f"record_{index}", "revision": 1}
            for index in range(201)
        ],
    )
    with pytest.raises(AgentTableValidationError, match="0 and 200"):
        _execute(store, too_many, "too-many-expected")


def test_expected_records_all_validate_before_filter_and_batch_mutation(tmp_path):
    store, table, schema = _published_store(tmp_path)
    first = _insert(store, table.table_id, schema, "first")["output"]
    second = _insert(store, table.table_id, schema, "second")["output"]
    store.update_record(
        table.table_id,
        first["record_id"],
        revision=1,
        data={"note": "changed elsewhere"},
    )
    request = _request(
        table.table_id,
        schema,
        operation="update",
        data={"done": True},
        filter_tree={"field": "title", "operator": "eq", "value": "second"},
        expected_records=[_expected(first), _expected(second)],
        writable_fields=["done"],
        max_affected_rows=1,
    )
    with pytest.raises(AgentTableConflictError, match="revision changed"):
        _execute(store, request, "stale-before-filter")
    current = {record.record_id: record for record in store.list_records(table.table_id)}
    assert current[first["record_id"]].revision == 2
    assert current[second["record_id"]].revision == 1
    assert current[second["record_id"]].data["done"] is False

    missing = dict(request)
    missing["expected_records"] = [
        {"record_id": "record_deleted", "revision": 1}
    ]
    with pytest.raises(AgentTableConflictError, match="missing or deleted"):
        _execute(store, missing, "missing-expected")


def test_empty_expected_set_is_zero_match_and_actual_cap_is_post_filter(tmp_path):
    store, table, schema = _published_store(tmp_path)
    first = _insert(store, table.table_id, schema, "first")["output"]
    second = _insert(store, table.table_id, schema, "second")["output"]

    empty = _request(
        table.table_id,
        schema,
        operation="update",
        data={"done": True},
        filter_tree={"field": "done", "operator": "eq", "value": False},
        expected_records=[],
        writable_fields=["done"],
    )
    assert _execute(store, empty, "empty-expected")["output"] == {
        "matched": 0,
        "affected": 0,
    }

    over_cap = _request(
        table.table_id,
        schema,
        operation="update",
        data={"done": True},
        filter_tree={"field": "done", "operator": "eq", "value": False},
        expected_records=[_expected(first), _expected(second)],
        writable_fields=["done"],
        max_affected_rows=1,
    )
    with pytest.raises(AgentTableValidationError, match="exceeds max_affected_rows"):
        _execute(store, over_cap, "post-filter-cap")
    assert all(not record.data["done"] for record in store.list_records(table.table_id))


def test_whole_batch_validates_before_mutation(tmp_path):
    fields = _fields() + [{"name": "blob", "data_type": "string"}]
    store, table, schema = _published_store(tmp_path, fields=fields)
    large = store.create_record(
        table.table_id,
        data={"title": "large", "blob": "x" * (256 * 1024 - 200)},
    )
    small = store.create_record(table.table_id, data={"title": "small"})
    request = _request(
        table.table_id,
        schema,
        operation="update",
        data={"note": "y" * 400},
        filter_tree={"field": "done", "operator": "eq", "value": False},
        expected_records=[
            {"record_id": large.record_id, "revision": 1},
            {"record_id": small.record_id, "revision": 1},
        ],
        writable_fields=["note"],
        max_affected_rows=2,
    )
    with pytest.raises(AgentTableValidationError, match="exceeds"):
        _execute(store, request, "batch-body-rollback")
    records = {record.record_id: record for record in store.list_records(table.table_id)}
    assert records[large.record_id].revision == 1
    assert records[small.record_id].revision == 1
    assert "note" not in records[large.record_id].data
    assert "note" not in records[small.record_id].data


def test_replay_is_request_bound_persistent_and_schema_independent(tmp_path):
    store, table, schema = _published_store(tmp_path)
    request = _request(
        table.table_id,
        schema,
        operation="insert",
        data={"title": "once"},
        writable_fields=["title"],
    )
    checksum = controlled_write_request_checksum(request)
    first = _execute(store, request, "durable-operation", capture_effects=True)
    replay = _execute(store, request, "durable-operation", capture_effects=True)
    assert replay == {**first, "replayed": True}
    assert store.get_controlled_operation(
        table.table_id,
        "durable-operation",
        request_checksum=checksum,
    ) == replay

    conflicting = dict(request)
    conflicting["data"] = {"title": "different"}
    with pytest.raises(AgentTableConflictError, match="different request"):
        _execute(store, conflicting, "durable-operation")
    with pytest.raises(AgentTableConflictError, match="different request"):
        store.get_controlled_operation(
            table.table_id,
            "durable-operation",
            request_checksum="0" * 64,
        )

    fields = [field.model_dump() for field in table.fields]
    fields.append({"name": "owner", "data_type": "string"})
    edited = store.update_table(
        table.table_id,
        revision=table.draft_revision,
        patch={"fields": fields},
    )
    store.publish_table(table.table_id, revision=edited.draft_revision)
    store.archive_table(table.table_id, revision=edited.draft_revision)

    reloaded = AgentTableStore(tmp_path)
    assert _execute(reloaded, request, "durable-operation") == replay
    assert reloaded.get_controlled_operation(
        table.table_id,
        "durable-operation",
        request_checksum=checksum,
    ) == replay
    assert len(reloaded.list_records(table.table_id)) == 1


def test_private_effect_is_durable_and_recovery_does_not_requery_table(tmp_path):
    store, table, schema = _published_store(tmp_path)
    record = store.create_record(table.table_id, data={"title": "before"})
    request = _request(
        table.table_id,
        schema,
        operation="update",
        data={"note": "evaluated"},
        filter_tree={"field": "title", "operator": "eq", "value": "before"},
        expected_records=[
            {"record_id": record.record_id, "revision": record.revision}
        ],
        writable_fields=["note"],
    )
    checksum = controlled_write_request_checksum(request)
    committed = _execute(
        store, request, "durable-private-effect", capture_effects=True
    )
    assert committed["private_effect"]["before_records"][0]["title"] == "before"
    assert committed["private_effect"]["after_records"][0]["note"] == "evaluated"

    store.update_record(
        table.table_id,
        record.record_id,
        revision=2,
        data={"title": "changed after commit"},
    )
    restarted = AgentTableStore(tmp_path)
    recovered = restarted.get_controlled_operation(
        table.table_id,
        "durable-private-effect",
        request_checksum=checksum,
    )
    assert recovered == {**committed, "replayed": True}
    assert recovered["private_effect"] == committed["private_effect"]
    assert restarted.list_records(table.table_id)[0].data["title"] == (
        "changed after commit"
    )
    assert _execute(restarted, request, "durable-private-effect") == recovered


def test_live_controlled_write_does_not_persist_private_effect(tmp_path):
    store, table, schema = _published_store(tmp_path)
    request = _request(
        table.table_id,
        schema,
        operation="insert",
        data={"title": "live"},
        writable_fields=["title"],
    )
    checksum = controlled_write_request_checksum(request)
    result = _execute(store, request, "live-without-effects")
    recovered = store.get_controlled_operation(
        table.table_id,
        "live-without-effects",
        request_checksum=checksum,
    )
    assert "private_effect" not in result
    assert "private_effect" not in recovered


def test_logical_quota_rejects_and_rolls_back_record_ledger_and_audit(tmp_path):
    store, table, schema = _published_store(tmp_path)
    request = _request(
        table.table_id,
        schema,
        operation="insert",
        data={"title": "quota"},
        writable_fields=["title"],
    )
    checksum = controlled_write_request_checksum(request)
    with pytest.raises(AgentTableValidationError, match="max_logical_bytes"):
        _execute(store, request, "quota-operation", max_logical_bytes=1)
    assert store.list_records(table.table_id) == []
    assert store.get_controlled_operation(
        table.table_id,
        "quota-operation",
        request_checksum=checksum,
    ) is None
    assert not any(
        entry.operation == "controlled.insert"
        for entry in store.list_audit(table.table_id)
    )

    succeeded = _execute(store, request, "quota-operation", max_logical_bytes=10_000)
    assert succeeded["output"]["title"] == "quota"


def test_capture_effects_are_counted_by_logical_quota_and_rollback(tmp_path):
    store, table, schema = _published_store(tmp_path)
    records = [
        store.create_record(
            table.table_id,
            data={"title": f"{index}:" + ("x" * 245_000)},
        )
        for index in range(34)
    ]
    request = _request(
        table.table_id,
        schema,
        operation="update",
        data={"priority": 1},
        filter_tree={"field": "priority", "operator": "eq", "value": 0},
        expected_records=[
            {"record_id": record.record_id, "revision": 1}
            for record in records
        ],
        writable_fields=["priority"],
        max_affected_rows=34,
    )
    checksum = controlled_write_request_checksum(request)
    logical_limit = 16 * 1024 * 1024
    with pytest.raises(AgentTableValidationError, match="max_logical_bytes"):
        _execute(
            store,
            request,
            "private-effect-quota",
            capture_effects=True,
            max_logical_bytes=logical_limit,
        )
    assert store.get_controlled_operation(
        table.table_id,
        "private-effect-quota",
        request_checksum=checksum,
    ) is None
    rolled_back = store.list_records(table.table_id, limit=100)
    assert all(record.revision == 1 for record in rolled_back)
    assert all(record.data["priority"] == 0 for record in rolled_back)

    succeeded = _execute(
        store,
        request,
        "private-effect-quota",
        capture_effects=False,
        max_logical_bytes=logical_limit,
    )
    assert succeeded["output"] == {"matched": 34, "affected": 34}
    assert "private_effect" not in succeeded


def test_capture_revision_conflict_persists_negative_effect_receipt(tmp_path):
    store, table, schema = _published_store(tmp_path)
    record = store.create_record(table.table_id, data={"title": "original"})
    store.update_record(
        table.table_id,
        record.record_id,
        revision=1,
        data={"note": "concurrent change"},
    )
    request = _request(
        table.table_id,
        schema,
        operation="update",
        data={"done": True},
        filter_tree={"field": "title", "operator": "eq", "value": "original"},
        expected_records=[{"record_id": record.record_id, "revision": 1}],
        writable_fields=["done"],
    )
    checksum = controlled_write_request_checksum(request)
    conflict = _execute(
        store, request, "captured-revision-conflict", capture_effects=True
    )
    assert conflict["output"] == {"matched": 0, "affected": 0}
    assert conflict["error_code"] == "DATA_TABLE_REVISION_CONFLICT"
    assert conflict["receipt"]["error_code"] == "DATA_TABLE_REVISION_CONFLICT"
    assert conflict["receipt"]["affected_count"] == 0
    assert conflict["private_effect"]["before_records"] == []
    assert conflict["private_effect"]["after_records"] == []
    assert (
        conflict["private_effect"]["untouched_before_checksum"]
        == conflict["private_effect"]["untouched_after_checksum"]
    )
    assert store.list_records(table.table_id)[0].revision == 2
    assert store.list_records(table.table_id)[0].data["done"] is False

    store.update_record(
        table.table_id,
        record.record_id,
        revision=2,
        data={"title": "changed after conflict receipt"},
    )
    restarted = AgentTableStore(tmp_path)
    recovered = restarted.get_controlled_operation(
        table.table_id,
        "captured-revision-conflict",
        request_checksum=checksum,
    )
    assert recovered == {**conflict, "replayed": True}
    assert _execute(restarted, request, "captured-revision-conflict") == recovered

    missing = dict(request)
    missing["expected_records"] = [
        {"record_id": "record_missing", "revision": 1}
    ]
    missing_checksum = controlled_write_request_checksum(missing)
    with pytest.raises(AgentTableConflictError, match="missing or deleted"):
        _execute(
            restarted,
            missing,
            "missing-is-not-revision-conflict",
            capture_effects=True,
        )
    assert restarted.get_controlled_operation(
        table.table_id,
        "missing-is-not-revision-conflict",
        request_checksum=missing_checksum,
    ) is None


def test_evaluation_initialization_creates_fixed_tables_and_private_ref_map(tmp_path):
    backend = SQLiteAgentTableBackend(tmp_path)
    snapshot = _evaluation_table(
        [
            {"ref": "first", "data": {"title": "Alpha"}},
            {
                "ref": "second",
                "data": {"title": "Beta", "priority": 2},
            },
        ]
    )
    checksum = _initialization_checksum()

    result = backend.initialize_evaluation_tables(
        [snapshot], initialization_checksum=checksum
    )

    assert result["initialization_checksum"] == checksum
    mapping = result["tables"]["evaluation_tasks"]
    assert set(mapping) == {"first", "second"}
    assert all(record_id.startswith("record_") for record_id in mapping.values())
    assert set(mapping.values()).isdisjoint(mapping)
    table = backend.get_table("evaluation_tasks")
    schema = backend.get_schema_version("evaluation_tasks", 7)
    assert table.status == "published"
    assert table.active_schema_version == 7
    assert schema.checksum == snapshot["schema_checksum"]
    records = {record.record_id: record for record in backend.list_records(table.table_id)}
    assert records[mapping["first"]].revision == 1
    assert records[mapping["first"]].data == {"title": "Alpha", "priority": 0}


def test_evaluation_initialization_replays_after_restart_and_rejects_new_checksum(
    tmp_path,
):
    snapshot = _evaluation_table(
        [{"ref": "stable", "data": {"title": "Stable"}}]
    )
    checksum = _initialization_checksum()
    first_backend = SQLiteAgentTableBackend(tmp_path)
    first = first_backend.initialize_evaluation_tables(
        [snapshot], initialization_checksum=checksum
    )

    restarted = SQLiteAgentTableBackend(tmp_path)
    replay = restarted.initialize_evaluation_tables(
        [snapshot], initialization_checksum=checksum, require_existing=True
    )
    assert replay == first
    assert len(restarted.list_records("evaluation_tasks")) == 1

    with pytest.raises(AgentTableConflictError, match="different checksum"):
        restarted.initialize_evaluation_tables(
            [snapshot], initialization_checksum=_initialization_checksum("fixture-v2")
        )
    with pytest.raises(AgentTableConflictError, match="初始化表内容"):
        restarted.initialize_evaluation_tables([], initialization_checksum=checksum)


def test_evaluation_recovery_requires_existing_initialization_marker(tmp_path):
    backend = SQLiteAgentTableBackend(tmp_path)
    snapshot = _evaluation_table(
        [{"ref": "must-not-seed", "data": {"title": "Must not seed"}}]
    )
    with pytest.raises(AgentTableConflictError, match="existing initialization marker"):
        backend.initialize_evaluation_tables(
            [snapshot],
            initialization_checksum=_initialization_checksum("missing-marker"),
            require_existing=True,
        )
    assert backend.list_tables() == []

    with pytest.raises(AgentTableValidationError, match="must be a boolean"):
        backend.initialize_evaluation_tables(
            [snapshot],
            initialization_checksum=_initialization_checksum("invalid-flag"),
            require_existing=1,
        )
    assert backend.list_tables() == []


@pytest.mark.parametrize("forged_key", ["record_id", "revision", "created_at", "updated_at"])
def test_evaluation_initialization_rejects_record_metadata_forgery(
    tmp_path, forged_key
):
    backend = SQLiteAgentTableBackend(tmp_path)
    record = {"ref": "forged", "data": {"title": "No"}, forged_key: "forged"}
    with pytest.raises(AgentTableValidationError, match="only ref and data"):
        backend.initialize_evaluation_tables(
            [_evaluation_table([record])],
            initialization_checksum=_initialization_checksum(forged_key),
        )
    assert backend.list_tables() == []


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"title": "Wrong type", "priority": "high"}, "must be an integer"),
        ({"title": "Unknown", "other": True}, "Unknown record fields"),
        ({"title": "System", "record_id": "injected"}, "Unknown record fields"),
    ],
)
def test_evaluation_initialization_validates_record_data_and_rolls_back(
    tmp_path, data, message
):
    backend = SQLiteAgentTableBackend(tmp_path)
    snapshot = _evaluation_table([{"ref": "bad", "data": data}])
    with pytest.raises(AgentTableValidationError, match=message):
        backend.initialize_evaluation_tables(
            [snapshot], initialization_checksum=_initialization_checksum(message)
        )
    assert backend.list_tables() == []


def test_evaluation_initialization_rejects_existing_business_database(tmp_path):
    backend = SQLiteAgentTableBackend(tmp_path)
    existing = backend.create_table(name="Existing", fields=_fields())
    snapshot = _evaluation_table(
        [{"ref": "new", "data": {"title": "Must not overwrite"}}],
        table_id=existing.table_id,
    )
    with pytest.raises(AgentTableConflictError, match="empty business database"):
        backend.initialize_evaluation_tables(
            [snapshot], initialization_checksum=_initialization_checksum()
        )
    assert backend.get_table(existing.table_id).name == "Existing"


def test_evaluation_initialization_rechecks_schema_checksum_and_rolls_back(tmp_path):
    backend = SQLiteAgentTableBackend(tmp_path)
    valid = _evaluation_table(
        [{"ref": "valid", "data": {"title": "Valid"}}],
        table_id="evaluation_valid",
    )
    invalid = _evaluation_table(
        [{"ref": "invalid", "data": {"title": "Invalid"}}],
        table_id="evaluation_invalid",
    )
    invalid["schema_checksum"] = "0" * 64
    with pytest.raises(AgentTableConflictError, match="schema checksum"):
        backend.initialize_evaluation_tables(
            [valid, invalid], initialization_checksum=_initialization_checksum()
        )
    assert backend.list_tables() == []


def test_evaluation_initialization_record_count_boundary(tmp_path):
    records = [
        {"ref": f"ref-{index}", "data": {"title": f"Record {index}"}}
        for index in range(200)
    ]
    backend = SQLiteAgentTableBackend(tmp_path / "accepted")
    result = backend.initialize_evaluation_tables(
        [_evaluation_table(records)],
        initialization_checksum=_initialization_checksum("two-hundred"),
    )
    assert len(result["tables"]["evaluation_tasks"]) == 200
    assert len(backend.list_records("evaluation_tasks", limit=500)) == 200

    rejected = SQLiteAgentTableBackend(tmp_path / "rejected")
    with pytest.raises(AgentTableValidationError, match="at most 200 records"):
        rejected.initialize_evaluation_tables(
            [
                _evaluation_table(
                    records
                    + [{"ref": "ref-200", "data": {"title": "Record 200"}}]
                )
            ],
            initialization_checksum=_initialization_checksum("two-hundred-one"),
        )
    assert rejected.list_tables() == []


def test_evaluation_initialization_logical_16_mib_limit_rolls_back(tmp_path):
    backend = SQLiteAgentTableBackend(tmp_path)
    records = [
        {
            "ref": f"large-{index}",
            "data": {"title": f"{index}:" + ("x" * 250_000)},
        }
        for index in range(68)
    ]
    with pytest.raises(AgentTableValidationError, match="16 MiB"):
        backend.initialize_evaluation_tables(
            [_evaluation_table(records)],
            initialization_checksum=_initialization_checksum("over-16-mib"),
        )
    assert backend.list_tables() == []
