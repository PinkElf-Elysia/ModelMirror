import assert from "node:assert/strict";
import test from "node:test";

import { collectProxyResponseHeaders } from "./server-headers.mjs";

test("preserves multiple Set-Cookie headers as separate values", () => {
  const headers = new Headers([
    ["content-type", "application/json"],
    ["set-cookie", "provider=session-a; Path=/api/router; HttpOnly"],
    ["set-cookie", "rag=session-b; Path=/api/rag; HttpOnly"],
    ["transfer-encoding", "chunked"],
  ]);

  assert.deepEqual(collectProxyResponseHeaders(headers), {
    "content-type": "application/json",
    "set-cookie": [
      "provider=session-a; Path=/api/router; HttpOnly",
      "rag=session-b; Path=/api/rag; HttpOnly",
    ],
  });
});
