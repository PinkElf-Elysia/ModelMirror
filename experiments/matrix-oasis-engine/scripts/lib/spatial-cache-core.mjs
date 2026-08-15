import path from "node:path";
import { createHash } from "node:crypto";

const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const HASH = /^sha256:[0-9a-f]{64}$/u;
const TEXT_LIMITS = Object.freeze({
  "authoring-game-pack.json": 1_048_576,
  "scene-blueprint.json": 1_048_576,
  "runtime-game-pack.json": 16_777_216,
  "runtime-receipt.json": 16_384,
  "generation-report.json": 262_144,
  "prototype-asset-bundle.json": 262_144,
  "prototype-asset-report.json": 262_144,
  "scene-pack.json": 262_144,
  "prototype-assembly-report.json": 262_144,
  "prototype-spatial-environment-bundle.json": 262_144,
  "prototype-spatial-environment-report.json": 262_144,
  "spatial-assembly.json": 262_144,
  "spatial-assembly-report.json": 262_144,
  "run-report.json": 65_536,
});
const SOURCE_TEXT_FILES = Object.freeze([
  "authoring-game-pack.json",
  "scene-blueprint.json",
  "runtime-game-pack.json",
  "runtime-receipt.json",
  "generation-report.json",
  "prototype-asset-bundle.json",
  "prototype-asset-report.json",
  "scene-pack.json",
]);
const ARGUMENTS = Object.freeze({
  "--prototype-run-root": "prototypeRunRoot",
  "--prototype-run-id": "prototypeRunId",
  "--spatial-environment-dir": "spatialEnvironmentDir",
  "--spatial-run-root": "spatialRunRoot",
});
const SPLAT_PATH = "assets/environment.compressed.ply";
const COLLIDER_PATH = "assets/environment-collider.glb";

export class SpatialCacheOperationalError extends Error {
  constructor(code = "SPATIAL_CACHE_INTERNAL_ERROR") {
    super(code);
    this.name = "SpatialCacheOperationalError";
    this.code = code;
  }
}

function fail(code) { throw new SpatialCacheOperationalError(code); }
function sha256(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }
function encode(value) { return new TextEncoder().encode(value); }
function equalBytes(left, right) {
  return left.length === right.length && left.every((byte, index) => byte === right[index]);
}
function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}
function directChild(root, candidate) { return path.dirname(candidate) === root; }
function identity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint"
    ? { dev: stat.dev, ino: stat.ino }
    : null;
}
function sameIdentity(stat, expected) {
  return expected && stat.dev === expected.dev && stat.ino === expected.ino;
}
function fileState(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? { size: stat.size, mtimeNs: stat.mtimeNs, ctimeNs: stat.ctimeNs }
    : null;
}
function sameFileState(stat, expected) {
  return expected && stat.size === expected.size && stat.mtimeNs === expected.mtimeNs && stat.ctimeNs === expected.ctimeNs;
}
function exactRecord(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key));
}
function safeRelative(relative) {
  return typeof relative === "string" && relative.length > 0 && !relative.includes("\0") &&
    !relative.includes("\\") && !path.posix.isAbsolute(relative) &&
    relative.split("/").every((part) => part !== "" && part !== "." && part !== "..");
}

export function parseSpatialCacheArguments(args) {
  if (!Array.isArray(args) || args.length !== 8) fail("SPATIAL_CACHE_ARGUMENT_INVALID");
  const output = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = ARGUMENTS[args[index]];
    const value = args[index + 1];
    if (!name || name in output || typeof value !== "string" || value.length === 0 || value.includes("\0")) {
      fail("SPATIAL_CACHE_ARGUMENT_INVALID");
    }
    output[name] = name === "prototypeRunId" ? value : path.resolve(value);
  }
  if (Object.keys(output).length !== 4 || !RUN_ID.test(output.prototypeRunId) ||
      [output.prototypeRunRoot, output.spatialEnvironmentDir, output.spatialRunRoot]
        .some((value) => !path.isAbsolute(value))) fail("SPATIAL_CACHE_ARGUMENT_INVALID");
  return Object.freeze(output);
}

async function exists(candidate, services) {
  try { await services.lstat(candidate, { bigint: true }); return true; }
  catch (error) { if (error?.code === "ENOENT") return false; throw error; }
}

async function trustedDirectory(candidate, parent, services, code) {
  try {
    const absolute = path.resolve(candidate);
    const real = path.resolve(await services.realpath(absolute));
    if (!contained(parent, real) || real !== absolute) fail(code);
    const stat = await services.lstat(absolute, { bigint: true });
    const observed = identity(stat);
    if (!stat.isDirectory() || stat.isSymbolicLink() || !observed) fail(code);
    return Object.freeze({ path: absolute, identity: observed });
  } catch (error) {
    if (error instanceof SpatialCacheOperationalError) throw error;
    fail(code);
  }
}

async function assertDirectory(record, parent, services, code) {
  const current = await trustedDirectory(record.path, parent, services, code);
  if (current.identity.dev !== record.identity.dev || current.identity.ino !== record.identity.ino) fail(code);
}

