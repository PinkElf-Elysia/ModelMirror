import assert from "node:assert/strict";
import { once } from "node:events";
import { readFile } from "node:fs/promises";
import http from "node:http";
import test from "node:test";
import {
  MESHY_PROVIDER_ENDPOINT,
  MESHY_PROVIDER_LIMITS,
  MESHY_PROVIDER_MODEL,
  PrototypeAssetPipelineOperationalError,
  createMeshyTextTo3DProvider,
} from "../packages/prototype-asset-pipeline/src/index.mjs";

const apiKey = ["fixture", "credential", "do", "not", "echo"].join("-");

async function startServer(handler) {
  const server = http.createServer(handler);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  return {
    endpoint: `http://127.0.0.1:${address.port}/openapi/v2/text-to-3d`,
    baseUrl: `http://127.0.0.1:${address.port}`,
    async close() {
      server.close();
      await once(server, "close");
    },
  };
}

function json(response, status, value, headers = {}) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    ...headers,
  });
  response.end(body);
}

async function body(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function provider(endpoint, options = {}) {
  return createMeshyTextTo3DProvider({
    endpoint,
    apiKey,
    ...options,
  });
}

function code(result) {
  return result.ok ? null : result.diagnostics[0].code;
}

test("public surface and fixed Meshy identity are minimal", async () => {
  const api = await import("../packages/prototype-asset-pipeline/src/index.mjs");
  assert.deepEqual(Object.keys(api).sort(), [
    "MESHY_PROVIDER_ENDPOINT",
    "MESHY_PROVIDER_LIMITS",
    "MESHY_PROVIDER_MODEL",
    "PrototypeAssetPipelineOperationalError",
    "createMeshyTextTo3DProvider",
  ].sort());
  assert.equal(MESHY_PROVIDER_ENDPOINT, "https://api.meshy.ai/openapi/v2/text-to-3d");
  assert.equal(MESHY_PROVIDER_MODEL, "meshy-6");
  assert.deepEqual(MESHY_PROVIDER_LIMITS, {
    timeoutMs: 120_000,
    responseBytes: 1024 * 1024,
    rawGlbBytes: 128 * 1024 * 1024,
    promptCharacters: 600,
    taskIdCharacters: 128,
  });
  assert.equal(Object.isFrozen(MESHY_PROVIDER_LIMITS), true);
});

test("preview and refine send the exact approved request bodies once", async () => {
  const requests = [];
  const server = await startServer(async (request, response) => {
    requests.push({
      method: request.method,
      url: request.url,
      authorization: request.headers.authorization,
      body: await body(request),
    });
    json(response, 200, { result: `task_${requests.length}` });
  });
  try {
    const client = provider(server.endpoint);
    assert.equal(Object.isFrozen(client), true);
    assert.deepEqual(await client.createPreview({ prompt: "a neutral wooden crate" }), {
      ok: true,
      taskId: "task_1",
    });
    assert.deepEqual(await client.createRefine({ previewTaskId: "task_1" }), {
      ok: true,
      taskId: "task_2",
    });
    assert.equal(requests.length, 2);
    assert.deepEqual(requests[0], {
      method: "POST",
      url: "/openapi/v2/text-to-3d",
      authorization: `Bearer ${apiKey}`,
      body: {
        mode: "preview",
        prompt: "a neutral wooden crate",
        model_type: "standard",
        ai_model: "meshy-6",
        should_remesh: true,
        topology: "triangle",
        target_polycount: 50_000,
        moderation: true,
        target_formats: ["glb"],
      },
    });
    assert.deepEqual(requests[1].body, {
      mode: "refine",
      preview_task_id: "task_1",
      ai_model: "meshy-6",
      texture_resolution: "2k",
      enable_pbr: false,
      remove_lighting: true,
      moderation: true,
      target_formats: ["glb"],
    });
  } finally {
    await server.close();
  }
});

test("task retrieval maps pending, success, and failure without raw response fields", async () => {
  const ignoredSentinel = ["ignored", "response", "detail", "never", "echo"].join("-");
  const statuses = new Map([
    ["pending", { status: "IN_PROGRESS", progress: 40, consumed_credits: 20 }],
    ["failed", { status: "FAILED", progress: 100, task_error: { message: "sensitive upstream detail" }, consumed_credits: 0 }],
  ]);
  const server = await startServer((request, response) => {
    const id = request.url.split("/").at(-1);
    const value = id === "success"
      ? {
          status: "SUCCEEDED",
          progress: 100,
          consumed_credits: 30,
          model_urls: {
            glb: `${server.baseUrl}/assets/model.glb`,
            future_format: `${server.baseUrl}/assets/ignored.bin`,
          },
          prompt: "must not be returned",
          model_type: "standard",
          ai_model: "meshy-6",
          target_polycount: 50_000,
          ignored_future_field: ignoredSentinel,
        }
      : statuses.get(id);
    json(response, 200, value);
  });
  try {
    const client = provider(server.endpoint);
    const pending = await client.getTask({ taskId: "pending" });
    const success = await client.getTask({ taskId: "success" });
    const failed = await client.getTask({ taskId: "failed" });
    assert.deepEqual(pending, {
      ok: true,
      task: { status: "pending", progress: 40, glbUrl: null, consumedCredits: 20 },
    });
    assert.deepEqual(success, {
      ok: true,
      task: {
        status: "succeeded",
        progress: 100,
        glbUrl: `${server.baseUrl}/assets/model.glb`,
        consumedCredits: 30,
      },
    });
    assert.deepEqual(failed, {
      ok: true,
      task: { status: "failed", progress: 100, glbUrl: null, consumedCredits: 0 },
    });
    assert.equal(Object.isFrozen(success), true);
    assert.equal(Object.isFrozen(success.task), true);
    assert.equal(JSON.stringify(failed).includes("upstream"), false);
    assert.equal(JSON.stringify(success).includes(ignoredSentinel), false);
    assert.equal(JSON.stringify(success).includes("future_format"), false);
  } finally {
    await server.close();
  }
});

test("GLB download accepts only approved hosts and enforces byte limits", async () => {
  const bytes = Buffer.from([0x67, 0x6c, 0x54, 0x46, 2, 0, 0, 0]);
  const server = await startServer((request, response) => {
    if (request.url === "/large.glb") {
      response.writeHead(200, {
        "content-length": String(MESHY_PROVIDER_LIMITS.rawGlbBytes + 1),
      });
      response.end();
      return;
    }
    response.writeHead(200, { "content-length": bytes.byteLength });
    response.end(bytes);
  });
  try {
    const client = provider(server.endpoint);
    const downloaded = await client.downloadGlb({ url: `${server.baseUrl}/asset.glb` });
    assert.equal(downloaded.ok, true);
    assert.deepEqual([...downloaded.bytes], [...bytes]);
    assert.equal(
      code(await client.downloadGlb({ url: `${server.baseUrl}/large.glb` })),
      "MESHY_PROVIDER_DOWNLOAD_TOO_LARGE",
    );
    const external = ["http", "://example.invalid/asset.glb"].join("");
    assert.equal(
      code(await client.downloadGlb({ url: external })),
      "MESHY_PROVIDER_DOWNLOAD_URL_INVALID",
    );
    assert.equal(
      code(await client.downloadGlb({ url: ["file", ":///tmp/asset.glb"].join("") })),
      "MESHY_PROVIDER_DOWNLOAD_URL_INVALID",
    );
  } finally {
    await server.close();
  }
});

test("redirect, rate limit, HTTP error, invalid JSON, and oversized JSON are static", async () => {
  let responseMode = "redirect";
  const server = await startServer((_request, response) => {
    if (responseMode === "redirect") {
      response.writeHead(302, { location: "/redirected" });
      response.end();
    } else if (responseMode === "rate") {
      json(response, 429, { private: "rate detail" });
    } else if (responseMode === "error") {
      json(response, 500, { private: "server detail" });
    } else if (responseMode === "invalid") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end("{");
    } else {
      response.writeHead(200, {
        "content-type": "application/json",
        "content-length": String(MESHY_PROVIDER_LIMITS.responseBytes + 1),
      });
      response.end();
    }
  });
  try {
    const client = provider(server.endpoint);
    assert.equal(
      code(await client.createPreview({ prompt: "neutral prop" })),
      "MESHY_PROVIDER_REDIRECT",
    );
    for (const [path, expected] of [
      ["rate", "MESHY_PROVIDER_RATE_LIMITED"],
      ["error", "MESHY_PROVIDER_HTTP_ERROR"],
      ["invalid", "MESHY_PROVIDER_RESPONSE_INVALID"],
      ["large", "MESHY_PROVIDER_RESPONSE_TOO_LARGE"],
    ]) {
      responseMode = path;
      assert.equal(code(await client.createPreview({ prompt: "neutral prop" })), expected);
    }
  } finally {
    await server.close();
  }
});

