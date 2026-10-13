from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from copy import deepcopy
from typing import Any

from .client_tool_store import ClientToolArtifact, ClientToolConflictError, ClientToolRequest, ClientToolStore
from .execution_store import (
    WorkflowExecution,
    WorkflowExecutionConflictError,
    WorkflowExecutionError,
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

    async def dispatch(
        self, request: ClientToolRequest, *,
        parent_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> bool:
        async with self._lock:
            current = self._connections.get(request.host_id)
        if current is None:
            return False
        connection_id, websocket = current
        # Commit the conservative send boundary before yielding to the socket.
        try:
            with parent_guard() if parent_guard is not None else nullcontext():
                request = self.store.mark_dispatched(
                    request.request_id, host_id=request.host_id,
                    connection_id=connection_id, expected_revision=request.revision,
                )
        except ClientToolConflictError:
            return False
        try:
            await websocket.send_json(
                {
                    "type": "tool_request",
                    "protocol": "modelmirror-client-tools-v1",
                    "request_id": request.request_id,
                    "dispatch_count": request.dispatch_count,
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
            return True
        except asyncio.CancelledError:
            try:
                await self.detach(request.host_id, connection_id)
            except Exception as exc:
                logger.warning("Client cancelled dispatch reconciliation pending type=%s", type(exc).__name__)
            raise
        except Exception as exc:
            logger.warning(
                "Client host dispatch failed host_id=%s request_id=%s type=%s",
                request.host_id,
                request.request_id,
                type(exc).__name__,
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

    @staticmethod
    def _matches_client_wait(execution: WorkflowExecution, request: ClientToolRequest) -> bool:
        return (
            execution.task_id == request.task_id and execution.run_id == request.run_id
            and execution.wait_kind == "client_tool" and execution.wait_id == request.request_id
            and execution.runtime_metadata.get("terminal_source_invalidated") is not True
        )

    @contextmanager
    def _parent_operation(self, request: ClientToolRequest) -> Iterator[None]:
        try:
            execution = self.executions.snapshot(request.task_id)
            if (not self.enabled or execution.status != "waiting"
                    or not self._matches_client_wait(execution, request)):
                raise ClientToolConflictError("父执行未停在此客户端请求的等待点，不能重试或派发。")
            with self.executions.projection_local_operation(
                execution.task_id, expected_snapshot=execution,
            ):
                with self.store.request_local_operation(expected_request=request):
                    yield
        except WorkflowExecutionError:
            raise ClientToolConflictError("父执行已变化或不可验证，不能重试或派发客户端请求。") from None

    def retry_blocked_reason(self, request: ClientToolRequest) -> str | None:
        request = deepcopy(request)
        blocked = self.store.serialize_request(request)["retry_blocked_reason"]
        if blocked is not None:
            return blocked
        try:
            with self._parent_operation(request):
                return None
        except ClientToolConflictError as exc:
            return str(exc)

    def retry_request(self, request_id: str) -> ClientToolRequest:
        request = deepcopy(self.store.require_request(request_id))
        with self._parent_operation(request):
            return self.store.retry_request(request_id)

    def register_artifact(
        self, *, expected_request: ClientToolRequest, **payload: Any,
    ) -> ClientToolArtifact:
        current = deepcopy(self.store.require_request(expected_request.request_id))
        # An accepted receipt can arrive while the HTTP body is read. It is not
        # a new dispatch; retry/reconnect identities still must remain fixed.
        if (expected_request.status not in {"dispatched", "running"}
                or current.dispatch_count != expected_request.dispatch_count
                or current.dispatched_connection_id != expected_request.dispatched_connection_id):
            raise ClientToolConflictError("截图上传期间派发已变化，请核对原请求。")
        with self._parent_operation(current):
            return self.store.register_artifact(request_id=current.request_id, **payload)

    async def run_once(self) -> int:
        expired = self.store.expire_due()
        if self.expire_execution is not None:
            for request in expired:
                execution = next(
                    (
                        item
                        for item in self.executions.list_items(limit=1000)
                        if item.status == "waiting"
                        and self._matches_client_wait(item, request)
                    ),
                    None,
                )
                if execution is not None:
                    await self.expire_execution(execution, request)
        dispatched = 0
        for request in reversed(
            self.store.list_requests(status="pending", limit=200)
        ):
            request = deepcopy(request)
            if await self.connections.dispatch(
                request, parent_guard=lambda request=request: self._parent_operation(request),
            ):
                dispatched += 1

        for execution in self.executions.list_items(limit=1000):
            if (
                execution.status != "waiting"
                or execution.wait_kind != "client_tool"
                or not execution.wait_id
            ):
                continue
            request = deepcopy(self.store.get_request(execution.wait_id))
            if request is None or not self._matches_client_wait(execution, request) or request.status not in {
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            }:
                continue
            try:
                # Recheck both snapshots before consuming a receipt. An explicit
                # retry may have replaced the terminal Client snapshot meanwhile.
                with self._parent_operation(request):
                    self.executions.mark_ready(
                        execution.task_id,
                        wait_kind="client_tool",
                        wait_id=request.request_id,
                    )
            except (WorkflowExecutionConflictError, ClientToolConflictError):
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
            if request is not None and self._matches_client_wait(execution, request) and request.status in {
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
            claimed: WorkflowExecution | None = None
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
                if claimed is not None:
                    try:
                        self.executions.fail(
                            execution.task_id,
                            error=str(exc),
                            expected_lease_token=claimed.lease_token,
                        )
                    except WorkflowExecutionConflictError:
                        # Ownership loss must not fail a newer execution.
                        pass
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
