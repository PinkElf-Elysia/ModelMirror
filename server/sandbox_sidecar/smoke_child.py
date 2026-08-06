"""Direct sidecar self-check for the bundled MCP child process."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .compute_mcp import ADAPTER_TOOL_NAMES


async def main() -> None:
    adapter_id = sys.argv[1] if len(sys.argv) > 1 else "calculator-mcp"
    if adapter_id not in ADAPTER_TOOL_NAMES:
        raise SystemExit("unsupported adapter")
    root = Path(os.getenv("SANDBOX_WORKSPACE_ROOT", "/workspaces")).resolve()
    workspace = root / f"self-check-{uuid.uuid4().hex}"
    (workspace / "work").mkdir(parents=True)
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(Path(__file__).with_name("landlock_exec.py")),
            str(workspace),
            "--read-only",
            "--compute-limits",
            "--",
            sys.executable,
            "-m",
            "sandbox_sidecar.compute_mcp",
            adapter_id,
        ],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/opt/modelmirror",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "HOME": str(workspace / "work"),
            "TMPDIR": str(workspace / "work"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "NO_PROXY": "*",
            "no_proxy": "*",
        },
        cwd=str(workspace / "work"),
    )
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await asyncio.wait_for(session.initialize(), timeout=10)
                tools = await asyncio.wait_for(session.list_tools(), timeout=10)
                names = {tool.name for tool in tools.tools}
                if names != set(ADAPTER_TOOL_NAMES[adapter_id]):
                    raise RuntimeError(f"schema drift: {sorted(names)}")
                print(f"{adapter_id}: {len(names)} tools")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
