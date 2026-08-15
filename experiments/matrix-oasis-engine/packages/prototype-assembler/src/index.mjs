import { createHash } from "node:crypto";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import {
  planPrototypeEnvironment,
  validatePrototypeEnvironmentBundleJson,
} from "@matrix-oasis/prototype-environment-pipeline";
import {
  GENERATION_PROPOSAL_FORMAT,
  GENERATION_PROPOSAL_FORMAT_VERSION,
  prepareGenerationProposalJson,
} from "@matrix-oasis/prototype-generation-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import { validateGlbBuffer } from "../../../scripts/lib/scene-pack-bundle-core.mjs";

const REQUEST_KEYS = Object.freeze([
  "authoringGamePackJson", "sceneBlueprintJson", "runtimeGamePackJson", "runtimeReceiptJson",
  "assetBundleJson", "assetFiles", "environmentBundleJson", "environmentFiles",
]);
const HASH_PATTERN = /^sha256:([0-9a-f]{64})$/u;
const SLOT_OFFSETS = Object.freeze([
  Object.freeze([-4500, -2500]), Object.freeze([-1500, -2500]),
  Object.freeze([1500, -2500]), Object.freeze([4500, -2500]),
  Object.freeze([-4500, 2500]), Object.freeze([-1500, 2500]),
  Object.freeze([1500, 2500]), Object.freeze([4500, 2500]),
]);
const ZONE_ORIGINS = Object.freeze([
  Object.freeze([-7500, -7500]), Object.freeze([7500, -7500]),
  Object.freeze([-7500, 7500]), Object.freeze([7500, 7500]),
]);

export const PROTOTYPE_ASSEMBLY_PROFILE = Object.freeze({
  id: "matrix-oasis.prototype-assembly/1", maxZones: 4, maxNonEnvironmentBriefs: 2,
  maxPlacements: 32, maxPlacementsPerZone: 8,
});
const PROTOTYPE_ASSEMBLY_PROFILE_V2 = Object.freeze({
  id: "matrix-oasis.prototype-assembly/2", maxZones: 4, maxNonEnvironmentBriefs: 6,
  maxPlacements: 32, maxPlacementsPerZone: 8,
});
const MULTI_SPACE_ENVIRONMENT_OPTIONS = Object.freeze({
  profile: "matrix-oasis.prototype-environment/2",
});

export class PrototypeAssemblerOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_ASSEMBLER_INTERNAL_ERROR");
    this.name = "PrototypeAssemblerOperationalError";
    this.code = "PROTOTYPE_ASSEMBLER_INTERNAL_ERROR";
  }
}

function deepFreeze(value, seen = new Set()) {
  if (value === null || typeof value !== "object" || seen.has(value)) return value;
  seen.add(value);
  for (const child of Object.values(value)) deepFreeze(child, seen);
  return Object.freeze(value);
}

function issue(code, path = "") {
  return Object.freeze({ phase: "assembly", severity: "error", code, path, message: code });
}

function reject(code, path = "") {
  return deepFreeze({ ok: false, diagnostics: [issue(code, path)] });
}

function sha256(bytes) { return `sha256:${createHash("sha256").update(bytes).digest("hex")}`; }
function sha256Text(text) { return sha256(new TextEncoder().encode(text)); }

function canonicalValue(text) {
  if (typeof text !== "string") return null;
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? value : null;
  } catch { return null; }
}

function isValidReport(report) {
  return report?.reportVersion === 1 && report.valid === true &&
    Array.isArray(report.diagnostics) && report.diagnostics.length === 0;
}

function captureRequest(request) {
  if (request === null || typeof request !== "object" || Object.getPrototypeOf(request) !== Object.prototype) return null;
  const descriptors = Object.getOwnPropertyDescriptors(request);
  const keys = Reflect.ownKeys(descriptors);
  if (keys.length !== REQUEST_KEYS.length || keys.some((key) =>
    typeof key !== "string" || !REQUEST_KEYS.includes(key) ||
    !descriptors[key].enumerable || !("value" in descriptors[key]))) return null;
  const output = Object.create(null);
  for (const key of REQUEST_KEYS) output[key] = descriptors[key].value;
  if (REQUEST_KEYS.slice(0, 5).some((key) => typeof output[key] !== "string") ||
      typeof output.environmentBundleJson !== "string") return null;
  return output;
}