async function readStableFile(directory, relative, maximum, services, code) {
  if (!safeRelative(relative) || !Number.isSafeInteger(maximum) || maximum < 1) fail(code);
  const candidate = path.resolve(directory.path, ...relative.split("/"));
  if (!contained(directory.path, candidate)) fail(code);
  let handle;
  try {
    await assertDirectory(directory, path.dirname(directory.path), services, code);
    handle = await services.openFile(candidate, "r");
    const before = await handle.stat({ bigint: true });
    const observed = identity(before); const state = fileState(before);
    if (!before.isFile() || before.isSymbolicLink() || !observed || !state || before.size < 1n || before.size > BigInt(maximum)) fail(code);
    const real = path.resolve(await services.realpath(candidate));
    const linked = await services.lstat(candidate, { bigint: true });
    if (real !== candidate || !contained(directory.path, real) || linked.isSymbolicLink() ||
        !linked.isFile() || !sameIdentity(linked, observed) || !sameFileState(linked, state)) fail(code);
    const output = new Uint8Array(Number(before.size)); let offset = 0;
    while (offset < output.length) {
      const result = await handle.read(output, offset, output.length - offset, offset);
      if (!result || result.bytesRead < 1) fail(code);
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, output.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameIdentity(after, observed) || !sameFileState(after, state)) fail(code);
    return output;
  } catch (error) {
    if (error instanceof SpatialCacheOperationalError) throw error;
    fail(code);
  } finally {
    if (handle) try { await handle.close(); } catch { /* preserve primary result */ }
  }
}

function decode(bytes, code) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { fail(code); }
}

function canonical(text, canonicalizeJsonValue, code) {
  try {
    const value = JSON.parse(text);
    if (canonicalizeJsonValue(value) !== text) fail(code);
    return value;
  } catch (error) {
    if (error instanceof SpatialCacheOperationalError) throw error;
    fail(code);
  }
}

function spatialAssemblyOptions(assemblyReport, code) {
  if (assemblyReport?.profile === "matrix-oasis.prototype-assembly/1") return undefined;
  if (assemblyReport?.profile === "matrix-oasis.prototype-assembly/2") {
    return Object.freeze({ profile: "matrix-oasis.prototype-spatial-assembly/2" });
  }
  fail(code);
}

function validSpatialReport(value, bundleText, bundle, files, canonicalizeJsonValue) {
  if (!exactRecord(value, ["format", "formatVersion", "bundleSha256", "source", "splat", "collider", "calibration", "statistics", "toolchain"]) ||
      value.format !== "matrix-oasis.prototype-spatial-environment-materialization-report" ||
      value.formatVersion !== "0.1.0" || value.bundleSha256 !== sha256(encode(bundleText)) ||
      canonicalizeJsonValue(value.source) !== canonicalizeJsonValue({
        format: bundle.source.format, byteLength: bundle.source.byteLength, sha256: bundle.source.sha256,
      }) || canonicalizeJsonValue(value.splat) !== canonicalizeJsonValue(bundle.assets.splat) ||
      canonicalizeJsonValue(value.collider) !== canonicalizeJsonValue(bundle.assets.collider) ||
      canonicalizeJsonValue(value.calibration) !== canonicalizeJsonValue(bundle.calibration) ||
      canonicalizeJsonValue(value.statistics) !== canonicalizeJsonValue(bundle.statistics) ||
      canonicalizeJsonValue(value.toolchain) !== canonicalizeJsonValue(bundle.toolchain)) return false;
  return files.get(SPLAT_PATH)?.length === bundle.assets.splat.byteLength &&
    sha256(files.get(SPLAT_PATH)) === bundle.assets.splat.sha256 &&
    files.get(COLLIDER_PATH)?.length === bundle.assets.collider.byteLength &&
    sha256(files.get(COLLIDER_PATH)) === bundle.assets.collider.sha256;
}

function sceneAssetDescriptors(scenePack) {
  if (!Array.isArray(scenePack?.assets) || scenePack.assets.length > 16) return null;
  const output = []; const seen = new Set();
  for (const asset of scenePack.assets) {
    if (!exactRecord(asset, ["id", "roles", "path", "format", "byteLength", "sha256"]) ||
        asset.format !== "glb" || !safeRelative(asset.path) || !asset.path.startsWith("assets/") ||
        !Number.isSafeInteger(asset.byteLength) || asset.byteLength < 1 || asset.byteLength > 33_554_432 ||
        typeof asset.sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(asset.sha256) || seen.has(asset.path)) return null;
    seen.add(asset.path); output.push(asset);
  }
  return output.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
}

