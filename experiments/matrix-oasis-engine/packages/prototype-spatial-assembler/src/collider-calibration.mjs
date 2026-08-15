import { NodeIO, getBounds } from "@gltf-transform/core";
import {
  MemoryReadFileSystem,
  createChunkDataPool,
  readFile,
} from "@playcanvas/splat-transform";

const TARGET_FLOOR_SPAN_METERS = 30;
const LEGACY_MAX_HORIZONTAL_SPAN_METERS = 90;
const OFFICIAL_METRIC_MAX_HORIZONTAL_SPAN_METERS = 128;
const TARGET_FLOOR_SPAN_MILLIMETERS = 30_000;
const LEGACY_MAX_HORIZONTAL_SPAN_MILLIMETERS = 90_000;
const OFFICIAL_METRIC_MAX_HORIZONTAL_SPAN_MILLIMETERS = 128_000;
const PLAYER_COLLISION_DIAMETER_MILLIMETERS = 700;
const SAFETY_FLOOR_THICKNESS_MILLIMETERS = 200;
const PLACEMENT_GRID_MILLIMETERS = 1000;
const PLACEMENT_BOUNDARY_CLEARANCE_MILLIMETERS = 1000;
const PLACEMENT_OBSTACLE_CLEARANCE_MILLIMETERS = 700;
const PLACEMENT_MINIMUM_SEPARATION_MILLIMETERS = 1800;
const ASSET_PLACEMENT_MINIMUM_SEPARATION_MILLIMETERS = 1000;
const ASSET_INTERACTION_MINIMUM_SEPARATION_MILLIMETERS = 1200;
const PLACEMENT_FLOOR_LIMIT_MILLIMETERS = 1500;
const PLAYER_CAPSULE_HALF_HEIGHT_MILLIMETERS = 900;
const PLAYER_HEADROOM_CLEARANCE_MILLIMETERS = 100;
const PLAYER_MAX_STEP_MILLIMETERS = 450;
const PLAYER_NAVIGATION_HEIGHT_MILLIMETERS = 1000;
const SPLAT_DENSITY_CELL_MILLIMETERS = 500;
const SPLAT_DENSITY_RADIUS_MILLIMETERS = 2000;
const SPLAT_DENSITY_VERTICAL_MINIMUM_MILLIMETERS = 200;
const SPLAT_DENSITY_VERTICAL_MAXIMUM_MILLIMETERS = 3200;
const SPLAT_DENSITY_MINIMUM_POINTS = 1024;
const SPLAT_DENSITY_PEAK_THRESHOLD_PERMILLE = 10;
const ACTION_TERMINAL_COLUMN_COUNT = 8;
const ACTION_TERMINAL_COLUMN_SPACING_MILLIMETERS = 1700;
const ACTION_TERMINAL_ROW_SPACING_MILLIMETERS = 2250;
const ACTION_TERMINAL_ORIGIN_Z_MILLIMETERS = -2400;
const ACTION_TERMINAL_HALF_WIDTH_MILLIMETERS = 625;
const ACTION_TERMINAL_HALF_DEPTH_MILLIMETERS = 250;
const SPLAT_INPUT_NAME = "environment.compressed.ply";
const CARDINAL_YAWS_MILLI_DEGREES = Object.freeze([0, 90_000, -90_000, 180_000]);

function transformPoint(matrix, x, y, z) {
  return [
    matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
    matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
    matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
  ];
}

function floorAt(triangle, x, z) {
  const [a, b, c] = triangle;
  const denominator = (b[2] - c[2]) * (a[0] - c[0]) +
    (c[0] - b[0]) * (a[2] - c[2]);
  if (Math.abs(denominator) <= 1e-9) return null;
  const weightA = ((b[2] - c[2]) * (x - c[0]) +
    (c[0] - b[0]) * (z - c[2])) / denominator;
  const weightB = ((c[2] - a[2]) * (x - c[0]) +
    (a[0] - c[0]) * (z - c[2])) / denominator;
  const weightC = 1 - weightA - weightB;
  return weightA >= -1e-7 && weightB >= -1e-7 && weightC >= -1e-7
    ? weightA * a[1] + weightB * b[1] + weightC * c[1]
    : null;
}

function millimeters(vector) {
  const output = vector.map((value) => {
    const rounded = Math.round(value * 1000);
    return Object.is(rounded, -0) ? 0 : rounded;
  });
  return output.every(Number.isSafeInteger) ? output : null;
}

function calibratedPoint(matrix, positions, id, scaleMicros, translationMm) {
  const source = transformPoint(matrix, positions[id * 3], positions[id * 3 + 1], positions[id * 3 + 2]);
  const output = source.map((value, index) =>
    Math.round(value * scaleMicros / 1000) + translationMm[index]);
  return output.every(Number.isSafeInteger) ? output : null;
}

function projectedDistance(point, edge) {
  const [startX, startZ, endX, endZ] = edge;
  const deltaX = endX - startX;
  const deltaZ = endZ - startZ;
  const lengthSquared = deltaX * deltaX + deltaZ * deltaZ;
  if (lengthSquared === 0) return Math.hypot(point[0] - startX, point[1] - startZ);
  const amount = Math.max(0, Math.min(1,
    ((point[0] - startX) * deltaX + (point[1] - startZ) * deltaZ) / lengthSquared));
  return Math.hypot(
    point[0] - (startX + amount * deltaX),
    point[1] - (startZ + amount * deltaZ),
  );
}

function segmentDistance(left, right) {
  const cross = (ax, az, bx, bz, cx, cz) =>
    (bx - ax) * (cz - az) - (bz - az) * (cx - ax);
  const a = [left[0], left[1]];
  const b = [left[2], left[3]];
  const c = [right[0], right[1]];
  const d = [right[2], right[3]];
  const abC = cross(...a, ...b, ...c);
  const abD = cross(...a, ...b, ...d);
  const cdA = cross(...c, ...d, ...a);
  const cdB = cross(...c, ...d, ...b);
  if (((abC <= 0 && abD >= 0) || (abC >= 0 && abD <= 0)) &&
      ((cdA <= 0 && cdB >= 0) || (cdA >= 0 && cdB <= 0))) return 0;
  return Math.min(
    projectedDistance(a, right),
    projectedDistance(b, right),
    projectedDistance(c, left),
    projectedDistance(d, left),
  );
}

function calibratedBounds(alignment) {
  const minimum = alignment.colliderBoundsMm.minimumMm.map((value, index) =>
    Math.round(value * alignment.colliderScaleMicros / 1_000_000) + alignment.colliderLocalTranslationMm[index]);
  const maximum = alignment.colliderBoundsMm.maximumMm.map((value, index) =>
    Math.round(value * alignment.colliderScaleMicros / 1_000_000) + alignment.colliderLocalTranslationMm[index]);
  return [...minimum, ...maximum].every(Number.isSafeInteger) &&
    minimum.every((value, index) => value < maximum[index])
    ? { minimum, maximum }
    : null;
}

