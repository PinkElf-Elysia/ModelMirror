"""Offline durable admission, including failures before and after file replacement."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict

import pytest

from server.xpert_runtime.execution_budget import (
    XpertExecutionBudget, XpertExecutionBudgetExceeded, XpertExecutionBudgetStateError,
    execution_operation, use_execution_budget,
)
from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError,
    WorkflowExecutionStorageError,
    WorkflowExecutionStore,
)
import server.xpert_runtime.execution_store as store_module


LIMITS = {"max_concurrency": 2, "recursion_limit": 1000, "max_model_calls": 3, "max_tool_calls": 2}
ZERO = {"steps_used": 0, "model_calls": 0, "tool_calls": 0}


def create(store, task_id):
    store.create(task_id=task_id, run_id=f"run-{task_id}", run_type="xpert",
                 workflow={"id": "synthetic", "nodes": [], "edges": []}, inputs={})


def initialize(store, task_id="root", usage=None, limits=None, **lease):
    return store.initialize_execution_budget(
        task_id, limits=LIMITS if limits is None else limits,
        usage=ZERO if usage is None else usage, **lease,
    )


def reservation(task_id="root", usage=None, token=None):
    return {"task_id": task_id, "expected_usage": deepcopy(ZERO if usage is None else usage),
            "expected_lease_token": token}


@pytest.fixture
def store(tmp_path):
    result = WorkflowExecutionStore(tmp_path / "executions")
    create(result, "root")
    return result


def test_durable_charge_survives_without_new_continuation(store):
    initialize(store)
    store.reserve_execution_budgets([reservation()], "model_call")
    reopened = WorkflowExecutionStore(store.storage_dir)
    claimed = reopened.claim("root", worker_id="restart")
    state = initialize(reopened, expected_lease_token=claimed.lease_token)
    assert state == {"version": 1, "limits": LIMITS, "usage": {**ZERO, "steps_used": 1, "model_calls": 1}}
    assert reopened.require("root").continuation == {}


@pytest.mark.parametrize("kind,usage", [
    ("workflow_node", {**ZERO, "steps_used": 1000}),
    ("model_call", {**ZERO, "steps_used": 3, "model_calls": 3}),
    ("tool_call", {**ZERO, "steps_used": 2, "tool_calls": 2}),
])
def test_child_exhaustion_commits_neither_budget(store, kind, usage):
    create(store, "child")
    initialize(store)
    initialize(store, "child", usage=usage)
    before = store.snapshot_path.read_bytes()
    with pytest.raises(XpertExecutionBudgetExceeded):
        store.reserve_execution_budgets([reservation(), reservation("child", usage)], kind)
    assert store.snapshot_path.read_bytes() == before
    assert store.require("root").execution_budget["usage"] == ZERO


def test_stale_counter_concurrent_admission_has_one_winner(store):
    initialize(store)
    def admit(_):
        try:
            store.reserve_execution_budgets([reservation()], "model_call")
            return "admitted"
        except XpertExecutionBudgetStateError:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(admit, range(2))) == ["admitted", "stale"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.parametrize("operation", ["initialize", "reserve"])
@pytest.mark.parametrize("ownership", ["stale", "omitted", "terminal"])
def test_budget_changes_require_current_owner(store, operation, ownership):
    initialize(store)
    old = store.claim("root", worker_id="old", lease_seconds=5, now=10)
    store.claim("root", worker_id="new", lease_seconds=60, now=20)
    if ownership == "terminal":
        store.cancel("root")
    token = None if ownership == "omitted" else old.lease_token
    before = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        if operation == "initialize":
            initialize(store, expected_lease_token=token)
        else:
            store.reserve_execution_budgets([reservation(token=token)], "model_call")
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
def test_failed_save_never_exposes_partial_in_memory_admission(store, monkeypatch, phase):
    create(store, "child")
    initialize(store)
    initialize(store, "child")
    before_items = deepcopy(store._items)
    before_bytes = store.snapshot_path.read_bytes()
    original_replace = store_module.os.replace
    def fail_replace(source, destination):
        if destination == store.snapshot_path:
            if phase == "after_replace":
                original_replace(source, destination)
            raise OSError("synthetic failure")
        return original_replace(source, destination)
    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    with pytest.raises(WorkflowExecutionStorageError):
        store.reserve_execution_budgets([reservation(), reservation("child")], "model_call")
    assert store._items == before_items
    assert not store.available
    monkeypatch.setattr(store_module.os, "replace", original_replace)
    if phase == "before_replace":
        assert store.snapshot_path.read_bytes() == before_bytes
    reopened = WorkflowExecutionStore(store.storage_dir)
    for task_id in ("root", "child"):
        assert reopened.require(task_id).execution_budget["usage"]["model_calls"] == (phase == "after_replace")


@pytest.mark.parametrize("change", ["limit", "ahead", "duplicate"])
def test_drift_or_duplicate_binding_rejected_without_mutation(store, change):
    initialize(store)
    before = store.snapshot_path.read_bytes()
    with pytest.raises(XpertExecutionBudgetStateError):
        if change == "limit":
            initialize(store, limits={**LIMITS, "max_model_calls": 10})
        elif change == "ahead":
            initialize(store, usage={**ZERO, "steps_used": 1})
        else:
            store.reserve_execution_budgets([reservation(), reservation()], "model_call")
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.parametrize("state", [
    {}, {"version": True, "limits": LIMITS, "usage": ZERO},
    {"version": 1, "limits": {**LIMITS, "max_concurrency": True}, "usage": ZERO},
    {"version": 1, "limits": {**LIMITS, "recursion_limit": 50}, "usage": ZERO},
    {"version": 1, "limits": LIMITS, "usage": {**ZERO, "model_calls": 1}},
])
def test_corrupt_budget_snapshot_rejected_on_load(store, state):
    initialize(store)
    raw = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    raw["items"][0]["execution_budget"] = state
    store.snapshot_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkflowExecutionStorageError):
        WorkflowExecutionStore(store.storage_dir)


def test_legacy_state_can_be_initialized_once_and_detached(store):
    assert store.require("root").execution_budget is None
    usage = {**ZERO, "steps_used": 8, "model_calls": 2}
    returned = initialize(store, usage=usage)
    returned["usage"]["steps_used"] = 0
    assert store.require("root").execution_budget["usage"] == usage
    assert "execution_budget" in asdict(store.require("root"))


@pytest.mark.asyncio
async def test_operation_body_observes_already_persisted_parent_and_child(store):
    create(store, "child")
    budgets = [XpertExecutionBudget(**LIMITS), XpertExecutionBudget(**LIMITS)]
    for budget, task_id in zip(budgets, ("root", "child")):
        budget.bind_persistence(store, task_id)
    with use_execution_budget(budgets[0]), use_execution_budget(budgets[1]):
        async with execution_operation("model_call"):
            data = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
            assert all(item["execution_budget"]["usage"]["model_calls"] == 1 for item in data["items"])
    assert budgets[0].usage() == budgets[1].usage() == {**ZERO, "steps_used": 1, "model_calls": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["save", "cancel", "stale"])
async def test_durable_rejection_prevents_dispatch_and_counter_change(store, monkeypatch, failure):
    budget = XpertExecutionBudget(**LIMITS)
    current = store.claim("root", worker_id="owner")
    budget.bind_persistence(store, "root", expected_lease_token=current.lease_token)
    if failure == "save":
        def fail():
            raise WorkflowExecutionStorageError("合成保存失败")
        monkeypatch.setattr(store, "_persist_unlocked", fail)
    elif failure == "cancel":
        store.cancel("root")
    else:
        store.claim("root", worker_id="new", now=current.lease_expires_at + 1)
    expected_error = WorkflowExecutionStorageError if failure == "save" else WorkflowExecutionConflictError
    with use_execution_budget(budget), pytest.raises(expected_error):
        async with execution_operation("model_call"):
            pytest.fail("持久准入未通过不得派发")
    assert budget.usage() == ZERO
    assert not budget._semaphore.locked()


@pytest.mark.asyncio
async def test_separate_store_authorities_reject_before_either_commit(store, tmp_path):
    other = WorkflowExecutionStore(tmp_path / "other")
    create(other, "child")
    parent, child = XpertExecutionBudget(**LIMITS), XpertExecutionBudget(**LIMITS)
    parent.bind_persistence(store, "root")
    child.bind_persistence(other, "child")
    before = [source.snapshot_path.read_bytes() for source in (store, other)]
    with use_execution_budget(parent), use_execution_budget(child), pytest.raises(RuntimeError, match="不同持久权威"):
        async with execution_operation("model_call"):
            pytest.fail("不可原子提交的预算不能派发")
    assert before == [source.snapshot_path.read_bytes() for source in (store, other)]
    assert parent.usage() == child.usage() == ZERO


@pytest.mark.asyncio
async def test_runner_restarts_from_durable_usage_not_stale_continuation(tmp_path, monkeypatch):
    import server.main as main
    from server.tests.test_workflow_r8a_budget_restore import setup_resume, response_for, USAGE

    store, _, execution, payload = setup_resume(tmp_path, monkeypatch)
    await response_for(execution, payload)
    first = main.workflow_task_store["task"]["execution_budget"]
    # The original continuation remains stale after a dispatched operation.
    with use_execution_budget(first):
        async with execution_operation("workflow_node"):
            pass
    assert store.require("task").continuation["execution_budget"] == USAGE
    reopened = WorkflowExecutionStore(store.storage_dir)
    reopened.mark_ready("task", wait_kind="approval", wait_id="wait")
    claimed = reopened.claim("task", worker_id="replacement")
    monkeypatch.setattr(main, "workflow_execution_store", reopened)
    monkeypatch.setattr(main, "workflow_task_store", {})
    result = await main.consume_workflow_stream(await response_for(claimed, payload))
    assert result["event"] == "workflow_end"
    assert reopened.require("task").execution_budget["usage"] == {**USAGE, "steps_used": 25}


@pytest.mark.asyncio
async def test_runner_durable_exhaustion_cannot_be_erased_by_old_continuation(tmp_path, monkeypatch):
    import server.main as main
    from server.tests.test_workflow_r8a_budget_restore import setup_resume, response_for, USAGE

    store, _, execution, payload = setup_resume(tmp_path, monkeypatch)
    store.initialize_execution_budget("task", limits=LIMITS, usage={**USAGE, "steps_used": 1000}, expected_lease_token=execution.lease_token)
    response = await response_for(execution, payload)
    events = []
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        events += [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
    assert not any(event.get("event") == "workflow_end" for event in events)
    assert store.require("task").status == "failed"
    assert store.require("task").execution_budget["usage"]["steps_used"] == 1000


@pytest.mark.asyncio
async def test_budget_drift_is_terminal_not_misclassified_as_lease_takeover(tmp_path, monkeypatch):
    import server.main as main
    from server.tests.test_workflow_r8a_budget_restore import setup_resume, USAGE

    store, _, execution, _ = setup_resume(tmp_path, monkeypatch, wait_kind="timer")
    store.initialize_execution_budget("task", limits=LIMITS, usage=ZERO, expected_lease_token=execution.lease_token)
    store.release_ready("task", expected_lease_token=execution.lease_token)
    result = await main.resume_runtime_due_execution("task")
    assert result["event"] == "error"
    assert result["code"] == "WORKFLOW_EXECUTION_BUDGET_INVALID"
    assert store.require("task").status == "failed"
    assert store.require("task").execution_budget["usage"] == ZERO
