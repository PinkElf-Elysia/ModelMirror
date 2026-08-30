import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  adjudicateNpcIntent,
  createNpcAuthorityIncrementalState,
  createNpcAuthorityTimeline,
  exportNpcAuthorityIncrementalState,
  hashCanonicalValue,
  prepareNpcAuthority,
  replayWorldEventLedger,
  submitNpcAuthorityIncrementalIntent,
  verifyNpcAuthorityIncrementalState,
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

test("R20 incremental path remains byte-equivalent to R19 across accepted, rejected and fail-closed cases for 20 runs", async () => {
  const prepared = await preparedAuthority();
  const overflowSource = {
    format: "matrix-oasis.authoring-game-pack",
    formatVersion: "0.1.0",
    id: "authority-overflow-differential",
    contentVersion: "1",
    language: "en",
    title: "Overflow differential",
    summary: "Exercises the deterministic integer overflow rejection.",
    entryNodeId: "node-overflow",
    entities: [{ id: "actor-unit", label: "Actor" }],
    variables: [{ id: "counter-value", type: "integer", initial: Number.MAX_SAFE_INTEGER }],
    cues: [],
    nodes: [{
      id: "node-overflow",
      title: "Overflow",
      entityIds: ["actor-unit"],
      entryCueIds: [],
      actions: [{
        id: "action-overflow",
        label: "Overflow",
        effects: [{ op: "add", variableId: "counter-value", value: 1 }],
        target: { kind: "ending", id: "ending-overflow" },
      }],
    }],
    endings: [{ id: "ending-overflow", title: "Done", cueIds: [] }],
  };
  const overflowCompiled = await compileAuthoringGamePackJson(canonicalizeJsonValue(overflowSource));
  assert.equal(overflowCompiled.ok, true, JSON.stringify(overflowCompiled.validationReport?.diagnostics));
  const overflowPolicy = canonicalizeJsonValue({
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "authority-overflow-policy",
    contentVersion: "1",
    runtime: {
      format: overflowCompiled.runtimePack.format,
      formatVersion: overflowCompiled.runtimePack.formatVersion,
      id: overflowCompiled.runtimePack.source.id,
      contentVersion: overflowCompiled.runtimePack.source.contentVersion,
      sourceSha256: `sha256:${overflowCompiled.runtimePack.source.canonicalSha256}`,
      artifactSha256: `sha256:${overflowCompiled.receipt.artifact.sha256}`,
      receiptSha256: hashCanonicalValue(overflowCompiled.receipt),
    },
    actorGrants: [{ actorEntityId: "actor-unit", grants: [{ nodeId: "node-overflow", actionId: "action-overflow" }] }],
  });
  const overflowPreparedResult = await prepareNpcAuthority({
    runtimeGamePackJson: overflowCompiled.canonicalJson,
    runtimeReceiptJson: canonicalizeJsonValue(overflowCompiled.receipt),
    policyJson: overflowPolicy,
  });
  assert.equal(overflowPreparedResult.ok, true, JSON.stringify(overflowPreparedResult.diagnostics));

  let baselineTranscript;
  for (let run = 0; run < 20; run += 1) {
    const transcript = [];
    const compareSubmission = ({ authority, incremental, state, npcIntentJson, expectedReason, expectedDiagnostic, expectedReplayed }) => {
      const beforeIncremental = exportNpcAuthorityIncrementalState(incremental.state);
      const legacyStateBytes = canonicalizeJsonValue({ runtimeSnapshot: state.snapshot, worldEventLedgerJson: state.ledgerJson });
      const legacy = adjudicateNpcIntent({ prepared: authority, runtimeSnapshot: state.snapshot, worldEventLedgerJson: state.ledgerJson, npcIntentJson });
      const next = submitNpcAuthorityIncrementalIntent({ state: incremental.state, npcIntentJson });
      assert.equal(canonicalizeJsonValue(next.ok ? { ok: true } : next), canonicalizeJsonValue(legacy.ok ? { ok: true } : legacy));
      if (!legacy.ok) {
        if (expectedDiagnostic) assert.equal(legacy.diagnostics[0].code, expectedDiagnostic);
        assert.equal(canonicalizeJsonValue(exportNpcAuthorityIncrementalState(incremental.state)), canonicalizeJsonValue(beforeIncremental));
        assert.equal(canonicalizeJsonValue({ runtimeSnapshot: state.snapshot, worldEventLedgerJson: state.ledgerJson }), legacyStateBytes);
        transcript.push(canonicalizeJsonValue(legacy));
        return state;
      }
      assert.equal(next.canonicalAdjudicationResultJson, legacy.canonicalAdjudicationResultJson);
      assert.equal(next.canonicalWorldEventLedgerJson, legacy.canonicalWorldEventLedgerJson);
      assert.equal(canonicalizeJsonValue(next.runtimeSnapshot), canonicalizeJsonValue(legacy.runtimeSnapshot));
      assert.equal(next.replayed, legacy.replayed);
      if (expectedReplayed !== undefined) assert.equal(legacy.replayed, expectedReplayed);
      const decision = JSON.parse(legacy.canonicalAdjudicationResultJson).decision;
      if (expectedReason) assert.equal(decision.reason, expectedReason);
      if (decision.status === "rejected") assert.equal(canonicalizeJsonValue(legacy.runtimeSnapshot), canonicalizeJsonValue(state.snapshot));
      const exported = exportNpcAuthorityIncrementalState(incremental.state);
      assert.equal(exported.canonicalWorldEventLedgerJson, legacy.canonicalWorldEventLedgerJson);
      assert.equal(canonicalizeJsonValue(exported.runtimeSnapshot), canonicalizeJsonValue(legacy.runtimeSnapshot));
      transcript.push(legacy.canonicalAdjudicationResultJson, legacy.canonicalWorldEventLedgerJson, canonicalizeJsonValue(legacy.runtimeSnapshot));
      return { snapshot: legacy.runtimeSnapshot, ledgerJson: legacy.canonicalWorldEventLedgerJson };
    };

    const created = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-incremental-differential", stepLimit: 16 });
    const incremental = createNpcAuthorityIncrementalState({ prepared, worldEventLedgerJson: created.canonicalWorldEventLedgerJson });
    assert.equal(incremental.ok, true);
    let state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: nextIntent("intent-diff-unauthorized", "control-unit", "node-start", "action-initialize", state), expectedReason: "NPC_INTENT_ACTOR_UNAUTHORIZED" });
    const acceptedIntent = nextIntent("intent-diff-initialize", "actor-unit", "node-start", "action-initialize", state);
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: acceptedIntent, expectedReason: "NPC_INTENT_ACCEPTED", expectedReplayed: false });
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: acceptedIntent, expectedReason: "NPC_INTENT_ACCEPTED", expectedReplayed: true });

    const collision = JSON.parse(acceptedIntent);collision.actionId = "different-action";
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: canonicalizeJsonValue(collision), expectedDiagnostic: "NPC_INTENT_ID_COLLISION" });
    const staleRevision = JSON.parse(nextIntent("intent-diff-stale-revision", "actor-unit", "node-check", "action-check", state));staleRevision.observed.revision -= 1;
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: canonicalizeJsonValue(staleRevision), expectedDiagnostic: "NPC_INTENT_STALE_REVISION" });
    const staleHead = JSON.parse(nextIntent("intent-diff-stale-head", "actor-unit", "node-check", "action-check", state));staleHead.observed.headSha256 = `sha256:${"0".repeat(64)}`;
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: canonicalizeJsonValue(staleHead), expectedDiagnostic: "NPC_INTENT_STALE_HEAD" });
    const staleSnapshot = JSON.parse(nextIntent("intent-diff-stale-snapshot", "actor-unit", "node-check", "action-check", state));staleSnapshot.observed.runtimeSnapshotSha256 = `sha256:${"0".repeat(64)}`;
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: canonicalizeJsonValue(staleSnapshot), expectedDiagnostic: "NPC_INTENT_STALE_SNAPSHOT" });

    for (const [id, nodeId, actionId] of [
      ["check", "node-check", "action-check"],
      ["adjust", "node-adjust", "action-adjust"],
      ["review", "node-review", "action-review"],
      ["complete", "node-complete", "action-complete"],
    ]) state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: nextIntent(`intent-diff-${id}`, "actor-unit", nodeId, actionId, state), expectedReason: "NPC_INTENT_ACCEPTED" });
    state = compareSubmission({ authority: prepared, incremental, state, npcIntentJson: nextIntent("intent-diff-ended", "actor-unit", "node-complete", "action-complete", state), expectedReason: "NPC_INTENT_SESSION_ENDED" });
    const verified = verifyNpcAuthorityIncrementalState(incremental.state);
    assert.equal(verified.ok, true, JSON.stringify(verified.diagnostics));
    assert.equal(canonicalizeJsonValue(verified.runtimeSnapshot), canonicalizeJsonValue(state.snapshot));

    const limited = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-incremental-step-limit", stepLimit: 1 });
    const limitedIncremental = createNpcAuthorityIncrementalState({ prepared, worldEventLedgerJson: limited.canonicalWorldEventLedgerJson });
    let limitedState = { snapshot: limited.runtimeSnapshot, ledgerJson: limited.canonicalWorldEventLedgerJson };
    limitedState = compareSubmission({ authority: prepared, incremental: limitedIncremental, state: limitedState, npcIntentJson: nextIntent("intent-diff-limited-accept", "actor-unit", "node-start", "action-initialize", limitedState), expectedReason: "NPC_INTENT_ACCEPTED" });
    limitedState = compareSubmission({ authority: prepared, incremental: limitedIncremental, state: limitedState, npcIntentJson: nextIntent("intent-diff-step-limit", "actor-unit", "node-check", "action-check", limitedState), expectedReason: "NPC_INTENT_STEP_LIMIT" });

    const overflowCreated = createNpcAuthorityTimeline(overflowPreparedResult.prepared, { timelineId: "timeline-incremental-overflow", stepLimit: 1 });
    const overflowIncremental = createNpcAuthorityIncrementalState({ prepared: overflowPreparedResult.prepared, worldEventLedgerJson: overflowCreated.canonicalWorldEventLedgerJson });
    let overflowState = { snapshot: overflowCreated.runtimeSnapshot, ledgerJson: overflowCreated.canonicalWorldEventLedgerJson };
    overflowState = compareSubmission({ authority: overflowPreparedResult.prepared, incremental: overflowIncremental, state: overflowState, npcIntentJson: nextIntent("intent-diff-overflow", "actor-unit", "node-overflow", "action-overflow", overflowState), expectedReason: "NPC_INTENT_INTEGER_OVERFLOW" });

    const transcriptBytes = canonicalizeJsonValue(transcript);
    if (baselineTranscript === undefined) baselineTranscript = transcriptBytes;
    else assert.equal(transcriptBytes, baselineTranscript);
  }
});

