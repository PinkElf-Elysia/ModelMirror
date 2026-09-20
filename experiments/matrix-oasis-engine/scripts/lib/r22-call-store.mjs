import { createHash, randomBytes } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readdir,
  realpath,
  rename,
  rmdir,
  rm,
} from "node:fs/promises";
import path from "node:path";
import {
  NPC_COGNITION_LIMITS,
  validateNpcCognitionCallPlanJson,
  validateNpcCognitionTurnReceiptJson,
} from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const CANONICALIZATION = "matrix-oasis.canonical-json/1";
const SHA256 = /^sha256:[0-9a-f]{64}$/u;
const IDENTIFIER = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/u;
const TURN_DIRECTORY = /^[0-9]{6}-[0-9a-f]{64}$/u;
const MAX_INTERNAL_BYTES = 1024 * 1024;

const defaultOperations = Object.freeze({
  lstat,
  mkdir,
  mkdtemp,
  openFile: open,
  readdir,
  realpath,
  rename,
  rmdir,
  rm,
  randomBytes,
  processId: process.pid,
  isProcessAlive(pid) {
    try {
      process.kill(pid, 0);
      return true;
    } catch (error) {
      return error?.code !== "ESRCH";
    }
  },
});

const storeStates = new WeakMap();

export class R22CallStoreOperationalError extends Error {
  constructor(code = "NPC_COGNITION_INTERNAL_ERROR") {
    super(code);
    this.name = "R22CallStoreOperationalError";
    this.code = code;
  }
}

function fail(code = "NPC_COGNITION_INTERNAL_ERROR") {
  throw new R22CallStoreOperationalError(code);
}

