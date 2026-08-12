"""Deterministic local HTTP fixture for the Wave-27 provider-timeout smoke."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FixtureHandler(BaseHTTPRequestHandler):
    request_count = 0
    lock = threading.Lock()

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        if not self.path.startswith("/v1/sql"):
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        if length > 64 * 1024:
            self.send_error(413)
            return
        self.rfile.read(length)
        with self.lock:
            type(self).request_count += 1
            request_number = type(self).request_count
        if request_number > 1:
            time.sleep(20)
        payload = json.dumps(
            {
                "code": 0,
                "output": [
                    {
                        "records": {
                            "schema": {
                                "column_schemas": [{"name": "modelmirror_readonly"}]
                            },
                            "rows": [[1]],
                        }
                    }
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionError):
            return


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 4000), FixtureHandler).serve_forever()


if __name__ == "__main__":
    main()
