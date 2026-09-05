import { canonicalText, sha256 } from "./r22-cli-core.mjs";
import { validateWorldEventLedgerJson } from "@matrix-oasis/npc-authority-contracts";

export const R22_PHYSICAL_MARKER = "R22_LIVE_PHYSICAL_EVIDENCE_JSON:";
const LIMIT = 32768;
const CANONICAL = "matrix-oasis.canonical-json/1";
const HASH = /^sha256:[0-9a-f]{64}$/u;
const VALIDATION_STAGES = new Set(["input", "canonical-json", "envelope", "return", "ledger", "command", "adjudication", "arrival", "trace", "performance"]);
function fail() { throw new Error("R22_LIVE_PHYSICAL_EVIDENCE_INVALID"); }
function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).sort().join("\0") !== [...keys].sort().join("\0")) fail();
}
function same(a, b) { if (canonicalText(a) !== canonicalText(b)) fail(); }
function integer(value, low, high) { if (!Number.isSafeInteger(value) || value < low || value > high) fail(); }
function freeze(value) { if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }

// A process marker is an observation, never authority. The caller must first
// fully replay the pinned R19 session; this check then binds the observation to
// that session's committed command, actual arrival proof, and mirror hashes.
export function validateR22LivePhysicalEvidence(text, snapshot, entityBindingSha256) {
  let stage = "input";
  try {
    if (typeof text !== "string" || Buffer.byteLength(text) > LIMIT || !HASH.test(entityBindingSha256)) fail();
    const value = JSON.parse(text);
    stage = "canonical-json";
    if (canonicalText(value) !== text) fail();
    stage = "envelope";
    exact(value, ["format", "formatVersion", "canonicalization", "timelineId", "entityBindingSha256", "command", "authority", "movement", "performance", "canonicalR20Trace"]);
    if (value.format !== "matrix-oasis.r22-live-physical-evidence" || value.formatVersion !== "0.1.0" ||
        value.canonicalization !== CANONICAL || value.entityBindingSha256 !== entityBindingSha256 || snapshot?.frozen !== false) fail();
    exact(value.command, ["sequence", "actorEntityId", "ruleIndex", "intentId", "nodeId", "actionId", "commandSha256", "intentSha256"]);
    exact(value.authority, ["decision", "beforeSnapshotSha256", "afterSnapshotSha256"]);
    exact(value.movement, ["outbound", "return"]);
    exact(value.movement.outbound, ["pathComplete", "floorVerified", "capsuleVerified", "domainVerified", "movementTicks", "pathLengthMm"]);
    exact(value.movement.return, ["kind", "returnedHome", "physicsProcessing", "positionErrorMm"]);
    stage = "return";
    const returned = value.movement.return;
    if (!["walked-home", "hidden-home"].includes(returned.kind) || returned.returnedHome !== true || returned.physicsProcessing !== false) fail();
    integer(returned.positionErrorMm, 0, 100);
    stage = "ledger";
    const ledgerText = snapshot?.authority?.canonicalWorldEventLedgerJson;
    const valid = validateWorldEventLedgerJson(ledgerText);
    if (!valid.valid || valid.diagnostics.length !== 0) fail();
    const ledger = JSON.parse(ledgerText);
    if (ledger.timeline.id !== value.timelineId || canonicalText(ledger) !== ledgerText) fail();
    stage = "command";
    const matches = snapshot.commands.filter((item) => item.sequence === value.command.sequence);
    if (matches.length !== 1) fail();
    const committed = matches[0];
    const entries = ledger.entries.filter((item) => item.intent.id === committed.intentId);
    if (entries.length !== 1) fail();
    const entry = entries[0];
    const identity = Object.fromEntries(["sequence", "actorEntityId", "ruleIndex", "intentId", "nodeId", "actionId"].map((key) => [key, committed[key]]));
    const npcIntentJson = canonicalText(entry.intent);
    const command = { ...identity, npcIntentJson };
    same(value.command, { ...identity, commandSha256: sha256(canonicalText(command)), intentSha256: sha256(npcIntentJson) });
    stage = "adjudication";
    if (committed.state !== entry.decision.status || committed.revisionFinished !== entry.revision ||
        committed.mirrorEvidence.entityBindingSha256 !== entityBindingSha256 ||
        committed.mirrorEvidence.commandSha256 !== sha256(canonicalText(identity))) fail();
    same(value.authority, { decision: entry.decision.status, beforeSnapshotSha256: entry.beforeSnapshotSha256, afterSnapshotSha256: entry.afterSnapshotSha256 });
    if (committed.mirrorEvidence.beforeSnapshotSha256 !== entry.beforeSnapshotSha256 ||
        committed.mirrorEvidence.afterSnapshotSha256 !== entry.afterSnapshotSha256) fail();
    stage = "arrival";
    // R20 stores sequence on the command, not inside its six-field arrival proof.
    const sequence = identity.sequence, arrival = committed.arrivalEvidence;
    exact(arrival, ["pathComplete", "floorVerified", "capsuleVerified", "domainVerified", "movementTicks", "pathLengthMm"]);
    if (["pathComplete", "floorVerified", "capsuleVerified", "domainVerified"].some((key) => arrival[key] !== true)) fail();
    integer(arrival.movementTicks, 0, 1800); integer(arrival.pathLengthMm, 0, 100000);
    same(value.movement.outbound, arrival);
    stage = "trace";
    const expectedTrace = [
      { sequence, actorEntityId: identity.actorEntityId, actionId: identity.actionId, state: "arrived", arrivalEvidence: arrival },
      { sequence, actorEntityId: identity.actorEntityId, actionId: identity.actionId, state: "mirrored", ...value.authority },
    ];
    if (value.canonicalR20Trace !== canonicalText(expectedTrace)) fail();
    stage = "performance";
    exact(value.performance, ["sampleCount", "frameMicros", "medianFrameMicros", "medianFpsMilli"]);
    const performance = value.performance;
    if (performance.sampleCount !== 300 || !Array.isArray(performance.frameMicros) || performance.frameMicros.length !== 300) fail();
    performance.frameMicros.forEach((frame) => integer(frame, 1, 60000000));
    const frames = [...performance.frameMicros].sort((a, b) => a - b);
    const median = Math.floor((frames[149] + frames[150]) / 2);
    if (performance.medianFrameMicros !== median || performance.medianFpsMilli !== Math.floor(1000000000 / median)) fail();
    return freeze({ observation: value, entrySha256: entry.entrySha256, revision: entry.revision,
      ledgerSha256: sha256(ledgerText), performancePassed: performance.medianFpsMilli >= 30000,
      observationSha256: sha256(text) });
  } catch {
    const error = new Error("R22_LIVE_PHYSICAL_EVIDENCE_INVALID");
    error.validationStage = stage;
    throw error;
  }
}

