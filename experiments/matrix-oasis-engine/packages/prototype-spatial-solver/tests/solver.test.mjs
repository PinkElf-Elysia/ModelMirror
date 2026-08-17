import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import {
  validatePrototypeEnvironmentFactsJson,
  validatePrototypeSpatialIntentJson,
} from "@matrix-oasis/prototype-spatial-planning-contracts";
import { validatePrototypeSpatialSolutionJson } from "@matrix-oasis/prototype-spatial-solution-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { solvePrototypeSpatialLayout, synthesizePrototypeSpatialIntent } from "../src/index.mjs";

const execFile = promisify(execFileCallback);

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return `sha256:${Buffer.from(digest).toString("hex")}`;
}

function metrics(size, height = 1800) {
  return { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 100, maxTextureWidth: 512, maxTextureHeight: 512, boundsMm: { min: [-Math.floor(size / 2), 0, -Math.floor(size / 2)], max: [Math.ceil(size / 2), height, Math.ceil(size / 2)] } };
}

function assetFile(id, assetPath, roles, profile, size, character) {
  const characterValue = character.repeat(64);
  return { id, path: assetPath, format: "glb", roles, normalizationProfile: profile, byteLength: 1024, sha256: `sha256:${characterValue}`, metrics: metrics(size) };
}

