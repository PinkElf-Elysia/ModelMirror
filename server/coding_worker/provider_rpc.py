from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import Field

from .broker_rpc import BrokerRPCServer
from .contracts import SAFE_ID, StrictModel
from .provider import (
    CodingAgentProvider,
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderEvent,
    ProviderOpenRequest,
    ProviderSession,
)
from .executor import ExecutorSidecarClientPool, SidecarExecutor


MAX_PROVIDER_RPC_BYTES = 8 * 1024 * 1024
_CLIENT_DISCONNECTED = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


class ProviderRPCError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ProviderRPCRequest(StrictModel):
    token: str = Field(min_length=32, max_length=256)
    action: str = Field(pattern=r"^[a-z_]{1,32}$")
    payload: dict[str, Any] = Field(default_factory=dict)


class ProviderRPCServer:
    """Single-slot provider host. Only provider-neutral frames cross this boundary."""

    def __init__(
        self,
        provider: CodingAgentProvider,
        *,
        token: str,
        bind_broker: Callable[[str, str, str], None] | None = None,
        unbind_broker: Callable[[str], None] | None = None,
        executor: SidecarExecutor | None = None,
        harness_identity: tuple[str, str, str] | None = None,
    ) -> None:
        if len(token) < 32:
            raise ValueError("provider RPC token is too short")
        self.provider = provider
        self._token = token
        self._bind_broker = bind_broker
        self._unbind_broker = unbind_broker
        self._executor = executor
        if harness_identity is not None:
            route_id, model_id, engine = harness_identity
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", route_id)
                is None
                or not model_id.strip()
                or engine not in {"opencode-1.18.9", "claude-code-2.1.89"}
            ):
                raise ValueError("provider Harness identity is invalid")
            self._harness_identity = (route_id, model_id, engine)
        else:
            self._harness_identity = None
        self._harness_generation = secrets.token_hex(16)
        self._server: asyncio.AbstractServer | None = None
        self.endpoint: str | None = None
        self._active_task_id: str | None = None
        self._active_session: ProviderSession | None = None
        self._controller_id: str | None = None
        self._controller_generation = 0
        self._lock = asyncio.Lock()
        self._connections: dict[asyncio.Task[None], asyncio.StreamWriter] = {}

    async def start_unix(self, socket_path: Path) -> str:
        if self._server is not None:
            raise RuntimeError("provider RPC server is already running")
        socket_path = Path(socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(socket_path), limit=MAX_PROVIDER_RPC_BYTES
        )
        socket_path.chmod(0o660)
        self.endpoint = f"unix:{socket_path}"
        return self.endpoint

    async def start_tcp_for_tests(self) -> str:
        if self._server is not None:
            raise RuntimeError("provider RPC server is already running")
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, limit=MAX_PROVIDER_RPC_BYTES
        )
        address = self._server.sockets[0].getsockname()
        self.endpoint = f"tcp:127.0.0.1:{address[1]}"
        return self.endpoint

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        connections = tuple(self._connections.items())
        for task, writer in connections:
            writer.close()
            task.cancel()
        if connections:
            await asyncio.wait(tuple(task for task, _writer in connections), timeout=2)
        if self._server is not None:
            await asyncio.wait_for(self._server.wait_closed(), timeout=2)
        self._server = None
        self.endpoint = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        current = asyncio.current_task()
        if current is not None:
            self._connections[current] = writer
        try:
            request = await self._read_request(reader)
            if request.action == "message":
                session = ProviderSession.model_validate(request.payload.get("session"))
                text = request.payload.get("text")
                if not isinstance(text, str):
                    raise ProviderRPCError(
                        "Provider message is invalid.", code="provider_request_invalid"
                    )
                controller_id, controller_generation = self._controller_binding(
                    request.payload
                )
                self._require_active_session(
                    session, controller_id, controller_generation
                )
                async for event in self.provider.message(session, text):
                    self._require_active_session(
                        session, controller_id, controller_generation
                    )
                    await self._write(writer, {"ok": True, "event": event.model_dump(mode="json")})
                await self._write(writer, {"ok": True, "done": True})
                return
            result = await self._dispatch(request)
            await self._write(writer, {"ok": True, "result": result})
        except _CLIENT_DISCONNECTED:
            # Parking a turn closes the streaming client deliberately.  The
            # provider may observe that close between yielding an event and
            # draining it; it is a normal cancellation boundary, not a
            # sidecar failure that should be written back to a dead socket.
            return
        except Exception as exc:
            with contextlib.suppress(*_CLIENT_DISCONNECTED):
                await self._write(
                    writer,
                    {
                        "ok": False,
                        "error": {
                            "code": getattr(exc, "code", "provider_failed"),
                            "message": str(exc)
                            if isinstance(exc, (ProviderRPCError, ValueError))
                            else "Provider request failed.",
                        },
                    },
                )
        finally:
            writer.close()
            with contextlib.suppress(
                asyncio.TimeoutError,
                *_CLIENT_DISCONNECTED,
            ):
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            if current is not None:
                self._connections.pop(current, None)

    async def _dispatch(self, request: ProviderRPCRequest) -> dict[str, Any]:
        if request.action == "harness_attestation":
            from .harness_v3 import (
                PROVIDER_HARNESS_CODE_FILES,
                harness_code_bundle_sha256,
            )

            if os.getenv("CODING_WORKER_HARNESS_V3_ENABLED", "").lower() != "true":
                raise ProviderRPCError(
                    "Provider Harness attestation is disabled.",
                    code="harness_attestation_disabled",
                )
            if self._harness_identity is None:
                raise ProviderRPCError(
                    "Provider Harness attestation is unavailable.",
                    code="harness_attestation_unavailable",
                )
            route_id, model_id, engine = self._harness_identity
            return {
                "route_id": route_id,
                "model_identity_sha256": hashlib.sha256(
                    model_id.encode("utf-8")
                ).hexdigest(),
                "engine": engine,
                "sidecar_generation": self._harness_generation,
                "code_bundle_sha256": harness_code_bundle_sha256(
                    Path(__file__).resolve().parent,
                    PROVIDER_HARNESS_CODE_FILES,
                ),
            }
        if request.action == "capabilities":
            return (await self.provider.capabilities()).model_dump(mode="json")
        if request.action == "execute_process":
            executor = self._require_executor()
            controller_id, controller_generation = self._controller_binding(
                request.payload
            )
            self._require_active(
                str(request.payload.get("task_id", "")),
                controller_id,
                controller_generation,
            )
            return await executor.run_process(
                task_id=str(request.payload.get("task_id", "")),
                workspace_id=str(request.payload.get("workspace_id", "")),
                argv=tuple(request.payload.get("argv", ())),
                timeout_seconds=int(request.payload.get("timeout_seconds", 0)),
                isolated=request.payload.get("isolated") is True,
                environment_overrides=request.payload.get("environment_overrides"),
            )
        if request.action == "start_service":
            executor = self._require_executor()
            controller_id, controller_generation = self._controller_binding(
                request.payload
            )
            self._require_active(
                str(request.payload.get("task_id", "")),
                controller_id,
                controller_generation,
            )
            return await executor.start_service(
                task_id=str(request.payload.get("task_id", "")),
                workspace_id=str(request.payload.get("workspace_id", "")),
                argv=tuple(request.payload.get("argv", ())),
                ttl_seconds=int(request.payload.get("ttl_seconds", 0)),
                preview_port=(
                    int(request.payload["preview_port"])
                    if request.payload.get("preview_port") is not None
                    else None
                ),
            )
        if request.action == "service_status":
            controller_id, controller_generation = self._controller_binding(
                request.payload
            )
            self._require_active(
                str(request.payload.get("task_id", "")),
                controller_id,
                controller_generation,
            )
            return self._require_executor().service_status(
                task_id=str(request.payload.get("task_id", "")),
                service_id=str(request.payload.get("service_id", "")),
            )
        if request.action == "service_input":
            controller_id, controller_generation = self._controller_binding(
                request.payload
            )
            self._require_active(
                str(request.payload.get("task_id", "")),
                controller_id,
                controller_generation,
            )
            await self._require_executor().service_input(
                task_id=str(request.payload.get("task_id", "")),
                service_id=str(request.payload.get("service_id", "")),
                data=str(request.payload.get("data", "")),
            )
            return {"accepted": True}
        if request.action == "stop_service":
            controller_id, controller_generation = self._controller_binding(
                request.payload
            )
            self._require_active(
                str(request.payload.get("task_id", "")),
                controller_id,
                controller_generation,
            )
            return await self._require_executor().stop_service(
                task_id=str(request.payload.get("task_id", "")),
                service_id=str(request.payload.get("service_id", "")),
            )
        if request.action in {"open", "restore"}:
            opened = ProviderOpenRequest.model_validate(request.payload.get("request"))
            broker_endpoint = request.payload.get("broker_endpoint")
            broker_token = request.payload.get("broker_token")
            controller_id, controller_generation = self._controller_binding(
                request.payload
            )
            if not isinstance(broker_endpoint, str) or not isinstance(broker_token, str):
                raise ProviderRPCError(
                    "Tool Broker binding is missing.", code="tool_broker_unavailable"
                )
            async with self._lock:
                if controller_generation < self._controller_generation or (
                    controller_generation == self._controller_generation
                    and self._controller_id not in {None, controller_id}
                ):
                    raise ProviderRPCError(
                        "Provider controller is stale.",
                        code="provider_controller_stale",
                    )
                if controller_generation > self._controller_generation:
                    self._controller_generation = controller_generation
                    self._controller_id = controller_id
                    await self._release_active()
                elif self._active_task_id is not None:
                    if self._controller_id == controller_id:
                        raise ProviderRPCError(
                            "Provider slot is busy.", code="provider_slot_busy"
                        )
                    raise ProviderRPCError(
                        "Provider controller is stale.",
                        code="provider_controller_stale",
                    )
                elif self._controller_id is None:
                    self._controller_generation = controller_generation
                    self._controller_id = controller_id
                if self._bind_broker is not None:
                    self._bind_broker(opened.task_id, broker_endpoint, broker_token)
                try:
                    if request.action == "restore":
                        checkpoint = ProviderCheckpoint.model_validate(
                            request.payload.get("checkpoint")
                        )
                        session = await self.provider.restore(opened, checkpoint)
                    else:
                        session = await self.provider.open(opened)
                except Exception:
                    if self._unbind_broker is not None:
                        self._unbind_broker(opened.task_id)
                    raise
                self._active_task_id = opened.task_id
                self._active_session = session
                return session.model_dump(mode="json")
        session = ProviderSession.model_validate(request.payload.get("session"))
        controller_id, controller_generation = self._controller_binding(
            request.payload
        )
        self._require_active_session(session, controller_id, controller_generation)
        if request.action == "cancel":
            return {"cancelled": await self.provider.cancel(session)}
        if request.action == "interrupt_turn":
            return {"interrupted": await self.provider.interrupt_turn(session)}
        if request.action == "checkpoint":
            return (await self.provider.checkpoint(session)).model_dump(mode="json")
        if request.action == "close":
            await self.provider.close(session)
            if self._executor is not None:
                await self._executor.stop_task(session.task_id)
            async with self._lock:
                if self._active_session == session:
                    self._active_task_id = None
                    self._active_session = None
                if self._unbind_broker is not None:
                    self._unbind_broker(session.task_id)
            return {"closed": True}
        raise ProviderRPCError(
            "Provider action is invalid.", code="provider_request_invalid"
        )

    def _require_executor(self) -> SidecarExecutor:
        if self._executor is None:
            raise ProviderRPCError(
                "Provider executor is unavailable.", code="executor_unavailable"
            )
        return self._executor

    def _require_active(
        self, task_id: str, controller_id: str, controller_generation: int
    ) -> None:
        if (
            self._active_task_id != task_id
            or self._controller_id != controller_id
            or self._controller_generation != controller_generation
        ):
            raise ProviderRPCError(
                "Provider session was not found.", code="session_not_found"
            )

    def _require_active_session(
        self,
        session: ProviderSession,
        controller_id: str,
        controller_generation: int,
    ) -> None:
        self._require_active(session.task_id, controller_id, controller_generation)
        if self._active_session != session:
            raise ProviderRPCError(
                "Provider session was not found.", code="session_not_found"
            )

    async def _release_active(self) -> None:
        session = self._active_session
        task_id = self._active_task_id
        if session is not None:
            with contextlib.suppress(Exception):
                await self.provider.cancel(session)
            with contextlib.suppress(Exception):
                await self.provider.close(session)
        if self._executor is not None and task_id is not None:
            with contextlib.suppress(Exception):
                await self._executor.stop_task(task_id)
        if self._unbind_broker is not None and task_id is not None:
            self._unbind_broker(task_id)
        self._active_task_id = None
        self._active_session = None

    @staticmethod
    def _controller_binding(payload: Mapping[str, Any]) -> tuple[str, int]:
        controller_id = payload.get("controller_id")
        generation = payload.get("controller_generation")
        if (
            not isinstance(controller_id, str)
            or SAFE_ID.fullmatch(controller_id) is None
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise ProviderRPCError(
                "Provider controller binding is invalid.",
                code="provider_request_invalid",
            )
        return controller_id, generation

    async def _read_request(self, reader: asyncio.StreamReader) -> ProviderRPCRequest:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_PROVIDER_RPC_BYTES or not raw.endswith(b"\n"):
            raise ProviderRPCError(
                "Provider request is invalid.", code="provider_request_invalid"
            )
        request = ProviderRPCRequest.model_validate_json(raw)
        if not secrets.compare_digest(request.token, self._token):
            raise ProviderRPCError(
                "Provider authentication failed.", code="provider_unauthorized"
            )
        return request

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_PROVIDER_RPC_BYTES:
            encoded = b'{"ok":false,"error":{"code":"provider_response_too_large","message":"Provider response is too large."}}\n'
        writer.write(encoded)
        await writer.drain()


class ProviderSidecarClientPool(CodingAgentProvider):
    """Routes each workspace to its fixed provider sidecar slot."""

    def __init__(
        self,
        *,
        endpoints: Mapping[str, str],
        tokens: Mapping[str, str],
        workspace_slot_resolver: Callable[[str], str],
        broker_rpc: BrokerRPCServer,
        executor_pool: ExecutorSidecarClientPool | None = None,
        controller_id: str = "controller_local",
        controller_generation: int = 1,
    ) -> None:
        if set(endpoints) != set(tokens) or not endpoints:
            raise ValueError("provider sidecar bindings are incomplete")
        self._endpoints = dict(endpoints)
        self._tokens = dict(tokens)
        self._workspace_slot_resolver = workspace_slot_resolver
        self._broker_rpc = broker_rpc
        self._executor_pool = executor_pool
        if (
            SAFE_ID.fullmatch(controller_id) is None
            or isinstance(controller_generation, bool)
            or not isinstance(controller_generation, int)
            or controller_generation < 1
        ):
            raise ValueError("provider controller binding is invalid")
        self._controller_id = controller_id
        self._controller_generation = controller_generation
        self._sessions: dict[str, tuple[str, str, str]] = {}

    async def capabilities(self) -> ProviderCapabilities:
        values = [
            ProviderCapabilities.model_validate(
                await self._call(slot_id, "capabilities", {})
            )
            for slot_id in self._endpoints
        ]
        first = values[0]
        if any(value != first for value in values[1:]):
            raise ProviderRPCError(
                "Provider slot capabilities differ.", code="provider_capability_mismatch"
            )
        return first

    @property
    def controller_generation(self) -> int:
        return self._controller_generation

    async def slot_capabilities(
        self,
    ) -> dict[str, ProviderCapabilities | None]:
        """Return one fail-closed observation per configured provider slot."""

        observations: dict[str, ProviderCapabilities | None] = {}
        for slot_id in self._endpoints:
            try:
                observations[slot_id] = ProviderCapabilities.model_validate(
                    await self._call(slot_id, "capabilities", {})
                )
            except (OSError, ValueError, ProviderRPCError, asyncio.TimeoutError):
                observations[slot_id] = None
        return observations

    async def harness_attestations(self) -> dict[str, dict[str, Any]]:
        return {
            slot_id: await self._call(slot_id, "harness_attestation", {})
            for slot_id in sorted(self._endpoints)
        }

    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        return await self._open_or_restore(request, None)

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        return await self._open_or_restore(request, checkpoint)

    async def _open_or_restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint | None
    ) -> ProviderSession:
        slot_id = self._workspace_slot_resolver(request.workspace_id)
        broker_endpoint = self._broker_rpc.endpoint
        if slot_id not in self._endpoints or broker_endpoint is None:
            raise ProviderRPCError(
                "Provider slot is unavailable.", code="provider_unavailable"
            )
        broker_token = self._broker_rpc.register_task(request.task_id)
        if self._executor_pool is not None:
            try:
                await self._executor_pool.bind_task(
                    request.task_id, request.workspace_id
                )
            except Exception:
                self._broker_rpc.revoke_task(request.task_id)
                raise
        payload: dict[str, Any] = {
            "request": request.model_dump(mode="json"),
            "broker_endpoint": broker_endpoint,
            "broker_token": broker_token,
        }
        action = "open"
        if checkpoint is not None:
            action = "restore"
            payload["checkpoint"] = checkpoint.model_dump(mode="json")
        try:
            session = ProviderSession.model_validate(
                await self._call(slot_id, action, payload)
            )
        except Exception:
            self._broker_rpc.revoke_task(request.task_id)
            if self._executor_pool is not None:
                with contextlib.suppress(Exception):
                    await self._executor_pool.close_task(
                        request.task_id, request.workspace_id
                    )
            raise
        if session.task_id != request.task_id:
            self._broker_rpc.revoke_task(request.task_id)
            raise ProviderRPCError(
                "Provider session binding is invalid.", code="provider_invalid_response"
            )
        self._sessions[session.session_id] = (
            slot_id,
            session.task_id,
            request.workspace_id,
        )
        return session

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        slot_id = self._require_session(session)
        reader, writer = await self._connect(slot_id)
        await self._send(
            writer,
            "message",
            {"session": session.model_dump(mode="json"), "text": text},
            slot_id,
        )
        completed = False
        try:
            while True:
                value = await self._read(reader)
                if value.get("done") is True:
                    completed = True
                    return
                event = value.get("event")
                if not isinstance(event, dict):
                    raise ProviderRPCError(
                        "Provider stream is invalid.", code="provider_invalid_response"
                    )
                yield ProviderEvent.model_validate(event)
        finally:
            writer.close()
            await writer.wait_closed()
            if not completed:
                with contextlib.suppress(Exception):
                    await self.interrupt_turn(session)

    async def cancel(self, session: ProviderSession) -> bool:
        slot_id = self._require_session(session)
        result = await self._call(
            slot_id, "cancel", {"session": session.model_dump(mode="json")}
        )
        return result.get("cancelled") is True

    async def interrupt_turn(self, session: ProviderSession) -> bool:
        slot_id = self._require_session(session)
        result = await self._call(
            slot_id,
            "interrupt_turn",
            {"session": session.model_dump(mode="json")},
        )
        return result.get("interrupted") is True

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        slot_id = self._require_session(session)
        return ProviderCheckpoint.model_validate(
            await self._call(
                slot_id, "checkpoint", {"session": session.model_dump(mode="json")}
            )
        )

    async def close(self, session: ProviderSession) -> None:
        binding = self._sessions.pop(session.session_id, None)
        if binding is None or binding[1] != session.task_id:
            return
        slot_id = binding[0]
        try:
            await self._call(
                slot_id, "close", {"session": session.model_dump(mode="json")}
            )
        finally:
            if self._executor_pool is not None:
                with contextlib.suppress(Exception):
                    await self._executor_pool.close_task(session.task_id, binding[2])
            self._broker_rpc.revoke_task(session.task_id)

    async def run_process(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        timeout_seconds: int,
        isolated: bool,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._executor_pool is not None:
            return await self._executor_pool.run_process(
                task_id=task_id,
                workspace_id=workspace_id,
                argv=argv,
                timeout_seconds=timeout_seconds,
                isolated=isolated,
                environment_overrides=environment_overrides,
            )
        return await self._workspace_call(
            workspace_id,
            "execute_process",
            {
                "task_id": task_id,
                "workspace_id": workspace_id,
                "argv": list(argv),
                "timeout_seconds": timeout_seconds,
                "isolated": isolated,
                "environment_overrides": dict(environment_overrides or {}),
            },
        )

    async def start_service(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        ttl_seconds: int,
        preview_port: int | None = None,
    ) -> dict[str, Any]:
        if self._executor_pool is not None:
            return await self._executor_pool.start_service(
                task_id=task_id,
                workspace_id=workspace_id,
                argv=argv,
                ttl_seconds=ttl_seconds,
                preview_port=preview_port,
            )
        return await self._workspace_call(
            workspace_id,
            "start_service",
            {
                "task_id": task_id,
                "workspace_id": workspace_id,
                "argv": list(argv),
                "ttl_seconds": ttl_seconds,
                "preview_port": preview_port,
            },
        )

    async def service_status(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]:
        if self._executor_pool is not None:
            return await self._executor_pool.service_status(task_id=task_id, workspace_id=workspace_id, service_id=service_id)
        return await self._workspace_call(workspace_id, "service_status", {"task_id": task_id, "service_id": service_id})

    async def service_input(self, *, task_id: str, workspace_id: str, service_id: str, data: str) -> dict[str, Any]:
        if self._executor_pool is not None:
            return await self._executor_pool.service_input(task_id=task_id, workspace_id=workspace_id, service_id=service_id, data=data)
        return await self._workspace_call(workspace_id, "service_input", {"task_id": task_id, "service_id": service_id, "data": data})

    async def stop_service(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]:
        if self._executor_pool is not None:
            return await self._executor_pool.stop_service(task_id=task_id, workspace_id=workspace_id, service_id=service_id)
        return await self._workspace_call(workspace_id, "stop_service", {"task_id": task_id, "service_id": service_id})

    async def _workspace_call(self, workspace_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        slot_id = self._workspace_slot_resolver(workspace_id)
        if slot_id not in self._endpoints:
            raise ProviderRPCError("Provider slot is unavailable.", code="provider_unavailable")
        return await self._call(slot_id, action, payload)

    def _require_session(self, session: ProviderSession) -> str:
        binding = self._sessions.get(session.session_id)
        if binding is None or binding[1] != session.task_id:
            raise ProviderRPCError(
                "Provider session was not found.", code="session_not_found"
            )
        return binding[0]

    async def _call(
        self, slot_id: str, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        reader, writer = await self._connect(slot_id)
        await self._send(writer, action, payload, slot_id)
        try:
            value = await self._read(reader)
            result = value.get("result")
            if not isinstance(result, dict):
                raise ProviderRPCError(
                    "Provider response is invalid.", code="provider_invalid_response"
                )
            return result
        finally:
            writer.close()
            await writer.wait_closed()

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        action: str,
        payload: dict[str, Any],
        slot_id: str,
    ) -> None:
        bound_payload = dict(payload)
        if action != "capabilities":
            bound_payload["controller_id"] = self._controller_id
            bound_payload["controller_generation"] = self._controller_generation
        request = ProviderRPCRequest(
            token=self._tokens[slot_id], action=action, payload=bound_payload
        )
        encoded = request.model_dump_json().encode("utf-8") + b"\n"
        if len(encoded) > MAX_PROVIDER_RPC_BYTES:
            raise ProviderRPCError(
                "Provider request is too large.", code="provider_request_too_large"
            )
        writer.write(encoded)
        await writer.drain()

    async def _connect(
        self, slot_id: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        endpoint = self._endpoints[slot_id]
        if endpoint.startswith("unix:"):
            return await asyncio.open_unix_connection(
                endpoint.removeprefix("unix:"), limit=MAX_PROVIDER_RPC_BYTES
            )
        if endpoint.startswith("tcp:127.0.0.1:"):
            return await asyncio.open_connection(
                "127.0.0.1", int(endpoint.rsplit(":", 1)[1]), limit=MAX_PROVIDER_RPC_BYTES
            )
        raise ProviderRPCError(
            "Provider endpoint is invalid.", code="provider_endpoint_invalid"
        )

    @staticmethod
    async def _read(reader: asyncio.StreamReader) -> dict[str, Any]:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_PROVIDER_RPC_BYTES:
            raise ProviderRPCError(
                "Provider response is invalid.", code="provider_invalid_response"
            )
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("ok") is not True:
            error = value.get("error") if isinstance(value, dict) else None
            code = error.get("code") if isinstance(error, dict) else "provider_failed"
            message = (
                error.get("message")
                if isinstance(error, dict)
                else "Provider request failed."
            )
            raise ProviderRPCError(str(message), code=str(code))
        return value
