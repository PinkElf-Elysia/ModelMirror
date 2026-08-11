from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import signal
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import SAFE_ID


MAX_EXECUTOR_RPC_BYTES = 8 * 1024 * 1024


class SidecarExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ExecutorRPCError(SidecarExecutionError):
    pass


@dataclass(slots=True)
class _Service:
    service_id: str
    task_id: str
    process: asyncio.subprocess.Process
    started_at: float
    expires_at: float
    output: bytearray
    preview_port: int | None = None
    state: str = "running"
    exit_code: int | None = None
    reason: str | None = None
    monitor: asyncio.Task[None] | None = None


class SidecarExecutor:
    """Runs task commands inside one non-root slot sidecar."""

    def __init__(
        self,
        workspace_resolver: Callable[[str], Path],
        *,
        runtime_root: Path,
        max_output_bytes: int = 2 * 1024 * 1024,
        max_services_per_task: int = 4,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._runtime_root = Path(runtime_root)
        self._max_output_bytes = max_output_bytes
        self._max_services_per_task = max_services_per_task
        self._services: dict[str, _Service] = {}
        self._lock = asyncio.Lock()

    async def run_process(
        self,
        *,
        workspace_id: str,
        argv: Sequence[str],
        timeout_seconds: int,
        isolated: bool,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        repository = self._workspace_resolver(workspace_id)
        execution_root: Path | None = None
        execution_repository = repository
        try:
            if isolated:
                execution_root = self._runtime_root / "checks" / f"run_{uuid.uuid4().hex}"
                execution_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(repository, execution_root, ignore=shutil.ignore_patterns(".git"))
                execution_repository = execution_root
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=execution_repository,
                env=self._environment(execution_repository, environment_overrides),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            try:
                output = await asyncio.wait_for(
                    self._collect(process), timeout=timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise SidecarExecutionError("Command timed out.", code="command_timeout")
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                raise
            return {
                "argv": list(argv),
                "exit_code": int(process.returncode or 0),
                "output": output.decode("utf-8", errors="replace"),
            }
        finally:
            if execution_root is not None:
                shutil.rmtree(execution_root, ignore_errors=True)

    async def start_service(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        ttl_seconds: int,
        preview_port: int | None = None,
    ) -> dict[str, object]:
        if not 1 <= ttl_seconds <= 3600:
            raise SidecarExecutionError("Service TTL is invalid.", code="service_ttl_invalid")
        if preview_port is not None and not 1024 <= preview_port <= 65535:
            raise SidecarExecutionError(
                "Preview port is invalid.", code="service_preview_invalid"
            )
        async with self._lock:
            active = sum(
                service.task_id == task_id and service.state == "running"
                for service in self._services.values()
            )
            if active >= self._max_services_per_task:
                raise SidecarExecutionError(
                    "Task service capacity is exhausted.", code="service_capacity_exhausted"
                )
            now = time.time()
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self._workspace_resolver(workspace_id),
                env=self._environment(
                    self._workspace_resolver(workspace_id),
                    (
                        {"HOST": "0.0.0.0", "PORT": str(preview_port)}
                        if preview_port is not None
                        else None
                    ),
                ),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            service = _Service(
                service_id=f"service_{uuid.uuid4().hex}",
                task_id=task_id,
                process=process,
                started_at=now,
                expires_at=now + ttl_seconds,
                output=bytearray(),
                preview_port=preview_port,
            )
            self._services[service.service_id] = service
            service.monitor = asyncio.create_task(self._monitor(service, ttl_seconds))
            return self._service_result(service, include_output=False)

    def service_status(self, *, task_id: str, service_id: str) -> dict[str, object]:
        return self._service_result(self._require_service(task_id, service_id), include_output=True)

    async def service_input(self, *, task_id: str, service_id: str, data: str) -> None:
        if not data or len(data.encode("utf-8")) > 64 * 1024:
            raise SidecarExecutionError("Service input is invalid.", code="service_input_invalid")
        service = self._require_service(task_id, service_id)
        if service.state != "running" or service.process.stdin is None:
            raise SidecarExecutionError("Service is not running.", code="service_not_running")
        service.process.stdin.write(data.encode("utf-8"))
        await service.process.stdin.drain()

    async def stop_service(self, *, task_id: str, service_id: str) -> dict[str, object]:
        service = self._require_service(task_id, service_id)
        if service.state == "running":
            service.reason = "user_interrupted"
            await self._interrupt(service.process)
            if service.monitor is not None:
                await service.monitor
        return self._service_result(service, include_output=True)

    async def stop_task(self, task_id: str) -> None:
        services = [
            service
            for service in self._services.values()
            if service.task_id == task_id and service.state == "running"
        ]
        for service in services:
            service.reason = "task_closed"
            await self._interrupt(service.process)
        await asyncio.gather(
            *(service.monitor for service in services if service.monitor is not None),
            return_exceptions=True,
        )

    async def _monitor(self, service: _Service, ttl_seconds: int) -> None:
        drain = asyncio.create_task(self._drain(service))
        try:
            try:
                await asyncio.wait_for(service.process.wait(), timeout=ttl_seconds)
            except TimeoutError:
                service.reason = "service_ttl_expired"
                await self._interrupt(service.process)
            await drain
        finally:
            if not drain.done():
                drain.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain
            service.exit_code = service.process.returncode
            if service.reason is not None:
                service.state = "stopped"
            elif service.exit_code == 0:
                service.state = "completed"
            else:
                service.state = "failed"
                service.reason = "service_exited"

    async def _drain(self, service: _Service) -> None:
        if service.process.stdout is None:
            return
        while True:
            chunk = await service.process.stdout.read(64 * 1024)
            if not chunk:
                return
            if len(service.output) + len(chunk) > self._max_output_bytes:
                remaining = self._max_output_bytes - len(service.output)
                service.output.extend(chunk[: max(remaining, 0)])
                service.reason = "service_output_limit"
                service.process.kill()
                return
            service.output.extend(chunk)

    async def _collect(self, process: asyncio.subprocess.Process) -> bytes:
        if process.stdout is None:
            raise SidecarExecutionError("Command output is unavailable.", code="command_failed")
        output = bytearray()
        while True:
            chunk = await process.stdout.read(64 * 1024)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > self._max_output_bytes:
                process.kill()
                await process.wait()
                raise SidecarExecutionError(
                    "Command output is too large.", code="tool_output_too_large"
                )
        await process.wait()
        return bytes(output)

    def _require_service(self, task_id: str, service_id: str) -> _Service:
        service = self._services.get(service_id)
        if service is None or service.task_id != task_id:
            raise SidecarExecutionError("Service was not found.", code="service_not_found")
        return service

    @staticmethod
    def _service_result(service: _Service, *, include_output: bool) -> dict[str, object]:
        result: dict[str, object] = {
            "service_id": service.service_id,
            "task_id": service.task_id,
            "state": service.state,
            "started_at": service.started_at,
            "expires_at": service.expires_at,
            "exit_code": service.exit_code,
            "reason": service.reason,
            "preview_port": service.preview_port,
        }
        if include_output and service.state != "running":
            result["output"] = bytes(service.output).decode("utf-8", errors="replace")
        return result

    @staticmethod
    async def _interrupt(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _environment(
        repository: Path, overrides: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        home = repository.parent / "home"
        home.mkdir(exist_ok=True)
        environment = {
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        environment.update(overrides or {})
        return environment


class ExecutorRPCServer:
    """Credential-free command host for one fixed workspace slot."""

    def __init__(self, executor: SidecarExecutor, *, token: str) -> None:
        if len(token) < 32:
            raise ValueError("executor RPC token is too short")
        self.executor = executor
        self._token = token
        self._server: asyncio.AbstractServer | None = None
        self.endpoint: str | None = None
        self._task_id: str | None = None
        self._workspace_id: str | None = None

    async def start_unix(self, socket_path: Path) -> str:
        socket_path = Path(socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(socket_path), limit=MAX_EXECUTOR_RPC_BYTES
        )
        socket_path.chmod(0o660)
        self.endpoint = f"unix:{socket_path}"
        return self.endpoint

    async def start_tcp_for_tests(self) -> str:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0, limit=MAX_EXECUTOR_RPC_BYTES
        )
        address = self._server.sockets[0].getsockname()
        self.endpoint = f"tcp:127.0.0.1:{address[1]}"
        return self.endpoint

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._task_id is not None:
            await self.executor.stop_task(self._task_id)
        self._server = None
        self.endpoint = None
        self._task_id = None
        self._workspace_id = None

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_EXECUTOR_RPC_BYTES or not raw.endswith(b"\n"):
                raise ExecutorRPCError("Executor request is invalid.", code="executor_request_invalid")
            value = json.loads(raw)
            token = value.get("token") if isinstance(value, dict) else None
            action = value.get("action") if isinstance(value, dict) else None
            payload = value.get("payload") if isinstance(value, dict) else None
            if not isinstance(token, str) or not secrets.compare_digest(token, self._token):
                raise ExecutorRPCError("Executor authentication failed.", code="executor_unauthorized")
            if not isinstance(action, str) or not isinstance(payload, dict):
                raise ExecutorRPCError("Executor request is invalid.", code="executor_request_invalid")
            result = await self._dispatch(action, payload)
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", "executor_failed"),
                    "message": str(exc)
                    if isinstance(exc, (ExecutorRPCError, SidecarExecutionError, ValueError))
                    else "Executor request failed.",
                },
            }
        encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
        writer.write(encoded)
        await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    async def _dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(payload.get("task_id", ""))
        workspace_id = str(payload.get("workspace_id", ""))
        if action == "bind_task":
            if SAFE_ID.fullmatch(task_id) is None or SAFE_ID.fullmatch(workspace_id) is None:
                raise ExecutorRPCError("Executor binding is invalid.", code="executor_binding_invalid")
            if self._task_id not in {None, task_id} or self._workspace_id not in {
                None,
                workspace_id,
            }:
                raise ExecutorRPCError("Executor slot is busy.", code="executor_slot_busy")
            self.executor._workspace_resolver(workspace_id)
            self._task_id, self._workspace_id = task_id, workspace_id
            return {"bound": True}
        if action == "close_task":
            self._require_binding(task_id, workspace_id)
            await self.executor.stop_task(task_id)
            self._task_id = self._workspace_id = None
            return {"closed": True}
        self._require_binding(task_id, workspace_id)
        if action == "execute_process":
            return await self.executor.run_process(
                workspace_id=workspace_id,
                argv=tuple(payload.get("argv", ())),
                timeout_seconds=int(payload.get("timeout_seconds", 0)),
                isolated=payload.get("isolated") is True,
                environment_overrides=payload.get("environment_overrides"),
            )
        if action == "start_service":
            return await self.executor.start_service(
                task_id=task_id,
                workspace_id=workspace_id,
                argv=tuple(payload.get("argv", ())),
                ttl_seconds=int(payload.get("ttl_seconds", 0)),
                preview_port=(
                    int(payload["preview_port"])
                    if payload.get("preview_port") is not None
                    else None
                ),
            )
        if action == "service_status":
            return self.executor.service_status(
                task_id=task_id, service_id=str(payload.get("service_id", ""))
            )
        if action == "service_input":
            await self.executor.service_input(
                task_id=task_id,
                service_id=str(payload.get("service_id", "")),
                data=str(payload.get("data", "")),
            )
            return {"accepted": True}
        if action == "stop_service":
            return await self.executor.stop_service(
                task_id=task_id, service_id=str(payload.get("service_id", ""))
            )
        raise ExecutorRPCError("Executor action is invalid.", code="executor_request_invalid")

    def _require_binding(self, task_id: str, workspace_id: str) -> None:
        if self._task_id != task_id or self._workspace_id != workspace_id:
            raise ExecutorRPCError("Executor task is not bound.", code="executor_binding_invalid")


class ExecutorSidecarClientPool:
    """Routes task commands to the credential-free executor for a fixed slot."""

    def __init__(
        self,
        *,
        endpoints: Mapping[str, str],
        tokens: Mapping[str, str],
        workspace_slot_resolver: Callable[[str], str],
    ) -> None:
        if set(endpoints) != set(tokens) or not endpoints:
            raise ValueError("executor sidecar bindings are incomplete")
        self._endpoints = dict(endpoints)
        self._tokens = dict(tokens)
        self._workspace_slot_resolver = workspace_slot_resolver

    async def bind_task(self, task_id: str, workspace_id: str) -> None:
        await self._workspace_call(workspace_id, "bind_task", {"task_id": task_id, "workspace_id": workspace_id})

    async def close_task(self, task_id: str, workspace_id: str) -> None:
        await self._workspace_call(workspace_id, "close_task", {"task_id": task_id, "workspace_id": workspace_id})

    async def run_process(self, *, task_id: str, workspace_id: str, argv: Sequence[str], timeout_seconds: int, isolated: bool, environment_overrides: Mapping[str, str] | None = None) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "execute_process", {"task_id": task_id, "workspace_id": workspace_id, "argv": list(argv), "timeout_seconds": timeout_seconds, "isolated": isolated, "environment_overrides": dict(environment_overrides or {})})

    async def start_service(self, *, task_id: str, workspace_id: str, argv: Sequence[str], ttl_seconds: int, preview_port: int | None = None) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "start_service", {"task_id": task_id, "workspace_id": workspace_id, "argv": list(argv), "ttl_seconds": ttl_seconds, "preview_port": preview_port})

    async def service_status(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "service_status", {"task_id": task_id, "workspace_id": workspace_id, "service_id": service_id})

    async def service_input(self, *, task_id: str, workspace_id: str, service_id: str, data: str) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "service_input", {"task_id": task_id, "workspace_id": workspace_id, "service_id": service_id, "data": data})

    async def stop_service(self, *, task_id: str, workspace_id: str, service_id: str) -> dict[str, Any]:
        return await self._workspace_call(workspace_id, "stop_service", {"task_id": task_id, "workspace_id": workspace_id, "service_id": service_id})

    async def _workspace_call(self, workspace_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        slot_id = self._workspace_slot_resolver(workspace_id)
        endpoint = self._endpoints.get(slot_id)
        token = self._tokens.get(slot_id)
        if endpoint is None or token is None:
            raise ExecutorRPCError("Executor slot is unavailable.", code="executor_unavailable")
        if endpoint.startswith("unix:"):
            reader, writer = await asyncio.open_unix_connection(endpoint[5:])
        elif endpoint.startswith("tcp:127.0.0.1:"):
            reader, writer = await asyncio.open_connection("127.0.0.1", int(endpoint.rsplit(":", 1)[1]))
        else:
            raise ExecutorRPCError("Executor endpoint is invalid.", code="executor_unavailable")
        try:
            request = json.dumps({"token": token, "action": action, "payload": payload}, separators=(",", ":")).encode() + b"\n"
            writer.write(request)
            await writer.drain()
            raw = await reader.readline()
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("ok") is not True:
                error = value.get("error", {}) if isinstance(value, dict) else {}
                raise ExecutorRPCError(str(error.get("message", "Executor request failed.")), code=str(error.get("code", "executor_failed")))
            result = value.get("result")
            if not isinstance(result, dict):
                raise ExecutorRPCError("Executor response is invalid.", code="executor_invalid_response")
            return result
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
