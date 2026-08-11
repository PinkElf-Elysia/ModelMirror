from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskCreateRequest,
    TaskSpec,
    TaskState,
    WorkspaceSource,
    require_transition,
)
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderEventKind,
    ProviderOpenRequest,
)


def _request(**overrides: object) -> TaskCreateRequest:
    values: dict[str, object] = {
        "client_task_id": "client-task-01",
        "objective": "Fix the failing tests and provide evidence.",
        "workspace_source": WorkspaceSource(
            kind="manifest", source_id="local-abc", revision="rev-01"
        ),
        "acceptance": AcceptanceContract(
            contract_id="python-test-contract",
            required_checks=(
                AcceptanceCheck(
                    check_id="pytest", label="Python tests", kind="command"
                ),
            ),
        ),
        "model_route": "coding/default",
    }
    values.update(overrides)
    return TaskCreateRequest.model_validate(values)


def test_public_task_request_cannot_supply_origin_or_execution_details() -> None:
    payload = _request().model_dump()
    payload["origin"] = {"module": "skill", "object_id": "forged"}
    with pytest.raises(ValidationError, match="origin"):
        TaskCreateRequest.model_validate(payload)

    for field, value in (
        ("physical_path", "C:/secret"),
        ("environment", {"TOKEN": "secret"}),
        ("provider", "opencode"),
        ("remote_url", "https://example.invalid/repo.git"),
    ):
        candidate = _request().model_dump()
        candidate[field] = value
        with pytest.raises(ValidationError, match=field):
            TaskCreateRequest.model_validate(candidate)


def test_acceptance_contract_is_frozen_and_cannot_be_empty() -> None:
    contract = _request().acceptance
    with pytest.raises(ValidationError):
        contract.required_checks = ()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        AcceptanceContract(contract_id="empty", required_checks=())


def test_task_state_machine_rejects_completion_without_testing() -> None:
    with pytest.raises(ValueError, match="invalid task transition"):
        require_transition(TaskState.RUNNING, TaskState.COMPLETED)
    require_transition(TaskState.RUNNING, TaskState.TESTING)
    require_transition(TaskState.TESTING, TaskState.COMPLETED)


@pytest.mark.asyncio
async def test_fake_provider_implements_neutral_open_stream_checkpoint_and_cancel() -> None:
    request = _request()
    spec = TaskSpec(**request.model_dump(), origin=Origin(module="console", object_id="user"))
    provider = FakeCodingAgentProvider()
    session = await provider.open(
        ProviderOpenRequest(
            task_id="task-01",
            workspace_id="workspace-01",
            objective=spec.objective,
            model_route=spec.model_route,
            policy_profile=spec.policy_profile,
            budget=spec.budget,
        )
    )
    events = [event async for event in provider.message(session, spec.objective)]
    assert [event.kind for event in events][-1] is ProviderEventKind.TURN_COMPLETED
    checkpoint = await provider.checkpoint(session)
    restored = await provider.restore(
        ProviderOpenRequest(
            task_id="task-01",
            workspace_id="workspace-01",
            objective=spec.objective,
            model_route=spec.model_route,
            policy_profile=spec.policy_profile,
            budget=spec.budget,
        ),
        checkpoint,
    )
    assert restored.task_id == session.task_id

    blocker = asyncio.Event()
    blocking = FakeCodingAgentProvider(block=blocker)
    blocking_session = await blocking.open(
        ProviderOpenRequest(
            task_id="task-02",
            workspace_id="workspace-02",
            objective="Wait",
            model_route="coding/default",
            policy_profile="inspect",
            budget=spec.budget,
        )
    )
    assert await blocking.cancel(blocking_session) is True
    blocker.set()
    cancelled = [event async for event in blocking.message(blocking_session, "Wait")]
    assert cancelled[-1].kind is ProviderEventKind.CANCELLED
