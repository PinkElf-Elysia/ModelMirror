from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from server.coding_runtime.api import (
    CodingService,
    configure_coding_service,
    router,
)
from server.coding_runtime.applier_client import ApplierClientError
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.models import CodingEvent, CodingEventKind
from server.coding_runtime.worker import CodingWorkerError


SNAPSHOT_FINGERPRINT = "a" * 64


class FakeWorker:
    def __init__(
        self,
        *,
        configured: bool = True,
        block_turn: bool = False,
        fail_health: bool = False,
        mode: str = "readonly",
        validation_passed: bool = True,
        malformed_changes: bool = False,
        verification_available: bool = True,
        change_path: str = "server/example.py",
    ) -> None:
        self.configured = configured
        self.block_turn = block_turn
        self.fail_health = fail_health
        self.mode = mode
        self.validation_passed = validation_passed
        self.malformed_changes = malformed_changes
        self.verification_available = verification_available
        self.change_path = change_path
        self.release = asyncio.Event()
        self.cancelled = False
        self.closed: list[str] = []
        self.session_id = "coding-session"
        self.revision = 1
        self.verification_revision = 1
        self.verification_state = "not_started"
        self.verification_result = "not_run"
        self.verification_reason: str | None = None

    async def health(self) -> dict[str, Any]:
        if self.fail_health:
            raise CodingWorkerError("unavailable", code="worker_unavailable")
        return {
            "ok": True,
            "configured": self.configured,
            "mode": self.mode,
            "snapshot_fingerprint": SNAPSHOT_FINGERPRINT,
            "verification": {
                "available": self.verification_available,
                "strategy": "adaptive",
                "required_for_patch": False,
                "max_duration_seconds": 600,
                **(
                    {}
                    if self.verification_available
                    else {"reason": "verifier_unavailable"}
                ),
            },
        }

    async def create_session(self) -> dict[str, Any]:
        return {
            "ok": True,
            "session_id": self.session_id,
            "mode": self.mode,
            "event": self._event(1, CodingEventKind.SESSION_STARTED).to_dict(),
        }

    async def prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]:
        yield self._event(2, CodingEventKind.TURN_STARTED, turn_id="turn-1")
        yield self._event(
            3,
            CodingEventKind.TOOL_STATUS,
            turn_id="turn-1",
            data={
                "tool_call_id": "read-1",
                "title": "Read C:\\private\\repo and /workspace/server/main.py",
                "kind": "read",
                "status": "completed",
                "raw": "must not cross API",
            },
        )
        if self.block_turn:
            await self.release.wait()
        if self.cancelled:
            yield self._event(4, CodingEventKind.CANCELLED, turn_id="turn-1")
            return
        yield self._event(
            4,
            CodingEventKind.ANSWER_DELTA,
            turn_id="turn-1",
            data={"text": f"Answer for {prompt}"},
        )
        yield self._event(
            5,
            CodingEventKind.TURN_COMPLETED,
            turn_id="turn-1",
            data={"stop_reason": "end_turn"},
        )

    async def cancel(self, session_id: str) -> bool:
        if self.cancelled:
            return False
        self.cancelled = True
        self.release.set()
        return True

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def changes(self, session_id: str) -> dict[str, Any]:
        return self._changes()

    async def diff(self, session_id: str, path: str, revision: int) -> str:
        self._require_revision(revision)
        if path != self.change_path:
            raise CodingWorkerError("missing", code="change_not_found")
        return (
            f"diff --git a/{self.change_path} b/{self.change_path}\n"
            f"--- a/{self.change_path}\n"
            f"+++ b/{self.change_path}\n"
            "@@ -1 +1 @@\n"
            "-before\n"
            "+after\n"
        )

    async def patch(self, session_id: str, revision: int) -> str:
        self._require_revision(revision)
        if not self.validation_passed:
            raise CodingWorkerError("blocked", code="validation_failed")
        return await self.diff(session_id, self.change_path, revision)

    async def validate(self, session_id: str) -> dict[str, Any]:
        return self._changes()

    async def discard(self, session_id: str) -> dict[str, Any]:
        self.revision += 1
        return self._changes(empty=True)

    async def verification_start(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        self._require_revision(revision)
        if not self.verification_available:
            raise CodingWorkerError(
                "unavailable",
                code="verifier_unavailable",
            )
        self.verification_revision = revision
        self.verification_state = "running"
        self.verification_result = "not_run"
        return {"verification": self._verification()}

    async def verification_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        if revision not in {self.revision, self.verification_revision}:
            raise CodingWorkerError("stale", code="stale_revision")
        return {"verification": self._verification()}

    async def verification_cancel(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        if revision != self.verification_revision:
            raise CodingWorkerError(
                "missing",
                code="verification_not_found",
            )
        self.verification_state = "cancelled"
        self.verification_result = "not_run"
        return {
            "accepted": True,
            "verification": self._verification(),
        }

    def _verification(self) -> dict[str, Any]:
        terminal = self.verification_state in {"completed", "cancelled"}
        details = (
            "C:\\private\\repo\\server\\main.py "
            + "sk-"
            + ("z" * 24)
            if self.verification_result == "failed"
            else ""
        )
        return {
            "revision": self.verification_revision,
            "state": self.verification_state,
            "result": self.verification_result,
            "stale": self.verification_revision != self.revision,
            "reason": self.verification_reason,
            "started_at": (
                time.time()
                if self.verification_state != "not_started"
                else None
            ),
            "finished_at": time.time() if terminal else None,
            "steps": [
                {
                    "id": "backend_tests",
                    "label": "检查服务代码",
                    "state": self.verification_state,
                    "result": self.verification_result,
                    "duration_ms": 20 if terminal else None,
                    "summary": (
                        "发现需要处理的问题"
                        if self.verification_result == "failed"
                        else ""
                    ),
                    "details": details,
                    "truncated": False,
                }
            ],
        }

    def _changes(self, *, empty: bool = False) -> dict[str, Any]:
        path = (
            "/workspace/private.py"
            if self.malformed_changes
            else self.change_path
        )
        files = (
            []
            if empty
            else [
                {
                    "path": path,
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                }
            ]
        )
        validation_status = (
            "passed" if self.validation_passed or empty else "failed"
        )
        return {
            "revision": self.revision,
            "files": files,
            "file_count": len(files),
            "additions": 0 if empty else 1,
            "deletions": 0 if empty else 1,
            "patch_bytes": 0 if empty else 150,
            "validation_status": validation_status,
            "can_download": bool(files) and validation_status == "passed",
            "checks": [
                {
                    "id": "python_syntax",
                    "label": "Python 文件结构",
                    "status": validation_status,
                    "message": (
                        "Python 文件结构正常"
                        if validation_status == "passed"
                        else "请检查：server/example.py:1"
                    ),
                }
            ],
        }

    def _require_revision(self, revision: int) -> None:
        if revision != self.revision:
            raise CodingWorkerError("stale", code="stale_revision")

    def _event(
        self,
        seq: int,
        kind: CodingEventKind,
        *,
        turn_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> CodingEvent:
        return CodingEvent(
            session_id=self.session_id,
            seq=seq,
            kind=kind,
            created_at=time.time(),
            turn_id=turn_id,
            data=dict(data or {}),
        )


class FakeApplier:
    def __init__(
        self,
        *,
        configured: bool = True,
        available: bool = True,
        fingerprint: str = SNAPSHOT_FINGERPRINT,
        apply_error: str | None = None,
        revert_error: str | None = None,
        block_apply: bool = False,
    ) -> None:
        self.configured = configured
        self.available = available
        self.fingerprint = fingerprint
        self.apply_error = apply_error
        self.revert_error = revert_error
        self.block_apply = block_apply
        self.apply_calls: list[dict[str, Any]] = []
        self.revert_calls: list[ApplyReceipt] = []
        self.apply_started = asyncio.Event()
        self.release_apply = asyncio.Event()

    async def health(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "available": self.available,
            "target": "dedicated_worktree",
            "snapshot_fingerprint": self.fingerprint,
            **({} if self.available else {"reason": "target_not_ready"}),
        }

    async def apply(self, **kwargs: Any) -> ApplyReceipt:
        self.apply_calls.append(kwargs)
        self.apply_started.set()
        if self.block_apply:
            await self.release_apply.wait()
        if self.apply_error is not None:
            raise ApplierClientError("apply failed", code=self.apply_error)
        return ApplyReceipt(
            apply_id=kwargs["operation_id"],
            revision=kwargs["revision"],
            snapshot_fingerprint=kwargs["expected_fingerprint"],
            files=(
                ApplyFileReceipt(
                    path=kwargs["paths"][0],
                    existed_before=True,
                    before_sha256="b" * 64,
                    after_sha256="c" * 64,
                ),
            ),
            applied_at=10.0,
        )

    async def revert(self, receipt: ApplyReceipt) -> ApplyReceipt:
        self.revert_calls.append(receipt)
        if self.revert_error is not None:
            raise ApplierClientError("revert failed", code=self.revert_error)
        return receipt


@pytest_asyncio.fixture
async def make_client():
    services: list[CodingService] = []

    async def factory(
        *,
        enabled: bool = True,
        worker: FakeWorker | None = None,
        applier: FakeApplier | None = None,
        ttl_seconds: float = 1800,
    ) -> tuple[httpx.AsyncClient, CodingService, FakeWorker]:
        fake = worker or FakeWorker()
        service = CodingService(
            enabled=enabled,
            worker=fake,
            applier=applier,
            ttl_seconds=ttl_seconds,
            mode=fake.mode,
        )
        services.append(service)
        configure_coding_service(service)
        app = FastAPI()
        app.include_router(router)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        return client, service, fake

    yield factory

    for service in services:
        await service.shutdown()
    configure_coding_service(None)


async def _create_and_start(client: httpx.AsyncClient, prompt: str = "Explain") -> str:
    created = await client.post("/api/coding/sessions")
    assert created.status_code == 201
    session_id = created.json()["id"]
    started = await client.post(
        f"/api/coding/sessions/{session_id}/turns",
        json={"prompt": prompt},
    )
    assert started.status_code == 202
    return session_id


def _sse_events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_disabled_state_is_explicit_and_core_router_stays_available(
    make_client,
) -> None:
    client, _, _ = await make_client(enabled=False)
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        create = await client.post("/api/coding/sessions")

    assert capabilities.json()["reason"] == "disabled"
    assert capabilities.json()["available"] is False
    assert create.status_code == 503
    assert create.json()["detail"]["code"] == "disabled"


@pytest.mark.asyncio
async def test_normal_turn_streams_canonical_sanitized_events(make_client) -> None:
    client, _, _ = await make_client()
    async with client:
        session_id = await _create_and_start(client)
        response = await client.get(f"/api/coding/sessions/{session_id}/events")

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event["type"] for event in events] == [
        "session_started",
        "turn_started",
        "tool_status",
        "answer_delta",
        "turn_completed",
    ]
    serialized = json.dumps(events, ensure_ascii=False)
    assert "C:\\private" not in serialized
    assert "/workspace" not in serialized
    assert "raw" not in events[2]["data"]
    assert response.headers["cache-control"] == "no-cache, no-store"


@pytest.mark.asyncio
async def test_session_status_is_content_free_and_missing_is_explicit(
    make_client,
) -> None:
    client, _, _ = await make_client()
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        current = await client.get(f"/api/coding/sessions/{session_id}")
        missing = await client.get("/api/coding/sessions/missing-session")

    assert current.status_code == 200
    assert current.json() == {"state": "ready"}
    assert current.headers["cache-control"] == "no-store"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_sse_can_resume_after_sequence(make_client) -> None:
    client, _, _ = await make_client()
    async with client:
        session_id = await _create_and_start(client)
        response = await client.get(
            f"/api/coding/sessions/{session_id}/events",
            params={"after": 3},
        )

    events = _sse_events(response.text)
    assert [event["seq"] for event in events] == [4, 5]


@pytest.mark.asyncio
async def test_concurrent_turn_and_extra_request_fields_are_rejected(make_client) -> None:
    worker = FakeWorker(block_turn=True)
    client, _, _ = await make_client(worker=worker)
    async with client:
        session_id = await _create_and_start(client)
        conflict = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "Second"},
        )
        injected = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "Question", "cwd": "C:\\private", "command": "dir"},
        )
        worker.release.set()

    assert conflict.status_code == 409
    assert injected.status_code == 422


