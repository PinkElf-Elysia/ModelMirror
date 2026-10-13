"""R8-A acceptance invariants against real, isolated runtime Stores.

These are deliberately not xfailed: A0 freezes the red baseline before A1.
No provider, application singleton, or existing runtime directory is used.
"""
from __future__ import annotations

from dataclasses import asdict
import asyncio
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from server.xpert_runtime import AgentTaskStore, WorkflowExecutionStore
from server.xpert_runtime import ClientToolConnectionManager, ClientToolCoordinator, ClientToolStore
from server.xpert_runtime import execution_store as store_module
from server.xpert_runtime.execution_store import WorkflowExecutionConflictError
from server.xpert_runtime.workflow_handoff_coordinator import WorkflowHandoffCoordinator


@pytest.fixture
def clock(monkeypatch):
    current = SimpleNamespace(now=1_000.0)
    monkeypatch.setattr(store_module, "time", SimpleNamespace(time=lambda: current.now))
    return current


@pytest.fixture
def store(tmp_path):
    executions = WorkflowExecutionStore(storage_dir=tmp_path / "executions")
    executions.create(
        task_id="task", run_id="run", run_type="workflow",
        workflow={"id": "workflow", "nodes": [], "edges": []}, inputs={},
    )
    return executions


def _mutate(store, operation, **lease):
    if operation == "fail":
        return store.fail("task", error="SYNTHETIC_FAILURE", **lease)
    if operation == "complete":
        return store.complete("task", result="synthetic", **lease)
    if operation == "suspend":
        return store.suspend(
            "task", wait_kind="approval", wait_id="next-approval",
            continuation={"variables": {"count": 2}}, **lease,
        )
    if operation == "append_event":
        return store.append_event("task", {"event": "node_end", "node_id": "n"}, **lease)
    if operation == "update_run_id":
        return store.update_run_id("task", run_id="next-run", **lease)
    raise AssertionError(f"Unknown test operation: {operation}")


@pytest.mark.parametrize("operation", ["fail", "complete", "suspend", "append_event", "update_run_id"])
@pytest.mark.parametrize("ownership", ["stale", "expired", "omitted", "empty"])
def test_unowned_mutation_is_rejected_without_changing_memory_or_disk(store, clock, operation, ownership):
    old = store.claim("task", worker_id="old", lease_seconds=5)
    clock.now += 10
    if ownership != "expired":
        store.claim("task", worker_id="new", lease_seconds=60)
    lease = {} if ownership == "omitted" else {"expected_lease_token": old.lease_token}
    if ownership == "empty":
        lease = {"expected_lease_token": ""}
    before = asdict(store.require("task"))
    before_bytes = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        _mutate(store, operation, **lease)
    assert asdict(store.require("task")) == before
    assert store.snapshot_path.read_bytes() == before_bytes


@pytest.mark.parametrize("operation", ["fail", "complete", "suspend", "append_event", "update_run_id"])
def test_current_owner_can_mutate(store, clock, operation):
    claimed = store.claim("task", worker_id="owner", lease_seconds=60)
    before = store.snapshot_path.read_bytes()
    _mutate(store, operation, expected_lease_token=claimed.lease_token)
    assert store.snapshot_path.read_bytes() != before
    expected = {"fail": "failed", "complete": "completed", "suspend": "waiting"}
    assert store.require("task").status == expected.get(operation, "running")


def test_legacy_initial_execution_and_source_invalidation_remain_compatible(store):
    store.complete("task", result="original")
    store.fail("task", error="SYNTHETIC_PRIVATE_ERROR")
    item = store.require("task")
    assert item.status == "completed"
    assert item.result == "original"
    assert item.runtime_metadata["terminal_source_invalidated"] is True
    assert "SYNTHETIC_PRIVATE_ERROR" not in store.snapshot_path.read_text(encoding="utf-8")
    with pytest.raises(WorkflowExecutionConflictError):
        store.suspend("task", wait_id="late", continuation={})


def test_management_cancel_cannot_be_undone_by_late_worker(store, clock):
    claimed = store.claim("task", worker_id="owner")
    store.cancel("task")
    before = asdict(store.require("task"))
    with pytest.raises(WorkflowExecutionConflictError):
        store.complete("task", result="late", expected_lease_token=claimed.lease_token)
    store.complete("task", result="late")
    store.cancel("task")
    assert asdict(store.require("task")) == before


def test_suspended_continuation_cannot_be_completed_without_reclaim(store, clock):
    claimed = store.claim("task", worker_id="owner")
    store.suspend(
        "task", wait_kind="approval", wait_id="approval",
        continuation={"variables": {"flag": True, "rows": [1, None]}},
        expected_lease_token=claimed.lease_token,
    )
    before = asdict(store.require("task"))
    before_bytes = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        store.complete("task", result="late")
    assert asdict(store.require("task")) == before
    assert store.snapshot_path.read_bytes() == before_bytes


