import { createHash } from "node:crypto";
import {
  validatePrototypeSpatialEnvironmentBundleJson,
} from "@matrix-oasis/prototype-spatial-environment";
import {
  CANONICAL_JSON_PROFILE,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import {
  deriveColliderCalibration,
  deriveColliderWalkableLayout,
  deriveSplatCalibration,
  deriveWalkableEnvelope,
} from "./collider-calibration.mjs";

export const PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT =
  "matrix-oasis.prototype-spatial-assembly";
export const PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE = Object.freeze({
  id: "matrix-oasis.prototype-spatial-assembly/1",
  panoramaVisible: false,
});
const PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2 = Object.freeze({
  id: "matrix-oasis.prototype-spatial-assembly/2",
  panoramaVisible: false,
  maxNonEnvironmentPlacements: 6,
});

const REQUEST_KEYS = Object.freeze([
  "assemblyReportJson",
  "scenePackJson",
  "runtimeGamePackJson",
  "runtimeReceiptJson",
  "spatialEnvironmentBundleJson",
  "spatialEnvironmentFiles",
]);
const HASH_PATTERN = /^sha256:[0-9a-f]{64}$/u;
const ID_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/u;
const LIMITS = Object.freeze({
  assemblyReportBytes: 256 * 1024,
  scenePackBytes: 256 * 1024,
  runtimePackBytes: 16 * 1024 * 1024,
  runtimeReceiptBytes: 16 * 1024,
  spatialEnvironmentBytes: 256 * 1024,
});

export class PrototypeSpatialAssemblerOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_SPATIAL_ASSEMBLER_INTERNAL_ERROR");
    this.name = "PrototypeSpatialAssemblerOperationalError";
    this.code = "PROTOTYPE_SPATIAL_ASSEMBLER_INTERNAL_ERROR";
  }
}

function deepFreeze(value, seen = new Set()) {
  if (value === null || typeof value !== "object" || seen.has(value)) return value;
  seen.add(value);
  for (const child of Object.values(value)) deepFreeze(child, seen);
  return Object.freeze(value);
}

function diagnostic(phase, code, path) {
  return Object.freeze({ phase, severity: "error", code, path, message: code });
}

function failure(code, path = "", phase = "assembly") {
  return deepFreeze({ ok: false, diagnostics: [diagnostic(phase, code, path)] });
}

function report(diagnostics) {
  return deepFreeze({ reportVersion: 1, valid: diagnostics.length === 0, diagnostics });
}

function sha256(value) {
  return "sha256:" + createHash("sha256").update(value).digest("hex");
}

function utf8Length(value) {
  return new TextEncoder().encode(value).byteLength;
}

