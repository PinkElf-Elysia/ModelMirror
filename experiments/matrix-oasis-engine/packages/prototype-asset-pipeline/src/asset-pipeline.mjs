import { createHash } from "node:crypto";
import {
  PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE,
  PROTOTYPE_ASSET_NORMALIZATION_PROFILE,
  validatePrototypeAssetBundleJson,
} from "@matrix-oasis/prototype-asset-contracts";
import {
  validateGenerationProposalJson,
} from "@matrix-oasis/prototype-generation-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";
import { normalizePrototypeGlb } from "./glb-normalizer.mjs";
import { PrototypeAssetPipelineOperationalError } from "./operational.mjs";

const STATIC = Object.freeze({ phase: "pipeline", severity: "error", path: "" });
const KENNEY_TEXTURE_URI = "Textures/colormap.png";
const preparedAssetPlans = new WeakMap();

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value) || value instanceof Uint8Array) {
    return value;
  }
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function failure(code) {
  return deepFreeze({
    ok: false,
    diagnostics: [{ ...STATIC, code, message: code }],
  });
}

function captureRecord(value, required, optional = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let descriptors;
  let prototype;
  try {
    descriptors = Object.getOwnPropertyDescriptors(value);
    prototype = Object.getPrototypeOf(value);
  } catch {
    throw new PrototypeAssetPipelineOperationalError();
  }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const allowed = new Set([...required, ...optional]);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.some((key) => typeof key !== "string" || !allowed.has(key)) ||
    required.some((key) => !Object.hasOwn(descriptors, key))
  ) return null;
  const output = Object.create(null);
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (
      !descriptor.enumerable ||
      descriptor.get !== undefined ||
      descriptor.set !== undefined ||
      !Object.hasOwn(descriptor, "value")
    ) return null;
    output[key] = descriptor.value;
  }
  return output;
}

function sha256Hex(value) {
  return createHash("sha256").update(value).digest("hex");
}

function sha256(value) {
  return `sha256:${sha256Hex(value)}`;
}

function parseCanonicalJson(text) {
  if (typeof text !== "string") return null;
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? value : null;
  } catch {
    return null;
  }
}

function exactValidReport(report) {
  const captured = captureRecord(report, ["reportVersion", "valid", "diagnostics"]);
  return captured !== null &&
    captured.reportVersion === 1 &&
    captured.valid === true &&
    Array.isArray(captured.diagnostics) &&
    captured.diagnostics.length === 0;
}

