from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import secrets
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import Field

from .broker_rpc import BrokerRPCServer
from .contracts import PolicyProfile, StrictModel
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
    PROVIDER_TOOL_NAMES,
    ProviderUsage,
)


OPENCODE_VERSION = "1.18.9"
TOOL_BROKER_MCP_NAME = "modelmirror-tool-broker"
DIRECT_TOOL_NAMES = frozenset(
    {
        "bash",
        "edit",
        "write",
        "patch",
        "webfetch",
        "websearch",
        "skill",
        "task",
        "question",
        "todowrite",
    }
)


class OpenCodeProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class OpenCodeRoute(StrictModel):
    route_id: str
    model_id: str
    base_url: str
    api_key: str = Field(min_length=1, repr=False, exclude=True)
    context_tokens: int = Field(default=128_000, ge=8_192, le=2_000_000)
    output_tokens: int = Field(default=32_000, ge=1_024, le=262_144)


@dataclass(slots=True)
class OpenCodeServerHandle:
    task_id: str
    workspace: Path
    state_root: Path
    client: httpx.AsyncClient
    close_callback: Callable[[], Awaitable[None]]

    async def close(self) -> None:
        await self.close_callback()


ServerFactory = Callable[
    [ProviderOpenRequest, Path, OpenCodeRoute], Awaitable[OpenCodeServerHandle]
]


