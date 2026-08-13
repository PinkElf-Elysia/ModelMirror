from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import signal
import stat
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from .contracts import StrictModel
from .provider import (
    CodingAgentProvider,
    ProviderCapabilities,
    ProviderCheckpoint,
    ProviderCheckpointCompatibility,
    ProviderEvent,
    ProviderEventKind,
    ProviderFailureKind,
    ProviderOpenRequest,
    ProviderSession,
    ProviderUsage,
    PROVIDER_TOOL_NAMES,
    provider_message_with_repository_instructions,
)


CLAUDE_CODE_VERSION = "2.1.89"
CLAUDE_MCP_NAME = "modelmirror-tool-broker"
MAX_SECRET_BYTES = 16 * 1024
MAX_STDERR_BYTES = 16 * 1024
CLAUDE_BUILTIN_TOOLS = (
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Skill",
    "Task",
    "NotebookEdit",
)


class ClaudeCodeProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        failure_kind: ProviderFailureKind = ProviderFailureKind.UNAVAILABLE,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.failure_kind = failure_kind


class ClaudeCodeRoute(StrictModel):
    route_id: str
    model_id: str = Field(min_length=1, max_length=128)
    max_budget_usd: float = Field(default=20.0, gt=0, le=1_000)


@dataclass(slots=True)
class ClaudeSessionHandle:
    request: ProviderOpenRequest
    route: ClaudeCodeRoute
    state_root: Path
    home: Path
    settings_path: Path
    mcp_path: Path
    session_id: str
    revoke_broker: Callable[[], None]
    resumed_once: bool = False
    active_process: asyncio.subprocess.Process | None = None
    public_context: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ClaudeCodeProvider(CodingAgentProvider):
    """Claude Code 2.1.89 adapter with no Workspace mount or public frames."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        routes: Mapping[str, ClaudeCodeRoute],
        secret_path: Path,
        command_prefix: tuple[str, ...] = ("/usr/local/bin/claude",),
        tool_broker_command: tuple[str, ...] = (
            "python",
            "-m",
            "coding_worker.broker_mcp",
        ),
        provider_proxy_url: str | None = None,
    ) -> None:
        if not command_prefix or not tool_broker_command:
            raise ValueError("Claude provider commands cannot be empty")
        self._runtime_root = Path(runtime_root)
        self._routes = dict(routes)
        self._secret_path = Path(secret_path)
        self._command_prefix = command_prefix
        self._tool_broker_command = tool_broker_command
        self._provider_proxy_url = provider_proxy_url
        self._broker_bindings: dict[str, tuple[str, str]] = {}
        self._handles: dict[str, ClaudeSessionHandle] = {}
        self._lock = asyncio.Lock()

    def bind_broker(self, task_id: str, endpoint: str, token: str) -> None:
        if not (
            endpoint.startswith("unix:") or endpoint.startswith("tcp:127.0.0.1:")
        ) or len(token) < 32:
            raise ClaudeCodeProviderError(
                "Tool Broker binding is invalid.", code="tool_broker_unavailable"
            )
        existing = self._broker_bindings.get(task_id)
        if existing is not None and existing != (endpoint, token):
            raise ClaudeCodeProviderError(
                "Tool Broker binding changed.", code="tool_broker_binding_changed"
            )
        self._broker_bindings[task_id] = (endpoint, token)

    def unbind_broker(self, task_id: str) -> None:
        self._broker_bindings.pop(task_id, None)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_streaming=True,
            supports_cancel=True,
            supports_checkpoint=True,
            supports_restore=True,
            supports_steering=True,
            supports_usage=True,
        )

    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        route = self._require_route(request.model_route)
        binding = self._broker_bindings.get(request.task_id)
        if binding is None:
            raise ClaudeCodeProviderError(
                "Tool Broker binding is missing.", code="tool_broker_unavailable"
            )
        self._validate_secret_file()
        session_id = str(uuid.uuid4())
        state_root = self._runtime_root / request.task_id / session_id
        state_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        home = state_root / "home"
        home.mkdir(mode=0o700)
        settings_path = state_root / "settings.json"
        mcp_path = state_root / "mcp.json"
        endpoint, token = binding
        self._write_private_json(
            settings_path, self.build_settings(request.tool_allowlist)
        )
        self._write_private_json(
            mcp_path,
            self.build_mcp_config(
                task_id=request.task_id,
                endpoint=endpoint,
                token=token,
            ),
        )
        handle = ClaudeSessionHandle(
            request=request,
            route=route,
            state_root=state_root,
            home=home,
            settings_path=settings_path,
            mcp_path=mcp_path,
            session_id=session_id,
            revoke_broker=lambda: self.unbind_broker(request.task_id),
        )
        async with self._lock:
            if session_id in self._handles:
                raise ClaudeCodeProviderError(
                    "Claude session collision.", code="provider_invalid_response"
                )
            self._handles[session_id] = handle
        return ProviderSession(
            session_id=session_id,
            task_id=request.task_id,
            provider_capabilities=await self.capabilities(),
        )

    async def message(
        self, session: ProviderSession, text: str
    ) -> AsyncIterator[ProviderEvent]:
        if not text.strip():
            raise ValueError("provider message cannot be blank")
        handle = self._require_session(session)
        async with handle.lock:
            if handle.active_process is not None:
                raise ClaudeCodeProviderError(
                    "Claude session is busy.", code="provider_session_busy"
                )
            api_key = self._read_secret()
            command = self.build_command(handle)
            environment = self.build_environment(handle, api_key=api_key)
            process: asyncio.subprocess.Process | None = None
            output_bytes = 0
            saw_terminal = False
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=handle.state_root,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                handle.active_process = process
                if process.stdin is None or process.stdout is None:
                    raise ClaudeCodeProviderError(
                        "Claude stream is unavailable.",
                        code="provider_unavailable",
                    )
                frame = self.build_input_frame(
                    session.session_id,
                    provider_message_with_repository_instructions(
                        handle.request, text
                    ),
                )
                process.stdin.write(
                    json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode(
                        "utf-8"
                    )
                    + b"\n"
                )
                await process.stdin.drain()
                process.stdin.close()
                with contextlib.suppress(Exception):
                    await process.stdin.wait_closed()
                async with asyncio.timeout(handle.request.budget.max_seconds):
                    while line := await process.stdout.readline():
                        output_bytes += len(line)
                        if output_bytes > handle.request.budget.max_output_bytes:
                            await _stop_process(process)
                            yield self._failure_event(ProviderFailureKind.BUDGET)
                            return
                        events = self.map_stream_frame(line, session.session_id)
                        for event in events:
                            if event.kind is ProviderEventKind.MESSAGE:
                                value = event.data.get("text")
                                if isinstance(value, str) and value:
                                    handle.public_context.append(value[:16_384])
                                    if len(handle.public_context) > 64:
                                        del handle.public_context[:-64]
                            yield event
                            if event.kind in {
                                ProviderEventKind.TURN_COMPLETED,
                                ProviderEventKind.CANCELLED,
                                ProviderEventKind.FAILED,
                            }:
                                saw_terminal = True
                    return_code = await process.wait()
                if not saw_terminal:
                    yield self._failure_event(
                        ProviderFailureKind.INTERRUPTED
                        if return_code == 0
                        else ProviderFailureKind.UNAVAILABLE
                    )
            except TimeoutError:
                if process is not None:
                    await _stop_process(process)
                yield self._failure_event(ProviderFailureKind.BUDGET)
            except OSError as exc:
                raise ClaudeCodeProviderError(
                    "Claude process failed to start.", code="provider_unavailable"
                ) from exc
            finally:
                if process is not None and process.returncode is None:
                    await _stop_process(process)
                if process is not None and process.stderr is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(
                            process.stderr.read(MAX_STDERR_BYTES), timeout=0.2
                        )
                handle.active_process = None
                handle.resumed_once = True

    async def cancel(self, session: ProviderSession) -> bool:
        handle = self._require_session(session)
        process = handle.active_process
        if process is None or process.returncode is not None:
            return False
        await _stop_process(process)
        return True

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        handle = self._require_session(session)
        public_text = "".join(handle.public_context)
        return ProviderCheckpoint(
            checkpoint_id=f"checkpoint_{secrets.token_hex(16)}",
            compatibility=ProviderCheckpointCompatibility(
                provider_family="claude-code",
                provider_version=CLAUDE_CODE_VERSION,
                task_id=session.task_id,
                workspace_tree_hash=handle.request.workspace_tree_hash,
            ),
            payload={
                "task_id": session.task_id,
                "public_output": public_text[-32_768:],
            },
        )

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        compatibility = checkpoint.compatibility
        if (
            compatibility is None
            or compatibility.provider_family != "claude-code"
            or compatibility.provider_version != CLAUDE_CODE_VERSION
            or compatibility.task_id != request.task_id
            or compatibility.workspace_tree_hash != request.workspace_tree_hash
            or checkpoint.payload.get("task_id") != request.task_id
            or not isinstance(checkpoint.payload.get("public_output", ""), str)
        ):
            raise ClaudeCodeProviderError(
                "Claude checkpoint binding is invalid.", code="checkpoint_invalid"
            )
        return await self.open(request)

    async def close(self, session: ProviderSession) -> None:
        async with self._lock:
            handle = self._handles.pop(session.session_id, None)
        if handle is None:
            return
        if handle.active_process is not None:
            await _stop_process(handle.active_process)
        handle.revoke_broker()

    def build_settings(self, tool_allowlist: tuple[str, ...]) -> dict[str, Any]:
        allowed = [self._claude_tool_name(name) for name in tool_allowlist]
        return {
            "permissions": {
                "allow": allowed,
                "deny": list(CLAUDE_BUILTIN_TOOLS),
                "defaultMode": "dontAsk",
                "disableBypassPermissionsMode": "disable",
            },
            "disableAllHooks": True,
            "enabledPlugins": {},
            "strictKnownMarketplaces": [],
            "allowManagedPermissionRulesOnly": True,
            "allowManagedHooksOnly": True,
        }

    def build_mcp_config(
        self, *, task_id: str, endpoint: str, token: str
    ) -> dict[str, Any]:
        return {
            "mcpServers": {
                CLAUDE_MCP_NAME: {
                    "type": "stdio",
                    "command": self._tool_broker_command[0],
                    "args": list(self._tool_broker_command[1:]),
                    "env": {
                        "CODING_WORKER_BROKER_ENDPOINT": endpoint,
                        "CODING_WORKER_BROKER_TOKEN": token,
                        "CODING_WORKER_TASK_ID": task_id,
                        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                    },
                }
            }
        }

    def build_command(self, handle: ClaudeSessionHandle) -> tuple[str, ...]:
        allowed = ",".join(
            self._claude_tool_name(name)
            for name in handle.request.tool_allowlist
        )
        command = [
            *self._command_prefix,
            "--print",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--bare",
            "--strict-mcp-config",
            "--mcp-config",
            str(handle.mcp_path),
            "--settings",
            str(handle.settings_path),
            "--tools",
            "",
            "--allowedTools",
            allowed,
            "--disallowedTools",
            ",".join(CLAUDE_BUILTIN_TOOLS),
            "--disable-slash-commands",
            "--no-chrome",
            "--permission-mode",
            "dontAsk",
            "--model",
            handle.route.model_id,
            "--max-budget-usd",
            f"{handle.route.max_budget_usd:.6f}",
        ]
        if handle.resumed_once:
            command.extend(("--resume", handle.session_id))
        else:
            command.extend(("--session-id", handle.session_id))
        return tuple(command)

    def build_environment(
        self, handle: ClaudeSessionHandle, *, api_key: str
    ) -> dict[str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(handle.home),
            "TMPDIR": str(handle.state_root),
            "ANTHROPIC_API_KEY": api_key,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
            "CLAUDE_CODE_DONT_INHERIT_ENV": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_PLUGIN_AUTOLOAD": "1",
            "DISABLE_TELEMETRY": "1",
        }
        if self._provider_proxy_url is not None:
            environment["HTTPS_PROXY"] = self._provider_proxy_url
            environment["HTTP_PROXY"] = self._provider_proxy_url
        return environment

    @staticmethod
    def build_input_frame(session_id: str, text: str) -> dict[str, Any]:
        return {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
            "parent_tool_use_id": None,
            "session_id": session_id,
        }

    @staticmethod
    def map_stream_frame(
        encoded: bytes, session_id: str
    ) -> tuple[ProviderEvent, ...]:
        if len(encoded) > 1_048_576:
            return ()
        try:
            value = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ()
        if not isinstance(value, dict):
            return ()
        frame_session = value.get("session_id")
        if frame_session is not None and frame_session != session_id:
            return ()
        frame_type = value.get("type")
        if frame_type == "assistant":
            message = value.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            text = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ) if isinstance(content, list) else ""
            events: list[ProviderEvent] = []
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_use":
                        continue
                    operation_id = item.get("id")
                    tool_name = item.get("name")
                    if (
                        isinstance(operation_id, str)
                        and isinstance(tool_name, str)
                        and tool_name.startswith(f"mcp__{CLAUDE_MCP_NAME}__")
                    ):
                        normalized_name = tool_name.rsplit("__", 1)[-1]
                        if normalized_name in PROVIDER_TOOL_NAMES:
                            events.append(
                                ProviderEvent(
                                    kind=ProviderEventKind.TOOL_STARTED,
                                    data={
                                        "operation_id": operation_id,
                                        "tool_name": normalized_name,
                                        "summary": "Tool execution started.",
                                    },
                                )
                            )
            if text:
                events.append(
                    ProviderEvent(
                        kind=ProviderEventKind.MESSAGE,
                        data={"text": text[:1_048_576]},
                    )
                )
            usage = _usage_from_mapping(
                message.get("usage") if isinstance(message, dict) else None,
                cost=None,
            )
            if usage is not None:
                events.append(_usage_event(usage))
            return tuple(events)
        if frame_type == "user":
            message = value.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            events: list[ProviderEvent] = []
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_result":
                        continue
                    operation_id = item.get("tool_use_id")
                    if isinstance(operation_id, str):
                        events.append(
                            ProviderEvent(
                                kind=ProviderEventKind.TOOL_COMPLETED,
                                data={
                                    "operation_id": operation_id,
                                    "tool_name": "run_command",
                                    "summary": "Tool execution completed.",
                                    "success": item.get("is_error") is not True,
                                    "artifact_id": None,
                                },
                            )
                        )
            return tuple(events)
        if frame_type == "result":
            usage = _usage_from_mapping(
                value.get("usage"), cost=value.get("total_cost_usd")
            )
            events = [_usage_event(usage)] if usage is not None else []
            if value.get("is_error") is True:
                subtype = value.get("subtype")
                failure = (
                    ProviderFailureKind.BUDGET
                    if subtype in {"error_max_budget_usd", "error_max_turns"}
                    else ProviderFailureKind.UNAVAILABLE
                )
                events.append(ClaudeCodeProvider._failure_event(failure))
            else:
                events.append(ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED))
            return tuple(events)
        return ()

    @staticmethod
    def _failure_event(kind: ProviderFailureKind) -> ProviderEvent:
        return ProviderEvent(
            kind=ProviderEventKind.FAILED,
            data={"failure_kind": kind.value},
        )

    @staticmethod
    def _claude_tool_name(tool_name: str) -> str:
        return f"mcp__{CLAUDE_MCP_NAME}__{tool_name}"

    def _require_route(self, route_id: str) -> ClaudeCodeRoute:
        route = self._routes.get(route_id)
        if route is None or route.route_id != route_id:
            raise ClaudeCodeProviderError(
                "Model route is unavailable.", code="model_route_unavailable"
            )
        return route

    def _require_session(self, session: ProviderSession) -> ClaudeSessionHandle:
        handle = self._handles.get(session.session_id)
        if handle is None or handle.request.task_id != session.task_id:
            raise ClaudeCodeProviderError(
                "Provider session was not found.", code="session_not_found"
            )
        return handle

    def _validate_secret_file(self) -> os.stat_result:
        try:
            metadata = self._secret_path.lstat()
        except OSError as exc:
            raise ClaudeCodeProviderError(
                "Claude credential is unavailable.",
                code="provider_credential_unavailable",
                failure_kind=ProviderFailureKind.AUTHENTICATION,
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or self._secret_path.is_symlink()
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > MAX_SECRET_BYTES
        ):
            raise ClaudeCodeProviderError(
                "Claude credential is unsafe.",
                code="provider_credential_unavailable",
                failure_kind=ProviderFailureKind.AUTHENTICATION,
            )
        return metadata

    def _read_secret(self) -> str:
        expected = self._validate_secret_file()
        descriptor = os.open(self._secret_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino, actual.st_size) != (
                expected.st_dev,
                expected.st_ino,
                expected.st_size,
            ):
                raise ClaudeCodeProviderError(
                    "Claude credential changed.",
                    code="provider_credential_unavailable",
                    failure_kind=ProviderFailureKind.AUTHENTICATION,
                )
            encoded = os.read(descriptor, MAX_SECRET_BYTES + 1)
        finally:
            os.close(descriptor)
        try:
            value = encoded.decode("utf-8").strip()
        except UnicodeError as exc:
            raise ClaudeCodeProviderError(
                "Claude credential is invalid.",
                code="provider_credential_unavailable",
                failure_kind=ProviderFailureKind.AUTHENTICATION,
            ) from exc
        if not value or len(value.encode("utf-8")) > MAX_SECRET_BYTES:
            raise ClaudeCodeProviderError(
                "Claude credential is invalid.",
                code="provider_credential_unavailable",
                failure_kind=ProviderFailureKind.AUTHENTICATION,
            )
        return value

    @staticmethod
    def _write_private_json(path: Path, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _usage_from_mapping(value: Any, *, cost: Any) -> ProviderUsage | None:
    if not isinstance(value, dict):
        return None
    return ProviderUsage(
        input_tokens=_nonnegative_int(value.get("input_tokens")),
        output_tokens=_nonnegative_int(value.get("output_tokens")),
        cache_read_tokens=_nonnegative_int(value.get("cache_read_input_tokens")),
        cache_write_tokens=_nonnegative_int(value.get("cache_creation_input_tokens")),
        cost_microusd=(
            round(float(cost) * 1_000_000)
            if isinstance(cost, (int, float)) and cost >= 0
            else None
        ),
    )


def _usage_event(usage: ProviderUsage) -> ProviderEvent:
    return ProviderEvent(
        kind=ProviderEventKind.USAGE,
        data={"usage": usage.model_dump(mode="json")},
    )


def _nonnegative_int(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
