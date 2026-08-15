import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import {
  MESHY_PROVIDER_ENDPOINT,
  createMeshyTextTo3DProvider,
  materializePrototypeAssetBundle,
  planPrototypeAssets,
  validatePrototypeAssetBundleJson,
} from "@matrix-oasis/prototype-asset-pipeline";
import {
  MARBLE_PROVIDER_ENDPOINT,
  createMarbleWorldProvider,
  listMarbleWorlds,
  materializeRecoveredPrototypeEnvironmentWithSpatialSource,
  materializePrototypeEnvironmentWithSpatialSource,
  planPrototypeEnvironment,
  recoverMarbleEnvironmentWithSpatialSource,
  validatePrototypeEnvironmentBundleJson,
  validatePrototypeSpatialSourceBundleJson,
} from "@matrix-oasis/prototype-environment-pipeline";
import { createOpenAICompatibleProvider, generatePrototype } from "@matrix-oasis/prototype-generator";
import {
  materializePrototypeSpatialEnvironmentFromSource,
} from "@matrix-oasis/prototype-spatial-environment";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { findVerifiedPrototypeRun, publishPrototypeRun, recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import { createPrototypeHost, PROTOTYPE_HOST_MARKER } from "./lib/prototype-host-core.mjs";
import { createR12PrototypeOperations, validateR12AssetApprovalSummary } from "./lib/r12-host-core.mjs";
import {
  R12_LAST_TRAIN_ACCEPTANCE_PROFILE,
  analyzeR12QualificationCandidate,
} from "./lib/r12-qualification-core.mjs";
import {
  findVerifiedSpatialPrototypeRun,
  loadVerifiedSpatialPrototypeRun,
  publishSpatialPrototypeRun,
  recoverSpatialPrototypeRuns,
} from "./lib/spatial-cache-core.mjs";
import { resolveGodotBinary } from "./lib/godot-core.mjs";
import { createSpatialPrototypeOperations, loadCreatorWebAssets } from "./preview-spatial-prototype.mjs";

const temporaryRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });
const allowedMarbleAssetHosts = Object.freeze([
  "assets.worldlabs.ai", "cdn.marble.worldlabs.ai", "cdn.worldlabs.ai", "storage.googleapis.com",
]);
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const R12_ENVIRONMENT_PLAN_OPTIONS = Object.freeze({
  profile: "matrix-oasis.prototype-environment/2",
});
const CHECKPOINT_FORMAT = "matrix-oasis.r12-acquisition-checkpoint";
const CHECKPOINT_VERSION = "0.1.0";
const ACQUISITION_CHECKPOINT_KEY_VERSION = 2;
const CHECKPOINT_DIRECTORY = "checkpoints";
const PENDING_FORMAT = "matrix-oasis.r12-pending-generation";
const PENDING_VERSION = "0.1.0";
const PENDING_DIRECTORY = "pending-generations";
const ENVIRONMENT_CHECKPOINT_FORMAT = "matrix-oasis.r12-environment-checkpoint";
const ENVIRONMENT_CHECKPOINT_VERSION = "0.1.0";
const ENVIRONMENT_CHECKPOINT_DIRECTORY = "environment-checkpoints";
const CHECKPOINT_FILE_LIMIT = 134_217_728;
const CHECKPOINT_TOTAL_LIMIT = 402_653_184;
const CHECKPOINT_FILE_COUNT_LIMIT = 64;
const SAFE_CHECKPOINT_KEY = /^[0-9a-f]{64}$/u;
const SAFE_MODEL = /^[A-Za-z0-9._/-]{1,128}$/u;
const SAFE_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;

function prototypeAssetPlanInput(artifacts) {
  return Object.freeze({
    authoringGamePackJson: artifacts.authoringGamePackJson,
    sceneBlueprintJson: artifacts.sceneBlueprintJson,
    runtimeGamePackJson: artifacts.runtimeGamePackJson,
    runtimeReceiptJson: artifacts.runtimeReceiptJson,
  });
}

export const R12_HOST_MARKER = "MATRIX_OASIS_R12_PROTOTYPE_HOST";

export function parseR12PreviewArguments(args) {
  if (!Array.isArray(args) || ![2, 4].includes(args.length) || args[0] !== "--run-root" ||
      typeof args[1] !== "string" || args[1].includes("\0") || !path.isAbsolute(args[1])) {
    throw new Error("R12_HOST_ARGUMENT_INVALID");
  }
  const prototypeRunRoot = path.resolve(args[1]);
  if (path.dirname(prototypeRunRoot) !== temporaryRoot || prototypeRunRoot.endsWith("-spatial")) {
    throw new Error("R12_HOST_ARGUMENT_INVALID");
  }
  let port = 43_110;
  if (args.length === 4) {
    if (args[2] !== "--port" || !/^[0-9]{4,5}$/u.test(args[3])) throw new Error("R12_HOST_ARGUMENT_INVALID");
    port = Number(args[3]);
    if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) throw new Error("R12_HOST_ARGUMENT_INVALID");
  }
  return Object.freeze({ prototypeRunRoot, spatialRunRoot: `${prototypeRunRoot}-spatial`, ...(args.length === 4 ? { port } : {}) });
}

const sha256 = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const sameIdentity = (left, right) => left.dev === right.dev && left.ino === right.ino && left.size === right.size;
const sameNode = (left, right) => left.dev === right.dev && left.ino === right.ino;

function exactKeys(value, names) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === names.length &&
    names.every((name) => Object.prototype.hasOwnProperty.call(value, name));
}

function validPendingApproval(value) {
  if (!validateR12AssetApprovalSummary(value) || !exactKeys(value, ["blueprintSha256", "marble", "meshy"]) ||
      !exactKeys(value.marble, ["model", "environmentPrompt", "recovered", "maxCreates", "maxPolls", "maxDownloads", "creditLimit", "usdLimitCents"]) ||
      typeof value.marble.environmentPrompt !== "string" || value.marble.environmentPrompt.length < 1 ||
      new TextEncoder().encode(value.marble.environmentPrompt).length > 32_768 ||
      !exactKeys(value.meshy, ["model", "briefs", "maxTasks", "creditLimit"])) return false;
  return value.meshy.briefs.every((brief) => exactKeys(brief, ["id", "kind", "prompt"]) &&
    typeof brief.id === "string" && brief.id.length > 0 && brief.id.length <= 128 &&
    ["prop", "character-placeholder"].includes(brief.kind) && typeof brief.prompt === "string" &&
    brief.prompt.length > 0 && new TextEncoder().encode(brief.prompt).length <= 32_768);
}

function safeRelativePath(value) {
  if (typeof value !== "string" || value.length < 1 || value.length > 512 || value.includes("\\") ||
      value.startsWith("/") || value.includes("\0")) return false;
  const parts = value.split("/");
  return parts.every((part) => part.length > 0 && part !== "." && part !== ".." && /^[A-Za-z0-9._-]+$/u.test(part));
}

function checkpointKey(promptSha256, model) {
  if (!/^sha256:[0-9a-f]{64}$/u.test(promptSha256) || !SAFE_MODEL.test(model)) throw new Error("R12_CHECKPOINT_INVALID");
  return sha256(new TextEncoder().encode(canonicalizeJsonValue({
    format: CHECKPOINT_FORMAT,
    formatVersion: CHECKPOINT_VERSION,
    model,
    promptSha256,
  }))).slice(7);
}

function acquisitionCheckpointKey(promptSha256, model, blueprintSha256) {
  if (!/^sha256:[0-9a-f]{64}$/u.test(promptSha256) || !SAFE_MODEL.test(model) ||
      !/^sha256:[0-9a-f]{64}$/u.test(blueprintSha256)) throw new Error("R12_CHECKPOINT_INVALID");
  return sha256(new TextEncoder().encode(canonicalizeJsonValue({
    blueprintSha256,
    format: CHECKPOINT_FORMAT,
    formatVersion: CHECKPOINT_VERSION,
    keyVersion: ACQUISITION_CHECKPOINT_KEY_VERSION,
    model,
    promptSha256,
  }))).slice(7);
}

function checkpointSnapshot(prompt, suppliedPromptSha256, suppliedModel, artifacts, acquisition) {
  const promptBytes = typeof prompt === "string" ? new TextEncoder().encode(prompt) : null;
  if (promptBytes !== null && (promptBytes.length < 1 || promptBytes.length > 32_768 || prompt.trim().length < 1 ||
      new TextDecoder("utf-8", { fatal: true }).decode(promptBytes) !== prompt)) throw new Error("R12_CHECKPOINT_INVALID");
  if (artifacts === null || typeof artifacts !== "object" || acquisition === null || typeof acquisition !== "object") {
    throw new Error("R12_CHECKPOINT_INVALID");
  }
  let generationReport;
  try { generationReport = JSON.parse(artifacts.generationReportJson); }
  catch { throw new Error("R12_CHECKPOINT_INVALID"); }
  if (generationReport === null || typeof generationReport !== "object" || !SAFE_MODEL.test(generationReport.model)) {
    throw new Error("R12_CHECKPOINT_INVALID");
  }
  const model = suppliedModel ?? generationReport.model;
  const promptSha256 = promptBytes === null ? suppliedPromptSha256 : sha256(promptBytes);
  const blueprintBytes = typeof artifacts.sceneBlueprintJson === "string"
    ? new TextEncoder().encode(artifacts.sceneBlueprintJson)
    : null;
  const blueprintSha256 = blueprintBytes === null ? null : sha256(blueprintBytes);
  if (model !== generationReport.model || !/^sha256:[0-9a-f]{64}$/u.test(promptSha256) ||
      !/^sha256:[0-9a-f]{64}$/u.test(blueprintSha256) ||
      (suppliedPromptSha256 !== undefined && suppliedPromptSha256 !== promptSha256)) throw new Error("R12_CHECKPOINT_INVALID");
  const entries = [];
  const seen = new Set();
  let totalBytes = 0;
  const add = (checkpointPath, value) => {
    if (!safeRelativePath(checkpointPath) || seen.has(checkpointPath)) throw new Error("R12_CHECKPOINT_INVALID");
    const bytes = typeof value === "string" ? new TextEncoder().encode(value)
      : value instanceof Uint8Array ? new Uint8Array(value) : null;
    if (!bytes || bytes.length < 1 || bytes.length > CHECKPOINT_FILE_LIMIT) throw new Error("R12_CHECKPOINT_INVALID");
    if (typeof value === "string" && new TextDecoder("utf-8", { fatal: true }).decode(bytes) !== value) {
      throw new Error("R12_CHECKPOINT_INVALID");
    }
    totalBytes += bytes.length;
    if (totalBytes > CHECKPOINT_TOTAL_LIMIT) throw new Error("R12_CHECKPOINT_INVALID");
    seen.add(checkpointPath);
    entries.push(Object.freeze({ path: checkpointPath, bytes }));
  };
  const artifactNames = [
    ["authoring-game-pack.json", "authoringGamePackJson"],
    ["scene-blueprint.json", "sceneBlueprintJson"],
    ["runtime-game-pack.json", "runtimeGamePackJson"],
    ["runtime-receipt.json", "runtimeReceiptJson"],
    ["generation-report.json", "generationReportJson"],
  ];
  for (const [name, key] of artifactNames) add(`prototype/${name}`, artifacts[key]);
  const groups = [
    ["asset", acquisition.normalized?.materialization, ["prototype-asset-bundle.json", "canonicalBundleJson"]],
    ["environment", acquisition.environment?.environment, ["prototype-environment-bundle.json", "canonicalBundleJson"],
      ["prototype-environment-report.json", "canonicalReportJson"]],
    ["spatial", acquisition.spatial?.materialization, ["prototype-spatial-environment-bundle.json", "canonicalBundleJson"],
      ["prototype-spatial-environment-report.json", "canonicalReportJson"]],
  ];
  for (const [prefix, materialization, ...texts] of groups) {
    if (materialization === null || typeof materialization !== "object" || !Array.isArray(materialization.files)) {
      throw new Error("R12_CHECKPOINT_INVALID");
    }
    for (const [name, key] of texts) add(`${prefix}/${name}`, materialization[key]);
    for (const output of materialization.files) {
      if (output === null || typeof output !== "object" || !safeRelativePath(output.path)) throw new Error("R12_CHECKPOINT_INVALID");
      add(`${prefix}/files/${output.path}`, output.bytes);
    }
  }
  if (entries.length > CHECKPOINT_FILE_COUNT_LIMIT) throw new Error("R12_CHECKPOINT_INVALID");
  entries.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
  const manifest = Object.freeze({
    blueprintSha256,
    files: Object.freeze(entries.map(({ path: entryPath, bytes }) => Object.freeze({
      byteLength: bytes.length,
      path: entryPath,
      sha256: sha256(bytes),
    }))),
    format: CHECKPOINT_FORMAT,
    formatVersion: CHECKPOINT_VERSION,
    model,
    promptSha256,
  });
  return Object.freeze({
    blueprintSha256,
    entries: Object.freeze(entries),
    key: acquisitionCheckpointKey(promptSha256, model, blueprintSha256),
    manifestText: canonicalizeJsonValue(manifest),
    model,
    promptSha256,
  });
}

