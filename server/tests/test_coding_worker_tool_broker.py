from __future__ import annotations

import asyncio
import hashlib
import sys
from unittest.mock import AsyncMock
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
from server.coding_worker.process_manager import BackgroundProcessManager
from server.coding_worker.network_policy import EgressPolicy
from server.coding_worker.executor import SidecarExecutor
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
async def test_command_execution_can_be_delegated_to_sidecar(tmp_path: Path) -> None:
    broker, store, task_id, _ = await _broker(tmp_path)
    executor = AsyncMock()
    executor.run_process.return_value = {
        "argv": ["python", "-V"],
        "exit_code": 0,
        "output": "Python sidecar",
    }
    broker.executor = executor
    arguments = {"argv": ["python", "-V"], "timeout_seconds": 30}
    approval = store.create_approval(
        task_id=task_id,
        operation_id="sidecar-command",
        capability="command",
        request=arguments,
    )
    lease = store.decide_approval(
        approval.approval_id, approved=True, task_scope=False
    ).lease
    assert lease is not None
    result = await broker.execute(
        task_id=task_id,
        operation_id="sidecar-command",
        tool_name="run_command",
        arguments=arguments,
        lease_id=lease.lease_id,
    )
    assert result.data["output"] == "Python sidecar"
    executor.run_process.assert_awaited_once()
    executor.service_status.return_value = {
        "service_id": "service_" + "a" * 32,
        "task_id": task_id,
        "state": "completed",
        "output": "archived sidecar output",
    }
    status = await broker.execute(
        task_id=task_id,
        operation_id="sidecar-status",
        tool_name="service_status",
        arguments={"service_id": "service_" + "a" * 32},
    )
    assert "output" not in status.data
    artifact_id = status.data["output_artifact_id"]
    assert store.read_artifact(artifact_id, task_id=task_id) == b"archived sidecar output"