@pytest.mark.asyncio
async def test_prompt_limit_and_cancel_are_idempotent(make_client) -> None:
    worker = FakeWorker(block_turn=True)
    client, _, _ = await make_client(worker=worker)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        too_long = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "x" * 20_001},
        )
        started = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "Stop me"},
        )
        assert started.status_code == 202
        await asyncio.sleep(0)
        first = await client.post(f"/api/coding/sessions/{session_id}/cancel")
        second = await client.post(f"/api/coding/sessions/{session_id}/cancel")
        events = await client.get(f"/api/coding/sessions/{session_id}/events")

    assert too_long.status_code == 422
    assert first.json() == {"accepted": True}
    assert second.json() == {"accepted": False}
    assert _sse_events(events.text)[-1]["type"] == "cancelled"


@pytest.mark.asyncio
async def test_worker_unavailable_and_expired_sessions_fail_cleanly(make_client) -> None:
    unavailable = FakeWorker(fail_health=True)
    client, _, _ = await make_client(worker=unavailable)
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        create = await client.post("/api/coding/sessions")

    assert capabilities.json()["reason"] == "worker_unavailable"
    assert create.status_code == 503

    client2, service, worker2 = await make_client(ttl_seconds=0.01)
    async with client2:
        created = await client2.post("/api/coding/sessions")
        session_id = created.json()["id"]
        record = service._sessions[session_id]
        removed = await service.cleanup_expired(now=record.updated_at + 1)
        missing = await client2.post(f"/api/coding/sessions/{session_id}/cancel")

    assert removed == 1
    assert worker2.closed == [session_id]
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_draft_capabilities_and_review_endpoints_are_explicit(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    client, _, _ = await make_client(worker=worker)
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        changes = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        diff = await client.get(
            f"/api/coding/sessions/{session_id}/diff",
            params={"path": "server/example.py", "revision": 1},
        )
        checked = await client.post(
            f"/api/coding/sessions/{session_id}/validate"
        )
        patch = await client.get(
            f"/api/coding/sessions/{session_id}/patch",
            params={"revision": 1},
        )
        discarded = await client.post(
            f"/api/coding/sessions/{session_id}/discard"
        )

    body = capabilities.json()
    assert body["available"] is True
    assert body["mode"] == "draft"
    assert body["host_apply"] is False
    assert body["limits"]["max_changed_files"] == 20
    assert body["limits"]["max_file_bytes"] == 512 * 1024
    assert body["limits"]["max_patch_bytes"] == 1024 * 1024
    assert changes.json()["files"][0]["path"] == "server/example.py"
    assert checked.json()["validation_status"] == "passed"
    assert diff.status_code == 200
    assert diff.headers["content-type"].startswith("text/x-diff")
    assert diff.headers["cache-control"] == "no-store"
    assert "/workspace" not in diff.text
    assert patch.status_code == 200
    assert patch.headers["cache-control"] == "no-store"
    assert patch.headers["content-disposition"].endswith(
        'filename="modelmirror-changes-r1.patch"'
    )
    assert discarded.json()["revision"] == 2
    assert discarded.json()["files"] == []


@pytest.mark.asyncio
async def test_review_rejects_readonly_busy_stale_and_invalid_path(
    make_client,
) -> None:
    readonly_client, _, _ = await make_client()
    async with readonly_client:
        created = await readonly_client.post("/api/coding/sessions")
        readonly_id = created.json()["id"]
        unavailable = await readonly_client.get(
            f"/api/coding/sessions/{readonly_id}/changes"
        )
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "draft_unavailable"

    worker = FakeWorker(mode="draft", block_turn=True)
    client, service, _ = await make_client(worker=worker)
    async with client:
        session_id = await _create_and_start(client)
        busy = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        worker.release.set()
        turn_task = service._sessions[session_id].turn_task
        assert turn_task is not None
        await turn_task
        invalid = await client.get(
            f"/api/coding/sessions/{session_id}/diff",
            params={"path": "../private.py", "revision": 1},
        )
        stale = await client.get(
            f"/api/coding/sessions/{session_id}/diff",
            params={"path": "server/example.py", "revision": 0},
        )

    assert busy.status_code == 409
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_path"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_revision"


@pytest.mark.asyncio
async def test_failed_check_keeps_review_but_blocks_patch(make_client) -> None:
    worker = FakeWorker(mode="draft", validation_passed=False)
    client, _, _ = await make_client(worker=worker)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        changes = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        patch = await client.get(
            f"/api/coding/sessions/{session_id}/patch",
            params={"revision": 1},
        )

    assert changes.status_code == 200
    assert changes.json()["validation_status"] == "failed"
    assert changes.json()["can_download"] is False
    assert patch.status_code == 409
    assert patch.json()["detail"]["code"] == "validation_failed"


@pytest.mark.asyncio
async def test_malformed_worker_review_payload_is_not_exposed(make_client) -> None:
    worker = FakeWorker(mode="draft", malformed_changes=True)
    client, _, _ = await make_client(worker=worker)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        changes = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )

    assert changes.status_code == 503
    serialized = json.dumps(changes.json())
    assert "/workspace" not in serialized
    assert changes.json()["detail"]["code"] == "invalid_worker_response"


