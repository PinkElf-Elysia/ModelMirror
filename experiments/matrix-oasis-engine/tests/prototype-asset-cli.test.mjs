import assert from "node:assert/strict";
import { once } from "node:events";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  MESHY_PROVIDER_ENDPOINT,
  MESHY_PROVIDER_LIMITS,
  MESHY_PROVIDER_MODEL,
  PrototypeAssetPipelineOperationalError,
  createMeshyTextTo3DProvider,
  materializePrototypeAssetBundle,
  planPrototypeAssets,
} from "../packages/prototype-asset-pipeline/src/index.mjs";
import { normalizePrototypeGlb } from "../packages/prototype-asset-pipeline/src/glb-normalizer.mjs";
import {
  executeMaterializePrototypeAssetsCli,
  executePlanPrototypeAssetsCli,
  parseMaterializePrototypeAssetsArgs,
  parsePlanPrototypeAssetsArgs,
} from "../scripts/lib/prototype-asset-cli-core.mjs";

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
    "materializePrototypeAssetBundle",
    "planPrototypeAssets",
    "validatePrototypeAssetBundleJson",
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

const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const moduleRoot = path.dirname(import.meta.dirname);
const environmentRoot = path.resolve(
  moduleRoot,
  "examples",
  "scene-bundles",
  "kenney-prototype",
  "assets",
);
const fileServices = { lstat, mkdir, mkdtemp, openFile: open, realpath, rename };

async function createAssetCliFixture() {
  const prototypeDir = await mkdtemp(path.join(tempRoot, "matrix-oasis-r9-prototype-"));
  const acquiredDir = await mkdtemp(path.join(tempRoot, "matrix-oasis-r9-acquired-"));
  const output = path.join(tempRoot, `${path.basename(prototypeDir).toLowerCase()}-output`);
  const authoring = JSON.parse(await readFile(
    path.join(moduleRoot, "examples", "mechanics-conformance.authoring-game-pack.json"),
    "utf8",
  ));
  const authoringGamePackJson = canonicalizeJsonValue(authoring);
  const compiled = await compileAuthoringGamePackJson(authoringGamePackJson);
  assert.equal(compiled.ok, true);
  const placementIds = ["place-room", "place-crate", "place-guide"];
  const sceneBlueprintJson = canonicalizeJsonValue({
    format: "matrix-oasis.scene-blueprint",
    formatVersion: "0.1.0",
    scene: {
      id: authoring.id,
      contentVersion: authoring.contentVersion,
      title: authoring.title,
      environmentPrompt: "A neutral enclosed validation room.",
      visualStylePrompt: "Simple neutral geometry.",
    },
    zones: [{ id: "zone-main", label: "Main", description: "Validation zone." }],
    assetBriefs: [
      { id: "room", kind: "environment", prompt: "Neutral room.", entityId: null, roles: ["visual", "collider"] },
      { id: "crate", kind: "prop", prompt: "Neutral crate.", entityId: "control-unit", roles: ["visual", "collider"] },
      { id: "guide", kind: "character-placeholder", prompt: "Static neutral guide.", entityId: "actor-unit", roles: ["visual"] },
    ],
    placements: [
      { id: placementIds[0], assetBriefId: "room", zoneId: "zone-main", entityId: null },
      { id: placementIds[1], assetBriefId: "crate", zoneId: "zone-main", entityId: "control-unit" },
      { id: placementIds[2], assetBriefId: "guide", zoneId: "zone-main", entityId: "actor-unit" },
    ],
    nodeBindings: authoring.nodes.map((node) => ({
      nodeId: node.id,
      zoneId: "zone-main",
      visiblePlacementIds: placementIds,
    })),
  });
  const prototypeFiles = {
    "authoring-game-pack.json": authoringGamePackJson,
    "scene-blueprint.json": sceneBlueprintJson,
    "runtime-game-pack.json": compiled.canonicalJson,
    "runtime-receipt.json": canonicalizeJsonValue(compiled.receipt),
  };
  for (const [name, text] of Object.entries(prototypeFiles)) {
    await writeFile(path.join(prototypeDir, name), text);
  }
  const source = await readFile(path.join(environmentRoot, "crate.glb"));
  const texture = await readFile(path.join(environmentRoot, "Textures", "colormap.png"));
  const embedded = await normalizePrototypeGlb(source, {
    kind: "prop",
    role: "visual",
    externalResources: new Map([["Textures/colormap.png", texture]]),
  });
  assert.equal(embedded.ok, true);
  await writeFile(path.join(acquiredDir, "crate.glb"), embedded.bytes);
  await writeFile(path.join(acquiredDir, "guide.glb"), embedded.bytes);
  return { prototypeDir, acquiredDir, output };
}

async function createNestedAcquiredFixture() {
  const fixture = await createAssetCliFixture();
  const qualificationRoot = await mkdtemp(path.join(tempRoot, "matrix-oasis-r9-qualification-"));
  const nestedAcquiredDir = path.join(qualificationRoot, "acquired");
  await mkdir(nestedAcquiredDir);
  for (const name of await readdir(fixture.acquiredDir)) {
    await writeFile(
      path.join(nestedAcquiredDir, name),
      await readFile(path.join(fixture.acquiredDir, name)),
    );
  }
  return {
    ...fixture,
    qualificationRoot,
    acquiredDir: nestedAcquiredDir,
    originalAcquiredDir: fixture.acquiredDir,
  };
}