function exactRecord(value, keys) {
  if (value === null || typeof value !== "object" || Array.isArray(value) ||
      Object.getPrototypeOf(value) !== Object.prototype) return false;
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function canonicalObject(text, maximumBytes) {
  if (typeof text !== "string" || utf8Length(text) > maximumBytes) return null;
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text && value && typeof value === "object" &&
      !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function captureRequest(value) {
  if (value === null || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
    return null;
  }
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Reflect.ownKeys(descriptors);
  if (keys.length !== REQUEST_KEYS.length) return null;
  const output = Object.create(null);
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (typeof key !== "string" || !REQUEST_KEYS.includes(key) || !descriptor.enumerable ||
        !Object.hasOwn(descriptor, "value")) return null;
    output[key] = descriptor.value;
  }
  for (const key of REQUEST_KEYS.slice(0, 5)) if (typeof output[key] !== "string") return null;
  return output;
}

function captureProfile(value) {
  if (value === undefined) return PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE;
  if (value === null || typeof value !== "object" || Array.isArray(value) ||
      Object.getPrototypeOf(value) !== Object.prototype) return null;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Reflect.ownKeys(descriptors);
  const profile = descriptors.profile;
  return keys.length === 1 && keys[0] === "profile" && profile?.enumerable &&
    profile.get === undefined && profile.set === undefined &&
    Object.hasOwn(profile, "value") && profile.value === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id
    ? PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2
    : null;
}

function copyFileMap(value) {
  if (value === null || typeof value !== "object" || Object.getPrototypeOf(value) !== Map.prototype) {
    return null;
  }
  const output = new Map();
  for (const [key, bytes] of Map.prototype.entries.call(value)) {
    if (typeof key !== "string" || !(bytes instanceof Uint8Array) || output.has(key)) return null;
    output.set(key, Uint8Array.prototype.slice.call(bytes));
  }
  return output;
}

function validSuccessReport(value) {
  return value?.reportVersion === 1 && value.valid === true &&
    Array.isArray(value.diagnostics) && value.diagnostics.length === 0;
}

function safeVector(value, minimum, maximum) {
  return Array.isArray(value) && value.length === 3 &&
    value.every((item) => Number.isSafeInteger(item) && item >= minimum && item <= maximum);
}

function validHash(value) {
  return typeof value === "string" && HASH_PATTERN.test(value);
}

function validSplatDerivation(value, runtimeCount) {
  if (!exactRecord(value, [
    "profile", "targetNumGaussians", "sourceNumGaussians",
    "fullResolutionCompressedPly",
  ]) ||
      !["identity-v1", "mpmm-uniform-v1"].includes(value.profile) ||
      value.targetNumGaussians !== 640_000 ||
      !Number.isSafeInteger(value.sourceNumGaussians) ||
      value.sourceNumGaussians < runtimeCount || value.sourceNumGaussians > 2_500_000 ||
      !exactRecord(value.fullResolutionCompressedPly, ["byteLength", "sha256", "numGaussians"]) ||
      !Number.isSafeInteger(value.fullResolutionCompressedPly.byteLength) ||
      value.fullResolutionCompressedPly.byteLength < 1 ||
      value.fullResolutionCompressedPly.byteLength > 96 * 1024 * 1024 ||
      !validHash(value.fullResolutionCompressedPly.sha256) ||
      value.fullResolutionCompressedPly.numGaussians !== value.sourceNumGaussians) return false;
  return value.sourceNumGaussians <= value.targetNumGaussians
    ? value.profile === "identity-v1" && runtimeCount === value.sourceNumGaussians
    : value.profile === "mpmm-uniform-v1" && runtimeCount === value.targetNumGaussians;
}

function validColliderAlignment(value) {
  return exactRecord(value, [
    "profile", "targetFloorSpanMm", "maximumHorizontalSpanMm",
    "colliderBoundsMm", "centerFloorSampleSourceMm",
    "splatProfile", "splatBoundsProfile", "splatBoundsMm",
  ]) &&
    ["collider-fit-30m-v1", "collider-official-metric-frame-v4"].includes(value.profile) &&
    value.targetFloorSpanMm === (value.profile === "collider-fit-30m-v1" ? 30_000 : 0) &&
    value.maximumHorizontalSpanMm ===
      (value.profile === "collider-fit-30m-v1" ? 90_000 : 128_000) &&
    exactRecord(value.colliderBoundsMm, ["minimumMm", "maximumMm"]) &&
    safeVector(value.colliderBoundsMm.minimumMm, -1_000_000, 1_000_000) &&
    safeVector(value.colliderBoundsMm.maximumMm, -1_000_000, 1_000_000) &&
    value.colliderBoundsMm.minimumMm.every((item, index) =>
      item <= value.colliderBoundsMm.maximumMm[index]) &&
    safeVector(value.centerFloorSampleSourceMm, -1_000_000, 1_000_000) &&
    ["splat-robust-fit-30m-v1", "splat-opencv-to-godot-official-metric-v4"].includes(value.splatProfile) &&
    value.splatBoundsProfile === "source-position-percentile-1-99-v1" &&
    exactRecord(value.splatBoundsMm, ["minimumMm", "maximumMm"]) &&
    safeVector(value.splatBoundsMm.minimumMm, -1_000_000, 1_000_000) &&
    safeVector(value.splatBoundsMm.maximumMm, -1_000_000, 1_000_000) &&
    value.splatBoundsMm.minimumMm.every((item, index) =>
      item <= value.splatBoundsMm.maximumMm[index]);
}

function validWalkableEnvelope(value) {
  return exactRecord(value, [
    "profile", "minimumMm", "maximumMm", "wallThicknessMm", "floorThicknessMm",
    "verticalBandMm", "lateralBandMm", "binSizeMm", "minimumBinCount",
    "peakThresholdPermille", "adjacentBins",
  ]) &&
    [
      "source-density-first-surface-v1",
      "collider-global-aabb-floor-grid-v1",
      "collider-connected-floor-component-v2",
      "collider-splat-intersection-component-v3",
      "collider-splat-density-component-v4",
      "collider-splat-reachable-component-v5",
      "collider-splat-terminal-centered-component-v6",
      "collider-agent-navigation-component-v7",
    ].includes(value.profile) &&
    safeVector(value.minimumMm, -2_000_000, 2_000_000) &&
    safeVector(value.maximumMm, -2_000_000, 2_000_000) &&
    value.minimumMm.every((item, index) => item < value.maximumMm[index]) &&
    value.minimumMm[1] === 0 && value.maximumMm[1] >= 3_000 &&
    value.maximumMm[1] <= 12_000 && value.wallThicknessMm === 700 &&
    value.floorThicknessMm === 200 &&
    Array.isArray(value.verticalBandMm) && value.verticalBandMm.length === 2 &&
    value.verticalBandMm[0] === 350 && value.verticalBandMm[1] === 3_000 &&
    value.lateralBandMm === 4_000 && value.binSizeMm === 250 &&
    value.minimumBinCount === 64 && value.peakThresholdPermille === 5 &&
    value.adjacentBins === 2;
}

function validPlacementLayout(value) {
  if (!Array.isArray(value) || value.length > 6) return false;
  const ids = new Set();
  return value.every((entry) => exactRecord(entry, ["placementId", "positionMm"]) &&
    typeof entry.placementId === "string" && ID_PATTERN.test(entry.placementId) &&
    !ids.has(entry.placementId) && ids.add(entry.placementId) &&
    safeVector(entry.positionMm, -2_000_000, 2_000_000));
}

function validNodeBindingLayout(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 4096) return false;
  const ids = new Set();
  return value.every((entry) => exactRecord(entry, ["nodeId", "playerSpawn", "actionAnchor"]) &&
    typeof entry.nodeId === "string" && ID_PATTERN.test(entry.nodeId) &&
    !ids.has(entry.nodeId) && ids.add(entry.nodeId) &&
    [entry.playerSpawn, entry.actionAnchor].every((anchor) =>
      exactRecord(anchor, ["positionMm", "yawMilliDegrees"]) &&
      safeVector(anchor.positionMm, -2_000_000, 2_000_000) &&
      Number.isSafeInteger(anchor.yawMilliDegrees) &&
      anchor.yawMilliDegrees >= -180_000 && anchor.yawMilliDegrees <= 180_000));
}

