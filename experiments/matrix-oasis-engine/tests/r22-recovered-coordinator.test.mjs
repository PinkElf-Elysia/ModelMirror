import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { createNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { prepareDeterministicNpcBehavior, selectNextNpcBehaviorCommand, synthesizeNpcBehaviorPolicy } from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { buildR20BridgeArtifacts } from "../scripts/lib/r20-cli-core.mjs";
import { createR20Coordinator, exportR20Coordinator, handleR20CoordinatorRequestAsync } from "../scripts/lib/r20-host-core.mjs";
import { rebuildR22RecoveredCoordinator } from "../scripts/lib/r22-recovered-coordinator.mjs";

const BAD = /R22_RECOVERY_COORDINATOR_INVALID/u;
const token = "r".repeat(64);
const sha = (character) => `sha256:${character.repeat(64)}`;
function req(method, url, body = null) { return { remoteAddress: "127.0.0.1", method, url,
  headers: { authorization: `Bearer ${token}`, ...(method === "POST" ? { "content-type": "application/json" } : {}) },
  ...(method === "POST" ? { body: canonicalizeJsonValue(body) } : {}) }; }
async function call(coordinator, method, url, body = null) {
  const response = await handleR20CoordinatorRequestAsync(coordinator, req(method, url, body));
  assert.equal(response.statusCode, 200, response.body); return JSON.parse(response.body);
}
async function makeInputs(resetCount = 2) {
  const authored = JSON.parse(await readFile(new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8"));
  const compiled = await compileAuthoringGamePackJson(canonicalizeJsonValue(authored)); assert.equal(compiled.ok, true);
  const runtimeGamePackJson = compiled.canonicalJson, runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
  const grants = compiled.runtimePack.nodes.flatMap((node) => node.actions.map((action) => ({ nodeId: node.id, actionId: action.id })));
  const authorityPolicyJson = canonicalizeJsonValue({ format: "matrix-oasis.npc-authority-policy", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    id: "recovered-coordinator-policy", contentVersion: "1", runtime: { format: compiled.runtimePack.format, formatVersion: compiled.runtimePack.formatVersion,
      id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion, sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
      artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`, receiptSha256: hashCanonicalValue(compiled.receipt) }, actorGrants: [{ actorEntityId: "actor-unit", grants }] });
  const behavior = synthesizeNpcBehaviorPolicy({ authorityPolicyJson }); assert.equal(behavior.ok, true);
  const entityBindingJson = canonicalizeJsonValue({ format: "matrix-oasis.npc-entity-binding", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    identities: { sceneBlueprintSha256: sha("a"), scenePackSha256: sha("b"), assetBundleSha256: sha("c"), spatialSolutionSha256: sha("d"), spatialVerificationSha256: sha("e"), authorityPolicySha256: behavior.npcBehaviorPolicy.authorityPolicySha256 },
    bindings: [{ actorEntityId: "actor-unit", assetBriefId: "brief", placementId: "placement", runtimeEntityId: "actor-unit", homeFloorAnchorId: "floor", homePositionMm: { x: 0, y: 0, z: 0 }, visibleNodeIds: compiled.runtimePack.nodes.map((node) => node.id) }] });
  const prepared = prepareDeterministicNpcBehavior({ behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson, entityBindingJson, authorityPolicyJson }); assert.equal(prepared.ok, true);
  const initialTimelineId = "timeline-recovered-coordinator";
  const authority = await createNpcAuthoritySession({ runtimeGamePackJson, runtimeReceiptJson, policyJson: authorityPolicyJson, timelineId: initialTimelineId, stepLimit: 16 });
  const control = createR20Coordinator({ authoritySession: authority.session, preparedBehavior: prepared.prepared, initialBehaviorState: prepared.initialState,
    entityBindingSha256: hashCanonicalValue(JSON.parse(entityBindingJson)), sessionToken: token });
  const auditedTimelineIds = [initialTimelineId];
  for (let count = 0; count < resetCount; count += 1) auditedTimelineIds.push((await call(control, "POST", "/v1/reset", {})).timelineId);
  const command = (await call(control, "GET", "/v1/command")).command;
  const arrivalEvidence = { pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 7, pathLengthMm: 350 };
  const verdict = await call(control, "POST", "/v1/arrived", { sequence: command.sequence, ...arrivalEvidence });
  await call(control, "POST", "/v1/mirror", { sequence: command.sequence, beforeSnapshotSha256: verdict.beforeSnapshotSha256, afterSnapshotSha256: verdict.afterSnapshotSha256 });
  const snapshot = exportR20Coordinator(control);
  const trace = JSON.parse(buildR20BridgeArtifacts({ snapshot, behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson, entityBindingJson }).canonicalBehaviorTraceJson);
  return { control, input: { source: { runtimeGamePackJson, runtimeReceiptJson, authorityPolicyJson }, preparedBehavior: prepared.prepared,
    initialBehaviorState: prepared.initialState, initialTimelineId, auditedTimelineIds, recovery: { canonicalWorldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson, behaviorTrace: trace },
    sessionToken: token, commandSelector: () => ({ ok: true, status: "quiescent" }) } };
}

test("replays two public resets and the active command so the next reset remains identical", async () => {
  const { control, input } = await makeInputs(); let commits = 0, resets = 0;
  input.auditedTimelineIds = [input.auditedTimelineIds[2], input.auditedTimelineIds[0], input.auditedTimelineIds[1]];
  input.onCommit = () => { commits += 1; }; input.onReset = () => { resets += 1; };
  const recovered = await rebuildR22RecoveredCoordinator(input);
  assert.equal(commits, 0); assert.equal(resets, 0);
  assert.deepEqual(exportR20Coordinator(recovered).commands, exportR20Coordinator(control).commands);
  const expected = await call(control, "POST", "/v1/reset", {}), actual = await call(recovered, "POST", "/v1/reset", {});
  assert.equal(actual.timelineId, expected.timelineId); assert.equal(resets, 1);
  assert.equal((await call(recovered, "GET", "/v1/command")).status, "quiescent");
});

test("rejects missing reset history, a forked timeline, and a forged recorded command", async () => {
  const attacks = [
    (value) => { value.auditedTimelineIds.splice(1, 1); },
    (value) => { value.auditedTimelineIds[1] = "timeline-fork"; },
    (value) => { value.recovery.behaviorTrace.commands[0].intentId += "-forged"; },
  ];
  for (const attack of attacks) { const { input } = await makeInputs(); attack(input); await assert.rejects(rebuildR22RecoveredCoordinator(input), BAD); }
});

test("rejects an audited node after the recovery timeline has already been reached", async () => {
  const { control, input } = await makeInputs(1);
  const extraTimelineId = (await call(control, "POST", "/v1/reset", {})).timelineId;
  input.auditedTimelineIds = [extraTimelineId, ...input.auditedTimelineIds];
  await assert.rejects(rebuildR22RecoveredCoordinator(input), BAD);
});

test("recovery suppression covers all hooks and live commit invokes the caller hook", async () => {
  const { input } = await makeInputs(); const calls = { commit: 0, reset: 0, verify: 0 };
  input.onCommit = () => { calls.commit += 1; }; input.onReset = () => { calls.reset += 1; }; input.onVerify = () => { calls.verify += 1; };
  input.commandSelector = selectNextNpcBehaviorCommand;
  const recovered = await rebuildR22RecoveredCoordinator(input);
  assert.deepEqual(calls, { commit: 0, reset: 0, verify: 0 });
  await call(recovered, "POST", "/v1/reset", {});
  for (;;) {
    const selected = await call(recovered, "GET", "/v1/command");
    if (selected.status !== "command") break;
    const command = selected.command, verdict = await call(recovered, "POST", "/v1/arrived", { sequence: command.sequence,
      pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 1, pathLengthMm: 1 });
    await call(recovered, "POST", "/v1/mirror", { sequence: command.sequence,
      beforeSnapshotSha256: verdict.beforeSnapshotSha256, afterSnapshotSha256: verdict.afterSnapshotSha256 });
  }
  const snapshot = exportR20Coordinator(recovered), events = snapshot.commands.flatMap((item) => [
    { sequence: item.sequence, actorEntityId: item.actorEntityId, actionId: item.actionId, state: "arrived", arrivalEvidence: item.arrivalEvidence },
    { sequence: item.sequence, actorEntityId: item.actorEntityId, actionId: item.actionId, state: "mirrored", decision: item.state,
      beforeSnapshotSha256: item.mirrorEvidence.beforeSnapshotSha256, afterSnapshotSha256: item.mirrorEvidence.afterSnapshotSha256 },
  ]);
  const godotTrace = { traceVersion: 1, entityBindingSha256: input.recovery.behaviorTrace.entityBindingSha256,
    renderer: "forward_plus", navigationSynchronized: true, eventCount: events.length, eventsSha256: hashCanonicalValue(events),
    performance: { sampleCount: 300, medianFrameMicros: 10_000, medianFpsMilli: 100_000 } };
  await call(recovered, "POST", "/v1/verify", godotTrace);
  assert.ok(calls.commit > 0); assert.equal(calls.reset, 1); assert.equal(calls.verify, 1);
});
