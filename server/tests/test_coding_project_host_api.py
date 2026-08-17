from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from server.coding_runtime.api import (
    CodingService,
    _normalize_worker_handoff_diff,
    configure_coding_service,
    router,
)
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CommitReceipt
from server.coding_runtime.applier_client import _receipt_to_payload
from server.coding_runtime.committer_client import _commit_receipt_to_payload
from server.coding_runtime.patch_policy import snapshot_fingerprint
from server.coding_runtime.project_host import (
    PROJECT_HOST_PROTOCOL_V2,
    ProjectHostError,
    ProjectHostStore,
)
from server.coding_runtime.project_host_api import (
    ProjectHostRuntime,
    _OperationPayload,
    _Transfer,
)
from server.coding_runtime.project_writer_client import ProjectWriterClientError
from server.coding_runtime.projects import ProjectFeatures, ProjectKind
from server.coding_worker.api import configure_coding_worker_for_tests
from server.coding_worker.contracts import TaskState
from server.coding_runtime.recovery import CodingRecoveryStore, RecoveryProjectContext
from server.coding_runtime.draft_workspace import DraftWorkspace
from server.coding_runtime.worker import CodingWorkerError, CodingWorkerServer
from server.tests.test_coding_runtime_api import FakeWorker


PROJECT_ID = "hostgit_0123456789abcdef0123456789abcdef"
PROJECT_HEAD = "a" * 40
PROJECT_FINGERPRINT = "b" * 64
PROJECT_BRANCH = "feature/nebula-k8r3"


def _apply_receipt(operation_id: str = "apply_0123456789abcdef012345") -> ApplyReceipt:
    return ApplyReceipt(
        apply_id=operation_id,
        revision=3,
        snapshot_fingerprint=PROJECT_FINGERPRINT,
        files=(
            ApplyFileReceipt(
                path="src/nebula.py",
                existed_before=True,
                before_sha256="c" * 64,
                after_sha256="d" * 64,
            ),
        ),
        applied_at=1_785_600_000.0,
    )


def _commit_receipt(
    apply_receipt: ApplyReceipt,
    operation_id: str = "commit_0123456789abcdef0123",
) -> CommitReceipt:
    return CommitReceipt(
        commit_id=operation_id,
        revision=apply_receipt.revision,
        apply_id=apply_receipt.apply_id,
        commit_sha="e" * 40,
        parent_sha=PROJECT_HEAD,
        tree_sha="f" * 40,
        message="feature: update nebula",
        files=tuple(item.path for item in apply_receipt.files),
        branch=PROJECT_BRANCH,
        committed_at=1_785_600_001.0,
    )


def _public_project(*, writeback: bool = False) -> dict[str, Any]:
    features = ProjectFeatures.host_git().to_dict()
    features.update({"apply": writeback, "commit": writeback})
    return {
        "id": PROJECT_ID,
        "name": "星云 k8r3",
        "kind": "host_git",
        "state": "available",
        "reason": None,
        "branch": "feature/nebula-k8r3",
        "head": PROJECT_HEAD[:12],
        "features": features,
        "writeback_reason": None if writeback else "project_host_writeback_disabled",
    }


def _lease() -> dict[str, Any]:
    return {
        "kind": "host_git",
        "lease_id": "lease_host_k8r3_202608",
        "project_id": PROJECT_ID,
        "name": "星云 k8r3",
        "branch": "feature/nebula-k8r3",
        "head": PROJECT_HEAD,
        "fingerprint": PROJECT_FINGERPRINT,
        "file_count": 3,
        "total_bytes": 731,
        "hidden_files": 1,
        "created_at": 1_785_600_000.0,
    }


def _registered_runtime(tmp_path: Path) -> ProjectHostRuntime:
    store = ProjectHostStore(tmp_path / "state.json")
    _pairing, code = store.create_pairing("local helper")
    host, _token = store.consume_pairing(
        code,
        device_id="pdev_0123456789abcdef0123456789abcdef",
        version="1.1.0",
        platform="windows",
        protocol=PROJECT_HOST_PROTOCOL_V2,
    )
    store.register_project(
        host.host_id,
        {
            "project_id": PROJECT_ID,
            "name": "nebula",
            "branch": PROJECT_BRANCH,
            "head": PROJECT_HEAD,
            "state": "available",
            "reason": None,
        },
    )
    return ProjectHostRuntime(
        store,
        tmp_path / "uploads",
        writeback_enabled=True,
    )


