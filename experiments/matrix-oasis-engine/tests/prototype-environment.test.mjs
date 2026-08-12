import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import http from "node:http";
import test from "node:test";
import { deflateSync } from "node:zlib";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  MARBLE_PROVIDER_MODEL,
  createMarbleWorldProvider,
  materializePrototypeEnvironment,
  planPrototypeEnvironment,
  validatePrototypeEnvironmentBundleJson,
} from "../packages/prototype-environment-pipeline/src/index.mjs";

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBytes = new TextEncoder().encode(type);
  const output = new Uint8Array(12 + data.length);
  const view = new DataView(output.buffer);
  view.setUint32(0, data.length, false);
  output.set(typeBytes, 4);
  output.set(data, 8);
  const checked = new Uint8Array(4 + data.length);
  checked.set(typeBytes);
  checked.set(data, 4);
  view.setUint32(8 + data.length, crc32(checked), false);
  return output;
}

function panoramaPng(width = 2, height = 1) {
  const header = new Uint8Array(13);
  const view = new DataView(header.buffer);
  view.setUint32(0, width, false);
  view.setUint32(4, height, false);
  header.set([8, 2, 0, 0, 0], 8);
  const scanlines = new Uint8Array(height * (1 + width * 3));
  const compressed = new Uint8Array(deflateSync(scanlines));
  const chunks = [
    Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10),
    pngChunk("IHDR", header),
    pngChunk("IDAT", compressed),
    pngChunk("IEND", new Uint8Array()),
  ];
  const output = new Uint8Array(chunks.reduce((sum, value) => sum + value.length, 0));
  let offset = 0;
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
  return output;
}

function glb() {
  const json = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [{ count: 3 }, { count: 3 }],
    buffers: [{ byteLength: 4 }],
  };
  const encoded = new TextEncoder().encode(JSON.stringify(json));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + 4);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength);
  output.set(encoded, 20);
  view.setUint32(20 + jsonLength, 4, true);
  view.setUint32(24 + jsonLength, 0x004e4942, true);
  return output;
}

function blueprint(overrides = {}) {
  return {
    format: "matrix-oasis.scene-blueprint",
    formatVersion: "0.1.0",
    scene: {
      id: "neutral-room",
      contentVersion: "1",
      title: "Neutral Room",
      environmentPrompt: "A quiet neutral stone workshop with an open center.",
      visualStylePrompt: "Readable prototype materials.",
    },
    zones: [{ id: "zone-main", label: "Main", description: "Central zone" }],
    assetBriefs: [
      { id: "asset-environment", kind: "environment", prompt: "Stone workshop", entityId: null, roles: ["visual", "collider"] },
    ],
    placements: [],
    nodeBindings: [{ nodeId: "node-entry", zoneId: "zone-main", visiblePlacementIds: [] }],
    ...overrides,
  };
}

function approval(plan) {
  return {
    blueprintSha256: plan.plan.blueprint.canonicalSha256,
    model: MARBLE_PROVIDER_MODEL,
    maxCreateRequests: 1,
    maxPollAttempts: 180,
    maxWorldGets: 1,
    maxDownloads: 2,
    creditLimit: 1600,
    usdLimitCents: 150,
  };
}

