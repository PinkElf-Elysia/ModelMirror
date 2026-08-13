import Ajv2020 from "ajv/dist/2020.js";
import {
  CANONICAL_JSON_PROFILE,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import { validatePrototypeEnvironmentBundleJson } from "@matrix-oasis/prototype-environment-pipeline";
import { convertSpzToCompressedPly, inspectCompressedPly } from "./convert.mjs";
import { diagnostic, failure, report } from "./diagnostics.mjs";
import { PrototypeSpatialEnvironmentOperationalError } from "./operational.mjs";
import {
  captureIntegerVector,
  captureRecord,
  copyBytes,
  copyFiles,
  deepFreeze,
  parseCanonical,
  sha256,
} from "./safety.mjs";

export const PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT =
  "matrix-oasis.prototype-spatial-environment-bundle";
export const PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS = Object.freeze({
  manifestBytes: 256 * 1024,
  spzBytes: 64 * 1024 * 1024,
  compressedPlyBytes: 96 * 1024 * 1024,
  colliderBytes: 32 * 1024 * 1024,
  totalBundleBytes: 256 * 1024 * 1024,
  maxSplats: 2_500_000,
  runtimeSplatTarget: 640_000,
  decimationMemoryBudgetBytes: 2 * 1024 * 1024 * 1024,
});

const SPLAT_PATH = "assets/environment.compressed.ply";
const COLLIDER_PATH = "assets/environment-collider.glb";
const HASH = "^sha256:[0-9a-f]{64}$";
const SAFE = Number.MAX_SAFE_INTEGER;

const schema = {
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "scene", "blueprint", "source", "assets", "calibration", "statistics", "toolchain"],
  properties: {
    format: { const: PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT },
    formatVersion: { const: PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT_VERSION },
    canonicalization: { const: CANONICAL_JSON_PROFILE },
    scene: { $ref: "#/$defs/scene" },
    blueprint: { $ref: "#/$defs/blueprint" },
    source: { $ref: "#/$defs/source" },
    assets: { $ref: "#/$defs/assets" },
    calibration: { $ref: "#/$defs/calibration" },
    statistics: { $ref: "#/$defs/statistics" },
    toolchain: { $ref: "#/$defs/toolchain" },
  },
  $defs: {
    id: { type: "string", minLength: 1, maxLength: 96, pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$" },
    hash: { type: "string", pattern: HASH },
    safeInteger: { type: "integer", minimum: -SAFE, maximum: SAFE },
    vector: {
      type: "array", minItems: 3, maxItems: 3,
      items: { $ref: "#/$defs/safeInteger" },
    },
    scene: {
      type: "object", additionalProperties: false,
      required: ["id", "contentVersion", "title"],
      properties: {
        id: { $ref: "#/$defs/id" },
        contentVersion: { type: "string", minLength: 1, maxLength: 64, pattern: "\\S" },
        title: { type: "string", minLength: 1, maxLength: 4096, pattern: "\\S" },
      },
    },
    blueprint: {
      type: "object", additionalProperties: false,
      required: ["format", "formatVersion", "canonicalSha256"],
      properties: {
        format: { const: "matrix-oasis.scene-blueprint" },
        formatVersion: { const: "0.1.0" },
        canonicalSha256: { $ref: "#/$defs/hash" },
      },
    },
    source: {
      type: "object", additionalProperties: false,
      required: ["environmentBundleSha256", "format", "byteLength", "sha256"],
      properties: {
        environmentBundleSha256: { $ref: "#/$defs/hash" },
        format: { const: "spz" },
        byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.spzBytes },
        sha256: { $ref: "#/$defs/hash" },
      },
    },
    splat: {
      type: "object", additionalProperties: false,
      required: ["path", "format", "byteLength", "sha256", "numGaussians", "numLods", "shBands", "derivation"],
      properties: {
        path: { const: SPLAT_PATH }, format: { const: "compressed-ply" },
        byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.compressedPlyBytes },
        sha256: { $ref: "#/$defs/hash" },
        numGaussians: { type: "integer", minimum: 1, maximum: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.maxSplats },
        numLods: { const: 1 }, shBands: { type: "integer", minimum: 0, maximum: 3 },
        derivation: { $ref: "#/$defs/splatDerivation" },
      },
    },
    splatDerivation: {
      type: "object", additionalProperties: false,
      required: ["profile", "targetNumGaussians", "sourceNumGaussians", "fullResolutionCompressedPly"],
      properties: {
        profile: { enum: ["identity-v1", "mpmm-uniform-v1"] },
        targetNumGaussians: { const: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.runtimeSplatTarget },
        sourceNumGaussians: { type: "integer", minimum: 1, maximum: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.maxSplats },
        fullResolutionCompressedPly: {
          type: "object", additionalProperties: false,
          required: ["byteLength", "sha256", "numGaussians"],
          properties: {
            byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.compressedPlyBytes },
            sha256: { $ref: "#/$defs/hash" },
            numGaussians: { type: "integer", minimum: 1, maximum: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.maxSplats },
          },
        },
      },
    },
    metrics: {
      type: "object", additionalProperties: false,
      required: ["nodeCount", "meshCount", "surfaceCount", "triangleCount"],
      properties: {
        nodeCount: { type: "integer", minimum: 0, maximum: 256 },
        meshCount: { type: "integer", minimum: 0, maximum: 64 },
        surfaceCount: { type: "integer", minimum: 0, maximum: 128 },
        triangleCount: { type: "integer", minimum: 0, maximum: 250000 },
      },
    },
    collider: {
      type: "object", additionalProperties: false,
      required: ["path", "format", "byteLength", "sha256", "metrics"],
      properties: {
        path: { const: COLLIDER_PATH }, format: { const: "glb" },
        byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.colliderBytes },
        sha256: { $ref: "#/$defs/hash" }, metrics: { $ref: "#/$defs/metrics" },
      },
    },
    assets: {
      type: "object", additionalProperties: false,
      required: ["splat", "collider"],
      properties: { splat: { $ref: "#/$defs/splat" }, collider: { $ref: "#/$defs/collider" } },
    },
    calibration: {
      type: "object", additionalProperties: false,
      required: ["coordinateTransform", "metricScaleMicros", "groundPlaneOffsetMm", "godotTranslationMm", "godotRotationMilliDegrees"],
      properties: {
        coordinateTransform: { const: "spz-raw-ply-to-godot-v1" },
        metricScaleMicros: { type: "integer", minimum: 1, maximum: 100000000 },
        groundPlaneOffsetMm: { type: "integer", minimum: -1000000, maximum: 1000000 },
        godotTranslationMm: { $ref: "#/$defs/vector" },
        godotRotationMilliDegrees: { $ref: "#/$defs/vector" },
      },
    },
    bounds: {
      type: "object", additionalProperties: false,
      required: ["minimumMm", "maximumMm"],
      properties: { minimumMm: { $ref: "#/$defs/vector" }, maximumMm: { $ref: "#/$defs/vector" } },
    },
    statistics: {
      type: "object", additionalProperties: false,
      required: ["sourceBounds", "runtimeRobustBounds", "sourceInteriorEnvelope", "sourceMeanMm", "rendererCenterCompensationMm"],
      properties: {
        sourceBounds: { $ref: "#/$defs/bounds" },
        runtimeRobustBounds: {
          type: "object", additionalProperties: false,
          required: ["profile", "minimumMm", "maximumMm"],
          properties: {
            profile: { const: "source-position-percentile-1-99-v1" },
            minimumMm: { $ref: "#/$defs/vector" },
            maximumMm: { $ref: "#/$defs/vector" },
          },
        },
        sourceMeanMm: { $ref: "#/$defs/vector" },
        rendererCenterCompensationMm: { $ref: "#/$defs/vector" },
        sourceInteriorEnvelope: {
          anyOf: [
            { type: "null" },
            {
              type: "object", additionalProperties: false,
              required: [
                "profile", "coordinateSpace", "minimumMm", "maximumMm",
                "verticalBandMm", "lateralBandMm", "binSizeMm",
                "minimumBinCount", "peakThresholdPermille", "adjacentBins",
              ],
              properties: {
                profile: { const: "source-density-first-surface-v1" },
                coordinateSpace: { const: "splat-robust-fit-30m-v1" },
                minimumMm: { $ref: "#/$defs/vector" },
                maximumMm: { $ref: "#/$defs/vector" },
                verticalBandMm: {
                  type: "array", minItems: 2, maxItems: 2,
                  prefixItems: [
                    { type: "integer", const: 350 },
                    { type: "integer", const: 3000 },
                  ],
                },
                lateralBandMm: { type: "integer", const: 4000 },
                binSizeMm: { type: "integer", const: 250 },
                minimumBinCount: { type: "integer", const: 64 },
                peakThresholdPermille: { type: "integer", const: 5 },
                adjacentBins: { type: "integer", const: 2 },
              },
            },
          ],
        },
      },
    },
    toolchain: {
      type: "object", additionalProperties: false,
      required: ["converter", "decoder"],
      properties: {
        converter: {
          type: "object", additionalProperties: false, required: ["id", "version"],
          properties: { id: { const: "@playcanvas/splat-transform" }, version: { const: "3.3.0" } },
        },
        decoder: {
          type: "object", additionalProperties: false, required: ["id", "version"],
          properties: { id: { const: "@adobe/spz" }, version: { const: "0.2.2" } },
        },
      },
    },
  },
};

