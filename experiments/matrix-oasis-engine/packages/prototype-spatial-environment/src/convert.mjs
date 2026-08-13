import {
  MemoryFileSystem,
  MemoryReadFileSystem,
  computeStats,
  createChunkDataPool,
  decimateSource,
  readFile,
  readFileInfo,
  writeSource,
} from "@playcanvas/splat-transform";
import { createHash } from "node:crypto";

const INPUT_NAME = "environment.spz";
const OUTPUT_NAME = "environment.compressed.ply";
const INTERIOR_TARGET_FLOOR_SPAN_METERS = 30;
const INTERIOR_BIN_METERS = 0.25;
const INTERIOR_MINIMUM_DISTANCE_METERS = 2;
const INTERIOR_MAXIMUM_DISTANCE_METERS = 90;
const INTERIOR_VERTICAL_MINIMUM_METERS = 0.35;
const INTERIOR_VERTICAL_MAXIMUM_METERS = 3;
const INTERIOR_LATERAL_BAND_METERS = 4;
const INTERIOR_MINIMUM_BIN_COUNT = 64;
const INTERIOR_PEAK_THRESHOLD_PERMILLE = 5;
const INTERIOR_ADJACENT_BINS = 2;

function fileSystem(bytes) {
  const result = new MemoryReadFileSystem();
  result.set(INPUT_NAME, bytes);
  return result;
}

function vectorStats(stats) {
  const lod = stats?.lods?.[0];
  if (!lod || !Array.isArray(lod.columns) || !lod.data) return null;
  const indices = ["x", "y", "z"].map((name) => lod.columns.indexOf(name));
  if (indices.some((index) => index < 0)) return null;
  const read = (field) => {
    const values = lod.data[field];
    if (!values || indices.some((index) => !Number.isFinite(values[index]))) return null;
    return indices.map((index) => values[index]);
  };
  const minimum = read("min");
  const maximum = read("max");
  const mean = read("mean");
  return minimum && maximum && mean ? { minimum, maximum, mean } : null;
}

