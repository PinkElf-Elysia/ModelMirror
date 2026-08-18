from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from .contracts import (
    AgencyAgentDefinition,
    AgencyModelRequest,
    AgencyModelResponse,
    AgencyWorkerResult,
)


AGENCY_BRIDGE_PROTOCOL = "mm-agency-bridge/v1"
AGENCY_UPSTREAM_REVISION = "e3f69fdf9da8a4630edbb8abeb116893b983b57d"
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 512 * 1024
MAX_MODEL_CALLS = 3
DEFAULT_WORKER_TIMEOUT_SECONDS = 300.0
MAX_WORKER_TIMEOUT_SECONDS = 600.0

ModelRunner = Callable[[AgencyModelRequest], Awaitable[AgencyModelResponse | str]]

logger = logging.getLogger(__name__)


class AgencyWorkerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        diagnostics: str = "",
        usage: Mapping[str, int] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics
        self.usage = {
            str(key): max(0, int(value))
            for key, value in (usage or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        self.finish_reason = str(finish_reason or "")[:80] or None


class AgencyWorkerClient:
    """Single-request, fail-closed host for the vendored Node planner."""

    _ENV_ALLOWLIST = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "TZ",
    }

    def __init__(
        self,
        *,
        model_runner: ModelRunner | None = None,
        node_binary: str = "node",
        worker_entry: str | Path | None = None,
        asset_root: str | Path | None = None,
        timeout_seconds: float = DEFAULT_WORKER_TIMEOUT_SECONDS,
    ) -> None:
        if not 0.05 <= timeout_seconds <= MAX_WORKER_TIMEOUT_SECONDS:
            raise ValueError("Agency worker timeout is invalid")
        self.model_runner = model_runner
        self.node_binary = str(node_binary)
        self.worker_entry = Path(worker_entry or self.default_worker_entry()).resolve()
        self.asset_root = Path(asset_root).resolve() if asset_root else None
        self.timeout_seconds = float(timeout_seconds)
        self._asset_lock = asyncio.Lock()

    @staticmethod
    def default_worker_entry() -> Path:
        return (
            Path(__file__).resolve().parent
            / "dist"
            / "orchestration_worker"
            / "src"
            / "index.js"
        )

    @property
    def argv(self) -> tuple[str, ...]:
        return (
            self.node_binary,
            "--max-old-space-size=256",
            str(self.worker_entry),
        )

    @classmethod
    def sanitized_environment(
        cls, source: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        values = os.environ if source is None else source
        environment = {
            key: str(values[key])
            for key in cls._ENV_ALLOWLIST
            if key in values and str(values[key])
        }
        environment["MM_AGENCY_WORKER"] = "1"
        return environment

    async def health(self) -> dict[str, Any]:
        return (await self.call("health", {})).payload

    async def validate(
        self,
        *,
        yaml: str,
        agents: Sequence[AgencyAgentDefinition],
    ) -> dict[str, Any]:
        return (
            await self.call(
                "validate",
                {
                    "yaml": yaml,
                    "agents": [agent.model_dump(mode="json") for agent in agents],
                },
            )
        ).payload

    async def compose(
        self,
        *,
        goal: str,
        model_id: str,
        agents: Sequence[AgencyAgentDefinition],
        mode: Literal["auto", "pinned"] = "auto",
        pinned_agent_ids: Sequence[str] = (),
        max_agents: int = 5,
        temperature: float = 0.2,
        allow_hitl: bool = False,
    ) -> dict[str, Any]:
        return (
            await self.call(
                "compose",
                {
                    "goal": goal,
                    "model_id": model_id,
                    "agents": [agent.model_dump(mode="json") for agent in agents],
                    "mode": mode,
                    "pinned_agent_ids": list(pinned_agent_ids),
                    "max_agents": max_agents,
                    "temperature": temperature,
                    "allow_hitl": bool(allow_hitl),
                },
            )
        ).payload

    async def assets(
        self,
        action: Literal["list", "save_team", "save_template"],
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._asset_lock:
            return (
                await self.call(
                    "assets",
                    {"action": action, **dict(payload or {})},
                )
            ).payload

    async def call(
        self,
        method: Literal["health", "compose", "validate", "assets"],
        params: Mapping[str, Any],
    ) -> AgencyWorkerResult:
        if not self.worker_entry.is_file():
            raise AgencyWorkerError(
                "Agency worker build output is unavailable.",
                code="worker_unavailable",
            )
        request_id = f"agency_{uuid.uuid4().hex}"
        request = {
            "protocol": AGENCY_BRIDGE_PROTOCOL,
            "type": "request",
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        encoded = self._encode(request, code="worker_request_too_large")
        process: asyncio.subprocess.Process | None = None
        stderr = bytearray()
        stderr_task: asyncio.Task[None] | None = None
        started = time.monotonic()
        model_calls = 0
        try:
            environment = self.sanitized_environment()
            if method == "assets":
                if self.asset_root is None:
                    raise AgencyWorkerError(
                        "Agency asset store is unavailable.",
                        code="agency_asset_store_unavailable",
                    )
                environment["MM_AGENCY_ASSET_ROOT"] = str(self.asset_root)
            process, stderr, stderr_task = await self._spawn_process(
                self.argv,
                self.worker_entry.parent,
                environment=environment,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            process.stdin.write(encoded)
            await process.stdin.drain()

            while True:
                remaining = self.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise AgencyWorkerError(
                        "Agency worker timed out.", code="worker_timeout"
                    )
                line = await self._read_stdout_line(
                    process,
                    timeout_seconds=remaining,
                    timeout_code="worker_timeout",
                    timeout_message="Agency worker timed out.",
                )
                message = self._decode(line)
                if (
                    message.get("protocol") != AGENCY_BRIDGE_PROTOCOL
                    or message.get("id") != request_id
                ):
                    raise AgencyWorkerError(
                        "Agency worker returned an invalid protocol message.",
                        code="worker_protocol_invalid",
                    )
                if message.get("type") == "model_request":
                    model_calls += 1
                    if model_calls > MAX_MODEL_CALLS:
                        raise AgencyWorkerError(
                            "Agency worker exceeded the model call limit.",
                            code="model_call_limit",
                        )
                    await self._serve_model_request(
                        process,
                        message,
                        timeout_seconds=max(0.05, remaining),
                    )
                    continue
                if message.get("type") != "response":
                    raise AgencyWorkerError(
                        "Agency worker returned an unexpected message type.",
                        code="worker_protocol_invalid",
                    )
                if message.get("ok") is not True:
                    error = message.get("error")
                    error = error if isinstance(error, dict) else {}
                    process.stdin.close()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    if stderr_task is not None:
                        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                            await asyncio.wait_for(
                                asyncio.shield(stderr_task), timeout=1.0
                            )
                    diagnostics = self._safe_worker_diagnostics(stderr)
                    if diagnostics:
                        logger.warning(
                            "Agency worker returned %s: %s",
                            str(error.get("code") or "worker_failed"),
                            diagnostics,
                        )
                    raise AgencyWorkerError(
                        str(error.get("message") or "Agency worker failed."),
                        code=str(error.get("code") or "worker_failed"),
                        diagnostics=diagnostics,
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise AgencyWorkerError(
                        "Agency worker result is invalid.",
                        code="worker_protocol_invalid",
                    )
                process.stdin.close()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                return AgencyWorkerResult(payload=result, model_calls=model_calls)
        except FileNotFoundError as exc:
            raise AgencyWorkerError(
                "Node runtime is unavailable for Agency worker.",
                code="worker_unavailable",
            ) from exc
        except AgencyWorkerError:
            raise
        except Exception as exc:
            raise AgencyWorkerError(
                "Agency worker bridge failed.", code="worker_failed"
            ) from exc
        finally:
            if process is not None and process.returncode is None:
                await self._interrupt_process(process)
            if stderr_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task

    async def _serve_model_request(
        self,
        process: asyncio.subprocess.Process,
        message: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> None:
        assert process.stdin is not None
        request_id = str(message.get("request_id") or "")
        try:
            request = AgencyModelRequest.model_validate(
                {
                    "request_id": request_id,
                    "model_id": message.get("model_id"),
                    "messages": message.get("messages"),
                    "temperature": message.get("temperature"),
                    "max_tokens": message.get("max_tokens"),
                    "json_response": message.get("json_response", False),
                }
            )
            if self.model_runner is None:
                raise AgencyWorkerError(
                    "Agency model runner is not configured.",
                    code="model_runner_unavailable",
                )
            try:
                response = await asyncio.wait_for(
                    self.model_runner(request),
                    timeout=timeout_seconds,
                )
            except TimeoutError as exc:
                raise AgencyWorkerError(
                    "模型网关请求超过规划等待时间。上游可能仍在处理并产生费用，请勿立即自动重试。",
                    code="model_gateway_timeout",
                ) from exc
            if isinstance(response, str):
                response = AgencyModelResponse(content=response)
            envelope: dict[str, Any] = {
                "protocol": AGENCY_BRIDGE_PROTOCOL,
                "type": "model_response",
                "id": message["id"],
                "request_id": request_id,
                "ok": True,
                "result": response.model_dump(mode="json"),
            }
        except Exception as exc:
            # The planner-wide deadline can cancel an in-flight gateway request.
            # Surface that boundary directly: there is no time left for Node to
            # consume an error envelope, and calling it a Worker failure hides
            # the upstream latency (and possible late billing) from the user.
            if (
                isinstance(exc, AgencyWorkerError)
                and exc.code == "model_gateway_timeout"
            ):
                raise
            envelope = {
                "protocol": AGENCY_BRIDGE_PROTOCOL,
                "type": "model_response",
                "id": message.get("id"),
                "request_id": request_id,
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", "model_call_failed"),
                    "message": (
                        str(exc)
                        if isinstance(exc, (AgencyWorkerError, ValidationError))
                        else "Model call failed."
                    ),
                    "usage": (
                        exc.usage if isinstance(exc, AgencyWorkerError) else {}
                    ),
                    "finish_reason": (
                        exc.finish_reason
                        if isinstance(exc, AgencyWorkerError)
                        else None
                    ),
                },
            }
        process.stdin.write(self._encode(envelope, code="model_response_too_large"))
        await process.stdin.drain()

    @staticmethod
    async def _drain_stderr(
        stream: asyncio.StreamReader,
        target: bytearray,
    ) -> None:
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return
            remaining = MAX_STDERR_BYTES - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])

    @staticmethod
    def _safe_worker_diagnostics(stderr: bytearray) -> str:
        """Keep only a bounded stack tail for server-side diagnosis."""

        lines = [
            line.strip()
            for line in bytes(stderr).decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        if not lines:
            return ""
        error_line = next(
            (
                index
                for index in range(len(lines) - 1, -1, -1)
                if re.search(
                    r"(?:^|\b)(?:Error|TypeError|ReferenceError|RangeError|SyntaxError):",
                    lines[index],
                )
            ),
            max(0, len(lines) - 4),
        )
        return "\n".join(lines[error_line : error_line + 8])[:4_000]

    @classmethod
    async def _spawn_process(
        cls,
        argv: Sequence[str],
        cwd: str | Path,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> tuple[asyncio.subprocess.Process, bytearray, asyncio.Task[None]]:
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=(dict(environment) if environment is not None else cls.sanitized_environment()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_MESSAGE_BYTES + 1,
            **kwargs,
        )
        assert process.stderr is not None
        stderr = bytearray()
        stderr_task = asyncio.create_task(cls._drain_stderr(process.stderr, stderr))
        return process, stderr, stderr_task

    @staticmethod
    async def _read_stdout_line(
        process: asyncio.subprocess.Process,
        *,
        timeout_seconds: float,
        timeout_code: str,
        timeout_message: str,
    ) -> bytes:
        assert process.stdout is not None
        try:
            line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise AgencyWorkerError(
                timeout_message,
                code=timeout_code,
            ) from exc
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise AgencyWorkerError(
                "Agency worker message exceeds 2 MiB.",
                code="worker_message_too_large",
            ) from exc
        if not line:
            raise AgencyWorkerError(
                "Agency worker exited before returning a response.",
                code="worker_crashed",
            )
        return line

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

    @staticmethod
    def _encode(message: Mapping[str, Any], *, code: str) -> bytes:
        encoded = (
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise AgencyWorkerError("Bridge message exceeds 2 MiB.", code=code)
        return encoded

    @staticmethod
    def _decode(line: bytes) -> dict[str, Any]:
        if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
            raise AgencyWorkerError(
                "Agency worker message exceeds 2 MiB or is incomplete.",
                code="worker_message_too_large",
            )
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgencyWorkerError(
                "Agency worker returned invalid JSON.", code="worker_invalid_json"
            ) from exc
        if not isinstance(value, dict):
            raise AgencyWorkerError(
                "Agency worker returned a non-object message.",
                code="worker_protocol_invalid",
            )
        return value
