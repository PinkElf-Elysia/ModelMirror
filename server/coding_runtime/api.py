from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import CodingEvent, CodingEventKind
from .worker import CodingWorkerClient, CodingWorkerError

MAX_PROMPT_CHARS = 20_000
SESSION_TTL_SECONDS = 30 * 60
EVENT_BUFFER_SIZE = 256
HEARTBEAT_SECONDS = 15.0
TERMINAL_EVENT_TYPES = {
    CodingEventKind.TURN_COMPLETED.value,
    CodingEventKind.FAILED.value,
    CodingEventKind.CANCELLED.value,
}
ACTIVE_STATES = {"starting", "ready", "running", "cancelling"}
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"'<>]+")
CONTAINER_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|run|opt|tmp|usr|etc|var)/[^\s\"'<>]+"
)
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class WorkerClient(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def create_session(self) -> dict[str, Any]: ...

    def prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]: ...

    async def cancel(self, session_id: str) -> bool: ...

    async def close(self, session_id: str) -> None: ...


class CodingTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)

    @field_validator("prompt")
    @classmethod
    def prompt_must_have_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt must not be empty")
        return value


@dataclass(slots=True)
class CodingApiSession:
    session_id: str
    worker_session_id: str
    state: str = "ready"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=EVENT_BUFFER_SIZE)
    )
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    turn_task: asyncio.Task[None] | None = None
    last_seq: int = 0


class CodingService:
    """Ephemeral single-session API state around the isolated Coding Worker."""

    def __init__(
        self,
        *,
        enabled: bool,
        worker: WorkerClient,
        ttl_seconds: float = SESSION_TTL_SECONDS,
    ) -> None:
        self.enabled = enabled
        self.worker = worker
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, CodingApiSession] = {}
        self._lock = asyncio.Lock()

    async def capabilities(self) -> dict[str, Any]:
        response = {
            "enabled": self.enabled,
            "available": False,
            "mode": "readonly",
            "workspace": "ModelMirror",
            "limits": {
                "max_prompt_chars": MAX_PROMPT_CHARS,
                "max_concurrency": 1,
                "session_ttl_seconds": int(self.ttl_seconds),
            },
        }
        if not self.enabled:
            response["reason"] = "disabled"
            return response
        try:
            health = await self.worker.health()
        except Exception:
            response["reason"] = "worker_unavailable"
            return response
        if health.get("ok") is not True:
            response["reason"] = "worker_unavailable"
            return response
        if health.get("configured") is not True:
            response["reason"] = "not_configured"
            return response
        response["available"] = True
        return response

    async def create_session(self) -> CodingApiSession:
        await self._require_available()
        await self.cleanup_expired()
        async with self._lock:
            if any(record.state in ACTIVE_STATES for record in self._sessions.values()):
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "concurrency_limit",
                )
        try:
            result = await self.worker.create_session()
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        session_id = result.get("session_id")
        event_data = result.get("event")
        if (
            not isinstance(session_id, str)
            or not SAFE_IDENTIFIER.fullmatch(session_id)
            or not isinstance(event_data, dict)
        ):
            if isinstance(session_id, str) and session_id:
                with contextlib.suppress(Exception):
                    await self.worker.close(session_id)
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_worker_response",
            )
        record = CodingApiSession(
            session_id=session_id,
            worker_session_id=session_id,
        )
        initial = _event_from_payload(event_data)
        if (
            initial.kind is not CodingEventKind.SESSION_STARTED
            or initial.seq != 1
            or initial.session_id != session_id
        ):
            with contextlib.suppress(Exception):
                await self.worker.close(session_id)
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "invalid_worker_response",
            )
        await self._append_event(record, initial)
        async with self._lock:
            if any(existing.state in ACTIVE_STATES for existing in self._sessions.values()):
                with contextlib.suppress(Exception):
                    await self.worker.close(session_id)
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "concurrency_limit",
                )
            self._sessions[session_id] = record
        return record

    async def start_turn(self, session_id: str, prompt: str) -> CodingApiSession:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        if record.state != "ready" or (
            record.turn_task is not None and not record.turn_task.done()
        ):
            raise _http_error(status.HTTP_409_CONFLICT, "turn_in_progress")
        record.state = "running"
        record.updated_at = time.time()
        record.turn_task = asyncio.create_task(self._run_turn(record, prompt))
        return record

    async def cancel(self, session_id: str) -> bool:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        task = record.turn_task
        if task is None or task.done() or record.state not in {"running", "cancelling"}:
            return False
        if record.state == "cancelling":
            return False
        try:
            accepted = await self.worker.cancel(record.worker_session_id)
        except CodingWorkerError as exc:
            raise _worker_http_error(exc) from exc
        if accepted:
            record.state = "cancelling"
            record.updated_at = time.time()
        return accepted

    async def stream_events(
        self,
        session_id: str,
        *,
        after: int,
        request: Request,
    ) -> AsyncIterator[str]:
        await self.cleanup_expired()
        record = self._get_session(session_id)
        cursor = after
        while True:
            if await request.is_disconnected():
                return
            pending = [event for event in tuple(record.events) if event["seq"] > cursor]
            terminal_seen = False
            for event in pending:
                cursor = event["seq"]
                terminal_seen = terminal_seen or event["type"] in TERMINAL_EVENT_TYPES
                yield _encode_sse(event)
            if terminal_seen:
                return
            if (
                record.turn_task is not None
                and record.turn_task.done()
                and cursor >= record.last_seq
            ):
                return
            try:
                async with record.condition:
                    await asyncio.wait_for(
                        record.condition.wait(),
                        timeout=HEARTBEAT_SECONDS,
                    )
            except TimeoutError:
                yield ": heartbeat\n\n"

    async def cleanup_expired(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        async with self._lock:
            expired = [
                record
                for record in self._sessions.values()
                if record.state not in {"running", "cancelling"}
                and current - record.updated_at >= self.ttl_seconds
            ]
            for record in expired:
                self._sessions.pop(record.session_id, None)
        for record in expired:
            with contextlib.suppress(Exception):
                await self.worker.close(record.worker_session_id)
        return len(expired)

    async def shutdown(self) -> None:
        async with self._lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        for record in records:
            if record.turn_task is not None and not record.turn_task.done():
                with contextlib.suppress(Exception):
                    await self.worker.cancel(record.worker_session_id)
                record.turn_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await record.turn_task
            with contextlib.suppress(Exception):
                await self.worker.close(record.worker_session_id)

    async def _run_turn(self, record: CodingApiSession, prompt: str) -> None:
        try:
            async for event in self.worker.prompt(record.worker_session_id, prompt):
                await self._append_event(record, event)
                if event.kind in {
                    CodingEventKind.TURN_COMPLETED,
                    CodingEventKind.CANCELLED,
                    CodingEventKind.FAILED,
                }:
                    record.state = "ready"
            if record.state in {"running", "cancelling"}:
                record.state = "ready"
        except asyncio.CancelledError:
            raise
        except CodingWorkerError as exc:
            record.state = "failed"
            await self._append_generated_failure(record, exc.code)
        except Exception:
            record.state = "failed"
            await self._append_generated_failure(record, "agent_turn_failed")
        finally:
            record.updated_at = time.time()
            async with record.condition:
                record.condition.notify_all()

    async def _append_generated_failure(
        self,
        record: CodingApiSession,
        code: str,
    ) -> None:
        event = CodingEvent(
            session_id=record.session_id,
            seq=record.last_seq + 1,
            kind=CodingEventKind.FAILED,
            created_at=time.time(),
            data={"code": _safe_code(code)},
        )
        await self._append_event(record, event)

    async def _append_event(
        self,
        record: CodingApiSession,
        event: CodingEvent,
    ) -> None:
        if event.session_id != record.worker_session_id:
            raise CodingWorkerError(
                "Coding worker event referenced another session.",
                code="invalid_worker_response",
            )
        if (
            event.turn_id is not None
            and not SAFE_IDENTIFIER.fullmatch(event.turn_id)
        ) or not math.isfinite(event.created_at):
            raise CodingWorkerError(
                "Coding worker event identity is invalid.",
                code="invalid_worker_response",
            )
        if event.seq <= record.last_seq:
            raise CodingWorkerError(
                "Coding worker event sequence is invalid.",
                code="invalid_worker_response",
            )
        public_event = _public_event(event)
        record.events.append(public_event)
        record.last_seq = event.seq
        record.updated_at = time.time()
        async with record.condition:
            record.condition.notify_all()

    async def _require_available(self) -> None:
        capabilities = await self.capabilities()
        if capabilities["available"] is not True:
            reason = str(capabilities.get("reason") or "unavailable")
            raise _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, reason)

    def _get_session(self, session_id: str) -> CodingApiSession:
        record = self._sessions.get(session_id)
        if record is None:
            raise _http_error(status.HTTP_404_NOT_FOUND, "session_not_found")
        return record


_service: CodingService | None = None


def configure_coding_service(service: CodingService | None) -> None:
    global _service
    _service = service


def get_coding_service() -> CodingService:
    global _service
    if _service is None:
        enabled = os.getenv("CODING_AGENT_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        socket_path = os.getenv(
            "CODING_AGENT_SOCKET_PATH",
            "/run/modelmirror-coding/coding-runtime.sock",
        )
        _service = CodingService(
            enabled=enabled,
            worker=CodingWorkerClient(Path(socket_path)),
        )
    return _service


@asynccontextmanager
async def _coding_lifespan(_: object) -> AsyncIterator[None]:
    try:
        yield
    finally:
        if _service is not None:
            await _service.shutdown()