@pytest.mark.asyncio
async def test_sidecar_executor_owns_commands_and_background_services(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "slot" / "workspaces" / "workspace_one" / "repo"
    repository.mkdir(parents=True)
    executor = SidecarExecutor(
        lambda _workspace_id: repository,
        runtime_root=tmp_path / "slot" / "runtime",
    )
    command = await executor.run_process(
        workspace_id="workspace_one",
        argv=(sys.executable, "-c", "print('sidecar-command')"),
        timeout_seconds=10,
        isolated=False,
    )
    assert command["exit_code"] == 0
    assert command["output"].strip() == "sidecar-command"
    service = await executor.start_service(
        task_id="task_sidecar",
        workspace_id="workspace_one",
        argv=(sys.executable, "-c", "print('sidecar-service')"),
        ttl_seconds=10,
        preview_port=4173,
    )
    for _ in range(100):
        status = executor.service_status(
            task_id="task_sidecar", service_id=str(service["service_id"])
        )
        if status["state"] != "running":
            break
        await asyncio.sleep(0.01)
    assert status["state"] == "completed"
    assert str(status["output"]).strip() == "sidecar-service"
    assert status["preview_port"] == 4173


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
async def test_command_is_killed_while_output_limit_is_crossed(tmp_path: Path) -> None:
    broker, store, task_id, _ = await _broker(tmp_path)
    broker.max_output_bytes = 1024
    argv = ["python", "-c", "print('x' * 5000)"]
    approval = store.create_approval(
        task_id=task_id,
        operation_id="approve-large-output",
        capability="command",
        request={"argv": argv},
    )
    lease = store.decide_approval(approval.approval_id, approved=True).lease
    assert lease is not None
    with pytest.raises(ToolBrokerError) as too_large:
        await broker.execute(
            task_id=task_id,
            operation_id="large-output",
            tool_name="run_command",
            arguments={"argv": argv},
            lease_id=lease.lease_id,
        )
    assert too_large.value.code == "tool_output_too_large"


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


@pytest.mark.asyncio
async def test_background_service_requires_exact_lease_and_remains_task_owned(
    tmp_path: Path,
) -> None:
    broker, store, task_id, _ = await _broker(tmp_path)
    manager = BackgroundProcessManager(
        store=store,
        workspace_broker=broker.workspace_broker,
        environment_factory=lambda workspace_id: broker._safe_environment(
            broker.workspace_broker.repository_path(workspace_id)
        ),
    )
    broker.process_manager = manager
    arguments = {
        "argv": [
            "python",
            "-c",
            "import sys,time;print('ready',flush=True);"
            "line=sys.stdin.readline();print(line,flush=True);time.sleep(30)",
        ],
        "ttl_seconds": 60,
    }
    with pytest.raises(ToolBrokerError) as approval_required:
        await broker.execute(
            task_id=task_id,
            operation_id="start-service",
            tool_name="start_service",
            arguments=arguments,
        )
    assert approval_required.value.code == "approval_required"
    approval = store.list_approvals(task_id)[-1]
    lease = store.decide_approval(approval.approval_id, approved=True).lease
    assert lease is not None
    store.transition(task_id, TaskState.RUNNING)
    started = await broker.execute(
        task_id=task_id,
        operation_id="start-service",
        tool_name="start_service",
        arguments=arguments,
        lease_id=lease.lease_id,
    )
    service_id = str(started.data["service_id"])
    await broker.execute(
        task_id=task_id,
        operation_id="service-input",
        tool_name="service_input",
        arguments={"service_id": service_id, "data": "hello\n"},
    )
    status = await broker.execute(
        task_id=task_id,
        operation_id="service-status",
        tool_name="service_status",
        arguments={"service_id": service_id},
    )
    assert status.data["task_id"] == task_id and "pid" not in status.data
    stopped = await broker.execute(
        task_id=task_id,
        operation_id="service-stop",
        tool_name="stop_service",
        arguments={"service_id": service_id},
    )
    assert stopped.data["reason"] == "user_interrupted"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_dependency_install_requires_both_exact_leases_and_records_source(
    tmp_path: Path,
) -> None:
    broker, store, task_id, _ = await _broker(
        tmp_path, profile=PolicyProfile.DEVELOP_NETWORKED
    )
    broker.egress_policy = EgressPolicy(
        enabled=True,
        allowed_domains={"registry.npmjs.org"},
        grant_key=b"g" * 32,
    )
    broker.egress_proxy_url = "http://worker-egress:8080"
    broker._run_process = AsyncMock(
        return_value={"argv": ["npm", "ci"], "exit_code": 0, "output": "installed"}
    )
    arguments = {"manager": "npm", "action": "ci"}
    with pytest.raises(ToolBrokerError) as approval_required:
        await broker.execute(
            task_id=task_id,
            operation_id="dependency-install",
            tool_name="install_dependencies",
            arguments=arguments,
        )
    assert approval_required.value.code == "approval_required"
    approvals = store.list_approvals(task_id)
    assert {item.capability for item in approvals} == {"dependency_install", "network"}
    decided = {
        item.capability: store.decide_approval(item.approval_id, approved=True).lease
        for item in approvals
    }
    store.transition(task_id, TaskState.RUNNING)
    result = await broker.execute(
        task_id=task_id,
        operation_id="dependency-install",
        tool_name="install_dependencies",
        arguments=arguments,
        lease_id=decided["dependency_install"].lease_id,
        network_lease_id=decided["network"].lease_id,
    )
    assert result.data["exit_code"] == 0
    source = store.read_artifact(result.data["source_artifact_id"], task_id=task_id)
    assert b"registry.npmjs.org" in source and b"worker-egress" not in source
    call = broker._run_process.await_args
    proxy = call.kwargs["environment_overrides"]["HTTPS_PROXY"]
    assert proxy.startswith("http://grant:") and proxy.endswith("@worker-egress:8080")


@pytest.mark.asyncio
async def test_dependency_install_is_disabled_without_global_egress_policy(
    tmp_path: Path,
) -> None:
    broker, _, task_id, _ = await _broker(
        tmp_path, profile=PolicyProfile.DEVELOP_NETWORKED
    )
    with pytest.raises(ToolBrokerError) as disabled:
        await broker.execute(
            task_id=task_id,
            operation_id="dependency-disabled",
            tool_name="install_dependencies",
            arguments={"manager": "npm", "action": "ci"},
        )
    assert disabled.value.code == "network_disabled"
