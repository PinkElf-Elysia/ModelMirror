import { createHash } from "node:crypto";
import Ajv2020 from "ajv/dist/2020.js";
import {
  SCENE_BLUEPRINT_SCHEMA,
} from "@matrix-oasis/prototype-generation-contracts";
import {
  CANONICAL_JSON_PROFILE,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import { inspectEnvironmentCollider, inspectPanoramaPng } from "./binary-inspection.mjs";
import {
  acquireMarbleEnvironment,
  acquireMarbleEnvironmentWithSpatialSource,
  MARBLE_PROVIDER_LIMITS,
  MARBLE_PROVIDER_MODEL,
} from "./marble-provider.mjs";
import { PrototypeEnvironmentPipelineOperationalError } from "./operational.mjs";

export const PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT =
  "matrix-oasis.prototype-environment-bundle";
export const PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_SPATIAL_SOURCE_BUNDLE_FORMAT =
  "matrix-oasis.prototype-spatial-source-bundle";
export const PROTOTYPE_SPATIAL_SOURCE_BUNDLE_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_ENVIRONMENT_LIMITS = Object.freeze({
  manifestBytes: 256 * 1024,
  panoramaBytes: 64 * 1024 * 1024,
  panoramaWidth: 16_384,
  panoramaHeight: 8_192,
  colliderBytes: 32 * 1024 * 1024,
  spzBytes: 64 * 1024 * 1024,
});

const SHA256 = "^sha256:[0-9a-f]{64}$";
const PANORAMA_PATH = "assets/environment-panorama.png";
const COLLIDER_PATH = "assets/environment-collider.glb";
const SPZ_PATH = "assets/environment.spz";
const preparedPlans = new WeakMap();

const REUSABLE_ENVIRONMENT_PROFILE = [
  "Create a reusable first-person prototype environment from the supplied scene intent and visual style.",
  "Build one self-contained rectangular room at realistic human scale, with a continuous level floor, four solid perimeter walls, a high ceiling, and a wide unobstructed central circulation area.",
  "Around the perimeter, imply four clearly readable functional zones using architecture and lighting only: an equipment bay, a storage alcove, an observation or work area, and a quiet briefing corner.",
  "Leave generous empty floor and wall space so externally generated props and one static character can be placed later without visual conflict.",
  "Use clean modular construction, restrained matte concrete and metal, subtle wear, soft even neutral lighting, and moderate production-quality detail while preserving the supplied visual style.",
  "Use coherent perspective, strong depth cues, continuous floor-wall-ceiling boundaries, and a complete seamless 360-degree view from a standing eye-height viewpoint near the room center.",
  "Keep the space easy to navigate and collision-friendly: no stairs, pits, narrow passages, unreachable platforms, open voids, loose clutter, furniture blocking routes, people, creatures, vehicles, text, logos, signage, UI, doors that must animate, mirrors, transparent walls, strong reflections, extreme darkness, fog, or outdoor vistas.",
  "The result should be polished but modular enough to reuse as an engine demo, interaction testbed, or foundation for multiple prototype genres.",
].join(" ");

const MULTI_SPACE_ENVIRONMENT_PROFILE = [
  "Create a reusable first-person environment matching the supplied scene intent and style.",
  "Keep the primary space fully enterable and bounded, with the standing eye-height viewpoint inside it. Connect each distinct secondary space by a wide, permanently open, walkable threshold.",
  "Do not substitute an exterior facade, isolated shell, duplicated or mirrored structure, closed door, solid barrier, or fake opening for a requested connection.",
  "Use level floors across thresholds, solid perimeter and partition walls on every side of the play area, sufficient ceiling height, and clear circulation.",
  "Reserve clear placement space for every external prop and static character; do not generate them.",
  "Follow the requested architecture, materials, lighting, wear, and style without imposing generic room functions.",
  "Use coherent perspective, depth cues, continuous floor-wall-ceiling boundaries, and a seamless 360-degree view of the connected layout.",
  "Keep it collision-friendly: no pits, narrow passages, unreachable surfaces, open voids, clutter, blocked routes, text, logos, signage, UI, animated doors, mirrors, transparent walls, strong reflections, extreme darkness, fog, or outdoor vistas.",
  "Produce a coherent reusable gameplay space.",
].join(" ");

const MULTI_SPACE_PROFILE = "matrix-oasis.prototype-environment/2";

const environmentBundleSchema = {
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "scene", "blueprint", "provider", "assets"],
  properties: {
    format: { const: PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT },
    formatVersion: { const: PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT_VERSION },
    canonicalization: { const: CANONICAL_JSON_PROFILE },
    scene: { $ref: "#/$defs/scene" },
    blueprint: { $ref: "#/$defs/blueprint" },
    provider: { $ref: "#/$defs/provider" },
    assets: { $ref: "#/$defs/assets" },
  },
  $defs: {
    id: { type: "string", minLength: 1, maxLength: 96, pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$" },
    hash: { type: "string", pattern: SHA256 },
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
    provider: {
      type: "object", additionalProperties: false,
      required: ["id", "model", "environmentPromptSha256"],
      properties: {
        id: { const: "world-labs-marble" },
        model: { const: MARBLE_PROVIDER_MODEL },
        environmentPromptSha256: { $ref: "#/$defs/hash" },
      },
    },
    panorama: {
      type: "object", additionalProperties: false,
      required: ["path", "format", "width", "height", "byteLength", "sha256"],
      properties: {
        path: { const: PANORAMA_PATH }, format: { const: "png" },
        width: { type: "integer", minimum: 2, maximum: PROTOTYPE_ENVIRONMENT_LIMITS.panoramaWidth },
        height: { type: "integer", minimum: 1, maximum: PROTOTYPE_ENVIRONMENT_LIMITS.panoramaHeight },
        byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_ENVIRONMENT_LIMITS.panoramaBytes },
        sha256: { $ref: "#/$defs/hash" },
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
        byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_ENVIRONMENT_LIMITS.colliderBytes },
        sha256: { $ref: "#/$defs/hash" }, metrics: { $ref: "#/$defs/metrics" },
      },
    },
    assets: {
      type: "object", additionalProperties: false,
      required: ["panorama", "collider"],
      properties: { panorama: { $ref: "#/$defs/panorama" }, collider: { $ref: "#/$defs/collider" } },
    },
  },
};