function validNavigation(value, nodeBindingLayout) {
  if (!exactRecord(value, [
    "profile", "cellSizeMm", "agentRadiusMm", "agentHeightMm", "maximumStepMm",
    "minimumClearanceMm", "sourceIslandCount", "cells", "bindings",
  ]) || value.profile !== "collider-agent-grid-v1" || value.cellSizeMm !== 1000 ||
      value.agentRadiusMm !== 350 || value.agentHeightMm !== 2000 ||
      value.maximumStepMm !== 450 || value.minimumClearanceMm !== 700 ||
      !Number.isSafeInteger(value.sourceIslandCount) || value.sourceIslandCount < 1 ||
      !Array.isArray(value.cells) || value.cells.length < 1 || value.cells.length > 16_384 ||
      !value.cells.every((cell) => safeVector(cell, -2_000_000, 2_000_000)) ||
      !Array.isArray(value.bindings) || value.bindings.length !== nodeBindingLayout.length) return false;
  const nodeIds = new Set(nodeBindingLayout.map((binding) => binding.nodeId));
  const bound = new Set();
  return value.bindings.every((binding) => exactRecord(binding, [
    "nodeId", "playerCellIndex", "terminalCellIndex", "pathCellCount",
  ]) && typeof binding.nodeId === "string" && nodeIds.has(binding.nodeId) &&
    !bound.has(binding.nodeId) && bound.add(binding.nodeId) &&
    [binding.playerCellIndex, binding.terminalCellIndex].every((index) =>
      Number.isSafeInteger(index) && index >= 0 && index < value.cells.length) &&
    Number.isSafeInteger(binding.pathCellCount) && binding.pathCellCount >= 2 &&
    binding.pathCellCount <= value.cells.length);
}

function validRenderer(value) {
  return exactRecord(value, [
    "profile", "depthBiasMicros", "depthTestMinAlphaPermille",
    "depthCaptureAlphaPermille",
  ]) && value.profile === "opaque-depth-compose-v1" &&
    value.depthBiasMicros === 0 &&
    value.depthTestMinAlphaPermille === 50 &&
    value.depthCaptureAlphaPermille === 500;
}

function allStringsWellFormed(value) {
  const pending = [value];
  while (pending.length > 0) {
    const current = pending.pop();
    if (typeof current === "string") {
      for (let index = 0; index < current.length; index += 1) {
        const unit = current.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) {
          const next = current.charCodeAt(index + 1);
          if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
          index += 1;
        } else if (unit >= 0xdc00 && unit <= 0xdfff) {
          return false;
        }
      }
    } else if (current && typeof current === "object") {
      for (const [key, child] of Object.entries(current)) pending.push(key, child);
    }
  }
  return true;
}

