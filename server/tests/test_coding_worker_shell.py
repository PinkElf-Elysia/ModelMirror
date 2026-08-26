from __future__ import annotations

import asyncio
import hashlib
import json
import time
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
    RuntimeProtocol,
    TaskSpec,
    TaskState,
    TurnBarrier,
    TurnTransactionState,
    WorkspaceSource,
)
from server.coding_worker.changeset import ChangesetError
from server.coding_worker.executor import ExecutorRPCError
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
    runtime_protocol: RuntimeProtocol = RuntimeProtocol.V16,
    harness_v20: bool = False,
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
    observed_at = time.time()
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
        ),
        runtime_protocol=runtime_protocol,
        capability_binding_sha256="a" * 64 if harness_v20 else None,
        capability_snapshot={"harness_protocol": "v20"} if harness_v20 else None,
        capability_observed_at=observed_at if harness_v20 else None,
        capability_expires_at=observed_at + 30 if harness_v20 else None,
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(
        task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id
    )
    if runtime_protocol is RuntimeProtocol.V17:
        store.open_turn_transaction(
            task_id=task.task_id,
            turn_id="turn_shell_transport",
            workspace_tree_hash=workspace.current_tree_hash(prepared.workspace_id),
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


@pytest.mark.parametrize(
    "script",
    (
        "cd /worker-data/workspaces/workspace_deadbeef/repo && pytest",
        r"python C:\private\repo\test.py",
        r"type \\host\share\test.py",
        "cat file:///private/repo/result.txt",
        "python ~/repo/test.py",
    ),
)
@pytest.mark.asyncio
async def test_shell_rejects_absolute_paths_before_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: str,
) -> None:
    broker, store, task_id, _, executor = await _broker(
        tmp_path, monkeypatch, profile=PolicyProfile.DEVELOP
    )

    with pytest.raises(ToolBrokerError) as rejected:
        await broker.execute(
            task_id=task_id,
            operation_id="absolute_path",
            tool_name="run_shell",
            arguments={
                "script": script,
                "cwd": ".",
                "mode": "inspect",
                "timeout_seconds": 120,
            },
        )

    assert rejected.value.code == "workspace_path_invalid"
    assert store.list_approvals(task_id) == []
    operation = store.get_operation("absolute_path")
    assert operation.state is OperationState.FAILED
    assert operation.result == {"code": "workspace_path_invalid"}
    executor.run_shell.assert_not_awaited()


@pytest.mark.parametrize(
    "script",
    (
        "python -m pytest tests/test_cache.py -v 2>&1",
        "sed -n '/foo/p' src/a.txt",
        "grep '/api/' src/a.txt",
    ),
)
@pytest.mark.asyncio
async def test_shell_allows_workspace_relative_paths_to_reach_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: str,
) -> None:
    broker, store, task_id, _, executor = await _broker(
        tmp_path, monkeypatch, profile=PolicyProfile.DEVELOP
    )
    arguments = {
        "script": script,
        "cwd": ".",
        "mode": "inspect",
        "timeout_seconds": 120,
    }

    with pytest.raises(ToolBrokerError) as required:
        await broker.execute(
            task_id=task_id,
            operation_id="relative_path",
            tool_name="run_shell",
            arguments=arguments,
        )

    assert required.value.code == "approval_required"
    approval = store.list_approvals(task_id)[-1]
    assert approval.operation_id == "relative_path"
    executor.run_shell.assert_not_awaited()


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
@pytest.mark.parametrize("receipt_available", (True, False))
async def test_shell_applied_unknown_reconciles_without_rerunning_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_available: bool,
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

    if not receipt_available:
        visible_artifacts = [
            item
            for item in store.list_artifacts(task_id)
            if item.metadata.get("kind") != "shell_result"
        ]
        monkeypatch.setattr(
            store, "list_artifacts", lambda _task_id: visible_artifacts
        )
        with pytest.raises(ToolBrokerError) as still_unknown:
            broker.reconcile("shell_unknown")
        assert still_unknown.value.code == "operation_result_unknown"
        assert store.get_operation("shell_unknown").state is OperationState.UNKNOWN
        assert broker.changesets.has_transaction(
            workspace_id=str(store.get_task(task_id).workspace_id),
            operation_id="shell_unknown",
        )
        assert executor.run_shell.await_count == 1
        return

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