function calibratedSplatBounds(statistics, metricScaleMicros, splatAlignment) {
  const mean = statistics?.sourceMeanMm;
  const bounds = splatAlignment?.splatBoundsMm;
  if (!Array.isArray(mean) || mean.length !== 3 || !mean.every(Number.isSafeInteger) ||
      !Number.isSafeInteger(metricScaleMicros) || metricScaleMicros < 1 ||
      !Array.isArray(bounds?.minimumMm) || !Array.isArray(bounds?.maximumMm) ||
      bounds.minimumMm.length !== 3 || bounds.maximumMm.length !== 3 ||
      ![...bounds.minimumMm, ...bounds.maximumMm].every(Number.isSafeInteger) ||
      !Number.isSafeInteger(splatAlignment?.splatScaleMicros) ||
      !Array.isArray(splatAlignment?.splatLocalTranslationMm) ||
      splatAlignment.splatLocalTranslationMm.length !== 3 ||
      !splatAlignment.splatLocalTranslationMm.every(Number.isSafeInteger)) return null;
  const godotMean = [mean[0], -mean[1], -mean[2]];
  const minimum = bounds.minimumMm.map((value, index) =>
    Math.round((value - godotMean[index]) * splatAlignment.splatScaleMicros /
      metricScaleMicros) + splatAlignment.splatLocalTranslationMm[index]);
  const maximum = bounds.maximumMm.map((value, index) =>
    Math.round((value - godotMean[index]) * splatAlignment.splatScaleMicros /
      metricScaleMicros) + splatAlignment.splatLocalTranslationMm[index]);
  return [...minimum, ...maximum].every(Number.isSafeInteger) &&
    minimum.every((value, index) => value < maximum[index])
    ? { minimum, maximum }
    : null;
}

function densityBinKey(x, y, z) {
  const offset = 4096;
  const shifted = [x + offset, y + offset, z + offset];
  return shifted.every((value) => Number.isSafeInteger(value) && value >= 0 && value < 8192)
    ? shifted[0] * 67_108_864 + shifted[1] * 8192 + shifted[2]
    : null;
}

async function createSplatDensityScorer(bytes, splatAlignment) {
  let sources = [];
  let pool = null;
  try {
    if (!(bytes instanceof Uint8Array) ||
        splatAlignment?.profile !== "splat-opencv-to-godot-official-metric-v4" ||
        !Number.isSafeInteger(splatAlignment.splatScaleMicros) ||
        !Array.isArray(splatAlignment.splatLocalTranslationMm) ||
        splatAlignment.splatLocalTranslationMm.length !== 3 ||
        !splatAlignment.splatLocalTranslationMm.every(Number.isSafeInteger)) return null;
    const fileSystem = new MemoryReadFileSystem();
    fileSystem.set(SPLAT_INPUT_NAME, bytes);
    sources = await readFile({
      filename: SPLAT_INPUT_NAME,
      inputFormat: "ply",
      fileSystem,
      options: {},
    });
    if (sources.length !== 1) return null;
    const source = sources[0];
    const count = source.meta.numGaussians;
    const layout = source.meta.layouts.position;
    if (!layout || !Number.isSafeInteger(count) || count < 1 || count > 640_000) return null;
    if (count < SPLAT_DENSITY_MINIMUM_POINTS) {
      return Object.freeze({ enabled: false, score: () => Number.MAX_SAFE_INTEGER });
    }
    pool = createChunkDataPool({
      chunkSize: source.meta.chunkSize,
      maxPooledBytes: 128 * 1024 * 1024,
    });
    const positions = new Float32Array(count * 3);
    let offset = 0;
    for (let chunkIndex = 0; chunkIndex < source.meta.numChunks[0]; chunkIndex += 1) {
      const chunkCount = Math.min(source.meta.chunkSize, count - offset);
      const position = pool.acquire("position", layout, chunkCount);
      try {
        await source.read({ lod: 0, chunkIndex, position });
        const values = position.field("position");
        if (values.length !== chunkCount * 3) return null;
        positions.set(values, offset * 3);
      } finally {
        position.release();
      }
      offset += chunkCount;
    }
    if (offset !== count) return null;
    const mean = [0, 0, 0];
    for (let index = 0; index < count; index += 1) {
      for (let axis = 0; axis < 3; axis += 1) {
        const value = positions[index * 3 + axis];
        if (!Number.isFinite(value)) return null;
        mean[axis] += value;
      }
    }
    for (let axis = 0; axis < 3; axis += 1) mean[axis] /= count;
    const scale = splatAlignment.splatScaleMicros / 1_000_000;
    const translation = splatAlignment.splatLocalTranslationMm;
    const bins = new Map();
    for (let index = 0; index < count; index += 1) {
      const point = [
        (positions[index * 3] - mean[0]) * scale * 1000 + translation[0],
        -(positions[index * 3 + 1] - mean[1]) * scale * 1000 + translation[1],
        -(positions[index * 3 + 2] - mean[2]) * scale * 1000 + translation[2],
      ];
      const coordinate = point.map((value) =>
        Math.floor(value / SPLAT_DENSITY_CELL_MILLIMETERS));
      const key = densityBinKey(...coordinate);
      if (key !== null) bins.set(key, (bins.get(key) ?? 0) + 1);
    }
    const score = (positionMm) => {
      const [x, floor, z] = positionMm;
      const cell = SPLAT_DENSITY_CELL_MILLIMETERS;
      const firstX = Math.floor((x - SPLAT_DENSITY_RADIUS_MILLIMETERS) / cell);
      const lastX = Math.floor((x + SPLAT_DENSITY_RADIUS_MILLIMETERS) / cell);
      const firstY = Math.floor((floor + SPLAT_DENSITY_VERTICAL_MINIMUM_MILLIMETERS) / cell);
      const lastY = Math.floor((floor + SPLAT_DENSITY_VERTICAL_MAXIMUM_MILLIMETERS) / cell);
      const firstZ = Math.floor((z - SPLAT_DENSITY_RADIUS_MILLIMETERS) / cell);
      const lastZ = Math.floor((z + SPLAT_DENSITY_RADIUS_MILLIMETERS) / cell);
      let total = 0;
      for (let binX = firstX; binX <= lastX; binX += 1) {
        const nearestX = Math.max(binX * cell, Math.min(x, (binX + 1) * cell));
        for (let binZ = firstZ; binZ <= lastZ; binZ += 1) {
          const nearestZ = Math.max(binZ * cell, Math.min(z, (binZ + 1) * cell));
          if ((nearestX - x) ** 2 + (nearestZ - z) ** 2 >
              SPLAT_DENSITY_RADIUS_MILLIMETERS ** 2) continue;
          for (let binY = firstY; binY <= lastY; binY += 1) {
            const key = densityBinKey(binX, binY, binZ);
            if (key !== null) total += bins.get(key) ?? 0;
          }
        }
      }
      return total;
    };
    return Object.freeze({ enabled: true, score });
  } catch {
    return null;
  } finally {
    for (const source of sources) {
      try { await source.close(); } catch { /* The caller receives a static failure. */ }
    }
    if (pool) pool.destroy();
  }
}