function validAssemblyShape(value) {
  if (!exactRecord(value, [
    "format", "formatVersion", "canonicalization", "scene", "runtimeIdentity",
    "sources", "environment", "transforms",
  ]) ||
      value.format !== PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT ||
      value.formatVersion !== PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION ||
      value.canonicalization !== CANONICAL_JSON_PROFILE ||
      !exactRecord(value.scene, ["id", "contentVersion", "title"]) ||
      !ID_PATTERN.test(value.scene.id) ||
      typeof value.scene.contentVersion !== "string" ||
      value.scene.contentVersion.length < 1 || value.scene.contentVersion.length > 64 ||
      typeof value.scene.title !== "string" ||
      value.scene.title.length < 1 || value.scene.title.length > 256 ||
      !exactRecord(value.runtimeIdentity, [
        "runtimeFormat", "runtimeFormatVersion", "packId", "packContentVersion",
        "sourceCanonicalSha256", "artifactSha256",
      ]) ||
      value.runtimeIdentity.runtimeFormat !== "matrix-oasis.runtime-game-pack" ||
      value.runtimeIdentity.runtimeFormatVersion !== "0.1.0" ||
      !ID_PATTERN.test(value.runtimeIdentity.packId) ||
      typeof value.runtimeIdentity.packContentVersion !== "string" ||
      value.runtimeIdentity.packContentVersion.length < 1 ||
      value.runtimeIdentity.packContentVersion.length > 64 ||
      !validHash(value.runtimeIdentity.sourceCanonicalSha256) ||
      !validHash(value.runtimeIdentity.artifactSha256) ||
      !exactRecord(value.sources, [
        "scenePackSha256", "prototypeAssemblyReportSha256",
        "spatialEnvironmentBundleSha256", "sceneBlueprintSha256",
      ]) ||
      !Object.values(value.sources).every(validHash) ||
      !exactRecord(value.environment, ["panoramaVisible", "renderer", "splat", "collider"]) ||
      value.environment.panoramaVisible !== false ||
      !validRenderer(value.environment.renderer) ||
      !exactRecord(value.environment.splat, ["path", "sha256", "numGaussians", "derivation"]) ||
      value.environment.splat.path !== "assets/environment.compressed.ply" ||
      !validHash(value.environment.splat.sha256) ||
      !Number.isSafeInteger(value.environment.splat.numGaussians) ||
      value.environment.splat.numGaussians < 1 || value.environment.splat.numGaussians > 2_500_000 ||
      !validSplatDerivation(value.environment.splat.derivation, value.environment.splat.numGaussians) ||
      !exactRecord(value.environment.collider, ["assetId", "placementId", "path", "sha256"]) ||
      value.environment.collider.path !== "assets/environment-collider.glb" ||
      !validHash(value.environment.collider.sha256) ||
      !["assetId", "placementId"].every((key) =>
        typeof value.environment.collider[key] === "string" &&
        ID_PATTERN.test(value.environment.collider[key])) ||
      !(exactRecord(value.transforms, [
        "coordinateTransform", "eulerOrder", "alignment", "root", "splat", "collider", "walkableEnvelope", "placementGroundTargetMm",
      ]) || exactRecord(value.transforms, [
        "coordinateTransform", "eulerOrder", "alignment", "root", "splat", "collider", "walkableEnvelope", "placementGroundTargetMm", "placementLayout",
      ]) || exactRecord(value.transforms, [
        "coordinateTransform", "eulerOrder", "alignment", "root", "splat", "collider", "walkableEnvelope", "placementGroundTargetMm", "placementLayout", "nodeBindingLayout",
      ]) || exactRecord(value.transforms, [
        "coordinateTransform", "eulerOrder", "alignment", "root", "splat", "collider", "walkableEnvelope", "placementGroundTargetMm", "placementLayout", "nodeBindingLayout", "navigation",
      ])) ||
      value.transforms.coordinateTransform !== "spz-raw-ply-to-godot-v1" ||
      value.transforms.eulerOrder !== "YXZ" ||
      !validColliderAlignment(value.transforms.alignment) ||
      !exactRecord(value.transforms.root, ["translationMm", "rotationMilliDegrees"]) ||
      !safeVector(value.transforms.root.translationMm, -2_000_000, 2_000_000) ||
      !safeVector(value.transforms.root.rotationMilliDegrees, -360_000, 360_000) ||
      !exactRecord(value.transforms.splat, ["localTranslationMm", "localRotationMilliDegrees", "scaleMicros"]) ||
      !safeVector(value.transforms.splat.localTranslationMm, -1_000_000, 1_000_000) ||
      !safeVector(value.transforms.splat.localRotationMilliDegrees, -360_000, 360_000) ||
      (value.transforms.alignment.splatProfile === "splat-robust-fit-30m-v1" &&
        (value.transforms.splat.localRotationMilliDegrees[0] !== 0 ||
          value.transforms.splat.localRotationMilliDegrees[1] !== 0 ||
          value.transforms.splat.localRotationMilliDegrees[2] !== 0)) ||
      (value.transforms.alignment.splatProfile === "splat-opencv-to-godot-official-metric-v4" &&
        (value.transforms.splat.localRotationMilliDegrees[0] !== 180_000 ||
          value.transforms.splat.localRotationMilliDegrees[1] !== 0 ||
          value.transforms.splat.localRotationMilliDegrees[2] !== 0)) ||
      !Number.isSafeInteger(value.transforms.splat.scaleMicros) ||
      value.transforms.splat.scaleMicros < 1 || value.transforms.splat.scaleMicros > 100_000_000 ||
      !exactRecord(value.transforms.collider, ["localTranslationMm", "scaleMicros"]) ||
      !safeVector(value.transforms.collider.localTranslationMm, -2_000_000, 2_000_000) ||
      !Number.isSafeInteger(value.transforms.collider.scaleMicros) ||
      value.transforms.collider.scaleMicros < 1 ||
      value.transforms.collider.scaleMicros > 100_000_000 ||
      !validWalkableEnvelope(value.transforms.walkableEnvelope) ||
      (Object.hasOwn(value.transforms, "placementLayout") && !validPlacementLayout(value.transforms.placementLayout)) ||
      (Object.hasOwn(value.transforms, "nodeBindingLayout") && !validNodeBindingLayout(value.transforms.nodeBindingLayout)) ||
      (Object.hasOwn(value.transforms, "navigation") && !validNavigation(
        value.transforms.navigation,
        value.transforms.nodeBindingLayout ?? [],
      )) ||
      (["collider-connected-floor-component-v2", "collider-splat-intersection-component-v3",
        "collider-splat-density-component-v4", "collider-splat-reachable-component-v5",
        "collider-splat-terminal-centered-component-v6", "collider-agent-navigation-component-v7"].includes(
        value.transforms.walkableEnvelope.profile) &&
        !Object.hasOwn(value.transforms, "nodeBindingLayout")) ||
      (value.transforms.walkableEnvelope.profile === "collider-agent-navigation-component-v7" &&
        !Object.hasOwn(value.transforms, "navigation")) ||
      value.transforms.placementGroundTargetMm !== 150) {
    return false;
  }
  return true;
}

