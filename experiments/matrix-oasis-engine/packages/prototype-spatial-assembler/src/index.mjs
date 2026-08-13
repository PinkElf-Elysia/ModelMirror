import { createHash } from "node:crypto";
import {
  validatePrototypeSpatialEnvironmentBundleJson,
} from "@matrix-oasis/prototype-spatial-environment";
import {
  CANONICAL_JSON_PROFILE,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";

export const PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT =
  "matrix-oasis.prototype-spatial-assembly";
export const PROTOTYPE_SPATIAL_ASSEMBLY_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE = Object.freeze({
  id: "matrix-oasis.prototype-spatial-assembly/1",
  panoramaVisible: false,
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
      !exactRecord(value.environment, ["panoramaVisible", "splat", "collider"]) ||
      value.environment.panoramaVisible !== false ||
      !exactRecord(value.environment.splat, ["path", "sha256", "numGaussians"]) ||
      value.environment.splat.path !== "assets/environment.compressed.ply" ||
      !validHash(value.environment.splat.sha256) ||
      !Number.isSafeInteger(value.environment.splat.numGaussians) ||
      value.environment.splat.numGaussians < 1 || value.environment.splat.numGaussians > 2_500_000 ||
      !exactRecord(value.environment.collider, ["assetId", "placementId", "path", "sha256"]) ||
      value.environment.collider.path !== "assets/environment-collider.glb" ||
      !validHash(value.environment.collider.sha256) ||
      !["assetId", "placementId"].every((key) =>
        typeof value.environment.collider[key] === "string" &&
        ID_PATTERN.test(value.environment.collider[key])) ||
      !exactRecord(value.transforms, ["coordinateTransform", "eulerOrder", "root", "splat", "collider"]) ||
      value.transforms.coordinateTransform !== "spz-raw-ply-to-godot-v1" ||
      value.transforms.eulerOrder !== "YXZ" ||
      !exactRecord(value.transforms.root, ["translationMm", "rotationMilliDegrees"]) ||
      !safeVector(value.transforms.root.translationMm, -2_000_000, 2_000_000) ||
      !safeVector(value.transforms.root.rotationMilliDegrees, -360_000, 360_000) ||
      !exactRecord(value.transforms.splat, ["localTranslationMm", "localRotationMilliDegrees", "scaleMicros"]) ||
      !safeVector(value.transforms.splat.localTranslationMm, -1_000_000, 1_000_000) ||
      !safeVector(value.transforms.splat.localRotationMilliDegrees, -360_000, 360_000) ||
      value.transforms.splat.localRotationMilliDegrees[0] !== 0 ||
      value.transforms.splat.localRotationMilliDegrees[1] !== 0 ||
      value.transforms.splat.localRotationMilliDegrees[2] !== -180_000 ||
      !Number.isSafeInteger(value.transforms.splat.scaleMicros) ||
      value.transforms.splat.scaleMicros < 1 || value.transforms.splat.scaleMicros > 100_000_000 ||
      !exactRecord(value.transforms.collider, ["localTranslationMm", "scaleMicros"]) ||
      !safeVector(value.transforms.collider.localTranslationMm, 0, 0) ||
      value.transforms.collider.scaleMicros !== value.transforms.splat.scaleMicros) {
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

function parseAssemblyReport(value) {
  if (value?.reportVersion !== 1 ||
      value.profile !== "matrix-oasis.prototype-assembly/1" ||
      !validHash(value.inputs?.sceneBlueprintSha256) ||
      !validHash(value.inputs?.prototypeEnvironmentBundleSha256) ||
      !validHash(value.environment?.colliderSha256) ||
      !validHash(value.output?.scenePackSha256)) return null;
  return value;
}

function addGroundOffset(translation, offset) {
  const output = [translation[0], translation[1] + offset, translation[2]];
  return output.every((value) => Number.isSafeInteger(value) &&
    value >= -2_000_000 && value <= 2_000_000) ? output : null;
}

function buildAssembly({
  assemblyReportText,
  assemblyReport,
  scenePackText,
  scenePack,
  spatialText,
  spatial,
  binding,
}) {
  const rootTranslation = addGroundOffset(
    spatial.calibration.godotTranslationMm,
    spatial.calibration.groundPlaneOffsetMm,
  );
  if (!rootTranslation) return null;
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
      splat: {
        path: spatial.assets.splat.path,
        sha256: spatial.assets.splat.sha256,
        numGaussians: spatial.assets.splat.numGaussians,
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
      root: {
        translationMm: rootTranslation,
        rotationMilliDegrees: [...spatial.calibration.godotRotationMilliDegrees],
      },
      splat: {
        localTranslationMm: [...spatial.statistics.rendererCenterCompensationMm],
        localRotationMilliDegrees: [0, 0, -180_000],
        scaleMicros: spatial.calibration.metricScaleMicros,
      },
      collider: {
        localTranslationMm: [0, 0, 0],
        scaleMicros: spatial.calibration.metricScaleMicros,
      },
    },
  };
}

function buildAssemblyReport({ assembly, canonicalAssemblyJson }) {
  return {
    reportVersion: 1,
    profile: PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE.id,
    inputs: { ...assembly.sources },
    alignment: {
      coordinateTransform: assembly.transforms.coordinateTransform,
      eulerOrder: assembly.transforms.eulerOrder,
      panoramaVisible: false,
      metricScaleMicros: assembly.transforms.splat.scaleMicros,
      rootTranslationMm: [...assembly.transforms.root.translationMm],
      rootRotationMilliDegrees: [...assembly.transforms.root.rotationMilliDegrees],
      rendererCenterCompensationMm: [...assembly.transforms.splat.localTranslationMm],
      splatLocalRotationMilliDegrees: [...assembly.transforms.splat.localRotationMilliDegrees],
    },
    output: {
      spatialAssemblySha256: sha256(canonicalAssemblyJson),
      splatCount: assembly.environment.splat.numGaussians,
      referencedFiles: 2,
    },
  };
}

async function assemble(request) {
  const captured = captureRequest(request);
  if (!captured) return failure("PROTOTYPE_SPATIAL_ASSEMBLY_INPUT_INVALID");
  const files = copyFileMap(captured.spatialEnvironmentFiles);
  if (!files) return failure("PROTOTYPE_SPATIAL_ASSEMBLY_INPUT_INVALID");
  const assemblyReport = parseAssemblyReport(canonicalObject(
    captured.assemblyReportJson,
    LIMITS.assemblyReportBytes,
  ));
  if (!assemblyReport) {
    return failure(
      "PROTOTYPE_SPATIAL_ASSEMBLY_SOURCE_REPORT_INVALID",
      "/prototypeAssemblyReport",
      "integrity",
    );
  }
  const scenePack = canonicalObject(captured.scenePackJson, LIMITS.scenePackBytes);
  const runtimePackValid = canonicalObject(
    captured.runtimeGamePackJson,
    LIMITS.runtimePackBytes,
  ) !== null;
  const runtimeReceiptValid = canonicalObject(
    captured.runtimeReceiptJson,
    LIMITS.runtimeReceiptBytes,
  ) !== null;
  const spatial = canonicalObject(
    captured.spatialEnvironmentBundleJson,
    LIMITS.spatialEnvironmentBytes,
  );
  if (!scenePack || !runtimePackValid || !runtimeReceiptValid || !spatial) {
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
  const assembly = buildAssembly({
    assemblyReportText: captured.assemblyReportJson,
    assemblyReport,
    scenePackText: captured.scenePackJson,
    scenePack,
    spatialText: captured.spatialEnvironmentBundleJson,
    spatial,
    binding,
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
    buildAssemblyReport({ assembly, canonicalAssemblyJson: canonicalSpatialAssemblyJson }),
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

export async function assemblePrototypeSpatialScene(request) {
  try {
    return await assemble(request);
  } catch (error) {
    if (error instanceof PrototypeSpatialAssemblerOperationalError) throw error;
    throw new PrototypeSpatialAssemblerOperationalError();
  }
}
