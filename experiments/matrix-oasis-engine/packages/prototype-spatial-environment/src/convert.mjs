import {
  MemoryFileSystem,
  MemoryReadFileSystem,
  computeStats,
  createChunkDataPool,
  readFile,
  readFileInfo,
  writeSource,
} from "@playcanvas/splat-transform";

const INPUT_NAME = "environment.spz";
const OUTPUT_NAME = "environment.compressed.ply";

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

export async function convertSpzToCompressedPly(bytes, limits) {
  let sources = [];
  let pool;
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
    if (!spatial) return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_SPZ_INVALID" };

    const outputFileSystem = new MemoryFileSystem();
    await writeSource({
      filename: OUTPUT_NAME,
      outputFormat: "compressed-ply",
      source,
      pool,
      options: {},
    }, outputFileSystem);
    const output = outputFileSystem.results.get(OUTPUT_NAME);
    if (!(output instanceof Uint8Array) || output.byteLength < 1 || output.byteLength > limits.compressedPlyBytes) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_COMPRESSED_PLY_LIMIT" };
    }
    const copiedOutput = Uint8Array.prototype.slice.call(output);
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
    if (!outputInfo.gaussian || outputInfo.numGaussians !== info.numGaussians || outputInfo.numLods !== 1 || outputInfo.shBands !== info.shBands) {
      return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_COMPRESSED_PLY_INVALID" };
    }
    return {
      ok: true,
      bytes: copiedOutput,
      metadata: {
        numGaussians: info.numGaussians,
        numLods: info.numLods,
        shBands: info.shBands,
        bounds: spatial,
      },
    };
  } catch {
    return { ok: false, code: "PROTOTYPE_SPATIAL_ENVIRONMENT_SPZ_INVALID" };
  } finally {
    for (const source of sources) {
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