const ajv = new Ajv2020({ allErrors: true, strict: true, validateFormats: false, ownProperties: true });
const validateBlueprintStructure = ajv.compile(SCENE_BLUEPRINT_SCHEMA);
const validateBundleStructure = ajv.compile(environmentBundleSchema);
const spatialSourceBundleSchema = {
  type: "object",
  additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "scene", "blueprint", "environment", "source", "scale"],
  properties: {
    format: { const: PROTOTYPE_SPATIAL_SOURCE_BUNDLE_FORMAT },
    formatVersion: { const: PROTOTYPE_SPATIAL_SOURCE_BUNDLE_FORMAT_VERSION },
    canonicalization: { const: CANONICAL_JSON_PROFILE },
    scene: { $ref: "#/$defs/scene" },
    blueprint: { $ref: "#/$defs/blueprint" },
    environment: {
      type: "object", additionalProperties: false,
      required: ["bundleSha256", "collider"],
      properties: {
        bundleSha256: { $ref: "#/$defs/hash" },
        collider: {
          type: "object", additionalProperties: false,
          required: ["path", "byteLength", "sha256"],
          properties: {
            path: { const: COLLIDER_PATH },
            byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_ENVIRONMENT_LIMITS.colliderBytes },
            sha256: { $ref: "#/$defs/hash" },
          },
        },
      },
    },
    source: {
      type: "object", additionalProperties: false,
      required: ["path", "format", "resolution", "byteLength", "sha256"],
      properties: {
        path: { const: SPZ_PATH }, format: { const: "spz" }, resolution: { const: "full_res" },
        byteLength: { type: "integer", minimum: 1, maximum: PROTOTYPE_ENVIRONMENT_LIMITS.spzBytes },
        sha256: { $ref: "#/$defs/hash" },
      },
    },
    scale: {
      type: "object", additionalProperties: false,
      required: ["metricScaleMicros", "groundPlaneOffsetMm"],
      properties: {
        metricScaleMicros: { type: "integer", minimum: 1, maximum: 100000000 },
        groundPlaneOffsetMm: { type: "integer", minimum: -1000000, maximum: 1000000 },
      },
    },
  },
  $defs: environmentBundleSchema.$defs,
};
const validateSpatialSourceStructure = ajv.compile(spatialSourceBundleSchema);

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value) || value instanceof Uint8Array) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function diagnostic(phase, code, path = "") {
  return Object.freeze({ phase, severity: "error", code, path, message: code });
}