function terminalGridFits(
  candidate,
  actionCount,
  yawMilliDegrees,
  bounds,
  blocked,
  selectedCandidates,
  playerCandidate = null,
  pathBlocked = null,
) {
  if (!Number.isSafeInteger(actionCount) || actionCount < 1 || actionCount > 64) return false;
  const yaw = yawMilliDegrees / 1000 * Math.PI / 180;
  const cosine = Math.cos(yaw);
  const sine = Math.sin(yaw);
  for (let index = 0; index < actionCount; index += 1) {
    const row = Math.floor(index / ACTION_TERMINAL_COLUMN_COUNT);
    const rowCount = Math.min(ACTION_TERMINAL_COLUMN_COUNT,
      actionCount - row * ACTION_TERMINAL_COLUMN_COUNT);
    const column = index % ACTION_TERMINAL_COLUMN_COUNT;
    const centerX = (column - (rowCount - 1) / 2) * ACTION_TERMINAL_COLUMN_SPACING_MILLIMETERS;
    const centerZ = ACTION_TERMINAL_ORIGIN_Z_MILLIMETERS -
      row * ACTION_TERMINAL_ROW_SPACING_MILLIMETERS;
    const worldCenter = [
      candidate.positionMm[0] + centerX * cosine - centerZ * sine,
      candidate.positionMm[2] + centerX * sine + centerZ * cosine,
    ];
    if (blocked(...worldCenter) || !selectedCandidates.some((floorCandidate) =>
      (floorCandidate.positionMm[0] - worldCenter[0]) ** 2 +
      (floorCandidate.positionMm[2] - worldCenter[1]) ** 2 <=
        PLACEMENT_GRID_MILLIMETERS ** 2) ||
      (playerCandidate && pathBlocked && pathBlocked(
        [playerCandidate.positionMm[0], playerCandidate.positionMm[2]],
        worldCenter,
      )) ||
      (playerCandidate && (playerCandidate.positionMm[0] - worldCenter[0]) ** 2 +
        (playerCandidate.positionMm[2] - worldCenter[1]) ** 2 <
          PLACEMENT_MINIMUM_SEPARATION_MILLIMETERS ** 2)) return false;
    for (const deltaX of [-ACTION_TERMINAL_HALF_WIDTH_MILLIMETERS,
      ACTION_TERMINAL_HALF_WIDTH_MILLIMETERS]) {
      for (const deltaZ of [-ACTION_TERMINAL_HALF_DEPTH_MILLIMETERS,
        ACTION_TERMINAL_HALF_DEPTH_MILLIMETERS]) {
        const localX = centerX + deltaX;
        const localZ = centerZ + deltaZ;
        const x = candidate.positionMm[0] + localX * cosine - localZ * sine;
        const z = candidate.positionMm[2] + localX * sine + localZ * cosine;
        if (x < bounds.minimum[0] || x > bounds.maximum[0] ||
            z < bounds.minimum[2] || z > bounds.maximum[2] || blocked(x, z)) return false;
      }
    }
  }
  return true;
}

function actionAnchorForTerminalBase(terminalBase, yawMilliDegrees) {
  const yaw = yawMilliDegrees / 1000 * Math.PI / 180;
  const rotatedOffsetX = -ACTION_TERMINAL_ORIGIN_Z_MILLIMETERS * Math.sin(yaw);
  const rotatedOffsetZ = ACTION_TERMINAL_ORIGIN_Z_MILLIMETERS * Math.cos(yaw);
  const positionMm = [
    Math.round(terminalBase.positionMm[0] - rotatedOffsetX),
    terminalBase.positionMm[1],
    Math.round(terminalBase.positionMm[2] - rotatedOffsetZ),
  ];
  return positionMm.every(Number.isSafeInteger) ? { positionMm } : null;
}