async function serverFixture(options = {}) {
  const calls = [];
  const panorama = options.panorama ?? panoramaPng();
  const collider = options.collider ?? glb();
  let polls = 0;
  const server = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString("utf8");
    calls.push({ method: request.method, url: request.url, body, credentialHeader: request.headers["wlt-api-key"] });
    const origin = `http://127.0.0.1:${server.address().port}`;
    if (request.url === "/marble/v1/worlds:generate") {
      if (options.delayCreateMs) await new Promise((resolve) => setTimeout(resolve, options.delayCreateMs));
      if (options.createStatus) { response.writeHead(options.createStatus); response.end(); return; }
      if (options.createLengthHeader) response.setHeader("content-length", options.createLengthHeader);
      response.setHeader("content-type", "application/json");
      response.end(options.createResponse ?? JSON.stringify({ done: false, operation_id: "operation-safe", error: null, metadata: null, response: null }));
    } else if (request.url === "/marble/v1/operations/operation-safe") {
      polls += 1;
      response.setHeader("content-type", "application/json");
      response.end(JSON.stringify(options.neverComplete || polls < (options.completeOnPoll ?? 1)
        ? { done: false, operation_id: "operation-safe", error: null, metadata: null, response: null }
        : { done: true, operation_id: "operation-safe", error: null, metadata: { world_id: "world-safe" }, response: { world_id: "world-safe" } }));
    } else if (request.url === "/marble/v1/worlds/world-safe") {
      response.setHeader("content-type", "application/json");
      const host = options.assetHost ?? origin;
      response.end(JSON.stringify({ world: { world_id: "world-safe", model: "marble-1.1", assets: { imagery: { pano_url: `${host}/assets/panorama.png` }, mesh: { collider_mesh_url: `${host}/assets/collider.glb` } } } }));
    } else if (request.url === "/assets/panorama.png") {
      if (options.panoramaLengthHeader) response.setHeader("content-length", options.panoramaLengthHeader);
      response.setHeader("content-type", "image/png"); response.end(panorama);
    } else if (request.url === "/assets/collider.glb") {
      response.setHeader("content-type", "model/gltf-binary"); response.end(collider);
    } else { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const endpoint = `http://127.0.0.1:${server.address().port}/marble/v1`;
  return {
    calls,
    endpoint,
    provider: createMarbleWorldProvider({ endpoint, apiKey: "example", allowedAssetHosts: ["127.0.0.1"], timeoutMs: options.timeoutMs ?? 1000, pollIntervalMs: 0 }),
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
}

test("plans one canonical text environment with an exact approval-bound provider prompt", () => {
  const text = canonicalizeJsonValue(blueprint());
  const result = planPrototypeEnvironment(text);
  assert.equal(result.ok, true);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(result.plan.environmentPrompt.includes(blueprint().scene.environmentPrompt), true);
  assert.equal(result.plan.environmentPrompt.includes(blueprint().scene.visualStylePrompt), true);
  assert.equal(result.plan.environmentPrompt.includes("complete seamless 360-degree view"), true);
  assert.equal(result.plan.environmentPrompt.includes("one static character"), true);
  assert.equal(result.plan.environmentPrompt.length <= 2000, true);
  assert.match(result.plan.blueprint.canonicalSha256, /^sha256:[0-9a-f]{64}$/u);
  assert.equal(result.plan.environmentPromptSha256,
    `sha256:${createHash("sha256").update(result.plan.environmentPrompt).digest("hex")}`);

  assert.equal(planPrototypeEnvironment(`${text}\n`).ok, false);
  assert.equal(planPrototypeEnvironment(canonicalizeJsonValue(blueprint({ assetBriefs: [] }))).diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_BLUEPRINT_SCHEMA_INVALID");
  assert.equal(planPrototypeEnvironment(canonicalizeJsonValue(blueprint({ assetBriefs: [
    { id: "asset-a", kind: "environment", prompt: "A", entityId: null, roles: ["visual", "collider"] },
    { id: "asset-b", kind: "environment", prompt: "B", entityId: null, roles: ["visual", "collider"] },
  ] }))).diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_BLUEPRINT_SEMANTIC_INVALID");
  const invalidText = blueprint(); invalidText.scene.environmentPrompt = String.fromCharCode(0xd800);
  assert.equal(planPrototypeEnvironment(canonicalizeJsonValue(invalidText)).diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_UNSUPPORTED_TEXT");
  const invalidTitle = blueprint(); invalidTitle.scene.title = String.fromCharCode(0xdfff);
  assert.equal(planPrototypeEnvironment(canonicalizeJsonValue(invalidTitle)).diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_UNSUPPORTED_TEXT");
  const oversized = blueprint();
  oversized.scene.environmentPrompt = "x".repeat(1000);
  oversized.scene.visualStylePrompt = "y".repeat(1000);
  assert.equal(planPrototypeEnvironment(canonicalizeJsonValue(oversized)).diagnostics[0].code,
    "PROTOTYPE_ENVIRONMENT_PROMPT_PROFILE_UNSUPPORTED");
  assert.throws(() => createMarbleWorldProvider({
    endpoint: "https://api.worldlabs.ai/marble/v1",
    apiKey: "example",
    allowedAssetHosts: ["unapproved.example"],
  }), { code: "PROTOTYPE_ENVIRONMENT_PIPELINE_INTERNAL_ERROR" });
  assert.doesNotThrow(() => createMarbleWorldProvider({
    endpoint: "https://api.worldlabs.ai/marble/v1",
    apiKey: "example",
    allowedAssetHosts: ["cdn.marble.worldlabs.ai"],
  }));
  assert.throws(() => createMarbleWorldProvider({
    endpoint: "https://api.worldlabs.ai/marble/v1",
    apiKey: "example",
    allowedAssetHosts: ["*.worldlabs.ai"],
  }), { code: "PROTOTYPE_ENVIRONMENT_PIPELINE_INTERNAL_ERROR" });
});

test("materializes the bounded text-only Marble flow and publishes only safe canonical evidence", async (t) => {
  const fixture = await serverFixture({ completeOnPoll: 2 });
  t.after(fixture.close);
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));
  const result = await materializePrototypeEnvironment({ plan, approval: approval(plan) }, fixture.provider);
  assert.equal(result.ok, true);
  assert.deepEqual(fixture.calls.map((call) => [call.method, call.url]), [
    ["POST", "/marble/v1/worlds:generate"],
    ["GET", "/marble/v1/operations/operation-safe"],
    ["GET", "/marble/v1/operations/operation-safe"],
    ["GET", "/marble/v1/worlds/world-safe"],
    ["GET", "/assets/panorama.png"],
    ["GET", "/assets/collider.glb"],
  ]);
  const createBody = JSON.parse(fixture.calls[0].body);
  assert.deepEqual(createBody, {
    display_name: "matrix-oasis-prototype-environment",
    model: "marble-1.1",
    world_prompt: { type: "text", text_prompt: plan.plan.environmentPrompt },
    permission: { allow_id_access: false, allowed_readers: [], allowed_writers: [], public: false },
  });
  assert.equal(fixture.calls.every((call) => call.credentialHeader === "example" || call.url.startsWith("/assets/")), true);
  const visible = JSON.stringify({ bundle: result.bundle, report: JSON.parse(result.canonicalReportJson) });
  for (const secret of ["example", "operation-safe", "world-safe", "quiet neutral", fixture.endpoint]) assert.equal(visible.includes(secret), false);
  const files = new Map(result.files.map((file) => [file.path, file.bytes]));
  assert.deepEqual(validatePrototypeEnvironmentBundleJson(result.canonicalBundleJson, files), { reportVersion: 1, valid: true, diagnostics: [] });
});

test("approval mismatch makes zero provider requests", async (t) => {
  const fixture = await serverFixture();
  t.after(fixture.close);
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));
  const invalid = { ...approval(plan), creditLimit: 1601 };
  const result = await materializePrototypeEnvironment({ plan, approval: invalid }, fixture.provider);
  assert.equal(result.diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_APPROVAL_INVALID");
  assert.equal(fixture.calls.length, 0);
});

test("rejects unapproved asset hosts, redirects, credit errors, and malformed assets", async (t) => {
  const fixtures = [];
  t.after(async () => { for (const fixture of fixtures) await fixture.close(); });
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));

  const hostile = await serverFixture({ assetHost: "https://127.0.0.1" }); fixtures.push(hostile);
  assert.equal((await materializePrototypeEnvironment({ plan, approval: approval(plan) }, hostile.provider)).diagnostics[0].code, "MARBLE_PROVIDER_ASSET_URL_INVALID");

  const redirected = await serverFixture({ createStatus: 302 }); fixtures.push(redirected);
  assert.equal((await materializePrototypeEnvironment({ plan, approval: approval(plan) }, redirected.provider)).diagnostics[0].code, "MARBLE_PROVIDER_REDIRECT");

  const credits = await serverFixture({ createStatus: 402 }); fixtures.push(credits);
  assert.equal((await materializePrototypeEnvironment({ plan, approval: approval(plan) }, credits.provider)).diagnostics[0].code, "MARBLE_PROVIDER_CREDIT_LIMIT");

  const badPng = await serverFixture({ panorama: panoramaPng(3, 1) }); fixtures.push(badPng);
  assert.equal((await materializePrototypeEnvironment({ plan, approval: approval(plan) }, badPng.provider)).diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_PANORAMA_DIMENSIONS_INVALID");

  const invalidGlb = glb(); invalidGlb[0] = 0;
  const badGlb = await serverFixture({ collider: invalidGlb }); fixtures.push(badGlb);
  assert.equal((await materializePrototypeEnvironment({ plan, approval: approval(plan) }, badGlb.provider)).diagnostics[0].code, "SCENE_PACK_GLB_INVALID");
});

test("provider fails closed on rate limits, timeouts, oversized and malformed responses, downloads, and poll exhaustion", async (t) => {
  const fixtures = [];
  t.after(async () => { for (const fixture of fixtures) await fixture.close(); });
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));
  const run = async (options) => {
    const fixture = await serverFixture(options); fixtures.push(fixture);
    return { fixture, result: await materializePrototypeEnvironment({ plan, approval: approval(plan) }, fixture.provider) };
  };

  assert.equal((await run({ createStatus: 429 })).result.diagnostics[0].code, "MARBLE_PROVIDER_RATE_LIMITED");
  assert.equal((await run({ delayCreateMs: 100, timeoutMs: 10 })).result.diagnostics[0].code, "MARBLE_PROVIDER_TIMEOUT");
  assert.equal((await run({ createLengthHeader: 1024 * 1024 + 1 })).result.diagnostics[0].code, "MARBLE_PROVIDER_RESPONSE_TOO_LARGE");
  assert.equal((await run({ createResponse: "not-json" })).result.diagnostics[0].code, "MARBLE_PROVIDER_RESPONSE_INVALID");
  assert.equal((await run({ panoramaLengthHeader: 64 * 1024 * 1024 + 1 })).result.diagnostics[0].code, "MARBLE_PROVIDER_DOWNLOAD_TOO_LARGE");
  const exhausted = await run({ neverComplete: true });
  assert.equal(exhausted.result.diagnostics[0].code, "MARBLE_PROVIDER_POLL_LIMIT");
  assert.equal(exhausted.fixture.calls.filter((call) => call.url.includes("/operations/")).length, 180);
});