@pytest.mark.asyncio
async def test_project_verification_api_is_manual_bounded_and_non_blocking(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    client, _, _ = await make_client(worker=worker)
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        injected = await client.post(
            f"/api/coding/sessions/{session_id}/verification",
            json={"revision": 1, "command": "pytest selected_test.py"},
        )
        started = await client.post(
            f"/api/coding/sessions/{session_id}/verification",
            json={"revision": 1},
        )
        running = await client.get(
            f"/api/coding/sessions/{session_id}/verification",
            params={"revision": 1},
        )
        turn_conflict = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "Start another change"},
        )
        discard_conflict = await client.post(
            f"/api/coding/sessions/{session_id}/discard"
        )
        cancelled = await client.post(
            f"/api/coding/sessions/{session_id}/verification/cancel",
            json={"revision": 1},
        )
        cancelled_again = await client.post(
            f"/api/coding/sessions/{session_id}/verification/cancel",
            json={"revision": 1},
        )
        restarted = await client.post(
            f"/api/coding/sessions/{session_id}/verification",
            json={"revision": 1},
        )
        worker.verification_state = "completed"
        worker.verification_result = "failed"
        failed = await client.get(
            f"/api/coding/sessions/{session_id}/verification",
            params={"revision": 1},
        )
        patch = await client.get(
            f"/api/coding/sessions/{session_id}/patch",
            params={"revision": 1},
        )
        discarded = await client.post(
            f"/api/coding/sessions/{session_id}/discard"
        )
        stale = await client.get(
            f"/api/coding/sessions/{session_id}/verification",
            params={"revision": 2},
        )

    verification_capability = capabilities.json()["verification"]
    assert verification_capability == {
        "available": True,
        "strategy": "adaptive",
        "required_for_patch": False,
        "max_duration_seconds": 600,
    }
    assert injected.status_code == 422
    assert started.status_code == 202
    assert started.headers["cache-control"] == "no-store"
    assert running.json()["state"] == "running"
    assert running.headers["cache-control"] == "no-store"
    assert turn_conflict.status_code == 409
    assert turn_conflict.json()["detail"]["code"] == "verification_in_progress"
    assert discard_conflict.status_code == 409
    assert cancelled.status_code == 200
    assert cancelled.headers["cache-control"] == "no-store"
    assert cancelled.json()["accepted"] is True
    assert cancelled_again.json()["state"] == "cancelled"
    assert restarted.status_code == 202
    assert failed.json()["result"] == "failed"
    serialized = json.dumps(failed.json())
    assert "C:\\private" not in serialized
    assert "sk-" + ("z" * 24) not in serialized
    assert patch.status_code == 200
    assert discarded.status_code == 200
    assert stale.json()["stale"] is True


