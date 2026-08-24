"""Fixture-only Inspect tasks for the AR0 engineering harness.

These tasks contain no scientific dataset, custom scorer, provider request, or
model comparison. They only exercise success, failure, cancellation, log, and
replay contracts with Inspect's built-in local mock model configuration.
"""

import anyio

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.solver import Generate, Solver, TaskState, solver


@solver
def deterministic_success() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        return state

    return solve


@solver
def intentional_task_error() -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        raise RuntimeError("intentional AR0 fixture task error")

    return solve


@solver
def cancellable_wait(seconds: float = 300.0) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        await anyio.sleep(seconds)
        return state

    return solve


@task
def fixture_success() -> Task:
    return Task(
        dataset=[Sample(id="success-1", input="fixture-only success")],
        solver=deterministic_success(),
    )


@task
def fixture_task_error() -> Task:
    return Task(
        dataset=[Sample(id="task-error-1", input="fixture-only error")],
        solver=intentional_task_error(),
    )


@task
def fixture_long_running_cancel(seconds: float = 300.0) -> Task:
    return Task(
        dataset=[Sample(id="cancel-1", input="fixture-only cancellation")],
        solver=cancellable_wait(seconds=seconds),
    )
