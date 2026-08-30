import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  deriveR20QualificationCoverageRequirement,
  evaluateR20QualificationCoverage,
  validateR20QualificationCoverageEvidence,
  validateR20QualificationCoverageRequirement,
} from "../scripts/lib/r20-qualification-coverage.mjs";

const sha = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const hashLabel = (value) => sha(Buffer.from(value));
function authoring(kind) {
  const ending = { id: "ending-complete", title: "Complete", cueIds: [] };
  const action = (id, target) => ({ id, label: id, entityIds: [], effects: [], target });
  let nodes;
  if (kind === "multi") nodes = [
    { id: "node-alpha", title: "Alpha", entityIds: [], entryCueIds: [], actions: [action("action-forward", { kind: "node", id: "node-beta" })] },
    { id: "node-beta", title: "Beta", entityIds: [], entryCueIds: [], actions: [action("action-back", { kind: "node", id: "node-alpha" }), action("action-finish", { kind: "ending", id: ending.id })] },
  ];
  else if (kind === "self") nodes = [
    { id: "node-alpha", title: "Alpha", entityIds: [], entryCueIds: [], actions: [action("action-repeat", { kind: "node", id: "node-alpha" }), action("action-finish", { kind: "ending", id: ending.id })] },
  ];
  else nodes = [{ id: "node-alpha", title: "Alpha", entityIds: [], entryCueIds: [], actions: [action("action-finish", { kind: "ending", id: ending.id })] }];
  return canonicalizeJsonValue({
    format: "matrix-oasis.authoring-game-pack", formatVersion: "0.1.0", id: `coverage-${kind}`,
    contentVersion: "1", language: "en", title: "Coverage Fixture", entryNodeId: "node-alpha",
    entities: [], variables: [], cues: [], nodes, endings: [ending],
  });
}
const compiled = Object.fromEntries(await Promise.all(["multi", "self", "acyclic"].map(async (kind) => {
  const result = await compileAuthoringGamePackJson(authoring(kind));
  assert.equal(result.ok, true);
  return [kind, result];
})));

function ledgerAndTrace(result, transitionSpecs) {
  const runtime = result.runtimePack;
  const entries = [];
  let previousEntrySha256 = null;
  let previousSnapshotSha256 = hashLabel("snapshot-initial");
  let acceptedStep = 0;
  for (let index = 0; index < transitionSpecs.length; index += 1) {
    const spec = transitionSpecs[index];
    const revision = index + 1;
    const intent = {
      format: "matrix-oasis.npc-intent", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
      id: `intent-${String(revision).padStart(3, "0")}`, actorEntityId: "actor-unit", timelineId: "timeline-coverage",
      nodeId: spec.from, actionId: spec.actionId,
      observed: { revision: revision - 1, headSha256: previousEntrySha256, runtimeSnapshotSha256: previousSnapshotSha256 },
    };
    const afterSnapshotSha256 = spec.accepted === false ? previousSnapshotSha256 : hashLabel(`snapshot-${revision}`);
    let transition = null;
    if (spec.accepted !== false) {
      acceptedStep += 1;
      const fromIndex = runtime.nodes.findIndex((node) => node.id === spec.from);
      const toIndex = spec.toKind === "ending" ? runtime.endings.findIndex((ending) => ending.id === spec.to) : runtime.nodes.findIndex((node) => node.id === spec.to);
      transition = { transitionVersion: 1, step: acceptedStep, from: { kind: "node", index: fromIndex, id: spec.from }, actionId: spec.actionId, to: { kind: spec.toKind, index: toIndex, id: spec.to }, emittedCues: [] };
    }
    const body = {
      revision, intent,
      decision: spec.accepted === false ? { status: "rejected", reason: "NPC_INTENT_ACTOR_UNAUTHORIZED" } : { status: "accepted", reason: "NPC_INTENT_ACCEPTED" },
      beforeSnapshotSha256: previousSnapshotSha256, afterSnapshotSha256, transition, previousEntrySha256,
    };
    const entry = { ...body, entrySha256: hashCanonicalValue(body) };
    entries.push(entry);
    previousEntrySha256 = entry.entrySha256;
    previousSnapshotSha256 = afterSnapshotSha256;
  }
  const runtimePackSha256 = sha(result.canonicalJson);
  const ledger = {
    format: "matrix-oasis.world-event-ledger", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    timeline: { id: "timeline-coverage", stepLimit: 100 },
    authority: {
      runtime: {
        format: runtime.format, formatVersion: runtime.formatVersion, id: runtime.source.id, contentVersion: runtime.source.contentVersion,
        sourceSha256: `sha256:${runtime.source.canonicalSha256}`, artifactSha256: runtimePackSha256, receiptSha256: hashCanonicalValue(result.receipt),
      },
      policy: { id: "policy-coverage", contentVersion: "1", canonicalSha256: hashLabel("policy") },
      initialSnapshotSha256: hashLabel("snapshot-initial"),
    },
    revision: entries.length, headSha256: previousEntrySha256, entries,
  };
  const entityBindingSha256 = hashLabel("binding");
  const commands = entries.map((entry, index) => {
    const commandIdentity = {
      sequence: index + 1,
      actorEntityId: entry.intent.actorEntityId,
      ruleIndex: 0,
      intentId: entry.intent.id,
      nodeId: entry.intent.nodeId,
      actionId: entry.intent.actionId,
    };
    return {
      ...commandIdentity, state: entry.decision.status,
      revisionStarted: entry.revision - 1, revisionFinished: entry.revision, movementTicks: 1, pathLengthMm: 1,
      arrivalEvidence: { pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 1, pathLengthMm: 1 },
      mirrorEvidence: {
        beforeSnapshotSha256: entry.beforeSnapshotSha256,
        afterSnapshotSha256: entry.afterSnapshotSha256,
        entityBindingSha256,
        commandSha256: sha(canonicalizeJsonValue(commandIdentity)),
      },
    };
  });
  const trace = {
    format: "matrix-oasis.npc-behavior-trace", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    timelineId: ledger.timeline.id, behaviorPolicySha256: hashLabel("behavior"), entityBindingSha256,
    commands, finalRevision: ledger.revision, finalHeadSha256: ledger.headSha256,
    terminalState: entries.some((entry) => entry.decision.status === "accepted" && entry.transition.to.kind === "ending") ? "ended" : "quiescent",
  };
  return { worldEventLedgerJson: canonicalizeJsonValue(ledger), behaviorTraceJson: canonicalizeJsonValue(trace) };
}

