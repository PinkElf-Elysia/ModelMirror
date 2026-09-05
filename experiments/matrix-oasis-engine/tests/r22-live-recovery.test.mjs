import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { createNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { prepareDeterministicNpcBehavior, synthesizeNpcBehaviorPolicy } from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { computeNpcCognitionApprovalHash } from "@matrix-oasis/npc-cognition-contracts";
import { createR20Coordinator, exportR20Coordinator, handleR20CoordinatorRequestAsync } from "../scripts/lib/r20-host-core.mjs";
import { createR20TimelineStore, recoverR20UnfinishedTimeline } from "../scripts/lib/r20-cli-core.mjs";
import { closeR22CallStore, computeR22DisplayAckHash, inspectR22CallStore, openR22CallStore, readR22ActiveCallArtifacts, readR22FinalizedTurnReceipt } from "../scripts/lib/r22-call-store.mjs";
import { acknowledgeR22CognitionDisplay, completeQueuedR22CognitionTurn, createR22TransactionalHost, declineR22CognitionTurn, executeApprovedR22CognitionTurn, issueR22CognitionApproval, registerR22CognitionTurn } from "../scripts/lib/r22-host-core.mjs";
import { loadR22LiveRecoveryHistory } from "../scripts/lib/r22-live-recovery.mjs";

const fake = (c) => `sha256:${c.repeat(64)}`;
const sha = (text) => `sha256:${createHash("sha256").update(text).digest("hex")}`;
async function findFile(root, basename) {
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const candidate = path.join(root, entry.name);
    if (entry.isDirectory()) { const nested = await findFile(candidate, basename); if (nested) return nested; }
    else if (entry.name === basename) return candidate;
  }
  return null;
}
function cognitionPlan(sequence = 1, mappedIntentSha256 = null) {
  const providerRequestJson = canonicalizeJsonValue({ background: false, input: "synthetic recovery fixture", instructions: "Return bounded JSON.", max_output_tokens: 512,
    model: "gpt-5.6-luna", reasoning: { effort: "none" }, store: false, stream: false,
    text: { format: { name: "matrix_oasis_npc_dialogue_proposal", schema: { additionalProperties: false, properties: {}, required: [], type: "object" }, strict: true, type: "json_schema" } }, truncation: "disabled" });
  const plan = { format: "matrix-oasis.npc-cognition-call-plan", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    turnId: `turn-${sequence}`, turnSha256: sha(`turn-${sequence}`), contextSha256: sha(`context-${sequence}`), candidateSha256: sha(canonicalizeJsonValue(mappedIntentSha256 ? [{ choiceId: `choice-${mappedIntentSha256.slice(7)}`, intentSha256: mappedIntentSha256 }] : [])), candidateChoices: mappedIntentSha256 ? [{ choiceId: `choice-${mappedIntentSha256.slice(7)}`, intentSha256: mappedIntentSha256 }] : [],
    providerPayloadSha256: sha(providerRequestJson), responseSchemaSha256: sha("schema"), endpoint: "https://api.openai.com/v1/responses", model: "gpt-5.6-luna",
    reasoningEffort: "none", priceLock: { inputMicrousdPerMillionTokens: 200000, cachedInputMicrousdPerMillionTokens: 20000,
      cacheWriteInputMicrousdPerMillionTokens: 250000, outputMicrousdPerMillionTokens: 1200000 }, maxOutputTokens: 512, timeoutMs: 30000,
    maxCostMicrousd: 10000, requestBytes: Buffer.byteLength(providerRequestJson), requestLimit: 1, retryLimit: 0,
    retentionPolicyVersion: "openai-api-data-controls-2026-09-03", retention: { store: false, zeroDataRetentionClaimed: false, abuseMonitoringMaxDays: 30, promptCachingPossible: true },
    approval: { hash: fake("0"), expiresAfterMs: 300000 } };
  plan.approval.hash = computeNpcCognitionApprovalHash(plan);
  return { providerRequestJson, callPlanJson: canonicalizeJsonValue(plan) };
}
async function fixture(t, { finalizedDecline = false, stagedDecline = false, mappedFallbackConflict = false } = {}) {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "r22-live-recovery-")); t.after(() => rm(temporaryRoot, { recursive: true, force: true }));
  const authored = await readFile(new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8");
  const compiled = await compileAuthoringGamePackJson(authored), runtimeGamePackJson = compiled.canonicalJson, runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
  const authorityPolicyJson = canonicalizeJsonValue({ format: "matrix-oasis.npc-authority-policy", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", id: "recovery-policy", contentVersion: "1",
    runtime: { format: compiled.runtimePack.format, formatVersion: compiled.runtimePack.formatVersion, id: compiled.runtimePack.source.id, contentVersion: compiled.runtimePack.source.contentVersion,
      sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`, artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`, receiptSha256: hashCanonicalValue(compiled.receipt) },
    actorGrants: [{ actorEntityId: "actor-unit", grants: [{ nodeId: "node-start", actionId: "action-initialize" }] }] });
  const behavior = synthesizeNpcBehaviorPolicy({ authorityPolicyJson });
  const npcEntityBindingJson = canonicalizeJsonValue({ format: "matrix-oasis.npc-entity-binding", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
    identities: { sceneBlueprintSha256: fake("a"), scenePackSha256: fake("b"), assetBundleSha256: fake("c"), spatialSolutionSha256: fake("d"), spatialVerificationSha256: fake("e"), authorityPolicySha256: behavior.npcBehaviorPolicy.authorityPolicySha256 },
    bindings: [{ actorEntityId: "actor-unit", assetBriefId: "brief", placementId: "placement", runtimeEntityId: "actor-unit", homeFloorAnchorId: "floor", homePositionMm: { x: 0, y: 0, z: 0 }, visibleNodeIds: ["node-start"] }] });
  const prepared = prepareDeterministicNpcBehavior({ behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson, entityBindingJson: npcEntityBindingJson, authorityPolicyJson });
  const source = { runtimeGamePackJson, runtimeReceiptJson, authorityPolicyJson, behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson, npcEntityBindingJson };
  const timelineId = "timeline-recovery", qualificationCoverage = { format: "matrix-oasis.r20-qualification-coverage-requirement", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", profile: "matrix-oasis.r20-runtime-coverage/1", runtimePackSha256: hashCanonicalValue(JSON.parse(runtimeGamePackJson)), endingRequired: true, loopRequirement: { required: false, minimumDistinctNodes: 0 } };
  const manifestFor = (id) => canonicalizeJsonValue({ format: "matrix-oasis.npc-authority-manifest", formatVersion: "0.2.0", canonicalization: "matrix-oasis.canonical-json/1", timelineId: id, qualificationRunId: "recovery-qualification", sourceRunId: "recovery-source", qualificationCoverage, toolchain: { godotVersion: "4.6.3", renderer: "forward_plus" },
    identities: { authorityPolicySha256: hashCanonicalValue(JSON.parse(authorityPolicyJson)), behaviorPolicySha256: hashCanonicalValue(behavior.npcBehaviorPolicy), entityBindingSha256: hashCanonicalValue(JSON.parse(npcEntityBindingJson)), implementationSha256: fake("f"), godotBinarySha256: fake("1"), runtimePackSha256: qualificationCoverage.runtimePackSha256, runtimeReceiptSha256: hashCanonicalValue(compiled.receipt), spatialSolutionSha256: fake("2"), spatialVerificationSha256: fake("3") } });
  const npcRunRoot = path.join(temporaryRoot, "history-npc"), cognitionRunRoot = path.join(temporaryRoot, "history-cognition");
  const authority = await createNpcAuthoritySession({ runtimeGamePackJson, runtimeReceiptJson, policyJson: authorityPolicyJson, timelineId });
  const r20 = await createR20TimelineStore({ npcRunRoot, temporaryRoot, authorityManifestJson: manifestFor(timelineId), behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson, entityBindingJson: npcEntityBindingJson });
  const coordinator = createR20Coordinator({ authoritySession: authority.session, preparedBehavior: prepared.prepared, initialBehaviorState: prepared.initialState, entityBindingSha256: hashCanonicalValue(JSON.parse(npcEntityBindingJson)), sessionToken: "r".repeat(64), onCommit: (snapshot) => r20.append(snapshot) });
  await r20.append(exportR20Coordinator(coordinator));
  const cognitionPolicyJson = canonicalizeJsonValue({ fixture: "cognition" }), hostRunId = "host-recovery";
  let receiptRename = false;
  const callConfig = { temporaryRoot, cognitionRunRoot, hostRunId, timelineId, authoritySessionSha256: hashCanonicalValue(JSON.parse(manifestFor(timelineId))), cognitionPolicySha256: hashCanonicalValue(JSON.parse(cognitionPolicyJson)), initialLedgerPoint: { revision: 0, headSha256: null, runtimeSnapshotSha256: hashCanonicalValue(authority.runtimeSnapshot) } };
  const call = await openR22CallStore(callConfig, stagedDecline ? { async rename(source, target) {
    await rename(source, target); if (!receiptRename && path.basename(target) === "turn-receipt.json") { receiptRename = true; throw new Error("synthetic crash"); }
  } } : undefined);
  if (finalizedDecline || stagedDecline || mappedFallbackConflict) {
    let mappedCommand = null;
    if (mappedFallbackConflict) {
      const selected = JSON.parse((await handleR20CoordinatorRequestAsync(coordinator, { remoteAddress: "127.0.0.1", method: "GET", url: "/v1/command", headers: { authorization: `Bearer ${"r".repeat(64)}` }, body: "" })).body);
      mappedCommand = selected.command;
    }
    const host = createR22TransactionalHost({ store: call, clock: () => 1_000_000, randomBytesImplementation: () => new Uint8Array(32).fill(7), operations: {
      async keyReader() { if (stagedDecline) return "dummy"; throw new Error("must not read"); },
      async providerExecutor() { if (stagedDecline) return { ok: false, diagnosticCode: "R22_PROVIDER_TIMEOUT", requestCount: 1, costUncertain: true }; throw new Error("must not call"); },
      async proposalValidator() { throw new Error("must not validate"); }, async adjudicationLookup() { return { found: false }; },
    } });
    if (mappedCommand) {
      const mappedIntentSha256 = sha(mappedCommand.npcIntentJson), choice = `choice-${mappedIntentSha256.slice(7)}`;
      const mappedHost = createR22TransactionalHost({ store: call, clock: () => 1_000_000, randomBytesImplementation: () => new Uint8Array(32).fill(7), operations: {
        async keyReader() { return "dummy"; }, async providerExecutor({ callPlanJson }) { const p = JSON.parse(callPlanJson), proposal = { contextSha256: p.contextSha256, dialogueText: "Synthetic fixture.", actionChoiceId: choice };
          return { ok: true, requestCount: 1, costUncertain: false, returnedModel: "gpt-5.6-luna", usage: { inputTokens: 10, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 5, totalTokens: 15 }, actualCostMicrousd: 8, responseBytes: 1, proposal, proposalJson: canonicalizeJsonValue(proposal) }; },
        async proposalValidator({ proposalJson }) { const proposal = JSON.parse(proposalJson); return { ok: true, canonicalNpcDialogueProposalJson: canonicalizeJsonValue({ format: "matrix-oasis.npc-dialogue-proposal", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", ...proposal }), dialogueText: proposal.dialogueText, actionChoiceId: choice, mappedIntentSha256, command: mappedCommand }; },
        async adjudicationLookup() { return { found: false }; },
      } });
      const plan = cognitionPlan(1, mappedIntentSha256), registered = await registerR22CognitionTurn(mappedHost, { turnId: "turn-1", sequence: 1, actorEntityId: "actor-unit", ...plan, beforeLedgerPoint: { revision: 0, headSha256: null, runtimeSnapshotSha256: hashCanonicalValue(authority.runtimeSnapshot) } });
      const approval = issueR22CognitionApproval(mappedHost, { turnId: "turn-1", disclosureSha256: registered.disclosureSha256 });
      const queued = await executeApprovedR22CognitionTurn(mappedHost, { turnId: "turn-1", approvalHash: approval.approvalHash });
      const displayAckHash = computeR22DisplayAckHash({ approvalTokenSha256: approval.approvalHash, turnSha256: JSON.parse(plan.callPlanJson).turnSha256, callPlanSha256: sha(plan.callPlanJson), proposalSha256: queued.proposalSha256, actionChoiceId: queued.actionChoiceId });
      await acknowledgeR22CognitionDisplay(mappedHost, { turnId: "turn-1", displayAckHash });
      await completeQueuedR22CognitionTurn(mappedHost, { turnId: "turn-1", status: "fallback", afterLedgerPoint: { revision: 0, headSha256: null, runtimeSnapshotSha256: hashCanonicalValue(authority.runtimeSnapshot) }, adjudicationResultJson: null, diagnosticCode: "R22_R20_UNAVAILABLE" });
      const arrived = JSON.parse((await handleR20CoordinatorRequestAsync(coordinator, { remoteAddress: "127.0.0.1", method: "POST", url: "/v1/arrived", headers: { authorization: `Bearer ${"r".repeat(64)}`, "content-type": "application/json" }, body: JSON.stringify({ sequence: mappedCommand.sequence, pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 0, pathLengthMm: 0 }) })).body);
      await handleR20CoordinatorRequestAsync(coordinator, { remoteAddress: "127.0.0.1", method: "POST", url: "/v1/mirror", headers: { authorization: `Bearer ${"r".repeat(64)}`, "content-type": "application/json" }, body: JSON.stringify({ sequence: mappedCommand.sequence, beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 }) });
    } else {
    const plan = cognitionPlan();
    const registered = await registerR22CognitionTurn(host, { turnId: "turn-1", sequence: 1, actorEntityId: "actor-unit", ...plan,
      beforeLedgerPoint: { revision: 0, headSha256: null, runtimeSnapshotSha256: hashCanonicalValue(authority.runtimeSnapshot) } });
    const approval = issueR22CognitionApproval(host, { turnId: "turn-1", disclosureSha256: registered.disclosureSha256 });
    if (stagedDecline) await assert.rejects(executeApprovedR22CognitionTurn(host, { turnId: "turn-1", approvalHash: approval.approvalHash }));
    else await declineR22CognitionTurn(host, { turnId: "turn-1", disclosureSha256: registered.disclosureSha256 });
    }
  }
  await closeR22CallStore(call);
  const recovery = (await recoverR20UnfinishedTimeline({ npcRunRoot, temporaryRoot })).recovered; assert.ok(recovery);
  return { input: { npcRunRoot, cognitionRunRoot, temporaryRoot, source, manifestFor, hostRunId, cognitionPolicyJson, preparedBehavior: prepared.prepared, initialBehaviorState: prepared.initialState, recovery }, cognitionRunRoot, callConfig };
}

async function assertStoredContractsRemainValid(value) {
  const store = await openR22CallStore(value.callConfig);
  try {
    const { checkpoint } = inspectR22CallStore(store);
    if (checkpoint.active) assert.ok(await readR22ActiveCallArtifacts(store));
    for (const item of checkpoint.finalized) assert.ok(await readR22FinalizedTurnReceipt(store, {
      timelineId: value.callConfig.timelineId, turnId: item.turnId, callPlanSha256: item.callPlanSha256,
    }));
  } finally { await closeR22CallStore(store); }
}

test("preflight audits a unique unfinished timeline and revalidates every captured record", async (t) => {
  const value = await fixture(t), result = await loadR22LiveRecoveryHistory(value.input);
  assert.deepEqual(result.auditedTimelineIds, ["timeline-recovery"]); assert.equal(await result.revalidate(), true);
  assert.equal(Object.isFrozen(result), true);
});

test("preflight fails closed for missing cognition state, manifest drift, and post-audit mutation", async (t) => {
  const missing = await fixture(t); await rm(path.join(missing.cognitionRunRoot, "host-budget.json"));
  await assert.rejects(loadR22LiveRecoveryHistory(missing.input), /R22_LIVE_RECOVERY_INVALID/u);
  const drift = await fixture(t), original = drift.input.manifestFor;
  drift.input.manifestFor = (id) => original(`${id}-drift`);
  await assert.rejects(loadR22LiveRecoveryHistory(drift.input), /R22_LIVE_RECOVERY_INVALID/u);
  const changed = await fixture(t), result = await loadR22LiveRecoveryHistory(changed.input), budget = path.join(changed.cognitionRunRoot, "host-budget.json");
  await writeFile(budget, `${await readFile(budget, "utf8")} `);
  await assert.rejects(result.revalidate(), /R22_LIVE_RECOVERY_INVALID/u);
});

test("preflight rejects a cognition turn directory not named by finalized or active checkpoint state", async (t) => {
  const value = await fixture(t), authorityId = hashCanonicalValue(JSON.parse(value.input.manifestFor("timeline-recovery"))).slice(7);
  await mkdir(path.join(value.cognitionRunRoot, "timelines", authorityId, "turns", `000001-${"9".repeat(64)}`));
  await assert.rejects(loadR22LiveRecoveryHistory(value.input), /R22_LIVE_RECOVERY_INVALID/u);
});

test("preflight preserves a real zero-request finalized receipt but rejects a forged reserved budget entry", async (t) => {
  const valid = await fixture(t, { finalizedDecline: true });
  const accepted = await loadR22LiveRecoveryHistory(valid.input);
  assert.equal(await accepted.revalidate(), true);
  const validBudget = JSON.parse(await readFile(path.join(valid.cognitionRunRoot, "host-budget.json"), "utf8"));
  assert.equal(validBudget.entries.length, 0);

  const forged = await fixture(t, { finalizedDecline: true });
  const budgetPath = path.join(forged.cognitionRunRoot, "host-budget.json"), budget = JSON.parse(await readFile(budgetPath, "utf8"));
  const checkpointRoot = await findFile(forged.cognitionRunRoot, "cognition-checkpoint.json"), checkpoint = JSON.parse(await readFile(checkpointRoot, "utf8"));
  budget.entries.push({ authoritySessionSha256: `sha256:${path.basename(path.dirname(checkpointRoot))}`, callPlanSha256: checkpoint.finalized[0].callPlanSha256,
    reservedMicrousd: 10000, chargedMicrousd: 0, state: "reserved" });
  await writeFile(budgetPath, canonicalizeJsonValue(budget), "utf8");
  await assertStoredContractsRemainValid(forged);
  await assert.rejects(loadR22LiveRecoveryHistory(forged.input), /R22_LIVE_RECOVERY_INVALID/u);
});

test("preflight rejects a finalized mapped fallback when the real authority Ledger contains that intent", async (t) => {
  const value = await fixture(t, { mappedFallbackConflict: true });
  await assertStoredContractsRemainValid(value);
  await assert.rejects(loadR22LiveRecoveryHistory(value.input), /R22_LIVE_RECOVERY_INVALID/u);
});

test("active staged receipt accepts its real reserved budget but rejects released-state drift", async (t) => {
  async function moveReceiptToStage(value) {
    const target = await findFile(value.cognitionRunRoot, "turn-receipt.json");
    assert.ok(target);
    const stage = path.join(path.dirname(target), ".s-Ab12Cd");
    await mkdir(stage);
    await rename(target, path.join(stage, "turn-receipt.json"));
  }
  const valid = await fixture(t, { stagedDecline: true });
  await moveReceiptToStage(valid);
  assert.equal(await (await loadR22LiveRecoveryHistory(valid.input)).revalidate(), true);

  const forged = await fixture(t, { stagedDecline: true });
  await moveReceiptToStage(forged);
  const budgetPath = path.join(forged.cognitionRunRoot, "host-budget.json"), budget = JSON.parse(await readFile(budgetPath, "utf8"));
  assert.equal(budget.entries[0].state, "reserved");
  budget.entries[0].state = "released";
  await writeFile(budgetPath, canonicalizeJsonValue(budget), "utf8");
  await assertStoredContractsRemainValid(forged);
  await assert.rejects(loadR22LiveRecoveryHistory(forged.input), /R22_LIVE_RECOVERY_INVALID/u);
});
