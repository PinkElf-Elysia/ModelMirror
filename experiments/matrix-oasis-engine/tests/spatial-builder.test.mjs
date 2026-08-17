import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { createHash } from "node:crypto";
import {
  lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir, writeFile,
} from "node:fs/promises";
import path from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import { deflateSync, gzipSync } from "node:zlib";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { materializePrototypeSpatialEnvironment } from "@matrix-oasis/prototype-spatial-environment";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  SpatialCacheOperationalError,
  findVerifiedSpatialPrototypeRun,
  importSpatialPrototypeCache,
  loadVerifiedSpatialPrototypeRun,
  parseSpatialCacheArguments,
  publishSpatialPrototypeRun,
  recoverSpatialPrototypeRuns,
} from "../scripts/lib/spatial-cache-core.mjs";
import {
  SPATIAL_PROTOTYPE_HOST_MARKER,
  SPATIAL_PROTOTYPE_READY_MARKER,
  createSpatialPrototypeOperations,
  copySpatialPreviewFiles,
  parseSpatialPreviewArguments,
  spatialPrototypeGodotArguments,
} from "../scripts/preview-spatial-prototype.mjs";

const TEMP_ROOT = path.resolve(path.parse(process.cwd()).root, "tmp");
const RUN_ID = `${"a".repeat(64)}-${"b".repeat(64)}`;
const PROMPT_HASH = `sha256:${"c".repeat(64)}`;
const MODEL = "fixture-model";
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });
const encoder = new TextEncoder();

function hash(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function colliderGlb() {
  const positions = new Float32Array([
    -1, -1, -2, 1, -1, -2, 1, -1, 2, -1, -1, 2,
    -1, 1, -2, 1, 1, -2, 1, 1, 2, -1, 1, 2,
  ]);
  const indices = new Uint16Array([
    0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
    0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5,
    2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7,
  ]);
  const binary = new Uint8Array(positions.byteLength + indices.byteLength);
  binary.set(new Uint8Array(positions.buffer)); binary.set(new Uint8Array(indices.buffer), positions.byteLength);
  const json = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 8, type: "VEC3", min: [-1, -1, -2], max: [1, 1, 2] },
      { bufferView: 1, componentType: 5123, count: 36, type: "SCALAR" },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positions.byteLength, target: 34962 },
      { buffer: 0, byteOffset: positions.byteLength, byteLength: indices.byteLength, target: 34963 },
    ],
    buffers: [{ byteLength: binary.byteLength }] };
  const encoded = encoder.encode(JSON.stringify(json));
  const padded = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + padded + 8 + binary.byteLength);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, output.length, true);
  view.setUint32(12, padded, true); view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + padded); output.set(encoded, 20);
  view.setUint32(20 + padded, binary.byteLength, true); view.setUint32(24 + padded, 0x004e4942, true);
  output.set(binary, 28 + padded);
  return output;
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
  const output = new Uint8Array(12 + data.length); const view = new DataView(output.buffer);
  view.setUint32(0, data.length, false); output.set(typeBytes, 4); output.set(data, 8);
  const checked = new Uint8Array(typeBytes.length + data.length); checked.set(typeBytes); checked.set(data, typeBytes.length);
  view.setUint32(8 + data.length, crc32(checked), false);
  return output;
}

function panoramaPng() {
  const header = new Uint8Array(13); const view = new DataView(header.buffer);
  view.setUint32(0, 2, false); view.setUint32(4, 1, false); header.set([8, 2, 0, 0, 0], 8);
  const chunks = [Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10), pngChunk("IHDR", header),
    pngChunk("IDAT", new Uint8Array(deflateSync(new Uint8Array(7)))), pngChunk("IEND", new Uint8Array())];
  const output = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0));
  let offset = 0; for (const item of chunks) { output.set(item, offset); offset += item.length; }
  return output;
}

function writeInt24(view, offset, value) {
  const normalized = value < 0 ? 0x1000000 + value : value;
  view.setUint8(offset, normalized & 0xff); view.setUint8(offset + 1, (normalized >>> 8) & 0xff);
  view.setUint8(offset + 2, (normalized >>> 16) & 0xff);
}

