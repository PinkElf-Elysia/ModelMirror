import {
  NPC_ADJUDICATION_RESULT_FORMAT,
  NPC_AUTHORITY_CANONICALIZATION,
  NPC_AUTHORITY_FORMAT_VERSION,
  NPC_AUTHORITY_LIMITS,
  WORLD_EVENT_LEDGER_REPLAY_REPORT_FORMAT,
  validateNpcAdjudicationResultJson,
  validateNpcAuthorityPolicyJson,
  validateNpcIntentJson,
  validateWorldEventLedgerJson,
  validateWorldEventLedgerReplayReportJson,
} from "@matrix-oasis/npc-authority-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  inspectRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import {
  NpcAuthorityRuntimeOperationalError,
  appendWorldEventLedgerEntryCore,
  appendValidatedWorldEventLedgerEntry,
  createWorldEventLedgerCore,
  hashCanonicalValue,
} from "./ledger.mjs";

const preparedData = new WeakMap();
const incrementalStateData = new WeakMap();

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function diag(code, path = "") {
  return deepFreeze({ phase: "runtime", severity: "error", code, path, message: code });
}

function fail(code, path = "") {
  return deepFreeze({ ok: false, diagnostics: [diag(code, path)] });
}

function validationFailure(report) {
  return deepFreeze({ ok: false, diagnostics: report.diagnostics });
}

function parseValidated(text, validator) {
  const report = validator(text);
  return report.valid ? { ok: true, value: JSON.parse(text) } : { ok: false, report };
}

function runtimeIdentity(pack, receipt) {
  return {
    format: pack.format,
    formatVersion: pack.formatVersion,
    id: pack.source.id,
    contentVersion: pack.source.contentVersion,
    sourceSha256: `sha256:${pack.source.canonicalSha256}`,
    artifactSha256: `sha256:${receipt.artifact.sha256}`,
    receiptSha256: hashCanonicalValue(receipt),
  };
}

function compareCanonical(left, right) {
  return canonicalizeJsonValue(left) === canonicalizeJsonValue(right);
}

function semanticPolicyDiagnostics(pack, policy, identity) {
  const output = [];
  if (!compareCanonical(policy.runtime, identity)) output.push(diag("NPC_AUTHORITY_POLICY_RUNTIME_IDENTITY_MISMATCH", "/runtime"));
  const entities = new Set(pack.entities.map((entity) => entity.id));
  const nodes = new Map(pack.nodes.map((node) => [node.id, node]));
  policy.actorGrants.forEach((actor, actorIndex) => {
    if (!entities.has(actor.actorEntityId)) output.push(diag("NPC_AUTHORITY_POLICY_ACTOR_NOT_FOUND", `/actorGrants/${actorIndex}/actorEntityId`));
    actor.grants.forEach((grant, grantIndex) => {
      const node = nodes.get(grant.nodeId);
      if (!node) output.push(diag("NPC_AUTHORITY_POLICY_NODE_NOT_FOUND", `/actorGrants/${actorIndex}/grants/${grantIndex}/nodeId`));
      else if (!node.actions.some((action) => action.id === grant.actionId)) output.push(diag("NPC_AUTHORITY_POLICY_ACTION_NOT_FOUND", `/actorGrants/${actorIndex}/grants/${grantIndex}/actionId`));
    });
  });
  return output;
}

function dataFor(prepared) {
  if (!prepared || (typeof prepared !== "object" && typeof prepared !== "function")) return undefined;
  return preparedData.get(prepared);
}

function ledgerAuthorityMatches(data, ledger) {
  return compareCanonical(ledger.authority.runtime, data.runtimeIdentity) &&
    ledger.authority.policy.id === data.policy.id &&
    ledger.authority.policy.contentVersion === data.policy.contentVersion &&
    ledger.authority.policy.canonicalSha256 === data.policySha256;
}