test("runtime graph requirement prefers a productive multi-node loop, then self-loop, then no loop", () => {
  const requirements = ["multi", "self", "acyclic"].map((kind) => deriveR20QualificationCoverageRequirement(compiled[kind].canonicalJson));
  assert.deepEqual(requirements.map((item) => item.requirement.loopRequirement), [
    { required: true, minimumDistinctNodes: 2 },
    { required: true, minimumDistinctNodes: 1 },
    { required: false, minimumDistinctNodes: 0 },
  ]);
  for (const result of requirements) {
    assert.equal(result.requirementSha256, sha(result.canonicalRequirementJson));
    assert.equal(Object.isFrozen(result.requirement.loopRequirement), true);
    assert.deepEqual(validateR20QualificationCoverageRequirement(result.canonicalRequirementJson), result.requirement);
  }
  const unproductive = structuredClone(compiled.acyclic.runtimePack);
  unproductive.nodes[0].actions.push({ id: "action-detour", label: "Detour", entityIndexes: [], when: null, effects: [], target: { kind: "node", index: 1 } });
  unproductive.nodes.push({ id: "node-dead-loop", title: "Dead loop", text: null, entityIndexes: [], entryCueIndexes: [], actions: [{ id: "action-repeat", label: "Repeat", entityIndexes: [], when: null, effects: [], target: { kind: "node", index: 1 } }] });
  assert.deepEqual(deriveR20QualificationCoverageRequirement(canonicalizeJsonValue(unproductive)).requirement.loopRequirement, { required: false, minimumDistinctNodes: 0 });
  const repeated = Array.from({ length: 20 }, () => deriveR20QualificationCoverageRequirement(compiled.multi.canonicalJson).canonicalRequirementJson);
  assert.equal(new Set(repeated).size, 1);
});

test("coverage evidence is recomputed from accepted transitions and records the first actual multi-node revisit", () => {
  const requirement = deriveR20QualificationCoverageRequirement(compiled.multi.canonicalJson).requirement;
  const artifacts = ledgerAndTrace(compiled.multi, [
    { from: "node-alpha", actionId: "action-forward", toKind: "node", to: "node-beta" },
    { from: "node-beta", actionId: "action-back", toKind: "node", to: "node-alpha" },
    { from: "node-alpha", actionId: "action-forward", toKind: "node", to: "node-beta" },
    { from: "node-beta", actionId: "action-finish", toKind: "ending", to: "ending-complete" },
  ]);
  const result = evaluateR20QualificationCoverage({ requirement, ...artifacts });
  assert.deepEqual(result.evidence.satisfied, ["ending", "loop"]);
  assert.equal(result.evidence.endingRevision, 4);
  assert.deepEqual(result.evidence.loopWitness, { firstVisitRevision: 0, repeatVisitRevision: 2, distinctNodeCount: 2 });
  assert.equal(result.canonicalEvidenceJson, canonicalizeJsonValue(result.evidence));
  assert.equal(Object.isFrozen(result.evidence.loopWitness), true);
  assert.equal(new Set(Array.from({ length: 20 }, () => evaluateR20QualificationCoverage({ requirement, ...artifacts }).canonicalEvidenceJson)).size, 1);
});

