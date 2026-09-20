import { createHash } from "node:crypto";
import { lstat, mkdtemp, open, readdir, realpath, rename, rm, rmdir, stat } from "node:fs/promises";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const ENCODER = new TextEncoder();
const DECODER = new TextDecoder("utf-8", { fatal: true });
const OUTPUT_NAME = /^(?!\.)(?!.*[. ]$)[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/u;
const WINDOWS_DEVICE = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/iu;

export class R22CliOperationalError extends Error {
  constructor(code, cause = undefined) {
    super(code);
    this.name = "R22CliOperationalError";
    this.code = code;
    if (cause !== undefined) this.cause = cause;
  }
}

function fail(code, cause) { throw new R22CliOperationalError(code, cause); }
export function sha256(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }
export function canonicalText(value) { return canonicalizeJsonValue(value); }

const defaults = Object.freeze({ lstat, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir, stat });
const ownedTemporaryDirectories = new WeakMap();
function operations(overrides = {}) {
  if (!overrides || typeof overrides !== "object" || Array.isArray(overrides)) fail("R22_CLI_OPERATIONS_INVALID");
  return Object.freeze({ ...defaults, ...overrides });
}
function identity(value) {
  if (!value || typeof value.dev !== "bigint" || typeof value.ino !== "bigint") fail("R22_PATH_IDENTITY_INVALID");
  return Object.freeze({ dev: value.dev, ino: value.ino, size: value.size, mtimeNs: value.mtimeNs, ctimeNs: value.ctimeNs });
}
function sameIdentity(a, b) { return a?.dev === b?.dev && a?.ino === b?.ino; }
function sameRecord(a, b) {
  return sameIdentity(a, b) && a.size === b.size && a.mtimeNs === b.mtimeNs && a.ctimeNs === b.ctimeNs && a.sha256 === b.sha256;
}
function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}
async function missing(candidate, ops) {
  try { await ops.lstat(candidate, { bigint: true }); }
  catch (error) { if (error?.code === "ENOENT") return; throw error; }
  fail("R22_OUTPUT_EXISTS");
}

export async function assertMissingR22Path(candidate, overrides = {}) {
  return missing(path.resolve(candidate), operations(overrides));
}

export async function trustR22TemporaryRoot(temporaryRoot, overrides = {}) {
  const ops = operations(overrides);
  if (typeof temporaryRoot !== "string" || !path.isAbsolute(temporaryRoot) || temporaryRoot.includes("\0")) fail("R22_PATH_INVALID");
  const absolute = path.resolve(temporaryRoot);
  let linked;
  try { linked = await ops.lstat(absolute, { bigint: true }); } catch { fail("R22_PATH_INVALID"); }
  if (!linked.isDirectory() || linked.isSymbolicLink() || path.resolve(await ops.realpath(absolute)) !== absolute) fail("R22_PATH_INVALID");
  return Object.freeze({ path: absolute, ...identity(linked) });
}

export async function createR22OwnedTemporaryDirectory(temporaryRoot, prefix = "r22-owned", overrides = {}) {
  const ops = operations(overrides);
  const trustedRoot = typeof temporaryRoot === "string" ? await trustR22TemporaryRoot(temporaryRoot, ops) : temporaryRoot;
  if (typeof prefix !== "string" || !/^[a-z][a-z0-9-]{0,47}$/u.test(prefix)) fail("R22_CLI_ARGUMENT_INVALID");
  const directory = path.resolve(await ops.mkdtemp(path.join(trustedRoot.path, `.${prefix}-`)));
  if (!contained(trustedRoot.path, directory) || path.dirname(directory) !== trustedRoot.path) fail("R22_PATH_IDENTITY_INVALID");
  const observed = await observeDirectory(directory, trustedRoot, ops);
  const handle = Object.freeze(Object.create(null));
  ownedTemporaryDirectories.set(handle, { trustedRoot, ops, observed, removed: false });
  return Object.freeze({ handle, path: directory });
}

