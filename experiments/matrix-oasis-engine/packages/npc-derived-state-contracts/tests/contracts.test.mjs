import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import * as api from "../src/index.mjs";

const sha = (hex) => `sha256:${hex.repeat(64)}`;
const authority = () => ({ runtimePackSha256: sha("a"), runtimeReceiptSha256: sha("b"), authorityPolicySha256: sha("c"), npcEntityBindingSha256: sha("f") });
const ledger = () => ({ timelineId: "timeline-one", canonicalSha256: sha("d"), throughRevision: 2, throughHeadSha256: sha("e") });
const reducer = (id) => ({ id, version: "1.0.0", sourceSha256: sha(id === "memory-reducer" ? "1" : "2") });
const profile = () => ({
  timelineMode: "single", authorityMode: "runtime-and-ledger-only", personaMode: "trusted-static-seed",
  memoryScope: "actor-self-accepted-actions", relationshipScope: "accepted-explicit-policy-rules",
  deletionMode: "whole-derived-state", selectiveForgetting: false, externalModelCalls: false, semanticRetrieval: false,
});
const persona = () => ({
  format: "matrix-oasis.npc-persona-seed", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
  id: "persona-one", contentVersion: "1.0.0", authority: authority(), traitIds: ["curiosity", "empathy"],
  actors: [
    { actorEntityId: "actor-one", traits: [{ traitId: "curiosity", value: 1000 }, { traitId: "empathy", value: -1000 }] },
    { actorEntityId: "actor-two", traits: [{ traitId: "curiosity", value: 0 }, { traitId: "empathy", value: 250 }] },
  ],
});
const relationshipPolicy = () => ({
  format: "matrix-oasis.npc-relationship-projection-policy", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
  id: "relationship-policy-one", contentVersion: "1.0.0", authority: authority(), personaSeedSha256: sha("3"),
  repeatMode: "first-accepted-per-rule-actor-target-timeline",
  rules: [
    { ruleId: "rule-one", sourceActorEntityId: "actor-one", targetEntityId: "actor-two", nodeId: "node-one", actionId: "action-one", dimensionId: "trust", delta: 25 },
    { ruleId: "rule-two", sourceActorEntityId: "actor-two", targetEntityId: "actor-one", nodeId: "node-two", actionId: "action-two", dimensionId: "respect", delta: -10 },
  ],
});
const memory = () => ({
  format: "matrix-oasis.npc-memory-projection", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
  authority: authority(), personaSeedSha256: sha("3"), ledger: ledger(), reducer: reducer("memory-reducer"),
  scopeActorEntityIds: ["actor-one", "actor-two"],
  episodes: [{
    episodeId: "episode-one", actorEntityId: "actor-one", intentId: "intent-one", revision: 1, entrySha256: sha("4"),
    beforeSnapshotSha256: sha("5"), afterSnapshotSha256: sha("6"), interactionEntityIds: ["object-one", "object-two"],
    transition: { transitionVersion: 1, step: 1, from: { kind: "node", index: 0, id: "node-one" }, actionId: "action-one", to: { kind: "node", index: 1, id: "node-two" } },
  }],
});
const relationships = () => ({
  format: "matrix-oasis.npc-relationship-projection", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
  authority: authority(), personaSeedSha256: sha("3"), relationshipPolicySha256: sha("7"), ledger: ledger(), reducer: reducer("relationship-reducer"),
  scopeActorEntityIds: ["actor-one", "actor-two"],
  relationships: [
    { sourceActorEntityId: "actor-one", targetEntityId: "actor-two", dimensionId: "trust", value: 25, contributions: [{ ruleId: "rule-one", revision: 1, entrySha256: sha("4"), delta: 25 }] },
    { sourceActorEntityId: "actor-two", targetEntityId: "actor-one", dimensionId: "respect", value: 0, contributions: [] },
  ],
});
const artifact = (format, hex) => ({ format, canonicalSha256: sha(hex), byteLength: 128 });
const bundle = () => ({
  format: "matrix-oasis.npc-derived-state-bundle", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
  source: { r20CurrentSha256: sha("1"), r20AuthorityManifestSha256: sha("2"), r20QualificationReceiptSha256: sha("3"), npcEntityBindingSha256: sha("4") },
  authority: authority(), ledger: ledger(),
  replay: { reportSha256: sha("5"), finalSnapshotSha256: sha("6"), finalInspectionSha256: sha("7") },
  reducers: { memory: reducer("memory-reducer"), relationship: reducer("relationship-reducer") }, profile: profile(),
  artifacts: {
    personaSeed: artifact("matrix-oasis.npc-persona-seed", "1"),
    relationshipPolicy: artifact("matrix-oasis.npc-relationship-projection-policy", "2"),
    memoryProjection: artifact("matrix-oasis.npc-memory-projection", "3"),
    relationshipProjection: artifact("matrix-oasis.npc-relationship-projection", "4"),
    memoryManifest: artifact("matrix-oasis.derived-projection-manifest", "5"),
    relationshipManifest: artifact("matrix-oasis.derived-projection-manifest", "6"),
  },
});
const rebuild = () => ({ personaSeedSha256: sha("1"), relationshipPolicySha256: sha("2"), replayReportSha256: sha("5"), memoryProjectionSha256: sha("8"), relationshipProjectionSha256: sha("9"), memoryManifestSha256: sha("a"), relationshipManifestSha256: sha("b"), bundleSha256: sha("7") });
const qualification = () => ({
  format: "matrix-oasis.npc-projection-qualification-report", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
  qualifiedBundleSha256: sha("7"), ledger: ledger(), profile: profile(),
  rebuilds: { initial: rebuild(), repeated: rebuild(), afterDeletion: rebuild(), repeatedBuildCount: 20 },
  deletion: { mode: "whole-derived-state", derivedArtifactsRemoved: true, runtimeSnapshotSha256Before: sha("c"), runtimeSnapshotSha256After: sha("c"), ledgerSha256Before: sha("d"), ledgerSha256After: sha("d") },
  counts: { ledgerEntries: 2, acceptedEntries: 1, rejectedEntries: 1, memoryEpisodes: 1, relationshipEdges: 2, relationshipContributions: 1 },
  isolation: { externalModelCalls: 0, networkRequests: 0, credentialReads: 0 },
  markers: ["R21_LEDGER_REBUILD_EQUIVALENT", "R21_MEMORY_DELETION_VERIFIED", "R21_RELATIONSHIP_PROJECTION_DETERMINISTIC"],
});
const cases = [
  [api.validateNpcPersonaSeedJson, persona],
  [api.validateNpcRelationshipProjectionPolicyJson, relationshipPolicy],
  [api.validateNpcMemoryProjectionJson, memory],
  [api.validateNpcRelationshipProjectionJson, relationships],
  [api.validateNpcDerivedStateBundleJson, bundle],
  [api.validateNpcProjectionQualificationReportJson, qualification],
];