@pytest.mark.parametrize("change", ["event", "renew", "takeover", "cancel", "complete"])
def test_management_failure_rejects_stale_snapshot_without_writing(store, clock, change):
    claimed = store.claim("task", worker_id="old", lease_seconds=5)
    expected = store.snapshot("task")
    if change == "event":
        store.append_event("task", {"event": "node_end"}, expected_lease_token=claimed.lease_token)
    elif change == "renew":
        store.refresh_lease("task", lease_token=claimed.lease_token, lease_seconds=60)
    elif change == "takeover":
        clock.now += 10
        store.claim("task", worker_id="new")
    elif change == "cancel":
        store.cancel("task")
    else:
        store.complete("task", result="synthetic", expected_lease_token=claimed.lease_token)
    before = asdict(store.require("task"))
    before_bytes = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        store.fail_if_current(
            "task", expected_snapshot=expected,
            error="synthetic", safe_event={"event": "management.failed"},
        )
    assert asdict(store.require("task")) == before
    assert store.snapshot_path.read_bytes() == before_bytes


def test_management_failure_preserves_terminal_with_current_revision(store):
    store.complete("task", result="synthetic")
    current = store.require("task")
    before_bytes = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        store.fail_if_current(
            "task", expected_snapshot=store.snapshot("task"),
            error="synthetic", safe_event={"event": "management.failed"},
        )
    assert store.snapshot_path.read_bytes() == before_bytes
    assert current.result == "synthetic"


def test_management_failure_persists_event_and_terminal_in_one_revision(store, clock):
    claimed = store.claim("task", worker_id="interrupted")
    result = store.fail_if_current(
        "task", expected_snapshot=store.snapshot("task"),
        error="synthetic", safe_event={"event": "management.failed"},
    )
    assert result.status == "failed"
    assert result.revision == claimed.revision + 1
    assert result.events[-1]["event"] == "management.failed"
    assert result.lease_token is None
    restored = WorkflowExecutionStore(storage_dir=store.snapshot_path.parent)
    assert asdict(restored.require("task")) == asdict(result)