function spz() {
  const points = [];
  for (const coordinate of [-162, -160]) {
    for (let index = 0; index < 64; index += 1) points.push([coordinate, 20, 0]);
  }
  for (const coordinate of [160, 162]) {
    for (let index = 0; index < 64; index += 1) points.push([coordinate, 20, 0]);
  }
  for (const coordinate of [-162, -160]) {
    for (let index = 0; index < 64; index += 1) points.push([0, 20, coordinate]);
  }
  for (const coordinate of [160, 162]) {
    for (let index = 0; index < 64; index += 1) points.push([0, 20, coordinate]);
  }
  for (let index = 0; index < 16; index += 1) points.push([0, 0, 0]);
  const count = points.length; const raw = new Uint8Array(16 + count * 20); const view = new DataView(raw.buffer);
  view.setUint32(0, 0x5053474e, true); view.setUint32(4, 3, true); view.setUint32(8, count, true);
  view.setUint8(12, 0); view.setUint8(13, 8); view.setUint8(14, 0);
  let offset = 16;
  for (const point of points) for (const value of point) { writeInt24(view, offset, value); offset += 3; }
  raw.fill(255, offset, offset + count); offset += count;
  raw.fill(128, offset, offset + count * 3); offset += count * 3;
  raw.fill(128, offset, offset + count * 3); offset += count * 3;
  raw.fill(0, offset, offset + count * 4);
  return new Uint8Array(gzipSync(raw, { level: 9, mtime: 0 }));
}

function authoring() {
  return { format: "matrix-oasis.authoring-game-pack", formatVersion: "0.1.0", id: "spatial-fixture",
    contentVersion: "1", language: "en", title: "Spatial Fixture", entryNodeId: "node-entry",
    entities: [], variables: [], cues: [], nodes: [{ id: "node-entry", title: "Entry", entityIds: [], entryCueIds: [],
      actions: [{ id: "action-finish", label: "Finish", effects: [], target: { kind: "ending", id: "ending-finish" } }] }],
    endings: [{ id: "ending-finish", title: "Finish", cueIds: [] }] };
}

async function writeBytes(root, relative, bytes) {
  const candidate = path.join(root, ...relative.split("/"));
  await mkdir(path.dirname(candidate), { recursive: true });
  await writeFile(candidate, bytes);
}