const ajv = new Ajv2020({ allErrors: true, strict: true, validateFormats: false, ownProperties: true });
const validateStructure = ajv.compile(schema);

function schemaPath(errors) {
  const first = [...(errors ?? [])].sort((left, right) => left.instancePath < right.instancePath ? -1 : left.instancePath > right.instancePath ? 1 : 0)[0];
  return first?.instancePath ? `/spatialEnvironmentBundle${first.instancePath}` : "/spatialEnvironmentBundle";
}

function captureCalibration(value) {
  const record = captureRecord(value, ["coordinateTransform", "metricScaleMicros", "groundPlaneOffsetMm", "godotTranslationMm", "godotRotationMilliDegrees"]);
  if (!record || record.coordinateTransform !== "spz-raw-ply-to-godot-v1" ||
      !Number.isSafeInteger(record.metricScaleMicros) || record.metricScaleMicros < 1 || record.metricScaleMicros > 100_000_000 ||
      !Number.isSafeInteger(record.groundPlaneOffsetMm) || record.groundPlaneOffsetMm < -1_000_000 || record.groundPlaneOffsetMm > 1_000_000) return null;
  const translation = captureIntegerVector(record.godotTranslationMm, { minimum: -1_000_000, maximum: 1_000_000 });
  const rotation = captureIntegerVector(record.godotRotationMilliDegrees, { minimum: -360_000, maximum: 360_000 });
  if (!translation || !rotation) return null;
  return {
    coordinateTransform: record.coordinateTransform,
    metricScaleMicros: record.metricScaleMicros,
    groundPlaneOffsetMm: Object.is(record.groundPlaneOffsetMm, -0) ? 0 : record.groundPlaneOffsetMm,
    godotTranslationMm: translation,
    godotRotationMilliDegrees: rotation,
  };
}

