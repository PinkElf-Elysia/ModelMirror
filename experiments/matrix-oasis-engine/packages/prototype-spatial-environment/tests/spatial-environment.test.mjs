import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { gzipSync, deflateSync } from "node:zlib";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT,
  PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT_VERSION,
  PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS,
  PrototypeSpatialEnvironmentOperationalError,
  materializePrototypeSpatialEnvironment,
  validatePrototypeSpatialEnvironmentBundleJson,
} from "../src/index.mjs";
import { convertSpzToCompressedPly } from "../src/convert.mjs";

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

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

function panoramaPng() {
  const header = new Uint8Array(13);
  const view = new DataView(header.buffer);
  view.setUint32(0, 2, false);
  view.setUint32(4, 1, false);
  header.set([8, 2, 0, 0, 0], 8);
  const chunks = [
    Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10),
    pngChunk("IHDR", header),
    pngChunk("IDAT", new Uint8Array(deflateSync(new Uint8Array(7)))),
    pngChunk("IEND", new Uint8Array()),
  ];
  const result = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0));
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}

function colliderGlb() {
  const json = {
    asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [{ count: 3 }, { count: 3 }], buffers: [{ byteLength: 4 }],
  };
  const encoded = new TextEncoder().encode(JSON.stringify(json));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + 4);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true); view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength); output.set(encoded, 20);
  view.setUint32(20 + jsonLength, 4, true); view.setUint32(24 + jsonLength, 0x004e4942, true);
  return output;
}

function writeInt24(view, offset, value) {
  const normalized = value < 0 ? 0x1000000 + value : value;
  view.setUint8(offset, normalized & 0xff);
  view.setUint8(offset + 1, (normalized >>> 8) & 0xff);
  view.setUint8(offset + 2, (normalized >>> 16) & 0xff);
}

function spz() {
  const count = 3;
  const header = 16;
  const raw = new Uint8Array(header + count * (9 + 1 + 3 + 3 + 4));
  const view = new DataView(raw.buffer);
  view.setUint32(0, 0x5053474e, true);
  view.setUint32(4, 3, true);
  view.setUint32(8, count, true);
  view.setUint8(12, 0);
  view.setUint8(13, 8);
  view.setUint8(14, 0);
  const points = [[-256, 0, 128], [0, 256, 256], [256, 512, 384]];
  let offset = header;
  for (const point of points) {
    for (const value of point) { writeInt24(view, offset, value); offset += 3; }
  }
  raw.fill(255, offset, offset + count); offset += count;
  raw.fill(128, offset, offset + count * 3); offset += count * 3;
  raw.fill(128, offset, offset + count * 3); offset += count * 3;
  raw.fill(0, offset, offset + count * 4);
  return new Uint8Array(gzipSync(raw, { level: 9, mtime: 0 }));
}

function environmentFixture() {
  const panorama = panoramaPng();
  const collider = colliderGlb();
  const environment = {
    format: "matrix-oasis.prototype-environment-bundle",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: "neutral-room", contentVersion: "1", title: "Neutral Room" },
    blueprint: { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0", canonicalSha256: sha256("blueprint") },
    provider: { id: "world-labs-marble", model: "marble-1.1", environmentPromptSha256: sha256("prompt") },
    assets: {
      panorama: { path: "assets/environment-panorama.png", format: "png", width: 2, height: 1, byteLength: panorama.byteLength, sha256: sha256(panorama) },
      collider: { path: "assets/environment-collider.glb", format: "glb", byteLength: collider.byteLength, sha256: sha256(collider), metrics: { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 1 } },
    },
  };
  return {
    json: canonicalizeJsonValue(environment),
    files: new Map([
      ["assets/environment-panorama.png", panorama],
      ["assets/environment-collider.glb", collider],
    ]),
  };
}

function calibration() {
  return {
    coordinateTransform: "spz-raw-ply-to-godot-v1",
    metricScaleMicros: 1_000_000,
    groundPlaneOffsetMm: -125,
    godotTranslationMm: [100, 200, 300],
    godotRotationMilliDegrees: [0, 180_000, 0],
  };
}

