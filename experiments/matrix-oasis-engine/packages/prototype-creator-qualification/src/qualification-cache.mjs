import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readdir,
  realpath,
  rename,
  rmdir,
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { validatePrototypeCreatorQualificationJson } from "@matrix-oasis/prototype-creator-qualification-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const RUN_ID = /^[0-9a-f]{64}$/u;
const SHA_256 = /^sha256:[0-9a-f]{64}$/u;
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/u;
const MAX_MANIFEST_BYTES = 1024 * 1024;
const MAX_RUN_ENTRIES = 256;
const CURRENT_FORMAT = "matrix-oasis.prototype-creator-qualified-current";
const CURRENT_VERSION = "0.1.0";

const defaultServices = Object.freeze({
  lstat,
  mkdir,
  mkdtemp,
  openFile: open,
  readdir,
  realpath,
  rename,
  rmdir,
  unlink,
  writeFile,
});

const CACHE_ERROR_CODES = new Set([
  "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID",
  "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID",
  "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_REFERENCE_INVALID",
  "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INTERNAL_ERROR",
]);

export class PrototypeCreatorQualificationCacheOperationalError extends Error {
  constructor(code) {
    super(code);
    this.name = "PrototypeCreatorQualificationCacheOperationalError";
    this.code = CACHE_ERROR_CODES.has(code) ? code : "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INTERNAL_ERROR";
  }
}

function fail(code) {
  throw new PrototypeCreatorQualificationCacheOperationalError(code);
}

function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function sameObjectIdentity(left, right) {
  return typeof left?.dev === "bigint" && typeof left?.ino === "bigint" &&
    typeof right?.dev === "bigint" && typeof right?.ino === "bigint" &&
    left.dev === right.dev && left.ino === right.ino;
}

function sameNodeIdentity(left, right) {
  return sameObjectIdentity(left, right) && typeof left?.ctimeNs === "bigint" &&
    typeof left?.mtimeNs === "bigint" && typeof right?.ctimeNs === "bigint" &&
    typeof right?.mtimeNs === "bigint" && left.ctimeNs === right.ctimeNs &&
    left.mtimeNs === right.mtimeNs;
}

function sameFileIdentity(left, right) {
  return sameNodeIdentity(left, right) && typeof left?.size === "bigint" &&
    typeof right?.size === "bigint" && left.size === right.size;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const item of Object.values(value)) deepFreeze(item);
    Object.freeze(value);
  }
  return value;
}

function requireRequest(request) {
  if (!request || typeof request.qualifiedRunRoot !== "string" || request.qualifiedRunRoot.length === 0 ||
      typeof request.temporaryRoot !== "string" || request.temporaryRoot.length === 0 ||
      typeof request.verifyReferences !== "function") {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  }
}

function parseCanonicalQualification(text) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") > MAX_MANIFEST_BYTES) {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  }
  const report = validatePrototypeCreatorQualificationJson(text);
  if (!report?.valid) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  try {
    const qualification = JSON.parse(text);
    if (canonicalizeJsonValue(qualification) !== text) {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
    }
    return deepFreeze(qualification);
  } catch (error) {
    if (error instanceof PrototypeCreatorQualificationCacheOperationalError) throw error;
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  }
}

async function verifyReferences(verify, qualification, qualificationJson, qualificationRunId) {
  let result;
  try {
    result = await verify(Object.freeze({ qualification, qualificationJson, qualificationRunId }));
  } catch {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_REFERENCE_INVALID");
  }
  if (result !== true && result?.valid !== true) {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_REFERENCE_INVALID");
  }
}

async function assertDirectory(candidate, services) {
  const absolute = path.resolve(candidate);
  let resolved;
  let stat;
  try {
    [resolved, stat] = await Promise.all([
      services.realpath(absolute),
      services.lstat(absolute, { bigint: true }),
    ]);
  } catch {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  }
  if (path.resolve(resolved) !== absolute || stat.isSymbolicLink() || !stat.isDirectory()) {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  }
  return Object.freeze({ absolute, stat });
}

async function assertSameDirectory(candidate, expected, services) {
  const actual = await assertDirectory(candidate, services);
  if (!sameNodeIdentity(actual.stat, expected.stat)) {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  }
  return actual;
}

