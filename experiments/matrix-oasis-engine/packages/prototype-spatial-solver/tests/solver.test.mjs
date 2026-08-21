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
import {
  diverseStationCandidates,
  solvePrototypeSpatialLayoutInternal,
  spatialCandidateRegionContains,
  spatialWalkableEnvelopeCandidateRegion,
  spatialPlacementCandidateKey,
  spatialStationCandidateKey,
  spatialTerminalCandidateKey,
  spatialTerminalCandidateKeys,
  navigationFootprintSupported,
  terminalApproachIsBroadside,
  terminalBoxesInteractableFrom,
  terminalBoxesReachableFromAnchors,
} from "../src/solver.mjs";

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
  assert.equal(result.spatialSolution.nodeContexts.every((context) =>
    context.playerSpawn.floorAnchorId !== context.actionTerminal.approachFloorAnchorId &&
    context.approachPathFloorAnchorIds.length === 2 &&
    context.approachPathFloorAnchorIds[0] === context.playerSpawn.floorAnchorId &&
    context.approachPathFloorAnchorIds[1] === context.actionTerminal.approachFloorAnchorId), true);
  assert.equal(result.spatialSolution.nodeContexts.every((context) => {
    const footprint = context.actionTerminal.footprint;
    const yaw = context.actionTerminal.yawMilliDegrees / 90_000;
    for (let index = 0; index < context.actionTerminal.actionCount; index += 1) {
      const row = Math.floor(index / footprint.columns);
      const column = index % footprint.columns;
      const firstIndex = row * footprint.columns;
      const rowCount = Math.min(footprint.columns, context.actionTerminal.actionCount - firstIndex);
      const center = [context.actionTerminal.positionMm[0] + ((column - ((rowCount - 1) / 2)) * 1700),
        context.actionTerminal.positionMm[1], context.actionTerminal.positionMm[2] - 2400 - (row * 2250)];
      const turns = ((yaw % 4) + 4) % 4;
      const localX = center[0] - context.actionTerminal.positionMm[0];
      const localZ = center[2] - context.actionTerminal.positionMm[2];
      const rotated = turns === 0 ? center : turns === 1 ? [context.actionTerminal.positionMm[0] + localZ, center[1], context.actionTerminal.positionMm[2] - localX] : turns === 2 ? [context.actionTerminal.positionMm[0] - localX, center[1], context.actionTerminal.positionMm[2] - localZ] : [context.actionTerminal.positionMm[0] - localZ, center[1], context.actionTerminal.positionMm[2] + localX];
      if (Math.hypot(context.playerSpawn.positionMm[0] - rotated[0], context.playerSpawn.positionMm[2] - rotated[2]) < 3000) return false;
    }
    return true;
  }), true);
  const facts = JSON.parse(input.environmentFactsJson);
  const floorAnchorsById = new Map(facts.floorAnchors.map((anchor) => [anchor.id, anchor]));
  const domainsByZone = new Map(result.spatialSolution.navigation.zoneDomains.map((domain) =>
    [domain.zoneId, new Set(domain.floorAnchorIds)]));
  assert.equal(result.spatialSolution.nodeContexts.every((context) =>
    context.actionTerminal.terminalSupports.length === context.actionTerminal.actionCount &&
    context.actionTerminal.terminalSupports.every((support) =>
      domainsByZone.get(context.zoneId).has(support.floorAnchorId) &&
      floorAnchorsById.get(support.floorAnchorId)?.positionMm[1] === support.baseHeightMm)), true);
  assert.equal(result.spatialSolution.nodeContexts.every((context) => {
    const terminal = context.actionTerminal;
    const turns = terminal.yawMilliDegrees / 90_000;
    const boxes = [];
    for (let index = 0; index < terminal.actionCount; index += 1) {
      const row = Math.floor(index / terminal.footprint.columns);
      const column = index % terminal.footprint.columns;
      const rowCount = Math.min(terminal.footprint.columns,
        terminal.actionCount - (row * terminal.footprint.columns));
      const localX = (column - ((rowCount - 1) / 2)) * 1_700;
      const localZ = -2_400 - (row * 2_250);
      const normalized = ((turns % 4) + 4) % 4;
      const center = normalized === 0 ? [terminal.positionMm[0] + localX, terminal.positionMm[1], terminal.positionMm[2] + localZ]
        : normalized === 1 ? [terminal.positionMm[0] + localZ, terminal.positionMm[1], terminal.positionMm[2] - localX]
          : normalized === 2 ? [terminal.positionMm[0] - localX, terminal.positionMm[1], terminal.positionMm[2] - localZ]
            : [terminal.positionMm[0] - localZ, terminal.positionMm[1], terminal.positionMm[2] + localX];
      boxes.push({ center });
    }
    const approach = floorAnchorsById.get(terminal.approachFloorAnchorId)?.positionMm;
    return approach && terminalApproachIsBroadside(approach, boxes, turns);
  }), true);
  const placementsById = new Map(result.spatialSolution.placements.map((placement) =>
    [placement.placementId, placement]));
  const intent = JSON.parse(input.spatialIntentJson);
  const placementIntentById = new Map(intent.placements.map((placement) => [placement.id, placement]));
  const clearanceByClass = { compact: 250, human: 350, large: 600 };
  assert.equal(result.spatialSolution.nodeContexts.every((context) => {
    const approach = floorAnchorsById.get(context.actionTerminal.approachFloorAnchorId).positionMm;
    return context.visiblePlacementIds.every((placementId) => {
      const placement = placementsById.get(placementId);
      const placementIntent = placementIntentById.get(placementId);
      const radius = Math.ceil(Math.hypot(placement.footprint.widthMm, placement.footprint.depthMm) / 2);
      return Math.hypot(placement.positionMm[0] - approach[0], placement.positionMm[2] - approach[2]) >=
        radius + clearanceByClass[placementIntent.clearanceClass] + 350;
    });
  }), true);
  const stationByZone = new Map(result.spatialSolution.nodeContexts.map((context) => [context.zoneId, context]));
  assert.equal(result.spatialSolution.placements.every((placement) => {
    const station = stationByZone.get(placementIntentById.get(placement.placementId)?.zoneId);
    const stationCenter = [0, 1, 2].map((axis) => Math.round((station.playerSpawn.positionMm[axis] +
      station.actionTerminal.positionMm[axis] +
      floorAnchorsById.get(station.actionTerminal.approachFloorAnchorId).positionMm[axis]) / 3));
    const radius = Math.ceil(Math.hypot(placement.footprint.widthMm, placement.footprint.depthMm) / 2);
    return Math.hypot(placement.positionMm[0] - stationCenter[0],
      placement.positionMm[2] - stationCenter[2]) + radius + 600 <= 6000;
  }), true);
  assert.equal(result.spatialSolution.nodeContexts.every((context) => {
    const terminal = context.actionTerminal;
    const turns = ((terminal.yawMilliDegrees / 90_000) % 4 + 4) % 4;
    for (let index = 0; index < terminal.actionCount; index += 1) {
      const row = Math.floor(index / terminal.footprint.columns);
      const column = index % terminal.footprint.columns;
      const rowCount = Math.min(terminal.footprint.columns,
        terminal.actionCount - (row * terminal.footprint.columns));
      const localX = (column - ((rowCount - 1) / 2)) * 1_700;
      const localZ = -2_400 - (row * 2_250);
      const center = turns === 0 ? [terminal.positionMm[0] + localX, terminal.positionMm[2] + localZ]
        : turns === 1 ? [terminal.positionMm[0] + localZ, terminal.positionMm[2] - localX]
          : turns === 2 ? [terminal.positionMm[0] - localX, terminal.positionMm[2] - localZ]
            : [terminal.positionMm[0] - localZ, terminal.positionMm[2] + localX];
      const terminalWidth = turns % 2 === 0 ? 1_250 : 500;
      const terminalDepth = turns % 2 === 0 ? 500 : 1_250;
      for (const placementId of context.visiblePlacementIds) {
        const placement = placementsById.get(placementId);
        if (!placement) return false;
        if (Math.abs(placement.positionMm[0] - center[0]) <
            (placement.footprint.widthMm + terminalWidth) / 2 &&
            Math.abs(placement.positionMm[2] - center[1]) <
            (placement.footprint.depthMm + terminalDepth) / 2) return false;
      }
    }
    return true;
  }), true);
  assert.equal(result.spatialSolution.proof.allHardConstraintsSatisfied, true);
});

