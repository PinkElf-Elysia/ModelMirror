"""Cached controlled requests must retain the original execution owner."""
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from server.evaluations.resource_fixtures import _resolve_filter
from server.tests.test_controlled_write_runtime import setup_table, workflow
from server.workflow_native.controlled_writes import ControlledWriteRuntime
from server.xpert_runtime import execution_store as store_module
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError, WorkflowExecutionStore


def setup(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: clock.now))
    backend, table_id, schema = setup_table(tmp_path)
    graph = workflow(table_id, schema, "insert")
    store = WorkflowExecutionStore(tmp_path / "executions")
    store.create(task_id="task", run_id="run", run_type="workflow", workflow=graph, inputs={})
    owner = store.claim("task", worker_id="old", lease_seconds=5)
    runner = ControlledWriteRuntime(task_id="task", workflow=graph, backend=backend, execution_store=store, expected_lease_token=owner.lease_token)
    return clock, backend, table_id, graph, store, runner


@pytest.mark.parametrize("state", ["expired", "replaced", "cancelled"])
def test_cached_request_cannot_dispatch_after_ownership_loss(tmp_path, monkeypatch, state):
    clock, backend, table_id, _, store, runner = setup(tmp_path, monkeypatch)
    original = backend.execute_controlled_write

    def not_sent(*args, **kwargs):
        raise RuntimeError("synthetic crash before dispatch")

    monkeypatch.setattr(backend, "execute_controlled_write", not_sent)
    with pytest.raises(RuntimeError, match="synthetic"):
        runner.execute("write", {}, resolve_filter=_resolve_filter)
    assert store.read_private_write_entry("task", "request:write") is not None
    monkeypatch.setattr(backend, "execute_controlled_write", original)
    if state == "cancelled":
        store.cancel("task")
    else:
        clock.now += 10
        if state == "replaced":
            store.claim("task", worker_id="new")
    with pytest.raises(WorkflowExecutionConflictError):
        runner.execute("write", {}, resolve_filter=_resolve_filter)
    assert backend.query_records(table_id, schema_version=1) == []


def test_takeover_waits_for_inflight_local_commit_then_replays_once(tmp_path, monkeypatch):
    clock, backend, table_id, graph, store, runner = setup(tmp_path, monkeypatch)
    entered, release, claiming = threading.Event(), threading.Event(), threading.Event()
    original = backend.execute_controlled_write
    dispatches = []

    def paused(*args, **kwargs):
        dispatches.append(kwargs["operation_id"])
        entered.set()
        assert release.wait(5), "test must release the local transaction"
        return original(*args, **kwargs)

    def takeover():
        claiming.set()
        return store.claim("task", worker_id="new")

    monkeypatch.setattr(backend, "execute_controlled_write", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(runner.execute, "write", {}, resolve_filter=_resolve_filter)
        try:
            assert entered.wait(5)
            clock.now += 10
            next_owner = pool.submit(takeover)
            assert claiming.wait(5)
            from concurrent.futures import TimeoutError
            with pytest.raises(TimeoutError):
                next_owner.result(timeout=0.1)
        finally:
            release.set()
        with pytest.raises(WorkflowExecutionConflictError):
            work.result(timeout=5)
        owner = next_owner.result(timeout=5)
    assert len(backend.query_records(table_id, schema_version=1)) == 1
    resumed = ControlledWriteRuntime(task_id="task", workflow=graph, backend=backend, execution_store=store, expected_lease_token=owner.lease_token)
    result = resumed.execute("write", {}, resolve_filter=_resolve_filter)
    assert result["replayed"] is True
    assert len(dispatches) == 1
    assert len(backend.query_records(table_id, schema_version=1)) == 1
    assert store.read_private_write_entry("task", "source:write") is not None