function sha256Text(text) {
  return `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
}

export function computeR22DisplayAckHash(input) {
  const keys = ["approvalTokenSha256", "turnSha256", "callPlanSha256", "proposalSha256", "actionChoiceId"];
  if (!exactObject(input, keys) || ![input.approvalTokenSha256, input.turnSha256, input.callPlanSha256, input.proposalSha256].every((value) => SHA256.test(value ?? "")) ||
      (input.actionChoiceId !== null && !/^choice-[0-9a-f]{64}$/u.test(input.actionChoiceId))) {
    fail("R22_STORE_DISPLAY_ACK_INVALID");
  }
  return sha256Text(canonicalizeJsonValue({
    purpose: "matrix-oasis.r22-dialogue-display-ack/1",
    approvalTokenSha256: input.approvalTokenSha256,
    turnSha256: input.turnSha256,
    callPlanSha256: input.callPlanSha256,
    proposalSha256: input.proposalSha256,
    actionChoiceId: input.actionChoiceId,
  }));
}

function exactObject(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype &&
    Reflect.ownKeys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function identity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint"
    ? `${stat.dev}:${stat.ino}`
    : null;
}

function stableState(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? `${stat.size}:${stat.mtimeNs}:${stat.ctimeNs}`
    : null;
}

function isPlainDirectory(stat) {
  return stat?.isDirectory?.() === true && stat.isSymbolicLink?.() !== true;
}

function isPlainFile(stat) {
  return stat?.isFile?.() === true && stat.isSymbolicLink?.() !== true;
}

function samePath(left, right) {
  return path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase();
}

function isDirectChild(parent, candidate) {
  return path.dirname(path.resolve(candidate)).toLowerCase() === path.resolve(parent).toLowerCase();
}

function validateLedgerPoint(value, code = "R22_STORE_LEDGER_POINT_INVALID") {
  if (!exactObject(value, ["revision", "headSha256", "runtimeSnapshotSha256"]) ||
      !Number.isSafeInteger(value.revision) || value.revision < 0 || value.revision > 10_000 ||
      (value.revision === 0 ? value.headSha256 !== null : !SHA256.test(value.headSha256 ?? "")) ||
      !SHA256.test(value.runtimeSnapshotSha256 ?? "")) fail(code);
  return Object.freeze(structuredClone(value));
}

function validateIdentifier(value, code) {
  if (typeof value !== "string" || value.length > 96 || !IDENTIFIER.test(value)) fail(code);
  return value;
}

function canonicalDocument(text, validator, code) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") < 1 || Buffer.byteLength(text, "utf8") > MAX_INTERNAL_BYTES) fail(code);
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    fail(code);
  }
  let canonical;
  try {
    canonical = canonicalizeJsonValue(value);
  } catch {
    fail(code);
  }
  const report = validator(text);
  if (canonical !== text || report?.valid !== true || report.diagnostics?.length !== 0) fail(code);
  return value;
}

async function assertDirectory(candidate, expectedIdentity, operations, code = "R22_STORE_PATH_IDENTITY_INVALID") {
  try {
    const linked = await operations.lstat(candidate, { bigint: true });
    if (!isPlainDirectory(linked) || identity(linked) !== expectedIdentity ||
        !samePath(await operations.realpath(candidate), candidate)) fail(code);
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError) throw error;
    fail(code);
  }
}

async function ensureDirectory(candidate, operations) {
  try {
    const linked = await operations.lstat(candidate, { bigint: true });
    const foundIdentity = identity(linked);
    if (!isPlainDirectory(linked) || foundIdentity === null || !samePath(await operations.realpath(candidate), candidate)) {
      fail("R22_STORE_PATH_IDENTITY_INVALID");
    }
    return foundIdentity;
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError) throw error;
    if (error?.code !== "ENOENT") fail("R22_STORE_PATH_IDENTITY_INVALID");
    try {
      await operations.mkdir(candidate, { recursive: false });
      const linked = await operations.lstat(candidate, { bigint: true });
      const foundIdentity = identity(linked);
      if (!isPlainDirectory(linked) || foundIdentity === null || !samePath(await operations.realpath(candidate), candidate)) {
        fail("R22_STORE_PATH_IDENTITY_INVALID");
      }
      return foundIdentity;
    } catch (creationError) {
      if (creationError instanceof R22CallStoreOperationalError) throw creationError;
      fail("R22_STORE_PATH_IDENTITY_INVALID");
    }
  }
}

async function readStableText(candidate, operations, maximumBytes = MAX_INTERNAL_BYTES) {
  let handle;
  try {
    const absolute = path.resolve(candidate);
    const linked = await operations.lstat(absolute, { bigint: true });
    if (!isPlainFile(linked) || linked.size < 1n || linked.size > BigInt(maximumBytes) ||
        !samePath(await operations.realpath(absolute), absolute)) fail("R22_STORE_FILE_IDENTITY_INVALID");
    handle = await operations.openFile(absolute, "r");
    const before = await handle.stat({ bigint: true });
    if (!isPlainFile(before) || identity(before) === null || identity(before) !== identity(linked) || stableState(before) === null) {
      fail("R22_STORE_FILE_IDENTITY_INVALID");
    }
    const bytes = new Uint8Array(await handle.readFile());
    const after = await handle.stat({ bigint: true });
    if (identity(after) !== identity(before) || stableState(after) !== stableState(before) || bytes.byteLength !== Number(before.size)) {
      fail("R22_STORE_FILE_IDENTITY_INVALID");
    }
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError || error?.code === "ENOENT") throw error;
    fail("R22_STORE_FILE_IDENTITY_INVALID");
  } finally {
    await handle?.close().catch(() => {});
  }
}

async function writeExclusive(candidate, text, operations) {
  let handle;
  try {
    handle = await operations.openFile(candidate, "wx+");
    const before = await handle.stat({ bigint: true });
    if (!isPlainFile(before) || identity(before) === null) fail("R22_STORE_WRITE_FAILED");
    await handle.writeFile(new TextEncoder().encode(text));
    await handle.sync();
    const after = await handle.stat({ bigint: true });
    if (identity(after) !== identity(before) || after.size !== BigInt(Buffer.byteLength(text, "utf8"))) fail("R22_STORE_WRITE_FAILED");
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError || error?.code === "EEXIST") throw error;
    fail("R22_STORE_WRITE_FAILED");
  } finally {
    await handle?.close().catch(() => {});
  }
  if (await readStableText(candidate, operations) !== text) fail("R22_STORE_WRITE_FAILED");
}

async function writeAtomic(candidate, text, operations, { immutable = false, guardedCleanup = false } = {}) {
  try {
    const existing = await readStableText(candidate, operations);
    if (existing === text) return false;
    if (immutable) fail("R22_STORE_IMMUTABLE_CONFLICT");
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError) throw error;
    if (error?.code !== "ENOENT") throw error;
  }
  const parent = path.dirname(candidate);
  const parentStat = await operations.lstat(parent, { bigint: true });
  const parentIdentity = identity(parentStat);
  if (!isPlainDirectory(parentStat) || parentIdentity === null || !samePath(await operations.realpath(parent), parent)) {
    fail("R22_STORE_PATH_IDENTITY_INVALID");
  }
  // Keep the staging leaf deliberately short. Windows still enforces legacy
  // path-length limits in some filesystem operations, while the immutable
  // turn directory already carries the full content identity.
  const staging = await operations.mkdtemp(path.join(parent, ".s-"));
  let moved = false;
  let stageIdentity = null;
  try {
    stageIdentity = identity(await operations.lstat(staging, { bigint: true }));
    if (stageIdentity === null) fail("R22_STORE_WRITE_FAILED");
    await assertDirectory(staging, stageIdentity, operations);
    const staged = path.join(staging, path.basename(candidate));
    await writeExclusive(staged, text, operations);
    await assertDirectory(parent, parentIdentity, operations);
    if (immutable) {
      try {
        await operations.rename(staged, candidate);
        moved = true;
      } catch (error) {
        if (error?.code !== "EEXIST" && error?.code !== "EPERM") throw error;
        if (await readStableText(candidate, operations) !== text) fail("R22_STORE_IMMUTABLE_CONFLICT");
      }
    } else {
      await operations.rename(staged, candidate);
      moved = true;
    }
    if (await readStableText(candidate, operations) !== text) fail("R22_STORE_WRITE_FAILED");
    return moved;
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError) throw error;
    fail("R22_STORE_WRITE_FAILED");
  } finally {
    if (guardedCleanup) {
      // The new diagnostic lane never recursively removes an uncertain stage.
      // Partial writes stay fail-closed for inspection, not recursive cleanup.
      if (stageIdentity !== null) {
        await assertDirectory(staging, stageIdentity, operations);
        if ((await operations.readdir(staging)).length === 0) await operations.rmdir(staging);
      }
    } else await operations.rm(staging, { recursive: true, force: true }).catch(() => {});
  }
}

function initialHostBudget(hostRunId) {
  return {
    format: "matrix-oasis.r22-host-budget",
    formatVersion: "0.1.0",
    canonicalization: CANONICALIZATION,
    hostRunId,
    limitMicrousd: NPC_COGNITION_LIMITS.perHostRunMicrousd,
    entries: [],
  };
}

function validateHostBudget(value, hostRunId) {
  if (!exactObject(value, ["format", "formatVersion", "canonicalization", "hostRunId", "limitMicrousd", "entries"]) ||
      value.format !== "matrix-oasis.r22-host-budget" || value.formatVersion !== "0.1.0" ||
      value.canonicalization !== CANONICALIZATION || value.hostRunId !== hostRunId ||
      value.limitMicrousd !== NPC_COGNITION_LIMITS.perHostRunMicrousd || !Array.isArray(value.entries) ||
      value.entries.length > NPC_COGNITION_LIMITS.callsPerHostRun) {
    fail("R22_STORE_HOST_BUDGET_INVALID");
  }
  const seen = new Set();
  let active = 0;
  let charged = 0;
  for (const entry of value.entries) {
    const entryIdentity = `${entry?.authoritySessionSha256 ?? ""}:${entry?.callPlanSha256 ?? ""}`;
    if (!exactObject(entry, ["callPlanSha256", "authoritySessionSha256", "reservedMicrousd", "chargedMicrousd", "state"]) ||
        !SHA256.test(entry.callPlanSha256 ?? "") || !SHA256.test(entry.authoritySessionSha256 ?? "") || seen.has(entryIdentity) ||
        entry.reservedMicrousd !== NPC_COGNITION_LIMITS.perCallMicrousd || !Number.isSafeInteger(entry.chargedMicrousd) ||
        entry.chargedMicrousd < 0 || entry.chargedMicrousd > entry.reservedMicrousd ||
        !["reserved", "charged", "released"].includes(entry.state) ||
        (entry.state === "reserved" && entry.chargedMicrousd !== 0) ||
        (entry.state === "released" && entry.chargedMicrousd !== 0)) fail("R22_STORE_HOST_BUDGET_INVALID");
    seen.add(entryIdentity);
    active += Number(entry.state === "reserved");
    charged += entry.chargedMicrousd;
  }
  if (active > 1 || !Number.isSafeInteger(charged) || charged > value.limitMicrousd) fail("R22_STORE_HOST_BUDGET_INVALID");
  return value;
}

function initialCheckpoint(config) {
  return {
    format: "matrix-oasis.r22-cognition-checkpoint",
    formatVersion: "0.1.0",
    canonicalization: CANONICALIZATION,
    timelineId: config.timelineId,
    authoritySessionSha256: config.authoritySessionSha256,
    cognitionPolicySha256: config.cognitionPolicySha256,
    initialLedger: config.initialLedgerPoint,
    latestSequence: 0,
    turns: 0,
    providerRequests: 0,
    chargedMicrousd: 0,
    actorUsage: [],
    finalized: [],
    active: null,
  };
}

function validateCheckpoint(value, config) {
  const keys = ["format", "formatVersion", "canonicalization", "timelineId", "authoritySessionSha256", "cognitionPolicySha256", "initialLedger", "latestSequence", "turns", "providerRequests", "chargedMicrousd", "actorUsage", "finalized", "active"];
  if (!exactObject(value, keys) || value.format !== "matrix-oasis.r22-cognition-checkpoint" || value.formatVersion !== "0.1.0" ||
      value.canonicalization !== CANONICALIZATION || value.timelineId !== config.timelineId ||
      value.authoritySessionSha256 !== config.authoritySessionSha256 || value.cognitionPolicySha256 !== config.cognitionPolicySha256 ||
      canonicalizeJsonValue(value.initialLedger) !== canonicalizeJsonValue(config.initialLedgerPoint) ||
      !Number.isSafeInteger(value.latestSequence) || value.latestSequence < 0 || value.latestSequence > NPC_COGNITION_LIMITS.turnsPerTimeline ||
      !Number.isSafeInteger(value.turns) || value.turns !== value.latestSequence ||
      !Number.isSafeInteger(value.providerRequests) || value.providerRequests < 0 || value.providerRequests > NPC_COGNITION_LIMITS.callsPerTimeline ||
      !Number.isSafeInteger(value.chargedMicrousd) || value.chargedMicrousd < 0 || value.chargedMicrousd > NPC_COGNITION_LIMITS.perTimelineMicrousd ||
      !Array.isArray(value.actorUsage) || !Array.isArray(value.finalized) || value.finalized.length > value.turns) fail("R22_STORE_CHECKPOINT_INVALID");
  const actors = new Set();
  let actorTurns = 0;
  let actorRequests = 0;
  for (const actor of value.actorUsage) {
    if (!exactObject(actor, ["actorEntityId", "turns", "providerRequests"]) || !IDENTIFIER.test(actor.actorEntityId ?? "") || actors.has(actor.actorEntityId) ||
        !Number.isSafeInteger(actor.turns) || actor.turns < 1 || actor.turns > NPC_COGNITION_LIMITS.turnsPerActor ||
        !Number.isSafeInteger(actor.providerRequests) || actor.providerRequests < 0 || actor.providerRequests > NPC_COGNITION_LIMITS.callsPerActor) fail("R22_STORE_CHECKPOINT_INVALID");
    actors.add(actor.actorEntityId); actorTurns += actor.turns; actorRequests += actor.providerRequests;
  }
  if (actorTurns !== value.turns || actorRequests !== value.providerRequests) fail("R22_STORE_CHECKPOINT_INVALID");
  const finalized = new Set();
  let finalizedRequests = 0;
  let finalizedCost = 0;
  for (const item of value.finalized) {
    if (!exactObject(item, ["sequence", "turnId", "callPlanSha256", "turnReceiptSha256", "requestCount", "actualMicrousd"]) ||
        !Number.isSafeInteger(item.sequence) || item.sequence < 1 || item.sequence > value.latestSequence ||
        !IDENTIFIER.test(item.turnId ?? "") || !SHA256.test(item.callPlanSha256 ?? "") ||
        !SHA256.test(item.turnReceiptSha256 ?? "") || finalized.has(item.callPlanSha256) || finalized.has(item.turnId) || ![0, 1].includes(item.requestCount) ||
        !Number.isSafeInteger(item.actualMicrousd) || item.actualMicrousd < 0 || item.actualMicrousd > NPC_COGNITION_LIMITS.perCallMicrousd) fail("R22_STORE_CHECKPOINT_INVALID");
    finalized.add(item.callPlanSha256); finalized.add(item.turnId); finalizedRequests += item.requestCount; finalizedCost += item.actualMicrousd;
  }
  if (finalizedRequests !== value.providerRequests || finalizedCost !== value.chargedMicrousd) fail("R22_STORE_CHECKPOINT_INVALID");
  if (value.active !== null) {
    const activeKeys = ["sequence", "turnId", "actorEntityId", "turnSha256", "callPlanSha256", "stage", "beforeLedger"];
    if (!exactObject(value.active, activeKeys) || value.active.sequence !== value.latestSequence ||
        !IDENTIFIER.test(value.active.turnId ?? "") || !IDENTIFIER.test(value.active.actorEntityId ?? "") ||
        !SHA256.test(value.active.turnSha256 ?? "") || !SHA256.test(value.active.callPlanSha256 ?? "") ||
        !["planned", "reserved", "dispatching", "validated", "queued_for_r20"].includes(value.active.stage)) fail("R22_STORE_CHECKPOINT_INVALID");
    validateLedgerPoint(value.active.beforeLedger, "R22_STORE_CHECKPOINT_INVALID");
  }
  return value;
}

function parseCanonicalInternal(text, validator, code) {
  let value;
  try {
    value = JSON.parse(text);
    if (canonicalizeJsonValue(value) !== text) fail(code);
    validator(value);
    return value;
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError) throw error;
    fail(code);
  }
}

async function readOrCreateJson(candidate, initialValue, validate, operations, code) {
  try {
    return parseCanonicalInternal(await readStableText(candidate, operations), validate, code);
  } catch (error) {
    if (error instanceof R22CallStoreOperationalError || error?.code !== "ENOENT") throw error;
    const text = canonicalizeJsonValue(initialValue);
    await writeAtomic(candidate, text, operations, { immutable: true });
    return parseCanonicalInternal(await readStableText(candidate, operations), validate, code);
  }
}

function totalsForBudget(budget) {
  return budget.entries.reduce((totals, entry) => {
    totals.charged += entry.chargedMicrousd;
    if (entry.state === "reserved") totals.reserved += entry.reservedMicrousd;
    return totals;
  }, { charged: 0, reserved: 0 });
}

async function persistBudget(state) {
  validateHostBudget(state.budget, state.hostRunId);
  await writeAtomic(state.hostBudgetPath, canonicalizeJsonValue(state.budget), state.operations, { guardedCleanup: state.diagnostic === true });
}

async function persistCheckpoint(state) {
  validateCheckpoint(state.checkpoint, state.config);
  await writeAtomic(state.checkpointPath, canonicalizeJsonValue(state.checkpoint), state.operations);
}

function budgetEntry(state, callPlanSha256) {
  return state.budget.entries.find((entry) => entry.authoritySessionSha256 === state.config.authoritySessionSha256 &&
    entry.callPlanSha256 === callPlanSha256) ?? null;
}

function replaceBudgetEntry(state, next) {
  const entries = state.budget.entries.filter((entry) => entry.callPlanSha256 !== next.callPlanSha256 ||
    entry.authoritySessionSha256 !== next.authoritySessionSha256);
  entries.push(next);
  entries.sort((left, right) => left.authoritySessionSha256.localeCompare(right.authoritySessionSha256) ||
    left.callPlanSha256.localeCompare(right.callPlanSha256));
  state.budget = { ...state.budget, entries };
}

// Both lanes reserve against the same account and writer lease. Keep the
// historical five-field entry and deterministic ordering unchanged.
async function reserveHostBudget(state, authoritySessionSha256, callPlanSha256) {
  const totals = totalsForBudget(state.budget);
  if (state.budget.entries.length >= NPC_COGNITION_LIMITS.callsPerHostRun) {
    return Object.freeze({ ok: false, diagnosticCode: "R22_BUDGET_EXHAUSTED" });
  }
  if (totals.reserved > 0) return Object.freeze({ ok: false, diagnosticCode: "R22_CALL_IN_FLIGHT" });
  if (totals.charged + NPC_COGNITION_LIMITS.perCallMicrousd > state.budget.limitMicrousd) {
    return Object.freeze({ ok: false, diagnosticCode: "R22_BUDGET_EXHAUSTED" });
  }
  if (state.budget.entries.some((entry) => entry.authoritySessionSha256 === authoritySessionSha256 && entry.callPlanSha256 === callPlanSha256)) {
    fail("R22_STORE_BUDGET_ENTRY_CONFLICT");
  }
  replaceBudgetEntry(state, { callPlanSha256, authoritySessionSha256,
    reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd, chargedMicrousd: 0, state: "reserved" });
  await persistBudget(state);
  return Object.freeze({ ok: true, reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd });
}

function actorUsage(checkpoint, actorEntityId) {
  return checkpoint.actorUsage.find((actor) => actor.actorEntityId === actorEntityId) ?? null;
}

async function assertStoreHierarchy(state) {
  for (const item of state.hierarchy) {
    await assertDirectory(item.path, item.identity, state.operations);
  }
  if (state.activeTurnIdentity !== null) {
    if (typeof state.activeTurnPath !== "string") fail("R22_STORE_PATH_IDENTITY_INVALID");
    await assertDirectory(state.activeTurnPath, state.activeTurnIdentity, state.operations);
  }
}

function turnDirectory(state, active = state.checkpoint.active) {
  if (!active) fail("R22_STORE_NO_ACTIVE_TURN");
  return path.join(state.turnsRoot, `${String(active.sequence).padStart(6, "0")}-${active.callPlanSha256.slice(7)}`);
}

function activeFor(state, callPlanSha256) {
  const active = state.checkpoint.active;
  if (!active || active.callPlanSha256 !== callPlanSha256) fail("R22_STORE_ACTIVE_TURN_MISMATCH");
  return active;
}

function runExclusive(state, operation) {
  const guarded = async () => {
    await assertStoreHierarchy(state);
    const result = await operation();
    await assertStoreHierarchy(state);
    return result;
  };
  const result = state.queue.then(guarded, guarded);
  state.queue = result.catch(() => {});
  return result;
}

function storeState(store) {
  const state = storeStates.get(store);
  if (!state || state.closed) fail("R22_STORE_HANDLE_INVALID");
  return state;
}

async function acquireWriterLease(root, operations, processEpochSha256) {
  const leasePath = path.join(path.dirname(root), `.${path.basename(root)}.r22-writer-lock`);
  const record = canonicalizeJsonValue({
    format: "matrix-oasis.r22-writer-lock",
    formatVersion: "0.1.0",
    processId: operations.processId,
    processEpochSha256,
    rootSha256: sha256Text(path.resolve(root).toLowerCase()),
  });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await writeExclusive(leasePath, record, operations);
      return { leasePath, record };
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      let previousText;
      let previous;
      try {
        previousText = await readStableText(leasePath, operations, 64 * 1024);
        previous = JSON.parse(previousText);
      } catch {
        fail("R22_STORE_WRITER_LOCK_INVALID");
      }
      if (!exactObject(previous, ["format", "formatVersion", "processId", "processEpochSha256", "rootSha256"]) ||
          previous.format !== "matrix-oasis.r22-writer-lock" || previous.formatVersion !== "0.1.0" ||
          !Number.isSafeInteger(previous.processId) || previous.processId < 1 || !SHA256.test(previous.processEpochSha256 ?? "") ||
          previous.rootSha256 !== sha256Text(path.resolve(root).toLowerCase())) fail("R22_STORE_WRITER_LOCK_INVALID");
      if (await operations.isProcessAlive(previous.processId) !== false) fail("R22_CALL_IN_FLIGHT");
      const quarantine = `${leasePath}.stale-${processEpochSha256.slice(7, 23)}`;
      try {
        await operations.rename(leasePath, quarantine);
        if (await readStableText(quarantine, operations, 64 * 1024) !== previousText) fail("R22_STORE_WRITER_LOCK_INVALID");
      } catch (quarantineError) {
        if (quarantineError?.code === "ENOENT") continue;
        throw quarantineError;
      } finally {
        await operations.rm(quarantine, { force: true }).catch(() => {});
      }
    }
  }
  fail("R22_CALL_IN_FLIGHT");
}

function captureOperations(overrides) {
  const value = { ...defaultOperations, ...(overrides ?? {}) };
  for (const name of ["lstat", "mkdir", "mkdtemp", "openFile", "readdir", "realpath", "rename", "rmdir", "rm", "randomBytes", "isProcessAlive"]) {
    if (typeof value[name] !== "function") fail("R22_STORE_OPERATIONS_INVALID");
  }
  if (!Number.isSafeInteger(value.processId) || value.processId < 1) fail("R22_STORE_OPERATIONS_INVALID");
  return Object.freeze(value);
}

export async function openR22CallStore(config, operationsOverride) {
  const operations = captureOperations(operationsOverride);
  if (!exactObject(config, ["temporaryRoot", "cognitionRunRoot", "hostRunId", "timelineId", "authoritySessionSha256", "cognitionPolicySha256", "initialLedgerPoint"])) {
    fail("R22_STORE_CONFIGURATION_INVALID");
  }
  const temporaryRoot = path.resolve(config.temporaryRoot);
  const cognitionRunRoot = path.resolve(config.cognitionRunRoot);
  validateIdentifier(config.hostRunId, "R22_STORE_CONFIGURATION_INVALID");
  validateIdentifier(config.timelineId, "R22_STORE_CONFIGURATION_INVALID");
  if (!SHA256.test(config.authoritySessionSha256 ?? "") || !SHA256.test(config.cognitionPolicySha256 ?? "") ||
      !isDirectChild(temporaryRoot, cognitionRunRoot) || !cognitionRunRoot.endsWith("-cognition")) fail("R22_STORE_CONFIGURATION_INVALID");
  const initialLedgerPoint = validateLedgerPoint(config.initialLedgerPoint, "R22_STORE_CONFIGURATION_INVALID");
  const tempStat = await operations.lstat(temporaryRoot, { bigint: true }).catch(() => null);
  const tempIdentity = identity(tempStat);
  if (!isPlainDirectory(tempStat) || tempIdentity === null || !samePath(await operations.realpath(temporaryRoot), temporaryRoot)) {
    fail("R22_STORE_CONFIGURATION_INVALID");
  }
  await ensureDirectory(cognitionRunRoot, operations);
  await assertDirectory(temporaryRoot, tempIdentity, operations);
  const processEpochBytes = operations.randomBytes(32);
  if (!(processEpochBytes instanceof Uint8Array) || processEpochBytes.byteLength !== 32) fail("R22_STORE_OPERATIONS_INVALID");
  const processEpochSha256 = sha256Text(Buffer.from(processEpochBytes));
  const writer = await acquireWriterLease(cognitionRunRoot, operations, processEpochSha256);
  try {
    const cognitionRunIdentity = await ensureDirectory(cognitionRunRoot, operations);
    const timelinesRoot = path.join(cognitionRunRoot, "timelines");
    const timelinesIdentity = await ensureDirectory(timelinesRoot, operations);
    const timelineRoot = path.join(timelinesRoot, config.authoritySessionSha256.slice(7));
    const timelineIdentity = await ensureDirectory(timelineRoot, operations);
    const turnsRoot = path.join(timelineRoot, "turns");
    const turnsIdentity = await ensureDirectory(turnsRoot, operations);
    const normalizedConfig = Object.freeze({ ...config, temporaryRoot, cognitionRunRoot, initialLedgerPoint });
    const hostBudgetPath = path.join(cognitionRunRoot, "host-budget.json");
    const checkpointPath = path.join(timelineRoot, "cognition-checkpoint.json");
    const budget = await readOrCreateJson(hostBudgetPath, initialHostBudget(config.hostRunId), (value) => validateHostBudget(value, config.hostRunId), operations, "R22_STORE_HOST_BUDGET_INVALID");
    const checkpoint = await readOrCreateJson(checkpointPath, initialCheckpoint(normalizedConfig), (value) => validateCheckpoint(value, normalizedConfig), operations, "R22_STORE_CHECKPOINT_INVALID");
    let activeTurnIdentity = null;
    let activeTurnPath = null;
    if (checkpoint.active !== null) {
      const activeTurn = path.join(turnsRoot, `${String(checkpoint.active.sequence).padStart(6, "0")}-${checkpoint.active.callPlanSha256.slice(7)}`);
      const activeTurnStat = await operations.lstat(activeTurn, { bigint: true }).catch(() => null);
      activeTurnIdentity = identity(activeTurnStat);
      if (!isPlainDirectory(activeTurnStat) || activeTurnIdentity === null || !samePath(await operations.realpath(activeTurn), activeTurn)) {
        fail("R22_STORE_PATH_IDENTITY_INVALID");
      }
      activeTurnPath = activeTurn;
    }
    const handle = Object.freeze(Object.create(null));
    storeStates.set(handle, {
      operations,
      config: normalizedConfig,
      hostRunId: config.hostRunId,
      cognitionRunRoot,
      timelineRoot,
      turnsRoot,
      hostBudgetPath,
      checkpointPath,
      budget,
      checkpoint,
      processEpochSha256,
      writer,
      queue: Promise.resolve(),
      closed: false,
      hierarchy: Object.freeze([
        Object.freeze({ path: temporaryRoot, identity: tempIdentity }),
        Object.freeze({ path: cognitionRunRoot, identity: cognitionRunIdentity }),
        Object.freeze({ path: timelinesRoot, identity: timelinesIdentity }),
        Object.freeze({ path: timelineRoot, identity: timelineIdentity }),
        Object.freeze({ path: turnsRoot, identity: turnsIdentity }),
      ]),
      activeTurnIdentity,
      activeTurnPath,
    });
    return handle;
  } catch (error) {
    if (await readStableText(writer.leasePath, operations, 64 * 1024).catch(() => null) === writer.record) {
      await operations.rm(writer.leasePath, { force: false }).catch(() => {});
    }
    throw error;
  }
}

export function getR22CallStoreIdentity(store) {
  const state = storeState(store);
  return Object.freeze({
    hostRunId: state.hostRunId,
    timelineId: state.config.timelineId,
    authoritySessionSha256: state.config.authoritySessionSha256,
    cognitionPolicySha256: state.config.cognitionPolicySha256,
    processEpochSha256: state.processEpochSha256,
  });
}

export function inspectR22CallStore(store) {
  const state = storeState(store);
  const totals = totalsForBudget(state.budget);
  return Object.freeze({
    checkpoint: structuredClone(state.checkpoint),
    hostBudget: Object.freeze({ chargedMicrousd: totals.charged, reservedMicrousd: totals.reserved, limitMicrousd: state.budget.limitMicrousd }),
  });
}

export async function recordR22PlannedCall(store, input) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    if (!exactObject(input, ["sequence", "turnId", "actorEntityId", "callPlanJson", "beforeLedgerPoint"])) fail("R22_STORE_PLAN_INVALID");
    if (state.checkpoint.active !== null) fail("R22_CALL_IN_FLIGHT");
    const sequence = input.sequence;
    const turnId = validateIdentifier(input.turnId, "R22_STORE_PLAN_INVALID");
    const actorEntityId = validateIdentifier(input.actorEntityId, "R22_STORE_PLAN_INVALID");
    if (!Number.isSafeInteger(sequence) || sequence !== state.checkpoint.latestSequence + 1 || sequence > NPC_COGNITION_LIMITS.turnsPerTimeline) {
      fail("R22_STORE_TURN_SEQUENCE_INVALID");
    }
    const currentActor = actorUsage(state.checkpoint, actorEntityId);
    if ((currentActor?.turns ?? 0) >= NPC_COGNITION_LIMITS.turnsPerActor) fail("R22_STORE_ACTOR_TURN_LIMIT");
    const beforeLedger = validateLedgerPoint(input.beforeLedgerPoint, "R22_STORE_PLAN_INVALID");
    const plan = canonicalDocument(input.callPlanJson, validateNpcCognitionCallPlanJson, "R22_STORE_CALL_PLAN_INVALID");
    if (plan.turnId !== turnId) fail("R22_STORE_PLAN_INVALID");
    const callPlanSha256 = sha256Text(input.callPlanJson);
    const active = {
      sequence,
      turnId,
      actorEntityId,
      turnSha256: plan.turnSha256,
      callPlanSha256,
      stage: "planned",
      beforeLedger,
    };
    const directory = path.join(state.turnsRoot, `${String(sequence).padStart(6, "0")}-${callPlanSha256.slice(7)}`);
    state.activeTurnIdentity = await ensureDirectory(directory, state.operations);
    state.activeTurnPath = directory;
    const names = await state.operations.readdir(directory);
    if (names.length !== 0) fail("R22_STORE_TURN_DIRECTORY_INVALID");
    await writeAtomic(path.join(directory, "call-plan.json"), input.callPlanJson, state.operations, { immutable: true });
    const nextActors = state.checkpoint.actorUsage.filter((actor) => actor.actorEntityId !== actorEntityId);
    nextActors.push({ actorEntityId, turns: (currentActor?.turns ?? 0) + 1, providerRequests: currentActor?.providerRequests ?? 0 });
    nextActors.sort((left, right) => left.actorEntityId.localeCompare(right.actorEntityId));
    state.checkpoint = { ...state.checkpoint, latestSequence: sequence, turns: state.checkpoint.turns + 1, actorUsage: nextActors, active };
    await persistCheckpoint(state);
    return Object.freeze({ ok: true, callPlanSha256, turnSha256: plan.turnSha256, providerPayloadSha256: plan.providerPayloadSha256, approvalContentSha256: plan.approval.hash, directory });
  });
}

export async function reserveR22CallBudget(store, callPlanSha256) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    const active = activeFor(state, callPlanSha256);
    if (active.stage !== "planned") fail("R22_STORE_STAGE_INVALID");
    if (state.checkpoint.providerRequests >= NPC_COGNITION_LIMITS.callsPerTimeline ||
        (actorUsage(state.checkpoint, active.actorEntityId)?.providerRequests ?? 0) >= NPC_COGNITION_LIMITS.callsPerActor ||
        state.checkpoint.chargedMicrousd + NPC_COGNITION_LIMITS.perCallMicrousd > NPC_COGNITION_LIMITS.perTimelineMicrousd) {
      return Object.freeze({ ok: false, diagnosticCode: "R22_BUDGET_EXHAUSTED" });
    }
    const reservation = await reserveHostBudget(state, state.config.authoritySessionSha256, callPlanSha256);
    if (!reservation.ok) return reservation;
    state.checkpoint = { ...state.checkpoint, active: { ...active, stage: "reserved" } };
    await persistCheckpoint(state);
    return Object.freeze({ ok: true, reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd });
  });
}

export async function markR22CallDispatching(store, input) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    if (!exactObject(input, ["callPlanSha256", "approvalTokenSha256", "approvalContentSha256"]) ||
        ![input.callPlanSha256, input.approvalTokenSha256, input.approvalContentSha256].every((value) => SHA256.test(value ?? ""))) {
      fail("R22_STORE_DISPATCH_INVALID");
    }
    const active = activeFor(state, input.callPlanSha256);
    if (active.stage !== "reserved" || budgetEntry(state, input.callPlanSha256)?.state !== "reserved") fail("R22_STORE_STAGE_INVALID");
    const callPlanJson = await readStableText(path.join(turnDirectory(state, active), "call-plan.json"), state.operations);
    const plan = canonicalDocument(callPlanJson, validateNpcCognitionCallPlanJson, "R22_STORE_CALL_PLAN_INVALID");
    if (plan.approval.hash !== input.approvalContentSha256) fail("R22_APPROVAL_MISMATCH");
    const dispatchRecord = canonicalizeJsonValue({
      format: "matrix-oasis.r22-dispatch-record",
      formatVersion: "0.1.0",
      canonicalization: CANONICALIZATION,
      callPlanSha256: input.callPlanSha256,
      turnSha256: active.turnSha256,
      approvalTokenSha256: input.approvalTokenSha256,
      approvalContentSha256: input.approvalContentSha256,
      reservationMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
      providerRequestLimit: 1,
      providerRetryLimit: 0,
    });
    await writeAtomic(path.join(turnDirectory(state, active), "dispatch-record.json"), dispatchRecord, state.operations, { immutable: true });
    state.checkpoint = { ...state.checkpoint, active: { ...active, stage: "dispatching" } };
    await persistCheckpoint(state);
    return Object.freeze({ ok: true });
  });
}

function validateUsage(usage) {
  const keys = ["inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens", "totalTokens"];
  if (!exactObject(usage, keys) || keys.some((key) => !Number.isSafeInteger(usage[key]) || usage[key] < 0) ||
      usage.inputTokens + usage.outputTokens !== usage.totalTokens ||
      usage.cachedInputTokens + usage.cacheWriteInputTokens > usage.inputTokens) fail("R22_STORE_VALIDATION_RECORD_INVALID");
  return structuredClone(usage);
}

export async function recordR22ValidatedProposal(store, input) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    const keys = ["callPlanSha256", "proposalSha256", "returnedModel", "usage", "actualMicrousd", "actionChoiceId", "mappedIntentId", "mappedIntentSha256"];
    if (!exactObject(input, keys) || !SHA256.test(input.callPlanSha256 ?? "") || !SHA256.test(input.proposalSha256 ?? "") ||
        typeof input.returnedModel !== "string" || input.returnedModel.length < 1 || input.returnedModel.length > 128 ||
        !Number.isSafeInteger(input.actualMicrousd) || input.actualMicrousd < 0 || input.actualMicrousd > NPC_COGNITION_LIMITS.perCallMicrousd ||
        (input.actionChoiceId === null
          ? input.mappedIntentId !== null || input.mappedIntentSha256 !== null
          : !/^choice-[0-9a-f]{64}$/u.test(input.actionChoiceId) ||
            ((input.mappedIntentId === null) !== (input.mappedIntentSha256 === null)) ||
            (input.mappedIntentId !== null && (!IDENTIFIER.test(input.mappedIntentId) || !SHA256.test(input.mappedIntentSha256 ?? ""))))) {
      fail("R22_STORE_VALIDATION_RECORD_INVALID");
    }
    const active = activeFor(state, input.callPlanSha256);
    if (active.stage !== "dispatching") fail("R22_STORE_STAGE_INVALID");
    const usage = validateUsage(input.usage);
    const record = canonicalizeJsonValue({
      format: "matrix-oasis.r22-validated-proposal-record",
      formatVersion: "0.1.0",
      canonicalization: CANONICALIZATION,
      callPlanSha256: input.callPlanSha256,
      proposalSha256: input.proposalSha256,
      returnedModel: input.returnedModel,
      usage,
      actualMicrousd: input.actualMicrousd,
      actionChoiceId: input.actionChoiceId,
      mappedIntentId: input.mappedIntentId,
      mappedIntentSha256: input.mappedIntentSha256,
    });
    await writeAtomic(path.join(turnDirectory(state, active), "validated-proposal-record.json"), record, state.operations, { immutable: true });
    replaceBudgetEntry(state, {
      ...budgetEntry(state, input.callPlanSha256),
      chargedMicrousd: input.actualMicrousd,
      state: "charged",
    });
    await persistBudget(state);
    state.checkpoint = { ...state.checkpoint, active: { ...active, stage: input.mappedIntentSha256 === null ? "validated" : "queued_for_r20" } };
    await persistCheckpoint(state);
    return Object.freeze({ ok: true });
  });
}

export async function recordR22DisplayAcknowledged(store, input) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    const keys = ["callPlanSha256", "proposalSha256", "displayAckSha256"];
    if (!exactObject(input, keys) || keys.some((key) => !SHA256.test(input[key] ?? ""))) {
      fail("R22_STORE_DISPLAY_ACK_INVALID");
    }
    const active = activeFor(state, input.callPlanSha256);
    if (!["validated", "queued_for_r20"].includes(active.stage)) fail("R22_STORE_STAGE_INVALID");
    const validatedText = await readStableText(path.join(turnDirectory(state, active), "validated-proposal-record.json"), state.operations);
    const dispatchText = await readStableText(path.join(turnDirectory(state, active), "dispatch-record.json"), state.operations);
    let validated;
    let dispatch;
    try {
      validated = JSON.parse(validatedText);
      dispatch = JSON.parse(dispatchText);
    } catch {
      fail("R22_STORE_VALIDATION_RECORD_INVALID");
    }
    if (validated.callPlanSha256 !== input.callPlanSha256 || validated.proposalSha256 !== input.proposalSha256 ||
        (validated.actionChoiceId === null
          ? validated.mappedIntentId !== null || validated.mappedIntentSha256 !== null || active.stage !== "validated"
          : !SHA256.test(validated.mappedIntentSha256 ?? "") || active.stage !== "queued_for_r20") ||
        dispatch.callPlanSha256 !== input.callPlanSha256 || dispatch.turnSha256 !== active.turnSha256 ||
        !SHA256.test(dispatch.approvalTokenSha256 ?? "") || input.displayAckSha256 !== computeR22DisplayAckHash({
          approvalTokenSha256: dispatch.approvalTokenSha256,
          turnSha256: active.turnSha256,
          callPlanSha256: input.callPlanSha256,
          proposalSha256: input.proposalSha256,
          actionChoiceId: validated.actionChoiceId,
        })) {
      fail("R22_STORE_DISPLAY_ACK_INVALID");
    }
    const record = canonicalizeJsonValue({
      format: "matrix-oasis.r22-display-ack-record",
      formatVersion: "0.1.0",
      canonicalization: CANONICALIZATION,
      callPlanSha256: input.callPlanSha256,
      proposalSha256: input.proposalSha256,
      displayAckSha256: input.displayAckSha256,
      state: "displayed",
    });
    await writeAtomic(path.join(turnDirectory(state, active), "display-ack-record.json"), record, state.operations, { immutable: true });
    return Object.freeze({ ok: true, displayAckRecordSha256: sha256Text(record) });
  });
}

function reconcileBudgetForReceipt(state, receipt) {
  const existing = budgetEntry(state, receipt.callPlanSha256);
  const targetState = receipt.requestCount === 1 ? "charged" : "released";
  const targetCharge = receipt.requestCount === 1 ? receipt.budget.actualMicrousd : 0;
  if (existing === null) {
    if (receipt.budget.reservedMicrousd === 0) return;
    replaceBudgetEntry(state, {
      callPlanSha256: receipt.callPlanSha256,
      authoritySessionSha256: state.config.authoritySessionSha256,
      reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
      chargedMicrousd: targetCharge,
      state: targetState,
    });
    return;
  }
  const mayUpgradeValidatedCharge = existing.state === "charged" && targetState === "charged" &&
    targetCharge >= existing.chargedMicrousd && targetCharge <= existing.reservedMicrousd;
  if (existing.authoritySessionSha256 !== state.config.authoritySessionSha256 ||
      (existing.state !== "reserved" && !mayUpgradeValidatedCharge &&
        (existing.state !== targetState || existing.chargedMicrousd !== targetCharge))) {
    fail("R22_STORE_BUDGET_ENTRY_CONFLICT");
  }
  replaceBudgetEntry(state, { ...existing, chargedMicrousd: targetCharge, state: targetState });
}

async function verifyReceiptEvidenceClosure(state, directory, callPlan, receipt, names, active = null) {
  const hasStage = (stage) => receipt.statusHistory.includes(stage);
  const dispatched = hasStage("dispatching");
  const validated = hasStage("validated");
  const displayed = hasStage("dialogue_only") || hasStage("queued_for_r20");
  for (const [name, required] of [
    ["dispatch-record.json", dispatched],
    ["validated-proposal-record.json", validated],
    ["display-ack-record.json", displayed],
  ]) {
    if (names.includes(name) !== required) fail("R22_STORE_FINALIZED_EVIDENCE_INCOMPLETE");
  }
  if (active !== null) {
    const expectedStage = !validated && dispatched
        ? "dispatching"
        : !validated && hasStage("reserved")
          ? "reserved"
          : !validated
            ? "planned"
            : null;
    if (expectedStage !== null && active.stage !== expectedStage) fail("R22_STORE_FINALIZED_EVIDENCE_INCOMPLETE");
  }
  let dispatch = null;
  if (dispatched) {
    const text = await readStableText(path.join(directory, "dispatch-record.json"), state.operations);
    try { dispatch = JSON.parse(text); } catch { fail("R22_STORE_FINALIZED_EVIDENCE_INVALID"); }
    const keys = ["format", "formatVersion", "canonicalization", "callPlanSha256", "turnSha256", "approvalTokenSha256", "approvalContentSha256", "reservationMicrousd", "providerRequestLimit", "providerRetryLimit"];
    if (canonicalizeJsonValue(dispatch) !== text || !exactObject(dispatch, keys) ||
        dispatch.format !== "matrix-oasis.r22-dispatch-record" || dispatch.formatVersion !== "0.1.0" ||
        dispatch.canonicalization !== CANONICALIZATION || dispatch.callPlanSha256 !== receipt.callPlanSha256 ||
        dispatch.turnSha256 !== callPlan.turnSha256 || !SHA256.test(dispatch.approvalTokenSha256 ?? "") ||
        dispatch.approvalContentSha256 !== callPlan.approval.hash ||
        dispatch.reservationMicrousd !== NPC_COGNITION_LIMITS.perCallMicrousd ||
        dispatch.providerRequestLimit !== 1 || dispatch.providerRetryLimit !== 0) {
      fail("R22_STORE_FINALIZED_EVIDENCE_INVALID");
    }
  }
  let proposal = null;
  if (validated) {
    const text = await readStableText(path.join(directory, "validated-proposal-record.json"), state.operations);
    try { proposal = JSON.parse(text); } catch { fail("R22_STORE_FINALIZED_EVIDENCE_INVALID"); }
    const keys = ["format", "formatVersion", "canonicalization", "callPlanSha256", "proposalSha256", "returnedModel", "usage", "actualMicrousd", "actionChoiceId", "mappedIntentId", "mappedIntentSha256"];
    if (canonicalizeJsonValue(proposal) !== text || !exactObject(proposal, keys) ||
        proposal.format !== "matrix-oasis.r22-validated-proposal-record" || proposal.formatVersion !== "0.1.0" ||
        proposal.canonicalization !== CANONICALIZATION || proposal.callPlanSha256 !== receipt.callPlanSha256 ||
        !SHA256.test(proposal.proposalSha256 ?? "") || typeof proposal.returnedModel !== "string" ||
        !Number.isSafeInteger(proposal.actualMicrousd) || proposal.actualMicrousd < 0 ||
        proposal.actualMicrousd > NPC_COGNITION_LIMITS.perCallMicrousd) fail("R22_STORE_FINALIZED_EVIDENCE_INVALID");
    const usage = validateUsage(proposal.usage);
    const receiptCarriesMappedIntent = displayed;
    if (proposal.proposalSha256 !== receipt.proposalSha256 || proposal.returnedModel !== receipt.returnedModel ||
        canonicalizeJsonValue(usage) !== canonicalizeJsonValue(receipt.usage) ||
        proposal.actualMicrousd !== receipt.budget.actualMicrousd || proposal.actionChoiceId !== receipt.actionChoiceId ||
        (receiptCarriesMappedIntent ? proposal.mappedIntentSha256 !== receipt.mappedIntentSha256 : receipt.mappedIntentSha256 !== null) ||
        (proposal.actionChoiceId === null
          ? proposal.mappedIntentId !== null || proposal.mappedIntentSha256 !== null
          : !/^choice-[0-9a-f]{64}$/u.test(proposal.actionChoiceId) ||
            ((proposal.mappedIntentId === null) !== (proposal.mappedIntentSha256 === null)) ||
            (proposal.mappedIntentId !== null && (!IDENTIFIER.test(proposal.mappedIntentId) || !SHA256.test(proposal.mappedIntentSha256 ?? "") ||
              proposal.actionChoiceId !== `choice-${proposal.mappedIntentSha256.slice(7)}`)))) {
      fail("R22_STORE_FINALIZED_EVIDENCE_INVALID");
    }
    if (active !== null && active.stage !== (proposal.mappedIntentSha256 === null ? "validated" : "queued_for_r20")) {
      fail("R22_STORE_FINALIZED_EVIDENCE_INCOMPLETE");
    }
  }
  if (displayed) {
    const text = await readStableText(path.join(directory, "display-ack-record.json"), state.operations);
    let displayAck;
    try { displayAck = JSON.parse(text); } catch { fail("R22_STORE_FINALIZED_EVIDENCE_INVALID"); }
    const keys = ["format", "formatVersion", "canonicalization", "callPlanSha256", "proposalSha256", "displayAckSha256", "state"];
    if (!dispatch || !proposal || canonicalizeJsonValue(displayAck) !== text || !exactObject(displayAck, keys) ||
        displayAck.format !== "matrix-oasis.r22-display-ack-record" || displayAck.formatVersion !== "0.1.0" ||
        displayAck.canonicalization !== CANONICALIZATION || displayAck.state !== "displayed" ||
        displayAck.callPlanSha256 !== receipt.callPlanSha256 || displayAck.proposalSha256 !== proposal.proposalSha256 ||
        displayAck.displayAckSha256 !== computeR22DisplayAckHash({
          approvalTokenSha256: dispatch.approvalTokenSha256,
          turnSha256: callPlan.turnSha256,
          callPlanSha256: receipt.callPlanSha256,
          proposalSha256: proposal.proposalSha256,
          actionChoiceId: proposal.actionChoiceId,
        })) fail("R22_STORE_FINALIZED_EVIDENCE_INVALID");
  }
}

export async function publishR22TurnReceipt(store, turnReceiptJson) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    const receipt = canonicalDocument(turnReceiptJson, validateNpcCognitionTurnReceiptJson, "R22_STORE_TURN_RECEIPT_INVALID");
    const active = activeFor(state, receipt.callPlanSha256);
    if (receipt.turnSha256 !== active.turnSha256 || canonicalizeJsonValue(receipt.ledger.before) !== canonicalizeJsonValue(active.beforeLedger)) {
      fail("R22_STORE_TURN_RECEIPT_IDENTITY_MISMATCH");
    }
    const directory = turnDirectory(state, active);
    const publication = await inspectReceiptPublication(directory, state.operations);
    const names = publication.evidenceNames;
    if (!names.includes("call-plan.json")) fail("R22_STORE_TURN_DIRECTORY_INVALID");
    const callPlanJson = await readStableText(path.join(directory, "call-plan.json"), state.operations);
    const callPlan = canonicalDocument(callPlanJson, validateNpcCognitionCallPlanJson, "R22_STORE_CALL_PLAN_INVALID");
    if (sha256Text(callPlanJson) !== active.callPlanSha256 || callPlan.turnId !== active.turnId || callPlan.turnSha256 !== active.turnSha256) {
      fail("R22_STORE_TURN_RECEIPT_IDENTITY_MISMATCH");
    }
    await verifyReceiptEvidenceClosure(state, directory, callPlan, receipt, names, active);
    const target = path.join(directory, "turn-receipt.json");
    if (publication.targetJson !== null && publication.targetJson !== turnReceiptJson ||
        publication.stagedJson !== null && publication.stagedJson !== turnReceiptJson) fail("R22_STORE_IMMUTABLE_CONFLICT");
    if (publication.stagingPath !== null) {
      if (publication.stagedJson !== null && publication.targetJson === null) {
        await state.operations.rename(path.join(publication.stagingPath, "turn-receipt.json"), target);
        if (await readStableText(target, state.operations) !== turnReceiptJson) fail("R22_STORE_WRITE_FAILED");
      }
      if ((await state.operations.readdir(publication.stagingPath)).length !== 0) fail("R22_STORE_TURN_DIRECTORY_INVALID");
      await state.operations.rmdir(publication.stagingPath);
    } else if (publication.targetJson === null) {
      await writeAtomic(target, turnReceiptJson, state.operations, { immutable: true });
    }
    reconcileBudgetForReceipt(state, receipt);
    await persistBudget(state);
    const actor = actorUsage(state.checkpoint, active.actorEntityId);
    const nextActors = state.checkpoint.actorUsage.filter((item) => item.actorEntityId !== active.actorEntityId);
    nextActors.push({ ...actor, providerRequests: actor.providerRequests + receipt.requestCount });
    nextActors.sort((left, right) => left.actorEntityId.localeCompare(right.actorEntityId));
    const finalized = [...state.checkpoint.finalized, {
      sequence: active.sequence,
      turnId: active.turnId,
      callPlanSha256: active.callPlanSha256,
      turnReceiptSha256: sha256Text(turnReceiptJson),
      requestCount: receipt.requestCount,
      actualMicrousd: receipt.budget.actualMicrousd,
    }];
    state.checkpoint = {
      ...state.checkpoint,
      providerRequests: state.checkpoint.providerRequests + receipt.requestCount,
      chargedMicrousd: state.checkpoint.chargedMicrousd + receipt.budget.actualMicrousd,
      actorUsage: nextActors,
      finalized,
      active: null,
    };
    await persistCheckpoint(state);
    return Object.freeze({ ok: true, turnReceiptSha256: sha256Text(turnReceiptJson) });
  });
}

async function inspectReceiptPublication(directory, operations) {
  const names = (await operations.readdir(directory)).sort();
  const ordinary = new Set(["call-plan.json", "dispatch-record.json", "validated-proposal-record.json", "display-ack-record.json", "turn-receipt.json"]);
  const stages = names.filter((name) => /^\.s-[A-Za-z0-9]{6}$/u.test(name));
  if (stages.length > 1 || names.some((name) => !ordinary.has(name) && !stages.includes(name))) fail("R22_STORE_TURN_DIRECTORY_INVALID");
  const targetJson = names.includes("turn-receipt.json") ? await readStableText(path.join(directory, "turn-receipt.json"), operations) : null;
  if (targetJson !== null) canonicalDocument(targetJson, validateNpcCognitionTurnReceiptJson, "R22_STORE_TURN_RECEIPT_INVALID");
  let stagingPath = null, stagedJson = null;
  if (stages.length === 1) {
    stagingPath = path.join(directory, stages[0]);
    const stageStat = await operations.lstat(stagingPath, { bigint: true }), stageIdentity = identity(stageStat);
    if (!isPlainDirectory(stageStat) || stageIdentity === null || !samePath(await operations.realpath(stagingPath), stagingPath)) fail("R22_STORE_PATH_IDENTITY_INVALID");
    const stagedNames = (await operations.readdir(stagingPath)).sort();
    if (stagedNames.length === 1 && stagedNames[0] === "turn-receipt.json") {
      stagedJson = await readStableText(path.join(stagingPath, "turn-receipt.json"), operations);
      canonicalDocument(stagedJson, validateNpcCognitionTurnReceiptJson, "R22_STORE_TURN_RECEIPT_INVALID");
    }
    else if (!(stagedNames.length === 0 && targetJson !== null)) fail("R22_STORE_TURN_DIRECTORY_INVALID");
    await assertDirectory(stagingPath, stageIdentity, operations);
  }
  return Object.freeze({ evidenceNames: Object.freeze(names.filter((name) => ordinary.has(name))), targetJson, stagingPath, stagedJson });
}

export async function readR22ActiveCallArtifacts(store) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    const active = state.checkpoint.active;
    if (active === null) return null;
    const directory = turnDirectory(state, active);
    const publication = await inspectReceiptPublication(directory, state.operations), names = publication.evidenceNames;
    if (!names.includes("call-plan.json")) fail("R22_STORE_TURN_DIRECTORY_INVALID");
    const readOptional = async (name) => names.includes(name) ? readStableText(path.join(directory, name), state.operations) : null;
    return Object.freeze({
      active: Object.freeze(structuredClone(active)),
      callPlanJson: await readOptional("call-plan.json"),
      dispatchRecordJson: await readOptional("dispatch-record.json"),
      validatedProposalRecordJson: await readOptional("validated-proposal-record.json"),
      displayAckRecordJson: await readOptional("display-ack-record.json"),
      turnReceiptJson: publication.targetJson,
      stagedTurnReceiptJson: publication.stagedJson,
    });
  });
}

export async function readR22FinalizedTurnReceipt(store, input) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    if (!exactObject(input, ["timelineId", "turnId", "callPlanSha256"]) ||
        input.timelineId !== state.config.timelineId || !IDENTIFIER.test(input.turnId ?? "") ||
        !SHA256.test(input.callPlanSha256 ?? "")) fail("R22_STORE_FINALIZED_RECEIPT_IDENTITY_INVALID");
    const item = state.checkpoint.finalized.find((candidate) => candidate.turnId === input.turnId &&
      candidate.callPlanSha256 === input.callPlanSha256);
    if (!item) return null;
    const directory = path.join(state.turnsRoot, `${String(item.sequence).padStart(6, "0")}-${item.callPlanSha256.slice(7)}`);
    const directoryStat = await state.operations.lstat(directory, { bigint: true }).catch(() => null);
    const directoryIdentity = identity(directoryStat);
    if (!isPlainDirectory(directoryStat) || directoryIdentity === null || !samePath(await state.operations.realpath(directory), directory)) {
      fail("R22_STORE_PATH_IDENTITY_INVALID");
    }
    const names = (await state.operations.readdir(directory)).sort();
    const allowed = new Set(["call-plan.json", "dispatch-record.json", "validated-proposal-record.json", "display-ack-record.json", "turn-receipt.json"]);
    if (!names.includes("call-plan.json") || !names.includes("turn-receipt.json") || names.some((name) => !allowed.has(name))) {
      fail("R22_STORE_TURN_DIRECTORY_INVALID");
    }
    const callPlanJson = await readStableText(path.join(directory, "call-plan.json"), state.operations);
    const callPlan = canonicalDocument(callPlanJson, validateNpcCognitionCallPlanJson, "R22_STORE_CALL_PLAN_INVALID");
    if (sha256Text(callPlanJson) !== item.callPlanSha256 || callPlan.turnId !== item.turnId || callPlan.turnId !== input.turnId) {
      fail("R22_STORE_FINALIZED_RECEIPT_IDENTITY_INVALID");
    }
    const turnReceiptJson = await readStableText(path.join(directory, "turn-receipt.json"), state.operations);
    const receipt = canonicalDocument(turnReceiptJson, validateNpcCognitionTurnReceiptJson, "R22_STORE_TURN_RECEIPT_INVALID");
    if (receipt.callPlanSha256 !== item.callPlanSha256 || receipt.turnSha256 !== callPlan.turnSha256 ||
        sha256Text(turnReceiptJson) !== item.turnReceiptSha256) fail("R22_STORE_FINALIZED_RECEIPT_IDENTITY_INVALID");
    await verifyReceiptEvidenceClosure(state, directory, callPlan, receipt, names);
    await assertDirectory(directory, directoryIdentity, state.operations);
    return Object.freeze({ turnReceiptJson, turnReceiptSha256: item.turnReceiptSha256 });
  });
}

export async function closeR22CallStore(store) {
  const state = storeState(store);
  return runExclusive(state, async () => {
    const found = await readStableText(state.writer.leasePath, state.operations, 64 * 1024).catch(() => null);
    if (found !== state.writer.record) fail("R22_STORE_WRITER_LOCK_LOST");
    await state.operations.rm(state.writer.leasePath, { force: false });
    state.closed = true;
    return Object.freeze({ ok: true });
  });
}

// Host-internal diagnostic account access, deliberately not a normal Turn store.
// No mkdir of a root/timeline, no readOrCreate budget and no second writer lock.
export function validateR22CognitionSessionManifest(value, hostRunId) {
  const hashes = ["sourceCurrentSha256", "sourceDerivedBundleSha256", "sourceAuthorityManifestSha256", "cognitionPolicySha256", "implementationSha256", "godotBinarySha256"];
  if (!exactObject(value, ["format", "formatVersion", "canonicalization", "hostRunId", "initialTimelineId", "providerMode", ...hashes]) ||
      value.format !== "matrix-oasis.r22-cognition-session-manifest" || value.formatVersion !== "0.1.0" ||
      value.canonicalization !== CANONICALIZATION || value.hostRunId !== hostRunId ||
      !["offline-fake", "official-once"].includes(value.providerMode) ||
      !hashes.every((key) => typeof value[key] === "string" && SHA256.test(value[key]))) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
  validateIdentifier(value.hostRunId, "R22_DIAGNOSTIC_SOURCE_INVALID");
  validateIdentifier(value.initialTimelineId, "R22_DIAGNOSTIC_SOURCE_INVALID");
  return value;
}

export async function openR22DiagnosticBudgetStore(config, operationsOverride) {
  const ops = captureOperations(operationsOverride);
  const keys = ["temporaryRoot", "cognitionRunRoot", "hostRunId", "expectedSessionManifestSha256", "expectedHostBudgetSha256"];
  if (!exactObject(config, keys) || !SHA256.test(config.expectedSessionManifestSha256 ?? "") ||
      !SHA256.test(config.expectedHostBudgetSha256 ?? "")) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
  validateIdentifier(config.hostRunId, "R22_DIAGNOSTIC_SOURCE_INVALID");
  if (![config.temporaryRoot, config.cognitionRunRoot].every((value) => typeof value === "string" && path.isAbsolute(value))) {
    fail("R22_DIAGNOSTIC_SOURCE_INVALID");
  }
  const root = path.resolve(config.cognitionRunRoot), temporaryRoot = path.resolve(config.temporaryRoot);
  if (!isDirectChild(temporaryRoot, root) || !root.endsWith("-cognition")) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
  const hierarchy = [];
  for (const directory of [temporaryRoot, root]) {
    const stat = await ops.lstat(directory, { bigint: true }).catch(() => null);
    if (!isPlainDirectory(stat) || identity(stat) === null) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
    await assertDirectory(directory, identity(stat), ops);
    hierarchy.push({ path: directory, identity: identity(stat) });
  }
  const pin = async (file, maximumBytes) => {
    const before = await ops.lstat(file, { bigint: true });
    if (!isPlainFile(before) || before.nlink !== 1n) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
    const text = await readStableText(file, ops, maximumBytes);
    const after = await ops.lstat(file, { bigint: true });
    if (!isPlainFile(after) || after.nlink !== 1n || identity(before) !== identity(after) ||
        stableState(before) !== stableState(after) || !samePath(await ops.realpath(file), file)) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
    return { text, identity: identity(after), state: stableState(after) };
  };
  const manifestPath = path.join(root, "cognition-session-manifest.json"), budgetPath = path.join(root, "host-budget.json");
  let writer;
  try {
    // Require both before acquiring a lock; an absent historical account must
    // not leave a new root, account, timeline, diagnostic directory or lease.
    const manifest = await pin(manifestPath, 16 * 1024), budgetBefore = await pin(budgetPath, MAX_INTERNAL_BYTES);
    if (sha256Text(manifest.text) !== config.expectedSessionManifestSha256 || sha256Text(budgetBefore.text) !== config.expectedHostBudgetSha256) {
      fail("R22_DIAGNOSTIC_SOURCE_INVALID");
    }
    const sessionManifest = parseCanonicalInternal(manifest.text, (value) => validateR22CognitionSessionManifest(value, config.hostRunId), "R22_DIAGNOSTIC_SOURCE_INVALID");
    const budget = parseCanonicalInternal(budgetBefore.text, (value) => validateHostBudget(value, config.hostRunId), "R22_STORE_HOST_BUDGET_INVALID");
    const random = ops.randomBytes(32);
    if (!(random instanceof Uint8Array) || random.byteLength !== 32) fail("R22_STORE_OPERATIONS_INVALID");
    const processEpochSha256 = sha256Text(Buffer.from(random));
    writer = await acquireWriterLease(root, ops, processEpochSha256);
    let budgetPin = budgetBefore, closed = false, closing = false, closeAttempt = null, poisoned = false, queue = Promise.resolve();
    const state = { operations: ops, hostRunId: config.hostRunId, hostBudgetPath: budgetPath, budget, diagnostic: true };
    const assertCurrent = async () => {
      if (closed || poisoned) fail("R22_DIAGNOSTIC_STORE_UNAVAILABLE");
      for (const directory of hierarchy) await assertDirectory(directory.path, directory.identity, ops);
      if ((await ops.readdir(root)).some((name) => name.startsWith(".s-"))) fail("R22_DIAGNOSTIC_INCOMPLETE_RECORD");
      if (await readStableText(writer.leasePath, ops, 64 * 1024) !== writer.record) fail("R22_STORE_WRITER_LOCK_LOST");
      const nextManifest = await pin(manifestPath, 16 * 1024), nextBudget = await pin(budgetPath, MAX_INTERNAL_BYTES);
      for (const [next, previous] of [[nextManifest, manifest], [nextBudget, budgetPin]]) {
        if (next.text !== previous.text || next.identity !== previous.identity || next.state !== previous.state) fail("R22_DIAGNOSTIC_SOURCE_INVALID");
      }
    };
    await assertCurrent();
    const exclusive = (operation) => {
      if (closing || closed) return Promise.reject(new R22CallStoreOperationalError("R22_DIAGNOSTIC_STORE_UNAVAILABLE"));
      const next = queue.then(async () => {
        try { await assertCurrent(); const result = await operation(); await assertCurrent(); return result; }
        catch (error) { poisoned = true; if (error instanceof R22CallStoreOperationalError) throw error; fail("R22_DIAGNOSTIC_SOURCE_INVALID"); }
      });
      queue = next.catch(() => {});
      return next;
    };
    const readBudgetPin = async () => { budgetPin = await pin(budgetPath, MAX_INTERNAL_BYTES); if (budgetPin.text !== canonicalizeJsonValue(state.budget)) fail("R22_DIAGNOSTIC_SOURCE_INVALID"); };
    const checkKey = (value) => {
      const hashKeys = ["authoritySessionSha256", "callPlanSha256", "transactionSha256"];
      const legacy = exactObject(value, hashKeys);
      if ((legacy && sessionManifest.providerMode !== "offline-fake") ||
          (!legacy && !exactObject(value, [...hashKeys, "executionKind", "sourceProviderMode"]))) fail("R22_DIAGNOSTIC_BUDGET_KEY_INVALID");
      const modes = legacy ? {} : { executionKind: value?.executionKind, sourceProviderMode: value?.sourceProviderMode };
      if ((!legacy && (!exactObject(value, [...hashKeys, "executionKind", "sourceProviderMode"]) ||
          !["official-once", "injected-transport"].includes(value.executionKind) || value.sourceProviderMode !== sessionManifest.providerMode ||
          value.sourceProviderMode !== (value.executionKind === "official-once" ? "official-once" : "offline-fake"))) ||
          !hashKeys.every((key) => SHA256.test(value?.[key] ?? "")) ||
          value.authoritySessionSha256 !== sha256Text(canonicalizeJsonValue({ purpose: legacy ? "matrix-oasis.r22-diagnostic-budget/1" : "matrix-oasis.r22-diagnostic-budget/2", ...modes, transactionSha256: value.transactionSha256 }))) {
        fail("R22_DIAGNOSTIC_BUDGET_KEY_INVALID");
      }
    };
    return Object.freeze({
      identity: Object.freeze({ hostRunId: config.hostRunId, processEpochSha256,
        providerMode: sessionManifest.providerMode, sessionManifestSha256: config.expectedSessionManifestSha256 }),
      revalidate: () => exclusive(async () => true),
      inspect: () => exclusive(async () => Object.freeze({ canonicalBudgetJson: canonicalizeJsonValue(state.budget), ...totalsForBudget(state.budget) })),
      reserve: (key) => exclusive(async () => {
        checkKey(key);
        const result = await reserveHostBudget(state, key.authoritySessionSha256, key.callPlanSha256);
        if (result.ok) await readBudgetPin();
        return result;
      }),
      settle: (key, dispatched) => exclusive(async () => {
        checkKey(key); if (typeof dispatched !== "boolean") fail("R22_DIAGNOSTIC_BUDGET_KEY_INVALID");
        const previous = state.budget.entries.find((entry) => entry.authoritySessionSha256 === key.authoritySessionSha256 && entry.callPlanSha256 === key.callPlanSha256);
        if (!previous) fail("R22_STORE_BUDGET_ENTRY_CONFLICT");
        const target = { ...previous, state: dispatched ? "charged" : "released", chargedMicrousd: dispatched ? previous.reservedMicrousd : 0 };
        if (previous.state !== "reserved" && canonicalizeJsonValue(previous) !== canonicalizeJsonValue(target)) fail("R22_STORE_BUDGET_ENTRY_CONFLICT");
        if (canonicalizeJsonValue(previous) !== canonicalizeJsonValue(target)) {
          replaceBudgetEntry(state, target); await persistBudget(state); await readBudgetPin();
        }
        return Object.freeze({ ok: true });
      }),
      close() {
        if (closeAttempt) return closeAttempt;
        if (closed) return Promise.resolve(Object.freeze({ ok: true }));
        closing = true;
        closeAttempt = (async () => {
          await queue;
          if (await readStableText(writer.leasePath, ops, 64 * 1024).catch(() => null) !== writer.record) fail("R22_STORE_WRITER_LOCK_LOST");
          try { await ops.rm(writer.leasePath, { force: false }); }
          catch {
            let remaining;
            try { remaining = await readStableText(writer.leasePath, ops, 64 * 1024); }
            catch (error) { if (error?.code === "ENOENT") remaining = null; else fail("R22_STORE_WRITER_LOCK_LOST"); }
            if (remaining !== null) fail(remaining === writer.record ? "R22_STORE_WRITE_FAILED" : "R22_STORE_WRITER_LOCK_LOST");
          }
          closed = true;
          return Object.freeze({ ok: true });
        })().finally(() => { closeAttempt = null; });
        // A failed close may retry only this cleanup. Business operations stay
        // fenced by closing=true, even after a transient filesystem error.
        return closeAttempt;
      },
    });
  } catch (error) {
    if (writer && await readStableText(writer.leasePath, ops, 64 * 1024).catch(() => null) === writer.record) await ops.rm(writer.leasePath, { force: false }).catch(() => {});
    if (error instanceof R22CallStoreOperationalError) throw error;
    fail("R22_DIAGNOSTIC_SOURCE_INVALID");
  }
}
