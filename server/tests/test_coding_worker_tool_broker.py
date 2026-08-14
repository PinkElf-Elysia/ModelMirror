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
    RuntimeProtocol,
    SessionLedgerKind,
    TaskSpec,
    TaskState,
    WorkspaceSource,
    TurnBarrier,
    TurnTransactionState,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.process_manager import BackgroundProcessManager
from server.coding_worker.network_policy import EgressPolicy
from server.coding_worker.executor import SidecarExecutor
from server.coding_worker.tool_broker import FrozenCheck, ToolBroker, ToolBrokerError
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


async def _broker(
    tmp_path: Path,
    *,
    profile: PolicyProfile = PolicyProfile.DEVELOP,
    runtime_protocol: RuntimeProtocol = RuntimeProtocol.V16,
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
        ),
        runtime_protocol=runtime_protocol,
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id)
    if runtime_protocol is RuntimeProtocol.V17:
        store.open_turn_transaction(
            task_id=task.task_id,
            turn_id="turn_broker_v17",
            workspace_tree_hash=workspace.current_tree_hash(prepared.workspace_id),
        )
        store.append_session_ledger(
            task.task_id,
            kind=SessionLedgerKind.TURN_STARTED,
            turn_id="turn_broker_v17",
            payload={},
        )
    broker = ToolBroker(
        store=store,
        workspace_broker=workspace,
        frozen_checks={
            "syntax": FrozenCheck(check_id="syntax", argv=(sys.executable, "-m", "py_compile", "app.py"))
        },
    )
    return broker, store, task.task_id, workspace.repository_path(prepared.workspace_id)


@pytest.mark.asyncio
async def test_v17_platform_plan_todo_and_question_are_turn_bound(tmp_path: Path) -> None:
    broker, store, task_id, _ = await _broker(
        tmp_path, runtime_protocol=RuntimeProtocol.V17
    )
    plan = await broker.execute(
        task_id=task_id,
        operation_id="platform-plan",
        tool_name="update_plan",
        arguments={
            "explanation": "Reproduce first.",
            "items": [{"step": "run tests", "status": "in_progress"}],
        },
    )
    assert plan.data["sequence"] >= 1
    assert store.latest_plan(task_id).items[0].step == "run tests"
    todo = await broker.execute(
        task_id=task_id,
        operation_id="platform-todo",
        tool_name="update_todo",
        arguments={
            "items": [
                {"todo_id": "todo_repro", "content": "reproduce", "status": "pending"}
            ]
        },
    )
    assert todo.data["todo"]["items"][0]["todo_id"] == "todo_repro"
    parked = await broker.execute(
        task_id=task_id,
        operation_id="platform-question",
        tool_name="request_user_input",
        arguments={
            "question_id": "question_scope",
            "prompt": "Which scope?",
            "options": [{"option_id": "scope_small", "label": "Small"}],
        },
    )
    assert parked.data == {
        "control": "turn_parking",
        "barrier": TurnBarrier.INPUT.value,
        "question_id": "question_scope",
    }
    turn = store.current_turn_transaction(task_id)
    assert turn is not None
    assert turn.state is TurnTransactionState.PARKING
    assert turn.barrier is TurnBarrier.INPUT
    assert store.list_questions(task_id)[0].question_id == "question_scope"
    with pytest.raises(ToolBrokerError) as late:
        await broker.execute(
            task_id=task_id,
            operation_id="platform-late-plan",
            tool_name="update_plan",
            arguments={"items": [{"step": "late", "status": "pending"}]},
        )
    assert late.value.code == "turn_parked"
    assert "platform-late-plan" not in {
        operation.operation_id for operation in store.list_operations(task_id)
    }


