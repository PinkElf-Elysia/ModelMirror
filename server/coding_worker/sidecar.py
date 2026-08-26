from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

from .contracts import SAFE_ID
from .claude_provider import CLAUDE_CODE_VERSION, ClaudeCodeProvider, ClaudeCodeRoute
from .opencode_provider import OPENCODE_VERSION, OpenCodeProvider, OpenCodeRoute
from .provider_rpc import ProviderRPCServer
from .executor import ExecutorRPCServer, SidecarExecutor


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _bounded_environment_integer(
    name: str, *, default: int, minimum: int, maximum: int
) -> int:
    encoded = os.getenv(name)
    if encoded is None or not encoded.strip():
        return default
    try:
        value = int(encoded)
    except ValueError as exc:
        raise RuntimeError(f"{name} is invalid") from exc
    if isinstance(value, bool) or not minimum <= value <= maximum:
        raise RuntimeError(f"{name} is invalid")
    return value


def _workspace_resolver(root: Path):
    resolved_root = root.resolve()

    def resolve(workspace_id: str) -> Path:
        if SAFE_ID.fullmatch(workspace_id) is None:
            raise ValueError("workspace id is invalid")
        candidate = resolved_root / "workspaces" / workspace_id / "repo"
        resolved = candidate.resolve(strict=True)
        if (
            not resolved.is_relative_to(resolved_root)
            or not resolved.is_dir()
            or candidate.is_symlink()
        ):
            raise ValueError("workspace is unavailable")
        return resolved

    return resolve


async def run() -> None:
    socket_path = Path(
        os.getenv(
            "CODING_WORKER_PROVIDER_SOCKET", "/run/modelmirror-coding/provider.sock"
        )
    )
    workspace_root = Path(
        os.getenv("CODING_WORKER_SLOT_ROOT", "/worker-data")
    ).resolve()
    runtime_root = Path(
        os.getenv("CODING_WORKER_RUNTIME_ROOT", "/worker-runtime")
    ).resolve()
    if os.getenv("CODING_WORKER_MODE", "provider").strip() == "executor":
        executor = SidecarExecutor(
            _workspace_resolver(workspace_root), runtime_root=runtime_root
        )
        executor_server = ExecutorRPCServer(
            executor, token=_required_environment("CODING_WORKER_EXECUTOR_TOKEN")
        )
        await executor_server.start_unix(socket_path)
        await _wait_for_stop(executor_server.close)
        return
    provider = _provider_from_environment(workspace_root, runtime_root)
    server = ProviderRPCServer(
        provider,
        token=_required_environment("CODING_WORKER_SIDECAR_TOKEN"),
        bind_broker=provider.bind_broker,
        unbind_broker=provider.unbind_broker,
        harness_identity=(
            _provider_harness_identity(provider)
            if os.getenv("CODING_WORKER_HARNESS_V3_ENABLED", "").lower() == "true"
            else None
        ),
        harness_descriptor=provider.harness_descriptor(),
    )
    await server.start_unix(socket_path)
    await _wait_for_stop(server.close)


def _provider_from_environment(
    workspace_root: Path, runtime_root: Path
) -> OpenCodeProvider | ClaudeCodeProvider:
    provider_kind = os.getenv("CODING_WORKER_PROVIDER_KIND", "opencode").strip()
    route_id = os.getenv("CODING_WORKER_ROUTE_ID", "coding/default").strip()
    model_id = _required_environment("CODING_WORKER_MODEL_ID")
    command = ("python", "-m", "coding_worker.broker_mcp")
    if provider_kind == "claude-code":
        route = ClaudeCodeRoute(
            route_id=route_id,
            model_id=model_id,
            gateway_base_url=(
                os.getenv("CODING_WORKER_CLAUDE_GATEWAY_BASE_URL") or None
            ),
        )
        provider = ClaudeCodeProvider(
            runtime_root=runtime_root,
            routes={route_id: route},
            secret_path=Path(_required_environment("CODING_WORKER_CLAUDE_SECRET_PATH")),
            tool_broker_command=command,
            provider_proxy_url=os.getenv("CODING_WORKER_PROVIDER_PROXY_URL") or None,
        )
        provider.validate_environment()
        return provider
    if provider_kind != "opencode":
        raise RuntimeError("CODING_WORKER_PROVIDER_KIND is invalid")
    route = OpenCodeRoute(
        route_id=route_id,
        model_id=model_id,
        base_url=_required_environment("CODING_WORKER_MODEL_BASE_URL"),
        api_key=_required_environment("CODING_WORKER_ROUTE_KEY"),
        output_tokens=_bounded_environment_integer(
            "CODING_WORKER_MODEL_OUTPUT_TOKENS",
            default=8_192,
            minimum=1_024,
            maximum=262_144,
        ),
    )
    return OpenCodeProvider(
        workspace_resolver=_workspace_resolver(workspace_root),
        runtime_root=runtime_root,
        routes={route_id: route},
        tool_broker_command=command,
        provider_proxy_url=os.getenv("CODING_WORKER_PROVIDER_PROXY_URL") or None,
    )


def _provider_harness_identity(
    provider: OpenCodeProvider | ClaudeCodeProvider,
) -> tuple[str, str, str]:
    route_id = _required_environment("CODING_WORKER_ROUTE_ID")
    model_id = _required_environment("CODING_WORKER_MODEL_ID")
    if isinstance(provider, ClaudeCodeProvider):
        command = (*provider._command_prefix, "--version")
        expected_version = CLAUDE_CODE_VERSION
        engine_name = "claude-code"
    else:
        command = (provider._executable, "--version")
        expected_version = OPENCODE_VERSION
        engine_name = "opencode"
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Provider Harness CLI identity is unavailable") from exc
    observed_version = completed.stdout.strip().split(maxsplit=1)[0]
    if completed.returncode != 0 or observed_version != expected_version:
        raise RuntimeError("Provider Harness CLI version does not match its runtime")
    engine = f"{engine_name}-{observed_version}"
    return route_id, model_id, engine


async def _wait_for_stop(close: Callable[[], Awaitable[None]]) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, stop.set)
    try:
        await stop.wait()
    finally:
        await close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
