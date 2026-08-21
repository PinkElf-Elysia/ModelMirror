import {
  MemoryReadFileSystem,
  createChunkDataPool,
  readFile,
} from "@playcanvas/splat-transform";

const SPLAT_INPUT_NAME = "environment.compressed.ply";
const REGISTRATION_BIN_MM = 50;
const REGISTRATION_MAX_SAMPLES = 120_000;
const REGISTRATION_MINIMUM_BIN_COUNT = 64;
const REGISTRATION_DENSE_PERMILLE = 200;
const REGISTRATION_BELOW_MM = 1_000;
const REGISTRATION_ABOVE_MM = 3_000;
const CELL_SIZE_MM = 250;
const VERTICAL_CELL_SIZE_MM = 500;
const VERTICAL_BAND_MM = Object.freeze([350, 3_000]);
const MINIMUM_CELL_POINTS = 16;
const PEAK_THRESHOLD_PERMILLE = 25;
const MINIMUM_VERTICAL_BINS = 3;
const MINIMUM_COMPONENT_CELLS = 3;
const MAX_GAUSSIANS = 640_000;
const MAX_BOXES = 512;

function multiply(left, right) {
  const output = Array.from({ length: 3 }, () => [0, 0, 0]);
  for (let row = 0; row < 3; row += 1) {
    for (let column = 0; column < 3; column += 1) {
      output[row][column] = left[row][0] * right[0][column] +
        left[row][1] * right[1][column] + left[row][2] * right[2][column];
    }
  }
  return output;
}

function rotationYXZ(rotationMilliDegrees) {
  if (!Array.isArray(rotationMilliDegrees) || rotationMilliDegrees.length !== 3 ||
      !rotationMilliDegrees.every(Number.isSafeInteger)) return null;
  const [x, y, z] = rotationMilliDegrees.map((value) => value * Math.PI / 180_000);
  const cx = Math.cos(x); const sx = Math.sin(x);
  const cy = Math.cos(y); const sy = Math.sin(y);
  const cz = Math.cos(z); const sz = Math.sin(z);
  const xAxis = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]];
  const yAxis = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]];
  const zAxis = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]];
  return multiply(multiply(yAxis, xAxis), zAxis);
}

function applyMatrix(matrix, value) {
  return [
    matrix[0][0] * value[0] + matrix[0][1] * value[1] + matrix[0][2] * value[2],
    matrix[1][0] * value[0] + matrix[1][1] * value[1] + matrix[1][2] * value[2],
    matrix[2][0] * value[0] + matrix[2][1] * value[1] + matrix[2][2] * value[2],
  ];
}

function transformPoint(point, transform, scale = 1) {
  const rotated = applyMatrix(transform.rotation, point.map((value) => value * scale));
  return rotated.map((value, index) => value + transform.translation[index]);
}

function captureTransform(value, scaled) {
  const translation = value?.[scaled ? "localTranslationMm" : "translationMm"];
  const rotation = value?.[scaled ? "localRotationMilliDegrees" : "rotationMilliDegrees"];
  const scaleMicros = scaled ? value?.scaleMicros : 1_000_000;
  const matrix = rotationYXZ(rotation);
  return Array.isArray(translation) && translation.length === 3 && translation.every(Number.isSafeInteger) &&
    Number.isSafeInteger(scaleMicros) && scaleMicros >= 1 && scaleMicros <= 100_000_000 && matrix
    ? { translation: translation.map((item) => item / 1000), rotation: matrix, scale: scaleMicros / 1_000_000 }
    : null;
}

function pointInRootBoundary(point, envelope) {
  return point[0] >= envelope.minimumMm[0] / 1000 && point[0] <= envelope.maximumMm[0] / 1000 &&
    point[2] >= envelope.minimumMm[2] / 1000 && point[2] <= envelope.maximumMm[2] / 1000;
}

