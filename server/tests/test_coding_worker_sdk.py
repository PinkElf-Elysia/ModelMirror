from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    ContextReference,
    TaskCreateRequest,
    WorkspaceSource,
)
from server.coding_worker.provider import FakeCodingAgentProvider
from server.coding_worker.adapters import (
    LegacyHarnessDriver,
    LegacyHarnessSupervisor,
    legacy_substrate_from_service,
)
from server.coding_worker.ports import CodingSubstrateHandle
from server.coding_worker.sdk import CodingWorkerModuleClient, CodingWorkerSDKError
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


def _client(
    tmp_path: Path,
) -> tuple[CodingWorkerModuleClient, CodingSubstrateHandle]:
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    provider = FakeCodingAgentProvider()
    service = CodingWorkerService(
        store=store,
        workspace_broker=WorkspaceBroker(
            tmp_path / "work",
            {
                "manifest": InMemoryWorkspaceSourceAdapter(
                    {("source_01", "revision_01"): {"README.md": b"fixture\n"}}
                )
            },
            id_key=b"m" * 32,
        ),
        provider=LegacyHarnessDriver(provider),
        harness_supervisor=LegacyHarnessSupervisor(provider),
    )
    substrate = legacy_substrate_from_service(service)
    return CodingWorkerModuleClient(
        module="skill-creator",
        substrate=substrate,
        source_kinds=frozenset({"manifest"}),
        check_ids=frozenset({"python-tests"}),
        model_routes=frozenset({"coding/default"}),
        context_validators={"artifact": lambda value: value == "artifact_context"},
    ), substrate


def _request() -> TaskCreateRequest:
    return TaskCreateRequest(
        client_task_id="module-task-01",
        objective="Implement the requested module change.",
        workspace_source=WorkspaceSource(
            kind="manifest", source_id="source_01", revision="revision_01"
        ),
        acceptance=AcceptanceContract(
            contract_id="contract_01",
            required_checks=(
                AcceptanceCheck(
                    check_id="python-tests", label="Python tests", kind="command"
                ),
            ),
        ),
        model_route="coding/default",
        context_refs=(ContextReference(ref_id="artifact_context", kind="artifact"),),
    )


def _other_client(
    client: CodingWorkerModuleClient, substrate: CodingSubstrateHandle
) -> CodingWorkerModuleClient:
    return CodingWorkerModuleClient(
        module="mcp-creator",
        substrate=substrate,
        source_kinds=client.source_kinds,
        check_ids=client.check_ids,
        model_routes=client.model_routes,
        context_validators=client.context_validators,
    )


@pytest.mark.asyncio
async def test_module_client_owns_origin_and_preserves_idempotency(tmp_path: Path) -> None:
    client, _substrate = _client(tmp_path)
    first = await client.create_task(business_object_id="skill_01", request=_request())
    same = await client.create_task(business_object_id="skill_01", request=_request())
    assert same.task_id == first.task_id
    assert first.spec.origin.module == "skill-creator"
    assert first.spec.origin.object_id == "skill_01"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "expected"),
    (("source", "worker_source_not_registered"), ("check", "worker_acceptance_not_registered"), ("route", "worker_model_route_not_registered"), ("context", "worker_context_not_registered")),
)
async def test_module_client_rejects_unregistered_execution_inputs(
    tmp_path: Path, field: str, expected: str
) -> None:
    client, _substrate = _client(tmp_path)
    request = _request()
    if field == "source":
        request = request.model_copy(update={"workspace_source": request.workspace_source.model_copy(update={"kind": "host_git"})})
    elif field == "check":
        request = request.model_copy(update={"acceptance": AcceptanceContract(contract_id="other", required_checks=(AcceptanceCheck(check_id="shell", label="shell", kind="command"),))})
    elif field == "route":
        request = request.model_copy(update={"model_route": "coding/other"})
    else:
        request = request.model_copy(update={"context_refs": (ContextReference(ref_id="artifact_other", kind="artifact"),)})
    with pytest.raises(CodingWorkerSDKError) as raised:
        await client.create_task(business_object_id="skill_01", request=request)
    assert raised.value.code == expected


@pytest.mark.asyncio
async def test_module_client_reads_and_controls_only_its_own_origin(tmp_path: Path) -> None:
    client, substrate = _client(tmp_path)
    task = await client.create_task(business_object_id="skill_01", request=_request())

    assert client.get_task(
        business_object_id="skill_01", task_id=task.task_id
    ).task_id == task.task_id
    assert [event.type for event in client.list_events(
        business_object_id="skill_01", task_id=task.task_id
    )][0] == "task_created"

    foreign = _other_client(client, substrate)
    for action in (
        lambda: foreign.get_task(
            business_object_id="mcp_01", task_id=task.task_id
        ),
        lambda: foreign.list_events(
            business_object_id="mcp_01", task_id=task.task_id
        ),
    ):
        with pytest.raises(CodingWorkerSDKError) as raised:
            action()
        assert raised.value.code == "worker_task_not_owned"

    with pytest.raises(CodingWorkerSDKError) as raised:
        await foreign.cancel_task(
            business_object_id="mcp_01", task_id=task.task_id
        )
    assert raised.value.code == "worker_task_not_owned"


@pytest.mark.asyncio
async def test_module_client_bounds_public_event_and_message_inputs(tmp_path: Path) -> None:
    client, _substrate = _client(tmp_path)
    task = await client.create_task(business_object_id="skill_01", request=_request())

    with pytest.raises(CodingWorkerSDKError) as cursor:
        client.list_events(
            business_object_id="skill_01", task_id=task.task_id, limit=1001
        )
    assert cursor.value.code == "worker_event_cursor_invalid"

    with pytest.raises(CodingWorkerSDKError) as message:
        await client.append_message(
            business_object_id="skill_01", task_id=task.task_id, message="   "
        )
    assert message.value.code == "worker_message_invalid"


def test_module_sdk_has_no_provider_tool_or_secret_registration_surface() -> None:
    forbidden = {
        "register_provider",
        "register_tool",
        "register_process",
        "register_secret",
        "register_mcp_server",
    }
    assert forbidden.isdisjoint(vars(CodingWorkerModuleClient))


def test_module_sdk_retains_only_control_and_projection_ports(tmp_path: Path) -> None:
    client, _substrate = _client(tmp_path)
    assert not hasattr(client, "service")
    assert not hasattr(client, "_substrate")
    assert hasattr(client, "_control_plane")
    assert hasattr(client, "_projection")