test("upright assets reject walkable slopes that exceed the floor-contact tolerance", async () => {
  const input = await buildInput();
  const baseline = await solvePrototypeSpatialLayout(input);
  assert.equal(baseline.ok, true, JSON.stringify(baseline));
  const target = baseline.spatialSolution.placements[0];
  const facts = JSON.parse(input.environmentFactsJson);
  const anchor = facts.floorAnchors.find((item) => item.id === target.anchorId);
  anchor.normalMicros = [258_819, 965_926, 0];
  input.environmentFactsJson = canonicalizeJsonValue(facts);
  assert.equal(validatePrototypeEnvironmentFactsJson(input.environmentFactsJson).valid, true);
  const result = await solvePrototypeSpatialLayout(input);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.notEqual(result.spatialSolution.placements[0].anchorId, target.anchorId);
});

test("upright support ignores unrelated nearby heights and defers actual contact to Godot verification", async () => {
  const input = await buildInput();
  const baseline = await solvePrototypeSpatialLayout(input);
  assert.equal(baseline.ok, true, JSON.stringify(baseline));
  const target = baseline.spatialSolution.placements[0];
  const facts = JSON.parse(input.environmentFactsJson);
  const anchor = facts.floorAnchors.find((item) => item.id === target.anchorId);
  const neighbor = facts.floorAnchors.filter((item) => item.id !== anchor.id).sort((left, right) =>
    Math.hypot(left.positionMm[0] - anchor.positionMm[0], left.positionMm[2] - anchor.positionMm[2]) -
    Math.hypot(right.positionMm[0] - anchor.positionMm[0], right.positionMm[2] - anchor.positionMm[2]))[0];
  neighbor.positionMm[1] = 100;
  input.environmentFactsJson = canonicalizeJsonValue(facts);
  assert.equal(validatePrototypeEnvironmentFactsJson(input.environmentFactsJson).valid, true);
  const result = await solvePrototypeSpatialLayout(input);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.spatialSolution.placements[0].anchorId, target.anchorId);
});