async function fixture(options = {}) {
  const root = await mkdtemp(path.join(TEMP_ROOT, "matrix-oasis-r11-builder-"));
  const prototypeRunRoot = path.join(root, "prototype-runs");
  const spatialEnvironmentDir = path.join(root, "spatial-source");
  const spatialRunRoot = path.join(root, "spatial-runs-root");
  const sourceRun = path.join(prototypeRunRoot, "runs", RUN_ID);
  await mkdir(path.join(sourceRun, "assets"), { recursive: true });
  const authoringText = canonicalizeJsonValue(authoring());
  const compiled = await compileAuthoringGamePackJson(authoringText); assert.equal(compiled.ok, true);
  const runtimeText = compiled.canonicalJson;
  const receiptText = canonicalizeJsonValue(compiled.receipt);
  const blueprintText = canonicalizeJsonValue({ format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: "spatial-fixture", contentVersion: "1", title: "Spatial Fixture", environmentPrompt: "Neutral room", visualStylePrompt: "Neutral" },
    zones: [{ id: "zone-main", label: "Main", description: "Main" }],
    assetBriefs: [{ id: "asset-environment", kind: "environment", prompt: "Neutral room", entityId: null, roles: ["visual", "collider"] }],
    placements: [{ id: "placement-environment", assetBriefId: "asset-environment", zoneId: "zone-main", entityId: null }],
    nodeBindings: [{ nodeId: "node-entry", zoneId: "zone-main", visiblePlacementIds: ["placement-environment"] }] });
  const collider = colliderGlb(); const colliderHash = hash(collider);
  const scene = { format: "matrix-oasis.scene-pack", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: "spatial-fixture", contentVersion: "1", title: "Spatial Fixture" },
    runtimeIdentity: { runtimeFormat: compiled.runtimePack.format, runtimeFormatVersion: compiled.runtimePack.formatVersion,
      packId: compiled.runtimePack.source.id, packContentVersion: compiled.runtimePack.source.contentVersion,
      sourceCanonicalSha256: compiled.runtimePack.source.canonicalSha256, artifactSha256: compiled.receipt.artifact.sha256 },
    assets: [{ id: "environment-collider", roles: ["visual", "collider"], path: "assets/environment-collider.glb",
      format: "glb", byteLength: collider.length, sha256: colliderHash.slice(7) }],
    placements: [{ id: "environment-placement", visualAssetId: "environment-collider", colliderAssetId: "environment-collider",
      entityId: null, transform: { positionMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0], scalePermille: [1000, 1000, 1000] } }],
    nodeBindings: [{ nodeId: "node-entry", playerSpawn: { positionMm: [0, 1000, 2000], yawMilliDegrees: 0 },
      actionAnchor: { positionMm: [0, 0, 0], yawMilliDegrees: 0 }, visiblePlacementIds: ["environment-placement"] }] };
  const sceneText = canonicalizeJsonValue(scene);
  const panorama = panoramaPng();
  const environment = { format: "matrix-oasis.prototype-environment-bundle", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: { ...scene.scene },
    blueprint: { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0", canonicalSha256: hash(blueprintText) },
    provider: { id: "world-labs-marble", model: "marble-1.1", environmentPromptSha256: hash("Neutral room") },
    assets: { panorama: { path: "assets/environment-panorama.png", format: "png", width: 2, height: 1,
      byteLength: panorama.length, sha256: hash(panorama) }, collider: { path: "assets/environment-collider.glb", format: "glb",
      byteLength: collider.length, sha256: colliderHash, metrics: { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 12 } } } };
  const environmentText = canonicalizeJsonValue(environment);
  const spatial = await materializePrototypeSpatialEnvironment({ environmentBundleJson: environmentText,
    environmentFiles: new Map([["assets/environment-panorama.png", panorama], ["assets/environment-collider.glb", collider]]),
    spzBytes: spz(), calibration: { coordinateTransform: "spz-raw-ply-to-godot-v1",
      metricScaleMicros: options.assemblyProfile === "matrix-oasis.prototype-assembly/2" ? 3_000_000 : 1_000_000,
      groundPlaneOffsetMm: 0, godotTranslationMm: [0, 0, 0], godotRotationMilliDegrees: [0, 0, 0] } });
  assert.equal(spatial.ok, true);
  const assemblyReportText = canonicalizeJsonValue({ reportVersion: 1,
    profile: options.assemblyProfile ?? "matrix-oasis.prototype-assembly/1",
    inputs: { sceneBlueprintSha256: hash(blueprintText), prototypeEnvironmentBundleSha256: hash(environmentText) },
    environment: { colliderSha256: colliderHash }, output: { scenePackSha256: hash(sceneText) } });
  const texts = new Map([
    ["authoring-game-pack.json", authoringText], ["scene-blueprint.json", blueprintText],
    ["runtime-game-pack.json", runtimeText], ["runtime-receipt.json", receiptText],
    ["generation-report.json", canonicalizeJsonValue({ model: MODEL })],
    ["prototype-asset-bundle.json", "{}"], ["prototype-asset-report.json", "{}"],
    ["scene-pack.json", sceneText], ["assembly-report.json", assemblyReportText],
    ["run-report.json", canonicalizeJsonValue({ format: "matrix-oasis.prototype-run-report", formatVersion: "0.1.0",
      status: "ready", source: "verified-cache", runId: RUN_ID, promptSha256: PROMPT_HASH, scenePackSha256: hash(sceneText) })],
  ]);
  for (const [name, text] of texts) await writeBytes(sourceRun, name, encoder.encode(text));
  await writeBytes(sourceRun, "assets/environment-collider.glb", collider);
  await mkdir(path.join(spatialEnvironmentDir, "assets"), { recursive: true });
  await writeBytes(spatialEnvironmentDir, "prototype-spatial-environment-bundle.json", encoder.encode(spatial.canonicalBundleJson));
  await writeBytes(spatialEnvironmentDir, "prototype-spatial-environment-report.json", encoder.encode(spatial.canonicalReportJson));
  for (const file of spatial.files) await writeBytes(spatialEnvironmentDir, file.path, file.bytes);
  const recoverPrototypeRuns = async () => Object.freeze({ currentRunId: RUN_ID,
    runs: Object.freeze([Object.freeze({ runId: RUN_ID, promptSha256: PROMPT_HASH, model: MODEL })]) });
  const dependencies = { temporaryRoot: root, services, recoverPrototypeRuns, assemblePrototypeScene,
    assemblePrototypeSpatialScene, canonicalizeJsonValue };
  const spatialAssemblyRequest = {
    assemblyReportJson: assemblyReportText,
    scenePackJson: sceneText,
    runtimeGamePackJson: runtimeText,
    runtimeReceiptJson: receiptText,
    spatialEnvironmentBundleJson: spatial.canonicalBundleJson,
    spatialEnvironmentFiles: new Map(spatial.files.map((file) => [file.path, file.bytes])),
  };
  return { root, prototypeRunRoot, spatialEnvironmentDir, spatialRunRoot, sourceRun, dependencies,
    spatial, spatialAssemblyRequest };
}