export function validatePrototypeSpatialAssemblyJson(text) {
  try {
    const value = canonicalObject(text, 256 * 1024);
    if (!value) {
      return report([diagnostic(
        "integrity",
        "PROTOTYPE_SPATIAL_ASSEMBLY_JSON_NON_CANONICAL",
        "/spatialAssembly",
      )]);
    }
    if (!validAssemblyShape(value)) {
      return report([diagnostic(
        "schema",
        "PROTOTYPE_SPATIAL_ASSEMBLY_SCHEMA_INVALID",
        "/spatialAssembly",
      )]);
    }
    if (!allStringsWellFormed(value)) {
      return report([diagnostic(
        "semantic",
        "PROTOTYPE_SPATIAL_ASSEMBLY_UNSUPPORTED_TEXT",
        "/spatialAssembly",
      )]);
    }
    return report([]);
  } catch (error) {
    if (error instanceof PrototypeSpatialAssemblerOperationalError) throw error;
    throw new PrototypeSpatialAssemblerOperationalError();
  }
}

function sameScene(left, right) {
  return left?.id === right?.id &&
    left?.contentVersion === right?.contentVersion &&
    left?.title === right?.title;
}

function expectedRuntimeIdentity(scenePack) {
  const identity = scenePack.runtimeIdentity;
  return {
    runtimeFormat: identity.runtimeFormat,
    runtimeFormatVersion: identity.runtimeFormatVersion,
    packId: identity.packId,
    packContentVersion: identity.packContentVersion,
    sourceCanonicalSha256: "sha256:" + identity.sourceCanonicalSha256,
    artifactSha256: "sha256:" + identity.artifactSha256,
  };
}

function findEnvironmentBinding(scenePack, spatial) {
  const spatialCollider = spatial.assets.collider;
  const expectedHash = spatialCollider.sha256.slice(7);
  const assets = scenePack.assets.filter((asset) =>
    asset.path === spatialCollider.path &&
    asset.format === "glb" &&
    asset.byteLength === spatialCollider.byteLength &&
    asset.sha256 === expectedHash &&
    Array.isArray(asset.roles) &&
    asset.roles.includes("collider"));
  if (assets.length !== 1) return null;
  const asset = assets[0];
  const placements = scenePack.placements.filter((placement) =>
    placement.colliderAssetId === asset.id);
  if (placements.length !== 1) return null;
  const placement = placements[0];
  if (placement.visualAssetId !== asset.id) return null;
  if (!scenePack.nodeBindings.every((binding) =>
    binding.visiblePlacementIds.includes(placement.id))) return null;
  return { asset, placement };
}

function parseAssemblyReport(value, profile) {
  const expectedSourceProfile = profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id
    ? "matrix-oasis.prototype-assembly/2"
    : "matrix-oasis.prototype-assembly/1";
  if (value?.reportVersion !== 1 ||
      value.profile !== expectedSourceProfile ||
      !validHash(value.inputs?.sceneBlueprintSha256) ||
      !validHash(value.inputs?.prototypeEnvironmentBundleSha256) ||
      !validHash(value.environment?.colliderSha256) ||
      !validHash(value.output?.scenePackSha256)) return null;
  return value;
}

function entryPlayerSpawn(scenePack, runtimePack) {
  const entryIndex = runtimePack?.entryNodeIndex;
  const entryId = Number.isSafeInteger(entryIndex) ? runtimePack.nodes?.[entryIndex]?.id : null;
  if (typeof entryId !== "string") return null;
  const bindings = scenePack.nodeBindings.filter((binding) => binding.nodeId === entryId);
  if (bindings.length !== 1 || !safeVector(bindings[0].playerSpawn?.positionMm, -1_000_000, 1_000_000)) {
    return null;
  }
  return [...bindings[0].playerSpawn.positionMm];
}

function alignedRootTranslation(translation, playerSpawn) {
  const output = [
    translation[0] + playerSpawn[0],
    translation[1],
    translation[2] + playerSpawn[2],
  ];
  return output.every((value) => Number.isSafeInteger(value) &&
    value >= -2_000_000 && value <= 2_000_000) ? output : null;
}

