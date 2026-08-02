from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
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
from server.coding_runtime.applier_client import (
    APPLIER_OPERATION_TIMEOUT_SECONDS,
    ApplierClientError,
    CodingApplierClient,
)
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CommitReceipt
from server.coding_runtime.committer_client import (
    COMMITTER_OPERATION_TIMEOUT_SECONDS,
    CodingCommitterClient,
    CommitterClientError,
)
from server.coding_runtime.models import CodingEvent, CodingEventKind
from server.coding_runtime.publisher_client import (
    PUBLISH_OPERATION_TIMEOUT_SECONDS,
    CodingPublisherClient,
    PublisherClientError,
)
from server.coding_runtime.publish_models import (
    PublishCommit,
    PublishManifest,
    PublishReceipt,
    PublishState,
)
from server.coding_runtime.recovery import CodingRecoveryStore
from server.coding_runtime.worker import (
    CodingWorkerError,
    _validate_recovered_verification,
)


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
        snapshot_fingerprint: str = SNAPSHOT_FINGERPRINT,
        fail_recovery_snapshot: bool = False,
    ) -> None:
        self.configured = configured
        self.block_turn = block_turn
        self.fail_health = fail_health
        self.mode = mode
        self.validation_passed = validation_passed
        self.malformed_changes = malformed_changes
        self.verification_available = verification_available
        self.change_path = change_path
        self.snapshot_fingerprint = snapshot_fingerprint
        self.fail_recovery_snapshot = fail_recovery_snapshot
        self.release = asyncio.Event()
        self.cancelled = False
        self.closed: list[str] = []
        self.session_id = "coding-session"
        self.revision = 1
        self.verification_revision = 1
        self.verification_state = "not_started"
        self.verification_result = "not_run"
        self.verification_reason: str | None = None
        self.restore_calls: list[dict[str, Any]] = []
        self.current_empty = False

    async def health(self) -> dict[str, Any]:
        if self.fail_health:
            raise CodingWorkerError("unavailable", code="worker_unavailable")
        return {
            "ok": True,
            "configured": self.configured,
            "mode": self.mode,
            "snapshot_fingerprint": self.snapshot_fingerprint,
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

    async def restore_session(self, **kwargs: Any) -> dict[str, Any]:
        self.restore_calls.append(kwargs)
        self.revision = kwargs["revision"]
        self.current_empty = not bool(kwargs.get("paths"))
        verification = kwargs.get("verification")
        if isinstance(verification, dict):
            self.verification_revision = verification["revision"]
            self.verification_state = verification["state"]
            self.verification_result = verification["result"]
            self.verification_reason = verification["reason"]
        return {
            "ok": True,
            "session_id": self.session_id,
            "mode": self.mode,
            "event": self._event(1, CodingEventKind.SESSION_STARTED).to_dict(),
            "changes": self._changes(empty=self.current_empty),
            "recovered": True,
        }

    async def recovery_snapshot(self, session_id: str) -> dict[str, Any]:
        if self.fail_recovery_snapshot:
            raise CodingWorkerError(
                "snapshot failed",
                code="recovery_snapshot_failed",
            )
        return {
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "changes": self._changes(empty=self.current_empty),
            "patch": "" if self.current_empty else self._diff_content(),
            "base_patch": self._diff_content() if self.current_empty else "",
            "cumulative_changes": self._changes(),
            "cumulative_patch": self._diff_content(),
            "verification": (
                self._verification()
                if self.verification_state == "completed"
                else None
            ),
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
        return self._changes(empty=self.current_empty)

    async def diff(self, session_id: str, path: str, revision: int) -> str:
        self._require_revision(revision)
        if path != self.change_path:
            raise CodingWorkerError("missing", code="change_not_found")
        return self._diff_content()

    def _diff_content(self) -> str:
        return (
            f"diff --git a/{self.change_path} b/{self.change_path}\n"
            f"--- a/{self.change_path}\n"
            f"+++ b/{self.change_path}\n"
            "@@ -1 +1 @@\n"
            "-before\n"
            "+after\n"
        )

    async def patch(
        self,
        session_id: str,
        revision: int,
        *,
        scope: str = "current",
    ) -> str:
        self._require_revision(revision)
        return await self.diff(session_id, self.change_path, revision)

    async def checkpoint_cycle(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        self._require_revision(revision)
        self.current_empty = True
        return self._changes(empty=True)

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
            "patch_bytes": 0 if empty else len(self._diff_content().encode("utf-8")),
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
        reconcile_state: str = "applied",
    ) -> None:
        self.configured = configured
        self.available = available
        self.fingerprint = fingerprint
        self.apply_error = apply_error
        self.revert_error = revert_error
        self.block_apply = block_apply
        self.reconcile_state = reconcile_state
        self.apply_calls: list[dict[str, Any]] = []
        self.revert_calls: list[ApplyReceipt] = []
        self.apply_started = asyncio.Event()
        self.release_apply = asyncio.Event()
        self.reconcile_calls: list[dict[str, Any]] = []

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

    async def reconcile(self, **kwargs: Any) -> tuple[str, ApplyReceipt | None]:
        self.reconcile_calls.append(kwargs)
        if self.reconcile_state != "applied":
            return self.reconcile_state, None
        return self.reconcile_state, ApplyReceipt(
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


class FakeCommitter:
    def __init__(
        self,
        *,
        configured: bool = True,
        available: bool = True,
        fingerprint: str = SNAPSHOT_FINGERPRINT,
        commit_error: str | None = None,
        undo_error: str | None = None,
        reconcile_state: str = "committed",
    ) -> None:
        self.configured = configured
        self.available = available
        self.fingerprint = fingerprint
        self.commit_error = commit_error
        self.undo_error = undo_error
        self.reconcile_state = reconcile_state
        self.commit_calls: list[dict[str, Any]] = []
        self.undo_calls: list[tuple[CommitReceipt, ApplyReceipt]] = []
        self.reconcile_calls: list[dict[str, Any]] = []
        self.next_parent_sha = "b" * 40

    async def health(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "available": self.available,
            "target": "isolated_local_repository",
            "snapshot_fingerprint": self.fingerprint,
            **({} if self.available else {"reason": "repository_not_ready"}),
        }

    async def commit(self, **kwargs: Any) -> CommitReceipt:
        self.commit_calls.append(kwargs)
        if self.commit_error is not None:
            raise CommitterClientError("commit failed", code=self.commit_error)
        apply_receipt = kwargs["apply_receipt"]
        commit_sha = ("d" if len(self.commit_calls) == 1 else "e") * 40
        receipt = CommitReceipt(
            commit_id=kwargs["operation_id"],
            revision=apply_receipt.revision,
            apply_id=apply_receipt.apply_id,
            commit_sha=commit_sha,
            parent_sha=self.next_parent_sha,
            tree_sha="f" * 40,
            message=kwargs["message"],
            files=tuple(item.path for item in apply_receipt.files),
            committed_at=20.0,
        )
        self.next_parent_sha = commit_sha
        return receipt

    async def undo(
        self,
        receipt: CommitReceipt,
        apply_receipt: ApplyReceipt,
    ) -> CommitReceipt:
        self.undo_calls.append((receipt, apply_receipt))
        if self.undo_error is not None:
            raise CommitterClientError("undo failed", code=self.undo_error)
        return receipt

    async def reconcile(self, **kwargs: Any) -> tuple[str, CommitReceipt | None]:
        self.reconcile_calls.append(kwargs)
        if self.reconcile_state not in {"committed", "undone"}:
            return self.reconcile_state, None
        apply_receipt = kwargs["apply_receipt"]
        return self.reconcile_state, CommitReceipt(
            commit_id=kwargs["operation_id"],
            revision=apply_receipt.revision,
            apply_id=apply_receipt.apply_id,
            commit_sha="d" * 40,
            parent_sha="b" * 40,
            tree_sha="f" * 40,
            message=kwargs["message"],
            files=tuple(item.path for item in apply_receipt.files),
            committed_at=20.0,
        )


class FakePublisher:
    def __init__(
        self,
        *,
        configured: bool = True,
        available: bool = True,
        publish_error: str | None = None,
        ready_error: str | None = None,
        reconcile_state: str = "draft",
    ) -> None:
        self.configured = configured
        self.available = available
        self.publish_error = publish_error
        self.ready_error = ready_error
        self.reconcile_state = reconcile_state
        self.publish_calls: list[PublishManifest] = []
        self.ready_calls: list[tuple[PublishManifest, PublishReceipt]] = []
        self.reconcile_calls: list[PublishManifest] = []

    async def health(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "available": self.available,
            "provider": "github",
            "target": "fixed_repository",
            **({} if self.available else {"reason": "publisher_unavailable"}),
        }

    async def publish(self, manifest: PublishManifest) -> PublishReceipt:
        self.publish_calls.append(manifest)
        await asyncio.sleep(0)
        if self.publish_error is not None:
            raise PublisherClientError("publish failed", code=self.publish_error)
        return self._receipt(manifest)

    async def reconcile(
        self,
        manifest: PublishManifest,
    ) -> tuple[str, PublishReceipt | None]:
        self.reconcile_calls.append(manifest)
        if self.reconcile_state not in {"draft", "ready"}:
            return self.reconcile_state, None
        return self.reconcile_state, self._receipt(
            manifest,
            ready=self.reconcile_state == "ready",
        )

    async def mark_ready(
        self,
        manifest: PublishManifest,
        receipt: PublishReceipt,
    ) -> PublishReceipt:
        self.ready_calls.append((manifest, receipt))
        await asyncio.sleep(0)
        if self.ready_error is not None:
            raise PublisherClientError("ready failed", code=self.ready_error)
        return self._receipt(manifest, ready=True)

    @staticmethod
    def _receipt(
        manifest: PublishManifest,
        *,
        ready: bool = False,
    ) -> PublishReceipt:
        return PublishReceipt(
            publish_id=manifest.publish_id,
            revision=manifest.revision,
            repository_id=731,
            repository="PinkElf-Elysia/ModelMirror",
            base_branch="main",
            branch=manifest.branch,
            head_sha=manifest.head_sha,
            pr_number=89,
            pr_node_id="PR_kwDOExample89",
            pr_url="https://github.com/PinkElf-Elysia/ModelMirror/pull/89",
            state=PublishState.READY if ready else PublishState.DRAFT,
            published_at=30.0,
            ready_at=31.0 if ready else None,
        )


@pytest_asyncio.fixture
async def make_client():
    services: list[CodingService] = []

    async def factory(
        *,
        enabled: bool = True,
        worker: FakeWorker | None = None,
        applier: FakeApplier | None = None,
        committer: FakeCommitter | None = None,
        publisher: FakePublisher | None = None,
        recovery_store: CodingRecoveryStore | None = None,
        recovery_enabled: bool = False,
        incremental_enabled: bool = False,
        publish_enabled: bool = False,
        ttl_seconds: float = 1800,
    ) -> tuple[httpx.AsyncClient, CodingService, FakeWorker]:
        fake = worker or FakeWorker()
        service = CodingService(
            enabled=enabled,
            worker=fake,
            applier=applier,
            committer=committer,
            publisher=publisher,
            recovery_store=recovery_store,
            recovery_enabled=recovery_enabled,
            incremental_enabled=incremental_enabled,
            publish_enabled=publish_enabled,
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


async def _wait_publish_state(
    client: httpx.AsyncClient,
    session_id: str,
    revision: int,
    expected: str,
) -> dict[str, Any]:
    for _ in range(200):
        response = await client.get(
            f"/api/coding/sessions/{session_id}/publish",
            params={"revision": revision},
        )
        assert response.status_code == 200
        if response.json()["state"] == expected:
            return response.json()
        await asyncio.sleep(0.01)
    raise AssertionError(f"publish state did not become {expected}")


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
async def test_empty_builtin_draft_session_can_close_without_apply(make_client) -> None:
    worker = FakeWorker(mode="draft")
    worker.current_empty = True
    client, _, _ = await make_client(worker=worker)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        closed = await client.post(f"/api/coding/sessions/{session_id}/close")
        missing = await client.get(f"/api/coding/sessions/{session_id}")

    assert closed.status_code == 200
    assert closed.json() == {"closed": True}
    assert worker.closed == [worker.session_id]
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_builtin_draft_session_with_changes_cannot_close(make_client) -> None:
    worker = FakeWorker(mode="draft")
    client, _, _ = await make_client(worker=worker)
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        rejected = await client.post(f"/api/coding/sessions/{session_id}/close")
        current = await client.get(f"/api/coding/sessions/{session_id}")

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "session_has_draft"
    assert worker.closed == []
    assert current.status_code == 200


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
async def test_failed_check_keeps_review_and_allows_patch_download(make_client) -> None:
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
    assert patch.status_code == 200
    assert patch.headers["content-type"].startswith("text/x-diff")


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
        "requires_verification": False,
        "allows_quality_risk_confirmation": True,
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
async def test_controlled_apply_requires_confirmation_for_quality_risks(
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
        worker.verification_state = "running"
        running = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1, "confirm_quality_risks": True},
        )
        worker.verification_state = "completed"
        worker.verification_result = "failed"
        invalid_confirmation = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1, "confirm_quality_risks": "true"},
        )
        confirmed = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1, "confirm_quality_risks": True},
        )

    assert not_run.json()["detail"]["code"] == "verification_required"
    assert failed.json()["detail"]["code"] == "verification_failed"
    assert stale.json()["detail"]["code"] == "verification_stale"
    assert dependency.json()["detail"]["code"] == (
        "dependency_change_unsupported"
    )
    assert validation_failed.json()["detail"]["code"] == "validation_failed"
    assert running.json()["detail"]["code"] == "verification_in_progress"
    assert invalid_confirmation.status_code == 422
    assert confirmed.json()["state"] == "applied"
    assert len(applier.apply_calls) == 1


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
async def test_apply_serializes_mutations_and_retries_same_operation_safely(
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
        applier.apply_error = None
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
    assert repeated.json()["state"] == "applied"
    assert len(applier.apply_calls) == 2
    assert (
        applier.apply_calls[0]["operation_id"]
        == applier.apply_calls[1]["operation_id"]
    )


@pytest.mark.asyncio
async def test_local_commit_is_gated_idempotent_undoable_and_private(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    applier = FakeApplier()
    committer = FakeCommitter()
    client, _, _ = await make_client(
        worker=worker,
        applier=applier,
        committer=committer,
    )
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        apply_id = applied.json()["apply_id"]
        before = await client.get(
            f"/api/coding/sessions/{session_id}/commit",
            params={"revision": 1},
        )
        payload = {
            "revision": 1,
            "apply_id": apply_id,
            "message": "feature: 保存随机功能 A91C",
        }
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json=payload,
        )
        repeated = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json=payload,
        )
        commit_status = await client.get(
            f"/api/coding/sessions/{session_id}/commit",
            params={"revision": 1},
        )
        apply_status = await client.get(
            f"/api/coding/sessions/{session_id}/apply",
            params={"revision": 1},
        )
        blocked_revert = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json={"revision": 1, "apply_id": apply_id},
        )
        undo_payload = {
            "revision": 1,
            "apply_id": apply_id,
            "commit_id": committed.json()["commit_id"],
        }
        undone = await client.post(
            f"/api/coding/sessions/{session_id}/commit/undo",
            json=undo_payload,
        )
        repeated_undo = await client.post(
            f"/api/coding/sessions/{session_id}/commit/undo",
            json=undo_payload,
        )
        reverted = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json={"revision": 1, "apply_id": apply_id},
        )

    assert capabilities.json()["commit"] == {
        "configured": True,
        "available": True,
        "target": "isolated_local_repository",
        "requires_apply": True,
        "supports_undo": True,
        "remote_operations": False,
        "max_message_chars": 2000,
    }
    assert before.json()["suggested_message"] == "feature: 更新项目功能"
    assert committed.status_code == 200
    assert committed.headers["cache-control"] == "no-store"
    assert committed.json() == repeated.json() == commit_status.json()
    assert len(committer.commit_calls) == 1
    serialized = json.dumps(committed.json())
    assert "parent_sha" not in serialized
    assert "tree_sha" not in serialized
    assert "server/example.py" not in serialized
    assert apply_status.json()["can_revert"] is False
    assert blocked_revert.json()["detail"]["code"] == "commit_must_be_undone"
    assert undone.json()["state"] == "undone"
    assert undone.json() == repeated_undo.json()
    assert len(committer.undo_calls) == 1
    assert reverted.json()["state"] == "reverted"


@pytest.mark.asyncio
async def test_incremental_continue_archives_cycle_and_opens_empty_next_cycle(
    make_client,
    tmp_path: Path,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    client, _, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        recovery_store=CodingRecoveryStore(tmp_path / "recovery"),
        recovery_enabled=True,
        incremental_enabled=True,
    )
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "feature: 保存增量样例 84C1",
            },
        )
        continued = await client.post(
            f"/api/coding/sessions/{session_id}/continue",
            json={
                "revision": 1,
                "commit_id": committed.json()["commit_id"],
            },
        )
        history = await client.get(
            f"/api/coding/sessions/{session_id}/history"
        )
        changes = await client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        cumulative = await client.get(
            f"/api/coding/sessions/{session_id}/patch",
            params={"revision": 1, "scope": "cumulative"},
        )

    assert capabilities.json()["incremental"] == {
        "enabled": True,
        "available": True,
        "max_cycles": 10,
        "requires_recovery": True,
        "commit_strategy": "linear",
        "undo_scope": "latest",
    }
    assert continued.status_code == 200
    assert continued.json()["active_cycle"] == 2
    assert history.json()["completed_count"] == 1
    assert history.json()["cycles"][0]["message"] == (
        "feature: 保存增量样例 84C1"
    )
    assert changes.json()["files"] == []
    assert cumulative.status_code == 200
    assert cumulative.headers["cache-control"] == "no-store"
    assert "server/example.py" in cumulative.text