function mapRuntimeFailure(result) {
  const code = result.diagnostics?.[0]?.code;
  const mapped = {
    PACK_RUNTIME_SESSION_ENDED: "NPC_INTENT_SESSION_ENDED",
    PACK_RUNTIME_STEP_LIMIT: "NPC_INTENT_STEP_LIMIT",
    PACK_RUNTIME_ACTION_UNKNOWN: "NPC_INTENT_ACTION_NOT_FOUND",
    PACK_RUNTIME_ACTION_UNAVAILABLE: "NPC_INTENT_ACTION_UNAVAILABLE",
    PACK_RUNTIME_INTEGER_OVERFLOW: "NPC_INTENT_INTEGER_OVERFLOW",
  }[code];
  return mapped ? { kind: "decision", decision: { status: "rejected", reason: mapped } } : { kind: "failure" };
}

function evaluateIntent(data, snapshot, inspection, intent) {
  if (!data.entities.has(intent.actorEntityId)) return { decision: { status: "rejected", reason: "NPC_INTENT_ACTOR_NOT_FOUND" }, transition: null, snapshot };
  const node = data.nodes.get(intent.nodeId);
  if (!node) return { decision: { status: "rejected", reason: "NPC_INTENT_NODE_NOT_FOUND" }, transition: null, snapshot };
  if (!node.actions.some((action) => action.id === intent.actionId)) return { decision: { status: "rejected", reason: "NPC_INTENT_ACTION_NOT_FOUND" }, transition: null, snapshot };
  if (inspection.status === "ended") return { decision: { status: "rejected", reason: "NPC_INTENT_SESSION_ENDED" }, transition: null, snapshot };
  if (inspection.location.id !== intent.nodeId) return { decision: { status: "rejected", reason: "NPC_INTENT_NODE_MISMATCH" }, transition: null, snapshot };
  if (!data.grants.get(intent.actorEntityId)?.has(`${intent.nodeId}\0${intent.actionId}`)) {
    return { decision: { status: "rejected", reason: "NPC_INTENT_ACTOR_UNAUTHORIZED" }, transition: null, snapshot };
  }
  const applied = applyRuntimeGameSessionAction(data.runtimePrepared, snapshot, intent.actionId);
  if (!applied.ok) {
    const mapped = mapRuntimeFailure(applied);
    if (mapped.kind === "failure") return undefined;
    return { decision: mapped.decision, transition: null, snapshot };
  }
  return { decision: { status: "accepted", reason: "NPC_INTENT_ACCEPTED" }, transition: applied.transition, snapshot: applied.snapshot, inspection: applied.inspection };
}

function resultDocument({ timelineId, intentId, replayed, revision, headSha256, entry }) {
  return {
    format: NPC_ADJUDICATION_RESULT_FORMAT,
    formatVersion: NPC_AUTHORITY_FORMAT_VERSION,
    canonicalization: NPC_AUTHORITY_CANONICALIZATION,
    timelineId,
    intentId,
    replayed,
    revision,
    headSha256,
    decision: entry.decision,
    beforeSnapshotSha256: entry.beforeSnapshotSha256,
    afterSnapshotSha256: entry.afterSnapshotSha256,
    transition: entry.transition,
  };
}

function captureResultDocument(document) {
  const canonicalNpcAdjudicationResultJson = canonicalizeJsonValue(document);
  const report = validateNpcAdjudicationResultJson(canonicalNpcAdjudicationResultJson);
  return report.valid ? { ok: true, canonicalNpcAdjudicationResultJson } : validationFailure(report);
}

