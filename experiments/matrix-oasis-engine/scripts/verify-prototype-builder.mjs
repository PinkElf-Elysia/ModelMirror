import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import {
  planPrototypeEnvironment,
  validatePrototypeEnvironmentBundleJson,
} from "@matrix-oasis/prototype-environment-pipeline";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import { inspectEnvironmentCollider } from "../packages/prototype-environment-pipeline/src/binary-inspection.mjs";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { prototypeGodotArguments } from "./preview-prototype.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryBase = path.resolve(path.parse(moduleRoot).root, "tmp");
const encoder = new TextEncoder();
const bytes = (...values) => Uint8Array.from(values);
const hash = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;

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
  view.setUint32(0, data.length, false); output.set(typeBytes, 4); output.set(data, 8);
  const checked = new Uint8Array(typeBytes.length + data.length); checked.set(typeBytes); checked.set(data, typeBytes.length);
  view.setUint32(8 + data.length, crc32(checked), false);
  return output;
}

function panorama() {
  const header = new Uint8Array(13); const view = new DataView(header.buffer);
  view.setUint32(0, 2, false); view.setUint32(4, 1, false); header.set([8, 2, 0, 0, 0], 8);
  const chunks = [bytes(137, 80, 78, 71, 13, 10, 26, 10), pngChunk("IHDR", header),
    pngChunk("IDAT", new Uint8Array(deflateSync(new Uint8Array(7)))), pngChunk("IEND", new Uint8Array())];
  const output = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0));
  let offset = 0; for (const item of chunks) { output.set(item, offset); offset += item.length; }
  return output;
}

function assetFixtureGlb() {
  const binary = new Uint8Array(44); const binaryView = new DataView(binary.buffer);
  const positions = [-1, 0, -1, 1, 0, -1, 0, 0, 1];
  positions.forEach((value, index) => binaryView.setFloat32(index * 4, value, true));
  [0, 1, 2].forEach((value, index) => binaryView.setUint16(36 + index * 2, value, true));
  const document = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, mode: 4 }] }],
    buffers: [{ byteLength: binary.length }], bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: 36, target: 34962 },
      { buffer: 0, byteOffset: 36, byteLength: 6, target: 34963 },
    ], accessors: [
      { bufferView: 0, byteOffset: 0, componentType: 5126, count: 3, type: "VEC3", min: [-1, 0, -1], max: [1, 0, 1] },
      { bufferView: 1, byteOffset: 0, componentType: 5123, count: 3, type: "SCALAR", min: [0], max: [2] },
    ] };
  const encoded = encoder.encode(JSON.stringify(document)); const padded = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + padded + 8 + binary.length); const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, output.length, true);
  view.setUint32(12, padded, true); view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + padded); output.set(encoded, 20);
  view.setUint32(20 + padded, binary.length, true); view.setUint32(24 + padded, 0x004e4942, true);
  output.set(binary, 28 + padded);
  return output;
}

function authoring() {
  return { format: "matrix-oasis.authoring-game-pack", formatVersion: "0.1.0", id: "r10-offline-smoke",
    contentVersion: "1.0.0", language: "en", title: "Offline prototype smoke", entryNodeId: "node-start",
    entities: [{ id: "object-console", label: "Console" }], variables: [], cues: [],
    nodes: [{ id: "node-start", title: "Start", entityIds: ["object-console"], entryCueIds: [],
      actions: [{ id: "action-complete", label: "Complete", effects: [], target: { kind: "ending", id: "ending-complete" } }] }],
    endings: [{ id: "ending-complete", title: "Complete", cueIds: [] }] };
}

function blueprint() {
  return { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: "r10-offline-smoke", contentVersion: "1.0.0", title: "Offline prototype smoke",
      environmentPrompt: "A neutral enclosed test room.", visualStylePrompt: "Restrained prototype geometry." },
    zones: [{ id: "zone-main", label: "Main", description: "Single logical test zone." }],
    assetBriefs: [{ id: "asset-environment", kind: "environment", prompt: "A neutral enclosed test room.",
      entityId: null, roles: ["visual", "collider"] }],
    placements: [{ id: "placement-environment", assetBriefId: "asset-environment", zoneId: "zone-main", entityId: null }],
    nodeBindings: [{ nodeId: "node-start", zoneId: "zone-main", visiblePlacementIds: ["placement-environment"] }] };
}