@pytest.mark.asyncio
async def test_publish_manifest_preserves_two_linear_local_commits(
    make_client,
    tmp_path: Path,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    committer = FakeCommitter()
    publisher = FakePublisher()
    client, _, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=committer,
        publisher=publisher,
        recovery_store=CodingRecoveryStore(tmp_path / "linear-publish-recovery"),
        recovery_enabled=True,
        incremental_enabled=True,
        publish_enabled=True,
    )
    async with client:
        session_id = (await client.post("/api/coding/sessions")).json()["id"]
        first_apply = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        first_commit = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": first_apply.json()["apply_id"],
                "message": "docs: first random publish cycle D31A",
            },
        )
        await client.post(
            f"/api/coding/sessions/{session_id}/continue",
            json={
                "revision": 1,
                "commit_id": first_commit.json()["commit_id"],
            },
        )
        worker.current_empty = False
        worker.revision = 2
        worker.verification_revision = 2
        worker.verification_state = "completed"
        worker.verification_result = "passed"
        second_apply = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 2},
        )
        second_commit = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 2,
                "apply_id": second_apply.json()["apply_id"],
                "message": "feature: second random publish cycle E42B",
            },
        )
        await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json={
                "revision": 2,
                "commit_id": second_commit.json()["commit_id"],
                "title": "Publish two linear random cycles D31A E42B",
                "body": "The draft PR should retain both local commits.",
            },
        )
        published = await _wait_publish_state(client, session_id, 2, "draft")

    assert published["commit_count"] == 2
    manifest = publisher.publish_calls[0]
    assert [item.commit_sha for item in manifest.commits] == ["d" * 40, "e" * 40]
    assert manifest.commits[1].parent_sha == manifest.commits[0].commit_sha


