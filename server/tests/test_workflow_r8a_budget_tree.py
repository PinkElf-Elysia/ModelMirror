"""Offline root-budget authority; background work does not inherit ContextVars."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from server.tests.test_workflow_r8a_budget_journal import LIMITS, ZERO, create, initialize
from server.xpert_runtime.execution_budget import (
    XpertExecutionBudget, XpertExecutionBudgetExceeded, XpertExecutionBudgetStateError,
    execution_operation, use_execution_budget,
    assert_execution_dispatch_allowed,
)
from server.xpert_runtime.execution_store import (
    WorkflowExecutionConflictError, WorkflowExecutionStorageError, WorkflowExecutionStore,
)


def tree(tmp_path, *, cap=3, concurrency=1):
    store = WorkflowExecutionStore(tmp_path / "tree")
    create(store, "root")
    initialize(store, limits={**LIMITS, "max_model_calls": cap, "max_concurrency": concurrency})
    return store


def child(store, task_id="child", parent="root"):
    occurrence = f"handoff-{task_id}"
    store.authorize_execution_budget_child(parent, occurrence)
    create(store, task_id)
    initialize(store, task_id)
    store.bind_execution_budget_parent(task_id, parent_task_id=parent, occurrence_id=occurrence)
    return task_id


def budget(store, task_id, token=None):
    value = XpertExecutionBudget.from_persisted_state(store.require(task_id).execution_budget)
    value.bind_persistence(store, task_id, expected_lease_token=token)
    return value


async def call(value):
    with use_execution_budget(value):
        async with execution_operation("model_call"):
            pass


@pytest.mark.asyncio
async def test_background_child_debits_waiting_root_without_inherited_context(tmp_path):
    store = tree(tmp_path, cap=1)
    child(store)
    store.suspend("root", continuation={}, wait_kind="agent_handoff", wait_id="handoff-child")
    await call(budget(store, "child"))
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    with pytest.raises(XpertExecutionBudgetExceeded):
        await call(budget(store, "child"))
    assert store.require("child").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_separate_children_share_root_concurrency(tmp_path):
    store = tree(tmp_path)
    child(store, "one")
    child(store, "two")
    one, two = budget(store, "one"), budget(store, "two")
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()

    async def first():
        with use_execution_budget(one):
            async with execution_operation("model_call"):
                entered.set()
                await release.wait()

    async def second():
        with use_execution_budget(two):
            async with execution_operation("model_call"):
                second_entered.set()

    first_task = asyncio.create_task(first())
    second_task = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second_task = asyncio.create_task(second())
        await asyncio.sleep(0.03)
        assert not second_entered.is_set()
        assert store.require("two").execution_budget["usage"] == ZERO
    finally:
        release.set()
        await first_task
        if second_task:
            await second_task
    assert second_entered.is_set()
    assert store.require("root").execution_budget["usage"]["model_calls"] == 2


@pytest.mark.asyncio
async def test_restart_preserves_single_child_identity_and_root_usage(tmp_path):
    store = tree(tmp_path, cap=1)
    child(store)
    await call(budget(store, "child"))
    reopened = WorkflowExecutionStore(store.storage_dir)
    claimed = reopened.claim("child", worker_id="restart")
    reopened.bind_execution_budget_parent(
        "child", parent_task_id="root", occurrence_id="handoff-child",
        expected_lease_token=claimed.lease_token,
    )
    with pytest.raises(XpertExecutionBudgetExceeded):
        await call(budget(reopened, "child", claimed.lease_token))
    create(reopened, "replacement")
    initialize(reopened, "replacement")
    with pytest.raises(XpertExecutionBudgetStateError):
        reopened.bind_execution_budget_parent("replacement", parent_task_id="root", occurrence_id="handoff-child")


@pytest.mark.asyncio
async def test_inline_inheritance_and_persisted_link_charge_root_once(tmp_path):
    store = tree(tmp_path)
    child(store)
    parent, leaf = budget(store, "root"), budget(store, "child")
    with use_execution_budget(parent), use_execution_budget(leaf):
        async with execution_operation("model_call"):
            assert parent.model_calls == leaf.model_calls == 1
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_root_object_refreshes_after_background_child_charge(tmp_path):
    store = tree(tmp_path, cap=2)
    parent = budget(store, "root")
    child(store)
    await call(budget(store, "child"))
    assert parent.model_calls == 0  # A stale object is not the accounting authority.
    await call(parent)
    assert parent.model_calls == 2
    with pytest.raises(XpertExecutionBudgetExceeded):
        await call(budget(store, "child"))


@pytest.mark.asyncio
async def test_multilevel_chain_is_atomic_and_preserves_independent_limits(tmp_path):
    store = tree(tmp_path, cap=2)
    child(store, "middle")
    child(store, "leaf", parent="middle")
    await call(budget(store, "leaf"))
    await call(budget(store, "middle"))
    before = store.snapshot_path.read_bytes()
    with pytest.raises(XpertExecutionBudgetExceeded):
        await call(budget(store, "leaf"))
    assert store.snapshot_path.read_bytes() == before
    assert [store.require(key).execution_budget["usage"]["model_calls"]
            for key in ("root", "middle", "leaf")] == [2, 2, 1]


@pytest.mark.parametrize("action", ["grant", "bind", "charge"])
def test_old_child_or_parent_owner_cannot_mutate_budget_tree(tmp_path, action):
    store = tree(tmp_path)
    child(store)
    key = "root" if action == "grant" else "child"
    old = store.claim(key, worker_id="old")
    store.claim(key, worker_id="new", now=old.lease_expires_at + 1)
    before = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        if action == "grant":
            store.authorize_execution_budget_child(key, "other", expected_lease_token=old.lease_token)
        elif action == "bind":
            store.bind_execution_budget_parent(key, parent_task_id="root", occurrence_id="handoff-child",
                                              expected_lease_token=old.lease_token)
        else:
            store.reserve_execution_budgets([{"task_id": key, "expected_usage": ZERO,
                                             "expected_lease_token": old.lease_token}], "model_call")
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["cancelled", "completed", "failed", "rejected", "expired"])
async def test_inactive_ancestor_blocks_before_dispatch(tmp_path, state):
    store = tree(tmp_path)
    child(store)
    leaf = budget(store, "child")
    if state == "expired":
        store.claim("root", worker_id="gone", now=10, lease_seconds=1)
    elif state == "cancelled":
        store.cancel("root")
    else:
        # Store lifecycle itself is tested elsewhere; exercise every terminal state here.
        store._items["root"].status = state
    before = store.snapshot_path.read_bytes()
    with pytest.raises(WorkflowExecutionConflictError):
        await call(leaf)
    assert store.snapshot_path.read_bytes() == before
    assert leaf.model_calls == 0


@pytest.mark.parametrize("fault", ["unregistered", "self", "cycle", "charged", "reparent"])
def test_forged_or_retroactive_parent_binding_rejected(tmp_path, fault):
    store = tree(tmp_path)
    create(store, "child")
    initialize(store, "child")
    target, parent, occurrence = "child", "root", "grant"
    if fault != "unregistered":
        store.authorize_execution_budget_child("root", "grant")
    if fault == "self":
        target = "root"
    elif fault == "cycle":
        store.bind_execution_budget_parent("child", parent_task_id="root", occurrence_id="grant")
        store.authorize_execution_budget_child("child", "back")
        target, parent, occurrence = "root", "child", "back"
    elif fault == "charged":
        store.reserve_execution_budgets([{"task_id": "child", "expected_usage": ZERO,
                                         "expected_lease_token": None}], "model_call")
    elif fault == "reparent":
        store.bind_execution_budget_parent("child", parent_task_id="root", occurrence_id="grant")
        create(store, "other")
        initialize(store, "other")
        store.authorize_execution_budget_child("other", "grant")
        parent = "other"
    before = store.snapshot_path.read_bytes()
    with pytest.raises(XpertExecutionBudgetStateError):
        store.bind_execution_budget_parent(target, parent_task_id=parent, occurrence_id=occurrence)
    assert store.snapshot_path.read_bytes() == before


@pytest.mark.parametrize("fault", ["missing_parent", "missing_child", "wrong_occurrence", "cycle", "invalid_type"])
def test_corrupt_links_fail_closed_on_reload(tmp_path, fault):
    store = tree(tmp_path)
    child(store)
    raw = json.loads(store.snapshot_path.read_text(encoding="utf-8"))
    items = {entry["task_id"]: entry for entry in raw["items"]}
    if fault == "missing_parent":
        items["child"]["execution_budget_parent"]["parent_task_id"] = "absent"
    elif fault == "missing_child":
        items["root"]["execution_budget_children"]["handoff-child"] = "absent"
    elif fault == "wrong_occurrence":
        items["child"]["execution_budget_parent"]["occurrence_id"] = "other"
    elif fault == "cycle":
        items["root"]["execution_budget_parent"] = {"parent_task_id": "child", "occurrence_id": "back"}
        items["child"]["execution_budget_children"]["back"] = "root"
    else:
        items["root"]["execution_budget_children"] = []
    store.snapshot_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WorkflowExecutionStorageError):
        WorkflowExecutionStore(store.storage_dir)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
async def test_root_child_save_failure_never_dispatches_or_refunds(tmp_path, monkeypatch, phase):
    import server.xpert_runtime.execution_store as store_module
    store = tree(tmp_path)
    child(store)
    leaf = budget(store, "child")
    before = deepcopy(store._items)
    replace = store_module.os.replace

    def fail(source, destination):
        if destination == store.snapshot_path:
            if phase == "after_replace":
                replace(source, destination)
            raise OSError("synthetic persistence fault")
        replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", fail)
    with pytest.raises(WorkflowExecutionStorageError):
        await call(leaf)
    assert store._items == before
    assert not store.available
    monkeypatch.setattr(store_module.os, "replace", replace)
    reopened = WorkflowExecutionStore(store.storage_dir)
    expected = 1 if phase == "after_replace" else 0
    assert [reopened.require(key).execution_budget["usage"]["model_calls"]
            for key in ("root", "child")] == [expected, expected]


@pytest.mark.asyncio
async def test_cancelled_root_while_queued_rechecks_and_releases_all_gates(tmp_path):
    store = tree(tmp_path)
    child(store, "one")
    child(store, "two")
    first, second = budget(store, "one"), budget(store, "two")
    with use_execution_budget(first):
        async with execution_operation("model_call"):
            # Suppress inherited ContextVars to model a background coordinator.
            import contextvars
            waiter = asyncio.create_task(call(second), context=contextvars.Context())
            await asyncio.sleep(0.03)
            assert not waiter.done()
            store.cancel("root")
    with pytest.raises(WorkflowExecutionConflictError):
        await waiter
    assert second.model_calls == 0
    assert not second._semaphore.locked()


def test_private_binding_is_not_inferred_from_untrusted_metadata(tmp_path):
    store = tree(tmp_path)
    store.create(task_id="forged", run_id="run-forged", run_type="xpert", workflow={}, inputs={},
                 runtime_metadata={"parent_task_id": "root", "execution_budget_parent": {"parent_task_id": "root"}})
    initialize(store, "forged")
    assert [entry["task_id"] for entry in store.execution_budget_chain("forged")] == ["forged"]


@pytest.mark.asyncio
async def test_parent_attachment_during_queue_cannot_skip_new_root_semaphore(tmp_path):
    store = tree(tmp_path)
    create(store, "child")
    initialize(store, "child")
    local = budget(store, "child")
    # Occupy all local slots before the operation resolves its original standalone chain.
    await local._semaphore.acquire()
    await local._semaphore.acquire()
    pending = asyncio.create_task(call(local))
    try:
        await asyncio.sleep(0.03)
        store.authorize_execution_budget_child("root", "attach")
        store.bind_execution_budget_parent("child", parent_task_id="root", occurrence_id="attach")
    finally:
        local._semaphore.release()
        local._semaphore.release()
    with pytest.raises(XpertExecutionBudgetStateError, match="身份变化"):
        await pending
    assert store.require("root").execution_budget["usage"] == ZERO


@pytest.mark.asyncio
async def test_last_request_guard_rechecks_root_after_admission(tmp_path):
    store = tree(tmp_path)
    child(store)
    with use_execution_budget(budget(store, "child")):
        async with execution_operation("model_call"):
            store.cancel("root")
            with pytest.raises(WorkflowExecutionConflictError):
                await assert_execution_dispatch_allowed()
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1


def test_threaded_children_cannot_overdraw_last_root_call(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = tree(tmp_path, cap=1)
    child(store, "one")
    child(store, "two")

    def reserve(key):
        try:
            store.reserve_execution_budgets([{"task_id": key, "expected_usage": ZERO,
                                             "expected_lease_token": None}], "model_call")
            return "admitted"
        except XpertExecutionBudgetExceeded:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, ("one", "two"))) == ["admitted", "denied"]
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert sum(store.require(key).execution_budget["usage"]["model_calls"] for key in ("one", "two")) == 1


def test_exhausted_child_does_not_partially_charge_root(tmp_path):
    store = tree(tmp_path)
    child(store)
    item = store._items["child"]
    item.execution_budget["usage"] = {**ZERO, "steps_used": 3, "model_calls": 3}
    before = store.snapshot_path.read_bytes()
    with pytest.raises(XpertExecutionBudgetExceeded):
        store.reserve_execution_budgets([{"task_id": "child", "expected_usage": item.execution_budget["usage"],
                                         "expected_lease_token": None}], "model_call")
    assert store.require("root").execution_budget["usage"] == ZERO
    assert store.snapshot_path.read_bytes() == before


def test_delegation_depth_is_bounded(tmp_path):
    store = tree(tmp_path)
    parent = "root"
    for index in range(15):
        parent = child(store, f"child-{index}", parent=parent)
    store.authorize_execution_budget_child(parent, "extra")
    create(store, "extra")
    initialize(store, "extra")
    with pytest.raises(XpertExecutionBudgetStateError, match="层级超限"):
        store.bind_execution_budget_parent("extra", parent_task_id=parent, occurrence_id="extra")
    reopened = WorkflowExecutionStore(store.storage_dir)
    claimed = reopened.claim(parent, worker_id="restart")
    assert len(reopened.execution_budget_chain(parent, expected_lease_token=claimed.lease_token)) == 16


@pytest.mark.parametrize("action", ["grant", "bind"])
def test_parent_wait_accepts_preissued_binding_but_not_new_grants(tmp_path, action):
    store = tree(tmp_path)
    store.authorize_execution_budget_child("root", "approved")
    store.suspend("root", continuation={}, wait_kind="agent_handoff", wait_id="approved")
    create(store, "child")
    initialize(store, "child")
    if action == "grant":
        with pytest.raises(WorkflowExecutionConflictError):
            store.authorize_execution_budget_child("root", "late")
    else:
        store.bind_execution_budget_parent("child", parent_task_id="root", occurrence_id="approved")
        assert len(store.execution_budget_chain("child")) == 2


def test_second_event_loop_cannot_duplicate_root_concurrency(tmp_path):
    store = tree(tmp_path)
    child(store)
    asyncio.run(call(budget(store, "child")))
    with pytest.raises(XpertExecutionBudgetStateError, match="事件循环"):
        asyncio.run(call(budget(store, "child")))
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1


def test_unrelated_roots_in_one_store_do_not_share_loop_ownership(tmp_path):
    store = tree(tmp_path)
    asyncio.run(call(budget(store, "root")))
    create(store, "independent")
    initialize(store, "independent")
    asyncio.run(call(budget(store, "independent")))
    assert store.require("root").execution_budget["usage"]["model_calls"] == 1
    assert store.require("independent").execution_budget["usage"]["model_calls"] == 1


@pytest.mark.parametrize("action", ["grant", "bind"])
@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
def test_delegation_save_is_atomic_and_does_not_create_second_identity(tmp_path, monkeypatch, action, phase):
    import server.xpert_runtime.execution_store as store_module
    store = tree(tmp_path)
    create(store, "child")
    initialize(store, "child")
    if action == "bind":
        store.authorize_execution_budget_child("root", "grant")
    before = deepcopy(store._items)
    replace = store_module.os.replace

    def fail(source, destination):
        if destination == store.snapshot_path:
            if phase == "after_replace":
                replace(source, destination)
            raise OSError("synthetic delegation failure")
        replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", fail)
    with pytest.raises(WorkflowExecutionStorageError):
        if action == "grant":
            store.authorize_execution_budget_child("root", "grant")
        else:
            store.bind_execution_budget_parent("child", parent_task_id="root", occurrence_id="grant")
    assert store._items == before
    assert not store.available
    monkeypatch.setattr(store_module.os, "replace", replace)
    reopened = WorkflowExecutionStore(store.storage_dir)
    root, leaf = reopened.require("root"), reopened.require("child")
    if action == "grant":
        assert ("grant" in root.execution_budget_children) == (phase == "after_replace")
        assert leaf.execution_budget_parent is None
    else:
        committed = phase == "after_replace"
        assert (root.execution_budget_children["grant"] == "child") == committed
        assert (leaf.execution_budget_parent is not None) == committed


@pytest.mark.asyncio
async def test_cancelled_queued_child_does_not_leak_root_slot(tmp_path):
    import contextvars
    store = tree(tmp_path)
    child(store, "one")
    child(store, "two")
    first, second = budget(store, "one"), budget(store, "two")
    with use_execution_budget(first):
        async with execution_operation("model_call"):
            pending = asyncio.create_task(call(second), context=contextvars.Context())
            await asyncio.sleep(0.03)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
    await asyncio.wait_for(call(second), 2)
    assert store.require("root").execution_budget["usage"]["model_calls"] == 2
