from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .commands import ProjectCommand


MAX_COMMANDS_PER_TURN = 20
MAX_COMMAND_EXECUTION_SECONDS = 600.0
COMMAND_CONFIRMATION_TIMEOUT_SECONDS = 300.0


class CommandBridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CommandRequestState(StrEnum):
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CommandDecision(StrEnum):
    ALLOW_ONCE = "allow_once"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class CommandExecutionResult:
    status: str
    exit_code: int | None
    output: str
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "output": self.output,
            "duration_seconds": round(max(0.0, self.duration_seconds), 3),
        }


@dataclass(slots=True)
class CommandRequest:
    request_id: str
    session_id: str
    turn_id: str
    command: ProjectCommand
    created_at: float
    expires_at: float
    state: CommandRequestState = CommandRequestState.AWAITING_CONFIRMATION
    resolved_at: float | None = None
    result: CommandExecutionResult | None = None
    _decision: asyncio.Future[CommandDecision] | None = None
    _execution: asyncio.Task[CommandExecutionResult] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "command": self.command.to_public_dict(),
            "state": self.state.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "result": None if self.result is None else self.result.to_dict(),
        }


CommandExecutor = Callable[[ProjectCommand, float], Awaitable[CommandExecutionResult]]
CommandObserver = Callable[[CommandRequest], Awaitable[None]]


