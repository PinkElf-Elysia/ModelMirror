from __future__ import annotations

import asyncio

import pytest

from server.xpert_runtime.execution_budget import (
    XpertExecutionBudget,
    XpertExecutionBudgetExceeded,
    active_execution_budgets,
    charge_execution_step,
    execution_operation,
    use_execution_budget,
)


@pytest.mark.asyncio
async def test_execution_tree_shares_recursion_budget() -> None:
    parent = XpertExecutionBudget(max_concurrency=2, recursion_limit=100, steps_used=98)
    child = XpertExecutionBudget(max_concurrency=1, recursion_limit=100)

    with use_execution_budget(parent), use_execution_budget(child):
        await charge_execution_step("workflow_node")
        async with execution_operation("model_call"):
            pass
        with pytest.raises(XpertExecutionBudgetExceeded):
            await charge_execution_step("tool_call")

    assert parent.steps_used == 100
    assert child.steps_used == 2


@pytest.mark.asyncio
async def test_execution_tree_enforces_global_concurrency() -> None:
    budget = XpertExecutionBudget(max_concurrency=1, recursion_limit=100)
    entered: list[str] = []
    release = asyncio.Event()

    async def operation(name: str) -> None:
        async with execution_operation("tool_call"):
            entered.append(name)
            if name == "first":
                await release.wait()

    with use_execution_budget(budget):
        first = asyncio.create_task(operation("first"))
        await asyncio.sleep(0)
        second = asyncio.create_task(operation("second"))
        await asyncio.sleep(0.01)
        assert entered == ["first"]
        release.set()
        await asyncio.gather(first, second)

    assert entered == ["first", "second"]
    assert budget.steps_used == 2


@pytest.mark.parametrize("boundary", ["step", "operation"])
@pytest.mark.parametrize("exhausted", ["steps", "model", "tool"])
@pytest.mark.asyncio
async def test_child_rejection_does_not_charge_parent(boundary, exhausted):
    parent = XpertExecutionBudget(max_concurrency=1, recursion_limit=100)
    child = XpertExecutionBudget(
        max_concurrency=1, recursion_limit=100,
        steps_used=100 if exhausted == "steps" else 0,
        max_model_calls=1, model_calls=1 if exhausted == "model" else 0,
        max_tool_calls=0 if exhausted == "tool" else 1,
    )
    before = [parent.usage(), child.usage()]
    kind = "tool_call" if exhausted == "tool" else "model_call"
    with use_execution_budget(parent), use_execution_budget(child):
        with pytest.raises(XpertExecutionBudgetExceeded):
            if boundary == "step":
                await charge_execution_step(kind)
            else:
                async with execution_operation(kind):
                    pytest.fail("rejected operation entered")
    assert [parent.usage(), child.usage()] == before
    assert active_execution_budgets() == ()
    for budget in (parent, child):
        await asyncio.wait_for(budget._semaphore.acquire(), timeout=2)
        budget._semaphore.release()


@pytest.mark.asyncio
async def test_cancelled_child_queue_releases_parent_slot_without_charging():
    parent = XpertExecutionBudget(max_concurrency=1, recursion_limit=100)
    child = XpertExecutionBudget(max_concurrency=1, recursion_limit=100)
    entered, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class ObservedSemaphore(asyncio.Semaphore):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    child._semaphore = ObservedSemaphore(1)

    async def blocker():
        with use_execution_budget(child):
            async with execution_operation("model_call"):
                entered.set()
                await release.wait()

    async def contender():
        with use_execution_budget(parent), use_execution_budget(child):
            async with execution_operation("model_call"):
                pytest.fail("cancelled queue entered")

    first = asyncio.create_task(blocker())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        second = asyncio.create_task(contender())
        await asyncio.wait_for(queued.wait(), timeout=2)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert parent.usage() == {"steps_used": 0, "model_calls": 0, "tool_calls": 0}
        assert child.usage() == {"steps_used": 1, "model_calls": 1, "tool_calls": 0}

        async def parent_operation():
            with use_execution_budget(parent):
                async with execution_operation("model_call"):
                    pass

        await asyncio.wait_for(parent_operation(), timeout=2)
        assert parent.model_calls == 1
    finally:
        release.set()
        if second is not None and not second.done():
            second.cancel()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_entered_operation_failure_keeps_parent_and_child_charge(error):
    budgets = [XpertExecutionBudget(max_concurrency=1, recursion_limit=100, max_model_calls=1)
               for _ in range(2)]
    with use_execution_budget(budgets[0]), use_execution_budget(budgets[1]):
        with pytest.raises(error):
            async with execution_operation("model_call"):
                raise error("synthetic unknown result")
        assert [budget.model_calls for budget in budgets] == [1, 1]
        for budget in budgets:
            await asyncio.wait_for(budget._semaphore.acquire(), timeout=2)
            budget._semaphore.release()
        with pytest.raises(XpertExecutionBudgetExceeded):
            async with execution_operation("model_call"):
                pytest.fail("spent budget retried")
    assert [budget.steps_used for budget in budgets] == [1, 1]