function millimeters(vector, metricScaleMicros) {
  const output = vector.map((value) => Math.round(value * metricScaleMicros / 1000));
  return output.every(Number.isSafeInteger) ? output.map((value) => Object.is(value, -0) ? 0 : value) : null;
}

function parseEnvironmentBundle(text) {
  try { return JSON.parse(text); } catch { throw new PrototypeSpatialEnvironmentOperationalError(); }
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
        } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
      }
    } else if (current && typeof current === "object") {
      for (const [key, child] of Object.entries(current)) {
        pending.push(key, child);
      }
    }
  }
  return true;
}

function copyOutputFiles(files) {
  const copied = copyFiles(files);
  if (!copied || copied.size !== 2 || !copied.has(SPLAT_PATH) || !copied.has(COLLIDER_PATH)) return null;
  return copied;
}

function validSplatDerivation(splat) {
  const derivation = splat.derivation;
  const sourceCount = derivation.sourceNumGaussians;
  if (derivation.fullResolutionCompressedPly.numGaussians !== sourceCount ||
      splat.numGaussians > sourceCount) return false;
  if (sourceCount <= derivation.targetNumGaussians) {
    return derivation.profile === "identity-v1" && splat.numGaussians === sourceCount &&
      splat.byteLength === derivation.fullResolutionCompressedPly.byteLength &&
      splat.sha256 === derivation.fullResolutionCompressedPly.sha256;
  }
  return derivation.profile === "mpmm-uniform-v1" &&
    splat.numGaussians === derivation.targetNumGaussians;
}