async function readSourceRun({ root, runId, services, canonicalizeJsonValue }) {
  const runs = await trustedDirectory(path.join(root.path, "runs"), root.path, services, "SPATIAL_CACHE_SOURCE_INVALID");
  const directory = await trustedDirectory(path.join(runs.path, runId), runs.path, services, "SPATIAL_CACHE_SOURCE_INVALID");
  const texts = Object.create(null);
  for (const name of SOURCE_TEXT_FILES) {
    texts[name] = decode(await readStableFile(directory, name, TEXT_LIMITS[name], services, "SPATIAL_CACHE_SOURCE_INVALID"), "SPATIAL_CACHE_SOURCE_INVALID");
    canonical(texts[name], canonicalizeJsonValue, "SPATIAL_CACHE_SOURCE_INVALID");
  }
  texts["prototype-assembly-report.json"] = decode(
    await readStableFile(directory, "assembly-report.json", TEXT_LIMITS["prototype-assembly-report.json"], services, "SPATIAL_CACHE_SOURCE_INVALID"),
    "SPATIAL_CACHE_SOURCE_INVALID",
  );
  const prototypeAssemblyReport = canonical(
    texts["prototype-assembly-report.json"], canonicalizeJsonValue, "SPATIAL_CACHE_SOURCE_INVALID",
  );
  const prototypeSpatialAssemblyOptions = spatialAssemblyOptions(
    prototypeAssemblyReport, "SPATIAL_CACHE_SOURCE_INVALID",
  );
  const runReportText = decode(await readStableFile(directory, "run-report.json", 65_536, services, "SPATIAL_CACHE_SOURCE_INVALID"), "SPATIAL_CACHE_SOURCE_INVALID");
  const runReport = canonical(runReportText, canonicalizeJsonValue, "SPATIAL_CACHE_SOURCE_INVALID");
  const generation = canonical(texts["generation-report.json"], canonicalizeJsonValue, "SPATIAL_CACHE_SOURCE_INVALID");
  if (!exactRecord(runReport, ["format", "formatVersion", "promptSha256", "runId", "scenePackSha256", "source", "status"]) ||
      runReport.format !== "matrix-oasis.prototype-run-report" || runReport.formatVersion !== "0.1.0" ||
      runReport.status !== "ready" || !["verified-cache", "live-provider"].includes(runReport.source) ||
      runReport.runId !== runId || !HASH.test(runReport.promptSha256) || typeof generation.model !== "string" ||
      !/^[A-Za-z0-9._/-]{1,128}$/u.test(generation.model)) fail("SPATIAL_CACHE_SOURCE_INVALID");
  const scenePack = canonical(texts["scene-pack.json"], canonicalizeJsonValue, "SPATIAL_CACHE_SOURCE_INVALID");
  const descriptors = sceneAssetDescriptors(scenePack);
  if (!descriptors || runReport.scenePackSha256 !== sha256(encode(texts["scene-pack.json"]))) {
    fail("SPATIAL_CACHE_SOURCE_INVALID");
  }
  const assets = await trustedDirectory(path.join(directory.path, "assets"), directory.path, services, "SPATIAL_CACHE_SOURCE_INVALID");
  const sceneFiles = new Map();
  for (const asset of descriptors) {
    const bytes = await readStableFile(assets, asset.path.slice(7), 33_554_432, services, "SPATIAL_CACHE_SOURCE_INVALID");
    if (bytes.length !== asset.byteLength || sha256(bytes).slice(7) !== asset.sha256) fail("SPATIAL_CACHE_SOURCE_INVALID");
    sceneFiles.set(asset.path, bytes);
  }
  return {
    directory, texts, runReport, model: generation.model, sceneFiles,
    prototypeAssemblyReport, prototypeSpatialAssemblyOptions,
  };
}

