import path from "node:path";
import { createHash } from "node:crypto";

const TEXT_LIMITS = Object.freeze({
  "authoring-game-pack.json": 1_048_576,
  "scene-blueprint.json": 1_048_576,
  "runtime-game-pack.json": 16_777_216,
  "runtime-receipt.json": 16_384,
  "generation-report.json": 262_144,
  "prototype-asset-bundle.json": 262_144,
  "prototype-environment-bundle.json": 262_144,
  "prototype-environment-report.json": 262_144,
});
const ARGUMENTS = Object.freeze({
  "--prompt-file": "promptFile",
  "--prototype-dir": "prototypeDir",
  "--asset-bundle-dir": "assetBundleDir",
  "--environment-bundle-dir": "environmentBundleDir",
  "--run-root": "runRoot",
});
const HASH = /^sha256:[0-9a-f]{64}$/u;
const SAFE_RUN = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const ASSEMBLY_PROFILE_V1 = "matrix-oasis.prototype-assembly/1";
const ASSEMBLY_PROFILE_V2 = "matrix-oasis.prototype-assembly/2";

export class PrototypeCacheOperationalError extends Error {
  constructor(code = "PROTOTYPE_CACHE_INTERNAL_ERROR") {
    super(code); this.name = "PrototypeCacheOperationalError"; this.code = code;
  }
}

function fail(code) { throw new PrototypeCacheOperationalError(code); }
function assemblyOptions(profile) {
  if (profile === ASSEMBLY_PROFILE_V1) return undefined;
  if (profile === ASSEMBLY_PROFILE_V2) return Object.freeze({ profile });
  return null;
}
function sha256(bytes) { return `sha256:${createHash("sha256").update(bytes).digest("hex")}`; }
function equalBytes(left, right) { return left.length === right.length && left.every((byte, index) => byte === right[index]); }
function contained(root, candidate) { const relative = path.relative(root, candidate); return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative)); }
function directChild(root, candidate) { return path.dirname(candidate) === root; }
function identity(stat) { return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint" ? { dev: stat.dev, ino: stat.ino } : null; }
function sameIdentity(stat, expected) { return expected && stat.dev === expected.dev && stat.ino === expected.ino; }
function stableFileState(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? { size: stat.size, mtimeNs: stat.mtimeNs, ctimeNs: stat.ctimeNs } : null;
}
function sameFileState(stat, expected) {
  return expected && stat.size === expected.size && stat.mtimeNs === expected.mtimeNs && stat.ctimeNs === expected.ctimeNs;
}

export function parsePrototypeCacheArguments(args) {
  if (!Array.isArray(args) || args.length !== 10) fail("PROTOTYPE_CACHE_ARGUMENT_INVALID");
  const output = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const key = ARGUMENTS[args[index]]; const value = args[index + 1];
    if (!key || key in output || typeof value !== "string" || value.length === 0 || value.includes("\0") || !path.isAbsolute(value)) {
      fail("PROTOTYPE_CACHE_ARGUMENT_INVALID");
    }
    output[key] = path.resolve(value);
  }
  if (Object.keys(output).length !== 5) fail("PROTOTYPE_CACHE_ARGUMENT_INVALID");
  return Object.freeze(output);
}

async function exists(candidate, services) {
  try { await services.lstat(candidate, { bigint: true }); return true; }
  catch (error) { if (error?.code === "ENOENT") return false; throw error; }
}

async function trustedDirectory(candidate, parent, services, code) {
  try {
    const absolute = path.resolve(candidate); const real = await services.realpath(absolute);
    if (!contained(parent, real)) fail(code);
    const stat = await services.lstat(absolute, { bigint: true });
    if (!stat.isDirectory() || stat.isSymbolicLink() || path.resolve(real) !== absolute) fail(code);
    const observed = identity(stat); if (!observed) fail(code);
    return { path: absolute, identity: observed };
  } catch (error) { if (error instanceof PrototypeCacheOperationalError) throw error; fail(code); }
}

async function assertDirectory(record, parent, services, code) {
  const current = await trustedDirectory(record.path, parent, services, code);
  if (current.identity.dev !== record.identity.dev || current.identity.ino !== record.identity.ino) fail(code);
}

