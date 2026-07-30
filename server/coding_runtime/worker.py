from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acp_client import AcpClient, AcpProcessConfig
from .models import CodingEvent, CodingSession, CodingSessionState

MAX_WORKER_FRAME_BYTES = 128 * 1024
MAX_PROMPT_CHARS = 20_000
SOCKET_PATH = Path(
    os.getenv(
        "CODING_AGENT_SOCKET_PATH",
        "/run/modelmirror-coding/coding-runtime.sock",
    )
)
WORKSPACE_PATH = "/workspace"
OPENCODE_PATH = "/usr/local/bin/opencode"
INTERNAL_GATEWAY_BASE_URL = "http://new-api:3000/v1"
SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


class CodingWorkerError(RuntimeError):
    def __init__(self, message: str, *, code: str = "worker_error") -> None:
        super().__init__(message)
        self.code = code


class CodingWorkerProtocolError(CodingWorkerError):
    pass


def _read_only_permission() -> dict[str, Any]:
    return {
        "*": "deny",
        "read": {
            "*": "allow",
            "**/.git": "deny",
            "**/.git/**": "deny",
            "**/.env": "deny",
            "**/.env.*": "deny",
            "**/*.pem": "deny",
            "**/*.key": "deny",
            "**/storage/**": "deny",
            "**/uploads/**": "deny",
            "**/new-api-data/**": "deny",
        },
        "list": "allow",
        "glob": "allow",
        "grep": "allow",
        "lsp": "allow",
        "edit": "deny",
        "bash": "deny",
        "task": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "skill": "deny",
        "external_directory": "deny",
        "question": "deny",
        "todowrite": "deny",
        "doom_loop": "deny",
    }


def build_opencode_config(model_id: str) -> dict[str, Any]:
    if not SAFE_MODEL_ID.fullmatch(model_id):
        raise CodingWorkerError(
            "Coding Agent model is not configured safely.",
            code="not_configured",
        )
    permission = _read_only_permission()
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"modelmirror/{model_id}",
        "default_agent": "readonly",
        "agent": {
            "readonly": {
                "description": "Read-only ModelMirror repository analyst",
                "mode": "primary",
                "permission": permission,
            }
        },
        "permission": permission,
        "provider": {
            "modelmirror": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "ModelMirror Internal Gateway",
                "options": {
                    "baseURL": INTERNAL_GATEWAY_BASE_URL,
                    "apiKey": "{env:CODING_AGENT_GATEWAY_KEY}",
                },
                "models": {
                    model_id: {
                        "name": "ModelMirror Coding Model",
                    }
                },
            }
        },
        "plugin": [],
        "mcp": {},
        "instructions": [],
        "share": "disabled",
        "autoupdate": False,
    }
def create_acp_client() -> AcpClient:
    model_id = os.getenv("CODING_AGENT_MODEL", "").strip()
    gateway_key = os.getenv("CODING_AGENT_GATEWAY_KEY", "").strip()
    if not gateway_key:
        raise CodingWorkerError(
            "Coding Agent gateway key is not configured.",
            code="not_configured",
        )
    config_content = json.dumps(
        build_opencode_config(model_id),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    child_environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/coding",
        "OPENCODE_TEST_HOME": "/home/coding",
        "XDG_CONFIG_HOME": "/home/coding/.config",
        "XDG_DATA_HOME": "/home/coding/.local/share",
        "XDG_STATE_HOME": "/home/coding/.local/state",
        "XDG_CACHE_HOME": "/home/coding/.cache",
        "OPENCODE_CONFIG_CONTENT": config_content,
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_PURE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_AUTOCOMPACT": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_AUTH_CONTENT": "{}",
        "CODING_AGENT_GATEWAY_KEY": gateway_key,
        "NO_PROXY": "new-api,localhost,127.0.0.1",
        "no_proxy": "new-api,localhost,127.0.0.1",
    }
    return AcpClient(
        AcpProcessConfig(
            command=(OPENCODE_PATH, "acp", "--cwd", WORKSPACE_PATH),
            workspace=WORKSPACE_PATH,
            process_cwd=WORKSPACE_PATH,
            environment=child_environment,
            request_timeout=120.0,
            shutdown_timeout=5.0,
        )
    )


