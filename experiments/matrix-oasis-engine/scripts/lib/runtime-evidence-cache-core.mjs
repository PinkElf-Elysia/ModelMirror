import { createHash } from "node:crypto";
import { lstat, mkdir, mkdtemp, open, readFile, realpath, readdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  validatePrototypeRuntimeEvidenceJson,
  validatePrototypeRuntimeReplayPlanJson,
} from "@matrix-oasis/prototype-runtime-evidence-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const RUN_ID = /^[0-9a-f]{64}$/u;
const MEDIA_PATH = /^media\/(?:replay-[0-9]{4}-checkpoint-[0-9]{4}\.png|full-run\.ogv)$/u;
const ASSET_PATH = /^assets\/[A-Za-z0-9._-]+$/u;
const PREVIEW_FILES = new Set([
  "runtime-game-pack.json",
  "runtime-receipt.json",
  "environment-facts.json",
  "spatial-intent.json",
  "prototype-asset-bundle.json",
  "spatial-solution.json",
  "spatial-verification-report.json",
  "scene-pack.json",
  "spatial-assembly.json",
]);
const REQUIRED_PREVIEW_FILES = Object.freeze([
  ...PREVIEW_FILES,
  "assets/environment.compressed.ply",
  "assets/environment-collider.glb",
]);
const MAX_FILE_BYTES = 256 * 1024 * 1024;
const defaultServices = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readFile, realpath, readdir, rename, rm, writeFile });