@pytest.mark.asyncio
async def test_commit_failure_reuses_operation_and_blocks_unsafe_apply_revert(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    committer = FakeCommitter(commit_error="committer_timeout")
    client, _, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=committer,
    )
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
            "message": "feature: 保存超时恢复 B72E",
        }
        failed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json=payload,
        )
        changed_message = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={**payload, "message": "feature: 不安全改名"},
        )
        blocked_revert = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json={"revision": 1, "apply_id": applied.json()["apply_id"]},
        )
        first_operation = committer.commit_calls[0]["operation_id"]
        committer.commit_error = None
        committer.available = False
        recovered = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json=payload,
        )

    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "committer_timeout"
    assert changed_message.json()["detail"]["code"] == (
        "commit_retry_message_mismatch"
    )
    assert blocked_revert.json()["detail"]["code"] == "commit_must_be_undone"
    assert recovered.json()["state"] == "committed"
    assert committer.commit_calls[1]["operation_id"] == first_operation


@pytest.mark.asyncio
async def test_committer_unavailable_does_not_disable_existing_draft_features(
    make_client,
) -> None:
    worker = FakeWorker(mode="draft")
    client, _, _ = await make_client(worker=worker, applier=FakeApplier())
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        changes = await client.get(
            f"/api/coding/sessions/{created.json()['id']}/changes"
        )

    assert capabilities.json()["available"] is True
    assert capabilities.json()["commit"]["available"] is False
    assert capabilities.json()["commit"]["reason"] == "committer_not_configured"
    assert changes.status_code == 200


@pytest.mark.asyncio
async def test_github_publish_is_draft_idempotent_ready_and_frozen(
    make_client,
    tmp_path: Path,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    publisher = FakePublisher()
    store = CodingRecoveryStore(tmp_path / "publish-recovery")
    client, _, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        publisher=publisher,
        recovery_store=store,
        recovery_enabled=True,
        publish_enabled=True,
    )
    title = "Publish reviewed random change A8F31"
    body = "One local commit with a randomly named backend fixture."
    async with client:
        capabilities = await client.get("/api/coding/capabilities")
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "feature: publish random fixture A8F31",
            },
        )
        rejected_secret = await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json={
                "revision": 1,
                "commit_id": committed.json()["commit_id"],
                "title": title,
                "body": "ghp_" + ("x" * 40),
            },
        )
        request = {
            "revision": 1,
            "commit_id": committed.json()["commit_id"],
            "title": title,
            "body": body,
        }
        started = await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json=request,
        )
        draft = await _wait_publish_state(client, session_id, 1, "draft")
        repeated = await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json=request,
        )
        blocked_turn = await client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "change one more file"},
        )
        blocked_undo = await client.post(
            f"/api/coding/sessions/{session_id}/commit/undo",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "commit_id": committed.json()["commit_id"],
            },
        )
        marking = await client.post(
            f"/api/coding/sessions/{session_id}/publish/ready",
            json={"revision": 1, "publish_id": draft["publish_id"]},
        )
        ready = await _wait_publish_state(client, session_id, 1, "ready")
        repeated_ready = await client.post(
            f"/api/coding/sessions/{session_id}/publish/ready",
            json={"revision": 1, "publish_id": draft["publish_id"]},
        )

    assert capabilities.json()["publish"] == {
        "enabled": True,
        "configured": True,
        "available": True,
        "provider": "github",
        "target": "fixed_repository",
        "default_pr_state": "draft",
        "supports_mark_ready": True,
        "requires_exact_base": True,
        "remote_merge": False,
    }
    assert rejected_secret.status_code == 422
    assert started.status_code == 202
    assert started.json()["state"] == "publishing"
    assert repeated.status_code == 202
    assert repeated.json()["pr_number"] == 89
    assert len(publisher.publish_calls) == 1
    assert blocked_turn.json()["detail"]["code"] == "session_frozen"
    assert blocked_undo.json()["detail"]["code"] == "session_published"
    assert marking.status_code == 202
    assert ready["pr_url"].endswith("/pull/89")
    assert ready["can_mark_ready"] is False
    assert repeated_ready.json()["state"] == "ready"
    assert len(publisher.ready_calls) == 1
    serialized = json.dumps(ready)
    assert "head_sha" not in serialized
    assert "repository_id" not in serialized
    persisted = b"".join(
        path.read_bytes()
        for path in (tmp_path / "publish-recovery").iterdir()
        if path.is_file()
    )
    assert title.encode() not in persisted
    assert store.load() is not None
    assert store.load().payload.publish is not None


