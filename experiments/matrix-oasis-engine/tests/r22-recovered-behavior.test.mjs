import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { createNpcAuthoritySession, exportNpcAuthoritySession, submitNpcAuthorityIntent } from "@matrix-oasis/npc-authority-session";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { enumerateEligibleNpcBehaviorCommands, prepareDeterministicNpcBehavior, selectEligibleNpcBehaviorCommand, selectNextNpcBehaviorCommand, synthesizeNpcBehaviorPolicy } from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { rebuildR22RecoveredBehavior } from "../scripts/lib/r22-recovered-behavior.mjs";

const BAD = /R22_RECOVERY_BEHAVIOR_INVALID/u;
const sha = (c) => `sha256:${c.repeat(64)}`;
async function fixture() {
  const authored = JSON.parse(await readFile(new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8"));
  const alternative = structuredClone(authored.nodes[0].actions[0]);
  alternative.id = "action-initialize-alternative"; alternative.label = "Initialize alternative";
  authored.nodes[0].actions.push(alternative);
  const compiled = await compileAuthoringGamePackJson(canonicalizeJsonValue(authored)); assert.equal(compiled.ok, true);
  const runtimeGamePackJson = compiled.canonicalJson, runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
  const grants = compiled.runtimePack.nodes.flatMap((node) => node.actions.map((action) => ({ nodeId: node.id, actionId: action.id })));
  const authorityPolicyJson = canonicalizeJsonValue({ format: "matrix-oasis.npc-authority-policy", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    id: "recovery-policy", contentVersion: "1", runtime: { format: compiled.runtimePack.format, formatVersion: compiled.runtimePack.formatVersion,
      id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion,
      sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`, artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
      receiptSha256: hashCanonicalValue(compiled.receipt) }, actorGrants: [{ actorEntityId: "actor-unit", grants }] });
  const behavior = synthesizeNpcBehaviorPolicy({ authorityPolicyJson }); assert.equal(behavior.ok, true);
  const entityBindingJson = canonicalizeJsonValue({ format: "matrix-oasis.npc-entity-binding", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    identities: { sceneBlueprintSha256: sha("a"), scenePackSha256: sha("b"), assetBundleSha256: sha("c"), spatialSolutionSha256: sha("d"), spatialVerificationSha256: sha("e"), authorityPolicySha256: behavior.npcBehaviorPolicy.authorityPolicySha256 },
    bindings: [{ actorEntityId: "actor-unit", assetBriefId: "brief", placementId: "placement", runtimeEntityId: "actor-unit", homeFloorAnchorId: "floor", homePositionMm: { x: 0, y: 0, z: 0 }, visibleNodeIds: compiled.runtimePack.nodes.map((node) => node.id) }] });
  const prepared = prepareDeterministicNpcBehavior({ behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson, entityBindingJson, authorityPolicyJson }); assert.equal(prepared.ok, true);
  const authority = await createNpcAuthoritySession({ runtimeGamePackJson, runtimeReceiptJson, policyJson: authorityPolicyJson, timelineId: "timeline-recovery", stepLimit: 16 }); assert.equal(authority.ok, true);
  let behaviorState = prepared.initialState; const commands = [];
  for (let index = 0; index < 2; index += 1) {
    const before = exportNpcAuthoritySession(authority.session);
    const base = { prepared: prepared.prepared, runtimeSnapshot: before.runtimeSnapshot, runtimeInspection: before.inspection, worldEventLedgerJson: before.canonicalWorldEventLedgerJson, behaviorState };
    let selected;
    if (index === 0) {
      const descriptor = enumerateEligibleNpcBehaviorCommands({ ...base, actorEntityId: "actor-unit", maximumCandidates: 64 }).candidates[1];
      selected = selectEligibleNpcBehaviorCommand({ ...base, actorEntityId: descriptor.actorEntityId, expectedIntentId: descriptor.intentId, expectedNpcIntentSha256: descriptor.npcIntentSha256 });
    } else selected = selectNextNpcBehaviorCommand(base);
    assert.equal(selected.status, "command");
    const submitted = submitNpcAuthorityIntent(authority.session, selected.command.npcIntentJson); assert.equal(submitted.ok, true);
    const result = JSON.parse(submitted.canonicalAdjudicationResultJson);
    commands.push({ sequence: selected.command.sequence, actorEntityId: selected.command.actorEntityId, ruleIndex: selected.command.ruleIndex,
      intentId: selected.command.intentId, nodeId: selected.command.nodeId, actionId: selected.command.actionId, state: result.decision.status,
      revisionStarted: index, revisionFinished: index + 1, movementTicks: 1, pathLengthMm: 1,
      arrivalEvidence: { pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 1, pathLengthMm: 1 },
      mirrorEvidence: { beforeSnapshotSha256: result.beforeSnapshotSha256, afterSnapshotSha256: result.afterSnapshotSha256, entityBindingSha256: sha("f"), commandSha256: sha("0") } });
    behaviorState = selected.nextBehaviorState;
  }
  return { source: { runtimeGamePackJson, runtimeReceiptJson, authorityPolicyJson }, preparedBehavior: prepared.prepared,
    initialBehaviorState: prepared.initialState, expectedBehaviorState: behaviorState,
    recovery: { canonicalWorldEventLedgerJson: exportNpcAuthoritySession(authority.session).canonicalWorldEventLedgerJson, behaviorTrace: { commands } } };
}

test("rebuild targets the recorded non-first choice and returns a reusable deeply frozen authority", async () => {
  const value = await fixture(), untouched = structuredClone(value.recovery);
  assert.equal(value.recovery.behaviorTrace.commands[0].actionId, "action-initialize-alternative");
  const rebuilt = await rebuildR22RecoveredBehavior(value);
  assert.equal(rebuilt.authority.canonicalWorldEventLedgerJson, value.recovery.canonicalWorldEventLedgerJson);
  assert.deepEqual(rebuilt.behaviorState, value.expectedBehaviorState);
  assert.deepEqual(rebuilt.commands, value.recovery.behaviorTrace.commands);
  assert.equal(Object.isFrozen(rebuilt), true); assert.equal(Object.isFrozen(rebuilt.authority), true); assert.ok(rebuilt.authority.session);
  assert.deepEqual(value.recovery, untouched);
});

test("rebuild rejects execution-limit drift, forged identity, order, duplication and mixed decisions", async () => {
  const attacks = [
    (v) => { v.initialBehaviorState = v.expectedBehaviorState; },
    (v) => { v.recovery.behaviorTrace.commands[0].ruleIndex += 1; },
    (v) => { v.recovery.behaviorTrace.commands[0].intentId += "-forged"; },
    (v) => { v.recovery.behaviorTrace.commands[0].actionId = "action-forged"; },
    (v) => { v.recovery.behaviorTrace.commands.reverse(); },
    (v) => { v.recovery.behaviorTrace.commands[1] = structuredClone(v.recovery.behaviorTrace.commands[0]); },
    (v) => { v.recovery.behaviorTrace.commands[0].state = "rejected"; },
  ];
  for (const attack of attacks) { const value = await fixture(); attack(value); await assert.rejects(rebuildR22RecoveredBehavior(value), BAD); }
});

test("rebuild rejects re-signed ledger intent and snapshot records under its one static code", async () => {
  const value = await fixture(), ledger = JSON.parse(value.recovery.canonicalWorldEventLedgerJson);
  ledger.entries[0].intent.id += "-forged";
  value.recovery.canonicalWorldEventLedgerJson = canonicalizeJsonValue(ledger);
  await assert.rejects(rebuildR22RecoveredBehavior(value), BAD);
  const mirror = await fixture(); mirror.recovery.behaviorTrace.commands[0].mirrorEvidence.afterSnapshotSha256 = sha("9");
  await assert.rejects(rebuildR22RecoveredBehavior(mirror), BAD);
});