function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}
function digest(bytes) { return `sha256:${createHash("sha256").update(bytes).digest("hex")}`; }
function validPreviewPath(relative) { return PREVIEW_FILES.has(relative) || ASSET_PATH.test(relative); }
function canonical(text) {
  const value = JSON.parse(text);
  if (canonicalizeJsonValue(value) !== text) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  return value;
}
function validateInputs(options) {
  if (!options || !RUN_ID.test(options.runId) || typeof options.replayPlanJson !== "string" ||
      typeof options.canonicalEvidenceJson !== "string" || !(options.mediaFiles instanceof Map) ||
      !(options.previewFiles instanceof Map)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  if (!validatePrototypeRuntimeReplayPlanJson(options.replayPlanJson).valid ||
      !validatePrototypeRuntimeEvidenceJson(options.canonicalEvidenceJson).valid) {
    throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  }
  const plan = canonical(options.replayPlanJson);
  const evidence = canonical(options.canonicalEvidenceJson);
  if (evidence.replayPlanSha256 !== digest(Buffer.from(options.replayPlanJson, "utf8")) ||
      JSON.stringify(evidence.identity) !== JSON.stringify(plan.identity)) {
    throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  }
  for (const required of REQUIRED_PREVIEW_FILES) {
    if (!(options.previewFiles.get(required) instanceof Uint8Array)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  }
  const identityFiles = Object.freeze({
    runtimePackSha256: "runtime-game-pack.json",
    runtimeReceiptSha256: "runtime-receipt.json",
    environmentFactsSha256: "environment-facts.json",
    spatialIntentSha256: "spatial-intent.json",
    assetBundleSha256: "prototype-asset-bundle.json",
    spatialSolutionSha256: "spatial-solution.json",
    spatialVerificationSha256: "spatial-verification-report.json",
  });
  for (const [field, relative] of Object.entries(identityFiles)) {
    if (digest(options.previewFiles.get(relative)) !== plan.identity[field]) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  }
  if (options.previewFiles.size < REQUIRED_PREVIEW_FILES.length || options.previewFiles.size > 32) {
    throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  }
  for (const [relative, bytes] of options.previewFiles) {
    if (!validPreviewPath(relative) || !(bytes instanceof Uint8Array)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  }
  const expectedScreenshotHashes = evidence.media.screenshots.map((item) => item.sha256).sort();
  const actualScreenshotHashes = [];
  for (const [relative, bytes] of options.mediaFiles) {
    if (!MEDIA_PATH.test(relative) || !(bytes instanceof Uint8Array)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
    if (relative.endsWith(".png")) actualScreenshotHashes.push(digest(bytes));
  }
  if (JSON.stringify(actualScreenshotHashes.sort()) !== JSON.stringify(expectedScreenshotHashes)) {
    throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  }
  const video = options.mediaFiles.get("media/full-run.ogv");
  if (!(video instanceof Uint8Array) || evidence.media.videos.length !== 1 ||
      digest(video) !== evidence.media.videos[0].sha256) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
}
async function trustedRoot(runRoot, temporaryRoot, services, create) {
  const temp = path.resolve(await services.realpath(temporaryRoot));
  const root = path.resolve(runRoot);
  if (path.dirname(root) !== temp) throw new Error("R15_EVIDENCE_CACHE_PATH_INVALID");
  if (create) await services.mkdir(root, { recursive: true });
  const resolved = path.resolve(await services.realpath(root));
  const stat = await services.lstat(root, { bigint: true });
  if (resolved !== root || stat.isSymbolicLink() || !stat.isDirectory()) throw new Error("R15_EVIDENCE_CACHE_PATH_INVALID");
  return root;
}
function sameIdentity(left, right) {
  return left?.dev === right?.dev && left?.ino === right?.ino && left?.size === right?.size && left?.mtimeNs === right?.mtimeNs;
}
async function readStableFile(candidate, services, encoding = null) {
  const absolute = path.resolve(candidate);
  const handle = await services.openFile(absolute, "r");
  try {
    const opened = await handle.stat({ bigint: true });
    const linked = await services.lstat(absolute, { bigint: true });
    const resolved = path.resolve(await services.realpath(absolute));
    if (!opened.isFile() || linked.isSymbolicLink() || resolved !== absolute || !sameIdentity(opened, linked) ||
        opened.size < 0n || opened.size > BigInt(MAX_FILE_BYTES)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
    const value = await handle.readFile(encoding ? { encoding } : undefined);
    const after = await handle.stat({ bigint: true });
    if (!sameIdentity(opened, after)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
    return value;
  } finally { await handle.close(); }
}
async function readVerifiedRun(directory, runId, services, includeFiles) {
  const planJson = await readStableFile(path.join(directory, "runtime-replay-plan.json"), services, "utf8");
  const evidenceJson = await readStableFile(path.join(directory, "runtime-evidence.json"), services, "utf8");
  const previewFiles = new Map();
  for (const name of PREVIEW_FILES) previewFiles.set(name, new Uint8Array(await readStableFile(path.join(directory, "preview", name), services)));
  const assets = await services.readdir(path.join(directory, "preview", "assets"), { withFileTypes: true });
  for (const entry of assets) {
    if (!entry.isFile() || entry.isSymbolicLink() || !ASSET_PATH.test(`assets/${entry.name}`)) {
      throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
    }
    previewFiles.set(`assets/${entry.name}`, new Uint8Array(await readStableFile(path.join(directory, "preview", "assets", entry.name), services)));
  }
  const mediaFiles = new Map();
  const media = await services.readdir(path.join(directory, "media"), { withFileTypes: true });
  for (const entry of media) {
    const relative = `media/${entry.name}`;
    if (!entry.isFile() || entry.isSymbolicLink() || !MEDIA_PATH.test(relative)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
    mediaFiles.set(relative, new Uint8Array(await readStableFile(path.join(directory, "media", entry.name), services)));
  }
  validateInputs({ runId, replayPlanJson: planJson, canonicalEvidenceJson: evidenceJson, mediaFiles, previewFiles });
  const result = { runId, replayPlanJson: planJson, canonicalEvidenceJson: evidenceJson };
  if (includeFiles) Object.assign(result, { mediaFiles, previewFiles });
  return Object.freeze(result);
}

export async function publishRuntimeEvidenceRun(options, services = defaultServices) {
  validateInputs(options);
  const runRoot = await trustedRoot(options.runRoot, options.temporaryRoot, services, true);
  const runs = path.join(runRoot, "runs");
  await services.mkdir(runs, { recursive: true });
  const finalPath = path.join(runs, options.runId);
  let staging = await services.mkdtemp(path.join(runs, `.${options.runId}-`));
  try {
    if (!contained(runRoot, staging)) throw new Error("R15_EVIDENCE_CACHE_PATH_INVALID");
    await services.mkdir(path.join(staging, "media"));
    await services.mkdir(path.join(staging, "preview"));
    await services.mkdir(path.join(staging, "preview", "assets"));
    await services.writeFile(path.join(staging, "runtime-replay-plan.json"), options.replayPlanJson, { encoding: "utf8", flag: "wx" });
    await services.writeFile(path.join(staging, "runtime-evidence.json"), options.canonicalEvidenceJson, { encoding: "utf8", flag: "wx" });
    for (const [relative, bytes] of options.mediaFiles) await services.writeFile(path.join(staging, ...relative.split("/")), bytes, { flag: "wx" });
    for (const [relative, bytes] of options.previewFiles) await services.writeFile(path.join(staging, "preview", ...relative.split("/")), bytes, { flag: "wx" });
    await services.rename(staging, finalPath);
    staging = null;
    await readVerifiedRun(finalPath, options.runId, services, false);
    const currentTemp = path.join(runRoot, `.current-${options.runId}.json`);
    const current = path.join(runRoot, "current.json");
    await services.writeFile(currentTemp, canonicalizeJsonValue({
      format: "matrix-oasis.prototype-runtime-evidence-current",
      formatVersion: "0.1.0",
      runId: options.runId,
    }), { encoding: "utf8", flag: "wx" });
    await services.rename(currentTemp, current);
    return Object.freeze({ runId: options.runId, runDirectory: finalPath });
  } finally {
    if (staging) await services.rm(staging, { recursive: true, force: true }).catch(() => {});
  }
}

export async function loadVerifiedRuntimeEvidenceRun({ runRoot, temporaryRoot, runId, includeFiles = true }, services = defaultServices) {
  if (!RUN_ID.test(runId)) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
  const root = await trustedRoot(runRoot, temporaryRoot, services, false);
  const directory = path.join(root, "runs", runId);
  const resolved = path.resolve(await services.realpath(directory));
  const stat = await services.lstat(directory, { bigint: true });
  if (resolved !== directory || stat.isSymbolicLink() || !stat.isDirectory() || !contained(root, directory)) {
    throw new Error("R15_EVIDENCE_CACHE_PATH_INVALID");
  }
  return await readVerifiedRun(directory, runId, services, includeFiles);
}

export async function recoverRuntimeEvidenceRuns({ runRoot, temporaryRoot }, services = defaultServices) {
  try {
    const root = await trustedRoot(runRoot, temporaryRoot, services, false);
    const entries = await services.readdir(path.join(root, "runs"), { withFileTypes: true });
    if (entries.length > 64) throw new Error("R15_EVIDENCE_CACHE_INPUT_INVALID");
    const runs = [];
    for (const entry of entries.filter((item) => RUN_ID.test(item.name)).sort((a, b) => a.name.localeCompare(b.name))) {
      if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
      try { runs.push(await readVerifiedRun(path.join(root, "runs", entry.name), entry.name, services, false)); } catch { /* ineligible */ }
    }
    let currentRunId = null;
    try {
      const currentText = await readStableFile(path.join(root, "current.json"), services, "utf8");
      const current = canonical(currentText);
      if (current.format === "matrix-oasis.prototype-runtime-evidence-current" && current.formatVersion === "0.1.0" &&
          runs.some((item) => item.runId === current.runId)) currentRunId = current.runId;
    } catch { /* invalid pointer does not invalidate history */ }
    return Object.freeze({ currentRunId, runs: Object.freeze(runs) });
  } catch (error) {
    if (error?.code === "ENOENT") return Object.freeze({ currentRunId: null, runs: Object.freeze([]) });
    throw error;
  }
}