test("bundle validation is closed, canonical, identity-bound, and deterministic", async (t) => {
  const fixture = await serverFixture();
  t.after(fixture.close);
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));
  const results = [];
  for (let index = 0; index < 20; index += 1) {
    const result = await materializePrototypeEnvironment({ plan, approval: approval(plan) }, fixture.provider);
    assert.equal(result.ok, true);
    results.push([result.canonicalBundleJson, result.canonicalReportJson, ...result.files.map((file) => createHash("sha256").update(file.bytes).digest("hex"))]);
  }
  assert.equal(new Set(results.map((value) => JSON.stringify(value))).size, 1);
  const result = await materializePrototypeEnvironment({ plan, approval: approval(plan) }, fixture.provider);
  const files = new Map(result.files.map((file) => [file.path, file.bytes]));
  assert.equal(validatePrototypeEnvironmentBundleJson(`${result.canonicalBundleJson}\n`, files).diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_JSON_NON_CANONICAL");
  const extra = JSON.parse(result.canonicalBundleJson); extra.provider.leaked = "sensitive-property-name";
  const schema = validatePrototypeEnvironmentBundleJson(canonicalizeJsonValue(extra), files);
  assert.equal(schema.diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_SCHEMA_INVALID");
  assert.equal(JSON.stringify(schema).includes("sensitive-property-name"), false);
  const changed = new Map(files); const panorama = new Uint8Array(changed.get("assets/environment-panorama.png")); panorama[panorama.length - 1] ^= 1; changed.set("assets/environment-panorama.png", panorama);
  assert.equal(validatePrototypeEnvironmentBundleJson(result.canonicalBundleJson, changed).valid, false);
  assert.equal(validatePrototypeEnvironmentBundleJson(result.canonicalBundleJson, new Map()).diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_FILES_INVALID");
});
