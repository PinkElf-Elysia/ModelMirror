"""Live UDS acceptance client for the three staged Wave-19A adapters.

The host harness supplies one base64 configuration and mounts only the private
database socket.  This process has no network and never prints credentials.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import time
from pathlib import Path
from typing import Any


SOCKET_PATH = Path(os.getenv("MCP_DATABASE_SOCKET_PATH", "/run/modelmirror-database-mcp/database-mcp.sock"))
ADAPTER_ID = os.getenv("MCP_DATABASE_WAVE19A_ADAPTER", "").strip()
CONFIGURATION_B64 = os.environ.pop("MCP_DATABASE_WAVE19A_CONFIGURATION_B64", "")

EXPECTED_TOOLS = {
    "pab1it0-prometheus-mcp-server": {
        "execute_query", "execute_range_query", "list_metrics",
        "get_metric_metadata", "get_targets",
    },
    "qdrant-mcp-server-qdrant": {
        "get_collection_info", "scroll_points", "query_points",
    },
    "cr7258-elasticsearch-mcp-server": {
        "get_cluster_health", "get_index", "search_documents", "get_document",
    },
}


class RpcClient:
    def __init__(self, adapter_id: str, configuration: dict[str, Any]) -> None:
        self.adapter_id = adapter_id
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(25)
        self.socket.connect(str(SOCKET_PATH))
        self.stream = self.socket.makefile("rwb", buffering=0)
        self.stream.write(
            json.dumps(
                {
                    "action": "mcp_stdio",
                    "adapter_id": adapter_id,
                    "configuration": configuration,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        handshake = self._read()
        if handshake.get("ok") is not True or handshake.get("read_only") is not True:
            raise RuntimeError("wave19a_handshake_rejected")
        if set(handshake.get("tools") or []) != EXPECTED_TOOLS[adapter_id]:
            raise RuntimeError("wave19a_handshake_tool_drift")
        self.next_id = 1

    def _read(self) -> dict[str, Any]:
        raw = self.stream.readline(512 * 1024 + 1)
        if not raw or len(raw) > 512 * 1024:
            raise RuntimeError("wave19a_rpc_response_invalid")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("wave19a_rpc_response_invalid")
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
            raise RuntimeError("wave19a_rpc_id_drift")
        return response

    def notify(self, method: str) -> None:
        self.stream.write(
            json.dumps({"jsonrpc": "2.0", "method": method}, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.socket.close()


def _assert_success(response: dict[str, Any], label: str) -> None:
    if "error" in response:
        raise RuntimeError(f"{label}_rpc_error")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        raise RuntimeError(f"{label}_tool_failed")


def _call(client: RpcClient, name: str, arguments: dict[str, Any]) -> None:
    _assert_success(
        client.request("tools/call", {"name": name, "arguments": arguments}),
        name.replace("-", "_"),
    )


def main() -> None:
    if ADAPTER_ID not in EXPECTED_TOOLS or not CONFIGURATION_B64:
        raise RuntimeError("wave19a_smoke_configuration_missing")
    try:
        configuration = json.loads(base64.urlsafe_b64decode(CONFIGURATION_B64.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("wave19a_smoke_configuration_invalid") from exc
    if not isinstance(configuration, dict):
        raise RuntimeError("wave19a_smoke_configuration_invalid")

    client = RpcClient(ADAPTER_ID, configuration)
    configuration = {}
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "modelmirror-wave19a-smoke", "version": "1"},
            },
        )
        _assert_success(initialized, "initialize")
        client.notify("notifications/initialized")
        listed = client.request("tools/list")
        tools = listed.get("result", {}).get("tools") if isinstance(listed.get("result"), dict) else None
        if not isinstance(tools, list) or {item.get("name") for item in tools if isinstance(item, dict)} != EXPECTED_TOOLS[ADAPTER_ID]:
            raise RuntimeError("wave19a_tools_list_drift")

        if ADAPTER_ID == "pab1it0-prometheus-mcp-server":
            now = int(time.time())
            _call(client, "execute_query", {"query": "up"})
            _call(client, "execute_range_query", {"query": "up", "start": str(now - 60), "end": str(now), "step": "15s"})
            _call(client, "list_metrics", {"limit": 20})
            _call(client, "get_metric_metadata", {"metric": "up", "limit": 5})
            _call(client, "get_targets", {"limit": 10})
            denied_tool = "delete_series"
            guarded_tool = "execute_query"
            guarded_args = {"query": "up", "url": "http://169.254.169.254"}
        elif ADAPTER_ID == "qdrant-mcp-server-qdrant":
            _call(client, "get_collection_info", {})
            _call(client, "scroll_points", {"limit": 10})
            _call(client, "query_points", {"vector": [1.0, 0.0, 0.0, 0.0], "limit": 5})
            denied_tool = "qdrant-store"
            guarded_tool = "query_points"
            guarded_args = {"vector": [1.0, 0.0, 0.0, 0.0], "headers": {"x": "y"}}
        else:
            _call(client, "get_cluster_health", {})
            _call(client, "get_index", {})
            _call(client, "search_documents", {"query": "wave19a", "max_rows": 10})
            _call(client, "get_document", {"id": "1"})
            denied_tool = "index_document"
            guarded_tool = "search_documents"
            guarded_args = {"query": "wave19a", "url": "http://169.254.169.254"}

        denied = client.request("tools/call", {"name": denied_tool, "arguments": {}})
        if denied.get("error", {}).get("code") != -32601:
            raise RuntimeError("wave19a_write_tool_not_denied")
        guarded = client.request("tools/call", {"name": guarded_tool, "arguments": guarded_args})
        if guarded.get("error", {}).get("code") != -32602:
            raise RuntimeError("wave19a_open_surface_not_denied")
    finally:
        client.close()

    print(
        json.dumps(
            {
                "ok": True,
                "adapter_id": ADAPTER_ID,
                "tools": sorted(EXPECTED_TOOLS[ADAPTER_ID]),
                "representative_calls": "passed",
                "write_tool": "denied",
                "open_surface": "denied",
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
