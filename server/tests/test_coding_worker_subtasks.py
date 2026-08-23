from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    RuntimeProtocol,
    SubtaskKind,
    SubtaskMergeState,
    SubtaskRequest,
    TaskState,
    TaskSpec,
    WorkspaceSource,
    TurnBarrier,
    TurnTransactionState,
)
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError
from server.coding_worker.provider import (
    FakeCodingAgentProvider,
    INSPECT_PROVIDER_TOOLS,
    PROVIDER_TOOL_NAMES,
    ProviderEvent,
    ProviderEventKind,
    ProviderSession,
)
from server.coding_worker.api import (
    coding_worker_capabilities,
    configure_coding_worker_for_tests,
    router,
)
from server.coding_worker.adapters import LegacyHarnessDriver, LegacyHarnessSupervisor
from server.coding_worker.runtime import CodingWorkerRuntime
from server.coding_worker.service import CodingWorkerService
from server.coding_worker.tool_broker import ToolBroker, ToolBrokerError
from server.coding_worker.workspace import (
    InMemoryWorkspaceSourceAdapter,
    WorkspaceBroker,
)


def _service_with_legacy_provider(
    *, provider: FakeCodingAgentProvider, **kwargs: object
) -> CodingWorkerService:
    return CodingWorkerService(
        provider=LegacyHarnessDriver(provider),
        harness_supervisor=LegacyHarnessSupervisor(provider),
        **kwargs,
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


class _DelegatingProvider(FakeCodingAgentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.delegate: Callable[[str, SubtaskRequest], Awaitable[object]] | None = None
        self.continued_after_delegation = False

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        assert self.delegate is not None
        await self.delegate(
            session.task_id,
            SubtaskRequest(
                client_subtask_id="delegated-exploration",
                kind=SubtaskKind.EXPLORE,
                objective="Inspect the relevant module.",
            ),
        )
        yield ProviderEvent(
            kind=ProviderEventKind.MESSAGE,
            data={"text": "Delegated the bounded exploration."},
        )
        self.continued_after_delegation = True
        yield ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)


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


def test_store_upgrades_pre_merge_subtask_schema(tmp_path: Path) -> None:
    root = tmp_path / "worker"
    key = Fernet.generate_key()
    initial = CodingWorkerStore(root, master_key=key)
    with sqlite3.connect(initial.database_path) as connection:
        connection.execute("DROP TABLE worker_subtasks")
        connection.execute(
            """
            CREATE TABLE worker_subtasks (
                parent_task_id TEXT NOT NULL,
                client_subtask_id TEXT NOT NULL,
                child_task_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                objective_ciphertext TEXT NOT NULL,
                base_tree_hash TEXT NOT NULL,
                merge_state TEXT NOT NULL,
                result_tree_hash TEXT,
                changed_paths_ciphertext TEXT,
                summary_ciphertext TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(parent_task_id, client_subtask_id),
                FOREIGN KEY(parent_task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY(child_task_id) REFERENCES worker_tasks(task_id) ON DELETE CASCADE
            )
            """
        )

    upgraded = CodingWorkerStore(root, master_key=key)
    with sqlite3.connect(upgraded.database_path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(worker_subtasks)"
            ).fetchall()
        }
    assert {"merge_operation_id", "merged_tree_hash"} <= columns

    parent = upgraded.create_task(_spec())
    created = _create(
        upgraded, parent.task_id, client_subtask_id="post-upgrade"
    )
    assert created.merge_operation_id is None
    assert created.merged_tree_hash is None


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


def test_v17_subtask_creation_and_parent_parking_are_atomic(tmp_path: Path) -> None:
    store = CodingWorkerStore(
        tmp_path / "worker", master_key=Fernet.generate_key()
    )
    parent = store.create_task(_spec(), runtime_protocol=RuntimeProtocol.V17)
    store.transition(parent.task_id, TaskState.PREPARING)
    store.transition(parent.task_id, TaskState.RUNNING)
    turn = store.open_turn_transaction(
        task_id=parent.task_id,
        turn_id="turn_v17_subtask_atomic",
        workspace_tree_hash="a" * 64,
    )
    child_spec = _spec("child-v17-atomic").model_copy(
        update={
            "origin": Origin(
                module="coding-worker-subtask", object_id=parent.task_id
            )
        }
    )

    relation = store.create_subtask_task(
        parent_task_id=parent.task_id,
        client_subtask_id="v17-atomic",
        kind=SubtaskKind.EXPLORE,
        objective="Inspect the dependency graph.",
        spec=child_spec,
        workspace_id="workspace-v17-atomic",
        base_tree_hash="a" * 64,
        parent_turn_id=turn.turn_id,
    )

    assert store.get_task(relation.child_task_id).state is TaskState.QUEUED
    parked = store.current_turn_transaction(parent.task_id)
    assert parked is not None
    assert parked.state is TurnTransactionState.PARKING
    assert parked.barrier is TurnBarrier.SUBTASKS
    events = store.list_events(parent.task_id)
    subtask_event = next(item for item in events if item.type == "subtask_created")
    parking_event = next(item for item in events if item.type == "turn_parking")
    assert subtask_event.sequence + 1 == parking_event.sequence


