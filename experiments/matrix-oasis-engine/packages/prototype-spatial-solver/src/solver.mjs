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
    player: { radiusMm: PROFILE.playerRadiusMm, heightMm: PROFILE.playerHeightMm, floorSnapMm: PROFILE.floorSnapMm },
    clearanceMm: { compact: PROFILE.compactClearanceMm, human: PROFILE.humanClearanceMm, large: PROFILE.largeClearanceMm },
    terminal: {
      widthMm: PROFILE.terminalWidthMm,
      depthMm: PROFILE.terminalDepthMm,
      columns: PROFILE.terminalColumns,
      columnSpacingMm: PROFILE.terminalColumnSpacingMm,
      rowSpacingMm: PROFILE.terminalRowSpacingMm,
      originZMm: PROFILE.terminalOriginZMm,
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
    output.push({ item, bounds, declarationIndex: index, clearanceMm: CLEARANCE[item.clearanceClass] });
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

function terminalLayout(actionCount) {
  const columns = Math.max(1, Math.min(PROFILE.terminalColumns, actionCount));
  const rows = Math.max(1, Math.ceil(actionCount / PROFILE.terminalColumns));
  return {
    widthMm: PROFILE.terminalWidthMm,
    depthMm: PROFILE.terminalDepthMm,
    layoutWidthMm: PROFILE.terminalWidthMm + ((columns - 1) * PROFILE.terminalColumnSpacingMm),
    layoutDepthMm: PROFILE.terminalDepthMm + ((rows - 1) * PROFILE.terminalRowSpacingMm),
    layoutCenterOffsetMm: [0, PROFILE.terminalOriginZMm - (((rows - 1) * PROFILE.terminalRowSpacingMm) / 2)],
  };
}

function componentCandidates(facts, intent, placements, counts) {
  const requiredArea = placements.reduce((total, item) => {
    const width = item.bounds.widthMm + (2 * item.clearanceMm);
    const depth = item.bounds.depthMm + (2 * item.clearanceMm);
    return total + BigInt(width * depth);
  }, 0n) + intent.zones.reduce((total, zone) => {
    const contexts = intent.nodeContexts.filter((item) => item.zoneId === zone.id);
    const maxActions = Math.max(0, ...contexts.map((item) => counts.get(item.nodeId) ?? 0));
    const layout = terminalLayout(maxActions);
    return total + BigInt(layout.layoutWidthMm * layout.layoutDepthMm) + BigInt(Math.ceil(Math.PI * PROFILE.playerRadiusMm * PROFILE.playerRadiusMm));
  }, 0n);
  return facts.navigationMesh.components.map((component) => {
    const anchors = facts.floorAnchors.filter((anchor) => anchor.componentIndex === component.index);
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

function selectSeeds(anchors, orderedZones) {
  const seeds = [];
  for (let index = 0; index < orderedZones.length; index += 1) {
    let selected;
    if (index === 0) selected = medoid(anchors);
    else {
      selected = anchors.filter((anchor) => !seeds.some((seed) => seed.anchor.id === anchor.id)).sort((left, right) => {
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

function buildDomains(facts, component, anchors, seeds, orderedZones) {
  const graph = polygonGraph(facts, component);
  const distances = new Map(seeds.map((seed) => [seed.zoneId, polygonDistances(graph, seed.anchor.polygonIndex)]));
  const domains = new Map(orderedZones.map((zoneId) => [zoneId, []]));
  for (const anchor of anchors.slice().sort((left, right) => compareText(left.id, right.id))) {
    const selected = seeds.slice().sort((left, right) => {
      const leftGraph = distances.get(left.zoneId).get(anchor.polygonIndex);
      const rightGraph = distances.get(right.zoneId).get(anchor.polygonIndex);
      const leftScore = (Number.isFinite(leftGraph) ? leftGraph : Number.MAX_SAFE_INTEGER) + (left.anchor.polygonIndex === anchor.polygonIndex ? xzDistance(left.anchor.positionMm, anchor.positionMm) : 0);
      const rightScore = (Number.isFinite(rightGraph) ? rightGraph : Number.MAX_SAFE_INTEGER) + (right.anchor.polygonIndex === anchor.polygonIndex ? xzDistance(right.anchor.positionMm, anchor.positionMm) : 0);
      return (leftScore - rightScore) || (orderedZones.indexOf(left.zoneId) - orderedZones.indexOf(right.zoneId));
    })[0];
    domains.get(selected.zoneId).push(anchor);
  }
  return [...domains.values()].some((items) => items.length === 0) ? null : domains;
}

function domainBounds(anchors) {
  const xs = anchors.map((item) => item.positionMm[0]);
  const ys = anchors.map((item) => item.positionMm[1]);
  const zs = anchors.map((item) => item.positionMm[2]);
  return { minimum: [Math.min(...xs) - 500, Math.min(...ys), Math.min(...zs) - 500], maximum: [Math.max(...xs) + 500, Math.max(...ys), Math.max(...zs) + 500] };
}

function rectangleFits(position, width, depth, bounds, yawQuarterTurns = 0) {
  const actualWidth = yawQuarterTurns % 2 === 0 ? width : depth;
  const actualDepth = yawQuarterTurns % 2 === 0 ? depth : width;
  return position[0] - (actualWidth / 2) >= bounds.minimum[0] && position[0] + (actualWidth / 2) <= bounds.maximum[0] &&
    position[2] - (actualDepth / 2) >= bounds.minimum[2] && position[2] + (actualDepth / 2) <= bounds.maximum[2];
}

function rotatedLayoutCenter(origin, offset, quarterTurns) {
  const [localX, localZ] = offset;
  const normalized = ((quarterTurns % 4) + 4) % 4;
  if (normalized === 0) return [origin[0] + localX, origin[1], origin[2] + localZ];
  if (normalized === 1) return [origin[0] + localZ, origin[1], origin[2] - localX];
  if (normalized === 2) return [origin[0] - localX, origin[1], origin[2] - localZ];
  return [origin[0] - localZ, origin[1], origin[2] + localX];
}

function insideRectangle(position, center, width, depth, padding = 0) {
  return Math.abs(position[0] - center[0]) < (width / 2) + padding && Math.abs(position[2] - center[2]) < (depth / 2) + padding;
}

function yawTo(from, to) {
  const degrees = Math.atan2(to[0] - from[0], to[2] - from[2]) * 180 / Math.PI;
  let value = Math.round(degrees * 1000);
  while (value > 180_000) value -= 360_000;
  while (value < -180_000) value += 360_000;
  return value;
}

function stationForZone(zoneId, domain, seed, contexts, counts) {
  const maximumActions = Math.max(0, ...contexts.map((item) => counts.get(item.nodeId) ?? 0));
  const maximumLayout = terminalLayout(maximumActions);
  const bounds = domainBounds(domain);
  const terminalCandidates = [];
  for (const anchor of domain) {
    for (const quarterTurns of [0, 1, 2, 3]) {
      const layoutCenter = rotatedLayoutCenter(anchor.positionMm, maximumLayout.layoutCenterOffsetMm, quarterTurns);
      if (!rectangleFits(layoutCenter, maximumLayout.layoutWidthMm, maximumLayout.layoutDepthMm, bounds, quarterTurns)) continue;
      const width = quarterTurns % 2 === 0 ? maximumLayout.layoutWidthMm : maximumLayout.layoutDepthMm;
      const depth = quarterTurns % 2 === 0 ? maximumLayout.layoutDepthMm : maximumLayout.layoutWidthMm;
      if (insideRectangle(anchor.positionMm, layoutCenter, width, depth, PROFILE.playerRadiusMm)) continue;
      terminalCandidates.push({ anchor, approach: anchor, quarterTurns, width, depth, layoutCenter });
    }
  }
  terminalCandidates.sort((left, right) => squaredDistance(left.anchor.positionMm, seed.positionMm) - squaredDistance(right.anchor.positionMm, seed.positionMm) || left.quarterTurns - right.quarterTurns || compareText(left.anchor.id, right.anchor.id) || compareText(left.approach.id, right.approach.id));
  const selected = terminalCandidates[0];
  if (!selected) return null;
  return {
    zoneId,
    terminalAnchor: selected.anchor,
    approachAnchor: selected.approach,
    spawnAnchor: selected.approach,
    yawMilliDegrees: selected.quarterTurns === 3 ? -90_000 : selected.quarterTurns * 90_000,
    reservedTerminal: { center: selected.layoutCenter, widthMm: selected.width, depthMm: selected.depth, clearanceMm: PROFILE.playerRadiusMm },
    reservedSpawn: { center: selected.approach.positionMm, radiusMm: PROFILE.playerRadiusMm },
  };
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

function circleRadius(bounds) {
  return Math.ceil(Math.hypot(bounds.widthMm, bounds.depthMm) / 2);
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

function generateCandidates(record, domains, facts, stations, selectedById) {
  const domain = domains.get(record.item.zoneId);
  const station = stations.get(record.item.zoneId);
  const zoneCenter = centerOfDomain(domain);
  const candidates = [];
  if (record.item.support === "floor") {
    const anchors = domain.slice().sort((left, right) => {
      const delta = squaredDistance(left.positionMm, zoneCenter) - squaredDistance(right.positionMm, zoneCenter);
      return (record.item.anchor === "edge" ? -delta : record.item.anchor === "center" ? delta : 0) || compareText(left.id, right.id);
    });
    const bounds = domainBounds(domain);
    for (const anchor of anchors) {
      const position = candidatePosition(record, anchor);
      if (!rectangleFits(position, record.bounds.widthMm + (2 * record.clearanceMm), record.bounds.depthMm + (2 * record.clearanceMm), bounds)) continue;
      candidates.push({ anchorKind: "floor", anchorId: anchor.id, positionMm: position, radiusMm: circleRadius(record.bounds), record });
    }
  } else {
    const floorIds = new Set(domain.map((item) => item.id));
    const walls = facts.wallAnchors.filter((wall) => floorIds.has(wall.nearestFloorAnchorId) && wall.availableWidthMm >= record.bounds.widthMm && wall.availableHeightMm >= record.bounds.heightMm)
      .sort((left, right) => compareText(left.id, right.id));
    for (const wall of walls) {
      const floor = domain.find((anchor) => anchor.id === wall.nearestFloorAnchorId);
      const position = candidatePosition(record, floor, wall);
      candidates.push({ anchorKind: "wall", anchorId: wall.id, positionMm: position, radiusMm: circleRadius(record.bounds), record });
    }
  }
  return candidates.filter((candidate) => {
    if (insideRectangle(candidate.positionMm, station.reservedTerminal.center, station.reservedTerminal.widthMm, station.reservedTerminal.depthMm, candidate.radiusMm + record.clearanceMm)) return false;
    return xzDistance(candidate.positionMm, station.reservedSpawn.center) >= candidate.radiusMm + record.clearanceMm + station.reservedSpawn.radiusMm;
  }).slice(0, PROFILE.maxCandidatesPerItem).map((candidate) => ({
    ...candidate,
    yawMilliDegrees: candidateYaw(record.item, candidate.positionMm, zoneCenter, selectedById),
  }));
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

function searchPlacements(records, domains, facts, stations) {
  const ordered = placementOrder(records);
  const selected = [];
  const selectedById = new Map();
  let expandedStates = 0;
  let candidateCount = 0;
  let limitExceeded = false;
  function visit(index) {
    if (index === ordered.length) return selected.every((candidate) => constraintsSatisfied(candidate, selectedById, true));
    const record = ordered[index];
    const candidates = generateCandidates(record, domains, facts, stations, selectedById);
    candidateCount += candidates.length;
    for (const candidate of candidates) {
      expandedStates += 1;
      if (expandedStates > PROFILE.maxSearchStates) { limitExceeded = true; return false; }
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

function finalizedPlacements(intent, records, selectedById, domains) {
  const byId = new Map(records.map((record) => [record.item.id, record]));
  return intent.placements.map((item) => {
    const candidate = selectedById.get(item.id);
    const record = byId.get(item.id);
    const yaw = candidateYaw(item, candidate.positionMm, centerOfDomain(domains.get(item.zoneId)), selectedById);
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
    const footprint = terminalLayout(actionCount);
    return {
      nodeId: context.nodeId,
      zoneId: context.zoneId,
      visiblePlacementIds: context.visiblePlacementIds,
      playerSpawn: { floorAnchorId: station.spawnAnchor.id, positionMm: station.spawnAnchor.positionMm, yawMilliDegrees: station.yawMilliDegrees },
      actionTerminal: { floorAnchorId: station.terminalAnchor.id, approachFloorAnchorId: station.approachAnchor.id, positionMm: station.terminalAnchor.positionMm, yawMilliDegrees: station.yawMilliDegrees, actionCount, footprint },
      approachPathFloorAnchorIds: [station.approachAnchor.id],
    };
  });
}

async function attemptSolve(captured, intent, facts, assetBundle, runtime, receipt) {
  if (intent.zones.length > PROFILE.maxZones || intent.placements.length > PROFILE.maxPlacements || intent.nodeContexts.length > PROFILE.maxNodeContexts || runtime.nodes.some((node) => node.actions.length > PROFILE.maxActionsPerNode)) return rejected("PROTOTYPE_SPATIAL_SOLVER_PROFILE_UNSUPPORTED");
  const records = placementRecords(intent, assetBundle);
  if (!records) return rejected("PROTOTYPE_SPATIAL_SOLVER_ASSET_BOUNDS_MISSING", "/assetBundleJson");
  const orderedZones = zoneOrder(intent, runtime);
  if (!orderedZones) return rejected("PROTOTYPE_SPATIAL_SOLVER_ENTRY_ZONE_INVALID", "/spatialIntentJson");
  const counts = actionCounts(runtime);
  const components = componentCandidates(facts, intent, records, counts);
  if (components.length === 0) return rejected("PROTOTYPE_SPATIAL_SOLVER_COMPONENT_CAPACITY_INSUFFICIENT", "/environmentFactsJson/navigationMesh/components");
  let sawSearchLimit = false;
  for (const entry of components) {
    const seeds = selectSeeds(entry.anchors, orderedZones);
    if (!seeds) continue;
    const domains = buildDomains(facts, entry.component, entry.anchors, seeds, orderedZones);
    if (!domains) continue;
    const stations = new Map();
    let stationsValid = true;
    for (const zoneId of orderedZones) {
      const contexts = intent.nodeContexts.filter((context) => context.zoneId === zoneId);
      const station = stationForZone(zoneId, domains.get(zoneId), seeds.find((item) => item.zoneId === zoneId).anchor, contexts, counts);
      if (!station) { stationsValid = false; break; }
      stations.set(zoneId, station);
    }
    if (!stationsValid) continue;
    const search = searchPlacements(records, domains, facts, stations);
    if (search.limitExceeded) { sawSearchLimit = true; continue; }
    if (!search.solved) continue;
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
      placements: finalizedPlacements(intent, records, search.selectedById, domains),
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

export async function solvePrototypeSpatialLayoutInternal(request) {
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
  return attemptSolve(captured, intent, facts, assetBundle, runtime, receipt);
}
