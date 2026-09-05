import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { ledgerFixture } from "../packages/npc-authority-contracts/tests/fixtures.mjs";
import { canonicalText, sha256 } from "../scripts/lib/r22-cli-core.mjs";
import { collectR22LivePhysicalEvidence, R22_PHYSICAL_MARKER, validateR22LivePhysicalEvidence } from "../scripts/lib/r22-live-evidence.mjs";

const binding = `sha256:${"b".repeat(64)}`;
function fixture() {
  const ledger = ledgerFixture(), entry = ledger.entries[0];
  const identity = { sequence: 1, actorEntityId: entry.intent.actorEntityId, ruleIndex: 0, intentId: entry.intent.id, nodeId: entry.intent.nodeId, actionId: entry.intent.actionId };
  const intentJson = canonicalText(entry.intent);
  const outbound = { pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 18, pathLengthMm: 900 };
  const authority = { decision: "accepted", beforeSnapshotSha256: entry.beforeSnapshotSha256, afterSnapshotSha256: entry.afterSnapshotSha256 };
  const trace = [{ sequence: 1, actorEntityId: identity.actorEntityId, actionId: identity.actionId, state: "arrived", arrivalEvidence: outbound },
    { sequence: 1, actorEntityId: identity.actorEntityId, actionId: identity.actionId, state: "mirrored", ...authority }];
  return { snapshot: { frozen: false, authority: { canonicalWorldEventLedgerJson: canonicalText(ledger) }, commands: [{ ...identity,
    state: "accepted", revisionFinished: 1, arrivalEvidence: outbound,
    mirrorEvidence: { entityBindingSha256: binding, commandSha256: sha256(canonicalText(identity)),
      beforeSnapshotSha256: entry.beforeSnapshotSha256, afterSnapshotSha256: entry.afterSnapshotSha256 } }] },
    observation: { format: "matrix-oasis.r22-live-physical-evidence", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
      timelineId: ledger.timeline.id, entityBindingSha256: binding,
      command: { ...identity, intentSha256: sha256(intentJson), commandSha256: sha256(canonicalText({ ...identity, npcIntentJson: intentJson })) },
      authority, movement: { outbound, return: { kind: "walked-home", returnedHome: true, physicsProcessing: false, positionErrorMm: 8 } },
      performance: { sampleCount: 300, frameMicros: Array(300).fill(16000), medianFrameMicros: 16000, medianFpsMilli: 62500 },
      canonicalR20Trace: canonicalText(trace) } };
}

test("physical observation binds actual command, R19 entry, mirror, and 300 frames deterministically", () => {
  const { observation, snapshot } = fixture();
  const outputs = Array.from({ length: 20 }, () => validateR22LivePhysicalEvidence(canonicalText(observation), snapshot, binding));
  assert.equal(new Set(outputs.map(canonicalText)).size, 1);
  assert.equal(outputs[0].performancePassed, true);
  assert.ok(Object.isFrozen(outputs[0].observation.performance.frameMicros));
});

