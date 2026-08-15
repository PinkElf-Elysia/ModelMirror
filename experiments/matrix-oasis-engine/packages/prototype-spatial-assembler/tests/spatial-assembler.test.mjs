import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT,
  PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION,
  PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE,
  PrototypeSpatialAssemblerOperationalError,
  assemblePrototypeSpatialScene,
  validatePrototypeSpatialAssemblyJson,
} from "../src/index.mjs";
import {
  deriveColliderCalibration,
  deriveColliderWalkableLayout,
  deriveSplatCalibration,
} from "../src/collider-calibration.mjs";

const COMPRESSED_PLY = Buffer.from(
  "cGx5CmZvcm1hdCBiaW5hcnlfbGl0dGxlX2VuZGlhbiAxLjAKY29tbWVudCBHZW5lcmF0ZWQgYnkgc3BsYXQtdHJhbnNmb3JtIDMuMy4wCmVsZW1lbnQgY2h1bmsgMQpwcm9wZXJ0eSBmbG9hdCBtaW5feApwcm9wZXJ0eSBmbG9hdCBtaW5feQpwcm9wZXJ0eSBmbG9hdCBtaW5fegpwcm9wZXJ0eSBmbG9hdCBtYXhfeApwcm9wZXJ0eSBmbG9hdCBtYXhfeQpwcm9wZXJ0eSBmbG9hdCBtYXhfegpwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfeApwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfeQpwcm9wZXJ0eSBmbG9hdCBtaW5fc2NhbGVfegpwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfeApwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfeQpwcm9wZXJ0eSBmbG9hdCBtYXhfc2NhbGVfegpwcm9wZXJ0eSBmbG9hdCBtaW5fcgpwcm9wZXJ0eSBmbG9hdCBtaW5fZwpwcm9wZXJ0eSBmbG9hdCBtaW5fYgpwcm9wZXJ0eSBmbG9hdCBtYXhfcgpwcm9wZXJ0eSBmbG9hdCBtYXhfZwpwcm9wZXJ0eSBmbG9hdCBtYXhfYgplbGVtZW50IHZlcnRleCAzCnByb3BlcnR5IHVpbnQgcGFja2VkX3Bvc2l0aW9uCnByb3BlcnR5IHVpbnQgcGFja2VkX3JvdGF0aW9uCnByb3BlcnR5IHVpbnQgcGFja2VkX3NjYWxlCnByb3BlcnR5IHVpbnQgcGFja2VkX2NvbG9yCmVuZF9oZWFkZXIKAACAvwAAAAAAAAA/AACAPwAAAEAAAMA/AAAAwAAAAMAAAADAAAAAwAAAAMAAAADAqvEAP6rxAD+q8QA/qvEAP6rxAD+q8QA/AAAAAAACCGAAAAAA/wAAAAAEEIAAAghgAAAAAP8AAAD/////AAIIYAAAAAD/AAAA",
  "base64",
);

function hash(value) {
  return "sha256:" + createHash("sha256").update(value).digest("hex");
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
  binary.set(new Uint8Array(positions.buffer), 0);
  binary.set(new Uint8Array(indices.buffer), positions.byteLength);
  const json = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: 8, type: "VEC3", min: [-1, -1, -2], max: [1, 1, 2] },
      { bufferView: 1, componentType: 5123, count: 36, type: "SCALAR" },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positions.byteLength, target: 34962 },
      { buffer: 0, byteOffset: positions.byteLength, byteLength: indices.byteLength, target: 34963 },
    ],
    buffers: [{ byteLength: binary.byteLength }],
  };
  const encoded = new TextEncoder().encode(JSON.stringify(json));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + binary.byteLength);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength);
  output.set(encoded, 20);
  view.setUint32(20 + jsonLength, binary.byteLength, true);
  view.setUint32(24 + jsonLength, 0x004e4942, true);
  output.set(binary, 28 + jsonLength);
  return output;
}