async function readStableFile(directory, relative, maximum, services, code) {
  if (typeof relative !== "string" || relative.includes("\0") || relative.includes("\\") || path.posix.isAbsolute(relative) ||
      relative.split("/").some((part) => part === "" || part === "." || part === "..")) fail(code);
  const candidate = path.resolve(directory.path, ...relative.split("/"));
  if (!contained(directory.path, candidate)) fail(code);
  let handle;
  try {
    await assertDirectory(directory, path.dirname(directory.path), services, code);
    handle = await services.openFile(candidate, "r");
    const before = await handle.stat({ bigint: true }); const fileIdentity = identity(before); const fileState = stableFileState(before);
    if (!before.isFile() || before.isSymbolicLink() || !fileIdentity || !fileState || before.size < 1n || before.size > BigInt(maximum)) fail(code);
    const real = await services.realpath(candidate); if (path.resolve(real) !== candidate || !contained(directory.path, real)) fail(code);
    const lstat = await services.lstat(candidate, { bigint: true });
    if (!sameIdentity(lstat, fileIdentity) || !sameFileState(lstat, fileState) || !lstat.isFile() || lstat.isSymbolicLink()) fail(code);
    const buffer = new Uint8Array(Number(before.size)); let offset = 0;
    while (offset < buffer.length) {
      const value = await handle.read(buffer, offset, buffer.length - offset, offset);
      if (!value || value.bytesRead < 1) fail(code); offset += value.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, buffer.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameIdentity(after, fileIdentity) || !sameFileState(after, fileState)) fail(code);
    return buffer;
  } catch (error) { if (error instanceof PrototypeCacheOperationalError) throw error; fail(code); }
  finally { if (handle) try { await handle.close(); } catch { /* preserve primary result */ } }
}

function decodeText(bytes, code) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { fail(code); }
}