class FakeHostRuntime:
    def __init__(self, *, writeback: bool = False) -> None:
        self.writeback = writeback
        self.snapshot_calls: list[
            tuple[str, str | None, str | None, str | None]
        ] = []
        self.finished: list[str] = []

    def capability(self, **_: Any) -> dict[str, Any]:
        return {
            "enabled": True,
            "paired": True,
            "available": True,
            "platform": "windows",
            "selection": True,
            "remembers_projects": True,
            "direct_writeback": self.writeback,
            "writeback_available": self.writeback,
        }

    async def health(self) -> dict[str, Any]:
        return {
            "configured": self.writeback,
            "available": self.writeback,
            **({} if self.writeback else {"reason": "project_host_writeback_disabled"}),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        return [_public_project(writeback=self.writeback)]

    def public_project(self, project_id: str) -> dict[str, Any]:
        if project_id != PROJECT_ID:
            raise ProjectHostError("project_not_found")
        return _public_project(writeback=self.writeback)

    def check_project(self, project_id: str, head: str, branch: str | None) -> dict[str, Any]:
        if (project_id, head, branch) != (
            PROJECT_ID,
            PROJECT_HEAD,
            "feature/nebula-k8r3",
        ):
            raise ProjectHostError("project_changed")
        return _public_project()

    async def request_snapshot(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> dict[str, Any]:
        self.snapshot_calls.append(
            (project_id, expected_head, expected_branch, managed_operation_id)
        )
        self.check_project(
            project_id,
            expected_head or PROJECT_HEAD,
            expected_branch or "feature/nebula-k8r3",
        )
        return {
            "upload_id": "1" * 32,
            "archive_sha256": "2" * 64,
            "project": {
                "project_id": PROJECT_ID,
                "name": "星云 k8r3",
                "branch": "feature/nebula-k8r3",
                "head": PROJECT_HEAD,
            },
        }

    def finish_transfer(self, transfer_id: str) -> None:
        self.finished.append(transfer_id)


class WritebackHostRuntime(FakeHostRuntime):
    def __init__(self) -> None:
        super().__init__(writeback=True)
        self.current_head = PROJECT_HEAD
        self.current_state = "available"
        self.intent_calls: list[tuple[str, str]] = []
        self.recovery_bindings: list[tuple[str | None, str | None]] = []
        self.apply_calls = 0
        self.commit_calls = 0
        self.reconcile_commit_calls = 0
        self.apply_requests: list[dict[str, Any]] = []
        self.commit_requests: list[dict[str, Any]] = []
        self.reconcile_commit_requests: list[dict[str, Any]] = []
        self.commit_receipts: list[CommitReceipt] = []
        self.fail_commit_after_effect = False
        self._committed: CommitReceipt | None = None
        self._committed_by_id: dict[str, CommitReceipt] = {}

    def check_project(
        self,
        project_id: str,
        head: str,
        branch: str | None,
    ) -> dict[str, Any]:
        if (project_id, head, branch) != (
            PROJECT_ID,
            self.current_head,
            PROJECT_BRANCH,
        ) or self.current_state != "available":
            raise ProjectHostError("project_changed")
        return _public_project(writeback=True)

    async def request_snapshot(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> dict[str, Any]:
        self.snapshot_calls.append(
            (project_id, expected_head, expected_branch, managed_operation_id)
        )
        if managed_operation_id is None:
            self.check_project(
                project_id,
                expected_head or self.current_head,
                expected_branch or PROJECT_BRANCH,
            )
        elif (project_id, expected_head, expected_branch) != (
            PROJECT_ID,
            PROJECT_HEAD,
            PROJECT_BRANCH,
        ):
            raise ProjectHostError("project_changed")
        return {
            "upload_id": "1" * 32,
            "archive_sha256": "2" * 64,
            "project": {
                "project_id": PROJECT_ID,
                "name": "星云 k8r3",
                "branch": PROJECT_BRANCH,
                "head": PROJECT_HEAD,
            },
        }

    def bind_persisted_intent(
        self,
        *,
        operation_id: str,
        kind: str,
        **_: Any,
    ) -> None:
        self.intent_calls.append((kind, operation_id))

    def bind_recovery_operations(
        self,
        *,
        apply_operation_id: str | None = None,
        commit_operation_id: str | None = None,
        **_: Any,
    ) -> None:
        self.recovery_bindings.append(
            (apply_operation_id, commit_operation_id)
        )

    async def apply(self, **kwargs: Any) -> ApplyReceipt:
        self.apply_calls += 1
        self.apply_requests.append(dict(kwargs))
        receipt = ApplyReceipt(
            apply_id=kwargs["operation_id"],
            revision=kwargs["revision"],
            snapshot_fingerprint=kwargs["expected_fingerprint"],
            files=tuple(
                ApplyFileReceipt(
                    path=path,
                    existed_before=True,
                    before_sha256="c" * 64,
                    after_sha256="d" * 64,
                )
                for path in kwargs["paths"]
            ),
            applied_at=1_785_600_000.0,
        )
        self.current_state = "dirty"
        return receipt

    async def commit(self, **kwargs: Any) -> CommitReceipt:
        self.commit_calls += 1
        self.commit_requests.append(dict(kwargs))
        applied = kwargs["apply_receipt"]
        commit_sha = f"{self.commit_calls:x}" * 40
        receipt = CommitReceipt(
            commit_id=kwargs["operation_id"],
            revision=applied.revision,
            apply_id=applied.apply_id,
            commit_sha=commit_sha,
            parent_sha=kwargs["expected_head"],
            tree_sha="f" * 40,
            message=kwargs["message"],
            files=tuple(item.path for item in applied.files),
            branch=kwargs["expected_branch"],
            committed_at=1_785_600_001.0,
        )
        self._committed = receipt
        self._committed_by_id[receipt.commit_id] = receipt
        self.commit_receipts.append(receipt)
        self.current_head = receipt.commit_sha
        self.current_state = "available"
        if self.fail_commit_after_effect:
            self.fail_commit_after_effect = False
            raise ProjectWriterClientError(
                "result lost",
                code="operation_result_unknown",
            )
        return receipt

    async def reconcile_commit(
        self,
        **kwargs: Any,
    ) -> tuple[str, ApplyReceipt, CommitReceipt | None]:
        self.reconcile_commit_calls += 1
        self.reconcile_commit_requests.append(dict(kwargs))
        applied = kwargs["apply_receipt"]
        committed = self._committed_by_id.get(kwargs["commit_operation_id"])
        if committed is None:
            return "not_committed", applied, None
        self.current_head = committed.commit_sha
        self.current_state = "available"
        return "committed", applied, committed


class FakeHostSource:
    def __init__(self) -> None:
        self.imports: list[dict[str, Any]] = []
        self.released: list[tuple[str, str]] = []

    async def health(self) -> dict[str, Any]:
        return {"configured": True, "available": True, "host_imports": True}

    async def list_projects(self) -> list[dict[str, Any]]:
        return []

    async def import_uploaded(self, **kwargs: Any) -> dict[str, Any]:
        self.imports.append(kwargs)
        return _lease()

    async def release(self, project_id: str, lease_id: str) -> bool:
        self.released.append((project_id, lease_id))
        return True


class HostWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__(mode="draft", snapshot_fingerprint=PROJECT_FINGERPRINT)
        self.source: dict[str, Any] | None = None

    async def create_session(self, source: dict[str, Any] | None = None) -> dict[str, Any]:
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


def _service(
    store: CodingRecoveryStore | None = None,
    *,
    host: FakeHostRuntime | None = None,
) -> tuple[CodingService, HostWorker, FakeHostSource, FakeHostRuntime]:
    worker = HostWorker()
    source = FakeHostSource()
    host = host or FakeHostRuntime()
    service = CodingService(
        enabled=True,
        worker=worker,
        project_source=source,
        project_host=host,  # type: ignore[arg-type]
        projects_enabled=True,
        project_host_enabled=True,
        recovery_store=store,
        recovery_enabled=store is not None,
        mode="draft",
    )
    return service, worker, source, host


def _client(service: CodingService) -> httpx.AsyncClient:
    configure_coding_service(service)
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def _complete_host_cycle(
    client: httpx.AsyncClient,
    worker: HostWorker,
    session_id: str,
    *,
    revision: int,
    path: str,
    message: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    worker.revision = revision
    worker.change_path = path
    worker.current_empty = False
    applied = await client.post(
        f"/api/coding/sessions/{session_id}/apply",
        json={"revision": revision, "confirm_quality_risks": True},
    )
    assert applied.status_code == 200, applied.text
    committed = await client.post(
        f"/api/coding/sessions/{session_id}/commit",
        json={
            "revision": revision,
            "apply_id": applied.json()["apply_id"],
            "message": message,
        },
    )
    assert committed.status_code == 200, committed.text
    continued = await client.post(
        f"/api/coding/sessions/{session_id}/continue",
        json={
            "revision": revision,
            "commit_id": committed.json()["commit_id"],
        },
    )
    assert continued.status_code == 200, continued.text
    return applied.json(), committed.json()


@pytest.mark.asyncio
async def test_host_project_catalog_and_session_use_one_path_free_snapshot() -> None:
    service, worker, source, host = _service()
    async with _client(service) as client:
        catalog = await client.get("/api/coding/projects")
        created = await client.post(
            "/api/coding/sessions",
            json={"project_id": PROJECT_ID},
        )

    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["projects"]] == [
        "modelmirror",
        PROJECT_ID,
    ]
    assert created.status_code == 201
    assert created.json()["project"]["kind"] == "host_git"
    assert worker.source == _lease()
    assert host.snapshot_calls == [(PROJECT_ID, None, None, None)]
    assert host.finished == ["1" * 32]
    assert source.imports[0]["project_id"] == PROJECT_ID
    encoded = json.dumps(created.json(), ensure_ascii=False).casefold()
    assert "c:\\" not in encoded
    assert "remote" not in encoded
    await service.shutdown()
    assert source.released == [(PROJECT_ID, "lease_host_k8r3_202608")]
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_completed_worker_patch_enters_existing_host_recovery_chain(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "worker-handoff-recovery")
    service, worker, source, host = _service(store)
    patch = worker._diff_content()

    record = await service.adopt_worker_patch(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        patch=patch,
        paths=[worker.change_path],
    )

    assert record.project["kind"] == "host_git"
    assert record.project_source == _lease()
    assert worker.restore_calls == [
        {
            "revision": 1,
            "patch": patch,
            "paths": [worker.change_path],
            "snapshot_fingerprint": PROJECT_FINGERPRINT,
            "verification": None,
            "source": _lease(),
            "writeback_only": True,
        }
    ]
    recovery = store.load()
    assert recovery is not None
    assert recovery.payload.patch == patch
    context = store.load_project_context(recovery.recovery_id)
    assert context is not None
    assert context.project_id == PROJECT_ID
    assert source.released == []
    assert host.snapshot_calls == [(PROJECT_ID, PROJECT_HEAD, None, None)]


@pytest.mark.asyncio
async def test_worker_handoff_recovery_stays_available_without_legacy_model(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "worker-handoff-restart")
    host = FakeHostRuntime(writeback=True)
    first, first_worker, _source, _host = _service(store, host=host)
    first_worker.configured = False
    patch = first_worker._diff_content()

    await first.adopt_worker_patch(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        patch=patch,
        paths=[first_worker.change_path],
    )
    await first.shutdown()

    second, second_worker, _second_source, _second_host = _service(
        store,
        host=host,
    )
    second_worker.configured = False
    pending = await second.recovery_status()
    resumed = await second.resume_recovery()

    assert pending["can_resume"] is True
    assert pending.get("reason") is None
    assert resumed.project["id"] == PROJECT_ID
    assert second_worker.restore_calls[0]["writeback_only"] is True
    await second.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_worker_handoff_rejects_binary_patch_before_host_snapshot(
    tmp_path: Path,
) -> None:
    service, worker, _source, host = _service(
        CodingRecoveryStore(tmp_path / "binary-handoff-recovery")
    )
    patch = (
        "diff --git a/image.png b/image.png\n"
        "Binary files a/image.png and b/image.png differ\n"
    )

    with pytest.raises(HTTPException) as caught:
        await service.adopt_worker_patch(
            project_id=PROJECT_ID,
            expected_head=PROJECT_HEAD,
            patch=patch,
            paths=["image.png"],
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "worker_writeback_patch_unsupported"
    assert worker.restore_calls == []
    assert host.snapshot_calls == []


@pytest.mark.asyncio
async def test_completed_worker_task_handoff_route_uses_v13_recovery(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "worker-route-recovery")
    service, worker, _source, _host = _service(
        store,
        host=FakeHostRuntime(writeback=True),
    )
    # A Host Snapshot handoff only needs the path-free draft runtime transport.
    # The legacy builtin model/source can remain unconfigured while Project Host
    # v2 is independently online and writable.
    worker.configured = False
    task_id = "task_" + "a" * 32
    task = SimpleNamespace(
        state=TaskState.COMPLETED,
        workspace_id="workspace_" + "b" * 32,
        spec=SimpleNamespace(
            workspace_source=SimpleNamespace(
                kind="host_snapshot",
                source_id=PROJECT_ID,
                revision=PROJECT_HEAD,
            )
        ),
    )
    git_patch = worker._diff_content().replace(
        "--- a/",
        "index 1111111..2222222 100644\n--- a/",
        1,
    )
    worker_service = SimpleNamespace(
        store=SimpleNamespace(get_task=lambda value: task if value == task_id else None),
        harness_runner=SimpleNamespace(acceptance_satisfied=lambda value: value == task_id),
        workspace_broker=SimpleNamespace(
            diff=lambda value, *, detect_renames: (
                git_patch.encode("utf-8")
                if value == task.workspace_id and detect_renames is False
                else pytest.fail("handoff did not request a no-renames diff")
            )
        ),
    )
    configure_coding_worker_for_tests(worker_service, enabled=True)  # type: ignore[arg-type]
    try:
        async with _client(service) as client:
            response = await client.post(f"/api/coding/worker-tasks/{task_id}/handoff")
    finally:
        configure_coding_worker_for_tests(None, enabled=None)
        configure_coding_service(None)

    assert response.status_code == 201, response.text
    assert response.json()["task_id"] == task_id
    assert response.json()["project"]["id"] == PROJECT_ID
    assert response.json()["revision"] == 1
    recovery = store.load()
    assert recovery is not None
    assert recovery.payload.patch == worker._diff_content()


def test_worker_handoff_restores_added_empty_file_without_git_object_ids(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    workspace = DraftWorkspace(
        source,
        tmp_path / "workspace",
        tmp_path / "checkpoint",
    )
    workspace.initialize()
    git_patch = (
        "diff --git a/formatters/__init__.py b/formatters/__init__.py\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
    )

    normalized = _normalize_worker_handoff_diff(git_patch)
    report = workspace.restore_from_patch(
        normalized,
        revision=1,
        expected_paths=("formatters/__init__.py",),
    )

    assert normalized == (
        "diff --git a/formatters/__init__.py b/formatters/__init__.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/formatters/__init__.py\n"
    )
    assert report.files[0].status == "added"
    assert (workspace.workspace_root / "formatters" / "__init__.py").read_bytes() == b""


@pytest.mark.asyncio
async def test_host_commit_unknown_result_reconciles_before_retry(
    tmp_path: Path,
) -> None:
    host = WritebackHostRuntime()
    service, _worker, _source, _ = _service(
        CodingRecoveryStore(tmp_path / "writeback-recovery"),
        host=host,
    )
    async with _client(service) as client:
        created = await client.post(
            "/api/coding/sessions",
            json={"project_id": PROJECT_ID},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1, "confirm_quality_risks": True},
        )
        apply_id = applied.json()["apply_id"]
        host.fail_commit_after_effect = True
        first_commit = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": apply_id,
                "message": "feature: update host project",
            },
        )
        retried = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": apply_id,
                "message": "feature: update host project",
            },
        )

    assert created.status_code == 201
    assert applied.status_code == 200
    assert first_commit.status_code == 503
    assert first_commit.json()["detail"]["code"] == "operation_result_unknown"
    assert retried.status_code == 200
    assert retried.json()["state"] == "committed"
    assert host.commit_calls == 1
    assert host.reconcile_commit_calls == 1
    assert host.recovery_bindings[-1][1] == retried.json()["commit_id"]
    await service.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_host_project_two_cycles_advance_parent_but_keep_source_baseline(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "two-cycle-recovery")
    host = WritebackHostRuntime()
    service, worker, _source, _ = _service(store, host=host)
    service.incremental_enabled = True
    async with _client(service) as client:
        created = await client.post(
            "/api/coding/sessions",
            json={"project_id": PROJECT_ID},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        await _complete_host_cycle(
            client,
            worker,
            session_id,
            revision=1,
            path="server/round_one.py",
            message="feature: host round one",
        )
        first_head = host.commit_receipts[0].commit_sha
        await _complete_host_cycle(
            client,
            worker,
            session_id,
            revision=2,
            path="server/round_two.py",
            message="feature: host round two",
        )
        history = await client.get(
            f"/api/coding/sessions/{session_id}/history"
        )

    assert history.status_code == 200
    assert history.json()["active_cycle"] == 3
    assert history.json()["completed_count"] == 2
    assert [item["expected_head"] for item in host.apply_requests] == [
        PROJECT_HEAD,
        first_head,
    ]
    assert [item["expected_head"] for item in host.commit_requests] == [
        PROJECT_HEAD,
        first_head,
    ]
    active = service._sessions[session_id]
    assert active.project_source is not None
    assert active.project_source["head"] == PROJECT_HEAD
    persisted = store.load()
    assert persisted is not None
    assert len(persisted.payload.cycles) == 2
    context = store.load_project_context(persisted.recovery_id)
    assert context is not None
    assert context.head == PROJECT_HEAD
    assert context.branch == PROJECT_BRANCH
    await service.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_completed_host_cycles_recover_by_reconciling_exact_last_cycle(
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "completed-cycle-recovery")
    host = WritebackHostRuntime()
    first, first_worker, _source, _ = _service(store, host=host)
    first.incremental_enabled = True
    async with _client(first) as client:
        created = await client.post(
            "/api/coding/sessions",
            json={"project_id": PROJECT_ID},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        first_apply, _first_commit = await _complete_host_cycle(
            client,
            first_worker,
            session_id,
            revision=1,
            path="server/recovery_round_one.py",
            message="feature: recovery round one",
        )
        second_apply, _second_commit = await _complete_host_cycle(
            client,
            first_worker,
            session_id,
            revision=2,
            path="server/recovery_round_two.py",
            message="feature: recovery round two",
        )
    first_head = host.commit_receipts[0].commit_sha
    last_commit = host.commit_receipts[1]
    await first.shutdown()

    second, second_worker, _second_source, _ = _service(store, host=host)
    second.incremental_enabled = True
    pending = await second.recovery_status()
    resumed = await second.resume_recovery()

    assert pending["can_resume"] is True
    assert resumed.recovery_conflict is None
    assert resumed.apply_operation_id is None
    assert resumed.commit_operation_id is None
    reconciled = host.reconcile_commit_requests[-1]
    assert reconciled["expected_head"] == first_head
    assert reconciled["operation_id"] == second_apply["apply_id"]
    assert reconciled["commit_operation_id"] == last_commit.commit_id
    assert reconciled["revision"] == 2
    assert reconciled["paths"] == ["server/recovery_round_two.py"]
    assert "server/recovery_round_two.py" in reconciled["patch"]
    assert "server/recovery_round_one.py" not in reconciled["patch"]
    assert host.snapshot_calls[-1] == (
        PROJECT_ID,
        PROJECT_HEAD,
        PROJECT_BRANCH,
        first_apply["apply_id"],
    )
    assert second_worker.restore_calls[0]["source"]["head"] == PROJECT_HEAD
    await second.shutdown()
    configure_coding_service(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["path", "parent"])
async def test_host_cycle_lineage_tampering_blocks_next_apply(
    tmp_path: Path,
    tamper: str,
) -> None:
    store = CodingRecoveryStore(tmp_path / f"lineage-{tamper}")
    host = WritebackHostRuntime()
    service, worker, _source, _ = _service(store, host=host)
    service.incremental_enabled = True
    async with _client(service) as client:
        created = await client.post(
            "/api/coding/sessions",
            json={"project_id": PROJECT_ID},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        await _complete_host_cycle(
            client,
            worker,
            session_id,
            revision=1,
            path="server/lineage_round_one.py",
            message="feature: lineage round one",
        )
        cycle = service._sessions[session_id].cycle_history.cycles[0]
        if tamper == "path":
            cycle.changes["files"][0]["path"] = "server/tampered.py"
        else:
            cycle.commit["receipt"]["parent_sha"] = "9" * 40
        worker.revision = 2
        worker.change_path = "server/lineage_round_two.py"
        worker.current_empty = False
        blocked = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 2, "confirm_quality_risks": True},
        )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "cycle_lineage_invalid"
    assert host.apply_calls == 1
    await service.shutdown()
    configure_coding_service(None)


def test_host_project_recovery_context_binds_branch_and_head() -> None:
    context = RecoveryProjectContext(
        recovery_id="recovery_host_k8r3_202608",
        project_id=PROJECT_ID,
        kind=ProjectKind.HOST_GIT,
        name="星云 k8r3",
        head=PROJECT_HEAD,
        branch="feature/nebula-k8r3",
    )

    restored = RecoveryProjectContext.from_dict(context.to_dict())

    assert restored == context
    assert restored.to_public()["features"]["verification"] is True
    with pytest.raises(ValueError):
        RecoveryProjectContext(
            recovery_id="recovery_host_k8r3_202608",
            project_id=PROJECT_ID,
            kind=ProjectKind.HOST_GIT,
            name="星云 k8r3",
            head=PROJECT_HEAD,
            branch="",
        )


@pytest.mark.asyncio
async def test_host_project_recovery_requests_fresh_matching_snapshot(tmp_path: Path) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery")
    first, _worker, _source, _host = _service(store)
    record = await first.create_session(PROJECT_ID)
    assert await first._persist_recovery(record, required=True) is True
    recovery_id = record.recovery_id
    assert recovery_id is not None
    context = store.load_project_context(recovery_id)
    assert context is not None
    assert context.branch == "feature/nebula-k8r3"
    await first.shutdown()

    second, second_worker, _second_source, second_host = _service(store)
    pending = await second.recovery_status()
    resumed = await second.resume_recovery()

    assert pending["can_resume"] is True
    assert pending["project"]["id"] == PROJECT_ID
    assert resumed.project["kind"] == "host_git"
    assert second_host.snapshot_calls[-1] == (
        PROJECT_ID,
        PROJECT_HEAD,
        "feature/nebula-k8r3",
        None,
    )
    assert second_worker.restore_calls[0]["source"]["kind"] == "host_git"
    await second.shutdown()
    configure_coding_service(None)


def test_runtime_accepts_host_lease_but_rejects_kind_id_mismatch(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    slot = tmp_path / "slot" / "current"
    workspace = slot / "workspace"
    builtin.mkdir()
    workspace.mkdir(parents=True)
    (builtin / "README.md").write_text("builtin\n", encoding="utf-8")
    (workspace / "README.md").write_bytes(b"nebula-k8r3\n")
    source = _lease()
    source["fingerprint"] = snapshot_fingerprint(workspace)
    source["total_bytes"] = len(b"nebula-k8r3\n")
    source["file_count"] = 1
    (slot / "lease.json").write_text(
        json.dumps({key: value for key, value in source.items() if key != "kind"}),
        encoding="utf-8",
    )
    server = CodingWorkerServer(
        tmp_path / "worker.sock",
        source_snapshot_path=builtin,
        project_snapshot_path=slot,
        workspace_path=tmp_path / "workspace",
        checkpoint_path=tmp_path / "checkpoint",
    )

    resolved = server._resolve_workspace_source(source)

    assert resolved.kind is ProjectKind.HOST_GIT
    assert resolved.to_public_dict()["id"] == PROJECT_ID
    wrong = dict(source)
    wrong["kind"] = ProjectKind.LOCAL_CLONE.value
    with pytest.raises(CodingWorkerError) as rejected:
        server._resolve_workspace_source(wrong)
    assert rejected.value.code == "snapshot_mismatch"


@pytest.mark.asyncio
async def test_transfer_requires_host_token_and_exact_length(tmp_path: Path) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    _pairing, code = store.create_pairing("本地项目助手")
    host, token = store.consume_pairing(
        code,
        device_id="pdev_0123456789abcdef0123456789abcdef",
        version="1.0.0",
        platform="windows",
    )
    runtime = ProjectHostRuntime(store, tmp_path / "uploads")
    transfer_id = "3" * 32
    runtime._transfers[transfer_id] = _Transfer(
        transfer_id=transfer_id,
        request_id="phreq_" + "4" * 32,
        host_id=host.host_id,
        project_id=PROJECT_ID,
        status="awaiting_upload",
        created_at=time.time(),
    )

    async def body() -> AsyncIterator[bytes]:
        yield b"random-"
        yield b"archive"

    with pytest.raises(ProjectHostError) as denied:
        await runtime.receive_transfer(
            host_id=host.host_id,
            token="wrong",
            transfer_id=transfer_id,
            content_length=14,
            body=body(),
        )
    assert denied.value.code == "project_host_authentication_failed"

    received = await runtime.receive_transfer(
        host_id=host.host_id,
        token=token,
        transfer_id=transfer_id,
        content_length=14,
        body=body(),
    )
    assert received == {"received": True, "size": 14}
    assert (tmp_path / "uploads" / f"{transfer_id}.tar.gz").read_bytes() == b"random-archive"
    runtime.finish_transfer(transfer_id)
    assert not (tmp_path / "uploads" / f"{transfer_id}.tar.gz").exists()


@pytest.mark.asyncio
async def test_operation_payload_is_bound_single_use_and_supports_large_patch(
    tmp_path: Path,
) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    _pairing, code = store.create_pairing("local helper")
    host, token = store.consume_pairing(
        code,
        device_id="pdev_0123456789abcdef0123456789abcdef",
        version="1.1.0",
        platform="windows",
        protocol=PROJECT_HOST_PROTOCOL_V2,
    )
    runtime = ProjectHostRuntime(
        store,
        tmp_path / "uploads",
        writeback_enabled=True,
    )
    payload_id = "phop_" + "1" * 32
    operation_id = "apply_0123456789abcdef012345"
    body = b"x" * (900 * 1024)
    runtime._operation_payloads[payload_id] = _OperationPayload(
        payload_id=payload_id,
        host_id=host.host_id,
        project_id=PROJECT_ID,
        operation_id=operation_id,
        action="apply",
        created_at=time.time(),
        expires_at=time.time() + 90,
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
        body=body,
    )

    for overrides in (
        {"token": "wrong-token"},
        {"project_id": "hostgit_" + "f" * 32},
        {"operation_id": "apply_abcdef0123456789abcdef"},
        {"action": "commit"},
    ):
        arguments = {
            "payload_id": payload_id,
            "host_id": host.host_id,
            "token": token,
            "project_id": PROJECT_ID,
            "operation_id": operation_id,
            "action": "apply",
            **overrides,
        }
        with pytest.raises(ProjectHostError):
            await runtime.consume_operation_payload(**arguments)
        assert runtime._operation_payloads[payload_id].body == body

    results = await asyncio.gather(
        *(
            runtime.consume_operation_payload(
                payload_id=payload_id,
                host_id=host.host_id,
                token=token,
                project_id=PROJECT_ID,
                operation_id=operation_id,
                action="apply",
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(result == body for result in results) == 1
    errors = [result for result in results if isinstance(result, ProjectHostError)]
    assert [error.code for error in errors] == ["operation_payload_consumed"]

    expired_id = "phop_" + "2" * 32
    runtime._operation_payloads[expired_id] = _OperationPayload(
        payload_id=expired_id,
        host_id=host.host_id,
        project_id=PROJECT_ID,
        operation_id=operation_id,
        action="apply",
        created_at=time.time() - 100,
        expires_at=time.time() - 1,
        sha256=hashlib.sha256(b"expired").hexdigest(),
        size=7,
        body=b"expired",
    )
    with pytest.raises(ProjectHostError) as expired:
        await runtime.consume_operation_payload(
            payload_id=expired_id,
            host_id=host.host_id,
            token=token,
            project_id=PROJECT_ID,
            operation_id=operation_id,
            action="apply",
        )
    assert expired.value.code == "operation_payload_unavailable"

    route_id = "phop_" + "3" * 32
    route_body = b'{"safe":true}'
    runtime._operation_payloads[route_id] = _OperationPayload(
        payload_id=route_id,
        host_id=host.host_id,
        project_id=PROJECT_ID,
        operation_id=operation_id,
        action="apply",
        created_at=time.time(),
        expires_at=time.time() + 90,
        sha256=hashlib.sha256(route_body).hexdigest(),
        size=len(route_body),
        body=route_body,
    )
    service = CodingService(
        enabled=False,
        worker=HostWorker(),
        project_host=runtime,
        mode="draft",
    )
    async with _client(service) as client:
        downloaded = await client.get(
            f"/api/coding/project-host/operations/{route_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-ModelMirror-Project-Host-Id": host.host_id,
                "X-ModelMirror-Project-Id": PROJECT_ID,
                "X-ModelMirror-Operation-Id": operation_id,
                "X-ModelMirror-Operation-Action": "apply",
            },
        )
    assert downloaded.status_code == 200
    assert downloaded.content == route_body
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["pragma"] == "no-cache"
    configure_coding_service(None)


@pytest.mark.asyncio
async def test_late_operation_result_is_ignored_and_malformed_receipt_is_unknown(
    tmp_path: Path,
) -> None:
    runtime = ProjectHostRuntime(
        ProjectHostStore(tmp_path / "state.json"),
        tmp_path / "uploads",
        writeback_enabled=True,
    )
    request_id = "phreq_" + "a" * 32
    runtime._request_tombstones[request_id] = time.time() + 60
    await runtime._incoming(
        "phost_" + "b" * 32,
        "phconn_" + "c" * 32,
        {
            "type": "operation_result",
            "request_id": request_id,
            "project_id": PROJECT_ID,
            "operation_id": "apply_0123456789abcdef012345",
            "action": "apply",
            "result": {},
        },
    )

    operation_id = "apply_0123456789abcdef012345"
    runtime._remember_managed_operation(
        project_id=PROJECT_ID,
        operation_id=operation_id,
        kind="apply",
        branch=PROJECT_BRANCH,
        expected_head=PROJECT_HEAD,
    )

    async def malformed(**_: Any) -> dict[str, Any]:
        return {"state": "applied", "receipt": {"apply_id": operation_id}}

    runtime._execute_operation = malformed  # type: ignore[method-assign]
    with pytest.raises(ProjectWriterClientError) as unknown:
        await runtime.apply(
            project_id=PROJECT_ID,
            expected_head=PROJECT_HEAD,
            expected_branch=PROJECT_BRANCH,
            operation_id=operation_id,
            revision=3,
            patch="diff --git a/src/nebula.py b/src/nebula.py\n",
            paths=["src/nebula.py"],
            expected_fingerprint=PROJECT_FINGERPRINT,
        )
    assert unknown.value.code == "operation_result_unknown"
    assert (PROJECT_ID, operation_id, "apply") in runtime._uncertain_operations


@pytest.mark.asyncio
async def test_host_runtime_binds_direct_commit_receipt(tmp_path: Path) -> None:
    runtime = _registered_runtime(tmp_path)
    applied = _apply_receipt()
    committed = _commit_receipt(applied)
    runtime._remember_managed_operation(
        project_id=PROJECT_ID,
        operation_id=committed.commit_id,
        kind="commit",
        branch=PROJECT_BRANCH,
        expected_head=PROJECT_HEAD,
    )

    async def execute(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["expected_head"] == PROJECT_HEAD
        assert kwargs["expected_branch"] == PROJECT_BRANCH
        assert kwargs["managed_operation_id"] == applied.apply_id
        return {"state": "committed", "receipt": _commit_receipt_to_payload(committed)}

    runtime._execute_operation = execute  # type: ignore[method-assign]
    restored = await runtime.commit(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=committed.commit_id,
        apply_receipt=applied,
        message=committed.message,
    )

    assert restored == committed
    assert runtime._managed_operations[(PROJECT_ID, committed.commit_id)].branch == PROJECT_BRANCH
    project = runtime.store.require_project(PROJECT_ID)
    assert (project.head, project.state, project.reason) == (
        committed.commit_sha,
        "available",
        None,
    )


@pytest.mark.asyncio
async def test_host_runtime_catalog_tracks_apply_commit_undo_revert(
    tmp_path: Path,
) -> None:
    runtime = _registered_runtime(tmp_path)
    applied = _apply_receipt()
    committed = _commit_receipt(applied)
    responses = [
        {"state": "applied", "receipt": _receipt_to_payload(applied)},
        {"state": "committed", "receipt": _commit_receipt_to_payload(committed)},
        {"state": "undone", "receipt": _commit_receipt_to_payload(committed)},
        {"state": "reverted", "receipt": _receipt_to_payload(applied)},
    ]
    actions: list[str] = []

    async def execute(**kwargs: Any) -> dict[str, Any]:
        actions.append(kwargs["action"])
        return responses.pop(0)

    runtime._execute_operation = execute  # type: ignore[method-assign]
    runtime.bind_persisted_intent(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=applied.apply_id,
        kind="apply",
    )
    restored_apply = await runtime.apply(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=applied.apply_id,
        revision=applied.revision,
        patch="diff --git a/src/nebula.py b/src/nebula.py\n",
        paths=["src/nebula.py"],
        expected_fingerprint=PROJECT_FINGERPRINT,
    )
    dirty_after_apply = runtime.store.require_project(PROJECT_ID)
    assert (dirty_after_apply.head, dirty_after_apply.state, dirty_after_apply.reason) == (
        PROJECT_HEAD,
        "unavailable",
        "git_repository_dirty",
    )

    runtime.bind_persisted_intent(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=committed.commit_id,
        kind="commit",
        parent_operation_id=applied.apply_id,
    )
    restored_commit = await runtime.commit(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=committed.commit_id,
        apply_receipt=restored_apply,
        message=committed.message,
    )
    clean_after_commit = runtime.store.require_project(PROJECT_ID)
    assert (clean_after_commit.head, clean_after_commit.state, clean_after_commit.reason) == (
        committed.commit_sha,
        "available",
        None,
    )

    await runtime.undo(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        apply_receipt=restored_apply,
        commit_receipt=restored_commit,
    )
    dirty_after_undo = runtime.store.require_project(PROJECT_ID)
    assert (dirty_after_undo.head, dirty_after_undo.state, dirty_after_undo.reason) == (
        PROJECT_HEAD,
        "unavailable",
        "git_repository_dirty",
    )

    await runtime.revert(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        receipt=restored_apply,
    )
    clean_after_revert = runtime.store.require_project(PROJECT_ID)
    assert (clean_after_revert.head, clean_after_revert.state, clean_after_revert.reason) == (
        PROJECT_HEAD,
        "available",
        None,
    )
    assert actions == ["apply", "commit", "undo", "revert"]
    assert responses == []


@pytest.mark.asyncio
async def test_host_runtime_binds_apply_and_commit_reconcile_receipts(
    tmp_path: Path,
) -> None:
    runtime = _registered_runtime(tmp_path)
    applied = _apply_receipt()
    committed = _commit_receipt(applied)
    responses = [
        {"state": "applied", "receipt": _receipt_to_payload(applied)},
        {
            "state": "committed",
            "apply_receipt": _receipt_to_payload(applied),
            "commit_receipt": _commit_receipt_to_payload(committed),
        },
    ]

    async def execute(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["expected_head"] == PROJECT_HEAD
        assert kwargs["expected_branch"] == PROJECT_BRANCH
        return responses.pop(0)

    runtime._execute_operation = execute  # type: ignore[method-assign]
    apply_state, restored_apply = await runtime.reconcile_apply(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=applied.apply_id,
        revision=applied.revision,
        patch="diff --git a/src/nebula.py b/src/nebula.py\n",
        paths=["src/nebula.py"],
        expected_fingerprint=PROJECT_FINGERPRINT,
    )
    applied_project = runtime.store.require_project(PROJECT_ID)
    assert (applied_project.head, applied_project.state, applied_project.reason) == (
        PROJECT_HEAD,
        "unavailable",
        "git_repository_dirty",
    )
    commit_state, reconciled_apply, restored_commit = await runtime.reconcile_commit(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=applied.apply_id,
        revision=applied.revision,
        patch="diff --git a/src/nebula.py b/src/nebula.py\n",
        paths=["src/nebula.py"],
        expected_fingerprint=PROJECT_FINGERPRINT,
        apply_receipt=applied,
        commit_operation_id=committed.commit_id,
        message=committed.message,
    )

    assert (apply_state, restored_apply) == ("applied", applied)
    assert (commit_state, reconciled_apply, restored_commit) == (
        "committed",
        applied,
        committed,
    )
    committed_project = runtime.store.require_project(PROJECT_ID)
    assert (committed_project.head, committed_project.state, committed_project.reason) == (
        committed.commit_sha,
        "available",
        None,
    )
    assert responses == []


def test_host_runtime_binds_commit_intent_after_managed_apply_is_dirty(
    tmp_path: Path,
) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    _pairing, code = store.create_pairing("local helper")
    host, _token = store.consume_pairing(
        code,
        device_id="pdev_0123456789abcdef0123456789abcdef",
        version="1.1.0",
        platform="windows",
        protocol=PROJECT_HOST_PROTOCOL_V2,
    )
    store.register_project(
        host.host_id,
        {
            "project_id": PROJECT_ID,
            "name": "nebula",
            "branch": PROJECT_BRANCH,
            "head": PROJECT_HEAD,
            "state": "unavailable",
            "reason": "git_repository_dirty",
        },
    )
    runtime = ProjectHostRuntime(
        store,
        tmp_path / "uploads",
        writeback_enabled=True,
    )
    applied = _apply_receipt()
    commit_operation_id = "commit_dirty_0123456789abcdef"
    runtime.bind_recovery_operations(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        apply_operation_id=applied.apply_id,
        apply_receipt=applied,
        commit_receipt=None,
    )

    runtime.bind_persisted_intent(
        project_id=PROJECT_ID,
        expected_head=PROJECT_HEAD,
        expected_branch=PROJECT_BRANCH,
        operation_id=commit_operation_id,
        kind="commit",
        parent_operation_id=applied.apply_id,
    )

    assert runtime._managed_operations[(PROJECT_ID, commit_operation_id)].kind == "commit"
