from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .client import (
    MAX_MESSAGE_BYTES,
    AgencyWorkerClient,
    AgencyWorkerError,
    ModelRunner,
)
from .contracts import (
    AgencyAgentDefinition,
    AgencyExecutionWorkerResult,
    AgencyModelRequest,
    AgencyModelResponse,
    AgencySkillDefinition,
)


AGENCY_EXECUTION_PROTOCOL = "mm-agency-bridge/v2"
AGENCY_HITL_PROTOCOL = "mm-agency-bridge/v3"
MAX_EXECUTION_MODEL_CALLS = 10
MAX_EXECUTION_CONCURRENCY = 2
MAX_MODEL_REQUEST_SECONDS = 240.0
MAX_EXECUTION_SECONDS = 900.0

ExecutionEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class AgencyExecutionClient:
    """Fail-closed v2 host with bounded, correlated model request concurrency."""

    def __init__(
        self,
        *,
        model_runner: ModelRunner | None = None,
        node_binary: str = "node",
        worker_entry: str | Path | None = None,
        timeout_seconds: float = MAX_EXECUTION_SECONDS,
    ) -> None:
        if not 0.05 <= timeout_seconds <= MAX_EXECUTION_SECONDS:
            raise ValueError("Agency execution timeout is invalid")
        self.model_runner = model_runner
        self.node_binary = str(node_binary)
        self.worker_entry = Path(
            worker_entry or AgencyWorkerClient.default_worker_entry()
        ).resolve()
        self.timeout_seconds = float(timeout_seconds)

    @property
    def argv(self) -> tuple[str, ...]:
        return (
            self.node_binary,
            "--max-old-space-size=256",
            str(self.worker_entry),
        )

    async def execute(
        self,
        *,
        goal: str,
        model_id: str,
        workflow: Mapping[str, Any],
        agents: Sequence[AgencyAgentDefinition],
        skills: Sequence[AgencySkillDefinition] = (),
        resume: Mapping[str, Any] | None = None,
        revision: Mapping[str, Any] | None = None,
        interaction_resume: Mapping[str, Any] | None = None,
        on_event: ExecutionEventHandler | None = None,
    ) -> AgencyExecutionWorkerResult:
        if not self.worker_entry.is_file():
            raise AgencyWorkerError(
                "Agency worker build output is unavailable.",
                code="worker_unavailable",
            )
        continuations = sum(
            value is not None for value in (resume, revision, interaction_resume)
        )
        if continuations > 1:
            raise AgencyWorkerError(
                "Agency execution continuations are mutually exclusive.",
                code="agency_execution_plan_invalid",
            )
        workflow_steps = workflow.get("steps")
        has_hitl = any(
            isinstance(step, Mapping)
            and str(step.get("type") or "normal") in {"human_input", "approval"}
            for step in (workflow_steps if isinstance(workflow_steps, Sequence) else [])
        )
        protocol = (
            AGENCY_HITL_PROTOCOL
            if has_hitl or interaction_resume is not None
            else AGENCY_EXECUTION_PROTOCOL
        )
        request_id = f"agency_exec_{uuid.uuid4().hex}"
        request = {
            "protocol": protocol,
            "type": "request",
            "id": request_id,
            "method": "execute",
            "params": {
                "goal": goal,
                "model_id": model_id,
                "workflow": dict(workflow),
                "agents": [agent.model_dump(mode="json") for agent in agents],
                "skills": [skill.model_dump(mode="json") for skill in skills],
                **({"resume": dict(resume)} if resume is not None else {}),
                **({"revision": dict(revision)} if revision is not None else {}),
                **(
                    {"interaction_resume": dict(interaction_resume)}
                    if interaction_resume is not None
                    else {}
                ),
            },
        }
        encoded = AgencyWorkerClient._encode(
            request, code="worker_request_too_large"
        )
        process: asyncio.subprocess.Process | None = None
        stderr = bytearray()
        stderr_task: asyncio.Task[None] | None = None
        model_tasks: set[asyncio.Task[None]] = set()
        all_model_tasks: list[asyncio.Task[None]] = []
        write_lock = asyncio.Lock()
        model_semaphore = asyncio.Semaphore(MAX_EXECUTION_CONCURRENCY)
        started = time.monotonic()
        prior_model_calls = (
            int((resume or interaction_resume or {}).get("prior_model_calls") or 0)
            if resume is not None or interaction_resume is not None
            else 0
        )
        prior_active_duration_ms = int(
            (interaction_resume or {}).get("prior_active_duration_ms") or 0
        )
        if not 0 <= prior_active_duration_ms < int(MAX_EXECUTION_SECONDS * 1000):
            raise AgencyWorkerError(
                "Agency active execution budget is exhausted.",
                code="agency_execution_timeout",
            )
        segment_timeout = min(
            self.timeout_seconds,
            MAX_EXECUTION_SECONDS - prior_active_duration_ms / 1000,
        )
        if not 0 <= prior_model_calls <= MAX_EXECUTION_MODEL_CALLS:
            raise AgencyWorkerError(
                "Agency resume model-call budget is invalid.",
                code="agency_execution_plan_invalid",
            )
        model_calls = prior_model_calls
        try:
            process, stderr, stderr_task = await AgencyWorkerClient._spawn_process(
                self.argv,
                self.worker_entry.parent,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            process.stdin.write(encoded)
            await process.stdin.drain()

            while True:
                remaining = segment_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise AgencyWorkerError(
                        "Agency execution timed out.",
                        code="agency_execution_timeout",
                    )
                line = await AgencyWorkerClient._read_stdout_line(
                    process,
                    timeout_seconds=remaining,
                    timeout_code="agency_execution_timeout",
                    timeout_message="Agency execution timed out.",
                )
                message = AgencyWorkerClient._decode(line)
                if (
                    message.get("protocol") != protocol
                    or message.get("id") != request_id
                ):
                    raise AgencyWorkerError(
                        "Agency worker returned an invalid execution message.",
                        code="worker_protocol_invalid",
                    )
                message_type = message.get("type")
                if message_type == "model_request":
                    model_calls += 1
                    if model_calls > MAX_EXECUTION_MODEL_CALLS:
                        raise AgencyWorkerError(
                            "Agency execution exceeded the model call limit.",
                            code="agency_execution_budget_exceeded",
                        )
                    task = asyncio.create_task(
                        self._serve_model_request(
                            process,
                            message,
                            semaphore=model_semaphore,
                            write_lock=write_lock,
                            protocol=protocol,
                        )
                    )
                    model_tasks.add(task)
                    all_model_tasks.append(task)
                    task.add_done_callback(model_tasks.discard)
                    continue
                if message_type == "event":
                    event = message.get("event")
                    if not isinstance(event, dict) or not isinstance(
                        event.get("event"), str
                    ):
                        raise AgencyWorkerError(
                            "Agency execution event is invalid.",
                            code="worker_protocol_invalid",
                        )
                    if on_event is not None:
                        callback_result = on_event(dict(event))
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    continue
                if message_type != "response":
                    raise AgencyWorkerError(
                        "Agency worker returned an unexpected execution message.",
                        code="worker_protocol_invalid",
                    )
                if message.get("ok") is not True:
                    error = message.get("error")
                    error = error if isinstance(error, dict) else {}
                    raise AgencyWorkerError(
                        str(error.get("message") or "Agency execution failed."),
                        code=str(error.get("code") or "worker_failed"),
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise AgencyWorkerError(
                        "Agency execution result is invalid.",
                        code="worker_protocol_invalid",
                    )
                if all_model_tasks:
                    completed = await asyncio.gather(
                        *all_model_tasks, return_exceptions=True
                    )
                    failure = next(
                        (item for item in completed if isinstance(item, Exception)),
                        None,
                    )
                    if failure is not None:
                        raise AgencyWorkerError(
                            "Agency model response bridge failed.",
                            code="worker_failed",
                        ) from failure
                process.stdin.close()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                return AgencyExecutionWorkerResult(
                    payload=result,
                    model_calls=model_calls,
                )
        except FileNotFoundError as exc:
            raise AgencyWorkerError(
                "Node runtime is unavailable for Agency worker.",
                code="worker_unavailable",
            ) from exc
        except AgencyWorkerError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgencyWorkerError(
                "Agency execution bridge failed.", code="worker_failed"
            ) from exc
        finally:
            for task in tuple(model_tasks):
                task.cancel()
            if all_model_tasks:
                await asyncio.gather(*all_model_tasks, return_exceptions=True)
            if process is not None and process.returncode is None:
                await AgencyWorkerClient._interrupt_process(process)
            if stderr_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task

    async def _serve_model_request(
        self,
        process: asyncio.subprocess.Process,
        message: dict[str, Any],
        *,
        semaphore: asyncio.Semaphore,
        write_lock: asyncio.Lock,
        protocol: str,
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
            expected_temperature = 0.0 if request.json_response else 0.3
            max_tokens = 2_000 if request.json_response else 4_096
            if request.temperature != expected_temperature or request.max_tokens > max_tokens:
                raise AgencyWorkerError(
                    "Agency execution model limits are invalid.",
                    code="worker_protocol_invalid",
                )
            if self.model_runner is None:
                raise AgencyWorkerError(
                    "Agency model runner is not configured.",
                    code="model_runner_unavailable",
                )
            async with semaphore:
                try:
                    response = await asyncio.wait_for(
                        self.model_runner(request),
                        timeout=MAX_MODEL_REQUEST_SECONDS,
                    )
                except TimeoutError as exc:
                    raise AgencyWorkerError(
                        "Agency model request timed out.",
                        code="agency_execution_timeout",
                    ) from exc
            if isinstance(response, str):
                response = AgencyModelResponse(content=response)
            if len(response.content.encode("utf-8")) > 64 * 1024:
                raise AgencyWorkerError(
                    "Agency model response exceeds 64 KiB.",
                    code="agency_execution_budget_exceeded",
                )
            envelope: dict[str, Any] = {
                "protocol": protocol,
                "type": "model_response",
                "id": message.get("id"),
                "request_id": request_id,
                "ok": True,
                "result": response.model_dump(mode="json"),
            }
        except Exception as exc:
            envelope = {
                "protocol": protocol,
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
        encoded = AgencyWorkerClient._encode(
            envelope, code="model_response_too_large"
        )
        async with write_lock:
            process.stdin.write(encoded)
            await process.stdin.drain()