test("rejected intents do not satisfy a required loop and missing runtime coverage fails distinctly", () => {
  const requirement = deriveR20QualificationCoverageRequirement(compiled.self.canonicalJson).requirement;
  const artifacts = ledgerAndTrace(compiled.self, [
    { from: "node-alpha", actionId: "action-repeat", accepted: false },
    { from: "node-alpha", actionId: "action-finish", toKind: "ending", to: "ending-complete" },
  ]);
  assert.throws(() => evaluateR20QualificationCoverage({ requirement, ...artifacts }), { code: "R20_QUALIFICATION_COVERAGE_INCOMPLETE" });
});

test("acyclic qualification proves the ending without inventing a loop witness", () => {
  const requirement = deriveR20QualificationCoverageRequirement(compiled.acyclic.canonicalJson).requirement;
  const artifacts = ledgerAndTrace(compiled.acyclic, [{ from: "node-alpha", actionId: "action-finish", toKind: "ending", to: "ending-complete" }]);
  const result = evaluateR20QualificationCoverage({ requirement, ...artifacts });
  assert.deepEqual(result.evidence.satisfied, ["ending"]);
  assert.equal(result.evidence.loopWitness, null);
  assert.equal(validateR20QualificationCoverageEvidence(result.canonicalEvidenceJson, requirement).endingRevision, 1);
});

test("unknown fields, identity drift and trace-to-ledger drift are malformed rather than incomplete", () => {
  const requirement = deriveR20QualificationCoverageRequirement(compiled.acyclic.canonicalJson).requirement;
  assert.throws(() => validateR20QualificationCoverageRequirement({ ...requirement, unknown: true }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });
  const artifacts = ledgerAndTrace(compiled.acyclic, [{ from: "node-alpha", actionId: "action-finish", toKind: "ending", to: "ending-complete" }]);
  const evidence = evaluateR20QualificationCoverage({ requirement, ...artifacts }).evidence;
  assert.throws(() => validateR20QualificationCoverageEvidence({ ...evidence, loopWitness: { firstVisitRevision: 0, repeatVisitRevision: 1, distinctNodeCount: 1 } }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });
  assert.throws(() => validateR20QualificationCoverageEvidence({ ...evidence, satisfied: ["ending", "loop"] }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });
  const trace = JSON.parse(artifacts.behaviorTraceJson);
  trace.commands[0].intentId = "intent-forged";
  assert.throws(() => evaluateR20QualificationCoverage({ requirement, worldEventLedgerJson: artifacts.worldEventLedgerJson, behaviorTraceJson: canonicalizeJsonValue(trace) }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });
  const ledger = JSON.parse(artifacts.worldEventLedgerJson);
  ledger.authority.runtime.artifactSha256 = hashLabel("different-runtime");
  assert.throws(() => evaluateR20QualificationCoverage({ requirement, worldEventLedgerJson: canonicalizeJsonValue(ledger), behaviorTraceJson: artifacts.behaviorTraceJson }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });
});

test("coverage rejects actor, entity binding and canonical command identity drift", () => {
  const requirement = deriveR20QualificationCoverageRequirement(compiled.acyclic.canonicalJson).requirement;
  const artifacts = ledgerAndTrace(compiled.acyclic, [{ from: "node-alpha", actionId: "action-finish", toKind: "ending", to: "ending-complete" }]);

  const actorTrace = JSON.parse(artifacts.behaviorTraceJson);
  actorTrace.commands[0].actorEntityId = "actor-forged";
  const actorIdentity = {
    sequence: actorTrace.commands[0].sequence,
    actorEntityId: actorTrace.commands[0].actorEntityId,
    ruleIndex: actorTrace.commands[0].ruleIndex,
    intentId: actorTrace.commands[0].intentId,
    nodeId: actorTrace.commands[0].nodeId,
    actionId: actorTrace.commands[0].actionId,
  };
  actorTrace.commands[0].mirrorEvidence.commandSha256 = sha(canonicalizeJsonValue(actorIdentity));
  assert.throws(() => evaluateR20QualificationCoverage({ requirement, worldEventLedgerJson: artifacts.worldEventLedgerJson, behaviorTraceJson: canonicalizeJsonValue(actorTrace) }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });

  const bindingTrace = JSON.parse(artifacts.behaviorTraceJson);
  bindingTrace.commands[0].mirrorEvidence.entityBindingSha256 = hashLabel("forged-binding");
  assert.throws(() => evaluateR20QualificationCoverage({ requirement, worldEventLedgerJson: artifacts.worldEventLedgerJson, behaviorTraceJson: canonicalizeJsonValue(bindingTrace) }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });

  const commandTrace = JSON.parse(artifacts.behaviorTraceJson);
  commandTrace.commands[0].mirrorEvidence.commandSha256 = hashLabel("forged-command");
  assert.throws(() => evaluateR20QualificationCoverage({ requirement, worldEventLedgerJson: artifacts.worldEventLedgerJson, behaviorTraceJson: canonicalizeJsonValue(commandTrace) }), { code: "R20_QUALIFICATION_COVERAGE_INVALID" });
});
