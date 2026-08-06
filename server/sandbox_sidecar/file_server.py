"""Unix-socket sidecar for fixed, network-free Wave 3 MCP adapters."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .engine import SandboxEngineError
from .file_mcp import BUILDERS, WORKSPACE_PATTERN


SOCKET_PATH = Path(os.getenv("MCP_FILES_SOCKET_PATH", "/run/modelmirror-files-mcp/files-mcp.sock"))
INPUT_ROOT = Path(os.getenv("MCP_FILE_INPUT_ROOT", "/inputs"))
OUTPUT_ROOT = Path(os.getenv("MCP_FILE_OUTPUT_ROOT", "/outputs"))
MEMORY_ROOT = Path(os.getenv("MCP_FILE_MEMORY_ROOT", "/memory"))
MAX_REQUEST_BYTES = 16 * 1024
MAX_MCP_MESSAGE_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = max(1, min(int(os.getenv("MCP_FILES_MAX_SESSIONS", "4")), 8))
SEMAPHORE = asyncio.Semaphore(MAX_SESSIONS)


async def _pump(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise SandboxEngineError("MCP message exceeds 16 MiB.", code="mcp_message_too_large")
        destination.write(raw)
        await destination.drain()


async def _drain(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(4096):
        pass


async def _stdio(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, request: dict[str, Any]) -> None:
    adapter_id = str(request.get("adapter_id") or "").strip()
    workspace_id = str(request.get("workspace_id") or "").strip()
    if adapter_id not in BUILDERS:
        raise SandboxEngineError("MCP file adapter is not allowed.", code="mcp_adapter_denied")
    if not WORKSPACE_PATTERN.fullmatch(workspace_id):
        raise SandboxEngineError("MCP file workspace identifier is invalid.", code="workspace_denied")
    input_root = (INPUT_ROOT / workspace_id).resolve()
    if input_root.parent != INPUT_ROOT.resolve() or not input_root.is_dir():
        raise SandboxEngineError("MCP file workspace is unavailable.", code="workspace_unavailable")

    async with SEMAPHORE:
        (OUTPUT_ROOT / workspace_id).mkdir(parents=True, exist_ok=True)
        (MEMORY_ROOT / workspace_id).mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=f"mcp-files-{workspace_id[-8:]}-"))
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": "/opt/modelmirror",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "HOME": str(temp_root),
            "TMPDIR": str(temp_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "MCP_FILE_WORKSPACE_ID": workspace_id,
            "MCP_FILE_ADAPTER_ID": adapter_id,
            "MCP_FILE_INPUT_ROOT": str(INPUT_ROOT),
            "MCP_FILE_OUTPUT_ROOT": str(OUTPUT_ROOT),
            "MCP_FILE_MEMORY_ROOT": str(MEMORY_ROOT),
        }
        command = [
            sys.executable,
            "-m", "sandbox_sidecar.file_landlock_exec", "--",
            sys.executable, "-m", "sandbox_sidecar.file_mcp", adapter_id,
        ]
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[None]] = []
        handshake = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command, cwd=temp_root, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True,
                limit=MAX_MCP_MESSAGE_BYTES + 1,
            )
            if process.stdin is None or process.stdout is None:
                raise SandboxEngineError("MCP file stdio unavailable.", code="mcp_stdio_unavailable")
            writer.write(json.dumps({"ok": True, "adapter_id": adapter_id, "workspace_id": workspace_id, "protocol": "modelmirror-mcp-stdio-v1"}, separators=(",", ":")).encode() + b"\n")
            await writer.drain()
            handshake = True
            tasks = [
                asyncio.create_task(_pump(reader, process.stdin)),
                asyncio.create_task(_pump(process.stdout, writer)),
                asyncio.create_task(_drain(process.stderr)),
            ]
            done, pending = await asyncio.wait(tasks[:2], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            if tasks: await asyncio.gather(*tasks, return_exceptions=True)
            if process is not None and process.returncode is None:
                process.terminate()
                try: await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill(); await process.wait()
            shutil.rmtree(temp_root, ignore_errors=True)
            if handshake:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1)
                except (asyncio.TimeoutError, ConnectionError, OSError):
                    pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            raise SandboxEngineError("File sidecar request invalid.", code="invalid_request")
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise SandboxEngineError("File sidecar request must be an object.", code="invalid_request")
        action = str(request.get("action") or "")
        if action == "mcp_stdio":
            await _stdio(reader, writer, request); return
        if action != "health":
            raise SandboxEngineError("File sidecar action denied.", code="action_denied")
        response = {"ok": True, "mcp_file_adapters": sorted(BUILDERS), "mcp_file_max_sessions": MAX_SESSIONS, "network": "none", "mcp_message_limit_bytes": MAX_MCP_MESSAGE_BYTES}
    except SandboxEngineError as exc:
        response = {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:
        response = {"ok": False, "error": str(exc)[:1000], "code": "file_sidecar_internal_error"}
    writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    await writer.drain()
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=1)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass


async def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(SOCKET_PATH), limit=MAX_MCP_MESSAGE_BYTES + 1)
    os.chmod(SOCKET_PATH, 0o660)
    async with server: await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
