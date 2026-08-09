from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class SidecarExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


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
                env=self._environment(self._workspace_resolver(workspace_id)),
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