function selectedBounds(facts, selectedPolygonIndices) {
  if (!Array.isArray(selectedPolygonIndices) || selectedPolygonIndices.length < 1) return null;
  const indexes = new Set();
  for (const polygonIndex of selectedPolygonIndices) {
    const polygon = facts?.navigationMesh?.polygons?.[polygonIndex];
    if (!polygon || !Array.isArray(polygon.vertexIndices)) return null;
    for (const vertexIndex of polygon.vertexIndices) indexes.add(vertexIndex);
  }
  const vertices = [...indexes].map((index) => facts.navigationMesh.verticesMm[index]);
  if (vertices.length < 3 || vertices.some((value) => !Array.isArray(value) || value.length !== 3 ||
      !value.every(Number.isSafeInteger))) return null;
  return {
    minimumX: Math.min(...vertices.map((value) => value[0])) - CELL_SIZE_MM,
    maximumX: Math.max(...vertices.map((value) => value[0])) + CELL_SIZE_MM,
    minimumZ: Math.min(...vertices.map((value) => value[2])) - CELL_SIZE_MM,
    maximumZ: Math.max(...vertices.map((value) => value[2])) + CELL_SIZE_MM,
  };
}

function popcount(value) {
  let count = 0;
  for (let current = value; current > 0; current >>>= 1) count += current & 1;
  return count;
}

function componentFilteredCells(counts, masks, threshold) {
  const remaining = new Set([...counts]
    .filter(([key, count]) => count >= threshold && popcount(masks.get(key) ?? 0) >= MINIMUM_VERTICAL_BINS)
    .map(([key]) => key));
  const output = new Set();
  while (remaining.size > 0) {
    const first = remaining.values().next().value;
    remaining.delete(first);
    const pending = [first];
    for (let offset = 0; offset < pending.length; offset += 1) {
      const [x, z] = pending[offset].split(",").map(Number);
      for (const [deltaX, deltaZ] of [[-1, 0], [0, -1], [0, 1], [1, 0]]) {
        const key = `${x + deltaX},${z + deltaZ}`;
        if (remaining.delete(key)) pending.push(key);
      }
    }
    if (pending.length >= MINIMUM_COMPONENT_CELLS) for (const key of pending) output.add(key);
  }
  return output;
}

function mergeCells(cells, runtimeSupportHeightMm) {
  const rows = new Map();
  for (const key of cells) {
    const [x, z] = key.split(",").map(Number);
    if (!rows.has(z)) rows.set(z, []);
    rows.get(z).push(x);
  }
  const rectangles = [];
  let active = new Map();
  for (const z of [...rows.keys()].sort((left, right) => left - right)) {
    const xs = rows.get(z).sort((left, right) => left - right);
    const runs = [];
    for (let index = 0; index < xs.length; index += 1) {
      const first = xs[index];
      let last = first;
      while (index + 1 < xs.length && xs[index + 1] === last + 1) {
        index += 1;
        last = xs[index];
      }
      runs.push([first, last]);
    }
    const next = new Map();
    for (const [first, last] of runs) {
      const key = `${first},${last}`;
      const existing = active.get(key);
      if (existing && existing.lastZ === z - 1) {
        existing.lastZ = z;
        next.set(key, existing);
      } else {
        const rectangle = { firstX: first, lastX: last, firstZ: z, lastZ: z };
        rectangles.push(rectangle);
        next.set(key, rectangle);
      }
    }
    active = next;
  }
  const boxes = rectangles.map((rectangle) => ({
    centerMm: [
      Math.round((rectangle.firstX + rectangle.lastX + 1) * CELL_SIZE_MM / 2),
      runtimeSupportHeightMm + Math.round(VERTICAL_BAND_MM[1] / 2),
      Math.round((rectangle.firstZ + rectangle.lastZ + 1) * CELL_SIZE_MM / 2),
    ],
    sizeMm: [
      (rectangle.lastX - rectangle.firstX + 1) * CELL_SIZE_MM,
      VERTICAL_BAND_MM[1],
      (rectangle.lastZ - rectangle.firstZ + 1) * CELL_SIZE_MM,
    ],
  }));
  boxes.sort((left, right) => left.centerMm[2] - right.centerMm[2] ||
    left.centerMm[0] - right.centerMm[0] || left.sizeMm[2] - right.sizeMm[2] ||
    left.sizeMm[0] - right.sizeMm[0]);
  return boxes.length <= MAX_BOXES ? boxes : null;
}

