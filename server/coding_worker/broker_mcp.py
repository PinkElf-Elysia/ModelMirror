from __future__ import annotations

import hashlib
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
    async def read_file_range(
        path: str, start_line: int = 1, end_line: int = 200
    ) -> dict[str, Any]:
        """Read a bounded line range from one workspace-relative UTF-8 file."""
        return await call(
            "read_file_range",
            {"path": path, "start_line": start_line, "end_line": end_line},
        )

    @mcp.tool()
    async def glob_files(pattern: str) -> dict[str, Any]:
        """List workspace entries matching one bounded workspace-relative glob."""
        return await call("glob_files", {"pattern": pattern})

    @mcp.tool()
    async def search_text(query: str) -> dict[str, Any]:
        """Search text inside the current task workspace."""
        return await call("search_text", {"query": query})

    @mcp.tool()
    async def search_regex(
        pattern: str,
        glob: str = "**/*",
        case_sensitive: bool = True,
    ) -> dict[str, Any]:
        """Run one bounded safe-regex search inside matching workspace files."""
        return await call(
            "search_regex",
            {"pattern": pattern, "glob": glob, "case_sensitive": case_sensitive},
        )

    @mcp.tool()
    async def workspace_diff() -> dict[str, Any]:
        """Return the current synthetic-H0 Git diff."""
        return await call("diff", {})

    @mcp.tool()
    async def write_file(operation_id: str, path: str, content: str) -> dict[str, Any]:
        """Atomically write UTF-8 text using a stable operation id."""
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
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
    async def apply_changeset(
        operation_id: str,
        base_tree_hash: str,
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically publish a preimage-bound write, patch, move, or delete batch."""
        return await call(
            "apply_changeset",
            {"base_tree_hash": base_tree_hash, "changes": changes},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def list_acceptance_checks() -> dict[str, Any]:
        """List the immutable acceptance checks allowed for this task."""
        return await call("list_acceptance_checks", {})

    @mcp.tool()
    async def run_check(check_id: str) -> dict[str, Any]:
        """Run one check returned by list_acceptance_checks."""
        return await call("run_check", {"check_id": check_id})

    @mcp.tool()
    async def run_command(
        operation_id: str,
        argv: list[str],
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Request approval, then run one exact argv-only command."""
        return await call(
            "run_command",
            {"argv": argv, "timeout_seconds": timeout_seconds},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def run_shell(
        operation_id: str,
        script: str,
        cwd: str = ".",
        mode: str = "inspect",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Run one exact approved Bash script in a disposable task clone."""
        return await call(
            "run_shell",
            {
                "script": script,
                "cwd": cwd,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
            },
            operation_id=operation_id,
        )

    @mcp.tool()
    async def install_dependencies(operation_id: str) -> dict[str, Any]:
        """Request approval, then run frozen npm-ci through controlled egress."""
        return await call(
            "install_dependencies",
            {"manager": "npm", "action": "ci"},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def start_service(
        operation_id: str,
        argv: list[str],
        ttl_seconds: int = 900,
        preview_port: int | None = None,
    ) -> dict[str, Any]:
        """Request approval, then start one task-owned background service."""
        return await call(
            "start_service",
            {
                "argv": argv,
                "ttl_seconds": ttl_seconds,
                "preview_port": preview_port,
            },
            operation_id=operation_id,
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
