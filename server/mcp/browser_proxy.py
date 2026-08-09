"""One-shot stdio proxy for the fixed Wave 7 browser MCP sidecar."""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
from pathlib import Path


ALLOWED_ADAPTERS = frozenset({"chrome-devtools-mcp", "playwright-mcp"})
CONTRACT_VERSION = "modelmirror-browser-wave7-v1"
SOCKET_PATH = Path(
    os.getenv(
        "MCP_BROWSER_SOCKET_PATH",
        "/run/modelmirror-browser-mcp/browser-mcp.sock",
    )
)
HANDSHAKE_LIMIT = 64 * 1024
RESPONSE_LIMIT = 4096
EXPECTED_LIMITS = {
    "max_actions": 50,
    "max_artifact_bytes": 32 * 1024 * 1024,
    "max_output_bytes": 256 * 1024,
    "max_pages": 1,
    "navigation_timeout_seconds": 20,
    "session_ttl_seconds": 15 * 60,
    "idle_ttl_seconds": 5 * 60,
    "tool_call_timeout_seconds": 30,
    "max_sessions": 1,
    "max_tunnels_per_session": 12,
    "max_egress_bytes_per_session": 64 * 1024 * 1024,
    "egress_tunnel_idle_seconds": 30,
    "egress_tunnel_ttl_seconds": 120,
}

# Kept in sync with sandbox_sidecar.browser_contracts.  The server image does
# not contain sidecar implementation modules, so the public contract digests
# are deliberately duplicated here and checked on both sides of the socket.
BROWSER_SCHEMA_SHA256 = {
    "chrome-devtools-mcp": "74e74ee147c7293035b969af687248b05934052973d7798ecdc651208a7739c3",
    "playwright-mcp": "efafd6dabf2e78173423ed2172092eb9865d82b8571fe5f687b9658ce9caaadc",
}


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


def _load_handshake(adapter_id: str) -> dict[str, object]:
    encoded = os.environ.pop("MCP_BROWSER_HANDSHAKE_B64", "")
    if not encoded or len(encoded) > HANDSHAKE_LIMIT * 2:
        raise ValueError("missing_browser_handshake")
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid_browser_handshake") from exc
    if len(raw) > HANDSHAKE_LIMIT:
        raise ValueError("browser_handshake_too_large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_browser_handshake") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "project_id",
        "contract_version",
        "tool_schema_sha256",
        "limits",
    }:
        raise ValueError("browser_handshake_contract_mismatch")
    if payload.get("project_id") != adapter_id:
        raise ValueError("browser_handshake_project_mismatch")
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("browser_handshake_version_mismatch")
    if payload.get("tool_schema_sha256") != BROWSER_SCHEMA_SHA256.get(adapter_id):
        raise ValueError("browser_handshake_schema_mismatch")
    if payload.get("limits") != EXPECTED_LIMITS:
        raise ValueError("browser_handshake_limits_mismatch")
    return payload


def run(adapter_id: str) -> int:
    if adapter_id not in ALLOWED_ADAPTERS:
        print("MCP browser adapter is not allowed.", file=sys.stderr)
        return 64
    if not SOCKET_PATH.is_absolute():
        print("MCP browser socket path must be absolute.", file=sys.stderr)
        return 78
    try:
        configuration = _load_handshake(adapter_id)
    except ValueError:
        print("MCP browser handshake is missing or invalid.", file=sys.stderr)
        return 78

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        # The sidecar verifies the real upstream package, starts one sandboxed
        # Chromium process and performs a representative blank-page snapshot
        # before accepting the session.
        sock.settimeout(60)
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
        raw = sock.makefile("rb", buffering=0).readline(RESPONSE_LIMIT + 1)
        if not raw or len(raw) > RESPONSE_LIMIT:
            print("MCP browser sidecar handshake failed.", file=sys.stderr)
            return 69
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = str(response.get("code") or "browser_sidecar_unavailable")
            print(f"MCP browser sidecar rejected the session: {code}", file=sys.stderr)
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
        print(f"MCP browser proxy unavailable: {type(exc).__name__}", file=sys.stderr)
        return 69
    finally:
        sock.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: browser_proxy.py ADAPTER_ID", file=sys.stderr)
        return 64
    return run(sys.argv[1].strip())


if __name__ == "__main__":
    raise SystemExit(main())
