import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { cp, mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import path from "node:path";
import test from "node:test";
import {
  computeNpcCognitionApprovalHash,
  NPC_COGNITION_LIMITS,
  validateNpcCognitionTurnReceiptJson,
} from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  closeR22CallStore,
  computeR22DisplayAckHash,
  inspectR22CallStore,
  markR22CallDispatching,
  openR22CallStore,
  recordR22DisplayAcknowledged,
  recordR22PlannedCall,
  recordR22ValidatedProposal,
  reserveR22CallBudget,
  readR22FinalizedTurnReceipt,
} from "../scripts/lib/r22-call-store.mjs";
import {
  acknowledgeR22CognitionDisplay,
  closeR22LoopbackController,
  completeQueuedR22CognitionTurn,
  computeR22ApprovalDisclosureSha256,
  createR22CognitionSelectorGate,
  createR22LoopbackController,
  createR22TransactionalHost,
  declineR22CognitionTurn,
  executeApprovedR22CognitionTurn,
  handleR22LoopbackRequestAsync,
  issueR22CognitionApproval,
  readFinalizedR22CognitionTurn,
  recoverR22TransactionalHost,
  registerR22CognitionTurn,
  restoreR22CognitionSelectorGate,
  restoreR22LoopbackController,
  startR22LoopbackServer,
} from "../scripts/lib/r22-host-core.mjs";
import {
  intentFixture as authorityIntentFixture,
  ledgerFixture as authorityLedgerFixture,
  resultFixture as authorityResultFixture,
} from "../packages/npc-authority-contracts/tests/fixtures.mjs";

const TMP = process.platform === "win32"
  ? path.win32.join(`C:${path.win32.sep}`, "tmp")
  : path.join(path.sep, "tmp");
const ZERO = "0".repeat(64);
const ONE = "1".repeat(64);
const TWO = "2".repeat(64);
const THREE = "3".repeat(64);