@pytest.mark.parametrize(
    "failure",
    (
        ExecutorRPCError("EOF", code="executor_invalid_response"),
        ConnectionResetError("reset"),
        asyncio.TimeoutError("timeout"),
        json.JSONDecodeError("invalid frame", "{", 1),
    ),
)
def test_v20_shell_transport_failures_are_uncertain(failure: Exception) -> None:
    assert ToolBroker._shell_executor_result_uncertain(failure) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    (
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid frame"),
        ValueError("Separator is not found, and chunk exceed the limit"),
    ),
)
async def test_v20_mutate_unclassified_post_dispatch_failure_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    broker, store, task_id, _, executor = await _broker(
        tmp_path,
        monkeypatch,
        profile=PolicyProfile.DEVELOP,
        runtime_protocol=RuntimeProtocol.V17,
        harness_v20=True,
    )
    monkeypatch.setattr(broker, "_authorize", lambda *_args, **_kwargs: None)
    executor.run_shell.side_effect = failure

    with pytest.raises(ToolBrokerError) as unknown:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_unclassified_transport",
            tool_name="run_shell",
            arguments={
                "script": "python fix.py",
                "cwd": ".",
                "mode": "mutate",
                "timeout_seconds": 60,
            },
        )

    assert unknown.value.code == "operation_result_unknown"
    assert (
        store.get_operation("shell_unclassified_transport").state
        is OperationState.UNKNOWN
    )
    turn = store.current_turn_transaction(task_id)
    assert turn is not None
    assert (turn.state, turn.barrier) == (
        TurnTransactionState.PARKING,
        TurnBarrier.OPERATION_UNKNOWN,
    )
    assert executor.run_shell.await_count == 1


@pytest.mark.asyncio
async def test_v20_mutate_structured_pre_dispatch_rejection_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, _, executor = await _broker(
        tmp_path,
        monkeypatch,
        profile=PolicyProfile.DEVELOP,
        runtime_protocol=RuntimeProtocol.V17,
        harness_v20=True,
    )
    monkeypatch.setattr(broker, "_authorize", lambda *_args, **_kwargs: None)
    executor.run_shell.side_effect = ExecutorRPCError(
        "Executor rejected the request before dispatch.",
        code="executor_request_invalid",
    )

    with pytest.raises(ToolBrokerError) as rejected:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_structured_rejection",
            tool_name="run_shell",
            arguments={
                "script": "python fix.py",
                "cwd": ".",
                "mode": "mutate",
                "timeout_seconds": 60,
            },
        )

    assert rejected.value.code == "executor_request_invalid"
    assert (
        store.get_operation("shell_structured_rejection").state
        is OperationState.FAILED
    )
    turn = store.current_turn_transaction(task_id)
    assert turn is not None and turn.state is TurnTransactionState.OPEN


