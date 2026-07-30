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
from server.coding_runtime.models import CodingEvent, CodingEventKind
from server.coding_runtime.worker import CodingWorkerError


class FakeWorker:
    def __init__(
        self,
        *,
        configured: bool = True,
        block_turn: bool = False,
        fail_health: bool = False,
    ) -> None:
        self.configured = configured
        self.block_turn = block_turn
        self.fail_health = fail_health
        self.release = asyncio.Event()
        self.cancelled = False
        self.closed: list[str] = []
        self.session_id = "coding-session"

    async def health(self) -> dict[str, Any]:
        if self.fail_health:
            raise CodingWorkerError("unavailable", code="worker_unavailable")
        return {"ok": True, "configured": self.configured}

    async def create_session(self) -> dict[str, Any]:
        return {
            "ok": True,
            "session_id": self.session_id,
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


@pytest_asyncio.fixture
async def make_client():
    services: list[CodingService] = []

    async def factory(
        *,
        enabled: bool = True,
        worker: FakeWorker | None = None,
        ttl_seconds: float = 1800,
    ) -> tuple[httpx.AsyncClient, CodingService, FakeWorker]:
        fake = worker or FakeWorker()
        service = CodingService(
            enabled=enabled,
            worker=fake,
            ttl_seconds=ttl_seconds,
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
