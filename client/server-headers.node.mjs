import assert from "node:assert/strict";
import test from "node:test";
import { createServer, request } from "node:http";
import { once } from "node:events";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { gzipSync, gunzipSync } from "node:zlib";

import { collectProxyResponseHeaders } from "./server-headers.mjs";
import {
  isMetaPlannerGenerationRequest, META_PLANNER_GENERATION_TIMEOUT_MS,
  proxyMetaPlannerGeneration,
} from "./server-meta-planner-proxy.mjs";

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

const generationPath = "/api/meta-agent/generate-xpert-candidate";

async function listen(server) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return `http://127.0.0.1:${server.address().port}`;
}

function close(server) {
  return new Promise((resolve) => {
    server.close(resolve);
    server.closeAllConnections();
  });
}

function exchange(url, body = "{}", options = {}) {
  return new Promise((resolve, reject) => {
    const req = request(url, { method: "POST", agent: false, ...options }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("error", reject);
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on("error", reject);
    req.end(body);
  });
}

async function fixture(t, handler, timeoutMs = 1000) {
  const receipts = [];
  let requests = 0;
  const upstream = createServer((req, res) => {
    requests += 1;
    Promise.resolve(handler(req, res)).catch(() => res.destroy());
  });
  const upstreamUrl = await listen(upstream);
  t.after(() => close(upstream));
  const proxy = createServer((req, res) => {
    const headers = { ...req.headers };
    for (const key of ["host", "connection", "content-length", "transfer-encoding"]) delete headers[key];
    void proxyMetaPlannerGeneration(req, res, new URL(req.url, upstreamUrl), headers, {
      timeoutMs, onReceipt: (receipt) => receipts.push(receipt),
    });
  });
  const proxyUrl = await listen(proxy);
  t.after(() => close(proxy));
  return { url: proxyUrl + generationPath, receipts, requests: () => requests };
}

test("only the candidate generation POST receives the bounded long window", () => {
  assert.equal(META_PLANNER_GENERATION_TIMEOUT_MS, 960_000);
  assert.equal(isMetaPlannerGenerationRequest({ method: "POST", url: generationPath }), true);
  assert.equal(isMetaPlannerGenerationRequest({ method: "POST", url: generationPath + "?sample=1" }), true);
  for (const url of ["/api/chat", "/api/meta-agent/capabilities", "/api/meta-agent/generate-workflow", generationPath + "/extra"]) {
    assert.equal(isMetaPlannerGenerationRequest({ method: "POST", url }), false);
  }
  assert.equal(isMetaPlannerGenerationRequest({ method: "GET", url: generationPath }), false);
});

test("forwards body, compressed bytes, status, cookies and a server-owned correlation ID", async (t) => {
  let received;
  const packed = gzipSync('{"proposal_id":"synthetic","validation":{"valid":false}}');
  const f = await fixture(t, async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    received = { body: Buffer.concat(chunks).toString(), headers: req.headers };
    res.writeHead(201, {
      "Content-Type": "application/json", "Content-Encoding": "gzip", "Content-Length": packed.length,
      "Set-Cookie": ["a=1; HttpOnly", "b=2; HttpOnly"],
    });
    res.end(packed);
  });
  const body = JSON.stringify({ goal: "合成测试目标", scope: { allowed_node_kinds: [] } });
  const result = await exchange(f.url, body, { headers: {
    "content-type": "application/json", "x-meta-planner-proxy-id": "forged",
  } });
  assert.equal(result.status, 201);
  assert.equal(received.body, body);
  assert.equal(result.headers["content-encoding"], "gzip");
  assert.equal(result.headers["content-length"], String(packed.length));
  assert.deepEqual(result.headers["set-cookie"], ["a=1; HttpOnly", "b=2; HttpOnly"]);
  assert.deepEqual(result.body, packed);
  assert.equal(JSON.parse(gunzipSync(result.body)).validation.valid, false);
  const id = result.headers["x-meta-planner-proxy-id"];
  assert.match(id, /^[0-9a-f-]{36}$/);
  assert.equal(received.headers["x-meta-planner-proxy-id"], id);
  assert.equal(f.receipts.length, 1);
  assert.equal(f.receipts[0].request_id, id);
  assert.equal(f.receipts[0].state, "completed");
  assert.equal(f.requests(), 1);
});

