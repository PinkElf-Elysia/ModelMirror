from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import traceback
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

from server.xpert_runtime import execution_store_recovery as recovery_module
from server.xpert_runtime.execution_store import (
    UnavailableWorkflowExecutionStore,
    WorkflowExecutionConflictError,
    WorkflowExecutionStorageError,
    WorkflowExecutionStore,
)
from server.xpert_runtime.execution_store_recovery import (
    RecoveryCommandError,
    inspect_storage,
    main as recovery_main,
    restore_backup,
)


def _create_wait(store, task_id="task-retry", *, wait_kind="node_retry"):
    store.create(
        task_id=task_id,
        run_id=f"run-{task_id}",
        run_type="workflow",
        workflow={"nodes": [{"id": "node-1"}]},
        inputs={},
    )
    return store.suspend(
        task_id,
        wait_kind=wait_kind,
        wait_id=f"{wait_kind}:{task_id}",
        resume_at=100 if wait_kind in {"timer", "node_retry"} else None,
        continuation={"queue": ["node-1"]},
    )


def _claim(store, *, worker="worker-a", now=100):
    return store.claim_due_wait(
        "task-retry",
        wait_kind="node_retry",
        wait_id="node_retry:task-retry",
        worker_id=worker,
        lease_seconds=5,
        now=now,
    )


def test_cancelled_execution_cannot_be_resuspended_or_reclassified(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    store.cancel("task-retry")
    with pytest.raises(WorkflowExecutionConflictError):
        store.suspend(
            "task-retry",
            wait_kind="node_retry",
            wait_id="node_retry:next",
            resume_at=200,
            continuation={},
        )
    with pytest.raises(WorkflowExecutionConflictError):
        _claim(store)
    store.complete("task-retry", result="late success")
    store.fail("task-retry", error="late failure")
    store.reject("task-retry", error="late rejection")
    assert store.require("task-retry").status == "cancelled"
    assert store.require("task-retry").error == "cancelled"


@pytest.mark.parametrize("wait_kind", ["approval", "agent_handoff"])
@pytest.mark.parametrize("final_action", ["suspend", "complete", "fail"])
def test_legacy_wait_callers_remain_compatible_without_explicit_token(
    tmp_path, wait_kind, final_action
):
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store, wait_kind=wait_kind)
    store.mark_ready(
        "task-retry", wait_kind=wait_kind, wait_id=f"{wait_kind}:task-retry"
    )
    store.claim("task-retry", worker_id="legacy-worker")
    if final_action == "suspend":
        store.suspend(
            "task-retry", wait_kind=wait_kind, wait_id="next-wait", continuation={}
        )
        assert store.require("task-retry").status == "waiting"
    elif final_action == "complete":
        store.complete("task-retry", result="done")
        assert store.require("task-retry").status == "completed"
    else:
        store.fail("task-retry", error="safe failure")
        assert store.require("task-retry").status == "failed"


@pytest.mark.parametrize(
    "action", ["suspend", "complete", "fail", "event", "refresh", "assert"]
)
def test_reclaimed_lease_fences_stale_worker_writes(tmp_path, monkeypatch, action):
    monkeypatch.setattr("server.xpert_runtime.execution_store.time.time", lambda: 106)
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    first = _claim(store)
    second = _claim(store, worker="worker-b", now=106)
    before = asdict(store.require("task-retry"))
    with pytest.raises(WorkflowExecutionConflictError):
        if action == "suspend":
            store.suspend(
                "task-retry", wait_kind="node_retry", wait_id="next",
                resume_at=200, continuation={}, expected_lease_token=first.lease_token,
            )
        elif action == "complete":
            store.complete("task-retry", result="late", expected_lease_token=first.lease_token)
        elif action == "fail":
            store.fail("task-retry", error="late", expected_lease_token=first.lease_token)
        elif action == "event":
            store.append_event(
                "task-retry", {"event": "node_retry_started", "attempt": 2},
                expected_lease_token=first.lease_token,
            )
        elif action == "assert":
            store.assert_lease("task-retry", lease_token=first.lease_token)
        else:
            store.refresh_lease("task-retry", lease_token=first.lease_token)
    assert asdict(store.require("task-retry")) == before
    assert store.require("task-retry").lease_token == second.lease_token