function importArgs(value) {
  return ["--prototype-run-root", value.prototypeRunRoot, "--prototype-run-id", RUN_ID,
    "--spatial-environment-dir", value.spatialEnvironmentDir, "--spatial-run-root", value.spatialRunRoot];
}

test("spatial CLI surfaces and Godot arguments are exact and panorama-free", () => {
  const prototypeRoot = path.join(TEMP_ROOT, "prototype"); const spatialRoot = path.join(TEMP_ROOT, "spatial");
  assert.deepEqual({ ...parseSpatialCacheArguments(["--prototype-run-root", prototypeRoot, "--prototype-run-id", RUN_ID,
    "--spatial-environment-dir", path.join(TEMP_ROOT, "environment"), "--spatial-run-root", spatialRoot]) },
  { prototypeRunRoot: prototypeRoot, prototypeRunId: RUN_ID, spatialEnvironmentDir: path.join(TEMP_ROOT, "environment"), spatialRunRoot: spatialRoot });
  assert.deepEqual({ ...parseSpatialPreviewArguments(["--prototype-run-root", prototypeRoot, "--spatial-run-root", spatialRoot]) },
    { prototypeRunRoot: prototypeRoot, spatialRunRoot: spatialRoot });
  assert.equal(SPATIAL_PROTOTYPE_HOST_MARKER, "MATRIX_OASIS_R11_SPATIAL_PROTOTYPE_HOST");
  assert.equal(SPATIAL_PROTOTYPE_READY_MARKER, "MATRIX_OASIS_R11_SPATIAL_READY");
  const args = spatialPrototypeGodotArguments({ projectRoot: path.join(TEMP_ROOT, "project"), runDirectory: path.join(TEMP_ROOT, "run"), smoke: true });
  assert.equal(args.includes("res://spatial_prototype/spatial_lab.tscn"), true);
  assert.equal(args.some((item) => item.includes("panorama")), false);
  assert.equal(args.at(-1), "--matrix-oasis-spatial-smoke");
  const qualificationArgs = spatialPrototypeGodotArguments({ projectRoot: path.join(TEMP_ROOT, "project"),
    runDirectory: path.join(TEMP_ROOT, "run"), qualification: true });
  assert.equal(qualificationArgs.at(-1), "--matrix-oasis-spatial-qualification");
  const captureArgs = spatialPrototypeGodotArguments({ projectRoot: path.join(TEMP_ROOT, "project"),
    runDirectory: path.join(TEMP_ROOT, "run"), capture: true });
  assert.equal(captureArgs.at(-1), "--matrix-oasis-spatial-capture");
  assert.throws(() => spatialPrototypeGodotArguments({ projectRoot: path.join(TEMP_ROOT, "project"),
    runDirectory: path.join(TEMP_ROOT, "run"), smoke: true, qualification: true }), /SPATIAL_HOST_GODOT_ARGUMENT_INVALID/u);
  assert.equal(typeof copySpatialPreviewFiles, "function");
});

