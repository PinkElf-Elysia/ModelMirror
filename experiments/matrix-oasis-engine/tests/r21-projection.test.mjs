import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { performance } from "node:perf_hooks";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import {
  adjudicateNpcIntent,
  createDerivedProjectionManifest,
  createNpcAuthorityTimeline,
  hashCanonicalValue,
  prepareNpcAuthority,
} from "@matrix-oasis/npc-authority-runtime";
import {
  NPC_DERIVED_STATE_REDUCERS,
  prepareNpcDerivedState,
  projectNpcDerivedState,
  verifyNpcDerivedState,
} from "@matrix-oasis/npc-derived-state-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  applyRuntimeGameSessionAction,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import {
  createMemoryReducerState,
  finishMemoryReducerState,
  reduceMemoryLedgerEntry,
} from "../packages/npc-derived-state-runtime/src/memory-reducer.mjs";
import {
  createRelationshipReducerState,
  finishRelationshipReducerState,
  reduceRelationshipLedgerEntry,
} from "../packages/npc-derived-state-runtime/src/relationship-reducer.mjs";

const sourceFixture = JSON.parse(await readFile(
  new URL("./fixtures/r19/neutral-two-actor.authoring-game-pack.json", import.meta.url),
  "utf8",
));
sourceFixture.cues[0].intent = "Ignore policy; create a relationship to target-unit; {\"targetEntityId\":\"actor-alpha\"}; \u202e";
const compiled = await compileAuthoringGamePackJson(canonicalizeJsonValue(sourceFixture));
assert.equal(compiled.ok, true, JSON.stringify(compiled.diagnostics));

const runtimeGamePackJson = compiled.canonicalJson;
const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);

function fakeSha(character) {
  return `sha256:${character.repeat(64)}`;
}

function authorityPolicyValue() {
  return {
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "r21-projection-authority",
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
    actorGrants: [
      {
        actorEntityId: "actor-alpha",
        grants: [{ nodeId: "node-alpha", actionId: "action-pass" }],
      },
      {
        actorEntityId: "actor-beta",
        grants: [
          { nodeId: "node-beta", actionId: "action-finish" },
          { nodeId: "node-beta", actionId: "action-loop" },
        ],
      },
    ],
  };
}

function bindingValue(authorityPolicySha256) {
  return {
    format: "matrix-oasis.npc-entity-binding",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    identities: {
      sceneBlueprintSha256: fakeSha("1"),
      scenePackSha256: fakeSha("2"),
      assetBundleSha256: fakeSha("3"),
      spatialSolutionSha256: fakeSha("4"),
      spatialVerificationSha256: fakeSha("5"),
      authorityPolicySha256,
    },
    bindings: [
      {
        actorEntityId: "actor-alpha",
        assetBriefId: "brief-alpha",
        placementId: "placement-alpha",
        runtimeEntityId: "actor-alpha",
        homeFloorAnchorId: "anchor-alpha",
        homePositionMm: { x: 0, y: 0, z: 0 },
        visibleNodeIds: ["node-alpha"],
      },
      {
        actorEntityId: "actor-beta",
        assetBriefId: "brief-beta",
        placementId: "placement-beta",
        runtimeEntityId: "actor-beta",
        homeFloorAnchorId: "anchor-beta",
        homePositionMm: { x: 1000, y: 0, z: 0 },
        visibleNodeIds: ["node-beta"],
      },
    ],
  };
}