function buildAssembly({
  assemblyReportText,
  assemblyReport,
  scenePackText,
  scenePack,
  runtimePack,
  spatialText,
  spatial,
  binding,
  alignment,
  splatAlignment,
  walkableEnvelope,
  profile,
  placementLayout,
  nodeBindingLayout,
  navigation,
}) {
  const playerSpawn = entryPlayerSpawn(scenePack, runtimePack);
  if (!playerSpawn) return null;
  const rootTranslation = alignedRootTranslation(
    spatial.calibration.godotTranslationMm,
    playerSpawn,
  );
  if (!rootTranslation) return null;
  const splatScaleMicros = splatAlignment.splatScaleMicros;
  const splatLocalTranslationMm = [...splatAlignment.splatLocalTranslationMm];
  if (!Number.isSafeInteger(splatScaleMicros) || splatScaleMicros < 1 ||
      splatScaleMicros > 100_000_000 ||
      !safeVector(splatLocalTranslationMm, -1_000_000, 1_000_000)) return null;
  return {
    format: PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT,
    formatVersion: PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION,
    canonicalization: CANONICAL_JSON_PROFILE,
    scene: { ...scenePack.scene },
    runtimeIdentity: expectedRuntimeIdentity(scenePack),
    sources: {
      scenePackSha256: sha256(scenePackText),
      prototypeAssemblyReportSha256: sha256(assemblyReportText),
      spatialEnvironmentBundleSha256: sha256(spatialText),
      sceneBlueprintSha256: assemblyReport.inputs.sceneBlueprintSha256,
    },
    environment: {
      panoramaVisible: false,
      renderer: {
        profile: "opaque-depth-compose-v1",
        depthBiasMicros: 0,
        depthTestMinAlphaPermille: 50,
        depthCaptureAlphaPermille: 500,
      },
      splat: {
        path: spatial.assets.splat.path,
        sha256: spatial.assets.splat.sha256,
        numGaussians: spatial.assets.splat.numGaussians,
        derivation: structuredClone(spatial.assets.splat.derivation),
      },
      collider: {
        assetId: binding.asset.id,
        placementId: binding.placement.id,
        path: spatial.assets.collider.path,
        sha256: spatial.assets.collider.sha256,
      },
    },
    transforms: {
      coordinateTransform: spatial.calibration.coordinateTransform,
      eulerOrder: "YXZ",
      alignment: {
        profile: alignment.profile,
        targetFloorSpanMm: alignment.targetFloorSpanMm,
        maximumHorizontalSpanMm: alignment.maximumHorizontalSpanMm,
        colliderBoundsMm: {
          minimumMm: [...alignment.colliderBoundsMm.minimumMm],
          maximumMm: [...alignment.colliderBoundsMm.maximumMm],
        },
        centerFloorSampleSourceMm: [...alignment.centerFloorSampleSourceMm],
        splatProfile: splatAlignment.profile,
        splatBoundsProfile: splatAlignment.boundsProfile,
        splatBoundsMm: {
          minimumMm: [...splatAlignment.splatBoundsMm.minimumMm],
          maximumMm: [...splatAlignment.splatBoundsMm.maximumMm],
        },
      },
      root: {
        translationMm: rootTranslation,
        rotationMilliDegrees: [...spatial.calibration.godotRotationMilliDegrees],
      },
      splat: {
        localTranslationMm: splatLocalTranslationMm,
        localRotationMilliDegrees: [...splatAlignment.splatLocalRotationMilliDegrees],
        scaleMicros: splatScaleMicros,
      },
      collider: {
        localTranslationMm: [...alignment.colliderLocalTranslationMm],
        scaleMicros: alignment.colliderScaleMicros,
      },
      walkableEnvelope: {
        profile: walkableEnvelope.profile,
        minimumMm: [...walkableEnvelope.minimumMm],
        maximumMm: [...walkableEnvelope.maximumMm],
        wallThicknessMm: walkableEnvelope.wallThicknessMm,
        floorThicknessMm: walkableEnvelope.floorThicknessMm,
        verticalBandMm: [...walkableEnvelope.verticalBandMm],
        lateralBandMm: walkableEnvelope.lateralBandMm,
        binSizeMm: walkableEnvelope.binSizeMm,
        minimumBinCount: walkableEnvelope.minimumBinCount,
        peakThresholdPermille: walkableEnvelope.peakThresholdPermille,
        adjacentBins: walkableEnvelope.adjacentBins,
      },
      placementGroundTargetMm: 150,
      ...(placementLayout === undefined ? {} : { placementLayout }),
      ...(nodeBindingLayout === undefined ? {} : { nodeBindingLayout }),
      ...(navigation === undefined ? {} : { navigation }),
    },
  };
}

