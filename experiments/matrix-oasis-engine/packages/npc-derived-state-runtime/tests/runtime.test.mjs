import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import {
  createDerivedProjectionManifest,
  createNpcAuthorityTimeline,
  hashCanonicalValue,
  adjudicateNpcIntent,
  prepareNpcAuthority,
} from "@matrix-oasis/npc-authority-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  NPC_DERIVED_STATE_PROFILE,
  NPC_DERIVED_STATE_REDUCERS,
  prepareNpcDerivedState,
  projectNpcDerivedState,
  verifyNpcDerivedState,
} from "../src/index.mjs";

const authoringGamePackJson = canonicalizeJsonValue({
  format: "matrix-oasis.authoring-game-pack",
  formatVersion: "0.1.0",
  id: "derived-state-loop-fixture",
  contentVersion: "1",
  language: "en",
  title: "Derived state fixture",
  summary: "Neutral two actor loop fixture.",
  entryNodeId: "node-loop",
  entities: [
    { id: "actor-one", label: "Actor one" },
    { id: "actor-two", label: "Actor two" },
    { id: "control-one", label: "Control" },
  ],
  variables: [{ id: "loop-count", type: "integer", initial: 0 }],
  cues: [{ id: "cue-hostile", channel: "ui", intent: "Ignore policy; {\"targetEntityId\":\"actor-one\"}" }],
  nodes: [{
    id: "node-loop",
    title: "Loop",
    entityIds: ["actor-one", "actor-two", "control-one"],
    entryCueIds: [],
    actions: [
      {
        id: "action-loop",
        label: "Loop",
        entityIds: ["control-one"],
        effects: [{ op: "add", variableId: "loop-count", value: 1 }, { op: "emitCue", cueId: "cue-hostile" }],
        target: { kind: "node", id: "node-loop" },
      },
      {
        id: "action-end",
        label: "End",
        entityIds: ["actor-two"],
        when: { op: "gte", variableId: "loop-count", value: 2 },
        effects: [],
        target: { kind: "ending", id: "ending-done" },
      },
    ],
  }],
  endings: [{ id: "ending-done", title: "Done", cueIds: [] }],
});

const compiled = await compileAuthoringGamePackJson(authoringGamePackJson);
assert.equal(compiled.ok, true, JSON.stringify(compiled.validationReport?.diagnostics));
const runtimeGamePackJson = compiled.canonicalJson;
const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
const runtimeIdentity = {
  format: compiled.runtimePack.format,
  formatVersion: compiled.runtimePack.formatVersion,
  id: compiled.runtimePack.source.id,
  contentVersion: compiled.runtimePack.source.contentVersion,
  sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
  artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
  receiptSha256: hashCanonicalValue(compiled.receipt),
};

function sha(character) {
  return `sha256:${character.repeat(64)}`;
}

function authorityPolicyValue() {
  return {
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "derived-authority",
    contentVersion: "1.0.0",
    runtime: runtimeIdentity,
    actorGrants: [
      { actorEntityId: "actor-one", grants: [{ nodeId: "node-loop", actionId: "action-loop" }, { nodeId: "node-loop", actionId: "action-end" }] },
      { actorEntityId: "actor-two", grants: [{ nodeId: "node-loop", actionId: "action-loop" }] },
    ],
  };
}

function bindingValue(authorityPolicySha256) {
  return {
    format: "matrix-oasis.npc-entity-binding",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    identities: {
      sceneBlueprintSha256: sha("a"),
      scenePackSha256: sha("b"),
      assetBundleSha256: sha("c"),
      spatialSolutionSha256: sha("d"),
      spatialVerificationSha256: sha("e"),
      authorityPolicySha256,
    },
    bindings: [
      { actorEntityId: "actor-one", assetBriefId: "brief-one", placementId: "placement-one", runtimeEntityId: "actor-one", homeFloorAnchorId: "anchor-one", homePositionMm: { x: 0, y: 0, z: 0 }, visibleNodeIds: ["node-loop"] },
      { actorEntityId: "actor-two", assetBriefId: "brief-two", placementId: "placement-two", runtimeEntityId: "actor-two", homeFloorAnchorId: "anchor-two", homePositionMm: { x: 1000, y: 0, z: 0 }, visibleNodeIds: ["node-loop"] },
    ],
  };
}