test("keeps upstream validation errors and never follows redirects or redispatches", async (t) => {
  for (const status of [409, 422, 500, 307]) {
    const f = await fixture(t, (_req, res) => {
      res.writeHead(status, { "Content-Type": "application/json", Location: "/must-not-follow" });
      res.end('{"detail":"合成校验失败"}');
    });
    const result = await exchange(f.url);
    assert.equal(result.status, status);
    assert.equal(JSON.parse(result.body).detail, "合成校验失败");
    assert.equal(f.requests(), 1);
    assert.equal(f.receipts[0].state, "completed");
    assert.equal(f.receipts[0].upstream_status, status);
  }
});

test("total deadline closes one dispatch and returns only a safe unknown-outcome receipt", async (t) => {
  let upstreamClosed = false;
  const f = await fixture(t, (_req, res) => {
    res.on("close", () => { upstreamClosed = true; });
  }, 100);
  const result = await exchange(f.url + "?secret=must-not-leak", "private-test-body");
  assert.equal(result.status, 504);
  const data = JSON.parse(result.body);
  assert.equal(data.code, "META_PLANNER_PROXY_TIMEOUT");
  assert.equal(data.cause, "TOTAL_DEADLINE");
  assert.equal(data.generation_outcome, "unknown");
  assert.equal(data.retryable, false);
  await delay(50);
  assert.equal(upstreamClosed, true);
  assert.equal(f.requests(), 1);
  assert.equal(f.receipts.length, 1);
  assert.equal(f.receipts[0].state, "timeout");
  for (const forbidden of ["must-not-leak", "private-test-body", "127.0.0.1", "http:", "stack"]) {
    assert.equal(JSON.stringify([data, f.receipts]).includes(forbidden), false);
  }
});

test("connection failure is 502 with safe cause, not raw fetch failure", async (t) => {
  const unavailable = createServer();
  const upstreamUrl = await listen(unavailable);
  await close(unavailable);
  const receipts = [];
  const proxy = createServer((req, res) => {
    void proxyMetaPlannerGeneration(req, res, new URL(generationPath, upstreamUrl), {}, {
      onReceipt: (receipt) => receipts.push(receipt),
    });
  });
  const url = await listen(proxy);
  t.after(() => close(proxy));
  const result = await exchange(url + generationPath);
  const data = JSON.parse(result.body);
  assert.equal(result.status, 502);
  assert.equal(data.code, "META_PLANNER_PROXY_UPSTREAM_FAILED");
  assert.equal(data.cause, "ECONNREFUSED");
  assert.equal(data.retryable, false);
  assert.equal(JSON.stringify([data, receipts]).includes(upstreamUrl), false);
  assert.equal(receipts.length, 1);
});

test("browser disconnect cancels the connection without a second upstream request", async (t) => {
  let ready;
  const dispatched = new Promise((resolve) => { ready = resolve; });
  let upstreamClosed = false;
  const f = await fixture(t, (_req, res) => {
    res.on("close", () => { upstreamClosed = true; });
    ready();
  });
  const req = request(f.url, { method: "POST", agent: false });
  req.on("error", () => {});
  req.end("{}");
  await dispatched;
  req.destroy();
  await delay(80);
  assert.equal(upstreamClosed, true);
  assert.equal(f.receipts.length, 1);
  assert.equal(f.receipts[0].state, "client_disconnected");
  assert.equal(f.requests(), 1);
});

test("a partial response cannot be presented as completed JSON", async (t) => {
  const f = await fixture(t, (_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json", "Content-Length": "300" });
    res.write('{"proposal_id":');
    setTimeout(() => res.destroy(), 40);
  });
  await assert.rejects(exchange(f.url));
  assert.equal(f.receipts.length, 1);
  assert.equal(f.receipts[0].state, "upstream_error");
  assert.equal(f.requests(), 1);
});

test("the deadline is total, not reset by continued response chunks", async (t) => {
  const f = await fixture(t, (_req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.write("{");
    const ticker = setInterval(() => res.write(" "), 10);
    res.once("close", () => clearInterval(ticker));
  }, 100);
  await assert.rejects(exchange(f.url));
  assert.equal(f.receipts.length, 1);
  assert.equal(f.receipts[0].state, "timeout");
  assert.equal(f.requests(), 1);
});

test("request and response streaming preserve a large bounded body", async (t) => {
  const body = "x".repeat(2 * 1024 * 1024);
  const f = await fixture(t, async (req, res) => {
    let count = 0;
    for await (const chunk of req) count += chunk.length;
    res.writeHead(200, { "Content-Type": "text/plain", "X-Received-Bytes": String(count) });
    res.end(body);
  }, 5000);
  const result = await exchange(f.url, body);
  assert.equal(result.headers["x-received-bytes"], String(body.length));
  assert.equal(result.body.toString(), body);
  assert.equal(f.requests(), 1);
});