function shaText(text) {
  return `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
}

function fixedSha(hex) {
  return `sha256:${hex.repeat(64).slice(0, 64)}`;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => { resolve = onResolve; reject = onReject; });
  return { promise, resolve, reject };
}

function ledgerPoint(revision = 0, digit = "1") {
  return {
    revision,
    headSha256: revision === 0 ? null : fixedSha(digit),
    runtimeSnapshotSha256: fixedSha(revision === 0 ? "a" : digit),
  };
}

function payload(sequence = 1) {
  return canonicalizeJsonValue({
    background: false,
    input: `Player input ${sequence} PRIVATE_RAW_PLAYER_TEXT`,
    instructions: "Return one bounded dialogue proposal.",
    max_output_tokens: 512,
    model: "gpt-5.6-luna",
    reasoning: { effort: "none" },
    store: false,
    stream: false,
    text: { format: { name: "matrix_oasis_npc_dialogue_proposal", schema: { additionalProperties: false, properties: {}, required: [], type: "object" }, strict: true, type: "json_schema" } },
    truncation: "disabled",
  });
}

function callPlan(providerRequestJson, sequence = 1, intentSha256s = []) {
  const candidateChoices = intentSha256s.map((intentSha256) => ({
    choiceId: `choice-${intentSha256.slice(7)}`,
    intentSha256,
  }));
  const value = {
    format: "matrix-oasis.npc-cognition-call-plan",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    turnId: `turn-${sequence}`,
    turnSha256: shaText(`turn-${sequence}`),
    contextSha256: shaText(`context-${sequence}`),
    candidateSha256: shaText(canonicalizeJsonValue(candidateChoices)),
    candidateChoices,
    providerPayloadSha256: shaText(providerRequestJson),
    responseSchemaSha256: shaText(`schema-${sequence}`),
    endpoint: "https://api.openai.com/v1/responses",
    model: "gpt-5.6-luna",
    reasoningEffort: "none",
    priceLock: {
      inputMicrousdPerMillionTokens: 200000,
      cachedInputMicrousdPerMillionTokens: 20000,
      cacheWriteInputMicrousdPerMillionTokens: 250000,
      outputMicrousdPerMillionTokens: 1200000,
    },
    maxOutputTokens: 512,
    timeoutMs: 30000,
    maxCostMicrousd: 10000,
    requestBytes: Buffer.byteLength(providerRequestJson, "utf8"),
    requestLimit: 1,
    retryLimit: 0,
    retentionPolicyVersion: "openai-api-data-controls-2026-09-03",
    retention: { store: false, zeroDataRetentionClaimed: false, abuseMonitoringMaxDays: 30, promptCachingPossible: true },
    approval: { hash: fixedSha("0"), expiresAfterMs: 300000 },
  };
  value.approval.hash = computeNpcCognitionApprovalHash(value);
  return canonicalizeJsonValue(value);
}

function providerSuccess(planJson, { choice = null, dialogue = "TRANSIENT_MODEL_DIALOGUE", usage } = {}) {
  const plan = JSON.parse(planJson);
  const measured = usage ?? { inputTokens: 10, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 5, totalTokens: 15 };
  const ordinary = measured.inputTokens - measured.cachedInputTokens - measured.cacheWriteInputTokens;
  const numerator = BigInt(ordinary) * 200000n + BigInt(measured.cachedInputTokens) * 20000n +
    BigInt(measured.cacheWriteInputTokens) * 250000n + BigInt(measured.outputTokens) * 1200000n;
  const actualCostMicrousd = Number((numerator + 999999n) / 1000000n);
  const proposal = { contextSha256: plan.contextSha256, dialogueText: dialogue, actionChoiceId: choice };
  return {
    ok: true,
    requestCount: 1,
    costUncertain: false,
    returnedModel: "gpt-5.6-luna",
    usage: measured,
    actualCostMicrousd,
    responseBytes: 128,
    proposal,
    proposalJson: canonicalizeJsonValue(proposal),
  };
}

function canonicalProposalJson(proposalJson) {
  const proposal = JSON.parse(proposalJson);
  return canonicalizeJsonValue({
    format: "matrix-oasis.npc-dialogue-proposal",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    ...proposal,
  });
}

function validProposal(proposalJson, { mappedIntentSha256 = null, command = null } = {}) {
  const proposal = JSON.parse(proposalJson);
  return {
    ok: true,
    canonicalNpcDialogueProposalJson: canonicalProposalJson(proposalJson),
    dialogueText: proposal.dialogueText,
    actionChoiceId: proposal.actionChoiceId,
    mappedIntentSha256,
    command,
  };
}

function hostOperations(overrides = {}) {
  return {
    async keyReader() { return "SECRET_R22_TEST_KEY"; },
    async providerExecutor(input) { return providerSuccess(input.callPlanJson); },
    async proposalValidator({ proposalJson }) {
      return validProposal(proposalJson);
    },
    async adjudicationLookup() { return { found: false }; },
    ...overrides,
  };
}

async function makeFixture(t, name, overrides = {}) {
  await mkdir(TMP, { recursive: true });
  const temporaryRoot = await mkdtemp(path.join(TMP, `matrix-oasis-r22-${name}-`));
  t.after(async () => { await rm(temporaryRoot, { recursive: true, force: true }); });
  const cognitionRunRoot = path.join(temporaryRoot, "run-cognition");
  const config = {
    temporaryRoot,
    cognitionRunRoot,
    hostRunId: "host-run-one",
    timelineId: overrides.timelineId ?? "timeline-one",
    authoritySessionSha256: overrides.authoritySessionSha256 ?? fixedSha("a"),
    cognitionPolicySha256: fixedSha("b"),
    initialLedgerPoint: overrides.initialLedgerPoint ?? ledgerPoint(),
  };
  const store = await openR22CallStore(config, overrides.storeOperations);
  const host = createR22TransactionalHost({
    store,
    operations: hostOperations(overrides.operations),
    clock: overrides.clock ?? (() => 1_000_000),
    randomBytesImplementation: overrides.randomBytesImplementation ?? (() => new Uint8Array(32).fill(7)),
  });
  return { temporaryRoot, cognitionRunRoot, config, store, host };
}

async function register(host, sequence = 1, actorEntityId = "actor-one", intentSha256s = []) {
  const providerRequestJson = payload(sequence);
  const callPlanJson = callPlan(providerRequestJson, sequence, intentSha256s);
  const result = await registerR22CognitionTurn(host, {
    turnId: `turn-${sequence}`,
    sequence,
    actorEntityId,
    callPlanJson,
    providerRequestJson,
    beforeLedgerPoint: ledgerPoint(),
  });
  return { result, providerRequestJson, callPlanJson };
}

async function approved(host, sequence = 1, actorEntityId = "actor-one", intentSha256s = []) {
  const planned = await register(host, sequence, actorEntityId, intentSha256s);
  assert.equal(planned.result.ok, true, JSON.stringify(planned.result));
  const approval = issueR22CognitionApproval(host, { turnId: `turn-${sequence}`, disclosureSha256: planned.result.disclosureSha256 });
  assert.equal(approval.ok, true, JSON.stringify(approval));
  return { ...planned, approval };
}

async function acknowledgeDirectDisplay(host, turn, result) {
  const plan = JSON.parse(turn.callPlanJson);
  const displayAckHash = computeR22DisplayAckHash({
    approvalTokenSha256: turn.approval.approvalHash,
    turnSha256: plan.turnSha256,
    callPlanSha256: shaText(turn.callPlanJson),
    proposalSha256: result.proposalSha256,
    actionChoiceId: result.actionChoiceId,
  });
  const acknowledged = await acknowledgeR22CognitionDisplay(host, { turnId: turn.result.turnId, displayAckHash });
  assert.equal(acknowledged.ok, true, JSON.stringify(acknowledged));
  return { acknowledged, displayAckHash };
}

async function allFiles(root) {
  const output = [];
  async function visit(directory) {
    for (const name of await readdir(directory, { withFileTypes: true })) {
      const candidate = path.join(directory, name.name);
      if (name.isDirectory()) await visit(candidate);
      else output.push(candidate);
    }
  }
  await visit(root);
  return output;
}

function loopbackRequest({ method = "GET", route, token, body = "", headers = {} }) {
  return new Promise((resolve, reject) => {
    const encoded = Buffer.isBuffer(body) ? body : Buffer.from(body, "utf8");
    const request = httpRequest({
      hostname: "127.0.0.1",
      port: 43122,
      path: route,
      method,
      headers: {
        authorization: `Bearer ${token}`,
        ...(method === "POST" ? { "content-type": "application/json" } : {}),
        ...headers,
        ...(encoded.byteLength > 0 ? { "content-length": encoded.byteLength } : {}),
      },
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        resolve({ statusCode: response.statusCode, headers: response.headers, text, body: JSON.parse(text) });
      });
    });
    request.once("error", reject);
    if (encoded.byteLength > 0) request.write(encoded);
    request.end();
  });
}

function jsonBody(value) {
  return JSON.stringify(value);
}

function delegatedResponse(statusCode, value) {
  return {
    statusCode,
    headers: { "content-type": "application/json; charset=utf-8" },
    body: canonicalizeJsonValue(value),
  };
}

function directLoopbackRequest(token, method, route, body = "") {
  return {
    remoteAddress: "127.0.0.1",
    method,
    url: route,
    headers: {
      authorization: `Bearer ${token}`,
      ...(method === "POST" ? { "content-type": "application/json" } : {}),
    },
    body: method === "POST" ? body : "",
  };
}

test("approval is bound to the exact disclosed payload, is one-time, and decline performs zero key/network access", async (t) => {
  let keys = 0;
  let calls = 0;
  const f = await makeFixture(t, "approval", { operations: {
    async keyReader() { keys += 1; return "SECRET"; },
    async providerExecutor() { calls += 1; throw new Error("must not execute"); },
  } });
  const planned = await register(f.host);
  assert.equal(planned.result.ok, true);
  assert.equal(planned.result.disclosureSha256, computeR22ApprovalDisclosureSha256({
    callPlanSha256: planned.result.callPlanSha256,
    providerPayloadSha256: JSON.parse(planned.callPlanJson).providerPayloadSha256,
  }));
  assert.equal(issueR22CognitionApproval(f.host, { turnId: "turn-1", disclosureSha256: fixedSha("f") }).ok, false);
  const approval = issueR22CognitionApproval(f.host, { turnId: "turn-1", disclosureSha256: planned.result.disclosureSha256 });
  assert.equal(approval.ok, true);
  assert.deepEqual(issueR22CognitionApproval(f.host, { turnId: "turn-1", disclosureSha256: planned.result.disclosureSha256 }), approval);
  const declined = await declineR22CognitionTurn(f.host, { turnId: "turn-1", disclosureSha256: planned.result.disclosureSha256 });
  assert.equal(declined.ok, true);
  const receipt = JSON.parse(declined.turnReceiptJson);
  assert.equal(receipt.requestCount, 0);
  assert.equal(receipt.budget.actualMicrousd, 0);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_APPROVAL_DECLINED");
  assert.equal(validateNpcCognitionTurnReceiptJson(declined.turnReceiptJson).valid, true);
  assert.equal(keys, 0);
  assert.equal(calls, 0);
  await closeR22CallStore(f.store);
});

test("persisted declined receipts recover byte-exactly before and after receipt rename without model replay", async (t) => {
  for (const window of ["staged", "target"]) await t.test(window, async (child) => {
    let crashed = false;
    const f = await makeFixture(child, `declined-receipt-${window}`, { storeOperations: {
      async rename(source, target) {
        await rename(source, target);
        if (!crashed && path.basename(target) === "turn-receipt.json") { crashed = true; throw new Error("simulated process exit after rename"); }
      },
    } });
    const planned = await register(f.host);
    const approval = issueR22CognitionApproval(f.host, { turnId: "turn-1", disclosureSha256: planned.result.disclosureSha256 });
    assert.equal(approval.ok, true);
    await assert.rejects(declineR22CognitionTurn(f.host, { turnId: "turn-1", disclosureSha256: planned.result.disclosureSha256 }), /NPC_COGNITION_INTERNAL_ERROR/u);
    const target = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
    const expected = await readFile(target, "utf8");
    if (window === "staged") {
      const stage = path.join(path.dirname(target), ".s-Ab12Cd"); await mkdir(stage);
      await rename(target, path.join(stage, "turn-receipt.json"));
    }
    await closeR22CallStore(f.store);
    let lookups = 0, providers = 0;
    const reopened = await openR22CallStore(f.config), host = createR22TransactionalHost({ store: reopened, operations: hostOperations({
      async adjudicationLookup(input) { lookups += 1; assert.equal(input.intentId, null); assert.deepEqual(input.beforeLedgerPoint, ledgerPoint()); return { found: false }; },
      async providerExecutor() { providers += 1; throw new Error("must not replay"); },
    }) });
    const recovered = await recoverR22TransactionalHost(host);
    assert.equal(recovered.turnReceiptJson, expected);
    assert.equal(JSON.parse(expected).fallbackReason, "NPC_COGNITION_FALLBACK_APPROVAL_DECLINED");
    assert.equal(lookups, 1); assert.equal(providers, 0);
    assert.equal(inspectR22CallStore(reopened).checkpoint.active, null);
    assert.deepEqual(inspectR22CallStore(reopened).hostBudget, { chargedMicrousd: 0, reservedMicrousd: 0, limitMicrousd: NPC_COGNITION_LIMITS.perHostRunMicrousd });
    assert.equal(await readFile(path.join(path.dirname(target), "turn-receipt.json"), "utf8"), expected);
    await closeR22CallStore(reopened);
  });
});

test("every persisted non-adjudicated terminal receipt recovers byte-exactly from stage and target without replay", async (t) => {
  const scenarios = [
    {
      name: "provider-timeout",
      fallbackReason: "NPC_COGNITION_FALLBACK_PROVIDER_TIMEOUT",
      expectedCharge: NPC_COGNITION_LIMITS.perCallMicrousd,
      async operations() {
        return { async providerExecutor() {
          return { ok: false, diagnosticCode: "R22_PROVIDER_TIMEOUT", requestCount: 1, costUncertain: true };
        } };
      },
      async finish(f, turn) {
        await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
      },
    },
    {
      name: "provider-refusal-with-usage",
      fallbackReason: "NPC_COGNITION_FALLBACK_PROVIDER_REFUSED",
      expectedCharge: 8,
      async operations() {
        return { async providerExecutor(input) {
          const measured = providerSuccess(input.callPlanJson);
          return { ...measured, ok: false, diagnosticCode: "R22_PROVIDER_REFUSED" };
        } };
      },
      async finish(f, turn) {
        await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
      },
    },
    {
      name: "validated-local-fallback",
      fallbackReason: "NPC_COGNITION_FALLBACK_CONTEXT_STALE",
      expectedCharge: 8,
      async operations() {
        return { async proposalValidator() { return { ok: false, diagnosticCode: "R22_CONTEXT_STALE" }; } };
      },
      async finish(f, turn) {
        await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
      },
    },
    {
      name: "queued-action-fallback",
      fallbackReason: "NPC_COGNITION_FALLBACK_R20_UNAVAILABLE",
      expectedCharge: 8,
      mappedIntentSha256: fixedSha("e"),
      async operations(scenario) {
        const choice = `choice-${scenario.mappedIntentSha256.slice(7)}`;
        return {
          async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice }); },
          async proposalValidator({ proposalJson }) {
            return validProposal(proposalJson, {
              mappedIntentSha256: scenario.mappedIntentSha256,
              command: { intentId: "intent-r20-one", opaque: true },
            });
          },
        };
      },
      async finish(f, turn) {
        const queued = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
        assert.equal(queued.status, "queued_for_r20");
        await acknowledgeDirectDisplay(f.host, turn, queued);
        await completeQueuedR22CognitionTurn(f.host, {
          turnId: "turn-1", status: "fallback", afterLedgerPoint: ledgerPoint(),
          adjudicationResultJson: null, diagnosticCode: "R22_R20_UNAVAILABLE",
        });
      },
    },
    {
      name: "dialogue-only",
      fallbackReason: "NPC_COGNITION_FALLBACK_NONE",
      expectedCharge: 8,
      async operations() { return {}; },
      async finish(f, turn) {
        const dialogue = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
        assert.equal(dialogue.status, "dialogue_only");
        await acknowledgeDirectDisplay(f.host, turn, dialogue);
      },
    },
  ];

  for (const [scenarioIndex, scenario] of scenarios.entries()) for (const window of ["staged", "target"]) await t.test(`${scenario.name}-${window}`, async (child) => {
    let crashed = false;
    const f = await makeFixture(child, `terminal-${scenarioIndex}-${window}`, {
      operations: await scenario.operations(scenario),
      storeOperations: { async rename(source, target) {
        await rename(source, target);
        if (!crashed && path.basename(target) === "turn-receipt.json") {
          crashed = true;
          throw new Error("simulated process exit after receipt rename");
        }
      } },
    });
    const turn = await approved(f.host, 1, "actor-one", scenario.mappedIntentSha256 ? [scenario.mappedIntentSha256] : []);
    await assert.rejects(scenario.finish(f, turn), /NPC_COGNITION_INTERNAL_ERROR/u);
    const target = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
    assert.ok(target);
    const expected = await readFile(target, "utf8");
    const receipt = JSON.parse(expected);
    assert.equal(receipt.fallbackReason, scenario.fallbackReason);
    assert.equal(receipt.budget.actualMicrousd, scenario.expectedCharge);
    assert.equal(receipt.adjudicationResultSha256, null);
    assert.deepEqual(receipt.ledger.before, receipt.ledger.after);
    if (window === "staged") {
      const stage = path.join(path.dirname(target), ".s-Ab12Cd");
      await mkdir(stage);
      await rename(target, path.join(stage, "turn-receipt.json"));
    }
    await closeR22CallStore(f.store);

    let lookups = 0;
    let replayKeys = 0;
    let replayProviders = 0;
    const reopened = await openR22CallStore(f.config);
    const host = createR22TransactionalHost({ store: reopened, operations: hostOperations({
      async keyReader() { replayKeys += 1; throw new Error("must not reread credentials"); },
      async providerExecutor() { replayProviders += 1; throw new Error("must not replay provider"); },
      async adjudicationLookup(input) {
        lookups += 1;
        assert.equal(input.mappedIntentSha256, scenario.mappedIntentSha256 ?? null);
        assert.equal(input.intentId, scenario.mappedIntentSha256 ? "intent-r20-one" : null);
        assert.deepEqual(input.beforeLedgerPoint, ledgerPoint());
        return { found: false };
      },
    }) });
    const recovered = await recoverR22TransactionalHost(host);
    assert.equal(recovered.turnReceiptJson, expected);
    assert.equal(await readFile(path.join(path.dirname(target), "turn-receipt.json"), "utf8"), expected);
    assert.equal(lookups, 1);
    assert.equal(replayKeys, 0);
    assert.equal(replayProviders, 0);
    assert.deepEqual(inspectR22CallStore(reopened).hostBudget, {
      chargedMicrousd: receipt.budget.actualMicrousd,
      reservedMicrousd: 0,
      limitMicrousd: NPC_COGNITION_LIMITS.perHostRunMicrousd,
    });
    assert.deepEqual(await recoverR22TransactionalHost(host), { ok: true, status: "idle", providerReplayRequests: 0 });
    assert.equal(inspectR22CallStore(reopened).hostBudget.chargedMicrousd, receipt.budget.actualMicrousd);
    await closeR22CallStore(reopened);
  });
});

test("non-adjudicated receipt recovery rejects ledger drift and an already-adjudicated mapped intent", async (t) => {
  await t.test("re-signed after ledger hash drift", async (child) => {
    let crashed = false;
    const f = await makeFixture(child, "forged-after", { storeOperations: { async rename(source, target) {
      await rename(source, target);
      if (!crashed && path.basename(target) === "turn-receipt.json") { crashed = true; throw new Error("crash after receipt rename"); }
    } } });
    const planned = await register(f.host);
    const approval = issueR22CognitionApproval(f.host, { turnId: "turn-1", disclosureSha256: planned.result.disclosureSha256 });
    await assert.rejects(declineR22CognitionTurn(f.host, { turnId: "turn-1", disclosureSha256: planned.result.disclosureSha256 }), /NPC_COGNITION_INTERNAL_ERROR/u);
    const target = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
    const forged = JSON.parse(await readFile(target, "utf8"));
    forged.ledger.after.runtimeSnapshotSha256 = fixedSha("9");
    await writeFile(target, canonicalizeJsonValue(forged), "utf8");
    await closeR22CallStore(f.store);
    let lookups = 0, keys = 0, providers = 0;
    const reopened = await openR22CallStore(f.config);
    const host = createR22TransactionalHost({ store: reopened, operations: hostOperations({
      async keyReader() { keys += 1; return "SECRET"; },
      async providerExecutor() { providers += 1; throw new Error("must not replay"); },
      async adjudicationLookup() { lookups += 1; return { found: false }; },
    }) });
    await assert.rejects(recoverR22TransactionalHost(host), /R22_STORE_TURN_RECEIPT_INVALID|NPC_COGNITION_INTERNAL_ERROR/u);
    assert.equal(lookups, 0);
    assert.equal(keys, 0);
    assert.equal(providers, 0);
    assert.equal(inspectR22CallStore(reopened).checkpoint.active.turnId, "turn-1");
    await closeR22CallStore(reopened);
  });

  await t.test("mapped intent already present in the authority Ledger", async (child) => {
    const mappedIntentSha256 = fixedSha("e");
    const choice = `choice-${mappedIntentSha256.slice(7)}`;
    let crashed = false;
    const f = await makeFixture(child, "mapped-existing", {
      operations: {
        async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice }); },
        async proposalValidator({ proposalJson }) {
          return validProposal(proposalJson, { mappedIntentSha256, command: { intentId: "intent-r20-one", opaque: true } });
        },
      },
      storeOperations: { async rename(source, target) {
        await rename(source, target);
        if (!crashed && path.basename(target) === "turn-receipt.json") { crashed = true; throw new Error("crash after receipt rename"); }
      } },
    });
    const turn = await approved(f.host, 1, "actor-one", [mappedIntentSha256]);
    const queued = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
    await acknowledgeDirectDisplay(f.host, turn, queued);
    await assert.rejects(completeQueuedR22CognitionTurn(f.host, {
      turnId: "turn-1", status: "fallback", afterLedgerPoint: ledgerPoint(),
      adjudicationResultJson: null, diagnosticCode: "R22_R20_UNAVAILABLE",
    }), /NPC_COGNITION_INTERNAL_ERROR/u);
    const target = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
    const expected = await readFile(target, "utf8");
    await closeR22CallStore(f.store);
    let lookups = 0, keys = 0, providers = 0;
    const reopened = await openR22CallStore(f.config);
    const chargedBeforeRejectedRecovery = inspectR22CallStore(reopened).hostBudget.chargedMicrousd;
    const host = createR22TransactionalHost({ store: reopened, operations: hostOperations({
      async keyReader() { keys += 1; return "SECRET"; },
      async providerExecutor() { providers += 1; throw new Error("must not replay"); },
      async adjudicationLookup(input) {
        lookups += 1;
        assert.equal(input.intentId, "intent-r20-one");
        assert.equal(input.mappedIntentSha256, mappedIntentSha256);
        assert.deepEqual(input.beforeLedgerPoint, ledgerPoint());
        return { found: true, canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) };
      },
    }) });
    await assert.rejects(recoverR22TransactionalHost(host), /NPC_COGNITION_INTERNAL_ERROR/u);
    assert.equal(await readFile(target, "utf8"), expected);
    assert.equal(lookups, 1);
    assert.equal(keys, 0);
    assert.equal(providers, 0);
    assert.equal(inspectR22CallStore(reopened).checkpoint.active.turnId, "turn-1");
    assert.equal(inspectR22CallStore(reopened).hostBudget.chargedMicrousd, chargedBeforeRejectedRecovery);
    await closeR22CallStore(reopened);
  });
});

test("expiry is checked before durable reservation and before reading credentials", async (t) => {
  let now = 10;
  let keys = 0;
  const f = await makeFixture(t, "expiry", { clock: () => now, operations: { async keyReader() { keys += 1; return "SECRET"; } } });
  const turn = await approved(f.host);
  now = turn.approval.expiresAtMs + 1;
  const result = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  assert.equal(result.ok, true);
  assert.equal(JSON.parse(result.turnReceiptJson).fallbackReason, "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED");
  assert.deepEqual(inspectR22CallStore(f.store).hostBudget, { chargedMicrousd: 0, reservedMicrousd: 0, limitMicrousd: 1_000_000 });
  assert.equal(keys, 0);
  await closeR22CallStore(f.store);
});

test("twenty concurrent approvals share one durable dispatch and one provider request", async (t) => {
  let keys = 0;
  let calls = 0;
  let release;
  let observeKeyRead;
  let observeProviderCall;
  const gate = new Promise((resolve) => { release = resolve; });
  const keyRead = new Promise((resolve) => { observeKeyRead = resolve; });
  const providerCalled = new Promise((resolve) => { observeProviderCall = resolve; });
  let dispatchObserved = false;
  let f;
  f = await makeFixture(t, "concurrency", { operations: {
    async keyReader() {
      keys += 1;
      const files = await allFiles(f.cognitionRunRoot);
      dispatchObserved = files.some((file) => file.endsWith("dispatch-record.json"));
      observeKeyRead();
      return "SECRET";
    },
    async providerExecutor(input) { calls += 1; observeProviderCall(); await gate; return providerSuccess(input.callPlanJson); },
  } });
  const turn = await approved(f.host);
  const executions = Array.from({ length: 20 }, () => executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash }));
  await Promise.all([keyRead, providerCalled]);
  assert.equal(keys, 1);
  assert.equal(calls, 1);
  assert.equal(dispatchObserved, true);
  release();
  const results = await Promise.all(executions);
  assert.equal(new Set(results.map((value) => value.proposalSha256)).size, 1);
  assert.equal(results[0].status, "dialogue_only");
  const displayed = await acknowledgeDirectDisplay(f.host, turn, results[0]);
  const receipt = JSON.parse(displayed.acknowledged.turnReceiptJson);
  assert.equal(receipt.requestCount, 1);
  assert.equal(receipt.budget.actualMicrousd, 8);
  assert.deepEqual(receipt.statusHistory, ["planned", "approved", "reserved", "dispatching", "validated", "dialogue_only", "finalized"]);
  await closeR22CallStore(f.store);
});

test("credentials are read only after dispatch is durable and credential absence remains a known zero-request failure", async (t) => {
  let dispatchBeforeKey = false;
  let calls = 0;
  let f;
  f = await makeFixture(t, "credential", { operations: {
    async keyReader() {
      dispatchBeforeKey = (await allFiles(f.cognitionRunRoot)).some((file) => file.endsWith("dispatch-record.json"));
      return "";
    },
    async providerExecutor() { calls += 1; throw new Error("must not run"); },
  } });
  const turn = await approved(f.host);
  const result = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  const receipt = JSON.parse(result.turnReceiptJson);
  assert.equal(dispatchBeforeKey, true);
  assert.equal(calls, 0);
  assert.equal(receipt.requestCount, 0);
  assert.equal(receipt.budget.actualMicrousd, 0);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_PROVIDER_CREDENTIAL_UNAVAILABLE");
  assert.equal(validateNpcCognitionTurnReceiptJson(result.turnReceiptJson).valid, true);
  await closeR22CallStore(f.store);
});

test("every post-dispatch provider ambiguity is charged at the full reservation and never retried", async (t) => {
  let calls = 0;
  const f = await makeFixture(t, "provider-failure", { operations: {
    async providerExecutor() { calls += 1; throw new Error("PRIVATE_PROVIDER_ERROR"); },
  } });
  const turn = await approved(f.host);
  const result = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  const receipt = JSON.parse(result.turnReceiptJson);
  assert.equal(calls, 1);
  assert.equal(receipt.requestCount, 1);
  assert.equal(receipt.budget.actualMicrousd, NPC_COGNITION_LIMITS.perCallMicrousd);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_PROVIDER_NETWORK_AMBIGUOUS");
  assert.equal(result.turnReceiptJson.includes("PRIVATE_PROVIDER_ERROR"), false);
  await closeR22CallStore(f.store);
});

test("provider refusal and model mismatch with valid usage retain their exact known charge", async (t) => {
  for (const [diagnosticCode, fallbackReason, returnedModel] of [
    ["R22_PROVIDER_REFUSED", "NPC_COGNITION_FALLBACK_PROVIDER_REFUSED", "gpt-5.6-luna"],
    ["R22_PROVIDER_MODEL_MISMATCH", "NPC_COGNITION_FALLBACK_MODEL_MISMATCH", "gpt-5.6-luna-drifted"],
  ]) {
    await t.test(diagnosticCode, async (child) => {
      const f = await makeFixture(child, diagnosticCode.toLowerCase(), { operations: {
        async providerExecutor(input) {
          const measured = providerSuccess(input.callPlanJson);
          return {
            ok: false,
            diagnosticCode,
            requestCount: 1,
            costUncertain: false,
            returnedModel,
            usage: measured.usage,
            actualCostMicrousd: measured.actualCostMicrousd,
          };
        },
      } });
      const turn = await approved(f.host);
      const result = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
      const receipt = JSON.parse(result.turnReceiptJson);
      assert.equal(receipt.fallbackReason, fallbackReason);
      assert.equal(receipt.budget.actualMicrousd, 8);
      assert.equal(receipt.returnedModel, returnedModel);
      assert.equal(validateNpcCognitionTurnReceiptJson(result.turnReceiptJson).valid, true);
      await closeR22CallStore(f.store);
    });
  }
});

test("context drift and local choice mapping failures are recorded only after provider output validation", async (t) => {
  for (const [diagnosticCode, fallbackReason] of [
    ["R22_CONTEXT_STALE", "NPC_COGNITION_FALLBACK_CONTEXT_STALE"],
    ["R22_CHOICE_INVALID", "NPC_COGNITION_FALLBACK_CHOICE_INVALID"],
  ]) {
    await t.test(diagnosticCode, async (child) => {
      const f = await makeFixture(child, diagnosticCode.toLowerCase(), { operations: {
        async proposalValidator() { return { ok: false, diagnosticCode }; },
      } });
      const turn = await approved(f.host);
      const result = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
      const receipt = JSON.parse(result.turnReceiptJson);
      assert.deepEqual(receipt.statusHistory, ["planned", "approved", "reserved", "dispatching", "validated", "fallback", "finalized"]);
      assert.equal(receipt.fallbackReason, fallbackReason);
      assert.equal(receipt.proposalSha256, shaText(canonicalProposalJson(providerSuccess(turn.callPlanJson).proposalJson)));
      assert.equal(receipt.requestCount, 1);
      assert.equal(receipt.budget.actualMicrousd, 8);
      assert.equal(validateNpcCognitionTurnReceiptJson(result.turnReceiptJson).valid, true);
      await closeR22CallStore(f.store);
    });
  }
});

test("an unknown opaque choice remains a dispatch-stage rejection with no validated proposal evidence", async (t) => {
  const f = await makeFixture(t, "unknown-choice", { operations: {
    async proposalValidator() { return { ok: false, diagnosticCode: "R22_ACTION_CHOICE_UNKNOWN" }; },
  } });
  const turn = await approved(f.host);
  const result = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  const receipt = JSON.parse(result.turnReceiptJson);
  assert.deepEqual(receipt.statusHistory, ["planned", "approved", "reserved", "dispatching", "fallback", "finalized"]);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_ACTION_CHOICE_UNKNOWN");
  assert.equal(receipt.proposalSha256, null);
  assert.equal(receipt.budget.actualMicrousd, 8);
  assert.equal(validateNpcCognitionTurnReceiptJson(result.turnReceiptJson).valid, true);
  await closeR22CallStore(f.store);
});

test("raw player payload, dialogue, response id, credential and errors never enter durable files", async (t) => {
  const f = await makeFixture(t, "redaction", { operations: {
    async providerExecutor(input) {
      const result = providerSuccess(input.callPlanJson, { dialogue: "TRANSIENT_MODEL_DIALOGUE" });
      return {
        ...result,
        proposal: { ...result.proposal, dialogueText: "DIVERGENT_UNVALIDATED_DIALOGUE" },
        responseId: "resp_PRIVATE",
      };
    },
  } });
  const turn = await approved(f.host);
  const result = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  assert.equal(result.dialogueText, "TRANSIENT_MODEL_DIALOGUE");
  const durable = (await Promise.all((await allFiles(f.cognitionRunRoot)).map((file) => readFile(file, "utf8")))).join("\n");
  for (const forbidden of ["PRIVATE_RAW_PLAYER_TEXT", "TRANSIENT_MODEL_DIALOGUE", "DIVERGENT_UNVALIDATED_DIALOGUE", "resp_PRIVATE", "SECRET_R22_TEST_KEY", "PRIVATE_PROVIDER_ERROR"]) {
    assert.equal(durable.includes(forbidden), false, forbidden);
  }
  await closeR22CallStore(f.store);
});

test("a reserved crash is recovered as provably zero requests while a dispatch crash is fully charged", async (t) => {
  const f = await makeFixture(t, "crash");
  const providerRequestJson = payload(1);
  const callPlanJson = callPlan(providerRequestJson, 1);
  const planned = await recordR22PlannedCall(f.store, { sequence: 1, turnId: "turn-1", actorEntityId: "actor-one", callPlanJson, beforeLedgerPoint: ledgerPoint() });
  assert.equal((await reserveR22CallBudget(f.store, planned.callPlanSha256)).ok, true);
  await closeR22CallStore(f.store);

  const reservedStore = await openR22CallStore(f.config);
  const reservedHost = createR22TransactionalHost({ store: reservedStore, operations: hostOperations(), clock: () => 2, randomBytesImplementation: () => new Uint8Array(32).fill(8) });
  const reserved = await recoverR22TransactionalHost(reservedHost);
  const reservedReceipt = JSON.parse(reserved.turnReceiptJson);
  assert.equal(reserved.providerReplayRequests, 0);
  assert.equal(reservedReceipt.requestCount, 0);
  assert.equal(reservedReceipt.budget.actualMicrousd, 0);
  assert.equal(reservedReceipt.fallbackReason, "NPC_COGNITION_FALLBACK_RESERVED_CRASH_RECOVERED");
  await closeR22CallStore(reservedStore);

  const nextConfig = { ...f.config, timelineId: "timeline-two", authoritySessionSha256: fixedSha("c"), initialLedgerPoint: ledgerPoint() };
  const dispatchStore = await openR22CallStore(nextConfig);
  const nextPayload = payload(1);
  const nextPlanJson = callPlan(nextPayload, 1);
  const nextPlan = await recordR22PlannedCall(dispatchStore, { sequence: 1, turnId: "turn-1", actorEntityId: "actor-one", callPlanJson: nextPlanJson, beforeLedgerPoint: ledgerPoint() });
  assert.equal((await reserveR22CallBudget(dispatchStore, nextPlan.callPlanSha256)).ok, true);
  await markR22CallDispatching(dispatchStore, { callPlanSha256: nextPlan.callPlanSha256, approvalTokenSha256: fixedSha("d"), approvalContentSha256: JSON.parse(nextPlanJson).approval.hash });
  await closeR22CallStore(dispatchStore);

  const restartedStore = await openR22CallStore(nextConfig);
  const restartedHost = createR22TransactionalHost({ store: restartedStore, operations: hostOperations(), clock: () => 3, randomBytesImplementation: () => new Uint8Array(32).fill(9) });
  const dispatch = await recoverR22TransactionalHost(restartedHost);
  const dispatchReceipt = JSON.parse(dispatch.turnReceiptJson);
  assert.equal(dispatch.providerReplayRequests, 0);
  assert.equal(dispatchReceipt.requestCount, 1);
  assert.equal(dispatchReceipt.budget.actualMicrousd, NPC_COGNITION_LIMITS.perCallMicrousd);
  assert.equal(dispatchReceipt.fallbackReason, "NPC_COGNITION_FALLBACK_DISPATCH_CRASH_UNCERTAIN");
  assert.equal(inspectR22CallStore(restartedStore).hostBudget.chargedMicrousd, NPC_COGNITION_LIMITS.perCallMicrousd);
  await closeR22CallStore(restartedStore);
});

test("validated action recovery queries the authoritative Ledger and completes a missing receipt without provider replay", async (t) => {
  const ledger = authorityLedgerFixture();
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: ledger.authority.initialSnapshotSha256 };
  const f = await makeFixture(t, "ledger-recovery", { initialLedgerPoint: before });
  const providerRequestJson = payload(1);
  const mappedIntentSha256 = shaText(canonicalizeJsonValue(ledger.entries[0].intent));
  const callPlanJson = callPlan(providerRequestJson, 1, [mappedIntentSha256]);
  const plan = await recordR22PlannedCall(f.store, { sequence: 1, turnId: "turn-1", actorEntityId: "actor-one", callPlanJson, beforeLedgerPoint: before });
  await reserveR22CallBudget(f.store, plan.callPlanSha256);
  await markR22CallDispatching(f.store, { callPlanSha256: plan.callPlanSha256, approvalTokenSha256: fixedSha("d"), approvalContentSha256: JSON.parse(callPlanJson).approval.hash });
  await recordR22ValidatedProposal(f.store, {
    callPlanSha256: plan.callPlanSha256,
    proposalSha256: fixedSha("f"),
    returnedModel: "gpt-5.6-luna",
    usage: { inputTokens: 10, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 5, totalTokens: 15 },
    actualMicrousd: 8,
    actionChoiceId: `choice-${mappedIntentSha256.slice(7)}`,
    mappedIntentId: ledger.entries[0].intent.id,
    mappedIntentSha256,
  });
  const displayAckSha256 = computeR22DisplayAckHash({
    approvalTokenSha256: fixedSha("d"),
    turnSha256: JSON.parse(callPlanJson).turnSha256,
    callPlanSha256: plan.callPlanSha256,
    proposalSha256: fixedSha("f"),
    actionChoiceId: `choice-${mappedIntentSha256.slice(7)}`,
  });
  await recordR22DisplayAcknowledged(f.store, { callPlanSha256: plan.callPlanSha256, proposalSha256: fixedSha("f"), displayAckSha256 });
  await closeR22CallStore(f.store);
  let lookups = 0;
  let providers = 0;
  const reopened = await openR22CallStore(f.config);
  const host = createR22TransactionalHost({ store: reopened, operations: hostOperations({
    async providerExecutor() { providers += 1; throw new Error("must not replay"); },
    async adjudicationLookup(input) {
      lookups += 1;
      assert.equal(input.intentId, ledger.entries[0].intent.id);
      assert.equal(input.mappedIntentSha256, mappedIntentSha256);
      return { found: true, canonicalWorldEventLedgerJson: canonicalizeJsonValue(ledger) };
    },
  }), clock: () => 4, randomBytesImplementation: () => new Uint8Array(32).fill(10) });
  const result = await recoverR22TransactionalHost(host);
  const receipt = JSON.parse(result.turnReceiptJson);
  assert.equal(result.providerReplayRequests, 0);
  assert.equal(lookups, 1);
  assert.equal(providers, 0);
  assert.equal(receipt.adjudicationResultSha256, shaText(canonicalizeJsonValue(authorityResultFixture())));
  assert.equal(receipt.mappedIntentSha256, mappedIntentSha256);
  assert.equal(receipt.ledger.after.revision, 1);
  await closeR22CallStore(reopened);
});

test("validated action without a durable display acknowledgement becomes a known-cost fallback after restart", async (t) => {
  const f = await makeFixture(t, "pending-recovery");
  const providerRequestJson = payload(1);
  const mappedIntentSha256 = fixedSha("e");
  const callPlanJson = callPlan(providerRequestJson, 1, [mappedIntentSha256]);
  const plan = await recordR22PlannedCall(f.store, { sequence: 1, turnId: "turn-1", actorEntityId: "actor-one", callPlanJson, beforeLedgerPoint: ledgerPoint() });
  await reserveR22CallBudget(f.store, plan.callPlanSha256);
  await markR22CallDispatching(f.store, { callPlanSha256: plan.callPlanSha256, approvalTokenSha256: fixedSha("d"), approvalContentSha256: JSON.parse(callPlanJson).approval.hash });
  await recordR22ValidatedProposal(f.store, {
    callPlanSha256: plan.callPlanSha256,
    proposalSha256: fixedSha("f"),
    returnedModel: "gpt-5.6-luna",
    usage: { inputTokens: 10, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 5, totalTokens: 15 },
    actualMicrousd: 8,
    actionChoiceId: `choice-${mappedIntentSha256.slice(7)}`,
    mappedIntentId: "intent-pending-one",
    mappedIntentSha256,
  });
  await closeR22CallStore(f.store);
  const reopened = await openR22CallStore(f.config);
  const host = createR22TransactionalHost({ store: reopened, operations: hostOperations(), clock: () => 4, randomBytesImplementation: () => new Uint8Array(32).fill(10) });
  const recovered = await recoverR22TransactionalHost(host);
  const receipt = JSON.parse(recovered.turnReceiptJson);
  assert.equal(recovered.status, "fallback");
  assert.equal(recovered.providerReplayRequests, 0);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED");
  assert.equal(receipt.requestCount, 1);
  assert.equal(receipt.budget.actualMicrousd, 8);
  assert.equal(receipt.actionChoiceId, `choice-${mappedIntentSha256.slice(7)}`);
  assert.equal(receipt.mappedIntentSha256, null);
  assert.equal(inspectR22CallStore(reopened).checkpoint.active, null);
  await closeR22CallStore(reopened);
});

test("a displayed queued Action rehydrates single-step with exactly one identity-bound R20 command", async (t) => {
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: fixedSha("4") };
  const intentJson = canonicalizeJsonValue(authorityIntentFixture());
  const mappedIntentSha256 = shaText(intentJson);
  const choice = `choice-${mappedIntentSha256.slice(7)}`;
  const command = { sequence: 1, actorEntityId: "actor-one", ruleIndex: 0, nodeId: "entry-node", actionId: "inspect", intentId: "intent-one", npcIntentJson: intentJson };
  let initialProviderRequests = 0;
  const f = await makeFixture(t, "controller-recovery", { initialLedgerPoint: before, operations: {
    async providerExecutor(input) { initialProviderRequests += 1; return providerSuccess(input.callPlanJson, { choice, dialogue: "This dialogue is intentionally not recoverable." }); },
    async proposalValidator({ proposalJson }) { return validProposal(proposalJson, { mappedIntentSha256, command }); },
  } });
  const firstGate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "command", command, nextBehaviorState: {} }; },
    queuedCommandSelector() { return { ok: true, status: "command", command, nextBehaviorState: {} }; },
  });
  const token = "controller-recovery-session-token-01234567";
  const firstController = createR22LoopbackController({
    host: f.host,
    selectorGate: firstGate,
    sessionToken: token,
    async turnFactory(input) {
      const providerRequestJson = payload(1);
      return { turnId: "turn-1", sequence: 1, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1, [mappedIntentSha256]), providerRequestJson, beforeLedgerPoint: before };
    },
    async authorityRequestHandler() { return delegatedResponse(500, { code: "R20_MUST_NOT_RUN_BEFORE_RESTART" }); },
    async authorityStateReader() { return null; },
  });
  const planned = JSON.parse((await handleR22LoopbackRequestAsync(firstController, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "PRIVATE_LOST_AFTER_RESTART" })))).body);
  await handleR22LoopbackRequestAsync(firstController, directLoopbackRequest(token, "POST", "/v1/cognition/approve", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
  let outcome;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    outcome = JSON.parse((await handleR22LoopbackRequestAsync(firstController, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`))).body);
    if (outcome.status !== "dispatching") break;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(outcome.status, "queued_for_r20");
  assert.equal((await handleR22LoopbackRequestAsync(firstController, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: outcome.displayAckHash })))).statusCode, 200);
  await closeR22CallStore(f.store);

  let replayProviderRequests = 0;
  const reopenedStore = await openR22CallStore(f.config);
  const restartedHost = createR22TransactionalHost({
    store: reopenedStore,
    operations: hostOperations({
      async providerExecutor() { replayProviderRequests += 1; throw new Error("provider replay forbidden"); },
      async adjudicationLookup() { return { found: false }; },
    }),
    clock: () => 3_000_000,
    randomBytesImplementation: () => new Uint8Array(32).fill(12),
  });
  const recovered = await recoverR22TransactionalHost(restartedHost);
  assert.equal(recovered.status, "queued_for_r20");
  assert.equal(recovered.actorEntityId, "actor-one");
  assert.equal(recovered.providerReplayRequests, 0);
  const restartedGate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "quiescent" }; },
    queuedCommandSelector(input) {
      assert.equal(input.actorEntityId, "actor-one");
      assert.equal(input.expectedIntentId, "intent-one");
      assert.equal(input.expectedNpcIntentSha256, mappedIntentSha256);
      return { ok: true, status: "command", command, nextBehaviorState: { recovered: true } };
    },
  });
  const restartedController = createR22LoopbackController({
    host: restartedHost,
    selectorGate: restartedGate,
    sessionToken: token,
    singleStep: true,
    async turnFactory() { throw new Error("no new turn while recovered action is pending"); },
    async authorityRequestHandler(request) {
      if (request.url === "/v1/command") {
        const selection = restartedGate.commandSelector({});
        return delegatedResponse(200, selection.status === "command" ? { status: "command", command: selection.command, nextBehaviorState: selection.nextBehaviorState } : { status: selection.status });
      }
      if (request.url === "/v1/arrived") return delegatedResponse(200, { status: "adjudicated", decision: "accepted", beforeSnapshotSha256: fixedSha("4"), afterSnapshotSha256: fixedSha("5") });
      if (request.url === "/v1/mirror") return delegatedResponse(200, { status: "committed", disposition: "returning" });
      return delegatedResponse(404, { code: "R20_ROUTE_NOT_FOUND" });
    },
    async authorityStateReader() { return { authority: { canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) } }; },
  });
  assert.deepEqual(JSON.parse((await handleR22LoopbackRequestAsync(restartedController, directLoopbackRequest(token, "GET", "/v1/command"))).body), { status: "quiescent" });
  const drifted = restoreR22LoopbackController(restartedController, {
    turnId: recovered.turnId,
    actorEntityId: recovered.actorEntityId,
    mappedIntentId: recovered.mappedIntentId,
    mappedIntentSha256: fixedSha("9"),
    displayAckSha256: recovered.displayAckSha256,
  });
  assert.equal(drifted.ok, false);
  const missingAck = restoreR22LoopbackController(restartedController, {
    turnId: recovered.turnId,
    actorEntityId: recovered.actorEntityId,
    mappedIntentId: recovered.mappedIntentId,
    mappedIntentSha256: recovered.mappedIntentSha256,
    displayAckSha256: fixedSha("8"),
  });
  assert.equal(missingAck.ok, false);
  assert.deepEqual(JSON.parse((await handleR22LoopbackRequestAsync(restartedController, directLoopbackRequest(token, "GET", "/v1/command"))).body), { status: "quiescent" });
  const restored = restoreR22LoopbackController(restartedController, {
    turnId: recovered.turnId,
    actorEntityId: recovered.actorEntityId,
    mappedIntentId: recovered.mappedIntentId,
    mappedIntentSha256: recovered.mappedIntentSha256,
    displayAckSha256: recovered.displayAckSha256,
  });
  assert.deepEqual(restored, { ok: true, status: "queued_for_r20", providerReplayRequests: 0 });
  assert.equal(restoreR22LoopbackController(restartedController, {
    turnId: recovered.turnId,
    actorEntityId: recovered.actorEntityId,
    mappedIntentId: recovered.mappedIntentId,
    mappedIntentSha256: recovered.mappedIntentSha256,
    displayAckSha256: recovered.displayAckSha256,
  }).ok, false);
  const selected = JSON.parse((await handleR22LoopbackRequestAsync(restartedController, directLoopbackRequest(token, "GET", "/v1/command"))).body);
  assert.equal(selected.command.intentId, "intent-one");
  assert.deepEqual(selected.nextBehaviorState, { recovered: true });
  assert.deepEqual(JSON.parse((await handleR22LoopbackRequestAsync(restartedController, directLoopbackRequest(token, "GET", "/v1/command"))).body), { status: "quiescent" });
  assert.equal((await handleR22LoopbackRequestAsync(restartedController, directLoopbackRequest(token, "POST", "/v1/arrived", "{}"))).statusCode, 200);
  assert.equal((await handleR22LoopbackRequestAsync(restartedController, directLoopbackRequest(token, "POST", "/v1/mirror", "{}"))).statusCode, 200);
  assert.deepEqual(JSON.parse((await handleR22LoopbackRequestAsync(restartedController, directLoopbackRequest(token, "GET", "/v1/command"))).body), { status: "quiescent" });
  assert.equal(replayProviderRequests, 0);
  assert.equal(initialProviderRequests, 1);
  assert.equal(inspectR22CallStore(reopenedStore).checkpoint.active, null);
  const receiptPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_NONE");
  assert.equal(receipt.mappedIntentSha256, mappedIntentSha256);
  assert.equal(receipt.requestCount, 1);
  const durable = (await Promise.all((await allFiles(f.cognitionRunRoot)).map((file) => readFile(file, "utf8")))).join("\n");
  assert.equal(durable.includes("PRIVATE_LOST_AFTER_RESTART"), false);
  assert.equal(durable.includes("This dialogue is intentionally not recoverable."), false);
  await closeR22CallStore(reopenedStore);
});

