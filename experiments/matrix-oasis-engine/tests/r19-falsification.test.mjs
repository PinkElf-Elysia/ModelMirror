import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validateWorldEventLedgerJson } from "@matrix-oasis/npc-authority-contracts";
import {
  adjudicateNpcIntent,
  createDerivedProjectionManifest,
  createNpcAuthorityTimeline,
  hashCanonicalValue,
  prepareNpcAuthority,
  replayWorldEventLedger,
} from "@matrix-oasis/npc-authority-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const fixtureText = await fs.readFile(new URL("./fixtures/r19/neutral-two-actor.authoring-game-pack.json", import.meta.url), "utf8");
const compiled = await compileAuthoringGamePackJson(fixtureText);
assert.equal(compiled.ok, true, JSON.stringify(compiled.diagnostics));
const runtimeGamePackJson = compiled.canonicalJson;
const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);

function policyValue(overrides = {}) {
  return {
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: overrides.id ?? "neutral-authority",
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
    actorGrants: overrides.actorGrants ?? [
      { actorEntityId: "actor-alpha", grants: [{ nodeId: "node-alpha", actionId: "action-pass" }] },
      { actorEntityId: "actor-beta", grants: [{ nodeId: "node-beta", actionId: "action-loop" }, { nodeId: "node-beta", actionId: "action-finish" }] },
    ],
  };
}

async function prepare(policy = policyValue()) {
  const result = await prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson, policyJson: canonicalizeJsonValue(policy) });
  assert.equal(result.ok, true, JSON.stringify(result.diagnostics));
  return result.prepared;
}

function intent(id, actorEntityId, nodeId, actionId, state) {
  const ledger = JSON.parse(state.ledgerJson);
  return canonicalizeJsonValue({
    format: "matrix-oasis.npc-intent",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id,
    actorEntityId,
    timelineId: ledger.timeline.id,
    nodeId,
    actionId,
    observed: {
      revision: ledger.revision,
      headSha256: ledger.headSha256,
      runtimeSnapshotSha256: hashCanonicalValue(state.snapshot),
    },
  });
}

function apply(prepared, state, npcIntentJson) {
  const result = adjudicateNpcIntent({ prepared, runtimeSnapshot: state.snapshot, worldEventLedgerJson: state.ledgerJson, npcIntentJson });
  assert.equal(result.ok, true, JSON.stringify(result.diagnostics));
  return { result, snapshot: result.runtimeSnapshot, ledgerJson: result.canonicalWorldEventLedgerJson };
}

async function completeTimeline(timelineId = "neutral-timeline") {
  const prepared = await prepare();
  const created = createNpcAuthorityTimeline(prepared, { timelineId, stepLimit: 16 });
  assert.equal(created.ok, true);
  let state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  state = apply(prepared, state, intent("intent-pass-one", "actor-alpha", "node-alpha", "action-pass", state));
  state = apply(prepared, state, intent("intent-forged", "actor-alpha", "node-beta", "action-loop", state));
  state = apply(prepared, state, intent("intent-loop", "actor-beta", "node-beta", "action-loop", state));
  state = apply(prepared, state, intent("intent-pass-two", "actor-alpha", "node-alpha", "action-pass", state));
  state = apply(prepared, state, intent("intent-finish", "actor-beta", "node-beta", "action-finish", state));
  return { prepared, state };
}

function rechain(ledger) {
  let previous = null;
  for (let index = 0; index < ledger.entries.length; index += 1) {
    const entry = ledger.entries[index];
    entry.revision = index + 1;
    entry.previousEntrySha256 = previous;
    entry.intent.observed.revision = index;
    entry.intent.observed.headSha256 = previous;
    const { entrySha256: _entrySha256, ...body } = entry;
    entry.entrySha256 = hashCanonicalValue(body);
    previous = entry.entrySha256;
  }
  ledger.revision = ledger.entries.length;
  ledger.headSha256 = previous;
  return canonicalizeJsonValue(ledger);
}

test("two actors remain exactly scoped while a loop and ending rebuild deterministically", async () => {
  const { prepared, state } = await completeTimeline();
  const ledger = JSON.parse(state.ledgerJson);
  assert.deepEqual(ledger.entries.map((entry) => entry.decision.status), ["accepted", "rejected", "accepted", "accepted", "accepted"]);
  assert.equal(ledger.entries[1].decision.reason, "NPC_INTENT_ACTOR_UNAUTHORIZED");
  assert.equal(state.snapshot.status, "ended");
  const replayed = replayWorldEventLedger({ prepared, worldEventLedgerJson: state.ledgerJson });
  assert.equal(replayed.ok, true, JSON.stringify(replayed.diagnostics));
  assert.deepEqual(replayed.runtimeSnapshot, state.snapshot);
  assert.equal(JSON.parse(replayed.canonicalWorldEventLedgerReplayReportJson).verifiedEntries, 5);
});