@pytest.mark.asyncio
async def test_publish_conflict_and_unavailable_publisher_do_not_break_draft(
    make_client,
    tmp_path: Path,
) -> None:
    unavailable_client, _, _ = await make_client(
        worker=FakeWorker(mode="draft"),
        applier=FakeApplier(),
        committer=FakeCommitter(),
        recovery_store=CodingRecoveryStore(tmp_path / "unavailable-recovery"),
        recovery_enabled=True,
        publish_enabled=True,
    )
    async with unavailable_client:
        capabilities = await unavailable_client.get("/api/coding/capabilities")
        created = await unavailable_client.post("/api/coding/sessions")
        changes = await unavailable_client.get(
            f"/api/coding/sessions/{created.json()['id']}/changes"
        )
    assert capabilities.json()["available"] is True
    assert capabilities.json()["publish"]["available"] is False
    assert capabilities.json()["publish"]["reason"] == "publisher_not_configured"
    assert changes.status_code == 200

    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    publisher = FakePublisher(publish_error="base_branch_changed")
    client, _, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        publisher=publisher,
        recovery_store=CodingRecoveryStore(tmp_path / "conflict-recovery"),
        recovery_enabled=True,
        publish_enabled=True,
    )
    async with client:
        session_id = (await client.post("/api/coding/sessions")).json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "docs: publish base conflict B7E42",
            },
        )
        await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json={
                "revision": 1,
                "commit_id": committed.json()["commit_id"],
                "title": "Publish base conflict B7E42",
                "body": "The remote base moved before upload.",
            },
        )
        conflict = await _wait_publish_state(client, session_id, 1, "conflict")
        patch_response = await client.get(
            f"/api/coding/sessions/{session_id}/patch",
            params={"revision": 1},
        )
    assert conflict["reason"] == "base_branch_changed"
    assert patch_response.status_code == 200
    assert len(publisher.publish_calls) == 1


@pytest.mark.asyncio
async def test_local_publisher_preflight_failure_is_retryable(
    make_client,
    tmp_path: Path,
) -> None:
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    publisher = FakePublisher(publish_error="repository_not_ready")
    client, _, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        publisher=publisher,
        recovery_store=CodingRecoveryStore(tmp_path / "retryable-publish-recovery"),
        recovery_enabled=True,
        publish_enabled=True,
    )
    title = "Retry local publisher preflight R8N42"
    body = "The fixed local repository temporarily failed its read-only check."
    async with client:
        session_id = (await client.post("/api/coding/sessions")).json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "docs: retry publisher preflight R8N42",
            },
        )
        request = {
            "revision": 1,
            "commit_id": committed.json()["commit_id"],
            "title": title,
            "body": body,
        }
        started = await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json=request,
        )
        failed = await _wait_publish_state(client, session_id, 1, "failed")
        publisher.publish_error = None
        retried = await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json=request,
        )
        draft = await _wait_publish_state(client, session_id, 1, "draft")

    assert started.status_code == 202
    assert failed["reason"] == "repository_not_ready"
    assert retried.status_code == 202
    assert draft["pr_number"] == 89
    assert len(publisher.publish_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_reason",
    [
        "repository_not_ready",
        "base_branch_changed",
        "apply_recovery_conflict",
    ],
)
async def test_legacy_local_conflict_recovers_as_retryable_failure(
    make_client,
    tmp_path: Path,
    legacy_reason: str,
) -> None:
    store = CodingRecoveryStore(
        tmp_path / f"legacy-publish-conflict-{legacy_reason}"
    )
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    publisher = FakePublisher(publish_error="repository_not_ready")
    client, service, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        publisher=publisher,
        recovery_store=store,
        recovery_enabled=True,
        publish_enabled=True,
    )
    title = "Recover local preflight conflict W9K31"
    body = "A legacy local mount failure must remain retryable after restart."
    async with client:
        session_id = (await client.post("/api/coding/sessions")).json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "docs: recover publisher preflight W9K31",
            },
        )
        request = {
            "revision": 1,
            "commit_id": committed.json()["commit_id"],
            "title": title,
            "body": body,
        }
        await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json=request,
        )
        await _wait_publish_state(client, session_id, 1, "failed")
        record = service._sessions[session_id]
        if legacy_reason in {"repository_not_ready", "base_branch_changed"}:
            record.publish_state = PublishState.CONFLICT
        else:
            record.apply_reason = legacy_reason
            record.commit_reason = legacy_reason
        record.publish_reason = legacy_reason
        record.state = "conflict"
        record.recovery_conflict = legacy_reason
        await service._persist_recovery(record, required=True)
    await service.shutdown()

    recovered_publisher = FakePublisher(reconcile_state="not_published")
    recovered_worker = FakeWorker(mode="draft")
    recovered_worker.verification_state = "completed"
    recovered_worker.verification_result = "passed"
    recovered_client, _, _ = await make_client(
        worker=recovered_worker,
        applier=FakeApplier(reconcile_state="applied"),
        committer=FakeCommitter(reconcile_state="committed"),
        publisher=recovered_publisher,
        recovery_store=store,
        recovery_enabled=True,
        publish_enabled=True,
    )
    async with recovered_client:
        resumed = await recovered_client.post("/api/coding/recovery/resume")
        recovered_session_id = resumed.json()["id"]
        status_response = await recovered_client.get(
            f"/api/coding/sessions/{recovered_session_id}/publish",
            params={"revision": 1},
        )
        retried = await recovered_client.post(
            f"/api/coding/sessions/{recovered_session_id}/publish",
            json=request,
        )
        draft = await _wait_publish_state(
            recovered_client,
            recovered_session_id,
            1,
            "draft",
        )

    assert resumed.status_code == 200
    assert resumed.json()["conflict"] is None
    assert status_response.json()["state"] == "failed"
    assert retried.status_code == 202
    assert draft["pr_number"] == 89
    assert len(recovered_publisher.reconcile_calls) == 1
    assert len(recovered_publisher.publish_calls) == 1