function authoring() {
  return {
    format: "matrix-oasis.authoring-game-pack",
    formatVersion: "0.1.0",
    id: "neutral-space",
    contentVersion: "1.0.0",
    language: "zh-CN",
    title: "中性空间",
    entryNodeId: "node-start",
    entities: [],
    variables: [],
    cues: [],
    nodes: [{
      id: "node-start",
      title: "起点",
      entityIds: [],
      entryCueIds: [],
      actions: [{
        id: "action-finish",
        label: "完成",
        effects: [],
        target: { kind: "ending", id: "ending-complete" },
      }],
    }],
    endings: [{ id: "ending-complete", title: "完成", cueIds: [] }],
  };
}

async function fixture() {
  const authoringText = canonicalizeJsonValue(authoring());
  const compiled = await compileAuthoringGamePackJson(authoringText);
  assert.equal(compiled.ok, true);
  const runtimeText = compiled.canonicalJson;
  const receiptText = canonicalizeJsonValue(compiled.receipt);
  const runtime = compiled.runtimePack;
  const receipt = compiled.receipt;
  const collider = colliderGlb();
  const colliderHash = hash(collider);
  const scene = {
    format: "matrix-oasis.scene-pack",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: "neutral-space", contentVersion: "1.0.0", title: "中性空间" },
    runtimeIdentity: {
      runtimeFormat: runtime.format,
      runtimeFormatVersion: runtime.formatVersion,
      packId: runtime.source.id,
      packContentVersion: runtime.source.contentVersion,
      sourceCanonicalSha256: runtime.source.canonicalSha256,
      artifactSha256: receipt.artifact.sha256,
    },
    assets: [{
      id: "environment-collider",
      roles: ["visual", "collider"],
      path: "assets/environment-collider.glb",
      format: "glb",
      byteLength: collider.byteLength,
      sha256: colliderHash.slice(7),
    }],
    placements: [{
      id: "environment-placement",
      visualAssetId: "environment-collider",
      colliderAssetId: "environment-collider",
      entityId: null,
      transform: {
        positionMm: [0, 0, 0],
        rotationMilliDegrees: [0, 0, 0],
        scalePermille: [1000, 1000, 1000],
      },
    }],
    nodeBindings: runtime.nodes.map((node) => ({
      nodeId: node.id,
      playerSpawn: { positionMm: [0, 1000, 2000], yawMilliDegrees: 0 },
      actionAnchor: { positionMm: [0, 0, 0], yawMilliDegrees: 0 },
      visiblePlacementIds: ["environment-placement"],
    })),
  };
  const scenePackJson = canonicalizeJsonValue(scene);
  const sceneBlueprintSha256 = hash("blueprint");
  const sourceEnvironmentSha256 = hash("environment-bundle");
  const spatial = {
    format: "matrix-oasis.prototype-spatial-environment-bundle",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { ...scene.scene },
    blueprint: {
      format: "matrix-oasis.scene-blueprint",
      formatVersion: "0.1.0",
      canonicalSha256: sceneBlueprintSha256,
    },
    source: {
      environmentBundleSha256: sourceEnvironmentSha256,
      format: "spz",
      byteLength: 4,
      sha256: hash(Uint8Array.of(1, 2, 3, 4)),
    },
    assets: {
      splat: {
        path: "assets/environment.compressed.ply",
        format: "compressed-ply",
        byteLength: COMPRESSED_PLY.byteLength,
        sha256: hash(COMPRESSED_PLY),
        numGaussians: 3,
        numLods: 1,
        shBands: 0,
        derivation: {
          profile: "identity-v1",
          targetNumGaussians: 640_000,
          sourceNumGaussians: 3,
          fullResolutionCompressedPly: {
            byteLength: COMPRESSED_PLY.byteLength,
            sha256: hash(COMPRESSED_PLY),
            numGaussians: 3,
          },
        },
      },
      collider: {
        path: "assets/environment-collider.glb",
        format: "glb",
        byteLength: collider.byteLength,
        sha256: colliderHash,
        metrics: { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 1 },
      },
    },
    calibration: {
      coordinateTransform: "spz-raw-ply-to-godot-v1",
      metricScaleMicros: 1_000_000,
      groundPlaneOffsetMm: -125,
      godotTranslationMm: [100, 200, 300],
      godotRotationMilliDegrees: [0, 0, 0],
    },
    statistics: {
      sourceBounds: { minimumMm: [-1000, 0, 500], maximumMm: [1000, 2000, 1500] },
      runtimeRobustBounds: {
        profile: "source-position-percentile-1-99-v1",
        minimumMm: [-1000, 0, 500],
        maximumMm: [1000, 2000, 1500],
      },
      sourceMeanMm: [0, 1000, 1000],
      rendererCenterCompensationMm: [0, 1000, 1000],
      sourceInteriorEnvelope: {
        profile: "source-density-first-surface-v1",
        coordinateSpace: "splat-robust-fit-30m-v1",
        minimumMm: [-6000, 0, -15_500],
        maximumMm: [5750, 12_000, 9500],
        verticalBandMm: [350, 3000],
        lateralBandMm: 4000,
        binSizeMm: 250,
        minimumBinCount: 64,
        peakThresholdPermille: 5,
        adjacentBins: 2,
      },
    },
    toolchain: {
      converter: { id: "@playcanvas/splat-transform", version: "3.3.0" },
      decoder: { id: "@adobe/spz", version: "0.2.2" },
    },
  };
  const spatialEnvironmentBundleJson = canonicalizeJsonValue(spatial);
  const assemblyReport = {
    reportVersion: 1,
    profile: "matrix-oasis.prototype-assembly/1",
    inputs: {
      sceneBlueprintSha256,
      prototypeEnvironmentBundleSha256: sourceEnvironmentSha256,
    },
    environment: { colliderSha256: colliderHash },
    output: { scenePackSha256: hash(scenePackJson) },
  };
  return {
    assemblyReportJson: canonicalizeJsonValue(assemblyReport),
    scenePackJson,
    runtimeGamePackJson: runtimeText,
    runtimeReceiptJson: receiptText,
    spatialEnvironmentBundleJson,
    spatialEnvironmentFiles: new Map([
      ["assets/environment.compressed.ply", COMPRESSED_PLY],
      ["assets/environment-collider.glb", collider],
    ]),
  };
}