router = APIRouter(
    prefix="/api/coding",
    tags=["coding"],
    lifespan=_coding_lifespan,
)


@router.get("/capabilities")
async def coding_capabilities() -> dict[str, Any]:
    return await get_coding_service().capabilities()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_coding_session() -> dict[str, Any]:
    record = await get_coding_service().create_session()
    return {"id": record.session_id, "status": record.state}


@router.post(
    "/sessions/{session_id}/turns",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_coding_turn(
    session_id: str,
    payload: CodingTurnRequest,
) -> dict[str, Any]:
    record = await get_coding_service().start_turn(session_id, payload.prompt)
    return {"accepted": True, "status": record.state}


@router.get("/sessions/{session_id}/events")
async def coding_session_events(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    stream = get_coding_service().stream_events(
        session_id,
        after=after,
        request=request,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/cancel")
async def cancel_coding_session(session_id: str) -> dict[str, Any]:
    accepted = await get_coding_service().cancel(session_id)
    return {"accepted": accepted}


def _event_from_payload(payload: dict[str, Any]) -> CodingEvent:
    try:
        session_id = payload["session_id"]
        event_type = payload["type"]
        if (
            not isinstance(session_id, str)
            or not SAFE_IDENTIFIER.fullmatch(session_id)
            or not isinstance(event_type, str)
        ):
            raise ValueError("invalid event identity")
        turn_id = payload.get("turn_id")
        if turn_id is not None and (
            not isinstance(turn_id, str) or not SAFE_IDENTIFIER.fullmatch(turn_id)
        ):
            raise ValueError("invalid turn identity")
        data = payload.get("data")
        return CodingEvent(
            session_id=session_id,
            seq=int(payload["seq"]),
            kind=CodingEventKind(event_type),
            created_at=float(payload["created_at"]),
            turn_id=turn_id,
            data=dict(data) if isinstance(data, dict) else {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "invalid_worker_response",
        ) from exc


def _public_event(event: CodingEvent) -> dict[str, Any]:
    data = event.data
    public_data: dict[str, Any]
    if event.kind is CodingEventKind.PLAN:
        entries = data.get("entries")
        public_entries = []
        if isinstance(entries, list):
            for entry in entries[:50]:
                if not isinstance(entry, dict):
                    continue
                public_entries.append(
                    {
                        "content": _sanitize_text(entry.get("content"), 1_000),
                        "priority": _sanitize_text(entry.get("priority"), 32),
                        "status": _sanitize_text(entry.get("status"), 32),
                    }
                )
        public_data = {"entries": public_entries}
    elif event.kind is CodingEventKind.ANSWER_DELTA:
        public_data = {"text": _sanitize_text(data.get("text"), 16_000)}
    elif event.kind is CodingEventKind.TOOL_STATUS:
        public_data = {
            "tool_call_id": _sanitize_text(data.get("tool_call_id"), 128),
            "title": _sanitize_text(data.get("title"), 200),
            "kind": _sanitize_text(data.get("kind"), 32),
            "status": _sanitize_text(data.get("status"), 32),
        }
    elif event.kind is CodingEventKind.TURN_COMPLETED:
        public_data = {
            "stop_reason": _safe_code(data.get("stop_reason")),
        }
    elif event.kind is CodingEventKind.FAILED:
        public_data = {"code": _safe_code(data.get("code"))}
    else:
        public_data = {}
    return {
        "session_id": event.session_id,
        "seq": event.seq,
        "type": event.kind.value,
        "created_at": event.created_at,
        "turn_id": event.turn_id,
        "data": public_data,
    }


def _sanitize_text(value: Any, limit: int) -> str:
    text = str(value or "")
    text = text.replace("\\workspace", "[workspace]")
    text = text.replace("/workspace", "[workspace]")
    text = WINDOWS_ABSOLUTE_PATH.sub("[redacted-path]", text)
    text = CONTAINER_ABSOLUTE_PATH.sub("[redacted-path]", text)
    return text[:limit]


def _safe_code(value: Any) -> str:
    code = re.sub(r"[^a-z0-9_-]", "_", str(value or "unknown").lower())
    return code[:64] or "unknown"


def _encode_sse(event: dict[str, Any]) -> str:
    return (
        f"id: {event['seq']}\n"
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _http_error(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": _safe_code(code)})


def _worker_http_error(exc: CodingWorkerError) -> HTTPException:
    if exc.code in {"concurrency_limit", "turn_in_progress"}:
        return _http_error(status.HTTP_409_CONFLICT, exc.code)
    if exc.code in {"invalid_prompt", "prompt_too_long", "invalid_request"}:
        return _http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code)
    if exc.code == "session_not_found":
        return _http_error(status.HTTP_404_NOT_FOUND, exc.code)
    return _http_error(status.HTTP_503_SERVICE_UNAVAILABLE, exc.code)
