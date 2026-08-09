from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    OperationState,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.tool_broker import FrozenCheck, ToolBroker, ToolBrokerError
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


async def _broker(
    tmp_path: Path, *, profile: PolicyProfile = PolicyProfile.DEVELOP
) -> tuple[ToolBroker, CodingWorkerStore, str, Path]:
    source = WorkspaceSource(kind="manifest", source_id="source", revision="h0")
    workspace = WorkspaceBroker(
        tmp_path / "broker",
        {"manifest": InMemoryWorkspaceSourceAdapter({("source", "h0"): {"app.py": b"print('old')\n"}})},
        id_key=b"w" * 32,
    )
    prepared = await workspace.prepare(source)
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    task = store.create_task(
        TaskSpec(
            client_task_id="client",
            origin=Origin(module="tests", object_id="broker"),
            objective="change app",
            workspace_source=source,
            acceptance=AcceptanceContract(
                contract_id="contract",
                required_checks=(AcceptanceCheck(check_id="syntax", label="syntax", kind="command"),),
            ),
            policy_profile=profile,
            model_route="coding/default",
        )
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id)
    broker = ToolBroker(
        store=store,
        workspace_broker=workspace,
        frozen_checks={
            "syntax": FrozenCheck(check_id="syntax", argv=(sys.executable, "-m", "py_compile", "app.py"))
        },
    )
    return broker, store, task.task_id, workspace.repository_path(prepared.workspace_id)


@pytest.mark.asyncio
async def test_develop_can_write_search_diff_and_run_frozen_check(tmp_path: Path) -> None:
    broker, _, task_id, repository = await _broker(tmp_path)
    content = "print('new')\n"
    digest = hashlib.sha256(content.encode()).hexdigest()
    written = await broker.execute(
        task_id=task_id,
        operation_id="write-01",
        tool_name="write_file",
        arguments={"path": "app.py", "content": content, "content_sha256": digest},
    )
    assert written.data["sha256"] == digest
    same = await broker.execute(
        task_id=task_id,
        operation_id="write-01",
        tool_name="write_file",
        arguments={"path": "app.py", "content": content, "content_sha256": digest},
    )
    assert same == written
    search = await broker.execute(
        task_id=task_id,
        operation_id="search-01",
        tool_name="search_text",
        arguments={"query": "new"},
    )
    assert search.data["matches"][0]["path"] == "app.py"
    diff = await broker.execute(
        task_id=task_id, operation_id="diff-01", tool_name="diff", arguments={}
    )
    assert "+print('new')" in diff.data["diff"]
    check = await broker.execute(
        task_id=task_id,
        operation_id="check-01",
        tool_name="run_check",
        arguments={"check_id": "syntax"},
    )
    assert check.data["exit_code"] == 0
    assert repository.joinpath("app.py").read_text() == content


@pytest.mark.asyncio
async def test_inspect_policy_and_paths_fail_closed(tmp_path: Path) -> None:
    broker, _, task_id, repository = await _broker(tmp_path, profile=PolicyProfile.INSPECT)
    content = "bad\n"
    with pytest.raises(ToolBrokerError) as readonly:
        await broker.execute(
            task_id=task_id,
            operation_id="write-readonly",
            tool_name="write_file",
            arguments={
                "path": "app.py",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            },
        )
    assert readonly.value.code == "approval_required"
    with pytest.raises(ToolBrokerError) as traversal:
        await broker.execute(
            task_id=task_id,
            operation_id="read-traversal",
            tool_name="read_file",
            arguments={"path": "../secret"},
        )
    assert traversal.value.code == "workspace_path_invalid"
    assert repository.joinpath("app.py").read_text() == "print('old')\n"