function personaValue(authority) {
  return {
    format: "matrix-oasis.npc-persona-seed",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "persona-one",
    contentVersion: "1.0.0",
    authority,
    traitIds: ["caution", "resolve"],
    actors: [
      { actorEntityId: "actor-one", traits: [{ traitId: "caution", value: 100 }, { traitId: "resolve", value: 500 }] },
      { actorEntityId: "actor-two", traits: [{ traitId: "caution", value: -100 }, { traitId: "resolve", value: 250 }] },
    ],
  };
}

function relationshipPolicyValue(authority, personaSeedSha256) {
  return {
    format: "matrix-oasis.npc-relationship-projection-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "relationships-one",
    contentVersion: "1.0.0",
    authority,
    personaSeedSha256,
    repeatMode: "first-accepted-per-rule-actor-target-timeline",
    rules: [
      { ruleId: "rule-end", sourceActorEntityId: "actor-one", targetEntityId: "control-one", nodeId: "node-loop", actionId: "action-end", dimensionId: "trust", delta: -2 },
      { ruleId: "rule-loop", sourceActorEntityId: "actor-one", targetEntityId: "actor-two", nodeId: "node-loop", actionId: "action-loop", dimensionId: "trust", delta: 7 },
      { ruleId: "rule-zero", sourceActorEntityId: "actor-two", targetEntityId: "actor-one", nodeId: "node-loop", actionId: "action-loop", dimensionId: "respect", delta: 4 },
    ],
  };
}

function staticDocuments(overrides = {}) {
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
  const persona = personaValue(authority);
  const personaSeedJson = canonicalizeJsonValue(persona);
  const relationshipPolicy = relationshipPolicyValue(authority, hashCanonicalValue(persona));
  return {
    runtimeGamePackJson,
    runtimeReceiptJson,
    authorityPolicyJson,
    npcEntityBindingJson,
    personaSeedJson,
    relationshipPolicyJson: canonicalizeJsonValue(relationshipPolicy),
    ...overrides,
  };
}

