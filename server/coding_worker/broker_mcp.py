from __future__ import annotations

import os
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from .broker_rpc import BrokerRPCClient


def build_server(client: BrokerRPCClient) -> FastMCP:
    mcp = FastMCP("ModelMirror Coding Worker Tools")

    async def call(
        tool_name: str,
        arguments: dict[str, Any],
        *,
        operation_id: str | None = None,
        lease_id: str | None = None,
        network_lease_id: str | None = None,
    ) -> dict[str, Any]:
        return await client.call(
            operation_id=operation_id or f"inspect_{uuid.uuid4().hex}",
            tool_name=tool_name,
            arguments=arguments,
            lease_id=lease_id,
            network_lease_id=network_lease_id,
        )

    @mcp.tool()
    async def list_files() -> dict[str, Any]:
        """List task workspace entries using opaque, workspace-relative identities."""
        return await call("list_files", {})

    @mcp.tool()
    async def read_file(path: str) -> dict[str, Any]:
        """Read one workspace-relative text file or return binary metadata."""
        return await call("read_file", {"path": path})

    @mcp.tool()
    async def search_text(query: str) -> dict[str, Any]:
        """Search text inside the current task workspace."""
        return await call("search_text", {"query": query})

    @mcp.tool()
    async def workspace_diff() -> dict[str, Any]:
        """Return the current synthetic-H0 Git diff."""
        return await call("diff", {})

    @mcp.tool()
    async def write_file(
        operation_id: str, path: str, content: str, content_sha256: str
    ) -> dict[str, Any]:
        """Atomically write UTF-8 text using a stable operation id and content digest."""
        return await call(
            "write_file",
            {"path": path, "content": content, "content_sha256": content_sha256},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def delete_file(
        operation_id: str, path: str, expected_sha256: str
    ) -> dict[str, Any]:
        """Delete an unchanged workspace file using a stable operation id."""
        return await call(
            "delete_file",
            {"path": path, "expected_sha256": expected_sha256},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def run_check(check_id: str) -> dict[str, Any]:
        """Run one immutable server-defined acceptance check."""
        return await call("run_check", {"check_id": check_id})

    @mcp.tool()
    async def run_command(
        operation_id: str,
        argv: list[str],
        lease_id: str,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Run an argv-only command after an exact command lease is approved."""
        return await call(
            "run_command",
            {"argv": argv, "timeout_seconds": timeout_seconds},
            operation_id=operation_id,
            lease_id=lease_id,
        )

    @mcp.tool()
    async def install_dependencies(
        operation_id: str, lease_id: str, network_lease_id: str
    ) -> dict[str, Any]:
        """Run frozen npm-ci through the approved egress lease and proxy."""
        return await call(
            "install_dependencies",
            {"manager": "npm", "action": "ci"},
            operation_id=operation_id,
            lease_id=lease_id,
            network_lease_id=network_lease_id,
        )

    @mcp.tool()
    async def start_service(
        operation_id: str,
        argv: list[str],
        lease_id: str,
        ttl_seconds: int = 900,
        preview_port: int | None = None,
    ) -> dict[str, Any]:
        """Start one approved task-owned background service."""
        return await call(
            "start_service",
            {
                "argv": argv,
                "ttl_seconds": ttl_seconds,
                "preview_port": preview_port,
            },
            operation_id=operation_id,
            lease_id=lease_id,
        )

    @mcp.tool()
    async def service_status(service_id: str) -> dict[str, Any]:
        """Read task-owned service state without exposing its process id."""
        return await call("service_status", {"service_id": service_id})

    @mcp.tool()
    async def service_input(
        operation_id: str, service_id: str, data: str
    ) -> dict[str, Any]:
        """Send bounded input to a task-owned service."""
        return await call(
            "service_input",
            {"service_id": service_id, "data": data},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def stop_service(operation_id: str, service_id: str) -> dict[str, Any]:
        """Send Ctrl-C to a task-owned service and archive its output."""
        return await call(
            "stop_service",
            {"service_id": service_id},
            operation_id=operation_id,
        )

    return mcp


def main() -> None:
    endpoint = os.environ.get("CODING_WORKER_BROKER_ENDPOINT", "")
    token = os.environ.get("CODING_WORKER_BROKER_TOKEN", "")
    task_id = os.environ.get("CODING_WORKER_TASK_ID", "")
    if not endpoint or not token or not task_id:
        raise SystemExit("Coding Worker broker binding is unavailable")
    build_server(BrokerRPCClient(endpoint, token=token, task_id=task_id)).run()


if __name__ == "__main__":
    main()