test("internal physical no-good evidence deterministically advances the same bounded search", async () => {
  const input = await buildInput();
  const baseline = await solvePrototypeSpatialLayout(input);
  assert.equal(baseline.ok, true, JSON.stringify(baseline));
  const target = baseline.spatialSolution.placements[0];
  const key = spatialPlacementCandidateKey(target);
  assert.equal(typeof key, "string");
  const retried = await solvePrototypeSpatialLayoutInternal(input, new Set([key]));
  assert.equal(retried.ok, true, JSON.stringify(retried));
  assert.notEqual(retried.spatialSolution.placements[0].anchorId, target.anchorId);
  assert.equal((await solvePrototypeSpatialLayoutInternal(input, new Set([1]))).diagnostics[0].code,
    "PROTOTYPE_SPATIAL_SOLVER_INPUT_INVALID");
});

test("station spawn and approach honor the frozen floor-snap tolerance", async () => {
  const input = await buildInput();
  const baseline = await solvePrototypeSpatialLayout(input);
  assert.equal(baseline.ok, true, JSON.stringify(baseline));
  const facts = JSON.parse(input.environmentFactsJson);
  for (const anchor of facts.floorAnchors) anchor.positionMm[1] = 150;
  for (const vertex of facts.navigationMesh.verticesMm) vertex[1] = 150;
  facts.navigationMesh.components[0].bounds.minimumMm[1] = 150;
  facts.navigationMesh.components[0].bounds.maximumMm[1] = 150;
  input.environmentFactsJson = canonicalizeJsonValue(facts);
  assert.equal(validatePrototypeEnvironmentFactsJson(input.environmentFactsJson).valid, true);
  const result = await solvePrototypeSpatialLayout(input);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.spatialSolution.nodeContexts.every((context) =>
    context.playerSpawn.positionMm[1] === 150 && context.actionTerminal.positionMm[1] === 150 &&
    context.actionTerminal.terminalSupports.every((support) => support.baseHeightMm === 150)), true);
});