test("incremental state is unchanged when result capture rejects the verified candidate", async () => {
  const cues = Array.from({ length: 256 }, (_, index) => ({
    id: `cue-${String(index).padStart(3, "0")}`,
    channel: "visual",
    intent: "x".repeat(4096),
  }));
  const source = {
    format: "matrix-oasis.authoring-game-pack",
    formatVersion: "0.1.0",
    id: "capture-limit-fixture",
    contentVersion: "1",
    language: "en",
    title: "Capture Limit Fixture",
    summary: "Forces result capture to reject an otherwise replayable candidate.",
    entryNodeId: "node-start",
    entities: [{ id: "actor-unit", label: "Actor" }],
    variables: [],
    cues,
    nodes: [
      {
        id: "node-start",
        title: "Start",
        entityIds: ["actor-unit"],
        entryCueIds: [],
        actions: [{ id: "action-advance", label: "Advance", effects: [], target: { kind: "node", id: "node-target" } }],
      },
      {
        id: "node-target",
        title: "Target",
        entityIds: ["actor-unit"],
        entryCueIds: cues.map(({ id }) => id),
        actions: [{ id: "action-finish", label: "Finish", effects: [], target: { kind: "ending", id: "ending-done" } }],
      },
    ],
    endings: [{ id: "ending-done", title: "Done", cueIds: [] }],
  };
  const compiledLargeResult = await compileAuthoringGamePackJson(canonicalizeJsonValue(source));
  assert.equal(compiledLargeResult.ok, true, JSON.stringify(compiledLargeResult.validationReport?.diagnostics));
  const largePolicy = {
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "capture-limit-authority",
    contentVersion: "1.0.0",
    runtime: {
      format: compiledLargeResult.runtimePack.format,
      formatVersion: compiledLargeResult.runtimePack.formatVersion,
      id: compiledLargeResult.runtimePack.source.id,
      contentVersion: compiledLargeResult.runtimePack.source.contentVersion,
      sourceSha256: `sha256:${compiledLargeResult.runtimePack.source.canonicalSha256}`,
      artifactSha256: `sha256:${compiledLargeResult.receipt.artifact.sha256}`,
      receiptSha256: hashCanonicalValue(compiledLargeResult.receipt),
    },
    actorGrants: [{ actorEntityId: "actor-unit", grants: [{ nodeId: "node-start", actionId: "action-advance" }] }],
  };
  const preparedResult = await prepareNpcAuthority({
    runtimeGamePackJson: compiledLargeResult.canonicalJson,
    runtimeReceiptJson: canonicalizeJsonValue(compiledLargeResult.receipt),
    policyJson: canonicalizeJsonValue(largePolicy),
  });
  assert.equal(preparedResult.ok, true, JSON.stringify(preparedResult.diagnostics));
  const created = createNpcAuthorityTimeline(preparedResult.prepared, { timelineId: "capture-limit", stepLimit: 2 });
  assert.equal(created.ok, true, JSON.stringify(created.diagnostics));
  const incremental = createNpcAuthorityIncrementalState({ prepared: preparedResult.prepared, worldEventLedgerJson: created.canonicalWorldEventLedgerJson });
  assert.equal(incremental.ok, true, JSON.stringify(incremental.diagnostics));
  const before = exportNpcAuthorityIncrementalState(incremental.state);
  const intent = nextIntent("intent-capture-limit", "actor-unit", "node-start", "action-advance", {
    snapshot: created.runtimeSnapshot,
    ledgerJson: created.canonicalWorldEventLedgerJson,
  });

  const rejected = submitNpcAuthorityIncrementalIntent({ state: incremental.state, npcIntentJson: intent });

  assert.equal(rejected.ok, false);
  assert.equal(rejected.diagnostics[0].code, "NPC_ADJUDICATION_RESULT_JSON_SIZE_EXCEEDED");
  assert.deepEqual(exportNpcAuthorityIncrementalState(incremental.state), before);
  const verified = verifyNpcAuthorityIncrementalState(incremental.state);
  assert.equal(verified.ok, true, JSON.stringify(verified.diagnostics));
  assert.equal(JSON.parse(verified.canonicalWorldEventLedgerReplayReportJson).throughRevision, 0);
});
