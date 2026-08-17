import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { deflateSync } from "node:zlib";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  MARBLE_PROVIDER_MODEL,
  createMarbleWorldProvider,
  materializePrototypeEnvironmentWithSpatialSource,
  planPrototypeEnvironment,
  validatePrototypeSpatialSourceBundleJson,
} from "../packages/prototype-environment-pipeline/src/index.mjs";

const API_KEY = ["fixture", "credential", "do", "not", "echo"].join("-");

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
  view.setUint32(0, data.length, false); output.set(typeBytes, 4); output.set(data, 8);
  const checked = new Uint8Array(4 + data.length); checked.set(typeBytes); checked.set(data, 4);
  view.setUint32(8 + data.length, crc32(checked), false);
  return output;
}

function panoramaPng() {
  const header = new Uint8Array(13); const view = new DataView(header.buffer);
  view.setUint32(0, 2, false); view.setUint32(4, 1, false); header.set([8, 2, 0, 0, 0], 8);
  const chunks = [
    Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10),
    pngChunk("IHDR", header),
    pngChunk("IDAT", new Uint8Array(deflateSync(new Uint8Array(7)))),
    pngChunk("IEND", new Uint8Array()),
  ];
  const output = new Uint8Array(chunks.reduce((sum, chunk) => sum + chunk.length, 0));
  let offset = 0; for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
  return output;
}

function colliderGlb() {
  const document = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [{ count: 3 }, { count: 3 }], buffers: [{ byteLength: 4 }] };
  const json = new TextEncoder().encode(JSON.stringify(document));
  const padded = Math.ceil(json.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + padded + 8 + 4); const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, output.length, true);
  view.setUint32(12, padded, true); view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + padded); output.set(json, 20);
  view.setUint32(20 + padded, 4, true); view.setUint32(24 + padded, 0x004e4942, true);
  return output;
}

function blueprint() {
  return {
    format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: "neutral-spatial-room", contentVersion: "1", title: "Neutral Spatial Room",
      environmentPrompt: "A quiet neutral stone workshop with an open center.", visualStylePrompt: "Readable prototype materials." },
    zones: [{ id: "zone-main", label: "Main", description: "Central zone" }],
    assetBriefs: [{ id: "asset-environment", kind: "environment", prompt: "Stone workshop", entityId: null,
      roles: ["visual", "collider"] }],
    placements: [],
    nodeBindings: [{ nodeId: "node-entry", zoneId: "zone-main", visiblePlacementIds: [] }],
  };
}

function approval(plan, maxDownloads = 3) {
  return { blueprintSha256: plan.plan.blueprint.canonicalSha256, model: MARBLE_PROVIDER_MODEL,
    maxCreateRequests: 1, maxPollAttempts: 180, maxWorldGets: 1, maxDownloads,
    creditLimit: 1600, usdLimitCents: 150 };
}