test("hash, ordering and transition attacks fail closed without partial state", async () => {
  const { prepared, state } = await completeTimeline("tamper-timeline");
  const mutations = [
    (ledger) => { ledger.headSha256 = `sha256:${"0".repeat(64)}`; },
    (ledger) => { ledger.entries.splice(1, 1); },
    (ledger) => { ledger.entries.splice(1, 0, structuredClone(ledger.entries[0])); },
    (ledger) => { [ledger.entries[0], ledger.entries[1]] = [ledger.entries[1], ledger.entries[0]]; },
    (ledger) => { ledger.entries[0].decision = { status: "rejected", reason: "NPC_INTENT_ACTOR_UNAUTHORIZED" }; },
  ];
  for (const mutate of mutations) {
    const ledger = JSON.parse(state.ledgerJson);
    mutate(ledger);
    const replayed = replayWorldEventLedger({ prepared, worldEventLedgerJson: canonicalizeJsonValue(ledger) });
    assert.equal(replayed.ok, false);
    assert.equal("runtimeSnapshot" in replayed, false);
    assert.equal("canonicalWorldEventLedgerJson" in replayed, false);
  }

  const semanticForgery = JSON.parse(state.ledgerJson);
  semanticForgery.entries[0].transition.to.id = "node-alpha";
  const forgedJson = rechain(semanticForgery);
  assert.equal(validateWorldEventLedgerJson(forgedJson).valid, true);
  const replayed = replayWorldEventLedger({ prepared, worldEventLedgerJson: forgedJson });
  assert.equal(replayed.ok, false);
  assert.equal(replayed.diagnostics[0].code, "WORLD_EVENT_LEDGER_REPLAY_TRANSITION_MISMATCH");
  assert.equal("runtimeSnapshot" in replayed, false);
});

test("Runtime, Receipt and Policy identity drift cannot reuse an authority history", async () => {
  const { state } = await completeTimeline("identity-timeline");
  const changedPrepared = await prepare(policyValue({ id: "changed-authority" }));
  const policyReplay = replayWorldEventLedger({ prepared: changedPrepared, worldEventLedgerJson: state.ledgerJson });
  assert.equal(policyReplay.ok, false);
  assert.equal(policyReplay.diagnostics[0].code, "WORLD_EVENT_LEDGER_AUTHORITY_MISMATCH");

  const changedAuthoring = JSON.parse(fixtureText);
  changedAuthoring.title = "Changed Neutral Fixture";
  const changedCompiled = await compileAuthoringGamePackJson(JSON.stringify(changedAuthoring));
  assert.equal(changedCompiled.ok, true);
  const runtimeDrift = await prepareNpcAuthority({
    runtimeGamePackJson: changedCompiled.canonicalJson,
    runtimeReceiptJson: canonicalizeJsonValue(changedCompiled.receipt),
    policyJson: canonicalizeJsonValue(policyValue()),
  });
  assert.equal(runtimeDrift.ok, false);
  assert.equal(runtimeDrift.diagnostics[0].code, "NPC_AUTHORITY_POLICY_RUNTIME_IDENTITY_MISMATCH");

  const receipt = structuredClone(compiled.receipt);
  receipt.artifact.byteLength += 1;
  const receiptDrift = await prepareNpcAuthority({ runtimeGamePackJson, runtimeReceiptJson: canonicalizeJsonValue(receipt), policyJson: canonicalizeJsonValue(policyValue()) });
  assert.equal(receiptDrift.ok, false);
});

