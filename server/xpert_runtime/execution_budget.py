from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, Protocol
from weakref import WeakKeyDictionary


class ExecutionBudgetStore(Protocol):
    def initialize_execution_budget(
        self, task_id: str, *, limits: dict[str, Any], usage: dict[str, int],
        expected_lease_token: str | None = None,
    ) -> dict[str, Any]: ...

    def reserve_execution_budgets(self, reservations: list[dict[str, Any]], kind: str) -> dict[str, Any]: ...

    def execution_budget_chain(
        self, task_id: str, *, expected_lease_token: str | None = None,
    ) -> list[dict[str, Any]]: ...


class XpertExecutionBudgetExceeded(RuntimeError):
    """Raised when an Xpert execution tree exceeds its global step budget."""


class XpertExecutionBudgetStateError(RuntimeError):
    """Invalid budget identity/counters, distinct from worker lease takeover."""


def restore_execution_budget_usage(snapshot: object) -> dict[str, int]:
    """Read saved counters without coercing corrupt state into fresh allowance."""
    keys = ("steps_used", "model_calls", "tool_calls")
    if snapshot is None:
        # Legacy continuations have no usage evidence; keep their read compatibility.
        return dict.fromkeys(keys, 0)
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) != set(keys)
        or any(type(value) is not int or value < 0 for value in snapshot.values())
        or snapshot["model_calls"] + snapshot["tool_calls"] > snapshot["steps_used"]
    ):
        raise ValueError("执行预算恢复计数无效，已停止恢复；不得通过重置计数继续。")
    return dict(snapshot)


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
    _persistence: tuple[ExecutionBudgetStore, str, str | None] | None = field(default=None, init=False, repr=False)

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
        await _charge_budgets((self,), kind)
        return self.steps_used

    def _check_charge(self, kind: str) -> None:
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
        elif kind == "tool_call":
            if (
                self.max_tool_calls is not None
                and self.tool_calls >= self.max_tool_calls
            ):
                raise XpertExecutionBudgetExceeded(
                    f"Evaluation tool-call budget reached ({self.max_tool_calls})."
                )

    def _apply_charge(self, kind: str) -> None:
        if kind == "model_call":
            self.model_calls += 1
        elif kind == "tool_call":
            self.tool_calls += 1
        self.steps_used += 1

    def usage(self) -> dict[str, int]:
        return {
            "steps_used": self.steps_used,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
        }

    def limits(self) -> dict[str, int | None]:
        return {
            "max_concurrency": self.max_concurrency,
            "recursion_limit": self.recursion_limit,
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
        }

    def bind_persistence(
        self, store: ExecutionBudgetStore, task_id: str, *, expected_lease_token: str | None = None,
    ) -> None:
        if self._persistence is not None:
            raise RuntimeError("执行预算已绑定持久记录，不能替换身份。")
        state = store.initialize_execution_budget(
            task_id, limits=self.limits(), usage=self.usage(),
            expected_lease_token=expected_lease_token,
        )
        restored = self.from_persisted_state(state)
        self.steps_used = restored.steps_used
        self.model_calls = restored.model_calls
        self.tool_calls = restored.tool_calls
        self._persistence = (store, task_id, expected_lease_token)

    def persisted_state(self) -> dict:
        return {"version": 1, "limits": self.limits(), "usage": self.usage()}

    @classmethod
    def from_persisted_state(cls, state: object) -> XpertExecutionBudget:
        invalid = "持久执行预算格式无效，不能重置或放宽额度。"
        if (
            not isinstance(state, dict)
            or set(state) != {"version", "limits", "usage"}
            or type(state["version"]) is not int
            or state["version"] != 1
            or not isinstance(state["limits"], dict)
            or set(state["limits"]) != {"max_concurrency", "recursion_limit", "max_model_calls", "max_tool_calls"}
            or not isinstance(state["usage"], dict)
        ):
            raise ValueError(invalid)
        limits = state["limits"]
        for key, value in limits.items():
            if value is None and key in {"max_model_calls", "max_tool_calls"}:
                continue
            if type(value) is not int:
                raise ValueError(invalid)
        budget = cls(**limits, **restore_execution_budget_usage(state["usage"]))
        if budget.limits() != limits:
            raise ValueError(invalid)
        return budget


_execution_budgets: ContextVar[tuple[XpertExecutionBudget, ...]] = ContextVar(
    "modelmirror_xpert_execution_budgets",
    default=(),
)

_dispatch_guards: ContextVar[tuple[Callable[[], Awaitable[None]], ...]] = ContextVar(
    "modelmirror_execution_dispatch_guards", default=(),
)


@contextmanager
def use_execution_dispatch_guard(guard: Callable[[], Awaitable[None]] | None) -> Iterator[None]:
    """Bind server-owned lifecycle checks without replacing inherited authority."""
    if guard is None or any(active is guard for active in _dispatch_guards.get()):
        yield
        return
    if not callable(guard):
        raise TypeError("执行派发检查器必须由服务端提供。")
    token = _dispatch_guards.set((*_dispatch_guards.get(), guard))
    try:
        yield
    finally:
        _dispatch_guards.reset(token)


async def assert_execution_dispatch_allowed() -> None:
    for guard in _dispatch_guards.get():
        await guard()
    _durable_chains(active_execution_budgets())


def active_execution_budgets() -> tuple[XpertExecutionBudget, ...]:
    return _execution_budgets.get()