function sha256(bytes) {
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

async function robustBounds(source, pool) {
  const count = source.meta.numGaussians;
  const layout = source.meta.layouts.position;
  if (!layout || !Number.isSafeInteger(count) || count < 1) return null;
  const axes = [new Float32Array(count), new Float32Array(count), new Float32Array(count)];
  let offset = 0;
  for (let chunkIndex = 0; chunkIndex < source.meta.numChunks[0]; chunkIndex += 1) {
    const chunkCount = Math.min(source.meta.chunkSize, count - offset);
    const position = pool.acquire("position", layout, chunkCount);
    try {
      await source.read({ lod: 0, chunkIndex, position });
      const values = position.field("position");
      if (values.length !== chunkCount * 3) return null;
      for (let row = 0; row < chunkCount; row += 1) {
        for (let axis = 0; axis < 3; axis += 1) {
          const value = values[row * 3 + axis];
          if (!Number.isFinite(value)) return null;
          axes[axis][offset + row] = value;
        }
      }
    } finally {
      position.release();
    }
    offset += chunkCount;
  }
  if (offset !== count) return null;
  const lowerIndex = Math.floor((count - 1) * 0.01);
  const upperIndex = Math.ceil((count - 1) * 0.99);
  for (const axis of axes) axis.sort();
  return {
    profile: "source-position-percentile-1-99-v1",
    minimum: axes.map((axis) => axis[lowerIndex]),
    maximum: axes.map((axis) => axis[upperIndex]),
  };
}

function firstDenseSurface(counts) {
  const peak = counts.reduce((maximum, count) => Math.max(maximum, count), 0);
  const threshold = Math.max(
    INTERIOR_MINIMUM_BIN_COUNT,
    Math.ceil(peak * INTERIOR_PEAK_THRESHOLD_PERMILLE / 1000),
  );
  const firstBin = Math.ceil(INTERIOR_MINIMUM_DISTANCE_METERS / INTERIOR_BIN_METERS);
  for (let index = firstBin; index <= counts.length - INTERIOR_ADJACENT_BINS; index += 1) {
    let dense = true;
    for (let adjacent = 0; adjacent < INTERIOR_ADJACENT_BINS; adjacent += 1) {
      if (counts[index + adjacent] < threshold) {
        dense = false;
        break;
      }
    }
    if (dense) return Math.round(index * INTERIOR_BIN_METERS * 1000);
  }
  return null;
}

async function sourceInteriorEnvelope(source, pool, bounds) {
  const count = source.meta.numGaussians;
  const layout = source.meta.layouts.position;
  if (!layout || !Number.isSafeInteger(count) || count < 1 ||
      !bounds?.minimum || !bounds?.maximum) return null;
  const spans = bounds.maximum.map((value, index) => value - bounds.minimum[index]);
  const narrowHorizontalSpan = Math.min(spans[0], spans[2]);
  if (!Number.isFinite(narrowHorizontalSpan) || narrowHorizontalSpan <= 0.01) return null;
  const scale = INTERIOR_TARGET_FLOOR_SPAN_METERS / narrowHorizontalSpan;
  const centerX = (bounds.minimum[0] + bounds.maximum[0]) / 2;
  const centerZ = (bounds.minimum[2] + bounds.maximum[2]) / 2;
  const binCount = Math.ceil(INTERIOR_MAXIMUM_DISTANCE_METERS / INTERIOR_BIN_METERS) + 1;
  const histograms = Array.from({ length: 4 }, () => new Uint32Array(binCount));
  let accepted = 0;
  let offset = 0;
  for (let chunkIndex = 0; chunkIndex < source.meta.numChunks[0]; chunkIndex += 1) {
    const chunkCount = Math.min(source.meta.chunkSize, count - offset);
    const position = pool.acquire("position", layout, chunkCount);
    try {
      await source.read({ lod: 0, chunkIndex, position });
      const values = position.field("position");
      if (values.length !== chunkCount * 3) return null;
      for (let row = 0; row < chunkCount; row += 1) {
        const x = (values[row * 3] - centerX) * scale;
        const y = (values[row * 3 + 1] - bounds.minimum[1]) * scale;
        const z = (values[row * 3 + 2] - centerZ) * scale;
        if (![x, y, z].every(Number.isFinite) ||
            y < INTERIOR_VERTICAL_MINIMUM_METERS ||
            y > INTERIOR_VERTICAL_MAXIMUM_METERS) continue;
        if (Math.abs(z) <= INTERIOR_LATERAL_BAND_METERS) {
          const distance = Math.abs(x);
          const bin = Math.floor(distance / INTERIOR_BIN_METERS);
          if (bin < binCount) histograms[x < 0 ? 0 : 1][bin] += 1;
        }
        if (Math.abs(x) <= INTERIOR_LATERAL_BAND_METERS) {
          const distance = Math.abs(z);
          const bin = Math.floor(distance / INTERIOR_BIN_METERS);
          if (bin < binCount) histograms[z < 0 ? 2 : 3][bin] += 1;
        }
        accepted += 1;
      }
    } finally {
      position.release();
    }
    offset += chunkCount;
  }
  if (offset !== count || accepted < INTERIOR_MINIMUM_BIN_COUNT * 4) return null;
  const distances = histograms.map(firstDenseSurface);
  if (distances.some((distance) => distance === null)) return null;
  const wallHeightMm = Math.max(3_000, Math.min(12_000, Math.round(spans[1] * scale * 1000)));
  return {
    profile: "source-density-first-surface-v1",
    coordinateSpace: "splat-robust-fit-30m-v1",
    minimumMm: [-distances[0], 0, -distances[2]],
    maximumMm: [distances[1], wallHeightMm, distances[3]],
    verticalBandMm: [
      Math.round(INTERIOR_VERTICAL_MINIMUM_METERS * 1000),
      Math.round(INTERIOR_VERTICAL_MAXIMUM_METERS * 1000),
    ],
    lateralBandMm: Math.round(INTERIOR_LATERAL_BAND_METERS * 1000),
    binSizeMm: Math.round(INTERIOR_BIN_METERS * 1000),
    minimumBinCount: INTERIOR_MINIMUM_BIN_COUNT,
    peakThresholdPermille: INTERIOR_PEAK_THRESHOLD_PERMILLE,
    adjacentBins: INTERIOR_ADJACENT_BINS,
  };
}

async function writeCompressedPly(source, pool) {
  const outputFileSystem = new MemoryFileSystem();
  await writeSource({
    filename: OUTPUT_NAME,
    outputFormat: "compressed-ply",
    source,
    pool,
    options: {},
  }, outputFileSystem);
  const output = outputFileSystem.results.get(OUTPUT_NAME);
  return output instanceof Uint8Array ? Uint8Array.prototype.slice.call(output) : null;
}

export async function convertSpzToCompressedPly(bytes, limits) {
  let sources = [];
  let pool;
  let runtimeSource;
  try {
    const info = await readFileInfo({
      filename: INPUT_NAME,
      inputFormat: "spz",
      fileSystem: fileSystem(bytes),
      options: {},
    });
    if (!info.gaussian || info.numLods !== 1 || info.numGaussians < 1 || info.numGaussians > limits.maxSplats || info.shBands > 3) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_SPZ_PROFILE_UNSUPPORTED" };
    }

    sources = await readFile({
      filename: INPUT_NAME,
      inputFormat: "spz",
      fileSystem: fileSystem(bytes),
      options: {},
    });
    if (sources.length !== 1 || sources[0].meta.numGaussians !== info.numGaussians) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_SPZ_INVALID" };
    }
    const source = sources[0];
    pool = createChunkDataPool({ chunkSize: source.meta.chunkSize, maxPooledBytes: 128 * 1024 * 1024 });
    const spatial = vectorStats(await computeStats(source, pool));
    const sourceRobustBounds = await robustBounds(source, pool);
    const interiorEnvelope = sourceRobustBounds
      ? await sourceInteriorEnvelope(source, pool, sourceRobustBounds)
      : null;
    if (!spatial || !sourceRobustBounds) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_SPZ_INVALID" };
    }

    const fullResolutionOutput = await writeCompressedPly(source, pool);
    if (!fullResolutionOutput || fullResolutionOutput.byteLength < 1 || fullResolutionOutput.byteLength > limits.compressedPlyBytes) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_COMPRESSED_PLY_LIMIT" };
    }

    const targetCount = Math.min(info.numGaussians, limits.runtimeSplatTarget);
    const profile = info.numGaussians > targetCount ? "mpmm-uniform-v1" : "identity-v1";
    runtimeSource = profile === "identity-v1"
      ? source
      : await decimateSource(source, pool, {
          targetCount,
          memoryBudgetBytes: limits.decimationMemoryBudgetBytes,
        });
    const copiedOutput = profile === "identity-v1"
      ? fullResolutionOutput
      : await writeCompressedPly(runtimeSource, pool);
    if (!copiedOutput || copiedOutput.byteLength < 1 || copiedOutput.byteLength > limits.compressedPlyBytes) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_COMPRESSED_PLY_LIMIT" };
    }
    const outputInfo = await readFileInfo({
      filename: OUTPUT_NAME,
      inputFormat: "ply",
      fileSystem: (() => {
        const result = new MemoryReadFileSystem();
        result.set(OUTPUT_NAME, copiedOutput);
        return result;
      })(),
      options: {},
    });
    if (!outputInfo.gaussian || outputInfo.numGaussians !== targetCount || outputInfo.numLods !== 1 || outputInfo.shBands !== info.shBands) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_COMPRESSED_PLY_INVALID" };
    }
    return {
      ok: true,
      bytes: copiedOutput,
      metadata: {
        numGaussians: info.numGaussians,
        runtimeNumGaussians: outputInfo.numGaussians,
        numLods: info.numLods,
        shBands: info.shBands,
        bounds: spatial,
        robustBounds: sourceRobustBounds,
        interiorEnvelope,
        derivation: {
          profile,
          targetNumGaussians: limits.runtimeSplatTarget,
          sourceNumGaussians: info.numGaussians,
          fullResolutionCompressedPly: {
            byteLength: fullResolutionOutput.byteLength,
            sha256: sha256(fullResolutionOutput),
            numGaussians: info.numGaussians,
          },
        },
      },
    };
  } catch {
    return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_SPZ_INVALID" };
  } finally {
    for (const source of [...new Set(runtimeSource ? [runtimeSource, ...sources] : sources)]) {
      try { await source.close(); } catch { /* Content failure remains a static diagnostic. */ }
    }
    if (pool) pool.destroy();
  }
}

export async function inspectCompressedPly(bytes, limits) {
  try {
    if (!(bytes instanceof Uint8Array) || bytes.byteLength < 1 || bytes.byteLength > limits.compressedPlyBytes) return null;
    const input = new MemoryReadFileSystem();
    input.set(OUTPUT_NAME, bytes);
    const info = await readFileInfo({
      filename: OUTPUT_NAME,
      inputFormat: "ply",
      fileSystem: input,
      options: {},
    });
    if (!info.gaussian || info.numLods !== 1 || info.numGaussians < 1 || info.numGaussians > limits.maxSplats || info.shBands > 3) return null;
    return {
      numGaussians: info.numGaussians,
      numLods: info.numLods,
      shBands: info.shBands,
    };
  } catch {
    return null;
  }
}
