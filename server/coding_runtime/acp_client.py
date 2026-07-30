from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import CodingEvent, CodingEventKind, CodingSession, CodingSessionState

ACP_PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1_048_576
MAX_TEXT_CHUNK = 16_000
MAX_PLAN_ENTRIES = 50


class AcpProtocolError(RuntimeError):
    """The ACP peer sent an invalid or unsupported protocol frame."""


class AcpRequestTimeout(TimeoutError):
    """An ACP request did not complete inside the configured timeout."""


class AcpProcessExited(RuntimeError):
    """The ACP process ended before the active request completed."""


@dataclass(frozen=True, slots=True)
class AcpProcessConfig:
    command: Sequence[str]
    workspace: str = "/workspace"
    process_cwd: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    request_timeout: float = 30.0
    shutdown_timeout: float = 2.0

    def __post_init__(self) -> None:
        if not self.command or not all(isinstance(part, str) and part for part in self.command):
            raise ValueError("ACP command must contain non-empty argv entries")
        if self.workspace != "/workspace":
            raise ValueError("ACP workspace is fixed to /workspace")
        if self.request_timeout <= 0 or self.shutdown_timeout <= 0:
            raise ValueError("ACP timeouts must be positive")


class AcpClient:
    """Minimal ACP v1 client with fail-closed permission and process handling."""

    def __init__(self, config: AcpProcessConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._updates: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._next_request_id = 1
        self._acp_session_id: str | None = None
        self._session: CodingSession | None = None
        self._closed = False
        self._stderr_tail: list[str] = []

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def returncode(self) -> int | None:
        return None if self._process is None else self._process.returncode

    async def open(self, session: CodingSession) -> CodingEvent:
        if self._process is not None:
            raise RuntimeError("ACP client already started")
        self._session = session
        try:
            await self._start_process()
            initialize = await self._request(
                "initialize",
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {},
                    "clientInfo": {
                        "name": "modelmirror-coding-runtime",
                        "version": "1.0.0",
                    },
                },
            )
            if initialize.get("protocolVersion") != ACP_PROTOCOL_VERSION:
                raise AcpProtocolError("ACP protocol version negotiation failed")

            result = await self._request(
                "session/new",
                {
                    "cwd": self._config.workspace,
                    "additionalDirectories": [],
                    "mcpServers": [],
                },
            )
            acp_session_id = result.get("sessionId")
            if not isinstance(acp_session_id, str) or not acp_session_id:
                raise AcpProtocolError("ACP session/new response omitted sessionId")
            self._acp_session_id = acp_session_id
            session.transition(CodingSessionState.READY)
            return session.append_event(CodingEventKind.SESSION_STARTED)
        except BaseException:
            if session.state is CodingSessionState.STARTING:
                session.transition(CodingSessionState.FAILED)
            await self._terminate_process()
            raise

    async def prompt(
        self,
        session: CodingSession,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]:
        if session is not self._session or not self._acp_session_id:
            raise RuntimeError("ACP session is not open")
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")

        turn_id = session.begin_turn()
        prompt_task = asyncio.create_task(
            self._request(
                "session/prompt",
                {
                    "sessionId": self._acp_session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
            )
        )
        await asyncio.sleep(0)
        yield session.append_event(CodingEventKind.TURN_STARTED, turn_id=turn_id)

        try:
            while True:
                if prompt_task.done() and self._updates.empty():
                    break
                update_task = asyncio.create_task(self._updates.get())
                done, _ = await asyncio.wait(
                    {prompt_task, update_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if update_task in done:
                    update_session_id, update = update_task.result()
                    if update_session_id != self._acp_session_id:
                        raise AcpProtocolError("ACP update referenced another session")
                    mapped = self._map_update(session, turn_id, update)
                    if mapped is not None:
                        yield mapped
                else:
                    update_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await update_task

            result = await prompt_task
            stop_reason = result.get("stopReason")
            if stop_reason == "cancelled":
                yield session.append_event(
                    CodingEventKind.CANCELLED,
                    turn_id=turn_id,
                )
            elif stop_reason == "end_turn":
                yield session.append_event(
                    CodingEventKind.TURN_COMPLETED,
                    turn_id=turn_id,
                    data={"stop_reason": stop_reason},
                )
            elif isinstance(stop_reason, str):
                yield session.append_event(
                    CodingEventKind.FAILED,
                    turn_id=turn_id,
                    data={"code": stop_reason},
                )
            else:
                raise AcpProtocolError("ACP prompt response omitted stopReason")
            session.finish_turn()
        except BaseException:
            if not prompt_task.done():
                prompt_task.cancel()
            with contextlib.suppress(BaseException):
                await prompt_task
            if session.state not in {
                CodingSessionState.FAILED,
                CodingSessionState.CLOSED,
            }:
                session.active_turn_id = None
                session.transition(CodingSessionState.FAILED)
            await self._terminate_process()
            raise

    async def cancel(self, session: CodingSession) -> bool:
        if session is not self._session or not self._acp_session_id:
            return False
        accepted = session.request_cancel()
        if accepted:
            await self._notify(
                "session/cancel",
                {"sessionId": self._acp_session_id},
            )
        return accepted

    async def close(self, session: CodingSession) -> None:
        await self._terminate_process()
        if session.state is not CodingSessionState.CLOSED:
            session.active_turn_id = None
            session.transition(CodingSessionState.CLOSED)

    async def _start_process(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._config.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._config.process_cwd,
            env=dict(self._config.environment),
            limit=MAX_FRAME_BYTES,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write_frame(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self._config.request_timeout,
            )
        except TimeoutError as exc:
            raise AcpRequestTimeout(f"ACP request timed out: {method}") from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write_frame(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    async def _write_frame(self, frame: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise AcpProcessExited("ACP process is not running")
        encoded = (
            json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            + b"\n"
        )
        if len(encoded) > MAX_FRAME_BYTES:
            raise AcpProtocolError("ACP frame exceeds size limit")
        async with self._write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()

    async def _reader_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    raise AcpProcessExited("ACP process closed stdout")
                if len(line) > MAX_FRAME_BYTES:
                    raise AcpProtocolError("ACP frame exceeds size limit")
                try:
                    frame = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AcpProtocolError("ACP emitted malformed JSON") from exc
                if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
                    raise AcpProtocolError("ACP emitted an invalid JSON-RPC frame")
                await self._dispatch_frame(frame)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._fail_pending(exc)

    async def _dispatch_frame(self, frame: dict[str, Any]) -> None:
        if "method" in frame:
            method = frame.get("method")
            params = frame.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                raise AcpProtocolError("ACP method frame is invalid")
            if method == "session/update" and "id" not in frame:
                session_id = params.get("sessionId")
                update = params.get("update")
                if not isinstance(session_id, str) or not isinstance(update, dict):
                    raise AcpProtocolError("ACP session/update params are invalid")
                await self._updates.put((session_id, update))
                return
            if method == "session/request_permission" and "id" in frame:
                await self._reject_permission(frame["id"], params)
                return
            if "id" in frame:
                await self._write_frame(
                    {
                        "jsonrpc": "2.0",
                        "id": frame["id"],
                        "error": {"code": -32601, "message": "Method not supported"},
                    }
                )
                return
            raise AcpProtocolError("ACP emitted an unsupported notification")

        response_id = frame.get("id")
        if not isinstance(response_id, int):
            raise AcpProtocolError("ACP response omitted numeric id")
        future = self._pending.get(response_id)
        if future is None:
            raise AcpProtocolError("ACP response referenced an unknown request")
        if "error" in frame:
            error = frame["error"]
            code = error.get("code") if isinstance(error, dict) else "unknown"
            future.set_exception(AcpProtocolError(f"ACP request failed ({code})"))
            return
        result = frame.get("result")
        if not isinstance(result, dict):
            future.set_exception(AcpProtocolError("ACP response result must be an object"))
            return
        future.set_result(result)

    async def _reject_permission(
        self,
        request_id: Any,
        params: dict[str, Any],
    ) -> None:
        options = params.get("options")
        selected_id: str | None = None
        if isinstance(options, list):
            for preferred_kind in ("reject_once", "reject_always"):
                for option in options:
                    if (
                        isinstance(option, dict)
                        and option.get("kind") == preferred_kind
                        and isinstance(option.get("optionId"), str)
                    ):
                        selected_id = option["optionId"]
                        break
                if selected_id is not None:
                    break
        outcome: dict[str, str]
        if selected_id is None:
            outcome = {"outcome": "cancelled"}
        else:
            outcome = {"outcome": "selected", "optionId": selected_id}
        await self._write_frame(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"outcome": outcome},
            }
        )

    def _map_update(
        self,
        session: CodingSession,
        turn_id: str,
        update: dict[str, Any],
    ) -> CodingEvent | None:
        update_kind = update.get("sessionUpdate")
        if update_kind == "agent_message_chunk":
            content = update.get("content")
            if (
                not isinstance(content, dict)
                or content.get("type") != "text"
                or not isinstance(content.get("text"), str)
            ):
                raise AcpProtocolError("ACP agent message chunk is invalid")
            return session.append_event(
                CodingEventKind.ANSWER_DELTA,
                turn_id=turn_id,
                data={"text": content["text"][:MAX_TEXT_CHUNK]},
            )
        if update_kind == "plan":
            entries = update.get("entries")
            if not isinstance(entries, list):
                raise AcpProtocolError("ACP plan update is invalid")
            safe_entries = []
            for entry in entries[:MAX_PLAN_ENTRIES]:
                if not isinstance(entry, dict):
                    continue
                safe_entries.append(
                    {
                        "content": str(entry.get("content", ""))[:1_000],
                        "priority": str(entry.get("priority", ""))[:32],
                        "status": str(entry.get("status", ""))[:32],
                    }
                )
            return session.append_event(
                CodingEventKind.PLAN,
                turn_id=turn_id,
                data={"entries": safe_entries},
            )
        if update_kind in {"tool_call", "tool_call_update"}:
            return session.append_event(
                CodingEventKind.TOOL_STATUS,
                turn_id=turn_id,
                data={
                    "tool_call_id": str(update.get("toolCallId", ""))[:128],
                    "title": str(update.get("title", "Tool activity"))[:200],
                    "kind": str(update.get("kind", "other"))[:32],
                    "status": str(update.get("status", "pending"))[:32],
                },
            )
        return None

    async def _stderr_loop(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                safe_line = line.decode("utf-8", errors="replace").rstrip()[:500]
                self._stderr_tail.append(safe_line)
                del self._stderr_tail[:-20]
        except asyncio.CancelledError:
            raise

    def _fail_pending(self, error: BaseException) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)

    async def _terminate_process(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=self._config.shutdown_timeout,
                    )
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._fail_pending(AcpProcessExited("ACP process closed"))
