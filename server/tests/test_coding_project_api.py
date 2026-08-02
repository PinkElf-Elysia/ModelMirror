from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from server.coding_runtime.api import CodingService, configure_coding_service, router
from server.coding_runtime.project_source_client import (
    ProjectSourceClientError,
    _validate_public_project,
)
from server.coding_runtime.projects import ProjectFeatures, ProjectKind
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
) -> CodingService:
    return CodingService(
        enabled=True,
        worker=worker,
        project_source=source,
        projects_enabled=True,
        recovery_store=store,
        recovery_enabled=store is not None,
        mode="draft",
    )


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
        responses = [
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
                f"/api/coding/sessions/{session_id}/commit",
                params={"revision": 1},
            ),
            await client.get(
                f"/api/coding/sessions/{session_id}/publish",
                params={"revision": 1},
            ),
        ]
        started = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "只改说明文字，随机标记 q9-731"},
        )

    assert all(response.status_code == 409 for response in responses)
    assert {
        response.json()["detail"]["code"] for response in responses
    } == {"project_operation_unavailable"}
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