@pytest.mark.asyncio
async def test_command_requires_exact_once_lease_and_denies_shell_remote_and_environment(
    tmp_path: Path,
) -> None:
    broker, store, task_id, _ = await _broker(tmp_path)
    argv = ["python", "-c", "import os;print(os.getenv('LLM_GATEWAY_KEY'))"]
    approval = store.create_approval(
        task_id=task_id,
        operation_id="approve-command",
        capability="command",
        request={"argv": argv},
    )
    lease = store.decide_approval(approval.approval_id, approved=True).lease
    assert lease is not None
    result = await broker.execute(
        task_id=task_id,
        operation_id="command-01",
        tool_name="run_command",
        arguments={"argv": argv},
        lease_id=lease.lease_id,
    )
    assert result.data["output"].strip() == "None"
    with pytest.raises(ToolBrokerError):
        await broker.execute(
            task_id=task_id,
            operation_id="command-02",
            tool_name="run_command",
            arguments={"argv": argv},
            lease_id=lease.lease_id,
        )
    for denied in (["sh", "-c", "id"], ["git", "push", "origin", "main"]):
        approval = store.create_approval(
            task_id=task_id,
            operation_id=f"approve-{denied[0]}-{denied[1]}",
            capability="command",
            request={"argv": denied},
        )
        denied_lease = store.decide_approval(approval.approval_id, approved=True).lease
        assert denied_lease is not None
        with pytest.raises(ToolBrokerError) as error:
            await broker.execute(
                task_id=task_id,
                operation_id=f"command-{denied[0]}-{denied[1]}",
                tool_name="run_command",
                arguments={"argv": denied},
                lease_id=denied_lease.lease_id,
            )
        assert error.value.code == "command_denied"


@pytest.mark.asyncio
async def test_unknown_write_reconciles_but_command_never_replays(tmp_path: Path) -> None:
    broker, store, task_id, repository = await _broker(tmp_path)
    content = "print('reconciled')\n"
    digest = hashlib.sha256(content.encode()).hexdigest()
    request = {
        "arguments": {"path": "app.py", "content": content, "content_sha256": digest},
        "lease_id": None,
        "workspace_id": store.get_task(task_id).workspace_id,
    }
    operation = store.create_operation(
        task_id=task_id,
        operation_id="unknown-write",
        tool_name="write_file",
        intent_sha256=broker._intent_sha256("write_file", request),
        request=request,
    )
    store.transition_operation(operation.operation_id, OperationState.RUNNING)
    repository.joinpath("app.py").write_bytes(content.encode("utf-8"))
    store.mark_inflight_operations_unknown()
    reconciled = broker.reconcile(operation.operation_id)
    assert reconciled.state.value == "completed"

    command = store.create_operation(
        task_id=task_id,
        operation_id="unknown-command",
        tool_name="run_command",
        intent_sha256="f" * 64,
        request={"arguments": {"argv": ["python", "-V"]}, "workspace_id": store.get_task(task_id).workspace_id},
    )
    store.transition_operation(command.operation_id, OperationState.RUNNING)
    store.mark_inflight_operations_unknown()
    with pytest.raises(ToolBrokerError) as unknown:
        broker.reconcile(command.operation_id)
    assert unknown.value.code == "operation_result_unknown"


@pytest.mark.asyncio
async def test_missing_command_lease_creates_one_approval_and_same_operation_resumes(
    tmp_path: Path,
) -> None:
    broker, store, task_id, _ = await _broker(tmp_path)
    argv = ["python", "-c", "print('approved')"]
    with pytest.raises(ToolBrokerError) as pending:
        await broker.execute(
            task_id=task_id,
            operation_id="approval-command",
            tool_name="run_command",
            arguments={"argv": argv},
        )
    assert pending.value.code == "approval_required"
    approvals = store.list_approvals(task_id)
    assert len(approvals) == 1
    decided = store.decide_approval(approvals[0].approval_id, approved=True)
    assert decided.lease is not None
    store.transition(task_id, TaskState.RUNNING)
    result = await broker.execute(
        task_id=task_id,
        operation_id="approval-command",
        tool_name="run_command",
        arguments={"argv": argv},
        lease_id=decided.lease.lease_id,
    )
    assert result.data["output"].strip() == "approved"