async function trustedRoot(qualifiedRunRoot, temporaryRoot, services, create) {
  let temporary;
  try {
    temporary = path.resolve(await services.realpath(temporaryRoot));
  } catch {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  }
  const root = path.resolve(qualifiedRunRoot);
  if (path.dirname(root) !== temporary) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  if (create) {
    try {
      await services.mkdir(root, { recursive: true });
    } catch {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
  }
  return await assertDirectory(root, services);
}

async function ensureRunsDirectory(root, services, create) {
  const runs = path.join(root.absolute, "runs");
  if (create) {
    try {
      await services.mkdir(runs, { recursive: true });
    } catch {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
  }
  const trusted = await assertDirectory(runs, services);
  if (!contained(root.absolute, trusted.absolute)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  return trusted;
}

async function readStableFile(candidate, services, maxBytes = MAX_MANIFEST_BYTES) {
  const absolute = path.resolve(candidate);
  let handle;
  try {
    handle = await services.openFile(absolute, "r");
    const opened = await handle.stat({ bigint: true });
    const linked = await services.lstat(absolute, { bigint: true });
    const resolved = path.resolve(await services.realpath(absolute));
    if (!opened.isFile() || linked.isSymbolicLink() || resolved !== absolute ||
        !sameFileIdentity(opened, linked) || opened.size < 0n || opened.size > BigInt(maxBytes)) {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    const buffer = Buffer.alloc(Number(maxBytes) + 1);
    let length = 0;
    while (length < buffer.length) {
      const { bytesRead } = await handle.read(
        buffer,
        length,
        buffer.length - length,
        null,
      );
      if (bytesRead === 0) break;
      length += bytesRead;
    }
    if (length > Number(maxBytes)) {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    const after = await handle.stat({ bigint: true });
    const linkedAfter = await services.lstat(absolute, { bigint: true });
    const resolvedAfter = path.resolve(await services.realpath(absolute));
    if (resolvedAfter !== absolute || linkedAfter.isSymbolicLink() ||
        !sameFileIdentity(opened, after) || !sameFileIdentity(opened, linkedAfter) ||
        !sameFileIdentity(linked, linkedAfter)) {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    return buffer.subarray(0, length).toString("utf8");
  } catch (error) {
    if (error instanceof PrototypeCreatorQualificationCacheOperationalError) throw error;
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  } finally {
    await handle?.close().catch(() => {});
  }
}

async function ensureMissing(candidate, services) {
  try {
    await services.lstat(candidate, { bigint: true });
  } catch (error) {
    if (error?.code === "ENOENT") return;
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  }
  fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
}

async function readVerifiedRun(root, runs, qualificationRunId, verify, services) {
  if (!RUN_ID.test(qualificationRunId)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  const runDirectory = path.join(runs.absolute, qualificationRunId);
  if (!contained(root.absolute, runDirectory)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  const trustedRun = await assertDirectory(runDirectory, services);
  const qualificationJson = await readStableFile(path.join(runDirectory, "qualification.json"), services);
  await assertSameDirectory(runDirectory, trustedRun, services);
  const qualification = parseCanonicalQualification(qualificationJson);
  if (sha256(Buffer.from(qualificationJson, "utf8")) !== qualificationRunId) {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  }
  await verifyReferences(verify, qualification, qualificationJson, qualificationRunId);
  await assertSameDirectory(root.absolute, root, services);
  await assertSameDirectory(runs.absolute, runs, services);
  await assertSameDirectory(runDirectory, trustedRun, services);
  return deepFreeze({ qualificationRunId, runDirectory, qualificationJson, qualification });
}

function currentJson(qualificationRunId) {
  return canonicalizeJsonValue({
    format: CURRENT_FORMAT,
    formatVersion: CURRENT_VERSION,
    qualificationRunId,
  });
}

function parseCurrent(text) {
  try {
    const value = JSON.parse(text);
    if (canonicalizeJsonValue(value) !== text || value?.format !== CURRENT_FORMAT ||
        value?.formatVersion !== CURRENT_VERSION || !RUN_ID.test(value?.qualificationRunId) ||
        Object.keys(value).length !== 3) return null;
    return value;
  } catch {
    return null;
  }
}

async function removeOwnedDirectory(candidate, identity, allowedFiles, services) {
  try {
    const actual = await assertDirectory(candidate, services);
    if (!sameObjectIdentity(actual.stat, identity.stat)) return;
    const entries = await services.readdir(candidate, { withFileTypes: true });
    for (const entry of entries) {
      if (!allowedFiles.has(entry.name) || !entry.isFile() || entry.isSymbolicLink()) return;
      const child = path.join(candidate, entry.name);
      const linked = await services.lstat(child, { bigint: true });
      const resolved = path.resolve(await services.realpath(child));
      if (!linked.isFile() || linked.isSymbolicLink() || resolved !== path.resolve(child)) return;
      await services.unlink(child);
    }
    const after = await assertDirectory(candidate, services);
    if (!sameObjectIdentity(after.stat, identity.stat)) return;
    await services.rmdir(candidate);
  } catch {
    // A changed identity is deliberately preserved rather than removed.
  }
}

export async function publishQualifiedCreatorRun(request, services = defaultServices) {
  requireRequest(request);
  const qualification = parseCanonicalQualification(request.canonicalQualificationJson);
  const qualificationRunId = sha256(Buffer.from(request.canonicalQualificationJson, "utf8"));
  await verifyReferences(request.verifyReferences, qualification, request.canonicalQualificationJson, qualificationRunId);

  let root = await trustedRoot(request.qualifiedRunRoot, request.temporaryRoot, services, true);
  const runs = await ensureRunsDirectory(root, services, true);
  const rootAfterRuns = await assertDirectory(root.absolute, services);
  if (!sameObjectIdentity(rootAfterRuns.stat, root.stat)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  root = rootAfterRuns;
  const finalPath = path.join(runs.absolute, qualificationRunId);

  let staging = null;
  let stagingIdentity = null;
  let published = false;
  let currentStage = null;
  let currentStageIdentity = null;
  const publishLock = path.join(root.absolute, ".qualification-publish-lock");
  let publishLockIdentity = null;
  try {
    try {
      await services.mkdir(publishLock);
    } catch {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    publishLockIdentity = await assertDirectory(publishLock, services);
    const rootAfterLock = await assertDirectory(root.absolute, services);
    if (!sameObjectIdentity(rootAfterLock.stat, root.stat)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    root = rootAfterLock;
    await ensureMissing(finalPath, services);
    try {
      staging = await services.mkdtemp(path.join(runs.absolute, `.${qualificationRunId}-`));
    } catch {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    if (!contained(runs.absolute, staging)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    stagingIdentity = await assertDirectory(staging, services);
    const runsAfterStaging = await assertDirectory(runs.absolute, services);
    if (!sameObjectIdentity(runsAfterStaging.stat, runs.stat)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    await services.writeFile(path.join(staging, "qualification.json"), request.canonicalQualificationJson, {
      encoding: "utf8",
      flag: "wx",
    });
    const stagedJson = await readStableFile(path.join(staging, "qualification.json"), services);
    if (stagedJson !== request.canonicalQualificationJson) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
    const stagingAfterWrite = await assertDirectory(staging, services);
    if (!sameObjectIdentity(stagingAfterWrite.stat, stagingIdentity.stat)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    stagingIdentity = stagingAfterWrite;
    await verifyReferences(request.verifyReferences, qualification, stagedJson, qualificationRunId);
    await assertSameDirectory(root.absolute, root, services);
    await assertSameDirectory(runs.absolute, runsAfterStaging, services);
    await assertSameDirectory(staging, stagingIdentity, services);
    await ensureMissing(finalPath, services);
    try {
      await services.rename(staging, finalPath);
    } catch {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    staging = null;
    published = true;
    let currentRuns = await assertDirectory(runs.absolute, services);
    const publishedIdentity = await assertDirectory(finalPath, services);
    if (!sameObjectIdentity(publishedIdentity.stat, stagingIdentity.stat)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    await readVerifiedRun(root, currentRuns, qualificationRunId, request.verifyReferences, services);

    currentStage = await services.mkdtemp(path.join(root.absolute, ".qualified-current-"));
    const rootAfterCurrentStage = await assertDirectory(root.absolute, services);
    if (!sameObjectIdentity(rootAfterCurrentStage.stat, root.stat)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    root = rootAfterCurrentStage;
    const trustedCurrentStage = await assertDirectory(currentStage, services);
    currentStageIdentity = trustedCurrentStage;
    const pointerSource = path.join(currentStage, "qualified-current.json");
    await services.writeFile(pointerSource, currentJson(qualificationRunId), { encoding: "utf8", flag: "wx" });
    if (!parseCurrent(await readStableFile(pointerSource, services))) {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INTERNAL_ERROR");
    }
    const currentStageAfterWrite = await assertDirectory(currentStage, services);
    if (!sameObjectIdentity(currentStageAfterWrite.stat, trustedCurrentStage.stat)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    currentStageIdentity = currentStageAfterWrite;
    await assertSameDirectory(root.absolute, root, services);
    currentRuns = await assertSameDirectory(runs.absolute, currentRuns, services);
    await assertSameDirectory(finalPath, publishedIdentity, services);
    await assertSameDirectory(currentStage, currentStageAfterWrite, services);
    try {
      const currentTarget = path.join(root.absolute, "qualified-current.json");
      try {
        await services.lstat(currentTarget, { bigint: true });
        await readStableFile(currentTarget, services, 16 * 1024);
      } catch (error) {
        if (error?.code !== "ENOENT" && !(error instanceof PrototypeCreatorQualificationCacheOperationalError &&
            error.code === "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID")) throw error;
        if (error instanceof PrototypeCreatorQualificationCacheOperationalError) throw error;
      }
      await services.rename(pointerSource, path.join(root.absolute, "qualified-current.json"));
    } catch {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    const currentStageAfterRename = await assertDirectory(currentStage, services);
    if (!sameObjectIdentity(currentStageAfterRename.stat, currentStageIdentity.stat)) {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
    }
    currentStageIdentity = currentStageAfterRename;
    const persistedCurrent = parseCurrent(await readStableFile(path.join(root.absolute, "qualified-current.json"), services, 16 * 1024));
    if (persistedCurrent?.qualificationRunId !== qualificationRunId) {
      fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INTERNAL_ERROR");
    }
    published = false;
    return Object.freeze({ qualificationRunId, runDirectory: finalPath });
  } finally {
    if (staging && stagingIdentity) {
      await removeOwnedDirectory(staging, stagingIdentity, new Set(["qualification.json"]), services);
    }
    if (currentStage && currentStageIdentity) {
      await removeOwnedDirectory(currentStage, currentStageIdentity, new Set(["qualified-current.json"]), services);
    }
    if (published && stagingIdentity) {
      await removeOwnedDirectory(finalPath, stagingIdentity, new Set(["qualification.json"]), services);
    }
    if (publishLockIdentity) {
      await removeOwnedDirectory(publishLock, publishLockIdentity, new Set(), services);
    }
  }
}

export async function loadVerifiedQualifiedCreatorRun(request, services = defaultServices) {
  requireRequest(request);
  if (!RUN_ID.test(request.qualificationRunId)) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  const root = await trustedRoot(request.qualifiedRunRoot, request.temporaryRoot, services, false);
  const runs = await ensureRunsDirectory(root, services, false);
  return await readVerifiedRun(root, runs, request.qualificationRunId, request.verifyReferences, services);
}

export async function recoverQualifiedCreatorRuns(request, services = defaultServices) {
  requireRequest(request);
  let root;
  let runs;
  try {
    root = await trustedRoot(request.qualifiedRunRoot, request.temporaryRoot, services, false);
    runs = await ensureRunsDirectory(root, services, false);
  } catch (error) {
    if (error?.code === "ENOENT" || error?.cause?.code === "ENOENT") {
      return Object.freeze({ currentQualificationRunId: null, runs: Object.freeze([]) });
    }
    // A missing root is the only empty-cache case. Existing malformed roots fail closed.
    try {
      await services.lstat(path.resolve(request.qualifiedRunRoot), { bigint: true });
    } catch (statError) {
      if (statError?.code === "ENOENT") {
        return Object.freeze({ currentQualificationRunId: null, runs: Object.freeze([]) });
      }
    }
    throw error;
  }

  let entries;
  try {
    entries = await services.readdir(runs.absolute, { withFileTypes: true });
  } catch {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID");
  }
  if (entries.length > MAX_RUN_ENTRIES * 2) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  const recovered = [];
  for (const entry of entries.filter((item) => RUN_ID.test(item.name)).sort((left, right) => left.name.localeCompare(right.name))) {
    if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
    try {
      recovered.push(await readVerifiedRun(root, runs, entry.name, request.verifyReferences, services));
    } catch {
      // Invalid, drifted, or unverified runs are never eligible for Creator ready.
    }
    if (recovered.length > MAX_RUN_ENTRIES) fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  }

  let currentQualificationRunId = null;
  try {
    const pointer = parseCurrent(await readStableFile(path.join(root.absolute, "qualified-current.json"), services, 16 * 1024));
    if (pointer && recovered.some((run) => run.qualificationRunId === pointer.qualificationRunId)) {
      currentQualificationRunId = pointer.qualificationRunId;
    }
  } catch {
    // Invalid current pointers do not turn unqualified history into ready state.
  }
  await assertSameDirectory(root.absolute, root, services);
  await assertSameDirectory(runs.absolute, runs, services);
  return Object.freeze({ currentQualificationRunId, runs: Object.freeze(recovered) });
}

export async function findVerifiedQualifiedCreatorRun(request, services = defaultServices) {
  requireRequest(request);
  if (!SHA_256.test(request.promptSha256) || typeof request.model !== "string" ||
      request.model.length > 128 || !MODEL_ID.test(request.model)) {
    fail("PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID");
  }
  const recovered = await recoverQualifiedCreatorRuns(request, services);
  const matches = recovered.runs.filter((run) =>
    run.qualification.promptSha256 === request.promptSha256 && run.qualification.model === request.model);
  const current = matches.find((run) => run.qualificationRunId === recovered.currentQualificationRunId);
  return current ?? matches[0] ?? null;
}