@pytest.mark.asyncio
async def test_published_task_recovers_same_draft_pr_without_republishing(
    make_client,
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "published-recovery")
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    client, service, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        publisher=FakePublisher(),
        recovery_store=store,
        recovery_enabled=True,
        publish_enabled=True,
    )
    async with client:
        session_id = (await client.post("/api/coding/sessions")).json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "docs: recover publish fixture C913A",
            },
        )
        await client.post(
            f"/api/coding/sessions/{session_id}/publish",
            json={
                "revision": 1,
                "commit_id": committed.json()["commit_id"],
                "title": "Recover publish fixture C913A",
                "body": "Restart after draft PR creation.",
            },
        )
        await _wait_publish_state(client, session_id, 1, "draft")
    await service.shutdown()

    recovered_publisher = FakePublisher(reconcile_state="draft")
    recovered_worker = FakeWorker(mode="draft")
    recovered_worker.verification_state = "completed"
    recovered_worker.verification_result = "passed"
    recovered_client, _, _ = await make_client(
        worker=recovered_worker,
        applier=FakeApplier(reconcile_state="applied"),
        committer=FakeCommitter(reconcile_state="committed"),
        publisher=recovered_publisher,
        recovery_store=store,
        recovery_enabled=True,
        publish_enabled=True,
    )
    async with recovered_client:
        pending = await recovered_client.get("/api/coding/recovery")
        blocked_discard = await recovered_client.post("/api/coding/recovery/discard")
        resumed = await recovered_client.post("/api/coding/recovery/resume")
        status_response = await recovered_client.get(
            f"/api/coding/sessions/{resumed.json()['id']}/publish",
            params={"revision": 1},
        )
    assert pending.json()["state"] == "published"
    assert blocked_discard.json()["detail"]["code"] == (
        "published_recovery_requires_resume"
    )
    assert resumed.json()["status"] == "published"
    assert status_response.json()["state"] == "draft"
    assert status_response.json()["pr_number"] == 89
    assert recovered_publisher.publish_calls == []
    assert len(recovered_publisher.reconcile_calls) == 1