test("R21 exposes only the approved contract names and formats", () => {
  assert.equal(api.NPC_PERSONA_SEED_FORMAT, "matrix-oasis.npc-persona-seed");
  assert.equal(api.NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT, "matrix-oasis.npc-relationship-projection-policy");
  assert.equal(api.NPC_PROJECTION_QUALIFICATION_REPORT_FORMAT, "matrix-oasis.npc-projection-qualification-report");
  assert.equal(api.validateNpcPersonaJson, undefined);
  assert.equal(api.validateNpcRelationshipPolicyJson, undefined);
  assert.equal(api.validateNpcDerivedStateQualificationReportJson, undefined);
});
test("all schemas are closed, frozen and canonical fixtures validate", () => {
  for (const schema of [api.NPC_PERSONA_SEED_SCHEMA, api.NPC_RELATIONSHIP_PROJECTION_POLICY_SCHEMA, api.NPC_MEMORY_PROJECTION_SCHEMA, api.NPC_RELATIONSHIP_PROJECTION_SCHEMA, api.NPC_DERIVED_STATE_BUNDLE_SCHEMA, api.NPC_PROJECTION_QUALIFICATION_REPORT_SCHEMA]) {
    assert.equal(schema.additionalProperties, false); assert.equal(Object.isFrozen(schema), true);
  }
  for (const [validate, fixture] of cases) assert.equal(validate(canonicalizeJsonValue(fixture())).valid, true);
});
test("every R21 authority identity is closed over the R20 entity binding", () => {
  for (const [validate, fixture] of [
    [api.validateNpcPersonaSeedJson, persona],
    [api.validateNpcRelationshipProjectionPolicyJson, relationshipPolicy],
    [api.validateNpcMemoryProjectionJson, memory],
    [api.validateNpcRelationshipProjectionJson, relationships],
    [api.validateNpcDerivedStateBundleJson, bundle],
  ]) {
    const value = fixture(); delete value.authority.npcEntityBindingSha256;
    assert.equal(validate(canonicalizeJsonValue(value)).valid, false);
  }
  const sourceMissing = bundle(); delete sourceMissing.source.npcEntityBindingSha256;
  assert.equal(api.validateNpcDerivedStateBundleJson(canonicalizeJsonValue(sourceMissing)).valid, false);
});
test("unknown fields, duplicate keys, floats and noncanonical bytes fail closed", () => {
  for (const [validate, fixture] of cases) {
    const unknown = fixture(); unknown.unknown = true;
    assert.equal(validate(canonicalizeJsonValue(unknown)).valid, false);
  }
  assert.match(api.validateNpcPersonaSeedJson('{"a":1,"a":2}').diagnostics[0].code, /DUPLICATE_KEY/);
  const floating = [
    [api.validateNpcPersonaSeedJson, persona, (v) => { v.actors[0].traits[0].value = 0.5; }],
    [api.validateNpcRelationshipProjectionPolicyJson, relationshipPolicy, (v) => { v.rules[0].delta = 0.5; }],
    [api.validateNpcMemoryProjectionJson, memory, (v) => { v.episodes[0].revision = 1.5; }],
    [api.validateNpcRelationshipProjectionJson, relationships, (v) => { v.relationships[0].value = 25.5; }],
    [api.validateNpcDerivedStateBundleJson, bundle, (v) => { v.artifacts.personaSeed.byteLength = 1.5; }],
    [api.validateNpcProjectionQualificationReportJson, qualification, (v) => { v.counts.ledgerEntries = 1.5; }],
  ];
  for (const [validate, fixture, mutate] of floating) { const value = fixture(); mutate(value); assert.equal(validate(JSON.stringify(value)).valid, false); }
  assert.match(api.validateNpcPersonaSeedJson(JSON.stringify(persona(), null, 2)).diagnostics[0].code, /NON_CANONICAL/);
});
test("persona requires a sorted 1-16 trait vector fully covered by 1-64 actors", () => {
  assert.equal(api.NPC_DERIVED_STATE_LIMITS.actors, 64);
  assert.equal(api.NPC_DERIVED_STATE_LIMITS.traitIds, 16);
  assert.equal(api.NPC_DERIVED_STATE_LIMITS.ledgerEntries, 10000);
  assert.equal(api.NPC_DERIVED_STATE_LIMITS.memoryProjectionBytes, 16 * 1024 * 1024);
  for (const mutate of [
    (v) => { v.traitIds = []; v.actors[0].traits = []; v.actors[1].traits = []; },
    (v) => { v.actors = []; },
    (v) => { v.traitIds.reverse(); },
    (v) => { v.actors.reverse(); },
    (v) => { v.actors[0].traits.pop(); },
    (v) => { v.actors[0].traits[0].traitId = "empathy"; },
    (v) => { v.actors[0].traits[0].value = 1001; },
  ]) { const value = persona(); mutate(value); assert.equal(api.validateNpcPersonaSeedJson(canonicalizeJsonValue(value)).valid, false); }
  const overActors = persona(); overActors.actors = Array.from({ length: 65 }, (_, index) => ({ actorEntityId: `actor-${String(index).padStart(2, "0")}`, traits: [{ traitId: "curiosity", value: 0 }, { traitId: "empathy", value: 0 }] }));
  assert.equal(api.validateNpcPersonaSeedJson(canonicalizeJsonValue(overActors)).valid, false);
});
test("every semantic array has one deterministic accepted order", () => {
  const policy = relationshipPolicy(); policy.rules.reverse();
  assert(policy && api.validateNpcRelationshipProjectionPolicyJson(canonicalizeJsonValue(policy)).diagnostics.some(({ code }) => code === "NPC_RELATIONSHIP_POLICY_RULE_ORDER"));
  const projectedMemory = memory(); projectedMemory.scopeActorEntityIds.reverse();
  assert(projectedMemory && api.validateNpcMemoryProjectionJson(canonicalizeJsonValue(projectedMemory)).diagnostics.some(({ code }) => code === "NPC_MEMORY_SCOPE_ORDER"));
  const episodeOrder = memory(); episodeOrder.episodes.push({ ...structuredClone(episodeOrder.episodes[0]), episodeId: "episode-two", actorEntityId: "actor-two", intentId: "intent-two", revision: 2, entrySha256: sha("8"), transition: { ...structuredClone(episodeOrder.episodes[0].transition), step: 2 } }); episodeOrder.episodes.reverse();
  assert(episodeOrder && api.validateNpcMemoryProjectionJson(canonicalizeJsonValue(episodeOrder)).diagnostics.some(({ code }) => code === "NPC_MEMORY_EPISODE_ORDER"));
  const projectedRelationships = relationships(); projectedRelationships.scopeActorEntityIds.reverse(); projectedRelationships.relationships.reverse();
  const relationshipCodes = api.validateNpcRelationshipProjectionJson(canonicalizeJsonValue(projectedRelationships)).diagnostics.map(({ code }) => code);
  assert(relationshipCodes.includes("NPC_RELATIONSHIP_SCOPE_ORDER")); assert(relationshipCodes.includes("NPC_RELATIONSHIP_EDGE_ORDER"));
  const contributionOrder = relationships(); contributionOrder.relationships[0].value = 15; contributionOrder.relationships[0].contributions.push({ ruleId: "rule-three", revision: 2, entrySha256: sha("8"), delta: -10 }); contributionOrder.relationships[0].contributions.reverse();
  assert(contributionOrder && api.validateNpcRelationshipProjectionJson(canonicalizeJsonValue(contributionOrder)).diagnostics.some(({ code }) => code === "NPC_RELATIONSHIP_CONTRIBUTION_ORDER"));
});
test("relationship policy rejects ambiguous, zero, out-of-range and theoretically overflowing rules", () => {
  const zero = relationshipPolicy(); zero.rules[0].delta = 0;
  assert(zero && api.validateNpcRelationshipProjectionPolicyJson(canonicalizeJsonValue(zero)).diagnostics.some(({ code }) => code === "NPC_RELATIONSHIP_POLICY_ZERO_DELTA_FORBIDDEN"));
  const tooLarge = relationshipPolicy(); tooLarge.rules[0].delta = 101;
  assert.equal(api.validateNpcRelationshipProjectionPolicyJson(canonicalizeJsonValue(tooLarge)).valid, false);
  const duplicate = relationshipPolicy(); duplicate.rules.push({ ...structuredClone(duplicate.rules[0]), ruleId: "rule-three" }); duplicate.rules.sort((a, b) => a.ruleId.localeCompare(b.ruleId));
  assert( api.validateNpcRelationshipProjectionPolicyJson(canonicalizeJsonValue(duplicate)).diagnostics.some(({ code }) => code === "NPC_RELATIONSHIP_POLICY_RULE_TUPLE_DUPLICATE"));
  const overflowing = relationshipPolicy(); overflowing.rules = Array.from({ length: 11 }, (_, index) => ({ ruleId: `rule-${String(index).padStart(2, "0")}`, sourceActorEntityId: "actor-one", targetEntityId: "actor-two", nodeId: `node-${String(index).padStart(2, "0")}`, actionId: "action-one", dimensionId: "trust", delta: 100 }));
  assert( api.validateNpcRelationshipProjectionPolicyJson(canonicalizeJsonValue(overflowing)).diagnostics.some(({ code }) => code === "NPC_RELATIONSHIP_POLICY_THEORETICAL_RANGE_EXCEEDED"));
  const overRules = relationshipPolicy(); overRules.rules = Array.from({ length: 4097 }, (_, index) => ({ ruleId: `rule-${String(index).padStart(4, "0")}`, sourceActorEntityId: "actor-one", targetEntityId: "actor-two", nodeId: `node-${String(index).padStart(4, "0")}`, actionId: "action-one", dimensionId: `dimension-${String(index).padStart(4, "0")}`, delta: 1 }));
  assert.equal(api.validateNpcRelationshipProjectionPolicyJson(canonicalizeJsonValue(overRules)).valid, false);
});
test("memory episodes preserve accepted action provenance but cannot carry cue or free-text payload", () => {
  const cue = memory(); cue.episodes[0].transition.emittedCues = [];
  assert.equal(api.validateNpcMemoryProjectionJson(canonicalizeJsonValue(cue)).valid, false);
  const unordered = memory(); unordered.episodes[0].interactionEntityIds.reverse();
  assert(unordered && api.validateNpcMemoryProjectionJson(canonicalizeJsonValue(unordered)).diagnostics.some(({ code }) => code === "NPC_MEMORY_INTERACTION_ENTITY_ORDER"));
  const outOfScope = memory(); outOfScope.episodes[0].actorEntityId = "actor-three";
  assert.equal(api.validateNpcMemoryProjectionJson(canonicalizeJsonValue(outOfScope)).valid, false);
  const future = memory(); future.episodes[0].revision = 3;
  assert.equal(api.validateNpcMemoryProjectionJson(canonicalizeJsonValue(future)).valid, false);
  const emptyScope = memory(); emptyScope.scopeActorEntityIds = [];
  assert.equal(api.validateNpcMemoryProjectionJson(canonicalizeJsonValue(emptyScope)).valid, false);
});
test("relationship projection retains declared zero edges and scopes rule replay per edge", () => {
  assert.equal(api.validateNpcRelationshipProjectionJson(canonicalizeJsonValue(relationships())).valid, true);
  const sameRuleDifferentEdge = relationships();
  sameRuleDifferentEdge.relationships[1].value = -10;
  sameRuleDifferentEdge.relationships[1].contributions = [{ ruleId: "rule-one", revision: 2, entrySha256: sha("5"), delta: -10 }];
  assert.equal(api.validateNpcRelationshipProjectionJson(canonicalizeJsonValue(sameRuleDifferentEdge)).valid, true);
  const reapplied = relationships(); reapplied.relationships[0].value = 50; reapplied.relationships[0].contributions.push(structuredClone(reapplied.relationships[0].contributions[0]));
  assert(reapplied && api.validateNpcRelationshipProjectionJson(canonicalizeJsonValue(reapplied)).diagnostics.some(({ code }) => code === "NPC_RELATIONSHIP_RULE_REAPPLIED"));
  const mismatch = relationships(); mismatch.relationships[0].value = 24;
  assert(mismatch && api.validateNpcRelationshipProjectionJson(canonicalizeJsonValue(mismatch)).diagnostics.some(({ code }) => code === "NPC_RELATIONSHIP_AGGREGATE_MISMATCH"));
  const emptyScope = relationships(); emptyScope.scopeActorEntityIds = [];
  assert.equal(api.validateNpcRelationshipProjectionJson(canonicalizeJsonValue(emptyScope)).valid, false);
});
test("bundle binds the complete R20, R19, reducer and six-artifact identity surface", () => {
  const missing = bundle(); delete missing.source.r20QualificationReceiptSha256;
  assert.equal(api.validateNpcDerivedStateBundleJson(canonicalizeJsonValue(missing)).valid, false);
  const implicit = bundle(); implicit.artifacts.memoryProjection.projectionManifestSha256 = sha("f");
  assert.equal(api.validateNpcDerivedStateBundleJson(canonicalizeJsonValue(implicit)).valid, false);
  const wrongFormat = bundle(); wrongFormat.artifacts.memoryManifest.format = "application/json";
  assert.equal(api.validateNpcDerivedStateBundleJson(canonicalizeJsonValue(wrongFormat)).valid, false);
});
test("qualification proves repeat and whole-delete rebuild equality without inventing incremental semantics", () => {
  assert.equal(Object.hasOwn(qualification().rebuilds, "incremental"), false);
  assert.deepEqual(Object.keys(qualification().rebuilds.initial).sort(), ["bundleSha256", "memoryManifestSha256", "memoryProjectionSha256", "personaSeedSha256", "relationshipManifestSha256", "relationshipPolicySha256", "relationshipProjectionSha256", "replayReportSha256"]);
  const repeatMismatch = qualification(); repeatMismatch.rebuilds.repeated.memoryProjectionSha256 = sha("f");
  assert(repeatMismatch && api.validateNpcProjectionQualificationReportJson(canonicalizeJsonValue(repeatMismatch)).diagnostics.some(({ code }) => code === "NPC_PROJECTION_REPEAT_REBUILD_MISMATCH"));
  const inputMismatch = qualification(); inputMismatch.rebuilds.afterDeletion.personaSeedSha256 = sha("f");
  assert(inputMismatch && api.validateNpcProjectionQualificationReportJson(canonicalizeJsonValue(inputMismatch)).diagnostics.some(({ code }) => code === "NPC_PROJECTION_POST_DELETION_REBUILD_MISMATCH"));
  const deletionMismatch = qualification(); deletionMismatch.deletion.runtimeSnapshotSha256After = sha("f");
  assert(deletionMismatch && api.validateNpcProjectionQualificationReportJson(canonicalizeJsonValue(deletionMismatch)).diagnostics.some(({ code }) => code === "NPC_PROJECTION_DELETION_CHANGED_RUNTIME"));
  const markerOrder = qualification(); markerOrder.markers.reverse();
  assert(markerOrder && api.validateNpcProjectionQualificationReportJson(canonicalizeJsonValue(markerOrder)).diagnostics.some(({ code }) => code === "NPC_PROJECTION_QUALIFICATION_MARKER_ORDER"));
});
test("depth, byte and canonical report outputs are deterministic", () => {
  const deep = `${"[".repeat(257)}0${"]".repeat(257)}`;
  assert.match(api.validateNpcPersonaSeedJson(deep).diagnostics[0].code, /DEPTH_EXCEEDED/);
  const large = `{"x":"${"a".repeat(api.NPC_DERIVED_STATE_LIMITS.personaBytes)}"}`;
  assert.match(api.validateNpcPersonaSeedJson(large).diagnostics[0].code, /SIZE_EXCEEDED/);
  const input = canonicalizeJsonValue(qualification());
  const reports = Array.from({ length: 20 }, () => canonicalizeJsonValue(api.validateNpcProjectionQualificationReportJson(input)));
  assert.equal(new Set(reports).size, 1);
});
