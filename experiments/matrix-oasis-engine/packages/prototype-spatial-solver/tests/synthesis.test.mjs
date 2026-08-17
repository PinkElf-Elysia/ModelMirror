import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import { validatePrototypeSpatialIntentJson } from "@matrix-oasis/prototype-spatial-planning-contracts";
import * as api from "../src/index.mjs";

const execFile = promisify(execFileCallback);

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return `sha256:${Buffer.from(digest).toString("hex")}`;
}
function metrics(size) {
  return { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 100, maxTextureWidth: 512, maxTextureHeight: 512, boundsMm: { min: [-Math.floor(size / 2), 0, -Math.floor(size / 2)], max: [Math.ceil(size / 2), 1800, Math.ceil(size / 2)] } };
}
async function fixture() {
  const authoringText = await readFile(new URL("../../../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8");
  const compiled = await compileAuthoringGamePackJson(authoringText);
  assert.equal(compiled.ok, true);
  const runtimeText = compiled.canonicalJson;
  const receiptText = canonicalizeJsonValue(compiled.receipt);
  const nodes = compiled.runtimePack.nodes;
  const blueprint = {
    format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion, title: "Neutral", environmentPrompt: "A neutral room.", visualStylePrompt: "Simple neutral materials." },
    zones: [{ id: "zone-a", label: "A", description: "First area." }, { id: "zone-b", label: "B", description: "Second area." }],
    assetBriefs: [
      { id: "brief-environment", kind: "environment", prompt: "Neutral room", entityId: null, roles: ["visual", "collider"] },
      { id: "brief-prop", kind: "prop", prompt: "Compact control", entityId: "control-unit", roles: ["visual", "collider"] },
      { id: "brief-character", kind: "character-placeholder", prompt: "Standing actor", entityId: "actor-unit", roles: ["visual", "collider"] },
    ],
    placements: [
      { id: "placement-environment", assetBriefId: "brief-environment", zoneId: "zone-a", entityId: null },
      { id: "placement-prop", assetBriefId: "brief-prop", zoneId: "zone-a", entityId: "control-unit" },
      { id: "placement-character", assetBriefId: "brief-character", zoneId: "zone-b", entityId: "actor-unit" },
    ],
    nodeBindings: nodes.map((node, index) => ({ nodeId: node.id, zoneId: index % 2 === 0 ? "zone-a" : "zone-b", visiblePlacementIds: ["placement-environment", "placement-prop", "placement-character"] })),
  };
  const blueprintText = canonicalizeJsonValue(blueprint);
  const file = (id, path, roles, profile, size) => ({ id, path, format: "glb", roles, normalizationProfile: profile, byteLength: 1024, sha256: `sha256:${id === "asset-env" ? "a" : id === "asset-prop" ? "b" : "c"}`.padEnd(71, id === "asset-env" ? "a" : id === "asset-prop" ? "b" : "c"), metrics: metrics(size) });
  const bundle = {
    format: "matrix-oasis.prototype-asset-bundle", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: blueprint.scene.id, contentVersion: blueprint.scene.contentVersion, title: blueprint.scene.title },
    blueprint: { format: blueprint.format, formatVersion: blueprint.formatVersion, canonicalSha256: await sha256(blueprintText), assetBriefs: blueprint.assetBriefs.map(({ id, kind, entityId, roles }) => ({ id, kind, entityId, roles })) },
    runtimeIdentity: { format: compiled.runtimePack.format, formatVersion: compiled.runtimePack.formatVersion, id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion, authoringCanonicalSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`, artifactSha256: `sha256:${compiled.receipt.artifact.sha256}` },
    environmentTemplate: "kenney-prototype-room-v1",
    materializations: [
      { assetBriefId: "brief-environment", source: { type: "builtin-template", template: "kenney-prototype-room-v1" }, assets: [file("asset-env", "assets/environment.glb", ["visual", "collider"], "kenney-prototype-room-v1", 30000)] },
      { assetBriefId: "brief-prop", source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" }, assets: [file("asset-prop", "assets/prop.glb", ["visual", "collider"], "matrix-oasis.glb-normalization/1", 800)] },
      { assetBriefId: "brief-character", source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" }, assets: [file("asset-character", "assets/character.glb", ["visual", "collider"], "matrix-oasis.glb-normalization/1", 700)] },
    ],
  };
  const assetBundleJson = canonicalizeJsonValue(bundle);
  assert.equal(validatePrototypeAssetBundleJson(assetBundleJson).valid, true, JSON.stringify(validatePrototypeAssetBundleJson(assetBundleJson)));
  return { sceneBlueprintJson: blueprintText, runtimeGamePackJson: runtimeText, runtimeReceiptJson: receiptText, assetBundleJson };
}

test("public API is minimal and profile is frozen", () => {
  assert.deepEqual(Object.keys(api).sort(), ["PROTOTYPE_SPATIAL_INTENT_SYNTHESIS_PROFILE", "PrototypeSpatialSolverOperationalError", "solvePrototypeSpatialLayout", "synthesizePrototypeSpatialIntent"].sort());
  assert.equal(Object.isFrozen(api.PROTOTYPE_SPATIAL_INTENT_SYNTHESIS_PROFILE), true);
});

test("synthesis derives symmetric adjacency, filters environment and classifies bounds", async () => {
  const input = await fixture(); const result = await api.synthesizePrototypeSpatialIntent(input);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(validatePrototypeSpatialIntentJson(result.canonicalSpatialIntentJson).valid, true);
  assert.deepEqual(result.spatialIntent.placements.map((item) => [item.id, item.clearanceClass]), [["placement-prop", "compact"], ["placement-character", "human"]]);
  assert.deepEqual(result.spatialIntent.zones.map((zone) => [zone.id, zone.adjacentZoneIds]), [["zone-a", ["zone-b"]], ["zone-b", ["zone-a"]]]);
  assert.equal(result.spatialIntent.nodeContexts.every((item) => !item.visiblePlacementIds.includes("placement-environment")), true);
  assert.equal(Object.isFrozen(result), true); assert.equal(Object.isFrozen(result.spatialIntent.placements), true);
});

test("classification uses explicit real footprint threshold without prose", async () => {
  const input = await fixture(); const bundle = JSON.parse(input.assetBundleJson);
  bundle.materializations[1].assets[0].metrics.boundsMm = { min: [-601, 0, -601], max: [601, 1800, 601] };
  input.assetBundleJson = canonicalizeJsonValue(bundle);
  const result = await api.synthesizePrototypeSpatialIntent(input);
  assert.equal(result.ok, true); assert.equal(result.spatialIntent.placements[0].clearanceClass, "large");
});

test("canonical, identity, profile and cardinality failures are static", async () => {
  async function rebindBlueprintHash(input) {
    const bundle = JSON.parse(input.assetBundleJson);
    bundle.blueprint.canonicalSha256 = await sha256(input.sceneBlueprintJson);
    input.assetBundleJson = canonicalizeJsonValue(bundle);
  }
  for (const [code, mutate] of [
    ["PROTOTYPE_SPATIAL_SYNTHESIS_BLUEPRINT_INVALID", (input) => { input.sceneBlueprintJson += "\n"; }],
    ["PROTOTYPE_SPATIAL_SYNTHESIS_IDENTITY_MISMATCH", (input) => { const value = JSON.parse(input.sceneBlueprintJson); value.scene.title = "Changed"; input.sceneBlueprintJson = canonicalizeJsonValue(value); }],
    ["PROTOTYPE_SPATIAL_SYNTHESIS_IDENTITY_MISMATCH", (input) => { const value = JSON.parse(input.assetBundleJson); value.blueprint.assetBriefs[1].kind = "character-placeholder"; input.assetBundleJson = canonicalizeJsonValue(value); }],
    ["PROTOTYPE_SPATIAL_SYNTHESIS_BLUEPRINT_REFERENCE_INVALID", async (input) => { const value = JSON.parse(input.sceneBlueprintJson); value.nodeBindings[0].zoneId = "zone-missing"; input.sceneBlueprintJson = canonicalizeJsonValue(value); await rebindBlueprintHash(input); }],
    ["PROTOTYPE_SPATIAL_SYNTHESIS_PLACEMENT_CARDINALITY_INVALID", async (input) => { const value = JSON.parse(input.sceneBlueprintJson); const removed = value.placements.pop(); for (const binding of value.nodeBindings) binding.visiblePlacementIds = binding.visiblePlacementIds.filter((id) => id !== removed.id); input.sceneBlueprintJson = canonicalizeJsonValue(value); await rebindBlueprintHash(input); }],
    ["PROTOTYPE_SPATIAL_SYNTHESIS_PROFILE_UNSUPPORTED", async (input) => { const value = JSON.parse(input.sceneBlueprintJson); value.zones.push({ id: "zone-c", label: "C", description: "C" }, { id: "zone-d", label: "D", description: "D" }, { id: "zone-e", label: "E", description: "E" }); input.sceneBlueprintJson = canonicalizeJsonValue(value); await rebindBlueprintHash(input); }],
  ]) {
    const input = await fixture(); await mutate(input); const result = await api.synthesizePrototypeSpatialIntent(input);
    assert.equal(result.ok, false); assert.equal(result.diagnostics[0].code, code);
    assert.deepEqual(Object.keys(result.diagnostics[0]), ["phase", "severity", "code", "path", "message"]);
  }
});

test("descriptor capture avoids getters and operational errors are static", async () => {
  const input = await fixture(); let reads = 0; const hostile = {};
  for (const [key, value] of Object.entries(input)) Object.defineProperty(hostile, key, { enumerable: true, get() { reads += 1; return value; } });
  const rejected = await api.synthesizePrototypeSpatialIntent(hostile);
  assert.equal(rejected.ok, false); assert.equal(reads, 0);
  await assert.rejects(api.solvePrototypeSpatialLayout({}), (error) => error.name === "PrototypeSpatialSolverOperationalError" && error.code === "PROTOTYPE_SPATIAL_SOLVER_INTERNAL_ERROR" && error.message === error.code && !("cause" in error));
});

test("twenty runs are deterministic and inputs remain unchanged", async () => {
  const input = await fixture(); const before = JSON.stringify(input);
  const outputs = [];
  for (let index = 0; index < 20; index += 1) outputs.push((await api.synthesizePrototypeSpatialIntent(input)).canonicalSpatialIntentJson);
  assert.equal(new Set(outputs).size, 1); assert.equal(JSON.stringify(input), before);
});

test("CLI publishes one canonical artifact transactionally under the temp root", async () => {
  const input = await fixture();
  const tempRoot = path.join(path.parse(process.cwd()).root, "tmp");
  await mkdir(tempRoot, { recursive: true });
  const root = await mkdtemp(path.join(tempRoot, "matrix-oasis-r14-synthesis-test-"));
  try {
    const files = {
      blueprint: path.join(root, "scene-blueprint.json"), runtime: path.join(root, "runtime-game-pack.json"),
      receipt: path.join(root, "runtime-receipt.json"), assets: path.join(root, "prototype-asset-bundle.json"),
    };
    await Promise.all([
      writeFile(files.blueprint, input.sceneBlueprintJson), writeFile(files.runtime, input.runtimeGamePackJson),
      writeFile(files.receipt, input.runtimeReceiptJson), writeFile(files.assets, input.assetBundleJson),
    ]);
    const output = path.join(root, "output");
    const result = await execFile(process.execPath, ["scripts/synthesize-spatial-intent.mjs", "--scene-blueprint", files.blueprint, "--runtime-pack", files.runtime, "--runtime-receipt", files.receipt, "--asset-bundle", files.assets, "--output", output], { cwd: path.resolve(new URL("../../..", import.meta.url).pathname.slice(process.platform === "win32" ? 1 : 0)) });
    assert.deepEqual(JSON.parse(result.stdout), { ok: true, artifact: "prototype-spatial-intent.json" });
    const text = await readFile(path.join(output, "prototype-spatial-intent.json"), "utf8");
    assert.equal(validatePrototypeSpatialIntentJson(text).valid, true);
    await assert.rejects(mkdir(output), /EEXIST/u);
  } finally { await rm(root, { recursive: true, force: true }); }
});
