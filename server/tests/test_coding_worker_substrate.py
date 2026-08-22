from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.adapters import (
    LegacyHarnessDriver,
    LegacyTaskControlPlane,
    StoreInteractionProjection,
)
from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskCreateRequest,
    WorkspaceSource,
)
from server.coding_worker.provider import FakeCodingAgentProvider
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore
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
    return CodingWorkerService(
        store=store,
        workspace_broker=workspace,
        provider=LegacyHarnessDriver(FakeCodingAgentProvider()),
    )


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
    assert projection.list_tasks(origin=origin) == service.store.list_tasks(
        origin=origin
    )
    assert projection.list_events(created.task_id) == service.store.list_events(
        created.task_id
    )

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
    assert projection.get_operation(operation.operation_id) == operation
    assert projection.list_events(created.task_id) == service.store.list_events(
        created.task_id
    )
    assert projection.list_questions(created.task_id) == []
    assert projection.list_evidence(created.task_id) == []
    assert projection.list_artifacts(created.task_id) == []
    assert projection.latest_plan(created.task_id) is None
    assert projection.latest_todo(created.task_id) is None
    assert projection.turn_history(created.task_id) == service.store.turn_history(
        created.task_id
    )
