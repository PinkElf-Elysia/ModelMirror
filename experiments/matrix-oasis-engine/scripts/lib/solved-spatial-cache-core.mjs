import { createHash } from "node:crypto";
import path from "node:path";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import {
  validatePrototypeEnvironmentFactsJson,
  validatePrototypeSpatialIntentJson,
} from "@matrix-oasis/prototype-spatial-planning-contracts";
import { validatePrototypeSpatialSolutionJson } from "@matrix-oasis/prototype-spatial-solution-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";

const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const HASH = /^sha256:[0-9a-f]{64}$/u;
const MODEL = /^[A-Za-z0-9._/-]{1,128}$/u;
const TEXT_LIMITS = Object.freeze({
  "spatial-intent.json": 16_777_216,
  "environment-facts.json": 16_777_216,
  "spatial-solution.json": 16_777_216,
  "spatial-solution-report.json": 262_144,
  "spatial-verification-report.json": 262_144,
  "run-report.json": 262_144,
  "prototype-asset-bundle.json": 262_144,
});
const OVERLAY_NAMES = Object.freeze([
  "spatial-intent.json",
  "environment-facts.json",
  "spatial-solution.json",
  "spatial-solution-report.json",
  "spatial-verification-report.json",
]);

export class SolvedSpatialCacheOperationalError extends Error {
  constructor(code = "SOLVED_SPATIAL_CACHE_INTERNAL_ERROR") {
    super(code);
    this.name = "SolvedSpatialCacheOperationalError";
    this.code = code;
  }
}

function fail(code) { throw new SolvedSpatialCacheOperationalError(code); }
function sha256(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }
function encode(value) { return new TextEncoder().encode(value); }
function equalBytes(left, right) {
  return left.length === right.length && left.every((byte, index) => byte === right[index]);
}
function exact(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key));
}
function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}
function directChild(root, candidate) { return path.dirname(candidate) === root; }
function identity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint"
    ? Object.freeze({ dev: stat.dev, ino: stat.ino }) : null;
}
function state(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? Object.freeze({ size: stat.size, mtimeNs: stat.mtimeNs, ctimeNs: stat.ctimeNs }) : null;
}
function sameIdentity(stat, expected) { return expected && stat.dev === expected.dev && stat.ino === expected.ino; }
function sameState(stat, expected) {
  return expected && stat.size === expected.size && stat.mtimeNs === expected.mtimeNs && stat.ctimeNs === expected.ctimeNs;
}
function compareText(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
function validReport(report) {
  return report && report.reportVersion === 1 && report.valid === true && Array.isArray(report.diagnostics) && report.diagnostics.length === 0;
}
function canonical(text, canonicalizeJsonValue, code) {
  try {
    const value = JSON.parse(text);
    if (canonicalizeJsonValue(value) !== text) fail(code);
    return value;
  } catch (error) {
    if (error instanceof SolvedSpatialCacheOperationalError) throw error;
    fail(code);
  }
}
function decode(bytes, code) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { fail(code); }
}

async function exists(candidate, services) {
  try { await services.lstat(candidate, { bigint: true }); return true; }
  catch (error) { if (error?.code === "ENOENT") return false; throw error; }
}

async function trustedDirectory(candidate, parent, services, code) {
  try {
    const absolute = path.resolve(candidate);
    const real = path.resolve(await services.realpath(absolute));
    const stat = await services.lstat(absolute, { bigint: true });
    const observed = identity(stat);
    if (real !== absolute || !contained(parent, real) || !stat.isDirectory() || stat.isSymbolicLink() || !observed) fail(code);
    return Object.freeze({ path: absolute, identity: observed });
  } catch (error) {
    if (error instanceof SolvedSpatialCacheOperationalError) throw error;
    fail(code);
  }
}

async function assertDirectory(directory, parent, services, code) {
  const current = await trustedDirectory(directory.path, parent, services, code);
  if (current.identity.dev !== directory.identity.dev || current.identity.ino !== directory.identity.ino) fail(code);
}