function buildAssemblyReport({
  assembly,
  canonicalAssemblyJson,
  entryPlayerSpawnMm,
  spatialMetricScaleMicros,
  spatialGroundPlaneOffsetMm,
  profile,
}) {
  return {
    reportVersion: 1,
    profile: profile.id,
    inputs: { ...assembly.sources },
    alignment: {
      coordinateTransform: assembly.transforms.coordinateTransform,
      eulerOrder: assembly.transforms.eulerOrder,
      panoramaVisible: false,
      sourceMetricScaleMicros: spatialMetricScaleMicros,
      sourceGroundPlaneOffsetMm: spatialGroundPlaneOffsetMm,
      verticalAlignmentProfile: "collider-calibrated-floor-v1",
      colliderFitProfile: assembly.transforms.alignment.profile,
      splatFitProfile: assembly.transforms.alignment.splatProfile,
      entryPlayerSpawnMm: [...entryPlayerSpawnMm],
      rootTranslationMm: [...assembly.transforms.root.translationMm],
      rootRotationMilliDegrees: [...assembly.transforms.root.rotationMilliDegrees],
      rendererCenterCompensationMm: [...assembly.transforms.splat.localTranslationMm],
      splatLocalRotationMilliDegrees: [...assembly.transforms.splat.localRotationMilliDegrees],
      splatScaleMicros: assembly.transforms.splat.scaleMicros,
      colliderLocalTranslationMm: [...assembly.transforms.collider.localTranslationMm],
      colliderScaleMicros: assembly.transforms.collider.scaleMicros,
      walkableEnvelopeProfile: assembly.transforms.walkableEnvelope.profile,
      walkableEnvelopeMinimumMm: [...assembly.transforms.walkableEnvelope.minimumMm],
      walkableEnvelopeMaximumMm: [...assembly.transforms.walkableEnvelope.maximumMm],
      wallThicknessMm: assembly.transforms.walkableEnvelope.wallThicknessMm,
      floorThicknessMm: assembly.transforms.walkableEnvelope.floorThicknessMm,
      wallDensityVerticalBandMm: [...assembly.transforms.walkableEnvelope.verticalBandMm],
      wallDensityLateralBandMm: assembly.transforms.walkableEnvelope.lateralBandMm,
      wallDensityBinSizeMm: assembly.transforms.walkableEnvelope.binSizeMm,
      wallDensityMinimumBinCount: assembly.transforms.walkableEnvelope.minimumBinCount,
      wallDensityPeakThresholdPermille: assembly.transforms.walkableEnvelope.peakThresholdPermille,
      wallDensityAdjacentBins: assembly.transforms.walkableEnvelope.adjacentBins,
      rendererProfile: assembly.environment.renderer.profile,
      rendererDepthBiasMicros: assembly.environment.renderer.depthBiasMicros,
      placementGroundTargetMm: assembly.transforms.placementGroundTargetMm,
      ...(Object.hasOwn(assembly.transforms, "placementLayout") ? {
        placementLayoutProfile: assembly.transforms.walkableEnvelope.profile ===
          "collider-agent-navigation-component-v7"
          ? "collider-agent-zone-constraint-v2"
          : [
          "collider-global-aabb-floor-grid-v1",
          "collider-connected-floor-component-v2",
          "collider-splat-intersection-component-v3",
          "collider-splat-density-component-v4",
          "collider-splat-reachable-component-v5",
          "collider-splat-terminal-centered-component-v6",
        ].includes(assembly.transforms.walkableEnvelope.profile)
          ? "collider-floor-grid-nearest-anchor-v1"
          : "walkable-envelope-grid-4x2-v1",
        placementLayoutCount: assembly.transforms.placementLayout.length,
      } : {}),
      ...(Object.hasOwn(assembly.transforms, "nodeBindingLayout") ? {
        nodeBindingLayoutProfile: Object.hasOwn(assembly.transforms, "navigation")
          ? "collider-agent-grid-bindings-v2"
          : "collider-connected-floor-bindings-v1",
        nodeBindingLayoutCount: assembly.transforms.nodeBindingLayout.length,
      } : {}),
      ...(Object.hasOwn(assembly.transforms, "navigation") ? {
        navigationProfile: assembly.transforms.navigation.profile,
        navigationCellCount: assembly.transforms.navigation.cells.length,
        navigationSourceIslandCount: assembly.transforms.navigation.sourceIslandCount,
        navigationMinimumClearanceMm: assembly.transforms.navigation.minimumClearanceMm,
      } : {}),
    },
    output: {
      spatialAssemblySha256: sha256(canonicalAssemblyJson),
      splatCount: assembly.environment.splat.numGaussians,
      sourceSplatCount: assembly.environment.splat.derivation.sourceNumGaussians,
      splatLodProfile: assembly.environment.splat.derivation.profile,
      referencedFiles: 2,
    },
  };
}

