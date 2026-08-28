import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  adjudicateNpcIntent,
  createNpcAuthorityTimeline,
  hashCanonicalValue,
  prepareNpcAuthority,
  replayWorldEventLedger,
} from "../src/index.mjs";

const authoringText = await readFile(new URL("../../../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8");
const compiled = await compileAuthoringGamePackJson(authoringText);
assert.equal(compiled.ok, true);
const runtimeGamePackJson = compiled.canonicalJson;
const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
const allGrants = compiled.runtimePack.nodes.flatMap((node) => node.actions.map((action) => ({ nodeId: node.id, actionId: action.id })));

function policyValue() {
  return {
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "mechanics-authority",
    contentVersion: "1.0.0",
    runtime: {
      format: compiled.runtimePack.format,
      formatVersion: compiled.runtimePack.formatVersion,
      id: compiled.runtimePack.source.id,
      contentVersion: compiled.runtimePack.source.contentVersion,
      sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
      artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
      receiptSha256: hashCanonicalValue(compiled.receipt),
    },
    actorGrants: [{ actorEntityId: "actor-unit", grants: allGrants.map((grant) => ({ ...grant })) }],
  };
}

async function preparedAuthority(policy = policyValue()) {
  const result = await prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson, policyJson: canonicalizeJsonValue(policy) });
  assert.equal(result.ok, true, JSON.stringify(result.diagnostics));
  return result.prepared;
}

function intentFor({ id, actorEntityId = "actor-unit", nodeId, actionId, snapshot, ledger }) {
  return canonicalizeJsonValue({
    format: "matrix-oasis.npc-intent",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id,
    actorEntityId,
    timelineId: ledger.timeline.id,
    nodeId,
    actionId,
    observed: { revision: ledger.revision, headSha256: ledger.headSha256, runtimeSnapshotSha256: hashCanonicalValue(snapshot) },
  });
}

function nextIntent(id, actorEntityId, nodeId, actionId, state) {
  return intentFor({ id, actorEntityId, nodeId, actionId, snapshot: state.snapshot, ledger: JSON.parse(state.ledgerJson) });
}

function apply(prepared, state, npcIntentJson) {
  const result = adjudicateNpcIntent({ prepared, runtimeSnapshot: state.snapshot, worldEventLedgerJson: state.ledgerJson, npcIntentJson });
  assert.equal(result.ok, true, JSON.stringify(result.diagnostics));
  return { result, snapshot: result.runtimeSnapshot, ledgerJson: result.canonicalWorldEventLedgerJson };
}

test("prepare binds policy to the exact Runtime/Receipt identity and references", async () => {
  const valid = await prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson, policyJson: canonicalizeJsonValue(policyValue()) });
  assert.equal(valid.ok, true);
  const identityDrift = policyValue();
  identityDrift.runtime.receiptSha256 = `sha256:${"0".repeat(64)}`;
  const mismatch = await prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson, policyJson: canonicalizeJsonValue(identityDrift) });
  assert.equal(mismatch.ok, false);
  assert.equal(mismatch.diagnostics[0].code, "NPC_AUTHORITY_POLICY_RUNTIME_IDENTITY_MISMATCH");
  const missingActor = policyValue();
  missingActor.actorGrants[0].actorEntityId = "missing-actor";
  assert((await prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson, policyJson: canonicalizeJsonValue(missingActor) })).diagnostics.some((value) => value.code === "NPC_AUTHORITY_POLICY_ACTOR_NOT_FOUND"));
  const missingAction = policyValue();
  missingAction.actorGrants[0].grants[0].actionId = "missing-action";
  assert((await prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson, policyJson: canonicalizeJsonValue(missingAction) })).diagnostics.some((value) => value.code === "NPC_AUTHORITY_POLICY_ACTION_NOT_FOUND"));
});

test("authorized intent is executed only by the frozen Runtime simulator", async () => {
  const prepared = await preparedAuthority();
  const created = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-one", stepLimit: 16 });
  assert.equal(created.ok, true);
  const state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  const advanced = apply(prepared, state, nextIntent("intent-initialize", "actor-unit", "node-start", "action-initialize", state));
  const result = JSON.parse(advanced.result.canonicalAdjudicationResultJson);
  assert.equal(result.decision.status, "accepted");
  assert.equal(result.transition.to.id, "node-check");
  assert.equal(advanced.snapshot.stepCount, 1);
  assert.equal(Object.isFrozen(advanced.result), true);
});