async function readStableFile(directory, name, maximum, services, code) {
  if (typeof name !== "string" || name.length < 1 || name.includes("/") || name.includes("\\") ||
      !Number.isSafeInteger(maximum) || maximum < 1) fail(code);
  const candidate = path.join(directory.path, name);
  let handle;
  try {
    await assertDirectory(directory, path.dirname(directory.path), services, code);
    handle = await services.openFile(candidate, "r");
    const before = await handle.stat({ bigint: true });
    const observed = identity(before); const capturedState = state(before);
    if (!before.isFile() || before.isSymbolicLink() || !observed || !capturedState || before.size < 1n || before.size > BigInt(maximum)) fail(code);
    const linked = await services.lstat(candidate, { bigint: true });
    const real = path.resolve(await services.realpath(candidate));
    if (real !== candidate || linked.isSymbolicLink() || !linked.isFile() || !sameIdentity(linked, observed) || !sameState(linked, capturedState)) fail(code);
    const output = new Uint8Array(Number(before.size)); let offset = 0;
    while (offset < output.length) {
      const result = await handle.read(output, offset, output.length - offset, offset);
      if (!result || result.bytesRead < 1) fail(code);
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, output.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameIdentity(after, observed) || !sameState(after, capturedState)) fail(code);
    return output;
  } catch (error) {
    if (error instanceof SolvedSpatialCacheOperationalError) throw error;
    fail(code);
  } finally {
    if (handle) try { await handle.close(); } catch { /* preserve primary result */ }
  }
}

