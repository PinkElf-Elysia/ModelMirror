import Ajv2020 from "ajv/dist/2020.js";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import { SCENE_BLUEPRINT_SCHEMA } from "@matrix-oasis/prototype-generation-contracts";
import { validatePrototypeSpatialIntentJson } from "@matrix-oasis/prototype-spatial-planning-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";

const INTERNAL_CODE = "PROTOTYPE_SPATIAL_SOLVER_INTERNAL_ERROR";
export const PROTOTYPE_SPATIAL_INTENT_SYNTHESIS_PROFILE = Object.freeze({
  id: "matrix-oasis.spatial-intent-synthesis/1",
  maxZones: 4,
  maxPlacements: 6,
  maxNodeContexts: 16,
  maxActionsPerNode: 64,
  largeFootprintThresholdMm: 1200,
});
export class PrototypeSpatialSolverOperationalError extends Error {
  constructor() { super(INTERNAL_CODE); this.name = "PrototypeSpatialSolverOperationalError"; this.code = INTERNAL_CODE; }
}
function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}
function diagnostic(code, path = "") { return deepFreeze({ phase: "synthesis", severity: "error", code, path, message: code }); }
function reject(code, path = "") { return deepFreeze({ ok: false, diagnostics: [diagnostic(code, path)] }); }
function captureRequest(value) {
  if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) return null;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const expected = ["assetBundleJson", "runtimeGamePackJson", "runtimeReceiptJson", "sceneBlueprintJson"];
  if (Reflect.ownKeys(descriptors).some((key) => typeof key !== "string") || Object.keys(descriptors).sort().join("\0") !== expected.join("\0")) return null;
  const output = {};
  for (const key of expected) {
    const descriptor = descriptors[key];
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor) || typeof descriptor.value !== "string") return null;
    output[key] = descriptor.value;
  }
  return output;
}
function canonicalValue(text) {
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? value : null;
  } catch { return null; }
}
function validReport(value) {
  if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Object.keys(descriptors).sort().join("\0") === "diagnostics\0reportVersion\0valid" &&
    descriptors.reportVersion?.value === 1 && descriptors.valid?.value === true &&
    Array.isArray(descriptors.diagnostics?.value) && descriptors.diagnostics.value.length === 0;
}
async function sha256Text(text) {
  const cryptoValue = globalThis.crypto;
  if (!cryptoValue?.subtle || typeof cryptoValue.subtle.digest !== "function") throw new PrototypeSpatialSolverOperationalError();
  const digest = await cryptoValue.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return `sha256:${Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("")}`;
}
const ajv = new Ajv2020({ strict: true, allErrors: true, coerceTypes: false, useDefaults: false, removeAdditional: false, ownProperties: true, validateFormats: false });
const validateBlueprint = ajv.compile(SCENE_BLUEPRINT_SCHEMA);

