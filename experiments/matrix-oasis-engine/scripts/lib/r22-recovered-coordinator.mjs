import { createNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { selectEligibleNpcBehaviorCommand } from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { createR20Coordinator, exportR20Coordinator, handleR20CoordinatorRequestAsync } from "./r20-host-core.mjs";
import { rebuildR22RecoveredBehavior } from "./r22-recovered-behavior.mjs";

const CODE = "R22_RECOVERY_COORDINATOR_INVALID";
const SHA = /^sha256:[0-9a-f]{64}$/u;
const IDENTITY = ["sequence", "actorEntityId", "ruleIndex", "intentId", "nodeId", "actionId"];
function fail() { throw new Error(CODE); }
function same(left, right) { return canonicalizeJsonValue(left) === canonicalizeJsonValue(right); }
function request(token, method, url, body = null) {
  return { remoteAddress: "127.0.0.1", method, url,
    headers: { authorization: `Bearer ${token}`, ...(method === "POST" ? { "content-type": "application/json" } : {}) },
    ...(method === "POST" ? { body: canonicalizeJsonValue(body) } : {}) };
}
async function call(coordinator, token, method, url, body = null) {
  const response = await handleR20CoordinatorRequestAsync(coordinator, request(token, method, url, body));
  if (response?.statusCode !== 200) fail();
  try { return JSON.parse(response.body); } catch { fail(); }
}

export async function rebuildR22RecoveredCoordinator(input) {
  try {
    const { source, preparedBehavior, initialBehaviorState, initialTimelineId, auditedTimelineIds, recovery,
      sessionToken, commandSelector, onCommit = null, onReset = null, onVerify = null } = input ?? {};
    if (!source || !preparedBehavior || !initialBehaviorState || typeof initialTimelineId !== "string" ||
        !Array.isArray(auditedTimelineIds) || auditedTimelineIds.length < 1 ||
        typeof sessionToken !== "string" || sessionToken.length < 32 || typeof commandSelector !== "function" ||
        ![onCommit, onReset, onVerify].every((hook) => hook === null || typeof hook === "function") ||
        !Array.isArray(recovery?.behaviorTrace?.commands)) fail();
    const ledger = JSON.parse(recovery.canonicalWorldEventLedgerJson);
    if (auditedTimelineIds.some((id) => typeof id !== "string") || new Set(auditedTimelineIds).size !== auditedTimelineIds.length ||
        !auditedTimelineIds.includes(initialTimelineId) || !auditedTimelineIds.includes(ledger.timeline?.id) ||
        !SHA.test(recovery.behaviorTrace.entityBindingSha256 ?? "")) fail();

    const expected = await rebuildR22RecoveredBehavior({ source, recovery, preparedBehavior, initialBehaviorState });
    const created = await createNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson,
      runtimeReceiptJson: source.runtimeReceiptJson, policyJson: source.authorityPolicyJson,
      timelineId: initialTimelineId, stepLimit: ledger.timeline.stepLimit });
    if (!created?.ok) fail();

    let recovering = true;
    let replayIndex = 0;
    const recordedCommands = recovery.behaviorTrace.commands;
    const selector = (selectionInput) => {
      if (!recovering) return commandSelector(selectionInput);
      const recorded = recordedCommands[replayIndex], entry = ledger.entries?.[replayIndex];
      if (!recorded || !entry?.intent) fail();
      const intentJson = canonicalizeJsonValue(entry.intent);
      const selected = selectEligibleNpcBehaviorCommand({ ...selectionInput, actorEntityId: recorded.actorEntityId,
        expectedIntentId: recorded.intentId, expectedNpcIntentSha256: hashCanonicalValue(entry.intent) });
      if (!selected?.ok || selected.status !== "command" || selected.command.npcIntentJson !== intentJson ||
          IDENTITY.some((field) => selected.command[field] !== recorded[field])) fail();
      return selected;
    };
    const gated = (hook) => async (...args) => { if (!recovering && hook) return hook(...args); };
    const coordinator = createR20Coordinator({ authoritySession: created.session, preparedBehavior, initialBehaviorState,
      entityBindingSha256: recovery.behaviorTrace.entityBindingSha256, sessionToken, commandSelector: selector,
      onCommit: gated(onCommit), onReset: gated(onReset), onVerify: gated(onVerify) });
    if (!coordinator) fail();

    const remainingTimelineIds = new Set(auditedTimelineIds);
    remainingTimelineIds.delete(initialTimelineId);
    let currentTimelineId = initialTimelineId;
    while (currentTimelineId !== ledger.timeline.id) {
      const reset = await call(coordinator, sessionToken, "POST", "/v1/reset", {});
      if (reset.status !== "reset" || !remainingTimelineIds.delete(reset.timelineId)) fail();
      currentTimelineId = reset.timelineId;
    }
    if (remainingTimelineIds.size !== 0) fail();
    for (const recorded of recordedCommands) {
      const selected = await call(coordinator, sessionToken, "GET", "/v1/command");
      if (selected.status !== "command" || IDENTITY.some((field) => selected.command?.[field] !== recorded[field])) fail();
      const arrived = await call(coordinator, sessionToken, "POST", "/v1/arrived", { sequence: recorded.sequence, ...recorded.arrivalEvidence });
      if (arrived.status !== "adjudicated" || arrived.decision !== recorded.state ||
          arrived.beforeSnapshotSha256 !== recorded.mirrorEvidence?.beforeSnapshotSha256 ||
          arrived.afterSnapshotSha256 !== recorded.mirrorEvidence?.afterSnapshotSha256) fail();
      const mirrored = await call(coordinator, sessionToken, "POST", "/v1/mirror", { sequence: recorded.sequence,
        beforeSnapshotSha256: recorded.mirrorEvidence.beforeSnapshotSha256,
        afterSnapshotSha256: recorded.mirrorEvidence.afterSnapshotSha256 });
      if (mirrored.status !== "committed") fail();
      replayIndex += 1;
    }
    const actual = exportR20Coordinator(coordinator);
    if (actual.authority.canonicalWorldEventLedgerJson !== expected.authority.canonicalWorldEventLedgerJson ||
        !same(actual.behaviorState, expected.behaviorState) || !same(actual.commands, expected.commands) ||
        actual.resetCount !== auditedTimelineIds.length - 1) fail();
    recovering = false;
    return coordinator;
  } catch (error) {
    if (error?.message === CODE) throw error;
    fail();
  }
}
