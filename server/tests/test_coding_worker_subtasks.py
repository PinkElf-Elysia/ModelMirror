from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    SubtaskKind,
    SubtaskMergeState,
    SubtaskRequest,
    TaskState,
    TaskSpec,
    WorkspaceSource,
)
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError
from server.coding_worker.provider import FakeCodingAgentProvider
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.workspace import (
    InMemoryWorkspaceSourceAdapter,
    WorkspaceBroker,
)


def _spec(client_task_id: str = "parent") -> TaskSpec:
    return TaskSpec(
        client_task_id=client_task_id,
        origin=Origin(module="test", object_id="subtasks"),
        objective="Parent objective",
        workspace_source=WorkspaceSource(
            kind="manifest", source_id="source", revision="revision"
        ),
        acceptance=AcceptanceContract(
            contract_id="contract",
            required_checks=(
                AcceptanceCheck(check_id="pytest", label="pytest", kind="command"),
            ),
        ),
        policy_profile=PolicyProfile.DEVELOP,
        model_route="coding/default",
    )


def _create(
    store: CodingWorkerStore,
    parent_task_id: str,
    *,
    client_subtask_id: str,
    kind: SubtaskKind = SubtaskKind.IMPLEMENT,
):
    child_spec = _spec(f"child-{client_subtask_id}").model_copy(
        update={
            "origin": Origin(
                module="coding-worker-subtask", object_id=parent_task_id
            )
        }
    )
    return store.create_subtask_task(
        parent_task_id=parent_task_id,
        client_subtask_id=client_subtask_id,
        kind=kind,
        objective=f"Do {client_subtask_id}",
        spec=child_spec,
        workspace_id=f"workspace-{client_subtask_id}",
        base_tree_hash="1" * 64,
    )


def test_subtask_relation_is_encrypted_idempotent_and_restart_safe(
    tmp_path: Path,
) -> None:
    root = tmp_path / "worker"
    key = Fernet.generate_key()
    store = CodingWorkerStore(root, master_key=key)
    parent = store.create_task(_spec())

    created = _create(
        store, parent.task_id, client_subtask_id="implementation"
    )
    assert created.kind is SubtaskKind.IMPLEMENT
    assert created.merge_state is SubtaskMergeState.PENDING
    assert _create(
        store, parent.task_id, client_subtask_id="implementation"
    ) == created

    finished = store.finish_subtask(
        created.child_task_id,
        result_tree_hash="2" * 64,
        changed_paths=("src/main.py",),
        summary="Implemented the delegated change.",
    )
    assert finished.merge_state is SubtaskMergeState.READY
    assert finished.changed_paths == ("src/main.py",)

    restarted = CodingWorkerStore(root, master_key=key)
    assert restarted.list_subtasks(parent.task_id) == [finished]
    raw = restarted.database_path.read_bytes()
    assert b"Implemented the delegated change" not in raw
    assert b"Do implementation" not in raw


def test_subtasks_are_depth_one_and_limited_to_four(tmp_path: Path) -> None:
    store = CodingWorkerStore(
        tmp_path / "worker", master_key=Fernet.generate_key()
    )
    parent = store.create_task(_spec())
    first = _create(store, parent.task_id, client_subtask_id="one")
    for index in range(2, 5):
        _create(store, parent.task_id, client_subtask_id=f"child-{index}")

    with pytest.raises(WorkerConflictError) as limit:
        _create(store, parent.task_id, client_subtask_id="child-5")
    assert limit.value.code == "subtask_limit_exceeded"

    with pytest.raises(WorkerConflictError) as depth:
        _create(store, first.child_task_id, client_subtask_id="nested")
    assert depth.value.code == "subtask_depth_exceeded"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (SubtaskKind.EXPLORE, SubtaskMergeState.NOT_APPLICABLE),
        (SubtaskKind.REVIEW, SubtaskMergeState.NOT_APPLICABLE),
    ],
)
def test_read_only_subtasks_never_enter_merge_queue(
    tmp_path: Path, kind: SubtaskKind, expected: SubtaskMergeState
) -> None:
    store = CodingWorkerStore(
        tmp_path / kind.value, master_key=Fernet.generate_key()
    )
    parent = store.create_task(_spec())
    child = _create(
        store, parent.task_id, client_subtask_id=kind.value, kind=kind
    )
    assert child.merge_state is expected


def test_service_parks_parent_spreads_fork_and_resumes_with_public_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
        store = CodingWorkerStore(
            tmp_path / "worker", master_key=Fernet.generate_key()
        )
        adapter = InMemoryWorkspaceSourceAdapter(
            {("source", "revision"): {"src/main.py": b"print('ok')\n"}}
        )
        broker = WorkspaceBroker(
            tmp_path / "worker",
            {"manifest": adapter},
            id_key=b"s" * 32,
            slot_roots={
                "slot-a": tmp_path / "slot-a",
                "slot-b": tmp_path / "slot-b",
            },
        )
        service = CodingWorkerService(
            store=store,
            workspace_broker=broker,
            provider=FakeCodingAgentProvider(),
        )
        parent = store.create_task(_spec())
        parent_workspace = await broker.prepare(
            parent.spec.workspace_source, slot_id="slot-a"
        )
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(
            parent.task_id,
            TaskState.RUNNING,
            workspace_id=parent_workspace.workspace_id,
        )

        relation = await service.create_subtask(
            parent.task_id,
            SubtaskRequest(
                client_subtask_id="implementation",
                kind=SubtaskKind.IMPLEMENT,
                objective="Inspect and update src/main.py",
            ),
        )
        child = store.get_task(relation.child_task_id)
        assert store.get_task(parent.task_id).state is TaskState.WAITING_SUBTASKS
        assert child.spec.policy_profile is PolicyProfile.DEVELOP
        assert child.spec.context_refs == ()
        assert broker.workspace_slot(child.workspace_id or "") == "slot-b"

        store.transition(child.task_id, TaskState.PREPARING)
        await service._run_task(child.task_id, slot_id="slot-b")

        completed = store.get_task(child.task_id)
        settled = store.subtask_for_child(child.task_id)
        assert completed.state is TaskState.COMPLETED
        assert settled is not None
        assert settled.merge_state is SubtaskMergeState.READY
        assert settled.changed_paths == ()
        assert store.get_task(parent.task_id).state is TaskState.QUEUED
        parent_messages = store.list_messages(parent.task_id)
        assert "child Evidence does not satisfy parent acceptance" in parent_messages[-1].content

    asyncio.run(scenario())
