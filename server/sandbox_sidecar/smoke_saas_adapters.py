"""Offline initialization and tool-schema smoke for Wave 6 SaaS adapters."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .saas_contracts import SAAS_ADAPTERS, SAAS_SCHEMA_SHA256


CONFIGURATIONS: dict[str, dict[str, dict[str, str]]] = {
    "airtable-mcp": {
        "credentials": {"personal_access_token": "offline-airtable-token"},
        "settings": {"base_id": "app12345678901234"},
    },
    "asana-mcp": {
        "credentials": {"personal_access_token": "offline-asana-token"},
        "settings": {"workspace_gid": "1200000000000001", "project_gid": "1200000000000002"},
    },
    "gitlab-mcp": {
        "credentials": {"personal_access_token": "offline-gitlab-token"},
        "settings": {"project_id": "123456"},
    },
    "notion-mcp-server": {
        "credentials": {"integration_token": "offline-notion-token"},
        "settings": {"data_source_id": "0123456789abcdef0123456789abcdef"},
    },
}


async def discover(adapter_id: str) -> tuple[set[str], str]:
    encoded = base64.urlsafe_b64encode(
        json.dumps(CONFIGURATIONS[adapter_id], separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    with tempfile.TemporaryDirectory(prefix=f"mcp-saas-smoke-{adapter_id[:12]}-") as root:
        workspace = Path(root)
        work_dir = workspace / "work"
        work_dir.mkdir(mode=0o700)
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", "/opt/modelmirror"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "HOME": str(work_dir),
            "TMPDIR": str(work_dir),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "MCP_SAAS_OFFLINE_SMOKE": "1",
            "MCP_SAAS_HANDSHAKE_B64": encoded,
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                str(Path(__file__).with_name("landlock_exec.py")),
                str(workspace),
                "--",
                sys.executable,
                "-m",
                "sandbox_sidecar.saas_mcp",
                adapter_id,
            ],
            env=env,
            cwd=work_dir,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()
    names = {tool.name for tool in response.tools}
    reviewed = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(response.tools, key=lambda item: item.name)
    ]
    encoded_schema = json.dumps(
        reviewed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if b"__modelmirror_" in encoded_schema:
        raise RuntimeError(f"{adapter_id} leaked private execution fields in tools/list")
    return names, hashlib.sha256(encoded_schema).hexdigest()


async def main() -> None:
    discovered_digests: dict[str, str] = {}
    for adapter_id, contract in SAAS_ADAPTERS.items():
        names, digest = await asyncio.wait_for(discover(adapter_id), timeout=20)
        expected_names = set(contract.tools)
        if names != expected_names:
            raise RuntimeError(
                f"{adapter_id} tool drift: expected={sorted(expected_names)} discovered={sorted(names)}"
            )
        discovered_digests[adapter_id] = digest
        print(
            f"{adapter_id}: initialized, tools={len(names)}, schema_sha256={digest}",
            flush=True,
        )
    if set(SAAS_SCHEMA_SHA256) != set(SAAS_ADAPTERS):
        raise RuntimeError("SaaS schema snapshot coverage mismatch")
    mismatches = {
        adapter_id: digest
        for adapter_id, digest in discovered_digests.items()
        if SAAS_SCHEMA_SHA256.get(adapter_id) != digest
    }
    if mismatches:
        raise RuntimeError(f"SaaS reviewed tool schema drifted: {mismatches}")


if __name__ == "__main__":
    asyncio.run(main())