@dataclass(slots=True)
class _WorkerSession:
    session: CodingSession
    adapter: AcpClient
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CodingWorkerServer:
    """Single-instance Unix socket host for one read-only ACP session."""

    def __init__(self, socket_path: Path = SOCKET_PATH) -> None:
        self._socket_path = socket_path
        self._sessions: dict[str, _WorkerSession] = {}
        self._sessions_lock = asyncio.Lock()

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_WORKER_FRAME_BYTES:
                raise CodingWorkerProtocolError(
                    "Coding worker request is empty or too large.",
                    code="invalid_request",
                )
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise CodingWorkerProtocolError(
                    "Coding worker request must be an object.",
                    code="invalid_request",
                )
            action = request.get("action")
            if action == "health":
                await self._send(
                    writer,
                    {
                        "ok": True,
                        "configured": bool(
                            os.getenv("CODING_AGENT_MODEL", "").strip()
                            and os.getenv("CODING_AGENT_GATEWAY_KEY", "").strip()
                        ),
                        "version": 1,
                    },
                )
            elif action == "create_session":
                await self._create_session(writer)
            elif action == "prompt":
                await self._prompt(request, writer)
            elif action == "cancel":
                await self._cancel(request, writer)
            elif action == "close":
                await self._close_session(request, writer)
            else:
                raise CodingWorkerProtocolError(
                    "Unsupported coding worker action.",
                    code="invalid_request",
                )
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._send_error(writer, "invalid_request")
        except CodingWorkerError as exc:
            await self._send_error(writer, exc.code)
        except Exception:
            await self._send_error(writer, "worker_internal_error")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def close(self) -> None:
        async with self._sessions_lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        for record in records:
            with contextlib.suppress(Exception):
                await record.adapter.close(record.session)

    async def _create_session(self, writer: asyncio.StreamWriter) -> None:
        async with self._sessions_lock:
            if self._sessions:
                raise CodingWorkerError(
                    "Coding runtime already has an active session.",
                    code="concurrency_limit",
                )
            session = CodingSession()
            adapter = create_acp_client()
            record = _WorkerSession(session=session, adapter=adapter)
            self._sessions[session.session_id] = record
        try:
            event = await adapter.open(session)
        except Exception:
            async with self._sessions_lock:
                self._sessions.pop(session.session_id, None)
            raise CodingWorkerError(
                "Coding runtime could not start the agent.",
                code="agent_unavailable",
            )
        await self._send(
            writer,
            {
                "ok": True,
                "session_id": session.session_id,
                "event": event.to_dict(),
            },
        )

    async def _prompt(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_session(request)
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise CodingWorkerProtocolError(
                "Prompt must not be empty.",
                code="invalid_prompt",
            )
        if len(prompt) > MAX_PROMPT_CHARS:
            raise CodingWorkerProtocolError(
                "Prompt exceeds the configured limit.",
                code="prompt_too_long",
            )
        if record.turn_lock.locked():
            raise CodingWorkerError(
                "Coding runtime already has an active turn.",
                code="concurrency_limit",
            )
        async with record.turn_lock:
            try:
                async for event in record.adapter.prompt(record.session, prompt):
                    await self._send(writer, {"ok": True, "event": event.to_dict()})
                await self._send(writer, {"ok": True, "done": True})
            except Exception:
                with contextlib.suppress(Exception):
                    await record.adapter.close(record.session)
                async with self._sessions_lock:
                    self._sessions.pop(record.session.session_id, None)
                raise CodingWorkerError(
                    "Coding Agent turn failed.",
                    code="agent_turn_failed",
                )

    async def _cancel(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_session(request)
        accepted = await record.adapter.cancel(record.session)
        await self._send(writer, {"ok": True, "accepted": accepted})

    async def _close_session(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CodingWorkerProtocolError(
                "Session id is required.",
                code="invalid_request",
            )
        async with self._sessions_lock:
            record = self._sessions.pop(session_id, None)
        if record is None:
            raise CodingWorkerError(
                "Coding session is not available.",
                code="session_not_found",
            )
        await record.adapter.close(record.session)
        await self._send(writer, {"ok": True})

    def _require_session(self, request: dict[str, Any]) -> _WorkerSession:
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CodingWorkerProtocolError(
                "Session id is required.",
                code="invalid_request",
            )
        record = self._sessions.get(session_id)
        if record is None or record.session.state in {
            CodingSessionState.FAILED,
            CodingSessionState.CLOSED,
        }:
            raise CodingWorkerError(
                "Coding session is not available.",
                code="session_not_found",
            )
        return record

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        if len(encoded) > MAX_WORKER_FRAME_BYTES:
            raise CodingWorkerProtocolError(
                "Coding worker response is too large.",
                code="response_too_large",
            )
        writer.write(encoded)
        await writer.drain()

    async def _send_error(self, writer: asyncio.StreamWriter, code: str) -> None:
        await self._send(
            writer,
            {
                "ok": False,
                "code": code,
                "error": "Coding runtime request failed.",
            },
        )

    async def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self._socket_path),
            limit=MAX_WORKER_FRAME_BYTES + 1,
        )
        os.chmod(self._socket_path, 0o660)
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self.close()
            self._socket_path.unlink(missing_ok=True)


class CodingWorkerClient:
    """FastAPI-side client for the private Coding Runtime Unix socket."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._socket_path = str(socket_path)
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"})

    async def create_session(self) -> dict[str, Any]:
        return await self._request({"action": "create_session"}, timeout=130.0)

    async def cancel(self, session_id: str) -> bool:
        result = await self._request(
            {"action": "cancel", "session_id": session_id}
        )
        return result.get("accepted") is True

    async def close(self, session_id: str) -> None:
        await self._request({"action": "close", "session_id": session_id})

    async def prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]:
        reader, writer = await self._connect()
        try:
            await self._write(
                writer,
                {
                    "action": "prompt",
                    "session_id": session_id,
                    "prompt": prompt,
                },
            )
            while True:
                frame = await self._read(reader)
                self._raise_for_error(frame)
                if frame.get("done") is True:
                    return
                event_data = frame.get("event")
                if not isinstance(event_data, dict):
                    raise CodingWorkerProtocolError(
                        "Coding worker omitted an event.",
                        code="invalid_response",
                    )
                yield _event_from_dict(event_data)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        reader, writer = await self._connect()
        try:
            await self._write(writer, payload)
            frame = await asyncio.wait_for(
                self._read(reader),
                timeout=timeout or self._timeout,
            )
            self._raise_for_error(frame)
            return frame
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _connect(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(
                    self._socket_path,
                    limit=MAX_WORKER_FRAME_BYTES + 1,
                ),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as exc:
            raise CodingWorkerError(
                "Coding runtime is unavailable.",
                code="worker_unavailable",
            ) from exc

    @staticmethod
    async def _write(
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
    ) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        if len(encoded) > MAX_WORKER_FRAME_BYTES:
            raise CodingWorkerProtocolError(
                "Coding worker request is too large.",
                code="invalid_request",
            )
        writer.write(encoded)
        await writer.drain()

    @staticmethod
    async def _read(reader: asyncio.StreamReader) -> dict[str, Any]:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_WORKER_FRAME_BYTES:
            raise CodingWorkerProtocolError(
                "Coding worker response is invalid.",
                code="invalid_response",
            )
        try:
            frame = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodingWorkerProtocolError(
                "Coding worker response is invalid.",
                code="invalid_response",
            ) from exc
        if not isinstance(frame, dict):
            raise CodingWorkerProtocolError(
                "Coding worker response must be an object.",
                code="invalid_response",
            )
        return frame

    @staticmethod
    def _raise_for_error(frame: dict[str, Any]) -> None:
        if frame.get("ok") is True:
            return
        code = frame.get("code")
        raise CodingWorkerError(
            "Coding runtime request failed.",
            code=code if isinstance(code, str) else "worker_error",
        )


def _event_from_dict(payload: dict[str, Any]) -> CodingEvent:
    from .models import CodingEventKind

    try:
        return CodingEvent(
            session_id=str(payload["session_id"]),
            seq=int(payload["seq"]),
            kind=CodingEventKind(str(payload["type"])),
            created_at=float(payload["created_at"]),
            turn_id=(
                str(payload["turn_id"])
                if payload.get("turn_id") is not None
                else None
            ),
            data=dict(payload.get("data") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CodingWorkerProtocolError(
            "Coding worker event is invalid.",
            code="invalid_response",
        ) from exc


async def main() -> None:
    await CodingWorkerServer().serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