test("internal physical station evidence advances to a distinct deterministic station", async () => {
  const input = await buildInput();
  const baseline = await solvePrototypeSpatialLayout(input);
  assert.equal(baseline.ok, true, JSON.stringify(baseline));
  const target = baseline.spatialSolution.nodeContexts[0];
  const key = spatialStationCandidateKey(target);
  assert.equal(typeof key, "string");
  const terminalKey = spatialTerminalCandidateKey(target);
  const differentApproach = structuredClone(target);
  differentApproach.actionTerminal.approachFloorAnchorId = `${target.actionTerminal.approachFloorAnchorId}-other`;
  assert.notEqual(spatialStationCandidateKey(differentApproach), key);
  assert.equal(spatialTerminalCandidateKey(differentApproach), terminalKey);
  const differentColumns = structuredClone(target);
  differentColumns.actionTerminal.footprint.columns = target.actionTerminal.footprint.columns === 1 ? 2 : 1;
  assert.equal(spatialStationCandidateKey(differentColumns), key);
  assert.notEqual(spatialTerminalCandidateKey(differentColumns), terminalKey);
  const adaptiveCandidate = {
    zoneId: target.zoneId,
    anchor: { id: target.actionTerminal.floorAnchorId },
    quarterTurns: target.actionTerminal.yawMilliDegrees === -90_000 ? 3 : target.actionTerminal.yawMilliDegrees / 90_000,
    terminalColumns: Math.min(8, target.actionTerminal.footprint.columns + 1),
    terminalLayoutsByActionCount: new Map([[target.actionTerminal.actionCount, {
      columns: target.actionTerminal.footprint.columns,
    }]]),
  };
  assert.equal(spatialTerminalCandidateKeys(adaptiveCandidate).includes(terminalKey), true);
  const retried = await solvePrototypeSpatialLayoutInternal(input, new Set(), new Set([key]));
  assert.equal(retried.ok, true, JSON.stringify(retried));
  assert.notEqual(spatialStationCandidateKey(retried.spatialSolution.nodeContexts[0]), key);
});

test("multi-terminal layouts require per-terminal reachable interaction points, not one shared point", () => {
  const boxes = (columns, actionCount) => {
    const output = [];
    for (let index = 0; index < actionCount; index += 1) {
      const row = Math.floor(index / columns);
      const column = index % columns;
      const rowCount = Math.min(columns, actionCount - (row * columns));
      const center = [(column - ((rowCount - 1) / 2)) * 1700, 0, -2400 - (row * 2250)];
      output.push({ center, sightTarget: [center[0], 850, center[2]], widthMm: 1250, depthMm: 500 });
    }
    return output;
  };
  const layout = boxes(3, 9);
  const sharedApproach = [0, 0, -4000];
  const perTerminalApproaches = layout.map((box) => [box.center[0], 0, box.center[2] + 1000]);
  assert.equal(terminalBoxesInteractableFrom(sharedApproach, layout), false);
  assert.equal(terminalBoxesReachableFromAnchors([sharedApproach], layout), false);
  assert.equal(terminalBoxesReachableFromAnchors(perTerminalApproaches, layout), true);
});

test("station approach is broadside and player or terminal footprints cannot cross navigation edges", async () => {
  const layout = [
    { center: [-850, 0, -2400] },
    { center: [850, 0, -2400] },
    { center: [0, 0, -4650] },
  ];
  assert.equal(terminalApproachIsBroadside([0, 0, 0], layout, 0), true);
  assert.equal(terminalApproachIsBroadside([3000, 0, -3150], layout, 0), false);
  const rotated = layout.map((box) => ({ center: [-box.center[2], 0, box.center[0]] }));
  assert.equal(terminalApproachIsBroadside([1050, 0, 0], rotated, 3), true);
  assert.equal(terminalApproachIsBroadside([3150, 0, 3000], rotated, 3), false);

  const input = await buildInput();
  const facts = JSON.parse(input.environmentFactsJson);
  assert.equal(navigationFootprintSupported(facts, [0, 0, 0], 700, 700, facts.floorAnchors), true);
  assert.equal(navigationFootprintSupported(facts, [9800, 0, 0], 700, 700, facts.floorAnchors), false);
  assert.equal(navigationFootprintSupported(facts, [0, 0, 9800], 1250, 500, facts.floorAnchors), false);
});