test("Runtime integer overflow becomes a deterministic rejection and rolls back state", async () => {
  const source = JSON.parse(fixtureText);
  source.id = "neutral-overflow";
  source.nodes[0].actions[0].effects = [
    { op: "set", variableId: "cycle-count", value: Number.MAX_SAFE_INTEGER },
    { op: "add", variableId: "cycle-count", value: 1 },
  ];
  const overflowCompiled = await compileAuthoringGamePackJson(JSON.stringify(source));
  assert.equal(overflowCompiled.ok, true, JSON.stringify(overflowCompiled.diagnostics));
  const overflowPolicy = policyValue();
  overflowPolicy.id = "overflow-authority";
  overflowPolicy.runtime = {
    format: overflowCompiled.runtimePack.format,
    formatVersion: overflowCompiled.runtimePack.formatVersion,
    id: overflowCompiled.runtimePack.source.id,
    contentVersion: overflowCompiled.runtimePack.source.contentVersion,
    sourceSha256: `sha256:${overflowCompiled.runtimePack.source.canonicalSha256}`,
    artifactSha256: `sha256:${overflowCompiled.receipt.artifact.sha256}`,
    receiptSha256: hashCanonicalValue(overflowCompiled.receipt),
  };
  const preparedResult = await prepareNpcAuthority({
    runtimeGamePackJson: overflowCompiled.canonicalJson,
    runtimeReceiptJson: canonicalizeJsonValue(overflowCompiled.receipt),
    policyJson: canonicalizeJsonValue(overflowPolicy),
  });
  assert.equal(preparedResult.ok, true, JSON.stringify(preparedResult.diagnostics));
  const created = createNpcAuthorityTimeline(preparedResult.prepared, { timelineId: "overflow-timeline" });
  const before = created.runtimeSnapshot;
  const state = { snapshot: before, ledgerJson: created.canonicalWorldEventLedgerJson };
  const rejected = apply(preparedResult.prepared, state, intent("intent-overflow", "actor-alpha", "node-alpha", "action-pass", state));
  assert.deepEqual(rejected.snapshot, before);
  assert.equal(JSON.parse(rejected.result.canonicalAdjudicationResultJson).decision.reason, "NPC_INTENT_INTEGER_OVERFLOW");
  assert.equal(JSON.parse(rejected.ledgerJson).entries.length, 1);
});

test("independent timelines cannot exchange observations or ledgers", async () => {
  const prepared = await prepare();
  const first = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-first" });
  const second = createNpcAuthorityTimeline(prepared, { timelineId: "timeline-second" });
  const firstState = { snapshot: first.runtimeSnapshot, ledgerJson: first.canonicalWorldEventLedgerJson };
  const secondState = { snapshot: second.runtimeSnapshot, ledgerJson: second.canonicalWorldEventLedgerJson };
  const firstIntent = intent("intent-first", "actor-alpha", "node-alpha", "action-pass", firstState);
  const crossed = adjudicateNpcIntent({ prepared, runtimeSnapshot: secondState.snapshot, worldEventLedgerJson: secondState.ledgerJson, npcIntentJson: firstIntent });
  assert.equal(crossed.ok, false);
  assert.equal(crossed.diagnostics[0].code, "NPC_INTENT_TIMELINE_MISMATCH");
  assert.equal("canonicalWorldEventLedgerJson" in crossed, false);
  const firstAdvanced = apply(prepared, firstState, firstIntent);
  assert.notEqual(JSON.parse(firstAdvanced.ledgerJson).headSha256, JSON.parse(secondState.ledgerJson).headSha256);
  assert.deepEqual(secondState.snapshot, second.runtimeSnapshot);
});

test("complete result, ledger, projection and replay bytes are deterministic for 20 runs", async () => {
  const outputs = [];
  for (let run = 0; run < 20; run += 1) {
    const { prepared, state } = await completeTimeline("deterministic-timeline");
    const replayed = replayWorldEventLedger({ prepared, worldEventLedgerJson: state.ledgerJson });
    assert.equal(replayed.ok, true);
    const projection = createDerivedProjectionManifest({
      worldEventLedgerJson: state.ledgerJson,
      projectionKind: "memory",
      reducer: { id: "identity-reducer", version: "1.0.0", sourceSha256: `sha256:${"1".repeat(64)}` },
      scopeEntityIds: ["actor-beta", "actor-alpha"],
      artifact: { format: "application.json", bytes: "{}" },
    });
    assert.equal(projection.ok, true);
    outputs.push(canonicalizeJsonValue({
      result: state.result.canonicalAdjudicationResultJson,
      ledger: state.ledgerJson,
      projection: projection.canonicalDerivedProjectionManifestJson,
      replay: replayed.canonicalWorldEventLedgerReplayReportJson,
    }));
  }
  assert.equal(new Set(outputs).size, 1);
});

test("R19 core sources contain no network, credential, process execution or case-specific path", async () => {
  const roots = [
    new URL("../packages/npc-authority-contracts/src/", import.meta.url),
    new URL("../packages/npc-authority-runtime/src/", import.meta.url),
  ];
  const files = [];
  for (const root of roots) {
    for (const name of await fs.readdir(root)) if (name.endsWith(".mjs")) files.push(new URL(name, root));
  }
  const source = (await Promise.all(files.map((file) => fs.readFile(file, "utf8")))).join("\n").toLowerCase();
  for (const forbidden of ["fetch(", "process.env", "child_process", "spawn(", "exec(", "openai", "marble", "meshy", "subway", "last-train"]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
});