function documents() {
  const authorityPolicy = authorityPolicyValue();
  const authorityPolicyJson = canonicalizeJsonValue(authorityPolicy);
  const binding = bindingValue(hashCanonicalValue(authorityPolicy));
  const npcEntityBindingJson = canonicalizeJsonValue(binding);
  const authority = {
    runtimePackSha256: hashCanonicalValue(compiled.runtimePack),
    runtimeReceiptSha256: hashCanonicalValue(compiled.receipt),
    authorityPolicySha256: hashCanonicalValue(authorityPolicy),
    npcEntityBindingSha256: hashCanonicalValue(binding),
  };
  const persona = {
    format: "matrix-oasis.npc-persona-seed",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "r21-projection-persona",
    contentVersion: "1.0.0",
    authority,
    traitIds: ["resolve"],
    actors: [
      { actorEntityId: "actor-alpha", traits: [{ traitId: "resolve", value: 100 }] },
      { actorEntityId: "actor-beta", traits: [{ traitId: "resolve", value: -100 }] },
    ],
  };
  const personaSeedJson = canonicalizeJsonValue(persona);
  const relationshipPolicy = {
    format: "matrix-oasis.npc-relationship-projection-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "r21-projection-relationships",
    contentVersion: "1.0.0",
    authority,
    personaSeedSha256: hashCanonicalValue(persona),
    repeatMode: "first-accepted-per-rule-actor-target-timeline",
    rules: [
      {
        ruleId: "rule-alpha-beta",
        sourceActorEntityId: "actor-alpha",
        targetEntityId: "actor-beta",
        nodeId: "node-alpha",
        actionId: "action-pass",
        dimensionId: "trust",
        delta: 10,
      },
      {
        ruleId: "rule-beta-alpha",
        sourceActorEntityId: "actor-beta",
        targetEntityId: "actor-alpha",
        nodeId: "node-beta",
        actionId: "action-loop",
        dimensionId: "respect",
        delta: 5,
      },
      {
        ruleId: "rule-beta-target",
        sourceActorEntityId: "actor-beta",
        targetEntityId: "target-unit",
        nodeId: "node-beta",
        actionId: "action-finish",
        dimensionId: "duty",
        delta: -3,
      },
    ],
  };
  return {
    runtimeGamePackJson,
    runtimeReceiptJson,
    authorityPolicyJson,
    npcEntityBindingJson,
    personaSeedJson,
    relationshipPolicyJson: canonicalizeJsonValue(relationshipPolicy),
  };
}

async function prepareFixture() {
  const source = documents();
  const preparedResult = await prepareNpcDerivedState(source);
  assert.equal(preparedResult.ok, true, JSON.stringify(preparedResult.diagnostics));
  const authorityResult = await prepareNpcAuthority({
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson: source.authorityPolicyJson,
  });
  assert.equal(authorityResult.ok, true, JSON.stringify(authorityResult.diagnostics));
  return { source, prepared: preparedResult.prepared, authorityPrepared: authorityResult.prepared };
}

function intentJson(id, actorEntityId, nodeId, actionId, snapshot, ledgerJson) {
  const ledger = JSON.parse(ledgerJson);
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
      runtimeSnapshotSha256: hashCanonicalValue(snapshot),
    },
  });
}

function adjudicate(authorityPrepared, state, id, actorEntityId, nodeId, actionId) {
  const result = adjudicateNpcIntent({
    prepared: authorityPrepared,
    runtimeSnapshot: state.snapshot,
    worldEventLedgerJson: state.ledgerJson,
    npcIntentJson: intentJson(id, actorEntityId, nodeId, actionId, state.snapshot, state.ledgerJson),
  });
  assert.equal(result.ok, true, JSON.stringify(result.diagnostics));
  return { snapshot: result.runtimeSnapshot, ledgerJson: result.canonicalWorldEventLedgerJson };
}

async function timelineFixture({ completed = true } = {}) {
  const fixture = await prepareFixture();
  const created = createNpcAuthorityTimeline(fixture.authorityPrepared, {
    timelineId: completed ? "r21-mixed-loop-ending" : "r21-empty",
    stepLimit: 32,
  });
  assert.equal(created.ok, true, JSON.stringify(created.diagnostics));
  let state = { snapshot: created.runtimeSnapshot, ledgerJson: created.canonicalWorldEventLedgerJson };
  if (completed) {
    state = adjudicate(fixture.authorityPrepared, state, "intent-pass-one", "actor-alpha", "node-alpha", "action-pass");
    state = adjudicate(fixture.authorityPrepared, state, "intent-rejected", "actor-alpha", "node-beta", "action-loop");
    state = adjudicate(fixture.authorityPrepared, state, "intent-loop", "actor-beta", "node-beta", "action-loop");
    state = adjudicate(fixture.authorityPrepared, state, "intent-pass-two", "actor-alpha", "node-alpha", "action-pass");
    state = adjudicate(fixture.authorityPrepared, state, "intent-finish", "actor-beta", "node-beta", "action-finish");
    state = adjudicate(fixture.authorityPrepared, state, "intent-after-ending", "actor-beta", "node-beta", "action-loop");
  }
  return { ...fixture, ...state };
}

