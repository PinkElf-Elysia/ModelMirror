from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from pydantic import Field

from .contracts import (
    ApprovalStatus,
    OperationState,
    StrictModel,
    TaskState,
    TERMINAL_STATES,
)
from .tool_broker import ToolBroker, ToolBrokerError, ToolResult


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
            result = await self._execute(request)
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

    async def _execute(self, request: BrokerRPCRequest) -> ToolResult:
        try:
            return await self.broker.execute(
                task_id=request.task_id,
                operation_id=request.operation_id,
                tool_name=request.tool_name,
                arguments=request.arguments,
                lease_id=request.lease_id,
                network_lease_id=request.network_lease_id,
            )
        except ToolBrokerError as exc:
            if exc.code != "approval_required" or request.lease_id is not None:
                raise
        lease_id, network_lease_id = await self._wait_for_approval(request)
        return await self.broker.execute(
            task_id=request.task_id,
            operation_id=request.operation_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            lease_id=lease_id,
            network_lease_id=network_lease_id,
        )

    async def _wait_for_approval(
        self, request: BrokerRPCRequest
    ) -> tuple[str, str | None]:
        required_ids = [request.operation_id]
        if request.tool_name == "install_dependencies":
            required_ids.append(
                "network_"
                + hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()[:32]
            )
        while True:
            task = self.broker.store.get_task(request.task_id)
            if task.state in TERMINAL_STATES or task.state in {
                TaskState.PAUSED,
                TaskState.INTERRUPTED,
            }:
                raise BrokerRPCError(
                    "Approval is no longer available.", code="approval_unavailable"
                )
            approvals = {
                approval.operation_id: approval
                for approval in self.broker.store.list_approvals(request.task_id)
                if approval.operation_id in required_ids
            }
            if len(approvals) == len(required_ids):
                if any(
                    approval.status
                    in {
                        ApprovalStatus.REJECTED,
                        ApprovalStatus.CANCELLED,
                        ApprovalStatus.EXPIRED,
                    }
                    for approval in approvals.values()
                ):
                    operation = self.broker.store.get_operation(request.operation_id)
                    if operation.state is OperationState.PREPARED:
                        self.broker.store.transition_operation(
                            request.operation_id,
                            OperationState.FAILED,
                            result={"code": "approval_rejected"},
                            expected_state=OperationState.PREPARED,
                        )
                    raise BrokerRPCError(
                        "Tool approval was rejected.", code="approval_rejected"
                    )
                if all(
                    approval.status is ApprovalStatus.APPROVED
                    and approval.lease is not None
                    for approval in approvals.values()
                ):
                    main = approvals[request.operation_id]
                    network = (
                        approvals.get(required_ids[1])
                        if len(required_ids) > 1
                        else None
                    )
                    main_lease = main.lease
                    if main_lease is None:
                        raise BrokerRPCError(
                            "Approved operation is missing its lease.",
                            code="approval_unavailable",
                        )
                    return (
                        main_lease.lease_id,
                        network.lease.lease_id if network and network.lease else None,
                    )
            await asyncio.sleep(0.05)


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
