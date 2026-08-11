"""Deterministic Wave-19A timeout and rate-limit fixture.

This process is used only by the isolated Docker acceptance harness.  It has
no credentials, does not proxy requests, and never logs request data.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class Handler(BaseHTTPRequestHandler):
    server_version = "ModelMirrorWave19AFixture/1"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        parsed = urlsplit(self.path)
        if parsed.path == "/api/v1/status/buildinfo":
            self._json(200, {"status": "success", "data": {"version": "fixture-1"}})
            return
        if parsed.path != "/api/v1/query":
            self._json(404, {"status": "error"})
            return
        query = parse_qs(parsed.query, keep_blank_values=True).get("query", [""])[0]
        if query == "rate_limit_probe":
            self._json(429, {"status": "error"})
            return
        if query == "timeout_probe":
            time.sleep(20)
        self._json(
            200,
            {
                "status": "success",
                "data": {"resultType": "vector", "result": []},
            },
        )


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 9090), Handler).serve_forever()


if __name__ == "__main__":
    main()
