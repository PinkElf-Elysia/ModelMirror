"""One-shot credential proxy for fixed Wave 6 stateful SaaS adapters."""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
from pathlib import Path


ALLOWED_ADAPTERS = {
    "airtable-mcp",
    "asana-mcp",
    "gitlab-mcp",
    "notion-mcp-server",
}
SOCKET_PATH = Path(
    os.getenv(
        "MCP_SAAS_SOCKET_PATH",
        "/run/modelmirror-saas-mcp/saas-mcp.sock",
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
    encoded = os.environ.pop("MCP_SAAS_HANDSHAKE_B64", "")
    if not encoded or len(encoded) > CONFIGURATION_LIMIT * 2:
        raise ValueError("missing_saas_configuration")
    raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    if len(raw) > CONFIGURATION_LIMIT:
        raise ValueError("saas_configuration_too_large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"settings", "credentials"}:
        raise ValueError("invalid_saas_configuration")
    if not isinstance(payload["settings"], dict) or not isinstance(
        payload["credentials"], dict
    ):
        raise ValueError("invalid_saas_configuration")
    return payload


def run(adapter_id: str) -> int:
    if adapter_id not in ALLOWED_ADAPTERS:
        print("MCP SaaS adapter is not allowed.", file=sys.stderr)
        return 64
    if not SOCKET_PATH.is_absolute():
        print("MCP SaaS socket path must be absolute.", file=sys.stderr)
        return 78
    try:
        configuration = _load_configuration()
    except (ValueError, UnicodeError, json.JSONDecodeError):
        print("MCP SaaS configuration is missing or invalid.", file=sys.stderr)
        return 78

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10)
        sock.connect(str(SOCKET_PATH))
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
            print("MCP SaaS sidecar handshake failed.", file=sys.stderr)
            return 69
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = str(response.get("code") or "saas_sidecar_unavailable")
            print(f"MCP SaaS sidecar rejected the session: {code}", file=sys.stderr)
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
        print(f"MCP SaaS proxy unavailable: {type(exc).__name__}", file=sys.stderr)
        return 69
    finally:
        sock.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: saas_proxy.py ADAPTER_ID", file=sys.stderr)
        return 64
    return run(sys.argv[1].strip())


if __name__ == "__main__":
    raise SystemExit(main())