function failure(code, phase = "plan", path = "") {
  return Object.freeze({ ok: false, diagnostics: Object.freeze([diagnostic(phase, code, path)]) });
}

function report(diagnostics) {
  const frozen = Object.freeze(diagnostics);
  return Object.freeze({ reportVersion: 1, valid: frozen.length === 0, diagnostics: frozen });
}

function captureRecord(value, required) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let descriptors;
  let prototype;
  try {
    descriptors = Object.getOwnPropertyDescriptors(value);
    prototype = Object.getPrototypeOf(value);
  } catch { throw new PrototypeEnvironmentPipelineOperationalError(); }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const keys = Reflect.ownKeys(descriptors);
  if (keys.length !== required.length || keys.some((key) => typeof key !== "string" || !required.includes(key))) return null;
  const output = Object.create(null);
  for (const key of required) {
    const descriptor = descriptors[key];
    if (!descriptor || !descriptor.enumerable || descriptor.get !== undefined || descriptor.set !== undefined || !Object.hasOwn(descriptor, "value")) return null;
    output[key] = descriptor.value;
  }
  return output;
}

function copyFiles(value) {
  if (!(value instanceof Map)) return null;
  const output = new Map();
  try {
    for (const [key, bytes] of Map.prototype.entries.call(value)) {
      if (typeof key !== "string" || !(bytes instanceof Uint8Array) || output.has(key)) return null;
      output.set(key, Uint8Array.prototype.slice.call(bytes));
    }
  } catch { return null; }
  return output;
}

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function wellFormedText(value) {
  if (typeof value !== "string") return false;
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

function allStringsWellFormed(value) {
  const pending = [value];
  while (pending.length > 0) {
    const current = pending.pop();
    if (typeof current === "string") {
      if (!wellFormedText(current)) return false;
    } else if (current && typeof current === "object") {
      for (const [key, child] of Object.entries(current)) {
        if (!wellFormedText(key)) return false;
        pending.push(child);
      }
    }
  }
  return true;
}

function parseCanonical(text, maximum) {
  if (typeof text !== "string" || new TextEncoder().encode(text).byteLength > maximum) return null;
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? value : null;
  } catch { return null; }
}

function schemaPath(errors, root) {
  const first = [...(errors ?? [])].sort((left, right) => left.instancePath < right.instancePath ? -1 : left.instancePath > right.instancePath ? 1 : 0)[0];
  return first?.instancePath ? `${root}${first.instancePath}` : root;
}

function blueprintSemantics(blueprint) {
  const ids = (values) => new Set(values.map((value) => value.id));
  const unique = (values) => ids(values).size === values.length;
  if (!unique(blueprint.zones) || !unique(blueprint.assetBriefs) || !unique(blueprint.placements)) return false;
  const environments = blueprint.assetBriefs.filter((brief) => brief.kind === "environment");
  if (environments.length !== 1 || !environments[0].roles.includes("visual") || !environments[0].roles.includes("collider")) return false;
  return true;
}

function environmentProfile(options) {
  if (options === undefined) return REUSABLE_ENVIRONMENT_PROFILE;
  const captured = captureRecord(options, ["profile"]);
  return captured?.profile === MULTI_SPACE_PROFILE
    ? MULTI_SPACE_ENVIRONMENT_PROFILE
    : null;
}