export async function planPrototypeAssets(request) {
  try {
    const captured = captureRecord(request, [
      "authoringGamePackJson",
      "sceneBlueprintJson",
      "runtimeGamePackJson",
      "runtimeReceiptJson",
    ]);
    if (!captured) return failure("PROTOTYPE_ASSET_PLAN_REQUEST_INVALID");
    const authoring = parseCanonicalJson(captured.authoringGamePackJson);
    const blueprint = parseCanonicalJson(captured.sceneBlueprintJson);
    const runtime = parseCanonicalJson(captured.runtimeGamePackJson);
    const receipt = parseCanonicalJson(captured.runtimeReceiptJson);
    if (!authoring || !blueprint || !runtime || !receipt) {
      return failure("PROTOTYPE_ASSET_PLAN_INPUT_INVALID");
    }
    const proposalText = canonicalizeJsonValue({
      format: "matrix-oasis.prototype-generation-proposal",
      formatVersion: "0.1.0",
      authoringGamePack: authoring,
      sceneBlueprint: blueprint,
    });
    const proposalReport = validateGenerationProposalJson(proposalText);
    if (!exactValidReport(proposalReport)) {
      return failure("PROTOTYPE_ASSET_PLAN_BLUEPRINT_INVALID");
    }
    const runtimeReport = await validateRuntimeGamePackJson(
      captured.runtimeGamePackJson,
      captured.runtimeReceiptJson,
    );
    if (!exactValidReport(runtimeReport)) {
      return failure("PROTOTYPE_ASSET_PLAN_RUNTIME_INVALID");
    }
    if (
      blueprint.scene.id !== runtime.source.id ||
      blueprint.scene.contentVersion !== runtime.source.contentVersion ||
      authoring.id !== runtime.source.id ||
      authoring.contentVersion !== runtime.source.contentVersion ||
      runtime.source.canonicalSha256 !== sha256Hex(captured.authoringGamePackJson) ||
      receipt.artifact.sha256 !== sha256Hex(captured.runtimeGamePackJson)
    ) {
      return failure("PROTOTYPE_ASSET_PLAN_IDENTITY_MISMATCH");
    }
    const briefs = blueprint.assetBriefs.map((brief) => deepFreeze({
      id: brief.id,
      kind: brief.kind,
      prompt: brief.prompt,
      entityId: brief.entityId,
      roles: [...brief.roles],
    }));
    const plan = deepFreeze({
      scene: {
        id: blueprint.scene.id,
        contentVersion: blueprint.scene.contentVersion,
        title: blueprint.scene.title,
      },
      blueprint: {
        format: blueprint.format,
        formatVersion: blueprint.formatVersion,
        canonicalSha256: sha256(captured.sceneBlueprintJson),
        assetBriefs: briefs,
      },
      runtimeIdentity: {
        format: runtime.format,
        formatVersion: runtime.formatVersion,
        id: runtime.source.id,
        contentVersion: runtime.source.contentVersion,
        authoringCanonicalSha256: `sha256:${runtime.source.canonicalSha256}`,
        artifactSha256: `sha256:${receipt.artifact.sha256}`,
      },
    });
    const success = deepFreeze({ ok: true, plan });
    preparedAssetPlans.set(success, plan);
    return success;
  } catch (error) {
    if (error instanceof PrototypeAssetPipelineOperationalError) throw error;
    throw new PrototypeAssetPipelineOperationalError();
  }
}

function captureBytesMap(value) {
  if (!(value instanceof Map)) return null;
  const output = new Map();
  for (const [key, bytes] of Map.prototype.entries.call(value)) {
    if (typeof key !== "string" || !(bytes instanceof Uint8Array) || output.has(key)) return null;
    let captured;
    try {
      captured = Uint8Array.prototype.slice.call(bytes);
    } catch {
      return null;
    }
    output.set(key, captured);
  }
  return output;
}

function canonicalRoles(roles) {
  return ["visual", "collider"].filter((role) => roles.includes(role));
}

async function normalizedFile({ id, kind, role, bytes, externalResources, profile }) {
  const normalized = await normalizePrototypeGlb(bytes, { kind, role, externalResources });
  if (!normalized.ok) return normalized;
  const path = `assets/${id}.glb`;
  return {
    ok: true,
    file: deepFreeze({
      id,
      path,
      format: "glb",
      roles: [role],
      normalizationProfile: profile,
      byteLength: normalized.bytes.byteLength,
      sha256: sha256(normalized.bytes),
      metrics: normalized.metrics,
    }),
    output: Object.freeze({ path, bytes: normalized.bytes }),
  };
}

