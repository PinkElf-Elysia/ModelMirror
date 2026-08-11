from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    EvidenceStatus,
    Origin,
    PolicyProfile,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.evidence import HarnessRunner
from server.coding_worker.store import CodingWorkerStore, WorkerConflictError
from server.coding_worker.tool_broker import FrozenCheck, ToolBroker
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


async def _harness(tmp_path: Path) -> tuple[HarnessRunner, CodingWorkerStore, ToolBroker, str, Path]:
    source = WorkspaceSource(kind="manifest", source_id="evidence", revision="h0")
    workspace = WorkspaceBroker(
        tmp_path / "workspace",
        {"manifest": InMemoryWorkspaceSourceAdapter({("evidence", "h0"): {"app.py": b"bad python\n"}})},
        id_key=b"e" * 32,
    )
    prepared = await workspace.prepare(source)
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    task = store.create_task(
        TaskSpec(
            client_task_id="evidence-task",
            origin=Origin(module="tests", object_id="evidence"),
            objective="fix syntax",
            workspace_source=source,
            acceptance=AcceptanceContract(
                contract_id="contract",
                required_checks=(AcceptanceCheck(check_id="syntax", label="syntax", kind="command"),),
            ),
            policy_profile=PolicyProfile.DEVELOP,
            model_route="coding/default",
        )
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id)
    broker = ToolBroker(
        store=store,
        workspace_broker=workspace,
        frozen_checks={"syntax": FrozenCheck(check_id="syntax", argv=(sys.executable, "-m", "py_compile", "app.py"))},
    )
    return (
        HarnessRunner(store=store, workspace_broker=workspace, tool_broker=broker),
        store,
        broker,
        task.task_id,
        workspace.repository_path(prepared.workspace_id),
    )


@pytest.mark.asyncio
async def test_failed_check_blocks_completion_then_fix_and_retest_passes(tmp_path: Path) -> None:
    harness, store, broker, task_id, repository = await _harness(tmp_path)
    failed = await harness.run_required_checks(task_id)
    assert failed[0].status is EvidenceStatus.FAILED
    assert not harness.acceptance_satisfied(task_id)
    content = "print('fixed')\n"
    await broker.execute(
        task_id=task_id,
        operation_id="fix-file",
        tool_name="write_file",
        arguments={
            "path": "app.py",
            "content": content,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        },
    )
    invalidated = store.list_evidence(
        task_id,
        current_tree_hash=harness.workspace_broker.current_tree_hash(store.get_task(task_id).workspace_id),
    )
    assert invalidated[0].status is EvidenceStatus.INVALIDATED
    passed = await harness.run_required_checks(task_id)
    assert passed[0].status is EvidenceStatus.PASSED
    assert harness.acceptance_satisfied(task_id)
    assert repository.joinpath("app.py").read_text() == content


@pytest.mark.asyncio
async def test_artifact_and_evidence_are_encrypted_and_task_bound(tmp_path: Path) -> None:
    harness, store, _, task_id, _ = await _harness(tmp_path)
    evidence = (await harness.run_required_checks(task_id))[0]
    content = store.read_artifact(evidence.artifact_id, task_id=task_id)
    assert b"SyntaxError" in content
    raw = store.database_path.read_bytes()
    assert b"SyntaxError" not in raw and b"bad python" not in raw
    other = store.create_task(store.get_task(task_id).spec.model_copy(update={"client_task_id": "other"}))
    with pytest.raises(Exception):
        store.read_artifact(evidence.artifact_id, task_id=other.task_id)


@pytest.mark.asyncio
async def test_acceptance_contract_cannot_be_replaced_by_agent_evidence(tmp_path: Path) -> None:
    harness, store, _, task_id, _ = await _harness(tmp_path)
    artifact = store.create_artifact(
        task_id=task_id,
        media_type="text/plain",
        content=b"pretend pass",
        metadata={},
    )
    with pytest.raises(WorkerConflictError) as unknown:
        store.record_evidence(
            task_id=task_id,
            check_id="agent-replaced-check",
            operation_id="forged-operation",
            workspace_tree_hash=harness.workspace_broker.current_tree_hash(store.get_task(task_id).workspace_id),
            exit_code=0,
            artifact_id=artifact.artifact_id,
        )
    assert unknown.value.code == "acceptance_check_unknown"
    assert not harness.acceptance_satisfied(task_id)