function pendingGenerationSnapshot({ promptSha256, model, artifacts, approval }) {
  const artifactNames = [
    ["authoring-game-pack.json", "authoringGamePackJson"],
    ["scene-blueprint.json", "sceneBlueprintJson"],
    ["runtime-game-pack.json", "runtimeGamePackJson"],
    ["runtime-receipt.json", "runtimeReceiptJson"],
    ["generation-report.json", "generationReportJson"],
  ];
  if (!/^sha256:[0-9a-f]{64}$/u.test(promptSha256) || !SAFE_MODEL.test(model) ||
      !exactKeys(artifacts, artifactNames.map(([, key]) => key)) || !validPendingApproval(approval)) {
    throw new Error("R12_PENDING_GENERATION_INVALID");
  }
  let generationReport;
  try { generationReport = JSON.parse(artifacts.generationReportJson); }
  catch { throw new Error("R12_PENDING_GENERATION_INVALID"); }
  if (generationReport === null || typeof generationReport !== "object" || generationReport.model !== model) {
    throw new Error("R12_PENDING_GENERATION_INVALID");
  }
  const entries = [];
  let totalBytes = 0;
  const add = (entryPath, text) => {
    if (!safeRelativePath(entryPath) || typeof text !== "string") throw new Error("R12_PENDING_GENERATION_INVALID");
    const bytes = new TextEncoder().encode(text);
    if (bytes.length < 1 || bytes.length > CHECKPOINT_FILE_LIMIT ||
        new TextDecoder("utf-8", { fatal: true }).decode(bytes) !== text) throw new Error("R12_PENDING_GENERATION_INVALID");
    totalBytes += bytes.length;
    if (totalBytes > CHECKPOINT_TOTAL_LIMIT) throw new Error("R12_PENDING_GENERATION_INVALID");
    entries.push(Object.freeze({ path: entryPath, bytes }));
  };
  for (const [name, key] of artifactNames) add(`prototype/${name}`, artifacts[key]);
  add("asset-approval.json", canonicalizeJsonValue(approval));
  entries.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
  const manifest = Object.freeze({
    files: Object.freeze(entries.map(({ path: entryPath, bytes }) => Object.freeze({
      byteLength: bytes.length, path: entryPath, sha256: sha256(bytes),
    }))),
    format: PENDING_FORMAT,
    formatVersion: PENDING_VERSION,
    model,
    promptSha256,
  });
  return Object.freeze({
    entries: Object.freeze(entries),
    key: checkpointKey(promptSha256, model),
    manifestText: canonicalizeJsonValue(manifest),
    model,
    promptSha256,
  });
}

function environmentCheckpointKey(blueprintSha256) {
  if (!/^sha256:[0-9a-f]{64}$/u.test(blueprintSha256)) throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
  return sha256(new TextEncoder().encode(canonicalizeJsonValue({
    blueprintSha256,
    format: ENVIRONMENT_CHECKPOINT_FORMAT,
    formatVersion: ENVIRONMENT_CHECKPOINT_VERSION,
  }))).slice(7);
}

function environmentCheckpointSnapshot({ blueprintSha256, materialization }) {
  if (!/^sha256:[0-9a-f]{64}$/u.test(blueprintSha256) || materialization?.ok !== true) {
    throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
  }
  const groups = [
    ["environment", materialization.environment, "prototype-environment-bundle.json", "prototype-environment-report.json"],
    ["spatial-source", materialization.spatialSource, "prototype-spatial-source-bundle.json", "prototype-spatial-source-report.json"],
  ];
  const entries = [];
  const seen = new Set();
  let totalBytes = 0;
  const add = (entryPath, value) => {
    if (!safeRelativePath(entryPath) || seen.has(entryPath)) throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    const bytes = typeof value === "string" ? new TextEncoder().encode(value)
      : value instanceof Uint8Array ? new Uint8Array(value) : null;
    if (!bytes || bytes.length < 1 || bytes.length > CHECKPOINT_FILE_LIMIT) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    if (typeof value === "string" && new TextDecoder("utf-8", { fatal: true }).decode(bytes) !== value) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    totalBytes += bytes.length;
    if (totalBytes > CHECKPOINT_TOTAL_LIMIT) throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    seen.add(entryPath);
    entries.push(Object.freeze({ path: entryPath, bytes }));
  };
  for (const [prefix, value, bundleName, reportName] of groups) {
    if (value === null || typeof value !== "object" || typeof value.canonicalBundleJson !== "string" ||
        typeof value.canonicalReportJson !== "string" || !Array.isArray(value.files)) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    add(`${prefix}/${bundleName}`, value.canonicalBundleJson);
    add(`${prefix}/${reportName}`, value.canonicalReportJson);
    for (const file of value.files) {
      if (file === null || typeof file !== "object" || !safeRelativePath(file.path)) {
        throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
      }
      add(`${prefix}/files/${file.path}`, file.bytes);
    }
  }
  if (entries.length > CHECKPOINT_FILE_COUNT_LIMIT) throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
  entries.sort((left, right) => left.path < right.path ? -1 : left.path > right.path ? 1 : 0);
  const manifest = Object.freeze({
    blueprintSha256,
    files: Object.freeze(entries.map(({ path: entryPath, bytes }) => Object.freeze({
      byteLength: bytes.length, path: entryPath, sha256: sha256(bytes),
    }))),
    format: ENVIRONMENT_CHECKPOINT_FORMAT,
    formatVersion: ENVIRONMENT_CHECKPOINT_VERSION,
  });
  return Object.freeze({
    blueprintSha256,
    entries: Object.freeze(entries),
    key: environmentCheckpointKey(blueprintSha256),
    manifestText: canonicalizeJsonValue(manifest),
  });
}

async function exists(candidate) {
  try { await lstat(candidate); return true; }
  catch (error) { if (error?.code === "ENOENT") return false; throw error; }
}

function contained(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative.length > 0 && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

async function trustedDirectory(candidate, parent, create = false) {
  const resolved = path.resolve(candidate);
  if (create && !(await exists(resolved))) await mkdir(resolved, { recursive: false });
  const stat = await lstat(resolved, { bigint: true });
  const real = path.resolve(await realpath(resolved));
  if (!stat.isDirectory() || stat.isSymbolicLink() || real !== resolved ||
      (parent !== null && path.dirname(resolved) !== parent)) throw new Error("R12_CHECKPOINT_INVALID");
  return Object.freeze({ path: resolved, stat });
}

async function storageRoots(prototypeRunRoot, directoryName, create) {
  const tempReal = path.resolve(await realpath(temporaryRoot));
  const resolvedRoot = path.resolve(prototypeRunRoot);
  if (path.dirname(resolvedRoot) !== tempReal) throw new Error("R12_CHECKPOINT_INVALID");
  if (!create && !(await exists(resolvedRoot))) return null;
  const runRoot = await trustedDirectory(resolvedRoot, tempReal, create);
  const storagePath = path.join(runRoot.path, directoryName);
  if (!create && !(await exists(storagePath))) return null;
  const storage = await trustedDirectory(storagePath, runRoot.path, create);
  return Object.freeze({ runRoot, storage });
}

async function checkpointRoots(prototypeRunRoot, create) {
  const roots = await storageRoots(prototypeRunRoot, CHECKPOINT_DIRECTORY, create);
  return roots === null ? null : Object.freeze({ runRoot: roots.runRoot, checkpoints: roots.storage });
}

async function pendingGenerationRoots(prototypeRunRoot, create) {
  const roots = await storageRoots(prototypeRunRoot, PENDING_DIRECTORY, create);
  return roots === null ? null : Object.freeze({ runRoot: roots.runRoot, pending: roots.storage });
}

async function environmentCheckpointRoots(prototypeRunRoot, create) {
  const roots = await storageRoots(prototypeRunRoot, ENVIRONMENT_CHECKPOINT_DIRECTORY, create);
  return roots === null ? null : Object.freeze({ runRoot: roots.runRoot, environments: roots.storage });
}

async function verifyDirectoryChain(root, relativeDirectory) {
  let current = root;
  for (const segment of relativeDirectory.split("/").filter(Boolean)) {
    current = path.join(current, segment);
    const stat = await lstat(current, { bigint: true });
    const real = path.resolve(await realpath(current));
    if (!stat.isDirectory() || stat.isSymbolicLink() || real !== current || !contained(root, real)) {
      throw new Error("R12_CHECKPOINT_INVALID");
    }
  }
}

async function writeCheckpointFile(root, relative, bytes) {
  const parts = relative.split("/");
  const parentRelative = parts.slice(0, -1).join("/");
  const parent = path.join(root, ...parts.slice(0, -1));
  if (parentRelative.length > 0) {
    await mkdir(parent, { recursive: true });
    await verifyDirectoryChain(root, parentRelative);
  }
  const candidate = path.join(root, ...parts);
  const handle = await open(candidate, "wx+");
  let identity;
  try {
    identity = await handle.stat({ bigint: true });
    const pathStat = await lstat(candidate, { bigint: true });
    const resolved = path.resolve(await realpath(candidate));
    if (!identity.isFile() || !sameNode(identity, pathStat) || pathStat.isSymbolicLink() ||
        resolved !== candidate || !contained(root, resolved)) throw new Error("R12_CHECKPOINT_INVALID");
    await handle.writeFile(bytes);
    await handle.sync();
    const output = new Uint8Array(bytes.length);
    let offset = 0;
    while (offset < output.length) {
      const { bytesRead } = await handle.read(output, offset, output.length - offset, offset);
      if (bytesRead < 1) throw new Error("R12_CHECKPOINT_INVALID");
      offset += bytesRead;
    }
    if (sha256(output) !== sha256(bytes)) throw new Error("R12_CHECKPOINT_INVALID");
  } finally { await handle.close(); }
  const finalStat = await lstat(candidate, { bigint: true });
  const finalReal = path.resolve(await realpath(candidate));
  if (!finalStat.isFile() || finalStat.isSymbolicLink() || finalReal !== candidate ||
      !sameNode(finalStat, identity) || finalStat.size !== BigInt(bytes.length) ||
      !contained(root, finalReal)) throw new Error("R12_CHECKPOINT_INVALID");
}

async function readCheckpointFile(root, relative, maximum = CHECKPOINT_FILE_LIMIT) {
  if (!safeRelativePath(relative)) throw new Error("R12_CHECKPOINT_INVALID");
  const candidate = path.join(root, ...relative.split("/"));
  const before = await lstat(candidate, { bigint: true });
  const resolved = path.resolve(await realpath(candidate));
  if (!before.isFile() || before.isSymbolicLink() || before.size < 1n || before.size > BigInt(maximum) ||
      resolved !== candidate || !contained(root, resolved)) throw new Error("R12_CHECKPOINT_INVALID");
  const bytes = new Uint8Array(await readFile(resolved));
  const after = await lstat(candidate, { bigint: true });
  if (!sameIdentity(before, after) || bytes.length !== Number(after.size)) throw new Error("R12_CHECKPOINT_INVALID");
  return bytes;
}

async function checkpointFileNames(root) {
  const pending = [""];
  const files = [];
  while (pending.length > 0) {
    const relative = pending.pop();
    const directory = relative.length > 0 ? path.join(root, ...relative.split("/")) : root;
    const items = await readdir(directory, { withFileTypes: true });
    for (const item of items) {
      const itemRelative = relative.length > 0 ? `${relative}/${item.name}` : item.name;
      if (!safeRelativePath(itemRelative) || item.isSymbolicLink()) throw new Error("R12_CHECKPOINT_INVALID");
      if (item.isDirectory()) pending.push(itemRelative);
      else if (item.isFile()) files.push(itemRelative);
      else throw new Error("R12_CHECKPOINT_INVALID");
      if (files.length + pending.length > CHECKPOINT_FILE_COUNT_LIMIT + 16) throw new Error("R12_CHECKPOINT_INVALID");
    }
  }
  return files.sort();
}

function decodeCheckpointText(bytes) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { throw new Error("R12_CHECKPOINT_INVALID"); }
}