export async function validatePrototypeSpatialEnvironmentBundleJson(text, files) {
  try {
    const value = parseCanonical(text, PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.manifestBytes, canonicalizeJsonValue);
    if (!value) return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_ENVIRONMENT_JSON_NON_CANONICAL", "/spatialEnvironmentBundle")]);
    if (!validateStructure(value)) return report([diagnostic("schema", "PROTOTYPE_SPATIAL_ENVIRONMENT_SCHEMA_INVALID", schemaPath(validateStructure.errors))]);
    if (!allStringsWellFormed(value)) return report([diagnostic("semantic", "PROTOTYPE_SPATIAL_ENVIRONMENT_UNSUPPORTED_TEXT", "/spatialEnvironmentBundle")]);
    const capturedFiles = copyOutputFiles(files);
    if (!capturedFiles) return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_ENVIRONMENT_FILES_INVALID", "/spatialEnvironmentBundle/assets")]);
    const splatBytes = capturedFiles.get(SPLAT_PATH);
    const colliderBytes = capturedFiles.get(COLLIDER_PATH);
    if (splatBytes.byteLength + colliderBytes.byteLength > PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.totalBundleBytes) {
      return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_ENVIRONMENT_TOTAL_SIZE_LIMIT", "/spatialEnvironmentBundle/assets")]);
    }
    const inspected = await inspectCompressedPly(splatBytes, PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS);
    if (!inspected) return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_ENVIRONMENT_COMPRESSED_PLY_INVALID", "/spatialEnvironmentBundle/assets/splat/path")]);
    if (!validSplatDerivation(value.assets.splat) ||
      value.assets.splat.byteLength !== splatBytes.byteLength || value.assets.splat.sha256 !== sha256(splatBytes) ||
      value.assets.splat.numGaussians !== inspected.numGaussians || value.assets.splat.numLods !== inspected.numLods || value.assets.splat.shBands !== inspected.shBands ||
      value.assets.collider.byteLength !== colliderBytes.byteLength || value.assets.collider.sha256 !== sha256(colliderBytes)
    ) return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_ENVIRONMENT_FILE_IDENTITY_MISMATCH", "/spatialEnvironmentBundle/assets")]);
    return report([]);
  } catch (error) {
    if (error instanceof PrototypeSpatialEnvironmentOperationalError) throw error;
    throw new PrototypeSpatialEnvironmentOperationalError();
  }
}