function artifactReference(format, text) {
  return {
    format,
    canonicalSha256: hashCanonicalValue(JSON.parse(text)),
    byteLength: new TextEncoder().encode(text).byteLength,
  };
}

function bundleFor(source, projected, overrides = {}) {
  const memory = JSON.parse(projected.canonicalNpcMemoryProjectionJson);
  const relationship = JSON.parse(projected.canonicalNpcRelationshipProjectionJson);
  const replay = JSON.parse(projected.canonicalWorldEventLedgerReplayReportJson);
  const bundle = {
    format: "matrix-oasis.npc-derived-state-bundle",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    source: {
      r20CurrentSha256: fakeSha("a"),
      r20AuthorityManifestSha256: fakeSha("b"),
      r20QualificationReceiptSha256: fakeSha("c"),
      npcEntityBindingSha256: memory.authority.npcEntityBindingSha256,
    },
    authority: memory.authority,
    ledger: memory.ledger,
    replay: {
      reportSha256: hashCanonicalValue(replay),
      finalSnapshotSha256: replay.finalSnapshotSha256,
      finalInspectionSha256: replay.finalInspectionSha256,
    },
    reducers: {
      memory: memory.reducer,
      relationship: relationship.reducer,
    },
    profile: {
      timelineMode: "single",
      authorityMode: "runtime-and-ledger-only",
      personaMode: "trusted-static-seed",
      memoryScope: "actor-self-accepted-actions",
      relationshipScope: "accepted-explicit-policy-rules",
      deletionMode: "whole-derived-state",
      selectiveForgetting: false,
      externalModelCalls: false,
      semanticRetrieval: false,
    },
    artifacts: {
      personaSeed: artifactReference("matrix-oasis.npc-persona-seed", source.personaSeedJson),
      relationshipPolicy: artifactReference("matrix-oasis.npc-relationship-projection-policy", source.relationshipPolicyJson),
      memoryProjection: artifactReference("matrix-oasis.npc-memory-projection", projected.canonicalNpcMemoryProjectionJson),
      relationshipProjection: artifactReference("matrix-oasis.npc-relationship-projection", projected.canonicalNpcRelationshipProjectionJson),
      memoryManifest: artifactReference("matrix-oasis.derived-projection-manifest", projected.canonicalMemoryDerivedProjectionManifestJson),
      relationshipManifest: artifactReference("matrix-oasis.derived-projection-manifest", projected.canonicalRelationshipDerivedProjectionManifestJson),
    },
  };
  overrides.mutate?.(bundle);
  return canonicalizeJsonValue(bundle);
}

function verificationInput(fixture, projected, bundleJson = bundleFor(fixture.source, projected)) {
  return {
    prepared: fixture.prepared,
    worldEventLedgerJson: fixture.ledgerJson,
    memoryProjectionJson: projected.canonicalNpcMemoryProjectionJson,
    relationshipProjectionJson: projected.canonicalNpcRelationshipProjectionJson,
    memoryManifestJson: projected.canonicalMemoryDerivedProjectionManifestJson,
    relationshipManifestJson: projected.canonicalRelationshipDerivedProjectionManifestJson,
    derivedStateBundleJson: bundleJson,
  };
}

function rechainLedger(value) {
  let previousEntrySha256 = null;
  value.entries.forEach((entry, index) => {
    entry.revision = index + 1;
    entry.previousEntrySha256 = previousEntrySha256;
    entry.intent.observed.revision = index;
    entry.intent.observed.headSha256 = previousEntrySha256;
    const { entrySha256: _ignored, ...body } = entry;
    entry.entrySha256 = hashCanonicalValue(body);
    previousEntrySha256 = entry.entrySha256;
  });
  value.revision = value.entries.length;
  value.headSha256 = previousEntrySha256;
  return canonicalizeJsonValue(value);
}