export function deriveVisualSafetyFromPositions({
  positions,
  spatialAssembly,
  environmentFacts,
  selectedPolygonIndices,
  runtimeSupportHeightMm,
}) {
  if (!(positions instanceof Float32Array) || positions.length < 3 || positions.length % 3 !== 0 ||
      positions.length / 3 > MAX_GAUSSIANS || !Number.isSafeInteger(runtimeSupportHeightMm)) return null;
  const root = captureTransform(spatialAssembly?.transforms?.root, false);
  const splat = captureTransform(spatialAssembly?.transforms?.splat, true);
  const envelope = spatialAssembly?.transforms?.walkableEnvelope;
  const bounds = selectedBounds(environmentFacts, selectedPolygonIndices);
  if (!root || !splat || !bounds || !envelope || !Array.isArray(envelope.minimumMm) ||
      !Array.isArray(envelope.maximumMm)) return null;
  const count = positions.length / 3;
  const stride = Math.max(1, Math.ceil(count / REGISTRATION_MAX_SAMPLES));
  const registration = new Map();
  let maximumRegistrationCount = 0;
  let registrationSamples = 0;
  const transform = (index) => {
    const rootPoint = transformPoint([
      positions[index * 3], positions[index * 3 + 1], positions[index * 3 + 2],
    ], splat, splat.scale);
    return { global: transformPoint(rootPoint, root), root: rootPoint };
  };
  for (let index = 0; index < count; index += stride) {
    const point = transform(index);
    const heightMm = Math.round(point.global[1] * 1000);
    if (heightMm < runtimeSupportHeightMm - REGISTRATION_BELOW_MM ||
        heightMm > runtimeSupportHeightMm + REGISTRATION_ABOVE_MM ||
        !pointInRootBoundary(point.root, envelope)) continue;
    const binMm = Math.round(heightMm / REGISTRATION_BIN_MM) * REGISTRATION_BIN_MM;
    const binCount = (registration.get(binMm) ?? 0) + 1;
    registration.set(binMm, binCount);
    maximumRegistrationCount = Math.max(maximumRegistrationCount, binCount);
    registrationSamples += 1;
  }
  let visualRegistrationOffsetMm = 0;
  let sparse = registrationSamples < REGISTRATION_MINIMUM_BIN_COUNT ||
    maximumRegistrationCount < REGISTRATION_MINIMUM_BIN_COUNT;
  if (!sparse) {
    const threshold = Math.max(REGISTRATION_MINIMUM_BIN_COUNT,
      Math.ceil(maximumRegistrationCount * REGISTRATION_DENSE_PERMILLE / 1000));
    const selected = [...registration]
      .filter(([, binCount]) => binCount >= threshold)
      .map(([binMm]) => binMm)
      .sort((left, right) => left - right)[0];
    if (!Number.isSafeInteger(selected)) sparse = true;
    else visualRegistrationOffsetMm = runtimeSupportHeightMm - selected;
  }
  if (Math.abs(visualRegistrationOffsetMm) > REGISTRATION_ABOVE_MM) return null;
  const counts = new Map();
  const masks = new Map();
  let acceptedPointCount = 0;
  let peakCellPointCount = 0;
  if (!sparse) {
    for (let index = 0; index < count; index += 1) {
      const point = transform(index).global;
      const xMm = Math.round(point[0] * 1000);
      const yMm = Math.round(point[1] * 1000) + visualRegistrationOffsetMm;
      const zMm = Math.round(point[2] * 1000);
      if (xMm < bounds.minimumX || xMm > bounds.maximumX || zMm < bounds.minimumZ ||
          zMm > bounds.maximumZ || yMm < runtimeSupportHeightMm + VERTICAL_BAND_MM[0] ||
          yMm > runtimeSupportHeightMm + VERTICAL_BAND_MM[1]) continue;
      const cellX = Math.floor(xMm / CELL_SIZE_MM);
      const cellZ = Math.floor(zMm / CELL_SIZE_MM);
      const verticalCell = Math.floor((yMm - runtimeSupportHeightMm - VERTICAL_BAND_MM[0]) /
        VERTICAL_CELL_SIZE_MM);
      const key = `${cellX},${cellZ}`;
      const cellCount = (counts.get(key) ?? 0) + 1;
      counts.set(key, cellCount);
      masks.set(key, (masks.get(key) ?? 0) | (1 << verticalCell));
      peakCellPointCount = Math.max(peakCellPointCount, cellCount);
      acceptedPointCount += 1;
    }
  }
  const cellPointThreshold = Math.max(MINIMUM_CELL_POINTS,
    Math.ceil(peakCellPointCount * PEAK_THRESHOLD_PERMILLE / 1000));
  const occupied = sparse ? new Set() : componentFilteredCells(counts, masks, cellPointThreshold);
  const boxes = mergeCells(occupied, runtimeSupportHeightMm);
  if (!boxes) return null;
  return Object.freeze({
    profile: "gaussian-vertical-occupancy-v1",
    cellSizeMm: CELL_SIZE_MM,
    verticalCellSizeMm: VERTICAL_CELL_SIZE_MM,
    verticalBandMm: Object.freeze([...VERTICAL_BAND_MM]),
    minimumCellPoints: MINIMUM_CELL_POINTS,
    peakThresholdPermille: PEAK_THRESHOLD_PERMILLE,
    minimumVerticalBins: MINIMUM_VERTICAL_BINS,
    minimumComponentCells: MINIMUM_COMPONENT_CELLS,
    visualRegistrationOffsetMm,
    sampledPointCount: count,
    acceptedPointCount,
    cellPointThreshold,
    occupiedCellCount: occupied.size,
    boxes: Object.freeze(boxes.map((box) => Object.freeze({
      centerMm: Object.freeze(box.centerMm), sizeMm: Object.freeze(box.sizeMm),
    }))),
  });
}

