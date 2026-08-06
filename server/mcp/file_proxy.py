"""Fixed stdio proxy for reviewed Wave 3 file adapters."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
from pathlib import Path


ALLOWED_ADAPTERS = {"basic-memory-mcp", "excel-mcp-server", "git-mcp", "markitdown-mcp"}
WORKSPACE_PATTERN = re.compile(r"mcpws_[0-9a-f]{32}")
SOCKET_PATH = Path(os.getenv("MCP_FILES_SOCKET_PATH", "/run/modelmirror-files-mcp/files-mcp.sock"))


def _copy_stdin(sock: socket.socket) -> None:
    try:
        while True:
            chunk = os.read(sys.stdin.fileno(), 64 * 1024)
            if not chunk: break
            sock.sendall(chunk)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        try: sock.shutdown(socket.SHUT_WR)
        except OSError: pass


def run(adapter_id: str) -> int:
    workspace_id = os.getenv("MCP_FILE_WORKSPACE_ID", "").strip()
    if adapter_id not in ALLOWED_ADAPTERS or not WORKSPACE_PATTERN.fullmatch(workspace_id):
        print("MCP file adapter or workspace is not allowed.", file=sys.stderr); return 64
    if not SOCKET_PATH.is_absolute():
        print("MCP file socket path must be absolute.", file=sys.stderr); return 78
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10); sock.connect(str(SOCKET_PATH))
        sock.sendall(json.dumps({"action": "mcp_stdio", "adapter_id": adapter_id, "workspace_id": workspace_id}, separators=(",", ":")).encode() + b"\n")
        raw = sock.makefile("rb", buffering=0).readline(4097)
        response = json.loads(raw.decode("utf-8")) if raw and len(raw) <= 4096 else {}
        if response.get("ok") is not True:
            print(f"MCP file sidecar rejected session: {response.get('code', 'unavailable')}", file=sys.stderr); return 69
        sock.settimeout(None)
        threading.Thread(target=_copy_stdin, args=(sock,), daemon=True).start()
        while True:
            chunk = sock.recv(64 * 1024)
            if not chunk: break
            sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()
        return 0
    except (ConnectionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"MCP file proxy unavailable: {type(exc).__name__}", file=sys.stderr); return 69
    finally:
        sock.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: file_proxy.py ADAPTER_ID", file=sys.stderr); return 64
    return run(sys.argv[1].strip())


if __name__ == "__main__":
    raise SystemExit(main())