async function multiAssetFixture(count = 6) {
  const input = await fixture();
  const scene = JSON.parse(input.scenePackJson);
  const spatial = JSON.parse(input.spatialEnvironmentBundleJson);
  // The synthetic collider is two source units wide. A 15m official metric scale
  // preserves the legacy 30m test footprint without applying a second fit scale.
  spatial.calibration.metricScaleMicros = 15_000_000;
  spatial.calibration.groundPlaneOffsetMm = 1000;
  for (const bounds of [spatial.statistics.sourceBounds, spatial.statistics.runtimeRobustBounds]) {
    bounds.minimumMm = bounds.minimumMm.map((value) => value * 15);
    bounds.maximumMm = bounds.maximumMm.map((value) => value * 15);
  }
  spatial.statistics.sourceMeanMm = spatial.statistics.sourceMeanMm.map((value) => value * 15);
  spatial.statistics.rendererCenterCompensationMm =
    spatial.statistics.rendererCenterCompensationMm.map((value) => value * 15);
  input.spatialEnvironmentBundleJson = canonicalizeJsonValue(spatial);
  const added = Array.from({ length: count }, (_, index) => ({
    id: `placement-item-${index}`,
    visualAssetId: "environment-collider",
    colliderAssetId: null,
    entityId: null,
    transform: {
      positionMm: [index * 100, 0, index * 100],
      rotationMilliDegrees: [0, 0, 0],
      scalePermille: [1000, 1000, 1000],
    },
  }));
  scene.placements.push(...added);
  for (const binding of scene.nodeBindings) {
    binding.visiblePlacementIds.push(...added.map((placement) => placement.id));
  }
  input.scenePackJson = canonicalizeJsonValue(scene);
  const report = JSON.parse(input.assemblyReportJson);
  report.profile = "matrix-oasis.prototype-assembly/2";
  report.output.scenePackSha256 = hash(input.scenePackJson);
  input.assemblyReportJson = canonicalizeJsonValue(report);
  return input;
}

test("exports one exact private assembly surface", async () => {
  const exports = await import("../src/index.mjs");
  assert.deepEqual(Object.keys(exports).sort(), [
    "PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT",
    "PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION",
    "PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE",
    "PrototypeSpatialAssemblerOperationalError",
    "assemblePrototypeSpatialScene",
    "validatePrototypeSpatialAssemblyJson",
  ]);
  assert.equal(PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT, "matrix-oasis.prototype-spatial-assembly");
  assert.equal(PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION, "0.1.0");
  assert.deepEqual(PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE, {
    id: "matrix-oasis.prototype-spatial-assembly/1",
    panoramaVisible: false,
  });
});

