"""Passive HTTP timing/counts only. No payloads, I/O, timeouts or retries."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
from time import monotonic
from typing import Any, Callable, Iterator

import httpx


_TRACE_STEPS = {"connection.connect_tcp", "connection.start_tls"} | {
    f"{protocol}.{step}" for protocol in ("http11", "http2")
    for step in ("send_request_headers", "send_request_body", "receive_response_headers", "receive_response_body")
}
_ID_HEADERS = ("x-request-id", "x-openrouter-request-id", "cf-ray")
_ERROR_TYPES = {
    "TimeoutError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectError", "ReadError", "WriteError", "RemoteProtocolError", "LocalProtocolError",
    "CancelledError",
}


class _ObservedStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, observer: TransportEvidence) -> None:
        self.inner = inner
        self.observer = observer

    async def __aiter__(self):
        async for chunk in self.inner:
            self.observer._safe(lambda: self.observer._chunk(len(chunk)))
            yield chunk
        self.observer._safe(lambda: self.observer.body.update(complete=True))

    async def aclose(self) -> None:
        await self.inner.aclose()


class TransportEvidence:
    def __init__(
        self, client_kwargs: dict[str, Any], record: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self.client_kwargs = client_kwargs
        self.request_kwargs: dict[str, Any] = {}
        self.record = record
        self.started: float | None = None
        self.elapsed_ms: float | None = None
        self.headers_ms: float | None = None
        self.status_code: int | None = None
        self.response_count = 0
        self.outcome = "pending"
        self.exception_type: str | None = None
        self.collection_failed = False
        self.trace_events: dict[str, dict[str, Any]] = {}
        self.request_id_hashes: dict[str, str] = {}
        self.body: dict[str, Any] = {
            "observation": "not_started", "raw_bytes": 0, "chunk_count": 0,
            "first_byte_ms": None, "last_byte_ms": None, "idle_ms_at_end": None,
            "complete": False,
        }
        if record is not None:
            # Copy hook containers, never mutate a caller's reusable client options.
            try:
                hooks = {name: list(items) for name, items in client_kwargs.get("event_hooks", {}).items()}
                hooks["response"] = [self._response, *hooks.get("response", [])]
                self.client_kwargs = {**client_kwargs, "event_hooks": hooks}
                self.request_kwargs = {"extensions": {"trace": self._trace}}
            except Exception:
                self.collection_failed = True

    def _safe(self, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except Exception:
            self.collection_failed = True

    def _elapsed(self) -> float:
        if self.started is None:
            raise ValueError("TRANSPORT_CLOCK_UNAVAILABLE")
        return round(max(0.0, monotonic() - self.started) * 1000, 3)

    def _start(self) -> None:
        self.started = monotonic()

    async def _trace(self, name: str, _info: dict[str, Any]) -> None:
        def record() -> None:
            step, _, state = name.rpartition(".")
            if step not in _TRACE_STEPS or state not in {"started", "complete", "failed"}:
                return
            event = self.trace_events.setdefault(name, {"first_ms": self._elapsed(), "count": 0})
            event["count"] += 1
        self._safe(record)

    async def _response(self, response: httpx.Response) -> None:
        def record() -> None:
            self.response_count += 1
            # Redirect/auth chains remain untouched, but are not a single-response proof.
            if self.response_count != 1:
                return
            self.status_code = response.status_code
            self.headers_ms = self._elapsed()
            for name in _ID_HEADERS:
                value = response.headers.get(name)
                if value and len(value) <= 512:
                    self.request_id_hashes[name] = hashlib.sha256(value.encode()).hexdigest()
            if response.is_stream_consumed:
                self.body.update(observation="preloaded", complete=True)
            else:
                response.stream = _ObservedStream(response.stream, self)
                self.body["observation"] = "stream"
        self._safe(record)

    def _chunk(self, size: int) -> None:
        if not size:
            return
        elapsed = self._elapsed()
        self.body["raw_bytes"] += size
        self.body["chunk_count"] += 1
        if self.body["first_byte_ms"] is None:
            self.body["first_byte_ms"] = elapsed
        self.body["last_byte_ms"] = elapsed

    def _finish(self, error: BaseException | None) -> None:
        self.outcome = (
            "completed" if error is None else
            "cancelled" if isinstance(error, asyncio.CancelledError) else
            "timeout" if isinstance(error, (TimeoutError, httpx.TimeoutException)) else "failed"
        )
        if error is not None:
            name = type(error).__name__
            self.exception_type = name if name in _ERROR_TYPES else "other"
        self.elapsed_ms = self._elapsed()
        if self.body["last_byte_ms"] is not None and not self.body["complete"]:
            self.body["idle_ms_at_end"] = max(0.0, round(self.elapsed_ms - self.body["last_byte_ms"], 3))

    def summary(self) -> dict[str, Any]:
        return {
            "version": 1, "status": "partial" if self.collection_failed else "observed",
            "outcome": self.outcome, "exception_type": self.exception_type,
            "phase": "before_response_headers" if not self.response_count else
                     "response_complete" if self.body["complete"] else "response_body",
            "elapsed_ms": self.elapsed_ms, "headers_ms": self.headers_ms,
            "status_code": self.status_code, "response_count": self.response_count,
            "response_scope": "first_response", "multiple_responses": self.response_count > 1,
            "body": dict(self.body), "trace_events": dict(self.trace_events),
            "request_id_hashes": dict(self.request_id_hashes),
        }

    @contextmanager
    def capture(self) -> Iterator[None]:
        if self.record is None:
            yield
            return
        self._safe(self._start)
        failure: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            failure = exc
            raise
        finally:
            self._safe(lambda: self._finish(failure))
            self._safe(lambda: self.record(self.summary()))