@pytest.mark.asyncio
async def test_late_handoff_callback_cannot_fail_the_replacement_owner(store, clock, tmp_path):
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    task = await tasks.create_task("synthetic", "synthetic")
    handoff = await tasks.create_handoff(
        task.task_id, source_agent="parent", target_agent="child", reason="synthetic",
    )
    await tasks.update_handoff_status(handoff.handoff_id, "accepted")
    await tasks.update_handoff_status(handoff.handoff_id, "completed")
    store.suspend(
        "task", wait_kind="agent_handoff", wait_id=handoff.handoff_id,
        continuation={"variables": {"count": 2}},
    )
    replacement = []
    snapshots = []

    async def late_callback(execution, _handoff):
        clock.now += 10
        replacement.append(store.claim("task", worker_id="new", lease_seconds=60))
        assert replacement[0].lease_token != execution.lease_token
        snapshots.append(store.snapshot_path.read_bytes())
        raise RuntimeError("synthetic delayed callback")

    coordinator = WorkflowHandoffCoordinator(
        tasks, store, late_callback, lease_seconds=5, worker_id="old",
    )
    assert await coordinator.run_once() == 0
    assert len(replacement) == 1
    assert asdict(store.require("task")) == asdict(replacement[0])
    assert store.snapshot_path.read_bytes() == snapshots[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_terminal", [False, True])
async def test_missing_handoff_receipt_cannot_fail_execution_after_lookup_race(
    store, tmp_path, monkeypatch, replacement_terminal,
):
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    store.suspend("task", wait_kind="agent_handoff", wait_id="missing", continuation={})
    snapshots = []

    async def delayed_lookup(_handoff_id):
        store.mark_ready("task", wait_kind="agent_handoff", wait_id="missing")
        owner = store.claim("task", worker_id="new")
        if replacement_terminal:
            store.complete("task", result="synthetic", expected_lease_token=owner.lease_token)
        snapshots.append(store.snapshot_path.read_bytes())
        return None

    async def forbidden_resume(_execution, _handoff):
        raise AssertionError("Missing receipt must not resume")

    monkeypatch.setattr(tasks, "get_handoff", delayed_lookup)
    coordinator = WorkflowHandoffCoordinator(tasks, store, forbidden_resume)
    assert await coordinator.run_once() == 0
    assert store.snapshot_path.read_bytes() == snapshots[0]


@pytest.mark.asyncio
async def test_late_client_callback_cannot_fail_the_replacement_owner(store, clock, tmp_path):
    clients = ClientToolStore(tmp_path / "clients")
    _pairing, code = clients.create_pairing(name="Synthetic")
    host, _token = clients.consume_pairing(code, version="1", capabilities=[])
    request = clients.create_request(
        operation_id="operation", tool_call_id="call", host_id=host.host_id,
        task_id="task", run_id="run", node_id="agent", scope_type="workflow",
        scope_id="task:agent", tool_name="host_page_read", arguments={},
        schema_hash="read-schema", timeout_seconds=60,
    )
    clients.mark_dispatched(request.request_id)
    clients.complete_request(
        request.request_id, host_id=host.host_id, operation_id=request.operation_id,
        tool_call_id=request.tool_call_id, result="synthetic",
    )
    store.suspend(
        "task", wait_kind="client_tool", wait_id=request.request_id,
        continuation={"variables": {"count": 2}},
    )
    replacement = []
    snapshots = []

    async def late_callback(execution, _request):
        clock.now += 10
        replacement.append(store.claim("task", worker_id="new", lease_seconds=60))
        assert replacement[0].lease_token != execution.lease_token
        snapshots.append(store.snapshot_path.read_bytes())
        raise RuntimeError("synthetic delayed callback")

    coordinator = ClientToolCoordinator(
        clients, store, ClientToolConnectionManager(clients), late_callback,
        lease_seconds=5, worker_id="old",
    )
    assert await coordinator.run_once() == 0
    assert len(replacement) == 1
    assert asdict(store.require("task")) == asdict(replacement[0])
    assert store.snapshot_path.read_bytes() == snapshots[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("replacement_terminal", [False, True])
async def test_agency_late_result_cannot_project_over_replacement(
    store, clock, outcome, replacement_terminal,
):
    from server.expert_team_agency_runtime import AgencyExecutionCoordinator
    from server.xpert_runtime import RunRegistry

    registry = RunRegistry()
    run = await registry.create_run("workflow", "synthetic", status="running")
    store.update_run_id("task", run_id=run.run_id)
    claimed = store.claim("task", worker_id="old", lease_seconds=5)
    snapshots = []

    class DelayedClient:
        async def execute(self, **_kwargs):
            clock.now += 10
            current = store.claim("task", worker_id="new", lease_seconds=60)
            if replacement_terminal:
                store.complete("task", result="new-result", expected_lease_token=current.lease_token)
            snapshots.append(store.snapshot_path.read_bytes())
            if outcome == "cancelled":
                raise asyncio.CancelledError()
            if outcome == "failed":
                raise RuntimeError("synthetic late failure")
            return SimpleNamespace(payload={"final_output": "old-result"})

    async def forbidden_model(_request):
        raise AssertionError("No model call is allowed in this test")

    runtime = AgencyExecutionCoordinator(
        store=store, run_registry=registry, model_runner=forbidden_model,
        client_factory=DelayedClient,
    )
    call = runtime._run(
        task_id="task", goal="synthetic", model_id="synthetic",
        prepared=SimpleNamespace(workflow={}, agents=[], skills=[]),
        expected_lease_token=claimed.lease_token,
    )
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        await call
    assert store.snapshot_path.read_bytes() == snapshots[0]
    assert (await registry.get_run(run.run_id)).status == "running"


@pytest.mark.asyncio
async def test_agent_task_cancel_is_not_replaced_by_late_completion(tmp_path):
    tasks = AgentTaskStore(storage_dir=tmp_path / "tasks")
    task = await tasks.create_task("synthetic", "synthetic")
    await tasks.cancel_task(task.task_id)
    try:
        await tasks.update_task(task.task_id, status="completed", result="late")
    except ValueError:
        pass  # Explicit transition rejection and idempotent no-op are both safe.
    restored = AgentTaskStore(storage_dir=tmp_path / "tasks")
    for source in (tasks, restored):
        item = await source.get_task(task.task_id)
        assert item.status == "cancelled"
        assert item.result is None


def test_legacy_snapshot_keeps_typed_continuation_and_terminal_result(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "r8a_legacy_workflow_executions.json"
    shutil.copyfile(fixture, tmp_path / "workflow_executions.json")
    store = WorkflowExecutionStore(storage_dir=tmp_path)
    waiting = store.require("legacy-waiting")
    assert waiting.wait_kind == "approval"
    assert waiting.wait_id == "legacy-approval"
    assert waiting.status == "waiting"
    assert waiting.continuation["variables"] == {"flag": True, "rows": [1, None], "count": 2}
    assert waiting.private_write_journal == {}
    done = store.require("legacy-completed")
    assert done.status == "completed"
    assert done.result == "synthetic-result"
    assert store.require("legacy-running").status == "ready"
    assert store.require("legacy-running").lease_token is None
    reopened = WorkflowExecutionStore(storage_dir=tmp_path)
    assert asdict(reopened.require("legacy-waiting")) == asdict(waiting)
    assert asdict(reopened.require("legacy-completed")) == asdict(done)