test("binds Scene Pack, splat and collider with explicit metric transforms", async () => {
  const result = await assemblePrototypeSpatialScene(await fixture());
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.assembly.environment.panoramaVisible, false);
  assert.deepEqual(result.assembly.environment.renderer, {
    profile: "opaque-depth-compose-v1",
    depthBiasMicros: 0,
    depthTestMinAlphaPermille: 50,
    depthCaptureAlphaPermille: 500,
  });
  assert.equal(result.assembly.environment.splat.derivation.profile, "identity-v1");
  assert.equal(result.assembly.environment.splat.derivation.sourceNumGaussians, 3);
  assert.deepEqual(result.assembly.transforms.root, {
    translationMm: [100, 200, 2300],
    rotationMilliDegrees: [0, 0, 0],
  });
  assert.deepEqual(result.assembly.transforms.alignment, {
    profile: "collider-fit-30m-v1",
    targetFloorSpanMm: 30_000,
    maximumHorizontalSpanMm: 90_000,
    colliderBoundsMm: {
      minimumMm: [-1000, -1000, -2000],
      maximumMm: [1000, 1000, 2000],
    },
    centerFloorSampleSourceMm: [0, -1000, 0],
    splatProfile: "splat-robust-fit-30m-v1",
    splatBoundsProfile: "source-position-percentile-1-99-v1",
    splatBoundsMm: {
      minimumMm: [-1000, 0, 500],
      maximumMm: [1000, 2000, 1500],
    },
  });
  assert.deepEqual(result.assembly.transforms.splat, {
    localTranslationMm: [0, 30_000, 0],
    localRotationMilliDegrees: [0, 0, 0],
    scaleMicros: 30_000_000,
  });
  assert.equal(result.assembly.transforms.eulerOrder, "YXZ");
  assert.deepEqual(result.assembly.transforms.collider, {
    localTranslationMm: [0, 15_000, 0],
    scaleMicros: 15_000_000,
  });
  assert.deepEqual(result.assembly.transforms.walkableEnvelope, {
    profile: "source-density-first-surface-v1",
    minimumMm: [-6000, 0, -15_500],
    maximumMm: [5750, 12_000, 9500],
    wallThicknessMm: 700,
    floorThicknessMm: 200,
    verticalBandMm: [350, 3000],
    lateralBandMm: 4000,
    binSizeMm: 250,
    minimumBinCount: 64,
    peakThresholdPermille: 5,
    adjacentBins: 2,
  });
  assert.equal(result.assembly.transforms.placementGroundTargetMm, 150);
  assert.deepEqual(result.referencedFiles, [
    { source: "spatial-environment", path: "assets/environment.compressed.ply" },
    { source: "spatial-environment", path: "assets/environment-collider.glb" },
  ]);
  assert.equal(validatePrototypeSpatialAssemblyJson(result.canonicalSpatialAssemblyJson).valid, true);
  const report = JSON.parse(result.canonicalSpatialAssemblyReportJson);
  assert.equal(report.alignment.colliderFitProfile, "collider-fit-30m-v1");
  assert.equal(report.alignment.colliderScaleMicros, 15_000_000);
  assert.equal(report.alignment.walkableEnvelopeProfile, "source-density-first-surface-v1");
  assert.deepEqual(report.alignment.walkableEnvelopeMinimumMm, [-6000, 0, -15_500]);
  assert.deepEqual(report.alignment.walkableEnvelopeMaximumMm, [5750, 12_000, 9500]);
  assert.equal(report.alignment.wallDensityBinSizeMm, 250);
  assert.equal(report.alignment.wallDensityMinimumBinCount, 64);
  assert.equal(report.alignment.rendererDepthBiasMicros, 0);
  assert.equal(report.alignment.sourceGroundPlaneOffsetMm, -125);
  assert.equal(report.alignment.verticalAlignmentProfile, "collider-calibrated-floor-v1");
  assert.equal(report.alignment.placementGroundTargetMm, 150);
  assert.equal(report.output.sourceSplatCount, 3);
  assert.equal(report.output.splatLodProfile, "identity-v1");
  assert.deepEqual(report.alignment.entryPlayerSpawnMm, [0, 1000, 2000]);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.assembly.transforms.splat), true);
});

