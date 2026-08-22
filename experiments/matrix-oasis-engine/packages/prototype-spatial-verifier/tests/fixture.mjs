import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import { validatePrototypeEnvironmentFactsJson } from "@matrix-oasis/prototype-spatial-planning-contracts";
import { solvePrototypeSpatialLayout, synthesizePrototypeSpatialIntent } from "@matrix-oasis/prototype-spatial-solver";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const encoder = new TextEncoder();
const SYNTHETIC_COMPRESSED_PLY = Buffer.from(
  "cGx5CmZvcm1hdCBiaW5hcnlfbGl0dGxlX2VuZGlhbiAxLjAKY29tbWVudCBHZW5lcmF0ZWQgYnkgc3BsYXQtdHJhbnNmb3JtIDMuMy4wCmVsZW1lbnQgY2h1bmsgMQpwcm9wZXJ0eSBmbG9hdCBtaW5feApwcm9wZXJ0eSBmbG9hdCBtaW5feQpwcm9wZXJ0eSBmbG9hdCBtaW5fegpwcm9wZXJ0eSBmbG9hdCBtYXhfeApwcm9wZXJ0eSBmbG9hdCBtYXhfeQpwcm9wZXJ0eSBmbG9hdCBtYXhfegpwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfeApwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfeQpwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfegpwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfeApwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfeQpwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfegpwcm9wZXJ0eSBmbG9hdCBtaW5fcgpwcm9wZXJ0eSBmbG9hdCBtaW5fZwpwcm9wZXJ0eSBmbG9hdCBtaW5fYgpwcm9wZXJ0eSBmbG9hdCBtYXhfcgpwcm9wZXJ0eSBmbG9hdCBtYXhfZwpwcm9wZXJ0eSBmbG9hdCBtYXhfYgplbGVtZW50IHZlcnRleCAzCnByb3BlcnR5IHVpbnQgcGFja2VkX3Bvc2l0aW9uCnByb3BlcnR5IHVpbnQgcGFja2VkX3JvdGF0aW9uCnByb3BlcnR5IHVpbnQgcGFja2VkX3NjYWxlCnByb3BlcnR5IHVpbnQgcGFja2VkX2NvbG9yCmVuZF9oZWFkZXIKAACAvwAAAAAAAAA/AACAPwAAAEAAAMA/AAAAwAAAAMAAAADAAAAAwAAAAMAAAADAqvEAP6rxAD+q8QA/qvEAP6rxAD+q8QA/AAAAAAACCGAAAAAA/wAAAAAEEIAAAghgAAAAAP8AAAD/////AAIIYAAAAAD/AAAA",
  "base64",
);

export function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
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

export function glbFromGeometry({ positions, indices }) {
  const positionsArray = new Float32Array(positions);
  const indicesArray = new Uint32Array(indices);
  const binary = new Uint8Array(positionsArray.byteLength + indicesArray.byteLength);
  binary.set(new Uint8Array(positionsArray.buffer));
  binary.set(new Uint8Array(indicesArray.buffer), positionsArray.byteLength);
  const coordinates = [0, 1, 2].map((axis) => positions.filter((_, index) => index % 3 === axis));
  const json = {
    asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: positions.length / 3, type: "VEC3", min: coordinates.map((values) => Math.min(...values)), max: coordinates.map((values) => Math.max(...values)) },
      { bufferView: 1, componentType: 5125, count: indices.length, type: "SCALAR" },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positionsArray.byteLength, target: 34962 },
      { buffer: 0, byteOffset: positionsArray.byteLength, byteLength: indicesArray.byteLength, target: 34963 },
    ], buffers: [{ byteLength: binary.byteLength }],
  };
  const encoded = encoder.encode(JSON.stringify(json));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const binaryLength = Math.ceil(binary.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + binaryLength);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true); view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength); output.set(encoded, 20);
  view.setUint32(20 + jsonLength, binaryLength, true); view.setUint32(24 + jsonLength, 0x004e4942, true);
  output.set(binary, 28 + jsonLength);
  return output;
}

function boxGlb(width, height, depth) {
  const positions = [];
  const indices = [];
  addBox(positions, indices, [-width / 2, 0, -depth / 2], [width / 2, height, depth / 2]);
  return glbFromGeometry({ positions, indices });
}