async function materialize() {
  const environment = environmentFixture();
  return materializePrototypeSpatialEnvironment({
    environmentBundleJson: environment.json,
    environmentFiles: environment.files,
    spzBytes: spz(),
    calibration: calibration(),
  });
}

test("publishes an exact minimal runtime surface", async () => {
  const exports = await import("../src/index.mjs");
  assert.deepEqual(Object.keys(exports).sort(), [
    "PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT",
    "PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT_VERSION",
    "PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS",
    "PrototypeSpatialEnvironmentOperationalError",
    "materializePrototypeSpatialEnvironment",
    "validatePrototypeSpatialEnvironmentBundleJson",
  ]);
  assert.equal(PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT, "matrix-oasis.prototype-spatial-environment-bundle");
  assert.equal(PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT_VERSION, "0.1.0");
  assert.equal(PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.maxSplats, 2_500_000);
  assert.equal(PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.runtimeSplatTarget, 640_000);
  assert.equal(new PrototypeSpatialEnvironmentOperationalError().message, "PROTOTYPE_SPATIAL_ENVIRONMENT_INTERNAL_ERROR");
});

test("materializes SPZ and inherited collider into a canonical spatial bundle", async () => {
  const result = await materialize();
  assert.equal(result.ok, true);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.bundle.calibration), true);
  assert.equal(result.bundle.assets.splat.numGaussians, 3);
  assert.equal(result.bundle.assets.splat.numLods, 1);
  assert.equal(result.bundle.assets.splat.shBands, 0);
  assert.deepEqual(result.bundle.assets.splat.derivation, {
    profile: "identity-v1",
    targetNumGaussians: 640_000,
    sourceNumGaussians: 3,
    fullResolutionCompressedPly: {
      byteLength: result.bundle.assets.splat.byteLength,
      sha256: result.bundle.assets.splat.sha256,
      numGaussians: 3,
    },
  });
  assert.deepEqual(result.bundle.statistics.sourceBounds, { minimumMm: [-1000, 0, 500], maximumMm: [1000, 2000, 1500] });
  assert.deepEqual(result.bundle.statistics.runtimeRobustBounds, {
    profile: "source-position-percentile-1-99-v1",
    minimumMm: [-1000, 0, 500],
    maximumMm: [1000, 2000, 1500],
  });
  assert.deepEqual(result.bundle.statistics.sourceMeanMm, [0, 1000, 1000]);
  assert.deepEqual(result.bundle.statistics.rendererCenterCompensationMm, [0, 1000, 1000]);
  assert.equal(result.bundle.statistics.sourceInteriorEnvelope, null);
  assert.equal(result.canonicalBundleJson, canonicalizeJsonValue(result.bundle));
  assert.equal(result.files[0].bytes.byteLength, result.bundle.assets.splat.byteLength);
  const files = new Map(result.files.map((file) => [file.path, file.bytes]));
  assert.deepEqual(await validatePrototypeSpatialEnvironmentBundleJson(result.canonicalBundleJson, files), { reportVersion: 1, valid: true, diagnostics: [] });
  const visible = `${result.canonicalBundleJson}${result.canonicalReportJson}`;
  for (const forbidden of ["operation", "world-safe", ["https", "://"].join(""), ["C:", "\\tmp"].join(""), "prompt"]) assert.equal(visible.includes(forbidden), false);
});

test("repeats the synthetic conversion byte-for-byte twenty times", async () => {
  const outputs = [];
  for (let index = 0; index < 20; index += 1) {
    const result = await materialize();
    assert.equal(result.ok, true);
    outputs.push(JSON.stringify({
      bundle: result.canonicalBundleJson,
      report: result.canonicalReportJson,
      splat: sha256(result.files[0].bytes),
      collider: sha256(result.files[1].bytes),
    }));
  }
  assert.equal(new Set(outputs).size, 1);
});