export async function removeR22OwnedTemporaryDirectory(handle) {
  const state = ownedTemporaryDirectories.get(handle);
  if (!state || state.removed) fail("R22_PATH_IDENTITY_INVALID");
  const current = await observeDirectory(state.observed.path, state.trustedRoot, state.ops, state.observed);
  if (!sameIdentity(current, state.observed) || path.dirname(current.path) !== state.trustedRoot.path) fail("R22_PATH_IDENTITY_INVALID");
  await state.ops.rm(current.path, { recursive: true, force: false });
  try {
    await state.ops.lstat(current.path, { bigint: true });
    fail("R22_STAGING_CLEANUP_FAILED");
  } catch (error) {
    if (error instanceof R22CliOperationalError) throw error;
    if (error?.code !== "ENOENT") fail("R22_STAGING_CLEANUP_FAILED", error);
  }
  state.removed = true;
}

export function directR22TemporaryChild(candidate, trustedRoot, suffix = null) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate) || candidate.includes("\0")) fail("R22_CLI_ARGUMENT_INVALID");
  const absolute = path.resolve(candidate); const name = path.basename(absolute);
  if (path.dirname(absolute) !== trustedRoot.path || !OUTPUT_NAME.test(name) || WINDOWS_DEVICE.test(name) || (suffix !== null && !name.endsWith(suffix))) fail("R22_CLI_ARGUMENT_INVALID");
  return absolute;
}

async function observeFile(candidate, trustedRoot, ops) {
  const absolute = path.resolve(candidate); let linked;
  try { linked = await ops.lstat(absolute, { bigint: true }); } catch { fail("R22_FILE_IDENTITY_INVALID"); }
  if (!linked.isFile() || linked.isSymbolicLink() || !contained(trustedRoot.path, absolute) || path.resolve(await ops.realpath(absolute)) !== absolute) fail("R22_FILE_IDENTITY_INVALID");
  return Object.freeze({ path: absolute, ...identity(linked) });
}
async function observeDirectory(candidate, trustedRoot, ops, expected = null) {
  const absolute = path.resolve(candidate); let linked;
  try { linked = await ops.lstat(absolute, { bigint: true }); } catch { fail("R22_DIRECTORY_IDENTITY_INVALID"); }
  const observed = Object.freeze({ path: absolute, ...identity(linked) });
  if (!linked.isDirectory() || linked.isSymbolicLink() || !contained(trustedRoot.path, absolute) || path.resolve(await ops.realpath(absolute)) !== absolute || (expected && !sameIdentity(expected, observed))) fail("R22_DIRECTORY_IDENTITY_INVALID");
  return observed;
}

export async function readStableR22File(candidate, maximumBytes, temporaryRoot, overrides = {}) {
  const ops = operations(overrides);
  const trustedRoot = typeof temporaryRoot === "string" ? await trustR22TemporaryRoot(temporaryRoot, ops) : temporaryRoot;
  if (!Number.isSafeInteger(maximumBytes) || maximumBytes < 1) fail("R22_FILE_LIMIT_INVALID");
  const before = await observeFile(candidate, trustedRoot, ops); let handle;
  try {
    handle = await ops.openFile(before.path, "r");
    const opened = identity(await handle.stat({ bigint: true }));
    if (!sameIdentity(before, opened) || opened.size < 1n || opened.size > BigInt(maximumBytes)) fail("R22_FILE_IDENTITY_INVALID");
    const bytes = new Uint8Array(await handle.readFile()); const afterOpen = identity(await handle.stat({ bigint: true }));
    if (!sameIdentity(opened, afterOpen) || opened.size !== afterOpen.size || opened.mtimeNs !== afterOpen.mtimeNs || opened.ctimeNs !== afterOpen.ctimeNs || bytes.byteLength !== Number(opened.size)) fail("R22_FILE_IDENTITY_INVALID");
    const after = await observeFile(before.path, trustedRoot, ops);
    const record = Object.freeze({ ...after, sha256: sha256(bytes), bytes: Uint8Array.from(bytes) });
    if (!sameRecord({ ...before, sha256: record.sha256 }, record)) fail("R22_FILE_IDENTITY_INVALID");
    return record;
  } finally { await handle?.close().catch(() => {}); }
}

