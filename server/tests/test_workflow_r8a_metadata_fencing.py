"""Fixed execution metadata ownership; real isolated Stores, no Provider calls."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from server.xpert_runtime import execution_store as store_module
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError, WorkflowExecutionStore


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock.now))
    store = WorkflowExecutionStore(tmp_path / "executions")
    store.create(task_id="task", run_id="run", run_type="workflow", workflow={}, inputs={})
    return store, clock


def mutate(store, operation, *, value="fixed", **lease):
    if operation == "skill":
        return store.bind_skill_versions("task", bindings={"skill": value}, **lease)
    return store.freeze_private_write_entry("task", "request:write", {"value": value}, **lease)


@pytest.mark.parametrize("operation", ["skill", "journal"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("ownership", ["omitted", "empty", "expired", "replaced"])
def test_unowned_metadata_rejected_even_when_value_is_unchanged(runtime, operation, existing, ownership):
    store, clock = runtime
    if existing:
        mutate(store, operation)
    old = store.claim("task", worker_id="old", lease_seconds=5)
    if ownership in {"expired", "replaced"}:
        clock.now += 10
        if ownership == "replaced":
            store.claim("task", worker_id="new")
    lease = {} if ownership == "omitted" else {"expected_lease_token": "" if ownership == "empty" else old.lease_token}
    before, raw = asdict(store.snapshot("task")), store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        mutate(store, operation, **lease)
    assert asdict(store.snapshot("task")) == before
    assert store.snapshot_path.read_bytes() == raw


@pytest.mark.parametrize("operation", ["skill", "journal"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("status", ["waiting", "completed", "failed", "cancelled"])
def test_inactive_execution_cannot_use_metadata_write_interface(runtime, operation, existing, status):
    store, _ = runtime
    if existing:
        mutate(store, operation)
    if status == "waiting":
        store.suspend("task", wait_kind="approval", wait_id="approval", continuation={})
    elif status == "completed":
        store.complete("task", result="done")
    elif status == "failed":
        store.fail("task", error="SYNTHETIC")
    else:
        store.cancel("task")
    before, raw = asdict(store.snapshot("task")), store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        mutate(store, operation)
    assert asdict(store.snapshot("task")) == before
    assert store.snapshot_path.read_bytes() == raw


@pytest.mark.parametrize("operation", ["skill", "journal"])
def test_initial_and_current_owner_persist_immutable_detached_values(runtime, operation):
    store, _ = runtime
    mutate(store, operation)
    claimed = store.claim("task", worker_id="owner")
    before = store.snapshot_path.read_bytes()
    result = mutate(store, operation, expected_lease_token=claimed.lease_token)
    assert store.snapshot_path.read_bytes() == before
    if operation == "skill":
        result.runtime_metadata["skill_version_bindings"]["skill"] = "tampered"
        assert store.snapshot("task").runtime_metadata["skill_version_bindings"]["skill"] == "fixed"
    else:
        result["value"] = "tampered"
        assert store.read_private_write_entry("task", "request:write") == {"value": "fixed"}
    with pytest.raises(WorkflowExecutionConflictError):
        mutate(store, operation, value="changed", expected_lease_token=claimed.lease_token)
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.parametrize("operation", ["skill", "journal"])
@pytest.mark.parametrize("existing", [False, True])
def test_restart_allows_only_existing_equal_value_without_claim(runtime, operation, existing):
    store, _ = runtime
    if existing:
        mutate(store, operation)
    restored = WorkflowExecutionStore(store.storage_dir)
    assert restored.snapshot("task").status == "ready"
    before, raw = asdict(restored.snapshot("task")), restored.snapshot_path.read_bytes()
    if existing:
        mutate(restored, operation)
    else:
        with pytest.raises(WorkflowExecutionConflictError):
            mutate(restored, operation)
    assert asdict(restored.snapshot("task")) == before
    assert restored.snapshot_path.read_bytes() == raw
    claimed = restored.claim("task", worker_id="resumed")
    mutate(restored, operation, expected_lease_token=claimed.lease_token)


@pytest.mark.parametrize("operation", ["skill", "journal"])
def test_new_value_return_does_not_expose_store_reference(runtime, operation):
    store, _ = runtime
    result = mutate(store, operation)
    before = asdict(store.snapshot("task"))
    if operation == "skill":
        result.runtime_metadata.clear()
    else:
        result.clear()
    assert asdict(store.snapshot("task")) == before


@pytest.mark.parametrize("entry", [
    "get", "require", "find", "list", "existing_create", "new_create",
    "suspend", "ready", "renew", "event", "rebind", "same_run",
    "complete", "cancel", "cancel_again", "reject", "fail_again", "invalidate",
])
def test_public_execution_objects_are_detached(runtime, entry):
    store, _ = runtime
    if entry == "get":
        item = store.get("task")
    elif entry == "require":
        item = store.require("task")
    elif entry == "find":
        item = store.find_by_run_id("run")
    elif entry == "list":
        item = store.list_items()[0]
    elif entry in {"existing_create", "new_create"}:
        item = store.create(task_id="task" if entry == "existing_create" else "new", run_id="run", run_type="workflow", workflow={}, inputs={})
    elif entry in {"suspend", "ready"}:
        item = store.suspend("task", wait_id="approval", continuation={"variables": {"n": [1]}})
        if entry == "ready":
            item = store.mark_ready("task", approval_id="approval")
    elif entry == "renew":
        claimed = store.claim("task", worker_id="owner")
        item = store.refresh_lease("task", lease_token=claimed.lease_token)
    elif entry == "event":
        item = store.append_event("task", {"event": "node_end", "node_id": "n"})
    elif entry in {"rebind", "same_run"}:
        item = store.update_run_id("task", run_id="next" if entry == "rebind" else "run")
    elif entry in {"complete", "invalidate"}:
        item = store.complete("task", result="done")
        if entry == "invalidate":
            item = store.fail("task", error="SYNTHETIC")
    elif entry in {"cancel", "cancel_again"}:
        item = store.cancel("task")
        if entry == "cancel_again":
            item = store.cancel("task")
    elif entry == "reject":
        item = store.reject("task")
    else:
        store.fail("task", error="SYNTHETIC")
        item = store.fail("task", error="LATE")
    task_id = item.task_id
    before, raw = asdict(store.snapshot(task_id)), store.snapshot_path.read_bytes()
    item.status = "running"
    item.lease_token = "forged"
    item.workflow["injected"] = True
    item.runtime_metadata["injected"] = {"data": [1]}
    item.events.append({"event": "forged"})
    item.continuation.setdefault("variables", {})["n"] = [99]
    assert asdict(store.snapshot(task_id)) == before
    assert store.snapshot_path.read_bytes() == raw


@pytest.mark.parametrize("entry", ["workflow", "inputs", "runtime_metadata", "continuation"])
def test_nested_input_alias_cannot_mutate_store_after_call(runtime, entry):
    store, _ = runtime
    nested = {"nested": {"values": [1]}}
    if entry == "continuation":
        task_id = "task"
        store.suspend(task_id, wait_id="approval", continuation=nested)
    else:
        task_id = "new"
        args = {"workflow": {}, "inputs": {}, "runtime_metadata": {}}
        args[entry] = nested
        store.create(task_id=task_id, run_id="new-run", run_type="workflow", **args)
    before, raw = asdict(store.snapshot(task_id)), store.snapshot_path.read_bytes()
    nested["nested"]["values"].append(99)
    assert asdict(store.snapshot(task_id)) == before
    assert store.snapshot_path.read_bytes() == raw


@pytest.mark.parametrize("ownership", ["current", "replaced", "expired", "cancelled"])
def test_controlled_runtime_keeps_captured_owner_for_new_request(runtime, tmp_path, ownership):
    from server.evaluations.resource_fixtures import _resolve_filter
    from server.tests.test_controlled_write_runtime import setup_table, workflow
    from server.workflow_native.controlled_writes import ControlledWriteRuntime

    store, clock = runtime
    backend, table_id, schema = setup_table(tmp_path)
    claimed = store.claim("task", worker_id="old", lease_seconds=5)
    runner = ControlledWriteRuntime(
        task_id="task", workflow=workflow(table_id, schema, "insert"),
        backend=backend, execution_store=store, expected_lease_token=claimed.lease_token,
    )
    if ownership in {"replaced", "expired"}:
        clock.now += 10
        if ownership == "replaced":
            store.claim("task", worker_id="new")
    elif ownership == "cancelled":
        store.cancel("task")
    before, raw = asdict(store.snapshot("task")), store.snapshot_path.read_bytes()
    if ownership == "current":
        result = runner.execute("write", {}, resolve_filter=_resolve_filter)
        assert result["receipt"]["affected_count"] == 1
        assert store.read_private_write_entry("task", "source:write") is not None
    else:
        with pytest.raises(WorkflowExecutionConflictError):
            runner.execute("write", {}, resolve_filter=_resolve_filter)
        assert backend.query_records(table_id, schema_version=1) == []
        assert asdict(store.snapshot("task")) == before
        assert store.snapshot_path.read_bytes() == raw


@pytest.mark.asyncio
async def test_real_resumed_runner_passes_owner_to_private_journal(tmp_path, monkeypatch, request):
    import server.main as main
    from server.tests.test_controlled_write_execution_integration import (
        _evaluation_context, _events,
        _full_write_workflow, _isolated_backend, _metadata, _published_business_table,
    )
    from server.xpert_runtime import RunRegistry
    from server.model_router.repository import SQLiteRouterRepository
    from server.model_router.service import ModelRouterService

    business, table_id, schema, sentinel = _published_business_table(tmp_path)
    isolated, checksum = _isolated_backend(tmp_path, business, table_id, schema)
    graph = _full_write_workflow(table_id, schema)
    store = WorkflowExecutionStore(tmp_path / "executions")
    store.create(
        task_id="resumed-write", run_id="old-run", run_type="xpert_evaluation",
        workflow=graph, inputs={"user_input": "synthetic"}, runtime_metadata=_metadata(),
    )
    store.suspend(
        "resumed-write", wait_kind="approval", wait_id="synthetic-continuation",
        continuation={"queue": ["input"], "queued": ["input"], "executed": []},
    )
    store.mark_ready("resumed-write", approval_id="synthetic-continuation")
    claimed = store.claim("resumed-write", worker_id="resume-worker")
    monkeypatch.setattr(main, "workflow_execution_store", store)
    monkeypatch.setattr(main, "workflow_task_store", {})
    monkeypatch.setattr(main, "run_registry", RunRegistry())
    monkeypatch.setattr(main, "agent_table_store", business)
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("", ""))
    router = ModelRouterService(SQLiteRouterRepository.open(tmp_path / "router", master_key=b"x" * 32))
    request.addfinalizer(router.repository.close)
    monkeypatch.setattr(main, "get_model_router_service", lambda: router)
    writes = []
    original = store.freeze_private_write_entry

    def checked_freeze(*args, **kwargs):
        assert kwargs.get("expected_lease_token") == claimed.lease_token
        writes.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "freeze_private_write_entry", checked_freeze)
    receipts = []
    response = await main._run_workflow_response(
        main.WorkflowRunRequest.model_validate({"workflow": graph, "inputs": {"user_input": "synthetic"}}),
        None, runtime_run_type="xpert_evaluation", runtime_source_id="evaluation-target",
        runtime_metadata=_metadata(), resume_execution=claimed,
        evaluation_write_backend=_evaluation_context(isolated, graph, checksum, receipts),
    )
    events = await _events(response)
    assert not [event for event in events if event.get("event") == "error"], events
    assert any(event.get("event") == "workflow_end" for event in events)
    assert set(writes) == {
        "request:insert", "source:insert", "source:query_after_insert",
        "request:update", "source:query_after_update", "request:delete",
    }
    assert [receipt["operation"] for receipt in receipts] == ["insert", "update", "delete"]
    assert store.snapshot("resumed-write").status == "completed"
    assert isolated.query_records(table_id, schema_version=1) == []
    assert business.query_records(table_id, schema_version=1) == [sentinel]
