from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    OperationState,
    Origin,
    PolicyProfile,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.tool_broker import ToolBroker, ToolBrokerError
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


@pytest.mark.parametrize(
    ("tool_name", "arguments", "side_effecting"),
    (
        ("run_command", {"argv": ["pytest"]}, False),
        ("run_check", {"check_id": "pytest"}, False),
        ("run_shell", {"mode": "inspect"}, False),
        ("run_shell", {"mode": "mutate"}, True),
        ("write_file", {"path": "app.py"}, True),
        ("update_plan", {"items": []}, False),
    ),
)
def test_operation_side_effect_fact_uses_tool_semantics(
    tool_name: str, arguments: dict[str, object], side_effecting: bool
) -> None:
    assert (
        ToolBroker.operation_side_effecting(
            tool_name, {"arguments": arguments, "workspace_id": "workspace"}
        )
        is side_effecting
    )


async def _broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: PolicyProfile,
    harness_faults_enabled: bool = False,
) -> tuple[ToolBroker, CodingWorkerStore, str, Path, AsyncMock]:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SHELL_ENABLED", "true")
    source = WorkspaceSource(kind="manifest", source_id="source", revision="h0")
    workspace = WorkspaceBroker(
        tmp_path / "workspace",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "h0"): {"app.py": b"print('old')\n"}}
            )
        },
        id_key=b"w" * 32,
    )
    prepared = await workspace.prepare(source)
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    task = store.create_task(
        TaskSpec(
            client_task_id="shell-client",
            origin=Origin(module="tests", object_id="shell"),
            objective="run an exact shell operation",
            workspace_source=source,
            acceptance=AcceptanceContract(
                contract_id="contract",
                required_checks=(
                    AcceptanceCheck(
                        check_id="syntax", label="syntax", kind="command"
                    ),
                ),
            ),
            policy_profile=profile,
            model_route="coding/default",
        )
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(
        task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id
    )
    executor = AsyncMock()
    broker = ToolBroker(
        store=store,
        workspace_broker=workspace,
        executor=executor,
        harness_faults_enabled=harness_faults_enabled,
    )
    return (
        broker,
        store,
        task.task_id,
        workspace.repository_path(prepared.workspace_id),
        executor,
    )


async def _approve(
    broker: ToolBroker,
    store: CodingWorkerStore,
    *,
    task_id: str,
    operation_id: str,
    arguments: dict[str, object],
    arm_harness_fault: bool = False,
) -> str:
    with pytest.raises(ToolBrokerError) as required:
        await broker.execute(
            task_id=task_id,
            operation_id=operation_id,
            tool_name="run_shell",
            arguments=arguments,
        )
    assert required.value.code == "approval_required"
    approval = store.list_approvals(task_id)[-1]
    assert approval.capability == "shell"
    assert "script" not in approval.request
    assert approval.request == {
        "operation_id": operation_id,
        "script_sha256": hashlib.sha256(
            str(arguments["script"]).encode("utf-8")
        ).hexdigest(),
        "cwd": arguments["cwd"],
        "mode": arguments["mode"],
        "timeout_seconds": arguments["timeout_seconds"],
        "network_scope_sha256": None,
    }
    if arm_harness_fault:
        broker.arm_harness_fault(
            task_id, "executor", "after_side_effect_before_receipt"
        )
    lease = store.decide_approval(
        approval.approval_id, approved=True, task_scope=False
    ).lease
    assert lease is not None and lease.operation_limit == 1
    store.transition(
        task_id,
        TaskState.RUNNING,
        expected_state=TaskState.WAITING_APPROVAL,
    )
    return lease.lease_id


def _shell_result(
    broker: ToolBroker,
    store: CodingWorkerStore,
    task_id: str,
    *,
    mode: str,
    output: str,
    exit_code: int = 0,
    changes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    workspace_id = store.get_task(task_id).workspace_id
    assert workspace_id is not None
    selected_changes = changes or []
    return {
        "mode": mode,
        "exit_code": exit_code,
        "reason": None,
        "base_tree_hash": broker.workspace_broker.current_tree_hash(workspace_id),
        "clone_tree_hash": "c" * 64,
        "workspace_changed": bool(selected_changes),
        "changeset_eligible": mode == "mutate" and exit_code == 0,
        "changes": selected_changes if mode == "mutate" and exit_code == 0 else [],
        "change_summary": {
            "added": [],
            "modified": ["app.py"] if selected_changes else [],
            "deleted": [],
            "violations": [],
        },
        "output": output,
    }


@pytest.mark.asyncio
async def test_shell_approval_is_exact_and_inspect_output_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, repository, executor = await _broker(
        tmp_path, monkeypatch, profile=PolicyProfile.INSPECT
    )
    arguments: dict[str, object] = {
        "script": "pytest -q",
        "cwd": ".",
        "mode": "inspect",
        "timeout_seconds": 30,
    }
    lease_id = await _approve(
        broker,
        store,
        task_id=task_id,
        operation_id="shell_inspect",
        arguments=arguments,
    )

    async def execute(**kwargs: object) -> dict[str, object]:
        callback = kwargs["output_callback"]
        await callback("stdout", b"two passed\n")  # type: ignore[operator]
        return _shell_result(
            broker, store, task_id, mode="inspect", output="two passed\n"
        )

    executor.run_shell.side_effect = execute
    result = await broker.execute(
        task_id=task_id,
        operation_id="shell_inspect",
        tool_name="run_shell",
        arguments=arguments,
        lease_id=lease_id,
    )

    assert result.state is OperationState.COMPLETED
    assert result.data["exit_code"] == 0
    assert "output" not in result.data
    assert repository.joinpath("app.py").read_text() == "print('old')\n"
    output_events = [
        event
        for event in store.list_events(task_id)
        if event.type == "operation_output"
    ]
    assert output_events[-1].payload == {
        "operation_id": "shell_inspect",
        "stream": "stdout",
        "text": "two passed\n",
        "truncated": False,
    }
    artifact_id = str(result.data["output_artifact_id"])
    assert store.read_artifact(artifact_id, task_id=task_id) == b"two passed\n"
    with pytest.raises(ToolBrokerError) as readonly:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_mutate_denied",
            tool_name="run_shell",
            arguments={**arguments, "mode": "mutate"},
        )
    assert readonly.value.code == "task_policy_readonly"
    assert store.get_operation("shell_mutate_denied").state is OperationState.FAILED


@pytest.mark.asyncio
async def test_shell_mutate_publishes_one_atomic_changeset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, repository, executor = await _broker(
        tmp_path, monkeypatch, profile=PolicyProfile.DEVELOP
    )
    arguments: dict[str, object] = {
        "script": "python fix.py",
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 60,
    }
    lease_id = await _approve(
        broker,
        store,
        task_id=task_id,
        operation_id="shell_mutate",
        arguments=arguments,
    )
    old = repository.joinpath("app.py").read_bytes()
    changed = b"print('fixed')\n"
    changes = [
        {
            "kind": "write",
            "path": "app.py",
            "expected_sha256": hashlib.sha256(old).hexdigest(),
            "content": changed.decode(),
            "content_sha256": hashlib.sha256(changed).hexdigest(),
        }
    ]

    async def execute(**kwargs: object) -> dict[str, object]:
        callback = kwargs["output_callback"]
        await callback("stdout", b"fixed\n")  # type: ignore[operator]
        return _shell_result(
            broker,
            store,
            task_id,
            mode="mutate",
            output="fixed\n",
            changes=changes,
        )

    executor.run_shell.side_effect = execute
    result = await broker.execute(
        task_id=task_id,
        operation_id="shell_mutate",
        tool_name="run_shell",
        arguments=arguments,
        lease_id=lease_id,
    )

    assert result.state is OperationState.COMPLETED
    assert result.data["changeset"]["state"] == "applied"
    assert repository.joinpath("app.py").read_bytes() == changed
    assert not broker.changesets.has_transaction(
        workspace_id=str(store.get_task(task_id).workspace_id),
        operation_id="shell_mutate",
    )
    executor.run_shell.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_shell_never_publishes_clone_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, repository, executor = await _broker(
        tmp_path, monkeypatch, profile=PolicyProfile.DEVELOP
    )
    arguments: dict[str, object] = {
        "script": "python failing.py",
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 60,
    }
    lease_id = await _approve(
        broker,
        store,
        task_id=task_id,
        operation_id="shell_failed",
        arguments=arguments,
    )

    async def execute(**kwargs: object) -> dict[str, object]:
        callback = kwargs["output_callback"]
        await callback("stderr", b"failure\n")  # type: ignore[operator]
        return _shell_result(
            broker,
            store,
            task_id,
            mode="mutate",
            output="failure\n",
            exit_code=7,
        )

    executor.run_shell.side_effect = execute
    result = await broker.execute(
        task_id=task_id,
        operation_id="shell_failed",
        tool_name="run_shell",
        arguments=arguments,
        lease_id=lease_id,
    )

    assert result.state is OperationState.COMPLETED
    assert result.data["exit_code"] == 7
    assert "changeset" not in result.data
    assert repository.joinpath("app.py").read_text() == "print('old')\n"


@pytest.mark.asyncio
async def test_shell_applied_unknown_reconciles_without_rerunning_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, repository, executor = await _broker(
        tmp_path, monkeypatch, profile=PolicyProfile.DEVELOP
    )
    arguments: dict[str, object] = {
        "script": "python fix.py",
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 60,
    }
    lease_id = await _approve(
        broker,
        store,
        task_id=task_id,
        operation_id="shell_unknown",
        arguments=arguments,
    )
    old = repository.joinpath("app.py").read_bytes()
    changed = b"print('once')\n"

    async def execute(**kwargs: object) -> dict[str, object]:
        return _shell_result(
            broker,
            store,
            task_id,
            mode="mutate",
            output="",
            changes=[
                {
                    "kind": "write",
                    "path": "app.py",
                    "expected_sha256": hashlib.sha256(old).hexdigest(),
                    "content": changed.decode(),
                    "content_sha256": hashlib.sha256(changed).hexdigest(),
                }
            ],
        )

    executor.run_shell.side_effect = execute
    original_transition = store.transition_operation
    failed_once = False

    def fail_terminal_once(
        operation_id: str,
        target: OperationState,
        **kwargs: object,
    ) -> object:
        nonlocal failed_once
        if target is OperationState.COMPLETED and not failed_once:
            failed_once = True
            raise OSError("simulated encrypted store interruption")
        return original_transition(operation_id, target, **kwargs)

    monkeypatch.setattr(store, "transition_operation", fail_terminal_once)
    with pytest.raises(ToolBrokerError) as unknown:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_unknown",
            tool_name="run_shell",
            arguments=arguments,
            lease_id=lease_id,
        )
    assert unknown.value.code == "operation_result_unknown"
    assert store.get_operation("shell_unknown").state is OperationState.UNKNOWN
    assert repository.joinpath("app.py").read_bytes() == changed

    reconciled = broker.reconcile("shell_unknown")
    assert reconciled.state is OperationState.COMPLETED
    assert reconciled.data["changeset"]["state"] == "applied"
    assert executor.run_shell.await_count == 1


