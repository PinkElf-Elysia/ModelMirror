from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    SessionLedgerKind,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.changeset import ChangesetEngine
from server.coding_worker.api import (
    coding_worker_capabilities,
    configure_coding_worker_for_tests,
)
from server.coding_worker.provider import FakeCodingAgentProvider, ProviderEvent, ProviderEventKind
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.tool_broker import ToolBroker
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.workspace import (
    InMemoryWorkspaceSourceAdapter,
    WorkspaceBroker,
    WorkspaceSnapshot,
)


def _spec() -> TaskSpec:
    return TaskSpec(
        client_task_id="session-controls",
        origin=Origin(module="tests", object_id="session-controls"),
        objective="Exercise turn controls.",
        workspace_source=WorkspaceSource(
            kind="builtin", source_id="source_session", revision="r1"
        ),
        acceptance=AcceptanceContract(
            contract_id="contract_session",
            required_checks=(
                AcceptanceCheck(check_id="compile", label="compile", kind="command"),
            ),
        ),
        policy_profile="develop",
        model_route="coding/default",
        budget={"max_seconds": 60, "max_turns": 4, "max_tool_calls": 8},
    )


def test_workspace_snapshot_preserves_binary_content(tmp_path: Path) -> None:
    broker = WorkspaceBroker(
        tmp_path / "workspaces",
        {
            "builtin": InMemoryWorkspaceSourceAdapter(
                {("source_session", "r1"): {"data.bin": b"\x00one"}}
            )
        },
        id_key=b"w" * 32,
    )
    import asyncio

    workspace = asyncio.run(broker.prepare(_spec().workspace_source))
    snapshot = broker.capture_snapshot(workspace.workspace_id)
    (broker.repository_path(workspace.workspace_id) / "data.bin").write_bytes(b"\x00two")
    files = broker.snapshot_files(workspace.workspace_id, snapshot)
    assert [(item.path, item.content) for item in files] == [("data.bin", b"\x00one")]
    restored = ChangesetEngine(broker).restore_snapshot(
        task_id="task_" + "a" * 32,
        workspace_id=workspace.workspace_id,
        operation_id="operation_" + "b" * 32,
        expected_tree_hash=broker.current_tree_hash(workspace.workspace_id),
        snapshot=snapshot,
    )
    assert restored.result_tree_hash == snapshot.tree_hash
    assert (broker.repository_path(workspace.workspace_id) / "data.bin").read_bytes() == b"\x00one"


def test_workspace_snapshot_does_not_apply_gitattributes_filters(tmp_path: Path) -> None:
    import asyncio

    broker = WorkspaceBroker(
        tmp_path / "attributes",
        {
            "builtin": InMemoryWorkspaceSourceAdapter(
                {
                    ("source_session", "r1"): {
                        ".gitattributes": b"*.txt text eol=lf\n",
                        "value.txt": b"one\r\ntwo\r\n",
                    }
                }
            )
        },
        id_key=b"a" * 32,
    )
    workspace = asyncio.run(broker.prepare(_spec().workspace_source))
    snapshot = broker.capture_snapshot(workspace.workspace_id)
    files = {item.path: item.content for item in broker.snapshot_files(workspace.workspace_id, snapshot)}
    assert files["value.txt"] == b"one\r\ntwo\r\n"