async function preparedFixture(documents = staticDocuments()) {
  const prepared = await prepareNpcDerivedState(documents);
  assert.equal(prepared.ok, true, JSON.stringify(prepared.diagnostics));
  return { documents, prepared: prepared.prepared };
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

async function completedLedger() {
  const documents = staticDocuments();
  const authority = await prepareNpcAuthority({
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson: documents.authorityPolicyJson,
  });
  assert.equal(authority.ok, true, JSON.stringify(authority.diagnostics));
  const created = createNpcAuthorityTimeline(authority.prepared, { timelineId: "timeline-derived", stepLimit: 16 });
  assert.equal(created.ok, true);
  let snapshot = created.runtimeSnapshot;
  let ledgerJson = created.canonicalWorldEventLedgerJson;
  const apply = (id, actor, action) => {
    const result = adjudicateNpcIntent({
      prepared: authority.prepared,
      runtimeSnapshot: snapshot,
      worldEventLedgerJson: ledgerJson,
      npcIntentJson: intentJson(id, actor, "node-loop", action, snapshot, ledgerJson),
    });
    assert.equal(result.ok, true, JSON.stringify(result.diagnostics));
    snapshot = result.runtimeSnapshot;
    ledgerJson = result.canonicalWorldEventLedgerJson;
    return JSON.parse(result.canonicalAdjudicationResultJson).decision;
  };
  assert.equal(apply("intent-rejected", "actor-two", "action-end").status, "rejected");
  assert.equal(apply("intent-loop-one", "actor-one", "action-loop").status, "accepted");
  assert.equal(apply("intent-loop-two", "actor-one", "action-loop").status, "accepted");
  assert.equal(apply("intent-ending", "actor-one", "action-end").status, "accepted");
  assert.equal(apply("intent-after-ending", "actor-one", "action-loop").status, "rejected");
  return { documents, ledgerJson, snapshot };
}

function artifactReference(format, text) {
  return { format, canonicalSha256: hashCanonicalValue(JSON.parse(text)), byteLength: new TextEncoder().encode(text).byteLength };
}

function bundleFor(documents, projection, sourceOverrides = {}) {
  const memory = JSON.parse(projection.canonicalNpcMemoryProjectionJson);
  const relationship = JSON.parse(projection.canonicalNpcRelationshipProjectionJson);
  const replay = JSON.parse(projection.canonicalWorldEventLedgerReplayReportJson);
  return canonicalizeJsonValue({
    format: "matrix-oasis.npc-derived-state-bundle",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    source: {
      r20CurrentSha256: sha("1"),
      r20AuthorityManifestSha256: sha("2"),
      r20QualificationReceiptSha256: sha("3"),
      npcEntityBindingSha256: memory.authority.npcEntityBindingSha256,
      ...sourceOverrides,
    },
    authority: memory.authority,
    ledger: memory.ledger,
    replay: {
      reportSha256: hashCanonicalValue(replay),
      finalSnapshotSha256: replay.finalSnapshotSha256,
      finalInspectionSha256: replay.finalInspectionSha256,
    },
    reducers: { memory: memory.reducer, relationship: relationship.reducer },
    profile: NPC_DERIVED_STATE_PROFILE,
    artifacts: {
      personaSeed: artifactReference("matrix-oasis.npc-persona-seed", documents.personaSeedJson),
      relationshipPolicy: artifactReference("matrix-oasis.npc-relationship-projection-policy", documents.relationshipPolicyJson),
      memoryProjection: artifactReference("matrix-oasis.npc-memory-projection", projection.canonicalNpcMemoryProjectionJson),
      relationshipProjection: artifactReference("matrix-oasis.npc-relationship-projection", projection.canonicalNpcRelationshipProjectionJson),
      memoryManifest: artifactReference("matrix-oasis.derived-projection-manifest", projection.canonicalMemoryDerivedProjectionManifestJson),
      relationshipManifest: artifactReference("matrix-oasis.derived-projection-manifest", projection.canonicalRelationshipDerivedProjectionManifestJson),
    },
  });
}

test("prepare binds persona and relationship rules to Runtime, Policy and the exact R20 actor binding", async () => {
  const documents = staticDocuments();
  assert.equal((await prepareNpcDerivedState(documents)).ok, true);

  const persona = JSON.parse(documents.personaSeedJson);
  persona.authority.npcEntityBindingSha256 = sha("f");
  const identityDrift = await prepareNpcDerivedState({ ...documents, personaSeedJson: canonicalizeJsonValue(persona) });
  assert.equal(identityDrift.ok, false);
  assert(identityDrift.diagnostics.some((value) => value.code === "NPC_DERIVED_STATE_PERSONA_AUTHORITY_MISMATCH"));

  const binding = JSON.parse(documents.npcEntityBindingJson);
  binding.bindings[0].runtimeEntityId = "control-one";
  const bindingJson = canonicalizeJsonValue(binding);
  const bindingAuthority = { ...JSON.parse(documents.personaSeedJson).authority, npcEntityBindingSha256: hashCanonicalValue(binding) };
  const reboundPersona = personaValue(bindingAuthority);
  const reboundPersonaJson = canonicalizeJsonValue(reboundPersona);
  const reboundRelationship = relationshipPolicyValue(bindingAuthority, hashCanonicalValue(reboundPersona));
  const invalidBinding = await prepareNpcDerivedState({
    ...documents,
    npcEntityBindingJson: bindingJson,
    personaSeedJson: reboundPersonaJson,
    relationshipPolicyJson: canonicalizeJsonValue(reboundRelationship),
  });
  assert.equal(invalidBinding.ok, false);
  assert(invalidBinding.diagnostics.some((value) => value.code === "NPC_DERIVED_STATE_BINDING_RUNTIME_ACTOR_MISMATCH"));
});

test("relationship policy rejects a theoretical directed-edge overflow before projection", async () => {
  const documents = staticDocuments();
  const policy = JSON.parse(documents.relationshipPolicyJson);
  policy.rules = Array.from({ length: 11 }, (_, index) => ({
    ruleId: `rule-overflow-${String(index).padStart(2, "0")}`,
    sourceActorEntityId: "actor-one",
    targetEntityId: "actor-two",
    nodeId: `node-overflow-${String(index).padStart(2, "0")}`,
    actionId: "action-overflow",
    dimensionId: "trust",
    delta: 100,
  }));
  const result = await prepareNpcDerivedState({ ...documents, relationshipPolicyJson: canonicalizeJsonValue(policy) });
  assert.equal(result.ok, false);
  assert(result.diagnostics.some((value) => value.code === "NPC_RELATIONSHIP_POLICY_THEORETICAL_RANGE_EXCEEDED"));
});

test("accepted actor-self actions form cue-free memory while rejected entries and repeated rules contribute nothing", async () => {
  const { documents, ledgerJson } = await completedLedger();
  const { prepared } = await preparedFixture(documents);
  const projected = projectNpcDerivedState({ prepared, worldEventLedgerJson: ledgerJson });
  assert.equal(projected.ok, true, JSON.stringify(projected.diagnostics));
  const memory = JSON.parse(projected.canonicalNpcMemoryProjectionJson);
  assert.equal(memory.episodes.length, 3);
  assert.deepEqual(memory.episodes.map((episode) => episode.actorEntityId), ["actor-one", "actor-one", "actor-one"]);
  assert.deepEqual(memory.episodes.map((episode) => episode.intentId), ["intent-loop-one", "intent-loop-two", "intent-ending"]);
  assert.deepEqual(memory.episodes.map((episode) => episode.interactionEntityIds), [["control-one"], ["control-one"], ["actor-two"]]);
  assert.equal(projected.canonicalNpcMemoryProjectionJson.includes("Ignore policy"), false);
  assert.equal(projected.canonicalNpcMemoryProjectionJson.includes("emittedCues"), false);

  const relationship = JSON.parse(projected.canonicalNpcRelationshipProjectionJson);
  assert.deepEqual(relationship.relationships.map((edge) => [edge.sourceActorEntityId, edge.targetEntityId, edge.dimensionId, edge.value]), [
    ["actor-one", "actor-two", "trust", 7],
    ["actor-one", "control-one", "trust", -2],
    ["actor-two", "actor-one", "respect", 0],
  ]);
  assert.equal(relationship.relationships[0].contributions.length, 1);
  assert.equal(relationship.relationships[2].contributions.length, 0);
  const memoryManifest = JSON.parse(projected.canonicalMemoryDerivedProjectionManifestJson);
  const relationshipManifest = JSON.parse(projected.canonicalRelationshipDerivedProjectionManifestJson);
  assert.deepEqual(memoryManifest.scopeEntityIds, ["actor-one", "actor-two", "control-one"]);
  assert.deepEqual(relationshipManifest.scopeEntityIds, ["actor-one", "actor-two", "control-one"]);
  const replay = JSON.parse(projected.canonicalWorldEventLedgerReplayReportJson);
  assert.deepEqual([replay.acceptedEntries, replay.rejectedEntries, replay.verifiedEntries], [3, 2, 5]);
});

test("same prepared input produces byte-identical deep-frozen projections and manifests for 20 runs", async () => {
  const { documents, ledgerJson } = await completedLedger();
  const { prepared } = await preparedFixture(documents);
  const outputs = [];
  for (let index = 0; index < 20; index += 1) {
    const projected = projectNpcDerivedState({ prepared, worldEventLedgerJson: ledgerJson });
    assert.equal(projected.ok, true);
    assert.equal(Object.isFrozen(projected), true);
    outputs.push(canonicalizeJsonValue(projected));
  }
  assert.equal(new Set(outputs).size, 1);
});

test("verify fully rebuilds semantics and rejects a valid R19 manifest wrapped around a forged artifact", async () => {
  const { documents, ledgerJson } = await completedLedger();
  const { prepared } = await preparedFixture(documents);
  const projected = projectNpcDerivedState({ prepared, worldEventLedgerJson: ledgerJson });
  assert.equal(projected.ok, true);
  const bundleJson = bundleFor(documents, projected);
  const verified = verifyNpcDerivedState({
    prepared,
    worldEventLedgerJson: ledgerJson,
    memoryProjectionJson: projected.canonicalNpcMemoryProjectionJson,
    relationshipProjectionJson: projected.canonicalNpcRelationshipProjectionJson,
    memoryManifestJson: projected.canonicalMemoryDerivedProjectionManifestJson,
    relationshipManifestJson: projected.canonicalRelationshipDerivedProjectionManifestJson,
    derivedStateBundleJson: bundleJson,
  });
  assert.equal(verified.ok, true, JSON.stringify(verified.diagnostics));

  const forgedMemory = JSON.parse(projected.canonicalNpcMemoryProjectionJson);
  forgedMemory.episodes[0].interactionEntityIds = ["actor-two"];
  const forgedMemoryJson = canonicalizeJsonValue(forgedMemory);
  const forgedManifest = createDerivedProjectionManifest({
    worldEventLedgerJson: ledgerJson,
    projectionKind: "memory",
    reducer: NPC_DERIVED_STATE_REDUCERS.memory,
    scopeEntityIds: forgedMemory.scopeActorEntityIds,
    artifact: { format: forgedMemory.format, bytes: forgedMemoryJson },
  });
  assert.equal(forgedManifest.ok, true);
  const forgedProjection = {
    ...projected,
    canonicalNpcMemoryProjectionJson: forgedMemoryJson,
    canonicalMemoryDerivedProjectionManifestJson: forgedManifest.canonicalDerivedProjectionManifestJson,
  };
  const forgedBundle = bundleFor(documents, forgedProjection);
  const rejected = verifyNpcDerivedState({
    prepared,
    worldEventLedgerJson: ledgerJson,
    memoryProjectionJson: forgedMemoryJson,
    relationshipProjectionJson: projected.canonicalNpcRelationshipProjectionJson,
    memoryManifestJson: forgedManifest.canonicalDerivedProjectionManifestJson,
    relationshipManifestJson: projected.canonicalRelationshipDerivedProjectionManifestJson,
    derivedStateBundleJson: forgedBundle,
  });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.diagnostics[0].code, "NPC_DERIVED_STATE_MEMORY_PROJECTION_MISMATCH");
});