async function assemble(request, profile) {
  const captured = captureRequest(request);
  if (!captured) return failure("PROTOTYPE_SPATIAL_ASSEMBLY_INPUT_INVALID");
  const files = copyFileMap(captured.spatialEnvironmentFiles);
  if (!files) return failure("PROTOTYPE_SPATIAL_ASSEMBLY_INPUT_INVALID");
  const assemblyReport = parseAssemblyReport(canonicalObject(
    captured.assemblyReportJson,
    LIMITS.assemblyReportBytes,
  ), profile);
  if (!assemblyReport) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_SOURCE_REPORT_INVALID",
      "/prototypeAssemblyReport",
      "integrity",
    );
  }
  const scenePack = canonicalObject(captured.scenePackJson, LIMITS.scenePackBytes);
  const runtimePack = canonicalObject(
    captured.runtimeGamePackJson,
    LIMITS.runtimePackBytes,
  );
  const runtimeReceiptValid = canonicalObject(
    captured.runtimeReceiptJson,
    LIMITS.runtimeReceiptBytes,
  ) !== null;
  const spatial = canonicalObject(
    captured.spatialEnvironmentBundleJson,
    LIMITS.spatialEnvironmentBytes,
  );
  if (!scenePack || !runtimePack || !runtimeReceiptValid || !spatial) {
    return failure("PROTOTYPE_SPATIAL_ASSEMBLY_INPUT_INVALID", "", "integrity");
  }
  const sceneReport = await validateScenePackJson(
    captured.scenePackJson,
    captured.runtimeGamePackJson,
    captured.runtimeReceiptJson,
  );
  if (!validSuccessReport(sceneReport)) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_SCENE_PACK_INVALID",
      "/scenePack",
      "semantic",
    );
  }
  const spatialReport = await validatePrototypeSpatialEnvironmentBundleJson(
    captured.spatialEnvironmentBundleJson,
    files,
  );
  if (!validSuccessReport(spatialReport)) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_ENVIRONMENT_INVALID",
      "/spatialEnvironmentBundle",
      "semantic",
    );
  }
  if (!sameScene(scenePack.scene, spatial.scene) ||
      assemblyReport.output.scenePackSha256 !== sha256(captured.scenePackJson) ||
      assemblyReport.inputs.sceneBlueprintSha256 !== spatial.blueprint.canonicalSha256 ||
      assemblyReport.inputs.prototypeEnvironmentBundleSha256 !==
        spatial.source.environmentBundleSha256 ||
      assemblyReport.environment.colliderSha256 !== spatial.assets.collider.sha256) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_IDENTITY_MISMATCH",
      "/spatialAssembly/sources",
      "semantic",
    );
  }
  const binding = findEnvironmentBinding(scenePack, spatial);
  if (!binding) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_COLLIDER_BINDING_INVALID",
      "/scenePack/assets",
      "semantic",
    );
  }
  const playerSpawn = entryPlayerSpawn(scenePack, runtimePack);
  const colliderBytes = files.get(spatial.assets.collider.path);
  const splatBytes = files.get(spatial.assets.splat.path);
  const useSharedMetricFrame = profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id;
  const alignment = playerSpawn && colliderBytes
    ? await deriveColliderCalibration(
        colliderBytes,
        useSharedMetricFrame ? {
          metricScaleMicros: spatial.calibration.metricScaleMicros,
          groundPlaneOffsetMm: spatial.calibration.groundPlaneOffsetMm,
        } : null,
      )
    : null;
  const splatAlignment = deriveSplatCalibration(
    spatial.statistics,
    spatial.calibration.metricScaleMicros,
    useSharedMetricFrame ? alignment : null,
  );
  const legacyWalkableEnvelope = deriveWalkableEnvelope(spatial.statistics);
  const multiSpaceLayout = profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id && alignment
    ? await deriveColliderWalkableLayout(
      colliderBytes,
      splatBytes,
      alignment,
      splatAlignment,
      spatial.calibration.metricScaleMicros,
      spatial.statistics,
      scenePack.placements.filter((placement) => placement.id !== binding.placement.id),
      playerSpawn,
      scenePack.nodeBindings,
      runtimePack.nodes.map((node) => ({
        nodeId: node.id,
        actionCount: node.actions.length,
      })),
    )
    : null;
  const walkableEnvelope = profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id
    ? multiSpaceLayout?.walkableEnvelope ?? legacyWalkableEnvelope
    : legacyWalkableEnvelope;
  if (!alignment || !splatAlignment || !walkableEnvelope) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_CALIBRATION_INVALID",
      "/spatialEnvironmentBundle/assets/collider",
      "semantic",
    );
  }
  const placementLayout = profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id
    ? multiSpaceLayout?.placementLayout ?? null
    : undefined;
  const nodeBindingLayout = profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id
    ? multiSpaceLayout?.nodeBindingLayout ?? null
    : undefined;
  const navigation = profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id
    ? multiSpaceLayout?.navigation ?? null
    : undefined;
  if (profile.id === PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_V2.id &&
      (placementLayout === null || nodeBindingLayout === null || navigation === null)) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_SAFE_LAYOUT_UNAVAILABLE",
      "/spatialEnvironmentBundle/statistics/sourceInteriorEnvelope",
      "semantic",
    );
  }
  const assembly = buildAssembly({
    assemblyReportText: captured.assemblyReportJson,
    assemblyReport,
    scenePackText: captured.scenePackJson,
    scenePack,
    runtimePack,
    spatialText: captured.spatialEnvironmentBundleJson,
    spatial,
    binding,
    alignment,
    splatAlignment,
    walkableEnvelope,
    profile,
    placementLayout,
    nodeBindingLayout,
    navigation,
  });
  if (!assembly) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_CALIBRATION_INVALID",
      "/spatialEnvironmentBundle/calibration",
      "semantic",
    );
  }
  const canonicalSpatialAssemblyJson = canonicalizeJsonValue(assembly);
  if (!validatePrototypeSpatialAssemblyJson(canonicalSpatialAssemblyJson).valid) {
    throw new PrototypeSpatialAssemblerOperationalError();
  }
  const canonicalSpatialAssemblyReportJson = canonicalizeJsonValue(
    buildAssemblyReport({
      assembly,
      canonicalAssemblyJson: canonicalSpatialAssemblyJson,
      entryPlayerSpawnMm: playerSpawn,
      spatialMetricScaleMicros: spatial.calibration.metricScaleMicros,
      spatialGroundPlaneOffsetMm: spatial.calibration.groundPlaneOffsetMm,
      profile,
    }),
  );
  const referencedFiles = [
    { source: "spatial-environment", path: spatial.assets.splat.path },
    { source: "spatial-environment", path: spatial.assets.collider.path },
  ];
  return deepFreeze({
    ok: true,
    assembly,
    canonicalSpatialAssemblyJson,
    canonicalSpatialAssemblyReportJson,
    referencedFiles,
  });
}

export async function assemblePrototypeSpatialScene(request, options) {
  try {
    const profile = captureProfile(options);
    if (!profile) return failure("PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE_UNSUPPORTED", "/profile");
    return await assemble(request, profile);
  } catch (error) {
    if (error instanceof PrototypeSpatialAssemblerOperationalError) throw error;
    throw new PrototypeSpatialAssemblerOperationalError();
  }
}
