import { createHash, randomBytes } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { canonicalJson, validateRuntimeSession } from "../contracts.mjs";

const CHECKPOINT_FORMAT = "modelmirror.ai-rpg.runtime-checkpoint", CHECKPOINT_VERSION = "0.1.0";
const MAX_BYTES = 16 * 1024 * 1024;
const ID_PATTERN = /^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$/u;
const diagnostic = (phase, code) => Object.freeze({ phase, severity: "error", code, path: "" });
const failure = (phase, code) => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(phase, code)]) });
const success = (value) => Object.freeze({ valid: true, diagnostics: Object.freeze([]), value });
const checkpointName = (sessionId) => `session-${sessionId}.json`;
const ownerName = ".owner.lock", claimName = ".claim.lock";

export function sha256(text) { return createHash("sha256").update(text).digest("hex"); }

function validSessionId(value) { return typeof value === "string" && ID_PATTERN.test(value); }
function localAbsolute(value) {
  if (typeof value !== "string" || !path.isAbsolute(value)) return false;
  const root = path.parse(value).root;
  return root !== "\\" && !root.startsWith("\\\\") && !value.startsWith("//");
}
async function inspectExistingAncestors(target) {
  const absolute = path.resolve(target), parsed = path.parse(absolute), relative = absolute.slice(parsed.root.length), parts = relative.split(path.sep).filter(Boolean); let current = parsed.root;
  for (const part of parts) {
    current = path.join(current, part);
    let stat; try { stat = await fsp.lstat(current); } catch (error) { if (error?.code === "ENOENT") continue; return false; }
    if (stat.isSymbolicLink()) return false;
  }
  return true;
}
async function safeRegularFile(file) {
  let stat; try { stat = await fsp.lstat(file); } catch (error) { return error?.code === "ENOENT" ? null : false; }
  return stat.isFile() && !stat.isSymbolicLink() && stat.nlink === 1 ? stat : false;
}
async function writeExclusive(file, bytes) {
  const handle = await fsp.open(file, "wx", 0o600);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
}
async function parseCanonicalFile(file, sizeCode, parseCode, maximum = MAX_BYTES) {
  let initial; try { initial = await fsp.lstat(file); } catch (error) { return error?.code === "ENOENT" ? success(null) : failure("storage", parseCode); }
  if (initial.isSymbolicLink() || !initial.isFile() || initial.nlink !== 1) return failure("storage", parseCode);
  let handle; try { handle = await fsp.open(file, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0)); } catch { return failure("storage", parseCode); }
  let bytes;
  try {
    const [opened, linked] = await Promise.all([handle.stat(), fsp.lstat(file)]);
    if (!opened.isFile() || linked.isSymbolicLink() || !linked.isFile() || opened.nlink !== 1 || linked.nlink !== 1 || initial.dev !== opened.dev || initial.ino !== opened.ino || opened.dev !== linked.dev || opened.ino !== linked.ino) return failure("storage", parseCode);
    if (opened.size > maximum) return failure("storage", sizeCode);
    const chunks = []; let total = 0;
    while (total <= maximum) { const chunk = Buffer.allocUnsafe(Math.min(65536, maximum + 1 - total)); const { bytesRead } = await handle.read(chunk, 0, chunk.length, null); if (bytesRead === 0) break; chunks.push(chunk.subarray(0, bytesRead)); total += bytesRead; }
    if (total > maximum) return failure("storage", sizeCode); bytes = Buffer.concat(chunks, total);
  } catch { return failure("storage", parseCode); } finally { await handle.close().catch(() => {}); }
  let text; try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { return failure("storage", parseCode); }
  if (!text.endsWith("\n") || text.slice(0, -1).includes("\n")) return failure("storage", parseCode);
  let value; try { value = JSON.parse(text); } catch { return failure("storage", parseCode); }
  const canonical = canonicalJson(value); if (!canonical.valid || !Buffer.from(`${canonical.value}\n`, "utf8").equals(bytes)) return failure("storage", parseCode);
  return success(value);
}
function wrapperFor(session) {
  const canonical = canonicalJson(session);
  return { format: CHECKPOINT_FORMAT, formatVersion: CHECKPOINT_VERSION, sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, sessionSha256: sha256(canonical.value), session };
}
function unwrap(value, sessionId, cardPackage, playerSetup) {
  if (value === null) return success(null);
  const keys = value && typeof value === "object" && !Array.isArray(value) ? Object.keys(value).sort() : [];
  const sessionCanonical = canonicalJson(value?.session);
  if (JSON.stringify(keys) !== JSON.stringify(["cardPackageSha256", "format", "formatVersion", "playerSetupSha256", "session", "sessionId", "sessionSha256"]) || value.format !== CHECKPOINT_FORMAT || value.formatVersion !== CHECKPOINT_VERSION || value.sessionId !== sessionId || value.session?.sessionId !== sessionId || value.cardPackageSha256 !== value.session?.resources?.cardPackage?.sha256 || value.playerSetupSha256 !== value.session?.resources?.playerSetup?.sha256 || !sessionCanonical.valid || value.sessionSha256 !== sha256(sessionCanonical.value)) return failure("storage", "RUNTIME_STORE_CHECKPOINT_INVALID");
  const report = validateRuntimeSession(value.session, cardPackage, playerSetup, sha256);
  return report.valid ? success(structuredClone(value.session)) : failure("storage", "RUNTIME_STORE_SESSION_INVALID");
}
function ownerBytes(token) { const value = canonicalJson({ pid: process.pid, token }); return Buffer.from(`${value.value}\n`, "utf8"); }
async function ownerStatus(ownerPath) {
  const parsed = await parseCanonicalFile(ownerPath, "RUNTIME_STORE_OWNER_INVALID", "RUNTIME_STORE_OWNER_INVALID", 4096);
  if (!parsed.valid || !parsed.value || !Number.isSafeInteger(parsed.value.pid) || parsed.value.pid <= 0 || typeof parsed.value.token !== "string" || !/^[a-f0-9]{32}$/u.test(parsed.value.token) || Object.keys(parsed.value).length !== 2) return { kind: "unknown" };
  try { process.kill(parsed.value.pid, 0); return { kind: "live" }; } catch (error) { return error?.code === "ESRCH" ? { kind: "dead" } : { kind: "unknown" }; }
}