test("imports an isolated spatial overlay and rejects drift without modifying the R10 run", async () => {
  const value = await fixture();
  try {
    const before = await readdir(value.sourceRun);
    const imported = await importSpatialPrototypeCache({ args: importArgs(value), ...value.dependencies });
    assert.deepEqual(imported, { runId: RUN_ID, cacheHit: true, files: 6 });
    assert.deepEqual((await readdir(path.join(value.spatialRunRoot, "spatial-runs", RUN_ID))).sort(), [
      "assets", "prototype-spatial-environment-bundle.json", "prototype-spatial-environment-report.json",
      "run-report.json", "spatial-assembly-report.json", "spatial-assembly.json",
    ]);
    assert.deepEqual(await readdir(value.sourceRun), before);
    const overlayText = await readFile(path.join(value.spatialRunRoot, "spatial-runs", RUN_ID, "run-report.json"), "utf8");
    assert.equal(overlayText.includes("panorama"), true);
    assert.equal(overlayText.includes("environment-panorama.png"), false);
    const recovered = await recoverSpatialPrototypeRuns({ runRoot: value.spatialRunRoot,
      prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies });
    assert.equal(recovered.currentRunId, RUN_ID); assert.equal(recovered.runs.length, 1);
    assert.deepEqual(await findVerifiedSpatialPrototypeRun({ promptSha256: PROMPT_HASH, model: MODEL,
      runRoot: value.spatialRunRoot, prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies }), { ok: true, runId: RUN_ID });
    const loaded = await loadVerifiedSpatialPrototypeRun({ runId: RUN_ID, runRoot: value.spatialRunRoot,
      prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies });
    assert.equal(loaded.previewFiles.has("assets/environment.compressed.ply"), true);
    assert.equal([...loaded.previewFiles.keys()].some((name) => name.includes("panorama")), false);
    const overlayReportPath = path.join(value.spatialRunRoot, "spatial-runs", RUN_ID, "run-report.json");
    const overlayReport = JSON.parse(await readFile(overlayReportPath, "utf8"));
    await writeFile(overlayReportPath, canonicalizeJsonValue({ ...overlayReport, promptSha256: `sha256:${"d".repeat(64)}` }));
    assert.deepEqual(await recoverSpatialPrototypeRuns({ runRoot: value.spatialRunRoot,
      prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies }), { currentRunId: null, runs: [] });
    await writeFile(overlayReportPath, canonicalizeJsonValue(overlayReport));
    const sourceReportPath = path.join(value.sourceRun, "run-report.json");
    const sourceReport = JSON.parse(await readFile(sourceReportPath, "utf8"));
    await writeFile(sourceReportPath, canonicalizeJsonValue({ ...sourceReport, promptSha256: `sha256:${"e".repeat(64)}` }));
    assert.deepEqual(await recoverSpatialPrototypeRuns({ runRoot: value.spatialRunRoot,
      prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies }), { currentRunId: null, runs: [] });
    await writeFile(sourceReportPath, canonicalizeJsonValue(sourceReport));
    await writeFile(path.join(value.spatialRunRoot, "spatial-runs", RUN_ID, "assets", "environment.compressed.ply"), Uint8Array.of(1));
    const drifted = await recoverSpatialPrototypeRuns({ runRoot: value.spatialRunRoot,
      prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies });
    assert.deepEqual(drifted, { currentRunId: null, runs: [] });
  } finally {
    await rm(value.root, { recursive: true, force: true });
  }
});

test("imports and recovers a v2 spatial overlay without changing v1 cache semantics", async () => {
  const value = await fixture({ assemblyProfile: "matrix-oasis.prototype-assembly/2" });
  try {
    const direct = await assemblePrototypeSpatialScene(value.spatialAssemblyRequest, {
      profile: "matrix-oasis.prototype-spatial-assembly/2",
    });
    assert.equal(direct.ok, true, JSON.stringify(direct));
    const imported = await importSpatialPrototypeCache({ args: importArgs(value), ...value.dependencies });
    assert.deepEqual(imported, { runId: RUN_ID, cacheHit: true, files: 6 });
    const overlayRoot = path.join(value.spatialRunRoot, "spatial-runs", RUN_ID);
    const report = JSON.parse(await readFile(path.join(overlayRoot, "spatial-assembly-report.json"), "utf8"));
    const assembly = JSON.parse(await readFile(path.join(overlayRoot, "spatial-assembly.json"), "utf8"));
    assert.equal(report.profile, "matrix-oasis.prototype-spatial-assembly/2");
    assert.equal(report.alignment.placementLayoutProfile, "collider-agent-zone-constraint-v2");
    assert.deepEqual(assembly.transforms.placementLayout, []);
    const recovered = await recoverSpatialPrototypeRuns({ runRoot: value.spatialRunRoot,
      prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies });
    assert.equal(recovered.currentRunId, RUN_ID);
    assert.deepEqual(recovered.runs.map((run) => run.runId), [RUN_ID]);
  } finally {
    await rm(value.root, { recursive: true, force: true });
  }
});