test("timeout and thrown network faults do not retry or expose causes", async () => {
  let requests = 0;
  const server = await startServer((_request, response) => {
    requests += 1;
    setTimeout(() => json(response, 200, { result: "late" }), 100);
  });
  try {
    const timed = provider(server.endpoint, { timeoutMs: 20 });
    const timeout = await timed.createPreview({ prompt: "neutral prop" });
    assert.equal(code(timeout), "MESHY_PROVIDER_TIMEOUT");
    assert.equal(requests, 1);

  } finally {
    await server.close();
  }
  const closed = await startServer((_request, response) => response.end());
  const broken = provider(closed.endpoint);
  await closed.close();
  const network = await broken.createPreview({ prompt: "neutral prop" });
  assert.equal(code(network), "MESHY_PROVIDER_NETWORK_ERROR");
  assert.equal(JSON.stringify(network).includes(apiKey), false);
});

test("request and response shape violations fail closed before further calls", async () => {
  let requests = 0;
  const server = await startServer((_request, response) => {
    requests += 1;
    json(response, 200, { result: { id: "wrong" } });
  });
  try {
    const client = provider(server.endpoint);
    for (const request of [
      {},
      { prompt: "" },
      { prompt: "x", extra: true },
      { prompt: String.fromCharCode(0xd800) },
      { prompt: "x".repeat(601) },
    ]) {
      assert.equal(
        code(await client.createPreview(request)),
        "MESHY_PROVIDER_REQUEST_INVALID",
      );
    }
    assert.equal(requests, 0);
    assert.equal(
      code(await client.createPreview({ prompt: "neutral prop" })),
      "MESHY_PROVIDER_RESPONSE_INVALID",
    );
    assert.equal(requests, 1);
  } finally {
    await server.close();
  }
});