test("R20 fallback after a validated choice cannot hide an Action and retains the known provider charge", async (t) => {
  const mappedIntentSha256 = fixedSha("e");
  const choice = `choice-${mappedIntentSha256.slice(7)}`;
  const f = await makeFixture(t, "r20-fallback", { operations: {
    async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice }); },
    async proposalValidator({ proposalJson }) {
      return validProposal(proposalJson, { mappedIntentSha256, command: { intentId: "intent-r20-one", opaque: true } });
    },
  } });
  const turn = await approved(f.host, 1, "actor-one", [mappedIntentSha256]);
  const queued = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  assert.equal(queued.status, "queued_for_r20");
  const forgedAdjudication = authorityResultFixture();
  forgedAdjudication.intentId = "intent-other";
  const forged = await completeQueuedR22CognitionTurn(f.host, {
    turnId: "turn-1",
    status: "adjudicated",
    afterLedgerPoint: { revision: 1, headSha256: forgedAdjudication.headSha256, runtimeSnapshotSha256: forgedAdjudication.afterSnapshotSha256 },
    adjudicationResultJson: canonicalizeJsonValue(forgedAdjudication),
    diagnosticCode: null,
  });
  assert.equal(forged.ok, false);
  assert.equal(forged.diagnostics[0].code, "R22_R20_UNAVAILABLE");
  await acknowledgeDirectDisplay(f.host, turn, queued);
  const forgedAfterDisplay = await completeQueuedR22CognitionTurn(f.host, {
    turnId: "turn-1",
    status: "adjudicated",
    afterLedgerPoint: { revision: 1, headSha256: forgedAdjudication.headSha256, runtimeSnapshotSha256: forgedAdjudication.afterSnapshotSha256 },
    adjudicationResultJson: canonicalizeJsonValue(forgedAdjudication),
    diagnosticCode: null,
  });
  assert.equal(forgedAfterDisplay.ok, false);
  assert.equal(forgedAfterDisplay.diagnostics[0].code, "R22_R19_FAILURE");
  const completed = await completeQueuedR22CognitionTurn(f.host, {
    turnId: "turn-1",
    status: "fallback",
    afterLedgerPoint: ledgerPoint(),
    adjudicationResultJson: null,
    diagnosticCode: "R22_R20_UNAVAILABLE",
  });
  const receipt = JSON.parse(completed.turnReceiptJson);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_R20_UNAVAILABLE");
  assert.equal(receipt.actionChoiceId, choice);
  assert.equal(receipt.mappedIntentSha256, mappedIntentSha256);
  assert.equal(receipt.budget.actualMicrousd, 8);
  assert.equal(inspectR22CallStore(f.store).hostBudget.chargedMicrousd, 8);
  await closeR22CallStore(f.store);
});