function parseCanonicalReuseText(bytes) {
  const text = decodeCheckpointText(bytes);
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? Object.freeze({ text, value }) : null;
  } catch { return null; }
}

function reusableTokens(value) {
  if (typeof value !== "string") return [];
  return [...new Set((value.toLocaleLowerCase("en-US").match(/[\p{L}\p{N}]+/gu) ?? [])
    .filter((token) => token.length >= 2))].sort();
}

function tokenSimilarity(leftValue, rightValue) {
  const left = reusableTokens(leftValue); const right = reusableTokens(rightValue);
  if (left.length === 0 || right.length === 0) return Object.freeze({ dice: 0, intersection: 0, jaccard: 0 });
  const rightSet = new Set(right); const intersection = left.filter((token) => rightSet.has(token)).length;
  return Object.freeze({
    dice: (2 * intersection) / (left.length + right.length),
    intersection,
    jaccard: intersection / (left.length + right.length - intersection),
  });
}

function reusablePairScore(target, source) {
  if (target.kind !== source.kind || canonicalizeJsonValue(target.roles) !== canonicalizeJsonValue(source.roles)) return null;
  const entityExact = target.entityId !== null && target.entityId === source.entityId;
  const idExact = target.id === source.id;
  const entity = tokenSimilarity(target.entityId, source.entityId);
  const prompt = tokenSimilarity(target.prompt, source.prompt);
  const eligible = entityExact || idExact || (entity.intersection >= 1 && entity.dice >= 0.6) ||
    (prompt.intersection >= 4 && prompt.jaccard >= 0.25);
  if (!eligible) return null;
  return (entityExact ? 1_000_000 : 0) + (idExact ? 500_000 : 0) +
    Math.round(entity.dice * 10_000) + Math.round(prompt.jaccard * 1_000);
}

/** Selects one complete, unique, topic-neutral historical mapping. */
export function matchReusablePrototypeAssets(targetBriefs, sourceSets) {
  try {
    if (!Array.isArray(targetBriefs) || targetBriefs.length < 1 || targetBriefs.length > 6 || !Array.isArray(sourceSets)) return null;
    const candidates = [];
    for (const sourceSet of sourceSets) {
      if (!sourceSet || !SAFE_RUN_ID.test(sourceSet.runId) || !Array.isArray(sourceSet.briefs) ||
          sourceSet.briefs.length !== targetBriefs.length) continue;
      let bestScore = -1; let best = null; let bestCount = 0;
      const visit = (targetIndex, used, matches, score) => {
        if (targetIndex === targetBriefs.length) {
          if (score > bestScore) { bestScore = score; best = [...matches]; bestCount = 1; }
          else if (score === bestScore) bestCount += 1;
          return;
        }
        for (let sourceIndex = 0; sourceIndex < sourceSet.briefs.length; sourceIndex += 1) {
          if (used.has(sourceIndex)) continue;
          const pairScore = reusablePairScore(targetBriefs[targetIndex], sourceSet.briefs[sourceIndex]);
          if (pairScore === null) continue;
          used.add(sourceIndex); matches.push(sourceIndex);
          visit(targetIndex + 1, used, matches, score + pairScore);
          matches.pop(); used.delete(sourceIndex);
        }
      };
      visit(0, new Set(), [], 0);
      if (best !== null && bestCount === 1) candidates.push({ sourceSet, indexes: best, score: bestScore });
    }
    candidates.sort((left, right) => right.score - left.score ||
      (left.sourceSet.runId < right.sourceSet.runId ? -1 : left.sourceSet.runId > right.sourceSet.runId ? 1 : 0));
    if (candidates.length < 1) return null;
    const tied = candidates.filter((candidate) => candidate.score === candidates[0].score);
    if (tied.length > 1) {
      const signatures = new Set(tied.map((candidate) => canonicalizeJsonValue(
        candidate.indexes.map((sourceIndex) => sha256(candidate.sourceSet.briefs[sourceIndex].bytes)),
      )));
      if (signatures.size !== 1) return null;
    }
    const selected = candidates[0];
    return Object.freeze({
      sourceRunId: selected.sourceSet.runId,
      matches: Object.freeze(targetBriefs.map((target, index) => Object.freeze({
        targetBriefId: target.id,
        sourceBriefId: selected.sourceSet.briefs[selected.indexes[index]].id,
        bytes: Uint8Array.prototype.slice.call(selected.sourceSet.briefs[selected.indexes[index]].bytes),
      }))),
    });
  } catch { return null; }
}

async function loadReusablePrototypeAssetSets({ prototypeRunRoot, recover = recoverPrototypeRuns }) {
  const recovered = await recover({ runRoot: prototypeRunRoot, temporaryRoot, services,
    assemblePrototypeScene, canonicalizeJsonValue });
  if (!Array.isArray(recovered?.runs) || recovered.runs.length < 1 || !(await exists(prototypeRunRoot))) return [];
  const tempReal = path.resolve(await realpath(temporaryRoot));
  const root = await trustedDirectory(prototypeRunRoot, tempReal, false);
  const runs = await trustedDirectory(path.join(root.path, "runs"), root.path, false);
  const output = [];
  for (const recoveredRun of recovered.runs) {
    try {
      if (!SAFE_RUN_ID.test(recoveredRun.runId)) continue;
      const directory = await trustedDirectory(path.join(runs.path, recoveredRun.runId), runs.path, false);
      const blueprintParsed = parseCanonicalReuseText(await readCheckpointFile(directory.path, "scene-blueprint.json", 1_048_576));
      const bundleParsed = parseCanonicalReuseText(await readCheckpointFile(directory.path, "prototype-asset-bundle.json", 262_144));
      if (blueprintParsed === null || bundleParsed === null) continue;
      const validation = validatePrototypeAssetBundleJson(bundleParsed.text);
      if (validation?.valid !== true || validation.reportVersion !== 1 || validation.diagnostics?.length !== 0 ||
          bundleParsed.value?.blueprint?.canonicalSha256 !== sha256(new TextEncoder().encode(blueprintParsed.text)) ||
          !Array.isArray(blueprintParsed.value?.assetBriefs) || !Array.isArray(bundleParsed.value?.materializations)) continue;
      const materializations = new Map(bundleParsed.value.materializations.map((item) => [item.assetBriefId, item]));
      const bundleFiles = [];
      const filesByPath = new Map();
      for (const materialization of bundleParsed.value.materializations) {
        if (!Array.isArray(materialization.assets)) throw new Error("INVALID");
        for (const asset of materialization.assets) {
          if (typeof asset.path !== "string" || !asset.path.startsWith("assets/") ||
              !safeRelativePath(asset.path) || !Number.isSafeInteger(asset.byteLength) || asset.byteLength < 1 ||
              asset.byteLength > 33_554_432 || !/^sha256:[0-9a-f]{64}$/u.test(asset.sha256) ||
              filesByPath.has(asset.path)) throw new Error("INVALID");
          const bytes = await readCheckpointFile(directory.path, asset.path, asset.byteLength);
          if (bytes.length !== asset.byteLength || sha256(bytes) !== asset.sha256) throw new Error("INVALID");
          filesByPath.set(asset.path, bytes);
          bundleFiles.push(Object.freeze({ path: asset.path, bytes }));
        }
      }
      if (bundleFiles.length < 1 || bundleFiles.length > 16) throw new Error("INVALID");
      const briefs = [];
      for (const brief of blueprintParsed.value.assetBriefs.filter((item) => item.kind !== "environment")) {
        const materialization = materializations.get(brief.id);
        const asset = materialization?.source?.type === "meshy-text-to-3d" && Array.isArray(materialization.assets)
          ? materialization.assets.find((item) => Array.isArray(item.roles) && item.roles.includes("visual")) : null;
        if (!asset || typeof asset.path !== "string" || !asset.path.startsWith("assets/") ||
            !safeRelativePath(asset.path) || !Number.isSafeInteger(asset.byteLength) || asset.byteLength < 1 ||
            asset.byteLength > 33_554_432 || !/^sha256:[0-9a-f]{64}$/u.test(asset.sha256)) throw new Error("INVALID");
        const bytes = filesByPath.get(asset.path);
        if (!(bytes instanceof Uint8Array)) throw new Error("INVALID");
        briefs.push(Object.freeze({ id: brief.id, kind: brief.kind, prompt: brief.prompt,
          entityId: brief.entityId, roles: Object.freeze([...brief.roles]), path: asset.path, bytes }));
      }
      if (briefs.length > 0 && briefs.length <= 6) output.push(Object.freeze({ runId: recoveredRun.runId,
        blueprintSha256: sha256(new TextEncoder().encode(blueprintParsed.text)),
        current: recoveredRun.runId === recovered.currentRunId,
        briefs: Object.freeze(briefs),
        materialization: Object.freeze({
          canonicalBundleJson: bundleParsed.text,
          files: Object.freeze(bundleFiles.map((file) => Object.freeze({
            path: file.path,
            bytes: Uint8Array.prototype.slice.call(file.bytes),
          }))),
        }),
      }));
    } catch { /* raced, corrupt, or incomplete history is ineligible */ }
  }
  const finalRecovery = await recover({ runRoot: prototypeRunRoot, temporaryRoot, services,
    assemblePrototypeScene, canonicalizeJsonValue });
  const stillVerified = new Set((Array.isArray(finalRecovery?.runs) ? finalRecovery.runs : []).map((item) => item.runId));
  return Object.freeze(output.filter((item) => stillVerified.has(item.runId)));
}

