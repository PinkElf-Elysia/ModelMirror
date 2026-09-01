from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import server.api.workflow_deployments as deployment_api


class _CoordinatorStore:
    def materialize_due_schedules(self) -> list[object]:
        return []

    def claimable_executions(self, *, limit: int) -> list[object]:
        assert limit == 20
        return []


@pytest.mark.asyncio
async def test_due_wait_resume_is_not_started_twice_while_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    started: list[str] = []

    async def resume(task_id: str) -> dict[str, str]:
        started.append(task_id)
        await release.wait()
        return {"status": "completed"}

    monkeypatch.setattr(deployment_api, "_store", _CoordinatorStore())
    monkeypatch.setattr(
        deployment_api,
        "_wait_due_source",
        lambda: [SimpleNamespace(task_id="task-retry")],
    )
    monkeypatch.setattr(deployment_api, "_wait_resume_executor", resume)
    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false")
    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false")

    coordinator = deployment_api.WorkflowTriggerCoordinator()
    await coordinator.run_once()
    await asyncio.sleep(0)
    await coordinator.run_once()
    await asyncio.sleep(0)
    assert started == ["task-retry"]
    assert list(coordinator._wait_resume_tasks) == ["task-retry"]

    release.set()
    await asyncio.gather(*coordinator._wait_resume_tasks.values())
    await asyncio.sleep(0)
    assert coordinator._wait_resume_tasks == {}


@pytest.mark.asyncio
async def test_resume_exception_is_isolated_and_task_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def resume(_task_id: str) -> dict[str, str]:
        raise RuntimeError("private provider response")

    monkeypatch.setattr(deployment_api, "_store", _CoordinatorStore())
    monkeypatch.setattr(
        deployment_api,
        "_wait_due_source",
        lambda: [SimpleNamespace(task_id="task-retry")],
    )
    monkeypatch.setattr(deployment_api, "_wait_resume_executor", resume)
    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false")
    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false")

    coordinator = deployment_api.WorkflowTriggerCoordinator()
    await coordinator.run_once()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert coordinator._wait_resume_tasks == {}
    assert "private provider response" not in caplog.text
    assert "failed safely" in caplog.text


@pytest.mark.asyncio
async def test_stop_drains_in_flight_resume_without_cancelling_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    cancelled = False

    async def resume(_task_id: str) -> dict[str, str]:
        nonlocal cancelled
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled = True
            raise
        return {"status": "completed"}

    monkeypatch.setattr(deployment_api, "_store", _CoordinatorStore())
    monkeypatch.setattr(
        deployment_api,
        "_wait_due_source",
        lambda: [SimpleNamespace(task_id="task-retry")],
    )
    monkeypatch.setattr(deployment_api, "_wait_resume_executor", resume)
    monkeypatch.setenv("WORKFLOW_RSS_TRIGGERS_ENABLED", "false")
    monkeypatch.setenv("WORKFLOW_IMAP_TRIGGERS_ENABLED", "false")

    coordinator = deployment_api.WorkflowTriggerCoordinator()
    await coordinator.run_once()
    await asyncio.sleep(0)
    asyncio.get_running_loop().call_soon(release.set)
    await coordinator.stop()

    assert cancelled is False
    assert coordinator._wait_resume_tasks == {}


def test_legacy_timer_runtime_configuration_maps_to_wait_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resume(_task_id: str) -> dict[str, str]:
        return {"status": "completed"}

    due = lambda: []
    monkeypatch.setattr(deployment_api, "_wait_due_source", None)
    monkeypatch.setattr(deployment_api, "_wait_resume_executor", None)
    deployment_api.configure_workflow_deployment_runtime(
        _CoordinatorStore(),
        timer_due_source=due,
        timer_resume_executor=resume,
    )
    assert deployment_api._wait_due_source is due
    assert deployment_api._wait_resume_executor is resume
