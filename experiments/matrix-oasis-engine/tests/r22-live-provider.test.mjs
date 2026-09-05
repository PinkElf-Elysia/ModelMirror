import assert from "node:assert/strict";
import test from "node:test";
import { computeNpcCognitionApprovalHash } from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { callPlan, sha } from "../packages/npc-cognition-contracts/tests/fixtures.mjs";
import { createR22OfficialOneShotOperations } from "../scripts/lib/r22-live-provider.mjs";
import { sha256 } from "../scripts/lib/r22-cli-core.mjs";

function fixture(label = "one") {
  const providerRequestJson = canonicalizeJsonValue({ input: label, model: "gpt-5.6-luna" });
  const plan = callPlan();
  plan.turnId = `turn-${label}`;
  plan.providerPayloadSha256 = sha256(providerRequestJson);
  plan.requestBytes = Buffer.byteLength(providerRequestJson, "utf8");
  plan.approval.hash = computeNpcCognitionApprovalHash(plan);
  const callPlanJson = canonicalizeJsonValue(plan);
  const callPlanSha256 = sha256(callPlanJson);
  const dispatchRecordJson = canonicalizeJsonValue({
    format: "matrix-oasis.r22-dispatch-record", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", callPlanSha256,
    turnSha256: plan.turnSha256, approvalTokenSha256: sha("e"),
    approvalContentSha256: plan.approval.hash, reservationMicrousd: 10000,
    providerRequestLimit: 1, providerRetryLimit: 0,
  });
  const checkpoint = { providerRequests: 0, active: { stage: "dispatching", callPlanSha256 } };
  return { providerRequestJson, callPlanJson, dispatchRecordJson, checkpoint, plan };
}

function executorInput(f, changes = {}) {
  return { apiKey: "dummy", callPlanJson: f.callPlanJson,
    providerRequestJson: f.providerRequestJson, approvalHash: f.plan.approval.hash, ...changes };
}

test("one-shot operations remain inert until durable dispatch and consume exactly one credential and execution claim", async () => {
  const f = fixture();
  let dispatchReads = 0, credentialReads = 0, executions = 0;
  const operations = createR22OfficialOneShotOperations({
    async readDispatch() { dispatchReads += 1; return f; },
    async readCredential() { credentialReads += 1; return "dummy"; },
    async execute(input) { executions += 1; return { ok: true, input }; },
  });
  assert.deepEqual(operations.inspect(), { credentialClaimed: false, executionClaimed: false,
    sourceCredentialReads: 0, providerRequests: 0, requestLimit: 1, retryLimit: 0 });
  assert.deepEqual([dispatchReads, credentialReads, executions], [0, 0, 0]);
  assert.equal(await operations.keyReader(), "dummy");
  assert.equal((await operations.providerExecutor(executorInput(f))).ok, true);
  assert.deepEqual([dispatchReads, credentialReads, executions], [2, 1, 1]);
  assert.deepEqual(operations.inspect(), { credentialClaimed: true, executionClaimed: true,
    sourceCredentialReads: 1, providerRequests: 1, requestLimit: 1, retryLimit: 0 });
});

test("twenty concurrent callers cannot duplicate credential reads or provider execution", async () => {
  const f = fixture("concurrent");
  let credentialReads = 0, executions = 0;
  const operations = createR22OfficialOneShotOperations({ async readDispatch() { return f; },
    async readCredential() { credentialReads += 1; await new Promise((resolve) => setImmediate(resolve)); return "dummy"; },
    async execute() { executions += 1; await new Promise((resolve) => setImmediate(resolve)); return { ok: true }; } });
  const keys = await Promise.allSettled(Array.from({ length: 20 }, () => operations.keyReader()));
  assert.equal(keys.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(credentialReads, 1);
  const providers = await Promise.allSettled(Array.from({ length: 20 }, () => operations.providerExecutor(executorInput(f))));
  assert.equal(providers.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(executions, 1);
  assert.equal(operations.inspect().providerRequests, 1);
});

test("the one-shot claim survives replacement dispatch checkpoints and rejects every identity substitution", async (t) => {
  const original = fixture("original");
  const reset = fixture("reset");
  const operations = createR22OfficialOneShotOperations({ async readDispatch() { return original; },
    async readCredential() { return "dummy"; }, async execute() { return { ok: true }; } });
  await operations.keyReader();
  await operations.providerExecutor(executorInput(original));
  await assert.rejects(operations.keyReader(), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  await assert.rejects(operations.providerExecutor(executorInput(reset)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);

  for (const [name, mutate] of [
    ["approval", (input) => ({ ...input, approvalHash: sha("f") })],
    ["payload", (input) => ({ ...input, providerRequestJson: "{}" })],
    ["plan", (input) => ({ ...input, callPlanJson: reset.callPlanJson })],
  ]) {
    await t.test(name, async () => {
      const isolated = createR22OfficialOneShotOperations({ async readDispatch() { return original; },
        async readCredential() { return "dummy"; }, async execute() { assert.fail("invalid identity reached execute"); } });
      await isolated.keyReader();
      await assert.rejects(isolated.providerExecutor(mutate(executorInput(original))), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
      assert.equal(isolated.inspect().providerRequests, 0);
    });
  }
});

test("used checkpoints and dispatch read failures fail closed before credential access", async (t) => {
  const f = fixture("blocked");
  for (const [name, readDispatch] of [
    ["dispatch-missing", async () => null],
    ["not-dispatching", async () => ({ ...f, checkpoint: { ...f.checkpoint, active: { ...f.checkpoint.active, stage: "planned" } } })],
    ["already-used", async () => ({ ...f, checkpoint: { ...f.checkpoint, providerRequests: 1 } })],
    ["read-failure", async () => { throw new Error("source unavailable"); }],
  ]) {
    await t.test(name, async () => {
      let credentialReads = 0;
      const operations = createR22OfficialOneShotOperations({ readDispatch,
        async readCredential() { credentialReads += 1; return "dummy"; }, async execute() { assert.fail(); } });
      await assert.rejects(operations.keyReader());
      assert.equal(credentialReads, 0);
      assert.equal(operations.inspect().sourceCredentialReads, 0);
    });
  }
});

test("missing credentials and ambiguous execution are terminal and inspect never exposes raw values", async () => {
  const missing = fixture("missing");
  const noKey = createR22OfficialOneShotOperations({ async readDispatch() { return missing; },
    async readCredential() { return undefined; }, async execute() { assert.fail(); } });
  assert.equal(await noKey.keyReader(), undefined);
  await assert.rejects(noKey.keyReader(), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  assert.equal(JSON.stringify(noKey.inspect()).includes("dummy"), false);

  const ambiguousFixture = fixture("ambiguous");
  let executions = 0;
  const ambiguous = createR22OfficialOneShotOperations({ async readDispatch() { return ambiguousFixture; },
    async readCredential() { return "dummy"; },
    async execute() { executions += 1; throw new Error("RAW_PROVIDER_FAILURE_MUST_NOT_LEAK"); } });
  await ambiguous.keyReader();
  await assert.rejects(ambiguous.providerExecutor(executorInput(ambiguousFixture)), /RAW_PROVIDER_FAILURE_MUST_NOT_LEAK/u);
  await assert.rejects(ambiguous.providerExecutor(executorInput(ambiguousFixture)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  assert.equal(executions, 1);
  assert.deepEqual(ambiguous.inspect(), { credentialClaimed: true, executionClaimed: true,
    sourceCredentialReads: 1, providerRequests: 1, requestLimit: 1, retryLimit: 0 });
  assert.equal(JSON.stringify(ambiguous.inspect()).includes("dummy"), false);
});