async function loadR12AcquisitionCheckpoint({ prototypeRunRoot, prompt, promptSha256, model, blueprintSha256 }) {
  const promptBytes = typeof prompt === "string" ? new TextEncoder().encode(prompt) : null;
  if ((promptBytes !== null && sha256(promptBytes) !== promptSha256) ||
      !/^sha256:[0-9a-f]{64}$/u.test(promptSha256) || !SAFE_MODEL.test(model)) throw new Error("R12_CHECKPOINT_INVALID");
  const roots = await checkpointRoots(prototypeRunRoot, false);
  if (roots === null) return null;
  if (blueprintSha256 === undefined || blueprintSha256 === null) {
    const candidates = [];
    for (const entry of await readdir(roots.checkpoints.path, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.isSymbolicLink() || !SAFE_CHECKPOINT_KEY.test(entry.name)) continue;
      try {
        const manifestText = decodeCheckpointText(await readCheckpointFile(
          path.join(roots.checkpoints.path, entry.name), "checkpoint.json", 1_048_576));
        const manifest = JSON.parse(manifestText);
        if (canonicalizeJsonValue(manifest) === manifestText &&
            exactKeys(manifest, ["blueprintSha256", "files", "format", "formatVersion", "model", "promptSha256"]) &&
            manifest.format === CHECKPOINT_FORMAT && manifest.formatVersion === CHECKPOINT_VERSION &&
            manifest.model === model && manifest.promptSha256 === promptSha256 &&
            /^sha256:[0-9a-f]{64}$/u.test(manifest.blueprintSha256) &&
            acquisitionCheckpointKey(promptSha256, model, manifest.blueprintSha256) === entry.name) {
          candidates.push(manifest.blueprintSha256);
        }
      } catch { /* An unrelated or incomplete checkpoint is never trusted. */ }
    }
    if (candidates.length !== 1) return null;
    [blueprintSha256] = candidates;
  }
  if (!/^sha256:[0-9a-f]{64}$/u.test(blueprintSha256)) throw new Error("R12_CHECKPOINT_INVALID");
  const key = acquisitionCheckpointKey(promptSha256, model, blueprintSha256);
  const candidate = path.join(roots.checkpoints.path, key);
  if (!(await exists(candidate))) return null;
  const directory = await trustedDirectory(candidate, roots.checkpoints.path, false);
  const manifestText = decodeCheckpointText(await readCheckpointFile(directory.path, "checkpoint.json", 1_048_576));
  let manifest;
  try { manifest = JSON.parse(manifestText); }
  catch { throw new Error("R12_CHECKPOINT_INVALID"); }
  if (canonicalizeJsonValue(manifest) !== manifestText ||
      !exactKeys(manifest, ["blueprintSha256", "files", "format", "formatVersion", "model", "promptSha256"]) ||
      manifest.format !== CHECKPOINT_FORMAT || manifest.formatVersion !== CHECKPOINT_VERSION ||
      manifest.model !== model || manifest.promptSha256 !== promptSha256 ||
      manifest.blueprintSha256 !== blueprintSha256 || !Array.isArray(manifest.files) ||
      manifest.files.length < 1 || manifest.files.length > CHECKPOINT_FILE_COUNT_LIMIT) throw new Error("R12_CHECKPOINT_INVALID");
  const expectedNames = ["checkpoint.json"];
  const captured = new Map();
  let totalBytes = 0;
  let previous = "";
  for (const item of manifest.files) {
    if (!exactKeys(item, ["byteLength", "path", "sha256"]) || !safeRelativePath(item.path) ||
        item.path === "checkpoint.json" || item.path <= previous || !Number.isSafeInteger(item.byteLength) ||
        item.byteLength < 1 || item.byteLength > CHECKPOINT_FILE_LIMIT || !/^sha256:[0-9a-f]{64}$/u.test(item.sha256)) {
      throw new Error("R12_CHECKPOINT_INVALID");
    }
    previous = item.path;
    totalBytes += item.byteLength;
    if (totalBytes > CHECKPOINT_TOTAL_LIMIT) throw new Error("R12_CHECKPOINT_INVALID");
    const bytes = await readCheckpointFile(directory.path, item.path, item.byteLength);
    if (bytes.length !== item.byteLength || sha256(bytes) !== item.sha256) throw new Error("R12_CHECKPOINT_INVALID");
    captured.set(item.path, bytes);
    expectedNames.push(item.path);
  }
  expectedNames.sort();
  if (JSON.stringify(await checkpointFileNames(directory.path)) !== JSON.stringify(expectedNames)) {
    throw new Error("R12_CHECKPOINT_INVALID");
  }
  const text = (name) => {
    const bytes = captured.get(name);
    if (!bytes) throw new Error("R12_CHECKPOINT_INVALID");
    return decodeCheckpointText(bytes);
  };
  const outputs = (prefix) => [...captured.entries()]
    .filter(([name]) => name.startsWith(`${prefix}/files/`))
    .map(([name, bytes]) => Object.freeze({ path: name.slice(`${prefix}/files/`.length), bytes: new Uint8Array(bytes) }));
  const artifacts = Object.freeze({
    authoringGamePackJson: text("prototype/authoring-game-pack.json"),
    sceneBlueprintJson: text("prototype/scene-blueprint.json"),
    runtimeGamePackJson: text("prototype/runtime-game-pack.json"),
    runtimeReceiptJson: text("prototype/runtime-receipt.json"),
    generationReportJson: text("prototype/generation-report.json"),
  });
  if (sha256(new TextEncoder().encode(artifacts.sceneBlueprintJson)) !== blueprintSha256) {
    throw new Error("R12_CHECKPOINT_INVALID");
  }
  const acquisition = Object.freeze({
    ok: true,
    normalized: Object.freeze({ ok: true, materialization: Object.freeze({
      canonicalBundleJson: text("asset/prototype-asset-bundle.json"), files: Object.freeze(outputs("asset")),
    }) }),
    environment: Object.freeze({ ok: true, environment: Object.freeze({
      canonicalBundleJson: text("environment/prototype-environment-bundle.json"),
      canonicalReportJson: text("environment/prototype-environment-report.json"),
      files: Object.freeze(outputs("environment")),
    }) }),
    spatial: Object.freeze({ ok: true, materialization: Object.freeze({
      canonicalBundleJson: text("spatial/prototype-spatial-environment-bundle.json"),
      canonicalReportJson: text("spatial/prototype-spatial-environment-report.json"),
      files: Object.freeze(outputs("spatial")),
    }) }),
  });
  return Object.freeze({ acquisition, artifacts, key });
}

async function safeRemoveCheckpointStaging(candidate, parent, identity) {
  try {
    const stat = await lstat(candidate, { bigint: true });
    const resolved = path.resolve(await realpath(candidate));
    if (stat.isDirectory() && !stat.isSymbolicLink() && sameNode(stat, identity) &&
        path.dirname(resolved) === parent && resolved === candidate) {
      await rm(candidate, { recursive: true, force: true });
    }
  } catch (error) { if (error?.code !== "ENOENT") return; }
}

async function saveR12AcquisitionCheckpoint({ prototypeRunRoot, prompt, promptSha256, model, artifacts, acquisition }) {
  const snapshot = checkpointSnapshot(prompt, promptSha256, model, artifacts, acquisition);
  const roots = await checkpointRoots(prototypeRunRoot, true);
  const finalPath = path.join(roots.checkpoints.path, snapshot.key);
  if (await exists(finalPath)) {
    const loaded = await loadR12AcquisitionCheckpoint({ prototypeRunRoot, prompt,
      promptSha256: snapshot.promptSha256, model: snapshot.model,
      blueprintSha256: snapshot.blueprintSha256 });
    if (loaded === null) throw new Error("R12_CHECKPOINT_INVALID");
    return Object.freeze({ key: snapshot.key });
  }
  const staging = await mkdtemp(path.join(roots.checkpoints.path, `.staging-${snapshot.key}-`));
  const stagingIdentity = await lstat(staging, { bigint: true });
  try {
    for (const entry of snapshot.entries) await writeCheckpointFile(staging, entry.path, entry.bytes);
    await writeCheckpointFile(staging, "checkpoint.json", new TextEncoder().encode(snapshot.manifestText));
    const verifiedNames = await checkpointFileNames(staging);
    const expectedNames = ["checkpoint.json", ...snapshot.entries.map(({ path: entryPath }) => entryPath)].sort();
    if (JSON.stringify(verifiedNames) !== JSON.stringify(expectedNames)) throw new Error("R12_CHECKPOINT_INVALID");
    await rename(staging, finalPath);
    const finalStat = await lstat(finalPath, { bigint: true });
    const finalReal = path.resolve(await realpath(finalPath));
    if (!finalStat.isDirectory() || finalStat.isSymbolicLink() || !sameNode(finalStat, stagingIdentity) ||
        finalReal !== finalPath || path.dirname(finalReal) !== roots.checkpoints.path) throw new Error("R12_CHECKPOINT_INVALID");
    const loaded = await loadR12AcquisitionCheckpoint({ prototypeRunRoot, prompt,
      promptSha256: snapshot.promptSha256, model: snapshot.model,
      blueprintSha256: snapshot.blueprintSha256 });
    if (loaded === null) throw new Error("R12_CHECKPOINT_INVALID");
    return Object.freeze({ key: snapshot.key });
  } catch (error) {
    if (await exists(finalPath)) {
      const loaded = await loadR12AcquisitionCheckpoint({ prototypeRunRoot, prompt,
        promptSha256: snapshot.promptSha256, model: snapshot.model,
        blueprintSha256: snapshot.blueprintSha256 });
      if (loaded !== null) return Object.freeze({ key: snapshot.key });
    }
    throw error;
  } finally {
    await safeRemoveCheckpointStaging(staging, roots.checkpoints.path, stagingIdentity);
  }
}