@pytest.mark.asyncio
async def test_siblings_share_last_parent_call_without_overspending():
    parent = XpertExecutionBudget(max_concurrency=2, recursion_limit=100, max_model_calls=1)
    children = [XpertExecutionBudget(max_concurrency=1, recursion_limit=100) for _ in range(2)]
    calls = []

    async def child_operation(child):
        with use_execution_budget(parent), use_execution_budget(child):
            async with execution_operation("model_call"):
                calls.append(child)

    results = await asyncio.gather(*(child_operation(child) for child in children), return_exceptions=True)
    assert sum(isinstance(result, XpertExecutionBudgetExceeded) for result in results) == 1
    assert len(calls) == parent.model_calls == parent.steps_used == 1
    assert sorted(child.model_calls for child in children) == [0, 1]
    assert active_execution_budgets() == ()


@pytest.mark.asyncio
async def test_rebinding_same_budget_does_not_duplicate_or_self_lock():
    budget = XpertExecutionBudget(max_concurrency=1, recursion_limit=100, max_model_calls=1)

    async def operation():
        with use_execution_budget(budget), use_execution_budget(budget):
            async with execution_operation("model_call"):
                pass
        assert active_execution_budgets() == ()

    await asyncio.wait_for(operation(), timeout=2)
    assert budget.usage() == {"steps_used": 1, "model_calls": 1, "tool_calls": 0}


@pytest.mark.asyncio
async def test_exhausted_budget_rejects_without_waiting_for_busy_slot():
    budget = XpertExecutionBudget(max_concurrency=1, recursion_limit=100, max_tool_calls=0)
    await budget._semaphore.acquire()

    async def rejected():
        with use_execution_budget(budget):
            with pytest.raises(XpertExecutionBudgetExceeded):
                async with execution_operation("tool_call"):
                    pytest.fail("exhausted budget entered")

    try:
        await asyncio.wait_for(rejected(), timeout=2)
        assert budget.steps_used == 0
    finally:
        budget._semaphore.release()


@pytest.mark.asyncio
async def test_budget_is_rechecked_after_waiting_for_slot():
    parent = XpertExecutionBudget(max_concurrency=1, recursion_limit=100, max_model_calls=1)
    child = XpertExecutionBudget(max_concurrency=1, recursion_limit=100)
    queued = asyncio.Event()

    class ObservedSemaphore(asyncio.Semaphore):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    child._semaphore = ObservedSemaphore(0)

    async def contender():
        with use_execution_budget(parent), use_execution_budget(child):
            async with execution_operation("model_call"):
                pytest.fail("budget spent during queue was ignored")

    task = asyncio.create_task(contender())
    try:
        await asyncio.wait_for(queued.wait(), timeout=2)
        await parent.charge("model_call")
        child._semaphore.release()
        with pytest.raises(XpertExecutionBudgetExceeded):
            await asyncio.wait_for(task, timeout=2)
        assert parent.steps_used == parent.model_calls == 1
        assert child.steps_used == child.model_calls == 0
        for budget in (parent, child):
            await asyncio.wait_for(budget._semaphore.acquire(), timeout=2)
            budget._semaphore.release()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_while_acquiring_counter_locks_is_atomic():
    budgets = sorted([XpertExecutionBudget(max_concurrency=1, recursion_limit=100)
                      for _ in range(2)], key=id)
    queued = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                queued.set()
            return await super().acquire()

    budgets[1]._step_lock = ObservedLock()
    await budgets[1]._step_lock.acquire()

    async def charge():
        with use_execution_budget(budgets[0]), use_execution_budget(budgets[1]):
            await charge_execution_step("model_call")

    task = asyncio.create_task(charge())
    try:
        await asyncio.wait_for(queued.wait(), timeout=2)
        assert budgets[0]._step_lock.locked()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not budgets[0]._step_lock.locked()
        assert [budget.steps_used for budget in budgets] == [0, 0]
    finally:
        budgets[1]._step_lock.release()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
