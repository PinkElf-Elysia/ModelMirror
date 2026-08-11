from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from .contracts import StrictModel
from .store import CodingWorkerStore, WorkerNotFoundError
from .workspace import WorkspaceBroker


class ProcessManagerError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ManagedProcess(StrictModel):
    service_id: str
    task_id: str
    state: Literal["running", "completed", "failed", "stopped"]
    started_at: float
    expires_at: float
    exit_code: int | None = None
    output_artifact_id: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class _LiveProcess:
    record: ManagedProcess
    process: asyncio.subprocess.Process
    output: bytearray
    monitor: asyncio.Task[None] | None = None
    reason: str | None = None


class BackgroundProcessManager:
    """Owns task processes and archives bounded output; it never exposes a PID."""

    def __init__(
        self,
        *,
        store: CodingWorkerStore,
        workspace_broker: WorkspaceBroker,
        environment_factory: Callable[[str], Mapping[str, str]],
        max_processes_per_task: int = 4,
        max_output_bytes: int = 2 * 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not 1 <= max_processes_per_task <= 16:
            raise ValueError("process capacity is invalid")
        if not 1024 <= max_output_bytes <= 16 * 1024 * 1024:
            raise ValueError("process output limit is invalid")
        self.store = store
        self.workspace_broker = workspace_broker
        self.environment_factory = environment_factory
        self.max_processes_per_task = max_processes_per_task
        self.max_output_bytes = max_output_bytes
        self._clock = clock
        self._processes: dict[str, _LiveProcess] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        task_id: str,
        workspace_id: str,
        argv: Sequence[str],
        ttl_seconds: int,
    ) -> ManagedProcess:
        if not 1 <= ttl_seconds <= 3600:
            raise ProcessManagerError("Service TTL is invalid.", code="service_ttl_invalid")
        async with self._lock:
            active = sum(
                item.record.task_id == task_id and item.record.state == "running"
                for item in self._processes.values()
            )
            if active >= self.max_processes_per_task:
                raise ProcessManagerError(
                    "Task service capacity is exhausted.", code="service_capacity_exhausted"
                )
            now = self._clock()
            service_id = f"service_{uuid.uuid4().hex}"
            kwargs: dict[str, object] = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.workspace_broker.repository_path(workspace_id),
                env=dict(self.environment_factory(workspace_id)),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **kwargs,
            )
            live = _LiveProcess(
                record=ManagedProcess(
                    service_id=service_id,
                    task_id=task_id,
                    state="running",
                    started_at=now,
                    expires_at=now + ttl_seconds,
                ),
                process=process,
                output=bytearray(),
            )
            self._processes[service_id] = live
            live.monitor = asyncio.create_task(
                self._monitor(live, ttl_seconds), name=f"worker-service-{service_id}"
            )
            self.store.append_event(task_id, "service_started", {"service_id": service_id})
            return live.record

    async def send_input(self, *, task_id: str, service_id: str, data: bytes) -> None:
        if not data or len(data) > 64 * 1024:
            raise ProcessManagerError("Service input is invalid.", code="service_input_invalid")
        live = self._require(task_id, service_id)
        if live.process.stdin is None or live.record.state != "running":
            raise ProcessManagerError("Service is not running.", code="service_not_running")
        live.process.stdin.write(data)
        await live.process.stdin.drain()

    async def interrupt(self, *, task_id: str, service_id: str) -> ManagedProcess:
        live = self._require(task_id, service_id)
        if live.record.state != "running":
            return live.record
        live.reason = "user_interrupted"
        await self._interrupt_process(live.process)
        if live.monitor is not None:
            await live.monitor
        return live.record

    def status(self, *, task_id: str, service_id: str) -> ManagedProcess:
        return self._require(task_id, service_id).record

    def list(self, task_id: str) -> tuple[ManagedProcess, ...]:
        self.store.get_task(task_id)
        return tuple(
            item.record for item in self._processes.values() if item.record.task_id == task_id
        )

    async def shutdown(self) -> None:
        running = [item for item in self._processes.values() if item.record.state == "running"]
        for item in running:
            item.reason = "manager_shutdown"
            await self._interrupt_process(item.process)
        await asyncio.gather(
            *(item.monitor for item in running if item.monitor is not None),
            return_exceptions=True,
        )

    async def _monitor(self, live: _LiveProcess, ttl_seconds: int) -> None:
        drain = asyncio.create_task(self._drain_output(live))
        try:
            try:
                await asyncio.wait_for(live.process.wait(), timeout=ttl_seconds)
            except TimeoutError:
                live.reason = "service_ttl_expired"
                await self._interrupt_process(live.process)
            await drain
        finally:
            if not drain.done():
                drain.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await drain
            exit_code = live.process.returncode
            reason = live.reason
            state: Literal["completed", "failed", "stopped"]
            if reason is not None:
                state = "stopped"
            elif exit_code == 0:
                state = "completed"
            else:
                state = "failed"
                reason = "service_exited"
            artifact = self.store.create_artifact(
                task_id=live.record.task_id,
                media_type="text/plain; charset=utf-8",
                content=bytes(live.output),
                metadata={"service_id": live.record.service_id, "reason": reason},
            )
            live.record = live.record.model_copy(
                update={
                    "state": state,
                    "exit_code": exit_code,
                    "output_artifact_id": artifact.artifact_id,
                    "reason": reason,
                }
            )
            self.store.append_event(
                live.record.task_id,
                "service_stopped",
                {
                    "service_id": live.record.service_id,
                    "state": state,
                    "artifact_id": artifact.artifact_id,
                    "reason": reason,
                },
            )

    async def _drain_output(self, live: _LiveProcess) -> None:
        if live.process.stdout is None:
            return
        while True:
            chunk = await live.process.stdout.read(64 * 1024)
            if not chunk:
                return
            if len(live.output) + len(chunk) > self.max_output_bytes:
                remaining = self.max_output_bytes - len(live.output)
                if remaining > 0:
                    live.output.extend(chunk[:remaining])
                live.reason = "service_output_limit"
                with contextlib.suppress(ProcessLookupError):
                    live.process.kill()
                return
            live.output.extend(chunk)

    @staticmethod
    async def _interrupt_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    def _require(self, task_id: str, service_id: str) -> _LiveProcess:
        item = self._processes.get(service_id)
        if item is None or item.record.task_id != task_id:
            raise WorkerNotFoundError("Service was not found.", code="service_not_found")
        return item