async function readSpatialSource({ directory, services, canonicalizeJsonValue }) {
  const bundleText = decode(await readStableFile(directory, "prototype-spatial-environment-bundle.json", TEXT_LIMITS["prototype-spatial-environment-bundle.json"], services, "SPATIAL_CACHE_INPUT_INVALID"), "SPATIAL_CACHE_INPUT_INVALID");
  const reportText = decode(await readStableFile(directory, "prototype-spatial-environment-report.json", TEXT_LIMITS["prototype-spatial-environment-report.json"], services, "SPATIAL_CACHE_INPUT_INVALID"), "SPATIAL_CACHE_INPUT_INVALID");
  const bundle = canonical(bundleText, canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
  const report = canonical(reportText, canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
  const assets = await trustedDirectory(path.join(directory.path, "assets"), directory.path, services, "SPATIAL_CACHE_INPUT_INVALID");
  const files = new Map([
    [SPLAT_PATH, await readStableFile(assets, "environment.compressed.ply", 100_663_296, services, "SPATIAL_CACHE_INPUT_INVALID")],
    [COLLIDER_PATH, await readStableFile(assets, "environment-collider.glb", 33_554_432, services, "SPATIAL_CACHE_INPUT_INVALID")],
  ]);
  if (!validSpatialReport(report, bundleText, bundle, files, canonicalizeJsonValue)) fail("SPATIAL_CACHE_INPUT_INVALID");
  return { bundleText, reportText, bundle, files };
}

async function writeHandle(handle, bytes, expectedIdentity, candidate, parent, services, code) {
  const stat = await handle.stat({ bigint: true });
  const linked = await services.lstat(candidate, { bigint: true });
  const real = path.resolve(await services.realpath(candidate));
  if (!sameIdentity(stat, expectedIdentity) || !sameIdentity(linked, expectedIdentity) || !stat.isFile() ||
      linked.isSymbolicLink() || real !== candidate || !contained(parent, real)) fail(code);
  await handle.writeFile(bytes); await handle.sync();
  const output = new Uint8Array(bytes.length); let offset = 0;
  while (offset < output.length) {
    const result = await handle.read(output, offset, output.length - offset, offset);
    if (!result || result.bytesRead < 1) fail(code);
    offset += result.bytesRead;
  }
  if (!equalBytes(output, bytes)) fail(code);
}

async function publishDirectory(runRoot, runId, artifacts, services) {
  const runsPath = path.join(runRoot.path, "spatial-runs");
  if (!(await exists(runsPath, services))) await services.mkdir(runsPath, { recursive: false });
  const runs = await trustedDirectory(runsPath, runRoot.path, services, "SPATIAL_CACHE_RUN_ROOT_INVALID");
  const target = path.join(runs.path, runId);
  if (!RUN_ID.test(runId) || await exists(target, services)) fail("SPATIAL_CACHE_RUN_EXISTS");
  let stage; let stageRecord; let assetsRecord; const records = []; const handles = [];
  try {
    stage = await services.mkdtemp(path.join(runs.path, ".matrix-oasis-r11-"));
    stageRecord = await trustedDirectory(stage, runs.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
    const assetsPath = path.join(stageRecord.path, "assets");
    await services.mkdir(assetsPath, { recursive: false });
    assetsRecord = await trustedDirectory(assetsPath, stageRecord.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
    const seen = new Set();
    for (const artifact of artifacts) {
      if (!artifact || !safeRelative(artifact.path) || !(artifact.bytes instanceof Uint8Array) || seen.has(artifact.path) ||
          (!artifact.path.startsWith("assets/") && artifact.path.includes("/")) ||
          (artifact.path.startsWith("assets/") && artifact.path.slice(7).includes("/"))) fail("SPATIAL_CACHE_PUBLISH_FAILED");
      seen.add(artifact.path);
      const parent = artifact.path.startsWith("assets/") ? assetsRecord : stageRecord;
      const name = artifact.path.startsWith("assets/") ? artifact.path.slice(7) : artifact.path;
      const candidate = path.join(parent.path, name);
      const handle = await services.openFile(candidate, "wx+"); handles.push(handle);
      const stat = await handle.stat({ bigint: true }); const observed = identity(stat);
      if (!observed) fail("SPATIAL_CACHE_PUBLISH_FAILED");
      records.push({ artifact, parent, candidate, handle, identity: observed });
    }
    for (const record of records) {
      await assertDirectory(stageRecord, runs.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
      await assertDirectory(record.parent, record.parent === stageRecord ? runs.path : stageRecord.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
      await writeHandle(record.handle, record.artifact.bytes, record.identity, record.candidate, record.parent.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
    }
    for (const record of records) { await record.handle.close(); handles.splice(handles.indexOf(record.handle), 1); }
    await assertDirectory(stageRecord, runs.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
    await services.rename(stageRecord.path, target); stage = undefined;
    const final = { path: target, identity: stageRecord.identity };
    await assertDirectory(final, runs.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
    const finalAssets = { path: path.join(target, "assets"), identity: assetsRecord.identity };
    await assertDirectory(finalAssets, target, services, "SPATIAL_CACHE_PUBLISH_FAILED");
    for (const record of records) {
      const nested = record.artifact.path.startsWith("assets/");
      const parent = nested ? finalAssets : final;
      const relative = nested ? record.artifact.path.slice(7) : record.artifact.path;
      const candidate = path.join(parent.path, relative);
      const bytes = await readStableFile(parent, relative, record.artifact.bytes.length, services, "SPATIAL_CACHE_PUBLISH_FAILED");
      if (!equalBytes(bytes, record.artifact.bytes) || !contained(target, candidate)) fail("SPATIAL_CACHE_PUBLISH_FAILED");
    }
  } catch (error) {
    if (error instanceof SpatialCacheOperationalError) throw error;
    fail("SPATIAL_CACHE_PUBLISH_FAILED");
  } finally {
    for (const handle of handles) try { await handle.close(); } catch { /* preserve primary result */ }
    if (stage && stageRecord && assetsRecord) {
      try {
        await assertDirectory(stageRecord, runs.path, services, "SPATIAL_CACHE_PUBLISH_FAILED");
        for (const record of records) {
          const stat = await services.lstat(record.candidate, { bigint: true });
          const real = path.resolve(await services.realpath(record.candidate));
          if (!sameIdentity(stat, record.identity) || stat.isSymbolicLink() || real !== record.candidate) throw new Error("AMBIGUOUS");
        }
        for (const record of records.reverse()) await services.rm(record.candidate, { recursive: false, force: false });
        await services.rmdir(assetsRecord.path); await services.rmdir(stageRecord.path);
      } catch { /* ambiguous staging intentionally remains */ }
    }
  }
  return target;
}

async function publishCurrent(runRoot, runId, services, canonicalizeJsonValue) {
  const text = canonicalizeJsonValue({ format: "matrix-oasis.prototype-spatial-current", formatVersion: "0.1.0", runId });
  const temporary = path.join(runRoot.path, `.current-${runId}.tmp`); let handle; let observed;
  try {
    if (await exists(temporary, services)) fail("SPATIAL_CACHE_CURRENT_FAILED");
    handle = await services.openFile(temporary, "wx+"); const stat = await handle.stat({ bigint: true }); observed = identity(stat);
    if (!observed) fail("SPATIAL_CACHE_CURRENT_FAILED");
    await writeHandle(handle, encode(text), observed, temporary, runRoot.path, services, "SPATIAL_CACHE_CURRENT_FAILED");
    await handle.close(); handle = undefined;
    const target = path.join(runRoot.path, "spatial-current.json");
    if (await exists(target, services)) {
      const linked = await services.lstat(target, { bigint: true }); const real = path.resolve(await services.realpath(target));
      if (!linked.isFile() || linked.isSymbolicLink() || real !== target) fail("SPATIAL_CACHE_CURRENT_FAILED");
    }
    await services.rename(temporary, target);
    const root = { path: runRoot.path, identity: runRoot.identity };
    const verified = await readStableFile(root, "spatial-current.json", 4096, services, "SPATIAL_CACHE_CURRENT_FAILED");
    if (!equalBytes(verified, encode(text))) fail("SPATIAL_CACHE_CURRENT_FAILED");
  } catch (error) {
    if (error instanceof SpatialCacheOperationalError) throw error;
    fail("SPATIAL_CACHE_CURRENT_FAILED");
  } finally {
    if (handle) try { await handle.close(); } catch {}
    if (observed) {
      try {
        const stat = await services.lstat(temporary, { bigint: true }); const real = path.resolve(await services.realpath(temporary));
        if (sameIdentity(stat, observed) && stat.isFile() && !stat.isSymbolicLink() && real === temporary) {
          await services.rm(temporary, { recursive: false, force: false });
        }
      } catch { /* renamed or ambiguous */ }
    }
  }
}

async function buildSpatialRun({ source, spatial, assemblePrototypeSpatialScene, canonicalizeJsonValue }) {
  const assembled = await assemblePrototypeSpatialScene({
    assemblyReportJson: source.texts["prototype-assembly-report.json"],
    scenePackJson: source.texts["scene-pack.json"],
    runtimeGamePackJson: source.texts["runtime-game-pack.json"],
    runtimeReceiptJson: source.texts["runtime-receipt.json"],
    spatialEnvironmentBundleJson: spatial.bundleText,
    spatialEnvironmentFiles: spatial.files,
  }, source.prototypeSpatialAssemblyOptions);
  if (!assembled?.ok) fail("SPATIAL_CACHE_ASSEMBLY_REJECTED");
  const sceneHash = sha256(encode(source.texts["scene-pack.json"])).slice(7);
  const assemblyHash = sha256(encode(assembled.canonicalSpatialAssemblyJson)).slice(7);
  const runId = source.runReport.runId;
  const overlay = [
    { path: "prototype-spatial-environment-bundle.json", bytes: encode(spatial.bundleText) },
    { path: "prototype-spatial-environment-report.json", bytes: encode(spatial.reportText) },
    { path: "spatial-assembly.json", bytes: encode(assembled.canonicalSpatialAssemblyJson) },
    { path: "spatial-assembly-report.json", bytes: encode(assembled.canonicalSpatialAssemblyReportJson) },
    { path: SPLAT_PATH, bytes: spatial.files.get(SPLAT_PATH) },
  ];
  const reportText = canonicalizeJsonValue({
    format: "matrix-oasis.prototype-spatial-run-report",
    formatVersion: "0.1.0",
    status: "ready",
    source: "verified-spatial-cache",
    runId,
    sourcePrototypeRunId: source.runReport.runId,
    promptSha256: source.runReport.promptSha256,
    model: source.model,
    scenePackSha256: `sha256:${sceneHash}`,
    spatialEnvironmentBundleSha256: sha256(encode(spatial.bundleText)),
    spatialAssemblySha256: `sha256:${assemblyHash}`,
    panoramaRendered: false,
    overlayFiles: overlay.map((file) => ({ path: file.path, byteLength: file.bytes.length, sha256: sha256(file.bytes) })),
  });
  const collider = source.sceneFiles.get(COLLIDER_PATH);
  if (!collider || !equalBytes(collider, spatial.files.get(COLLIDER_PATH))) fail("SPATIAL_CACHE_INPUT_INVALID");
  const artifacts = [...overlay, { path: "run-report.json", bytes: encode(reportText) }];
  return { runId, artifacts };
}

export async function importSpatialPrototypeCache({
  args,
  temporaryRoot,
  services,
  recoverPrototypeRuns,
  assemblePrototypeScene,
  assemblePrototypeSpatialScene,
  canonicalizeJsonValue,
}) {
  try {
    const parsed = parseSpatialCacheArguments(args);
    const tempReal = path.resolve(await services.realpath(temporaryRoot));
    if (![parsed.prototypeRunRoot, parsed.spatialEnvironmentDir, parsed.spatialRunRoot]
      .every((candidate) => directChild(tempReal, candidate))) fail("SPATIAL_CACHE_ARGUMENT_INVALID");
    const recovered = await recoverPrototypeRuns({ runRoot: parsed.prototypeRunRoot, temporaryRoot, services,
      assemblePrototypeScene, canonicalizeJsonValue });
    const recoveredSource = recovered.runs.find((run) => run.runId === parsed.prototypeRunId);
    if (!recoveredSource) fail("SPATIAL_CACHE_SOURCE_INVALID");
    const sourceRoot = await trustedDirectory(parsed.prototypeRunRoot, tempReal, services, "SPATIAL_CACHE_SOURCE_INVALID");
    const source = await readSourceRun({ root: sourceRoot, runId: parsed.prototypeRunId, services, canonicalizeJsonValue });
    if (source.runReport.promptSha256 !== recoveredSource.promptSha256 || source.model !== recoveredSource.model) {
      fail("SPATIAL_CACHE_SOURCE_INVALID");
    }
    const spatialDirectory = await trustedDirectory(parsed.spatialEnvironmentDir, tempReal, services, "SPATIAL_CACHE_INPUT_INVALID");
    const spatial = await readSpatialSource({ directory: spatialDirectory, services, canonicalizeJsonValue });
    if (!(await exists(parsed.spatialRunRoot, services))) await services.mkdir(parsed.spatialRunRoot, { recursive: false });
    const runRoot = await trustedDirectory(parsed.spatialRunRoot, tempReal, services, "SPATIAL_CACHE_RUN_ROOT_INVALID");
    const built = await buildSpatialRun({ source, spatial, assemblePrototypeSpatialScene, canonicalizeJsonValue });
    await publishDirectory(runRoot, built.runId, built.artifacts, services);
    await publishCurrent(runRoot, built.runId, services, canonicalizeJsonValue);
    return Object.freeze({ runId: built.runId, cacheHit: true, files: built.artifacts.length });
  } catch (error) {
    if (error instanceof SpatialCacheOperationalError) throw error;
    fail("SPATIAL_CACHE_INTERNAL_ERROR");
  }
}

export async function publishSpatialPrototypeRun({
  prototypeRunRoot,
  prototypeRunId,
  spatialRunRoot,
  temporaryRoot,
  spatialMaterialization,
  services,
  recoverPrototypeRuns,
  assemblePrototypeScene,
  assemblePrototypeSpatialScene,
  canonicalizeJsonValue,
}) {
  try {
    if (!RUN_ID.test(prototypeRunId) || !path.isAbsolute(prototypeRunRoot) ||
        !path.isAbsolute(spatialRunRoot) || !path.isAbsolute(temporaryRoot) ||
        !exactRecord(spatialMaterialization, ["canonicalBundleJson", "canonicalReportJson", "files"]) ||
        typeof spatialMaterialization.canonicalBundleJson !== "string" ||
        typeof spatialMaterialization.canonicalReportJson !== "string" || !Array.isArray(spatialMaterialization.files)) {
      fail("SPATIAL_CACHE_INPUT_INVALID");
    }
    const tempReal = path.resolve(await services.realpath(temporaryRoot));
    if (![prototypeRunRoot, spatialRunRoot].every((candidate) => directChild(tempReal, path.resolve(candidate)))) {
      fail("SPATIAL_CACHE_ARGUMENT_INVALID");
    }
    const recovered = await recoverPrototypeRuns({ runRoot: prototypeRunRoot, temporaryRoot, services,
      assemblePrototypeScene, canonicalizeJsonValue });
    const recoveredSource = recovered.runs.find((run) => run.runId === prototypeRunId);
    if (!recoveredSource) fail("SPATIAL_CACHE_SOURCE_INVALID");
    const sourceRoot = await trustedDirectory(prototypeRunRoot, tempReal, services, "SPATIAL_CACHE_SOURCE_INVALID");
    const source = await readSourceRun({ root: sourceRoot, runId: prototypeRunId, services, canonicalizeJsonValue });
    if (source.runReport.promptSha256 !== recoveredSource.promptSha256 || source.model !== recoveredSource.model) {
      fail("SPATIAL_CACHE_SOURCE_INVALID");
    }
    const bundleText = spatialMaterialization.canonicalBundleJson;
    const reportText = spatialMaterialization.canonicalReportJson;
    const bundle = canonical(bundleText, canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
    const report = canonical(reportText, canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
    const files = new Map();
    for (const file of spatialMaterialization.files) {
      if (!exactRecord(file, ["path", "bytes"]) || ![SPLAT_PATH, COLLIDER_PATH].includes(file.path) ||
          !(file.bytes instanceof Uint8Array) || files.has(file.path)) fail("SPATIAL_CACHE_INPUT_INVALID");
      files.set(file.path, Uint8Array.prototype.slice.call(file.bytes));
    }
    if (files.size !== 2 || !validSpatialReport(report, bundleText, bundle, files, canonicalizeJsonValue)) {
      fail("SPATIAL_CACHE_INPUT_INVALID");
    }
    if (!(await exists(spatialRunRoot, services))) await services.mkdir(spatialRunRoot, { recursive: false });
    const runRoot = await trustedDirectory(spatialRunRoot, tempReal, services, "SPATIAL_CACHE_RUN_ROOT_INVALID");
    const built = await buildSpatialRun({ source, spatial: { bundleText, reportText, bundle, files },
      assemblePrototypeSpatialScene, canonicalizeJsonValue });
    await publishDirectory(runRoot, built.runId, built.artifacts, services);
    await publishCurrent(runRoot, built.runId, services, canonicalizeJsonValue);
    return Object.freeze({ runId: built.runId, cacheHit: false, files: built.artifacts.length });
  } catch (error) {
    if (error instanceof SpatialCacheOperationalError) throw error;
    fail("SPATIAL_CACHE_INTERNAL_ERROR");
  }
}

async function verifySpatialOverlay(directory, runId, source, services, assemblePrototypeSpatialScene, canonicalizeJsonValue, includeFiles = false) {
  const texts = Object.create(null);
  for (const name of ["prototype-spatial-environment-bundle.json", "prototype-spatial-environment-report.json",
    "spatial-assembly.json", "spatial-assembly-report.json", "run-report.json"]) {
    texts[name] = decode(await readStableFile(directory, name, TEXT_LIMITS[name], services, "SPATIAL_CACHE_INPUT_INVALID"), "SPATIAL_CACHE_INPUT_INVALID");
    canonical(texts[name], canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
  }
  const spatialBundle = canonical(texts["prototype-spatial-environment-bundle.json"], canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
  const spatialReport = canonical(texts["prototype-spatial-environment-report.json"], canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
  const collider = source.sceneFiles.get(COLLIDER_PATH); if (!collider) fail("SPATIAL_CACHE_INPUT_INVALID");
  const overlayAssets = await trustedDirectory(path.join(directory.path, "assets"), directory.path, services, "SPATIAL_CACHE_INPUT_INVALID");
  const spatialFiles = new Map([
    [SPLAT_PATH, await readStableFile(overlayAssets, "environment.compressed.ply", 100_663_296, services, "SPATIAL_CACHE_INPUT_INVALID")],
    [COLLIDER_PATH, collider],
  ]);
  if (!validSpatialReport(spatialReport, texts["prototype-spatial-environment-bundle.json"], spatialBundle, spatialFiles, canonicalizeJsonValue)) fail("SPATIAL_CACHE_INPUT_INVALID");
  const assembled = await assemblePrototypeSpatialScene({
    assemblyReportJson: source.texts["prototype-assembly-report.json"], scenePackJson: source.texts["scene-pack.json"],
    runtimeGamePackJson: source.texts["runtime-game-pack.json"], runtimeReceiptJson: source.texts["runtime-receipt.json"],
    spatialEnvironmentBundleJson: texts["prototype-spatial-environment-bundle.json"], spatialEnvironmentFiles: spatialFiles,
  }, source.prototypeSpatialAssemblyOptions);
  if (!assembled?.ok || texts["spatial-assembly.json"] !== assembled.canonicalSpatialAssemblyJson ||
      texts["spatial-assembly-report.json"] !== assembled.canonicalSpatialAssemblyReportJson) fail("SPATIAL_CACHE_INPUT_INVALID");
  const report = canonical(texts["run-report.json"], canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
  if (!exactRecord(report, ["format", "formatVersion", "status", "source", "runId", "sourcePrototypeRunId", "promptSha256", "model",
    "scenePackSha256", "spatialEnvironmentBundleSha256", "spatialAssemblySha256", "panoramaRendered", "overlayFiles"]) ||
      report.format !== "matrix-oasis.prototype-spatial-run-report" || report.formatVersion !== "0.1.0" ||
      report.status !== "ready" || report.source !== "verified-spatial-cache" || report.runId !== runId ||
      report.sourcePrototypeRunId !== source.runReport.runId || report.promptSha256 !== source.runReport.promptSha256 ||
      report.model !== source.model || report.panoramaRendered !== false ||
      report.scenePackSha256 !== sha256(encode(source.texts["scene-pack.json"])) ||
      report.spatialEnvironmentBundleSha256 !== sha256(encode(texts["prototype-spatial-environment-bundle.json"])) ||
      report.spatialAssemblySha256 !== sha256(encode(texts["spatial-assembly.json"]))) fail("SPATIAL_CACHE_INPUT_INVALID");
  const expectedOverlay = [
    ["prototype-spatial-environment-bundle.json", encode(texts["prototype-spatial-environment-bundle.json"])],
    ["prototype-spatial-environment-report.json", encode(texts["prototype-spatial-environment-report.json"])],
    ["spatial-assembly.json", encode(texts["spatial-assembly.json"])],
    ["spatial-assembly-report.json", encode(texts["spatial-assembly-report.json"])],
    [SPLAT_PATH, spatialFiles.get(SPLAT_PATH)],
  ];
  if (!Array.isArray(report.overlayFiles) || report.overlayFiles.length !== expectedOverlay.length ||
      expectedOverlay.some(([filePath, bytes], index) => {
        const item = report.overlayFiles[index];
        return !exactRecord(item, ["path", "byteLength", "sha256"]) || item.path !== filePath ||
          item.byteLength !== bytes.length || item.sha256 !== sha256(bytes);
      })) fail("SPATIAL_CACHE_INPUT_INVALID");
  const result = { runId, promptSha256: report.promptSha256, model: report.model };
  if (includeFiles) {
    const previewFiles = new Map([
      ["runtime-game-pack.json", encode(source.texts["runtime-game-pack.json"])],
      ["runtime-receipt.json", encode(source.texts["runtime-receipt.json"])],
      ["scene-pack.json", encode(source.texts["scene-pack.json"])],
      ["spatial-assembly.json", encode(texts["spatial-assembly.json"])],
      ...source.sceneFiles,
      [SPLAT_PATH, spatialFiles.get(SPLAT_PATH)],
    ]);
    result.previewFiles = previewFiles;
    result.qualificationEvidence = Object.freeze({
      source: source.runReport.source,
      sceneBlueprintJson: source.texts["scene-blueprint.json"],
      runtimeGamePackJson: source.texts["runtime-game-pack.json"],
      runtimeReceiptJson: source.texts["runtime-receipt.json"],
    });
  }
  return Object.freeze(result);
}

async function scanSpatialOverlays({
  runRoot: runRootPath, prototypeRunRoot, temporaryRoot, services,
  recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue,
}) {
  const tempReal = path.resolve(await services.realpath(temporaryRoot)); const resolved = path.resolve(runRootPath);
  if (!directChild(tempReal, resolved) || !(await exists(resolved, services))) return Object.freeze({ currentRunId: null, runs: Object.freeze([]) });
  const root = await trustedDirectory(resolved, tempReal, services, "SPATIAL_CACHE_RUN_ROOT_INVALID");
  const runsPath = path.join(root.path, "spatial-runs");
  if (!(await exists(runsPath, services))) return Object.freeze({ currentRunId: null, runs: Object.freeze([]) });
  const runs = await trustedDirectory(runsPath, root.path, services, "SPATIAL_CACHE_RUN_ROOT_INVALID");
  const entries = await services.readdir(runs.path, { withFileTypes: true });
  if (!Array.isArray(entries) || entries.length > 200) fail("SPATIAL_CACHE_RUN_ROOT_INVALID");
  const verified = [];
  const prototypeRecovery = await recoverPrototypeRuns({ runRoot: prototypeRunRoot, temporaryRoot, services,
    assemblePrototypeScene, canonicalizeJsonValue });
  const prototypeRoot = await trustedDirectory(prototypeRunRoot, tempReal, services, "SPATIAL_CACHE_SOURCE_INVALID");
  for (const entry of entries.filter((item) => RUN_ID.test(item.name)).sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0)) {
    if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
    try {
      const recoveredSource = prototypeRecovery.runs.find((run) => run.runId === entry.name);
      if (!recoveredSource) continue;
      const source = await readSourceRun({ root: prototypeRoot, runId: entry.name, services, canonicalizeJsonValue });
      if (source.runReport.promptSha256 !== recoveredSource.promptSha256 || source.model !== recoveredSource.model) continue;
      const directory = await trustedDirectory(path.join(runs.path, entry.name), runs.path, services, "SPATIAL_CACHE_INPUT_INVALID");
      verified.push(await verifySpatialOverlay(directory, entry.name, source, services, assemblePrototypeSpatialScene, canonicalizeJsonValue));
    } catch { /* corrupt or raced run is ineligible */ }
  }
  let currentRunId = null;
  if (await exists(path.join(root.path, "spatial-current.json"), services)) {
    try {
      const text = decode(await readStableFile(root, "spatial-current.json", 4096, services, "SPATIAL_CACHE_INPUT_INVALID"), "SPATIAL_CACHE_INPUT_INVALID");
      const current = canonical(text, canonicalizeJsonValue, "SPATIAL_CACHE_INPUT_INVALID");
      if (exactRecord(current, ["format", "formatVersion", "runId"]) &&
          current.format === "matrix-oasis.prototype-spatial-current" && current.formatVersion === "0.1.0" &&
          verified.some((run) => run.runId === current.runId)) currentRunId = current.runId;
    } catch { /* invalid pointer does not invalidate historical runs */ }
  }
  return Object.freeze({ currentRunId, runs: Object.freeze(verified) });
}

export async function recoverSpatialPrototypeRuns(options) {
  try {
    const overlays = await scanSpatialOverlays(options);
    return overlays;
  }
  catch (error) { if (error instanceof SpatialCacheOperationalError) throw error; fail("SPATIAL_CACHE_INTERNAL_ERROR"); }
}

export async function findVerifiedSpatialPrototypeRun({ promptSha256, model, ...options }) {
  try {
    if (!HASH.test(promptSha256) || typeof model !== "string" || !/^[A-Za-z0-9._/-]{1,128}$/u.test(model)) fail("SPATIAL_CACHE_INPUT_INVALID");
    const recovered = await recoverSpatialPrototypeRuns(options);
    const run = recovered.runs.find((item) => item.promptSha256 === promptSha256 && item.model === model);
    return run ? Object.freeze({ ok: true, runId: run.runId }) : Object.freeze({ ok: false });
  } catch (error) { if (error instanceof SpatialCacheOperationalError) throw error; fail("SPATIAL_CACHE_INTERNAL_ERROR"); }
}

export async function loadVerifiedSpatialPrototypeRun({
  runId, runRoot: runRootPath, prototypeRunRoot, temporaryRoot, services,
  recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue,
}) {
  try {
    if (!RUN_ID.test(runId)) fail("SPATIAL_CACHE_INPUT_INVALID");
    const recovered = await recoverSpatialPrototypeRuns({ runRoot: runRootPath, prototypeRunRoot, temporaryRoot, services,
      recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue });
    const recoveredSource = recovered.runs.find((run) => run.runId === runId);
    if (!recoveredSource) fail("SPATIAL_CACHE_INPUT_INVALID");
    const tempReal = path.resolve(await services.realpath(temporaryRoot)); const resolved = path.resolve(runRootPath);
    if (!directChild(tempReal, resolved)) fail("SPATIAL_CACHE_RUN_ROOT_INVALID");
    const root = await trustedDirectory(resolved, tempReal, services, "SPATIAL_CACHE_RUN_ROOT_INVALID");
    const runs = await trustedDirectory(path.join(root.path, "spatial-runs"), root.path, services, "SPATIAL_CACHE_RUN_ROOT_INVALID");
    const directory = await trustedDirectory(path.join(runs.path, runId), runs.path, services, "SPATIAL_CACHE_INPUT_INVALID");
    const prototypeRootRecord = await trustedDirectory(prototypeRunRoot, tempReal, services, "SPATIAL_CACHE_SOURCE_INVALID");
    const source = await readSourceRun({ root: prototypeRootRecord, runId, services, canonicalizeJsonValue });
    if (source.runReport.promptSha256 !== recoveredSource.promptSha256 || source.model !== recoveredSource.model) {
      fail("SPATIAL_CACHE_SOURCE_INVALID");
    }
    return await verifySpatialOverlay(directory, runId, source, services, assemblePrototypeSpatialScene, canonicalizeJsonValue, true);
  } catch (error) { if (error instanceof SpatialCacheOperationalError) throw error; fail("SPATIAL_CACHE_INTERNAL_ERROR"); }
}