function assetDimensions(materialization) {
  let width = 0; let height = 0; let depth = 0; let found = false;
  for (const asset of materialization.assets) {
    if (!asset.roles.includes("visual") && !asset.roles.includes("collider")) continue;
    const bounds = asset.metrics.boundsMm;
    width = Math.max(width, bounds.max[0] - bounds.min[0]);
    height = Math.max(height, bounds.max[1] - bounds.min[1]);
    depth = Math.max(depth, bounds.max[2] - bounds.min[2]);
    found = true;
  }
  return found ? { width, height, depth } : null;
}
function sameIdentity(blueprint, runtime, receipt, assetBundle, blueprintSha) {
  const briefs = blueprint.assetBriefs.map(({ id, kind, entityId, roles }) => ({ id, kind, entityId, roles }));
  return blueprint.scene.id === runtime.source.id && blueprint.scene.contentVersion === runtime.source.contentVersion &&
    assetBundle.scene.id === blueprint.scene.id && assetBundle.scene.contentVersion === blueprint.scene.contentVersion &&
    assetBundle.blueprint.canonicalSha256 === blueprintSha &&
    canonicalizeJsonValue(assetBundle.blueprint.assetBriefs) === canonicalizeJsonValue(briefs) &&
    assetBundle.runtimeIdentity.id === runtime.source.id && assetBundle.runtimeIdentity.contentVersion === runtime.source.contentVersion &&
    assetBundle.runtimeIdentity.authoringCanonicalSha256 === `sha256:${runtime.source.canonicalSha256}` &&
    assetBundle.runtimeIdentity.artifactSha256 === `sha256:${receipt.artifact.sha256}`;
}
function blueprintReferencesValid(blueprint, runtime) {
  const unique = (items, field) => new Set(items.map((item) => item[field])).size === items.length;
  if (!unique(blueprint.zones, "id") || !unique(blueprint.assetBriefs, "id") || !unique(blueprint.placements, "id") || !unique(blueprint.nodeBindings, "nodeId")) return false;
  const zones = new Set(blueprint.zones.map((item) => item.id));
  const briefs = new Map(blueprint.assetBriefs.map((item) => [item.id, item]));
  const placements = new Set(blueprint.placements.map((item) => item.id));
  const entities = new Set(runtime.entities.map((item) => item.id));
  const nodes = new Set(runtime.nodes.map((item) => item.id));
  if (blueprint.nodeBindings.length !== runtime.nodes.length || blueprint.nodeBindings.some((item) => !nodes.has(item.nodeId) || !zones.has(item.zoneId) || item.visiblePlacementIds.some((id) => !placements.has(id)))) return false;
  return blueprint.placements.every((item) => {
    const brief = briefs.get(item.assetBriefId);
    return brief && zones.has(item.zoneId) && item.entityId === brief.entityId && (item.entityId === null || entities.has(item.entityId));
  }) && blueprint.assetBriefs.every((item) => item.entityId === null || entities.has(item.entityId));
}
function adjacency(blueprint, runtime) {
  const bindingByNode = new Map(blueprint.nodeBindings.map((item) => [item.nodeId, item]));
  const output = new Map(blueprint.zones.map((zone) => [zone.id, new Set()]));
  for (let index = 0; index < runtime.nodes.length; index += 1) {
    const source = bindingByNode.get(runtime.nodes[index].id);
    if (!source || !output.has(source.zoneId)) return null;
    for (const action of runtime.nodes[index].actions) {
      if (action.target.kind !== "node") continue;
      const targetNode = runtime.nodes[action.target.index]; const target = targetNode && bindingByNode.get(targetNode.id);
      if (!target || !output.has(target.zoneId)) return null;
      if (source.zoneId !== target.zoneId) { output.get(source.zoneId).add(target.zoneId); output.get(target.zoneId).add(source.zoneId); }
    }
  }
  return output;
}

