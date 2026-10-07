import json
import hashlib

import pytest

from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStorageError,
    WorkflowExecutionStore,
)


LIMIT = 16 * 1024 * 1024


def _store(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    store.complete("task", result="done")
    return store


def _bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _journal_at_limit(extra=0):
    journal = {"request:write": {"value": ""}}
    journal["request:write"]["value"] = "x" * (LIMIT - len(_bytes(journal)) + extra)
    return journal


@pytest.mark.parametrize("boundary", ["load", "inspect", "persist"])
@pytest.mark.parametrize("invalid", [None, [], "private-text", {"request:write": None}, {"request:write": []}, {"": {}}])
def test_invalid_private_journal_never_becomes_trusted(tmp_path, boundary, invalid):
    store = _store(tmp_path)
    original = store.snapshot_path.read_bytes()
    payload = json.loads(original)
    payload["items"][0]["private_write_journal"] = invalid
    raw = _bytes(payload)
    with pytest.raises(WorkflowExecutionStorageError):
        if boundary == "inspect":
            store.inspect_snapshot_bytes(raw)
        elif boundary == "load":
            store.snapshot_path.write_bytes(raw)
            WorkflowExecutionStore(tmp_path)
        else:
            store.require("task").private_write_journal = invalid
            store._persist_unlocked()
    if boundary == "persist":
        assert store.available is False
        assert store.snapshot_path.read_bytes() == original


@pytest.mark.parametrize("boundary", ["load", "inspect"])
@pytest.mark.parametrize("limit", ["count", "bytes"])
def test_oversized_journal_cannot_bypass_freeze_on_recovery(tmp_path, boundary, limit):
    store = _store(tmp_path)
    payload = json.loads(store.snapshot_path.read_bytes())
    payload["items"][0]["private_write_journal"] = (
        {f"request:{index}": {} for index in range(1001)}
        if limit == "count" else _journal_at_limit(1)
    )
    raw = _bytes(payload)
    with pytest.raises(WorkflowExecutionStorageError):
        if boundary == "inspect":
            store.inspect_snapshot_bytes(raw)
        else:
            store.snapshot_path.write_bytes(raw)
            WorkflowExecutionStore(tmp_path)


@pytest.mark.parametrize("key,value", [(17, {}), ("", {}), ("request:write", []), ("request:write", None)])
def test_freeze_rejects_invalid_entry_without_mutation(tmp_path, key, value):
    store = _store(tmp_path)
    before = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        store.freeze_private_write_entry("task", key, value)
    assert store.require("task").private_write_journal == {}
    assert store.snapshot_path.read_bytes() == before
    assert store.available is True


@pytest.mark.parametrize("limit", ["count", "bytes"])
def test_exact_limit_remains_valid_and_next_entry_is_rejected(tmp_path, limit):
    store = _store(tmp_path)
    journal = (
        {f"request:{index}": {} for index in range(1000)}
        if limit == "count" else _journal_at_limit()
    )
    store.require("task").private_write_journal = journal
    store._persist_unlocked()
    restored = WorkflowExecutionStore(tmp_path)
    assert restored.require("task").private_write_journal == journal
    before = restored.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        restored.freeze_private_write_entry("task", "new", {})
    assert restored.snapshot_path.read_bytes() == before
    assert restored.available is True


def test_legacy_snapshot_defaults_to_empty_private_journal(tmp_path):
    store = _store(tmp_path)
    payload = json.loads(store.snapshot_path.read_bytes())
    del payload["items"][0]["private_write_journal"]
    store.snapshot_path.write_bytes(_bytes(payload))
    restored = WorkflowExecutionStore(tmp_path)
    assert restored.require("task").private_write_journal == {}
    assert restored.inspect_snapshot_bytes(store.snapshot_path.read_bytes())["item_count"] == 1


def test_private_freeze_preserves_backup_and_replay_without_public_content(tmp_path):
    store = _store(tmp_path)
    original = store.snapshot_path.read_bytes()
    value = {"request_checksum": "a" * 64, "values": {"title": "PRIVATE_SENTINEL"}}
    frozen = store.freeze_private_write_entry("task", "request:write", value)
    assert store.backup_path.read_bytes() == original
    current = store.snapshot_path.read_bytes()
    value["values"]["title"] = "changed"
    restored = WorkflowExecutionStore(tmp_path)
    assert restored.freeze_private_write_entry("task", "request:write", frozen) == frozen
    assert store.snapshot_path.read_bytes() == current
    with pytest.raises(WorkflowExecutionConflictError):
        restored.freeze_private_write_entry("task", "request:write", value)
    public = json.dumps(restored.serialize_public(restored.require("task")))
    inspection = json.dumps(restored.inspect_snapshot_bytes(current))
    assert "PRIVATE_SENTINEL" not in public + inspection
    assert "private_write_journal" not in public + inspection


def test_write_failure_rolls_back_journal_and_disables_store(tmp_path, monkeypatch):
    store = _store(tmp_path)
    item = store.require("task")
    before = store.snapshot_path.read_bytes()

    def fail_write(*_args):
        raise OSError("PRIVATE_SENTINEL")

    monkeypatch.setattr(store, "_write_fsynced", fail_write)
    with pytest.raises(WorkflowExecutionStorageError) as failure:
        store.freeze_private_write_entry("task", "request:write", {"value": "private"})
    assert "PRIVATE_SENTINEL" not in str(failure.value)
    assert item.private_write_journal == {}
    assert store.snapshot_path.read_bytes() == before
    assert store.available is False
    with pytest.raises(WorkflowExecutionStorageError):
        store.read_private_write_entry("task", "request:write")


@pytest.mark.parametrize("change_request", [False, True])
def test_older_backup_cannot_duplicate_a_committed_write(tmp_path, monkeypatch, change_request):
    from copy import deepcopy

    from server.tests.test_controlled_write_runtime import runtime, setup_table, workflow
    from server.evaluations.resource_fixtures import _resolve_filter
    from server.workflow_native.controlled_writes import ControlledWriteRuntime
    from server.xpert_runtime.execution_store_recovery import restore_backup

    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert")
    runner, store = runtime(tmp_path, backend, graph)
    original_write = store._write_fsynced

    def fail_after_backend_commit(path, content):
        if b'"source:write"' in content:
            raise OSError("simulated checkpoint failure after commit")
        return original_write(path, content)

    monkeypatch.setattr(store, "_write_fsynced", fail_after_backend_commit)
    with pytest.raises(WorkflowExecutionStorageError):
        runner.execute("write", {}, resolve_filter=_resolve_filter)
    original_rows = backend.query_records(table_id, schema_version=1)
    assert len(original_rows) == 1
    backup = store.backup_path.read_bytes()
    assert json.loads(backup)["items"][0]["private_write_journal"] == {}
    store.snapshot_path.write_bytes(b"damaged snapshot")
    restore_backup(
        store.storage_dir,
        expected_primary_sha256=hashlib.sha256(b"damaged snapshot").hexdigest(),
        expected_backup_sha256=hashlib.sha256(backup).hexdigest(),
    )
    restored_graph = deepcopy(graph)
    if change_request:
        restored_graph["nodes"][-2]["data"]["literalValues"]["title"] = "different"
    recovered = ControlledWriteRuntime(
        task_id="task-fixed", workflow=restored_graph, backend=backend,
        execution_store=WorkflowExecutionStore(store.storage_dir),
    )
    if change_request:
        from server.data_tables.store import AgentTableConflictError

        with pytest.raises(AgentTableConflictError):
            recovered.execute("write", {}, resolve_filter=_resolve_filter)
    else:
        result = recovered.execute("write", {}, resolve_filter=_resolve_filter)
        assert result["replayed"] is True
        assert result["output"] == original_rows[0]
    assert backend.query_records(table_id, schema_version=1) == original_rows
