"""Fixed 429 fixture and UDS acceptance client for Wave-19B."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import smoke_database_wave19b_live as live


class _FixtureHandler(BaseHTTPRequestHandler):
    request_count = 0

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers.get("content-length", "0"))
        if length < 0 or length > 128 * 1024:
            self.send_error(413)
            return
        self.rfile.read(length)
        type(self).request_count += 1
        if type(self).request_count == 1 and self.path == "/db/neo4j/query/v2":
            status = 200
            payload: dict[str, Any] = {
                "data": {"fields": ["modelmirror_preflight"], "values": [[1]]},
                "queryType": "r",
            }
        else:
            status = 429
            payload = {"error": "rate_limited"}
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def fixture() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 18080), _FixtureHandler)
    print("wave19b_rate_fixture=ready", flush=True)
    server.serve_forever()


def client() -> None:
    if live.ADAPTER_ID != "neo4j-contrib-mcp-neo4j":
        raise RuntimeError("wave19b_rate_adapter_invalid")
    configuration = live._decode_configuration()
    rpc = live.RpcClient(live.ADAPTER_ID, configuration)
    configuration = {}
    try:
        initialized = rpc.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "modelmirror-wave19b-rate-smoke", "version": "1"},
            },
        )
        live._assert_success(initialized, "initialize")
        rpc.notify("notifications/initialized")
        limited = rpc.request(
            "tools/call",
            {
                "name": "read_cypher",
                "arguments": {"query": "MATCH (n) RETURN n LIMIT 1", "max_rows": 1},
            },
        )
        result = limited.get("result")
        if "error" in limited or not isinstance(result, dict) or result.get("isError") is not True:
            raise RuntimeError("wave19b_rate_limit_not_rejected")
        encoded = json.dumps(result, ensure_ascii=False)
        for forbidden in ("rate_limited", "database_rate_limited", "http://", "https://", "neo4j-rate"):
            if forbidden in encoded:
                raise RuntimeError("wave19b_rate_limit_not_redacted")
    finally:
        rpc.close()
    print('{"ok":true,"adapter_id":"neo4j-contrib-mcp-neo4j","rate_limit":"redacted"}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("fixture", "client"))
    arguments = parser.parse_args()
    fixture() if arguments.mode == "fixture" else client()


if __name__ == "__main__":
    main()
