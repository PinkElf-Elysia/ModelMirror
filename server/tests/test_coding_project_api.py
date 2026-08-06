from __future__ import annotations

import sqlite3
import difflib
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from server.coding_runtime.api import CodingService, configure_coding_service, router
from server.coding_project_source.server import ProjectSnapshotBroker
from server.coding_project_writer import CodingProjectWriterEngine
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CommitReceipt
from server.coding_runtime.project_source_client import (
    ProjectSourceClientError,
    _validate_public_project,
)
from server.coding_runtime.projects import ProjectFeatures, ProjectKind, build_project_id
from server.coding_runtime.recovery import CodingRecoveryStore, RecoveryProjectContext
from server.tests.test_coding_runtime_api import FakeWorker


PROJECT_ID = "local-731c5e9a21f44394b8de54a1"
PROJECT_HEAD = "c" * 40
PROJECT_FINGERPRINT = "b" * 64


def _public_project() -> dict[str, Any]:
    return {
        "id": PROJECT_ID,
        "name": "随机项目 731",
        "kind": "local_clone",
        "state": "available",
        "reason": None,
        "branch": "main",
        "head": PROJECT_HEAD[:12],
        "features": ProjectFeatures.local_draft().to_dict(),
        "writeback_reason": "writeback_not_enabled",
    }


def _lease() -> dict[str, Any]:
    return {
        "kind": "local_clone",
        "lease_id": "lease-731-random",
        "project_id": PROJECT_ID,
        "name": "随机项目 731",
        "branch": "main",
        "head": PROJECT_HEAD,
        "fingerprint": PROJECT_FINGERPRINT,
        "file_count": 7,
        "total_bytes": 731,
        "hidden_files": 2,
        "created_at": 1000.0,
    }


