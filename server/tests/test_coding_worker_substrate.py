from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.adapters import (
    LegacyExecutionBackend,
    LegacyHarnessDriver,
    LegacyHarnessSupervisor,
    LegacyTaskControlPlane,
    StoreInteractionProjection,
)
from server.coding_worker.ports import CodingSubstrateError
from server.coding_worker.harness_contracts import (
    HarnessCheckpoint,
    HarnessEvent,
    HarnessEventKind,
    HarnessOpenRequest,
    HarnessSession,
)
from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskCreateRequest,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    ProviderCheckpoint,
    ProviderOpenRequest,
    ProviderSession,
)
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError
from server.coding_worker.workspace import (
    InMemoryWorkspaceSourceAdapter,
    WorkspaceBroker,
)


def _service(tmp_path: Path) -> CodingWorkerService:
    store = CodingWorkerStore(
        tmp_path / "state", master_key=Fernet.generate_key()
    )
    workspace = WorkspaceBroker(
        tmp_path / "state",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "h0"): {"main.py": b"print('ok')\n"}}
            )
        },
        id_key=b"s" * 32,
    )
    provider = FakeCodingAgentProvider()
    return CodingWorkerService(
        store=store,
        workspace_broker=workspace,
        provider=LegacyHarnessDriver(provider),
        harness_supervisor=LegacyHarnessSupervisor(provider),
    )


@pytest.mark.asyncio
async def test_legacy_execution_backend_fails_closed_when_capability_is_absent() -> None:
    backend = LegacyExecutionBackend(SimpleNamespace())

    with pytest.raises(CodingSubstrateError) as caught:
        await backend.run_shell(task_id="task", workspace_id="workspace")

    assert caught.value.code == "execution_backend_unavailable"
    assert caught.value.status == 503


@pytest.mark.asyncio
async def test_legacy_harness_driver_does_not_claim_native_steering() -> None:
    provider = FakeCodingAgentProvider()
    driver = LegacyHarnessDriver(provider)
    session = await driver.open(
        HarnessOpenRequest(
            task_id="task-steering",
            workspace_id="workspace-steering",
            objective="Inspect steering support.",
            model_route="coding/default",
            policy_profile="inspect",
            budget={},
        )
    )

    assert await driver.steer(session, "change direction") is False
    await driver.close(session)


class _SharedSessionIdProvider(FakeCodingAgentProvider):
    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        return ProviderSession(
            session_id="shared-session",
            task_id=request.task_id,
            provider_capabilities=await self.capabilities(),
        )

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        return ProviderCheckpoint(
            checkpoint_id=f"checkpoint_{session.task_id}",
            payload={"task_id": session.task_id},
        )


@pytest.mark.asyncio
async def test_legacy_harness_driver_scopes_equal_session_ids_by_task() -> None:
    driver = LegacyHarnessDriver(_SharedSessionIdProvider())

    async def open_task(task_id: str) -> HarnessSession:
        return await driver.open(
            HarnessOpenRequest(
                task_id=task_id,
                workspace_id=f"workspace-{task_id}",
                objective="Inspect composite session binding.",
                model_route="coding/default",
                policy_profile="inspect",
                budget={},
            )
        )

    first = await open_task("taska")
    second = await open_task("taskb")

    assert first.session_id == second.session_id
    assert (await driver.checkpoint(first)).payload["task_id"] == "taska"
    assert (await driver.checkpoint(second)).payload["task_id"] == "taskb"
    await driver.close(first)
    await driver.close(second)


@pytest.mark.asyncio
async def test_legacy_harness_driver_translates_private_carriers_at_adapter() -> None:
    provider = FakeCodingAgentProvider()
    driver = LegacyHarnessDriver(provider)
    request = HarnessOpenRequest(
        task_id="task-neutral",
        workspace_id="workspace-neutral",
        objective="Inspect the neutral boundary.",
        model_route="coding/default",
        policy_profile="inspect",
        budget={},
    )

    session = await driver.open(request)
    events = [
        event
        async for event in driver.message(
            session, "continue", turn_id="turn-neutral"
        )
    ]
    checkpoint = await driver.checkpoint(session)

    assert type(session) is HarnessSession
    assert all(type(event) is HarnessEvent for event in events)
    assert events[-1].kind is HarnessEventKind.TURN_COMPLETED
    assert type(checkpoint) is HarnessCheckpoint
    assert ProviderCheckpoint.model_validate(
        checkpoint.model_dump(mode="json")
    ).model_dump(mode="json") == checkpoint.model_dump(mode="json")
    await driver.close(session)