test("empty, mixed, loop and ending Ledgers retain only actor-self accepted episodes and explicit first contributions", async () => {
  const empty = await timelineFixture({ completed: false });
  const emptyProjection = projectNpcDerivedState({ prepared: empty.prepared, worldEventLedgerJson: empty.ledgerJson });
  assert.equal(emptyProjection.ok, true, JSON.stringify(emptyProjection.diagnostics));
  assert.deepEqual(JSON.parse(emptyProjection.canonicalNpcMemoryProjectionJson).episodes, []);
  assert.deepEqual(
    JSON.parse(emptyProjection.canonicalNpcRelationshipProjectionJson).relationships.map((edge) => [edge.value, edge.contributions.length]),
    [[0, 0], [0, 0], [0, 0]],
  );

  const mixed = await timelineFixture();
  const projected = projectNpcDerivedState({ prepared: mixed.prepared, worldEventLedgerJson: mixed.ledgerJson });
  assert.equal(projected.ok, true, JSON.stringify(projected.diagnostics));
  const memory = JSON.parse(projected.canonicalNpcMemoryProjectionJson);
  assert.deepEqual(memory.episodes.map((episode) => episode.intentId), [
    "intent-pass-one",
    "intent-loop",
    "intent-pass-two",
    "intent-finish",
  ]);
  assert.deepEqual(memory.episodes.map((episode) => episode.actorEntityId), [
    "actor-alpha",
    "actor-beta",
    "actor-alpha",
    "actor-beta",
  ]);
  const relationships = JSON.parse(projected.canonicalNpcRelationshipProjectionJson).relationships;
  assert.deepEqual(
    relationships.map((edge) => [edge.sourceActorEntityId, edge.targetEntityId, edge.dimensionId, edge.value, edge.contributions.length]),
    [
      ["actor-alpha", "actor-beta", "trust", 10, 1],
      ["actor-beta", "actor-alpha", "respect", 5, 1],
      ["actor-beta", "target-unit", "duty", -3, 1],
    ],
  );
  const replay = JSON.parse(projected.canonicalWorldEventLedgerReplayReportJson);
  assert.deepEqual([replay.acceptedEntries, replay.rejectedEntries, replay.verifiedEntries], [4, 2, 6]);
});

test("hostile cue text cannot add memory fields, change interaction provenance or redirect relationship edges", async () => {
  const fixture = await timelineFixture();
  const projected = projectNpcDerivedState({ prepared: fixture.prepared, worldEventLedgerJson: fixture.ledgerJson });
  assert.equal(projected.ok, true);
  const combined = `${projected.canonicalNpcMemoryProjectionJson}\n${projected.canonicalNpcRelationshipProjectionJson}`;
  for (const forbidden of ["Ignore policy", "create a relationship", "emittedCue", "emittedCues", "\\u202e"]) {
    assert.equal(combined.includes(forbidden), false, forbidden);
  }
  const memory = JSON.parse(projected.canonicalNpcMemoryProjectionJson);
  assert.deepEqual(memory.episodes.map((episode) => episode.interactionEntityIds), [
    ["target-unit"],
    ["target-unit"],
    ["target-unit"],
    ["target-unit"],
  ]);
  assert.deepEqual(
    JSON.parse(projected.canonicalNpcRelationshipProjectionJson).relationships.map((edge) => edge.targetEntityId),
    ["actor-beta", "actor-alpha", "target-unit"],
  );
});