export function decodeCanonicalR22Record(record, code = "R22_DOCUMENT_INVALID") {
  let text; let value;
  try { text = DECODER.decode(record.bytes); value = JSON.parse(text); } catch { fail(code); }
  if (canonicalizeJsonValue(value) !== text) fail(code);
  return Object.freeze({ text, value });
}

export async function readCanonicalR22File(candidate, maximumBytes, temporaryRoot, overrides = {}) {
  return decodeCanonicalR22Record(await readStableR22File(candidate, maximumBytes, temporaryRoot, overrides));
}

export async function revalidateR22FileRecord(record, maximumBytes, temporaryRoot, overrides = {}) {
  const current = await readStableR22File(record.path, maximumBytes, temporaryRoot, overrides);
  if (!sameRecord(record, current) || !Buffer.from(record.bytes).equals(Buffer.from(current.bytes))) fail("R22_INPUT_CHANGED");
  return current;
}

export async function assertExactR22DirectoryFiles(directory, expectedNames, temporaryRoot, overrides = {}) {
  const ops = operations(overrides); const trustedRoot = typeof temporaryRoot === "string" ? await trustR22TemporaryRoot(temporaryRoot, ops) : temporaryRoot;
  await observeDirectory(directory, trustedRoot, ops);
  const found = (await ops.readdir(path.resolve(directory))).sort(); const expected = [...expectedNames].sort();
  if (found.join("\0") !== expected.join("\0")) fail("R22_OUTPUT_CONTENT_INVALID");
  return Object.freeze(found);
}

export function parseR22Pairs(args, names, required) {
  if (!Array.isArray(args) || args.length !== required.length * 2) fail("R22_CLI_ARGUMENT_INVALID");
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const key = names[args[index]]; const value = args[index + 1];
    if (!key || Object.hasOwn(values, key) || typeof value !== "string" || value.includes("\0")) fail("R22_CLI_ARGUMENT_INVALID");
    values[key] = value;
  }
  if (required.some((key) => !Object.hasOwn(values, key))) fail("R22_CLI_ARGUMENT_INVALID");
  return values;
}

async function writeExclusive(file, bytes, ops) {
  let handle;
  try {
    handle = await ops.openFile(file, "wx");
    await handle.writeFile(bytes); await handle.sync();
    const observed = await handle.stat({ bigint: true });
    if (!observed.isFile() || observed.size !== BigInt(bytes.byteLength)) fail("R22_OUTPUT_WRITE_INVALID");
  } finally { await handle?.close().catch(() => {}); }
}
async function verifyArtifacts(directory, expected, trustedRoot, ops) {
  await observeDirectory(directory, trustedRoot, ops);
  const names = (await ops.readdir(directory)).sort(); const expectedNames = [...expected.keys()].sort();
  if (names.join("\0") !== expectedNames.join("\0")) fail("R22_OUTPUT_CONTENT_INVALID");
  for (const name of expectedNames) {
    const record = await readStableR22File(path.join(directory, name), expected.get(name).byteLength, trustedRoot, ops);
    if (!Buffer.from(record.bytes).equals(Buffer.from(expected.get(name)))) fail("R22_OUTPUT_CONTENT_INVALID");
  }
}
async function removeOwnedStage(stage, stageIdentity, expectedNames, trustedRoot, ops) {
  const current = await observeDirectory(stage, trustedRoot, ops, stageIdentity);
  const names = (await ops.readdir(stage)).sort();
  if (names.some((name) => !expectedNames.includes(name))) fail("R22_STAGING_CLEANUP_FAILED");
  for (const name of names) {
    const file = await observeFile(path.join(stage, name), trustedRoot, ops);
    if (!sameIdentity(current, await observeDirectory(stage, trustedRoot, ops, stageIdentity))) fail("R22_STAGING_CLEANUP_FAILED");
    await ops.rm(file.path, { force: false });
  }
  await ops.rmdir(stage);
}