test("dense station truncation preserves every adaptive terminal column before anchor retries", () => {
  const candidates = [];
  for (let anchorIndex = 0; anchorIndex < 64; anchorIndex += 1) {
    const anchor = { id: `anchor-${String(anchorIndex).padStart(2, "0")}`,
      positionMm: [(anchorIndex % 8) * 1000, 0, Math.floor(anchorIndex / 8) * 1000] };
    for (let terminalColumns = 1; terminalColumns <= 8; terminalColumns += 1) {
      for (let quarterTurns = 0; quarterTurns < 4; quarterTurns += 1) {
        candidates.push({ zoneId: "zone", anchor,
          approach: { id: `approach-${anchorIndex}-${terminalColumns}-${quarterTurns}`, positionMm: [anchor.positionMm[0] + 1000, 0, anchor.positionMm[2]] },
          spawn: { id: `spawn-${anchorIndex}-${terminalColumns}-${quarterTurns}`, positionMm: [anchor.positionMm[0], 0, anchor.positionMm[2] + 4000] },
          quarterTurns, terminalColumns, layoutRank: terminalColumns - 1, seedPositionMm: [0, 0, 0] });
      }
    }
  }
  const selected = diverseStationCandidates(candidates, null);
  assert.equal(selected.length, 256);
  assert.deepEqual([...new Set(selected.map((item) => item.terminalColumns))], [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.deepEqual(Object.fromEntries([...new Set(selected.map((item) => item.terminalColumns))].map((columns) =>
    [columns, selected.filter((item) => item.terminalColumns === columns).length])),
  { 1: 32, 2: 32, 3: 32, 4: 32, 5: 32, 6: 32, 7: 32, 8: 32 });
  assert.deepEqual(diverseStationCandidates(candidates, null).map(spatialStationCandidateKey),
    selected.map(spatialStationCandidateKey));
});

test("verified spatial envelopes are a hard domain-partition boundary", async () => {
  const input = await buildInput();
  const region = spatialWalkableEnvelopeCandidateRegion({
    format: "matrix-oasis.prototype-spatial-assembly",
    formatVersion: "0.1.0",
    transforms: {
      eulerOrder: "YXZ",
      root: { translationMm: [1000, 0, 2000], rotationMilliDegrees: [0, 90_000, 0] },
      walkableEnvelope: { minimumMm: [-5000, 0, -5000], maximumMm: [5000, 4000, 5000],
        wallThicknessMm: 700, binSizeMm: 250, lateralBandMm: 4000 },
    },
  });
  assert.equal(Object.isFrozen(region), true);
  assert.deepEqual(region.minimumMm, [-5950, 0, -5950]);
  assert.deepEqual(region.maximumMm, [5950, 4000, 5950]);
  assert.deepEqual(region.preferredMinimumMm, [-5000, 0, -5000]);
  assert.deepEqual(region.preferredMaximumMm, [5000, 4000, 5000]);
  assert.equal(spatialCandidateRegionContains([15000, 0, -1000], region), false);
  assert.equal(spatialCandidateRegionContains([5000, 0, -1000], region), true);

  const constrained = await solvePrototypeSpatialLayoutInternal(input, new Set(), new Set(), region);
  assert.equal(constrained.ok, true, JSON.stringify(constrained));
  const facts = JSON.parse(input.environmentFactsJson);
  const byId = new Map(facts.floorAnchors.map((anchor) => [anchor.id, anchor]));
  const selectedAnchorIds = constrained.spatialSolution.nodeContexts.flatMap((context) => [
    context.playerSpawn.floorAnchorId,
    context.actionTerminal.floorAnchorId,
    context.actionTerminal.approachFloorAnchorId,
  ]);
  assert.equal(selectedAnchorIds.every((id) => spatialCandidateRegionContains(byId.get(id).positionMm, region)), true);
  assert.equal(constrained.spatialSolution.placements.every((placement) =>
    spatialCandidateRegionContains(placement.positionMm, region)), true);
  const preferredRegion = {
    ...region,
    minimumMm: region.preferredMinimumMm,
    maximumMm: region.preferredMaximumMm,
  };
  const bundle = JSON.parse(input.assetBundleJson);
  const intent = JSON.parse(input.spatialIntentJson);
  const clearanceByClass = { compact: 250, human: 350, large: 600 };
  const boundsByBrief = new Map(bundle.materializations.map((materialization) => {
    const bounds = materialization.assets[0].metrics.boundsMm;
    return [materialization.assetBriefId, {
      widthMm: bounds.max[0] - bounds.min[0],
      depthMm: bounds.max[2] - bounds.min[2],
    }];
  }));
  const intentByPlacement = new Map(intent.placements.map((placement) => [placement.id, placement]));
  assert.equal(constrained.spatialSolution.placements.every((placement) => {
    const item = intentByPlacement.get(placement.placementId);
    const bounds = boundsByBrief.get(item.assetBriefId);
    const marginMm = Math.ceil(Math.hypot(bounds.widthMm, bounds.depthMm) / 2) +
      clearanceByClass[item.clearanceClass];
    return spatialCandidateRegionContains(placement.positionMm, preferredRegion, marginMm);
  }), true);
  const admittedIds = new Set(facts.floorAnchors.filter((anchor) =>
    spatialCandidateRegionContains(anchor.positionMm, region)).map((anchor) => anchor.id));
  assert.equal(constrained.spatialSolution.navigation.zoneDomains.every((domain) =>
    domain.floorAnchorIds.every((id) => admittedIds.has(id))), true);
  assert.equal(Object.keys(input).sort().join(","),
    "assetBundleJson,environmentFactsJson,runtimeGamePackJson,runtimeReceiptJson,spatialIntentJson");
});

test("candidate-region inverse transform matches Godot 4.6 YXZ composition", () => {
  const region = {
    rootTranslationMm: [0, 0, 0], rootRotationMilliDegrees: [20_000, 30_000, 40_000],
    minimumMm: [990, 0, 2990], maximumMm: [1010, 4000, 3010],
    preferredMinimumMm: [990, 0, 2990], preferredMaximumMm: [1010, 4000, 3010],
  };
  assert.equal(spatialCandidateRegionContains([1332, 1018, 3345], region), true);
  assert.equal(spatialCandidateRegionContains([1432, 1018, 3345], region), false);
});

test("terminal supports use the containing navigation plane at each terminal center", async () => {
  const input = await buildInput();
  const intent = JSON.parse(input.spatialIntentJson);
  intent.placements = [];
  for (const context of intent.nodeContexts) context.visiblePlacementIds = [];
  input.spatialIntentJson = canonicalizeJsonValue(intent);
  const facts = JSON.parse(input.environmentFactsJson);
  for (const anchor of facts.floorAnchors) {
    anchor.positionMm[1] = Math.round((anchor.positionMm[0] + anchor.positionMm[2]) / 200);
    anchor.normalMicros = [-5_000, 999_975, -5_000];
  }
  facts.navigationMesh.verticesMm = [
    [-10_000, -100, -10_000],
    [-10_000, 0, 10_000],
    [10_000, 100, 10_000],
    [10_000, 0, -10_000],
  ];
  facts.navigationMesh.components[0].bounds.minimumMm[1] = -100;
  facts.navigationMesh.components[0].bounds.maximumMm[1] = 100;
  input.environmentFactsJson = canonicalizeJsonValue(facts);
  assert.equal(validatePrototypeEnvironmentFactsJson(input.environmentFactsJson).valid, true);
  const result = await solvePrototypeSpatialLayout(input);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.spatialSolution.placements.length, 0);
  const anchorsById = new Map(facts.floorAnchors.map((anchor) => [anchor.id, anchor]));
  const supports = result.spatialSolution.nodeContexts.flatMap((context) => context.actionTerminal.terminalSupports);
  assert.equal(supports.length > 1, true);
  assert.equal(supports.every((support) => anchorsById.has(support.floorAnchorId)), true);
  assert.equal(new Set(supports.map((support) => support.baseHeightMm)).size > 1, true);
  assert.equal(supports.some((support) => anchorsById.get(support.floorAnchorId).positionMm[1] !== support.baseHeightMm), true);
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
  const station = initial.spatialSolution.nodeContexts.find((item) => item.zoneId === prop.zoneId);
  const approach = facts.floorAnchors.find((item) =>
    item.id === station.actionTerminal.approachFloorAnchorId);
  const stationCenter = [0, 1, 2].map((axis) => Math.round((station.playerSpawn.positionMm[axis] +
    station.actionTerminal.positionMm[axis] + approach.positionMm[axis]) / 3));
  const floor = facts.floorAnchors.filter((item) => domain.floorAnchorIds.includes(item.id)).sort((left, right) =>
    Math.abs(Math.hypot(left.positionMm[0] - stationCenter[0], left.positionMm[2] - stationCenter[2]) - 4500) -
    Math.abs(Math.hypot(right.positionMm[0] - stationCenter[0], right.positionMm[2] - stationCenter[2]) - 4500) ||
    left.id.localeCompare(right.id))[0];
  const floorId = floor.id;
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
    for (const context of intent.nodeContexts.filter((item) => item.zoneId === "zone-a")) {
      context.visiblePlacementIds.push(`placement-extra-${index}`);
    }
  }
  input.assetBundleJson = canonicalizeJsonValue(assetBundle);
  assert.equal(validatePrototypeAssetBundleJson(input.assetBundleJson).valid, true, JSON.stringify(validatePrototypeAssetBundleJson(input.assetBundleJson)));
  intent.assetBundle.canonicalSha256 = await sha256(input.assetBundleJson);
  const seven = structuredClone(intent);
  const removed = intent.placements.pop();
  for (const context of intent.nodeContexts) {
    context.visiblePlacementIds = context.visiblePlacementIds.filter((id) => id !== removed.id);
  }
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