test("publishes an in-memory spatial materialization without an intermediate directory", async () => {
  const value = await fixture({ assemblyProfile: "matrix-oasis.prototype-assembly/2" });
  try {
    const published = await publishSpatialPrototypeRun({
      prototypeRunRoot: value.prototypeRunRoot,
      prototypeRunId: RUN_ID,
      spatialRunRoot: value.spatialRunRoot,
      spatialMaterialization: {
        canonicalBundleJson: value.spatial.canonicalBundleJson,
        canonicalReportJson: value.spatial.canonicalReportJson,
        files: value.spatial.files,
      },
      ...value.dependencies,
    });
    assert.deepEqual(published, { runId: RUN_ID, cacheHit: false, files: 6 });
    const recovered = await recoverSpatialPrototypeRuns({ runRoot: value.spatialRunRoot,
      prototypeRunRoot: value.prototypeRunRoot, ...value.dependencies });
    assert.equal(recovered.currentRunId, RUN_ID);
    assert.deepEqual(recovered.runs.map((run) => run.runId), [RUN_ID]);
    await assert.rejects(() => publishSpatialPrototypeRun({
      prototypeRunRoot: value.prototypeRunRoot, prototypeRunId: RUN_ID,
      spatialRunRoot: value.spatialRunRoot,
      spatialMaterialization: { canonicalBundleJson: value.spatial.canonicalBundleJson,
        canonicalReportJson: value.spatial.canonicalReportJson,
        files: value.spatial.files.map((file, index) => index === 0 ? { ...file, bytes: Uint8Array.of(1) } : file) },
      ...value.dependencies,
    }), (error) => error.code === "SPATIAL_CACHE_INPUT_INVALID" || error.code === "SPATIAL_CACHE_RUN_EXISTS");
  } finally {
    await rm(value.root, { recursive: true, force: true });
  }
});

