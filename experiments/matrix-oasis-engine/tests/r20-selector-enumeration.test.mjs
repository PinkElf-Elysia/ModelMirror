import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { createNpcAuthoritySession, exportNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import {
  enumerateEligibleNpcBehaviorCommands,
  prepareDeterministicNpcBehavior,
  selectEligibleNpcBehaviorCommand,
  selectNextNpcBehaviorCommand,
  synthesizeNpcBehaviorPolicy,
} from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const sha = (character) => `sha256:${character.repeat(64)}`;
const source = JSON.parse(await readFile(
  new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
  "utf8",
));
const alternative = structuredClone(source.nodes[0].actions[0]);
alternative.id = "action-initialize-alternative";
alternative.label = "Initialize alternative";
source.nodes[0].actions.push(alternative);
const compiled = await compileAuthoringGamePackJson(canonicalizeJsonValue(source));
assert.equal(compiled.ok, true);
const runtimeGamePackJson = compiled.canonicalJson;
const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
const authorityPolicyJson = canonicalizeJsonValue({
  format: "matrix-oasis.npc-authority-policy",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  id: "selector-enumeration-policy",
  contentVersion: "1",
  runtime: {
    format: compiled.runtimePack.format,
    formatVersion: compiled.runtimePack.formatVersion,
    id: compiled.runtimePack.source.id,
    contentVersion: compiled.runtimePack.source.contentVersion,
    sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
    artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
    receiptSha256: hashCanonicalValue(compiled.receipt),
  },
  actorGrants: [{
    actorEntityId: "actor-unit",
    grants: [
      { nodeId: "node-start", actionId: "action-initialize" },
      { nodeId: "node-start", actionId: "action-initialize-alternative" },
    ],
  }],
});
const behavior = synthesizeNpcBehaviorPolicy({ authorityPolicyJson });
assert.equal(behavior.ok, true);
const entityBindingJson = canonicalizeJsonValue({
  format: "matrix-oasis.npc-entity-binding",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  identities: {
    sceneBlueprintSha256: sha("a"),
    scenePackSha256: sha("b"),
    assetBundleSha256: sha("c"),
    spatialSolutionSha256: sha("d"),
    spatialVerificationSha256: sha("e"),
    authorityPolicySha256: behavior.npcBehaviorPolicy.authorityPolicySha256,
  },
  bindings: [{
    actorEntityId: "actor-unit",
    assetBriefId: "brief-one",
    placementId: "placement-one",
    runtimeEntityId: "actor-unit",
    homeFloorAnchorId: "floor-one",
    homePositionMm: { x: 0, y: 0, z: 0 },
    visibleNodeIds: ["node-start"],
  }],
});
const prepared = prepareDeterministicNpcBehavior({
  behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson,
  entityBindingJson,
  authorityPolicyJson,
});
assert.equal(prepared.ok, true);

async function state() {
  const authority = await createNpcAuthoritySession({
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson: authorityPolicyJson,
    timelineId: "timeline-selector-enumeration",
  });
  assert.equal(authority.ok, true);
  const exported = exportNpcAuthoritySession(authority.session);
  assert.equal(exported.ok, true);
  return {
    prepared: prepared.prepared,
    runtimeSnapshot: exported.runtimeSnapshot,
    runtimeInspection: exported.inspection,
    worldEventLedgerJson: exported.canonicalWorldEventLedgerJson,
    behaviorState: prepared.initialState,
  };
}

test("R20 enumeration is an identity-only view over the same selector core", async () => {
  const input = await state();
  const ordinary = selectNextNpcBehaviorCommand(input);
  const enumerated = enumerateEligibleNpcBehaviorCommands({
    ...input,
    actorEntityId: "actor-unit",
    maximumCandidates: 64,
  });
  assert.equal(enumerated.ok, true);
  assert.equal(enumerated.status, "candidates");
  assert.equal(enumerated.candidates.length, 2);
  assert.deepEqual(enumerated.candidates.map(({ actionId }) => actionId), [
    "action-initialize",
    "action-initialize-alternative",
  ]);
  for (const descriptor of enumerated.candidates) {
    const selected = selectEligibleNpcBehaviorCommand({
      ...input,
      actorEntityId: descriptor.actorEntityId,
      expectedIntentId: descriptor.intentId,
      expectedNpcIntentSha256: descriptor.npcIntentSha256,
    });
    assert.equal(selected.ok, true);
    assert.equal(selected.status, "command");
    assert.equal(selected.command.actionId, descriptor.actionId);
    assert.equal(hashCanonicalValue(JSON.parse(selected.command.npcIntentJson)), descriptor.npcIntentSha256);
  }
  const first = enumerated.candidates[0];
  const selectedFirst = selectEligibleNpcBehaviorCommand({
    ...input,
    actorEntityId: first.actorEntityId,
    expectedIntentId: first.intentId,
    expectedNpcIntentSha256: first.npcIntentSha256,
  });
  assert.deepEqual(selectedFirst, ordinary);
});

test("R20 selected-candidate identity and current-state drift fail closed", async () => {
  const input = await state();
  const descriptor = enumerateEligibleNpcBehaviorCommands({
    ...input,
    actorEntityId: "actor-unit",
    maximumCandidates: 64,
  }).candidates[1];
  for (const override of [
    { expectedIntentId: `${descriptor.intentId}-forged` },
    { expectedNpcIntentSha256: sha("f") },
    { runtimeInspection: { ...input.runtimeInspection, actions: [] } },
    { behaviorState: { nextSequence: input.behaviorState.nextSequence + 1, executions: [] } },
  ]) {
    const selected = selectEligibleNpcBehaviorCommand({
      ...input,
      actorEntityId: descriptor.actorEntityId,
      expectedIntentId: descriptor.intentId,
      expectedNpcIntentSha256: descriptor.npcIntentSha256,
      ...override,
    });
    assert.equal(selected.ok, false);
    assert(selected.diagnostics.some((item) => item.code === "NPC_BEHAVIOR_SELECTION_STALE"));
  }
});

test("R20 enumeration and targeted selection are byte stable across twenty runs", async () => {
  const input = await state();
  const outputs = Array.from({ length: 20 }, () => {
    const enumerated = enumerateEligibleNpcBehaviorCommands({
      ...input,
      actorEntityId: "actor-unit",
      maximumCandidates: 64,
    });
    const last = enumerated.candidates.at(-1);
    const selected = selectEligibleNpcBehaviorCommand({
      ...input,
      actorEntityId: last.actorEntityId,
      expectedIntentId: last.intentId,
      expectedNpcIntentSha256: last.npcIntentSha256,
    });
    return canonicalizeJsonValue({ enumerated, selected });
  });
  assert.equal(new Set(outputs).size, 1);
});
