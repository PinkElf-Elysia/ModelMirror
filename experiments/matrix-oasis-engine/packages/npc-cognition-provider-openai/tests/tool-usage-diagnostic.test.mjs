import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { canonicalizeJsonValue as canonical } from "@matrix-oasis/runtime-pack-contracts";
import { computeNpcCognitionApprovalHash } from "@matrix-oasis/npc-cognition-contracts";
import {
  createNpcCognitionToolUsageDiagnosticPlan as plan,
  evaluateNpcCognitionToolUsageDiagnosticFixture as diagnose,
  createOpenAiNpcCognitionProvider,
  executeApprovedNpcCognitionTurn,
} from "../src/index.mjs";

const hash = (text) => `sha256:${createHash("sha256").update(text).digest("hex")}`;
const encode = (text) => new TextEncoder().encode(text);

// Independent Response fixture, NOT a recorded response or the lost real c body.
function envelope(input = plan()) {
  return {
    id: "resp_local_only", object: "response", status: "completed", error: null,
    incomplete_details: null, model: "gpt-5.6-luna", service_tier: "default",
    output: [{ id: "msg_local_only", type: "message", status: "completed", role: "assistant",
      content: [{ type: "output_text", text: JSON.stringify({
        contextSha256: JSON.parse(input.callPlanJson).contextSha256,
        dialogueText: "Neutral acknowledgement.", actionChoiceId: null,
      }), annotations: [] }] }],
    usage: { input_tokens: 20, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 10, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 30 },
  };
}

function ordinaryPlan() {
  const input = plan(), body = JSON.parse(input.providerRequestJson), record = JSON.parse(input.callPlanJson);
  delete body.tools;
  delete body.tool_choice;
  const providerRequestJson = canonical(body);
  record.providerPayloadSha256 = hash(providerRequestJson);
  record.requestBytes = Buffer.byteLength(providerRequestJson);
  record.approval.hash = computeNpcCognitionApprovalHash(record);
  return { callPlanJson: canonical(record), providerRequestJson, approvalHash: record.approval.hash };
}

async function compareRaw(bytes) {
  const input = plan();
  const before = bytes.slice();
  const diagnostic = await diagnose(input, bytes);
  let fetches = 0;
  const provider = createOpenAiNpcCognitionProvider({ apiKey: "offline-placeholder",
    fetchImplementation: async () => { fetches += 1; return new Response(bytes,
      { headers: { "content-type": "application/json" } }); } });
  const ordinary = await executeApprovedNpcCognitionTurn(ordinaryPlan(), provider);
  assert.equal(fetches, 1);
  assert.deepEqual(diagnostic.providerResult, ordinary, "observer must never change the business result or accounting");
  assert.deepEqual(bytes, before);
  assert.equal(diagnostic.fixtureOnly, true);
  assert.equal(diagnostic.realRequestCount, 0);
  assert.equal(diagnostic.qualificationEligible, false);
  assert.equal(diagnostic.observation?.qualificationEligible, false);
  return diagnostic;
}

test("planning is fixed, public, null-action, tools-disabled and independently content bound", () => {
  const input = plan(), record = JSON.parse(input.callPlanJson), body = JSON.parse(input.providerRequestJson);
  assert.deepEqual(body.tools, []);
  assert.equal(body.tool_choice, "none");
  assert.equal(body.service_tier, "default");
  assert.equal(body.store, false);
  assert.deepEqual(body.text.format.schema.properties.actionChoiceId.enum, [null]);
  assert.deepEqual(record.candidateChoices, []);
  assert.equal(record.providerPayloadSha256, hash(input.providerRequestJson));
  assert.equal(record.contextSha256, hash(body.input));
  assert.equal(record.responseSchemaSha256, hash(canonical(body.text.format.schema)));
  assert.equal(input.diagnosticApprovalSha256, hash(canonical({
    diagnosticProfile: input.diagnosticProfile, capturePolicySha256: input.capturePolicySha256,
    callPlanSha256: hash(input.callPlanJson), providerPayloadSha256: record.providerPayloadSha256,
    approvalHash: input.approvalHash,
  })));
  assert.equal(Object.isFrozen(input), true);
  const outputs = Array.from({ length: 20 }, () => canonical(plan()));
  assert.equal(new Set(outputs).size, 1);
});

test("ordinary API rejects diagnostic fields and rejects tools keys even with an ordinary re-signed plan", async () => {
  let requests = 0;
  const provider = createOpenAiNpcCognitionProvider({ apiKey: "offline-placeholder",
    fetchImplementation: () => { requests += 1; assert.fail("must not dispatch"); } });
  const diagnostic = plan();
  for (const input of [diagnostic, { callPlanJson: diagnostic.callPlanJson,
    providerRequestJson: diagnostic.providerRequestJson, approvalHash: diagnostic.approvalHash }]) {
    const result = await executeApprovedNpcCognitionTurn(input, provider);
    assert.equal(result.diagnosticCode, "R22_APPROVAL_MISMATCH");
    assert.equal(result.requestCount, 0);
    assert.equal(Object.hasOwn(result, "observation"), false);
  }
  assert.equal(requests, 0);
});