@pytest.mark.asyncio
async def test_v20_shell_changeset_rollback_failure_stays_unknown_until_reconciled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, repository, executor = await _broker(
        tmp_path,
        monkeypatch,
        profile=PolicyProfile.DEVELOP,
        runtime_protocol=RuntimeProtocol.V17,
        harness_v20=True,
    )
    monkeypatch.setattr(broker, "_authorize", lambda *_args, **_kwargs: None)
    old = repository.joinpath("app.py").read_bytes()
    changed = b"print('partially-published')\n"
    executor.run_shell.return_value = _shell_result(
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

    def interrupt_publication(index: int) -> None:
        if index == 0:
            raise OSError("simulated interruption after first install")

    original_rollback = broker.changesets._rollback

    def fail_rollback(*_args: object) -> None:
        raise ChangesetError(
            "Rollback result is unknown.", code="changeset_rollback_failed"
        )

    broker.changesets.fault_hook = interrupt_publication
    monkeypatch.setattr(broker.changesets, "_rollback", fail_rollback)

    with pytest.raises(ToolBrokerError) as unknown:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_rollback_unknown",
            tool_name="run_shell",
            arguments={
                "script": "python fix.py",
                "cwd": ".",
                "mode": "mutate",
                "timeout_seconds": 60,
            },
        )

    assert unknown.value.code == "operation_result_unknown"
    assert store.get_operation("shell_rollback_unknown").state is OperationState.UNKNOWN
    assert repository.joinpath("app.py").read_bytes() == changed
    workspace_id = str(store.get_task(task_id).workspace_id)
    assert broker.changesets.has_transaction(
        workspace_id=workspace_id, operation_id="shell_rollback_unknown"
    )
    turn = store.current_turn_transaction(task_id)
    assert turn is not None
    assert (turn.state, turn.barrier) == (
        TurnTransactionState.PARKING,
        TurnBarrier.OPERATION_UNKNOWN,
    )

    with pytest.raises(ToolBrokerError) as still_unknown:
        broker.reconcile("shell_rollback_unknown")
    assert still_unknown.value.code == "changeset_rollback_failed"
    assert store.get_operation("shell_rollback_unknown").state is OperationState.UNKNOWN

    monkeypatch.setattr(broker.changesets, "_rollback", original_rollback)
    broker.changesets.fault_hook = None
    with pytest.raises(ToolBrokerError) as rolled_back:
        broker.reconcile("shell_rollback_unknown")
    assert rolled_back.value.code == "changeset_rolled_back"
    assert store.get_operation("shell_rollback_unknown").state is OperationState.FAILED
    assert repository.joinpath("app.py").read_bytes() == old


@pytest.mark.asyncio
async def test_v20_shell_unprovable_changeset_journal_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, repository, executor = await _broker(
        tmp_path,
        monkeypatch,
        profile=PolicyProfile.DEVELOP,
        runtime_protocol=RuntimeProtocol.V17,
        harness_v20=True,
    )
    monkeypatch.setattr(broker, "_authorize", lambda *_args, **_kwargs: None)
    old = repository.joinpath("app.py").read_bytes()
    changed = b"print('must-not-be-published')\n"
    executor.run_shell.return_value = _shell_result(
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
    repository.parent.joinpath("changesets").symlink_to(
        repository, target_is_directory=True
    )

    with pytest.raises(ToolBrokerError) as unknown:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_unprovable_journal",
            tool_name="run_shell",
            arguments={
                "script": "python fix.py",
                "cwd": ".",
                "mode": "mutate",
                "timeout_seconds": 60,
            },
        )

    assert unknown.value.code == "operation_result_unknown"
    assert (
        store.get_operation("shell_unprovable_journal").state
        is OperationState.UNKNOWN
    )
    assert repository.joinpath("app.py").read_bytes() == old
    turn = store.current_turn_transaction(task_id)
    assert turn is not None
    assert (turn.state, turn.barrier) == (
        TurnTransactionState.PARKING,
        TurnBarrier.OPERATION_UNKNOWN,
    )
    assert executor.run_shell.await_count == 1


