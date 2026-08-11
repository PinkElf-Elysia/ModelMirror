from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Protocol

from .models import ENGINE_PROTOCOL, UPSTREAM_REVISION


MAX_FRAME_BYTES = 4 * 1024 * 1024
HANDSHAKE_TIMEOUT_SECONDS = 5.0
IDLE_TIMEOUT_SECONDS = 30.0
CANCEL_GRACE_SECONDS = 5.0
KILL_GRACE_SECONDS = 2.0
DEFAULT_WATCHDOG_SECONDS = 60 * 60
DEFAULT_MAX_PRESTART_RETRIES = 5


class EnginePortError(RuntimeError):
    code = "engine_port_error"


class EngineProtocolError(EnginePortError):
    code = "engine_protocol_error"


class EngineUnavailableError(EnginePortError):
    code = "engine_unavailable"


class EngineWatchdogError(EnginePortError):
    code = "engine_watchdog"


@dataclass(frozen=True, slots=True)
class EngineShadowRunSpec:
    run_id: str
    session_id: str
    objective: str
    workspace_dir: Path
    goal_file_path: Path
    system_prompt: str
    thinking_level: str
    token_budget: int
    max_goal_rounds: int
    max_task_turns: int
    model_base_id: str
    model_context_window: int
    tools: tuple[dict[str, Any], ...]
    watchdog_seconds: int = DEFAULT_WATCHDOG_SECONDS
    max_prestart_retries: int = DEFAULT_MAX_PRESTART_RETRIES


@dataclass(frozen=True, slots=True)
class EngineRequest:
    run_id: str
    request_id: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EngineExecutionResult:
    status: Literal[
        "candidate_ready",
        "blocked",
        "budget_limited",
        "stopped",
        "failed",
    ]
    goal_round: int = 0
    tokens_used: int = 0
    model_turns: int = 0
    tool_calls: int = 0
    retry_count: int = 0
    public_error: str = ""


EngineEventCallback = Callable[
    [str, dict[str, Any]], Awaitable[None] | None
]
EngineRequestCallback = Callable[
    [EngineRequest], Awaitable[dict[str, Any]] | dict[str, Any]
]


class AppBuildEnginePort(Protocol):
    async def start_run(
        self,
        spec: EngineShadowRunSpec,
        *,
        on_event: EngineEventCallback,
        execute_model: EngineRequestCallback,
        execute_tool: EngineRequestCallback,
    ) -> EngineExecutionResult: ...

    async def stop_run(self, run_id: str) -> None: ...

    async def shutdown(self) -> None: ...


@dataclass(slots=True)
class _ActiveProcess:
    run_id: str
    process: asyncio.subprocess.Process
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    outgoing_seq: int = 1
    incoming_seq: int = 1
    last_protocol_at: float = field(default_factory=time.monotonic)
    started: bool = False
    stopping: bool = False
    terminal: asyncio.Future[EngineExecutionResult] | None = None
    request_tasks: set[asyncio.Task[Any]] = field(default_factory=set)