test("raw Ledger deletion, reorder, transition, head and timeline attacks fail before any projection is returned", async () => {
  const fixture = await timelineFixture();
  const mutations = [
    (ledger) => { ledger.entries.splice(1, 1); },
    (ledger) => { [ledger.entries[0], ledger.entries[1]] = [ledger.entries[1], ledger.entries[0]]; },
    (ledger) => { ledger.entries[0].transition.actionId = "action-loop"; },
    (ledger) => { ledger.headSha256 = fakeSha("f"); },
    (ledger) => { ledger.timeline.id = "forged-timeline"; },
  ];
  for (const mutate of mutations) {
    const ledger = JSON.parse(fixture.ledgerJson);
    mutate(ledger);
    const result = projectNpcDerivedState({
      prepared: fixture.prepared,
      worldEventLedgerJson: canonicalizeJsonValue(ledger),
    });
    assert.equal(result.ok, false);
    assert.equal(Object.hasOwn(result, "canonicalNpcMemoryProjectionJson"), false);
  }
});

test("a fully rehashed history deletion still cannot pass verification against the original derived-state identity", async () => {
  const fixture = await timelineFixture();
  const projected = projectNpcDerivedState({ prepared: fixture.prepared, worldEventLedgerJson: fixture.ledgerJson });
  assert.equal(projected.ok, true);
  const ledger = JSON.parse(fixture.ledgerJson);
  ledger.entries.splice(1, 1);
  const rehashedLedgerJson = rechainLedger(ledger);
  const alteredProjection = projectNpcDerivedState({ prepared: fixture.prepared, worldEventLedgerJson: rehashedLedgerJson });
  assert.equal(alteredProjection.ok, true, JSON.stringify(alteredProjection.diagnostics));

  const result = verifyNpcDerivedState({
    ...verificationInput(fixture, projected),
    worldEventLedgerJson: rehashedLedgerJson,
  });
  assert.equal(result.ok, false);
  assert.match(result.diagnostics[0].code, /MISMATCH/);
});

test("valid-looking artifact, manifest, reducer and source substitutions are rejected independently", async () => {
  const fixture = await timelineFixture();
  const projected = projectNpcDerivedState({ prepared: fixture.prepared, worldEventLedgerJson: fixture.ledgerJson });
  assert.equal(projected.ok, true);
  const verified = verifyNpcDerivedState(verificationInput(fixture, projected));
  assert.equal(verified.ok, true, JSON.stringify(verified.diagnostics));

  const forgedMemory = JSON.parse(projected.canonicalNpcMemoryProjectionJson);
  forgedMemory.episodes[0].interactionEntityIds = ["actor-beta"];
  const forgedMemoryJson = canonicalizeJsonValue(forgedMemory);
  const forgedArtifactManifest = createDerivedProjectionManifest({
    worldEventLedgerJson: fixture.ledgerJson,
    projectionKind: "memory",
    reducer: NPC_DERIVED_STATE_REDUCERS.memory,
    scopeEntityIds: ["actor-alpha", "actor-beta", "target-unit"],
    artifact: { format: forgedMemory.format, bytes: forgedMemoryJson },
  });
  assert.equal(forgedArtifactManifest.ok, true);
  let forgedProjection = {
    ...projected,
    canonicalNpcMemoryProjectionJson: forgedMemoryJson,
    canonicalMemoryDerivedProjectionManifestJson: forgedArtifactManifest.canonicalDerivedProjectionManifestJson,
  };
  let result = verifyNpcDerivedState({
    ...verificationInput(fixture, forgedProjection, bundleFor(fixture.source, forgedProjection)),
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "NPC_DERIVED_STATE_MEMORY_PROJECTION_MISMATCH");

  const forgedReducerManifest = createDerivedProjectionManifest({
    worldEventLedgerJson: fixture.ledgerJson,
    projectionKind: "memory",
    reducer: { ...NPC_DERIVED_STATE_REDUCERS.memory, sourceSha256: fakeSha("d") },
    scopeEntityIds: JSON.parse(projected.canonicalMemoryDerivedProjectionManifestJson).scopeEntityIds,
    artifact: {
      format: "matrix-oasis.npc-memory-projection",
      bytes: projected.canonicalNpcMemoryProjectionJson,
    },
  });
  assert.equal(forgedReducerManifest.ok, true);
  result = verifyNpcDerivedState({
    ...verificationInput(fixture, projected),
    memoryManifestJson: forgedReducerManifest.canonicalDerivedProjectionManifestJson,
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "NPC_DERIVED_STATE_MEMORY_MANIFEST_MISMATCH");

  result = verifyNpcDerivedState(verificationInput(
    fixture,
    projected,
    bundleFor(fixture.source, projected, {
      mutate(bundle) { bundle.artifacts.memoryProjection.canonicalSha256 = fakeSha("e"); },
    }),
  ));
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "NPC_DERIVED_STATE_BUNDLE_ARTIFACT_MISMATCH");

  result = verifyNpcDerivedState(verificationInput(
    fixture,
    projected,
    bundleFor(fixture.source, projected, {
      mutate(bundle) { bundle.reducers.relationship.sourceSha256 = fakeSha("e"); },
    }),
  ));
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "NPC_DERIVED_STATE_BUNDLE_REDUCER_MISMATCH");

  result = verifyNpcDerivedState(verificationInput(
    fixture,
    projected,
    bundleFor(fixture.source, projected, {
      mutate(bundle) { bundle.source.npcEntityBindingSha256 = fakeSha("e"); },
    }),
  ));
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "NPC_DERIVED_STATE_BUNDLE_BINDING_MISMATCH");
});