test("a queued R20 selector mismatch is a dedicated receipt reason while the public UI reports context stale", async (t) => {
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: fixedSha("4") };
  const mappedIntentJson = canonicalizeJsonValue(authorityIntentFixture());
  const mappedIntentSha256 = shaText(mappedIntentJson);
  const choice = `choice-${mappedIntentSha256.slice(7)}`;
  const mappedCommand = { sequence: 1, actorEntityId: "actor-one", ruleIndex: 0, nodeId: "entry-node", actionId: "inspect", intentId: "intent-one", npcIntentJson: mappedIntentJson };
  const freshIntentJson = canonicalizeJsonValue(authorityIntentFixture({ id: "intent-two" }));
  const freshCommand = { ...mappedCommand, intentId: "intent-two", npcIntentJson: freshIntentJson };
  const f = await makeFixture(t, "selector-stale", { initialLedgerPoint: before, operations: {
    async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice, dialogue: "Canonical response." }); },
    async proposalValidator({ proposalJson }) { return validProposal(proposalJson, { mappedIntentSha256, command: mappedCommand }); },
  } });
  const selectorGate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "command", command: freshCommand, nextBehaviorState: { fresh: true } }; },
    queuedCommandSelector() { return { ok: true, status: "command", command: freshCommand, nextBehaviorState: { fresh: true } }; },
  });
  const token = "selector-stale-session-token-0123456789";
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate,
    sessionToken: token,
    async turnFactory(input) {
      assert.deepEqual(input.transientDialogue, []);
      const providerRequestJson = payload(1);
      return { turnId: "turn-1", sequence: 1, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1, [mappedIntentSha256]), providerRequestJson, beforeLedgerPoint: before };
    },
    async authorityRequestHandler(request) {
      assert.equal(request.url, "/v1/command");
      const selected = selectorGate.commandSelector({});
      return delegatedResponse(200, selected.status === "command" ? { status: "command", command: selected.command } : { status: selected.status });
    },
    async authorityStateReader() { return { canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) }; },
  });
  const planned = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "hello" })))).body);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/approve", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
  let status;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    status = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`))).body);
    if (status.status !== "dispatching") break;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(status.status, "queued_for_r20");
  assert.match(status.displayAckHash, /^sha256:[0-9a-f]{64}$/u);
  const held = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"));
  assert.equal(JSON.parse(held.body).status, "quiescent");
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: status.displayAckHash })));
  const selected = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"));
  assert.deepEqual(JSON.parse(selected.body), { status: "quiescent" });
  const after = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`))).body);
  assert.deepEqual(after, { status: "fallback", diagnostic: "R22_CONTEXT_STALE" });
  const nextPoll = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"));
  assert.equal(JSON.parse(nextPoll.body).command.intentId, "intent-two");
  const receiptPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_R20_SELECTION_STALE");
  assert.equal(receipt.budget.actualMicrousd, 8);
  await closeR22CallStore(f.store);
});