@pytest.mark.asyncio
async def test_v20_mutate_transport_loss_parks_reconciles_and_retries_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, _, executor = await _broker(
        tmp_path,
        monkeypatch,
        profile=PolicyProfile.DEVELOP,
        runtime_protocol=RuntimeProtocol.V17,
        harness_v20=True,
    )
    monkeypatch.setattr(broker, "_authorize", lambda *_args, **_kwargs: None)
    arguments = {
        "script": "node fix.mjs",
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 60,
    }
    operation_id = "shell_transport_unknown"
    executor.run_shell.side_effect = ExecutorRPCError(
        "Executor response ended before a receipt.",
        code="executor_invalid_response",
    )

    with pytest.raises(ToolBrokerError) as unknown:
        await broker.execute(
            task_id=task_id,
            operation_id=operation_id,
            tool_name="run_shell",
            arguments=arguments,
        )
    assert unknown.value.code == "operation_result_unknown"
    assert store.get_operation(operation_id).state is OperationState.UNKNOWN
    turn = store.current_turn_transaction(task_id)
    task = store.get_task(task_id)
    assert turn is not None and task.workspace_id is not None
    assert (turn.state, turn.barrier) == (
        TurnTransactionState.PARKING,
        TurnBarrier.OPERATION_UNKNOWN,
    )

    checkpoint = store.create_checkpoint(
        task_id=task_id,
        workspace_tree_hash=broker.workspace_broker.current_tree_hash(
            task.workspace_id
        ),
        payload={"phase": "operation_unknown"},
    )
    store.park_turn_transaction(
        task_id=task_id,
        turn_id=turn.turn_id,
        checkpoint_id=checkpoint.checkpoint_id,
    )
    store.transition(
        task_id,
        TaskState.INTERRUPTED,
        reason="operation_result_unknown",
        expected_state=TaskState.RUNNING,
    )
    store.settle_parked_turn(
        task_id=task_id,
        barrier=TurnBarrier.OPERATION_UNKNOWN,
        expected_state=TaskState.INTERRUPTED,
    )
    store.transition(task_id, TaskState.PREPARING)
    store.transition(task_id, TaskState.RUNNING)

    reconciled = await broker.execute(
        task_id=task_id,
        operation_id=operation_id,
        tool_name="run_shell",
        arguments=arguments,
    )
    assert reconciled.state is OperationState.FAILED
    assert reconciled.data == {"code": "shell_result_unavailable"}
    assert executor.run_shell.await_count == 1

    executor.run_shell.side_effect = None
    executor.run_shell.return_value = _shell_result(
        broker, store, task_id, mode="mutate", output=""
    )
    retried = await broker.execute(
        task_id=task_id,
        operation_id="shell_transport_retry",
        tool_name="run_shell",
        arguments=arguments,
    )
    assert retried.state is OperationState.COMPLETED
    assert executor.run_shell.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_protocol", "harness_v20", "mode", "failure", "expected_code"),
    (
        (
            RuntimeProtocol.V17,
            True,
            "inspect",
            ConnectionResetError("reset"),
            "executor_transport_failed",
        ),
        (
            RuntimeProtocol.V16,
            False,
            "mutate",
            ExecutorRPCError("EOF", code="executor_invalid_response"),
            "executor_invalid_response",
        ),
        (
            RuntimeProtocol.V17,
            True,
            "inspect",
            ExecutorRPCError("failed", code="executor_failed"),
            "executor_runtime_failed",
        ),
    ),
)
async def test_v20_inspect_and_legacy_mutate_transport_fail_explicitly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_protocol: RuntimeProtocol,
    harness_v20: bool,
    mode: str,
    failure: Exception,
    expected_code: str,
) -> None:
    broker, store, task_id, _, executor = await _broker(
        tmp_path,
        monkeypatch,
        profile=PolicyProfile.DEVELOP,
        runtime_protocol=runtime_protocol,
        harness_v20=harness_v20,
    )
    monkeypatch.setattr(broker, "_authorize", lambda *_args, **_kwargs: None)
    executor.run_shell.side_effect = failure
    with pytest.raises(ToolBrokerError) as failed:
        await broker.execute(
            task_id=task_id,
            operation_id="shell_explicit_failure",
            tool_name="run_shell",
            arguments={
                "script": "pytest -q",
                "cwd": ".",
                "mode": mode,
                "timeout_seconds": 60,
            },
        )
    assert failed.value.code == expected_code
    assert store.get_operation("shell_explicit_failure").state is OperationState.FAILED