@pytest.mark.asyncio
async def test_verifier_unavailable_does_not_disable_draft(make_client) -> None:
    worker = FakeWorker(mode="draft", verification_available=False)
    client, _, _ = await make_client(worker=worker)
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        changes = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        patch = await client.get(
            f"/api/coding/sessions/{session_id}/patch",
            params={"revision": 1},
        )

    body = capabilities.json()
    assert body["available"] is True
    assert body["verification"]["available"] is False
    assert body["verification"]["reason"] == "verifier_unavailable"
    assert changes.status_code == 200
    assert patch.status_code == 200


@pytest.mark.asyncio
async def test_controlled_apply_is_gated_idempotent_frozen_and_revertible(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    applier = FakeApplier()
    client, _, _ = await make_client(worker=worker, applier=applier)
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        first = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        repeated = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        status_response = await client.get(
            f"/api/coding/sessions/{session_id}/apply",
            params={"revision": 1},
        )
        changes = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        frozen_turn = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "Change it again"},
        )
        frozen_discard = await client.post(
            f"/api/coding/sessions/{session_id}/discard"
        )
        frozen_verification = await client.post(
            f"/api/coding/sessions/{session_id}/verification",
            json={"revision": 1},
        )
        frozen_verification_cancel = await client.post(
            f"/api/coding/sessions/{session_id}/verification/cancel",
            json={"revision": 1},
        )
        wrong_revert = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json={
                "revision": 1,
                "apply_id": "wrong_apply_identifier_123",
            },
        )
        reverted = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json={
                "revision": 1,
                "apply_id": first.json()["apply_id"],
            },
        )
        repeated_revert = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json={
                "revision": 1,
                "apply_id": first.json()["apply_id"],
            },
        )
        closed = await client.post(
            f"/api/coding/sessions/{session_id}/close"
        )
        missing = await client.get(
            f"/api/coding/sessions/{session_id}/apply",
            params={"revision": 1},
        )

    capability = capabilities.json()
    assert capability["host_apply"] is True
    assert capability["apply"] == {
        "configured": True,
        "available": True,
        "target": "dedicated_worktree",
        "requires_verification": True,
        "allows_not_applicable": True,
        "supports_revert": True,
    }
    assert SNAPSHOT_FINGERPRINT not in json.dumps(capability)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["state"] == "applied"
    assert first.json() == repeated.json() == status_response.json()
    assert len(applier.apply_calls) == 1
    assert changes.status_code == 200
    assert frozen_turn.json()["detail"]["code"] == "session_frozen"
    assert frozen_discard.json()["detail"]["code"] == "session_frozen"
    assert frozen_verification.json()["detail"]["code"] == "session_frozen"
    assert frozen_verification_cancel.json()["detail"]["code"] == (
        "session_frozen"
    )
    assert wrong_revert.json()["detail"]["code"] == "apply_mismatch"
    assert reverted.json()["state"] == "reverted"
    assert reverted.json() == repeated_revert.json()
    assert len(applier.revert_calls) == 1
    assert closed.json() == {"closed": True}
    assert closed.headers["cache-control"] == "no-store"
    assert missing.status_code == 404
    assert worker.closed == [worker.session_id]


