import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { deflateSync } from "node:zlib";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import { planPrototypeEnvironment } from "@matrix-oasis/prototype-environment-pipeline";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import {
  PROTOTYPE_ASSEMBLY_PROFILE,
  PrototypeAssemblerOperationalError,
  assemblePrototypeScene,
} from "../packages/prototype-assembler/src/index.mjs";
import {
  findVerifiedPrototypeRun,
  PrototypeCacheOperationalError,
  importPrototypeCache,
  parsePrototypeCacheArguments,
  publishPrototypeRun,
  recoverPrototypeRuns,
} from "../scripts/lib/prototype-cache-core.mjs";

const hash = (bytes) => `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
const bytes = (...values) => Uint8Array.from(values);
const execFileAsync = promisify(execFile);

function crc32(value) {
  let crc = 0xffffffff;
  for (const byte of value) {
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

function panorama() {
  const header = new Uint8Array(13); const view = new DataView(header.buffer);
  view.setUint32(0, 2, false); view.setUint32(4, 1, false); header.set([8, 2, 0, 0, 0], 8);
  const chunks = [bytes(137, 80, 78, 71, 13, 10, 26, 10), pngChunk("IHDR", header),
    pngChunk("IDAT", new Uint8Array(deflateSync(new Uint8Array(7)))), pngChunk("IEND", new Uint8Array())];
  const output = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0));
  let offset = 0; for (const item of chunks) { output.set(item, offset); offset += item.length; }
  return output;
}

function glb() {
  const json = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [{ count: 3 }, { count: 3 }], buffers: [{ byteLength: 4 }] };
  const encoded = new TextEncoder().encode(JSON.stringify(json));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + 4); const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true); view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength); output.set(encoded, 20);
  view.setUint32(20 + jsonLength, 4, true); view.setUint32(24 + jsonLength, 0x004e4942, true);
  return output;
}

function authoring() {
  return { format: "matrix-oasis.authoring-game-pack", formatVersion: "0.1.0", id: "neutral-prototype",
    contentVersion: "1.0.0", language: "zh-CN", title: "中性原型", entryNodeId: "node-start",
    entities: [{ id: "object-console", label: "控制台" }, { id: "person-guide", label: "引导占位" }],
    variables: [], cues: [], nodes: [
      { id: "node-start", title: "起点", entityIds: ["object-console"], entryCueIds: [], actions: [
        { id: "action-continue", label: "继续", effects: [], target: { kind: "node", id: "node-next" } }] },
      { id: "node-next", title: "终点前", entityIds: ["person-guide"], entryCueIds: [], actions: [
        { id: "action-finish", label: "完成", effects: [], target: { kind: "ending", id: "ending-complete" } }] }],
    endings: [{ id: "ending-complete", title: "完成", cueIds: [] }] };
}

function blueprint({ zones = 1, nonEnvironmentKinds = ["prop"], placements = null } = {}) {
  const zoneValues = Array.from({ length: zones }, (_, index) => ({ id: `zone-${index}`, label: `区域${index}`, description: "逻辑空间" }));
  const briefs = [{ id: "asset-environment", kind: "environment", prompt: "中性环境", entityId: null, roles: ["visual", "collider"] }];
  for (const [index, kind] of nonEnvironmentKinds.entries()) {
    briefs.push({ id: `asset-${kind}-${index}`, kind, prompt: "中性物件", entityId: kind === "prop" ? "object-console" : "person-guide",
      roles: kind === "prop" ? ["visual", "collider"] : ["visual"] });
  }
  const logical = placements ?? [
    { id: "placement-environment", assetBriefId: "asset-environment", zoneId: "zone-0", entityId: null },
    ...briefs.slice(1).map((brief, index) => ({ id: `placement-${index}`, assetBriefId: brief.id,
      zoneId: `zone-${index % zones}`, entityId: brief.entityId })),
  ];
  return { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: "neutral-prototype", contentVersion: "1.0.0", title: "中性原型场景",
      environmentPrompt: "一个封闭、清晰、可漫游的中性测试空间", visualStylePrompt: "克制的原型视觉" },
    zones: zoneValues, assetBriefs: briefs, placements: logical,
    nodeBindings: ["node-start", "node-next"].map((nodeId, index) => ({ nodeId,
      zoneId: `zone-${index % zones}`, visiblePlacementIds: logical.map(({ id }) => id) })) };
}

function assetMetrics() {
  return { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 1,
    maxTextureWidth: 0, maxTextureHeight: 0, boundsMm: { min: [-500, 0, -500], max: [500, 1000, 500] } };
}

async function fixture(options = {}) {
  const authoringValue = authoring(); const blueprintValue = blueprint(options);
  const authoringText = canonicalizeJsonValue(authoringValue); const blueprintText = canonicalizeJsonValue(blueprintValue);
  const compiled = await compileAuthoringGamePackJson(authoringText); assert.equal(compiled.ok, true);
  const runtimeText = compiled.canonicalJson; const receiptText = canonicalizeJsonValue(compiled.receipt);
  const receipt = compiled.receipt; const runtime = compiled.runtimePack;
  const assetFiles = new Map();
  const materializations = blueprintValue.assetBriefs.map((brief, index) => {
    const source = brief.kind === "environment" ? { type: "builtin-template", template: "kenney-prototype-room-v1" }
      : { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" };
    const roles = brief.kind === "environment" ? [["visual", "collider"]]
      : brief.roles.includes("collider") ? [["visual"], ["collider"]] : [["visual"]];
    const assets = roles.map((assetRoles, roleIndex) => {
      const value = glb(); const id = `${brief.id}-${roleIndex}`; const path = `assets/${id}.glb`;
      assetFiles.set(path, value);
      return { id, path, format: "glb", roles: assetRoles,
        normalizationProfile: brief.kind === "environment" ? "kenney-prototype-room-v1" : "matrix-oasis.glb-normalization/1",
        byteLength: value.length, sha256: hash(value), metrics: assetMetrics() };
    });
    return { assetBriefId: brief.id, source, assets };
  });
  const assetBundle = { format: "matrix-oasis.prototype-asset-bundle", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: { id: blueprintValue.scene.id,
      contentVersion: blueprintValue.scene.contentVersion, title: blueprintValue.scene.title },
    blueprint: { format: blueprintValue.format, formatVersion: blueprintValue.formatVersion,
      canonicalSha256: hash(new TextEncoder().encode(blueprintText)),
      assetBriefs: blueprintValue.assetBriefs.map(({ id, kind, entityId, roles }) => ({ id, kind, entityId, roles })) },
    runtimeIdentity: { format: runtime.format, formatVersion: runtime.formatVersion, id: runtime.source.id,
      contentVersion: runtime.source.contentVersion, authoringCanonicalSha256: `sha256:${runtime.source.canonicalSha256}`,
      artifactSha256: `sha256:${receipt.artifact.sha256}` }, environmentTemplate: "kenney-prototype-room-v1", materializations };
  const panoramaBytes = panorama(); const colliderBytes = glb();
  const environmentPlan = planPrototypeEnvironment(blueprintText); assert.equal(environmentPlan.ok, true);
  const environmentBundle = { format: "matrix-oasis.prototype-environment-bundle", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: { id: blueprintValue.scene.id,
      contentVersion: blueprintValue.scene.contentVersion, title: blueprintValue.scene.title },
    blueprint: { format: blueprintValue.format, formatVersion: blueprintValue.formatVersion,
      canonicalSha256: hash(new TextEncoder().encode(blueprintText)) },
    provider: { id: "world-labs-marble", model: "marble-1.1",
      environmentPromptSha256: environmentPlan.plan.environmentPromptSha256 },
    assets: { panorama: { path: "assets/environment-panorama.png", format: "png", width: 2, height: 1,
      byteLength: panoramaBytes.length, sha256: hash(panoramaBytes) },
      collider: { path: "assets/environment-collider.glb", format: "glb", byteLength: colliderBytes.length,
        sha256: hash(colliderBytes), metrics: { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 1 } } } };
  return { authoringGamePackJson: authoringText, sceneBlueprintJson: blueprintText,
    runtimeGamePackJson: runtimeText, runtimeReceiptJson: receiptText,
    assetBundleJson: canonicalizeJsonValue(assetBundle), assetFiles,
    environmentBundleJson: canonicalizeJsonValue(environmentBundle), environmentFiles: new Map([
      ["assets/environment-panorama.png", panoramaBytes], ["assets/environment-collider.glb", colliderBytes]]) };
}

test("public profile is exact and successful assembly replaces Kenney with Marble", async () => {
  assert.deepEqual(PROTOTYPE_ASSEMBLY_PROFILE, { id: "matrix-oasis.prototype-assembly/1", maxZones: 4,
    maxNonEnvironmentBriefs: 2, maxPlacements: 32, maxPlacementsPerZone: 8 });
  const input = await fixture();
  assert.equal(validatePrototypeAssetBundleJson(input.assetBundleJson).valid, true,
    JSON.stringify(validatePrototypeAssetBundleJson(input.assetBundleJson)));
  const result = await assemblePrototypeScene(input);
  assert.equal(result.ok, true, JSON.stringify(result)); const scene = JSON.parse(result.canonicalScenePackJson);
  assert.equal(scene.scene.id, "neutral-prototype"); assert.equal(scene.assets.some((asset) => asset.path.includes("floor-square")), false);
  assert.equal(scene.assets[0].path, "assets/environment-collider.glb");
  assert.deepEqual(scene.placements.map(({ id }) => id), ["r10-environment", "r10-placement-0"]);
  assert.deepEqual(result.referencedFiles, [
    { source: "prototype-environment", path: "assets/environment-panorama.png" },
    { source: "prototype-environment", path: "assets/environment-collider.glb" },
    { source: "prototype-assets", path: "assets/asset-prop-0-0.glb" },
    { source: "prototype-assets", path: "assets/asset-prop-0-1.glb" },
  ]);
  assert.equal((await validateScenePackJson(result.canonicalScenePackJson, input.runtimeGamePackJson, input.runtimeReceiptJson)).valid, true);
});

test("layout and report are byte deterministic twenty times and inputs remain unchanged", async () => {
  const input = await fixture({ nonEnvironmentKinds: ["prop", "character-placeholder"] });
  const before = input.assetFiles.get("assets/asset-prop-0-0.glb").slice();
  const results = await Promise.all(Array.from({ length: 20 }, () => assemblePrototypeScene(input)));
  assert.ok(results.every(({ ok }) => ok));
  assert.equal(new Set(results.map(({ canonicalScenePackJson }) => canonicalScenePackJson)).size, 1);
  assert.equal(new Set(results.map(({ canonicalAssemblyReportJson }) => canonicalAssemblyReportJson)).size, 1);
  assert.equal(Object.isFrozen(results[0]), true); assert.equal(Object.isFrozen(results[0].referencedFiles), true);
  assert.deepEqual(input.assetFiles.get("assets/asset-prop-0-0.glb"), before);
});

test("physical placement ids remain within Scene Pack limits for maximum-length blueprint ids", async () => {
  const maximumId = `p${"a".repeat(95)}`;
  const input = await fixture({ placements: [
    { id: "placement-environment", assetBriefId: "asset-environment", zoneId: "zone-0", entityId: null },
    { id: maximumId, assetBriefId: "asset-prop-0", zoneId: "zone-0", entityId: "object-console" },
  ] });
  const result = await assemblePrototypeScene(input);
  assert.equal(result.ok, true, JSON.stringify(result));
  const scene = JSON.parse(result.canonicalScenePackJson);
  assert.deepEqual(scene.placements.map(({ id }) => id), ["r10-environment", "r10-placement-0"]);
  assert.ok(scene.placements.every(({ id }) => id.length <= 96));
});

test("profile accepts four zones and 32 placements but rejects each exceeded boundary", async () => {
  const repeated = Array.from({ length: 32 }, (_, index) => ({ id: `placement-${index}`,
    assetBriefId: index === 0 ? "asset-environment" : "asset-prop-0", zoneId: `zone-${Math.floor(index / 8)}`,
    entityId: index === 0 ? null : "object-console" }));
  for (const nonEnvironmentKinds of [[], ["prop"], ["prop", "character-placeholder"]]) {
    assert.equal((await assemblePrototypeScene(await fixture({ nonEnvironmentKinds }))).ok, true);
  }
  assert.equal((await assemblePrototypeScene(await fixture({ zones: 4, placements: repeated }))).ok, true);
  for (const options of [
    { zones: 5 },
    { nonEnvironmentKinds: ["prop", "character-placeholder", "prop"] },
    { zones: 4, placements: [...repeated, { id: "placement-32", assetBriefId: "asset-prop-0", zoneId: "zone-0", entityId: "object-console" }] },
    { zones: 1, placements: Array.from({ length: 9 }, (_, index) => ({ id: `placement-${index}`,
      assetBriefId: index === 0 ? "asset-environment" : "asset-prop-0", zoneId: "zone-0", entityId: index === 0 ? null : "object-console" })) },
  ]) {
    const result = await assemblePrototypeScene(await fixture(options));
    assert.equal(result.ok, false); assert.equal(result.diagnostics[0].code, "PROTOTYPE_ASSEMBLY_PROFILE_UNSUPPORTED");
  }
});

test("canonical, identity, file and reference failures are static and fail closed", async () => {
  const input = await fixture();
  assert.equal((await assemblePrototypeScene({ ...input, sceneBlueprintJson: `${input.sceneBlueprintJson}\n` })).diagnostics[0].code,
    "PROTOTYPE_ASSEMBLY_GENERATION_INVALID");
  const changedAssetFiles = new Map(input.assetFiles); changedAssetFiles.set("assets/asset-prop-0-0.glb", bytes(9));
  assert.equal((await assemblePrototypeScene({ ...input, assetFiles: changedAssetFiles })).diagnostics[0].code,
    "PROTOTYPE_ASSEMBLY_ASSET_FILES_INVALID");
  const environment = JSON.parse(input.environmentBundleJson); environment.scene.contentVersion = "2.0.0";
  assert.equal((await assemblePrototypeScene({ ...input, environmentBundleJson: canonicalizeJsonValue(environment) })).diagnostics[0].code,
    "PROTOTYPE_ASSEMBLY_IDENTITY_MISMATCH");
});

test("accessors are never invoked and operational failures expose one static code", async () => {
  let reads = 0; const input = await fixture();
  const hostile = { ...input }; Object.defineProperty(hostile, "assetBundleJson", { enumerable: true, get() { reads += 1; return input.assetBundleJson; } });
  const rejected = await assemblePrototypeScene(hostile); assert.equal(rejected.ok, false); assert.equal(reads, 0);
  await assert.rejects(() => assemblePrototypeScene(new Proxy(input, { getPrototypeOf() { throw new Error("dynamic-sentinel"); } })),
    (error) => error instanceof PrototypeAssemblerOperationalError && error.code === "PROTOTYPE_ASSEMBLER_INTERNAL_ERROR" && !String(error).includes("sentinel"));
});

test("assembler source is offline and independent from provider adapters", async () => {
  const source = await readFile(new URL("../packages/prototype-assembler/src/index.mjs", import.meta.url), "utf8");
  for (const forbidden of ["fetch(", "process.env", "createMarbleWorldProvider", "createMeshyTextTo3DProvider",
    "prototype-generator/src", "meshy-provider.mjs", "marble-provider.mjs"]) assert.equal(source.includes(forbidden), false);
});

const cacheServices = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });

async function writeCacheInputs(root, input, runRootOverride = null) {
  const promptFile = path.join(root, "prompt.txt");
  const prototypeDir = path.join(root, "prototype");
  const assetBundleDir = path.join(root, "asset-bundle");
  const environmentBundleDir = path.join(root, "environment-bundle");
  const runRoot = runRootOverride ?? path.join(root, "run-root");
  await Promise.all([mkdir(prototypeDir), mkdir(path.join(assetBundleDir, "assets"), { recursive: true }),
    mkdir(path.join(environmentBundleDir, "assets"), { recursive: true })]);
  const prompt = "Build a neutral room with one console and one static guide.";
  await writeFile(promptFile, prompt, "utf8");
  const prototypeFiles = {
    "authoring-game-pack.json": input.authoringGamePackJson,
    "scene-blueprint.json": input.sceneBlueprintJson,
    "runtime-game-pack.json": input.runtimeGamePackJson,
    "runtime-receipt.json": input.runtimeReceiptJson,
  };
  for (const [name, text] of Object.entries(prototypeFiles)) await writeFile(path.join(prototypeDir, name), text, "utf8");
  const artifacts = Object.entries(prototypeFiles).map(([name, text]) => ({ byteLength: new TextEncoder().encode(text).length,
    name, sha256: hash(new TextEncoder().encode(text)) }));
  await writeFile(path.join(prototypeDir, "generation-report.json"), canonicalizeJsonValue({ artifacts,
    format: "matrix-oasis.prototype-generation-report", formatVersion: "0.1.0", model: "qualification-model",
    requestCount: 1, runtimeCheck: { declaredActionCount: 2, initialAvailableActionCount: 1, status: "ready" }, usage: null }), "utf8");
  await writeFile(path.join(assetBundleDir, "prototype-asset-bundle.json"), input.assetBundleJson, "utf8");
  for (const [name, value] of input.assetFiles) await writeFile(path.join(assetBundleDir, ...name.split("/")), value);
  await writeFile(path.join(environmentBundleDir, "prototype-environment-bundle.json"), input.environmentBundleJson, "utf8");
  for (const [name, value] of input.environmentFiles) await writeFile(path.join(environmentBundleDir, ...name.split("/")), value);
  const environment = JSON.parse(input.environmentBundleJson);
  const environmentReport = { bundleSha256: hash(new TextEncoder().encode(input.environmentBundleJson)),
    counts: { creates: 1, downloads: 2, polls: 1, worldGets: 1 },
    files: [environment.assets.panorama, environment.assets.collider].map((asset) => ({ byteLength: asset.byteLength,
      path: asset.path, sha256: asset.sha256 })), format: "matrix-oasis.prototype-environment-materialization-report",
    formatVersion: "0.1.0", provider: { id: "world-labs-marble", model: "marble-1.1" } };
  await writeFile(path.join(environmentBundleDir, "prototype-environment-report.json"), canonicalizeJsonValue(environmentReport), "utf8");
  const args = ["--prompt-file", promptFile, "--prototype-dir", prototypeDir, "--asset-bundle-dir", assetBundleDir,
    "--environment-bundle-dir", environmentBundleDir, "--run-root", runRoot];
  return { args, prompt, runRoot };
}

test("cache argument surface is exact and absolute", () => {
  const root = path.resolve(os.tmpdir(), "matrix-oasis-r10-arguments");
  const parsed = parsePrototypeCacheArguments(["--prompt-file", path.join(root, "p.txt"), "--prototype-dir", path.join(root, "p"),
    "--asset-bundle-dir", path.join(root, "a"), "--environment-bundle-dir", path.join(root, "e"), "--run-root", path.join(root, "r")]);
  assert.equal(parsed.runRoot, path.join(root, "r"));
  for (const args of [[], ["--prompt-file", "relative"], ["--unknown", root, "--prototype-dir", root,
    "--asset-bundle-dir", root, "--environment-bundle-dir", root, "--run-root", root]]) {
    assert.throws(() => parsePrototypeCacheArguments(args), PrototypeCacheOperationalError);
  }
});

test("verified cache publishes one self-validating run, keeps Kenney only as R9 provenance, and stores no prompt", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r10-cache-"));
  try {
    const input = await fixture({ nonEnvironmentKinds: ["prop", "character-placeholder"] });
    const prepared = await writeCacheInputs(root, input);
    const result = await importPrototypeCache({ args: prepared.args, temporaryRoot: root, services: cacheServices,
      assemblePrototypeScene, canonicalizeJsonValue });
    assert.equal(result.cacheHit, true); assert.match(result.runId, /^[0-9a-f]{64}-[0-9a-f]{64}$/u);
    const current = JSON.parse(await readFile(path.join(prepared.runRoot, "current.json"), "utf8"));
    assert.equal(current.runId, result.runId);
    const run = path.join(prepared.runRoot, "runs", result.runId);
    const names = (await readdir(run)).sort();
    assert.deepEqual(names, ["assembly-report.json", "assets", "authoring-game-pack.json", "generation-report.json",
      "prototype-asset-bundle.json", "prototype-asset-report.json", "prototype-environment-bundle.json",
      "prototype-environment-report.json", "run-report.json", "runtime-game-pack.json", "runtime-receipt.json",
      "scene-blueprint.json", "scene-pack.json"]);
    const assetNames = (await readdir(path.join(run, "assets"))).sort();
    assert.deepEqual(assetNames, ["asset-character-placeholder-1-0.glb", "asset-environment-0.glb",
      "asset-prop-0-0.glb", "asset-prop-0-1.glb", "environment-collider.glb", "environment-panorama.png"]);
    const scene = JSON.parse(await readFile(path.join(run, "scene-pack.json"), "utf8"));
    assert.equal(scene.assets.some((asset) => asset.path === "assets/asset-environment-0.glb"), false);
    const allText = (await Promise.all(names.filter((name) => name.endsWith(".json")).map((name) => readFile(path.join(run, name), "utf8")))).join("\n");
    assert.equal(allText.includes(prepared.prompt), false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("memory publication is restart-recoverable and cache hits revalidate every persisted byte", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r10-memory-run-"));
  const runRoot = path.join(root, "run-root");
  try {
    const input = await fixture({ nonEnvironmentKinds: ["prop", "character-placeholder"] });
    const prototypeArtifacts = {
      authoringGamePackJson: input.authoringGamePackJson,
      sceneBlueprintJson: input.sceneBlueprintJson,
      runtimeGamePackJson: input.runtimeGamePackJson,
      runtimeReceiptJson: input.runtimeReceiptJson,
    };
    const names = [["authoring-game-pack.json", "authoringGamePackJson"], ["scene-blueprint.json", "sceneBlueprintJson"],
      ["runtime-game-pack.json", "runtimeGamePackJson"], ["runtime-receipt.json", "runtimeReceiptJson"]];
    prototypeArtifacts.generationReportJson = canonicalizeJsonValue({ artifacts: names.map(([name, key]) => ({
      byteLength: new TextEncoder().encode(prototypeArtifacts[key]).length, name,
      sha256: hash(new TextEncoder().encode(prototypeArtifacts[key])),
    })), format: "matrix-oasis.prototype-generation-report", formatVersion: "0.1.0", model: "qualification-model",
    requestCount: 1, runtimeCheck: { declaredActionCount: 2, initialAvailableActionCount: 1, status: "ready" }, usage: null });
    const environmentBundle = JSON.parse(input.environmentBundleJson);
    const environmentReportJson = canonicalizeJsonValue({
      bundleSha256: hash(new TextEncoder().encode(input.environmentBundleJson)), counts: { creates: 1, downloads: 2, polls: 1, worldGets: 1 },
      files: [environmentBundle.assets.panorama, environmentBundle.assets.collider].map(({ path: assetPath }) => {
        const value = input.environmentFiles.get(assetPath); return { byteLength: value.length, path: assetPath, sha256: hash(value) };
      }), format: "matrix-oasis.prototype-environment-materialization-report", formatVersion: "0.1.0",
      provider: { id: "world-labs-marble", model: "marble-1.1" },
    });
    const prompt = "Build one neutral room with a console and a static guide.";
    const published = await publishPrototypeRun({ prompt, prototypeArtifacts,
      assetMaterialization: { canonicalBundleJson: input.assetBundleJson,
        files: [...input.assetFiles].map(([assetPath, value]) => ({ path: assetPath, bytes: value })) },
      environmentMaterialization: { canonicalBundleJson: input.environmentBundleJson, canonicalReportJson: environmentReportJson,
        files: [...input.environmentFiles].map(([assetPath, value]) => ({ path: assetPath, bytes: value })) },
      runRoot, temporaryRoot: root, services: cacheServices, source: "live-provider",
      assemblePrototypeScene, canonicalizeJsonValue });
    const recovered = await recoverPrototypeRuns({ runRoot, temporaryRoot: root, services: cacheServices,
      assemblePrototypeScene, canonicalizeJsonValue });
    assert.equal(recovered.currentRunId, published.runId); assert.deepEqual(recovered.runs.map(({ runId }) => runId), [published.runId]);
    const found = await findVerifiedPrototypeRun({ promptSha256: hash(new TextEncoder().encode(prompt)), model: "qualification-model",
      runRoot, temporaryRoot: root, services: cacheServices, assemblePrototypeScene, canonicalizeJsonValue });
    assert.deepEqual(found, { ok: true, runId: published.runId });
    const scenePath = path.join(runRoot, "runs", published.runId, "scene-pack.json");
    await writeFile(scenePath, `${await readFile(scenePath, "utf8")}\n`, "utf8");
    assert.deepEqual(await findVerifiedPrototypeRun({ promptSha256: hash(new TextEncoder().encode(prompt)), model: "qualification-model",
      runRoot, temporaryRoot: root, services: cacheServices, assemblePrototypeScene, canonicalizeJsonValue }), { ok: false });
    assert.deepEqual((await recoverPrototypeRuns({ runRoot, temporaryRoot: root, services: cacheServices,
      assemblePrototypeScene, canonicalizeJsonValue })).runs, []);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("same cache publication has one winner and never replaces current on failure", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r10-cache-race-"));
  try {
    const prepared = await writeCacheInputs(root, await fixture());
    const calls = [0, 1].map(() => importPrototypeCache({ args: prepared.args, temporaryRoot: root, services: cacheServices,
      assemblePrototypeScene, canonicalizeJsonValue }));
    const outcomes = await Promise.allSettled(calls);
    assert.equal(outcomes.filter(({ status }) => status === "fulfilled").length, 1);
    assert.equal(outcomes.filter(({ status }) => status === "rejected").length, 1);
    const first = outcomes.find(({ status }) => status === "fulfilled").value;
    const before = await readFile(path.join(prepared.runRoot, "current.json"));
    await assert.rejects(() => importPrototypeCache({ args: prepared.args, temporaryRoot: root, services: cacheServices,
      assemblePrototypeScene, canonicalizeJsonValue }), (error) => error.code === "PROTOTYPE_CACHE_RUN_EXISTS");
    assert.deepEqual(await readFile(path.join(prepared.runRoot, "current.json")), before);
    assert.equal(JSON.parse(before).runId, first.runId);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("publish failure cleans only owned staging and does not create current", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r10-cache-fail-"));
  try {
    const prepared = await writeCacheInputs(root, await fixture());
    const services = { ...cacheServices, async rename(source, target) {
      if (path.basename(source).startsWith(".matrix-oasis-r10-")) throw Object.assign(new Error("dynamic-value"), { code: "EACCES" });
      return rename(source, target);
    } };
    await assert.rejects(() => importPrototypeCache({ args: prepared.args, temporaryRoot: root, services,
      assemblePrototypeScene, canonicalizeJsonValue }), (error) => error.code === "PROTOTYPE_CACHE_PUBLISH_FAILED" && !String(error).includes("dynamic"));
    const runs = path.join(prepared.runRoot, "runs");
    assert.deepEqual(await readdir(runs), []);
    await assert.rejects(() => lstat(path.join(prepared.runRoot, "current.json")), { code: "ENOENT" });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("input directory junction is rejected before cache bytes are trusted", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r10-cache-link-"));
  try {
    const prepared = await writeCacheInputs(root, await fixture());
    const external = path.join(root, "external-assets"); await rename(path.join(root, "asset-bundle"), external);
    const junction = path.join(root, "asset-bundle"); await symlink(external, junction, "junction");
    await assert.rejects(() => importPrototypeCache({ args: prepared.args, temporaryRoot: root, services: cacheServices,
      assemblePrototypeScene, canonicalizeJsonValue }), (error) => error.code === "PROTOTYPE_CACHE_INPUT_INVALID");
    await assert.rejects(() => lstat(prepared.runRoot), { code: "ENOENT" });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("same-inode input mutation is rejected and creates no run", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r10-cache-mutate-"));
  try {
    const prepared = await writeCacheInputs(root, await fixture());
    const target = path.join(root, "prompt.txt"); let mutated = false;
    const services = { ...cacheServices, async openFile(candidate, flags) {
      const handle = await open(candidate, flags);
      if (candidate !== target || flags !== "r") return handle;
      return {
        stat: (...args) => handle.stat(...args),
        async read(...args) {
          const result = await handle.read(...args);
          if (!mutated) { mutated = true; await writeFile(target, "X".repeat((await lstat(target)).size)); }
          return result;
        },
        close: () => handle.close(),
      };
    } };
    await assert.rejects(() => importPrototypeCache({ args: prepared.args, temporaryRoot: root, services,
      assemblePrototypeScene, canonicalizeJsonValue }), (error) => error.code === "PROTOTYPE_CACHE_PROMPT_INVALID");
    await assert.rejects(() => lstat(prepared.runRoot), { code: "ENOENT" });
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("real cache import CLI resolves workspaces and publishes one static success line", async () => {
  const cliTemporaryRoot = process.platform === "win32" ? path.join(path.parse(process.cwd()).root, "tmp") : os.tmpdir();
  const root = await mkdtemp(path.join(cliTemporaryRoot, "matrix-oasis-r10-cache-cli-input-"));
  const runRoot = path.join(cliTemporaryRoot, `matrix-oasis-r10-cache-cli-output-${path.basename(root)}`);
  try {
    const prepared = await writeCacheInputs(root, await fixture(), runRoot);
    const { stdout, stderr } = await execFileAsync(process.execPath,
      [path.resolve("scripts/import-prototype-cache.mjs"), ...prepared.args],
      { cwd: path.resolve("."), windowsHide: true, timeout: 30_000 });
    assert.match(stdout, /^PROTOTYPE_CACHE_IMPORTED run=[0-9a-f]{64}-[0-9a-f]{64} files=\d+\n$/u);
    assert.equal(stderr, "");
    assert.equal((await readdir(path.join(runRoot, "runs"))).length, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(runRoot, { recursive: true, force: true });
  }
});
