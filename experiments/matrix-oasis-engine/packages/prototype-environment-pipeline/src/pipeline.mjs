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
  MARBLE_PROVIDER_LIMITS,
  MARBLE_PROVIDER_MODEL,
} from "./marble-provider.mjs";
import { PrototypeEnvironmentPipelineOperationalError } from "./operational.mjs";

export const PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT =
  "matrix-oasis.prototype-environment-bundle";
export const PROTOTYPE_ENVIRONMENT_BUNDLE_FORMAT_VERSION = "0.1.0";
export const PROTOTYPE_ENVIRONMENT_LIMITS = Object.freeze({
  manifestBytes: 256 * 1024,
  panoramaBytes: 64 * 1024 * 1024,
  panoramaWidth: 16_384,
  panoramaHeight: 8_192,
  colliderBytes: 32 * 1024 * 1024,
});

const SHA256 = "^sha256:[0-9a-f]{64}$";
const PANORAMA_PATH = "assets/environment-panorama.png";
const COLLIDER_PATH = "assets/environment-collider.glb";
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

function environmentPrompt(blueprint) {
  return [
    `Scene intent: ${blueprint.scene.environmentPrompt}`,
    `Visual style intent: ${blueprint.scene.visualStylePrompt}`,
    REUSABLE_ENVIRONMENT_PROFILE,
  ].join("\n\n");
}

export function planPrototypeEnvironment(sceneBlueprintJson) {
  try {
    const blueprint = parseCanonical(sceneBlueprintJson, 1024 * 1024);
    if (!blueprint) return failure("PROTOTYPE_ENVIRONMENT_BLUEPRINT_INVALID");
    if (!validateBlueprintStructure(blueprint)) return failure("PROTOTYPE_ENVIRONMENT_BLUEPRINT_SCHEMA_INVALID", "schema", schemaPath(validateBlueprintStructure.errors, "/sceneBlueprint"));
    if (!blueprintSemantics(blueprint)) return failure("PROTOTYPE_ENVIRONMENT_BLUEPRINT_SEMANTIC_INVALID", "semantic", "/sceneBlueprint");
    if (!allStringsWellFormed(blueprint)) return failure("PROTOTYPE_ENVIRONMENT_UNSUPPORTED_TEXT", "semantic", "/sceneBlueprint");
    const prompt = environmentPrompt(blueprint);
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

export async function materializePrototypeEnvironment(request, provider) {
  try {
    const captured = captureRecord(request, ["plan", "approval"]);
    if (!captured || !preparedPlans.has(captured.plan)) return failure("PROTOTYPE_ENVIRONMENT_MATERIALIZATION_REQUEST_INVALID");
    const internal = preparedPlans.get(captured.plan);
    if (!validateApproval(captured.approval, internal.blueprint.canonicalSha256)) return failure("PROTOTYPE_ENVIRONMENT_APPROVAL_INVALID");
    const acquired = await acquireMarbleEnvironment(provider, internal.prompt);
    if (!acquired.ok) return acquired;
    if (acquired.counts.creates !== 1 || acquired.counts.polls > 180 || acquired.counts.worldGets !== 1 || acquired.counts.downloads !== 2) throw new PrototypeEnvironmentPipelineOperationalError();
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
      counts: acquired.counts,
      files: files.map((file) => ({ path: file.path, byteLength: file.bytes.byteLength, sha256: sha256(file.bytes) })),
    }));
    return Object.freeze({ ok: true, bundle, canonicalBundleJson, canonicalReportJson, files });
  } catch (error) {
    if (error instanceof PrototypeEnvironmentPipelineOperationalError) throw error;
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
}