function roomGlb(obstacle = null) {
  const positions = [];
  const indices = [];
  addQuad(positions, indices, [-12, 0, -12], [-12, 0, 12], [12, 0, 12], [12, 0, -12]);
  addBox(positions, indices, [-12.2, 0, -12.2], [-12, 4, 12.2]);
  addBox(positions, indices, [12, 0, -12.2], [12.2, 4, 12.2]);
  addBox(positions, indices, [-12, 0, -12.2], [12, 4, -12]);
  addBox(positions, indices, [-12, 0, 12], [12, 4, 12.2]);
  if (obstacle !== null) addBox(positions, indices, obstacle.minimum, obstacle.maximum);
  return glbFromGeometry({ positions, indices });
}

export function roomGlbWithObstacle(minimum, maximum) {
  return roomGlb({ minimum, maximum });
}

function metrics(width, height, depth) {
  return {
    nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 12, maxTextureWidth: 0, maxTextureHeight: 0,
    boundsMm: { min: [-Math.round(width * 500), 0, -Math.round(depth * 500)], max: [Math.round(width * 500), Math.round(height * 1000), Math.round(depth * 500)] },
  };
}

function assetRecord(id, assetPath, bytes, width, height, depth) {
  return {
    id, path: assetPath, format: "glb", roles: ["visual", "collider"], normalizationProfile: "matrix-oasis.glb-normalization/1",
    byteLength: bytes.byteLength, sha256: sha256(bytes), metrics: metrics(width, height, depth),
  };
}