async function buildInput() {
  const authoringText = await readFile(new URL("../../../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8");
  const compiled = await compileAuthoringGamePackJson(authoringText);
  assert.equal(compiled.ok, true);
  const runtimeGamePackJson = compiled.canonicalJson;
  const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
  const blueprint = {
    format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion, title: "Neutral", environmentPrompt: "A neutral connected room.", visualStylePrompt: "Simple neutral materials." },
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
    nodeBindings: compiled.runtimePack.nodes.map((node, index) => ({ nodeId: node.id, zoneId: index % 2 === 0 ? "zone-a" : "zone-b", visiblePlacementIds: ["placement-environment", "placement-prop", "placement-character"] })),
  };
  const sceneBlueprintJson = canonicalizeJsonValue(blueprint);
  const assetBundle = {
    format: "matrix-oasis.prototype-asset-bundle", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: blueprint.scene.id, contentVersion: blueprint.scene.contentVersion, title: blueprint.scene.title },
    blueprint: { format: blueprint.format, formatVersion: blueprint.formatVersion, canonicalSha256: await sha256(sceneBlueprintJson), assetBriefs: blueprint.assetBriefs.map(({ id, kind, entityId, roles }) => ({ id, kind, entityId, roles })) },
    runtimeIdentity: { format: compiled.runtimePack.format, formatVersion: compiled.runtimePack.formatVersion, id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion, authoringCanonicalSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`, artifactSha256: `sha256:${compiled.receipt.artifact.sha256}` },
    environmentTemplate: "kenney-prototype-room-v1",
    materializations: [
      { assetBriefId: "brief-environment", source: { type: "builtin-template", template: "kenney-prototype-room-v1" }, assets: [assetFile("asset-environment", "assets/environment.glb", ["visual", "collider"], "kenney-prototype-room-v1", 20000, "a")] },
      { assetBriefId: "brief-prop", source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" }, assets: [assetFile("asset-prop", "assets/prop.glb", ["visual", "collider"], "matrix-oasis.glb-normalization/1", 800, "b")] },
      { assetBriefId: "brief-character", source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" }, assets: [assetFile("asset-character", "assets/character.glb", ["visual", "collider"], "matrix-oasis.glb-normalization/1", 700, "c")] },
    ],
  };
  const assetBundleJson = canonicalizeJsonValue(assetBundle);
  assert.equal(validatePrototypeAssetBundleJson(assetBundleJson).valid, true);
  const synthesis = await synthesizePrototypeSpatialIntent({ sceneBlueprintJson, runtimeGamePackJson, runtimeReceiptJson, assetBundleJson });
  assert.equal(synthesis.ok, true, JSON.stringify(synthesis));
  const floorAnchors = [];
  for (let x = -9000; x <= 9000; x += 1000) for (let z = -9000; z <= 9000; z += 1000) {
    floorAnchors.push({ id: `floor-${String(floorAnchors.length).padStart(4, "0")}`, positionMm: [x, 0, z], normalMicros: [0, 1_000_000, 0], clearanceRadiusMm: 350, clearanceHeightMm: 1800, ceilingHeightMm: 4000, componentIndex: 0, polygonIndex: 0, capsuleClearanceVerified: true });
  }
  const facts = {
    format: "matrix-oasis.prototype-environment-facts", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    source: {
      scene: synthesis.spatialIntent.scene,
      blueprint: synthesis.spatialIntent.blueprint,
      runtime: synthesis.spatialIntent.runtime,
      spatialEnvironmentBundle: { format: "matrix-oasis.prototype-spatial-environment-bundle", formatVersion: "0.1.0", canonicalSha256: `sha256:${"d".repeat(64)}` },
      environmentBundleSha256: `sha256:${"e".repeat(64)}`,
      collider: { format: "glb", byteLength: 4096, sha256: `sha256:${"f".repeat(64)}` },
      calibration: { coordinateTransform: "spz-raw-ply-to-godot-v1", metricScaleMicros: 1_000_000, groundPlaneOffsetMm: 0, godotTranslationMm: [0, 0, 0], godotRotationMilliDegrees: [0, 0, 0] },
      analysisTransform: { profile: "spatial-environment-calibration-v1", sourceCanonicalSha256: `sha256:${"1".repeat(64)}`, eulerOrder: "YXZ", root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] }, collider: { localTranslationMm: [0, 0, 0], scaleMicros: 1_000_000 } },
    },
    coordinateSystem: { handedness: "right", upAxis: "Y", unit: "millimeter", eulerOrder: "YXZ" },
    analysisProfile: { playerRadiusMm: 350, playerHeightMm: 1800, floorSnapMm: 200, maxSlopeMilliDegrees: 45_000 },
    environmentBounds: { minimumMm: [-10_000, -100, -10_000], maximumMm: [10_000, 4000, 10_000] },
    navigationMesh: {
      verticesMm: [[-10_000, 0, -10_000], [-10_000, 0, 10_000], [10_000, 0, 10_000], [10_000, 0, -10_000]],
      polygons: [{ vertexIndices: [0, 1, 2, 3], componentIndex: 0 }],
      components: [{ index: 0, polygonIndices: [0], bounds: { minimumMm: [-10_000, 0, -10_000], maximumMm: [10_000, 0, 10_000] } }],
    },
    floorAnchors,
    wallAnchors: [],
  };
  const environmentFactsJson = canonicalizeJsonValue(facts);
  assert.equal(validatePrototypeEnvironmentFactsJson(environmentFactsJson).valid, true, JSON.stringify(validatePrototypeEnvironmentFactsJson(environmentFactsJson)));
  assert.equal(validatePrototypeSpatialIntentJson(synthesis.canonicalSpatialIntentJson).valid, true);
  return { spatialIntentJson: synthesis.canonicalSpatialIntentJson, environmentFactsJson, assetBundleJson, runtimeGamePackJson, runtimeReceiptJson };
}

test("solver produces a canonical frozen solution with shared same-zone stations", async () => {
  const input = await buildInput();
  const result = await solvePrototypeSpatialLayout(input);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(validatePrototypeSpatialSolutionJson(result.canonicalSpatialSolutionJson).valid, true);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.spatialSolution.nodeContexts), true);
  const byZone = new Map();
  for (const context of result.spatialSolution.nodeContexts) {
    const station = canonicalizeJsonValue({ playerSpawn: context.playerSpawn, positionMm: context.actionTerminal.positionMm });
    if (byZone.has(context.zoneId)) assert.equal(station, byZone.get(context.zoneId));
    else byZone.set(context.zoneId, station);
  }
  assert.equal(result.spatialSolution.placements.length, 2);
  assert.equal(result.spatialSolution.proof.allHardConstraintsSatisfied, true);
});

test("twenty solver runs are byte deterministic and inputs remain unchanged", async () => {
  const input = await buildInput();
  const before = JSON.stringify(input);
  const outputs = [];
  const reports = [];
  for (let index = 0; index < 20; index += 1) {
    const result = await solvePrototypeSpatialLayout(input);
    assert.equal(result.ok, true, JSON.stringify(result));
    outputs.push(result.canonicalSpatialSolutionJson);
    reports.push(result.canonicalSpatialSolutionReportJson);
  }
  assert.equal(new Set(outputs).size, 1);
  assert.equal(new Set(reports).size, 1);
  assert.equal(JSON.stringify(input), before);
});

test("identity drift, insufficient capacity and conflicting constraints fail closed", async () => {
  const identity = await buildInput();
  const drift = JSON.parse(identity.spatialIntentJson);
  drift.assetBundle.canonicalSha256 = `sha256:${"9".repeat(64)}`;
  identity.spatialIntentJson = canonicalizeJsonValue(drift);
  assert.equal((await solvePrototypeSpatialLayout(identity)).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLVER_IDENTITY_MISMATCH");

  const capacity = await buildInput();
  const facts = JSON.parse(capacity.environmentFactsJson);
  facts.navigationMesh.verticesMm = [[-1000, 0, -1000], [-1000, 0, 1000], [1000, 0, 1000], [1000, 0, -1000]];
  facts.navigationMesh.components[0].bounds = { minimumMm: [-1000, 0, -1000], maximumMm: [1000, 0, 1000] };
  facts.environmentBounds = { minimumMm: [-1000, -100, -1000], maximumMm: [1000, 4000, 1000] };
  facts.floorAnchors = facts.floorAnchors.filter((anchor) => Math.abs(anchor.positionMm[0]) <= 1000 && Math.abs(anchor.positionMm[2]) <= 1000);
  capacity.environmentFactsJson = canonicalizeJsonValue(facts);
  assert.equal(validatePrototypeEnvironmentFactsJson(capacity.environmentFactsJson).valid, true);
  assert.equal((await solvePrototypeSpatialLayout(capacity)).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLVER_COMPONENT_CAPACITY_INSUFFICIENT");

  const conflict = await buildInput();
  const intent = JSON.parse(conflict.spatialIntentJson);
  intent.placements[0].near = [{ placementId: intent.placements[1].id, distanceMm: 1000 }];
  intent.placements[1].separate = [{ placementId: intent.placements[0].id, distanceMm: 8000 }];
  conflict.spatialIntentJson = canonicalizeJsonValue(intent);
  assert.equal(validatePrototypeSpatialIntentJson(conflict.spatialIntentJson).valid, true);
  assert.equal((await solvePrototypeSpatialLayout(conflict)).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLVER_NO_SOLUTION");
});

test("zero placements and placement-facing cycles use the same solver path", async () => {
  const zero = await buildInput();
  const zeroIntent = JSON.parse(zero.spatialIntentJson);
  zeroIntent.placements = [];
  for (const context of zeroIntent.nodeContexts) context.visiblePlacementIds = [];
  zero.spatialIntentJson = canonicalizeJsonValue(zeroIntent);
  const zeroResult = await solvePrototypeSpatialLayout(zero);
  assert.equal(zeroResult.ok, true, JSON.stringify(zeroResult));
  assert.deepEqual(zeroResult.spatialSolution.placements, []);

  const cycle = await buildInput();
  const cycleIntent = JSON.parse(cycle.spatialIntentJson);
  cycleIntent.placements[0].facing = { kind: "placement", placementId: cycleIntent.placements[1].id };
  cycleIntent.placements[1].facing = { kind: "placement", placementId: cycleIntent.placements[0].id };
  cycle.spatialIntentJson = canonicalizeJsonValue(cycleIntent);
  assert.equal(validatePrototypeSpatialIntentJson(cycle.spatialIntentJson).valid, true);
  const cycleResult = await solvePrototypeSpatialLayout(cycle);
  assert.equal(cycleResult.ok, true, JSON.stringify(cycleResult));
  assert.equal(cycleResult.spatialSolution.placements.every((item) => item.rotationMilliDegrees[1] >= -180000 && item.rotationMilliDegrees[1] <= 180000), true);
});

test("wall support and four-zone boundaries solve while a fifth zone is rejected", async () => {
  const wall = await buildInput();
  const initial = await solvePrototypeSpatialLayout(wall);
  assert.equal(initial.ok, true);
  const intent = JSON.parse(wall.spatialIntentJson);
  const facts = JSON.parse(wall.environmentFactsJson);
  const prop = intent.placements.find((item) => item.id === "placement-prop");
  prop.support = "wall";
  const domain = initial.spatialSolution.navigation.zoneDomains.find((item) => item.zoneId === prop.zoneId);
  const floorId = domain.floorAnchorIds.at(-1);
  const floor = facts.floorAnchors.find((item) => item.id === floorId);
  facts.wallAnchors = [{ id: "wall-0000", positionMm: [floor.positionMm[0], 1200, floor.positionMm[2]], normalMicros: [1_000_000, 0, 0], availableWidthMm: 2400, availableHeightMm: 2400, nearestFloorAnchorId: floorId }];
  wall.spatialIntentJson = canonicalizeJsonValue(intent);
  wall.environmentFactsJson = canonicalizeJsonValue(facts);
  assert.equal(validatePrototypeSpatialIntentJson(wall.spatialIntentJson).valid, true);
  assert.equal(validatePrototypeEnvironmentFactsJson(wall.environmentFactsJson).valid, true);
  const wallResult = await solvePrototypeSpatialLayout(wall);
  assert.equal(wallResult.ok, true, JSON.stringify(wallResult));
  assert.equal(wallResult.spatialSolution.placements.find((item) => item.placementId === "placement-prop").anchorKind, "wall");

  const zones = await buildInput();
  const four = JSON.parse(zones.spatialIntentJson);
  four.zones.push({ id: "zone-c", adjacentZoneIds: [] }, { id: "zone-d", adjacentZoneIds: [] });
  zones.spatialIntentJson = canonicalizeJsonValue(four);
  assert.equal(validatePrototypeSpatialIntentJson(zones.spatialIntentJson).valid, true);
  assert.equal((await solvePrototypeSpatialLayout(zones)).ok, true);
  four.zones.push({ id: "zone-e", adjacentZoneIds: [] });
  zones.spatialIntentJson = canonicalizeJsonValue(four);
  assert.equal(validatePrototypeSpatialIntentJson(zones.spatialIntentJson).valid, true);
  assert.equal((await solvePrototypeSpatialLayout(zones)).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLVER_PROFILE_UNSUPPORTED");
});

test("six placements solve deterministically while seven exceed the fixed profile", async () => {
  const input = await buildInput();
  const intent = JSON.parse(input.spatialIntentJson);
  const assetBundle = JSON.parse(input.assetBundleJson);
  for (let index = 0; index < 5; index += 1) {
    const briefId = `brief-extra-${index}`;
    assetBundle.blueprint.assetBriefs.push({ id: briefId, kind: "prop", entityId: "control-unit", roles: ["visual", "collider"] });
    assetBundle.materializations.push({
      assetBriefId: briefId,
      source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" },
      assets: [assetFile(`asset-extra-${index}`, `assets/extra-${index}.glb`, ["visual", "collider"], "matrix-oasis.glb-normalization/1", 500, String(index + 2))],
    });
    intent.placements.push({ id: `placement-extra-${index}`, assetBriefId: briefId, zoneId: "zone-a", support: "floor", anchor: "free", facing: { kind: "zone-center" }, near: [], separate: [], clearanceClass: "compact" });
  }
  input.assetBundleJson = canonicalizeJsonValue(assetBundle);
  assert.equal(validatePrototypeAssetBundleJson(input.assetBundleJson).valid, true, JSON.stringify(validatePrototypeAssetBundleJson(input.assetBundleJson)));
  intent.assetBundle.canonicalSha256 = await sha256(input.assetBundleJson);
  const seven = structuredClone(intent);
  intent.placements.pop();
  input.spatialIntentJson = canonicalizeJsonValue(intent);
  assert.equal(validatePrototypeSpatialIntentJson(input.spatialIntentJson).valid, true);
  const sixResult = await solvePrototypeSpatialLayout(input);
  assert.equal(sixResult.ok, true, JSON.stringify(sixResult));
  assert.equal(sixResult.spatialSolution.placements.length, 6);
  input.spatialIntentJson = canonicalizeJsonValue(seven);
  assert.equal(validatePrototypeSpatialIntentJson(input.spatialIntentJson).valid, true);
  assert.equal((await solvePrototypeSpatialLayout(input)).diagnostics[0].code, "PROTOTYPE_SPATIAL_SOLVER_PROFILE_UNSUPPORTED");
});

test("solver CLI publishes the canonical solution and report as one directory", async () => {
  const input = await buildInput();
  const tempRoot = path.join(path.parse(process.cwd()).root, "tmp");
  await mkdir(tempRoot, { recursive: true });
  const root = await mkdtemp(path.join(tempRoot, "matrix-oasis-r14-solver-test-"));
  try {
    const names = {
      intent: path.join(root, "intent.json"), facts: path.join(root, "facts.json"), assets: path.join(root, "assets.json"),
      runtime: path.join(root, "runtime.json"), receipt: path.join(root, "receipt.json"),
    };
    await Promise.all([
      writeFile(names.intent, input.spatialIntentJson), writeFile(names.facts, input.environmentFactsJson),
      writeFile(names.assets, input.assetBundleJson), writeFile(names.runtime, input.runtimeGamePackJson),
      writeFile(names.receipt, input.runtimeReceiptJson),
    ]);
    const output = path.join(root, "output");
    const result = await execFile(process.execPath, [
      "scripts/solve-spatial-layout.mjs", "--spatial-intent", names.intent, "--environment-facts", names.facts,
      "--asset-bundle", names.assets, "--runtime-pack", names.runtime, "--runtime-receipt", names.receipt, "--output", output,
    ], { cwd: path.resolve(new URL("../../..", import.meta.url).pathname.slice(process.platform === "win32" ? 1 : 0)) });
    assert.deepEqual(JSON.parse(result.stdout), { ok: true, artifacts: ["prototype-spatial-solution.json", "prototype-spatial-solution-report.json"] });
    const solution = await readFile(path.join(output, "prototype-spatial-solution.json"), "utf8");
    const report = await readFile(path.join(output, "prototype-spatial-solution-report.json"), "utf8");
    assert.equal(validatePrototypeSpatialSolutionJson(solution).valid, true);
    assert.equal(canonicalizeJsonValue(JSON.parse(report)), report);
    await assert.rejects(mkdir(output), /EEXIST/u);
  } finally { await rm(root, { recursive: true, force: true }); }
});