async function readAssetBundle(sourceOptions, runId, services, canonicalizeJsonValue) {
  const temp = path.resolve(await services.realpath(sourceOptions.temporaryRoot));
  const root = await trustedDirectory(sourceOptions.prototypeRunRoot, temp, services, "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  const runs = await trustedDirectory(path.join(root.path, "runs"), root.path, services, "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  const run = await trustedDirectory(path.join(runs.path, runId), runs.path, services, "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  const text = decode(await readStableFile(run, "prototype-asset-bundle.json", TEXT_LIMITS["prototype-asset-bundle.json"], services,
    "SOLVED_SPATIAL_CACHE_SOURCE_INVALID"), "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  if (!validReport(validatePrototypeAssetBundleJson(text))) fail("SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  canonical(text, canonicalizeJsonValue, "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  return text;
}

function solverReportValid(value, texts, solution) {
  return exact(value, ["format", "formatVersion", "source", "solutionSha256", "componentIndex", "zoneCount",
    "placementCount", "nodeContextCount", "candidateCount", "expandedStates", "deterministic"]) &&
    value.format === "matrix-oasis.prototype-spatial-solution-report" && value.formatVersion === "0.1.0" &&
    value.deterministic === true && value.solutionSha256 === sha256(texts["spatial-solution.json"]) &&
    exact(value.source, ["spatialIntentSha256", "environmentFactsSha256", "runtimeReceiptSha256", "assetBundleSha256"]) &&
    value.source.spatialIntentSha256 === sha256(texts["spatial-intent.json"]) &&
    value.source.environmentFactsSha256 === sha256(texts["environment-facts.json"]) &&
    value.source.runtimeReceiptSha256 === solution.source.runtimeReceiptSha256 &&
    value.source.assetBundleSha256 === solution.source.assetBundle.canonicalSha256 &&
    value.componentIndex === solution.navigation.componentIndex && value.zoneCount === solution.navigation.zoneSeeds.length &&
    value.placementCount === solution.placements.length && value.nodeContextCount === solution.nodeContexts.length &&
    value.candidateCount === solution.metrics.candidateCount && value.expandedStates === solution.metrics.expandedStates;
}

function verificationReportValid(value, solutionText, solution) {
  const terminalCount = solution.nodeContexts.reduce((sum, context) => sum + context.actionTerminal.actionCount, 0);
  return exact(value, ["format", "formatVersion", "solutionSha256", "evidenceSha256", "verifier", "checks"]) &&
    value.format === "matrix-oasis.prototype-spatial-verification-report" && value.formatVersion === "0.1.0" &&
    value.solutionSha256 === sha256(solutionText) && HASH.test(value.evidenceSha256) &&
    exact(value.verifier, ["id", "version", "godotVersion"]) && value.verifier.id === "godot-spatial-solution-verifier" &&
    value.verifier.version === "0.1.0-r14" && value.verifier.godotVersion === "4.6.3" &&
    exact(value.checks, ["placementCount", "nodeContextCount", "pathCount", "terminalCount"]) &&
    value.checks.placementCount === solution.placements.length && value.checks.nodeContextCount === solution.nodeContexts.length &&
    value.checks.pathCount === solution.nodeContexts.length && value.checks.terminalCount === terminalCount;
}

async function validateArtifacts({ artifacts, source, assetBundleJson, canonicalizeJsonValue }) {
  if (!exact(artifacts, ["spatialIntentJson", "environmentFactsJson", "spatialSolutionJson",
    "spatialSolutionReportJson", "spatialVerificationReportJson"]) ||
      Object.values(artifacts).some((value) => typeof value !== "string")) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  const texts = Object.freeze({
    "spatial-intent.json": artifacts.spatialIntentJson,
    "environment-facts.json": artifacts.environmentFactsJson,
    "spatial-solution.json": artifacts.spatialSolutionJson,
    "spatial-solution-report.json": artifacts.spatialSolutionReportJson,
    "spatial-verification-report.json": artifacts.spatialVerificationReportJson,
  });
  for (const [name, text] of Object.entries(texts)) {
    if (encode(text).byteLength > TEXT_LIMITS[name]) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
    canonical(text, canonicalizeJsonValue, "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  }
  if (!validReport(validatePrototypeSpatialIntentJson(texts["spatial-intent.json"])) ||
      !validReport(validatePrototypeEnvironmentFactsJson(texts["environment-facts.json"])) ||
      !validReport(validatePrototypeSpatialSolutionJson(texts["spatial-solution.json"]))) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  const intent = JSON.parse(texts["spatial-intent.json"]);
  const facts = JSON.parse(texts["environment-facts.json"]);
  const solution = JSON.parse(texts["spatial-solution.json"]);
  const solverReport = JSON.parse(texts["spatial-solution-report.json"]);
  const verificationReport = JSON.parse(texts["spatial-verification-report.json"]);
  const runtimeText = new TextDecoder().decode(source.previewFiles.get("runtime-game-pack.json"));
  const receiptText = new TextDecoder().decode(source.previewFiles.get("runtime-receipt.json"));
  const assemblyText = new TextDecoder().decode(source.previewFiles.get("spatial-assembly.json"));
  if (!validReport(await validateRuntimeGamePackJson(runtimeText, receiptText))) fail("SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  const runtime = canonical(runtimeText, canonicalizeJsonValue, "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  const receipt = canonical(receiptText, canonicalizeJsonValue, "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  canonical(assemblyText, canonicalizeJsonValue, "SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  const sourceMatches = solution.source.spatialIntent.canonicalSha256 === sha256(texts["spatial-intent.json"]) &&
    solution.source.environmentFacts.canonicalSha256 === sha256(texts["environment-facts.json"]) &&
    solution.source.assetBundle.canonicalSha256 === sha256(assetBundleJson) &&
    solution.source.runtimeReceiptSha256 === sha256(receiptText) &&
    solution.source.runtime.id === runtime.source.id && solution.source.runtime.contentVersion === runtime.source.contentVersion &&
    solution.source.runtime.sourceSha256 === `sha256:${runtime.source.canonicalSha256}` &&
    solution.source.runtime.artifactSha256 === `sha256:${receipt.artifact.sha256}` &&
    solution.source.analysisTransformSource.profile === "spatial-assembly-collider-v1" &&
    solution.source.analysisTransformSource.canonicalSha256 === sha256(assemblyText) &&
    facts.source.analysisTransform.profile === "spatial-assembly-collider-v1" &&
    facts.source.analysisTransform.sourceCanonicalSha256 === sha256(assemblyText) &&
    intent.scene.id === runtime.source.id && intent.scene.contentVersion === runtime.source.contentVersion;
  if (!sourceMatches || !solverReportValid(solverReport, texts, solution) ||
      !verificationReportValid(verificationReport, texts["spatial-solution.json"], solution)) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  return Object.freeze({ texts, solution, solutionSha256: sha256(texts["spatial-solution.json"]) });
}

function artifactsFromTexts(texts) {
  return OVERLAY_NAMES.map((name) => Object.freeze({ path: name, bytes: encode(texts[name]) }));
}

async function writeHandle(handle, record, services, code) {
  const opened = await handle.stat({ bigint: true }); const observed = identity(opened);
  const linked = await services.lstat(record.path, { bigint: true }); const real = path.resolve(await services.realpath(record.path));
  if (!observed || !sameIdentity(linked, observed) || !opened.isFile() || linked.isSymbolicLink() || real !== record.path) fail(code);
  await handle.writeFile(record.bytes); await handle.sync();
  const output = new Uint8Array(record.bytes.length); let offset = 0;
  while (offset < output.length) {
    const result = await handle.read(output, offset, output.length - offset, offset);
    if (!result || result.bytesRead < 1) fail(code);
    offset += result.bytesRead;
  }
  if (!equalBytes(output, record.bytes)) fail(code);
  record.identity = observed;
}

async function publishDirectory(root, runId, solutionHex, artifacts, services) {
  const runsPath = path.join(root.path, "solved-runs");
  if (!(await exists(runsPath, services))) await services.mkdir(runsPath, { recursive: false });
  const runs = await trustedDirectory(runsPath, root.path, services, "SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
  const sourcePath = path.join(runs.path, runId);
  if (!(await exists(sourcePath, services))) await services.mkdir(sourcePath, { recursive: false });
  const source = await trustedDirectory(sourcePath, runs.path, services, "SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
  const target = path.join(source.path, solutionHex);
  if (await exists(target, services)) fail("SOLVED_SPATIAL_CACHE_RUN_EXISTS");
  let stage; let stageRecord; const records = []; const handles = [];
  try {
    stage = await services.mkdtemp(path.join(source.path, ".s-"));
    stageRecord = await trustedDirectory(stage, source.path, services, "SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
    for (const artifact of artifacts) {
      const candidate = path.join(stageRecord.path, artifact.path);
      const handle = await services.openFile(candidate, "wx+"); handles.push(handle);
      records.push({ path: candidate, bytes: artifact.bytes, handle, identity: null });
    }
    for (const record of records) {
      await assertDirectory(stageRecord, source.path, services, "SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
      await writeHandle(record.handle, record, services, "SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
    }
    for (const record of records) { await record.handle.close(); handles.splice(handles.indexOf(record.handle), 1); }
    await assertDirectory(stageRecord, source.path, services, "SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
    await services.rename(stageRecord.path, target); stage = undefined;
    const final = Object.freeze({ path: target, identity: stageRecord.identity });
    await assertDirectory(final, source.path, services, "SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
    for (const artifact of artifacts) {
      const bytes = await readStableFile(final, artifact.path, artifact.bytes.length, services, "SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
      if (!equalBytes(bytes, artifact.bytes)) fail("SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
    }
  } catch (error) {
    if (error instanceof SolvedSpatialCacheOperationalError) throw error;
    fail("SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
  } finally {
    for (const handle of handles) try { await handle.close(); } catch { /* preserve result */ }
    if (stage && stageRecord) {
      try {
        await assertDirectory(stageRecord, source.path, services, "SOLVED_SPATIAL_CACHE_PUBLISH_FAILED");
        for (const record of records) {
          if (!record.identity) continue;
          const stat = await services.lstat(record.path, { bigint: true });
          if (sameIdentity(stat, record.identity) && stat.isFile() && !stat.isSymbolicLink()) await services.rm(record.path, { recursive: false, force: false });
        }
        await services.rmdir(stageRecord.path);
      } catch { /* ambiguous staging is intentionally retained */ }
    }
  }
}

async function publishCurrent(root, runId, solutionSha256, services, canonicalizeJsonValue) {
  const text = canonicalizeJsonValue({ format: "matrix-oasis.prototype-solved-current", formatVersion: "0.1.0", runId, solutionSha256 });
  const temporary = path.join(root.path, `.solved-current-${process.pid}-${Date.now()}.tmp`);
  let handle; let observed;
  try {
    handle = await services.openFile(temporary, "wx+");
    const record = { path: temporary, bytes: encode(text), handle, identity: null };
    await writeHandle(handle, record, services, "SOLVED_SPATIAL_CACHE_CURRENT_FAILED"); observed = record.identity;
    await handle.close(); handle = null;
    await assertDirectory(root, path.dirname(root.path), services, "SOLVED_SPATIAL_CACHE_CURRENT_FAILED");
    const target = path.join(root.path, "solved-current.json");
    if (await exists(target, services)) {
      const linked = await services.lstat(target, { bigint: true }); const real = path.resolve(await services.realpath(target));
      if (!linked.isFile() || linked.isSymbolicLink() || real !== target) fail("SOLVED_SPATIAL_CACHE_CURRENT_FAILED");
    }
    await services.rename(temporary, target);
    const linked = await services.lstat(target, { bigint: true }); const real = path.resolve(await services.realpath(target));
    if (!sameIdentity(linked, observed) || !linked.isFile() || linked.isSymbolicLink() || real !== target) fail("SOLVED_SPATIAL_CACHE_CURRENT_FAILED");
    const verified = await readStableFile(root, "solved-current.json", 4096, services, "SOLVED_SPATIAL_CACHE_CURRENT_FAILED");
    const after = await services.lstat(target, { bigint: true });
    if (!sameIdentity(after, observed) || !equalBytes(verified, encode(text))) fail("SOLVED_SPATIAL_CACHE_CURRENT_FAILED");
  } finally {
    if (handle) try { await handle.close(); } catch {}
    if (observed) {
      try {
        const stat = await services.lstat(temporary, { bigint: true });
        if (sameIdentity(stat, observed) && stat.isFile() && !stat.isSymbolicLink()) await services.rm(temporary, { recursive: false, force: false });
      } catch { /* renamed or ambiguous */ }
    }
  }
}

async function sourceFor(runId, sourceOptions, services, canonicalizeJsonValue) {
  const source = await sourceOptions.loadVerifiedSpatialPrototypeRun({ runId, ...sourceOptions.cacheOptions });
  if (!source || !(source.previewFiles instanceof Map)) fail("SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  for (const required of ["runtime-game-pack.json", "runtime-receipt.json", "scene-pack.json", "spatial-assembly.json", "assets/environment.compressed.ply"]) {
    if (!(source.previewFiles.get(required) instanceof Uint8Array)) fail("SOLVED_SPATIAL_CACHE_SOURCE_INVALID");
  }
  const assetBundleJson = await readAssetBundle(sourceOptions.cacheOptions, runId, services, canonicalizeJsonValue);
  return Object.freeze({ ...source, assetBundleJson });
}

function reportText({ runId, source, checked, artifacts, canonicalizeJsonValue }) {
  const overlayFiles = artifacts.map((artifact) => ({ path: artifact.path, byteLength: artifact.bytes.length, sha256: sha256(artifact.bytes) }));
  return canonicalizeJsonValue({
    format: "matrix-oasis.prototype-solved-spatial-run-report", formatVersion: "0.1.0", status: "ready",
    source: "verified-spatial-solution", runId, promptSha256: source.promptSha256, model: source.model,
    solutionSha256: checked.solutionSha256,
    spatialIntentSha256: sha256(checked.texts["spatial-intent.json"]),
    environmentFactsSha256: sha256(checked.texts["environment-facts.json"]),
    spatialAssemblySha256: sha256(source.previewFiles.get("spatial-assembly.json")),
    assetBundleSha256: sha256(source.assetBundleJson),
    runtimePackSha256: sha256(source.previewFiles.get("runtime-game-pack.json")),
    runtimeReceiptSha256: sha256(source.previewFiles.get("runtime-receipt.json")),
    verificationReportSha256: sha256(checked.texts["spatial-verification-report.json"]),
    overlayFiles,
  });
}

export async function publishSolvedSpatialPrototypeRun({ runId, runRoot, temporaryRoot, sourceOptions, artifacts,
  services, canonicalizeJsonValue }) {
  try {
    if (!RUN_ID.test(runId) || !path.isAbsolute(runRoot) || !path.isAbsolute(temporaryRoot)) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
    const temp = path.resolve(await services.realpath(temporaryRoot)); const resolved = path.resolve(runRoot);
    if (!directChild(temp, resolved)) fail("SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
    const source = await sourceFor(runId, sourceOptions, services, canonicalizeJsonValue);
    const checked = await validateArtifacts({ artifacts, source, assetBundleJson: source.assetBundleJson, canonicalizeJsonValue });
    if (!(await exists(resolved, services))) await services.mkdir(resolved, { recursive: false });
    const root = await trustedDirectory(resolved, temp, services, "SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
    const overlay = artifactsFromTexts(checked.texts);
    const report = reportText({ runId, source, checked, artifacts: overlay, canonicalizeJsonValue });
    const published = [...overlay, Object.freeze({ path: "run-report.json", bytes: encode(report) })];
    await publishDirectory(root, runId, checked.solutionSha256.slice(7), published, services);
    await publishCurrent(root, runId, checked.solutionSha256, services, canonicalizeJsonValue);
    return Object.freeze({ runId, solutionSha256: checked.solutionSha256, files: published.length });
  } catch (error) {
    if (error instanceof SolvedSpatialCacheOperationalError) throw error;
    fail("SOLVED_SPATIAL_CACHE_INTERNAL_ERROR");
  }
}

async function verifyOverlay(directory, runId, source, services, canonicalizeJsonValue, includeFiles) {
  const artifacts = Object.create(null);
  for (const name of OVERLAY_NAMES) artifacts[name] = decode(await readStableFile(directory, name, TEXT_LIMITS[name], services,
    "SOLVED_SPATIAL_CACHE_INPUT_INVALID"), "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  const checked = await validateArtifacts({ artifacts: {
    spatialIntentJson: artifacts["spatial-intent.json"], environmentFactsJson: artifacts["environment-facts.json"],
    spatialSolutionJson: artifacts["spatial-solution.json"], spatialSolutionReportJson: artifacts["spatial-solution-report.json"],
    spatialVerificationReportJson: artifacts["spatial-verification-report.json"],
  }, source, assetBundleJson: source.assetBundleJson, canonicalizeJsonValue });
  const runReportText = decode(await readStableFile(directory, "run-report.json", TEXT_LIMITS["run-report.json"], services,
    "SOLVED_SPATIAL_CACHE_INPUT_INVALID"), "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  const report = canonical(runReportText, canonicalizeJsonValue, "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  const overlay = artifactsFromTexts(checked.texts);
  const expectedReport = reportText({ runId, source, checked, artifacts: overlay, canonicalizeJsonValue });
  if (runReportText !== expectedReport) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
  const result = { runId, promptSha256: source.promptSha256, model: source.model, solutionSha256: checked.solutionSha256 };
  if (includeFiles) result.previewFiles = new Map([...source.previewFiles, ["spatial-solution.json", encode(artifacts["spatial-solution.json"])],
    ["spatial-verification-report.json", encode(artifacts["spatial-verification-report.json"])]]);
  return Object.freeze(result);
}

async function scan({ runRoot, temporaryRoot, sourceOptions, services, canonicalizeJsonValue }) {
  const temp = path.resolve(await services.realpath(temporaryRoot)); const resolved = path.resolve(runRoot);
  if (!directChild(temp, resolved) || !(await exists(resolved, services))) return Object.freeze({ currentRunId: null, runs: Object.freeze([]) });
  const root = await trustedDirectory(resolved, temp, services, "SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
  const runsPath = path.join(root.path, "solved-runs");
  if (!(await exists(runsPath, services))) return Object.freeze({ currentRunId: null, runs: Object.freeze([]) });
  const runs = await trustedDirectory(runsPath, root.path, services, "SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
  const sourceEntries = await services.readdir(runs.path, { withFileTypes: true });
  if (!Array.isArray(sourceEntries) || sourceEntries.length > 200) fail("SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
  const verified = [];
  for (const sourceEntry of sourceEntries.filter((entry) => RUN_ID.test(entry.name)).sort((a, b) => compareText(a.name, b.name))) {
    if (!sourceEntry.isDirectory() || sourceEntry.isSymbolicLink()) continue;
    try {
      const source = await sourceFor(sourceEntry.name, sourceOptions, services, canonicalizeJsonValue);
      const sourceDirectory = await trustedDirectory(path.join(runs.path, sourceEntry.name), runs.path, services, "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
      const solutions = await services.readdir(sourceDirectory.path, { withFileTypes: true });
      if (!Array.isArray(solutions) || solutions.length > 64) continue;
      for (const entry of solutions.filter((item) => /^[0-9a-f]{64}$/u.test(item.name)).sort((a, b) => compareText(a.name, b.name))) {
        if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
        try {
          const directory = await trustedDirectory(path.join(sourceDirectory.path, entry.name), sourceDirectory.path, services, "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
          const candidate = await verifyOverlay(directory, sourceEntry.name, source, services, canonicalizeJsonValue, false);
          if (candidate.solutionSha256 === `sha256:${entry.name}`) verified.push(candidate);
        } catch { /* invalid solution is ineligible */ }
      }
    } catch { /* invalid source is ineligible */ }
  }
  let current = null;
  if (await exists(path.join(root.path, "solved-current.json"), services)) {
    try {
      const text = decode(await readStableFile(root, "solved-current.json", 4096, services, "SOLVED_SPATIAL_CACHE_INPUT_INVALID"), "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
      const value = canonical(text, canonicalizeJsonValue, "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
      if (exact(value, ["format", "formatVersion", "runId", "solutionSha256"]) &&
          value.format === "matrix-oasis.prototype-solved-current" && value.formatVersion === "0.1.0" &&
          verified.some((item) => item.runId === value.runId && item.solutionSha256 === value.solutionSha256)) current = value;
    } catch { /* invalid pointer does not invalidate history */ }
  }
  const unique = [];
  for (const item of verified) if (!unique.some((entry) => entry.runId === item.runId)) unique.push(item);
  return Object.freeze({ currentRunId: current?.runId ?? null, runs: Object.freeze(unique), currentSolutionSha256: current?.solutionSha256 ?? null });
}

export async function recoverSolvedSpatialPrototypeRuns(options) {
  try { return await scan(options); }
  catch (error) { if (error instanceof SolvedSpatialCacheOperationalError) throw error; fail("SOLVED_SPATIAL_CACHE_INTERNAL_ERROR"); }
}

export async function findVerifiedSolvedSpatialPrototypeRun({ promptSha256, model, ...options }) {
  try {
    if (!HASH.test(promptSha256) || !MODEL.test(model)) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
    const recovered = await recoverSolvedSpatialPrototypeRuns(options);
    const item = recovered.runs.find((run) => run.promptSha256 === promptSha256 && run.model === model);
    return item ? Object.freeze({ ok: true, runId: item.runId }) : Object.freeze({ ok: false });
  } catch (error) { if (error instanceof SolvedSpatialCacheOperationalError) throw error; fail("SOLVED_SPATIAL_CACHE_INTERNAL_ERROR"); }
}

export async function loadVerifiedSolvedSpatialPrototypeRun({ runId, runRoot, temporaryRoot, sourceOptions, services, canonicalizeJsonValue }) {
  try {
    if (!RUN_ID.test(runId)) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
    const recovered = await recoverSolvedSpatialPrototypeRuns({ runRoot, temporaryRoot, sourceOptions, services, canonicalizeJsonValue });
    const summary = recovered.runs.find((run) => run.runId === runId);
    if (!summary) fail("SOLVED_SPATIAL_CACHE_INPUT_INVALID");
    const source = await sourceFor(runId, sourceOptions, services, canonicalizeJsonValue);
    const temp = path.resolve(await services.realpath(temporaryRoot)); const root = await trustedDirectory(runRoot, temp, services, "SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
    const runs = await trustedDirectory(path.join(root.path, "solved-runs"), root.path, services, "SOLVED_SPATIAL_CACHE_RUN_ROOT_INVALID");
    const sourceDirectory = await trustedDirectory(path.join(runs.path, runId), runs.path, services, "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
    const solutionHex = (recovered.currentRunId === runId ? recovered.currentSolutionSha256 : summary.solutionSha256).slice(7);
    const directory = await trustedDirectory(path.join(sourceDirectory.path, solutionHex), sourceDirectory.path, services, "SOLVED_SPATIAL_CACHE_INPUT_INVALID");
    return await verifyOverlay(directory, runId, source, services, canonicalizeJsonValue, true);
  } catch (error) { if (error instanceof SolvedSpatialCacheOperationalError) throw error; fail("SOLVED_SPATIAL_CACHE_INTERNAL_ERROR"); }
}