@pytest.mark.parametrize("action", ["complete", "fail", "event"])
def test_terminal_winner_still_fences_stale_worker(tmp_path, monkeypatch, action):
    clock = [100]
    monkeypatch.setattr(
        "server.xpert_runtime.execution_store.time.time", lambda: clock[0]
    )
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    first = _claim(store)
    clock[0] = 106
    second = _claim(store, worker="worker-b", now=clock[0])
    winner = store.complete(
        "task-retry",
        result="winner",
        expected_lease_token=second.lease_token,
    )
    assert winner.status == "completed"

    with pytest.raises(WorkflowExecutionConflictError):
        if action == "complete":
            store.complete(
                "task-retry",
                result="stale",
                expected_lease_token=first.lease_token,
            )
        elif action == "fail":
            store.fail(
                "task-retry",
                error="stale",
                expected_lease_token=first.lease_token,
            )
        else:
            store.append_event(
                "task-retry",
                {"event": "workflow_end", "final_output": "stale"},
                expected_lease_token=first.lease_token,
            )

    stored = store.require("task-retry")
    assert stored.status == "completed"
    assert stored.result == "winner"
    assert stored.error is None
    assert stored.events == []


def test_unfenced_late_failure_marks_source_invalid_without_overwriting_result(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-source",
        run_id="run-source",
        run_type="workflow",
        source_kind="workflow_classic",
        workflow={"id": "workflow-source", "nodes": []},
        inputs={},
    )
    completed = store.complete("task-source", result="trusted result")
    completed_revision = completed.revision

    invalidated = store.fail("task-source", error="private late failure")

    assert invalidated.status == "completed"
    assert invalidated.result == "trusted result"
    assert invalidated.error is None
    assert invalidated.revision == completed_revision + 1
    assert invalidated.runtime_metadata["terminal_source_invalidated"] is True
    reloaded = WorkflowExecutionStore(tmp_path).require("task-source")
    assert reloaded.status == "completed"
    assert reloaded.runtime_metadata["terminal_source_invalidated"] is True
    assert "terminal_source_invalidated" not in WorkflowExecutionStore.serialize_public(
        reloaded
    )


def test_due_claim_is_a_deep_copy_and_refresh_rejects_expired_or_empty_token(
    tmp_path, monkeypatch
):
    clock = [100]
    monkeypatch.setattr("server.xpert_runtime.execution_store.time.time", lambda: clock[0])
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    claimed = _claim(store)
    claimed.continuation["queue"].append("malicious-node")
    claimed.workflow["nodes"][0]["id"] = "mutated"
    claimed.lease_token = "mutated"
    assert store.require("task-retry").continuation == {"queue": ["node-1"]}
    assert store.require("task-retry").workflow["nodes"][0]["id"] == "node-1"
    token = store.require("task-retry").lease_token
    with pytest.raises(WorkflowExecutionConflictError):
        store.refresh_lease("task-retry", lease_token="")
    clock[0] = 105
    with pytest.raises(WorkflowExecutionConflictError):
        store.refresh_lease("task-retry", lease_token=token)


def test_run_id_rebind_requires_the_current_live_resume_lease(tmp_path, monkeypatch):
    clock = [100]
    monkeypatch.setattr("server.xpert_runtime.execution_store.time.time", lambda: clock[0])
    store = WorkflowExecutionStore(tmp_path)
    _create_wait(store)
    claimed = _claim(store)
    store.cancel("task-retry")

    with pytest.raises(WorkflowExecutionConflictError):
        store.update_run_id(
            "task-retry",
            run_id="run-after-cancel",
            expected_lease_token=claimed.lease_token,
        )

    cancelled = store.require("task-retry")
    assert cancelled.status == "cancelled"
    assert cancelled.run_id == "run-task-retry"
    assert cancelled.previous_run_ids == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("resume_at", "bad-timestamp"), ("resume_at", float("nan")),
        ("resume_at", True), ("resume_at", None),
        ("lease_expires_at", "bad-lease"), ("lease_expires_at", float("nan")),
        ("lease_expires_at", None), ("lease_expires_at", True),
        ("wait_id", []), ("wait_id", ""), ("wait_id", 123),
    ],
)
def test_one_invalid_due_wait_is_quarantined_without_blocking_timer(tmp_path, field, value):
    store = WorkflowExecutionStore(tmp_path)
    bad = _create_wait(store)
    _create_wait(store, "task-timer", wait_kind="timer")
    setattr(bad, field, value)
    bad.status = "running"
    assert [item.task_id for item in store.list_due_waits(now=200)] == ["task-timer"]
    assert store.require("task-retry").status == "failed"
    assert store.require("task-retry").error == "WORKFLOW_WAIT_STATE_INVALID"
    assert store.require("task-timer").status == "waiting"