test("host budget survives a timeline reset and a stale process approval cannot authorize a later timeline", async (t) => {
  const f = await makeFixture(t, "reset-budget");
  const first = await approved(f.host);
  const done = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: first.approval.approvalHash });
  assert.equal(done.ok, true);
  await acknowledgeDirectDisplay(f.host, first, done);
  const firstCharge = inspectR22CallStore(f.store).hostBudget.chargedMicrousd;
  assert.equal(firstCharge, 8);
  await closeR22CallStore(f.store);

  const config = { ...f.config, timelineId: "timeline-reset", authoritySessionSha256: fixedSha("c") };
  const store = await openR22CallStore(config);
  const host = createR22TransactionalHost({ store, operations: hostOperations(), clock: () => 1_500_000, randomBytesImplementation: () => new Uint8Array(32).fill(11) });
  const second = await register(host);
  assert.equal(second.result.ok, true);
  const secondApproval = issueR22CognitionApproval(host, { turnId: "turn-1", disclosureSha256: second.result.disclosureSha256 });
  assert.notEqual(secondApproval.approvalHash, first.approval.approvalHash);
  const stale = await executeApprovedR22CognitionTurn(host, { turnId: "turn-1", approvalHash: first.approval.approvalHash });
  assert.equal(stale.ok, false);
  assert.equal(stale.diagnostics[0].code, "R22_APPROVAL_MISMATCH");
  assert.equal(inspectR22CallStore(store).hostBudget.chargedMicrousd, firstCharge);
  await declineR22CognitionTurn(host, { turnId: "turn-1", disclosureSha256: second.result.disclosureSha256 });
  await closeR22CallStore(store);
});

test("a reservation owned by another timeline yields an approved zero-request fallback without touching the prior reservation", async (t) => {
  const f = await makeFixture(t, "cross-timeline-reservation");
  const firstPayload = payload(1);
  const firstPlanJson = callPlan(firstPayload, 1);
  const firstPlan = await recordR22PlannedCall(f.store, { sequence: 1, turnId: "turn-1", actorEntityId: "actor-one", callPlanJson: firstPlanJson, beforeLedgerPoint: ledgerPoint() });
  assert.equal((await reserveR22CallBudget(f.store, firstPlan.callPlanSha256)).ok, true);
  await closeR22CallStore(f.store);

  let keyReads = 0;
  let providerRequests = 0;
  const secondConfig = { ...f.config, timelineId: "timeline-two", authoritySessionSha256: fixedSha("d") };
  const secondStore = await openR22CallStore(secondConfig);
  const secondHost = createR22TransactionalHost({
    store: secondStore,
    operations: hostOperations({
      async keyReader() { keyReads += 1; return "MUST_NOT_BE_READ"; },
      async providerExecutor() { providerRequests += 1; throw new Error("must not dispatch"); },
    }),
    clock: () => 1_600_000,
    randomBytesImplementation: () => new Uint8Array(32).fill(13),
  });
  const turn = await approved(secondHost);
  const result = await executeApprovedR22CognitionTurn(secondHost, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  const receipt = JSON.parse(result.turnReceiptJson);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_CALL_IN_FLIGHT");
  assert.deepEqual(receipt.statusHistory, ["planned", "approved", "fallback", "finalized"]);
  assert.equal(receipt.requestCount, 0);
  assert.equal(receipt.budget.actualMicrousd, 0);
  assert.equal(keyReads, 0);
  assert.equal(providerRequests, 0);
  assert.deepEqual(inspectR22CallStore(secondStore).hostBudget, {
    chargedMicrousd: 0,
    reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
    limitMicrousd: NPC_COGNITION_LIMITS.perHostRunMicrousd,
  });
  await closeR22CallStore(secondStore);
});

test("same actor cannot exceed eight provider requests even though declined turns do not consume call quota", async (t) => {
  const f = await makeFixture(t, "actor-budget");
  for (let sequence = 1; sequence <= 8; sequence += 1) {
    const turn = await approved(f.host, sequence);
    const result = await executeApprovedR22CognitionTurn(f.host, { turnId: `turn-${sequence}`, approvalHash: turn.approval.approvalHash });
    const displayed = await acknowledgeDirectDisplay(f.host, turn, result);
    assert.equal(JSON.parse(displayed.acknowledged.turnReceiptJson).requestCount, 1);
  }
  const ninth = await approved(f.host, 9);
  const exhausted = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-9", approvalHash: ninth.approval.approvalHash });
  const receipt = JSON.parse(exhausted.turnReceiptJson);
  assert.equal(receipt.requestCount, 0);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_BUDGET_EXHAUSTED");
  assert.equal(inspectR22CallStore(f.store).checkpoint.providerRequests, 8);
  await closeR22CallStore(f.store);
});

test("the selector lease holds R20 and an abandoned undispatched turn is finalized before fixed behavior resumes", async (t) => {
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: fixedSha("4") };
  const f = await makeFixture(t, "selector-lease", { initialLedgerPoint: before });
  const intentJson = canonicalizeJsonValue(authorityIntentFixture());
  const command = {
    sequence: 1,
    actorEntityId: "actor-one",
    ruleIndex: 0,
    nodeId: "entry-node",
    actionId: "inspect",
    intentId: "intent-one",
    npcIntentJson: intentJson,
  };
  let selectorCalls = 0;
  const selectorGate = createR22CognitionSelectorGate({
    commandSelector() {
      selectorCalls += 1;
      return { ok: true, status: "command", command, nextBehaviorState: { trusted: true } };
    },
    queuedCommandSelector() {
      selectorCalls += 1;
      return { ok: true, status: "command", command, nextBehaviorState: { trusted: true } };
    },
  });
  const token = "selector-session-token-0123456789abcdef";
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate,
    sessionToken: token,
    async turnFactory(body) {
      assert.equal(body.actorEntityId, "actor-one");
      assert.equal(body.sequence, 1);
      assert.equal(body.playerText, "hello");
      assert.deepEqual(body.transientDialogue, []);
      assert.equal(body.abortSignal.aborted, false);
      const providerRequestJson = payload(1);
      return { turnId: "turn-1", sequence: 1, actorEntityId: body.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1), providerRequestJson, beforeLedgerPoint: before };
    },
    async authorityRequestHandler(request) {
      assert.equal(request.url, "/v1/command");
      const selection = selectorGate.commandSelector({});
      return delegatedResponse(200, selection.status === "command" ? { status: "command", command: selection.command } : { status: selection.status });
    },
    async authorityStateReader() { return { canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) }; },
  });
  const planned = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "hello" })));
  assert.equal(planned.statusCode, 200);
  assert.deepEqual(selectorGate.commandSelector({}), { ok: true, status: "quiescent" });
  assert.equal(selectorCalls, 0);

  const resumed = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"));
  assert.equal(resumed.statusCode, 200);
  assert.equal(JSON.parse(resumed.body).status, "command");
  assert.equal(selectorCalls, 1);
  const files = await allFiles(f.cognitionRunRoot);
  const receiptPath = files.find((file) => file.endsWith("turn-receipt.json"));
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_APPROVAL_DECLINED");
  assert.equal(receipt.requestCount, 0);
  assert.deepEqual(inspectR22CallStore(f.store).hostBudget, { chargedMicrousd: 0, reservedMicrousd: 0, limitMicrousd: 1_000_000 });
  await closeR22CallStore(f.store);
});

test("transient dialogue is actor-scoped, bounded to four whole exchanges, passed only in memory, and cleared by reset", async (t) => {
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: fixedSha("4") };
  const f = await makeFixture(t, "transient-dialogue", { initialLedgerPoint: before });
  const selectorGate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "quiescent" }; },
    queuedCommandSelector() { return { ok: true, status: "quiescent" }; },
  });
  const token = "transient-dialogue-session-token-01234567";
  const factoryInputs = [];
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate,
    sessionToken: token,
    async turnFactory(input) {
      factoryInputs.push(structuredClone(input));
      const sequence = factoryInputs.length;
      const providerRequestJson = payload(sequence);
      return { turnId: `turn-${sequence}`, sequence, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, sequence), providerRequestJson, beforeLedgerPoint: before };
    },
    async authorityRequestHandler(request) {
      if (request.url === "/v1/reset") return delegatedResponse(200, { status: "reset", timelineId: "timeline-reset" });
      return delegatedResponse(200, { status: "quiescent" });
    },
    async authorityStateReader() { return { canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) }; },
  });

  async function createTurn(playerText, actorEntityId = "actor-one") {
    const response = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId, playerText })));
    assert.equal(response.statusCode, 200, response.body);
    return JSON.parse(response.body);
  }
  async function approveAndWait(planned) {
    await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/approve", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
    for (let attempt = 0; attempt < 200; attempt += 1) {
      const response = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`));
      const body = JSON.parse(response.body);
      if (body.status !== "dispatching") {
        assert.equal(body.status, "dialogue_only");
        const displayed = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: body.displayAckHash })));
        assert.deepEqual(JSON.parse(displayed.body), { status: "acknowledged" });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    assert.fail("dialogue turn did not settle");
  }

  for (let sequence = 1; sequence <= 5; sequence += 1) {
    const playerText = sequence <= 2 ? `${sequence}`.repeat(4096) : `player-${sequence}`;
    const planned = await createTurn(playerText);
    if (sequence === 3) assert.deepEqual(factoryInputs[2].transientDialogue.map((entry) => entry.sequence), [2]);
    await approveAndWait(planned);
  }
  assert.deepEqual(factoryInputs[0].transientDialogue, []);
  const sixth = await createTurn("player-6");
  assert.deepEqual(factoryInputs[5].transientDialogue.map((entry) => entry.sequence), [2, 3, 4, 5]);
  assert.equal(factoryInputs[5].transientDialogue.every((entry) => entry.dialogueText === "TRANSIENT_MODEL_DIALOGUE"), true);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/decline", jsonBody({ turnId: sixth.turnId, approvalHash: sixth.approvalHash })));

  const otherActor = await createTurn("other-player", "actor-two");
  assert.deepEqual(factoryInputs[6].transientDialogue, []);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/decline", jsonBody({ turnId: otherActor.turnId, approvalHash: otherActor.approvalHash })));
  const reset = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/reset", "{}"));
  assert.equal(JSON.parse(reset.body).status, "reset");
  const afterReset = await createTurn("player-after-reset");
  assert.deepEqual(factoryInputs[7].transientDialogue, []);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/decline", jsonBody({ turnId: afterReset.turnId, approvalHash: afterReset.approvalHash })));

  const durable = (await Promise.all((await allFiles(f.cognitionRunRoot)).map((file) => readFile(file, "utf8")))).join("\n");
  for (const forbidden of ["1".repeat(4096), "player-3", "player-6", "other-player", "player-after-reset", "TRANSIENT_MODEL_DIALOGUE"]) {
    assert.equal(durable.includes(forbidden), false, forbidden);
  }
  await closeR22CallStore(f.store);
});

test("persisted reset rotates the cognition Host atomically onto the new authority timeline", async (t) => {
  const f = await makeFixture(t, "reset-rotation");
  const gate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "quiescent" }; },
    queuedCommandSelector() { return { ok: true, status: "quiescent" }; },
  });
  const rotationEntered = deferred();
  const rotationReleased = deferred();
  let rotatedStore;
  let rotatedHost;
  const sequences = [];
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate: gate,
    sessionToken: "reset-rotation-session-token-0123456789",
    async turnFactory(input) {
      sequences.push(input.sequence);
      const providerRequestJson = payload(input.sequence);
      return { turnId: `turn-${input.sequence}`, sequence: input.sequence, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, input.sequence), providerRequestJson, beforeLedgerPoint: ledgerPoint() };
    },
    async authorityRequestHandler(request) {
      return request.url === "/v1/reset"
        ? delegatedResponse(200, { status: "reset", timelineId: "timeline-reset" })
        : delegatedResponse(200, { status: "quiescent" });
    },
    async authorityStateReader() { return null; },
    async rotateTimelineHost({ timelineId, releasePreviousHost }) {
      assert.equal(timelineId, "timeline-reset");
      rotationEntered.resolve();
      await rotationReleased.promise;
      await releasePreviousHost();
      rotatedStore = await openR22CallStore({ ...f.config, timelineId, authoritySessionSha256: fixedSha("c") });
      rotatedHost = createR22TransactionalHost({ store: rotatedStore, operations: hostOperations(), clock: () => 1_000_000, randomBytesImplementation: () => new Uint8Array(32).fill(8) });
      return rotatedHost;
    },
  });
  const first = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "old" })))).body);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "POST", "/v1/cognition/decline", jsonBody({ turnId: first.turnId, approvalHash: first.approvalHash })));
  const beforeBudget = inspectR22CallStore(f.store).hostBudget;
  const resetPromise = handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "POST", "/v1/reset", "{}"));
  await rotationEntered.promise;
  const concurrent = await Promise.all([
    handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "racing" }))),
    handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "POST", "/v1/cognition/approve", jsonBody({ turnId: first.turnId, approvalHash: first.approvalHash }))),
    handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "POST", "/v1/cognition/displayed", jsonBody({ turnId: first.turnId, displayAckHash: fixedSha("f") }))),
    handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "GET", `/v1/cognition/status/${first.turnId}`)),
  ]);
  assert.equal(concurrent.every((response) => response.statusCode === 409), true);
  rotationReleased.resolve();
  const reset = await resetPromise;
  assert.deepEqual(JSON.parse(reset.body), { status: "reset", timelineId: "timeline-reset" });
  assert.deepEqual(inspectR22CallStore(rotatedStore).hostBudget, beforeBudget);
  assert.equal(inspectR22CallStore(rotatedStore).checkpoint.latestSequence, 0);
  const next = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-rotation-session-token-0123456789", "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "new" })));
  assert.equal(next.statusCode, 200, next.body);
  assert.deepEqual(sequences, [1, 1]);
  assert.equal((await allFiles(f.cognitionRunRoot)).filter((file) => file.endsWith("turn-receipt.json")).length, 1);
  await closeR22LoopbackController(controller);
  await closeR22CallStore(rotatedStore);
});

test("a failed timeline Host rotation freezes every later operation", async (t) => {
  const f = await makeFixture(t, "reset-rotation-failure");
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate: createR22CognitionSelectorGate({ commandSelector() { return { ok: true, status: "quiescent" }; }, queuedCommandSelector() { return { ok: true, status: "quiescent" }; } }),
    sessionToken: "reset-failure-session-token-0123456789",
    async turnFactory() { throw new Error("must remain frozen"); },
    async authorityRequestHandler() { return delegatedResponse(200, { status: "reset", timelineId: "timeline-reset" }); },
    async authorityStateReader() { return null; },
    async rotateTimelineHost({ releasePreviousHost }) { await releasePreviousHost(); throw new Error("rotation failed"); },
  });
  const reset = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-failure-session-token-0123456789", "POST", "/v1/reset", "{}"));
  assert.deepEqual([reset.statusCode, JSON.parse(reset.body)], [500, { code: "R22_HOST_ROTATION_FAILED" }]);
  for (const [method, route, body] of [["GET", "/v1/command", ""], ["POST", "/v1/reset", "{}"], ["POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "blocked" })]]) {
    const response = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest("reset-failure-session-token-0123456789", method, route, body));
    assert.deepEqual([response.statusCode, JSON.parse(response.body)], [503, { code: "R22_HOST_FROZEN" }]);
  }
  await closeR22LoopbackController(controller);
});

test("single-step mode permits at most one R20 command after a completed cognition turn", async (t) => {
  const f = await makeFixture(t, "single-step");
  let commandCalls = 0;
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate: createR22CognitionSelectorGate({ commandSelector() { return { ok: true, status: "quiescent" }; }, queuedCommandSelector() { return { ok: true, status: "quiescent" }; } }),
    sessionToken: "single-step-session-token-012345678901",
    singleStep: true,
    async turnFactory(input) {
      const providerRequestJson = payload(input.sequence);
      return { turnId: `turn-${input.sequence}`, sequence: input.sequence, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, input.sequence), providerRequestJson, beforeLedgerPoint: ledgerPoint() };
    },
    async authorityRequestHandler(request) {
      if (request.url === "/v1/command") {
        commandCalls += 1;
        return delegatedResponse(200, { status: "command", command: { sequence: commandCalls } });
      }
      if (request.url === "/v1/reset") return delegatedResponse(200, { status: "reset", timelineId: "timeline-one" });
      return delegatedResponse(200, { status: "committed" });
    },
    async authorityStateReader() { return null; },
  });
  const token = "single-step-session-token-012345678901";
  assert.deepEqual(JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"))).body), { status: "quiescent" });
  const planned = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "step" })))).body);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/decline", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
  assert.equal(JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"))).body).status, "command");
  assert.deepEqual(JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"))).body), { status: "quiescent" });
  assert.equal(commandCalls, 1);
  await closeR22LoopbackController(controller);
  await closeR22CallStore(f.store);
});

test("single-step dialogue-only acknowledgement cannot release an available fixed Action", async (t) => {
  const f = await makeFixture(t, "single-step-dialogue-only");
  let commandCalls = 0;
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate: createR22CognitionSelectorGate({
      commandSelector() { return { ok: true, status: "command", command: { sequence: 1 }, nextBehaviorState: {} }; },
      queuedCommandSelector() { return { ok: true, status: "quiescent" }; },
    }),
    sessionToken: "single-step-dialogue-session-token-012345",
    singleStep: true,
    async turnFactory(input) {
      const providerRequestJson = payload(input.sequence);
      return { turnId: `turn-${input.sequence}`, sequence: input.sequence, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, input.sequence), providerRequestJson, beforeLedgerPoint: ledgerPoint() };
    },
    async authorityRequestHandler(request) {
      if (request.url === "/v1/command") {
        commandCalls += 1;
        return delegatedResponse(200, { status: "command", command: { sequence: commandCalls } });
      }
      return delegatedResponse(200, { status: "quiescent" });
    },
    async authorityStateReader() { return null; },
  });
  const token = "single-step-dialogue-session-token-012345";
  const planned = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "dialogue only" })))).body);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/approve", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
  let outcome;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    outcome = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`))).body);
    if (outcome.status !== "dispatching") break;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(outcome.status, "dialogue_only");
  assert.equal(outcome.actionChoiceId, null);
  const displayBody = jsonBody({ turnId: planned.turnId, displayAckHash: outcome.displayAckHash });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const displayed = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", displayBody));
    assert.deepEqual(JSON.parse(displayed.body), { status: "acknowledged" });
  }
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const command = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"));
    assert.deepEqual(JSON.parse(command.body), { status: "quiescent" });
  }
  assert.equal(commandCalls, 0);
  assert.equal(inspectR22CallStore(f.store).checkpoint.providerRequests, 1);
  await closeR22LoopbackController(controller);
  await closeR22CallStore(f.store);
});