@pytest.mark.asyncio
async def test_v17_unresolved_unknown_operation_reparks_the_same_turn(
    tmp_path: Path,
) -> None:
    broker, store, task_id, _ = await _broker(
        tmp_path, runtime_protocol=RuntimeProtocol.V17
    )
    task = store.get_task(task_id)
    turn = store.current_turn_transaction(task_id)
    assert task.workspace_id is not None and turn is not None
    content = "print('unconfirmed')\n"
    arguments = {
        "path": "app.py",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
    }
    request = {"arguments": arguments, "workspace_id": task.workspace_id}
    operation = store.create_operation(
        task_id=task_id,
        operation_id="unknown-write-v17",
        tool_name="write_file",
        intent_sha256=broker._intent_sha256("write_file", request),
        request=request,
        turn_id=turn.turn_id,
    )
    store.transition_operation(
        operation.operation_id,
        OperationState.RUNNING,
        expected_state=OperationState.PREPARED,
    )
    store.transition_operation(
        operation.operation_id,
        OperationState.UNKNOWN,
        result={"code": "operation_result_unknown"},
        expected_state=OperationState.RUNNING,
    )

    with pytest.raises(ToolBrokerError) as unresolved:
        await broker.execute(
            task_id=task_id,
            operation_id=operation.operation_id,
            tool_name="write_file",
            arguments=arguments,
        )
    assert unresolved.value.code == "operation_result_unknown"
    parked = store.current_turn_transaction(task_id)
    assert parked is not None
    assert parked.turn_id == turn.turn_id
    assert parked.state is TurnTransactionState.PARKING
    assert parked.barrier is TurnBarrier.OPERATION_UNKNOWN

    with pytest.raises(ToolBrokerError) as late:
        await broker.execute(
            task_id=task_id,
            operation_id="late-after-unknown",
            tool_name="read_file",
            arguments={"path": "app.py"},
        )
    assert late.value.code == "turn_parked"
    assert "late-after-unknown" not in {
        item.operation_id for item in store.list_operations(task_id)
    }


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
    assert diff.data["tree_hash"] == broker.workspace_broker.current_tree_hash(
        broker.store.get_task(task_id).workspace_id or ""
    )
    check = await broker.execute(
        task_id=task_id,
        operation_id="check-01",
        tool_name="run_check",
        arguments={"check_id": "syntax"},
    )
    assert check.data["exit_code"] == 0
    assert repository.joinpath("app.py").read_text() == content


@pytest.mark.asyncio
async def test_read_tools_expose_exact_preimage_and_current_tree_hash(
    tmp_path: Path,
) -> None:
    broker, _, task_id, repository = await _broker(tmp_path)
    expected = repository.joinpath("app.py").read_bytes()
    tree_hash = broker.workspace_broker.current_tree_hash(
        broker.store.get_task(task_id).workspace_id or ""
    )

    listed = await broker.execute(
        task_id=task_id,
        operation_id="list-bindings",
        tool_name="list_files",
        arguments={},
    )
    read = await broker.execute(
        task_id=task_id,
        operation_id="read-bindings",
        tool_name="read_file",
        arguments={"path": "app.py"},
    )

    assert listed.data["tree_hash"] == tree_hash
    assert read.data["tree_hash"] == tree_hash
    assert read.data["sha256"] == hashlib.sha256(expected).hexdigest()


@pytest.mark.asyncio
async def test_reused_operation_id_explains_exact_reconciliation_rule(
    tmp_path: Path,
) -> None:
    broker, _, task_id, _ = await _broker(tmp_path)
    await broker.execute(
        task_id=task_id,
        operation_id="shared-intent",
        tool_name="list_files",
        arguments={},
    )

    with pytest.raises(ToolBrokerError) as conflict:
        await broker.execute(
            task_id=task_id,
            operation_id="shared-intent",
            tool_name="search_text",
            arguments={"query": "old"},
        )

    assert conflict.value.code == "operation_intent_conflict"
    assert "Use a new operation_id for the changed intent" in str(conflict.value)
    assert "exact original tool and arguments" in str(conflict.value)