def test_one_bad_snapshot_record_blocks_the_entire_snapshot(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    first = _create_wait(store, "task-first", wait_kind="timer")
    last = _create_wait(store, "task-last", wait_kind="timer")
    corrupt = json.dumps({"version": "workflow-executions-v1", "items": [
        asdict(first), {"task_id": "broken-record"}, asdict(last),
    ]})
    store.snapshot_path.write_text(
        corrupt, encoding="utf-8",
    )
    with pytest.raises(WorkflowExecutionStorageError):
        WorkflowExecutionStore(tmp_path)
    assert store.snapshot_path.read_text(encoding="utf-8") == corrupt


def test_loaded_structurally_invalid_wait_blocks_the_entire_snapshot(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    bad = _create_wait(store)
    valid = _create_wait(store, "task-timer", wait_kind="timer")
    bad_raw = asdict(bad)
    bad_raw["lease_expires_at"] = "malformed-lease"
    store.snapshot_path.write_text(
        json.dumps({"version": "workflow-executions-v1", "items": [bad_raw, asdict(valid)]}),
        encoding="utf-8",
    )
    with pytest.raises(WorkflowExecutionStorageError):
        WorkflowExecutionStore(tmp_path)


def test_invalid_suspend_does_not_partially_change_running_execution(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    before = asdict(store.require("task"))
    with pytest.raises(WorkflowExecutionConflictError):
        store.suspend("task", wait_kind="node_retry", wait_id="", continuation={})
    assert asdict(store.require("task")) == before


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        json.dumps({"items": []}).encode(),
        json.dumps({"version": "workflow-executions-v2", "items": []}).encode(),
        json.dumps({"version": "workflow-executions-v1", "items": {}}).encode(),
        b'{"version":"workflow-executions-v1","items":[],"extra":true}',
        b'{"version":"workflow-executions-v1","items":[NaN]}',
    ],
)
def test_invalid_snapshot_never_becomes_an_empty_store(tmp_path, payload):
    snapshot = tmp_path / "workflow_executions.json"
    snapshot.write_bytes(payload)
    with pytest.raises(WorkflowExecutionStorageError):
        WorkflowExecutionStore(tmp_path)
    assert snapshot.read_bytes() == payload


def test_semantically_invalid_due_wait_is_safely_failed_after_snapshot_validation(
    tmp_path,
):
    store = WorkflowExecutionStore(tmp_path)
    bad = _create_wait(store)
    valid = _create_wait(store, "task-timer", wait_kind="timer")
    bad_raw = asdict(bad)
    bad_raw["wait_id"] = ""
    snapshot = {
        "version": "workflow-executions-v1",
        "items": [bad_raw, asdict(valid)],
    }
    store.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    restored = WorkflowExecutionStore(tmp_path)

    assert restored.require("task-retry").status == "failed"
    assert restored.require("task-retry").error == "WORKFLOW_WAIT_STATE_INVALID"
    assert [item.task_id for item in restored.list_due_waits(now=200)] == [
        "task-timer"
    ]


def test_each_successful_replace_preserves_the_previous_valid_primary(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-first",
        run_id="run-first",
        run_type="workflow",
        workflow={},
        inputs={},
    )
    first_primary = store.snapshot_path.read_bytes()
    store.create(
        task_id="task-second",
        run_id="run-second",
        run_type="workflow",
        workflow={},
        inputs={},
    )

    assert store.backup_path.read_bytes() == first_primary
    assert WorkflowExecutionStore.inspect_snapshot_bytes(first_primary)["item_count"] == 1
    assert WorkflowExecutionStore.inspect_snapshot_bytes(
        store.snapshot_path.read_bytes()
    )["item_count"] == 2


def test_external_primary_corruption_fails_closed_before_overwrite(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-first",
        run_id="run-first",
        run_type="workflow",
        workflow={},
        inputs={},
    )
    corrupt = b"corrupt-primary-sentinel"
    store.snapshot_path.write_bytes(corrupt)

    with pytest.raises(WorkflowExecutionStorageError):
        store.create(
            task_id="task-second",
            run_id="run-second",
            run_type="workflow",
            workflow={},
            inputs={},
        )
    assert store.snapshot_path.read_bytes() == corrupt
    assert store.available is False
    with pytest.raises(WorkflowExecutionStorageError):
        store.get("task-first")


def test_inspect_and_hash_pinned_restore_never_expose_snapshot_contents(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-secret",
        run_id="run-secret",
        run_type="workflow",
        workflow={"secret": "UNIQUE_STORE_SENTINEL"},
        inputs={},
    )
    store.create(
        task_id="task-next",
        run_id="run-next",
        run_type="workflow",
        workflow={},
        inputs={},
    )
    backup_bytes = store.backup_path.read_bytes()
    corrupt_bytes = b"UNIQUE_CORRUPT_SENTINEL"
    store.snapshot_path.write_bytes(corrupt_bytes)

    inspection = inspect_storage(tmp_path)
    serialized_inspection = json.dumps(inspection)
    assert inspection["primary"]["valid"] is False
    assert inspection["backup"]["valid"] is True
    assert "UNIQUE_STORE_SENTINEL" not in serialized_inspection
    assert "UNIQUE_CORRUPT_SENTINEL" not in serialized_inspection

    result = restore_backup(
        tmp_path,
        expected_primary_sha256=hashlib.sha256(corrupt_bytes).hexdigest(),
        expected_backup_sha256=hashlib.sha256(backup_bytes).hexdigest(),
    )
    restored = WorkflowExecutionStore(tmp_path)
    assert restored.require("task-secret").task_id == "task-secret"
    assert result["status"] == "restored"
    assert result["item_count"] == 1
    assert len(list(tmp_path.glob("workflow_executions.corrupt.*.json"))) == 1


def test_restore_refuses_stale_hashes_and_valid_primary(tmp_path):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-one",
        run_id="run-one",
        run_type="workflow",
        workflow={},
        inputs={},
    )
    store.create(
        task_id="task-two",
        run_id="run-two",
        run_type="workflow",
        workflow={},
        inputs={},
    )
    primary_hash = hashlib.sha256(store.snapshot_path.read_bytes()).hexdigest()
    backup_hash = hashlib.sha256(store.backup_path.read_bytes()).hexdigest()
    with pytest.raises(RecoveryCommandError) as valid_error:
        restore_backup(
            tmp_path,
            expected_primary_sha256=primary_hash,
            expected_backup_sha256=backup_hash,
        )
    assert valid_error.value.code == "primary_snapshot_still_valid"

    store.snapshot_path.write_bytes(b"changed-primary")
    with pytest.raises(RecoveryCommandError) as stale_error:
        restore_backup(
            tmp_path,
            expected_primary_sha256="0" * 64,
            expected_backup_sha256=backup_hash,
        )
    assert stale_error.value.code == "primary_snapshot_changed"


