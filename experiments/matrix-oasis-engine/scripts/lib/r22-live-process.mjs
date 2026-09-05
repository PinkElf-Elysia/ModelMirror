import { randomBytes } from "node:crypto";
import { lstat, open, realpath, unlink } from "node:fs/promises";
import path from "node:path";
import { canonicalText, decodeCanonicalR22Record, readStableR22File, revalidateR22FileRecord, sha256, trustR22TemporaryRoot } from "./r22-cli-core.mjs";
const SHA = /^sha256:[0-9a-f]{64}$/u, handles = new WeakMap();
const defaults = { lstat, openFile: open, realpath, unlink, probeProcess: process.kill.bind(process) };
function fail(code = "R22_RESIDUAL_GODOT_ACTIVE") { throw new Error(code); }
function exact(v, keys) { return v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).sort().join("\0") === [...keys].sort().join("\0"); }
function identity(v) { if (!exact(v, ["sourceSha256", "implementationSha256", "godotBinarySha256"]) || !SHA.test(v.sourceSha256) || !SHA.test(v.implementationSha256) || !SHA.test(v.godotBinarySha256)) fail("R22_LIVE_PROCESS_IDENTITY_INVALID"); return Object.freeze({ ...v }); }
function sameIdentity(a, b) { return a.sourceSha256 === b.sourceSha256 && a.implementationSha256 === b.implementationSha256 && a.godotBinarySha256 === b.godotBinarySha256; }
function sameFs(a, b) { return a.dev === b.dev && a.ino === b.ino && a.size === b.size && a.mtimeNs === b.mtimeNs && a.ctimeNs === b.ctimeNs; }
async function context(cognitionRunRoot, overrides) {
  const ops = { ...defaults, ...overrides }, root = path.resolve(cognitionRunRoot), temporaryRoot = path.dirname(root);
  const requiredTemporaryRoot = overrides?.expectedTemporaryRoot === undefined ? path.resolve(path.parse(root).root, "tmp") : path.resolve(overrides.expectedTemporaryRoot);
  if (!/-cognition$/u.test(path.basename(root)) || temporaryRoot.toLowerCase() !== requiredTemporaryRoot.toLowerCase()) fail();
  try { const trusted = await trustR22TemporaryRoot(temporaryRoot, ops), directory = await ops.lstat(root, { bigint: true });
    if (!directory.isDirectory() || directory.isSymbolicLink() || path.resolve(await ops.realpath(root)) !== root) fail();
    return { ops, trusted, root, directory, reservation: path.join(root, "r22-live-process-reservation.json"), running: path.join(root, "r22-live-process-running.json") };
  } catch { fail(); }
}
async function verifyDirectory(c) { let now; try { now = await c.ops.lstat(c.root, { bigint: true }); } catch { fail(); } if (!now.isDirectory() || now.isSymbolicLink() || path.resolve(await c.ops.realpath(c.root)) !== c.root || now.dev !== c.directory.dev || now.ino !== c.directory.ino) fail(); }
async function optional(file, c) {
  try { return await readStableR22File(file, 4096, c.trusted, c.ops); }
  catch { try { await c.ops.lstat(file, { bigint: true }); } catch (e) { if (e?.code === "ENOENT") return null; } fail(); }
}
function decoded(record, format, keys) { if (!record) return null; let v; try { v = decodeCanonicalR22Record(record).value; } catch { fail(); } if (!exact(v, keys) || v.format !== format || v.formatVersion !== "0.1.0" || v.canonicalization !== "matrix-oasis.canonical-json/1") fail(); return v; }
async function persisted(file, value, c) {
  const text = canonicalText(value); let h;
  try { await verifyDirectory(c); h = await c.ops.openFile(file, "wx"); await h.writeFile(text, "utf8"); await h.sync(); const s = await h.stat({ bigint: true }); if (!s.isFile() || s.size !== BigInt(Buffer.byteLength(text))) fail(); }
  catch (e) { if (e?.code === "EEXIST") fail(); throw e; } finally { await h?.close().catch(() => {}); }
  const record = await readStableR22File(file, 4096, c.trusted, c.ops); if (Buffer.from(record.bytes).toString("utf8") !== text) fail(); return record;
}
function probe(pid, fn) { try { fn(pid, 0); return true; } catch (e) { return e?.code !== "ESRCH"; } }
async function stable(record, c) { try { const now = await revalidateR22FileRecord(record, 4096, c.trusted, c.ops); if (!sameFs(record, now)) fail(); } catch { fail(); } }
async function removeRecord(record, c) { await verifyDirectory(c); await stable(record, c); await c.ops.unlink(record.path); await verifyDirectory(c); }
async function inspect(root, overrides) {
  const c = await context(root, overrides), rr = await optional(c.reservation, c), pr = await optional(c.running, c); if (!rr && !pr) return { c, clear: true };
  const r = decoded(rr, "matrix-oasis.r22-live-process-reservation", ["format", "formatVersion", "canonicalization", "launchIdSha256", "identity"]);
  const p = decoded(pr, "matrix-oasis.r22-live-process-running", ["format", "formatVersion", "canonicalization", "reservationSha256", "pid"]);
  if (!r || !p || !SHA.test(r.launchIdSha256 ?? "") || !exact(r.identity, ["sourceSha256", "implementationSha256", "godotBinarySha256"]) || p.reservationSha256 !== rr.sha256 || !Number.isSafeInteger(p.pid) || p.pid <= 0) fail();
  return { c, rr, pr, r, p };
}
export async function preflightR22ResidualProcess({ cognitionRunRoot }, overrides = {}) {
  const s = await inspect(cognitionRunRoot, overrides); if (s.clear) return Object.freeze({ status: "clear" }); if (probe(s.p.pid, s.c.ops.probeProcess)) fail();
  await stable(s.rr, s.c); await stable(s.pr, s.c); await verifyDirectory(s.c); if (probe(s.p.pid, s.c.ops.probeProcess)) fail(); return Object.freeze({ status: "dead-pending-identity" });
}
export async function guardR22ResidualProcess({ cognitionRunRoot, expectedIdentity }, overrides = {}) {
  const expected = identity(expectedIdentity), s = await inspect(cognitionRunRoot, overrides); if (s.clear) return Object.freeze({ status: "clear" });
  if (!sameIdentity(identity(s.r.identity), expected) || probe(s.p.pid, s.c.ops.probeProcess)) fail(); await stable(s.rr, s.c); await stable(s.pr, s.c); await verifyDirectory(s.c); if (probe(s.p.pid, s.c.ops.probeProcess)) fail();
  await removeRecord(s.pr, s.c); await removeRecord(s.rr, s.c); return Object.freeze({ status: "dead-cleared" });
}
export async function reserveR22LiveProcess({ cognitionRunRoot, identity: value }, overrides = {}) {
  const c = await context(cognitionRunRoot, overrides), rr = await persisted(c.reservation, { format: "matrix-oasis.r22-live-process-reservation", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", launchIdSha256: sha256(randomBytes(32)), identity: identity(value) }, c);
  const token = Object.freeze(Object.create(null)); handles.set(token, { c, rr, pr: null }); return token;
}
export async function markR22LiveProcessRunning(token, pid) {
  const s = handles.get(token); if (!s || s.pr || !Number.isSafeInteger(pid) || pid <= 0) fail("R22_LIVE_PROCESS_IDENTITY_INVALID"); await stable(s.rr, s.c);
  s.pr = await persisted(s.c.running, { format: "matrix-oasis.r22-live-process-running", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", reservationSha256: s.rr.sha256, pid }, s.c);
}
export async function clearR22LiveProcess(token) { const s = handles.get(token); if (!s) fail("R22_LIVE_PROCESS_IDENTITY_INVALID"); if (s.pr) await removeRecord(s.pr, s.c); await removeRecord(s.rr, s.c); handles.delete(token); }