function replayLedger(data, ledgerJson, ledger) {
  if (!ledgerAuthorityMatches(data, ledger)) return fail("WORLD_EVENT_LEDGER_AUTHORITY_MISMATCH", "/authority");
  const created = createRuntimeGameSession(data.runtimePrepared, { stepLimit: ledger.timeline.stepLimit });
  if (!created.ok) return fail("WORLD_EVENT_LEDGER_REPLAY_RUNTIME_FAILURE");
  if (hashCanonicalValue(created.snapshot) !== ledger.authority.initialSnapshotSha256) return fail("WORLD_EVENT_LEDGER_INITIAL_SNAPSHOT_MISMATCH", "/authority/initialSnapshotSha256");
  let snapshot = created.snapshot;
  let inspection = created.inspection;
  let acceptedEntries = 0;
  let rejectedEntries = 0;
  for (let index = 0; index < ledger.entries.length; index += 1) {
    const entry = ledger.entries[index];
    const beforeHash = hashCanonicalValue(snapshot);
    if (beforeHash !== entry.beforeSnapshotSha256) return fail("WORLD_EVENT_LEDGER_REPLAY_BEFORE_MISMATCH", `/entries/${index}/beforeSnapshotSha256`);
    const evaluated = evaluateIntent(data, snapshot, inspection, entry.intent);
    if (!evaluated) return fail("WORLD_EVENT_LEDGER_REPLAY_RUNTIME_FAILURE", `/entries/${index}`);
    if (!compareCanonical(evaluated.decision, entry.decision)) return fail("WORLD_EVENT_LEDGER_REPLAY_DECISION_MISMATCH", `/entries/${index}/decision`);
    if (!compareCanonical(evaluated.transition, entry.transition)) return fail("WORLD_EVENT_LEDGER_REPLAY_TRANSITION_MISMATCH", `/entries/${index}/transition`);
    if (entry.decision.status === "accepted") acceptedEntries += 1;
    else rejectedEntries += 1;
    snapshot = evaluated.snapshot;
    const inspected = inspectRuntimeGameSession(data.runtimePrepared, snapshot);
    if (!inspected?.ok) return fail("WORLD_EVENT_LEDGER_REPLAY_RUNTIME_FAILURE", `/entries/${index}`);
    inspection = inspected.inspection;
    if (hashCanonicalValue(snapshot) !== entry.afterSnapshotSha256) return fail("WORLD_EVENT_LEDGER_REPLAY_AFTER_MISMATCH", `/entries/${index}/afterSnapshotSha256`);
  }
  const reportDocument = {
    format: WORLD_EVENT_LEDGER_REPLAY_REPORT_FORMAT,
    formatVersion: NPC_AUTHORITY_FORMAT_VERSION,
    canonicalization: NPC_AUTHORITY_CANONICALIZATION,
    timelineId: ledger.timeline.id,
    ledgerSha256: hashCanonicalValue(ledger),
    throughRevision: ledger.revision,
    throughHeadSha256: ledger.headSha256,
    verifiedEntries: ledger.entries.length,
    acceptedEntries,
    rejectedEntries,
    finalSnapshotSha256: hashCanonicalValue(snapshot),
    finalInspectionSha256: hashCanonicalValue(inspection),
  };
  const canonicalWorldEventLedgerReplayReportJson = canonicalizeJsonValue(reportDocument);
  const report = validateWorldEventLedgerReplayReportJson(canonicalWorldEventLedgerReplayReportJson);
  if (!report.valid) return validationFailure(report);
  return deepFreeze({ ok: true, runtimeSnapshot: snapshot, inspection, canonicalWorldEventLedgerJson: ledgerJson, canonicalWorldEventLedgerReplayReportJson });
}

