"""Fixed stdio proxy for reviewed public-network catalog adapters."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path


ALLOWED_ADAPTERS = {
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
SOCKET_PATH = Path(
    os.getenv(
        "MCP_PUBLIC_SOCKET_PATH",
        "/run/modelmirror-public-mcp/public-mcp.sock",
    )
)
HANDSHAKE_LIMIT = 4096


def _copy_stdin(sock: socket.socket) -> None:
    try:
        while True:
            chunk = os.read(sys.stdin.fileno(), 64 * 1024)
            if not chunk:
                break
            sock.sendall(chunk)
    except (BrokenPipeError, ConnectionError, OSError):
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def run(adapter_id: str) -> int:
    if adapter_id not in ALLOWED_ADAPTERS:
        print("MCP public adapter is not allowed.", file=sys.stderr)
        return 64
    if not SOCKET_PATH.is_absolute():
        print("MCP public socket path must be absolute.", file=sys.stderr)
        return 78

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10)
        sock.connect(str(SOCKET_PATH))
        request = json.dumps(
            {"action": "mcp_stdio", "adapter_id": adapter_id},
            separators=(",", ":"),
        ).encode("utf-8")
        sock.sendall(request + b"\n")
        handshake_file = sock.makefile("rb", buffering=0)
        raw = handshake_file.readline(HANDSHAKE_LIMIT + 1)
        if not raw or len(raw) > HANDSHAKE_LIMIT:
            print("MCP public sidecar handshake failed.", file=sys.stderr)
            return 69
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = str(response.get("code") or "public_sidecar_unavailable")
            print(f"MCP public sidecar rejected the session: {code}", file=sys.stderr)
            return 69

        sock.settimeout(None)
        input_thread = threading.Thread(target=_copy_stdin, args=(sock,), daemon=True)
        input_thread.start()
        while True:
            chunk = sock.recv(64 * 1024)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return 0
    except (ConnectionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"MCP public proxy unavailable: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 69
    finally:
        sock.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: public_proxy.py ADAPTER_ID", file=sys.stderr)
        return 64
    return run(sys.argv[1].strip())


if __name__ == "__main__":
    raise SystemExit(main())