export async function materializePrototypeAssetBundle(request) {
  try {
    const captured = captureRecord(request, [
      "plan",
      "acquiredAssets",
      "environmentAssets",
      "environmentTexture",
    ]);
    if (!captured || !preparedAssetPlans.has(captured.plan)) {
      return failure("PROTOTYPE_ASSET_MATERIALIZATION_REQUEST_INVALID");
    }
    const acquired = captureBytesMap(captured.acquiredAssets);
    const environment = captureBytesMap(captured.environmentAssets);
    if (!acquired || !environment || !(captured.environmentTexture instanceof Uint8Array)) {
      return failure("PROTOTYPE_ASSET_MATERIALIZATION_REQUEST_INVALID");
    }
    let environmentTexture;
    try {
      environmentTexture = Uint8Array.prototype.slice.call(captured.environmentTexture);
    } catch {
      return failure("PROTOTYPE_ASSET_MATERIALIZATION_REQUEST_INVALID");
    }
    const plan = preparedAssetPlans.get(captured.plan);
    const expectedAcquired = new Set(
      plan.blueprint.assetBriefs
        .filter((brief) => brief.kind !== "environment")
        .map((brief) => brief.id),
    );
    if (
      acquired.size !== expectedAcquired.size ||
      [...acquired.keys()].some((key) => !expectedAcquired.has(key)) ||
      environment.size !== 2 ||
      !environment.has("floor-square") ||
      !environment.has("wall")
    ) {
      return failure("PROTOTYPE_ASSET_MATERIALIZATION_INPUT_MISMATCH");
    }
    const outputs = [];
    const materializations = [];
    for (const brief of plan.blueprint.assetBriefs) {
      if (brief.kind === "environment") {
        const assets = [];
        const externalResources = new Map([[KENNEY_TEXTURE_URI, environmentTexture]]);
        for (const sourceId of ["floor-square", "wall"]) {
          const normalized = await normalizePrototypeGlb(environment.get(sourceId), {
            kind: "environment",
            role: "visual",
            externalResources,
          });
          if (!normalized.ok || normalized.metrics.triangleCount > 10_000) {
            return failure(normalized.code ?? "PROTOTYPE_ASSET_NORMALIZATION_FAILED");
          }
          const id = `${brief.id}-${sourceId}`;
          const path = `assets/${id}.glb`;
          assets.push(deepFreeze({
            id,
            path,
            format: "glb",
            roles: ["visual", "collider"],
            normalizationProfile: PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE,
            byteLength: normalized.bytes.byteLength,
            sha256: sha256(normalized.bytes),
            metrics: normalized.metrics,
          }));
          outputs.push(Object.freeze({ path, bytes: normalized.bytes }));
        }
        materializations.push(deepFreeze({
          assetBriefId: brief.id,
          source: { type: "builtin-template", template: PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE },
          assets,
        }));
        continue;
      }
      const assets = [];
      for (const role of canonicalRoles(brief.roles)) {
        const id = `${brief.id}-${role}`;
        const normalized = await normalizedFile({
          id,
          kind: brief.kind,
          role,
          bytes: acquired.get(brief.id),
          externalResources: new Map(),
          profile: PROTOTYPE_ASSET_NORMALIZATION_PROFILE,
        });
        if (!normalized.ok) return failure(normalized.code);
        assets.push(normalized.file);
        outputs.push(normalized.output);
      }
      materializations.push(deepFreeze({
        assetBriefId: brief.id,
        source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" },
        assets,
      }));
    }
    if (outputs.length > 16) return failure("PROTOTYPE_ASSET_FILE_LIMIT");
    const bundle = deepFreeze({
      format: "matrix-oasis.prototype-asset-bundle",
      formatVersion: "0.1.0",
      canonicalization: "matrix-oasis.canonical-json/1",
      scene: plan.scene,
      blueprint: {
        ...plan.blueprint,
        assetBriefs: plan.blueprint.assetBriefs.map(({ prompt: _prompt, ...brief }) => brief),
      },
      runtimeIdentity: plan.runtimeIdentity,
      environmentTemplate: PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE,
      materializations,
    });
    const canonicalBundleJson = canonicalizeJsonValue(bundle);
    const validationReport = validatePrototypeAssetBundleJson(canonicalBundleJson);
    if (!exactValidReport(validationReport)) {
      throw new PrototypeAssetPipelineOperationalError();
    }
    const totalBytes = outputs.reduce((sum, output) => sum + output.bytes.byteLength, 0);
    if (totalBytes > 128 * 1024 * 1024) return failure("PROTOTYPE_ASSET_TOTAL_SIZE_LIMIT");
    const report = deepFreeze({
      format: "matrix-oasis.prototype-asset-materialization-report",
      formatVersion: "0.1.0",
      bundleSha256: sha256(canonicalBundleJson),
      fileCount: outputs.length,
      totalBytes,
      files: outputs.map((output) => ({ path: output.path, sha256: sha256(output.bytes) })),
    });
    return Object.freeze({
      ok: true,
      bundle,
      canonicalBundleJson,
      canonicalReportJson: canonicalizeJsonValue(report),
      files: Object.freeze(outputs),
    });
  } catch (error) {
    if (error instanceof PrototypeAssetPipelineOperationalError) throw error;
    throw new PrototypeAssetPipelineOperationalError();
  }
}
