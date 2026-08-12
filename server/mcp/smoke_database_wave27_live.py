"""Live UDS acceptance client for the staged Wave-27 GreptimeDB adapter.

The host harness supplies one base64 configuration and mounts only the private
database socket. This helper has no network access and never prints credentials,
provider payloads, SQL text, hostnames, or row values.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Any


SOCKET_PATH = Path(
    os.getenv("MCP_DATABASE_SOCKET_PATH", "/run/modelmirror-database-mcp/database-mcp.sock")
)
ADAPTER_ID = "greptimeteam-greptimedb-mcp-server"
CONFIGURATION_B64 = os.environ.pop("MCP_DATABASE_WAVE27_CONFIGURATION_B64", "")
EXPECT_TIMEOUT = os.environ.pop("MCP_DATABASE_WAVE27_EXPECT_TIMEOUT", "") == "true"
EXPECTED_TOOLS = {"describe_table", "query_range", "health_check"}
EXPECTED_SCHEMA_SHA256 = "86c8dbbfda387925e345fde14bdfdb3681c2b02e5072e5b84bfb7000e1aef65c"


class RpcClient:
    def __init__(self, configuration: dict[str, Any]) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(25)
        self.socket.connect(str(SOCKET_PATH))
        self.stream = self.socket.makefile("rwb", buffering=0)
        self.stream.write(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": ADAPTER_ID,
                    "configuration": configuration,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        handshake = self._read()
        if handshake.get("ok") is not True or handshake.get("read_only") is not True:
            raise RuntimeError("wave27_handshake_rejected")
        if set(handshake.get("tools") or []) != EXPECTED_TOOLS:
            raise RuntimeError("wave27_handshake_tool_drift")
        self.next_id = 1

    def _read(self) -> dict[str, Any]:
        raw = self.stream.readline(512 * 1024 + 1)
        if not raw or len(raw) > 512 * 1024:
            raise RuntimeError("wave27_rpc_response_invalid")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("wave27_rpc_response_invalid")
        return value

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.stream.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
        response = self._read()
        if response.get("id") != request_id:
            raise RuntimeError("wave27_rpc_id_drift")
        return response

    def notify(self, method: str) -> None:
        self.stream.write(
            json.dumps({"jsonrpc": "2.0", "method": method}, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.socket.close()


def _result(response: dict[str, Any], label: str) -> dict[str, Any]:
    if "error" in response:
        raise RuntimeError(f"{label}_rpc_error")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        raise RuntimeError(f"{label}_tool_failed")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        value = structured.get("result", structured)
        if isinstance(value, dict):
            return value
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise RuntimeError(f"{label}_result_invalid")


def _call(client: RpcClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _result(
        client.request("tools/call", {"name": name, "arguments": arguments}),
        name,
    )


def main() -> None:
    if not CONFIGURATION_B64:
        raise RuntimeError("wave27_smoke_configuration_missing")
    try:
        configuration = json.loads(base64.urlsafe_b64decode(CONFIGURATION_B64.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("wave27_smoke_configuration_invalid") from exc
    if not isinstance(configuration, dict):
        raise RuntimeError("wave27_smoke_configuration_invalid")

    client = RpcClient(configuration)
    configuration = {}
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "modelmirror-wave27-smoke", "version": "1"},
            },
        )
        if "error" in initialized:
            raise RuntimeError("wave27_initialize_failed")
        client.notify("notifications/initialized")
        listed = client.request("tools/list")
        result = listed.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise RuntimeError("wave27_tools_list_invalid")
        reviewed = [
            {"name": item.get("name"), "inputSchema": item.get("inputSchema")}
            for item in sorted(
                (item for item in tools if isinstance(item, dict)),
                key=lambda item: str(item.get("name")),
            )
        ]
        if {item["name"] for item in reviewed} != EXPECTED_TOOLS:
            raise RuntimeError("wave27_tools_list_drift")
        digest = hashlib.sha256(
            json.dumps(reviewed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if digest != EXPECTED_SCHEMA_SHA256:
            raise RuntimeError("wave27_schema_drift")

        if EXPECT_TIMEOUT:
            started = time.monotonic()
            timed_out = client.request("tools/call", {"name": "health_check", "arguments": {}})
            elapsed = time.monotonic() - started
            result = timed_out.get("result")
            if not isinstance(result, dict) or result.get("isError") is not True:
                raise RuntimeError("wave27_timeout_not_reported")
            if not 10.5 <= elapsed <= 16.0:
                raise RuntimeError("wave27_timeout_deadline_drift")
            summary = {
                "ok": True,
                "adapter_id": ADAPTER_ID,
                "tools": sorted(EXPECTED_TOOLS),
                "schema_sha256": EXPECTED_SCHEMA_SHA256,
                "provider_timeout": "bounded",
                "elapsed_seconds_bucket": "10.5-16.0",
            }
        else:
            health = _call(client, "health_check", {})
            described = _call(client, "describe_table", {})
            queried = _call(
                client,
                "query_range",
                {
                    "start": "2026-08-01T00:00:00Z",
                    "end": "2026-08-01T02:00:00Z",
                    "limit": 10,
                },
            )
            if health.get("status") != "ok":
                raise RuntimeError("wave27_health_result_invalid")
            if not isinstance(described.get("returned_count"), int) or described["returned_count"] < 3:
                raise RuntimeError("wave27_describe_result_invalid")
            if queried.get("returned_count") != 2 or queried.get("truncated") is not False:
                raise RuntimeError("wave27_query_result_invalid")

            denied = client.request("tools/call", {"name": "execute_sql", "arguments": {}})
            if denied.get("error", {}).get("code") != -32601:
                raise RuntimeError("wave27_generic_sql_not_denied")
            guarded = client.request(
                "tools/call",
                {
                    "name": "query_range",
                    "arguments": {
                        "start": "2026-08-01T00:00:00Z",
                        "end": "2026-08-01T02:00:00Z",
                        "query": "SELECT secret FROM other_table",
                    },
                },
            )
            if guarded.get("error", {}).get("code") != -32602:
                raise RuntimeError("wave27_open_query_surface_not_denied")
            summary = {
                "ok": True,
                "adapter_id": ADAPTER_ID,
                "tools": sorted(EXPECTED_TOOLS),
                "schema_sha256": EXPECTED_SCHEMA_SHA256,
                "health_status": "ok",
                "described_columns_minimum": 3,
                "query_returned_count": 2,
                "generic_sql": "denied",
                "open_query_surface": "denied",
            }
    finally:
        client.close()

    print(json.dumps(summary, separators=(",", ":")))


if __name__ == "__main__":
    main()