function validateEnvironmentCheckpointReport(text, format, bundleText) {
  let report;
  try { report = JSON.parse(text); }
  catch { throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID"); }
  if (canonicalizeJsonValue(report) !== text || report === null || typeof report !== "object" ||
      report.format !== format || report.formatVersion !== "0.1.0" || report.bundleSha256 !== sha256(bundleText)) {
    throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
  }
}

async function loadR12EnvironmentCheckpoint({ prototypeRunRoot, blueprintSha256 }) {
  const key = environmentCheckpointKey(blueprintSha256);
  const roots = await environmentCheckpointRoots(prototypeRunRoot, false);
  if (roots === null) return null;
  const candidate = path.join(roots.environments.path, key);
  if (!(await exists(candidate))) return null;
  const directory = await trustedDirectory(candidate, roots.environments.path, false);
  const manifestText = decodeCheckpointText(await readCheckpointFile(directory.path, "checkpoint.json", 1_048_576));
  let manifest;
  try { manifest = JSON.parse(manifestText); }
  catch { throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID"); }
  if (canonicalizeJsonValue(manifest) !== manifestText ||
      !exactKeys(manifest, ["blueprintSha256", "files", "format", "formatVersion"]) ||
      manifest.blueprintSha256 !== blueprintSha256 || manifest.format !== ENVIRONMENT_CHECKPOINT_FORMAT ||
      manifest.formatVersion !== ENVIRONMENT_CHECKPOINT_VERSION || !Array.isArray(manifest.files) ||
      manifest.files.length < 1 || manifest.files.length > CHECKPOINT_FILE_COUNT_LIMIT) {
    throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
  }
  const captured = new Map();
  const expectedNames = ["checkpoint.json"];
  let previous = "";
  let totalBytes = 0;
  for (const item of manifest.files) {
    if (!exactKeys(item, ["byteLength", "path", "sha256"]) || !safeRelativePath(item.path) ||
        item.path === "checkpoint.json" || item.path <= previous || !Number.isSafeInteger(item.byteLength) ||
        item.byteLength < 1 || item.byteLength > CHECKPOINT_FILE_LIMIT || !/^sha256:[0-9a-f]{64}$/u.test(item.sha256)) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    previous = item.path;
    totalBytes += item.byteLength;
    if (totalBytes > CHECKPOINT_TOTAL_LIMIT) throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    const bytes = await readCheckpointFile(directory.path, item.path, item.byteLength);
    if (bytes.length !== item.byteLength || sha256(bytes) !== item.sha256) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    captured.set(item.path, bytes);
    expectedNames.push(item.path);
  }
  expectedNames.sort();
  if (JSON.stringify(await checkpointFileNames(directory.path)) !== JSON.stringify(expectedNames)) {
    throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
  }
  const text = (name) => {
    const bytes = captured.get(name);
    if (!bytes) throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    return decodeCheckpointText(bytes);
  };
  const outputs = (prefix) => [...captured.entries()]
    .filter(([name]) => name.startsWith(`${prefix}/files/`))
    .map(([name, bytes]) => Object.freeze({
      path: name.slice(`${prefix}/files/`.length), bytes: new Uint8Array(bytes),
    }));
  const environmentBundleJson = text("environment/prototype-environment-bundle.json");
  const environmentReportJson = text("environment/prototype-environment-report.json");
  const spatialBundleJson = text("spatial-source/prototype-spatial-source-bundle.json");
  const spatialReportJson = text("spatial-source/prototype-spatial-source-report.json");
  const environmentFiles = Object.freeze(outputs("environment"));
  const spatialFiles = Object.freeze(outputs("spatial-source"));
  const environmentValidation = validatePrototypeEnvironmentBundleJson(
    environmentBundleJson,
    new Map(environmentFiles.map((file) => [file.path, file.bytes])),
  );
  const spatialValidation = validatePrototypeSpatialSourceBundleJson(
    spatialBundleJson,
    new Map(spatialFiles.map((file) => [file.path, file.bytes])),
    environmentBundleJson,
  );
  let environmentBundle;
  let spatialBundle;
  try {
    environmentBundle = JSON.parse(environmentBundleJson);
    spatialBundle = JSON.parse(spatialBundleJson);
  } catch { throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID"); }
  if (!environmentValidation.valid || !spatialValidation.valid ||
      environmentBundle?.blueprint?.canonicalSha256 !== blueprintSha256 ||
      spatialBundle?.blueprint?.canonicalSha256 !== blueprintSha256) {
    throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
  }
  validateEnvironmentCheckpointReport(
    environmentReportJson,
    "matrix-oasis.prototype-environment-materialization-report",
    environmentBundleJson,
  );
  validateEnvironmentCheckpointReport(
    spatialReportJson,
    "matrix-oasis.prototype-spatial-source-materialization-report",
    spatialBundleJson,
  );
  return Object.freeze({
    ok: true,
    environment: Object.freeze({ canonicalBundleJson: environmentBundleJson,
      canonicalReportJson: environmentReportJson, files: environmentFiles }),
    spatialSource: Object.freeze({ canonicalBundleJson: spatialBundleJson,
      canonicalReportJson: spatialReportJson, files: spatialFiles }),
  });
}

async function saveR12EnvironmentCheckpoint({ prototypeRunRoot, blueprintSha256, materialization }) {
  const snapshot = environmentCheckpointSnapshot({ blueprintSha256, materialization });
  const roots = await environmentCheckpointRoots(prototypeRunRoot, true);
  const finalPath = path.join(roots.environments.path, snapshot.key);
  if (await exists(finalPath)) {
    const loaded = await loadR12EnvironmentCheckpoint({ prototypeRunRoot, blueprintSha256 });
    if (loaded === null || environmentCheckpointSnapshot({ blueprintSha256, materialization: loaded }).manifestText !== snapshot.manifestText) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    return Object.freeze({ key: snapshot.key });
  }
  const staging = await mkdtemp(path.join(roots.environments.path, `.staging-${snapshot.key}-`));
  const stagingIdentity = await lstat(staging, { bigint: true });
  try {
    for (const entry of snapshot.entries) await writeCheckpointFile(staging, entry.path, entry.bytes);
    await writeCheckpointFile(staging, "checkpoint.json", new TextEncoder().encode(snapshot.manifestText));
    const expectedNames = ["checkpoint.json", ...snapshot.entries.map(({ path: entryPath }) => entryPath)].sort();
    if (JSON.stringify(await checkpointFileNames(staging)) !== JSON.stringify(expectedNames)) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    await rename(staging, finalPath);
    const finalStat = await lstat(finalPath, { bigint: true });
    const finalReal = path.resolve(await realpath(finalPath));
    if (!finalStat.isDirectory() || finalStat.isSymbolicLink() || !sameNode(finalStat, stagingIdentity) ||
        finalReal !== finalPath || path.dirname(finalReal) !== roots.environments.path) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    if (await loadR12EnvironmentCheckpoint({ prototypeRunRoot, blueprintSha256 }) === null) {
      throw new Error("R12_ENVIRONMENT_CHECKPOINT_INVALID");
    }
    return Object.freeze({ key: snapshot.key });
  } finally {
    await safeRemoveCheckpointStaging(staging, roots.environments.path, stagingIdentity);
  }
}

async function loadR12PendingGeneration({ prototypeRunRoot, key }) {
  if (!SAFE_CHECKPOINT_KEY.test(key)) throw new Error("R12_PENDING_GENERATION_INVALID");
  const roots = await pendingGenerationRoots(prototypeRunRoot, false);
  if (roots === null) return null;
  const candidate = path.join(roots.pending.path, key);
  if (!(await exists(candidate))) return null;
  const directory = await trustedDirectory(candidate, roots.pending.path, false);
  const manifestText = decodeCheckpointText(await readCheckpointFile(directory.path, "pending.json", 1_048_576));
  let manifest;
  try { manifest = JSON.parse(manifestText); }
  catch { throw new Error("R12_PENDING_GENERATION_INVALID"); }
  if (canonicalizeJsonValue(manifest) !== manifestText ||
      !exactKeys(manifest, ["files", "format", "formatVersion", "model", "promptSha256"]) ||
      manifest.format !== PENDING_FORMAT || manifest.formatVersion !== PENDING_VERSION || !SAFE_MODEL.test(manifest.model) ||
      !/^sha256:[0-9a-f]{64}$/u.test(manifest.promptSha256) || checkpointKey(manifest.promptSha256, manifest.model) !== key ||
      !Array.isArray(manifest.files) || manifest.files.length !== 6) throw new Error("R12_PENDING_GENERATION_INVALID");
  const expectedNames = ["pending.json"];
  const captured = new Map();
  let previous = "";
  for (const item of manifest.files) {
    if (!exactKeys(item, ["byteLength", "path", "sha256"]) || !safeRelativePath(item.path) ||
        item.path === "pending.json" || item.path <= previous || !Number.isSafeInteger(item.byteLength) ||
        item.byteLength < 1 || item.byteLength > CHECKPOINT_FILE_LIMIT || !/^sha256:[0-9a-f]{64}$/u.test(item.sha256)) {
      throw new Error("R12_PENDING_GENERATION_INVALID");
    }
    previous = item.path;
    const bytes = await readCheckpointFile(directory.path, item.path, item.byteLength);
    if (bytes.length !== item.byteLength || sha256(bytes) !== item.sha256) throw new Error("R12_PENDING_GENERATION_INVALID");
    captured.set(item.path, bytes);
    expectedNames.push(item.path);
  }
  expectedNames.sort();
  if (JSON.stringify(await checkpointFileNames(directory.path)) !== JSON.stringify(expectedNames)) {
    throw new Error("R12_PENDING_GENERATION_INVALID");
  }
  const text = (name) => {
    const bytes = captured.get(name);
    if (!bytes) throw new Error("R12_PENDING_GENERATION_INVALID");
    return decodeCheckpointText(bytes);
  };
  const artifacts = Object.freeze({
    authoringGamePackJson: text("prototype/authoring-game-pack.json"),
    sceneBlueprintJson: text("prototype/scene-blueprint.json"),
    runtimeGamePackJson: text("prototype/runtime-game-pack.json"),
    runtimeReceiptJson: text("prototype/runtime-receipt.json"),
    generationReportJson: text("prototype/generation-report.json"),
  });
  let approval;
  try { approval = JSON.parse(text("asset-approval.json")); }
  catch { throw new Error("R12_PENDING_GENERATION_INVALID"); }
  if (canonicalizeJsonValue(approval) !== text("asset-approval.json") || !validPendingApproval(approval)) {
    throw new Error("R12_PENDING_GENERATION_INVALID");
  }
  let generationReport;
  try { generationReport = JSON.parse(artifacts.generationReportJson); }
  catch { throw new Error("R12_PENDING_GENERATION_INVALID"); }
  if (generationReport?.model !== manifest.model) throw new Error("R12_PENDING_GENERATION_INVALID");
  return Object.freeze({ promptSha256: manifest.promptSha256, model: manifest.model,
    artifacts, approval: Object.freeze(approval), key });
}

async function saveR12PendingGeneration({ prototypeRunRoot, promptSha256, model, artifacts, approval }) {
  const snapshot = pendingGenerationSnapshot({ promptSha256, model, artifacts, approval });
  const roots = await pendingGenerationRoots(prototypeRunRoot, true);
  const finalPath = path.join(roots.pending.path, snapshot.key);
  if (await exists(finalPath)) {
    const loaded = await loadR12PendingGeneration({ prototypeRunRoot, key: snapshot.key });
    if (loaded === null || pendingGenerationSnapshot(loaded).manifestText !== snapshot.manifestText) {
      throw new Error("R12_PENDING_GENERATION_INVALID");
    }
    return Object.freeze({ key: snapshot.key });
  }
  const staging = await mkdtemp(path.join(roots.pending.path, `.staging-${snapshot.key}-`));
  const stagingIdentity = await lstat(staging, { bigint: true });
  try {
    for (const entry of snapshot.entries) await writeCheckpointFile(staging, entry.path, entry.bytes);
    await writeCheckpointFile(staging, "pending.json", new TextEncoder().encode(snapshot.manifestText));
    const expectedNames = ["pending.json", ...snapshot.entries.map(({ path: entryPath }) => entryPath)].sort();
    if (JSON.stringify(await checkpointFileNames(staging)) !== JSON.stringify(expectedNames)) {
      throw new Error("R12_PENDING_GENERATION_INVALID");
    }
    await rename(staging, finalPath);
    const finalStat = await lstat(finalPath, { bigint: true });
    const finalReal = path.resolve(await realpath(finalPath));
    if (!finalStat.isDirectory() || finalStat.isSymbolicLink() || !sameNode(finalStat, stagingIdentity) ||
        finalReal !== finalPath || path.dirname(finalReal) !== roots.pending.path) throw new Error("R12_PENDING_GENERATION_INVALID");
    if (await loadR12PendingGeneration({ prototypeRunRoot, key: snapshot.key }) === null) {
      throw new Error("R12_PENDING_GENERATION_INVALID");
    }
    return Object.freeze({ key: snapshot.key });
  } finally {
    await safeRemoveCheckpointStaging(staging, roots.pending.path, stagingIdentity);
  }
}

async function recoverR12PendingGenerations({ prototypeRunRoot }) {
  const roots = await pendingGenerationRoots(prototypeRunRoot, false);
  if (roots === null) return Object.freeze([]);
  const items = await readdir(roots.pending.path, { withFileTypes: true });
  if (items.length > 8) throw new Error("R12_PENDING_GENERATION_INVALID");
  const recovered = [];
  for (const item of items.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0)) {
    if (!SAFE_CHECKPOINT_KEY.test(item.name) || !item.isDirectory() || item.isSymbolicLink()) {
      throw new Error("R12_PENDING_GENERATION_INVALID");
    }
    const loaded = await loadR12PendingGeneration({ prototypeRunRoot, key: item.name });
    if (loaded === null) throw new Error("R12_PENDING_GENERATION_INVALID");
    recovered.push(loaded);
  }
  return Object.freeze(recovered);
}

async function discardR12PendingGeneration({ prototypeRunRoot, promptSha256, model }) {
  const roots = await pendingGenerationRoots(prototypeRunRoot, false);
  if (roots === null) return;
  const key = checkpointKey(promptSha256, model);
  const candidate = path.join(roots.pending.path, key);
  if (!(await exists(candidate))) return;
  const directory = await trustedDirectory(candidate, roots.pending.path, false);
  await safeRemoveCheckpointStaging(directory.path, roots.pending.path, directory.stat);
  if (await exists(candidate)) throw new Error("R12_PENDING_GENERATION_INVALID");
}

async function readRecoveryFile(rootReal, name, maximum) {
  const filePath = path.join(rootReal, name);
  const before = await lstat(filePath, { bigint: true });
  if (!before.isFile() || before.isSymbolicLink() || before.size < 1n || before.size > BigInt(maximum)) throw new Error("R12_RECOVERY_INPUT_INVALID");
  const resolved = await realpath(filePath);
  if (path.dirname(resolved) !== rootReal) throw new Error("R12_RECOVERY_INPUT_INVALID");
  const bytes = new Uint8Array(await readFile(resolved));
  const after = await lstat(filePath, { bigint: true });
  if (!sameIdentity(before, after) || bytes.byteLength !== Number(after.size)) throw new Error("R12_RECOVERY_INPUT_INVALID");
  return Object.freeze({ path: resolved, bytes, sha256: sha256(bytes) });
}

async function loadRecoveryCache(rootPath, expectedWorldIdSha256) {
  const rootStat = await lstat(rootPath, { bigint: true });
  const rootReal = await realpath(rootPath);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink() || rootReal !== rootPath || path.dirname(rootReal) !== temporaryRoot) {
    throw new Error("R12_RECOVERY_CACHE_INVALID");
  }
  const assetsPath = path.join(rootReal, "assets");
  const assetsStat = await lstat(assetsPath, { bigint: true });
  const assetsReal = await realpath(assetsPath);
  if (!assetsStat.isDirectory() || assetsStat.isSymbolicLink() || assetsReal !== assetsPath || path.dirname(assetsReal) !== rootReal) {
    throw new Error("R12_RECOVERY_CACHE_INVALID");
  }
  const manifestBytes = await readRecoveryFile(rootReal, "recovery-cache.json", 1024 * 1024);
  let manifest;
  let manifestText;
  try {
    manifestText = new TextDecoder("utf-8", { fatal: true }).decode(manifestBytes.bytes);
    manifest = JSON.parse(manifestText);
  } catch { throw new Error("R12_RECOVERY_CACHE_INVALID"); }
  if (!exactKeys(manifest, ["format", "formatVersion", "model", "worldIdSha256", "worldPromptSha256", "scale", "counts", "assets"]) ||
      manifest.format !== "matrix-oasis.r12-marble-recovery-cache" || manifest.formatVersion !== "0.2.0" ||
      manifest.model !== "marble-1.1" || !/^sha256:[0-9a-f]{64}$/u.test(manifest.worldIdSha256) ||
      (expectedWorldIdSha256 !== null && manifest.worldIdSha256 !== expectedWorldIdSha256) ||
      !/^sha256:[0-9a-f]{64}$/u.test(manifest.worldPromptSha256) || canonicalizeJsonValue(manifest) !== manifestText ||
      !exactKeys(manifest.scale, ["metricScaleMicros", "groundPlaneOffsetMm"]) ||
      !Number.isSafeInteger(manifest.scale.metricScaleMicros) || manifest.scale.metricScaleMicros < 1 ||
      !Number.isSafeInteger(manifest.scale.groundPlaneOffsetMm) ||
      !exactKeys(manifest.counts, ["creates", "polls", "worldGets", "downloads"]) ||
      manifest.counts.creates !== 0 || manifest.counts.polls !== 0 || manifest.counts.worldGets !== 1 || manifest.counts.downloads !== 3 ||
      !exactKeys(manifest.assets, ["panorama", "collider", "spz"])) throw new Error("R12_RECOVERY_CACHE_INVALID");
  const specifications = [
    ["panorama", "environment-panorama.png", 64 * 1024 * 1024],
    ["collider", "environment-collider.glb", 32 * 1024 * 1024],
    ["spz", "environment.spz", 64 * 1024 * 1024],
  ];
  const loaded = Object.create(null);
  for (const [name, fileName, maximum] of specifications) {
    const identity = manifest.assets[name];
    if (!exactKeys(identity, ["path", "byteLength", "sha256"]) || identity.path !== `assets/${fileName}` ||
        !Number.isSafeInteger(identity.byteLength) || identity.byteLength < 1 ||
        !/^sha256:[0-9a-f]{64}$/u.test(identity.sha256)) throw new Error("R12_RECOVERY_CACHE_INVALID");
    const file = await readRecoveryFile(assetsReal, fileName, maximum);
    if (file.bytes.byteLength !== identity.byteLength || file.sha256 !== identity.sha256) throw new Error("R12_RECOVERY_CACHE_INVALID");
    loaded[name] = file.bytes;
  }
  const expectedNames = ["assets/environment-collider.glb", "assets/environment-panorama.png", "assets/environment.spz", "recovery-cache.json"];
  if (JSON.stringify(await checkpointFileNames(rootReal)) !== JSON.stringify(expectedNames)) {
    throw new Error("R12_RECOVERY_CACHE_INVALID");
  }
  const finalRootStat = await lstat(rootPath, { bigint: true });
  const finalAssetsStat = await lstat(assetsPath, { bigint: true });
  if (!sameNode(rootStat, finalRootStat) || !sameNode(assetsStat, finalAssetsStat)) {
    throw new Error("R12_RECOVERY_CACHE_INVALID");
  }
  return Object.freeze({
    panoramaBytes: loaded.panorama,
    colliderBytes: loaded.collider,
    spzBytes: loaded.spz,
    metricScaleFactor: manifest.scale.metricScaleMicros / 1_000_000,
    groundPlaneOffset: manifest.scale.groundPlaneOffsetMm / 1_000,
    worldSource: "get-world-recovery",
    worldIdSha256: manifest.worldIdSha256,
    worldPromptSha256: manifest.worldPromptSha256,
    counts: Object.freeze({ creates: 0, polls: 0, worldGets: 1, downloads: 3 }),
  });
}

export async function createR12CachedRecoveryConfiguration({ rootPath: configuredRoot, dependencies = {} }) {
  if (typeof configuredRoot !== "string" || !path.isAbsolute(configuredRoot) ||
      dependencies === null || typeof dependencies !== "object" || Array.isArray(dependencies) ||
      Object.keys(dependencies).some((name) => name !== "materialize") ||
      Object.values(dependencies).some((value) => typeof value !== "function")) {
    throw new Error("R12_RECOVERY_CONFIG_INVALID");
  }
  const rootPath = path.resolve(configuredRoot);
  if (path.dirname(rootPath) !== temporaryRoot) throw new Error("R12_RECOVERY_CONFIG_INVALID");
  const initial = await loadRecoveryCache(rootPath, null);
  const materialize = dependencies.materialize ?? materializeRecoveredPrototypeEnvironmentWithSpatialSource;
  let ready = false;
  const summary = Object.freeze({
    model: "marble-1.1", worldIdSha256: initial.worldIdSha256,
    maxCreates: 0, maxPolls: 0, maxWorldGets: 0, maxDownloads: 0, creditLimit: 0, usdLimitCents: 0,
  });
  return Object.freeze({
    summary,
    async execute() {
      try {
        await loadRecoveryCache(rootPath, summary.worldIdSha256);
        ready = true;
        return Object.freeze({ ok: true });
      } catch {
        return { ok: false, diagnostics: [{ code: "R12_RECOVERY_FAILED", path: "" }] };
      }
    },
    async materialize(plan) {
      if (!ready) return { ok: false, diagnostics: [{ code: "R12_RECOVERY_NOT_READY", path: "" }] };
      const recovered = await loadRecoveryCache(rootPath, summary.worldIdSha256);
      return materialize({ plan, recovered });
    },
    async matches(plan) {
      if (!ready || typeof plan?.environmentPromptSha256 !== "string") return false;
      const recovered = await loadRecoveryCache(rootPath, summary.worldIdSha256);
      return recovered.worldPromptSha256 === plan.environmentPromptSha256;
    },
    isReady() { return ready; },
  });
}

export async function createR12RecoveryConfiguration({ worldId, rootPath: configuredRoot, dependencies = {} }) {
  if (typeof worldId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/u.test(worldId) ||
      typeof configuredRoot !== "string" || !path.isAbsolute(configuredRoot) ||
      dependencies === null || typeof dependencies !== "object" || Array.isArray(dependencies) ||
      Object.keys(dependencies).some((name) => !["createProvider", "recover", "materialize"].includes(name)) ||
      Object.values(dependencies).some((value) => typeof value !== "function")) throw new Error("R12_RECOVERY_CONFIG_INVALID");
  const rootPath = path.resolve(configuredRoot);
  if (path.dirname(rootPath) !== temporaryRoot) throw new Error("R12_RECOVERY_CONFIG_INVALID");
  const output = rootPath;
  const worldIdSha256 = sha256(worldId);
  const createProvider = dependencies.createProvider ?? (() => createMarbleWorldProvider({ endpoint: MARBLE_PROVIDER_ENDPOINT,
    ["api" + "Key"]: secret("MATRIX_OASIS_MARBLE_API_KEY"), allowedAssetHosts: allowedMarbleAssetHosts }));
  const recover = dependencies.recover ?? recoverMarbleEnvironmentWithSpatialSource;
  const materialize = dependencies.materialize ?? materializeRecoveredPrototypeEnvironmentWithSpatialSource;
  let ready = false;
  const summary = Object.freeze({
    model: "marble-1.1", worldIdSha256,
    maxCreates: 0, maxPolls: 0, maxWorldGets: 1, maxDownloads: 3, creditLimit: 0, usdLimitCents: 0,
  });
  return Object.freeze({
    summary,
    async execute() {
      try {
        if (await exists(output)) {
          await loadRecoveryCache(output, worldIdSha256);
          ready = true;
          return Object.freeze({ ok: true });
        }
        const provider = createProvider();
        const recovered = await recover(provider, worldId);
        if (!recovered.ok) return recovered;
        if (recovered.counts.creates !== 0 || recovered.counts.polls !== 0 || recovered.counts.worldGets !== 1 || recovered.counts.downloads !== 3) {
          throw new Error("R12_RECOVERY_COUNT_INVALID");
        }
        const metricScaleMicros = Math.round(recovered.metricScaleFactor * 1_000_000);
        const groundPlaneOffsetMm = Math.round(recovered.groundPlaneOffset * 1_000);
        if (!Number.isSafeInteger(metricScaleMicros) || metricScaleMicros < 1 || !Number.isSafeInteger(groundPlaneOffsetMm)) {
          return { ok: false, diagnostics: [{ code: "R12_RECOVERY_SCALE_INVALID", path: "" }] };
        }
        const manifest = canonicalizeJsonValue(Object.freeze({
          format: "matrix-oasis.r12-marble-recovery-cache", formatVersion: "0.2.0", model: "marble-1.1",
          worldIdSha256: summary.worldIdSha256, worldPromptSha256: sha256(recovered.worldPrompt),
          scale: { metricScaleMicros, groundPlaneOffsetMm: Object.is(groundPlaneOffsetMm, -0) ? 0 : groundPlaneOffsetMm },
          counts: recovered.counts,
          assets: {
            panorama: { path: "assets/environment-panorama.png", byteLength: recovered.panoramaBytes.byteLength, sha256: sha256(recovered.panoramaBytes) },
            collider: { path: "assets/environment-collider.glb", byteLength: recovered.colliderBytes.byteLength, sha256: sha256(recovered.colliderBytes) },
            spz: { path: "assets/environment.spz", byteLength: recovered.spzBytes.byteLength, sha256: sha256(recovered.spzBytes) },
          },
        }));
        const staging = await mkdtemp(`${output}.staging-`);
        const stagingIdentity = await lstat(staging, { bigint: true });
        try {
          await writeCheckpointFile(staging, "assets/environment-panorama.png", recovered.panoramaBytes);
          await writeCheckpointFile(staging, "assets/environment-collider.glb", recovered.colliderBytes);
          await writeCheckpointFile(staging, "assets/environment.spz", recovered.spzBytes);
          await writeCheckpointFile(staging, "recovery-cache.json", new TextEncoder().encode(manifest));
          const expectedNames = ["assets/environment-collider.glb", "assets/environment-panorama.png", "assets/environment.spz", "recovery-cache.json"];
          if (JSON.stringify(await checkpointFileNames(staging)) !== JSON.stringify(expectedNames)) {
            throw new Error("R12_RECOVERY_CACHE_INVALID");
          }
          await rename(staging, output);
          const finalStat = await lstat(output, { bigint: true });
          const finalReal = path.resolve(await realpath(output));
          if (!finalStat.isDirectory() || finalStat.isSymbolicLink() || !sameNode(finalStat, stagingIdentity) ||
              finalReal !== output || path.dirname(finalReal) !== temporaryRoot) throw new Error("R12_RECOVERY_CACHE_INVALID");
          await loadRecoveryCache(output, worldIdSha256);
          ready = true;
          return Object.freeze({ ok: true });
        } finally {
          await safeRemoveCheckpointStaging(staging, temporaryRoot, stagingIdentity);
        }
      } catch { return { ok: false, diagnostics: [{ code: "R12_RECOVERY_FAILED", path: "" }] }; }
    },
    async materialize(plan) {
      if (!ready) return { ok: false, diagnostics: [{ code: "R12_RECOVERY_NOT_READY", path: "" }] };
      const recovered = await loadRecoveryCache(output, worldIdSha256);
      return materialize({ plan, recovered });
    },
    async matches(plan) {
      if (!ready || typeof plan?.environmentPromptSha256 !== "string") return false;
      const recovered = await loadRecoveryCache(output, worldIdSha256);
      return recovered.worldPromptSha256 === plan.environmentPromptSha256;
    },
    isReady() { return ready; },
  });
}

async function loadRecoveryConfiguration() {
  const worldId = process.env.MATRIX_OASIS_R12_RECOVERY_WORLD_ID;
  const configuredRoot = process.env.MATRIX_OASIS_R12_RECOVERY_ROOT;
  if (worldId === undefined && configuredRoot === undefined) return null;
  if (worldId === undefined) return createR12CachedRecoveryConfiguration({ rootPath: configuredRoot });
  return createR12RecoveryConfiguration({ worldId, rootPath: configuredRoot });
}

export function createR12WorldDiscoveryConfiguration({ recoveryRoot = null, activateRecovery = () => {}, dependencies = {} } = {}) {
  if (dependencies === null || typeof dependencies !== "object" || Array.isArray(dependencies) ||
      Object.keys(dependencies).some((name) => !["createProvider", "listWorlds", "createRecovery"].includes(name)) ||
      Object.values(dependencies).some((value) => typeof value !== "function") || typeof activateRecovery !== "function" ||
      (recoveryRoot !== null && (typeof recoveryRoot !== "string" || !path.isAbsolute(recoveryRoot) ||
        path.dirname(path.resolve(recoveryRoot)) !== temporaryRoot))) throw new Error("R12_WORLD_DISCOVERY_CONFIG_INVALID");
  const createProvider = dependencies.createProvider ?? (() => createMarbleWorldProvider({ endpoint: MARBLE_PROVIDER_ENDPOINT,
    ["api" + "Key"]: secret("MATRIX_OASIS_MARBLE_API_KEY"), allowedAssetHosts: allowedMarbleAssetHosts }));
  const listWorlds = dependencies.listWorlds ?? listMarbleWorlds;
  const createRecovery = dependencies.createRecovery ?? ((worldId) => createR12RecoveryConfiguration({ worldId, rootPath: recoveryRoot }));
  return Object.freeze({
    summary: Object.freeze({
      provider: "world-labs-marble", operation: "worlds:list", model: "marble-1.1", pageSize: 100,
      status: "SUCCEEDED", sortBy: "created_at", maxRequests: 1, maxCreates: 0, maxPolls: 0,
      maxWorldGets: 0, maxDownloads: 0, creditLimit: 0, usdLimitCents: 0,
    }),
    async execute() {
      try { return await listWorlds(createProvider()); }
      catch { return { ok: false, diagnostics: [{ code: "R12_WORLD_DISCOVERY_FAILED", path: "" }] }; }
    },
    async recover(worldId) {
      if (recoveryRoot === null) return { ok: false, diagnostics: [{ code: "R12_WORLD_DISCOVERY_RECOVERY_ROOT_REQUIRED", path: "" }] };
      try {
        const configuration = await createRecovery(worldId);
        const result = await configuration.execute();
        if (!result?.ok) return result;
        activateRecovery(configuration);
        return Object.freeze({ ok: true });
      } catch { return { ok: false, diagnostics: [{ code: "R12_WORLD_DISCOVERY_RECOVERY_FAILED", path: "" }] }; }
    },
  });
}

function loadWorldDiscoveryConfiguration(activateRecovery) {
  const enabled = process.env.MATRIX_OASIS_R12_DISCOVER_WORLDS;
  if (enabled === undefined) return null;
  if (enabled !== "1") throw new Error("R12_WORLD_DISCOVERY_CONFIG_INVALID");
  const recoveryRoot = process.env.MATRIX_OASIS_R12_DISCOVERY_RECOVERY_ROOT;
  if (typeof recoveryRoot !== "string" || recoveryRoot.length < 1) throw new Error("R12_WORLD_DISCOVERY_CONFIG_INVALID");
  return createR12WorldDiscoveryConfiguration({ recoveryRoot, activateRecovery });
}

function configured(name) {
  return Object.prototype.hasOwnProperty.call(process.env, name);
}

function secret(name) {
  const value = process.env[name];
  if (typeof value !== "string" || value.length < 1 || value.length > 8192 || /[\r\n]/u.test(value)) {
    throw new Error("R12_HOST_CONFIG_INVALID");
  }
  return value;
}

function modelConfiguration() {
  const endpoint = process.env.MATRIX_OASIS_MODEL_ENDPOINT;
  const model = process.env.MATRIX_OASIS_MODEL_ID;
  if (typeof endpoint !== "string" || typeof model !== "string") throw new Error("R12_HOST_CONFIG_INVALID");
  return { endpoint, model, ["api" + "Key"]: secret("MATRIX_OASIS_MODEL_API_KEY") };
}

async function completedMeshyTask(provider, taskId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await wait(5_000);
    const status = await provider.getTask({ taskId });
    if (!status.ok) return status;
    if (status.task.status === "failed") return { ok: false, diagnostics: [{ code: "MESHY_PROVIDER_GENERATION_FAILED", path: "" }] };
    if (status.task.status === "succeeded") return status;
  }
  return { ok: false, diagnostics: [{ code: "MESHY_PROVIDER_POLL_LIMIT", path: "" }] };
}