export async function buildSpatialVerificationFixture({ entryActionCount = null } = {}) {
  let authoringText = await readFile(new URL("../../../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8");
  if (entryActionCount !== null) {
    const authoring = JSON.parse(authoringText);
    const template = authoring.nodes[0].actions[0];
    authoring.nodes[0].actions = Array.from({ length: entryActionCount }, (_, index) => ({
      ...structuredClone(template), id: `action-entry-${String(index).padStart(2, "0")}`,
      label: `Neutral action ${index + 1}`,
    }));
    authoringText = canonicalizeJsonValue(authoring);
  }
  const compiled = await compileAuthoringGamePackJson(authoringText);
  assert.equal(compiled.ok, true);
  const runtimeGamePackJson = compiled.canonicalJson;
  const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
  const blueprint = {
    format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion, title: "Neutral verification", environmentPrompt: "A neutral connected room.", visualStylePrompt: "Simple neutral materials." },
    zones: [{ id: "zone-a", label: "A", description: "First area." }, { id: "zone-b", label: "B", description: "Second area." }],
    assetBriefs: [
      { id: "brief-environment", kind: "environment", prompt: "Neutral room", entityId: null, roles: ["visual", "collider"] },
      { id: "brief-prop", kind: "prop", prompt: "Compact object", entityId: "control-unit", roles: ["visual", "collider"] },
      { id: "brief-character", kind: "character-placeholder", prompt: "Standing figure", entityId: "actor-unit", roles: ["visual", "collider"] },
    ],
    placements: [
      { id: "placement-environment", assetBriefId: "brief-environment", zoneId: "zone-a", entityId: null },
      { id: "placement-prop", assetBriefId: "brief-prop", zoneId: "zone-a", entityId: "control-unit" },
      { id: "placement-character", assetBriefId: "brief-character", zoneId: "zone-b", entityId: "actor-unit" },
    ],
    nodeBindings: compiled.runtimePack.nodes.map((node, index) => ({ nodeId: node.id, zoneId: index % 2 === 0 ? "zone-a" : "zone-b", visiblePlacementIds: ["placement-environment", "placement-prop", "placement-character"] })),
  };
  const sceneBlueprintJson = canonicalizeJsonValue(blueprint);
  const prop = boxGlb(0.8, 1.0, 0.8);
  const character = boxGlb(0.7, 1.75, 0.7);
  const assetFiles = new Map([["assets/prop.glb", prop], ["assets/character.glb", character]]);
  const environmentPlaceholder = {
    ...assetRecord("asset-environment", "assets/environment.glb", boxGlb(1, 1, 1), 1, 1, 1),
    normalizationProfile: "kenney-prototype-room-v1",
  };
  const assetBundle = {
    format: "matrix-oasis.prototype-asset-bundle", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: blueprint.scene.id, contentVersion: blueprint.scene.contentVersion, title: blueprint.scene.title },
    blueprint: { format: blueprint.format, formatVersion: blueprint.formatVersion, canonicalSha256: sha256(sceneBlueprintJson), assetBriefs: blueprint.assetBriefs.map(({ id, kind, entityId, roles }) => ({ id, kind, entityId, roles })) },
    runtimeIdentity: { format: compiled.runtimePack.format, formatVersion: compiled.runtimePack.formatVersion, id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion, authoringCanonicalSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`, artifactSha256: `sha256:${compiled.receipt.artifact.sha256}` },
    environmentTemplate: "kenney-prototype-room-v1",
    materializations: [
      { assetBriefId: "brief-environment", source: { type: "builtin-template", template: "kenney-prototype-room-v1" }, assets: [environmentPlaceholder] },
      { assetBriefId: "brief-prop", source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" }, assets: [assetRecord("asset-prop", "assets/prop.glb", prop, 0.8, 1.0, 0.8)] },
      { assetBriefId: "brief-character", source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" }, assets: [assetRecord("asset-character", "assets/character.glb", character, 0.7, 1.75, 0.7)] },
    ],
  };
  const assetBundleJson = canonicalizeJsonValue(assetBundle);
  assert.equal(validatePrototypeAssetBundleJson(assetBundleJson).valid, true, JSON.stringify(validatePrototypeAssetBundleJson(assetBundleJson)));
  const synthesized = await synthesizePrototypeSpatialIntent({ sceneBlueprintJson, runtimeGamePackJson, runtimeReceiptJson, assetBundleJson });
  assert.equal(synthesized.ok, true, JSON.stringify(synthesized));
  const environmentColliderBytes = roomGlb();
  const environmentSplatBytes = Uint8Array.from(SYNTHETIC_COMPRESSED_PLY);
  const spatialAssembly = {
    format: "matrix-oasis.prototype-spatial-assembly", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: blueprint.scene.id, contentVersion: blueprint.scene.contentVersion, title: blueprint.scene.title },
    runtimeIdentity: {
      runtimeFormat: compiled.runtimePack.format, runtimeFormatVersion: compiled.runtimePack.formatVersion,
      packId: compiled.runtimePack.source.id, packContentVersion: compiled.runtimePack.source.contentVersion,
      sourceCanonicalSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
      artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
    },
    sources: {
      scenePackSha256: `sha256:${"2".repeat(64)}`,
      prototypeAssemblyReportSha256: `sha256:${"3".repeat(64)}`,
      spatialEnvironmentBundleSha256: `sha256:${"4".repeat(64)}`,
      sceneBlueprintSha256: sha256(sceneBlueprintJson),
    },
    environment: {
      panoramaVisible: false,
      renderer: { profile: "opaque-depth-compose-v1", depthBiasMicros: 0, depthTestMinAlphaPermille: 50, depthCaptureAlphaPermille: 500 },
      splat: {
        path: "assets/environment.compressed.ply", sha256: sha256(environmentSplatBytes), numGaussians: 3,
        derivation: { profile: "identity-v1", targetNumGaussians: 640_000, sourceNumGaussians: 3,
          fullResolutionCompressedPly: { byteLength: environmentSplatBytes.byteLength,
            sha256: sha256(environmentSplatBytes), numGaussians: 3 } },
      },
      collider: { assetId: "environment-collider", placementId: "environment-placement",
        path: "assets/environment-collider.glb", sha256: sha256(environmentColliderBytes) },
    },
    transforms: {
      coordinateTransform: "spz-raw-ply-to-godot-v1", eulerOrder: "YXZ",
      alignment: {
        profile: "collider-fit-30m-v1", targetFloorSpanMm: 30_000, maximumHorizontalSpanMm: 90_000,
        colliderBoundsMm: { minimumMm: [-12_000, 0, -12_000], maximumMm: [12_000, 4_000, 12_000] },
        centerFloorSampleSourceMm: [0, 0, 0], splatProfile: "splat-robust-fit-30m-v1",
        splatBoundsProfile: "source-position-percentile-1-99-v1",
        splatBoundsMm: { minimumMm: [-1_000, 0, 500], maximumMm: [1_000, 2_000, 1_500] },
      },
      root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] },
      splat: { localTranslationMm: [0, 0, 0], localRotationMilliDegrees: [0, 0, 0], scaleMicros: 1_000_000 },
      collider: { localTranslationMm: [0, 0, 0], scaleMicros: 1_000_000 },
      walkableEnvelope: { profile: "source-density-first-surface-v1", minimumMm: [-12_000, 0, -12_000],
        maximumMm: [12_000, 4_000, 12_000], wallThicknessMm: 700, floorThicknessMm: 200,
        verticalBandMm: [350, 3_000], lateralBandMm: 4_000, binSizeMm: 250,
        minimumBinCount: 64, peakThresholdPermille: 5, adjacentBins: 2 },
      placementGroundTargetMm: 150,
    },
  };
  const spatialAssemblyJson = canonicalizeJsonValue(spatialAssembly);
  const floorAnchors = [];
  for (let x = -9000; x <= 9000; x += 1000) for (let z = -9000; z <= 9000; z += 1000) {
    floorAnchors.push({ id: `floor-${String(floorAnchors.length).padStart(4, "0")}`, positionMm: [x, 0, z], normalMicros: [0, 1_000_000, 0], clearanceRadiusMm: 350, clearanceHeightMm: 1800, ceilingHeightMm: 4000, componentIndex: 0, polygonIndex: 0, capsuleClearanceVerified: true });
  }
  const environmentFacts = {
    format: "matrix-oasis.prototype-environment-facts", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    source: {
      scene: synthesized.spatialIntent.scene, blueprint: synthesized.spatialIntent.blueprint, runtime: synthesized.spatialIntent.runtime,
      spatialEnvironmentBundle: { format: "matrix-oasis.prototype-spatial-environment-bundle", formatVersion: "0.1.0", canonicalSha256: `sha256:${"d".repeat(64)}` },
      environmentBundleSha256: `sha256:${"e".repeat(64)}`,
      collider: { format: "glb", byteLength: environmentColliderBytes.byteLength, sha256: sha256(environmentColliderBytes) },
      calibration: { coordinateTransform: "spz-raw-ply-to-godot-v1", metricScaleMicros: 1_000_000, groundPlaneOffsetMm: 0, godotTranslationMm: [0, 0, 0], godotRotationMilliDegrees: [0, 0, 0] },
      analysisTransform: { profile: "spatial-assembly-collider-v1", sourceCanonicalSha256: sha256(spatialAssemblyJson), eulerOrder: "YXZ", root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] }, collider: { localTranslationMm: [0, 0, 0], scaleMicros: 1_000_000 } },
    },
    coordinateSystem: { handedness: "right", upAxis: "Y", unit: "millimeter", eulerOrder: "YXZ" },
    analysisProfile: { playerRadiusMm: 350, playerHeightMm: 1800, floorSnapMm: 200, maxSlopeMilliDegrees: 45_000 },
    environmentBounds: { minimumMm: [-12_000, 0, -12_000], maximumMm: [12_000, 4000, 12_000] },
    navigationMesh: {
      verticesMm: [[-10_000, 0, -10_000], [-10_000, 0, 10_000], [10_000, 0, 10_000], [10_000, 0, -10_000]],
      polygons: [{ vertexIndices: [0, 1, 2, 3], componentIndex: 0 }],
      components: [{ index: 0, polygonIndices: [0], bounds: { minimumMm: [-10_000, 0, -10_000], maximumMm: [10_000, 0, 10_000] } }],
    }, floorAnchors, wallAnchors: [],
  };
  const environmentFactsJson = canonicalizeJsonValue(environmentFacts);
  assert.equal(validatePrototypeEnvironmentFactsJson(environmentFactsJson).valid, true);
  const solved = await solvePrototypeSpatialLayout({ spatialIntentJson: synthesized.canonicalSpatialIntentJson, environmentFactsJson, assetBundleJson, runtimeGamePackJson, runtimeReceiptJson });
  assert.equal(solved.ok, true, JSON.stringify(solved));
  return {
    spatialIntentJson: synthesized.canonicalSpatialIntentJson, environmentFactsJson, spatialSolutionJson: solved.canonicalSpatialSolutionJson,
    assetBundleJson, runtimeGamePackJson, runtimeReceiptJson, spatialAssemblyJson,
    environmentColliderBytes, environmentSplatBytes, assetFiles,
  };
}