async function serverFixture(options = {}) {
  const calls = []; let polls = 0;
  const panorama = panoramaPng(); const collider = colliderGlb();
  const spz = Uint8Array.of(0x53, 0x50, 0x5a, 0x01);
  const server = http.createServer(async (request, response) => {
    for await (const _chunk of request) { /* consume the bounded request */ }
    calls.push({ method: request.method, url: request.url });
    const origin = `http://127.0.0.1:${server.address().port}`;
    response.setHeader("content-type", "application/json");
    if (request.url === "/marble/v1/worlds:generate") {
      response.end(JSON.stringify({ done: false, operation_id: "operation-safe", error: null, metadata: null, response: null }));
    } else if (request.url === "/marble/v1/operations/operation-safe") {
      polls += 1; response.end(JSON.stringify(polls < (options.completeOnPoll ?? 1)
        ? { done: false, operation_id: "operation-safe", error: null, metadata: null, response: null }
        : { done: true, operation_id: "operation-safe", error: null, metadata: { world_id: "world-safe" }, response: { world_id: "world-safe" } }));
    } else if (request.url === "/marble/v1/worlds/world-safe") {
      const host = options.assetHost ?? origin;
      response.end(JSON.stringify({ world: { world_id: "world-safe", model: "marble-1.1", assets: {
        imagery: { pano_url: `${host}/assets/panorama.png` },
        mesh: { collider_mesh_url: `${host}/assets/collider.glb` },
        splats: options.splats ?? { spz_urls: { full_res: `${host}/assets/environment.spz` },
          semantics_metadata: { metric_scale_factor: 1.2345674, ground_plane_offset: -0.1254 } },
      } } }));
    } else if (request.url === "/assets/panorama.png") {
      response.setHeader("content-type", "image/png"); response.end(panorama);
    } else if (request.url === "/assets/collider.glb") {
      response.setHeader("content-type", "model/gltf-binary"); response.end(collider);
    } else if (request.url === "/assets/environment.spz") {
      response.setHeader("content-type", "application/octet-stream"); response.end(spz);
    } else { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const endpoint = `http://127.0.0.1:${server.address().port}/marble/v1`;
  return { calls, endpoint,
    provider: createMarbleWorldProvider({ endpoint, apiKey: API_KEY, allowedAssetHosts: ["127.0.0.1"], timeoutMs: 1000, pollIntervalMs: 0 }),
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())) };
}

test("one Marble world materializes panorama, collider, full-res SPZ and official scale", async (t) => {
  const fixture = await serverFixture({ completeOnPoll: 2 }); t.after(fixture.close);
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));
  const result = await materializePrototypeEnvironmentWithSpatialSource({ plan, approval: approval(plan) }, fixture.provider);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(fixture.calls.map((call) => [call.method, call.url]), [
    ["POST", "/marble/v1/worlds:generate"], ["GET", "/marble/v1/operations/operation-safe"],
    ["GET", "/marble/v1/operations/operation-safe"], ["GET", "/marble/v1/worlds/world-safe"],
    ["GET", "/assets/panorama.png"], ["GET", "/assets/collider.glb"], ["GET", "/assets/environment.spz"],
  ]);
  assert.deepEqual(result.spatialSource.bundle.scale, { metricScaleMicros: 1_234_567, groundPlaneOffsetMm: -125 });
  const files = new Map(result.spatialSource.files.map((file) => [file.path, file.bytes]));
  assert.equal(validatePrototypeSpatialSourceBundleJson(result.spatialSource.canonicalBundleJson, files,
    result.environment.canonicalBundleJson).valid, true);
  for (const sensitive of [API_KEY, "operation-safe", "world-safe", fixture.endpoint]) {
    assert.equal(JSON.stringify(result).includes(sensitive), false);
  }
  assert.equal(Object.isFrozen(result.spatialSource.bundle.scale), true);
});

test("spatial acquisition requires the exact approval before any request", async (t) => {
  const fixture = await serverFixture(); t.after(fixture.close);
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));
  const result = await materializePrototypeEnvironmentWithSpatialSource({ plan, approval: approval(plan, 2) }, fixture.provider);
  assert.equal(result.diagnostics[0].code, "PROTOTYPE_ENVIRONMENT_APPROVAL_INVALID");
  assert.equal(fixture.calls.length, 0);
});

test("missing, malformed, or unapproved official spatial metadata fails closed", async (t) => {
  const fixtures = []; t.after(async () => { for (const fixture of fixtures) await fixture.close(); });
  const plan = planPrototypeEnvironment(canonicalizeJsonValue(blueprint()));
  for (const splats of [
    { spz_urls: {}, semantics_metadata: { metric_scale_factor: 1, ground_plane_offset: 0 } },
    { spz_urls: { full_res: "https://example.invalid/a.spz" }, semantics_metadata: { metric_scale_factor: 1, ground_plane_offset: 0 } },
    { spz_urls: { full_res: "placeholder" }, semantics_metadata: { metric_scale_factor: null, ground_plane_offset: 0 } },
  ]) {
    const fixture = await serverFixture({ splats }); fixtures.push(fixture);
    const result = await materializePrototypeEnvironmentWithSpatialSource({ plan, approval: approval(plan) }, fixture.provider);
    assert.equal(["MARBLE_PROVIDER_SPATIAL_SOURCE_INVALID", "MARBLE_PROVIDER_ASSET_URL_INVALID"].includes(result.diagnostics[0].code), true);
  }
});