async function acquireMeshy(provider, plan) {
  const acquired = new Map();
  for (const brief of plan.plan.blueprint.assetBriefs) {
    if (brief.kind === "environment") continue;
    const preview = await provider.createPreview({ prompt: brief.prompt }); if (!preview.ok) return preview;
    const previewStatus = await completedMeshyTask(provider, preview.taskId); if (!previewStatus.ok) return previewStatus;
    const refined = await provider.createRefine({ previewTaskId: preview.taskId }); if (!refined.ok) return refined;
    const refineStatus = await completedMeshyTask(provider, refined.taskId); if (!refineStatus.ok) return refineStatus;
    const downloaded = await provider.downloadGlb({ url: refineStatus.task.glbUrl }); if (!downloaded.ok) return downloaded;
    acquired.set(brief.id, downloaded.bytes);
  }
  return { ok: true, acquired };
}

export function createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot, recovery = null, dependencies = {} }) {
  const spatialOperations = (dependencies.createSpatialPrototypeOperations ?? createSpatialPrototypeOperations)({
    prototypeRunRoot, spatialRunRoot, godot, root: moduleRoot, tempRoot: temporaryRoot,
  });
  const generate = dependencies.generatePrototype ?? generatePrototype;
  const makeModelProvider = dependencies.createModelProvider ?? createOpenAICompatibleProvider;
  const getModelConfiguration = dependencies.modelConfiguration ?? modelConfiguration;
  const planEnvironment = dependencies.planPrototypeEnvironment ?? planPrototypeEnvironment;
  const makeEnvironmentProvider = dependencies.createMarbleWorldProvider ?? createMarbleWorldProvider;
  const planAssets = dependencies.planPrototypeAssets ?? planPrototypeAssets;
  const makeAssetProvider = dependencies.createMeshyTextTo3DProvider ?? createMeshyTextTo3DProvider;
  const acquireAssets = dependencies.acquireMeshy ?? acquireMeshy;
  const readSecret = dependencies.secret ?? secret;
  const readEnvironmentAssets = dependencies.readEnvironmentAssets ?? (async () => {
    const kenneyRoot = new URL("../examples/scene-bundles/kenney-prototype/assets/", import.meta.url);
    return { environmentAssets: new Map([
      ["floor-square", new Uint8Array(await readFile(new URL("floor-square.glb", kenneyRoot)))],
      ["wall", new Uint8Array(await readFile(new URL("wall.glb", kenneyRoot)))],
    ]), environmentTexture: new Uint8Array(await readFile(new URL("Textures/colormap.png", kenneyRoot))) };
  });
  const environmentMaterializer = dependencies.materializePrototypeEnvironmentWithSpatialSource ?? materializePrototypeEnvironmentWithSpatialSource;
  const spatialMaterializer = dependencies.materializePrototypeSpatialEnvironmentFromSource ?? materializePrototypeSpatialEnvironmentFromSource;
  const assetMaterializer = dependencies.materializePrototypeAssetBundle ?? materializePrototypeAssetBundle;
  const publishPrototype = dependencies.publishPrototypeRun ?? publishPrototypeRun;
  const publishSpatial = dependencies.publishSpatialPrototypeRun ?? publishSpatialPrototypeRun;
  const findPrototype = dependencies.findVerifiedPrototypeRun ?? findVerifiedPrototypeRun;
  const loadSpatial = dependencies.loadVerifiedSpatialPrototypeRun ?? loadVerifiedSpatialPrototypeRun;
  const analyzeQualification = dependencies.analyzeR12QualificationCandidate ?? analyzeR12QualificationCandidate;
  const saveCheckpoint = dependencies.saveAcquisitionCheckpoint ?? saveR12AcquisitionCheckpoint;
  const loadCheckpoint = dependencies.loadAcquisitionCheckpoint ?? loadR12AcquisitionCheckpoint;
  const savePendingStore = dependencies.savePendingGeneration ?? saveR12PendingGeneration;
  const recoverPendingStore = dependencies.recoverPendingGenerations ?? recoverR12PendingGenerations;
  const discardPendingStore = dependencies.discardPendingGeneration ?? discardR12PendingGeneration;
  const loadEnvironmentCheckpoint = dependencies.loadEnvironmentCheckpoint ?? loadR12EnvironmentCheckpoint;
  const saveEnvironmentCheckpoint = dependencies.saveEnvironmentCheckpoint ?? saveR12EnvironmentCheckpoint;
  const findReusableAssets = dependencies.findReusableAssets ?? (async ({ plan }) => {
    const sourceSets = await loadReusablePrototypeAssetSets({ prototypeRunRoot,
      recover: dependencies.recoverPrototypeRuns ?? recoverPrototypeRuns });
    const targets = plan.blueprint.assetBriefs.filter((brief) => brief.kind !== "environment");
    const exact = sourceSets.filter((sourceSet) =>
      sourceSet.blueprintSha256 === plan.blueprint.canonicalSha256);
    if (exact.length > 0) {
      exact.sort((left, right) => Number(right.current) - Number(left.current) ||
        (left.runId < right.runId ? -1 : left.runId > right.runId ? 1 : 0));
      const selected = exact.find((sourceSet) => sourceSet.current) ??
        (exact.length === 1 || new Set(exact.map((sourceSet) => sourceSet.materialization.canonicalBundleJson)).size === 1
          ? exact[0]
          : null);
      if (selected === null) return Object.freeze({ ok: false });
      return Object.freeze({ ok: true, sourceRunId: selected.runId,
        acquired: new Map(selected.briefs.map((brief) => [brief.id,
          Uint8Array.prototype.slice.call(brief.bytes)])),
        materialization: Object.freeze({
          canonicalBundleJson: selected.materialization.canonicalBundleJson,
          files: Object.freeze(selected.materialization.files.map((file) => Object.freeze({
            path: file.path,
            bytes: Uint8Array.prototype.slice.call(file.bytes),
          }))),
        }),
      });
    }
    const match = matchReusablePrototypeAssets(targets, sourceSets);
    if (match === null) return Object.freeze({ ok: false });
    return Object.freeze({ ok: true, sourceRunId: match.sourceRunId,
      acquired: new Map(match.matches.map((item) => [item.targetBriefId,
        Uint8Array.prototype.slice.call(item.bytes)])) });
  });
  const qualifiesArtifacts = async (artifacts) => {
    if (!artifacts || typeof artifacts.sceneBlueprintJson !== "string" ||
        typeof artifacts.runtimeGamePackJson !== "string" || typeof artifacts.runtimeReceiptJson !== "string") return false;
    const result = await analyzeQualification(
      artifacts.sceneBlueprintJson,
      artifacts.runtimeGamePackJson,
      artifacts.runtimeReceiptJson,
    );
    return result?.ok === true;
  };
  const qualifiesRun = async (runId) => {
    try {
      const loaded = await loadSpatial({
        runId,
        runRoot: spatialRunRoot,
        prototypeRunRoot,
        temporaryRoot,
        services,
        recoverPrototypeRuns,
        assemblePrototypeScene,
        assemblePrototypeSpatialScene,
        canonicalizeJsonValue,
      });
      const evidence = loaded?.qualificationEvidence;
      return exactKeys(evidence, ["source", "sceneBlueprintJson", "runtimeGamePackJson", "runtimeReceiptJson"]) &&
        await qualifiesArtifacts(evidence);
    } catch {
      return false;
    }
  };
  const publishPrototypeMaterialization = async ({ prompt, promptSha256, artifacts, acquisition, source }) => {
    const result = await publishPrototype({ prompt, promptSha256, prototypeArtifacts: artifacts,
      assetMaterialization: { canonicalBundleJson: acquisition.normalized.materialization.canonicalBundleJson,
        files: acquisition.normalized.materialization.files },
      environmentMaterialization: { canonicalBundleJson: acquisition.environment.environment.canonicalBundleJson,
        canonicalReportJson: acquisition.environment.environment.canonicalReportJson,
        files: acquisition.environment.environment.files },
      runRoot: prototypeRunRoot, temporaryRoot, source, services,
      assemblePrototypeScene, canonicalizeJsonValue, assemblyProfile: "matrix-oasis.prototype-assembly/2",
      activateCurrent: false, reuseExisting: true });
    return { ok: true, runId: result.runId };
  };
  const publishSpatialMaterialization = async ({ runId, acquisition }) => {
    const result = await publishSpatial({ prototypeRunRoot, prototypeRunId: runId, spatialRunRoot, temporaryRoot,
      spatialMaterialization: { canonicalBundleJson: acquisition.spatial.materialization.canonicalBundleJson,
        canonicalReportJson: acquisition.spatial.materialization.canonicalReportJson,
        files: acquisition.spatial.materialization.files },
      services, recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue });
    return { ok: true, runId: result.runId };
  };
  const describeAssetRequirements = async (artifacts) => {
    const assetPlan = await planAssets(prototypeAssetPlanInput(artifacts));
    if (!assetPlan.ok) return assetPlan;
    const environmentPlan = planEnvironment(artifacts.sceneBlueprintJson, R12_ENVIRONMENT_PLAN_OPTIONS);
    if (!environmentPlan.ok) return environmentPlan;
    const briefs = assetPlan.plan.blueprint.assetBriefs.filter((brief) => brief.kind !== "environment")
      .map(({ id, kind, prompt }) => ({ id, kind, prompt }));
    const blueprintSha256 = environmentPlan.plan.blueprint.canonicalSha256;
    const environmentCheckpoint = await loadEnvironmentCheckpoint({ prototypeRunRoot, blueprintSha256 });
    const reusable = await findReusableAssets({ plan: assetPlan.plan });
    return { ok: true, blueprintSha256,
      environmentPrompt: environmentPlan.plan.environmentPrompt,
      environmentCached: environmentCheckpoint !== null || await recovery?.matches?.(environmentPlan.plan) === true,
      assetsCached: reusable?.ok === true,
      briefs };
  };
  return {
    async findCache(input) {
      const pendingCandidates = await recoverPendingStore({ prototypeRunRoot });
      const matchingPending = pendingCandidates.find((candidate) =>
        candidate.promptSha256 === input.promptSha256 && candidate.model === input.model) ?? null;
      const blueprintSha256 = matchingPending === null ? null : sha256(
        new TextEncoder().encode(matchingPending.artifacts.sceneBlueprintJson),
      );
      const checkpoint = await loadCheckpoint({ prototypeRunRoot, prompt: input.prompt,
        promptSha256: input.promptSha256, model: input.model, blueprintSha256 });
      if (checkpoint !== null && !(await qualifiesArtifacts(checkpoint.artifacts))) return { ok: false };
      const ready = await spatialOperations.findCache({ promptSha256: input.promptSha256, model: input.model });
      if (ready?.ok && await qualifiesRun(ready.runId)) return ready;
      if (checkpoint === null) return { ok: false };
      const cachedPrototype = await findPrototype({ promptSha256: input.promptSha256, model: input.model,
        runRoot: prototypeRunRoot, temporaryRoot, services, assemblePrototypeScene, canonicalizeJsonValue });
      const prototype = cachedPrototype?.ok ? cachedPrototype : await publishPrototypeMaterialization({
        prompt: input.prompt, promptSha256: input.promptSha256, artifacts: checkpoint.artifacts,
        acquisition: checkpoint.acquisition, source: "verified-cache",
      });
      if (!prototype?.ok || typeof prototype.runId !== "string") throw new Error("R12_CHECKPOINT_INVALID");
      let spatial;
      try {
        spatial = await publishSpatialMaterialization({ runId: prototype.runId, acquisition: checkpoint.acquisition });
      } catch (error) {
        if (cachedPrototype?.ok && ["SPATIAL_CACHE_ASSEMBLY_REJECTED", "SPATIAL_CACHE_RUN_EXISTS"].includes(error?.code)) {
          return { ok: false };
        }
        throw error;
      }
      if (!spatial?.ok || spatial.runId !== prototype.runId) throw new Error("R12_CHECKPOINT_INVALID");
      return Object.freeze({ ok: true, runId: prototype.runId });
    },
    async generate({ prompt }) {
      return generate({ prompt }, makeModelProvider(getModelConfiguration()),
        { acceptanceProfile: R12_LAST_TRAIN_ACCEPTANCE_PROFILE });
    },
    async describeAssets({ artifacts }) {
      try {
        return await describeAssetRequirements(artifacts);
      } catch { return { ok: false, diagnostics: [{ code: "R12_HOST_GENERATION_FAILED", path: "" }] }; }
    },
    async persistPending({ promptSha256, model, artifacts, approval }) {
      await savePendingStore({ prototypeRunRoot, promptSha256, model, artifacts, approval });
    },
    async recoverPending() {
      const candidates = await recoverPendingStore({ prototypeRunRoot });
      const ready = await spatialOperations.recover();
      const readyIdentities = new Set((Array.isArray(ready?.runs) ? ready.runs : [])
        .filter((run) => typeof run?.promptSha256 === "string" && typeof run?.model === "string")
        .map((run) => `${run.promptSha256}\0${run.model}`));
      const accepted = [];
      for (const candidate of candidates) {
        if (readyIdentities.has(`${candidate.promptSha256}\0${candidate.model}`)) {
          await discardPendingStore({ prototypeRunRoot, promptSha256: candidate.promptSha256, model: candidate.model });
          continue;
        }
        if (!(await qualifiesArtifacts(candidate.artifacts))) throw new Error("R12_PENDING_GENERATION_INVALID");
        const description = await describeAssetRequirements(candidate.artifacts);
        if (!description?.ok || description.blueprintSha256 !== candidate.approval.blueprintSha256) {
          throw new Error("R12_PENDING_GENERATION_INVALID");
        }
        const approval = Object.freeze({ ...candidate.approval,
          marble: Object.freeze({ model: "marble-1.1", environmentPrompt: description.environmentPrompt,
            recovered: description.environmentCached, maxCreates: description.environmentCached ? 0 : 1,
            maxPolls: description.environmentCached ? 0 : 180, maxDownloads: description.environmentCached ? 0 : 3,
            creditLimit: description.environmentCached ? 0 : 1600, usdLimitCents: description.environmentCached ? 0 : 150 }),
          meshy: Object.freeze({ ...candidate.approval.meshy,
            maxTasks: description.assetsCached ? 0 : description.briefs.length * 2,
            creditLimit: description.assetsCached ? 0 : description.briefs.length * 30 }),
        });
        accepted.push(Object.freeze({ promptSha256: candidate.promptSha256, model: candidate.model,
          artifacts: candidate.artifacts, approval }));
      }
      if (accepted.length > 1) throw new Error("R12_PENDING_GENERATION_INVALID");
      return Object.freeze({ runs: Object.freeze(accepted) });
    },
    async discardPending({ promptSha256, model }) {
      await discardPendingStore({ prototypeRunRoot, promptSha256, model });
    },
    async acquireEnvironment({ artifacts, approval }) {
      const plan = planEnvironment(artifacts.sceneBlueprintJson, R12_ENVIRONMENT_PLAN_OPTIONS); if (!plan.ok) return plan;
      const blueprintSha256 = plan.plan.blueprint.canonicalSha256;
      const environmentCheckpoint = await loadEnvironmentCheckpoint({ prototypeRunRoot, blueprintSha256 });
      if (environmentCheckpoint !== null) return environmentCheckpoint;
      if (await recovery?.matches?.(plan.plan) === true) return recovery.materialize(plan);
      if (approval.marble?.recovered === true) {
        return { ok: false, diagnostics: [{ code: "R12_ENVIRONMENT_CHECKPOINT_MISSING", path: "" }] };
      }
      const provider = makeEnvironmentProvider({ endpoint: MARBLE_PROVIDER_ENDPOINT,
        ["api" + "Key"]: readSecret("MATRIX_OASIS_MARBLE_API_KEY"), allowedAssetHosts: allowedMarbleAssetHosts });
      const materialization = await environmentMaterializer({ plan, approval: {
        blueprintSha256: approval.blueprintSha256, model: "marble-1.1", maxCreateRequests: 1,
        maxPollAttempts: 180, maxWorldGets: 1, maxDownloads: 3, creditLimit: 1600, usdLimitCents: 150,
      } }, provider);
      if (!materialization?.ok) return materialization;
      await saveEnvironmentCheckpoint({ prototypeRunRoot, blueprintSha256, materialization });
      return materialization;
    },
    async acquireAssets({ artifacts, approval }) {
      const plan = await planAssets(prototypeAssetPlanInput(artifacts)); if (!plan.ok) return plan;
      if (approval?.meshy?.maxTasks === 0 && approval.meshy.creditLimit === 0) {
        const reusable = await findReusableAssets({ plan: plan.plan });
        if (!reusable?.ok || !(reusable.acquired instanceof Map)) {
          return { ok: false, diagnostics: [{ code: "R12_ASSET_REUSE_MISSING", path: "" }] };
        }
        return { ok: true, plan, acquired: reusable.acquired, reused: true,
          ...(reusable.materialization === undefined ? {} : { materialization: reusable.materialization }) };
      }
      const provider = makeAssetProvider({ endpoint: MESHY_PROVIDER_ENDPOINT,
        ["api" + "Key"]: readSecret("MATRIX_OASIS_MESHY_API_KEY") });
      const acquired = await acquireAssets(provider, plan); if (!acquired.ok) return acquired;
      return { ok: true, plan, acquired: acquired.acquired };
    },
    async normalizeAssets({ assets }) {
      if (assets.materialization !== undefined) {
        return { ok: true, materialization: assets.materialization };
      }
      const environmentAssets = await readEnvironmentAssets();
      const materialization = await assetMaterializer({ plan: assets.plan, acquiredAssets: assets.acquired,
        environmentAssets: environmentAssets.environmentAssets,
        environmentTexture: environmentAssets.environmentTexture });
      return materialization.ok ? { ok: true, materialization } : materialization;
    },
    async spatializeEnvironment({ environment }) {
      const materialization = await spatialMaterializer({
        environmentBundleJson: environment.environment.canonicalBundleJson,
        environmentFiles: new Map(environment.environment.files.map((file) => [file.path, file.bytes])),
        spatialSourceBundleJson: environment.spatialSource.canonicalBundleJson,
        spatialSourceFiles: new Map(environment.spatialSource.files.map((file) => [file.path, file.bytes])),
      });
      return materialization.ok ? { ok: true, materialization } : materialization;
    },
    async publishPrototype({ prompt, promptSha256, model, artifacts, acquisition }) {
      await saveCheckpoint({ prototypeRunRoot, prompt, promptSha256, model, artifacts, acquisition });
      return publishPrototypeMaterialization({ prompt, promptSha256, artifacts, acquisition, source: "live-provider" });
    },
    async publishSpatial({ runId, acquisition }) {
      return publishSpatialMaterialization({ runId, acquisition });
    },
    async launch(input) { return spatialOperations.launch(input); },
    async recover() {
      const recovered = await spatialOperations.recover();
      const accepted = [];
      for (const run of Array.isArray(recovered?.runs) ? recovered.runs : []) {
        if (typeof run?.runId === "string" && await qualifiesRun(run.runId)) accepted.push(run);
      }
      const currentRunId = accepted.some((run) => run.runId === recovered?.currentRunId)
        ? recovered.currentRunId
        : null;
      return Object.freeze({ currentRunId, runs: Object.freeze(accepted) });
    },
    async stopLaunch() { return spatialOperations.stopLaunch(); },
  };
}

