import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import {
  validatePrototypeEnvironmentFactsJson,
  validatePrototypeSpatialIntentJson,
} from "@matrix-oasis/prototype-spatial-planning-contracts";
import {
  PROTOTYPE_SPATIAL_SOLUTION_PROFILE,
  validatePrototypeSpatialSolutionJson,
} from "@matrix-oasis/prototype-spatial-solution-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";

const PROFILE = PROTOTYPE_SPATIAL_SOLUTION_PROFILE;
const CLEARANCE = Object.freeze({ compact: 250, human: 350, large: 600 });
const CLASS_ORDER = Object.freeze({ large: 0, human: 1, compact: 2 });

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function diagnostic(code, path = "") {
  return deepFreeze({ phase: "solver", severity: "error", code, path, message: code });
}

function rejected(code, path = "") {
  return deepFreeze({ ok: false, diagnostics: [diagnostic(code, path)] });
}

function captureRequest(value) {
  if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) return null;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const expected = ["assetBundleJson", "environmentFactsJson", "runtimeGamePackJson", "runtimeReceiptJson", "spatialIntentJson"];
  if (Reflect.ownKeys(descriptors).some((key) => typeof key !== "string") || Object.keys(descriptors).sort().join("\0") !== expected.join("\0")) return null;
  const output = {};
  for (const key of expected) {
    const descriptor = descriptors[key];
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor) || typeof descriptor.value !== "string") return null;
    output[key] = descriptor.value;
  }
  return output;
}

function validReport(value) {
  if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) return false;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  return Object.keys(descriptors).sort().join("\0") === "diagnostics\0reportVersion\0valid" &&
    descriptors.reportVersion?.value === 1 && descriptors.valid?.value === true &&
    Array.isArray(descriptors.diagnostics?.value) && descriptors.diagnostics.value.length === 0;
}

function parseCanonical(text) {
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? value : null;
  } catch {
    return null;
  }
}

