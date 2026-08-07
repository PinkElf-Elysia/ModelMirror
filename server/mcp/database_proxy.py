"""Credential-aware stdio proxy for fixed Wave 5 database adapters.

The API process supplies one short-lived, base64 encoded configuration through
the child environment.  The proxy removes it before opening the private Unix
socket and never accepts commands, connection strings, endpoints or environment
variable names from a catalog client.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
from pathlib import Path


ALLOWED_ADAPTERS = {
    "dbhub",
    "mongodb-mcp",
    "clickhouse-mcp",
    "redis-mcp",
    "duckdb-mcp",
    "supabase-mcp",
}
REMOTE_SOCKET_PATH = Path(
    os.getenv(
        "MCP_DATABASE_SOCKET_PATH",
        "/run/modelmirror-database-mcp/database-mcp.sock",
    )
)
LOCAL_SOCKET_PATH = Path(
    os.getenv(
        "MCP_DATABASE_LOCAL_SOCKET_PATH",
        "/run/modelmirror-database-local-mcp/database-mcp.sock",
    )
)
HANDSHAKE_LIMIT = 4096
CONFIGURATION_LIMIT = 64 * 1024


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


def _load_configuration() -> dict[str, object]:
    encoded = os.environ.pop("MCP_DATABASE_HANDSHAKE_B64", "")
    if not encoded or len(encoded) > CONFIGURATION_LIMIT * 2:
        raise ValueError("missing_database_configuration")
    raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    if len(raw) > CONFIGURATION_LIMIT:
        raise ValueError("database_configuration_too_large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid_database_configuration")
    if not isinstance(payload.get("settings"), dict):
        raise ValueError("invalid_database_settings")
    if not isinstance(payload.get("credentials"), dict):
        raise ValueError("invalid_database_credentials")
    workspace_id = payload.get("workspace_id")
    if workspace_id is not None and not isinstance(workspace_id, str):
        raise ValueError("invalid_database_workspace")
    return payload


def run(adapter_id: str) -> int:
    if adapter_id not in ALLOWED_ADAPTERS:
        print("MCP database adapter is not allowed.", file=sys.stderr)
        return 64
    socket_path = LOCAL_SOCKET_PATH if adapter_id == "duckdb-mcp" else REMOTE_SOCKET_PATH
    if not socket_path.is_absolute():
        print("MCP database socket path must be absolute.", file=sys.stderr)
        return 78
    try:
        configuration = _load_configuration()
    except (ValueError, UnicodeError, json.JSONDecodeError):
        print("MCP database configuration is missing or invalid.", file=sys.stderr)
        return 78

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10)
        sock.connect(str(socket_path))
        request = json.dumps(
            {
                "action": "mcp_stdio",
                "adapter_id": adapter_id,
                "configuration": configuration,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        configuration = {}
        sock.sendall(request + b"\n")
        raw = sock.makefile("rb", buffering=0).readline(HANDSHAKE_LIMIT + 1)
        if not raw or len(raw) > HANDSHAKE_LIMIT:
            print("MCP database sidecar handshake failed.", file=sys.stderr)
            return 69
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = str(response.get("code") or "database_sidecar_unavailable")
            print(f"MCP database sidecar rejected the session: {code}", file=sys.stderr)
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
        print(f"MCP database proxy unavailable: {type(exc).__name__}", file=sys.stderr)
        return 69
    finally:
        sock.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: database_proxy.py ADAPTER_ID", file=sys.stderr)
        return 64
    return run(sys.argv[1].strip())


if __name__ == "__main__":
    raise SystemExit(main())