function captureProfile(options) {
  if (options === undefined) return PROTOTYPE_ASSEMBLY_PROFILE;
  if (options === null || typeof options !== "object" || Object.getPrototypeOf(options) !== Object.prototype) return null;
  const descriptors = Object.getOwnPropertyDescriptors(options);
  if (Reflect.ownKeys(descriptors).length !== 1 || !descriptors.profile?.enumerable ||
      !("value" in descriptors.profile) || descriptors.profile.value !== PROTOTYPE_ASSEMBLY_PROFILE_V2.id) return null;
  return PROTOTYPE_ASSEMBLY_PROFILE_V2;
}

function captureFileMap(value) {
  if (value === null || typeof value !== "object" || Object.getPrototypeOf(value) !== Map.prototype) return null;
  const output = new Map();
  for (const [key, bytes] of Map.prototype.entries.call(value)) {
    if (typeof key !== "string" || !(bytes instanceof Uint8Array) || output.has(key)) return null;
    output.set(key, new Uint8Array(bytes));
  }
  return output;
}

function unprefixedHash(value) {
  const match = typeof value === "string" ? HASH_PATTERN.exec(value) : null;
  return match ? match[1] : null;
}

function validateAssetFiles(bundle, files) {
  const expected = bundle.materializations.flatMap((item) => item.assets);
  if (files.size !== expected.length) return false;
  const paths = new Set();
  for (const asset of expected) {
    if (paths.has(asset.path)) return false;
    paths.add(asset.path);
    const bytes = files.get(asset.path);
    if (!bytes || bytes.byteLength !== asset.byteLength || sha256(bytes) !== asset.sha256) return false;
    const glb = validateGlbBuffer(bytes);
    if (!glb.ok || glb.summary.nodes !== asset.metrics.nodeCount || glb.summary.meshes !== asset.metrics.meshCount ||
        glb.summary.surfaces !== asset.metrics.surfaceCount || glb.summary.triangles !== asset.metrics.triangleCount) return false;
  }
  return true;
}

function sameScene(left, right) {
  return left?.id === right?.id && left?.contentVersion === right?.contentVersion && left?.title === right?.title;
}

function runtimeIdentity(runtimePack, receipt) {
  return {
    runtimeFormat: runtimePack.format, runtimeFormatVersion: runtimePack.formatVersion,
    packId: runtimePack.source.id, packContentVersion: runtimePack.source.contentVersion,
    sourceCanonicalSha256: runtimePack.source.canonicalSha256, artifactSha256: receipt.artifact.sha256,
  };
}

function assetIdentityMatches(bundle, runtimePack, receipt, blueprintSha) {
  const identity = bundle.runtimeIdentity;
  return bundle.blueprint.canonicalSha256 === blueprintSha && identity.format === runtimePack.format &&
    identity.formatVersion === runtimePack.formatVersion && identity.id === runtimePack.source.id &&
    identity.contentVersion === runtimePack.source.contentVersion &&
    identity.authoringCanonicalSha256 === `sha256:${runtimePack.source.canonicalSha256}` &&
    identity.artifactSha256 === `sha256:${receipt.artifact.sha256}`;
}

function profileCheck(blueprint, profile) {
  if (blueprint.zones.length < 1 || blueprint.zones.length > profile.maxZones ||
      blueprint.placements.length > profile.maxPlacements) return false;
  const nonEnvironment = blueprint.assetBriefs.filter((brief) => brief.kind !== "environment");
  if (nonEnvironment.length > profile.maxNonEnvironmentBriefs ||
      (profile.id === PROTOTYPE_ASSEMBLY_PROFILE.id &&
       (nonEnvironment.filter((brief) => brief.kind === "prop").length > 1 ||
        nonEnvironment.filter((brief) => brief.kind === "character-placeholder").length > 1))) return false;
  const counts = new Map(blueprint.zones.map((zone) => [zone.id, 0]));
  for (const placement of blueprint.placements) {
    if (!counts.has(placement.zoneId)) return false;
    const next = counts.get(placement.zoneId) + 1;
    if (next > profile.maxPlacementsPerZone) return false;
    counts.set(placement.zoneId, next);
  }
  return true;
}