test("display acknowledgement is content-bound, expires without another request, and never releases a hidden Action", async (t) => {
  let now = 2_000_000;
  let scheduled = null;
  let cancelled = 0;
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: fixedSha("4") };
  const intentJson = canonicalizeJsonValue(authorityIntentFixture());
  const mappedIntentSha256 = shaText(intentJson);
  const choice = `choice-${mappedIntentSha256.slice(7)}`;
  const command = { sequence: 1, actorEntityId: "actor-one", ruleIndex: 0, nodeId: "entry-node", actionId: "inspect", intentId: "intent-one", npcIntentJson: intentJson };
  const f = await makeFixture(t, "display-expiry", {
    initialLedgerPoint: before,
    clock: () => now,
    operations: {
      async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice, dialogue: "Display me before acting." }); },
      async proposalValidator({ proposalJson }) { return validProposal(proposalJson, { mappedIntentSha256, command }); },
    },
  });
  let selectorCalls = 0;
  const selectorGate = createR22CognitionSelectorGate({
    commandSelector() { selectorCalls += 1; return { ok: true, status: "command", command, nextBehaviorState: {} }; },
    queuedCommandSelector() { selectorCalls += 1; return { ok: true, status: "command", command, nextBehaviorState: {} }; },
  });
  const token = "display-expiry-session-token-0123456789";
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate,
    sessionToken: token,
    displayScheduler(callback, delayMs) {
      scheduled = { callback, delayMs };
      return () => { cancelled += 1; };
    },
    async turnFactory(input) {
      const providerRequestJson = payload(1);
      return { turnId: "turn-1", sequence: 1, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1, [mappedIntentSha256]), providerRequestJson, beforeLedgerPoint: before };
    },
    async authorityRequestHandler(request) {
      assert.equal(request.url, "/v1/command");
      const selected = selectorGate.commandSelector({});
      return delegatedResponse(200, selected.status === "command" ? { status: "command", command: selected.command } : { status: selected.status });
    },
    async authorityStateReader() { return { canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) }; },
  });
  const planned = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "PRIVATE_DISPLAY_INPUT" })))).body);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/approve", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
  let outcome;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    outcome = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`))).body);
    if (outcome.status !== "dispatching") break;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(outcome.status, "queued_for_r20");
  assert.equal(scheduled.delayMs, 60_000);
  const driftedAck = `${outcome.displayAckHash.slice(0, -1)}${outcome.displayAckHash.endsWith("0") ? "1" : "0"}`;
  const rejected = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: driftedAck })));
  assert.equal(rejected.statusCode, 400);
  assert.equal(selectorCalls, 0);
  now += 60_001;
  await scheduled.callback();
  assert.equal(selectorCalls, 0);
  assert.equal(cancelled, 1);
  const receiptPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED");
  assert.equal(receipt.requestCount, 1);
  assert.equal(receipt.budget.actualMicrousd, 8);
  assert.equal(receipt.mappedIntentSha256, null);
  const late = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: outcome.displayAckHash })));
  assert.equal(late.statusCode, 409);
  assert.equal((await allFiles(f.cognitionRunRoot)).filter((file) => file.endsWith("turn-receipt.json")).length, 1);
  const durable = (await Promise.all((await allFiles(f.cognitionRunRoot)).map((file) => readFile(file, "utf8")))).join("\n");
  assert.equal(durable.includes("PRIVATE_DISPLAY_INPUT"), false);
  assert.equal(durable.includes("Display me before acting."), false);
  assert.equal(selectorGate.commandSelector({}).status, "command");
  await closeR22CallStore(f.store);
});

test("the queued selector can release the model-selected second candidate without substituting R20's first eligible command", async (t) => {
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: fixedSha("4") };
  const firstJson = canonicalizeJsonValue(authorityIntentFixture({ id: "intent-first" }));
  const secondJson = canonicalizeJsonValue(authorityIntentFixture({ id: "intent-second" }));
  const firstSha = shaText(firstJson);
  const secondSha = shaText(secondJson);
  const secondChoice = `choice-${secondSha.slice(7)}`;
  const firstCommand = { sequence: 1, actorEntityId: "actor-one", ruleIndex: 0, nodeId: "entry-node", actionId: "inspect", intentId: "intent-first", npcIntentJson: firstJson };
  const secondCommand = { ...firstCommand, ruleIndex: 1, intentId: "intent-second", npcIntentJson: secondJson };
  const f = await makeFixture(t, "second-candidate", { initialLedgerPoint: before, operations: {
    async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice: secondChoice, dialogue: "I choose the second safe option." }); },
    async proposalValidator({ proposalJson }) { return validProposal(proposalJson, { mappedIntentSha256: secondSha, command: secondCommand }); },
  } });
  let firstSelectorCalls = 0;
  let queuedSelectorCalls = 0;
  const selectorGate = createR22CognitionSelectorGate({
    commandSelector() { firstSelectorCalls += 1; return { ok: true, status: "command", command: firstCommand, nextBehaviorState: { selected: 1 } }; },
    queuedCommandSelector(input) {
      queuedSelectorCalls += 1;
      assert.equal(input.actorEntityId, "actor-one");
      assert.equal(input.expectedIntentId, "intent-second");
      assert.equal(input.expectedNpcIntentSha256, secondSha);
      return { ok: true, status: "command", command: secondCommand, nextBehaviorState: { selected: 2 } };
    },
  });
  const token = "second-candidate-session-token-012345678";
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate,
    sessionToken: token,
    async turnFactory(input) {
      const providerRequestJson = payload(1);
      return { turnId: "turn-1", sequence: 1, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1, [firstSha, secondSha]), providerRequestJson, beforeLedgerPoint: before };
    },
    async authorityRequestHandler(request) {
      if (request.url === "/v1/command") {
        const selection = selectorGate.commandSelector({ prepared: {}, runtimeSnapshot: {}, runtimeInspection: {}, worldEventLedgerJson: "{}", behaviorState: {} });
        return delegatedResponse(200, selection.status === "command" ? { status: "command", command: selection.command, nextBehaviorState: selection.nextBehaviorState } : { status: selection.status });
      }
      return delegatedResponse(500, { code: "R20_BRIDGE_UNAVAILABLE" });
    },
    async authorityStateReader() { return { canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) }; },
  });
  const planned = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "pick safely" })))).body);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/approve", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
  let outcome;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    outcome = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`))).body);
    if (outcome.status !== "dispatching") break;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(outcome.actionChoiceId, secondChoice);
  assert.equal(firstSelectorCalls, 0);
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: outcome.displayAckHash })));
  const selected = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"))).body);
  assert.equal(selected.command.intentId, "intent-second");
  assert.deepEqual(selected.nextBehaviorState, { selected: 2 });
  assert.equal(firstSelectorCalls, 0);
  assert.equal(queuedSelectorCalls, 1);
  const failedArrival = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/arrived", "{}"));
  assert.equal(failedArrival.statusCode, 500);
  assert.equal(inspectR22CallStore(f.store).checkpoint.active, null);
  await closeR22CallStore(f.store);
});

