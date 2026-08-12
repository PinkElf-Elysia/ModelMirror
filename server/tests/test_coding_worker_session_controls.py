from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    TaskSpec,
    WorkspaceSource,
)
from server.coding_worker.changeset import ChangesetEngine
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


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