function environmentPrompt(blueprint, profile) {
  const externalBriefs = blueprint.assetBriefs.filter((brief) => brief.kind !== "environment");
  const characterCount = externalBriefs.filter((brief) => brief.kind === "character-placeholder").length;
  const propCount = externalBriefs.filter((brief) => brief.kind === "prop").length;
  return [
    `Scene intent: ${blueprint.scene.environmentPrompt}`,
    `Visual style intent: ${blueprint.scene.visualStylePrompt}`,
    ...(profile === MULTI_SPACE_ENVIRONMENT_PROFILE
      ? [`Layout capacity: preserve ${blueprint.zones.length} logical spaces and reserve clear placement capacity for ${characterCount} static characters and ${propCount} props.`]
      : []),
    profile,
  ].join("\n\n");
}

export function planPrototypeEnvironment(sceneBlueprintJson, options) {
  try {
    const profile = environmentProfile(options);
    if (profile === null) return failure("PROTOTYPE_ENVIRONMENT_PROFILE_UNSUPPORTED", "plan", "/profile");
    const blueprint = parseCanonical(sceneBlueprintJson, 1024 * 1024);
    if (!blueprint) return failure("PROTOTYPE_ENVIRONMENT_BLUEPRINT_INVALID");
    if (!validateBlueprintStructure(blueprint)) return failure("PROTOTYPE_ENVIRONMENT_BLUEPRINT_SCHEMA_INVALID", "schema", schemaPath(validateBlueprintStructure.errors, "/sceneBlueprint"));
    if (!blueprintSemantics(blueprint)) return failure("PROTOTYPE_ENVIRONMENT_BLUEPRINT_SEMANTIC_INVALID", "semantic", "/sceneBlueprint");
    if (!allStringsWellFormed(blueprint)) return failure("PROTOTYPE_ENVIRONMENT_UNSUPPORTED_TEXT", "semantic", "/sceneBlueprint");
    const prompt = environmentPrompt(blueprint, profile);
    if (prompt.length > MARBLE_PROVIDER_LIMITS.promptCharacters) {
      return failure("PROTOTYPE_ENVIRONMENT_PROMPT_PROFILE_UNSUPPORTED", "plan", "/sceneBlueprint/scene/environmentPrompt");
    }
    const internal = deepFreeze({
      scene: { id: blueprint.scene.id, contentVersion: blueprint.scene.contentVersion, title: blueprint.scene.title },
      blueprint: { format: blueprint.format, formatVersion: blueprint.formatVersion, canonicalSha256: sha256(sceneBlueprintJson) },
      environmentPromptSha256: sha256(prompt),
      prompt,
    });
    const success = deepFreeze({ ok: true, plan: {
      scene: internal.scene,
      blueprint: internal.blueprint,
      environmentPrompt: internal.prompt,
      environmentPromptSha256: internal.environmentPromptSha256,
    } });
    preparedPlans.set(success, internal);
    return success;
  } catch (error) {
    if (error instanceof PrototypeEnvironmentPipelineOperationalError) throw error;
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
}

function validateApproval(value, blueprintSha256) {
  const approval = captureRecord(value, ["blueprintSha256", "model", "maxCreateRequests", "maxPollAttempts", "maxWorldGets", "maxDownloads", "creditLimit", "usdLimitCents"]);
  return approval && approval.blueprintSha256 === blueprintSha256 && approval.model === MARBLE_PROVIDER_MODEL &&
    approval.maxCreateRequests === 1 && approval.maxPollAttempts === 180 && approval.maxWorldGets === 1 && approval.maxDownloads === 2 &&
    approval.creditLimit === 1600 && approval.usdLimitCents === 150;
}

function sameMetrics(left, right) {
  return left.nodeCount === right.nodeCount && left.meshCount === right.meshCount && left.surfaceCount === right.surfaceCount && left.triangleCount === right.triangleCount;
}

export function validatePrototypeEnvironmentBundleJson(text, files) {
  try {
    const value = parseCanonical(text, PROTOTYPE_ENVIRONMENT_LIMITS.manifestBytes);
    if (!value) return report([diagnostic("integrity", "PROTOTYPE_ENVIRONMENT_JSON_NON_CANONICAL", "/environmentBundle")]);
    if (!validateBundleStructure(value)) return report([diagnostic("schema", "PROTOTYPE_ENVIRONMENT_SCHEMA_INVALID", schemaPath(validateBundleStructure.errors, "/environmentBundle"))]);
    if (!allStringsWellFormed(value)) return report([diagnostic("semantic", "PROTOTYPE_ENVIRONMENT_UNSUPPORTED_TEXT", "/environmentBundle")]);
    const capturedFiles = copyFiles(files);
    if (!capturedFiles || capturedFiles.size !== 2 || !capturedFiles.has(PANORAMA_PATH) || !capturedFiles.has(COLLIDER_PATH)) {
      return report([diagnostic("integrity", "PROTOTYPE_ENVIRONMENT_FILES_INVALID", "/environmentBundle/assets")]);
    }
    const panoramaBytes = capturedFiles.get(PANORAMA_PATH);
    const colliderBytes = capturedFiles.get(COLLIDER_PATH);
    const panorama = inspectPanoramaPng(panoramaBytes, PROTOTYPE_ENVIRONMENT_LIMITS);
    if (!panorama.ok) return report([diagnostic("integrity", panorama.code, "/environmentBundle/assets/panorama/path")]);
    const collider = inspectEnvironmentCollider(colliderBytes, PROTOTYPE_ENVIRONMENT_LIMITS);
    if (!collider.ok) return report([diagnostic("integrity", collider.code, "/environmentBundle/assets/collider/path")]);
    if (
      value.assets.panorama.width !== panorama.width || value.assets.panorama.height !== panorama.height ||
      value.assets.panorama.byteLength !== panoramaBytes.byteLength || value.assets.panorama.sha256 !== sha256(panoramaBytes) ||
      value.assets.collider.byteLength !== colliderBytes.byteLength || value.assets.collider.sha256 !== sha256(colliderBytes) ||
      !sameMetrics(value.assets.collider.metrics, collider.metrics)
    ) return report([diagnostic("integrity", "PROTOTYPE_ENVIRONMENT_FILE_IDENTITY_MISMATCH", "/environmentBundle/assets")]);
    return report([]);
  } catch (error) {
    if (error instanceof PrototypeEnvironmentPipelineOperationalError) throw error;
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
}

export function validatePrototypeSpatialSourceBundleJson(
  text,
  files,
  environmentBundleJson,
) {
  try {
    const value = parseCanonical(text, PROTOTYPE_ENVIRONMENT_LIMITS.manifestBytes);
    if (!value) return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_SOURCE_JSON_NON_CANONICAL", "/spatialSourceBundle")]);
    if (!validateSpatialSourceStructure(value)) {
      return report([diagnostic("schema", "PROTOTYPE_SPATIAL_SOURCE_SCHEMA_INVALID", schemaPath(validateSpatialSourceStructure.errors, "/spatialSourceBundle"))]);
    }
    if (!allStringsWellFormed(value)) return report([diagnostic("semantic", "PROTOTYPE_SPATIAL_SOURCE_UNSUPPORTED_TEXT", "/spatialSourceBundle")]);
    const environment = parseCanonical(environmentBundleJson, PROTOTYPE_ENVIRONMENT_LIMITS.manifestBytes);
    if (!environment || !validateBundleStructure(environment)) {
      return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_SOURCE_ENVIRONMENT_INVALID", "/environmentBundle")]);
    }
    const capturedFiles = copyFiles(files);
    if (!capturedFiles || capturedFiles.size !== 2 || !capturedFiles.has(SPZ_PATH) || !capturedFiles.has(COLLIDER_PATH)) {
      return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_SOURCE_FILES_INVALID", "/spatialSourceBundle/source")]);
    }
    const spzBytes = capturedFiles.get(SPZ_PATH);
    const colliderBytes = capturedFiles.get(COLLIDER_PATH);
    if (spzBytes.byteLength < 1 || spzBytes.byteLength > PROTOTYPE_ENVIRONMENT_LIMITS.spzBytes ||
        colliderBytes.byteLength < 1 || colliderBytes.byteLength > PROTOTYPE_ENVIRONMENT_LIMITS.colliderBytes ||
        value.environment.bundleSha256 !== sha256(environmentBundleJson) ||
        value.environment.collider.path !== environment.assets.collider.path ||
        value.environment.collider.byteLength !== environment.assets.collider.byteLength ||
        value.environment.collider.sha256 !== environment.assets.collider.sha256 ||
        value.environment.collider.byteLength !== colliderBytes.byteLength ||
        value.environment.collider.sha256 !== sha256(colliderBytes) ||
        value.source.byteLength !== spzBytes.byteLength || value.source.sha256 !== sha256(spzBytes)) {
      return report([diagnostic("integrity", "PROTOTYPE_SPATIAL_SOURCE_IDENTITY_MISMATCH", "/spatialSourceBundle")]);
    }
    return report([]);
  } catch (error) {
    if (error instanceof PrototypeEnvironmentPipelineOperationalError) throw error;
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
}

function buildEnvironmentMaterialization(internal, acquired, reportCounts = acquired.counts) {
  const panoramaBytes = Uint8Array.prototype.slice.call(acquired.panoramaBytes);
  const colliderBytes = Uint8Array.prototype.slice.call(acquired.colliderBytes);
  const panorama = inspectPanoramaPng(panoramaBytes, PROTOTYPE_ENVIRONMENT_LIMITS);
  if (!panorama.ok) return failure(panorama.code, "integrity", "/environmentBundle/assets/panorama/path");
  const collider = inspectEnvironmentCollider(colliderBytes, PROTOTYPE_ENVIRONMENT_LIMITS);
  if (!collider.ok) return failure(collider.code, "integrity", "/environmentBundle/assets/collider/path");
  const bundle = deepFreeze({
    format: PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT,
    formatVersion: PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT_VERSION,
    canonicalization: CANONICAL_JSON_PROFILE,
    scene: internal.scene,
    blueprint: internal.blueprint,
    provider: { id: "world-labs-marble", model: MARBLE_PROVIDER_MODEL, environmentPromptSha256: internal.environmentPromptSha256 },
    assets: {
      panorama: { path: PANORAMA_PATH, format: "png", width: panorama.width, height: panorama.height, byteLength: panoramaBytes.byteLength, sha256: sha256(panoramaBytes) },
      collider: { path: COLLIDER_PATH, format: "glb", byteLength: colliderBytes.byteLength, sha256: sha256(colliderBytes), metrics: collider.metrics },
    },
  });
  const canonicalBundleJson = canonicalizeJsonValue(bundle);
  const files = Object.freeze([
    Object.freeze({ path: PANORAMA_PATH, bytes: panoramaBytes }),
    Object.freeze({ path: COLLIDER_PATH, bytes: colliderBytes }),
  ]);
  const validation = validatePrototypeEnvironmentBundleJson(canonicalBundleJson, new Map(files.map((file) => [file.path, file.bytes])));
  if (!validation.valid) throw new PrototypeEnvironmentPipelineOperationalError();
  const canonicalReportJson = canonicalizeJsonValue(deepFreeze({
    format: "matrix-oasis.prototype-environment-materialization-report",
    formatVersion: "0.1.0",
    provider: { id: "world-labs-marble", model: MARBLE_PROVIDER_MODEL },
    bundleSha256: sha256(canonicalBundleJson),
    counts: reportCounts,
    files: files.map((file) => ({ path: file.path, byteLength: file.bytes.byteLength, sha256: sha256(file.bytes) })),
  }));
  return Object.freeze({ ok: true, bundle, canonicalBundleJson, canonicalReportJson, files });
}

function validateSpatialApproval(value, blueprintSha256) {
  const approval = captureRecord(value, ["blueprintSha256", "model", "maxCreateRequests", "maxPollAttempts", "maxWorldGets", "maxDownloads", "creditLimit", "usdLimitCents"]);
  return approval && approval.blueprintSha256 === blueprintSha256 && approval.model === MARBLE_PROVIDER_MODEL &&
    approval.maxCreateRequests === 1 && approval.maxPollAttempts === 180 && approval.maxWorldGets === 1 && approval.maxDownloads === 3 &&
    approval.creditLimit === 1600 && approval.usdLimitCents === 150;
}

function quantizedScale(metricScaleFactor, groundPlaneOffset) {
  const metricScaleMicros = Math.round(metricScaleFactor * 1_000_000);
  const groundPlaneOffsetMm = Math.round(groundPlaneOffset * 1_000);
  if (!Number.isSafeInteger(metricScaleMicros) || metricScaleMicros < 1 || metricScaleMicros > 100_000_000 ||
      !Number.isSafeInteger(groundPlaneOffsetMm) || groundPlaneOffsetMm < -1_000_000 || groundPlaneOffsetMm > 1_000_000) return null;
  return { metricScaleMicros, groundPlaneOffsetMm: Object.is(groundPlaneOffsetMm, -0) ? 0 : groundPlaneOffsetMm };
}

function buildSpatialMaterialization(internal, acquired) {
  const scale = quantizedScale(acquired.metricScaleFactor, acquired.groundPlaneOffset);
  const spzBytes = acquired.spzBytes instanceof Uint8Array
    ? Uint8Array.prototype.slice.call(acquired.spzBytes)
    : null;
  if (!scale || !spzBytes || spzBytes.byteLength < 1 || spzBytes.byteLength > PROTOTYPE_ENVIRONMENT_LIMITS.spzBytes) {
    return failure("PROTOTYPE_SPATIAL_SOURCE_METADATA_INVALID", "integrity", "/spatialSourceBundle/scale");
  }
  const environment = buildEnvironmentMaterialization(internal, acquired, deepFreeze({
    creates: acquired.counts.creates,
    polls: acquired.counts.polls,
    worldGets: acquired.counts.worldGets,
    downloads: 2,
  }));
  if (!environment.ok) return environment;
  const colliderBytes = environment.files.find((file) => file.path === COLLIDER_PATH)?.bytes;
  if (!(colliderBytes instanceof Uint8Array)) throw new PrototypeEnvironmentPipelineOperationalError();
  const bundle = deepFreeze({
    format: PROTOTYPE_SPATIAL_SOURCE_BUNDLE_FORMAT,
    formatVersion: PROTOTYPE_SPATIAL_SOURCE_BUNDLE_FORMAT_VERSION,
    canonicalization: CANONICAL_JSON_PROFILE,
    scene: internal.scene,
    blueprint: internal.blueprint,
    environment: {
      bundleSha256: sha256(environment.canonicalBundleJson),
      collider: { path: COLLIDER_PATH, byteLength: colliderBytes.byteLength, sha256: sha256(colliderBytes) },
    },
    source: { path: SPZ_PATH, format: "spz", resolution: "full_res", byteLength: spzBytes.byteLength, sha256: sha256(spzBytes) },
    scale,
  });
  const canonicalBundleJson = canonicalizeJsonValue(bundle);
  const files = Object.freeze([
    Object.freeze({ path: SPZ_PATH, bytes: spzBytes }),
    Object.freeze({ path: COLLIDER_PATH, bytes: Uint8Array.prototype.slice.call(colliderBytes) }),
  ]);
  const validation = validatePrototypeSpatialSourceBundleJson(
    canonicalBundleJson,
    new Map(files.map((file) => [file.path, file.bytes])),
    environment.canonicalBundleJson,
  );
  if (!validation.valid) throw new PrototypeEnvironmentPipelineOperationalError();
  const canonicalReportJson = canonicalizeJsonValue(deepFreeze({
    format: "matrix-oasis.prototype-spatial-source-materialization-report",
    formatVersion: "0.1.0",
    bundleSha256: sha256(canonicalBundleJson),
    counts: acquired.counts,
    worldSource: acquired.worldSource,
    scale,
    files: files.map((file) => ({ path: file.path, byteLength: file.bytes.byteLength, sha256: sha256(file.bytes) })),
  }));
  return deepFreeze({
    ok: true,
    environment: {
      bundle: environment.bundle,
      canonicalBundleJson: environment.canonicalBundleJson,
      canonicalReportJson: environment.canonicalReportJson,
      files: environment.files,
    },
    spatialSource: { bundle, canonicalBundleJson, canonicalReportJson, files },
  });
}

export async function materializePrototypeEnvironment(request, provider) {
  try {
    const captured = captureRecord(request, ["plan", "approval"]);
    if (!captured || !preparedPlans.has(captured.plan)) return failure("PROTOTYPE_ENVIRONMENT_MATERIALIZATION_REQUEST_INVALID");
    const internal = preparedPlans.get(captured.plan);
    if (!validateApproval(captured.approval, internal.blueprint.canonicalSha256)) return failure("PROTOTYPE_ENVIRONMENT_APPROVAL_INVALID");
    const acquired = await acquireMarbleEnvironment(provider, internal.prompt);
    if (!acquired.ok) return acquired;
    if (acquired.counts.creates !== 1 || acquired.counts.polls > 180 || acquired.counts.worldGets !== 1 || acquired.counts.downloads !== 2) throw new PrototypeEnvironmentPipelineOperationalError();
    return buildEnvironmentMaterialization(internal, acquired);
  } catch (error) {
    if (error instanceof PrototypeEnvironmentPipelineOperationalError) throw error;
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
}

export async function materializePrototypeEnvironmentWithSpatialSource(request, provider) {
  try {
    const captured = captureRecord(request, ["plan", "approval"]);
    if (!captured || !preparedPlans.has(captured.plan)) return failure("PROTOTYPE_ENVIRONMENT_MATERIALIZATION_REQUEST_INVALID");
    const internal = preparedPlans.get(captured.plan);
    if (!validateSpatialApproval(captured.approval, internal.blueprint.canonicalSha256)) return failure("PROTOTYPE_ENVIRONMENT_APPROVAL_INVALID");
    const acquired = await acquireMarbleEnvironmentWithSpatialSource(provider, internal.prompt);
    if (!acquired.ok) return acquired;
    if (acquired.counts.creates !== 1 || acquired.counts.polls > 180 || acquired.counts.worldGets !== 1 || acquired.counts.downloads !== 3) throw new PrototypeEnvironmentPipelineOperationalError();
    return buildSpatialMaterialization(internal, acquired);
  } catch (error) {
    if (error instanceof PrototypeEnvironmentPipelineOperationalError) throw error;
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
}

export function materializeRecoveredPrototypeEnvironmentWithSpatialSource(request) {
  try {
    const captured = captureRecord(request, ["plan", "recovered"]);
    if (!captured || !preparedPlans.has(captured.plan)) return failure("PROTOTYPE_ENVIRONMENT_MATERIALIZATION_REQUEST_INVALID");
    const internal = preparedPlans.get(captured.plan);
    const recovered = captureRecord(captured.recovered, [
      "panoramaBytes", "colliderBytes", "spzBytes", "metricScaleFactor", "groundPlaneOffset", "worldSource", "worldPromptSha256", "counts",
    ]);
    const counts = recovered && captureRecord(recovered.counts, ["creates", "polls", "worldGets", "downloads"]);
    if (!recovered || !counts || !(recovered.panoramaBytes instanceof Uint8Array) ||
        !(recovered.colliderBytes instanceof Uint8Array) || !(recovered.spzBytes instanceof Uint8Array) ||
        !Number.isFinite(recovered.metricScaleFactor) || !Number.isFinite(recovered.groundPlaneOffset) ||
        recovered.worldPromptSha256 !== internal.environmentPromptSha256 ||
        recovered.worldSource !== "get-world-recovery" || counts.creates !== 0 || counts.polls !== 0 ||
        counts.worldGets !== 1 || counts.downloads !== 3) {
      return failure("PROTOTYPE_ENVIRONMENT_MATERIALIZATION_REQUEST_INVALID");
    }
    return buildSpatialMaterialization(internal, {
      panoramaBytes: Uint8Array.prototype.slice.call(recovered.panoramaBytes),
      colliderBytes: Uint8Array.prototype.slice.call(recovered.colliderBytes),
      spzBytes: Uint8Array.prototype.slice.call(recovered.spzBytes),
      metricScaleFactor: recovered.metricScaleFactor,
      groundPlaneOffset: recovered.groundPlaneOffset,
      worldSource: recovered.worldSource,
      counts: deepFreeze({ creates: 0, polls: 0, worldGets: 1, downloads: 3 }),
    });
  } catch (error) {
    if (error instanceof PrototypeEnvironmentPipelineOperationalError) throw error;
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
}