function chooseRoleAsset(materialization, role) {
  const matches = materialization.assets.filter((asset) => asset.roles.includes(role));
  return matches.length === 1 ? matches[0] : null;
}

function transform(positionMm) {
  return { positionMm, rotationMilliDegrees: [0, 0, 0], scalePermille: [1000, 1000, 1000] };
}

function buildScene({ blueprint, runtimePack, receipt, assetBundle, environmentBundle }) {
  const materializations = new Map(assetBundle.materializations.map((entry) => [entry.assetBriefId, entry]));
  const environmentBriefs = blueprint.assetBriefs.filter((brief) => brief.kind === "environment");
  if (materializations.size !== assetBundle.materializations.length || environmentBriefs.length !== 1) return null;
  const environmentMaterialization = materializations.get(environmentBriefs[0].id);
  if (!environmentMaterialization || environmentMaterialization.source?.type !== "builtin-template") return null;
  const colliderHash = unprefixedHash(environmentBundle.assets.collider.sha256);
  if (!colliderHash) return null;
  const sceneAssets = [{ id: "r10-environment-collider", roles: ["visual", "collider"],
    path: environmentBundle.assets.collider.path, format: "glb", byteLength: environmentBundle.assets.collider.byteLength,
    sha256: colliderHash }];
  const physicalByLogical = new Map();
  const placements = [{ id: "r10-environment", visualAssetId: "r10-environment-collider",
    colliderAssetId: "r10-environment-collider", entityId: null, transform: transform([0, 0, 0]) }];
  for (const logical of blueprint.placements.filter((entry) => entry.assetBriefId === environmentBriefs[0].id)) {
    physicalByLogical.set(logical.id, ["r10-environment"]);
  }
  const zoneIndex = new Map(blueprint.zones.map((zone, index) => [zone.id, index]));
  const zoneSlots = new Map(blueprint.zones.map((zone) => [zone.id, 0]));
  const assetIds = new Set(sceneAssets.map((asset) => asset.id));
  const assetPaths = new Set(sceneAssets.map((asset) => asset.path));
  let physicalPlacementIndex = 0;
  for (const logical of blueprint.placements) {
    const brief = blueprint.assetBriefs.find((entry) => entry.id === logical.assetBriefId);
    if (!brief || brief.kind === "environment") continue;
    const materialization = materializations.get(brief.id);
    if (!materialization || materialization.source?.type !== "meshy-text-to-3d") return null;
    const visual = chooseRoleAsset(materialization, "visual");
    const collider = chooseRoleAsset(materialization, "collider");
    if (!visual || (brief.roles.includes("collider") && !collider)) return null;
    for (const asset of materialization.assets) {
      if (assetIds.has(asset.id)) continue;
      const hash = unprefixedHash(asset.sha256);
      if (!hash || assetPaths.has(asset.path)) return null;
      assetIds.add(asset.id); assetPaths.add(asset.path);
      sceneAssets.push({ id: asset.id, roles: [...asset.roles], path: asset.path,
        format: "glb", byteLength: asset.byteLength, sha256: hash });
    }
    const slot = zoneSlots.get(logical.zoneId);
    const origin = ZONE_ORIGINS[zoneIndex.get(logical.zoneId)];
    const offset = SLOT_OFFSETS[slot];
    if (!origin || !offset) return null;
    zoneSlots.set(logical.zoneId, slot + 1);
    const id = `r10-placement-${physicalPlacementIndex}`;
    physicalPlacementIndex += 1;
    placements.push({ id, visualAssetId: visual.id, colliderAssetId: collider?.id ?? null,
      entityId: logical.entityId, transform: transform([origin[0] + offset[0], 0, origin[1] + offset[1]]) });
    physicalByLogical.set(logical.id, [id]);
  }
  const sourceBindings = new Map(blueprint.nodeBindings.map((binding) => [binding.nodeId, binding]));
  if (sourceBindings.size !== runtimePack.nodes.length) return null;
  const nodeBindings = [];
  for (const node of runtimePack.nodes) {
    const source = sourceBindings.get(node.id);
    const origin = source ? ZONE_ORIGINS[zoneIndex.get(source.zoneId)] : null;
    if (!source || !origin) return null;
    const visible = ["r10-environment"];
    for (const logicalId of source.visiblePlacementIds) {
      const mapped = physicalByLogical.get(logicalId);
      if (!mapped) return null;
      for (const id of mapped) if (!visible.includes(id)) visible.push(id);
    }
    nodeBindings.push({ nodeId: node.id,
      playerSpawn: { positionMm: [origin[0], 1000, origin[1] + 4000], yawMilliDegrees: 0 },
      actionAnchor: { positionMm: [origin[0], 0, origin[1]], yawMilliDegrees: 0 }, visiblePlacementIds: visible });
  }
  return { format: "matrix-oasis.scene-pack", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: blueprint.scene.id, contentVersion: blueprint.scene.contentVersion, title: blueprint.scene.title },
    runtimeIdentity: runtimeIdentity(runtimePack, receipt), assets: sceneAssets, placements, nodeBindings };
}