export async function prepareNpcAuthority(input) {
  try {
    const { runtimeGamePackJson, runtimeReceiptJson, policyJson } = input ?? {};
    const policyResult = parseValidated(policyJson, validateNpcAuthorityPolicyJson);
    if (!policyResult.ok) return validationFailure(policyResult.report);
    const runtime = await prepareRuntimeGamePackJson(runtimeGamePackJson, runtimeReceiptJson);
    if (!runtime.ok) return validationFailure(runtime.validationReport);
    const pack = JSON.parse(runtimeGamePackJson);
    const receipt = JSON.parse(runtimeReceiptJson);
    const identity = runtimeIdentity(pack, receipt);
    const policyDiagnostics = semanticPolicyDiagnostics(pack, policyResult.value, identity);
    if (policyDiagnostics.length) return deepFreeze({ ok: false, diagnostics: policyDiagnostics });
    const grants = new Map(policyResult.value.actorGrants.map((actor) => [actor.actorEntityId, new Set(actor.grants.map((grant) => `${grant.nodeId}\0${grant.actionId}`))]));
    const handle = Object.freeze(Object.create(null));
    preparedData.set(handle, Object.freeze({
      runtimePrepared: runtime.prepared,
      pack,
      receipt,
      policy: policyResult.value,
      policyJson,
      policySha256: hashCanonicalValue(policyResult.value),
      runtimeIdentity: identity,
      grants,
      entities: new Set(pack.entities.map((entity) => entity.id)),
      nodes: new Map(pack.nodes.map((node) => [node.id, node])),
    }));
    return deepFreeze({ ok: true, prepared: handle });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function createNpcAuthorityTimeline(prepared, options) {
  try {
    const { timelineId, stepLimit } = options ?? {};
    const data = dataFor(prepared);
    if (!data) return fail("NPC_AUTHORITY_PREPARED_INVALID");
    const created = createRuntimeGameSession(data.runtimePrepared, stepLimit === undefined ? undefined : { stepLimit });
    if (!created.ok) return deepFreeze({ ok: false, diagnostics: created.diagnostics });
    const ledger = createWorldEventLedgerCore({
      policyJson: data.policyJson,
      timelineId,
      stepLimit: created.snapshot.stepLimit,
      initialSnapshotSha256: hashCanonicalValue(created.snapshot),
    });
    if (!ledger.ok) return ledger;
    return deepFreeze({ ok: true, runtimeSnapshot: created.snapshot, inspection: created.inspection, canonicalWorldEventLedgerJson: ledger.canonicalWorldEventLedgerJson });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function replayWorldEventLedger(input) {
  try {
    const { prepared, worldEventLedgerJson } = input ?? {};
    const data = dataFor(prepared);
    if (!data) return fail("NPC_AUTHORITY_PREPARED_INVALID");
    const ledgerResult = parseValidated(worldEventLedgerJson, validateWorldEventLedgerJson);
    if (!ledgerResult.ok) return validationFailure(ledgerResult.report);
    return replayLedger(data, worldEventLedgerJson, ledgerResult.value);
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function adjudicateNpcIntent(input) {
  try {
    const { prepared, runtimeSnapshot, worldEventLedgerJson, npcIntentJson } = input ?? {};
    const data = dataFor(prepared);
    if (!data) return fail("NPC_AUTHORITY_PREPARED_INVALID");
    const intentResult = parseValidated(npcIntentJson, validateNpcIntentJson);
    if (!intentResult.ok) return validationFailure(intentResult.report);
    const replayedLedger = replayWorldEventLedger({ prepared, worldEventLedgerJson });
    if (!replayedLedger.ok) return replayedLedger;
    const ledger = JSON.parse(worldEventLedgerJson);
    const duplicate = ledger.entries.find((entry) => entry.intent.id === intentResult.value.id);
    if (duplicate && canonicalizeJsonValue(duplicate.intent) !== npcIntentJson) return fail("NPC_INTENT_ID_COLLISION", "/id");
    if (duplicate) {
      const document = resultDocument({
        timelineId: intentResult.value.timelineId,
        intentId: intentResult.value.id,
        replayed: true,
        revision: duplicate.revision,
        headSha256: duplicate.entrySha256,
        entry: duplicate,
      });
      const captured = captureResultDocument(document);
      if (!captured.ok) return captured;
      return deepFreeze({ ok: true, replayed: true, canonicalAdjudicationResultJson: captured.canonicalNpcAdjudicationResultJson, runtimeSnapshot: replayedLedger.runtimeSnapshot, canonicalWorldEventLedgerJson: worldEventLedgerJson });
    }
    if (!compareCanonical(replayedLedger.runtimeSnapshot, runtimeSnapshot)) return fail("NPC_INTENT_RUNTIME_SNAPSHOT_MISMATCH", "/runtimeSnapshot");
    if (intentResult.value.timelineId !== ledger.timeline.id) return fail("NPC_INTENT_TIMELINE_MISMATCH", "/timelineId");
    if (intentResult.value.observed.revision !== ledger.revision) return fail("NPC_INTENT_STALE_REVISION", "/observed/revision");
    if (intentResult.value.observed.headSha256 !== ledger.headSha256) return fail("NPC_INTENT_STALE_HEAD", "/observed/headSha256");
    const snapshotSha256 = hashCanonicalValue(runtimeSnapshot);
    if (intentResult.value.observed.runtimeSnapshotSha256 !== snapshotSha256) return fail("NPC_INTENT_STALE_SNAPSHOT", "/observed/runtimeSnapshotSha256");
    if (ledger.revision >= NPC_AUTHORITY_LIMITS.ledgerEntries) return fail("WORLD_EVENT_LEDGER_CAPACITY_EXCEEDED", "/revision");
    const inspected = inspectRuntimeGameSession(data.runtimePrepared, runtimeSnapshot);
    if (!inspected.ok) return deepFreeze({ ok: false, diagnostics: inspected.diagnostics });
    const evaluated = evaluateIntent(data, runtimeSnapshot, inspected.inspection, intentResult.value);
    if (!evaluated) return fail("NPC_AUTHORITY_RUNTIME_FAILURE");
    const afterSha256 = hashCanonicalValue(evaluated.snapshot);
    const appended = appendWorldEventLedgerEntryCore({
      worldEventLedgerJson,
      npcIntentJson,
      decision: evaluated.decision,
      beforeSnapshotSha256: snapshotSha256,
      afterSnapshotSha256: afterSha256,
      transition: evaluated.transition,
    });
    if (!appended.ok || appended.kind !== "appended") return appended;
    const nextLedger = JSON.parse(appended.canonicalWorldEventLedgerJson);
    const document = resultDocument({
      timelineId: intentResult.value.timelineId,
      intentId: intentResult.value.id,
      replayed: false,
      revision: nextLedger.revision,
      headSha256: nextLedger.headSha256,
      entry: appended.entry,
    });
    const captured = captureResultDocument(document);
    if (!captured.ok) return captured;
    return deepFreeze({ ok: true, replayed: false, canonicalAdjudicationResultJson: captured.canonicalNpcAdjudicationResultJson, runtimeSnapshot: evaluated.snapshot, canonicalWorldEventLedgerJson: appended.canonicalWorldEventLedgerJson });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function createNpcAuthorityIncrementalState(input) {
  try {
    const { prepared, worldEventLedgerJson } = input ?? {};
    const data = dataFor(prepared);
    if (!data) return fail("NPC_AUTHORITY_PREPARED_INVALID");
    const replayed = replayWorldEventLedger({ prepared, worldEventLedgerJson });
    if (!replayed.ok) return replayed;
    const ledger = JSON.parse(worldEventLedgerJson);
    const handle = Object.freeze(Object.create(null));
    incrementalStateData.set(handle, {
      prepared,
      data,
      ledger,
      ledgerJson: worldEventLedgerJson,
      snapshot: replayed.runtimeSnapshot,
      inspection: replayed.inspection,
      fullReplayCount: 1,
    });
    return deepFreeze({ ok: true, state: handle, runtimeSnapshot: replayed.runtimeSnapshot, inspection: replayed.inspection, canonicalWorldEventLedgerJson: worldEventLedgerJson });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

function powerOfTwo(value) {
  return value > 0 && (value & (value - 1)) === 0;
}

export function submitNpcAuthorityIncrementalIntent(input) {
  try {
    const { state, npcIntentJson } = input ?? {};
    const current = incrementalStateData.get(state);
    if (!current) return fail("NPC_AUTHORITY_INCREMENTAL_STATE_INVALID");
    const intentResult = parseValidated(npcIntentJson, validateNpcIntentJson);
    if (!intentResult.ok) return validationFailure(intentResult.report);
    const intent = intentResult.value;
    const duplicate = current.ledger.entries.find((entry) => entry.intent.id === intent.id);
    if (duplicate && canonicalizeJsonValue(duplicate.intent) !== npcIntentJson) return fail("NPC_INTENT_ID_COLLISION", "/id");
    if (duplicate) {
      const document = resultDocument({ timelineId: intent.timelineId, intentId: intent.id, replayed: true, revision: duplicate.revision, headSha256: duplicate.entrySha256, entry: duplicate });
      const captured = captureResultDocument(document);
      if (!captured.ok) return captured;
      return deepFreeze({ ok: true, replayed: true, fullReplayPerformed: false, canonicalAdjudicationResultJson: captured.canonicalNpcAdjudicationResultJson, runtimeSnapshot: current.snapshot, inspection: current.inspection, canonicalWorldEventLedgerJson: current.ledgerJson });
    }
    if (intent.timelineId !== current.ledger.timeline.id) return fail("NPC_INTENT_TIMELINE_MISMATCH", "/timelineId");
    if (intent.observed.revision !== current.ledger.revision) return fail("NPC_INTENT_STALE_REVISION", "/observed/revision");
    if (intent.observed.headSha256 !== current.ledger.headSha256) return fail("NPC_INTENT_STALE_HEAD", "/observed/headSha256");
    const beforeSnapshotSha256 = hashCanonicalValue(current.snapshot);
    if (intent.observed.runtimeSnapshotSha256 !== beforeSnapshotSha256) return fail("NPC_INTENT_STALE_SNAPSHOT", "/observed/runtimeSnapshotSha256");
    if (current.ledger.revision >= NPC_AUTHORITY_LIMITS.ledgerEntries) return fail("WORLD_EVENT_LEDGER_CAPACITY_EXCEEDED", "/revision");
    const evaluated = evaluateIntent(current.data, current.snapshot, current.inspection, intent);
    if (!evaluated) return fail("NPC_AUTHORITY_RUNTIME_FAILURE");
    const appended = appendValidatedWorldEventLedgerEntry({ ledger: current.ledger, intent, decision: evaluated.decision, beforeSnapshotSha256, afterSnapshotSha256: hashCanonicalValue(evaluated.snapshot), transition: evaluated.transition, validateOutput: false });
    if (!appended.ok) return appended;
    const inspected = inspectRuntimeGameSession(current.data.runtimePrepared, evaluated.snapshot);
    if (!inspected.ok) return deepFreeze({ ok: false, diagnostics: inspected.diagnostics });
    current.ledger = appended.ledger;
    current.ledgerJson = appended.canonicalWorldEventLedgerJson;
    current.snapshot = evaluated.snapshot;
    current.inspection = inspected.inspection;
    let fullReplayPerformed = false;
    if (powerOfTwo(current.ledger.revision)) {
      const replayed = replayWorldEventLedger({ prepared: current.prepared, worldEventLedgerJson: current.ledgerJson });
      if (!replayed.ok || !compareCanonical(replayed.runtimeSnapshot, current.snapshot) || !compareCanonical(replayed.inspection, current.inspection)) return fail("NPC_AUTHORITY_INCREMENTAL_REPLAY_MISMATCH");
      current.fullReplayCount += 1;
      fullReplayPerformed = true;
    }
    const document = resultDocument({ timelineId: intent.timelineId, intentId: intent.id, replayed: false, revision: appended.entry.revision, headSha256: appended.entry.entrySha256, entry: appended.entry });
    const captured = captureResultDocument(document);
    if (!captured.ok) return captured;
    return deepFreeze({ ok: true, replayed: false, fullReplayPerformed, canonicalAdjudicationResultJson: captured.canonicalNpcAdjudicationResultJson, runtimeSnapshot: current.snapshot, inspection: current.inspection, canonicalWorldEventLedgerJson: current.ledgerJson });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function exportNpcAuthorityIncrementalState(state) {
  try {
    const current = incrementalStateData.get(state);
    if (!current) return fail("NPC_AUTHORITY_INCREMENTAL_STATE_INVALID");
    return deepFreeze({ ok: true, runtimeSnapshot: current.snapshot, inspection: current.inspection, canonicalWorldEventLedgerJson: current.ledgerJson, fullReplayCount: current.fullReplayCount });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function verifyNpcAuthorityIncrementalState(state) {
  try {
    const current = incrementalStateData.get(state);
    if (!current) return fail("NPC_AUTHORITY_INCREMENTAL_STATE_INVALID");
    const replayed = replayWorldEventLedger({ prepared: current.prepared, worldEventLedgerJson: current.ledgerJson });
    if (!replayed.ok || !compareCanonical(replayed.runtimeSnapshot, current.snapshot) || !compareCanonical(replayed.inspection, current.inspection)) return fail("NPC_AUTHORITY_INCREMENTAL_REPLAY_MISMATCH");
    current.fullReplayCount += 1;
    return deepFreeze({ ...replayed, fullReplayCount: current.fullReplayCount });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}