def current_execution_budget_binding() -> tuple[ExecutionBudgetStore, str, str | None] | None:
    """Return the active leaf's authority, never a caller-supplied task identity."""
    budgets = active_execution_budgets()
    if not budgets:
        return None
    chains = _durable_chains(budgets)
    binding = budgets[-1]._persistence
    if chains and binding is None:
        raise XpertExecutionBudgetStateError("内联子执行缺少当前持久预算身份。")
    return binding


_durable_gates: WeakKeyDictionary = WeakKeyDictionary()
_durable_gates_lock = threading.RLock()


def _durable_chains(budgets: tuple[XpertExecutionBudget, ...]) -> dict[int, list[dict[str, Any]]]:
    result = {}
    authority = None
    for budget in budgets:
        if budget._persistence is None:
            continue
        store, task_id, token = budget._persistence
        if authority is not None and authority is not store:
            raise RuntimeError("父子预算分属不同持久权威，已阻止派发。")
        authority = store
        chain = store.execution_budget_chain(task_id, expected_lease_token=token)
        local = XpertExecutionBudget.from_persisted_state(chain[-1]["state"])
        if local.limits() != budget.limits():
            raise XpertExecutionBudgetStateError("执行预算限额与持久记录不一致。")
        result[id(budget)] = chain
    return result


def _operation_gates(
    budgets: tuple[XpertExecutionBudget, ...], chains: dict[int, list[dict[str, Any]]],
) -> list[asyncio.Semaphore]:
    gates: dict[tuple[int, str], asyncio.Semaphore] = {}
    for budget in budgets:
        if budget._persistence is None:
            gates[(id(budget), "")] = budget._semaphore
            continue
        store = budget._persistence[0]
        with _durable_gates_lock:
            shared = _durable_gates.setdefault(store, {})
            loop = asyncio.get_running_loop()
            for entry in chains[id(budget)]:
                task_id = entry["task_id"]
                maximum = entry["state"]["limits"]["max_concurrency"]
                if task_id not in shared:
                    gate = budget._semaphore if task_id == budget._persistence[1] else asyncio.Semaphore(maximum)
                    shared[task_id] = (maximum, gate, loop)
                if shared[task_id][0] != maximum:
                    raise XpertExecutionBudgetStateError("根预算并发上限发生漂移。")
                if shared[task_id][2] is not loop:
                    raise XpertExecutionBudgetStateError("同一预算身份不能跨事件循环复制并发额度。")
                gates[(id(store), task_id)] = shared[task_id][1]
    # All callers acquire overlapping gates in the same order.
    return [gates[key] for key in sorted(gates)]


@contextmanager
def use_execution_budget(
    budget: XpertExecutionBudget | None,
) -> Iterator[XpertExecutionBudget | None]:
    if budget is None:
        yield None
        return
    if any(active is budget for active in active_execution_budgets()):
        yield budget
        return
    token = _execution_budgets.set((*active_execution_budgets(), budget))
    try:
        yield budget
    finally:
        _execution_budgets.reset(token)


async def _charge_budgets(
    budgets: tuple[XpertExecutionBudget, ...], kind: str,
    *, expected_chains: dict[int, list[dict[str, Any]]] | None = None,
) -> None:
    # Hold every counter lock before checking; no await separates check and commit.
    async with AsyncExitStack() as locks:
        for budget in sorted(budgets, key=id):
            await locks.enter_async_context(budget._step_lock)
        await assert_execution_dispatch_allowed()
        chains = _durable_chains(budgets)
        if expected_chains is not None and {
            key: [(entry["task_id"], entry["state"]["limits"]) for entry in chain]
            for key, chain in chains.items()
        } != {
            key: [(entry["task_id"], entry["state"]["limits"]) for entry in chain]
            for key, chain in expected_chains.items()
        }:
            raise XpertExecutionBudgetStateError("等待并发准入期间预算身份变化，已阻止派发。")
        for budget in budgets:
            if budget._persistence is None:
                budget._check_charge(kind)
        durable = [budget for budget in budgets if budget._persistence is not None]
        if durable:
            authority = durable[0]._persistence[0]
            if any(budget._persistence[0] is not authority for budget in durable):
                raise RuntimeError("父子预算分属不同持久权威，已阻止派发。")
            reservations = {}
            for budget in durable:
                task_id, token = budget._persistence[1:]
                entry = {"task_id": task_id, "expected_usage": chains[id(budget)][-1]["state"]["usage"],
                         "expected_lease_token": token}
                if task_id in reservations and reservations[task_id] != entry:
                    raise XpertExecutionBudgetStateError("同一执行存在冲突的预算所有权。")
                reservations[task_id] = entry
            states = authority.reserve_execution_budgets(list(reservations.values()), kind)
        for budget in budgets:
            if budget._persistence is None:
                budget._apply_charge(kind)
            else:
                for key, value in states[budget._persistence[1]]["usage"].items():
                    setattr(budget, key, value)


async def charge_execution_step(kind: str) -> None:
    await _charge_budgets(active_execution_budgets(), kind)


@asynccontextmanager
async def execution_operation(kind: str) -> AsyncIterator[None]:
    budgets = active_execution_budgets()
    await assert_execution_dispatch_allowed()
    chains = _durable_chains(budgets)
    for budget in budgets:
        if budget._persistence is None:
            budget._check_charge(kind)
    gates = _operation_gates(budgets, chains)
    acquired: list[asyncio.Semaphore] = []
    try:
        for gate in gates:
            await gate.acquire()
            acquired.append(gate)
        await _charge_budgets(budgets, kind, expected_chains=chains)
        yield
    finally:
        for gate in reversed(acquired):
            gate.release()
