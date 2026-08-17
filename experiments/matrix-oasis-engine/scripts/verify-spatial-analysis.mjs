import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { deflateSync, gzipSync } from "node:zlib";
import { createGodotEnvironmentAnalyzer, analyzePrototypeEnvironment } from "@matrix-oasis/prototype-environment-analyzer";
import { materializePrototypeSpatialEnvironment } from "@matrix-oasis/prototype-spatial-environment";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { resolveGodotBinary } from "./lib/godot-core.mjs";

const encoder = new TextEncoder();
let failureStage = "initialization";

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function crc32(value) {
  let crc = 0xffffffff;
  for (const byte of value) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBytes = encoder.encode(type);
  const output = new Uint8Array(12 + data.length);
  const view = new DataView(output.buffer);
  view.setUint32(0, data.length, false);
  output.set(typeBytes, 4);
  output.set(data, 8);
  const checked = new Uint8Array(typeBytes.length + data.length);
  checked.set(typeBytes);
  checked.set(data, typeBytes.length);
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
  const output = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0));
  let offset = 0;
  for (const item of chunks) {
    output.set(item, offset);
    offset += item.length;
  }
  return output;
}

function addQuad(positions, indices, a, b, c, d) {
  const start = positions.length / 3;
  positions.push(...a, ...b, ...c, ...d);
  indices.push(start, start + 1, start + 2, start, start + 2, start + 3);
}

function addBox(positions, indices, minimum, maximum) {
  const [x0, y0, z0] = minimum;
  const [x1, y1, z1] = maximum;
  addQuad(positions, indices, [x0, y0, z0], [x0, y0, z1], [x1, y0, z1], [x1, y0, z0]);
  addQuad(positions, indices, [x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]);
  addQuad(positions, indices, [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]);
  addQuad(positions, indices, [x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0]);
  addQuad(positions, indices, [x1, y0, z1], [x0, y0, z1], [x0, y1, z1], [x1, y1, z1]);
  addQuad(positions, indices, [x0, y0, z1], [x0, y0, z0], [x0, y1, z0], [x0, y1, z1]);
}

function roomGeometry(kind) {
  const positions = [];
  const indices = [];
  if (kind === "rectangle") {
    addQuad(positions, indices, [-5, 0, -5], [-5, 0, 5], [5, 0, 5], [5, 0, -5]);
    addBox(positions, indices, [-5.2, 0, -5.2], [-5, 3, 5.2]);
    addBox(positions, indices, [5, 0, -5.2], [5.2, 3, 5.2]);
    addBox(positions, indices, [-5, 0, -5.2], [5, 3, -5]);
    addBox(positions, indices, [-5, 0, 5], [5, 3, 5.2]);
    addBox(positions, indices, [-0.6, 0, -0.6], [0.6, 1.2, 0.6]);
  } else {
    addQuad(positions, indices, [-6, 0, -4], [-6, 0, 4], [1, 0, 4], [1, 0, -4]);
    addQuad(positions, indices, [1, 0, -1.5], [1, 0, 4], [6, 0, 4], [6, 0, -1.5]);
    addBox(positions, indices, [-6.2, 0, -4.2], [-6, 3, 4.2]);
    addBox(positions, indices, [-6, 0, -4.2], [1, 3, -4]);
    addBox(positions, indices, [-6, 0, 4], [6.2, 3, 4.2]);
    addBox(positions, indices, [6, 0, -1.7], [6.2, 3, 4.2]);
    addBox(positions, indices, [1, 0, -1.7], [6.2, 3, -1.5]);
    addBox(positions, indices, [0.8, 0, -4], [1, 3, -0.6]);
    addBox(positions, indices, [0.8, 0, 0.6], [1, 3, 4]);
    addBox(positions, indices, [-3.5, 0, -0.75], [-2.5, 2.4, 0.75]);
    addBox(positions, indices, [3, 2.0, 0.5], [5, 2.2, 3]);
  }
  return { positions, indices };
}

function glbFromGeometry({ positions, indices }) {
  const positionValues = new Float32Array(positions);
  const indexValues = new Uint32Array(indices);
  const binaryLength = positionValues.byteLength + indexValues.byteLength;
  const binary = new Uint8Array(binaryLength);
  binary.set(new Uint8Array(positionValues.buffer));
  binary.set(new Uint8Array(indexValues.buffer), positionValues.byteLength);
  const xs = positions.filter((_, index) => index % 3 === 0);
  const ys = positions.filter((_, index) => index % 3 === 1);
  const zs = positions.filter((_, index) => index % 3 === 2);
  const json = {
    asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: positions.length / 3, type: "VEC3",
        min: [Math.min(...xs), Math.min(...ys), Math.min(...zs)], max: [Math.max(...xs), Math.max(...ys), Math.max(...zs)] },
      { bufferView: 1, componentType: 5125, count: indices.length, type: "SCALAR" },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positionValues.byteLength, target: 34962 },
      { buffer: 0, byteOffset: positionValues.byteLength, byteLength: indexValues.byteLength, target: 34963 },
    ], buffers: [{ byteLength: binaryLength }],
  };
  const encoded = encoder.encode(JSON.stringify(json));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const binaryPadded = Math.ceil(binary.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + binaryPadded);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength);
  output.set(encoded, 20);
  view.setUint32(20 + jsonLength, binaryPadded, true);
  view.setUint32(24 + jsonLength, 0x004e4942, true);
  output.set(binary, 28 + jsonLength);
  return output;
}