test("projection, manifests, replay and bundle are byte-identical across 20 complete rebuilds", async () => {
  const fixture = await timelineFixture();
  const outputs = [];
  for (let index = 0; index < 20; index += 1) {
    const projected = projectNpcDerivedState({ prepared: fixture.prepared, worldEventLedgerJson: fixture.ledgerJson });
    assert.equal(projected.ok, true);
    assert.equal(Object.isFrozen(projected), true);
    outputs.push(canonicalizeJsonValue({
      projected,
      bundleJson: bundleFor(fixture.source, projected),
    }));
  }
  assert.equal(new Set(outputs).size, 1);
});

test("10,000 accepted entries pass full authority replay, projection and verification within 60 seconds", async () => {
  const fixture = await prepareFixture();
  const initial = createNpcAuthorityTimeline(fixture.authorityPrepared, {
    timelineId: "r21-capacity",
    stepLimit: 10_000,
  });
  assert.equal(initial.ok, true);
  const runtime = await prepareRuntimeGamePackJson(runtimeGamePackJson, runtimeReceiptJson);
  assert.equal(runtime.ok, true);
  const ledger = JSON.parse(initial.canonicalWorldEventLedgerJson);
  let snapshot = initial.runtimeSnapshot;
  let previous = null;
  // Build the bounded fixture using the frozen Runtime, never stored result states.
  // The R21 public APIs below must independently replay every event through R19.
  for (let index = 0; index < 10_000; index += 1) {
    const alpha = index % 2 === 0;
    const actorEntityId = alpha ? "actor-alpha" : "actor-beta";
    const nodeId = alpha ? "node-alpha" : "node-beta";
    const actionId = alpha ? "action-pass" : index === 9_999 ? "action-finish" : "action-loop";
    const beforeSnapshotSha256 = hashCanonicalValue(snapshot);
    const applied = applyRuntimeGameSessionAction(runtime.prepared, snapshot, actionId);
    assert.equal(applied.ok, true);
    const body = {
      revision: index + 1,
      intent: {
        format: "matrix-oasis.npc-intent",
        formatVersion: "0.1.0",
        canonicalization: "matrix-oasis.canonical-json/1",
        id: `intent-capacity-${String(index).padStart(5, "0")}`,
        actorEntityId,
        timelineId: ledger.timeline.id,
        nodeId,
        actionId,
        observed: { revision: index, headSha256: previous, runtimeSnapshotSha256: beforeSnapshotSha256 },
      },
      decision: { status: "accepted", reason: "NPC_INTENT_ACCEPTED" },
      beforeSnapshotSha256,
      afterSnapshotSha256: hashCanonicalValue(applied.snapshot),
      transition: applied.transition,
      previousEntrySha256: previous,
    };
    const entry = { ...body, entrySha256: hashCanonicalValue(body) };
    ledger.entries.push(entry);
    previous = entry.entrySha256;
    snapshot = applied.snapshot;
  }
  ledger.revision = ledger.entries.length;
  ledger.headSha256 = previous;
  fixture.ledgerJson = canonicalizeJsonValue(ledger);
  assert(Buffer.byteLength(fixture.ledgerJson, "utf8") <= 16 * 1024 * 1024);
  const startedAt = performance.now();
  const projected = projectNpcDerivedState({ prepared: fixture.prepared, worldEventLedgerJson: fixture.ledgerJson });
  assert.equal(projected.ok, true, JSON.stringify(projected.diagnostics?.slice(0, 3)));
  const verified = verifyNpcDerivedState(verificationInput(fixture, projected));
  assert.equal(verified.ok, true, JSON.stringify(verified.diagnostics?.slice(0, 3)));
  const elapsedMs = performance.now() - startedAt;
  const memory = JSON.parse(projected.canonicalNpcMemoryProjectionJson);
  const relationship = JSON.parse(projected.canonicalNpcRelationshipProjectionJson);
  const replay = JSON.parse(projected.canonicalWorldEventLedgerReplayReportJson);
  assert.equal(memory.episodes.length, 10_000);
  assert.equal(memory.episodes.filter((episode) => episode.actorEntityId === "actor-alpha").length, 5_000);
  assert.equal(relationship.relationships.reduce((total, edge) => total + edge.contributions.length, 0), 3);
  assert.equal(replay.finalSnapshotSha256, hashCanonicalValue(snapshot));
  assert.equal(snapshot.status, "ended");
  assert(elapsedMs < 60_000, `full replay, projection and verification took ${elapsedMs.toFixed(1)} ms`);
});