@pytest.mark.asyncio
async def test_v15_range_glob_and_safe_regex_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    broker, _, task_id, repository = await _broker(tmp_path)
    repository.joinpath("notes.txt").write_bytes(b"alpha\nbeta boundary\ngamma\n")
    ranged = await broker.execute(
        task_id=task_id,
        operation_id="range-01",
        tool_name="read_file_range",
        arguments={"path": "notes.txt", "start_line": 2, "end_line": 3},
    )
    assert ranged.data["content"] == "beta boundary\ngamma\n"
    assert ranged.data["total_lines"] == 3
    globbed = await broker.execute(
        task_id=task_id,
        operation_id="glob-01",
        tool_name="glob_files",
        arguments={"pattern": "**/*.txt"},
    )
    assert [item["display_path"] for item in globbed.data["entries"]] == [
        "notes.txt"
    ]
    searched = await broker.execute(
        task_id=task_id,
        operation_id="regex-01",
        tool_name="search_regex",
        arguments={"pattern": r"b[a-z]+ boundary", "glob": "**/*.txt"},
    )
    assert searched.data["matches"][0]["line"] == 2
    with pytest.raises(ToolBrokerError) as unsafe:
        await broker.execute(
            task_id=task_id,
            operation_id="regex-unsafe",
            tool_name="search_regex",
            arguments={"pattern": r"(a+)+$"},
        )
    assert unsafe.value.code == "tool_input_invalid"


@pytest.mark.asyncio
async def test_v15_changeset_write_patch_move_and_evidence_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    broker, store, task_id, repository = await _broker(tmp_path)
    workspace_id = store.get_task(task_id).workspace_id
    assert workspace_id is not None
    base = broker.workspace_broker.current_tree_hash(workspace_id)
    artifact = store.create_artifact(
        task_id=task_id,
        media_type="text/plain",
        content=b"passed\n",
        metadata={"check_id": "syntax", "workspace_tree_hash": base},
    )
    store.record_evidence(
        task_id=task_id,
        check_id="syntax",
        operation_id="evidence-before-changeset",
        workspace_tree_hash=base,
        exit_code=0,
        artifact_id=artifact.artifact_id,
    )
    old = repository.joinpath("app.py").read_bytes()
    new = b"print('new')\n"
    readme = b"# Example\n"
    with pytest.raises(ToolBrokerError) as conflict:
        await broker.execute(
            task_id=task_id,
            operation_id="changeset-conflict",
            tool_name="apply_changeset",
            arguments={
                "base_tree_hash": base,
                "changes": [
                    {
                        "kind": "write",
                        "path": "app.py",
                        "expected_sha256": hashlib.sha256(old).hexdigest(),
                        "content": new.decode(),
                        "content_sha256": hashlib.sha256(new).hexdigest(),
                    },
                    {
                        "kind": "delete",
                        "path": "missing.txt",
                        "expected_sha256": "f" * 64,
                    },
                ],
            },
        )
    assert conflict.value.code == "preimage_changed"
    assert repository.joinpath("app.py").read_bytes() == old
    first = await broker.execute(
        task_id=task_id,
        operation_id="changeset-first",
        tool_name="apply_changeset",
        arguments={
            "base_tree_hash": base,
            "changes": [
                {
                    "kind": "write",
                    "path": "app.py",
                    "expected_sha256": hashlib.sha256(old).hexdigest(),
                    "content": new.decode(),
                    "content_sha256": hashlib.sha256(new).hexdigest(),
                },
                {
                    "kind": "write",
                    "path": "README.md",
                    "expected_absent": True,
                    "content": readme.decode(),
                    "content_sha256": hashlib.sha256(readme).hexdigest(),
                },
            ],
        },
    )
    assert first.data["changeset"]["state"] == "applied"
    assert first.data["changeset"]["task_id"] == task_id
    assert repository.joinpath("app.py").read_bytes() == new
    current = broker.workspace_broker.current_tree_hash(workspace_id)
    assert store.list_evidence(task_id, current_tree_hash=current)[0].status.value == "invalidated"

    patch = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-print('new')\n"
        "+print('patched')\n"
    )
    second = await broker.execute(
        task_id=task_id,
        operation_id="changeset-second",
        tool_name="apply_changeset",
        arguments={
            "base_tree_hash": current,
            "changes": [
                {
                    "kind": "patch",
                    "path": "app.py",
                    "expected_sha256": hashlib.sha256(new).hexdigest(),
                    "patch": patch,
                    "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
                },
                {
                    "kind": "move",
                    "path": "README.md",
                    "destination": "docs/README.md",
                    "expected_sha256": hashlib.sha256(readme).hexdigest(),
                    "destination_expected_absent": True,
                },
            ],
        },
    )
    assert second.data["changeset"]["entries"][1]["kind"] == "move"
    assert repository.joinpath("app.py").read_text() == "print('patched')\n"
    assert not repository.joinpath("README.md").exists()
    assert repository.joinpath("docs/README.md").read_bytes() == readme