test("preview operations are cache-only and publish verified files through an imported project", async () => {
  const projectRoot = await mkdtemp(path.join(TEMP_ROOT, "matrix-oasis-r11-preview-project-"));
  const temporaryRoot = await mkdtemp(path.join(TEMP_ROOT, "matrix-oasis-r11-preview-owner-"));
  const child = new EventEmitter(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.exitCode = null; child.signalCode = null; child.kill = () => { child.exitCode = 0; child.emit("exit", 0); };
  const files = new Map([
    ["runtime-game-pack.json", encoder.encode("runtime")], ["runtime-receipt.json", encoder.encode("receipt")],
    ["scene-pack.json", encoder.encode("scene")], ["spatial-assembly.json", encoder.encode("assembly")],
    ["assets/environment-collider.glb", Uint8Array.of(1)], ["assets/environment.compressed.ply", Uint8Array.of(2)],
  ]);
  const calls = { configured: 0, imported: 0, removed: 0 };
  const operations = createSpatialPrototypeOperations({ prototypeRunRoot: path.join(TEMP_ROOT, "prototype"),
    spatialRunRoot: path.join(TEMP_ROOT, "spatial"), godot: { command: "godot" }, tempRoot: TEMP_ROOT,
    cache: { findVerifiedSpatialPrototypeRun: async () => ({ ok: true, runId: RUN_ID }),
      recoverSpatialPrototypeRuns: async () => ({ currentRunId: RUN_ID, runs: [{ runId: RUN_ID, promptSha256: PROMPT_HASH, model: MODEL }] }),
      loadVerifiedSpatialPrototypeRun: async () => ({ runId: RUN_ID, previewFiles: files }) },
    godotTools: { createRuntimePreviewProject: () => ({ projectRoot, temporaryRoot, identity: { dev: 1n, ino: 1n } }),
      removeRuntimePreviewProject: () => { calls.removed += 1; },
      configureGdgsProject: () => { calls.configured += 1; },
      runGodotCommand: () => { calls.imported += 1; return ""; }, assertGodotOutputClean: () => {} },
    spawnProcess: () => { queueMicrotask(() => child.stdout.write(`${SPATIAL_PROTOTYPE_READY_MARKER}\n`)); return child; } });
  try {
    assert.deepEqual(await operations.findCache({ promptSha256: PROMPT_HASH, model: MODEL }), { ok: true, runId: RUN_ID });
    assert.equal((await operations.generate()).diagnostics[0].code, "SPATIAL_HOST_OFFLINE_CACHE_ONLY");
    assert.deepEqual(await operations.launch({ runId: RUN_ID }), { ok: true });
    assert.deepEqual({ configured: calls.configured, imported: calls.imported }, { configured: 1, imported: 1 });
    assert.equal(await readFile(path.join(projectRoot, "spatial_run", "assets", "environment.compressed.ply"), "utf8"), "\u0002");
    await operations.stopLaunch(); assert.equal(calls.removed, 1);
  } finally {
    await rm(projectRoot, { recursive: true, force: true });
    await rm(temporaryRoot, { recursive: true, force: true });
  }
});

test("Godot wrapper is Compute-only, transform-explicit, and contains no panorama fallback", async () => {
  const loader = await readFile(new URL("../apps/runtime-godot/spatial_prototype/spatial_assembly_loader.gd", import.meta.url), "utf8");
  const lab = await readFile(new URL("../apps/runtime-godot/spatial_prototype/spatial_lab.gd", import.meta.url), "utf8");
  const spatialSceneLab = await readFile(new URL("../apps/runtime-godot/spatial_prototype/spatial_scene_lab.gd", import.meta.url), "utf8");
  const source = `${loader}\n${lab}\n${spatialSceneLab}`;
  for (const required of ["MATRIX_OASIS_R11_SPATIAL_READY", "require_compute", "Compositor.new()",
    "EULER_ORDER_YXZ", "localRotationMilliDegrees", "panoramaVisible", "SpatialSplat",
    "ResourceLoader.load(IMPORTED_RESOURCE_PATH", "source-density-first-surface-v1",
    "collider-global-aabb-floor-grid-v1", "collider-connected-floor-component-v2",
    "R11WalkableEnvelope", "opaque-depth-compose-v1", "depthBiasMicros",
    "placementGroundTargetMm", "nodeBindingLayout", "set_spatial_binding_layout",
    "collider-agent-navigation-component-v7", "collider-agent-grid-v1", "navigation",
    "MeshInstance3D", "get_aabb()", "to_global(corner)"])
    assert.equal(source.includes(required), true, required);
  assert.match(loader, /return path == IMPORTED_RESOURCE_PATH/u);
  assert.match(loader, /if has_layout and has_binding_layout and has_navigation and not _exact\(value, \["alignment", "collider", "coordinateTransform"/u);
  assert.match(lab, /if value\["profile"\] == "collider-agent-navigation-component-v7"/u);
  assert.match(lab, /evidence\.name = "R11NavigationEvidence"/u);
  assert.match(spatialSceneLab, /func _navigation_cell_for_player\(global_position: Vector3\) -> int:/u);
  assert.match(spatialSceneLab, /player\.global_transform = _last_safe_player_transform/u);
  for (const forbidden of ["PanoramaSkyMaterial", "environment-panorama.png", "prototype_builder/prototype_lab", "Raster", "HTTPClient", "OS.execute"])
    assert.equal(source.includes(forbidden), false, forbidden);
  assert.match(lab, /node\.rotation_degrees = _milli_degrees\(value\["localRotationMilliDegrees"\]\)/u);
});

test("invalid spatial arguments fail with one static operational code", () => {
  assert.throws(() => parseSpatialCacheArguments([]), (error) => error instanceof SpatialCacheOperationalError && error.code === "SPATIAL_CACHE_ARGUMENT_INVALID");
  assert.throws(() => parseSpatialPreviewArguments(["--prototype-run-root", "relative", "--spatial-run-root", "relative"]), /SPATIAL_HOST_ARGUMENT_INVALID/u);
});