class FakeProjectSource:
    def __init__(self) -> None:
        self.available = True
        self.current_head = PROJECT_HEAD
        self.acquired: list[tuple[str, str | None]] = []
        self.released: list[tuple[str, str]] = []

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "configured": True,
            "available": self.available,
            "reason": None if self.available else "project_source_unavailable",
        }

    async def list_projects(self) -> list[dict[str, Any]]:
        return [_public_project()]

    async def check(self, project_id: str, expected_head: str) -> dict[str, Any]:
        if project_id != PROJECT_ID or expected_head != self.current_head:
            from server.coding_runtime.project_source_client import (
                ProjectSourceClientError,
            )

            raise ProjectSourceClientError("changed", code="project_changed")
        return _public_project()

    async def acquire(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        if project_id != PROJECT_ID or (
            expected_head is not None and expected_head != self.current_head
        ):
            from server.coding_runtime.project_source_client import (
                ProjectSourceClientError,
            )

            raise ProjectSourceClientError("changed", code="project_changed")
        self.acquired.append((project_id, expected_head))
        return _lease()

    async def release(self, project_id: str, lease_id: str) -> bool:
        self.released.append((project_id, lease_id))
        return True


class LocalProjectWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__(mode="draft", snapshot_fingerprint=PROJECT_FINGERPRINT)
        self.source: dict[str, Any] | None = None
        self.verification_status_calls = 0

    async def create_session(
        self,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.source = source
        result = await super().create_session()
        result["project"] = _public_project() if source is not None else None
        return result

    async def restore_session(self, **kwargs: Any) -> dict[str, Any]:
        self.source = kwargs.get("source")
        result = await super().restore_session(**kwargs)
        result["project"] = _public_project()
        return result

    async def recovery_snapshot(self, session_id: str) -> dict[str, Any]:
        result = await super().recovery_snapshot(session_id)
        result["project"] = _public_project()
        return result

    async def verification_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        self.verification_status_calls += 1
        return await super().verification_status(session_id, revision)


def _service(
    worker: LocalProjectWorker,
    source: FakeProjectSource,
    *,
    store: CodingRecoveryStore | None = None,
    writer: Any | None = None,
) -> CodingService:
    return CodingService(
        enabled=True,
        worker=worker,
        project_source=source,
        project_writer=writer,
        projects_enabled=True,
        project_writeback_enabled=writer is not None,
        recovery_store=store,
        recovery_enabled=store is not None,
        mode="draft",
    )


class FakeProjectWriter:
    def __init__(self) -> None:
        self.apply_receipt: ApplyReceipt | None = None
        self.commit_receipt: CommitReceipt | None = None
        self.calls: list[str] = []

    async def health(self) -> dict[str, Any]:
        return {
            "configured": True,
            "available": True,
            "target": "selected_local_repository",
        }

    async def apply(self, **kwargs: Any) -> ApplyReceipt:
        self.calls.append("apply")
        self.apply_receipt = ApplyReceipt(
            apply_id=kwargs["operation_id"],
            revision=kwargs["revision"],
            snapshot_fingerprint=kwargs["expected_fingerprint"],
            files=(
                ApplyFileReceipt(
                    path=kwargs["paths"][0],
                    existed_before=True,
                    before_sha256="1" * 64,
                    after_sha256="2" * 64,
                ),
            ),
        )
        return self.apply_receipt

    async def revert(self, **kwargs: Any) -> ApplyReceipt:
        self.calls.append("revert")
        return kwargs["receipt"]

    async def commit(self, **kwargs: Any) -> CommitReceipt:
        self.calls.append("commit")
        receipt = kwargs["apply_receipt"]
        self.commit_receipt = CommitReceipt(
            commit_id=kwargs["operation_id"],
            revision=receipt.revision,
            apply_id=receipt.apply_id,
            commit_sha="3" * 40,
            parent_sha=PROJECT_HEAD,
            tree_sha="4" * 40,
            message=kwargs["message"],
            files=tuple(item.path for item in receipt.files),
        )
        return self.commit_receipt

    async def undo(self, **kwargs: Any) -> CommitReceipt:
        self.calls.append("undo")
        return kwargs["commit_receipt"]

    async def reconcile_apply(self, **kwargs: Any) -> tuple[str, ApplyReceipt | None]:
        self.calls.append("reconcile_apply")
        return ("applied", self.apply_receipt)

    async def reconcile_commit(
        self,
        **kwargs: Any,
    ) -> tuple[str, ApplyReceipt, CommitReceipt | None]:
        self.calls.append("reconcile_commit")
        assert self.apply_receipt is not None
        return "committed", self.apply_receipt, self.commit_receipt


def _enable_writeback(record: Any) -> None:
    record.project["features"]["apply"] = True
    record.project["features"]["commit"] = True
    record.project["writeback_reason"] = None


def _client(service: CodingService) -> httpx.AsyncClient:
    configure_coding_service(service)
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def test_project_source_public_contract_allows_safe_unavailable_entry_only() -> None:
    unavailable = {
        **_public_project(),
        "state": "unavailable",
        "reason": "project_dirty",
        "branch": None,
        "head": None,
    }
    assert _validate_public_project(unavailable) == unavailable

    with pytest.raises(ProjectSourceClientError):
        _validate_public_project({**unavailable, "path": "C:/private/project"})
    with pytest.raises(ProjectSourceClientError):
        _validate_public_project({**unavailable, "reason": "unsafe reason"})


@pytest.mark.asyncio
async def test_project_catalog_and_optional_session_body_do_not_expose_paths() -> None:
    worker = LocalProjectWorker()
    source = FakeProjectSource()
    service = _service(worker, source)
    async with _client(service) as client:
        catalog = await client.get("/api/coding/projects")
        builtin = await client.post("/api/coding/sessions")

    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["projects"]] == [
        "modelmirror",
        PROJECT_ID,
    ]
    serialized = catalog.text.lower()
    assert "path" not in serialized
    assert "remote" not in serialized
    assert builtin.status_code == 201
    assert builtin.json()["project"]["id"] == "modelmirror"
    await service.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_local_session_binds_one_lease_and_releases_after_safe_close() -> None:
    worker = LocalProjectWorker()
    source = FakeProjectSource()
    service = _service(worker, source)
    async with _client(service) as client:
        created = await client.post(
            "/api/coding/sessions",
            json={"project_id": PROJECT_ID},
        )
        session_id = created.json()["id"]
        status = await client.get(f"/api/coding/sessions/{session_id}")
        worker.current_empty = True
        closed = await client.post(f"/api/coding/sessions/{session_id}/close")

    assert created.status_code == 201
    assert created.json()["project"] == _public_project()
    assert status.json()["project"]["id"] == PROJECT_ID
    assert source.acquired == [(PROJECT_ID, None)]
    assert closed.status_code == 200
    assert worker.closed == [worker.session_id]
    assert source.released == [(PROJECT_ID, "lease-731-random")]
    await service.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_local_project_operations_fail_before_optional_services_are_used() -> None:
    worker = LocalProjectWorker()
    source = FakeProjectSource()
    service = _service(worker, source)
    async with _client(service) as client:
        created = await client.post(
            "/api/coding/sessions",
            json={"project_id": PROJECT_ID},
        )
        session_id = created.json()["id"]
        blocked = [
            await client.post(
                f"/api/coding/sessions/{session_id}/verification",
                json={"revision": 1},
            ),
            await client.get(
                f"/api/coding/sessions/{session_id}/verification",
                params={"revision": 1},
            ),
            await client.post(
                f"/api/coding/sessions/{session_id}/apply",
                json={"revision": 1},
            ),
            await client.get(
                f"/api/coding/sessions/{session_id}/publish",
                params={"revision": 1},
            ),
        ]
        commit_status = await client.get(
            f"/api/coding/sessions/{session_id}/commit",
            params={"revision": 1},
        )
        started = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "只改说明文字，随机标记 q9-731"},
        )

    assert all(response.status_code == 409 for response in blocked)
    assert {
        response.json()["detail"]["code"] for response in blocked
    } == {"project_operation_unavailable", "writeback_not_enabled"}
    assert commit_status.status_code == 200
    assert commit_status.json()["state"] == "not_committed"
    assert started.status_code == 202
    assert worker.verification_status_calls == 0
    await service.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_local_recovery_context_is_encrypted_and_resumes_exact_project(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery")
    first_worker = LocalProjectWorker()
    first_source = FakeProjectSource()
    first = _service(first_worker, first_source, store=store)
    record = await first.create_session(PROJECT_ID)
    assert await first._persist_recovery(record, required=True) is True
    recovery_id = record.recovery_id
    assert recovery_id is not None
    context = store.load_project_context(recovery_id)
    assert context is not None
    assert context == RecoveryProjectContext(
        recovery_id=recovery_id,
        project_id=PROJECT_ID,
        kind=ProjectKind.LOCAL_CLONE,
        name="随机项目 731",
        head=PROJECT_HEAD,
    )
    database = store.database_path.read_bytes()
    assert PROJECT_ID.encode() not in database
    assert "随机项目 731".encode() not in database
    assert PROJECT_HEAD.encode() not in database
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    await first.shutdown()

    second_worker = LocalProjectWorker()
    second_source = FakeProjectSource()
    second = _service(second_worker, second_source, store=store)
    pending = await second.recovery_status()
    resumed = await second.resume_recovery()

    assert pending["can_resume"] is True
    assert pending["project"]["id"] == PROJECT_ID
    assert resumed.project["id"] == PROJECT_ID
    assert second_source.acquired == [(PROJECT_ID, PROJECT_HEAD)]
    assert second_worker.restore_calls[0]["source"]["project_id"] == PROJECT_ID
    await second.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_changed_project_blocks_resume_but_keeps_recovery_patch(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery")
    worker = LocalProjectWorker()
    source = FakeProjectSource()
    first = _service(worker, source, store=store)
    record = await first.create_session(PROJECT_ID)
    await first._persist_recovery(record, required=True)
    await first.shutdown()

    changed_source = FakeProjectSource()
    changed_source.current_head = "d" * 40
    second = _service(LocalProjectWorker(), changed_source, store=store)
    pending = await second.recovery_status()
    revision, patch = await second.recovery_patch()

    assert pending["can_resume"] is False
    assert pending["reason"] == "project_changed"
    assert revision == 1
    assert "server/example.py" in patch
    await second.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_custom_project_apply_commit_undo_and_revert_use_only_writer() -> None:
    worker = LocalProjectWorker()
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    source = FakeProjectSource()
    writer = FakeProjectWriter()
    service = _service(worker, source, writer=writer)
    record = await service.create_session(PROJECT_ID)
    _enable_writeback(record)

    applied = await service.apply(record.session_id, 1)
    committed = await service.commit(
        record.session_id,
        1,
        applied["apply_id"],
        "feature: save random q7m4",
    )
    undone = await service.undo_commit(
        record.session_id,
        1,
        applied["apply_id"],
        committed["commit_id"],
    )
    reverted = await service.revert_apply(
        record.session_id,
        1,
        applied["apply_id"],
    )

    assert writer.calls == ["apply", "commit", "undo", "revert"]
    assert applied["state"] == "applied"
    assert committed["state"] == "committed"
    assert undone["state"] == "undone"
    assert reverted["state"] == "reverted"
    await service.shutdown()


@pytest.mark.asyncio
async def test_custom_project_commit_state_is_encrypted_and_reconciled_after_restart(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery")
    writer = FakeProjectWriter()
    first_worker = LocalProjectWorker()
    first_worker.verification_state = "completed"
    first_worker.verification_result = "passed"
    first = _service(
        first_worker,
        FakeProjectSource(),
        store=store,
        writer=writer,
    )
    record = await first.create_session(PROJECT_ID)
    _enable_writeback(record)
    applied = await first.apply(record.session_id, 1)
    committed = await first.commit(
        record.session_id,
        1,
        applied["apply_id"],
        "feature: persist random r8v3",
    )
    database = store.database_path.read_bytes()
    assert committed["commit_id"].encode() not in database
    await first.shutdown()

    second = _service(
        LocalProjectWorker(),
        FakeProjectSource(),
        store=store,
        writer=writer,
    )
    resumed = await second.resume_recovery()

    assert resumed.apply_state.value == "applied"
    assert resumed.commit_state.value == "committed"
    assert resumed.commit_receipt == writer.commit_receipt
    assert resumed.project["features"]["apply"] is True
    assert writer.calls[-1] == "reconcile_commit"
    await second.shutdown()


def _git_fixture(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=project,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_writeback_baseline_snapshot_survives_dirty_tree_and_linear_commit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    project = root / "team" / "writeback-q7m4"
    project.mkdir(parents=True)
    _git_fixture(project, "init", "-b", "coding/local-draft")
    (project / "marker.txt").write_text("baseline=q7m4\n", encoding="utf-8")
    _git_fixture(project, "add", ".")
    _git_fixture(
        project,
        "-c",
        "user.name=Snapshot Test",
        "-c",
        "user.email=snapshot@example.test",
        "commit",
        "-m",
        "baseline",
    )
    baseline = _git_fixture(project, "rev-parse", "HEAD")
    (root / ".modelmirror-coding-projects.json").write_text(
        json.dumps(
            {
                "version": 3,
                "projects": [
                    {
                        "name": "Writeback q7m4",
                        "path": "team/writeback-q7m4",
                        "writeback": {"enabled": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    project_id = build_project_id("team/writeback-q7m4")

    (project / "marker.txt").write_text("applied=r8v3\n", encoding="utf-8")
    dirty_broker = ProjectSnapshotBroker(root, tmp_path / "dirty-slot")
    dirty_lease = dirty_broker.acquire(project_id, baseline)
    assert dirty_lease.head == baseline
    assert (
        tmp_path / "dirty-slot" / "current" / "workspace" / "marker.txt"
    ).read_text(encoding="utf-8") == "baseline=q7m4\n"
    dirty_broker.close()

    _git_fixture(project, "add", "marker.txt")
    _git_fixture(
        project,
        "-c",
        "user.name=Snapshot Test",
        "-c",
        "user.email=snapshot@example.test",
        "commit",
        "-m",
        "applied",
    )
    committed_broker = ProjectSnapshotBroker(root, tmp_path / "commit-slot")
    committed_lease = committed_broker.acquire(project_id, baseline)
    assert committed_lease.head == baseline
    assert (
        tmp_path / "commit-slot" / "current" / "workspace" / "marker.txt"
    ).read_text(encoding="utf-8") == "baseline=q7m4\n"


def test_writer_reconciles_commit_receipt_after_process_restart(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    project = root / "team" / "reconcile-r8v3"
    project.mkdir(parents=True)
    _git_fixture(project, "init", "-b", "coding/local-draft")
    before = "value=q7m4\n"
    after = "value=r8v3\n"
    (project / "value.txt").write_text(before, encoding="utf-8")
    _git_fixture(project, "add", ".")
    _git_fixture(
        project,
        "-c",
        "user.name=Reconcile Test",
        "-c",
        "user.email=reconcile@example.test",
        "commit",
        "-m",
        "baseline",
    )
    baseline = _git_fixture(project, "rev-parse", "HEAD")
    relative = "team/reconcile-r8v3"
    (root / ".modelmirror-coding-projects.json").write_text(
        json.dumps(
            {
                "version": 3,
                "projects": [
                    {
                        "name": "Reconcile r8v3",
                        "path": relative,
                        "writeback": {"enabled": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    project_id = build_project_id(relative)
    lease = ProjectSnapshotBroker(root, tmp_path / "snapshot").acquire(
        project_id,
        baseline,
    )
    body = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/value.txt",
            tofile="b/value.txt",
            lineterm="\n",
        )
    )
    patch = f"diff --git a/value.txt b/value.txt\n{body}"
    apply_id = "apply_reconcile_q7m4_123"
    commit_id = "commit_reconcile_r8v3_12"
    first = CodingProjectWriterEngine(root, tmp_path / "writer-temp")
    applied = first.apply(
        project_id=project_id,
        expected_head=baseline,
        operation_id=apply_id,
        revision=9,
        patch=patch,
        paths=["value.txt"],
        expected_fingerprint=lease.fingerprint,
    )
    committed = first.commit(
        project_id=project_id,
        expected_head=baseline,
        operation_id=commit_id,
        apply_receipt=applied,
        message="feature: reconcile random r8v3",
    )

    restarted = CodingProjectWriterEngine(root, tmp_path / "writer-temp")
    state, restored_apply, restored_commit = restarted.reconcile_commit(
        project_id=project_id,
        expected_head=baseline,
        operation_id=apply_id,
        revision=9,
        patch=patch,
        paths=["value.txt"],
        expected_fingerprint=lease.fingerprint,
        apply_receipt=applied,
        commit_operation_id=commit_id,
        message="feature: reconcile random r8v3",
    )

    assert state == "committed"
    assert restored_apply == applied
    assert restored_commit == committed
    restarted.undo(
        project_id=project_id,
        expected_head=baseline,
        apply_receipt=restored_apply,
        commit_receipt=restored_commit,
    )
    restarted.revert(
        project_id=project_id,
        expected_head=baseline,
        receipt=restored_apply,
    )
    assert _git_fixture(project, "status", "--porcelain") == ""