@pytest.mark.asyncio
async def test_v15_changeset_rolls_back_fault_and_reconciles_applied_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    broker, store, task_id, repository = await _broker(tmp_path)
    workspace_id = store.get_task(task_id).workspace_id
    assert workspace_id is not None
    base = broker.workspace_broker.current_tree_hash(workspace_id)
    old = repository.joinpath("app.py").read_bytes()
    changed = b"print('changed')\n"
    extra = b"extra\n"

    def fail_after_first(index: int) -> None:
        if index == 0:
            raise OSError("simulated changeset interruption")

    broker.changesets.fault_hook = fail_after_first
    with pytest.raises(ToolBrokerError) as interrupted:
        await broker.execute(
            task_id=task_id,
            operation_id="changeset-fault",
            tool_name="apply_changeset",
            arguments={
                "base_tree_hash": base,
                "changes": [
                    {
                        "kind": "write",
                        "path": "app.py",
                        "expected_sha256": hashlib.sha256(old).hexdigest(),
                        "content": changed.decode(),
                        "content_sha256": hashlib.sha256(changed).hexdigest(),
                    },
                    {
                        "kind": "write",
                        "path": "extra.txt",
                        "expected_absent": True,
                        "content": extra.decode(),
                        "content_sha256": hashlib.sha256(extra).hexdigest(),
                    },
                ],
            },
        )
    assert interrupted.value.code == "tool_failed"
    broker.changesets.fault_hook = None
    assert repository.joinpath("app.py").read_bytes() == old
    assert not repository.joinpath("extra.txt").exists()
    assert broker.workspace_broker.current_tree_hash(workspace_id) == base

    arguments = {
        "base_tree_hash": base,
        "changes": [
            {
                "kind": "write",
                "path": "app.py",
                "expected_sha256": hashlib.sha256(old).hexdigest(),
                "content": changed.decode(),
                "content_sha256": hashlib.sha256(changed).hexdigest(),
            }
        ],
    }
    original_prepare = broker.changesets._prepare

    def hard_stop(**_kwargs: object) -> object:
        raise SystemExit("simulated hard stop during changeset preparation")

    monkeypatch.setattr(broker.changesets, "_prepare", hard_stop)
    with pytest.raises(SystemExit):
        await broker.execute(
            task_id=task_id,
            operation_id="changeset-preparing-stop",
            tool_name="apply_changeset",
            arguments=arguments,
        )
    monkeypatch.setattr(broker.changesets, "_prepare", original_prepare)
    store.mark_inflight_operations_unknown()
    with pytest.raises(ToolBrokerError) as rolled_back:
        broker.reconcile("changeset-preparing-stop")
    assert rolled_back.value.code == "changeset_rolled_back"
    assert store.get_operation("changeset-preparing-stop").state is OperationState.FAILED

    request = {"arguments": arguments, "workspace_id": workspace_id}
    operation = store.create_operation(
        task_id=task_id,
        operation_id="changeset-unknown",
        tool_name="apply_changeset",
        intent_sha256=broker._intent_sha256("apply_changeset", request),
        request=request,
    )
    store.transition_operation(operation.operation_id, OperationState.RUNNING)
    broker.changesets.apply(
        task_id=task_id,
        workspace_id=workspace_id,
        operation_id=operation.operation_id,
        arguments=arguments,
    )
    store.mark_inflight_operations_unknown()
    reconciled = broker.reconcile(operation.operation_id)
    assert reconciled.state is OperationState.COMPLETED
    assert repository.joinpath("app.py").read_bytes() == changed


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
    assert executor.run_process.await_args.kwargs["isolated"] is True
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
        task_id="task_one",
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
    with pytest.raises(ToolBrokerError) as parked:
        await broker.execute(
            task_id=task_id,
            operation_id="approval-command-duplicate",
            tool_name="run_command",
            arguments={"argv": ["python", "-c", "print('duplicate')"]},
        )
    assert parked.value.code == "task_state_conflict"
    assert len(store.list_approvals(task_id)) == 1
    assert [item.operation_id for item in store.list_operations(task_id)] == [
        "approval-command"
    ]
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
    repository = broker.workspace_broker.repository_path(
        store.get_task(task_id).workspace_id or ""
    )
    repository.joinpath("package.json").write_text("{}", encoding="utf-8")
    repository.joinpath("package-lock.json").write_text(
        '{"lockfileVersion":3}', encoding="utf-8"
    )
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
@pytest.mark.parametrize(
    ("arguments", "files", "expected_argv"),
    [
        (
            {"manager": "uv", "action": "sync"},
            {"pyproject.toml": b"[project]\nname='demo'\n", "uv.lock": b"version = 1\n"},
            ("uv", "sync", "--frozen"),
        ),
        (
            {"manager": "pip", "action": "install", "requirements": "requirements.txt"},
            {"requirements.txt": b"demo==1.0 --hash=sha256:" + b"a" * 64 + b"\n"},
            ("python", "-m", "pip", "install", "--require-hashes", "-r", "requirements.txt"),
        ),
    ],
)
async def test_python_dependency_plans_are_frozen(
    tmp_path: Path,
    arguments: dict[str, str],
    files: dict[str, bytes],
    expected_argv: tuple[str, ...],
) -> None:
    broker, store, task_id, repository = await _broker(
        tmp_path, profile=PolicyProfile.DEVELOP_NETWORKED
    )
    for path, content in files.items():
        repository.joinpath(path).write_bytes(content)
    broker.egress_policy = EgressPolicy(
        enabled=True,
        allowed_domains={"pypi.org", "files.pythonhosted.org"},
        grant_key=b"g" * 32,
    )
    broker.egress_proxy_url = "http://worker-egress:8080"
    broker._run_process = AsyncMock(return_value={"exit_code": 0, "output": "ok"})
    with pytest.raises(ToolBrokerError):
        await broker.execute(
            task_id=task_id,
            operation_id="python-dependencies",
            tool_name="install_dependencies",
            arguments=arguments,
        )
    approvals = store.list_approvals(task_id)
    leases = {
        item.capability: store.decide_approval(item.approval_id, approved=True).lease
        for item in approvals
    }
    store.transition(task_id, TaskState.RUNNING)
    await broker.execute(
        task_id=task_id,
        operation_id="python-dependencies",
        tool_name="install_dependencies",
        arguments=arguments,
        lease_id=leases["dependency_install"].lease_id,
        network_lease_id=leases["network"].lease_id,
    )
    assert broker._run_process.await_args.args[2] == expected_argv