test("the fixed 43122 composite host enforces its HTTP boundary and releases only the exact R20 command", async (t) => {
  const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: fixedSha("4") };
  const intentJson = canonicalizeJsonValue(authorityIntentFixture());
  const mappedIntentSha256 = shaText(intentJson);
  const choice = `choice-${mappedIntentSha256.slice(7)}`;
  const command = {
    sequence: 1,
    actorEntityId: "actor-one",
    ruleIndex: 0,
    nodeId: "entry-node",
    actionId: "inspect",
    intentId: "intent-one",
    npcIntentJson: intentJson,
  };
  let providerCalls = 0;
  let releaseProvider;
  let observeProvider;
  const providerGate = new Promise((resolve) => { releaseProvider = resolve; });
  const providerStarted = new Promise((resolve) => { observeProvider = resolve; });
  const f = await makeFixture(t, "loopback", {
    initialLedgerPoint: before,
    operations: {
      async providerExecutor(input) {
        providerCalls += 1;
        observeProvider();
        await providerGate;
        return providerSuccess(input.callPlanJson, { choice, dialogue: "A bounded reply." });
      },
      async proposalValidator({ proposalJson }) {
      return validProposal(proposalJson, { mappedIntentSha256, command });
      },
    },
  });
  let selectorCalls = 0;
  const selectorGate = createR22CognitionSelectorGate({
    commandSelector() {
      selectorCalls += 1;
      return { ok: true, status: "command", command, nextBehaviorState: { derivedByR20: true } };
    },
    queuedCommandSelector(input) {
      selectorCalls += 1;
      assert.equal(input.actorEntityId, "actor-one");
      assert.equal(input.expectedIntentId, "intent-one");
      assert.equal(input.expectedNpcIntentSha256, mappedIntentSha256);
      return { ok: true, status: "command", command, nextBehaviorState: { derivedByR20: true } };
    },
  });
  const delegatedRoutes = [];
  const token = "r22-composite-session-token-0123456789abcdef";
  let factoryCalls = 0;
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate,
    sessionToken: token,
    async turnFactory(body) {
      factoryCalls += 1;
      const providerRequestJson = payload(1);
      assert.equal(Object.hasOwn(body, "providerRequestJson"), false);
      assert.deepEqual(body.transientDialogue, []);
      return { turnId: "turn-1", sequence: 1, actorEntityId: body.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1, [mappedIntentSha256]), providerRequestJson, beforeLedgerPoint: before };
    },
    async authorityRequestHandler(request) {
      delegatedRoutes.push(`${request.method} ${request.url}`);
      if (request.method === "GET" && request.url === "/v1/command") {
        const selection = selectorGate.commandSelector({});
        return delegatedResponse(200, selection.status === "command" ? { status: "command", command: selection.command } : { status: selection.status });
      }
      if (request.url === "/v1/arrived") return delegatedResponse(200, { status: "adjudicated", decision: "accepted", beforeSnapshotSha256: fixedSha("4"), afterSnapshotSha256: fixedSha("5") });
      if (request.url === "/v1/mirror") return delegatedResponse(200, { status: "committed", disposition: "returning" });
      if (request.url === "/v1/reset") return delegatedResponse(200, { status: "reset", timelineId: "timeline-reset" });
      if (request.url === "/v1/verify") return delegatedResponse(200, { status: "verified", revision: 1, headSha256: authorityLedgerFixture().headSha256 });
      return delegatedResponse(404, { code: "R20_ROUTE_NOT_FOUND" });
    },
    async authorityStateReader() {
      return { authority: { canonicalWorldEventLedgerJson: canonicalizeJsonValue(authorityLedgerFixture()) } };
    },
  });
  const server = await startR22LoopbackServer({ controller });
  try {
    const unauthorized = await loopbackRequest({ route: "/v1/command", token: "x".repeat(32) });
    assert.equal(unauthorized.statusCode, 401);
    assert.equal(unauthorized.headers["access-control-allow-origin"], undefined);

    const wrongType = await loopbackRequest({ method: "POST", route: "/v1/cognition/turn", token, body: jsonBody({ actorEntityId: "actor-one", playerText: "hello" }), headers: { "content-type": "application/json; charset=utf-8" } });
    assert.equal(wrongType.statusCode, 415);
    const oversized = await loopbackRequest({ method: "POST", route: "/v1/cognition/turn", token, body: "x".repeat(65_537) });
    assert.equal(oversized.statusCode, 413);
    const injected = await loopbackRequest({ method: "POST", route: "/v1/cognition/turn", token, body: jsonBody({ actorEntityId: "actor-one", playerText: "hello", providerRequestJson: "{}" }) });
    assert.equal(injected.statusCode, 400);
    assert.equal(factoryCalls, 0);

    const planned = await loopbackRequest({ method: "POST", route: "/v1/cognition/turn", token, body: jsonBody({ actorEntityId: "actor-one", playerText: "hello" }) });
    assert.equal(planned.statusCode, 200);
    assert.deepEqual(Object.keys(planned.body).sort(), ["approvalHash", "disclosure", "status", "turnId"]);
    assert.deepEqual(Object.keys(planned.body.disclosure).sort(), [
      "endpoint", "maxCostMicrousd", "maxOutputTokens", "model", "priceLock", "providerRequestJson", "requestLimit", "retention", "retryLimit",
    ]);
    assert.equal(planned.body.disclosure.maxOutputTokens, 512);
    assert.equal(planned.body.disclosure.requestLimit, 1);
    assert.equal(planned.body.disclosure.retryLimit, 0);
    assert.equal(factoryCalls, 1);
    assert.deepEqual(selectorGate.commandSelector({}), { ok: true, status: "quiescent" });
    assert.equal(selectorCalls, 0);

    const approvalBody = jsonBody({ turnId: planned.body.turnId, approvalHash: planned.body.approvalHash });
    const approvals = Array.from({ length: 20 }, () => loopbackRequest({ method: "POST", route: "/v1/cognition/approve", token, body: approvalBody }));
    await providerStarted;
    assert.equal(providerCalls, 1);
    releaseProvider();
    const approvalResponses = await Promise.all(approvals);
    assert.equal(approvalResponses.every((response) => response.statusCode === 200 && ["dispatching", "queued_for_r20"].includes(response.body.status)), true);
    assert.equal(providerCalls, 1);

    let status;
    for (let attempt = 0; attempt < 200; attempt += 1) {
      status = await loopbackRequest({ route: `/v1/cognition/status/${planned.body.turnId}`, token });
      if (status.body.status !== "dispatching") break;
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    assert.equal(status.body.status, "queued_for_r20");
    assert.equal(status.body.dialogueText, "A bounded reply.");
    assert.equal(status.body.actionChoiceId, choice);
    assert.match(status.body.displayAckHash, /^sha256:[0-9a-f]{64}$/u);
    assert.equal((await loopbackRequest({ route: `/v1/cognition/status/${planned.body.turnId}?poll=1`, token })).statusCode, 404);

    const heldBeforeDisplay = await loopbackRequest({ route: "/v1/command", token });
    assert.deepEqual(heldBeforeDisplay.body, { status: "quiescent" });
    assert.equal(selectorCalls, 0);
    const displayedBody = jsonBody({ turnId: planned.body.turnId, displayAckHash: status.body.displayAckHash });
    assert.deepEqual((await loopbackRequest({ method: "POST", route: "/v1/cognition/displayed", token, body: displayedBody })).body, { status: "acknowledged" });
    assert.deepEqual((await loopbackRequest({ method: "POST", route: "/v1/cognition/displayed", token, body: displayedBody })).body, { status: "acknowledged" });

    const selected = await loopbackRequest({ route: "/v1/command", token });
    assert.equal(selected.statusCode, 200);
    assert.equal(selected.body.status, "command");
    assert.equal(selected.body.command.intentId, "intent-one");
    assert.equal(selectorCalls, 1);
    const arrived = await loopbackRequest({ method: "POST", route: "/v1/arrived", token, body: jsonBody({ sequence: 1, pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 1, pathLengthMm: 1 }) });
    assert.equal(arrived.body.status, "adjudicated");
    const mirrored = await loopbackRequest({ method: "POST", route: "/v1/mirror", token, body: jsonBody({ sequence: 1, beforeSnapshotSha256: fixedSha("4"), afterSnapshotSha256: fixedSha("5") }) });
    assert.equal(mirrored.body.status, "committed");
    assert.equal(inspectR22CallStore(f.store).checkpoint.active, null);
    assert.equal(inspectR22CallStore(f.store).hostBudget.chargedMicrousd, 8);

    assert.equal((await loopbackRequest({ method: "POST", route: "/v1/verify", token, body: "{}" })).body.status, "verified");
    assert.equal((await loopbackRequest({ method: "POST", route: "/v1/reset", token, body: "{}" })).body.status, "reset");
    assert.deepEqual(delegatedRoutes, ["GET /v1/command", "POST /v1/arrived", "POST /v1/mirror", "POST /v1/verify", "POST /v1/reset"]);
  } finally {
    await server.close();
    await closeR22CallStore(f.store);
  }
});

test("planning has a hard deadline, aborts late work, and a late result cannot consume the first sequence", async (t) => {
  const f = await makeFixture(t, "planning-deadline");
  const gate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "quiescent" }; },
    queuedCommandSelector() { return { ok: true, status: "quiescent" }; },
  });
  const late = deferred();
  let planningCallback;
  let capturedSignal;
  let factoryCalls = 0;
  const token = "planning-deadline-session-token-01234567";
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate: gate,
    sessionToken: token,
    planningScheduler(callback, delayMs) {
      assert.equal(delayMs, 60_000);
      planningCallback = callback;
      return () => {};
    },
    async turnFactory(input) {
      factoryCalls += 1;
      capturedSignal = input.abortSignal;
      if (factoryCalls === 1) return late.promise;
      assert.equal(input.sequence, 1);
      const providerRequestJson = payload(1);
      return { turnId: "turn-1", sequence: 1, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1), providerRequestJson, beforeLedgerPoint: ledgerPoint() };
    },
    async authorityRequestHandler() { return delegatedResponse(200, { status: "quiescent" }); },
    async authorityStateReader() { return null; },
  });
  const first = handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "late" })));
  while (!planningCallback) await Promise.resolve();
  await planningCallback();
  const timedOut = await first;
  assert.equal(timedOut.statusCode, 408);
  assert.equal(capturedSignal.aborted, true);
  const lateRequest = payload(1);
  late.resolve({ turnId: "turn-1", sequence: 1, actorEntityId: "actor-one", callPlanJson: callPlan(lateRequest, 1), providerRequestJson: lateRequest, beforeLedgerPoint: ledgerPoint() });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(inspectR22CallStore(f.store).checkpoint.latestSequence, 0);
  const second = await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "next" })));
  assert.equal(second.statusCode, 200);
  assert.equal(JSON.parse(second.body).turnId, "turn-1");
  assert.equal(inspectR22CallStore(f.store).checkpoint.latestSequence, 1);
  await closeR22LoopbackController(controller);
  await closeR22CallStore(f.store);
});

test("turn identity is bound to the Call Plan and a mismatched factory result leaves sequence one reusable", async (t) => {
  const f = await makeFixture(t, "turn-id-binding");
  const gate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "quiescent" }; },
    queuedCommandSelector() { return { ok: true, status: "quiescent" }; },
  });
  let calls = 0;
  const token = "turn-binding-session-token-0123456789";
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate: gate,
    sessionToken: token,
    async turnFactory(input) {
      calls += 1;
      const providerRequestJson = payload(1);
      let callPlanJson = callPlan(providerRequestJson, 1);
      if (calls === 1) {
        const plan = JSON.parse(callPlanJson);
        plan.turnId = "turn-other";
        plan.approval.hash = fixedSha("0");
        plan.approval.hash = computeNpcCognitionApprovalHash(plan);
        callPlanJson = canonicalizeJsonValue(plan);
      }
      return { turnId: "turn-1", sequence: input.sequence, actorEntityId: input.actorEntityId, callPlanJson, providerRequestJson, beforeLedgerPoint: ledgerPoint() };
    },
    async authorityRequestHandler() { return delegatedResponse(200, { status: "quiescent" }); },
    async authorityStateReader() { return null; },
  });
  const request = directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "bind" }));
  assert.equal((await handleR22LoopbackRequestAsync(controller, request)).statusCode, 409);
  assert.equal(inspectR22CallStore(f.store).checkpoint.latestSequence, 0);
  const accepted = await handleR22LoopbackRequestAsync(controller, request);
  assert.equal(accepted.statusCode, 200);
  assert.equal(inspectR22CallStore(f.store).checkpoint.latestSequence, 1);
  await closeR22LoopbackController(controller);
  await closeR22CallStore(f.store);
});

test("approval expiry is proactive and a turn response lost by the client cannot hold the R20 selector", async (t) => {
  let now = 4_000_000;
  let approvalCallback;
  const f = await makeFixture(t, "approval-expiry", { clock: () => now });
  const intentJson = canonicalizeJsonValue(authorityIntentFixture());
  const command = { sequence: 1, actorEntityId: "actor-one", ruleIndex: 0, nodeId: "entry-node", actionId: "inspect", intentId: "intent-one", npcIntentJson: intentJson };
  const gate = createR22CognitionSelectorGate({
    commandSelector() { return { ok: true, status: "command", command, nextBehaviorState: {} }; },
    queuedCommandSelector() { return { ok: true, status: "command", command, nextBehaviorState: {} }; },
  });
  const token = "approval-expiry-session-token-012345678";
  const controller = createR22LoopbackController({
    host: f.host,
    selectorGate: gate,
    sessionToken: token,
    approvalScheduler(callback, delayMs) {
      assert.equal(delayMs, 300_000);
      approvalCallback = callback;
      return () => {};
    },
    async turnFactory(input) {
      const providerRequestJson = payload(1);
      return { turnId: "turn-1", sequence: input.sequence, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1), providerRequestJson, beforeLedgerPoint: ledgerPoint() };
    },
    async authorityRequestHandler(request) {
      if (request.url === "/v1/command") {
        const selected = gate.commandSelector({});
        return delegatedResponse(200, selected.status === "command" ? { status: "command", command: selected.command } : { status: selected.status });
      }
      return delegatedResponse(200, { status: "reset" });
    },
    async authorityStateReader() { return null; },
  });
  await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "lost response" })));
  assert.equal(gate.commandSelector({}).status, "quiescent");
  now += 300_001;
  await approvalCallback();
  const receiptPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("turn-receipt.json"));
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED");
  assert.equal(receipt.requestCount, 0);
  assert.equal(gate.commandSelector({}).status, "command");
  const resumed = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", "/v1/command"))).body);
  assert.equal(resumed.status, "command");
  await closeR22LoopbackController(controller);
  await closeR22CallStore(f.store);
});

test("approval is rechecked after deferred credential lookup and can never dispatch after its TTL", async (t) => {
  let now = 5_000_000;
  const keyStarted = deferred();
  const keyRelease = deferred();
  let providers = 0;
  const f = await makeFixture(t, "approval-ttl-race", { clock: () => now, operations: {
    async keyReader() { keyStarted.resolve(); await keyRelease.promise; return "SECRET_R22_TEST_KEY"; },
    async providerExecutor() { providers += 1; return null; },
  } });
  const turn = await approved(f.host);
  const execution = executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  await keyStarted.promise;
  now += 300_001;
  keyRelease.resolve();
  const result = await execution;
  const receipt = JSON.parse(result.turnReceiptJson);
  assert.equal(receipt.fallbackReason, "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED_PRE_REQUEST");
  assert.equal(receipt.requestCount, 0);
  assert.equal(receipt.budget.actualMicrousd, 0);
  assert.equal(providers, 0);
  await closeR22CallStore(f.store);
});

test("strict finalized reads require the complete immutable evidence closure", async (t) => {
  const f = await makeFixture(t, "strict-finalized");
  const turn = await approved(f.host);
  const outcome = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  await acknowledgeDirectDisplay(f.host, turn, outcome);
  const callPlanSha256 = shaText(turn.callPlanJson);
  const query = { timelineId: "timeline-one", turnId: "turn-1", callPlanSha256 };
  assert.equal((await readR22FinalizedTurnReceipt(f.store, query)).turnReceiptSha256.startsWith("sha256:"), true);
  assert.equal((await readFinalizedR22CognitionTurn(f.host, query)).ok, true);
  const displayAckPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("display-ack-record.json"));
  await rm(displayAckPath);
  await assert.rejects(readR22FinalizedTurnReceipt(f.store, query), /R22_STORE_FINALIZED_EVIDENCE_INCOMPLETE/u);
  assert.equal((await readFinalizedR22CognitionTurn(f.host, query)).ok, false);
  await closeR22CallStore(f.store);
});

test("receipt publication validates intermediate evidence before mutating budget or checkpoint", async (t) => {
  const intentJson = canonicalizeJsonValue(authorityIntentFixture());
  const mappedIntentSha256 = shaText(intentJson);
  const choice = `choice-${mappedIntentSha256.slice(7)}`;
  const command = { sequence: 1, actorEntityId: "actor-one", ruleIndex: 0, nodeId: "entry-node", actionId: "inspect", intentId: "intent-one", npcIntentJson: intentJson };
  const f = await makeFixture(t, "prepublish-evidence", { operations: {
    async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice }); },
    async proposalValidator({ proposalJson }) { return validProposal(proposalJson, { mappedIntentSha256, command }); },
  } });
  const turn = await approved(f.host, 1, "actor-one", [mappedIntentSha256]);
  const outcome = await executeApprovedR22CognitionTurn(f.host, { turnId: "turn-1", approvalHash: turn.approval.approvalHash });
  await acknowledgeDirectDisplay(f.host, turn, outcome);
  const displayAckPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("display-ack-record.json"));
  await rm(displayAckPath);
  const before = inspectR22CallStore(f.store);
  await assert.rejects(completeQueuedR22CognitionTurn(f.host, {
    turnId: "turn-1",
    status: "fallback",
    afterLedgerPoint: ledgerPoint(),
    adjudicationResultJson: null,
    diagnosticCode: "R22_R20_UNAVAILABLE",
  }), /NPC_COGNITION_INTERNAL_ERROR/u);
  const after = inspectR22CallStore(f.store);
  assert.deepEqual(after.checkpoint, before.checkpoint);
  assert.deepEqual(after.hostBudget, before.hostBudget);
  assert.equal((await allFiles(f.cognitionRunRoot)).some((file) => file.endsWith("turn-receipt.json")), false);
  await closeR22CallStore(f.store);
});

