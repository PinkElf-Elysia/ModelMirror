from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from .contracts import SAFE_ID
from .opencode_provider import OpenCodeProvider, OpenCodeRoute
from .provider_rpc import ProviderRPCServer
from .executor import SidecarExecutor


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
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
    route_id = os.getenv("CODING_WORKER_ROUTE_ID", "coding/default").strip()
    route = OpenCodeRoute(
        route_id=route_id,
        model_id=_required_environment("CODING_WORKER_MODEL_ID"),
        base_url=_required_environment("CODING_WORKER_MODEL_BASE_URL"),
        api_key=_required_environment("CODING_WORKER_ROUTE_KEY"),
    )
    provider = OpenCodeProvider(
        workspace_resolver=_workspace_resolver(workspace_root),
        runtime_root=runtime_root,
        routes={route_id: route},
        tool_broker_command=("python", "-m", "coding_worker.broker_mcp"),
    )
    executor = SidecarExecutor(
        _workspace_resolver(workspace_root), runtime_root=runtime_root
    )
    server = ProviderRPCServer(
        provider,
        token=_required_environment("CODING_WORKER_SIDECAR_TOKEN"),
        bind_broker=provider.bind_broker,
        unbind_broker=provider.unbind_broker,
        executor=executor,
    )
    await server.start_unix(socket_path)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, stop.set)
    try:
        await stop.wait()
    finally:
        await server.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