class CommandConfirmationBridge:
    """Per-turn, in-memory confirmation gate for one structured command at a time."""

    def __init__(
        self,
        *,
        confirmation_timeout: float = COMMAND_CONFIRMATION_TIMEOUT_SECONDS,
        max_commands: int = MAX_COMMANDS_PER_TURN,
        max_execution_seconds: float = MAX_COMMAND_EXECUTION_SECONDS,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        observer: CommandObserver | None = None,
    ) -> None:
        if confirmation_timeout <= 0 or max_commands < 1 or max_execution_seconds <= 0:
            raise ValueError("Command bridge limits must be positive")
        self._confirmation_timeout = confirmation_timeout
        self._max_commands = max_commands
        self._max_execution_seconds = max_execution_seconds
        self._clock = clock
        self._monotonic = monotonic
        self._observer = observer
        self._active_turn_id: str | None = None
        self._request_count = 0
        self._execution_seconds = 0.0
        self._current: CommandRequest | None = None
        self._resolved: dict[str, CommandRequest] = {}
        self._lock = asyncio.Lock()

    async def begin_turn(self, turn_id: str) -> None:
        if not isinstance(turn_id, str) or not turn_id:
            raise CommandBridgeError("command_turn_invalid", "Command turn is invalid")
        async with self._lock:
            if self._active_turn_id is not None:
                raise CommandBridgeError("command_turn_busy", "Another command turn is active")
            self._active_turn_id = turn_id
            self._request_count = 0
            self._execution_seconds = 0.0
            self._current = None
            self._resolved.clear()

    async def finish_turn(self, reason: str = "turn_finished") -> None:
        await self.cancel_pending(reason=reason)
        async with self._lock:
            self._active_turn_id = None

    async def request(
        self,
        *,
        session_id: str,
        turn_id: str,
        command: ProjectCommand,
        executor: CommandExecutor,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        now = self._clock()
        request = CommandRequest(
            request_id="command-request-" + secrets.token_hex(16),
            session_id=session_id,
            turn_id=turn_id,
            command=command,
            created_at=now,
            expires_at=now + self._confirmation_timeout,
            _decision=loop.create_future(),
        )
        async with self._lock:
            if self._active_turn_id != turn_id:
                raise CommandBridgeError("command_turn_inactive", "Command turn is not active")
            if self._current is not None:
                raise CommandBridgeError("command_request_busy", "Another command is pending")
            if self._request_count >= self._max_commands:
                raise CommandBridgeError("command_limit_reached", "Command limit reached")
            if self._execution_seconds >= self._max_execution_seconds:
                raise CommandBridgeError("command_time_limit_reached", "Command time limit reached")
            self._request_count += 1
            self._current = request
        await self._notify(request)

        try:
            try:
                decision = await asyncio.wait_for(
                    asyncio.shield(request._decision),
                    timeout=self._confirmation_timeout,
                )
            except TimeoutError:
                await self._resolve_without_execution(
                    request,
                    CommandRequestState.TIMED_OUT,
                    status="confirmation_timed_out",
                )
                return self._tool_result(request)

            if decision is CommandDecision.REJECT:
                await self._resolve_without_execution(
                    request,
                    CommandRequestState.REJECTED,
                    status="rejected",
                )
                return self._tool_result(request)

            async with self._lock:
                if self._current is not request:
                    return self._tool_result(request)
                request.state = CommandRequestState.RUNNING
                remaining = max(
                    0.0,
                    self._max_execution_seconds - self._execution_seconds,
                )
            await self._notify(request)
            if remaining <= 0:
                await self._resolve_without_execution(
                    request,
                    CommandRequestState.REJECTED,
                    status="command_time_limit_reached",
                )
                return self._tool_result(request)

            started = self._monotonic()
            request._execution = asyncio.create_task(executor(command, remaining))
            try:
                result = await request._execution
            except asyncio.CancelledError:
                await self._resolve_without_execution(
                    request,
                    CommandRequestState.CANCELLED,
                    status="turn_cancelled",
                )
                if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                    raise
                return self._tool_result(request)
            except Exception:
                result = CommandExecutionResult(
                    status="execution_failed",
                    exit_code=None,
                    output="The approved check could not be completed.",
                    duration_seconds=max(0.0, self._monotonic() - started),
                )
                terminal_state = CommandRequestState.FAILED
            else:
                terminal_state = CommandRequestState.COMPLETED
            duration = max(result.duration_seconds, self._monotonic() - started, 0.0)
            async with self._lock:
                self._execution_seconds = min(
                    self._max_execution_seconds,
                    self._execution_seconds + duration,
                )
                request.result = result
                request.state = terminal_state
                request.resolved_at = self._clock()
                self._archive_current(request)
            await self._notify(request)
            return self._tool_result(request)
        finally:
            async with self._lock:
                if self._current is request and request.state not in {
                    CommandRequestState.AWAITING_CONFIRMATION,
                    CommandRequestState.RUNNING,
                }:
                    self._archive_current(request)

    async def pending(self, *, session_id: str, turn_id: str | None = None) -> dict[str, Any] | None:
        async with self._lock:
            request = self._current
            if (
                request is None
                or request.session_id != session_id
                or (turn_id is not None and request.turn_id != turn_id)
            ):
                return None
            return request.to_public_dict()

    async def decide(
        self,
        *,
        session_id: str,
        request_id: str,
        decision: str,
    ) -> dict[str, Any]:
        try:
            selected = CommandDecision(decision)
        except ValueError as exc:
            raise CommandBridgeError("command_decision_invalid", "Command decision is invalid") from exc
        async with self._lock:
            request = self._current
            if request is None or request.request_id != request_id or request.session_id != session_id:
                resolved = self._resolved.get(request_id)
                if resolved is not None and resolved.session_id == session_id:
                    return resolved.to_public_dict()
                raise CommandBridgeError("command_request_not_found", "Command request is unavailable")
            if request.state is not CommandRequestState.AWAITING_CONFIRMATION:
                return request.to_public_dict()
            assert request._decision is not None
            if not request._decision.done():
                request._decision.set_result(selected)
            return request.to_public_dict()

    async def cancel_pending(self, *, reason: str = "turn_cancelled") -> bool:
        async with self._lock:
            request = self._current
            if request is None:
                return False
            if request.state is CommandRequestState.AWAITING_CONFIRMATION:
                assert request._decision is not None
                if not request._decision.done():
                    request._decision.set_result(CommandDecision.REJECT)
                request.state = CommandRequestState.CANCELLED
                request.result = CommandExecutionResult(reason, None, "", 0.0)
                request.resolved_at = self._clock()
                self._archive_current(request)
            elif request.state is CommandRequestState.RUNNING:
                request.state = CommandRequestState.CANCELLED
                request.result = CommandExecutionResult(reason, None, "", 0.0)
                request.resolved_at = self._clock()
                if request._execution is not None and not request._execution.done():
                    request._execution.cancel()
            else:
                return False
        await self._notify(request)
        return True

    async def _resolve_without_execution(
        self,
        request: CommandRequest,
        state: CommandRequestState,
        *,
        status: str,
    ) -> None:
        async with self._lock:
            if request.state is CommandRequestState.CANCELLED:
                return
            request.state = state
            request.result = CommandExecutionResult(status, None, "", 0.0)
            request.resolved_at = self._clock()
            self._archive_current(request)
        await self._notify(request)

    def _archive_current(self, request: CommandRequest) -> None:
        self._resolved[request.request_id] = request
        if self._current is request:
            self._current = None

    async def _notify(self, request: CommandRequest) -> None:
        if self._observer is not None:
            await self._observer(request)

    @staticmethod
    def _tool_result(request: CommandRequest) -> dict[str, Any]:
        result = request.result or CommandExecutionResult(
            status="turn_cancelled",
            exit_code=None,
            output="",
            duration_seconds=0.0,
        )
        return {
            "request_id": request.request_id,
            "state": request.state.value,
            "result": result.to_dict(),
        }


def encode_tool_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