test("keeps the same world layout when source metric units are scaled", async () => {
  const input = await fixture();
  const spatial = JSON.parse(input.spatialEnvironmentBundleJson);
  spatial.calibration.metricScaleMicros = 2_000_000;
  for (const bounds of [spatial.statistics.sourceBounds, spatial.statistics.runtimeRobustBounds]) {
    bounds.minimumMm = bounds.minimumMm.map((value) => value * 2);
    bounds.maximumMm = bounds.maximumMm.map((value) => value * 2);
  }
  spatial.statistics.sourceMeanMm = spatial.statistics.sourceMeanMm.map((value) => value * 2);
  spatial.statistics.rendererCenterCompensationMm =
    spatial.statistics.rendererCenterCompensationMm.map((value) => value * 2);
  const result = await assemblePrototypeSpatialScene({
    ...input,
    spatialEnvironmentBundleJson: canonicalizeJsonValue(spatial),
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(result.assembly.transforms.splat, {
    localTranslationMm: [0, 30_000, 0],
    localRotationMilliDegrees: [0, 0, 0],
    scaleMicros: 30_000_000,
  });
  assert.deepEqual(result.assembly.transforms.walkableEnvelope, {
    profile: "source-density-first-surface-v1",
    minimumMm: [-6000, 0, -15_500],
    maximumMm: [5750, 12_000, 9500],
    wallThicknessMm: 700,
    floorThicknessMm: 200,
    verticalBandMm: [350, 3000],
    lateralBandMm: 4000,
    binSizeMm: 250,
    minimumBinCount: 64,
    peakThresholdPermille: 5,
    adjacentBins: 2,
  });
});

test("v2 derives six ordered safe slots inside the walkable envelope and fails closed without capacity", async () => {
  const input = await multiAssetFixture(6);
  const result = await assemblePrototypeSpatialScene(input, {
    profile: "matrix-oasis.prototype-spatial-assembly/2",
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(result.assembly.transforms.placementLayout.map((entry) => entry.placementId),
    Array.from({ length: 6 }, (_, index) => `placement-item-${index}`));
  assert.equal(new Set(result.assembly.transforms.placementLayout.map((entry) => entry.positionMm.join(","))).size, 6);
  const navigationCells = new Set(result.assembly.transforms.navigation.cells.map((entry) => entry.join(",")));
  for (const entry of result.assembly.transforms.placementLayout) {
    assert.equal(navigationCells.has(entry.positionMm.join(",")), true);
    assert.equal(entry.positionMm[0] >= result.assembly.transforms.walkableEnvelope.minimumMm[0], true);
    assert.equal(entry.positionMm[0] <= result.assembly.transforms.walkableEnvelope.maximumMm[0], true);
    assert.equal(entry.positionMm[2] >= result.assembly.transforms.walkableEnvelope.minimumMm[2], true);
    assert.equal(entry.positionMm[2] <= result.assembly.transforms.walkableEnvelope.maximumMm[2], true);
  }
  assert.equal(
    result.assembly.transforms.walkableEnvelope.profile,
    "collider-agent-navigation-component-v7",
  );
  assert.equal(result.assembly.transforms.navigation.profile, "collider-agent-grid-v1");
  assert.equal(result.assembly.transforms.navigation.cellSizeMm, 1000);
  assert.equal(result.assembly.transforms.navigation.agentRadiusMm, 350);
  assert.equal(result.assembly.transforms.navigation.maximumStepMm, 450);
  assert.equal(result.assembly.transforms.navigation.minimumClearanceMm, 700);
  assert.equal(result.assembly.transforms.navigation.bindings.length, 1);
  assert.equal(result.assembly.transforms.navigation.bindings[0].nodeId, "node-start");
  assert.equal(result.assembly.transforms.navigation.bindings[0].pathCellCount >= 2, true);
  assert.equal(result.assembly.transforms.nodeBindingLayout.length, 1);
  assert.equal(result.assembly.transforms.nodeBindingLayout[0].nodeId, "node-start");
  assert.equal(result.assembly.transforms.nodeBindingLayout[0].playerSpawn.positionMm[1] >= 1000, true);
  assert.equal(result.assembly.transforms.nodeBindingLayout[0].actionAnchor.positionMm[1] >= 0, true);
  assert.equal(result.assembly.transforms.alignment.profile, "collider-official-metric-frame-v4");
  assert.equal(result.assembly.transforms.alignment.targetFloorSpanMm, 0);
  assert.equal(result.assembly.transforms.alignment.maximumHorizontalSpanMm, 128_000);
  assert.equal(result.assembly.transforms.alignment.splatProfile, "splat-opencv-to-godot-official-metric-v4");
  assert.deepEqual(result.assembly.transforms.alignment.splatBoundsMm, {
    minimumMm: [-15_000, -30_000, -22_500],
    maximumMm: [15_000, 0, -7500],
  });
  assert.deepEqual(result.assembly.transforms.splat.localRotationMilliDegrees, [180_000, 0, 0]);
  assert.deepEqual(result.assembly.transforms.splat.localTranslationMm, [0, 0, -15_000]);
  assert.equal(
    result.assembly.transforms.splat.scaleMicros,
    result.assembly.transforms.collider.scaleMicros,
  );
  assert.equal(result.assembly.transforms.splat.scaleMicros, 15_000_000);
  assert.notDeepEqual(
    result.assembly.transforms.splat.localTranslationMm,
    result.assembly.transforms.collider.localTranslationMm,
  );
  assert.equal(validatePrototypeSpatialAssemblyJson(result.canonicalSpatialAssemblyJson).valid, true);
  const report = JSON.parse(result.canonicalSpatialAssemblyReportJson);
  assert.equal(report.profile, "matrix-oasis.prototype-spatial-assembly/2");
  assert.equal(report.alignment.placementLayoutProfile, "collider-agent-zone-constraint-v2");
  assert.equal(report.alignment.placementLayoutCount, 6);
  const outputs = await Promise.all(Array.from({ length: 20 }, () =>
    assemblePrototypeSpatialScene(input, { profile: "matrix-oasis.prototype-spatial-assembly/2" })));
  assert.equal(new Set(outputs.map((entry) => entry.canonicalSpatialAssemblyJson)).size, 1);

  const tooMany = await assemblePrototypeSpatialScene(await multiAssetFixture(7), {
    profile: "matrix-oasis.prototype-spatial-assembly/2",
  });
  assert.equal(tooMany.diagnostics[0].code, "PROTOTYPE_SPATIAL_ASSEMBLY_SAFE_LAYOUT_UNAVAILABLE");
  const narrowInput = await multiAssetFixture(2);
  const spatial = JSON.parse(narrowInput.spatialEnvironmentBundleJson);
  spatial.statistics.sourceInteriorEnvelope.minimumMm = [-2000, 0, -2000];
  spatial.statistics.sourceInteriorEnvelope.maximumMm = [2000, 4000, 2000];
  narrowInput.spatialEnvironmentBundleJson = canonicalizeJsonValue(spatial);
  const narrow = await assemblePrototypeSpatialScene(narrowInput, {
    profile: "matrix-oasis.prototype-spatial-assembly/2",
  });
  assert.equal(narrow.ok, true, JSON.stringify(narrow));
  assert.equal(narrow.assembly.transforms.walkableEnvelope.profile, "collider-agent-navigation-component-v7");
  assert.equal((await assemblePrototypeSpatialScene(input, {
    profile: "matrix-oasis.prototype-spatial-assembly/1",
  })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_UNSUPPORTED");
});

test("official metric calibration accepts bounded 100m worlds and rejects spans beyond 128m", async () => {
  const input = await multiAssetFixture(2);
  const collider = input.spatialEnvironmentFiles.get("assets/environment-collider.glb");
  const accepted = await deriveColliderCalibration(collider, {
    metricScaleMicros: 25_000_000,
    groundPlaneOffsetMm: 0,
  });
  assert.notEqual(accepted, null);
  assert.equal(accepted.maximumHorizontalSpanMm, 128_000);
  assert.equal(await deriveColliderCalibration(collider, {
    metricScaleMicros: 33_000_000,
    groundPlaneOffsetMm: 0,
  }), null);
});

test("v2 deduplicates repeated node anchors before enforcing the four-zone limit", async () => {
  const input = await multiAssetFixture(6);
  const scene = JSON.parse(input.scenePackJson);
  const spatial = JSON.parse(input.spatialEnvironmentBundleJson);
  const collider = input.spatialEnvironmentFiles.get("assets/environment-collider.glb");
  const environment = scene.placements[0];
  const alignment = await deriveColliderCalibration(collider, {
    metricScaleMicros: spatial.calibration.metricScaleMicros,
    groundPlaneOffsetMm: spatial.calibration.groundPlaneOffsetMm,
  });
  const splatAlignment = deriveSplatCalibration(
    spatial.statistics,
    spatial.calibration.metricScaleMicros,
    alignment,
  );
  const result = await deriveColliderWalkableLayout(
    collider,
    COMPRESSED_PLY,
    alignment,
    splatAlignment,
    spatial.calibration.metricScaleMicros,
    spatial.statistics,
    scene.placements.filter((placement) => placement.id !== environment.id),
    [0, 1000, 2000],
    Array.from({ length: 7 }, (_, index) => ({
      nodeId: `node-${index}`,
      playerSpawn: {
        positionMm: index < 2 ? [-4000, 1000, -2000] : [4000, 1000, 6000],
        yawMilliDegrees: 0,
      },
      actionAnchor: {
        positionMm: index < 2 ? [-4000, 0, -4000] : [4000, 0, 4000],
        yawMilliDegrees: 0,
      },
      visiblePlacementIds: Array.from({ length: 3 }, (_, itemIndex) =>
        `placement-item-${itemIndex + (index < 2 ? 0 : 3)}`),
    })),
    Array.from({ length: 7 }, (_, index) => ({
      nodeId: `node-${index}`,
      actionCount: index === 0 ? 4 : 2,
    })),
  );
  assert.notEqual(result, null);
  assert.equal(result.placementLayout.length, 6);
  assert.equal(result.nodeBindingLayout.length, 7);
  for (let left = 0; left < result.placementLayout.length; left += 1) {
    for (let right = left + 1; right < result.placementLayout.length; right += 1) {
      const deltaX = result.placementLayout[left].positionMm[0] - result.placementLayout[right].positionMm[0];
      const deltaZ = result.placementLayout[left].positionMm[2] - result.placementLayout[right].positionMm[2];
      assert.equal(deltaX ** 2 + deltaZ ** 2 >= 1_000 ** 2, true);
    }
  }
  const fourActionBinding = result.nodeBindingLayout.find((binding) => binding.nodeId === "node-0");
  const secondZoneBinding = result.nodeBindingLayout.find((binding) => binding.nodeId === "node-2");
  assert.equal(fourActionBinding.actionAnchor.positionMm[0] - 3175 >=
    result.walkableEnvelope.minimumMm[0], true);
  assert.equal(fourActionBinding.actionAnchor.positionMm[0] + 3175 <=
    result.walkableEnvelope.maximumMm[0], true);
  assert.equal(fourActionBinding.actionAnchor.positionMm[2] - 2650 >=
    result.walkableEnvelope.minimumMm[2], true);
  const squaredDistance = (placement, binding) =>
    (placement.positionMm[0] - binding.actionAnchor.positionMm[0]) ** 2 +
    (placement.positionMm[2] - binding.actionAnchor.positionMm[2]) ** 2;
  for (const placement of result.placementLayout.slice(0, 3)) {
    assert.equal(squaredDistance(placement, fourActionBinding) <
      squaredDistance(placement, secondZoneBinding), true);
  }
  for (const placement of result.placementLayout.slice(3)) {
    assert.equal(squaredDistance(placement, secondZoneBinding) <
      squaredDistance(placement, fourActionBinding), true);
  }
});

test("is byte deterministic twenty times and leaves caller bytes unchanged", async () => {
  const input = await fixture();
  const before = input.spatialEnvironmentFiles.get("assets/environment.compressed.ply").slice();
  const results = await Promise.all(Array.from(
    { length: 20 },
    () => assemblePrototypeSpatialScene(input),
  ));
  assert.ok(results.every((result) => result.ok));
  assert.equal(new Set(results.map((result) => result.canonicalSpatialAssemblyJson)).size, 1);
  assert.equal(new Set(results.map((result) => result.canonicalSpatialAssemblyReportJson)).size, 1);
  assert.deepEqual(input.spatialEnvironmentFiles.get("assets/environment.compressed.ply"), before);
});

test("rejects source, identity, collider and canonical drift with static diagnostics", async () => {
  const input = await fixture();
  const changedReport = JSON.parse(input.assemblyReportJson);
  changedReport.output.scenePackSha256 = hash("changed");
  assert.equal((await assemblePrototypeSpatialScene({
    ...input,
    assemblyReportJson: canonicalizeJsonValue(changedReport),
  })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ASSEMBLY_IDENTITY_MISMATCH");
  const changedScene = JSON.parse(input.scenePackJson);
  changedScene.assets[0].sha256 = "0".repeat(64);
  assert.equal((await assemblePrototypeSpatialScene({
    ...input,
    scenePackJson: canonicalizeJsonValue(changedScene),
  })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ASSEMBLY_IDENTITY_MISMATCH");
  const changedFiles = new Map(input.spatialEnvironmentFiles);
  changedFiles.set("assets/environment.compressed.ply", Uint8Array.of(1));
  assert.equal((await assemblePrototypeSpatialScene({
    ...input,
    spatialEnvironmentFiles: changedFiles,
  })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ASSEMBLY_ENVIRONMENT_INVALID");
  assert.equal((await assemblePrototypeSpatialScene({
    ...input,
    assemblyReportJson: input.assemblyReportJson + "\n",
  })).diagnostics[0].code, "PROTOTYPE_SPATIAL_ASSEMBLY_SOURCE_REPORT_INVALID");
});

test("assembly validator rejects unknown shape and noncanonical bytes", async () => {
  const result = await assemblePrototypeSpatialScene(await fixture());
  assert.equal(result.ok, true);
  const changed = JSON.parse(result.canonicalSpatialAssemblyJson);
  changed.secretProperty = "dynamic-sentinel";
  const schema = validatePrototypeSpatialAssemblyJson(canonicalizeJsonValue(changed));
  assert.equal(schema.valid, false);
  assert.equal(schema.diagnostics[0].code, "PROTOTYPE_SPATIAL_ASSEMBLY_SCHEMA_INVALID");
  assert.equal(JSON.stringify(schema).includes("sentinel"), false);
  assert.equal(
    validatePrototypeSpatialAssemblyJson(result.canonicalSpatialAssemblyJson + "\n")
      .diagnostics[0].code,
    "PROTOTYPE_SPATIAL_ASSEMBLY_JSON_NON_CANONICAL",
  );
  const unsupported = JSON.parse(result.canonicalSpatialAssemblyJson);
  unsupported.scene.title = String.fromCharCode(0xdfff);
  assert.equal(
    validatePrototypeSpatialAssemblyJson(canonicalizeJsonValue(unsupported))
      .diagnostics[0].code,
    "PROTOTYPE_SPATIAL_ASSEMBLY_UNSUPPORTED_TEXT",
  );
});

test("never invokes accessors and operational faults expose one static code", async () => {
  const input = await fixture();
  let getterCalls = 0;
  const hostile = { ...input };
  Object.defineProperty(hostile, "scenePackJson", {
    enumerable: true,
    get() {
      getterCalls += 1;
      return input.scenePackJson;
    },
  });
  const rejected = await assemblePrototypeSpatialScene(hostile);
  assert.equal(rejected.ok, false);
  assert.equal(getterCalls, 0);
  await assert.rejects(
    () => assemblePrototypeSpatialScene(new Proxy(input, {
      getPrototypeOf() {
        throw new Error("dynamic-sentinel");
      },
    })),
    (error) => error instanceof PrototypeSpatialAssemblerOperationalError &&
      error.code === "PROTOTYPE_SPATIAL_ASSEMBLER_INTERNAL_ERROR" &&
      !String(error).includes("sentinel"),
  );
});