def test_recovery_cli_inspect_is_content_free(tmp_path, capsys):
    store = WorkflowExecutionStore(tmp_path)
    store.create(
        task_id="task-cli-secret",
        run_id="run-cli-secret",
        run_type="workflow",
        workflow={"secret": "UNIQUE_CLI_SENTINEL"},
        inputs={},
    )

    assert recovery_main(["inspect", "--storage-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "inspected"
    assert payload["primary"]["valid"] is True
    assert "UNIQUE_CLI_SENTINEL" not in output
    assert str(tmp_path) not in output


def test_unavailable_store_facade_always_fails_with_fixed_error(tmp_path):
    store = UnavailableWorkflowExecutionStore(tmp_path)
    assert store.available is False
    with pytest.raises(
        WorkflowExecutionStorageError,
        match="^Workflow execution storage is unavailable\\.$",
    ):
        store.list_items()


def test_main_health_and_startup_fail_closed_when_store_is_unavailable(
    tmp_path, monkeypatch
):
    from server import main as main_module

    corrupt_dir = tmp_path / "corrupt-store"
    corrupt_dir.mkdir()
    (corrupt_dir / "workflow_executions.json").write_bytes(b"invalid")
    loaded = main_module.load_workflow_execution_store(corrupt_dir)
    assert loaded.available is False

    unavailable = UnavailableWorkflowExecutionStore(tmp_path)
    monkeypatch.setattr(main_module, "workflow_execution_store", unavailable)
    monkeypatch.setattr(main_module, "runtime_services_started", False)
    agency_recoveries = []
    monkeypatch.setattr(
        main_module.agency_execution_coordinator,
        "recover_interrupted",
        lambda: agency_recoveries.append("agency"),
    )
    real_start_workflow_services = main_module._start_workflow_execution_services
    started = []

    async def start_independent():
        started.append("independent")

    async def start_workflow_services():
        started.append("workflow")
        return False

    monkeypatch.setattr(
        main_module, "_start_independent_runtime_services", start_independent
    )
    monkeypatch.setattr(
        main_module, "_start_workflow_execution_services", start_workflow_services
    )

    response = asyncio.run(main_module.health())
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "degraded",
        "code": "workflow_execution_store_unavailable",
        "components": {"workflow_execution_store": "unavailable"},
    }
    asyncio.run(main_module.start_mcp_ttl_cleanup())
    assert started == ["independent", "workflow"]
    assert main_module.runtime_services_started is True

    invoked = []
    monkeypatch.setattr(
        main_module,
        "get_handoff_executor",
        lambda: invoked.append("handoff"),
    )
    assert asyncio.run(real_start_workflow_services()) is False
    assert invoked == []
    assert agency_recoveries == []

    fixed_error = asyncio.run(
        main_module.workflow_execution_storage_unavailable(
            None,
            WorkflowExecutionStorageError("UNIQUE_ERROR_SENTINEL"),
        )
    )
    assert fixed_error.status_code == 503
    assert "UNIQUE_ERROR_SENTINEL" not in fixed_error.body.decode("utf-8")

    assert main_module.request_requires_workflow_execution_store(
        "POST", "/api/workflow/run"
    )
    assert main_module.request_requires_workflow_execution_store(
        "POST", "/api/workflows/wf_1/versions/2/activate"
    )
    assert main_module.request_requires_workflow_execution_store(
        "POST", "/api/workflow-forms/form_1/submissions"
    )
    assert main_module.request_requires_workflow_execution_store(
        "GET", "/api/runtime/client-hosts"
    )
    assert not main_module.request_requires_workflow_execution_store(
        "GET", "/api/workflows/wf_1"
    )
    assert not main_module.request_requires_workflow_execution_store(
        "GET", "/api/models"
    )


@pytest.mark.parametrize("invalid_object", ['{"n":1e999}', '{"n":1,"n":2}'])
def test_nested_ambiguous_or_nonfinite_json_is_rejected_without_rewrite(
    tmp_path, invalid_object
):
    store = WorkflowExecutionStore(tmp_path)
    item = store.create(
        task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={}
    )
    record = asdict(item)
    record["inputs"] = "INPUT_PLACEHOLDER"
    content = json.dumps({"version": "workflow-executions-v1", "items": [record]})
    content = content.replace('"INPUT_PLACEHOLDER"', invalid_object).encode()
    store.snapshot_path.write_bytes(content)
    for validate in (
        lambda: WorkflowExecutionStore(tmp_path),
        lambda: WorkflowExecutionStore.inspect_snapshot_bytes(content),
    ):
        with pytest.raises(WorkflowExecutionStorageError):
            validate()
    assert store.snapshot_path.read_bytes() == content
    assert not store.backup_path.exists()


@pytest.mark.parametrize("invalid_source", [0, False, [], "xpert_chat"])
def test_snapshot_source_kind_is_not_silently_coerced(tmp_path, invalid_source):
    store = WorkflowExecutionStore(tmp_path)
    item = store.create(
        task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={}
    )
    record = asdict(item)
    record["source_kind"] = invalid_source
    content = json.dumps({"version": "workflow-executions-v1", "items": [record]}).encode()
    with pytest.raises(WorkflowExecutionStorageError):
        WorkflowExecutionStore.inspect_snapshot_bytes(content)


@pytest.mark.parametrize("failed_destination", ["backup", "primary"])
def test_failed_atomic_replace_keeps_previous_primary_and_disables_store(
    tmp_path, monkeypatch, failed_destination
):
    from server.xpert_runtime import execution_store as store_module

    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    primary = store.snapshot_path.read_bytes()
    destination = store.backup_path if failed_destination == "backup" else store.snapshot_path
    original_replace = store_module.os.replace

    def fail_selected_replace(source, target):
        if target == destination:
            raise OSError("UNIQUE_IO_ERROR_SENTINEL")
        original_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", fail_selected_replace)
    with pytest.raises(WorkflowExecutionStorageError) as failure:
        store.create(task_id="second", run_id="second", run_type="workflow", workflow={}, inputs={})
    assert "UNIQUE_IO_ERROR_SENTINEL" not in str(failure.value)
    assert "UNIQUE_IO_ERROR_SENTINEL" not in "".join(
        traceback.format_exception(failure.value)
    )
    assert store.snapshot_path.read_bytes() == primary
    if store.backup_path.exists():
        assert store.backup_path.read_bytes() == primary
    assert not list(tmp_path.glob("*.tmp"))
    assert store.available is False
    with pytest.raises(WorkflowExecutionStorageError):
        store.require("second")


@pytest.mark.parametrize("corrupt_backup", [False, True])
def test_written_temporary_bytes_are_verified_before_atomic_replace(
    tmp_path, monkeypatch, corrupt_backup
):
    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    original_primary = store.snapshot_path.read_bytes()
    original_write = WorkflowExecutionStore._write_fsynced

    def corrupt_written_bytes(path, content):
        original_write(path, content)
        if (".bak." in path.name) == corrupt_backup:
            path.write_bytes(b"UNIQUE_CORRUPT_WRITE_SENTINEL")

    monkeypatch.setattr(WorkflowExecutionStore, "_write_fsynced", staticmethod(corrupt_written_bytes))
    with pytest.raises(WorkflowExecutionStorageError):
        store.complete("task", result="completed")
    assert store.snapshot_path.read_bytes() == original_primary
    assert not store.backup_path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert store.available is False


def test_missing_primary_with_existing_backup_is_not_a_new_empty_store(tmp_path):
    backup = tmp_path / "workflow_executions.bak.json"
    backup.write_bytes(b'{"version":"workflow-executions-v1","items":[]}')
    original = backup.read_bytes()
    with pytest.raises(WorkflowExecutionStorageError, match="primary snapshot is missing"):
        WorkflowExecutionStore(tmp_path)
    assert backup.read_bytes() == original
    assert not (tmp_path / "workflow_executions.json").exists()


def test_snapshot_symlink_or_lstat_failure_never_becomes_an_empty_store(
    tmp_path, monkeypatch
):
    snapshot = tmp_path / "workflow_executions.json"
    try:
        snapshot.symlink_to(tmp_path / "missing-target")
    except OSError as exc:  # pragma: no cover - platform policy boundary
        pytest.skip(f"symlink unavailable: {exc.__class__.__name__}")
    with pytest.raises(WorkflowExecutionStorageError, match="not a regular file"):
        WorkflowExecutionStore(tmp_path)
    snapshot.unlink()

    original_lstat = type(snapshot).lstat

    def fail_snapshot_lstat(path, *args, **kwargs):
        if str(path).endswith("workflow_executions.json"):
            raise OSError("UNIQUE_LSTAT_SENTINEL")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(type(snapshot), "lstat", fail_snapshot_lstat)
    with pytest.raises(WorkflowExecutionStorageError) as failure:
        WorkflowExecutionStore(tmp_path)
    rendered = "".join(traceback.format_exception(failure.value))
    assert "UNIQUE_LSTAT_SENTINEL" not in rendered


def test_directory_sync_io_failure_is_not_reported_as_durable_success(tmp_path, monkeypatch):
    from server.xpert_runtime import execution_store as store_module

    # The Windows path does not support directory fsync; exercise the supported
    # POSIX branch with a deterministic descriptor and an actual EIO code.
    monkeypatch.setattr(store_module.os, "name", "posix")
    monkeypatch.setattr(store_module.os, "open", lambda *_args: 123)
    closed = []
    monkeypatch.setattr(store_module.os, "close", lambda descriptor: closed.append(descriptor))

    def fail_sync(_descriptor):
        raise OSError(errno.EIO, "UNIQUE_SYNC_ERROR_SENTINEL")

    monkeypatch.setattr(store_module.os, "fsync", fail_sync)
    with pytest.raises(OSError) as failure:
        WorkflowExecutionStore._fsync_directory(tmp_path)
    assert failure.value.errno == errno.EIO
    assert closed == [123]


def test_persist_directory_sync_failure_disables_store_without_leaking_io_error(
    tmp_path, monkeypatch
):
    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    previous = store.snapshot_path.read_bytes()

    def fail_directory_sync(_path):
        raise OSError(errno.EIO, "UNIQUE_DIRECTORY_SYNC_SENTINEL")

    monkeypatch.setattr(WorkflowExecutionStore, "_fsync_directory", staticmethod(fail_directory_sync))
    with pytest.raises(WorkflowExecutionStorageError) as failure:
        store.complete("task", result="done")
    rendered = "".join(traceback.format_exception(failure.value))
    assert "UNIQUE_DIRECTORY_SYNC_SENTINEL" not in rendered
    assert store.available is False
    assert store.backup_path.read_bytes() == previous
    assert WorkflowExecutionStore.inspect_snapshot_bytes(store.snapshot_path.read_bytes())[
        "item_count"
    ] == 1


def test_missing_primary_can_be_restored_create_only_from_explicit_backup(
    tmp_path, monkeypatch
):
    store = WorkflowExecutionStore(tmp_path)
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    backup_content = store.snapshot_path.read_bytes()
    store.backup_path.write_bytes(backup_content)
    store.snapshot_path.unlink()
    backup_hash = hashlib.sha256(backup_content).hexdigest()

    restored = restore_backup(
        tmp_path,
        expected_primary_sha256="missing",
        expected_backup_sha256=backup_hash,
    )
    assert store.snapshot_path.read_bytes() == backup_content
    assert restored["archived_corrupt_sha256"] is None
    assert restored["archived_corrupt_name"] is None
    assert not list(tmp_path.glob("*.corrupt.*.json"))

    store.snapshot_path.unlink()
    original_link = recovery_module.os.link

    def race_link(source, target):
        target.write_bytes(b"competing-primary")
        original_link(source, target)

    monkeypatch.setattr(recovery_module.os, "link", race_link)
    with pytest.raises(RecoveryCommandError) as race:
        restore_backup(
            tmp_path,
            expected_primary_sha256="missing",
            expected_backup_sha256=backup_hash,
        )
    assert race.value.code == "primary_snapshot_changed"
    assert store.snapshot_path.read_bytes() == b"competing-primary"


def test_restore_detects_changes_during_archive_and_does_not_replace_primary(
    tmp_path, monkeypatch
):
    store = WorkflowExecutionStore(tmp_path)
    for index in range(2):
        store.create(task_id=str(index), run_id=str(index), run_type="workflow", workflow={}, inputs={})
    corrupt = b"invalid-primary"
    replacement_corrupt = b"changed-after-inspection"
    store.snapshot_path.write_bytes(corrupt)
    inspection = inspect_storage(tmp_path)
    original_write = WorkflowExecutionStore._write_fsynced

    def change_after_archive(path, content):
        original_write(path, content)
        if ".corrupt." in path.name:
            store.snapshot_path.write_bytes(replacement_corrupt)

    monkeypatch.setattr(WorkflowExecutionStore, "_write_fsynced", staticmethod(change_after_archive))
    with pytest.raises(RecoveryCommandError) as failure:
        restore_backup(
            tmp_path,
            expected_primary_sha256=inspection["primary"]["sha256"],
            expected_backup_sha256=inspection["backup"]["sha256"],
        )
    assert failure.value.code == "snapshot_changed_before_restore"
    assert store.snapshot_path.read_bytes() == replacement_corrupt
    assert next(tmp_path.glob("*.corrupt.*.json")).read_bytes() == corrupt
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("available,scope_type", [(True, "http"), (False, "lifespan"), (False, "websocket")])
def test_availability_middleware_preserves_asgi_stream_and_callables(
    monkeypatch, available, scope_type
):
    from server import main as main_module

    monkeypatch.setattr(main_module, "workflow_execution_store", SimpleNamespace(available=available))
    scope = {"type": scope_type, "method": "POST", "path": "/api/workflow/run"}
    emitted = []
    chunks = [
        {"type": "http.response.start", "status": 200, "headers": []},
        {"type": "http.response.body", "body": b"data: first\n\n", "more_body": True},
        {"type": "http.response.body", "body": b"data: last\n\n", "more_body": False},
    ]

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        emitted.append(message)

    async def downstream(actual_scope, actual_receive, actual_send):
        assert actual_scope is scope
        assert actual_receive is receive
        assert actual_send is send
        for chunk in chunks:
            await actual_send(chunk)

    middleware = main_module.WorkflowExecutionAvailabilityMiddleware(downstream)
    asyncio.run(middleware(scope, receive, send))
    assert emitted == chunks


def test_real_asgi_requests_are_blocked_before_execution_and_keep_read_access(
    tmp_path, monkeypatch
):
    from server import main as main_module

    monkeypatch.setattr(main_module, "workflow_execution_store", UnavailableWorkflowExecutionStore(tmp_path))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main_module.app), base_url="http://test"
        ) as client:
            for path in (
                "/api/workflow/run", "/api/workflow/run/task/resume",
                "/api/workflows/wf/versions/1/activate",
                "/api/workflow-forms/form/submissions", "/api/workflow-hooks/hook",
                "/api/xperts/xpert/run", "/api/v1/xpert-apps/app/chat/completions",
                "/api/runtime/goals/goal/start", "/api/expert-team/dag-runs",
            ):
                response = await client.post(path, json={"sentinel": "PRIVATE_BODY_SENTINEL"})
                assert response.status_code == 503, path
                assert response.json()["code"] == "workflow_execution_store_unavailable"
                assert "PRIVATE_BODY_SENTINEL" not in response.text
            health = await client.get("/api/health")
            assert health.status_code == 503
            registry = await client.get("/api/workflow/node-registry")
            assert registry.status_code == 200

    asyncio.run(exercise())