@pytest.mark.asyncio
async def test_harness_executor_reset_after_changeset_requires_exact_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, repository, executor = await _broker(
        tmp_path,
        monkeypatch,
        profile=PolicyProfile.DEVELOP,
        harness_faults_enabled=True,
    )
    arguments: dict[str, object] = {
        "script": "python fix.py",
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 60,
    }
    lease_id = await _approve(
        broker,
        store,
        task_id=task_id,
        operation_id="shell_harness_reset",
        arguments=arguments,
        arm_harness_fault=True,
    )
    old = repository.joinpath("app.py").read_bytes()
    changed = b"print('fault-once')\n"

    async def execute(**kwargs: object) -> dict[str, object]:
        return _shell_result(
            broker,
            store,
            task_id,
            mode="mutate",
            output="",
            changes=[
                {
                    "kind": "write",
                    "path": "app.py",
                    "expected_sha256": hashlib.sha256(old).hexdigest(),
                    "content": changed.decode(),
                    "content_sha256": hashlib.sha256(changed).hexdigest(),
                }
            ],
        )

    executor.run_shell.side_effect = execute
    with pytest.raises(ToolBrokerError) as unknown:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_harness_reset",
            tool_name="run_shell",
            arguments=arguments,
            lease_id=lease_id,
        )

    assert unknown.value.code == "operation_result_unknown"
    assert store.get_operation("shell_harness_reset").state is OperationState.UNKNOWN
    assert repository.joinpath("app.py").read_bytes() == changed
    executor.close_task.assert_awaited_once()

    reconciled = broker.reconcile("shell_harness_reset")
    assert reconciled.state is OperationState.COMPLETED
    assert reconciled.data["changeset"]["state"] == "applied"
    assert executor.run_shell.await_count == 1