export async function materializePrototypeSpatialEnvironment(request) {
  try {
    const captured = captureRecord(request, ["environmentBundleJson", "environmentFiles", "spzBytes", "calibration"]);
    if (!captured || typeof captured.environmentBundleJson !== "string") return failure("PROTOTYPE_SPATIAL_ENVIRONMENT_REQUEST_INVALID");
    const environmentFiles = copyFiles(captured.environmentFiles);
    const spzBytes = copyBytes(captured.spzBytes, PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS.spzBytes);
    const calibration = captureCalibration(captured.calibration);
    if (!environmentFiles || !spzBytes || !calibration) return failure("PROTOTYPE_SPATIAL_ENVIRONMENT_REQUEST_INVALID");
    const environmentValidation = validatePrototypeEnvironmentBundleJson(captured.environmentBundleJson, environmentFiles);
    if (!environmentValidation.valid) return failure("PROTOTYPE_SPATIAL_ENVIRONMENT_SOURCE_ENVIRONMENT_INVALID", "integrity", "/environmentBundle");
    const environment = parseEnvironmentBundle(captured.environmentBundleJson);
    const colliderBytes = environmentFiles.get(COLLIDER_PATH);
    if (!(colliderBytes instanceof Uint8Array)) return failure("PROTOTYPE_SPATIAL_ENVIRONMENT_SOURCE_ENVIRONMENT_INVALID", "integrity", "/environmentBundle/assets/collider/path");

    const converted = await convertSpzToCompressedPly(spzBytes, PROTOTYPE_SPATIAL_ENVIRONMENT_LIMITS);
    if (!converted.ok) return failure(converted.code, "conversion", "/spatialEnvironmentBundle/source");
    const minimumMm = millimeters(converted.metadata.bounds.minimum, calibration.metricScaleMicros);
    const maximumMm = millimeters(converted.metadata.bounds.maximum, calibration.metricScaleMicros);
    const meanMm = millimeters(converted.metadata.bounds.mean, calibration.metricScaleMicros);
    const robustMinimumMm = millimeters(
      converted.metadata.robustBounds.minimum,
      calibration.metricScaleMicros,
    );
    const robustMaximumMm = millimeters(
      converted.metadata.robustBounds.maximum,
      calibration.metricScaleMicros,
    );
    if (!minimumMm || !maximumMm || !meanMm || !robustMinimumMm || !robustMaximumMm ||
        robustMinimumMm.some((value, index) => value > robustMaximumMm[index])) {
      return failure("PROTOTYPE_SPATIAL_ENVIRONMENT_CALIBRATION_INVALID", "semantic", "/spatialEnvironmentBundle/calibration");
    }
    const rendererCenterCompensationMm = [...meanMm];

    const bundle = deepFreeze({
      format: PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT,
      formatVersion: PROTOTYPE_SPATIAL_ENVIRONMENT_BUNDLE_FORMAT_VERSION,
      canonicalization: CANONICAL_JSON_PROFILE,
      scene: {
        id: environment.scene.id,
        contentVersion: environment.scene.contentVersion,
        title: environment.scene.title,
      },
      blueprint: { ...environment.blueprint },
      source: {
        environmentBundleSha256: sha256(captured.environmentBundleJson),
        format: "spz",
        byteLength: spzBytes.byteLength,
        sha256: sha256(spzBytes),
      },
      assets: {
        splat: {
          path: SPLAT_PATH,
          format: "compressed-ply",
          byteLength: converted.bytes.byteLength,
          sha256: sha256(converted.bytes),
          numGaussians: converted.metadata.runtimeNumGaussians,
          numLods: converted.metadata.numLods,
          shBands: converted.metadata.shBands,
          derivation: converted.metadata.derivation,
        },
        collider: {
          path: COLLIDER_PATH,
          format: "glb",
          byteLength: colliderBytes.byteLength,
          sha256: sha256(colliderBytes),
          metrics: { ...environment.assets.collider.metrics },
        },
      },
      calibration,
      statistics: {
        sourceBounds: { minimumMm, maximumMm },
        runtimeRobustBounds: {
          profile: converted.metadata.robustBounds.profile,
          minimumMm: robustMinimumMm,
          maximumMm: robustMaximumMm,
        },
        sourceMeanMm: meanMm,
        rendererCenterCompensationMm,
        sourceInteriorEnvelope: converted.metadata.interiorEnvelope === null
          ? null
          : {
              ...converted.metadata.interiorEnvelope,
              minimumMm: [...converted.metadata.interiorEnvelope.minimumMm],
              maximumMm: [...converted.metadata.interiorEnvelope.maximumMm],
              verticalBandMm: [...converted.metadata.interiorEnvelope.verticalBandMm],
            },
      },
      toolchain: {
        converter: { id: "@playcanvas/splat-transform", version: "3.3.0" },
        decoder: { id: "@adobe/spz", version: "0.2.2" },
      },
    });
    const canonicalBundleJson = canonicalizeJsonValue(bundle);
    const files = Object.freeze([
      Object.freeze({ path: SPLAT_PATH, bytes: Uint8Array.prototype.slice.call(converted.bytes) }),
      Object.freeze({ path: COLLIDER_PATH, bytes: Uint8Array.prototype.slice.call(colliderBytes) }),
    ]);
    const validation = await validatePrototypeSpatialEnvironmentBundleJson(canonicalBundleJson, new Map(files.map((file) => [file.path, file.bytes])));
    if (!validation.valid) throw new PrototypeSpatialEnvironmentOperationalError();
    const canonicalReportJson = canonicalizeJsonValue(deepFreeze({
      format: "matrix-oasis.prototype-spatial-environment-materialization-report",
      formatVersion: "0.1.0",
      bundleSha256: sha256(canonicalBundleJson),
      source: { format: "spz", byteLength: spzBytes.byteLength, sha256: sha256(spzBytes) },
      splat: { ...bundle.assets.splat },
      collider: { ...bundle.assets.collider },
      calibration: bundle.calibration,
      statistics: bundle.statistics,
      toolchain: bundle.toolchain,
    }));
    return Object.freeze({ ok: true, bundle, canonicalBundleJson, canonicalReportJson, files });
  } catch (error) {
    if (error instanceof PrototypeSpatialEnvironmentOperationalError) throw error;
    throw new PrototypeSpatialEnvironmentOperationalError();
  }
}
