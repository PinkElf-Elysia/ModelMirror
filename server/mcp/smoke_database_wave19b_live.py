"""Isolated UDS acceptance client for the staged Wave-19B adapters.

The helper has no network.  It receives one disposable configuration, checks
the exact MCP surface, performs representative reads, and proves that write
tools plus open connection fields remain unreachable.
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
ADAPTER_ID = os.getenv("MCP_DATABASE_WAVE19B_ADAPTER", "").strip()
CONFIGURATION_B64 = os.environ.pop("MCP_DATABASE_WAVE19B_CONFIGURATION_B64", "")
EXPECT_TIMEOUT = os.getenv("MCP_DATABASE_WAVE19B_EXPECT_TIMEOUT", "").strip().lower() == "true"

EXPECTED_TOOLS = {
    "zilliztech-mcp-server-milvus": {
        "list_collections", "describe_collection", "get_entities", "search_vectors",
    },
    "neo4j-contrib-mcp-neo4j": {"get_schema", "read_cypher"},
    "arcadedata-arcadedb": {"list_types", "describe_type", "read_query"},
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
            code = handshake.get("code")
            safe_code = (
                code
                if isinstance(code, str)
                and 1 <= len(code) <= 64
                and all(character.islower() or character.isdigit() or character == "_" for character in code)
                else "unclassified"
            )
            raise RuntimeError(f"wave19b_handshake_rejected_{safe_code}")
        if set(handshake.get("tools") or []) != EXPECTED_TOOLS[adapter_id]:
            raise RuntimeError("wave19b_handshake_tool_drift")
        self.next_id = 1

    def _read(self) -> dict[str, Any]:
        raw = self.stream.readline(512 * 1024 + 1)
        if not raw or len(raw) > 512 * 1024:
            raise RuntimeError("wave19b_rpc_response_invalid")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("wave19b_rpc_response_invalid")
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
            raise RuntimeError("wave19b_rpc_id_drift")
        return response

    def notify(self, method: str) -> None:
        self.stream.write(json.dumps({"jsonrpc": "2.0", "method": method}).encode("utf-8") + b"\n")

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.socket.close()


def _assert_success(response: dict[str, Any], label: str) -> None:
    if "error" in response:
        raise RuntimeError(f"wave19b_{label}_rpc_error")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        raise RuntimeError(f"wave19b_{label}_tool_failed")


def _call(client: RpcClient, name: str, arguments: dict[str, Any]) -> None:
    _assert_success(client.request("tools/call", {"name": name, "arguments": arguments}), name)


def _decode_configuration() -> dict[str, Any]:
    if ADAPTER_ID not in EXPECTED_TOOLS or not CONFIGURATION_B64:
        raise RuntimeError("wave19b_smoke_configuration_missing")
    try:
        configuration = json.loads(base64.urlsafe_b64decode(CONFIGURATION_B64.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("wave19b_smoke_configuration_invalid") from exc
    if not isinstance(configuration, dict):
        raise RuntimeError("wave19b_smoke_configuration_invalid")
    return configuration


def main() -> None:
    configuration = _decode_configuration()

    client = RpcClient(ADAPTER_ID, configuration)
    configuration = {}
    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "modelmirror-wave19b-smoke", "version": "1"},
            },
        )
        _assert_success(initialized, "initialize")
        client.notify("notifications/initialized")
        listed = client.request("tools/list")
        tools = listed.get("result", {}).get("tools") if isinstance(listed.get("result"), dict) else None
        if not isinstance(tools, list) or {item.get("name") for item in tools if isinstance(item, dict)} != EXPECTED_TOOLS[ADAPTER_ID]:
            raise RuntimeError("wave19b_tools_list_drift")

        if EXPECT_TIMEOUT:
            if ADAPTER_ID != "neo4j-contrib-mcp-neo4j":
                raise RuntimeError("wave19b_timeout_adapter_invalid")
            started = time.monotonic()
            timed_out = client.request(
                "tools/call",
                {
                    "name": "read_cypher",
                    "arguments": {
                        "query": (
                            "UNWIND range(1, 20000) AS a "
                            "UNWIND range(1, 20000) AS b "
                            "RETURN sum(sin(toFloat(a * b))) AS total"
                        ),
                        "max_rows": 1,
                    },
                },
            )
            elapsed = time.monotonic() - started
            result = timed_out.get("result")
            if (
                "error" in timed_out
                or not isinstance(result, dict)
                or result.get("isError") is not True
                or not 10.0 <= elapsed <= 17.5
            ):
                if "error" in timed_out:
                    response_class = "rpc_error"
                elif isinstance(result, dict) and result.get("isError") is True:
                    response_class = "tool_error"
                elif isinstance(result, dict):
                    response_class = "tool_success"
                else:
                    response_class = "invalid_response"
                elapsed_bucket = "under_10s" if elapsed < 10.0 else "over_17s" if elapsed > 17.5 else "in_window"
                raise RuntimeError(
                    f"wave19b_timeout_contract_failed_{response_class}_{elapsed_bucket}"
                )
            denied_tool = "write_neo4j_cypher"
            guarded_tool = "read_cypher"
            guarded_args = {"query": "MATCH (n) DELETE n RETURN n"}
        elif ADAPTER_ID == "zilliztech-mcp-server-milvus":
            _call(client, "list_collections", {})
            _call(client, "describe_collection", {})
            _call(client, "get_entities", {"ids": [1, 2]})
            _call(client, "search_vectors", {"vector": [1.0, 0.0, 0.0, 0.0], "limit": 5})
            denied_tool = "insert_data"
            guarded_tool = "search_vectors"
            guarded_args = {"vector": [1.0, 0.0, 0.0, 0.0], "url": "http://169.254.169.254"}
        elif ADAPTER_ID == "neo4j-contrib-mcp-neo4j":
            _call(client, "get_schema", {})
            _call(
                client,
                "read_cypher",
                {"query": "MATCH (n:Person) RETURN n.name AS name ORDER BY name", "max_rows": 10},
            )
            denied_tool = "write_neo4j_cypher"
            guarded_tool = "read_cypher"
            guarded_args = {"query": "MATCH (n) DELETE n RETURN n"}
        else:
            _call(client, "list_types", {})
            _call(client, "describe_type", {"type_name": "Person"})
            _call(
                client,
                "read_query",
                {"query": "SELECT name FROM Person ORDER BY name", "max_rows": 10},
            )
            denied_tool = "execute_command"
            guarded_tool = "read_query"
            guarded_args = {"query": "DELETE FROM Person"}

        denied = client.request("tools/call", {"name": denied_tool, "arguments": {}})
        if denied.get("error", {}).get("code") != -32601:
            raise RuntimeError("wave19b_write_tool_not_denied")
        guarded = client.request("tools/call", {"name": guarded_tool, "arguments": guarded_args})
        if guarded.get("error", {}).get("code") != -32602:
            raise RuntimeError("wave19b_policy_bypass_not_denied")
    finally:
        client.close()

    print(
        json.dumps(
            {
                "ok": True,
                "adapter_id": ADAPTER_ID,
                "tools": sorted(EXPECTED_TOOLS[ADAPTER_ID]),
                "representative_calls": "timeout_path" if EXPECT_TIMEOUT else "passed",
                "write_tool": "denied",
                "policy_bypass": "denied",
                "timeout": "bounded" if EXPECT_TIMEOUT else "not_requested",
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