function cleanTemporary(candidate, identity) {
  try {
    const resolvedBase = fs.realpathSync(temporaryBase);
    const resolved = fs.realpathSync(candidate);
    const stat = fs.lstatSync(resolved, { bigint: true });
    if (path.dirname(resolved) !== resolvedBase || !path.basename(resolved).startsWith("matrix-oasis-r10-builder-") ||
        stat.isSymbolicLink() || stat.dev !== identity.dev || stat.ino !== identity.ino) return;
    fs.rmSync(resolved, { recursive: true });
  } catch {
    // An ambiguous temporary directory is preserved.
  }
}

async function prepareRun(runDirectory) {
  const authoringJson = canonicalizeJsonValue(authoring());
  const sceneBlueprintJson = canonicalizeJsonValue(blueprint());
  const compiled = await compileAuthoringGamePackJson(authoringJson);
  if (!compiled.ok) throw new Error("PROTOTYPE_BUILDER_VERIFY_COMPILE_FAILED");
  const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
  const blueprintValue = JSON.parse(sceneBlueprintJson);
  const sceneIdentity = { id: blueprintValue.scene.id, contentVersion: blueprintValue.scene.contentVersion,
    title: blueprintValue.scene.title };
  const assetBytes = assetFixtureGlb();
  const assetPath = "assets/asset-environment-0.glb";
  const assetBundle = { format: "matrix-oasis.prototype-asset-bundle", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: sceneIdentity,
    blueprint: { format: blueprintValue.format, formatVersion: blueprintValue.formatVersion,
      canonicalSha256: hash(encoder.encode(sceneBlueprintJson)), assetBriefs: blueprintValue.assetBriefs.map(({ id, kind, entityId, roles }) => ({ id, kind, entityId, roles })) },
    runtimeIdentity: { format: compiled.runtimePack.format, formatVersion: compiled.runtimePack.formatVersion,
      id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion,
      authoringCanonicalSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
      artifactSha256: `sha256:${compiled.receipt.artifact.sha256}` },
    environmentTemplate: "kenney-prototype-room-v1",
    materializations: [{ assetBriefId: "asset-environment", source: { type: "builtin-template", template: "kenney-prototype-room-v1" },
      assets: [{ id: "asset-environment-0", path: assetPath, format: "glb", roles: ["visual", "collider"],
        normalizationProfile: "kenney-prototype-room-v1", byteLength: assetBytes.length, sha256: hash(assetBytes),
        metrics: { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 1,
          maxTextureWidth: 0, maxTextureHeight: 0, boundsMm: { min: [-500, 0, -500], max: [500, 1000, 500] } } }] }] };
  const assetBundleJson = canonicalizeJsonValue(assetBundle);
  const assetReport = validatePrototypeAssetBundleJson(assetBundleJson);
  if (!assetReport.valid) throw new Error(`PROTOTYPE_BUILDER_VERIFY_ASSET_${assetReport.diagnostics[0]?.code ?? "FAILED"}`);

  const panoramaBytes = panorama();
  const colliderBytes = assetFixtureGlb();
  const environmentPlan = planPrototypeEnvironment(sceneBlueprintJson);
  if (!environmentPlan.ok) throw new Error("PROTOTYPE_BUILDER_VERIFY_ENVIRONMENT_PLAN_FAILED");
  const inspected = inspectEnvironmentCollider(colliderBytes, { colliderBytes: 32 * 1024 * 1024 });
  if (!inspected.ok) throw new Error("PROTOTYPE_BUILDER_VERIFY_COLLIDER_FAILED");
  const environmentBundle = { format: "matrix-oasis.prototype-environment-bundle", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: sceneIdentity,
    blueprint: { format: blueprintValue.format, formatVersion: blueprintValue.formatVersion,
      canonicalSha256: hash(encoder.encode(sceneBlueprintJson)) },
    provider: { id: "world-labs-marble", model: "marble-1.1",
      environmentPromptSha256: environmentPlan.plan.environmentPromptSha256 },
    assets: { panorama: { path: "assets/environment-panorama.png", format: "png", width: 2, height: 1,
      byteLength: panoramaBytes.length, sha256: hash(panoramaBytes) },
      collider: { path: "assets/environment-collider.glb", format: "glb", byteLength: colliderBytes.length,
        sha256: hash(colliderBytes), metrics: inspected.metrics } } };
  const environmentBundleJson = canonicalizeJsonValue(environmentBundle);
  const environmentFiles = new Map([["assets/environment-panorama.png", panoramaBytes], ["assets/environment-collider.glb", colliderBytes]]);
  if (!validatePrototypeEnvironmentBundleJson(environmentBundleJson, environmentFiles).valid)
    throw new Error("PROTOTYPE_BUILDER_VERIFY_ENVIRONMENT_FAILED");
  const assembled = await assemblePrototypeScene({ authoringGamePackJson: authoringJson, sceneBlueprintJson,
    runtimeGamePackJson: compiled.canonicalJson, runtimeReceiptJson, assetBundleJson,
    assetFiles: new Map([[assetPath, assetBytes]]), environmentBundleJson, environmentFiles });
  if (!assembled.ok || !(await validateScenePackJson(assembled.canonicalScenePackJson, compiled.canonicalJson, runtimeReceiptJson)).valid)
    throw new Error("PROTOTYPE_BUILDER_VERIFY_ASSEMBLY_FAILED");
  fs.mkdirSync(path.join(runDirectory, "assets"));
  const textFiles = new Map([["runtime-game-pack.json", compiled.canonicalJson], ["runtime-receipt.json", runtimeReceiptJson],
    ["scene-pack.json", assembled.canonicalScenePackJson], ["prototype-environment-bundle.json", environmentBundleJson]]);
  for (const [name, text] of textFiles) fs.writeFileSync(path.join(runDirectory, name), text, { encoding: "utf8", flag: "wx" });
  for (const [name, value] of environmentFiles) fs.writeFileSync(path.join(runDirectory, ...name.split("/")), value, { flag: "wx" });
}

