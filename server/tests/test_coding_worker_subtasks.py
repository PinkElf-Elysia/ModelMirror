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
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    INSPECT_PROVIDER_TOOLS,
    PROVIDER_TOOL_NAMES,
)
from server.coding_worker.api import (
    coding_worker_capabilities,
    configure_coding_worker_for_tests,
    router,
)
from server.coding_worker.runtime import CodingWorkerRuntime
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.tool_broker import ToolBroker, ToolBrokerError
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


def test_provider_tool_delegates_exact_idempotent_subtask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
        store = CodingWorkerStore(
            tmp_path / "worker", master_key=Fernet.generate_key()
        )
        adapter = InMemoryWorkspaceSourceAdapter(
            {("source", "revision"): {"main.py": b"print('ok')\n"}}
        )
        workspace_broker = WorkspaceBroker(
            tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
        )
        service = CodingWorkerService(
            store=store,
            workspace_broker=workspace_broker,
            provider=FakeCodingAgentProvider(),
        )
        broker = ToolBroker(
            store=store,
            workspace_broker=workspace_broker,
            subtask_handler=service.create_subtask,
        )
        parent = store.create_task(_spec())
        workspace = await workspace_broker.prepare(parent.spec.workspace_source)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(
            parent.task_id,
            TaskState.RUNNING,
            workspace_id=workspace.workspace_id,
        )
        arguments = {
            "client_subtask_id": "explore-api",
            "kind": "explore",
            "objective": "Locate the relevant module.",
        }
        first = await broker.execute(
            task_id=parent.task_id,
            operation_id="subtask-operation",
            tool_name="create_subtask",
            arguments=arguments,
        )
        replay = await broker.execute(
            task_id=parent.task_id,
            operation_id="subtask-operation",
            tool_name="create_subtask",
            arguments=arguments,
        )
        assert replay == first
        assert first.data["subtask"]["kind"] == "explore"
        assert len(store.list_subtasks(parent.task_id)) == 1
        assert "create_subtask" in PROVIDER_TOOL_NAMES
        assert "create_subtask" in INSPECT_PROVIDER_TOOLS

    asyncio.run(scenario())


def test_inspect_parent_cannot_delegate_implementation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
        store = CodingWorkerStore(
            tmp_path / "worker", master_key=Fernet.generate_key()
        )
        adapter = InMemoryWorkspaceSourceAdapter(
            {("source", "revision"): {"main.py": b"print('ok')\n"}}
        )
        workspace_broker = WorkspaceBroker(
            tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
        )
        service = CodingWorkerService(
            store=store,
            workspace_broker=workspace_broker,
            provider=FakeCodingAgentProvider(),
        )
        broker = ToolBroker(
            store=store,
            workspace_broker=workspace_broker,
            subtask_handler=service.create_subtask,
        )
        parent = store.create_task(
            _spec().model_copy(update={"policy_profile": PolicyProfile.INSPECT})
        )
        workspace = await workspace_broker.prepare(parent.spec.workspace_source)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(
            parent.task_id,
            TaskState.RUNNING,
            workspace_id=workspace.workspace_id,
        )
        with pytest.raises(ToolBrokerError) as raised:
            await broker.execute(
                task_id=parent.task_id,
                operation_id="subtask-implement-denied",
                tool_name="create_subtask",
                arguments={
                    "client_subtask_id": "implement-denied",
                    "kind": "implement",
                    "objective": "Modify main.py",
                },
            )
        assert raised.value.code == "task_policy_readonly"
        assert store.list_subtasks(parent.task_id) == []

    asyncio.run(scenario())


def test_subtask_capability_routes_and_runtime_wiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
    adapter = InMemoryWorkspaceSourceAdapter(
        {("source", "revision"): {"main.py": b"print('ok')\n"}}
    )
    runtime = CodingWorkerRuntime(
        storage_root=tmp_path / "runtime",
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
        source_adapters={"manifest": adapter},
        frozen_checks={},
        provider_endpoints={"slot-a": "tcp:127.0.0.1:1", "slot-b": "tcp:127.0.0.1:2"},
        provider_tokens={"slot-a": "a" * 32, "slot-b": "b" * 32},
        broker_socket_path=None,
    )
    configure_coding_worker_for_tests(runtime.service, enabled=True)
    try:
        assert coding_worker_capabilities().subtasks is True
        paths = {route.path for route in router.routes}
        assert "/api/coding-worker/v1/tasks/{task_id}/subtasks" in paths
        assert "/api/coding-worker/v1/tasks/{task_id}/children" in paths
        assert runtime.tool_broker.subtask_handler is not None
        assert runtime.tool_broker.subtask_handler.__self__ is runtime.service
    finally:
        configure_coding_worker_for_tests(None, enabled=None)