def test_turn_navigation_intent_is_durable_and_exact(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    store = CodingWorkerStore(tmp_path / "worker", master_key=key)
    task = store.create_task(_spec())
    checkpoint = store.create_turn_checkpoint(
        task_id=task.task_id,
        turn_id="turn_" + "a" * 32,
        before_tree_hash="1" * 64,
        before_tree_oid="2" * 40,
        after_tree_hash="3" * 64,
        after_tree_oid="4" * 40,
        ledger_sequence=1,
    )
    intent = store.begin_turn_navigation(task.task_id, "undo")
    assert intent[0] == checkpoint
    assert intent[1:] == (0, "3" * 64, "1" * 64, "2" * 40)

    reopened = CodingWorkerStore(tmp_path / "worker", master_key=key)
    assert reopened.begin_turn_navigation(task.task_id, "undo") == intent
    history = reopened.finish_turn_navigation(
        task.task_id,
        action="undo",
        checkpoint_id=checkpoint.checkpoint_id,
        target_cursor=0,
        workspace_tree_hash="1" * 64,
    )
    assert history.cursor == 0
    redo = reopened.begin_turn_navigation(task.task_id, "redo")
    assert redo[1:] == (1, "1" * 64, "3" * 64, "4" * 40)


def test_service_undo_redo_restores_exact_turn_tree(tmp_path: Path) -> None:
    import asyncio

    async def scenario() -> None:
        key = Fernet.generate_key()
        store = CodingWorkerStore(tmp_path / "store", master_key=key)
        broker = WorkspaceBroker(
            tmp_path / "workspaces",
            {
                "builtin": InMemoryWorkspaceSourceAdapter(
                    {("source_session", "r1"): {"value.txt": b"before"}}
                )
            },
            id_key=b"w" * 32,
        )
        provider = FakeCodingAgentProvider()
        tool_broker = ToolBroker(store=store, workspace_broker=broker)
        service = CodingWorkerService(
            store=store,
            workspace_broker=broker,
            provider=provider,
            tool_broker=tool_broker,
        )
        task = store.create_task(_spec())
        workspace = await broker.prepare(task.spec.workspace_source)
        store.transition(
            task.task_id, TaskState.PREPARING, expected_state=TaskState.QUEUED
        )
        store.transition(
            task.task_id,
            TaskState.RUNNING,
            workspace_id=workspace.workspace_id,
            expected_state=TaskState.PREPARING,
        )
        before = broker.capture_snapshot(workspace.workspace_id)
        target = broker.repository_path(workspace.workspace_id) / "value.txt"
        target.write_bytes(b"after")
        after = broker.capture_snapshot(workspace.workspace_id)
        turn_id = "turn_" + "c" * 32
        store.append_session_ledger(
            task.task_id,
            kind=SessionLedgerKind.TURN_STARTED,
            turn_id=turn_id,
            payload={},
        )
        store.finish_session_turn(
            task.task_id,
            turn_id=turn_id,
            result_state="completed",
            turn_checkpoint={
                "before_tree_hash": before.tree_hash,
                "before_tree_oid": before.tree_oid,
                "after_tree_hash": after.tree_hash,
                "after_tree_oid": after.tree_oid,
                "before_public_context": {
                    "messages": [{"role": "user", "content": "before"}],
                    "plan": None,
                },
                "after_public_context": {
                    "messages": [{"role": "assistant", "content": "after"}],
                    "plan": None,
                },
            },
        )
        store.transition(
            task.task_id, TaskState.TESTING, expected_state=TaskState.RUNNING
        )
        store.transition(
            task.task_id, TaskState.COMPLETED, expected_state=TaskState.TESTING
        )

        history = await service.navigate_turn(task.task_id, "undo")
        assert history.cursor == 0
        assert target.read_bytes() == b"before"
        assert store.get_task(task.task_id).state.value == "paused"
        history = await service.navigate_turn(task.task_id, "redo")
        assert history.cursor == 1
        assert target.read_bytes() == b"after"

        checkpoint, cursor, source_hash, target_hash, target_oid = (
            store.begin_turn_navigation(task.task_id, "undo")
        )
        operation_id = service._turn_navigation_operation_id(
            task.task_id, checkpoint.checkpoint_id, "undo"
        )
        changesets = tool_broker.changesets
        changesets.restore_snapshot(
            task_id=task.task_id,
            workspace_id=workspace.workspace_id,
            operation_id=operation_id,
            expected_tree_hash=source_hash,
            snapshot=WorkspaceSnapshot(tree_hash=target_hash, tree_oid=target_oid),
        )
        changesets.finalize(
            task_id=task.task_id,
            workspace_id=workspace.workspace_id,
            operation_id=operation_id,
        )
        assert cursor == 0 and target.read_bytes() == b"before"
        reconciled = await service.navigate_turn(task.task_id, "undo")
        assert reconciled.cursor == 0
        assert target.read_bytes() == b"before"

        child = await service.fork_task(task.task_id, "fork-one")
        assert child.state is TaskState.PAUSED
        assert child.workspace_id != task.workspace_id
        assert child.spec.acceptance == task.spec.acceptance
        assert child.provider_session_id is None
        assert '"content":"before"' in child.spec.objective
        assert '"content":"after"' not in child.spec.objective
        child_target = broker.repository_path(child.workspace_id or "") / "value.txt"
        assert child_target.read_bytes() == b"before"
        assert await service.fork_task(task.task_id, "fork-one") == child
        assert store.list_children(task.task_id) == [child]

    asyncio.run(scenario())


def test_session_control_capability_is_independently_gated(
    tmp_path: Path, monkeypatch
) -> None:
    store = CodingWorkerStore(tmp_path / "capability", master_key=Fernet.generate_key())
    broker = WorkspaceBroker(
        tmp_path / "capability-workspaces",
        {"builtin": InMemoryWorkspaceSourceAdapter({})},
        id_key=b"c" * 32,
    )
    service = CodingWorkerService(
        store=store,
        workspace_broker=broker,
        provider=FakeCodingAgentProvider(),
        tool_broker=ToolBroker(store=store, workspace_broker=broker),
    )
    configure_coding_worker_for_tests(service, enabled=True)
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SESSION_CONTROLS_ENABLED", "true")
    try:
        assert coding_worker_capabilities().turn_history is True
        monkeypatch.setenv("CODING_WORKER_SESSION_CONTROLS_ENABLED", "false")
        assert coding_worker_capabilities().turn_history is False
    finally:
        configure_coding_worker_for_tests(None, enabled=None)