test("replacing any open store hierarchy directory is detected before reservation and leaves state unchanged", async (t) => {
  for (const level of ["cognition", "timeline", "turn"]) {
    await t.test(level, async (child) => {
      const f = await makeFixture(child, `identity-${level}`);
      const registered = await register(f.host);
      assert.equal(registered.result.ok, true);
      const callPlanPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("call-plan.json"));
      const turnRoot = path.dirname(callPlanPath);
      const timelineRoot = path.dirname(path.dirname(turnRoot));
      const target = level === "cognition" ? f.cognitionRunRoot : level === "timeline" ? timelineRoot : turnRoot;
      const replacementSource = `${target}-original`;
      const before = inspectR22CallStore(f.store);
      await rename(target, replacementSource);
      await cp(replacementSource, target, { recursive: true });
      await assert.rejects(reserveR22CallBudget(f.store, registered.result.callPlanSha256), /R22_STORE_PATH_IDENTITY_INVALID/u);
      assert.deepEqual(inspectR22CallStore(f.store), before);
    });
  }
});

test("display acknowledgement, expiry, and close use one terminal claim under controlled I/O races", async (t) => {
  async function within(label, promise) {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label)), 2_000); })]);
    } finally {
      clearTimeout(timer);
    }
  }
  for (const winner of ["ack", "expiry"]) {
    await t.test(`${winner}-wins`, { timeout: 10_000 }, async (child) => {
      let now = 6_000_000;
      let displayCallback;
      const ioEntered = deferred();
      const ioRelease = deferred();
      const intentJson = canonicalizeJsonValue(authorityIntentFixture());
      const mappedIntentSha256 = shaText(intentJson);
      const choice = `choice-${mappedIntentSha256.slice(7)}`;
      const command = { sequence: 1, actorEntityId: "actor-one", ruleIndex: 0, nodeId: "entry-node", actionId: "inspect", intentId: "intent-one", npcIntentJson: intentJson };
      const f = await makeFixture(child, `display-race-${winner}`, {
        clock: () => now,
        storeOperations: {
          async rename(source, target) {
            const targetName = path.basename(target);
            if ((winner === "ack" && targetName === "display-ack-record.json") ||
                (winner === "expiry" && targetName === "turn-receipt.json")) {
              ioEntered.resolve();
              await ioRelease.promise;
            }
            return rename(source, target);
          },
        },
        operations: {
          async providerExecutor(input) { return providerSuccess(input.callPlanJson, { choice, dialogue: "Race-safe dialogue." }); },
          async proposalValidator({ proposalJson }) { return validProposal(proposalJson, { mappedIntentSha256, command }); },
        },
      });
      const selectorGate = createR22CognitionSelectorGate({
        commandSelector() { return { ok: true, status: "command", command, nextBehaviorState: {} }; },
        queuedCommandSelector() { return { ok: true, status: "command", command, nextBehaviorState: {} }; },
      });
      const token = `display-race-${winner}-session-token-0123456789`;
      const controller = createR22LoopbackController({
        host: f.host,
        selectorGate,
        sessionToken: token,
        displayScheduler(callback) { displayCallback = callback; return () => {}; },
        async turnFactory(input) {
          const providerRequestJson = payload(1);
          return { turnId: "turn-1", sequence: input.sequence, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1, [mappedIntentSha256]), providerRequestJson, beforeLedgerPoint: ledgerPoint() };
        },
        async authorityRequestHandler(request) {
          if (request.url === "/v1/command") {
            const selected = selectorGate.commandSelector({});
            return delegatedResponse(200, selected.status === "command" ? { status: "command", command: selected.command } : { status: selected.status });
          }
          return delegatedResponse(500, { code: "R20_UNAVAILABLE" });
        },
        async authorityStateReader() { return null; },
      });
      const planned = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "race" })))).body);
      await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/approve", jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash })));
      let outcome;
      for (let attempt = 0; attempt < 400; attempt += 1) {
        outcome = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "GET", `/v1/cognition/status/${planned.turnId}`))).body);
        if (outcome.status !== "dispatching") break;
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      assert.equal(outcome.status, "queued_for_r20");
      if (winner === "ack") {
        const acknowledgement = handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: outcome.displayAckHash })));
        await within("ack io did not start", ioEntered.promise);
        now += 60_001;
        const expiration = displayCallback();
        ioRelease.resolve();
        assert.equal((await within("ack did not settle", acknowledgement)).statusCode, 200);
        await within("expiry waiter did not observe ack", expiration);
        assert.equal((await allFiles(f.cognitionRunRoot)).some((file) => file.endsWith("turn-receipt.json")), false);
        await closeR22LoopbackController(controller);
      } else {
        now += 60_001;
        const expiration = displayCallback();
        await within("expiry receipt did not start", ioEntered.promise);
        const acknowledgement = handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/displayed", jsonBody({ turnId: planned.turnId, displayAckHash: outcome.displayAckHash })));
        let closed = false;
        const closing = closeR22LoopbackController(controller).then(() => { closed = true; });
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(closed, false);
        ioRelease.resolve();
        const acknowledgementResult = await within("late ack did not observe expiry", acknowledgement);
        assert.equal(acknowledgementResult.statusCode, 409);
        await within("expiry and close did not settle", Promise.all([expiration, closing]));
      }
      const receipts = (await allFiles(f.cognitionRunRoot)).filter((file) => file.endsWith("turn-receipt.json"));
      assert.equal(receipts.length, 1);
      const receipt = JSON.parse(await readFile(receipts[0], "utf8"));
      assert.equal(winner === "ack" ? receipt.fallbackReason : receipt.fallbackReason, winner === "ack"
        ? "NPC_COGNITION_FALLBACK_R20_UNAVAILABLE"
        : "NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED");
      assert.equal(inspectR22CallStore(f.store).checkpoint.active, null);
      await closeR22CallStore(f.store);
    });
  }
});

test("twenty mixed approve and decline requests select one decision and never double-dispatch", async (t) => {
  for (const firstDecision of ["decline", "approve"]) {
    await t.test(`${firstDecision}-wins`, async (child) => {
      let providerCalls = 0;
      const providerRelease = deferred();
      const providerStarted = deferred();
      const f = await makeFixture(child, `mixed-decision-${firstDecision}`, { operations: {
        async providerExecutor(input) {
          providerCalls += 1;
          providerStarted.resolve();
          await providerRelease.promise;
          return providerSuccess(input.callPlanJson);
        },
      } });
      const selectorGate = createR22CognitionSelectorGate({
        commandSelector() { return { ok: true, status: "quiescent" }; },
        queuedCommandSelector() { return { ok: true, status: "quiescent" }; },
      });
      const token = `mixed-${firstDecision}-session-token-0123456789ab`;
      const controller = createR22LoopbackController({
        host: f.host,
        selectorGate,
        sessionToken: token,
        async turnFactory(input) {
          const providerRequestJson = payload(1);
          return { turnId: "turn-1", sequence: input.sequence, actorEntityId: input.actorEntityId, callPlanJson: callPlan(providerRequestJson, 1), providerRequestJson, beforeLedgerPoint: ledgerPoint() };
        },
        async authorityRequestHandler() { return delegatedResponse(200, { status: "quiescent" }); },
        async authorityStateReader() { return null; },
      });
      const planned = JSON.parse((await handleR22LoopbackRequestAsync(controller, directLoopbackRequest(token, "POST", "/v1/cognition/turn", jsonBody({ actorEntityId: "actor-one", playerText: "decide" })))).body);
      const body = jsonBody({ turnId: planned.turnId, approvalHash: planned.approvalHash });
      const order = [firstDecision, ...Array.from({ length: 19 }, (_, index) => index % 2 === 0 ? "approve" : "decline")];
      const responsesPromise = Promise.all(order.map((decision) => handleR22LoopbackRequestAsync(controller,
        directLoopbackRequest(token, "POST", `/v1/cognition/${decision}`, body))));
      if (firstDecision === "approve") {
        await providerStarted.promise;
        providerRelease.resolve();
      } else {
        providerRelease.resolve();
      }
      const responses = await responsesPromise;
      assert.equal(providerCalls, firstDecision === "approve" ? 1 : 0);
      assert.equal(responses.every((response) => [200, 409].includes(response.statusCode)), true);
      await closeR22LoopbackController(controller);
      const receipts = (await allFiles(f.cognitionRunRoot)).filter((file) => file.endsWith("turn-receipt.json"));
      assert.equal(receipts.length, 1);
      assert.equal(inspectR22CallStore(f.store).checkpoint.providerRequests, firstDecision === "approve" ? 1 : 0);
      await closeR22CallStore(f.store);
    });
  }
});

test("recovery rejects forged but canonical Ledger successors before releasing an R20 command", async (t) => {
  const mutations = {
    "wrong-intent"(ledger) { ledger.entries[0].intent.id = "intent-other"; },
    "wrong-before"(ledger) {
      ledger.authority.initialSnapshotSha256 = fixedSha("9");
      ledger.entries[0].intent.observed.runtimeSnapshotSha256 = fixedSha("9");
      ledger.entries[0].beforeSnapshotSha256 = fixedSha("9");
    },
    "wrong-timeline"(ledger) {
      ledger.timeline.id = "timeline-other";
      ledger.entries[0].intent.timelineId = "timeline-other";
    },
    "noncontinuous-head"(ledger) { ledger.entries[0].previousEntrySha256 = fixedSha("8"); },
  };
  for (const [name, mutate] of Object.entries(mutations)) {
    await t.test(name, async (child) => {
      const sourceLedger = authorityLedgerFixture();
      const before = { revision: 0, headSha256: null, runtimeSnapshotSha256: sourceLedger.authority.initialSnapshotSha256 };
      const f = await makeFixture(child, `forged-ledger-${name}`, { initialLedgerPoint: before });
      const providerRequestJson = payload(1);
      const mappedIntentSha256 = shaText(canonicalizeJsonValue(sourceLedger.entries[0].intent));
      const callPlanJson = callPlan(providerRequestJson, 1, [mappedIntentSha256]);
      const plan = await recordR22PlannedCall(f.store, { sequence: 1, turnId: "turn-1", actorEntityId: "actor-one", callPlanJson, beforeLedgerPoint: before });
      await reserveR22CallBudget(f.store, plan.callPlanSha256);
      await markR22CallDispatching(f.store, { callPlanSha256: plan.callPlanSha256, approvalTokenSha256: fixedSha("d"), approvalContentSha256: JSON.parse(callPlanJson).approval.hash });
      await recordR22ValidatedProposal(f.store, {
        callPlanSha256: plan.callPlanSha256,
        proposalSha256: fixedSha("f"),
        returnedModel: "gpt-5.6-luna",
        usage: { inputTokens: 10, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 5, totalTokens: 15 },
        actualMicrousd: 8,
        actionChoiceId: `choice-${mappedIntentSha256.slice(7)}`,
        mappedIntentId: sourceLedger.entries[0].intent.id,
        mappedIntentSha256,
      });
      const displayAckSha256 = computeR22DisplayAckHash({
        approvalTokenSha256: fixedSha("d"), turnSha256: JSON.parse(callPlanJson).turnSha256,
        callPlanSha256: plan.callPlanSha256, proposalSha256: fixedSha("f"), actionChoiceId: `choice-${mappedIntentSha256.slice(7)}`,
      });
      await recordR22DisplayAcknowledged(f.store, { callPlanSha256: plan.callPlanSha256, proposalSha256: fixedSha("f"), displayAckSha256 });
      await closeR22CallStore(f.store);
      const forged = structuredClone(sourceLedger);
      mutate(forged);
      const body = { ...forged.entries[0] };
      delete body.entrySha256;
      forged.entries[0].entrySha256 = shaText(canonicalizeJsonValue(body));
      forged.headSha256 = forged.entries[0].entrySha256;
      const reopened = await openR22CallStore(f.config);
      const host = createR22TransactionalHost({
        store: reopened,
        operations: hostOperations({ async adjudicationLookup() { return { found: true, canonicalWorldEventLedgerJson: canonicalizeJsonValue(forged) }; } }),
        clock: () => 7_000_000,
        randomBytesImplementation: () => new Uint8Array(32).fill(17),
      });
      await assert.rejects(recoverR22TransactionalHost(host), /NPC_COGNITION_INTERNAL_ERROR/u);
      assert.notEqual(inspectR22CallStore(reopened).checkpoint.active, null);
      assert.equal((await allFiles(f.cognitionRunRoot)).some((file) => file.endsWith("turn-receipt.json")), false);
      await closeR22CallStore(reopened);
    });
  }
});

test("recovery rejects a checkpoint turn relabel before hydrating any active Host state", async (t) => {
  const f = await makeFixture(t, "checkpoint-turn-relabel");
  await register(f.host);
  await closeR22CallStore(f.store);
  const checkpointPath = (await allFiles(f.cognitionRunRoot)).find((file) => file.endsWith("cognition-checkpoint.json"));
  const checkpoint = JSON.parse(await readFile(checkpointPath, "utf8"));
  checkpoint.active.turnId = "turn-relabeled";
  await writeFile(checkpointPath, canonicalizeJsonValue(checkpoint), "utf8");
  const reopened = await openR22CallStore(f.config);
  const host = createR22TransactionalHost({ store: reopened, operations: hostOperations(), clock: () => 8_000_000, randomBytesImplementation: () => new Uint8Array(32).fill(18) });
  await assert.rejects(recoverR22TransactionalHost(host), /NPC_COGNITION_INTERNAL_ERROR/u);
  assert.equal((await allFiles(f.cognitionRunRoot)).some((file) => file.endsWith("turn-receipt.json")), false);
  await closeR22CallStore(reopened);
});