test("Action entityIds never elevate an interaction target into an actor", async () => {
  const prepared = await preparedAuthority();
  const created = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-one" });
  const state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  const rejected = apply(prepared, state, nextIntent("intent-target-actor", "control-unit", "node-start", "action-initialize", state));
  const result = JSON.parse(rejected.result.canonicalAdjudicationResultJson);
  assert.deepEqual(result.decision, { reason: "NPC_INTENT_ACTOR_UNAUTHORIZED", status: "rejected" });
  assert.deepEqual(rejected.snapshot, state.snapshot);
});

test("rejections cover missing actor/node/action, node mismatch and unavailable action without state mutation", async () => {
  const prepared = await preparedAuthority();
  const created = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-one", stepLimit: 16 });
  let state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  const cases = [
    ["missing-actor", "missing-actor", "node-start", "action-initialize", "NPC_INTENT_ACTOR_NOT_FOUND"],
    ["missing-node", "actor-unit", "missing-node", "action-initialize", "NPC_INTENT_NODE_NOT_FOUND"],
    ["missing-action", "actor-unit", "node-start", "missing-action", "NPC_INTENT_ACTION_NOT_FOUND"],
  ];
  for (const [id, actor, node, action, reason] of cases) {
    const before = state.snapshot;
    const next = apply(prepared, state, nextIntent(`intent-${id}`, actor, node, action, state));
    assert.equal(JSON.parse(next.result.canonicalAdjudicationResultJson).decision.reason, reason);
    assert.deepEqual(next.snapshot, before);
    state = next;
  }
  state = apply(prepared, state, nextIntent("intent-initialize", "actor-unit", "node-start", "action-initialize", state));
  let rejected = apply(prepared, state, nextIntent("intent-wrong-node", "actor-unit", "node-start", "action-initialize", state));
  assert.equal(JSON.parse(rejected.result.canonicalAdjudicationResultJson).decision.reason, "NPC_INTENT_NODE_MISMATCH");
  state = rejected;
  rejected = apply(prepared, state, nextIntent("intent-unavailable", "actor-unit", "node-check", "action-check-hold", state));
  assert.equal(JSON.parse(rejected.result.canonicalAdjudicationResultJson).decision.reason, "NPC_INTENT_ACTION_UNAVAILABLE");
  assert.equal(rejected.snapshot.stepCount, 1);
});

test("stale writes and intent id collisions fail without returning a Ledger candidate", async () => {
  const prepared = await preparedAuthority();
  const created = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-one" });
  const state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  const acceptedIntent = nextIntent("intent-one", "actor-unit", "node-start", "action-initialize", state);
  const accepted = apply(prepared, state, acceptedIntent);
  const stale = adjudicateNpcIntent({ prepared, runtimeSnapshot: accepted.snapshot, worldEventLedgerJson: accepted.ledgerJson, npcIntentJson: nextIntent("intent-stale", "actor-unit", "node-check", "action-check", state) });
  assert.equal(stale.ok, false);
  assert.equal(stale.diagnostics[0].code, "NPC_INTENT_STALE_REVISION");
  assert.equal("canonicalWorldEventLedgerJson" in stale, false);
  const collisionValue = JSON.parse(acceptedIntent);
  collisionValue.actionId = "different-action";
  const collision = adjudicateNpcIntent({ prepared, runtimeSnapshot: accepted.snapshot, worldEventLedgerJson: accepted.ledgerJson, npcIntentJson: canonicalizeJsonValue(collisionValue) });
  assert.equal(collision.ok, false);
  assert.equal(collision.diagnostics[0].code, "NPC_INTENT_ID_COLLISION");
  assert.equal("canonicalWorldEventLedgerJson" in collision, false);
});

test("exact retry returns the historical verdict and does not append", async () => {
  const prepared = await preparedAuthority();
  const created = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-one" });
  const state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  const intent = nextIntent("intent-retry", "actor-unit", "node-start", "action-initialize", state);
  const accepted = apply(prepared, state, intent);
  const replayed = adjudicateNpcIntent({ prepared, runtimeSnapshot: state.snapshot, worldEventLedgerJson: accepted.ledgerJson, npcIntentJson: intent });
  assert.equal(replayed.ok, true);
  assert.equal(replayed.replayed, true);
  assert.equal(replayed.canonicalWorldEventLedgerJson, accepted.ledgerJson);
  assert.equal(JSON.parse(replayed.canonicalAdjudicationResultJson).replayed, true);
  assert.deepEqual(replayed.runtimeSnapshot, accepted.snapshot);
});

