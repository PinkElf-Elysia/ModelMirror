from __future__ import annotations

import asyncio
import contextlib
import json
import os
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

ModelRunner = Callable[[AgencyModelRequest], Awaitable[AgencyModelResponse | str]]


class AgencyWorkerError(RuntimeError):
    def __init__(self, message: str, *, code: str, diagnostics: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics


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
        timeout_seconds: float = 300.0,
    ) -> None:
        if not 0.05 <= timeout_seconds <= 300:
            raise ValueError("Agency worker timeout is invalid")
        self.model_runner = model_runner
        self.node_binary = str(node_binary)
        self.worker_entry = Path(worker_entry or self.default_worker_entry()).resolve()
        self.timeout_seconds = float(timeout_seconds)

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
        values = source or os.environ
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
                },
            )
        ).payload

    async def call(
        self,
        method: Literal["health", "compose", "validate"],
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
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            process = await asyncio.create_subprocess_exec(
                *self.argv,
                cwd=str(self.worker_entry.parent),
                env=self.sanitized_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_MESSAGE_BYTES + 1,
                **kwargs,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            stderr_task = asyncio.create_task(self._drain_stderr(process.stderr, stderr))
            process.stdin.write(encoded)
            await process.stdin.drain()

            while True:
                remaining = self.timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise AgencyWorkerError(
                        "Agency worker timed out.", code="worker_timeout"
                    )
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
                except TimeoutError as exc:
                    raise AgencyWorkerError(
                        "Agency worker timed out.", code="worker_timeout"
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
                    await self._serve_model_request(process, message)
                    continue
                if message.get("type") != "response":
                    raise AgencyWorkerError(
                        "Agency worker returned an unexpected message type.",
                        code="worker_protocol_invalid",
                    )
                if message.get("ok") is not True:
                    error = message.get("error")
                    error = error if isinstance(error, dict) else {}
                    raise AgencyWorkerError(
                        str(error.get("message") or "Agency worker failed."),
                        code=str(error.get("code") or "worker_failed"),
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
                }
            )
            if self.model_runner is None:
                raise AgencyWorkerError(
                    "Agency model runner is not configured.",
                    code="model_runner_unavailable",
                )
            response = await self.model_runner(request)
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