async function removeFixture(fixture) {
  for (const candidate of [
    fixture.output,
    fixture.qualificationRoot,
    fixture.originalAcquiredDir,
    fixture.acquiredDir,
    fixture.prototypeDir,
  ]) {
    if (!candidate) continue;
    await rm(candidate, { recursive: true, force: true });
  }
  const prefix = `.matrix-oasis-r9-${path.basename(fixture.output)}-`;
  for (const name of await readdir(tempRoot)) {
    if (name.startsWith(prefix)) {
      await rm(path.join(tempRoot, name), { recursive: true, force: true });
    }
  }
}

function materializeCliRequest(fixture, services = fileServices) {
  return executeMaterializePrototypeAssetsCli({
    args: [
      "--prototype-dir", fixture.prototypeDir,
      "--acquired-dir", fixture.acquiredDir,
      "--output", fixture.output,
    ],
    tempRoot,
    services,
    environmentRoot,
    planPrototypeAssets,
    materializePrototypeAssetBundle,
  });
}

test("asset CLI arguments are closed and do not accept path omissions", () => {
  assert.equal(parsePlanPrototypeAssetsArgs(["--prototype-dir", "x"]).prototypeDir, "x");
  const materializeArgs = parseMaterializePrototypeAssetsArgs([
    "--prototype-dir", "a", "--acquired-dir", "b", "--output", "c",
  ]);
  assert.deepEqual(
    [materializeArgs.prototypeDir, materializeArgs.acquiredDir, materializeArgs.output],
    ["a", "b", "c"],
  );
  for (const args of [[], ["--other", "x"], ["--prototype-dir"], ["--prototype-dir", "x", "--prototype-dir", "y"]]) {
    assert.throws(() => parsePlanPrototypeAssetsArgs(args));
  }
});

test("asset planning and materialization publish only a complete canonical pair and GLBs", async () => {
  const fixture = await createAssetCliFixture();
  try {
    const planned = await executePlanPrototypeAssetsCli({
      args: ["--prototype-dir", fixture.prototypeDir],
      tempRoot,
      services: fileServices,
      planPrototypeAssets,
    });
    assert.equal(planned.exitCode, 0, planned.stderr);
    const publicPlan = JSON.parse(planned.stdout);
    assert.deepEqual(publicPlan.assetBriefs.map(({ id }) => id), ["room", "crate", "guide"]);
    assert.equal(planned.stdout.includes("Neutral crate"), false);

    const materialized = await materializeCliRequest(fixture);
    assert.equal(materialized.exitCode, 0, materialized.stderr);
    const bundleText = await readFile(path.join(fixture.output, "prototype-asset-bundle.json"), "utf8");
    const reportText = await readFile(path.join(fixture.output, "generation-report.json"), "utf8");
    assert.equal(canonicalizeJsonValue(JSON.parse(bundleText)), bundleText);
    assert.equal(canonicalizeJsonValue(JSON.parse(reportText)), reportText);
    for (const name of ["room-floor-square.glb", "room-wall.glb", "crate-visual.glb", "crate-collider.glb", "guide-visual.glb"]) {
      assert.ok((await readFile(path.join(fixture.output, "assets", name))).byteLength > 0);
    }
  } finally {
    await removeFixture(fixture);
  }
});

test("materialization accepts the qualification nested acquired directory", async () => {
  const fixture = await createNestedAcquiredFixture();
  try {
    const materialized = await materializeCliRequest(fixture);
    assert.equal(materialized.exitCode, 0, materialized.stderr);
    assert.ok((await readFile(path.join(fixture.output, "prototype-asset-bundle.json"))).byteLength > 0);
  } finally {
    await removeFixture(fixture);
  }
});

test("materialization fails closed for malformed acquired GLB and existing targets", async () => {
  const fixture = await createAssetCliFixture();
  try {
    await writeFile(path.join(fixture.acquiredDir, "crate.glb"), new Uint8Array([1, 2, 3]));
    const rejected = await materializeCliRequest(fixture);
    assert.equal(rejected.exitCode, 1);
    await assert.rejects(readFile(path.join(fixture.output, "prototype-asset-bundle.json")));
    await writeFile(
      path.join(fixture.acquiredDir, "crate.glb"),
      await readFile(path.join(fixture.acquiredDir, "guide.glb")),
    );
    await writeFile(path.join(fixture.output), "existing");
    const existing = await materializeCliRequest(fixture);
    assert.equal(existing.exitCode, 2);
    assert.equal(await readFile(fixture.output, "utf8"), "existing");
  } finally {
    await removeFixture(fixture);
  }
});

test("concurrent publication has exactly one winner and never exposes a partial pair", async () => {
  const fixture = await createAssetCliFixture();
  try {
    const results = await Promise.all([
      materializeCliRequest(fixture),
      materializeCliRequest(fixture),
    ]);
    assert.deepEqual(results.map(({ exitCode }) => exitCode).sort(), [0, 2]);
    assert.ok((await readFile(path.join(fixture.output, "prototype-asset-bundle.json"))).byteLength > 0);
    assert.ok((await readFile(path.join(fixture.output, "generation-report.json"))).byteLength > 0);
  } finally {
    await removeFixture(fixture);
  }
});

test("post-rename target identity replacement is reported as failure", async () => {
  const fixture = await createAssetCliFixture();
  const held = `${fixture.output}-held`;
  try {
    const services = {
      ...fileServices,
      async rename(source, target) {
        await rename(source, target);
        await rename(target, held);
        await mkdir(target);
      },
    };
    const rejected = await materializeCliRequest(fixture, services);
    assert.equal(rejected.exitCode, 2);
    assert.deepEqual(await readdir(fixture.output), []);
    assert.ok((await readFile(path.join(held, "prototype-asset-bundle.json"))).byteLength > 0);
  } finally {
    await rm(held, { recursive: true, force: true });
    await removeFixture(fixture);
  }
});
