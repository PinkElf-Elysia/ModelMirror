from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any

from pydantic import Field

from .contracts import StrictModel
from .tool_broker import ToolBroker, ToolBrokerError


MAX_RPC_BYTES = 1024 * 1024


class BrokerRPCError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class BrokerRPCRequest(StrictModel):
    token: str = Field(min_length=32, max_length=256)
    task_id: str
    operation_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    lease_id: str | None = None
    network_lease_id: str | None = None


class BrokerRPCServer:
    """Authenticated internal RPC boundary used by the task-local MCP process."""

    def __init__(self, broker: ToolBroker) -> None:
        self.broker = broker
        self._tokens: dict[str, str] = {}
        self._server: asyncio.AbstractServer | None = None
        self.endpoint: str | None = None

    def register_task(self, task_id: str) -> str:
        self.broker.store.get_task(task_id)
        token = secrets.token_urlsafe(48)
        self._tokens[task_id] = token
        return token

    def revoke_task(self, task_id: str) -> None:
        self._tokens.pop(task_id, None)

    async def start_unix(self, socket_path: Path, *, group_id: int | None = None) -> str:
        if self._server is not None:
            raise RuntimeError("broker RPC server is already running")
        socket_path = Path(socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(socket_path), limit=MAX_RPC_BYTES
        )
        socket_path.chmod(0o600)
        if group_id is not None and hasattr(os, "chown"):
            os.chown(socket_path, -1, group_id)
            socket_path.chmod(0o660)
        self.endpoint = f"unix:{socket_path}"
        return self.endpoint

    async def start_tcp_for_tests(self) -> str:
        if self._server is not None:
            raise RuntimeError("broker RPC server is already running")
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, limit=MAX_RPC_BYTES
        )
        address = self._server.sockets[0].getsockname()
        self.endpoint = f"tcp:127.0.0.1:{address[1]}"
        return self.endpoint

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._server = None
        self.endpoint = None
        self._tokens.clear()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        response: dict[str, Any]
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_RPC_BYTES or not raw.endswith(b"\n"):
                raise BrokerRPCError("Broker request is invalid.", code="broker_request_invalid")
            request = BrokerRPCRequest.model_validate_json(raw)
            expected = self._tokens.get(request.task_id)
            if expected is None or not secrets.compare_digest(expected, request.token):
                raise BrokerRPCError("Broker authentication failed.", code="broker_unauthorized")
            result = await self.broker.execute(
                task_id=request.task_id,
                operation_id=request.operation_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                lease_id=request.lease_id,
                network_lease_id=request.network_lease_id,
            )
            response = {"ok": True, "result": result.model_dump(mode="json")}
        except Exception as exc:
            response = {
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", "broker_failed"),
                    "message": str(exc)
                    if isinstance(exc, (BrokerRPCError, ToolBrokerError))
                    else "Broker request failed.",
                },
            }
        encoded = json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_RPC_BYTES:
            encoded = b'{"ok":false,"error":{"code":"broker_response_too_large","message":"Broker response is too large."}}\n'
        writer.write(encoded)
        await writer.drain()
        writer.close()
        await writer.wait_closed()


class BrokerRPCClient:
    def __init__(self, endpoint: str, *, token: str, task_id: str) -> None:
        self.endpoint = endpoint
        self.token = token
        self.task_id = task_id

    async def call(
        self,
        *,
        operation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        lease_id: str | None = None,
        network_lease_id: str | None = None,
    ) -> dict[str, Any]:
        reader, writer = await self._connect()
        request = BrokerRPCRequest(
            token=self.token,
            task_id=self.task_id,
            operation_id=operation_id,
            tool_name=tool_name,
            arguments=arguments,
            lease_id=lease_id,
            network_lease_id=network_lease_id,
        )
        writer.write(request.model_dump_json().encode("utf-8") + b"\n")
        await writer.drain()
        raw = await reader.readline()
        writer.close()
        await writer.wait_closed()
        if not raw or len(raw) > MAX_RPC_BYTES:
            raise BrokerRPCError("Broker response is invalid.", code="broker_invalid_response")
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
            raise BrokerRPCError("Broker response is invalid.", code="broker_invalid_response")
        if not value["ok"]:
            error = value.get("error")
            code = error.get("code") if isinstance(error, dict) else "broker_failed"
            message = error.get("message") if isinstance(error, dict) else "Broker request failed."
            raise BrokerRPCError(str(message), code=str(code))
        result = value.get("result")
        if not isinstance(result, dict):
            raise BrokerRPCError("Broker response is invalid.", code="broker_invalid_response")
        return result

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.endpoint.startswith("unix:"):
            return await asyncio.open_unix_connection(
                self.endpoint.removeprefix("unix:"), limit=MAX_RPC_BYTES
            )
        if self.endpoint.startswith("tcp:127.0.0.1:"):
            port = int(self.endpoint.rsplit(":", 1)[1])
            return await asyncio.open_connection("127.0.0.1", port, limit=MAX_RPC_BYTES)
        raise BrokerRPCError("Broker endpoint is invalid.", code="broker_endpoint_invalid")
