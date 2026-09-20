import assert from "node:assert/strict";
import { mkdtemp, open, readFile, rename, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { clearR22LiveProcess, guardR22ResidualProcess, markR22LiveProcessRunning, preflightR22ResidualProcess, reserveR22LiveProcess } from "../scripts/lib/r22-live-process.mjs";

const identity = Object.freeze({ sourceSha256: `sha256:${"1".repeat(64)}`, implementationSha256: `sha256:${"2".repeat(64)}`, godotBinarySha256: `sha256:${"3".repeat(64)}` });
const io = (cognitionRunRoot, extra = {}) => ({ expectedTemporaryRoot: path.dirname(cognitionRunRoot), ...extra });
async function root(t, name = "run-cognition") { const parent = await mkdtemp(path.join(os.tmpdir(), "r22-process-")); t.after(() => rm(parent, { recursive: true, force: true })); const value = path.join(parent, name); await import("node:fs/promises").then(({ mkdir }) => mkdir(value)); return value; }
const dead = () => { const error = new Error("dead"); error.code = "ESRCH"; throw error; };

test("parent crash with a live child and PID ambiguity both refuse resume", async (t) => {
  for (const probeProcess of [() => {}, () => { const error = new Error("denied"); error.code = "EPERM"; throw error; }]) {
    const cognitionRunRoot = await root(t, `run-${Math.random()}-cognition`); const lease = await reserveR22LiveProcess({ cognitionRunRoot, identity }, io(cognitionRunRoot)); await markR22LiveProcessRunning(lease, 42);
    await assert.rejects(preflightR22ResidualProcess({ cognitionRunRoot }, io(cognitionRunRoot, { probeProcess })), /R22_RESIDUAL_GODOT_ACTIVE/u);
  }
});

test("dead child is retained until identity matches, then cleared", async (t) => {
  const cognitionRunRoot = await root(t); const lease = await reserveR22LiveProcess({ cognitionRunRoot, identity }, io(cognitionRunRoot)); await markR22LiveProcessRunning(lease, 43);
  assert.equal((await preflightR22ResidualProcess({ cognitionRunRoot }, io(cognitionRunRoot, { probeProcess: dead }))).status, "dead-pending-identity");
  await assert.rejects(guardR22ResidualProcess({ cognitionRunRoot, expectedIdentity: { ...identity, sourceSha256: `sha256:${"9".repeat(64)}` } }, io(cognitionRunRoot, { probeProcess: dead })), /R22_RESIDUAL_GODOT_ACTIVE/u);
  assert.equal((await guardR22ResidualProcess({ cognitionRunRoot, expectedIdentity: identity }, io(cognitionRunRoot, { probeProcess: dead }))).status, "dead-cleared");
  assert.equal((await preflightR22ResidualProcess({ cognitionRunRoot }, io(cognitionRunRoot, { probeProcess: dead }))).status, "clear");
});

test("launch reservation closes the spawn-to-PID crash window", async (t) => {
  const cognitionRunRoot = await root(t); await reserveR22LiveProcess({ cognitionRunRoot, identity }, io(cognitionRunRoot));
  await assert.rejects(preflightR22ResidualProcess({ cognitionRunRoot }, io(cognitionRunRoot, { probeProcess: dead })), /R22_RESIDUAL_GODOT_ACTIVE/u);
});

test("a PID becoming live during dead cleanup is a conservative TOCTOU refusal", async (t) => {
  const cognitionRunRoot = await root(t); const lease = await reserveR22LiveProcess({ cognitionRunRoot, identity }, io(cognitionRunRoot)); await markR22LiveProcessRunning(lease, 44);
  let calls = 0; const probeProcess = () => { calls += 1; if (calls === 1) return dead(); };
  await assert.rejects(guardR22ResidualProcess({ cognitionRunRoot, expectedIdentity: identity }, io(cognitionRunRoot, { probeProcess })), /R22_RESIDUAL_GODOT_ACTIVE/u);
  assert.match(await readFile(path.join(cognitionRunRoot, "r22-live-process-running.json"), "utf8"), /"pid":44/u);
});

test("normal close clears only its own run after child close", async (t) => {
  const first = await root(t, "first-cognition"), other = await root(t, "other-cognition");
  const firstLease = await reserveR22LiveProcess({ cognitionRunRoot: first, identity }, io(first)); await markR22LiveProcessRunning(firstLease, 45);
  const otherLease = await reserveR22LiveProcess({ cognitionRunRoot: other, identity }, io(other)); await markR22LiveProcessRunning(otherLease, 46);
  const order = []; order.push("child-close"); await clearR22LiveProcess(firstLease); order.push("record-clear");
  assert.deepEqual(order, ["child-close", "record-clear"]); assert.equal((await preflightR22ResidualProcess({ cognitionRunRoot: first }, io(first, { probeProcess: dead }))).status, "clear");
  await assert.rejects(preflightR22ResidualProcess({ cognitionRunRoot: other }, io(other, { probeProcess: () => {} })), /R22_RESIDUAL_GODOT_ACTIVE/u);
  await clearR22LiveProcess(otherLease);
});

test("closed records reject unknown fields and same-byte inode replacement", async (t) => {
  const first = await root(t, "unknown-cognition"), lease = await reserveR22LiveProcess({ cognitionRunRoot: first, identity }, io(first)); await markR22LiveProcessRunning(lease, 50);
  const reservation = path.join(first, "r22-live-process-reservation.json"), text = await readFile(reservation, "utf8"), value = JSON.parse(text);
  value.unknown = true; await writeFile(reservation, JSON.stringify(value));
  await assert.rejects(preflightR22ResidualProcess({ cognitionRunRoot: first }, io(first, { probeProcess: dead })), /R22_RESIDUAL_GODOT_ACTIVE/u);
  const second = await root(t, "replacement-cognition"), secondLease = await reserveR22LiveProcess({ cognitionRunRoot: second, identity }, io(second));
  const secondFile = path.join(second, "r22-live-process-reservation.json"), same = await readFile(secondFile); await rename(secondFile, `${secondFile}.old`); await writeFile(secondFile, same);
  await assert.rejects(clearR22LiveProcess(secondLease), /R22_RESIDUAL_GODOT_ACTIVE/u);
});

test("junction-like realpath drift and reservation fsync failure fail closed", async (t) => {
  const drift = await root(t, "drift-cognition"); let realpathCalls = 0;
  await assert.rejects(reserveR22LiveProcess({ cognitionRunRoot: drift, identity }, io(drift, { realpath: async (candidate) => { realpathCalls += 1; return realpathCalls > 1 ? `${candidate}-moved` : candidate; } })), /R22_RESIDUAL_GODOT_ACTIVE/u);
  const failed = await root(t, "fsync-cognition");
  await assert.rejects(reserveR22LiveProcess({ cognitionRunRoot: failed, identity }, io(failed, { openFile: async (...args) => { const handle = await open(...args); handle.sync = async () => { throw new Error("disk"); }; return handle; } })), /disk/u);
  await assert.rejects(preflightR22ResidualProcess({ cognitionRunRoot: failed }, io(failed, { probeProcess: dead })), /R22_RESIDUAL_GODOT_ACTIVE/u);
});
