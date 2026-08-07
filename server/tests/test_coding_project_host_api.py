from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from server.coding_runtime.api import (
    CodingService,
    configure_coding_service,
    router,
)
from server.coding_runtime.patch_policy import snapshot_fingerprint
from server.coding_runtime.project_host import ProjectHostError, ProjectHostStore
from server.coding_runtime.project_host_api import ProjectHostRuntime, _Transfer
from server.coding_runtime.projects import ProjectFeatures, ProjectKind
from server.coding_runtime.recovery import CodingRecoveryStore, RecoveryProjectContext
from server.coding_runtime.worker import CodingWorkerError, CodingWorkerServer
from server.tests.test_coding_runtime_api import FakeWorker


PROJECT_ID = "hostgit_0123456789abcdef0123456789abcdef"
PROJECT_HEAD = "a" * 40
PROJECT_FINGERPRINT = "b" * 64


def _public_project() -> dict[str, Any]:
    return {
        "id": PROJECT_ID,
        "name": "星云 k8r3",
        "kind": "host_git",
        "state": "available",
        "reason": None,
        "branch": "feature/nebula-k8r3",
        "head": PROJECT_HEAD[:12],
        "features": ProjectFeatures.host_git().to_dict(),
        "writeback_reason": None,
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


class FakeHostRuntime:
    def __init__(self) -> None:
        self.snapshot_calls: list[tuple[str, str | None, str | None]] = []
        self.finished: list[str] = []

    def capability(self, **_: Any) -> dict[str, Any]:
        return {
            "enabled": True,
            "paired": True,
            "available": True,
            "platform": "windows",
            "selection": True,
            "remembers_projects": True,
            "direct_writeback": False,
        }

    def list_projects(self) -> list[dict[str, Any]]:
        return [_public_project()]

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
    ) -> dict[str, Any]:
        self.snapshot_calls.append((project_id, expected_head, expected_branch))
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
) -> tuple[CodingService, HostWorker, FakeHostSource, FakeHostRuntime]:
    worker = HostWorker()
    source = FakeHostSource()
    host = FakeHostRuntime()
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
    assert host.snapshot_calls == [(PROJECT_ID, None, None)]
    assert host.finished == ["1" * 32]
    assert source.imports[0]["project_id"] == PROJECT_ID
    encoded = json.dumps(created.json(), ensure_ascii=False).casefold()
    assert "c:\\" not in encoded
    assert "remote" not in encoded
    await service.shutdown()
    assert source.released == [(PROJECT_ID, "lease_host_k8r3_202608")]
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
    (workspace / "README.md").write_text("nebula-k8r3\n", encoding="utf-8")
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
