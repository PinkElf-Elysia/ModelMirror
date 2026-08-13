import { NodeIO, getBounds } from "@gltf-transform/core";

const TARGET_FLOOR_SPAN_METERS = 30;
const MAX_HORIZONTAL_SPAN_METERS = 90;
const TARGET_FLOOR_SPAN_MILLIMETERS = 30_000;
const MAX_HORIZONTAL_SPAN_MILLIMETERS = 90_000;
const PLAYER_COLLISION_DIAMETER_MILLIMETERS = 700;
const SAFETY_FLOOR_THICKNESS_MILLIMETERS = 200;

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

export async function deriveColliderCalibration(bytes) {
  try {
    if (!(bytes instanceof Uint8Array)) return null;
    const document = await new NodeIO().readBinary(bytes);
    const scenes = document.getRoot().listScenes();
    if (scenes.length !== 1) return null;
    const bounds = getBounds(scenes[0]);
    if (![...bounds.min, ...bounds.max].every(Number.isFinite)) return null;
    const span = bounds.max.map((value, index) => value - bounds.min[index]);
    const narrowHorizontalSpan = Math.min(span[0], span[2]);
    if (!Number.isFinite(narrowHorizontalSpan) || narrowHorizontalSpan <= 0.01) return null;
    const requestedScale = TARGET_FLOOR_SPAN_METERS / narrowHorizontalSpan;
    const scaleMicros = Math.round(requestedScale * 1_000_000);
    const scale = scaleMicros / 1_000_000;
    if (!Number.isSafeInteger(scaleMicros) || scaleMicros < 1 ||
        Math.max(span[0], span[2]) * scale > MAX_HORIZONTAL_SPAN_METERS) return null;
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
      profile: "collider-fit-30m-v1",
      targetFloorSpanMm: 30_000,
      maximumHorizontalSpanMm: 90_000,
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

export function deriveSplatCalibration(statistics, metricScaleMicros) {
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
    const scaleMicros = Math.round(
      TARGET_FLOOR_SPAN_MILLIMETERS * metricScaleMicros / narrowHorizontalSpan,
    );
    if (!Number.isSafeInteger(scaleMicros) || scaleMicros < 1 || scaleMicros > 100_000_000 ||
        Math.max(spans[0], spans[2]) * scaleMicros / metricScaleMicros >
          MAX_HORIZONTAL_SPAN_MILLIMETERS) {
      return null;
    }
    const center = bounds.minimumMm.map((value, index) =>
      (value + bounds.maximumMm[index]) / 2);
    const translation = [
      Math.round((mean[0] - center[0]) * scaleMicros / metricScaleMicros),
      Math.round((mean[1] - bounds.minimumMm[1]) * scaleMicros / metricScaleMicros),
      Math.round((mean[2] - center[2]) * scaleMicros / metricScaleMicros),
    ].map((value) => Object.is(value, -0) ? 0 : value);
    if (!translation.every(Number.isSafeInteger)) return null;
    return Object.freeze({
      profile: "splat-robust-fit-30m-v1",
      boundsProfile: bounds.profile,
      targetFloorSpanMm: TARGET_FLOOR_SPAN_MILLIMETERS,
      maximumHorizontalSpanMm: MAX_HORIZONTAL_SPAN_MILLIMETERS,
      splatBoundsMm: Object.freeze({
        minimumMm: Object.freeze([...bounds.minimumMm]),
        maximumMm: Object.freeze([...bounds.maximumMm]),
      }),
      splatScaleMicros: scaleMicros,
      splatLocalTranslationMm: Object.freeze(translation),
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
