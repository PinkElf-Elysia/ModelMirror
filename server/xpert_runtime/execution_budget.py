from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterator


class XpertExecutionBudgetExceeded(RuntimeError):
    """Raised when an Xpert execution tree exceeds its global step budget."""


@dataclass
class XpertExecutionBudget:
    max_concurrency: int
    recursion_limit: int
    steps_used: int = 0
    max_model_calls: int | None = None
    max_tool_calls: int | None = None
    model_calls: int = 0
    tool_calls: int = 0
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)
    _step_lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.max_concurrency = max(1, min(int(self.max_concurrency), 100))
        self.recursion_limit = max(100, min(int(self.recursion_limit), 10_000))
        self.steps_used = max(0, int(self.steps_used))
        self.max_model_calls = (
            max(1, int(self.max_model_calls))
            if self.max_model_calls is not None
            else None
        )
        self.max_tool_calls = (
            max(0, int(self.max_tool_calls))
            if self.max_tool_calls is not None
            else None
        )
        self.model_calls = max(0, int(self.model_calls))
        self.tool_calls = max(0, int(self.tool_calls))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._step_lock = asyncio.Lock()

    async def charge(self, kind: str) -> int:
        async with self._step_lock:
            if self.steps_used >= self.recursion_limit:
                raise XpertExecutionBudgetExceeded(
                    f"Xpert global recursion limit reached ({self.recursion_limit})."
                )
            if kind == "model_call":
                if (
                    self.max_model_calls is not None
                    and self.model_calls >= self.max_model_calls
                ):
                    raise XpertExecutionBudgetExceeded(
                        f"Evaluation model-call budget reached ({self.max_model_calls})."
                    )
                self.model_calls += 1
            elif kind == "tool_call":
                if (
                    self.max_tool_calls is not None
                    and self.tool_calls >= self.max_tool_calls
                ):
                    raise XpertExecutionBudgetExceeded(
                        f"Evaluation tool-call budget reached ({self.max_tool_calls})."
                    )
                self.tool_calls += 1
            self.steps_used += 1
            return self.steps_used

    def usage(self) -> dict[str, int]:
        return {
            "steps_used": self.steps_used,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
        }


_execution_budgets: ContextVar[tuple[XpertExecutionBudget, ...]] = ContextVar(
    "modelmirror_xpert_execution_budgets",
    default=(),
)


def active_execution_budgets() -> tuple[XpertExecutionBudget, ...]:
    return _execution_budgets.get()


@contextmanager
def use_execution_budget(
    budget: XpertExecutionBudget | None,
) -> Iterator[XpertExecutionBudget | None]:
    if budget is None:
        yield None
        return
    token = _execution_budgets.set((*active_execution_budgets(), budget))
    try:
        yield budget
    finally:
        _execution_budgets.reset(token)


async def charge_execution_step(kind: str) -> None:
    for budget in active_execution_budgets():
        await budget.charge(kind)


@asynccontextmanager
async def execution_operation(kind: str) -> AsyncIterator[None]:
    budgets = active_execution_budgets()
    for budget in budgets:
        await budget.charge(kind)
    acquired: list[XpertExecutionBudget] = []
    try:
        for budget in budgets:
            await budget._semaphore.acquire()
            acquired.append(budget)
        yield
    finally:
        for budget in reversed(acquired):
            budget._semaphore.release()