export async function deriveVisualSafetyEvidence({
  spatialResourceBytes,
  spatialAssembly,
  environmentFacts,
  selectedPolygonIndices,
  runtimeSupportHeightMm,
}) {
  let sources = [];
  let pool = null;
  try {
    if (!(spatialResourceBytes instanceof Uint8Array) || spatialResourceBytes.byteLength < 1 ||
        spatialResourceBytes.byteLength > 96 * 1024 * 1024) return null;
    const fileSystem = new MemoryReadFileSystem();
    fileSystem.set(SPLAT_INPUT_NAME, spatialResourceBytes);
    sources = await readFile({ filename: SPLAT_INPUT_NAME, inputFormat: "ply", fileSystem, options: {} });
    if (sources.length !== 1) return null;
    const source = sources[0];
    const count = source.meta.numGaussians;
    const layout = source.meta.layouts.position;
    if (!layout || !Number.isSafeInteger(count) || count < 1 || count > MAX_GAUSSIANS) return null;
    pool = createChunkDataPool({ chunkSize: source.meta.chunkSize, maxPooledBytes: 128 * 1024 * 1024 });
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
    for (const value of positions) if (!Number.isFinite(value)) return null;
    const center = [0, 0, 0];
    for (let index = 0; index < count; index += 1) {
      center[0] += positions[index * 3];
      center[1] += positions[index * 3 + 1];
      center[2] += positions[index * 3 + 2];
    }
    center[0] /= count; center[1] /= count; center[2] /= count;
    for (let index = 0; index < count; index += 1) {
      positions[index * 3] -= center[0];
      positions[index * 3 + 1] -= center[1];
      positions[index * 3 + 2] -= center[2];
    }
    return deriveVisualSafetyFromPositions({
      positions, spatialAssembly, environmentFacts, selectedPolygonIndices, runtimeSupportHeightMm,
    });
  } catch {
    return null;
  } finally {
    for (const source of sources) {
      try { await source.close(); } catch { /* The caller receives a static diagnostic. */ }
    }
    if (pool) pool.destroy();
  }
}