function referencedFiles(scene, assetBundle, environmentBundle) {
  const assetPaths = new Set(assetBundle.materializations.flatMap((item) =>
    item.source.type === "meshy-text-to-3d" ? item.assets.map((asset) => asset.path) : []));
  const output = [
    { source: "prototype-environment", path: environmentBundle.assets.panorama.path },
    { source: "prototype-environment", path: environmentBundle.assets.collider.path },
  ];
  for (const asset of scene.assets) {
    if (asset.id === "r10-environment-collider") continue;
    if (!assetPaths.has(asset.path)) return null;
    output.push({ source: "prototype-assets", path: asset.path });
  }
  return output;
}

function assemblyReport(values, profile) {
  return { reportVersion: 1, profile: profile.id,
    inputs: {
      authoringGamePackSha256: sha256Text(values.authoringText),
      sceneBlueprintSha256: sha256Text(values.blueprintText),
      runtimeGamePackSha256: sha256Text(values.runtimeText),
      runtimeReceiptSha256: sha256Text(values.receiptText),
      prototypeAssetBundleSha256: sha256Text(values.assetText),
      prototypeEnvironmentBundleSha256: sha256Text(values.environmentText),
    },
    environment: { panoramaSha256: values.environmentBundle.assets.panorama.sha256,
      colliderSha256: values.environmentBundle.assets.collider.sha256 },
    output: { scenePackSha256: sha256Text(values.sceneText), assets: values.scene.assets.length,
      placements: values.scene.placements.length, nodeBindings: values.scene.nodeBindings.length,
      referencedFiles: values.referenced.map(({ source, path }) => ({ source, path })) } };
}