test("public authority operations reject absent arguments without leaking argument exceptions", async () => {
  const prepared = await prepareNpcAuthority();
  assert.equal(prepared.ok, false);
  assert.equal(prepared.diagnostics[0].code, "NPC_AUTHORITY_POLICY_JSON_INPUT_TYPE");
  const replayed = replayWorldEventLedger();
  assert.equal(replayed.ok, false);
  assert.equal(replayed.diagnostics[0].code, "NPC_AUTHORITY_PREPARED_INVALID");
  const adjudicated = adjudicateNpcIntent();
  assert.equal(adjudicated.ok, false);
  assert.equal(adjudicated.diagnostics[0].code, "NPC_AUTHORITY_PREPARED_INVALID");
  const timeline = createNpcAuthorityTimeline({}, null);
  assert.equal(timeline.ok, false);
  assert.equal(timeline.diagnostics[0].code, "NPC_AUTHORITY_PREPARED_INVALID");
});

test("hostile accessors collapse to the static operational error without leaking values", async () => {
  const prepared = await preparedAuthority();
  const created = createNpcAuthorityTimeline(prepared, { timelineId: "hostile-accessor" });
  const state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  const npcIntentJson = nextIntent("intent-hostile", "actor-unit", "node-start", "action-initialize", state);
  const hostileSnapshot = new Proxy({}, {
    ownKeys() { throw new Error("secret-host-path"); },
  });
  assert.throws(
    () => adjudicateNpcIntent({ prepared, runtimeSnapshot: hostileSnapshot, worldEventLedgerJson: state.ledgerJson, npcIntentJson }),
    (error) => error?.code === "NPC_AUTHORITY_INTERNAL_ERROR" && error.message === "NPC_AUTHORITY_INTERNAL_ERROR" && !String(error.stack).includes("secret-host-path"),
  );
});

test("mixed accepted/rejected history rebuilds the identical final Runtime state", async () => {
  const prepared = await preparedAuthority();
  const created = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-one", stepLimit: 16 });
  let state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  state = apply(prepared, state, nextIntent("intent-unauthorized", "control-unit", "node-start", "action-initialize", state));
  const route = [
    ["initialize", "node-start", "action-initialize"],
    ["check", "node-check", "action-check"],
    ["adjust", "node-adjust", "action-adjust"],
    ["review", "node-review", "action-review"],
    ["complete", "node-complete", "action-complete"],
  ];
  for (const [id, node, action] of route) state = apply(prepared, state, nextIntent(`intent-${id}`, "actor-unit", node, action, state));
  assert.equal(state.snapshot.status, "ended");
  const replay = replayWorldEventLedger({ prepared, worldEventLedgerJson: state.ledgerJson });
  assert.equal(replay.ok, true, JSON.stringify(replay.diagnostics));
  assert.deepEqual(replay.runtimeSnapshot, state.snapshot);
  const report = JSON.parse(replay.canonicalWorldEventLedgerReplayReportJson);
  assert.equal(report.acceptedEntries, 5);
  assert.equal(report.rejectedEntries, 1);
});

test("step-limit and ended intents are deterministic rejection entries", async () => {
  const prepared = await preparedAuthority();
  const limited = createNpcAuthorityTimeline(prepared, { timelineId: "limited", stepLimit: 1 });
  let state = { snapshot: limited.runtimeSnapshot, ledgerJson: limited.canonicalWorldEventLedgerJson };
  state = apply(prepared, state, nextIntent("intent-limited-initialize", "actor-unit", "node-start", "action-initialize", state));
  const stepLimit = apply(prepared, state, nextIntent("intent-step-limit", "actor-unit", "node-check", "action-check", state));
  assert.equal(JSON.parse(stepLimit.result.canonicalAdjudicationResultJson).decision.reason, "NPC_INTENT_STEP_LIMIT");

  const normal = createNpcAuthorityTimeline(prepared, { timelineId: "ended", stepLimit: 16 });
  state = { snapshot: normal.runtimeSnapshot, ledgerJson: normal.canonicalWorldEventLedgerJson };
  for (const [id, node, action] of [["i", "node-start", "action-initialize"], ["c", "node-check", "action-check"], ["a", "node-adjust", "action-adjust"], ["r", "node-review", "action-review"], ["e", "node-complete", "action-complete"]]) {
    state = apply(prepared, state, nextIntent(`intent-${id}`, "actor-unit", node, action, state));
  }
  const ended = apply(prepared, state, nextIntent("intent-after-ending", "actor-unit", "node-complete", "action-complete", state));
  assert.equal(JSON.parse(ended.result.canonicalAdjudicationResultJson).decision.reason, "NPC_INTENT_SESSION_ENDED");
});
