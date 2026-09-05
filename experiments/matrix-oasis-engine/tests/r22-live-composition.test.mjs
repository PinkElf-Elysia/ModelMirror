import assert from "node:assert/strict";
import { access, lstat, readFile, readdir, realpath, rm } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";
import { createR22LiveComposition } from "../scripts/lib/r22-live-composition.mjs";
import { handleR22LoopbackRequestAsync } from "../scripts/lib/r22-host-core.mjs";
import { verifyR22QualifiedSourcePair } from "../scripts/lib/r22-qualification-core.mjs";
import { canonicalText, sha256 } from "../scripts/lib/r22-cli-core.mjs";

const TEMP_ROOT = path.join(path.parse(fileURLToPath(import.meta.url)).root, "tmp");
const CASE = Object.freeze({
  caseId: "neutral-cache",
  sourceKind: "qualified-cache",
  npcRunRoot: path.join(TEMP_ROOT, "matrix-oasis-r20-neutral-r10-npc"),
  derivedStateRoot: path.join(TEMP_ROOT, "matrix-oasis-r21-real-20260901-b-case-1"),
  expectedNpcCurrentSha256: "sha256:187221f904f7eee7fc97c0194fe16f075365b1b489050ac00a04be0c4da91135",
  expectedDerivedStateBundleSha256: "sha256:7cfc9b43e6318076b57ee97e34a759a9b9a52c31377a9beaaf5d8d829d1a008b",
});
const SHA_A = `sha256:${"a".repeat(64)}`;
const SHA_B = `sha256:${"b".repeat(64)}`;

function request(token, method, url, body) {
  return {
    remoteAddress: "127.0.0.1", method, url,
    headers: { authorization: `Bearer ${token}`, ...(method === "POST" ? { "content-type": "application/json" } : {}) },
    body: method === "POST" ? JSON.stringify(body ?? {}) : "",
  };
}

async function filesBelow(root) {
  const output = [];
  async function visit(directory) {
    for (const item of await readdir(directory, { withFileTypes: true })) {
      const candidate = path.join(directory, item.name);
      if (item.isDirectory()) await visit(candidate); else output.push(candidate);
    }
  }
  await visit(root);
  return output;
}

async function inspectOwnedDirectory(target, namePattern) {
  const resolved = path.resolve(target);
  assert.equal(path.dirname(resolved), path.resolve(TEMP_ROOT));
  assert.match(path.basename(resolved), namePattern);
  const stat = await lstat(resolved, { bigint: true });
  assert.equal(stat.isDirectory(), true);
  assert.equal(stat.isSymbolicLink(), false);
  assert.equal(await realpath(resolved), resolved);
  return { dev: String(stat.dev), ino: String(stat.ino) };
}

async function removeOwnedDirectory(target, namePattern, expectedIdentity = null) {
  const resolved = path.resolve(target);
  let identity;
  try {
    identity = await inspectOwnedDirectory(resolved, namePattern);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  if (expectedIdentity !== null) assert.deepEqual(identity, expectedIdentity);
  await rm(resolved, { recursive: true, force: true });
}

async function physicalOutputs() {
  return (await readdir(TEMP_ROOT, { withFileTypes: true }))
    .filter((item) => item.isDirectory() && item.name.startsWith("matrix-oasis-r22-physical-"))
    .map((item) => item.name).sort();
}

async function route(live, method, url, body, expected = 200) {
  const response = await handleR22LoopbackRequestAsync(live.controller, request(live.sessionToken, method, url, body));
  assert.equal(response.statusCode, expected, response.body);
  return JSON.parse(response.body);
}

async function settled(live, turnId) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const response = await route(live, "GET", `/v1/cognition/status/${turnId}`);
    if (response.status !== "dispatching") return response;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("offline fake provider did not settle");
}