export async function deriveColliderWalkableLayout(
  bytes,
  splatBytes,
  alignment,
  splatAlignment,
  metricScaleMicros,
  statistics,
  placements,
  entryPlayerSpawnMm,
  nodeBindings,
  nodeActionCounts,
) {
  try {
    if (!(bytes instanceof Uint8Array) || !(splatBytes instanceof Uint8Array) ||
        !Array.isArray(placements) || placements.length > 6 ||
        !Number.isSafeInteger(alignment?.colliderScaleMicros) ||
        !Array.isArray(alignment?.colliderLocalTranslationMm) ||
        alignment.colliderLocalTranslationMm.length !== 3 ||
        !Array.isArray(entryPlayerSpawnMm) || entryPlayerSpawnMm.length !== 3 ||
        !entryPlayerSpawnMm.every(Number.isSafeInteger) || !Array.isArray(nodeBindings) ||
        nodeBindings.length < 1 || nodeBindings.length > 4096 ||
        !Array.isArray(nodeActionCounts) || nodeActionCounts.length !== nodeBindings.length) return null;
    const actionCountByNodeId = new Map();
    for (const item of nodeActionCounts) {
      if (typeof item?.nodeId !== "string" || actionCountByNodeId.has(item.nodeId) ||
          !Number.isSafeInteger(item.actionCount) || item.actionCount < 1 || item.actionCount > 64) return null;
      actionCountByNodeId.set(item.nodeId, item.actionCount);
    }
    const zoneByKey = new Map();
    const zones = [];
    const bindings = [];
    for (const binding of nodeBindings) {
      const player = binding?.playerSpawn;
      const anchor = binding?.actionAnchor;
      const visiblePlacementIds = binding?.visiblePlacementIds;
      if (typeof binding?.nodeId !== "string" || !Array.isArray(player?.positionMm) ||
          !Array.isArray(anchor?.positionMm) || player.positionMm.length !== 3 ||
          anchor.positionMm.length !== 3 || !player.positionMm.every(Number.isSafeInteger) ||
          !anchor.positionMm.every(Number.isSafeInteger) ||
          !Number.isSafeInteger(player.yawMilliDegrees) ||
          !Number.isSafeInteger(anchor.yawMilliDegrees) ||
          !Array.isArray(visiblePlacementIds) ||
          visiblePlacementIds.some((id, index) =>
            typeof id !== "string" || visiblePlacementIds.indexOf(id) !== index) ||
          !actionCountByNodeId.has(binding.nodeId)) return null;
      const key = `${anchor.positionMm[0]},${anchor.positionMm[2]}`;
      let zone = zoneByKey.get(key);
      if (!zone) {
        zone = {
          original: [anchor.positionMm[0], anchor.positionMm[2]],
          local: [
            anchor.positionMm[0] - entryPlayerSpawnMm[0],
            anchor.positionMm[2] - entryPlayerSpawnMm[2],
          ],
          bindings: [],
        };
        zoneByKey.set(key, zone);
        zones.push(zone);
      }
      const captured = {
        nodeId: binding.nodeId,
        playerSpawn: {
          positionMm: [...player.positionMm],
          yawMilliDegrees: player.yawMilliDegrees,
        },
        actionAnchor: {
          positionMm: [...anchor.positionMm],
          yawMilliDegrees: anchor.yawMilliDegrees,
        },
        actionCount: actionCountByNodeId.get(binding.nodeId),
        visiblePlacementIds: [...visiblePlacementIds],
        zone,
      };
      zone.bindings.push(captured);
      bindings.push(captured);
    }
    if (zones.length < 1 || zones.length > 4) return null;
    const requiredHeadroom = Math.max(...bindings.map((binding) =>
      binding.playerSpawn.positionMm[1])) + PLAYER_CAPSULE_HALF_HEIGHT_MILLIMETERS +
      PLAYER_HEADROOM_CLEARANCE_MILLIMETERS;
    if (!Number.isSafeInteger(requiredHeadroom) || requiredHeadroom < 1000 ||
        requiredHeadroom > 12_000) return null;
    const ids = new Set();
    const requested = placements.map((placement) => {
      const position = placement?.transform?.positionMm;
      if (typeof placement?.id !== "string" || ids.has(placement.id) ||
          !Array.isArray(position) || position.length !== 3 || !position.every(Number.isSafeInteger)) return null;
      ids.add(placement.id);
      return { placementId: placement.id, original: [position[0], position[2]], anchor: [
        position[0] - entryPlayerSpawnMm[0],
        position[2] - entryPlayerSpawnMm[2],
      ] };
    });
    if (requested.some((value) => value === null)) return null;
    const colliderBounds = calibratedBounds(alignment);
    const splatBounds = calibratedSplatBounds(statistics, metricScaleMicros, splatAlignment);
    const sourceEnvelope = deriveWalkableEnvelope(statistics);
    if (!colliderBounds || !splatBounds || !sourceEnvelope) return null;
    // The collider is the geometry authority. Gaussian density is only visual
    // evidence and may be sparse around doors, windows, or low-texture walls;
    // intersecting it with the collider would turn render noise into fake
    // navigation boundaries.
    const bounds = {
      minimum: [...colliderBounds.minimum],
      maximum: [...colliderBounds.maximum],
    };
    if (bounds.minimum.some((value, index) => value >= bounds.maximum[index])) return null;
    const document = await new NodeIO().readBinary(bytes);
    const scenes = document.getRoot().listScenes();
    if (scenes.length !== 1) return null;
    const floorTriangles = [];
    const ceilingTriangles = [];
    const floorSamples = new Map();
    const obstacleEdges = [];
    for (const top of scenes[0].listChildren()) {
      top.traverse((node) => {
        const mesh = node.getMesh();
        if (!mesh) return;
        const matrix = node.getWorldMatrix();
        for (const primitive of mesh.listPrimitives()) {
          if (primitive.getMode() !== 4) continue;
          const position = primitive.getAttribute("POSITION");
          if (!position) continue;
          const positions = position.getArray();
          const indices = primitive.getIndices()?.getArray() ?? null;
          const count = indices ? indices.length : position.getCount();
          for (let index = 0; index + 2 < count; index += 3) {
            const triangleIds = indices
              ? [indices[index], indices[index + 1], indices[index + 2]]
              : [index, index + 1, index + 2];
            const triangle = triangleIds.map((id) => calibratedPoint(
              matrix,
              positions,
              id,
              alignment.colliderScaleMicros,
              alignment.colliderLocalTranslationMm,
            ));
            if (triangle.some((value) => value === null)) return;
            const first = triangle[0];
            const left = triangle[1].map((value, axis) => value - first[axis]);
            const right = triangle[2].map((value, axis) => value - first[axis]);
            const normal = [
              left[1] * right[2] - left[2] * right[1],
              left[2] * right[0] - left[0] * right[2],
              left[0] * right[1] - left[1] * right[0],
            ];
            const length = Math.hypot(...normal);
            if (!Number.isFinite(length) || length < 1) continue;
            const verticalRatio = normal[1] / length;
            const minimumY = Math.min(...triangle.map((value) => value[1]));
            const maximumY = Math.max(...triangle.map((value) => value[1]));
            if (Math.abs(verticalRatio) >= 0.85) {
              const horizontal = {
                triangle,
                minimumX: Math.min(...triangle.map((value) => value[0])),
                maximumX: Math.max(...triangle.map((value) => value[0])),
                minimumZ: Math.min(...triangle.map((value) => value[2])),
                maximumZ: Math.max(...triangle.map((value) => value[2])),
              };
              if (verticalRatio >= 0.85) {
                floorTriangles.push(horizontal);
                const center = [0, 1, 2].map((axis) =>
                  Math.round((triangle[0][axis] + triangle[1][axis] + triangle[2][axis]) / 3));
                if (Math.abs(center[1]) <= PLACEMENT_FLOOR_LIMIT_MILLIMETERS) {
                  const key = `${Math.round(center[0] / PLACEMENT_GRID_MILLIMETERS)},${Math.round(center[2] / PLACEMENT_GRID_MILLIMETERS)}`;
                  const samples = floorSamples.get(key) ?? [];
                  samples.push(center);
                  floorSamples.set(key, samples);
                }
              } else {
                ceilingTriangles.push(horizontal);
              }
            } else if (Math.abs(verticalRatio) <= 0.5 && maximumY >= -250 && minimumY <= 2500) {
              for (let edge = 0; edge < 3; edge += 1) {
                const start = triangle[edge];
                const end = triangle[(edge + 1) % 3];
                if (Math.hypot(end[0] - start[0], end[2] - start[2]) >= 50) {
                  obstacleEdges.push([start[0], start[2], end[0], end[2]]);
                }
              }
            }
          }
        }
      });
    }
    if (floorTriangles.length < 1 || obstacleEdges.length > 250_000) return null;
    const candidates = [];
    const firstX = Math.ceil((bounds.minimum[0] + PLACEMENT_BOUNDARY_CLEARANCE_MILLIMETERS) /
      PLACEMENT_GRID_MILLIMETERS) * PLACEMENT_GRID_MILLIMETERS;
    const lastX = Math.floor((bounds.maximum[0] - PLACEMENT_BOUNDARY_CLEARANCE_MILLIMETERS) /
      PLACEMENT_GRID_MILLIMETERS) * PLACEMENT_GRID_MILLIMETERS;
    const firstZ = Math.ceil((bounds.minimum[2] + PLACEMENT_BOUNDARY_CLEARANCE_MILLIMETERS) /
      PLACEMENT_GRID_MILLIMETERS) * PLACEMENT_GRID_MILLIMETERS;
    const lastZ = Math.floor((bounds.maximum[2] - PLACEMENT_BOUNDARY_CLEARANCE_MILLIMETERS) /
      PLACEMENT_GRID_MILLIMETERS) * PLACEMENT_GRID_MILLIMETERS;
    const candidateKeys = new Set();
    const hasHeadroom = (x, z, floor) => {
      let ceiling = null;
      for (const candidate of ceilingTriangles) {
        if (x < candidate.minimumX || x > candidate.maximumX ||
            z < candidate.minimumZ || z > candidate.maximumZ) continue;
        const sampled = floorAt(candidate.triangle, x, z);
        const value = sampled === null ? null : Math.round(sampled);
        if (value !== null && value > floor && (ceiling === null || value < ceiling)) ceiling = value;
      }
      return ceiling === null || ceiling - Math.max(0, floor) >= requiredHeadroom;
    };
    const blocked = (x, z, clearance = PLACEMENT_OBSTACLE_CLEARANCE_MILLIMETERS) =>
      obstacleEdges.some((edge) =>
        x >= Math.min(edge[0], edge[2]) - clearance &&
        x <= Math.max(edge[0], edge[2]) + clearance &&
        z >= Math.min(edge[1], edge[3]) - clearance &&
        z <= Math.max(edge[1], edge[3]) + clearance &&
        projectedDistance([x, z], edge) < clearance);
    const obstacleClearance = (x, z) => obstacleEdges.length === 0
      ? Number.MAX_SAFE_INTEGER
      : Math.round(Math.min(...obstacleEdges.map((edge) => projectedDistance([x, z], edge))));
    const pathBlocked = (start, end) => obstacleEdges.some((edge) =>
      Math.min(start[0], end[0]) <= Math.max(edge[0], edge[2]) +
        PLAYER_COLLISION_DIAMETER_MILLIMETERS / 2 &&
      Math.max(start[0], end[0]) >= Math.min(edge[0], edge[2]) -
        PLAYER_COLLISION_DIAMETER_MILLIMETERS / 2 &&
      Math.min(start[1], end[1]) <= Math.max(edge[1], edge[3]) +
        PLAYER_COLLISION_DIAMETER_MILLIMETERS / 2 &&
      Math.max(start[1], end[1]) >= Math.min(edge[1], edge[3]) -
        PLAYER_COLLISION_DIAMETER_MILLIMETERS / 2 &&
      segmentDistance([start[0], start[1], end[0], end[1]], edge) <
        PLAYER_COLLISION_DIAMETER_MILLIMETERS / 2);
    for (let z = firstZ; z <= lastZ; z += PLACEMENT_GRID_MILLIMETERS) {
      for (let x = firstX; x <= lastX; x += PLACEMENT_GRID_MILLIMETERS) {
        let floor = null;
        for (const candidate of floorTriangles) {
          if (x < candidate.minimumX || x > candidate.maximumX || z < candidate.minimumZ || z > candidate.maximumZ) continue;
          const sampled = floorAt(candidate.triangle, x, z);
          const value = sampled === null ? null : Math.round(sampled);
          if (value !== null && Math.abs(value) <= PLACEMENT_FLOOR_LIMIT_MILLIMETERS &&
              (floor === null || Math.abs(value) < Math.abs(floor))) floor = value;
        }
        if (floor === null || !hasHeadroom(x, z, floor)) continue;
        if (!blocked(x, z)) {
          candidates.push({
            positionMm: [x, floor, z],
            clearanceMm: obstacleClearance(x, z),
            densityScore: 0,
          });
          candidateKeys.add(`${Math.round(x / PLACEMENT_GRID_MILLIMETERS)},${Math.round(z / PLACEMENT_GRID_MILLIMETERS)}`);
        }
      }
    }
    for (const [key, samples] of floorSamples) {
      if (candidateKeys.has(key)) continue;
      samples.sort((left, right) => Math.abs(left[1]) - Math.abs(right[1]) ||
        left[2] - right[2] || left[0] - right[0]);
      const sample = samples.find((value) => value[0] >= firstX && value[0] <= lastX &&
        value[2] >= firstZ && value[2] <= lastZ && !blocked(value[0], value[2]) &&
        hasHeadroom(value[0], value[2], value[1]));
      if (!sample) continue;
      candidates.push({
        positionMm: [...sample],
        clearanceMm: obstacleClearance(sample[0], sample[2]),
        densityScore: 0,
      });
      candidateKeys.add(key);
    }
    const densityScorer = await createSplatDensityScorer(splatBytes, splatAlignment);
    if (!densityScorer) return null;
    if (densityScorer.enabled) {
      const scores = candidates.map((candidate) => densityScorer.score(candidate.positionMm));
      for (let index = 0; index < candidates.length; index += 1) {
        candidates[index].densityScore = scores[index];
      }
    }
    if (candidates.length < 1) return null;
    const layout = [];
    const distanceSquared = (left, right) =>
      (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2;
    candidates.sort((left, right) =>
      left.positionMm[2] - right.positionMm[2] || left.positionMm[0] - right.positionMm[0] ||
      left.positionMm[1] - right.positionMm[1]);
    const candidateByGrid = new Map();
    for (let index = 0; index < candidates.length; index += 1) {
      candidates[index].graphIndex = index;
      candidateByGrid.set(`${candidates[index].positionMm[0]},${candidates[index].positionMm[2]}`, index);
    }
    const adjacency = candidates.map(() => []);
    for (let index = 0; index < candidates.length; index += 1) {
      const current = candidates[index];
      for (const [deltaX, deltaZ] of [
        [-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1],
      ]) {
        const other = candidateByGrid.get(`${current.positionMm[0] + deltaX * PLACEMENT_GRID_MILLIMETERS},${current.positionMm[2] + deltaZ * PLACEMENT_GRID_MILLIMETERS}`);
        if (other === undefined || other <= index ||
            Math.abs(current.positionMm[1] - candidates[other].positionMm[1]) >
              PLAYER_MAX_STEP_MILLIMETERS ||
            pathBlocked(
              [current.positionMm[0], current.positionMm[2]],
              [candidates[other].positionMm[0], candidates[other].positionMm[2]],
            )) continue;
        adjacency[index].push(other);
        adjacency[other].push(index);
      }
    }
    for (const links of adjacency) links.sort((left, right) => left - right);
    const componentIndexes = [];
    const remainingIndexes = new Set(candidates.map((_, index) => index));
    while (remainingIndexes.size > 0) {
      const first = Math.min(...remainingIndexes);
      remainingIndexes.delete(first);
      const pending = [first];
      const component = [];
      while (pending.length > 0) {
        const current = pending.pop();
        component.push(current);
        for (const other of adjacency[current]) {
          if (remainingIndexes.delete(other)) pending.push(other);
        }
      }
      component.sort((left, right) => left - right);
      componentIndexes.push(component);
    }
    const zoneGroups = zones.map(() => []);
    for (const item of requested) {
      const selectedZone = zones.findIndex((zone) => zone.bindings.some((binding) =>
        binding.visiblePlacementIds.includes(item.placementId)));
      if (selectedZone < 0) return null;
      zoneGroups[selectedZone].push(item);
    }
    const componentData = componentIndexes.map((indexes) => {
      const values = indexes.map((index) => candidates[index]);
      return {
        indexes,
        values,
        center: [
          Math.round(values.reduce((sum, value) => sum + value.positionMm[0], 0) / values.length),
          Math.round(values.reduce((sum, value) => sum + value.positionMm[2], 0) / values.length),
        ],
      };
    });
    const requiredCapacity = requested.length + zones.length * 2;
    const eligibleComponents = componentData.filter((component) =>
      component.values.length >= requiredCapacity);
    eligibleComponents.sort((left, right) =>
      right.values.length - left.values.length ||
      Math.max(...right.values.map((value) => value.clearanceMm)) -
        Math.max(...left.values.map((value) => value.clearanceMm)) ||
      right.values.reduce((sum, value) => sum + value.densityScore, 0) -
        left.values.reduce((sum, value) => sum + value.densityScore, 0) ||
      left.center[1] - right.center[1] || left.center[0] - right.center[0]);
    const selectedComponent = eligibleComponents[0];
    if (!selectedComponent) return null;
    const selectedCandidates = [...selectedComponent.values].sort((left, right) =>
      left.positionMm[2] - right.positionMm[2] || left.positionMm[0] - right.positionMm[0] ||
      left.positionMm[1] - right.positionMm[1]);
    const terminalCandidateLimit = Math.min(128, selectedCandidates.length);
    const terminalCandidates = terminalCandidateLimit === selectedCandidates.length
      ? selectedCandidates
      : Array.from({ length: terminalCandidateLimit }, (_, index) =>
          selectedCandidates[Math.floor(index * (selectedCandidates.length - 1) /
            (terminalCandidateLimit - 1))]);
    const selectedIndexes = new Set(selectedComponent.indexes);
    const graphDistances = (start) => {
      const distances = new Map([[start, 0]]);
      const pending = [start];
      for (let offset = 0; offset < pending.length; offset += 1) {
        const current = pending[offset];
        for (const other of adjacency[current]) {
          if (!selectedIndexes.has(other) || distances.has(other)) continue;
          distances.set(other, distances.get(current) + 1);
          pending.push(other);
        }
      }
      return distances;
    };
    const componentXs = selectedCandidates.map((candidate) => candidate.positionMm[0]);
    const componentZs = selectedCandidates.map((candidate) => candidate.positionMm[2]);
    const minimumX = Math.max(bounds.minimum[0], Math.min(...componentXs) - PLACEMENT_GRID_MILLIMETERS);
    const maximumX = Math.min(bounds.maximum[0], Math.max(...componentXs) + PLACEMENT_GRID_MILLIMETERS);
    const minimumZ = Math.max(bounds.minimum[2], Math.min(...componentZs) - PLACEMENT_GRID_MILLIMETERS);
    const maximumZ = Math.min(bounds.maximum[2], Math.max(...componentZs) + PLACEMENT_GRID_MILLIMETERS);
    const componentBounds = {
      minimum: [minimumX, bounds.minimum[1], minimumZ],
      maximum: [maximumX, bounds.maximum[1], maximumZ],
    };
    const occupied = [];
    const nearestCandidates = (target, allowOccupied = false, predicate = () => true) => {
      const available = selectedCandidates.filter((candidate) => predicate(candidate) &&
        (allowOccupied || occupied.every((selected) => distanceSquared(
          [selected.positionMm[0], selected.positionMm[2]],
          [candidate.positionMm[0], candidate.positionMm[2]],
        ) >= ASSET_PLACEMENT_MINIMUM_SEPARATION_MILLIMETERS ** 2)));
      available.sort((left, right) => distanceSquared(
        [left.positionMm[0], left.positionMm[2]], target) - distanceSquared(
        [right.positionMm[0], right.positionMm[2]], target) ||
        Math.abs(left.positionMm[1]) - Math.abs(right.positionMm[1]) ||
        left.positionMm[2] - right.positionMm[2] || left.positionMm[0] - right.positionMm[0]);
      return available;
    };
    const selectNearest = (target, allowOccupied = false, predicate = () => true) => {
      const selected = nearestCandidates(target, allowOccupied, predicate)[0] ?? null;
      if (selected && !allowOccupied) occupied.push(selected);
      return selected;
    };
    const zonePairOptions = zones.map((zone) => {
      const pairs = [];
      for (const terminalBase of terminalCandidates) {
        for (const yawMilliDegrees of CARDINAL_YAWS_MILLI_DEGREES) {
          const actionAnchor = actionAnchorForTerminalBase(terminalBase, yawMilliDegrees);
          if (!actionAnchor || !zone.bindings.every((binding) => terminalGridFits(
            actionAnchor,
            binding.actionCount,
            yawMilliDegrees,
            componentBounds,
            blocked,
            selectedCandidates,
          ))) continue;
          const yaw = yawMilliDegrees / 1000 * Math.PI / 180;
          const playerTarget = [
            actionAnchor.positionMm[0] + Math.sin(yaw) * 3500,
            actionAnchor.positionMm[2] + Math.cos(yaw) * 3500,
          ];
          const playerOptions = nearestCandidates(playerTarget, true, (candidate) =>
            candidate !== terminalBase &&
            candidate.clearanceMm >= PLACEMENT_OBSTACLE_CLEARANCE_MILLIMETERS);
          const player = playerOptions.find((candidate) =>
            zone.bindings.every((binding) => terminalGridFits(
              actionAnchor,
              binding.actionCount,
              yawMilliDegrees,
              componentBounds,
              blocked,
              selectedCandidates,
              candidate,
              pathBlocked,
            ))) ?? null;
          if (!player) continue;
          const path = graphDistances(player.graphIndex).get(terminalBase.graphIndex);
          if (!Number.isSafeInteger(path) || path < 1) continue;
          pairs.push({ terminalBase, player, yawMilliDegrees, pathCellCount: path + 1 });
        }
      }
      pairs.sort((left, right) =>
        right.terminalBase.clearanceMm - left.terminalBase.clearanceMm ||
        right.terminalBase.densityScore - left.terminalBase.densityScore ||
        distanceSquared(
          [left.terminalBase.positionMm[0], left.terminalBase.positionMm[2]],
          selectedComponent.center,
        ) - distanceSquared(
          [right.terminalBase.positionMm[0], right.terminalBase.positionMm[2]],
          selectedComponent.center,
        ) || left.yawMilliDegrees - right.yawMilliDegrees ||
        left.terminalBase.positionMm[2] - right.terminalBase.positionMm[2] ||
        left.terminalBase.positionMm[0] - right.terminalBase.positionMm[0] ||
        left.player.positionMm[2] - right.player.positionMm[2] ||
        left.player.positionMm[0] - right.player.positionMm[0]);
      return pairs.slice(0, 128);
    });
    if (zonePairOptions.some((pairs) => pairs.length < 1)) return null;
    const selectedZonePairs = [];
    let searchVisits = 0;
    const compatible = (pair) => selectedZonePairs.every((selected) => distanceSquared(
      [selected.terminalBase.positionMm[0], selected.terminalBase.positionMm[2]],
      [pair.terminalBase.positionMm[0], pair.terminalBase.positionMm[2]],
    ) >= PLACEMENT_MINIMUM_SEPARATION_MILLIMETERS ** 2);
    const selectZones = (zoneIndex) => {
      if (zoneIndex === zones.length) return true;
      if (searchVisits >= 100_000) return false;
      const ranked = zonePairOptions[zoneIndex].map((pair, index) => ({
        pair,
        index,
        separation: selectedZonePairs.length === 0 ? 0 : Math.min(...selectedZonePairs.map((selected) =>
          graphDistances(selected.terminalBase.graphIndex).get(pair.terminalBase.graphIndex) ?? -1)),
      })).filter(({ pair, separation }) => separation >= 0 && compatible(pair));
      ranked.sort((left, right) => right.separation - left.separation || left.index - right.index);
      for (const { pair } of ranked) {
        searchVisits += 1;
        if (!compatible(pair)) continue;
        selectedZonePairs.push(pair);
        if (selectZones(zoneIndex + 1)) return true;
        selectedZonePairs.pop();
      }
      return false;
    };
    if (!selectZones(0)) return null;
    const zoneLayouts = new Map(zones.map((zone, index) => [zone, selectedZonePairs[index]]));
    const selectedById = new Map();
    for (let zoneIndex = 0; zoneIndex < zones.length; zoneIndex += 1) {
      const zone = zones[zoneIndex];
      const terminalBase = zoneLayouts.get(zone).terminalBase;
      for (const item of zoneGroups[zoneIndex]) {
        const target = [terminalBase.positionMm[0], terminalBase.positionMm[2]];
        const relevantPairs = zones.filter((candidateZone) => candidateZone.bindings.some((binding) =>
          binding.visiblePlacementIds.includes(item.placementId))).map((candidateZone) => zoneLayouts.get(candidateZone));
        const selected = selectNearest(target, false, (candidate) => relevantPairs.every((pair) =>
          [pair.terminalBase, pair.player].every((anchor) => distanceSquared(
            [anchor.positionMm[0], anchor.positionMm[2]],
            [candidate.positionMm[0], candidate.positionMm[2]],
          ) >= ASSET_INTERACTION_MINIMUM_SEPARATION_MILLIMETERS ** 2)));
        if (!selected) return null;
        selectedById.set(item.placementId, selected);
      }
    }
    for (const item of requested) {
      const selected = selectedById.get(item.placementId);
      if (!selected) return null;
      layout.push({
        placementId: item.placementId,
        positionMm: [selected.positionMm[0], Math.max(0, selected.positionMm[1]), selected.positionMm[2]],
      });
    }
    const nodeBindingLayout = bindings.map((binding) => {
      const selected = zoneLayouts.get(binding.zone);
      const actionAnchor = actionAnchorForTerminalBase(
        selected.terminalBase,
        selected.yawMilliDegrees,
      );
      if (!actionAnchor) return null;
      const actionFloor = Math.max(0, actionAnchor.positionMm[1]);
      const playerFloor = Math.max(0, selected.player.positionMm[1]);
      return {
        nodeId: binding.nodeId,
        playerSpawn: {
          positionMm: [
            selected.player.positionMm[0],
            playerFloor + PLAYER_NAVIGATION_HEIGHT_MILLIMETERS,
            selected.player.positionMm[2],
          ],
          yawMilliDegrees: selected.yawMilliDegrees,
        },
        actionAnchor: {
          positionMm: [
            actionAnchor.positionMm[0],
            actionFloor,
            actionAnchor.positionMm[2],
          ],
          yawMilliDegrees: selected.yawMilliDegrees,
        },
      };
    });
    if (nodeBindingLayout.some((binding) => binding === null)) return null;
    const height = Math.max(3000, Math.min(12_000,
      Math.max(sourceEnvelope.maximumMm[1], bounds.maximum[1] - bounds.minimum[1])));
    if (maximumX - minimumX <= 2 * PLAYER_COLLISION_DIAMETER_MILLIMETERS ||
        maximumZ - minimumZ <= 2 * PLAYER_COLLISION_DIAMETER_MILLIMETERS) return null;
    const cellIndexByGraphIndex = new Map(selectedCandidates.map((candidate, index) =>
      [candidate.graphIndex, index]));
    const navigationBindings = bindings.map((binding) => {
      const selected = zoneLayouts.get(binding.zone);
      const playerCellIndex = cellIndexByGraphIndex.get(selected.player.graphIndex);
      const terminalCellIndex = cellIndexByGraphIndex.get(selected.terminalBase.graphIndex);
      return Number.isSafeInteger(playerCellIndex) && Number.isSafeInteger(terminalCellIndex)
        ? {
            nodeId: binding.nodeId,
            playerCellIndex,
            terminalCellIndex,
            pathCellCount: selected.pathCellCount,
          }
        : null;
    });
    if (navigationBindings.some((binding) => binding === null)) return null;
    return Object.freeze({
      walkableEnvelope: Object.freeze({
        ...sourceEnvelope,
        profile: "collider-agent-navigation-component-v7",
        minimumMm: Object.freeze([minimumX, 0, minimumZ]),
        maximumMm: Object.freeze([maximumX, height, maximumZ]),
      }),
      placementLayout: Object.freeze(layout.map((item) => Object.freeze({
        placementId: item.placementId,
        positionMm: Object.freeze(item.positionMm),
      }))),
      nodeBindingLayout: Object.freeze(nodeBindingLayout.map((binding) => Object.freeze({
        nodeId: binding.nodeId,
        playerSpawn: Object.freeze({
          positionMm: Object.freeze(binding.playerSpawn.positionMm),
          yawMilliDegrees: binding.playerSpawn.yawMilliDegrees,
        }),
        actionAnchor: Object.freeze({
          positionMm: Object.freeze(binding.actionAnchor.positionMm),
          yawMilliDegrees: binding.actionAnchor.yawMilliDegrees,
        }),
      }))),
      navigation: Object.freeze({
        profile: "collider-agent-grid-v1",
        cellSizeMm: PLACEMENT_GRID_MILLIMETERS,
        agentRadiusMm: PLAYER_COLLISION_DIAMETER_MILLIMETERS / 2,
        agentHeightMm: PLAYER_NAVIGATION_HEIGHT_MILLIMETERS * 2,
        maximumStepMm: PLAYER_MAX_STEP_MILLIMETERS,
        minimumClearanceMm: PLACEMENT_OBSTACLE_CLEARANCE_MILLIMETERS,
        sourceIslandCount: componentIndexes.length,
        cells: Object.freeze(selectedCandidates.map((candidate) =>
          Object.freeze([...candidate.positionMm]))),
        bindings: Object.freeze(navigationBindings.map((binding) => Object.freeze(binding))),
      }),
    });
  } catch {
    return null;
  }
}

export async function deriveColliderCalibration(bytes, sharedMetricFrame = null) {
  try {
    if (!(bytes instanceof Uint8Array)) return null;
    const useSharedFrame = sharedMetricFrame !== null;
    if (useSharedFrame && (typeof sharedMetricFrame !== "object" ||
        !Number.isSafeInteger(sharedMetricFrame.metricScaleMicros) ||
        sharedMetricFrame.metricScaleMicros < 1 ||
        sharedMetricFrame.metricScaleMicros > 100_000_000 ||
        !Number.isSafeInteger(sharedMetricFrame.groundPlaneOffsetMm) ||
        sharedMetricFrame.groundPlaneOffsetMm < -1_000_000 ||
        sharedMetricFrame.groundPlaneOffsetMm > 1_000_000)) return null;
    const document = await new NodeIO().readBinary(bytes);
    const scenes = document.getRoot().listScenes();
    if (scenes.length !== 1) return null;
    const bounds = getBounds(scenes[0]);
    if (![...bounds.min, ...bounds.max].every(Number.isFinite)) return null;
    const span = bounds.max.map((value, index) => value - bounds.min[index]);
    const narrowHorizontalSpan = Math.min(span[0], span[2]);
    if (!Number.isFinite(narrowHorizontalSpan) || narrowHorizontalSpan <= 0.01) return null;
    const scaleMicros = useSharedFrame
      ? sharedMetricFrame.metricScaleMicros
      : Math.round(TARGET_FLOOR_SPAN_METERS / narrowHorizontalSpan * 1_000_000);
    const scale = scaleMicros / 1_000_000;
    const maximumHorizontalSpanMeters = useSharedFrame
      ? OFFICIAL_METRIC_MAX_HORIZONTAL_SPAN_METERS
      : LEGACY_MAX_HORIZONTAL_SPAN_METERS;
    if (!Number.isSafeInteger(scaleMicros) || scaleMicros < 1 ||
        Math.max(span[0], span[2]) * scale > maximumHorizontalSpanMeters) return null;
    const center = bounds.max.map((value, index) => (value + bounds.min[index]) / 2);
    const sample = [center[0], center[2]];
    let floor = Number.POSITIVE_INFINITY;
    let triangleCount = 0;
    for (const top of scenes[0].listChildren()) {
      top.traverse((node) => {
        const mesh = node.getMesh();
        if (!mesh) return;
        const matrix = node.getWorldMatrix();
        for (const primitive of mesh.listPrimitives()) {
          if (primitive.getMode() !== 4) continue;
          const position = primitive.getAttribute("POSITION");
          if (!position) continue;
          const positions = position.getArray();
          const indices = primitive.getIndices()?.getArray() ?? null;
          const count = indices ? indices.length : position.getCount();
          for (let index = 0; index + 2 < count; index += 3) {
            const ids = indices
              ? [indices[index], indices[index + 1], indices[index + 2]]
              : [index, index + 1, index + 2];
            const triangle = ids.map((id) => transformPoint(
              matrix,
              positions[id * 3],
              positions[id * 3 + 1],
              positions[id * 3 + 2],
            ));
            triangleCount += 1;
            const candidate = floorAt(triangle, sample[0], sample[1]);
            if (candidate !== null) floor = Math.min(floor, candidate);
          }
        }
      });
    }
    if (triangleCount < 1) return null;
    if (!Number.isFinite(floor)) floor = bounds.min[1];
    const translation = millimeters([
          -center[0] * scale,
          -floor * scale,
          -center[2] * scale,
        ]);
    const boundsMinimumMm = millimeters(bounds.min);
    const boundsMaximumMm = millimeters(bounds.max);
    const sampleSourceMm = millimeters([sample[0], floor, sample[1]]);
    if (!translation || !boundsMinimumMm || !boundsMaximumMm || !sampleSourceMm) return null;
    return Object.freeze({
      profile: useSharedFrame ? "collider-official-metric-frame-v4" : "collider-fit-30m-v1",
      targetFloorSpanMm: useSharedFrame ? 0 : 30_000,
      maximumHorizontalSpanMm: useSharedFrame
        ? OFFICIAL_METRIC_MAX_HORIZONTAL_SPAN_MILLIMETERS
        : LEGACY_MAX_HORIZONTAL_SPAN_MILLIMETERS,
      colliderBoundsMm: Object.freeze({
        minimumMm: Object.freeze(boundsMinimumMm),
        maximumMm: Object.freeze(boundsMaximumMm),
      }),
      centerFloorSampleSourceMm: Object.freeze(sampleSourceMm),
      colliderScaleMicros: scaleMicros,
      colliderLocalTranslationMm: Object.freeze(translation),
    });
  } catch {
    return null;
  }
}

export function deriveSplatCalibration(
  statistics,
  metricScaleMicros,
  colliderAlignment = null,
) {
  try {
    const bounds = statistics?.runtimeRobustBounds;
    const mean = statistics?.sourceMeanMm;
    if (bounds?.profile !== "source-position-percentile-1-99-v1" ||
        !Array.isArray(bounds.minimumMm) || !Array.isArray(bounds.maximumMm) ||
        !Array.isArray(mean) || bounds.minimumMm.length !== 3 ||
        bounds.maximumMm.length !== 3 || mean.length !== 3 ||
        ![...bounds.minimumMm, ...bounds.maximumMm, ...mean].every(Number.isSafeInteger) ||
        !Number.isSafeInteger(metricScaleMicros) || metricScaleMicros < 1 ||
        metricScaleMicros > 100_000_000) {
      return null;
    }
    const spans = bounds.maximumMm.map((value, index) => value - bounds.minimumMm[index]);
    const narrowHorizontalSpan = Math.min(spans[0], spans[2]);
    if (!Number.isSafeInteger(narrowHorizontalSpan) || narrowHorizontalSpan < 10) return null;
    const useSharedFrame = colliderAlignment !== null;
    if (useSharedFrame && (colliderAlignment?.profile !== "collider-official-metric-frame-v4" ||
        !Number.isSafeInteger(colliderAlignment.colliderScaleMicros) ||
        !Array.isArray(colliderAlignment.colliderLocalTranslationMm) ||
        colliderAlignment.colliderLocalTranslationMm.length !== 3 ||
        !colliderAlignment.colliderLocalTranslationMm.every(Number.isSafeInteger))) return null;
    const scaleMicros = useSharedFrame
      ? colliderAlignment.colliderScaleMicros
      : Math.round(TARGET_FLOOR_SPAN_MILLIMETERS * metricScaleMicros / narrowHorizontalSpan);
    const maximumHorizontalSpanMillimeters = useSharedFrame
      ? OFFICIAL_METRIC_MAX_HORIZONTAL_SPAN_MILLIMETERS
      : LEGACY_MAX_HORIZONTAL_SPAN_MILLIMETERS;
    if (!Number.isSafeInteger(scaleMicros) || scaleMicros < 1 || scaleMicros > 100_000_000 ||
        Math.max(spans[0], spans[2]) * scaleMicros / metricScaleMicros >
          maximumHorizontalSpanMillimeters) {
      return null;
    }
    const center = bounds.minimumMm.map((value, index) =>
      (value + bounds.maximumMm[index]) / 2);
    const godotMean = useSharedFrame ? [mean[0], -mean[1], -mean[2]] : mean;
    const invertAxis = (value) => value === 0 ? 0 : -value;
    const godotBounds = useSharedFrame ? {
      minimumMm: [bounds.minimumMm[0], invertAxis(bounds.maximumMm[1]), invertAxis(bounds.maximumMm[2])],
      maximumMm: [bounds.maximumMm[0], invertAxis(bounds.minimumMm[1]), invertAxis(bounds.minimumMm[2])],
    } : {
      minimumMm: [...bounds.minimumMm],
      maximumMm: [...bounds.maximumMm],
    };
    const translation = (useSharedFrame
      ? godotMean.map((value, index) => colliderAlignment.colliderLocalTranslationMm[index] +
          Math.round(value * colliderAlignment.colliderScaleMicros / metricScaleMicros))
      : [
          Math.round((mean[0] - center[0]) * scaleMicros / metricScaleMicros),
          Math.round((mean[1] - bounds.minimumMm[1]) * scaleMicros / metricScaleMicros),
          Math.round((mean[2] - center[2]) * scaleMicros / metricScaleMicros),
        ]).map((value) => Object.is(value, -0) ? 0 : value);
    if (!translation.every(Number.isSafeInteger)) return null;
    return Object.freeze({
      profile: useSharedFrame ? "splat-opencv-to-godot-official-metric-v4" : "splat-robust-fit-30m-v1",
      boundsProfile: bounds.profile,
      targetFloorSpanMm: useSharedFrame ? 0 : TARGET_FLOOR_SPAN_MILLIMETERS,
      maximumHorizontalSpanMm: maximumHorizontalSpanMillimeters,
      splatBoundsMm: Object.freeze({
        minimumMm: Object.freeze(godotBounds.minimumMm),
        maximumMm: Object.freeze(godotBounds.maximumMm),
      }),
      splatScaleMicros: scaleMicros,
      splatLocalTranslationMm: Object.freeze(translation),
      splatLocalRotationMilliDegrees: Object.freeze(useSharedFrame ? [180_000, 0, 0] : [0, 0, 0]),
    });
  } catch {
    return null;
  }
}

export function deriveWalkableEnvelope(statistics) {
  try {
    const value = statistics?.sourceInteriorEnvelope;
    if (!value || value.profile !== "source-density-first-surface-v1" ||
        value.coordinateSpace !== "splat-robust-fit-30m-v1" ||
        !Array.isArray(value.minimumMm) || !Array.isArray(value.maximumMm) ||
        value.minimumMm.length !== 3 || value.maximumMm.length !== 3 ||
        ![...value.minimumMm, ...value.maximumMm].every(Number.isSafeInteger) ||
        value.minimumMm[1] !== 0 || value.maximumMm[1] < 3_000 ||
        value.maximumMm[1] > 12_000 ||
        !Array.isArray(value.verticalBandMm) ||
        value.verticalBandMm.length !== 2 || value.verticalBandMm[0] !== 350 ||
        value.verticalBandMm[1] !== 3_000 || value.lateralBandMm !== 4_000 ||
        value.binSizeMm !== 250 || value.minimumBinCount !== 64 ||
        value.peakThresholdPermille !== 5 || value.adjacentBins !== 2) return null;
    return Object.freeze({
      profile: value.profile,
      minimumMm: Object.freeze([...value.minimumMm]),
      maximumMm: Object.freeze([...value.maximumMm]),
      wallThicknessMm: PLAYER_COLLISION_DIAMETER_MILLIMETERS,
      floorThicknessMm: SAFETY_FLOOR_THICKNESS_MILLIMETERS,
      verticalBandMm: Object.freeze([...value.verticalBandMm]),
      lateralBandMm: value.lateralBandMm,
      binSizeMm: value.binSizeMm,
      minimumBinCount: value.minimumBinCount,
      peakThresholdPermille: value.peakThresholdPermille,
      adjacentBins: value.adjacentBins,
    });
  } catch {
    return null;
  }
}
