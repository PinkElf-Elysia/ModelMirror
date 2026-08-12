"""Unix-socket sidecar for fixed public-network MCP child processes."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from .engine import MAX_REQUEST_BYTES, SandboxEngineError
from .public_mcp import BUILDERS


SOCKET_PATH = Path(
    os.getenv(
        "MCP_PUBLIC_SOCKET_PATH",
        "/run/modelmirror-public-mcp/public-mcp.sock",
    )
)
WORKSPACE_ROOT = Path(os.getenv("MCP_PUBLIC_WORKSPACE_ROOT", "/workspaces"))
ALL_PUBLIC_ADAPTERS = frozenset(BUILDERS)
DEFAULT_PUBLIC_ADAPTERS = frozenset(
    {
        "fetch-mcp",
        "quickchart-mcp",
        "geowire-mcp",
        "nickclyde-duckduckgo-mcp-server",
        "jpisnice-shadcn-ui-mcp-server",
        "docker-hub-mcp",
        "genomoncology-biomcp",
        "safedep-vet",
        "aas-ee-open-websearch",
        "mnemox-ai-idea-reality-mcp",
        "idosal-git-mcp",
        "coinpaprika-dexpaprika-mcp",
        "pab1it0-chess-mcp",
        "rishijatia-fantasy-pl-mcp",
        "yuna0x0-anilist-mcp",
    }
)


def configured_public_adapters(raw: str) -> frozenset[str]:
    clean = str(raw or "").strip()
    configured = (
        frozenset(item.strip() for item in clean.split(",") if item.strip())
        if clean
        else DEFAULT_PUBLIC_ADAPTERS
    )
    if not configured or not configured.issubset(ALL_PUBLIC_ADAPTERS):
        raise RuntimeError("invalid_public_adapter_allowlist")
    return configured


PUBLIC_ADAPTERS = configured_public_adapters(
    os.getenv("MCP_PUBLIC_ALLOWED_ADAPTERS", "")
)
MAX_MCP_MESSAGE_BYTES = 256 * 1024
MAX_PUBLIC_SESSIONS = max(
    1,
    min(int(os.getenv("MCP_PUBLIC_MAX_SESSIONS", "6")), 16),
)
PUBLIC_SEMAPHORE = asyncio.Semaphore(MAX_PUBLIC_SESSIONS)


async def _pump_lines(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    *,
    adapter_id: str,
    direction: str,
) -> None:
    first_line = True
    while True:
        raw = await source.readline()
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            raise SandboxEngineError(
                "MCP message exceeds the 256 KiB limit.",
                code="mcp_message_too_large",
            )
        if first_line:
            print(
                f"MCP public stream active: adapter={adapter_id} direction={direction}",
                file=sys.stderr,
            )
            first_line = False
        destination.write(raw)
        await destination.drain()


async def _drain_stderr(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(4096):
        pass


async def _handle_mcp_stdio(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request: dict[str, Any],
) -> None:
    adapter_id = str(request.get("adapter_id") or "").strip()
    if adapter_id not in PUBLIC_ADAPTERS:
        raise SandboxEngineError(
            "MCP public adapter is not allowed.",
            code="mcp_adapter_denied",
        )

    async with PUBLIC_SEMAPHORE:
        workspace = (WORKSPACE_ROOT / f"mcp-public-{uuid.uuid4().hex}").resolve()
        if workspace.parent != WORKSPACE_ROOT.resolve():
            raise SandboxEngineError(
                "Unsafe public MCP workspace.",
                code="unsafe_workspace",
            )
        (workspace / "work").mkdir(parents=True, exist_ok=False)
        command = [
            sys.executable,
            str(Path(__file__).with_name("landlock_exec.py")),
            str(workspace),
            "--read-only",
            "--compute-limits",
            "--",
            sys.executable,
            "-m",
            "sandbox_sidecar.public_mcp",
            adapter_id,
        ]
        env = {
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
            "MCP_PUBLIC_ALLOW_SYNTHETIC_DNS": os.getenv(
                "MCP_PUBLIC_ALLOW_SYNTHETIC_DNS",
                "false",
            ),
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
        process: asyncio.subprocess.Process | None = None
        tasks: list[asyncio.Task[None]] = []
        handshake_sent = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workspace / "work"),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=MAX_MCP_MESSAGE_BYTES + 1,
            )
            if process.stdin is None or process.stdout is None:
                raise SandboxEngineError(
                    "MCP public stdio is unavailable.",
                    code="mcp_stdio_unavailable",
                )
            writer.write(
                json.dumps(
                    {
                        "ok": True,
                        "adapter_id": adapter_id,
                        "protocol": "modelmirror-mcp-stdio-v1",
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            await writer.drain()
            handshake_sent = True
            tasks = [
                asyncio.create_task(
                    _pump_lines(
                        reader,
                        process.stdin,
                        adapter_id=adapter_id,
                        direction="client_to_child",
                    )
                ),
                asyncio.create_task(
                    _pump_lines(
                        process.stdout,
                        writer,
                        adapter_id=adapter_id,
                        direction="child_to_client",
                    )
                ),
                asyncio.create_task(_drain_stderr(process.stderr)),
            ]
            done, pending = await asyncio.wait(
                tasks[:2],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
        except Exception as exc:
            if not handshake_sent:
                raise
            print(
                f"MCP public session ended: adapter={adapter_id} error_type={type(exc).__name__}",
                file=sys.stderr,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            if process is not None:
                print(
                    f"MCP public process stopped: adapter={adapter_id} exit_code={process.returncode}",
                    file=sys.stderr,
                )
            shutil.rmtree(workspace, ignore_errors=True)
            if handshake_sent:
                writer.close()
                await writer.wait_closed()


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            raise SandboxEngineError(
                "Public sidecar request is empty or too large.",
                code="invalid_request",
            )
        request = json.loads(raw.decode("utf-8"))
        if not isinstance(request, dict):
            raise SandboxEngineError(
                "Public sidecar request must be an object.",
                code="invalid_request",
            )
        action = str(request.get("action") or "").strip()
        if action == "mcp_stdio":
            await _handle_mcp_stdio(reader, writer, request)
            return
        if action != "health":
            raise SandboxEngineError(
                "Public sidecar action is not allowed.",
                code="action_denied",
            )
        response = {
            "ok": True,
            "mcp_public_adapters": sorted(PUBLIC_ADAPTERS),
            "mcp_public_max_sessions": MAX_PUBLIC_SESSIONS,
            "mcp_message_limit_bytes": MAX_MCP_MESSAGE_BYTES,
        }
    except SandboxEngineError as exc:
        response = {"ok": False, "error": str(exc), "code": exc.code}
    except Exception as exc:
        response = {
            "ok": False,
            "error": str(exc)[:1000],
            "code": "public_sidecar_internal_error",
        }
    writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(
        handle_client,
        path=str(SOCKET_PATH),
        limit=MAX_REQUEST_BYTES + 1,
    )
    os.chmod(SOCKET_PATH, 0o660)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