test("live composition preserves source, rotates stores on reset, and releases exactly one command per approved turn", async (t) => {
  if (process.platform !== "win32") return t.skip("fixed Windows qualification cache is unavailable");
  try {
    await access(path.join(CASE.npcRunRoot, "npc-current.json"));
    await access(path.join(CASE.derivedStateRoot, "npc-derived-state-bundle.json"));
  } catch {
    return t.skip("fixed Windows qualification cache is unavailable");
  }
  const suffix = randomUUID();
  const base = path.join(TEMP_ROOT, `matrix-oasis-r22-live-${suffix}`);
  const npcRunRoot = `${base}-npc`;
  const cognitionRunRoot = `${base}-cognition`;
  const liveRootName = /^matrix-oasis-r22-live-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-(?:npc|cognition)$/u;
  const leasePaths = [path.join(path.dirname(npcRunRoot), `.${path.basename(npcRunRoot)}.r20-writer-lock`),
    path.join(path.dirname(cognitionRunRoot), `.${path.basename(cognitionRunRoot)}.r22-writer-lock`)];
  t.after(async () => {
    await Promise.all([removeOwnedDirectory(npcRunRoot, liveRootName), removeOwnedDirectory(cognitionRunRoot, liveRootName),
      ...leasePaths.map((item) => rm(item, { force: true }))]);
  });
  const source = await verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
  const sourceIdentity = [source.npc.record.sha256, source.derived.record.sha256];
  const actorEntityId = JSON.parse(source.npcEntityBindingJson).bindings[0].actorEntityId;
  const publishedPhysicalOutputs = new Map();
  const physicalName = /^matrix-oasis-r22-physical-[0-9a-f]{64}$/u;
  t.after(async () => { await Promise.all([...publishedPhysicalOutputs].map(([output, identity]) => removeOwnedDirectory(output, physicalName, identity))); });
  const live = await createR22LiveComposition({ providerMode: "offline-fake", implementationSha256: SHA_A,
    godotBinarySha256: SHA_B, source, temporaryRoot: TEMP_ROOT, npcRunRoot, cognitionRunRoot });
  try {
    assert.deepEqual(await route(live, "GET", "/v1/command"), { status: "quiescent" });

    const declined = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "Decline this disclosed turn." });
    await route(live, "POST", "/v1/cognition/decline", { turnId: declined.turnId, approvalHash: declined.approvalHash });
    assert.equal(live.exportState().fakeDispatches, 0);

    const approved = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "Approve this bounded safe action." });
    await route(live, "POST", "/v1/cognition/approve", { turnId: approved.turnId, approvalHash: approved.approvalHash });
    const outcome = await settled(live, approved.turnId);
    assert.equal(outcome.status, "queued_for_r20");
    await route(live, "POST", "/v1/cognition/displayed", { turnId: approved.turnId, displayAckHash: outcome.displayAckHash });
    const selected = await route(live, "GET", "/v1/command");
    assert.equal(selected.status, "command");
    assert.deepEqual(await route(live, "GET", "/v1/command"), { status: "quiescent" });
    const arrived = await route(live, "POST", "/v1/arrived", { sequence: selected.command.sequence,
      pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 0, pathLengthMm: 0 });
    assert.equal(arrived.status, "adjudicated");
    await route(live, "POST", "/v1/mirror", { sequence: selected.command.sequence,
      beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 });
    const authoritySnapshot = live.exportState().authority;
    const committed = authoritySnapshot.commands.find((item) => item.sequence === selected.command.sequence);
    assert.ok(committed);
    assert.deepEqual(Object.keys(committed.arrivalEvidence).sort(), ["capsuleVerified", "domainVerified", "floorVerified", "movementTicks", "pathComplete", "pathLengthMm"]);
    const ledger = JSON.parse(authoritySnapshot.authority.canonicalWorldEventLedgerJson);
    const entry = ledger.entries.find((item) => item.intent.id === selected.command.intentId);
    assert.ok(entry);
    const identity = Object.fromEntries(["sequence", "actorEntityId", "ruleIndex", "intentId", "nodeId", "actionId"].map((key) => [key, selected.command[key]]));
    const authority = { decision: arrived.decision, beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 };
    const syntheticOutbound = structuredClone(committed.arrivalEvidence);
    const syntheticTrace = [
      { sequence: selected.command.sequence, actorEntityId: selected.command.actorEntityId, actionId: selected.command.actionId, state: "arrived", arrivalEvidence: syntheticOutbound },
      { sequence: selected.command.sequence, actorEntityId: selected.command.actorEntityId, actionId: selected.command.actionId, state: "mirrored", ...authority },
    ];
    const intentJson = canonicalText(entry.intent);
    // This marker and its repeated 300-frame sample are synthetic contract-test
    // input only. They are not evidence of a real runtime or qualification.
    const syntheticMarker = {
      format: "matrix-oasis.r22-live-physical-evidence", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
      timelineId: ledger.timeline.id, entityBindingSha256: sha256(source.npcEntityBindingJson),
      command: { ...identity, commandSha256: sha256(canonicalText({ ...identity, npcIntentJson: intentJson })), intentSha256: sha256(intentJson) },
      authority, movement: { outbound: syntheticOutbound,
        return: { kind: "walked-home", returnedHome: true, physicsProcessing: false, positionErrorMm: 0 } },
      performance: { sampleCount: 300, frameMicros: Array(300).fill(16000), medianFrameMicros: 16000, medianFpsMilli: 62500 },
      canonicalR20Trace: canonicalText(syntheticTrace),
    };
    const forgedMovement = structuredClone(syntheticMarker);
    const outputsBeforeAttacks = await physicalOutputs();
    forgedMovement.movement.outbound.movementTicks += 1;
    await assert.rejects(live.recordPhysicalEvidence(canonicalText(forgedMovement)), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
    const forgedCommand = structuredClone(syntheticMarker);
    forgedCommand.command.actionId = "forged-action";
    forgedCommand.command.commandSha256 = sha256(canonicalText({ ...identity, actionId: "forged-action", npcIntentJson: intentJson }));
    await assert.rejects(live.recordPhysicalEvidence(canonicalText(forgedCommand)), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
    assert.deepEqual(await physicalOutputs(), outputsBeforeAttacks);
    const physical = await live.recordPhysicalEvidence(canonicalText(syntheticMarker));
    publishedPhysicalOutputs.set(physical.output, await inspectOwnedDirectory(physical.output, physicalName));
    assert.equal(physical.performancePassed, true);
    assert.equal(physical.medianFpsMilli, 62500);
    assert.equal(physical.cognitionActionVerified, true);
    const observationReport = JSON.parse(await readFile(path.join(physical.output, "observation-report.json"), "utf8"));
    assert.equal(observationReport.qualificationStatus, "unqualified-manual-observation");
    assert.equal(observationReport.manualAcceptancePassed, false);
    assert.equal(observationReport.entrySha256, entry.entrySha256);
    assert.equal(observationReport.turnReceiptSha256 === null, false);
    assert.equal(await readFile(path.join(physical.output, "world-event-ledger.json"), "utf8"), authoritySnapshot.authority.canonicalWorldEventLedgerJson);
    const receiptFilesBeforeReset = (await filesBelow(cognitionRunRoot)).filter((item) => item.endsWith("turn-receipt.json"));
    assert.equal(receiptFilesBeforeReset.length, 2);
    const budgetPath = path.join(cognitionRunRoot, "host-budget.json");
    const budgetBeforeReset = await readFile(budgetPath, "utf8");

    const reset = await route(live, "POST", "/v1/reset", {});
    assert.deepEqual(Object.keys(reset).sort(), ["status", "timelineId"]);
    assert.equal(reset.status, "reset");
    assert.equal(await readFile(budgetPath, "utf8"), budgetBeforeReset);
    const afterReset = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "First turn on the reset timeline." });
    await route(live, "POST", "/v1/cognition/decline", { turnId: afterReset.turnId, approvalHash: afterReset.approvalHash });
    const persistedFiles = await filesBelow(cognitionRunRoot);
    const receipts = await Promise.all(persistedFiles.filter((item) => item.endsWith("turn-receipt.json")).map((item) => readFile(item, "utf8").then(JSON.parse)));
    assert.equal(receipts.length, 3);
    const checkpoints = await Promise.all(persistedFiles.filter((item) => item.endsWith("cognition-checkpoint.json")).map((item) => readFile(item, "utf8").then(JSON.parse)));
    assert.deepEqual(checkpoints.map((item) => item.latestSequence).sort((left, right) => left - right), [1, 2]);
    assert.equal(persistedFiles.filter((item) => item.endsWith("call-plan.json")).length, 3);
    assert.equal(live.exportState().fakeDispatches, 1);
    await live.revalidate();
    const sourceAfter = await verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
    assert.equal(sourceAfter.npc.record.sha256, CASE.expectedNpcCurrentSha256);
    assert.equal(sourceAfter.derived.record.sha256, CASE.expectedDerivedStateBundleSha256);
    assert.deepEqual([sourceAfter.npc.record.sha256, sourceAfter.derived.record.sha256], sourceIdentity);
  } finally {
    await live.close();
  }
  for (const leasePath of leasePaths) await assert.rejects(access(leasePath));
});