@pytest.mark.asyncio
async def test_unhashed_python_requirements_fail_before_approval(tmp_path: Path) -> None:
    broker, store, task_id, repository = await _broker(
        tmp_path, profile=PolicyProfile.DEVELOP_NETWORKED
    )
    broker.egress_policy = EgressPolicy(
        enabled=True,
        allowed_domains={"pypi.org", "files.pythonhosted.org"},
        grant_key=b"g" * 32,
    )
    repository.joinpath("requirements.txt").write_text("demo==1.0\n", encoding="utf-8")
    with pytest.raises(ToolBrokerError) as rejected:
        await broker.execute(
            task_id=task_id,
            operation_id="unhashed-requirements",
            tool_name="install_dependencies",
            arguments={"manager": "pip", "action": "install", "requirements": "requirements.txt"},
        )
    assert rejected.value.code == "dependency_plan_invalid"
    assert store.list_approvals(task_id) == []


@pytest.mark.asyncio
async def test_dependency_lease_is_invalidated_when_lock_input_changes(
    tmp_path: Path,
) -> None:
    broker, store, task_id, repository = await _broker(
        tmp_path, profile=PolicyProfile.DEVELOP_NETWORKED
    )
    repository.joinpath("package.json").write_text("{}", encoding="utf-8")
    lockfile = repository.joinpath("package-lock.json")
    lockfile.write_text('{"lockfileVersion":3}', encoding="utf-8")
    broker.egress_policy = EgressPolicy(
        enabled=True,
        allowed_domains={"registry.npmjs.org"},
        grant_key=b"g" * 32,
    )
    broker.egress_proxy_url = "http://worker-egress:8080"
    arguments = {"manager": "npm", "action": "ci"}
    with pytest.raises(ToolBrokerError):
        await broker.execute(
            task_id=task_id,
            operation_id="changed-dependency-lock",
            tool_name="install_dependencies",
            arguments=arguments,
        )
    leases = {
        item.capability: store.decide_approval(item.approval_id, approved=True).lease
        for item in store.list_approvals(task_id)
    }
    store.transition(task_id, TaskState.RUNNING)
    lockfile.write_text('{"lockfileVersion":3,"changed":true}', encoding="utf-8")
    broker._run_process = AsyncMock()
    with pytest.raises(ToolBrokerError) as rejected:
        await broker.execute(
            task_id=task_id,
            operation_id="changed-dependency-lock",
            tool_name="install_dependencies",
            arguments=arguments,
            lease_id=leases["dependency_install"].lease_id,
            network_lease_id=leases["network"].lease_id,
        )
    assert rejected.value.code == "lease_scope_mismatch"
    broker._run_process.assert_not_awaited()