class OpenCodeProvider(CodingAgentProvider):
    """OpenCode 1.18.9 headless adapter; raw HTTP details remain private."""

    def __init__(
        self,
        *,
        workspace_resolver: Callable[[str], Path],
        runtime_root: Path,
        routes: Mapping[str, OpenCodeRoute],
        executable: str = "/usr/local/bin/opencode",
        tool_broker_command: tuple[str, ...] | None = None,
        broker_rpc: BrokerRPCServer | None = None,
        server_factory: ServerFactory | None = None,
    ) -> None:
        self._workspace_resolver = workspace_resolver
        self._runtime_root = Path(runtime_root)
        self._routes = dict(routes)
        self._executable = executable
        self._broker_rpc = broker_rpc
        self._tool_broker_command = tool_broker_command or (
            ("python", "-m", "coding_worker.broker_mcp")
            if broker_rpc is not None
            else None
        )
        self._server_factory = server_factory or self._launch_server
        self._handles: dict[str, OpenCodeServerHandle] = {}
        self._requests: dict[str, ProviderOpenRequest] = {}
        self._public_context: dict[str, list[str]] = {}
        self._broker_bindings: dict[str, tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    def bind_broker(self, task_id: str, endpoint: str, token: str) -> None:
        if not (
            endpoint.startswith("unix:")
            or endpoint.startswith("tcp:127.0.0.1:")
        ) or len(token) < 32:
            raise OpenCodeProviderError(
                "Tool Broker binding is invalid.", code="tool_broker_unavailable"
            )
        existing = self._broker_bindings.get(task_id)
        if existing is not None and existing != (endpoint, token):
            raise OpenCodeProviderError(
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
        )

    async def open(self, request: ProviderOpenRequest) -> ProviderSession:
        route = self._require_route(request.model_route)
        workspace = self._workspace_resolver(request.workspace_id).resolve()
        if not workspace.is_dir() or workspace.is_symlink():
            raise OpenCodeProviderError("Workspace is unavailable.", code="workspace_unavailable")
        handle = await self._server_factory(request, workspace, route)
        try:
            await self._wait_for_tool_broker(handle)
            response = await handle.client.post(
                self._url("/session", workspace),
                json={
                    "title": f"ModelMirror {request.task_id}",
                    "agent": "modelmirror-worker",
                    "model": {
                        "providerID": "modelmirror",
                        "id": route.model_id,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
            session_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(session_id, str) or not session_id.startswith("ses"):
                raise OpenCodeProviderError(
                    "OpenCode returned an invalid session.", code="provider_invalid_response"
                )
        except Exception:
            await handle.close()
            raise
        async with self._lock:
            if session_id in self._handles:
                await handle.close()
                raise OpenCodeProviderError(
                    "OpenCode session collision.", code="provider_invalid_response"
                )
            self._handles[session_id] = handle
            self._requests[session_id] = request
            self._public_context[session_id] = []
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
        handle, request = self._require_session(session)
        route = self._require_route(request.model_route)
        event_response: httpx.Response | None = None
        try:
            event_request = handle.client.build_request(
                "GET", self._url("/event", handle.workspace)
            )
            event_response = await handle.client.send(event_request, stream=True)
            event_response.raise_for_status()
            prompt = await handle.client.post(
                self._url(f"/session/{quote(session.session_id)}/prompt_async", handle.workspace),
                json={
                    "model": {
                        "providerID": "modelmirror",
                        "modelID": route.model_id,
                    },
                    "agent": "modelmirror-worker",
                    "tools": self._prompt_tools(request.tool_allowlist),
                    "parts": [{"type": "text", "text": text}],
                },
            )
            prompt.raise_for_status()
            async for payload in _iter_sse_json(event_response):
                mapped = self._map_event(payload, session.session_id)
                if mapped is None:
                    continue
                if mapped.kind is ProviderEventKind.MESSAGE:
                    text_part = mapped.data.get("text")
                    if isinstance(text_part, str) and text_part:
                        public = self._public_context.setdefault(session.session_id, [])
                        public.append(text_part[:16_384])
                        if len(public) > 64:
                            del public[:-64]
                yield mapped
                if mapped.kind in {
                    ProviderEventKind.TURN_COMPLETED,
                    ProviderEventKind.CANCELLED,
                    ProviderEventKind.FAILED,
                }:
                    return
            raise OpenCodeProviderError(
                "OpenCode event stream ended before a terminal event.",
                code="provider_stream_ended",
            )
        except httpx.HTTPError as exc:
            raise OpenCodeProviderError(
                "OpenCode request failed.", code="provider_unavailable"
            ) from exc
        finally:
            if event_response is not None:
                await event_response.aclose()

    async def cancel(self, session: ProviderSession) -> bool:
        handle, _request = self._require_session(session)
        try:
            response = await handle.client.post(
                self._url(f"/session/{quote(session.session_id)}/abort", handle.workspace)
            )
            response.raise_for_status()
            value = response.json()
        except httpx.HTTPError as exc:
            raise OpenCodeProviderError(
                "OpenCode cancel failed.", code="provider_unavailable"
            ) from exc
        return value is True

    async def checkpoint(self, session: ProviderSession) -> ProviderCheckpoint:
        _handle, request = self._require_session(session)
        public_text = "".join(self._public_context.get(session.session_id, ()))
        return ProviderCheckpoint(
            checkpoint_id=f"checkpoint_{secrets.token_hex(16)}",
            compatibility=ProviderCheckpointCompatibility(
                provider_family="opencode",
                provider_version=OPENCODE_VERSION,
                task_id=request.task_id,
                workspace_tree_hash=request.workspace_tree_hash,
            ),
            payload={
                "engine": "opencode-1.18.9",
                "task_id": request.task_id,
                "public_output": public_text[-32_768:],
            },
        )

    async def restore(
        self, request: ProviderOpenRequest, checkpoint: ProviderCheckpoint
    ) -> ProviderSession:
        compatibility = checkpoint.compatibility
        if (
            checkpoint.payload.get("engine") != "opencode-1.18.9"
            or checkpoint.payload.get("task_id") != request.task_id
            or not isinstance(checkpoint.payload.get("public_output", ""), str)
            or (
                compatibility is not None
                and (
                    compatibility.provider_family != "opencode"
                    or compatibility.provider_version != OPENCODE_VERSION
                    or compatibility.task_id != request.task_id
                    or compatibility.workspace_tree_hash
                    != request.workspace_tree_hash
                )
            )
        ):
            raise OpenCodeProviderError(
                "OpenCode checkpoint binding is invalid.", code="checkpoint_invalid"
            )
        # A provider process is never resurrected. Restore opens a fresh, pinned
        # engine session; the control plane supplies the public checkpoint
        # summary in the next message. Raw vendor frames and hidden reasoning
        # are intentionally absent from the durable checkpoint.
        return await self.open(request)

    async def close(self, session: ProviderSession) -> None:
        async with self._lock:
            handle = self._handles.pop(session.session_id, None)
            self._requests.pop(session.session_id, None)
            self._public_context.pop(session.session_id, None)
        if handle is not None:
            await handle.close()

    def build_config(
        self,
        route: OpenCodeRoute,
        tool_allowlist: tuple[str, ...] = PROVIDER_TOOL_NAMES,
    ) -> dict[str, Any]:
        permission: dict[str, str] = {
            "*": "deny",
            "external_directory": "deny",
            "doom_loop": "deny",
            "question": "deny",
        }
        mcp: dict[str, Any] = {}
        if self._tool_broker_command is not None:
            for tool_name in tool_allowlist:
                permission[f"{TOOL_BROKER_MCP_NAME}_{tool_name}"] = "allow"
            mcp[TOOL_BROKER_MCP_NAME] = {
                "type": "local",
                "command": list(self._tool_broker_command),
                "environment": {
                    "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                },
                "enabled": True,
                "timeout": 310_000,
            }
        return {
            "$schema": "https://opencode.ai/config.json",
            "model": f"modelmirror/{route.model_id}",
            "default_agent": "modelmirror-worker",
            "agent": {
                "modelmirror-worker": {
                    "description": "ModelMirror provider-neutral coding worker",
                    "mode": "primary",
                    "permission": permission,
                }
            },
            "permission": permission,
            "provider": {
                "modelmirror": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "ModelMirror Internal Gateway",
                    "options": {
                        "baseURL": route.base_url,
                        "apiKey": "{env:CODING_WORKER_ROUTE_KEY}",
                    },
                    "models": {
                        route.model_id: {
                            "name": "ModelMirror controlled model route",
                            "limit": {
                                "context": route.context_tokens,
                                "output": route.output_tokens,
                            },
                        }
                    },
                }
            },
            "plugin": [],
            "mcp": mcp,
            "instructions": [],
            "share": "disabled",
            "autoupdate": False,
        }

    async def _launch_server(
        self, request: ProviderOpenRequest, workspace: Path, route: OpenCodeRoute
    ) -> OpenCodeServerHandle:
        state_root = self._runtime_root / request.task_id
        state_root.mkdir(parents=True, exist_ok=True)
        home = state_root / "home"
        home.mkdir(exist_ok=True)
        password = secrets.token_urlsafe(32)
        port = _unused_loopback_port()
        broker_environment, revoke_broker = self._broker_environment(request.task_id)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                self.build_config(route, request.tool_allowlist),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_PURE": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_AUTOCOMPACT": "1",
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_AUTH_CONTENT": "{}",
            "OPENCODE_SERVER_PASSWORD": password,
            "CODING_WORKER_ROUTE_KEY": route.api_key,
            "NO_PROXY": "127.0.0.1,localhost,new-api",
            "no_proxy": "127.0.0.1,localhost,new-api",
            **broker_environment,
        }
        try:
            process = await asyncio.create_subprocess_exec(
                self._executable,
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(port),
                cwd=workspace,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            revoke_broker()
            raise
        auth = base64.b64encode(f"opencode:{password}".encode()).decode("ascii")
        client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Basic {auth}"},
            timeout=httpx.Timeout(120.0, connect=5.0),
            trust_env=False,
        )
        try:
            await _wait_for_server(client, process)
            health = await client.get("/global/health")
            health.raise_for_status()
            payload = health.json()
            if payload != {"healthy": True, "version": OPENCODE_VERSION}:
                raise OpenCodeProviderError(
                    "OpenCode version is not the pinned version.", code="provider_version_mismatch"
                )
        except Exception:
            await client.aclose()
            await _stop_process(process)
            revoke_broker()
            raise

        async def close() -> None:
            await client.aclose()
            await _stop_process(process)
            revoke_broker()

        return OpenCodeServerHandle(
            task_id=request.task_id,
            workspace=workspace,
            state_root=state_root,
            client=client,
            close_callback=close,
        )

    def _broker_environment(self, task_id: str) -> tuple[dict[str, str], Callable[[], None]]:
        if self._broker_rpc is not None:
            endpoint = self._broker_rpc.endpoint
            if endpoint is None:
                raise OpenCodeProviderError(
                    "Tool Broker RPC is unavailable.", code="tool_broker_unavailable"
                )
            token = self._broker_rpc.register_task(task_id)
        else:
            binding = self._broker_bindings.get(task_id)
            if binding is None:
                return {}, lambda: None
            endpoint, token = binding
        revoked = False

        def revoke() -> None:
            nonlocal revoked
            if not revoked:
                if self._broker_rpc is not None:
                    self._broker_rpc.revoke_task(task_id)
                else:
                    self.unbind_broker(task_id)
                revoked = True

        return {
            "CODING_WORKER_BROKER_ENDPOINT": endpoint,
            "CODING_WORKER_BROKER_TOKEN": token,
            "CODING_WORKER_TASK_ID": task_id,
        }, revoke

    def _require_route(self, route_id: str) -> OpenCodeRoute:
        route = self._routes.get(route_id)
        if route is None or route.route_id != route_id:
            raise OpenCodeProviderError("Model route is unavailable.", code="model_route_unavailable")
        return route

    def _require_session(
        self, session: ProviderSession
    ) -> tuple[OpenCodeServerHandle, ProviderOpenRequest]:
        handle = self._handles.get(session.session_id)
        request = self._requests.get(session.session_id)
        if handle is None or request is None or request.task_id != session.task_id:
            raise OpenCodeProviderError("Provider session was not found.", code="session_not_found")
        return handle, request

    def _prompt_tools(
        self, tool_allowlist: tuple[str, ...] = PROVIDER_TOOL_NAMES
    ) -> dict[str, bool]:
        tools = {name: False for name in DIRECT_TOOL_NAMES}
        if self._tool_broker_command is not None:
            tools.update(
                {
                    f"{TOOL_BROKER_MCP_NAME}_{tool_name}": True
                    for tool_name in tool_allowlist
                }
            )
        return tools

    async def _wait_for_tool_broker(self, handle: OpenCodeServerHandle) -> None:
        if self._tool_broker_command is None:
            return
        deadline = asyncio.get_running_loop().time() + 15.0
        while asyncio.get_running_loop().time() < deadline:
            try:
                status_response = await handle.client.get(
                    self._url("/mcp", handle.workspace), timeout=2.0
                )
                status_response.raise_for_status()
                statuses = status_response.json()
                broker_status = (
                    statuses.get(TOOL_BROKER_MCP_NAME)
                    if isinstance(statuses, dict)
                    else None
                )
                if (
                    isinstance(broker_status, dict)
                    and broker_status.get("status") == "connected"
                ):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(0.1)
        raise OpenCodeProviderError(
            "Tool Broker MCP failed to become ready.",
            code="tool_broker_unavailable",
        )

    @staticmethod
    def _url(path: str, workspace: Path) -> str:
        return f"{path}?directory={quote(str(workspace), safe='')}"

    @staticmethod
    def _map_event(payload: dict[str, Any], session_id: str) -> ProviderEvent | None:
        event_type = payload.get("type")
        properties = payload.get("properties")
        if not isinstance(event_type, str) or not isinstance(properties, dict):
            return None
        event_session = properties.get("sessionID") or properties.get("sessionId")
        if event_session is not None and event_session != session_id:
            return None
        if event_type in {"message.part.updated", "message.part.delta"}:
            part = properties.get("part")
            delta = properties.get("delta")
            text = None
            if isinstance(part, dict) and part.get("type") == "text":
                text = delta if isinstance(delta, str) else part.get("text")
            if isinstance(text, str) and text:
                return ProviderEvent(kind=ProviderEventKind.MESSAGE, data={"text": text})
            if isinstance(part, dict) and part.get("type") == "tool":
                operation_id = part.get("callID") or part.get("callId")
                tool_name = part.get("tool")
                state = part.get("state")
                if (
                    isinstance(operation_id, str)
                    and isinstance(tool_name, str)
                    and tool_name in PROVIDER_TOOL_NAMES
                    and isinstance(state, dict)
                ):
                    status = state.get("status")
                    if status in {"pending", "running"}:
                        return ProviderEvent(
                            kind=ProviderEventKind.TOOL_STARTED,
                            data={
                                "operation_id": operation_id,
                                "tool_name": tool_name,
                                "summary": "Tool execution started.",
                            },
                        )
                    if status in {"completed", "error"}:
                        return ProviderEvent(
                            kind=ProviderEventKind.TOOL_COMPLETED,
                            data={
                                "operation_id": operation_id,
                                "tool_name": tool_name,
                                "summary": "Tool execution completed.",
                                "success": status == "completed",
                                "artifact_id": None,
                            },
                        )
        if event_type == "message.updated":
            info = properties.get("info")
            tokens = info.get("tokens") if isinstance(info, dict) else None
            if isinstance(tokens, dict):
                cache = tokens.get("cache")
                cost = info.get("cost")
                usage = ProviderUsage(
                    input_tokens=_nonnegative_int(tokens.get("input")),
                    output_tokens=_nonnegative_int(tokens.get("output")),
                    cache_read_tokens=_nonnegative_int(
                        cache.get("read") if isinstance(cache, dict) else None
                    ),
                    cache_write_tokens=_nonnegative_int(
                        cache.get("write") if isinstance(cache, dict) else None
                    ),
                    cost_microusd=(
                        round(float(cost) * 1_000_000)
                        if isinstance(cost, (int, float)) and cost >= 0
                        else None
                    ),
                )
                return ProviderEvent(
                    kind=ProviderEventKind.USAGE,
                    data={"usage": usage.model_dump(mode="json")},
                )
        if event_type in {"permission.asked", "permission.updated"}:
            return ProviderEvent(
                kind=ProviderEventKind.APPROVAL_REQUIRED,
                data={"capability": "provider_permission"},
            )
        if event_type in {"session.idle", "session.completed"}:
            return ProviderEvent(kind=ProviderEventKind.TURN_COMPLETED)
        if event_type in {"session.aborted", "session.cancelled"}:
            return ProviderEvent(kind=ProviderEventKind.CANCELLED)
        if event_type in {"session.error", "message.error"}:
            return ProviderEvent(
                kind=ProviderEventKind.FAILED,
                data={"failure_kind": ProviderFailureKind.UNAVAILABLE.value},
            )
        return None


def _nonnegative_int(value: Any) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


async def _iter_sse_json(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                try:
                    value = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    value = None
                if isinstance(value, dict):
                    yield value
                data_lines.clear()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            value = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return
        if isinstance(value, dict):
            yield value


async def _wait_for_server(
    client: httpx.AsyncClient,
    process: asyncio.subprocess.Process,
    *,
    timeout: float = 10.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if process.returncode is not None:
            raise OpenCodeProviderError("OpenCode exited during startup.", code="provider_unavailable")
        try:
            response = await client.get("/global/health", timeout=0.25)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.05)
    raise OpenCodeProviderError("OpenCode startup timed out.", code="provider_unavailable")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])