test("verify rejects reducer and source-binding substitution without trusting caller-reported hashes", async () => {
  const { documents, ledgerJson } = await completedLedger();
  const { prepared } = await preparedFixture(documents);
  const projected = projectNpcDerivedState({ prepared, worldEventLedgerJson: ledgerJson });
  const bundle = JSON.parse(bundleFor(documents, projected));
  bundle.reducers.memory.sourceSha256 = sha("f");
  let result = verifyNpcDerivedState({
    prepared,
    worldEventLedgerJson: ledgerJson,
    memoryProjectionJson: projected.canonicalNpcMemoryProjectionJson,
    relationshipProjectionJson: projected.canonicalNpcRelationshipProjectionJson,
    memoryManifestJson: projected.canonicalMemoryDerivedProjectionManifestJson,
    relationshipManifestJson: projected.canonicalRelationshipDerivedProjectionManifestJson,
    derivedStateBundleJson: canonicalizeJsonValue(bundle),
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "NPC_DERIVED_STATE_BUNDLE_REDUCER_MISMATCH");
  bundle.reducers.memory = NPC_DERIVED_STATE_REDUCERS.memory;
  bundle.source.npcEntityBindingSha256 = sha("f");
  result = verifyNpcDerivedState({
    prepared,
    worldEventLedgerJson: ledgerJson,
    memoryProjectionJson: projected.canonicalNpcMemoryProjectionJson,
    relationshipProjectionJson: projected.canonicalNpcRelationshipProjectionJson,
    memoryManifestJson: projected.canonicalMemoryDerivedProjectionManifestJson,
    relationshipManifestJson: projected.canonicalRelationshipDerivedProjectionManifestJson,
    derivedStateBundleJson: canonicalizeJsonValue(bundle),
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "NPC_DERIVED_STATE_BUNDLE_BINDING_MISMATCH");
});

test("compile-time reducer identities equal the exact reducer source bytes and core has no ambient authority", async () => {
  const files = [
    ["memory", new URL("../src/memory-reducer.mjs", import.meta.url)],
    ["relationship", new URL("../src/relationship-reducer.mjs", import.meta.url)],
  ];
  for (const [kind, url] of files) {
    const bytes = await readFile(url);
    assert.equal(NPC_DERIVED_STATE_REDUCERS[kind].sourceSha256, `sha256:${createHash("sha256").update(bytes).digest("hex")}`);
  }
  for (const file of ["index.mjs", "memory-reducer.mjs", "relationship-reducer.mjs", "reducer-registry.mjs"]) {
    const source = await readFile(new URL(`../src/${file}`, import.meta.url), "utf8");
    assert.equal(/node:fs|process\.|\bfetch\s*\(|import\s*\(/u.test(source), false, file);
  }
});

test("hostile accessor faults collapse to the single R21 operational error", async () => {
  const { documents } = await completedLedger();
  const hostile = { ...documents };
  Object.defineProperty(hostile, "runtimeGamePackJson", { get() { throw new Error("secret-host-value"); } });
  await assert.rejects(
    prepareNpcDerivedState(hostile),
    (error) => error?.code === "NPC_DERIVED_STATE_INTERNAL_ERROR" && error.message === "NPC_DERIVED_STATE_INTERNAL_ERROR" && !String(error.stack).includes("secret-host-value"),
  );
});