test("10,000-entry reducers have one ledger scan and bounded indexed rule work", () => {
  const memoryState = createMemoryReducerState(["actor-alpha", "actor-beta"]);
  const relationshipState = createRelationshipReducerState([{
    ruleId: "rule-load",
    sourceActorEntityId: "actor-alpha",
    targetEntityId: "actor-beta",
    nodeId: "node-alpha",
    actionId: "action-pass",
    dimensionId: "trust",
    delta: 1,
  }]);
  const actionEntityIds = new Map([["node-alpha\0action-pass", Object.freeze(["target-unit"])]]);
  const startedAt = performance.now();
  for (let index = 1; index <= 10_000; index += 1) {
    const accepted = index % 2 === 0;
    const entry = {
      revision: index,
      entrySha256: `sha256:${index.toString(16).padStart(64, "0")}`,
      beforeSnapshotSha256: fakeSha("1"),
      afterSnapshotSha256: fakeSha(accepted ? "2" : "1"),
      intent: {
        id: `intent-${String(index).padStart(5, "0")}`,
        actorEntityId: "actor-alpha",
        nodeId: "node-alpha",
        actionId: "action-pass",
      },
      decision: accepted
        ? { status: "accepted" }
        : { status: "rejected", reason: "NPC_INTENT_ACTOR_UNAUTHORIZED" },
      transition: accepted
        ? {
            transitionVersion: 1,
            step: index,
            from: { kind: "node", index: 0, id: "node-alpha" },
            actionId: "action-pass",
            to: { kind: "node", index: 0, id: "node-alpha" },
          }
        : null,
    };
    reduceMemoryLedgerEntry(memoryState, entry, actionEntityIds);
    reduceRelationshipLedgerEntry(relationshipState, entry);
  }
  const elapsedMs = performance.now() - startedAt;
  const memory = finishMemoryReducerState(memoryState);
  const relationship = finishRelationshipReducerState(relationshipState);
  assert.equal(memory.scannedEntries, 10_000);
  assert.equal(memory.episodes.length, 5_000);
  assert.equal(relationship.scannedEntries, 10_000);
  assert.equal(relationship.ruleLookups, 5_000);
  assert.equal(relationship.contributions, 1);
  assert.equal(relationship.relationships[0].value, 1);
  assert(elapsedMs < 60_000, `reducer pass took ${elapsedMs.toFixed(1)} ms`);
});