@pytest.mark.parametrize("started", [False, True])
def test_shutdown_cleans_started_services_even_after_storage_failure(
    tmp_path, monkeypatch, started
):
    from server import main as main_module

    monkeypatch.setattr(main_module, "workflow_execution_store", UnavailableWorkflowExecutionStore(tmp_path))
    monkeypatch.setattr(main_module, "runtime_services_started", started)
    calls = []

    async def stop_steps(_steps):
        calls.append("shutdown")
        raise RuntimeError("safe shutdown failure")

    monkeypatch.setattr(main_module, "_stop_runtime_service_steps", stop_steps)
    if started:
        with pytest.raises(RuntimeError, match="safe shutdown failure"):
            asyncio.run(main_module.shutdown_mcp_sessions())
        assert calls == ["shutdown"]
        assert main_module.runtime_services_started is False
    else:
        asyncio.run(main_module.shutdown_mcp_sessions())
        assert calls == []


def test_shutdown_steps_continue_after_failure_and_raise_only_safe_summary():
    from server import main as main_module

    calls = []

    async def fail_first():
        calls.append("first")
        raise RuntimeError("UNIQUE_SHUTDOWN_SENTINEL")

    async def finish_second():
        calls.append("second")

    with pytest.raises(RuntimeError) as failure:
        asyncio.run(
            main_module._stop_runtime_service_steps(
                [("first", fail_first), ("second", finish_second)]
            )
        )
    assert calls == ["first", "second"]
    rendered = "".join(traceback.format_exception(failure.value))
    assert "UNIQUE_SHUTDOWN_SENTINEL" not in rendered