@pytest.mark.asyncio
async def test_recovery_persists_draft_without_conversation_and_resumes_explicitly(
    make_client,
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery")
    worker = FakeWorker(mode="draft")
    client, service, _ = await make_client(
        worker=worker,
        recovery_store=store,
        recovery_enabled=True,
    )
    random_prompt = "RECOVERY_PROMPT_7F31A9"
    async with client:
        session_id = await _create_and_start(client, random_prompt)
        events = await client.get(f"/api/coding/sessions/{session_id}/events")
        assert _sse_events(events.text)[-1]["type"] == "turn_completed"
        pending = await client.get("/api/coding/recovery")
    await service.shutdown()

    persisted = b"".join(
        path.read_bytes()
        for path in (tmp_path / "recovery").iterdir()
        if path.is_file()
    )
    assert random_prompt.encode() not in persisted
    assert b"Answer for" not in persisted
    assert pending.json()["pending"] is True
    assert pending.json()["restores_conversation"] is False

    resumed_worker = FakeWorker(mode="draft")
    resumed_client, _, _ = await make_client(
        worker=resumed_worker,
        recovery_store=store,
        recovery_enabled=True,
    )
    async with resumed_client:
        blocked = await resumed_client.post("/api/coding/sessions")
        downloaded = await resumed_client.get("/api/coding/recovery/patch")
        resumed = await resumed_client.post("/api/coding/recovery/resume")
        status_response = await resumed_client.get(
            f"/api/coding/sessions/{resumed.json()['id']}"
        )
        discard_while_active = await resumed_client.post(
            "/api/coding/recovery/discard"
        )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "recovery_pending"
    assert downloaded.status_code == 200
    assert downloaded.headers["cache-control"] == "no-store"
    assert "server/example.py" in downloaded.text
    assert resumed.status_code == 200
    assert resumed.json()["conversation_restored"] is False
    assert status_response.json() == {"state": "ready"}
    assert discard_while_active.json()["detail"]["code"] == "session_active"
    assert len(resumed_worker.restore_calls) == 1


@pytest.mark.asyncio
async def test_recovery_save_failure_withholds_success_terminal_event(
    make_client,
    tmp_path: Path,
) -> None:
    worker = FakeWorker(mode="draft", fail_recovery_snapshot=True)
    client, _, _ = await make_client(
        worker=worker,
        recovery_store=CodingRecoveryStore(tmp_path / "recovery-failure"),
        recovery_enabled=True,
    )
    async with client:
        session_id = await _create_and_start(client, "RANDOM_SAVE_FAIL_91A7")
        response = await client.get(f"/api/coding/sessions/{session_id}/events")

    events = _sse_events(response.text)
    assert events[-1]["type"] == "failed"
    assert events[-1]["data"]["code"] == "recovery_snapshot_failed"
    assert all(event["type"] != "turn_completed" for event in events)


@pytest.mark.asyncio
async def test_recovery_snapshot_mismatch_still_allows_download_and_discard(
    make_client,
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery-mismatch")
    client, service, _ = await make_client(
        worker=FakeWorker(mode="draft"),
        recovery_store=store,
        recovery_enabled=True,
    )
    async with client:
        session_id = await _create_and_start(client, "RANDOM_BASELINE_22D7")
        await client.get(f"/api/coding/sessions/{session_id}/events")
    await service.shutdown()

    mismatch_worker = FakeWorker(
        mode="draft",
        snapshot_fingerprint="f" * 64,
    )
    mismatch_client, _, _ = await make_client(
        worker=mismatch_worker,
        recovery_store=store,
        recovery_enabled=True,
    )
    async with mismatch_client:
        recovery = await mismatch_client.get("/api/coding/recovery")
        resume = await mismatch_client.post("/api/coding/recovery/resume")
        patch_response = await mismatch_client.get("/api/coding/recovery/patch")
        discarded = await mismatch_client.post("/api/coding/recovery/discard")

    assert recovery.json()["can_resume"] is False
    assert recovery.json()["reason"] == "snapshot_mismatch"
    assert resume.status_code == 409
    assert resume.json()["detail"]["code"] == "snapshot_mismatch"
    assert patch_response.status_code == 200
    assert discarded.json() == {"discarded": True}
    assert store.load() is None


@pytest.mark.asyncio
async def test_recovery_reconciles_applied_and_committed_receipts(
    make_client,
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery-commit")
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    client, service, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        recovery_store=store,
        recovery_enabled=True,
    )
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "feature: 恢复本地版本 8C31",
            },
        )
        assert committed.json()["state"] == "committed"
    await service.shutdown()

    recovered_worker = FakeWorker(mode="draft")
    recovered_worker.verification_state = "completed"
    recovered_worker.verification_result = "passed"
    recovered_applier = FakeApplier(reconcile_state="conflict")
    recovered_committer = FakeCommitter(reconcile_state="committed")
    recovered_client, _, _ = await make_client(
        worker=recovered_worker,
        applier=recovered_applier,
        committer=recovered_committer,
        recovery_store=store,
        recovery_enabled=True,
    )
    async with recovered_client:
        resumed = await recovered_client.post("/api/coding/recovery/resume")
        commit_status = await recovered_client.get(
            f"/api/coding/sessions/{resumed.json()['id']}/commit",
            params={"revision": 1},
        )

    assert resumed.json()["status"] == "applied"
    assert commit_status.json()["state"] == "committed"
    assert commit_status.json()["message"] == "feature: 恢复本地版本 8C31"
    assert recovered_applier.reconcile_calls == []
    assert len(recovered_committer.reconcile_calls) == 1


@pytest.mark.asyncio
async def test_recovery_preserves_undone_commit_after_apply_was_reverted(
    make_client,
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery-reverted")
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    client, service, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        committer=FakeCommitter(),
        recovery_store=store,
        recovery_enabled=True,
    )
    async with client:
        created = await client.post("/api/coding/sessions")
        session_id = created.json()["id"]
        applied = await client.post(
            f"/api/coding/sessions/{session_id}/apply",
            json={"revision": 1},
        )
        committed = await client.post(
            f"/api/coding/sessions/{session_id}/commit",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "message": "feature: 撤销恢复样例 62E4",
            },
        )
        await client.post(
            f"/api/coding/sessions/{session_id}/commit/undo",
            json={
                "revision": 1,
                "apply_id": applied.json()["apply_id"],
                "commit_id": committed.json()["commit_id"],
            },
        )
        reverted = await client.post(
            f"/api/coding/sessions/{session_id}/apply/revert",
            json={"revision": 1, "apply_id": applied.json()["apply_id"]},
        )
        assert reverted.json()["state"] == "reverted"
    await service.shutdown()

    recovered_committer = FakeCommitter(reconcile_state="conflict")
    recovered_client, _, _ = await make_client(
        worker=FakeWorker(mode="draft"),
        applier=FakeApplier(reconcile_state="not_applied"),
        committer=recovered_committer,
        recovery_store=store,
        recovery_enabled=True,
    )
    async with recovered_client:
        resumed = await recovered_client.post("/api/coding/recovery/resume")
        commit_status = await recovered_client.get(
            f"/api/coding/sessions/{resumed.json()['id']}/commit",
            params={"revision": 1},
        )

    assert resumed.json()["status"] == "reverted"
    assert commit_status.json()["state"] == "undone"
    assert recovered_committer.reconcile_calls == []