function writeInt24(view, offset, value) {
  const normalized = value < 0 ? 0x1000000 + value : value;
  view.setUint8(offset, normalized & 0xff);
  view.setUint8(offset + 1, (normalized >>> 8) & 0xff);
  view.setUint8(offset + 2, (normalized >>> 16) & 0xff);
}

function tinySpz() {
  const points = [[-16, 0, -16], [16, 0, -16], [16, 0, 16], [-16, 0, 16]];
  const raw = new Uint8Array(16 + points.length * 20);
  const view = new DataView(raw.buffer);
  view.setUint32(0, 0x5053474e, true);
  view.setUint32(4, 3, true);
  view.setUint32(8, points.length, true);
  view.setUint8(12, 0);
  view.setUint8(13, 8);
  view.setUint8(14, 0);
  let offset = 16;
  for (const point of points) for (const value of point) {
    writeInt24(view, offset, value);
    offset += 3;
  }
  raw.fill(255, offset, offset + points.length);
  offset += points.length;
  raw.fill(128, offset, offset + points.length * 6);
  offset += points.length * 6;
  raw.fill(0, offset, offset + points.length * 4);
  return new Uint8Array(gzipSync(raw, { level: 9, mtime: 0 }));
}

async function fixture(kind) {
  const id = `spatial-${kind}`;
  const blueprintHash = sha256(encoder.encode(`${id}-blueprint`));
  const collider = glbFromGeometry(roomGeometry(kind));
  const panorama = panoramaPng();
  const environment = canonicalizeJsonValue({
    format: "matrix-oasis.prototype-environment-bundle", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: { id, contentVersion: "1", title: id },
    blueprint: { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0", canonicalSha256: blueprintHash },
    provider: { id: "world-labs-marble", model: "marble-1.1", environmentPromptSha256: sha256(encoder.encode(id)) },
    assets: {
      panorama: { path: "assets/environment-panorama.png", format: "png", width: 2, height: 1,
        byteLength: panorama.byteLength, sha256: sha256(panorama) },
      collider: { path: "assets/environment-collider.glb", format: "glb", byteLength: collider.byteLength,
        sha256: sha256(collider), metrics: { nodeCount: 1, meshCount: 1, surfaceCount: 1,
          triangleCount: roomGeometry(kind).indices.length / 3 } },
    },
  });
  const spatial = await materializePrototypeSpatialEnvironment({
    environmentBundleJson: environment,
    environmentFiles: new Map([
      ["assets/environment-panorama.png", panorama], ["assets/environment-collider.glb", collider],
    ]),
    spzBytes: tinySpz(),
    calibration: { coordinateTransform: "spz-raw-ply-to-godot-v1", metricScaleMicros: 1_000_000,
      groundPlaneOffsetMm: 0, godotTranslationMm: [0, 0, 0], godotRotationMilliDegrees: [0, 0, 0] },
  });
  assert.equal(spatial.ok, true);
  const intent = canonicalizeJsonValue({
    format: "matrix-oasis.prototype-spatial-intent", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: { id, contentVersion: "1" },
    blueprint: { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0", canonicalSha256: blueprintHash },
    runtime: { format: "matrix-oasis.runtime-game-pack", formatVersion: "0.1.0", id, contentVersion: "1",
      sourceSha256: `sha256:${"a".repeat(64)}`, artifactSha256: `sha256:${"b".repeat(64)}` },
    assetBundle: { format: "matrix-oasis.prototype-asset-bundle", formatVersion: "0.1.0",
      canonicalSha256: `sha256:${"c".repeat(64)}` },
    zones: [{ id: "zone-main", adjacentZoneIds: [] }], placements: [],
    nodeContexts: [{ nodeId: "node-entry", zoneId: "zone-main", visiblePlacementIds: [],
      requiresPlayerSpawn: true, requiresActionTerminal: true }],
  });
  return { intent, spatial };
}

async function main() {
  const repeatCount = 20;
  failureStage = "godot-probe";
  const godot = resolveGodotBinary();
  const analyzer = createGodotEnvironmentAnalyzer({ godotBin: godot.command });
  const reports = [];
  for (const kind of ["rectangle", "l-shape"]) {
    failureStage = `${kind}-fixture`;
    const value = await fixture(kind);
    let first = null;
    for (let run = 0; run < repeatCount; run += 1) {
      failureStage = `${kind}-analysis`;
      const result = await analyzePrototypeEnvironment({
        spatialIntentJson: value.intent,
        spatialEnvironmentBundleJson: value.spatial.canonicalBundleJson,
        spatialEnvironmentFiles: new Map(value.spatial.files.map((file) => [file.path, file.bytes])),
      }, analyzer);
      failureStage = `${kind}-result`;
      assert.equal(result.ok, true);
      if (first === null) first = result.canonicalFactsJson;
      else assert.equal(result.canonicalFactsJson, first);
    }
    failureStage = `${kind}-facts`;
    const facts = JSON.parse(first);
    assert.equal(facts.navigationMesh.polygons.length > 0, true);
    assert.equal(facts.navigationMesh.components.length > 0, true);
    assert.equal(facts.floorAnchors.length > 0, true);
    assert.equal(facts.floorAnchors.every((item) => item.capsuleClearanceVerified === true), true);
    reports.push(`${kind}:${facts.navigationMesh.components.length}:${facts.floorAnchors.length}:${facts.wallAnchors.length}`);
  }
  console.log(`SPATIAL_ANALYSIS_OK cases=2 runs=${repeatCount * 2} facts=${reports.join(",")}`);
}

main().catch(() => {
  console.error(`SPATIAL_ANALYSIS_VERIFY_FAILED stage=${failureStage}`);
  process.exitCode = 1;
});
