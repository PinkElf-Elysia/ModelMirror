import { createHash } from "node:crypto";
import { createNpcAuthoritySession, exportNpcAuthoritySession, submitNpcAuthorityIntent, verifyNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { selectEligibleNpcBehaviorCommand } from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const CODE = "R22_RECOVERY_BEHAVIOR_INVALID";
const IDENTITIES = ["sequence", "actorEntityId", "ruleIndex", "intentId", "nodeId", "actionId"];
function fail() { throw new Error(CODE); }
function hash(text) { return `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`; }
function frozen(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) frozen(child);
  return Object.freeze(value);
}
function canonical(text) {
  if (typeof text !== "string") fail();
  let value; try { value = JSON.parse(text); } catch { fail(); }
  if (canonicalizeJsonValue(value) !== text) fail();
  return value;
}
function same(left, right) { return canonicalizeJsonValue(left) === canonicalizeJsonValue(right); }

export async function rebuildR22RecoveredBehavior(input) {
  try {
    const { source, recovery, preparedBehavior, initialBehaviorState } = input ?? {};
    if (!source || !recovery || !preparedBehavior || !initialBehaviorState ||
        ![source.runtimeGamePackJson, source.runtimeReceiptJson, source.authorityPolicyJson].every((item) => typeof item === "string") ||
        !Array.isArray(recovery.behaviorTrace?.commands)) fail();
    const expectedLedgerJson = recovery.canonicalWorldEventLedgerJson;
    const expectedLedger = canonical(expectedLedgerJson);
    if (typeof expectedLedger.timeline?.id !== "string" || !Number.isSafeInteger(expectedLedger.timeline.stepLimit) ||
        !Array.isArray(expectedLedger.entries) || expectedLedger.entries.length !== recovery.behaviorTrace.commands.length) fail();
    const created = await createNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson,
      runtimeReceiptJson: source.runtimeReceiptJson, policyJson: source.authorityPolicyJson,
      timelineId: expectedLedger.timeline.id, stepLimit: expectedLedger.timeline.stepLimit });
    if (!created?.ok) fail();
    let behaviorState = structuredClone(initialBehaviorState);
    for (let index = 0; index < expectedLedger.entries.length; index += 1) {
      const entry = expectedLedger.entries[index], recorded = recovery.behaviorTrace.commands[index];
      if (!entry || !recorded || typeof entry.intent !== "object" || Array.isArray(entry.intent)) fail();
      const before = exportNpcAuthoritySession(created.session);
      if (!before.ok) fail();
      const intentJson = canonicalizeJsonValue(entry.intent);
      const selection = selectEligibleNpcBehaviorCommand({ prepared: preparedBehavior,
        runtimeSnapshot: before.runtimeSnapshot, runtimeInspection: before.inspection,
        worldEventLedgerJson: before.canonicalWorldEventLedgerJson, behaviorState,
        actorEntityId: recorded.actorEntityId, expectedIntentId: recorded.intentId,
        expectedNpcIntentSha256: hash(intentJson) });
      if (!selection?.ok || selection.status !== "command" || selection.command.npcIntentJson !== intentJson ||
          IDENTITIES.some((field) => selection.command[field] !== recorded[field])) fail();
      const submitted = submitNpcAuthorityIntent(created.session, selection.command.npcIntentJson);
      if (!submitted?.ok) fail();
      const actualLedger = canonical(submitted.canonicalWorldEventLedgerJson);
      const actualEntry = actualLedger.entries[index];
      const result = canonical(submitted.canonicalAdjudicationResultJson);
      if (!actualEntry || !same(actualEntry, entry) || result.decision?.status !== recorded.state ||
          recorded.revisionStarted !== index || recorded.revisionFinished !== index + 1 ||
          recorded.mirrorEvidence?.beforeSnapshotSha256 !== result.beforeSnapshotSha256 ||
          recorded.mirrorEvidence?.afterSnapshotSha256 !== result.afterSnapshotSha256) fail();
      behaviorState = selection.nextBehaviorState;
    }
    const verified = verifyNpcAuthoritySession(created.session);
    const authority = exportNpcAuthoritySession(created.session);
    if (!verified?.ok || !authority?.ok || authority.canonicalWorldEventLedgerJson !== expectedLedgerJson ||
        verified.canonicalWorldEventLedgerJson !== expectedLedgerJson) fail();
    return frozen({ authority: frozen({ ...authority, session: created.session }),
      behaviorState: structuredClone(behaviorState), commands: structuredClone(recovery.behaviorTrace.commands) });
  } catch (error) {
    if (error?.message === CODE) throw error;
    fail();
  }
}
