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
from server.coding_worker.sdk import CodingWorkerModuleClient, CodingWorkerSDKError
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.workspace import WorkspaceBroker


def _client(tmp_path: Path) -> CodingWorkerModuleClient:
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    service = CodingWorkerService(
        store=store,
        workspace_broker=WorkspaceBroker(tmp_path / "work", {}, id_key=b"m" * 32),
        provider=FakeCodingAgentProvider(),
    )
    return CodingWorkerModuleClient(
        module="skill-creator",
        service=service,
        source_kinds=frozenset({"manifest"}),
        check_ids=frozenset({"python-tests"}),
        model_routes=frozenset({"coding/default"}),
        context_validators={"artifact": lambda value: value == "artifact_context"},
    )


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


@pytest.mark.asyncio
async def test_module_client_owns_origin_and_preserves_idempotency(tmp_path: Path) -> None:
    client = _client(tmp_path)
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
    client = _client(tmp_path)
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