async function main() {
  fs.mkdirSync(temporaryBase, { recursive: true });
  const temporaryRoot = fs.mkdtempSync(path.join(temporaryBase, "matrix-oasis-r10-builder-"));
  const stat = fs.lstatSync(temporaryRoot, { bigint: true });
  const identity = { dev: stat.dev, ino: stat.ino };
  let preview = null;
  try {
    const runDirectory = path.join(temporaryRoot, "run"); fs.mkdirSync(runDirectory);
    await prepareRun(runDirectory);
    preview = createRuntimePreviewProject({ moduleRoot });
    const godot = resolveGodotBinary();
    let imported;
    try { imported = runGodotCommand({ command: godot.command,
      args: ["--headless", "--editor", "--path", preview.projectRoot, "--quit"], cwd: moduleRoot, timeout: 120_000 }); }
    catch { throw new Error("PROTOTYPE_BUILDER_VERIFY_GODOT_IMPORT_FAILED"); }
    assertGodotOutputClean(imported);
    const smoke = spawnSync(godot.command,
      prototypeGodotArguments({ projectRoot: preview.projectRoot, runDirectory, smoke: true }), {
        cwd: moduleRoot, encoding: "utf8", maxBuffer: 8 * 1024 * 1024, shell: false, timeout: 30_000, windowsHide: true,
      });
    const output = `${smoke.stdout ?? ""}${smoke.stderr ?? ""}`;
    if (smoke.error || smoke.status !== 0) {
      const runtimeCode = /\b(?:PACK|GODOT)_[A-Z0-9_]{2,127}\b/u.exec(output)?.[0];
      const readySeen = output.includes("MATRIX_OASIS_R10_PROTOTYPE_READY");
      throw new Error(runtimeCode ? `PROTOTYPE_BUILDER_VERIFY_${runtimeCode}` : readySeen
        ? "PROTOTYPE_BUILDER_VERIFY_GODOT_SMOKE_DID_NOT_EXIT" : "PROTOTYPE_BUILDER_VERIFY_GODOT_SMOKE_FAILED");
    }
    assertGodotOutputClean(output);
    const readyCount = output.split("MATRIX_OASIS_R10_PROTOTYPE_READY").length - 1;
    const sceneCount = output.split("MATRIX_OASIS_R7_SCENE_BINDING_READY").length - 1;
    if (readyCount !== 1 || sceneCount !== 1) throw new Error("PROTOTYPE_BUILDER_VERIFY_MARKER_FAILED");
    process.stdout.write("PROTOTYPE_BUILDER_VERIFY_OK markers=2\n");
  } finally {
    if (preview) removeRuntimePreviewProject(preview.temporaryRoot, { moduleRoot, identity: preview.identity });
    cleanTemporary(temporaryRoot, identity);
  }
}

try { await main(); }
catch (error) {
  const candidate = typeof error?.code === "string" ? error.code : error?.message;
  const code = typeof candidate === "string" && /^[A-Z][A-Z0-9_]{2,127}$/u.test(candidate)
    ? candidate : "PROTOTYPE_BUILDER_VERIFY_FAILED";
  process.stderr.write(`${code}\n`); process.exitCode = 1;
}