async function assemble(request, profile) {
  const captured = captureRequest(request);
  if (!captured) return reject("PROTOTYPE_ASSEMBLY_INPUT_INVALID");
  const assetFiles = captureFileMap(captured.assetFiles);
  const environmentFiles = captureFileMap(captured.environmentFiles);
  if (!assetFiles || !environmentFiles) return reject("PROTOTYPE_ASSEMBLY_INPUT_INVALID");
  const authoring = canonicalValue(captured.authoringGamePackJson);
  const blueprint = canonicalValue(captured.sceneBlueprintJson);
  if (!authoring || !blueprint) return reject("PROTOTYPE_ASSEMBLY_GENERATION_INVALID");
  const proposal = prepareGenerationProposalJson(canonicalizeJsonValue({ format: GENERATION_PROPOSAL_FORMAT,
    formatVersion: GENERATION_PROPOSAL_FORMAT_VERSION, authoringGamePack: authoring, sceneBlueprint: blueprint }));
  if (!proposal?.ok) return reject("PROTOTYPE_ASSEMBLY_GENERATION_INVALID");
  if (!profileCheck(blueprint, profile)) return reject("PROTOTYPE_ASSEMBLY_PROFILE_UNSUPPORTED", "/sceneBlueprint");
  const runtimeReport = await validateRuntimeGamePackJson(captured.runtimeGamePackJson, captured.runtimeReceiptJson);
  if (!isValidReport(runtimeReport)) return reject("PROTOTYPE_ASSEMBLY_RUNTIME_INVALID");
  const runtimePack = JSON.parse(captured.runtimeGamePackJson);
  const receipt = JSON.parse(captured.runtimeReceiptJson);
  if (!isValidReport(validatePrototypeAssetBundleJson(captured.assetBundleJson))) return reject("PROTOTYPE_ASSEMBLY_ASSET_BUNDLE_INVALID");
  const assetBundle = JSON.parse(captured.assetBundleJson);
  if (!validateAssetFiles(assetBundle, assetFiles)) return reject("PROTOTYPE_ASSEMBLY_ASSET_FILES_INVALID");
  if (!isValidReport(validatePrototypeEnvironmentBundleJson(captured.environmentBundleJson, environmentFiles))) {
    return reject("PROTOTYPE_ASSEMBLY_ENVIRONMENT_BUNDLE_INVALID");
  }
  const environmentBundle = JSON.parse(captured.environmentBundleJson);
  const blueprintSha = sha256Text(captured.sceneBlueprintJson);
  const environmentPlan = planPrototypeEnvironment(
    captured.sceneBlueprintJson,
    profile.id === PROTOTYPE_ASSEMBLY_PROFILE_V2.id ? MULTI_SPACE_ENVIRONMENT_OPTIONS : undefined,
  );
  if (!sameScene(blueprint.scene, assetBundle.scene) || !sameScene(blueprint.scene, environmentBundle.scene) ||
      runtimePack.source.id !== authoring.id || runtimePack.source.contentVersion !== authoring.contentVersion ||
      runtimePack.source.canonicalSha256 !== sha256Text(captured.authoringGamePackJson).slice(7) ||
      !assetIdentityMatches(assetBundle, runtimePack, receipt, blueprintSha) ||
      environmentBundle.blueprint.canonicalSha256 !== blueprintSha ||
      !environmentPlan.ok ||
      environmentBundle.provider.environmentPromptSha256 !== environmentPlan.plan.environmentPromptSha256) {
    return reject("PROTOTYPE_ASSEMBLY_IDENTITY_MISMATCH");
  }
  const scene = buildScene({ blueprint, runtimePack, receipt, assetBundle, environmentBundle });
  if (!scene) return reject("PROTOTYPE_ASSEMBLY_REFERENCE_INVALID");
  const canonicalScenePackJson = canonicalizeJsonValue(scene);
  if (!isValidReport(await validateScenePackJson(canonicalScenePackJson, captured.runtimeGamePackJson, captured.runtimeReceiptJson))) {
    return reject("PROTOTYPE_ASSEMBLY_SCENE_PACK_INVALID");
  }
  const referenced = referencedFiles(scene, assetBundle, environmentBundle);
  if (!referenced) return reject("PROTOTYPE_ASSEMBLY_REFERENCE_INVALID");
  const canonicalAssemblyReportJson = canonicalizeJsonValue(assemblyReport({ authoringText: captured.authoringGamePackJson,
    blueprintText: captured.sceneBlueprintJson, runtimeText: captured.runtimeGamePackJson,
    receiptText: captured.runtimeReceiptJson, assetText: captured.assetBundleJson,
    environmentText: captured.environmentBundleJson, sceneText: canonicalScenePackJson,
    scene, environmentBundle, referenced }, profile));
  return deepFreeze({ ok: true, canonicalScenePackJson, canonicalAssemblyReportJson, referencedFiles: referenced });
}

export async function assemblePrototypeScene(request, options) {
  try {
    const profile = captureProfile(options);
    if (!profile) return reject("PROTOTYPE_ASSEMBLY_PROFILE_UNSUPPORTED", "/profile");
    return await assemble(request, profile);
  }
  catch (error) {
    if (error instanceof PrototypeAssemblerOperationalError) throw error;
    throw new PrototypeAssemblerOperationalError();
  }
}