// Only complete, bounded UTF-8 marker lines from the launched child's stdout
// reach the consumer. Other output is transient and is never persisted here.
export function collectR22LivePhysicalEvidence(child, accept, onFailure = () => {}) {
  let buffer = Buffer.alloc(0), total = 0, failed = false, ended = false, pending = Promise.resolve();
  let resolveFailure;
  const failure = new Promise((resolve) => { resolveFailure = resolve; });
  const reject = (error) => {
    if (failed) return;
    failed = true; buffer = Buffer.alloc(0);
    let stage = "collection-or-publication";
    try { if (VALIDATION_STAGES.has(error?.validationStage)) stage = error.validationStage; } catch { /* No raw error data is observed. */ }
    try { onFailure(Object.freeze({ code: "R22_LIVE_PHYSICAL_EVIDENCE_INVALID", stage })); } catch { /* Reporting cannot suppress fail-closed cleanup. */ }
    resolveFailure("physical-evidence-failure");
  };
  const consume = (line) => {
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(line); } catch { reject(); return; }
    if (text.endsWith("\r")) text = text.slice(0, -1);
    if (!text.startsWith(R22_PHYSICAL_MARKER)) return;
    const body = text.slice(R22_PHYSICAL_MARKER.length);
    if (Buffer.byteLength(body) > LIMIT || typeof accept !== "function") { reject(); return; }
    pending = pending.then(async () => { if (!failed) await accept(body); }).catch(reject);
  };
  const feed = (chunk) => {
    if (failed || ended) return;
    total += chunk.length;
    if (total > 8 * 1024 * 1024) { reject(); return; }
    buffer = Buffer.concat([buffer, Buffer.from(chunk)]);
    let newline;
    while ((newline = buffer.indexOf(10)) >= 0 && !failed) {
      const line = buffer.subarray(0, newline); buffer = buffer.subarray(newline + 1);
      if (line.length > LIMIT + R22_PHYSICAL_MARKER.length + 1) { reject(); return; }
      consume(line);
    }
    if (buffer.length > LIMIT + R22_PHYSICAL_MARKER.length + 1) reject();
  };
  child.stdout?.on("data", feed);
  return Object.freeze({ failure, async finish() {
    if (!ended) {
      ended = true; child.stdout?.removeListener("data", feed);
      if (buffer.length > 0) consume(buffer); buffer = Buffer.alloc(0);
    }
    await pending;
    if (failed) throw new Error("R22_LIVE_PHYSICAL_EVIDENCE_INVALID");
  } });
}