@pytest.mark.asyncio
async def test_controlled_apply_rejects_unverified_and_stale_results(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    applier = FakeApplier()
    client, _, _ = await make_client(worker=worker, applier=applier)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        not_run = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        worker.verification_state = "completed"
        worker.verification_result = "failed"
        failed = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        worker.verification_result = "passed"
        worker.verification_revision = 0
        stale = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        worker.verification_revision = 1
        worker.verification_result = "not_run"
        worker.verification_reason = "dependency_change_unsupported"
        dependency = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        worker.validation_passed = False
        validation_failed = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )

    assert not_run.json()["detail"]["code"] == "verification_required"
    assert failed.json()["detail"]["code"] == "verification_failed"
    assert stale.json()["detail"]["code"] == "verification_stale"
    assert dependency.json()["detail"]["code"] == (
        "dependency_change_unsupported"
    )
    assert validation_failed.json()["detail"]["code"] == "validation_failed"
    assert applier.apply_calls == []


@pytest.mark.asyncio
async def test_documentation_only_result_is_allowed_only_for_documentation(
    make_client,
) -> None:
    applier = FakeApplier()
    docs_worker = FakeWorker(mode="draft", change_path="docs/guide.md")
    docs_worker.verification_state = "completed"
    docs_worker.verification_result = "not_applicable"
    docs_worker.verification_reason = "documentation_only"
    docs_client, _, _ = await make_client(
        worker=docs_worker,
        applier=applier,
    )
    async with docs_client:
        created = await docs_client.post("/api/coding/sessions")
        allowed = await docs_client.post(
            f"/api/coding/sessions/{created.json()['id']}/apply",
            json={"revision": 1},
        )

    code_worker = FakeWorker(mode="draft")
    code_worker.verification_state = "completed"
    code_worker.verification_result = "not_applicable"
    code_worker.verification_reason = "documentation_only"
    code_client, _, _ = await make_client(
        worker=code_worker,
        applier=FakeApplier(),
    )
    async with code_client:
        created = await code_client.post("/api/coding/sessions")
        rejected = await code_client.post(
            f"/api/coding/sessions/{created.json()['id']}/apply",
            json={"revision": 1},
        )

    assert allowed.json()["state"] == "applied"
    assert rejected.json()["detail"]["code"] == "verification_required"