test("diagnostic profile, vocabulary, approval, payload, ordinary and re-signed changes cannot enable capture", async (t) => {
  const mutations = {
    ordinary: () => ordinaryPlan(),
    missingApproval: (input) => { delete input.diagnosticApprovalSha256; return input; },
    oldApproval: (input) => ({ ...input, diagnosticApprovalSha256: input.approvalHash }),
    profile: (input) => ({ ...input, diagnosticProfile: "matrix-oasis.r22-tool-usage-diagnostic/2" }),
    vocabulary: (input) => ({ ...input, capturePolicySha256: `sha256:${"e".repeat(64)}` }),
    payload: (input) => ({ ...input, providerRequestJson: `${input.providerRequestJson} ` }),
    captureSwitch: (input) => ({ ...input, capture: true }),
    resigned: (input) => {
      const body = JSON.parse(input.providerRequestJson), record = JSON.parse(input.callPlanJson);
      body.input = "Private replacement must never be sent";
      const providerRequestJson = canonical(body);
      record.providerPayloadSha256 = hash(providerRequestJson);
      record.contextSha256 = hash(body.input);
      record.requestBytes = Buffer.byteLength(providerRequestJson);
      record.approval.hash = computeNpcCognitionApprovalHash(record);
      return { ...input, providerRequestJson, callPlanJson: canonical(record), approvalHash: record.approval.hash };
    },
  };
  for (const [name, mutate] of Object.entries(mutations)) await t.test(name, async () => {
    const result = await diagnose(mutate({ ...plan() }), encode(JSON.stringify(envelope())));
    assert.equal(result.providerResult.diagnosticCode, "R22_APPROVAL_MISMATCH");
    assert.equal(result.providerResult.requestCount, 0);
    assert.equal(result.realRequestCount, 0);
    assert.equal(result.observation, null);
  });
});

test("shape matrix through the actual strict Provider parser leaves primary failures and cost unchanged", async (t) => {
  const cases = [
    ["absent", undefined], ["null", null], ["empty", {}], ["all-zero", { image_gen: { input_tokens: 0 }, web_search: { num_requests: 0 } }],
    ["positive", { web_search: { num_requests: 1 } }],
    ["unknown-positive", { image_gen: { input_tokens: 0 }, file_search: { num_requests: 38765 } }],
    ["dotted-key", { "image_gen.input_tokens": 43876 }],
    ["nested-dotted-key", { image_gen: { "input_tokens_details.image_tokens": 43876 } }],
    ["unknown-parent", { other: { num_requests: 43876 } }],
    ["prototype", JSON.parse('{"__proto__":{"privateText":"PRIVATE_BAIT"}}')],
    ["counter-string", { web_search: { num_requests: "PRIVATE_BAIT" } }],
    ["array", [0, { privateText: "PRIVATE_BAIT" }]],
    ["deep-limit", { child: [[[[[[[[null]]]]]]]] }],
    ["key-limit", Object.fromEntries(Array.from({ length: 33 }, (_, index) => [`key${index}`, 1]))],
    ["node-limit", Array(128).fill(null)],
  ];
  for (const [name, value] of cases) await t.test(name, async () => {
    const response = envelope();
    if (value !== undefined) response.tool_usage = value;
    const result = await compareRaw(encode(JSON.stringify(response)));
    assert.equal(result.providerResult.ok, ["absent", "empty"].includes(name));
    if (!result.providerResult.ok) {
      assert.equal(result.providerResult.responseDiagnostic.stage, "root_echo");
      assert.equal(result.providerResult.costUncertain, true);
      assert.equal(result.providerResult.actualCostMicrousd, null);
    }
    const observation = canonical(result.observation);
    for (const bait of ["PRIVATE_BAIT", "file_search", "38765", "43876", "__proto__"]) assert.equal(observation.includes(bait), false);
    if (name.endsWith("limit")) {
      assert.equal(result.observation.status, "failed_limit");
      assert.equal(Object.hasOwn(result.observation, "paths"), false);
    }
  });
});

test("duplicate keys, malformed JSON, invalid UTF-8, overdepth and root rejection cannot yield partial capture", async (t) => {
  const valid = JSON.stringify({ ...envelope(), tool_usage: {} });
  const duplicate = valid.replace('"tool_usage":{}', '"tool_usage":{"web_search":{"num_requests":9,"num_requests":0}}');
  const escapedDuplicate = valid.replace('"tool_usage":{}', '"tool_usage":{"web_search":{"num_requests":9,"num_\\u0072equests":0}}');
  const overdepth = valid.replace('"tool_usage":{}', `"tool_usage":${"[".repeat(257)}0${"]".repeat(257)}`);
  for (const [name, bytes, stage] of [
    ["duplicate", encode(duplicate), "json"], ["escaped-duplicate", encode(escapedDuplicate), "json"],
    ["truncated", encode(valid.slice(0, -1)), "json"], ["invalid-utf8", Uint8Array.of(0xc3), "body"],
    ["overdepth", encode(overdepth), "json"], ["root", encode('{"tool_usage":{"web_search":{"num_requests":1}}}'), "root_fields"],
  ]) await t.test(name, async () => {
    const result = await compareRaw(bytes);
    assert.equal(result.providerResult.responseDiagnostic.stage, stage);
    assert.equal(result.observation.status, "not_captured");
    assert.equal(Object.hasOwn(result.observation, "paths"), false);
    assert.equal(Object.hasOwn(result.observation, "summary"), false);
  });
});

test("64 KiB body limit is shared, not replaced by an 8 KiB raw subtree limit", async () => {
  const raw = JSON.stringify({ ...envelope(), tool_usage: { unknown: "x".repeat(12000) } });
  const atLimit = `${raw}${" ".repeat(65536 - Buffer.byteLength(raw))}`;
  const result = await compareRaw(encode(atLimit));
  assert.equal(result.observation.status, "observed");
  assert.equal(result.observation.coverage, "redacted");
  assert.ok(Buffer.byteLength(canonical(result.observation)) < 8192);
  const over = await compareRaw(encode(`${atLimit} `));
  assert.equal(over.providerResult.diagnosticCode, "R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED");
  assert.equal(over.observation.status, "not_captured");
});

test("negative zero is never lost by an extra serialization or permissive parse pass", async () => {
  const raw = JSON.stringify({ ...envelope(), tool_usage: {} }).replace('"tool_usage":{}',
    '"tool_usage":{"image_gen":{"input_tokens":-0},"web_search":{"num_requests":65537}}');
  const result = await compareRaw(encode(raw));
  for (const path of ["image_gen.input_tokens", "web_search.num_requests"]) {
    const entry = result.observation.paths.find((item) => item.path === path);
    assert.equal(entry.counterStatus, "invalid_number");
    assert.equal(Object.hasOwn(entry, "value"), false);
  }
});

test("wrong model, tier, refusal and unknown choice remain refusals, irrespective of observable zeros", async (t) => {
  for (const [name, mutate] of [
    ["model", (response) => { response.model = "wrong-model"; }],
    ["tier", (response) => { response.service_tier = "priority"; }],
    ["refusal", (response) => { response.output[0].content = [{ type: "refusal", refusal: "PRIVATE_REFUSAL" }]; }],
    ["choice", (response) => {
      const proposal = JSON.parse(response.output[0].content[0].text);
      proposal.actionChoiceId = `choice-${"b".repeat(64)}`;
      response.output[0].content[0].text = JSON.stringify(proposal);
    }],
  ]) await t.test(name, async () => {
    const response = { ...envelope(), tool_usage: { web_search: { num_requests: 0 } } };
    mutate(response);
    const result = await compareRaw(encode(JSON.stringify(response)));
    assert.equal(result.providerResult.ok, false);
    assert.equal(result.providerResult.costUncertain, true);
    assert.equal(result.observation.paths.find((item) => item.path === "web_search.num_requests").value, 0);
    assert.equal(canonical(result.observation).includes("PRIVATE_REFUSAL"), false);
  });
});

test("twenty independent offline fixtures are deterministic, without claiming durable request deduplication", async () => {
  const input = plan();
  const bytes = encode(JSON.stringify({ ...envelope(), tool_usage: { image_gen: { total_tokens: 0 } } }));
  const results = await Promise.all(Array.from({ length: 20 }, () => diagnose(input, bytes)));
  assert.equal(new Set(results.map(canonical)).size, 1);
  assert.ok(results.every((result) => result.realRequestCount === 0 && result.fixtureOnly));
});

test("diagnostic fixture rejects proxies before traps and copies typed bytes without invoking accessors", async () => {
  let traps = 0;
  const handler = { get() { traps += 1; throw new Error("PRIVATE_PROXY_BAIT"); },
    ownKeys() { traps += 1; throw new Error("PRIVATE_PROXY_BAIT"); },
    getPrototypeOf() { traps += 1; throw new Error("PRIVATE_PROXY_BAIT"); } };
  const bytes = encode(JSON.stringify(envelope()));
  for (const [input, data] of [[new Proxy(plan(), handler), bytes], [plan(), new Proxy(bytes, handler)]]) {
    const result = await diagnose(input, data);
    assert.equal(result.providerResult.diagnosticCode, "R22_APPROVAL_MISMATCH");
    assert.equal(result.providerResult.requestCount, 0);
    assert.equal(result.observation, null);
  }
  const revoked = Proxy.revocable(bytes, handler);
  revoked.revoke();
  assert.equal((await diagnose(plan(), revoked.proxy)).providerResult.requestCount, 0);
  Object.defineProperty(bytes, "byteLength", { get() { traps += 1; throw new Error("PRIVATE_ACCESSOR_BAIT"); } });
  Object.defineProperty(bytes, Symbol.iterator, { get() { traps += 1; throw new Error("PRIVATE_ITERATOR_BAIT"); } });
  const result = await diagnose(plan(), bytes);
  assert.equal(result.providerResult.ok, true);
  assert.equal(traps, 0);
  const shared = new Uint8Array(new SharedArrayBuffer(8));
  assert.equal((await diagnose(plan(), shared)).providerResult.requestCount, 0);
});