async function sha256Text(text) {
  const cryptoValue = globalThis.crypto;
  if (!cryptoValue?.subtle || typeof cryptoValue.subtle.digest !== "function") throw new Error("crypto");
  const digest = await cryptoValue.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return `sha256:${Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function sameHashIdentity(left, right) {
  return left.format === right.format && left.formatVersion === right.formatVersion && left.canonicalSha256 === right.canonicalSha256;
}

function identityMatches(intent, facts, assetBundle, runtime, receipt, assetBundleSha) {
  const runtimeIdentity = {
    format: runtime.format,
    formatVersion: runtime.formatVersion,
    id: runtime.source.id,
    contentVersion: runtime.source.contentVersion,
    sourceSha256: `sha256:${runtime.source.canonicalSha256}`,
    artifactSha256: `sha256:${receipt.artifact.sha256}`,
  };
  return intent.scene.id === facts.source.scene.id && intent.scene.contentVersion === facts.source.scene.contentVersion &&
    intent.scene.id === assetBundle.scene.id && intent.scene.contentVersion === assetBundle.scene.contentVersion &&
    canonicalizeJsonValue(intent.runtime) === canonicalizeJsonValue(runtimeIdentity) &&
    canonicalizeJsonValue(facts.source.runtime) === canonicalizeJsonValue(runtimeIdentity) &&
    sameHashIdentity(intent.blueprint, facts.source.blueprint) &&
    assetBundle.blueprint.format === intent.blueprint.format && assetBundle.blueprint.formatVersion === intent.blueprint.formatVersion && assetBundle.blueprint.canonicalSha256 === intent.blueprint.canonicalSha256 &&
    intent.assetBundle.canonicalSha256 === assetBundleSha &&
    assetBundle.runtimeIdentity.id === runtime.source.id && assetBundle.runtimeIdentity.contentVersion === runtime.source.contentVersion &&
    assetBundle.runtimeIdentity.authoringCanonicalSha256 === runtimeIdentity.sourceSha256 &&
    assetBundle.runtimeIdentity.artifactSha256 === runtimeIdentity.artifactSha256;
}

function buildProfile() {
  return {
    id: PROFILE.id,
    player: { radiusMm: PROFILE.playerRadiusMm, heightMm: PROFILE.playerHeightMm, eyeHeightMm: PROFILE.playerEyeHeightMm, floorSnapMm: PROFILE.floorSnapMm },
    clearanceMm: { compact: PROFILE.compactClearanceMm, human: PROFILE.humanClearanceMm, large: PROFILE.largeClearanceMm },
    terminal: {
      widthMm: PROFILE.terminalWidthMm,
      depthMm: PROFILE.terminalDepthMm,
      columns: PROFILE.terminalColumns,
      columnSpacingMm: PROFILE.terminalColumnSpacingMm,
      rowSpacingMm: PROFILE.terminalRowSpacingMm,
      originZMm: PROFILE.terminalOriginZMm,
      centerHeightMm: PROFILE.terminalCenterHeightMm,
      interactionDistanceMm: PROFILE.interactionDistanceMm,
    },
    limits: { maxCandidatesPerItem: PROFILE.maxCandidatesPerItem, maxSearchStates: PROFILE.maxSearchStates },
    tolerances: { floorContactMm: PROFILE.floorContactToleranceMm, pathEndpointMm: PROFILE.pathEndpointToleranceMm },
  };
}

function squaredDistance(left, right) {
  const dx = left[0] - right[0];
  const dy = left[1] - right[1];
  const dz = left[2] - right[2];
  return (dx * dx) + (dy * dy) + (dz * dz);
}

function xzDistance(left, right) {
  return Math.hypot(left[0] - right[0], left[2] - right[2]);
}

function compareText(left, right) {
  return left === right ? 0 : left < right ? -1 : 1;
}

function comparePosition(left, right) {
  return (left[0] - right[0]) || (left[1] - right[1]) || (left[2] - right[2]);
}

function safeVector3(value) {
  return Array.isArray(value) && value.length === 3 && value.every(Number.isSafeInteger);
}

function validCandidateRegion(value) {
  if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype ||
      Object.keys(value).sort().join("\0") !==
        "maximumMm\0minimumMm\0preferredMaximumMm\0preferredMinimumMm\0rootRotationMilliDegrees\0rootTranslationMm" ||
      !safeVector3(value.rootTranslationMm) || !safeVector3(value.rootRotationMilliDegrees) ||
      !safeVector3(value.minimumMm) || !safeVector3(value.maximumMm) ||
      !safeVector3(value.preferredMinimumMm) || !safeVector3(value.preferredMaximumMm)) return false;
  return value.minimumMm.every((minimum, axis) => minimum <= value.preferredMinimumMm[axis] &&
    value.preferredMinimumMm[axis] <= value.preferredMaximumMm[axis] &&
    value.preferredMaximumMm[axis] <= value.maximumMm[axis]);
}

export function spatialWalkableEnvelopeCandidateRegion(spatialAssembly) {
  const transforms = spatialAssembly?.transforms;
  if (spatialAssembly?.format !== "matrix-oasis.prototype-spatial-assembly" ||
      spatialAssembly?.formatVersion !== "0.1.0" || transforms?.eulerOrder !== "YXZ" ||
      !safeVector3(transforms?.root?.translationMm) || !safeVector3(transforms?.root?.rotationMilliDegrees) ||
      !safeVector3(transforms?.walkableEnvelope?.minimumMm) || !safeVector3(transforms?.walkableEnvelope?.maximumMm) ||
      !Number.isSafeInteger(transforms?.walkableEnvelope?.wallThicknessMm) ||
      transforms.walkableEnvelope.wallThicknessMm < 0 ||
      !Number.isSafeInteger(transforms?.walkableEnvelope?.binSizeMm) || transforms.walkableEnvelope.binSizeMm < 1 ||
      !Number.isSafeInteger(transforms?.walkableEnvelope?.lateralBandMm) ||
      transforms.walkableEnvelope.lateralBandMm < 1 ||
      transforms.walkableEnvelope.minimumMm.some((minimum, axis) =>
        minimum > transforms.walkableEnvelope.maximumMm[axis])) return null;
  const toleranceMm = transforms.walkableEnvelope.wallThicknessMm + transforms.walkableEnvelope.binSizeMm;
  // The walkable envelope is visual-confidence evidence, not a seed for another broad search band. Only
  // retain its declared wall/bin quantization halo; lateralBandMm was used while deriving the envelope and
  // expanding by it again admitted anchors in visually empty collider regions.
  return deepFreeze({
    rootTranslationMm: [...transforms.root.translationMm],
    rootRotationMilliDegrees: [...transforms.root.rotationMilliDegrees],
    minimumMm: transforms.walkableEnvelope.minimumMm.map((value, axis) =>
      axis === 1 ? value : value - toleranceMm),
    maximumMm: transforms.walkableEnvelope.maximumMm.map((value, axis) =>
      axis === 1 ? value : value + toleranceMm),
    // Hard bounds retain the wall/bin quantization halo for sparse navigation samples. Preferred bounds
    // are the original visual-confidence envelope and are used for complete asset footprints.
    preferredMinimumMm: [...transforms.walkableEnvelope.minimumMm],
    preferredMaximumMm: [...transforms.walkableEnvelope.maximumMm],
  });
}

function rotateX(value, angle) {
  const cosine = Math.cos(angle); const sine = Math.sin(angle);
  return [value[0], (value[1] * cosine) - (value[2] * sine),
    (value[1] * sine) + (value[2] * cosine)];
}

function rotateY(value, angle) {
  const cosine = Math.cos(angle); const sine = Math.sin(angle);
  return [(value[0] * cosine) + (value[2] * sine), value[1],
    (-value[0] * sine) + (value[2] * cosine)];
}

function rotateZ(value, angle) {
  const cosine = Math.cos(angle); const sine = Math.sin(angle);
  return [(value[0] * cosine) - (value[1] * sine),
    (value[0] * sine) + (value[1] * cosine), value[2]];
}

function candidateLocalPosition(position, region) {
  let local = position.map((coordinate, axis) => coordinate - region.rootTranslationMm[axis]);
  const radians = region.rootRotationMilliDegrees.map((value) => value * Math.PI / 180_000);
  // Godot's YXZ basis is Ry * Rx * Rz. Inverting it applies -Y, -X, then -Z.
  local = rotateY(local, -radians[1]);
  local = rotateX(local, -radians[0]);
  return rotateZ(local, -radians[2]);
}

export function spatialCandidateRegionContains(position, region, marginMm = 0) {
  if (!safeVector3(position) || !validCandidateRegion(region) || !Number.isSafeInteger(marginMm) || marginMm < 0) {
    return false;
  }
  const local = candidateLocalPosition(position, region);
  return local[0] >= region.minimumMm[0] + marginMm && local[0] <= region.maximumMm[0] - marginMm &&
    local[2] >= region.minimumMm[2] + marginMm && local[2] <= region.maximumMm[2] - marginMm;
}

function spatialCandidatePreferredContains(position, region, marginMm = 0) {
  if (!safeVector3(position) || !validCandidateRegion(region) || !Number.isSafeInteger(marginMm) || marginMm < 0) {
    return false;
  }
  const local = candidateLocalPosition(position, region);
  return local[0] >= region.preferredMinimumMm[0] + marginMm &&
    local[0] <= region.preferredMaximumMm[0] - marginMm &&
    local[2] >= region.preferredMinimumMm[2] + marginMm &&
    local[2] <= region.preferredMaximumMm[2] - marginMm;
}

function spatialCandidatePreferredDistanceMm(position, region) {
  if (!region) return 0;
  const local = candidateLocalPosition(position, region);
  const dx = local[0] < region.preferredMinimumMm[0] ? region.preferredMinimumMm[0] - local[0] :
    local[0] > region.preferredMaximumMm[0] ? local[0] - region.preferredMaximumMm[0] : 0;
  const dz = local[2] < region.preferredMinimumMm[2] ? region.preferredMinimumMm[2] - local[2] :
    local[2] > region.preferredMaximumMm[2] ? local[2] - region.preferredMaximumMm[2] : 0;
  return Math.hypot(dx, dz);
}

export function spatialPlacementCandidateKey(value) {
  if (!value || typeof value !== "object" || typeof value.placementId !== "string" ||
      !["floor", "wall"].includes(value.anchorKind) || typeof value.anchorId !== "string") return null;
  return `${value.placementId}\0${value.anchorKind}\0${value.anchorId}`;
}

export function spatialStationCandidateKey(value) {
  const spawnId = value?.playerSpawn?.floorAnchorId ?? value?.spawn?.id;
  const terminalId = value?.actionTerminal?.floorAnchorId ?? value?.anchor?.id;
  const approachId = value?.actionTerminal?.approachFloorAnchorId ?? value?.approach?.id;
  const yaw = value?.actionTerminal?.yawMilliDegrees ??
    (value?.quarterTurns === 3 ? -90_000 : value?.quarterTurns * 90_000);
  if (typeof value?.zoneId !== "string" || typeof spawnId !== "string" || typeof terminalId !== "string" ||
      typeof approachId !== "string" || !Number.isSafeInteger(yaw)) return null;
  return `${value.zoneId}\0${spawnId}\0${terminalId}\0${approachId}\0${yaw}`;
}

export function spatialTerminalCandidateKey(value) {
  const terminalId = value?.actionTerminal?.floorAnchorId ?? value?.anchor?.id;
  const yaw = value?.actionTerminal?.yawMilliDegrees ??
    (value?.quarterTurns === 3 ? -90_000 : value?.quarterTurns * 90_000);
  const columns = value?.actionTerminal?.footprint?.columns ?? value?.terminalColumns;
  const actionCount = value?.actionTerminal?.actionCount;
  if (typeof value?.zoneId !== "string" || typeof terminalId !== "string" ||
      !Number.isSafeInteger(yaw) || !Number.isSafeInteger(actionCount) || actionCount < 0 ||
      !Number.isSafeInteger(columns) ||
      columns < 1 || columns > PROFILE.terminalColumns) return null;
  return `terminal\0${value.zoneId}\0${terminalId}\0${yaw}\0${actionCount}\0${columns}`;
}

export function spatialTerminalCandidateKeys(value) {
  if (value?.actionTerminal) {
    const key = spatialTerminalCandidateKey(value);
    return key ? [key] : [];
  }
  const terminalId = value?.anchor?.id;
  const yaw = value?.quarterTurns === 3 ? -90_000 : value?.quarterTurns * 90_000;
  if (typeof value?.zoneId !== "string" || typeof terminalId !== "string" ||
      !Number.isSafeInteger(yaw) || !(value?.terminalLayoutsByActionCount instanceof Map)) return [];
  return [...value.terminalLayoutsByActionCount.entries()].map(([actionCount, layout]) => {
    const columns = layout?.columns;
    if (!Number.isSafeInteger(actionCount) || actionCount < 0 || !Number.isSafeInteger(columns) ||
        columns < 1 || columns > PROFILE.terminalColumns) return null;
    return `terminal\0${value.zoneId}\0${terminalId}\0${yaw}\0${actionCount}\0${columns}`;
  }).filter((key) => key !== null);
}

function polygonAreaXZ(vertices, polygon) {
  let twiceArea = 0n;
  for (let index = 0; index < polygon.vertexIndices.length; index += 1) {
    const current = vertices[polygon.vertexIndices[index]];
    const next = vertices[polygon.vertexIndices[(index + 1) % polygon.vertexIndices.length]];
    twiceArea += (BigInt(current[0]) * BigInt(next[2])) - (BigInt(next[0]) * BigInt(current[2]));
  }
  return (twiceArea < 0n ? -twiceArea : twiceArea) / 2n;
}

function componentArea(facts, component) {
  return component.polygonIndices.reduce((total, index) => total + polygonAreaXZ(facts.navigationMesh.verticesMm, facts.navigationMesh.polygons[index]), 0n);
}

function materializationBounds(materialization) {
  const assets = materialization?.assets.filter((asset) => asset.roles.includes("visual") || asset.roles.includes("collider")) ?? [];
  if (assets.length === 0) return null;
  const minimum = [Infinity, Infinity, Infinity];
  const maximum = [-Infinity, -Infinity, -Infinity];
  for (const asset of assets) {
    for (let axis = 0; axis < 3; axis += 1) {
      minimum[axis] = Math.min(minimum[axis], asset.metrics.boundsMm.min[axis]);
      maximum[axis] = Math.max(maximum[axis], asset.metrics.boundsMm.max[axis]);
    }
  }
  return {
    minimum,
    maximum,
    widthMm: Math.max(1, maximum[0] - minimum[0]),
    heightMm: Math.max(1, maximum[1] - minimum[1]),
    depthMm: Math.max(1, maximum[2] - minimum[2]),
  };
}

function placementRecords(intent, assetBundle) {
  const materializations = new Map(assetBundle.materializations.map((item) => [item.assetBriefId, item]));
  const output = [];
  for (let index = 0; index < intent.placements.length; index += 1) {
    const item = intent.placements[index];
    const bounds = materializationBounds(materializations.get(item.assetBriefId));
    if (!bounds) return null;
    const visibleZoneIds = [...new Set(intent.nodeContexts.filter((context) =>
      context.visiblePlacementIds.includes(item.id)).map((context) => context.zoneId))];
    output.push({ item, bounds, declarationIndex: index, clearanceMm: CLEARANCE[item.clearanceClass],
      visibleZoneIds });
  }
  return output;
}

function zoneOrder(intent, runtime) {
  const zoneIndex = new Map(intent.zones.map((zone, index) => [zone.id, index]));
  const nodeContexts = new Map(intent.nodeContexts.map((item) => [item.nodeId, item]));
  const entryNode = runtime.nodes[runtime.entryNodeIndex];
  const entryZone = entryNode && nodeContexts.get(entryNode.id)?.zoneId;
  if (!entryZone || !zoneIndex.has(entryZone)) return null;
  const ordered = [];
  const seen = new Set([entryZone]);
  const pending = [entryZone];
  while (pending.length > 0) {
    const current = pending.shift();
    ordered.push(current);
    const zone = intent.zones[zoneIndex.get(current)];
    const neighbors = zone.adjacentZoneIds.slice().sort((left, right) => zoneIndex.get(left) - zoneIndex.get(right));
    for (const neighbor of neighbors) {
      if (!seen.has(neighbor)) { seen.add(neighbor); pending.push(neighbor); }
    }
  }
  for (const zone of intent.zones) if (!seen.has(zone.id)) ordered.push(zone.id);
  return ordered;
}

function actionCounts(runtime) {
  return new Map(runtime.nodes.map((node) => [node.id, node.actions.length]));
}

function terminalLayout(actionCount, requestedColumns = PROFILE.terminalColumns) {
  const columns = Math.max(1, Math.min(PROFILE.terminalColumns, Math.max(1, actionCount), requestedColumns));
  const rows = Math.max(1, Math.ceil(actionCount / columns));
  return {
    columns,
    widthMm: PROFILE.terminalWidthMm,
    depthMm: PROFILE.terminalDepthMm,
    layoutWidthMm: PROFILE.terminalWidthMm + ((columns - 1) * PROFILE.terminalColumnSpacingMm),
    layoutDepthMm: PROFILE.terminalDepthMm + ((rows - 1) * PROFILE.terminalRowSpacingMm),
    layoutCenterOffsetMm: [0, PROFILE.terminalOriginZMm - (((rows - 1) * PROFILE.terminalRowSpacingMm) / 2)],
  };
}

function terminalLayoutCandidates(actionCount) {
  const maximumColumns = Math.max(1, Math.min(PROFILE.terminalColumns, Math.max(1, actionCount)));
  return Array.from({ length: maximumColumns }, (_, index) => terminalLayout(actionCount, index + 1))
    .sort((left, right) => Math.max(left.layoutWidthMm, left.layoutDepthMm) -
      Math.max(right.layoutWidthMm, right.layoutDepthMm) ||
      (left.layoutWidthMm * left.layoutDepthMm) - (right.layoutWidthMm * right.layoutDepthMm) ||
      Math.abs(left.layoutWidthMm - left.layoutDepthMm) - Math.abs(right.layoutWidthMm - right.layoutDepthMm) ||
      left.columns - right.columns);
}

function zoneCapacityWeights(intent, placements, counts) {
  return new Map(intent.zones.map((zone) => {
    const placementArea = placements.filter((item) => item.item.zoneId === zone.id).reduce((total, item) => {
      const width = item.bounds.widthMm + (2 * item.clearanceMm);
      const depth = item.bounds.depthMm + (2 * item.clearanceMm);
      return total + BigInt(width * depth);
    }, 0n);
    const contexts = intent.nodeContexts.filter((item) => item.zoneId === zone.id);
    const maxActions = Math.max(0, ...contexts.map((item) => counts.get(item.nodeId) ?? 0));
    const layout = terminalLayoutCandidates(maxActions)[0];
    const interactionArea = BigInt(layout.layoutWidthMm * layout.layoutDepthMm) +
      BigInt(Math.ceil(Math.PI * PROFILE.playerRadiusMm * PROFILE.playerRadiusMm));
    return [zone.id, placementArea + interactionArea];
  }));
}

function componentCandidates(facts, intent, zoneWeights, candidateRegion) {
  const requiredArea = [...zoneWeights.values()].reduce((total, weight) => total + weight, 0n);
  return facts.navigationMesh.components.map((component) => {
    // Domain partitioning must use the same visually admitted anchors as final placement. Partitioning the
    // full collider component first can starve one zone inside the envelope and then select its station in
    // visually empty geometry outside that envelope.
    const anchors = facts.floorAnchors.filter((anchor) => anchor.componentIndex === component.index &&
      (!candidateRegion || spatialCandidateRegionContains(anchor.positionMm, candidateRegion)));
    return { component, anchors, area: componentArea(facts, component) };
  }).filter((entry) => entry.anchors.length >= intent.zones.length && entry.area >= requiredArea)
    .sort((left, right) => (left.area === right.area ? 0 : left.area > right.area ? -1 : 1) || (right.anchors.length - left.anchors.length) || (left.component.index - right.component.index));
}

function medoid(anchors) {
  const sum = anchors.reduce((total, item) => [total[0] + item.positionMm[0], total[1] + item.positionMm[1], total[2] + item.positionMm[2]], [0, 0, 0]);
  const squareSum = anchors.reduce((total, item) => total + (item.positionMm[0] ** 2) + (item.positionMm[1] ** 2) + (item.positionMm[2] ** 2), 0);
  const count = anchors.length;
  return anchors.slice().sort((left, right) => {
    const score = (item) => (count * ((item.positionMm[0] ** 2) + (item.positionMm[1] ** 2) + (item.positionMm[2] ** 2))) - (2 * ((item.positionMm[0] * sum[0]) + (item.positionMm[1] * sum[1]) + (item.positionMm[2] * sum[2]))) + squareSum;
    return (score(left) - score(right)) || compareText(left.id, right.id);
  })[0];
}

function selectSeeds(anchors, orderedZones, candidateRegion) {
  const preferred = candidateRegion ? anchors.filter((anchor) =>
    spatialCandidatePreferredDistanceMm(anchor.positionMm, candidateRegion) === 0) : anchors;
  const pool = preferred.length >= orderedZones.length ? preferred : anchors;
  const seeds = [];
  for (let index = 0; index < orderedZones.length; index += 1) {
    let selected;
    if (index === 0) selected = medoid(pool);
    else {
      selected = pool.filter((anchor) => !seeds.some((seed) => seed.anchor.id === anchor.id)).sort((left, right) => {
        const leftScore = Math.min(...seeds.map((seed) => squaredDistance(left.positionMm, seed.anchor.positionMm)));
        const rightScore = Math.min(...seeds.map((seed) => squaredDistance(right.positionMm, seed.anchor.positionMm)));
        return (rightScore - leftScore) || compareText(left.id, right.id);
      })[0];
    }
    if (!selected) return null;
    seeds.push({ zoneId: orderedZones[index], anchor: selected });
  }
  return seeds;
}

function polygonCentroid(facts, polygonIndex) {
  const polygon = facts.navigationMesh.polygons[polygonIndex];
  const points = polygon.vertexIndices.map((index) => facts.navigationMesh.verticesMm[index]);
  return points.reduce((total, point) => [total[0] + (point[0] / points.length), total[1] + (point[1] / points.length), total[2] + (point[2] / points.length)], [0, 0, 0]);
}

function triangleContainsXZ(position, first, second, third) {
  const cross = (left, right, point) =>
    ((right[0] - left[0]) * (point[2] - left[2])) - ((right[2] - left[2]) * (point[0] - left[0]));
  const firstSign = cross(first, second, position);
  const secondSign = cross(second, third, position);
  const thirdSign = cross(third, first, position);
  const tolerance = 1;
  return (firstSign >= -tolerance && secondSign >= -tolerance && thirdSign >= -tolerance) ||
    (firstSign <= tolerance && secondSign <= tolerance && thirdSign <= tolerance);
}

function navigationHeightAtPosition(facts, polygonIndex, position, requireContainment = false) {
  const polygon = facts.navigationMesh.polygons[polygonIndex];
  if (!polygon || polygon.vertexIndices.length < 3) return null;
  const origin = facts.navigationMesh.verticesMm[polygon.vertexIndices[0]];
  for (let index = 1; index < polygon.vertexIndices.length - 1; index += 1) {
    const left = facts.navigationMesh.verticesMm[polygon.vertexIndices[index]];
    const right = facts.navigationMesh.verticesMm[polygon.vertexIndices[index + 1]];
    if (requireContainment && !triangleContainsXZ(position, origin, left, right)) continue;
    const first = [left[0] - origin[0], left[1] - origin[1], left[2] - origin[2]];
    const second = [right[0] - origin[0], right[1] - origin[1], right[2] - origin[2]];
    const normalX = (first[1] * second[2]) - (first[2] * second[1]);
    const normalY = (first[2] * second[0]) - (first[0] * second[2]);
    const normalZ = (first[0] * second[1]) - (first[1] * second[0]);
    if (normalY === 0) continue;
    return Math.round(origin[1] - (((normalX * (position[0] - origin[0])) +
      (normalZ * (position[2] - origin[2]))) / normalY));
  }
  return null;
}

function navigationSupportAtPosition(facts, position, polygonIndexes) {
  for (const polygonIndex of polygonIndexes.slice().sort((left, right) => left - right)) {
    const baseHeightMm = navigationHeightAtPosition(facts, polygonIndex, position, true);
    if (baseHeightMm !== null) return { polygonIndex, baseHeightMm };
  }
  return null;
}

function navigationHeightAtAnchor(facts, anchor) {
  return navigationHeightAtPosition(facts, anchor.polygonIndex, anchor.positionMm);
}

function anchorMatchesNavigationHeight(facts, anchor) {
  const navigationHeight = navigationHeightAtAnchor(facts, anchor);
  return navigationHeight !== null &&
    Math.abs(navigationHeight - anchor.positionMm[1]) <= PROFILE.floorSnapMm;
}

function polygonGraph(facts, component) {
  const polygonSet = new Set(component.polygonIndices);
  const byVertex = new Map();
  for (const polygonIndex of component.polygonIndices) {
    for (const vertexIndex of facts.navigationMesh.polygons[polygonIndex].vertexIndices) {
      if (!byVertex.has(vertexIndex)) byVertex.set(vertexIndex, []);
      byVertex.get(vertexIndex).push(polygonIndex);
    }
  }
  const graph = new Map(component.polygonIndices.map((index) => [index, new Map()]));
  for (const polygons of byVertex.values()) {
    for (const left of polygons) for (const right of polygons) {
      if (left === right || !polygonSet.has(left) || !polygonSet.has(right)) continue;
      const distance = xzDistance(polygonCentroid(facts, left), polygonCentroid(facts, right));
      graph.get(left).set(right, Math.max(1, Math.round(distance)));
    }
  }
  return graph;
}

function polygonDistances(graph, start) {
  const distances = new Map([...graph.keys()].map((key) => [key, Infinity]));
  distances.set(start, 0);
  const pending = new Set(graph.keys());
  while (pending.size > 0) {
    const current = [...pending].sort((left, right) => (distances.get(left) - distances.get(right)) || (left - right))[0];
    pending.delete(current);
    if (!Number.isFinite(distances.get(current))) break;
    for (const [neighbor, weight] of graph.get(current)) {
      const candidate = distances.get(current) + weight;
      if (candidate < distances.get(neighbor)) distances.set(neighbor, candidate);
    }
  }
  return distances;
}

function polygonDistancesFromStarts(graph, starts) {
  const distances = new Map([...graph.keys()].map((key) => [key, Infinity]));
  for (const start of starts) if (distances.has(start)) distances.set(start, 0);
  const pending = new Set(graph.keys());
  while (pending.size > 0) {
    const current = [...pending].sort((left, right) =>
      (distances.get(left) - distances.get(right)) || (left - right))[0];
    pending.delete(current);
    const currentDistance = distances.get(current);
    if (!Number.isFinite(currentDistance)) break;
    for (const [neighbor, weight] of graph.get(current)) {
      if (!pending.has(neighbor)) continue;
      const candidate = currentDistance + weight;
      if (candidate < distances.get(neighbor)) distances.set(neighbor, candidate);
    }
  }
  return distances;
}

function zoneAnchorQuotas(anchorCount, orderedZones, zoneWeights) {
  const zoneCount = orderedZones.length;
  const remaining = anchorCount - zoneCount;
  const totalWeight = orderedZones.reduce((sum, zoneId) => sum + zoneWeights.get(zoneId), 0n);
  const records = orderedZones.map((zoneId, index) => {
    const numerator = BigInt(remaining) * zoneWeights.get(zoneId);
    return { zoneId, index, quota: 1 + Number(numerator / totalWeight), remainder: numerator % totalWeight };
  });
  let unassigned = anchorCount - records.reduce((sum, item) => sum + item.quota, 0);
  for (const record of records.slice().sort((left, right) =>
    (left.remainder > right.remainder ? -1 : left.remainder < right.remainder ? 1 : 0) || left.index - right.index)) {
    if (unassigned === 0) break;
    record.quota += 1;
    unassigned -= 1;
  }
  return new Map(records.map((item) => [item.zoneId, item.quota]));
}

function buildDomains(facts, component, anchors, seeds, orderedZones, zoneWeights) {
  const graph = polygonGraph(facts, component);
  const distances = new Map(seeds.map((seed) => [seed.zoneId, polygonDistances(graph, seed.anchor.polygonIndex)]));
  const domains = new Map(orderedZones.map((zoneId) => [zoneId, []]));
  const quotas = zoneAnchorQuotas(anchors.length, orderedZones, zoneWeights);
  const assigned = new Set();
  for (const seed of seeds) {
    domains.get(seed.zoneId).push(seed.anchor);
    assigned.add(seed.anchor.id);
  }
  const pairs = [];
  for (const seed of seeds) for (const anchor of anchors) {
    if (assigned.has(anchor.id)) continue;
    const graphDistance = distances.get(seed.zoneId).get(anchor.polygonIndex);
    const score = (Number.isFinite(graphDistance) ? graphDistance : Number.MAX_SAFE_INTEGER) +
      (seed.anchor.polygonIndex === anchor.polygonIndex ? xzDistance(seed.anchor.positionMm, anchor.positionMm) : 0);
    pairs.push({ zoneId: seed.zoneId, zoneIndex: orderedZones.indexOf(seed.zoneId), anchor, score });
  }
  pairs.sort((left, right) => (left.score - right.score) || left.zoneIndex - right.zoneIndex || compareText(left.anchor.id, right.anchor.id));
  for (const pair of pairs) {
    if (assigned.has(pair.anchor.id) || domains.get(pair.zoneId).length >= quotas.get(pair.zoneId)) continue;
    domains.get(pair.zoneId).push(pair.anchor);
    assigned.add(pair.anchor.id);
  }
  for (const domain of domains.values()) domain.sort((left, right) => compareText(left.id, right.id));
  return [...domains.values()].some((items) => items.length === 0) ? null : domains;
}

function expandDomainsForStationAccess(facts, component, anchors, domains, orderedZones, intent, counts) {
  const graph = polygonGraph(facts, component);
  const expanded = new Map();
  for (const zoneId of orderedZones) {
    const contexts = intent.nodeContexts.filter((context) => context.zoneId === zoneId);
    const maximumActions = Math.max(0, ...contexts.map((context) => counts.get(context.nodeId) ?? 0));
    const layout = terminalLayoutCandidates(maximumActions)[0];
    const stationRadiusMm = Math.ceil(Math.hypot(layout.layoutWidthMm / 2, layout.layoutDepthMm / 2)) +
      PROFILE.interactionDistanceMm + PROFILE.playerRadiusMm;
    const base = domains.get(zoneId);
    const distances = polygonDistancesFromStarts(graph, new Set(base.map((anchor) => anchor.polygonIndex)));
    expanded.set(zoneId, anchors.filter((anchor) => distances.get(anchor.polygonIndex) <= stationRadiusMm)
      .sort((left, right) => compareText(left.id, right.id)));
  }
  return expanded;
}

function rectangleSamplePoints(position, width, depth, yawQuarterTurns = 0) {
  const actualWidth = yawQuarterTurns % 2 === 0 ? width : depth;
  const actualDepth = yawQuarterTurns % 2 === 0 ? depth : width;
  const halfWidth = actualWidth / 2;
  const halfDepth = actualDepth / 2;
  return [
    position,
    [position[0] - halfWidth, position[1], position[2] - halfDepth],
    [position[0] - halfWidth, position[1], position[2] + halfDepth],
    [position[0] + halfWidth, position[1], position[2] - halfDepth],
    [position[0] + halfWidth, position[1], position[2] + halfDepth],
  ];
}

function footprintCoveredByAnchors(position, width, depth, anchors, yawQuarterTurns = 0,
  maximumHeightGapMm = PROFILE.floorContactToleranceMm) {
  const maximumHorizontalGapMm = Math.max(1_500, Math.ceil(Math.max(width, depth) / 2));
  return rectangleSamplePoints(position, width, depth, yawQuarterTurns).every((sample) =>
    anchors.some((anchor) =>
      Math.abs(anchor.positionMm[1] - sample[1]) <= maximumHeightGapMm &&
      xzDistance(anchor.positionMm, sample) <= maximumHorizontalGapMm));
}

export function navigationFootprintSupported(facts, position, widthMm, depthMm, coverageAnchors) {
  const polygonIndexes = [...new Set(coverageAnchors.map((anchor) => anchor.polygonIndex))];
  return rectangleSamplePoints(position, widthMm, depthMm).every((sample) =>
    navigationSupportAtPosition(facts, sample, polygonIndexes) !== null);
}

function rotatedPosition(origin, localX, localZ, quarterTurns) {
  const normalized = ((quarterTurns % 4) + 4) % 4;
  if (normalized === 0) return [origin[0] + localX, origin[1], origin[2] + localZ];
  if (normalized === 1) return [origin[0] + localZ, origin[1], origin[2] - localX];
  if (normalized === 2) return [origin[0] - localX, origin[1], origin[2] - localZ];
  return [origin[0] - localZ, origin[1], origin[2] + localX];
}

function terminalBoxes(origin, actionCount, columns, quarterTurns) {
  const boxes = [];
  for (let index = 0; index < actionCount; index += 1) {
    const row = Math.floor(index / columns);
    const column = index % columns;
    const firstIndex = row * columns;
    const rowCount = Math.min(columns, actionCount - firstIndex);
    const centeredColumn = column - ((rowCount - 1) / 2);
    boxes.push({
      center: rotatedPosition(origin, centeredColumn * PROFILE.terminalColumnSpacingMm,
        PROFILE.terminalOriginZMm - (row * PROFILE.terminalRowSpacingMm), quarterTurns),
      widthMm: quarterTurns % 2 === 0 ? PROFILE.terminalWidthMm : PROFILE.terminalDepthMm,
      depthMm: quarterTurns % 2 === 0 ? PROFILE.terminalDepthMm : PROFILE.terminalWidthMm,
    });
  }
  for (const box of boxes) box.sightTarget = [box.center[0], box.center[1] + PROFILE.terminalCenterHeightMm, box.center[2]];
  return boxes;
}

function supportedTerminalLayout(facts, origin, actionCount, columns, quarterTurns, supportCandidates,
  coverageAnchors, candidateRegion) {
  const boxes = terminalBoxes(origin, actionCount, columns, quarterTurns);
  const footprintBounds = {
    widthMm: PROFILE.terminalWidthMm,
    heightMm: PROFILE.terminalCenterHeightMm * 2,
    depthMm: PROFILE.terminalDepthMm,
  };
  const footprintRadiusMm = circleRadius(footprintBounds);
  const polygonIndexes = [...new Set(coverageAnchors.map((anchor) => anchor.polygonIndex))];
  const supports = [];
  for (const box of boxes) {
    const navigationSupport = navigationSupportAtPosition(facts, box.center, polygonIndexes);
    if (!navigationSupport) return null;
    const baseHeightMm = navigationSupport.baseHeightMm;
    const supportedCenter = [box.center[0], baseHeightMm, box.center[2]];
    if (candidateRegion && !spatialCandidatePreferredContains(supportedCenter, candidateRegion,
      footprintRadiusMm)) return null;
    if (!navigationFootprintSupported(facts, supportedCenter, box.widthMm, box.depthMm,
      coverageAnchors)) return null;
    // R14 terminals have a fixed physical bottom clearance. The exact center support height comes from the
    // containing NavMesh polygon; the isolated Godot verifier remains authoritative for the full collider.
    const support = supportCandidates.slice().sort((left, right) =>
      xzDistance(left.positionMm, supportedCenter) - xzDistance(right.positionMm, supportedCenter) ||
      compareText(left.id, right.id))[0];
    if (!support) return null;
    box.center = [box.center[0], baseHeightMm, box.center[2]];
    box.sightTarget = [box.center[0], box.center[1] + PROFILE.terminalCenterHeightMm, box.center[2]];
    supports.push({ anchor: support, baseHeightMm });
  }
  return { boxes, supports };
}

export function terminalBoxesInteractableFrom(approach, boxes) {
  const eye = [approach[0], approach[1] + PROFILE.playerEyeHeightMm, approach[2]];
  const maximumSquaredDistance = PROFILE.interactionDistanceMm * PROFILE.interactionDistanceMm;
  return boxes.every((box) => squaredDistance(eye, box.sightTarget) <= maximumSquaredDistance);
}

export function terminalBoxesReachableFromAnchors(anchors, boxes) {
  return boxes.every((box) => anchors.some((approach) =>
    !boxes.some((other) => insideRectangle(approach, other.center, other.widthMm, other.depthMm,
      PROFILE.playerRadiusMm)) && terminalBoxesInteractableFrom(approach, [box])));
}

export function terminalApproachIsBroadside(approach, boxes, quarterTurns) {
  if (boxes.length === 0) return true;
  const center = boxes.reduce((sum, box) => [sum[0] + box.center[0], sum[1] + box.center[2]],
    [0, 0]).map((value) => value / boxes.length);
  const deltaX = approach[0] - center[0];
  const deltaZ = approach[2] - center[1];
  const normalized = ((quarterTurns % 4) + 4) % 4;
  const columnProjection = normalized === 0 ? deltaX : normalized === 1 ? -deltaZ :
    normalized === 2 ? -deltaX : deltaZ;
  const depthProjection = normalized === 0 ? deltaZ : normalized === 1 ? deltaX :
    normalized === 2 ? -deltaZ : -deltaX;
  return Math.abs(depthProjection) >= Math.abs(columnProjection);
}

function insideRectangle(position, center, width, depth, padding = 0) {
  return Math.abs(position[0] - center[0]) < (width / 2) + padding && Math.abs(position[2] - center[2]) < (depth / 2) + padding;
}

function rectanglesOverlap(leftCenter, leftWidth, leftDepth, rightCenter, rightWidth, rightDepth) {
  return Math.abs(leftCenter[0] - rightCenter[0]) < (leftWidth + rightWidth) / 2 &&
    Math.abs(leftCenter[2] - rightCenter[2]) < (leftDepth + rightDepth) / 2;
}

function yawTo(from, to) {
  const degrees = Math.atan2(to[0] - from[0], to[2] - from[2]) * 180 / Math.PI;
  let value = Math.round(degrees * 1000);
  while (value > 180_000) value -= 360_000;
  while (value < -180_000) value += 360_000;
  return value;
}

function compareStationCandidates(left, right, candidateRegion) {
  return (spatialCandidatePreferredDistanceMm(left.anchor.positionMm, candidateRegion) +
    spatialCandidatePreferredDistanceMm(left.approach.positionMm, candidateRegion) +
    spatialCandidatePreferredDistanceMm(left.spawn.positionMm, candidateRegion)) -
    (spatialCandidatePreferredDistanceMm(right.anchor.positionMm, candidateRegion) +
    spatialCandidatePreferredDistanceMm(right.approach.positionMm, candidateRegion) +
    spatialCandidatePreferredDistanceMm(right.spawn.positionMm, candidateRegion)) ||
    left.layoutRank - right.layoutRank ||
    squaredDistance(left.anchor.positionMm, left.seedPositionMm) -
      squaredDistance(right.anchor.positionMm, right.seedPositionMm) ||
    squaredDistance(left.approach.positionMm, left.anchor.positionMm) -
      squaredDistance(right.approach.positionMm, right.anchor.positionMm) ||
    squaredDistance(left.spawn.positionMm, left.seedPositionMm) -
      squaredDistance(right.spawn.positionMm, right.seedPositionMm) ||
    left.quarterTurns - right.quarterTurns || compareText(left.anchor.id, right.anchor.id) ||
    compareText(left.approach.id, right.approach.id) || compareText(left.spawn.id, right.spawn.id);
}

function diverseStationCandidatesForLayout(candidates, candidateRegion, maximum) {
  const ranked = candidates.slice().sort((left, right) => compareStationCandidates(left, right, candidateRegion));
  const groupsByAnchor = new Map();
  for (const candidate of ranked) {
    if (!groupsByAnchor.has(candidate.anchor.id)) groupsByAnchor.set(candidate.anchor.id, []);
    groupsByAnchor.get(candidate.anchor.id).push(candidate);
  }
  const groups = [...groupsByAnchor.values()];
  if (groups.length === 0) return [];
  const groupLimit = Math.min(groups.length, Math.max(1, Math.floor(maximum / 4)));
  const selected = [groups[0]];
  const selectedIds = new Set([groups[0][0].anchor.id]);
  while (selected.length < groupLimit) {
    const next = groups.filter((group) => !selectedIds.has(group[0].anchor.id)).sort((left, right) => {
      const leftDistance = Math.min(...selected.map((group) =>
        squaredDistance(left[0].anchor.positionMm, group[0].anchor.positionMm)));
      const rightDistance = Math.min(...selected.map((group) =>
        squaredDistance(right[0].anchor.positionMm, group[0].anchor.positionMm)));
      return (rightDistance - leftDistance) || compareStationCandidates(left[0], right[0], candidateRegion);
    })[0];
    if (!next) break;
    selected.push(next); selectedIds.add(next[0].anchor.id);
  }
  const perAnchorLimit = Math.max(1, Math.floor(maximum / selected.length));
  const output = [];
  for (const group of selected) {
    const byOrientation = new Map([0, 1, 2, 3].map((quarterTurns) => [quarterTurns,
      group.filter((candidate) => candidate.quarterTurns === quarterTurns)]));
    for (let round = 0; output.filter((candidate) => candidate.anchor.id === group[0].anchor.id).length < perAnchorLimit;
      round += 1) {
      let added = false;
      for (const quarterTurns of [0, 1, 2, 3]) {
        const candidate = byOrientation.get(quarterTurns)[round];
        if (!candidate || output.filter((item) => item.anchor.id === group[0].anchor.id).length >= perAnchorLimit) continue;
        output.push(candidate); added = true;
      }
      if (!added) break;
    }
  }
  return output.sort((left, right) => compareStationCandidates(left, right, candidateRegion)).slice(0, maximum);
}

export function diverseStationCandidates(candidates, candidateRegion) {
  const byColumns = new Map();
  for (const candidate of candidates) {
    if (!byColumns.has(candidate.terminalColumns)) byColumns.set(candidate.terminalColumns, []);
    byColumns.get(candidate.terminalColumns).push(candidate);
  }
  const queues = [...byColumns.entries()].map(([columns, values]) => ({
    columns,
    values: diverseStationCandidatesForLayout(values, candidateRegion, PROFILE.maxCandidatesPerItem),
  })).filter((entry) => entry.values.length > 0).sort((left, right) =>
    compareStationCandidates(left.values[0], right.values[0], candidateRegion) || left.columns - right.columns);
  const output = [];
  for (let round = 0; output.length < PROFILE.maxCandidatesPerItem; round += 1) {
    let added = false;
    for (const queue of queues) {
      const candidate = queue.values[round];
      if (!candidate || output.length >= PROFILE.maxCandidatesPerItem) continue;
      output.push(candidate);
      added = true;
    }
    if (!added) break;
  }
  return output;
}

function stationCandidatesForZone(zoneId, facts, domain, coverageAnchors, seed, contexts, counts,
  rejectedStationCandidateKeys, candidateRegion) {
  const maximumActions = Math.max(0, ...contexts.map((item) => counts.get(item.nodeId) ?? 0));
  const actionCountsInZone = [...new Set(contexts.map((item) => counts.get(item.nodeId) ?? 0))]
    .sort((left, right) => right - left);
  const layouts = terminalLayoutCandidates(maximumActions);
  const selectableAnchors = candidateRegion ? domain.filter((anchor) =>
    spatialCandidateRegionContains(anchor.positionMm, candidateRegion)) : domain;
  const navigationAnchors = selectableAnchors.filter((anchor) =>
    anchorMatchesNavigationHeight(facts, anchor) && (!candidateRegion ||
      spatialCandidateRegionContains(anchor.positionMm, candidateRegion, PROFILE.playerRadiusMm)) &&
      navigationFootprintSupported(facts, anchor.positionMm, PROFILE.playerRadiusMm * 2,
        PROFILE.playerRadiusMm * 2, coverageAnchors));
  const terminalCandidates = [];
  for (const anchor of selectableAnchors) {
    for (let layoutRank = 0; layoutRank < layouts.length; layoutRank += 1) {
      const layout = layouts[layoutRank];
      for (const quarterTurns of [0, 1, 2, 3]) {
      const terminalLayoutsByActionCount = new Map();
      let supported = true;
      for (const actionCount of actionCountsInZone) {
        const columns = Math.min(layout.columns, Math.max(1, actionCount));
        const terminalLayoutValue = supportedTerminalLayout(facts, anchor.positionMm, actionCount, columns,
          quarterTurns, navigationAnchors, coverageAnchors, candidateRegion);
        if (!terminalLayoutValue) { supported = false; break; }
        terminalLayoutsByActionCount.set(actionCount, { columns, ...terminalLayoutValue });
      }
      if (!supported) continue;
      const boxes = actionCountsInZone.flatMap((actionCount) => terminalLayoutsByActionCount.get(actionCount).boxes);
      if (!terminalBoxesReachableFromAnchors(navigationAnchors.map((candidate) => candidate.positionMm), boxes)) continue;
      const primaryBoxes = terminalLayoutsByActionCount.get(maximumActions)?.boxes ?? [];
      const approaches = navigationAnchors.filter((candidate) => candidate.id !== anchor.id &&
        (boxes.length === 0 || boxes.some((box) => terminalBoxesInteractableFrom(candidate.positionMm, [box]))) &&
        terminalApproachIsBroadside(candidate.positionMm, primaryBoxes, quarterTurns) &&
        !boxes.some((box) => insideRectangle(candidate.positionMm, box.center, box.widthMm, box.depthMm,
          PROFILE.playerRadiusMm))).sort((left, right) =>
        squaredDistance(left.positionMm, anchor.positionMm) - squaredDistance(right.positionMm, anchor.positionMm) ||
        compareText(left.id, right.id));
      for (const approach of approaches) {
        const spawn = navigationAnchors.filter((candidate) => candidate.id !== approach.id &&
          !boxes.some((box) => insideRectangle(candidate.positionMm, box.center, box.widthMm, box.depthMm,
            PROFILE.playerRadiusMm)) && boxes.every((box) =>
            xzDistance(candidate.positionMm, box.center) >= PROFILE.interactionDistanceMm)).sort((left, right) =>
          squaredDistance(left.positionMm, seed.positionMm) - squaredDistance(right.positionMm, seed.positionMm) ||
          compareText(left.id, right.id))[0];
        if (!spawn) continue;
        const candidate = { zoneId, anchor, approach, spawn, quarterTurns, boxes, terminalColumns: layout.columns,
          terminalLayoutsByActionCount,
          layoutRank, seedPositionMm: seed.positionMm };
        if (!rejectedStationCandidateKeys.has(spatialStationCandidateKey(candidate)) &&
            !spatialTerminalCandidateKeys(candidate).some((key) => rejectedStationCandidateKeys.has(key))) {
          terminalCandidates.push(candidate);
        }
      }
    }
    }
  }
  return diverseStationCandidates(terminalCandidates, candidateRegion).map((selected) => ({
    zoneId,
    terminalAnchor: selected.anchor,
    approachAnchor: selected.approach,
    spawnAnchor: selected.spawn,
    yawMilliDegrees: selected.quarterTurns === 3 ? -90_000 : selected.quarterTurns * 90_000,
    terminalColumns: selected.terminalColumns,
    terminalLayoutsByActionCount: selected.terminalLayoutsByActionCount,
    terminalBoxes: selected.boxes,
    reservedSpawn: { center: selected.spawn.positionMm, radiusMm: PROFILE.playerRadiusMm },
  }));
}

function placementOrder(records) {
  return records.slice().sort((left, right) => {
    const leftConstraints = left.item.near.length + left.item.separate.length + (left.item.facing.kind === "placement" ? 1 : 0);
    const rightConstraints = right.item.near.length + right.item.separate.length + (right.item.facing.kind === "placement" ? 1 : 0);
    return (left.item.support === right.item.support ? 0 : left.item.support === "wall" ? -1 : 1) ||
      (CLASS_ORDER[left.item.clearanceClass] - CLASS_ORDER[right.item.clearanceClass]) ||
      (rightConstraints - leftConstraints) || (left.declarationIndex - right.declarationIndex);
  });
}

function centerOfDomain(domain) {
  return domain.reduce((total, anchor) => [total[0] + (anchor.positionMm[0] / domain.length), total[1] + (anchor.positionMm[1] / domain.length), total[2] + (anchor.positionMm[2] / domain.length)], [0, 0, 0]).map(Math.round);
}

function stationCenter(station, fallback) {
  if (!station) return fallback;
  const points = [station.terminalAnchor.positionMm, station.approachAnchor.positionMm,
    station.spawnAnchor.positionMm];
  return points.reduce((total, point) => total.map((value, axis) => value + point[axis] / points.length),
    [0, 0, 0]).map(Math.round);
}

function stationCanReachRequiredWallPlacements(station, zoneId, records, domain, facts,
  candidateRegion) {
  const center = stationCenter(station, centerOfDomain(domain));
  const floorIds = new Set(domain.map((anchor) => anchor.id));
  for (const record of records.filter((item) =>
    item.item.zoneId === zoneId && item.item.support === "wall")) {
    const available = facts.wallAnchors.some((wall) => {
      if (!floorIds.has(wall.nearestFloorAnchorId) || wall.availableWidthMm < record.bounds.widthMm ||
          wall.availableHeightMm < record.bounds.heightMm) return false;
      const floor = domain.find((anchor) => anchor.id === wall.nearestFloorAnchorId);
      if (!floor) return false;
      const position = candidatePosition(record, floor, wall);
      return (!candidateRegion || spatialCandidateRegionContains(position, candidateRegion)) &&
        xzDistance(position, center) + circleRadius(record.bounds) + record.clearanceMm <=
          PROFILE.interactionDistanceMm * 2;
    });
    if (!available) return false;
  }
  return true;
}

function circleRadius(bounds) {
  return Math.ceil(Math.hypot(bounds.widthMm, bounds.depthMm) / 2);
}

function uprightAssetFitsFloorContact(anchor, bounds, anchors) {
  const vertical = anchor.normalMicros[1];
  if (vertical <= 0) return false;
  const horizontal = Math.hypot(anchor.normalMicros[0], anchor.normalMicros[2]);
  const maximumRiseMm = (circleRadius(bounds) * horizontal) / vertical;
  return maximumRiseMm <= PROFILE.floorContactToleranceMm && anchors.includes(anchor);
}

function candidateYaw(item, position, zoneCenter, selectedById) {
  if (item.facing.kind === "none") return 0;
  if (item.facing.kind === "zone-center") return yawTo(position, zoneCenter);
  const target = selectedById.get(item.facing.placementId);
  return target ? yawTo(position, target.positionMm) : 0;
}

function candidatePosition(record, anchor, wallAnchor = null) {
  if (!wallAnchor) return [anchor.positionMm[0], anchor.positionMm[1] - record.bounds.minimum[1], anchor.positionMm[2]];
  const normal = wallAnchor.normalMicros.map((value) => value / 1_000_000);
  const depthOffset = record.bounds.depthMm / 2;
  return [
    Math.round(wallAnchor.positionMm[0] + (normal[0] * depthOffset)),
    Math.round(wallAnchor.positionMm[1] - ((record.bounds.minimum[1] + record.bounds.maximum[1]) / 2)),
    Math.round(wallAnchor.positionMm[2] + (normal[2] * depthOffset)),
  ];
}

function generateCandidates(record, domains, facts, stations, coverageAnchors, selectedById, rejectedCandidateKeys,
  candidateRegion) {
  const domain = domains.get(record.item.zoneId);
  const zoneCenter = stationCenter(stations.get(record.item.zoneId), centerOfDomain(domain));
  const candidates = [];
  if (record.item.support === "floor") {
    const anchors = domain.slice().sort((left, right) => {
      const delta = squaredDistance(left.positionMm, zoneCenter) - squaredDistance(right.positionMm, zoneCenter);
      return spatialCandidatePreferredDistanceMm(left.positionMm, candidateRegion) -
        spatialCandidatePreferredDistanceMm(right.positionMm, candidateRegion) ||
        (record.item.anchor === "edge" ? -delta : delta) ||
        compareText(left.id, right.id);
    });
    for (const anchor of anchors) {
      const position = candidatePosition(record, anchor);
      const footprintRadiusMm = circleRadius(record.bounds) + record.clearanceMm;
      // A zone's already validated spawn/terminal station is the observable interaction cluster. A free
      // placement must not degrade into a lexicographically selected remote collider island: keep the
      // complete footprint within two interaction ranges of that station or fail closed.
      if (stations.has(record.item.zoneId) &&
          xzDistance(position, zoneCenter) + footprintRadiusMm > PROFILE.interactionDistanceMm * 2) continue;
      if (candidateRegion && !spatialCandidatePreferredContains(position, candidateRegion, footprintRadiusMm)) continue;
      if (!uprightAssetFitsFloorContact(anchor, record.bounds, coverageAnchors)) continue;
      if (!footprintCoveredByAnchors(position, record.bounds.widthMm + (2 * record.clearanceMm),
        record.bounds.depthMm + (2 * record.clearanceMm), coverageAnchors)) continue;
      candidates.push({ anchorKind: "floor", anchorId: anchor.id, positionMm: position, radiusMm: circleRadius(record.bounds), record });
    }
  } else {
    const floorIds = new Set(domain.map((item) => item.id));
    const walls = facts.wallAnchors.filter((wall) => floorIds.has(wall.nearestFloorAnchorId) && wall.availableWidthMm >= record.bounds.widthMm && wall.availableHeightMm >= record.bounds.heightMm)
      .sort((left, right) => compareText(left.id, right.id));
    for (const wall of walls) {
      const floor = domain.find((anchor) => anchor.id === wall.nearestFloorAnchorId);
      const position = candidatePosition(record, floor, wall);
      if (stations.has(record.item.zoneId) &&
          xzDistance(position, zoneCenter) + circleRadius(record.bounds) + record.clearanceMm >
            PROFILE.interactionDistanceMm * 2) continue;
      if (candidateRegion && !spatialCandidateRegionContains(position, candidateRegion)) continue;
      candidates.push({ anchorKind: "wall", anchorId: wall.id, positionMm: position, radiusMm: circleRadius(record.bounds), record });
    }
  }
  return candidates.filter((candidate) => {
    const candidateWidth = record.bounds.widthMm + (2 * record.clearanceMm);
    const candidateDepth = record.bounds.depthMm + (2 * record.clearanceMm);
    const visibleStations = record.visibleZoneIds.map((zoneId) => stations.get(zoneId)).filter(Boolean);
    if (visibleStations.some((visibleStation) => visibleStation.terminalBoxes.some((box) =>
      rectanglesOverlap(candidate.positionMm, candidateWidth, candidateDepth,
        box.center, box.widthMm, box.depthMm)))) return false;
    return visibleStations.every((visibleStation) => {
      const requiredDistance = candidate.radiusMm + record.clearanceMm + PROFILE.playerRadiusMm;
      return xzDistance(candidate.positionMm, visibleStation.reservedSpawn.center) >= requiredDistance &&
        xzDistance(candidate.positionMm, visibleStation.approachAnchor.positionMm) >= requiredDistance;
    });
  }).map((candidate) => ({
    ...candidate,
    yawMilliDegrees: candidateYaw(record.item, candidate.positionMm, zoneCenter, selectedById),
  })).filter((candidate) => !rejectedCandidateKeys.has(spatialPlacementCandidateKey({
    placementId: record.item.id, anchorKind: candidate.anchorKind, anchorId: candidate.anchorId,
  }))).slice(0, PROFILE.maxCandidatesPerItem);
}

function candidatesOverlap(left, right) {
  const dx = Math.abs(left.positionMm[0] - right.positionMm[0]);
  const dz = Math.abs(left.positionMm[2] - right.positionMm[2]);
  const horizontalGap = (left.record.bounds.widthMm + right.record.bounds.widthMm) / 2 + Math.max(left.record.clearanceMm, right.record.clearanceMm);
  const depthGap = (left.record.bounds.depthMm + right.record.bounds.depthMm) / 2 + Math.max(left.record.clearanceMm, right.record.clearanceMm);
  return dx < horizontalGap && dz < depthGap;
}

function constraintsSatisfied(candidate, selectedById, complete) {
  for (const constraint of candidate.record.item.near) {
    const other = selectedById.get(constraint.placementId);
    if (!other) { if (complete) return false; continue; }
    if (xzDistance(candidate.positionMm, other.positionMm) > constraint.distanceMm) return false;
  }
  for (const constraint of candidate.record.item.separate) {
    const other = selectedById.get(constraint.placementId);
    if (!other) { if (complete) return false; continue; }
    if (xzDistance(candidate.positionMm, other.positionMm) < constraint.distanceMm) return false;
  }
  return true;
}

function distanceConstraintsFeasible(records) {
  const pairs = new Map();
  for (const record of records) {
    for (const constraint of record.item.near) {
      const pair = [record.item.id, constraint.placementId].sort(compareText).join("\0");
      const bounds = pairs.get(pair) ?? { minimumMm: 0, maximumMm: Number.POSITIVE_INFINITY };
      bounds.maximumMm = Math.min(bounds.maximumMm, constraint.distanceMm);
      pairs.set(pair, bounds);
    }
    for (const constraint of record.item.separate) {
      const pair = [record.item.id, constraint.placementId].sort(compareText).join("\0");
      const bounds = pairs.get(pair) ?? { minimumMm: 0, maximumMm: Number.POSITIVE_INFINITY };
      bounds.minimumMm = Math.max(bounds.minimumMm, constraint.distanceMm);
      pairs.set(pair, bounds);
    }
  }
  return [...pairs.values()].every((bounds) => bounds.minimumMm <= bounds.maximumMm);
}

function searchPlacements(records, domains, facts, stations, coverageAnchors, rejectedCandidateKeys,
  candidateRegion, maximumStates = PROFILE.maxSearchStates) {
  const ordered = placementOrder(records);
  const selected = [];
  const selectedById = new Map();
  let expandedStates = 0;
  let candidateCount = 0;
  let limitExceeded = false;
  function visit(index) {
    if (index === ordered.length) return selected.every((candidate) => constraintsSatisfied(candidate, selectedById, true));
    const record = ordered[index];
    const candidates = generateCandidates(record, domains, facts, stations, coverageAnchors, selectedById,
      rejectedCandidateKeys, candidateRegion);
    candidateCount += candidates.length;
    for (const candidate of candidates) {
      expandedStates += 1;
      if (expandedStates > maximumStates) { limitExceeded = true; return false; }
      if (selected.some((other) => candidatesOverlap(candidate, other)) || !constraintsSatisfied(candidate, selectedById, false)) continue;
      selected.push(candidate); selectedById.set(record.item.id, candidate);
      if (visit(index + 1)) return true;
      selectedById.delete(record.item.id); selected.pop();
    }
    return false;
  }
  const solved = visit(0);
  return { solved, limitExceeded, expandedStates: Math.max(1, expandedStates), candidateCount: Math.max(1, candidateCount), selectedById };
}

function searchStationsAndPlacements(orderedZones, stationCandidates, records, domains, facts, coverageAnchors,
  rejectedCandidateKeys, candidateRegion) {
  const stations = new Map();
  let selectedById = new Map();
  let expandedStates = 0;
  let candidateCount = [...stationCandidates.values()].reduce((sum, candidates) => sum + candidates.length, 0);
  let limitExceeded = false;
  function visit(index) {
    if (index === orderedZones.length) {
      const remaining = PROFILE.maxSearchStates - expandedStates;
      if (remaining <= 0) { limitExceeded = true; return false; }
      const placements = searchPlacements(records, domains, facts, stations, coverageAnchors, rejectedCandidateKeys,
        candidateRegion, remaining);
      expandedStates += placements.expandedStates;
      candidateCount += placements.candidateCount;
      if (placements.limitExceeded || expandedStates > PROFILE.maxSearchStates) { limitExceeded = true; return false; }
      if (!placements.solved) return false;
      selectedById = placements.selectedById;
      return true;
    }
    const zoneId = orderedZones[index];
    for (const station of stationCandidates.get(zoneId)) {
      expandedStates += 1;
      if (expandedStates > PROFILE.maxSearchStates) { limitExceeded = true; return false; }
      stations.set(zoneId, station);
      if (visit(index + 1)) return true;
      stations.delete(zoneId);
      if (limitExceeded) return false;
    }
    return false;
  }
  const solved = visit(0);
  return { solved, limitExceeded, expandedStates: Math.max(1, expandedStates),
    candidateCount: Math.max(1, candidateCount), selectedById, stations: new Map(stations) };
}

function finalizedPlacements(intent, records, selectedById, domains, stations) {
  const byId = new Map(records.map((record) => [record.item.id, record]));
  return intent.placements.map((item) => {
    const candidate = selectedById.get(item.id);
    const record = byId.get(item.id);
    const yaw = candidateYaw(item, candidate.positionMm,
      stationCenter(stations.get(item.zoneId), centerOfDomain(domains.get(item.zoneId))), selectedById);
    return {
      placementId: item.id,
      anchorKind: candidate.anchorKind,
      anchorId: candidate.anchorId,
      positionMm: candidate.positionMm,
      rotationMilliDegrees: [0, yaw, 0],
      footprint: { widthMm: record.bounds.widthMm, heightMm: record.bounds.heightMm, depthMm: record.bounds.depthMm },
      proof: { supportVerified: true, clearanceVerified: true, nonOverlapping: true },
    };
  });
}

function nodeSolutions(intent, runtime, stations) {
  const counts = actionCounts(runtime);
  return intent.nodeContexts.map((context) => {
    const station = stations.get(context.zoneId);
    const actionCount = counts.get(context.nodeId);
    const supportedLayout = station.terminalLayoutsByActionCount.get(actionCount);
    if (!supportedLayout) throw new Error("terminal-layout");
    const footprint = terminalLayout(actionCount, supportedLayout.columns);
    return {
      nodeId: context.nodeId,
      zoneId: context.zoneId,
      visiblePlacementIds: context.visiblePlacementIds,
      playerSpawn: { floorAnchorId: station.spawnAnchor.id, positionMm: station.spawnAnchor.positionMm, yawMilliDegrees: station.yawMilliDegrees },
      actionTerminal: {
        floorAnchorId: station.terminalAnchor.id,
        approachFloorAnchorId: station.approachAnchor.id,
        positionMm: station.terminalAnchor.positionMm,
        yawMilliDegrees: station.yawMilliDegrees,
        actionCount,
        footprint,
        terminalSupports: supportedLayout.supports.map((support) => ({
          floorAnchorId: support.anchor.id,
          baseHeightMm: support.baseHeightMm,
        })),
      },
      approachPathFloorAnchorIds: [station.spawnAnchor.id, station.approachAnchor.id],
    };
  });
}

function stationCapableSeeds(facts, anchors, orderedZones, initialSeeds, intent, counts,
  rejectedStationCandidateKeys, candidateRegion) {
  const selected = [];
  const center = medoid(anchors);
  for (const zoneId of orderedZones) {
    const initial = initialSeeds.find((seed) => seed.zoneId === zoneId);
    const contexts = intent.nodeContexts.filter((context) => context.zoneId === zoneId);
    const candidates = initial && stationCandidatesForZone(zoneId, facts, anchors, anchors, initial.anchor,
      contexts, counts, rejectedStationCandidateKeys, candidateRegion);
    if (!candidates || candidates.length === 0) return null;
    const viable = [...new Map(candidates.map((candidate) =>
      [candidate.terminalAnchor.id, candidate.terminalAnchor])).values()];
    const available = viable.filter((anchor) => !selected.some((seed) => seed.anchor.id === anchor.id));
    const pool = available.length > 0 ? available : viable;
    const anchor = pool.sort((left, right) => {
      if (selected.length === 0) {
        return squaredDistance(left.positionMm, center.positionMm) -
          squaredDistance(right.positionMm, center.positionMm) || compareText(left.id, right.id);
      }
      const leftDistance = Math.min(...selected.map((seed) =>
        squaredDistance(left.positionMm, seed.anchor.positionMm)));
      const rightDistance = Math.min(...selected.map((seed) =>
        squaredDistance(right.positionMm, seed.anchor.positionMm)));
      return (rightDistance - leftDistance) || compareText(left.id, right.id);
    })[0];
    if (!anchor) return null;
    selected.push({ zoneId, anchor });
  }
  return selected;
}

async function attemptSolve(captured, intent, facts, assetBundle, runtime, receipt, rejectedCandidateKeys,
  rejectedStationCandidateKeys, candidateRegion) {
  if (intent.zones.length > PROFILE.maxZones || intent.placements.length > PROFILE.maxPlacements || intent.nodeContexts.length > PROFILE.maxNodeContexts || runtime.nodes.some((node) => node.actions.length > PROFILE.maxActionsPerNode)) return rejected("PROTOTYPE_SPATIAL_SOLVER_PROFILE_UNSUPPORTED");
  const visiblePlacementIds = new Set(intent.nodeContexts.flatMap((context) => context.visiblePlacementIds));
  if (intent.placements.some((placement) => !visiblePlacementIds.has(placement.id))) {
    return rejected("PROTOTYPE_SPATIAL_SOLVER_PLACEMENT_NEVER_VISIBLE", "/spatialIntentJson/placements");
  }
  const records = placementRecords(intent, assetBundle);
  if (!records) return rejected("PROTOTYPE_SPATIAL_SOLVER_ASSET_BOUNDS_MISSING", "/assetBundleJson");
  if (!distanceConstraintsFeasible(records)) return rejected("PROTOTYPE_SPATIAL_SOLVER_NO_SOLUTION");
  const orderedZones = zoneOrder(intent, runtime);
  if (!orderedZones) return rejected("PROTOTYPE_SPATIAL_SOLVER_ENTRY_ZONE_INVALID", "/spatialIntentJson");
  const counts = actionCounts(runtime);
  const zoneWeights = zoneCapacityWeights(intent, records, counts);
  const components = componentCandidates(facts, intent, zoneWeights, candidateRegion);
  if (components.length === 0) return rejected("PROTOTYPE_SPATIAL_SOLVER_COMPONENT_CAPACITY_INSUFFICIENT", "/environmentFactsJson/navigationMesh/components");
  let sawSearchLimit = false;
  for (const entry of components) {
    const initialSeeds = selectSeeds(entry.anchors, orderedZones, candidateRegion);
    const seeds = initialSeeds && stationCapableSeeds(facts, entry.anchors, orderedZones, initialSeeds,
      intent, counts, rejectedStationCandidateKeys, candidateRegion);
    if (!seeds) continue;
    const partitionedDomains = buildDomains(facts, entry.component, entry.anchors, seeds, orderedZones, zoneWeights);
    if (!partitionedDomains) continue;
    const domains = expandDomainsForStationAccess(facts, entry.component, entry.anchors, partitionedDomains,
      orderedZones, intent, counts);
    const stationCandidates = new Map();
    let stationsValid = true;
    for (const zoneId of orderedZones) {
      const contexts = intent.nodeContexts.filter((context) => context.zoneId === zoneId);
      const candidates = stationCandidatesForZone(zoneId, facts, domains.get(zoneId), entry.anchors,
        seeds.find((item) => item.zoneId === zoneId).anchor, contexts, counts, rejectedStationCandidateKeys,
        candidateRegion).filter((station) => stationCanReachRequiredWallPlacements(station, zoneId,
        records, domains.get(zoneId), facts, candidateRegion));
      if (candidates.length === 0) { stationsValid = false; break; }
      stationCandidates.set(zoneId, candidates);
    }
    if (!stationsValid) continue;
    const search = searchStationsAndPlacements(orderedZones, stationCandidates, records, domains, facts, entry.anchors,
      rejectedCandidateKeys, candidateRegion);
    if (search.limitExceeded) { sawSearchLimit = true; continue; }
    if (!search.solved) continue;
    const stations = search.stations;
    const intentSha = await sha256Text(captured.spatialIntentJson);
    const factsSha = await sha256Text(captured.environmentFactsJson);
    const receiptSha = await sha256Text(captured.runtimeReceiptJson);
    const assetBundleSha = await sha256Text(captured.assetBundleJson);
    const solution = {
      format: "matrix-oasis.prototype-spatial-solution",
      formatVersion: "0.1.0",
      canonicalization: "matrix-oasis.canonical-json/1",
      source: {
        spatialIntent: { format: intent.format, formatVersion: intent.formatVersion, canonicalSha256: intentSha },
        environmentFacts: { format: facts.format, formatVersion: facts.formatVersion, canonicalSha256: factsSha },
        runtime: { format: runtime.format, formatVersion: runtime.formatVersion, id: runtime.source.id, contentVersion: runtime.source.contentVersion, sourceSha256: `sha256:${runtime.source.canonicalSha256}`, artifactSha256: `sha256:${receipt.artifact.sha256}` },
        runtimeReceiptSha256: receiptSha,
        assetBundle: { format: assetBundle.format, formatVersion: assetBundle.formatVersion, canonicalSha256: assetBundleSha },
        analysisTransformSource: {
          profile: facts.source.analysisTransform.profile,
          format: facts.source.analysisTransform.profile === "spatial-assembly-collider-v1" ? "matrix-oasis.prototype-spatial-assembly" : "matrix-oasis.prototype-spatial-environment-bundle",
          formatVersion: "0.1.0",
          canonicalSha256: facts.source.analysisTransform.sourceCanonicalSha256,
        },
      },
      profile: buildProfile(),
      navigation: {
        componentIndex: entry.component.index,
        zoneSeeds: intent.zones.map((zone) => ({ zoneId: zone.id, floorAnchorId: seeds.find((seed) => seed.zoneId === zone.id).anchor.id })),
        zoneDomains: intent.zones.map((zone) => ({ zoneId: zone.id, componentIndex: entry.component.index, floorAnchorIds: domains.get(zone.id).map((anchor) => anchor.id) })),
      },
      placements: finalizedPlacements(intent, records, search.selectedById, domains, stations),
      nodeContexts: nodeSolutions(intent, runtime, stations),
      metrics: { candidateCount: search.candidateCount, expandedStates: search.expandedStates },
      proof: { allHardConstraintsSatisfied: true, singleNavigationComponent: true, allNodeApproachesReachable: true },
    };
    const canonicalSpatialSolutionJson = canonicalizeJsonValue(solution);
    if (!validReport(validatePrototypeSpatialSolutionJson(canonicalSpatialSolutionJson))) throw new Error("solution");
    const solutionSha = await sha256Text(canonicalSpatialSolutionJson);
    const reportValue = {
      format: "matrix-oasis.prototype-spatial-solution-report",
      formatVersion: "0.1.0",
      source: { spatialIntentSha256: intentSha, environmentFactsSha256: factsSha, runtimeReceiptSha256: receiptSha, assetBundleSha256: assetBundleSha },
      solutionSha256: solutionSha,
      componentIndex: entry.component.index,
      zoneCount: intent.zones.length,
      placementCount: intent.placements.length,
      nodeContextCount: intent.nodeContexts.length,
      candidateCount: search.candidateCount,
      expandedStates: search.expandedStates,
      deterministic: true,
    };
    const canonicalSpatialSolutionReportJson = canonicalizeJsonValue(reportValue);
    return deepFreeze({ ok: true, spatialSolution: JSON.parse(canonicalSpatialSolutionJson), canonicalSpatialSolutionJson, canonicalSpatialSolutionReportJson });
  }
  return rejected(sawSearchLimit ? "PROTOTYPE_SPATIAL_SOLVER_SEARCH_LIMIT_EXCEEDED" : "PROTOTYPE_SPATIAL_SOLVER_NO_SOLUTION");
}

export async function solvePrototypeSpatialLayoutInternal(request, rejectedCandidateKeys = new Set(),
  rejectedStationCandidateKeys = new Set(), candidateRegion = null) {
  if (!(rejectedCandidateKeys instanceof Set) || rejectedCandidateKeys.size > PROFILE.maxSearchStates ||
      [...rejectedCandidateKeys].some((key) => typeof key !== "string") ||
      !(rejectedStationCandidateKeys instanceof Set) || rejectedStationCandidateKeys.size > PROFILE.maxSearchStates ||
      [...rejectedStationCandidateKeys].some((key) => typeof key !== "string") ||
      (candidateRegion !== null && !validCandidateRegion(candidateRegion))) {
    return rejected("PROTOTYPE_SPATIAL_SOLVER_INPUT_INVALID");
  }
  const captured = captureRequest(request);
  if (!captured) return rejected("PROTOTYPE_SPATIAL_SOLVER_INPUT_INVALID");
  if (!validReport(validatePrototypeSpatialIntentJson(captured.spatialIntentJson))) return rejected("PROTOTYPE_SPATIAL_SOLVER_INTENT_INVALID", "/spatialIntentJson");
  if (!validReport(validatePrototypeEnvironmentFactsJson(captured.environmentFactsJson))) return rejected("PROTOTYPE_SPATIAL_SOLVER_FACTS_INVALID", "/environmentFactsJson");
  if (!validReport(validatePrototypeAssetBundleJson(captured.assetBundleJson))) return rejected("PROTOTYPE_SPATIAL_SOLVER_ASSET_BUNDLE_INVALID", "/assetBundleJson");
  const runtimeReport = await validateRuntimeGamePackJson(captured.runtimeGamePackJson, captured.runtimeReceiptJson);
  if (!validReport(runtimeReport)) return rejected("PROTOTYPE_SPATIAL_SOLVER_RUNTIME_INVALID", "/runtimeGamePackJson");
  const intent = parseCanonical(captured.spatialIntentJson);
  const facts = parseCanonical(captured.environmentFactsJson);
  const assetBundle = parseCanonical(captured.assetBundleJson);
  const runtime = parseCanonical(captured.runtimeGamePackJson);
  const receipt = parseCanonical(captured.runtimeReceiptJson);
  if (!intent || !facts || !assetBundle || !runtime || !receipt) return rejected("PROTOTYPE_SPATIAL_SOLVER_INPUT_NON_CANONICAL");
  const assetBundleSha = await sha256Text(captured.assetBundleJson);
  if (!identityMatches(intent, facts, assetBundle, runtime, receipt, assetBundleSha)) return rejected("PROTOTYPE_SPATIAL_SOLVER_IDENTITY_MISMATCH");
  return attemptSolve(captured, intent, facts, assetBundle, runtime, receipt, new Set(rejectedCandidateKeys),
    new Set(rejectedStationCandidateKeys), candidateRegion === null ? null : deepFreeze(structuredClone(candidateRegion)));
}