@pytest.mark.asyncio
async def test_documentation_query_uses_registered_resource_and_exact_leases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker, store, task_id, _ = await _broker(
        tmp_path, profile=PolicyProfile.DEVELOP_NETWORKED
    )
    broker.documentation_resources = {"python": "https://docs.python.org/3"}
    broker.egress_policy = EgressPolicy(
        enabled=True,
        allowed_domains={"docs.python.org"},
        grant_key=b"d" * 32,
    )
    broker.egress_proxy_url = "http://worker-egress:8080"
    broker._fetch_documentation = AsyncMock(return_value={"content": b"asyncio docs"})
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_DOCUMENTATION_EGRESS_ENABLED", "true")
    arguments = {"resource_id": "python", "document_path": "library/asyncio.html"}
    with pytest.raises(ToolBrokerError):
        await broker.execute(
            task_id=task_id,
            operation_id="documentation-query",
            tool_name="query_documentation",
            arguments=arguments,
        )
    approvals = store.list_approvals(task_id)
    assert {item.capability for item in approvals} == {"documentation_query", "network"}
    leases = {
        item.capability: store.decide_approval(item.approval_id, approved=True).lease
        for item in approvals
    }
    store.transition(task_id, TaskState.RUNNING)
    result = await broker.execute(
        task_id=task_id,
        operation_id="documentation-query",
        tool_name="query_documentation",
        arguments=arguments,
        lease_id=leases["documentation_query"].lease_id,
        network_lease_id=leases["network"].lease_id,
    )
    assert result.data["content"] == "asyncio docs"
    call = broker._fetch_documentation.await_args
    assert call.args[0] == "https://docs.python.org/3/library/asyncio.html"
    assert call.kwargs["proxy"].startswith("http://grant:")


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