class NodeUpstreamEnginePort:
    """One Node 24 Penguin Core worker per Shadow run.

    The worker never receives a gateway credential. Model requests cross the
    protocol boundary and are executed by the Python callback.
    """

    _OUTBOUND_TYPES = frozenset(
        {
            "worker.hello",
            "worker.heartbeat",
            "run.started",
            "run.progress",
            "engine.omni",
            "engine.trace",
            "engine.trace_rotated",
            "model.request",
            "tool.request",
            "run.finished",
            "worker.fatal",
        }
    )

    def __init__(
        self,
        *,
        package_root: Path | None = None,
        node_executable: str | None = None,
        command_factory: Callable[[EngineShadowRunSpec], list[str]] | None = None,
        process_env: dict[str, str] | None = None,
    ) -> None:
        self.package_root = (
            package_root or Path(__file__).resolve().parent
        ).resolve()
        configured_worker_path = os.getenv("AGENT_UPSTREAM_WORKER_PATH", "").strip()
        self.worker_path = (
            Path(configured_worker_path).resolve()
            if configured_worker_path
            else self.package_root / "worker" / "src" / "worker.mjs"
        )
        self.node_executable = (
            node_executable
            or os.getenv("AGENT_UPSTREAM_NODE_EXECUTABLE", "node").strip()
            or "node"
        )
        self._command_factory = command_factory
        self._process_env = process_env
        self._active: dict[str, _ActiveProcess] = {}
        self._active_lock = asyncio.Lock()

    async def start_run(
        self,
        spec: EngineShadowRunSpec,
        *,
        on_event: EngineEventCallback,
        execute_model: EngineRequestCallback,
        execute_tool: EngineRequestCallback,
    ) -> EngineExecutionResult:
        last_error: EnginePortError | None = None
        retries = max(0, min(spec.max_prestart_retries, 5))
        for attempt in range(retries + 1):
            try:
                return await self._run_once(
                    spec,
                    on_event=on_event,
                    execute_model=execute_model,
                    execute_tool=execute_tool,
                    retry_count=attempt,
                )
            except EnginePortError as exc:
                last_error = exc
                if bool(getattr(exc, "worker_started", False)) or attempt >= retries:
                    raise
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
        raise last_error or EngineUnavailableError("Upstream worker did not start")

    async def _run_once(
        self,
        spec: EngineShadowRunSpec,
        *,
        on_event: EngineEventCallback,
        execute_model: EngineRequestCallback,
        execute_tool: EngineRequestCallback,
        retry_count: int,
    ) -> EngineExecutionResult:
        if not self.worker_path.is_file() and self._command_factory is None:
            raise EngineUnavailableError("Upstream worker entrypoint is missing")
        process = await self._spawn(spec)
        active = _ActiveProcess(run_id=spec.run_id, process=process)
        async with self._active_lock:
            if spec.run_id in self._active:
                await self._terminate(active)
                raise EnginePortError("Upstream run is already active")
            self._active[spec.run_id] = active

        hello: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        terminal: asyncio.Future[EngineExecutionResult] = (
            asyncio.get_running_loop().create_future()
        )
        active.terminal = terminal
        reader = asyncio.create_task(
            self._read_worker(
                active,
                hello=hello,
                terminal=terminal,
                on_event=on_event,
                execute_model=execute_model,
                execute_tool=execute_tool,
                retry_count=retry_count,
            )
        )
        stderr = asyncio.create_task(self._drain_stderr(process))
        monitor: asyncio.Task[Any] | None = None
        try:
            try:
                payload = await asyncio.wait_for(
                    asyncio.shield(hello), HANDSHAKE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError as exc:
                raise EngineUnavailableError("Upstream worker handshake timed out") from exc
            self._validate_hello(payload)
            await self._send(active, "run.start", self._start_payload(spec))
            monitor = asyncio.create_task(
                self._monitor(active, terminal, spec.watchdog_seconds)
            )
            result = await terminal
            return EngineExecutionResult(
                status=result.status,
                goal_round=result.goal_round,
                tokens_used=result.tokens_used,
                model_turns=result.model_turns,
                tool_calls=result.tool_calls,
                retry_count=retry_count,
                public_error=result.public_error,
            )
        except EnginePortError as exc:
            setattr(exc, "worker_started", active.started)
            raise
        finally:
            active.stopping = True
            if monitor is not None:
                monitor.cancel()
            for task in tuple(active.request_tasks):
                task.cancel()
            if process.returncode is None:
                try:
                    await self._send(
                        active,
                        "run.shutdown",
                        {"run_id": spec.run_id, "reason": "host_complete"},
                    )
                except EnginePortError:
                    pass
            await self._terminate(active)
            reader.cancel()
            stderr.cancel()
            await asyncio.gather(reader, stderr, *(active.request_tasks), return_exceptions=True)
            # A pre-handshake protocol failure completes both the handshake
            # and terminal futures with the same exception. The caller awaits
            # the handshake path, so explicitly retrieve the terminal error to
            # avoid leaking an unobserved Future warning.
            if terminal.done() and not terminal.cancelled():
                terminal.exception()
            async with self._active_lock:
                if self._active.get(spec.run_id) is active:
                    self._active.pop(spec.run_id, None)

    async def stop_run(self, run_id: str) -> None:
        async with self._active_lock:
            active = self._active.get(run_id)
        if active is None:
            return
        active.stopping = True
        terminal = active.terminal
        try:
            await self._send(active, "run.cancel", {"run_id": run_id, "reason": "user_stop"})
        except EnginePortError:
            if terminal is not None and not terminal.done():
                terminal.set_result(EngineExecutionResult(status="stopped"))
            await self._terminate(active)
            return

        if terminal is None or terminal.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(terminal), CANCEL_GRACE_SECONDS
            )
        except asyncio.TimeoutError:
            # Unblock the run owner before the common finally path sends the
            # final shutdown frame and terminates the whole process group.
            if not terminal.done():
                terminal.set_result(EngineExecutionResult(status="stopped"))

    async def shutdown(self) -> None:
        async with self._active_lock:
            run_ids = list(self._active)
        await asyncio.gather(*(self.stop_run(run_id) for run_id in run_ids))

    async def _spawn(self, spec: EngineShadowRunSpec) -> asyncio.subprocess.Process:
        command = (
            self._command_factory(spec)
            if self._command_factory is not None
            else self._permission_command(spec)
        )
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            return await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.package_root,
                env=self._minimal_environment(),
                limit=MAX_FRAME_BYTES + 1,
                **kwargs,
            )
        except (OSError, ValueError) as exc:
            raise EngineUnavailableError("Upstream worker could not be started") from exc

    def _permission_command(self, spec: EngineShadowRunSpec) -> list[str]:
        workspace = str(spec.workspace_dir.resolve(strict=True))
        package = str(self.package_root)
        return [
            self.node_executable,
            "--permission",
            f"--allow-fs-read={package}",
            f"--allow-fs-read={workspace}",
            f"--allow-fs-write={workspace}",
            str(self.worker_path),
        ]

    def _minimal_environment(self) -> dict[str, str]:
        if self._process_env is not None:
            return dict(self._process_env)
        allowed = {
            "PATH",
            "PATHEXT",
            "SystemRoot",
            "ComSpec",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "LANG",
            "LC_ALL",
        }
        return {key: value for key, value in os.environ.items() if key in allowed}

    async def _read_worker(
        self,
        active: _ActiveProcess,
        *,
        hello: asyncio.Future[dict[str, Any]],
        terminal: asyncio.Future[EngineExecutionResult],
        on_event: EngineEventCallback,
        execute_model: EngineRequestCallback,
        execute_tool: EngineRequestCallback,
        retry_count: int,
    ) -> None:
        assert active.process.stdout is not None
        try:
            while True:
                line = await active.process.stdout.readline()
                if not line:
                    if not terminal.done() and not active.stopping:
                        terminal.set_exception(
                            EngineUnavailableError("Upstream worker disconnected")
                        )
                    return
                active.last_protocol_at = time.monotonic()
                frame = self._decode_frame(active, line)
                frame_type = frame["type"]
                payload = frame["payload"]
                if frame_type == "worker.hello":
                    if hello.done():
                        raise EngineProtocolError("duplicate worker handshake")
                    hello.set_result(payload)
                elif frame_type == "worker.heartbeat":
                    continue
                elif frame_type == "run.started":
                    active.started = True
                    await self._call(on_event, "worker_started", {"retry_count": retry_count})
                elif frame_type in {"model.request", "tool.request"}:
                    callback = execute_model if frame_type == "model.request" else execute_tool
                    task = asyncio.create_task(
                        self._handle_request(active, frame_type, payload, callback)
                    )
                    active.request_tasks.add(task)
                    task.add_done_callback(
                        lambda finished, *, current=active, result=terminal: self._request_done(
                            finished, current, result
                        )
                    )
                elif frame_type == "run.finished":
                    if terminal.done():
                        raise EngineProtocolError("duplicate run.finished")
                    terminal.set_result(self._terminal_result(payload, retry_count))
                elif frame_type == "worker.fatal":
                    message = self._public_worker_error(payload.get("message"))
                    if not terminal.done():
                        terminal.set_exception(EngineProtocolError(message))
                else:
                    await self._call(on_event, frame_type, self._safe_event_payload(frame_type, payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = exc if isinstance(exc, EnginePortError) else EngineProtocolError(str(exc))
            if not hello.done():
                hello.set_exception(error)
            if not terminal.done():
                terminal.set_exception(error)

    async def _handle_request(
        self,
        active: _ActiveProcess,
        frame_type: str,
        payload: dict[str, Any],
        callback: EngineRequestCallback,
    ) -> None:
        request_id = payload.get("request_id")
        run_id = payload.get("run_id")
        if not isinstance(request_id, str) or not request_id:
            raise EngineProtocolError("worker request_id is invalid")
        if run_id != active.run_id:
            raise EngineProtocolError("worker request run_id mismatch")
        response_type = "model.response" if frame_type == "model.request" else "tool.response"
        try:
            result = callback(
                EngineRequest(
                    run_id=active.run_id,
                    request_id=request_id,
                    payload=dict(payload),
                )
            )

            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise EngineProtocolError("host callback result must be an object")
            await self._send(
                active,
                response_type,
                {"request_id": request_id, "ok": True, "result": result},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._send(
                active,
                response_type,
                {
                    "request_id": request_id,
                    "ok": False,
                    "error": self._public_callback_error(exc),
                },
            )

    @staticmethod
    def _request_done(
        task: asyncio.Task[Any],
        active: _ActiveProcess,
        terminal: asyncio.Future[EngineExecutionResult],
    ) -> None:
        active.request_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None or terminal.done():
            return
        terminal.set_exception(
            error
            if isinstance(error, EnginePortError)
            else EngineProtocolError("Upstream worker request handling failed")
        )

    async def _monitor(
        self,
        active: _ActiveProcess,
        terminal: asyncio.Future[EngineExecutionResult],
        watchdog_seconds: int,
    ) -> None:
        deadline = time.monotonic() + max(1, watchdog_seconds)
        while not terminal.done():
            await asyncio.sleep(0.5)
            now = time.monotonic()
            if now >= deadline:
                terminal.set_exception(EngineWatchdogError("Upstream run exceeded the host watchdog"))
                return
            if now - active.last_protocol_at > IDLE_TIMEOUT_SECONDS:
                terminal.set_exception(EngineUnavailableError("Upstream worker heartbeat was lost"))
                return
            if active.process.returncode is not None:
                terminal.set_exception(EngineUnavailableError("Upstream worker exited unexpectedly"))
                return

    async def _send(
        self, active: _ActiveProcess, frame_type: str, payload: dict[str, Any]
    ) -> None:
        if active.process.stdin is None or active.process.returncode is not None:
            raise EngineUnavailableError("Upstream worker is not writable")
        async with active.write_lock:
            frame = {
                "protocol": ENGINE_PROTOCOL,
                "seq": active.outgoing_seq,
                "type": frame_type,
                "payload": payload,
            }
            encoded = json.dumps(
                frame, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(encoded) > MAX_FRAME_BYTES:
                raise EngineProtocolError("host protocol frame exceeds 4 MiB")
            active.outgoing_seq += 1
            active.process.stdin.write(encoded + b"\n")
            await active.process.stdin.drain()

    def _decode_frame(self, active: _ActiveProcess, raw: bytes) -> dict[str, Any]:
        if len(raw) > MAX_FRAME_BYTES + 1 or not raw.endswith(b"\n"):
            raise EngineProtocolError("worker protocol frame is oversized or truncated")
        try:
            frame = json.loads(raw[:-1].decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EngineProtocolError("worker stdout was not valid UTF-8 NDJSON") from exc
        if not isinstance(frame, dict) or set(frame) != {"protocol", "seq", "type", "payload"}:
            raise EngineProtocolError("worker frame envelope is invalid")
        if frame["protocol"] != ENGINE_PROTOCOL:
            raise EngineProtocolError("worker protocol mismatch")
        if frame["seq"] != active.incoming_seq:
            raise EngineProtocolError("worker protocol sequence mismatch")
        active.incoming_seq += 1
        if frame["type"] not in self._OUTBOUND_TYPES or not isinstance(frame["payload"], dict):
            raise EngineProtocolError("worker frame type or payload is invalid")
        return frame

    @staticmethod
    def _validate_hello(payload: dict[str, Any]) -> None:
        if payload.get("upstream_revision") != UPSTREAM_REVISION:
            raise EngineProtocolError("worker upstream revision mismatch")
        version = str(payload.get("node_version") or "")
        if not version.startswith("v24."):
            raise EngineUnavailableError("Upstream worker requires Node 24")
        if set(payload.get("capabilities") or []) != {"read_file", "write_file", "edit_file"}:
            raise EngineProtocolError("worker capability set is invalid")

    @staticmethod
    def _start_payload(spec: EngineShadowRunSpec) -> dict[str, Any]:
        return {
            "run_id": spec.run_id,
            "session_id": spec.session_id,
            "objective": spec.objective,
            "workspace_dir": str(spec.workspace_dir),
            "goal_file_path": str(spec.goal_file_path),
            "system_prompt": spec.system_prompt,
            "thinking_level": spec.thinking_level,
            "token_budget": spec.token_budget,
            "max_goal_rounds": spec.max_goal_rounds,
            "max_task_turns": spec.max_task_turns,
            "model_base_id": spec.model_base_id,
            "model_context_window": spec.model_context_window,
            "tools": list(spec.tools),
        }

    @staticmethod
    def _terminal_result(payload: dict[str, Any], retry_count: int) -> EngineExecutionResult:
        status = payload.get("status")
        if status not in {"candidate_ready", "blocked", "budget_limited", "stopped", "failed"}:
            raise EngineProtocolError("worker returned an invalid terminal status")
        goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else {}
        stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
        return EngineExecutionResult(
            status=status,
            goal_round=max(0, int(goal.get("rounds") or 0)),
            tokens_used=max(0, int(goal.get("tokens_used") or 0)),
            model_turns=max(0, int(stats.get("model_turns") or 0)),
            tool_calls=max(0, int(stats.get("tool_calls") or 0)),
            retry_count=retry_count,
            public_error=str(payload.get("error") or "")[:1_000],
        )

    @staticmethod
    def _safe_event_payload(frame_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Model and trace bodies are never persisted by the port. The service
        # receives only enough structure to derive aggregate counters.
        if frame_type in {"engine.omni", "engine.trace", "engine.trace_rotated"}:
            return {"run_id": payload.get("run_id")}
        return dict(payload)

    @staticmethod
    def _public_callback_error(exc: Exception) -> str:
        if isinstance(exc, EnginePortError):
            return str(exc)[:1_000]
        return "The ModelMirror host callback failed."

    @staticmethod
    def _public_worker_error(value: Any) -> str:
        text = str(value or "Upstream worker protocol failed")
        return text[:1_000]

    @staticmethod
    async def _call(
        callback: EngineEventCallback, event_type: str, payload: dict[str, Any]
    ) -> None:
        result = callback(event_type, payload)
        if inspect.isawaitable(result):
            await result

    async def _terminate(self, active: _ActiveProcess) -> None:
        process = active.process
        if process.returncode is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), KILL_GRACE_SECONDS)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    async def _drain_stderr(process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        while await process.stderr.read(16_384):
            pass
