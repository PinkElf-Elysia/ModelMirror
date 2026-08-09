"""Offline initialization and tool-discovery smoke for fixed token runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .token_contracts import TOKEN_ADAPTERS, TOKEN_SCHEMA_SHA256


VALID_SETTINGS = {
    "organization_id": "smoke-org",
    "environment_id": "smoke-env",
    "stack_slug": "smoke-stack",
    "assistant_host": "smoke.svc.pinecone.io",
    "assistant_name": "smoke-assistant",
}


async def discover(adapter_id: str) -> tuple[str, set[str], str]:
    contract = TOKEN_ADAPTERS[adapter_id]
    env = {
        "PATH": "/opt/modelmirror/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": "/opt/modelmirror",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "DO_NOT_TRACK": "1",
        "FRAMELINK_TELEMETRY": "off",
        "DISABLE_TELEMETRY": "true",
        "NO_PROXY": "*",
        "no_proxy": "*",
        "MCP_ALLOWED_HOSTS": ",".join(sorted(contract.allowed_hosts)),
        "NODE_OPTIONS": "--require=/opt/modelmirror/sandbox_sidecar/network_guard.cjs",
    }
    for _, environment_name in contract.credential_environment:
        env[environment_name] = "modelmirror-offline-smoke-token"
    for key, environment_name in contract.setting_environment:
        env[environment_name] = VALID_SETTINGS[key]
    params = StdioServerParameters(
        command=contract.command[0],
        args=list(contract.command[1:]),
        env=env,
        cwd=Path("/tmp"),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            response = await session.list_tools()
    discovered = {tool.name for tool in response.tools}
    missing = contract.tools - discovered
    if missing:
        raise RuntimeError(
            f"{adapter_id} missing reviewed tools: {sorted(missing)}; "
            f"discovered={sorted(discovered)}"
        )
    reviewed_schemas = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(response.tools, key=lambda item: item.name)
        if tool.name in contract.tools
    ]
    digest = hashlib.sha256(
        json.dumps(
            reviewed_schemas,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if digest != TOKEN_SCHEMA_SHA256.get(adapter_id):
        raise RuntimeError(
            f"{adapter_id} reviewed tool schema drifted: {digest}"
        )
    return adapter_id, discovered, digest


async def main() -> None:
    requested = {
        item.strip()
        for item in os.getenv("MCP_SMOKE_ADAPTERS", "").split(",")
        if item.strip()
    }
    adapter_ids = requested or set(TOKEN_ADAPTERS)
    if not adapter_ids.issubset(TOKEN_ADAPTERS):
        raise RuntimeError("unknown adapter requested for runtime smoke")
    for adapter_id in sorted(adapter_ids):
        name, discovered, digest = await asyncio.wait_for(discover(adapter_id), timeout=20)
        allowed = discovered & TOKEN_ADAPTERS[name].tools
        print(
            f"{name}: initialized, reviewed_tools={len(allowed)}, "
            f"upstream_tools={len(discovered)}, schema_sha256={digest}"
        )


if __name__ == "__main__":
    asyncio.run(main())