@pytest.mark.asyncio
async def test_runtime_store_failure_ends_stream_with_one_safe_error(tmp_path, monkeypatch):
    from server import main as main_module

    store = WorkflowExecutionStore(tmp_path / "executions")
    monkeypatch.setattr(main_module, "workflow_execution_store", store)
    payload = main_module.WorkflowRunRequest.model_validate(
        {
            "workflow": {
                "id": "store-failure-stream",
                "title": "store failure stream",
                "nodes": [
                    {
                        "id": "input",
                        "type": "input",
                        "data": {"kind": "input", "variableName": "user_input"},
                    },
                    {
                        "id": "output",
                        "type": "output",
                        "data": {"kind": "output", "outputVariable": "user_input"},
                    },
                ],
                "edges": [{"id": "e1", "source": "input", "target": "output"}],
            },
            "inputs": {"user_input": "safe input"},
        }
    )
    response = await main_module._run_workflow_response(payload, None)

    def fail_persist():
        store.available = False
        raise WorkflowExecutionStorageError("UNIQUE_STREAM_STORAGE_SENTINEL")

    monkeypatch.setattr(store, "_persist_unlocked", fail_persist)
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    rendered = "".join(chunks)
    assert rendered.count('"event": "error"') == 1
    assert "WORKFLOW_EXECUTION_STORE_UNAVAILABLE" in rendered
    assert "Workflow execution storage is unavailable." in rendered
    assert "UNIQUE_STREAM_STORAGE_SENTINEL" not in rendered