@pytest.mark.asyncio
async def test_ready_implementation_must_be_resolved_before_another_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    broker = WorkspaceBroker(
        tmp_path / "worker",
        {"manifest": InMemoryWorkspaceSourceAdapter(
            {("source", "revision"): {"src/main.py": b"print('ok')\n"}}
        )},
        id_key=b"s" * 32,
    )
    service = _service_with_legacy_provider(
        store=store,
        workspace_broker=broker,
        provider=FakeCodingAgentProvider(),
    )
    parent = store.create_task(_spec())
    workspace = await broker.prepare(parent.spec.workspace_source)
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
            objective="Fix the async boundary.",
        ),
    )
    store.finish_subtask(
        first.child_task_id,
        result_tree_hash=first.base_tree_hash,
        changed_paths=("src/main.py",),
        summary="Ready to merge.",
    )

    with pytest.raises(WorkerConflictError) as blocked:
        await service.create_subtask(
            parent.task_id,
            SubtaskRequest(
                client_subtask_id="second",
                kind=SubtaskKind.IMPLEMENT,
                objective="Rewrite the same request in different words.",
            ),
        )
    assert blocked.value.code == "subtask_merge_required"


@pytest.mark.asyncio
async def test_ready_implementation_blocks_parent_mutation_and_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
    store = CodingWorkerStore(tmp_path / "worker", master_key=Fernet.generate_key())
    workspace_broker = WorkspaceBroker(
        tmp_path / "worker",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("source", "revision"): {"main.py": b"VALUE = 1\n"}}
            )
        },
        id_key=b"s" * 32,
    )
    tool_broker = ToolBroker(store=store, workspace_broker=workspace_broker)
    service = _service_with_legacy_provider(
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
    relation = await service.create_subtask(
        parent.task_id,
        SubtaskRequest(
            client_subtask_id="implementation",
            kind=SubtaskKind.IMPLEMENT,
            objective="Change main.py",
        ),
    )
    child = store.get_task(relation.child_task_id)
    child_path = (
        workspace_broker.repository_path(child.workspace_id or "") / "main.py"
    )
    child_path.write_text("VALUE = 2\n", encoding="utf-8")
    store.finish_subtask(
        child.task_id,
        result_tree_hash=workspace_broker.current_tree_hash(child.workspace_id or ""),
        changed_paths=("main.py",),
        summary="Changed main.py",
    )
    store.transition(parent.task_id, TaskState.QUEUED)
    store.transition(parent.task_id, TaskState.PREPARING)
    store.transition(parent.task_id, TaskState.RUNNING)

    with pytest.raises(ToolBrokerError) as direct_write:
        await tool_broker.execute(
            task_id=parent.task_id,
            operation_id="direct-parent-write",
            tool_name="write_file",
            arguments={"path": "main.py", "content": "VALUE = 2\n"},
        )
    assert direct_write.value.code == "subtask_merge_required"
    current_parent = store.get_task(parent.task_id)
    parent_path = (
        workspace_broker.repository_path(current_parent.workspace_id or "") / "main.py"
    )
    assert parent_path.read_text(encoding="utf-8") == "VALUE = 1\n"

    feedback, _ = await service._evaluate_acceptance(
        store.get_task(parent.task_id), 1, message_cursor=0
    )
    assert feedback is not None
    assert "merge_subtask" in feedback
    assert store.get_task(parent.task_id).state is TaskState.RUNNING


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
        service = _service_with_legacy_provider(
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
        assert completed.state is TaskState.BLOCKED
        assert completed.reason == "subtask_no_changes"
        assert settled is not None
        assert settled.merge_state is SubtaskMergeState.FAILED
        assert settled.changed_paths == ()
        assert store.get_task(parent.task_id).state is TaskState.QUEUED
        parent_messages = store.list_messages(parent.task_id)
        assert "child Evidence does not satisfy parent acceptance" in parent_messages[-1].content
        assert settled.child_task_id in parent_messages[-1].content
        restored_message = service._subtask_results_message(parent.task_id)
        assert settled.child_task_id in restored_message
        assert "merge=failed" in restored_message

    asyncio.run(scenario())


def test_terminal_subtask_failure_settles_relation_and_wakes_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
        store = CodingWorkerStore(
            tmp_path / "worker", master_key=Fernet.generate_key()
        )
        adapter = InMemoryWorkspaceSourceAdapter(
            {("source", "revision"): {"main.py": b"VALUE = 1\n"}}
        )
        workspace_broker = WorkspaceBroker(
            tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
        )
        service = _service_with_legacy_provider(
            store=store,
            workspace_broker=workspace_broker,
            provider=FakeCodingAgentProvider(),
        )
        parent = store.create_task(_spec())
        workspace = await workspace_broker.prepare(parent.spec.workspace_source)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(
            parent.task_id,
            TaskState.RUNNING,
            workspace_id=workspace.workspace_id,
        )
        relation = await service.create_subtask(
            parent.task_id,
            SubtaskRequest(
                client_subtask_id="broken",
                kind=SubtaskKind.IMPLEMENT,
                objective="Change main.py",
            ),
        )
        child = store.get_task(relation.child_task_id)
        child_repo = workspace_broker.repository_path(child.workspace_id or "")
        (child_repo / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
        assert workspace_broker.changed_paths(child.workspace_id or "") == ("main.py",)
        store.transition(child.task_id, TaskState.PREPARING)
        store.transition(child.task_id, TaskState.RUNNING)
        store.transition(child.task_id, TaskState.TESTING)
        store.transition(child.task_id, TaskState.FAILED, reason="worker_failed")

        service._settle_terminal_subtask(child.task_id)

        settled = store.subtask_for_child(child.task_id)
        assert settled is not None
        assert settled.merge_state is SubtaskMergeState.FAILED
        assert settled.changed_paths == ("main.py",)
        assert store.get_task(parent.task_id).state is TaskState.QUEUED
        assert "worker_failed" in (settled.summary or "")

    asyncio.run(scenario())


def test_equivalent_active_subtask_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
        store = CodingWorkerStore(
            tmp_path / "worker", master_key=Fernet.generate_key()
        )
        adapter = InMemoryWorkspaceSourceAdapter(
            {("source", "revision"): {"main.py": b"VALUE = 1\n"}}
        )
        workspace_broker = WorkspaceBroker(
            tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
        )
        service = _service_with_legacy_provider(
            store=store,
            workspace_broker=workspace_broker,
            provider=FakeCodingAgentProvider(),
        )
        parent = store.create_task(_spec())
        workspace = await workspace_broker.prepare(parent.spec.workspace_source)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(
            parent.task_id,
            TaskState.RUNNING,
            workspace_id=workspace.workspace_id,
        )
        request = SubtaskRequest(
            client_subtask_id="first",
            kind=SubtaskKind.IMPLEMENT,
            objective="Change main.py",
        )
        await service.create_subtask(parent.task_id, request)
        store.transition(parent.task_id, TaskState.QUEUED)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(parent.task_id, TaskState.RUNNING)

        with pytest.raises(WorkerConflictError) as duplicate:
            await service.create_subtask(
                parent.task_id,
                request.model_copy(update={"client_subtask_id": "second"}),
            )
        assert duplicate.value.code == "subtask_duplicate_intent"
        assert len(store.list_subtasks(parent.task_id)) == 1

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
        service = _service_with_legacy_provider(
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

        with pytest.raises(ToolBrokerError) as parked:
            await broker.execute(
                task_id=parent.task_id,
                operation_id="parked-read",
                tool_name="read_file",
                arguments={"path": "main.py"},
            )
        assert parked.value.code == "task_state_conflict"

    asyncio.run(scenario())


def test_provider_parks_immediately_after_delegation_and_resumes_after_release(
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
        provider = _DelegatingProvider()
        service = _service_with_legacy_provider(
            store=store,
            workspace_broker=workspace_broker,
            provider=provider,
        )
        provider.delegate = service.create_subtask
        parent = store.create_task(_spec())
        store.transition(parent.task_id, TaskState.PREPARING)

        await service._run_task(parent.task_id)

        parked = store.get_task(parent.task_id)
        assert parked.state is TaskState.WAITING_SUBTASKS
        assert provider.continued_after_delegation is False
        checkpoint = store.latest_checkpoint(parent.task_id)
        assert checkpoint is not None, (
            parked.reason,
            [(event.type, event.payload) for event in store.list_events(parent.task_id)],
        )
        assert checkpoint.payload["phase"] == "waiting_subtasks"
        relation = store.list_subtasks(parent.task_id)[0]
        child = store.get_task(relation.child_task_id)
        store.finish_subtask(
            child.task_id,
            result_tree_hash=workspace_broker.current_tree_hash(
                child.workspace_id or ""
            ),
            changed_paths=(),
            summary="Inspected the module.",
        )

        service._active[parent.task_id] = asyncio.current_task()  # type: ignore[assignment]
        service._resume_parent_after_subtasks(parent.task_id)
        assert store.get_task(parent.task_id).state is TaskState.WAITING_SUBTASKS
        service._active.pop(parent.task_id)
        service._resume_parent_after_subtasks(parent.task_id)
        assert store.get_task(parent.task_id).state is TaskState.QUEUED

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
        service = _service_with_legacy_provider(
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
    configure_coding_worker_for_tests(runtime.substrate, enabled=True)
    try:
        # Flags do not make a provider-dependent capability available. These
        # deliberately unreachable sidecars keep the public capability false.
        assert coding_worker_capabilities().subtasks is False
        paths = {route.path for route in router.routes}
        assert "/api/coding-worker/v1/tasks/{task_id}/subtasks" in paths
        assert (
            "/api/coding-worker/v1/tasks/{task_id}/subtasks/"
            "{child_task_id}/merge"
        ) in paths
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
        service = _service_with_legacy_provider(
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


def test_merge_subtask_tool_reconciles_lost_receipt_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("CODING_WORKER_V16_ENABLED", "true")
        monkeypatch.setenv("CODING_WORKER_SUBAGENTS_ENABLED", "true")
        store = CodingWorkerStore(
            tmp_path / "worker", master_key=Fernet.generate_key()
        )
        adapter = InMemoryWorkspaceSourceAdapter(
            {("source", "revision"): {"main.py": b"VALUE = 1\n"}}
        )
        workspace_broker = WorkspaceBroker(
            tmp_path / "worker", {"manifest": adapter}, id_key=b"s" * 32
        )
        service = _service_with_legacy_provider(
            store=store,
            workspace_broker=workspace_broker,
            provider=FakeCodingAgentProvider(),
        )
        broker = ToolBroker(
            store=store,
            workspace_broker=workspace_broker,
            subtask_handler=service.create_subtask,
            subtask_merge_handler=service.merge_subtask,
        )
        service.tool_broker = broker
        parent = store.create_task(_spec())
        workspace = await workspace_broker.prepare(parent.spec.workspace_source)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(
            parent.task_id,
            TaskState.RUNNING,
            workspace_id=workspace.workspace_id,
        )
        relation = await service.create_subtask(
            parent.task_id,
            SubtaskRequest(
                client_subtask_id="implementation",
                kind=SubtaskKind.IMPLEMENT,
                objective="Update main.py",
            ),
        )
        child = store.get_task(relation.child_task_id)
        child_repo = workspace_broker.repository_path(child.workspace_id or "")
        (child_repo / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
        store.finish_subtask(
            child.task_id,
            result_tree_hash=workspace_broker.current_tree_hash(
                child.workspace_id or ""
            ),
            changed_paths=("main.py",),
            summary="Updated main.py",
        )
        store.transition(parent.task_id, TaskState.QUEUED)
        store.transition(parent.task_id, TaskState.PREPARING)
        store.transition(parent.task_id, TaskState.RUNNING)

        original_transition = store.transition_operation
        failed_receipt = False

        def fail_first_receipt(*args, **kwargs):
            nonlocal failed_receipt
            if (
                kwargs.get("state") is None
                and len(args) > 1
                and args[1].value == "completed"
                and not failed_receipt
            ):
                failed_receipt = True
                raise OSError("simulated receipt loss")
            return original_transition(*args, **kwargs)

        monkeypatch.setattr(store, "transition_operation", fail_first_receipt)
        with pytest.raises(ToolBrokerError) as unknown:
            await broker.execute(
                task_id=parent.task_id,
                operation_id="merge-operation",
                tool_name="merge_subtask",
                arguments={"child_task_id": child.task_id},
            )
        assert unknown.value.code == "operation_result_unknown"
        reconciled = await broker.execute(
            task_id=parent.task_id,
            operation_id="merge-operation",
            tool_name="merge_subtask",
            arguments={"child_task_id": child.task_id},
        )
        assert reconciled.data["subtask"]["merge_state"] == "merged"
        assert (
            workspace_broker.repository_path(workspace.workspace_id) / "main.py"
        ).read_text(encoding="utf-8") == "VALUE = 2\n"
        assert "merge_subtask" in PROVIDER_TOOL_NAMES
        assert "merge_subtask" not in INSPECT_PROVIDER_TOOLS

    asyncio.run(scenario())