@pytest.mark.asyncio
async def test_shadow_projection_matches_legacy_store_without_double_command(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    control = LegacyTaskControlPlane(service)
    projection = StoreInteractionProjection(service)
    origin = Origin(module="shadow", object_id="object-1")
    request = TaskCreateRequest(
        client_task_id="shadow-create",
        objective="Inspect once.",
        workspace_source=WorkspaceSource(
            kind="manifest", source_id="source", revision="h0"
        ),
        acceptance=AcceptanceContract(
            contract_id="shadow",
            required_checks=(
                AcceptanceCheck(
                    check_id="pytest", label="pytest", kind="command"
                ),
            ),
        ),
        model_route="coding/default",
    )

    created = await control.create_task(origin, request)
    replay = await control.create_task(origin, request)
    assert replay.task_id == created.task_id
    assert len(service.store.list_tasks(origin=origin)) == 1
    assert projection.get_task(created.task_id) == service.store.get_task(
        created.task_id
    )
    assert control.status().max_active_tasks == 2
    assert control.status().network_enabled is False
    projected_capabilities = projection.get_task_capability_snapshot(created.task_id)
    stored_capabilities = service.store.get_task_capability_snapshot(created.task_id)
    assert projected_capabilities is not None and stored_capabilities is not None
    assert projected_capabilities.model_dump() == {
        "task_id": stored_capabilities.task_id,
        "binding_sha256": stored_capabilities.binding_sha256,
        "snapshot": stored_capabilities.snapshot,
        "observed_at": stored_capabilities.observed_at,
        "expires_at": stored_capabilities.expires_at,
    }
    assert projection.list_tasks(origin=origin) == service.store.list_tasks(
        origin=origin
    )
    assert projection.list_events(created.task_id) == service.store.list_events(
        created.task_id
    )
    assert projection.get_task(created.task_id).model_dump(mode="json") == (
        service.store.get_task(created.task_id).model_dump(mode="json")
    )
    assert [
        item.model_dump(mode="json")
        for item in projection.list_events(created.task_id)
    ] == [
        item.model_dump(mode="json")
        for item in service.store.list_events(created.task_id)
    ]

    approval = service.store.create_approval(
        task_id=created.task_id,
        operation_id="shadow-operation",
        capability="command",
        request={"argv": ["python", "-m", "pytest"]},
    )
    operation = service.store.create_operation(
        task_id=created.task_id,
        operation_id="shadow-read-operation",
        tool_name="read_file",
        intent_sha256="a" * 64,
        request={"path": "main.py"},
    )
    assert projection.list_approvals(created.task_id) == [approval]
    decided = control.decide_approval(
        created.task_id,
        approval.approval_id,
        approved=False,
        task_scope=False,
        ttl_seconds=900,
    )
    assert projection.list_approvals(created.task_id) == [decided]
    assert projection.get_approval(approval.approval_id).model_dump(mode="json") == (
        service.store.get_approval(approval.approval_id).model_dump(mode="json")
    )
    assert projection.get_operation(operation.operation_id) == operation
    assert projection.list_events(created.task_id) == service.store.list_events(
        created.task_id
    )
    assert projection.list_questions(created.task_id) == []
    assert projection.list_evidence(created.task_id) == []
    assert projection.list_artifacts(created.task_id) == []
    assert projection.list_children(created.task_id) == []
    assert projection.list_subtasks(created.task_id) == []
    assert projection.latest_plan(created.task_id) is None
    assert projection.latest_todo(created.task_id) is None
    assert projection.turn_history(created.task_id) == service.store.turn_history(
        created.task_id
    )
    approval_events = [
        item
        for item in projection.list_events(created.task_id)
        if item.type == "approval_decided"
    ]
    assert len(approval_events) == 1

    for state in (
        TaskState.PREPARING,
        TaskState.RUNNING,
        TaskState.TESTING,
        TaskState.COMPLETED,
    ):
        service.store.transition(created.task_id, state, reason="shadow-terminal")
    assert projection.get_task(created.task_id).model_dump(mode="json") == (
        service.store.get_task(created.task_id).model_dump(mode="json")
    )
    assert projection.get_task(created.task_id).state is TaskState.COMPLETED


@pytest.mark.asyncio
async def test_writeback_candidate_is_acceptance_tree_and_patch_hash_bound(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    created = await service.create_task(
        Origin(module="shadow", object_id="writeback"),
        TaskCreateRequest(
            client_task_id="writeback-candidate",
            objective="Prepare host writeback.",
            workspace_source=WorkspaceSource(
                kind="manifest", source_id="source", revision="h0"
            ),
            acceptance=AcceptanceContract(
                contract_id="writeback",
                required_checks=(
                    AcceptanceCheck(
                        check_id="pytest", label="pytest", kind="command"
                    ),
                ),
            ),
            model_route="coding/default",
        ),
    )
    task = created.model_copy(
        update={
            "state": TaskState.COMPLETED,
            "workspace_id": "workspace_" + "a" * 32,
            "spec": created.spec.model_copy(
                update={
                    "workspace_source": WorkspaceSource(
                        kind="host_snapshot",
                        source_id="project-1",
                        revision="f" * 40,
                    )
                }
            ),
        }
    )
    patch = b"diff --git a/main.py b/main.py\n"

    class Workspace:
        def __init__(self) -> None:
            self.hashes = iter(("b" * 64, "b" * 64))
            self.rename_flags: list[bool] = []

        def current_tree_hash(self, workspace_id: str) -> str:
            assert workspace_id == task.workspace_id
            return next(self.hashes)

        def diff(self, workspace_id: str, *, detect_renames: bool) -> bytes:
            assert workspace_id == task.workspace_id
            self.rename_flags.append(detect_renames)
            return patch

    workspace = Workspace()
    control = LegacyTaskControlPlane(
        SimpleNamespace(
            store=SimpleNamespace(get_task=lambda task_id: task),
            harness_runner=SimpleNamespace(
                acceptance_satisfied=lambda task_id: True
            ),
            workspace_broker=workspace,
        )
    )
    candidate = await control.prepare_writeback_candidate(task.task_id)
    assert candidate.patch == patch
    assert candidate.patch_sha256 == hashlib.sha256(patch).hexdigest()
    assert candidate.workspace_tree_hash == "b" * 64
    assert workspace.rename_flags == [False]


@pytest.mark.asyncio
async def test_writeback_candidate_rejects_tree_race(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = await service.create_task(
        Origin(module="shadow", object_id="writeback-race"),
        TaskCreateRequest(
            client_task_id="writeback-race",
            objective="Reject a raced writeback.",
            workspace_source=WorkspaceSource(
                kind="manifest", source_id="source", revision="h0"
            ),
            acceptance=AcceptanceContract(
                contract_id="writeback-race",
                required_checks=(
                    AcceptanceCheck(
                        check_id="pytest", label="pytest", kind="command"
                    ),
                ),
            ),
            model_route="coding/default",
        ),
    )
    task = created.model_copy(
        update={
            "state": TaskState.COMPLETED,
            "workspace_id": "workspace_" + "c" * 32,
            "spec": created.spec.model_copy(
                update={
                    "workspace_source": WorkspaceSource(
                        kind="host_snapshot",
                        source_id="project-1",
                        revision="f" * 40,
                    )
                }
            ),
        }
    )
    hashes = iter(("b" * 64, "c" * 64))
    control = LegacyTaskControlPlane(
        SimpleNamespace(
            store=SimpleNamespace(get_task=lambda task_id: task),
            harness_runner=SimpleNamespace(
                acceptance_satisfied=lambda task_id: True
            ),
            workspace_broker=SimpleNamespace(
                current_tree_hash=lambda workspace_id: next(hashes),
                diff=lambda workspace_id, *, detect_renames: b"patch",
            ),
        )
    )
    with pytest.raises(WorkerConflictError) as caught:
        await control.prepare_writeback_candidate(task.task_id)
    assert caught.value.code == "worker_acceptance_invalidated"