test("configuration is descriptor-safe and permits only official or loopback endpoint", () => {
  assert.doesNotThrow(() => createMeshyTextTo3DProvider({
    endpoint: MESHY_PROVIDER_ENDPOINT,
    apiKey,
  }));
  for (const endpoint of [
    "http://api.meshy.ai/openapi/v2/text-to-3d",
    "https://api.meshy.ai/openapi/v2/text-to-3d?x=1",
    "https://assets.meshy.ai/openapi/v2/text-to-3d",
    "http://192.0.2.1:8080/openapi/v2/text-to-3d",
  ]) {
    assert.throws(
      () => createMeshyTextTo3DProvider({ endpoint, apiKey }),
      PrototypeAssetPipelineOperationalError,
    );
  }
  let getterCalls = 0;
  const hostile = {};
  Object.defineProperty(hostile, "endpoint", {
    enumerable: true,
    get() { getterCalls += 1; return MESHY_PROVIDER_ENDPOINT; },
  });
  Object.defineProperty(hostile, "apiKey", { enumerable: true, value: apiKey });
  assert.throws(
    () => createMeshyTextTo3DProvider(hostile),
    PrototypeAssetPipelineOperationalError,
  );
  assert.equal(getterCalls, 0);
});

test("provider source is the only network surface and never reads environment", async () => {
  const source = await readFile(new URL("../packages/prototype-asset-pipeline/src/meshy-provider.mjs", import.meta.url), "utf8");
  const index = await readFile(new URL("../packages/prototype-asset-pipeline/src/index.mjs", import.meta.url), "utf8");
  assert.match(source, /fetchImpl/);
  assert.match(source, /redirect: "manual"/);
  assert.equal(source.includes("process.env"), false);
  assert.equal(source.includes("EventSource"), false);
  assert.equal(source.includes("/stream"), false);
  assert.equal(index.includes("fetch"), false);
});