test("derives a deterministic MPMM runtime LOD while retaining full-resolution identity", async () => {
  const limits = {
    ...PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS,
    runtimeSplatTarget: 2,
    decimationMemoryBudgetBytes: 32 * 1024 * 1024,
  };
  const outputs = [];
  for (let index = 0; index < 20; index += 1) {
    const converted = await convertSpzToCompressedPly(spz(), limits);
    assert.equal(converted.ok, true);
    assert.equal(converted.metadata.numGaussians, 3);
    assert.equal(converted.metadata.runtimeNumGaussians, 2);
    assert.equal(converted.metadata.derivation.profile, "mpmm-uniform-v1");
    assert.equal(converted.metadata.derivation.sourceNumGaussians, 3);
    assert.equal(converted.metadata.derivation.targetNumGaussians, 2);
    assert.equal(converted.metadata.derivation.fullResolutionCompressedPly.numGaussians, 3);
    assert.equal(converted.metadata.interiorEnvelope, null);
    outputs.push(`${sha256(converted.bytes)}:${JSON.stringify(converted.metadata.derivation)}`);
  }
  assert.equal(new Set(outputs).size, 1);
});

test("fails closed for malformed input and preserves caller-owned bytes", async () => {
  const environment = environmentFixture();
  const source = spz();
  const before = Uint8Array.prototype.slice.call(source);
  const valid = await materializePrototypeSpatialEnvironment({ environmentBundleJson: environment.json, environmentFiles: environment.files, spzBytes: source, calibration: calibration() });
  assert.equal(valid.ok, true);
  assert.deepEqual(source, before);
  assert.equal((await materializePrototypeSpatialEnvironment({ environmentBundleJson: "{}", environmentFiles: environment.files, spzBytes: source, calibration: calibration() })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_SOURCE_ENVIRONMENT_INVALID");
  assert.equal((await materializePrototypeSpatialEnvironment({ environmentBundleJson: environment.json, environmentFiles: environment.files, spzBytes: Uint8Array.of(1, 2, 3), calibration: calibration() })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_SPZ_INVALID");
  assert.equal((await materializePrototypeSpatialEnvironment({ environmentBundleJson: environment.json, environmentFiles: environment.files, spzBytes: source, calibration: { ...calibration(), metricScaleMicros: 0 } })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_REQUEST_INVALID");
});

test("rejects noncanonical manifests, missing files and changed bytes", async () => {
  const result = await materialize();
  assert.equal(result.ok, true);
  const files = new Map(result.files.map((file) => [file.path, file.bytes]));
  assert.equal((await validatePrototypeSpatialEnvironmentBundleJson(`${result.canonicalBundleJson}\n`, files)).diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_JSON_NON_CANONICAL");
  assert.equal((await validatePrototypeSpatialEnvironmentBundleJson(result.canonicalBundleJson, new Map())).diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_FILES_INVALID");
  const changed = new Map(files);
  changed.set("assets/environment-collider.glb", Uint8Array.of(1));
  assert.equal((await validatePrototypeSpatialEnvironmentBundleJson(result.canonicalBundleJson, changed)).diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_FILE_IDENTITY_MISMATCH");
  const extra = JSON.parse(result.canonicalBundleJson);
  extra.secretKey = "sentinel-secret";
  const report = await validatePrototypeSpatialEnvironmentBundleJson(canonicalizeJsonValue(extra), files);
  assert.equal(report.diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_SCHEMA_INVALID");
  assert.equal(JSON.stringify(report).includes("sentinel-secret"), false);
  assert.equal(JSON.stringify(report).includes("secretKey"), false);
  const unsupported = JSON.parse(result.canonicalBundleJson);
  unsupported.scene.title = String.fromCharCode(0xdfff);
  assert.equal((await validatePrototypeSpatialEnvironmentBundleJson(canonicalizeJsonValue(unsupported), files)).diagnostics[0].code, "PROTOTYPE_SPATIAL_ENVIRONMENT_UNSUPPORTED_TEXT");
});

test("never invokes accessors while capturing the public request", async () => {
  let getterCalls = 0;
  const request = {};
  Object.defineProperty(request, "environmentBundleJson", { enumerable: true, get() { getterCalls += 1; return "{}"; } });
  for (const key of ["environmentFiles", "spzBytes", "calibration"]) Object.defineProperty(request, key, { enumerable: true, value: null });
  const result = await materializePrototypeSpatialEnvironment(request);
  assert.equal(result.ok, false);
  assert.equal(getterCalls, 0);
});