async function main() {
  let parsed;
  try { parsed = parseR12PreviewArguments(process.argv.slice(2)); }
  catch { process.stderr.write("R12_HOST_ARGUMENT_INVALID\n"); process.exitCode = 2; return; }
  let godot = null;
  try { if (configured("GODOT_BIN")) godot = resolveGodotBinary({ environment: { GODOT_BIN: process.env.GODOT_BIN } }); }
  catch { /* readiness remains false */ }
  let endpointHost = "unconfigured.local";
  try { if (configured("MATRIX_OASIS_MODEL_ENDPOINT")) endpointHost = new URL(process.env.MATRIX_OASIS_MODEL_ENDPOINT).host; }
  catch { /* readiness remains false */ }
  const model = typeof process.env.MATRIX_OASIS_MODEL_ID === "string" && /^[A-Za-z0-9._/-]{1,128}$/u.test(process.env.MATRIX_OASIS_MODEL_ID)
    ? process.env.MATRIX_OASIS_MODEL_ID : "unconfigured-model";
  let recovery = null;
  try { recovery = await loadRecoveryConfiguration(); }
  catch { process.stderr.write("R12_RECOVERY_CONFIG_INVALID\n"); process.exitCode = 2; return; }
  const recoverySlot = { current: recovery };
  const recoveryFacade = Object.freeze({
    isReady() { return recoverySlot.current?.isReady?.() === true; },
    matches(plan) {
      if (recoverySlot.current === null) return false;
      return recoverySlot.current.matches(plan);
    },
    materialize(plan) {
      if (recoverySlot.current === null) return { ok: false, diagnostics: [{ code: "R12_RECOVERY_NOT_READY", path: "" }] };
      return recoverySlot.current.materialize(plan);
    },
  });
  let worldDiscovery = null;
  try { worldDiscovery = loadWorldDiscoveryConfiguration((configuration) => { recoverySlot.current = configuration; }); }
  catch { process.stderr.write("R12_WORLD_DISCOVERY_CONFIG_INVALID\n"); process.exitCode = 2; return; }
  const operations = createR12PrototypeOperations(createR12LiveSteps({ ...parsed, godot,
    recovery: recovery !== null || worldDiscovery !== null ? recoveryFacade : null }));
  let webAssetRoot = moduleRoot;
  if (configured("MATRIX_OASIS_R12_CREATOR_ROOT")) {
    const candidate = path.resolve(process.env.MATRIX_OASIS_R12_CREATOR_ROOT);
    if (path.dirname(candidate) !== temporaryRoot) { process.stderr.write("R12_HOST_CONFIG_INVALID\n"); process.exitCode = 2; return; }
    webAssetRoot = candidate;
  }
  const host = createPrototypeHost({ profile: "r12", configuration: {
    endpointHost, model,
    modelReady: ["MATRIX_OASIS_MODEL_ENDPOINT", "MATRIX_OASIS_MODEL_ID", "MATRIX_OASIS_MODEL_API_KEY"].every(configured),
    assetsReady: ["MATRIX_OASIS_MARBLE_API_KEY", "MATRIX_OASIS_MESHY_API_KEY"].every(configured),
    godotReady: godot !== null,
  }, operations, webAssets: await loadCreatorWebAssets(webAssetRoot), port: parsed.port,
  recovery: recovery === null ? undefined : Object.freeze({ summary: recovery.summary, execute: recovery.execute }),
  worldDiscovery: worldDiscovery === null ? undefined : worldDiscovery });
  try {
    const address = await host.start();
    process.stdout.write(`${R12_HOST_MARKER} origin=${address.origin} api=${PROTOTYPE_HOST_MARKER}\n`);
    const stop = async () => { await host.stop(); process.exitCode = 0; };
    process.once("SIGINT", () => { void stop(); }); process.once("SIGTERM", () => { void stop(); });
  } catch { process.stderr.write("R12_HOST_INTERNAL_ERROR\n"); process.exitCode = 2; }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();
