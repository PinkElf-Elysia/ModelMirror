from __future__ import annotations

import asyncio

import pytest

from server.xpert_runtime.execution_budget import (
    XpertExecutionBudget,
    XpertExecutionBudgetExceeded,
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