export async function openFileSessionStore({ rootDirectory } = {}) {
  if (!localAbsolute(rootDirectory) || !(await inspectExistingAncestors(rootDirectory))) return failure("storage", "RUNTIME_STORE_ROOT_UNSAFE");
  const root = path.resolve(rootDirectory); try { await fsp.mkdir(root, { recursive: true }); } catch { return failure("storage", "RUNTIME_STORE_ROOT_UNAVAILABLE"); }
  if (!(await inspectExistingAncestors(root))) return failure("storage", "RUNTIME_STORE_ROOT_UNSAFE");
  const rootStat = await fsp.lstat(root).catch(() => null); if (!rootStat?.isDirectory() || rootStat.isSymbolicLink()) return failure("storage", "RUNTIME_STORE_ROOT_UNSAFE");
  const claimPath = path.join(root, claimName), ownerPath = path.join(root, ownerName), token = randomBytes(16).toString("hex");
  try { await writeExclusive(claimPath, ownerBytes(token)); } catch { return failure("storage", "RUNTIME_STORE_CLAIMED"); }
  let acquired = false, acquisitionFailure = null;
  try {
    const ownerFile = await safeRegularFile(ownerPath);
    if (ownerFile === false) acquisitionFailure = failure("storage", "RUNTIME_STORE_OWNER_INVALID");
    if (ownerFile) {
      const state = await ownerStatus(ownerPath); if (state.kind !== "dead") acquisitionFailure = failure("storage", state.kind === "live" ? "RUNTIME_STORE_OWNER_ACTIVE" : "RUNTIME_STORE_OWNER_UNKNOWN");
      else try { await fsp.rename(ownerPath, path.join(root, `.owner.dead-${Date.now()}-${randomBytes(4).toString("hex")}.lock`)); } catch { acquisitionFailure = failure("storage", "RUNTIME_STORE_OWNER_ARCHIVE_FAILED"); }
    }
    if (!acquisitionFailure) try { await writeExclusive(ownerPath, ownerBytes(token)); acquired = true; } catch { acquisitionFailure = failure("storage", "RUNTIME_STORE_OWNER_ACTIVE"); }
  } finally {
    const claim = await parseCanonicalFile(claimPath, "RUNTIME_STORE_CLAIM_LOST", "RUNTIME_STORE_CLAIM_LOST", 4096), ownClaim = claim.valid && claim.value?.pid === process.pid && claim.value?.token === token;
    if (ownClaim) await fsp.unlink(claimPath).catch(() => { acquisitionFailure = failure("storage", "RUNTIME_STORE_CLAIM_LOST"); }); else acquisitionFailure = failure("storage", "RUNTIME_STORE_CLAIM_LOST");
  }
  if (acquisitionFailure || !acquired) {
    if (acquired) { const owner = await parseCanonicalFile(ownerPath, "RUNTIME_STORE_OWNER_INVALID", "RUNTIME_STORE_OWNER_INVALID", 4096); if (owner.valid && owner.value?.pid === process.pid && owner.value?.token === token) await fsp.unlink(ownerPath).catch(() => {}); }
    return acquisitionFailure ?? failure("storage", "RUNTIME_STORE_OWNER_ACTIVE");
  }

  let queue = Promise.resolve(), closed = false, tempCounter = 0;
  const enqueue = (operation) => { if (closed) return Promise.resolve(failure("storage", "RUNTIME_STORE_CLOSED")); const run = queue.then(operation, operation); queue = run.then(() => undefined, () => undefined); return run; };
  async function ownsLock() {
    if (!(await inspectExistingAncestors(root))) return false;
    const parsed = await parseCanonicalFile(ownerPath, "RUNTIME_STORE_OWNER_INVALID", "RUNTIME_STORE_OWNER_INVALID", 4096); return parsed.valid && parsed.value?.pid === process.pid && parsed.value?.token === token;
  }
  async function readInternal(sessionId, options) {
    if (!validSessionId(sessionId) || !options?.cardPackage || !options?.playerSetup || !(await ownsLock())) return failure("storage", "RUNTIME_STORE_READ_ARGUMENT");
    const parsed = await parseCanonicalFile(path.join(root, checkpointName(sessionId)), "RUNTIME_STORE_CHECKPOINT_TOO_LARGE", "RUNTIME_STORE_CHECKPOINT_INVALID"); if (!parsed.valid) return parsed;
    return unwrap(parsed.value, sessionId, options.cardPackage, options.playerSetup);
  }
  const store = Object.freeze({
    read(sessionId, options) { if (!validSessionId(sessionId) || !canonicalJson(options?.cardPackage).valid || !canonicalJson(options?.playerSetup).valid) return Promise.resolve(failure("storage", "RUNTIME_STORE_READ_ARGUMENT")); const snapshot = { cardPackage: structuredClone(options.cardPackage), playerSetup: structuredClone(options.playerSetup) }; return enqueue(() => readInternal(sessionId, snapshot)); },
    write(session, options) { if (!validSessionId(session?.sessionId) || !options?.cardPackage || !options?.playerSetup || !(options.expectedRevision === null || Number.isSafeInteger(options.expectedRevision))) return Promise.resolve(failure("storage", "RUNTIME_STORE_WRITE_ARGUMENT")); const validation = validateRuntimeSession(session, options.cardPackage, options.playerSetup, sha256); if (!validation.valid) return Promise.resolve(failure("storage", "RUNTIME_STORE_SESSION_INVALID")); const snapshot = { session: structuredClone(session), cardPackage: structuredClone(options.cardPackage), playerSetup: structuredClone(options.playerSetup), expectedRevision: options.expectedRevision }; return enqueue(async () => {
      if (!(await ownsLock())) return failure("storage", "RUNTIME_STORE_WRITE_ARGUMENT");
      const current = await readInternal(snapshot.session.sessionId, snapshot); if (!current.valid) return current;
      if (snapshot.expectedRevision === null ? current.value !== null || snapshot.session.revision !== 0 : current.value === null || current.value.revision !== snapshot.expectedRevision || snapshot.session.revision !== snapshot.expectedRevision + 1) return failure("storage", "RUNTIME_STORE_REVISION_CONFLICT");
      const canonical = canonicalJson(wrapperFor(snapshot.session)); if (!canonical.valid) return failure("storage", "RUNTIME_STORE_CHECKPOINT_INVALID"); const bytes = Buffer.from(`${canonical.value}\n`, "utf8"); if (bytes.length > MAX_BYTES) return failure("storage", "RUNTIME_STORE_CHECKPOINT_TOO_LARGE");
      const target = path.join(root, checkpointName(snapshot.session.sessionId)), temporary = path.join(root, `${checkpointName(snapshot.session.sessionId)}.${token}.${tempCounter += 1}.tmp`);
      try { await writeExclusive(temporary, bytes); if (!(await ownsLock()) || await safeRegularFile(temporary) === false) return failure("storage", "RUNTIME_STORE_OWNERSHIP_LOST"); await fsp.rename(temporary, target); } catch { return failure("storage", "RUNTIME_STORE_WRITE_FAILED"); }
      return success(structuredClone(snapshot.session));
    }); },
    async close() { if (closed) return failure("storage", "RUNTIME_STORE_CLOSED"); closed = true; await queue; if (!(await ownsLock())) return failure("storage", "RUNTIME_STORE_OWNERSHIP_LOST"); try { await fsp.unlink(ownerPath); } catch { return failure("storage", "RUNTIME_STORE_CLOSE_FAILED"); } return success(null); },
  });
  return success(store);
}