export async function publishR22Artifacts({ output, temporaryRoot, artifacts, beforeRename = async () => {}, afterRename = async () => {}, verifyPublished = async () => {} }, overrides = {}) {
  const ops = operations(overrides); const trustedRoot = await trustR22TemporaryRoot(temporaryRoot, ops);
  const target = directR22TemporaryChild(output, trustedRoot); await missing(target, ops);
  if (!(artifacts instanceof Map) || artifacts.size < 1) fail("R22_OUTPUT_ARTIFACTS_INVALID");
  const expected = new Map();
  for (const [name, value] of [...artifacts.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    if (!OUTPUT_NAME.test(name) || path.basename(name) !== name) fail("R22_OUTPUT_ARTIFACTS_INVALID");
    const bytes = typeof value === "string" ? ENCODER.encode(value) : value;
    if (!(bytes instanceof Uint8Array) || bytes.byteLength < 1 || bytes.byteLength > 16 * 1024 * 1024) fail("R22_OUTPUT_ARTIFACTS_INVALID");
    expected.set(name, Uint8Array.from(bytes));
  }
  let stage = await ops.mkdtemp(path.join(trustedRoot.path, `.${path.basename(target)}-`));
  const stageIdentity = await observeDirectory(stage, trustedRoot, ops); let published = false; let targetIdentity = null; let primaryError = null;
  try {
    for (const [name, bytes] of expected) await writeExclusive(path.join(stage, name), bytes, ops);
    await verifyArtifacts(stage, expected, trustedRoot, ops); await beforeRename();
    await missing(target, ops); await observeDirectory(trustedRoot.path, trustedRoot, ops, trustedRoot);
    try { await ops.rename(stage, target); stage = null; }
    catch (error) {
      try { await verifyArtifacts(target, expected, trustedRoot, ops); stage = null; }
      catch { throw error; }
    }
    targetIdentity = await observeDirectory(target, trustedRoot, ops);
    await verifyArtifacts(target, expected, trustedRoot, ops); await verifyPublished(target); await afterRename();
    published = true;
    return Object.freeze({ output: target, files: Object.freeze([...expected.keys()].sort()), hashes: Object.freeze(Object.fromEntries([...expected].map(([name, bytes]) => [name, sha256(bytes)]))) });
  } catch (error) { primaryError = error; }
  finally {
    if (stage !== null) {
      try { await removeOwnedStage(stage, stageIdentity, [...expected.keys()], trustedRoot, ops); }
      catch (cleanupError) { primaryError = new R22CliOperationalError("R22_STAGING_CLEANUP_FAILED", cleanupError); }
    }
    if (!published && targetIdentity !== null) {
      try { await verifyArtifacts(target, expected, trustedRoot, ops); await removeOwnedStage(target, targetIdentity, [...expected.keys()], trustedRoot, ops); }
      catch (rollbackError) { primaryError = new R22CliOperationalError("R22_OUTPUT_ROLLBACK_FAILED", rollbackError); }
    }
  }
  if (!published) throw primaryError;
}

export async function observeR22SourceDirectory(root, requiredFile, expectedSha256, temporaryRoot, overrides = {}) {
  const ops = operations(overrides); const trustedRoot = typeof temporaryRoot === "string" ? await trustR22TemporaryRoot(temporaryRoot, ops) : temporaryRoot;
  const directory = await observeDirectory(root, trustedRoot, ops);
  const record = await readStableR22File(path.join(directory.path, requiredFile), 16 * 1024 * 1024, trustedRoot, ops);
  if (record.sha256 !== expectedSha256) fail("R22_SOURCE_IDENTITY_MISMATCH");
  return Object.freeze({ directory, record, requiredFile, expectedSha256 });
}

export async function revalidateR22SourceDirectory(source, temporaryRoot, overrides = {}) {
  const ops = operations(overrides); const trustedRoot = typeof temporaryRoot === "string" ? await trustR22TemporaryRoot(temporaryRoot, ops) : temporaryRoot;
  await observeDirectory(source.directory.path, trustedRoot, ops, source.directory);
  const current = await readStableR22File(path.join(source.directory.path, source.requiredFile), 16 * 1024 * 1024, trustedRoot, ops);
  if (!sameRecord(source.record, current) || !Buffer.from(source.record.bytes).equals(Buffer.from(current.bytes))) fail("R22_SOURCE_CHANGED");
  return current;
}