def test_implement_subtasks_merge_non_overlapping_changes_and_conflict_on_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
        store = CodingWorkerStore(
            tmp_path / "worker", master_key=Fernet.generate_key()
        )
        adapter = InMemoryWorkspaceSourceAdapter(
            {
                ("source", "revision"): {
                    "a.py": b"A = 1\n",
                    "b.py": b"B = 1\n",
                }
            }
        )
        workspace_broker = WorkspaceBroker(
            tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
        )
        tool_broker = ToolBroker(store=store, workspace_broker=workspace_broker)
        service = CodingWorkerService(
            store=store,
            workspace_broker=workspace_broker,
            provider=FakeCodingAgentProvider(),
            tool_broker=tool_broker,
        )
        parent = store.create_task(_spec())
        workspace = await workspace_broker.prepare(parent.spec.workspace_source)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(
            parent.task_id,
            TaskState.RUNNING,
            workspace_id=workspace.workspace_id,
        )
        first = await service.create_subtask(
            parent.task_id,
            SubtaskRequest(
                client_subtask_id="first",
                kind=SubtaskKind.IMPLEMENT,
                objective="Change a.py",
            ),
        )
        store.transition(parent.task_id, TaskState.QUEUED)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(parent.task_id, TaskState.RUNNING)
        second = await service.create_subtask(
            parent.task_id,
            SubtaskRequest(
                client_subtask_id="second",
                kind=SubtaskKind.IMPLEMENT,
                objective="Change b.py",
            ),
        )
        for relation, path, content in (
            (first, "a.py", "A = 2\n"),
            (second, "b.py", "B = 2\n"),
        ):
            child = store.get_task(relation.child_task_id)
            child_path = workspace_broker.repository_path(child.workspace_id or "") / path
            child_path.write_text(content, encoding="utf-8")
            store.finish_subtask(
                child.task_id,
                result_tree_hash=workspace_broker.current_tree_hash(
                    child.workspace_id or ""
                ),
                changed_paths=(path,),
                summary=f"Changed {path}",
            )
        store.transition(parent.task_id, TaskState.QUEUED)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(parent.task_id, TaskState.RUNNING)

        first_result = await service.merge_subtask(
            parent.task_id, first.child_task_id, "merge-first"
        )
        second_result = await service.merge_subtask(
            parent.task_id, second.child_task_id, "merge-second"
        )
        assert first_result.merge_state is SubtaskMergeState.MERGED
        assert second_result.merge_state is SubtaskMergeState.MERGED
        parent_repo = workspace_broker.repository_path(workspace.workspace_id)
        assert (parent_repo / "a.py").read_text(encoding="utf-8") == "A = 2\n"
        assert (parent_repo / "b.py").read_text(encoding="utf-8") == "B = 2\n"

        third = await service.create_subtask(
            parent.task_id,
            SubtaskRequest(
                client_subtask_id="third",
                kind=SubtaskKind.IMPLEMENT,
                objective="Change a.py again",
            ),
        )
        third_task = store.get_task(third.child_task_id)
        third_repo = workspace_broker.repository_path(third_task.workspace_id or "")
        (third_repo / "a.py").write_text("A = 3\n", encoding="utf-8")
        store.finish_subtask(
            third.child_task_id,
            result_tree_hash=workspace_broker.current_tree_hash(
                third_task.workspace_id or ""
            ),
            changed_paths=("a.py",),
            summary="Changed a.py again",
        )
        (parent_repo / "a.py").write_text("A = 4\n", encoding="utf-8")
        store.transition(parent.task_id, TaskState.QUEUED)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(parent.task_id, TaskState.RUNNING)
        conflict = await service.merge_subtask(
            parent.task_id, third.child_task_id, "merge-third"
        )
        assert conflict.merge_state is SubtaskMergeState.CONFLICTED
        assert (parent_repo / "a.py").read_text(encoding="utf-8") == "A = 4\n"
        events = store.list_events(parent.task_id)
        assert [event.type for event in events].count("changeset_merged") == 2
        assert events[-1].type == "changeset_conflicted"

    asyncio.run(scenario())