@pytest.mark.asyncio
async def test_recovery_conflict_is_read_only_and_preserves_download(
    make_client,
    tmp_path: Path,
) -> None:
    store = CodingRecoveryStore(tmp_path / "recovery-conflict")
    worker = FakeWorker(mode="draft")
    worker.verification_state = "completed"
    worker.verification_result = "passed"
    client, service, _ = await make_client(
        worker=worker,
        applier=FakeApplier(),
        recovery_store=store,
        recovery_enabled=True,
    )
    async with client:
        created = await client.post("/api/coding/sessions")
        applied = await client.post(
            f"/api/coding/sessions/{created.json()['id']}/apply",
            json={"revision": 1},
        )
        assert applied.json()["state"] == "applied"
    await service.shutdown()

    conflict_client, _, _ = await make_client(
        worker=FakeWorker(mode="draft"),
        applier=FakeApplier(reconcile_state="conflict"),
        recovery_store=store,
        recovery_enabled=True,
    )
    async with conflict_client:
        resumed = await conflict_client.post("/api/coding/recovery/resume")
        session_id = resumed.json()["id"]
        turn = await conflict_client.post(
            f"/api/coding/sessions/{session_id}/turns",
            json={"prompt": "不要覆盖人工内容 49B2"},
        )
        changes = await conflict_client.get(
            f"/api/coding/sessions/{session_id}/changes"
        )
        patch_response = await conflict_client.get("/api/coding/recovery/patch")
        discarded = await conflict_client.post("/api/coding/recovery/discard")

    assert resumed.json()["status"] == "conflict"
    assert resumed.json()["conflict"] == "apply_recovery_conflict"
    assert turn.status_code == 409
    assert turn.json()["detail"]["code"] == "recovery_conflict"
    assert changes.status_code == 200
    assert patch_response.status_code == 200
    assert discarded.json() == {"discarded": True}
    assert store.load() is None


def test_recovered_verification_is_revision_bound_and_resanitized() -> None:
    valid = {
        "revision": 7,
        "state": "completed",
        "result": "passed",
        "stale": False,
        "reason": None,
        "started_at": 1700000000.0,
        "finished_at": 1700000001.0,
        "steps": [
            {
                "id": "backend_tests",
                "label": "检查服务代码",
                "state": "completed",
                "result": "passed",
                "duration_ms": 731,
                "summary": "检查通过",
                "details": "38 项检查通过",
                "truncated": False,
            }
        ],
    }

    assert _validate_recovered_verification(
        valid,
        revision=7,
        paths=["server/recovery_case_7f31.py"],
    ) == valid
    failed = json.loads(json.dumps(valid))
    failed["result"] = "failed"
    failed["steps"][0].update(
        {"result": "failed", "duration_ms": None, "summary": "检查未能完成"}
    )
    assert _validate_recovered_verification(
        failed,
        revision=7,
        paths=["server/recovery_case_7f31.py"],
    ) == failed
    with pytest.raises(CodingWorkerError) as stale:
        _validate_recovered_verification(
            valid,
            revision=8,
            paths=["server/recovery_case_7f31.py"],
        )
    assert stale.value.code == "recovery_invalid"

    leaked = json.loads(json.dumps(valid))
    leaked["steps"][0]["details"] = "C:\\private\\repo\\secret.py"
    with pytest.raises(CodingWorkerError) as unsafe:
        _validate_recovered_verification(
            leaked,
            revision=7,
            paths=["server/recovery_case_7f31.py"],
        )
    assert unsafe.value.code == "recovery_invalid"


@pytest.mark.asyncio
async def test_reconcile_clients_reject_inconsistent_socket_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applier = CodingApplierClient(Path("/unused/applier.sock"))
    assert APPLIER_OPERATION_TIMEOUT_SECONDS == 90.0

    async def invalid_apply_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"state": "applied", "receipt": None}

    monkeypatch.setattr(applier, "_request", invalid_apply_response)
    with pytest.raises(ApplierClientError) as invalid_apply:
        await applier.reconcile(
            operation_id="a" * 24,
            revision=4,
            patch="diff --git a/server/a.py b/server/a.py\n",
            paths=["server/a.py"],
            expected_fingerprint=SNAPSHOT_FINGERPRINT,
        )
    assert invalid_apply.value.code == "invalid_response"

    committer = CodingCommitterClient(Path("/unused/committer.sock"))
    assert COMMITTER_OPERATION_TIMEOUT_SECONDS == 90.0

    async def invalid_commit_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"state": "conflict", "receipt": {"unexpected": True}}

    monkeypatch.setattr(committer, "_request", invalid_commit_response)
    apply_receipt = ApplyReceipt(
        apply_id="b" * 24,
        revision=4,
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        files=(
            ApplyFileReceipt(
                path="server/a.py",
                existed_before=True,
                before_sha256="c" * 64,
                after_sha256="d" * 64,
            ),
        ),
    )
    with pytest.raises(CommitterClientError) as invalid_commit:
        await committer.reconcile(
            operation_id="e" * 24,
            apply_receipt=apply_receipt,
            message="feature: 恢复检查 4D2A",
        )
    assert invalid_commit.value.code == "invalid_response"

    publisher = CodingPublisherClient(Path("/unused/publisher.sock"))
    assert PUBLISH_OPERATION_TIMEOUT_SECONDS == 180.0

    async def invalid_publish_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"state": "draft", "receipt": None}

    monkeypatch.setattr(publisher, "_request", invalid_publish_response)
    with pytest.raises(PublisherClientError) as invalid_publish:
        await publisher.reconcile(
            PublishManifest(
                publish_id="p" * 24,
                task_id="t" * 24,
                revision=4,
                snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
                base_sha="1" * 40,
                head_sha="2" * 40,
                commits=(
                    PublishCommit(
                        commit_id="c" * 24,
                        commit_sha="2" * 40,
                        parent_sha="1" * 40,
                        message="docs: validate publisher response 4D2A",
                        files=("docs/publisher-4D2A.md",),
                    ),
                ),
                title="Validate publisher response 4D2A",
                body="",
            )
        )
    assert invalid_publish.value.code == "invalid_response"