async function synthesize(request) {
  const captured = captureRequest(request);
  if (!captured) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_INPUT_INVALID");
  const blueprint = canonicalValue(captured.sceneBlueprintJson);
  if (!blueprint || !validateBlueprint(blueprint)) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_BLUEPRINT_INVALID", "/sceneBlueprintJson");
  const runtimeReport = await validateRuntimeGamePackJson(captured.runtimeGamePackJson, captured.runtimeReceiptJson);
  if (!validReport(runtimeReport)) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_RUNTIME_INVALID", "/runtimeGamePackJson");
  if (!validReport(validatePrototypeAssetBundleJson(captured.assetBundleJson))) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_ASSET_BUNDLE_INVALID", "/assetBundleJson");
  const runtime = JSON.parse(captured.runtimeGamePackJson); const receipt = JSON.parse(captured.runtimeReceiptJson); const assetBundle = JSON.parse(captured.assetBundleJson);
  if (!blueprintReferencesValid(blueprint, runtime)) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_BLUEPRINT_REFERENCE_INVALID", "/sceneBlueprintJson");
  const blueprintSha = await sha256Text(captured.sceneBlueprintJson);
  if (!sameIdentity(blueprint, runtime, receipt, assetBundle, blueprintSha)) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_IDENTITY_MISMATCH");
  const nonEnvironmentBriefs = blueprint.assetBriefs.filter((item) => item.kind !== "environment");
  const nonEnvironmentIds = new Set(nonEnvironmentBriefs.map((item) => item.id));
  const placements = blueprint.placements.filter((item) => nonEnvironmentIds.has(item.assetBriefId));
  if (blueprint.zones.length > 4 || placements.length > 6 || runtime.nodes.length > 16 || runtime.nodes.some((node) => node.actions.length > 64)) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_PROFILE_UNSUPPORTED");
  if (new Set(placements.map((item) => item.assetBriefId)).size !== placements.length || placements.length !== nonEnvironmentBriefs.length) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_PLACEMENT_CARDINALITY_INVALID", "/placements");
  const materializations = new Map(assetBundle.materializations.map((item) => [item.assetBriefId, item]));
  const briefById = new Map(nonEnvironmentBriefs.map((item) => [item.id, item]));
  const intentPlacements = [];
  for (const placement of placements) {
    const brief = briefById.get(placement.assetBriefId); const materialization = materializations.get(placement.assetBriefId);
    const dimensions = materialization && assetDimensions(materialization);
    if (!brief || !dimensions) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_ASSET_BOUNDS_MISSING", "/assetBundleJson");
    const clearanceClass = brief.kind === "character-placeholder" ? "human" : Math.max(dimensions.width, dimensions.depth) > 1200 ? "large" : "compact";
    intentPlacements.push({ id: placement.id, assetBriefId: placement.assetBriefId, zoneId: placement.zoneId, support: "floor", anchor: "free", facing: { kind: "zone-center" }, near: [], separate: [], clearanceClass });
  }
  const graph = adjacency(blueprint, runtime);
  if (!graph) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_NODE_BINDING_INVALID", "/nodeBindings");
  const placementIds = new Set(intentPlacements.map((item) => item.id));
  const bindingByNode = new Map(blueprint.nodeBindings.map((item) => [item.nodeId, item]));
  const nodeContexts = runtime.nodes.map((node) => {
    const binding = bindingByNode.get(node.id);
    return binding && { nodeId: node.id, zoneId: binding.zoneId, visiblePlacementIds: binding.visiblePlacementIds.filter((id) => placementIds.has(id)), requiresPlayerSpawn: true, requiresActionTerminal: true };
  });
  if (nodeContexts.some((item) => !item)) return reject("PROTOTYPE_SPATIAL_SYNTHESIS_NODE_BINDING_INVALID", "/nodeBindings");
  const intent = {
    format: "matrix-oasis.prototype-spatial-intent", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: blueprint.scene.id, contentVersion: blueprint.scene.contentVersion },
    blueprint: { format: blueprint.format, formatVersion: blueprint.formatVersion, canonicalSha256: blueprintSha },
    runtime: { format: runtime.format, formatVersion: runtime.formatVersion, id: runtime.source.id, contentVersion: runtime.source.contentVersion, sourceSha256: `sha256:${runtime.source.canonicalSha256}`, artifactSha256: `sha256:${receipt.artifact.sha256}` },
    assetBundle: { format: assetBundle.format, formatVersion: assetBundle.formatVersion, canonicalSha256: await sha256Text(captured.assetBundleJson) },
    zones: blueprint.zones.map((zone) => ({ id: zone.id, adjacentZoneIds: blueprint.zones.filter((candidate) => graph.get(zone.id).has(candidate.id)).map((candidate) => candidate.id) })),
    placements: intentPlacements,
    nodeContexts,
  };
  const canonicalSpatialIntentJson = canonicalizeJsonValue(intent);
  if (!validReport(validatePrototypeSpatialIntentJson(canonicalSpatialIntentJson))) throw new PrototypeSpatialSolverOperationalError();
  return deepFreeze({ ok: true, spatialIntent: JSON.parse(canonicalSpatialIntentJson), canonicalSpatialIntentJson });
}

export async function synthesizePrototypeSpatialIntent(request) {
  try { return await synthesize(request); }
  catch (error) { if (error instanceof PrototypeSpatialSolverOperationalError) throw error; throw new PrototypeSpatialSolverOperationalError(); }
}

export async function solvePrototypeSpatialLayout() {
  throw new PrototypeSpatialSolverOperationalError();
}