@pytest.mark.asyncio
async def test_applier_unavailable_does_not_disable_draft_review(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    client, _, _ = await make_client(worker=worker)
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        changes = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        patch = await client.get(
            f"/api/coding/sessions/{session_id}/patch",
            params={"revision": 1},
        )

    assert capabilities.json()["available"] is True
    assert capabilities.json()["host_apply"] is False
    assert capabilities.json()["apply"]["reason"] == "applier_not_configured"
    assert changes.status_code == 200
    assert patch.status_code == 200


@pytest.mark.asyncio
async def test_revert_conflict_is_safe_and_does_not_retry(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    applier = FakeApplier(revert_error="revert_conflict")
    client, _, _ = await make_client(worker=worker, applier=applier)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        payload = {
            "revision": 1,
            "apply_id": applied.json()["apply_id"],
        }
        conflict = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json=payload,
        )
        repeated = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json=payload,
        )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "revert_conflict"
    assert repeated.status_code == 200
    assert repeated.json()["state"] == "failed"
    assert repeated.json()["reason"] == "revert_conflict"
    assert len(applier.revert_calls) == 1


@pytest.mark.asyncio
async def test_apply_serializes_mutations_and_failed_request_is_idempotent(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    applier = FakeApplier(block_apply=True, apply_error="target_changed")
    client, _, _ = await make_client(worker=worker, applier=applier)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        apply_task = asyncio.create_task(
            client.post(
                f"/api/coding/sessions/{session_id}/apply",
                json={"revision": 1},
            )
        )
        await applier.apply_started.wait()
        turn = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "Race with application"},
        )
        discard = await client.post(
            f"/api/coding/sessions/{session_id}/discard"
        )
        applier.release_apply.set()
        failed = await apply_task
        repeated = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )

    assert turn.json()["detail"]["code"] == "apply_in_progress"
    assert discard.json()["detail"]["code"] == "apply_in_progress"
    assert failed.status_code == 409
    assert failed.json()["detail"]["code"] == "target_changed"
    assert failed.headers["cache-control"] == "no-store"
    assert repeated.status_code == 200
    assert repeated.json()["state"] == "failed"
    assert repeated.json()["reason"] == "target_changed"
    assert len(applier.apply_calls) == 1
