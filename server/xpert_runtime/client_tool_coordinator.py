from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .client_tool_store import ClientToolRequest, ClientToolStore
from .execution_store import (
    WorkflowExecution,
    WorkflowExecutionConflictError,
    WorkflowExecutionStore,
)


logger = logging.getLogger("modelmirror.client_tool_coordinator")
ResumeClientExecution = Callable[
    [WorkflowExecution, ClientToolRequest], Awaitable[None]
]
ExpireClientExecution = Callable[
    [WorkflowExecution, ClientToolRequest], Awaitable[None]
]


class ClientToolConnectionManager:
    """In-memory WebSocket connections; durable state remains in ClientToolStore."""

    def __init__(self, store: ClientToolStore) -> None:
        self.store = store
        self._connections: dict[str, tuple[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def attach(self, host_id: str, connection_id: str, websocket: Any) -> None:
        previous: Any | None = None
        async with self._lock:
            current = self._connections.get(host_id)
            if current is not None:
                previous = current[1]
            self._connections[host_id] = (connection_id, websocket)
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4001, reason="Client host connection replaced.")
            except Exception:
                pass

    async def detach(self, host_id: str, connection_id: str) -> None:
        async with self._lock:
            current = self._connections.get(host_id)
            if current is not None and current[0] == connection_id:
                self._connections.pop(host_id, None)
        self.store.disconnect_host(host_id, connection_id=connection_id)

    async def is_online(self, host_id: str) -> bool:
        async with self._lock:
            return host_id in self._connections

    async def dispatch(self, request: ClientToolRequest) -> bool:
        async with self._lock:
            current = self._connections.get(request.host_id)
        if current is None:
            return False
        connection_id, websocket = current
        try:
            await websocket.send_json(
                {
                    "type": "tool_request",
                    "protocol": "modelmirror-client-tools-v1",
                    "request_id": request.request_id,
                    "operation_id": request.operation_id,
                    "tool_call_id": request.tool_call_id,
                    "host_id": request.host_id,
                    "tool_name": request.tool_name,
                    "arguments": dict(request.arguments),
                    "schema_hash": request.schema_hash,
                    "mutating": request.mutating,
                    "expires_at": request.expires_at,
                }
            )
            self.store.mark_dispatched(request.request_id)
            return True
        except Exception:
            logger.warning(
                "Client host dispatch failed host_id=%s request_id=%s",
                request.host_id,
                request.request_id,
                exc_info=True,
            )
            await self.detach(request.host_id, connection_id)
            return False


class ClientToolCoordinator:
    """Dispatches durable client requests and resumes completed continuations."""

    def __init__(
        self,
        store: ClientToolStore,
        executions: WorkflowExecutionStore,
        connections: ClientToolConnectionManager,
        resume_execution: ResumeClientExecution,
        *,
        expire_execution: ExpireClientExecution | None = None,
        enabled: bool = True,
        poll_interval: float = 0.5,
        lease_seconds: float = 60.0,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.executions = executions
        self.connections = connections
        self.resume_execution = resume_execution
        self.expire_execution = expire_execution
        self.enabled = enabled
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_seconds = max(5.0, float(lease_seconds))
        self.worker_id = worker_id or f"client-tool-{uuid.uuid4().hex[:8]}"
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._active: set[str] = set()

    def start(self) -> None:
        if not self.enabled or (self._loop_task and not self._loop_task.done()):
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="runtime-client-tool-coordinator"
        )

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        task = self._loop_task
        self._loop_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def wake(self) -> None:
        self._wake.set()

    async def run_once(self) -> int:
        expired = self.store.expire_due()
        if self.expire_execution is not None:
            for request in expired:
                execution = next(
                    (
                        item
                        for item in self.executions.list_items(limit=1000)
                        if item.status == "waiting"
                        and item.wait_kind == "client_tool"
                        and item.wait_id == request.request_id
                    ),
                    None,
                )
                if execution is not None:
                    await self.expire_execution(execution, request)
        dispatched = 0
        for request in reversed(
            self.store.list_requests(status="pending", limit=200)
        ):
            if await self.connections.dispatch(request):
                dispatched += 1

        for execution in self.executions.list_items(limit=1000):
            if (
                execution.status != "waiting"
                or execution.wait_kind != "client_tool"
                or not execution.wait_id
            ):
                continue
            request = self.store.get_request(execution.wait_id)
            if request is None or request.status not in {
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            }:
                continue
            try:
                self.executions.mark_ready(
                    execution.task_id,
                    wait_kind="client_tool",
                    wait_id=request.request_id,
                )
            except WorkflowExecutionConflictError:
                continue

        ready: list[tuple[WorkflowExecution, ClientToolRequest]] = []
        for execution in self.executions.list_items(status="ready", limit=1000):
            if (
                execution.task_id in self._active
                or execution.wait_kind != "client_tool"
                or not execution.wait_id
            ):
                continue
            request = self.store.get_request(execution.wait_id)
            if request is not None and request.status in {
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            }:
                ready.append((execution, request))

        async def resume(
            execution: WorkflowExecution, request: ClientToolRequest
        ) -> bool:
            self._active.add(execution.task_id)
            try:
                claimed = self.executions.claim(
                    execution.task_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                await self.resume_execution(claimed, request)
                return True
            except WorkflowExecutionConflictError:
                return False
            except Exception as exc:
                logger.exception(
                    "Client tool continuation failed task_id=%s",
                    execution.task_id,
                )
                self.executions.fail(execution.task_id, error=str(exc))
                return False
            finally:
                self._active.discard(execution.task_id)

        resumed = 0
        if ready:
            values = await asyncio.gather(*(resume(*item) for item in ready[:20]))
            resumed = sum(1 for value in values if value)
        return dispatched + resumed

    async def status(self) -> dict[str, Any]:
        requests = self.store.list_requests(limit=1000)
        hosts = self.store.list_hosts()
        executions = self.executions.list_items(limit=1000)
        return {
            "enabled": self.enabled,
            "running": bool(self._loop_task and not self._loop_task.done()),
            "worker_id": self.worker_id,
            "online_hosts": sum(1 for item in hosts if item.status == "online"),
            "pending_requests": sum(1 for item in requests if item.status == "pending"),
            "uncertain_requests": sum(1 for item in requests if item.status == "uncertain"),
            "waiting_executions": sum(
                1
                for item in executions
                if item.status == "waiting" and item.wait_kind == "client_tool"
            ),
            "active_executions": len(self._active),
        }

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Client tool coordinator loop failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass
