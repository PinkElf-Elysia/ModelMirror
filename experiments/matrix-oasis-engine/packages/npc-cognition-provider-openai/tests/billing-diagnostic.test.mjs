import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { canonicalizeJsonValue as canonical } from "@matrix-oasis/runtime-pack-contracts";
import { computeNpcCognitionApprovalHash } from "@matrix-oasis/npc-cognition-contracts";
import { createNpcCognitionBillingDiagnosticPlan as plan, evaluateNpcCognitionBillingDiagnosticFixture as diagnose,
  createNpcCognitionToolUsageDiagnosticPlan as oldPlan, evaluateNpcCognitionToolUsageDiagnosticFixture as oldDiagnose,
  createOpenAiNpcCognitionProvider, executeApprovedNpcCognitionTurn } from "../src/index.mjs";
import { observeBilling } from "../src/billing-observer.mjs";

const hash = (text) => `sha256:${createHash("sha256").update(text).digest("hex")}`;
const encode = (text) => new TextEncoder().encode(text);
function envelope(billing) {
  return { id: "resp_synthetic", object: "response", status: "completed", error: null, incomplete_details: null,
    model: "gpt-5.6-luna", service_tier: "default", ...(billing === undefined ? {} : { billing }),
    output: [{ id: "msg_synthetic", type: "message", status: "completed", role: "assistant", content: [{
      type: "output_text", annotations: [], text: JSON.stringify({ contextSha256: JSON.parse(plan().callPlanJson).contextSha256,
        dialogueText: "PRIVATE_DIALOGUE_BAIT", actionChoiceId: null }) }] }],
    usage: { input_tokens: 20, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 10, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 30 } };
}

test("billing plan fixes capture identity but preserves the exact old public payload and limits", () => {
  const current = plan(), old = oldPlan(), record = JSON.parse(current.callPlanJson), prior = JSON.parse(old.callPlanJson);
  assert.equal(current.providerRequestJson, old.providerRequestJson);
  for (const key of ["callPlanJson", "approvalHash", "diagnosticProfile", "capturePolicySha256", "diagnosticApprovalSha256"]) {
    assert.notEqual(current[key], old[key], key);
  }
  assert.equal(record.approval.hash, computeNpcCognitionApprovalHash(record));
  assert.equal(current.diagnosticApprovalSha256, hash(canonical({ diagnosticProfile: current.diagnosticProfile,
    capturePolicySha256: current.capturePolicySha256, callPlanSha256: hash(current.callPlanJson),
    providerPayloadSha256: record.providerPayloadSha256, approvalHash: current.approvalHash })));
  for (const key of ["requestLimit", "retryLimit", "maxCostMicrousd", "priceLock", "retention", "candidateChoices"]) {
    assert.deepEqual(record[key], prior[key]);
  }
  assert.equal(new Set(Array.from({ length: 20 }, () => canonical(plan()))).size, 1);
});

test("capture profile/approval swaps fail before parsing or any simulated request", async (t) => {
  const input = plan(), old = oldPlan(), bytes = encode(JSON.stringify(envelope({ payer: "developer" })));
  for (const key of Object.keys(input)) {
    if (input[key] === old[key]) continue;
    await t.test(key, async () => {
      for (const [evaluate, current, other] of [[diagnose, input, old], [oldDiagnose, old, input]]) {
        const result = await evaluate({ ...current, [key]: other[key] }, bytes);
        assert.equal(result.providerResult.diagnosticCode, "R22_APPROVAL_MISMATCH");
        assert.equal(result.providerResult.requestCount, 0); assert.equal(result.observation, null);
      }
    });
  }
  const provider = createOpenAiNpcCognitionProvider({ apiKey: "offline-placeholder",
    fetchImplementation: () => assert.fail("diagnostic plans are not ordinary Turns") });
  for (const supplied of [input, { callPlanJson: input.callPlanJson, providerRequestJson: input.providerRequestJson, approvalHash: input.approvalHash }]) {
    const result = await executeApprovedNpcCognitionTurn(supplied, provider);
    assert.equal(result.requestCount, 0); assert.equal(result.diagnosticCode, "R22_APPROVAL_MISMATCH");
  }
});

test("billing wire matrix is one-way, redacted, bounded and byte-deterministic", async (t) => {
  const shapes = [undefined, null, {}, { payer: "developer" }, { payer: "user" }, { payer: "PRIVATE_PAYER" },
    { amount: 987654321.125, currency: "usd" }, { amount: 0, currency: "PRIVATE_CURRENCY" },
    { service_tier: "default", tool_costs: { total: 987654321.125, PRIVATE_UNKNOWN: "PRIVATE_VALUE" } },
    ["PRIVATE_ARRAY"], "PRIVATE_ROOT", 987654321.125, true, { tool_costs: ["PRIVATE_ITEM"] },
    { "tool_costs.total": 987654321.125 }, { amount: { PRIVATE_UNKNOWN: "PRIVATE_VALUE" } },
    { deeper: [[[[[[[[null]]]]]]]] }, Object.fromEntries(Array.from({ length: 33 }, (_, i) => [`k${i}`, null])), Array(128).fill(null)];
  for (const [index, value] of shapes.entries()) await t.test(`shape ${index}`, async () => {
    const input = plan(), bytes = encode(JSON.stringify(envelope(value))), before = bytes.slice();
    const current = await diagnose(input, bytes), legacy = await oldDiagnose(oldPlan(), bytes);
    assert.deepEqual(current.providerResult, legacy.providerResult, "collection must not alter acceptance or fees");
    // Index 3 is the exact shape independently observed after this matrix was
    // introduced. This changes only production compatibility, not capture trust.
    assert.equal(current.providerResult.ok, value === null || value === undefined || index === 3);
    assert.deepEqual(current.observation, observeBilling(value,
      { callPlanSha256: hash(input.callPlanJson), diagnosticApprovalSha256: input.diagnosticApprovalSha256 }, { present: value !== undefined }));
    assert.equal(current.realRequestCount, 0); assert.equal(current.qualificationEligible, false);
    assert.deepEqual(bytes, before);
    assert.doesNotMatch(canonical(current.observation), /PRIVATE_|987654321/u);
    assert.equal(new Set(await Promise.all(Array.from({ length: 20 }, async () => canonical(await diagnose(plan(), bytes))))).size, 1);
  });
});

test("invalid wire bytes never become billing observations or weaken strict parsing", async (t) => {
  const valid = JSON.stringify(envelope({ amount: 0 }));
  for (const [name, bytes] of [
    ["duplicate", encode(valid.replace('"amount":0', '"amount":1,"amount":0'))],
    ["escaped-duplicate", encode(valid.replace('"amount":0', '"amount":1,"\\u0061mount":0'))],
    ["invalid-utf8", Uint8Array.of(0xc3)], ["truncated", encode(valid.slice(0, -1))],
    ["overdepth", encode(valid.replace('"amount":0', `"amount":${"[".repeat(257)}0${"]".repeat(257)}`))],
    ["overlength", encode(`${valid}${" ".repeat(65537 - Buffer.byteLength(valid))}`)],
    ["unknown-root", encode(JSON.stringify({ ...envelope({}), PRIVATE_ROOT: "PRIVATE_VALUE" }))],
  ]) await t.test(name, async () => {
    const result = await diagnose(plan(), bytes), legacy = await oldDiagnose(oldPlan(), bytes);
    assert.deepEqual(result.providerResult, legacy.providerResult);
    assert.equal(result.providerResult.ok, false); assert.equal(result.observation.status, "not_captured");
    assert.equal(Object.hasOwn(result.observation, "paths"), false);
    assert.doesNotMatch(canonical(result.observation), /PRIVATE_/u);
  });
});