const slowDelayMs = process.env.MODELMIRROR_PROXY_SLOW_TEST === "1" ? 342_400 : 80;
test(`actual server generation route survives ${slowDelayMs} ms before headers`, { timeout: slowDelayMs + 20_000 }, async (t) => {
  let generationRequests = 0;
  const ordinaryRequests = [];
  const rpgRequests = [];
  let child;
  let dir;
  const upstream = createServer((req, res) => {
    req.resume();
    if (req.url !== generationPath) {
      ordinaryRequests.push(req.url);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"health":"synthetic"}');
      return;
    }
    generationRequests += 1;
    const timer = setTimeout(() => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"proposal_id":"synthetic","validation":{"valid":false}}');
    }, slowDelayMs);
    res.once("close", () => clearTimeout(timer));
  });
  const upstreamUrl = await listen(upstream);
  const rpgUpstream = createServer((req, res) => {
    req.resume();
    rpgRequests.push(req.url);
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end('{"upstream":"rpg"}');
  });
  const rpgUpstreamUrl = await listen(rpgUpstream);
  t.after(async () => {
    // Windows cannot remove a directory while it is a running process's cwd.
    if (child && child.exitCode === null && child.signalCode === null) {
      const exited = once(child, "exit");
      assert.equal(child.kill(), true);
      await exited;
    }
    await close(upstream);
    await close(rpgUpstream);
    if (dir) {
      assert.ok(path.resolve(dir).startsWith(path.join(path.resolve(tmpdir()), "mm-proxy-test-")));
      await rm(dir, { recursive: true, force: true });
    }
  });
  const reservation = createServer();
  const clientUrl = await listen(reservation);
  const port = reservation.address().port;
  await close(reservation);
  dir = await mkdtemp(path.join(tmpdir(), "mm-proxy-test-"));
  await mkdir(path.join(dir, "dist"));
  await writeFile(path.join(dir, "dist", "index.html"), "<title>模镜合成代理验收</title>");
  child = spawn(process.execPath, [fileURLToPath(new URL("./server.mjs", import.meta.url))], {
    cwd: dir, env: {
      PORT: String(port), API_TARGET: upstreamUrl, RPG_TARGET: rpgUpstreamUrl,
      SystemRoot: process.env.SystemRoot,
    },
    windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  let errors = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { errors += chunk; });
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Synthetic proxy startup timeout")), 8000);
    child.once("error", reject);
    child.once("exit", () => { clearTimeout(timer); reject(new Error("Synthetic proxy exited")); });
    const check = () => {
      if (output.includes(`listening on ${port}`)) {
        clearTimeout(timer);
        child.stdout.off("data", check);
        resolve();
      }
    };
    child.stdout.on("data", check);
    check();
  });
  const health = await exchange(clientUrl + "/api/health", "", { method: "GET" });
  assert.equal(health.status, 200);
  assert.equal(health.headers["x-meta-planner-proxy-id"], undefined);
  const repairPath = "/api/meta-agent/authoring/proposals/synthetic/repair/preflight";
  const repair = await exchange(clientUrl + repairPath);
  assert.equal(repair.status, 200);
  assert.equal(repair.headers["x-meta-planner-proxy-id"], undefined);
  const rpg = await exchange(clientUrl + "/rpg-app/health", "", { method: "GET" });
  assert.equal(rpg.status, 200);
  assert.equal(JSON.parse(rpg.body).upstream, "rpg");
  assert.equal(rpg.headers["x-meta-planner-proxy-id"], undefined);
  assert.deepEqual(rpgRequests, ["/rpg-app/health"]);
  assert.deepEqual(ordinaryRequests, ["/api/health", repairPath]);
  const started = performance.now();
  const result = await exchange(clientUrl + generationPath);
  const elapsed = performance.now() - started;
  assert.equal(result.status, 200);
  assert.equal(JSON.parse(result.body).proposal_id, "synthetic");
  assert.equal(JSON.parse(result.body).validation.valid, false);
  assert.ok(elapsed >= slowDelayMs);
  assert.equal(generationRequests, 1);
  assert.match(result.headers["x-meta-planner-proxy-id"], /^[0-9a-f-]{36}$/);
  assert.equal(errors, "");
  const dockerfile = await readFile(new URL("./Dockerfile", import.meta.url), "utf8");
  assert.match(dockerfile, /^COPY server-meta-planner-proxy\.mjs \.$/m);
});