function parseCanonical(text, canonicalize, code) {
  try { const value = JSON.parse(text); if (canonicalize(value) !== text) fail(code); return value; }
  catch (error) { if (error instanceof PrototypeCacheOperationalError) throw error; fail(code); }
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype &&
    Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function safeGenerationReport(value, texts) {
  if (!exactKeys(value, ["artifacts", "format", "formatVersion", "model", "requestCount", "runtimeCheck", "usage"]) ||
      value.format !== "matrix-oasis.prototype-generation-report" || value.formatVersion !== "0.1.0" ||
      typeof value.model !== "string" || !/^[A-Za-z0-9._/-]{1,128}$/u.test(value.model) ||
      !Number.isSafeInteger(value.requestCount) || value.requestCount < 1 || value.requestCount > 3 || !Array.isArray(value.artifacts) || value.artifacts.length !== 4) return false;
  const names = ["authoring-game-pack.json", "scene-blueprint.json", "runtime-game-pack.json", "runtime-receipt.json"];
  for (let index = 0; index < names.length; index += 1) {
    const item = value.artifacts[index]; const text = texts[names[index]];
    if (!exactKeys(item, ["byteLength", "name", "sha256"]) || item.name !== names[index] ||
        item.byteLength !== new TextEncoder().encode(text).length || item.sha256 !== sha256(new TextEncoder().encode(text))) return false;
  }
  return exactKeys(value.runtimeCheck, ["declaredActionCount", "initialAvailableActionCount", "status"]) &&
    value.runtimeCheck.status === "ready" && Number.isSafeInteger(value.runtimeCheck.declaredActionCount) &&
    value.runtimeCheck.declaredActionCount >= 1 && Number.isSafeInteger(value.runtimeCheck.initialAvailableActionCount) &&
    value.runtimeCheck.initialAvailableActionCount >= 0 &&
    (value.usage === null || (exactKeys(value.usage, ["completionTokens", "promptTokens", "totalTokens"]) &&
      [value.usage.completionTokens, value.usage.promptTokens, value.usage.totalTokens].every((item) => Number.isSafeInteger(item) && item >= 0)));
}

function safeEnvironmentReport(value, bundleText, environmentFiles) {
  if (!exactKeys(value, ["bundleSha256", "counts", "files", "format", "formatVersion", "provider"]) ||
      value.format !== "matrix-oasis.prototype-environment-materialization-report" || value.formatVersion !== "0.1.0" ||
      value.bundleSha256 !== sha256(new TextEncoder().encode(bundleText)) ||
      !exactKeys(value.provider, ["id", "model"]) || value.provider.id !== "world-labs-marble" || value.provider.model !== "marble-1.1" ||
      !exactKeys(value.counts, ["creates", "downloads", "polls", "worldGets"]) || value.counts.creates !== 1 ||
      value.counts.downloads !== 2 || value.counts.worldGets !== 1 || !Number.isSafeInteger(value.counts.polls) || value.counts.polls < 1 || value.counts.polls > 180 ||
      !Array.isArray(value.files) || value.files.length !== 2) return false;
  const expected = ["assets/environment-panorama.png", "assets/environment-collider.glb"];
  return expected.every((name, index) => {
    const item = value.files[index]; const bytes = environmentFiles.get(name);
    return bytes && exactKeys(item, ["byteLength", "path", "sha256"]) && item.path === name &&
      item.byteLength === bytes.length && item.sha256 === sha256(bytes);
  });
}

function assetReport(bundleText, bundle, assetFiles) {
  const files = bundle.materializations.flatMap((item) => item.assets).map((asset) => {
    const bytes = assetFiles.get(asset.path);
    return { path: asset.path, sha256: sha256(bytes) };
  });
  return { bundleSha256: sha256(new TextEncoder().encode(bundleText)), fileCount: files.length, files,
    format: "matrix-oasis.prototype-asset-materialization-report", formatVersion: "0.1.0",
    totalBytes: [...assetFiles.values()].reduce((sum, bytes) => sum + bytes.length, 0) };
}

async function writeHandle(handle, bytes, expectedIdentity, candidate, parent, services) {
  const stat = await handle.stat({ bigint: true });
  if (!sameIdentity(stat, expectedIdentity) || !stat.isFile()) fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
  const pathStat = await services.lstat(candidate, { bigint: true }); const real = await services.realpath(candidate);
  if (!sameIdentity(pathStat, expectedIdentity) || pathStat.isSymbolicLink() || path.resolve(real) !== candidate || !contained(parent, real)) fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
  await handle.writeFile(bytes); await handle.sync();
  const output = new Uint8Array(bytes.length); let offset = 0;
  while (offset < output.length) { const read = await handle.read(output, offset, output.length - offset, offset); if (!read || read.bytesRead < 1) fail("PROTOTYPE_CACHE_PUBLISH_FAILED"); offset += read.bytesRead; }
  if (!equalBytes(output, bytes)) fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
}

async function publishRun(runRoot, runId, artifacts, services) {
  const runsPath = path.join(runRoot.path, "runs");
  if (!(await exists(runsPath, services))) {
    try { await services.mkdir(runsPath, { recursive: false }); }
    catch (error) { if (error?.code !== "EEXIST") throw error; }
  }
  const runs = await trustedDirectory(runsPath, runRoot.path, services, "PROTOTYPE_CACHE_RUN_ROOT_INVALID");
  const target = path.join(runs.path, runId); if (!SAFE_RUN.test(runId) || await exists(target, services)) fail("PROTOTYPE_CACHE_RUN_EXISTS");
  let staging; let stageRecord; let assetsRecord; const handles = []; const records = [];
  try {
    staging = await services.mkdtemp(path.join(runs.path, ".matrix-oasis-r10-"));
    const stage = await trustedDirectory(staging, runs.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED"); stageRecord = stage;
    const assetsPath = path.join(stage.path, "assets"); await services.mkdir(assetsPath, { recursive: false });
    const assets = await trustedDirectory(assetsPath, stage.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED"); assetsRecord = assets;
    const seenPaths = new Set();
    for (const artifact of artifacts) {
      if (!artifact || typeof artifact.path !== "string" || !(artifact.bytes instanceof Uint8Array) || seenPaths.has(artifact.path)) {
        fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
      }
      seenPaths.add(artifact.path);
      const parent = artifact.path.startsWith("assets/") ? assets : stage;
      const name = artifact.path.startsWith("assets/") ? artifact.path.slice(7) : artifact.path;
      if (name.includes("/") || name.includes("\\") || name.length < 1) fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
      await assertDirectory(stage, runs.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
      await assertDirectory(parent, parent === stage ? runs.path : stage.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
      const candidate = path.join(parent.path, name); const handle = await services.openFile(candidate, "wx+"); handles.push(handle);
      const stat = await handle.stat({ bigint: true }); const observed = identity(stat); if (!observed) fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
      records.push({ ...artifact, parent, candidate, handle, identity: observed });
    }
    for (const record of records) {
      await assertDirectory(stage, runs.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
      await writeHandle(record.handle, record.bytes, record.identity, record.candidate, record.parent.path, services);
    }
    for (const record of records) { await record.handle.close(); handles.splice(handles.indexOf(record.handle), 1); }
    await assertDirectory(stage, runs.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
    if (await exists(target, services)) fail("PROTOTYPE_CACHE_RUN_EXISTS");
    await services.rename(stage.path, target); staging = undefined;
    const finalStage = { path: target, identity: stage.identity };
    await assertDirectory(finalStage, runs.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
    const finalAssets = { path: path.join(target, "assets"), identity: assets.identity };
    await assertDirectory(finalAssets, target, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
    for (const record of records) {
      const finalParent = record.parent.path === assets.path ? finalAssets : finalStage;
      const finalPath = path.join(finalParent.path, path.basename(record.candidate));
      const finalHandle = await services.openFile(finalPath, "r"); handles.push(finalHandle);
      const finalStat = await finalHandle.stat({ bigint: true });
      const finalPathStat = await services.lstat(finalPath, { bigint: true });
      const finalReal = await services.realpath(finalPath);
      if (!sameIdentity(finalStat, record.identity) || !sameIdentity(finalPathStat, record.identity) ||
          !finalStat.isFile() || finalPathStat.isSymbolicLink() || path.resolve(finalReal) !== finalPath ||
          !contained(finalParent.path, finalReal)) fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
      const output = new Uint8Array(record.bytes.length); let offset = 0;
      while (offset < output.length) {
        const read = await finalHandle.read(output, offset, output.length - offset, offset);
        if (!read || read.bytesRead < 1) fail("PROTOTYPE_CACHE_PUBLISH_FAILED"); offset += read.bytesRead;
      }
      const tail = await finalHandle.read(new Uint8Array(1), 0, 1, output.length);
      if (tail.bytesRead !== 0 || !equalBytes(output, record.bytes)) fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
      await finalHandle.close(); handles.splice(handles.indexOf(finalHandle), 1);
    }
  } catch (error) {
    if (error instanceof PrototypeCacheOperationalError) throw error;
    fail("PROTOTYPE_CACHE_PUBLISH_FAILED");
  } finally {
    for (const handle of handles) try { await handle.close(); } catch { /* preserve primary failure */ }
    if (staging && stageRecord && assetsRecord) {
      try {
        await assertDirectory(stageRecord, runs.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
        await assertDirectory(assetsRecord, stageRecord.path, services, "PROTOTYPE_CACHE_PUBLISH_FAILED");
        for (const record of records) {
          const stat = await services.lstat(record.candidate, { bigint: true });
          const real = await services.realpath(record.candidate);
          if (!sameIdentity(stat, record.identity) || !stat.isFile() || stat.isSymbolicLink() ||
              path.resolve(real) !== record.candidate || !contained(record.parent.path, real)) throw new Error("AMBIGUOUS");
        }
        for (const record of records.reverse()) await services.rm(record.candidate, { recursive: false, force: false });
        await services.rmdir(assetsRecord.path);
        await services.rmdir(stageRecord.path);
      } catch { /* ambiguous staging remains for inspection */ }
    }
  }
  return target;
}

async function publishCurrent(runRoot, runId, services, canonicalize) {
  const text = canonicalize({ format: "matrix-oasis.prototype-current", formatVersion: "0.1.0", runId });
  const temporary = path.join(runRoot.path, `.current-${runId}.tmp`); let handle; let temporaryIdentity;
  try {
    if (await exists(temporary, services)) fail("PROTOTYPE_CACHE_CURRENT_FAILED");
    handle = await services.openFile(temporary, "wx+"); const stat = await handle.stat({ bigint: true }); const observed = identity(stat);
    if (!observed) fail("PROTOTYPE_CACHE_CURRENT_FAILED"); temporaryIdentity = observed;
    await writeHandle(handle, new TextEncoder().encode(text), observed, temporary, runRoot.path, services);
    await handle.close(); handle = undefined;
    const target = path.join(runRoot.path, "current.json");
    if (await exists(target, services)) {
      const existing = await services.lstat(target, { bigint: true }); const real = await services.realpath(target);
      if (!existing.isFile() || existing.isSymbolicLink() || path.resolve(real) !== target) fail("PROTOTYPE_CACHE_CURRENT_FAILED");
    }
    await services.rename(temporary, target);
    const final = await services.openFile(target, "r");
    try {
      const finalStat = await final.stat({ bigint: true }); const finalPathStat = await services.lstat(target, { bigint: true });
      const finalReal = await services.realpath(target);
      if (!sameIdentity(finalStat, observed) || !sameIdentity(finalPathStat, observed) || finalPathStat.isSymbolicLink() ||
          path.resolve(finalReal) !== target) fail("PROTOTYPE_CACHE_CURRENT_FAILED");
      const expected = new TextEncoder().encode(text); const output = new Uint8Array(expected.length);
      let offset = 0; while (offset < output.length) { const read = await final.read(output, offset, output.length - offset, offset); if (!read || read.bytesRead < 1) fail("PROTOTYPE_CACHE_CURRENT_FAILED"); offset += read.bytesRead; }
      if (!equalBytes(output, expected)) fail("PROTOTYPE_CACHE_CURRENT_FAILED");
    } finally { await final.close(); }
  } catch (error) { if (error instanceof PrototypeCacheOperationalError) throw error; fail("PROTOTYPE_CACHE_CURRENT_FAILED"); }
  finally {
    if (handle) try { await handle.close(); } catch {}
    if (temporaryIdentity) {
      try {
        const stat = await services.lstat(temporary, { bigint: true }); const real = await services.realpath(temporary);
        if (sameIdentity(stat, temporaryIdentity) && stat.isFile() && !stat.isSymbolicLink() &&
            path.resolve(real) === temporary && contained(runRoot.path, real)) {
          await services.rm(temporary, { recursive: false, force: false });
        }
      } catch { /* renamed or ambiguous temporary file */ }
    }
  }
}

async function publishPreparedPrototypeRun({
  promptBytes,
  texts,
  assetText,
  assetBundle,
  assetFiles,
  environmentText,
  environmentBundle,
  environmentReportText,
  environmentFiles,
  runRoot,
  source,
  services,
  assemblePrototypeScene,
  canonicalizeJsonValue,
  assemblyProfile = ASSEMBLY_PROFILE_V1,
}) {
  const generationReport = parseCanonical(texts["generation-report.json"], canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  if (!safeGenerationReport(generationReport, texts)) fail("PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentReport = parseCanonical(environmentReportText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  if (!safeEnvironmentReport(environmentReport, environmentText, environmentFiles)) fail("PROTOTYPE_CACHE_INPUT_INVALID");
  const selectedAssemblyOptions = assemblyOptions(assemblyProfile);
  if (selectedAssemblyOptions === null) fail("PROTOTYPE_CACHE_INPUT_INVALID");
  const assembled = await assemblePrototypeScene({ authoringGamePackJson: texts["authoring-game-pack.json"],
    sceneBlueprintJson: texts["scene-blueprint.json"], runtimeGamePackJson: texts["runtime-game-pack.json"],
    runtimeReceiptJson: texts["runtime-receipt.json"], assetBundleJson: assetText, assetFiles,
    environmentBundleJson: environmentText, environmentFiles }, selectedAssemblyOptions);
  if (!assembled?.ok) fail("PROTOTYPE_CACHE_ASSEMBLY_REJECTED");
  const promptSha256 = sha256(promptBytes);
  const blueprintHash = sha256(new TextEncoder().encode(texts["scene-blueprint.json"])).slice(7);
  const cacheKey = {
    cacheKeyVersion: 1,
    promptSha256,
    model: generationReport.model,
    blueprintSha256: `sha256:${blueprintHash}`,
    assetBundleSha256: sha256(new TextEncoder().encode(assetText)),
    environmentBundleSha256: sha256(new TextEncoder().encode(environmentText)),
    assemblerVersion: "0.1.0-r10",
  };
  if (assemblyProfile === ASSEMBLY_PROFILE_V2) cacheKey.assemblyProfile = assemblyProfile;
  const bundleHash = sha256(new TextEncoder().encode(canonicalizeJsonValue(cacheKey))).slice(7);
  const runId = `${blueprintHash}-${bundleHash}`;
  const safeAssetReportText = canonicalizeJsonValue(assetReport(assetText, assetBundle, assetFiles));
  const runReportText = canonicalizeJsonValue({ format: "matrix-oasis.prototype-run-report", formatVersion: "0.1.0",
    status: "ready", source, promptSha256, runId,
    scenePackSha256: sha256(new TextEncoder().encode(assembled.canonicalScenePackJson)) });
  const artifacts = [
    ...["authoring-game-pack.json", "scene-blueprint.json", "runtime-game-pack.json", "runtime-receipt.json", "generation-report.json"].map((name) => ({ path: name, bytes: new TextEncoder().encode(texts[name]) })),
    { path: "prototype-asset-bundle.json", bytes: new TextEncoder().encode(assetText) },
    { path: "prototype-asset-report.json", bytes: new TextEncoder().encode(safeAssetReportText) },
    { path: "prototype-environment-bundle.json", bytes: new TextEncoder().encode(environmentText) },
    { path: "prototype-environment-report.json", bytes: new TextEncoder().encode(environmentReportText) },
    { path: "scene-pack.json", bytes: new TextEncoder().encode(assembled.canonicalScenePackJson) },
    { path: "assembly-report.json", bytes: new TextEncoder().encode(assembled.canonicalAssemblyReportJson) },
    { path: "run-report.json", bytes: new TextEncoder().encode(runReportText) },
  ];
  for (const reference of assembled.referencedFiles) {
    const fileSource = reference.source === "prototype-assets" ? assetFiles : environmentFiles;
    const value = fileSource.get(reference.path); if (!value) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    artifacts.push({ path: reference.path, bytes: value });
  }
  const publishedPaths = new Set(artifacts.map(({ path: artifactPath }) => artifactPath));
  for (const [assetPath, value] of [...assetFiles.entries()].sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)) {
    if (!publishedPaths.has(assetPath)) { artifacts.push({ path: assetPath, bytes: value }); publishedPaths.add(assetPath); }
  }
  await publishRun(runRoot, runId, artifacts, services);
  await publishCurrent(runRoot, runId, services, canonicalizeJsonValue);
  return Object.freeze({ runId, cacheHit: source === "verified-cache", files: artifacts.length });
}

export async function publishPrototypeRun({
  prompt,
  prototypeArtifacts,
  assetMaterialization,
  environmentMaterialization,
  runRoot: runRootPath,
  temporaryRoot,
  source = "live-provider",
  services,
  assemblePrototypeScene,
  canonicalizeJsonValue,
  assemblyProfile = ASSEMBLY_PROFILE_V1,
}) {
  try {
    const promptBytes = typeof prompt === "string" ? new TextEncoder().encode(prompt) : null;
    if (!promptBytes || promptBytes.length > 32_768 || prompt.trim().length < 1 ||
        new TextDecoder("utf-8", { fatal: true }).decode(promptBytes) !== prompt ||
        !["live-provider", "verified-cache"].includes(source) || !path.isAbsolute(runRootPath) || !path.isAbsolute(temporaryRoot)) {
      fail("PROTOTYPE_CACHE_INPUT_INVALID");
    }
    if (assemblyOptions(assemblyProfile) === null) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    const artifactNames = ["authoringGamePackJson", "sceneBlueprintJson", "runtimeGamePackJson", "runtimeReceiptJson", "generationReportJson"];
    if (!exactKeys(prototypeArtifacts, artifactNames) || artifactNames.some((name) => typeof prototypeArtifacts[name] !== "string") ||
        !exactKeys(assetMaterialization, ["canonicalBundleJson", "files"]) ||
        !exactKeys(environmentMaterialization, ["canonicalBundleJson", "canonicalReportJson", "files"]) ||
        !Array.isArray(assetMaterialization.files) || !Array.isArray(environmentMaterialization.files)) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    const tempReal = path.resolve(await services.realpath(temporaryRoot));
    const resolvedRunRoot = path.resolve(runRootPath);
    if (!directChild(tempReal, resolvedRunRoot)) fail("PROTOTYPE_CACHE_RUN_ROOT_INVALID");
    if (!(await exists(resolvedRunRoot, services))) await services.mkdir(resolvedRunRoot, { recursive: false });
    const runRoot = await trustedDirectory(resolvedRunRoot, tempReal, services, "PROTOTYPE_CACHE_RUN_ROOT_INVALID");
    const texts = {
      "authoring-game-pack.json": prototypeArtifacts.authoringGamePackJson,
      "scene-blueprint.json": prototypeArtifacts.sceneBlueprintJson,
      "runtime-game-pack.json": prototypeArtifacts.runtimeGamePackJson,
      "runtime-receipt.json": prototypeArtifacts.runtimeReceiptJson,
      "generation-report.json": prototypeArtifacts.generationReportJson,
    };
    for (const [name, text] of Object.entries(texts)) {
      const limit = TEXT_LIMITS[name]; const encoded = new TextEncoder().encode(text);
      if (encoded.length < 1 || encoded.length > limit || new TextDecoder("utf-8", { fatal: true }).decode(encoded) !== text) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    }
    const assetText = assetMaterialization.canonicalBundleJson;
    const environmentText = environmentMaterialization.canonicalBundleJson;
    const environmentReportText = environmentMaterialization.canonicalReportJson;
    if ([assetText, environmentText, environmentReportText].some((text) => typeof text !== "string")) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    const assetBundle = parseCanonical(assetText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
    const environmentBundle = parseCanonical(environmentText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
    const captureOutputs = (outputs) => {
      const captured = new Map();
      for (const output of outputs) {
        if (!exactKeys(output, ["path", "bytes"]) || typeof output.path !== "string" || !(output.bytes instanceof Uint8Array) || captured.has(output.path)) fail("PROTOTYPE_CACHE_INPUT_INVALID");
        captured.set(output.path, Uint8Array.prototype.slice.call(output.bytes));
      }
      return captured;
    };
    const assetFiles = captureOutputs(assetMaterialization.files);
    const environmentFiles = captureOutputs(environmentMaterialization.files);
    return await publishPreparedPrototypeRun({ promptBytes, texts, assetText, assetBundle,
      assetFiles, environmentText, environmentBundle, environmentReportText, environmentFiles, runRoot, source,
      services, assemblePrototypeScene, canonicalizeJsonValue, assemblyProfile });
  } catch (error) {
    if (error instanceof PrototypeCacheOperationalError) throw error;
    fail("PROTOTYPE_CACHE_INTERNAL_ERROR");
  }
}

async function verifyPublishedRun(directory, runId, services, canonicalizeJsonValue, assemblePrototypeScene) {
  const texts = Object.create(null);
  for (const name of ["authoring-game-pack.json", "scene-blueprint.json", "runtime-game-pack.json", "runtime-receipt.json", "generation-report.json"]) {
    texts[name] = decodeText(await readStableFile(directory, name, TEXT_LIMITS[name], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  }
  const assetText = decodeText(await readStableFile(directory, "prototype-asset-bundle.json", TEXT_LIMITS["prototype-asset-bundle.json"], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const assetReportText = decodeText(await readStableFile(directory, "prototype-asset-report.json", 262_144, services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentText = decodeText(await readStableFile(directory, "prototype-environment-bundle.json", TEXT_LIMITS["prototype-environment-bundle.json"], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentReportText = decodeText(await readStableFile(directory, "prototype-environment-report.json", TEXT_LIMITS["prototype-environment-report.json"], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const sceneText = decodeText(await readStableFile(directory, "scene-pack.json", 262_144, services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const assemblyReportText = decodeText(await readStableFile(directory, "assembly-report.json", 262_144, services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const runReportText = decodeText(await readStableFile(directory, "run-report.json", 65_536, services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const generationReport = parseCanonical(texts["generation-report.json"], canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  if (!safeGenerationReport(generationReport, texts)) fail("PROTOTYPE_CACHE_INPUT_INVALID");
  const assetBundle = parseCanonical(assetText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentBundle = parseCanonical(environmentText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  const assetFiles = new Map();
  for (const asset of assetBundle.materializations?.flatMap((item) => item.assets) ?? []) {
    assetFiles.set(asset.path, await readStableFile(directory, asset.path, 33_554_432, services, "PROTOTYPE_CACHE_INPUT_INVALID"));
  }
  const environmentFiles = new Map();
  for (const asset of [environmentBundle.assets?.panorama, environmentBundle.assets?.collider]) {
    if (!asset?.path) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    environmentFiles.set(asset.path, await readStableFile(directory, asset.path, asset.format === "png" ? 67_108_864 : 33_554_432, services, "PROTOTYPE_CACHE_INPUT_INVALID"));
  }
  const environmentReport = parseCanonical(environmentReportText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  if (!safeEnvironmentReport(environmentReport, environmentText, environmentFiles) ||
      assetReportText !== canonicalizeJsonValue(assetReport(assetText, assetBundle, assetFiles))) fail("PROTOTYPE_CACHE_INPUT_INVALID");
  const assemblyReport = parseCanonical(assemblyReportText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  const selectedAssemblyOptions = assemblyOptions(assemblyReport?.profile);
  if (selectedAssemblyOptions === null) fail("PROTOTYPE_CACHE_INPUT_INVALID");
  const assembled = await assemblePrototypeScene({ authoringGamePackJson: texts["authoring-game-pack.json"],
    sceneBlueprintJson: texts["scene-blueprint.json"], runtimeGamePackJson: texts["runtime-game-pack.json"],
    runtimeReceiptJson: texts["runtime-receipt.json"], assetBundleJson: assetText, assetFiles,
    environmentBundleJson: environmentText, environmentFiles }, selectedAssemblyOptions);
  if (!assembled?.ok || sceneText !== assembled.canonicalScenePackJson || assemblyReportText !== assembled.canonicalAssemblyReportJson) {
    fail("PROTOTYPE_CACHE_INPUT_INVALID");
  }
  const runReport = parseCanonical(runReportText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  if (!exactKeys(runReport, ["format", "formatVersion", "promptSha256", "runId", "scenePackSha256", "source", "status"]) ||
      runReport.format !== "matrix-oasis.prototype-run-report" || runReport.formatVersion !== "0.1.0" ||
      runReport.status !== "ready" || !["verified-cache", "live-provider"].includes(runReport.source) ||
      runReport.runId !== runId || !HASH.test(runReport.promptSha256) ||
      runReport.scenePackSha256 !== sha256(new TextEncoder().encode(sceneText))) fail("PROTOTYPE_CACHE_INPUT_INVALID");
  return Object.freeze({ runId, promptSha256: runReport.promptSha256, model: generationReport.model });
}

async function scanPrototypeRuns({ runRoot: runRootPath, temporaryRoot, services, canonicalizeJsonValue, assemblePrototypeScene }) {
  const tempReal = path.resolve(await services.realpath(temporaryRoot)); const resolvedRunRoot = path.resolve(runRootPath);
  if (!directChild(tempReal, resolvedRunRoot) || !(await exists(resolvedRunRoot, services))) return { currentRunId: null, runs: [] };
  const runRoot = await trustedDirectory(resolvedRunRoot, tempReal, services, "PROTOTYPE_CACHE_RUN_ROOT_INVALID");
  const runsPath = path.join(runRoot.path, "runs"); if (!(await exists(runsPath, services))) return { currentRunId: null, runs: [] };
  const runsRoot = await trustedDirectory(runsPath, runRoot.path, services, "PROTOTYPE_CACHE_RUN_ROOT_INVALID");
  let entries;
  try { entries = await services.readdir(runsRoot.path, { withFileTypes: true }); }
  catch { fail("PROTOTYPE_CACHE_RUN_ROOT_INVALID"); }
  if (!Array.isArray(entries) || entries.length > 200) fail("PROTOTYPE_CACHE_RUN_ROOT_INVALID");
  const verified = [];
  for (const entry of entries.filter((item) => SAFE_RUN.test(item.name)).sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0)) {
    if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
    try {
      const directory = await trustedDirectory(path.join(runsRoot.path, entry.name), runsRoot.path, services, "PROTOTYPE_CACHE_INPUT_INVALID");
      verified.push(await verifyPublishedRun(directory, entry.name, services, canonicalizeJsonValue, assemblePrototypeScene));
    } catch { /* a corrupt or raced run is not eligible for reuse */ }
  }
  let currentRunId = null;
  const currentPath = path.join(runRoot.path, "current.json");
  if (await exists(currentPath, services)) {
    try {
      const currentText = decodeText(await readStableFile(runRoot, "current.json", 4096, services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
      const current = parseCanonical(currentText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
      if (exactKeys(current, ["format", "formatVersion", "runId"]) && current.format === "matrix-oasis.prototype-current" &&
          current.formatVersion === "0.1.0" && verified.some((run) => run.runId === current.runId)) currentRunId = current.runId;
    } catch { /* an invalid current pointer never invalidates verified historical runs */ }
  }
  return Object.freeze({ currentRunId, runs: Object.freeze(verified) });
}

export async function recoverPrototypeRuns(options) {
  try { return await scanPrototypeRuns(options); }
  catch (error) { if (error instanceof PrototypeCacheOperationalError) throw error; fail("PROTOTYPE_CACHE_INTERNAL_ERROR"); }
}

export async function findVerifiedPrototypeRun({ promptSha256, model, ...options }) {
  try {
    if (!HASH.test(promptSha256) || typeof model !== "string" || !/^[A-Za-z0-9._/-]{1,128}$/u.test(model)) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    const scanned = await scanPrototypeRuns(options);
    const matched = scanned.runs.find((run) => run.promptSha256 === promptSha256 && run.model === model);
    return matched ? Object.freeze({ ok: true, runId: matched.runId }) : Object.freeze({ ok: false });
  } catch (error) { if (error instanceof PrototypeCacheOperationalError) throw error; fail("PROTOTYPE_CACHE_INTERNAL_ERROR"); }
}

export async function importPrototypeCache({ args, temporaryRoot, services, assemblePrototypeScene, canonicalizeJsonValue }) {
  const parsed = parsePrototypeCacheArguments(args);
  const tempReal = path.resolve(await services.realpath(temporaryRoot));
  const promptRoot = await trustedDirectory(path.dirname(parsed.promptFile), tempReal, services, "PROTOTYPE_CACHE_PROMPT_INVALID");
  const promptBytes = await readStableFile(promptRoot, path.basename(parsed.promptFile), 32_768, services, "PROTOTYPE_CACHE_PROMPT_INVALID");
  const prompt = decodeText(promptBytes, "PROTOTYPE_CACHE_PROMPT_INVALID");
  if (prompt.trim().length < 1) fail("PROTOTYPE_CACHE_PROMPT_INVALID");
  const prototypeDir = await trustedDirectory(parsed.prototypeDir, tempReal, services, "PROTOTYPE_CACHE_INPUT_INVALID");
  const assetDir = await trustedDirectory(parsed.assetBundleDir, tempReal, services, "PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentDir = await trustedDirectory(parsed.environmentBundleDir, tempReal, services, "PROTOTYPE_CACHE_INPUT_INVALID");
  if (!directChild(tempReal, parsed.runRoot)) fail("PROTOTYPE_CACHE_RUN_ROOT_INVALID");
  if (!(await exists(parsed.runRoot, services))) await services.mkdir(parsed.runRoot, { recursive: false });
  const runRoot = await trustedDirectory(parsed.runRoot, tempReal, services, "PROTOTYPE_CACHE_RUN_ROOT_INVALID");
  const texts = Object.create(null);
  for (const name of ["authoring-game-pack.json", "scene-blueprint.json", "runtime-game-pack.json", "runtime-receipt.json", "generation-report.json"]) {
    texts[name] = decodeText(await readStableFile(prototypeDir, name, TEXT_LIMITS[name], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  }
  const assetText = decodeText(await readStableFile(assetDir, "prototype-asset-bundle.json", TEXT_LIMITS["prototype-asset-bundle.json"], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentText = decodeText(await readStableFile(environmentDir, "prototype-environment-bundle.json", TEXT_LIMITS["prototype-environment-bundle.json"], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentReportText = decodeText(await readStableFile(environmentDir, "prototype-environment-report.json", TEXT_LIMITS["prototype-environment-report.json"], services, "PROTOTYPE_CACHE_INPUT_INVALID"), "PROTOTYPE_CACHE_INPUT_INVALID");
  const assetBundle = parseCanonical(assetText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  const environmentBundle = parseCanonical(environmentText, canonicalizeJsonValue, "PROTOTYPE_CACHE_INPUT_INVALID");
  const assetFiles = new Map();
  for (const asset of assetBundle.materializations?.flatMap((item) => item.assets) ?? []) {
    assetFiles.set(asset.path, await readStableFile(assetDir, asset.path, 33_554_432, services, "PROTOTYPE_CACHE_INPUT_INVALID"));
  }
  const environmentFiles = new Map();
  for (const asset of [environmentBundle.assets?.panorama, environmentBundle.assets?.collider]) {
    if (!asset?.path) fail("PROTOTYPE_CACHE_INPUT_INVALID");
    environmentFiles.set(asset.path, await readStableFile(environmentDir, asset.path, asset.format === "png" ? 67_108_864 : 33_554_432, services, "PROTOTYPE_CACHE_INPUT_INVALID"));
  }
  return publishPreparedPrototypeRun({ promptBytes, texts, assetText, assetBundle, assetFiles,
    environmentText, environmentBundle, environmentReportText, environmentFiles, runRoot,
    source: "verified-cache", services, assemblePrototypeScene, canonicalizeJsonValue });
}
