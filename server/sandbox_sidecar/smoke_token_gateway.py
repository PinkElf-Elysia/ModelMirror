"""Manual JSON-RPC probe for the private Token sidecar Unix-socket gateway."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path

from .token_contracts import TOKEN_ADAPTERS, TOKEN_SCHEMA_SHA256


def _configuration(adapter_id: str) -> dict[str, dict[str, str]]:
    contract = TOKEN_ADAPTERS[adapter_id]
    return {
        "credentials": {
            key: f"offline-smoke-{key}"
            for key, _ in contract.credential_environment
        },
        "settings": {
            key: "offline-smoke"
            for key, _ in contract.setting_environment
        },
    }


def _read_response(stream: object, request_id: int) -> dict[str, object]:
    for _ in range(64):
        raw = stream.readline(256 * 1024 + 1)
        if not raw or len(raw) > 256 * 1024:
            raise RuntimeError("token_gateway_response_invalid")
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return payload
    raise RuntimeError("token_gateway_response_missing")


def probe(adapter_id: str, *, expect_denied: bool) -> None:
    socket_path = Path(
        os.getenv(
            "MCP_TOKEN_SOCKET_PATH",
            "/run/modelmirror-token-mcp/token-mcp.sock",
        )
    )
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    client.connect(str(socket_path))
    stream = client.makefile("rwb", buffering=0)
    handshake = {
        "action": "mcp_stdio",
        "adapter_id": adapter_id,
        "configuration": _configuration(adapter_id),
    }
    stream.write(json.dumps(handshake, separators=(",", ":")).encode() + b"\n")
    raw = stream.readline(4097)
    response = json.loads(raw.decode("utf-8")) if raw else {}
    if expect_denied:
        if not isinstance(response, dict) or response.get("code") != "mcp_adapter_denied":
            raise RuntimeError("staged_adapter_was_not_denied")
        print(f"adapter={adapter_id} gateway_default_deny=ok")
        return
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("token_gateway_handshake_rejected")

    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "modelmirror-token-gateway-smoke", "version": "1"},
        },
    }
    stream.write(json.dumps(initialize, separators=(",", ":")).encode() + b"\n")
    initialized = _read_response(stream, 1)
    if "result" not in initialized:
        raise RuntimeError("token_gateway_initialize_failed")
    stream.write(
        b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n'
    )
    stream.write(b'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n')
    listed = _read_response(stream, 2)
    result = listed.get("result")
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise RuntimeError("token_gateway_tools_invalid")
    reviewed = [
        {"name": item.get("name"), "inputSchema": item.get("inputSchema")}
        for item in sorted(
            (value for value in tools if isinstance(value, dict)),
            key=lambda value: str(value.get("name")),
        )
    ]
    names = {str(item["name"]) for item in reviewed}
    if names != set(TOKEN_ADAPTERS[adapter_id].tools):
        raise RuntimeError("token_gateway_tool_filter_drift")
    digest = hashlib.sha256(
        json.dumps(
            reviewed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if digest != TOKEN_SCHEMA_SHA256[adapter_id]:
        raise RuntimeError("token_gateway_schema_drift")
    client.shutdown(socket.SHUT_RDWR)
    client.close()
    print(
        f"adapter={adapter_id} gateway_initialize=ok tools={len(names)} "
        f"schema_sha256={digest} disconnect=ok"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("adapter_id", choices=sorted(TOKEN_ADAPTERS))
    parser.add_argument("--expect-denied", action="store_true")
    args = parser.parse_args()
    probe(args.adapter_id, expect_denied=args.expect_denied)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