test("re-signed marker cannot substitute identity, motion, mirror, or another timeline", () => {
  const attacks = [
    (v) => { v.command.commandSha256 = binding; }, (v) => { v.command.intentSha256 = binding; },
    (v) => { v.command.actorEntityId = "other"; }, (v) => { v.command.sequence = 2; },
    (v) => { v.timelineId = "another-timeline"; }, (v) => { v.entityBindingSha256 = `sha256:${"c".repeat(64)}`; },
    (v) => { v.movement.outbound.floorVerified = false; }, (v) => { v.movement.outbound.pathLengthMm += 1; },
    (v) => { v.authority.afterSnapshotSha256 = binding; }, (v) => { v.authority.decision = "rejected"; },
    (v) => { v.canonicalR20Trace = "[]"; }, (v) => { v.canonicalR20Trace = canonicalText([...JSON.parse(v.canonicalR20Trace)].reverse()); },
    (v) => { v.movement.return.returnedHome = false; }, (v) => { v.movement.return.positionErrorMm = 101; },
    (v) => { v.movement.return.physicsProcessing = true; }, (v) => { v.payload = "not allowed"; },
    (v) => { v.performance.sampleCount = 299; }, (v) => { v.performance.frameMicros.pop(); },
    (v) => { v.performance.medianFrameMicros += 1; }, (v) => { v.performance.medianFpsMilli += 1; },
    (v) => { v.performance.frameMicros[0] = 0; },
  ];
  for (const attack of attacks) { const { observation, snapshot } = fixture(); attack(observation);
    assert.throws(() => validateR22LivePhysicalEvidence(canonicalText(observation), snapshot, binding), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u); }
  const { observation, snapshot } = fixture();
  const invalidFloat = canonicalText(observation).replace('"frameMicros":[16000', '"frameMicros":[1.5');
  assert.throws(() => validateR22LivePhysicalEvidence(invalidFloat, snapshot, binding), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
});

test("an absent command, corrupt chain, or frozen coordinator cannot certify a physical observation", () => {
  for (const attack of [s => { s.commands = []; }, s => { s.commands.push(s.commands[0]); }, s => { s.frozen = true; },
    s => { s.commands[0].arrivalEvidence = { ...s.commands[0].arrivalEvidence, sequence: 1 }; },
    s => { const l = JSON.parse(s.authority.canonicalWorldEventLedgerJson); l.headSha256 = binding; s.authority.canonicalWorldEventLedgerJson = canonicalText(l); }]) {
    const { observation, snapshot } = fixture(); attack(snapshot);
    assert.throws(() => validateR22LivePhysicalEvidence(canonicalText(observation), snapshot, binding), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
  }
});

test("slow actual frames are retained as failed performance, not rewritten as qualification", () => {
  const { observation, snapshot } = fixture();
  observation.movement.return.kind = "hidden-home";
  observation.performance = { sampleCount: 300, frameMicros: Array(300).fill(50000), medianFrameMicros: 50000, medianFpsMilli: 20000 };
  assert.equal(validateR22LivePhysicalEvidence(canonicalText(observation), snapshot, binding).performancePassed, false);
});

test("bounded collector accepts split UTF-8 marker lines in order and discards other output", async () => {
  const child = { stdout: new EventEmitter() }, seen = [];
  const collector = collectR22LivePhysicalEvidence(child, async text => { await Promise.resolve(); seen.push(text); });
  const first = Buffer.from(`other unretained output\n${R22_PHYSICAL_MARKER}{"x":"好"}\r\n`);
  for (const byte of first) child.stdout.emit("data", Buffer.from([byte]));
  child.stdout.emit("data", Buffer.from(`${R22_PHYSICAL_MARKER}{}\n`));
  await collector.finish();
  assert.deepEqual(seen, ['{"x":"好"}', "{}"]);
  assert.equal(child.stdout.listenerCount("data"), 0);
});

test("collector fails closed for malformed UTF-8, oversized output, or rejected marker", async () => {
  for (const chunk of [Buffer.from([0xff, 0x0a]), Buffer.alloc(65537, 120), Buffer.from(`${R22_PHYSICAL_MARKER}{bad}\n`)]) {
    const child = { stdout: new EventEmitter() };
    const collector = collectR22LivePhysicalEvidence(child, async () => { throw new Error("unretained detail"); });
    child.stdout.emit("data", chunk);
    await assert.rejects(collector.finish(), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
    assert.equal(await collector.failure, "physical-evidence-failure");
  }
});

test("physical rejection diagnostics expose only a fixed validation stage, never input or exception text", async () => {
  const { observation, snapshot } = fixture();
  observation.command.commandSha256 = binding;
  const child = { stdout: new EventEmitter() }, diagnostics = [];
  const collector = collectR22LivePhysicalEvidence(child,
    async text => validateR22LivePhysicalEvidence(text, snapshot, binding), value => diagnostics.push(value));
  child.stdout.emit("data", Buffer.from(`${R22_PHYSICAL_MARKER}${canonicalText(observation)}\n`));
  await assert.rejects(collector.finish(), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
  assert.deepEqual(diagnostics, [{ code: "R22_LIVE_PHYSICAL_EVIDENCE_INVALID", stage: "command" }]);
  for (const validationStage of ["untrusted private message", "__proto__"]) {
    const other = { stdout: new EventEmitter() }, rejected = [];
    const c = collectR22LivePhysicalEvidence(other, async () => { throw Object.assign(new Error("unretained private detail"), { validationStage }); }, value => rejected.push(value));
    other.stdout.emit("data", Buffer.from(`${R22_PHYSICAL_MARKER}{}\n`));
    await assert.rejects(c.finish());
    assert.deepEqual(rejected, [{ code: "R22_LIVE_PHYSICAL_EVIDENCE_INVALID", stage: "collection-or-publication" }]);
  }
});
